"""Tests for vormap_transform — geometric transformations."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_transform as vt


def _square():
    return [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def _close(a, b, tol=1e-9):
    return abs(a - b) < tol


def test_rotate_360():
    pts = _square()
    out = vt.rotate(pts, 360.0)
    for (x1, y1), (x2, y2) in zip(pts, out):
        assert _close(x1, x2) and _close(y1, y2)


def test_rotate_90():
    pts = [(1.0, 0.0)]
    out = vt.rotate(pts, 90.0, pivot=(0.0, 0.0))
    assert _close(out[0][0], 0.0) and _close(out[0][1], 1.0)


def test_scale_uniform():
    pts = _square()
    out = vt.scale(pts, 2.0, center=(0.0, 0.0))
    assert _close(out[1][0], 2.0) and _close(out[1][1], 0.0)


def test_scale_anisotropic():
    pts = [(1.0, 1.0)]
    out = vt.scale(pts, sx=3.0, sy=0.5, center=(0.0, 0.0))
    assert _close(out[0][0], 3.0) and _close(out[0][1], 0.5)


def test_mirror_x():
    pts = [(1.0, 2.0)]
    out = vt.mirror(pts, axis="x", center=(0.0, 0.0))
    assert _close(out[0][0], 1.0) and _close(out[0][1], -2.0)


def test_mirror_y():
    pts = [(2.0, 1.0)]
    out = vt.mirror(pts, axis="y", center=(0.0, 0.0))
    assert _close(out[0][0], -2.0) and _close(out[0][1], 1.0)


def test_mirror_angle():
    pts = [(1.0, 0.0)]
    out = vt.mirror(pts, axis="angle", angle_deg=45.0, center=(0.0, 0.0))
    assert _close(out[0][0], 0.0) and _close(out[0][1], 1.0)


def test_shear():
    pts = [(0.0, 1.0)]
    out = vt.shear(pts, shx=1.0, center=(0.0, 0.0))
    assert _close(out[0][0], 1.0) and _close(out[0][1], 1.0)


def test_translate():
    pts = [(1.0, 2.0)]
    out = vt.translate(pts, dx=10.0, dy=-5.0)
    assert _close(out[0][0], 11.0) and _close(out[0][1], -3.0)


def test_jitter_deterministic():
    pts = _square()
    out1 = vt.jitter(pts, radius=1.0, seed=42)
    out2 = vt.jitter(pts, radius=1.0, seed=42)
    for (x1, y1), (x2, y2) in zip(out1, out2):
        assert _close(x1, x2) and _close(y1, y2)


def test_jitter_bounded():
    pts = [(0.0, 0.0)] * 100
    out = vt.jitter(pts, radius=5.0, seed=1)
    for x, y in out:
        assert math.sqrt(x**2 + y**2) <= 5.0 + 1e-9


def test_chain():
    pts = _square()
    result = vt.chain_transforms(pts, [
        ("rotate", {"angle_deg": 90}),
        ("scale", {"sx": 2.0}),
    ])
    assert result.transforms_applied == ["rotate", "scale"]
    assert len(result.transformed) == 4


def test_format_report():
    pts = _square()
    result = vt.chain_transforms(pts, [("translate", {"dx": 10})])
    report = vt.format_report(result)
    assert "Transform Report" in report
    assert "translate" in report


def test_render_svg():
    pts = _square()
    result = vt.chain_transforms(pts, [("rotate", {"angle_deg": 45})])
    svg = vt.render_svg(result, show_voronoi=False)
    assert "<svg" in svg
    assert "Original" in svg
    assert "Transformed" in svg


def test_save_and_load(tmp_path):
    pts = _square()
    out_file = str(tmp_path / "out.txt")
    vt.save_points(pts, out_file)
    with open(out_file) as f:
        lines = f.readlines()
    assert len(lines) == 4
    x, y = lines[0].strip().split()
    assert _close(float(x), 0.0) and _close(float(y), 0.0)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
