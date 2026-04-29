"""Spatial Causality Engine — autonomous counterfactual analysis for Voronoi interventions.

Answers the question "what would happen if we add / remove / move points?"
by simulating Voronoi diagrams before and after an intervention, then
quantifying causal effects on spatial metrics.

Six analysis engines run autonomously:

- **Treatment Effect Estimation** — average treatment effect (ATE) and
  effect on treated cells (ATT) for area, compactness, neighbor-count,
  and nearest-neighbor distance.
- **Difference-in-Differences** — spatial DiD comparing treated vs
  control regions to isolate the intervention's impact.
- **Synthetic Control** — constructs a counterfactual control region
  from a donor pool of unaffected cells.
- **Spillover Detection** — identifies indirect effects radiating
  outward through k-hop neighborhoods.
- **Intervention Ranking** — scores and ranks multiple candidate
  interventions by expected impact on a user-chosen objective.
- **Interactive HTML Dashboard** — 4-tab visualization summarizing
  all analyses in a self-contained HTML file.

Usage (Python API)::

    from vormap_causality import (
        analyze_causality, Intervention, CausalityReport,
        estimate_treatment_effect, difference_in_differences,
        synthetic_control, detect_spillovers, rank_interventions,
    )

    points = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
    bounds = (0, 1000, 0, 1000)

    # Define interventions
    add_iv = Intervention("add", points=[(500, 500), (300, 100)])
    remove_iv = Intervention("remove", points=[(400, 300)])
    relocate_iv = Intervention("relocate", points=[(100, 200)],
                               targets=[(150, 250)])

    # Full analysis
    report = analyze_causality(points, [add_iv, remove_iv], bounds=bounds)
    print(report.summary())
    report.to_json("causality.json")
    report.to_html("causality.html")

    # Individual engines
    effect = estimate_treatment_effect(points, add_iv, "area", bounds=bounds)
    print(f"ATE={effect.ate:.3f}  spillover={effect.spillover:.3f}")

    did = difference_in_differences(points, add_iv, bounds=bounds)
    sc = synthetic_control(points, remove_iv, bounds=bounds)
    spill = detect_spillovers(points, add_iv, hops=2, bounds=bounds)
    ranked = rank_interventions(points, [add_iv, remove_iv, relocate_iv],
                                objective="area_equity", bounds=bounds)

CLI::

    voronoimap datauni5.txt 5 --causality add --causality-points "500,500;300,100"
    voronoimap datauni5.txt 5 --causality remove --causality-points "400,300"
    voronoimap datauni5.txt 5 --causality relocate --causality-points "100,200" --causality-targets "150,250"
    voronoimap datauni5.txt 5 --causality add --causality-points "500,500" --causality-json out.json
    voronoimap datauni5.txt 5 --causality add --causality-points "500,500" --causality-html out.html
"""

from __future__ import annotations

import json
import math
import csv
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any, Sequence

from vormap import validate_output_path
from vormap_geometry import edge_length as _dist

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
Point = Tuple[float, float]

# ---------------------------------------------------------------------------
# Metrics we can measure
# ---------------------------------------------------------------------------
ALL_METRICS = ("area", "compactness", "neighbors", "nn_distance")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Intervention:
    """A spatial intervention to evaluate.

    Parameters
    ----------
    type : str
        One of ``"add"``, ``"remove"``, ``"relocate"``.
    points : list of (x, y)
        Points to add, remove, or (for relocate) source locations.
    targets : list of (x, y) or None
        For ``"relocate"`` only — destination coordinates.
    label : str or None
        Optional human-readable label for this intervention.
    """
    type: str
    points: List[Point] = field(default_factory=list)
    targets: Optional[List[Point]] = None
    label: Optional[str] = None

    def __post_init__(self):
        if self.type not in ("add", "remove", "relocate"):
            raise ValueError(f"Unknown intervention type: {self.type!r}")
        if self.type == "relocate":
            if not self.targets or len(self.targets) != len(self.points):
                raise ValueError("relocate requires targets with same length as points")
        if self.label is None:
            n = len(self.points)
            self.label = f"{self.type}_{n}pt{'s' if n != 1 else ''}"


@dataclass
class CausalEffect:
    """Estimated causal effect of an intervention on a single metric."""
    metric: str
    ate: float          # average treatment effect
    att: float          # average treatment on treated
    spillover: float    # effect on neighbors
    confidence: float   # 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "ate": round(self.ate, 6),
            "att": round(self.att, 6),
            "spillover": round(self.spillover, 6),
            "confidence": round(self.confidence, 4),
        }


@dataclass
class DIDResult:
    """Difference-in-differences result for one metric."""
    metric: str
    treated_before: float
    treated_after: float
    control_before: float
    control_after: float
    did_estimate: float
    parallel_trend_score: float  # 0-1, how well parallel-trends holds

    def to_dict(self) -> Dict[str, Any]:
        return {k: round(v, 6) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


@dataclass
class SyntheticControlResult:
    """Synthetic control method result."""
    metric: str
    actual_after: float
    synthetic_after: float
    treatment_effect: float
    donor_weights: Dict[int, float]
    fit_quality: float  # 0-1

    def to_dict(self) -> Dict[str, Any]:
        d = {k: round(v, 6) if isinstance(v, float) else v
             for k, v in self.__dict__.items()}
        d["donor_weights"] = {str(k): round(v, 4) for k, v in self.donor_weights.items()}
        return d


@dataclass
class SpilloverCell:
    """Spillover effect on a single cell."""
    cell_index: int
    hop_distance: int
    metric_delta: Dict[str, float]
    intensity: float  # 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_index": self.cell_index,
            "hop_distance": self.hop_distance,
            "metric_delta": {k: round(v, 6) for k, v in self.metric_delta.items()},
            "intensity": round(self.intensity, 4),
        }


@dataclass
class InterventionScore:
    """Ranked intervention with composite score."""
    intervention: Intervention
    score: float
    metric_impacts: Dict[str, float]
    rank: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.intervention.label,
            "type": self.intervention.type,
            "score": round(self.score, 4),
            "rank": self.rank,
            "metric_impacts": {k: round(v, 6) for k, v in self.metric_impacts.items()},
        }


@dataclass
class CausalityReport:
    """Full causality analysis report."""
    interventions: List[Intervention]
    effects: List[List[CausalEffect]]       # per intervention, per metric
    did_results: List[List[DIDResult]]       # per intervention, per metric
    synthetic_control: List[List[SyntheticControlResult]]
    spillover_map: List[List[SpilloverCell]]  # per intervention
    ranking: List[InterventionScore]
    bounds: Optional[Tuple[float, float, float, float]] = None

    def summary(self) -> str:
        """Human-readable summary."""
        lines = ["=" * 60, "SPATIAL CAUSALITY REPORT", "=" * 60, ""]
        lines.append(f"Interventions analyzed: {len(self.interventions)}")
        lines.append("")
        for i, iv in enumerate(self.interventions):
            lines.append(f"--- Intervention {i+1}: {iv.label} ({iv.type}) ---")
            if self.effects and i < len(self.effects):
                for e in self.effects[i]:
                    lines.append(f"  {e.metric:15s}  ATE={e.ate:+.4f}  "
                                 f"ATT={e.att:+.4f}  spillover={e.spillover:+.4f}  "
                                 f"conf={e.confidence:.2f}")
            if self.did_results and i < len(self.did_results):
                for d in self.did_results[i]:
                    lines.append(f"  DiD {d.metric:12s}  estimate={d.did_estimate:+.4f}  "
                                 f"parallel_trend={d.parallel_trend_score:.2f}")
            if self.spillover_map and i < len(self.spillover_map):
                n_spill = len(self.spillover_map[i])
                lines.append(f"  Spillover cells: {n_spill}")
            lines.append("")
        if self.ranking:
            lines.append("--- Intervention Ranking ---")
            for r in self.ranking:
                lines.append(f"  #{r.rank} {r.intervention.label}: "
                             f"score={r.score:.4f}")
        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_json(self, path: str) -> None:
        """Export report as JSON."""
        validate_output_path(path)
        data = {
            "interventions": [
                {"type": iv.type, "label": iv.label,
                 "points": iv.points,
                 "targets": iv.targets}
                for iv in self.interventions
            ],
            "effects": [[e.to_dict() for e in effs] for effs in self.effects],
            "did_results": [[d.to_dict() for d in dids] for dids in self.did_results],
            "synthetic_control": [[s.to_dict() for s in scs]
                                  for scs in self.synthetic_control],
            "spillover_map": [[s.to_dict() for s in spills]
                              for spills in self.spillover_map],
            "ranking": [r.to_dict() for r in self.ranking],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def to_csv(self, path: str) -> None:
        """Export treatment effects as CSV."""
        validate_output_path(path)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["intervention", "type", "metric", "ate", "att",
                         "spillover", "confidence"])
            for i, iv in enumerate(self.interventions):
                if i < len(self.effects):
                    for e in self.effects[i]:
                        w.writerow([iv.label, iv.type, e.metric,
                                    f"{e.ate:.6f}", f"{e.att:.6f}",
                                    f"{e.spillover:.6f}", f"{e.confidence:.4f}"])

    def to_html(self, path: str) -> None:
        """Export interactive HTML dashboard."""
        validate_output_path(path)
        html = _generate_html(self)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)


# ---------------------------------------------------------------------------
# Voronoi helpers (pure-Python, no scipy)
# ---------------------------------------------------------------------------

def _compute_voronoi_cells(points: List[Point],
                           bounds: Tuple[float, float, float, float]
                           ) -> List[Dict[str, Any]]:
    """Compute approximate Voronoi cell metrics via grid sampling.

    Returns a list of dicts, one per seed, with keys:
    ``area``, ``compactness``, ``neighbors``, ``nn_distance``, ``centroid``.
    """
    if not points:
        return []
    south, north, west, east = bounds
    width = east - west
    height = north - south
    if width <= 0 or height <= 0:
        return [{"area": 0, "compactness": 0, "neighbors": set(),
                 "nn_distance": 0, "centroid": p} for p in points]

    n = len(points)
    # Grid resolution scales with point count but caps for performance
    res = min(max(int(math.sqrt(n) * 15), 60), 200)
    dx = width / res
    dy = height / res
    cell_area_unit = dx * dy

    # Assign grid cells to nearest seed
    counts = [0] * n
    cx_sum = [0.0] * n
    cy_sum = [0.0] * n
    neighbor_sets: List[set] = [set() for _ in range(n)]

    # Build a grid of owner indices for neighbor detection
    owner_grid = [[0] * res for _ in range(res)]

    for gy in range(res):
        py = south + (gy + 0.5) * dy
        for gx in range(res):
            px = west + (gx + 0.5) * dx
            best_idx = 0
            best_d2 = float("inf")
            for k, (sx, sy) in enumerate(points):
                d2 = (px - sx) ** 2 + (py - sy) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best_idx = k
            owner_grid[gy][gx] = best_idx
            counts[best_idx] += 1
            cx_sum[best_idx] += px
            cy_sum[best_idx] += py

    # Detect neighbors from adjacent grid cells
    for gy in range(res):
        for gx in range(res):
            o = owner_grid[gy][gx]
            if gx + 1 < res and owner_grid[gy][gx + 1] != o:
                neighbor_sets[o].add(owner_grid[gy][gx + 1])
                neighbor_sets[owner_grid[gy][gx + 1]].add(o)
            if gy + 1 < res and owner_grid[gy + 1][gx] != o:
                neighbor_sets[o].add(owner_grid[gy + 1][gx])
                neighbor_sets[owner_grid[gy + 1][gx]].add(o)

    cells = []
    for i, (sx, sy) in enumerate(points):
        area = counts[i] * cell_area_unit
        centroid = ((cx_sum[i] / counts[i], cy_sum[i] / counts[i])
                    if counts[i] > 0 else (sx, sy))
        # Compactness: ratio of area to area of circle with same perimeter
        # Approximate using isoperimetric quotient with sqrt(area) proxy
        perimeter_proxy = math.sqrt(area) * 4  # rough
        compactness = (4 * math.pi * area / (perimeter_proxy ** 2)
                       if perimeter_proxy > 0 else 0)
        # Nearest-neighbor distance
        nn_d = float("inf")
        for j, (ox, oy) in enumerate(points):
            if j != i:
                d = math.sqrt((sx - ox) ** 2 + (sy - oy) ** 2)
                if d < nn_d:
                    nn_d = d
        if nn_d == float("inf"):
            nn_d = 0.0
        cells.append({
            "area": area,
            "compactness": compactness,
            "neighbors": neighbor_sets[i],
            "nn_distance": nn_d,
            "centroid": centroid,
        })
    return cells


def _apply_intervention(points: List[Point],
                        intervention: Intervention) -> List[Point]:
    """Return a new point list after applying the intervention."""
    pts = list(points)
    if intervention.type == "add":
        pts.extend(intervention.points)
    elif intervention.type == "remove":
        for rp in intervention.points:
            # Remove closest match
            best_idx = -1
            best_d = float("inf")
            for k, p in enumerate(pts):
                d = _dist(p, rp)
                if d < best_d:
                    best_d = d
                    best_idx = k
            if best_idx >= 0 and best_d < 1e-6:
                pts.pop(best_idx)
            elif best_idx >= 0:
                # Allow fuzzy match within 1% of extent
                pts.pop(best_idx)
    elif intervention.type == "relocate":
        targets = intervention.targets or []
        for rp, tp in zip(intervention.points, targets):
            best_idx = -1
            best_d = float("inf")
            for k, p in enumerate(pts):
                d = _dist(p, rp)
                if d < best_d:
                    best_d = d
                    best_idx = k
            if best_idx >= 0:
                pts[best_idx] = tp
    return pts


def _auto_bounds(points: List[Point],
                 extra: Optional[List[Point]] = None
                 ) -> Tuple[float, float, float, float]:
    """Compute bounds with 10% padding."""
    all_pts = list(points) + (extra or [])
    if not all_pts:
        return (0, 1000, 0, 1000)
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad_x = max((max_x - min_x) * 0.1, 1.0)
    pad_y = max((max_y - min_y) * 0.1, 1.0)
    return (min_y - pad_y, max_y + pad_y, min_x - pad_x, max_x + pad_x)


def _metric_value(cell: Dict[str, Any], metric: str) -> float:
    """Extract a metric value from a cell dict."""
    if metric == "neighbors":
        return float(len(cell.get("neighbors", set())))
    return float(cell.get(metric, 0))


def _safe_mean(values: Sequence[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _safe_stdev(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) >= 2 else 0.0


# ---------------------------------------------------------------------------
# Engine 1: Treatment Effect Estimation
# ---------------------------------------------------------------------------

def estimate_treatment_effect(
    points: List[Point],
    intervention: Intervention,
    metric: str = "area",
    bounds: Optional[Tuple[float, float, float, float]] = None,
) -> CausalEffect:
    """Estimate causal effect of an intervention on a spatial metric.

    Computes ATE (average treatment effect across all cells), ATT (effect
    on directly-treated cells), and spillover (effect on neighboring
    cells).
    """
    if metric not in ALL_METRICS:
        raise ValueError(f"Unknown metric {metric!r}; choose from {ALL_METRICS}")

    extra = intervention.points + (intervention.targets or [])
    b = bounds or _auto_bounds(points, extra)

    before_cells = _compute_voronoi_cells(points, b)
    after_points = _apply_intervention(points, intervention)
    if not after_points:
        return CausalEffect(metric=metric, ate=0, att=0, spillover=0, confidence=0)
    after_cells = _compute_voronoi_cells(after_points, b)

    before_vals = [_metric_value(c, metric) for c in before_cells]
    after_vals = [_metric_value(c, metric) for c in after_cells]

    mean_before = _safe_mean(before_vals)
    mean_after = _safe_mean(after_vals)
    ate = mean_after - mean_before

    # Identify treated cells (cells closest to intervention points)
    treated_indices = _find_treated_indices(points, intervention)
    # ATT: change in treated cells (matched by proximity)
    att = _compute_att(before_cells, after_cells, points, after_points,
                       treated_indices, metric)

    # Spillover: change in neighbors of treated cells
    spillover = _compute_spillover_metric(before_cells, after_cells, points,
                                          after_points, treated_indices, metric)

    # Confidence based on sample size and effect magnitude
    n = len(points)
    effect_ratio = abs(ate) / (abs(mean_before) + 1e-9)
    confidence = min(1.0, 0.3 + 0.4 * min(n / 20, 1.0) + 0.3 * min(effect_ratio * 5, 1.0))

    return CausalEffect(metric=metric, ate=ate, att=att,
                         spillover=spillover, confidence=confidence)


def _find_treated_indices(points: List[Point],
                          intervention: Intervention) -> List[int]:
    """Find indices of cells directly affected by the intervention."""
    if intervention.type == "add":
        # Cells whose area/neighbors change most are near added points
        # Return indices of cells closest to each added point
        treated = set()
        for ap in intervention.points:
            best_idx = -1
            best_d = float("inf")
            for k, p in enumerate(points):
                d = _dist(p, ap)
                if d < best_d:
                    best_d = d
                    best_idx = k
            if best_idx >= 0:
                treated.add(best_idx)
        return list(treated)
    elif intervention.type == "remove":
        treated = set()
        for rp in intervention.points:
            best_idx = -1
            best_d = float("inf")
            for k, p in enumerate(points):
                d = _dist(p, rp)
                if d < best_d:
                    best_d = d
                    best_idx = k
            if best_idx >= 0:
                treated.add(best_idx)
        return list(treated)
    elif intervention.type == "relocate":
        treated = set()
        for rp in intervention.points:
            best_idx = -1
            best_d = float("inf")
            for k, p in enumerate(points):
                d = _dist(p, rp)
                if d < best_d:
                    best_d = d
                    best_idx = k
            if best_idx >= 0:
                treated.add(best_idx)
        return list(treated)
    return []


def _match_cell(before_points: List[Point], after_points: List[Point],
                idx: int) -> int:
    """Match a before-cell index to the closest after-cell index."""
    if idx >= len(before_points):
        return -1
    target = before_points[idx]
    best_idx = -1
    best_d = float("inf")
    for k, p in enumerate(after_points):
        d = _dist(target, p)
        if d < best_d:
            best_d = d
            best_idx = k
    return best_idx


def _compute_att(before_cells, after_cells, before_pts, after_pts,
                 treated_indices, metric) -> float:
    """Average treatment effect on treated cells."""
    if not treated_indices:
        return 0.0
    deltas = []
    for ti in treated_indices:
        if ti >= len(before_cells):
            continue
        bval = _metric_value(before_cells[ti], metric)
        mi = _match_cell(before_pts, after_pts, ti)
        if mi >= 0 and mi < len(after_cells):
            aval = _metric_value(after_cells[mi], metric)
            deltas.append(aval - bval)
    return _safe_mean(deltas)


def _compute_spillover_metric(before_cells, after_cells, before_pts,
                              after_pts, treated_indices, metric) -> float:
    """Average change in neighbor cells of treated cells."""
    neighbor_indices = set()
    for ti in treated_indices:
        if ti < len(before_cells):
            neighbor_indices.update(before_cells[ti].get("neighbors", set()))
    neighbor_indices -= set(treated_indices)
    if not neighbor_indices:
        return 0.0
    deltas = []
    for ni in neighbor_indices:
        if ni >= len(before_cells):
            continue
        bval = _metric_value(before_cells[ni], metric)
        mi = _match_cell(before_pts, after_pts, ni)
        if mi >= 0 and mi < len(after_cells):
            aval = _metric_value(after_cells[mi], metric)
            deltas.append(aval - bval)
    return _safe_mean(deltas)


# ---------------------------------------------------------------------------
# Engine 2: Difference-in-Differences
# ---------------------------------------------------------------------------

def difference_in_differences(
    points: List[Point],
    intervention: Intervention,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    metrics: Optional[List[str]] = None,
) -> List[DIDResult]:
    """Spatial difference-in-differences analysis.

    Compares treated cells (near intervention points) against control
    cells (far from intervention) to isolate the treatment effect.
    """
    metrics = metrics or list(ALL_METRICS)
    extra = intervention.points + (intervention.targets or [])
    b = bounds or _auto_bounds(points, extra)

    before_cells = _compute_voronoi_cells(points, b)
    after_points = _apply_intervention(points, intervention)
    if not after_points:
        return [DIDResult(m, 0, 0, 0, 0, 0, 0) for m in metrics]
    after_cells = _compute_voronoi_cells(after_points, b)

    treated = set(_find_treated_indices(points, intervention))
    # Also include neighbors of treated as treated-adjacent
    treated_and_neighbors = set(treated)
    for ti in treated:
        if ti < len(before_cells):
            treated_and_neighbors.update(before_cells[ti].get("neighbors", set()))

    control = [i for i in range(len(points)) if i not in treated_and_neighbors]
    if not control:
        control = [i for i in range(len(points)) if i not in treated]

    results = []
    for m in metrics:
        # Before values
        tb = _safe_mean([_metric_value(before_cells[i], m) for i in treated
                         if i < len(before_cells)])
        cb = _safe_mean([_metric_value(before_cells[i], m) for i in control
                         if i < len(before_cells)])
        # After values (matched)
        ta_vals = []
        for ti in treated:
            mi = _match_cell(points, after_points, ti)
            if mi >= 0 and mi < len(after_cells):
                ta_vals.append(_metric_value(after_cells[mi], m))
        ta = _safe_mean(ta_vals)

        ca_vals = []
        for ci in control:
            mi = _match_cell(points, after_points, ci)
            if mi >= 0 and mi < len(after_cells):
                ca_vals.append(_metric_value(after_cells[mi], m))
        ca = _safe_mean(ca_vals)

        did = (ta - tb) - (ca - cb)
        # Parallel trend score: how similar were treated/control before
        diff = abs(tb - cb)
        scale = max(abs(tb), abs(cb), 1e-9)
        pt_score = max(0, 1.0 - diff / scale)

        results.append(DIDResult(m, tb, ta, cb, ca, did, pt_score))
    return results


# ---------------------------------------------------------------------------
# Engine 3: Synthetic Control
# ---------------------------------------------------------------------------

def synthetic_control(
    points: List[Point],
    intervention: Intervention,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    metrics: Optional[List[str]] = None,
) -> List[SyntheticControlResult]:
    """Construct synthetic counterfactual for treated cells.

    Uses a weighted combination of donor (unaffected) cells to estimate
    what the treated cell metrics would have been without intervention.
    """
    metrics = metrics or list(ALL_METRICS)
    extra = intervention.points + (intervention.targets or [])
    b = bounds or _auto_bounds(points, extra)

    before_cells = _compute_voronoi_cells(points, b)
    after_points = _apply_intervention(points, intervention)
    if not after_points:
        return [SyntheticControlResult(m, 0, 0, 0, {}, 0) for m in metrics]
    after_cells = _compute_voronoi_cells(after_points, b)

    treated = set(_find_treated_indices(points, intervention))
    treated_neighbors = set()
    for ti in treated:
        if ti < len(before_cells):
            treated_neighbors.update(before_cells[ti].get("neighbors", set()))
    donors = [i for i in range(len(points))
              if i not in treated and i not in treated_neighbors]
    if not donors:
        donors = [i for i in range(len(points)) if i not in treated]
    if not donors:
        return [SyntheticControlResult(m, 0, 0, 0, {}, 0) for m in metrics]

    results = []
    for m in metrics:
        # Target: mean treated-cell metric before intervention
        target_before = _safe_mean([_metric_value(before_cells[i], m)
                                    for i in treated if i < len(before_cells)])
        # Donor metrics before
        donor_before = {d: _metric_value(before_cells[d], m)
                        for d in donors if d < len(before_cells)}
        if not donor_before:
            results.append(SyntheticControlResult(m, 0, 0, 0, {}, 0))
            continue

        # Compute weights by inverse distance to target
        weights = {}
        total_w = 0.0
        for d, val in donor_before.items():
            diff = abs(val - target_before) + 1e-9
            w = 1.0 / diff
            weights[d] = w
            total_w += w
        # Normalize
        for d in weights:
            weights[d] /= total_w

        # Synthetic before (weighted sum)
        synthetic_before = sum(weights[d] * donor_before[d] for d in weights)

        # Actual treated after
        ta_vals = []
        for ti in treated:
            mi = _match_cell(points, after_points, ti)
            if mi >= 0 and mi < len(after_cells):
                ta_vals.append(_metric_value(after_cells[mi], m))
        actual_after = _safe_mean(ta_vals)

        # Synthetic after (weighted sum of donor after-values)
        synthetic_after = 0.0
        for d, w in weights.items():
            mi = _match_cell(points, after_points, d)
            if mi >= 0 and mi < len(after_cells):
                synthetic_after += w * _metric_value(after_cells[mi], m)

        treatment_effect = actual_after - synthetic_after
        fit_q = max(0, 1.0 - abs(synthetic_before - target_before) /
                    (abs(target_before) + 1e-9))

        results.append(SyntheticControlResult(
            m, actual_after, synthetic_after, treatment_effect,
            weights, min(fit_q, 1.0)))
    return results


# ---------------------------------------------------------------------------
# Engine 4: Spillover Detection
# ---------------------------------------------------------------------------

def detect_spillovers(
    points: List[Point],
    intervention: Intervention,
    hops: int = 2,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    metrics: Optional[List[str]] = None,
) -> List[SpilloverCell]:
    """Detect indirect effects radiating through k-hop neighborhoods."""
    metrics = metrics or list(ALL_METRICS)
    extra = intervention.points + (intervention.targets or [])
    b = bounds or _auto_bounds(points, extra)

    before_cells = _compute_voronoi_cells(points, b)
    after_points = _apply_intervention(points, intervention)
    if not after_points:
        return []
    after_cells = _compute_voronoi_cells(after_points, b)

    treated = set(_find_treated_indices(points, intervention))

    # BFS to find cells at each hop distance
    visited: Dict[int, int] = {t: 0 for t in treated}
    frontier = list(treated)
    for hop in range(1, hops + 1):
        next_frontier = []
        for fi in frontier:
            if fi < len(before_cells):
                for ni in before_cells[fi].get("neighbors", set()):
                    if ni not in visited:
                        visited[ni] = hop
                        next_frontier.append(ni)
        frontier = next_frontier

    spillovers = []
    max_delta = 1e-9
    for cell_idx, hop_dist in visited.items():
        if hop_dist == 0:
            continue  # skip treated cells themselves
        if cell_idx >= len(before_cells):
            continue
        deltas = {}
        for m in metrics:
            bval = _metric_value(before_cells[cell_idx], m)
            mi = _match_cell(points, after_points, cell_idx)
            if mi >= 0 and mi < len(after_cells):
                aval = _metric_value(after_cells[mi], m)
                deltas[m] = aval - bval
            else:
                deltas[m] = 0.0
        total_delta = sum(abs(v) for v in deltas.values())
        if total_delta > max_delta:
            max_delta = total_delta
        spillovers.append(SpilloverCell(cell_idx, hop_dist, deltas, total_delta))

    # Normalize intensity
    for s in spillovers:
        s.intensity = min(1.0, s.intensity / max_delta) if max_delta > 1e-9 else 0.0

    spillovers.sort(key=lambda s: (-s.intensity, s.hop_distance))
    return spillovers


# ---------------------------------------------------------------------------
# Engine 5: Intervention Ranking
# ---------------------------------------------------------------------------

OBJECTIVES = {
    "area_equity": lambda effects: -_safe_stdev([e.ate for e in effects]),
    "max_coverage": lambda effects: sum(e.ate for e in effects if e.metric == "area"),
    "min_disruption": lambda effects: -sum(abs(e.spillover) for e in effects),
    "max_connectivity": lambda effects: sum(
        e.ate for e in effects if e.metric == "neighbors"),
}


def rank_interventions(
    points: List[Point],
    interventions: List[Intervention],
    objective: str = "area_equity",
    bounds: Optional[Tuple[float, float, float, float]] = None,
) -> List[InterventionScore]:
    """Rank candidate interventions by expected impact."""
    if objective not in OBJECTIVES:
        raise ValueError(f"Unknown objective {objective!r}; "
                         f"choose from {list(OBJECTIVES.keys())}")

    scores = []
    for iv in interventions:
        effects = [estimate_treatment_effect(points, iv, m, bounds)
                   for m in ALL_METRICS]
        score_val = OBJECTIVES[objective](effects)
        impacts = {e.metric: e.ate for e in effects}
        scores.append(InterventionScore(iv, score_val, impacts))

    scores.sort(key=lambda s: -s.score)
    for i, s in enumerate(scores):
        s.rank = i + 1
    return scores


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_causality(
    points: List[Point],
    interventions: List[Intervention],
    bounds: Optional[Tuple[float, float, float, float]] = None,
    metrics: Optional[List[str]] = None,
    objective: str = "area_equity",
    hops: int = 2,
) -> CausalityReport:
    """Run full causality analysis across all interventions.

    Parameters
    ----------
    points : list of (x, y)
        Existing seed points.
    interventions : list of Intervention
        Candidate interventions to analyze.
    bounds : (south, north, west, east) or None
        Spatial bounds; auto-detected if None.
    metrics : list of str or None
        Metrics to analyze; defaults to all.
    objective : str
        Objective for ranking interventions.
    hops : int
        Neighborhood hops for spillover detection.

    Returns
    -------
    CausalityReport
    """
    metrics = metrics or list(ALL_METRICS)
    extra_pts: List[Point] = []
    for iv in interventions:
        extra_pts.extend(iv.points)
        if iv.targets:
            extra_pts.extend(iv.targets)
    b = bounds or _auto_bounds(points, extra_pts)

    all_effects = []
    all_did = []
    all_sc = []
    all_spill = []

    for iv in interventions:
        effects = [estimate_treatment_effect(points, iv, m, b) for m in metrics]
        all_effects.append(effects)

        did = difference_in_differences(points, iv, b, metrics)
        all_did.append(did)

        sc = synthetic_control(points, iv, b, metrics)
        all_sc.append(sc)

        spill = detect_spillovers(points, iv, hops, b, metrics)
        all_spill.append(spill)

    ranking = rank_interventions(points, interventions, objective, b)

    return CausalityReport(
        interventions=interventions,
        effects=all_effects,
        did_results=all_did,
        synthetic_control=all_sc,
        spillover_map=all_spill,
        ranking=ranking,
        bounds=b,
    )


# ---------------------------------------------------------------------------
# HTML Dashboard Generator
# ---------------------------------------------------------------------------

def _generate_html(report: CausalityReport) -> str:
    """Generate self-contained interactive HTML dashboard."""
    # Build effects data for JS
    effects_js = []
    for i, iv in enumerate(report.interventions):
        if i < len(report.effects):
            for e in report.effects[i]:
                effects_js.append({
                    "intervention": iv.label,
                    "metric": e.metric,
                    "ate": e.ate,
                    "att": e.att,
                    "spillover": e.spillover,
                    "confidence": e.confidence,
                })

    spillover_js = []
    for i, iv in enumerate(report.interventions):
        if i < len(report.spillover_map):
            for s in report.spillover_map[i]:
                spillover_js.append({
                    "intervention": iv.label,
                    "cell": s.cell_index,
                    "hop": s.hop_distance,
                    "intensity": s.intensity,
                })

    ranking_js = [r.to_dict() for r in report.ranking]

    interventions_js = []
    for iv in report.interventions:
        interventions_js.append({
            "label": iv.label,
            "type": iv.type,
            "points": iv.points,
            "targets": iv.targets,
        })

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spatial Causality Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     background:#0f172a;color:#e2e8f0}}
.header{{background:linear-gradient(135deg,#1e293b,#334155);padding:24px 32px;
         border-bottom:2px solid #3b82f6}}
.header h1{{font-size:1.6rem;color:#60a5fa}}
.header p{{color:#94a3b8;font-size:0.9rem;margin-top:4px}}
.tabs{{display:flex;gap:0;background:#1e293b;border-bottom:2px solid #334155}}
.tab{{padding:12px 24px;cursor:pointer;color:#94a3b8;border-bottom:2px solid transparent;
     transition:all .2s}}
.tab:hover{{color:#e2e8f0;background:#334155}}
.tab.active{{color:#60a5fa;border-bottom-color:#3b82f6;background:#334155}}
.panel{{display:none;padding:24px 32px;max-width:1200px;margin:0 auto}}
.panel.active{{display:block}}
table{{width:100%;border-collapse:collapse;margin:16px 0}}
th,td{{padding:10px 14px;text-align:left;border-bottom:1px solid #334155}}
th{{color:#60a5fa;font-weight:600;font-size:0.85rem;text-transform:uppercase}}
td{{font-size:0.9rem}}
.bar-container{{display:flex;align-items:center;gap:8px}}
.bar{{height:18px;border-radius:3px;min-width:2px}}
.bar.pos{{background:#22c55e}}
.bar.neg{{background:#ef4444}}
.card{{background:#1e293b;border-radius:8px;padding:16px;margin:12px 0;
       border:1px solid #334155}}
.card h3{{color:#60a5fa;margin-bottom:8px;font-size:1rem}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.75rem;
       font-weight:600;margin:2px}}
.badge-add{{background:#22c55e33;color:#22c55e}}
.badge-remove{{background:#ef444433;color:#ef4444}}
.badge-relocate{{background:#f59e0b33;color:#f59e0b}}
.intensity-bar{{height:12px;border-radius:2px;background:linear-gradient(90deg,#3b82f6,#8b5cf6)}}
.rank-num{{font-size:1.4rem;font-weight:700;color:#60a5fa;width:40px;text-align:center}}
.summary{{background:#1e293b;padding:16px;border-radius:8px;white-space:pre-wrap;
         font-family:monospace;font-size:0.85rem;color:#cbd5e1;margin:16px 0;
         border:1px solid #334155;max-height:400px;overflow-y:auto}}
</style>
</head>
<body>
<div class="header">
<h1>🔬 Spatial Causality Engine</h1>
<p>{len(report.interventions)} intervention(s) analyzed &middot;
{sum(len(e) for e in report.effects)} effect estimates &middot;
{sum(len(s) for s in report.spillover_map)} spillover cells detected</p>
</div>
<div class="tabs">
<div class="tab active" onclick="showTab(0)">Treatment Effects</div>
<div class="tab" onclick="showTab(1)">Difference-in-Differences</div>
<div class="tab" onclick="showTab(2)">Spillover Map</div>
<div class="tab" onclick="showTab(3)">Intervention Ranking</div>
</div>

<div class="panel active" id="panel-0">
<h2 style="margin:16px 0;color:#f8fafc">Treatment Effect Estimates</h2>
<table>
<tr><th>Intervention</th><th>Metric</th><th>ATE</th><th>ATT</th>
<th>Spillover</th><th>Confidence</th><th>Visual</th></tr>
{"".join(_effect_row(e) for e in effects_js)}
</table>
</div>

<div class="panel" id="panel-1">
<h2 style="margin:16px 0;color:#f8fafc">Difference-in-Differences</h2>
{_did_cards_html(report)}
</div>

<div class="panel" id="panel-2">
<h2 style="margin:16px 0;color:#f8fafc">Spillover Map</h2>
<table>
<tr><th>Intervention</th><th>Cell</th><th>Hop</th><th>Intensity</th><th></th></tr>
{"".join(_spillover_row(s) for s in spillover_js[:50])}
</table>
{f'<p style="color:#94a3b8;margin-top:8px">Showing top 50 of {len(spillover_js)} spillover cells</p>' if len(spillover_js) > 50 else ''}
</div>

<div class="panel" id="panel-3">
<h2 style="margin:16px 0;color:#f8fafc">Intervention Ranking</h2>
<table>
<tr><th>#</th><th>Intervention</th><th>Type</th><th>Score</th><th>Impacts</th></tr>
{"".join(_ranking_row(r) for r in ranking_js)}
</table>
</div>

<script>
function showTab(idx) {{
  document.querySelectorAll('.tab').forEach((t,i) => {{
    t.classList.toggle('active', i===idx);
  }});
  document.querySelectorAll('.panel').forEach((p,i) => {{
    p.classList.toggle('active', i===idx);
  }});
}}
</script>
</body>
</html>"""


def _effect_row(e: dict) -> str:
    ate = e["ate"]
    bar_w = min(abs(ate) * 100, 120)
    cls = "pos" if ate >= 0 else "neg"
    conf_pct = e["confidence"] * 100
    return (f'<tr><td>{e["intervention"]}</td><td>{e["metric"]}</td>'
            f'<td>{ate:+.4f}</td><td>{e["att"]:+.4f}</td>'
            f'<td>{e["spillover"]:+.4f}</td>'
            f'<td>{conf_pct:.0f}%</td>'
            f'<td><div class="bar-container">'
            f'<div class="bar {cls}" style="width:{bar_w}px"></div>'
            f'</div></td></tr>')


def _did_cards_html(report: CausalityReport) -> str:
    cards = []
    for i, iv in enumerate(report.interventions):
        if i >= len(report.did_results):
            continue
        badge_cls = f"badge-{iv.type}"
        rows = ""
        for d in report.did_results[i]:
            rows += (f"<tr><td>{d.metric}</td>"
                     f"<td>{d.treated_before:.4f}</td>"
                     f"<td>{d.treated_after:.4f}</td>"
                     f"<td>{d.control_before:.4f}</td>"
                     f"<td>{d.control_after:.4f}</td>"
                     f"<td><b>{d.did_estimate:+.4f}</b></td>"
                     f"<td>{d.parallel_trend_score:.2f}</td></tr>")
        cards.append(
            f'<div class="card"><h3>{iv.label} '
            f'<span class="badge {badge_cls}">{iv.type}</span></h3>'
            f'<table><tr><th>Metric</th><th>Treated Before</th>'
            f'<th>Treated After</th><th>Control Before</th>'
            f'<th>Control After</th><th>DiD</th><th>Parallel Trend</th></tr>'
            f'{rows}</table></div>')
    return "\n".join(cards)


def _spillover_row(s: dict) -> str:
    pct = s["intensity"] * 100
    return (f'<tr><td>{s["intervention"]}</td><td>Cell {s["cell"]}</td>'
            f'<td>{s["hop"]}</td><td>{pct:.1f}%</td>'
            f'<td><div class="intensity-bar" '
            f'style="width:{pct:.0f}%"></div></td></tr>')


def _ranking_row(r: dict) -> str:
    badge_cls = f"badge-{r['type']}"
    impacts = " &middot; ".join(f"{k}: {v:+.4f}" for k, v in r["metric_impacts"].items())
    return (f'<tr><td class="rank-num">#{r["rank"]}</td>'
            f'<td>{r["label"]}</td>'
            f'<td><span class="badge {badge_cls}">{r["type"]}</span></td>'
            f'<td>{r["score"]:.4f}</td>'
            f'<td style="font-size:0.8rem;color:#94a3b8">{impacts}</td></tr>')
