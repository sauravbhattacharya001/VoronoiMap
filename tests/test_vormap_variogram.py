"""Tests for vormap_variogram — variogram analysis module."""

import math
import os
import tempfile
import unittest

from vormap_variogram import (
    ExperimentalVariogram,
    LagBin,
    VariogramModel,
    auto_fit,
    evaluate_model,
    experimental_variogram,
    export_variogram_csv,
    export_variogram_json,
    export_variogram_svg,
    fit_variogram,
    variogram_cloud,
    variogram_summary,
    variogram_surface,
    _dist,
    _angle_deg,
    _angle_in_tolerance,
    _model_spherical,
    _model_exponential,
    _model_gaussian,
    _model_linear,
)


# ── Helper: generate spatially correlated data ───────────────────────

def _make_grid_data(nx=10, ny=10, spacing=10.0):
    """Create a grid of points with a simple spatial gradient (z = x + y + noise)."""
    import random
    rng = random.Random(42)
    points = []
    values = []
    for i in range(nx):
        for j in range(ny):
            x = i * spacing
            y = j * spacing
            z = x + y + rng.gauss(0, 5)  # spatial trend + noise
            points.append((x, y))
            values.append(z)
    return points, values


def _make_clustered_data():
    """Two clusters with distinct values — should show spatial structure."""
    import random
    rng = random.Random(99)
    points = []
    values = []
    # Cluster A: low values around (50, 50)
    for _ in range(30):
        points.append((50 + rng.gauss(0, 10), 50 + rng.gauss(0, 10)))
        values.append(10 + rng.gauss(0, 2))
    # Cluster B: high values around (200, 200)
    for _ in range(30):
        points.append((200 + rng.gauss(0, 10), 200 + rng.gauss(0, 10)))
        values.append(50 + rng.gauss(0, 2))
    return points, values


class TestHelpers(unittest.TestCase):
    def test_dist(self):
        self.assertAlmostEqual(_dist((0, 0), (3, 4)), 5.0)
        self.assertAlmostEqual(_dist((1, 1), (1, 1)), 0.0)

    def test_angle_deg(self):
        self.assertAlmostEqual(_angle_deg((0, 0), (1, 0)), 0.0)   # East
        self.assertAlmostEqual(_angle_deg((0, 0), (0, 1)), 90.0)  # North
        self.assertAlmostEqual(_angle_deg((0, 0), (-1, 0)), 180.0)

    def test_angle_in_tolerance(self):
        self.assertTrue(_angle_in_tolerance(0, 0, 22.5))
        self.assertTrue(_angle_in_tolerance(10, 0, 22.5))
        self.assertFalse(_angle_in_tolerance(50, 0, 22.5))
        # Wrapping: 355° should be within tolerance of 0°
        self.assertTrue(_angle_in_tolerance(355, 0, 10))


class TestModelFunctions(unittest.TestCase):
    def test_spherical_zero(self):
        self.assertEqual(_model_spherical(0, 1, 5, 100), 0.0)

    def test_spherical_at_range(self):
        val = _model_spherical(100, 1, 5, 100)
        self.assertAlmostEqual(val, 6.0)  # nugget + partial_sill

    def test_spherical_beyond_range(self):
        val = _model_spherical(200, 1, 5, 100)
        self.assertAlmostEqual(val, 6.0)

    def test_spherical_mid_range(self):
        val = _model_spherical(50, 1, 5, 100)
        self.assertGreater(val, 1.0)
        self.assertLess(val, 6.0)

    def test_exponential_zero(self):
        self.assertEqual(_model_exponential(0, 1, 5, 100), 0.0)

    def test_exponential_approaches_sill(self):
        val = _model_exponential(500, 1, 5, 100)
        self.assertAlmostEqual(val, 6.0, places=2)

    def test_gaussian_zero(self):
        self.assertEqual(_model_gaussian(0, 1, 5, 100), 0.0)

    def test_gaussian_approaches_sill(self):
        val = _model_gaussian(500, 1, 5, 100)
        self.assertAlmostEqual(val, 6.0, places=2)

    def test_linear(self):
        val = _model_linear(50, 1, 5, 100)
        self.assertAlmostEqual(val, 3.5)  # 1 + (5/100)*50

    def test_model_zero_range(self):
        # Should not crash with zero range
        self.assertEqual(_model_spherical(10, 1, 5, 0), 6.0)
        self.assertEqual(_model_exponential(10, 1, 5, 0), 6.0)


class TestEvaluateModel(unittest.TestCase):
    def test_evaluate_model(self):
        model = VariogramModel(
            model_type="spherical", nugget=1.0, sill=6.0,
            range_param=100, partial_sill=5.0, rmse=0, r_squared=0,
        )
        self.assertEqual(evaluate_model(model, 0), 0.0)
        self.assertAlmostEqual(evaluate_model(model, 100), 6.0)

    def test_evaluate_unknown_type_defaults_spherical(self):
        model = VariogramModel(
            model_type="unknown", nugget=0, sill=1,
            range_param=10, partial_sill=1, rmse=0, r_squared=0,
        )
        # Should use spherical as fallback
        val = evaluate_model(model, 10)
        self.assertGreater(val, 0)


class TestExperimentalVariogram(unittest.TestCase):
    def test_basic_grid(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values, n_lags=10)
        self.assertGreater(len(ev.bins), 0)
        self.assertEqual(ev.n_points, 100)
        self.assertGreater(ev.n_pairs, 0)
        self.assertGreater(ev.max_distance, 0)

    def test_semivariance_increases_with_lag(self):
        """For spatially correlated data, semivariance should generally increase."""
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values, n_lags=10)
        # First bin should have lower semivariance than last
        self.assertLess(ev.bins[0].semivariance, ev.bins[-1].semivariance)

    def test_custom_max_lag(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values, n_lags=5, max_lag=50)
        self.assertLessEqual(ev.bins[-1].lag_center, 55)

    def test_too_few_points(self):
        with self.assertRaises(ValueError):
            experimental_variogram([(0, 0), (1, 1)], [1, 2])

    def test_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            experimental_variogram([(0, 0), (1, 1), (2, 2)], [1, 2])

    def test_directional(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values, direction=0, tolerance=22.5)
        self.assertEqual(ev.direction, 0)
        self.assertGreater(len(ev.bins), 0)

    def test_all_same_location(self):
        with self.assertRaises(ValueError):
            experimental_variogram([(5, 5)] * 10, list(range(10)))

    def test_pair_count_consistency(self):
        points, values = _make_grid_data(5, 5)
        ev = experimental_variogram(points, values, n_lags=8)
        total_binned = sum(b.pair_count for b in ev.bins)
        # Total binned pairs should be <= n_pairs (some may fall outside max_lag)
        self.assertLessEqual(total_binned, ev.n_pairs)


class TestFitVariogram(unittest.TestCase):
    def test_fit_spherical(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev, model_type="spherical")
        self.assertEqual(model.model_type, "spherical")
        self.assertGreater(model.sill, 0)
        self.assertGreater(model.range_param, 0)
        self.assertGreaterEqual(model.nugget, 0)
        self.assertGreater(model.r_squared, 0)

    def test_fit_exponential(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev, model_type="exponential")
        self.assertEqual(model.model_type, "exponential")
        self.assertGreater(model.sill, 0)

    def test_fit_gaussian(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev, model_type="gaussian")
        self.assertEqual(model.model_type, "gaussian")

    def test_fit_linear(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev, model_type="linear")
        self.assertEqual(model.model_type, "linear")

    def test_unknown_model_type(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        with self.assertRaises(ValueError):
            fit_variogram(ev, model_type="invalid")

    def test_empty_bins(self):
        ev = ExperimentalVariogram(bins=[], n_points=0, n_pairs=0,
                                    max_distance=0, lag_width=0)
        model = fit_variogram(ev, model_type="spherical")
        self.assertEqual(model.rmse, 0)

    def test_model_captures_spatial_structure(self):
        """Model should have positive R² for spatially structured data."""
        points, values = _make_clustered_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev, model_type="spherical")
        self.assertGreater(model.r_squared, 0)


class TestAutoFit(unittest.TestCase):
    def test_auto_fit_selects_best(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = auto_fit(ev)
        self.assertIn(model.model_type, ["spherical", "exponential", "gaussian", "linear"])
        self.assertGreater(model.r_squared, 0)


class TestVariogramSurface(unittest.TestCase):
    def test_surface_detection(self):
        points, values = _make_grid_data()
        surface = variogram_surface(points, values, n_directions=4)
        self.assertEqual(len(surface.angles), 4)
        self.assertGreater(surface.anisotropy_ratio, 0)
        self.assertGreater(len(surface.variograms), 0)


class TestVariogramCloud(unittest.TestCase):
    def test_cloud_pairs(self):
        points = [(0, 0), (1, 0), (0, 1), (1, 1)]
        values = [1, 2, 3, 4]
        cloud = variogram_cloud(points, values)
        # 4 points → 6 pairs
        self.assertEqual(len(cloud), 6)
        for d, sv in cloud:
            self.assertGreater(d, 0)
            self.assertGreaterEqual(sv, 0)

    def test_cloud_max_lag(self):
        points = [(0, 0), (1, 0), (100, 0)]
        values = [1, 2, 3]
        cloud = variogram_cloud(points, values, max_lag=10)
        # Only (0,0)-(1,0) pair is within lag 10
        self.assertEqual(len(cloud), 1)


class TestExportSVG(unittest.TestCase):
    def test_svg_export(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev, model_type="spherical")
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            result = export_variogram_svg(ev, model, path)
            self.assertEqual(result, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("Semivariance", content)
            self.assertIn("Spherical", content)
        finally:
            os.unlink(path)

    def test_svg_without_model(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_variogram_svg(ev, output_path=path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
        finally:
            os.unlink(path)

    def test_svg_empty_bins_raises(self):
        ev = ExperimentalVariogram(bins=[], n_points=0, n_pairs=0,
                                    max_distance=0, lag_width=0)
        with self.assertRaises(ValueError):
            export_variogram_svg(ev)


class TestExportCSV(unittest.TestCase):
    def test_csv_export(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            export_variogram_csv(ev, output_path=path)
            with open(path) as f:
                lines = f.readlines()
            self.assertIn("lag_center", lines[0])
            self.assertGreater(len(lines), 1)
        finally:
            os.unlink(path)

    def test_csv_with_model(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            export_variogram_csv(ev, model, output_path=path)
            with open(path) as f:
                header = f.readline()
            self.assertIn("model_value", header)
        finally:
            os.unlink(path)


class TestExportJSON(unittest.TestCase):
    def test_json_export(self):
        import json
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            export_variogram_json(ev, model, output_path=path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("bins", data)
            self.assertIn("model", data)
            self.assertEqual(data["model"]["type"], "spherical")
        finally:
            os.unlink(path)


class TestVariogramSummary(unittest.TestCase):
    def test_summary_without_model(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        text = variogram_summary(ev)
        self.assertIn("VARIOGRAM ANALYSIS", text)
        self.assertIn("Points:", text)
        self.assertIn("Omnidirectional", text)

    def test_summary_with_model(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values)
        model = fit_variogram(ev)
        text = variogram_summary(ev, model)
        self.assertIn("FITTED MODEL", text)
        self.assertIn("Nugget", text)
        self.assertIn("INTERPRETATION", text)

    def test_summary_directional(self):
        points, values = _make_grid_data()
        ev = experimental_variogram(points, values, direction=45, tolerance=22.5)
        text = variogram_summary(ev)
        self.assertIn("45°", text)


if __name__ == "__main__":
    unittest.main()
