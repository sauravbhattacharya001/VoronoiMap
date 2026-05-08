"""Tests for vormap_variogram — Variogram analysis."""

import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_variogram import (
    experimental_variogram,
    fit_variogram,
    evaluate_model,
    auto_fit,
    VariogramModel,
    ExperimentalVariogram,
    variogram_cloud,
    variogram_summary,
)


# ── evaluate_model with fitted models ──────────────────────────────

class TestEvaluateModel:
    def _fit_model(self, model_type="spherical"):
        pts = [(i * 10, j * 10) for i in range(10) for j in range(10)]
        rng = random.Random(42)
        vals = [rng.gauss(0, 5) for _ in pts]
        ev = experimental_variogram(pts, vals, n_lags=10)
        return fit_variogram(ev, model_type=model_type)

    def test_spherical_zero(self):
        m = self._fit_model("spherical")
        val = evaluate_model(m, 0)
        assert val == pytest.approx(0.0, abs=m.nugget + 1e-6)

    def test_spherical_large_distance(self):
        m = self._fit_model("spherical")
        val = evaluate_model(m, m.range_param * 10)
        assert val == pytest.approx(m.sill, rel=0.01)

    def test_spherical_within_range(self):
        m = self._fit_model("spherical")
        val = evaluate_model(m, m.range_param * 0.5)
        assert m.nugget <= val <= m.sill

    def test_exponential_zero(self):
        m = self._fit_model("exponential")
        assert evaluate_model(m, 0) == pytest.approx(0.0, abs=m.nugget + 1e-6)

    def test_exponential_large(self):
        m = self._fit_model("exponential")
        val = evaluate_model(m, m.range_param * 100)
        assert val == pytest.approx(m.sill, rel=0.05)

    def test_gaussian_large(self):
        m = self._fit_model("gaussian")
        val = evaluate_model(m, m.range_param * 100)
        assert val == pytest.approx(m.sill, rel=0.05)

    def test_model_has_positive_sill(self):
        m = self._fit_model("spherical")
        assert m.sill > 0

    def test_model_r_squared(self):
        m = self._fit_model("spherical")
        assert hasattr(m, "r_squared")


# ── Experimental variogram ──────────────────────────────────────────

class TestExperimentalVariogram:
    def _random_data(self, n=50, seed=42):
        rng = random.Random(seed)
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]
        values = [rng.gauss(50, 10) for _ in range(n)]
        return points, values

    def test_returns_correct_type(self):
        pts, vals = self._random_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        assert isinstance(ev, ExperimentalVariogram)

    def test_bins_non_negative_semivariance(self):
        pts, vals = self._random_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        for b in ev.bins:
            assert b.semivariance >= 0

    def test_bin_count(self):
        pts, vals = self._random_data(n=100)
        ev = experimental_variogram(pts, vals, n_lags=15)
        assert len(ev.bins) <= 15

    def test_n_pairs_positive(self):
        pts, vals = self._random_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        assert ev.n_pairs > 0


# ── Variogram fitting ──────────────────────────────────────────────

class TestFitVariogram:
    def _grid_data(self):
        pts = [(i * 10, j * 10) for i in range(10) for j in range(10)]
        rng = random.Random(42)
        vals = [rng.gauss(0, 5) for _ in pts]
        return pts, vals

    def test_fit_spherical(self):
        pts, vals = self._grid_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        model = fit_variogram(ev, model_type="spherical")
        assert isinstance(model, VariogramModel)
        assert model.sill >= model.nugget

    def test_fit_exponential(self):
        pts, vals = self._grid_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        model = fit_variogram(ev, model_type="exponential")
        assert model.sill > 0

    def test_auto_fit(self):
        pts, vals = self._grid_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        model = auto_fit(ev)
        assert isinstance(model, VariogramModel)

    def test_partial_sill(self):
        pts, vals = self._grid_data()
        ev = experimental_variogram(pts, vals, n_lags=10)
        model = fit_variogram(ev, model_type="spherical")
        assert abs(model.partial_sill - (model.sill - model.nugget)) < 1e-6


# ── Variogram cloud ────────────────────────────────────────────────

class TestVariogramCloud:
    def test_cloud(self):
        rng = random.Random(42)
        pts = [(rng.uniform(0, 50), rng.uniform(0, 50)) for _ in range(20)]
        vals = [rng.gauss(10, 3) for _ in pts]
        cloud = variogram_cloud(pts, vals)
        assert len(cloud) > 0


# ── Summary ─────────────────────────────────────────────────────────

class TestSummary:
    def test_summary_string(self):
        rng = random.Random(42)
        pts = [(rng.uniform(0, 50), rng.uniform(0, 50)) for _ in range(30)]
        vals = [rng.gauss(10, 3) for _ in pts]
        ev = experimental_variogram(pts, vals, n_lags=8)
        model = fit_variogram(ev, model_type="spherical")
        s = variogram_summary(ev, model)
        assert isinstance(s, str)
        assert len(s) > 0
