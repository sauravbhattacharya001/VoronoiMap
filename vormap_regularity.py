"""Voronoi entropy and regularity analysis.

Quantifies tessellation order using topological and geometric
measures drawn from materials science and spatial statistics:

- **Voronoi entropy** — Shannon entropy of the polygon side-count
  distribution.  Zero for a perfect lattice, ~1.71 bits for
  Poisson-Voronoi.
- **Lewis's law** — linear relationship between cell area and
  number of sides: A(n) ∝ (n − n₀).
- **Aboav-Weaire law** — topological neighbour correlation:
  n · m(n) ≈ (6 − a) · n + 6a + μ₂, where m(n) is the mean
  neighbour side-count for n-sided cells.
- **Polygon type distribution** — histogram of n-gons with
  percentages.
- **Regularity index** — composite 0–100 score combining entropy,
  Lewis's law fit, hexagonal fraction, and area CV.

Typical usage::

    import vormap, vormap_viz
    from vormap_regularity import regularity_analysis, format_report

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)
    graph = vormap_viz.extract_neighborhood_graph(regions, data)

    result = regularity_analysis(stats, regions, graph)
    print(format_report(result))

CLI::

    voronoimap datauni5.txt 5 --regularity
    voronoimap datauni5.txt 5 --regularity-json regularity.json
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Result containers ───────────────────────────────────────────────

@dataclass
class PolygonDistribution:
    """Distribution of polygon types (n-gons).

    Attributes
    ----------
    counts : dict[int, int]
        Maps side count n → number of cells with n sides.
    fractions : dict[int, float]
        Maps side count n → fraction of cells.
    total_cells : int
        Total number of cells analysed.
    mean_sides : float
        Mean number of sides per cell.
    variance : float
        Variance of the side-count distribution (μ₂).
    min_sides : int
        Minimum side count.
    max_sides : int
        Maximum side count.
    """
    counts: Dict[int, int] = field(default_factory=dict)
    fractions: Dict[int, float] = field(default_factory=dict)
    total_cells: int = 0
    mean_sides: float = 0.0
    variance: float = 0.0
    min_sides: int = 0
    max_sides: int = 0


@dataclass
class LewisLaw:
    """Lewis's law fit: A(n) = slope · (n − intercept_n).

    Attributes
    ----------
    slope : float
        Linear coefficient.
    intercept_n : float
        x-intercept in side-count space (n₀ where area → 0).
    r_squared : float
        Coefficient of determination for the linear fit.
    data_points : list of (int, float)
        (side_count, mean_area) pairs used for fitting.
    holds : bool
        True if R² ≥ 0.7 (reasonable linear fit).
    """
    slope: float = 0.0
    intercept_n: float = 0.0
    r_squared: float = 0.0
    data_points: List[Tuple[int, float]] = field(default_factory=list)
    holds: bool = False


@dataclass
class AboavWeaire:
    """Aboav-Weaire law: n · m(n) ≈ (6 − a) · n + 6a + μ₂.

    Attributes
    ----------
    a : float
        Aboav-Weaire parameter (typically 1.0–1.4 for random 2D).
    r_squared : float
        Fit quality for the linear regression.
    data_points : list of (int, float)
        (n, n·m(n)) pairs used for fitting.
    holds : bool
        True if R² ≥ 0.6.
    """
    a: float = 0.0
    r_squared: float = 0.0
    data_points: List[Tuple[int, float]] = field(default_factory=list)
    holds: bool = False


@dataclass
class RegularityResult:
    """Full regularity analysis result.

    Attributes
    ----------
    entropy : float
        Shannon entropy of side-count distribution (bits).
    max_entropy : float
        Maximum possible entropy (log₂ of distinct side counts).
    normalised_entropy : float
        entropy / max_entropy  (0 = perfectly regular, 1 = max disorder).
    hex_fraction : float
        Fraction of cells that are hexagons (6-sided).
    area_cv : float
        Coefficient of variation of cell areas (σ/μ).
    regularity_score : float
        Composite score 0–100 (100 = perfectly regular lattice).
    interpretation : str
        Human-readable classification.
    distribution : PolygonDistribution
        Polygon type distribution.
    lewis : LewisLaw
        Lewis's law analysis.
    aboav : AboavWeaire
        Aboav-Weaire law analysis.
    """
    entropy: float = 0.0
    max_entropy: float = 0.0
    normalised_entropy: float = 0.0
    hex_fraction: float = 0.0
    area_cv: float = 0.0
    regularity_score: float = 0.0
    interpretation: str = ""
    distribution: PolygonDistribution = field(default_factory=PolygonDistribution)
    lewis: LewisLaw = field(default_factory=LewisLaw)
    aboav: AboavWeaire = field(default_factory=AboavWeaire)

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON export."""
        return {
            "entropy": round(self.entropy, 6),
            "max_entropy": round(self.max_entropy, 6),
            "normalised_entropy": round(self.normalised_entropy, 6),
            "hex_fraction": round(self.hex_fraction, 6),
            "area_cv": round(self.area_cv, 6),
            "regularity_score": round(self.regularity_score, 2),
            "interpretation": self.interpretation,
            "distribution": {
                "counts": self.distribution.counts,
                "fractions": {k: round(v, 6) for k, v in self.distribution.fractions.items()},
                "total_cells": self.distribution.total_cells,
                "mean_sides": round(self.distribution.mean_sides, 4),
                "variance": round(self.distribution.variance, 4),
                "min_sides": self.distribution.min_sides,
                "max_sides": self.distribution.max_sides,
            },
            "lewis_law": {
                "slope": round(self.lewis.slope, 6),
                "intercept_n": round(self.lewis.intercept_n, 4),
                "r_squared": round(self.lewis.r_squared, 6),
                "holds": self.lewis.holds,
                "data_points": [
                    {"sides": n, "mean_area": round(a, 6)}
                    for n, a in self.lewis.data_points
                ],
            },
            "aboav_weaire": {
                "a": round(self.aboav.a, 6),
                "r_squared": round(self.aboav.r_squared, 6),
                "holds": self.aboav.holds,
                "data_points": [
                    {"n": n, "n_times_mn": round(v, 6)}
                    for n, v in self.aboav.data_points
                ],
            },
        }


# ── Core analysis functions ─────────────────────────────────────────

def polygon_distribution(side_counts: List[int]) -> PolygonDistribution:
    """Compute polygon type distribution from side counts."""
    if not side_counts:
        return PolygonDistribution()

    counts: Dict[int, int] = {}
    for n in side_counts:
        counts[n] = counts.get(n, 0) + 1

    total = len(side_counts)
    fractions = {n: c / total for n, c in sorted(counts.items())}
    mean_n = sum(side_counts) / total
    variance = sum((n - mean_n) ** 2 for n in side_counts) / total

    return PolygonDistribution(
        counts=dict(sorted(counts.items())),
        fractions=fractions,
        total_cells=total,
        mean_sides=mean_n,
        variance=variance,
        min_sides=min(side_counts),
        max_sides=max(side_counts),
    )


def voronoi_entropy(side_counts: List[int]) -> float:
    """Shannon entropy of side-count distribution (bits).

    H = -Σ p(n) · log₂(p(n))

    Returns 0.0 for empty or uniform distributions.
    """
    if not side_counts:
        return 0.0

    total = len(side_counts)
    counts: Dict[int, int] = {}
    for n in side_counts:
        counts[n] = counts.get(n, 0) + 1

    entropy = 0.0
    for c in counts.values():
        p = c / total
        if p > 0:
            entropy -= p * math.log2(p)

    return entropy


def lewis_law_fit(
    side_counts: List[int], areas: List[float]
) -> LewisLaw:
    """Fit Lewis's law: mean_area(n) = slope · n + offset.

    Groups cells by side count, computes mean area per group,
    and performs linear regression.
    """
    if len(side_counts) != len(areas) or len(side_counts) < 2:
        return LewisLaw()

    # Group areas by side count
    groups: Dict[int, List[float]] = {}
    for n, a in zip(side_counts, areas):
        groups.setdefault(n, []).append(a)

    # Need at least 2 groups for a line fit
    if len(groups) < 2:
        return LewisLaw()

    points = []
    for n in sorted(groups):
        mean_a = sum(groups[n]) / len(groups[n])
        points.append((n, mean_a))

    # Linear regression: A = slope * n + offset
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n_pts = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))

    denom = n_pts * sxx - sx * sx
    if abs(denom) < 1e-15:
        return LewisLaw(data_points=points)

    slope = (n_pts * sxy - sx * sy) / denom
    offset = (sy - slope * sx) / n_pts

    # R² calculation
    y_mean = sy / n_pts
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + offset)) ** 2 for x, y in zip(xs, ys))
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    # x-intercept: slope * n₀ + offset = 0 → n₀ = -offset/slope
    intercept_n = -offset / slope if abs(slope) > 1e-15 else 0.0

    return LewisLaw(
        slope=slope,
        intercept_n=intercept_n,
        r_squared=r_sq,
        data_points=points,
        holds=r_sq >= 0.7,
    )


def aboav_weaire_fit(
    side_counts: List[int],
    adjacency: Dict,
    seed_to_sides: Dict,
) -> AboavWeaire:
    """Fit the Aboav-Weaire law from adjacency data.

    For each n-sided cell, computes the mean side count m(n) of its
    neighbours.  Then fits: n · m(n) = (6 − a) · n + (6a + μ₂).

    Parameters
    ----------
    side_counts : list of int
        Side counts for all cells.
    adjacency : dict
        Maps seed (x,y) → list of neighbour seeds.
    seed_to_sides : dict
        Maps seed (x,y) → side count.
    """
    if len(side_counts) < 3:
        return AboavWeaire()

    mean_n = sum(side_counts) / len(side_counts)
    mu2 = sum((n - mean_n) ** 2 for n in side_counts) / len(side_counts)

    # Compute n · m(n) for each side count
    # m(n) = mean side count of neighbours of n-sided cells
    group_nm: Dict[int, List[float]] = {}

    for seed, neighbors in adjacency.items():
        seed_key = tuple(seed) if not isinstance(seed, tuple) else seed
        n = seed_to_sides.get(seed_key)
        if n is None:
            continue

        neighbor_sides = []
        for nb in neighbors:
            nb_key = tuple(nb) if not isinstance(nb, tuple) else nb
            ns = seed_to_sides.get(nb_key)
            if ns is not None:
                neighbor_sides.append(ns)

        if neighbor_sides:
            m_n = sum(neighbor_sides) / len(neighbor_sides)
            group_nm.setdefault(n, []).append(n * m_n)

    if len(group_nm) < 2:
        return AboavWeaire()

    # Average n·m(n) per side count
    points = []
    for n in sorted(group_nm):
        mean_nm = sum(group_nm[n]) / len(group_nm[n])
        points.append((n, mean_nm))

    # Fit: n·m(n) = (6 - a)·n + (6a + μ₂)
    # This is y = slope·x + intercept where slope = 6-a, intercept = 6a + μ₂
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    n_pts = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))

    denom = n_pts * sxx - sx * sx
    if abs(denom) < 1e-15:
        return AboavWeaire(data_points=points)

    slope = (n_pts * sxy - sx * sy) / denom

    # a = 6 - slope
    a = 6.0 - slope

    # R²
    y_mean = sy / n_pts
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    intercept = (sy - slope * sx) / n_pts
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return AboavWeaire(
        a=a,
        r_squared=r_sq,
        data_points=points,
        holds=r_sq >= 0.6,
    )


def _regularity_score(
    entropy: float,
    max_entropy: float,
    hex_fraction: float,
    area_cv: float,
    lewis_r2: float,
) -> Tuple[float, str]:
    """Compute composite regularity score 0–100.

    Components (weighted):
    - 30%: low normalised entropy (0 = ordered, 1 = disordered)
    - 30%: high hexagonal fraction
    - 20%: low area CV (uniform areas = regular)
    - 20%: Lewis's law fit quality

    Returns (score, interpretation).
    """
    norm_ent = entropy / max_entropy if max_entropy > 0 else 0.0
    entropy_score = max(0.0, 1.0 - norm_ent)

    # Area CV contribution: CV=0 → perfect, CV≥1 → poor
    area_score = max(0.0, 1.0 - min(area_cv, 1.0))

    score = (
        0.30 * entropy_score
        + 0.30 * hex_fraction
        + 0.20 * area_score
        + 0.20 * lewis_r2
    ) * 100.0

    score = max(0.0, min(100.0, score))

    if score >= 90:
        interp = "Highly regular (near-crystalline lattice)"
    elif score >= 70:
        interp = "Moderately regular"
    elif score >= 50:
        interp = "Semi-regular (some local order)"
    elif score >= 30:
        interp = "Disordered (Poisson-like randomness)"
    else:
        interp = "Highly disordered (irregular tessellation)"

    return score, interp


# ── Main entry point ────────────────────────────────────────────────

def regularity_analysis(
    region_stats: list,
    regions: dict,
    graph: dict,
) -> RegularityResult:
    """Run full Voronoi regularity and entropy analysis.

    Parameters
    ----------
    region_stats : list of dict
        Output of ``compute_region_stats()``.
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    graph : dict
        Output of ``extract_neighborhood_graph()``.

    Returns
    -------
    RegularityResult
    """
    if not region_stats:
        return RegularityResult(interpretation="No cells to analyse")

    # Extract side counts and areas
    side_counts = [s["vertex_count"] for s in region_stats]
    areas = [s["area"] for s in region_stats]

    # Polygon distribution
    dist = polygon_distribution(side_counts)

    # Shannon entropy
    ent = voronoi_entropy(side_counts)
    distinct_types = len(dist.counts)
    max_ent = math.log2(distinct_types) if distinct_types > 1 else 0.0
    norm_ent = ent / max_ent if max_ent > 0 else 0.0

    # Hexagonal fraction
    hex_frac = dist.fractions.get(6, 0.0)

    # Area coefficient of variation
    if areas:
        mean_area = sum(areas) / len(areas)
        if mean_area > 0:
            std_area = math.sqrt(sum((a - mean_area) ** 2 for a in areas) / len(areas))
            area_cv = std_area / mean_area
        else:
            area_cv = 0.0
    else:
        area_cv = 0.0

    # Lewis's law
    lewis = lewis_law_fit(side_counts, areas)

    # Aboav-Weaire law
    adjacency = graph.get("adjacency", {})
    # Build seed → side count mapping
    seed_to_sides: Dict[Tuple[float, float], int] = {}
    sorted_seeds = sorted(regions.keys())
    for i, seed in enumerate(sorted_seeds):
        key = tuple(seed) if not isinstance(seed, tuple) else seed
        if i < len(side_counts):
            seed_to_sides[key] = side_counts[i]

    aboav = aboav_weaire_fit(side_counts, adjacency, seed_to_sides)

    # Composite score
    score, interp = _regularity_score(
        ent, max_ent, hex_frac, area_cv, lewis.r_squared
    )

    return RegularityResult(
        entropy=ent,
        max_entropy=max_ent,
        normalised_entropy=norm_ent,
        hex_fraction=hex_frac,
        area_cv=area_cv,
        regularity_score=score,
        interpretation=interp,
        distribution=dist,
        lewis=lewis,
        aboav=aboav,
    )


# ── Export ──────────────────────────────────────────────────────────

def export_regularity_json(result: RegularityResult, path: str) -> str:
    """Write regularity analysis to JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path


def format_report(result: RegularityResult) -> str:
    """Format a human-readable regularity report."""
    lines = [
        "═══ Voronoi Regularity & Entropy Analysis ═══",
        "",
        f"  Cells analysed:       {result.distribution.total_cells}",
        f"  Shannon entropy:      {result.entropy:.4f} bits",
        f"  Max entropy:          {result.max_entropy:.4f} bits",
        f"  Normalised entropy:   {result.normalised_entropy:.4f}",
        f"  Hexagonal fraction:   {result.hex_fraction:.1%}",
        f"  Area CV:              {result.area_cv:.4f}",
        f"  Regularity score:     {result.regularity_score:.1f}/100",
        f"  Interpretation:       {result.interpretation}",
        "",
        "── Polygon Distribution ──",
    ]

    for n in sorted(result.distribution.counts):
        c = result.distribution.counts[n]
        pct = result.distribution.fractions[n] * 100
        bar = "█" * max(1, int(pct / 2))
        lines.append(f"  {n:2d}-gon: {c:4d} ({pct:5.1f}%) {bar}")

    lines.append(f"  Mean sides: {result.distribution.mean_sides:.2f}")
    lines.append(f"  Variance:   {result.distribution.variance:.4f}")

    lines.append("")
    lines.append("── Lewis's Law ──")
    lines.append(f"  A(n) = {result.lewis.slope:.4f} · n + ({result.lewis.intercept_n:.2f})")
    lines.append(f"  R² = {result.lewis.r_squared:.4f}")
    lines.append(f"  Holds: {'Yes' if result.lewis.holds else 'No'}")

    lines.append("")
    lines.append("── Aboav-Weaire Law ──")
    lines.append(f"  a = {result.aboav.a:.4f}")
    lines.append(f"  R² = {result.aboav.r_squared:.4f}")
    lines.append(f"  Holds: {'Yes' if result.aboav.holds else 'No'}")

    return "\n".join(lines)
