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


def test_escape_html():
    assert _escape_html('<b>"hi"</b>') == '&lt;b&gt;&quot;hi&quot;&lt;/b&gt;'


def test_format_number():
    assert _format_number(42) == '42'
    assert _format_number(3.14159, 2) == '3.14'


def test_polygon_area():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(_polygon_area(sq) - 1.0) < 1e-9
    assert _polygon_area([]) == 0.0


def test_polygon_perimeter():
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(_polygon_perimeter(sq) - 4.0) < 1e-9


def test_polygon_centroid():
    sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
    cx, cy = _polygon_centroid(sq)
    assert abs(cx - 1.0) < 1e-9
    assert abs(cy - 1.0) < 1e-9


def test_stats():
    assert abs(_mean([1, 2, 3]) - 2.0) < 1e-9
    assert _median([1, 2, 3]) == 2
    assert _mean([]) == 0.0


def test_color_lerp():
    assert _color_lerp(0.0).startswith('rgb(')
    assert _color_lerp(0.0) != _color_lerp(1.0)


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
        assert 'Custom' in open(out, encoding='utf-8').read()


def test_report_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, 'r.html')
        generate_report([], [], (0, 100, 0, 100), out, allow_absolute=True)
        assert os.path.isfile(out)
