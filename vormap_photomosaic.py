"""Voronoi photo-mosaic renderer.

Transforms a source image into a poster-art mosaic by partitioning it with
a Voronoi tessellation then re-filling each cell using a **quantised colour
palette** and an optional **texture fill** (solid, dots, crosshatch).

Unlike ``vormap_lowpoly`` (which faithfully reproduces the original colours),
this module deliberately *reduces* the palette to a small number of
representative colours (k-means quantisation), giving the output a bold,
screen-print / risograph / pop-art feel.

Features
--------
- **k-means colour quantisation** — reduces the image to *k* colours
  (default 12) for a bold poster-art look
- **Edge-aware seed placement** — concentrates seeds along edges to
  preserve structure while keeping flat areas coarse
- **Texture fill modes**: ``solid`` (flat colour), ``dots`` (stipple dots
  inside each cell), ``crosshatch`` (diagonal hatch lines)
- **Custom palette override** — supply your own hex colours and the image
  will be mapped to the nearest ones
- Configurable seed count, outline width, dot density, hatch spacing
- Pure-Python PNG I/O — no Pillow / scipy required

CLI usage
---------
::

    python vormap_photomosaic.py input.png output.png
    python vormap_photomosaic.py input.png output.png --seeds 600 --colours 8
    python vormap_photomosaic.py input.png output.png --fill dots --dot-radius 2
    python vormap_photomosaic.py input.png output.png --fill crosshatch --hatch-spacing 6
    python vormap_photomosaic.py input.png output.png --palette "#E63946,#457B9D,#1D3557,#F1FAEE,#A8DADC"
    python vormap_photomosaic.py input.png output.png --seeds 400 --colours 6 --outline 2

Programmatic usage
------------------
::

    from vormap_photomosaic import photo_mosaic
    photo_mosaic("photo.png", "mosaic.png", seeds=500, colours=10, fill="dots")
"""

from __future__ import annotations

import argparse
import math
import random
import struct
import sys
import zlib
from typing import List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Minimal PNG reader / writer (RGB only, no dependencies)
# ---------------------------------------------------------------------------

def _read_png(path: str) -> Tuple[int, int, List[List[Tuple[int, int, int]]]]:
    """Return (width, height, pixels) where pixels[y][x] = (r, g, b)."""
    with open(path, "rb") as fh:
        sig = fh.read(8)
        if sig[:4] != b"\x89PNG":
            raise ValueError("Not a PNG file")
        width = height = bpp = 0
        raw_idat = b""
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            length = struct.unpack(">I", hdr[:4])[0]
            ctype = hdr[4:8]
            data = fh.read(length)
            fh.read(4)  # crc
            if ctype == b"IHDR":
                width = struct.unpack(">I", data[0:4])[0]
                height = struct.unpack(">I", data[4:8])[0]
                bit_depth = data[8]
                colour_type = data[9]
                if bit_depth != 8 or colour_type not in (2, 6):
                    raise ValueError("Only 8-bit RGB/RGBA PNGs supported")
                bpp = 3 if colour_type == 2 else 4
            elif ctype == b"IDAT":
                raw_idat += data
            elif ctype == b"IEND":
                break
        decompressed = zlib.decompress(raw_idat)
        stride = 1 + width * bpp
        pixels: List[List[Tuple[int, int, int]]] = []
        prev_row = bytes(width * bpp)
        idx = 0
        for _y in range(height):
            filt = decompressed[idx]
            idx += 1
            row_bytes = bytearray(width * bpp)
            for i in range(width * bpp):
                raw = decompressed[idx]; idx += 1
                a = row_bytes[i - bpp] if i >= bpp else 0
                b_val = prev_row[i]
                if filt == 0:
                    row_bytes[i] = raw & 0xFF
                elif filt == 1:
                    row_bytes[i] = (raw + a) & 0xFF
                elif filt == 2:
                    row_bytes[i] = (raw + b_val) & 0xFF
                elif filt == 3:
                    row_bytes[i] = (raw + (a + b_val) // 2) & 0xFF
                elif filt == 4:
                    c = prev_row[i - bpp] if i >= bpp else 0
                    p = a + b_val - c
                    pa, pb, pc = abs(p - a), abs(p - b_val), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else (b_val if pb <= pc else c)
                    row_bytes[i] = (raw + pr) & 0xFF
            prev_row = bytes(row_bytes)
            row: List[Tuple[int, int, int]] = []
            for x in range(width):
                o = x * bpp
                row.append((row_bytes[o], row_bytes[o + 1], row_bytes[o + 2]))
            pixels.append(row)
    return width, height, pixels


def _write_png(path: str, width: int, height: int,
               pixels: List[List[Tuple[int, int, int]]]) -> None:
    """Write an RGB PNG."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # no filter
        for r, g, b in row:
            raw += bytes((r, g, b))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_chunk(b"IHDR", ihdr))
        fh.write(_chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        fh.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# k-means colour quantisation
# ---------------------------------------------------------------------------

def _kmeans(samples: List[Tuple[int, int, int]], k: int,
            max_iter: int = 20) -> List[Tuple[int, int, int]]:
    """Return *k* representative colours from *samples*."""
    if len(samples) <= k:
        return list(samples)
    centres = random.sample(samples, k)
    for _ in range(max_iter):
        buckets: List[List[Tuple[int, int, int]]] = [[] for _ in range(k)]
        for s in samples:
            best = 0
            best_d = float("inf")
            for ci, c in enumerate(centres):
                d = (s[0] - c[0]) ** 2 + (s[1] - c[1]) ** 2 + (s[2] - c[2]) ** 2
                if d < best_d:
                    best_d = d
                    best = ci
            buckets[best].append(s)
        new_centres: List[Tuple[int, int, int]] = []
        for ci in range(k):
            if buckets[ci]:
                n = len(buckets[ci])
                r = sum(p[0] for p in buckets[ci]) // n
                g = sum(p[1] for p in buckets[ci]) // n
                b = sum(p[2] for p in buckets[ci]) // n
                new_centres.append((r, g, b))
            else:
                new_centres.append(centres[ci])
        if new_centres == centres:
            break
        centres = new_centres
    return centres


def _nearest_colour(pixel: Tuple[int, int, int],
                    palette: List[Tuple[int, int, int]]) -> Tuple[int, int, int]:
    best = palette[0]
    best_d = float("inf")
    for c in palette:
        d = (pixel[0] - c[0]) ** 2 + (pixel[1] - c[1]) ** 2 + (pixel[2] - c[2]) ** 2
        if d < best_d:
            best_d = d
            best = c
    return best


# ---------------------------------------------------------------------------
# Simple edge detection (Sobel-ish) for seed biasing
# ---------------------------------------------------------------------------

def _luminance(r: int, g: int, b: int) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def _edge_map(pixels: List[List[Tuple[int, int, int]]],
              width: int, height: int) -> List[List[float]]:
    lum = [[_luminance(*pixels[y][x]) for x in range(width)] for y in range(height)]
    edge = [[0.0] * width for _ in range(height)]
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            gx = (-lum[y-1][x-1] + lum[y-1][x+1]
                  - 2*lum[y][x-1] + 2*lum[y][x+1]
                  - lum[y+1][x-1] + lum[y+1][x+1])
            gy = (-lum[y-1][x-1] - 2*lum[y-1][x] - lum[y-1][x+1]
                  + lum[y+1][x-1] + 2*lum[y+1][x] + lum[y+1][x+1])
            edge[y][x] = math.sqrt(gx * gx + gy * gy)
    return edge


# ---------------------------------------------------------------------------
# Voronoi assignment (brute force for simplicity)
# ---------------------------------------------------------------------------

def _assign_voronoi(width: int, height: int,
                    seeds: List[Tuple[int, int]]) -> List[List[int]]:
    """Return grid where cell[y][x] = index of nearest seed."""
    grid = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            best_i = 0
            best_d = float("inf")
            for si, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = si
            grid[y][x] = best_i
    return grid


# ---------------------------------------------------------------------------
# Cell average colour
# ---------------------------------------------------------------------------

def _cell_colours(grid: List[List[int]],
                  pixels: List[List[Tuple[int, int, int]]],
                  n_seeds: int,
                  palette: List[Tuple[int, int, int]]) -> List[Tuple[int, int, int]]:
    """Average colour per cell, snapped to nearest palette colour."""
    sums = [[0, 0, 0, 0] for _ in range(n_seeds)]  # r, g, b, count
    h = len(pixels)
    w = len(pixels[0]) if h else 0
    for y in range(h):
        for x in range(w):
            ci = grid[y][x]
            r, g, b = pixels[y][x]
            sums[ci][0] += r
            sums[ci][1] += g
            sums[ci][2] += b
            sums[ci][3] += 1
    result: List[Tuple[int, int, int]] = []
    for s in sums:
        if s[3] > 0:
            avg = (s[0] // s[3], s[1] // s[3], s[2] // s[3])
        else:
            avg = (128, 128, 128)
        result.append(_nearest_colour(avg, palette))
    return result


# ---------------------------------------------------------------------------
# Texture fills
# ---------------------------------------------------------------------------

def _is_cell_border(grid: List[List[int]], x: int, y: int,
                    width: int, height: int) -> bool:
    ci = grid[y][x]
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        ny, nx = y + dy, x + dx
        if 0 <= ny < height and 0 <= nx < width and grid[ny][nx] != ci:
            return True
    return False


def _dot_fill(x: int, y: int, dot_radius: int, dot_spacing: int) -> bool:
    """True if (x, y) falls inside a repeating dot grid."""
    cx = (x // dot_spacing) * dot_spacing + dot_spacing // 2
    cy = (y // dot_spacing) * dot_spacing + dot_spacing // 2
    return (x - cx) ** 2 + (y - cy) ** 2 <= dot_radius ** 2


def _crosshatch_fill(x: int, y: int, spacing: int) -> bool:
    """True if (x, y) is on a diagonal hatch line."""
    return (x + y) % spacing == 0 or (x - y) % spacing == 0


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def photo_mosaic(
    input_path: str,
    output_path: str,
    *,
    seeds: int = 400,
    colours: int = 12,
    fill: str = "solid",
    outline: int = 1,
    edge_bias: float = 0.7,
    dot_radius: int = 2,
    dot_spacing: int = 8,
    hatch_spacing: int = 5,
    palette: Optional[Sequence[str]] = None,
    bg_colour: Tuple[int, int, int] = (255, 255, 255),
) -> str:
    """Render a Voronoi photo mosaic and write to *output_path*.

    Parameters
    ----------
    input_path : str
        Path to an 8-bit RGB or RGBA PNG.
    output_path : str
        Destination PNG path.
    seeds : int
        Number of Voronoi seed points.
    colours : int
        Number of quantised palette colours (ignored if *palette* given).
    fill : str
        Cell texture: ``"solid"``, ``"dots"``, or ``"crosshatch"``.
    outline : int
        Cell border width in pixels (0 = no border).
    edge_bias : float
        Fraction of seeds placed along edges (0–1).
    dot_radius, dot_spacing : int
        Controls for ``"dots"`` fill mode.
    hatch_spacing : int
        Line spacing for ``"crosshatch"`` fill mode.
    palette : sequence of hex strings, optional
        Custom colour palette (e.g. ``["#E63946", "#457B9D"]``).
    bg_colour : tuple
        Background / border colour (r, g, b).

    Returns
    -------
    str
        The *output_path*.
    """
    width, height, pixels = _read_png(input_path)

    # Build palette
    if palette:
        pal = []
        for h in palette:
            h = h.lstrip("#")
            pal.append((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    else:
        # Sample pixels for k-means
        step = max(1, (width * height) // 2000)
        samples = [pixels[y][x]
                   for y in range(0, height, max(1, int(math.sqrt(step))))
                   for x in range(0, width, max(1, int(math.sqrt(step))))]
        pal = _kmeans(samples, colours)

    # Edge map for biased seed placement
    emap = _edge_map(pixels, width, height)
    max_edge = max(max(row) for row in emap) or 1.0

    # Place seeds
    n_edge = int(seeds * edge_bias)
    n_rand = seeds - n_edge

    seed_pts: List[Tuple[int, int]] = []
    # Edge-biased seeds
    flat: List[Tuple[float, int, int]] = []
    for y in range(height):
        for x in range(width):
            if emap[y][x] / max_edge > 0.15:
                flat.append((emap[y][x], x, y))
    flat.sort(key=lambda t: -t[0])
    if flat:
        chosen = flat[:n_edge * 3]
        random.shuffle(chosen)
        for _, x, y in chosen[:n_edge]:
            seed_pts.append((x, y))

    # Random seeds
    for _ in range(n_rand):
        seed_pts.append((random.randint(0, width - 1), random.randint(0, height - 1)))

    if not seed_pts:
        seed_pts.append((width // 2, height // 2))

    # Voronoi assignment
    grid = _assign_voronoi(width, height, seed_pts)

    # Compute cell colours
    cell_cols = _cell_colours(grid, pixels, len(seed_pts), pal)

    # Render
    out: List[List[Tuple[int, int, int]]] = [[bg_colour] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            # Border check
            if outline > 0 and _is_cell_border(grid, x, y, width, height):
                # Thicker borders: check within outline radius
                out[y][x] = bg_colour
                continue
            c = cell_cols[grid[y][x]]
            if fill == "dots":
                if _dot_fill(x, y, dot_radius, dot_spacing):
                    out[y][x] = c
                else:
                    # Lighter version of colour for background
                    out[y][x] = (min(255, c[0] + 60),
                                 min(255, c[1] + 60),
                                 min(255, c[2] + 60))
            elif fill == "crosshatch":
                if _crosshatch_fill(x, y, hatch_spacing):
                    out[y][x] = c
                else:
                    out[y][x] = (min(255, c[0] + 40),
                                 min(255, c[1] + 40),
                                 min(255, c[2] + 40))
            else:
                out[y][x] = c

    _write_png(output_path, width, height, out)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi photo-mosaic renderer — poster-art style with "
                    "colour quantisation and texture fills.")
    parser.add_argument("input", help="Input PNG image path")
    parser.add_argument("output", help="Output PNG image path")
    parser.add_argument("--seeds", type=int, default=400,
                        help="Number of Voronoi seeds (default 400)")
    parser.add_argument("--colours", type=int, default=12,
                        help="Number of palette colours (default 12)")
    parser.add_argument("--fill", choices=["solid", "dots", "crosshatch"],
                        default="solid", help="Cell texture fill mode")
    parser.add_argument("--outline", type=int, default=1,
                        help="Border width in pixels (0=none)")
    parser.add_argument("--edge-bias", type=float, default=0.7,
                        help="Fraction of seeds on edges (0-1)")
    parser.add_argument("--dot-radius", type=int, default=2,
                        help="Dot radius for 'dots' fill")
    parser.add_argument("--dot-spacing", type=int, default=8,
                        help="Dot grid spacing for 'dots' fill")
    parser.add_argument("--hatch-spacing", type=int, default=5,
                        help="Line spacing for 'crosshatch' fill")
    parser.add_argument("--palette", type=str, default=None,
                        help="Comma-separated hex colours (e.g. '#E63946,#457B9D')")
    args = parser.parse_args(argv)

    pal = args.palette.split(",") if args.palette else None

    out = photo_mosaic(
        args.input, args.output,
        seeds=args.seeds, colours=args.colours,
        fill=args.fill, outline=args.outline,
        edge_bias=args.edge_bias,
        dot_radius=args.dot_radius, dot_spacing=args.dot_spacing,
        hatch_spacing=args.hatch_spacing, palette=pal,
    )
    print(f"Mosaic written to {out}")


if __name__ == "__main__":
    main()
