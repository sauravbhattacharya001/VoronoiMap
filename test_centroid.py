"""Tests for vormap_centroid — Spatial Center Analysis."""

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


class TestMeanCenter(unittest.TestCase):
    def test_basic(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cx, cy = _mean_center(pts)
        self.assertAlmostEqual(cx, 5.0)
        self.assertAlmostEqual(cy, 5.0)

    def test_single_point(self):
        cx, cy = _mean_center([(7.5, 3.2)])
        self.assertAlmostEqual(cx, 7.5)
        self.assertAlmostEqual(cy, 3.2)

    def test_collinear(self):
        pts = [(0, 0), (5, 0), (10, 0)]
        cx, cy = _mean_center(pts)
        self.assertAlmostEqual(cx, 5.0)
        self.assertAlmostEqual(cy, 0.0)


class TestWeightedMeanCenter(unittest.TestCase):
    def test_equal_weights(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cx, cy = _weighted_mean_center(pts, [1, 1, 1, 1])
        self.assertAlmostEqual(cx, 5.0)
        self.assertAlmostEqual(cy, 5.0)

    def test_skewed_weights(self):
        pts = [(0, 0), (10, 0)]
        cx, cy = _weighted_mean_center(pts, [3, 1])
        self.assertAlmostEqual(cx, 2.5)
        self.assertAlmostEqual(cy, 0.0)

    def test_zero_weights_fallback(self):
        pts = [(0, 0), (10, 0)]
        cx, cy = _weighted_mean_center(pts, [0, 0])
        self.assertAlmostEqual(cx, 5.0)  # falls back to mean


class TestMedianCenter(unittest.TestCase):
    def test_symmetric(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cx, cy = _median_center(pts)
        self.assertAlmostEqual(cx, 5.0, places=3)
        self.assertAlmostEqual(cy, 5.0, places=3)

    def test_with_outlier(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1), (100, 100)]
        cx, cy = _median_center(pts)
        # Median center should be pulled less than mean
        mx, my = _mean_center(pts)
        self.assertLess(cx, mx)
        self.assertLess(cy, my)


class TestCentralFeature(unittest.TestCase):
    def test_finds_closest(self):
        pts = [(0, 0), (4, 5), (10, 10)]
        center = (5, 5)
        idx, pt = _central_feature(pts, center)
        self.assertEqual(idx, 1)
        self.assertEqual(pt, (4, 5))

    def test_exact_match(self):
        pts = [(0, 0), (5, 5), (10, 10)]
        idx, pt = _central_feature(pts, (5, 5))
        self.assertEqual(idx, 1)


class TestStandardDistance(unittest.TestCase):
    def test_basic(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        center = (5, 5)
        sd = _standard_distance(pts, center)
        expected = math.sqrt((25 + 25) * 4 / 4)
        self.assertAlmostEqual(sd, expected)

    def test_single_point(self):
        sd = _standard_distance([(5, 5)], (5, 5))
        self.assertAlmostEqual(sd, 0.0)

    def test_weighted(self):
        pts = [(0, 0), (10, 0)]
        sd = _standard_distance(pts, (5, 0), weights=[1, 1])
        self.assertAlmostEqual(sd, 5.0)

    def test_weighted_skewed(self):
        pts = [(0, 0), (10, 0)]
        sd_even = _standard_distance(pts, (5, 0), weights=[1, 1])
        sd_skew = _standard_distance(pts, (2.5, 0), weights=[3, 1])
        # Both valid, just checking they compute
        self.assertGreater(sd_even, 0)
        self.assertGreater(sd_skew, 0)


class TestDeviationalEllipse(unittest.TestCase):
    def test_circular_pattern(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        center = (5, 5)
        angle, smaj, smin, rot = _deviational_ellipse(pts, center)
        # For a square pattern, major ≈ minor
        self.assertAlmostEqual(smaj, smin, places=3)

    def test_elongated_x(self):
        pts = [(0, 0), (100, 0), (200, 0), (0, 1), (100, 1), (200, 1)]
        center = _mean_center(pts)
        angle, smaj, smin, rot = _deviational_ellipse(pts, center)
        self.assertGreater(smaj, smin)

    def test_too_few_points(self):
        angle, smaj, smin, rot = _deviational_ellipse([(0, 0), (1, 1)], (0.5, 0.5))
        self.assertEqual(angle, 0.0)
        self.assertEqual(smaj, 0.0)


class TestAnalyzeCenters(unittest.TestCase):
    def test_empty(self):
        report = analyze_centers([])
        self.assertEqual(report.point_count, 0)

    def test_basic(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        report = analyze_centers(pts)
        self.assertEqual(report.point_count, 4)
        self.assertAlmostEqual(report.mean_center[0], 5.0)
        self.assertAlmostEqual(report.mean_center[1], 5.0)
        self.assertGreater(report.standard_distance, 0)
        self.assertIsNone(report.weighted_mean_center)

    def test_with_weights(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        report = analyze_centers(pts, weights=[1, 1, 1, 1])
        self.assertIsNotNone(report.weighted_mean_center)
        self.assertIsNotNone(report.standard_distance_weighted)

    def test_with_bounds(self):
        pts = [(5, 5), (15, 15)]
        report = analyze_centers(pts, bounds=(0, 20, 0, 20))
        self.assertEqual(report.bounds, (0, 20, 0, 20))

    def test_summary(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        report = analyze_centers(pts)
        s = report.summary()
        self.assertIn("Mean center", s)
        self.assertIn("Standard distance", s)
        self.assertIn("Ellipse angle", s)


class TestCenterReportExport(unittest.TestCase):
    def setUp(self):
        self.pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        self.report = analyze_centers(self.pts, bounds=(0, 10, 0, 10))

    def test_to_dict(self):
        d = self.report.to_dict()
        self.assertIn("mean_center", d)
        self.assertIn("median_center", d)
        self.assertIn("central_feature", d)
        self.assertIn("standard_distance", d)
        self.assertIn("ellipse", d)
        self.assertEqual(d["point_count"], 5)

    def test_to_json(self):
        path = "test_output_centers.json"
        try:
            self.report.to_json(path)
            with open(path) as fh:
                d = json.load(fh)
            self.assertEqual(d["point_count"], 5)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_csv(self):
        path = "test_output_centers.csv"
        try:
            self.report.to_csv(path)
            with open(path) as fh:
                lines = fh.readlines()
            self.assertTrue(lines[0].startswith("metric"))
            self.assertGreater(len(lines), 5)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_svg(self):
        path = "test_output_centers.svg"
        try:
            self.report.to_svg(path)
            with open(path) as fh:
                content = fh.read()
            self.assertIn("<svg", content)
            self.assertIn("ellipse", content)
            self.assertIn("Mean", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_dict_with_weights(self):
        report = analyze_centers(self.pts, weights=[1, 2, 3, 4, 5])
        d = report.to_dict()
        self.assertIn("weighted_mean_center", d)
        self.assertIn("standard_distance_weighted", d)


class TestMedianCenterConvergence(unittest.TestCase):
    def test_large_dataset(self):
        import random
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(200)]
        cx, cy = _median_center(pts)
        # Should be roughly near center
        self.assertGreater(cx, 20)
        self.assertLess(cx, 80)
        self.assertGreater(cy, 20)
        self.assertLess(cy, 80)

    def test_point_on_data(self):
        """Median center with a point at the current estimate."""
        pts = [(0, 0), (5, 5), (10, 10)]
        cx, cy = _median_center(pts)
        # Should converge (handling the zero-distance case)
        self.assertTrue(math.isfinite(cx))
        self.assertTrue(math.isfinite(cy))


class TestEllipseProperties(unittest.TestCase):
    def test_major_ge_minor(self):
        import random
        random.seed(99)
        pts = [(random.gauss(50, 30), random.gauss(50, 10)) for _ in range(100)]
        center = _mean_center(pts)
        angle, smaj, smin, rot = _deviational_ellipse(pts, center)
        self.assertGreaterEqual(smaj, smin)

    def test_angle_range(self):
        pts = [(0, 0), (10, 5), (20, 10), (5, 15), (15, 20)]
        center = _mean_center(pts)
        angle, _, _, _ = _deviational_ellipse(pts, center)
        self.assertGreaterEqual(angle, 0)
        self.assertLess(angle, 360)


class TestSvgNoBounds(unittest.TestCase):
    def test_svg_without_bounds(self):
        """SVG rendering without explicit bounds."""
        pts = [(10, 20), (30, 40), (50, 60)]
        report = analyze_centers(pts)
        path = "test_output_centers_nobounds.svg"
        try:
            report.to_svg(path)
            with open(path) as fh:
                content = fh.read()
            self.assertIn("<svg", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
