"""Tests for vormap_report — HTML analysis report generation."""

import os
import tempfile
import math

from vormap_report import (
    VoronoiReport, generate_report,
    _escape_html, _format_number, _polygon_area, _polygon_perimeter,
    _polygon_centroid, _mean, _std, _median, _color_lerp,
)


def _square(cx, cy, size):
    h = size / 2
    return [
        (cx - h, cy - h), (cx + h, cy - h),
        (cx + h, cy + h), (cx - h, cy + h),
    ]


# ── HTML escaping ───────────────────────────────────────────────────

def test_escape_html():
    assert _escape_html('<b>"hi"</b>') == '&lt;b&gt;&quot;hi&quot;&lt;/b&gt;'


def test_escape_html_ampersand():
    assert _escape_html('a & b') == 'a &amp; b'


def test_escape_html_plain():
    assert _escape_html('hello') == 'hello'


def test_escape_html_non_string():
    assert _escape_html(42) == '42'


# ── Number formatting ──────────────────────────────────────────────

def test_format_number_int():
    assert _format_number(42) == '42'


def test_format_number_float():
    assert _format_number(3.14159, 2) == '3.14'


def test_format_number_float_zero_decimals():
    assert _format_number(3.7, 0) == '4'


def test_format_number_nan():
    assert _format_number(float('nan')) == 'nan'


def test_format_number_inf():
    assert _format_number(float('inf')) == 'inf'


def test_format_number_string():
    assert _format_number('hello') == 'hello'


# ── Polygon area ────────────────────────────────────────────────────

def test_polygon_area_unit_square():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(_polygon_area(sq) - 1.0) < 1e-9


def test_polygon_area_empty():
    assert _polygon_area([]) == 0.0


def test_polygon_area_two_points():
    assert _polygon_area([(0, 0), (1, 1)]) == 0.0


def test_polygon_area_triangle():
    tri = [(0, 0), (4, 0), (0, 3)]
    assert abs(_polygon_area(tri) - 6.0) < 1e-9


def test_polygon_area_reversed_winding():
    """Area should be positive regardless of winding direction."""
    sq = [(0, 0), (0, 1), (1, 1), (1, 0)]
    assert abs(_polygon_area(sq) - 1.0) < 1e-9


# ── Polygon perimeter ──────────────────────────────────────────────

def test_polygon_perimeter_unit_square():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(_polygon_perimeter(sq) - 4.0) < 1e-9


def test_polygon_perimeter_empty():
    assert _polygon_perimeter([]) == 0.0


def test_polygon_perimeter_single_point():
    assert _polygon_perimeter([(5, 5)]) == 0.0


def test_polygon_perimeter_triangle():
    tri = [(0, 0), (3, 0), (0, 4)]
    expected = 3 + 4 + 5  # 3-4-5 right triangle
    assert abs(_polygon_perimeter(tri) - expected) < 1e-9


# ── Polygon centroid ────────────────────────────────────────────────

def test_polygon_centroid_square():
    sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
    cx, cy = _polygon_centroid(sq)
    assert abs(cx - 1.0) < 1e-9
    assert abs(cy - 1.0) < 1e-9


def test_polygon_centroid_empty():
    cx, cy = _polygon_centroid([])
    assert cx == 0.0 and cy == 0.0


def test_polygon_centroid_single_point():
    assert _polygon_centroid([(5, 7)]) == (5, 7)


def test_polygon_centroid_degenerate_line():
    """Collinear points (zero area) should fall back to average."""
    cx, cy = _polygon_centroid([(0, 0), (2, 0), (4, 0)])
    assert abs(cx - 2.0) < 1e-9
    assert abs(cy - 0.0) < 1e-9


# ── Statistical helpers ─────────────────────────────────────────────

def test_mean_basic():
    assert abs(_mean([1, 2, 3]) - 2.0) < 1e-9


def test_mean_empty():
    assert _mean([]) == 0.0


def test_mean_single():
    assert abs(_mean([7.5]) - 7.5) < 1e-9


def test_std_basic():
    # std of [2, 4] = sqrt(((2-3)^2 + (4-3)^2)/2) = 1.0
    assert abs(_std([2, 4]) - 1.0) < 1e-9


def test_std_single():
    assert _std([5]) == 0.0


def test_std_empty():
    assert _std([]) == 0.0


def test_median_odd():
    assert _median([3, 1, 2]) == 2


def test_median_even():
    assert abs(_median([1, 2, 3, 4]) - 2.5) < 1e-9


def test_median_empty():
    assert _median([]) == 0.0


def test_median_single():
    assert _median([42]) == 42


# ── Color lerp ──────────────────────────────────────────────────────

def test_color_lerp_bounds():
    assert _color_lerp(0.0).startswith('rgb(')
    assert _color_lerp(1.0).startswith('rgb(')


def test_color_lerp_different_ends():
    assert _color_lerp(0.0) != _color_lerp(1.0)


def test_color_lerp_clamps():
    assert _color_lerp(-1.0) == _color_lerp(0.0)
    assert _color_lerp(2.0) == _color_lerp(1.0)


def test_color_lerp_hot_cold():
    c = _color_lerp(0.5, ramp="hot_cold")
    assert c.startswith('rgb(')


def test_color_lerp_unknown_ramp_defaults_to_viridis():
    assert _color_lerp(0.5, ramp="nonexistent") == _color_lerp(0.5, ramp="viridis")


# ── VoronoiReport internals ────────────────────────────────────────

def _make_report(n_regions=3):
    """Create a report with n_regions square regions on a line."""
    size = 100
    seeds = [(i * size + size // 2, size // 2) for i in range(n_regions)]
    regions = [_square(s[0], s[1], size * 0.9) for s in seeds]
    bounds = (0, size, 0, n_regions * size)
    return VoronoiReport(seeds, regions, bounds)


def test_compute_stats_fields():
    r = _make_report(3)
    stats = r._compute_stats()
    assert len(stats) == 3
    for s in stats:
        assert 'area' in s
        assert 'perimeter' in s
        assert 'compactness' in s
        assert 'centroid_x' in s
        assert 'centroid_y' in s
        assert 'vertices' in s
        assert s['area'] > 0
        assert s['perimeter'] > 0
        assert 0 <= s['compactness'] <= 1


def test_compute_stats_cached():
    """Calling _compute_stats twice returns the same object (cached)."""
    r = _make_report(2)
    s1 = r._compute_stats()
    s2 = r._compute_stats()
    assert s1 is s2


def test_compute_adjacency():
    """Regions sharing vertices should be adjacent."""
    # Two squares sharing an edge
    r1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    r2 = [(1, 0), (2, 0), (2, 1), (1, 1)]
    report = VoronoiReport([(0.5, 0.5), (1.5, 0.5)], [r1, r2], (0, 1, 0, 2))
    adj = report._compute_adjacency()
    assert 1 in adj[0]
    assert 0 in adj[1]


def test_compute_adjacency_disjoint():
    """Non-touching regions have no adjacency."""
    r1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    r2 = [(5, 5), (6, 5), (6, 6), (5, 6)]
    report = VoronoiReport([(0.5, 0.5), (5.5, 5.5)], [r1, r2], (0, 7, 0, 7))
    adj = report._compute_adjacency()
    assert len(adj[0]) == 0
    assert len(adj[1]) == 0


def test_summary_stats_keys():
    r = _make_report(4)
    summary = r._summary_stats()
    expected_keys = [
        'num_regions', 'total_bounds_area',
        'area_mean', 'area_std', 'area_median',
        'area_min', 'area_max',
        'perim_mean', 'perim_std', 'compact_mean',
        'verts_mean', 'verts_min', 'verts_max',
    ]
    for k in expected_keys:
        assert k in summary, f"Missing key: {k}"


def test_summary_stats_values():
    r = _make_report(1)
    summary = r._summary_stats()
    assert summary['num_regions'] == 1
    assert summary['area_std'] == 0.0  # only one region


# ── SVG rendering ───────────────────────────────────────────────────

def test_render_svg_diagram():
    r = _make_report(3)
    svg = r._render_svg_diagram()
    assert '<svg' in svg
    assert 'polygon' in svg
    assert 'circle' in svg


def test_render_svg_zero_bounds():
    """Zero-size bounds should return a message, not crash."""
    report = VoronoiReport([(0, 0)], [[(0, 0), (0, 0), (0, 0)]], (0, 0, 0, 0))
    result = report._render_svg_diagram()
    assert 'Cannot render' in result


def test_render_area_histogram():
    r = _make_report(5)
    svg = r._render_area_histogram()
    assert '<svg' in svg
    assert 'rect' in svg


def test_render_area_histogram_empty():
    r = VoronoiReport([], [], (0, 100, 0, 100))
    result = r._render_area_histogram()
    assert 'No data' in result


def test_render_area_histogram_uniform():
    """All regions same size should still render."""
    seeds = [(50, 50), (150, 50)]
    regions = [_square(50, 50, 90), _square(150, 50, 90)]
    r = VoronoiReport(seeds, regions, (0, 100, 0, 200))
    svg = r._render_area_histogram()
    assert '<svg' in svg


def test_render_degree_chart():
    r = _make_report(3)
    svg = r._render_degree_chart()
    assert '<svg' in svg


def test_render_degree_chart_empty():
    r = VoronoiReport([], [], (0, 100, 0, 100))
    result = r._render_degree_chart()
    assert 'No data' in result


# ── Full report generation ──────────────────────────────────────────

def test_report_generation():
    seeds = [(100, 100), (300, 100), (200, 300)]
    regions = [_square(100, 100, 150), _square(300, 100, 150), _square(200, 300, 150)]
    bounds = (0, 400, 0, 400)
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'report.html')
        result = generate_report(seeds, regions, bounds, out, allow_absolute=True)
        assert os.path.isfile(result)
        content = open(result, encoding='utf-8').read()
        assert '<!DOCTYPE html>' in content
        assert 'Summary' in content
        assert '<svg' in content
        assert 'Graph Metrics' in content
        assert 'Region Details' in content
        assert 'VoronoiMap' in content


def test_report_dict_seeds():
    seeds = [{'x': 50, 'y': 50}, {'x': 150, 'y': 150}]
    regions = [_square(50, 50, 80), _square(150, 150, 80)]
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report(seeds, regions, (0, 200, 0, 200), out, allow_absolute=True)
        assert os.path.isfile(out)


def test_report_custom_title():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report([(50, 50)], [_square(50, 50, 40)], (0, 100, 0, 100),
                        out, title="Custom", allow_absolute=True)
        content = open(out, encoding='utf-8').read()
        assert 'Custom' in content


def test_report_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report([], [], (0, 100, 0, 100), out, allow_absolute=True)
        assert os.path.isfile(out)


def test_report_html_escaping_in_title():
    """Special chars in title should be escaped in the HTML output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report([(50, 50)], [_square(50, 50, 40)], (0, 100, 0, 100),
                        out, title='<script>alert("xss")</script>',
                        allow_absolute=True)
        content = open(out, encoding='utf-8').read()
        assert '<script>' not in content
        assert '&lt;script&gt;' in content


def test_report_many_regions_truncates_table():
    """With >50 regions, the table should show '... and N more'."""
    n = 60
    seeds = [(i * 10, 0) for i in range(n)]
    regions = [_square(i * 10, 0, 8) for i in range(n)]
    bounds = (0, 10, 0, n * 10)
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report(seeds, regions, bounds, out, allow_absolute=True)
        content = open(out, encoding='utf-8').read()
        assert '... and 10 more regions' in content


def test_report_dark_mode_css():
    """Report should contain dark mode media query."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report([(50, 50)], [_square(50, 50, 40)], (0, 100, 0, 100),
                        out, allow_absolute=True)
        content = open(out, encoding='utf-8').read()
        assert 'prefers-color-scheme:dark' in content


def test_report_creates_subdirectory():
    """generate() should create parent dirs if needed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'sub', 'dir', 'r.html')
        generate_report([(50, 50)], [_square(50, 50, 40)], (0, 100, 0, 100),
                        out, allow_absolute=True)
        assert os.path.isfile(out)


def test_voronoi_report_class_api():
    """VoronoiReport class should work as a standalone object."""
    seeds = [(100, 100), (200, 200)]
    regions = [_square(100, 100, 80), _square(200, 200, 80)]
    report = VoronoiReport(seeds, regions, (0, 300, 0, 300), title="API Test")
    assert report.title == "API Test"
    assert len(report.seeds) == 2
    assert len(report.regions) == 2

    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        result = report.generate(out, allow_absolute=True)
        assert os.path.isfile(result)
