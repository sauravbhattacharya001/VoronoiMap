"""Voronoi gradient-fill renderer.

Fills each Voronoi cell with a smooth radial gradient emanating from the
seed point outward to the cell boundary, producing soft, painterly visuals.

Features
--------
- Radial gradient from seed centre → cell edge
- Configurable inner/outer colour per cell (or auto-palette)
- Multiple blend modes: **linear**, **ease**, **quadratic**
- Optional cell-edge outlines
- Pure-Python PNG output (no Pillow dependency)
- CLI + programmatic API

CLI usage
---------
::

    python vormap_gradient.py 400 400 gradient.png
    python vormap_gradient.py 800 600 gradient.png --seeds 50 --blend ease
    python vormap_gradient.py 600 600 gradient.png --palette sunset --outline

Programmatic usage
------------------
::

    import vormap_gradient

    result = vormap_gradient.generate(width=400, height=400, num_seeds=20)
    vormap_gradient.save_png(result, "gradient.png")

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

PALETTES: Dict[str, List[Tuple[int, int, int]]] = {
    "sunset": [
        (255, 94, 77), (255, 154, 0), (255, 206, 84),
        (123, 40, 125), (45, 0, 80), (255, 127, 80),
        (200, 50, 50), (220, 120, 60),
    ],
    "ocean": [
        (0, 30, 60), (0, 80, 120), (0, 150, 199),
        (72, 202, 228), (144, 224, 239), (202, 240, 248),
        (25, 100, 126), (0, 60, 100),
    ],
    "forest": [
        (20, 50, 20), (34, 100, 34), (60, 140, 60),
        (100, 180, 70), (144, 200, 80), (180, 220, 140),
        (80, 120, 50), (40, 80, 30),
    ],
    "neon": [
        (255, 0, 255), (0, 255, 255), (255, 255, 0),
        (0, 255, 0), (255, 0, 100), (100, 0, 255),
        (255, 100, 0), (0, 200, 255),
    ],
    "pastel": [
        (255, 179, 186), (255, 223, 186), (255, 255, 186),
        (186, 255, 201), (186, 225, 255), (219, 186, 255),
        (255, 186, 238), (200, 230, 255),
    ],
    "grayscale": [
        (40, 40, 40), (80, 80, 80), (120, 120, 120),
        (160, 160, 160), (200, 200, 200), (230, 230, 230),
        (60, 60, 60), (180, 180, 180),
    ],
}

DEFAULT_PALETTE = "sunset"

# ---------------------------------------------------------------------------
# Blend / easing helpers
# ---------------------------------------------------------------------------


def _lerp_color(
    c1: Tuple[int, int, int],
    c2: Tuple[int, int, int],
    t: float,
) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colours.  *t* in [0, 1]."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _ease_t(t: float, blend: str) -> float:
    """Apply an easing function to normalised distance *t*."""
    if blend == "linear":
        return t
    if blend == "ease":
        # Smooth-step (3t² − 2t³)
        return t * t * (3.0 - 2.0 * t)
    if blend == "quadratic":
        return t * t
    return t


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class GradientCell:
    """One Voronoi cell with gradient metadata."""
    seed: Tuple[float, float]
    inner_color: Tuple[int, int, int]
    outer_color: Tuple[int, int, int]
    max_dist: float = 0.0  # farthest pixel distance (computed)
    pixel_count: int = 0


@dataclass
class GradientResult:
    """Container for the rendered gradient image."""
    width: int
    height: int
    pixels: List[List[Tuple[int, int, int]]]  # row-major RGB
    cells: List[GradientCell] = field(default_factory=list)
    num_seeds: int = 0


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------


def generate(
    width: int = 400,
    height: int = 400,
    num_seeds: int = 20,
    palette: str = DEFAULT_PALETTE,
    custom_colors: Optional[List[str]] = None,
    blend: str = "linear",
    outline: bool = False,
    outline_color: Tuple[int, int, int] = (30, 30, 30),
    outline_width: int = 1,
    fade_amount: float = 0.55,
    seed: Optional[int] = None,
) -> GradientResult:
    """Generate a Voronoi gradient-fill image.

    Parameters
    ----------
    width, height : int
        Canvas dimensions in pixels.
    num_seeds : int
        Number of Voronoi seed points.
    palette : str
        Named palette key (sunset, ocean, forest, neon, pastel, grayscale).
    custom_colors : list[str] | None
        Hex colour strings to use instead of a named palette.
    blend : str
        Easing curve: 'linear', 'ease', or 'quadratic'.
    outline : bool
        Draw dark outlines along cell boundaries.
    outline_color : tuple
        RGB for outlines.
    outline_width : int
        Thickness of outlines (pixels checked by neighbour delta).
    fade_amount : float
        How much the outer colour fades toward white (0 = no fade, 1 = full).
    seed : int | None
        Random seed for reproducibility.
    """
    rng = random.Random(seed)

    # Build colour list
    if custom_colors:
        colors = [_hex_to_rgb(c) for c in custom_colors]
    else:
        colors = list(PALETTES.get(palette, PALETTES[DEFAULT_PALETTE]))

    # Generate seeds
    seeds: List[Tuple[float, float]] = [
        (rng.uniform(0, width - 1), rng.uniform(0, height - 1))
        for _ in range(num_seeds)
    ]

    # Build cells
    cells: List[GradientCell] = []
    for i, s in enumerate(seeds):
        inner = colors[i % len(colors)]
        # Outer colour: fade toward white
        outer = _lerp_color(inner, (255, 255, 255), fade_amount)
        cells.append(GradientCell(seed=s, inner_color=inner, outer_color=outer))

    # Assign pixels → nearest seed & track max distance per cell
    ownership = [[0] * width for _ in range(height)]
    dist_map = [[0.0] * width for _ in range(height)]

    for y in range(height):
        for x in range(width):
            best_idx = 0
            best_d = float("inf")
            for ci, c in enumerate(cells):
                dx = x - c.seed[0]
                dy = y - c.seed[1]
                d = dx * dx + dy * dy
                if d < best_d:
                    best_d = d
                    best_idx = ci
            ownership[y][x] = best_idx
            d_sqrt = math.sqrt(best_d)
            dist_map[y][x] = d_sqrt
            if d_sqrt > cells[best_idx].max_dist:
                cells[best_idx].max_dist = d_sqrt
            cells[best_idx].pixel_count += 1

    # Render pixels with gradient
    pixels: List[List[Tuple[int, int, int]]] = [
        [(0, 0, 0)] * width for _ in range(height)
    ]

    for y in range(height):
        for x in range(width):
            ci = ownership[y][x]
            c = cells[ci]
            max_d = c.max_dist if c.max_dist > 0 else 1.0
            t = min(dist_map[y][x] / max_d, 1.0)
            t = _ease_t(t, blend)
            pixels[y][x] = _lerp_color(c.inner_color, c.outer_color, t)

    # Outlines: detect cell-boundary pixels
    if outline:
        for y in range(height):
            for x in range(width):
                ci = ownership[y][x]
                is_edge = False
                for dy in range(-outline_width, outline_width + 1):
                    for dx in range(-outline_width, outline_width + 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < height and 0 <= nx < width:
                            if ownership[ny][nx] != ci:
                                is_edge = True
                                break
                    if is_edge:
                        break
                if is_edge:
                    pixels[y][x] = outline_color

    return GradientResult(
        width=width, height=height, pixels=pixels,
        cells=cells, num_seeds=num_seeds,
    )


# ---------------------------------------------------------------------------
# PNG writer (pure Python, no Pillow)
# ---------------------------------------------------------------------------


def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _make_png(
    pixels: List[List[Tuple[int, int, int]]],
    width: int,
    height: int,
) -> bytes:
    """Encode *pixels* (row-major RGB) as a minimal PNG file."""

    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(
        b"IDAT"[:4].replace(b"IDAT"[:4], b"IHDR"),
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
    )

    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filter: none
        for r, g, b in row:
            raw.extend((r, g, b))

    idat = _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


def save_png(result: GradientResult, path: str) -> str:
    """Save a :class:`GradientResult` as a PNG file and return the path."""
    data = _make_png(result.pixels, result.width, result.height)
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Voronoi gradient-fill renderer",
    )
    ap.add_argument("width", type=int, help="Canvas width in pixels")
    ap.add_argument("height", type=int, help="Canvas height in pixels")
    ap.add_argument("output", help="Output PNG path")
    ap.add_argument("--seeds", type=int, default=20, help="Number of seed points")
    ap.add_argument(
        "--palette",
        default=DEFAULT_PALETTE,
        help="Colour palette: sunset, ocean, forest, neon, pastel, grayscale, "
        "or comma-separated hex e.g. '#ff0000,#00ff00'",
    )
    ap.add_argument(
        "--blend",
        choices=["linear", "ease", "quadratic"],
        default="linear",
        help="Gradient easing curve",
    )
    ap.add_argument("--outline", action="store_true", help="Draw cell-edge outlines")
    ap.add_argument(
        "--outline-color",
        default="1e1e1e",
        help="Outline hex colour (default: 1e1e1e)",
    )
    ap.add_argument(
        "--outline-width",
        type=int,
        default=1,
        help="Outline thickness in pixels",
    )
    ap.add_argument(
        "--fade",
        type=float,
        default=0.55,
        help="Fade amount toward white at cell edges (0–1)",
    )
    ap.add_argument("--seed", type=int, default=None, help="Random seed")

    args = ap.parse_args()

    # Handle custom hex palette
    custom = None
    palette = args.palette
    if "," in palette or palette.startswith("#"):
        custom = [c.strip() for c in palette.split(",")]
        palette = DEFAULT_PALETTE

    result = generate(
        width=args.width,
        height=args.height,
        num_seeds=args.seeds,
        palette=palette,
        custom_colors=custom,
        blend=args.blend,
        outline=args.outline,
        outline_color=_hex_to_rgb(args.outline_color),
        outline_width=args.outline_width,
        fade_amount=args.fade,
        seed=args.seed,
    )

    out = save_png(result, args.output)
    print(f"Saved gradient Voronoi ({args.width}x{args.height}, "
          f"{args.seeds} cells, blend={args.blend}) → {out}")


if __name__ == "__main__":
    main()
