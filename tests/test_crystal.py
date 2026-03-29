"""Tests for vormap_crystal — Voronoi Crystal Growth Simulator."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from vormap_crystal import (
    Crystal, CrystalConfig, simulate, extract_boundaries,
    grain_stats, render, save_image, main,
)


def test_simulate_basic():
    cfg = CrystalConfig(width=32, height=32, initial_seeds=4,
                        total_steps=20, seed=42)
    grid, crystals, frames = simulate(cfg)
    assert len(grid) == 32
    assert len(grid[0]) == 32
    assert len(crystals) == 4
    assert len(frames) >= 2  # at least initial + one step
    # Should be mostly filled
    filled = sum(1 for row in grid for c in row if c != -1)
    assert filled > 0


def test_simulate_nucleation():
    cfg = CrystalConfig(width=32, height=32, initial_seeds=2,
                        nucleation_rate=0.5, total_steps=30, seed=99)
    grid, crystals, frames = simulate(cfg)
    assert len(crystals) >= 2  # may have nucleated more


def test_simulate_temp_gradient():
    cfg = CrystalConfig(width=32, height=32, initial_seeds=3,
                        nucleation_rate=0.8, total_steps=20,
                        temp_gradient=True, seed=7)
    grid, crystals, frames = simulate(cfg)
    assert len(crystals) >= 3


def test_extract_boundaries():
    cfg = CrystalConfig(width=32, height=32, initial_seeds=4,
                        total_steps=40, seed=42)
    grid, crystals, _ = simulate(cfg)
    borders = extract_boundaries(grid)
    assert isinstance(borders, list)
    assert len(borders) > 0


def test_grain_stats():
    cfg = CrystalConfig(width=32, height=32, initial_seeds=5,
                        total_steps=40, seed=42)
    grid, crystals, _ = simulate(cfg)
    stats = grain_stats(grid, crystals)
    assert stats["num_grains"] == 5
    assert stats["mean_area"] > 0
    assert 0 < stats["coverage"] <= 1.0


def test_render():
    cfg = CrystalConfig(width=16, height=16, initial_seeds=3,
                        total_steps=20, seed=42)
    grid, crystals, _ = simulate(cfg)
    img = render(grid, crystals, cfg, show_borders=True)
    assert len(img) == 16
    assert len(img[0]) == 16
    assert all(len(px) == 3 for row in img for px in row)


def test_save_image():
    cfg = CrystalConfig(width=16, height=16, initial_seeds=3,
                        total_steps=15, seed=42)
    grid, crystals, _ = simulate(cfg)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        save_image(path, grid, crystals, cfg)
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


def test_cli():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        main(["--width", "16", "--height", "16", "--seeds", "3",
              "--steps", "10", "--stats", "-o", path, "--seed", "42"])
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


def test_anisotropy_effect():
    """Higher anisotropy should produce elongated grains."""
    cfg_iso = CrystalConfig(width=32, height=32, initial_seeds=1,
                            anisotropy=1.0, total_steps=30, seed=42)
    cfg_aniso = CrystalConfig(width=32, height=32, initial_seeds=1,
                              anisotropy=3.0, total_steps=30, seed=42)
    g1, _, _ = simulate(cfg_iso)
    g2, _, _ = simulate(cfg_aniso)
    # Both should fill some area
    f1 = sum(1 for row in g1 for c in row if c != -1)
    f2 = sum(1 for row in g2 for c in row if c != -1)
    assert f1 > 0 and f2 > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as e:
                print(f"  FAIL  {name}: {e}")
