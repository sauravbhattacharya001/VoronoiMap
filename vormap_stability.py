"""Voronoi Stability Analysis - measure diagram sensitivity to point perturbation.

Monte Carlo perturbation analysis that quantifies how robust each Voronoi
region is to small changes in seed point positions.  Useful for understanding
measurement uncertainty, identifying fragile boundaries, and assessing
overall tessellation robustness.

Usage::

    from vormap_stability import (
        stability_analysis, StabilityResult, CellStability,
    )

    # Analyze how stable the diagram is under noise
    result = stability_analysis(data, noise_radius=5.0, iterations=100)
    print(result.summary())

    for cell in result.cells:
        print(f"Seed {cell.seed}: stability={cell.stability_score:.3f}")

    # CLI
    python vormap_stability.py data/points.txt --noise 5.0 --iterations 100
    python vormap_stability.py data/points.txt --noise 5.0 --json stability.json
    python vormap_stability.py data/points.txt --noise 5.0 --csv stability.csv
    python vormap_stability.py data/points.txt --noise 5.0 --svg stability.svg
"""


import json
import math
import random
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import vormap
from vormap_geometry import polygon_area
from vormap_viz import compute_regions, compute_region_stats


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class CellStability:
    """Stability metrics for a single Voronoi cell.

    Attributes
    ----------
    seed : tuple of (float, float)
        Original seed point coordinates.
    region_index : int
        1-based index in the original data ordering.
    original_area : float
        Area of the cell in the unperturbed diagram.
    mean_area : float
        Mean area across all perturbation trials.
    area_std : float
        Standard deviation of area across trials.
    area_cv : float
        Coefficient of variation (std / mean) of area.  Lower means more
        stable.
    min_area : float
        Minimum area observed across trials.
    max_area : float
        Maximum area observed across trials.
    stability_score : float
        Composite stability score in [0, 1].  1.0 = perfectly stable,
        0.0 = extremely fragile.  Derived from area CV and topology change
        frequency.
    topology_change_rate : float
        Fraction of trials where the cell's vertex count changed from the
        original.  High rate indicates the cell's shape is fragile.
    original_vertex_count : int
        Vertex count in the unperturbed diagram.
    mean_vertex_count : float
        Mean vertex count across trials.
    survival_rate : float
        Fraction of trials where the cell was successfully computed
        (non-degenerate).  1.0 means the cell always appeared.
    """

    seed: Tuple[float, float]
    region_index: int = 0
    original_area: float = 0.0
    mean_area: float = 0.0
    area_std: float = 0.0
    area_cv: float = 0.0
    min_area: float = 0.0
    max_area: float = 0.0
    stability_score: float = 1.0
    topology_change_rate: float = 0.0
    original_vertex_count: int = 0
    mean_vertex_count: float = 0.0
    survival_rate: float = 1.0


@dataclass
class StabilityResult:
    """Aggregate stability analysis results.

    Attributes
    ----------
    cells : list of CellStability
        Per-cell stability metrics, sorted by region index.
    global_stability : float
        Mean stability score across all cells, in [0, 1].
    noise_radius : float
        Perturbation radius used for the analysis.
    iterations : int
        Number of Monte Carlo trials performed.
    most_stable_cell : int
        Region index of the most stable cell.
    least_stable_cell : int
        Region index of the least stable cell.
    mean_area_cv : float
        Mean coefficient of variation of area across all cells.
    mean_topology_change_rate : float
        Mean topology change rate across all cells.
    """

    cells: List[CellStability] = field(default_factory=list)
    global_stability: float = 0.0
    noise_radius: float = 0.0
    iterations: int = 0
    most_stable_cell: int = 0
    least_stable_cell: int = 0
    mean_area_cv: float = 0.0
    mean_topology_change_rate: float = 0.0

    def summary(self) -> str:
        """Human-readable summary of stability analysis."""
        lines = [
            "=== Voronoi Stability Analysis ===",
            f"  Noise radius:        {self.noise_radius}",
            f"  Iterations:          {self.iterations}",
            f"  Cells analyzed:      {len(self.cells)}",
            f"  Global stability:    {self.global_stability:.4f}",
            f"  Mean area CV:        {self.mean_area_cv:.4f}",
            f"  Mean topology churn: {self.mean_topology_change_rate:.4f}",
            f"  Most stable cell:    #{self.most_stable_cell}",
            f"  Least stable cell:   #{self.least_stable_cell}",
            "",
            "  Rating: ",
        ]
        if self.global_stability >= 0.9:
            lines[-1] += "EXCELLENT - diagram is highly robust"
        elif self.global_stability >= 0.7:
            lines[-1] += "GOOD - diagram is reasonably stable"
        elif self.global_stability >= 0.5:
            lines[-1] += "MODERATE - some regions are sensitive"
        elif self.global_stability >= 0.3:
            lines[-1] += "POOR - many regions are fragile"
        else:
            lines[-1] += "CRITICAL - diagram is highly sensitive to noise"
        return "\n".join(lines)


# ── Core analysis ────────────────────────────────────────────────────

def _perturb_points(
    data: List[Tuple[float, float]],
    noise_radius: float,
    rng: random.Random,
) -> List[Tuple[float, float]]:
    """Perturb each point by a random displacement within *noise_radius*.

    Uses uniform sampling within a disk (rejection-free polar method).
    """
    perturbed = []
    for x, y in data:
        angle = rng.uniform(0, 2 * math.pi)
        # sqrt gives uniform area distribution within disk
        r = noise_radius * math.sqrt(rng.random())
        perturbed.append((x + r * math.cos(angle), y + r * math.sin(angle)))
    return perturbed


def _match_seeds(
    original_seeds: List[Tuple[float, float]],
    perturbed_regions: dict,
    perturbed_data: List[Tuple[float, float]],
) -> Dict[Tuple[float, float], dict]:
    """Match perturbed region seeds back to their original seed.

    Uses the correspondence between original data indices and perturbed
    data indices (same ordering).
    """
    # Build lookup: perturbed point -> region
    perturbed_lookup = {}
    for seed in perturbed_regions:
        perturbed_lookup[seed] = perturbed_regions[seed]

    # Map original seed index -> perturbed seed -> region vertices
    matched = {}
    for i, orig_seed in enumerate(original_seeds):
        if i < len(perturbed_data):
            p_seed = perturbed_data[i]
            if p_seed in perturbed_lookup:
                verts = perturbed_lookup[p_seed]
                area = polygon_area(verts)
                matched[orig_seed] = {
                    "area": area,
                    "vertex_count": len(verts),
                }
    return matched


def stability_analysis(
    data: List[Tuple[float, float]],
    noise_radius: float = 1.0,
    iterations: int = 50,
    seed: Optional[int] = None,
) -> StabilityResult:
    """Run Monte Carlo stability analysis on a Voronoi diagram.

    Perturbs each seed point by up to *noise_radius* in a random
    direction for *iterations* trials, re-computes the Voronoi diagram
    each time, and measures how much each cell's area and topology
    change.

    Parameters
    ----------
    data : list of (float, float)
        Seed points (output of ``vormap.load_data()``).
    noise_radius : float
        Maximum perturbation distance for each point per trial.
    iterations : int
        Number of Monte Carlo trials.  More iterations give more
        reliable stability estimates but take longer.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    StabilityResult
        Aggregate and per-cell stability metrics.

    Raises
    ------
    ValueError
        If data has fewer than 3 points, noise_radius is non-positive,
        or iterations is less than 1.
    """
    if len(data) < 3:
        raise ValueError("Need at least 3 seed points for stability analysis")
    if noise_radius <= 0:
        raise ValueError("noise_radius must be positive")
    if iterations < 1:
        raise ValueError("iterations must be at least 1")

    rng = random.Random(seed)

    # Compute baseline diagram
    base_regions = compute_regions(data)
    base_stats = compute_region_stats(base_regions, data)
    base_lookup = {}
    for stat in base_stats:
        key = (stat["seed_x"], stat["seed_y"])
        base_lookup[key] = stat

    # Per-seed accumulators
    area_samples = {tuple(pt): [] for pt in data}
    vertex_samples = {tuple(pt): [] for pt in data}
    survival_counts = {tuple(pt): 0 for pt in data}

    # Run Monte Carlo trials
    for _ in range(iterations):
        perturbed = _perturb_points(data, noise_radius, rng)
        try:
            p_regions = compute_regions(perturbed)
        except Exception:
            # If the entire diagram fails, skip this trial
            continue

        matched = _match_seeds(
            [tuple(pt) for pt in data], p_regions, perturbed
        )

        for pt_key in area_samples:
            if pt_key in matched:
                area_samples[pt_key].append(matched[pt_key]["area"])
                vertex_samples[pt_key].append(matched[pt_key]["vertex_count"])
                survival_counts[pt_key] += 1

    # Compute per-cell stability
    cells = []
    for pt in data:
        pt_key = tuple(pt)
        base = base_lookup.get(pt_key)
        if base is None:
            # Point wasn't in the base diagram (degenerate)
            continue

        areas = area_samples[pt_key]
        verts = vertex_samples[pt_key]
        n_survived = survival_counts[pt_key]

        orig_area = base["area"]
        orig_vcount = base["vertex_count"]
        survival_rate = n_survived / iterations if iterations > 0 else 0.0

        if len(areas) >= 2:
            mean_a = sum(areas) / len(areas)
            std_a = math.sqrt(
                sum((a - mean_a) ** 2 for a in areas) / len(areas)
            )
            cv_a = std_a / mean_a if mean_a > 0 else 0.0
            min_a = min(areas)
            max_a = max(areas)
        elif len(areas) == 1:
            mean_a = areas[0]
            std_a = 0.0
            cv_a = 0.0
            min_a = max_a = areas[0]
        else:
            mean_a = orig_area
            std_a = 0.0
            cv_a = 0.0
            min_a = max_a = orig_area

        if verts:
            mean_v = sum(verts) / len(verts)
            topo_changes = sum(1 for v in verts if v != orig_vcount)
            topo_rate = topo_changes / len(verts)
        else:
            mean_v = float(orig_vcount)
            topo_rate = 0.0

        # Stability score: combine area CV and topology change rate
        # Both are "badness" metrics — higher = less stable
        # Transform to [0, 1] stability using exponential decay
        area_stability = math.exp(-3.0 * cv_a)  # CV of 0.23 -> 0.5
        topo_stability = 1.0 - topo_rate
        survival_factor = survival_rate

        score = (
            0.5 * area_stability
            + 0.3 * topo_stability
            + 0.2 * survival_factor
        )
        score = max(0.0, min(1.0, score))

        cells.append(CellStability(
            seed=pt_key,
            region_index=base["region_index"],
            original_area=orig_area,
            mean_area=round(mean_a, 4),
            area_std=round(std_a, 4),
            area_cv=round(cv_a, 4),
            min_area=round(min_a, 4),
            max_area=round(max_a, 4),
            stability_score=round(score, 4),
            topology_change_rate=round(topo_rate, 4),
            original_vertex_count=orig_vcount,
            mean_vertex_count=round(mean_v, 2),
            survival_rate=round(survival_rate, 4),
        ))

    cells.sort(key=lambda c: c.region_index)

    # Global metrics
    if cells:
        global_stab = sum(c.stability_score for c in cells) / len(cells)
        mean_cv = sum(c.area_cv for c in cells) / len(cells)
        mean_topo = sum(c.topology_change_rate for c in cells) / len(cells)
        most_stable = max(cells, key=lambda c: c.stability_score)
        least_stable = min(cells, key=lambda c: c.stability_score)
    else:
        global_stab = 0.0
        mean_cv = 0.0
        mean_topo = 0.0
        most_stable = least_stable = None

    return StabilityResult(
        cells=cells,
        global_stability=round(global_stab, 4),
        noise_radius=noise_radius,
        iterations=iterations,
        most_stable_cell=most_stable.region_index if most_stable else 0,
        least_stable_cell=least_stable.region_index if least_stable else 0,
        mean_area_cv=round(mean_cv, 4),
        mean_topology_change_rate=round(mean_topo, 4),
    )


# ── Export: JSON ─────────────────────────────────────────────────────

def export_json(result: StabilityResult, output_path: str) -> str:
    """Export stability results to a JSON file.

    Parameters
    ----------
    result : StabilityResult
        Output of ``stability_analysis()``.
    output_path : str
        Destination file path.

    Returns
    -------
    str
        The validated output path.
    """
    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    obj = {
        "noise_radius": result.noise_radius,
        "iterations": result.iterations,
        "global_stability": result.global_stability,
        "mean_area_cv": result.mean_area_cv,
        "mean_topology_change_rate": result.mean_topology_change_rate,
        "most_stable_cell": result.most_stable_cell,
        "least_stable_cell": result.least_stable_cell,
        "cells": [
            {
                "region_index": c.region_index,
                "seed_x": c.seed[0],
                "seed_y": c.seed[1],
                "original_area": c.original_area,
                "mean_area": c.mean_area,
                "area_std": c.area_std,
                "area_cv": c.area_cv,
                "min_area": c.min_area,
                "max_area": c.max_area,
                "stability_score": c.stability_score,
                "topology_change_rate": c.topology_change_rate,
                "original_vertex_count": c.original_vertex_count,
                "mean_vertex_count": c.mean_vertex_count,
                "survival_rate": c.survival_rate,
            }
            for c in result.cells
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    return output_path


# ── Export: CSV ──────────────────────────────────────────────────────

def export_csv(result: StabilityResult, output_path: str) -> str:
    """Export per-cell stability metrics to a CSV file.

    Parameters
    ----------
    result : StabilityResult
        Output of ``stability_analysis()``.
    output_path : str
        Destination file path.

    Returns
    -------
    str
        The validated output path.
    """
    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    header = (
        "region_index,seed_x,seed_y,original_area,mean_area,area_std,"
        "area_cv,min_area,max_area,stability_score,topology_change_rate,"
        "original_vertex_count,mean_vertex_count,survival_rate"
    )
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        f.write(header + "\n")
        for c in result.cells:
            row = (
                f"{c.region_index},{c.seed[0]},{c.seed[1]},"
                f"{c.original_area},{c.mean_area},{c.area_std},"
                f"{c.area_cv},{c.min_area},{c.max_area},"
                f"{c.stability_score},{c.topology_change_rate},"
                f"{c.original_vertex_count},{c.mean_vertex_count},"
                f"{c.survival_rate}"
            )
            f.write(row + "\n")
    return output_path


# ── Export: SVG ──────────────────────────────────────────────────────

def _stability_color(score: float) -> str:
    """Map stability score [0, 1] to a red-yellow-green color.

    0.0 = red (fragile), 0.5 = yellow, 1.0 = green (stable).
    """
    if score <= 0.5:
        # Red to yellow
        t = score / 0.5
        r = 220
        g = int(60 + 160 * t)
        b = 50
    else:
        # Yellow to green
        t = (score - 0.5) / 0.5
        r = int(220 - 180 * t)
        g = int(220 - 30 * t)
        b = int(50 + 70 * t)
    return f"rgb({r},{g},{b})"


def export_svg(
    result: StabilityResult,
    regions: dict,
    data: List[Tuple[float, float]],
    output_path: str,
    *,
    width: int = 800,
    height: int = 600,
    margin: int = 40,
    show_labels: bool = True,
    show_scores: bool = True,
    stroke_color: str = "#333333",
    stroke_width: float = 1.5,
    point_radius: float = 4.0,
    background: str = "#ffffff",
    title: Optional[str] = None,
) -> str:
    """Export stability heatmap as an SVG file.

    Colors each cell by its stability score: green = stable, red = fragile.

    Parameters
    ----------
    result : StabilityResult
        Output of ``stability_analysis()``.
    regions : dict
        Output of ``compute_regions()``.
    data : list of (float, float)
        Original seed points.
    output_path : str
        Destination file path.
    width, height : int
        SVG canvas dimensions.
    margin : int
        Margin around the diagram.
    show_labels : bool
        Show region index labels.
    show_scores : bool
        Show stability scores on each cell.
    stroke_color : str
        Color of cell boundaries.
    stroke_width : float
        Width of cell boundaries.
    point_radius : float
        Radius of seed point markers.
    background : str
        Background color.
    title : str or None
        Optional title displayed above the diagram.

    Returns
    -------
    str
        The validated output path.
    """
    output_path = vormap.validate_output_path(output_path, allow_absolute=True)

    if not regions:
        raise ValueError("No regions to export")

    # Build score lookup
    score_lookup = {}
    for c in result.cells:
        score_lookup[c.seed] = c.stability_score

    # Compute bounding box
    all_x = []
    all_y = []
    for seed, verts in regions.items():
        for vx, vy in verts:
            all_x.append(vx)
            all_y.append(vy)
    if not all_x:
        raise ValueError("No vertex data to export")

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    data_w = max_x - min_x or 1.0
    data_h = max_y - min_y or 1.0

    draw_w = width - 2 * margin
    draw_h = height - 2 * margin
    scale = min(draw_w / data_w, draw_h / data_h)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        return margin + (max_y - y) * scale  # flip Y

    # SVG header
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
    ]

    if title:
        safe_title = title.replace("&", "&amp;").replace("<", "&lt;")
        lines.append(
            f'<text x="{width / 2}" y="20" text-anchor="middle" '
            f'font-size="14" font-weight="bold">{safe_title}</text>'
        )

    # Draw regions colored by stability
    for seed, verts in sorted(regions.items()):
        pts = " ".join(f"{tx(vx):.1f},{ty(vy):.1f}" for vx, vy in verts)
        score = score_lookup.get(tuple(seed), 0.5)
        fill = _stability_color(score)
        lines.append(
            f'<polygon points="{pts}" fill="{fill}" fill-opacity="0.7" '
            f'stroke="{stroke_color}" stroke-width="{stroke_width}"/>'
        )

    # Draw seed points
    for px, py in data:
        sx, sy = tx(px), ty(py)
        lines.append(
            f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="{point_radius}" '
            f'fill="#111111" stroke="white" stroke-width="0.8"/>'
        )

    # Labels and scores
    if show_labels or show_scores:
        for c in result.cells:
            sx, sy = tx(c.seed[0]), ty(c.seed[1])
            label_parts = []
            if show_labels:
                label_parts.append(f"#{c.region_index}")
            if show_scores:
                label_parts.append(f"{c.stability_score:.2f}")
            label = " ".join(label_parts)
            lines.append(
                f'<text x="{sx:.1f}" y="{sy + point_radius + 12:.1f}" '
                f'text-anchor="middle" font-size="10" fill="#222">'
                f'{label}</text>'
            )

    # Legend
    legend_y = height - 25
    legend_x = margin
    legend_w = 200
    lines.append(
        f'<text x="{legend_x}" y="{legend_y - 5}" font-size="10" '
        f'fill="#333">Stability: </text>'
    )
    for i in range(20):
        t = i / 19.0
        fill = _stability_color(t)
        bx = legend_x + 60 + i * (legend_w / 20)
        lines.append(
            f'<rect x="{bx:.1f}" y="{legend_y - 12}" '
            f'width="{legend_w / 20 + 0.5:.1f}" height="12" fill="{fill}"/>'
        )
    lines.append(
        f'<text x="{legend_x + 60}" y="{legend_y + 5}" font-size="8" '
        f'fill="#666">0 (fragile)</text>'
    )
    lines.append(
        f'<text x="{legend_x + 60 + legend_w}" y="{legend_y + 5}" '
        f'font-size="8" fill="#666" text-anchor="end">1 (stable)</text>'
    )

    lines.append("</svg>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return output_path


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv=None):
    """Command-line interface for Voronoi stability analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Voronoi Stability Analysis - measure diagram sensitivity "
                    "to seed point perturbation via Monte Carlo simulation.",
    )
    parser.add_argument(
        "datafile",
        help="Data file with seed points (one per line: x y).",
    )
    parser.add_argument(
        "--noise",
        type=float,
        default=5.0,
        help="Maximum perturbation radius per point (default: 5.0).",
    )
    parser.add_argument(
        "--iterations",
        "-n",
        type=int,
        default=50,
        help="Number of Monte Carlo trials (default: 50).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility.",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        default=None,
        help="Export results as JSON.",
    )
    parser.add_argument(
        "--csv",
        metavar="FILE",
        default=None,
        help="Export per-cell results as CSV.",
    )
    parser.add_argument(
        "--svg",
        metavar="FILE",
        default=None,
        help="Export stability heatmap as SVG.",
    )
    parser.add_argument(
        "--svg-width",
        type=int,
        default=800,
        help="SVG width in pixels (default: 800).",
    )
    parser.add_argument(
        "--svg-height",
        type=int,
        default=600,
        help="SVG height in pixels (default: 600).",
    )
    parser.add_argument(
        "--no-labels",
        action="store_true",
        help="Suppress region index labels on SVG.",
    )
    parser.add_argument(
        "--no-scores",
        action="store_true",
        help="Suppress stability scores on SVG.",
    )

    args = parser.parse_args(argv)

    # Load data
    data = vormap.load_data(args.datafile)
    print(f"Loaded {len(data)} seed points from {args.datafile}")

    if len(data) < 3:
        print("Error: need at least 3 points for stability analysis.",
              file=sys.stderr)
        sys.exit(1)

    print(f"Running {args.iterations} perturbation trials "
          f"(noise radius = {args.noise}) ...")

    result = stability_analysis(
        data,
        noise_radius=args.noise,
        iterations=args.iterations,
        seed=args.seed,
    )

    print(result.summary())
    print()

    # Per-cell details (top 5 most fragile)
    fragile = sorted(result.cells, key=lambda c: c.stability_score)[:5]
    if fragile:
        print("Top 5 most fragile cells:")
        for c in fragile:
            print(f"  #{c.region_index}: score={c.stability_score:.3f}  "
                  f"area_cv={c.area_cv:.3f}  topo_churn={c.topology_change_rate:.3f}")

    # Exports
    if args.json:
        path = export_json(result, args.json)
        print(f"\nJSON exported to {path}")

    if args.csv:
        path = export_csv(result, args.csv)
        print(f"CSV exported to {path}")

    if args.svg:
        regions = compute_regions(data)
        path = export_svg(
            result, regions, data, args.svg,
            width=args.svg_width,
            height=args.svg_height,
            show_labels=not args.no_labels,
            show_scores=not args.no_scores,
            title=f"Voronoi Stability (noise={args.noise}, n={args.iterations})",
        )
        print(f"SVG heatmap exported to {path}")


if __name__ == "__main__":
    main()
