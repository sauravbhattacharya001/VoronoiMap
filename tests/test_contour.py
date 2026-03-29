"""Tests for vormap_contour — contour line extraction."""

import json
import math
import os
import tempfile
import unittest

from vormap_contour import (
    auto_levels,
    contour_length,
    contour_to_geojson,
    export_contour_svg,
    extract_contours,
    generate_contours,
    _idw_grid,
    _march_squares,
    _chain_segments,
    _lerp,
)


class TestAutoLevels(unittest.TestCase):
    def test_basic(self):
        levels = auto_levels([10, 20, 30, 40, 50], 4)
        self.assertEqual(len(levels), 4)
        self.assertGreater(levels[0], 10)
        self.assertLess(levels[-1], 50)

    def test_evenly_spaced(self):
        levels = auto_levels([0, 100], 9)
        for i in range(len(levels) - 1):
            self.assertAlmostEqual(levels[i + 1] - levels[i],
                                   levels[1] - levels[0], places=6)

    def test_empty(self):
        self.assertEqual(auto_levels([], 5), [])

    def test_zero_range(self):
        self.assertEqual(auto_levels([5, 5, 5], 3), [])

    def test_single_level(self):
        levels = auto_levels([0, 10], 1)
        self.assertEqual(len(levels), 1)
        self.assertAlmostEqual(levels[0], 5.0)


class TestContourLength(unittest.TestCase):
    def test_straight_line(self):
        path = [(0, 0), (3, 0), (3, 4)]
        self.assertAlmostEqual(contour_length(path), 7.0)

    def test_single_point(self):
        self.assertAlmostEqual(contour_length([(1, 1)]), 0.0)

    def test_empty(self):
        self.assertAlmostEqual(contour_length([]), 0.0)


class TestLerp(unittest.TestCase):
    def test_midpoint(self):
        self.assertAlmostEqual(_lerp(0, 10, 0, 10, 5), 0.5)

    def test_equal_values(self):
        self.assertAlmostEqual(_lerp(0, 10, 5, 5, 5), 0.5)


class TestIDWGrid(unittest.TestCase):
    def test_exact_at_seed(self):
        seeds = [(5.0, 5.0)]
        values = [42.0]
        grid = _idw_grid(seeds, values, 5.0, 5.0, 1.0, 1.0, 1, 1)
        self.assertAlmostEqual(grid[0][0], 42.0)

    def test_grid_shape(self):
        seeds = [(0, 0), (10, 10)]
        values = [1.0, 2.0]
        grid = _idw_grid(seeds, values, 0, 0, 1, 1, 5, 5)
        self.assertEqual(len(grid), 5)
        self.assertEqual(len(grid[0]), 5)


class TestMarchSquares(unittest.TestCase):
    def test_simple_gradient(self):
        # Linear gradient: left=0, right=10
        grid = [[i * 2.0 for i in range(6)] for _ in range(6)]
        paths = _march_squares(grid, 5.0, 0, 0, 1, 1)
        self.assertGreater(len(paths), 0)
        # All contour points should have x ≈ 2.5
        for path in paths:
            for x, y in path:
                self.assertAlmostEqual(x, 2.5, places=3)

    def test_no_contour_below_min(self):
        grid = [[5.0] * 3 for _ in range(3)]
        paths = _march_squares(grid, 10.0, 0, 0, 1, 1)
        self.assertEqual(len(paths), 0)

    def test_no_contour_above_max(self):
        grid = [[5.0] * 3 for _ in range(3)]
        paths = _march_squares(grid, 1.0, 0, 0, 1, 1)
        self.assertEqual(len(paths), 0)


class TestChainSegments(unittest.TestCase):
    def test_chain_connected(self):
        segs = [((0, 0), (1, 0)), ((1, 0), (2, 0)), ((2, 0), (3, 0))]
        chains = _chain_segments(segs)
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]), 4)

    def test_chain_disconnected(self):
        segs = [((0, 0), (1, 0)), ((5, 5), (6, 6))]
        chains = _chain_segments(segs)
        self.assertEqual(len(chains), 2)

    def test_empty(self):
        self.assertEqual(_chain_segments([]), [])


class TestExtractContours(unittest.TestCase):
    def setUp(self):
        # Grid of seeds with a radial gradient
        self.seeds = []
        self.values = []
        for i in range(5):
            for j in range(5):
                x = 100 + i * 200
                y = 100 + j * 200
                self.seeds.append((x, y))
                # Distance from center
                d = math.sqrt((x - 500) ** 2 + (y - 500) ** 2)
                self.values.append(d)

    def test_returns_contours(self):
        result = extract_contours(self.seeds, self.values, levels=5,
                                  resolution=30)
        self.assertEqual(len(result), 5)
        for c in result:
            self.assertIn("level", c)
            self.assertIn("paths", c)
            self.assertIn("total_length", c)

    def test_length_mismatch(self):
        with self.assertRaises(ValueError):
            extract_contours([(0, 0)], [1, 2])

    def test_too_few_seeds(self):
        with self.assertRaises(ValueError):
            extract_contours([(0, 0), (1, 1)], [1, 2])

    def test_explicit_levels(self):
        result = extract_contours(self.seeds, self.values,
                                  levels=[200.0, 400.0])
        self.assertEqual(len(result), 2)
        self.assertAlmostEqual(result[0]["level"], 200.0)

    def test_auto_levels_default(self):
        result = extract_contours(self.seeds, self.values)
        self.assertEqual(len(result), 8)  # default

    def test_paths_have_points(self):
        result = extract_contours(self.seeds, self.values, levels=3,
                                  resolution=40)
        has_paths = any(len(c["paths"]) > 0 for c in result)
        self.assertTrue(has_paths)


class TestContourToGeoJSON(unittest.TestCase):
    def test_basic_structure(self):
        contours = [{
            "level": 5.0,
            "paths": [[(0, 0), (1, 1), (2, 0)]],
            "total_length": 3.0,
        }]
        gj = contour_to_geojson(contours)
        self.assertEqual(gj["type"], "FeatureCollection")
        self.assertEqual(len(gj["features"]), 1)
        feat = gj["features"][0]
        self.assertEqual(feat["geometry"]["type"], "MultiLineString")
        self.assertAlmostEqual(feat["properties"]["level"], 5.0)

    def test_empty_paths_skipped(self):
        contours = [{"level": 1.0, "paths": [], "total_length": 0}]
        gj = contour_to_geojson(contours)
        self.assertEqual(len(gj["features"]), 0)


class TestExportContourSVG(unittest.TestCase):
    def test_creates_svg(self):
        contours = [{
            "level": 5.0,
            "paths": [[(100, 100), (200, 200), (300, 100)]],
            "total_length": 300.0,
        }]
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            result = export_contour_svg(contours, path)
            self.assertTrue(os.path.exists(result))
            with open(result) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("path", content)
        finally:
            os.unlink(path)

    def test_with_seeds(self):
        contours = [{
            "level": 5.0,
            "paths": [[(100, 100), (200, 200)]],
            "total_length": 141.4,
        }]
        seeds = [(150, 150)]
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_contour_svg(contours, path, seeds=seeds)
            with open(path) as f:
                content = f.read()
            self.assertIn("circle", content)
        finally:
            os.unlink(path)

    def test_colormaps(self):
        contours = [{"level": i, "paths": [[(i*10, 0), (i*10, 100)]],
                      "total_length": 100} for i in range(5)]
        for cmap in ["viridis", "plasma", "terrain", "grayscale"]:
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
                path = f.name
            try:
                export_contour_svg(contours, path, colormap=cmap)
                self.assertTrue(os.path.exists(path))
            finally:
                os.unlink(path)


class TestGenerateContours(unittest.TestCase):
    def setUp(self):
        self.seeds = [(100, 100), (500, 100), (300, 500),
                      (100, 500), (500, 500)]
        self.values = [10, 20, 30, 15, 25]

    def test_pipeline(self):
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            svg_path = f.name
        try:
            result = generate_contours(self.seeds, self.values, svg_path,
                                       levels=4, resolution=30)
            self.assertEqual(result["levels"], 4)
            self.assertGreaterEqual(result["total_paths"], 0)
            self.assertTrue(os.path.exists(svg_path))
        finally:
            os.unlink(svg_path)

    def test_with_geojson(self):
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            svg_path = f.name
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            gj_path = f.name
        try:
            result = generate_contours(self.seeds, self.values, svg_path,
                                       geojson_path=gj_path, levels=3)
            self.assertIsNotNone(result["geojson_path"])
            with open(gj_path) as f:
                gj = json.load(f)
            self.assertEqual(gj["type"], "FeatureCollection")
        finally:
            os.unlink(svg_path)
            if os.path.exists(gj_path):
                os.unlink(gj_path)


if __name__ == "__main__":
    unittest.main()
