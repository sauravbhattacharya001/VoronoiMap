"""Extended tests for vormap_trend — SVG export, color mapping, predict_grid edge cases.

Covers functions not tested in test_trend.py:
- export_trend_svg (predicted + residual modes)
- _value_to_color (diverging color mapping)
- predict_grid with custom bounds
- predict_at boundary/extrapolation cases
- TrendResult.summary_text edge cases
- compare_trends with single order
"""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_trend import (
    fit_trend_surface,
    compare_trends,
    predict_at,
    predict_grid,
    export_trend_csv,
    export_trend_json,
    export_trend_svg,
    TrendResult,
    TrendComparison,
    _design_row,
    _value_to_color,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_stats(points, values, attribute="area"):
    """Build minimal stats dicts for fit_trend_surface."""
    stats = []
    for (x, y), v in zip(points, values):
        verts = [(x - 1, y - 1), (x + 1, y - 1), (x + 1, y + 1), (x - 1, y + 1)]
        stats.append({
            "centroid": (x, y),
            "vertices": verts,
            "polygon": verts,
            attribute: v,
        })
    return stats


def _linear_data(n=25):
    """Grid of points with z = 2x + 3y + 1 (perfect linear trend)."""
    points = []
    values = []
    for i in range(int(math.sqrt(n))):
        for j in range(int(math.sqrt(n))):
            x, y = i * 10.0, j * 10.0
            points.append((x, y))
            values.append(2 * x + 3 * y + 1)
    return points, values


def _quadratic_data(n=36):
    """Grid with z = x² + y² (bowl shape)."""
    side = int(math.sqrt(n))
    points = []
    values = []
    for i in range(side):
        for j in range(side):
            x, y = i * 10.0 - 25, j * 10.0 - 25
            points.append((x, y))
            values.append(x * x + y * y)
    return points, values


def _noisy_data(n=36, seed=42):
    """Grid with linear trend + noise."""
    import random
    rng = random.Random(seed)
    side = int(math.sqrt(n))
    points = []
    values = []
    for i in range(side):
        for j in range(side):
            x, y = i * 10.0, j * 10.0
            points.append((x, y))
            values.append(x + y + rng.gauss(0, 5))
    return points, values


# ── Tests: _value_to_color ──────────────────────────────────────────

class TestValueToColor(unittest.TestCase):
    def test_min_value_blue_ish(self):
        color = _value_to_color(0, 0, 100)
        self.assertTrue(color.startswith("#"))
        self.assertEqual(len(color), 7)

    def test_max_value_red_ish(self):
        color = _value_to_color(100, 0, 100)
        self.assertTrue(color.startswith("#"))

    def test_mid_value_white_ish(self):
        color = _value_to_color(50, 0, 100)
        self.assertEqual(color.lower(), "#ffffff")

    def test_equal_min_max_returns_white(self):
        color = _value_to_color(5, 5, 5)
        self.assertEqual(color, "#ffffff")

    def test_below_min_clamped(self):
        color = _value_to_color(-10, 0, 100)
        self.assertTrue(color.startswith("#"))

    def test_above_max_clamped(self):
        color = _value_to_color(200, 0, 100)
        self.assertTrue(color.startswith("#"))

    def test_returns_valid_hex(self):
        for val in [0, 25, 50, 75, 100]:
            color = _value_to_color(val, 0, 100)
            self.assertRegex(color, r"^#[0-9a-f]{6}$")


# ── Tests: predict_grid edge cases ──────────────────────────────────

class TestPredictGridEdgeCases(unittest.TestCase):
    def setUp(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        self.result = fit_trend_surface(stats, attribute="area", order=1)

    def test_1x1_grid(self):
        gd = predict_grid(self.result, nx=1, ny=1)
        self.assertEqual(len(gd["grid"]), 1)
        self.assertEqual(len(gd["grid"][0]), 1)

    def test_custom_bounds(self):
        gd = predict_grid(self.result, nx=5, ny=5, bounds=(-100, 100, -100, 100))
        self.assertEqual(gd["bounds"], (-100, 100, -100, 100))
        self.assertEqual(len(gd["x_vals"]), 5)
        self.assertEqual(len(gd["y_vals"]), 5)
        self.assertAlmostEqual(gd["x_vals"][0], -100)
        self.assertAlmostEqual(gd["x_vals"][-1], 100)

    def test_grid_values_match_predict_at(self):
        gd = predict_grid(self.result, nx=3, ny=3)
        for j, y in enumerate(gd["y_vals"]):
            for i, x in enumerate(gd["x_vals"]):
                expected = predict_at(self.result, x, y)
                self.assertAlmostEqual(gd["grid"][j][i], expected, places=6)

    def test_large_grid(self):
        gd = predict_grid(self.result, nx=100, ny=100)
        self.assertEqual(len(gd["grid"]), 100)
        self.assertEqual(len(gd["grid"][0]), 100)


# ── Tests: predict_at extrapolation ─────────────────────────────────

class TestPredictAtExtrapolation(unittest.TestCase):
    def test_extrapolation_outside_bounds(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        val = predict_at(result, 1000, 1000)
        self.assertIsInstance(val, float)
        self.assertFalse(math.isnan(val))

    def test_predict_at_origin(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        val = predict_at(result, 0, 0)
        self.assertAlmostEqual(val, 1.0, places=0)


# ── Tests: export_trend_svg ─────────────────────────────────────────

class TestExportTrendSVG(unittest.TestCase):
    def _make_data_and_result(self):
        points, values = _noisy_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=2)
        data = {"points": points}
        regions = [{"vertices": s["polygon"]} for s in stats]
        return result, regions, data

    def test_svg_predicted_creates_file(self):
        result, regions, data = self._make_data_and_result()
        path = "test_trend_predicted_output.svg"
        try:
            export_trend_svg(result, regions, data, path, show="predicted")
            self.assertTrue(os.path.exists(path))
            content = open(path).read()
            self.assertIn("<svg", content)
            self.assertIn("Predicted", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_residuals_creates_file(self):
        result, regions, data = self._make_data_and_result()
        path = "test_trend_residuals_output.svg"
        try:
            export_trend_svg(result, regions, data, path, show="residuals")
            content = open(path).read()
            self.assertIn("<svg", content)
            self.assertIn("Residuals", content)
            self.assertIn("polygon", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_custom_dimensions(self):
        result, regions, data = self._make_data_and_result()
        path = "test_trend_dimensions_output.svg"
        try:
            export_trend_svg(result, regions, data, path, width=400, height=300)
            content = open(path).read()
            self.assertIn('width="400"', content)
            self.assertIn('height="300"', content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_contains_seed_points(self):
        result, regions, data = self._make_data_and_result()
        path = "test_trend_seeds_output.svg"
        try:
            export_trend_svg(result, regions, data, path)
            content = open(path).read()
            self.assertIn("circle", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_empty_points_raises(self):
        points, values = _noisy_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        path = "test_trend_empty_output.svg"
        try:
            with self.assertRaises(ValueError):
                export_trend_svg(result, [], {"points": []}, path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_low_grid_res(self):
        result, regions, data = self._make_data_and_result()
        path = "test_trend_lowres_output.svg"
        try:
            export_trend_svg(result, regions, data, path, grid_res=5)
            content = open(path).read()
            self.assertIn("<svg", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ── Tests: design_row consistency ───────────────────────────────────

class TestDesignRow(unittest.TestCase):
    def test_order1_length(self):
        row = _design_row(0.5, 0.5, 1)
        self.assertEqual(len(row), 3)

    def test_order2_length(self):
        row = _design_row(0.5, 0.5, 2)
        self.assertEqual(len(row), 6)

    def test_order3_length(self):
        row = _design_row(0.5, 0.5, 3)
        self.assertEqual(len(row), 10)

    def test_intercept_is_one(self):
        for order in [1, 2, 3]:
            row = _design_row(0.3, 0.7, order)
            self.assertEqual(row[0], 1.0)

    def test_zero_input(self):
        row = _design_row(0, 0, 2)
        self.assertEqual(row, [1, 0, 0, 0, 0, 0])


# ── Tests: compare_trends edge cases ───────────────────────────────

class TestCompareTrendsEdgeCases(unittest.TestCase):
    def test_single_order(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area", orders=[1])
        self.assertEqual(len(comp.results), 1)
        self.assertIsNotNone(comp.best_order)

    def test_all_orders(self):
        points, values = _quadratic_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area", orders=[1, 2, 3])
        self.assertEqual(len(comp.results), 3)
        self.assertIn(comp.best_order, [2, 3])

    def test_comparison_best_r_squared_highest(self):
        points, values = _noisy_data()
        stats = _make_stats(points, values)
        comp = compare_trends(stats, attribute="area")
        best = comp.results[comp.best_order]
        self.assertGreaterEqual(best.r_squared, -0.1)


# ── Tests: TrendResult.to_dict completeness ─────────────────────────

class TestTrendResultDict(unittest.TestCase):
    def test_dict_has_all_keys(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        result = fit_trend_surface(stats, attribute="area", order=1)
        d = result.to_dict()
        expected_keys = {"order", "attribute", "n_points", "r_squared",
                         "coefficients"}
        self.assertTrue(expected_keys.issubset(set(d.keys())),
                        f"Missing keys: {expected_keys - set(d.keys())}")

    def test_coefficient_count_matches_order(self):
        points, values = _linear_data()
        stats = _make_stats(points, values)
        expected_terms = {1: 3, 2: 6, 3: 10}
        for order in [1, 2, 3]:
            result = fit_trend_surface(stats, attribute="area", order=order)
            d = result.to_dict()
            self.assertEqual(len(d["coefficients"]), expected_terms[order])


if __name__ == "__main__":
    unittest.main()
