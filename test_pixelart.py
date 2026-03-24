"""Tests for vormap_pixelart module."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import vormap_pixelart


def test_generate_default():
    result = vormap_pixelart.generate()
    assert result.grid_w == 32
    assert result.grid_h == 32
    assert result.num_seeds == 12
    assert len(result.grid) == 32
    assert len(result.grid[0]) == 32
    assert len(result.cell_counts) > 0
    total = sum(result.cell_counts.values())
    assert total == 32 * 32


def test_generate_custom_size():
    result = vormap_pixelart.generate(grid_w=16, grid_h=8, num_seeds=4)
    assert result.grid_w == 16
    assert result.grid_h == 8
    assert result.num_seeds == 4
    assert len(result.grid) == 8
    assert len(result.grid[0]) == 16


def test_generate_reproducible():
    r1 = vormap_pixelart.generate(seed_value=42)
    r2 = vormap_pixelart.generate(seed_value=42)
    assert r1.grid == r2.grid
    assert r1.seeds == r2.seeds


def test_generate_custom_points():
    pts = [(5.0, 5.0), (15.0, 15.0), (25.0, 5.0)]
    result = vormap_pixelart.generate(grid_w=32, grid_h=32, points=pts)
    assert result.num_seeds == 3
    assert result.seeds == pts


def test_all_palettes():
    for name in vormap_pixelart.PALETTES:
        result = vormap_pixelart.generate(palette=name, num_seeds=4,
                                          grid_w=8, grid_h=8, seed_value=1)
        assert result.palette_name == name
        assert len(result.colors) > 0


def test_custom_hex_palette():
    result = vormap_pixelart.generate(palette="#ff0000,#00ff00,#0000ff",
                                      grid_w=8, grid_h=8, num_seeds=3)
    assert result.palette_name == "custom"
    assert result.colors == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]


def test_render():
    result = vormap_pixelart.generate(grid_w=4, grid_h=4, num_seeds=2,
                                      seed_value=7)
    pixels, w, h = vormap_pixelart.render(result, scale=2)
    assert w == 8
    assert h == 8
    assert len(pixels) == 8 * 8 * 3


def test_render_with_grid():
    result = vormap_pixelart.generate(grid_w=4, grid_h=4, num_seeds=2,
                                      seed_value=7)
    pixels, w, h = vormap_pixelart.render(result, scale=4, show_grid=True)
    assert len(pixels) == w * h * 3


def test_render_with_border_glow():
    result = vormap_pixelart.generate(grid_w=4, grid_h=4, num_seeds=2,
                                      seed_value=7)
    pixels, w, h = vormap_pixelart.render(result, scale=4, border_glow=True)
    assert len(pixels) == w * h * 3


def test_save_png():
    result = vormap_pixelart.generate(grid_w=8, grid_h=8, num_seeds=4,
                                      seed_value=99)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        out = vormap_pixelart.save_png(result, path, scale=2)
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0
        # Verify PNG signature
        with open(out, "rb") as f:
            sig = f.read(8)
        assert sig == b"\x89PNG\r\n\x1a\n"
    finally:
        os.unlink(path)


def test_is_border():
    grid = [[0, 0, 1], [0, 1, 1], [1, 1, 1]]
    assert vormap_pixelart._is_border(grid, 1, 0, 3, 3) is True  # next to 1
    assert vormap_pixelart._is_border(grid, 2, 2, 3, 3) is False  # all 1s


def test_invalid_palette():
    try:
        vormap_pixelart.generate(palette="nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nAll {len(tests)} tests passed!")
