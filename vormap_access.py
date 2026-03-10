"""Spatial Accessibility Analyzer for Voronoi diagrams.

Computes accessibility indices for demand points relative to service
(supply) locations using established spatial accessibility methods from
health geography and urban planning.

Methods:

- **Proximity Score**: Distance-weighted access based on nearest-k
  facilities.  Closer services contribute more (inverse distance or
  Gaussian decay).

- **Gravity Model**: Attraction-based access where each demand point's
  score reflects the sum of supply capacities divided by distance
  friction.  Models the real-world fact that people use multiple
  facilities, not just the nearest one.

- **Two-Step Floating Catchment Area (2SFCA)**: The standard method in
  health geography.  Step 1: compute supply-to-demand ratio for each
  facility's catchment.  Step 2: sum ratios for each demand point's
  reachable facilities.

- **Enhanced 2SFCA (E2SFCA)**: Adds distance decay weights to the
  basic 2SFCA using a step-wise or continuous function, reducing the
  "all-or-nothing" catchment boundary artifact.

- **Access Inequality**: Gini coefficient and Lorenz curve data for
  the access score distribution.  High Gini = unequal access across
  the study area.

Usage (Python API)::

    from vormap_access import (
        proximity_scores, gravity_scores, two_step_fca,
        enhanced_two_step_fca, access_inequality, accessibility_report,
    )

    demand = [(100, 200), (300, 400), (500, 600)]
    supply = [(150, 250, 10), (450, 550, 20)]  # (x, y, capacity)

    prox = proximity_scores(demand, supply)
    grav = gravity_scores(demand, supply)
    fca = two_step_fca(demand, supply, catchment=300)
    efca = enhanced_two_step_fca(demand, supply, catchment=300)
    ineq = access_inequality([s.score for s in prox])

    report = accessibility_report(demand, supply, catchment=300)
    print(report.summary)

CLI::

    python vormap_access.py demand.txt supply.csv --method gravity
    python vormap_access.py demand.txt supply.csv --method 2sfca --catchment 300
    python vormap_access.py demand.txt supply.csv --method e2sfca --catchment 300
    python vormap_access.py demand.txt supply.csv --report --json access.json
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import vormap


# ── Data Structures ──────────────────────────────────────────────────


@dataclass
class AccessScore:
    """Accessibility score for a single demand point."""
    x: float
    y: float
    score: float
    rank: int = 0
    level: str = ""
    nearest_supply_dist: float = 0.0
    reachable_supply_count: int = 0


@dataclass
class SupplyPoint:
    """A service/supply location with capacity."""
    x: float
    y: float
    capacity: float = 1.0


@dataclass
class InequalityResult:
    """Access inequality metrics."""
    gini: float
    mean_score: float
    median_score: float
    std_score: float
    min_score: float
    max_score: float
    cv: float  # coefficient of variation
    lorenz_x: List[float] = field(default_factory=list)
    lorenz_y: List[float] = field(default_factory=list)
    quintile_shares: List[float] = field(default_factory=list)


@dataclass
class AccessReport:
    """Full accessibility analysis report."""
    method: str
    demand_count: int
    supply_count: int
    catchment: float
    scores: List[AccessScore]
    inequality: InequalityResult
    supply_utilization: List[dict]
    summary: str
    underserved: List[AccessScore]
    hotspots: List[AccessScore]


# ── Distance & Decay ─────────────────────────────────────────────────


def _euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
    """Euclidean distance between two points."""
    dx = x2 - x1
    dy = y2 - y1
    return math.sqrt(dx * dx + dy * dy)


def _gaussian_decay(distance: float, bandwidth: float) -> float:
    """Gaussian distance decay weight.  Returns value in (0, 1]."""
    if bandwidth <= 0:
        return 0.0
    ratio = distance / bandwidth
    return math.exp(-0.5 * ratio * ratio)


def _inverse_power_decay(distance: float, beta: float = 1.0,
                         min_dist: float = 1.0) -> float:
    """Inverse power distance decay: 1 / d^beta.

    Uses *min_dist* floor to avoid division by zero.
    """
    d = max(distance, min_dist)
    return 1.0 / (d ** beta)


def _step_decay(distance: float, catchment: float,
                zones: int = 3) -> float:
    """Step-wise decay for E2SFCA.  Divides catchment into equal zones
    with decreasing weights: 1.0, 0.68, 0.22 (following Luo & Qi 2009).
    """
    if distance > catchment:
        return 0.0
    zone_width = catchment / zones
    zone_idx = min(int(distance / zone_width), zones - 1)
    weights = [1.0, 0.68, 0.22]
    if zones > 3:
        # Extend with exponentially decreasing weights
        weights = [math.exp(-0.5 * (i / (zones / 3)) ** 2) for i in range(zones)]
    return weights[min(zone_idx, len(weights) - 1)]


# ── Input Parsing ────────────────────────────────────────────────────


def _parse_supply(supply_input) -> List[SupplyPoint]:
    """Normalize supply input to list of SupplyPoint.

    Accepts:
      - List of (x, y) tuples — capacity defaults to 1.0
      - List of (x, y, capacity) tuples
      - List of SupplyPoint objects
    """
    result = []
    for item in supply_input:
        if isinstance(item, SupplyPoint):
            result.append(item)
        elif hasattr(item, '__len__'):
            if len(item) >= 3:
                result.append(SupplyPoint(float(item[0]), float(item[1]),
                                          float(item[2])))
            elif len(item) >= 2:
                result.append(SupplyPoint(float(item[0]), float(item[1]), 1.0))
            else:
                raise ValueError("Supply point must have at least x, y: %s" % str(item))
        else:
            raise ValueError("Invalid supply point format: %s" % str(item))
    return result


def _parse_demand(demand_input) -> List[Tuple[float, float]]:
    """Normalize demand input to list of (x, y) tuples."""
    result = []
    for item in demand_input:
        if hasattr(item, '__len__') and len(item) >= 2:
            result.append((float(item[0]), float(item[1])))
        else:
            raise ValueError("Demand point must have at least x, y: %s" % str(item))
    return result


# ── Proximity Scoring ────────────────────────────────────────────────


def proximity_scores(demand, supply, k: int = 3,
                     decay: str = "gaussian",
                     bandwidth: float = 0.0,
                     beta: float = 1.0) -> List[AccessScore]:
    """Compute proximity-based accessibility scores.

    For each demand point, finds the nearest *k* supply points and
    computes a weighted score based on distance decay.

    Parameters
    ----------
    demand : list of (x, y) tuples
        Locations where access is measured.
    supply : list of (x, y) or (x, y, capacity) tuples
        Service/supply locations.
    k : int
        Number of nearest supply points to consider (default 3).
    decay : str
        Distance decay function: ``"gaussian"`` (default) or ``"inverse"``.
    bandwidth : float
        Bandwidth for Gaussian decay.  If 0, auto-computed as
        median nearest-supply distance × 2.
    beta : float
        Exponent for inverse power decay (default 1.0).

    Returns
    -------
    list of AccessScore
        One score per demand point, ranked from best to worst.
    """
    demand_pts = _parse_demand(demand)
    supply_pts = _parse_supply(supply)

    if not demand_pts:
        raise ValueError("No demand points provided")
    if not supply_pts:
        raise ValueError("No supply points provided")

    k = min(k, len(supply_pts))

    # Auto-compute bandwidth if needed
    if decay == "gaussian" and bandwidth <= 0:
        dists = []
        for dx, dy in demand_pts:
            min_d = min(_euclidean(dx, dy, s.x, s.y) for s in supply_pts)
            dists.append(min_d)
        dists.sort()
        bandwidth = dists[len(dists) // 2] * 2 if dists else 1.0
        bandwidth = max(bandwidth, 1.0)

    results = []
    for dx, dy in demand_pts:
        # Find distances to all supply points
        sd = [(i, _euclidean(dx, dy, s.x, s.y)) for i, s in enumerate(supply_pts)]
        sd.sort(key=lambda x: x[1])

        score = 0.0
        nearest_dist = sd[0][1] if sd else 0.0
        for j in range(k):
            si, dist = sd[j]
            cap = supply_pts[si].capacity
            if decay == "gaussian":
                w = _gaussian_decay(dist, bandwidth)
            else:
                w = _inverse_power_decay(dist, beta)
            score += w * cap

        results.append(AccessScore(
            x=dx, y=dy, score=score,
            nearest_supply_dist=nearest_dist,
            reachable_supply_count=k,
        ))

    _rank_and_classify(results)
    return results


# ── Gravity Model ────────────────────────────────────────────────────


def gravity_scores(demand, supply, beta: float = 1.0,
                   min_dist: float = 1.0) -> List[AccessScore]:
    """Compute gravity-model accessibility scores.

    Score_i = sum_j( capacity_j / distance(i,j)^beta )

    Models accessibility as the total "pull" of all supply points,
    weighted by their capacity and inverse distance.

    Parameters
    ----------
    demand : list of (x, y)
        Demand locations.
    supply : list of (x, y) or (x, y, capacity)
        Supply locations with optional capacity.
    beta : float
        Distance friction exponent (default 1.0).
    min_dist : float
        Minimum distance floor to prevent division by zero.

    Returns
    -------
    list of AccessScore
    """
    demand_pts = _parse_demand(demand)
    supply_pts = _parse_supply(supply)

    if not demand_pts:
        raise ValueError("No demand points provided")
    if not supply_pts:
        raise ValueError("No supply points provided")

    results = []
    for dx, dy in demand_pts:
        score = 0.0
        nearest_dist = float('inf')
        reachable = 0
        for s in supply_pts:
            dist = _euclidean(dx, dy, s.x, s.y)
            if dist < nearest_dist:
                nearest_dist = dist
            w = _inverse_power_decay(dist, beta, min_dist)
            score += w * s.capacity
            reachable += 1

        results.append(AccessScore(
            x=dx, y=dy, score=score,
            nearest_supply_dist=nearest_dist,
            reachable_supply_count=reachable,
        ))

    _rank_and_classify(results)
    return results


# ── Two-Step Floating Catchment Area (2SFCA) ─────────────────────────


def two_step_fca(demand, supply, catchment: float = 300.0,
                 demand_weights: Optional[List[float]] = None) -> List[AccessScore]:
    """Two-Step Floating Catchment Area method (Luo & Wang 2003).

    Step 1: For each supply point j, compute supply-to-demand ratio R_j:
        R_j = capacity_j / sum(demand_weight_k for all k within catchment of j)

    Step 2: For each demand point i, sum the ratios of all reachable supply:
        A_i = sum(R_j for all j within catchment of i)

    Parameters
    ----------
    demand : list of (x, y)
        Demand locations.
    supply : list of (x, y) or (x, y, capacity)
        Supply locations.
    catchment : float
        Maximum distance for a facility to be considered reachable.
    demand_weights : list of float, optional
        Population/demand weight for each demand point (default: 1.0 each).

    Returns
    -------
    list of AccessScore
    """
    demand_pts = _parse_demand(demand)
    supply_pts = _parse_supply(supply)

    if not demand_pts:
        raise ValueError("No demand points provided")
    if not supply_pts:
        raise ValueError("No supply points provided")
    if catchment <= 0:
        raise ValueError("Catchment must be positive, got %s" % catchment)

    n_demand = len(demand_pts)
    if demand_weights is None:
        dw = [1.0] * n_demand
    else:
        if len(demand_weights) != n_demand:
            raise ValueError("demand_weights length (%d) != demand count (%d)"
                             % (len(demand_weights), n_demand))
        dw = list(demand_weights)

    # Precompute distance matrix (demand x supply)
    dist_matrix = []
    for dx, dy in demand_pts:
        row = [_euclidean(dx, dy, s.x, s.y) for s in supply_pts]
        dist_matrix.append(row)

    # Step 1: compute supply-to-demand ratios
    ratios = []
    for j, s in enumerate(supply_pts):
        total_demand = 0.0
        for i in range(n_demand):
            if dist_matrix[i][j] <= catchment:
                total_demand += dw[i]
        ratio = s.capacity / total_demand if total_demand > 0 else 0.0
        ratios.append(ratio)

    # Step 2: sum ratios for each demand point
    results = []
    for i, (dx, dy) in enumerate(demand_pts):
        score = 0.0
        nearest_dist = float('inf')
        reachable = 0
        for j in range(len(supply_pts)):
            d = dist_matrix[i][j]
            if d < nearest_dist:
                nearest_dist = d
            if d <= catchment:
                score += ratios[j]
                reachable += 1

        results.append(AccessScore(
            x=dx, y=dy, score=score,
            nearest_supply_dist=nearest_dist,
            reachable_supply_count=reachable,
        ))

    _rank_and_classify(results)
    return results


# ── Enhanced 2SFCA (E2SFCA) ──────────────────────────────────────────


def enhanced_two_step_fca(demand, supply, catchment: float = 300.0,
                          demand_weights: Optional[List[float]] = None,
                          decay: str = "step",
                          zones: int = 3,
                          bandwidth: float = 0.0) -> List[AccessScore]:
    """Enhanced Two-Step Floating Catchment Area (Luo & Qi 2009).

    Like 2SFCA but applies distance decay weights within the catchment,
    eliminating the "all-or-nothing" boundary artifact.

    Parameters
    ----------
    demand, supply, catchment, demand_weights
        Same as ``two_step_fca``.
    decay : str
        Decay function: ``"step"`` (default, zone-based) or ``"gaussian"``.
    zones : int
        Number of zones for step decay (default 3).
    bandwidth : float
        Bandwidth for Gaussian decay.  If 0, uses catchment / 2.

    Returns
    -------
    list of AccessScore
    """
    demand_pts = _parse_demand(demand)
    supply_pts = _parse_supply(supply)

    if not demand_pts:
        raise ValueError("No demand points provided")
    if not supply_pts:
        raise ValueError("No supply points provided")
    if catchment <= 0:
        raise ValueError("Catchment must be positive, got %s" % catchment)

    n_demand = len(demand_pts)
    if demand_weights is None:
        dw = [1.0] * n_demand
    else:
        if len(demand_weights) != n_demand:
            raise ValueError("demand_weights length (%d) != demand count (%d)"
                             % (len(demand_weights), n_demand))
        dw = list(demand_weights)

    if decay == "gaussian" and bandwidth <= 0:
        bandwidth = catchment / 2

    def _weight(dist):
        if dist > catchment:
            return 0.0
        if decay == "step":
            return _step_decay(dist, catchment, zones)
        else:
            return _gaussian_decay(dist, bandwidth)

    # Precompute distance matrix
    dist_matrix = []
    for dx, dy in demand_pts:
        row = [_euclidean(dx, dy, s.x, s.y) for s in supply_pts]
        dist_matrix.append(row)

    # Step 1: weighted supply-to-demand ratios
    ratios = []
    for j, s in enumerate(supply_pts):
        weighted_demand = 0.0
        for i in range(n_demand):
            w = _weight(dist_matrix[i][j])
            if w > 0:
                weighted_demand += dw[i] * w
        ratio = s.capacity / weighted_demand if weighted_demand > 0 else 0.0
        ratios.append(ratio)

    # Step 2: weighted sum of ratios
    results = []
    for i, (dx, dy) in enumerate(demand_pts):
        score = 0.0
        nearest_dist = float('inf')
        reachable = 0
        for j in range(len(supply_pts)):
            d = dist_matrix[i][j]
            if d < nearest_dist:
                nearest_dist = d
            w = _weight(d)
            if w > 0:
                score += ratios[j] * w
                reachable += 1

        results.append(AccessScore(
            x=dx, y=dy, score=score,
            nearest_supply_dist=nearest_dist,
            reachable_supply_count=reachable,
        ))

    _rank_and_classify(results)
    return results


# ── Access Inequality ────────────────────────────────────────────────


def access_inequality(scores: List[float]) -> InequalityResult:
    """Compute access inequality metrics from a list of scores.

    Calculates the Gini coefficient, Lorenz curve, quintile shares,
    and basic statistics.  A Gini of 0 = perfect equality, 1 = maximum
    inequality.

    Parameters
    ----------
    scores : list of float
        Accessibility scores (one per demand point).

    Returns
    -------
    InequalityResult
    """
    if not scores:
        raise ValueError("No scores provided")

    n = len(scores)
    sorted_s = sorted(scores)

    mean_val = sum(sorted_s) / n
    median_val = sorted_s[n // 2] if n % 2 == 1 else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2

    # Standard deviation
    var_sum = sum((x - mean_val) ** 2 for x in sorted_s)
    std_val = math.sqrt(var_sum / n) if n > 0 else 0.0
    cv = std_val / mean_val if mean_val > 0 else 0.0

    # Gini coefficient (rank-weighted formula)
    total_sum = sum(sorted_s)
    if total_sum == 0:
        gini = 0.0
    else:
        rank_sum = sum((i + 1) * v for i, v in enumerate(sorted_s))
        gini = (2 * rank_sum) / (n * total_sum) - (n + 1) / n

    # Clamp to [0, 1]
    gini = max(0.0, min(1.0, gini))

    # Lorenz curve
    lorenz_x = [0.0]
    lorenz_y = [0.0]
    cumulative = 0.0
    for i, val in enumerate(sorted_s):
        cumulative += val
        lorenz_x.append((i + 1) / n)
        lorenz_y.append(cumulative / total_sum if total_sum > 0 else 0.0)

    # Quintile shares (5 groups)
    quintile_shares = []
    q_size = max(1, n // 5)
    for q in range(5):
        start = q * q_size
        end = (q + 1) * q_size if q < 4 else n
        q_sum = sum(sorted_s[start:end])
        quintile_shares.append(q_sum / total_sum if total_sum > 0 else 0.0)

    return InequalityResult(
        gini=round(gini, 4),
        mean_score=round(mean_val, 4),
        median_score=round(median_val, 4),
        std_score=round(std_val, 4),
        min_score=round(sorted_s[0], 4),
        max_score=round(sorted_s[-1], 4),
        cv=round(cv, 4),
        lorenz_x=[round(x, 4) for x in lorenz_x],
        lorenz_y=[round(y, 4) for y in lorenz_y],
        quintile_shares=[round(q, 4) for q in quintile_shares],
    )


# ── Ranking & Classification ─────────────────────────────────────────


def _rank_and_classify(scores: List[AccessScore]) -> None:
    """Assign ranks (1 = best) and access-level labels in-place."""
    scores.sort(key=lambda s: s.score, reverse=True)
    n = len(scores)
    for i, s in enumerate(scores):
        s.rank = i + 1
        pct = (i + 1) / n if n > 0 else 0
        if pct <= 0.2:
            s.level = "excellent"
        elif pct <= 0.4:
            s.level = "good"
        elif pct <= 0.6:
            s.level = "moderate"
        elif pct <= 0.8:
            s.level = "poor"
        else:
            s.level = "critical"


# ── Supply Utilization ───────────────────────────────────────────────


def _supply_utilization(demand_pts: List[Tuple[float, float]],
                        supply_pts: List[SupplyPoint],
                        catchment: float) -> List[dict]:
    """Compute supply point utilization stats.

    For each supply point, count how many demand points are within
    catchment and compute a demand-to-capacity ratio.
    """
    util = []
    for j, s in enumerate(supply_pts):
        in_catchment = 0
        for dx, dy in demand_pts:
            if _euclidean(dx, dy, s.x, s.y) <= catchment:
                in_catchment += 1
        util.append({
            "supply_index": j,
            "x": s.x,
            "y": s.y,
            "capacity": s.capacity,
            "demand_in_catchment": in_catchment,
            "demand_capacity_ratio": round(in_catchment / s.capacity, 4) if s.capacity > 0 else 0,
            "utilization_level": (
                "overloaded" if in_catchment / max(s.capacity, 1e-9) > 2.0
                else "high" if in_catchment / max(s.capacity, 1e-9) > 1.0
                else "moderate" if in_catchment / max(s.capacity, 1e-9) > 0.5
                else "low"
            ),
        })
    return util


# ── Full Report ──────────────────────────────────────────────────────


def accessibility_report(demand, supply, catchment: float = 300.0,
                         method: str = "e2sfca",
                         demand_weights: Optional[List[float]] = None,
                         underserved_pct: float = 0.2,
                         **kwargs) -> AccessReport:
    """Run a full accessibility analysis and produce a report.

    Parameters
    ----------
    demand : list of (x, y)
        Demand locations.
    supply : list of (x, y) or (x, y, capacity)
        Supply locations.
    catchment : float
        Catchment distance for FCA methods (ignored by proximity/gravity).
    method : str
        One of ``"proximity"``, ``"gravity"``, ``"2sfca"``, ``"e2sfca"``.
    demand_weights : list of float, optional
        Population weights for demand points.
    underserved_pct : float
        Bottom fraction of scores flagged as underserved (default 0.2).
    **kwargs
        Passed to the chosen scoring method.

    Returns
    -------
    AccessReport
    """
    demand_pts = _parse_demand(demand)
    supply_pts = _parse_supply(supply)

    if method == "proximity":
        scores = proximity_scores(demand_pts, supply_pts, **kwargs)
    elif method == "gravity":
        scores = gravity_scores(demand_pts, supply_pts, **kwargs)
    elif method == "2sfca":
        scores = two_step_fca(demand_pts, supply_pts, catchment, demand_weights)
    elif method == "e2sfca":
        scores = enhanced_two_step_fca(demand_pts, supply_pts, catchment,
                                       demand_weights, **kwargs)
    else:
        raise ValueError("Unknown method: %s (use proximity/gravity/2sfca/e2sfca)" % method)

    score_vals = [s.score for s in scores]
    inequality = access_inequality(score_vals)
    utilization = _supply_utilization(demand_pts, supply_pts, catchment)

    n = len(scores)
    underserved_cutoff = max(1, int(n * underserved_pct))
    underserved = scores[-underserved_cutoff:]
    hotspot_cutoff = max(1, int(n * underserved_pct))
    hotspots = scores[:hotspot_cutoff]

    # Generate summary text
    summary_lines = [
        "Spatial Accessibility Report (%s)" % method.upper(),
        "=" * 50,
        "Demand points: %d" % len(demand_pts),
        "Supply points: %d" % len(supply_pts),
        "Catchment: %.1f" % catchment,
        "",
        "Score distribution:",
        "  Mean:   %.4f" % inequality.mean_score,
        "  Median: %.4f" % inequality.median_score,
        "  Std:    %.4f" % inequality.std_score,
        "  Min:    %.4f" % inequality.min_score,
        "  Max:    %.4f" % inequality.max_score,
        "",
        "Inequality:",
        "  Gini coefficient: %.4f" % inequality.gini,
        "  CV:               %.4f" % inequality.cv,
        "",
        "Access levels:",
    ]

    level_counts = {}
    for s in scores:
        level_counts[s.level] = level_counts.get(s.level, 0) + 1
    for level in ["excellent", "good", "moderate", "poor", "critical"]:
        count = level_counts.get(level, 0)
        pct = count / n * 100 if n > 0 else 0
        summary_lines.append("  %-10s %3d (%5.1f%%)" % (level, count, pct))

    over_count = sum(1 for u in utilization if u["utilization_level"] == "overloaded")
    if over_count > 0:
        summary_lines.append("")
        summary_lines.append("WARNING: %d supply point(s) overloaded" % over_count)

    summary = "\n".join(summary_lines)

    return AccessReport(
        method=method,
        demand_count=len(demand_pts),
        supply_count=len(supply_pts),
        catchment=catchment,
        scores=scores,
        inequality=inequality,
        supply_utilization=utilization,
        summary=summary,
        underserved=underserved,
        hotspots=hotspots,
    )


# ── Export ───────────────────────────────────────────────────────────


def export_json(report: AccessReport, filepath: str) -> None:
    """Export accessibility report to JSON."""
    safe_path = vormap.validate_output_path(filepath, allow_absolute=True)
    data = {
        "method": report.method,
        "demand_count": report.demand_count,
        "supply_count": report.supply_count,
        "catchment": report.catchment,
        "inequality": {
            "gini": report.inequality.gini,
            "mean": report.inequality.mean_score,
            "median": report.inequality.median_score,
            "std": report.inequality.std_score,
            "min": report.inequality.min_score,
            "max": report.inequality.max_score,
            "cv": report.inequality.cv,
            "quintile_shares": report.inequality.quintile_shares,
        },
        "scores": [
            {
                "x": s.x, "y": s.y,
                "score": round(s.score, 6),
                "rank": s.rank,
                "level": s.level,
                "nearest_supply_dist": round(s.nearest_supply_dist, 2),
                "reachable_supply_count": s.reachable_supply_count,
            }
            for s in report.scores
        ],
        "supply_utilization": report.supply_utilization,
        "underserved_count": len(report.underserved),
        "hotspot_count": len(report.hotspots),
    }
    with open(safe_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def export_csv(report: AccessReport, filepath: str) -> None:
    """Export accessibility scores to CSV."""
    safe_path = vormap.validate_output_path(filepath, allow_absolute=True)
    with open(safe_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["x", "y", "score", "rank", "level",
                         "nearest_supply_dist", "reachable_supply_count"])
        for s in report.scores:
            writer.writerow([
                round(s.x, 4), round(s.y, 4),
                round(s.score, 6), s.rank, s.level,
                round(s.nearest_supply_dist, 2), s.reachable_supply_count,
            ])


# ── CLI ──────────────────────────────────────────────────────────────


def _load_supply_csv(filepath: str) -> List[SupplyPoint]:
    """Load supply points from CSV (x,y,capacity or x,y)."""
    safe = vormap.validate_input_path(filepath, allow_absolute=True)
    points = []
    with open(safe, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) >= 3:
                points.append(SupplyPoint(float(row[0]), float(row[1]),
                                          float(row[2])))
            elif len(row) >= 2:
                points.append(SupplyPoint(float(row[0]), float(row[1]), 1.0))
    return points


def main(argv=None):
    """CLI entry point for spatial accessibility analysis."""
    parser = argparse.ArgumentParser(
        description="Spatial Accessibility Analyzer for point data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("demand_file",
                        help="Demand points file (txt/csv/json)")
    parser.add_argument("supply_file",
                        help="Supply points file (csv with x,y,capacity)")
    parser.add_argument("--method", default="e2sfca",
                        choices=["proximity", "gravity", "2sfca", "e2sfca"],
                        help="Accessibility method (default: e2sfca)")
    parser.add_argument("--catchment", type=float, default=300.0,
                        help="Catchment distance (default: 300)")
    parser.add_argument("--json", metavar="FILE",
                        help="Export results to JSON")
    parser.add_argument("--csv", metavar="FILE",
                        help="Export results to CSV")
    parser.add_argument("--report", action="store_true",
                        help="Print summary report to stdout")
    parser.add_argument("--beta", type=float, default=1.0,
                        help="Distance friction exponent for gravity model")
    parser.add_argument("--k", type=int, default=3,
                        help="Nearest-k for proximity scoring")

    args = parser.parse_args(argv)

    # Load demand points
    demand_data = vormap.load_data(args.demand_file)
    demand_pts = [(d[0], d[1]) for d in demand_data]

    # Load supply points
    supply_pts = _load_supply_csv(args.supply_file)

    kwargs = {}
    if args.method == "proximity":
        kwargs["k"] = args.k
    elif args.method == "gravity":
        kwargs["beta"] = args.beta

    report = accessibility_report(
        demand_pts, supply_pts,
        catchment=args.catchment,
        method=args.method,
        **kwargs,
    )

    if args.report:
        print(report.summary)

    if args.json:
        export_json(report, args.json)
        print("Exported JSON to %s" % args.json)

    if args.csv:
        export_csv(report, args.csv)
        print("Exported CSV to %s" % args.csv)

    if not args.report and not args.json and not args.csv:
        print(report.summary)


if __name__ == "__main__":
    main()
