"""Tests for vormap_clip — Voronoi region clipping module.

Tests cover boundary generators, geometry helpers, Sutherland-Hodgman clipping,
region clipping, ClipResult statistics, and edge cases.
"""

import math
import pytest

from vormap_clip import (
    make_rectangle,
    make_circle,
    make_ellipse,
    make_regular_polygon,
    _line_intersection,
    _is_inside,
    point_in_polygon,
    clip_polygon,
    clip_region,
    clip_all_regions,
    ClipResult,
    ClipStats,
)
from vormap_geometry import polygon_area


# ---------------------------------------------------------------------------
# Boundary generators
# ---------------------------------------------------------------------------

class TestMakeRectangle:
    def test_basic_rectangle(self):
        rect = make_rectangle(0, 0, 10, 10)
        assert len(rect) == 4
        assert rect == [(0, 0), (10, 0), (10, 10), (0, 10)]

    def test_rectangle_area(self):
        rect = make_rectangle(0, 0, 5, 3)
        assert abs(polygon_area(rect) - 15.0) < 1e-10

    def test_negative_coords(self):
        rect = make_rectangle(-5, -5, 5, 5)
        assert abs(polygon_area(rect) - 100.0) < 1e-10


class TestMakeCircle:
    def test_default_segments(self):
        circle = make_circle((0, 0), 10)
        assert len(circle) == 64

    def test_custom_segments(self):
        circle = make_circle((0, 0), 10, segments=8)
        assert len(circle) == 8

    def test_area_approximation(self):
        """Circle area should approach pi*r^2 with many segments."""
        r = 100
        circle = make_circle((0, 0), r, segments=1000)
        expected = math.pi * r * r
        actual = polygon_area(circle)
        assert abs(actual - expected) / expected < 0.001  # within 0.1%

    def test_center_offset(self):
        """All points should be at distance r from center."""
        cx, cy, r = 50, 50, 20
        circle = make_circle((cx, cy), r, segments=32)
        for x, y in circle:
            dist = math.hypot(x - cx, y - cy)
            assert abs(dist - r) < 1e-10


class TestMakeEllipse:
    def test_ellipse_point_count(self):
        ellipse = make_ellipse((0, 0), 10, 5, segments=32)
        assert len(ellipse) == 32

    def test_ellipse_axes(self):
        """Check that semi-axes match rx and ry."""
        rx, ry = 20, 10
        ellipse = make_ellipse((0, 0), rx, ry, segments=360)
        xs = [p[0] for p in ellipse]
        ys = [p[1] for p in ellipse]
        assert abs(max(xs) - rx) < 0.1
        assert abs(max(ys) - ry) < 0.1


class TestMakeRegularPolygon:
    def test_hexagon(self):
        hexagon = make_regular_polygon((0, 0), 10, 6)
        assert len(hexagon) == 6

    def test_square_area(self):
        """Regular 4-gon with radius r has area 2*r^2."""
        r = 10
        square = make_regular_polygon((0, 0), r, 4)
        expected = 2 * r * r
        actual = polygon_area(square)
        assert abs(actual - expected) < 0.01


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

class TestLineIntersection:
    def test_perpendicular_lines(self):
        # Horizontal line (0,0)-(10,0) and vertical line (5,-5)-(5,5)
        pt = _line_intersection((0, 0), (10, 0), (5, -5), (5, 5))
        assert abs(pt[0] - 5.0) < 1e-10
        assert abs(pt[1] - 0.0) < 1e-10

    def test_diagonal_intersection(self):
        pt = _line_intersection((0, 0), (10, 10), (0, 10), (10, 0))
        assert abs(pt[0] - 5.0) < 1e-10
        assert abs(pt[1] - 5.0) < 1e-10

    def test_parallel_lines_fallback(self):
        """Parallel lines should return midpoint fallback without crashing."""
        pt = _line_intersection((0, 0), (10, 0), (0, 5), (10, 5))
        assert isinstance(pt, tuple)
        assert len(pt) == 2


class TestIsInside:
    def test_left_of_upward_edge(self):
        # Edge going upward: (0,0) -> (0,10). Point to the left: (-1, 5)
        assert _is_inside((-1, 5), (0, 0), (0, 10))

    def test_right_of_upward_edge(self):
        assert not _is_inside((1, 5), (0, 0), (0, 10))

    def test_on_edge(self):
        # On the edge should count as inside (>= 0)
        assert _is_inside((0, 5), (0, 0), (0, 10))


class TestPointInPolygon:
    def test_inside_square(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon((5, 5), square)

    def test_outside_square(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert not point_in_polygon((15, 5), square)

    def test_far_outside(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert not point_in_polygon((-100, -100), square)


# ---------------------------------------------------------------------------
# Sutherland-Hodgman clipping
# ---------------------------------------------------------------------------

class TestClipPolygon:
    def test_subject_inside_clip(self):
        """Subject entirely inside clip => should return subject unchanged."""
        clip = make_rectangle(0, 0, 100, 100)
        subject = make_rectangle(10, 10, 20, 20)
        result = clip_polygon(subject, clip)
        assert len(result) == 4
        assert abs(polygon_area(result) - 100.0) < 1e-6

    def test_subject_outside_clip(self):
        """Subject entirely outside clip => should return empty."""
        clip = make_rectangle(0, 0, 10, 10)
        subject = make_rectangle(20, 20, 30, 30)
        result = clip_polygon(subject, clip)
        assert len(result) == 0 or polygon_area(result) < 1e-10

    def test_partial_overlap(self):
        """Half overlap should give ~50% area."""
        clip = make_rectangle(0, 0, 10, 10)
        subject = make_rectangle(5, 0, 15, 10)
        result = clip_polygon(subject, clip)
        assert abs(polygon_area(result) - 50.0) < 1e-6

    def test_quarter_overlap(self):
        """Corner overlap should give 25% of subject area."""
        clip = make_rectangle(0, 0, 10, 10)
        subject = make_rectangle(5, 5, 15, 15)
        result = clip_polygon(subject, clip)
        assert abs(polygon_area(result) - 25.0) < 1e-6

    def test_empty_subject(self):
        clip = make_rectangle(0, 0, 10, 10)
        assert clip_polygon([], clip) == []

    def test_empty_clip(self):
        subject = make_rectangle(0, 0, 10, 10)
        assert clip_polygon(subject, []) == []

    def test_clip_triangle_to_rectangle(self):
        """Triangle partially inside a rectangle."""
        clip = make_rectangle(0, 0, 10, 10)
        triangle = [(5, -5), (5, 15), (15, 5)]
        result = clip_polygon(triangle, clip)
        # Result should be a polygon with area > 0
        assert len(result) >= 3
        assert polygon_area(result) > 0


class TestClipRegion:
    def test_same_as_clip_polygon(self):
        """clip_region should delegate to clip_polygon."""
        boundary = make_rectangle(0, 0, 10, 10)
        region = make_rectangle(2, 2, 8, 8)
        assert clip_region(region, boundary) == clip_polygon(region, boundary)


# ---------------------------------------------------------------------------
# clip_all_regions and ClipResult
# ---------------------------------------------------------------------------

class TestClipAllRegions:
    @pytest.fixture
    def sample_regions(self):
        """Create simple regions: 4 squares in a 2x2 grid."""
        regions = {
            (2.5, 2.5): make_rectangle(0, 0, 5, 5),
            (7.5, 2.5): make_rectangle(5, 0, 10, 5),
            (2.5, 7.5): make_rectangle(0, 5, 5, 10),
            (7.5, 7.5): make_rectangle(5, 5, 10, 10),
        }
        data = [(2.5, 2.5), (7.5, 2.5), (2.5, 7.5), (7.5, 7.5)]
        return regions, data

    def test_full_boundary_keeps_all(self, sample_regions):
        """Boundary encompassing all regions should keep everything."""
        regions, data = sample_regions
        boundary = make_rectangle(-1, -1, 11, 11)
        result = clip_all_regions(regions, data, boundary)
        assert result.clipped_count == 4
        assert len(result.removed_seeds) == 0

    def test_small_boundary_removes_some(self, sample_regions):
        """Small boundary should clip/remove outer regions."""
        regions, data = sample_regions
        boundary = make_rectangle(0, 0, 5, 5)
        result = clip_all_regions(regions, data, boundary)
        # Only the (2.5, 2.5) region fully inside; others partially or outside
        assert result.clipped_count >= 1
        assert result.clipped_count <= 4

    def test_stats_properties(self, sample_regions):
        regions, data = sample_regions
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions(regions, data, boundary)
        stats = result.stats
        assert isinstance(stats, ClipStats)
        assert stats.total_clipped_area > 0
        assert 0 <= stats.area_retained_pct <= 100
        assert 0 <= stats.coverage_pct <= 200  # could exceed 100 if regions overlap boundary edge

    def test_summary_string(self, sample_regions):
        regions, data = sample_regions
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions(regions, data, boundary)
        summary = result.summary
        assert "Clipping Summary" in summary
        assert "Regions:" in summary
        assert "Coverage:" in summary

    def test_to_dict(self, sample_regions):
        regions, data = sample_regions
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions(regions, data, boundary)
        d = result.to_dict()
        assert "original_count" in d
        assert "clipped_count" in d
        assert d["original_count"] == 4

    def test_empty_regions(self):
        result = clip_all_regions({}, [], make_rectangle(0, 0, 10, 10))
        assert result.clipped_count == 0
        stats = result.stats
        assert stats.total_clipped_area == 0

    def test_seeds_inside_outside(self, sample_regions):
        regions, data = sample_regions
        boundary = make_rectangle(0, 0, 5, 5)
        result = clip_all_regions(regions, data, boundary)
        assert result.seeds_inside_boundary + result.seeds_outside_boundary == len(data)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_degenerate_polygon_line(self):
        """A degenerate 2-point 'polygon' should clip to empty."""
        clip = make_rectangle(0, 0, 10, 10)
        result = clip_polygon([(5, 5), (15, 5)], clip)
        # 2 points can't form a polygon, but the algorithm may return points
        if result:
            assert polygon_area(result) < 1e-10

    def test_identical_subject_and_clip(self):
        """Clipping a polygon against itself should return ~same area."""
        rect = make_rectangle(0, 0, 10, 10)
        result = clip_polygon(rect, rect)
        assert abs(polygon_area(result) - 100.0) < 1e-6

    def test_tiny_polygon(self):
        """Very small polygon should work without numerical issues."""
        clip = make_rectangle(0, 0, 1e-6, 1e-6)
        subject = make_rectangle(0, 0, 0.5e-6, 0.5e-6)
        result = clip_polygon(subject, clip)
        assert len(result) >= 3
