"""Spatial Interaction & Gravity Model for Voronoi diagrams (vormap_gravity).

Models flows and interactions between Voronoi regions using gravity-model
variants.  Given region masses (population, revenue, importance) and the
distances between region centroids, this module computes:

- **Interaction flows** between all region pairs
- **Market areas** (Huff model — probabilistic catchment zones)
- **Accessibility scores** (Hansen model — opportunity potential)
- **Flow dominance** (which regions attract the most interaction)

Four gravity model variants are supported:

- **Classic** — ``F = k * M_i * M_j / d^beta``
- **Huff** — probability of region *j* attracting from *i*:
  ``P_ij = (M_j / d_ij^beta) / sum_k(M_k / d_ik^beta)``
- **Hansen** — accessibility of region *i*:
  ``A_i = sum_j(M_j / d_ij^beta)``
- **Doubly-Constrained** — total outflow and inflow constrained to
  observed origin/destination totals (iterative balancing).

Usage (Python API)::

    from vormap_gravity import (
        gravity_analysis, GravityConfig, GravityModel,
        export_gravity_svg, export_gravity_json, export_gravity_csv,
    )

    # Locations with masses
    locations = [(100, 200, 50), (400, 300, 120), (700, 500, 80)]
    # Each tuple: (x, y, mass)

    config = GravityConfig(model=GravityModel.HUFF, beta=2.0)
    result = gravity_analysis(locations, config)
    print(result.summary())

    export_gravity_svg(result, "gravity.svg", width=800, height=600)
    export_gravity_json(result, "gravity.json")
    export_gravity_csv(result, "gravity.csv")

    # With Voronoi regions
    import vormap, vormap_viz
    data = vormap.load_data("cities.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)
    result = gravity_analysis_from_stats(stats, attribute="area")

CLI::

    python vormap_gravity.py --locations "100,200,50;400,300,120;700,500,80"
    python vormap_gravity.py --locations "..." --model huff --beta 2.0
    python vormap_gravity.py --locations "..." --svg gravity.svg
    python vormap_gravity.py --locations "..." --json gravity.json
    python vormap_gravity.py --locations "..." --csv gravity.csv
    python vormap_gravity.py --generate 10 --model hansen

"""


import argparse
import csv
import json
import math
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from vormap import validate_output_path


# ── Enums & Configuration ───────────────────────────────────────────


class GravityModel(Enum):
    """Supported gravity model variants."""
    CLASSIC = "classic"
    HUFF = "huff"
    HANSEN = "hansen"
    DOUBLY_CONSTRAINED = "doubly_constrained"


class FlowCategory(Enum):
    """Categorisation of flow strength."""
    DOMINANT = "dominant"
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NEGLIGIBLE = "negligible"


@dataclass
class GravityConfig:
    """Configuration for a gravity model run.

    Parameters
    ----------
    model : GravityModel
        Which model variant to use.
    beta : float
        Distance decay exponent (higher = steeper decay).
    k : float
        Proportionality constant for classic model.
    self_interaction : bool
        Whether to include i==j flows (usually False).
    min_flow : float
        Minimum flow threshold — flows below this are dropped.
    max_iterations : int
        Max iterations for doubly-constrained balancing.
    convergence_threshold : float
        Convergence tolerance for doubly-constrained model.
    """
    model: GravityModel = GravityModel.CLASSIC
    beta: float = 2.0
    k: float = 1.0
    self_interaction: bool = False
    min_flow: float = 0.0
    max_iterations: int = 100
    convergence_threshold: float = 1e-6


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class Location:
    """A spatial location with mass (attractiveness)."""
    index: int
    x: float
    y: float
    mass: float
    label: str = ""


@dataclass
class Flow:
    """A directed interaction flow between two locations."""
    origin: int
    destination: int
    flow: float
    distance: float
    category: FlowCategory

    @property
    def flow_per_distance(self) -> float:
        """Flow intensity per unit distance."""
        return self.flow / max(self.distance, 1e-12)


@dataclass
class MarketArea:
    """Huff-model market area for a destination."""
    destination: int
    label: str
    total_probability: float
    primary_origins: List[int]  # origins where this dest has highest prob
    market_share: float  # fraction of all origins primarily attracted


@dataclass
class AccessibilityScore:
    """Hansen accessibility score for an origin."""
    location: int
    label: str
    score: float
    rank: int
    percentile: float


@dataclass
class GravityResult:
    """Full gravity model analysis result."""
    model: str
    beta: float
    k: float
    locations: List[Location]
    flows: List[Flow]
    flow_matrix: List[List[float]]
    distance_matrix: List[List[float]]
    total_flow: float
    avg_flow: float
    max_flow: float
    top_flows: List[Flow]
    market_areas: List[MarketArea]
    accessibility: List[AccessibilityScore]
    dominance_index: Dict[int, float]  # Gini-like dominance per dest
    convergence_iterations: int  # for doubly-constrained
    config_summary: str

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            "=" * 60,
            "  SPATIAL INTERACTION / GRAVITY MODEL REPORT",
            "=" * 60,
            "",
            f"  Model:            {self.model}",
            f"  Beta (decay):     {self.beta}",
            f"  K (constant):     {self.k}",
            f"  Locations:        {len(self.locations)}",
            f"  Total flows:      {len(self.flows)}",
            f"  Total interaction: {self.total_flow:.4f}",
            f"  Average flow:     {self.avg_flow:.4f}",
            f"  Max flow:         {self.max_flow:.4f}",
        ]
        if self.convergence_iterations > 0:
            lines.append(f"  Convergence:      {self.convergence_iterations} iterations")

        if self.top_flows:
            lines.append("")
            lines.append("── Top Flows ──────────────────────────────────────────")
            for f in self.top_flows[:10]:
                o = self.locations[f.origin]
                d = self.locations[f.destination]
                ol = o.label or f"L{f.origin}"
                dl = d.label or f"L{f.destination}"
                lines.append(
                    f"  {ol:>10s} → {dl:<10s}  "
                    f"flow={f.flow:8.4f}  dist={f.distance:7.2f}  [{f.category.value}]"
                )

        if self.market_areas:
            lines.append("")
            lines.append("── Market Areas (Huff) ────────────────────────────────")
            for ma in sorted(self.market_areas, key=lambda m: m.market_share, reverse=True):
                lbl = ma.label or f"L{ma.destination}"
                lines.append(
                    f"  {lbl:>10s}  share={ma.market_share:5.1%}  "
                    f"primary_origins={len(ma.primary_origins)}  "
                    f"total_prob={ma.total_probability:.4f}"
                )

        if self.accessibility:
            lines.append("")
            lines.append("── Accessibility (Hansen) ─────────────────────────────")
            for a in sorted(self.accessibility, key=lambda s: s.rank):
                lbl = a.label or f"L{a.location}"
                bar = "█" * min(40, int(a.percentile * 40 / 100))
                lines.append(f"  {a.rank:3d}. {lbl:>10s}  score={a.score:8.4f}  {bar}")

        if self.dominance_index:
            lines.append("")
            lines.append("── Flow Dominance ─────────────────────────────────────")
            for idx in sorted(self.dominance_index, key=self.dominance_index.get, reverse=True)[:5]:
                lbl = self.locations[idx].label or f"L{idx}"
                lines.append(f"  {lbl:>10s}  dominance={self.dominance_index[idx]:.4f}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)


# ── Core distance & flow computation ────────────────────────────────


def _euclidean(a: Location, b: Location) -> float:
    """Euclidean distance between two locations."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2)


def _build_distance_matrix(locations: List[Location]) -> List[List[float]]:
    """Compute pairwise distance matrix."""
    n = len(locations)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _euclidean(locations[i], locations[j])
            matrix[i][j] = d
            matrix[j][i] = d
    return matrix


def _categorise_flow(flow: float, max_flow: float) -> FlowCategory:
    """Categorise flow strength relative to maximum."""
    if max_flow <= 0:
        return FlowCategory.NEGLIGIBLE
    ratio = flow / max_flow
    if ratio >= 0.75:
        return FlowCategory.DOMINANT
    if ratio >= 0.5:
        return FlowCategory.STRONG
    if ratio >= 0.25:
        return FlowCategory.MODERATE
    if ratio >= 0.05:
        return FlowCategory.WEAK
    return FlowCategory.NEGLIGIBLE


# ── Model implementations ───────────────────────────────────────────


def _classic_model(
    locations: List[Location],
    dist_matrix: List[List[float]],
    config: GravityConfig,
) -> List[List[float]]:
    """Classic gravity model: F_ij = k * M_i * M_j / d_ij^beta."""
    n = len(locations)
    flows = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j and not config.self_interaction:
                continue
            d = dist_matrix[i][j]
            if d <= 0:
                continue
            flows[i][j] = config.k * locations[i].mass * locations[j].mass / (d ** config.beta)
    return flows


def _huff_model(
    locations: List[Location],
    dist_matrix: List[List[float]],
    config: GravityConfig,
) -> List[List[float]]:
    """Huff model: P_ij = (M_j / d_ij^beta) / sum_k(M_k / d_ik^beta).

    Returns probability matrix (rows sum to 1).
    """
    n = len(locations)
    probs = [[0.0] * n for _ in range(n)]
    for i in range(n):
        attractiveness = []
        for j in range(n):
            if i == j and not config.self_interaction:
                attractiveness.append(0.0)
                continue
            d = dist_matrix[i][j]
            if d <= 0:
                attractiveness.append(0.0)
                continue
            attractiveness.append(locations[j].mass / (d ** config.beta))
        total = sum(attractiveness)
        if total > 0:
            for j in range(n):
                probs[i][j] = attractiveness[j] / total
    return probs


def _hansen_model(
    locations: List[Location],
    dist_matrix: List[List[float]],
    config: GravityConfig,
) -> List[List[float]]:
    """Hansen accessibility: A_i = sum_j(M_j / d_ij^beta).

    Returns matrix where flows[i][j] = M_j / d_ij^beta (contribution
    of j to i's accessibility).
    """
    n = len(locations)
    flows = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j and not config.self_interaction:
                continue
            d = dist_matrix[i][j]
            if d <= 0:
                continue
            flows[i][j] = locations[j].mass / (d ** config.beta)
    return flows


def _doubly_constrained_model(
    locations: List[Location],
    dist_matrix: List[List[float]],
    config: GravityConfig,
) -> Tuple[List[List[float]], int]:
    """Doubly-constrained gravity model with iterative balancing.

    Constrains row sums to origin masses and column sums to destination
    masses via Furness (IPF) balancing.

    Returns (flow_matrix, iterations_used).
    """
    n = len(locations)
    if n == 0:
        return [], 0

    # Initial unconstrained cost matrix
    cost = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j and not config.self_interaction:
                continue
            d = dist_matrix[i][j]
            if d > 0:
                cost[i][j] = 1.0 / (d ** config.beta)

    # Target row/col sums = masses
    row_targets = [loc.mass for loc in locations]
    col_targets = [loc.mass for loc in locations]

    # Balancing factors
    a_factors = [1.0] * n
    b_factors = [1.0] * n

    iterations = 0
    for iteration in range(config.max_iterations):
        iterations = iteration + 1

        # Update A factors (row balancing)
        for i in range(n):
            row_sum = sum(a_factors[i] * b_factors[j] * cost[i][j] for j in range(n))
            if row_sum > 0 and row_targets[i] > 0:
                a_factors[i] = row_targets[i] / row_sum

        # Update B factors (column balancing)
        for j in range(n):
            col_sum = sum(a_factors[i] * b_factors[j] * cost[i][j] for i in range(n))
            if col_sum > 0 and col_targets[j] > 0:
                b_factors[j] = col_targets[j] / col_sum

        # Check convergence
        max_err = 0.0
        for i in range(n):
            row_sum = sum(a_factors[i] * b_factors[j] * cost[i][j] for j in range(n))
            if row_targets[i] > 0:
                max_err = max(max_err, abs(row_sum - row_targets[i]) / row_targets[i])
        for j in range(n):
            col_sum = sum(a_factors[i] * b_factors[j] * cost[i][j] for i in range(n))
            if col_targets[j] > 0:
                max_err = max(max_err, abs(col_sum - col_targets[j]) / col_targets[j])

        if max_err < config.convergence_threshold:
            break

    # Build final flow matrix
    flows = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            flows[i][j] = a_factors[i] * b_factors[j] * cost[i][j]

    return flows, iterations


# ── Market areas & accessibility ─────────────────────────────────────


def _compute_market_areas(
    locations: List[Location],
    prob_matrix: List[List[float]],
) -> List[MarketArea]:
    """Compute Huff market areas from probability matrix."""
    n = len(locations)
    areas: List[MarketArea] = []

    # For each destination, find which origins it primarily attracts
    primary: Dict[int, List[int]] = {j: [] for j in range(n)}
    for i in range(n):
        best_j = -1
        best_p = 0.0
        for j in range(n):
            if prob_matrix[i][j] > best_p:
                best_p = prob_matrix[i][j]
                best_j = j
        if best_j >= 0:
            primary[best_j].append(i)

    for j in range(n):
        total_prob = sum(prob_matrix[i][j] for i in range(n))
        areas.append(MarketArea(
            destination=j,
            label=locations[j].label,
            total_probability=total_prob,
            primary_origins=primary[j],
            market_share=len(primary[j]) / max(1, n),
        ))

    return areas


def _compute_accessibility(
    locations: List[Location],
    flow_matrix: List[List[float]],
) -> List[AccessibilityScore]:
    """Compute Hansen accessibility scores from flow contributions."""
    n = len(locations)
    scores = []
    for i in range(n):
        total = sum(flow_matrix[i])
        scores.append((i, total))

    scores.sort(key=lambda x: x[1], reverse=True)
    result: List[AccessibilityScore] = []
    for rank, (idx, score) in enumerate(scores, 1):
        result.append(AccessibilityScore(
            location=idx,
            label=locations[idx].label,
            score=score,
            rank=rank,
            percentile=100.0 * (n - rank) / max(1, n - 1) if n > 1 else 100.0,
        ))
    return result


def _compute_dominance(
    locations: List[Location],
    flow_matrix: List[List[float]],
) -> Dict[int, float]:
    """Compute flow dominance index for each destination.

    Dominance is the share of total inflow that a destination captures
    relative to the maximum possible (if it captured everything).
    """
    n = len(locations)
    total_all = sum(flow_matrix[i][j] for i in range(n) for j in range(n))
    if total_all <= 0:
        return {i: 0.0 for i in range(n)}

    dominance: Dict[int, float] = {}
    for j in range(n):
        inflow = sum(flow_matrix[i][j] for i in range(n))
        dominance[j] = inflow / total_all
    return dominance


# ── Main analysis entry point ────────────────────────────────────────


def gravity_analysis(
    location_tuples: List[Tuple[float, float, float]],
    config: Optional[GravityConfig] = None,
    labels: Optional[List[str]] = None,
) -> GravityResult:
    """Run gravity model analysis on a set of locations.

    Parameters
    ----------
    location_tuples : list of (x, y, mass)
        Spatial locations with mass/attractiveness values.
    config : GravityConfig or None
        Model configuration. Defaults to classic with beta=2.
    labels : list of str or None
        Optional labels for each location.

    Returns
    -------
    GravityResult
        Full analysis result with flows, market areas, accessibility.

    Raises
    ------
    ValueError
        If fewer than 2 locations provided or masses are negative.
    """
    if config is None:
        config = GravityConfig()

    if len(location_tuples) < 2:
        raise ValueError("At least 2 locations required for gravity analysis")

    locations: List[Location] = []
    for i, tup in enumerate(location_tuples):
        if len(tup) != 3:
            raise ValueError(f"Location {i} must be (x, y, mass), got {len(tup)} values")
        x, y, mass = tup
        if mass < 0:
            raise ValueError(f"Location {i} has negative mass: {mass}")
        lbl = labels[i] if labels and i < len(labels) else ""
        locations.append(Location(index=i, x=x, y=y, mass=mass, label=lbl))

    dist_matrix = _build_distance_matrix(locations)

    # Run the selected model
    convergence_iters = 0
    if config.model == GravityModel.CLASSIC:
        flow_matrix = _classic_model(locations, dist_matrix, config)
    elif config.model == GravityModel.HUFF:
        flow_matrix = _huff_model(locations, dist_matrix, config)
    elif config.model == GravityModel.HANSEN:
        flow_matrix = _hansen_model(locations, dist_matrix, config)
    elif config.model == GravityModel.DOUBLY_CONSTRAINED:
        flow_matrix, convergence_iters = _doubly_constrained_model(
            locations, dist_matrix, config
        )
    else:
        raise ValueError(f"Unknown model: {config.model}")

    # Build flow list
    n = len(locations)
    all_flows: List[Flow] = []
    max_flow = 0.0
    for i in range(n):
        for j in range(n):
            if i == j and not config.self_interaction:
                continue
            val = flow_matrix[i][j]
            if val > max_flow:
                max_flow = val

    for i in range(n):
        for j in range(n):
            if i == j and not config.self_interaction:
                continue
            val = flow_matrix[i][j]
            if val < config.min_flow:
                continue
            all_flows.append(Flow(
                origin=i,
                destination=j,
                flow=val,
                distance=dist_matrix[i][j],
                category=_categorise_flow(val, max_flow),
            ))

    total_flow = sum(f.flow for f in all_flows)
    avg_flow = total_flow / max(1, len(all_flows))
    top_flows = sorted(all_flows, key=lambda f: f.flow, reverse=True)[:10]

    # Compute market areas (Huff probabilities)
    if config.model == GravityModel.HUFF:
        prob_matrix = flow_matrix  # already probabilities
    else:
        # Compute Huff probabilities for market area analysis regardless
        prob_matrix = _huff_model(locations, dist_matrix, config)
    market_areas = _compute_market_areas(locations, prob_matrix)

    # Compute accessibility
    if config.model == GravityModel.HANSEN:
        hansen_matrix = flow_matrix
    else:
        hansen_matrix = _hansen_model(locations, dist_matrix, config)
    accessibility = _compute_accessibility(locations, hansen_matrix)

    # Compute dominance
    dominance = _compute_dominance(locations, flow_matrix)

    return GravityResult(
        model=config.model.value,
        beta=config.beta,
        k=config.k,
        locations=locations,
        flows=all_flows,
        flow_matrix=flow_matrix,
        distance_matrix=dist_matrix,
        total_flow=total_flow,
        avg_flow=avg_flow,
        max_flow=max_flow,
        top_flows=top_flows,
        market_areas=market_areas,
        accessibility=accessibility,
        dominance_index=dominance,
        convergence_iterations=convergence_iters,
        config_summary=f"{config.model.value} beta={config.beta} k={config.k}",
    )


def gravity_analysis_from_stats(
    stats: List[Dict[str, Any]],
    attribute: str = "area",
    config: Optional[GravityConfig] = None,
) -> GravityResult:
    """Run gravity analysis using Voronoi region stats.

    Parameters
    ----------
    stats : list of dict
        Region stats from ``vormap_viz.compute_region_stats()``.
    attribute : str
        Attribute name to use as mass (default: "area").
    config : GravityConfig or None
        Model configuration.

    Returns
    -------
    GravityResult
    """
    locations: List[Tuple[float, float, float]] = []
    labels: List[str] = []
    for i, s in enumerate(stats):
        cx = s.get("centroid_x", 0.0)
        cy = s.get("centroid_y", 0.0)
        mass = s.get(attribute, 1.0)
        if mass < 0:
            mass = 0.0
        locations.append((cx, cy, mass))
        labels.append(f"R{i}")
    return gravity_analysis(locations, config=config, labels=labels)


# ── Export: SVG ──────────────────────────────────────────────────────


def export_gravity_svg(
    result: GravityResult,
    path: str,
    *,
    width: int = 800,
    height: int = 600,
    show_flows: bool = True,
    min_flow_category: FlowCategory = FlowCategory.WEAK,
) -> str:
    """Export gravity analysis as an SVG visualisation.

    Draws locations as circles (sized by mass) and flows as arrows
    (width by flow strength, colour by category).

    Parameters
    ----------
    result : GravityResult
        Analysis result.
    path : str
        Output SVG file path.
    width, height : int
        SVG canvas dimensions.
    show_flows : bool
        Whether to draw flow arrows.
    min_flow_category : FlowCategory
        Minimum category to draw (filter out weaker flows).

    Returns
    -------
    str
        Resolved output path.
    """
    resolved = validate_output_path(path, allow_absolute=True)

    locs = result.locations
    if not locs:
        raise ValueError("No locations to visualise")

    # Compute bounding box with padding
    xs = [loc.x for loc in locs]
    ys = [loc.y for loc in locs]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    range_x = max_x - min_x or 1.0
    range_y = max_y - min_y or 1.0
    pad = 60

    def tx(x: float) -> float:
        return pad + (x - min_x) / range_x * (width - 2 * pad)

    def ty(y: float) -> float:
        return pad + (1.0 - (y - min_y) / range_y) * (height - 2 * pad)

    # Category colours
    cat_colors = {
        FlowCategory.DOMINANT: "#d32f2f",
        FlowCategory.STRONG: "#f57c00",
        FlowCategory.MODERATE: "#fbc02d",
        FlowCategory.WEAK: "#90caf9",
        FlowCategory.NEGLIGIBLE: "#e0e0e0",
    }
    cat_order = [
        FlowCategory.NEGLIGIBLE, FlowCategory.WEAK,
        FlowCategory.MODERATE, FlowCategory.STRONG, FlowCategory.DOMINANT,
    ]
    min_cat_idx = cat_order.index(min_flow_category)

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height),
                     viewBox=f"0 0 {width} {height}")

    # Background
    ET.SubElement(svg, "rect", width=str(width), height=str(height),
                  fill="#fafafa", stroke="#ccc")

    # Title
    title = ET.SubElement(svg, "text", x=str(width // 2), y="20",
                          fill="#333")
    title.set("font-size", "14")
    title.set("text-anchor", "middle")
    title.set("font-family", "sans-serif")
    title.text = f"Gravity Model: {result.model} (beta={result.beta})"

    # Draw flows as lines
    if show_flows and result.flows:
        max_width = 6.0
        for fl in result.flows:
            cat_idx = cat_order.index(fl.category)
            if cat_idx < min_cat_idx:
                continue
            o = locs[fl.origin]
            d = locs[fl.destination]
            ratio = fl.flow / max(result.max_flow, 1e-12)
            lw = max(0.5, ratio * max_width)
            opacity = max(0.2, min(0.9, ratio))
            line = ET.SubElement(svg, "line",
                          x1=f"{tx(o.x):.1f}", y1=f"{ty(o.y):.1f}",
                          x2=f"{tx(d.x):.1f}", y2=f"{ty(d.y):.1f}",
                          stroke=cat_colors[fl.category])
            line.set("stroke-width", f"{lw:.1f}")
            line.set("stroke-opacity", f"{opacity:.2f}")

    # Draw locations as circles
    max_mass = max(loc.mass for loc in locs) if locs else 1.0
    for loc in locs:
        r = max(5, (loc.mass / max(max_mass, 1e-12)) * 25)
        cx_s = f"{tx(loc.x):.1f}"
        cy_s = f"{ty(loc.y):.1f}"
        circle = ET.SubElement(svg, "circle", cx=cx_s, cy=cy_s, r=f"{r:.1f}",
                      fill="#1976d2", stroke="#0d47a1")
        circle.set("stroke-width", "1.5")
        circle.set("fill-opacity", "0.7")
        lbl = loc.label or f"L{loc.index}"
        label_el = ET.SubElement(svg, "text", x=cx_s, y=f"{ty(loc.y) - r - 4:.1f}",
                                 fill="#333")
        label_el.set("font-size", "10")
        label_el.set("text-anchor", "middle")
        label_el.set("font-family", "sans-serif")
        label_el.text = f"{lbl} ({loc.mass:.0f})"

    # Legend
    legend_y = height - 25
    legend_x = 10
    for cat in cat_order:
        ET.SubElement(svg, "rect", x=str(legend_x), y=str(legend_y),
                      width="12", height="12", fill=cat_colors[cat], stroke="#999")
        lbl_el = ET.SubElement(svg, "text", x=str(legend_x + 16), y=str(legend_y + 10),
                               fill="#555")
        lbl_el.set("font-size", "9")
        lbl_el.set("font-family", "sans-serif")
        lbl_el.text = cat.value
        legend_x += len(cat.value) * 7 + 28

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(resolved, xml_declaration=True, encoding="unicode")
    return resolved


# ── Export: JSON ─────────────────────────────────────────────────────


def export_gravity_json(result: GravityResult, path: str) -> str:
    """Export gravity analysis result as JSON.

    Parameters
    ----------
    result : GravityResult
        Analysis result.
    path : str
        Output JSON file path.

    Returns
    -------
    str
        Resolved output path.
    """
    resolved = validate_output_path(path, allow_absolute=True)
    data = {
        "model": result.model,
        "beta": result.beta,
        "k": result.k,
        "locations": [
            {"index": l.index, "x": l.x, "y": l.y, "mass": l.mass,
             "label": l.label}
            for l in result.locations
        ],
        "flows": [
            {"origin": f.origin, "destination": f.destination,
             "flow": round(f.flow, 6), "distance": round(f.distance, 4),
             "category": f.category.value}
            for f in result.flows
        ],
        "total_flow": round(result.total_flow, 6),
        "avg_flow": round(result.avg_flow, 6),
        "max_flow": round(result.max_flow, 6),
        "market_areas": [
            {"destination": ma.destination, "label": ma.label,
             "total_probability": round(ma.total_probability, 4),
             "primary_origins": ma.primary_origins,
             "market_share": round(ma.market_share, 4)}
            for ma in result.market_areas
        ],
        "accessibility": [
            {"location": a.location, "label": a.label,
             "score": round(a.score, 4), "rank": a.rank,
             "percentile": round(a.percentile, 1)}
            for a in result.accessibility
        ],
        "dominance_index": {str(k): round(v, 4) for k, v in result.dominance_index.items()},
        "convergence_iterations": result.convergence_iterations,
    }
    with open(resolved, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return resolved


# ── Export: CSV ──────────────────────────────────────────────────────


def export_gravity_csv(result: GravityResult, path: str) -> str:
    """Export flow list as CSV.

    Parameters
    ----------
    result : GravityResult
        Analysis result.
    path : str
        Output CSV file path.

    Returns
    -------
    str
        Resolved output path.
    """
    resolved = validate_output_path(path, allow_absolute=True)
    with open(resolved, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "origin", "origin_label", "origin_x", "origin_y", "origin_mass",
            "destination", "dest_label", "dest_x", "dest_y", "dest_mass",
            "flow", "distance", "category",
        ])
        for fl in result.flows:
            o = result.locations[fl.origin]
            d = result.locations[fl.destination]
            writer.writerow([
                fl.origin, o.label or f"L{o.index}", round(o.x, 4), round(o.y, 4), round(o.mass, 4),
                fl.destination, d.label or f"L{d.index}", round(d.x, 4), round(d.y, 4), round(d.mass, 4),
                round(fl.flow, 6), round(fl.distance, 4), fl.category.value,
            ])
    return resolved


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_locations(raw: str) -> List[Tuple[float, float, float]]:
    """Parse 'x,y,mass;x,y,mass;...' into location tuples."""
    locations: List[Tuple[float, float, float]] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        vals = [float(v.strip()) for v in part.split(",")]
        if len(vals) != 3:
            raise ValueError(f"Expected x,y,mass but got {len(vals)} values: '{part}'")
        locations.append((vals[0], vals[1], vals[2]))
    return locations


def main(argv: Optional[list] = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="vormap_gravity",
        description="Spatial interaction / gravity model for Voronoi diagrams",
    )
    parser.add_argument("--locations", type=str,
                        help="Locations as 'x,y,mass;x,y,mass;...'")
    parser.add_argument("--generate", type=int, default=0,
                        help="Generate N random locations for demo")
    parser.add_argument("--model", type=str, default="classic",
                        choices=["classic", "huff", "hansen", "doubly_constrained"],
                        help="Gravity model variant")
    parser.add_argument("--beta", type=float, default=2.0,
                        help="Distance decay exponent")
    parser.add_argument("--k", type=float, default=1.0,
                        help="Proportionality constant")
    parser.add_argument("--svg", type=str, default=None,
                        help="Export SVG visualisation to path")
    parser.add_argument("--json", type=str, default=None,
                        help="Export JSON result to path")
    parser.add_argument("--csv", type=str, default=None,
                        help="Export CSV flows to path")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for --generate")
    parser.add_argument("--labels", type=str, default=None,
                        help="Comma-separated location labels")

    args = parser.parse_args(argv)

    if not args.locations and args.generate <= 0:
        parser.error("Provide --locations or --generate N")

    if args.generate > 0:
        rng = random.Random(args.seed)
        location_tuples = [
            (rng.uniform(0, 1000), rng.uniform(0, 1000), rng.uniform(10, 500))
            for _ in range(args.generate)
        ]
    else:
        location_tuples = _parse_locations(args.locations)

    labels = args.labels.split(",") if args.labels else None

    config = GravityConfig(
        model=GravityModel(args.model),
        beta=args.beta,
        k=args.k,
    )

    result = gravity_analysis(location_tuples, config=config, labels=labels)
    print(result.summary())

    if args.svg:
        out = export_gravity_svg(result, args.svg)
        print(f"\nSVG written to {out}")
    if args.json:
        out = export_gravity_json(result, args.json)
        print(f"\nJSON written to {out}")
    if args.csv:
        out = export_gravity_csv(result, args.csv)
        print(f"\nCSV written to {out}")


if __name__ == "__main__":
    main()
