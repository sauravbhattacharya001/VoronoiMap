"""Voronoi stained-glass renderer.

Renders a Voronoi diagram with a stained-glass aesthetic: thick dark lead
lines between cells, translucent coloured fills with subtle texture, and an
optional directional light-source that simulates light shining through glass.

Features
--------
- Configurable **lead width** (the dark border between cells)
- Multiple glass palette presets: **cathedral**, **tiffany**, **modern**,
  **warm**, **cool**, **sunset**, **forest**, or supply a custom hex list
- Optional **light source** with configurable direction that brightens/dims
  cells based on their position — simulates sunlight through a window
- Optional **texture grain** that adds a subtle frosted-glass effect
- Optional **bevel effect** on cell edges for 3-D depth
- Outputs a PNG image using only the standard library

CLI usage
---------
::

    python vormap_stainedglass.py 800 600 glass.png
    python vormap_stainedglass.py 800 600 glass.png --seeds 40 --palette tiffany
    python vormap_stainedglass.py 800 600 glass.png --lead-width 4 --light-angle 45
    python vormap_stainedglass.py 1024 768 glass.png --grain --bevel --palette cathedral

Programmatic usage
------------------
::

    import vormap_stainedglass

    result = vormap_stainedglass.generate(800, 600, num_seeds=30, palette="cathedral")
    vormap_stainedglass.save_png(result, "glass.png")

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
# Glass palettes — rich, translucent-inspired colours
# ---------------------------------------------------------------------------

PALETTES = {
    "cathedral": [
        (180, 30, 30), (30, 30, 180), (180, 150, 30), (30, 150, 60),
        (140, 30, 140), (200, 100, 30), (30, 120, 180), (180, 60, 90),
    ],
    "tiffany": [
        (0, 150, 136), (76, 175, 80), (139, 195, 74), (205, 220, 57),
        (255, 235, 59), (255, 193, 7), (255, 152, 0), (255, 87, 34),
    ],
    "modern": [
        (233, 30, 99), (156, 39, 176), (103, 58, 183), (63, 81, 181),
        (33, 150, 243), (0, 188, 212), (0, 150, 136), (76, 175, 80),
    ],
    "warm": [
        (211, 47, 47), (245, 124, 0), (251, 192, 45), (230, 74, 25),
        (183, 28, 28), (255, 160, 0), (255, 111, 0), (198, 40, 40),
    ],
    "cool": [
        (21, 101, 192), (2, 136, 209), (0, 151, 167), (0, 121, 107),
        (25, 118, 210), (48, 63, 159), (69, 90, 100), (56, 142, 60),
    ],
    "sunset": [
        (255, 87, 34), (255, 138, 101), (255, 183, 77), (255, 213, 79),
        (255, 241, 118), (255, 110, 64), (244, 81, 30), (230, 81, 0),
    ],
    "forest": [
        (27, 94, 32), (46, 125, 50), (56, 142, 60), (67, 160, 71),
        (76, 175, 80), (102, 187, 106), (129, 199, 132), (165, 214, 167),
    ],
}

LEAD_COLOR = (20, 20, 20)

# ---------------------------------------------------------------------------
# Minimal PNG writer (same pattern as other vormap modules)
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
        off = y * width * 3
        raw += pixels[off : off + width * 3]
    compressed = zlib.compress(bytes(raw), 9)

    with open(path, "wb") as fp:
        fp.write(b"\x89PNG\r\n\x1a\n")
        fp.write(_chunk(b"IHDR", header))
        fp.write(_chunk(b"IDAT", compressed))
        fp.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class StainedGlassResult:
    """Result of stained-glass generation."""

    width: int
    height: int
    seeds: List[Tuple[float, float]]
    cell_colors: List[Tuple[int, int, int]]
    cell_areas: Dict[int, int] = field(default_factory=dict)
    pixels: bytes = b""
    lead_width: int = 3
    light_angle: Optional[float] = None


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------


def _resolve_palette(name_or_hex: str) -> List[Tuple[int, int, int]]:
    """Return palette from preset name or comma-separated hex list."""
    if name_or_hex in PALETTES:
        return list(PALETTES[name_or_hex])
    colors = []
    for h in name_or_hex.split(","):
        h = h.strip().lstrip("#")
        if len(h) == 6:
            colors.append((int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    if not colors:
        return list(PALETTES["cathedral"])
    return colors


def _distance_sq(ax: float, ay: float, bx: float, by: float) -> float:
    return (ax - bx) ** 2 + (ay - by) ** 2


def generate(
    width: int = 800,
    height: int = 600,
    num_seeds: int = 30,
    palette: str = "cathedral",
    lead_width: int = 3,
    light_angle: Optional[float] = None,
    light_strength: float = 0.3,
    grain: bool = False,
    bevel: bool = False,
    seed: Optional[int] = None,
) -> StainedGlassResult:
    """Generate a stained-glass Voronoi image.

    Parameters
    ----------
    width, height : int
        Output image dimensions in pixels.
    num_seeds : int
        Number of Voronoi cells (glass panes).
    palette : str
        Palette preset name or comma-separated hex colours.
    lead_width : int
        Width of the dark lead lines between cells (pixels).
    light_angle : float | None
        Angle in degrees (0 = right, 90 = top) of simulated light source.
        ``None`` disables directional lighting.
    light_strength : float
        How much the light affects brightness (0.0–1.0).
    grain : bool
        Add subtle frosted-glass texture grain.
    bevel : bool
        Add bevel highlight/shadow on cell edges.
    seed : int | None
        Random seed for reproducibility.

    Returns
    -------
    StainedGlassResult
    """
    rng = random.Random(seed)
    colors = _resolve_palette(palette)

    # Generate seed points
    seeds = [(rng.uniform(0, width), rng.uniform(0, height)) for _ in range(num_seeds)]
    cell_colors = [colors[i % len(colors)] for i in range(num_seeds)]

    # Compute light direction vector (normalised)
    light_dx, light_dy = 0.0, 0.0
    if light_angle is not None:
        rad = math.radians(light_angle)
        light_dx = math.cos(rad)
        light_dy = -math.sin(rad)  # screen Y is inverted

    # Rasterise: for each pixel, find closest and second-closest seed
    buf = bytearray(width * height * 3)
    cell_areas: Dict[int, int] = {}
    cx, cy = width / 2.0, height / 2.0

    for y in range(height):
        for x in range(width):
            # Find two nearest seeds
            best_i, best_d = 0, float("inf")
            second_d = float("inf")
            for i, (sx, sy) in enumerate(seeds):
                d = _distance_sq(x, y, sx, sy)
                if d < best_d:
                    second_d = best_d
                    best_i, best_d = i, d
                elif d < second_d:
                    second_d = d

            best_dist = math.sqrt(best_d)
            second_dist = math.sqrt(second_d)
            edge_dist = (second_dist - best_dist) / 2.0

            cell_areas[best_i] = cell_areas.get(best_i, 0) + 1

            # Base colour
            r, g, b = cell_colors[best_i]

            # Lead line check
            if edge_dist < lead_width:
                # Inside the lead
                lead_factor = edge_dist / lead_width  # 0 at edge, 1 at boundary
                if bevel:
                    # Bevel: slight highlight on one side, shadow on other
                    bevel_val = int(15 * (1.0 - lead_factor))
                    r_l, g_l, b_l = (
                        min(255, LEAD_COLOR[0] + bevel_val),
                        min(255, LEAD_COLOR[1] + bevel_val),
                        min(255, LEAD_COLOR[2] + bevel_val),
                    )
                else:
                    r_l, g_l, b_l = LEAD_COLOR
                # Blend from lead to glass colour
                t = max(0.0, min(1.0, lead_factor))
                r = int(r_l * (1 - t) + r * t)
                g = int(g_l * (1 - t) + g * t)
                b = int(b_l * (1 - t) + b * t)
            else:
                # Inside a glass pane
                # Directional lighting
                if light_angle is not None:
                    # How aligned is this pixel's position with the light?
                    nx = (x - cx) / (width / 2.0)
                    ny = (y - cy) / (height / 2.0)
                    dot = nx * light_dx + ny * light_dy
                    brightness = 1.0 + dot * light_strength
                    brightness = max(0.5, min(1.4, brightness))
                    r = min(255, int(r * brightness))
                    g = min(255, int(g * brightness))
                    b = min(255, int(b * brightness))

                # Grain texture
                if grain:
                    noise = rng.randint(-8, 8)
                    r = max(0, min(255, r + noise))
                    g = max(0, min(255, g + noise))
                    b = max(0, min(255, b + noise))

            off = (y * width + x) * 3
            buf[off] = r
            buf[off + 1] = g
            buf[off + 2] = b

    return StainedGlassResult(
        width=width,
        height=height,
        seeds=seeds,
        cell_colors=cell_colors,
        cell_areas=cell_areas,
        pixels=bytes(buf),
        lead_width=lead_width,
        light_angle=light_angle,
    )


def save_png(result: StainedGlassResult, path: str) -> str:
    """Save a StainedGlassResult to a PNG file."""
    out = os.path.abspath(path)
    _write_png(out, result.pixels, result.width, result.height)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi stained-glass renderer",
    )
    parser.add_argument("width", type=int, help="Image width in pixels")
    parser.add_argument("height", type=int, help="Image height in pixels")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--seeds", type=int, default=30, help="Number of cells")
    parser.add_argument(
        "--palette",
        default="cathedral",
        help="Palette: cathedral, tiffany, modern, warm, cool, sunset, forest, or hex list",
    )
    parser.add_argument("--lead-width", type=int, default=3, help="Lead line width")
    parser.add_argument(
        "--light-angle", type=float, default=None, help="Light direction in degrees"
    )
    parser.add_argument(
        "--light-strength", type=float, default=0.3, help="Light intensity (0-1)"
    )
    parser.add_argument("--grain", action="store_true", help="Add frosted-glass grain")
    parser.add_argument("--bevel", action="store_true", help="Add bevel on lead lines")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")

    args = parser.parse_args()

    result = generate(
        width=args.width,
        height=args.height,
        num_seeds=args.seeds,
        palette=args.palette,
        lead_width=args.lead_width,
        light_angle=args.light_angle,
        light_strength=args.light_strength,
        grain=args.grain,
        bevel=args.bevel,
        seed=args.seed,
    )

    out = save_png(result, args.output)
    print(f"Stained glass saved to {out}")
    print(f"  {len(result.seeds)} cells, lead width {result.lead_width}px")
    if result.light_angle is not None:
        print(f"  Light angle: {result.light_angle}°")


if __name__ == "__main__":
    main()
