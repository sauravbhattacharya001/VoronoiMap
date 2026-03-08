"""Tests for vormap_geometry — core geometry and statistics helpers."""

import math
import pytest
from vormap_geometry import (
    polygon_area,
    polygon_perimeter,
    polygon_centroid,
    isoperimetric_quotient,
    edge_length,
    build_data_index,
    mean,
    std,
    percentile,
    normal_cdf,
    SVGCoordinateTransform,
)


# ── polygon_area ─────────────────────────────────────────────────────


class TestPolygonArea:
    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert polygon_area(sq) == pytest.approx(1.0)

    def test_rectangle(self):
        rect = [(0, 0), (4, 0), (4, 3), (0, 3)]
        assert polygon_area(rect) == pytest.approx(12.0)

    def test_triangle(self):
        tri = [(0, 0), (6, 0), (3, 4)]
        assert polygon_area(tri) == pytest.approx(12.0)

    def test_reversed_winding_same_area(self):
        sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
        assert polygon_area(sq) == pytest.approx(1.0)

    def test_degenerate_two_points(self):
        assert polygon_area([(0, 0), (1, 1)]) == 0.0

    def test_degenerate_one_point(self):
        assert polygon_area([(5, 5)]) == 0.0

    def test_empty(self):
        assert polygon_area([]) == 0.0

    def test_collinear_points(self):
        assert polygon_area([(0, 0), (1, 1), (2, 2)]) == pytest.approx(0.0)


# ── polygon_perimeter ────────────────────────────────────────────────


class TestPolygonPerimeter:
    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert polygon_perimeter(sq) == pytest.approx(4.0)

    def test_single_point(self):
        assert polygon_perimeter([(0, 0)]) == 0.0

    def test_empty(self):
        assert polygon_perimeter([]) == 0.0

    def test_two_points(self):
        # Two points form a degenerate "polygon" — perimeter = 2 * distance
        p = polygon_perimeter([(0, 0), (3, 4)])
        assert p == pytest.approx(10.0)

    def test_equilateral_triangle(self):
        s = 2.0
        h = s * math.sqrt(3) / 2
        tri = [(0, 0), (s, 0), (s / 2, h)]
        assert polygon_perimeter(tri) == pytest.approx(6.0)


# ── polygon_centroid ─────────────────────────────────────────────────


class TestPolygonCentroid:
    def test_unit_square(self):
        sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
        cx, cy = polygon_centroid(sq)
        assert cx == pytest.approx(1.0)
        assert cy == pytest.approx(1.0)

    def test_single_point(self):
        assert polygon_centroid([(3, 7)]) == (3, 7)

    def test_empty(self):
        assert polygon_centroid([]) == (0.0, 0.0)

    def test_two_points_midpoint(self):
        cx, cy = polygon_centroid([(0, 0), (4, 6)])
        assert cx == pytest.approx(2.0)
        assert cy == pytest.approx(3.0)

    def test_collinear_fallback(self):
        # Collinear points have zero signed area → falls back to arithmetic mean
        cx, cy = polygon_centroid([(0, 0), (2, 2), (4, 4)])
        assert cx == pytest.approx(2.0)
        assert cy == pytest.approx(2.0)


# ── isoperimetric_quotient ───────────────────────────────────────────


class TestIsoperimetricQuotient:
    def test_circle(self):
        r = 5.0
        area = math.pi * r * r
        perimeter = 2 * math.pi * r
        assert isoperimetric_quotient(area, perimeter) == pytest.approx(1.0)

    def test_square(self):
        # IQ of a square = pi/4 ≈ 0.7854
        assert isoperimetric_quotient(1.0, 4.0) == pytest.approx(math.pi / 4)

    def test_zero_perimeter(self):
        assert isoperimetric_quotient(10.0, 0.0) == 0.0


# ── edge_length ──────────────────────────────────────────────────────


class TestEdgeLength:
    def test_3_4_5(self):
        assert edge_length((0, 0), (3, 4)) == pytest.approx(5.0)

    def test_zero(self):
        assert edge_length((1, 1), (1, 1)) == 0.0


# ── build_data_index ─────────────────────────────────────────────────


class TestBuildDataIndex:
    def test_basic(self):
        pts = [(1, 2), (3, 4), (5, 6)]
        idx = build_data_index(pts)
        assert idx[(1, 2)] == 0
        assert idx[(3, 4)] == 1
        assert idx[(5, 6)] == 2

    def test_duplicates_keep_first(self):
        pts = [(1, 1), (2, 2), (1, 1)]
        idx = build_data_index(pts)
        assert idx[(1, 1)] == 0  # first occurrence

    def test_empty(self):
        assert build_data_index([]) == {}


# ── Statistics helpers ───────────────────────────────────────────────


class TestMean:
    def test_basic(self):
        assert mean([1, 2, 3, 4, 5]) == pytest.approx(3.0)

    def test_empty(self):
        assert mean([]) == 0.0

    def test_single(self):
        assert mean([42]) == pytest.approx(42.0)


class TestStd:
    def test_population_std(self):
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        assert std(vals, population=True) == pytest.approx(2.0)

    def test_sample_std(self):
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        expected = math.sqrt(sum((v - 5) ** 2 for v in vals) / 7)
        assert std(vals, population=False) == pytest.approx(expected)

    def test_single_value(self):
        assert std([5]) == 0.0

    def test_precomputed_mean(self):
        vals = [10, 20, 30]
        m = mean(vals)
        assert std(vals, mean_val=m) == std(vals)


class TestPercentile:
    def test_median(self):
        vals = [1, 2, 3, 4, 5]
        assert percentile(vals, 50) == pytest.approx(3.0)

    def test_min_max(self):
        vals = [10, 20, 30]
        assert percentile(vals, 0) == pytest.approx(10.0)
        assert percentile(vals, 100) == pytest.approx(30.0)

    def test_empty(self):
        assert percentile([], 50) == 0.0

    def test_interpolation(self):
        vals = [0, 10]
        assert percentile(vals, 25) == pytest.approx(2.5)


class TestNormalCdf:
    def test_zero(self):
        assert normal_cdf(0) == pytest.approx(0.5, abs=1e-6)

    def test_positive(self):
        # Φ(1.96) ≈ 0.975
        assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)

    def test_negative_symmetry(self):
        assert normal_cdf(-1.0) == pytest.approx(1.0 - normal_cdf(1.0), abs=1e-7)

    def test_large_value(self):
        assert normal_cdf(5.0) == pytest.approx(1.0, abs=1e-5)


# ── SVGCoordinateTransform ──────────────────────────────────────────


class TestSVGCoordinateTransform:
    def test_uniform_corners(self):
        t = SVGCoordinateTransform((0, 100), (0, 100), 200, 200, margin=0)
        # min x → 0, max x → 200 (no margin, scale=2)
        assert t.tx(0) == pytest.approx(0.0)
        assert t.tx(100) == pytest.approx(200.0)
        # Y is flipped: max y → 0 (top), min y → 200 (bottom)
        assert t.ty(100) == pytest.approx(0.0)
        assert t.ty(0) == pytest.approx(200.0)

    def test_stretch_mode(self):
        t = SVGCoordinateTransform((0, 10), (0, 5), 200, 100, margin=0, mode="stretch")
        # Independent scaling: x: 200/10=20, y: 100/5=20
        assert t.tx(5) == pytest.approx(100.0)
        assert t.ty(5) == pytest.approx(0.0)  # max y → top

    def test_from_points(self):
        pts = [(0, 0), (10, 10)]
        t = SVGCoordinateTransform.from_points(pts, 200, 200, margin=0)
        assert t.tx(0) == pytest.approx(0.0)
        assert t.tx(10) == pytest.approx(200.0)

    def test_from_points_empty_raises(self):
        with pytest.raises(ValueError):
            SVGCoordinateTransform.from_points([], 200, 200)

    def test_scale_property(self):
        t = SVGCoordinateTransform((0, 100), (0, 100), 300, 300, margin=0)
        assert t.scale == pytest.approx(3.0)

    def test_margin_offsets(self):
        t = SVGCoordinateTransform((0, 100), (0, 100), 280, 280, margin=40)
        # drawable = 200, scale = 2
        assert t.tx(0) == pytest.approx(40.0)
        assert t.tx(100) == pytest.approx(240.0)
