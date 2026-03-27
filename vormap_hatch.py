"""Voronoi Hatch Pattern Generator — pen-plotter / engraving style output.

Fills Voronoi cells with line-based hatch patterns to produce
technical illustration, engraving, or pen-plotter style graphics.
Each cell's hatch density can encode a data value, creating
thematic hatch maps.

Hatch styles:
    - ``lines``:     Parallel lines at a given angle
    - ``cross``:     Two sets of parallel lines (crosshatch)
    - ``dots``:      Dot/stipple fill (circle packing)
    - ``zigzag``:    Zigzag strokes
    - ``contour``:   Concentric offset outlines
    - ``random``:    Random short strokes (sketch style)

Output formats:
    - SVG (default) — clean vector, plotter-ready
    - JSON — structured hatch geometry data

CLI usage
---------
::

    python vormap_hatch.py points.txt --style cross --output hatch.svg
    python vormap_hatch.py points.txt --style lines --angle 45 --density 0.5
    python vormap_hatch.py points.txt --style dots --spacing 6 --output dots.svg
    python vormap_hatch.py points.txt --style zigzag --values values.txt
    python vormap_hatch.py points.txt --style contour --line-width 0.5 --format json

"""

import argparse
import json
import math
import os
import random as _random
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from vormap_utils import polygon_centroid_mean as _polygon_centroid

# ── Core Voronoi computation (pure-Python fallback) ──

try:
    from scipy.spatial import Voronoi as ScipyVoronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _line_segment_clip(x1, y1, x2, y2, polygon):
    """Clip a line segment to a convex polygon (Cyrus-Beck algorithm).

    Determines the inward normal direction automatically from the polygon
    centroid so it works regardless of winding order.
    """
    dx, dy = x2 - x1, y2 - y1
    t_enter, t_exit = 0.0, 1.0
    # Compute centroid once to determine inward direction
    n = len(polygon)
    ccx = sum(p[0] for p in polygon) / n
    ccy = sum(p[1] for p in polygon) / n

    for i in range(n):
        ex, ey = polygon[i]
        fx, fy = polygon[(i + 1) % n]
        # Candidate normal perpendicular to edge
        enx, eny = -(fy - ey), fx - ex
        # Check it points inward (toward centroid)
        mx, my = (ex + fx) / 2, (ey + fy) / 2
        if enx * (ccx - mx) + eny * (ccy - my) < 0:
            enx, eny = -enx, -eny
        denom = enx * dx + eny * dy
        num = enx * (x1 - ex) + eny * (y1 - ey)
        if abs(denom) < 1e-12:
            if num < 0:
                return None
        else:
            t = -num / denom
            if denom > 0:
                t_enter = max(t_enter, t)
            else:
                t_exit = min(t_exit, t)
        if t_enter > t_exit:
            return None
    if t_enter > t_exit:
        return None
    cx1 = x1 + t_enter * dx
    cy1 = y1 + t_enter * dy
    cx2 = x1 + t_exit * dx
    cy2 = y1 + t_exit * dy
    return (cx1, cy1, cx2, cy2)


## _polygon_centroid imported from vormap_utils


def _polygon_bbox(poly):
    # Thin wrapper — same as vormap_utils.bounding_box
    from vormap_utils import bounding_box
    return bounding_box(poly)


def _point_in_polygon(px, py, poly):
    """Ray-casting point-in-polygon test."""
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _ensure_ccw(poly):
    """Ensure polygon vertices are counter-clockwise."""
    area2 = 0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        area2 += (x2 - x1) * (y2 + y1)
    if area2 > 0:
        poly = list(reversed(poly))
    return poly


# ---------------------------------------------------------------------------
# Voronoi cell computation
# ---------------------------------------------------------------------------

def _compute_cells(points, bounds):
    """Compute clipped Voronoi cells. Returns list of (centroid_idx, polygon)."""
    xmin, ymin, xmax, ymax = bounds
    clip_poly = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]

    if _HAS_SCIPY and len(points) >= 4:
        return _scipy_cells(points, clip_poly, bounds)
    return _brute_cells(points, clip_poly, bounds)


def _scipy_cells(points, clip_poly, bounds):
    """Use scipy.spatial.Voronoi with clipping."""
    import numpy as np
    xmin, ymin, xmax, ymax = bounds
    w, h = xmax - xmin, ymax - ymin
    # Mirror points for bounded diagram
    pts = np.array(points)
    mirrored = np.concatenate([
        pts,
        np.column_stack([2 * xmin - pts[:, 0], pts[:, 1]]),
        np.column_stack([2 * xmax - pts[:, 0], pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * ymin - pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * ymax - pts[:, 1]]),
    ])
    vor = ScipyVoronoi(mirrored)
    n = len(points)
    cells = []
    for i in range(n):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]
        if -1 in region or len(region) == 0:
            continue
        poly = [tuple(vor.vertices[v]) for v in region]
        poly = _clip_polygon_to_rect(poly, bounds)
        if len(poly) >= 3:
            poly = _ensure_ccw(poly)
            cells.append((i, poly))
    return cells


def _clip_polygon_to_rect(poly, bounds):
    """Sutherland-Hodgman clip polygon to rectangle."""
    xmin, ymin, xmax, ymax = bounds

    def clip_edge(poly, edge_fn, inside_fn):
        if not poly:
            return []
        out = []
        n = len(poly)
        for i in range(n):
            curr = poly[i]
            prev = poly[i - 1]
            c_in = inside_fn(curr)
            p_in = inside_fn(prev)
            if c_in:
                if not p_in:
                    out.append(edge_fn(prev, curr))
                out.append(curr)
            elif p_in:
                out.append(edge_fn(prev, curr))
        return out

    def isect(p1, p2, val, axis):
        x1, y1 = p1
        x2, y2 = p2
        if axis == 0:
            if abs(x2 - x1) < 1e-12:
                return (val, y1)
            t = (val - x1) / (x2 - x1)
            return (val, y1 + t * (y2 - y1))
        else:
            if abs(y2 - y1) < 1e-12:
                return (x1, val)
            t = (val - y1) / (y2 - y1)
            return (x1 + t * (x2 - x1), val)

    poly = clip_edge(poly, lambda a, b: isect(a, b, xmin, 0), lambda p: p[0] >= xmin)
    poly = clip_edge(poly, lambda a, b: isect(a, b, xmax, 0), lambda p: p[0] <= xmax)
    poly = clip_edge(poly, lambda a, b: isect(a, b, ymin, 1), lambda p: p[1] >= ymin)
    poly = clip_edge(poly, lambda a, b: isect(a, b, ymax, 1), lambda p: p[1] <= ymax)
    return poly


def _brute_cells(points, clip_poly, bounds):
    """Brute-force Voronoi via half-plane intersection (for small n)."""
    xmin, ymin, xmax, ymax = bounds
    n = len(points)
    cells = []
    for i in range(n):
        poly = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        for j in range(n):
            if i == j:
                continue
            poly = _clip_half_plane(poly, points[i], points[j])
            if len(poly) < 3:
                break
        if len(poly) >= 3:
            poly = _ensure_ccw(poly)
            cells.append((i, poly))
    return cells


def _clip_half_plane(poly, pi, pj):
    """Clip polygon to half-plane closer to pi than pj."""
    mx = (pi[0] + pj[0]) / 2
    my = (pi[1] + pj[1]) / 2
    nx = pj[0] - pi[0]
    ny = pj[1] - pi[1]

    out = []
    n = len(poly)
    for k in range(n):
        curr = poly[k]
        prev = poly[k - 1]
        dc = nx * (curr[0] - mx) + ny * (curr[1] - my)
        dp = nx * (prev[0] - mx) + ny * (prev[1] - my)
        if dc <= 0:
            if dp > 0:
                out.append(_intersect_edge(prev, curr, mx, my, nx, ny))
            out.append(curr)
        elif dp <= 0:
            out.append(_intersect_edge(prev, curr, mx, my, nx, ny))
    return out


def _intersect_edge(p1, p2, mx, my, nx, ny):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    denom = nx * dx + ny * dy
    if abs(denom) < 1e-12:
        return p1
    t = (nx * (mx - p1[0]) + ny * (my - p1[1])) / denom
    return (p1[0] + t * dx, p1[1] + t * dy)


# ---------------------------------------------------------------------------
# Hatch pattern generators
# ---------------------------------------------------------------------------

def _hatch_lines(poly, spacing, angle_deg, _rng=None):
    """Generate parallel hatch lines clipped to polygon."""
    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    bx1, by1, bx2, by2 = _polygon_bbox(poly)
    # Project polygon onto perpendicular axis
    diag = math.hypot(bx2 - bx1, by2 - by1)
    cx, cy = (bx1 + bx2) / 2, (by1 + by2) / 2
    segments = []
    n_lines = int(diag / spacing) + 2
    for i in range(-n_lines, n_lines + 1):
        offset = i * spacing
        # Line through center + offset along perpendicular
        px = cx + offset * (-sin_a)
        py = cy + offset * cos_a
        # Line extends along angle direction
        x1 = px - diag * cos_a
        y1 = py - diag * sin_a
        x2 = px + diag * cos_a
        y2 = py + diag * sin_a
        seg = _line_segment_clip(x1, y1, x2, y2, poly)
        if seg:
            segments.append(seg)
    return segments


def _hatch_cross(poly, spacing, angle_deg, _rng=None):
    """Crosshatch — two sets of parallel lines at 90°."""
    s1 = _hatch_lines(poly, spacing, angle_deg)
    s2 = _hatch_lines(poly, spacing, angle_deg + 90)
    return s1 + s2


def _hatch_dots(poly, spacing, _angle_deg=0, _rng=None):
    """Dot/stipple fill — regular grid of dots inside polygon."""
    bx1, by1, bx2, by2 = _polygon_bbox(poly)
    dots = []
    y = by1 + spacing / 2
    while y < by2:
        x = bx1 + spacing / 2
        while x < bx2:
            if _point_in_polygon(x, y, poly):
                dots.append((x, y))
            x += spacing
        y += spacing
    return dots


def _hatch_zigzag(poly, spacing, angle_deg, _rng=None):
    """Zigzag strokes clipped to polygon."""
    lines = _hatch_lines(poly, spacing, angle_deg)
    segments = []
    for i, seg in enumerate(lines):
        if i % 2 == 0:
            segments.append(seg)
        else:
            # Reverse direction for zigzag
            segments.append((seg[2], seg[3], seg[0], seg[1]))
    # Connect consecutive endpoints
    zag_segs = []
    for i in range(len(segments)):
        zag_segs.append(segments[i])
        if i < len(segments) - 1:
            zag_segs.append((segments[i][2], segments[i][3],
                             segments[i + 1][0], segments[i + 1][1]))
    return zag_segs


def _hatch_contour(poly, spacing, _angle_deg=0, _rng=None):
    """Concentric inset outlines of the polygon."""
    contours = []
    current = list(poly)
    for _ in range(50):  # safety limit
        if len(current) < 3:
            break
        contours.append(list(current))
        # Inset by spacing
        current = _inset_polygon(current, spacing)
        if not current or len(current) < 3:
            break
    return contours


def _inset_polygon(poly, dist):
    """Shrink polygon inward by dist (simple offset)."""
    n = len(poly)
    if n < 3:
        return []
    # Determine winding: negative signed-area = CCW
    sa = 0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        sa += x1 * y2 - x2 * y1
    # sa > 0 means CCW in standard math coords
    sign = 1 if sa > 0 else -1  # flip normal if CW

    edges = []
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 1e-10:
            continue
        # Inward normal: for CCW, inward is (-dy, dx) normalized * sign
        nx = sign * (-dy / length)
        ny = sign * (dx / length)
        edges.append((x1 + nx * dist, y1 + ny * dist,
                       x2 + nx * dist, y2 + ny * dist))
    if len(edges) < 3:
        return []
    # Intersect consecutive offset edges
    new_poly = []
    m = len(edges)
    for i in range(m):
        ax1, ay1, ax2, ay2 = edges[i]
        bx1, by1, bx2, by2 = edges[(i + 1) % m]
        pt = _line_line_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2)
        if pt:
            new_poly.append(pt)
    if len(new_poly) < 3:
        return []
    # Validate — inset area should have same sign but smaller magnitude
    new_sa = 0
    for i in range(len(new_poly)):
        x1, y1 = new_poly[i]
        x2, y2 = new_poly[(i + 1) % len(new_poly)]
        new_sa += x1 * y2 - x2 * y1
    if sa * new_sa <= 0:  # Sign flipped = collapsed
        return []
    if abs(new_sa) > abs(sa):  # Grew instead of shrunk = collapsed
        return []
    return new_poly


def _line_line_intersect(ax1, ay1, ax2, ay2, bx1, by1, bx2, by2):
    dx1, dy1 = ax2 - ax1, ay2 - ay1
    dx2, dy2 = bx2 - bx1, by2 - by1
    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None
    t = ((bx1 - ax1) * dy2 - (by1 - ay1) * dx2) / denom
    return (ax1 + t * dx1, ay1 + t * dy1)


def _hatch_random(poly, spacing, _angle_deg=0, _rng=None):
    """Random short strokes (sketch style)."""
    rng = _rng or _random.Random(42)
    bx1, by1, bx2, by2 = _polygon_bbox(poly)
    stroke_len = spacing * 1.5
    segments = []
    # Density inversely proportional to spacing
    area = abs(bx2 - bx1) * abs(by2 - by1)
    count = int(area / (spacing * spacing * 2))
    for _ in range(count):
        cx = rng.uniform(bx1, bx2)
        cy = rng.uniform(by1, by2)
        if not _point_in_polygon(cx, cy, poly):
            continue
        angle = rng.uniform(0, math.pi)
        dx = stroke_len / 2 * math.cos(angle)
        dy = stroke_len / 2 * math.sin(angle)
        seg = _line_segment_clip(cx - dx, cy - dy, cx + dx, cy + dy, poly)
        if seg:
            segments.append(seg)
    return segments


HATCH_STYLES = {
    "lines": _hatch_lines,
    "cross": _hatch_cross,
    "dots": _hatch_dots,
    "zigzag": _hatch_zigzag,
    "contour": _hatch_contour,
    "random": _hatch_random,
}


# ---------------------------------------------------------------------------
# Main hatch generation
# ---------------------------------------------------------------------------

def generate_hatch(points, style="lines", spacing=8.0, angle=45.0,
                   values=None, bounds=None, line_width=0.5,
                   border=True, border_width=0.3, seed=42):
    """Generate hatched Voronoi diagram.

    Parameters
    ----------
    points : list of (x, y)
        Seed points.
    style : str
        Hatch style name (lines/cross/dots/zigzag/contour/random).
    spacing : float
        Base spacing between hatch lines/dots.
    angle : float
        Angle in degrees for directional hatch patterns.
    values : list of float or None
        Per-cell values [0, 1] controlling hatch density.
        Higher value = denser hatching (smaller spacing).
    bounds : tuple or None
        (xmin, ymin, xmax, ymax). Auto-detected if None.
    line_width : float
        SVG stroke width.
    border : bool
        Draw cell borders.
    border_width : float
        Border stroke width.
    seed : int
        Random seed for random style.

    Returns
    -------
    dict
        ``{'cells': [...], 'bounds': (xmin, ymin, xmax, ymax)}``
        Each cell: ``{'index': i, 'polygon': [...], 'segments': [...],
        'dots': [...], 'contours': [...]}``
    """
    if not points:
        raise ValueError("No points provided")
    if style not in HATCH_STYLES:
        raise ValueError(f"Unknown style '{style}'. Choose from: {', '.join(HATCH_STYLES)}")
    if spacing <= 0:
        raise ValueError("Spacing must be positive")

    if bounds is None:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        margin = spacing * 2
        bounds = (min(xs) - margin, min(ys) - margin,
                  max(xs) + margin, max(ys) + margin)

    cells_raw = _compute_cells(points, bounds)
    rng = _random.Random(seed)
    hatch_fn = HATCH_STYLES[style]

    result_cells = []
    for idx, poly in cells_raw:
        # Determine spacing for this cell
        cell_spacing = spacing
        if values and idx < len(values):
            v = max(0.05, min(1.0, values[idx]))
            cell_spacing = spacing / v  # Higher value → denser

        cell_data = {
            "index": idx,
            "polygon": poly,
            "segments": [],
            "dots": [],
            "contours": [],
        }

        if style == "dots":
            cell_data["dots"] = _hatch_dots(poly, cell_spacing)
        elif style == "contour":
            cell_data["contours"] = _hatch_contour(poly, cell_spacing)
        else:
            cell_data["segments"] = hatch_fn(poly, cell_spacing, angle, rng)

        result_cells.append(cell_data)

    return {"cells": result_cells, "bounds": bounds,
            "style": style, "line_width": line_width,
            "border": border, "border_width": border_width}


# ---------------------------------------------------------------------------
# SVG export
# ---------------------------------------------------------------------------

def to_svg(hatch_data, width=800, height=600):
    """Render hatch data to SVG string."""
    bounds = hatch_data["bounds"]
    bx1, by1, bx2, by2 = bounds
    bw, bh = bx2 - bx1, by2 - by1
    lw = hatch_data["line_width"]
    blw = hatch_data["border_width"]

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="{bx1} {by1} {bw} {bh}">',
        f'<rect x="{bx1}" y="{by1}" width="{bw}" height="{bh}" fill="white"/>',
    ]

    for cell in hatch_data["cells"]:
        poly = cell["polygon"]
        # Cell border
        if hatch_data["border"] and len(poly) >= 3:
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in poly)
            lines.append(
                f'<polygon points="{pts}" fill="none" '
                f'stroke="#999" stroke-width="{blw}"/>'
            )

        # Hatch lines / zigzag / random
        for seg in cell["segments"]:
            x1, y1, x2, y2 = seg
            lines.append(
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" '
                f'x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="black" stroke-width="{lw}"/>'
            )

        # Dots
        r = lw * 1.5
        for dx, dy in cell["dots"]:
            lines.append(
                f'<circle cx="{dx:.2f}" cy="{dy:.2f}" r="{r}" fill="black"/>'
            )

        # Contours
        for contour in cell["contours"]:
            if len(contour) >= 3:
                pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in contour)
                lines.append(
                    f'<polygon points="{pts}" fill="none" '
                    f'stroke="black" stroke-width="{lw}"/>'
                )

    lines.append("</svg>")
    return "\n".join(lines)


def to_json(hatch_data):
    """Export hatch data as JSON (geometry coordinates)."""
    out = {
        "style": hatch_data["style"],
        "bounds": list(hatch_data["bounds"]),
        "cells": [],
    }
    for cell in hatch_data["cells"]:
        c = {
            "index": cell["index"],
            "polygon": [list(p) for p in cell["polygon"]],
        }
        if cell["segments"]:
            c["segments"] = [list(s) for s in cell["segments"]]
        if cell["dots"]:
            c["dots"] = [list(d) for d in cell["dots"]]
        if cell["contours"]:
            c["contours"] = [[list(p) for p in ring] for ring in cell["contours"]]
        out["cells"].append(c)
    return json.dumps(out, indent=2)


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _load_points(filepath):
    """Load points from text file (x y per line or x,y)."""
    pts = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                pts.append((float(parts[0]), float(parts[1])))
    return pts


def _load_values(filepath):
    """Load per-cell values from text file (one float per line)."""
    vals = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            vals.append(float(line))
    return vals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Voronoi Hatch Pattern Generator — pen-plotter style output"
    )
    parser.add_argument("input", help="Points file (x y per line)")
    parser.add_argument("-o", "--output", default="hatch.svg",
                        help="Output file (default: hatch.svg)")
    parser.add_argument("-s", "--style", default="lines",
                        choices=list(HATCH_STYLES.keys()),
                        help="Hatch style (default: lines)")
    parser.add_argument("--spacing", type=float, default=8.0,
                        help="Hatch line spacing (default: 8)")
    parser.add_argument("--angle", type=float, default=45.0,
                        help="Hatch angle in degrees (default: 45)")
    parser.add_argument("--values", help="Per-cell values file (0-1, one per line)")
    parser.add_argument("--line-width", type=float, default=0.5,
                        help="Stroke width (default: 0.5)")
    parser.add_argument("--no-border", action="store_true",
                        help="Omit cell borders")
    parser.add_argument("--border-width", type=float, default=0.3,
                        help="Cell border width (default: 0.3)")
    parser.add_argument("--width", type=int, default=800,
                        help="SVG width (default: 800)")
    parser.add_argument("--height", type=int, default=600,
                        help="SVG height (default: 600)")
    parser.add_argument("--format", choices=["svg", "json"], default="svg",
                        help="Output format (default: svg)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--demo", action="store_true",
                        help="Generate demo with random points (ignores input)")

    args = parser.parse_args()

    if args.demo:
        rng = _random.Random(args.seed)
        points = [(rng.uniform(50, 750), rng.uniform(50, 550)) for _ in range(30)]
        values = [rng.random() for _ in range(30)]
    else:
        points = _load_points(args.input)
        values = _load_values(args.values) if args.values else None

    if not points:
        print("Error: no points loaded")
        return

    hatch = generate_hatch(
        points,
        style=args.style,
        spacing=args.spacing,
        angle=args.angle,
        values=values,
        line_width=args.line_width,
        border=not args.no_border,
        border_width=args.border_width,
        seed=args.seed,
    )

    if args.format == "json" or args.output.endswith(".json"):
        content = to_json(hatch)
    else:
        content = to_svg(hatch, args.width, args.height)

    with open(args.output, "w") as f:
        f.write(content)

    n_cells = len(hatch["cells"])
    print(f"Generated {args.style} hatch for {n_cells} cells -> {args.output}")


if __name__ == "__main__":
    main()
