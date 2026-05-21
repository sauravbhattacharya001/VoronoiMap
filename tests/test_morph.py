"""Tests for vormap_morph — Voronoi point-set morphing.

Covers:
- All easing functions (boundary behaviour at t=0, t=1, monotonic-ish midpoint).
- ``match_points`` greedy matching (equal sizes, unmatched leftovers).
- ``interpolate_points`` at the three key parameter values.
- All four built-in target generators.
- ``morph()`` and ``auto_morph()`` end-to-end HTML emission.
- ``MorphConfig`` defaults.
"""

import os
import sys
import tempfile
import random

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vormap_morph as vm


# ---------------------------------------------------------------------------
# Easing functions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(vm.EASING_MAP.keys()))
def test_easing_endpoints(name):
    """Every easing function must pin to 0 at t=0 and 1 at t=1."""
    fn = vm.EASING_MAP[name]
    assert fn(0.0) == pytest.approx(0.0, abs=1e-9)
    assert fn(1.0) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("name", ["linear", "ease_in_out", "cubic_bezier"])
def test_easing_smooth_monotonic_midrange(name):
    """Smooth (non-elastic, non-bounce) easings stay in [0, 1] over [0, 1]."""
    fn = vm.EASING_MAP[name]
    for k in range(11):
        t = k / 10.0
        val = fn(t)
        assert -1e-9 <= val <= 1 + 1e-9, f"{name}({t}) = {val} out of range"


def test_easing_map_covers_documented_set():
    """EASING_FUNCTIONS and EASING_MAP must agree."""
    assert set(vm.EASING_FUNCTIONS) == set(vm.EASING_MAP.keys())


# ---------------------------------------------------------------------------
# match_points
# ---------------------------------------------------------------------------

def test_match_points_equal_size_identity():
    """Identical source and target → each point matches its twin."""
    pts = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]
    matching = vm.match_points(pts, pts)
    assert len(matching) == 3
    # All pairs should be (i, i) — every point's nearest neighbour is itself.
    for si, ti in matching:
        assert si is not None and ti is not None
        assert si == ti


def test_match_points_more_source_than_target():
    """Extra source points get (idx, None) entries."""
    src = [(0.0, 0.0), (1.0, 0.0), (100.0, 100.0)]
    tgt = [(0.0, 0.1), (1.0, 0.1)]
    matching = vm.match_points(src, tgt)
    matched = [(s, t) for s, t in matching if s is not None and t is not None]
    unmatched_src = [s for s, t in matching if s is not None and t is None]
    unmatched_tgt = [t for s, t in matching if s is None and t is not None]
    assert len(matched) == 2
    assert len(unmatched_src) == 1
    assert unmatched_src[0] == 2  # the far-away point
    assert unmatched_tgt == []


def test_match_points_more_target_than_source():
    """Extra target points get (None, idx) entries."""
    src = [(0.0, 0.0)]
    tgt = [(0.0, 0.1), (50.0, 50.0)]
    matching = vm.match_points(src, tgt)
    matched = [(s, t) for s, t in matching if s is not None and t is not None]
    unmatched_tgt = [t for s, t in matching if s is None and t is not None]
    assert len(matched) == 1
    assert len(unmatched_tgt) == 1
    assert unmatched_tgt[0] == 1


def test_match_points_empty():
    """Both empty → empty matching."""
    assert vm.match_points([], []) == []


def test_match_points_no_duplicate_indices():
    """Each source/target index appears at most once across matched pairs."""
    random.seed(7)
    src = [(random.random() * 100, random.random() * 100) for _ in range(20)]
    tgt = [(random.random() * 100, random.random() * 100) for _ in range(20)]
    matching = vm.match_points(src, tgt)
    matched_src = [s for s, t in matching if s is not None and t is not None]
    matched_tgt = [t for s, t in matching if s is not None and t is not None]
    assert len(matched_src) == len(set(matched_src))
    assert len(matched_tgt) == len(set(matched_tgt))


# ---------------------------------------------------------------------------
# interpolate_points
# ---------------------------------------------------------------------------

def test_interpolate_at_t_zero_matches_source():
    """With ``linear`` easing, t=0 must reproduce source coords exactly."""
    src = [(0.0, 0.0), (100.0, 100.0)]
    tgt = [(50.0, 50.0), (200.0, 200.0)]
    matching = vm.match_points(src, tgt)
    out = vm.interpolate_points(src, tgt, matching, 0.0, easing="linear")
    assert len(out) == 2
    for (x, y, _alpha), (sx, sy) in zip(out, src):
        assert x == pytest.approx(sx)
        assert y == pytest.approx(sy)


def test_interpolate_at_t_one_matches_target():
    """With ``linear`` easing, t=1 must reproduce matched target coords."""
    src = [(0.0, 0.0), (100.0, 100.0)]
    tgt = [(50.0, 50.0), (200.0, 200.0)]
    matching = vm.match_points(src, tgt)
    out = vm.interpolate_points(src, tgt, matching, 1.0, easing="linear")
    # Each matched pair lands exactly on its target.
    matched_pairs = [(si, ti) for si, ti in matching if si is not None and ti is not None]
    for (x, y, _alpha), (_, ti) in zip(out, matched_pairs):
        tx, ty = tgt[ti]
        assert x == pytest.approx(tx)
        assert y == pytest.approx(ty)


def test_interpolate_clamps_out_of_range_t():
    """t outside [0,1] is clamped — t=-1 behaves like t=0, t=2 like t=1."""
    src = [(0.0, 0.0)]
    tgt = [(10.0, 10.0)]
    matching = vm.match_points(src, tgt)
    a = vm.interpolate_points(src, tgt, matching, -1.0, easing="linear")
    b = vm.interpolate_points(src, tgt, matching, 0.0, easing="linear")
    c = vm.interpolate_points(src, tgt, matching, 2.0, easing="linear")
    d = vm.interpolate_points(src, tgt, matching, 1.0, easing="linear")
    assert a[0][:2] == pytest.approx(b[0][:2])
    assert c[0][:2] == pytest.approx(d[0][:2])


def test_interpolate_unmatched_source_fades_out():
    """Source points without a target have opacity 0 at t=1."""
    src = [(400.0, 300.0), (10.0, 10.0)]  # second is unmatched
    tgt = [(400.0, 300.0)]  # only one target
    matching = vm.match_points(src, tgt)
    out = vm.interpolate_points(src, tgt, matching, 1.0, easing="linear",
                                width=800, height=600)
    # Find the entry whose source index is unmatched.
    unmatched = [(s, t) for s, t in matching if s is not None and t is None]
    assert len(unmatched) == 1
    # Among the output, exactly one entry should have alpha 0.
    alphas = sorted(alpha for _, _, alpha in out)
    assert alphas[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Target generators
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,gen", list(vm._TARGET_GENERATORS.items()))
def test_target_generators_produce_n_points_in_bounds(name, gen):
    """Each built-in generator emits exactly n points inside the canvas (with
    a small slack for ``clustered`` which uses Gaussian noise)."""
    random.seed(name)  # deterministic per-generator
    n, w, h = 25, 400, 300
    pts = gen(n, w, h)
    assert len(pts) == n, f"{name}: expected {n} points, got {len(pts)}"
    if name == "clustered":
        # Gaussian samples can escape the canvas; only require they're finite.
        for x, y in pts:
            assert -1e6 < x < 1e6 and -1e6 < y < 1e6
    else:
        for x, y in pts:
            assert -1e-6 <= x <= w + 1e-6, f"{name}: x={x} out of [0,{w}]"
            assert -1e-6 <= y <= h + 1e-6, f"{name}: y={y} out of [0,{h}]"


def test_pick_best_target_maximises_displacement():
    """``_pick_best_target`` returns a layout with positive total displacement
    relative to the source (i.e. it actually picks *something* that moves)."""
    src = [(200.0, 150.0)] * 10  # all stacked — any layout will spread them out
    out = vm._pick_best_target(src, 400, 300)
    assert len(out) == 10
    # Spread should be greater than 0 — at least one chosen point differs from src.
    assert any((x, y) != (200.0, 150.0) for x, y in out)


# ---------------------------------------------------------------------------
# morph / auto_morph end-to-end
# ---------------------------------------------------------------------------

def _read_html(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_morph_writes_html_file(tmp_path):
    """``morph()`` writes a self-contained HTML file with embedded data."""
    src = [(50.0, 50.0), (100.0, 100.0), (200.0, 50.0)]
    tgt = [(80.0, 80.0), (150.0, 50.0), (200.0, 150.0)]
    out = tmp_path / "morph.html"
    result = vm.morph(src, tgt, str(out), config=vm.MorphConfig(width=400, height=300))
    assert os.path.exists(result)
    html = _read_html(result)
    assert "<!DOCTYPE html>" in html
    assert "<canvas" in html or "<svg" in html or "Voronoi" in html


def test_auto_morph_picks_some_target(tmp_path):
    """``auto_morph()`` chooses a target and writes a file."""
    random.seed(42)
    src = [(random.uniform(0, 400), random.uniform(0, 300)) for _ in range(8)]
    out = tmp_path / "auto.html"
    result = vm.auto_morph(src, str(out), config=vm.MorphConfig(width=400, height=300))
    assert os.path.exists(result)
    assert os.path.getsize(result) > 0


def test_morph_config_defaults():
    """Sanity-check MorphConfig defaults haven't drifted into nonsense."""
    cfg = vm.MorphConfig()
    assert cfg.width > 0 and cfg.height > 0
    assert cfg.fps > 0
    assert cfg.duration_seconds > 0
    assert cfg.easing in vm.EASING_FUNCTIONS
    assert cfg.color_scheme in vm.COLOR_SCHEMES
