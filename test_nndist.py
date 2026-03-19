"""Tests for vormap_nndist — nearest-neighbor distance analysis."""

import json
import math
import os
import tempfile

import pytest

from vormap_nndist import (
    nn_distances,
    clark_evans,
    g_function,
    distance_summary,
    export_nn_csv,
    export_nn_json,
    ClarkEvansResult,
    GFunctionResult,
    DistanceSummary,
    _validate_points,
    _polygon_area_shoelace,
    _euclidean,
    _nn1_brute,
    _knn_brute,
)


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def grid_points():
    """4x4 regular grid — perfectly dispersed pattern."""
    return [(float(x), float(y)) for x in range(4) for y in range(4)]


@pytest.fixture
def clustered_points():
    """Two tight clusters far apart — clearly clustered pattern."""
    cluster_a = [(0.0 + i * 0.1, 0.0 + j * 0.1) for i in range(5) for j in range(5)]
    cluster_b = [(100.0 + i * 0.1, 100.0 + j * 0.1) for i in range(5) for j in range(5)]
    return cluster_a + cluster_b


@pytest.fixture
def collinear_points():
    """Points on a line — degenerate geometry."""
    return [(float(i), 0.0) for i in range(10)]


@pytest.fixture
def triangle_points():
    """Simple 3-point triangle."""
    return [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]


# ── _validate_points ────────────────────────────────────────────────


class TestValidatePoints:
    def test_valid_tuples(self):
        pts = [(1, 2), (3, 4)]
        result = _validate_points(pts)
        assert result == [(1.0, 2.0), (3.0, 4.0)]

    def test_valid_lists(self):
        pts = [[1.5, 2.5], [3.5, 4.5]]
        result = _validate_points(pts)
        assert result == [(1.5, 2.5), (3.5, 4.5)]

    def test_rejects_single_point(self):
        with pytest.raises(ValueError, match="At least 2 points"):
            _validate_points([(1, 2)])

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="At least 2 points"):
            _validate_points([])

    def test_rejects_nan(self):
        with pytest.raises(ValueError, match="NaN"):
            _validate_points([(1, 2), (float("nan"), 3)])

    def test_rejects_inf(self):
        with pytest.raises(ValueError, match="NaN/Inf"):
            _validate_points([(1, 2), (float("inf"), 3)])

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError, match="2-element"):
            _validate_points([(1, 2, 3), (4, 5)])

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="non-numeric"):
            _validate_points([(1, 2), ("a", "b")])


# ── _euclidean ──────────────────────────────────────────────────────


class TestEuclidean:
    def test_same_point(self):
        assert _euclidean((1, 1), (1, 1)) == 0.0

    def test_horizontal(self):
        assert _euclidean((0, 0), (3, 0)) == pytest.approx(3.0)

    def test_diagonal(self):
        assert _euclidean((0, 0), (3, 4)) == pytest.approx(5.0)


# ── _polygon_area_shoelace ──────────────────────────────────────────


class TestPolygonAreaShoelace:
    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert _polygon_area_shoelace(sq) == pytest.approx(1.0)

    def test_triangle(self):
        tri = [(0, 0), (4, 0), (0, 3)]
        assert _polygon_area_shoelace(tri) == pytest.approx(6.0)

    def test_degenerate_line(self):
        assert _polygon_area_shoelace([(0, 0), (1, 0)]) == 0.0

    def test_empty(self):
        assert _polygon_area_shoelace([]) == 0.0


# ── _nn1_brute ──────────────────────────────────────────────────────


class TestNN1Brute:
    def test_equidistant_line(self, collinear_points):
        dists = _nn1_brute(collinear_points)
        # All interior points have NN distance 1.0; endpoints also 1.0
        assert all(d == pytest.approx(1.0) for d in dists)

    def test_triangle(self, triangle_points):
        dists = _nn1_brute(triangle_points)
        # (0,0)->(3,0) = 3, (3,0)->(0,0) = 3, (0,4)->(0,0) = 4
        # But (0,4)'s NN is (0,0) at dist 4, and (3,0) at dist 5
        # Actually: (0,0) NN is (3,0)=3; (3,0) NN is (0,0)=3; (0,4) NN is (0,0)=4
        assert dists[0] == pytest.approx(3.0)
        assert dists[1] == pytest.approx(3.0)
        assert dists[2] == pytest.approx(4.0)


# ── _knn_brute ──────────────────────────────────────────────────────


class TestKNNBrute:
    def test_k1_matches_nn1(self, collinear_points):
        knn = _knn_brute(collinear_points, 1)
        nn1 = _nn1_brute(collinear_points)
        for i, (dists, _) in enumerate(knn):
            assert dists[0] == pytest.approx(nn1[i])

    def test_k2_sorted(self, triangle_points):
        knn = _knn_brute(triangle_points, 2)
        for dists, indices in knn:
            assert len(dists) == 2
            assert dists[0] <= dists[1]

    def test_k_exceeds_n(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        knn = _knn_brute(pts, 10)
        # k capped at n-1 = 2
        for dists, _ in knn:
            assert len(dists) == 2


# ── nn_distances ────────────────────────────────────────────────────


class TestNNDistances:
    def test_basic_output_structure(self, grid_points):
        result = nn_distances(grid_points, k=1)
        assert len(result) == 16
        for entry in result:
            assert "x" in entry
            assert "y" in entry
            assert "distances" in entry
            assert "neighbors" in entry
            assert len(entry["distances"]) == 1

    def test_k2(self, grid_points):
        result = nn_distances(grid_points, k=2)
        for entry in result:
            assert len(entry["distances"]) == 2
            assert entry["distances"][0] <= entry["distances"][1]

    def test_grid_nn_distance_is_1(self, grid_points):
        result = nn_distances(grid_points, k=1)
        for entry in result:
            assert entry["distances"][0] == pytest.approx(1.0)

    def test_rejects_k_zero(self):
        with pytest.raises(ValueError, match="k must be >= 1"):
            nn_distances([(0, 0), (1, 1)], k=0)

    def test_two_points(self):
        result = nn_distances([(0, 0), (5, 0)], k=1)
        assert result[0]["distances"][0] == pytest.approx(5.0)
        assert result[1]["distances"][0] == pytest.approx(5.0)

    def test_neighbor_indices_valid(self, grid_points):
        result = nn_distances(grid_points, k=3)
        n = len(grid_points)
        for i, entry in enumerate(result):
            for nbr in entry["neighbors"]:
                assert 0 <= nbr < n
                assert nbr != i


# ── clark_evans ─────────────────────────────────────────────────────


class TestClarkEvans:
    def test_grid_dispersed(self, grid_points):
        """Regular grid should be classified as dispersed."""
        result = clark_evans(grid_points, area=9.0)
        assert isinstance(result, ClarkEvansResult)
        assert result.R > 1.0  # dispersed
        assert result.n == 16

    def test_clustered_pattern(self, clustered_points):
        """Two tight clusters should be classified as clustered."""
        result = clark_evans(clustered_points, area=110.0 * 110.0)
        assert result.R < 1.0

    def test_auto_area_estimation(self, grid_points):
        """Should work without explicit area (bounding box estimate)."""
        result = clark_evans(grid_points)
        assert result.n == 16
        assert result.density > 0
        assert result.expected_nn > 0

    def test_boundary_polygon_area(self, grid_points):
        """Should compute area from boundary polygon."""
        boundary = [(0, 0), (3, 0), (3, 3), (0, 3)]
        result = clark_evans(grid_points, boundary=boundary)
        assert result.density == pytest.approx(16.0 / 9.0)

    def test_format_report(self, grid_points):
        result = clark_evans(grid_points, area=9.0)
        report = result.format_report()
        assert "Clark–Evans" in report
        assert "Points:" in report

    def test_to_dict(self, grid_points):
        result = clark_evans(grid_points, area=9.0)
        d = result.to_dict()
        assert "R" in d
        assert "z" in d
        assert "interpretation" in d

    def test_rejects_negative_area(self):
        with pytest.raises(ValueError, match="positive"):
            clark_evans([(0, 0), (1, 1)], area=-10)

    def test_rejects_zero_area_boundary(self):
        with pytest.raises(ValueError, match="zero or negative"):
            clark_evans([(0, 0), (1, 0)], boundary=[(0, 0), (1, 0)])


# ── g_function ──────────────────────────────────────────────────────


class TestGFunction:
    def test_basic_output(self, grid_points):
        result = g_function(grid_points, steps=10)
        assert isinstance(result, GFunctionResult)
        assert len(result.steps) == 11  # steps + 1 (includes 0)
        assert len(result.theoretical) == 11
        assert len(result.nn_distances) == 16

    def test_g_monotonically_increasing(self, grid_points):
        result = g_function(grid_points, steps=20)
        g_values = [s["G"] for s in result.steps]
        for i in range(1, len(g_values)):
            assert g_values[i] >= g_values[i - 1]

    def test_g_starts_at_zero(self, grid_points):
        result = g_function(grid_points, steps=10)
        assert result.steps[0]["d"] == pytest.approx(0.0)
        assert result.steps[0]["G"] == pytest.approx(0.0)

    def test_g_ends_near_one(self, grid_points):
        result = g_function(grid_points, steps=50)
        # Last step distance > max NN distance, so G should be 1.0
        assert result.steps[-1]["G"] == pytest.approx(1.0)

    def test_summary_stats(self, grid_points):
        result = g_function(grid_points, steps=10)
        assert "mean" in result.summary
        assert "median" in result.summary
        assert result.summary["min"] > 0

    def test_boundary_area(self, grid_points):
        boundary = [(0, 0), (3, 0), (3, 3), (0, 3)]
        result = g_function(grid_points, steps=10, boundary=boundary)
        # Theoretical G should use density = 16/9
        assert len(result.theoretical) == 11

    def test_rejects_zero_steps(self):
        with pytest.raises(ValueError, match="steps must be >= 1"):
            g_function([(0, 0), (1, 1)], steps=0)

    def test_nn_distances_sorted(self, grid_points):
        result = g_function(grid_points)
        for i in range(1, len(result.nn_distances)):
            assert result.nn_distances[i] >= result.nn_distances[i - 1]

    def test_format_report(self, grid_points):
        result = g_function(grid_points, steps=5)
        report = result.format_report()
        assert "G-function" in report
        assert "Points:" in report

    def test_to_dict(self, grid_points):
        result = g_function(grid_points, steps=5)
        d = result.to_dict()
        assert "steps" in d
        assert "theoretical" in d
        assert "nn_distances" in d


# ── distance_summary ────────────────────────────────────────────────


class TestDistanceSummary:
    def test_basic_output(self, grid_points):
        result = distance_summary(grid_points, k=1, bins=5)
        assert isinstance(result, DistanceSummary)
        assert result.point_count == 16
        assert result.k == 1
        assert len(result.histogram) == 5

    def test_histogram_counts_sum(self, grid_points):
        result = distance_summary(grid_points, k=2, bins=10)
        total = sum(h["count"] for h in result.histogram)
        # k=2, 16 points = 32 total distances
        assert total == 32

    def test_stats_correctness(self, collinear_points):
        result = distance_summary(collinear_points, k=1)
        # All NN distances are 1.0 on a unit-spaced line
        assert result.stats["mean"] == pytest.approx(1.0)
        assert result.stats["min"] == pytest.approx(1.0)
        assert result.stats["max"] == pytest.approx(1.0)
        assert result.stats["std"] == pytest.approx(0.0, abs=1e-10)

    def test_format_report(self, grid_points):
        result = distance_summary(grid_points, k=1, bins=3)
        report = result.format_report()
        assert "Nearest-Neighbor Distance Summary" in report

    def test_to_dict(self, grid_points):
        result = distance_summary(grid_points)
        d = result.to_dict()
        assert "stats" in d
        assert "histogram" in d


# ── Export functions ─────────────────────────────────────────────────


class TestExport:
    def test_export_csv(self, grid_points, tmp_path):
        summary = distance_summary(grid_points, k=1)
        out = str(tmp_path / "nn.csv")
        result_path = export_nn_csv(summary, out)
        assert os.path.isfile(result_path)
        with open(result_path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "x,y,k,nn_distance,neighbor_index"
        assert len(lines) == 17  # header + 16 points

    def test_export_json_clark_evans(self, grid_points, tmp_path):
        ce = clark_evans(grid_points, area=9.0)
        out = str(tmp_path / "ce.json")
        export_nn_json(ce, out)
        with open(out) as f:
            data = json.load(f)
        assert "R" in data
        assert "interpretation" in data

    def test_export_json_g_function(self, grid_points, tmp_path):
        gf = g_function(grid_points, steps=5)
        out = str(tmp_path / "gf.json")
        export_nn_json(gf, out)
        with open(out) as f:
            data = json.load(f)
        assert "steps" in data
        assert "theoretical" in data

    def test_export_json_distance_summary(self, grid_points, tmp_path):
        ds = distance_summary(grid_points, k=2, bins=5)
        out = str(tmp_path / "ds.json")
        export_nn_json(ds, out)
        with open(out) as f:
            data = json.load(f)
        assert data["point_count"] == 16
        assert data["k"] == 2
