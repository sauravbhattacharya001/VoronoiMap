"""Voronoi watercolour painter.

Renders a Voronoi diagram with a watercolour-painting aesthetic: soft bleeding
edges between cells, subtle paper texture grain, and semi-transparent washes
that overlap at cell boundaries to simulate pigment diffusion.

Features
--------
- Configurable **bleed radius** — how far colour bleeds past cell borders
- Multiple palette presets: **autumn**, **ocean**, **meadow**, **sunset**,
  **monochrome**, **sakura**, **tropical**, or supply a custom hex list
- Optional **paper texture** that adds a warm, fibrous background
- Optional **splatter** effect for artistic paint-drop accents
- Optional **wet edge** darkening along cell boundaries
- Outputs a PNG image using only the standard library

CLI usage
---------
::

    python vormap_watercolor.py 800 600 watercolor.png
    python vormap_watercolor.py 800 600 watercolor.png --seeds 40 --palette sakura
    python vormap_watercolor.py 1024 768 watercolor.png --bleed 12 --paper --wet-edge
    python vormap_watercolor.py 800 600 watercolor.png --splatter --palette tropical

Programmatic usage
------------------
::

    import vormap_watercolor

    result = vormap_watercolor.generate(800, 600, num_seeds=30, palette="ocean")
    vormap_watercolor.save_png(result, "watercolor.png")

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
# Palettes — soft, muted tones suited to watercolour
# ---------------------------------------------------------------------------

PALETTES = {
    "autumn": [
        (201, 128, 70), (180, 90, 60), (220, 170, 90), (140, 80, 50),
        (190, 150, 100), (160, 110, 70), (210, 140, 80),
    ],
    "ocean": [
        (100, 160, 200), (70, 130, 180), (140, 190, 210), (60, 110, 150),
        (120, 180, 200), (80, 140, 170), (160, 200, 220),
    ],
    "meadow": [
        (120, 180, 100), (90, 160, 80), (150, 200, 120), (80, 140, 70),
        (140, 190, 110), (100, 170, 90), (170, 210, 140),
    ],
    "sunset": [
        (220, 120, 80), (200, 90, 70), (240, 160, 100), (180, 80, 60),
        (230, 140, 90), (210, 100, 75), (250, 180, 120),
    ],
    "monochrome": [
        (80, 80, 90), (110, 110, 120), (140, 140, 150), (170, 170, 175),
        (100, 100, 110), (130, 130, 140), (160, 160, 165),
    ],
    "sakura": [
        (230, 170, 180), (240, 190, 200), (220, 150, 170), (250, 200, 210),
        (210, 140, 160), (235, 180, 190), (245, 195, 205),
    ],
    "tropical": [
        (60, 190, 170), (220, 130, 60), (100, 200, 120), (230, 180, 70),
        (80, 170, 190), (200, 100, 80), (140, 210, 150),
    ],
}

PAPER_BASE = (245, 240, 230)  # warm off-white paper

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class WatercolorResult:
    """Holds the generated watercolour image and metadata."""
    width: int
    height: int
    pixels: list  # flat list of (r, g, b) per pixel (row-major)
    seeds: List[Tuple[int, int]] = field(default_factory=list)
    cell_areas: Dict[int, int] = field(default_factory=dict)
    palette_name: str = ""


# ---------------------------------------------------------------------------
# PNG writer (standard-library only)
# ---------------------------------------------------------------------------

def _write_png(path: str, width: int, height: int, pixels: list) -> None:
    """Write an RGB PNG file."""

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter none
        row_start = y * width
        for x in range(width):
            r, g, b = pixels[row_start + x]
            raw.extend((r, g, b))

    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    iend = _chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(header + ihdr + idat + iend)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_palette(name_or_hex: str) -> List[Tuple[int, int, int]]:
    """Return a colour list from a preset name or comma-separated hex."""
    if name_or_hex in PALETTES:
        return list(PALETTES[name_or_hex])
    colours = []
    for h in name_or_hex.split(","):
        h = h.strip().lstrip("#")
        if len(h) == 6:
            colours.append((int(h[:2], 16), int(h[2:4], 16), int(h[4:], 16)))
    if not colours:
        raise ValueError(f"Unknown palette '{name_or_hex}' and could not parse as hex list")
    return colours


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp(v: int, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, v))


def _blend(base: Tuple[int, int, int], overlay: Tuple[int, int, int], alpha: float) -> Tuple[int, int, int]:
    """Alpha-blend overlay onto base."""
    return tuple(_clamp(int(_lerp(base[i], overlay[i], alpha))) for i in range(3))


def _perlin_noise_simple(x: float, y: float, seed: int = 0) -> float:
    """Very simple hash-based pseudo noise in [0, 1]."""
    n = int(x * 374761 + y * 668265 + seed * 137) & 0x7FFFFFFF
    n = (n >> 13) ^ n
    n = (n * (n * n * 60493 + 19990303) + 1376312589) & 0x7FFFFFFF
    return n / 2147483647.0


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

def generate(
    width: int = 800,
    height: int = 600,
    num_seeds: int = 30,
    palette: str = "ocean",
    bleed: int = 8,
    paper: bool = True,
    splatter: bool = False,
    wet_edge: bool = False,
    seed: Optional[int] = None,
) -> WatercolorResult:
    """Generate a Voronoi watercolour painting.

    Parameters
    ----------
    width, height : int
        Canvas dimensions in pixels.
    num_seeds : int
        Number of Voronoi cells / colour regions.
    palette : str
        Palette preset name or comma-separated hex colours.
    bleed : int
        Pixel radius of colour bleeding past cell borders.
    paper : bool
        If True, add a warm paper-texture background.
    splatter : bool
        If True, add random paint-splatter dots.
    wet_edge : bool
        If True, darken edges where two cells meet (pigment pooling).
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    WatercolorResult
    """
    rng = random.Random(seed)
    colours = _parse_palette(palette)

    # Generate seed points
    seeds = [(rng.randint(0, width - 1), rng.randint(0, height - 1)) for _ in range(num_seeds)]
    seed_colours = [colours[i % len(colours)] for i in range(num_seeds)]

    # Assign each pixel to nearest seed (brute-force Voronoi)
    cell_map = [0] * (width * height)
    dist_map = [0.0] * (width * height)
    dist2_map = [0.0] * (width * height)  # second-nearest distance

    for y in range(height):
        for x in range(width):
            min_d = float("inf")
            min2_d = float("inf")
            best = 0
            for i, (sx, sy) in enumerate(seeds):
                d = math.hypot(x - sx, y - sy)
                if d < min_d:
                    min2_d = min_d
                    min_d = d
                    best = i
                elif d < min2_d:
                    min2_d = d
            idx = y * width + x
            cell_map[idx] = best
            dist_map[idx] = min_d
            dist2_map[idx] = min2_d

    # Count cell areas
    cell_areas: Dict[int, int] = {}
    for c in cell_map:
        cell_areas[c] = cell_areas.get(c, 0) + 1

    # Build pixel buffer starting with paper or white
    if paper:
        pixels = []
        for y in range(height):
            for x in range(width):
                noise = _perlin_noise_simple(x * 0.05, y * 0.05, 42)
                grain = int((noise - 0.5) * 16)
                pixels.append(tuple(_clamp(c + grain) for c in PAPER_BASE))
    else:
        pixels = [PAPER_BASE] * (width * height)

    # Paint cells with watercolour wash — semi-transparent, with bleed
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            cell = cell_map[idx]
            d_near = dist_map[idx]
            d_far = dist2_map[idx]
            border_dist = d_far - d_near  # how close to border

            colour = seed_colours[cell]

            # Base wash alpha — slightly varied for natural look
            noise_val = _perlin_noise_simple(x * 0.03, y * 0.03, cell * 7)
            base_alpha = 0.45 + noise_val * 0.25

            # Bleed effect: reduce alpha near borders
            if bleed > 0 and border_dist < bleed * 2:
                bleed_factor = border_dist / (bleed * 2)
                base_alpha *= (0.3 + 0.7 * bleed_factor)

                # Blend with neighbour colour for bleeding
                if border_dist < bleed:
                    # Find neighbour cell — approximate: use second-closest seed
                    for i, (sx, sy) in enumerate(seeds):
                        if i == cell:
                            continue
                        d = math.hypot(x - sx, y - sy)
                        if abs(d - d_far) < 1.0:
                            neighbour_colour = seed_colours[i]
                            mix = 1.0 - (border_dist / bleed)
                            colour = tuple(
                                _clamp(int(_lerp(colour[c], neighbour_colour[c], mix * 0.4)))
                                for c in range(3)
                            )
                            break

            pixels[idx] = _blend(pixels[idx], colour, base_alpha)

    # Wet edge — darken borders where pigment pools
    if wet_edge:
        for y in range(height):
            for x in range(width):
                idx = y * width + x
                border_dist = dist2_map[idx] - dist_map[idx]
                if border_dist < 4.0:
                    darken = (1.0 - border_dist / 4.0) * 0.35
                    r, g, b = pixels[idx]
                    pixels[idx] = (
                        _clamp(int(r * (1 - darken))),
                        _clamp(int(g * (1 - darken))),
                        _clamp(int(b * (1 - darken))),
                    )

    # Splatter effect
    if splatter:
        num_splatters = width * height // 800
        for _ in range(num_splatters):
            sx = rng.randint(0, width - 1)
            sy = rng.randint(0, height - 1)
            cell = cell_map[sy * width + sx]
            colour = seed_colours[cell]
            radius = rng.randint(1, 3)
            alpha = rng.uniform(0.2, 0.5)
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    nx, ny = sx + dx, sy + dy
                    if 0 <= nx < width and 0 <= ny < height and dx * dx + dy * dy <= radius * radius:
                        pidx = ny * width + nx
                        pixels[pidx] = _blend(pixels[pidx], colour, alpha)

    return WatercolorResult(
        width=width, height=height, pixels=pixels,
        seeds=seeds, cell_areas=cell_areas,
        palette_name=palette if palette in PALETTES else "custom",
    )


# ---------------------------------------------------------------------------
# Save helper
# ---------------------------------------------------------------------------

def save_png(result: WatercolorResult, path: str) -> str:
    """Save a WatercolorResult to a PNG file."""
    out = os.path.abspath(path)
    _write_png(out, result.width, result.height, result.pixels)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Voronoi watercolour painter")
    parser.add_argument("width", type=int, help="Canvas width in pixels")
    parser.add_argument("height", type=int, help="Canvas height in pixels")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--seeds", type=int, default=30, help="Number of Voronoi cells (default 30)")
    parser.add_argument("--palette", default="ocean",
                        help="Palette name or comma-separated hex colours")
    parser.add_argument("--bleed", type=int, default=8, help="Bleed radius in pixels (default 8)")
    parser.add_argument("--paper", action="store_true", default=True,
                        help="Add paper texture (default on)")
    parser.add_argument("--no-paper", action="store_false", dest="paper",
                        help="Disable paper texture")
    parser.add_argument("--splatter", action="store_true", help="Add paint splatter")
    parser.add_argument("--wet-edge", action="store_true", help="Darken cell borders (pigment pooling)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")

    args = parser.parse_args()
    result = generate(
        width=args.width, height=args.height, num_seeds=args.seeds,
        palette=args.palette, bleed=args.bleed, paper=args.paper,
        splatter=args.splatter, wet_edge=args.wet_edge, seed=args.seed,
    )
    out = save_png(result, args.output)
    print(f"Watercolour painting saved → {out}")
    print(f"  {len(result.seeds)} cells, palette={result.palette_name}")
    print(f"  canvas={result.width}×{result.height}")


if __name__ == "__main__":
    main()
