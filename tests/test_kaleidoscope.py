"""Tests for vormap_kaleidoscope.py"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from vormap_kaleidoscope import generate_kaleidoscope, generate_kaleidoscope_data, main


def test_svg_output():
    svg = generate_kaleidoscope(folds=6, n_seeds=10, size=400, seed=42)
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "<polygon" in svg


def test_folds_clamped():
    svg_low = generate_kaleidoscope(folds=1, n_seeds=5, size=200, seed=1)
    assert "<svg" in svg_low  # folds clamped to 3

    svg_high = generate_kaleidoscope(folds=100, n_seeds=5, size=200, seed=1)
    assert "<svg" in svg_high  # folds clamped to 24


def test_all_palettes():
    from vormap_kaleidoscope import PALETTES
    for name in PALETTES:
        svg = generate_kaleidoscope(folds=4, n_seeds=8, size=300, palette_name=name, seed=7)
        assert "<svg" in svg


def test_glow_and_no_mask():
    svg = generate_kaleidoscope(folds=5, n_seeds=10, size=400, glow=True, mask=False, seed=3)
    assert 'filter id="glow"' in svg
    assert 'clip-path' not in svg.split('<g')[1]


def test_json_output():
    data = generate_kaleidoscope_data(folds=8, n_seeds=15, size=600, seed=99)
    assert data["folds"] == 8
    assert data["seeds_per_wedge"] == 15
    assert len(data["wedge_seeds"]) == 15
    assert data["total_rendered_cells"] == len(data["wedge_cells"]) * 8 * 2


def test_cli_svg():
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        path = f.name
    try:
        main(["--folds", "4", "--seeds", "8", "--size", "300", "--seed", "1", path])
        with open(path) as fh:
            content = fh.read()
        assert "<svg" in content
    finally:
        os.unlink(path)


def test_cli_json():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        main(["--folds", "6", "--seeds", "10", "--format", "json", "--seed", "2", path])
        with open(path) as fh:
            data = json.load(fh)
        assert data["folds"] == 6
    finally:
        os.unlink(path)


def test_reproducibility():
    svg1 = generate_kaleidoscope(folds=6, n_seeds=20, size=500, seed=42)
    svg2 = generate_kaleidoscope(folds=6, n_seeds=20, size=500, seed=42)
    assert svg1 == svg2


if __name__ == "__main__":
    test_svg_output()
    test_folds_clamped()
    test_all_palettes()
    test_glow_and_no_mask()
    test_json_output()
    test_cli_svg()
    test_cli_json()
    test_reproducibility()
    print("All tests passed!")
