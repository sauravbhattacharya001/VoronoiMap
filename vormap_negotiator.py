#!/usr/bin/env python3
"""Spatial Conflict Negotiator — autonomous multi-party conflict resolution.

Detects spatial conflicts between Voronoi regions and resolves them
through iterative multi-round negotiation simulation.  Each point is
treated as an autonomous agent with preferences (desired area, buffer
distance, centrality) and the negotiator finds a compromise layout that
maximises social welfare while maintaining fairness.

This is an *agentic* capability — the tool autonomously identifies
conflicts, infers agent preferences, runs a negotiation protocol,
and produces a Pareto-aware resolution with full audit trail.

Six analysis engines:

- **Conflict Detection** — five conflict types: territory imbalance,
  border tension, crowding, isolation, resource contention.
- **Preference Inference** — auto-assign per-point preferences
  (desired area, buffer radius, centrality weight) from layout
  statistics, with optional user overrides via JSON.
- **Negotiation Engine** — multi-round iterative resolution: compute
  satisfaction, propose movements proportional to dissatisfaction,
  converge when delta < epsilon or max rounds reached.
- **Pareto Analysis** — evaluate social welfare, fairness (1 − Gini),
  and whether the result is Pareto-optimal.
- **Compromise Report** — per-point wanted-vs-got breakdown with
  compromise ratios and conflicting-neighbor identification.
- **Resolution Timeline** — per-round conflict count, average
  satisfaction, and convergence curve.

Usage (Python API)::

    from vormap_negotiator import negotiate, Negotiator

    result = negotiate("points.txt")
    print(f"Social welfare: {result.social_welfare:.3f}")
    print(f"Fairness: {result.fairness:.3f}")
    for c in result.conflicts:
        print(f"  [{c.severity}] {c.kind}: {c.message}")

    # Detailed API
    n = Negotiator(points=[(100,200), (300,400), ...])
    result = n.run(max_rounds=20, epsilon=0.01)
    result.to_json("result.json")
    result.to_html("result.html")

CLI::

    python vormap_negotiator.py points.txt
    python vormap_negotiator.py points.txt --rounds 20 --epsilon 0.01
    python vormap_negotiator.py points.txt --preferences prefs.json
    python vormap_negotiator.py points.txt --json result.json
    python vormap_negotiator.py points.txt --html result.html
    python vormap_negotiator.py --demo
"""

from __future__ import annotations

import argparse
import html as _html
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vormap_utils import (
    euclidean as _dist,
    load_points as _load_points,
    polygon_area as _polygon_area,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Conflict:
    """A detected spatial conflict."""
    kind: str          # TerritoryImbalance | BorderTension | Crowding | Isolation | ResourceContention
    severity: str      # info | warning | critical
    indices: List[int] # affected point indices
    message: str
    value: float = 0.0 # metric value that triggered the conflict


@dataclass
class Preference:
    """Per-point negotiation preferences."""
    desired_area: float = 0.0
    buffer_radius: float = 0.0
    centrality_weight: float = 0.5


@dataclass
class CompromiseEntry:
    """Per-point compromise report."""
    index: int
    wanted_area: float
    got_area: float
    wanted_buffer: float
    got_buffer: float
    compromise_ratio: float  # 0 = got nothing, 1 = got everything
    conflicting_neighbors: List[int] = field(default_factory=list)


@dataclass
class RoundSnapshot:
    """Per-round negotiation state."""
    round_num: int
    conflict_count: int
    avg_satisfaction: float
    max_movement: float
    satisfactions: List[float] = field(default_factory=list)


@dataclass
class NegotiationResult:
    """Full negotiation result."""
    original_points: List[Tuple[float, float]]
    final_points: List[Tuple[float, float]]
    conflicts: List[Conflict]
    residual_conflicts: List[Conflict]
    preferences: List[Preference]
    compromises: List[CompromiseEntry]
    timeline: List[RoundSnapshot]
    rounds_used: int
    converged: bool
    social_welfare: float       # mean satisfaction
    fairness: float             # 1 - Gini of satisfactions
    pareto_optimal: bool
    final_satisfactions: List[float] = field(default_factory=list)

    # --- serialisation ------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_points": self.original_points,
            "final_points": self.final_points,
            "conflicts": [
                {"kind": c.kind, "severity": c.severity,
                 "indices": c.indices, "message": c.message, "value": c.value}
                for c in self.conflicts
            ],
            "residual_conflicts": [
                {"kind": c.kind, "severity": c.severity,
                 "indices": c.indices, "message": c.message, "value": c.value}
                for c in self.residual_conflicts
            ],
            "preferences": [
                {"desired_area": p.desired_area, "buffer_radius": p.buffer_radius,
                 "centrality_weight": p.centrality_weight}
                for p in self.preferences
            ],
            "compromises": [
                {"index": c.index, "wanted_area": c.wanted_area,
                 "got_area": c.got_area, "wanted_buffer": c.wanted_buffer,
                 "got_buffer": c.got_buffer, "compromise_ratio": c.compromise_ratio,
                 "conflicting_neighbors": c.conflicting_neighbors}
                for c in self.compromises
            ],
            "timeline": [
                {"round": s.round_num, "conflict_count": s.conflict_count,
                 "avg_satisfaction": s.avg_satisfaction, "max_movement": s.max_movement}
                for s in self.timeline
            ],
            "rounds_used": self.rounds_used,
            "converged": self.converged,
            "social_welfare": self.social_welfare,
            "fairness": self.fairness,
            "pareto_optimal": self.pareto_optimal,
            "final_satisfactions": self.final_satisfactions,
        }

    def to_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    def to_html(self, path: str) -> None:
        html = _build_html(self)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)


# ---------------------------------------------------------------------------
# Geometry helpers (lightweight, no numpy)
# ---------------------------------------------------------------------------

def _voronoi_cells(points: List[Tuple[float, float]], bbox=None):
    """Compute approximate Voronoi cell polygons via grid sampling.

    Returns list of polygon vertex lists, one per point.  Uses a
    grid-based approach to avoid scipy dependency.
    """
    if not points:
        return []
    if bbox is None:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        margin = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.1 + 1
        bbox = (min(xs) - margin, min(ys) - margin,
                max(xs) + margin, max(ys) + margin)
    xmin, ymin, xmax, ymax = bbox
    n = len(points)
    # Grid resolution adaptive to point count
    res = max(40, min(200, int(math.sqrt(n) * 15)))
    dx = (xmax - xmin) / res
    dy = (ymax - ymin) / res
    # Assign grid cells to nearest point
    grid = {}
    for gy in range(res):
        cy = ymin + (gy + 0.5) * dy
        for gx in range(res):
            cx = xmin + (gx + 0.5) * dx
            best_i = 0
            best_d = float("inf")
            for i, (px, py) in enumerate(points):
                d = (cx - px) ** 2 + (cy - py) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            grid[(gx, gy)] = best_i

    # Extract cell boundaries as convex hull of grid cell centres
    cell_pixels: Dict[int, List[Tuple[float, float]]] = {i: [] for i in range(n)}
    for (gx, gy), owner in grid.items():
        cell_pixels[owner].append((xmin + (gx + 0.5) * dx,
                                   ymin + (gy + 0.5) * dy))

    cells = []
    for i in range(n):
        pts = cell_pixels[i]
        if len(pts) < 3:
            cells.append(pts)
        else:
            cells.append(_convex_hull(pts))
    return cells


def _convex_hull(points: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Andrew's monotone chain convex hull."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _cell_areas(cells):
    """Compute areas of cell polygons."""
    areas = []
    for cell in cells:
        if len(cell) < 3:
            areas.append(0.0)
        else:
            areas.append(abs(_polygon_area(cell)))
    return areas


def _nn_distances(points):
    """Nearest-neighbor distances for each point."""
    n = len(points)
    dists = []
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = _dist(points[i], points[j])
            if d < best:
                best = d
        dists.append(best if best < float("inf") else 0.0)
    return dists


def _neighbors(points, cells):
    """Find neighbor indices for each point based on shared cell boundaries.

    Two points are neighbors if they are each other's nearest grid
    candidates — approximated by checking if any grid cell assigned to
    point i is adjacent to a grid cell assigned to point j.
    Here we use a simpler distance heuristic: two points are neighbors
    if their distance is ≤ 2× the median NN distance.
    """
    n = len(points)
    nn_dists = _nn_distances(points)
    if not nn_dists:
        return {i: [] for i in range(n)}
    median_nn = sorted(nn_dists)[len(nn_dists) // 2]
    threshold = median_nn * 2.5
    nbrs: Dict[int, List[int]] = {i: [] for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            d = _dist(points[i], points[j])
            if d <= threshold:
                nbrs[i].append(j)
                nbrs[j].append(i)
    return nbrs


def _gini(values: List[float]) -> float:
    """Gini coefficient of a list of non-negative values."""
    if not values:
        return 0.0
    n = len(values)
    s = sorted(values)
    total = sum(s)
    if total == 0:
        return 0.0
    cumsum = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(s):
        cumsum += v
        weighted_sum += (2 * (i + 1) - n - 1) * v
    return weighted_sum / (n * total)


# ---------------------------------------------------------------------------
# Engine 1: Conflict Detection
# ---------------------------------------------------------------------------

def detect_conflicts(points, cells=None, areas=None, nn_dists=None,
                     nbrs=None, bbox=None,
                     imbalance_threshold=0.35,
                     tension_ratio=3.0,
                     crowding_factor=0.3,
                     isolation_factor=3.0):
    """Detect spatial conflicts.

    Returns list of Conflict objects.
    """
    n = len(points)
    if n < 2:
        return []
    if cells is None:
        cells = _voronoi_cells(points, bbox)
    if areas is None:
        areas = _cell_areas(cells)
    if nn_dists is None:
        nn_dists = _nn_distances(points)
    if nbrs is None:
        nbrs = _neighbors(points, cells)

    conflicts: List[Conflict] = []

    # 1. Territory Imbalance (Gini of areas)
    positive_areas = [a for a in areas if a > 0]
    if positive_areas:
        gini_val = _gini(positive_areas)
        if gini_val > imbalance_threshold:
            sev = "critical" if gini_val > 0.5 else "warning"
            conflicts.append(Conflict(
                kind="TerritoryImbalance",
                severity=sev,
                indices=list(range(n)),
                message=f"Area Gini coefficient {gini_val:.3f} exceeds threshold {imbalance_threshold}",
                value=gini_val,
            ))

    # 2. Border Tension (adjacent cells with extreme area ratio)
    seen_pairs = set()
    for i in range(n):
        for j in nbrs.get(i, []):
            pair = (min(i, j), max(i, j))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if areas[i] > 0 and areas[j] > 0:
                ratio = max(areas[i], areas[j]) / min(areas[i], areas[j])
                if ratio > tension_ratio:
                    sev = "critical" if ratio > tension_ratio * 2 else "warning"
                    conflicts.append(Conflict(
                        kind="BorderTension",
                        severity=sev,
                        indices=[i, j],
                        message=f"Points {i}↔{j} area ratio {ratio:.2f} (threshold {tension_ratio})",
                        value=ratio,
                    ))

    # 3. Crowding
    if nn_dists:
        median_nn = sorted(nn_dists)[len(nn_dists) // 2]
        crowd_thresh = median_nn * crowding_factor
        for i in range(n):
            if nn_dists[i] < crowd_thresh:
                conflicts.append(Conflict(
                    kind="Crowding",
                    severity="warning",
                    indices=[i],
                    message=f"Point {i} NN distance {nn_dists[i]:.2f} < {crowd_thresh:.2f}",
                    value=nn_dists[i],
                ))

    # 4. Isolation
    if nn_dists:
        median_nn = sorted(nn_dists)[len(nn_dists) // 2]
        iso_thresh = median_nn * isolation_factor
        for i in range(n):
            if nn_dists[i] > iso_thresh:
                conflicts.append(Conflict(
                    kind="Isolation",
                    severity="info",
                    indices=[i],
                    message=f"Point {i} NN distance {nn_dists[i]:.2f} > {iso_thresh:.2f}",
                    value=nn_dists[i],
                ))

    # 5. Resource Contention (multiple points close to global centroid)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    dists_to_center = [_dist(p, (cx, cy)) for p in points]
    if dists_to_center:
        median_dc = sorted(dists_to_center)[len(dists_to_center) // 2]
        contention_thresh = median_dc * 0.3
        contenders = [i for i, d in enumerate(dists_to_center) if d < contention_thresh]
        if len(contenders) > max(2, n * 0.2):
            conflicts.append(Conflict(
                kind="ResourceContention",
                severity="warning",
                indices=contenders,
                message=f"{len(contenders)} points competing for central position "
                        f"(within {contention_thresh:.1f} of centroid)",
                value=float(len(contenders)),
            ))

    return conflicts


# ---------------------------------------------------------------------------
# Engine 2: Preference Inference
# ---------------------------------------------------------------------------

def infer_preferences(points, areas=None, nn_dists=None,
                      overrides: Optional[Dict[int, Dict]] = None) -> List[Preference]:
    """Auto-assign preferences to each point.

    *overrides* maps point index → dict with optional keys
    ``desired_area``, ``buffer_radius``, ``centrality_weight``.
    """
    n = len(points)
    if areas is None:
        cells = _voronoi_cells(points)
        areas = _cell_areas(cells)
    if nn_dists is None:
        nn_dists = _nn_distances(points)

    mean_area = sum(areas) / n if n else 1.0
    mean_nn = sum(nn_dists) / n if n else 1.0
    cx = sum(p[0] for p in points) / n if n else 0.0
    cy = sum(p[1] for p in points) / n if n else 0.0
    max_dist_c = max(_dist(p, (cx, cy)) for p in points) if n else 1.0
    if max_dist_c == 0:
        max_dist_c = 1.0

    prefs = []
    for i in range(n):
        p = Preference(
            desired_area=mean_area,
            buffer_radius=mean_nn * 0.8,
            centrality_weight=1.0 - min(1.0, _dist(points[i], (cx, cy)) / max_dist_c),
        )
        if overrides and i in overrides:
            ov = overrides[i]
            if "desired_area" in ov:
                p.desired_area = ov["desired_area"]
            if "buffer_radius" in ov:
                p.buffer_radius = ov["buffer_radius"]
            if "centrality_weight" in ov:
                p.centrality_weight = ov["centrality_weight"]
        prefs.append(p)
    return prefs


# ---------------------------------------------------------------------------
# Engine 3: Negotiation Engine
# ---------------------------------------------------------------------------

def _satisfaction(point_idx, points, areas, nn_dists, prefs, cx, cy, max_dist_c):
    """Compute satisfaction score (0–1) for a single point."""
    p = prefs[point_idx]
    # Area satisfaction: 1 when area == desired, decays otherwise
    if p.desired_area > 0:
        area_ratio = areas[point_idx] / p.desired_area
        area_sat = 1.0 - min(1.0, abs(1.0 - area_ratio))
    else:
        area_sat = 1.0

    # Buffer satisfaction: 1 when nn_dist >= buffer_radius
    if p.buffer_radius > 0:
        buf_sat = min(1.0, nn_dists[point_idx] / p.buffer_radius)
    else:
        buf_sat = 1.0

    # Centrality satisfaction
    if max_dist_c > 0:
        rel_dist = _dist(points[point_idx], (cx, cy)) / max_dist_c
        cent_sat = 1.0 - abs(p.centrality_weight - (1.0 - rel_dist))
        cent_sat = max(0.0, cent_sat)
    else:
        cent_sat = 1.0

    return 0.45 * area_sat + 0.35 * buf_sat + 0.20 * cent_sat


def _propose_movement(idx, points, areas, nn_dists, prefs, nbrs,
                      cx, cy, max_dist_c, step_scale=0.1):
    """Propose a movement vector for a dissatisfied point."""
    p = prefs[idx]
    sat = _satisfaction(idx, points, areas, nn_dists, prefs, cx, cy, max_dist_c)
    if sat > 0.95:
        return (0.0, 0.0)

    force_x, force_y = 0.0, 0.0
    px, py = points[idx]
    dissatisfaction = 1.0 - sat

    # Force from area imbalance: move away from large neighbors
    for j in nbrs.get(idx, []):
        if areas[j] > 0 and areas[idx] > 0:
            ratio = areas[j] / areas[idx]
            if ratio > 1.5:
                dx = px - points[j][0]
                dy = py - points[j][1]
                d = math.sqrt(dx * dx + dy * dy)
                if d > 0:
                    mag = (ratio - 1.0) * step_scale
                    force_x += dx / d * mag
                    force_y += dy / d * mag

    # Force from crowding: repel from nearest neighbor
    if nn_dists[idx] < p.buffer_radius:
        # Find nearest neighbor
        best_j = -1
        best_d = float("inf")
        for j in range(len(points)):
            if j == idx:
                continue
            d = _dist(points[idx], points[j])
            if d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0 and best_d > 0:
            dx = px - points[best_j][0]
            dy = py - points[best_j][1]
            d = math.sqrt(dx * dx + dy * dy)
            if d > 0:
                mag = (p.buffer_radius - nn_dists[idx]) / p.buffer_radius * step_scale
                force_x += dx / d * mag
                force_y += dy / d * mag

    # Force toward/away from centre based on centrality preference
    if max_dist_c > 0:
        rel_dist = _dist(points[idx], (cx, cy)) / max_dist_c
        desired_rel = 1.0 - p.centrality_weight
        diff = desired_rel - rel_dist
        dx = cx - px
        dy = cy - py
        d = math.sqrt(dx * dx + dy * dy)
        if d > 0:
            mag = diff * step_scale * 0.5
            force_x += dx / d * mag
            force_y += dy / d * mag

    # Scale by dissatisfaction
    force_x *= dissatisfaction
    force_y *= dissatisfaction

    # Clamp movement magnitude
    mag = math.sqrt(force_x ** 2 + force_y ** 2)
    nn_med = sorted(nn_dists)[len(nn_dists) // 2] if nn_dists else 1.0
    max_step = nn_med * step_scale * 2
    if mag > max_step and mag > 0:
        force_x = force_x / mag * max_step
        force_y = force_y / mag * max_step

    return (force_x, force_y)


# ---------------------------------------------------------------------------
# Engine 4: Pareto Analysis
# ---------------------------------------------------------------------------

def _is_pareto_optimal(satisfactions: List[float], tolerance=0.01) -> bool:
    """Check if the satisfaction vector is approximately Pareto-optimal.

    A simple heuristic: if all satisfactions are above a minimum threshold
    and variance is low, consider it Pareto-optimal (no easy single-agent
    improvements without hurting others).
    """
    if not satisfactions:
        return True
    min_sat = min(satisfactions)
    mean_sat = sum(satisfactions) / len(satisfactions)
    # If worst-off agent is within tolerance of mean, it's hard to improve
    # anyone without hurting another
    if min_sat > mean_sat - tolerance and mean_sat > 0.7:
        return True
    return False


# ---------------------------------------------------------------------------
# Engine 5: Compromise Report
# ---------------------------------------------------------------------------

def _build_compromises(points, original_points, areas, original_areas,
                       nn_dists, original_nn_dists, prefs,
                       nbrs) -> List[CompromiseEntry]:
    """Build per-point compromise reports."""
    n = len(points)
    compromises = []
    for i in range(n):
        p = prefs[i]
        # Compute compromise ratios per dimension
        area_comp = 0.5
        if p.desired_area > 0:
            before_gap = abs(original_areas[i] - p.desired_area)
            after_gap = abs(areas[i] - p.desired_area)
            if before_gap > 0:
                area_comp = max(0.0, min(1.0, 1.0 - after_gap / before_gap))
            elif after_gap < 1e-6:
                area_comp = 1.0

        buf_comp = 0.5
        if p.buffer_radius > 0:
            if original_nn_dists[i] < p.buffer_radius:
                before_gap = p.buffer_radius - original_nn_dists[i]
                after_gap = max(0, p.buffer_radius - nn_dists[i])
                if before_gap > 0:
                    buf_comp = max(0.0, min(1.0, 1.0 - after_gap / before_gap))
                else:
                    buf_comp = 1.0
            else:
                buf_comp = 1.0

        ratio = 0.5 * area_comp + 0.5 * buf_comp

        # Find conflicting neighbors (neighbors that moved toward this point)
        conflicting = []
        for j in nbrs.get(i, []):
            d_before = _dist(original_points[i], original_points[j])
            d_after = _dist(points[i], points[j])
            if d_after < d_before * 0.95:
                conflicting.append(j)

        compromises.append(CompromiseEntry(
            index=i,
            wanted_area=p.desired_area,
            got_area=areas[i],
            wanted_buffer=p.buffer_radius,
            got_buffer=nn_dists[i],
            compromise_ratio=round(ratio, 4),
            conflicting_neighbors=conflicting,
        ))
    return compromises


# ---------------------------------------------------------------------------
# Main Negotiator
# ---------------------------------------------------------------------------

class Negotiator:
    """Autonomous spatial conflict negotiator.

    Parameters
    ----------
    points : list of (float, float)
        Seed coordinates.
    bbox : tuple, optional
        (xmin, ymin, xmax, ymax) bounding box.
    """

    def __init__(self, points: Sequence[Tuple[float, float]], bbox=None):
        self.original_points = [(float(x), float(y)) for x, y in points]
        self.bbox = bbox

    def run(self, max_rounds: int = 15, epsilon: float = 0.02,
            step_scale: float = 0.15,
            preference_overrides: Optional[Dict[int, Dict]] = None,
            ) -> NegotiationResult:
        """Run the full negotiation pipeline.

        Parameters
        ----------
        max_rounds : int
            Maximum negotiation rounds.
        epsilon : float
            Convergence threshold (max satisfaction delta between rounds).
        step_scale : float
            Movement step scale factor.
        preference_overrides : dict, optional
            Per-point preference overrides {index: {key: value}}.
        """
        pts = list(self.original_points)
        n = len(pts)
        if n < 2:
            return NegotiationResult(
                original_points=self.original_points,
                final_points=pts,
                conflicts=[], residual_conflicts=[],
                preferences=[], compromises=[], timeline=[],
                rounds_used=0, converged=True,
                social_welfare=1.0, fairness=1.0,
                pareto_optimal=True, final_satisfactions=[1.0] * n,
            )

        # Compute initial state
        cells = _voronoi_cells(pts, self.bbox)
        areas = _cell_areas(cells)
        nn_dists = _nn_distances(pts)
        nbrs = _neighbors(pts, cells)
        original_areas = list(areas)
        original_nn_dists = list(nn_dists)

        # Detect initial conflicts
        initial_conflicts = detect_conflicts(
            pts, cells=cells, areas=areas, nn_dists=nn_dists, nbrs=nbrs,
            bbox=self.bbox,
        )

        # Infer preferences
        prefs = infer_preferences(pts, areas=areas, nn_dists=nn_dists,
                                  overrides=preference_overrides)

        # Centroid for centrality
        gx = sum(p[0] for p in pts) / n
        gy = sum(p[1] for p in pts) / n
        max_dist_c = max(_dist(p, (gx, gy)) for p in pts)
        if max_dist_c == 0:
            max_dist_c = 1.0

        # Negotiation loop
        timeline: List[RoundSnapshot] = []
        prev_sats = [_satisfaction(i, pts, areas, nn_dists, prefs, gx, gy, max_dist_c)
                     for i in range(n)]
        converged = False

        for rnd in range(1, max_rounds + 1):
            # Propose movements
            movements = []
            for i in range(n):
                mv = _propose_movement(i, pts, areas, nn_dists, prefs, nbrs,
                                       gx, gy, max_dist_c, step_scale)
                movements.append(mv)

            # Apply movements
            max_mv = 0.0
            new_pts = []
            for i in range(n):
                nx = pts[i][0] + movements[i][0]
                ny = pts[i][1] + movements[i][1]
                new_pts.append((nx, ny))
                mv_mag = math.sqrt(movements[i][0] ** 2 + movements[i][1] ** 2)
                if mv_mag > max_mv:
                    max_mv = mv_mag
            pts = new_pts

            # Recompute spatial state
            cells = _voronoi_cells(pts, self.bbox)
            areas = _cell_areas(cells)
            nn_dists = _nn_distances(pts)
            nbrs = _neighbors(pts, cells)

            # Recompute centroid
            gx = sum(p[0] for p in pts) / n
            gy = sum(p[1] for p in pts) / n
            max_dist_c = max(_dist(p, (gx, gy)) for p in pts)
            if max_dist_c == 0:
                max_dist_c = 1.0

            # Satisfaction
            sats = [_satisfaction(i, pts, areas, nn_dists, prefs, gx, gy, max_dist_c)
                    for i in range(n)]

            # Conflicts this round
            round_conflicts = detect_conflicts(
                pts, cells=cells, areas=areas, nn_dists=nn_dists, nbrs=nbrs,
                bbox=self.bbox,
            )

            timeline.append(RoundSnapshot(
                round_num=rnd,
                conflict_count=len(round_conflicts),
                avg_satisfaction=sum(sats) / n,
                max_movement=max_mv,
                satisfactions=list(sats),
            ))

            # Check convergence
            max_delta = max(abs(sats[i] - prev_sats[i]) for i in range(n))
            if max_delta < epsilon and rnd > 1:
                converged = True
                prev_sats = sats
                break
            prev_sats = sats

        # Final analysis
        final_sats = prev_sats
        social_welfare = sum(final_sats) / n
        fairness = 1.0 - _gini(final_sats)
        pareto = _is_pareto_optimal(final_sats)

        # Residual conflicts
        residual = detect_conflicts(pts, cells=cells, areas=areas,
                                    nn_dists=nn_dists, nbrs=nbrs, bbox=self.bbox)

        # Compromise report
        compromises = _build_compromises(
            pts, self.original_points, areas, original_areas,
            nn_dists, original_nn_dists, prefs, nbrs,
        )

        return NegotiationResult(
            original_points=self.original_points,
            final_points=pts,
            conflicts=initial_conflicts,
            residual_conflicts=residual,
            preferences=prefs,
            compromises=compromises,
            timeline=timeline,
            rounds_used=len(timeline),
            converged=converged,
            social_welfare=round(social_welfare, 4),
            fairness=round(fairness, 4),
            pareto_optimal=pareto,
            final_satisfactions=[round(s, 4) for s in final_sats],
        )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def negotiate(source, **kwargs) -> NegotiationResult:
    """Convenience wrapper — accepts file path or point list.

    All keyword arguments are forwarded to :meth:`Negotiator.run`.
    """
    if isinstance(source, str):
        points = _load_points(source)
    else:
        points = list(source)
    bbox = kwargs.pop("bbox", None)
    neg = Negotiator(points, bbox=bbox)
    return neg.run(**kwargs)


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _build_html(result: NegotiationResult) -> str:
    """Build a self-contained interactive HTML report."""
    e = _html.escape

    # Timeline data for chart
    tl_rounds = [s.round_num for s in result.timeline]
    tl_sats = [round(s.avg_satisfaction, 4) for s in result.timeline]
    tl_conflicts = [s.conflict_count for s in result.timeline]

    # Point colours by satisfaction
    def _sat_color(s):
        r = int(255 * (1 - s))
        g = int(255 * s)
        return f"rgb({r},{g},60)"

    # Build SVG layout comparison
    all_pts = result.original_points + result.final_points
    if all_pts:
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        pmin_x, pmax_x = min(xs), max(xs)
        pmin_y, pmax_y = min(ys), max(ys)
        pw = pmax_x - pmin_x or 1
        ph = pmax_y - pmin_y or 1
    else:
        pmin_x = pmin_y = 0
        pw = ph = 1

    svg_w, svg_h = 400, 300
    margin = 20

    def _tx(x):
        return margin + (x - pmin_x) / pw * (svg_w - 2 * margin)

    def _ty(y):
        return margin + (y - pmin_y) / ph * (svg_h - 2 * margin)

    orig_dots = "".join(
        f'<circle cx="{_tx(p[0]):.1f}" cy="{_ty(p[1]):.1f}" r="4" fill="#888" opacity="0.5"/>'
        for p in result.original_points
    )
    final_dots = "".join(
        f'<circle cx="{_tx(p[0]):.1f}" cy="{_ty(p[1]):.1f}" r="5" '
        f'fill="{_sat_color(result.final_satisfactions[i] if i < len(result.final_satisfactions) else 0.5)}"/>'
        for i, p in enumerate(result.final_points)
    )
    # Arrows from original to final
    arrows = "".join(
        f'<line x1="{_tx(result.original_points[i][0]):.1f}" '
        f'y1="{_ty(result.original_points[i][1]):.1f}" '
        f'x2="{_tx(result.final_points[i][0]):.1f}" '
        f'y2="{_ty(result.final_points[i][1]):.1f}" '
        f'stroke="#aaa" stroke-width="1" stroke-dasharray="3,3"/>'
        for i in range(len(result.original_points))
        if i < len(result.final_points)
    )

    # Timeline SVG
    tl_svg_w, tl_svg_h = 500, 200
    tl_margin = 40
    if tl_rounds:
        max_r = max(tl_rounds)
        max_c = max(tl_conflicts) if tl_conflicts else 1
    else:
        max_r = max_c = 1

    def _tl_x(r):
        return tl_margin + (r / max(max_r, 1)) * (tl_svg_w - 2 * tl_margin)

    def _tl_y_sat(s):
        return tl_svg_h - tl_margin - s * (tl_svg_h - 2 * tl_margin)

    def _tl_y_conf(c):
        return tl_svg_h - tl_margin - (c / max(max_c, 1)) * (tl_svg_h - 2 * tl_margin)

    sat_path = " ".join(
        f"{'M' if i == 0 else 'L'}{_tl_x(r):.1f},{_tl_y_sat(s):.1f}"
        for i, (r, s) in enumerate(zip(tl_rounds, tl_sats))
    )
    conf_bars = "".join(
        f'<rect x="{_tl_x(r) - 5:.1f}" y="{_tl_y_conf(c):.1f}" '
        f'width="10" height="{tl_svg_h - tl_margin - _tl_y_conf(c):.1f}" '
        f'fill="rgba(220,80,60,0.4)"/>'
        for r, c in zip(tl_rounds, tl_conflicts)
    )

    # Compromise table rows
    comp_rows = ""
    for c in result.compromises:
        bg = "#e8f5e9" if c.compromise_ratio > 0.7 else "#fff3e0" if c.compromise_ratio > 0.4 else "#fce4ec"
        comp_rows += (
            f"<tr style='background:{bg}'>"
            f"<td>{c.index}</td>"
            f"<td>{c.wanted_area:.1f}</td><td>{c.got_area:.1f}</td>"
            f"<td>{c.wanted_buffer:.1f}</td><td>{c.got_buffer:.1f}</td>"
            f"<td><b>{c.compromise_ratio:.2f}</b></td>"
            f"<td>{', '.join(str(n) for n in c.conflicting_neighbors) or '—'}</td>"
            f"</tr>"
        )

    # Conflict list
    conf_html = ""
    for c in result.conflicts:
        icon = "🔴" if c.severity == "critical" else "🟡" if c.severity == "warning" else "🔵"
        conf_html += f"<div>{icon} <b>{e(c.kind)}</b>: {e(c.message)}</div>"
    if not conf_html:
        conf_html = "<div>✅ No initial conflicts detected</div>"

    resid_html = ""
    for c in result.residual_conflicts:
        icon = "🔴" if c.severity == "critical" else "🟡" if c.severity == "warning" else "🔵"
        resid_html += f"<div>{icon} <b>{e(c.kind)}</b>: {e(c.message)}</div>"
    if not resid_html:
        resid_html = "<div>✅ All conflicts resolved</div>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Spatial Conflict Negotiation Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#f5f5f5;color:#333;padding:24px}}
.container{{max-width:1100px;margin:0 auto}}
h1{{font-size:1.6rem;margin-bottom:8px;color:#1a237e}}
h2{{font-size:1.2rem;margin:20px 0 8px;color:#283593}}
.card{{background:#fff;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.08)}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}}
.metric{{text-align:center;padding:16px;border-radius:8px;background:#f8f9fa}}
.metric .val{{font-size:1.8rem;font-weight:700}}
.metric .lbl{{font-size:.75rem;color:#666;margin-top:4px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th,td{{padding:6px 10px;border-bottom:1px solid #eee;text-align:left}}
th{{background:#f0f0f0;font-weight:600}}
svg{{display:block;margin:8px auto}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:600}}
.badge-ok{{background:#c8e6c9;color:#2e7d32}}
.badge-warn{{background:#fff9c4;color:#f57f17}}
.badge-fail{{background:#ffcdd2;color:#c62828}}
</style></head><body>
<div class="container">
<h1>🤝 Spatial Conflict Negotiation Report</h1>
<p style="color:#666;margin-bottom:16px">{len(result.original_points)} points · {result.rounds_used} rounds · {'converged ✓' if result.converged else 'max rounds reached'}</p>

<div class="metrics">
  <div class="metric"><div class="val" style="color:{'#2e7d32' if result.social_welfare > 0.7 else '#f57f17' if result.social_welfare > 0.5 else '#c62828'}">{result.social_welfare:.3f}</div><div class="lbl">Social Welfare</div></div>
  <div class="metric"><div class="val" style="color:{'#2e7d32' if result.fairness > 0.8 else '#f57f17'}">{result.fairness:.3f}</div><div class="lbl">Fairness (1−Gini)</div></div>
  <div class="metric"><div class="val">{len(result.conflicts)}</div><div class="lbl">Initial Conflicts</div></div>
  <div class="metric"><div class="val" style="color:{'#2e7d32' if len(result.residual_conflicts) == 0 else '#f57f17'}">{len(result.residual_conflicts)}</div><div class="lbl">Residual Conflicts</div></div>
  <div class="metric"><div class="val"><span class="badge {'badge-ok' if result.pareto_optimal else 'badge-warn'}">{'Yes' if result.pareto_optimal else 'No'}</span></div><div class="lbl">Pareto Optimal</div></div>
</div>

<div class="card">
<h2>📍 Layout Comparison (Before → After)</h2>
<svg width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">
  <rect width="{svg_w}" height="{svg_h}" fill="#fafafa" rx="6"/>
  {arrows}{orig_dots}{final_dots}
  <text x="10" y="14" font-size="11" fill="#999">Grey=original · Coloured=final (green=satisfied, red=dissatisfied)</text>
</svg>
</div>

<div class="card">
<h2>📊 Negotiation Timeline</h2>
<svg width="{tl_svg_w}" height="{tl_svg_h}" viewBox="0 0 {tl_svg_w} {tl_svg_h}">
  <rect width="{tl_svg_w}" height="{tl_svg_h}" fill="#fafafa" rx="6"/>
  <line x1="{tl_margin}" y1="{tl_svg_h - tl_margin}" x2="{tl_svg_w - tl_margin}" y2="{tl_svg_h - tl_margin}" stroke="#ccc"/>
  <line x1="{tl_margin}" y1="{tl_margin}" x2="{tl_margin}" y2="{tl_svg_h - tl_margin}" stroke="#ccc"/>
  {conf_bars}
  <path d="{sat_path}" fill="none" stroke="#1565c0" stroke-width="2"/>
  <text x="{tl_svg_w - tl_margin}" y="{tl_svg_h - 5}" font-size="10" fill="#999" text-anchor="end">Round</text>
  <text x="5" y="{tl_margin - 5}" font-size="10" fill="#1565c0">Satisfaction</text>
  <text x="{tl_svg_w - 10}" y="{tl_margin - 5}" font-size="10" fill="#c62828" text-anchor="end">Conflicts (bars)</text>
</svg>
</div>

<div class="card">
<h2>⚠️ Initial Conflicts</h2>
{conf_html}
</div>

<div class="card">
<h2>✅ Residual Conflicts (After Negotiation)</h2>
{resid_html}
</div>

<div class="card">
<h2>🤝 Compromise Breakdown</h2>
<table>
<tr><th>Point</th><th>Wanted Area</th><th>Got Area</th><th>Wanted Buffer</th><th>Got Buffer</th><th>Compromise</th><th>Conflicted With</th></tr>
{comp_rows}
</table>
</div>

</div></body></html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _demo():
    """Run a demo negotiation on generated points.

    Uses a local ``random.Random`` instance so the demo does not
    perturb the host process's global RNG state. See issue #192.
    """
    rng = random.Random(42)
    # Create a deliberately conflicted layout
    points = []
    # Cluster of crowded points near centre
    for _ in range(6):
        points.append((500 + rng.uniform(-30, 30),
                        500 + rng.uniform(-30, 30)))
    # Scattered outliers
    points.append((50, 50))
    points.append((950, 950))
    points.append((50, 950))
    points.append((950, 50))
    # Mid-range points
    for _ in range(8):
        points.append((rng.uniform(100, 900), rng.uniform(100, 900)))

    print("=" * 60)
    print("  SPATIAL CONFLICT NEGOTIATOR — DEMO")
    print("=" * 60)
    print(f"\n{len(points)} points generated (clustered centre + outliers)")
    print()

    neg = Negotiator(points, bbox=(0, 0, 1000, 1000))
    result = neg.run(max_rounds=20, epsilon=0.01)

    print(f"Rounds: {result.rounds_used} ({'converged' if result.converged else 'max reached'})")
    print(f"Social Welfare: {result.social_welfare:.3f}")
    print(f"Fairness: {result.fairness:.3f}")
    print(f"Pareto Optimal: {'Yes' if result.pareto_optimal else 'No'}")
    print(f"Initial Conflicts: {len(result.conflicts)}")
    print(f"Residual Conflicts: {len(result.residual_conflicts)}")
    print()

    print("--- Initial Conflicts ---")
    for c in result.conflicts:
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(c.severity, "·")
        print(f"  {icon} [{c.severity}] {c.kind}: {c.message}")
    print()

    print("--- Compromise Summary ---")
    for c in result.compromises[:10]:
        print(f"  Point {c.index}: compromise={c.compromise_ratio:.2f} "
              f"(area {c.wanted_area:.0f}→{c.got_area:.0f}, "
              f"buffer {c.wanted_buffer:.1f}→{c.got_buffer:.1f})")
    if len(result.compromises) > 10:
        print(f"  ... and {len(result.compromises) - 10} more")
    print()

    print("--- Timeline ---")
    for s in result.timeline:
        bar = "█" * int(s.avg_satisfaction * 20)
        print(f"  Round {s.round_num:2d}: sat={s.avg_satisfaction:.3f} {bar} "
              f"conflicts={s.conflict_count} Δ={s.max_movement:.3f}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Spatial Conflict Negotiator — autonomous conflict resolution "
                    "for Voronoi point layouts",
    )
    parser.add_argument("input", nargs="?", help="Points file (one 'x y' per line)")
    parser.add_argument("--demo", action="store_true", help="Run demo with generated points")
    parser.add_argument("--rounds", type=int, default=15, help="Max negotiation rounds (default 15)")
    parser.add_argument("--epsilon", type=float, default=0.02, help="Convergence threshold (default 0.02)")
    parser.add_argument("--step-scale", type=float, default=0.15, help="Movement step scale (default 0.15)")
    parser.add_argument("--preferences", type=str, help="JSON file with preference overrides")
    parser.add_argument("--json", dest="json_path", help="Write JSON report")
    parser.add_argument("--html", dest="html_path", help="Write HTML report")
    parser.add_argument("--output", help="Write resolved points to file")
    args = parser.parse_args()

    if args.demo:
        result = _demo()
        if args.json_path:
            result.to_json(args.json_path)
            print(f"\nJSON report written to {args.json_path}")
        if args.html_path:
            result.to_html(args.html_path)
            print(f"HTML report written to {args.html_path}")
        return

    if not args.input:
        parser.error("Provide a points file or use --demo")

    points = _load_points(args.input)

    overrides = None
    if args.preferences:
        with open(args.preferences) as fh:
            raw = json.load(fh)
            overrides = {int(k): v for k, v in raw.items()}

    neg = Negotiator(points)
    result = neg.run(max_rounds=args.rounds, epsilon=args.epsilon,
                     step_scale=args.step_scale,
                     preference_overrides=overrides)

    print(f"Negotiation complete: {result.rounds_used} rounds, "
          f"{'converged' if result.converged else 'max reached'}")
    print(f"Social Welfare: {result.social_welfare:.3f}")
    print(f"Fairness: {result.fairness:.3f}")
    print(f"Pareto Optimal: {'Yes' if result.pareto_optimal else 'No'}")
    print(f"Conflicts: {len(result.conflicts)} initial → {len(result.residual_conflicts)} residual")

    if args.json_path:
        result.to_json(args.json_path)
        print(f"JSON report written to {args.json_path}")
    if args.html_path:
        result.to_html(args.html_path)
        print(f"HTML report written to {args.html_path}")
    if args.output:
        with open(args.output, "w") as fh:
            for x, y in result.final_points:
                fh.write(f"{x} {y}\n")
        print(f"Resolved points written to {args.output}")


if __name__ == "__main__":
    main()
