"""Tests for vormap_nndist — nearest-neighbor distance analysis."""

import json
import math
import os
import tempfile

import pytest

from vormap_nndist import (
    ClarkEvansResult,
    DistanceSummary,
    GFunctionResult,
    clark_evans,
    distance_summary,
    export_nn_csv,
    export_nn_json,
    g_function,
    nn_distances,
    _euclidean,
    _mean,
    _median,
    _normal_cdf,
    _percentile,
    _std,
    _validate_points,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _grid_points(rows, cols, spacing=100.0):
    """Generate a regular grid of points."""
    return [
        (c * spacing, r * spacing)
        for r in range(rows)
        for c in range(cols)
    ]


def _cluster_points():
    """Two tight clusters far apart."""
    cluster_a = [(10 + i, 10 + j) for i in range(3) for j in range(3)]
    cluster_b = [(900 + i, 900 + j) for i in range(3) for j in range(3)]
    return cluster_a + cluster_b


def _line_points(n, spacing=10.0):
    """Points on a straight line."""
    return [(i * spacing, 0.0) for i in range(n)]


# ── Statistical helpers ──────────────────────────────────────────────

class TestStatHelpers:
    def test_euclidean_basic(self):
        assert _euclidean((0, 0), (3, 4)) == 5.0

    def test_euclidean_same_point(self):
        assert _euclidean((5, 5), (5, 5)) == 0.0

    def test_euclidean_negative(self):
        d = _euclidean((-1, -1), (2, 3))
        assert abs(d - 5.0) < 1e-10

    def test_mean_basic(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_mean_single(self):
        assert _mean([42]) == 42.0

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std_basic(self):
        assert _std([2, 2, 2, 2]) == 0.0

    def test_std_nonzero(self):
        s = _std([0, 10])
        assert abs(s - 5.0) < 1e-10

    def test_std_single(self):
        assert _std([7]) == 0.0

    def test_median_odd(self):
        assert _median([3, 1, 2]) == 2.0

    def test_median_even(self):
        assert _median([1, 2, 3, 4]) == 2.5

    def test_median_empty(self):
        assert _median([]) == 0.0

    def test_percentile_0(self):
        assert _percentile([10, 20, 30, 40, 50], 0) == 10.0

    def test_percentile_100(self):
        assert _percentile([10, 20, 30, 40, 50], 100) == 50.0

    def test_percentile_50(self):
        assert _percentile([10, 20, 30, 40, 50], 50) == 30.0

    def test_percentile_empty(self):
        assert _percentile([], 50) == 0.0


class TestNormalCDF:
    def test_cdf_zero(self):
        assert abs(_normal_cdf(0) - 0.5) < 1e-5

    def test_cdf_large_positive(self):
        assert _normal_cdf(5.0) > 0.999

    def test_cdf_large_negative(self):
        assert _normal_cdf(-5.0) < 0.001

    def test_cdf_symmetry(self):
        assert abs(_normal_cdf(1.0) + _normal_cdf(-1.0) - 1.0) < 1e-5

    def test_cdf_one_sigma(self):
        # P(Z <= 1) ≈ 0.8413
        assert abs(_normal_cdf(1.0) - 0.8413) < 0.001

    def test_cdf_two_sigma(self):
        # P(Z <= 2) ≈ 0.9772
        assert abs(_normal_cdf(2.0) - 0.9772) < 0.001


# ── Input validation ────────────────────────────────────────────────

class TestValidation:
    def test_valid_tuples(self):
        pts = _validate_points([(0, 0), (1, 1)])
        assert len(pts) == 2
        assert pts[0] == (0.0, 0.0)

    def test_valid_lists(self):
        pts = _validate_points([[0, 0], [1, 1]])
        assert len(pts) == 2

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="At least 2"):
            _validate_points([(0, 0)])

    def test_empty_list(self):
        with pytest.raises(ValueError, match="At least 2"):
            _validate_points([])

    def test_wrong_length(self):
        with pytest.raises(ValueError, match="2-element"):
            _validate_points([(1, 2, 3)])

    def test_non_numeric(self):
        with pytest.raises(ValueError, match="non-numeric"):
            _validate_points([("a", "b")])

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            _validate_points([(0, float("nan"))])

    def test_inf_rejected(self):
        with pytest.raises(ValueError, match="Inf"):
            _validate_points([(float("inf"), 0)])

    def test_not_sequence(self):
        with pytest.raises(ValueError, match="2-element"):
            _validate_points([42])


# ── nn_distances ─────────────────────────────────────────────────────

class TestNNDistances:
    def test_two_points(self):
        result = nn_distances([(0, 0), (3, 4)], k=1)
        assert len(result) == 2
        assert abs(result[0]["distances"][0] - 5.0) < 1e-10
        assert result[0]["neighbors"] == [1]
        assert result[1]["neighbors"] == [0]

    def test_three_points_k1(self):
        pts = [(0, 0), (1, 0), (10, 0)]
        result = nn_distances(pts, k=1)
        # (0,0) nearest is (1,0)
        assert result[0]["neighbors"] == [1]
        assert abs(result[0]["distances"][0] - 1.0) < 1e-10
        # (1,0) nearest is (0,0)
        assert result[1]["neighbors"] == [0]
        # (10,0) nearest is (1,0)
        assert result[2]["neighbors"] == [1]

    def test_k2(self):
        pts = [(0, 0), (1, 0), (3, 0)]
        result = nn_distances(pts, k=2)
        assert len(result[0]["distances"]) == 2
        assert abs(result[0]["distances"][0] - 1.0) < 1e-10
        assert abs(result[0]["distances"][1] - 3.0) < 1e-10
        assert result[0]["neighbors"] == [1, 2]

    def test_k_exceeds_n(self):
        """k larger than n-1 should return n-1 distances."""
        pts = [(0, 0), (1, 0)]
        result = nn_distances(pts, k=5)
        assert len(result[0]["distances"]) == 1

    def test_k_zero_rejected(self):
        with pytest.raises(ValueError, match="k must be"):
            nn_distances([(0, 0), (1, 1)], k=0)

    def test_grid_symmetry(self):
        """All interior grid points should have the same NN distance."""
        pts = _grid_points(3, 3, spacing=10)
        result = nn_distances(pts, k=1)
        # All NN distances should be 10.0
        for r in result:
            assert abs(r["distances"][0] - 10.0) < 1e-10

    def test_returns_correct_coords(self):
        pts = [(5.5, 3.3), (8.8, 9.9)]
        result = nn_distances(pts, k=1)
        assert result[0]["x"] == 5.5
        assert result[0]["y"] == 3.3

    def test_distances_ascending(self):
        """NN distances should be sorted ascending."""
        pts = [(0, 0), (1, 0), (5, 0), (100, 0)]
        result = nn_distances(pts, k=3)
        for r in result:
            for i in range(len(r["distances"]) - 1):
                assert r["distances"][i] <= r["distances"][i + 1]


# ── Clark–Evans Index ────────────────────────────────────────────────

class TestClarkEvans:
    def test_regular_grid_dispersed(self):
        """A regular grid should show R > 1 (dispersed)."""
        pts = _grid_points(5, 5, spacing=100)
        result = clark_evans(pts, area=500 * 500)
        assert result.R > 1.0
        assert result.interpretation == "dispersed"

    def test_clustered_points(self):
        """Two tight clusters should show R < 1 (clustered)."""
        pts = _cluster_points()
        result = clark_evans(pts, area=1000 * 1000)
        assert result.R < 1.0
        assert result.interpretation == "clustered"

    def test_z_and_p_present(self):
        pts = _grid_points(4, 4, spacing=50)
        result = clark_evans(pts, area=200 * 200)
        assert isinstance(result.z, float)
        assert isinstance(result.p, float)
        assert 0.0 <= result.p <= 1.0

    def test_n_correct(self):
        pts = [(i, 0) for i in range(10)]
        result = clark_evans(pts)
        assert result.n == 10

    def test_density_correct(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
        area = 100.0
        result = clark_evans(pts, area=area)
        assert abs(result.density - 4.0 / 100.0) < 1e-10

    def test_auto_area_estimation(self):
        """Without explicit area, should estimate from bounding box."""
        pts = [(0, 0), (100, 0), (0, 100), (100, 100)]
        result = clark_evans(pts)
        assert result.density > 0

    def test_negative_area_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            clark_evans([(0, 0), (1, 1)], area=-10)

    def test_zero_area_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            clark_evans([(0, 0), (1, 1)], area=0)

    def test_format_report(self):
        pts = _grid_points(3, 3, spacing=50)
        result = clark_evans(pts, area=200 * 200)
        report = result.format_report()
        assert "Clark" in report
        assert "R index" in report
        assert str(result.n) in report

    def test_to_dict_keys(self):
        pts = _grid_points(3, 3, spacing=50)
        result = clark_evans(pts, area=200 * 200)
        d = result.to_dict()
        for key in ["R", "mean_nn", "expected_nn", "z", "p", "n",
                     "density", "interpretation"]:
            assert key in d

    def test_two_points(self):
        """Minimum valid input — two points."""
        result = clark_evans([(0, 0), (10, 0)])
        assert result.n == 2
        assert result.R > 0


# ── G-function ───────────────────────────────────────────────────────

class TestGFunction:
    def test_basic_output(self):
        pts = _grid_points(3, 3, spacing=50)
        result = g_function(pts, steps=10)
        assert len(result.steps) == 11  # 0..10
        assert len(result.theoretical) == 11
        assert len(result.nn_distances) == 9

    def test_g_monotonic(self):
        """G(d) must be non-decreasing."""
        pts = _grid_points(4, 4, spacing=25)
        result = g_function(pts, steps=20)
        for i in range(len(result.steps) - 1):
            assert result.steps[i]["G"] <= result.steps[i + 1]["G"]

    def test_g_starts_at_zero(self):
        """G(0) should be 0 or very small."""
        pts = _grid_points(3, 3, spacing=100)
        result = g_function(pts, steps=10)
        assert result.steps[0]["G"] == 0.0

    def test_g_ends_near_one(self):
        """G(d_max * 1.2) should be 1.0 (all NN distances included)."""
        pts = _grid_points(3, 3, spacing=50)
        result = g_function(pts, steps=10)
        assert result.steps[-1]["G"] == 1.0

    def test_theoretical_monotonic(self):
        """Theoretical G(d) must be non-decreasing."""
        pts = _grid_points(3, 3, spacing=50)
        result = g_function(pts, steps=10)
        for i in range(len(result.theoretical) - 1):
            assert result.theoretical[i]["G"] <= result.theoretical[i + 1]["G"]

    def test_summary_stats(self):
        pts = _line_points(5, spacing=10)
        result = g_function(pts, steps=5)
        s = result.summary
        assert s["min"] > 0
        assert s["max"] >= s["min"]
        assert s["mean"] > 0
        assert s["median"] > 0

    def test_nn_distances_sorted(self):
        pts = _cluster_points()
        result = g_function(pts, steps=10)
        for i in range(len(result.nn_distances) - 1):
            assert result.nn_distances[i] <= result.nn_distances[i + 1]

    def test_steps_one(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        result = g_function(pts, steps=1)
        assert len(result.steps) == 2  # 0 and 1

    def test_steps_zero_rejected(self):
        with pytest.raises(ValueError, match="steps must be"):
            g_function([(0, 0), (1, 1)], steps=0)

    def test_format_report(self):
        pts = _grid_points(3, 3, spacing=50)
        result = g_function(pts, steps=5)
        report = result.format_report()
        assert "G-function" in report
        assert "Mean NN" in report

    def test_to_dict_keys(self):
        pts = _grid_points(3, 3, spacing=50)
        result = g_function(pts, steps=5)
        d = result.to_dict()
        assert "steps" in d
        assert "theoretical" in d
        assert "nn_distances" in d
        assert "summary" in d

    def test_custom_area(self):
        pts = _grid_points(3, 3, spacing=50)
        r1 = g_function(pts, steps=5, area=100000)
        r2 = g_function(pts, steps=5, area=1000)
        # Theoretical curves differ with different areas
        assert r1.theoretical[5]["G"] != r2.theoretical[5]["G"]

    def test_clustered_g_rises_fast(self):
        """Clustered points: empirical G rises faster than theoretical at small d."""
        pts = _cluster_points()
        result = g_function(pts, steps=50, area=1000 * 1000)
        # With tight clusters, most NN distances are very small (~1-2 units).
        # The empirical G should reach ~1.0 well before d_max, while CSR
        # rises more slowly.  Check that empirical reaches 0.5 before
        # theoretical does.
        emp_half = None
        theo_half = None
        for i, (e, t) in enumerate(zip(result.steps, result.theoretical)):
            if emp_half is None and e["G"] >= 0.5:
                emp_half = i
            if theo_half is None and t["G"] >= 0.5:
                theo_half = i
        # Empirical should reach 50% at an earlier step than theoretical
        assert emp_half is not None
        assert theo_half is None or emp_half <= theo_half


# ── Distance Summary ─────────────────────────────────────────────────

class TestDistanceSummary:
    def test_basic(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=1)
        assert result.point_count == 9
        assert result.k == 1
        assert len(result.distances) == 9
        assert result.stats["min"] > 0

    def test_histogram_bins(self):
        pts = _grid_points(4, 4, spacing=25)
        result = distance_summary(pts, k=1, bins=5)
        assert len(result.histogram) == 5
        total_count = sum(h["count"] for h in result.histogram)
        assert total_count == 16  # 16 points × 1 NN each

    def test_histogram_proportions_sum_to_one(self):
        pts = _grid_points(4, 4, spacing=25)
        result = distance_summary(pts, k=1, bins=5)
        total = sum(h["proportion"] for h in result.histogram)
        assert abs(total - 1.0) < 1e-10

    def test_k2_doubles_distances(self):
        pts = _grid_points(3, 3, spacing=50)
        r1 = distance_summary(pts, k=1)
        r2 = distance_summary(pts, k=2)
        # k=2 should have more distance entries per point
        assert len(r2.distances[0]["distances"]) == 2

    def test_stats_keys(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=1)
        for key in ["mean", "median", "std", "min", "max", "q1", "q3"]:
            assert key in result.stats

    def test_format_report(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=1)
        report = result.format_report()
        assert "Distance Summary" in report
        assert "Mean" in report

    def test_to_dict(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=1)
        d = result.to_dict()
        assert d["point_count"] == 9
        assert d["k"] == 1

    def test_line_points_uniform(self):
        """Points on a line with equal spacing: all 1-NN distances equal."""
        pts = _line_points(5, spacing=10)
        result = distance_summary(pts, k=1)
        # Interior points have NN dist = 10; endpoints have NN dist = 10
        assert abs(result.stats["mean"] - 10.0) < 1e-10


# ── Export ───────────────────────────────────────────────────────────

class TestExportCSV:
    def test_csv_output(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=1)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_nn_csv(result, path)
            with open(path) as f:
                lines = f.readlines()
            # Header + 9 data rows (9 points × 1 NN each)
            assert lines[0].strip() == "x,y,k,nn_distance,neighbor_index"
            assert len(lines) == 10  # header + 9
        finally:
            os.unlink(path)

    def test_csv_k2(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=2)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_nn_csv(result, path)
            with open(path) as f:
                lines = f.readlines()
            # 9 points × 2 NN each = 18 data rows + header
            assert len(lines) == 19
        finally:
            os.unlink(path)


class TestExportJSON:
    def test_json_distance_summary(self):
        pts = _grid_points(3, 3, spacing=50)
        result = distance_summary(pts, k=1)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_nn_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert data["point_count"] == 9
            assert data["k"] == 1
            assert "stats" in data
        finally:
            os.unlink(path)

    def test_json_clark_evans(self):
        pts = _grid_points(3, 3, spacing=50)
        result = clark_evans(pts, area=200 * 200)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_nn_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert "R" in data
            assert "interpretation" in data
        finally:
            os.unlink(path)

    def test_json_g_function(self):
        pts = _grid_points(3, 3, spacing=50)
        result = g_function(pts, steps=5)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_nn_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert "steps" in data
            assert "theoretical" in data
        finally:
            os.unlink(path)


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    def test_coincident_points(self):
        """Multiple points at the same location."""
        pts = [(5, 5), (5, 5), (5, 5)]
        result = nn_distances(pts, k=1)
        for r in result:
            assert r["distances"][0] == 0.0

    def test_clark_evans_coincident(self):
        """Coincident points → R = 0 (perfectly clustered)."""
        pts = [(5, 5), (5, 5), (5, 5)]
        result = clark_evans(pts, area=100)
        assert result.R == 0.0

    def test_large_k(self):
        """k = n should return n-1 distances."""
        pts = [(i, 0) for i in range(5)]
        result = nn_distances(pts, k=10)
        for r in result:
            assert len(r["distances"]) == 4  # n-1

    def test_two_points_minimum(self):
        result = nn_distances([(0, 0), (1, 1)], k=1)
        assert len(result) == 2
        d = math.sqrt(2)
        assert abs(result[0]["distances"][0] - d) < 1e-10

    def test_negative_coordinates(self):
        pts = [(-10, -20), (30, 40), (-5, 15)]
        result = nn_distances(pts, k=1)
        assert len(result) == 3
        # All distances should be positive
        for r in result:
            assert r["distances"][0] > 0

    def test_very_close_points(self):
        pts = [(0, 0), (1e-10, 0)]
        result = nn_distances(pts, k=1)
        assert result[0]["distances"][0] < 1e-9

    def test_histogram_all_same_distance(self):
        """Points equidistant from each other."""
        # Equilateral triangle
        pts = [(0, 0), (10, 0), (5, 10 * math.sqrt(3) / 2)]
        result = distance_summary(pts, k=1, bins=5)
        assert result.point_count == 3
        # All 1-NN distances should be 10.0
        assert abs(result.stats["std"]) < 1e-8


# ── Path validation security tests ──────────────────────────────────

class TestPathValidation:
    """Verify that export functions validate paths to prevent traversal."""

    POINTS = [(0, 0), (10, 0), (5, 8.66), (3, 4), (7, 2)]

    def test_csv_rejects_traversal(self):
        pts = self.POINTS
        result = distance_summary(pts, k=1)
        with pytest.raises(ValueError, match="traversal"):
            export_nn_csv(result, "../../etc/passwd")

    def test_json_rejects_traversal(self):
        pts = self.POINTS
        result = clark_evans(pts, area=100)
        with pytest.raises(ValueError, match="traversal"):
            export_nn_json(result, "../../../tmp/evil.json")

    def test_csv_accepts_valid_path(self):
        pts = self.POINTS
        result = distance_summary(pts, k=1)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            written = export_nn_csv(result, path)
            assert os.path.exists(written)
        finally:
            os.unlink(path)

    def test_json_accepts_valid_path(self):
        pts = self.POINTS
        result = clark_evans(pts, area=100)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            written = export_nn_json(result, path)
            assert os.path.exists(written)
        finally:
            os.unlink(path)


class TestBoundaryParameter:
    """Tests for the boundary polygon parameter in g_function and clark_evans."""

    POINTS = [(1, 1), (2, 3), (4, 2), (5, 5), (3, 4)]
    # A square boundary enclosing all points
    BOUNDARY = [(0, 0), (6, 0), (6, 6), (0, 6)]

    def test_g_function_with_boundary(self):
        """boundary parameter should compute area via shoelace (6*6=36)."""
        result = g_function(self.POINTS, steps=10, boundary=self.BOUNDARY)
        assert len(result.steps) == 11
        assert len(result.theoretical) == 11
        # With boundary area=36, density=5/36 ≈ 0.1389
        # Verify theoretical uses correct density
        d = result.steps[5]["d"]
        expected_g = 1.0 - math.exp(-(5.0 / 36.0) * math.pi * d * d)
        assert abs(result.theoretical[5]["G"] - expected_g) < 1e-10

    def test_g_function_boundary_vs_explicit_area(self):
        """boundary-computed area should match explicit area=36."""
        r_boundary = g_function(self.POINTS, steps=10, boundary=self.BOUNDARY)
        r_explicit = g_function(self.POINTS, steps=10, area=36.0)
        for b, e in zip(r_boundary.theoretical, r_explicit.theoretical):
            assert abs(b["G"] - e["G"]) < 1e-10

    def test_clark_evans_with_boundary(self):
        """clark_evans should accept boundary parameter."""
        result = clark_evans(self.POINTS, boundary=self.BOUNDARY)
        assert result.n == 5
        assert abs(result.density - 5.0 / 36.0) < 1e-10

    def test_boundary_triangle(self):
        """Non-rectangular boundary should compute correct area."""
        triangle = [(0, 0), (10, 0), (5, 10)]
        # Area = 0.5 * 10 * 10 = 50
        pts = [(3, 2), (5, 4), (4, 3)]
        result = g_function(pts, steps=5, boundary=triangle)
        density = 3.0 / 50.0
        d = result.steps[3]["d"]
        expected_g = 1.0 - math.exp(-density * math.pi * d * d)
        assert abs(result.theoretical[3]["G"] - expected_g) < 1e-10

    def test_invalid_boundary_raises(self):
        """Degenerate boundary (line) should raise ValueError."""
        line = [(0, 0), (1, 1)]
        with pytest.raises(ValueError, match="zero or negative area"):
            g_function(self.POINTS, boundary=line)
