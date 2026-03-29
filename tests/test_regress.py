"""Tests for vormap_regress — Spatial Regression Analysis."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_regress import (
    fit_ols, fit_gwr, parse_formula,
    export_regress_json, export_regress_csv, export_regress_svg,
    _transpose, _mat_mul, _solve, _invert, _mean, _morans_i,
    _build_distance_weights, OLSResult, GWRResult,
)


def _make_stats(n=20, seed=42):
    """Generate synthetic region stats for testing."""
    import random
    rng = random.Random(seed)
    stats = []
    for i in range(n):
        x = rng.uniform(0, 1000)
        y = rng.uniform(0, 1000)
        area = 100 + 50 * (x / 1000) + 30 * (y / 1000) + rng.gauss(0, 10)
        compactness = 0.3 + 0.5 * rng.random()
        perimeter = math.sqrt(area) * (4 + rng.gauss(0, 0.5))
        verts = rng.randint(4, 12)
        stats.append({
            "region_index": i + 1,
            "seed_x": x,
            "seed_y": y,
            "area": round(area, 4),
            "perimeter": round(perimeter, 4),
            "centroid_x": x + rng.gauss(0, 5),
            "centroid_y": y + rng.gauss(0, 5),
            "vertex_count": verts,
            "compactness": round(compactness, 4),
            "avg_edge_length": round(perimeter / verts, 4),
        })
    return stats


class TestMatrixHelpers(unittest.TestCase):
    def test_transpose(self):
        m = [[1, 2], [3, 4], [5, 6]]
        t = _transpose(m)
        self.assertEqual(t, [[1, 3, 5], [2, 4, 6]])

    def test_mat_mul(self):
        a = [[1, 2], [3, 4]]
        b = [[5, 6], [7, 8]]
        r = _mat_mul(a, b)
        self.assertEqual(r, [[19, 22], [43, 50]])

    def test_solve_identity(self):
        A = [[1, 0], [0, 1]]
        b = [3, 7]
        x = _solve(A, b)
        self.assertAlmostEqual(x[0], 3.0)
        self.assertAlmostEqual(x[1], 7.0)

    def test_solve_2x2(self):
        A = [[2, 1], [1, 3]]
        b = [5, 10]
        x = _solve(A, b)
        self.assertAlmostEqual(x[0], 1.0, places=5)
        self.assertAlmostEqual(x[1], 3.0, places=5)

    def test_invert(self):
        A = [[2, 1], [1, 3]]
        inv = _invert(A)
        # A * inv should be identity
        prod = _mat_mul(A, inv)
        self.assertAlmostEqual(prod[0][0], 1.0, places=10)
        self.assertAlmostEqual(prod[0][1], 0.0, places=10)
        self.assertAlmostEqual(prod[1][0], 0.0, places=10)
        self.assertAlmostEqual(prod[1][1], 1.0, places=10)


class TestParseFormula(unittest.TestCase):
    def test_simple(self):
        y, x = parse_formula("area~compactness+vertex_count")
        self.assertEqual(y, "area")
        self.assertEqual(x, ["compactness", "vertex_count"])

    def test_single_predictor(self):
        y, x = parse_formula("perimeter~area")
        self.assertEqual(y, "perimeter")
        self.assertEqual(x, ["area"])

    def test_invalid_no_tilde(self):
        with self.assertRaises(ValueError):
            parse_formula("area+compactness")

    def test_invalid_no_predictors(self):
        with self.assertRaises(ValueError):
            parse_formula("area~")


class TestOLS(unittest.TestCase):
    def setUp(self):
        self.stats = _make_stats(25)

    def test_fit_basic(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        self.assertIsInstance(result, OLSResult)
        self.assertEqual(result.n, 25)
        self.assertEqual(result.k, 2)
        self.assertEqual(len(result.coefficients), 2)
        self.assertEqual(len(result.residuals), 25)

    def test_fit_multiple(self):
        result = fit_ols(self.stats, y="area",
                         x=["compactness", "vertex_count"])
        self.assertEqual(result.k, 3)
        self.assertEqual(len(result.vif), 2)

    def test_r_squared_bounds(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        self.assertGreaterEqual(result.r_squared, 0.0)
        self.assertLessEqual(result.r_squared, 1.0)

    def test_residuals_sum_near_zero(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        self.assertAlmostEqual(sum(result.residuals), 0.0, places=5)

    def test_cooks_d_nonnegative(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        for cd in result.cooks_d:
            self.assertGreaterEqual(cd, 0.0)

    def test_moran_i_computed(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        self.assertIsInstance(result.moran_i, float)
        self.assertIsInstance(result.moran_p, float)

    def test_summary_text(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        text = result.summary_text()
        self.assertIn("OLS Spatial Regression", text)
        self.assertIn("R²", text)
        self.assertIn("Moran's I", text)

    def test_to_dict(self):
        result = fit_ols(self.stats, y="area", x=["compactness"])
        d = result.to_dict()
        self.assertEqual(d["model"], "OLS")
        self.assertIn("r_squared", d)
        self.assertIn("spatial_diagnostics", d)

    def test_invalid_variable(self):
        with self.assertRaises(ValueError):
            fit_ols(self.stats, y="nonexistent", x=["compactness"])

    def test_y_in_x_error(self):
        with self.assertRaises(ValueError):
            fit_ols(self.stats, y="area", x=["area"])

    def test_too_few_obs(self):
        with self.assertRaises(ValueError):
            fit_ols(self.stats[:2], y="area",
                    x=["compactness", "vertex_count", "perimeter"])


class TestGWR(unittest.TestCase):
    def setUp(self):
        self.stats = _make_stats(20)

    def test_fit_basic(self):
        result = fit_gwr(self.stats, y="area", x=["compactness"],
                         bandwidth=300.0)
        self.assertIsInstance(result, GWRResult)
        self.assertEqual(result.n, 20)
        self.assertEqual(len(result.local_coefficients), 20)

    def test_auto_bandwidth(self):
        result = fit_gwr(self.stats, y="area", x=["compactness"])
        self.assertGreater(result.bandwidth, 0)

    def test_bisquare_kernel(self):
        result = fit_gwr(self.stats, y="area", x=["compactness"],
                         bandwidth=500.0, kernel="bisquare")
        self.assertEqual(result.kernel, "bisquare")
        self.assertEqual(len(result.residuals), 20)

    def test_local_r2_bounds(self):
        result = fit_gwr(self.stats, y="area", x=["compactness"],
                         bandwidth=300.0)
        for lr2 in result.local_r_squared:
            self.assertGreaterEqual(lr2, 0.0)
            self.assertLessEqual(lr2, 1.0)

    def test_summary_text(self):
        result = fit_gwr(self.stats, y="area", x=["compactness"],
                         bandwidth=300.0)
        text = result.summary_text()
        self.assertIn("Geographically Weighted", text)
        self.assertIn("Local R²", text)

    def test_to_dict(self):
        result = fit_gwr(self.stats, y="area", x=["compactness"],
                         bandwidth=300.0)
        d = result.to_dict()
        self.assertEqual(d["model"], "GWR")
        self.assertIn("per_observation", d)
        self.assertEqual(len(d["per_observation"]), 20)

    def test_invalid_kernel(self):
        with self.assertRaises(ValueError):
            fit_gwr(self.stats, y="area", x=["compactness"],
                    kernel="invalid")


class TestExport(unittest.TestCase):
    def setUp(self):
        self.stats = _make_stats(15)
        self.ols = fit_ols(self.stats, y="area", x=["compactness"])

    def test_export_json(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name
        try:
            export_regress_json(self.ols, path)
            with open(path) as f:
                d = json.load(f)
            self.assertEqual(d["model"], "OLS")
        finally:
            os.unlink(path)

    def test_export_csv(self):
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            path = f.name
        try:
            export_regress_csv(self.ols, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("region", content)
            self.assertIn("residual", content)
        finally:
            os.unlink(path)

    def test_export_svg(self):
        # Need regions — create minimal mock
        regions = {}
        for s in self.stats:
            seed = (s["seed_x"], s["seed_y"])
            cx, cy = seed
            # Create a simple square region
            regions[seed] = [
                (cx - 20, cy - 20), (cx + 20, cy - 20),
                (cx + 20, cy + 20), (cx - 20, cy + 20),
            ]
        data = [(s["seed_x"], s["seed_y"]) for s in self.stats]
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            export_regress_svg(self.ols, regions, data, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("polygon", content)
        finally:
            os.unlink(path)

    def test_export_gwr_csv(self):
        gwr = fit_gwr(self.stats, y="area", x=["compactness"],
                      bandwidth=300.0)
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            path = f.name
        try:
            export_regress_csv(gwr, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("local_r2", content)
            self.assertIn("coef_intercept", content)
        finally:
            os.unlink(path)


class TestSpatialWeights(unittest.TestCase):
    def test_weights_symmetric(self):
        coords = [(0, 0), (10, 0), (0, 10), (100, 100)]
        W, dists = _build_distance_weights(coords, threshold=15)
        # Check row-standardised
        for i in range(len(coords)):
            row_sum = sum(W[i])
            if row_sum > 0:
                self.assertAlmostEqual(row_sum, 1.0, places=10)

    def test_morans_i_random(self):
        import random
        rng = random.Random(99)
        coords = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(20)]
        residuals = [rng.gauss(0, 1) for _ in range(20)]
        W, _ = _build_distance_weights(coords)
        I, expected, z, p = _morans_i(residuals, W)
        # Random data should have I near expected
        self.assertAlmostEqual(I, expected, delta=1.0)


if __name__ == '__main__':
    unittest.main()
