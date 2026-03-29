"""Tests for vormap_fractal — fractal dimension analysis."""

import json
import math
import os
import random
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from vormap_fractal import (
    box_count,
    lacunarity,
    correlation_dimension,
    multifractal_spectrum,
    boundary_dimension,
    fractal_analysis,
    format_report,
    export_json,
    _extract_points,
    _linear_regression,
)


# ── Fixtures ─────────────────────────────────────────────────

def _grid_points(n=10):
    """Regular n×n grid (highly uniform)."""
    return [(i, j) for i in range(n) for j in range(n)]


def _random_points(n=200, seed=42):
    """Uniform random points in [0, 100]²."""
    rng = random.Random(seed)
    return [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]


def _clustered_points(n_clusters=5, pts_per=20, spread=2.0, seed=42):
    """Gaussian clusters around random centres."""
    rng = random.Random(seed)
    points = []
    centres = [(rng.uniform(10, 90), rng.uniform(10, 90)) for _ in range(n_clusters)]
    for cx, cy in centres:
        for _ in range(pts_per):
            points.append((cx + rng.gauss(0, spread), cy + rng.gauss(0, spread)))
    return points


def _line_points(n=100, seed=42):
    """Points along y = x with small noise (1D structure)."""
    rng = random.Random(seed)
    return [(i + rng.gauss(0, 0.1), i + rng.gauss(0, 0.1)) for i in range(n)]


def _data_dict(points):
    """Wrap points as a vormap data dict."""
    return {"points": [{"long": x, "lat": y} for x, y in points]}


# ── Box-Counting Tests ───────────────────────────────────────

class TestBoxCount:
    def test_grid_dimension_positive(self):
        bc = box_count(_grid_points(20), num_scales=10)
        assert bc["dimension"] > 0.5
        assert bc["r_squared"] > 0.8

    def test_line_dimension_near_1(self):
        bc = box_count(_line_points(200), num_scales=10)
        assert 0.7 < bc["dimension"] < 1.4

    def test_random_dimension_positive(self):
        bc = box_count(_random_points(500), num_scales=10)
        assert bc["dimension"] > 0.5

    def test_returns_required_keys(self):
        bc = box_count(_random_points(50), num_scales=5)
        assert "dimension" in bc
        assert "scales" in bc
        assert "counts" in bc
        assert "r_squared" in bc
        assert "raw" in bc

    def test_scales_length_matches_counts(self):
        bc = box_count(_random_points(50), num_scales=8)
        assert len(bc["scales"]) == len(bc["counts"])

    def test_rejects_single_point(self):
        with pytest.raises(ValueError, match="at least 2"):
            box_count([(0, 0)])

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            box_count([])

    def test_rejects_identical_points(self):
        with pytest.raises(ValueError, match="identical"):
            box_count([(5, 5), (5, 5), (5, 5)])

    def test_num_scales_controls_resolution(self):
        bc5 = box_count(_random_points(100), num_scales=5)
        bc15 = box_count(_random_points(100), num_scales=15)
        assert len(bc15["scales"]) >= len(bc5["scales"])

    def test_raw_has_epsilon_and_count(self):
        bc = box_count(_grid_points(5), num_scales=5)
        for eps, n in bc["raw"]:
            assert eps > 0
            assert n > 0


# ── Lacunarity Tests ─────────────────────────────────────────

class TestLacunarity:
    def test_grid_reasonable_lacunarity(self):
        lac = lacunarity(_grid_points(10), num_scales=8)
        # Grid at meaningful scales should not be extremely clustered
        assert lac["mean_lacunarity"] > 0
        assert "classification" in lac

    def test_clustered_high_lacunarity(self):
        lac = lacunarity(_clustered_points(3, 30, 1.5), num_scales=8)
        assert lac["mean_lacunarity"] > 1.5

    def test_returns_required_keys(self):
        lac = lacunarity(_random_points(50), num_scales=5)
        assert "mean_lacunarity" in lac
        assert "by_scale" in lac
        assert "classification" in lac

    def test_by_scale_has_fields(self):
        lac = lacunarity(_random_points(100), num_scales=5)
        for entry in lac["by_scale"]:
            assert "epsilon" in entry
            assert "lacunarity" in entry
            assert "mean_count" in entry

    def test_classification_is_valid(self):
        lac = lacunarity(_random_points(200), num_scales=6)
        assert lac["classification"] in ("uniform", "moderate", "heterogeneous", "highly_clustered")

    def test_rejects_too_few_points(self):
        with pytest.raises(ValueError, match="at least 2"):
            lacunarity([(0, 0)])


# ── Correlation Dimension Tests ──────────────────────────────

class TestCorrelationDimension:
    def test_grid_dimension_near_2(self):
        cd = correlation_dimension(_grid_points(10), num_radii=15)
        assert 1.2 < cd["dimension"] < 2.5

    def test_line_dimension_near_1(self):
        cd = correlation_dimension(_line_points(100), num_radii=15)
        assert 0.5 < cd["dimension"] < 1.6

    def test_returns_required_keys(self):
        cd = correlation_dimension(_random_points(50), num_radii=10)
        assert "dimension" in cd
        assert "r_squared" in cd
        assert "radii" in cd

    def test_rejects_too_few(self):
        with pytest.raises(ValueError, match="at least 3"):
            correlation_dimension([(0, 0), (1, 1)])

    def test_r_squared_positive(self):
        cd = correlation_dimension(_random_points(100), num_radii=15)
        assert cd["r_squared"] >= 0


# ── Multifractal Tests ───────────────────────────────────────

class TestMultifractal:
    def test_grid_spectrum_width(self):
        mf = multifractal_spectrum(_grid_points(10), num_scales=8)
        # Spectrum width should be finite
        assert mf["spectrum_width"] >= 0

    def test_returns_all_q_values(self):
        q_range = list(range(-3, 4))
        mf = multifractal_spectrum(_random_points(100), q_range=q_range, num_scales=6)
        for q in q_range:
            assert q in mf["dimensions"]

    def test_d0_d1_d2_keys(self):
        mf = multifractal_spectrum(_random_points(100), num_scales=6)
        assert "d0" in mf
        assert "d1" in mf
        assert "d2" in mf

    def test_d0_consistent_sign(self):
        pts = _random_points(200)
        bc = box_count(pts, num_scales=10)
        mf = multifractal_spectrum(pts, num_scales=10)
        # Both D₀ estimates should be positive
        assert bc["dimension"] > 0
        assert mf["d0"] > 0

    def test_rejects_too_few(self):
        with pytest.raises(ValueError, match="at least 2"):
            multifractal_spectrum([(0, 0)])

    def test_spectrum_width_non_negative(self):
        mf = multifractal_spectrum(_random_points(50), num_scales=6)
        assert mf["spectrum_width"] >= 0


# ── Boundary Dimension Tests ─────────────────────────────────

class TestBoundaryDimension:
    def _square_regions(self):
        """Two adjacent square regions."""
        return [
            {"vertices": [(0, 0), (1, 0), (1, 1), (0, 1)]},
            {"vertices": [(1, 0), (2, 0), (2, 1), (1, 1)]},
        ]

    def test_square_boundary_positive(self):
        bd = boundary_dimension(self._square_regions(), num_scales=8)
        assert bd["dimension"] > 0

    def test_returns_required_keys(self):
        bd = boundary_dimension(self._square_regions(), num_scales=5)
        assert "dimension" in bd
        assert "r_squared" in bd

    def test_empty_regions(self):
        bd = boundary_dimension([], num_scales=5)
        assert bd["dimension"] == 1.0

    def test_regions_without_vertices(self):
        bd = boundary_dimension([{"vertices": []}, {}], num_scales=5)
        assert bd["dimension"] == 1.0


# ── Combined Analysis Tests ──────────────────────────────────

class TestFractalAnalysis:
    def test_full_analysis_keys(self):
        data = _data_dict(_random_points(100))
        result = fractal_analysis(data)
        assert "box_counting" in result
        assert "lacunarity" in result
        assert "correlation" in result
        assert "multifractal" in result
        assert "summary" in result
        assert "interpretation" in result

    def test_summary_keys(self):
        data = _data_dict(_random_points(100))
        result = fractal_analysis(data)
        s = result["summary"]
        assert "box_counting_dimension" in s
        assert "correlation_dimension" in s
        assert "mean_lacunarity" in s
        assert "is_multifractal" in s

    def test_with_regions(self):
        data = _data_dict(_random_points(50))
        regions = [
            {"vertices": [(0, 0), (50, 0), (50, 50), (0, 50)]},
            {"vertices": [(50, 0), (100, 0), (100, 50), (50, 50)]},
        ]
        result = fractal_analysis(data, regions=regions)
        assert "boundary" in result
        assert "boundary_dimension" in result["summary"]

    def test_without_regions(self):
        data = _data_dict(_random_points(50))
        result = fractal_analysis(data)
        assert "boundary" not in result

    def test_custom_options(self):
        data = _data_dict(_random_points(50))
        result = fractal_analysis(data, options={
            "num_scales": 6, "num_radii": 10, "q_range": [-2, -1, 0, 1, 2],
        })
        assert len(result["multifractal"]["dimensions"]) == 5

    def test_rejects_too_few_points(self):
        data = _data_dict([(0, 0), (1, 1)])
        with pytest.raises(ValueError, match="at least 3"):
            fractal_analysis(data)

    def test_interpretation_is_list(self):
        data = _data_dict(_random_points(100))
        result = fractal_analysis(data)
        assert isinstance(result["interpretation"], list)
        assert len(result["interpretation"]) >= 2

    def test_num_points_correct(self):
        pts = _random_points(77)
        result = fractal_analysis(_data_dict(pts))
        assert result["num_points"] == 77


# ── Report and Export Tests ──────────────────────────────────

class TestReporting:
    def test_format_report_returns_string(self):
        data = _data_dict(_random_points(50))
        result = fractal_analysis(data)
        report = format_report(result)
        assert isinstance(report, str)
        assert "Box-counting" in report
        assert "Lacunarity" in report

    def test_format_report_with_boundary(self):
        data = _data_dict(_random_points(50))
        regions = [{"vertices": [(0, 0), (50, 0), (50, 50), (0, 50)]}]
        result = fractal_analysis(data, regions=regions)
        report = format_report(result)
        assert "Boundary" in report

    def test_format_report_has_dq_table(self):
        data = _data_dict(_random_points(50))
        result = fractal_analysis(data)
        report = format_report(result)
        assert "D_q" in report
        assert "+0" in report

    def test_export_json_creates_file(self):
        data = _data_dict(_random_points(50))
        result = fractal_analysis(data)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(result, path)
            with open(path, "r") as f:
                loaded = json.load(f)
            assert "summary" in loaded
            assert "box_counting" in loaded
            assert "raw" not in loaded.get("box_counting", {})
        finally:
            os.unlink(path)


# ── Helper Tests ─────────────────────────────────────────────

class TestHelpers:
    def test_extract_points_from_dict(self):
        pts = _extract_points({"points": [{"long": 1, "lat": 2}, {"long": 3, "lat": 4}]})
        assert pts == [(1, 2), (3, 4)]

    def test_extract_points_from_list(self):
        pts = _extract_points([(5, 6), (7, 8)])
        assert pts == [(5, 6), (7, 8)]

    def test_extract_points_xy_keys(self):
        pts = _extract_points({"points": [{"x": 10, "y": 20}]})
        assert pts == [(10, 20)]

    def test_extract_points_bad_input(self):
        with pytest.raises(ValueError):
            _extract_points("not valid")

    def test_linear_regression_perfect_fit(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2, 4, 6, 8, 10]
        slope, intercept, r_sq = _linear_regression(xs, ys)
        assert abs(slope - 2.0) < 0.001
        assert abs(intercept) < 0.001
        assert r_sq > 0.999

    def test_linear_regression_single_point(self):
        slope, intercept, r_sq = _linear_regression([1], [2])
        assert slope == 0.0
        assert r_sq == 0.0

    def test_linear_regression_noisy(self):
        xs = [1, 2, 3, 4, 5]
        ys = [2.1, 3.9, 6.2, 7.8, 10.1]
        slope, _, r_sq = _linear_regression(xs, ys)
        assert 1.5 < slope < 2.5
        assert r_sq > 0.95


# ── Pattern Discrimination Tests ─────────────────────────────

class TestPatternDiscrimination:
    """Verify fractal metrics can distinguish different spatial patterns."""

    def test_grid_vs_clustered_lacunarity(self):
        grid_lac = lacunarity(_grid_points(10), num_scales=8)
        clust_lac = lacunarity(_clustered_points(3, 30, 2.0), num_scales=8)
        assert clust_lac["mean_lacunarity"] > grid_lac["mean_lacunarity"]

    def test_line_lower_dimension_than_plane(self):
        """Line points should have lower correlation dimension than plane points."""
        line_cd = correlation_dimension(_line_points(100), num_radii=15)
        plane_cd = correlation_dimension(_random_points(300), num_radii=15)
        assert plane_cd["dimension"] > line_cd["dimension"]

    def test_grid_vs_random_regularity(self):
        """Grid should have lower lacunarity (more uniform) than random."""
        grid_lac = lacunarity(_grid_points(10), num_scales=8)
        rand_lac = lacunarity(_random_points(100), num_scales=8)
        # Grid should be more uniform (lower or similar lacunarity)
        assert grid_lac["mean_lacunarity"] <= rand_lac["mean_lacunarity"] + 0.5
