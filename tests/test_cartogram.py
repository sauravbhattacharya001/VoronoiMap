"""Tests for vormap_cartogram — Voronoi Cartogram module."""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import vormap_cartogram as vc


def test_polygon_area():
    # Unit square
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(vc._polygon_area(sq) - 1.0) < 1e-9
    # Triangle
    tri = [(0, 0), (4, 0), (0, 3)]
    assert abs(vc._polygon_area(tri) - 6.0) < 1e-9
    # Empty
    assert vc._polygon_area([]) == 0.0
    print("  polygon_area: OK")


def test_polygon_centroid():
    sq = [(0, 0), (2, 0), (2, 2), (0, 2)]
    cx, cy = vc._polygon_centroid(sq)
    assert abs(cx - 1.0) < 1e-6
    assert abs(cy - 1.0) < 1e-6
    print("  polygon_centroid: OK")


def test_cartogram_basic():
    # 4 points with equal values — should converge quickly
    points = [(200, 200), (800, 200), (200, 800), (800, 800)]
    values = [1, 1, 1, 1]
    result = vc.cartogram(points, values, iterations=30)
    assert len(result["seeds"]) == 4
    assert len(result["regions"]) == 4
    assert result["max_error"] < 0.5  # should converge reasonably
    print("  cartogram_basic: OK")


def test_cartogram_unequal():
    # One big, three small
    points = [(250, 250), (750, 250), (250, 750), (750, 750)]
    values = [100, 10, 10, 10]
    result = vc.cartogram(points, values, iterations=80, damping=0.3)
    # Should have completed iterations and produced valid regions
    assert len(result["regions"]) == 4
    assert all(a > 0 for a in result["areas"])
    print("  cartogram_unequal: OK")


def test_cartogram_empty():
    result = vc.cartogram([], [])
    assert result["seeds"] == []
    assert result["max_error"] == 0.0
    print("  cartogram_empty: OK")


def test_export_svg():
    points = [(200, 200), (800, 200), (500, 800)]
    values = [30, 50, 20]
    result = vc.cartogram(points, values, iterations=20)
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        path = f.name
    try:
        vc.export_svg(result, path, values=values)
        content = open(path).read()
        assert "<svg" in content
        assert "<polygon" in content
        print("  export_svg: OK")
    finally:
        os.unlink(path)


def test_export_json():
    points = [(100, 100), (500, 500)]
    values = [10, 20]
    result = vc.cartogram(points, values, iterations=10)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        vc.export_json(result, path)
        data = json.load(open(path))
        assert "seeds" in data
        assert "areas" in data
        print("  export_json: OK")
    finally:
        os.unlink(path)


def test_format_report():
    points = [(200, 200), (800, 800)]
    values = [40, 60]
    result = vc.cartogram(points, values, iterations=10)
    report = vc.format_report(result)
    assert "Cartogram Report" in report
    assert "Seeds: 2" in report
    print("  format_report: OK")


if __name__ == "__main__":
    print("Running vormap_cartogram tests...")
    test_polygon_area()
    test_polygon_centroid()
    test_cartogram_basic()
    test_cartogram_unequal()
    test_cartogram_empty()
    test_export_svg()
    test_export_json()
    test_format_report()
    print("All tests passed!")
