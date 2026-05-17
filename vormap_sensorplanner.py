#!/usr/bin/env python3
"""vormap_sensorplanner - Agentic Budget-Aware Next-N Sensor Placement Advisor.

Given a list of currently-measured 2D points and a budget ``n`` of new
sensors / samples you can afford to add, this module recommends **N
concrete (x, y) candidate placements** that maximise spatial coverage
gain while respecting a minimum separation distance from existing
measurements.

It is the *forward-looking, prescriptive* companion to
:mod:`vormap_coverage` (which surfaces one best site at a time) and
:mod:`vormap_sample` (which draws random samples *inside* a polygon).
Where those modules answer "where is one gap?" or "give me random points",
the sensor planner answers the operational question:

    "I have budget for N new sensors. Where should they go, in what
    order, and how confident am I that placing them actually closes the
    gaps?"

Verdict catalogue
-----------------

* ``CRITICAL_GAP``     - candidate sits in a clearly under-covered
  region (distance to nearest existing point is well above the 75th
  percentile of existing NN distances). Place these *first*.
* ``COVERAGE_EXPAND``  - candidate is near the bounding-box edge and
  expands the spatial envelope of the dataset.
* ``REFINE_LOCAL``     - candidate sits in a dense region where extra
  resolution is useful (e.g. hot-spot follow-up sampling). Usually only
  recommended under ``risk_appetite="aggressive"``.
* ``REDUNDANT``        - rarely emitted; included for completeness when
  the budget exceeds genuinely useful placements.

Each candidate carries:

* a 0-100 ``priority`` (higher = place sooner),
* a ``tier`` P0/P1/P2/P3,
* a list of structured ``reasons``,
* a ``score_breakdown`` with the contributing sub-scores,
* ``nearest_existing_distance`` and ``nearest_planned_distance``.

Aggregate output (``SensorPlan``):

* dataset *coverage grade* A-F based on expected coverage gain,
* per-tier playbook (P0 -> P3),
* mean nearest-neighbour distance *before* and *after* the simulated
  placements,
* a human-friendly ``summary`` block.

Renderers: ``text`` / ``markdown`` / ``json``.

CLI::

    python vormap_sensorplanner.py points.csv --budget 5
    python vormap_sensorplanner.py points.csv --budget 8 --format md
    python vormap_sensorplanner.py points.csv --budget 5 --risk cautious
    python vormap_sensorplanner.py points.csv --budget 5 --bounds 0,0,1000,1000 --seed 7

The module deliberately has **no required third-party dependencies** -
``numpy`` is used opportunistically when available, purely for speed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
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
    """Best-effort conversion of one point-ish thing to ``(x, y)``."""
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


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.hypot(dx, dy)


def _nearest_distance(p: Tuple[float, float], others: Sequence[Tuple[float, float]]) -> float:
    if not others:
        return float("inf")
    best = float("inf")
    for q in others:
        d = _dist(p, q)
        if d < best:
            best = d
    return best


def _auto_bounds(pts: Sequence[Tuple[float, float]],
                 pad_frac: float = 0.05) -> Tuple[float, float, float, float]:
    if not pts:
        # arbitrary unit square when caller supplied nothing
        return (0.0, 0.0, 1.0, 1.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, s, e, n = min(xs), min(ys), max(xs), max(ys)
    dx = max(e - w, 1e-9)
    dy = max(n - s, 1e-9)
    pad_x = dx * pad_frac
    pad_y = dy * pad_frac
    return (w - pad_x, s - pad_y, e + pad_x, n + pad_y)


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


def _nn_distances(pts: Sequence[Tuple[float, float]]) -> List[float]:
    """Nearest-neighbour distance from each point to its closest neighbour."""
    out: List[float] = []
    if len(pts) < 2:
        return out
    for i, p in enumerate(pts):
        best = float("inf")
        for j, q in enumerate(pts):
            if i == j:
                continue
            d = _dist(p, q)
            if d < best:
                best = d
        out.append(best)
    return out


# ---------------------------------------------------------------------------
# Halton-like quasi-random sequence
# ---------------------------------------------------------------------------


def _van_der_corput(n: int, base: int) -> float:
    q = 0.0
    bk = 1.0 / base
    while n > 0:
        q += (n % base) * bk
        n //= base
        bk /= base
    return q


def _halton_2d(count: int, skip: int = 13) -> List[Tuple[float, float]]:
    pts = []
    for i in range(skip, skip + count):
        pts.append((_van_der_corput(i, 2), _van_der_corput(i, 3)))
    return pts


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SensorCandidate:
    x: float
    y: float
    verdict: str
    priority: float
    tier: str
    reasons: List[dict] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    nearest_existing_distance: float = 0.0
    nearest_planned_distance: Optional[float] = None

    def as_dict(self) -> dict:
        d = asdict(self)
        # round floats for sane JSON output
        d["x"] = round(self.x, 6)
        d["y"] = round(self.y, 6)
        d["priority"] = round(self.priority, 2)
        d["nearest_existing_distance"] = round(self.nearest_existing_distance, 4)
        if self.nearest_planned_distance is not None:
            d["nearest_planned_distance"] = round(self.nearest_planned_distance, 4)
        d["score_breakdown"] = {
            k: round(float(v), 2) for k, v in self.score_breakdown.items()
        }
        return d


@dataclass
class SensorPlan:
    points_in: int
    requested_budget: int
    placed: int
    bounds: Tuple[float, float, float, float]
    risk_appetite: str
    min_separation: float
    expected_coverage_gain: float
    mean_nn_before: float
    mean_nn_after: float
    coverage_grade: str
    candidates: List[SensorCandidate] = field(default_factory=list)
    playbook: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "points_in": self.points_in,
            "requested_budget": self.requested_budget,
            "placed": self.placed,
            "bounds": [round(v, 4) for v in self.bounds],
            "risk_appetite": self.risk_appetite,
            "min_separation": round(self.min_separation, 4),
            "expected_coverage_gain": round(self.expected_coverage_gain, 2),
            "mean_nn_before": round(self.mean_nn_before, 4),
            "mean_nn_after": round(self.mean_nn_after, 4),
            "coverage_grade": self.coverage_grade,
            "candidates": [c.as_dict() for c in self.candidates],
            "playbook": self.playbook,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Core planning logic
# ---------------------------------------------------------------------------


_RISK_MODIFIERS = {
    "cautious":   {"gap_w": 1.15, "boundary_w": 1.15, "density_w": 1.00, "offset": +2.0},
    "balanced":   {"gap_w": 1.00, "boundary_w": 1.00, "density_w": 1.00, "offset":  0.0},
    "aggressive": {"gap_w": 0.85, "boundary_w": 0.95, "density_w": 1.20, "offset": -2.0},
}


def _coverage_grade(gain: float) -> str:
    if gain >= 85: return "A"
    if gain >= 70: return "B"
    if gain >= 55: return "C"
    if gain >= 40: return "D"
    return "F"


def _tier_for(priority: float, verdict: str) -> str:
    if verdict == "CRITICAL_GAP" and priority >= 80:
        return "P0"
    if priority >= 60:
        return "P1"
    if priority >= 35:
        return "P2"
    return "P3"


def _build_candidate_pool(bounds: Tuple[float, float, float, float],
                          target_count: int,
                          rng: random.Random) -> List[Tuple[float, float]]:
    w, s, e, n = bounds
    dx = e - w
    dy = n - s
    # Halton base sequence skipped by a rng-derived offset for "seed" honouring
    skip = 13 + (rng.randint(0, 997) if rng else 0)
    halton = _halton_2d(target_count, skip=skip)
    pool = [(w + hx * dx, s + hy * dy) for hx, hy in halton]
    # Boundary helpers: 4 corners + 4 edge midpoints
    cx = (w + e) / 2.0
    cy = (s + n) / 2.0
    pool.extend([
        (w, s), (e, s), (w, n), (e, n),
        (cx, s), (cx, n), (w, cy), (e, cy),
    ])
    return pool


def _score_candidate(cand: Tuple[float, float],
                     existing: Sequence[Tuple[float, float]],
                     planned: Sequence[Tuple[float, float]],
                     q50: float,
                     q75: float,
                     bounds: Tuple[float, float, float, float],
                     min_separation: float,
                     n_existing: int,
                     risk_appetite: str) -> Tuple[float, str, List[dict], dict, float, Optional[float]]:
    w, s, e, n = bounds
    d_existing = _nearest_distance(cand, existing) if existing else float("inf")
    d_planned = _nearest_distance(cand, planned) if planned else None
    d_combined = min(d_existing, d_planned) if d_planned is not None else d_existing

    # Sub-scores ------------------------------------------------------
    if math.isinf(d_existing) or q75 <= 0:
        gap_score = 100.0
    else:
        gap_score = min(100.0, 100.0 * d_combined / q75)

    shortest_side = max(min(e - w, n - s), 1e-9)
    edge_d = min(cand[0] - w, e - cand[0], cand[1] - s, n - cand[1])
    boundary_score = max(0.0, 100.0 * (1.0 - edge_d / (0.25 * shortest_side)))
    boundary_score = min(100.0, boundary_score)

    if existing and q50 > 0:
        nearby = sum(1 for p in existing if _dist(cand, p) <= q50)
        density_score = min(100.0, 100.0 * nearby / max(1.0, 0.10 * n_existing))
    else:
        density_score = 0.0

    if min_separation > 0:
        separation_penalty = max(0.0, 100.0 * (1.0 - d_combined / min_separation))
    else:
        separation_penalty = 0.0

    mods = _RISK_MODIFIERS.get(risk_appetite, _RISK_MODIFIERS["balanced"])
    priority = (
        0.55 * gap_score * mods["gap_w"]
        + 0.20 * boundary_score * mods["boundary_w"]
        - 0.15 * density_score * mods["density_w"]
        - 0.10 * separation_penalty
        + mods["offset"]
    )
    priority = max(0.0, min(100.0, priority))

    # Verdict ---------------------------------------------------------
    if (not math.isinf(d_existing)
            and q75 > 0
            and d_combined > q75 * 1.5
            and gap_score >= 70):
        verdict = "CRITICAL_GAP"
    elif math.isinf(d_existing) and gap_score >= 70:
        verdict = "CRITICAL_GAP"
    elif boundary_score >= 60:
        verdict = "COVERAGE_EXPAND"
    elif density_score >= 40:
        verdict = "REFINE_LOCAL"
    else:
        verdict = "REDUNDANT"

    # Reasons ---------------------------------------------------------
    reasons: List[dict] = []
    if gap_score >= 70:
        reasons.append({"code": "LARGE_GAP_FROM_EXISTING",
                        "message": f"distance to nearest existing point is {d_existing:.2f}"})
    if boundary_score >= 60:
        reasons.append({"code": "NEAR_BBOX_EDGE",
                        "message": f"within {edge_d:.2f} of the search bounding box"})
    if density_score >= 40:
        reasons.append({"code": "HIGH_LOCAL_DENSITY",
                        "message": f"{int(density_score)}% local density indicator (refines hotspot)"})
    if min_separation > 0 and d_combined >= min_separation:
        reasons.append({"code": "MEETS_MIN_SEPARATION",
                        "message": f"separation {d_combined:.2f} >= min {min_separation:.2f}"})
    if separation_penalty > 0:
        reasons.append({"code": "BELOW_MIN_SEPARATION",
                        "message": f"separation {d_combined:.2f} < min {min_separation:.2f}"})

    breakdown = {
        "gap_score": gap_score,
        "boundary_score": boundary_score,
        "density_score": density_score,
        "separation_penalty": separation_penalty,
        "risk_modifier": mods["offset"],
    }

    return priority, verdict, reasons, breakdown, d_existing, d_planned


def plan_sensors(points: Iterable[Any],
                 n: int,
                 bounds: Optional[Tuple[float, float, float, float]] = None,
                 risk_appetite: str = "balanced",
                 min_separation: Optional[float] = None,
                 seed: Optional[int] = None) -> SensorPlan:
    """Recommend ``n`` new sensor placements via greedy farthest-point-first.

    See module docstring for the full algorithm description.
    """
    if n is None or n < 0:
        raise ValueError("n must be a non-negative integer")
    risk_appetite = (risk_appetite or "balanced").lower()
    if risk_appetite not in _RISK_MODIFIERS:
        raise ValueError(f"unknown risk_appetite={risk_appetite!r}")

    existing = _normalize_points(points)
    bbox = tuple(bounds) if bounds is not None else _auto_bounds(existing)
    if len(bbox) != 4:
        raise ValueError("bounds must be (west, south, east, north)")
    w, s, e, nrt = bbox
    if not (e > w and nrt > s):
        raise ValueError(f"bounds must be non-degenerate, got {bbox}")

    nn_existing = _nn_distances(existing)
    q25 = _percentile(nn_existing, 25) if nn_existing else 0.0
    q50 = _percentile(nn_existing, 50) if nn_existing else max(e - w, nrt - s) * 0.10
    q75 = _percentile(nn_existing, 75) if nn_existing else max(e - w, nrt - s) * 0.25

    if min_separation is None:
        if nn_existing:
            min_separation = max(q25, 1e-9)
        else:
            min_separation = max(e - w, nrt - s) * 0.05

    rng = random.Random(seed if seed is not None else 0xC0FFEE)
    pool_size = max(64, 10 * max(n, 1))
    pool = _build_candidate_pool(bbox, pool_size, rng)
    # Deterministic shuffle so ties break reproducibly per seed
    rng.shuffle(pool)

    planned: List[Tuple[float, float]] = []
    selected: List[SensorCandidate] = []
    n_existing = len(existing)

    for _ in range(n):
        best = None
        best_priority = -1.0
        best_payload = None
        for cand in pool:
            d_existing = _nearest_distance(cand, existing) if existing else float("inf")
            d_planned = _nearest_distance(cand, planned) if planned else float("inf")
            d_combined = min(d_existing, d_planned)
            # Hard floor: never pick within half of min_separation of anything
            if d_combined < 0.5 * min_separation:
                continue
            priority, verdict, reasons, breakdown, de, dp = _score_candidate(
                cand, existing, planned, q50, q75, bbox,
                min_separation, max(n_existing, 1), risk_appetite,
            )
            if priority > best_priority:
                best_priority = priority
                best = cand
                best_payload = (verdict, reasons, breakdown, de, dp)
        if best is None:
            break
        verdict, reasons, breakdown, de, dp = best_payload
        cand_obj = SensorCandidate(
            x=best[0], y=best[1],
            verdict=verdict,
            priority=best_priority,
            tier=_tier_for(best_priority, verdict),
            reasons=reasons,
            score_breakdown=breakdown,
            nearest_existing_distance=de,
            nearest_planned_distance=None if (dp is None or math.isinf(dp)) else dp,
        )
        selected.append(cand_obj)
        planned.append(best)
        # Remove that point from the pool so we don't repick the same coord
        pool = [p for p in pool if p != best]

    # Aggregate metrics ----------------------------------------------
    if selected:
        gain = sum(c.score_breakdown["gap_score"] for c in selected) / len(selected)
    else:
        gain = 0.0
    grade = _coverage_grade(gain)

    if nn_existing:
        mean_before = statistics.fmean(nn_existing)
    else:
        mean_before = 0.0

    combined = list(existing) + list(planned)
    nn_after = _nn_distances(combined)
    mean_after = statistics.fmean(nn_after) if nn_after else mean_before

    playbook: dict = {"P0": [], "P1": [], "P2": [], "P3": []}
    for i, c in enumerate(selected, start=1):
        playbook[c.tier].append({
            "order": i,
            "x": round(c.x, 4),
            "y": round(c.y, 4),
            "verdict": c.verdict,
            "priority": round(c.priority, 1),
        })

    by_verdict = {"CRITICAL_GAP": 0, "COVERAGE_EXPAND": 0,
                  "REFINE_LOCAL": 0, "REDUNDANT": 0}
    for c in selected:
        by_verdict[c.verdict] = by_verdict.get(c.verdict, 0) + 1

    summary = {
        "by_verdict": by_verdict,
        "by_tier": {k: len(v) for k, v in playbook.items()},
        "q25_existing_nn": round(q25, 4),
        "q50_existing_nn": round(q50, 4),
        "q75_existing_nn": round(q75, 4),
        "pool_size_considered": pool_size + 8,
        "headline": _headline(by_verdict, len(selected), grade),
    }

    return SensorPlan(
        points_in=len(existing),
        requested_budget=int(n),
        placed=len(selected),
        bounds=bbox,
        risk_appetite=risk_appetite,
        min_separation=float(min_separation),
        expected_coverage_gain=gain,
        mean_nn_before=mean_before,
        mean_nn_after=mean_after,
        coverage_grade=grade,
        candidates=selected,
        playbook=playbook,
        summary=summary,
    )


def _headline(by_verdict: dict, placed: int, grade: str) -> str:
    if placed == 0:
        return "No candidates placed (check bounds and min_separation)."
    crit = by_verdict.get("CRITICAL_GAP", 0)
    if crit >= max(1, placed // 2):
        return f"Coverage grade {grade}: {crit}/{placed} placements close critical gaps."
    if grade in ("A", "B"):
        return f"Coverage grade {grade}: plan substantially improves spatial coverage."
    if grade in ("C",):
        return f"Coverage grade {grade}: plan delivers moderate coverage gains."
    return f"Coverage grade {grade}: limited coverage gain - consider raising budget."


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def format_plan(plan: SensorPlan, fmt: str = "text") -> str:
    fmt = (fmt or "text").lower()
    if fmt == "json":
        return json.dumps(plan.as_dict(), indent=2, sort_keys=True)
    if fmt == "markdown" or fmt == "md":
        return _render_markdown(plan)
    return _render_text(plan)


def _render_text(plan: SensorPlan) -> str:
    lines: List[str] = []
    lines.append("VoronoiMap Sensor Placement Plan")
    lines.append("=" * 36)
    lines.append(f"Points in     : {plan.points_in}")
    lines.append(f"Budget        : {plan.requested_budget}")
    lines.append(f"Placed        : {plan.placed}")
    lines.append(f"Bounds        : W={plan.bounds[0]:.3f} S={plan.bounds[1]:.3f} "
                 f"E={plan.bounds[2]:.3f} N={plan.bounds[3]:.3f}")
    lines.append(f"Risk appetite : {plan.risk_appetite}")
    lines.append(f"Min separation: {plan.min_separation:.3f}")
    lines.append(f"Coverage gain : {plan.expected_coverage_gain:.1f}  (grade {plan.coverage_grade})")
    lines.append(f"Mean NN       : before={plan.mean_nn_before:.3f}  after={plan.mean_nn_after:.3f}")
    lines.append("")
    lines.append(plan.summary.get("headline", ""))
    lines.append("")
    lines.append("Ordered placements:")
    for i, c in enumerate(plan.candidates, start=1):
        lines.append(f"  {i:>2}. [{c.tier}] {c.verdict:<16} "
                     f"({c.x:8.3f}, {c.y:8.3f})  pri={c.priority:5.1f}  "
                     f"nn_exist={c.nearest_existing_distance:.3f}")
        for r in c.reasons[:3]:
            lines.append(f"        - {r['code']}: {r['message']}")
    lines.append("")
    lines.append("Tier counts:")
    for tier in ("P0", "P1", "P2", "P3"):
        lines.append(f"  {tier}: {len(plan.playbook.get(tier, []))}")
    return "\n".join(lines)


def _render_markdown(plan: SensorPlan) -> str:
    lines: List[str] = []
    lines.append("# VoronoiMap Sensor Placement Plan")
    lines.append("")
    lines.append(f"**Headline:** {plan.summary.get('headline','')}")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Points in | {plan.points_in} |")
    lines.append(f"| Budget | {plan.requested_budget} |")
    lines.append(f"| Placed | {plan.placed} |")
    lines.append(f"| Bounds (W,S,E,N) | {plan.bounds[0]:.3f}, {plan.bounds[1]:.3f}, "
                 f"{plan.bounds[2]:.3f}, {plan.bounds[3]:.3f} |")
    lines.append(f"| Risk appetite | {plan.risk_appetite} |")
    lines.append(f"| Min separation | {plan.min_separation:.3f} |")
    lines.append(f"| Coverage gain | {plan.expected_coverage_gain:.1f} |")
    lines.append(f"| Coverage grade | **{plan.coverage_grade}** |")
    lines.append(f"| Mean NN before / after | {plan.mean_nn_before:.3f} / {plan.mean_nn_after:.3f} |")
    lines.append("")
    lines.append("## Placements")
    lines.append("")
    lines.append("| # | Tier | Verdict | x | y | Priority | nn(existing) |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(plan.candidates, start=1):
        lines.append(f"| {i} | {c.tier} | {c.verdict} | {c.x:.3f} | {c.y:.3f} | "
                     f"{c.priority:.1f} | {c.nearest_existing_distance:.3f} |")
    lines.append("")
    lines.append("## Playbook")
    for tier in ("P0", "P1", "P2", "P3"):
        items = plan.playbook.get(tier, [])
        if not items:
            continue
        lines.append(f"### {tier}")
        for it in items:
            lines.append(f"- #{it['order']} {it['verdict']} at "
                         f"({it['x']:.3f}, {it['y']:.3f}) priority={it['priority']:.1f}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_points_from_csv(path: str) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            # take first 2 numeric columns
            nums = []
            for cell in row[:6]:
                try:
                    nums.append(float(cell))
                except (TypeError, ValueError):
                    pass
                if len(nums) == 2:
                    break
            if len(nums) == 2:
                out.append((nums[0], nums[1]))
    return out


def _parse_bounds(arg: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    if not arg:
        return None
    parts = [p.strip() for p in arg.split(",")]
    if len(parts) != 4:
        raise ValueError("--bounds must be 'W,S,E,N'")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Agentic budget-aware sensor placement advisor for VoronoiMap.",
    )
    p.add_argument("points", help="CSV file with x,y in first two numeric columns")
    p.add_argument("--budget", "-n", type=int, default=5,
                   help="Number of placements to recommend (default: 5)")
    p.add_argument("--bounds", default=None,
                   help="W,S,E,N (default: auto from points with 5%% padding)")
    p.add_argument("--risk", default="balanced",
                   choices=["cautious", "balanced", "aggressive"])
    p.add_argument("--min-separation", type=float, default=None,
                   help="Minimum required distance from existing points (default: q25 of NN distances)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--format", "-f", default="text",
                   choices=["text", "markdown", "md", "json"])
    args = p.parse_args(list(argv) if argv is not None else None)

    pts = _read_points_from_csv(args.points)
    plan = plan_sensors(
        pts, args.budget,
        bounds=_parse_bounds(args.bounds),
        risk_appetite=args.risk,
        min_separation=args.min_separation,
        seed=args.seed,
    )
    print(format_plan(plan, args.format))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
