"""Tests for vormap_profile — Spatial Data Profiler."""

import json
import math
import os
import sys
import unittest

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vormap_profile import profile_points, _percentile, _std, _to_json, _to_csv, _to_html


class TestPercentile(unittest.TestCase):
    def test_median_odd(self):
        self.assertAlmostEqual(_percentile([1, 2, 3, 4, 5], 50), 3.0)

    def test_median_even(self):
        self.assertAlmostEqual(_percentile([1, 2, 3, 4], 50), 2.5)

    def test_min_max(self):
        data = [10, 20, 30]
        self.assertAlmostEqual(_percentile(data, 0), 10.0)
        self.assertAlmostEqual(_percentile(data, 100), 30.0)

    def test_empty(self):
        self.assertEqual(_percentile([], 50), 0.0)


class TestStd(unittest.TestCase):
    def test_uniform(self):
        self.assertAlmostEqual(_std([5, 5, 5], 5), 0.0)

    def test_simple(self):
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        mean = sum(vals) / len(vals)
        self.assertAlmostEqual(_std(vals, mean), 2.0, places=1)

    def test_single(self):
        self.assertEqual(_std([42], 42), 0.0)


class TestProfilePointsEmpty(unittest.TestCase):
    def test_empty_list(self):
        r = profile_points([])
        self.assertIn("error", r)

    def test_single_point(self):
        r = profile_points([(5.0, 10.0)])
        self.assertEqual(r["basic"]["count"], 1)
        self.assertEqual(r["centroid"]["x"], 5.0)
        self.assertEqual(r["centroid"]["y"], 10.0)


class TestProfilePointsBasic(unittest.TestCase):
    def setUp(self):
        self.pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        self.r = profile_points(self.pts)

    def test_count(self):
        self.assertEqual(self.r["basic"]["count"], 5)

    def test_bounds(self):
        b = self.r["bounds"]
        self.assertEqual(b["x_min"], 0)
        self.assertEqual(b["x_max"], 10)
        self.assertEqual(b["y_min"], 0)
        self.assertEqual(b["y_max"], 10)
        self.assertEqual(b["x_range"], 10)
        self.assertEqual(b["y_range"], 10)

    def test_centroid(self):
        self.assertEqual(self.r["centroid"]["x"], 5.0)
        self.assertEqual(self.r["centroid"]["y"], 5.0)

    def test_area(self):
        self.assertEqual(self.r["density"]["area"], 100.0)

    def test_density(self):
        self.assertAlmostEqual(self.r["density"]["points_per_unit_area"], 0.05)

    def test_diagonal(self):
        expected = math.sqrt(200)
        self.assertAlmostEqual(self.r["basic"]["diagonal"], expected, places=4)


class TestProfileCoordStats(unittest.TestCase):
    def test_symmetric_distribution(self):
        pts = [(i, i) for i in range(11)]  # 0..10
        r = profile_points(pts)
        self.assertAlmostEqual(r["x_stats"]["mean"], 5.0, places=2)
        self.assertAlmostEqual(r["y_stats"]["mean"], 5.0, places=2)
        self.assertAlmostEqual(r["x_stats"]["median"], 5.0, places=2)

    def test_skew_near_zero_for_uniform(self):
        pts = [(i, i) for i in range(100)]
        r = profile_points(pts)
        self.assertAlmostEqual(r["x_stats"]["skewness"], 0.0, places=0)


class TestProfileNearestNeighbor(unittest.TestCase):
    def test_regular_grid(self):
        pts = [(i, j) for i in range(5) for j in range(5)]
        r = profile_points(pts)
        self.assertAlmostEqual(r["spacing"]["nn_min"], 1.0, places=4)
        self.assertAlmostEqual(r["spacing"]["nn_mean"], 1.0, places=4)

    def test_two_points(self):
        r = profile_points([(0, 0), (3, 4)])
        self.assertAlmostEqual(r["spacing"]["nn_min"], 5.0, places=4)


class TestProfileQuadrants(unittest.TestCase):
    def test_balanced(self):
        pts = [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        r = profile_points(pts)
        q = r["quadrants"]
        self.assertEqual(q["NE"], 1)
        self.assertEqual(q["NW"], 1)
        self.assertEqual(q["SE"], 1)
        self.assertEqual(q["SW"], 1)
        self.assertAlmostEqual(q["balance"], 1.0)


class TestProfileDuplicates(unittest.TestCase):
    def test_no_duplicates(self):
        r = profile_points([(0, 0), (1, 1), (2, 2)])
        self.assertEqual(r["duplicates"]["count"], 0)

    def test_with_duplicates(self):
        r = profile_points([(0, 0), (0, 0), (1, 1)])
        self.assertEqual(r["duplicates"]["count"], 1)


class TestProfileSpatialPattern(unittest.TestCase):
    def test_regular_grid_dispersed(self):
        pts = [(i, j) for i in range(10) for j in range(10)]
        r = profile_points(pts)
        self.assertGreater(r["spatial_pattern"]["clark_evans_r"], 1.0)
        self.assertEqual(r["spatial_pattern"]["pattern"], "dispersed")

    def test_clustered(self):
        import random
        random.seed(42)
        # Tight cluster + scattered
        pts = [(0.5 + random.gauss(0, 0.01), 0.5 + random.gauss(0, 0.01))
               for _ in range(50)]
        pts += [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(5)]
        r = profile_points(pts)
        self.assertLess(r["spatial_pattern"]["clark_evans_r"], 1.0)


class TestProfileOutliers(unittest.TestCase):
    def test_outlier_detected(self):
        pts = [(i, 0) for i in range(20)]  # Regular spacing
        pts.append((1000, 0))  # Far outlier
        r = profile_points(pts)
        self.assertGreater(r["outliers"]["count"], 0)


class TestProfileSummary(unittest.TestCase):
    def test_summary_string(self):
        r = profile_points([(0, 0), (1, 1)])
        self.assertIn("SPATIAL DATA PROFILE", r["summary"])
        self.assertIn("Points:", r["summary"])


class TestExportJson(unittest.TestCase):
    def test_valid_json(self):
        r = profile_points([(0, 0), (1, 1), (2, 2)])
        j = _to_json(r)
        parsed = json.loads(j)
        self.assertIn("basic", parsed)
        self.assertNotIn("summary", parsed)


class TestExportCsv(unittest.TestCase):
    def test_csv_header(self):
        r = profile_points([(0, 0), (1, 1)])
        csv = _to_csv(r)
        lines = csv.split("\n")
        self.assertEqual(lines[0], "key,value")
        self.assertGreater(len(lines), 10)


class TestExportHtml(unittest.TestCase):
    def test_html_structure(self):
        r = profile_points([(0, 0), (5, 5), (10, 10)])
        html = _to_html(r)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Spatial Data Profile", html)
        self.assertIn("Clark-Evans", html)
        self.assertIn("Quadrant", html)


class TestEdgeCases(unittest.TestCase):
    def test_collinear_points(self):
        """Points on a line — zero area."""
        pts = [(i, 0) for i in range(10)]
        r = profile_points(pts)
        self.assertEqual(r["density"]["area"], 0.0)
        self.assertEqual(r["density"]["points_per_unit_area"], 0)

    def test_two_identical_points(self):
        r = profile_points([(5, 5), (5, 5)])
        self.assertEqual(r["duplicates"]["count"], 1)
        self.assertEqual(r["spacing"]["nn_min"], 0.0)

    def test_large_coordinates(self):
        pts = [(1e6 + i, 2e6 + i) for i in range(10)]
        r = profile_points(pts)
        self.assertEqual(r["basic"]["count"], 10)


if __name__ == "__main__":
    unittest.main()
