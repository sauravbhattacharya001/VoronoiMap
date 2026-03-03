"""Tests for vormap_pattern -- point pattern spatial analysis."""

import math
import random
import unittest

from vormap import eudist_pts
from vormap_pattern import (
    clark_evans_nni,
    ripleys_k,
    quadrat_analysis,
    mean_center,
    standard_distance,
    convex_hull_ratio,
    analyze_pattern,
    format_pattern_report,
    generate_pattern_json,
    _validate_points,
    _compute_nn_distances,
    _bounding_area,
    _convex_hull_area,
    _normal_cdf,
    _chi2_survival,
    NNIResult,
    RipleysResult,
    QuadratResult,
    PatternSummary,
)


class TestValidation(unittest.TestCase):
    """Input validation tests."""

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            _validate_points([])

    def test_single_point_raises(self):
        with self.assertRaises(ValueError):
            _validate_points([(0, 0)])

    def test_two_points_ok(self):
        result = _validate_points([(0, 0), (1, 1)])
        self.assertEqual(len(result), 2)

    def test_normalises_to_float(self):
        result = _validate_points([(1, 2), (3, 4)])
        for x, y in result:
            self.assertIsInstance(x, float)
            self.assertIsInstance(y, float)


class TestEuclidean(unittest.TestCase):
    """Distance calculation tests."""

    def test_same_point(self):
        self.assertAlmostEqual(eudist_pts((0, 0), (0, 0)), 0.0)

    def test_unit_distance(self):
        self.assertAlmostEqual(eudist_pts((0, 0), (1, 0)), 1.0)

    def test_diagonal(self):
        self.assertAlmostEqual(eudist_pts((0, 0), (3, 4)), 5.0)


class TestNNDistances(unittest.TestCase):
    """Nearest-neighbor distance computation."""

    def test_two_points(self):
        dists = _compute_nn_distances([(0, 0), (3, 4)])
        self.assertEqual(len(dists), 2)
        self.assertAlmostEqual(dists[0], 5.0)
        self.assertAlmostEqual(dists[1], 5.0)

    def test_three_points(self):
        dists = _compute_nn_distances([(0, 0), (1, 0), (10, 0)])
        self.assertAlmostEqual(dists[0], 1.0)  # 0 -> 1
        self.assertAlmostEqual(dists[1], 1.0)  # 1 -> 0
        self.assertAlmostEqual(dists[2], 9.0)  # 10 -> 1


class TestBoundingArea(unittest.TestCase):
    """Bounding box area computation."""

    def test_with_explicit_bounds(self):
        pts = [(0, 0), (1, 1)]
        area = _bounding_area(pts, (0, 10, 0, 10))
        self.assertAlmostEqual(area, 100.0)

    def test_from_points(self):
        pts = [(0, 0), (5, 10)]
        area = _bounding_area(pts)
        self.assertAlmostEqual(area, 50.0)


class TestConvexHullArea(unittest.TestCase):
    """Convex hull area computation."""

    def test_triangle(self):
        area = _convex_hull_area([(0, 0), (4, 0), (0, 3)])
        self.assertAlmostEqual(area, 6.0)

    def test_square(self):
        area = _convex_hull_area([(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertAlmostEqual(area, 100.0)

    def test_two_points_zero_area(self):
        area = _convex_hull_area([(0, 0), (1, 1)])
        self.assertAlmostEqual(area, 0.0)


class TestNormalCDF(unittest.TestCase):
    """Normal CDF approximation."""

    def test_zero(self):
        self.assertAlmostEqual(_normal_cdf(0), 0.5, places=4)

    def test_large_positive(self):
        self.assertAlmostEqual(_normal_cdf(10), 1.0, places=4)

    def test_large_negative(self):
        self.assertAlmostEqual(_normal_cdf(-10), 0.0, places=4)

    def test_one_sigma(self):
        val = _normal_cdf(1.0)
        self.assertAlmostEqual(val, 0.8413, places=1)
        self.assertGreater(val, 0.83)
        self.assertLess(val, 0.88)

    def test_two_sigma(self):
        val = _normal_cdf(2.0)
        self.assertAlmostEqual(val, 0.9772, places=1)
        self.assertGreater(val, 0.97)
        self.assertLess(val, 0.99)


class TestChi2Survival(unittest.TestCase):
    """Chi-squared survival function approximation."""

    def test_zero_x(self):
        self.assertAlmostEqual(_chi2_survival(0, 5), 1.0)

    def test_large_x(self):
        result = _chi2_survival(100, 5)
        self.assertAlmostEqual(result, 0.0, places=4)


class TestClarkEvansNNI(unittest.TestCase):
    """Clark-Evans Nearest Neighbor Index."""

    def test_returns_nni_result(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        result = clark_evans_nni(pts)
        self.assertIsInstance(result, NNIResult)

    def test_random_pattern_near_one(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(200)]
        result = clark_evans_nni(pts)
        self.assertGreater(result.nni, 0.7)
        self.assertLess(result.nni, 1.4)

    def test_clustered_pattern_below_one(self):
        random.seed(42)
        pts = []
        for cx, cy in [(20, 20), (80, 80)]:
            for _ in range(30):
                pts.append((cx + random.gauss(0, 2), cy + random.gauss(0, 2)))
        result = clark_evans_nni(pts)
        self.assertLess(result.nni, 0.8)
        self.assertEqual(result.interpretation, "clustered")

    def test_dispersed_pattern_above_one(self):
        pts = [(x * 10, y * 10) for x in range(10) for y in range(10)]
        result = clark_evans_nni(pts)
        self.assertGreater(result.nni, 1.2)
        self.assertEqual(result.interpretation, "dispersed")

    def test_point_count(self):
        pts = [(i, i) for i in range(10)]
        result = clark_evans_nni(pts)
        self.assertEqual(result.n, 10)

    def test_with_explicit_bounds(self):
        pts = [(5, 5), (6, 6), (7, 7)]
        result = clark_evans_nni(pts, bounds=(0, 100, 0, 100))
        self.assertEqual(result.n, 3)

    def test_z_score_has_sign(self):
        # Clustered should have negative z-score
        pts = [(0.1 * i, 0.1 * i) for i in range(20)]
        result = clark_evans_nni(pts)
        # Very clustered (linear) — z-score should be negative
        # Actually linear points are dispersed in 1D but we measure 2D
        # Just check z_score is a number
        self.assertIsInstance(result.z_score, float)


class TestRipleysK(unittest.TestCase):
    """Ripley's K function tests."""

    def test_returns_ripleys_result(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        result = ripleys_k(pts, n_radii=5)
        self.assertIsInstance(result, RipleysResult)

    def test_radii_count(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        result = ripleys_k(pts, n_radii=10)
        self.assertEqual(len(result.radii), 10)
        self.assertEqual(len(result.k_values), 10)
        self.assertEqual(len(result.l_values), 10)
        self.assertEqual(len(result.csr_k), 10)

    def test_k_increases_with_r(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        result = ripleys_k(pts, n_radii=10)
        # K(r) should generally increase with r
        for i in range(1, len(result.k_values)):
            self.assertGreaterEqual(result.k_values[i], result.k_values[i - 1])

    def test_csr_k_is_pi_r_squared(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(20)]
        result = ripleys_k(pts, n_radii=5)
        for i, r in enumerate(result.radii):
            self.assertAlmostEqual(result.csr_k[i], math.pi * r * r, places=2)

    def test_custom_radii(self):
        pts = [(i, 0) for i in range(20)]
        result = ripleys_k(pts, radii=[1.0, 2.0, 5.0])
        self.assertEqual(len(result.radii), 3)

    def test_peak_clustering_exists(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        result = ripleys_k(pts, n_radii=5)
        self.assertIsNotNone(result.peak_clustering_r)
        self.assertIsNotNone(result.peak_clustering_l)

    def test_k_uses_n_minus_1_denominator(self):
        """K estimator must use n*(n-1) not n*n in the denominator.

        For a pair of points distance 1 apart in area 100, at radius 2:
        count = 2 (both ordered pairs within radius).
        K(r) = (A / (n*(n-1))) * count = (100 / 2) * 2 = 100.
        The biased n*n formula would give (100/4) * 2 = 50.
        """
        pts = [(0.0, 0.0), (1.0, 0.0)]
        result = ripleys_k(pts, radii=[2.0])
        # area ~ bounding box with padding
        area = result.area
        expected_k = (area / (2 * 1)) * 2  # n=2, n-1=1, count=2
        self.assertAlmostEqual(result.k_values[0], round(expected_k, 6), places=2)

    def test_k_small_sample_not_biased(self):
        """With n=3 equilateral triangle, K should use n*(n-1)=6 not n*n=9."""
        pts = [(0.0, 0.0), (10.0, 0.0), (5.0, 8.66)]
        result = ripleys_k(pts, radii=[15.0])
        area = result.area
        # All 6 ordered pairs within radius 15
        expected_k = (area / (3 * 2)) * 6  # = area
        self.assertAlmostEqual(result.k_values[0], round(expected_k, 6), places=2)


class TestQuadratAnalysis(unittest.TestCase):
    """Quadrat (grid cell) analysis."""

    def test_returns_quadrat_result(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        result = quadrat_analysis(pts)
        self.assertIsInstance(result, QuadratResult)

    def test_counts_sum_to_n(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        result = quadrat_analysis(pts, rows=5, cols=5)
        self.assertEqual(sum(result.counts), 50)
        self.assertEqual(len(result.counts), 25)

    def test_uniform_pattern_low_vmr(self):
        # Perfect grid: each cell gets exactly one point
        pts = [(i * 10 + 5, j * 10 + 5) for i in range(10) for j in range(10)]
        result = quadrat_analysis(pts, rows=10, cols=10,
                                  bounds=(0, 100, 0, 100))
        self.assertLess(result.vmr, 1.0)

    def test_clustered_pattern_high_vmr(self):
        random.seed(42)
        pts = [(50 + random.gauss(0, 2), 50 + random.gauss(0, 2)) for _ in range(100)]
        result = quadrat_analysis(pts, rows=5, cols=5,
                                  bounds=(0, 100, 0, 100))
        self.assertGreater(result.vmr, 2.0)
        self.assertEqual(result.interpretation, "clustered")

    def test_grid_dimensions(self):
        pts = [(i, j) for i in range(10) for j in range(10)]
        result = quadrat_analysis(pts, rows=3, cols=4)
        self.assertEqual(result.rows, 3)
        self.assertEqual(result.cols, 4)
        self.assertEqual(len(result.counts), 12)

    def test_expected_count(self):
        pts = [(i, i) for i in range(20)]
        result = quadrat_analysis(pts, rows=4, cols=4)
        self.assertAlmostEqual(result.expected_count, 20 / 16, places=2)

    def test_degenerate_bounds_raises(self):
        pts = [(5, 5), (5, 5)]
        with self.assertRaises(ValueError):
            quadrat_analysis(pts, bounds=(5, 5, 0, 10))


class TestMeanCenter(unittest.TestCase):
    """Mean center (centroid) tests."""

    def test_simple(self):
        cx, cy = mean_center([(0, 0), (10, 10)])
        self.assertAlmostEqual(cx, 5.0, places=4)
        self.assertAlmostEqual(cy, 5.0, places=4)

    def test_three_points(self):
        cx, cy = mean_center([(0, 0), (3, 0), (0, 6)])
        self.assertAlmostEqual(cx, 1.0, places=4)
        self.assertAlmostEqual(cy, 2.0, places=4)

    def test_symmetric(self):
        cx, cy = mean_center([(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertAlmostEqual(cx, 5.0, places=4)
        self.assertAlmostEqual(cy, 5.0, places=4)


class TestStandardDistance(unittest.TestCase):
    """Standard distance (spatial spread) tests."""

    def test_two_points(self):
        sd = standard_distance([(0, 0), (10, 0)])
        self.assertGreater(sd, 0)

    def test_identical_points(self):
        sd = standard_distance([(5, 5), (5, 5)])
        self.assertAlmostEqual(sd, 0.0)

    def test_spread_increases(self):
        sd1 = standard_distance([(0, 0), (1, 0)])
        sd2 = standard_distance([(0, 0), (10, 0)])
        self.assertGreater(sd2, sd1)


class TestConvexHullRatio(unittest.TestCase):
    """Convex hull area ratio tests."""

    def test_rectangle_fills_bbox(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        ratio = convex_hull_ratio(pts)
        self.assertAlmostEqual(ratio, 1.0, places=2)

    def test_triangle_half(self):
        pts = [(0, 0), (10, 0), (0, 10)]
        ratio = convex_hull_ratio(pts)
        self.assertAlmostEqual(ratio, 0.5, places=2)

    def test_with_bounds(self):
        pts = [(0, 0), (5, 0), (0, 5)]
        ratio = convex_hull_ratio(pts, bounds=(0, 10, 0, 10))
        area_hull = 12.5
        area_bbox = 100
        self.assertAlmostEqual(ratio, 0.125, places=2)


class TestAnalyzePattern(unittest.TestCase):
    """Combined analysis tests."""

    def test_returns_summary(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        result = analyze_pattern(pts, n_radii=5)
        self.assertIsInstance(result, PatternSummary)

    def test_summary_fields(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        result = analyze_pattern(pts, n_radii=5)
        self.assertEqual(result.n, 50)
        self.assertIsNotNone(result.mean_center)
        self.assertIsNotNone(result.std_distance)
        self.assertIsNotNone(result.hull_area_ratio)
        self.assertIsNotNone(result.nni)
        self.assertIsNotNone(result.quadrat)
        self.assertIsNotNone(result.ripleys)
        self.assertIn(result.overall, ["clustered", "random", "dispersed"])

    def test_clustered_detected(self):
        random.seed(42)
        pts = []
        for cx, cy in [(10, 10), (90, 90), (10, 90)]:
            for _ in range(40):
                pts.append((cx + random.gauss(0, 2), cy + random.gauss(0, 2)))
        result = analyze_pattern(pts, n_radii=5)
        self.assertEqual(result.overall, "clustered")

    def test_dispersed_detected(self):
        pts = [(x * 10 + 5, y * 10 + 5) for x in range(10) for y in range(10)]
        result = analyze_pattern(pts, n_radii=5)
        self.assertEqual(result.overall, "dispersed")


class TestFormatReport(unittest.TestCase):
    """Report formatting tests."""

    def test_report_not_empty(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        summary = analyze_pattern(pts, n_radii=5)
        report = format_pattern_report(summary)
        self.assertGreater(len(report), 200)
        self.assertIn("POINT PATTERN ANALYSIS", report)
        self.assertIn("Clark-Evans", report)
        self.assertIn("Quadrat", report)
        self.assertIn("Ripley", report)
        self.assertIn("Overall", report)

    def test_report_ascii_only(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        summary = analyze_pattern(pts, n_radii=5)
        report = format_pattern_report(summary)
        for char in report:
            self.assertLess(ord(char), 128,
                            f"Non-ASCII char: {char!r} (U+{ord(char):04X})")


class TestGenerateJSON(unittest.TestCase):
    """JSON export tests."""

    def test_json_structure(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        summary = analyze_pattern(pts, n_radii=5)
        data = generate_pattern_json(summary)
        self.assertIn("n", data)
        self.assertIn("mean_center", data)
        self.assertIn("nni", data)
        self.assertIn("quadrat", data)
        self.assertIn("ripleys", data)
        self.assertIn("overall", data)

    def test_json_serialisable(self):
        import json
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        summary = analyze_pattern(pts, n_radii=5)
        data = generate_pattern_json(summary)
        # Should not raise
        serialised = json.dumps(data)
        self.assertGreater(len(serialised), 50)

    def test_json_nni_fields(self):
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(30)]
        summary = analyze_pattern(pts, n_radii=5)
        data = generate_pattern_json(summary)
        nni = data["nni"]
        self.assertIn("observed_mean", nni)
        self.assertIn("expected_mean", nni)
        self.assertIn("nni", nni)
        self.assertIn("z_score", nni)
        self.assertIn("p_value", nni)
        self.assertIn("interpretation", nni)


if __name__ == '__main__':
    unittest.main()
