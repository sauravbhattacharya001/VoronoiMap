"""Voronoi Halftone Renderer.

Converts an image into a halftone-style output where Voronoi cells are
represented by circles (dots) whose size is proportional to the darkness
of the corresponding region in the source image.  The result mimics
newspaper halftone printing with a Voronoi twist — irregular, organic
dot placement instead of a rigid grid.

Features
--------
- **Adaptive dot sizing** — darker regions get larger dots, lighter regions
  get smaller dots (or vice-versa with ``--invert``)
- **Edge-aware seed placement** — more dots along edges for detail retention
- Configurable dot count, background colour, dot colour, and min/max radius
- **Multi-colour CMYK mode** — layer cyan, magenta, yellow, and black dots
  for full-colour halftone output
- Supports PNG input/output using only the standard library
- No external dependencies

CLI usage
---------
::

    python vormap_halftone.py input.png output.png
    python vormap_halftone.py input.png output.png --dots 1000
    python vormap_halftone.py input.png output.png --dots 600 --bg 255,255,255 --fg 0,0,0
    python vormap_halftone.py input.png output.png --dots 800 --cmyk
    python vormap_halftone.py input.png output.png --dots 500 --invert --min-radius 1 --max-radius 8

Programmatic usage
------------------
::

    import vormap_halftone

    result = vormap_halftone.render("photo.png", num_dots=600)
    vormap_halftone.save_png(result, "halftone.png")

    # CMYK colour halftone:
    result = vormap_halftone.render("photo.png", num_dots=800, cmyk=True)
    vormap_halftone.save_png(result, "halftone_cmyk.png")

"""

import argparse
import math
import os
import random
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# PNG reader (minimal, supports 8-bit RGB/RGBA non-interlaced)
# ---------------------------------------------------------------------------

def _read_png(path: str) -> Tuple[int, int, List[Tuple[int, int, int]]]:
    """Read a PNG file and return (width, height, pixels) where pixels is a
    flat list of (R, G, B) tuples in row-major order."""
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a valid PNG file")

        idat_data = b""
        width = height = 0
        bit_depth = color_type = 0

        while True:
            raw = f.read(8)
            if len(raw) < 8:
                break
            length = struct.unpack(">I", raw[:4])[0]
            ctype = raw[4:8].decode("ascii", errors="replace")
            data = f.read(length)
            _crc = f.read(4)

            if ctype == "IHDR":
                width = struct.unpack(">I", data[0:4])[0]
                height = struct.unpack(">I", data[4:8])[0]
                bit_depth = data[8]
                color_type = data[9]
            elif ctype == "IDAT":
                idat_data += data
            elif ctype == "IEND":
                break

        if bit_depth != 8:
            raise ValueError(f"Only 8-bit PNGs supported (got {bit_depth})")

        raw_data = zlib.decompress(idat_data)

        if color_type == 2:
            bpp = 3
        elif color_type == 6:
            bpp = 4
        elif color_type == 0:
            bpp = 1
        elif color_type == 4:
            bpp = 2
        else:
            raise ValueError(f"Unsupported color type {color_type}")

        stride = 1 + width * bpp
        pixels: List[Tuple[int, int, int]] = []
        prev_row = bytes(width * bpp)
        offset = 0

        for y in range(height):
            filter_type = raw_data[offset]
            row_data = bytearray(raw_data[offset + 1: offset + stride])
            offset += stride

            recon = bytearray(len(row_data))
            for i in range(len(row_data)):
                a = recon[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0

                if filter_type == 0:
                    recon[i] = row_data[i]
                elif filter_type == 1:
                    recon[i] = (row_data[i] + a) & 0xFF
                elif filter_type == 2:
                    recon[i] = (row_data[i] + b) & 0xFF
                elif filter_type == 3:
                    recon[i] = (row_data[i] + (a + b) // 2) & 0xFF
                elif filter_type == 4:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                    recon[i] = (row_data[i] + pr) & 0xFF

            for x in range(width):
                idx = x * bpp
                if bpp >= 3:
                    pixels.append((recon[idx], recon[idx + 1], recon[idx + 2]))
                elif bpp == 1:
                    v = recon[idx]
                    pixels.append((v, v, v))
                elif bpp == 2:
                    v = recon[idx]
                    pixels.append((v, v, v))

            prev_row = bytes(recon)

    return width, height, pixels


# ---------------------------------------------------------------------------
# PNG writer (minimal)
# ---------------------------------------------------------------------------

def _write_png(path: str, width: int, height: int,
               pixels: List[Tuple[int, int, int]]) -> None:
    """Write an RGB PNG file."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw_rows.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Edge detection (Sobel)
# ---------------------------------------------------------------------------

def _edge_map(pixels: List[Tuple[int, int, int]], w: int, h: int) -> List[float]:
    """Compute edge-magnitude map (0.0-1.0)."""
    gray = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    edges = [0.0] * (w * h)
    max_val = 1.0

    for y in range(1, h - 1):
        for x in range(1, w - 1):
            gx = (
                -gray[(y - 1) * w + x - 1] + gray[(y - 1) * w + x + 1]
                - 2 * gray[y * w + x - 1] + 2 * gray[y * w + x + 1]
                - gray[(y + 1) * w + x - 1] + gray[(y + 1) * w + x + 1]
            )
            gy = (
                -gray[(y - 1) * w + x - 1] - 2 * gray[(y - 1) * w + x]
                - gray[(y - 1) * w + x + 1]
                + gray[(y + 1) * w + x - 1] + 2 * gray[(y + 1) * w + x]
                + gray[(y + 1) * w + x + 1]
            )
            mag = math.sqrt(gx * gx + gy * gy)
            edges[y * w + x] = mag
            if mag > max_val:
                max_val = mag

    if max_val > 0:
        edges = [e / max_val for e in edges]
    return edges


# ---------------------------------------------------------------------------
# Seed placement (edge-biased)
# ---------------------------------------------------------------------------

def _place_seeds(w: int, h: int, num_seeds: int,
                 edge_bias: float,
                 edges: List[float]) -> List[Tuple[int, int]]:
    """Place seeds with bias towards high-edge areas."""
    seeds: List[Tuple[int, int]] = []
    n_edge = int(num_seeds * edge_bias)
    n_rand = num_seeds - n_edge

    # Weighted random for edge-biased seeds
    if n_edge > 0:
        weights = [e + 0.01 for e in edges]
        total = sum(weights)
        cum = []
        s = 0.0
        for wt in weights:
            s += wt / total
            cum.append(s)

        for _ in range(n_edge):
            r = random.random()
            lo, hi = 0, len(cum) - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if cum[mid] < r:
                    lo = mid + 1
                else:
                    hi = mid
            idx = lo
            y, x = divmod(idx, w)
            seeds.append((x, y))

    # Uniform random seeds
    for _ in range(n_rand):
        seeds.append((random.randint(0, w - 1), random.randint(0, h - 1)))

    return seeds


# ---------------------------------------------------------------------------
# Voronoi assignment (brute-force nearest seed per pixel)
# ---------------------------------------------------------------------------

def _assign_voronoi(w: int, h: int,
                    seeds: List[Tuple[int, int]]) -> List[int]:
    """Assign each pixel to its nearest seed. Returns cell index per pixel."""
    # Use grid acceleration for speed
    n = len(seeds)
    cell_size = max(1, int(math.sqrt(w * h / max(n, 1))))
    grid_w = (w + cell_size - 1) // cell_size
    grid_h = (h + cell_size - 1) // cell_size
    grid: Dict[Tuple[int, int], List[int]] = {}

    for i, (sx, sy) in enumerate(seeds):
        gx, gy = sx // cell_size, sy // cell_size
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                key = (gx + dx, gy + dy)
                if key not in grid:
                    grid[key] = []
                grid[key].append(i)

    assignment = [0] * (w * h)
    for y in range(h):
        gy = y // cell_size
        for x in range(w):
            gx = x // cell_size
            best_d = float("inf")
            best_i = 0
            candidates = grid.get((gx, gy), range(n))
            for i in candidates:
                sx, sy = seeds[i]
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            assignment[y * w + x] = best_i

    return assignment


# ---------------------------------------------------------------------------
# Average colour and brightness per cell
# ---------------------------------------------------------------------------

def _cell_stats(pixels: List[Tuple[int, int, int]], assignment: List[int],
                num_cells: int, w: int, h: int) -> List[Tuple[float, Tuple[int, int, int]]]:
    """Return (brightness_0_to_1, avg_colour) for each cell."""
    r_sum = [0.0] * num_cells
    g_sum = [0.0] * num_cells
    b_sum = [0.0] * num_cells
    count = [0] * num_cells

    for idx in range(w * h):
        c = assignment[idx]
        r, g, b = pixels[idx]
        r_sum[c] += r
        g_sum[c] += g
        b_sum[c] += b
        count[c] += 1

    result = []
    for i in range(num_cells):
        if count[i] == 0:
            result.append((1.0, (255, 255, 255)))
            continue
        ar = int(r_sum[i] / count[i])
        ag = int(g_sum[i] / count[i])
        ab = int(b_sum[i] / count[i])
        brightness = (0.299 * ar + 0.587 * ag + 0.114 * ab) / 255.0
        result.append((brightness, (ar, ag, ab)))

    return result


# ---------------------------------------------------------------------------
# Circle rasterizer
# ---------------------------------------------------------------------------

def _draw_filled_circle(canvas: List[Tuple[int, int, int]], w: int, h: int,
                        cx: int, cy: int, radius: float,
                        colour: Tuple[int, int, int]) -> None:
    """Draw a filled circle onto the canvas (opaque)."""
    _draw_blended_circle(canvas, w, h, cx, cy, radius, colour, 1.0)


# ---------------------------------------------------------------------------
# RGB ↔ CMYK helpers
# ---------------------------------------------------------------------------

def _rgb_to_cmyk(r: int, g: int, b: int) -> Tuple[float, float, float, float]:
    """Convert RGB (0-255) to CMYK (0.0-1.0)."""
    r_, g_, b_ = r / 255.0, g / 255.0, b / 255.0
    k = 1.0 - max(r_, g_, b_)
    if k >= 1.0:
        return (0.0, 0.0, 0.0, 1.0)
    c = (1.0 - r_ - k) / (1.0 - k)
    m = (1.0 - g_ - k) / (1.0 - k)
    y = (1.0 - b_ - k) / (1.0 - k)
    return (c, m, y, k)


# ---------------------------------------------------------------------------
# Alpha-blend helper for CMYK layering
# ---------------------------------------------------------------------------

def _blend(base: Tuple[int, int, int], overlay: Tuple[int, int, int],
           alpha: float) -> Tuple[int, int, int]:
    """Alpha blend overlay onto base."""
    inv = 1.0 - alpha
    return (
        int(base[0] * inv + overlay[0] * alpha),
        int(base[1] * inv + overlay[1] * alpha),
        int(base[2] * inv + overlay[2] * alpha),
    )


def _draw_blended_circle(canvas: List[Tuple[int, int, int]], w: int, h: int,
                          cx: int, cy: int, radius: float,
                          colour: Tuple[int, int, int],
                          alpha: float = 1.0) -> None:
    """Draw a filled circle with optional alpha blending.

    When *alpha* is 1.0, the colour overwrites existing pixels directly
    (no blending overhead).
    """
    r_int = int(math.ceil(radius))
    r_sq = radius * radius
    opaque = alpha >= 1.0
    y_lo = max(0, cy - r_int)
    y_hi = min(h - 1, cy + r_int)
    x_lo = max(0, cx - r_int)
    x_hi = min(w - 1, cx + r_int)
    for py in range(y_lo, y_hi + 1):
        dy = py - cy
        dy_sq = dy * dy
        row_off = py * w
        for px in range(x_lo, x_hi + 1):
            dx = px - cx
            if dx * dx + dy_sq <= r_sq:
                if opaque:
                    canvas[row_off + px] = colour
                else:
                    canvas[row_off + px] = _blend(canvas[row_off + px], colour, alpha)


# ---------------------------------------------------------------------------
# Main renderers
# ---------------------------------------------------------------------------

@dataclass
class HalftoneResult:
    """Container for halftone render output."""
    width: int
    height: int
    pixels: List[Tuple[int, int, int]]
    num_dots: int
    seeds: List[Tuple[int, int]] = field(default_factory=list)


def render(input_path: str, *,
           num_dots: int = 600,
           edge_bias: float = 0.6,
           min_radius: float = 1.0,
           max_radius: float = 0.0,
           bg: Tuple[int, int, int] = (255, 255, 255),
           fg: Tuple[int, int, int] = (0, 0, 0),
           invert: bool = False,
           cmyk: bool = False,
           seed: Optional[int] = None) -> HalftoneResult:
    """Render halftone from a PNG file.

    Parameters
    ----------
    input_path : str
        Path to input PNG.
    num_dots : int
        Number of Voronoi dots.
    edge_bias : float
        0.0-1.0, fraction of dots placed along edges.
    min_radius : float
        Minimum dot radius in pixels.
    max_radius : float
        Maximum dot radius (0 = auto based on image size).
    bg : tuple
        Background colour (R, G, B).
    fg : tuple
        Dot colour for mono mode (R, G, B).
    invert : bool
        If True, lighter areas get bigger dots (white-on-black style).
    cmyk : bool
        If True, render in 4-colour CMYK halftone.
    seed : int or None
        Random seed for reproducibility.
    """
    if seed is not None:
        random.seed(seed)

    w, h, pixels = _read_png(input_path)
    return render_from_pixels(pixels, w, h,
                              num_dots=num_dots, edge_bias=edge_bias,
                              min_radius=min_radius, max_radius=max_radius,
                              bg=bg, fg=fg, invert=invert, cmyk=cmyk)


def render_from_pixels(pixels: List[Tuple[int, int, int]],
                       w: int, h: int, *,
                       num_dots: int = 600,
                       edge_bias: float = 0.6,
                       min_radius: float = 1.0,
                       max_radius: float = 0.0,
                       bg: Tuple[int, int, int] = (255, 255, 255),
                       fg: Tuple[int, int, int] = (0, 0, 0),
                       invert: bool = False,
                       cmyk: bool = False) -> HalftoneResult:
    """Render halftone from raw pixel data."""
    # Auto max_radius based on image dimensions and dot count
    if max_radius <= 0:
        avg_cell_area = (w * h) / max(num_dots, 1)
        max_radius = math.sqrt(avg_cell_area / math.pi) * 1.1

    edges = _edge_map(pixels, w, h)
    seeds = _place_seeds(w, h, num_dots, edge_bias, edges)
    assignment = _assign_voronoi(w, h, seeds)
    stats = _cell_stats(pixels, assignment, len(seeds), w, h)

    # Create canvas
    canvas: List[Tuple[int, int, int]] = [bg] * (w * h)

    if cmyk:
        # CMYK halftone: compute per-cell CMYK values and draw 4 layers
        cmyk_values = []
        for brightness, avg_col in stats:
            c, m, y, k = _rgb_to_cmyk(*avg_col)
            cmyk_values.append((c, m, y, k))

        # Layer colours: Cyan, Magenta, Yellow, Key(Black)
        layer_colours = [
            (0, 180, 230),   # Cyan
            (220, 40, 120),  # Magenta
            (240, 220, 0),   # Yellow
            (30, 30, 30),    # Key (near-black)
        ]

        for layer_idx, layer_col in enumerate(layer_colours):
            for i, (sx, sy) in enumerate(seeds):
                ink = cmyk_values[i][layer_idx]
                if ink < 0.02:
                    continue  # skip negligible ink
                radius = min_radius + ink * (max_radius - min_radius)
                alpha = min(1.0, ink * 1.2)
                # Offset seeds slightly per layer for rosette effect
                offsets = [(0, 0), (1, 1), (-1, 1), (0, -1)]
                ox, oy = offsets[layer_idx]
                _draw_blended_circle(canvas, w, h,
                                     sx + ox, sy + oy, radius,
                                     layer_col, alpha)
    else:
        # Mono halftone
        for i, (sx, sy) in enumerate(seeds):
            brightness, _ = stats[i]
            if invert:
                darkness = brightness  # light = big dot
            else:
                darkness = 1.0 - brightness  # dark = big dot

            if darkness < 0.02:
                continue  # skip nearly-invisible dots

            radius = min_radius + darkness * (max_radius - min_radius)
            _draw_filled_circle(canvas, w, h, sx, sy, radius, fg)

    return HalftoneResult(
        width=w, height=h, pixels=canvas,
        num_dots=len(seeds), seeds=seeds,
    )


def save_png(result: HalftoneResult, output_path: str) -> None:
    """Save a HalftoneResult to PNG."""
    _write_png(output_path, result.width, result.height, result.pixels)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_colour(s: str) -> Tuple[int, int, int]:
    """Parse 'R,G,B' string."""
    parts = s.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"Expected R,G,B but got: {s}")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi Halftone Renderer — convert images to halftone dot art"
    )
    parser.add_argument("input", help="Input PNG path")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--dots", type=int, default=600,
                        help="Number of Voronoi dots (default: 600)")
    parser.add_argument("--edge-bias", type=float, default=0.6,
                        help="Edge bias 0.0-1.0 (default: 0.6)")
    parser.add_argument("--min-radius", type=float, default=1.0,
                        help="Minimum dot radius in pixels (default: 1.0)")
    parser.add_argument("--max-radius", type=float, default=0.0,
                        help="Maximum dot radius, 0=auto (default: 0)")
    parser.add_argument("--bg", type=_parse_colour, default=(255, 255, 255),
                        help="Background colour R,G,B (default: 255,255,255)")
    parser.add_argument("--fg", type=_parse_colour, default=(0, 0, 0),
                        help="Dot colour R,G,B for mono mode (default: 0,0,0)")
    parser.add_argument("--invert", action="store_true",
                        help="Invert: lighter areas get larger dots")
    parser.add_argument("--cmyk", action="store_true",
                        help="Render in 4-colour CMYK halftone")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")

    args = parser.parse_args()

    print(f"Reading {args.input}...")
    result = render(
        args.input,
        num_dots=args.dots,
        edge_bias=args.edge_bias,
        min_radius=args.min_radius,
        max_radius=args.max_radius,
        bg=args.bg,
        fg=args.fg,
        invert=args.invert,
        cmyk=args.cmyk,
        seed=args.seed,
    )
    save_png(result, args.output)
    print(f"Halftone saved to {args.output} ({result.num_dots} dots, "
          f"{result.width}x{result.height})")


if __name__ == "__main__":
    main()
