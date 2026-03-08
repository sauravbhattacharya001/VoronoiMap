"""Landscape Ecology Metrics for Voronoi Diagrams.

Computes landscape-level metrics used in ecology, conservation, and
urban planning to quantify how space is structured. Treats Voronoi
regions as habitat patches grouped by a categorical attribute (class).

Implements a subset of FRAGSTATS-style metrics at three levels:

- **Patch-level**: area, perimeter, shape index, core area, fractal dim
- **Class-level**: percent landscape, patch density, mean patch size,
  edge density, cohesion, aggregation, largest-patch index
- **Landscape-level**: Shannon/Simpson diversity, dominance, evenness,
  contagion, total edge density, patch richness, mean shape complexity

Applications include habitat fragmentation analysis, land-use change
monitoring, urban sprawl measurement, and conservation prioritisation.

Example::

    import vormap, vormap_viz
    from vormap_landscape import (
        analyze_landscape, format_landscape_report,
        export_landscape_json, export_landscape_csv,
    )

    data = vormap.load_data("sites.txt")
    regions = vormap_viz.compute_regions(data)

    # Assign each point a class (e.g. land-use type)
    classes = {pt: "forest" if i % 3 == 0 else "urban" if i % 3 == 1
               else "water" for i, pt in enumerate(data)}

    analysis = analyze_landscape(regions, data, classes)
    print(format_landscape_report(analysis))
    export_landscape_json(analysis, "landscape.json")
"""

from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import vormap
import vormap_viz

from vormap_geometry import (
    polygon_area as _polygon_area,
    polygon_perimeter as _polygon_perimeter,
    polygon_centroid as _polygon_centroid,
    edge_length as _edge_length,
    isoperimetric_quotient as _isoperimetric_quotient,
)


# ---------------------------------------------------------------------------
# Shared-border detection
# ---------------------------------------------------------------------------

def _find_shared_borders(
    regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
    tolerance: float = 1.0,
) -> Dict[
    Tuple[Tuple[float, float], Tuple[float, float]],
    float,
]:
    """Find shared border lengths between adjacent regions."""
    edge_map: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
    for seed, verts in regions.items():
        n = len(verts)
        for i in range(n):
            v1 = verts[i]
            v2 = verts[(i + 1) % n]
            mid = (
                round((v1[0] + v2[0]) / 2, 1),
                round((v1[1] + v2[1]) / 2, 1),
            )
            edge_map.setdefault(mid, []).append(seed)

    borders: Dict[
        Tuple[Tuple[float, float], Tuple[float, float]], float
    ] = {}
    for seed, verts in regions.items():
        n = len(verts)
        for i in range(n):
            v1 = verts[i]
            v2 = verts[(i + 1) % n]
            mid = (
                round((v1[0] + v2[0]) / 2, 1),
                round((v1[1] + v2[1]) / 2, 1),
            )
            for other in edge_map.get(mid, []):
                if other != seed:
                    pair = tuple(sorted([seed, other]))
                    length = _edge_length(v1, v2)
                    borders[pair] = borders.get(pair, 0) + length
    return borders


# ---------------------------------------------------------------------------
# Patch-level metrics
# ---------------------------------------------------------------------------

class PatchMetrics:
    """Metrics for a single patch (Voronoi region)."""

    __slots__ = (
        "seed", "patch_class", "area", "perimeter", "shape_index",
        "fractal_dimension", "core_area", "core_area_index",
        "compactness", "neighbors", "class_neighbors",
        "edge_contrast",
    )

    def __init__(self) -> None:
        self.seed: Tuple[float, float] = (0, 0)
        self.patch_class: str = ""
        self.area: float = 0.0
        self.perimeter: float = 0.0
        self.shape_index: float = 0.0
        self.fractal_dimension: float = 0.0
        self.core_area: float = 0.0
        self.core_area_index: float = 0.0
        self.compactness: float = 0.0
        self.neighbors: int = 0
        self.class_neighbors: int = 0
        self.edge_contrast: float = 0.0


def _shape_index(area: float, perimeter: float) -> float:
    """Shape index: 1 for circle, >1 for more complex shapes."""
    if area <= 0:
        return 0.0
    return perimeter / (2 * math.sqrt(math.pi * area))


def _fractal_dimension(area: float, perimeter: float) -> float:
    """Patch fractal dimension (perimeter-area relationship)."""
    if area <= 0 or perimeter <= 0:
        return 0.0
    ln_p = math.log(perimeter)
    ln_a = math.log(area)
    if ln_a == 0:
        return 0.0
    return 2 * ln_p / ln_a


def _core_area(area: float, perimeter: float, edge_depth: float) -> float:
    """Estimate core area by buffering inward from edges.

    Uses simplified approximation: core = area - perimeter * depth + pi*depth^2.
    Clamped to [0, area].
    """
    if area <= 0 or edge_depth <= 0:
        return area
    core = area - perimeter * edge_depth + math.pi * edge_depth * edge_depth
    return max(0.0, min(core, area))


def compute_patch_metrics(
    regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
    classes: Dict[Tuple[float, float], str],
    borders: Dict[Tuple[Tuple[float, float], Tuple[float, float]], float],
    edge_depth: float = 20.0,
) -> List[PatchMetrics]:
    """Compute metrics for each patch."""
    adj: Dict[Tuple[float, float], List[Tuple[float, float]]] = {}
    for (s1, s2) in borders:
        adj.setdefault(s1, []).append(s2)
        adj.setdefault(s2, []).append(s1)

    patches = []
    for seed, verts in regions.items():
        pm = PatchMetrics()
        pm.seed = seed
        pm.patch_class = classes.get(seed, "unclassified")
        pm.area = _polygon_area(verts)
        pm.perimeter = _polygon_perimeter(verts)
        pm.shape_index = _shape_index(pm.area, pm.perimeter)
        pm.fractal_dimension = _fractal_dimension(pm.area, pm.perimeter)
        pm.core_area = _core_area(pm.area, pm.perimeter, edge_depth)
        pm.core_area_index = (
            pm.core_area / pm.area if pm.area > 0 else 0.0
        )
        pm.compactness = _isoperimetric_quotient(pm.area, pm.perimeter)

        nbrs = adj.get(seed, [])
        pm.neighbors = len(nbrs)
        pm.class_neighbors = sum(
            1 for n in nbrs if classes.get(n, "") == pm.patch_class
        )
        pm.edge_contrast = (
            (pm.neighbors - pm.class_neighbors) / pm.neighbors
            if pm.neighbors > 0 else 0.0
        )
        patches.append(pm)
    return patches


# ---------------------------------------------------------------------------
# Class-level metrics
# ---------------------------------------------------------------------------

class ClassMetrics:
    """Aggregated metrics for one landscape class."""

    __slots__ = (
        "class_name", "patch_count", "total_area", "percent_landscape",
        "mean_patch_area", "std_patch_area", "largest_patch_area",
        "largest_patch_index", "patch_density", "total_edge",
        "edge_density", "mean_shape_index", "mean_fractal_dim",
        "total_core_area", "mean_core_area_index",
        "cohesion", "aggregation_index",
    )

    def __init__(self) -> None:
        self.class_name: str = ""
        self.patch_count: int = 0
        self.total_area: float = 0.0
        self.percent_landscape: float = 0.0
        self.mean_patch_area: float = 0.0
        self.std_patch_area: float = 0.0
        self.largest_patch_area: float = 0.0
        self.largest_patch_index: float = 0.0
        self.patch_density: float = 0.0
        self.total_edge: float = 0.0
        self.edge_density: float = 0.0
        self.mean_shape_index: float = 0.0
        self.mean_fractal_dim: float = 0.0
        self.total_core_area: float = 0.0
        self.mean_core_area_index: float = 0.0
        self.cohesion: float = 0.0
        self.aggregation_index: float = 0.0


def compute_class_metrics(
    patches: List[PatchMetrics],
    borders: Dict[Tuple[Tuple[float, float], Tuple[float, float]], float],
    classes: Dict[Tuple[float, float], str],
    total_landscape_area: float,
) -> Dict[str, ClassMetrics]:
    """Compute class-level metrics."""
    by_class: Dict[str, List[PatchMetrics]] = {}
    for pm in patches:
        by_class.setdefault(pm.patch_class, []).append(pm)

    results = {}
    for cls_name, cls_patches in by_class.items():
        cm = ClassMetrics()
        cm.class_name = cls_name
        cm.patch_count = len(cls_patches)

        areas = [p.area for p in cls_patches]
        cm.total_area = sum(areas)
        cm.percent_landscape = (
            cm.total_area / total_landscape_area * 100
            if total_landscape_area > 0 else 0.0
        )
        cm.mean_patch_area = cm.total_area / cm.patch_count
        mean_a = cm.mean_patch_area
        cm.std_patch_area = math.sqrt(
            sum((a - mean_a) ** 2 for a in areas) / cm.patch_count
        ) if cm.patch_count > 0 else 0.0
        cm.largest_patch_area = max(areas)
        cm.largest_patch_index = (
            cm.largest_patch_area / total_landscape_area * 100
            if total_landscape_area > 0 else 0.0
        )
        cm.patch_density = (
            cm.patch_count / (total_landscape_area / 1e6)
            if total_landscape_area > 0 else 0.0
        )

        class_seeds = {p.seed for p in cls_patches}
        class_edge = 0.0
        for (s1, s2), length in borders.items():
            if s1 in class_seeds or s2 in class_seeds:
                class_edge += length
        cm.total_edge = class_edge
        cm.edge_density = (
            class_edge / (total_landscape_area / 1e3)
            if total_landscape_area > 0 else 0.0
        )

        cm.mean_shape_index = (
            sum(p.shape_index for p in cls_patches) / cm.patch_count
        )
        cm.mean_fractal_dim = (
            sum(p.fractal_dimension for p in cls_patches) / cm.patch_count
        )
        cm.total_core_area = sum(p.core_area for p in cls_patches)
        cm.mean_core_area_index = (
            sum(p.core_area_index for p in cls_patches) / cm.patch_count
        )

        perim_sum = sum(p.perimeter for p in cls_patches)
        area_sqrt_sum = sum(math.sqrt(p.area) for p in cls_patches)
        if perim_sum > 0 and total_landscape_area > 0:
            numerator = 1 - (perim_sum / (perim_sum + area_sqrt_sum))
            denominator = 1 - (1 / math.sqrt(total_landscape_area))
            cm.cohesion = (
                numerator / denominator * 100
                if denominator > 0 else 100.0
            )
        else:
            cm.cohesion = 100.0

        same_adj = sum(p.class_neighbors for p in cls_patches)
        total_adj = sum(p.neighbors for p in cls_patches)
        cm.aggregation_index = (
            same_adj / total_adj * 100
            if total_adj > 0 else 0.0
        )

        results[cls_name] = cm
    return results


# ---------------------------------------------------------------------------
# Landscape-level metrics
# ---------------------------------------------------------------------------

class LandscapeMetrics:
    """Metrics for the entire landscape."""

    __slots__ = (
        "total_area", "num_patches", "num_classes", "patch_richness",
        "patch_density", "mean_patch_area", "total_edge",
        "edge_density", "mean_shape_index", "mean_fractal_dim",
        "shannon_diversity", "simpson_diversity", "dominance",
        "evenness", "contagion", "total_core_area",
        "mean_core_area_index",
    )

    def __init__(self) -> None:
        self.total_area: float = 0.0
        self.num_patches: int = 0
        self.num_classes: int = 0
        self.patch_richness: int = 0
        self.patch_density: float = 0.0
        self.mean_patch_area: float = 0.0
        self.total_edge: float = 0.0
        self.edge_density: float = 0.0
        self.mean_shape_index: float = 0.0
        self.mean_fractal_dim: float = 0.0
        self.shannon_diversity: float = 0.0
        self.simpson_diversity: float = 0.0
        self.dominance: float = 0.0
        self.evenness: float = 0.0
        self.contagion: float = 0.0
        self.total_core_area: float = 0.0
        self.mean_core_area_index: float = 0.0


def compute_landscape_metrics(
    patches: List[PatchMetrics],
    class_metrics: Dict[str, ClassMetrics],
    borders: Dict[Tuple[Tuple[float, float], Tuple[float, float]], float],
    classes: Dict[Tuple[float, float], str],
) -> LandscapeMetrics:
    """Compute landscape-level aggregate metrics."""
    lm = LandscapeMetrics()
    lm.num_patches = len(patches)
    lm.num_classes = len(class_metrics)
    lm.patch_richness = lm.num_classes
    lm.total_area = sum(p.area for p in patches)
    lm.mean_patch_area = (
        lm.total_area / lm.num_patches if lm.num_patches > 0 else 0.0
    )
    lm.patch_density = (
        lm.num_patches / (lm.total_area / 1e6)
        if lm.total_area > 0 else 0.0
    )

    lm.total_edge = sum(borders.values())
    lm.edge_density = (
        lm.total_edge / (lm.total_area / 1e3)
        if lm.total_area > 0 else 0.0
    )

    lm.mean_shape_index = (
        sum(p.shape_index for p in patches) / lm.num_patches
        if lm.num_patches > 0 else 0.0
    )
    lm.mean_fractal_dim = (
        sum(p.fractal_dimension for p in patches) / lm.num_patches
        if lm.num_patches > 0 else 0.0
    )
    lm.total_core_area = sum(p.core_area for p in patches)
    lm.mean_core_area_index = (
        sum(p.core_area_index for p in patches) / lm.num_patches
        if lm.num_patches > 0 else 0.0
    )

    if lm.total_area > 0 and lm.num_classes > 0:
        proportions = [
            cm.total_area / lm.total_area
            for cm in class_metrics.values()
        ]
        lm.shannon_diversity = -sum(
            p * math.log(p) for p in proportions if p > 0
        )
        lm.simpson_diversity = 1 - sum(p * p for p in proportions)
        max_diversity = math.log(lm.num_classes) if lm.num_classes > 1 else 0
        lm.dominance = max(0.0, max_diversity - lm.shannon_diversity)
        lm.evenness = (
            lm.shannon_diversity / max_diversity
            if max_diversity > 0 else 1.0
        )

    if lm.num_classes > 1:
        adj_matrix: Dict[str, Dict[str, int]] = {}
        for pm in patches:
            cls_a = pm.patch_class
            for nbr_seed in _get_neighbors(pm.seed, borders):
                cls_b = classes.get(nbr_seed, "unclassified")
                adj_matrix.setdefault(cls_a, {})
                adj_matrix[cls_a][cls_b] = (
                    adj_matrix[cls_a].get(cls_b, 0) + 1
                )
        total_adj = sum(
            v for row in adj_matrix.values() for v in row.values()
        )
        if total_adj > 0:
            m = lm.num_classes
            entropy = 0.0
            for cls_a in adj_matrix:
                pi = (class_metrics[cls_a].total_area / lm.total_area
                      if cls_a in class_metrics and lm.total_area > 0
                      else 0)
                row_sum = sum(adj_matrix[cls_a].values())
                for cls_b, count in adj_matrix[cls_a].items():
                    gij = count / total_adj
                    if gij > 0 and pi > 0:
                        pij = pi * (count / row_sum) if row_sum > 0 else 0
                        if pij > 0:
                            entropy += pij * math.log(pij)
            max_entropy = 2 * math.log(m) if m > 1 else 1
            lm.contagion = (1 + entropy / max_entropy) * 100 if max_entropy > 0 else 0
    else:
        lm.contagion = 100.0

    return lm


def _get_neighbors(
    seed: Tuple[float, float],
    borders: Dict[Tuple[Tuple[float, float], Tuple[float, float]], float],
) -> List[Tuple[float, float]]:
    """Get all neighboring seeds."""
    nbrs = []
    for (s1, s2) in borders:
        if s1 == seed:
            nbrs.append(s2)
        elif s2 == seed:
            nbrs.append(s1)
    return nbrs


# ---------------------------------------------------------------------------
# Main analysis function
# ---------------------------------------------------------------------------

class LandscapeAnalysis:
    """Container for all landscape analysis results."""

    __slots__ = (
        "patch_metrics", "class_metrics", "landscape_metrics",
        "fragmentation_summary",
    )

    def __init__(self) -> None:
        self.patch_metrics: List[PatchMetrics] = []
        self.class_metrics: Dict[str, ClassMetrics] = {}
        self.landscape_metrics: LandscapeMetrics = LandscapeMetrics()
        self.fragmentation_summary: Dict[str, str] = {}


def _fragmentation_level(
    evenness: float, mean_shape: float, core_index: float,
) -> str:
    """Classify overall fragmentation level."""
    score = 0
    if evenness > 0.8:
        score += 1
    if mean_shape > 1.5:
        score += 1
    if core_index < 0.5:
        score += 1

    if score >= 3:
        return "High"
    elif score >= 2:
        return "Moderate-High"
    elif score >= 1:
        return "Moderate"
    else:
        return "Low"


def analyze_landscape(
    regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
    data: List[Tuple[float, float]],
    classes: Dict[Tuple[float, float], str],
    edge_depth: float = 20.0,
) -> LandscapeAnalysis:
    """Run full landscape ecology analysis.

    Parameters
    ----------
    regions : dict
        Voronoi regions from ``vormap_viz.compute_regions()``.
    data : list of (float, float)
        Seed points.
    classes : dict
        Maps each seed point to a class label (e.g. "forest", "urban").
    edge_depth : float
        Buffer depth for core area calculation (pixels).

    Returns
    -------
    LandscapeAnalysis
        Complete analysis results at patch, class, and landscape levels.
    """
    result = LandscapeAnalysis()

    borders = _find_shared_borders(regions)
    total_area = sum(_polygon_area(v) for v in regions.values())

    result.patch_metrics = compute_patch_metrics(
        regions, classes, borders, edge_depth
    )
    result.class_metrics = compute_class_metrics(
        result.patch_metrics, borders, classes, total_area
    )
    result.landscape_metrics = compute_landscape_metrics(
        result.patch_metrics, result.class_metrics, borders, classes
    )

    lm = result.landscape_metrics
    frag_level = _fragmentation_level(
        lm.evenness, lm.mean_shape_index, lm.mean_core_area_index
    )
    result.fragmentation_summary = {
        "level": frag_level,
        "diversity": (
            "High" if lm.shannon_diversity > 1.5 else
            "Moderate" if lm.shannon_diversity > 0.8 else "Low"
        ),
        "shape_complexity": (
            "Complex" if lm.mean_shape_index > 1.5 else
            "Moderate" if lm.mean_shape_index > 1.2 else "Simple"
        ),
        "core_preservation": (
            "Good" if lm.mean_core_area_index > 0.6 else
            "Fair" if lm.mean_core_area_index > 0.3 else "Poor"
        ),
        "spatial_pattern": (
            "Clumped" if lm.contagion > 60 else
            "Random" if lm.contagion > 40 else "Dispersed"
        ),
    }

    return result


# ---------------------------------------------------------------------------
# Formatting / reporting
# ---------------------------------------------------------------------------

def format_landscape_report(analysis: LandscapeAnalysis) -> str:
    """Format analysis as human-readable text report."""
    lines = []
    lm = analysis.landscape_metrics
    fs = analysis.fragmentation_summary

    lines.append("=" * 60)
    lines.append("LANDSCAPE ECOLOGY METRICS REPORT")
    lines.append("=" * 60)
    lines.append("")

    lines.append("--- Overview ---")
    lines.append(f"  Total area:        {lm.total_area:,.0f} sq units")
    lines.append(f"  Patches:           {lm.num_patches}")
    lines.append(f"  Classes:           {lm.num_classes}")
    lines.append(f"  Patch density:     {lm.patch_density:,.1f} per M sq units")
    lines.append(f"  Mean patch area:   {lm.mean_patch_area:,.0f} sq units")
    lines.append("")

    lines.append("--- Diversity ---")
    lines.append(f"  Shannon (H'):      {lm.shannon_diversity:.4f}")
    lines.append(f"  Simpson (1-D):     {lm.simpson_diversity:.4f}")
    lines.append(f"  Dominance:         {lm.dominance:.4f}")
    lines.append(f"  Evenness:          {lm.evenness:.4f}")
    lines.append(f"  Contagion:         {lm.contagion:.1f}%")
    lines.append("")

    lines.append("--- Shape & Edges ---")
    lines.append(f"  Mean shape index:  {lm.mean_shape_index:.3f}")
    lines.append(f"  Mean fractal dim:  {lm.mean_fractal_dim:.3f}")
    lines.append(f"  Total edge:        {lm.total_edge:,.0f} units")
    lines.append(f"  Edge density:      {lm.edge_density:,.1f} per K sq units")
    lines.append("")

    lines.append("--- Core Area ---")
    lines.append(f"  Total core area:   {lm.total_core_area:,.0f} sq units")
    lines.append(f"  Mean core index:   {lm.mean_core_area_index:.3f}")
    lines.append("")

    lines.append("--- Fragmentation Assessment ---")
    for key, value in fs.items():
        lines.append(f"  {key:20s} {value}")
    lines.append("")

    lines.append("--- Class Breakdown ---")
    for cls_name in sorted(analysis.class_metrics.keys()):
        cm = analysis.class_metrics[cls_name]
        lines.append(f"  [{cls_name}]")
        lines.append(f"    Patches:         {cm.patch_count}")
        lines.append(f"    Area:            {cm.total_area:,.0f} ({cm.percent_landscape:.1f}%)")
        lines.append(f"    Mean patch:      {cm.mean_patch_area:,.0f} sq units")
        lines.append(f"    Largest patch:   {cm.largest_patch_area:,.0f} ({cm.largest_patch_index:.1f}%)")
        lines.append(f"    Edge density:    {cm.edge_density:,.1f}")
        lines.append(f"    Shape index:     {cm.mean_shape_index:.3f}")
        lines.append(f"    Core area:       {cm.total_core_area:,.0f}")
        lines.append(f"    Cohesion:        {cm.cohesion:.1f}%")
        lines.append(f"    Aggregation:     {cm.aggregation_index:.1f}%")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _metrics_to_dict(obj: Any) -> Dict[str, Any]:
    """Convert a metrics object to a JSON-serialisable dict."""
    d = {}
    for attr in obj.__slots__:
        val = getattr(obj, attr)
        if isinstance(val, tuple):
            val = list(val)
        d[attr] = val
    return d


def export_landscape_json(
    analysis: LandscapeAnalysis,
    path: str,
) -> str:
    """Export analysis to JSON file."""
    data = {
        "landscape": _metrics_to_dict(analysis.landscape_metrics),
        "fragmentation": analysis.fragmentation_summary,
        "classes": {
            name: _metrics_to_dict(cm)
            for name, cm in analysis.class_metrics.items()
        },
        "patches": [_metrics_to_dict(pm) for pm in analysis.patch_metrics],
    }
    abs_path = os.path.abspath(path)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return abs_path


def export_landscape_csv(
    analysis: LandscapeAnalysis,
    path: str,
) -> str:
    """Export patch-level metrics to CSV file."""
    abs_path = os.path.abspath(path)
    headers = [
        "seed_x", "seed_y", "class", "area", "perimeter",
        "shape_index", "fractal_dim", "core_area", "core_area_index",
        "compactness", "neighbors", "class_neighbors", "edge_contrast",
    ]
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(",".join(headers) + "\n")
        for pm in analysis.patch_metrics:
            row = [
                f"{pm.seed[0]:.2f}",
                f"{pm.seed[1]:.2f}",
                pm.patch_class,
                f"{pm.area:.2f}",
                f"{pm.perimeter:.2f}",
                f"{pm.shape_index:.4f}",
                f"{pm.fractal_dimension:.4f}",
                f"{pm.core_area:.2f}",
                f"{pm.core_area_index:.4f}",
                f"{pm.compactness:.4f}",
                str(pm.neighbors),
                str(pm.class_neighbors),
                f"{pm.edge_contrast:.4f}",
            ]
            f.write(",".join(row) + "\n")
    return abs_path
