"""Voronoi cross-stitch pattern generator.

Converts a Voronoi diagram into a printable cross-stitch pattern with a
grid of stitch symbols, colour legend, and optional PNG preview.  Each
Voronoi cell maps to a region of stitches in a single thread colour chosen
from popular embroidery-floss palettes (DMC).

Features
--------
- Configurable **grid size** (stitch count across width)
- Multiple **palette styles**: pastel, jewel, earth, monochrome, rainbow
- Optional **backstitch outlines** on cell edges
- Generates both a **text pattern** (.txt) and a **PNG chart** image
- Includes a **colour legend** with DMC-like codes and stitch counts
- Adjustable **cell count** (number of Voronoi seeds)

CLI usage
---------
::

    python vormap_crossstitch.py 60 80 pattern.txt
    python vormap_crossstitch.py 60 80 pattern.txt --seeds 15 --palette jewel
    python vormap_crossstitch.py 80 60 pattern.txt --chart chart.png --backstitch
    python vormap_crossstitch.py 40 40 pattern.txt --palette monochrome --cell-size 12

Programmatic usage
------------------
::

    import vormap_crossstitch

    result = vormap_crossstitch.generate(60, 80, num_seeds=20, palette="pastel")
    vormap_crossstitch.save_pattern(result, "pattern.txt")
    vormap_crossstitch.save_chart(result, "chart.png")

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
# DMC-inspired colour palettes
# ---------------------------------------------------------------------------

PALETTES: Dict[str, List[Tuple[str, Tuple[int, int, int], str]]] = {
    # (name, RGB, symbol)
    "pastel": [
        ("Baby Pink", (255, 200, 200), "o"),
        ("Lavender", (200, 180, 240), "+"),
        ("Mint", (180, 240, 200), "x"),
        ("Lemon", (255, 255, 180), "*"),
        ("Sky", (180, 220, 255), "#"),
        ("Peach", (255, 218, 185), "@"),
        ("Lilac", (220, 190, 255), "~"),
        ("Seafoam", (180, 255, 230), "="),
        ("Rose", (255, 180, 210), "^"),
        ("Cream", (255, 250, 220), "&"),
        ("Powder", (200, 210, 255), "%"),
        ("Blush", (255, 200, 220), "!"),
    ],
    "jewel": [
        ("Ruby", (180, 30, 50), "o"),
        ("Sapphire", (30, 60, 180), "+"),
        ("Emerald", (20, 140, 60), "x"),
        ("Amethyst", (130, 50, 160), "*"),
        ("Topaz", (220, 170, 30), "#"),
        ("Garnet", (140, 20, 40), "@"),
        ("Jade", (30, 130, 90), "~"),
        ("Citrine", (230, 200, 40), "="),
        ("Onyx", (40, 40, 45), "^"),
        ("Pearl", (235, 230, 220), "&"),
        ("Opal", (200, 220, 240), "%"),
        ("Coral", (220, 90, 70), "!"),
    ],
    "earth": [
        ("Bark", (100, 70, 40), "o"),
        ("Moss", (80, 110, 50), "+"),
        ("Sand", (210, 190, 150), "x"),
        ("Clay", (180, 120, 80), "*"),
        ("Slate", (110, 120, 130), "#"),
        ("Olive", (130, 140, 60), "@"),
        ("Sienna", (160, 80, 40), "~"),
        ("Wheat", (230, 210, 170), "="),
        ("Pebble", (160, 155, 145), "^"),
        ("Walnut", (80, 50, 30), "&"),
        ("Lichen", (140, 160, 120), "%"),
        ("Rust", (180, 70, 30), "!"),
    ],
    "monochrome": [
        ("White", (240, 240, 240), "."),
        ("Light Grey", (200, 200, 200), "o"),
        ("Silver", (170, 170, 170), "+"),
        ("Grey", (140, 140, 140), "x"),
        ("Pewter", (115, 115, 115), "*"),
        ("Charcoal", (90, 90, 90), "#"),
        ("Dark Grey", (65, 65, 65), "@"),
        ("Graphite", (45, 45, 45), "~"),
        ("Near Black", (30, 30, 30), "="),
        ("Ash", (155, 155, 155), "^"),
        ("Smoke", (180, 180, 180), "&"),
        ("Iron", (100, 100, 100), "%"),
    ],
    "rainbow": [
        ("Red", (220, 40, 40), "o"),
        ("Orange", (240, 150, 30), "+"),
        ("Yellow", (240, 230, 40), "x"),
        ("Chartreuse", (140, 210, 40), "*"),
        ("Green", (40, 180, 60), "#"),
        ("Teal", (30, 170, 170), "@"),
        ("Cyan", (40, 200, 230), "~"),
        ("Blue", (40, 80, 210), "="),
        ("Indigo", (70, 40, 170), "^"),
        ("Violet", (140, 40, 190), "&"),
        ("Magenta", (200, 40, 140), "%"),
        ("Pink", (240, 100, 130), "!"),
    ],
}

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CrossStitchResult:
    """Container for cross-stitch pattern output."""
    grid_w: int
    grid_h: int
    grid: List[List[int]]  # cell index per stitch
    seeds: List[Tuple[float, float]]
    palette_name: str
    colours: List[Tuple[str, Tuple[int, int, int], str]]  # used subset
    stitch_counts: Dict[int, int]  # cell_idx -> count
    backstitch: bool
    cell_size: int  # pixels per stitch in chart


# ---------------------------------------------------------------------------
# Voronoi cell assignment (stitch-grid resolution)
# ---------------------------------------------------------------------------

def _assign_cells(grid_w: int, grid_h: int,
                  seeds: List[Tuple[float, float]]) -> List[List[int]]:
    grid = [[0] * grid_w for _ in range(grid_h)]
    for y in range(grid_h):
        for x in range(grid_w):
            best_d = float("inf")
            best_i = 0
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            grid[y][x] = best_i
    return grid


def _is_edge(grid: List[List[int]], x: int, y: int,
             grid_w: int, grid_h: int) -> bool:
    c = grid[y][x]
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        ny, nx = y + dy, x + dx
        if 0 <= ny < grid_h and 0 <= nx < grid_w and grid[ny][nx] != c:
            return True
    return False


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate(grid_w: int = 60, grid_h: int = 80, *,
             num_seeds: int = 20, palette: str = "pastel",
             backstitch: bool = False, cell_size: int = 10,
             seed: Optional[int] = None) -> CrossStitchResult:
    """Generate a Voronoi cross-stitch pattern.

    Parameters
    ----------
    grid_w, grid_h : int
        Stitch count (columns × rows).
    num_seeds : int
        Number of Voronoi cells / thread colours.
    palette : str
        Colour palette name.
    backstitch : bool
        Whether to add backstitch outlines.
    cell_size : int
        Pixels per stitch in the chart PNG.
    seed : int, optional
        RNG seed for reproducibility.
    """
    if seed is not None:
        random.seed(seed)

    pal = PALETTES.get(palette, PALETTES["pastel"])
    # Clamp seeds to palette size
    num_seeds = min(num_seeds, len(pal))

    seeds = [(random.uniform(0, grid_w), random.uniform(0, grid_h))
             for _ in range(num_seeds)]
    colours = pal[:num_seeds]

    grid = _assign_cells(grid_w, grid_h, seeds)

    # Stitch counts
    counts: Dict[int, int] = {}
    for row in grid:
        for c in row:
            counts[c] = counts.get(c, 0) + 1

    return CrossStitchResult(
        grid_w=grid_w, grid_h=grid_h, grid=grid,
        seeds=seeds, palette_name=palette, colours=colours,
        stitch_counts=counts, backstitch=backstitch, cell_size=cell_size,
    )


# ---------------------------------------------------------------------------
# Text pattern output
# ---------------------------------------------------------------------------

def save_pattern(result: CrossStitchResult, path: str) -> None:
    """Save a text cross-stitch pattern with grid + legend."""
    lines: List[str] = []
    lines.append(f"Cross-Stitch Pattern — {result.grid_w}w × {result.grid_h}h stitches")
    lines.append(f"Palette: {result.palette_name}  |  Cells: {len(result.colours)}")
    lines.append("")

    # Column rulers every 10
    ruler = " " * 4
    for x in range(result.grid_w):
        ruler += str(x % 10) if x % 10 == 0 else " "
    lines.append(ruler)

    # Grid
    for y in range(result.grid_h):
        row_label = f"{y:3d} "
        row_chars = ""
        for x in range(result.grid_w):
            c = result.grid[y][x]
            sym = result.colours[c][2]
            if result.backstitch and _is_edge(result.grid, x, y,
                                               result.grid_w, result.grid_h):
                sym = "|"
            row_chars += sym
        lines.append(row_label + row_chars)

    # Legend
    lines.append("")
    lines.append("COLOUR LEGEND")
    lines.append("-" * 50)
    lines.append(f"{'Sym':<5} {'Colour':<16} {'Code':<8} {'Stitches':>8}")
    lines.append("-" * 50)
    for i, (name, rgb, sym) in enumerate(result.colours):
        cnt = result.stitch_counts.get(i, 0)
        code = f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
        lines.append(f"  {sym:<3}  {name:<16} {code:<8} {cnt:>8}")
    total = sum(result.stitch_counts.values())
    lines.append("-" * 50)
    lines.append(f"{'':5} {'TOTAL':<16} {'':8} {total:>8}")
    lines.append("")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# PNG chart output (no PIL — manual PNG encoder)
# ---------------------------------------------------------------------------

def _make_png(width: int, height: int,
              pixels: List[List[Tuple[int, int, int]]]) -> bytes:
    """Encode an RGB pixel grid as a PNG file (deflate)."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b""
    for row in pixels:
        raw += b"\x00"  # filter none
        for r, g, b in row:
            raw += bytes((r, g, b))
    idat = _chunk(b"IDAT", zlib.compress(raw, 9))
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


def save_chart(result: CrossStitchResult, path: str) -> None:
    """Render a PNG chart with coloured cells and optional grid lines."""
    cs = result.cell_size
    gw, gh = result.grid_w, result.grid_h
    img_w = gw * cs + 1
    img_h = gh * cs + 1

    # Background
    pixels: List[List[Tuple[int, int, int]]] = [
        [(255, 255, 255)] * img_w for _ in range(img_h)
    ]

    grid_line_color = (180, 180, 180)
    edge_color = (40, 40, 40)

    for gy in range(gh):
        for gx in range(gw):
            ci = result.grid[gy][gx]
            _, rgb, sym = result.colours[ci]
            # Fill cell rectangle
            for py in range(gy * cs + 1, (gy + 1) * cs):
                for px in range(gx * cs + 1, (gx + 1) * cs):
                    pixels[py][px] = rgb

            # Draw symbol in center (simple 3x3 marker)
            cx = gx * cs + cs // 2
            cy = gy * cs + cs // 2
            # Contrast colour
            lum = rgb[0] * 0.299 + rgb[1] * 0.587 + rgb[2] * 0.114
            sc = (30, 30, 30) if lum > 128 else (230, 230, 230)
            if cs >= 6:
                for d in range(-1, 2):
                    if 0 <= cx + d < img_w:
                        pixels[cy][cx + d] = sc
                    if 0 <= cy + d < img_h:
                        pixels[cy + d][cx] = sc

    # Grid lines
    for gx in range(gw + 1):
        px = gx * cs
        if px < img_w:
            for py in range(img_h):
                pixels[py][px] = grid_line_color
    for gy in range(gh + 1):
        py = gy * cs
        if py < img_h:
            for px in range(img_w):
                pixels[py][px] = grid_line_color

    # Major grid lines every 10
    major = (120, 120, 120)
    for gx in range(0, gw + 1, 10):
        px = gx * cs
        if px < img_w:
            for py in range(img_h):
                pixels[py][px] = major
    for gy in range(0, gh + 1, 10):
        py = gy * cs
        if py < img_h:
            for px in range(img_w):
                pixels[py][px] = major

    # Backstitch outlines
    if result.backstitch:
        for gy in range(gh):
            for gx in range(gw):
                if _is_edge(result.grid, gx, gy, gw, gh):
                    # Draw thick border on the cell edges
                    for py in range(gy * cs, (gy + 1) * cs + 1):
                        for px in range(gx * cs, (gx + 1) * cs + 1):
                            if (py == gy * cs or py == (gy + 1) * cs or
                                    px == gx * cs or px == (gx + 1) * cs):
                                if 0 <= py < img_h and 0 <= px < img_w:
                                    pixels[py][px] = edge_color

    data = _make_png(img_w, img_h, pixels)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Voronoi cross-stitch pattern (text + optional PNG chart)."
    )
    parser.add_argument("width", type=int, help="Stitch columns")
    parser.add_argument("height", type=int, help="Stitch rows")
    parser.add_argument("output", help="Output text pattern file (.txt)")
    parser.add_argument("--seeds", type=int, default=12, help="Number of Voronoi cells (default 12)")
    parser.add_argument("--palette", choices=list(PALETTES.keys()), default="pastel",
                        help="Colour palette (default pastel)")
    parser.add_argument("--chart", default=None, help="Output PNG chart file")
    parser.add_argument("--backstitch", action="store_true", help="Add backstitch outlines")
    parser.add_argument("--cell-size", type=int, default=10,
                        help="Pixels per stitch in chart (default 10)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    args = parser.parse_args()

    result = generate(args.width, args.height,
                      num_seeds=args.seeds, palette=args.palette,
                      backstitch=args.backstitch, cell_size=args.cell_size,
                      seed=args.seed)

    save_pattern(result, args.output)
    print(f"Pattern saved -> {args.output}")
    print(f"  {result.grid_w} x {result.grid_h} stitches, "
          f"{len(result.colours)} colours ({result.palette_name})")

    if args.chart:
        save_chart(result, args.chart)
        print(f"Chart saved  -> {args.chart}")

    # Print legend summary
    print("\nColour Legend:")
    for i, (name, rgb, sym) in enumerate(result.colours):
        cnt = result.stitch_counts.get(i, 0)
        print(f"  [{sym}] {name:<16} #{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}  {cnt} stitches")


if __name__ == "__main__":
    main()
