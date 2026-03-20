"""Tests for vormap_texture — seamless tileable Voronoi texture generator."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import vormap_texture as vt


def test_all_styles_run():
    """Each style produces a valid pixel buffer of correct size."""
    for style in vt.STYLES:
        buf = vt.generate_texture(style, 32, 32, num_seeds=10, seed_value=1)
        assert len(buf) == 32 * 32 * 3, f"{style}: wrong buffer size"


def test_png_output():
    """generate_texture_to_file writes a valid PNG."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        vt.generate_texture_to_file(path, "stone", 16, 16, num_seeds=5, seed_value=7)
        assert os.path.exists(path)
        with open(path, "rb") as f:
            header = f.read(8)
        assert header == b"\x89PNG\r\n\x1a\n"
    finally:
        os.unlink(path)


def test_reproducibility():
    """Same seed_value produces identical output."""
    a = vt.generate_texture("cells", 24, 24, num_seeds=8, seed_value=99)
    b = vt.generate_texture("cells", 24, 24, num_seeds=8, seed_value=99)
    assert a == b


def test_colormaps():
    """All named colormaps work without error."""
    for cm in vt.COLORMAPS:
        buf = vt.generate_texture("mud", 16, 16, num_seeds=5, colormap=cm, seed_value=1)
        assert len(buf) == 16 * 16 * 3


def test_border():
    """Border option doesn't crash and changes output."""
    a = vt.generate_texture("crystal", 24, 24, num_seeds=8, border=0, seed_value=1)
    b = vt.generate_texture("crystal", 24, 24, num_seeds=8, border=3, seed_value=1)
    assert a != b


if __name__ == "__main__":
    test_all_styles_run()
    test_png_output()
    test_reproducibility()
    test_colormaps()
    test_border()
    print("All tests passed!")
