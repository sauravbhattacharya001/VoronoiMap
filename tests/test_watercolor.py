"""Tests for vormap_watercolor module."""
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import vormap_watercolor


def test_generate_default():
    r = vormap_watercolor.generate(100, 80, num_seeds=5, seed=42)
    assert r.width == 100
    assert r.height == 80
    assert len(r.pixels) == 100 * 80
    assert len(r.seeds) == 5


def test_all_palettes():
    for name in vormap_watercolor.PALETTES:
        r = vormap_watercolor.generate(40, 30, num_seeds=3, palette=name, seed=1)
        assert len(r.pixels) == 40 * 30


def test_custom_palette():
    r = vormap_watercolor.generate(40, 30, num_seeds=3, palette="#ff0000,#00ff00,#0000ff", seed=1)
    assert r.palette_name == "custom"


def test_options():
    r = vormap_watercolor.generate(60, 50, num_seeds=4, bleed=15, paper=False,
                                    splatter=True, wet_edge=True, seed=7)
    assert len(r.pixels) == 60 * 50


def test_save_png():
    r = vormap_watercolor.generate(40, 30, num_seeds=3, seed=42)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        vormap_watercolor.save_png(r, path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 100
        with open(path, "rb") as f:
            assert f.read(4) == b"\x89PNG"
    finally:
        os.unlink(path)


def test_cell_areas():
    r = vormap_watercolor.generate(50, 50, num_seeds=4, seed=10)
    assert sum(r.cell_areas.values()) == 50 * 50
