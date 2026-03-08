"""Tests for vormap_trend — Trend Surface Analysis."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_trend import (
    fit_trend_surface,
    compare_trends,
    predict_at,
    predict_grid,
    export_trend_csv,
    export_trend_json,
    TrendResult,
    TrendComparison,
    _design_row,
    _term_labels,
    _transpose,
    _mat_mul,
    _invert,
    _ols_fit,
)


def _make_stats(points, values, attribute="area"):
    """Build minimal region stats from points and values."""
    stats = []
    for (x, y), v in zip(points, values):
        # Create a small square region around the point
        d = 5
        verts = [(x - d, y - d), (x + d, y - d), (x + d, y + d), (x - d, y + d)]
        stats.append({"vertices": verts, attribute: v})
    return stats


def _linear_data():
    """Points with a known linear trend: z = 10 + 2x + 3y."""
    points = [(0, 0), (100, 0), (0, 100), (100, 100),
              (50, 50), (25, 75), (75, 25), (50, 0),
              (0, 50), (100, 50)]
    values = [10 + 2 * x / 100 + 3 * y / 100 for x, y in points]
    return points, values


def _quadratic_data():
    """Points with a known quadratic trend: z = 1 + x² + y²."""
    points = [(0, 0), (100, 0), (0, 100), (100, 100),
              (50, 50), (25, 25), (75, 75), (50, 0),
              (0, 50), (100, 50), (25, 75), (75, 25)]
    values = [1 + (x / 100) ** 2 + (y / 100) ** 2 for x, y in points]
    return points, values


class TestMatrixHelpers(unittest.TestCase):
    """Test internal matrix operations."""

    def test_transpose(self):
        m = [[1, 2, 3], [4, 5, 6]]
        t = _transpose(m)
        self.assertEqual(t, [[1, 4], [2, 5], [3, 6]])

    def test_mat_mul_identity(self):
        I = [[1, 0], [0, 1]]
        A = [[3, 4], [5, 6]]
        self.assertEqual(_mat_mul(I, A), A)

    def test_mat_mul(self):
        A = [[1, 2], [3, 4]]
        B = [[5, 6], [7, 8]]
        result = _mat_mul(A, B)
        self.assertEqual(result, [[19, 22], [43, 50]])

    def test_invert_identity(self):
        I = [[1.0, 0.0], [0.0, 1.0]]
        inv = _invert(I)
        for i in range(2):
            for j in range(2):
                self.assertAlmostEqual(inv[i][j], I[i][j])

    def test_invert_2x2(self):
        A = [[4.0, 7.0], [2.0, 6.0]]
        inv = _invert(A)
        # A * inv should be identity
        prod = _mat_mul(A, inv)
        for i in range(2):
            for j in range(2):
                expected = 1.0 if i == j else 0.0
                self.assertAlmostEqual(prod[i][j], expected, places=10)

    def test_invert_singular_raises(self):
        A = [[1.0, 2.0], [2.0, 4.0]]
        with self.assertRaises(ValueError):
            _invert(A)


class TestDesignMatrix(unittest.TestCase):
    """Test polynomial design matrix construction."""

    def test_order_1(self):
        row = _design_row(2.0, 3.0, 1)
        self.assertEqual(row, [1.0, 2.0, 3.0])

    def test_order_2(self):
        row = _design_row(2.0, 3.0, 2)
        self.assertEqual(row, [1.0, 2.0, 3.0, 4.0, 6.0, 9.0])

    def test_order_3(self):
        row = _design_row(2.0, 3.0, 3)
        self.assertEqual(len(row), 10)
        self.assertEqual(row[0], 1.0)
        self.assertEqual(row[6], 8.0)  # x³
        self.assertEqual(row[9], 27.0)  # y³

    def test_order_invalid(self):
        with self.assertRaises(ValueError):
            _design_row(1.0, 1.0, 4)

    def test_term_labels_lengths(self):
        self.assertEqual(len(_term_labels(1)), 3)
        self.assertEqual(len(_term_labels(2)), 6)
        self.assertEqual(len(_term_labels(3)), 10)


class TestFitTrendSurface(unittest.TestCase):
    """Test the main fit_trend_surface function."""

    def test_linear_perfect_fit(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        self.assertIsInstance(result, TrendResult)
        self.assertEqual(result.order, 1)
        self.assertAlmostEqual(result.r_squared, 1.0, places=6)
        self.assertEqual(result.n_points, len(points))

    def test_quadratic_fit_better_than_linear(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        r1 = fit_trend_surface(stats, attribute="area", order=1)
        r2 = fit_trend_surface(stats, attribute="area", order=2)
        self.assertGreater(r2.r_squared, r1.r_squared)

    def test_residuals_sum_near_zero(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        self.assertAlmostEqual(sum(result.residuals), 0.0, places=8)

    def test_predicted_length_matches(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=2)
        self.assertEqual(len(result.predicted), len(values))
        self.assertEqual(len(result.residuals), len(values))

    def test_invalid_order_raises(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        with self.assertRaises(ValueError):
            fit_trend_surface(stats, attribute="area", order=4)

    def test_too_few_points_raises(self):
        stats = _make_stats([(0, 0), (1, 1)], [1.0, 2.0])
        with self.assertRaises(ValueError):
            fit_trend_surface(stats, attribute="area", order=1)

    def test_missing_attribute_skips(self):
        stats = [{"vertices": [(0, 0), (1, 0), (0, 1)], "area": 5.0}] * 3
        stats.append({"vertices": [(2, 2), (3, 2), (2, 3)]})  # no 'area'
        # Only 3 valid points — should raise
        with self.assertRaises(ValueError):
            fit_trend_surface(stats, attribute="area", order=1)

    def test_attribute_name(self):
        points = [(0, 0), (100, 0), (0, 100), (100, 100), (50, 50)]
        stats = [{"vertices": [(x-5, y-5), (x+5, y-5), (x+5, y+5), (x-5, y+5)],
                  "compactness": x + y} for x, y in points]
        result = fit_trend_surface(stats, attribute="compactness", order=1)
        self.assertEqual(result.attribute, "compactness")

    def test_cubic_more_terms(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=3)
        self.assertEqual(len(result.coefficients), 10)


class TestCompareTrends(unittest.TestCase):
    """Test polynomial order comparison."""

    def test_compare_returns_all_orders(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area")
        self.assertEqual(len(comp.results), 3)
        self.assertIn(comp.best_order, [1, 2, 3])

    def test_quadratic_data_prefers_order_2(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area")
        # Order 2 should be at least as good as order 1
        r1 = next(r for r in comp.results if r.order == 1)
        r2 = next(r for r in comp.results if r.order == 2)
        self.assertGreaterEqual(r2.r_squared, r1.r_squared)

    def test_custom_orders(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area", orders=[1, 2])
        self.assertEqual(len(comp.results), 2)

    def test_aic_values_present(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area")
        self.assertEqual(len(comp.aic_values), 3)
        for order, aic in comp.aic_values:
            self.assertIn(order, [1, 2, 3])
            self.assertIsInstance(aic, float)


class TestPredictAt(unittest.TestCase):
    """Test point prediction."""

    def test_predict_at_known_point(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        # Predict at a training point
        pred = predict_at(result, points[0][0], points[0][1])
        self.assertAlmostEqual(pred, values[0], places=4)

    def test_predict_at_center(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        pred = predict_at(result, 50, 50)
        expected = 10 + 2 * 0.5 + 3 * 0.5  # z = 10 + 2x + 3y (normalized)
        self.assertAlmostEqual(pred, expected, places=4)


class TestPredictGrid(unittest.TestCase):
    """Test grid prediction."""

    def test_grid_shape(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        g = predict_grid(result, nx=20, ny=15)
        self.assertEqual(len(g["grid"]), 15)
        self.assertEqual(len(g["grid"][0]), 20)
        self.assertEqual(len(g["x_vals"]), 20)
        self.assertEqual(len(g["y_vals"]), 15)

    def test_grid_bounds_custom(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        bounds = (0, 200, 0, 200)
        g = predict_grid(result, nx=5, ny=5, bounds=bounds)
        self.assertEqual(g["bounds"], bounds)


class TestSummaryAndSerialization(unittest.TestCase):
    """Test text summaries and dict serialization."""

    def test_summary_text(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        text = result.summary_text()
        self.assertIn("Order 1", text)
        self.assertIn("R²", text)
        self.assertIn("Linear", text)

    def test_to_dict(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        d = result.to_dict()
        self.assertEqual(d["order"], 1)
        self.assertEqual(d["n_points"], len(points))
        self.assertIn("coefficients", d)
        self.assertIn("points", d)
        self.assertEqual(len(d["points"]), len(points))

    def test_comparison_summary(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area")
        text = comp.summary_text()
        self.assertIn("Comparison", text)
        self.assertIn("Best order", text)

    def test_assessment_strong(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        self.assertIn("Strong", result.summary_text())


class TestExportCSV(unittest.TestCase):
    """Test CSV export."""

    def test_csv_export(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        path = "test_trend_output.csv"
        try:
            export_trend_csv(result, path)
            with open(path) as f:
                reader = list(csv.reader(f))
            self.assertEqual(reader[0], ["x", "y", "observed", "predicted", "residual"])
            self.assertEqual(len(reader), len(points) + 1)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportJSON(unittest.TestCase):
    """Test JSON export."""

    def test_json_export(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        path = "test_trend_output.json"
        try:
            export_trend_json(result, path)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["order"], 1)
            self.assertIn("r_squared", data)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and robustness."""

    def test_constant_values(self):
        """All same values — R² should be 0 (no variance to explain)."""
        points = [(0, 0), (100, 0), (0, 100), (100, 100), (50, 50)]
        stats = _make_stats(points, [5.0] * 5)
        result = fit_trend_surface(stats, attribute="area", order=1)
        self.assertAlmostEqual(result.r_squared, 0.0, places=6)

    def test_degenerate_vertices_skipped(self):
        """Regions with <3 vertices should be skipped."""
        stats = [
            {"vertices": [(0, 0), (10, 0), (5, 10)], "area": 1.0},
            {"vertices": [(20, 0), (30, 0), (25, 10)], "area": 2.0},
            {"vertices": [(0, 20), (10, 20), (5, 30)], "area": 3.0},
            {"vertices": [(20, 20), (30, 20), (25, 30)], "area": 4.0},
            {"vertices": [(10, 10)], "area": 99.0},  # degenerate — skipped
        ]
        result = fit_trend_surface(stats, attribute="area", order=1)
        self.assertEqual(result.n_points, 4)

    def test_noisy_data_moderate_r2(self):
        """Noisy data should give R² < 1."""
        import random
        random.seed(42)
        points = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(20)]
        values = [x + y + random.gauss(0, 50) for x, y in points]
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        self.assertLess(result.r_squared, 1.0)
        self.assertGreater(result.r_squared, 0.0)

    def test_r_squared_between_0_and_1(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        for order in [1, 2, 3]:
            result = fit_trend_surface(stats, attribute="area", order=order)
            self.assertGreaterEqual(result.r_squared, 0.0)
            self.assertLessEqual(result.r_squared, 1.0 + 1e-10)


import csv  # needed at top for TestExportCSV


if __name__ == "__main__":
    unittest.main()
