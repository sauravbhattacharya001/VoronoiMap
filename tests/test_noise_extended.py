"""Extended tests for vormap_noise — private helpers, edge cases, and PNG export."""

import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_noise


# ── _lerp_color ──────────────────────────────────────────────────────

def test_lerp_color_endpoints():
    colors = [(0, 0, 0), (255, 255, 255)]
    assert vormap_noise._lerp_color(colors, 0.0) == (0, 0, 0)
    assert vormap_noise._lerp_color(colors, 1.0) == (255, 255, 255)


def test_lerp_color_midpoint():
    colors = [(0, 0, 0), (200, 100, 50)]
    r, g, b = vormap_noise._lerp_color(colors, 0.5)
    assert r == 100
    assert g == 50
    assert b == 25


def test_lerp_color_clamps_out_of_range():
    colors = [(10, 20, 30), (100, 200, 255)]
    assert vormap_noise._lerp_color(colors, -0.5) == (10, 20, 30)
    assert vormap_noise._lerp_color(colors, 1.5) == (100, 200, 255)


def test_lerp_color_three_stops():
    colors = [(0, 0, 0), (100, 100, 100), (200, 200, 200)]
    mid = vormap_noise._lerp_color(colors, 0.5)
    assert mid == (100, 100, 100)


# ── _generate_seeds ─────────────────────────────────────────────────

def test_generate_seeds_count():
    import random
    seeds = vormap_noise._generate_seeds(20, 100, 100, rng=random.Random(42))
    assert len(seeds) == 20


def test_generate_seeds_bounds():
    import random
    seeds = vormap_noise._generate_seeds(50, 200, 300, rng=random.Random(7))
    for x, y in seeds:
        assert 0 <= x <= 200
        assert 0 <= y <= 300


def test_generate_seeds_deterministic():
    import random
    a = vormap_noise._generate_seeds(10, 50, 50, rng=random.Random(99))
    b = vormap_noise._generate_seeds(10, 50, 50, rng=random.Random(99))
    assert a == b


# ── _tile_seeds ──────────────────────────────────────────────────────

def test_tile_seeds_multiplies_by_nine():
    seeds = [(10, 20), (30, 40)]
    tiled = vormap_noise._tile_seeds(seeds, 100, 100)
    assert len(tiled) == 18  # 2 * 9


def test_tile_seeds_contains_originals():
    seeds = [(50, 50)]
    tiled = vormap_noise._tile_seeds(seeds, 100, 100)
    assert (50, 50) in tiled


# ── Distance functions ───────────────────────────────────────────────

def test_euclidean_zero():
    assert vormap_noise._euclidean(5, 5, 5, 5) == 0.0


def test_euclidean_345():
    assert abs(vormap_noise._euclidean(0, 0, 3, 4) - 5.0) < 1e-9


def test_manhattan():
    assert vormap_noise._manhattan(1, 2, 4, 6) == 7


def test_chebyshev():
    assert vormap_noise._chebyshev(1, 2, 4, 6) == 4


# ── _compute_distances ──────────────────────────────────────────────

def test_compute_distances_sorted():
    seeds = [(0, 0), (10, 0), (5, 0)]
    dists = vormap_noise._compute_distances(3, 0, seeds, vormap_noise._euclidean, k=3)
    assert dists == sorted(dists)


def test_compute_distances_k_limit():
    seeds = [(i, 0) for i in range(20)]
    dists = vormap_noise._compute_distances(0, 0, seeds, vormap_noise._euclidean, k=2)
    assert len(dists) == 2


# ── generate edge cases ─────────────────────────────────────────────

def test_generate_single_seed():
    img = vormap_noise.generate(width=8, height=8, num_seeds=1, seed=1)
    assert len(img) == 8
    assert len(img[0]) == 8


def test_generate_unknown_colormap_falls_back():
    """Unknown colormap should fall back to grayscale without error."""
    img = vormap_noise.generate(width=8, height=8, num_seeds=5, seed=1,
                                colormap="nonexistent")
    assert len(img) == 8


def test_generate_octaves_changes_output():
    img1 = vormap_noise.generate(width=16, height=16, num_seeds=10, seed=42,
                                 octaves=1)
    img3 = vormap_noise.generate(width=16, height=16, num_seeds=10, seed=42,
                                 octaves=3)
    diffs = sum(1 for y in range(16) for x in range(16)
                if img1[y][x] != img3[y][x])
    assert diffs > 0


def test_generate_deterministic():
    a = vormap_noise.generate(width=16, height=16, num_seeds=10, seed=123)
    b = vormap_noise.generate(width=16, height=16, num_seeds=10, seed=123)
    assert a == b


def test_generate_pixel_values_valid():
    img = vormap_noise.generate(width=16, height=16, num_seeds=8, seed=5)
    for row in img:
        for r, g, b in row:
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255


# ── generate_from_points ─────────────────────────────────────────────

def test_generate_from_points_all_modes():
    pts = [(10, 10), (50, 50), (90, 10), (50, 90)]
    for mode in vormap_noise.MODES:
        img = vormap_noise.generate_from_points(pts, width=16, height=16,
                                                mode=mode)
        assert len(img) == 16


# ── save_png ─────────────────────────────────────────────────────────

def test_save_png():
    """Test PNG export (skips if Pillow not installed)."""
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return  # skip
    img = vormap_noise.generate(width=8, height=8, num_seeds=4, seed=1)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        vormap_noise.save_png(img, path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


def test_save_auto_format():
    """save() dispatches by extension."""
    img = vormap_noise.generate(width=8, height=8, num_seeds=4, seed=1)
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as f:
        path = f.name
    try:
        vormap_noise.save(img, path)
        assert os.path.exists(path)
        with open(path, "rb") as f:
            assert f.read(2) == b"P6"
    finally:
        os.unlink(path)


# ── CLI ──────────────────────────────────────────────────────────────

def test_cli_with_mode_and_colormap():
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as f:
        path = f.name
    try:
        vormap_noise.main(["-W", "16", "-H", "16", "-n", "5", "-s", "1",
                           "--mode", "f2-f1", "--colormap", "fire",
                           "-o", path])
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


def test_cli_tiled_octaves():
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as f:
        path = f.name
    try:
        vormap_noise.main(["-W", "16", "-H", "16", "-n", "5", "-s", "1",
                           "--tiled", "--octaves", "2", "-o", path])
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


if __name__ == "__main__":
    import inspect
    tests = [obj for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    for t in tests:
        t()
    print(f"All {len(tests)} extended noise tests passed!")
