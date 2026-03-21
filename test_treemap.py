"""Tests for vormap_treemap module."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from vormap_treemap import (
    voronoi_treemap, format_treemap_report, export_treemap_json,
    export_treemap_svg, export_treemap_csv, TreemapCell,
    _polygon_area, _polygon_centroid, _get_weight,
)


def _sample_data():
    return {
        "name": "root",
        "children": [
            {"name": "A", "weight": 60, "children": [
                {"name": "A1", "weight": 30},
                {"name": "A2", "weight": 30},
            ]},
            {"name": "B", "weight": 25},
            {"name": "C", "weight": 15},
        ],
    }


def test_polygon_area():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert abs(_polygon_area(square) - 100.0) < 1e-6


def test_polygon_centroid():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy = _polygon_centroid(square)
    assert abs(cx - 5.0) < 1e-6
    assert abs(cy - 5.0) < 1e-6


def test_get_weight():
    data = _sample_data()
    assert _get_weight(data) == 100


def test_treemap_basic():
    data = _sample_data()
    root = voronoi_treemap(data, bbox=(0, 0, 100, 100), iterations=10)
    assert isinstance(root, TreemapCell)
    assert root.name == "root"
    assert len(root.children) == 3


def test_treemap_leaf():
    data = {"name": "leaf", "weight": 10}
    root = voronoi_treemap(data, bbox=(0, 0, 50, 50))
    assert root.name == "leaf"
    assert root.children == []


def test_format_report():
    data = _sample_data()
    root = voronoi_treemap(data, bbox=(0, 0, 100, 100), iterations=10)
    report = format_treemap_report(root)
    assert "Voronoi Treemap Report" in report
    assert "root" in report


def test_export_json():
    data = _sample_data()
    root = voronoi_treemap(data, bbox=(0, 0, 100, 100), iterations=10)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        path = f.name
    try:
        export_treemap_json(root, path)
        with open(path) as f:
            result = json.load(f)
        assert result["name"] == "root"
        assert "children" in result
    finally:
        os.unlink(path)


def test_export_svg():
    data = _sample_data()
    root = voronoi_treemap(data, bbox=(0, 0, 200, 150), iterations=10)
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
        path = f.name
    try:
        export_treemap_svg(root, path)
        with open(path) as f:
            svg = f.read()
        assert "<svg" in svg
        assert "</svg>" in svg
    finally:
        os.unlink(path)


def test_export_csv():
    data = _sample_data()
    root = voronoi_treemap(data, bbox=(0, 0, 100, 100), iterations=10)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        path = f.name
    try:
        export_treemap_csv(root, path)
        with open(path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "name,weight,depth,area,centroid_x,centroid_y"
        assert len(lines) >= 4  # header + at least 3 leaves
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_polygon_area()
    test_polygon_centroid()
    test_get_weight()
    test_treemap_basic()
    test_treemap_leaf()
    test_format_report()
    test_export_json()
    test_export_svg()
    test_export_csv()
    print("All tests passed!")
