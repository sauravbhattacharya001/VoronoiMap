"""Tests for vormap_mesh3d — 3D Mesh Exporter."""

import json
import math
import os
import struct
import sys
import tempfile

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(__file__))

import vormap
import vormap_mesh3d as m3d


# ── Unit tests for helpers ───────────────────────────────────────────


def test_fan_triangulate():
    # Triangle → 1 tri
    assert m3d._fan_triangulate([(0, 0), (1, 0), (0, 1)]) == [(0, 1, 2)]
    # Square → 2 tris
    assert len(m3d._fan_triangulate([(0, 0), (1, 0), (1, 1), (0, 1)])) == 2
    # Degenerate
    assert m3d._fan_triangulate([]) == []
    assert m3d._fan_triangulate([(0, 0)]) == []
    print("  PASS test_fan_triangulate")


def test_polygon_centroid():
    cx, cy = m3d._polygon_centroid([(0, 0), (2, 0), (2, 2), (0, 2)])
    assert abs(cx - 1.0) < 1e-9
    assert abs(cy - 1.0) < 1e-9
    print("  PASS test_polygon_centroid")


def test_sort_polygon_ccw():
    poly = [(1, 0), (0, 1), (-1, 0), (0, -1)]
    sorted_poly = m3d._sort_polygon_ccw(poly)
    # Should be in angular order around centroid
    cx, cy = m3d._polygon_centroid(sorted_poly)
    angles = [math.atan2(p[1] - cy, p[0] - cx) for p in sorted_poly]
    for i in range(len(angles) - 1):
        assert angles[i] <= angles[i + 1], "Not sorted CCW"
    print("  PASS test_sort_polygon_ccw")


def test_normal():
    # Flat face on XY plane → normal should be (0, 0, 1) or (0, 0, -1)
    n = m3d._normal((0, 0, 0), (1, 0, 0), (0, 1, 0))
    assert abs(n[2]) - 1.0 < 1e-9
    print("  PASS test_normal")


def test_compute_heights_uniform():
    cells = [{"area": 10}, {"area": 20}, {"area": 5}]
    h = m3d._compute_heights(cells, mode="uniform", scale=2.0, base=1.0, uniform_h=5.0)
    assert all(abs(x - 11.0) < 1e-9 for x in h)
    print("  PASS test_compute_heights_uniform")


def test_compute_heights_area():
    cells = [{"area": 0}, {"area": 50}, {"area": 100}]
    h = m3d._compute_heights(cells, mode="area", scale=1.0, base=0.0)
    assert h[0] < h[1] < h[2]
    print("  PASS test_compute_heights_area")


def test_compute_heights_density():
    cells = [{"area": 0}, {"area": 50}, {"area": 100}]
    h = m3d._compute_heights(cells, mode="density", scale=1.0, base=0.0)
    # Smaller area = taller in density mode
    assert h[0] > h[1] > h[2]
    print("  PASS test_compute_heights_density")


# ── Integration tests with file I/O ─────────────────────────────────


def _make_test_points(n=20, seed=42):
    """Create a simple test point file in data/ directory."""
    import random
    rng = random.Random(seed)
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "test_mesh3d_points.txt")
    with open(path, "w") as f:
        for _ in range(n):
            x = rng.uniform(100, 900)
            y = rng.uniform(100, 900)
            f.write("%.4f %.4f\n" % (x, y))
    return "test_mesh3d_points.txt"


def test_export_obj():
    pts = _make_test_points()
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.obj")
        stats = m3d.generate_mesh(pts, out, height_mode="area", scale=1.0)
        assert stats["cells"] > 0
        assert os.path.exists(out)
        with open(out) as f:
            content = f.read()
        assert "v " in content
        assert "f " in content
        print("  PASS test_export_obj (cells=%d)" % stats["cells"])


def test_export_stl():
    pts = _make_test_points()
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.stl")
        stats = m3d.generate_mesh(pts, out, height_mode="density", scale=2.0)
        assert stats["cells"] > 0
        assert os.path.exists(out)
        size = os.path.getsize(out)
        assert size > 84  # header + at least one triangle
        with open(out, "rb") as f:
            f.read(80)  # header
            tri_count = struct.unpack("<I", f.read(4))[0]
            assert tri_count > 0
        print("  PASS test_export_stl (cells=%d, tris=%d)" % (stats["cells"], tri_count))


def test_export_json_summary():
    pts = _make_test_points()
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.obj")
        stats = m3d.generate_mesh(pts, out, json_summary=True)
        assert "summary" in stats
        with open(stats["summary"]) as f:
            summary = json.load(f)
        assert "cells" in summary
        assert "bounds" in summary
        assert summary["cells"] == stats["cells"]
        print("  PASS test_export_json_summary")


def test_uniform_mode():
    pts = _make_test_points()
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.obj")
        stats = m3d.generate_mesh(pts, out, height_mode="uniform",
                                  uniform_height=7.5, base=1.0)
        assert stats["cells"] > 0
        print("  PASS test_uniform_mode")


def test_sample_option():
    pts = _make_test_points(n=30)
    with tempfile.TemporaryDirectory() as tmpdir:
        out = os.path.join(tmpdir, "test.obj")
        stats = m3d.generate_mesh(pts, out, sample=10, seed=123)
        assert stats["cells"] <= 10
        print("  PASS test_sample_option (cells=%d)" % stats["cells"])


if __name__ == "__main__":
    print("vormap_mesh3d tests")
    print("=" * 40)
    test_fan_triangulate()
    test_polygon_centroid()
    test_sort_polygon_ccw()
    test_normal()
    test_compute_heights_uniform()
    test_compute_heights_area()
    test_compute_heights_density()
    test_export_obj()
    test_export_stl()
    test_export_json_summary()
    test_uniform_mode()
    test_sample_option()
    print("\nAll tests passed!")
