#!/usr/bin/env python3
"""vormap_drift - Agentic Temporal Drift Advisor.

Given two snapshots of a spatial point set - a *baseline* and a
*current* - this module classifies how the dataset has changed over
time and emits an actionable playbook for the operator running the
underlying sensors / surveys / measurement campaign.

It is the *temporal* sibling to the other agentic advisors:

* :mod:`vormap_curator`        - clean a single snapshot.
* :mod:`vormap_sensorplanner`  - plan the next-N placements.
* :mod:`vormap_redundancy`     - retire over-sampled sensors.
* :mod:`vormap_brief`          - executive synthesis.
* :mod:`vormap_doctor`         - dataset health.

Where those answer "what is wrong now?" or "where should I place
next?", this advisor answers:

    "I just re-measured. What changed since last time, what does it
    mean, and what should I do today?"

Verdict catalogue
-----------------

* ``STABLE``               - baseline point matched to a current point
  within ``match_radius`` with no significant value drift. Priority P3.
* ``SHIFTED``              - matched but with a non-trivial displacement
  (> ``match_radius * 0.3``). Priority P2.
* ``VALUE_DRIFT``          - matched and the scalar value moved beyond
  ``value_drift_threshold``. P1, escalates to P0 if relative |delta|
  >= 1.0.
* ``DISAPPEARED``          - baseline point with no current match within
  ``match_radius * 2``. P1, escalates to P0 if the lost point sat in a
  dense baseline region (lost critical coverage).
* ``EMERGED``              - current point with no baseline match. P2,
  optionally P1 if redundant (inside a dense baseline area).
* ``DUPLICATE_EMERGENCE``  - current point with no baseline match but
  near another emerged current point (likely a duplicate new sensor).
  Priority P1.

Each ``DriftPoint`` carries:

* ``id``                  - ``b#i`` (baseline) or ``c#i`` (current),
* ``verdict``             - one of the labels above,
* ``priority``            - ``P0`` / ``P1`` / ``P2`` / ``P3``,
* ``priority_score``      - 0-100 (higher = act sooner),
* ``point``               - ``(x, y)``,
* optional ``value``, ``matched_id``, ``displacement``,
  ``value_delta``, ``relative_value_delta``,
* ``reasons``             - structured ``{code, message}`` rows.

Aggregate output (:class:`DriftReport`):

* ``stability_score``     - 0-100,
* ``grade``               - A-F,
* ``summary``             - one-line headline,
* ``playbook``            - P0-first deduped action list,
* ``insights``            - structured cross-cutting strings,
* ``metrics``             - rich count/distance summary.

Renderers: ``text`` / ``markdown`` / ``json``.

CLI::

    python vormap_drift.py baseline.csv current.csv
    python vormap_drift.py baseline.csv current.csv --format md
    python vormap_drift.py baseline.csv current.csv --risk cautious --apply cleaned.csv

The module has **no required third-party dependencies** - ``numpy`` is
used opportunistically when available, purely for speed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

try:  # opportunistic only
    import numpy as _np  # noqa: F401
    _HAS_NUMPY = True
except Exception:  # pragma: no cover - environment-dependent
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PointLike = Union[
    Tuple[float, float],
    Tuple[float, float, float],
    Sequence[float],
]


@dataclass
class DriftPoint:
    id: str
    verdict: str
    priority: str
    priority_score: float
    point: Tuple[float, float]
    value: Optional[float] = None
    matched_id: Optional[str] = None
    displacement: Optional[float] = None
    value_delta: Optional[float] = None
    relative_value_delta: Optional[float] = None
    reasons: List[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class DriftReport:
    baseline_count: int
    current_count: int
    match_radius: float
    value_drift_threshold: float
    risk_appetite: str
    generated_at: str
    stability_score: float
    grade: str
    summary: str
    points: List[DriftPoint]
    playbook: List[dict]
    insights: List[str]
    metrics: dict

    def as_dict(self) -> dict:
        return {
            "baseline_count": self.baseline_count,
            "current_count": self.current_count,
            "match_radius": self.match_radius,
            "value_drift_threshold": self.value_drift_threshold,
            "risk_appetite": self.risk_appetite,
            "generated_at": self.generated_at,
            "stability_score": self.stability_score,
            "grade": self.grade,
            "summary": self.summary,
            "points": [p.as_dict() for p in self.points],
            "playbook": list(self.playbook),
            "insights": list(self.insights),
            "metrics": dict(self.metrics),
        }


# ---------------------------------------------------------------------------
# Input normalisation
# ---------------------------------------------------------------------------


def _coerce(p: Any) -> Optional[Tuple[float, float, Optional[float]]]:
    if p is None:
        return None
    val: Optional[float] = None
    if isinstance(p, dict):
        x = p.get("x", p.get("lng", p.get("lon", p.get("longitude"))))
        y = p.get("y", p.get("lat", p.get("latitude")))
        v = p.get("value", p.get("v", p.get("reading")))
    elif isinstance(p, (tuple, list)) and len(p) >= 2:
        x, y = p[0], p[1]
        v = p[2] if len(p) >= 3 else None
    else:
        return None
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(fx) and math.isfinite(fy)):
        return None
    if v is not None:
        try:
            fv = float(v)
            if math.isfinite(fv):
                val = fv
        except (TypeError, ValueError):
            val = None
    return (fx, fy, val)


def _normalize(points: Iterable[Any]) -> List[Tuple[float, float, Optional[float]]]:
    out: List[Tuple[float, float, Optional[float]]] = []
    for p in points or []:
        c = _coerce(p)
        if c is not None:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _nn_distances(points: Sequence[Tuple[float, float]]) -> List[float]:
    n = len(points)
    if n < 2:
        return []
    out: List[float] = []
    for i in range(n):
        best = math.inf
        for j in range(n):
            if i == j:
                continue
            d = _dist(points[i], points[j])
            if d < best:
                best = d
        if math.isfinite(best):
            out.append(best)
    return out


def _quantile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = q * (len(sorted_vals) - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    frac = idx - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def _nearest_index(
    target: Tuple[float, float],
    pool: Sequence[Tuple[float, float]],
) -> Tuple[int, float]:
    best_i, best_d = -1, math.inf
    for i, p in enumerate(pool):
        d = _dist(target, p)
        if d < best_d:
            best_i, best_d = i, d
    return best_i, best_d


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _greedy_match(
    baseline_xy: Sequence[Tuple[float, float]],
    current_xy: Sequence[Tuple[float, float]],
    radius: float,
) -> Tuple[dict, dict]:
    """Greedy nearest-neighbour matching within ``radius``.

    Returns ``(b2c, c2b)`` index maps and the displacement at the matched
    pair (stored in b2c values as a tuple ``(c_idx, dist)``).
    """
    pairs: List[Tuple[float, int, int]] = []
    for bi, bp in enumerate(baseline_xy):
        for ci, cp in enumerate(current_xy):
            d = _dist(bp, cp)
            if d <= radius:
                pairs.append((d, bi, ci))
    pairs.sort()
    b2c: dict = {}
    c2b: dict = {}
    for d, bi, ci in pairs:
        if bi in b2c or ci in c2b:
            continue
        b2c[bi] = (ci, d)
        c2b[ci] = (bi, d)
    return b2c, c2b


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


_RISK_MULT = {"cautious": 1.10, "balanced": 1.0, "aggressive": 0.90}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _priority_bucket(score: float, verdict: str, rel_delta: Optional[float]) -> str:
    # Verdict-specific escalation rules take precedence over numeric score.
    if verdict == "VALUE_DRIFT" and rel_delta is not None and abs(rel_delta) >= 1.0:
        return "P0"
    if verdict == "DISAPPEARED" and score >= 70:
        return "P0"
    if verdict in ("VALUE_DRIFT", "DISAPPEARED", "DUPLICATE_EMERGENCE"):
        return "P1"
    if verdict == "SHIFTED":
        return "P2"
    if verdict == "EMERGED":
        return "P2" if score < 50 else "P1"
    return "P3"  # STABLE


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def detect_drift(
    baseline: Iterable[PointLike],
    current: Iterable[PointLike],
    *,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    match_radius: Optional[float] = None,
    value_drift_threshold: float = 0.25,
    risk_appetite: str = "balanced",
    now: Optional[Callable[[], datetime]] = None,
) -> DriftReport:
    """Compare ``baseline`` to ``current`` and classify the drift."""
    if risk_appetite not in _RISK_MULT:
        raise ValueError(f"risk_appetite must be one of {sorted(_RISK_MULT)}")
    if value_drift_threshold < 0:
        raise ValueError("value_drift_threshold must be >= 0")

    b_full = _normalize(baseline)
    c_full = _normalize(current)
    b_xy = [(p[0], p[1]) for p in b_full]
    c_xy = [(p[0], p[1]) for p in c_full]
    b_val = [p[2] for p in b_full]
    c_val = [p[2] for p in c_full]

    # Auto match_radius.
    if match_radius is None:
        nn = _nn_distances(b_xy) if len(b_xy) >= 2 else []
        if nn:
            nn_sorted = sorted(nn)
            median_nn = _quantile(nn_sorted, 0.5)
            match_radius = max(1e-9, median_nn * 0.5)
        else:
            # fall back: half the bounding diagonal / 20
            if b_xy or c_xy:
                pts = b_xy + c_xy
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
                match_radius = max(1e-9, diag / 40.0) if diag > 0 else 1.0
            else:
                match_radius = 1.0
    match_radius = float(match_radius)

    # Auto bounds.
    if bounds is None:
        pts = b_xy + c_xy
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            pad_x = max(1e-9, (max(xs) - min(xs)) * 0.05)
            pad_y = max(1e-9, (max(ys) - min(ys)) * 0.05)
            bounds = (min(xs) - pad_x, min(ys) - pad_y,
                      max(xs) + pad_x, max(ys) + pad_y)
        else:
            bounds = (0.0, 0.0, 1.0, 1.0)

    # Matching.
    b2c, c2b = _greedy_match(b_xy, c_xy, match_radius)

    # Dense region thresholds (baseline NN q25).
    base_nn = _nn_distances(b_xy)
    base_nn_sorted = sorted(base_nn)
    dense_threshold = _quantile(base_nn_sorted, 0.25) if base_nn_sorted else 0.0

    risk_mult = _RISK_MULT[risk_appetite]

    points: List[DriftPoint] = []
    displacements: List[float] = []
    value_deltas: List[float] = []

    # Iterate baseline first (matched / disappeared / value-drift / stable / shifted).
    for bi, bp in enumerate(b_xy):
        bv = b_val[bi]
        reasons: List[dict] = []
        if bi in b2c:
            ci, d = b2c[bi]
            cp = c_xy[ci]
            cv = c_val[ci]
            displacements.append(d)
            # Value drift?
            val_delta = None
            rel_delta = None
            if bv is not None and cv is not None:
                val_delta = cv - bv
                value_deltas.append(abs(val_delta))
                denom = max(abs(bv), 1e-9)
                rel_delta = val_delta / denom
            is_value_drift = (
                rel_delta is not None
                and abs(rel_delta) >= value_drift_threshold
            )
            if is_value_drift:
                verdict = "VALUE_DRIFT"
                score = 50.0 + 30.0 * abs(rel_delta)
                code = "VALUE_INCREASED" if rel_delta > 0 else "VALUE_DECREASED"
                reasons.append({
                    "code": f"{code}_{int(round(abs(rel_delta) * 100))}PCT",
                    "message": (
                        f"Value moved from {bv:.4g} to {cv:.4g} "
                        f"({rel_delta * 100:+.1f}%)"
                    ),
                })
                if abs(rel_delta) >= 1.0:
                    reasons.append({
                        "code": "MAJOR_VALUE_SWING",
                        "message": "Relative change >= 100% - investigate sensor calibration.",
                    })
            elif d > match_radius * 0.3:
                verdict = "SHIFTED"
                score = 30.0 + 50.0 * (d / max(match_radius, 1e-9))
                if d > match_radius:
                    score = 60.0
                reasons.append({
                    "code": "LARGE_DISPLACEMENT",
                    "message": (
                        f"Matched point moved {d:.3f} "
                        f"(>{match_radius * 0.3:.3f} threshold)"
                    ),
                })
            else:
                verdict = "STABLE"
                score = 5.0
                reasons.append({
                    "code": "MATCHED_NEAR",
                    "message": f"Matched within {d:.3f} (< {match_radius * 0.3:.3f}).",
                })
            score = _clamp(score * risk_mult)
            priority = _priority_bucket(score, verdict, rel_delta)
            points.append(DriftPoint(
                id=f"b#{bi}",
                verdict=verdict,
                priority=priority,
                priority_score=round(score, 1),
                point=(bp[0], bp[1]),
                value=bv,
                matched_id=f"c#{ci}",
                displacement=round(d, 4),
                value_delta=None if val_delta is None else round(val_delta, 4),
                relative_value_delta=None if rel_delta is None else round(rel_delta, 4),
                reasons=reasons,
            ))
        else:
            # DISAPPEARED if no match within match_radius * 2 either.
            best_i, best_d = _nearest_index(bp, c_xy) if c_xy else (-1, math.inf)
            if best_d <= match_radius * 2 and best_i >= 0 and best_i in c2b:
                # The nearest current point is taken by someone else within the
                # wider radius - we still call this disappeared, but flag it.
                pass
            verdict = "DISAPPEARED"
            in_dense = (
                len(base_nn_sorted) > 0
                and bi < len(base_nn)
                and base_nn[bi] <= dense_threshold
            )
            score = 60.0 + (15.0 if in_dense else 0.0)
            reasons.append({
                "code": "NO_MATCH_WITHIN_RADIUS",
                "message": (
                    f"No current point within {match_radius:.3f} of "
                    f"baseline ({bp[0]:.3f},{bp[1]:.3f})."
                ),
            })
            if in_dense:
                reasons.append({
                    "code": "LOST_DENSE_AREA",
                    "message": "Removed point sat in a densely-sampled region.",
                })
            if best_d < math.inf:
                reasons.append({
                    "code": "NEAREST_CURRENT",
                    "message": f"Nearest current point is {best_d:.3f} away.",
                })
            score = _clamp(score * risk_mult)
            priority = _priority_bucket(score, verdict, None)
            points.append(DriftPoint(
                id=f"b#{bi}",
                verdict=verdict,
                priority=priority,
                priority_score=round(score, 1),
                point=(bp[0], bp[1]),
                value=bv,
                matched_id=None,
                displacement=None,
                value_delta=None,
                relative_value_delta=None,
                reasons=reasons,
            ))

    # Identify emerged current points and duplicate-emergences.
    emerged_indices = [ci for ci in range(len(c_xy)) if ci not in c2b]
    # Detect duplicates: any emerged current point within match_radius * 0.5
    # of another emerged current point.
    dup_radius = match_radius * 0.5
    duplicate_set = set()
    for i_idx, i in enumerate(emerged_indices):
        for j in emerged_indices[i_idx + 1:]:
            if _dist(c_xy[i], c_xy[j]) <= dup_radius:
                # mark the *later* index as duplicate (keep the first)
                duplicate_set.add(j)

    for ci in emerged_indices:
        cp = c_xy[ci]
        cv = c_val[ci]
        reasons: List[dict] = []
        # Distance from nearest baseline.
        nb_i, nb_d = _nearest_index(cp, b_xy) if b_xy else (-1, math.inf)
        in_dense_baseline = (
            b_xy
            and dense_threshold > 0
            and nb_d <= dense_threshold * 1.5
        )

        if ci in duplicate_set:
            verdict = "DUPLICATE_EMERGENCE"
            score = 70.0
            reasons.append({
                "code": "EMERGED_REDUNDANT_TO_OTHER_NEW",
                "message": (
                    f"New point near another new point "
                    f"(<= {dup_radius:.3f})."
                ),
            })
        else:
            verdict = "EMERGED"
            score = 35.0 + (15.0 if in_dense_baseline else 0.0)
            if in_dense_baseline:
                reasons.append({
                    "code": "EMERGED_IN_DENSE_AREA",
                    "message": (
                        "New point sits inside an already-dense baseline "
                        "region - check for redundancy."
                    ),
                })
            else:
                reasons.append({
                    "code": "NEW_COVERAGE_GAP_FILLED",
                    "message": (
                        f"New point extends coverage "
                        f"(nearest baseline {nb_d:.3f} away)."
                    ),
                })
        score = _clamp(score * risk_mult)
        priority = _priority_bucket(score, verdict, None)
        points.append(DriftPoint(
            id=f"c#{ci}",
            verdict=verdict,
            priority=priority,
            priority_score=round(score, 1),
            point=(cp[0], cp[1]),
            value=cv,
            matched_id=None,
            displacement=None,
            value_delta=None,
            relative_value_delta=None,
            reasons=reasons,
        ))

    # ---------- Aggregate ----------
    counts = {"STABLE": 0, "SHIFTED": 0, "VALUE_DRIFT": 0,
              "DISAPPEARED": 0, "EMERGED": 0, "DUPLICATE_EMERGENCE": 0}
    for p in points:
        counts[p.verdict] = counts.get(p.verdict, 0) + 1

    non_stable_scores = [p.priority_score for p in points if p.verdict != "STABLE"]
    if non_stable_scores:
        mean_action = statistics.fmean(non_stable_scores)
        stability_score = max(0.0, 100.0 - mean_action)
    else:
        stability_score = 100.0
    grade = _grade(stability_score)

    metrics = {
        "baseline_count": len(b_xy),
        "current_count": len(c_xy),
        "matched_count": len(b2c),
        "stable_count": counts["STABLE"],
        "shifted_count": counts["SHIFTED"],
        "value_drift_count": counts["VALUE_DRIFT"],
        "disappeared_count": counts["DISAPPEARED"],
        "emerged_count": counts["EMERGED"],
        "duplicate_emergence_count": counts["DUPLICATE_EMERGENCE"],
        "mean_displacement": round(statistics.fmean(displacements), 4) if displacements else 0.0,
        "max_displacement": round(max(displacements), 4) if displacements else 0.0,
        "mean_abs_value_delta": round(statistics.fmean(value_deltas), 4) if value_deltas else 0.0,
    }

    playbook = _build_playbook(points, counts, risk_appetite, grade)
    insights = _build_insights(points, counts, metrics, len(b_xy))

    headline = (
        f"Drift report: {len(b_xy)} baseline -> {len(c_xy)} current "
        f"({counts['STABLE']} STABLE, {counts['SHIFTED']} SHIFTED, "
        f"{counts['VALUE_DRIFT']} VALUE_DRIFT, "
        f"{counts['DISAPPEARED']} DISAPPEARED, "
        f"{counts['EMERGED']} EMERGED; grade {grade})"
    )

    ts_fn = now or (lambda: datetime.now(timezone.utc))
    try:
        generated_at = ts_fn().isoformat()
    except Exception:
        generated_at = datetime.now(timezone.utc).isoformat()

    return DriftReport(
        baseline_count=len(b_xy),
        current_count=len(c_xy),
        match_radius=round(match_radius, 6),
        value_drift_threshold=float(value_drift_threshold),
        risk_appetite=risk_appetite,
        generated_at=generated_at,
        stability_score=round(stability_score, 1),
        grade=grade,
        summary=headline,
        points=points,
        playbook=playbook,
        insights=insights,
        metrics=metrics,
    )


def _grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    if score >= 40:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Playbook / insights
# ---------------------------------------------------------------------------


def _build_playbook(
    points: Sequence[DriftPoint],
    counts: dict,
    risk_appetite: str,
    grade: str,
) -> List[dict]:
    by_verdict: dict = {}
    for p in points:
        by_verdict.setdefault(p.verdict, []).append(p)

    actions: List[dict] = []

    # P0: lost critical coverage
    disappeared_dense = [
        p for p in by_verdict.get("DISAPPEARED", [])
        if any(r["code"] == "LOST_DENSE_AREA" for r in p.reasons)
    ]
    if disappeared_dense:
        actions.append({
            "id": "INVESTIGATE_LOST_COVERAGE",
            "priority": "P0",
            "label": "Investigate lost coverage in dense regions",
            "reason": (
                f"{len(disappeared_dense)} disappeared point(s) sat in a dense "
                "baseline area; critical coverage gap likely."
            ),
            "owner": "field_team",
            "blast_radius": 4,
            "reversibility": "medium",
            "point_ids": [p.id for p in disappeared_dense],
        })

    # P0: large value drift
    big_value = [
        p for p in by_verdict.get("VALUE_DRIFT", [])
        if p.relative_value_delta is not None and abs(p.relative_value_delta) >= 1.0
    ]
    if big_value:
        actions.append({
            "id": "RESOLVE_LARGE_VALUE_DRIFT",
            "priority": "P0",
            "label": "Resolve >=100% value swings",
            "reason": (
                f"{len(big_value)} matched point(s) show |delta| >= 100%. "
                "Likely sensor failure or environmental event."
            ),
            "owner": "operator",
            "blast_radius": 4,
            "reversibility": "low",
            "point_ids": [p.id for p in big_value],
        })

    # P1: recalibrate other value drift
    small_value = [p for p in by_verdict.get("VALUE_DRIFT", []) if p not in big_value]
    if small_value:
        actions.append({
            "id": "RECALIBRATE_VALUE_DRIFT",
            "priority": "P1",
            "label": "Recalibrate sensors showing value drift",
            "reason": (
                f"{len(small_value)} sensor(s) drifted past the configured "
                "threshold but below the major-swing line."
            ),
            "owner": "operator",
            "blast_radius": 3,
            "reversibility": "high",
            "point_ids": [p.id for p in small_value],
        })

    # P1: merge duplicate emergences
    dups = by_verdict.get("DUPLICATE_EMERGENCE", [])
    if dups:
        actions.append({
            "id": "MERGE_DUPLICATE_EMERGENCES",
            "priority": "P1",
            "label": "Merge or dedupe new co-located placements",
            "reason": (
                f"{len(dups)} new point(s) sit within half the match radius "
                "of another new point."
            ),
            "owner": "data_steward",
            "blast_radius": 2,
            "reversibility": "high",
            "point_ids": [p.id for p in dups],
        })

    # P1: resurvey other disappeared
    other_disappeared = [
        p for p in by_verdict.get("DISAPPEARED", []) if p not in disappeared_dense
    ]
    if other_disappeared:
        actions.append({
            "id": "RESURVEY_DISAPPEARED",
            "priority": "P1",
            "label": "Resurvey disappeared points",
            "reason": (
                f"{len(other_disappeared)} baseline point(s) have no match in "
                "the current snapshot."
            ),
            "owner": "field_team",
            "blast_radius": 3,
            "reversibility": "medium",
            "point_ids": [p.id for p in other_disappeared],
        })

    # P2: shifted review
    shifted = by_verdict.get("SHIFTED", [])
    if len(shifted) >= 3:
        actions.append({
            "id": "REVIEW_SHIFTED_SENSORS",
            "priority": "P2",
            "label": "Review shifted sensor locations",
            "reason": (
                f"{len(shifted)} sensor(s) moved more than 30% of the match "
                "radius - check if mounts have drifted."
            ),
            "owner": "field_team",
            "blast_radius": 2,
            "reversibility": "high",
            "point_ids": [p.id for p in shifted],
        })

    # P2: redundant emergences
    redundant_emerged = [
        p for p in by_verdict.get("EMERGED", [])
        if any(r["code"] == "EMERGED_IN_DENSE_AREA" for r in p.reasons)
    ]
    if len(redundant_emerged) >= 2:
        actions.append({
            "id": "AUDIT_REDUNDANT_EMERGENCES",
            "priority": "P2",
            "label": "Audit new points in already-dense regions",
            "reason": (
                f"{len(redundant_emerged)} new point(s) landed inside the "
                "densest baseline quartile."
            ),
            "owner": "analyst",
            "blast_radius": 2,
            "reversibility": "high",
            "point_ids": [p.id for p in redundant_emerged],
        })

    welcome_emerged = [
        p for p in by_verdict.get("EMERGED", []) if p not in redundant_emerged
    ]
    if welcome_emerged:
        actions.append({
            "id": "WELCOME_NEW_COVERAGE",
            "priority": "P2",
            "label": "Acknowledge new coverage",
            "reason": (
                f"{len(welcome_emerged)} new point(s) extend coverage into "
                "sparser areas - record provenance."
            ),
            "owner": "data_steward",
            "blast_radius": 1,
            "reversibility": "high",
            "point_ids": [p.id for p in welcome_emerged],
        })

    # P3 fallback
    if not actions:
        actions.append({
            "id": "MAINTAIN_MONITORING",
            "priority": "P3",
            "label": "Continue routine monitoring",
            "reason": "No drift exceeded action thresholds.",
            "owner": "analyst",
            "blast_radius": 1,
            "reversibility": "high",
            "point_ids": [],
        })

    # Cautious adds a follow-up audit reminder when grade is C/D/F.
    if risk_appetite == "cautious" and grade in ("C", "D", "F"):
        actions.append({
            "id": "SCHEDULE_NEXT_AUDIT",
            "priority": "P2",
            "label": "Schedule the next drift audit",
            "reason": "Cautious appetite: re-audit sooner given the current grade.",
            "owner": "analyst",
            "blast_radius": 1,
            "reversibility": "high",
            "point_ids": [],
        })

    # Aggressive trims P2/P3.
    if risk_appetite == "aggressive":
        actions = [a for a in actions if a["priority"] not in ("P2", "P3")]
        if not actions:
            actions.append({
                "id": "MAINTAIN_MONITORING",
                "priority": "P3",
                "label": "Continue routine monitoring",
                "reason": "Aggressive appetite: no P0/P1 items left.",
                "owner": "analyst",
                "blast_radius": 1,
                "reversibility": "high",
                "point_ids": [],
            })

    # Dedupe by id, keep first.
    seen = set()
    deduped: List[dict] = []
    for a in actions:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        deduped.append(a)

    # P0-first, then P1, P2, P3; preserve original order within a bucket.
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    deduped.sort(key=lambda a: priority_order.get(a["priority"], 9))
    return deduped


def _build_insights(
    points: Sequence[DriftPoint],
    counts: dict,
    metrics: dict,
    baseline_size: int,
) -> List[str]:
    insights: List[str] = []
    net = metrics["current_count"] - metrics["baseline_count"]
    if net > 0:
        insights.append(f"NET_GROWTH:+{net}")
    elif net < 0:
        insights.append(f"NET_SHRINK:{net}")
    else:
        insights.append("NET_FLAT:0")

    churn = counts["EMERGED"] + counts["DISAPPEARED"]
    if baseline_size > 0 and churn / baseline_size > 0.4:
        insights.append("HIGH_TURNOVER")

    if counts["VALUE_DRIFT"] >= 3:
        insights.append("VALUE_DRIFT_CLUSTER")

    if counts["STABLE"] > 0 and counts["DISAPPEARED"] == 0 and counts["VALUE_DRIFT"] == 0:
        insights.append("STABLE_COVERAGE")

    lost_dense = sum(
        1 for p in points
        if p.verdict == "DISAPPEARED"
        and any(r["code"] == "LOST_DENSE_AREA" for r in p.reasons)
    )
    if lost_dense >= 2:
        insights.append("DENSE_AREA_HIT")

    return insights


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def apply_plan(
    report: DriftReport,
    baseline: Iterable[PointLike],  # noqa: ARG001 - signature parity
    current: Iterable[PointLike],
) -> List[Tuple[float, float, Optional[float]]]:
    """Return a deduped copy of ``current`` minus DUPLICATE_EMERGENCE picks."""
    c_full = _normalize(current)
    drop_idx = set()
    for p in report.points:
        if p.verdict == "DUPLICATE_EMERGENCE" and p.id.startswith("c#"):
            try:
                drop_idx.add(int(p.id.split("#", 1)[1]))
            except ValueError:
                continue
    return [c_full[i] for i in range(len(c_full)) if i not in drop_idx]


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def format_report(report: DriftReport, fmt: str = "text") -> str:
    fmt = (fmt or "text").lower()
    if fmt == "json":
        return json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str)
    if fmt in ("markdown", "md"):
        return _render_markdown(report)
    if fmt == "text":
        return _render_text(report)
    raise ValueError(f"Unknown format: {fmt}")


def _render_text(report: DriftReport) -> str:
    lines: List[str] = []
    lines.append("VoronoiMap Drift Report")
    lines.append("=" * 28)
    lines.append(report.summary)
    lines.append("")
    lines.append(f"Stability score : {report.stability_score:.1f}/100  (grade {report.grade})")
    lines.append(f"Match radius    : {report.match_radius:.4f}")
    lines.append(f"Value threshold : {report.value_drift_threshold:.3f}")
    lines.append(f"Risk appetite   : {report.risk_appetite}")
    lines.append("")
    lines.append("Counts:")
    for k in ("STABLE", "SHIFTED", "VALUE_DRIFT", "DISAPPEARED",
              "EMERGED", "DUPLICATE_EMERGENCE"):
        lines.append(f"  {k:<20} {sum(1 for p in report.points if p.verdict == k)}")
    lines.append("")
    if report.points:
        lines.append("Top items:")
        ranked = sorted(
            report.points,
            key=lambda p: (-p.priority_score, p.id),
        )[:10]
        for p in ranked:
            extra = ""
            if p.displacement is not None:
                extra = f"  shift={p.displacement:.3f}"
            if p.relative_value_delta is not None:
                extra += f"  rel_dv={p.relative_value_delta:+.2f}"
            lines.append(
                f"  [{p.priority}] {p.verdict:<20} {p.id:<6} "
                f"({p.point[0]:.3f},{p.point[1]:.3f}) "
                f"pri={p.priority_score:5.1f}{extra}"
            )
    lines.append("")
    lines.append("Playbook:")
    for a in report.playbook:
        lines.append(f"  [{a['priority']}] {a['id']} ({a['owner']}): {a['reason']}")
    if report.insights:
        lines.append("")
        lines.append("Insights:")
        for i in report.insights:
            lines.append(f"  - {i}")
    return "\n".join(lines)


def _render_markdown(report: DriftReport) -> str:
    lines: List[str] = []
    lines.append("# VoronoiMap Drift Report")
    lines.append("")
    lines.append(f"**Headline:** {report.summary}")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Baseline / Current | {report.baseline_count} / {report.current_count} |")
    lines.append(f"| Stability score | {report.stability_score:.1f}/100 |")
    lines.append(f"| Grade | **{report.grade}** |")
    lines.append(f"| Match radius | {report.match_radius:.4f} |")
    lines.append(f"| Value drift threshold | {report.value_drift_threshold:.3f} |")
    lines.append(f"| Risk appetite | {report.risk_appetite} |")
    lines.append(f"| Generated at | {report.generated_at} |")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append("| Verdict | Count |")
    lines.append("|---|---|")
    for k in ("STABLE", "SHIFTED", "VALUE_DRIFT", "DISAPPEARED",
              "EMERGED", "DUPLICATE_EMERGENCE"):
        n = sum(1 for p in report.points if p.verdict == k)
        lines.append(f"| {k} | {n} |")
    lines.append("")
    lines.append("## Top items")
    lines.append("")
    lines.append("| Priority | Verdict | Id | x | y | priority_score | displacement | rel_dv |")
    lines.append("|---|---|---|---|---|---|---|---|")
    ranked = sorted(report.points, key=lambda p: (-p.priority_score, p.id))[:15]
    for p in ranked:
        disp = "" if p.displacement is None else f"{p.displacement:.3f}"
        rdv = "" if p.relative_value_delta is None else f"{p.relative_value_delta:+.3f}"
        lines.append(
            f"| {p.priority} | {p.verdict} | {p.id} | "
            f"{p.point[0]:.3f} | {p.point[1]:.3f} | "
            f"{p.priority_score:.1f} | {disp} | {rdv} |"
        )
    lines.append("")
    lines.append("## Playbook")
    lines.append("")
    lines.append("| Priority | Id | Owner | Reason |")
    lines.append("|---|---|---|---|")
    for a in report.playbook:
        lines.append(f"| {a['priority']} | {a['id']} | {a['owner']} | {a['reason']} |")
    lines.append("")
    lines.append("## Insights")
    lines.append("")
    if report.insights:
        for i in report.insights:
            lines.append(f"- {i}")
    else:
        lines.append("- (none)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_points_from_csv(path: str) -> List[Tuple[float, float, Optional[float]]]:
    out: List[Tuple[float, float, Optional[float]]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return out
    # Try first row as header (skip if not numeric).
    start = 0
    first = rows[0]
    try:
        float(first[0])
        float(first[1])
    except (ValueError, IndexError):
        start = 1
    for row in rows[start:]:
        if not row:
            continue
        nums: List[float] = []
        for cell in row[:6]:
            try:
                nums.append(float(cell))
            except (TypeError, ValueError):
                pass
            if len(nums) == 3:
                break
        if len(nums) >= 2:
            v = nums[2] if len(nums) >= 3 else None
            out.append((nums[0], nums[1], v))
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
        description=(
            "Agentic temporal drift advisor for VoronoiMap spatial snapshots."
        ),
    )
    p.add_argument("baseline", help="Baseline CSV (x,y[,value])")
    p.add_argument("current", help="Current CSV (x,y[,value])")
    p.add_argument("--bounds", default=None,
                   help="W,S,E,N (default: auto from union)")
    p.add_argument("--match-radius", type=float, default=None,
                   help="Match radius (default: 0.5 * median baseline NN distance)")
    p.add_argument("--value-threshold", type=float, default=0.25,
                   help="Relative |delta| for VALUE_DRIFT (default 0.25)")
    p.add_argument("--risk", default="balanced",
                   choices=["cautious", "balanced", "aggressive"])
    p.add_argument("--format", "-f", default="text",
                   choices=["text", "markdown", "md", "json"])
    p.add_argument("--apply", default=None,
                   help="Write deduped current points (drops DUPLICATE_EMERGENCE) to this CSV")
    p.add_argument("--output", "-o", default=None,
                   help="Write the report to this path instead of stdout")
    args = p.parse_args(argv)

    baseline = _read_points_from_csv(args.baseline)
    current = _read_points_from_csv(args.current)

    report = detect_drift(
        baseline,
        current,
        bounds=_parse_bounds(args.bounds),
        match_radius=args.match_radius,
        value_drift_threshold=args.value_threshold,
        risk_appetite=args.risk,
    )

    out = format_report(report, args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)

    if args.apply:
        cleaned = apply_plan(report, baseline, current)
        with open(args.apply, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["x", "y", "value"])
            for pt in cleaned:
                w.writerow([pt[0], pt[1], "" if pt[2] is None else pt[2]])

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
