"""Tests for vormap_crossstitch — Voronoi cross-stitch pattern generator."""

import os
import tempfile

import vormap_crossstitch


def test_generate_defaults():
    result = vormap_crossstitch.generate(30, 20, seed=1)
    assert result.grid_w == 30
    assert result.grid_h == 20
    assert len(result.grid) == 20
    assert len(result.grid[0]) == 30
    assert result.palette_name == "pastel"
    assert sum(result.stitch_counts.values()) == 600


def test_generate_palette():
    for pal in vormap_crossstitch.PALETTES:
        result = vormap_crossstitch.generate(10, 10, palette=pal, seed=42)
        assert result.palette_name == pal


def test_backstitch():
    result = vormap_crossstitch.generate(10, 10, backstitch=True, seed=1)
    assert result.backstitch is True


def test_save_pattern():
    result = vormap_crossstitch.generate(15, 10, num_seeds=5, seed=7)
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        path = f.name
    try:
        vormap_crossstitch.save_pattern(result, path)
        text = open(path, encoding="utf-8").read()
        assert "COLOUR LEGEND" in text
        assert "TOTAL" in text
    finally:
        os.unlink(path)


def test_save_chart():
    result = vormap_crossstitch.generate(8, 6, num_seeds=3, cell_size=8, seed=2)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        vormap_crossstitch.save_chart(result, path)
        data = open(path, "rb").read()
        assert data[:4] == b"\x89PNG"
        assert len(data) > 100
    finally:
        os.unlink(path)


def test_num_seeds_clamped():
    result = vormap_crossstitch.generate(10, 10, num_seeds=999, seed=1)
    assert len(result.colours) <= 12  # max palette size


if __name__ == "__main__":
    test_generate_defaults()
    test_generate_palette()
    test_backstitch()
    test_save_pattern()
    test_save_chart()
    test_num_seeds_clamped()
    print("All tests passed!")
