"""Voronoi Crystal Growth Simulator — anisotropic nucleation & growth.

Simulates crystal nucleation and growth on a 2-D canvas where each
crystal grain corresponds to a Voronoi cell.  Seeds appear at random
times (nucleation) and grow outward at direction-dependent rates
(anisotropic growth), producing realistic polycrystalline micro-
structure patterns.

Features
--------
* Time-stepped growth with configurable nucleation rate
* Anisotropic growth — crystals grow faster along preferred axes
* Grain boundary extraction and width statistics
* Temperature gradient influence on nucleation probability
* Export frames as PNG sequence or animated GIF
* Pure-Python fallback (NumPy optional but recommended)

CLI usage
---------
::

    python vormap_crystal.py --width 512 --height 512 --seeds 30 -o crystal.png
    python vormap_crystal.py --width 256 --height 256 --seeds 20 --animate growth.gif --frames 60
    python vormap_crystal.py --width 400 --height 400 --seeds 50 --anisotropy 2.5 --temp-gradient

"""

from __future__ import annotations

import argparse
import colorsys
import math
import random
import struct
import sys
import zlib
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# NumPy fast-path / pure-Python fallback
# ---------------------------------------------------------------------------
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class Crystal:
    """A single crystal grain."""
    __slots__ = ("cx", "cy", "birth_step", "orientation", "anisotropy",
                 "color", "id")

    def __init__(self, cx: float, cy: float, birth_step: int,
                 orientation: float = 0.0, anisotropy: float = 1.0,
                 color: Tuple[int, int, int] = (200, 200, 200),
                 cid: int = 0):
        self.cx = cx
        self.cy = cy
        self.birth_step = birth_step
        self.orientation = orientation  # radians — preferred growth axis
        self.anisotropy = anisotropy    # ratio fast/slow axis
        self.color = color
        self.id = cid


class CrystalConfig:
    """Configuration for a crystal growth simulation."""
    __slots__ = ("width", "height", "initial_seeds", "nucleation_rate",
                 "anisotropy", "total_steps", "temp_gradient",
                 "border_color", "bg_color", "seed")

    def __init__(self, *, width: int = 512, height: int = 512,
                 initial_seeds: int = 20, nucleation_rate: float = 0.0,
                 anisotropy: float = 1.5, total_steps: int = 80,
                 temp_gradient: bool = False,
                 border_color: Tuple[int, int, int] = (30, 30, 30),
                 bg_color: Tuple[int, int, int] = (10, 10, 10),
                 seed: Optional[int] = None):
        self.width = width
        self.height = height
        self.initial_seeds = initial_seeds
        self.nucleation_rate = nucleation_rate
        self.anisotropy = anisotropy
        self.total_steps = total_steps
        self.temp_gradient = temp_gradient
        self.border_color = border_color
        self.bg_color = bg_color
        self.seed = seed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _distinct_colors(n: int) -> List[Tuple[int, int, int]]:
    """Generate *n* visually distinct pastel colours."""
    colors: list[Tuple[int, int, int]] = []
    for i in range(n):
        h = i / max(n, 1)
        s = 0.45 + 0.15 * ((i * 7) % 5) / 4
        v = 0.75 + 0.15 * ((i * 3) % 5) / 4
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors


def _aniso_dist(px: float, py: float, crystal: Crystal) -> float:
    """Effective distance accounting for anisotropic growth."""
    dx = px - crystal.cx
    dy = py - crystal.cy
    cos_o = math.cos(crystal.orientation)
    sin_o = math.sin(crystal.orientation)
    # project onto crystal axes
    along = dx * cos_o + dy * sin_o
    perp  = -dx * sin_o + dy * cos_o
    # fast axis has lower effective distance
    return math.sqrt((along / crystal.anisotropy) ** 2 + perp ** 2)

# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def simulate(config: CrystalConfig | None = None,
             ) -> Tuple[List[List[int]], List[Crystal], List[List[List[int]]]]:
    """Run crystal growth simulation.

    Returns
    -------
    grid : 2-D list  (height × width) of crystal ids (-1 = empty)
    crystals : list of Crystal objects
    frames : list of grid snapshots (one per step), for animation
    """
    cfg = config or CrystalConfig()
    rng = random.Random(cfg.seed)
    w, h = cfg.width, cfg.height

    # Initialise grid
    grid: List[List[int]] = [[-1] * w for _ in range(h)]
    crystals: List[Crystal] = []
    frames: List[List[List[int]]] = []

    max_crystals = cfg.initial_seeds + int(cfg.nucleation_rate * cfg.total_steps) + 10
    palette = _distinct_colors(max_crystals)

    # Place initial seeds
    for i in range(cfg.initial_seeds):
        cx = rng.randint(0, w - 1)
        cy = rng.randint(0, h - 1)
        orient = rng.uniform(0, math.pi)
        aniso = cfg.anisotropy * rng.uniform(0.8, 1.2)
        c = Crystal(cx, cy, 0, orient, aniso, palette[i], cid=i)
        crystals.append(c)
        grid[cy][cx] = i

    def _snapshot() -> List[List[int]]:
        return [row[:] for row in grid]

    frames.append(_snapshot())

    # Growth loop
    for step in range(1, cfg.total_steps + 1):
        # Nucleation
        if cfg.nucleation_rate > 0 and rng.random() < cfg.nucleation_rate:
            cx = rng.randint(0, w - 1)
            cy = rng.randint(0, h - 1)
            if grid[cy][cx] == -1:
                idx = len(crystals)
                orient = rng.uniform(0, math.pi)
                aniso = cfg.anisotropy * rng.uniform(0.8, 1.2)
                # Temperature gradient: nucleation more likely at top
                if cfg.temp_gradient:
                    prob = 1.0 - (cy / h) * 0.7
                    if rng.random() > prob:
                        continue
                c = Crystal(cx, cy, step, orient, aniso,
                            palette[idx % len(palette)], cid=idx)
                crystals.append(c)
                grid[cy][cx] = idx

        # Expand existing crystals by one pixel shell
        new_claims: List[Tuple[int, int, int, float]] = []  # (y, x, cid, dist)
        for y in range(h):
            for x in range(w):
                if grid[y][x] != -1:
                    continue
                # Check neighbours
                best_id = -1
                best_dist = float("inf")
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and grid[ny][nx] != -1:
                            cid = grid[ny][nx]
                            cr = crystals[cid]
                            age = step - cr.birth_step
                            d = _aniso_dist(x, y, cr) / max(age, 1)
                            if d < best_dist:
                                best_dist = d
                                best_id = cid
                if best_id != -1:
                    new_claims.append((y, x, best_id, best_dist))

        for y, x, cid, _ in new_claims:
            if grid[y][x] == -1:
                grid[y][x] = cid

        frames.append(_snapshot())

        # Early exit if fully filled
        if all(grid[y][x] != -1 for y in range(h) for x in range(w)):
            break

    return grid, crystals, frames

# ---------------------------------------------------------------------------
# Grain boundary extraction
# ---------------------------------------------------------------------------

def extract_boundaries(grid: List[List[int]]) -> List[Tuple[int, int]]:
    """Return list of (x, y) pixels on grain boundaries."""
    h = len(grid)
    w = len(grid[0]) if h else 0
    borders: list[Tuple[int, int]] = []
    for y in range(h):
        for x in range(w):
            cid = grid[y][x]
            if cid == -1:
                continue
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w:
                    if grid[ny][nx] != -1 and grid[ny][nx] != cid:
                        borders.append((x, y))
                        break
    return borders

# ---------------------------------------------------------------------------
# Grain statistics
# ---------------------------------------------------------------------------

def grain_stats(grid: List[List[int]], crystals: List[Crystal]) -> dict:
    """Compute per-grain area and overall statistics."""
    from collections import Counter
    counts = Counter(cid for row in grid for cid in row if cid != -1)
    areas = [counts.get(c.id, 0) for c in crystals]
    total = sum(areas) or 1
    return {
        "num_grains": len(crystals),
        "areas": {c.id: counts.get(c.id, 0) for c in crystals},
        "mean_area": total / max(len(crystals), 1),
        "min_area": min(areas) if areas else 0,
        "max_area": max(areas) if areas else 0,
        "coverage": total / (len(grid) * (len(grid[0]) if grid else 1)),
    }

# ---------------------------------------------------------------------------
# PNG writer (minimal, no dependency)
# ---------------------------------------------------------------------------

def _write_png(path: str, pixels: List[List[Tuple[int, int, int]]],
               width: int, height: int) -> None:
    """Write an RGB PNG file without external dependencies."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b""
    for row in pixels:
        raw += b"\x00"  # filter none
        for r, g, b in row:
            raw += bytes((r, g, b))
    idat = zlib.compress(raw, 9)
    with open(path, "wb") as f:
        f.write(sig)
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", idat))
        f.write(_chunk(b"IEND", b""))

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(grid: List[List[int]], crystals: List[Crystal],
           config: CrystalConfig | None = None,
           show_borders: bool = True) -> List[List[Tuple[int, int, int]]]:
    """Render the grid to a pixel buffer."""
    cfg = config or CrystalConfig()
    h = len(grid)
    w = len(grid[0]) if h else 0
    img: List[List[Tuple[int, int, int]]] = [
        [cfg.bg_color] * w for _ in range(h)
    ]

    for y in range(h):
        for x in range(w):
            cid = grid[y][x]
            if cid != -1 and cid < len(crystals):
                img[y][x] = crystals[cid].color

    if show_borders:
        borders = extract_boundaries(grid)
        for x, y in borders:
            img[y][x] = cfg.border_color

    return img


def save_image(path: str, grid: List[List[int]], crystals: List[Crystal],
               config: CrystalConfig | None = None,
               show_borders: bool = True) -> None:
    """Render and save as PNG."""
    cfg = config or CrystalConfig()
    img = render(grid, crystals, cfg, show_borders)
    h = len(img)
    w = len(img[0]) if h else 0
    _write_png(path, img, w, h)


def save_animation(path: str, frames: List[List[List[int]]],
                   crystals: List[Crystal],
                   config: CrystalConfig | None = None,
                   show_borders: bool = True,
                   every: int = 1) -> None:
    """Save growth animation as animated GIF (requires Pillow) or PNG sequence."""
    cfg = config or CrystalConfig()
    try:
        from PIL import Image as PILImage
        pil_frames = []
        for i, frame in enumerate(frames):
            if i % every != 0 and i != len(frames) - 1:
                continue
            img = render(frame, crystals, cfg, show_borders)
            h = len(img)
            w = len(img[0]) if h else 0
            pil_img = PILImage.new("RGB", (w, h))
            pil_img.putdata([px for row in img for px in row])
            pil_frames.append(pil_img)
        if pil_frames:
            pil_frames[0].save(path, save_all=True,
                               append_images=pil_frames[1:],
                               duration=100, loop=0)
            print(f"Saved animated GIF: {path} ({len(pil_frames)} frames)")
    except ImportError:
        # Fallback: save last frame as PNG
        if frames:
            img = render(frames[-1], crystals, cfg, show_borders)
            h = len(img)
            w = len(img[0]) if h else 0
            _write_png(path, img, w, h)
            print(f"Pillow not available — saved final frame as PNG: {path}")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi Crystal Growth Simulator")
    parser.add_argument("--width", type=int, default=256,
                        help="Canvas width in pixels (default 256)")
    parser.add_argument("--height", type=int, default=256,
                        help="Canvas height in pixels (default 256)")
    parser.add_argument("--seeds", type=int, default=20,
                        help="Number of initial crystal seeds (default 20)")
    parser.add_argument("--nucleation-rate", type=float, default=0.0,
                        help="Probability of new seed per step (default 0)")
    parser.add_argument("--anisotropy", type=float, default=1.5,
                        help="Anisotropy ratio for crystal growth (default 1.5)")
    parser.add_argument("--steps", type=int, default=80,
                        help="Total simulation steps (default 80)")
    parser.add_argument("--temp-gradient", action="store_true",
                        help="Apply temperature gradient (more nucleation at top)")
    parser.add_argument("--no-borders", action="store_true",
                        help="Hide grain boundaries")
    parser.add_argument("--animate", metavar="FILE",
                        help="Save growth animation as GIF")
    parser.add_argument("--frame-skip", type=int, default=1,
                        help="Save every Nth frame in animation (default 1)")
    parser.add_argument("--stats", action="store_true",
                        help="Print grain statistics")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("-o", "--output", default="crystal.png",
                        help="Output PNG path (default crystal.png)")
    args = parser.parse_args(argv)

    cfg = CrystalConfig(
        width=args.width, height=args.height,
        initial_seeds=args.seeds,
        nucleation_rate=args.nucleation_rate,
        anisotropy=args.anisotropy,
        total_steps=args.steps,
        temp_gradient=args.temp_gradient,
        seed=args.seed,
    )

    print(f"Simulating crystal growth: {cfg.width}x{cfg.height}, "
          f"{cfg.initial_seeds} seeds, {cfg.total_steps} steps...")

    grid, crystals, frames = simulate(cfg)
    show_borders = not args.no_borders

    save_image(args.output, grid, crystals, cfg, show_borders)
    print(f"Saved final state: {args.output}")

    if args.animate:
        save_animation(args.animate, frames, crystals, cfg,
                       show_borders, every=args.frame_skip)

    if args.stats:
        stats = grain_stats(grid, crystals)
        print(f"\n{'-' * 40}")
        print(f"Grains:       {stats['num_grains']}")
        print(f"Mean area:    {stats['mean_area']:.1f} px")
        print(f"Min area:     {stats['min_area']} px")
        print(f"Max area:     {stats['max_area']} px")
        print(f"Coverage:     {stats['coverage'] * 100:.1f}%")
        print(f"{'-' * 40}")


if __name__ == "__main__":
    main()
