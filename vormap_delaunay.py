"""Delaunay triangulation quality analysis for Voronoi tessellations.

Since the Delaunay triangulation is the dual of the Voronoi diagram,
every Voronoi tessellation has a corresponding Delaunay mesh.  This
module analyses the quality of that mesh — useful for finite-element
meshing, terrain modelling, and understanding the spatial structure of
point distributions.

Analyses provided
-----------------
- **Triangle quality metrics:**
  Aspect ratio (circumradius / 2×inradius; 1.0 = equilateral),
  minimum angle (ideally 60°), area, edge lengths, and per-triangle
  quality classification (excellent / good / fair / poor / degenerate).

- **Mesh-level statistics:**
  Histogram of quality classes, angle distributions, area uniformity
  (coefficient of variation), edge-length statistics, Euler
  characteristic verification (V - E + F = 2 for convex hull).

- **Angle spectrum:**
  Distribution of all interior angles across every triangle.
  Ideal Delaunay has no angle < 0° or > 180°; quality degrades as
  min-angle shrinks.  Reports percentiles and the number of triangles
  with angles below configurable thresholds (e.g. < 20°, < 10°).

- **Edge analysis:**
  Min/max/mean/median/std of edge lengths, ratio of longest to
  shortest (uniformity indicator), and per-triangle edge-length
  range.

- **Quality map export:**
  JSON export of per-triangle metrics for downstream visualisation
  or GIS integration.

Typical usage::

    from vormap_delaunay import (
        delaunay_quality, format_report, export_json
    )

    points = [(1,1), (3,1), (2,4), (5,3), (0,3)]
    result = delaunay_quality(points)
    print(format_report(result))
    export_json(result, "mesh_quality.json")

All functions are pure and depend only on the Python standard library
(plus ``math``).  No scipy/numpy required.
"""


import json
import math
import os
from vormap_utils import euclidean as _edge_length
import statistics

from vormap_utils import euclidean as _edge_length

__all__ = [
    "delaunay_triangulate",
    "triangle_quality",
    "mesh_statistics",
    "angle_spectrum",
    "edge_analysis",
    "delaunay_quality",
    "format_report",
    "export_json",
    "classify_triangle",
]


# ═══════════════════════════════════════════════════════════════════
#  Pure-Python Delaunay Triangulation (Bowyer-Watson)
# ═══════════════════════════════════════════════════════════════════

def _circumcircle(p1, p2, p3):
    """Return (cx, cy, r²) of the circumscribed circle of triangle p1-p2-p3.

    Uses the determinant formula for numerical stability.
    Returns None if the three points are collinear.
    """
    ax, ay = p1
    bx, by = p2
    cx, cy = p3

    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None  # collinear

    ux = ((ax * ax + ay * ay) * (by - cy) +
          (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) +
          (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d

    r2 = (ax - ux) ** 2 + (ay - uy) ** 2
    return (ux, uy, r2)


def _in_circumcircle(p, cc):
    """Check if point p is inside circumcircle cc = (cx, cy, r²)."""
    dx = p[0] - cc[0]
    dy = p[1] - cc[1]
    return dx * dx + dy * dy < cc[2] - 1e-10


def delaunay_triangulate(points):
    """Compute the Delaunay triangulation of a 2D point set.

    Uses the Bowyer-Watson incremental algorithm.

    Parameters
    ----------
    points : list of (float, float)
        At least 3 non-collinear points.

    Returns
    -------
    list of (int, int, int)
        Triangles as index-triples into the original *points* list.

    Raises
    ------
    ValueError
        If fewer than 3 points or all points are collinear.
    """
    if len(points) < 3:
        raise ValueError("Need at least 3 points for triangulation")

    pts = [(float(x), float(y)) for x, y in points]
    n = len(pts)

    # Compute bounding super-triangle
    min_x = min(p[0] for p in pts)
    max_x = max(p[0] for p in pts)
    min_y = min(p[1] for p in pts)
    max_y = max(p[1] for p in pts)

    dx = max_x - min_x
    dy = max_y - min_y
    dmax = max(dx, dy, 1e-6)
    mid_x = (min_x + max_x) / 2.0
    mid_y = (min_y + max_y) / 2.0

    # Super-triangle vertices (indices n, n+1, n+2)
    margin = 20.0 * dmax
    p_super = [
        (mid_x - margin, mid_y - margin),
        (mid_x + margin, mid_y - margin),
        (mid_x, mid_y + margin * 2),
    ]
    all_pts = pts + p_super

    # Start with the super-triangle
    triangles = [(n, n + 1, n + 2)]
    cc_cache = {}

    def get_cc(tri):
        if tri not in cc_cache:
            cc_cache[tri] = _circumcircle(
                all_pts[tri[0]], all_pts[tri[1]], all_pts[tri[2]]
            )
        return cc_cache[tri]

    # Insert each point
    for i in range(n):
        p = all_pts[i]
        bad = []
        for tri in triangles:
            cc = get_cc(tri)
            if cc is not None and _in_circumcircle(p, cc):
                bad.append(tri)

        # Find the boundary polygon (edges shared by exactly one bad triangle)
        edge_count = {}
        for tri in bad:
            edges = [
                (tri[0], tri[1]),
                (tri[1], tri[2]),
                (tri[2], tri[0]),
            ]
            for e in edges:
                # normalise edge direction
                key = (min(e), max(e))
                edge_count[key] = edge_count.get(key, 0) + 1

        boundary = [e for e, cnt in edge_count.items() if cnt == 1]

        # Remove bad triangles
        triangles = [t for t in triangles if t not in bad]
        for t in bad:
            cc_cache.pop(t, None)

        # Create new triangles from boundary edges to new point
        for e in boundary:
            new_tri = tuple(sorted([e[0], e[1], i]))
            triangles.append(new_tri)

    # Remove triangles that share vertices with the super-triangle
    result = []
    for tri in triangles:
        if tri[0] < n and tri[1] < n and tri[2] < n:
            result.append(tri)

    if not result:
        raise ValueError("All points appear to be collinear")

    return result


# ═══════════════════════════════════════════════════════════════════
#  Triangle Quality Metrics
# ═══════════════════════════════════════════════════════════════════


def _triangle_area(p1, p2, p3):
    """Signed area via the shoelace formula (returns absolute value)."""
    return abs(
        (p2[0] - p1[0]) * (p3[1] - p1[1]) -
        (p3[0] - p1[0]) * (p2[1] - p1[1])
    ) / 2.0


def _angle_at_vertex(pa, pb, pc):
    """Angle at vertex pa in triangle pa-pb-pc, in degrees."""
    v1x, v1y = pb[0] - pa[0], pb[1] - pa[1]
    v2x, v2y = pc[0] - pa[0], pc[1] - pa[1]
    dot = v1x * v2x + v1y * v2y
    m1 = math.sqrt(v1x * v1x + v1y * v1y)
    m2 = math.sqrt(v2x * v2x + v2y * v2y)
    if m1 < 1e-15 or m2 < 1e-15:
        return 0.0
    cos_a = max(-1.0, min(1.0, dot / (m1 * m2)))
    return math.degrees(math.acos(cos_a))


def classify_triangle(aspect_ratio, min_angle):
    """Classify a triangle's quality.

    Parameters
    ----------
    aspect_ratio : float
        Circumradius / (2 × inradius).  1.0 = equilateral (perfect).
    min_angle : float
        Smallest interior angle in degrees.

    Returns
    -------
    str
        One of 'excellent', 'good', 'fair', 'poor', 'degenerate'.
    """
    if min_angle >= 50 and aspect_ratio <= 1.1:
        return "excellent"
    elif min_angle >= 30 and aspect_ratio <= 2.0:
        return "good"
    elif min_angle >= 15 and aspect_ratio <= 5.0:
        return "fair"
    elif min_angle >= 5:
        return "poor"
    else:
        return "degenerate"


def triangle_quality(points, tri):
    """Compute quality metrics for a single triangle.

    Parameters
    ----------
    points : list of (float, float)
        All points.
    tri : (int, int, int)
        Triangle as index-triple.

    Returns
    -------
    dict with keys:
        indices, vertices, area, edges (a, b, c),
        angles (A, B, C in degrees), min_angle, max_angle,
        aspect_ratio, circumradius, inradius, quality_class.
    """
    p1, p2, p3 = points[tri[0]], points[tri[1]], points[tri[2]]

    a = _edge_length(p2, p3)  # opposite to p1
    b = _edge_length(p1, p3)  # opposite to p2
    c = _edge_length(p1, p2)  # opposite to p3

    area = _triangle_area(p1, p2, p3)
    perimeter = a + b + c
    semi = perimeter / 2.0

    # Inradius  r = Area / s
    inradius = area / semi if semi > 1e-15 else 0.0

    # Circumradius  R = (a·b·c) / (4·Area)
    circumradius = (a * b * c) / (4.0 * area) if area > 1e-15 else float("inf")

    # Aspect ratio: R / (2r)  — equilateral triangle gives exactly 1.0
    aspect_ratio = circumradius / (2.0 * inradius) if inradius > 1e-15 else float("inf")

    angle_a = _angle_at_vertex(points[tri[0]], points[tri[1]], points[tri[2]])
    angle_b = _angle_at_vertex(points[tri[1]], points[tri[0]], points[tri[2]])
    angle_c = 180.0 - angle_a - angle_b

    min_angle = min(angle_a, angle_b, angle_c)
    max_angle = max(angle_a, angle_b, angle_c)

    quality_class = classify_triangle(aspect_ratio, min_angle)

    return {
        "indices": tri,
        "vertices": (p1, p2, p3),
        "area": area,
        "edges": {"a": a, "b": b, "c": c},
        "angles": {"A": angle_a, "B": angle_b, "C": angle_c},
        "min_angle": min_angle,
        "max_angle": max_angle,
        "aspect_ratio": aspect_ratio,
        "circumradius": circumradius,
        "inradius": inradius,
        "quality_class": quality_class,
    }


# ═══════════════════════════════════════════════════════════════════
#  Mesh-Level Statistics
# ═══════════════════════════════════════════════════════════════════

def mesh_statistics(points, triangles, tri_metrics=None):
    """Compute mesh-level quality statistics.

    Parameters
    ----------
    points : list of (float, float)
    triangles : list of (int, int, int)
    tri_metrics : list of dict, optional
        Pre-computed per-triangle metrics (from triangle_quality).
        Computed fresh if not provided.

    Returns
    -------
    dict with mesh-level statistics.
    """
    if not triangles:
        return {"error": "No triangles"}

    if tri_metrics is None:
        tri_metrics = [triangle_quality(points, t) for t in triangles]

    n_tri = len(tri_metrics)

    # Quality class histogram
    classes = {"excellent": 0, "good": 0, "fair": 0, "poor": 0, "degenerate": 0}
    for m in tri_metrics:
        classes[m["quality_class"]] += 1

    areas = [m["area"] for m in tri_metrics]
    aspects = [m["aspect_ratio"] for m in tri_metrics if math.isfinite(m["aspect_ratio"])]
    min_angles = [m["min_angle"] for m in tri_metrics]
    max_angles = [m["max_angle"] for m in tri_metrics]

    # Area uniformity: coefficient of variation
    area_mean = statistics.mean(areas) if areas else 0.0
    area_cv = (statistics.stdev(areas) / area_mean) if len(areas) > 1 and area_mean > 0 else 0.0

    # Euler characteristic: V - E + F = 2 (for convex hull triangulation)
    n_vertices = len(set(idx for t in triangles for idx in t))
    edges = set()
    for t in triangles:
        edges.add((min(t[0], t[1]), max(t[0], t[1])))
        edges.add((min(t[1], t[2]), max(t[1], t[2])))
        edges.add((min(t[0], t[2]), max(t[0], t[2])))
    n_edges = len(edges)
    euler = n_vertices - n_edges + n_tri

    return {
        "num_triangles": n_tri,
        "num_vertices": n_vertices,
        "num_edges": n_edges,
        "euler_characteristic": euler,
        "quality_histogram": classes,
        "quality_pct": {k: round(100 * v / n_tri, 1) for k, v in classes.items()},
        "area": {
            "total": sum(areas),
            "min": min(areas),
            "max": max(areas),
            "mean": area_mean,
            "cv": round(area_cv, 4),
        },
        "aspect_ratio": {
            "min": min(aspects) if aspects else None,
            "max": max(aspects) if aspects else None,
            "mean": statistics.mean(aspects) if aspects else None,
            "median": statistics.median(aspects) if aspects else None,
        },
        "min_angle": {
            "overall_min": min(min_angles),
            "mean": statistics.mean(min_angles),
            "p5": _percentile(sorted(min_angles), 5),
            "p25": _percentile(sorted(min_angles), 25),
        },
        "max_angle": {
            "overall_max": max(max_angles),
            "mean": statistics.mean(max_angles),
            "p75": _percentile(sorted(max_angles), 75),
            "p95": _percentile(sorted(max_angles), 95),
        },
    }


def _percentile(sorted_vals, pct):
    """Simple percentile on a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    k = (pct / 100.0) * (len(sorted_vals) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


# ═══════════════════════════════════════════════════════════════════
#  Angle Spectrum
# ═══════════════════════════════════════════════════════════════════

def angle_spectrum(tri_metrics, thresholds=None):
    """Analyse the distribution of all interior angles.

    Parameters
    ----------
    tri_metrics : list of dict
        Per-triangle metrics from triangle_quality.
    thresholds : list of float, optional
        Angle thresholds (degrees) to count triangles below.
        Default: [5, 10, 15, 20, 25, 30].

    Returns
    -------
    dict with angle distribution statistics.
    """
    if thresholds is None:
        thresholds = [5, 10, 15, 20, 25, 30]

    all_angles = []
    for m in tri_metrics:
        all_angles.extend([m["angles"]["A"], m["angles"]["B"], m["angles"]["C"]])

    all_angles_sorted = sorted(all_angles)
    n = len(all_angles_sorted)

    # Count triangles with min angle below each threshold
    below = {}
    for t in thresholds:
        count = sum(1 for m in tri_metrics if m["min_angle"] < t)
        below[f"below_{t}deg"] = count

    # Angle histogram (6-degree bins from 0 to 180)
    bins = list(range(0, 181, 6))
    histogram = [0] * (len(bins) - 1)
    for a in all_angles:
        idx = min(int(a / 6), len(histogram) - 1)
        histogram[idx] += 1

    return {
        "total_angles": n,
        "min": all_angles_sorted[0] if n else 0.0,
        "max": all_angles_sorted[-1] if n else 0.0,
        "mean": statistics.mean(all_angles) if n else 0.0,
        "median": statistics.median(all_angles) if n else 0.0,
        "stdev": statistics.stdev(all_angles) if n > 1 else 0.0,
        "percentiles": {
            "p5": _percentile(all_angles_sorted, 5),
            "p10": _percentile(all_angles_sorted, 10),
            "p25": _percentile(all_angles_sorted, 25),
            "p50": _percentile(all_angles_sorted, 50),
            "p75": _percentile(all_angles_sorted, 75),
            "p90": _percentile(all_angles_sorted, 90),
            "p95": _percentile(all_angles_sorted, 95),
        },
        "threshold_counts": below,
        "histogram_bins": [(bins[i], bins[i + 1]) for i in range(len(histogram))],
        "histogram_counts": histogram,
    }


# ═══════════════════════════════════════════════════════════════════
#  Edge Analysis
# ═══════════════════════════════════════════════════════════════════

def edge_analysis(points, triangles, tri_metrics=None):
    """Analyse edge-length distribution of the Delaunay mesh.

    Parameters
    ----------
    points : list of (float, float)
    triangles : list of (int, int, int)
    tri_metrics : list of dict, optional

    Returns
    -------
    dict with edge length statistics and uniformity measures.
    """
    if tri_metrics is None:
        tri_metrics = [triangle_quality(points, t) for t in triangles]

    # Collect unique edge lengths
    edge_set = {}
    for m in tri_metrics:
        t = m["indices"]
        edges_in_tri = [
            (min(t[0], t[1]), max(t[0], t[1]), m["edges"]["c"]),  # edge p1-p2
            (min(t[1], t[2]), max(t[1], t[2]), m["edges"]["a"]),  # edge p2-p3
            (min(t[0], t[2]), max(t[0], t[2]), m["edges"]["b"]),  # edge p1-p3
        ]
        for i1, i2, length in edges_in_tri:
            edge_set[(i1, i2)] = length

    lengths = list(edge_set.values())
    lengths.sort()

    if not lengths:
        return {"error": "No edges"}

    min_len = lengths[0]
    max_len = lengths[-1]
    mean_len = statistics.mean(lengths)
    median_len = statistics.median(lengths)
    stdev_len = statistics.stdev(lengths) if len(lengths) > 1 else 0.0

    # Uniformity ratio: max/min (1.0 = perfectly uniform)
    uniformity_ratio = max_len / min_len if min_len > 1e-15 else float("inf")

    # CV of edge lengths
    cv = stdev_len / mean_len if mean_len > 1e-15 else 0.0

    # Per-triangle edge range (max_edge - min_edge within each triangle)
    tri_ranges = []
    for m in tri_metrics:
        e = m["edges"]
        tri_ranges.append(max(e["a"], e["b"], e["c"]) - min(e["a"], e["b"], e["c"]))

    return {
        "num_edges": len(lengths),
        "min": min_len,
        "max": max_len,
        "mean": mean_len,
        "median": median_len,
        "stdev": stdev_len,
        "uniformity_ratio": uniformity_ratio,
        "cv": round(cv, 4),
        "percentiles": {
            "p5": _percentile(lengths, 5),
            "p25": _percentile(lengths, 25),
            "p50": _percentile(lengths, 50),
            "p75": _percentile(lengths, 75),
            "p95": _percentile(lengths, 95),
        },
        "per_triangle_range": {
            "min": min(tri_ranges) if tri_ranges else 0.0,
            "max": max(tri_ranges) if tri_ranges else 0.0,
            "mean": statistics.mean(tri_ranges) if tri_ranges else 0.0,
        },
    }


# ═══════════════════════════════════════════════════════════════════
#  Full Analysis Pipeline
# ═══════════════════════════════════════════════════════════════════

def delaunay_quality(points, angle_thresholds=None):
    """Run the complete Delaunay quality analysis pipeline.

    Parameters
    ----------
    points : list of (float, float)
        Input point set (at least 3 non-collinear).
    angle_thresholds : list of float, optional
        Angle thresholds for the spectrum analysis.

    Returns
    -------
    dict with keys: points, triangles, per_triangle, mesh, angles, edges.
    """
    pts = [(float(x), float(y)) for x, y in points]
    triangles = delaunay_triangulate(pts)

    per_tri = [triangle_quality(pts, t) for t in triangles]
    mesh = mesh_statistics(pts, triangles, per_tri)
    angles = angle_spectrum(per_tri, thresholds=angle_thresholds)
    edges = edge_analysis(pts, triangles, per_tri)

    return {
        "num_points": len(pts),
        "points": pts,
        "triangles": triangles,
        "per_triangle": per_tri,
        "mesh": mesh,
        "angles": angles,
        "edges": edges,
    }


# ═══════════════════════════════════════════════════════════════════
#  Reporting & Export
# ═══════════════════════════════════════════════════════════════════

def format_report(result):
    """Format a human-readable quality report.

    Parameters
    ----------
    result : dict
        Output of delaunay_quality.

    Returns
    -------
    str
    """
    lines = []
    lines.append("=" * 60)
    lines.append("  DELAUNAY TRIANGULATION QUALITY REPORT")
    lines.append("=" * 60)
    lines.append("")

    m = result["mesh"]
    lines.append(f"Points:    {result['num_points']}")
    lines.append(f"Triangles: {m['num_triangles']}")
    lines.append(f"Edges:     {m['num_edges']}")
    lines.append(f"Vertices:  {m['num_vertices']}")
    lines.append(f"Euler χ:   {m['euler_characteristic']}  (expect 2 for convex hull)")
    lines.append("")

    # Quality histogram
    lines.append("── Quality Classification ──")
    total = m["num_triangles"]
    for cls in ["excellent", "good", "fair", "poor", "degenerate"]:
        cnt = m["quality_histogram"][cls]
        pct = m["quality_pct"][cls]
        bar = "█" * int(pct / 2)
        lines.append(f"  {cls:12s}  {cnt:4d}  ({pct:5.1f}%)  {bar}")
    lines.append("")

    # Aspect ratio
    ar = m["aspect_ratio"]
    if ar["mean"] is not None:
        lines.append("── Aspect Ratio (1.0 = equilateral) ──")
        lines.append(f"  Min:    {ar['min']:.3f}")
        lines.append(f"  Max:    {ar['max']:.3f}")
        lines.append(f"  Mean:   {ar['mean']:.3f}")
        lines.append(f"  Median: {ar['median']:.3f}")
        lines.append("")

    # Angles
    a = result["angles"]
    lines.append("── Angle Spectrum ──")
    lines.append(f"  Min angle:  {a['min']:.1f}°")
    lines.append(f"  Max angle:  {a['max']:.1f}°")
    lines.append(f"  Mean:       {a['mean']:.1f}°")
    lines.append(f"  Std dev:    {a['stdev']:.1f}°")
    lines.append("")

    # Thresholds
    tc = a.get("threshold_counts", {})
    if tc:
        lines.append("  Triangles with min angle below threshold:")
        for key, cnt in sorted(tc.items()):
            lines.append(f"    {key}: {cnt}")
        lines.append("")

    # Edges
    e = result["edges"]
    lines.append("── Edge Lengths ──")
    lines.append(f"  Min:     {e['min']:.4f}")
    lines.append(f"  Max:     {e['max']:.4f}")
    lines.append(f"  Mean:    {e['mean']:.4f}")
    lines.append(f"  Median:  {e['median']:.4f}")
    lines.append(f"  CV:      {e['cv']:.4f}")
    lines.append(f"  Ratio:   {e['uniformity_ratio']:.2f}")
    lines.append("")

    # Area
    area = m["area"]
    lines.append("── Triangle Areas ──")
    lines.append(f"  Total:   {area['total']:.4f}")
    lines.append(f"  Min:     {area['min']:.4f}")
    lines.append(f"  Max:     {area['max']:.4f}")
    lines.append(f"  Mean:    {area['mean']:.4f}")
    lines.append(f"  CV:      {area['cv']:.4f}")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


def export_json(result, path):
    """Export quality analysis to a JSON file.

    Parameters
    ----------
    result : dict
        Output of delaunay_quality.
    path : str
        Output file path.
    """
    # Build a JSON-safe copy (omit large vertex arrays per-triangle)
    export = {
        "num_points": result["num_points"],
        "num_triangles": result["mesh"]["num_triangles"],
        "triangles": result["triangles"],
        "mesh": result["mesh"],
        "angles": result["angles"],
        "edges": result["edges"],
        "per_triangle": [
            {
                "indices": m["indices"],
                "area": round(m["area"], 6),
                "aspect_ratio": round(m["aspect_ratio"], 4) if math.isfinite(m["aspect_ratio"]) else "inf",
                "min_angle": round(m["min_angle"], 2),
                "max_angle": round(m["max_angle"], 2),
                "quality_class": m["quality_class"],
            }
            for m in result["per_triangle"]
        ],
    }

    with open(path, "w") as f:
        json.dump(export, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python vormap_delaunay.py <points_file> [--json out.json]")
        print("  points_file: one 'x y' pair per line")
        sys.exit(1)

    points_path = sys.argv[1]
    json_out = None
    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        if idx + 1 < len(sys.argv):
            json_out = sys.argv[idx + 1]

    pts = []
    with open(points_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                pts.append((float(parts[0]), float(parts[1])))

    result = delaunay_quality(pts)
    print(format_report(result))

    if json_out:
        export_json(result, json_out)
        print(f"\nExported to {json_out}")
