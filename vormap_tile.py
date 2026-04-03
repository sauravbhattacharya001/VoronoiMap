"""Seamless tileable Voronoi pattern generator.

Creates Voronoi diagrams that tile perfectly — the left edge matches the
right edge and the top edge matches the bottom edge.  Essential for:

- Game textures and materials
- Wallpaper / background patterns
- Fabric and textile design
- Procedural content generation

The trick: duplicate seed points into 8 surrounding copies so that cells
near borders are influenced by wrapped neighbors.  Then clip to the
central tile and export.

Usage (CLI)
-----------
    python vormap_tile.py --seeds 40 --width 512 --height 512 -o tile.svg
    python vormap_tile.py --seeds 20 --style stained-glass --format png -o tile.png
    python vormap_tile.py --seeds 60 --style mosaic --palette viridis --tilepreview 3x3 -o preview.svg

Styles
------
- **flat** — solid color fills (default)
- **stained-glass** — dark outlines, jewel-tone fills, subtle inner glow
- **wireframe** — edges only, no fill
- **mosaic** — textured fills with slight color variation per cell
- **gradient** — radial gradient from cell center outward

Tile preview
------------
Use ``--tilepreview RxC`` to render an R×C grid proving the pattern
tiles seamlessly.

Programmatic API
----------------
    from vormap_tile import TileGenerator
    gen = TileGenerator(width=256, height=256, seeds=30, style='flat')
    svg_str = gen.render_svg()
    gen.save('output.svg')
"""

import argparse
import hashlib
import math
import os
import random
import struct
import sys

from vormap_geometry import polygon_area as _polygon_area, polygon_centroid as _polygon_centroid

# Optional dependencies — graceful degradation
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ── Colour palettes ──────────────────────────────────────────────────

PALETTES = {
    "default": [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
        "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    ],
    "viridis": [
        "#440154", "#482777", "#3f4a8a", "#31678e", "#26838f",
        "#1f9d8a", "#6cce5a", "#b6de2b", "#fee825", "#addc30",
    ],
    "pastel": [
        "#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6",
        "#ffffcc", "#e5d8bd", "#fddaec", "#f2f2f2", "#b3e2cd",
    ],
    "earth": [
        "#8c510a", "#bf812d", "#dfc27d", "#f6e8c3", "#c7eae5",
        "#80cdc1", "#35978f", "#01665e", "#543005", "#003c30",
    ],
    "neon": [
        "#ff00ff", "#00ffff", "#ff3366", "#33ff33", "#ffff00",
        "#ff6600", "#6633ff", "#00ff99", "#ff0099", "#66ffcc",
    ],
    "jewel": [
        "#9b2335", "#0f4c81", "#006b54", "#7b3f00", "#4b0082",
        "#c41e3a", "#002fa7", "#009473", "#b5651d", "#663399",
    ],
}


# ── Geometry helpers ────────────────────────────────────────────────

def _voronoi_cell_for_seed(sx, sy, all_seeds, width, height, margin=2):
    """Compute the Voronoi cell polygon for seed (sx, sy) by
    intersecting half-planes with the tile rectangle.

    Uses the half-plane intersection approach: start with the bounding
    rectangle and clip against the perpendicular bisector of every
    other seed.
    """
    # Start with bounding rect (with small margin to avoid edge gaps)
    poly = [
        (-margin, -margin),
        (width + margin, -margin),
        (width + margin, height + margin),
        (-margin, height + margin),
    ]

    for ox, oy in all_seeds:
        if ox == sx and oy == sy:
            continue
        # Bisector: points closer to (sx,sy) than (ox,oy)
        poly = _clip_polygon_by_bisector(poly, sx, sy, ox, oy)
        if not poly:
            return []

    return poly


def _clip_polygon_by_bisector(poly, sx, sy, ox, oy):
    """Sutherland-Hodgman clip of *poly* to the half-plane closer to
    (sx,sy) than (ox,oy)."""
    if not poly:
        return []

    # Midpoint and normal
    mx, my = (sx + ox) / 2, (sy + oy) / 2
    nx, ny = sx - ox, sy - oy  # normal points toward seed

    output = []
    n = len(poly)
    for i in range(n):
        curr = poly[i]
        nxt = poly[(i + 1) % n]

        dc = (curr[0] - mx) * nx + (curr[1] - my) * ny
        dn = (nxt[0] - mx) * nx + (nxt[1] - my) * ny

        if dc >= 0:
            output.append(curr)
            if dn < 0:
                output.append(_intersect_line(curr, nxt, mx, my, nx, ny))
        elif dn >= 0:
            output.append(_intersect_line(curr, nxt, mx, my, nx, ny))

    return output


def _intersect_line(p1, p2, mx, my, nx, ny):
    """Intersection of segment p1→p2 with bisector plane."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    denom = dx * nx + dy * ny
    if abs(denom) < 1e-12:
        return p1
    t = ((mx - p1[0]) * nx + (my - p1[1]) * ny) / denom
    return (p1[0] + t * dx, p1[1] + t * dy)


def _clip_polygon_to_rect(poly, x0, y0, x1, y1):
    """Sutherland-Hodgman clip of polygon to axis-aligned rectangle."""
    # Clip against each edge
    for edge_nx, edge_ny, edge_px, edge_py in [
        (1, 0, x0, y0),   # left
        (0, 1, x0, y0),   # top
        (-1, 0, x1, y1),  # right
        (0, -1, x1, y1),  # bottom
    ]:
        if not poly:
            return []
        output = []
        n = len(poly)
        for i in range(n):
            curr = poly[i]
            nxt = poly[(i + 1) % n]
            dc = (curr[0] - edge_px) * edge_nx + (curr[1] - edge_py) * edge_ny
            dn = (nxt[0] - edge_px) * edge_nx + (nxt[1] - edge_py) * edge_ny
            if dc >= 0:
                output.append(curr)
                if dn < 0:
                    output.append(_intersect_line(curr, nxt, edge_px, edge_py, edge_nx, edge_ny))
            elif dn >= 0:
                output.append(_intersect_line(curr, nxt, edge_px, edge_py, edge_nx, edge_ny))
        poly = output
    return poly


def _points_to_svg_str(poly):
    """Convert polygon points to SVG points attribute string."""
    return " ".join("%.2f,%.2f" % (x, y) for x, y in poly)


# ── Tile Generator ──────────────────────────────────────────────────

class TileGenerator:
    """Generate seamless tileable Voronoi patterns.

    Parameters
    ----------
    width : int
        Tile width in pixels (default 512).
    height : int
        Tile height in pixels (default 512).
    seeds : int
        Number of seed points (default 30).
    style : str
        Visual style: flat, stained-glass, wireframe, mosaic, gradient.
    palette : str
        Colour palette name (see PALETTES).
    seed : int or None
        Random seed for reproducibility.
    lloyd_iterations : int
        Lloyd relaxation iterations for more regular cells (default 2).
    """

    def __init__(self, width=512, height=512, seeds=30, style="flat",
                 palette="default", seed=None, lloyd_iterations=2):
        self.width = width
        self.height = height
        self.n_seeds = seeds
        self.style = style
        self.palette_name = palette
        self.colors = PALETTES.get(palette, PALETTES["default"])
        self.rng = random.Random(seed)
        self.lloyd_iterations = lloyd_iterations

        # Generate and relax seeds
        self.seed_points = self._generate_seeds()
        for _ in range(self.lloyd_iterations):
            self.seed_points = self._lloyd_relax(self.seed_points)

        # Compute cells
        self.cells = self._compute_tiled_cells()

    def _generate_seeds(self):
        """Generate random seed points within the tile."""
        return [
            (self.rng.uniform(0, self.width),
             self.rng.uniform(0, self.height))
            for _ in range(self.n_seeds)
        ]

    def _wrap_seeds(self, seeds):
        """Create 9 copies of seeds (center + 8 neighbors) for tiling."""
        wrapped = []
        for dx in (-self.width, 0, self.width):
            for dy in (-self.height, 0, self.height):
                for sx, sy in seeds:
                    wrapped.append((sx + dx, sy + dy))
        return wrapped

    def _lloyd_relax(self, seeds):
        """One iteration of Lloyd relaxation with toroidal wrapping."""
        wrapped = self._wrap_seeds(seeds)
        new_seeds = []
        for i, (sx, sy) in enumerate(seeds):
            cell = _voronoi_cell_for_seed(sx, sy, wrapped, self.width, self.height)
            clipped = _clip_polygon_to_rect(cell, 0, 0, self.width, self.height)
            if clipped and _polygon_area(clipped) > 1e-6:
                cx, cy = _polygon_centroid(clipped)
                # Wrap centroid back into tile
                cx = cx % self.width
                cy = cy % self.height
                new_seeds.append((cx, cy))
            else:
                new_seeds.append((sx, sy))
        return new_seeds

    def _compute_tiled_cells(self):
        """Compute Voronoi cells using wrapped seeds, clipped to tile."""
        wrapped = self._wrap_seeds(self.seed_points)
        cells = []
        for i, (sx, sy) in enumerate(self.seed_points):
            cell = _voronoi_cell_for_seed(sx, sy, wrapped, self.width, self.height)
            clipped = _clip_polygon_to_rect(cell, 0, 0, self.width, self.height)
            if clipped and len(clipped) >= 3:
                color = self.colors[i % len(self.colors)]
                cells.append({
                    "seed": (sx, sy),
                    "polygon": clipped,
                    "color": color,
                    "index": i,
                })
        return cells

    def render_svg(self, tile_repeat=None):
        """Render the tile as an SVG string.

        Parameters
        ----------
        tile_repeat : tuple or None
            If (cols, rows), render a cols×rows grid to prove tiling.
        """
        if tile_repeat:
            cols, rows = tile_repeat
            vw, vh = self.width * cols, self.height * rows
        else:
            cols, rows = 1, 1
            vw, vh = self.width, self.height

        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="%d" height="%d" viewBox="0 0 %d %d">' % (vw, vh, vw, vh),
        ]

        # Defs for gradient style
        if self.style == "gradient":
            parts.append("<defs>")
            for cell in self.cells:
                idx = cell["index"]
                cx, cy = cell["seed"]
                r = math.sqrt(_polygon_area(cell["polygon"]) / math.pi) * 1.2
                parts.append(
                    '<radialGradient id="g%d" cx="%.1f" cy="%.1f" r="%.1f" '
                    'gradientUnits="userSpaceOnUse">' % (idx, cx, cy, r)
                )
                parts.append(
                    '<stop offset="0%%" stop-color="%s" stop-opacity="1"/>' % cell["color"]
                )
                parts.append(
                    '<stop offset="100%%" stop-color="%s" stop-opacity="0.5"/>' % cell["color"]
                )
                parts.append("</radialGradient>")
            parts.append("</defs>")

        # Background
        parts.append('<rect width="%d" height="%d" fill="#1a1a2e"/>' % (vw, vh))

        # Render tile copies
        for col in range(cols):
            for row in range(rows):
                ox, oy = col * self.width, row * self.height
                if cols > 1 or rows > 1:
                    parts.append('<g transform="translate(%d,%d)">' % (ox, oy))
                self._render_cells_svg(parts)
                if cols > 1 or rows > 1:
                    parts.append("</g>")

        parts.append("</svg>")
        return "\n".join(parts)

    def _render_cells_svg(self, parts):
        """Append SVG elements for all cells in current style."""
        for cell in self.cells:
            poly = cell["polygon"]
            pts = _points_to_svg_str(poly)
            idx = cell["index"]
            color = cell["color"]

            if self.style == "wireframe":
                parts.append(
                    '<polygon points="%s" fill="none" stroke="#ffffff" '
                    'stroke-width="1.5" opacity="0.8"/>' % pts
                )
            elif self.style == "stained-glass":
                parts.append(
                    '<polygon points="%s" fill="%s" fill-opacity="0.85" '
                    'stroke="#111111" stroke-width="3"/>' % (pts, color)
                )
                # Inner glow
                cx, cy = _polygon_centroid(poly)
                r = math.sqrt(_polygon_area(poly) / math.pi) * 0.4
                parts.append(
                    '<circle cx="%.1f" cy="%.1f" r="%.1f" '
                    'fill="white" opacity="0.12"/>' % (cx, cy, r)
                )
            elif self.style == "mosaic":
                # Slight color variation
                h = int(hashlib.md5(str(idx).encode()).hexdigest()[:2], 16)
                variation = (h - 128) / 512  # ±0.25 brightness
                parts.append(
                    '<polygon points="%s" fill="%s" fill-opacity="%.2f" '
                    'stroke="#333333" stroke-width="1"/>' % (
                        pts, color, 0.75 + variation * 0.2
                    )
                )
            elif self.style == "gradient":
                parts.append(
                    '<polygon points="%s" fill="url(#g%d)" '
                    'stroke="#222222" stroke-width="0.5"/>' % (pts, idx)
                )
            else:  # flat
                parts.append(
                    '<polygon points="%s" fill="%s" stroke="#222222" '
                    'stroke-width="0.5"/>' % (pts, color)
                )

    def save(self, filepath):
        """Save tile to file (SVG format)."""
        svg = self.render_svg()
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg)
        return filepath

    def save_preview(self, filepath, cols=3, rows=3):
        """Save a tiled preview showing the pattern repeating."""
        svg = self.render_svg(tile_repeat=(cols, rows))
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(svg)
        return filepath

    def stats(self):
        """Return statistics about the generated tile."""
        areas = [_polygon_area(c["polygon"]) for c in self.cells]
        return {
            "seeds": self.n_seeds,
            "cells": len(self.cells),
            "width": self.width,
            "height": self.height,
            "style": self.style,
            "palette": self.palette_name,
            "mean_area": sum(areas) / len(areas) if areas else 0,
            "min_area": min(areas) if areas else 0,
            "max_area": max(areas) if areas else 0,
            "coverage": sum(areas) / (self.width * self.height) if areas else 0,
        }


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate seamless tileable Voronoi patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python vormap_tile.py --seeds 40 -o tile.svg
  python vormap_tile.py --seeds 25 --style stained-glass --palette jewel -o glass.svg
  python vormap_tile.py --seeds 50 --style wireframe --tilepreview 4x4 -o preview.svg
  python vormap_tile.py --seeds 30 --style mosaic --palette earth -o mosaic.svg
  python vormap_tile.py --seeds 20 --style gradient --palette neon -o neon.svg
  python vormap_tile.py --stats --seeds 60
        """,
    )
    parser.add_argument("-o", "--output", default="tile.svg",
                        help="Output file path (default: tile.svg)")
    parser.add_argument("--seeds", type=int, default=30,
                        help="Number of seed points (default: 30)")
    parser.add_argument("--width", type=int, default=512,
                        help="Tile width in pixels (default: 512)")
    parser.add_argument("--height", type=int, default=512,
                        help="Tile height in pixels (default: 512)")
    parser.add_argument("--style", default="flat",
                        choices=["flat", "stained-glass", "wireframe", "mosaic", "gradient"],
                        help="Visual style (default: flat)")
    parser.add_argument("--palette", default="default",
                        choices=list(PALETTES.keys()),
                        help="Color palette (default: default)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--lloyd", type=int, default=2,
                        help="Lloyd relaxation iterations (default: 2)")
    parser.add_argument("--tilepreview", default=None,
                        help="Render RxC tile preview (e.g. 3x3)")
    parser.add_argument("--stats", action="store_true",
                        help="Print tile statistics")

    args = parser.parse_args()

    gen = TileGenerator(
        width=args.width,
        height=args.height,
        seeds=args.seeds,
        style=args.style,
        palette=args.palette,
        seed=args.seed,
        lloyd_iterations=args.lloyd,
    )

    if args.stats:
        s = gen.stats()
        print("Tile Statistics")
        print("=" * 40)
        for k, v in s.items():
            if isinstance(v, float):
                print("  %-14s %.2f" % (k, v))
            else:
                print("  %-14s %s" % (k, v))
        if not args.output or args.output == "tile.svg":
            return

    if args.tilepreview:
        try:
            cols, rows = map(int, args.tilepreview.lower().split("x"))
        except ValueError:
            print("Error: --tilepreview must be in RxC format (e.g. 3x3)")
            sys.exit(1)
        gen.save_preview(args.output, cols=cols, rows=rows)
        print("Saved %dx%d tile preview -> %s" % (cols, rows, args.output))
    else:
        gen.save(args.output)
        print("Saved tile -> %s" % args.output)

    if args.stats:
        s = gen.stats()
        print("\nTile Statistics")
        print("=" * 40)
        for k, v in s.items():
            if isinstance(v, float):
                print("  %-14s %.2f" % (k, v))
            else:
                print("  %-14s %s" % (k, v))


if __name__ == "__main__":
    main()
