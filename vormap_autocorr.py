"""Spatial autocorrelation analysis for Voronoi-partitioned point data.

Computes **Moran's I** (global and local) to measure spatial
autocorrelation — the degree to which nearby locations have similar
values.  Works with any numeric attribute assigned to Voronoi cells
(area, density, compactness, or user-supplied values).

- **Global Moran's I** answers: *"Is there an overall spatial pattern
  (clustering/dispersion) in this variable?"*
- **Local Moran's I (LISA)** answers: *"Which specific cells contribute
  to the pattern?"* — identifying hot-spots (High-High), cold-spots
  (Low-Low), and spatial outliers (High-Low, Low-High).

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_autocorr import (
        global_morans_i, local_morans_i,
        export_autocorr_json, export_lisa_svg,
    )

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)
    values = [s["area"] for s in stats]

    # Global test
    result = global_morans_i(values, regions, data)
    print(f"I = {result.I:.4f}  E[I] = {result.expected_I:.4f}")
    print(f"z = {result.z:.3f}  p = {result.p:.4f}")
    print(f"Pattern: {result.interpretation}")

    # Local (LISA)
    lisa = local_morans_i(values, regions, data)
    for cell in lisa.cells[:5]:
        print(f"Cell {cell.index}: Ii={cell.Ii:.4f}  type={cell.cluster_type}")

    export_lisa_svg(lisa, regions, data, "lisa.svg")
    export_autocorr_json(result, lisa, "autocorr.json")

CLI::

    voronoimap datauni5.txt 5 --autocorr
    voronoimap datauni5.txt 5 --autocorr --autocorr-metric density
    voronoimap datauni5.txt 5 --autocorr-json autocorr.json
    voronoimap datauni5.txt 5 --lisa-svg lisa.svg
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict

import vormap


# ── Result containers ───────────────────────────────────────────────

@dataclass
class GlobalMoranResult:
    """Result of a global Moran's I test."""
    I: float                    # Moran's I statistic
    expected_I: float           # Expected I under null hypothesis (-1/(n-1))
    variance: float             # Variance under randomization assumption
    z: float                    # Z-score
    p: float                    # Two-tailed p-value (normal approximation)
    n: int                      # Number of observations
    num_pairs: int              # Number of neighbor pairs (non-zero weights)
    interpretation: str         # "clustered", "dispersed", or "random"
    metric: str = "area"        # Metric used for values


@dataclass
class LISACell:
    """Local Moran's I result for a single cell."""
    index: int                  # Cell index
    x: float                    # Seed x-coordinate
    y: float                    # Seed y-coordinate
    value: float                # Raw attribute value
    z_value: float              # Standardized value (z-score)
    Ii: float                   # Local Moran's I statistic
    cluster_type: str           # "HH", "LL", "HL", "LH", or "NS" (not significant)
    p: float                    # Pseudo p-value from permutation test
    num_neighbors: int          # Number of spatial neighbors


@dataclass
class LISAResult:
    """Result of a Local Moran's I (LISA) analysis."""
    cells: List[LISACell] = field(default_factory=list)
    global_I: float = 0.0
    metric: str = "area"
    significance_level: float = 0.05
    num_permutations: int = 999
    counts: Dict[str, int] = field(default_factory=dict)


# ── Spatial weights ─────────────────────────────────────────────────

def _build_adjacency(regions: list, data: list) -> Dict[int, List[int]]:
    """Build a spatial adjacency dictionary from Voronoi regions.

    Two cells are neighbors if they share at least one edge vertex
    (within floating-point tolerance).
    """
    n = len(regions)
    adjacency: Dict[int, List[int]] = {i: [] for i in range(n)}

    # Collect edge segments per cell for fast intersection
    edge_sets: List[set] = []
    for region in regions:
        vertices = region.get("vertices", region) if isinstance(region, dict) else region
        # Normalize vertices to frozenset of rounded vertex tuples
        vset = set()
        if isinstance(vertices, list):
            for v in vertices:
                if isinstance(v, dict):
                    vset.add((round(v.get("x", 0), 4), round(v.get("y", 0), 4)))
                elif isinstance(v, (list, tuple)) and len(v) >= 2:
                    vset.add((round(v[0], 4), round(v[1], 4)))
        edge_sets.append(frozenset(vset))

    for i in range(n):
        for j in range(i + 1, n):
            # Two cells are adjacent if they share >= 2 vertices (an edge)
            shared = len(edge_sets[i] & edge_sets[j])
            if shared >= 2:
                adjacency[i].append(j)
                adjacency[j].append(i)

    return adjacency


def _row_standardize_weights(
    adjacency: Dict[int, List[int]], n: int
) -> Dict[int, Dict[int, float]]:
    """Row-standardize the binary adjacency matrix.

    Each cell's neighbor weights sum to 1.0.  Isolates (no neighbors)
    get an empty weight dict.
    """
    weights: Dict[int, Dict[int, float]] = {}
    for i in range(n):
        neighbors = adjacency.get(i, [])
        k = len(neighbors)
        if k == 0:
            weights[i] = {}
        else:
            w = 1.0 / k
            weights[i] = {j: w for j in neighbors}
    return weights


# ── Helpers ─────────────────────────────────────────────────────────

def _mean(values: list) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std_pop(values: list) -> float:
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def _z_to_p_two_tailed(z: float) -> float:
    """Approximate two-tailed p-value from z-score using error function."""
    return math.erfc(abs(z) / math.sqrt(2))


def _extract_metric_values(
    stats: list, metric: str = "area"
) -> List[float]:
    """Extract a numeric metric from region stats.

    Supported metrics: area, density, compactness, perimeter.
    """
    valid_metrics = {"area", "density", "compactness", "perimeter"}
    if metric not in valid_metrics:
        raise ValueError(
            f"Unknown metric '{metric}'. Choose from: {', '.join(sorted(valid_metrics))}"
        )
    values = []
    for s in stats:
        v = s.get(metric, 0)
        values.append(float(v) if v is not None else 0.0)
    return values


# ── Global Moran's I ───────────────────────────────────────────────

def global_morans_i(
    values: List[float],
    regions: list,
    data: list,
    metric: str = "area",
) -> GlobalMoranResult:
    """Compute Global Moran's I statistic.

    Measures overall spatial autocorrelation: positive I indicates
    clustering (similar values near each other), negative I indicates
    dispersion (dissimilar values near each other), and I near
    E[I] = -1/(n-1) indicates spatial randomness.

    Parameters
    ----------
    values : list of float
        Attribute values, one per cell (same order as regions).
    regions : list
        Voronoi regions (from ``vormap_viz.compute_regions``).
    data : list
        Seed points (from ``vormap.load_data``).
    metric : str
        Name of the metric (for labeling only).

    Returns
    -------
    GlobalMoranResult
        Test result with I, expected I, z-score, p-value, interpretation.
    """
    n = len(values)
    if n < 3:
        raise ValueError("At least 3 observations required for Moran's I.")
    if n != len(regions):
        raise ValueError(
            f"Length mismatch: {n} values vs {len(regions)} regions."
        )

    adjacency = _build_adjacency(regions, data)
    weights = _row_standardize_weights(adjacency, n)

    mean_val = _mean(values)
    deviations = [v - mean_val for v in values]
    m2 = sum(d * d for d in deviations)

    if m2 == 0:
        # All values identical — no variation, I is undefined
        return GlobalMoranResult(
            I=0.0, expected_I=-1.0 / (n - 1), variance=0.0,
            z=0.0, p=1.0, n=n, num_pairs=0,
            interpretation="random", metric=metric,
        )

    # Compute I = (n / S0) * (sum_i sum_j w_ij * z_i * z_j) / (sum z_i^2)
    # With row-standardized weights, S0 = n (each row sums to 1)
    S0 = 0.0
    numerator = 0.0
    num_pairs = 0

    for i in range(n):
        for j, w in weights[i].items():
            numerator += w * deviations[i] * deviations[j]
            S0 += w
            num_pairs += 1

    if S0 == 0:
        return GlobalMoranResult(
            I=0.0, expected_I=-1.0 / (n - 1), variance=0.0,
            z=0.0, p=1.0, n=n, num_pairs=0,
            interpretation="random", metric=metric,
        )

    I = (n / S0) * (numerator / m2)
    expected_I = -1.0 / (n - 1)

    # Variance under randomization assumption
    # Simplified formula for row-standardized weights
    S1 = 0.0
    for i in range(n):
        for j, w in weights[i].items():
            wji = weights[j].get(i, 0.0)
            S1 += (w + wji) ** 2
    S1 /= 2.0

    S2 = 0.0
    for i in range(n):
        row_sum = sum(weights[i].values())
        col_sum = sum(weights[j].get(i, 0.0) for j in range(n))
        S2 += (row_sum + col_sum) ** 2

    m4 = sum(d ** 4 for d in deviations)
    b2 = (n * m4) / (m2 ** 2)  # kurtosis

    A = n * ((n * n - 3 * n + 3) * S1 - n * S2 + 3 * S0 * S0)
    B = b2 * ((n * n - n) * S1 - 2 * n * S2 + 6 * S0 * S0)
    C = (n - 1) * (n - 2) * (n - 3) * S0 * S0

    if C == 0:
        variance = 0.0
    else:
        variance = (A - B) / C - expected_I ** 2

    if variance > 0:
        z = (I - expected_I) / math.sqrt(variance)
    else:
        z = 0.0

    p = _z_to_p_two_tailed(z)

    # Interpretation
    if p < 0.05:
        interpretation = "clustered" if I > expected_I else "dispersed"
    else:
        interpretation = "random"

    return GlobalMoranResult(
        I=I, expected_I=expected_I, variance=variance,
        z=z, p=p, n=n, num_pairs=num_pairs // 2,
        interpretation=interpretation, metric=metric,
    )


# ── Local Moran's I (LISA) ─────────────────────────────────────────

def local_morans_i(
    values: List[float],
    regions: list,
    data: list,
    metric: str = "area",
    permutations: int = 999,
    significance: float = 0.05,
) -> LISAResult:
    """Compute Local Indicators of Spatial Association (LISA).

    For each cell, computes a local Moran's I statistic that decomposes
    the global statistic into per-cell contributions.  Cells are
    classified as:

    - **HH** (High-High): high value surrounded by high values (hot-spot)
    - **LL** (Low-Low): low value surrounded by low values (cold-spot)
    - **HL** (High-Low): high value surrounded by low values (spatial outlier)
    - **LH** (Low-High): low value surrounded by high values (spatial outlier)
    - **NS** (Not Significant): p-value above significance threshold

    Parameters
    ----------
    values : list of float
        Attribute values, one per cell.
    regions : list
        Voronoi regions.
    data : list
        Seed points.
    metric : str
        Name of the metric.
    permutations : int
        Number of random permutations for pseudo p-value (default 999).
    significance : float
        Significance threshold for cluster classification (default 0.05).

    Returns
    -------
    LISAResult
        Per-cell LISA results with cluster types and pseudo p-values.
    """
    n = len(values)
    if n < 3:
        raise ValueError("At least 3 observations required for LISA.")
    if n != len(regions):
        raise ValueError(
            f"Length mismatch: {n} values vs {len(regions)} regions."
        )

    adjacency = _build_adjacency(regions, data)
    weights = _row_standardize_weights(adjacency, n)

    mean_val = _mean(values)
    std_val = _std_pop(values)

    if std_val == 0:
        # No variation — everything is NS
        cells = []
        for i in range(n):
            pt = data[i]
            x = pt[0] if isinstance(pt, (list, tuple)) else pt.get("x", 0)
            y = pt[1] if isinstance(pt, (list, tuple)) else pt.get("y", 0)
            cells.append(LISACell(
                index=i, x=float(x), y=float(y),
                value=values[i], z_value=0.0, Ii=0.0,
                cluster_type="NS", p=1.0,
                num_neighbors=len(adjacency.get(i, [])),
            ))
        return LISAResult(
            cells=cells, global_I=0.0, metric=metric,
            significance_level=significance,
            num_permutations=permutations,
            counts={"HH": 0, "LL": 0, "HL": 0, "LH": 0, "NS": n},
        )

    # Standardize values
    z_vals = [(v - mean_val) / std_val for v in values]

    # Compute m2 for normalization
    m2 = sum(z * z for z in z_vals) / n

    # Compute local I for each cell
    local_Is = []
    for i in range(n):
        lag = sum(w * z_vals[j] for j, w in weights[i].items())
        Ii = (z_vals[i] * lag) / m2 if m2 != 0 else 0.0
        local_Is.append(Ii)

    # Permutation test for pseudo p-values
    import random
    rng = random.Random(42)  # Deterministic for reproducibility

    p_values = []
    for i in range(n):
        if not weights[i]:
            p_values.append(1.0)
            continue

        neighbor_indices = list(weights[i].keys())
        observed_Ii = local_Is[i]
        count_extreme = 0

        for _ in range(permutations):
            # Randomly reassign neighbor values (conditional permutation)
            perm_indices = rng.sample(
                [j for j in range(n) if j != i],
                len(neighbor_indices),
            )
            lag_perm = sum(
                weights[i][neighbor_indices[k]] * z_vals[perm_indices[k]]
                for k in range(len(neighbor_indices))
            )
            Ii_perm = (z_vals[i] * lag_perm) / m2 if m2 != 0 else 0.0
            if abs(Ii_perm) >= abs(observed_Ii):
                count_extreme += 1

        p_val = (count_extreme + 1) / (permutations + 1)
        p_values.append(p_val)

    # Classify cells
    cells = []
    counts = {"HH": 0, "LL": 0, "HL": 0, "LH": 0, "NS": 0}

    for i in range(n):
        pt = data[i]
        if isinstance(pt, (list, tuple)):
            x, y = float(pt[0]), float(pt[1])
        elif isinstance(pt, dict):
            x, y = float(pt.get("x", 0)), float(pt.get("y", 0))
        else:
            x, y = 0.0, 0.0

        lag = sum(w * z_vals[j] for j, w in weights[i].items())

        if p_values[i] <= significance:
            if z_vals[i] > 0 and lag > 0:
                ctype = "HH"
            elif z_vals[i] < 0 and lag < 0:
                ctype = "LL"
            elif z_vals[i] > 0 and lag < 0:
                ctype = "HL"
            else:
                ctype = "LH"
        else:
            ctype = "NS"

        counts[ctype] += 1
        cells.append(LISACell(
            index=i, x=x, y=y,
            value=values[i], z_value=z_vals[i],
            Ii=local_Is[i], cluster_type=ctype,
            p=p_values[i],
            num_neighbors=len(adjacency.get(i, [])),
        ))

    global_I = sum(local_Is) / n if n > 0 else 0.0

    return LISAResult(
        cells=cells, global_I=global_I, metric=metric,
        significance_level=significance,
        num_permutations=permutations,
        counts=counts,
    )


# ── Formatting ──────────────────────────────────────────────────────

def format_global_report(result: GlobalMoranResult) -> str:
    """Format a human-readable report for global Moran's I."""
    lines = [
        "Global Moran's I — Spatial Autocorrelation Test",
        "=" * 48,
        f"  Metric:          {result.metric}",
        f"  Observations:    {result.n}",
        f"  Neighbor pairs:  {result.num_pairs}",
        "",
        f"  Moran's I:       {result.I:>10.6f}",
        f"  Expected I:      {result.expected_I:>10.6f}",
        f"  Variance:        {result.variance:>10.6f}",
        f"  Z-score:         {result.z:>10.4f}",
        f"  p-value:         {result.p:>10.6f}",
        "",
        f"  Interpretation:  {result.interpretation.upper()}",
    ]
    if result.interpretation == "clustered":
        lines.append("  → Similar values tend to be near each other.")
    elif result.interpretation == "dispersed":
        lines.append("  → Dissimilar values tend to be near each other.")
    else:
        lines.append("  → No significant spatial pattern detected.")
    return "\n".join(lines)


def format_lisa_summary(result: LISAResult) -> str:
    """Format a summary table for LISA results."""
    lines = [
        "Local Moran's I (LISA) — Cluster Summary",
        "=" * 42,
        f"  Metric:            {result.metric}",
        f"  Cells:             {len(result.cells)}",
        f"  Significance:      {result.significance_level}",
        f"  Permutations:      {result.num_permutations}",
        f"  Global I (from L): {result.global_I:.6f}",
        "",
        "  Cluster Type  Count  Description",
        "  ──────────── ─────  ───────────────────────",
        f"  HH (Hot-Spot)  {result.counts.get('HH', 0):>4}  High value, high neighbors",
        f"  LL (Cold-Spot) {result.counts.get('LL', 0):>4}  Low value, low neighbors",
        f"  HL (Outlier)   {result.counts.get('HL', 0):>4}  High value, low neighbors",
        f"  LH (Outlier)   {result.counts.get('LH', 0):>4}  Low value, high neighbors",
        f"  NS (Random)    {result.counts.get('NS', 0):>4}  Not significant",
    ]
    return "\n".join(lines)


# ── Export ──────────────────────────────────────────────────────────

def export_autocorr_json(
    global_result: GlobalMoranResult,
    lisa_result: Optional[LISAResult],
    path: str,
) -> None:
    """Export autocorrelation results to JSON."""
    output = {
        "global_morans_i": {
            "I": round(global_result.I, 6),
            "expected_I": round(global_result.expected_I, 6),
            "variance": round(global_result.variance, 6),
            "z_score": round(global_result.z, 4),
            "p_value": round(global_result.p, 6),
            "n": global_result.n,
            "num_pairs": global_result.num_pairs,
            "interpretation": global_result.interpretation,
            "metric": global_result.metric,
        },
    }

    if lisa_result is not None:
        output["lisa"] = {
            "global_I_from_local": round(lisa_result.global_I, 6),
            "significance_level": lisa_result.significance_level,
            "num_permutations": lisa_result.num_permutations,
            "counts": lisa_result.counts,
            "cells": [
                {
                    "index": c.index,
                    "x": round(c.x, 4),
                    "y": round(c.y, 4),
                    "value": round(c.value, 4),
                    "z_value": round(c.z_value, 4),
                    "Ii": round(c.Ii, 6),
                    "cluster_type": c.cluster_type,
                    "p": round(c.p, 6),
                    "num_neighbors": c.num_neighbors,
                }
                for c in lisa_result.cells
            ],
        }

    validated = vormap.validate_output_path(path, allow_absolute=True)
    with open(validated, "w") as f:
        json.dump(output, f, indent=2)


def export_lisa_svg(
    result: LISAResult,
    regions: list,
    data: list,
    path: str,
    width: int = 800,
    height: int = 600,
    title: str = "LISA Cluster Map",
) -> None:
    """Export LISA cluster map as SVG.

    Colors:
    - Red (#e31a1c): HH (hot-spot)
    - Blue (#2c7bb6): LL (cold-spot)
    - Orange (#fd8d3c): HL (spatial outlier)
    - Light blue (#a6bddb): LH (spatial outlier)
    - Light gray (#f0f0f0): NS (not significant)
    """
    COLORS = {
        "HH": "#e31a1c",
        "LL": "#2c7bb6",
        "HL": "#fd8d3c",
        "LH": "#a6bddb",
        "NS": "#f0f0f0",
    }

    # Compute bounds from data
    xs = []
    ys = []
    for pt in data:
        if isinstance(pt, (list, tuple)):
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
        elif isinstance(pt, dict):
            xs.append(float(pt.get("x", 0)))
            ys.append(float(pt.get("y", 0)))

    if not xs or not ys:
        return

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    data_w = x_max - x_min or 1
    data_h = y_max - y_min or 1

    margin = 50
    plot_w = width - 2 * margin
    plot_h = height - 2 * margin - 30  # leave room for title

    def tx(x):
        return margin + (x - x_min) / data_w * plot_w

    def ty(y):
        return margin + 30 + (1 - (y - y_min) / data_h) * plot_h

    # Build SVG
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                      width=str(width), height=str(height))

    # Background
    ET.SubElement(svg, "rect", width=str(width), height=str(height),
                  fill="white")

    # Title
    title_el = ET.SubElement(svg, "text", x=str(width // 2),
                              y=str(margin // 2 + 10),
                              fill="#333")
    title_el.set("text-anchor", "middle")
    title_el.set("font-family", "sans-serif")
    title_el.set("font-size", "16")
    title_el.set("font-weight", "bold")
    title_el.text = title

    # Build cell type lookup from LISA results
    cell_types = {}
    for cell in result.cells:
        cell_types[cell.index] = cell.cluster_type

    # Draw Voronoi cells
    for i, region in enumerate(regions):
        vertices = region.get("vertices", region) if isinstance(region, dict) else region
        if not isinstance(vertices, list) or len(vertices) < 3:
            continue

        points_str = " ".join(
            f"{tx(v[0] if isinstance(v, (list, tuple)) else v.get('x', 0)):.1f},"
            f"{ty(v[1] if isinstance(v, (list, tuple)) else v.get('y', 0)):.1f}"
            for v in vertices
        )

        ctype = cell_types.get(i, "NS")
        color = COLORS.get(ctype, "#f0f0f0")

        ET.SubElement(svg, "polygon",
                      points=points_str,
                      fill=color,
                      stroke="#666",
                      **{"stroke-width": "0.5", "opacity": "0.85"})

    # Draw seed points
    for i, pt in enumerate(data):
        if isinstance(pt, (list, tuple)):
            px, py = float(pt[0]), float(pt[1])
        elif isinstance(pt, dict):
            px, py = float(pt.get("x", 0)), float(pt.get("y", 0))
        else:
            continue
        ET.SubElement(svg, "circle",
                      cx=f"{tx(px):.1f}", cy=f"{ty(py):.1f}",
                      r="2", fill="#333", opacity="0.6")

    # Legend
    legend_x = width - margin - 130
    legend_y = margin + 40
    legend_items = [
        ("HH (Hot-Spot)", COLORS["HH"]),
        ("LL (Cold-Spot)", COLORS["LL"]),
        ("HL (Outlier)", COLORS["HL"]),
        ("LH (Outlier)", COLORS["LH"]),
        ("NS (Random)", COLORS["NS"]),
    ]
    # Legend background
    ET.SubElement(svg, "rect",
                  x=str(legend_x - 5), y=str(legend_y - 15),
                  width="140", height=str(len(legend_items) * 20 + 10),
                  fill="white", stroke="#ccc",
                  **{"stroke-width": "0.5", "rx": "4", "opacity": "0.9"})

    for idx, (label, color) in enumerate(legend_items):
        ly = legend_y + idx * 20
        ET.SubElement(svg, "rect",
                      x=str(legend_x), y=str(ly),
                      width="12", height="12",
                      fill=color, stroke="#666",
                      **{"stroke-width": "0.5"})
        text_el = ET.SubElement(svg, "text",
                                x=str(legend_x + 18), y=str(ly + 10),
                                fill="#333")
        text_el.set("font-family", "sans-serif")
        text_el.set("font-size", "11")
        text_el.text = label

    validated = vormap.validate_output_path(path, allow_absolute=True)
    tree = ET.ElementTree(svg)
    tree.write(validated, encoding="unicode", xml_declaration=True)
