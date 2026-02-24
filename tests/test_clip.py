"""Tests for vormap_clip — Voronoi region clipping."""

import json
import math
import os
import pytest

from vormap_clip import (
    clip_polygon,
    clip_region,
    clip_all_regions,
    make_rectangle,
    make_circle,
    make_ellipse,
    make_regular_polygon,
    point_in_polygon,
    _polygon_area,
    _line_intersection,
    _is_inside,
    ClipResult,
    ClipStats,
    load_boundary,
    export_clip_json,
    export_clip_svg,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def unit_square():
    """Unit square from (0,0) to (1,1) CCW."""
    return [(0, 0), (1, 0), (1, 1), (0, 1)]


@pytest.fixture
def big_square():
    """Square from (0,0) to (10,10) CCW."""
    return [(0, 0), (10, 0), (10, 10), (0, 10)]


@pytest.fixture
def small_triangle():
    return [(2, 2), (4, 2), (3, 4)]


@pytest.fixture
def sample_regions():
    """Four simple rectangular regions arranged in a 2x2 grid."""
    return {
        (2.5, 2.5): [(0, 0), (5, 0), (5, 5), (0, 5)],
        (7.5, 2.5): [(5, 0), (10, 0), (10, 5), (5, 5)],
        (2.5, 7.5): [(0, 5), (5, 5), (5, 10), (0, 10)],
        (7.5, 7.5): [(5, 5), (10, 5), (10, 10), (5, 10)],
    }


@pytest.fixture
def sample_data():
    return [[2.5, 2.5], [7.5, 2.5], [2.5, 7.5], [7.5, 7.5]]


# ── Boundary generators (10 tests) ───────────────────────────────────────

class TestMakeRectangle:
    def test_make_rectangle_vertices(self):
        rect = make_rectangle(1, 2, 5, 6)
        assert len(rect) == 4
        assert rect[0] == (1, 2)
        assert rect[1] == (5, 2)
        assert rect[2] == (5, 6)
        assert rect[3] == (1, 6)

    def test_make_rectangle_area(self):
        rect = make_rectangle(0, 0, 4, 3)
        assert _polygon_area(rect) == pytest.approx(12.0)


class TestMakeCircle:
    def test_make_circle_vertex_count_default(self):
        c = make_circle((0, 0), 10)
        assert len(c) == 64

    def test_make_circle_vertex_count_custom(self):
        c = make_circle((0, 0), 10, segments=32)
        assert len(c) == 32

    def test_make_circle_radius(self):
        cx, cy, r = 5, 5, 10
        c = make_circle((cx, cy), r, segments=100)
        for x, y in c:
            dist = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            assert dist == pytest.approx(r, abs=1e-10)

    def test_make_circle_area_approximation(self):
        r = 100
        c = make_circle((0, 0), r, segments=1000)
        expected = math.pi * r * r
        assert _polygon_area(c) == pytest.approx(expected, rel=1e-4)


class TestMakeEllipse:
    def test_make_ellipse_vertex_count(self):
        e = make_ellipse((0, 0), 5, 3, segments=48)
        assert len(e) == 48

    def test_make_ellipse_axes(self):
        rx, ry = 10, 5
        e = make_ellipse((0, 0), rx, ry, segments=256)
        max_x = max(abs(p[0]) for p in e)
        max_y = max(abs(p[1]) for p in e)
        assert max_x == pytest.approx(rx, abs=0.1)
        assert max_y == pytest.approx(ry, abs=0.1)


class TestMakeRegularPolygon:
    def test_make_regular_polygon_hexagon(self):
        h = make_regular_polygon((0, 0), 10, 6)
        assert len(h) == 6

    def test_make_regular_polygon_square(self):
        s = make_regular_polygon((0, 0), 10, 4, rotation=math.pi / 4)
        area = _polygon_area(s)
        # side = r*sqrt(2), area = side^2 = 2*r^2 = 200
        assert area == pytest.approx(200.0, rel=1e-6)

    def test_make_regular_polygon_rotation(self):
        p1 = make_regular_polygon((0, 0), 10, 6, rotation=0)
        p2 = make_regular_polygon((0, 0), 10, 6, rotation=math.pi / 6)
        # First vertex should differ
        assert p1[0] != pytest.approx(p2[0], abs=0.01)


# ── Point-in-polygon (6 tests) ───────────────────────────────────────────

class TestPointInPolygon:
    def test_point_inside_rectangle(self, unit_square):
        assert point_in_polygon((0.5, 0.5), unit_square) is True

    def test_point_outside_rectangle(self, unit_square):
        assert point_in_polygon((2.0, 2.0), unit_square) is False

    def test_point_on_edge(self, unit_square):
        # Boundary behaviour varies; just ensure it doesn't crash
        result = point_in_polygon((0.5, 0.0), unit_square)
        assert isinstance(result, bool)

    def test_point_inside_circle(self):
        c = make_circle((0, 0), 10, segments=64)
        assert point_in_polygon((0, 0), c) is True

    def test_point_outside_circle(self):
        c = make_circle((0, 0), 10, segments=64)
        assert point_in_polygon((20, 20), c) is False

    def test_point_in_complex_polygon(self):
        # L-shaped polygon
        poly = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
        assert point_in_polygon((0.5, 0.5), poly) is True
        assert point_in_polygon((1.5, 1.5), poly) is False


# ── Polygon area (4 tests) ───────────────────────────────────────────────

class TestPolygonArea:
    def test_area_unit_square(self, unit_square):
        assert _polygon_area(unit_square) == pytest.approx(1.0)

    def test_area_triangle(self):
        tri = [(0, 0), (4, 0), (0, 3)]
        assert _polygon_area(tri) == pytest.approx(6.0)

    def test_area_degenerate(self):
        line = [(0, 0), (1, 1)]
        assert _polygon_area(line) == 0.0

    def test_area_empty(self):
        assert _polygon_area([]) == 0.0
        assert _polygon_area([(0, 0)]) == 0.0


# ── Line intersection (3 tests) ──────────────────────────────────────────

class TestLineIntersection:
    def test_perpendicular_lines(self):
        # Horizontal and vertical crossing at (0.5, 0.5)
        pt = _line_intersection((0, 0.5), (1, 0.5), (0.5, 0), (0.5, 1))
        assert pt[0] == pytest.approx(0.5)
        assert pt[1] == pytest.approx(0.5)

    def test_parallel_lines_fallback(self):
        # Parallel horizontal lines — should return midpoint fallback
        pt = _line_intersection((0, 0), (1, 0), (0, 1), (1, 1))
        assert isinstance(pt, tuple)
        assert len(pt) == 2

    def test_diagonal_lines(self):
        pt = _line_intersection((0, 0), (2, 2), (0, 2), (2, 0))
        assert pt[0] == pytest.approx(1.0)
        assert pt[1] == pytest.approx(1.0)


# ── _is_inside (3 tests) ─────────────────────────────────────────────────

class TestIsInside:
    def test_point_left_of_edge(self):
        # Edge from (0,0) to (1,0) — point above is "inside" (left)
        assert _is_inside((0.5, 1), (0, 0), (1, 0)) is True

    def test_point_right_of_edge(self):
        assert _is_inside((0.5, -1), (0, 0), (1, 0)) is False

    def test_point_on_edge_is_inside(self):
        # On the edge itself — >= 0 so should be True
        assert _is_inside((0.5, 0), (0, 0), (1, 0)) is True


# ── clip_polygon — Sutherland-Hodgman (10 tests) ─────────────────────────

class TestClipPolygon:
    def test_clip_fully_inside(self, unit_square, big_square):
        result = clip_polygon(unit_square, big_square)
        assert len(result) == 4
        assert _polygon_area(result) == pytest.approx(1.0)

    def test_clip_fully_outside(self, unit_square):
        far_rect = make_rectangle(100, 100, 200, 200)
        result = clip_polygon(unit_square, far_rect)
        assert result == [] or _polygon_area(result) < 1e-10

    def test_clip_partial_overlap(self, big_square):
        # Half-overlapping rectangle
        half = make_rectangle(5, 0, 15, 10)
        result = clip_polygon(half, big_square)
        assert len(result) >= 3
        assert _polygon_area(result) == pytest.approx(50.0)

    def test_clip_same_polygon(self, unit_square):
        result = clip_polygon(unit_square, unit_square)
        assert _polygon_area(result) == pytest.approx(1.0)

    def test_clip_triangle_by_rectangle(self, small_triangle, big_square):
        result = clip_polygon(small_triangle, big_square)
        orig_area = _polygon_area(small_triangle)
        assert _polygon_area(result) == pytest.approx(orig_area)

    def test_clip_large_region_to_small_boundary(self, big_square, unit_square):
        result = clip_polygon(big_square, unit_square)
        assert _polygon_area(result) == pytest.approx(1.0)

    def test_clip_preserves_area_when_inside(self):
        inner = make_rectangle(2, 2, 4, 4)
        outer = make_rectangle(0, 0, 10, 10)
        result = clip_polygon(inner, outer)
        assert _polygon_area(result) == pytest.approx(_polygon_area(inner))

    def test_clip_reduces_area_when_partial(self):
        subject = make_rectangle(0, 0, 10, 10)
        clip = make_rectangle(5, 5, 15, 15)
        result = clip_polygon(subject, clip)
        assert _polygon_area(result) < _polygon_area(subject)
        assert _polygon_area(result) == pytest.approx(25.0)

    def test_clip_empty_subject_returns_empty(self, unit_square):
        assert clip_polygon([], unit_square) == []

    def test_clip_empty_clip_returns_empty(self, unit_square):
        assert clip_polygon(unit_square, []) == []


# ── clip_region (2 tests) ────────────────────────────────────────────────

class TestClipRegion:
    def test_clip_region_delegates_to_clip_polygon(self, unit_square, big_square):
        r1 = clip_region(unit_square, big_square)
        r2 = clip_polygon(unit_square, big_square)
        assert r1 == r2

    def test_clip_region_empty_vertices(self, big_square):
        assert clip_region([], big_square) == []


# ── clip_all_regions (10 tests) ──────────────────────────────────────────

class TestClipAllRegions:
    def test_clip_all_basic(self, sample_regions, sample_data):
        boundary = make_rectangle(0, 0, 5, 5)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        # Only the (2.5,2.5) region is fully inside; others partially overlap
        assert result.clipped_count >= 1
        assert result.clipped_count <= 4

    def test_clip_all_remove_empty_true(self, sample_regions, sample_data):
        # Boundary far away — all regions should be removed
        boundary = make_rectangle(100, 100, 200, 200)
        result = clip_all_regions(sample_regions, sample_data, boundary, remove_empty=True)
        assert result.clipped_count == 0

    def test_clip_all_remove_empty_false(self, sample_regions, sample_data):
        boundary = make_rectangle(100, 100, 200, 200)
        result = clip_all_regions(sample_regions, sample_data, boundary, remove_empty=False)
        # Even with remove_empty=False, fully outside regions still produce empty clip
        assert result.clipped_count == 0

    def test_clip_all_counts(self, sample_regions, sample_data):
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        assert result.original_count == 4
        assert result.clipped_count == 4

    def test_clip_all_seeds_inside_outside(self, sample_regions, sample_data):
        boundary = make_rectangle(0, 0, 5, 5)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        assert result.seeds_inside_boundary + result.seeds_outside_boundary == len(sample_data)

    def test_clip_all_preserves_seeds_inside_boundary(self, sample_regions, sample_data):
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        assert result.seeds_inside_boundary == 4

    def test_clip_all_removes_seeds_outside_boundary(self, sample_regions, sample_data):
        boundary = make_rectangle(0, 0, 3, 3)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        assert result.seeds_outside_boundary >= 2

    def test_clip_all_areas_computed(self, sample_regions, sample_data):
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        assert len(result.original_areas) == 4
        assert len(result.clipped_areas) == 4
        for seed, area in result.clipped_areas.items():
            assert area > 0

    def test_clip_all_empty_regions(self, sample_data):
        boundary = make_rectangle(0, 0, 10, 10)
        result = clip_all_regions({}, sample_data, boundary)
        assert result.original_count == 0
        assert result.clipped_count == 0

    def test_clip_all_boundary_larger_than_all_regions(self, sample_regions, sample_data):
        boundary = make_rectangle(-100, -100, 200, 200)
        result = clip_all_regions(sample_regions, sample_data, boundary)
        assert result.clipped_count == 4
        # Areas should be preserved
        for seed in sample_regions:
            orig = _polygon_area(sample_regions[seed])
            clip = result.clipped_areas[seed]
            assert clip == pytest.approx(orig, rel=1e-6)


# ── ClipResult (6 tests) ─────────────────────────────────────────────────

class TestClipResult:
    def _make_result(self, regions=None, boundary=None):
        """Helper to build a simple ClipResult."""
        if regions is None:
            regions = {
                (5, 5): [(0, 0), (10, 0), (10, 10), (0, 10)],
            }
        if boundary is None:
            boundary = make_rectangle(0, 0, 10, 10)
        orig_areas = {s: _polygon_area(v) for s, v in regions.items()}
        clip_areas = {s: _polygon_area(v) for s, v in regions.items()}
        return ClipResult(
            regions=regions,
            boundary=boundary,
            removed_seeds=[],
            original_count=len(regions),
            clipped_count=len(regions),
            seeds_inside_boundary=len(regions),
            seeds_outside_boundary=0,
            original_areas=orig_areas,
            clipped_areas=clip_areas,
            boundary_area=_polygon_area(boundary),
        )

    def test_clip_result_stats_areas(self):
        r = self._make_result()
        s = r.stats
        assert s.total_original_area == pytest.approx(100.0)
        assert s.total_clipped_area == pytest.approx(100.0)

    def test_clip_result_stats_ratios(self):
        r = self._make_result()
        s = r.stats
        assert s.min_area_ratio == pytest.approx(1.0)
        assert s.max_area_ratio == pytest.approx(1.0)
        assert s.mean_area_ratio == pytest.approx(1.0)

    def test_clip_result_summary_format(self):
        r = self._make_result()
        summary = r.summary
        assert "Clipping Summary" in summary
        assert "Boundary:" in summary
        assert "Regions:" in summary

    def test_clip_result_to_dict_keys(self):
        r = self._make_result()
        d = r.to_dict()
        expected_keys = {
            "original_count", "clipped_count", "removed_count",
            "seeds_inside", "seeds_outside", "boundary_area",
            "total_original_area", "total_clipped_area",
            "area_retained_pct", "coverage_pct",
            "min_area_ratio", "max_area_ratio", "mean_area_ratio",
        }
        assert set(d.keys()) == expected_keys

    def test_clip_result_coverage_percentage(self):
        r = self._make_result()
        s = r.stats
        assert s.coverage_pct == pytest.approx(100.0)

    def test_clip_result_empty(self):
        r = ClipResult(
            regions={},
            boundary=make_rectangle(0, 0, 10, 10),
            removed_seeds=[],
            original_count=0,
            clipped_count=0,
            seeds_inside_boundary=0,
            seeds_outside_boundary=0,
            original_areas={},
            clipped_areas={},
            boundary_area=100.0,
        )
        s = r.stats
        assert s.total_original_area == 0
        assert s.total_clipped_area == 0
        assert s.area_retained_pct == 0
        assert s.coverage_pct == 0
        assert s.mean_area_ratio == 0


# ── ClipStats (2 tests) ──────────────────────────────────────────────────

class TestClipStats:
    def test_clip_stats_all_fields(self):
        s = ClipStats(
            total_original_area=200,
            total_clipped_area=100,
            boundary_area=150,
            area_retained_pct=50.0,
            coverage_pct=66.67,
            regions_removed=2,
            regions_retained=3,
            min_area_ratio=0.3,
            max_area_ratio=0.9,
            mean_area_ratio=0.6,
        )
        assert s.total_original_area == 200
        assert s.regions_removed == 2
        assert s.mean_area_ratio == 0.6

    def test_clip_stats_zero_values(self):
        s = ClipStats(
            total_original_area=0,
            total_clipped_area=0,
            boundary_area=0,
            area_retained_pct=0,
            coverage_pct=0,
            regions_removed=0,
            regions_retained=0,
            min_area_ratio=0,
            max_area_ratio=0,
            mean_area_ratio=0,
        )
        assert s.total_original_area == 0
        assert s.regions_retained == 0


# ── Export (4 tests) ─────────────────────────────────────────────────────

class TestExport:
    def _make_result(self):
        regions = {(5, 5): [(0, 0), (10, 0), (10, 10), (0, 10)]}
        boundary = make_rectangle(0, 0, 10, 10)
        orig_areas = {s: _polygon_area(v) for s, v in regions.items()}
        clip_areas = {s: _polygon_area(v) for s, v in regions.items()}
        return ClipResult(
            regions=regions,
            boundary=boundary,
            removed_seeds=[],
            original_count=1,
            clipped_count=1,
            seeds_inside_boundary=1,
            seeds_outside_boundary=0,
            original_areas=orig_areas,
            clipped_areas=clip_areas,
            boundary_area=_polygon_area(boundary),
        )

    def test_export_json_creates_file(self, tmp_path):
        result = self._make_result()
        out = str(tmp_path / "clip.json")
        export_clip_json(result, out)
        assert os.path.exists(out)

    def test_export_json_content(self, tmp_path):
        result = self._make_result()
        out = str(tmp_path / "clip.json")
        export_clip_json(result, out)
        with open(out) as f:
            data = json.load(f)
        assert "clipped_regions" in data
        assert "boundary" in data
        assert data["original_count"] == 1
        assert data["clipped_count"] == 1

    def test_export_svg_creates_file(self, tmp_path):
        result = self._make_result()
        out = str(tmp_path / "clip.svg")
        export_clip_svg(result, out)
        assert os.path.exists(out)

    def test_export_svg_contains_polygon_elements(self, tmp_path):
        result = self._make_result()
        out = str(tmp_path / "clip.svg")
        export_clip_svg(result, out)
        with open(out) as f:
            content = f.read()
        assert "<polygon" in content
        assert "<svg" in content


# ── load_boundary (3 tests) ──────────────────────────────────────────────

class TestLoadBoundary:
    def test_load_boundary_valid(self, tmp_path):
        bf = tmp_path / "boundary.txt"
        bf.write_text("0 0\n10 0\n10 10\n0 10\n")
        poly = load_boundary(str(bf))
        assert len(poly) == 4
        assert poly[0] == (0.0, 0.0)

    def test_load_boundary_with_comments(self, tmp_path):
        bf = tmp_path / "boundary.txt"
        bf.write_text("# header\n0 0\n10 0\n10 10\n0 10\n# footer\n")
        poly = load_boundary(str(bf))
        assert len(poly) == 4

    def test_load_boundary_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_boundary("/nonexistent/path.txt")

    def test_load_boundary_too_few_points(self, tmp_path):
        bf = tmp_path / "boundary.txt"
        bf.write_text("0 0\n1 1\n")
        with pytest.raises(ValueError, match="at least 3"):
            load_boundary(str(bf))
