"""Tests for vormap_emboss module."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import vormap_emboss


class TestEmbossGenerate(unittest.TestCase):
    """Core generation tests."""

    def test_basic_generate(self):
        result = vormap_emboss.generate(80, 60, num_seeds=5, seed=42)
        self.assertEqual(result.width, 80)
        self.assertEqual(result.height, 60)
        self.assertEqual(len(result.pixels), 60)
        self.assertEqual(len(result.pixels[0]), 80)

    def test_all_materials(self):
        for mat in vormap_emboss.MATERIALS:
            result = vormap_emboss.generate(40, 30, num_seeds=3, material=mat, seed=1)
            self.assertEqual(result.material, mat)

    def test_height_vary(self):
        result = vormap_emboss.generate(40, 30, num_seeds=5, height_vary=True, seed=7)
        self.assertTrue(any(h != 1.0 for h in result.cell_heights))

    def test_chisel(self):
        result = vormap_emboss.generate(40, 30, num_seeds=5, chisel=True, seed=7)
        self.assertEqual(result.width, 40)

    def test_pixel_values_in_range(self):
        result = vormap_emboss.generate(50, 40, num_seeds=4, seed=99)
        for row in result.pixels:
            for r, g, b in row:
                self.assertTrue(0 <= r <= 255)
                self.assertTrue(0 <= g <= 255)
                self.assertTrue(0 <= b <= 255)

    def test_save_png(self):
        result = vormap_emboss.generate(30, 20, num_seeds=3, seed=1)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            vormap_emboss.save_png(result, path)
            self.assertTrue(os.path.exists(path))
            with open(path, "rb") as f:
                header = f.read(8)
            self.assertEqual(header, b"\x89PNG\r\n\x1a\n")
        finally:
            os.unlink(path)

    def test_light_angles(self):
        for angle in [0, 90, 180, 270, 315]:
            result = vormap_emboss.generate(30, 20, num_seeds=3, light_angle=angle, seed=1)
            self.assertAlmostEqual(result.light_angle, angle)

    def test_depth_extremes(self):
        for depth in [0.0, 0.5, 1.0]:
            result = vormap_emboss.generate(30, 20, num_seeds=3, depth=depth, seed=1)
            self.assertAlmostEqual(result.depth, depth)


if __name__ == "__main__":
    unittest.main()
