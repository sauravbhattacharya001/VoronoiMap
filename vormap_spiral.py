"""Voronoi Spiral Pattern Generator.

Creates visually striking Voronoi diagrams from seeds arranged along
mathematical spiral curves. Useful for generative art, nature-inspired
patterns (sunflower heads, galaxy arms), and decorative tiling.

Spiral types:
- **fermat**      – Fermat spiral (r = sqrt(θ)), golden-angle spacing → sunflower pattern
- **archimedean** – Archimedean spiral (r = a·θ), constant spacing between arms
- **logarithmic** – Logarithmic spiral (r = a·e^{bθ}), self-similar growth (nautilus)
- **fibonacci**   – Points placed at Fibonacci-number angles along expanding radius

Features:
- Configurable seed count, turns, scale, and rotation
- Golden-angle mode for natural phyllotaxis patterns
- SVG and JSON export
- Colormap support (viridis, plasma, twilight, mono, golden)
- Optional Voronoi overlay rendering via ``vormap_generate``

CLI usage
---------
::

    python vormap_spiral.py fermat spiral.svg --seeds 300 --size 800
    python vormap_spiral.py archimedean spiral.json --seeds 200 --format json
    python vormap_spiral.py logarithmic spiral.svg --turns 5 --growth 0.15 --colormap plasma
    python vormap_spiral.py fibonacci spiral.svg --seeds 150 --voronoi --colormap twilight

"""

import argparse
import json
import math
import os
import sys

# Golden angle in radians ≈ 137.508°
GOLDEN_ANGLE = math.pi * (3 - math.sqrt(5))

# ── Colormaps ────────────────────────────────────────────────────────────

COLORMAPS = {
    "viridis": [
        "#440154", "#482777", "#3E4A89", "#31688E", "#26838E",
        "#1F9E89", "#6CCD5A", "#B6DE2B", "#FDE725",
    ],
    "plasma": [
        "#0D0887", "#4B03A1", "#7D03A8", "#A82296", "#CB4679",
        "#E8695B", "#F89441", "#FDC328", "#F0F921",
    ],
    "twilight": [
        "#E2D9E2", "#9E7AA0", "#5A4C82", "#2D1B4E", "#5A4C82",
        "#9E7AA0", "#E2D9E2",
    ],
    "mono": ["#222222", "#555555", "#888888", "#AAAAAA", "#DDDDDD"],
    "golden": ["#DAA520", "#FFD700", "#F0C040", "#B8860B", "#CD950C"],
}


def _color_at(cmap_name: str, t: float) -> str:
    """Sample a color from a named colormap at position *t* ∈ [0, 1]."""
    pal = COLORMAPS.get(cmap_name, COLORMAPS["viridis"])
    t = max(0.0, min(1.0, t))
    idx = t * (len(pal) - 1)
    return pal[min(int(round(idx)), len(pal) - 1)]


# ── Spiral generators ───────────────────────────────────────────────────


def fermat_spiral(n: int, scale: float = 1.0, rotation: float = 0.0):
    """Fermat spiral with golden-angle spacing (sunflower pattern).

    Each seed *k* is placed at angle ``k * golden_angle + rotation`` and
    radius ``scale * sqrt(k)``.
    """
    points = []
    for k in range(n):
        theta = k * GOLDEN_ANGLE + rotation
        r = scale * math.sqrt(k)
        points.append((r * math.cos(theta), r * math.sin(theta)))
    return points


def archimedean_spiral(n: int, turns: float = 5.0, scale: float = 1.0,
                       rotation: float = 0.0):
    """Archimedean spiral – constant radial separation between arms."""
    points = []
    max_theta = turns * 2 * math.pi
    for k in range(n):
        theta = (k / max(n - 1, 1)) * max_theta + rotation
        r = scale * theta / (2 * math.pi)
        points.append((r * math.cos(theta), r * math.sin(theta)))
    return points


def logarithmic_spiral(n: int, turns: float = 3.0, growth: float = 0.18,
                        scale: float = 1.0, rotation: float = 0.0):
    """Logarithmic (equiangular) spiral – self-similar growth."""
    points = []
    max_theta = turns * 2 * math.pi
    for k in range(n):
        theta = (k / max(n - 1, 1)) * max_theta + rotation
        r = scale * math.exp(growth * theta)
        points.append((r * math.cos(theta), r * math.sin(theta)))
    return points


def fibonacci_spiral(n: int, scale: float = 1.0, rotation: float = 0.0):
    """Seeds at Fibonacci-number angular positions along expanding radius."""
    points = []
    a, b = 0, 1
    for k in range(n):
        angle = (b % 360) * math.pi / 180.0 + rotation
        r = scale * math.sqrt(k + 1)
        points.append((r * math.cos(angle), r * math.sin(angle)))
        a, b = b, a + b
    return points


SPIRALS = {
    "fermat": fermat_spiral,
    "archimedean": archimedean_spiral,
    "logarithmic": logarithmic_spiral,
    "fibonacci": fibonacci_spiral,
}


# ── Normalise to canvas ─────────────────────────────────────────────────


def _normalise(points, size: int, margin: float = 0.05):
    """Scale and translate points to fit inside a *size* × *size* canvas."""
    if not points:
        return []
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x or 1.0
    span_y = max_y - min_y or 1.0
    m = size * margin
    usable = size - 2 * m
    scale = usable / max(span_x, span_y)
    cx = size / 2
    cy = size / 2
    mid_x = (min_x + max_x) / 2
    mid_y = (min_y + max_y) / 2
    return [
        (cx + (p[0] - mid_x) * scale, cy + (p[1] - mid_y) * scale)
        for p in points
    ]


# ── Voronoi overlay (optional) ──────────────────────────────────────────


def _voronoi_polygons(points, size):
    """Compute Voronoi polygons clipped to canvas using scipy if available."""
    try:
        from scipy.spatial import Voronoi  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return None

    pts = np.array(points)
    # Mirror points for bounded Voronoi
    mirrored = np.concatenate([
        pts,
        np.column_stack([2 * 0 - pts[:, 0], pts[:, 1]]),
        np.column_stack([2 * size - pts[:, 0], pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * 0 - pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * size - pts[:, 1]]),
    ])
    vor = Voronoi(mirrored)
    polygons = []
    for i in range(len(pts)):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]
        if -1 in region or not region:
            polygons.append(None)
            continue
        verts = [vor.vertices[v] for v in region]
        # Clip to canvas
        clipped = [(max(0, min(size, v[0])), max(0, min(size, v[1]))) for v in verts]
        polygons.append(clipped)
    return polygons


# ── SVG export ───────────────────────────────────────────────────────────


def to_svg(points, size: int = 800, colormap: str = "viridis",
           voronoi: bool = False, seed_radius: float = 3.0,
           title: str = "") -> str:
    """Render spiral seeds (and optional Voronoi cells) as SVG."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"'
        f' viewBox="0 0 {size} {size}">',
        f'<rect width="{size}" height="{size}" fill="#111119"/>',
    ]
    if title:
        parts.append(
            f'<text x="{size // 2}" y="30" text-anchor="middle" '
            f'fill="#cccccc" font-size="18" font-family="sans-serif">{title}</text>'
        )

    n = len(points)

    # Voronoi cells
    if voronoi:
        polys = _voronoi_polygons(points, size)
        if polys:
            for i, poly in enumerate(polys):
                if poly is None:
                    continue
                t = i / max(n - 1, 1)
                fill = _color_at(colormap, t)
                path = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in poly)
                parts.append(
                    f'<polygon points="{path}" fill="{fill}" '
                    f'fill-opacity="0.55" stroke="#ffffff" stroke-width="0.5"/>'
                )

    # Seed dots
    for i, (x, y) in enumerate(points):
        t = i / max(n - 1, 1)
        fill = _color_at(colormap, t)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{seed_radius}" '
            f'fill="{fill}" fill-opacity="0.9"/>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


# ── JSON export ──────────────────────────────────────────────────────────


def to_json(points, spiral_type: str, params: dict) -> str:
    """Export spiral seeds as JSON with metadata."""
    return json.dumps({
        "type": "voronoi_spiral",
        "spiral": spiral_type,
        "parameters": params,
        "seed_count": len(points),
        "seeds": [{"x": round(p[0], 2), "y": round(p[1], 2)} for p in points],
    }, indent=2)


# ── CLI ──────────────────────────────────────────────────────────────────


def _build_parser():
    p = argparse.ArgumentParser(
        description="Generate Voronoi diagrams from mathematical spiral seed patterns."
    )
    p.add_argument("spiral", choices=list(SPIRALS.keys()),
                   help="Spiral type")
    p.add_argument("output", help="Output file (.svg or .json)")
    p.add_argument("--seeds", type=int, default=200,
                   help="Number of seed points (default: 200)")
    p.add_argument("--size", type=int, default=800,
                   help="Canvas size in pixels (default: 800)")
    p.add_argument("--turns", type=float, default=5.0,
                   help="Number of spiral turns (archimedean/logarithmic)")
    p.add_argument("--growth", type=float, default=0.18,
                   help="Growth factor for logarithmic spiral")
    p.add_argument("--scale", type=float, default=1.0,
                   help="Scale multiplier")
    p.add_argument("--rotation", type=float, default=0.0,
                   help="Initial rotation in degrees")
    p.add_argument("--colormap", default="viridis",
                   choices=list(COLORMAPS.keys()),
                   help="Color palette (default: viridis)")
    p.add_argument("--voronoi", action="store_true",
                   help="Overlay Voronoi cell polygons (requires scipy)")
    p.add_argument("--format", choices=["svg", "json"], default=None,
                   help="Force output format (auto-detected from extension)")
    p.add_argument("--title", default="", help="Optional SVG title text")
    p.add_argument("--seeds-out", default=None,
                   help="Also write seed coordinates to a CSV file")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)

    rotation_rad = args.rotation * math.pi / 180.0

    kwargs = {"n": args.seeds, "scale": args.scale, "rotation": rotation_rad}
    if args.spiral in ("archimedean", "logarithmic"):
        kwargs["turns"] = args.turns
    if args.spiral == "logarithmic":
        kwargs["growth"] = args.growth

    raw_pts = SPIRALS[args.spiral](**kwargs)
    points = _normalise(raw_pts, args.size)

    # Determine format
    fmt = args.format
    if fmt is None:
        ext = os.path.splitext(args.output)[1].lower()
        fmt = "json" if ext == ".json" else "svg"

    if fmt == "json":
        content = to_json(points, args.spiral, {
            "seeds": args.seeds, "turns": args.turns,
            "growth": args.growth, "scale": args.scale,
            "rotation": args.rotation,
        })
    else:
        content = to_svg(points, args.size, args.colormap,
                         voronoi=args.voronoi, title=args.title)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✓ {args.spiral} spiral ({args.seeds} seeds) → {args.output}")

    # Optional CSV seed export
    if args.seeds_out:
        with open(args.seeds_out, "w", encoding="utf-8") as f:
            f.write("x,y\n")
            for x, y in points:
                f.write(f"{x:.2f},{y:.2f}\n")
        print(f"  Seeds → {args.seeds_out}")


if __name__ == "__main__":
    main()
