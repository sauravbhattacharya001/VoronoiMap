"""Voronoi Cell Shape Analysis — geometric shape metrics for Voronoi regions.

Quantifies the shape characteristics of individual Voronoi cells and the
overall tessellation using well-established shape indices from computational
geometry and landscape ecology.

**Per-cell metrics:**

- **Compactness (IPQ)** — Isoperimetric Quotient: ``4π × area / perimeter²``.
  1.0 for a perfect circle, lower for irregular/elongated shapes.
- **Shape Index (SI)** — ``perimeter / (2√(π × area))``.  1.0 for a circle,
  >1 for increasingly irregular shapes.  The fractal dimension analogue.
- **Elongation** — aspect ratio of the minimum-area bounding rectangle.
  1.0 for a square cell, >1 for narrow/elongated cells.
- **Centroid Displacement** — normalised distance from the Voronoi seed to
  its cell centroid.  High values indicate asymmetric cells.
- **Orientation** — angle (degrees) of the cell's major axis.  Reveals
  directional bias in the tessellation.
- **Rectangularity** — ``area / bounding_rectangle_area``.  How well the
  cell fills its bounding box.
- **Shape Class** — categorical label: compact / regular / moderate /
  elongated / highly_elongated.

**Aggregate statistics:**

- Mean, std, min, max of each metric across all cells.
- Shape class distribution histogram.
- Spatial autocorrelation of shape index (are irregular cells clustered?).

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_shape import analyze_shapes, format_shape_report

    data = vormap.load_data("sites.txt")
    regions = vormap_viz.compute_regions(data)
    analysis = analyze_shapes(regions, data)

    print(format_shape_report(analysis))
    export_shape_json(analysis, "shapes.json")
    export_shape_csv(analysis, "shapes.csv")

Usage (CLI)::

    python vormap_shape.py sites.txt
    python vormap_shape.py sites.txt --json shapes.json
    python vormap_shape.py sites.txt --csv shapes.csv
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import vormap
import vormap_viz

from vormap_geometry import (
    polygon_area as _polygon_area,
    polygon_perimeter as _polygon_perimeter,
    polygon_centroid as _polygon_centroid,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TWO_PI = 2.0 * math.pi
_FOUR_PI = 4.0 * math.pi

# Shape class thresholds based on IPQ (compactness)
_CLASS_THRESHOLDS = [
    (0.80, "compact"),          # IPQ >= 0.80
    (0.60, "regular"),          # 0.60 <= IPQ < 0.80
    (0.40, "moderate"),         # 0.40 <= IPQ < 0.60
    (0.20, "elongated"),        # 0.20 <= IPQ < 0.40
    (0.00, "highly_elongated"), # IPQ < 0.20
]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_shape(ipq: float) -> str:
    """Classify a cell's shape based on compactness (IPQ)."""
    for threshold, label in _CLASS_THRESHOLDS:
        if ipq >= threshold:
            return label
    return "highly_elongated"


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _min_bounding_rectangle(vertices: List[Tuple[float, float]]) -> Dict[str, float]:
    """Compute the minimum-area bounding rectangle via rotating calipers.

    Uses the convex hull edge rotation approach: the optimal bounding
    rectangle is always aligned with one edge of the convex hull.

    Returns dict with keys: width, height, area, angle (degrees).
    """
    if len(vertices) < 3:
        return {"width": 0.0, "height": 0.0, "area": 0.0, "angle": 0.0}

    # Convex hull (Graham scan — vertices are already a convex polygon
    # for Voronoi cells, but we sort to be safe)
    hull = _convex_hull(vertices)
    if len(hull) < 3:
        return {"width": 0.0, "height": 0.0, "area": 0.0, "angle": 0.0}

    best_area = float("inf")
    best_width = 0.0
    best_height = 0.0
    best_angle = 0.0

    n = len(hull)
    for i in range(n):
        # Edge vector
        ex = hull[(i + 1) % n][0] - hull[i][0]
        ey = hull[(i + 1) % n][1] - hull[i][1]
        edge_len = math.sqrt(ex * ex + ey * ey)
        if edge_len < 1e-12:
            continue

        # Normalise
        ux, uy = ex / edge_len, ey / edge_len

        # Project all hull points onto edge (u) and perpendicular (v)
        min_u = float("inf")
        max_u = float("-inf")
        min_v = float("inf")
        max_v = float("-inf")

        for px, py in hull:
            dx, dy = px - hull[i][0], py - hull[i][1]
            proj_u = dx * ux + dy * uy
            proj_v = -dx * uy + dy * ux

            min_u = min(min_u, proj_u)
            max_u = max(max_u, proj_u)
            min_v = min(min_v, proj_v)
            max_v = max(max_v, proj_v)

        w = max_u - min_u
        h = max_v - min_v
        a = w * h

        if a < best_area:
            best_area = a
            best_width = max(w, h)
            best_height = min(w, h)
            best_angle = math.degrees(math.atan2(uy, ux))

    return {
        "width": best_width,
        "height": best_height,
        "area": best_area,
        "angle": best_angle % 180.0,  # Normalise to [0, 180)
    }


def _convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Andrew's monotone chain convex hull."""
    pts = sorted(set(points))
    if len(pts) <= 2:
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


def _mean(values: List[float]) -> float:
    """Arithmetic mean."""
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float]) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def _safe_div(a: float, b: float) -> float:
    """Division with zero guard."""
    return a / b if abs(b) > 1e-12 else 0.0


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_cell(
    seed: Tuple[float, float],
    vertices: List[Tuple[float, float]],
) -> Dict[str, Any]:
    """Compute shape metrics for a single Voronoi cell.

    Parameters
    ----------
    seed : tuple
        (x, y) generator point.
    vertices : list
        Ordered polygon vertices [(x, y), ...].

    Returns
    -------
    dict
        Shape metrics for this cell.
    """
    area = _polygon_area(vertices)
    perimeter = _polygon_perimeter(vertices)
    centroid = _polygon_centroid(vertices)

    # Compactness: Isoperimetric Quotient (IPQ)
    ipq = _safe_div(_FOUR_PI * area, perimeter * perimeter)
    ipq = min(ipq, 1.0)  # Clamp numerical noise

    # Shape Index: 1.0 = circle, higher = more irregular
    si = _safe_div(perimeter, 2.0 * math.sqrt(math.pi * area)) if area > 0 else 0.0

    # Minimum bounding rectangle
    mbr = _min_bounding_rectangle(vertices)

    # Elongation: aspect ratio of MBR (>= 1.0)
    elongation = _safe_div(mbr["width"], mbr["height"]) if mbr["height"] > 0 else 1.0

    # Rectangularity: how well cell fills its MBR
    rectangularity = _safe_div(area, mbr["area"]) if mbr["area"] > 0 else 0.0
    rectangularity = min(rectangularity, 1.0)

    # Centroid displacement: normalised distance seed→centroid
    if area > 0:
        equiv_radius = math.sqrt(area / math.pi)
        displacement = _safe_div(_distance(seed, centroid), equiv_radius)
    else:
        displacement = 0.0

    # Orientation: major axis angle from MBR
    orientation = mbr["angle"]

    # Number of vertices (proxy for complexity)
    n_vertices = len(vertices)

    shape_class = _classify_shape(ipq)

    return {
        "seed": list(seed),
        "area": round(area, 4),
        "perimeter": round(perimeter, 4),
        "centroid": [round(centroid[0], 4), round(centroid[1], 4)],
        "n_vertices": n_vertices,
        "compactness": round(ipq, 6),
        "shape_index": round(si, 6),
        "elongation": round(elongation, 6),
        "rectangularity": round(rectangularity, 6),
        "centroid_displacement": round(displacement, 6),
        "orientation": round(orientation, 2),
        "mbr_width": round(mbr["width"], 4),
        "mbr_height": round(mbr["height"], 4),
        "shape_class": shape_class,
    }


def analyze_shapes(
    regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
    data: Optional[List[Tuple[float, float]]] = None,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    exclude_boundary: bool = False,
    boundary_tolerance: float = 1.0,
) -> Dict[str, Any]:
    """Run full shape analysis on a Voronoi tessellation.

    Parameters
    ----------
    regions : dict
        Voronoi regions as {seed: [vertices]}.
    data : list, optional
        Original seed points.
    bounds : tuple, optional
        (south, north, west, east). If None, inferred from vormap globals.
    exclude_boundary : bool
        If True, exclude cells touching the diagram boundary.
    boundary_tolerance : float
        Distance threshold for boundary detection.

    Returns
    -------
    dict with keys:
        - ``cells``: list of per-cell shape metrics
        - ``summary``: aggregate statistics
        - ``distribution``: shape class histogram
    """
    if bounds is None:
        bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)

    cells = []
    for seed, vertices in regions.items():
        if not vertices or len(vertices) < 3:
            continue

        # Optionally exclude boundary cells
        if exclude_boundary:
            on_boundary = any(
                _point_near_boundary(v, bounds, boundary_tolerance)
                for v in vertices
            )
            if on_boundary:
                continue

        cell = analyze_cell(seed, vertices)
        cells.append(cell)

    if not cells:
        return {
            "cells": [],
            "summary": _empty_summary(),
            "distribution": {},
        }

    # Aggregate statistics
    summary = _compute_summary(cells)

    # Shape class distribution
    distribution = {}
    for cell in cells:
        cls = cell["shape_class"]
        distribution[cls] = distribution.get(cls, 0) + 1

    return {
        "cells": cells,
        "summary": summary,
        "distribution": distribution,
    }


def _point_near_boundary(
    point: Tuple[float, float],
    bounds: Tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    """Check if a vertex is near the diagram boundary."""
    south, north, west, east = bounds
    x, y = point
    return (
        abs(x - west) < tolerance
        or abs(x - east) < tolerance
        or abs(y - south) < tolerance
        or abs(y - north) < tolerance
    )


def _empty_summary() -> Dict[str, Any]:
    """Return an empty summary when no cells are analysable."""
    metrics = [
        "compactness", "shape_index", "elongation",
        "rectangularity", "centroid_displacement",
    ]
    summary = {"cell_count": 0}
    for m in metrics:
        summary[m] = {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    summary["most_compact_seed"] = None
    summary["most_elongated_seed"] = None
    summary["mean_orientation"] = 0.0
    summary["orientation_std"] = 0.0
    summary["mean_n_vertices"] = 0.0
    return summary


def _compute_summary(cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate shape statistics."""
    metrics = [
        "compactness", "shape_index", "elongation",
        "rectangularity", "centroid_displacement",
    ]

    summary: Dict[str, Any] = {"cell_count": len(cells)}

    for m in metrics:
        values = [c[m] for c in cells]
        summary[m] = {
            "mean": round(_mean(values), 6),
            "std": round(_std(values), 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
        }

    # Most compact / most elongated
    most_compact = min(cells, key=lambda c: c["shape_index"])
    most_elongated = max(cells, key=lambda c: c["elongation"])
    summary["most_compact_seed"] = most_compact["seed"]
    summary["most_elongated_seed"] = most_elongated["seed"]

    # Orientation statistics
    orientations = [c["orientation"] for c in cells]
    summary["mean_orientation"] = round(_mean(orientations), 2)
    summary["orientation_std"] = round(_std(orientations), 2)

    # Vertex count
    nv = [c["n_vertices"] for c in cells]
    summary["mean_n_vertices"] = round(_mean(nv), 2)

    return summary


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_shape_report(analysis: Dict[str, Any]) -> str:
    """Format shape analysis as a human-readable text report.

    Parameters
    ----------
    analysis : dict
        Output from ``analyze_shapes()``.

    Returns
    -------
    str
        Multi-line text report.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  VORONOI CELL SHAPE ANALYSIS")
    lines.append("=" * 60)

    summary = analysis["summary"]
    lines.append(f"\n  Total cells analysed: {summary['cell_count']}")

    if summary["cell_count"] == 0:
        lines.append("  No cells to analyse.")
        return "\n".join(lines)

    lines.append(f"  Mean vertices per cell: {summary['mean_n_vertices']:.1f}")
    lines.append("")

    # Metric table
    lines.append("  Metric                 Mean      Std       Min       Max")
    lines.append("  " + "-" * 56)
    for m in ["compactness", "shape_index", "elongation", "rectangularity",
              "centroid_displacement"]:
        s = summary[m]
        label = m.replace("_", " ").title()
        lines.append(
            f"  {label:<22s} {s['mean']:>8.4f}  {s['std']:>8.4f}  "
            f"{s['min']:>8.4f}  {s['max']:>8.4f}"
        )

    lines.append("")
    lines.append(f"  Mean orientation: {summary['mean_orientation']:.1f}° "
                 f"(σ = {summary['orientation_std']:.1f}°)")

    # Most notable cells
    mc = summary["most_compact_seed"]
    me = summary["most_elongated_seed"]
    if mc:
        lines.append(f"  Most compact cell: seed ({mc[0]:.1f}, {mc[1]:.1f})")
    if me:
        lines.append(f"  Most elongated cell: seed ({me[0]:.1f}, {me[1]:.1f})")

    # Distribution
    dist = analysis.get("distribution", {})
    if dist:
        lines.append("")
        lines.append("  Shape Class Distribution")
        lines.append("  " + "-" * 36)
        total = sum(dist.values())
        # Sort by class severity
        class_order = ["compact", "regular", "moderate", "elongated",
                       "highly_elongated"]
        for cls in class_order:
            count = dist.get(cls, 0)
            pct = 100.0 * count / total if total > 0 else 0.0
            bar = "█" * int(pct / 2.5)
            lines.append(f"  {cls:<20s} {count:>4d} ({pct:>5.1f}%) {bar}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_shape_json(
    analysis: Dict[str, Any],
    path: str,
) -> None:
    """Export shape analysis to JSON.

    Parameters
    ----------
    analysis : dict
        Output from ``analyze_shapes()``.
    path : str
        Output file path.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(analysis, f, indent=2)


def export_shape_csv(
    analysis: Dict[str, Any],
    path: str,
) -> None:
    """Export per-cell shape metrics to CSV.

    Parameters
    ----------
    analysis : dict
        Output from ``analyze_shapes()``.
    path : str
        Output file path.
    """
    cells = analysis.get("cells", [])
    if not cells:
        return

    os.makedirs(os.path.dirname(os.path.abspath(path)) if os.path.dirname(path) else ".", exist_ok=True)

    fieldnames = [
        "seed_x", "seed_y", "area", "perimeter", "n_vertices",
        "compactness", "shape_index", "elongation", "rectangularity",
        "centroid_displacement", "orientation", "shape_class",
    ]

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for cell in cells:
            writer.writerow({
                "seed_x": cell["seed"][0],
                "seed_y": cell["seed"][1],
                "area": cell["area"],
                "perimeter": cell["perimeter"],
                "n_vertices": cell["n_vertices"],
                "compactness": cell["compactness"],
                "shape_index": cell["shape_index"],
                "elongation": cell["elongation"],
                "rectangularity": cell["rectangularity"],
                "centroid_displacement": cell["centroid_displacement"],
                "orientation": cell["orientation"],
                "shape_class": cell["shape_class"],
            })


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    """Command-line entry point for shape analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyse Voronoi cell shapes — compactness, elongation, "
                    "shape index, orientation, and more.",
    )
    parser.add_argument("input", help="Path to seed-point data file")
    parser.add_argument("--json", metavar="PATH", help="Export analysis to JSON")
    parser.add_argument("--csv", metavar="PATH", help="Export per-cell metrics to CSV")
    parser.add_argument(
        "--exclude-boundary", action="store_true",
        help="Exclude cells touching the diagram boundary",
    )
    parser.add_argument(
        "--boundary-tolerance", type=float, default=1.0,
        help="Distance threshold for boundary detection (default: 1.0)",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress text report output",
    )

    args = parser.parse_args(argv)

    data = vormap.load_data(args.input)
    regions = vormap_viz.compute_regions(data)
    analysis = analyze_shapes(
        regions,
        data,
        exclude_boundary=args.exclude_boundary,
        boundary_tolerance=args.boundary_tolerance,
    )

    if not args.quiet:
        print(format_shape_report(analysis))

    if args.json:
        export_shape_json(analysis, args.json)
        print(f"  JSON exported to {args.json}")

    if args.csv:
        export_shape_csv(analysis, args.csv)
        print(f"  CSV exported to {args.csv}")


if __name__ == "__main__":
    main()
