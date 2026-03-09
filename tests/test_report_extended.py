"""Extended tests for vormap_report — covers stats, adjacency, SVG rendering,
histogram, degree chart, edge cases, and HTML report structure.

Brings test_report.py from 11 tests / 95 lines to 60+ tests.
"""

import math
import os
import tempfile

import pytest

from vormap_report import (
    VoronoiReport, generate_report,
    _escape_html, _format_number, _color_lerp,
)
from vormap_geometry import (
    polygon_area as _polygon_area,
    polygon_perimeter as _polygon_perimeter,
    polygon_centroid as _polygon_centroid,
    mean as _mean,
    std as _std,
    median as _median,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _square(cx, cy, size):
    h = size / 2
    return [
        (cx - h, cy - h), (cx + h, cy - h),
        (cx + h, cy + h), (cx - h, cy + h),
    ]


def _triangle(cx, cy, size):
    """Equilateral-ish triangle centred at (cx, cy)."""
    h = size * math.sqrt(3) / 3
    return [
        (cx, cy + h),
        (cx - size / 2, cy - h / 2),
        (cx + size / 2, cy - h / 2),
    ]


def _grid_regions(nx=3, ny=3, cell=100):
    """Generate grid-aligned square regions with seeds at centres."""
    seeds = []
    regions = []
    for iy in range(ny):
        for ix in range(nx):
            cx, cy = ix * cell + cell / 2, iy * cell + cell / 2
            seeds.append((cx, cy))
            regions.append(_square(cx, cy, cell))
    bounds = (0, ny * cell, 0, nx * cell)
    return seeds, regions, bounds


@pytest.fixture
def three_squares():
    """Three non-overlapping square regions."""
    seeds = [(100, 100), (300, 100), (200, 300)]
    regions = [_square(100, 100, 150), _square(300, 100, 150), _square(200, 300, 150)]
    bounds = (0, 400, 0, 400)
    return seeds, regions, bounds


@pytest.fixture
def grid_3x3():
    return _grid_regions(3, 3, 100)


# ══════════════════════════════════════════════════════════════════════
#  Private helper functions
# ══════════════════════════════════════════════════════════════════════

class TestEscapeHtml:
    def test_angle_brackets(self):
        assert "&lt;" in _escape_html("<script>")
        assert "&gt;" in _escape_html("</script>")

    def test_ampersand(self):
        assert _escape_html("a&b") == "a&amp;b"

    def test_double_quotes(self):
        assert _escape_html('"hi"') == "&quot;hi&quot;"

    def test_no_change_for_safe_text(self):
        assert _escape_html("hello world") == "hello world"

    def test_non_string_input(self):
        assert _escape_html(42) == "42"


class TestFormatNumber:
    def test_integer(self):
        assert _format_number(42) == "42"

    def test_float_default_decimals(self):
        assert _format_number(3.14159) == "3.14"

    def test_float_custom_decimals(self):
        assert _format_number(3.14159, 4) == "3.1416"

    def test_nan(self):
        assert _format_number(float("nan")) == "nan"

    def test_inf(self):
        assert _format_number(float("inf")) == "inf"

    def test_string_passthrough(self):
        assert _format_number("hello") == "hello"

    def test_zero_float(self):
        assert _format_number(0.0) == "0.00"


class TestPolygonArea:
    def test_unit_square(self):
        assert abs(_polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]) - 1.0) < 1e-9

    def test_empty(self):
        assert _polygon_area([]) == 0.0

    def test_two_points(self):
        assert _polygon_area([(0, 0), (1, 1)]) == 0.0

    def test_triangle(self):
        # (0,0), (4,0), (0,3) => area = 6
        assert abs(_polygon_area([(0, 0), (4, 0), (0, 3)]) - 6.0) < 1e-9

    def test_rectangle(self):
        assert abs(_polygon_area([(0, 0), (5, 0), (5, 3), (0, 3)]) - 15.0) < 1e-9


class TestPolygonPerimeter:
    def test_unit_square(self):
        assert abs(_polygon_perimeter([(0, 0), (1, 0), (1, 1), (0, 1)]) - 4.0) < 1e-9

    def test_empty(self):
        assert _polygon_perimeter([]) == 0.0

    def test_single_point(self):
        assert _polygon_perimeter([(0, 0)]) == 0.0


class TestPolygonCentroid:
    def test_square_centroid(self):
        cx, cy = _polygon_centroid([(0, 0), (2, 0), (2, 2), (0, 2)])
        assert abs(cx - 1.0) < 1e-9
        assert abs(cy - 1.0) < 1e-9

    def test_empty(self):
        assert _polygon_centroid([]) == (0.0, 0.0)

    def test_single_point(self):
        assert _polygon_centroid([(5, 7)]) == (5, 7)

    def test_degenerate_line(self):
        """Collinear points: fallback to average."""
        cx, cy = _polygon_centroid([(0, 0), (2, 0), (4, 0)])
        assert abs(cx - 2.0) < 1e-9
        assert abs(cy - 0.0) < 1e-9


class TestStatHelpers:
    def test_mean(self):
        assert abs(_mean([1, 2, 3]) - 2.0) < 1e-9

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std(self):
        assert abs(_std([2, 4, 4, 4, 5, 5, 7, 9]) - 2.0) < 0.01

    def test_std_single(self):
        assert _std([5]) == 0.0

    def test_median_odd(self):
        assert _median([3, 1, 2]) == 2

    def test_median_even(self):
        assert _median([1, 2, 3, 4]) == 2.5

    def test_median_empty(self):
        assert _median([]) == 0.0


class TestColorLerp:
    def test_returns_rgb(self):
        assert _color_lerp(0.0).startswith("rgb(")
        assert _color_lerp(1.0).startswith("rgb(")

    def test_endpoints_differ(self):
        assert _color_lerp(0.0) != _color_lerp(1.0)

    def test_clamps_below_zero(self):
        # Should not crash
        assert _color_lerp(-0.5).startswith("rgb(")

    def test_clamps_above_one(self):
        assert _color_lerp(1.5).startswith("rgb(")

    def test_hot_cold_ramp(self):
        assert _color_lerp(0.5, "hot_cold").startswith("rgb(")

    def test_unknown_ramp_fallback(self):
        # Falls back to viridis
        assert _color_lerp(0.5, "nonexistent").startswith("rgb(")

    def test_midpoint_has_valid_values(self):
        c = _color_lerp(0.5)
        # Parse rgb(r,g,b)
        inner = c[4:-1]
        parts = inner.split(",")
        assert len(parts) == 3
        for p in parts:
            v = int(p)
            assert 0 <= v <= 255


# ══════════════════════════════════════════════════════════════════════
#  VoronoiReport methods
# ══════════════════════════════════════════════════════════════════════

class TestComputeStats:
    def test_returns_list(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        stats = report._compute_stats()
        assert isinstance(stats, list)
        assert len(stats) == 3

    def test_stat_keys(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        stat = report._compute_stats()[0]
        for key in ("index", "area", "perimeter", "centroid_x", "centroid_y",
                     "vertices", "compactness"):
            assert key in stat

    def test_area_positive(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        for s in report._compute_stats():
            assert s["area"] > 0

    def test_compactness_range(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        for s in report._compute_stats():
            # Compactness is (4*pi*A)/P^2 — max 1.0 for a circle
            assert 0.0 < s["compactness"] <= 1.0

    def test_caches_result(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        s1 = report._compute_stats()
        s2 = report._compute_stats()
        assert s1 is s2  # Same object — cached


class TestComputeAdjacency:
    def test_grid_adjacency(self, grid_3x3):
        seeds, regions, bounds = grid_3x3
        report = VoronoiReport(seeds, regions, bounds)
        adj = report._compute_adjacency()
        assert len(adj) == 9

    def test_corner_cells_fewer_neighbors(self, grid_3x3):
        """Corner cells in a 3x3 grid have fewer neighbors than centre."""
        seeds, regions, bounds = grid_3x3
        report = VoronoiReport(seeds, regions, bounds)
        adj = report._compute_adjacency()
        degrees = [len(nb) for nb in adj.values()]
        # Corner cell has 1 neighbor (shared edge), centre has up to 4
        assert min(degrees) < max(degrees)

    def test_symmetric(self, grid_3x3):
        """If A is adjacent to B, B is adjacent to A."""
        seeds, regions, bounds = grid_3x3
        report = VoronoiReport(seeds, regions, bounds)
        adj = report._compute_adjacency()
        for i, neighbors in adj.items():
            for j in neighbors:
                assert i in adj[j]


class TestSummaryStats:
    def test_keys(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        summary = report._summary_stats()
        expected_keys = {
            "num_regions", "total_bounds_area",
            "area_mean", "area_std", "area_median",
            "area_min", "area_max",
            "perim_mean", "perim_std",
            "compact_mean", "verts_mean", "verts_min", "verts_max",
        }
        assert expected_keys.issubset(summary.keys())

    def test_num_regions(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        assert report._summary_stats()["num_regions"] == 3

    def test_total_bounds_area(self, three_squares):
        seeds, regions, bounds = three_squares
        # bounds = (0, 400, 0, 400) => area = 400*400 = 160000
        assert report._summary_stats()["total_bounds_area"] == 160000.0 if False else True
        report = VoronoiReport(seeds, regions, bounds)
        assert report._summary_stats()["total_bounds_area"] == 160000.0

    def test_equal_regions_zero_std(self):
        """Three identical square regions should have zero area std."""
        seeds = [(50, 50), (150, 50), (250, 50)]
        regions = [_square(50, 50, 100), _square(150, 50, 100), _square(250, 50, 100)]
        report = VoronoiReport(seeds, regions, (0, 100, 0, 300))
        assert report._summary_stats()["area_std"] == 0.0


class TestRenderSvgDiagram:
    def test_returns_svg(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        svg = report._render_svg_diagram()
        assert "<svg" in svg
        assert "</svg>" in svg

    def test_contains_polygons(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        svg = report._render_svg_diagram()
        assert "<polygon" in svg

    def test_contains_seed_circles(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        svg = report._render_svg_diagram()
        assert svg.count("<circle") == 3

    def test_zero_bounds_returns_message(self):
        """Zero-size bounds should return an error message, not SVG."""
        report = VoronoiReport([(0, 0)], [_square(0, 0, 10)], (0, 0, 0, 0))
        result = report._render_svg_diagram()
        assert "Cannot render" in result

    def test_empty_regions(self):
        report = VoronoiReport([], [], (0, 100, 0, 100))
        svg = report._render_svg_diagram()
        # Should still produce valid SVG (just no polygons)
        assert "<svg" in svg


class TestRenderHistogram:
    def test_returns_svg(self, three_squares):
        seeds, regions, bounds = three_squares
        report = VoronoiReport(seeds, regions, bounds)
        svg = report._render_area_histogram()
        assert "<svg" in svg

    def test_contains_bars(self, grid_3x3):
        seeds, regions, bounds = grid_3x3
        report = VoronoiReport(seeds, regions, bounds)
        svg = report._render_area_histogram()
        assert "<rect" in svg

    def test_empty_returns_no_data(self):
        report = VoronoiReport([], [], (0, 100, 0, 100))
        result = report._render_area_histogram()
        assert "No data" in result

    def test_all_same_area(self):
        """All same area => one bin with all counts."""
        seeds = [(50, 50), (150, 50)]
        regions = [_square(50, 50, 100), _square(150, 50, 100)]
        report = VoronoiReport(seeds, regions, (0, 100, 0, 200))
        svg = report._render_area_histogram()
        assert "<svg" in svg  # Should not crash


class TestRenderDegreeChart:
    def test_returns_svg(self, grid_3x3):
        seeds, regions, bounds = grid_3x3
        report = VoronoiReport(seeds, regions, bounds)
        svg = report._render_degree_chart()
        assert "<svg" in svg
        assert "<rect" in svg

    def test_empty_returns_no_data(self):
        report = VoronoiReport([], [], (0, 100, 0, 100))
        result = report._render_degree_chart()
        assert "No data" in result


# ══════════════════════════════════════════════════════════════════════
#  Full report generation
# ══════════════════════════════════════════════════════════════════════

class TestGenerate:
    def test_creates_file(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "report.html")
            result = generate_report(seeds, regions, bounds, out,
                                     allow_absolute=True)
            assert os.path.isfile(result)

    def test_valid_html(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "report.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "<!DOCTYPE html>" in html
            assert "</html>" in html

    def test_contains_svg_visualizations(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "report.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert html.count("<svg") >= 3  # diagram, histogram, degree

    def test_custom_title(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report([(50, 50)], [_square(50, 50, 40)],
                            (0, 100, 0, 100), out,
                            title="My Custom Title", allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "My Custom Title" in html

    def test_title_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report([(50, 50)], [_square(50, 50, 40)],
                            (0, 100, 0, 100), out,
                            title='<script>alert("xss")</script>',
                            allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "<script>" not in html
            assert "&lt;script&gt;" in html

    def test_dict_seeds(self):
        seeds = [{"x": 50, "y": 50}, {"x": 150, "y": 150}]
        regions = [_square(50, 50, 80), _square(150, 150, 80)]
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report(seeds, regions, (0, 200, 0, 200), out,
                            allow_absolute=True)
            assert os.path.isfile(out)

    def test_empty_report(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report([], [], (0, 100, 0, 100), out,
                            allow_absolute=True)
            assert os.path.isfile(out)

    def test_large_region_set_truncated(self):
        """With >50 regions, the table shows '... and N more regions'."""
        seeds, regions, bounds = _grid_regions(8, 8, 50)  # 64 regions
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "more regions" in html

    def test_report_contains_region_table(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "Region Details" in html
            assert "<table" in html

    def test_report_contains_summary_section(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "Summary" in html
            assert "Graph Metrics" in html

    def test_report_dark_mode_support(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "prefers-color-scheme:dark" in html

    def test_creates_subdirectory(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "sub", "dir", "report.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            assert os.path.isfile(out)

    def test_footer_present(self, three_squares):
        seeds, regions, bounds = three_squares
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.html")
            generate_report(seeds, regions, bounds, out, allow_absolute=True)
            html = open(out, encoding="utf-8").read()
            assert "VoronoiMap" in html
            assert "GitHub" in html


class TestVoronoiReportInit:
    def test_seeds_from_tuples(self):
        report = VoronoiReport([(1, 2), (3, 4)], [[], []], (0, 10, 0, 10))
        assert report.seeds == [(1, 2), (3, 4)]

    def test_seeds_from_dicts(self):
        report = VoronoiReport([{"x": 1, "y": 2}], [[]], (0, 10, 0, 10))
        assert report.seeds == [(1, 2)]

    def test_default_title(self):
        report = VoronoiReport([], [], (0, 10, 0, 10))
        assert report.title == "Voronoi Analysis Report"

    def test_custom_title(self):
        report = VoronoiReport([], [], (0, 10, 0, 10), title="Custom")
        assert report.title == "Custom"
