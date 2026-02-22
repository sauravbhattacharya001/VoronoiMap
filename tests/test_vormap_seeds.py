"""Tests for vormap_seeds — seed point generators for Voronoi diagrams."""

import math
import os
import tempfile
import unittest

from vormap_seeds import (
    random_uniform, grid, hexagonal, jittered_grid, poisson_disk,
    halton, save_seeds, load_seeds, _validate_bounds,
)


class TestRandomUniform(unittest.TestCase):
    """Tests for random_uniform generator."""

    def test_generates_correct_count(self):
        pts = random_uniform(50, seed=1)
        self.assertEqual(len(pts), 50)

    def test_points_within_bounds(self):
        pts = random_uniform(200, 10, 90, 20, 80, seed=2)
        for x, y in pts:
            self.assertGreaterEqual(x, 10)
            self.assertLessEqual(x, 90)
            self.assertGreaterEqual(y, 20)
            self.assertLessEqual(y, 80)

    def test_seed_reproducibility(self):
        a = random_uniform(30, seed=42)
        b = random_uniform(30, seed=42)
        self.assertEqual(a, b)

    def test_different_seeds_differ(self):
        a = random_uniform(30, seed=1)
        b = random_uniform(30, seed=2)
        self.assertNotEqual(a, b)

    def test_single_point(self):
        pts = random_uniform(1, seed=5)
        self.assertEqual(len(pts), 1)

    def test_invalid_n_zero(self):
        with self.assertRaises(ValueError):
            random_uniform(0)

    def test_invalid_n_negative(self):
        with self.assertRaises(ValueError):
            random_uniform(-5)

    def test_invalid_bounds(self):
        with self.assertRaises(ValueError):
            random_uniform(10, 100, 50, 0, 100)  # x_min > x_max


class TestGrid(unittest.TestCase):
    """Tests for grid generator."""

    def test_correct_count(self):
        pts = grid(rows=5, cols=8)
        self.assertEqual(len(pts), 40)

    def test_single_row_col(self):
        pts = grid(rows=1, cols=1)
        self.assertEqual(len(pts), 1)

    def test_points_within_bounds(self):
        pts = grid(10, 90, 20, 80, rows=5, cols=5)
        for x, y in pts:
            self.assertGreaterEqual(x, 10)
            self.assertLessEqual(x, 90)
            self.assertGreaterEqual(y, 20)
            self.assertLessEqual(y, 80)

    def test_corners_on_bounds(self):
        pts = grid(0, 100, 0, 100, rows=2, cols=2)
        xs = sorted(set(p[0] for p in pts))
        ys = sorted(set(p[1] for p in pts))
        self.assertAlmostEqual(xs[0], 0)
        self.assertAlmostEqual(xs[-1], 100)
        self.assertAlmostEqual(ys[0], 0)
        self.assertAlmostEqual(ys[-1], 100)

    def test_with_margin(self):
        pts = grid(0, 100, 0, 100, rows=3, cols=3, margin=10)
        for x, y in pts:
            self.assertGreaterEqual(x, 10)
            self.assertLessEqual(x, 90)
            self.assertGreaterEqual(y, 10)
            self.assertLessEqual(y, 90)

    def test_margin_too_large(self):
        with self.assertRaises(ValueError):
            grid(0, 100, 0, 100, rows=3, cols=3, margin=55)

    def test_invalid_rows(self):
        with self.assertRaises(ValueError):
            grid(rows=0, cols=5)

    def test_negative_margin(self):
        with self.assertRaises(ValueError):
            grid(margin=-1)


class TestHexagonal(unittest.TestCase):
    """Tests for hexagonal grid generator."""

    def test_generates_points(self):
        pts = hexagonal(0, 500, 0, 500, spacing=100)
        self.assertGreater(len(pts), 0)

    def test_points_within_bounds(self):
        pts = hexagonal(10, 200, 10, 200, spacing=30)
        for x, y in pts:
            self.assertGreaterEqual(x, 10)
            self.assertLessEqual(x, 200)
            self.assertGreaterEqual(y, 10)
            self.assertLessEqual(y, 200)

    def test_odd_rows_offset(self):
        pts = hexagonal(0, 1000, 0, 1000, spacing=200)
        # Group by y to find rows
        row_ys = sorted(set(round(p[1], 6) for p in pts))
        if len(row_ys) >= 2:
            row0 = [p[0] for p in pts if abs(p[1] - row_ys[0]) < 1]
            row1 = [p[0] for p in pts if abs(p[1] - row_ys[1]) < 1]
            if row0 and row1:
                # Odd row should be offset by spacing/2
                offset = abs(min(row1) - min(row0))
                self.assertAlmostEqual(offset, 100.0, places=1)

    def test_with_margin(self):
        pts = hexagonal(0, 500, 0, 500, spacing=50, margin=20)
        for x, y in pts:
            self.assertGreaterEqual(x, 20)
            self.assertLessEqual(x, 480)

    def test_invalid_spacing(self):
        with self.assertRaises(ValueError):
            hexagonal(spacing=0)

    def test_negative_spacing(self):
        with self.assertRaises(ValueError):
            hexagonal(spacing=-10)


class TestJitteredGrid(unittest.TestCase):
    """Tests for jittered_grid generator."""

    def test_correct_count(self):
        pts = jittered_grid(rows=5, cols=8, seed=1)
        self.assertEqual(len(pts), 40)

    def test_zero_jitter_equals_grid_centers(self):
        pts = jittered_grid(0, 100, 0, 100, rows=2, cols=2, jitter=0.0)
        # With zero jitter, points should be at cell centers
        expected_xs = {25.0, 75.0}
        expected_ys = {25.0, 75.0}
        actual_xs = set(round(p[0], 6) for p in pts)
        actual_ys = set(round(p[1], 6) for p in pts)
        self.assertEqual(actual_xs, expected_xs)
        self.assertEqual(actual_ys, expected_ys)

    def test_points_within_bounds(self):
        pts = jittered_grid(10, 90, 20, 80, rows=5, cols=5,
                            jitter=1.0, seed=3)
        for x, y in pts:
            # With jitter, points stay within their cells which are
            # within bounds
            self.assertGreaterEqual(x, 10 - 1)  # Small tolerance
            self.assertLessEqual(x, 90 + 1)

    def test_seed_reproducibility(self):
        a = jittered_grid(rows=5, cols=5, jitter=0.5, seed=42)
        b = jittered_grid(rows=5, cols=5, jitter=0.5, seed=42)
        self.assertEqual(a, b)

    def test_invalid_jitter_over_1(self):
        with self.assertRaises(ValueError):
            jittered_grid(jitter=1.5)

    def test_invalid_jitter_negative(self):
        with self.assertRaises(ValueError):
            jittered_grid(jitter=-0.1)


class TestPoissonDisk(unittest.TestCase):
    """Tests for poisson_disk (Bridson's algorithm)."""

    def test_generates_points(self):
        pts = poisson_disk(0, 500, 0, 500, min_dist=50, seed=1)
        self.assertGreater(len(pts), 0)

    def test_minimum_distance_respected(self):
        pts = poisson_disk(0, 500, 0, 500, min_dist=40, seed=42)
        min_dist_sq = 40 * 40
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[i][0] - pts[j][0]
                dy = pts[i][1] - pts[j][1]
                self.assertGreaterEqual(dx * dx + dy * dy,
                                        min_dist_sq * 0.99)

    def test_points_within_bounds(self):
        pts = poisson_disk(10, 200, 30, 150, min_dist=20, seed=5)
        for x, y in pts:
            self.assertGreaterEqual(x, 10)
            self.assertLessEqual(x, 200)
            self.assertGreaterEqual(y, 30)
            self.assertLessEqual(y, 150)

    def test_seed_reproducibility(self):
        a = poisson_disk(0, 300, 0, 300, min_dist=30, seed=42)
        b = poisson_disk(0, 300, 0, 300, min_dist=30, seed=42)
        self.assertEqual(a, b)

    def test_smaller_distance_more_points(self):
        a = poisson_disk(0, 500, 0, 500, min_dist=100, seed=1)
        b = poisson_disk(0, 500, 0, 500, min_dist=50, seed=1)
        self.assertGreater(len(b), len(a))

    def test_invalid_min_dist(self):
        with self.assertRaises(ValueError):
            poisson_disk(min_dist=0)

    def test_negative_min_dist(self):
        with self.assertRaises(ValueError):
            poisson_disk(min_dist=-10)

    def test_invalid_max_attempts(self):
        with self.assertRaises(ValueError):
            poisson_disk(max_attempts=0)

    def test_large_min_dist_few_points(self):
        pts = poisson_disk(0, 100, 0, 100, min_dist=80, seed=1)
        # Only a few points should fit
        self.assertLessEqual(len(pts), 10)
        self.assertGreaterEqual(len(pts), 1)


class TestHalton(unittest.TestCase):
    """Tests for Halton quasi-random sequence."""

    def test_generates_correct_count(self):
        pts = halton(50)
        self.assertEqual(len(pts), 50)

    def test_points_within_bounds(self):
        pts = halton(100, 10, 90, 20, 80)
        for x, y in pts:
            self.assertGreaterEqual(x, 10)
            self.assertLessEqual(x, 90)
            self.assertGreaterEqual(y, 20)
            self.assertLessEqual(y, 80)

    def test_deterministic(self):
        a = halton(30)
        b = halton(30)
        self.assertEqual(a, b)

    def test_skip(self):
        a = halton(10, skip=0)
        b = halton(10, skip=5)
        # Different starting points
        self.assertNotEqual(a, b)
        # Skipped sequence should overlap with unskipped
        c = halton(15, skip=0)
        # b[0] should equal c[5]
        self.assertAlmostEqual(b[0][0], c[5][0], places=10)
        self.assertAlmostEqual(b[0][1], c[5][1], places=10)

    def test_custom_bases(self):
        pts = halton(20, bases=(3, 5))
        self.assertEqual(len(pts), 20)

    def test_invalid_n(self):
        with self.assertRaises(ValueError):
            halton(0)

    def test_invalid_bases(self):
        with self.assertRaises(ValueError):
            halton(10, bases=(1, 3))

    def test_negative_skip(self):
        with self.assertRaises(ValueError):
            halton(10, skip=-1)

    def test_good_space_filling(self):
        pts = halton(100, 0, 100, 0, 100)
        # Check that points fill all four quadrants
        q1 = sum(1 for x, y in pts if x < 50 and y < 50)
        q2 = sum(1 for x, y in pts if x >= 50 and y < 50)
        q3 = sum(1 for x, y in pts if x < 50 and y >= 50)
        q4 = sum(1 for x, y in pts if x >= 50 and y >= 50)
        for q in [q1, q2, q3, q4]:
            self.assertGreater(q, 10)  # At least 10% in each quadrant


class TestSaveLoadSeeds(unittest.TestCase):
    """Tests for save_seeds and load_seeds I/O."""

    def test_round_trip(self):
        pts = [(1.5, 2.5), (10.123, 20.456), (100.0, 200.0)]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False) as f:
            fname = f.name
        try:
            save_seeds(pts, fname)
            loaded = load_seeds(fname)
            self.assertEqual(len(loaded), len(pts))
            for orig, loaded_pt in zip(pts, loaded):
                self.assertAlmostEqual(orig[0], loaded_pt[0], places=5)
                self.assertAlmostEqual(orig[1], loaded_pt[1], places=5)
        finally:
            os.unlink(fname)

    def test_round_trip_no_header(self):
        pts = [(5.0, 10.0), (15.0, 20.0)]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False) as f:
            fname = f.name
        try:
            save_seeds(pts, fname, header=False)
            loaded = load_seeds(fname)
            self.assertEqual(len(loaded), len(pts))
        finally:
            os.unlink(fname)

    def test_save_with_header(self):
        pts = [(1.0, 2.0), (3.0, 4.0)]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False) as f:
            fname = f.name
        try:
            save_seeds(pts, fname, header=True)
            with open(fname) as f:
                lines = f.readlines()
            self.assertEqual(lines[0].strip(), '2')  # count header
            self.assertEqual(len(lines), 3)  # header + 2 points
        finally:
            os.unlink(fname)

    def test_save_empty_raises(self):
        with self.assertRaises(ValueError):
            save_seeds([], 'out.txt')

    def test_save_empty_filename_raises(self):
        with self.assertRaises(ValueError):
            save_seeds([(1, 2)], '')

    def test_load_empty_filename_raises(self):
        with self.assertRaises(ValueError):
            load_seeds('')

    def test_load_skips_non_numeric(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt',
                                         delete=False) as f:
            f.write("# comment\n")
            f.write("hello world\n")
            f.write("5.0 10.0\n")
            f.write("not a number\n")
            f.write("15.0 20.0\n")
            fname = f.name
        try:
            pts = load_seeds(fname)
            self.assertEqual(len(pts), 2)
        finally:
            os.unlink(fname)


class TestValidateBounds(unittest.TestCase):
    """Tests for bounds validation."""

    def test_valid_bounds(self):
        # Should not raise
        _validate_bounds(0, 100, 0, 100)

    def test_x_min_equals_x_max(self):
        with self.assertRaises(ValueError):
            _validate_bounds(50, 50, 0, 100)

    def test_x_min_greater_than_x_max(self):
        with self.assertRaises(ValueError):
            _validate_bounds(100, 50, 0, 100)

    def test_y_min_equals_y_max(self):
        with self.assertRaises(ValueError):
            _validate_bounds(0, 100, 50, 50)

    def test_y_min_greater_than_y_max(self):
        with self.assertRaises(ValueError):
            _validate_bounds(0, 100, 100, 50)


class TestGeneratorInterop(unittest.TestCase):
    """Integration tests — generators produce valid vormap input."""

    def test_all_generators_produce_tuples(self):
        generators = [
            random_uniform(10, seed=1),
            grid(rows=3, cols=3),
            hexagonal(spacing=200),
            jittered_grid(rows=3, cols=3, seed=1),
            poisson_disk(min_dist=200, seed=1),
            halton(10),
        ]
        for pts in generators:
            self.assertGreater(len(pts), 0)
            for pt in pts:
                self.assertIsInstance(pt, tuple)
                self.assertEqual(len(pt), 2)
                self.assertIsInstance(pt[0], float)
                self.assertIsInstance(pt[1], float)

    def test_save_load_round_trip_all_generators(self):
        generators = {
            'random': random_uniform(20, seed=1),
            'grid': grid(rows=4, cols=4),
            'hex': hexagonal(spacing=300),
            'jittered': jittered_grid(rows=4, cols=4, seed=1),
            'poisson': poisson_disk(min_dist=150, seed=1),
            'halton': halton(20),
        }
        for name, pts in generators.items():
            with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.txt', delete=False) as f:
                fname = f.name
            try:
                save_seeds(pts, fname)
                loaded = load_seeds(fname)
                self.assertEqual(len(loaded), len(pts),
                                 f"{name} count mismatch")
            finally:
                os.unlink(fname)


if __name__ == '__main__':
    unittest.main()
