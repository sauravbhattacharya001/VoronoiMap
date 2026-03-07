"""Tests for vormap_transect — Transect Profiler."""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_transect import (
    Transect,
    TransectCrossing,
    TransectPoint,
    TransectResult,
    _find_region_for_point,
    _point_in_polygon,
    _segments_intersect,
    _transect_region_intersections,
    analyse_transect,
    create_transect,
    export_transect_csv,
    export_transect_json,
    export_transect_svg,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def square_regions():
    """Four square regions tiling [0,200]×[0,200]."""
    return {
        (50.0, 50.0): [(0, 0), (100, 0), (100, 100), (0, 100)],
        (150.0, 50.0): [(100, 0), (200, 0), (200, 100), (100, 100)],
        (50.0, 150.0): [(0, 100), (100, 100), (100, 200), (0, 200)],
        (150.0, 150.0): [(100, 100), (200, 100), (200, 200), (100, 200)],
    }


@pytest.fixture
def square_data():
    return [(50.0, 50.0), (150.0, 50.0), (50.0, 150.0), (150.0, 150.0)]


@pytest.fixture
def square_stats():
    return [
        {"region_index": 1, "seed_x": 50.0, "seed_y": 50.0, "area": 10000.0,
         "compactness": 0.7854, "centroid_x": 50.0, "centroid_y": 50.0},
        {"region_index": 2, "seed_x": 150.0, "seed_y": 50.0, "area": 10000.0,
         "compactness": 0.7854, "centroid_x": 150.0, "centroid_y": 50.0},
        {"region_index": 3, "seed_x": 50.0, "seed_y": 150.0, "area": 10000.0,
         "compactness": 0.7854, "centroid_x": 50.0, "centroid_y": 150.0},
        {"region_index": 4, "seed_x": 150.0, "seed_y": 150.0, "area": 10000.0,
         "compactness": 0.7854, "centroid_x": 150.0, "centroid_y": 150.0},
    ]


# ── create_transect ─────────────────────────────────────────────────

class TestCreateTransect:
    def test_basic_two_points(self):
        t = create_transect((0, 0), (100, 100))
        assert len(t.waypoints) == 2
        assert t.segment_count == 1
        assert abs(t.total_length - math.hypot(100, 100)) < 0.01

    def test_multi_segment(self):
        t = create_transect((0, 0), (100, 0), (100, 100))
        assert t.segment_count == 2
        assert abs(t.total_length - 200) < 0.01

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 2"):
            create_transect((0, 0))

    def test_bad_coordinate(self):
        with pytest.raises(ValueError, match="finite"):
            create_transect((0, 0), (float("nan"), 1))

    def test_bad_tuple(self):
        with pytest.raises(ValueError, match="\\(x, y\\)"):
            create_transect((0, 0), (1, 2, 3))


# ── Geometry helpers ─────────────────────────────────────────────────

class TestSegmentsIntersect:
    def test_cross(self):
        r = _segments_intersect((0, 0), (10, 10), (10, 0), (0, 10))
        assert r is not None
        assert abs(r[0] - 5) < 0.01
        assert abs(r[1] - 5) < 0.01

    def test_parallel(self):
        assert _segments_intersect((0, 0), (10, 0), (0, 1), (10, 1)) is None

    def test_no_cross(self):
        assert _segments_intersect((0, 0), (5, 0), (10, 0), (10, 10)) is None


class TestPointInPolygon:
    def test_inside(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert _point_in_polygon(5, 5, poly) is True

    def test_outside(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert _point_in_polygon(15, 5, poly) is False


class TestFindRegion:
    def test_finds_correct_region(self, square_regions):
        seed = _find_region_for_point(25, 25, square_regions)
        assert seed == (50.0, 50.0)

    def test_other_quadrant(self, square_regions):
        seed = _find_region_for_point(175, 175, square_regions)
        assert seed == (150.0, 150.0)


# ── analyse_transect ─────────────────────────────────────────────────

class TestAnalyseTransect:
    def test_horizontal_transect(self, square_regions, square_data, square_stats):
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        assert result.regions_crossed == 2
        assert abs(result.total_length - 200) < 0.01

    def test_diagonal_transect(self, square_regions, square_data, square_stats):
        t = create_transect((0, 0), (200, 200))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        assert result.regions_crossed >= 2

    def test_multi_segment(self, square_regions, square_data, square_stats):
        t = create_transect((0, 50), (100, 150), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        assert result.regions_crossed >= 2
        assert result.transect.segment_count == 2

    def test_summary(self, square_regions, square_data, square_stats):
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        s = result.summary()
        assert "Transect Analysis Summary" in s
        assert "Regions crossed" in s

    def test_no_stats(self, square_regions, square_data):
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data)
        assert result.regions_crossed >= 1

    def test_zero_length_segment(self, square_regions, square_data, square_stats):
        t = create_transect((50, 50), (50, 50), (100, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        assert result.total_length > 0


# ── Export: SVG ──────────────────────────────────────────────────────

class TestExportSVG:
    def test_writes_svg(self, square_regions, square_data, square_stats):
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_transect_svg(result, square_regions, square_data, path)
            content = open(path, encoding="utf-8").read()
            assert "<svg" in content
            assert "Transect Profile" in content
        finally:
            os.unlink(path)

    def test_empty_crossings(self, square_regions, square_data):
        t = create_transect((-1000, -1000), (-999, -999))
        result = analyse_transect(t, square_regions, square_data)
        # Even with weird coords, should produce valid SVG
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_transect_svg(result, square_regions, square_data, path)
            assert os.path.exists(path)
        finally:
            os.unlink(path)


# ── Export: JSON ─────────────────────────────────────────────────────

class TestExportJSON:
    def test_writes_json(self, square_regions, square_data, square_stats):
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_transect_json(result, path)
            obj = json.loads(open(path, encoding="utf-8").read())
            assert "transect" in obj
            assert "crossings" in obj
            assert obj["summary"]["regions_crossed"] == result.regions_crossed
        finally:
            os.unlink(path)


# ── Export: CSV ──────────────────────────────────────────────────────

class TestExportCSV:
    def test_writes_csv(self, square_regions, square_data, square_stats):
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_transect_csv(result, path)
            lines = open(path, encoding="utf-8").readlines()
            assert "region_index" in lines[0]
            assert len(lines) >= 2  # header + at least 1 data row
        finally:
            os.unlink(path)


# ── TransectResult dataclass ─────────────────────────────────────────

class TestTransectResult:
    def test_density_gradient_flat(self, square_regions, square_data, square_stats):
        """Equal-area regions should have ~zero gradient."""
        t = create_transect((0, 50), (200, 50))
        result = analyse_transect(t, square_regions, square_data, square_stats)
        assert abs(result.density_gradient) < 0.001
