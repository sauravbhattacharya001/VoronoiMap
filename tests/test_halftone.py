"""Tests for vormap_halftone — Voronoi Halftone Renderer."""

import math
import os
import struct
import tempfile
import zlib
import unittest

import vormap_halftone


def _make_test_png(path: str, w: int, h: int,
                   pixels=None) -> None:
    """Write a minimal test PNG."""
    if pixels is None:
        pixels = []
        for y in range(h):
            for x in range(w):
                gray = int(255 * x / max(w - 1, 1))
                pixels.append((gray, gray, gray))

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw_rows = bytearray()
    for y in range(h):
        raw_rows.append(0)
        for x in range(w):
            r, g, b = pixels[y * w + x]
            raw_rows.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


class TestHalftoneBasic(unittest.TestCase):
    """Basic halftone rendering tests."""

    def test_mono_render(self):
        """Mono halftone renders without error and produces correct dimensions."""
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "input.png")
            out = os.path.join(td, "output.png")
            _make_test_png(inp, 40, 30)

            result = vormap_halftone.render(inp, num_dots=50, seed=42)
            self.assertEqual(result.width, 40)
            self.assertEqual(result.height, 30)
            self.assertEqual(len(result.pixels), 40 * 30)
            self.assertEqual(result.num_dots, 50)

            vormap_halftone.save_png(result, out)
            self.assertTrue(os.path.exists(out))

    def test_cmyk_render(self):
        """CMYK halftone renders without error."""
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "input.png")
            out = os.path.join(td, "output.png")
            # Colourful gradient
            pixels = []
            for y in range(20):
                for x in range(30):
                    pixels.append((x * 8, y * 12, 128))
            _make_test_png(inp, 30, 20, pixels)

            result = vormap_halftone.render(inp, num_dots=30, cmyk=True, seed=7)
            self.assertEqual(result.width, 30)
            self.assertEqual(result.height, 20)
            vormap_halftone.save_png(result, out)
            self.assertTrue(os.path.exists(out))

    def test_invert_mode(self):
        """Inverted halftone runs without error."""
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "input.png")
            _make_test_png(inp, 20, 20)
            result = vormap_halftone.render(inp, num_dots=20, invert=True, seed=1)
            self.assertEqual(result.num_dots, 20)

    def test_custom_colours(self):
        """Custom bg/fg colours are respected."""
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "input.png")
            # All-white image → mono halftone with darkness=0 → no dots drawn
            pixels = [(255, 255, 255)] * (10 * 10)
            _make_test_png(inp, 10, 10, pixels)
            bg = (30, 30, 30)
            result = vormap_halftone.render(inp, num_dots=10, bg=bg, seed=0)
            # All pixels should be bg since white → darkness=0 → no dots
            for px in result.pixels:
                self.assertEqual(px, bg)

    def test_render_from_pixels(self):
        """render_from_pixels works directly."""
        pixels = [(128, 64, 32)] * (8 * 8)
        result = vormap_halftone.render_from_pixels(pixels, 8, 8, num_dots=5)
        self.assertEqual(result.width, 8)
        self.assertEqual(result.height, 8)

    def test_rgb_to_cmyk(self):
        """CMYK conversion sanity checks."""
        c, m, y, k = vormap_halftone._rgb_to_cmyk(255, 0, 0)
        self.assertAlmostEqual(c, 0.0)
        self.assertAlmostEqual(m, 1.0)
        self.assertAlmostEqual(y, 1.0)
        self.assertAlmostEqual(k, 0.0)

        c, m, y, k = vormap_halftone._rgb_to_cmyk(0, 0, 0)
        self.assertAlmostEqual(k, 1.0)

    def test_seed_reproducibility(self):
        """Same seed produces same output."""
        with tempfile.TemporaryDirectory() as td:
            inp = os.path.join(td, "input.png")
            _make_test_png(inp, 15, 15)
            r1 = vormap_halftone.render(inp, num_dots=20, seed=99)
            r2 = vormap_halftone.render(inp, num_dots=20, seed=99)
            self.assertEqual(r1.pixels, r2.pixels)


if __name__ == "__main__":
    unittest.main()
