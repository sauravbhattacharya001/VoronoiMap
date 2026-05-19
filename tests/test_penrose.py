"""Tests for vormap_penrose — Penrose tiling generator (P2/P3)."""

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import vormap_penrose as vp
from vormap_penrose import (
    COLORMAPS,
    DEFAULT_COLORMAP,
    INV_PHI,
    PHI,
    _initial_wheel,
    _subdivide_p2,
    _subdivide_p3,
    _svg_polygon,
    export_json,
    export_seeds_csv,
    export_svg,
    extract_seeds,
    generate_penrose,
    main,
    triangles_to_tiles,
)


# ── Constants ────────────────────────────────────────────────────────

def test_phi_is_golden_ratio():
    assert abs(PHI - 1.6180339887) < 1e-9
    assert abs(INV_PHI * PHI - 1.0) < 1e-12


def test_colormaps_define_all_required_keys():
    required = {"kite", "dart", "thick", "thin"}
    for name, cmap in COLORMAPS.items():
        assert required.issubset(cmap.keys()), f"colormap {name} missing keys"
        for v in cmap.values():
            assert v.startswith("#") and len(v) == 7


def test_default_colormap_exists():
    assert DEFAULT_COLORMAP in COLORMAPS


# ── Initial wheel ────────────────────────────────────────────────────

def test_initial_wheel_p2_count_and_kinds():
    tris = _initial_wheel("P2", 100, 100, 50)
    assert len(tris) == 10
    for kind, _, _, _ in tris:
        assert kind == 0  # P2 wheel starts as acute halves


def test_initial_wheel_p3_kinds():
    tris = _initial_wheel("P3", 50, 50, 20)
    assert len(tris) == 10
    for kind, _, _, _ in tris:
        assert kind == 2  # P3 wheel starts as thick halves


def test_initial_wheel_centered_on_cx_cy():
    cx, cy = 200, 150
    tris = _initial_wheel("P2", cx, cy, 80)
    # First vertex of every triangle should be the center
    for _, a, _, _ in tris:
        assert abs(a[0] - cx) < 1e-9
        assert abs(a[1] - cy) < 1e-9


# ── Subdivision ──────────────────────────────────────────────────────

def test_subdivide_p2_grows_triangle_count():
    tris = _initial_wheel("P2", 0, 0, 100)
    n0 = len(tris)
    one = _subdivide_p2(tris)
    two = _subdivide_p2(one)
    assert len(one) > n0
    assert len(two) > len(one)


def test_subdivide_p3_grows_triangle_count():
    tris = _initial_wheel("P3", 0, 0, 100)
    n0 = len(tris)
    one = _subdivide_p3(tris)
    assert len(one) > n0
    # Only kinds 2 or 3 should appear
    for k, _, _, _ in one:
        assert k in (2, 3)


def test_subdivide_p2_kind_invariant():
    tris = _initial_wheel("P2", 0, 0, 100)
    out = _subdivide_p2(_subdivide_p2(tris))
    for k, _, _, _ in out:
        assert k in (0, 1)


# ── generate_penrose ─────────────────────────────────────────────────

def test_generate_penrose_p2_basic():
    tris, meta = generate_penrose("P2", depth=3, size=400)
    assert meta["tiling_type"] == "P2"
    assert meta["depth"] == 3
    assert meta["size"] == 400
    assert meta["num_triangles"] == len(tris)
    assert len(tris) > 10  # subdivision happened


def test_generate_penrose_p3_basic():
    tris, meta = generate_penrose("P3", depth=2, size=200)
    assert meta["tiling_type"] == "P3"
    assert len(tris) > 10


def test_generate_penrose_depth_clamped_low():
    _, meta = generate_penrose("P2", depth=-5, size=100)
    assert meta["depth"] == 1


def test_generate_penrose_depth_clamped_high():
    _, meta = generate_penrose("P2", depth=99, size=100)
    assert meta["depth"] == 10


def test_generate_penrose_invalid_type_raises():
    with pytest.raises(ValueError):
        generate_penrose("P4", depth=2, size=100)


def test_generate_penrose_triangles_within_canvas_ish():
    # Tiles extend slightly past the half-size, but should be in the same
    # order of magnitude as the canvas.
    _, meta = generate_penrose("P2", depth=2, size=400)
    tris, _ = generate_penrose("P2", depth=2, size=400)
    for _, a, b, c in tris:
        for p in (a, b, c):
            assert -400 < p[0] < 800
            assert -400 < p[1] < 800


# ── triangles_to_tiles ──────────────────────────────────────────────

def test_triangles_to_tiles_p2_returns_tiles():
    tris, _ = generate_penrose("P2", depth=3, size=400)
    tiles = triangles_to_tiles(tris, "P2")
    assert len(tiles) > 0
    types = {t["type"] for t in tiles}
    # Should contain at least one of the P2 tile labels
    assert types.issubset({"kite", "dart"})
    # Each tile has the expected shape
    for t in tiles:
        assert "vertices" in t
        assert "centroid" in t
        assert len(t["vertices"]) in (3, 4)


def test_triangles_to_tiles_p3_returns_tiles():
    tris, _ = generate_penrose("P3", depth=3, size=400)
    tiles = triangles_to_tiles(tris, "P3")
    assert len(tiles) > 0
    types = {t["type"] for t in tiles}
    assert types.issubset({"thick", "thin"})


def test_extract_seeds_returns_xy_pairs():
    tris, _ = generate_penrose("P2", depth=2, size=200)
    tiles = triangles_to_tiles(tris, "P2")
    seeds = extract_seeds(tiles)
    assert len(seeds) == len(tiles)
    for s in seeds:
        assert len(s) == 2
        x, y = s
        assert isinstance(x, float)
        assert isinstance(y, float)


# ── SVG/JSON/CSV export ─────────────────────────────────────────────

def test_svg_polygon_format():
    s = _svg_polygon([(1.234, 2.0), (3, 4.5)], fill="#abcdef")
    assert s.startswith("<polygon")
    assert 'fill="#abcdef"' in s
    assert "1.23,2.00" in s
    assert "3.00,4.50" in s


def test_export_svg_well_formed():
    tris, meta = generate_penrose("P2", depth=2, size=300)
    tiles = triangles_to_tiles(tris, "P2")
    svg = export_svg(tiles, meta)
    assert svg.startswith("<?xml")
    assert "<svg" in svg
    assert "</svg>" in svg
    assert 'width="300"' in svg
    assert "<polygon" in svg
    # No labels by default
    assert "<text" not in svg


def test_export_svg_with_labels():
    tris, meta = generate_penrose("P2", depth=2, size=200)
    tiles = triangles_to_tiles(tris, "P2")
    svg = export_svg(tiles, meta, colormap_name="cool", labels=True)
    assert "<text" in svg
    # First-letter label for kite/dart -> K or D
    assert ">K<" in svg or ">D<" in svg


def test_export_svg_unknown_colormap_falls_back():
    tris, meta = generate_penrose("P2", depth=1, size=100)
    tiles = triangles_to_tiles(tris, "P2")
    svg = export_svg(tiles, meta, colormap_name="does-not-exist")
    assert "<svg" in svg
    assert "<polygon" in svg


def test_export_json_parses_back():
    tris, meta = generate_penrose("P3", depth=2, size=150)
    tiles = triangles_to_tiles(tris, "P3")
    js = export_json(tiles, meta)
    data = json.loads(js)
    assert "meta" in data
    assert "tiles" in data
    assert data["meta"]["tiling_type"] == "P3"
    assert len(data["tiles"]) == len(tiles)
    first = data["tiles"][0]
    assert "type" in first
    assert "vertices" in first
    assert "centroid" in first
    # Lists, not tuples (JSON roundtrip)
    assert isinstance(first["vertices"], list)
    assert isinstance(first["vertices"][0], list)


def test_export_seeds_csv_format():
    tris, meta = generate_penrose("P2", depth=2, size=200)
    tiles = triangles_to_tiles(tris, "P2")
    csv = export_seeds_csv(tiles)
    lines = csv.splitlines()
    assert lines[0] == "x,y"
    assert len(lines) == len(tiles) + 1
    # Every data row should be two floats
    for row in lines[1:]:
        parts = row.split(",")
        assert len(parts) == 2
        float(parts[0]); float(parts[1])


# ── CLI ──────────────────────────────────────────────────────────────

def test_main_writes_svg(tmp_path, capsys, monkeypatch):
    out = tmp_path / "p.svg"
    # Patch vormap.validate_output_path so absolute tmp paths are accepted
    import vormap
    monkeypatch.setattr(vormap, "validate_output_path",
                        lambda p, allow_absolute=True: None)
    rc = main(["P2", str(out), "--depth", "2", "--size", "200"])
    assert rc is None
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "<svg" in content
    captured = capsys.readouterr()
    assert "Done." in captured.out


def test_main_writes_json(tmp_path, monkeypatch):
    out = tmp_path / "p.json"
    import vormap
    monkeypatch.setattr(vormap, "validate_output_path",
                        lambda p, allow_absolute=True: None)
    main(["P3", str(out), "--depth", "2", "--size", "200"])
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["tiling_type"] == "P3"


def test_main_seeds_out(tmp_path, monkeypatch):
    out = tmp_path / "p.svg"
    seeds = tmp_path / "seeds.csv"
    import vormap
    monkeypatch.setattr(vormap, "validate_output_path",
                        lambda p, allow_absolute=True: None)
    main(["P2", str(out), "--depth", "2", "--size", "200",
          "--seeds-out", str(seeds)])
    assert seeds.exists()
    txt = seeds.read_text(encoding="utf-8")
    assert txt.startswith("x,y")
    assert len(txt.splitlines()) > 1
