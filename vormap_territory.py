"""Territorial Analysis for Voronoi Diagrams.

Analyses Voronoi tessellations as territories — computing metrics about
how space is divided, which regions dominate, how much border pressure
each region faces, and whether the overall division is balanced or
heavily skewed.

Applications include ecology (animal territories), urban planning
(service areas), market analysis (competitive zones), and resource
allocation studies.

Example::

    import vormap, vormap_viz
    from vormap_territory import (
        analyze_territories, format_territory_report,
        export_territory_json, export_territory_csv,
    )

    data = vormap.load_data("sites.txt")
    regions = vormap_viz.compute_regions(data)
    analysis = analyze_territories(regions, data)

    print(format_territory_report(analysis))
    export_territory_json(analysis, "territory.json")
"""


import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import vormap


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

from vormap_geometry import (
    polygon_area as _polygon_area,
    polygon_perimeter as _polygon_perimeter,
    polygon_centroid as _polygon_centroid,
    edge_length as _edge_length,
)


def _point_near_boundary(
    point: Tuple[float, float],
    bounds: Tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    """Check if a point is near the diagram boundary."""
    south, north, west, east = bounds
    x, y = point
    return (
        abs(x - west) < tolerance
        or abs(x - east) < tolerance
        or abs(y - south) < tolerance
        or abs(y - north) < tolerance
    )


# ---------------------------------------------------------------------------
# Shared border detection
# ---------------------------------------------------------------------------

def _find_shared_borders(
    regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
) -> Dict[
    Tuple[Tuple[float, float], Tuple[float, float]],
    float,
]:
    """Find shared border lengths between adjacent regions.

    Two regions share a border if they have two or more vertices that
    are very close (within tolerance).  The shared border length is
    the sum of edge lengths along the shared vertices.

    Returns a dict mapping (seed_a, seed_b) → shared_length where
    seed_a < seed_b lexicographically.
    """
    tol = 1e-6

    # Build a mapping: rounded vertex → list of seeds that contain it
    vertex_to_seeds: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
    for seed, verts in regions.items():
        for v in verts:
            key = (round(v[0], 4), round(v[1], 4))
            if key not in vertex_to_seeds:
                vertex_to_seeds[key] = []
            vertex_to_seeds[key].append(seed)

    # For each pair of regions, find shared vertices
    pair_shared_verts: Dict[
        Tuple[Tuple[float, float], Tuple[float, float]],
        List[Tuple[float, float]],
    ] = {}
    for key, seeds in vertex_to_seeds.items():
        unique_seeds = list(set(seeds))
        for i in range(len(unique_seeds)):
            for j in range(i + 1, len(unique_seeds)):
                pair = tuple(sorted([unique_seeds[i], unique_seeds[j]]))
                if pair not in pair_shared_verts:
                    pair_shared_verts[pair] = []
                pair_shared_verts[pair].append(key)

    # Build a set of actual polygon edges (from both regions in each pair)
    # so we can chain shared vertices along real edges, not angular sort.
    region_edges: Dict[Tuple[float, float], set] = {}
    for seed, verts in regions.items():
        edge_set: set = set()
        n = len(verts)
        for k in range(n):
            v1 = (round(verts[k][0], 4), round(verts[k][1], 4))
            v2 = (round(verts[(k + 1) % n][0], 4), round(verts[(k + 1) % n][1], 4))
            edge_set.add((min(v1, v2), max(v1, v2)))
        region_edges[seed] = edge_set

    # Compute shared border length for each pair by summing actual
    # shared polygon edges (not phantom diagonals from angular sort)
    shared_borders: Dict[
        Tuple[Tuple[float, float], Tuple[float, float]],
        float,
    ] = {}
    for pair, verts in pair_shared_verts.items():
        if len(verts) < 2:
            continue
        shared_set = frozenset(verts)
        # Collect edges from both regions where both endpoints are shared
        edges_a = region_edges.get(pair[0], set())
        edges_b = region_edges.get(pair[1], set())
        all_edges = edges_a | edges_b
        total = 0.0
        for v1, v2 in all_edges:
            if v1 in shared_set and v2 in shared_set:
                total += _edge_length(v1, v2)
        if total > tol:
            shared_borders[pair] = total

    return shared_borders


# ---------------------------------------------------------------------------
# Territory classification
# ---------------------------------------------------------------------------

def _classify_territory(
    area: float, mean_area: float, std_area: float,
) -> str:
    """Classify a region as dominant/average/marginal based on area.

    - dominant:  area > mean + 1 std
    - marginal:  area < mean - 1 std (but >= 0)
    - average:   everything else
    """
    if std_area < 1e-12:
        return "average"
    if area > mean_area + std_area:
        return "dominant"
    elif area < max(0, mean_area - std_area):
        return "marginal"
    return "average"


# ---------------------------------------------------------------------------
# Gini coefficient
# ---------------------------------------------------------------------------

def _gini_coefficient(values: List[float]) -> float:
    """Compute the Gini coefficient of a list of non-negative values.

    Returns a value in [0, 1] where 0 = perfect equality and 1 = maximal
    inequality.  Returns 0.0 for empty or all-zero lists.
    """
    n = len(values)
    if n == 0:
        return 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    sorted_vals = sorted(values)
    cumulative = 0.0
    gini_sum = 0.0
    for i, v in enumerate(sorted_vals):
        cumulative += v
        gini_sum += (2 * (i + 1) - n - 1) * v
    return gini_sum / (n * total)


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def analyze_territories(
    regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
    data: Optional[List[Tuple[float, float]]] = None,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    boundary_tolerance: float = 1.0,
) -> Dict[str, Any]:
    """Run full territorial analysis on a Voronoi diagram.

    Parameters
    ----------
    regions : dict
        Voronoi regions as {seed: [vertices]}.
    data : list, optional
        Original seed points (used for metadata).
    bounds : tuple, optional
        (south, north, west, east) diagram bounds.  If None, inferred
        from vormap module globals.
    boundary_tolerance : float
        Distance threshold for considering a vertex "on the boundary".

    Returns
    -------
    dict with keys:
        - ``regions``: list of per-region territory metrics
        - ``summary``: aggregate territory statistics
        - ``shared_borders``: list of shared border records
    """
    if bounds is None:
        bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)

    # Compute per-region metrics
    region_metrics = []
    areas = []
    for seed, verts in regions.items():
        area = _polygon_area(verts)
        perim = _polygon_perimeter(verts)
        centroid = _polygon_centroid(verts)
        # Check if any vertex is on the diagram boundary
        is_frontier = any(
            _point_near_boundary(v, bounds, boundary_tolerance) for v in verts
        )
        areas.append(area)
        region_metrics.append({
            "seed": seed,
            "area": round(area, 4),
            "perimeter": round(perim, 4),
            "centroid": (round(centroid[0], 4), round(centroid[1], 4)),
            "vertex_count": len(verts),
            "is_frontier": is_frontier,
        })

    # Area statistics
    n = len(areas)
    if n == 0:
        return {"regions": [], "summary": _empty_summary(), "shared_borders": []}

    mean_area = sum(areas) / n
    variance = sum((a - mean_area) ** 2 for a in areas) / n
    std_area = math.sqrt(variance)
    sorted_areas = sorted(areas)
    median_area = (
        sorted_areas[n // 2]
        if n % 2 == 1
        else (sorted_areas[n // 2 - 1] + sorted_areas[n // 2]) / 2.0
    )
    cv = std_area / mean_area if mean_area > 0 else 0.0
    gini = _gini_coefficient(areas)
    total_area = sum(areas)

    # Classify each region
    for rm in region_metrics:
        rm["classification"] = _classify_territory(rm["area"], mean_area, std_area)
        rm["area_share"] = round(rm["area"] / total_area, 6) if total_area > 0 else 0.0

    # Shared borders and border pressure
    shared_borders = _find_shared_borders(regions)
    seed_neighbors: Dict[Tuple[float, float], int] = {}
    seed_shared_length: Dict[Tuple[float, float], float] = {}

    border_records = []
    for (s1, s2), length in shared_borders.items():
        border_records.append({
            "seed_a": s1,
            "seed_b": s2,
            "shared_length": round(length, 4),
        })
        for s in (s1, s2):
            seed_neighbors[s] = seed_neighbors.get(s, 0) + 1
            seed_shared_length[s] = seed_shared_length.get(s, 0.0) + length

    # Add border pressure to region metrics
    for rm in region_metrics:
        seed = rm["seed"]
        rm["neighbor_count"] = seed_neighbors.get(seed, 0)
        shared_len = seed_shared_length.get(seed, 0.0)
        perim = rm["perimeter"]
        rm["border_pressure"] = (
            round(shared_len / perim, 6) if perim > 0 else 0.0
        )
        rm["shared_border_length"] = round(shared_len, 4)

    # Count classifications
    dominant = sum(1 for rm in region_metrics if rm["classification"] == "dominant")
    marginal = sum(1 for rm in region_metrics if rm["classification"] == "marginal")
    average = n - dominant - marginal
    frontier_count = sum(1 for rm in region_metrics if rm["is_frontier"])

    # Dominance ratio: largest area / total area
    max_area = max(areas)
    dominance_ratio = max_area / total_area if total_area > 0 else 0.0

    # Balance score: 1 - gini (higher = more balanced)
    balance_score = 1.0 - gini

    summary = {
        "total_regions": n,
        "total_area": round(total_area, 4),
        "area_min": round(min(areas), 4),
        "area_max": round(max_area, 4),
        "area_mean": round(mean_area, 4),
        "area_median": round(median_area, 4),
        "area_std": round(std_area, 4),
        "area_cv": round(cv, 6),
        "gini_coefficient": round(gini, 6),
        "balance_score": round(balance_score, 6),
        "dominance_ratio": round(dominance_ratio, 6),
        "dominant_count": dominant,
        "average_count": average,
        "marginal_count": marginal,
        "frontier_count": frontier_count,
        "interior_count": n - frontier_count,
        "total_shared_borders": len(border_records),
        "avg_neighbor_count": (
            round(sum(seed_neighbors.values()) / n, 2) if n > 0 else 0.0
        ),
    }

    return {
        "regions": region_metrics,
        "summary": summary,
        "shared_borders": border_records,
    }


def _empty_summary() -> Dict[str, Any]:
    return {
        "total_regions": 0,
        "total_area": 0.0,
        "area_min": 0.0,
        "area_max": 0.0,
        "area_mean": 0.0,
        "area_median": 0.0,
        "area_std": 0.0,
        "area_cv": 0.0,
        "gini_coefficient": 0.0,
        "balance_score": 1.0,
        "dominance_ratio": 0.0,
        "dominant_count": 0,
        "average_count": 0,
        "marginal_count": 0,
        "frontier_count": 0,
        "interior_count": 0,
        "total_shared_borders": 0,
        "avg_neighbor_count": 0.0,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_territory_report(analysis: Dict[str, Any]) -> str:
    """Format a human-readable territorial analysis report.

    Parameters
    ----------
    analysis : dict
        Output from :func:`analyze_territories`.

    Returns
    -------
    str
        Multi-line text report.
    """
    s = analysis["summary"]
    lines = [
        "=" * 60,
        "TERRITORIAL ANALYSIS REPORT",
        "=" * 60,
        "",
        "Overview",
        "-" * 40,
        "  Total regions:       %d" % s["total_regions"],
        "  Total area:          %.2f" % s["total_area"],
        "  Shared borders:      %d" % s["total_shared_borders"],
        "  Avg neighbors:       %.1f" % s["avg_neighbor_count"],
        "",
        "Area Distribution",
        "-" * 40,
        "  Min:                 %.2f" % s["area_min"],
        "  Max:                 %.2f" % s["area_max"],
        "  Mean:                %.2f" % s["area_mean"],
        "  Median:              %.2f" % s["area_median"],
        "  Std deviation:       %.2f" % s["area_std"],
        "  Coeff of variation:  %.4f" % s["area_cv"],
        "",
        "Territorial Balance",
        "-" * 40,
        "  Gini coefficient:    %.4f  (0=equal, 1=monopoly)" % s["gini_coefficient"],
        "  Balance score:       %.4f  (1=equal, 0=monopoly)" % s["balance_score"],
        "  Dominance ratio:     %.4f  (largest / total)" % s["dominance_ratio"],
        "",
        "Classification",
        "-" * 40,
        "  Dominant:            %d" % s["dominant_count"],
        "  Average:             %d" % s["average_count"],
        "  Marginal:            %d" % s["marginal_count"],
        "",
        "Frontier Analysis",
        "-" * 40,
        "  Frontier (boundary): %d" % s["frontier_count"],
        "  Interior:            %d" % s["interior_count"],
        "",
    ]

    # Top regions by area
    sorted_regions = sorted(analysis["regions"], key=lambda r: r["area"], reverse=True)
    lines.append("Top Regions by Area")
    lines.append("-" * 40)
    for i, r in enumerate(sorted_regions[:5]):
        seed = r["seed"]
        lines.append(
            "  %d. (%.1f, %.1f)  area=%.2f  share=%.2f%%  "
            "class=%s  neighbors=%d  pressure=%.3f"
            % (
                i + 1,
                seed[0],
                seed[1],
                r["area"],
                r["area_share"] * 100,
                r["classification"],
                r["neighbor_count"],
                r["border_pressure"],
            )
        )
    lines.append("")

    # Most pressured regions (highest border pressure)
    sorted_pressure = sorted(
        analysis["regions"], key=lambda r: r["border_pressure"], reverse=True
    )
    lines.append("Most Pressured Regions (by border exposure)")
    lines.append("-" * 40)
    for i, r in enumerate(sorted_pressure[:5]):
        seed = r["seed"]
        lines.append(
            "  %d. (%.1f, %.1f)  pressure=%.3f  neighbors=%d  "
            "shared=%.2f / perim=%.2f"
            % (
                i + 1,
                seed[0],
                seed[1],
                r["border_pressure"],
                r["neighbor_count"],
                r["shared_border_length"],
                r["perimeter"],
            )
        )
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _serialize_seed(seed: Tuple[float, float]) -> List[float]:
    """Convert seed tuple to JSON-safe list."""
    return [float(seed[0]), float(seed[1])]


def export_territory_json(
    analysis: Dict[str, Any],
    output_path: str,
    *,
    indent: int = 2,
) -> None:
    """Export territorial analysis to JSON.

    Parameters
    ----------
    analysis : dict
        Output from :func:`analyze_territories`.
    output_path : str
        Destination file path.
    indent : int
        JSON indentation.
    """
    # Convert tuples to lists for JSON compatibility
    export = {
        "summary": analysis["summary"],
        "regions": [],
        "shared_borders": [],
    }
    for r in analysis["regions"]:
        entry = dict(r)
        entry["seed"] = _serialize_seed(entry["seed"])
        entry["centroid"] = list(entry["centroid"])
        export["regions"].append(entry)

    for b in analysis["shared_borders"]:
        entry = dict(b)
        entry["seed_a"] = _serialize_seed(entry["seed_a"])
        entry["seed_b"] = _serialize_seed(entry["seed_b"])
        export["shared_borders"].append(entry)

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=indent)


def export_territory_csv(
    analysis: Dict[str, Any],
    output_path: str,
) -> None:
    """Export per-region territorial metrics to CSV.

    Parameters
    ----------
    analysis : dict
        Output from :func:`analyze_territories`.
    output_path : str
        Destination file path.
    """
    header = (
        "seed_x,seed_y,area,perimeter,centroid_x,centroid_y,"
        "vertex_count,is_frontier,classification,area_share,"
        "neighbor_count,border_pressure,shared_border_length"
    )
    lines = [header]
    for r in analysis["regions"]:
        seed = r["seed"]
        centroid = r["centroid"]
        lines.append(
            "%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d,%s,%s,%.6f,%d,%.6f,%.4f"
            % (
                seed[0],
                seed[1],
                r["area"],
                r["perimeter"],
                centroid[0],
                centroid[1],
                r["vertex_count"],
                r["is_frontier"],
                r["classification"],
                r["area_share"],
                r["neighbor_count"],
                r["border_pressure"],
                r["shared_border_length"],
            )
        )

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
