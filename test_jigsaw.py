"""Tests for vormap_jigsaw — Voronoi Jigsaw Puzzle Generator."""

import json
import os
import struct
import tempfile
import zlib

import pytest

from vormap_jigsaw import (
    generate_jigsaw,
    _place_seeds_random,
    _place_seeds_grid,
    _assign_voronoi,
    _find_borders,
    _region_bboxes,
    _read_png,
)


def _make_test_png(path: str, width: int = 40, height: int = 30) -> str:
    """Create a simple test RGB PNG."""

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # no filter
        for x in range(width):
            raw.extend((x * 6 % 256, y * 8 % 256, (x + y) * 3 % 256))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(_chunk(b"IEND", b""))
    return path


class TestSeedPlacement:
    def test_random_count(self):
        import random
        seeds = _place_seeds_random(100, 100, 10, random.Random(42))
        assert len(seeds) == 10

    def test_grid_count(self):
        import random
        seeds = _place_seeds_grid(100, 100, 9, random.Random(42))
        assert len(seeds) == 9

    def test_seeds_in_bounds(self):
        import random
        for _ in range(5):
            seeds = _place_seeds_random(50, 50, 20, random.Random())
            for x, y in seeds:
                assert 0 <= x < 50
                assert 0 <= y < 50


class TestVoronoiAssignment:
    def test_basic_assignment(self):
        grid = _assign_voronoi(10, 10, [(2, 2), (8, 8)])
        # corner (0,0) should be closer to seed 0
        val = grid[0][0] if isinstance(grid, list) else int(grid[0, 0])
        assert val == 0
        # corner (9,9) should be closer to seed 1
        val = grid[9][9] if isinstance(grid, list) else int(grid[9, 9])
        assert val == 1


class TestBorders:
    def test_has_borders(self):
        grid = _assign_voronoi(20, 20, [(5, 5), (15, 15)])
        borders = _find_borders(grid, 20, 20)
        assert len(borders) > 0


class TestGenerateJigsaw:
    def test_basic_generation(self, tmp_path):
        img = _make_test_png(str(tmp_path / "test.png"))
        out_dir = str(tmp_path / "pieces")
        result = generate_jigsaw(img, n_pieces=4, output_dir=out_dir, seed=42)
        assert result["pieces"] == 4
        assert os.path.isdir(out_dir)
        assert os.path.isfile(os.path.join(out_dir, "manifest.json"))
        # Check manifest
        with open(os.path.join(out_dir, "manifest.json")) as f:
            manifest = json.load(f)
        assert len(manifest["files"]) == 4

    def test_overlay(self, tmp_path):
        img = _make_test_png(str(tmp_path / "test.png"))
        overlay = str(tmp_path / "overlay.png")
        result = generate_jigsaw(img, n_pieces=3, output_dir=str(tmp_path / "p"),
                                 overlay_path=overlay, seed=1)
        assert os.path.isfile(overlay)

    def test_grid_placement(self, tmp_path):
        img = _make_test_png(str(tmp_path / "test.png"))
        result = generate_jigsaw(img, n_pieces=6, output_dir=str(tmp_path / "p"),
                                 placement="grid", seed=7)
        assert result["pieces"] == 6

    def test_shuffle(self, tmp_path):
        img = _make_test_png(str(tmp_path / "test.png"))
        result = generate_jigsaw(img, n_pieces=5, output_dir=str(tmp_path / "p"),
                                 shuffle=True, seed=99)
        assert result["pieces"] == 5

    def test_piece_files_readable(self, tmp_path):
        img = _make_test_png(str(tmp_path / "test.png"), 20, 20)
        out_dir = str(tmp_path / "pieces")
        generate_jigsaw(img, n_pieces=3, output_dir=out_dir, seed=42)
        # Each piece file should be valid PNG (starts with signature)
        for fname in os.listdir(out_dir):
            if fname.endswith(".png"):
                with open(os.path.join(out_dir, fname), "rb") as f:
                    assert f.read(4) == b"\x89PNG"
