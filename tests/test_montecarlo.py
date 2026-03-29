"""Tests for vormap_montecarlo — Monte Carlo spatial simulation."""

import json
import math
import os
import random

import pytest

from vormap_montecarlo import (
    MonteCarloTest,
    MonteCarloResult,
    export_envelope_svg,
    export_json,
    _load_points,
)

BOUNDS = (0, 1000, 0, 2000)


def _random_points(n=50, seed=42):
    rng = random.Random(seed)
    return [(rng.uniform(0, 2000), rng.uniform(0, 1000)) for _ in range(n)]


def _clustered_points(n=50, seed=42):
    rng = random.Random(seed)
    points = []
    centers = [(500, 500), (1500, 500)]
    for _ in range(n):
        cx, cy = rng.choice(centers)
        points.append((cx + rng.gauss(0, 30), cy + rng.gauss(0, 30)))
    return points


def _grid_points(nx=7, ny=7):
    points = []
    for i in range(nx):
        for j in range(ny):
            points.append((200 + i * 200, 100 + j * 100))
    return points


# ── Construction ────────────────────────────────────────────────────

class TestConstruction:
    def test_basic_creation(self):
        test = MonteCarloTest(_random_points(20), BOUNDS)
        assert test.n == 20
        assert test.bounds == BOUNDS

    def test_too_few_points_raises(self):
        with pytest.raises(ValueError, match="at least 3"):
            MonteCarloTest([(0, 0), (1, 1)], BOUNDS)

    def test_single_point_raises(self):
        with pytest.raises(ValueError):
            MonteCarloTest([(5, 5)], BOUNDS)


# ── Run ─────────────────────────────────────────────────────────────

class TestRun:
    def test_basic_run(self):
        result = MonteCarloTest(_random_points(30), BOUNDS).run(
            simulations=19, seed=123)
        assert isinstance(result, MonteCarloResult)
        assert result.n_points == 30
        assert result.n_sims == 19

    def test_nni_envelope(self):
        result = MonteCarloTest(_random_points(30), BOUNDS).run(
            simulations=19, seed=123)
        assert result.nni is not None
        assert 0 < result.nni.observed < 3
        assert result.nni.lower_2_5 <= result.nni.upper_97_5
        assert 0 <= result.nni.p_value <= 1
        assert result.nni.n_sims == 19

    def test_vmr_envelope(self):
        result = MonteCarloTest(_random_points(30), BOUNDS).run(
            simulations=19, seed=123)
        assert result.vmr is not None
        assert result.vmr.observed >= 0
        assert result.vmr.lower_2_5 <= result.vmr.upper_97_5
        assert 0 <= result.vmr.p_value <= 1

    def test_area_envelope(self):
        result = MonteCarloTest(_random_points(30), BOUNDS).run(
            simulations=19, seed=123)
        assert result.area is not None
        assert result.area.observed_mean > 0
        assert result.area.observed_cv >= 0
        assert 0 <= result.area.cv_p_value <= 1

    def test_ripleys_l_envelope(self):
        result = MonteCarloTest(_random_points(30), BOUNDS).run(
            simulations=19, seed=123, radii_count=5)
        rl = result.ripleys_l
        assert rl is not None
        assert len(rl.radii) == 5
        assert len(rl.observed_l) == 5
        assert len(rl.envelope_mean) == 5
        assert 0 <= rl.global_p_value <= 1

    def test_reproducible_with_seed(self):
        pts = _random_points(20)
        r1 = MonteCarloTest(pts, BOUNDS).run(simulations=9, seed=42)
        r2 = MonteCarloTest(pts, BOUNDS).run(simulations=9, seed=42)
        assert r1.nni.p_value == r2.nni.p_value
        assert r1.nni.observed == r2.nni.observed

    def test_custom_quadrat_grid(self):
        result = MonteCarloTest(_random_points(30), BOUNDS).run(
            simulations=9, seed=1, quadrat_nx=4, quadrat_ny=4)
        assert result.vmr is not None


# ── Pattern detection ───────────────────────────────────────────────

class TestDetection:
    def test_clustered_nni_below_one(self):
        result = MonteCarloTest(_clustered_points(40), BOUNDS).run(
            simulations=49, seed=99)
        assert result.nni.observed < 1.0

    def test_clustered_nni_below_simulated_mean(self):
        result = MonteCarloTest(_clustered_points(40), BOUNDS).run(
            simulations=49, seed=99)
        assert result.nni.observed < result.nni.simulated_mean

    def test_grid_nni_above_one(self):
        pts = _grid_points(7, 7)
        tight_bounds = (50, 750, 150, 1450)
        result = MonteCarloTest(pts, tight_bounds).run(
            simulations=49, seed=77)
        assert result.nni.observed > 1.0


# ── Summary & serialization ─────────────────────────────────────────

class TestSummary:
    def test_summary_string(self):
        result = MonteCarloTest(_random_points(20), BOUNDS).run(
            simulations=9, seed=1)
        s = result.summary()
        assert "Monte Carlo Spatial Hypothesis Test" in s
        assert "NNI" in s
        assert "VMR" in s
        assert "p-value" in s
        assert "Overall Verdict" in s

    def test_to_dict(self):
        result = MonteCarloTest(_random_points(20), BOUNDS).run(
            simulations=9, seed=1)
        d = result.to_dict()
        assert d["n_points"] == 20
        assert d["n_sims"] == 9
        assert "nni" in d
        assert "vmr" in d
        assert "area" in d
        assert "ripleys_l" in d

    def test_to_dict_json_serializable(self):
        result = MonteCarloTest(_random_points(20), BOUNDS).run(
            simulations=9, seed=1)
        j = json.dumps(result.to_dict())
        assert len(j) > 100
        parsed = json.loads(j)
        assert parsed["n_points"] == 20


# ── SVG Export ──────────────────────────────────────────────────────

class TestSVGExport:
    def test_svg_creation(self):
        result = MonteCarloTest(_random_points(20), BOUNDS).run(
            simulations=9, seed=1, radii_count=5)
        out = "test_mc_envelope.svg"
        try:
            export_envelope_svg(result, out)
            assert os.path.exists(out)
            with open(out, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
            assert "Ripley" in content
        finally:
            if os.path.exists(out):
                os.remove(out)

    def test_svg_no_ripleys_raises(self):
        result = MonteCarloResult(10, BOUNDS, 5, None)
        with pytest.raises(ValueError):
            export_envelope_svg(result, "test_mc_bad.svg")


# ── JSON Export ─────────────────────────────────────────────────────

class TestJSONExport:
    def test_json_creation(self):
        result = MonteCarloTest(_random_points(20), BOUNDS).run(
            simulations=9, seed=1)
        out = "test_mc_result.json"
        try:
            export_json(result, out)
            assert os.path.exists(out)
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
            assert data["n_points"] == 20
            assert "nni" in data
        finally:
            if os.path.exists(out):
                os.remove(out)


# ── Point loading ───────────────────────────────────────────────────

class TestLoadPoints:
    def test_load_csv(self):
        f = "test_mc_pts.csv"
        try:
            with open(f, "w") as fh:
                fh.write("100,200\n300,400\n500,600\n")
            pts = _load_points(f)
            assert len(pts) == 3
            assert pts[0] == (100.0, 200.0)
        finally:
            if os.path.exists(f):
                os.remove(f)

    def test_load_space_separated(self):
        f = "test_mc_pts.txt"
        try:
            with open(f, "w") as fh:
                fh.write("100 200\n300 400\n")
            pts = _load_points(f)
            assert len(pts) == 2
        finally:
            if os.path.exists(f):
                os.remove(f)

    def test_load_json_dict(self):
        f = "test_mc_pts.json"
        try:
            with open(f, "w") as fh:
                json.dump([{"x": 100, "y": 200}, {"x": 300, "y": 400}], fh)
            pts = _load_points(f)
            assert len(pts) == 2
            assert pts[0] == (100.0, 200.0)
        finally:
            if os.path.exists(f):
                os.remove(f)

    def test_load_json_array(self):
        f = "test_mc_pts2.json"
        try:
            with open(f, "w") as fh:
                json.dump([[100, 200], [300, 400], [500, 600]], fh)
            pts = _load_points(f)
            assert len(pts) == 3
        finally:
            if os.path.exists(f):
                os.remove(f)

    def test_load_skips_comments(self):
        f = "test_mc_comments.txt"
        try:
            with open(f, "w") as fh:
                fh.write("# header\n100 200\n# comment\n300 400\n")
            pts = _load_points(f)
            assert len(pts) == 2
        finally:
            if os.path.exists(f):
                os.remove(f)

    def test_load_skips_bad_lines(self):
        f = "test_mc_bad.txt"
        try:
            with open(f, "w") as fh:
                fh.write("100 200\nnot_a_number\n300 400\n")
            pts = _load_points(f)
            assert len(pts) == 2
        finally:
            if os.path.exists(f):
                os.remove(f)


# ── Edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_three_points_minimum(self):
        result = MonteCarloTest(
            [(100, 100), (500, 500), (900, 900)], BOUNDS
        ).run(simulations=9, seed=1)
        assert result.nni is not None

    def test_collinear_points(self):
        result = MonteCarloTest(
            [(100, 500), (500, 500), (900, 500), (1300, 500)], BOUNDS
        ).run(simulations=9, seed=1)
        assert result.nni.observed > 0

    def test_identical_points(self):
        result = MonteCarloTest(
            [(500, 500)] * 5, BOUNDS
        ).run(simulations=9, seed=1)
        assert result.nni.observed == 0.0

    def test_large_bounds(self):
        result = MonteCarloTest(
            [(50, 50), (60, 60), (70, 70)], (0, 100000, 0, 100000)
        ).run(simulations=9, seed=1)
        assert result.nni is not None

    def test_interpretation_values(self):
        result = MonteCarloTest(_random_points(20), BOUNDS).run(
            simulations=9, seed=1)
        assert result.nni.interpretation in (
            "clustered", "random", "dispersed")
        assert result.vmr.interpretation in (
            "clustered", "random", "dispersed")
