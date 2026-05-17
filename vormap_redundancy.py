#!/usr/bin/env python3
"""vormap_redundancy - Agentic Sensor Retirement / Merge Advisor.

The natural complement to :mod:`vormap_sensorplanner` (which recommends
*new* placements) and :mod:`vormap_curator` (which strips *invalid*
points).  Where those modules add coverage or remove bad data, this
module answers an operational cost question:

    "I am paying to maintain N sensors.  Which ones are redundant?
    Which can I retire, merge, or relocate without losing spatial
    information?"

Verdict catalogue
-----------------

* ``KEEP``              - point covers a unique spatial area.  Do not
  touch.
* ``REDUNDANT_MERGE``   - point is within ``merge_radius`` of another;
  the lower-indexed point is the keeper, the higher-indexed point is
  marked for merging (``merge_into`` set).
* ``OVERSAMPLED``       - point sits in a dense cluster (local density
  > ~2x median).  Candidate for retirement to cut operational cost.
* ``RETIRE_LOW_VALUE``  - when ``values`` are supplied, point's reading
  is within ``value_epsilon`` of >=2 neighbours *and* the point is
  oversampled.  Highest-confidence retirement.
* ``RELOCATE``          - point is in a dense area but retiring it
  would open a meaningful gap.  Recommend moving it (use
  :mod:`vormap_sensorplanner` to pick a destination).

Each verdict carries a 0-100 ``priority`` (higher = retire / merge
sooner), a tier P0/P1/P2/P3, structured reasons, a
``score_breakdown`` with the contributing sub-scores,
``projected_cost_savings`` (when ``costs`` provided), and a
``merge_into`` index when applicable.

Renderers: ``text`` / ``markdown`` / ``json`` (byte-stable via
``sort_keys=True``).

CLI::

    python vormap_redundancy.py points.csv
    python vormap_redundancy.py points.csv --format md
    python vormap_redundancy.py points.csv --merge-radius 3.0 --risk aggressive
    python vormap_redundancy.py points.csv --costs-col cost --values-col reading
    python vormap_redundancy.py points.csv --apply cleaned.csv

The module is pure stdlib; numpy is used opportunistically when present
purely for speed but is never required.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, List, Optional, Sequence, Tuple

try:  # opportunistic only
    import numpy as _np  # noqa: F401
    _HAS_NUMPY = True
except Exception:  # pragma: no cover - environment-dependent
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def _coerce_point(p: Any) -> Optional[Tuple[float, float]]:
    if p is None:
        return None
    if isinstance(p, dict):
        x = p.get("x", p.get("lng", p.get("lon", p.get("longitude"))))
        y = p.get("y", p.get("lat", p.get("latitude")))
    elif isinstance(p, (tuple, list)) and len(p) >= 2:
        x, y = p[0], p[1]
    else:
        return None
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(fx) and math.isfinite(fy)):
        return None
    return (fx, fy)


def _normalize_points(points: Iterable[Any]) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    for p in points or []:
        c = _coerce_point(p)
        if c is not None:
            out.append(c)
    return out


def _normalize_floats(values: Optional[Iterable[Any]],
                      n: int,
                      default: float = 0.0) -> Optional[List[float]]:
    if values is None:
        return None
    out: List[float] = []
    for v in values:
        try:
            f = float(v)
            if not math.isfinite(f):
                f = default
        except (TypeError, ValueError):
            f = default
        out.append(f)
    # Pad/truncate to length n
    if len(out) < n:
        out.extend([default] * (n - len(out)))
    elif len(out) > n:
        out = out[:n]
    return out


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    sv = sorted(values)
    k = (len(sv) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sv[lo])
    frac = k - lo
    return float(sv[lo] * (1 - frac) + sv[hi] * frac)


def _nn_table(pts: Sequence[Tuple[float, float]]) -> List[Tuple[float, int]]:
    """For each point, (nearest_neighbor_distance, nearest_neighbor_index).

    For a single point, returns (inf, -1).
    """
    out: List[Tuple[float, int]] = []
    if len(pts) < 2:
        return [(float("inf"), -1) for _ in pts]
    for i, p in enumerate(pts):
        best = float("inf")
        best_j = -1
        for j, q in enumerate(pts):
            if i == j:
                continue
            d = _dist(p, q)
            if d < best:
                best = d
                best_j = j
        out.append((best, best_j))
    return out


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RedundancyVerdict:
    point_index: int
    x: float
    y: float
    verdict: str
    priority: float
    tier: str
    reasons: List[dict] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    nearest_neighbor_distance: float = 0.0
    nearest_neighbor_index: int = -1
    merge_into: Optional[int] = None
    projected_cost_savings: float = 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["x"] = round(self.x, 6)
        d["y"] = round(self.y, 6)
        d["priority"] = round(self.priority, 2)
        d["nearest_neighbor_distance"] = (
            None if math.isinf(self.nearest_neighbor_distance)
            else round(self.nearest_neighbor_distance, 4)
        )
        d["projected_cost_savings"] = round(self.projected_cost_savings, 4)
        d["score_breakdown"] = {
            k: round(float(v), 2) for k, v in self.score_breakdown.items()
        }
        return d


@dataclass
class RedundancyReport:
    points_in: int
    risk_appetite: str
    merge_radius: float
    value_epsilon: Optional[float]
    median_nn: float
    grade: str
    verdicts: List[RedundancyVerdict] = field(default_factory=list)
    playbook: List[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    insights: List[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "points_in": self.points_in,
            "risk_appetite": self.risk_appetite,
            "merge_radius": round(self.merge_radius, 6),
            "value_epsilon": (None if self.value_epsilon is None
                              else round(self.value_epsilon, 6)),
            "median_nn": round(self.median_nn, 6),
            "grade": self.grade,
            "verdicts": [v.as_dict() for v in self.verdicts],
            "playbook": self.playbook,
            "summary": self.summary,
            "insights": self.insights,
        }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


_RISK_MODIFIERS = {
    # offset: bumped into priority before clamp.  cautious shifts down (keep
    # more), aggressive shifts up (retire more).  cluster_pct controls how
    # aggressively OVERSAMPLED fires.
    # merge_frac multiplies median NN distance to get the default merge
    # radius.  Cautious is strict (only very-close duplicates), aggressive
    # is loose (treat near-neighbours as duplicates too).
    "cautious":   {"offset": -10.0, "cluster_pct": 75.0, "merge_frac": 0.05},
    "balanced":   {"offset":   0.0, "cluster_pct": 65.0, "merge_frac": 0.10},
    "aggressive": {"offset": +10.0, "cluster_pct": 55.0, "merge_frac": 0.20},
}


def _grade_for(redundancy_pct: float) -> str:
    if redundancy_pct < 5: return "A"
    if redundancy_pct < 15: return "B"
    if redundancy_pct < 30: return "C"
    if redundancy_pct < 50: return "D"
    return "F"


def _tier_for(priority: float) -> str:
    if priority >= 75: return "P0"
    if priority >= 55: return "P1"
    if priority >= 35: return "P2"
    return "P3"


def _reversibility_for(verdict: str) -> str:
    return {
        "REDUNDANT_MERGE": "medium",
        "RETIRE_LOW_VALUE": "low",
        "OVERSAMPLED": "medium",
        "RELOCATE": "high",
        "KEEP": "high",
    }.get(verdict, "medium")


# ---------------------------------------------------------------------------
# Core analyser
# ---------------------------------------------------------------------------


def analyse(points: Iterable[Any],
            costs: Optional[Iterable[Any]] = None,
            values: Optional[Iterable[Any]] = None,
            merge_radius: Optional[float] = None,
            value_epsilon: Optional[float] = None,
            risk_appetite: str = "balanced") -> RedundancyReport:
    """Classify each point's redundancy and produce a playbook.

    Parameters
    ----------
    points
        Iterable of points.  Each may be a ``(x, y)`` tuple, a ``[x, y]``
        list, or a dict with ``x``/``y`` (or lat/lng/lon) keys.
    costs
        Optional per-point operational cost.  Used for the
        ``projected_cost_savings`` field and the ``cost_modifier``
        sub-score.
    values
        Optional per-point reading value.  Used to detect
        ``RETIRE_LOW_VALUE`` cases where the reading duplicates >=2
        neighbours within ``value_epsilon``.
    merge_radius
        Distance below which two points are treated as duplicates.
        Default = ``risk_appetite``-adjusted percentile of NN distances
        (q8 cautious / q10 balanced / q15 aggressive).
    value_epsilon
        Tolerance for "same reading".  Defaults to 5% of the value range.
    risk_appetite
        ``"cautious"`` / ``"balanced"`` / ``"aggressive"`` - shifts the
        priority offset by -10 / 0 / +10 and tunes thresholds.
    """
    risk_appetite = (risk_appetite or "balanced").lower()
    if risk_appetite not in _RISK_MODIFIERS:
        raise ValueError(f"unknown risk_appetite={risk_appetite!r}")
    mods = _RISK_MODIFIERS[risk_appetite]

    pts = _normalize_points(points)
    n = len(pts)
    costs_list = _normalize_floats(costs, n, default=0.0) if costs is not None else None
    values_list = _normalize_floats(values, n, default=float("nan")) if values is not None else None

    nn = _nn_table(pts)
    nn_dists = [d for (d, _j) in nn if math.isfinite(d)]
    median_nn = statistics.median(nn_dists) if nn_dists else 0.0

    # merge_radius default: a small fraction of median NN distance, so
    # uniformly-spaced points do not merge with each other but truly
    # coincident points do.
    if merge_radius is None:
        if median_nn > 0:
            merge_radius = median_nn * mods["merge_frac"]
        else:
            merge_radius = 0.0
    merge_radius = max(0.0, float(merge_radius))

    # value_epsilon default
    if value_epsilon is None and values_list is not None:
        finite_vals = [v for v in values_list if math.isfinite(v)]
        if finite_vals:
            rng = max(finite_vals) - min(finite_vals)
            value_epsilon = max(rng * 0.05, 1e-9)
        else:
            value_epsilon = None

    # Local density: count points within q50 (median_nn) of each point.
    local_density: List[int] = [0] * n
    if median_nn > 0 and n >= 2:
        radius = median_nn  # close neighbours only
        for i in range(n):
            c = 0
            for j in range(n):
                if i == j:
                    continue
                if _dist(pts[i], pts[j]) <= radius:
                    c += 1
            local_density[i] = c
    median_density = statistics.median(local_density) if local_density else 0

    # Per-point cost share (only used as a modifier so it never dominates).
    if costs_list is not None:
        total_cost = sum(costs_list)
        max_cost = max(costs_list) if costs_list else 0.0
    else:
        total_cost = 0.0
        max_cost = 0.0

    verdicts: List[RedundancyVerdict] = []
    merged_into_used: set = set()

    # 1) First pass: detect merge pairs.  Lower-indexed = keeper.
    is_merge_target: List[Optional[int]] = [None] * n
    if merge_radius > 0 and n >= 2:
        for i in range(n):
            if is_merge_target[i] is not None:
                continue
            for j in range(i + 1, n):
                if is_merge_target[j] is not None:
                    continue
                if _dist(pts[i], pts[j]) <= merge_radius:
                    is_merge_target[j] = i
                    merged_into_used.add(i)
                    # don't break - point i may absorb multiple duplicates

    # 2) Per-point scoring + verdict
    for i in range(n):
        nn_d, nn_j = nn[i]
        density = local_density[i]
        # Sub-scores 0-100
        if median_nn > 0 and math.isfinite(nn_d):
            # closer than median NN => higher proximity score
            proximity_score = max(0.0, min(100.0, 100.0 * (1.0 - nn_d / max(median_nn * 2.0, 1e-9))))
        else:
            proximity_score = 0.0

        if median_density > 0:
            density_score = max(0.0, min(100.0, 100.0 * (density - median_density) / max(median_density, 1.0) * 50.0))
            # rescale so being 2x median => ~100
            density_score = max(0.0, min(100.0, 50.0 * density / max(median_density, 1.0) - 50.0))
        else:
            density_score = 0.0

        # value similarity: if any 2 neighbours within radius have similar value, score climbs
        value_similarity_score = 0.0
        value_match_count = 0
        if values_list is not None and value_epsilon is not None and math.isfinite(values_list[i]):
            for j in range(n):
                if j == i:
                    continue
                if _dist(pts[i], pts[j]) <= max(median_nn * 1.5, merge_radius):
                    if math.isfinite(values_list[j]) and abs(values_list[i] - values_list[j]) <= value_epsilon:
                        value_match_count += 1
            value_similarity_score = min(100.0, value_match_count * 35.0)

        # cost modifier: high-cost points are higher priority to retire
        if costs_list is not None and max_cost > 0:
            cost_modifier = 100.0 * costs_list[i] / max_cost
        else:
            cost_modifier = 0.0

        # coverage_loss_penalty: if retiring this point creates a large gap,
        # penalise priority.  Approximate "second nearest neighbour distance"
        # as the post-removal NN of any neighbour currently pointing at us.
        coverage_loss_penalty = 0.0
        if math.isfinite(nn_d) and median_nn > 0 and n >= 3:
            # Find the next-nearest distance of *neighbour* nn_j if we retire i.
            jp = pts[nn_j] if 0 <= nn_j < n else None
            if jp is not None:
                second_best = float("inf")
                for k in range(n):
                    if k == i or k == nn_j:
                        continue
                    d = _dist(jp, pts[k])
                    if d < second_best:
                        second_best = d
                if math.isfinite(second_best):
                    gap_ratio = second_best / max(median_nn, 1e-9)
                    # >2x median => big gap => big penalty
                    if gap_ratio > 1.5:
                        coverage_loss_penalty = min(60.0, 40.0 * (gap_ratio - 1.5))

        # Priority -- combine sub-scores
        priority = (
            0.30 * proximity_score
            + 0.30 * density_score
            + 0.20 * value_similarity_score
            + 0.20 * cost_modifier
            - 1.00 * coverage_loss_penalty
            + mods["offset"]
        )
        priority = max(0.0, min(100.0, priority))

        # Verdict ladder
        verdict = "KEEP"
        merge_into_val: Optional[int] = None
        reasons: List[dict] = []

        if is_merge_target[i] is not None:
            verdict = "REDUNDANT_MERGE"
            merge_into_val = int(is_merge_target[i])
            # Ensure REDUNDANT_MERGE always ranks high enough to act on
            priority = max(priority, 70.0 + mods["offset"] * 0.5)
            priority = max(0.0, min(100.0, priority))
            reasons.append({
                "code": "WITHIN_MERGE_RADIUS",
                "message": (f"distance to point {merge_into_val} is "
                            f"{_dist(pts[i], pts[merge_into_val]):.4f} <= merge_radius "
                            f"{merge_radius:.4f}"),
            })
        else:
            # Oversampled / retire_low_value / relocate detection.  Only when
            # density is meaningfully above median.
            cluster_threshold = max(2 * median_density, 2)
            is_oversampled = (median_density > 0 and density >= cluster_threshold)

            if is_oversampled:
                # Distinguish RELOCATE vs OVERSAMPLED vs RETIRE_LOW_VALUE.
                if value_match_count >= 2:
                    verdict = "RETIRE_LOW_VALUE"
                    reasons.append({
                        "code": "READING_DUPLICATES_NEIGHBORS",
                        "message": f"reading matches {value_match_count} neighbours within epsilon {value_epsilon:.4f}",
                    })
                elif coverage_loss_penalty >= 25.0:
                    verdict = "RELOCATE"
                    reasons.append({
                        "code": "RETIREMENT_OPENS_GAP",
                        "message": "retiring this point leaves a large local gap; relocate instead",
                    })
                else:
                    verdict = "OVERSAMPLED"
                    reasons.append({
                        "code": "DENSE_CLUSTER",
                        "message": f"local density {density} vs median {median_density}",
                    })

        if proximity_score >= 70 and verdict == "KEEP" and not is_oversampled_safe_unused():
            # noop - placeholder to keep static analysers calm
            pass

        # Always include the density / proximity reasons when meaningful
        if density >= max(2 * median_density, 2) and verdict not in ("REDUNDANT_MERGE",):
            already = any(r["code"] == "DENSE_CLUSTER" for r in reasons)
            if not already:
                reasons.append({
                    "code": "DENSE_CLUSTER",
                    "message": f"local density {density} vs median {median_density}",
                })
        if math.isfinite(nn_d) and median_nn > 0 and nn_d < 0.5 * median_nn and verdict == "KEEP":
            reasons.append({
                "code": "CLOSE_TO_NEIGHBOR",
                "message": f"nearest neighbour at {nn_d:.4f} (< 50% of median {median_nn:.4f})",
            })

        # Projected savings: only when we'd retire (not relocate, not keep).
        projected_savings = 0.0
        if costs_list is not None and verdict in ("REDUNDANT_MERGE", "OVERSAMPLED", "RETIRE_LOW_VALUE"):
            projected_savings = float(costs_list[i])

        breakdown = {
            "proximity_score": proximity_score,
            "density_score": density_score,
            "value_similarity_score": value_similarity_score,
            "coverage_loss_penalty": coverage_loss_penalty,
            "cost_modifier": cost_modifier,
            "risk_offset": mods["offset"],
        }

        verdicts.append(RedundancyVerdict(
            point_index=i,
            x=pts[i][0],
            y=pts[i][1],
            verdict=verdict,
            priority=priority,
            tier=_tier_for(priority),
            reasons=reasons,
            score_breakdown=breakdown,
            nearest_neighbor_distance=nn_d,
            nearest_neighbor_index=nn_j,
            merge_into=merge_into_val,
            projected_cost_savings=projected_savings,
        ))

    # Insights
    retire_count = sum(1 for v in verdicts
                       if v.verdict in ("REDUNDANT_MERGE", "OVERSAMPLED", "RETIRE_LOW_VALUE"))
    redundancy_pct = (100.0 * retire_count / n) if n else 0.0
    total_savings = sum(v.projected_cost_savings for v in verdicts)
    merge_pairs = [(v.merge_into, v.point_index) for v in verdicts if v.verdict == "REDUNDANT_MERGE"]
    oversampled_idx = [v.point_index for v in verdicts if v.verdict == "OVERSAMPLED"]
    relocate_idx = [v.point_index for v in verdicts if v.verdict == "RELOCATE"]
    retire_low_value_idx = [v.point_index for v in verdicts if v.verdict == "RETIRE_LOW_VALUE"]

    # Dense zone detection: clusters of >=3 oversampled points mutually within 3*merge_radius
    dense_zone_count = 0
    if merge_radius > 0 and len(oversampled_idx) >= 3:
        radius3 = merge_radius * 3.0
        visited = set()
        for i in oversampled_idx:
            if i in visited:
                continue
            cluster = [i]
            visited.add(i)
            for j in oversampled_idx:
                if j in visited:
                    continue
                if _dist(pts[i], pts[j]) <= radius3:
                    cluster.append(j)
                    visited.add(j)
            if len(cluster) >= 3:
                dense_zone_count += 1

    insights: List[dict] = []
    insights.append({
        "code": "REDUNDANCY_PCT",
        "value": round(redundancy_pct, 2),
        "message": f"{retire_count}/{n} points flagged for retire/merge ({redundancy_pct:.1f}%)",
    })
    if total_savings > 0:
        insights.append({
            "code": "PROJECTED_COST_SAVINGS_TOTAL",
            "value": round(total_savings, 4),
            "message": f"retiring flagged points cuts ${total_savings:.2f} of cost",
        })
    if dense_zone_count > 0:
        insights.append({
            "code": "DENSE_ZONE_COUNT",
            "value": dense_zone_count,
            "message": f"{dense_zone_count} dense zone(s) of >=3 oversampled points",
        })
    if merge_pairs:
        insights.append({
            "code": "TOP_REDUNDANCY_CLUSTER_COUNT",
            "value": len(merge_pairs),
            "message": f"{len(merge_pairs)} merge pair(s) detected at radius {merge_radius:.4f}",
        })

    # Playbook (P0-first, dedup by id)
    playbook: List[dict] = []
    if merge_pairs:
        playbook.append({
            "id": "MERGE_DUPLICATE_PAIRS",
            "priority": "P0",
            "label": "Merge duplicate point pairs into their keepers",
            "reason": f"{len(merge_pairs)} pair(s) within merge_radius {merge_radius:.4f}",
            "blast_radius": 2,
            "reversibility": "medium",
            "point_ids": [pair[1] for pair in merge_pairs],
            "suggested_value": len(merge_pairs),
        })
    if retire_low_value_idx:
        playbook.append({
            "id": "RETIRE_LOW_VALUE_BATCH",
            "priority": "P0",
            "label": "Retire oversampled points whose readings duplicate neighbours",
            "reason": f"{len(retire_low_value_idx)} point(s) flagged RETIRE_LOW_VALUE",
            "blast_radius": 2,
            "reversibility": "low",
            "point_ids": list(retire_low_value_idx),
            "suggested_value": round(sum(verdicts[i].projected_cost_savings for i in retire_low_value_idx), 4),
        })
    if oversampled_idx:
        os_savings = sum(verdicts[i].projected_cost_savings for i in oversampled_idx)
        playbook.append({
            "id": "REDUCE_OVERSAMPLING",
            "priority": "P1",
            "label": "Reduce oversampling in dense regions",
            "reason": f"{len(oversampled_idx)} oversampled point(s)",
            "blast_radius": 3,
            "reversibility": "medium",
            "point_ids": list(oversampled_idx),
            "suggested_value": round(os_savings, 4),
        })
    if relocate_idx:
        playbook.append({
            "id": "RELOCATE_TO_GAPS",
            "priority": "P1",
            "label": "Relocate dense-region sensors to coverage gaps (use vormap_sensorplanner)",
            "reason": f"{len(relocate_idx)} point(s) flagged RELOCATE",
            "blast_radius": 3,
            "reversibility": "high",
            "point_ids": list(relocate_idx),
            "suggested_value": len(relocate_idx),
        })
    if total_savings > 0:
        playbook.append({
            "id": "AUDIT_OPERATIONAL_COST",
            "priority": "P2",
            "label": "Audit operational cost and confirm retirement plan with ops",
            "reason": f"projected ${total_savings:.2f} savings if all flagged points retire",
            "blast_radius": 1,
            "reversibility": "high",
            "point_ids": [],
            "suggested_value": round(total_savings, 4),
        })

    # P0-first sort, stable, deduped by id
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    playbook = sorted(playbook, key=lambda a: priority_rank.get(a["priority"], 9))
    seen_ids: set = set()
    deduped: List[dict] = []
    for a in playbook:
        if a["id"] in seen_ids:
            continue
        seen_ids.add(a["id"])
        deduped.append(a)
    playbook = deduped

    grade = _grade_for(redundancy_pct)

    summary = {
        "points_in": n,
        "keep_count": sum(1 for v in verdicts if v.verdict == "KEEP"),
        "merge_count": len(merge_pairs),
        "oversampled_count": len(oversampled_idx),
        "retire_low_value_count": len(retire_low_value_idx),
        "relocate_count": len(relocate_idx),
        "redundancy_pct": round(redundancy_pct, 2),
        "projected_cost_savings_total": round(total_savings, 4),
        "median_nn": round(median_nn, 6),
        "merge_radius": round(merge_radius, 6),
        "grade": grade,
        "headline": (
            f"Redundancy {redundancy_pct:.1f}% ({retire_count}/{n}), "
            f"savings ${total_savings:.2f}, grade {grade}"
        ),
    }

    return RedundancyReport(
        points_in=n,
        risk_appetite=risk_appetite,
        merge_radius=merge_radius,
        value_epsilon=value_epsilon,
        median_nn=median_nn,
        grade=grade,
        verdicts=verdicts,
        playbook=playbook,
        summary=summary,
        insights=insights,
    )


def is_oversampled_safe_unused() -> bool:  # pragma: no cover - kept for symmetry
    return False


# ---------------------------------------------------------------------------
# Apply / simulate
# ---------------------------------------------------------------------------


def apply_plan(points: Iterable[Any],
               report: RedundancyReport,
               keep_merge_representatives: bool = True) -> List[Tuple[float, float]]:
    """Return a fresh point list with redundant points removed.

    * REDUNDANT_MERGE and RETIRE_LOW_VALUE and OVERSAMPLED points are dropped.
    * RELOCATE and KEEP points are kept (RELOCATE means "move, do not delete").
    * Keepers (points that absorbed merges) remain.
    """
    pts = _normalize_points(points)
    drop = set()
    for v in report.verdicts:
        if v.verdict in ("REDUNDANT_MERGE", "OVERSAMPLED", "RETIRE_LOW_VALUE"):
            drop.add(v.point_index)
    cleaned: List[Tuple[float, float]] = []
    for i, p in enumerate(pts):
        if i in drop:
            continue
        cleaned.append((p[0], p[1]))
    if not keep_merge_representatives:
        # Drop the keeper side of every merge pair too (rare opt-out)
        merge_keepers = {v.merge_into for v in report.verdicts
                         if v.merge_into is not None}
        cleaned = []
        for i, p in enumerate(pts):
            if i in drop or i in merge_keepers:
                continue
            cleaned.append((p[0], p[1]))
    return cleaned


def simulate(points: Iterable[Any], report: RedundancyReport) -> dict:
    """Materialise the plan and report before/after spacing + cost saved."""
    pts = _normalize_points(points)
    nn_before = _nn_table(pts)
    finite_before = [d for d, _ in nn_before if math.isfinite(d)]
    mean_before = statistics.mean(finite_before) if finite_before else 0.0

    after = apply_plan(pts, report)
    nn_after = _nn_table(after)
    finite_after = [d for d, _ in nn_after if math.isfinite(d)]
    mean_after = statistics.mean(finite_after) if finite_after else 0.0

    return {
        "before_count": len(pts),
        "after_count": len(after),
        "before_mean_nn": round(mean_before, 6),
        "after_mean_nn": round(mean_after, 6),
        "cost_saved": round(report.summary.get("projected_cost_savings_total", 0.0), 4),
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def format_text(report: RedundancyReport) -> str:
    lines: List[str] = []
    lines.append("vormap_redundancy - sensor retirement / merge advisor")
    lines.append("=" * 60)
    s = report.summary
    lines.append(f"Points in:     {s.get('points_in', 0)}")
    lines.append(f"Risk appetite: {report.risk_appetite}")
    lines.append(f"Merge radius:  {report.merge_radius:.4f}")
    lines.append(f"Median NN:     {report.median_nn:.4f}")
    lines.append(f"Grade:         {report.grade}")
    lines.append(f"Headline:      {s.get('headline', '')}")
    lines.append("")
    lines.append("Verdicts (top 20 by priority):")
    sorted_v = sorted(report.verdicts, key=lambda v: (-v.priority, v.point_index))
    for v in sorted_v[:20]:
        merge = f" -> merge_into={v.merge_into}" if v.merge_into is not None else ""
        savings = f", save=${v.projected_cost_savings:.2f}" if v.projected_cost_savings else ""
        lines.append(f"  [{v.tier}] #{v.point_index} ({v.x:.3f},{v.y:.3f}) "
                     f"{v.verdict} pri={v.priority:.1f}{merge}{savings}")
    lines.append("")
    lines.append(f"Playbook ({len(report.playbook)} action(s)):")
    for a in report.playbook:
        lines.append(f"  [{a['priority']}] {a['id']}: {a['label']} -- {a['reason']}")
    lines.append("")
    lines.append(f"Insights ({len(report.insights)}):")
    for ins in report.insights:
        lines.append(f"  - {ins['code']}: {ins['message']}")
    return "\n".join(lines)


def format_markdown(report: RedundancyReport) -> str:
    lines: List[str] = []
    lines.append("# vormap_redundancy")
    lines.append("")
    lines.append(f"**Grade:** {report.grade} &mdash; {report.summary.get('headline', '')}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    for k in ("points_in", "keep_count", "merge_count", "oversampled_count",
              "retire_low_value_count", "relocate_count", "redundancy_pct",
              "projected_cost_savings_total", "median_nn", "merge_radius"):
        lines.append(f"| {k} | {report.summary.get(k, '')} |")
    lines.append("")
    lines.append("## Verdicts (top 30 by priority)")
    lines.append("")
    lines.append("| # | Pt | (x, y) | Verdict | Pri | Tier | Merge | Save |")
    lines.append("|---|----|--------|---------|-----|------|-------|------|")
    sorted_v = sorted(report.verdicts, key=lambda v: (-v.priority, v.point_index))
    for n, v in enumerate(sorted_v[:30], 1):
        merge = "" if v.merge_into is None else str(v.merge_into)
        save = f"${v.projected_cost_savings:.2f}" if v.projected_cost_savings else ""
        lines.append(f"| {n} | {v.point_index} | ({v.x:.3f}, {v.y:.3f}) | "
                     f"{v.verdict} | {v.priority:.1f} | {v.tier} | {merge} | {save} |")
    lines.append("")
    lines.append("## Playbook")
    lines.append("")
    if not report.playbook:
        lines.append("_no actions_")
    else:
        lines.append("| Priority | Action | Reason | Blast | Reversibility |")
        lines.append("|----------|--------|--------|-------|---------------|")
        for a in report.playbook:
            lines.append(f"| {a['priority']} | **{a['id']}** &mdash; {a['label']} | "
                         f"{a['reason']} | {a['blast_radius']} | {a['reversibility']} |")
    lines.append("")
    lines.append("## Insights")
    lines.append("")
    for ins in report.insights:
        lines.append(f"- **{ins['code']}**: {ins['message']}")
    return "\n".join(lines)


def format_json(report: RedundancyReport) -> str:
    return json.dumps(report.as_dict(), sort_keys=True, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_csv(path: str,
              x_col: str,
              y_col: str,
              costs_col: Optional[str],
              values_col: Optional[str]) -> Tuple[List[Tuple[float, float]],
                                                  Optional[List[float]],
                                                  Optional[List[float]]]:
    pts: List[Tuple[float, float]] = []
    costs: Optional[List[float]] = [] if costs_col else None
    values: Optional[List[float]] = [] if values_col else None
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError(f"{path}: no header row")
        for col, label in ((x_col, "x"), (y_col, "y")):
            if col not in reader.fieldnames:
                raise ValueError(f"{path}: missing required column {col!r}")
        if costs_col and costs_col not in reader.fieldnames:
            raise ValueError(f"{path}: --costs-col {costs_col!r} not in header")
        if values_col and values_col not in reader.fieldnames:
            raise ValueError(f"{path}: --values-col {values_col!r} not in header")
        for row in reader:
            try:
                x = float(row[x_col])
                y = float(row[y_col])
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            pts.append((x, y))
            if costs is not None:
                try:
                    costs.append(float(row.get(costs_col, "") or 0.0))
                except (TypeError, ValueError):
                    costs.append(0.0)
            if values is not None:
                try:
                    values.append(float(row.get(values_col, "") or "nan"))
                except (TypeError, ValueError):
                    values.append(float("nan"))
    return pts, costs, values


def _write_csv(path: str, pts: Sequence[Tuple[float, float]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["x", "y"])
        for p in pts:
            w.writerow([p[0], p[1]])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vormap_redundancy",
        description="Agentic sensor retirement / merge advisor.",
    )
    p.add_argument("path", help="CSV file with x,y columns (and optional cost/value)")
    p.add_argument("--x-col", default="x", help="x column name (default 'x')")
    p.add_argument("--y-col", default="y", help="y column name (default 'y')")
    p.add_argument("--costs-col", default=None, help="optional per-point cost column")
    p.add_argument("--values-col", default=None, help="optional per-point reading column")
    p.add_argument("--merge-radius", type=float, default=None,
                   help="distance below which two points are considered duplicates")
    p.add_argument("--value-epsilon", type=float, default=None,
                   help="reading tolerance for RETIRE_LOW_VALUE (auto if omitted)")
    p.add_argument("--risk", choices=["cautious", "balanced", "aggressive"],
                   default="balanced", help="risk appetite (default balanced)")
    p.add_argument("--format", choices=["text", "md", "markdown", "json"],
                   default="text", help="output format (default text)")
    p.add_argument("--output", default=None, help="write report to this file (default stdout)")
    p.add_argument("--apply", default=None, metavar="OUT.csv",
                   help="materialise the cleaned point list to this CSV")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    pts, costs, values = _read_csv(args.path, args.x_col, args.y_col,
                                   args.costs_col, args.values_col)
    report = analyse(
        pts,
        costs=costs,
        values=values,
        merge_radius=args.merge_radius,
        value_epsilon=args.value_epsilon,
        risk_appetite=args.risk,
    )
    fmt = args.format.lower()
    if fmt in ("md", "markdown"):
        out = format_markdown(report)
    elif fmt == "json":
        out = format_json(report)
    else:
        out = format_text(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        print(out)
    if args.apply:
        cleaned = apply_plan(pts, report)
        _write_csv(args.apply, cleaned)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
