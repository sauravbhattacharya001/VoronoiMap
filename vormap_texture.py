"""Voronoi seamless tileable texture generator.

Generates seamless (tileable) textures based on Voronoi diagrams — useful for
game development, 3D art, web backgrounds, and procedural material creation.

Texture styles:
- **stone**   – irregular stone/cobblestone with edge darkening
- **scales**  – overlapping organic scale patterns
- **cells**   – biological cell-like texture with membranes
- **crystal** – angular crystalline facets with refraction-like shading
- **leather** – pebbled leather grain
- **mud**     – cracked dry mud / earth

All textures tile seamlessly by wrapping seed points across boundaries with
toroidal (wrap-around) distance. Output is a PNG image.

CLI usage
---------
::

    python vormap_texture.py stone 512 512 stone_tile.png
    python vormap_texture.py crystal 256 256 crystal.png --seeds 80 --border 2
    python vormap_texture.py cells 512 512 cells.png --colormap warm --seeds 200
    python vormap_texture.py mud 1024 1024 mud.png --seed-value 42

"""

import argparse
import math
import os
import random
import struct
import zlib


# ---------------------------------------------------------------------------
# Minimal PNG writer (no external deps)
# ---------------------------------------------------------------------------

def _write_png(path, pixels, width, height):
    """Write an RGB pixel buffer to a PNG file."""

    def _chunk(chunk_type, data):
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    for y in range(height):
        raw += b"\x00"  # filter none
        row_start = y * width * 3
        raw += pixels[row_start: row_start + width * 3]
    compressed = zlib.compress(raw, 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", header))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Colormaps
# ---------------------------------------------------------------------------

COLORMAPS = {
    "grey":  [(40, 40, 40), (100, 100, 100), (160, 160, 160), (200, 200, 200)],
    "warm":  [(180, 60, 30), (220, 130, 50), (240, 190, 80), (250, 220, 150)],
    "cool":  [(20, 60, 120), (40, 120, 180), (80, 180, 220), (160, 220, 240)],
    "earth": [(80, 50, 30), (140, 100, 50), (180, 150, 80), (200, 180, 130)],
    "neon":  [(255, 0, 80), (0, 255, 160), (80, 0, 255), (255, 220, 0)],
    "moss":  [(30, 60, 20), (60, 110, 40), (100, 160, 60), (160, 200, 100)],
}


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _sample_colormap(cmap, t):
    """Sample a colour from a colormap list at position t ∈ [0,1]."""
    t = max(0.0, min(1.0, t))
    n = len(cmap) - 1
    idx = t * n
    lo = int(idx)
    hi = min(lo + 1, n)
    frac = idx - lo
    return _lerp_color(cmap[lo], cmap[hi], frac)


# ---------------------------------------------------------------------------
# Toroidal distance helpers (for seamless tiling)
# ---------------------------------------------------------------------------

def _toroidal_dist_sq(x1, y1, x2, y2, w, h):
    """Squared distance with toroidal wrapping."""
    dx = abs(x1 - x2)
    dy = abs(y1 - y2)
    if dx > w / 2:
        dx = w - dx
    if dy > h / 2:
        dy = h - dy
    return dx * dx + dy * dy


def _toroidal_nearest(px, py, seeds, w, h):
    """Return (index, dist_sq, second_dist_sq) of nearest and 2nd nearest seed."""
    best_i, best_d = 0, float("inf")
    second_d = float("inf")
    for i, (sx, sy) in enumerate(seeds):
        d = _toroidal_dist_sq(px, py, sx, sy, w, h)
        if d < best_d:
            second_d = best_d
            best_d = d
            best_i = i
        elif d < second_d:
            second_d = d
    return best_i, best_d, second_d


# ---------------------------------------------------------------------------
# Seed generation
# ---------------------------------------------------------------------------

def _generate_seeds(n, w, h, rng):
    """Generate n random seed points."""
    return [(rng.uniform(0, w), rng.uniform(0, h)) for _ in range(n)]


# ---------------------------------------------------------------------------
# Style renderers
# ---------------------------------------------------------------------------

def _clamp(v, lo=0, hi=255):
    return max(lo, min(hi, int(v)))


def _style_stone(idx, d1, d2, cell_val, cmap):
    """Irregular stone with dark grout lines."""
    edge = math.sqrt(d2) - math.sqrt(d1)
    edge_factor = max(0.0, min(1.0, edge / 8.0))
    base = _sample_colormap(cmap, cell_val)
    # darken near edges
    dark = 0.3 + 0.7 * edge_factor
    return (_clamp(base[0] * dark), _clamp(base[1] * dark), _clamp(base[2] * dark))


def _style_scales(idx, d1, d2, cell_val, cmap):
    """Overlapping scale pattern."""
    r = math.sqrt(d1)
    highlight = max(0.0, 1.0 - r / 20.0)
    base = _sample_colormap(cmap, cell_val)
    return (
        _clamp(base[0] + highlight * 60),
        _clamp(base[1] + highlight * 60),
        _clamp(base[2] + highlight * 40),
    )


def _style_cells(idx, d1, d2, cell_val, cmap):
    """Biological cells with visible membranes."""
    edge = math.sqrt(d2) - math.sqrt(d1)
    membrane = 1.0 if edge < 3.0 else 0.0
    base = _sample_colormap(cmap, cell_val * 0.7 + 0.15)
    if membrane:
        t = edge / 3.0
        return _lerp_color((20, 40, 20), base, t)
    # inner gradient — lighter toward center
    inner = max(0.0, min(1.0, 1.0 - math.sqrt(d1) / 30.0))
    return (
        _clamp(base[0] + inner * 30),
        _clamp(base[1] + inner * 30),
        _clamp(base[2] + inner * 20),
    )


def _style_crystal(idx, d1, d2, cell_val, cmap):
    """Angular crystalline facets."""
    # use ratio of distances for facet shading
    ratio = math.sqrt(d1) / (math.sqrt(d2) + 1e-6)
    facet = ratio * 0.8 + cell_val * 0.2
    base = _sample_colormap(cmap, facet)
    # specular-like highlight near center
    spec = max(0.0, 1.0 - math.sqrt(d1) / 15.0) ** 2
    return (
        _clamp(base[0] + spec * 100),
        _clamp(base[1] + spec * 100),
        _clamp(base[2] + spec * 120),
    )


def _style_leather(idx, d1, d2, cell_val, cmap):
    """Pebbled leather grain."""
    r = math.sqrt(d1)
    bump = math.sin(r * 1.5) * 0.3 + 0.7
    base = _sample_colormap(cmap, cell_val * 0.5 + 0.25)
    return (
        _clamp(base[0] * bump),
        _clamp(base[1] * bump),
        _clamp(base[2] * bump),
    )


def _style_mud(idx, d1, d2, cell_val, cmap):
    """Cracked dry mud / earth."""
    edge = math.sqrt(d2) - math.sqrt(d1)
    crack = 1.0 if edge < 2.5 else 0.0
    if crack:
        t = edge / 2.5
        return _lerp_color((30, 20, 10), (80, 60, 40), t)
    base = _sample_colormap(cmap, cell_val * 0.6 + 0.2)
    return base


STYLES = {
    "stone": _style_stone,
    "scales": _style_scales,
    "cells": _style_cells,
    "crystal": _style_crystal,
    "leather": _style_leather,
    "mud": _style_mud,
}


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_texture(style, width, height, num_seeds=120, colormap="grey",
                     border=0, border_color=(0, 0, 0), seed_value=None):
    """Generate a seamless tileable Voronoi texture.

    Parameters
    ----------
    style : str
        One of: stone, scales, cells, crystal, leather, mud
    width, height : int
        Output dimensions in pixels.
    num_seeds : int
        Number of Voronoi seed points.
    colormap : str
        Named colormap (grey, warm, cool, earth, neon, moss).
    border : int
        Extra border line width (0 = style default only).
    border_color : tuple
        RGB tuple for border lines.
    seed_value : int or None
        Random seed for reproducibility.

    Returns
    -------
    bytes
        Raw RGB pixel buffer (width * height * 3 bytes).
    """
    rng = random.Random(seed_value)
    cmap = COLORMAPS.get(colormap, COLORMAPS["grey"])
    style_fn = STYLES.get(style, _style_stone)
    seeds = _generate_seeds(num_seeds, width, height, rng)

    # Pre-assign a stable random value per cell for color variation
    cell_vals = [rng.random() for _ in range(num_seeds)]

    buf = bytearray(width * height * 3)

    for y in range(height):
        for x in range(width):
            idx, d1, d2 = _toroidal_nearest(x, y, seeds, width, height)
            r, g, b = style_fn(idx, d1, d2, cell_vals[idx], cmap)

            # optional extra border
            if border > 0:
                edge = math.sqrt(d2) - math.sqrt(d1)
                if edge < border:
                    t = edge / border
                    r, g, b = _lerp_color(border_color, (r, g, b), t)

            off = (y * width + x) * 3
            buf[off] = _clamp(r)
            buf[off + 1] = _clamp(g)
            buf[off + 2] = _clamp(b)

    return bytes(buf)


def generate_texture_to_file(output_path, style, width, height, **kwargs):
    """Generate a texture and save as PNG."""
    pixels = generate_texture(style, width, height, **kwargs)
    _write_png(output_path, pixels, width, height)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate seamless tileable Voronoi textures.",
        epilog="Styles: " + ", ".join(sorted(STYLES.keys())),
    )
    parser.add_argument("style", choices=sorted(STYLES.keys()),
                        help="Texture style")
    parser.add_argument("width", type=int, help="Image width in pixels")
    parser.add_argument("height", type=int, help="Image height in pixels")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--seeds", type=int, default=120,
                        help="Number of seed points (default: 120)")
    parser.add_argument("--colormap", choices=sorted(COLORMAPS.keys()),
                        default="grey", help="Color palette (default: grey)")
    parser.add_argument("--border", type=int, default=0,
                        help="Extra border width in pixels (default: 0)")
    parser.add_argument("--border-color", default="000000",
                        help="Border colour as hex (default: 000000)")
    parser.add_argument("--seed-value", type=int, default=None,
                        help="Random seed for reproducibility")

    args = parser.parse_args()

    bc = args.border_color.lstrip("#")
    border_rgb = (int(bc[0:2], 16), int(bc[2:4], 16), int(bc[4:6], 16))

    print(f"Generating {args.style} texture ({args.width}x{args.height}, "
          f"{args.seeds} seeds, colormap={args.colormap})...")

    generate_texture_to_file(
        args.output, args.style, args.width, args.height,
        num_seeds=args.seeds, colormap=args.colormap,
        border=args.border, border_color=border_rgb,
        seed_value=args.seed_value,
    )
    size_kb = os.path.getsize(args.output) / 1024
    print(f"Saved -> {args.output} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
