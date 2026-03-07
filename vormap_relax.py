"""Lloyd's Relaxation for VoronoiMap — Centroidal Voronoi Tessellation.

Iteratively moves Voronoi seeds to their cell centroids, producing
progressively more uniform tessellations.  Useful for mesh generation,
artistic stippling, spatial optimization, and blue-noise sampling.

Algorithm:
    1. Compute Voronoi diagram for current seeds.
    2. For each finite cell, compute the centroid (area-weighted center).
    3. Move each seed to its cell centroid.
    4. Repeat until convergence or max iterations reached.

Convergence is measured as the maximum displacement of any seed between
iterations.  When all seeds move less than ``--tolerance``, the process
stops early.

Example::

    from vormap_relax import lloyd_relaxation
    relaxed = lloyd_relaxation(points, iterations=20)

CLI::

    python vormap_relax.py points.txt --iterations 30 --output relaxed.txt
    python vormap_relax.py points.txt -n 50 --svg relaxed.svg --animate
    python vormap_relax.py points.txt -n 10 --tolerance 0.5 --json out.json
    python vormap_relax.py --generate 200 -n 40 --svg demo.svg --animate

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


# ── Core Algorithm ───────────────────────────────────────────────────


def _polygon_centroid(vertices):
    """Compute centroid of a polygon using the standard formula.

    Parameters
    ----------
    vertices : ndarray, shape (n, 2)
        Ordered polygon vertices.

    Returns
    -------
    (float, float)
        Centroid (cx, cy).  Falls back to geometric mean if area is ~0.
    """
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        return (float(vertices[:, 0].mean()), float(vertices[:, 1].mean()))

    # Shoelace-based centroid
    x = vertices[:, 0]
    y = vertices[:, 1]
    x_next = np.roll(x, -1)
    y_next = np.roll(y, -1)

    cross = x * y_next - x_next * y
    area6 = cross.sum()  # 2 * signed area * 3

    if abs(area6) < 1e-12:
        return (float(x.mean()), float(y.mean()))

    cx = ((x + x_next) * cross).sum() / (3.0 * area6)
    cy = ((y + y_next) * cross).sum() / (3.0 * area6)
    return (float(cx), float(cy))


def _clip_infinite_region(vor, region_idx, bounds):
    """Clip a Voronoi region to a bounding box.

    For regions with vertices at infinity (index -1), projects the
    infinite edges to the boundary.  Returns the clipped polygon
    vertices as an ndarray.

    Parameters
    ----------
    vor : scipy.spatial.Voronoi
        Voronoi diagram.
    region_idx : int
        Index into ``vor.regions``.
    bounds : tuple
        (x_min, x_max, y_min, y_max).

    Returns
    -------
    ndarray or None
        Clipped polygon vertices, or None if region is degenerate.
    """
    region = vor.regions[region_idx]
    if not region:
        return None

    x_min, x_max, y_min, y_max = bounds

    if -1 not in region:
        # All finite — just clip to bounds
        verts = vor.vertices[region]
        clipped = _clip_polygon_to_box(verts, bounds)
        return clipped if len(clipped) >= 3 else None

    # Region has infinite edges — we need to handle them
    # Find the point this region belongs to
    point_idx = None
    for i, r in enumerate(vor.point_region):
        if r == region_idx:
            point_idx = i
            break
    if point_idx is None:
        return None

    # Collect finite vertices and project infinite ridges
    finite_verts = []
    for v_idx in region:
        if v_idx >= 0:
            finite_verts.append(vor.vertices[v_idx])

    # Find ridges that belong to this point and have -1 vertex
    center = vor.points.mean(axis=0)
    new_verts = list(finite_verts)

    for ridge_pts, ridge_verts in zip(vor.ridge_points, vor.ridge_vertices):
        if point_idx not in ridge_pts:
            continue
        if -1 not in ridge_verts:
            continue

        # Get the finite vertex of this ridge
        fin_v = [v for v in ridge_verts if v >= 0]
        if not fin_v:
            continue
        fin_vert = vor.vertices[fin_v[0]]

        # Direction: perpendicular to the two points sharing this ridge
        p1, p2 = vor.points[ridge_pts[0]], vor.points[ridge_pts[1]]
        tangent = p2 - p1
        normal = np.array([-tangent[1], tangent[0]])

        # Orient outward
        midpoint = 0.5 * (p1 + p2)
        if np.dot(midpoint - center, normal) < 0:
            normal = -normal

        # Project to a far point (well beyond bounds)
        scale = max(x_max - x_min, y_max - y_min) * 10
        far_point = fin_vert + normal / np.linalg.norm(normal) * scale
        new_verts.append(far_point)

    if len(new_verts) < 3:
        return None

    # Sort vertices by angle around centroid
    pts = np.array(new_verts)
    cx, cy = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    order = np.argsort(angles)
    pts = pts[order]

    clipped = _clip_polygon_to_box(pts, bounds)
    return clipped if len(clipped) >= 3 else None


def _clip_polygon_to_box(vertices, bounds):
    """Sutherland-Hodgman polygon clipping against an axis-aligned box.

    Parameters
    ----------
    vertices : ndarray, shape (n, 2)
    bounds : tuple
        (x_min, x_max, y_min, y_max).

    Returns
    -------
    ndarray
        Clipped polygon vertices.
    """
    x_min, x_max, y_min, y_max = bounds

    def clip_edge(poly, edge_func, intersect_func):
        if len(poly) == 0:
            return poly
        output = []
        prev = poly[-1]
        prev_inside = edge_func(prev)
        for curr in poly:
            curr_inside = edge_func(curr)
            if curr_inside:
                if not prev_inside:
                    output.append(intersect_func(prev, curr))
                output.append(curr)
            elif prev_inside:
                output.append(intersect_func(prev, curr))
            prev = curr
            prev_inside = curr_inside
        return output

    def _lerp(a, b, t):
        return np.array([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])])

    poly = list(vertices)

    # Clip left
    poly = clip_edge(poly,
        lambda p: p[0] >= x_min,
        lambda a, b: _lerp(a, b, (x_min - a[0]) / (b[0] - a[0])) if abs(b[0] - a[0]) > 1e-15 else a)

    # Clip right
    poly = clip_edge(poly,
        lambda p: p[0] <= x_max,
        lambda a, b: _lerp(a, b, (x_max - a[0]) / (b[0] - a[0])) if abs(b[0] - a[0]) > 1e-15 else a)

    # Clip bottom
    poly = clip_edge(poly,
        lambda p: p[1] >= y_min,
        lambda a, b: _lerp(a, b, (y_min - a[1]) / (b[1] - a[1])) if abs(b[1] - a[1]) > 1e-15 else a)

    # Clip top
    poly = clip_edge(poly,
        lambda p: p[1] <= y_max,
        lambda a, b: _lerp(a, b, (y_max - a[1]) / (b[1] - a[1])) if abs(b[1] - a[1]) > 1e-15 else a)

    return np.array(poly) if poly else np.empty((0, 2))


def lloyd_relaxation(points, iterations=20, bounds=None, tolerance=0.1,
                     callback=None):
    """Apply Lloyd's relaxation to a set of 2D points.

    Parameters
    ----------
    points : list of (float, float)
        Initial seed coordinates.
    iterations : int
        Maximum number of relaxation iterations.
    bounds : tuple or None
        (x_min, x_max, y_min, y_max) clipping bounds.  Auto-computed
        from points with padding if None.
    tolerance : float
        Stop early if max seed displacement falls below this threshold.
    callback : callable or None
        Called after each iteration as ``callback(step, current_points,
        max_displacement)``.  Useful for animation / progress tracking.

    Returns
    -------
    dict
        ``points``: final relaxed coordinates (list of [x, y]).
        ``history``: list of point arrays at each step (including initial).
        ``displacements``: max displacement at each iteration.
        ``converged``: True if tolerance was reached before max iterations.
        ``iterations``: number of iterations actually performed.

    Raises
    ------
    ImportError
        If scipy is not available.
    ValueError
        If fewer than 3 points are provided.
    """
    if not _HAS_SCIPY:
        raise ImportError(
            "scipy is required for Lloyd's relaxation. "
            "Install it with: pip install scipy"
        )

    pts = np.array(points, dtype=float)
    if len(pts) < 3:
        raise ValueError("At least 3 points are required for relaxation")

    if bounds is None:
        pad = 0.1
        x_min, x_max = pts[:, 0].min(), pts[:, 0].max()
        y_min, y_max = pts[:, 1].min(), pts[:, 1].max()
        dx = max((x_max - x_min) * pad, 1.0)
        dy = max((y_max - y_min) * pad, 1.0)
        bounds = (x_min - dx, x_max + dx, y_min - dy, y_max + dy)

    history = [pts.copy()]
    displacements = []
    converged = False

    for step in range(iterations):
        vor = Voronoi(pts)
        new_pts = pts.copy()

        for i in range(len(pts)):
            region_idx = vor.point_region[i]
            clipped = _clip_infinite_region(vor, region_idx, bounds)
            if clipped is not None and len(clipped) >= 3:
                cx, cy = _polygon_centroid(clipped)
                # Clamp to bounds
                cx = max(bounds[0], min(bounds[1], cx))
                cy = max(bounds[2], min(bounds[3], cy))
                new_pts[i] = [cx, cy]

        disp = np.sqrt(((new_pts - pts) ** 2).sum(axis=1)).max()
        displacements.append(float(disp))
        pts = new_pts
        history.append(pts.copy())

        if callback:
            callback(step + 1, pts, disp)

        if disp < tolerance:
            converged = True
            break

    return {
        "points": pts.tolist(),
        "history": [h.tolist() for h in history],
        "displacements": displacements,
        "converged": converged,
        "iterations": len(displacements),
    }


# ── Metrics ──────────────────────────────────────────────────────────


def uniformity_score(points, bounds=None):
    """Measure spatial uniformity of a point set (0 = clustered, 1 = uniform).

    Uses coefficient of variation of nearest-neighbor distances.
    Lower CV means more uniform spacing.  Returns ``1 - min(cv, 1)``
    so 1.0 is perfectly uniform.

    Parameters
    ----------
    points : list or ndarray
        2D point coordinates.
    bounds : tuple or None
        Ignored (included for API consistency).

    Returns
    -------
    float
        Uniformity score in [0, 1].
    """
    if not _HAS_SCIPY:
        raise ImportError("scipy is required for uniformity_score")

    from scipy.spatial import KDTree

    pts = np.array(points)
    if len(pts) < 2:
        return 1.0

    tree = KDTree(pts)
    dists, _ = tree.query(pts, k=2)
    nn_dists = dists[:, 1]  # skip self

    mean_d = nn_dists.mean()
    if mean_d < 1e-12:
        return 0.0

    cv = nn_dists.std() / mean_d
    return round(max(0.0, 1.0 - min(cv, 1.0)), 4)


# ── SVG Output ───────────────────────────────────────────────────────


def _generate_svg(result, bounds, width=800, height=600, animate=False,
                  show_seeds=True, color_scheme="pastel"):
    """Generate SVG visualization of relaxation result.

    Parameters
    ----------
    result : dict
        Output from :func:`lloyd_relaxation`.
    bounds : tuple
        (x_min, x_max, y_min, y_max).
    width, height : int
        Canvas dimensions.
    animate : bool
        If True, include CSS animation showing seed movement across steps.
    show_seeds : bool
        Draw seed points.
    color_scheme : str
        Color scheme for Voronoi cells.

    Returns
    -------
    str
        SVG markup.
    """
    x_min, x_max, y_min, y_max = bounds
    bw = x_max - x_min
    bh = y_max - y_min

    def tx(x):
        return (x - x_min) / bw * width

    def ty(y):
        return height - (y - y_min) / bh * height

    schemes = {
        "pastel": ["#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9", "#BAE1FF",
                    "#E8BAFF", "#FFB3E6", "#C9BAFF"],
        "cool":   ["#264653", "#2A9D8F", "#E9C46A", "#F4A261", "#E76F51",
                    "#606C38", "#283618", "#DDA15E"],
        "warm":   ["#FF6B6B", "#FFA07A", "#FFD700", "#FF8C00", "#FF4500",
                    "#DC143C", "#B22222", "#FF69B4"],
        "mono":   ["#F8F9FA", "#E9ECEF", "#DEE2E6", "#CED4DA", "#ADB5BD",
                    "#6C757D", "#495057", "#343A40"],
    }
    colors = schemes.get(color_scheme, schemes["pastel"])

    final_pts = np.array(result["points"])

    # Build final Voronoi for cell rendering
    vor = Voronoi(final_pts)

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'width="{width}" height="{height}" '
                 f'viewBox="0 0 {width} {height}">')
    lines.append(f'<rect width="{width}" height="{height}" fill="#1a1a2e"/>')

    # Draw cells
    for i in range(len(final_pts)):
        region_idx = vor.point_region[i]
        clipped = _clip_infinite_region(vor, region_idx, bounds)
        if clipped is not None and len(clipped) >= 3:
            poly_str = " ".join(f"{tx(v[0]):.1f},{ty(v[1]):.1f}" for v in clipped)
            fill = colors[i % len(colors)]
            lines.append(
                f'  <polygon points="{poly_str}" '
                f'fill="{fill}" fill-opacity="0.3" '
                f'stroke="{fill}" stroke-opacity="0.6" stroke-width="1"/>'
            )

    # Draw seeds
    if show_seeds:
        if animate and len(result["history"]) > 1:
            # Animated seeds showing relaxation progression
            duration = max(len(result["history"]) * 0.5, 2.0)
            for i in range(len(final_pts)):
                values_x = ";".join(
                    f"{tx(step[i][0]):.1f}" for step in result["history"]
                )
                values_y = ";".join(
                    f"{ty(step[i][1]):.1f}" for step in result["history"]
                )
                color = colors[i % len(colors)]
                sx = tx(result["history"][0][i][0])
                sy = ty(result["history"][0][i][1])
                lines.append(
                    f'  <circle cx="{sx:.1f}" cy="{sy:.1f}" r="3" '
                    f'fill="{color}">'
                )
                lines.append(
                    f'    <animate attributeName="cx" '
                    f'values="{values_x}" dur="{duration:.1f}s" '
                    f'repeatCount="indefinite"/>'
                )
                lines.append(
                    f'    <animate attributeName="cy" '
                    f'values="{values_y}" dur="{duration:.1f}s" '
                    f'repeatCount="indefinite"/>'
                )
                lines.append('  </circle>')
        else:
            for i, pt in enumerate(final_pts):
                color = colors[i % len(colors)]
                lines.append(
                    f'  <circle cx="{tx(pt[0]):.1f}" cy="{ty(pt[1]):.1f}" '
                    f'r="3" fill="{color}"/>'
                )

    # Convergence info
    iters = result["iterations"]
    conv = "converged" if result["converged"] else f"{iters} iterations"
    score = uniformity_score(final_pts)
    lines.append(
        f'  <text x="10" y="{height - 10}" fill="#aaa" '
        f'font-family="monospace" font-size="12">'
        f'Lloyd relaxation: {conv} | uniformity: {score:.3f}</text>'
    )

    lines.append('</svg>')
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    """CLI entry point for Lloyd's relaxation."""
    parser = argparse.ArgumentParser(
        description="Lloyd's relaxation — centroidal Voronoi tessellation.",
        epilog="Example: python vormap_relax.py points.txt -n 30 --svg out.svg",
    )
    parser.add_argument(
        "datafile", nargs="?",
        help="Input point data file (txt/csv/json/geojson). "
             "Omit if using --generate.",
    )
    parser.add_argument(
        "-n", "--iterations", type=int, default=20,
        help="Maximum relaxation iterations (default: 20).",
    )
    parser.add_argument(
        "--tolerance", type=float, default=0.1,
        help="Convergence threshold — max seed displacement to stop "
             "(default: 0.1).",
    )
    parser.add_argument(
        "--bounds", nargs=4, type=float, metavar=("XMIN", "XMAX", "YMIN", "YMAX"),
        help="Explicit clipping bounds. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write relaxed points to a text file (x y per line).",
    )
    parser.add_argument(
        "--json", metavar="FILE",
        help="Write full result (history, displacements, metrics) as JSON.",
    )
    parser.add_argument(
        "--svg", metavar="FILE",
        help="Generate SVG visualization of the relaxed diagram.",
    )
    parser.add_argument(
        "--animate", action="store_true",
        help="Animate seed movement in SVG output (shows relaxation process).",
    )
    parser.add_argument(
        "--svg-width", type=int, default=800,
        help="SVG canvas width (default: 800).",
    )
    parser.add_argument(
        "--svg-height", type=int, default=600,
        help="SVG canvas height (default: 600).",
    )
    parser.add_argument(
        "--color-scheme", default="pastel",
        choices=["pastel", "cool", "warm", "mono"],
        help="Color scheme for SVG cells (default: pastel).",
    )
    parser.add_argument(
        "--generate", type=int, metavar="N",
        help="Generate N random points instead of reading a file.",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for --generate reproducibility.",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress progress output.",
    )

    args = parser.parse_args()

    if not _HAS_SCIPY:
        print("Error: scipy is required. Install with: pip install scipy",
              file=sys.stderr)
        sys.exit(1)

    # Load or generate points
    if args.generate:
        import random as _random
        rng = _random.Random(args.seed)
        bx = (0, 1000)
        by = (0, 1000)
        points = [(rng.uniform(*bx), rng.uniform(*by))
                  for _ in range(args.generate)]
        if not args.quiet:
            print(f"Generated {args.generate} random points")
    elif args.datafile:
        safe_path = vormap.validate_input_path(
            args.datafile, allow_absolute=True)
        data = vormap.load_data(safe_path, auto_bounds=False)
        points = [(x, y) for x, y in data]
        if not args.quiet:
            print(f"Loaded {len(points)} points from {args.datafile}")
    else:
        parser.error("Provide a data file or use --generate N")

    if len(points) < 3:
        print("Error: at least 3 points are required", file=sys.stderr)
        sys.exit(1)

    bounds = tuple(args.bounds) if args.bounds else None

    # Compute initial uniformity
    init_score = uniformity_score(points)
    if not args.quiet:
        print(f"Initial uniformity: {init_score:.4f}")

    # Run relaxation
    def on_step(step, pts, disp):
        if not args.quiet:
            score = uniformity_score(pts)
            print(f"  Step {step:3d}: max displacement = {disp:.4f}, "
                  f"uniformity = {score:.4f}")

    result = lloyd_relaxation(
        points,
        iterations=args.iterations,
        bounds=bounds,
        tolerance=args.tolerance,
        callback=on_step,
    )

    final_score = uniformity_score(result["points"])
    improvement = final_score - init_score

    if not args.quiet:
        status = "Converged" if result["converged"] else "Max iterations reached"
        print(f"\n{status} after {result['iterations']} iterations")
        print(f"Final uniformity: {final_score:.4f} "
              f"({'+'if improvement >= 0 else ''}{improvement:.4f})")

    # Determine bounds for output
    if bounds is None:
        pts_arr = np.array(result["points"])
        pad = 0.1
        x_min, x_max = pts_arr[:, 0].min(), pts_arr[:, 0].max()
        y_min, y_max = pts_arr[:, 1].min(), pts_arr[:, 1].max()
        dx = max((x_max - x_min) * pad, 1.0)
        dy = max((y_max - y_min) * pad, 1.0)
        bounds = (x_min - dx, x_max + dx, y_min - dy, y_max + dy)

    # Write outputs
    if args.output:
        safe_out = vormap.validate_output_path(args.output, allow_absolute=True)
        with open(safe_out, "w") as f:
            for x, y in result["points"]:
                f.write(f"{x:.6f} {y:.6f}\n")
        if not args.quiet:
            print(f"Relaxed points written to {args.output}")

    if args.json:
        safe_json = vormap.validate_output_path(args.json, allow_absolute=True)
        out = {
            "points": result["points"],
            "displacements": result["displacements"],
            "converged": result["converged"],
            "iterations": result["iterations"],
            "uniformity": {
                "initial": init_score,
                "final": final_score,
                "improvement": round(improvement, 4),
            },
            "bounds": list(bounds),
        }
        with open(safe_json, "w") as f:
            json.dump(out, f, indent=2)
        if not args.quiet:
            print(f"Full result written to {args.json}")

    if args.svg:
        safe_svg = vormap.validate_output_path(args.svg, allow_absolute=True)
        svg = _generate_svg(
            result, bounds,
            width=args.svg_width, height=args.svg_height,
            animate=args.animate, color_scheme=args.color_scheme,
        )
        with open(safe_svg, "w") as f:
            f.write(svg)
        if not args.quiet:
            print(f"SVG visualization written to {args.svg}")


if __name__ == "__main__":
    main()
