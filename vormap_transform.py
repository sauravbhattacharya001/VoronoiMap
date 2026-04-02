"""Voronoi geometric transformations — rotate, scale, mirror, shear, translate point sets.

Apply affine transformations to Voronoi seed points and visualise
before/after tessellations side-by-side in SVG.  Transformations can be
chained in the order specified on the command line.

Supported transforms:
    - **rotate**    — rotate by angle (degrees) around centroid or custom pivot
    - **scale**     — uniform or anisotropic scaling around centroid
    - **mirror**    — reflect across X axis, Y axis, or arbitrary angle
    - **shear**     — horizontal or vertical shear
    - **translate** — shift by (dx, dy)
    - **jitter**    — random displacement within a radius (for perturbation studies)

Typical usage::

    import vormap
    from vormap_transform import transform_points, chain_transforms

    data = vormap.load_data("points.txt")
    pts  = [(r[0], r[1]) for r in data]
    out  = chain_transforms(pts, [("rotate", {"angle_deg": 45}),
                                   ("scale",  {"sx": 1.5, "sy": 0.8})])

CLI::

    python vormap_transform.py points.txt --rotate 45
    python vormap_transform.py points.txt --scale 2.0
    python vormap_transform.py points.txt --mirror y
    python vormap_transform.py points.txt --shear-x 0.3
    python vormap_transform.py points.txt --translate 100 -50
    python vormap_transform.py points.txt --jitter 5.0
    python vormap_transform.py points.txt --rotate 30 --scale 1.2 --svg out.svg
    python vormap_transform.py points.txt --rotate 90 --output rotated.txt
"""

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import vormap

# ── Types ───────────────────────────────────────────────────────────

Point = Tuple[float, float]


@dataclass
class TransformResult:
    """Outcome of a transformation pipeline."""
    original: List[Point]
    transformed: List[Point]
    transforms_applied: List[str]
    centroid_before: Point
    centroid_after: Point
    bounding_box_before: Tuple[float, float, float, float]  # minx, miny, maxx, maxy
    bounding_box_after: Tuple[float, float, float, float]


# ── Helpers ─────────────────────────────────────────────────────────

def _centroid(pts: List[Point]) -> Point:
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _bbox(pts: List[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


# ── Individual transforms ──────────────────────────────────────────

def rotate(pts: List[Point], angle_deg: float, pivot: Optional[Point] = None) -> List[Point]:
    """Rotate points by *angle_deg* degrees around *pivot* (default: centroid)."""
    if pivot is None:
        pivot = _centroid(pts)
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    out = []
    for x, y in pts:
        dx, dy = x - pivot[0], y - pivot[1]
        out.append((pivot[0] + dx * cos_a - dy * sin_a,
                     pivot[1] + dx * sin_a + dy * cos_a))
    return out


def scale(pts: List[Point], sx: float = 1.0, sy: Optional[float] = None,
          center: Optional[Point] = None) -> List[Point]:
    """Scale points by (sx, sy) around *center* (default: centroid)."""
    if sy is None:
        sy = sx
    if center is None:
        center = _centroid(pts)
    out = []
    for x, y in pts:
        out.append((center[0] + (x - center[0]) * sx,
                     center[1] + (y - center[1]) * sy))
    return out


def mirror(pts: List[Point], axis: str = "y", angle_deg: Optional[float] = None,
           center: Optional[Point] = None) -> List[Point]:
    """Reflect points across an axis through *center*."""
    if center is None:
        center = _centroid(pts)
    if axis == "x":
        return [(x, 2 * center[1] - y) for x, y in pts]
    elif axis == "y":
        return [(2 * center[0] - x, y) for x, y in pts]
    elif axis == "angle" and angle_deg is not None:
        rad = math.radians(angle_deg)
        cos2 = math.cos(2 * rad)
        sin2 = math.sin(2 * rad)
        out = []
        for x, y in pts:
            dx, dy = x - center[0], y - center[1]
            out.append((center[0] + dx * cos2 + dy * sin2,
                         center[1] + dx * sin2 - dy * cos2))
        return out
    else:
        raise ValueError(f"Unknown mirror axis: {axis!r}")


def shear(pts: List[Point], shx: float = 0.0, shy: float = 0.0,
          center: Optional[Point] = None) -> List[Point]:
    """Apply shear transformation."""
    if center is None:
        center = _centroid(pts)
    out = []
    for x, y in pts:
        dx, dy = x - center[0], y - center[1]
        out.append((center[0] + dx + shx * dy,
                     center[1] + shy * dx + dy))
    return out


def translate(pts: List[Point], dx: float = 0.0, dy: float = 0.0) -> List[Point]:
    """Shift all points by (dx, dy)."""
    return [(x + dx, y + dy) for x, y in pts]


def jitter(pts: List[Point], radius: float = 1.0, seed: Optional[int] = None) -> List[Point]:
    """Add random displacement within *radius* to each point."""
    rng = random.Random(seed)
    out = []
    for x, y in pts:
        angle = rng.uniform(0, 2 * math.pi)
        r = rng.uniform(0, radius)
        out.append((x + r * math.cos(angle), y + r * math.sin(angle)))
    return out


# ── Pipeline ────────────────────────────────────────────────────────

_REGISTRY = {
    "rotate": rotate,
    "scale": scale,
    "mirror": mirror,
    "shear": shear,
    "translate": translate,
    "jitter": jitter,
}


def chain_transforms(pts: List[Point],
                     steps: Sequence[Tuple[str, Dict]]) -> TransformResult:
    """Apply a sequence of named transforms and return a *TransformResult*."""
    original = list(pts)
    current = list(pts)
    names = []
    for name, kwargs in steps:
        fn = _REGISTRY.get(name)
        if fn is None:
            raise ValueError(f"Unknown transform: {name!r}")
        current = fn(current, **kwargs)
        names.append(name)
    return TransformResult(
        original=original,
        transformed=current,
        transforms_applied=names,
        centroid_before=_centroid(original),
        centroid_after=_centroid(current),
        bounding_box_before=_bbox(original),
        bounding_box_after=_bbox(current),
    )


# ── SVG visualisation ──────────────────────────────────────────────

def _vor_edges_naive(pts: List[Point], bbox: Tuple[float, float, float, float]):
    """Compute Voronoi edges via scipy if available, else return empty."""
    try:
        from scipy.spatial import Voronoi
        import numpy as np
        if len(pts) < 4:
            return []
        vor = Voronoi(np.array(pts))
        edges = []
        for simplex in vor.ridge_vertices:
            if -1 not in simplex:
                p1 = vor.vertices[simplex[0]]
                p2 = vor.vertices[simplex[1]]
                edges.append(((p1[0], p1[1]), (p2[0], p2[1])))
        return edges
    except ImportError:
        return []


def render_svg(result: TransformResult, width: int = 1200, height: int = 500,
               show_voronoi: bool = True) -> str:
    """Render side-by-side before/after SVG."""
    pad = 30
    half_w = (width - 3 * pad) // 2

    def _fit(pts, w, h, pad_inner=20):
        if not pts:
            return [], 1.0, 0.0, 0.0
        bb = _bbox(pts)
        dx = bb[2] - bb[0] or 1.0
        dy = bb[3] - bb[1] or 1.0
        sc = min((w - 2 * pad_inner) / dx, (h - 2 * pad_inner) / dy)
        ox = pad_inner - bb[0] * sc + ((w - 2 * pad_inner) - dx * sc) / 2
        oy = pad_inner - bb[1] * sc + ((h - 2 * pad_inner) - dy * sc) / 2
        fitted = [(x * sc + ox, y * sc + oy) for x, y in pts]
        return fitted, sc, ox, oy

    svg_h = height
    orig_fitted, s1, ox1, oy1 = _fit(result.original, half_w, svg_h - 60)
    trans_fitted, s2, ox2, oy2 = _fit(result.transformed, half_w, svg_h - 60)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{svg_h}" '
        f'style="background:#fafafa;font-family:sans-serif">',
        f'<text x="{pad + half_w // 2}" y="22" text-anchor="middle" font-size="14" '
        f'font-weight="bold" fill="#333">Original</text>',
        f'<text x="{2 * pad + half_w + half_w // 2}" y="22" text-anchor="middle" '
        f'font-size="14" font-weight="bold" fill="#333">Transformed '
        f'({", ".join(result.transforms_applied)})</text>',
    ]

    for panel_x, fitted, pts_raw, sc, ox, oy, color in [
        (pad, orig_fitted, result.original, s1, ox1, oy1, "#4a90d9"),
        (2 * pad + half_w, trans_fitted, result.transformed, s2, ox2, oy2, "#d94a4a"),
    ]:
        lines.append(f'<g transform="translate({panel_x},30)">')
        lines.append(f'<rect width="{half_w}" height="{svg_h - 40}" rx="6" '
                     f'fill="white" stroke="#ddd"/>')

        if show_voronoi:
            edges = _vor_edges_naive(pts_raw, _bbox(pts_raw) if pts_raw else (0, 0, 1, 1))
            for (x1, y1), (x2, y2) in edges:
                lx1, ly1 = x1 * sc + ox, y1 * sc + oy
                lx2, ly2 = x2 * sc + ox, y2 * sc + oy
                lines.append(f'<line x1="{lx1:.1f}" y1="{ly1:.1f}" '
                             f'x2="{lx2:.1f}" y2="{ly2:.1f}" '
                             f'stroke="#e0e0e0" stroke-width="0.8"/>')

        for fx, fy in fitted:
            lines.append(f'<circle cx="{fx:.1f}" cy="{fy:.1f}" r="3" fill="{color}" '
                         f'opacity="0.7"/>')
        lines.append("</g>")

    arrow_y = svg_h // 2
    ax = pad + half_w + pad // 2
    lines.append(f'<polygon points="{ax - 6},{arrow_y - 5} {ax + 6},{arrow_y} '
                 f'{ax - 6},{arrow_y + 5}" fill="#999"/>')

    lines.append("</svg>")
    return "\n".join(lines)


# ── Text output ─────────────────────────────────────────────────────

def save_points(pts: List[Point], path: str) -> None:
    """Write points to a text file (one per line, space-separated)."""
    safe = vormap._validate_path(path, allow_absolute=True, label="Output file")
    with open(safe, "w") as f:
        for x, y in pts:
            f.write(f"{x} {y}\n")


def format_report(result: TransformResult) -> str:
    """Human-readable summary of the transformation."""
    lines = [
        "═══ Voronoi Transform Report ═══",
        f"Points:       {len(result.original)}",
        f"Transforms:   {' → '.join(result.transforms_applied) or 'none'}",
        "",
        f"Centroid before: ({result.centroid_before[0]:.2f}, {result.centroid_before[1]:.2f})",
        f"Centroid after:  ({result.centroid_after[0]:.2f}, {result.centroid_after[1]:.2f})",
        "",
        "Bounding box before: "
        f"[{result.bounding_box_before[0]:.2f}, {result.bounding_box_before[1]:.2f}] → "
        f"[{result.bounding_box_before[2]:.2f}, {result.bounding_box_before[3]:.2f}]",
        "Bounding box after:  "
        f"[{result.bounding_box_after[0]:.2f}, {result.bounding_box_after[1]:.2f}] → "
        f"[{result.bounding_box_after[2]:.2f}, {result.bounding_box_after[3]:.2f}]",
        "",
        f"Displacement (centroid shift): "
        f"{math.dist(result.centroid_before, result.centroid_after):.4f}",
    ]
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Apply geometric transformations to Voronoi point sets.",
        epilog="Transforms are applied in the order specified on the command line.",
    )
    p.add_argument("input", help="Input data file (x y per line, CSV, or GeoJSON)")
    p.add_argument("--rotate", type=float, metavar="DEG",
                   help="Rotate by DEG degrees around centroid")
    p.add_argument("--scale", type=float, metavar="FACTOR",
                   help="Uniform scale by FACTOR around centroid")
    p.add_argument("--scale-xy", nargs=2, type=float, metavar=("SX", "SY"),
                   help="Anisotropic scale by SX, SY")
    p.add_argument("--mirror", choices=["x", "y"],
                   help="Mirror across axis through centroid")
    p.add_argument("--mirror-angle", type=float, metavar="DEG",
                   help="Mirror across line at DEG degrees through centroid")
    p.add_argument("--shear-x", type=float, metavar="SHX",
                   help="Horizontal shear factor")
    p.add_argument("--shear-y", type=float, metavar="SHY",
                   help="Vertical shear factor")
    p.add_argument("--translate", nargs=2, type=float, metavar=("DX", "DY"),
                   help="Translate by (DX, DY)")
    p.add_argument("--jitter", type=float, metavar="RADIUS",
                   help="Random jitter within RADIUS")
    p.add_argument("--seed", type=int, help="Random seed for jitter")
    p.add_argument("--output", "-o", metavar="FILE",
                   help="Write transformed points to FILE")
    p.add_argument("--svg", metavar="FILE",
                   help="Write before/after SVG to FILE")
    p.add_argument("--json", metavar="FILE",
                   help="Write result summary as JSON")
    p.add_argument("--no-voronoi", action="store_true",
                   help="Omit Voronoi edges from SVG (faster for large sets)")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    data = vormap.load_data(args.input)
    pts: List[Point] = [(row[0], row[1]) for row in data]

    if len(pts) < 2:
        print("Need at least 2 points.", file=sys.stderr)
        sys.exit(1)

    steps: List[Tuple[str, Dict]] = []

    if args.rotate is not None:
        steps.append(("rotate", {"angle_deg": args.rotate}))
    if args.scale is not None:
        steps.append(("scale", {"sx": args.scale}))
    if args.scale_xy is not None:
        steps.append(("scale", {"sx": args.scale_xy[0], "sy": args.scale_xy[1]}))
    if args.mirror is not None:
        steps.append(("mirror", {"axis": args.mirror}))
    if args.mirror_angle is not None:
        steps.append(("mirror", {"axis": "angle", "angle_deg": args.mirror_angle}))
    if args.shear_x is not None:
        steps.append(("shear", {"shx": args.shear_x}))
    if args.shear_y is not None:
        steps.append(("shear", {"shy": args.shear_y}))
    if args.translate is not None:
        steps.append(("translate", {"dx": args.translate[0], "dy": args.translate[1]}))
    if args.jitter is not None:
        steps.append(("jitter", {"radius": args.jitter, "seed": args.seed}))

    if not steps:
        print("No transforms specified. Use --rotate, --scale, --mirror, etc.",
              file=sys.stderr)
        sys.exit(1)

    result = chain_transforms(pts, steps)
    print(format_report(result))

    if args.output:
        save_points(result.transformed, args.output)
        print(f"\nTransformed points written to {args.output}")

    if args.svg:
        svg = render_svg(result, show_voronoi=not args.no_voronoi)
        safe = vormap._validate_path(args.svg, allow_absolute=True, label="SVG file")
        with open(safe, "w") as f:
            f.write(svg)
        print(f"SVG written to {args.svg}")

    if args.json:
        obj = {
            "num_points": len(pts),
            "transforms": result.transforms_applied,
            "centroid_before": list(result.centroid_before),
            "centroid_after": list(result.centroid_after),
            "bbox_before": list(result.bounding_box_before),
            "bbox_after": list(result.bounding_box_after),
            "centroid_displacement": math.dist(result.centroid_before, result.centroid_after),
        }
        safe = vormap._validate_path(args.json, allow_absolute=True, label="JSON file")
        with open(safe, "w") as f:
            json.dump(obj, f, indent=2)
        print(f"JSON written to {args.json}")


if __name__ == "__main__":
    main()
