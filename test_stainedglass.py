"""Tests for vormap_stainedglass module."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import vormap_stainedglass


def test_generate_default():
    result = vormap_stainedglass.generate(100, 80, num_seeds=8, seed=42)
    assert result.width == 100
    assert result.height == 80
    assert len(result.seeds) == 8
    assert len(result.pixels) == 100 * 80 * 3


def test_palettes():
    for name in vormap_stainedglass.PALETTES:
        result = vormap_stainedglass.generate(40, 40, num_seeds=4, palette=name, seed=1)
        assert len(result.pixels) == 40 * 40 * 3


def test_custom_palette():
    result = vormap_stainedglass.generate(
        40, 40, num_seeds=4, palette="#ff0000,#00ff00,#0000ff", seed=1
    )
    assert len(result.seeds) == 4


def test_light_source():
    result = vormap_stainedglass.generate(
        60, 60, num_seeds=5, light_angle=45, light_strength=0.5, seed=7
    )
    assert result.light_angle == 45


def test_grain_and_bevel():
    result = vormap_stainedglass.generate(
        50, 50, num_seeds=5, grain=True, bevel=True, seed=3
    )
    assert len(result.pixels) == 50 * 50 * 3


def test_save_png():
    result = vormap_stainedglass.generate(30, 30, num_seeds=4, seed=10)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        vormap_stainedglass.save_png(result, path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
        with open(path, "rb") as f:
            assert f.read(4) == b"\x89PNG"
    finally:
        os.unlink(path)


def test_cell_areas():
    result = vormap_stainedglass.generate(50, 50, num_seeds=5, seed=99)
    total = sum(result.cell_areas.values())
    assert total == 50 * 50


if __name__ == "__main__":
    test_generate_default()
    test_palettes()
    test_custom_palette()
    test_light_source()
    test_grain_and_bevel()
    test_save_png()
    test_cell_areas()
    print("All tests passed!")
