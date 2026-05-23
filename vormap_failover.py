#!/usr/bin/env python3
"""vormap_failover - Agentic Single-Sensor Outage Impact + Contingency Advisor.

12th VoronoiMap operational sibling. Distinct from:

* ``vormap_replacement`` (hardware EOL / scheduled replacement),
* ``vormap_redundancy`` (which sensors can we retire because we're
  oversampled?),
* ``vormap_sensorplanner`` (where to add the next N sensors?),
* ``vormap_route`` (which visits to run today?).

This advisor answers the *operational continuity* question::

    "If sensor X drops off the network right now, who picks up the
     load, how much coverage do we lose, and which sensors are
     themselves so isolated that losing them would be catastrophic?"

It simulates per-sensor outages over a 2D point field, computes
nearest-neighbour failover assignments, and grades each sensor's
*single-point-of-failure risk* + each neighbour's *projected overload*
+ produces a P0-first deduped contingency playbook for the whole fleet.

Pure stdlib (numpy opportunistic, not required), deterministic given a
fixed ``now_fn``, never mutates inputs.

CLI::

    python vormap_failover.py points.csv --risk balanced --format md
    python vormap_failover.py --demo --format text
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
# Constants
# ---------------------------------------------------------------------------

_RISK_APPETITES = ("cautious", "balanced", "aggressive")

_OWNERS = {
    "shift_lead",
    "field_team",
    "ops",
    "procurement",
    "data_steward",
    "maintenance",
}

# Sensor-level outage verdicts (the sensor that goes down).
SENSOR_VERDICTS = (
    "ISOLATED_FAILURE",       # P0: failover backup is very far away
    "CASCADE_RISK",           # P0: backup is itself near-saturated
    "DEGRADED_COVERAGE",      # P1: backup exists but coverage gap >= 2x median
    "GRACEFUL_FAILOVER",      # P2: backup picks up cleanly
    "REDUNDANT_OUTAGE",       # P3: many neighbours, low impact
    "INSUFFICIENT_DATA",      # P3: <2 points in fleet
)

# Per-neighbour roles when a target sensor fails.
NEIGHBOUR_ROLES = (
    "PRIMARY_BACKUP",
    "SECONDARY_BACKUP",
    "TERTIARY_BACKUP",
    "OVERLOADED_AFTER_FAILOVER",
    "UNAFFECTED",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FleetSensor:
    """A 2D sensor in the fleet.

    Optional ``criticality`` (1-5, default 3) and ``has_backup_link``
    flag (default False) influence priority scoring.
    """

    sid: str
    x: float
    y: float
    criticality: int = 3
    has_backup_link: bool = False
    label: Optional[str] = None

    @classmethod
    def from_record(cls, rec: Dict[str, Any], default_sid: str = "") -> "FleetSensor":
        sid = str(rec.get("sid") or rec.get("id") or rec.get("name") or default_sid).strip()
        if not sid:
            raise ValueError("FleetSensor requires non-empty sid/id/name")
        try:
            x = float(rec.get("x", rec.get("lon", rec.get("longitude", 0.0))))
            y = float(rec.get("y", rec.get("lat", rec.get("latitude", 0.0))))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"FleetSensor {sid!r}: bad x/y") from exc
        crit_raw = rec.get("criticality", rec.get("crit", 3))
        try:
            criticality = max(1, min(5, int(float(crit_raw))))
        except (TypeError, ValueError):
            criticality = 3
        backup_raw = rec.get("has_backup_link", rec.get("backup", False))
        if isinstance(backup_raw, str):
            has_backup_link = backup_raw.strip().lower() in {"1", "true", "t", "yes", "y"}
        else:
            has_backup_link = bool(backup_raw)
        label = rec.get("label")
        if label is not None:
            label = str(label)
        return cls(
            sid=sid,
            x=x,
            y=y,
            criticality=criticality,
            has_backup_link=has_backup_link,
            label=label,
        )


@dataclass
class OutageScenario:
    """Per-target outage simulation result."""

    sid: str
    verdict: str
    priority: str          # P0..P3
    priority_score: float  # 0-100
    coverage_gap: float    # distance from target to its primary backup
    coverage_gap_ratio: float  # coverage_gap / median_nn
    primary_backup: Optional[str]
    backups: List[Dict[str, Any]] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)


@dataclass
class FailoverReport:
    """Top-level output of :func:`analyze`."""

    generated_at: str
    sensor_count: int
    median_nn_distance: float
    portfolio_risk_score: float  # 0-100
    grade: str
    risk_appetite: str
    scenarios: List[OutageScenario]
    neighbour_load: List[Dict[str, Any]]  # per-sensor projected overload summary
    playbook: List[Dict[str, Any]]
    insights: List[str]
    headline: str


# ---------------------------------------------------------------------------
# Core geometry helpers
# ---------------------------------------------------------------------------

def _dist(a: FleetSensor, b: FleetSensor) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _nearest_neighbours(target: FleetSensor, others: Sequence[FleetSensor], k: int = 3) -> List[Tuple[FleetSensor, float]]:
    pairs = [(s, _dist(target, s)) for s in others if s.sid != target.sid]
    pairs.sort(key=lambda t: (t[1], t[0].sid))
    return pairs[: max(0, k)]


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return float((s[mid - 1] + s[mid]) / 2.0)


def _percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    pos = (len(s) - 1) * (q / 100.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(s[lo])
    frac = pos - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _risk_multiplier(risk_appetite: str) -> float:
    if risk_appetite == "cautious":
        return 1.15
    if risk_appetite == "aggressive":
        return 0.85
    return 1.0


def _verdict_for(
    target: FleetSensor,
    backups: List[Tuple[FleetSensor, float]],
    median_nn: float,
    neighbour_loads: Dict[str, int],
) -> Tuple[str, List[str], float]:
    """Decide the outage verdict for a single sensor, plus base priority."""

    reasons: List[str] = []
    if median_nn <= 0 or not backups:
        if not backups:
            reasons.append("NO_BACKUP_AVAILABLE")
            return "ISOLATED_FAILURE", reasons, 90.0
        reasons.append("INSUFFICIENT_DATA")
        return "INSUFFICIENT_DATA", reasons, 10.0

    primary, primary_dist = backups[0]
    gap_ratio = primary_dist / median_nn if median_nn > 0 else 0.0

    # Cascade risk: the primary backup is already serving many neighbours.
    backup_load = neighbour_loads.get(primary.sid, 0)
    if backup_load >= 4:
        reasons.append("PRIMARY_BACKUP_OVERLOADED")
    if not target.has_backup_link:
        reasons.append("NO_REDUNDANT_LINK")

    base: float
    verdict: str
    if gap_ratio >= 3.0:
        reasons.append("BACKUP_VERY_DISTANT")
        verdict = "ISOLATED_FAILURE"
        base = 85.0 + min(10.0, gap_ratio)
    elif backup_load >= 4 and gap_ratio >= 1.5:
        verdict = "CASCADE_RISK"
        base = 75.0 + min(10.0, backup_load * 1.5)
    elif gap_ratio >= 2.0:
        reasons.append("BACKUP_DISTANT")
        verdict = "DEGRADED_COVERAGE"
        base = 55.0 + min(15.0, (gap_ratio - 2.0) * 8.0)
    elif len(backups) >= 3 and gap_ratio <= 1.0:
        reasons.append("DENSE_NEIGHBOURHOOD")
        verdict = "REDUNDANT_OUTAGE"
        base = 15.0
    else:
        verdict = "GRACEFUL_FAILOVER"
        base = 35.0

    # Criticality amplification (1..5 → -10..+10 on top of base).
    base += (target.criticality - 3) * 5.0

    # has_backup_link dampens by 8 if true.
    if target.has_backup_link and verdict not in {"ISOLATED_FAILURE", "CASCADE_RISK"}:
        base -= 8.0

    return verdict, reasons, base


def _priority_bucket(score: float, verdict: str) -> str:
    if verdict in {"ISOLATED_FAILURE", "CASCADE_RISK"}:
        return "P0"
    if score >= 60.0:
        return "P1"
    if score >= 35.0:
        return "P2"
    return "P3"


def _grade(portfolio_score: float, p0_count: int, critical_p0: int) -> str:
    if critical_p0 >= 1 or p0_count >= 3 or portfolio_score >= 75.0:
        return "F"
    if p0_count >= 1 or portfolio_score >= 55.0:
        return "D"
    if portfolio_score >= 35.0:
        return "C"
    if portfolio_score >= 18.0:
        return "B"
    return "A"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(
    sensors: Sequence[Any],
    *,
    risk_appetite: str = "balanced",
    k_backups: int = 3,
    now_fn: Callable[[], datetime] = _utc_now,
) -> FailoverReport:
    """Run the outage simulation across the whole fleet.

    ``sensors`` may be a sequence of :class:`FleetSensor` or dict records
    consumed by :meth:`FleetSensor.from_record`.
    """

    if risk_appetite not in _RISK_APPETITES:
        raise ValueError(f"risk_appetite must be one of {_RISK_APPETITES}")
    if k_backups < 1:
        raise ValueError("k_backups must be >= 1")

    # Defensive deep copy + normalize.
    parsed: List[FleetSensor] = []
    seen: Dict[str, int] = {}
    for idx, raw in enumerate(sensors):
        if isinstance(raw, FleetSensor):
            s = FleetSensor(**asdict(raw))
        elif isinstance(raw, dict):
            s = FleetSensor.from_record(raw, default_sid=f"s{idx}")
        else:
            raise TypeError(f"Unsupported sensor record at index {idx}: {type(raw)!r}")
        if s.sid in seen:
            raise ValueError(f"duplicate sensor id: {s.sid!r}")
        seen[s.sid] = idx
        parsed.append(s)

    n = len(parsed)
    now = now_fn().astimezone(timezone.utc).isoformat()

    if n < 2:
        return FailoverReport(
            generated_at=now,
            sensor_count=n,
            median_nn_distance=0.0,
            portfolio_risk_score=0.0,
            grade="A" if n == 0 else "D",
            risk_appetite=risk_appetite,
            scenarios=[
                OutageScenario(
                    sid=parsed[0].sid if n == 1 else "",
                    verdict="INSUFFICIENT_DATA",
                    priority="P3",
                    priority_score=10.0,
                    coverage_gap=0.0,
                    coverage_gap_ratio=0.0,
                    primary_backup=None,
                    reasons=["FLEET_TOO_SMALL"],
                )
            ] if n == 1 else [],
            neighbour_load=[],
            playbook=[{
                "id": "BOOTSTRAP_FLEET",
                "priority": "P1",
                "label": "Add at least 2 sensors to enable failover analysis",
                "reason": "Failover requires neighbouring coverage",
                "owner": "ops",
                "blast_radius": 2,
                "reversibility": "high",
                "related_sids": [s.sid for s in parsed],
            }],
            insights=["FLEET_TOO_SMALL" if n == 1 else "EMPTY_FLEET"],
            headline=f"VERDICT: grade={'D' if n==1 else 'A'} N={n} insufficient for failover",
        )

    # Nearest-neighbour distance per sensor (k=1).
    nn_distances: List[float] = []
    for s in parsed:
        nearest = _nearest_neighbours(s, parsed, k=1)
        if nearest:
            nn_distances.append(nearest[0][1])
    median_nn = _median(nn_distances)

    # First pass: who is the primary backup for each sensor? Count loads.
    neighbour_loads: Dict[str, int] = {s.sid: 0 for s in parsed}
    primary_map: Dict[str, str] = {}
    for s in parsed:
        nn = _nearest_neighbours(s, parsed, k=1)
        if nn:
            primary_map[s.sid] = nn[0][0].sid
            neighbour_loads[nn[0][0].sid] += 1

    # Per-sensor outage scenarios.
    mult = _risk_multiplier(risk_appetite)
    scenarios: List[OutageScenario] = []
    for target in sorted(parsed, key=lambda s: s.sid):
        backups = _nearest_neighbours(target, parsed, k=k_backups)
        verdict, reasons, base = _verdict_for(target, backups, median_nn, neighbour_loads)
        score = max(0.0, min(100.0, base * mult))
        priority = _priority_bucket(score, verdict)

        backup_dicts: List[Dict[str, Any]] = []
        for i, (b, d) in enumerate(backups):
            role = "PRIMARY_BACKUP" if i == 0 else ("SECONDARY_BACKUP" if i == 1 else "TERTIARY_BACKUP")
            backup_dicts.append({
                "sid": b.sid,
                "distance": round(d, 4),
                "distance_ratio": round(d / median_nn, 3) if median_nn > 0 else 0.0,
                "role": role,
                "criticality": b.criticality,
                "current_load": neighbour_loads.get(b.sid, 0),
            })

        primary = backups[0][0] if backups else None
        gap = backups[0][1] if backups else 0.0
        gap_ratio = gap / median_nn if median_nn > 0 else 0.0
        scenarios.append(OutageScenario(
            sid=target.sid,
            verdict=verdict,
            priority=priority,
            priority_score=round(score, 2),
            coverage_gap=round(gap, 4),
            coverage_gap_ratio=round(gap_ratio, 3),
            primary_backup=primary.sid if primary else None,
            backups=backup_dicts,
            reasons=sorted(set(reasons)),
        ))

    # Re-sort scenarios deterministic: priority (P0..P3), score desc, sid asc.
    priority_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    scenarios.sort(key=lambda sc: (priority_rank.get(sc.priority, 9), -sc.priority_score, sc.sid))

    # Neighbour load summary: who is on the hook for the most failovers?
    neighbour_load_rows: List[Dict[str, Any]] = []
    sensor_by_sid = {s.sid: s for s in parsed}
    for sid in sorted(neighbour_loads, key=lambda k: (-neighbour_loads[k], k)):
        load = neighbour_loads[sid]
        if load == 0:
            continue
        s = sensor_by_sid[sid]
        role = (
            "OVERLOADED_AFTER_FAILOVER" if load >= 4 else (
                "PRIMARY_BACKUP" if load >= 2 else "PRIMARY_BACKUP"
            )
        )
        neighbour_load_rows.append({
            "sid": sid,
            "criticality": s.criticality,
            "primary_for_count": load,
            "role": role,
        })

    # Portfolio risk: top-3 mean (heavier on the worst).
    top_scores = sorted([sc.priority_score for sc in scenarios], reverse=True)[:3]
    portfolio = (sum(top_scores) / len(top_scores)) if top_scores else 0.0

    p0_count = sum(1 for sc in scenarios if sc.priority == "P0")
    p1_count = sum(1 for sc in scenarios if sc.priority == "P1")
    p2_count = sum(1 for sc in scenarios if sc.priority == "P2")
    critical_p0 = sum(1 for sc in scenarios if sc.priority == "P0" and sensor_by_sid[sc.sid].criticality >= 4)
    grade = _grade(portfolio, p0_count, critical_p0)

    # Playbook
    playbook = _build_playbook(scenarios, neighbour_load_rows, sensor_by_sid, grade, risk_appetite)

    # Insights
    insights = _build_insights(scenarios, neighbour_load_rows, sensor_by_sid)

    headline = (
        f"VERDICT: grade={grade} N={n} P0={p0_count} P1={p1_count} "
        f"portfolio_risk={portfolio:.1f} median_nn={median_nn:.3f}"
    )

    return FailoverReport(
        generated_at=now,
        sensor_count=n,
        median_nn_distance=round(median_nn, 4),
        portfolio_risk_score=round(portfolio, 2),
        grade=grade,
        risk_appetite=risk_appetite,
        scenarios=scenarios,
        neighbour_load=neighbour_load_rows,
        playbook=playbook,
        insights=insights,
        headline=headline,
    )


def _build_playbook(
    scenarios: List[OutageScenario],
    neighbour_load: List[Dict[str, Any]],
    sensor_by_sid: Dict[str, FleetSensor],
    grade: str,
    risk_appetite: str,
) -> List[Dict[str, Any]]:
    p0_sids = [sc.sid for sc in scenarios if sc.priority == "P0"]
    p1_sids = [sc.sid for sc in scenarios if sc.priority == "P1"]
    isolated = [sc.sid for sc in scenarios if sc.verdict == "ISOLATED_FAILURE"]
    cascade = [sc.sid for sc in scenarios if sc.verdict == "CASCADE_RISK"]
    degraded = [sc.sid for sc in scenarios if sc.verdict == "DEGRADED_COVERAGE"]
    no_backup_link = [sc.sid for sc in scenarios if "NO_REDUNDANT_LINK" in sc.reasons and sc.priority in {"P0", "P1"}]
    overloaded = [row["sid"] for row in neighbour_load if row["primary_for_count"] >= 4]

    actions: List[Dict[str, Any]] = []

    if isolated:
        actions.append({
            "id": "DEPLOY_REDUNDANT_SENSOR_NEAR_ISOLATED",
            "priority": "P0",
            "label": f"Deploy redundant sensor near {len(isolated)} isolated SPOF site(s)",
            "reason": "Primary backup is too far away; outage would leave a large coverage gap",
            "owner": "field_team",
            "blast_radius": 3,
            "reversibility": "medium",
            "related_sids": sorted(isolated),
        })

    if cascade:
        actions.append({
            "id": "SHED_LOAD_FROM_CASCADE_BACKUPS",
            "priority": "P0",
            "label": f"Pre-stage relief for {len(cascade)} cascade-risk sensor(s)",
            "reason": "Primary backup is already saturated; failover would cascade",
            "owner": "ops",
            "blast_radius": 4,
            "reversibility": "high",
            "related_sids": sorted(cascade),
        })

    if no_backup_link:
        actions.append({
            "id": "PROVISION_REDUNDANT_COMMS_LINK",
            "priority": "P1",
            "label": f"Provision redundant comms link for {len(no_backup_link)} high-risk sensor(s)",
            "reason": "These sensors are high-priority but lack a backup communication link",
            "owner": "procurement",
            "blast_radius": 2,
            "reversibility": "high",
            "related_sids": sorted(no_backup_link),
        })

    if degraded:
        actions.append({
            "id": "REVIEW_DEGRADED_COVERAGE_PLAN",
            "priority": "P1",
            "label": f"Document fallback for {len(degraded)} degraded-coverage sensor(s)",
            "reason": "Backup exists but coverage gap is >= 2x median nearest neighbour",
            "owner": "ops",
            "blast_radius": 2,
            "reversibility": "high",
            "related_sids": sorted(degraded),
        })

    if overloaded:
        actions.append({
            "id": "REBALANCE_OVERLOADED_BACKUPS",
            "priority": "P1",
            "label": f"Rebalance load away from {len(overloaded)} over-subscribed backup(s)",
            "reason": "Single sensor is the primary backup for 4+ peers",
            "owner": "ops",
            "blast_radius": 3,
            "reversibility": "high",
            "related_sids": sorted(overloaded),
        })

    if p1_sids and not (isolated or cascade):
        actions.append({
            "id": "TABLETOP_FAILOVER_DRILL",
            "priority": "P2",
            "label": "Run a tabletop failover drill for the top-risk sensors",
            "reason": "P1 risks exist without immediate P0 exposure; verify runbooks before they fire",
            "owner": "shift_lead",
            "blast_radius": 1,
            "reversibility": "high",
            "related_sids": sorted(p1_sids)[:5],
        })

    if risk_appetite == "cautious" and grade in {"C", "D", "F"}:
        actions.append({
            "id": "SCHEDULE_FAILOVER_AUDIT",
            "priority": "P2",
            "label": "Schedule a fleet-wide failover audit this quarter",
            "reason": f"Risk appetite is cautious and grade is {grade}",
            "owner": "data_steward",
            "blast_radius": 1,
            "reversibility": "high",
            "related_sids": [],
        })

    if not actions:
        actions.append({
            "id": "MAINTAIN_OBSERVABILITY",
            "priority": "P3",
            "label": "Maintain failover observability",
            "reason": "Fleet is healthy; keep monitoring nearest-neighbour gaps",
            "owner": "data_steward",
            "blast_radius": 1,
            "reversibility": "high",
            "related_sids": [],
        })
    elif risk_appetite == "aggressive":
        # Trim P3 fallbacks when other actions present (none here, but stay consistent with siblings).
        actions = [a for a in actions if a["priority"] != "P3" or len(actions) == 1]

    # Stable order: priority asc, id asc.
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    actions.sort(key=lambda a: (order.get(a["priority"], 9), a["id"]))
    return actions


def _build_insights(
    scenarios: List[OutageScenario],
    neighbour_load: List[Dict[str, Any]],
    sensor_by_sid: Dict[str, FleetSensor],
) -> List[str]:
    insights: List[str] = []
    isolated = sum(1 for sc in scenarios if sc.verdict == "ISOLATED_FAILURE")
    cascade = sum(1 for sc in scenarios if sc.verdict == "CASCADE_RISK")
    degraded = sum(1 for sc in scenarios if sc.verdict == "DEGRADED_COVERAGE")
    redundant = sum(1 for sc in scenarios if sc.verdict == "REDUNDANT_OUTAGE")
    critical = sum(
        1 for sc in scenarios
        if sc.priority == "P0" and sensor_by_sid[sc.sid].criticality >= 4
    )
    overloaded = sum(1 for row in neighbour_load if row["primary_for_count"] >= 4)

    if isolated >= 1:
        insights.append(f"SINGLE_POINTS_OF_FAILURE:{isolated}")
    if cascade >= 1:
        insights.append(f"CASCADE_RISK_NODES:{cascade}")
    if critical >= 1:
        insights.append(f"CRITICAL_ASSET_SPOF:{critical}")
    if overloaded >= 1:
        insights.append(f"OVERSUBSCRIBED_BACKUPS:{overloaded}")
    if degraded >= 2:
        insights.append(f"WIDE_COVERAGE_GAPS:{degraded}")
    if redundant >= max(2, len(scenarios) // 2):
        insights.append("DENSE_REDUNDANCY")
    if not insights:
        insights.append("FLEET_FAILOVER_HEALTHY")
    return insights


# ---------------------------------------------------------------------------
# Simulation: project portfolio risk after applying top-N actions
# ---------------------------------------------------------------------------

def simulate(report: FailoverReport, apply_top_n: int = 0) -> Dict[str, Any]:
    """Project portfolio_risk after applying the top-N playbook actions.

    Pure: never mutates ``report``. Returns a small dict with the
    projected score, projected grade, and the list of applied action
    ids.
    """

    if apply_top_n < 0:
        raise ValueError("apply_top_n must be >= 0")
    applied = report.playbook[:apply_top_n]
    score = report.portfolio_risk_score
    # Per-action risk reduction with diminishing returns.
    weights = {
        "DEPLOY_REDUNDANT_SENSOR_NEAR_ISOLATED": 25.0,
        "SHED_LOAD_FROM_CASCADE_BACKUPS": 18.0,
        "PROVISION_REDUNDANT_COMMS_LINK": 12.0,
        "REVIEW_DEGRADED_COVERAGE_PLAN": 8.0,
        "REBALANCE_OVERLOADED_BACKUPS": 10.0,
        "TABLETOP_FAILOVER_DRILL": 3.0,
        "SCHEDULE_FAILOVER_AUDIT": 2.0,
        "MAINTAIN_OBSERVABILITY": 0.0,
    }
    for i, action in enumerate(applied):
        w = weights.get(action["id"], 1.0)
        score = max(5.0, score - w * (0.85 ** i))

    # Approximate projected grade using same thresholds with no P0 to flag.
    if score >= 75:
        grade = "F"
    elif score >= 55:
        grade = "D"
    elif score >= 35:
        grade = "C"
    elif score >= 18:
        grade = "B"
    else:
        grade = "A"
    return {
        "applied": [a["id"] for a in applied],
        "before_portfolio_risk": report.portfolio_risk_score,
        "after_portfolio_risk": round(score, 2),
        "before_grade": report.grade,
        "after_grade": grade,
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def to_json(report: FailoverReport) -> str:
    payload = _as_json_dict(report)
    return json.dumps(payload, sort_keys=True, indent=2, default=str)


def _as_json_dict(report: FailoverReport) -> Dict[str, Any]:
    d = asdict(report)
    # OutageScenario subobjects already dataclasses -> dict via asdict.
    return d


def to_text(report: FailoverReport) -> str:
    lines = [report.headline, ""]
    lines.append(
        f"sensors={report.sensor_count} median_nn={report.median_nn_distance:.3f} "
        f"portfolio_risk={report.portfolio_risk_score:.1f} grade={report.grade} "
        f"risk_appetite={report.risk_appetite}"
    )
    lines.append("")
    lines.append("Top scenarios:")
    for sc in report.scenarios[:10]:
        lines.append(
            f"  [{sc.priority}] {sc.sid:<10} {sc.verdict:<22} "
            f"score={sc.priority_score:5.1f} gap={sc.coverage_gap:.3f} "
            f"(x{sc.coverage_gap_ratio:.2f}) backup={sc.primary_backup or '-'}"
        )
    lines.append("")
    lines.append("Playbook:")
    for action in report.playbook:
        lines.append(f"  [{action['priority']}] {action['id']}: {action['label']}")
    lines.append("")
    lines.append("Insights:")
    for ins in report.insights:
        lines.append(f"  - {ins}")
    return "\n".join(lines)


def to_markdown(report: FailoverReport) -> str:
    lines: List[str] = []
    lines.append(f"# Failover Advisor — grade {report.grade}")
    lines.append("")
    lines.append(report.headline)
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| sensors | {report.sensor_count} |")
    lines.append(f"| median nearest neighbour | {report.median_nn_distance:.4f} |")
    lines.append(f"| portfolio risk | {report.portfolio_risk_score:.2f} |")
    lines.append(f"| grade | {report.grade} |")
    lines.append(f"| risk appetite | {report.risk_appetite} |")
    lines.append(f"| generated at | {report.generated_at} |")
    lines.append("")

    lines.append("## Scenarios")
    lines.append("")
    lines.append("| Priority | Sensor | Verdict | Score | Gap | Gap ratio | Primary backup | Reasons |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for sc in report.scenarios:
        reasons = ", ".join(sc.reasons) if sc.reasons else "-"
        lines.append(
            f"| {sc.priority} | {sc.sid} | {sc.verdict} | {sc.priority_score:.1f} "
            f"| {sc.coverage_gap:.3f} | {sc.coverage_gap_ratio:.2f} "
            f"| {sc.primary_backup or '-'} | {reasons} |"
        )
    lines.append("")

    lines.append("## Neighbour load")
    lines.append("")
    if report.neighbour_load:
        lines.append("| Sensor | Criticality | Primary for | Role |")
        lines.append("| --- | --- | --- | --- |")
        for row in report.neighbour_load:
            lines.append(
                f"| {row['sid']} | {row['criticality']} | {row['primary_for_count']} | {row['role']} |"
            )
    else:
        lines.append("_No neighbour load to report._")
    lines.append("")

    lines.append("## Playbook")
    lines.append("")
    lines.append("| Priority | ID | Owner | Blast | Reversibility | Label | Reason |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for a in report.playbook:
        lines.append(
            f"| {a['priority']} | {a['id']} | {a['owner']} | {a['blast_radius']} "
            f"| {a['reversibility']} | {a['label']} | {a['reason']} |"
        )
    lines.append("")

    lines.append("## Insights")
    lines.append("")
    for ins in report.insights:
        lines.append(f"- {ins}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo_sensors() -> List[FleetSensor]:
    return [
        FleetSensor("alpha", 0.0, 0.0, criticality=4),
        FleetSensor("bravo", 1.0, 0.0, criticality=3, has_backup_link=True),
        FleetSensor("charlie", 0.0, 1.0, criticality=3),
        FleetSensor("delta", 1.0, 1.0, criticality=3),
        FleetSensor("echo", 5.0, 5.0, criticality=5),  # isolated critical asset
        FleetSensor("foxtrot", 1.2, 0.2, criticality=2),
        FleetSensor("golf", 0.2, 1.2, criticality=2),
    ]


# ---------------------------------------------------------------------------
# CSV ingest
# ---------------------------------------------------------------------------

def _load_csv(path: str) -> List[FleetSensor]:
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        records = list(reader)
    return [FleetSensor.from_record(r, default_sid=f"s{idx}") for idx, r in enumerate(records)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _ensure_utf8_stdout() -> None:
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="VoronoiMap failover/outage impact advisor",
    )
    parser.add_argument("csv", nargs="?", help="CSV of sensors (sid,x,y[,criticality,has_backup_link])")
    parser.add_argument("--demo", action="store_true", help="Use built-in 7-sensor demo fleet")
    parser.add_argument("--risk", default="balanced", choices=list(_RISK_APPETITES))
    parser.add_argument("--format", "-f", default="text", choices=["text", "md", "markdown", "json"])
    parser.add_argument("--output", "-o", help="Write to file instead of stdout")
    parser.add_argument("--k-backups", type=int, default=3, help="Number of backup neighbours to surface per sensor (default 3)")
    args = parser.parse_args(argv)

    if not args.csv and not args.demo:
        parser.error("provide a CSV path or --demo")
    sensors: List[FleetSensor]
    if args.demo:
        sensors = _demo_sensors()
    else:
        sensors = _load_csv(args.csv)

    report = analyze(sensors, risk_appetite=args.risk, k_backups=args.k_backups)
    fmt = args.format
    if fmt == "markdown":
        fmt = "md"
    if fmt == "text":
        out = to_text(report)
    elif fmt == "md":
        out = to_markdown(report)
    else:
        out = to_json(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
