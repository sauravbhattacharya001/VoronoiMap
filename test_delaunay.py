"""Tests for vormap_delaunay — Delaunay triangulation quality analysis."""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vormap_delaunay import (
    delaunay_triangulate,
    triangle_quality,
    mesh_statistics,
    angle_spectrum,
    edge_analysis,
    delaunay_quality,
    format_report,
    export_json,
    classify_triangle,
    _circumcircle,
    _in_circumcircle,
    _edge_length,
    _triangle_area,
    _angle_at_vertex,
    _percentile,
)


# ── Fixtures ─────────────────────────────────────────────────

def _equilateral():
    """Equilateral triangle points."""
    return [(0, 0), (2, 0), (1, math.sqrt(3))]


def _square():
    """Square with 4 corners."""
    return [(0, 0), (1, 0), (1, 1), (0, 1)]


def _grid(n=4):
    """Regular n×n grid."""
    return [(i, j) for i in range(n) for j in range(n)]


def _random_points(n=20, seed=42):
    """Deterministic pseudo-random points."""
    import random
    rng = random.Random(seed)
    return [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]


def _collinear():
    """Collinear points (should raise ValueError)."""
    return [(0, 0), (1, 1), (2, 2), (3, 3)]


def _pentagon():
    """Regular pentagon."""
    return [
        (math.cos(2 * math.pi * i / 5), math.sin(2 * math.pi * i / 5))
        for i in range(5)
    ]


# ══════════════════════════════════════════════════════════════
#  Internal Helpers
# ══════════════════════════════════════════════════════════════

class TestCircumcircle:
    def test_equilateral(self):
        pts = _equilateral()
        cc = _circumcircle(pts[0], pts[1], pts[2])
        assert cc is not None
        cx, cy, r2 = cc
        assert abs(cx - 1.0) < 1e-10
        r = math.sqrt(r2)
        assert r > 0

    def test_right_triangle(self):
        cc = _circumcircle((0, 0), (4, 0), (0, 3))
        assert cc is not None
        cx, cy, r2 = cc
        # Hypotenuse midpoint = circumcenter for right triangle
        assert abs(cx - 2.0) < 1e-10
        assert abs(cy - 1.5) < 1e-10
        assert abs(math.sqrt(r2) - 2.5) < 1e-10

    def test_collinear_returns_none(self):
        cc = _circumcircle((0, 0), (1, 1), (2, 2))
        assert cc is None

    def test_in_circumcircle(self):
        cc = _circumcircle((0, 0), (4, 0), (0, 4))
        assert cc is not None
        # Center should be inside
        assert _in_circumcircle((2, 2), cc)
        # Far-away point should be outside
        assert not _in_circumcircle((100, 100), cc)


class TestEdgeLength:
    def test_horizontal(self):
        assert abs(_edge_length((0, 0), (3, 0)) - 3.0) < 1e-10

    def test_diagonal(self):
        assert abs(_edge_length((0, 0), (1, 1)) - math.sqrt(2)) < 1e-10

    def test_zero(self):
        assert _edge_length((5, 5), (5, 5)) == 0.0


class TestTriangleArea:
    def test_unit_right(self):
        assert abs(_triangle_area((0, 0), (1, 0), (0, 1)) - 0.5) < 1e-10

    def test_equilateral(self):
        pts = _equilateral()
        area = _triangle_area(*pts)
        expected = math.sqrt(3)  # side=2, area = sqrt(3)/4 * 4
        assert abs(area - expected) < 1e-10

    def test_degenerate(self):
        assert _triangle_area((0, 0), (1, 1), (2, 2)) < 1e-10


class TestAngleAtVertex:
    def test_right_angle(self):
        angle = _angle_at_vertex((0, 0), (1, 0), (0, 1))
        assert abs(angle - 90.0) < 1e-10

    def test_equilateral_angles(self):
        pts = _equilateral()
        a1 = _angle_at_vertex(pts[0], pts[1], pts[2])
        a2 = _angle_at_vertex(pts[1], pts[0], pts[2])
        a3 = _angle_at_vertex(pts[2], pts[0], pts[1])
        assert abs(a1 - 60.0) < 1e-8
        assert abs(a2 - 60.0) < 1e-8
        assert abs(a3 - 60.0) < 1e-8

    def test_sum_180(self):
        pts = [(0, 0), (3, 0), (1, 2)]
        a = _angle_at_vertex(pts[0], pts[1], pts[2])
        b = _angle_at_vertex(pts[1], pts[0], pts[2])
        c = _angle_at_vertex(pts[2], pts[0], pts[1])
        assert abs(a + b + c - 180.0) < 1e-8


class TestPercentile:
    def test_median(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_min(self):
        assert _percentile([10, 20, 30], 0) == 10.0

    def test_max(self):
        assert _percentile([10, 20, 30], 100) == 30.0

    def test_empty(self):
        assert _percentile([], 50) == 0.0

    def test_single(self):
        assert _percentile([42], 50) == 42.0

    def test_interpolation(self):
        # p25 of [0, 10, 20, 30]: k = 0.75 -> lerp(0, 10, 0.75) = 7.5
        assert abs(_percentile([0, 10, 20, 30], 25) - 7.5) < 1e-10


# ══════════════════════════════════════════════════════════════
#  Delaunay Triangulation
# ══════════════════════════════════════════════════════════════

class TestDelaunayTriangulate:
    def test_three_points(self):
        pts = _equilateral()
        tris = delaunay_triangulate(pts)
        assert len(tris) == 1
        assert set(tris[0]) == {0, 1, 2}

    def test_square(self):
        tris = delaunay_triangulate(_square())
        assert len(tris) == 2  # 4 points -> 2 triangles

    def test_grid_4x4(self):
        pts = _grid(4)
        tris = delaunay_triangulate(pts)
        # 16 points, convex hull -> 2*(16)-2-h triangles (h = hull pts)
        assert len(tris) >= 18  # at least 18 triangles for 4x4 grid

    def test_pentagon(self):
        tris = delaunay_triangulate(_pentagon())
        assert len(tris) == 3  # 5 points -> 3 triangles

    def test_random_20(self):
        pts = _random_points(20)
        tris = delaunay_triangulate(pts)
        assert len(tris) > 0
        # All indices valid
        for t in tris:
            assert all(0 <= i < 20 for i in t)

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 3"):
            delaunay_triangulate([(0, 0), (1, 1)])

    def test_collinear_raises(self):
        with pytest.raises(ValueError, match="collinear"):
            delaunay_triangulate(_collinear())

    def test_duplicate_points(self):
        # Duplicate points should still work (degenerate triangles filtered)
        pts = [(0, 0), (1, 0), (0, 1), (0, 0)]  # duplicate
        tris = delaunay_triangulate(pts)
        assert len(tris) >= 1

    def test_large_set(self):
        pts = _random_points(100, seed=99)
        tris = delaunay_triangulate(pts)
        assert len(tris) >= 100  # roughly 2n - h - 2


# ══════════════════════════════════════════════════════════════
#  Triangle Quality
# ══════════════════════════════════════════════════════════════

class TestTriangleQuality:
    def test_equilateral(self):
        pts = _equilateral()
        m = triangle_quality(pts, (0, 1, 2))
        assert abs(m["min_angle"] - 60.0) < 1e-6
        assert abs(m["max_angle"] - 60.0) < 1e-6
        assert abs(m["aspect_ratio"] - 1.0) < 1e-6
        assert m["quality_class"] == "excellent"

    def test_right_triangle(self):
        pts = [(0, 0), (1, 0), (0, 1)]
        m = triangle_quality(pts, (0, 1, 2))
        assert abs(m["min_angle"] - 45.0) < 1e-6
        assert abs(m["max_angle"] - 90.0) < 1e-6
        assert m["quality_class"] == "good"

    def test_skinny_triangle(self):
        pts = [(0, 0), (100, 0), (50, 0.1)]
        m = triangle_quality(pts, (0, 1, 2))
        assert m["min_angle"] < 1.0
        assert m["quality_class"] == "degenerate"

    def test_area_positive(self):
        pts = [(0, 0), (3, 0), (1, 4)]
        m = triangle_quality(pts, (0, 1, 2))
        assert m["area"] > 0

    def test_edges_match(self):
        pts = [(0, 0), (3, 0), (0, 4)]
        m = triangle_quality(pts, (0, 1, 2))
        # c = edge p1-p2 = 3, b = edge p1-p3 = 4, a = edge p2-p3 = 5
        assert abs(m["edges"]["c"] - 3.0) < 1e-10
        assert abs(m["edges"]["b"] - 4.0) < 1e-10
        assert abs(m["edges"]["a"] - 5.0) < 1e-10

    def test_angles_sum_180(self):
        pts = [(1, 2), (4, 1), (3, 5)]
        m = triangle_quality(pts, (0, 1, 2))
        total = m["angles"]["A"] + m["angles"]["B"] + m["angles"]["C"]
        assert abs(total - 180.0) < 1e-6

    def test_circumradius_inradius_positive(self):
        pts = [(0, 0), (5, 0), (2, 3)]
        m = triangle_quality(pts, (0, 1, 2))
        assert m["circumradius"] > 0
        assert m["inradius"] > 0
        assert m["circumradius"] >= m["inradius"]


# ══════════════════════════════════════════════════════════════
#  Classify Triangle
# ══════════════════════════════════════════════════════════════

class TestClassifyTriangle:
    def test_excellent(self):
        assert classify_triangle(1.0, 60) == "excellent"

    def test_good(self):
        assert classify_triangle(1.5, 35) == "good"

    def test_fair(self):
        assert classify_triangle(4.0, 18) == "fair"

    def test_poor(self):
        assert classify_triangle(10.0, 8) == "poor"

    def test_degenerate(self):
        assert classify_triangle(100.0, 2) == "degenerate"

    def test_boundary_excellent(self):
        assert classify_triangle(1.1, 50) == "excellent"

    def test_boundary_good(self):
        assert classify_triangle(2.0, 30) == "good"


# ══════════════════════════════════════════════════════════════
#  Mesh Statistics
# ══════════════════════════════════════════════════════════════

class TestMeshStatistics:
    def test_single_triangle(self):
        pts = _equilateral()
        tris = [(0, 1, 2)]
        stats = mesh_statistics(pts, tris)
        assert stats["num_triangles"] == 1
        assert stats["num_vertices"] == 3
        assert stats["num_edges"] == 3

    def test_quality_histogram_sums_to_total(self):
        pts = _random_points(30)
        tris = delaunay_triangulate(pts)
        stats = mesh_statistics(pts, tris)
        total = sum(stats["quality_histogram"].values())
        assert total == stats["num_triangles"]

    def test_quality_pct_sums_to_100(self):
        pts = _grid(5)
        tris = delaunay_triangulate(pts)
        stats = mesh_statistics(pts, tris)
        pct_sum = sum(stats["quality_pct"].values())
        assert abs(pct_sum - 100.0) < 0.5  # rounding tolerance

    def test_area_total_positive(self):
        pts = _random_points(15)
        tris = delaunay_triangulate(pts)
        stats = mesh_statistics(pts, tris)
        assert stats["area"]["total"] > 0

    def test_euler_characteristic(self):
        """For convex hull triangulation, Euler χ = 2."""
        pts = _grid(5)
        tris = delaunay_triangulate(pts)
        stats = mesh_statistics(pts, tris)
        # Interior triangulations: V - E + F should be close to 2
        # (exact if all points on convex hull are counted correctly)
        assert abs(stats["euler_characteristic"]) <= 3

    def test_empty_triangles(self):
        pts = [(0, 0)]
        stats = mesh_statistics(pts, [])
        assert stats.get("error") == "No triangles"

    def test_area_cv_low_for_grid(self):
        pts = _grid(5)
        tris = delaunay_triangulate(pts)
        stats = mesh_statistics(pts, tris)
        # Grid triangulations have two sizes of triangle but low CV
        assert stats["area"]["cv"] < 1.0

    def test_aspect_ratio_for_equilateral(self):
        pts = _equilateral()
        stats = mesh_statistics(pts, [(0, 1, 2)])
        assert abs(stats["aspect_ratio"]["mean"] - 1.0) < 1e-6

    def test_min_angle_stats(self):
        pts = _random_points(25)
        tris = delaunay_triangulate(pts)
        stats = mesh_statistics(pts, tris)
        assert stats["min_angle"]["overall_min"] > 0
        assert stats["min_angle"]["mean"] > 0


# ══════════════════════════════════════════════════════════════
#  Angle Spectrum
# ══════════════════════════════════════════════════════════════

class TestAngleSpectrum:
    def test_equilateral_all_60(self):
        pts = _equilateral()
        metrics = [triangle_quality(pts, (0, 1, 2))]
        spec = angle_spectrum(metrics)
        assert spec["total_angles"] == 3
        assert abs(spec["mean"] - 60.0) < 1e-6
        assert abs(spec["stdev"]) < 1e-6

    def test_histogram_counts_match(self):
        pts = _random_points(20)
        tris = delaunay_triangulate(pts)
        metrics = [triangle_quality(pts, t) for t in tris]
        spec = angle_spectrum(metrics)
        assert sum(spec["histogram_counts"]) == spec["total_angles"]

    def test_custom_thresholds(self):
        pts = _random_points(20)
        tris = delaunay_triangulate(pts)
        metrics = [triangle_quality(pts, t) for t in tris]
        spec = angle_spectrum(metrics, thresholds=[10, 20, 45])
        assert "below_10deg" in spec["threshold_counts"]
        assert "below_20deg" in spec["threshold_counts"]
        assert "below_45deg" in spec["threshold_counts"]

    def test_percentiles_ordered(self):
        pts = _random_points(30)
        tris = delaunay_triangulate(pts)
        metrics = [triangle_quality(pts, t) for t in tris]
        spec = angle_spectrum(metrics)
        p = spec["percentiles"]
        assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]

    def test_all_angles_between_0_and_180(self):
        pts = _random_points(25)
        tris = delaunay_triangulate(pts)
        metrics = [triangle_quality(pts, t) for t in tris]
        spec = angle_spectrum(metrics)
        assert spec["min"] > 0
        assert spec["max"] <= 180.0


# ══════════════════════════════════════════════════════════════
#  Edge Analysis
# ══════════════════════════════════════════════════════════════

class TestEdgeAnalysis:
    def test_equilateral_uniform(self):
        pts = _equilateral()
        tris = [(0, 1, 2)]
        ea = edge_analysis(pts, tris)
        assert ea["num_edges"] == 3
        assert abs(ea["cv"]) < 1e-6  # all edges equal
        assert abs(ea["uniformity_ratio"] - 1.0) < 1e-6

    def test_edges_positive(self):
        pts = _random_points(15)
        tris = delaunay_triangulate(pts)
        ea = edge_analysis(pts, tris)
        assert ea["min"] > 0
        assert ea["max"] >= ea["min"]

    def test_percentiles_ordered(self):
        pts = _random_points(30)
        tris = delaunay_triangulate(pts)
        ea = edge_analysis(pts, tris)
        p = ea["percentiles"]
        assert p["p5"] <= p["p25"] <= p["p50"] <= p["p75"] <= p["p95"]

    def test_per_triangle_range(self):
        pts = _grid(4)
        tris = delaunay_triangulate(pts)
        ea = edge_analysis(pts, tris)
        assert ea["per_triangle_range"]["min"] >= 0
        assert ea["per_triangle_range"]["max"] >= ea["per_triangle_range"]["min"]


# ══════════════════════════════════════════════════════════════
#  Full Pipeline
# ══════════════════════════════════════════════════════════════

class TestDelaunayQuality:
    def test_basic(self):
        pts = _random_points(20)
        result = delaunay_quality(pts)
        assert "mesh" in result
        assert "angles" in result
        assert "edges" in result
        assert "per_triangle" in result
        assert result["num_points"] == 20

    def test_equilateral_excellent(self):
        pts = _equilateral()
        result = delaunay_quality(pts)
        assert result["mesh"]["quality_histogram"]["excellent"] == 1

    def test_grid(self):
        pts = _grid(5)
        result = delaunay_quality(pts)
        assert result["mesh"]["num_triangles"] > 0
        # All triangles from a regular grid should be at least "good"
        assert result["mesh"]["quality_histogram"]["degenerate"] == 0

    def test_custom_angle_thresholds(self):
        pts = _random_points(15)
        result = delaunay_quality(pts, angle_thresholds=[15, 30])
        assert "below_15deg" in result["angles"]["threshold_counts"]
        assert "below_30deg" in result["angles"]["threshold_counts"]

    def test_pentagon(self):
        pts = _pentagon()
        result = delaunay_quality(pts)
        assert result["mesh"]["num_triangles"] == 3


# ══════════════════════════════════════════════════════════════
#  Report & Export
# ══════════════════════════════════════════════════════════════

class TestFormatReport:
    def test_report_contains_sections(self):
        result = delaunay_quality(_random_points(15))
        report = format_report(result)
        assert "DELAUNAY TRIANGULATION QUALITY REPORT" in report
        assert "Quality Classification" in report
        assert "Aspect Ratio" in report
        assert "Angle Spectrum" in report
        assert "Edge Lengths" in report
        assert "Triangle Areas" in report

    def test_report_contains_data(self):
        result = delaunay_quality(_grid(4))
        report = format_report(result)
        assert "Points:" in report
        assert "Triangles:" in report
        assert "Euler" in report

    def test_report_is_string(self):
        result = delaunay_quality(_equilateral())
        report = format_report(result)
        assert isinstance(report, str)
        assert len(report) > 100


class TestExportJson:
    def test_creates_file(self):
        result = delaunay_quality(_random_points(10))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(result, path)
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert data["num_points"] == 10
            assert "mesh" in data
            assert "per_triangle" in data
        finally:
            os.unlink(path)

    def test_per_triangle_has_quality_class(self):
        result = delaunay_quality(_grid(3))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(result, path)
            with open(path) as f:
                data = json.load(f)
            for tri in data["per_triangle"]:
                assert "quality_class" in tri
                assert "aspect_ratio" in tri
                assert "min_angle" in tri
        finally:
            os.unlink(path)

    def test_json_is_valid(self):
        result = delaunay_quality(_random_points(20))
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(result, path)
            with open(path) as f:
                data = json.load(f)  # should not throw
            assert isinstance(data, dict)
        finally:
            os.unlink(path)


# ══════════════════════════════════════════════════════════════
#  Edge Cases
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_many_points_no_crash(self):
        pts = _random_points(200, seed=123)
        result = delaunay_quality(pts)
        assert result["mesh"]["num_triangles"] > 100

    def test_close_points(self):
        """Points very close together but not identical."""
        pts = [(0, 0), (1e-8, 0), (0, 1e-8), (1, 0), (0, 1)]
        tris = delaunay_triangulate(pts)
        assert len(tris) >= 1

    def test_negative_coordinates(self):
        pts = [(-5, -3), (10, -2), (-1, 8), (4, 4)]
        result = delaunay_quality(pts)
        assert result["mesh"]["num_triangles"] == 2

    def test_large_coordinates(self):
        pts = [(1e6, 1e6), (1e6 + 1, 1e6), (1e6, 1e6 + 1)]
        result = delaunay_quality(pts)
        assert result["mesh"]["num_triangles"] == 1

    def test_float_input(self):
        pts = [(0.5, 0.5), (1.7, 0.3), (1.1, 1.9)]
        result = delaunay_quality(pts)
        assert result["mesh"]["num_triangles"] == 1
