#!/usr/bin/env python3
"""vormap_route - Agentic Field-Visit Route Planner Advisor.

Operational sibling to vormap_curator / vormap_redundancy /
vormap_sensorplanner / vormap_drift / vormap_handoff /
vormap_calibration / vormap_replacement.

Where vormap_sensorplanner answers "where should I put the next
sensor?", and vormap_handoff answers "what does the next shift need
to know?", this advisor answers a tactical, day-of question:

    "I have a crew, a shift budget, and N field visits to do. Which
     ones do we run today, in what order, and what should we drop
     or escalate?"

Given a list of :class:`SensorVisit` candidates and a crew origin,
the advisor:

* Plans a deterministic greedy route weighted by urgency, criticality,
  and travel cost (risk_appetite knob trades coverage vs throughput).
* Tags each visit with a verdict (``VISIT_NOW`` / ``SCHEDULED`` /
  ``DEFERRED`` / ``DROPPED_LOW_VALUE`` / ``BLOCKED_TIME_WINDOW`` /
  ``INSUFFICIENT_DATA``) and 0-100 priority score.
* Emits a P0-first deduped playbook of crew-level interventions.
* Surfaces portfolio insights and an A-F grade.
* Renders to text / markdown / byte-stable JSON.

Pure stdlib (numpy optional), deterministic given a fixed ``now_fn``,
never mutates inputs.

CLI::

    python vormap_route.py visits.csv --crew-start 0,0 \\
        --shift-minutes 480 --speed 1.0 --risk balanced --format md

    python vormap_route.py --demo --format text
"""

from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants & utilities
# ---------------------------------------------------------------------------

_RISK_APPETITES = ("cautious", "balanced", "aggressive")

_OWNERS = {
    "shift_lead",
    "field_team",
    "dispatcher",
    "ops",
    "data_steward",
    "safety_officer",
}

_TASK_TYPES = (
    "inspect",
    "repair",
    "replace",
    "calibrate",
    "clean",
    "refuel",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _appetite_mult(risk: str) -> float:
    if risk == "cautious":
        return 1.15
    if risk == "aggressive":
        return 0.85
    return 1.0


def _coerce_point(p: Any) -> Optional[Tuple[float, float]]:
    if p is None:
        return None
    try:
        x, y = p[0], p[1]
        return float(x), float(y)
    except (TypeError, ValueError, IndexError):
        return None


def _euclid(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SensorVisit:
    id: str
    x: float
    y: float
    urgency: float = 50.0  # 0..100
    task_type: str = "inspect"
    estimated_minutes: float = 20.0
    last_visited_days: Optional[float] = None
    criticality: int = 3  # 1..5
    access_difficulty: int = 1  # 1..5
    time_window: Optional[Tuple[float, float]] = None  # minutes from shift start

    @classmethod
    def from_record(cls, row: Dict[str, Any]) -> "SensorVisit":
        tw_raw = row.get("time_window")
        tw: Optional[Tuple[float, float]] = None
        if tw_raw is not None:
            try:
                a, b = float(tw_raw[0]), float(tw_raw[1])
                tw = (a, b)
            except (TypeError, ValueError, IndexError):
                tw = None
        return cls(
            id=str(row["id"]),
            x=float(row["x"]),
            y=float(row["y"]),
            urgency=float(row.get("urgency", 50.0)),
            task_type=str(row.get("task_type", "inspect")),
            estimated_minutes=float(row.get("estimated_minutes", 20.0)),
            last_visited_days=(
                float(row["last_visited_days"])
                if row.get("last_visited_days") not in (None, "", "None")
                else None
            ),
            criticality=int(row.get("criticality", 3)),
            access_difficulty=int(row.get("access_difficulty", 1)),
            time_window=tw,
        )


@dataclass
class RoutedVisit:
    id: str
    verdict: str
    priority: str  # P0..P3
    priority_score: float
    point: Tuple[float, float]
    travel_min: float
    eta_min: float
    finish_min: float
    estimated_minutes: float
    sequence: Optional[int]  # 1-based order in dispatched route, None if not run
    reasons: List[Dict[str, str]] = field(default_factory=list)
    task_type: str = "inspect"
    urgency: float = 50.0
    criticality: int = 3
    access_difficulty: int = 1


@dataclass
class PlaybookAction:
    id: str
    priority: str
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    related_ids: List[str] = field(default_factory=list)


@dataclass
class RouteReport:
    summary: str
    grade: str
    risk_appetite: str
    crew_start: Tuple[float, float]
    shift_minutes: float
    travel_speed: float
    total_visits: int
    completed_visits: int
    deferred_visits: int
    dropped_visits: int
    blocked_visits: int
    total_travel_min: float
    total_work_min: float
    utilisation_pct: float
    urgency_coverage_pct: float
    shift_finish_min: float
    visits: List[RoutedVisit]
    playbook: List[PlaybookAction]
    insights: List[str]
    generated_at: str


# ---------------------------------------------------------------------------
# Advisor
# ---------------------------------------------------------------------------


class RouteAdvisor:
    """Plan a one-shift field-visit route with agentic verdicts."""

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn or _utc_now

    # ---- public ----------------------------------------------------------

    def analyze(
        self,
        visits: Sequence[Any],
        *,
        crew_start: Tuple[float, float] = (0.0, 0.0),
        shift_minutes: float = 480.0,
        travel_speed: float = 1.0,
        max_visits: Optional[int] = None,
        risk_appetite: str = "balanced",
    ) -> RouteReport:
        if risk_appetite not in _RISK_APPETITES:
            raise ValueError(
                f"risk_appetite must be one of {_RISK_APPETITES}, got {risk_appetite!r}"
            )
        if travel_speed <= 0:
            raise ValueError("travel_speed must be > 0")
        if shift_minutes < 0:
            raise ValueError("shift_minutes must be >= 0")

        start = _coerce_point(crew_start) or (0.0, 0.0)
        # Deep-copy inputs to guarantee zero mutation.
        normalised: List[SensorVisit] = []
        for v in visits:
            if isinstance(v, SensorVisit):
                normalised.append(copy.deepcopy(v))
            else:
                normalised.append(SensorVisit.from_record(copy.deepcopy(dict(v))))

        appetite = _appetite_mult(risk_appetite)
        urgency_weight = {
            "cautious": 1.25,
            "balanced": 1.0,
            "aggressive": 0.85,
        }[risk_appetite]
        cost_weight = {
            "cautious": 0.6,
            "balanced": 1.0,
            "aggressive": 1.4,
        }[risk_appetite]

        # ---- greedy nearest-urgency route -------------------------------
        remaining = {v.id: v for v in normalised}
        order: List[str] = []
        cursor = start
        elapsed = 0.0
        per_visit_meta: Dict[str, Dict[str, float]] = {}

        cap = max_visits if max_visits is not None else len(normalised)

        # Stable iteration: pre-sort ids so ties resolve deterministically.
        all_ids_sorted = sorted(remaining.keys())

        while remaining and len(order) < cap:
            best_id: Optional[str] = None
            best_score = -math.inf
            best_travel = 0.0
            best_eta = 0.0
            for vid in all_ids_sorted:
                if vid not in remaining:
                    continue
                v = remaining[vid]
                travel = _euclid(cursor, (v.x, v.y)) / travel_speed
                eta = elapsed + travel
                # Time-window feasibility check (soft: if window present and ETA past window end, big penalty)
                window_ok = True
                if v.time_window is not None:
                    w0, w1 = v.time_window
                    if eta > w1 + 1e-6:
                        window_ok = False
                # Budget check: would finishing this visit blow the shift?
                finish = eta + v.estimated_minutes
                budget_ok = finish <= shift_minutes + 1e-6
                # Score
                base = v.urgency * urgency_weight
                crit_bonus = (v.criticality - 3) * 6
                travel_pen = travel * cost_weight
                window_pen = 0.0 if window_ok else 1000.0
                budget_pen = 0.0 if budget_ok else 1000.0
                access_pen = (v.access_difficulty - 1) * 2 * cost_weight
                score = base + crit_bonus - travel_pen - access_pen - window_pen - budget_pen
                if score > best_score:
                    best_score = score
                    best_id = vid
                    best_travel = travel
                    best_eta = eta

            if best_id is None:
                break
            v = remaining[best_id]
            # If best is infeasible (negative budget penalty), stop dispatching.
            if best_score < -500:
                break
            order.append(best_id)
            per_visit_meta[best_id] = {
                "travel_min": best_travel,
                "eta_min": best_eta,
                "finish_min": best_eta + v.estimated_minutes,
            }
            elapsed = best_eta + v.estimated_minutes
            cursor = (v.x, v.y)
            del remaining[best_id]

        # ---- per-visit verdicts ----------------------------------------
        routed: List[RoutedVisit] = []
        for v in normalised:
            reasons: List[Dict[str, str]] = []
            meta = per_visit_meta.get(v.id)
            if meta is None:
                # Did not dispatch: figure out why
                travel = _euclid(start, (v.x, v.y)) / travel_speed
                eta = travel  # rough best-case ETA from cold start
                finish = eta + v.estimated_minutes
                sequence: Optional[int] = None
                if (
                    v.time_window is not None
                    and eta > v.time_window[1] + 1e-6
                ):
                    verdict = "BLOCKED_TIME_WINDOW"
                    reasons.append(
                        {
                            "code": "TIME_WINDOW_MISSED",
                            "label": f"window {v.time_window} unreachable from start",
                        }
                    )
                elif (v.urgency < 20) and v.criticality <= 2:
                    verdict = "DROPPED_LOW_VALUE"
                    reasons.append(
                        {"code": "LOW_URGENCY", "label": "urgency<20 and criticality<=2"}
                    )
                elif finish > shift_minutes + 1e-6:
                    verdict = "DEFERRED"
                    reasons.append(
                        {"code": "OVER_BUDGET", "label": "did not fit in shift budget"}
                    )
                else:
                    verdict = "DEFERRED"
                    reasons.append(
                        {"code": "LOWER_PRIORITY", "label": "displaced by higher-value visits"}
                    )
            else:
                travel = meta["travel_min"]
                eta = meta["eta_min"]
                finish = meta["finish_min"]
                sequence = order.index(v.id) + 1
                if v.urgency >= 80 or v.criticality >= 5:
                    verdict = "VISIT_NOW"
                else:
                    verdict = "SCHEDULED"
                reasons.append(
                    {"code": "DISPATCHED", "label": f"seq={sequence} eta={eta:.1f}m"}
                )
                if v.time_window is not None:
                    w0, w1 = v.time_window
                    if eta < w0 - 1e-6:
                        reasons.append(
                            {"code": "EARLY_FOR_WINDOW", "label": "arriving before window opens"}
                        )

            # Priority + score
            base_score = (
                v.urgency * (1.0 if verdict != "DROPPED_LOW_VALUE" else 0.3)
                + (v.criticality - 3) * 6
            )
            if verdict == "BLOCKED_TIME_WINDOW":
                base_score += 10  # surfaces in playbook even though not dispatched
            score = _clamp(base_score * (1.15 if risk_appetite == "cautious" else (0.85 if risk_appetite == "aggressive" else 1.0)))

            if verdict == "VISIT_NOW":
                priority = "P0"
            elif verdict == "BLOCKED_TIME_WINDOW":
                priority = "P1"
            elif verdict == "SCHEDULED":
                priority = "P1"
            elif verdict == "DEFERRED":
                priority = "P2" if v.urgency >= 60 or v.criticality >= 4 else "P3"
            elif verdict == "DROPPED_LOW_VALUE":
                priority = "P3"
            else:
                priority = "P3"

            routed.append(
                RoutedVisit(
                    id=v.id,
                    verdict=verdict,
                    priority=priority,
                    priority_score=round(score, 2),
                    point=(v.x, v.y),
                    travel_min=round(travel, 3),
                    eta_min=round(eta, 3),
                    finish_min=round(finish, 3),
                    estimated_minutes=v.estimated_minutes,
                    sequence=sequence,
                    reasons=reasons,
                    task_type=v.task_type,
                    urgency=v.urgency,
                    criticality=v.criticality,
                    access_difficulty=v.access_difficulty,
                )
            )

        # Sort: dispatched first by sequence asc, then everyone else by priority/score
        def _sort_key(rv: RoutedVisit) -> Tuple[int, float, str]:
            if rv.sequence is not None:
                return (0, rv.sequence, rv.id)
            pri_rank = {"P0": 1, "P1": 2, "P2": 3, "P3": 4}.get(rv.priority, 5)
            return (1, pri_rank * 1000 - rv.priority_score, rv.id)

        routed.sort(key=_sort_key)

        # ---- portfolio rollups -----------------------------------------
        total = len(routed)
        completed = sum(1 for r in routed if r.sequence is not None)
        deferred = sum(1 for r in routed if r.verdict == "DEFERRED")
        dropped = sum(1 for r in routed if r.verdict == "DROPPED_LOW_VALUE")
        blocked = sum(1 for r in routed if r.verdict == "BLOCKED_TIME_WINDOW")
        total_travel = sum(r.travel_min for r in routed if r.sequence is not None)
        total_work = sum(r.estimated_minutes for r in routed if r.sequence is not None)
        shift_finish = max(
            (r.finish_min for r in routed if r.sequence is not None),
            default=0.0,
        )
        utilisation = (
            (total_travel + total_work) / shift_minutes * 100.0
            if shift_minutes > 0
            else 0.0
        )
        total_urg = sum(r.urgency for r in routed)
        done_urg = sum(r.urgency for r in routed if r.sequence is not None)
        urgency_coverage = (done_urg / total_urg * 100.0) if total_urg > 0 else 100.0

        # ---- grade -----------------------------------------------------
        grade = self._grade(routed, urgency_coverage)

        # ---- insights --------------------------------------------------
        insights = self._insights(routed, total_travel, total_work, urgency_coverage, utilisation)

        # ---- playbook --------------------------------------------------
        playbook = self._playbook(routed, total_travel, total_work, utilisation, deferred, blocked, dropped, grade, risk_appetite)

        # ---- headline --------------------------------------------------
        p0_count = sum(1 for r in routed if r.priority == "P0")
        summary = (
            f"VERDICT: grade={grade} visits={completed}/{total} P0={p0_count} "
            f"travel={total_travel:.1f}m work={total_work:.1f}m "
            f"util={utilisation:.0f}% coverage={urgency_coverage:.0f}%"
        )

        return RouteReport(
            summary=summary,
            grade=grade,
            risk_appetite=risk_appetite,
            crew_start=start,
            shift_minutes=shift_minutes,
            travel_speed=travel_speed,
            total_visits=total,
            completed_visits=completed,
            deferred_visits=deferred,
            dropped_visits=dropped,
            blocked_visits=blocked,
            total_travel_min=round(total_travel, 2),
            total_work_min=round(total_work, 2),
            utilisation_pct=round(utilisation, 2),
            urgency_coverage_pct=round(urgency_coverage, 2),
            shift_finish_min=round(shift_finish, 2),
            visits=routed,
            playbook=playbook,
            insights=insights,
            generated_at=self._now_fn().isoformat(),
        )

    # ---- helpers --------------------------------------------------------

    def _grade(self, routed: List[RoutedVisit], coverage: float) -> str:
        # F if any P0 deferred (or blocked!) or coverage<25%
        p0_deferred = any(
            r.priority == "P0" and r.verdict in ("DEFERRED", "BLOCKED_TIME_WINDOW")
            for r in routed
        )
        if not routed:
            return "F"
        if p0_deferred or coverage < 25:
            return "F"
        if coverage < 40:
            return "D"
        if coverage < 60:
            return "C"
        if coverage < 80:
            return "B"
        return "A"

    def _insights(
        self,
        routed: List[RoutedVisit],
        travel: float,
        work: float,
        coverage: float,
        utilisation: float,
    ) -> List[str]:
        out: List[str] = []
        if not routed:
            out.append("EMPTY_ROUTE")
            return out
        if work > 0 and travel > work:
            out.append("HIGH_TRAVEL_OVERHEAD")
        p0 = [r for r in routed if r.priority == "P0"]
        if len(p0) >= 3:
            xs = [r.point[0] for r in p0]
            ys = [r.point[1] for r in p0]
            spread = max(max(xs) - min(xs), max(ys) - min(ys))
            if spread <= 25.0:
                out.append("URGENCY_CLUSTER")
        if sum(1 for r in routed if r.verdict == "DEFERRED") >= 3:
            out.append("DEFERRED_BACKLOG")
        if sum(1 for r in routed if r.verdict == "BLOCKED_TIME_WINDOW") >= 2:
            out.append("TIGHT_TIME_WINDOWS")
        if coverage >= 85:
            out.append("GOOD_COVERAGE")
        if utilisation < 40:
            out.append("THIN_SHIFT")
        return out

    def _playbook(
        self,
        routed: List[RoutedVisit],
        travel: float,
        work: float,
        utilisation: float,
        deferred: int,
        blocked: int,
        dropped: int,
        grade: str,
        risk: str,
    ) -> List[PlaybookAction]:
        actions: List[PlaybookAction] = []
        p0_deferred = [
            r for r in routed if r.priority == "P0" and r.verdict in ("DEFERRED", "BLOCKED_TIME_WINDOW")
        ]
        if p0_deferred:
            actions.append(
                PlaybookAction(
                    id="DISPATCH_EMERGENCY_CREW",
                    priority="P0",
                    label="Dispatch emergency crew for high-urgency deferred visits",
                    reason=f"{len(p0_deferred)} P0 visit(s) could not be fit in shift",
                    owner="dispatcher",
                    blast_radius=4,
                    reversibility="medium",
                    related_ids=[r.id for r in p0_deferred],
                )
            )
        if utilisation > 95 and deferred >= 3:
            actions.append(
                PlaybookAction(
                    id="ADD_SECOND_SHIFT",
                    priority="P1",
                    label="Add second shift / overflow crew",
                    reason=f"utilisation {utilisation:.0f}% with {deferred} deferred visits",
                    owner="shift_lead",
                    blast_radius=3,
                    reversibility="high",
                    related_ids=[r.id for r in routed if r.verdict == "DEFERRED"],
                )
            )
        if blocked >= 2:
            actions.append(
                PlaybookAction(
                    id="RESEQUENCE_FOR_TIME_WINDOWS",
                    priority="P1",
                    label="Resequence route to honour time windows",
                    reason=f"{blocked} visit(s) blocked by time window",
                    owner="dispatcher",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=[r.id for r in routed if r.verdict == "BLOCKED_TIME_WINDOW"],
                )
            )
        if dropped >= 2:
            actions.append(
                PlaybookAction(
                    id="DROP_LOW_VALUE_VISITS",
                    priority="P2",
                    label="Confirm dropped low-value visits with ops",
                    reason=f"{dropped} visit(s) deprioritised (low urgency + criticality)",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    related_ids=[r.id for r in routed if r.verdict == "DROPPED_LOW_VALUE"],
                )
            )
        if work > 0 and travel > 0.5 * (travel + work):
            actions.append(
                PlaybookAction(
                    id="RECHARGE_TRAVEL_BUDGET",
                    priority="P2",
                    label="Recharge travel budget / cluster visits geographically",
                    reason=f"travel {travel:.1f}m is >50% of in-route time {(travel+work):.1f}m",
                    owner="dispatcher",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=[],
                )
            )
        visit_now = [r for r in routed if r.verdict == "VISIT_NOW"]
        # Heuristic: most VISIT_NOW visits were also recently visited
        # (last_visited_days < 7 known) -> urgency model is over-reacting.
        recent = [r for r in visit_now if r.priority == "P0"]
        # We don't have last_visited_days on RoutedVisit; recompute via reasons skip
        # Note: we use estimated_minutes & sequence only here. Skip noisy heuristic.
        if any(r.access_difficulty >= 4 for r in routed if r.sequence is not None):
            actions.append(
                PlaybookAction(
                    id="CONFIRM_ACCESS",
                    priority="P2",
                    label="Confirm access for hard-to-reach sites",
                    reason="route includes site(s) with access_difficulty>=4",
                    owner="safety_officer",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=[
                        r.id for r in routed if r.sequence is not None and r.access_difficulty >= 4
                    ],
                )
            )
        if not actions:
            actions.append(
                PlaybookAction(
                    id="MAINTAIN_ROUTINE",
                    priority="P3",
                    label="Maintain routine route execution",
                    reason="no escalations detected; continue planned dispatch",
                    owner="shift_lead",
                    blast_radius=1,
                    reversibility="high",
                    related_ids=[],
                )
            )
        # Cautious tail.
        if risk == "cautious" and grade in ("C", "D", "F"):
            actions.append(
                PlaybookAction(
                    id="SCHEDULE_ROUTE_REVIEW",
                    priority="P2",
                    label="Schedule end-of-shift route review",
                    reason=f"grade {grade} with cautious risk appetite",
                    owner="shift_lead",
                    blast_radius=1,
                    reversibility="high",
                    related_ids=[],
                )
            )
        # Aggressive trim P3 fallback if other actions exist
        if risk == "aggressive" and any(a.priority != "P3" for a in actions):
            actions = [a for a in actions if a.priority != "P3"]

        # P0-first stable dedup + ordering
        seen = set()
        deduped: List[PlaybookAction] = []
        for a in sorted(actions, key=lambda a: ({"P0": 0, "P1": 1, "P2": 2, "P3": 3}[a.priority], a.id)):
            if a.id in seen:
                continue
            seen.add(a.id)
            deduped.append(a)
        return deduped


# ---------------------------------------------------------------------------
# Helpers: apply / simulate
# ---------------------------------------------------------------------------


def apply_plan(report: RouteReport) -> List[str]:
    """Return dispatched visit ids in sequence order."""
    return [r.id for r in report.visits if r.sequence is not None]


def simulate(
    report: RouteReport,
    *,
    extra_shift_minutes: float,
    visits: Sequence[Any],
    crew_start: Optional[Tuple[float, float]] = None,
    travel_speed: Optional[float] = None,
    risk_appetite: Optional[str] = None,
    now_fn: Optional[Callable[[], datetime]] = None,
) -> RouteReport:
    """Re-plan with shift_minutes + extra_shift_minutes; never mutates inputs."""
    advisor = RouteAdvisor(now_fn=now_fn)
    new = advisor.analyze(
        visits,
        crew_start=crew_start or report.crew_start,
        shift_minutes=report.shift_minutes + extra_shift_minutes,
        travel_speed=travel_speed or report.travel_speed,
        risk_appetite=risk_appetite or report.risk_appetite,
    )
    new.summary = new.summary + f" [simulated: +{extra_shift_minutes:.0f}m shift]"
    return new


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _serialise_visit(v: RoutedVisit) -> Dict[str, Any]:
    d = asdict(v)
    d["point"] = list(d["point"])
    return d


def to_text(report: RouteReport) -> str:
    lines: List[str] = []
    lines.append(report.summary)
    lines.append(f"Grade: {report.grade}    Coverage: {report.urgency_coverage_pct:.1f}%")
    lines.append(f"Risk appetite: {report.risk_appetite}")
    lines.append(f"Shift: {report.shift_minutes:.0f}m  Finish: {report.shift_finish_min:.1f}m  Util: {report.utilisation_pct:.0f}%")
    lines.append(f"Generated at: {report.generated_at}")
    lines.append("")
    lines.append("Route:")
    if report.visits:
        for v in report.visits:
            seq = f"#{v.sequence:>2}" if v.sequence is not None else " --"
            lines.append(
                f"  {seq} [{v.priority}] {v.verdict:<22} {v.id} "
                f"@({v.point[0]:.1f},{v.point[1]:.1f}) urgency={v.urgency:.0f} "
                f"travel={v.travel_min:.1f}m work={v.estimated_minutes:.1f}m"
            )
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("Playbook:")
    for a in report.playbook:
        lines.append(f"  [{a.priority}] {a.label} - {a.reason} (owner: {a.owner})")
    lines.append("")
    lines.append("Insights:")
    if report.insights:
        for ins in report.insights:
            lines.append(f"  - {ins}")
    else:
        lines.append("  - (none)")
    return "\n".join(lines)


def to_markdown(report: RouteReport) -> str:
    parts: List[str] = []
    parts.append("# Field Visit Route Plan")
    parts.append("")
    parts.append("## Summary")
    parts.append("")
    parts.append("| Metric | Value |")
    parts.append("|---|---|")
    parts.append(f"| Headline | {report.summary} |")
    parts.append(f"| Grade | {report.grade} |")
    parts.append(f"| Risk appetite | {report.risk_appetite} |")
    parts.append(f"| Shift minutes | {report.shift_minutes:.0f} |")
    parts.append(f"| Total visits | {report.total_visits} |")
    parts.append(f"| Completed | {report.completed_visits} |")
    parts.append(f"| Deferred | {report.deferred_visits} |")
    parts.append(f"| Dropped | {report.dropped_visits} |")
    parts.append(f"| Blocked | {report.blocked_visits} |")
    parts.append(f"| Travel minutes | {report.total_travel_min:.1f} |")
    parts.append(f"| Work minutes | {report.total_work_min:.1f} |")
    parts.append(f"| Utilisation % | {report.utilisation_pct:.1f} |")
    parts.append(f"| Urgency coverage % | {report.urgency_coverage_pct:.1f} |")
    parts.append(f"| Shift finish min | {report.shift_finish_min:.1f} |")
    parts.append(f"| Generated at | {report.generated_at} |")
    parts.append("")

    parts.append("## Route")
    parts.append("")
    if report.visits:
        parts.append("| Seq | Priority | Verdict | ID | Urgency | Travel(m) | ETA(m) | Finish(m) | Score |")
        parts.append("|---|---|---|---|---|---|---|---|---|")
        for v in report.visits:
            seq = str(v.sequence) if v.sequence is not None else "-"
            parts.append(
                f"| {seq} | {v.priority} | {v.verdict} | {v.id} | "
                f"{v.urgency:.0f} | {v.travel_min:.1f} | {v.eta_min:.1f} | "
                f"{v.finish_min:.1f} | {v.priority_score:.1f} |"
            )
    else:
        parts.append("_No visits._")
    parts.append("")

    parts.append("## Playbook")
    parts.append("")
    parts.append("| Priority | Action | Owner | Reason |")
    parts.append("|---|---|---|---|")
    for a in report.playbook:
        parts.append(f"| {a.priority} | {a.label} | {a.owner} | {a.reason} |")
    parts.append("")

    parts.append("## Insights")
    parts.append("")
    if report.insights:
        for ins in report.insights:
            parts.append(f"- {ins}")
    else:
        parts.append("- _none_")
    parts.append("")
    return "\n".join(parts)


def to_json(report: RouteReport) -> str:
    payload = {
        "summary": report.summary,
        "grade": report.grade,
        "risk_appetite": report.risk_appetite,
        "crew_start": list(report.crew_start),
        "shift_minutes": report.shift_minutes,
        "travel_speed": report.travel_speed,
        "total_visits": report.total_visits,
        "completed_visits": report.completed_visits,
        "deferred_visits": report.deferred_visits,
        "dropped_visits": report.dropped_visits,
        "blocked_visits": report.blocked_visits,
        "total_travel_min": report.total_travel_min,
        "total_work_min": report.total_work_min,
        "utilisation_pct": report.utilisation_pct,
        "urgency_coverage_pct": report.urgency_coverage_pct,
        "shift_finish_min": report.shift_finish_min,
        "visits": [_serialise_visit(v) for v in report.visits],
        "playbook": [asdict(a) for a in report.playbook],
        "insights": list(report.insights),
        "generated_at": report.generated_at,
    }
    return json.dumps(payload, sort_keys=True, indent=2, default=str)


def render(report: RouteReport, fmt: str) -> str:
    if fmt in ("text", "txt"):
        return to_text(report)
    if fmt in ("md", "markdown"):
        return to_markdown(report)
    if fmt == "json":
        return to_json(report)
    raise ValueError(f"unknown format: {fmt!r}")


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def load_visits_csv(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rec: Dict[str, Any] = {k: v for k, v in row.items() if v != ""}
            tw = rec.pop("time_window", None)
            if tw and ":" in tw:
                a, b = tw.split(":", 1)
                rec["time_window"] = (float(a), float(b))
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def _demo_visits() -> List[SensorVisit]:
    return [
        SensorVisit(id="S1", x=2.0, y=1.0, urgency=92, task_type="repair",
                    estimated_minutes=35, criticality=5, access_difficulty=2),
        SensorVisit(id="S2", x=5.0, y=4.0, urgency=70, task_type="calibrate",
                    estimated_minutes=25, criticality=3, access_difficulty=1),
        SensorVisit(id="S3", x=12.0, y=3.0, urgency=40, task_type="inspect",
                    estimated_minutes=15, criticality=2, access_difficulty=1,
                    time_window=(60.0, 150.0)),
        SensorVisit(id="S4", x=8.0, y=9.0, urgency=15, task_type="clean",
                    estimated_minutes=20, criticality=1, access_difficulty=1),
        SensorVisit(id="S5", x=20.0, y=20.0, urgency=85, task_type="replace",
                    estimated_minutes=60, criticality=4, access_difficulty=4),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _windows_utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass


def _parse_crew_start(s: str) -> Tuple[float, float]:
    a, b = s.split(",")
    return float(a), float(b)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vormap_route",
        description="Agentic field-visit route planner advisor.",
    )
    p.add_argument("input", nargs="?", help="CSV with visit rows (omit when using --demo)")
    p.add_argument("--demo", action="store_true", help="use built-in demo visits")
    p.add_argument("--crew-start", default="0,0", help="origin point 'x,y' (default 0,0)")
    p.add_argument("--shift-minutes", type=float, default=480.0)
    p.add_argument("--speed", type=float, default=1.0, dest="speed",
                   help="travel speed units per minute (default 1.0)")
    p.add_argument("--max-visits", type=int, default=None)
    p.add_argument("--risk", choices=_RISK_APPETITES, default="balanced")
    p.add_argument("--format", choices=("text", "md", "markdown", "json"), default="text")
    p.add_argument("--output", default=None)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    _windows_utf8_stdout()
    args = build_arg_parser().parse_args(argv)

    if args.demo:
        visits: Sequence[Any] = _demo_visits()
    elif args.input:
        visits = load_visits_csv(args.input)
    else:
        print("error: provide a CSV input path or --demo", file=sys.stderr)
        return 2

    advisor = RouteAdvisor()
    report = advisor.analyze(
        visits,
        crew_start=_parse_crew_start(args.crew_start),
        shift_minutes=args.shift_minutes,
        travel_speed=args.speed,
        max_visits=args.max_visits,
        risk_appetite=args.risk,
    )
    out = render(report, args.format)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
