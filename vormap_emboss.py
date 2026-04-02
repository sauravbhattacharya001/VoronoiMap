"""Voronoi emboss / relief renderer.

Renders a Voronoi diagram with a 3-D embossed (relief / bas-relief) look.
Each cell is shaded as if it were a raised plateau lit by a directional light
source, with subtle shadows on one side and highlights on the other.

Features
--------
- Configurable **light direction** (angle in degrees, default 315 = upper-left)
- Adjustable **emboss depth** controlling highlight/shadow intensity
- Multiple **material presets**: stone, metal, clay, paper, wood
- Optional **cell height variation** — cells get random heights for depth
- Optional **edge chisel** effect for carved stone look
- Outputs a PNG image using only the standard library (no PIL/Pillow)

CLI usage
---------
::

    python vormap_emboss.py 800 600 emboss.png
    python vormap_emboss.py 800 600 emboss.png --seeds 50 --material metal
    python vormap_emboss.py 1024 768 emboss.png --light-angle 225 --depth 0.8
    python vormap_emboss.py 800 600 emboss.png --height-vary --chisel --material stone

Programmatic usage
------------------
::

    import vormap_emboss

    result = vormap_emboss.generate(800, 600, num_seeds=40, material="stone")
    vormap_emboss.save_png(result, "emboss.png")

"""

import argparse
import math
import os
import random
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import vormap

# ---------------------------------------------------------------------------
# Material palettes — base colour + highlight/shadow tints
# ---------------------------------------------------------------------------

MATERIALS = {
    "stone": {
        "base": (160, 155, 145),
        "highlight": (210, 205, 195),
        "shadow": (90, 85, 78),
        "ambient": 0.35,
    },
    "metal": {
        "base": (170, 175, 185),
        "highlight": (230, 235, 245),
        "shadow": (70, 72, 80),
        "ambient": 0.25,
    },
    "clay": {
        "base": (190, 140, 110),
        "highlight": (235, 195, 165),
        "shadow": (110, 75, 55),
        "ambient": 0.40,
    },
    "paper": {
        "base": (235, 230, 220),
        "highlight": (255, 252, 245),
        "shadow": (160, 155, 145),
        "ambient": 0.45,
    },
    "wood": {
        "base": (160, 120, 80),
        "highlight": (210, 175, 130),
        "shadow": (85, 60, 35),
        "ambient": 0.35,
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EmbossResult:
    """Container for emboss render output."""
    width: int
    height: int
    pixels: List[List[Tuple[int, int, int]]]
    seeds: List[Tuple[float, float]]
    cell_heights: List[float]
    material: str
    light_angle: float
    depth: float


# ---------------------------------------------------------------------------
# Voronoi cell assignment
# ---------------------------------------------------------------------------

def _assign_cells(width: int, height: int, seeds: List[Tuple[float, float]]) -> List[List[int]]:
    """Assign each pixel to its nearest seed (brute-force for simplicity)."""
    grid = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            best_dist = float("inf")
            best_idx = 0
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_dist:
                    best_dist = d
                    best_idx = i
            grid[y][x] = best_idx
    return grid


def _distance_to_edge(x: int, y: int, cell_id: int, grid: List[List[int]],
                      width: int, height: int, radius: int = 6) -> float:
    """Approximate distance from (x, y) to the nearest cell boundary."""
    min_dist = float("inf")
    for dy in range(-radius, radius + 1):
        ny = y + dy
        if ny < 0 or ny >= height:
            continue
        for dx in range(-radius, radius + 1):
            nx = x + dx
            if nx < 0 or nx >= width:
                continue
            if grid[ny][nx] != cell_id:
                d = math.sqrt(dx * dx + dy * dy)
                if d < min_dist:
                    min_dist = d
    return min_dist


# ---------------------------------------------------------------------------
# Shading
# ---------------------------------------------------------------------------

def _lerp_color(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _clamp(v: int, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate(
    width: int,
    height: int,
    *,
    num_seeds: int = 40,
    material: str = "stone",
    light_angle: float = 315.0,
    depth: float = 0.6,
    height_vary: bool = False,
    chisel: bool = False,
    seed: Optional[int] = None,
) -> EmbossResult:
    """Generate a Voronoi emboss/relief image.

    Parameters
    ----------
    width, height : int
        Image dimensions in pixels.
    num_seeds : int
        Number of Voronoi cells.
    material : str
        Material preset name (stone, metal, clay, paper, wood).
    light_angle : float
        Light direction in degrees (0 = right, 90 = down, 315 = upper-left).
    depth : float
        Emboss intensity from 0.0 (flat) to 1.0 (deep relief).
    height_vary : bool
        If True, cells get random height variation for extra depth.
    chisel : bool
        If True, add a chiselled edge effect at cell boundaries.
    seed : int or None
        Random seed for reproducibility.
    """
    if seed is not None:
        random.seed(seed)

    mat = MATERIALS.get(material, MATERIALS["stone"])

    # Generate seeds
    seeds = [(random.uniform(0, width), random.uniform(0, height)) for _ in range(num_seeds)]

    # Cell heights (0.0 to 1.0)
    if height_vary:
        cell_heights = [random.uniform(0.3, 1.0) for _ in range(num_seeds)]
    else:
        cell_heights = [1.0] * num_seeds

    # Assign cells
    grid = _assign_cells(width, height, seeds)

    # Light direction vector
    lx = math.cos(math.radians(light_angle))
    ly = -math.sin(math.radians(light_angle))  # flip Y for screen coords

    # Render pixels
    pixels = [[(0, 0, 0)] * width for _ in range(height)]
    edge_radius = max(3, int(6 * depth))

    for y in range(height):
        for x in range(width):
            cell_id = grid[y][x]
            sx, sy = seeds[cell_id]
            ch = cell_heights[cell_id]

            # Distance to cell edge
            edge_dist = _distance_to_edge(x, y, cell_id, grid, width, height, edge_radius)

            # Normalise edge distance to 0..1 (0 = at edge, 1 = deep inside)
            plateau = min(1.0, edge_dist / (edge_radius * 1.2))

            # Direction from pixel to seed (for bevelling)
            dx = x - sx
            dy = y - sy
            cell_dist = math.sqrt(dx * dx + dy * dy) if (dx != 0 or dy != 0) else 1.0
            nx_norm = dx / cell_dist
            ny_norm = dy / cell_dist

            # Dot product with light for directional shading
            dot = nx_norm * lx + ny_norm * ly  # -1 to 1

            # Edge bevel contribution
            bevel = (1.0 - plateau) * depth
            # Light factor: combine directional light with bevel
            light_factor = mat["ambient"] + (1.0 - mat["ambient"]) * (dot * 0.5 + 0.5)
            light_factor *= (1.0 - bevel * 0.5) + bevel * (dot * 0.5 + 0.5)
            light_factor *= ch  # height variation

            # Choose between highlight and shadow
            if light_factor > 0.5:
                t = (light_factor - 0.5) * 2.0
                color = _lerp_color(mat["base"], mat["highlight"], t * depth)
            else:
                t = (0.5 - light_factor) * 2.0
                color = _lerp_color(mat["base"], mat["shadow"], t * depth)

            # Chisel effect: darken pixels very close to edges
            if chisel and edge_dist < 2.0:
                chisel_t = 1.0 - edge_dist / 2.0
                color = _lerp_color(color, mat["shadow"], chisel_t * 0.7)

            pixels[y][x] = (
                _clamp(color[0]),
                _clamp(color[1]),
                _clamp(color[2]),
            )

    return EmbossResult(
        width=width,
        height=height,
        pixels=pixels,
        seeds=seeds,
        cell_heights=cell_heights,
        material=material,
        light_angle=light_angle,
        depth=depth,
    )


# ---------------------------------------------------------------------------
# PNG output (no external dependencies)
# ---------------------------------------------------------------------------

def _make_png(width: int, height: int, pixels: List[List[Tuple[int, int, int]]]) -> bytes:
    """Encode RGB pixel data as a PNG file (uncompressed scanlines)."""

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter: none
        for r, g, b in row:
            raw.extend((r, g, b))

    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def save_png(result: EmbossResult, filepath: str) -> None:
    """Save an EmbossResult to a PNG file."""
    vormap.validate_output_path(filepath)
    resolved = os.path.realpath(filepath)
    data = _make_png(result.width, result.height, result.pixels)
    with open(resolved, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a Voronoi emboss / relief image.",
    )
    parser.add_argument("width", type=int, help="Image width in pixels")
    parser.add_argument("height", type=int, help="Image height in pixels")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--seeds", type=int, default=40, help="Number of Voronoi cells (default: 40)")
    parser.add_argument("--material", choices=list(MATERIALS.keys()), default="stone",
                        help="Material preset (default: stone)")
    parser.add_argument("--light-angle", type=float, default=315.0,
                        help="Light direction in degrees (default: 315 = upper-left)")
    parser.add_argument("--depth", type=float, default=0.6,
                        help="Emboss depth 0.0-1.0 (default: 0.6)")
    parser.add_argument("--height-vary", action="store_true",
                        help="Add random height variation to cells")
    parser.add_argument("--chisel", action="store_true",
                        help="Add chiselled edge effect")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")

    args = parser.parse_args()

    result = generate(
        args.width,
        args.height,
        num_seeds=args.seeds,
        material=args.material,
        light_angle=args.light_angle,
        depth=args.depth,
        height_vary=args.height_vary,
        chisel=args.chisel,
        seed=args.seed,
    )
    save_png(result, args.output)
    print(f"Emboss render saved to {args.output} ({args.width}x{args.height}, "
          f"material={args.material}, depth={args.depth})")


if __name__ == "__main__":
    main()
