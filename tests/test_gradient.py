"""Tests for vormap_gradient — Voronoi gradient-fill renderer."""

import os
import tempfile

import vormap_gradient


def test_generate_defaults():
    result = vormap_gradient.generate(width=50, height=50, num_seeds=5, seed=42)
    assert result.width == 50
    assert result.height == 50
    assert result.num_seeds == 5
    assert len(result.cells) == 5
    assert len(result.pixels) == 50
    assert len(result.pixels[0]) == 50
    # Every cell should own at least one pixel
    for c in result.cells:
        assert c.pixel_count > 0


def test_blend_modes():
    for blend in ("linear", "ease", "quadratic"):
        result = vormap_gradient.generate(
            width=30, height=30, num_seeds=4, blend=blend, seed=7
        )
        assert len(result.pixels) == 30


def test_outline():
    result = vormap_gradient.generate(
        width=40, height=40, num_seeds=6, outline=True, seed=1
    )
    assert len(result.pixels) == 40


def test_custom_colors():
    result = vormap_gradient.generate(
        width=30, height=30, num_seeds=3,
        custom_colors=["#ff0000", "#00ff00", "#0000ff"], seed=99,
    )
    assert len(result.cells) == 3


def test_all_palettes():
    for pal in vormap_gradient.PALETTES:
        result = vormap_gradient.generate(
            width=20, height=20, num_seeds=3, palette=pal, seed=0
        )
        assert result.width == 20


def test_save_png():
    result = vormap_gradient.generate(width=20, height=20, num_seeds=3, seed=5)
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "test.png")
        out = vormap_gradient.save_png(result, path)
        assert os.path.isfile(out)
        with open(out, "rb") as f:
            sig = f.read(4)
        assert sig == b"\x89PNG"


def test_fade_zero():
    result = vormap_gradient.generate(
        width=20, height=20, num_seeds=2, fade_amount=0.0, seed=3
    )
    # With zero fade, outer == inner for each cell
    for c in result.cells:
        assert c.outer_color == c.inner_color


if __name__ == "__main__":
    test_generate_defaults()
    test_blend_modes()
    test_outline()
    test_custom_colors()
    test_all_palettes()
    test_save_png()
    test_fade_zero()
    print("All tests passed!")
