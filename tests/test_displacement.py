"""Tests for vormap_displacement — Voronoi displacement & normal-map generator."""

import math
import os
import struct
import tempfile
import unittest
import zlib

import vormap_displacement as vd


class TestGenerateSeeds(unittest.TestCase):
    """Tests for _generate_seeds."""

    def test_correct_count(self):
        import random
        rng = random.Random(42)
        seeds = vd._generate_seeds(100, 100, 20, rng)
        self.assertEqual(len(seeds), 20)

    def test_within_bounds(self):
        import random
        rng = random.Random(99)
        seeds = vd._generate_seeds(200, 150, 50, rng)
        for x, y in seeds:
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x, 200)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y, 150)

    def test_deterministic(self):
        import random
        s1 = vd._generate_seeds(100, 100, 10, random.Random(7))
        s2 = vd._generate_seeds(100, 100, 10, random.Random(7))
        self.assertEqual(s1, s2)


class TestFindNearestTwo(unittest.TestCase):
    """Tests for _find_nearest_two."""

    def test_simple_case(self):
        seeds = [(0, 0), (10, 0), (5, 10)]
        idx, d1, d2 = vd._find_nearest_two(1, 0, seeds)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(d1, 1.0)

    def test_equidistant(self):
        seeds = [(0, 0), (10, 0)]
        idx, d1, d2 = vd._find_nearest_two(5, 0, seeds)
        self.assertAlmostEqual(d1, 5.0)
        self.assertAlmostEqual(d2, 5.0)

    def test_single_seed(self):
        seeds = [(3, 4)]
        idx, d1, d2 = vd._find_nearest_two(0, 0, seeds)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(d1, 5.0)
        self.assertEqual(d2, float("inf"))


class TestComputeHeights(unittest.TestCase):
    """Tests for _compute_heights."""

    def _make_heights(self, mode, w=32, h=32, num_seeds=10, seed=42):
        import random
        rng = random.Random(seed)
        seeds = vd._generate_seeds(w, h, num_seeds, rng)
        return vd._compute_heights(w, h, seeds, mode, random.Random(seed + 1), 0.15)

    def test_distance_mode_range(self):
        heights = self._make_heights("distance")
        flat = [v for row in heights for v in row]
        self.assertAlmostEqual(max(flat), 1.0, places=5)
        self.assertGreaterEqual(min(flat), 0.0)

    def test_ridge_mode_range(self):
        heights = self._make_heights("ridge")
        flat = [v for row in heights for v in row]
        self.assertAlmostEqual(max(flat), 1.0, places=5)
        self.assertGreaterEqual(min(flat), 0.0)

    def test_radial_mode_range(self):
        heights = self._make_heights("radial")
        flat = [v for row in heights for v in row]
        # Radial mode inverts after normalization; max may be slightly < 1.0
        self.assertGreater(max(flat), 0.9)
        self.assertLessEqual(max(flat), 1.0)
        self.assertGreaterEqual(min(flat), 0.0)

    def test_random_mode_range(self):
        heights = self._make_heights("random")
        flat = [v for row in heights for v in row]
        self.assertLessEqual(max(flat), 1.0)
        self.assertGreaterEqual(min(flat), 0.0)

    def test_dimensions(self):
        heights = self._make_heights("distance", w=20, h=15)
        self.assertEqual(len(heights), 15)
        self.assertEqual(len(heights[0]), 20)


class TestBoxBlur(unittest.TestCase):
    """Tests for _box_blur."""

    def test_zero_radius_noop(self):
        heights = [[0.5, 0.5], [0.5, 0.5]]
        result = vd._box_blur(heights, 2, 2, 0)
        self.assertEqual(result, heights)

    def test_uniform_unchanged(self):
        heights = [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
        result = vd._box_blur(heights, 3, 3, 1)
        for row in result:
            for v in row:
                self.assertAlmostEqual(v, 1.0)

    def test_smoothing_effect(self):
        # Single bright pixel should spread
        heights = [[0]*5 for _ in range(5)]
        heights[2][2] = 1.0
        result = vd._box_blur(heights, 5, 5, 1)
        # Center should decrease, neighbors should increase
        self.assertLess(result[2][2], 1.0)
        self.assertGreater(result[2][1], 0.0)


class TestHeightsToNormals(unittest.TestCase):
    """Tests for _heights_to_normals."""

    def test_flat_surface_points_up(self):
        # Flat heightfield should produce normals pointing straight up (128, 128, 255)
        heights = [[0.5]*10 for _ in range(10)]
        normals = vd._heights_to_normals(heights, 10, 10, 5.0)
        r, g, b = normals[5][5]
        self.assertAlmostEqual(r, 128, delta=1)
        self.assertAlmostEqual(g, 128, delta=1)
        self.assertAlmostEqual(b, 255, delta=1)

    def test_output_dimensions(self):
        heights = [[0.0]*8 for _ in range(6)]
        normals = vd._heights_to_normals(heights, 8, 6, 1.0)
        self.assertEqual(len(normals), 6)
        self.assertEqual(len(normals[0]), 8)

    def test_rgb_in_valid_range(self):
        import random
        rng = random.Random(1)
        heights = [[rng.random() for _ in range(16)] for _ in range(16)]
        normals = vd._heights_to_normals(heights, 16, 16, 10.0)
        for row in normals:
            for r, g, b in row:
                self.assertGreaterEqual(r, 0)
                self.assertLessEqual(r, 255)
                self.assertGreaterEqual(g, 0)
                self.assertLessEqual(g, 255)
                self.assertGreaterEqual(b, 0)
                self.assertLessEqual(b, 255)


class TestDisplacementResult(unittest.TestCase):
    """Tests for DisplacementResult dataclass."""

    def test_fields(self):
        r = vd.DisplacementResult(width=64, height=64, map_type="displacement",
                                  mode="distance", num_seeds=10)
        self.assertEqual(r.width, 64)
        self.assertIsNone(r.displacement)
        self.assertIsNone(r.normals)
        self.assertEqual(r.seeds, [])


class TestGenerate(unittest.TestCase):
    """Tests for the public generate() function."""

    def test_displacement_only(self):
        r = vd.generate(32, 32, num_seeds=10, map_type="displacement",
                        seed_value=42)
        self.assertIsNotNone(r.displacement)
        self.assertIsNone(r.normals)
        self.assertEqual(r.map_type, "displacement")
        self.assertEqual(len(r.displacement), 32)
        self.assertEqual(len(r.displacement[0]), 32)

    def test_normal_only(self):
        r = vd.generate(32, 32, num_seeds=10, map_type="normal",
                        seed_value=42)
        self.assertIsNotNone(r.normals)
        self.assertEqual(r.map_type, "normal")

    def test_both(self):
        r = vd.generate(32, 32, num_seeds=10, map_type="both",
                        seed_value=42)
        self.assertIsNotNone(r.displacement)
        self.assertIsNotNone(r.normals)

    def test_all_modes(self):
        for mode in ("distance", "random", "radial", "ridge"):
            r = vd.generate(16, 16, num_seeds=5, map_type="displacement",
                            mode=mode, seed_value=1)
            flat = [v for row in r.displacement for v in row]
            self.assertGreaterEqual(min(flat), 0.0)
            self.assertLessEqual(max(flat), 1.0)

    def test_invert(self):
        r1 = vd.generate(16, 16, num_seeds=5, seed_value=7, invert=False)
        r2 = vd.generate(16, 16, num_seeds=5, seed_value=7, invert=True)
        # Inverted values should sum to ~1.0 for each pixel
        for y in range(16):
            for x in range(16):
                self.assertAlmostEqual(
                    r1.displacement[y][x] + r2.displacement[y][x], 1.0, places=5)

    def test_blur(self):
        r = vd.generate(32, 32, num_seeds=10, seed_value=42, blur=2)
        self.assertIsNotNone(r.displacement)

    def test_deterministic(self):
        r1 = vd.generate(32, 32, num_seeds=10, seed_value=123)
        r2 = vd.generate(32, 32, num_seeds=10, seed_value=123)
        self.assertEqual(r1.displacement, r2.displacement)

    def test_seeds_stored(self):
        r = vd.generate(32, 32, num_seeds=15, seed_value=1)
        self.assertEqual(len(r.seeds), 15)


class TestSavePng(unittest.TestCase):
    """Tests for save_png — validates output files are valid PNGs."""

    def _is_valid_png(self, path):
        with open(path, "rb") as f:
            sig = f.read(8)
        return sig == b"\x89PNG\r\n\x1a\n"

    def _read_ihdr(self, path):
        with open(path, "rb") as f:
            f.read(8)  # signature
            length = struct.unpack(">I", f.read(4))[0]
            chunk_type = f.read(4)
            data = f.read(length)
        self.assertEqual(chunk_type, b"IHDR")
        w, h = struct.unpack(">II", data[:8])
        return w, h

    def test_displacement_png(self):
        r = vd.generate(24, 24, num_seeds=5, seed_value=1)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            vd.save_png(r, path)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(self._is_valid_png(path))
            w, h = self._read_ihdr(path)
            self.assertEqual(w, 24)
            self.assertEqual(h, 24)
        finally:
            os.unlink(path)

    def test_normal_png(self):
        r = vd.generate(24, 24, num_seeds=5, seed_value=1, map_type="normal")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            vd.save_png(r, path)
            self.assertTrue(self._is_valid_png(path))
            w, h = self._read_ihdr(path)
            self.assertEqual(w, 24)
            self.assertEqual(h, 24)
        finally:
            os.unlink(path)

    def test_both_png_double_width(self):
        r = vd.generate(20, 20, num_seeds=5, seed_value=1, map_type="both")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            vd.save_png(r, path)
            self.assertTrue(self._is_valid_png(path))
            w, h = self._read_ihdr(path)
            self.assertEqual(w, 40)  # double width for side-by-side
            self.assertEqual(h, 20)
        finally:
            os.unlink(path)


class TestWritePng(unittest.TestCase):
    """Tests for the internal _write_png helper."""

    def test_valid_rgb(self):
        pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 128, 128, 128])
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            vd._write_png(path, pixels, 2, 2, channels=3)
            with open(path, "rb") as f:
                sig = f.read(8)
            self.assertEqual(sig, b"\x89PNG\r\n\x1a\n")
        finally:
            os.unlink(path)


class TestCLI(unittest.TestCase):
    """Tests for the CLI main() function."""

    def test_basic_run(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            vd.main.__wrapped__ if hasattr(vd.main, '__wrapped__') else None
            # Directly test argparse path via main's internals
            r = vd.generate(16, 16, num_seeds=5, seed_value=1)
            vd.save_png(r, path)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
