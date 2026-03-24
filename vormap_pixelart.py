"""Voronoi pixel-art generator.

Rasterises a Voronoi diagram onto a low-resolution grid and renders it as
retro pixel art.  Useful for game development (procedural maps, biomes),
creative coding, and artistic applications.

Features
--------
- Configurable grid resolution (e.g. 32×32, 64×64, 128×128)
- Multiple palette presets: **gameboy**, **nes**, **pico8**, **grayscale**,
  **pastel**, **neon**, **earth**, or supply a custom hex list
- Optional **grid lines** for a classic pixel-art look
- Optional **border glow** that darkens cell edges
- Outputs PNG with nearest-neighbour upscaling to a viewable size

CLI usage
---------
::

    python vormap_pixelart.py 32 32 pixel.png
    python vormap_pixelart.py 64 64 pixel.png --seeds 30 --palette pico8
    python vormap_pixelart.py 48 48 pixel.png --palette "#ff0000,#00ff00,#0000ff"
    python vormap_pixelart.py 128 128 pixel.png --grid --scale 4 --border-glow

Programmatic usage
------------------
::

    import vormap_pixelart

    result = vormap_pixelart.generate(
        grid_w=32, grid_h=32, num_seeds=12, palette="gameboy"
    )
    print(result.cell_counts)          # pixels per cell
    vormap_pixelart.save_png(result, "pixel.png", scale=8)

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
# Palettes
# ---------------------------------------------------------------------------

PALETTES = {
    "gameboy": [
        (15, 56, 15), (48, 98, 48), (139, 172, 15), (155, 188, 15),
    ],
    "nes": [
        (0, 0, 0), (252, 252, 252), (248, 56, 0), (0, 120, 248),
        (0, 168, 0), (248, 184, 0), (104, 68, 252), (0, 184, 248),
        (248, 120, 88), (88, 216, 84), (152, 120, 248), (248, 216, 120),
    ],
    "pico8": [
        (0, 0, 0), (29, 43, 83), (126, 37, 83), (0, 135, 81),
        (171, 82, 54), (95, 87, 79), (194, 195, 199), (255, 241, 232),
        (255, 0, 77), (255, 163, 0), (255, 236, 39), (0, 228, 54),
        (41, 173, 255), (131, 118, 156), (255, 119, 168), (255, 204, 170),
    ],
    "grayscale": [
        (30, 30, 30), (80, 80, 80), (130, 130, 130), (180, 180, 180),
        (210, 210, 210), (240, 240, 240),
    ],
    "pastel": [
        (255, 179, 186), (255, 223, 186), (255, 255, 186),
        (186, 255, 201), (186, 225, 255), (219, 186, 255),
    ],
    "neon": [
        (57, 255, 20), (255, 0, 255), (0, 255, 255),
        (255, 255, 0), (255, 0, 100), (0, 100, 255),
    ],
    "earth": [
        (101, 67, 33), (139, 90, 43), (160, 120, 60), (85, 107, 47),
        (107, 142, 35), (34, 139, 34), (188, 143, 143), (210, 180, 140),
    ],
}

# ---------------------------------------------------------------------------
# Minimal PNG writer
# ---------------------------------------------------------------------------

def _write_png(path: str, pixels: bytes, width: int, height: int) -> None:
    """Write an RGB pixel buffer to a PNG file."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = bytearray()
    for y in range(height):
        raw += b"\x00"
        row_start = y * width * 3
        raw += pixels[row_start: row_start + width * 3]
    compressed = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", header))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PixelArtResult:
    """Result of pixel-art generation."""
    grid_w: int
    grid_h: int
    num_seeds: int
    seeds: List[Tuple[float, float]]
    grid: List[List[int]]             # cell index for each pixel
    palette_name: str
    colors: List[Tuple[int, int, int]]
    cell_counts: Dict[int, int] = field(default_factory=dict)

# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

def _parse_palette(palette: str) -> Tuple[str, List[Tuple[int, int, int]]]:
    """Return (name, color_list) from a palette name or comma-separated hex."""
    if palette in PALETTES:
        return palette, list(PALETTES[palette])
    # Try parsing as hex list
    colors = []
    for tok in palette.split(","):
        tok = tok.strip().lstrip("#")
        if len(tok) == 6:
            colors.append((int(tok[0:2], 16), int(tok[2:4], 16), int(tok[4:6], 16)))
    if not colors:
        raise ValueError(f"Unknown palette '{palette}' and could not parse as hex list")
    return "custom", colors


def generate(
    grid_w: int = 32,
    grid_h: int = 32,
    num_seeds: int = 12,
    palette: str = "pico8",
    seed_value: Optional[int] = None,
    points: Optional[List[Tuple[float, float]]] = None,
) -> PixelArtResult:
    """Generate a Voronoi pixel-art grid.

    Parameters
    ----------
    grid_w, grid_h : int
        Resolution of the pixel grid.
    num_seeds : int
        Number of Voronoi seed points (ignored if *points* supplied).
    palette : str
        Palette name or comma-separated hex colors.
    seed_value : int, optional
        Random seed for reproducibility.
    points : list of (x, y), optional
        Custom seed points in [0, grid_w) × [0, grid_h) space.
    """
    if seed_value is not None:
        random.seed(seed_value)

    pal_name, colors = _parse_palette(palette)

    # Generate or use provided seed points
    if points is not None:
        seeds = list(points)
        num_seeds = len(seeds)
    else:
        seeds = [(random.uniform(0, grid_w), random.uniform(0, grid_h))
                 for _ in range(num_seeds)]

    # Assign each grid cell to nearest seed (Manhattan for retro feel)
    grid: List[List[int]] = []
    cell_counts: Dict[int, int] = {}
    for y in range(grid_h):
        row = []
        for x in range(grid_w):
            best_idx = 0
            best_dist = float("inf")
            px, py = x + 0.5, y + 0.5
            for i, (sx, sy) in enumerate(seeds):
                d = abs(px - sx) + abs(py - sy)  # Manhattan distance
                if d < best_dist:
                    best_dist = d
                    best_idx = i
            row.append(best_idx)
            cell_counts[best_idx] = cell_counts.get(best_idx, 0) + 1
        grid.append(row)

    return PixelArtResult(
        grid_w=grid_w,
        grid_h=grid_h,
        num_seeds=num_seeds,
        seeds=seeds,
        grid=grid,
        palette_name=pal_name,
        colors=colors,
        cell_counts=cell_counts,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _is_border(grid: List[List[int]], x: int, y: int, w: int, h: int) -> bool:
    """Check if pixel (x, y) is on a cell boundary."""
    cell = grid[y][x]
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nx, ny = x + dx, y + dy
        if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] != cell:
            return True
    return False


def render(
    result: PixelArtResult,
    scale: int = 8,
    show_grid: bool = False,
    border_glow: bool = False,
) -> Tuple[bytes, int, int]:
    """Render pixel-art result to an RGB pixel buffer.

    Returns (pixels_bytes, width, height).
    """
    gw, gh = result.grid_w, result.grid_h
    out_w, out_h = gw * scale, gh * scale
    colors = result.colors
    grid = result.grid

    buf = bytearray(out_w * out_h * 3)

    for gy in range(gh):
        for gx in range(gw):
            cell_idx = grid[gy][gx]
            r, g, b = colors[cell_idx % len(colors)]

            # Darken border pixels
            if border_glow and _is_border(grid, gx, gy, gw, gh):
                r = max(0, r - 60)
                g = max(0, g - 60)
                b = max(0, b - 60)

            # Fill the scaled block
            for sy in range(scale):
                for sx in range(scale):
                    px = gx * scale + sx
                    py = gy * scale + sy

                    # Grid lines: darken the right and bottom edge of each cell
                    if show_grid and (sx == scale - 1 or sy == scale - 1):
                        pr = max(0, r - 40)
                        pg = max(0, g - 40)
                        pb = max(0, b - 40)
                    else:
                        pr, pg, pb = r, g, b

                    offset = (py * out_w + px) * 3
                    buf[offset] = pr
                    buf[offset + 1] = pg
                    buf[offset + 2] = pb

    return bytes(buf), out_w, out_h


def save_png(
    result: PixelArtResult,
    path: str,
    scale: int = 8,
    show_grid: bool = False,
    border_glow: bool = False,
) -> str:
    """Render and save pixel art to a PNG file. Returns the output path."""
    pixels, w, h = render(result, scale=scale, show_grid=show_grid,
                          border_glow=border_glow)
    _write_png(path, pixels, w, h)
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi pixel-art generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_pixelart.py 32 32 pixel.png
  python vormap_pixelart.py 64 64 pixel.png --seeds 30 --palette pico8
  python vormap_pixelart.py 48 48 pixel.png --palette "#ff0000,#00ff00,#0000ff"
  python vormap_pixelart.py 128 128 pixel.png --grid --scale 4 --border-glow
""",
    )
    parser.add_argument("width", type=int, help="Grid width in pixels")
    parser.add_argument("height", type=int, help="Grid height in pixels")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--seeds", type=int, default=12,
                        help="Number of Voronoi seed points (default: 12)")
    parser.add_argument("--palette", default="pico8",
                        help="Palette name or comma-separated hex colors")
    parser.add_argument("--scale", type=int, default=8,
                        help="Upscale factor (default: 8)")
    parser.add_argument("--grid", action="store_true",
                        help="Show grid lines between pixels")
    parser.add_argument("--border-glow", action="store_true",
                        help="Darken cell boundary pixels")
    parser.add_argument("--seed-value", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--points", help="Seed points file (x,y per line)")

    args = parser.parse_args()

    points = None
    if args.points:
        points = []
        with open(args.points) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(",")
                    if len(parts) >= 2:
                        points.append((float(parts[0]), float(parts[1])))

    result = generate(
        grid_w=args.width,
        grid_h=args.height,
        num_seeds=args.seeds,
        palette=args.palette,
        seed_value=args.seed_value,
        points=points,
    )

    out = save_png(result, args.output, scale=args.scale,
                   show_grid=args.grid, border_glow=args.border_glow)

    print(f"Pixel art saved to {out}")
    print(f"  Grid: {result.grid_w}×{result.grid_h} "
          f"({result.grid_w * result.grid_h} pixels)")
    print(f"  Seeds: {result.num_seeds}")
    print(f"  Palette: {result.palette_name} ({len(result.colors)} colors)")
    print(f"  Output: {args.width * args.scale}×{args.height * args.scale} px")
    print(f"  Cells: {len(result.cell_counts)} regions")


if __name__ == "__main__":
    main()
