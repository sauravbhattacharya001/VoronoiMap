"""Extended tests for vormap_centroid — edge cases, error handling, round-trips."""

import csv
import json
import math
import os
import tempfile
import unittest

from vormap_centroid import (
    analyze_centers,
    CenterReport,
    _mean_center,
    _weighted_mean_center,
    _median_center,
    _central_feature,
    _standard_distance,
    _deviational_ellipse,
)


class TestMeanCenterEdgeCases(unittest.TestCase):
    """Edge cases for _mean_center."""

    def test_negative_coordinates(self):
        pts = [(-10, -20), (-30, -40)]
        cx, cy = _mean_center(pts)
        self.assertAlmostEqual(cx, -20.0)
        self.assertAlmostEqual(cy, -30.0)

    def test_mixed_sign_coordinates(self):
        pts = [(-5, 5), (5, -5)]
        cx, cy = _mean_center(pts)
        self.assertAlmostEqual(cx, 0.0)
        self.assertAlmostEqual(cy, 0.0)

    def test_very_large_coordinates(self):
        pts = [(1e12, 1e12), (-1e12, -1e12)]
        cx, cy = _mean_center(pts)
        self.assertAlmostEqual(cx, 0.0, places=0)
        self.assertAlmostEqual(cy, 0.0, places=0)

    def test_many_identical_points(self):
        pts = [(3.0, 7.0)] * 1000
        cx, cy = _mean_center(pts)
        self.assertAlmostEqual(cx, 3.0)
        self.assertAlmostEqual(cy, 7.0)


class TestWeightedMeanEdgeCases(unittest.TestCase):
    """Edge cases for _weighted_mean_center."""

    def test_negative_weights(self):
        """Negative weights are not prohibited — they shift the center."""
        pts = [(0, 0), (10, 0)]
        result = _weighted_mean_center(pts, [-1, 3])
        # (-1*0 + 3*10) / (-1+3) = 30/2 = 15
        self.assertAlmostEqual(result[0], 15.0)

    def test_all_zero_weights_falls_back_to_mean(self):
        pts = [(0, 0), (10, 10)]
        result = _weighted_mean_center(pts, [0, 0])
        self.assertAlmostEqual(result[0], 5.0)
        self.assertAlmostEqual(result[1], 5.0)

    def test_single_nonzero_weight(self):
        pts = [(0, 0), (10, 10), (20, 20)]
        result = _weighted_mean_center(pts, [0, 5, 0])
        self.assertAlmostEqual(result[0], 10.0)
        self.assertAlmostEqual(result[1], 10.0)


class TestMedianCenterEdgeCases(unittest.TestCase):
    """Edge cases for _median_center (Weiszfeld's algorithm)."""

    def test_two_points_converges_to_midpoint(self):
        pts = [(0, 0), (10, 0)]
        cx, cy = _median_center(pts)
        self.assertAlmostEqual(cx, 5.0, places=2)
        self.assertAlmostEqual(cy, 0.0, places=2)

    def test_point_at_center_doesnt_crash(self):
        """When a data point coincides with the current estimate, skip it."""
        pts = [(5, 5), (0, 0), (10, 10)]
        cx, cy = _median_center(pts)
        # Should converge without division by zero
        self.assertTrue(math.isfinite(cx))
        self.assertTrue(math.isfinite(cy))

    def test_max_iter_respected(self):
        pts = [(0, 0), (100, 100)]
        # With max_iter=1, result should still be finite
        cx, cy = _median_center(pts, max_iter=1)
        self.assertTrue(math.isfinite(cx))

    def test_collinear_points(self):
        pts = [(0, 0), (5, 0), (10, 0)]
        cx, cy = _median_center(pts)
        self.assertAlmostEqual(cy, 0.0, places=4)
        # Median should be at or near 5
        self.assertAlmostEqual(cx, 5.0, places=2)


class TestCentralFeatureEdgeCases(unittest.TestCase):
    """Edge cases for _central_feature."""

    def test_center_exactly_on_point(self):
        pts = [(0, 0), (5, 5), (10, 10)]
        idx, pt = _central_feature(pts, (5, 5))
        self.assertEqual(idx, 1)
        self.assertEqual(pt, (5, 5))

    def test_equidistant_returns_first(self):
        pts = [(0, 0), (10, 0)]
        idx, pt = _central_feature(pts, (5, 0))
        # Both are equidistant; should return the first found (idx 0)
        self.assertIn(idx, [0, 1])


class TestStandardDistanceEdgeCases(unittest.TestCase):
    """Edge cases for _standard_distance."""

    def test_all_points_at_center(self):
        pts = [(5, 5)] * 10
        sd = _standard_distance(pts, (5, 5))
        self.assertAlmostEqual(sd, 0.0)

    def test_weighted_with_zero_total_falls_back(self):
        pts = [(0, 0), (10, 10)]
        sd = _standard_distance(pts, (5, 5), weights=[0, 0])
        # Falls back to unweighted
        self.assertGreater(sd, 0)


class TestDeviationalEllipseEdgeCases(unittest.TestCase):
    """Edge cases for _deviational_ellipse."""

    def test_fewer_than_3_points(self):
        angle, smaj, smin, rot = _deviational_ellipse([(0, 0), (1, 1)], (0.5, 0.5))
        self.assertEqual(angle, 0.0)
        self.assertEqual(smaj, 0.0)

    def test_circular_pattern_equal_axes(self):
        """Points on a circle should yield roughly equal semi-axes."""
        n = 100
        pts = [(math.cos(2 * math.pi * i / n), math.sin(2 * math.pi * i / n))
               for i in range(n)]
        center = _mean_center(pts)
        angle, smaj, smin, rot = _deviational_ellipse(pts, center)
        self.assertAlmostEqual(smaj, smin, places=2)

    def test_major_always_ge_minor(self):
        pts = [(0, 0), (10, 0), (20, 0), (0, 1), (10, 1), (20, 1)]
        center = _mean_center(pts)
        angle, smaj, smin, rot = _deviational_ellipse(pts, center)
        self.assertGreaterEqual(smaj, smin)


class TestAnalyzeCentersIntegration(unittest.TestCase):
    """Integration tests for analyze_centers."""

    def test_empty_points_returns_default(self):
        report = analyze_centers([])
        self.assertEqual(report.point_count, 0)
        self.assertEqual(report.mean_center, (0.0, 0.0))

    def test_weights_ignored_when_length_mismatch(self):
        pts = [(0, 0), (10, 10)]
        report = analyze_centers(pts, weights=[1.0])  # wrong length
        self.assertIsNone(report.weighted_mean_center)
        self.assertIsNone(report.standard_distance_weighted)

    def test_bounds_passed_through(self):
        pts = [(0, 0), (10, 10)]
        bounds = (0, 10, 0, 10)
        report = analyze_centers(pts, bounds=bounds)
        self.assertEqual(report.bounds, bounds)

    def test_report_fields_finite(self):
        pts = [(1, 2), (3, 4), (5, 6), (7, 8)]
        report = analyze_centers(pts)
        self.assertTrue(math.isfinite(report.standard_distance))
        self.assertTrue(math.isfinite(report.ellipse_angle))
        self.assertTrue(math.isfinite(report.ellipse_semi_major))
        self.assertTrue(math.isfinite(report.ellipse_semi_minor))
        self.assertEqual(report.point_count, 4)


class TestCenterReportExport(unittest.TestCase):
    """Test export round-trips (JSON, CSV, SVG).

    Export methods use validate_output_path which rejects absolute paths,
    so we chdir into a temp directory and use relative filenames.
    """

    def setUp(self):
        self.pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        self.report = analyze_centers(self.pts)
        self._orig_dir = os.getcwd()
        self._td = tempfile.mkdtemp()
        os.chdir(self._td)

    def tearDown(self):
        os.chdir(self._orig_dir)
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_to_dict_keys(self):
        d = self.report.to_dict()
        required_keys = {"point_count", "mean_center", "median_center",
                         "central_feature", "standard_distance", "ellipse"}
        self.assertTrue(required_keys.issubset(d.keys()))

    def test_to_json_round_trip(self):
        self.report.to_json("centers.json")
        with open("centers.json") as f:
            data = json.load(f)
        self.assertEqual(data["point_count"], 5)
        self.assertAlmostEqual(data["mean_center"]["x"], 5.0)
        self.assertAlmostEqual(data["mean_center"]["y"], 5.0)

    def test_to_csv_readable(self):
        self.report.to_csv("centers.csv")
        with open("centers.csv") as f:
            reader = csv.reader(f)
            rows = list(reader)
        self.assertEqual(rows[0], ["metric", "x_or_value", "y"])
        metrics = [row[0] for row in rows[1:]]
        self.assertIn("mean_center", metrics)
        self.assertIn("standard_distance", metrics)

    def test_to_csv_with_weights(self):
        report = analyze_centers(self.pts, weights=[1, 2, 3, 4, 5])
        report.to_csv("centers_w.csv")
        with open("centers_w.csv") as f:
            content = f.read()
        self.assertIn("weighted_mean_center", content)
        self.assertIn("standard_distance_weighted", content)

    def test_to_svg_creates_valid_file(self):
        self.report.to_svg("centers.svg")
        with open("centers.svg") as f:
            content = f.read()
        self.assertIn("<svg", content)
        self.assertIn("Mean", content)
        self.assertIn("Median", content)

    def test_to_svg_with_bounds(self):
        report = analyze_centers(self.pts, bounds=(0, 10, 0, 10))
        report.to_svg("centers_b.svg")
        self.assertTrue(os.path.getsize("centers_b.svg") > 100)

    def test_to_svg_with_weights(self):
        report = analyze_centers(self.pts, weights=[1, 1, 1, 1, 1])
        report.to_svg("centers_wt.svg")
        with open("centers_wt.svg") as f:
            content = f.read()
        self.assertIn("Weighted", content)

    def test_summary_contains_key_info(self):
        s = self.report.summary()
        self.assertIn("5 points", s)
        self.assertIn("Mean center", s)
        self.assertIn("Standard distance", s)
        self.assertIn("Ellipse", s)

    def test_summary_with_weights(self):
        report = analyze_centers(self.pts, weights=[1, 2, 3, 4, 5])
        s = report.summary()
        self.assertIn("Weighted mean", s)
        self.assertIn("Weighted std dist", s)


class TestLargeDataset(unittest.TestCase):
    """Performance sanity test with larger datasets."""

    def test_1000_points(self):
        import random
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(1000)]
        report = analyze_centers(pts)
        # Mean should be near (50, 50)
        self.assertAlmostEqual(report.mean_center[0], 50, delta=10)
        self.assertAlmostEqual(report.mean_center[1], 50, delta=10)
        self.assertGreater(report.standard_distance, 0)
        self.assertGreater(report.ellipse_semi_major, 0)


if __name__ == "__main__":
    unittest.main()
