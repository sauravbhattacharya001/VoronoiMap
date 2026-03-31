"""Voronoi Kaleidoscope Generator.

Creates kaleidoscope / mandala art by generating a Voronoi wedge and
reflecting it around a central axis with N-fold symmetry. The result
is a mesmerizing, symmetrical pattern reminiscent of looking through
a kaleidoscope.

Algorithm:
1. Generate random seed points inside a triangular wedge (one slice
   of the full circle, angle = 2π / N).
2. Build Voronoi cells for the wedge.
3. Reflect and rotate the wedge N times around the centre to fill
   the circle.
4. Render as SVG with optional colour themes and border effects.

Features:
- Configurable fold count (3 to 24 symmetry axes)
- Multiple colour palettes (jewel, pastel, neon, earth, ice)
- Optional circular mask for clean mandala look
- JSON export for downstream processing
- Adjustable seed density and canvas size
- Glow / shadow border effects for a stained-glass feel

CLI usage
---------
::

    python vormap_kaleidoscope.py output.svg --folds 6 --seeds 40
    python vormap_kaleidoscope.py mandala.svg --folds 8 --palette jewel --size 1000
    python vormap_kaleidoscope.py art.svg --folds 12 --seeds 80 --glow --mask
    python vormap_kaleidoscope.py data.json --format json --folds 6 --seeds 30

"""

import argparse
import json
import math
import os
import random
import sys

# ── Colour palettes ─────────────────────────────────────────────────────

PALETTES = {
    "jewel": [
        "#E0115F", "#50C878", "#0F52BA", "#E6E200", "#9B59B6",
        "#FF6F00", "#1ABC9C", "#C0392B", "#2980B9", "#F39C12",
    ],
    "pastel": [
        "#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9", "#BAE1FF",
        "#E8BAFF", "#FFC8DD", "#BDE0FE", "#A2D2FF", "#CDB4DB",
    ],
    "neon": [
        "#FF073A", "#39FF14", "#FF6EC7", "#DFFF00", "#00FFFF",
        "#FF3F00", "#BC13FE", "#04D9FF", "#FF5F1F", "#CCFF00",
    ],
    "earth": [
        "#8B4513", "#D2691E", "#DAA520", "#556B2F", "#6B8E23",
        "#A0522D", "#CD853F", "#DEB887", "#808000", "#BC8F8F",
    ],
    "ice": [
        "#E0F7FA", "#B2EBF2", "#80DEEA", "#4DD0E1", "#26C6DA",
        "#00BCD4", "#00ACC1", "#0097A7", "#00838F", "#006064",
    ],
}

DEFAULT_PALETTE = "jewel"


# ── Geometry helpers ─────────────────────────────────────────────────────


def _generate_wedge_seeds(n_seeds, wedge_angle, radius, rng):
    """Generate random points inside a triangular wedge [0, wedge_angle] from origin."""
    seeds = []
    for _ in range(n_seeds):
        # Use sqrt for uniform distribution in polar coords
        r = radius * math.sqrt(rng.random()) * 0.92  # slight inset
        theta = rng.random() * wedge_angle
        seeds.append((r * math.cos(theta), r * math.sin(theta)))
    return seeds


def _voronoi_cells_bounded(seeds, radius):
    """Compute Voronoi cells for seeds clipped to a circle of given radius.

    Uses brute-force nearest-seed assignment on a polar grid for simplicity.
    Returns list of (seed_index, polygon_points) tuples.
    """
    if not seeds:
        return []

    # Build region polygons by sampling boundary and assigning to nearest seed
    n_angle = 120
    n_radial = 40
    # Accumulate points per seed
    cell_points = {i: [] for i in range(len(seeds))}

    for ri in range(n_radial + 1):
        r = radius * ri / n_radial
        for ai in range(n_angle):
            a = 2 * math.pi * ai / n_angle
            px, py = r * math.cos(a), r * math.sin(a)
            best = 0
            best_d = float('inf')
            for si, (sx, sy) in enumerate(seeds):
                d = (px - sx) ** 2 + (py - sy) ** 2
                if d < best_d:
                    best_d = d
                    best = si
            cell_points[best].append((px, py))

    # Convert point clouds to convex hull polygons
    cells = []
    for si in range(len(seeds)):
        pts = cell_points[si]
        if len(pts) < 3:
            continue
        hull = _convex_hull(pts)
        if hull:
            cells.append((si, hull))
    return cells


def _convex_hull(points):
    """Simple Graham scan convex hull."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _rotate_point(x, y, angle):
    """Rotate (x,y) by angle radians around origin."""
    c, s = math.cos(angle), math.sin(angle)
    return x * c - y * s, x * s + y * c


def _reflect_y(x, y):
    """Reflect across x-axis (negate y)."""
    return x, -y


# ── Wedge-based Voronoi ─────────────────────────────────────────────────

def _voronoi_wedge_cells(seeds, wedge_angle, radius):
    """Compute Voronoi cells restricted to a wedge [0, wedge_angle]."""
    if not seeds:
        return []

    n_angle = 80
    n_radial = 30
    cell_points = {i: [] for i in range(len(seeds))}

    for ri in range(1, n_radial + 1):
        r = radius * ri / n_radial
        for ai in range(n_angle + 1):
            a = wedge_angle * ai / n_angle
            px, py = r * math.cos(a), r * math.sin(a)
            best = 0
            best_d = float('inf')
            for si, (sx, sy) in enumerate(seeds):
                d = (px - sx) ** 2 + (py - sy) ** 2
                if d < best_d:
                    best_d = d
                    best = si
            cell_points[best].append((px, py))

    cells = []
    for si in range(len(seeds)):
        pts = cell_points[si]
        if len(pts) < 3:
            continue
        hull = _convex_hull(pts)
        if hull:
            cells.append((si, hull))
    return cells


# ── SVG rendering ────────────────────────────────────────────────────────

def _polygon_svg(points, fill, stroke="#222", stroke_width=0.5, opacity=1.0):
    pts_str = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return (f'<polygon points="{pts_str}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{stroke_width}" '
            f'opacity="{opacity}" />')


def generate_kaleidoscope(
    folds=6,
    n_seeds=30,
    size=800,
    palette_name=None,
    mask=True,
    glow=False,
    seed=None,
):
    """Generate kaleidoscope SVG string.

    Parameters
    ----------
    folds : int
        Number of symmetry folds (3–24).
    n_seeds : int
        Seeds per wedge slice.
    size : int
        Canvas width/height in pixels.
    palette_name : str
        Colour palette name.
    mask : bool
        Apply circular mask.
    glow : bool
        Add glow effect to cell borders.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    str
        SVG document string.
    """
    folds = max(3, min(24, folds))
    palette = PALETTES.get(palette_name or DEFAULT_PALETTE, PALETTES[DEFAULT_PALETTE])
    rng = random.Random(seed)
    radius = size / 2 * 0.95
    cx, cy = size / 2, size / 2
    wedge_angle = math.pi / folds  # half-fold angle (we reflect once)

    # Generate seeds in wedge
    wedge_seeds = _generate_wedge_seeds(n_seeds, wedge_angle, radius, rng)

    # Compute Voronoi cells within wedge
    cells = _voronoi_wedge_cells(wedge_seeds, wedge_angle, radius)

    # Assign colours
    cell_colors = {}
    for si, _ in cells:
        cell_colors[si] = palette[si % len(palette)]

    # Build SVG
    parts = []
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'width="{size}" height="{size}" viewBox="0 0 {size} {size}">')
    parts.append(f'<rect width="{size}" height="{size}" fill="#111" />')

    # Defs
    parts.append('<defs>')
    if mask:
        parts.append(f'<clipPath id="circle-mask">'
                     f'<circle cx="{cx}" cy="{cy}" r="{radius}" />'
                     f'</clipPath>')
    if glow:
        parts.append('<filter id="glow"><feGaussianBlur stdDeviation="2" result="g"/>'
                     '<feMerge><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/>'
                     '</feMerge></filter>')
    parts.append('</defs>')

    # Main group
    clip = ' clip-path="url(#circle-mask)"' if mask else ''
    filt = ' filter="url(#glow)"' if glow else ''
    parts.append(f'<g transform="translate({cx},{cy})"{clip}{filt}>')

    # For each fold, render wedge + reflected wedge
    for f in range(folds):
        angle = 2 * math.pi * f / folds
        for reflected in (False, True):
            for si, polygon in cells:
                transformed = []
                for px, py in polygon:
                    x, y = px, py
                    if reflected:
                        x, y = _reflect_y(x, y)
                    x, y = _rotate_point(x, y, angle)
                    transformed.append((x, y))
                parts.append(_polygon_svg(
                    transformed,
                    fill=cell_colors.get(si, palette[0]),
                    stroke="#000" if not glow else "#fff3",
                    stroke_width=0.8 if glow else 0.5,
                ))

    parts.append('</g>')

    # Optional decorative centre dot
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius * 0.015}" fill="#fff" opacity="0.6" />')
    parts.append('</svg>')

    return "\n".join(parts)


def generate_kaleidoscope_data(folds=6, n_seeds=30, size=800, palette_name=None, seed=None):
    """Generate kaleidoscope data as JSON-serialisable dict."""
    folds = max(3, min(24, folds))
    palette = PALETTES.get(palette_name or DEFAULT_PALETTE, PALETTES[DEFAULT_PALETTE])
    rng = random.Random(seed)
    radius = size / 2 * 0.95
    wedge_angle = math.pi / folds

    wedge_seeds = _generate_wedge_seeds(n_seeds, wedge_angle, radius, rng)
    cells = _voronoi_wedge_cells(wedge_seeds, wedge_angle, radius)

    result = {
        "folds": folds,
        "seeds_per_wedge": n_seeds,
        "size": size,
        "palette": palette_name or DEFAULT_PALETTE,
        "radius": radius,
        "wedge_seeds": [{"x": round(x, 2), "y": round(y, 2)} for x, y in wedge_seeds],
        "wedge_cells": [
            {
                "seed_index": si,
                "color": palette[si % len(palette)],
                "polygon": [{"x": round(x, 2), "y": round(y, 2)} for x, y in poly],
            }
            for si, poly in cells
        ],
        "total_rendered_cells": len(cells) * folds * 2,
    }
    return result


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Voronoi Kaleidoscope Generator — mandala art from reflected Voronoi wedges",
    )
    parser.add_argument("output", help="Output file path (.svg or .json)")
    parser.add_argument("--folds", type=int, default=6,
                        help="Symmetry folds, 3–24 (default: 6)")
    parser.add_argument("--seeds", type=int, default=30,
                        help="Seeds per wedge slice (default: 30)")
    parser.add_argument("--size", type=int, default=800,
                        help="Canvas size in pixels (default: 800)")
    parser.add_argument("--palette", choices=list(PALETTES.keys()),
                        default=DEFAULT_PALETTE,
                        help=f"Colour palette (default: {DEFAULT_PALETTE})")
    parser.add_argument("--format", choices=["svg", "json"], default=None,
                        help="Output format (auto-detected from extension)")
    parser.add_argument("--no-mask", action="store_true",
                        help="Disable circular mask")
    parser.add_argument("--glow", action="store_true",
                        help="Add glow effect to borders")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")

    args = parser.parse_args(argv)

    # Detect format
    fmt = args.format
    if fmt is None:
        ext = os.path.splitext(args.output)[1].lower()
        fmt = "json" if ext == ".json" else "svg"

    if fmt == "json":
        data = generate_kaleidoscope_data(
            folds=args.folds,
            n_seeds=args.seeds,
            size=args.size,
            palette_name=args.palette,
            seed=args.seed,
        )
        content = json.dumps(data, indent=2)
    else:
        content = generate_kaleidoscope(
            folds=args.folds,
            n_seeds=args.seeds,
            size=args.size,
            palette_name=args.palette,
            mask=not args.no_mask,
            glow=args.glow,
            seed=args.seed,
        )

    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(content)

    print(f"Kaleidoscope ({args.folds}-fold, {args.seeds} seeds/wedge) -> {args.output}")


if __name__ == "__main__":
    main()
