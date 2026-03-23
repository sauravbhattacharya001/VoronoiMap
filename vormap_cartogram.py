"""Voronoi Cartogram — area-proportional region distortion for VoronoiMap.

Distorts Voronoi regions so that each cell's area becomes proportional
to a user-supplied value (e.g. population, density, weight) while
preserving adjacency topology.  Based on the Gastner-Newman diffusion
cartogram idea, simplified for Voronoi tessellations using iterative
rubber-sheet scaling.

Algorithm:
    1. Compute Voronoi regions for the input seeds.
    2. Assign a target value to each region.
    3. Iteratively scale each seed outward/inward from the centroid,
       proportional to the ratio of desired area vs current area.
    4. Recompute Voronoi after each iteration.
    5. Stop when area error is below tolerance or max iterations reached.

Example::

    from vormap_cartogram import cartogram
    result = cartogram(points, values=[10, 50, 30, 80, 20])

CLI::

    python vormap_cartogram.py points.txt --values 10,50,30,80,20 --svg out.svg
    python vormap_cartogram.py points.txt --values-file weights.csv --iterations 100
    python vormap_cartogram.py --generate 20 --svg cartogram.svg --animate

Requires scipy (``pip install scipy``).
"""

import argparse
import json
import math
import os
import sys

try:
    import numpy as np
    from scipy.spatial import Voronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

import vormap
from vormap_utils import polygon_area as _polygon_area
from vormap_utils import polygon_centroid as _polygon_centroid


def _clip_region(vertices, bounds):
    """Clip polygon vertices to bounding box using Sutherland-Hodgman."""
    x_min, y_min, x_max, y_max = bounds

    def clip_edge(poly, edge_fn, inside_fn):
        if not poly:
            return []
        out = []
        for i in range(len(poly)):
            cur = poly[i]
            prev = poly[i - 1]
            cur_in = inside_fn(cur)
            prev_in = inside_fn(prev)
            if cur_in:
                if not prev_in:
                    out.append(edge_fn(prev, cur))
                out.append(cur)
            elif prev_in:
                out.append(edge_fn(prev, cur))
        return out

    def intersect_left(a, b):
        t = (x_min - a[0]) / (b[0] - a[0]) if abs(b[0] - a[0]) > 1e-12 else 0
        return (x_min, a[1] + t * (b[1] - a[1]))

    def intersect_right(a, b):
        t = (x_max - a[0]) / (b[0] - a[0]) if abs(b[0] - a[0]) > 1e-12 else 0
        return (x_max, a[1] + t * (b[1] - a[1]))

    def intersect_bottom(a, b):
        t = (y_min - a[1]) / (b[1] - a[1]) if abs(b[1] - a[1]) > 1e-12 else 0
        return (a[0] + t * (b[0] - a[0]), y_min)

    def intersect_top(a, b):
        t = (y_max - a[1]) / (b[1] - a[1]) if abs(b[1] - a[1]) > 1e-12 else 0
        return (a[0] + t * (b[0] - a[0]), y_max)

    poly = list(vertices)
    poly = clip_edge(poly, intersect_left, lambda p: p[0] >= x_min)
    poly = clip_edge(poly, intersect_right, lambda p: p[0] <= x_max)
    poly = clip_edge(poly, intersect_bottom, lambda p: p[1] >= y_min)
    poly = clip_edge(poly, intersect_top, lambda p: p[1] <= y_max)
    return poly


# ── Voronoi Region Computation ──────────────────────────────────────

def _compute_regions(seeds, bounds):
    """Compute clipped Voronoi regions for the given seeds and bounds."""
    if not _HAS_SCIPY:
        raise ImportError("scipy is required: pip install scipy")

    pts = np.array(seeds)
    x_min, y_min, x_max, y_max = bounds
    w = x_max - x_min
    h = y_max - y_min

    # Mirror points for bounded Voronoi
    mirrored = np.vstack([
        pts,
        np.column_stack([2 * x_min - pts[:, 0], pts[:, 1]]),
        np.column_stack([2 * x_max - pts[:, 0], pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * y_min - pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * y_max - pts[:, 1]]),
    ])

    vor = Voronoi(mirrored)
    n = len(seeds)
    regions = []
    for i in range(n):
        region_idx = vor.point_region[i]
        vert_indices = vor.regions[region_idx]
        if -1 in vert_indices or len(vert_indices) == 0:
            regions.append([])
            continue
        verts = [tuple(vor.vertices[vi]) for vi in vert_indices]
        clipped = _clip_region(verts, bounds)
        regions.append(clipped)
    return regions


# ── Cartogram Core ───────────────────────────────────────────────────

def cartogram(points, values, iterations=50, tolerance=0.01, bounds=None,
              damping=0.5):
    """Compute a Voronoi cartogram.

    Parameters
    ----------
    points : list of (x, y)
        Seed point coordinates.
    values : list of float
        Target value for each seed (determines desired relative area).
    iterations : int
        Maximum iterations.
    tolerance : float
        Stop when max relative area error is below this.
    bounds : tuple (x_min, y_min, x_max, y_max) or None
        Bounding box. Auto-detected if None.
    damping : float
        Damping factor (0-1) to prevent oscillation.

    Returns
    -------
    dict with keys:
        seeds : list of (x, y) — final seed positions
        regions : list of list of (x, y) — final Voronoi regions
        areas : list of float — final cell areas
        target_areas : list of float — desired cell areas
        max_error : float — maximum relative area error
        iterations : int — iterations performed
        history : list of dict — per-iteration stats
    """
    if len(points) != len(values):
        raise ValueError("points and values must have the same length")

    n = len(points)
    if n == 0:
        return {"seeds": [], "regions": [], "areas": [], "target_areas": [],
                "max_error": 0.0, "iterations": 0, "history": []}

    seeds = [list(p) for p in points]
    vals = [max(float(v), 1e-6) for v in values]
    total_val = sum(vals)

    # Auto bounds
    if bounds is None:
        xs = [s[0] for s in seeds]
        ys = [s[1] for s in seeds]
        margin_x = (max(xs) - min(xs)) * 0.1 + 1
        margin_y = (max(ys) - min(ys)) * 0.1 + 1
        bounds = (min(xs) - margin_x, min(ys) - margin_y,
                  max(xs) + margin_x, max(ys) + margin_y)

    total_area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    target_areas = [(v / total_val) * total_area for v in vals]

    history = []

    for it in range(iterations):
        regions = _compute_regions(seeds, bounds)
        areas = [_polygon_area(r) if r else 0.0 for r in regions]

        # Compute error
        max_error = 0.0
        for i in range(n):
            if target_areas[i] > 1e-12:
                err = abs(areas[i] - target_areas[i]) / target_areas[i]
                max_error = max(max_error, err)

        history.append({"iteration": it, "max_error": max_error,
                        "mean_area": sum(areas) / n if n else 0})

        if max_error < tolerance:
            break

        # Compute centroids and scale seeds
        cx_all = (bounds[0] + bounds[2]) / 2
        cy_all = (bounds[1] + bounds[3]) / 2

        for i in range(n):
            if not regions[i] or areas[i] < 1e-12:
                continue
            ratio = target_areas[i] / areas[i]
            # Damped scaling: move seed away from/toward centroid
            scale = 1.0 + damping * (math.sqrt(ratio) - 1.0)
            centroid = _polygon_centroid(regions[i])
            new_x = centroid[0] + (seeds[i][0] - centroid[0]) * scale
            new_y = centroid[1] + (seeds[i][1] - centroid[1]) * scale
            # Clamp to bounds
            new_x = max(bounds[0] + 1, min(bounds[2] - 1, new_x))
            new_y = max(bounds[1] + 1, min(bounds[3] - 1, new_y))
            seeds[i] = [new_x, new_y]

    # Final regions
    regions = _compute_regions(seeds, bounds)
    areas = [_polygon_area(r) if r else 0.0 for r in regions]

    return {
        "seeds": [(s[0], s[1]) for s in seeds],
        "regions": regions,
        "areas": areas,
        "target_areas": target_areas,
        "max_error": max_error if n > 0 else 0.0,
        "iterations": len(history),
        "history": history,
    }


# ── SVG Export ───────────────────────────────────────────────────────

def export_svg(result, output_path, width=800, height=600,
               color_by="value", values=None, title=None):
    """Export cartogram result to SVG.

    Parameters
    ----------
    result : dict from cartogram()
    output_path : str
    width, height : int
    color_by : str — "value" or "error"
    values : list of float or None — original values for color mapping
    title : str or None
    """
    regions = result["regions"]
    seeds = result["seeds"]
    areas = result["areas"]
    target_areas = result["target_areas"]

    if not regions:
        with open(output_path, "w") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg"/>')
        return

    # Compute bounds from regions
    all_x = [v[0] for r in regions for v in r]
    all_y = [v[1] for r in regions for v in r]
    x_min, x_max = min(all_x), max(all_x)
    y_min, y_max = min(all_y), max(all_y)
    data_w = x_max - x_min or 1
    data_h = y_max - y_min or 1
    margin = 20
    scale = min((width - 2 * margin) / data_w, (height - 2 * margin) / data_h)

    def tx(x):
        return margin + (x - x_min) * scale

    def ty(y):
        return margin + (y - y_min) * scale

    # Color ramp
    def value_color(val, vmin, vmax):
        if vmax <= vmin:
            t = 0.5
        else:
            t = (val - vmin) / (vmax - vmin)
        t = max(0, min(1, t))
        # Blue → Green → Red
        if t < 0.5:
            r = int(50 + 150 * (t * 2))
            g = int(100 + 155 * (t * 2))
            b = int(200 - 150 * (t * 2))
        else:
            r = int(200 + 55 * ((t - 0.5) * 2))
            g = int(255 - 155 * ((t - 0.5) * 2))
            b = int(50 - 50 * ((t - 0.5) * 2))
        return f"rgb({r},{g},{b})"

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8f9fa"/>',
    ]

    if title:
        lines.append(f'<text x="{width//2}" y="15" text-anchor="middle" '
                      f'font-size="14" font-family="sans-serif">{title}</text>')

    # Determine color values
    if color_by == "error":
        color_vals = []
        for i in range(len(areas)):
            if target_areas[i] > 1e-12:
                color_vals.append(abs(areas[i] - target_areas[i]) / target_areas[i])
            else:
                color_vals.append(0)
    elif values is not None:
        color_vals = [float(v) for v in values]
    else:
        color_vals = list(areas)

    vmin = min(color_vals) if color_vals else 0
    vmax = max(color_vals) if color_vals else 1

    # Draw regions
    for i, region in enumerate(regions):
        if not region:
            continue
        pts_str = " ".join(f"{tx(v[0]):.1f},{ty(v[1]):.1f}" for v in region)
        fill = value_color(color_vals[i], vmin, vmax)
        lines.append(f'<polygon points="{pts_str}" fill="{fill}" '
                      f'stroke="#333" stroke-width="0.8" opacity="0.85"/>')

    # Draw seeds
    for i, (sx, sy) in enumerate(seeds):
        lines.append(f'<circle cx="{tx(sx):.1f}" cy="{ty(sy):.1f}" r="3" '
                      f'fill="#222" opacity="0.6"/>')

    lines.append('</svg>')

    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))


# ── JSON Export ──────────────────────────────────────────────────────

def export_json(result, output_path):
    """Export cartogram result to JSON."""
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2, default=str)


# ── Report ───────────────────────────────────────────────────────────

def format_report(result):
    """Return a human-readable summary of the cartogram result."""
    lines = ["Voronoi Cartogram Report", "=" * 40]
    lines.append(f"Seeds: {len(result['seeds'])}")
    lines.append(f"Iterations: {result['iterations']}")
    lines.append(f"Final max error: {result['max_error']:.4f} "
                 f"({result['max_error']*100:.1f}%)")

    if result['areas']:
        lines.append(f"Area range: {min(result['areas']):.1f} - "
                      f"{max(result['areas']):.1f}")
        lines.append(f"Target range: {min(result['target_areas']):.1f} - "
                      f"{max(result['target_areas']):.1f}")

    if result['history']:
        errors = [h['max_error'] for h in result['history']]
        lines.append(f"Error reduction: {errors[0]:.4f} → {errors[-1]:.4f}")

    return '\n'.join(lines)


# ── CLI ──────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Voronoi Cartogram — area-proportional region distortion",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_cartogram.py points.txt --values 10,50,30,80,20 --svg out.svg
  python vormap_cartogram.py points.txt --values-file weights.csv
  python vormap_cartogram.py --generate 20 --svg cartogram.svg
  python vormap_cartogram.py points.txt --values 1,5,2,8 --json result.json
""")
    parser.add_argument("input", nargs="?", help="Input points file")
    parser.add_argument("--generate", type=int, metavar="N",
                        help="Generate N random points instead of reading input")
    parser.add_argument("--values", type=str,
                        help="Comma-separated target values")
    parser.add_argument("--values-file", type=str,
                        help="File with one value per line")
    parser.add_argument("--iterations", type=int, default=50,
                        help="Max iterations (default: 50)")
    parser.add_argument("--tolerance", type=float, default=0.01,
                        help="Convergence tolerance (default: 0.01)")
    parser.add_argument("--damping", type=float, default=0.5,
                        help="Damping factor 0-1 (default: 0.5)")
    parser.add_argument("--svg", type=str, metavar="FILE",
                        help="Export SVG visualization")
    parser.add_argument("--json", type=str, metavar="FILE",
                        help="Export JSON result")
    parser.add_argument("--report", action="store_true",
                        help="Print summary report")
    parser.add_argument("--animate", action="store_true",
                        help="Generate per-iteration SVG frames")
    args = parser.parse_args()

    # Load or generate points
    if args.generate:
        import random
        rng = random.Random(42)
        seeds = [(rng.uniform(0, 1000), rng.uniform(0, 1000))
                 for _ in range(args.generate)]
        if not args.values and not args.values_file:
            vals = [rng.uniform(1, 100) for _ in range(args.generate)]
        else:
            vals = None
    elif args.input:
        data = vormap.load_data(args.input)
        seeds = [(float(p[0]), float(p[1])) for p in data]
        vals = None
    else:
        parser.error("Provide an input file or --generate N")
        return

    # Load values
    if args.values:
        vals = [float(v.strip()) for v in args.values.split(",")]
    elif args.values_file:
        with open(args.values_file) as f:
            vals = [float(line.strip()) for line in f if line.strip()]

    if vals is None:
        import random
        rng = random.Random(42)
        vals = [rng.uniform(1, 100) for _ in range(len(seeds))]

    if len(vals) != len(seeds):
        print(f"Error: {len(vals)} values but {len(seeds)} points", file=sys.stderr)
        sys.exit(1)

    result = cartogram(seeds, vals, iterations=args.iterations,
                       tolerance=args.tolerance, damping=args.damping)

    if args.report or (not args.svg and not args.json):
        print(format_report(result))

    if args.svg:
        export_svg(result, args.svg, values=vals, title="Voronoi Cartogram")
        print(f"SVG saved: {args.svg}")

    if args.json:
        export_json(result, args.json)
        print(f"JSON saved: {args.json}")

    if args.animate and args.svg:
        base, ext = os.path.splitext(args.svg)
        # Re-run with frame capture
        frame_seeds = [list(p) for p in seeds]
        frame_vals = vals
        bounds = None
        xs = [s[0] for s in seeds]
        ys = [s[1] for s in seeds]
        margin_x = (max(xs) - min(xs)) * 0.1 + 1
        margin_y = (max(ys) - min(ys)) * 0.1 + 1
        bounds = (min(xs) - margin_x, min(ys) - margin_y,
                  max(xs) + margin_x, max(ys) + margin_y)
        total_area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
        total_val = sum(frame_vals)
        target = [(v / total_val) * total_area for v in frame_vals]

        for frame in range(min(args.iterations, result['iterations'])):
            regions = _compute_regions(frame_seeds, bounds)
            areas = [_polygon_area(r) if r else 0.0 for r in regions]
            frame_result = {
                "seeds": [(s[0], s[1]) for s in frame_seeds],
                "regions": regions,
                "areas": areas,
                "target_areas": target,
                "max_error": 0,
                "iterations": frame,
                "history": [],
            }
            frame_path = f"{base}_frame{frame:03d}{ext}"
            export_svg(frame_result, frame_path, values=vals,
                       title=f"Iteration {frame}")

            # Step
            for i in range(len(frame_seeds)):
                if not regions[i] or areas[i] < 1e-12:
                    continue
                ratio = target[i] / areas[i]
                scale = 1.0 + args.damping * (math.sqrt(ratio) - 1.0)
                centroid = _polygon_centroid(regions[i])
                new_x = centroid[0] + (frame_seeds[i][0] - centroid[0]) * scale
                new_y = centroid[1] + (frame_seeds[i][1] - centroid[1]) * scale
                new_x = max(bounds[0] + 1, min(bounds[2] - 1, new_x))
                new_y = max(bounds[1] + 1, min(bounds[3] - 1, new_y))
                frame_seeds[i] = [new_x, new_y]

        print(f"Animation frames saved: {base}_frame*{ext}")


if __name__ == "__main__":
    _cli()
