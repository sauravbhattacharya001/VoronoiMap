"""Tests for vormap_quality — Spatial Data Quality Assessment."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_quality import (
    assess_quality,
    QualityReport,
    SpacingStats,
    DuplicateInfo,
    BoundaryInfo,
    DensityInfo,
    IsolationInfo,
    _nn_distances,
    _auto_bounds,
    _check_spacing,
    _check_uniformity,
    _check_duplicates,
    _check_boundary,
    _check_density,
    _check_isolation,
    _score_color,
    _grade,
)


class TestHelpers(unittest.TestCase):
    def test_nn_distances_basic(self):
        pts = [(0, 0), (3, 4), (10, 0)]
        dists = _nn_distances(pts)
        self.assertEqual(len(dists), 3)
        self.assertAlmostEqual(dists[0], 5.0)  # to (3,4)
        self.assertAlmostEqual(dists[1], 5.0)  # to (0,0)

    def test_nn_distances_single(self):
        self.assertEqual(_nn_distances([(1, 1)]), [0.0])

    def test_nn_distances_empty(self):
        self.assertEqual(_nn_distances([]), [])

    def test_auto_bounds(self):
        pts = [(10, 20), (50, 80)]
        s, n, w, e = _auto_bounds(pts)
        self.assertLess(s, 20)
        self.assertGreater(n, 80)
        self.assertLess(w, 10)
        self.assertGreater(e, 50)

    def test_score_color(self):
        self.assertEqual(_score_color(90), "#28a745")
        self.assertEqual(_score_color(70), "#ffc107")
        self.assertEqual(_score_color(50), "#fd7e14")
        self.assertEqual(_score_color(20), "#dc3545")

    def test_grade(self):
        self.assertEqual(_grade(95), "A")
        self.assertEqual(_grade(85), "B")
        self.assertEqual(_grade(75), "C")
        self.assertEqual(_grade(65), "D")
        self.assertEqual(_grade(50), "F")


class TestCheckSpacing(unittest.TestCase):
    def test_uniform_spacing(self):
        # Evenly spaced line
        pts = [(i * 10, 0) for i in range(10)]
        dists = _nn_distances(pts)
        stats, score = _check_spacing(dists)
        self.assertAlmostEqual(stats.min_dist, 10.0)
        self.assertAlmostEqual(stats.max_dist, 10.0)
        self.assertGreater(score, 80)

    def test_empty(self):
        stats, score = _check_spacing([])
        self.assertEqual(stats.mean_dist, 0)


class TestCheckUniformity(unittest.TestCase):
    def test_regular_grid(self):
        pts = [(i * 10, j * 10) for i in range(5) for j in range(5)]
        dists = _nn_distances(pts)
        ui, score = _check_uniformity(dists, len(pts), 50 * 50)
        self.assertGreater(ui, 0.8)
        self.assertGreater(score, 50)

    def test_zero_area(self):
        ui, score = _check_uniformity([1, 2], 2, 0)
        self.assertEqual(ui, 0.0)


class TestCheckDuplicates(unittest.TestCase):
    def test_no_duplicates(self):
        pts = [(i * 100, i * 100) for i in range(5)]
        info, score = _check_duplicates(pts, tolerance=1.0)
        self.assertEqual(info.exact_count, 0)
        self.assertEqual(info.near_count, 0)
        self.assertEqual(score, 100)

    def test_exact_duplicates(self):
        pts = [(0, 0), (0, 0), (10, 10)]
        info, score = _check_duplicates(pts, tolerance=1.0)
        self.assertEqual(info.exact_count, 2)
        self.assertLess(score, 100)

    def test_near_duplicates(self):
        pts = [(0, 0), (0.5, 0.5), (100, 100)]
        info, score = _check_duplicates(pts, tolerance=1.0)
        self.assertGreater(info.near_count, 0)


class TestCheckBoundary(unittest.TestCase):
    def test_center_points(self):
        bounds = (0, 100, 0, 100)
        pts = [(50, 50), (40, 60)]
        info, score = _check_boundary(pts, bounds, margin_fraction=0.05)
        self.assertEqual(info.points_near_boundary, 0)
        self.assertEqual(score, 100)

    def test_edge_points(self):
        bounds = (0, 100, 0, 100)
        pts = [(1, 50), (50, 99), (50, 50)]
        info, score = _check_boundary(pts, bounds, margin_fraction=0.05)
        self.assertEqual(info.points_near_boundary, 2)
        self.assertLess(score, 100)


class TestCheckDensity(unittest.TestCase):
    def test_uniform_density(self):
        pts = [(i * 10, j * 10) for i in range(10) for j in range(10)]
        bounds = (0, 100, 0, 100)
        info, score = _check_density(pts, bounds, grid_size=5)
        self.assertGreater(score, 60)
        self.assertEqual(info.grid_size, 5)

    def test_clustered(self):
        pts = [(1, 1)] * 50 + [(99, 99)]
        bounds = (0, 100, 0, 100)
        info, score = _check_density(pts, bounds, grid_size=3)
        self.assertGreater(info.cv, 0.5)


class TestCheckIsolation(unittest.TestCase):
    def test_no_isolation(self):
        dists = [10, 10, 10, 10, 10]
        pts = [(0, 0)] * 5
        info, score = _check_isolation(pts, dists, multiplier=3.0)
        self.assertEqual(info.isolated_count, 0)

    def test_isolated_point(self):
        dists = [10, 10, 10, 10, 200]
        pts = [(0, 0)] * 5
        info, score = _check_isolation(pts, dists, multiplier=3.0)
        self.assertGreater(info.isolated_count, 0)
        self.assertLess(score, 100)


class TestAssessQuality(unittest.TestCase):
    def _grid_points(self, n=5):
        return [(i * 20, j * 20) for i in range(n) for j in range(n)]

    def test_basic_report(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        self.assertIsInstance(report, QualityReport)
        self.assertEqual(report.n_points, 25)
        self.assertGreater(report.score, 0)
        self.assertIn(report.grade, "ABCDF")

    def test_too_few_points(self):
        report = assess_quality([(0, 0)])
        self.assertEqual(report.score, 0)
        self.assertEqual(report.grade, "F")

    def test_auto_bounds(self):
        pts = self._grid_points()
        report = assess_quality(pts)
        self.assertIsNotNone(report.bounds)

    def test_high_quality_data(self):
        pts = self._grid_points(8)
        report = assess_quality(pts, bounds=(-10, 170, -10, 170))
        self.assertGreater(report.score, 50)

    def test_duplicates_lower_score(self):
        pts = [(50, 50)] * 10 + [(i * 20, 50) for i in range(5)]
        r = assess_quality(pts, bounds=(0, 100, 0, 100))
        self.assertLess(r.checks["duplicate_free"], 100)

    def test_summary(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        s = report.summary()
        self.assertIn("Quality Report", s)
        self.assertIn(report.grade, s)

    def test_to_dict(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        d = report.to_dict()
        self.assertIn("score", d)
        self.assertIn("spacing", d)
        self.assertIn("recommendations", d)

    def test_to_json(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            report.to_json(path, allow_absolute=True)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("score", data)
        finally:
            os.unlink(path)

    def test_to_csv(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            report.to_csv(path, allow_absolute=True)
            with open(path) as f:
                lines = f.readlines()
            self.assertGreater(len(lines), 5)
            self.assertTrue(lines[0].startswith("metric"))
        finally:
            os.unlink(path)

    def test_to_svg(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            report.to_svg(path, allow_absolute=True)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("Quality", content)
        finally:
            os.unlink(path)

    def test_recommendations_present(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        self.assertIsInstance(report.recommendations, list)
        self.assertGreater(len(report.recommendations), 0)

    def test_checks_dict(self):
        pts = self._grid_points()
        report = assess_quality(pts, bounds=(0, 100, 0, 100))
        expected_keys = {"spacing_uniformity", "spatial_distribution",
                         "duplicate_free", "boundary_clearance",
                         "density_evenness", "no_isolation"}
        self.assertEqual(set(report.checks.keys()), expected_keys)


class TestDataclasses(unittest.TestCase):
    def test_spacing_stats_to_dict(self):
        s = SpacingStats(min_dist=1, max_dist=10, mean_dist=5,
                         median_dist=4.5, std_dist=2.5, cv=0.5)
        d = s.to_dict()
        self.assertEqual(d["min_distance"], 1)

    def test_duplicate_info_to_dict(self):
        d = DuplicateInfo(exact_count=2, near_count=3, tolerance=1.0)
        self.assertEqual(d.to_dict()["exact_duplicates"], 2)

    def test_boundary_info_to_dict(self):
        b = BoundaryInfo(points_near_boundary=5, fraction_near_boundary=0.1)
        self.assertEqual(b.to_dict()["points_near_boundary"], 5)

    def test_density_info_to_dict(self):
        d = DensityInfo(grid_size=5, mean_density=10)
        self.assertEqual(d.to_dict()["grid_size"], 5)

    def test_isolation_info_to_dict(self):
        i = IsolationInfo(isolated_count=2, threshold_distance=50)
        self.assertEqual(i.to_dict()["isolated_count"], 2)


if __name__ == "__main__":
    unittest.main()
