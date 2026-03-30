"""Tests for vormap_geometry — shared geometry and statistics helpers."""

import math
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_geometry as geo


class TestPolygonPerimeter:
    def test_triangle(self):
        verts = [(0, 0), (3, 0), (0, 4)]
        # 3 + 4 + 5 = 12
        assert geo.polygon_perimeter(verts) == pytest.approx(12.0)

    def test_square(self):
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert geo.polygon_perimeter(verts) == pytest.approx(4.0)

    def test_degenerate(self):
        assert geo.polygon_perimeter([]) == 0.0
        assert geo.polygon_perimeter([(1, 1)]) == 0.0

    def test_two_points(self):
        # Two points form a degenerate polygon; perimeter = 2 * distance
        verts = [(0, 0), (5, 0)]
        assert geo.polygon_perimeter(verts) == pytest.approx(10.0)


class TestIsoperimetricQuotient:
    def test_circle_approx(self):
        # A regular polygon with many sides approximates a circle (IQ ≈ 1)
        n = 100
        r = 1.0
        verts = [(r * math.cos(2 * math.pi * i / n),
                  r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        area = geo.polygon_area(verts)
        peri = geo.polygon_perimeter(verts)
        iq = geo.isoperimetric_quotient(area, peri)
        assert iq == pytest.approx(1.0, abs=0.01)

    def test_zero_perimeter(self):
        assert geo.isoperimetric_quotient(1.0, 0.0) == 0.0

    def test_square(self):
        # Square: area=1, perimeter=4, IQ = 4π/16 ≈ 0.785
        iq = geo.isoperimetric_quotient(1.0, 4.0)
        assert iq == pytest.approx(math.pi / 4, abs=0.001)


class TestEdgeLength:
    def test_horizontal(self):
        assert geo.edge_length((0, 0), (5, 0)) == pytest.approx(5.0)

    def test_diagonal(self):
        assert geo.edge_length((1, 1), (4, 5)) == pytest.approx(5.0)

    def test_same_point(self):
        assert geo.edge_length((3, 7), (3, 7)) == 0.0


class TestBuildDataIndex:
    def test_basic(self):
        data = [(1, 2), (3, 4), (5, 6)]
        idx = geo.build_data_index(data)
        assert idx[(1, 2)] == 0
        assert idx[(3, 4)] == 1
        assert idx[(5, 6)] == 2

    def test_duplicates_first_wins(self):
        data = [(1, 2), (3, 4), (1, 2)]
        idx = geo.build_data_index(data)
        assert idx[(1, 2)] == 0  # first occurrence

    def test_empty(self):
        assert geo.build_data_index([]) == {}


class TestMean:
    def test_basic(self):
        assert geo.mean([1, 2, 3, 4, 5]) == pytest.approx(3.0)

    def test_empty(self):
        assert geo.mean([]) == 0.0

    def test_single(self):
        assert geo.mean([42]) == 42.0

    def test_negative(self):
        assert geo.mean([-2, 2]) == pytest.approx(0.0)


class TestStd:
    def test_uniform(self):
        assert geo.std([5, 5, 5]) == 0.0

    def test_population(self):
        # std of [1, 3] population: sqrt(((1-2)^2 + (3-2)^2)/2) = 1
        assert geo.std([1, 3], population=True) == pytest.approx(1.0)

    def test_sample(self):
        # sample std of [1, 3]: sqrt(((1-2)^2 + (3-2)^2)/1) = sqrt(2)
        assert geo.std([1, 3], population=False) == pytest.approx(math.sqrt(2))

    def test_single_value(self):
        assert geo.std([7]) == 0.0

    def test_precomputed_mean(self):
        vals = [2, 4, 6]
        assert geo.std(vals, mean_val=4.0) == pytest.approx(
            geo.std(vals))


class TestMedian:
    def test_odd(self):
        assert geo.median([3, 1, 2]) == 2.0

    def test_even(self):
        assert geo.median([1, 2, 3, 4]) == 2.5

    def test_empty(self):
        assert geo.median([]) == 0.0

    def test_single(self):
        assert geo.median([99]) == 99.0


class TestCrossProduct2D:
    def test_ccw(self):
        # Counter-clockwise: positive
        assert geo.cross_product_2d((0, 0), (1, 0), (0, 1)) > 0

    def test_cw(self):
        # Clockwise: negative
        assert geo.cross_product_2d((0, 0), (0, 1), (1, 0)) < 0

    def test_collinear(self):
        assert geo.cross_product_2d((0, 0), (1, 1), (2, 2)) == pytest.approx(0.0)


class TestPercentile:
    def test_median(self):
        assert geo.percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_min(self):
        assert geo.percentile([10, 20, 30], 0) == 10.0

    def test_max(self):
        assert geo.percentile([10, 20, 30], 100) == 30.0

    def test_interpolation(self):
        # 25th percentile of [1, 2, 3, 4, 5]: index = 1.0 → value = 2.0
        assert geo.percentile([1, 2, 3, 4, 5], 25) == pytest.approx(2.0)

    def test_empty(self):
        assert geo.percentile([], 50) == 0.0


class TestNormalCDF:
    def test_zero(self):
        assert geo.normal_cdf(0) == pytest.approx(0.5, abs=1e-5)

    def test_positive(self):
        # Φ(1) ≈ 0.8413
        assert geo.normal_cdf(1) == pytest.approx(0.8413, abs=1e-3)

    def test_negative(self):
        # Φ(-1) ≈ 0.1587
        assert geo.normal_cdf(-1) == pytest.approx(0.1587, abs=1e-3)

    def test_large(self):
        assert geo.normal_cdf(5) == pytest.approx(1.0, abs=1e-5)

    def test_symmetry(self):
        assert geo.normal_cdf(2) + geo.normal_cdf(-2) == pytest.approx(1.0, abs=1e-6)


class TestSVGCoordinateTransform:
    def test_uniform_center(self):
        t = geo.SVGCoordinateTransform((0, 10), (0, 10), 200, 200, margin=0)
        # Center point should map to center
        assert t.tx(5) == pytest.approx(100.0)
        assert t.ty(5) == pytest.approx(100.0)

    def test_corners_uniform(self):
        t = geo.SVGCoordinateTransform((0, 100), (0, 100), 200, 200, margin=0)
        assert t.tx(0) == pytest.approx(0.0)
        assert t.tx(100) == pytest.approx(200.0)
        assert t.ty(100) == pytest.approx(0.0)  # Y flipped
        assert t.ty(0) == pytest.approx(200.0)

    def test_stretch_mode(self):
        t = geo.SVGCoordinateTransform(
            (0, 100), (0, 50), 400, 200, margin=0, mode="stretch")
        assert t.tx(50) == pytest.approx(200.0)
        assert t.ty(25) == pytest.approx(100.0)

    def test_from_points(self):
        pts = [(0, 0), (10, 10)]
        t = geo.SVGCoordinateTransform.from_points(pts, 200, 200)
        # Should not raise
        assert t.tx(5) is not None

    def test_from_points_empty_raises(self):
        with pytest.raises(ValueError):
            geo.SVGCoordinateTransform.from_points([], 200, 200)

    def test_scale_property(self):
        t = geo.SVGCoordinateTransform((0, 10), (0, 10), 200, 200, margin=0)
        assert t.scale > 0
