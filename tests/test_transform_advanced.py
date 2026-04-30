"""Extended tests for vormap_transform — affine internals, composition, edge cases.

Covers: _centroid, _bbox, _affine_compose, _affine_apply, _affine_around,
to_affine_matrix, chain_transforms with mixed affine+jitter pipelines,
mirror at arbitrary angles, scale with default center, shear composition,
and the TransformResult dataclass fields.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_transform as vt


# ── Helpers ──────────────────────────────────────────────────────────

def _close(a, b, tol=1e-9):
    return abs(a - b) < tol


def _pts_close(a, b, tol=1e-9):
    """Check two point lists are element-wise close."""
    if len(a) != len(b):
        return False
    return all(_close(x1, x2, tol) and _close(y1, y2, tol)
               for (x1, y1), (x2, y2) in zip(a, b))


def _triangle():
    return [(0.0, 0.0), (4.0, 0.0), (2.0, 3.0)]


def _square():
    return [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


# ── _centroid ────────────────────────────────────────────────────────

def test_centroid_empty():
    assert vt._centroid([]) == (0.0, 0.0)


def test_centroid_single_point():
    c = vt._centroid([(5.0, 7.0)])
    assert _close(c[0], 5.0) and _close(c[1], 7.0)


def test_centroid_symmetric():
    c = vt._centroid(_square())
    assert _close(c[0], 0.5) and _close(c[1], 0.5)


def test_centroid_triangle():
    c = vt._centroid(_triangle())
    assert _close(c[0], 2.0) and _close(c[1], 1.0)


# ── _bbox ────────────────────────────────────────────────────────────

def test_bbox_square():
    b = vt._bbox(_square())
    assert b == (0.0, 0.0, 1.0, 1.0)


def test_bbox_negative_coords():
    b = vt._bbox([(-5.0, -3.0), (2.0, 10.0), (0.0, 0.0)])
    assert b == (-5.0, -3.0, 2.0, 10.0)


# ── _affine_compose ──────────────────────────────────────────────────

def test_compose_identity():
    """Composing with identity leaves transform unchanged."""
    m = (2.0, 0.0, 3.0, 0.0, 2.0, -1.0)
    result = vt._affine_compose(m, vt._IDENTITY)
    for a, b in zip(result, m):
        assert _close(a, b)


def test_compose_identity_left():
    m = (2.0, 0.0, 3.0, 0.0, 2.0, -1.0)
    result = vt._affine_compose(vt._IDENTITY, m)
    for a, b in zip(result, m):
        assert _close(a, b)


def test_compose_two_translations():
    """Two translations compose by adding offsets."""
    t1 = (1.0, 0.0, 5.0, 0.0, 1.0, 3.0)
    t2 = (1.0, 0.0, -2.0, 0.0, 1.0, 7.0)
    result = vt._affine_compose(t1, t2)
    assert _close(result[2], 3.0)  # tx = 5 + (-2)
    assert _close(result[5], 10.0)  # ty = 3 + 7


def test_compose_scale_then_translate():
    """Scale(2) then Translate(10,0) = Translate(10,0)·Scale(2)."""
    s = (2.0, 0.0, 0.0, 0.0, 2.0, 0.0)
    t = (1.0, 0.0, 10.0, 0.0, 1.0, 0.0)
    # In composed form: first apply s, then t → compose(t, s)
    composed = vt._affine_compose(t, s)
    pts = [(1.0, 1.0)]
    out = vt._affine_apply(composed, pts)
    # Expected: scale(1,1) = (2,2), then translate = (12, 2)
    assert _close(out[0][0], 12.0) and _close(out[0][1], 2.0)


# ── _affine_apply ────────────────────────────────────────────────────

def test_apply_identity():
    pts = _square()
    out = vt._affine_apply(vt._IDENTITY, pts)
    assert _pts_close(pts, out)


def test_apply_scale():
    pts = [(3.0, 4.0)]
    m = (2.0, 0.0, 0.0, 0.0, 3.0, 0.0)
    out = vt._affine_apply(m, pts)
    assert _close(out[0][0], 6.0) and _close(out[0][1], 12.0)


# ── _affine_around ──────────────────────────────────────────────────

def test_affine_around_origin():
    """Around origin should be same as raw linear transform."""
    m = vt._affine_around((0.0, 0.0), 2.0, 0.0, 0.0, 2.0)
    assert _close(m[0], 2.0)
    assert _close(m[2], 0.0)  # no translation


def test_affine_around_nonzero_pivot():
    """Scale by 2 around (5, 5) should leave (5,5) fixed."""
    m = vt._affine_around((5.0, 5.0), 2.0, 0.0, 0.0, 2.0)
    out = vt._affine_apply(m, [(5.0, 5.0)])
    assert _close(out[0][0], 5.0) and _close(out[0][1], 5.0)


def test_affine_around_rotation_pivot():
    """90° rotation around (1,0) should map (2,0) to (1,1)."""
    rad = math.radians(90)
    c, s = math.cos(rad), math.sin(rad)
    m = vt._affine_around((1.0, 0.0), c, -s, s, c)
    out = vt._affine_apply(m, [(2.0, 0.0)])
    assert _close(out[0][0], 1.0) and _close(out[0][1], 1.0)


# ── to_affine_matrix ────────────────────────────────────────────────

def test_to_affine_rotate():
    m = vt.to_affine_matrix("rotate", angle_deg=180, pivot=(0.0, 0.0))
    out = vt._affine_apply(m, [(1.0, 0.0)])
    assert _close(out[0][0], -1.0) and _close(out[0][1], 0.0, 1e-6)


def test_to_affine_scale():
    m = vt.to_affine_matrix("scale", sx=3.0, sy=0.5, center=(0.0, 0.0))
    out = vt._affine_apply(m, [(2.0, 4.0)])
    assert _close(out[0][0], 6.0) and _close(out[0][1], 2.0)


def test_to_affine_mirror_x():
    m = vt.to_affine_matrix("mirror", axis="x", center=(0.0, 0.0))
    out = vt._affine_apply(m, [(3.0, 5.0)])
    assert _close(out[0][0], 3.0) and _close(out[0][1], -5.0)


def test_to_affine_mirror_angle():
    m = vt.to_affine_matrix("mirror", axis="angle", angle_deg=0.0, center=(0.0, 0.0))
    out = vt._affine_apply(m, [(0.0, 1.0)])
    # Mirror across angle=0 (x-axis) → y flips
    assert _close(out[0][0], 0.0) and _close(out[0][1], -1.0)


def test_to_affine_shear():
    m = vt.to_affine_matrix("shear", shx=2.0, shy=0.0, center=(0.0, 0.0))
    out = vt._affine_apply(m, [(0.0, 3.0)])
    assert _close(out[0][0], 6.0) and _close(out[0][1], 3.0)


def test_to_affine_translate():
    m = vt.to_affine_matrix("translate", dx=7.0, dy=-3.0)
    out = vt._affine_apply(m, [(1.0, 1.0)])
    assert _close(out[0][0], 8.0) and _close(out[0][1], -2.0)


def test_to_affine_jitter_raises():
    import pytest
    with pytest.raises(ValueError, match="jitter"):
        vt.to_affine_matrix("jitter")


def test_to_affine_unknown_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown"):
        vt.to_affine_matrix("warp")


# ── Composition: rotate then scale equals single affine ─────────────

def test_composed_rotate_scale_matches_sequential():
    pts = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0)]
    # Apply sequentially
    r = vt.rotate(pts, 45.0, pivot=(0.0, 0.0))
    rs = vt.scale(r, sx=2.0, center=(0.0, 0.0))
    # Compose matrices
    m1 = vt.to_affine_matrix("rotate", angle_deg=45, pivot=(0.0, 0.0))
    m2 = vt.to_affine_matrix("scale", sx=2.0, center=(0.0, 0.0))
    composed = vt._affine_compose(m2, m1)
    out = vt._affine_apply(composed, pts)
    assert _pts_close(rs, out, tol=1e-6)


# ── chain_transforms ────────────────────────────────────────────────

def test_chain_empty_steps():
    pts = _square()
    result = vt.chain_transforms(pts, [])
    assert _pts_close(result.original, pts)
    assert _pts_close(result.transformed, pts)
    assert result.transforms_applied == []


def test_chain_single_translate():
    pts = [(0.0, 0.0)]
    result = vt.chain_transforms(pts, [("translate", {"dx": 5.0, "dy": 3.0})])
    assert _close(result.transformed[0][0], 5.0)
    assert _close(result.transformed[0][1], 3.0)


def test_chain_preserves_point_count():
    pts = [(float(i), float(i)) for i in range(50)]
    result = vt.chain_transforms(pts, [
        ("rotate", {"angle_deg": 30}),
        ("scale", {"sx": 0.5}),
        ("translate", {"dx": 10}),
    ])
    assert len(result.transformed) == 50


def test_chain_with_jitter_breaks_affine_batch():
    """Jitter in the middle forces flush of affine batch."""
    pts = [(0.0, 0.0), (10.0, 0.0)]
    result = vt.chain_transforms(pts, [
        ("translate", {"dx": 5.0}),
        ("jitter", {"radius": 0.001, "seed": 99}),
        ("translate", {"dx": 5.0}),
    ])
    # After translate(5), jitter(~0), translate(5): ~(10, 0) and ~(20, 0)
    assert result.transformed[0][0] > 9.0
    assert result.transformed[1][0] > 19.0
    assert result.transforms_applied == ["translate", "jitter", "translate"]


def test_chain_unknown_transform_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown"):
        vt.chain_transforms([(0, 0)], [("nonexistent", {})])


# ── TransformResult fields ──────────────────────────────────────────

def test_result_bounding_boxes():
    pts = _square()
    result = vt.chain_transforms(pts, [("translate", {"dx": 10.0, "dy": 5.0})])
    assert result.bounding_box_before == (0.0, 0.0, 1.0, 1.0)
    assert _close(result.bounding_box_after[0], 10.0)
    assert _close(result.bounding_box_after[1], 5.0)
    assert _close(result.bounding_box_after[2], 11.0)
    assert _close(result.bounding_box_after[3], 6.0)


def test_result_centroids():
    pts = [(0.0, 0.0), (10.0, 0.0)]
    result = vt.chain_transforms(pts, [("translate", {"dx": 0.0, "dy": 20.0})])
    assert _close(result.centroid_before[0], 5.0)
    assert _close(result.centroid_before[1], 0.0)
    assert _close(result.centroid_after[0], 5.0)
    assert _close(result.centroid_after[1], 20.0)


# ── Mirror edge cases ───────────────────────────────────────────────

def test_mirror_double_is_identity():
    """Mirroring twice across same axis returns original."""
    pts = [(1.0, 2.0), (3.0, 4.0), (5.0, -1.0)]
    once = vt.mirror(pts, axis="y", center=(0.0, 0.0))
    twice = vt.mirror(once, axis="y", center=(0.0, 0.0))
    assert _pts_close(pts, twice, tol=1e-9)


def test_mirror_angle_45():
    """Mirror across 45° line maps (1,0) to (0,1)."""
    pts = [(1.0, 0.0)]
    out = vt.mirror(pts, axis="angle", angle_deg=45.0, center=(0.0, 0.0))
    assert _close(out[0][0], 0.0, 1e-6) and _close(out[0][1], 1.0, 1e-6)


def test_mirror_angle_90():
    """Mirror across 90° line (y-axis) maps (1,0) to (-1,0)."""
    pts = [(1.0, 0.0)]
    out = vt.mirror(pts, axis="angle", angle_deg=90.0, center=(0.0, 0.0))
    assert _close(out[0][0], -1.0, 1e-6) and _close(out[0][1], 0.0, 1e-6)


def test_mirror_invalid_axis_raises():
    import pytest
    with pytest.raises(ValueError):
        vt.mirror([(0, 0)], axis="z")


# ── Scale edge cases ────────────────────────────────────────────────

def test_scale_zero():
    """Scale by 0 collapses all points to the center."""
    pts = _square()
    center = (0.5, 0.5)
    out = vt.scale(pts, sx=0.0, center=center)
    for x, y in out:
        assert _close(x, 0.5) and _close(y, 0.5)


def test_scale_negative_flips():
    """Negative scale is like mirror + scale."""
    pts = [(2.0, 0.0)]
    out = vt.scale(pts, sx=-1.0, center=(0.0, 0.0))
    assert _close(out[0][0], -2.0) and _close(out[0][1], 0.0)


# ── Shear edge cases ────────────────────────────────────────────────

def test_shear_both_axes():
    pts = [(1.0, 1.0)]
    out = vt.shear(pts, shx=1.0, shy=1.0, center=(0.0, 0.0))
    # x' = x + shx*y = 1 + 1 = 2, y' = shy*x + y = 1 + 1 = 2
    assert _close(out[0][0], 2.0) and _close(out[0][1], 2.0)


def test_shear_preserves_area():
    """Shear is area-preserving (det = 1 - shx*shy = 1 when shy=0)."""
    pts = _square()
    out = vt.shear(pts, shx=3.0, shy=0.0, center=(0.0, 0.0))
    # Compute "area" via shoelace on original and sheared
    def shoelace(p):
        n = len(p)
        s = sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1] for i in range(n))
        return abs(s) / 2.0
    assert _close(shoelace(pts), shoelace(out), tol=1e-6)


# ── Rotation properties ─────────────────────────────────────────────

def test_rotate_preserves_distances():
    """Rotation preserves pairwise distances."""
    pts = _triangle()
    out = vt.rotate(pts, 73.0, pivot=(0.0, 0.0))
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d_orig = math.dist(pts[i], pts[j])
            d_rot = math.dist(out[i], out[j])
            assert _close(d_orig, d_rot, tol=1e-6)


def test_rotate_180_double_negation():
    pts = [(3.0, 7.0), (-2.0, 1.0)]
    out = vt.rotate(pts, 180.0, pivot=(0.0, 0.0))
    for (x, y), (x2, y2) in zip(pts, out):
        assert _close(x2, -x, 1e-6) and _close(y2, -y, 1e-6)


# ── format_report ───────────────────────────────────────────────────

def test_format_report_content():
    result = vt.chain_transforms([(0, 0), (1, 1)], [
        ("rotate", {"angle_deg": 45}),
        ("scale", {"sx": 2}),
    ])
    report = vt.format_report(result)
    assert "rotate" in report
    assert "scale" in report
    assert "Centroid" in report or "centroid" in report.lower()


# ── save_points round-trip ──────────────────────────────────────────

def test_save_points_precision(tmp_path):
    pts = [(1.123456789, 2.987654321), (0.0, -99.5)]
    out_file = str(tmp_path / "pts.txt")
    vt.save_points(pts, out_file)
    with open(out_file) as f:
        lines = f.readlines()
    assert len(lines) == 2
    # Verify values are recoverable
    x, y = map(float, lines[0].split())
    assert _close(x, 1.123456789, tol=1e-6)
    assert _close(y, 2.987654321, tol=1e-6)


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
