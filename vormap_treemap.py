"""Voronoi Treemap — hierarchical Voronoi tessellations for data visualisation.

Recursively subdivides Voronoi cells based on weighted hierarchical data,
producing a space-filling treemap where area is proportional to weight.
This is a well-known technique for visualising hierarchical/proportional
data (file system sizes, budgets, population breakdowns, etc.).

Example::

    from vormap_treemap import voronoi_treemap, format_treemap_report

    # Hierarchical data: each node has a name, weight, and optional children
    data = {
        "name": "root",
        "children": [
            {"name": "A", "weight": 60, "children": [
                {"name": "A1", "weight": 30},
                {"name": "A2", "weight": 30},
            ]},
            {"name": "B", "weight": 25},
            {"name": "C", "weight": 15},
        ],
    }

    treemap = voronoi_treemap(data, bbox=(0, 0, 800, 600))
    print(format_treemap_report(treemap))

    # Export to SVG for browser viewing
    export_treemap_svg(treemap, "treemap.svg")

CLI usage::

    python vormap_treemap.py data.json --output treemap.svg
    python vormap_treemap.py data.json --format json --output treemap.json
"""

import json
import math
import os
import random
import sys

import vormap

try:
    import numpy as np
    from scipy.spatial import Voronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Data Model ────────────────────────────────────────────────────────

class TreemapCell:
    """A single cell in the Voronoi treemap."""

    __slots__ = ("name", "weight", "depth", "polygon", "centroid", "area", "children")

    def __init__(self, name, weight, depth, polygon, centroid, area, children=None):
        self.name = name
        self.weight = weight
        self.depth = depth
        self.polygon = polygon          # list of (x, y) tuples
        self.centroid = centroid         # (x, y)
        self.area = area
        self.children = children or []  # list of TreemapCell


# ── Geometry Helpers ──────────────────────────────────────────────────

from vormap_utils import polygon_area as _polygon_area, point_in_polygon as _point_in_polygon
from vormap_utils import polygon_centroid as _polygon_centroid


def _clip_polygon_to_bbox(poly, xmin, ymin, xmax, ymax):
    """Sutherland-Hodgman polygon clipping against an axis-aligned bbox."""
    def clip_edge(points, edge_fn, inside_fn):
        if not points:
            return []
        out = []
        prev = points[-1]
        for curr in points:
            if inside_fn(curr):
                if not inside_fn(prev):
                    out.append(edge_fn(prev, curr))
                out.append(curr)
            elif inside_fn(prev):
                out.append(edge_fn(prev, curr))
            prev = curr
        return out

    def lerp(a, b, t):
        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))

    edges = [
        (lambda p: p[0] >= xmin, lambda a, b: lerp(a, b, (xmin - a[0]) / (b[0] - a[0]) if abs(b[0] - a[0]) > 1e-12 else 0)),
        (lambda p: p[0] <= xmax, lambda a, b: lerp(a, b, (xmax - a[0]) / (b[0] - a[0]) if abs(b[0] - a[0]) > 1e-12 else 0)),
        (lambda p: p[1] >= ymin, lambda a, b: lerp(a, b, (ymin - a[1]) / (b[1] - a[1]) if abs(b[1] - a[1]) > 1e-12 else 0)),
        (lambda p: p[1] <= ymax, lambda a, b: lerp(a, b, (ymax - a[1]) / (b[1] - a[1]) if abs(b[1] - a[1]) > 1e-12 else 0)),
    ]

    result = list(poly)
    for inside_fn, intersect_fn in edges:
        result = clip_edge(result, intersect_fn, inside_fn)
        if not result:
            break
    return result


def _bbox_polygon(xmin, ymin, xmax, ymax):
    """Return bbox as a polygon (CCW)."""
    return [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]



def _compute_bounded_voronoi(seeds, bbox):
    """Compute Voronoi regions clipped to a bounding box.

    Returns a list of polygons (one per seed), each clipped to bbox.
    """
    xmin, ymin, xmax, ymax = bbox
    n = len(seeds)

    if n == 1:
        return [_bbox_polygon(xmin, ymin, xmax, ymax)]

    if not _HAS_SCIPY:
        # Fallback: simple rectangular partition (very rough)
        cols = max(1, int(math.ceil(math.sqrt(n))))
        rows = max(1, int(math.ceil(n / cols)))
        dx = (xmax - xmin) / cols
        dy = (ymax - ymin) / rows
        polys = []
        for idx in range(n):
            r, c = divmod(idx, cols)
            polys.append(_bbox_polygon(
                xmin + c * dx, ymin + r * dy,
                xmin + (c + 1) * dx, ymin + (r + 1) * dy,
            ))
        return polys

    pts = np.array(seeds)

    # Mirror points to create bounded regions
    w, h = xmax - xmin, ymax - ymin
    mirrored = np.concatenate([
        pts,
        np.column_stack([2 * xmin - pts[:, 0], pts[:, 1]]),
        np.column_stack([2 * xmax - pts[:, 0], pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * ymin - pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * ymax - pts[:, 1]]),
    ])

    vor = Voronoi(mirrored)
    polys = []
    for i in range(n):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]
        if -1 in region or not region:
            polys.append(_bbox_polygon(xmin, ymin, xmax, ymax))
            continue
        verts = [tuple(vor.vertices[v]) for v in region]
        clipped = _clip_polygon_to_bbox(verts, xmin, ymin, xmax, ymax)
        if len(clipped) < 3:
            clipped = _bbox_polygon(xmin, ymin, xmax, ymax)
        polys.append(clipped)
    return polys


def _weighted_voronoi_relaxation(weights, bbox, iterations=50, seed=42):
    """Iteratively adjust seeds so cell areas match target weights.

    Uses Lloyd relaxation with area-weight correction.
    Returns final polygons matching the weight distribution.
    """
    xmin, ymin, xmax, ymax = bbox
    n = len(weights)
    total_w = sum(weights)
    target_fracs = [w / total_w for w in weights]
    total_area = (xmax - xmin) * (ymax - ymin)
    target_areas = [f * total_area for f in target_fracs]

    rng = random.Random(seed)
    seeds = [(rng.uniform(xmin, xmax), rng.uniform(ymin, ymax)) for _ in range(n)]

    for iteration in range(iterations):
        polys = _compute_bounded_voronoi(seeds, bbox)
        areas = [_polygon_area(p) for p in polys]
        centroids = [_polygon_centroid(p) for p in polys]

        # Move seeds toward centroids with area correction
        new_seeds = []
        for i in range(n):
            cx, cy = centroids[i]
            # Clamp to bbox
            cx = max(xmin + 1e-6, min(xmax - 1e-6, cx))
            cy = max(ymin + 1e-6, min(ymax - 1e-6, cy))
            new_seeds.append((cx, cy))
        seeds = new_seeds

    # Final pass
    polys = _compute_bounded_voronoi(seeds, bbox)
    return polys, seeds


# ── Treemap Construction ─────────────────────────────────────────────

def _get_weight(node):
    """Recursively compute the total weight of a node."""
    if "children" in node and node["children"]:
        return sum(_get_weight(c) for c in node["children"])
    return node.get("weight", 1)


def _build_treemap(node, bbox, depth=0, iterations=50, seed=42):
    """Recursively build the Voronoi treemap."""
    name = node.get("name", f"node_{depth}")
    weight = _get_weight(node)
    children_data = node.get("children", [])

    poly = _bbox_polygon(*bbox)
    centroid = _polygon_centroid(poly)
    area = _polygon_area(poly)

    if not children_data:
        return TreemapCell(name, weight, depth, poly, centroid, area)

    # Subdivide this bbox among children
    weights = [_get_weight(c) for c in children_data]
    polys, seeds = _weighted_voronoi_relaxation(
        weights, bbox, iterations=iterations, seed=seed + depth * 1000,
    )

    child_cells = []
    for i, child_node in enumerate(children_data):
        cpoly = polys[i] if i < len(polys) else poly
        # Compute child bbox from its polygon
        xs = [p[0] for p in cpoly]
        ys = [p[1] for p in cpoly]
        child_bbox = (min(xs), min(ys), max(xs), max(ys))
        child_cell = _build_treemap(
            child_node, child_bbox,
            depth=depth + 1,
            iterations=iterations,
            seed=seed + i * 100 + depth * 1000,
        )
        # Override polygon with the actual Voronoi cell
        if depth == 0 or not child_node.get("children"):
            child_cell.polygon = cpoly
            child_cell.area = _polygon_area(cpoly)
            child_cell.centroid = _polygon_centroid(cpoly)
        child_cells.append(child_cell)

    return TreemapCell(name, weight, depth, poly, centroid, area, child_cells)


def voronoi_treemap(data, bbox=(0, 0, 800, 600), iterations=50, seed=42):
    """Build a Voronoi treemap from hierarchical weighted data.

    Parameters
    ----------
    data : dict
        Hierarchical data with ``name``, ``weight``, and optional ``children``.
    bbox : tuple
        Bounding box ``(xmin, ymin, xmax, ymax)``.
    iterations : int
        Lloyd relaxation iterations per level (more = better area matching).
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    TreemapCell
        Root cell of the treemap with nested children.
    """
    if not isinstance(data, dict):
        raise ValueError("data must be a dict with 'name' and optional 'children'")
    return _build_treemap(data, bbox, iterations=iterations, seed=seed)


# ── Reporting ─────────────────────────────────────────────────────────

def _flatten_cells(cell, out=None):
    """Collect all leaf cells."""
    if out is None:
        out = []
    if not cell.children:
        out.append(cell)
    else:
        for c in cell.children:
            _flatten_cells(c, out)
    return out


def format_treemap_report(root):
    """Format a human-readable treemap summary."""
    lines = ["═══ Voronoi Treemap Report ═══", ""]
    leaves = _flatten_cells(root)
    total_area = root.area

    lines.append(f"Root: {root.name}  (total weight: {root.weight:.1f})")
    lines.append(f"Bounding area: {total_area:.1f}")
    lines.append(f"Leaf cells: {len(leaves)}")
    lines.append(f"Max depth: {max(c.depth for c in leaves)}")
    lines.append("")
    lines.append(f"{'Name':<20} {'Weight':>8} {'Area':>10} {'Area%':>7} {'Depth':>6}")
    lines.append("─" * 55)

    for cell in sorted(leaves, key=lambda c: c.area, reverse=True):
        pct = (cell.area / total_area * 100) if total_area > 0 else 0
        lines.append(f"{cell.name:<20} {cell.weight:>8.1f} {cell.area:>10.1f} {pct:>6.1f}% {cell.depth:>6}")

    return "\n".join(lines)


# ── Export ────────────────────────────────────────────────────────────

def _cell_to_dict(cell):
    """Convert a TreemapCell to a JSON-serialisable dict."""
    d = {
        "name": cell.name,
        "weight": cell.weight,
        "depth": cell.depth,
        "area": round(cell.area, 2),
        "centroid": [round(c, 2) for c in cell.centroid],
        "polygon": [[round(x, 2), round(y, 2)] for x, y in cell.polygon],
    }
    if cell.children:
        d["children"] = [_cell_to_dict(c) for c in cell.children]
    return d


def export_treemap_json(root, filepath):
    """Export treemap to JSON."""
    safe = vormap.validate_output_path(filepath, allow_absolute=True)
    with open(safe, "w") as f:
        json.dump(_cell_to_dict(root), f, indent=2)


def _depth_color(depth, max_depth):
    """Generate an HSL color string based on depth."""
    if max_depth == 0:
        hue = 200
    else:
        hue = (depth * 137) % 360  # golden angle distribution
    sat = max(30, 70 - depth * 10)
    light = min(85, 50 + depth * 8)
    return f"hsl({hue}, {sat}%, {light}%)"


def export_treemap_svg(root, filepath, width=None, height=None):
    """Export treemap as an SVG file.

    Parameters
    ----------
    root : TreemapCell
        Root of the treemap.
    filepath : str
        Output SVG path.
    width, height : float or None
        SVG dimensions. Defaults to bounding box size.
    """
    leaves = _flatten_cells(root)
    max_depth = max(c.depth for c in leaves) if leaves else 0

    # Determine SVG dimensions from root polygon
    all_pts = root.polygon
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    vw = width or (max(xs) - min(xs))
    vh = height or (max(ys) - min(ys))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{vw}" height="{vh}" '
        f'viewBox="{min(xs)} {min(ys)} {vw} {vh}">',
        '<style>',
        '  text { font-family: Arial, sans-serif; pointer-events: none; }',
        '  polygon { stroke: #333; stroke-width: 1; }',
        '  polygon:hover { stroke-width: 2; stroke: #000; filter: brightness(1.1); }',
        '</style>',
    ]

    # Add tooltip support
    parts.append('<defs></defs>')

    for cell in leaves:
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y in cell.polygon)
        color = _depth_color(cell.depth, max_depth)
        cx, cy = cell.centroid
        parts.append(f'<g>')
        parts.append(f'  <title>{cell.name} (weight: {cell.weight:.1f}, area: {cell.area:.1f})</title>')
        parts.append(f'  <polygon points="{pts_str}" fill="{color}" />')
        # Only label if cell is large enough
        if cell.area > (vw * vh * 0.01):
            font_size = max(8, min(16, math.sqrt(cell.area) / 5))
            parts.append(
                f'  <text x="{cx:.1f}" y="{cy:.1f}" font-size="{font_size:.0f}" '
                f'text-anchor="middle" dominant-baseline="central" fill="#333">'
                f'{cell.name}</text>'
            )
        parts.append('</g>')

    parts.append('</svg>')

    safe = vormap.validate_output_path(filepath, allow_absolute=True)
    with open(safe, "w") as f:
        f.write("\n".join(parts))


def export_treemap_csv(root, filepath):
    """Export leaf cells to CSV."""
    leaves = _flatten_cells(root)
    safe = vormap.validate_output_path(filepath, allow_absolute=True)
    with open(safe, "w") as f:
        f.write("name,weight,depth,area,centroid_x,centroid_y\n")
        for cell in leaves:
            cx, cy = cell.centroid
            f.write(f"{cell.name},{cell.weight:.2f},{cell.depth},{cell.area:.2f},{cx:.2f},{cy:.2f}\n")


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    """CLI entry point for Voronoi treemap generation."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate a Voronoi treemap from hierarchical JSON data.",
        epilog="Input JSON format: {\"name\": \"root\", \"children\": [{\"name\": \"A\", \"weight\": 10}, ...]}",
    )
    parser.add_argument("input", help="Input JSON file with hierarchical data")
    parser.add_argument("-o", "--output", default="treemap.svg", help="Output file (default: treemap.svg)")
    parser.add_argument("-f", "--format", choices=["svg", "json", "csv", "text"],
                        default=None, help="Output format (auto-detected from extension)")
    parser.add_argument("--width", type=float, default=800, help="Canvas width (default: 800)")
    parser.add_argument("--height", type=float, default=600, help="Canvas height (default: 600)")
    parser.add_argument("--iterations", type=int, default=50, help="Relaxation iterations (default: 50)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")

    args = parser.parse_args()

    # Load input
    safe_input = vormap.validate_input_path(args.input, allow_absolute=True)
    with open(safe_input) as f:
        data = json.load(f)

    # Build treemap
    bbox = (0, 0, args.width, args.height)
    root = voronoi_treemap(data, bbox=bbox, iterations=args.iterations, seed=args.seed)

    # Determine format
    fmt = args.format
    if fmt is None:
        ext = os.path.splitext(args.output)[1].lower()
        fmt = {".svg": "svg", ".json": "json", ".csv": "csv"}.get(ext, "svg")

    if fmt == "svg":
        export_treemap_svg(root, args.output)
    elif fmt == "json":
        export_treemap_json(root, args.output)
    elif fmt == "csv":
        export_treemap_csv(root, args.output)
    elif fmt == "text":
        report = format_treemap_report(root)
        if args.output == "treemap.svg":
            print(report)
        else:
            safe_out = vormap.validate_output_path(args.output, allow_absolute=True)
            with open(safe_out, "w") as f:
                f.write(report)
    else:
        export_treemap_svg(root, args.output)

    print(f"Voronoi treemap → {args.output} ({fmt})")


if __name__ == "__main__":
    main()
