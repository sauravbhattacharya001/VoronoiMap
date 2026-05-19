"""Tests for vormap_tile — seamless tileable Voronoi pattern generator."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import vormap_tile as vt
from vormap_tile import (
    PALETTES,
    TileGenerator,
    _clip_polygon_by_bisector,
    _intersect_line,
    _points_to_svg_str,
    _voronoi_cell_for_seed,
    main,
)


# ── Palette tests ────────────────────────────────────────────────────

def test_palettes_present():
    expected = {"default", "viridis", "pastel", "earth", "neon", "jewel"}
    assert expected.issubset(set(PALETTES.keys()))


def test_palette_colors_are_hex():
    for name, colors in PALETTES.items():
        assert len(colors) >= 5, f"palette {name} too small"
        for c in colors:
            assert c.startswith("#") and len(c) == 7


# ── Geometry helper tests ────────────────────────────────────────────

def test_intersect_line_midpoint():
    # Segment crossing a vertical bisector at x=5 (normal pointing +x)
    p = _intersect_line((0, 0), (10, 0), 5, 0, 1, 0)
    assert abs(p[0] - 5) < 1e-9
    assert abs(p[1] - 0) < 1e-9


def test_intersect_line_degenerate_returns_first():
    # Zero-length normal direction -> denom near zero -> returns p1
    p = _intersect_line((1, 2), (3, 4), 0, 0, 0, 0)
    assert p == (1, 2)


def test_clip_polygon_by_bisector_keeps_seed_side():
    # Square; bisector between (1,1) and (9,1) is x=5
    poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
    clipped = _clip_polygon_by_bisector(poly, 1, 1, 9, 1)
    # All resulting vertices should have x <= 5 + eps
    assert clipped
    for x, y in clipped:
        assert x <= 5 + 1e-6


def test_clip_polygon_by_bisector_empty_input():
    assert _clip_polygon_by_bisector([], 0, 0, 1, 1) == []


def test_voronoi_cell_for_seed_two_seeds_halves_box():
    seeds = [(2, 5), (8, 5)]
    cell = _voronoi_cell_for_seed(2, 5, seeds, 10, 10)
    assert cell
    # Cell for left seed should not cross x=5 (with small margin tolerance)
    for x, y in cell:
        assert x <= 5 + 1e-6


def test_voronoi_cell_skips_self():
    # Single seed -> returns the bounding rect (no clipping)
    seeds = [(5, 5)]
    cell = _voronoi_cell_for_seed(5, 5, seeds, 10, 10, margin=0)
    # 4 corners
    assert len(cell) == 4


def test_points_to_svg_str_format():
    out = _points_to_svg_str([(1.234, 2.0), (3, 4.5)])
    assert out == "1.23,2.00 3.00,4.50"


# ── TileGenerator core ───────────────────────────────────────────────

@pytest.fixture
def gen_small():
    return TileGenerator(width=64, height=64, seeds=8, seed=42,
                         lloyd_iterations=1)


def test_generator_basic_properties(gen_small):
    assert gen_small.width == 64
    assert gen_small.height == 64
    assert gen_small.n_seeds == 8
    assert len(gen_small.seed_points) == 8
    # Every seed lies inside the tile after Lloyd wrap-back
    for x, y in gen_small.seed_points:
        assert 0 <= x <= 64
        assert 0 <= y <= 64


def test_generator_cells_have_polygons(gen_small):
    assert len(gen_small.cells) > 0
    for cell in gen_small.cells:
        assert "polygon" in cell
        assert len(cell["polygon"]) >= 3
        assert "color" in cell
        assert cell["color"].startswith("#")
        assert "index" in cell


def test_generator_palette_fallback():
    g = TileGenerator(width=32, height=32, seeds=3, palette="nonexistent",
                      seed=1, lloyd_iterations=0)
    # Falls back to default palette
    assert g.colors == PALETTES["default"]


def test_generator_reproducible_with_seed():
    g1 = TileGenerator(width=64, height=64, seeds=10, seed=123,
                       lloyd_iterations=1)
    g2 = TileGenerator(width=64, height=64, seeds=10, seed=123,
                       lloyd_iterations=1)
    assert g1.seed_points == g2.seed_points


def test_generator_zero_lloyd_iterations():
    g = TileGenerator(width=64, height=64, seeds=6, seed=7,
                      lloyd_iterations=0)
    assert len(g.cells) > 0


def test_wrap_seeds_produces_9x_count(gen_small):
    wrapped = gen_small._wrap_seeds([(10, 10), (20, 20)])
    assert len(wrapped) == 9 * 2
    # Should include the originals
    assert (10, 10) in wrapped
    assert (20, 20) in wrapped


# ── Tiling correctness (seamlessness) ────────────────────────────────

def test_tiling_coverage_close_to_full():
    g = TileGenerator(width=128, height=128, seeds=20, seed=11,
                      lloyd_iterations=2)
    s = g.stats()
    # With tiled cells clipped to the rectangle, coverage should be high
    # (some loss at borders from finite seed count, but well above 0.8).
    assert s["coverage"] > 0.8
    assert s["coverage"] <= 1.01


# ── Rendering / styles ───────────────────────────────────────────────

@pytest.mark.parametrize("style", ["flat", "stained-glass", "wireframe",
                                    "mosaic", "gradient"])
def test_render_svg_all_styles(style):
    g = TileGenerator(width=48, height=48, seeds=5, style=style, seed=3,
                      lloyd_iterations=0)
    svg = g.render_svg()
    assert svg.startswith("<?xml")
    assert "<svg" in svg
    assert "</svg>" in svg
    assert "<polygon" in svg
    if style == "gradient":
        assert "<radialGradient" in svg
    if style == "stained-glass":
        assert "<circle" in svg


def test_render_svg_tile_preview_dimensions():
    g = TileGenerator(width=40, height=30, seeds=4, seed=8,
                      lloyd_iterations=0)
    svg = g.render_svg(tile_repeat=(3, 2))
    # Viewbox should be expanded
    assert 'width="120"' in svg
    assert 'height="60"' in svg
    # Should contain translation groups for repeats
    assert svg.count("<g transform=") == 6


def test_save_and_save_preview(tmp_path):
    g = TileGenerator(width=32, height=32, seeds=4, seed=2,
                      lloyd_iterations=0)
    out = tmp_path / "tile.svg"
    g.save(str(out))
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "<svg" in content

    preview = tmp_path / "preview.svg"
    g.save_preview(str(preview), cols=2, rows=2)
    assert preview.exists()
    pcontent = preview.read_text(encoding="utf-8")
    assert "<g transform=" in pcontent


# ── Stats ────────────────────────────────────────────────────────────

def test_stats_keys_and_ranges():
    g = TileGenerator(width=64, height=64, seeds=10, seed=42,
                      lloyd_iterations=1)
    s = g.stats()
    for k in ("seeds", "cells", "width", "height", "style", "palette",
              "mean_area", "min_area", "max_area", "coverage"):
        assert k in s
    assert s["seeds"] == 10
    assert s["width"] == 64
    assert s["height"] == 64
    assert s["cells"] > 0
    assert s["min_area"] <= s["mean_area"] <= s["max_area"]


# ── CLI ──────────────────────────────────────────────────────────────

def test_main_basic_cli(tmp_path, monkeypatch, capsys):
    out = tmp_path / "cli.svg"
    monkeypatch.setattr(sys, "argv", [
        "vormap_tile.py",
        "-o", str(out),
        "--seeds", "6",
        "--width", "48",
        "--height", "48",
        "--seed", "1",
        "--lloyd", "0",
    ])
    main()
    captured = capsys.readouterr()
    assert "Saved tile" in captured.out
    assert out.exists()
    assert "<svg" in out.read_text(encoding="utf-8")


def test_main_with_stats(tmp_path, monkeypatch, capsys):
    out = tmp_path / "stats.svg"
    monkeypatch.setattr(sys, "argv", [
        "vormap_tile.py",
        "-o", str(out),
        "--seeds", "4",
        "--width", "32",
        "--height", "32",
        "--seed", "5",
        "--lloyd", "0",
        "--stats",
    ])
    main()
    captured = capsys.readouterr()
    assert "Tile Statistics" in captured.out
    assert "seeds" in captured.out


def test_main_tile_preview(tmp_path, monkeypatch, capsys):
    out = tmp_path / "p.svg"
    monkeypatch.setattr(sys, "argv", [
        "vormap_tile.py",
        "-o", str(out),
        "--seeds", "5",
        "--width", "32",
        "--height", "32",
        "--seed", "9",
        "--lloyd", "0",
        "--tilepreview", "2x2",
    ])
    main()
    captured = capsys.readouterr()
    assert "tile preview" in captured.out
    assert out.exists()


def test_main_invalid_tile_preview_exits(tmp_path, monkeypatch):
    out = tmp_path / "bad.svg"
    monkeypatch.setattr(sys, "argv", [
        "vormap_tile.py",
        "-o", str(out),
        "--seeds", "3",
        "--width", "32",
        "--height", "32",
        "--seed", "0",
        "--lloyd", "0",
        "--tilepreview", "not-a-grid",
    ])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
