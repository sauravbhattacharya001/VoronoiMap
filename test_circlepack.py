"""Tests for vormap_circlepack module."""

import math
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import vormap_circlepack as cp


# ── Geometry helpers ─────────────────────────────────────────────────

def test_polygon_area_square():
    verts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert abs(cp._polygon_area(verts) - 100.0) < 1e-6


def test_polygon_area_triangle():
    verts = [(0, 0), (10, 0), (5, 10)]
    assert abs(cp._polygon_area(verts) - 50.0) < 1e-6


def test_polygon_centroid_square():
    verts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy = cp._polygon_centroid(verts)
    assert abs(cx - 5.0) < 1e-6
    assert abs(cy - 5.0) < 1e-6


def test_point_in_polygon():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert cp._point_in_polygon(5, 5, square) is True
    assert cp._point_in_polygon(15, 5, square) is False


def test_largest_inscribed_circle_square():
    """For a 10x10 square, the largest inscribed circle should have radius ~5."""
    verts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy, r = cp._largest_inscribed_circle(verts, iterations=20)
    assert abs(cx - 5.0) < 0.5
    assert abs(cy - 5.0) < 0.5
    assert abs(r - 5.0) < 0.5


def test_circle_pack():
    regions = {
        (5, 5): [(0, 0), (10, 0), (10, 10), (0, 10)],
        (15, 5): [(10, 0), (20, 0), (20, 10), (10, 10)],
    }
    packing = cp.circle_pack(regions)
    assert len(packing) == 2
    for seed, info in packing.items():
        assert "cx" in info
        assert "radius" in info
        assert 0 < info["efficiency"] <= 1.0


def test_packing_stats():
    regions = {(5, 5): [(0, 0), (10, 0), (10, 10), (0, 10)]}
    packing = cp.circle_pack(regions)
    stats = cp.packing_stats(packing)
    assert stats["total_cells"] == 1
    assert 0 < stats["mean_efficiency"] <= 1.0


def test_packing_stats_empty():
    stats = cp.packing_stats({})
    assert stats["total_cells"] == 0


def test_export_svg(tmp_path):
    regions = {(5, 5): [(0, 0), (10, 0), (10, 10), (0, 10)]}
    packing = cp.circle_pack(regions)
    out = str(tmp_path / "test.svg")
    cp.export_svg(packing, regions, [(5, 5)], out)
    assert os.path.exists(out)
    content = open(out).read()
    assert "<svg" in content
    assert "circle" in content


def test_export_html(tmp_path):
    regions = {(5, 5): [(0, 0), (10, 0), (10, 10), (0, 10)]}
    packing = cp.circle_pack(regions)
    out = str(tmp_path / "test.html")
    cp.export_html(packing, regions, [(5, 5)], out)
    assert os.path.exists(out)
    content = open(out).read()
    assert "Circle Packing" in content


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    
    test_polygon_area_square()
    test_polygon_area_triangle()
    test_polygon_centroid_square()
    test_point_in_polygon()
    test_largest_inscribed_circle_square()
    test_circle_pack()
    test_packing_stats()
    test_packing_stats_empty()
    
    class TmpPath:
        def __truediv__(self, name):
            return os.path.join(tmp, name)
    
    test_export_svg(TmpPath())
    test_export_html(TmpPath())
    print("All tests passed!")
