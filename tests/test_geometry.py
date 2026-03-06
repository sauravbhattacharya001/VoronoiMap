"""Pytest tests for vormap_geometry — shared geometry & statistics helpers.

Replaces the previous script-style test with proper pytest test classes
and adds comprehensive coverage for the statistics functions (mean, std,
percentile, normal_cdf) that were previously untested.
"""

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
)


# ── polygon_area ─────────────────────────────────────────────────────

class TestPolygonArea:
    """Tests for the Shoelace-formula polygon area."""

    def test_unit_square(self):
        assert polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]) == pytest.approx(1.0)

    def test_345_triangle(self):
        assert polygon_area([(0, 0), (4, 0), (0, 3)]) == pytest.approx(6.0)

    def test_reversed_winding(self):
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert polygon_area(list(reversed(square))) == pytest.approx(1.0)

    def test_empty(self):
        assert polygon_area([]) == pytest.approx(0.0)

    def test_single_point(self):
        assert polygon_area([(1, 1)]) == pytest.approx(0.0)

    def test_two_points(self):
        assert polygon_area([(0, 0), (1, 1)]) == pytest.approx(0.0)

    def test_regular_hexagon(self):
        pts = [(math.cos(math.pi / 3 * i), math.sin(math.pi / 3 * i)) for i in range(6)]
        expected = 3 * math.sqrt(3) / 2
        assert polygon_area(pts) == pytest.approx(expected, abs=1e-6)

    def test_large_rectangle(self):
        r = [(0, 0), (1000, 0), (1000, 500), (0, 500)]
        assert polygon_area(r) == pytest.approx(500000.0)

    def test_collinear_degenerate(self):
        assert polygon_area([(0, 0), (1, 1), (2, 2)]) == pytest.approx(0.0)

    def test_nonconvex_polygon(self):
        # L-shaped polygon: 2x2 square minus 1x1 corner = area 3
        pts = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
        assert polygon_area(pts) == pytest.approx(3.0)

    def test_negative_coordinates(self):
        # Square in negative quadrant
        sq = [(-2, -2), (-1, -2), (-1, -1), (-2, -1)]
        assert polygon_area(sq) == pytest.approx(1.0)


# ── polygon_perimeter ────────────────────────────────────────────────

class TestPolygonPerimeter:
    """Tests for polygon perimeter calculation."""

    def test_unit_square(self):
        assert polygon_perimeter([(0, 0), (1, 0), (1, 1), (0, 1)]) == pytest.approx(4.0)

    def test_345_triangle(self):
        assert polygon_perimeter([(0, 0), (4, 0), (0, 3)]) == pytest.approx(12.0)

    def test_empty(self):
        assert polygon_perimeter([]) == pytest.approx(0.0)

    def test_single_point(self):
        assert polygon_perimeter([(0, 0)]) == pytest.approx(0.0)

    def test_two_points(self):
        # Round trip: (0,0)->(3,4)->(0,0) = 5 + 5 = 10
        assert polygon_perimeter([(0, 0), (3, 4)]) == pytest.approx(10.0)

    def test_equilateral_triangle(self):
        s = 2.0
        pts = [(0, 0), (s, 0), (s / 2, s * math.sqrt(3) / 2)]
        assert polygon_perimeter(pts) == pytest.approx(3 * s, abs=1e-9)


# ── polygon_centroid ─────────────────────────────────────────────────

class TestPolygonCentroid:
    """Tests for polygon centroid computation."""

    def test_unit_square(self):
        cx, cy = polygon_centroid([(0, 0), (1, 0), (1, 1), (0, 1)])
        assert cx == pytest.approx(0.5)
        assert cy == pytest.approx(0.5)

    def test_345_triangle(self):
        cx, cy = polygon_centroid([(0, 0), (4, 0), (0, 3)])
        assert cx == pytest.approx(4.0 / 3.0, abs=1e-6)
        assert cy == pytest.approx(1.0, abs=1e-6)

    def test_empty(self):
        assert polygon_centroid([]) == (0.0, 0.0)

    def test_single_point(self):
        assert polygon_centroid([(3, 7)]) == (3, 7)

    def test_two_points_midpoint(self):
        cx, cy = polygon_centroid([(2, 4), (8, 10)])
        assert cx == pytest.approx(5.0)
        assert cy == pytest.approx(7.0)

    def test_collinear_fallback(self):
        cx, cy = polygon_centroid([(0, 0), (1, 1), (2, 2)])
        assert cx == pytest.approx(1.0)
        assert cy == pytest.approx(1.0)

    def test_symmetric_at_origin(self):
        sym = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        cx, cy = polygon_centroid(sym)
        assert cx == pytest.approx(0.0, abs=1e-9)
        assert cy == pytest.approx(0.0, abs=1e-9)

    def test_shifted_square(self):
        cx, cy = polygon_centroid([(10, 20), (12, 20), (12, 22), (10, 22)])
        assert cx == pytest.approx(11.0)
        assert cy == pytest.approx(21.0)


# ── isoperimetric_quotient ───────────────────────────────────────────

class TestIsoperimetricQuotient:
    """Tests for the circularity measure."""

    def test_circle(self):
        area = math.pi * 25
        perim = 2 * math.pi * 5
        assert isoperimetric_quotient(area, perim) == pytest.approx(1.0)

    def test_square(self):
        assert isoperimetric_quotient(1.0, 4.0) == pytest.approx(math.pi / 4, abs=1e-6)

    def test_zero_perimeter(self):
        assert isoperimetric_quotient(1.0, 0.0) == 0.0

    def test_thin_rectangle(self):
        iq = isoperimetric_quotient(1.0, 202.0)
        assert iq < 0.01

    def test_range_zero_to_one(self):
        iq = isoperimetric_quotient(100.0, 40.0)
        assert 0 <= iq <= 1


# ── edge_length ──────────────────────────────────────────────────────

class TestEdgeLength:
    """Tests for Euclidean distance."""

    def test_345_triangle(self):
        assert edge_length((0, 0), (3, 4)) == pytest.approx(5.0)

    def test_horizontal(self):
        assert edge_length((1, 2), (8, 2)) == pytest.approx(7.0)

    def test_vertical(self):
        assert edge_length((5, 1), (5, 4)) == pytest.approx(3.0)

    def test_zero_length(self):
        assert edge_length((1, 1), (1, 1)) == pytest.approx(0.0)

    def test_diagonal_unit(self):
        assert edge_length((0, 0), (1, 1)) == pytest.approx(math.sqrt(2))

    def test_negative_coords(self):
        assert edge_length((-3, -4), (0, 0)) == pytest.approx(5.0)

    def test_symmetry(self):
        assert edge_length((1, 2), (4, 6)) == edge_length((4, 6), (1, 2))


# ── build_data_index ─────────────────────────────────────────────────

class TestBuildDataIndex:
    """Tests for coordinate-to-index lookup."""

    def test_basic(self):
        data = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
        idx = build_data_index(data)
        assert len(idx) == 3
        assert idx[(1.0, 2.0)] == 0
        assert idx[(5.0, 6.0)] == 2

    def test_duplicates_first_wins(self):
        idx = build_data_index([(1, 1), (2, 2), (1, 1), (3, 3)])
        assert len(idx) == 3
        assert idx[(1, 1)] == 0

    def test_empty(self):
        assert len(build_data_index([])) == 0

    def test_single_point(self):
        idx = build_data_index([(42, 99)])
        assert idx[(42, 99)] == 0

    def test_many_points(self):
        data = [(float(i), float(i * 2)) for i in range(100)]
        idx = build_data_index(data)
        assert len(idx) == 100
        assert idx[(50.0, 100.0)] == 50


# ── mean ─────────────────────────────────────────────────────────────

class TestMean:
    """Tests for arithmetic mean."""

    def test_empty(self):
        assert mean([]) == 0.0

    def test_single(self):
        assert mean([7.0]) == pytest.approx(7.0)

    def test_simple(self):
        assert mean([1, 2, 3, 4, 5]) == pytest.approx(3.0)

    def test_negative(self):
        assert mean([-10, 10]) == pytest.approx(0.0)

    def test_floats(self):
        assert mean([0.1, 0.2, 0.3]) == pytest.approx(0.2, abs=1e-10)

    def test_large_values(self):
        assert mean([1e12, 1e12]) == pytest.approx(1e12)

    def test_identical(self):
        assert mean([5, 5, 5, 5]) == pytest.approx(5.0)


# ── std ──────────────────────────────────────────────────────────────

class TestStd:
    """Tests for standard deviation."""

    def test_empty(self):
        assert std([]) == 0.0

    def test_single(self):
        assert std([42]) == 0.0

    def test_identical_values(self):
        assert std([5, 5, 5, 5]) == pytest.approx(0.0)

    def test_population(self):
        # Population std of [2, 4, 4, 4, 5, 5, 7, 9]
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        expected = 2.0  # known result
        assert std(vals, population=True) == pytest.approx(expected, abs=0.01)

    def test_sample(self):
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        pop = std(vals, population=True)
        samp = std(vals, population=False)
        # Sample std should be larger
        assert samp > pop

    def test_precomputed_mean(self):
        vals = [10, 20, 30]
        m = 20.0
        result_with = std(vals, mean_val=m)
        result_without = std(vals)
        assert result_with == pytest.approx(result_without)

    def test_two_values(self):
        # Population std of [0, 10] = 5.0
        assert std([0, 10], population=True) == pytest.approx(5.0)

    def test_symmetric(self):
        # Symmetric around mean → known std
        vals = [-1, 1]
        assert std(vals, population=True) == pytest.approx(1.0)


# ── percentile ───────────────────────────────────────────────────────

class TestPercentile:
    """Tests for percentile with linear interpolation."""

    def test_empty(self):
        assert percentile([], 50) == 0.0

    def test_single(self):
        assert percentile([42], 0) == pytest.approx(42)
        assert percentile([42], 50) == pytest.approx(42)
        assert percentile([42], 100) == pytest.approx(42)

    def test_median_odd(self):
        assert percentile([1, 2, 3, 4, 5], 50) == pytest.approx(3.0)

    def test_median_even(self):
        # Linear interpolation between 2 and 3
        assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)

    def test_percentile_0(self):
        assert percentile([10, 20, 30], 0) == pytest.approx(10)

    def test_percentile_100(self):
        assert percentile([10, 20, 30], 100) == pytest.approx(30)

    def test_percentile_25(self):
        vals = sorted([1, 2, 3, 4, 5, 6, 7, 8])
        p25 = percentile(vals, 25)
        assert 2 <= p25 <= 3

    def test_percentile_75(self):
        vals = sorted([1, 2, 3, 4, 5, 6, 7, 8])
        p75 = percentile(vals, 75)
        assert 6 <= p75 <= 7

    def test_monotonic(self):
        vals = sorted(range(100))
        p10 = percentile(vals, 10)
        p50 = percentile(vals, 50)
        p90 = percentile(vals, 90)
        assert p10 < p50 < p90


# ── normal_cdf ───────────────────────────────────────────────────────

class TestNormalCdf:
    """Tests for the standard normal CDF approximation."""

    def test_zero(self):
        assert normal_cdf(0) == pytest.approx(0.5, abs=1e-6)

    def test_large_positive(self):
        # Φ(5) ≈ 1.0
        assert normal_cdf(5) == pytest.approx(1.0, abs=1e-4)

    def test_large_negative(self):
        # Φ(-5) ≈ 0.0
        assert normal_cdf(-5) == pytest.approx(0.0, abs=1e-4)

    def test_symmetry(self):
        # Φ(x) + Φ(-x) = 1
        for x in [0.5, 1.0, 1.5, 2.0, 3.0]:
            assert normal_cdf(x) + normal_cdf(-x) == pytest.approx(1.0, abs=1e-7)

    def test_known_values(self):
        # Standard normal CDF table values
        assert normal_cdf(1.0) == pytest.approx(0.8413, abs=1e-3)
        assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-2)
        assert normal_cdf(-1.0) == pytest.approx(0.1587, abs=1e-3)

    def test_monotonic(self):
        vals = [normal_cdf(x) for x in [-3, -2, -1, 0, 1, 2, 3]]
        for i in range(len(vals) - 1):
            assert vals[i] < vals[i + 1]

    def test_range_zero_to_one(self):
        for x in [-10, -3, -1, 0, 1, 3, 10]:
            v = normal_cdf(x)
            assert 0 <= v <= 1

    def test_one_sigma(self):
        # ~68% within 1 sigma
        coverage = normal_cdf(1) - normal_cdf(-1)
        assert coverage == pytest.approx(0.6827, abs=1e-2)

    def test_two_sigma(self):
        # ~95% within 2 sigma
        coverage = normal_cdf(2) - normal_cdf(-2)
        assert coverage == pytest.approx(0.9545, abs=1e-2)
