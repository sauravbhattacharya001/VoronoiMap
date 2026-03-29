"""Tests for vormap_lowpoly – Voronoi low-poly image renderer."""

import os
import struct
import tempfile
import zlib

import vormap_lowpoly


def _make_test_png(path: str, w: int = 16, h: int = 16) -> None:
    """Create a tiny test PNG with a gradient."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw = bytearray()
    for y in range(h):
        raw.append(0)  # no filter
        for x in range(w):
            r = int(255 * x / max(w - 1, 1))
            g = int(255 * y / max(h - 1, 1))
            b = 128
            raw.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


def test_render_basic():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input.png")
        out = os.path.join(td, "output.png")
        _make_test_png(inp, 32, 32)

        result = vormap_lowpoly.render(inp, num_seeds=10, seed=42)
        assert result.width == 32
        assert result.height == 32
        assert result.num_cells == 10
        assert len(result.pixels) == 32 * 32
        assert len(result.seeds) == 10

        vormap_lowpoly.save_png(result, out)
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0


def test_render_with_outline():
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input.png")
        out = os.path.join(td, "output.png")
        _make_test_png(inp, 24, 24)

        result = vormap_lowpoly.render(
            inp, num_seeds=8, outline=2, seed=99
        )
        vormap_lowpoly.save_png(result, out)
        assert os.path.exists(out)

        # Verify some border pixels exist (dark outline colour)
        dark_count = sum(1 for r, g, b in result.pixels if r <= 20 and g <= 20 and b <= 20)
        assert dark_count > 0, "Expected outline pixels"


def test_render_from_pixels():
    pixels = [(255, 0, 0)] * 64 + [(0, 0, 255)] * 64
    result = vormap_lowpoly.render_from_pixels(
        pixels, 16, 8, num_seeds=4, seed=7
    )
    assert result.width == 16
    assert result.height == 8
    assert len(result.cell_colours) == 4


def test_cell_counts():
    pixels = [(i, i, i) for i in range(100) for _ in range(1)]
    # 10x10 image
    result = vormap_lowpoly.render_from_pixels(
        pixels, 10, 10, num_seeds=5, seed=1
    )
    counts = result.cell_counts
    assert sum(counts.values()) == 100


def test_edge_bias_zero():
    """With edge_bias=0, all seeds are random."""
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input.png")
        _make_test_png(inp, 20, 20)
        result = vormap_lowpoly.render(inp, num_seeds=5, edge_bias=0.0, seed=3)
        assert result.num_cells == 5


def test_edge_bias_one():
    """With edge_bias=1.0, all seeds are edge-biased."""
    with tempfile.TemporaryDirectory() as td:
        inp = os.path.join(td, "input.png")
        _make_test_png(inp, 20, 20)
        result = vormap_lowpoly.render(inp, num_seeds=5, edge_bias=1.0, seed=3)
        assert result.num_cells == 5


def test_single_seed():
    """With 1 seed, entire image is one colour."""
    pixels = [(100, 150, 200)] * 25
    result = vormap_lowpoly.render_from_pixels(pixels, 5, 5, num_seeds=1, seed=0)
    assert all(p == (100, 150, 200) for p in result.pixels)


if __name__ == "__main__":
    test_render_basic()
    test_render_with_outline()
    test_render_from_pixels()
    test_cell_counts()
    test_edge_bias_zero()
    test_edge_bias_one()
    test_single_seed()
    print("All tests passed!")
