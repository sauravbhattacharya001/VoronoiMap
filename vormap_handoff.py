#!/usr/bin/env python3
"""vormap_handoff - Agentic Shift Handoff Advisor.

Operational sibling to vormap_curator / vormap_redundancy /
vormap_sensorplanner / vormap_drift / vormap_brief / vormap_doctor.

Where those modules answer "what is wrong with my data?" or "where
do I place the next sensor?", this advisor answers a different
operational question:

    "My shift is ending. What does the incoming shift need to know,
     what's on fire, and what should they touch first?"

It fuses up to five operational signal streams from a spatial
sensor / survey / measurement network into a single briefing:

* ``incidents``         - unresolved field problems.
* ``pending_visits``    - scheduled crew visits.
* ``recent_drift``      - drift findings from vormap_drift.
* ``coverage_gaps``     - spatial gaps from vormap_sensorplanner.
* ``weather_blockers``  - regions temporarily off-limits.

The output is a :class:`HandoffReport` with per-item verdicts, a
P0-first deduped playbook, cross-cutting insights, an A-F grade,
and a one-line headline suitable for a Slack post.

The module is pure stdlib (numpy not required) and deterministic
given an injectable ``now_fn`` callable.

CLI::

    python vormap_handoff.py --incidents inc.json --visits v.json \\
        --drift drift.json --gaps gaps.json --weather w.json \\
        --crew alice,bob --risk cautious --format md --output handoff.md

"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_OWNERS = {
    "shift_lead",
    "field_team",
    "data_steward",
    "ops",
    "on_call",
    "safety_officer",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    """Best-effort datetime coercion. Returns None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Allow trailing Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def _hours_between(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0


def _coerce_point(p: Any) -> Optional[Tuple[float, float]]:
    if p is None:
        return None
    try:
        x, y = p[0], p[1]
        return float(x), float(y)
    except (TypeError, ValueError, IndexError):
        return None


def _appetite_mult(risk: str) -> float:
    if risk == "cautious":
        return 1.15
    if risk == "aggressive":
        return 0.85
    return 1.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class HandoffItem:
    id: str
    kind: str
    source: str  # 'incident' / 'visit' / 'drift' / 'gap' / 'weather'
    verdict: str
    priority: str  # P0..P3
    priority_score: float
    point: Optional[Tuple[float, float]] = None
    owner: str = "shift_lead"
    reasons: List[Dict[str, str]] = field(default_factory=list)
    blast_radius: int = 2
    reversibility: str = "medium"
    extra: Dict[str, Any] = field(default_factory=dict)


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
class HandoffReport:
    handoff_score: float
    grade: str
    summary: str
    risk_appetite: str
    generated_at: str
    items: List[HandoffItem] = field(default_factory=list)
    playbook: List[PlaybookAction] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Advisor
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


def _bucket(score: float) -> str:
    if score >= 75:
        return "P0"
    if score >= 55:
        return "P1"
    if score >= 30:
        return "P2"
    return "P3"


class HandoffAdvisor:
    """Synthesises a shift-handoff briefing from operational signals."""

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn or _utc_now

    # -- public ------------------------------------------------------------

    def scan(
        self,
        incidents: Optional[Sequence[Dict[str, Any]]] = None,
        pending_visits: Optional[Sequence[Dict[str, Any]]] = None,
        recent_drift: Optional[Sequence[Dict[str, Any]]] = None,
        coverage_gaps: Optional[Sequence[Dict[str, Any]]] = None,
        weather_blockers: Optional[Sequence[Dict[str, Any]]] = None,
        crew_available: Optional[Sequence[str]] = None,
        risk_appetite: str = "balanced",
    ) -> HandoffReport:
        if risk_appetite not in ("cautious", "balanced", "aggressive"):
            raise ValueError(
                f"risk_appetite must be cautious|balanced|aggressive, got {risk_appetite!r}"
            )

        # Deep-copy inputs we'll consult so we never mutate caller state.
        incidents = copy.deepcopy(list(incidents or []))
        pending_visits = copy.deepcopy(list(pending_visits or []))
        recent_drift = copy.deepcopy(list(recent_drift or []))
        coverage_gaps = copy.deepcopy(list(coverage_gaps or []))
        weather_blockers = copy.deepcopy(list(weather_blockers or []))
        crew_list = (
            [str(c) for c in crew_available] if crew_available is not None else None
        )

        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        mult = _appetite_mult(risk_appetite)

        items: List[HandoffItem] = []

        items.extend(self._classify_incidents(incidents, now, mult))
        items.extend(
            self._classify_visits(
                pending_visits, incidents, weather_blockers, crew_list, now, mult
            )
        )
        items.extend(self._classify_drift(recent_drift, mult))
        items.extend(self._classify_gaps(coverage_gaps, mult))
        items.extend(self._classify_weather(weather_blockers, mult))

        # Sort items deterministically: priority then source then id
        items.sort(key=lambda it: (_PRIORITY_ORDER[it.priority], it.source, it.id))

        playbook = self._build_playbook(items, crew_list, risk_appetite)
        insights = self._build_insights(items, incidents, crew_list)

        metrics = self._build_metrics(items)

        score = self._score(items, insights, risk_appetite)
        grade = self._grade(score, items, crew_list, risk_appetite)
        playbook = self._tune_playbook(playbook, grade, risk_appetite)

        summary = self._summary(score, grade, items, weather_blockers)

        return HandoffReport(
            handoff_score=score,
            grade=grade,
            summary=summary,
            risk_appetite=risk_appetite,
            generated_at=now.isoformat(),
            items=items,
            playbook=playbook,
            insights=insights,
            metrics=metrics,
        )

    # -- classifiers -------------------------------------------------------

    def _classify_incidents(
        self,
        incidents: List[Dict[str, Any]],
        now: datetime,
        mult: float,
    ) -> List[HandoffItem]:
        out: List[HandoffItem] = []
        for idx, raw in enumerate(incidents):
            iid = str(raw.get("id", f"inc#{idx}"))
            kind = str(raw.get("kind", "unknown"))
            try:
                sev = int(raw.get("severity", 1))
            except (TypeError, ValueError):
                sev = 1
            sev = max(1, min(5, sev))
            opened = _parse_dt(raw.get("opened_at"))
            age_h = _hours_between(now, opened) if opened else 0.0
            point = _coerce_point(raw.get("point"))

            reasons: List[Dict[str, str]] = []
            verdict = "MONITOR"
            base_score = 0.0

            if sev <= 1:
                verdict = "MONITOR"
                base_score = 12.0
                reasons.append({"code": "LOW_SEVERITY", "message": f"severity {sev}"})
            elif sev == 2:
                verdict = "TRIAGE"
                base_score = 34.0
                reasons.append({"code": "MEDIUM_SEVERITY", "message": "severity 2"})
            elif sev == 3:
                verdict = "DISPATCH_TODAY"
                base_score = 56.0
                reasons.append({"code": "ELEVATED_SEVERITY", "message": "severity 3"})
            else:  # sev >= 4
                verdict = "DISPATCH_TODAY"
                base_score = 68.0
                reasons.append(
                    {"code": "HIGH_SEVERITY", "message": f"severity {sev}"}
                )

            # Age pressure
            if age_h >= 12:
                base_score += min(20.0, 1.5 * (age_h - 12))
                reasons.append(
                    {
                        "code": "AGED_INCIDENT",
                        "message": f"open for {age_h:.1f}h",
                    }
                )

            # Escalation
            critical_kinds = {"power_failure", "comm_failure"}
            escalate = (sev >= 4 and age_h >= 12) or (
                kind in critical_kinds and sev >= 3
            )
            if escalate:
                verdict = "ESCALATE_NOW"
                base_score = max(base_score, 80.0)
                reasons.append(
                    {
                        "code": "ESCALATION_TRIGGERED",
                        "message": "severe + sustained or critical-class outage",
                    }
                )

            # Stale incidents
            stale = sev >= 2 and age_h >= 72
            if stale:
                verdict = "STALE_INCIDENT"
                base_score = max(base_score, 82.0)
                reasons.append(
                    {
                        "code": "STALE_BACKLOG",
                        "message": f"{age_h:.0f}h with no closure",
                    }
                )

            score = _clamp(base_score * mult)
            prio = _bucket(score)
            if verdict in ("ESCALATE_NOW", "STALE_INCIDENT"):
                prio = "P0"

            owner = "on_call" if verdict == "ESCALATE_NOW" else "field_team"
            if verdict == "MONITOR":
                owner = "shift_lead"
            if kind == "access_blocked":
                owner = "safety_officer"

            extra = {
                "severity": sev,
                "kind": kind,
                "age_hours": round(age_h, 2),
                "notes": str(raw.get("notes", "")) if raw.get("notes") else "",
            }

            out.append(
                HandoffItem(
                    id=iid,
                    kind=kind,
                    source="incident",
                    verdict=verdict,
                    priority=prio,
                    priority_score=round(score, 2),
                    point=point,
                    owner=owner,
                    reasons=reasons,
                    blast_radius=min(5, 1 + sev),
                    reversibility="low" if sev >= 4 else "medium",
                    extra=extra,
                )
            )
        return out

    def _classify_visits(
        self,
        visits: List[Dict[str, Any]],
        incidents: List[Dict[str, Any]],
        weather: List[Dict[str, Any]],
        crew_list: Optional[List[str]],
        now: datetime,
        mult: float,
    ) -> List[HandoffItem]:
        out: List[HandoffItem] = []

        # Build incident point index for proximity checks
        inc_points: List[Tuple[Tuple[float, float], int]] = []
        for raw in incidents:
            p = _coerce_point(raw.get("point"))
            try:
                sev = int(raw.get("severity", 1))
            except (TypeError, ValueError):
                sev = 1
            if p is not None:
                inc_points.append((p, sev))

        for idx, raw in enumerate(visits):
            vid = str(raw.get("id", f"visit#{idx}"))
            point = _coerce_point(raw.get("point"))
            purpose = str(raw.get("purpose", "inspect"))
            owner_name = str(raw.get("owner", ""))
            sched = _parse_dt(raw.get("scheduled_for"))
            try:
                duration = int(raw.get("duration_min", 60))
            except (TypeError, ValueError):
                duration = 60

            reasons: List[Dict[str, str]] = []
            verdict = "GO_AS_PLANNED"
            base_score = 22.0

            # Weather hold
            weather_hit = None
            if point is not None:
                for w in weather:
                    bbox = w.get("region_bbox") or w.get("bbox")
                    if not bbox or len(bbox) != 4:
                        continue
                    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
                    until = _parse_dt(w.get("until"))
                    if (
                        xmin <= point[0] <= xmax
                        and ymin <= point[1] <= ymax
                        and (sched is None or until is None or sched < until)
                    ):
                        weather_hit = w
                        break
            if weather_hit is not None:
                verdict = "WEATHER_HOLD"
                base_score = 62.0
                reasons.append(
                    {
                        "code": "WEATHER_BLOCKER",
                        "message": f"visit point inside {weather_hit.get('kind', 'weather')} region",
                    }
                )

            # Owner unavailable
            if verdict == "GO_AS_PLANNED" and crew_list is not None and owner_name:
                if owner_name not in crew_list:
                    verdict = "OWNER_UNAVAILABLE"
                    base_score = 58.0
                    reasons.append(
                        {
                            "code": "OWNER_NOT_ON_SHIFT",
                            "message": f"owner {owner_name} not in crew_available",
                        }
                    )

            # Overdue
            if verdict == "GO_AS_PLANNED" and sched is not None and sched < now:
                verdict = "OVERDUE_VISIT"
                hours_late = _hours_between(now, sched)
                base_score = 50.0 + min(20.0, hours_late * 1.0)
                reasons.append(
                    {
                        "code": "OVERDUE",
                        "message": f"scheduled {hours_late:.1f}h ago",
                    }
                )

            # Reschedule near hot incident
            if verdict == "GO_AS_PLANNED" and point is not None:
                for (ip, isev) in inc_points:
                    if isev >= 3 and math.hypot(ip[0] - point[0], ip[1] - point[1]) <= 50.0:
                        verdict = "RESCHEDULE_RECOMMENDED"
                        base_score = 55.0
                        reasons.append(
                            {
                                "code": "INCIDENT_NEARBY",
                                "message": "high-sev incident within 50 units",
                            }
                        )
                        break

            if verdict == "GO_AS_PLANNED":
                reasons.append(
                    {"code": "ON_PLAN", "message": f"{purpose} as scheduled"}
                )

            score = _clamp(base_score * mult)
            prio = _bucket(score)

            owner = "field_team"
            if verdict == "WEATHER_HOLD":
                owner = "safety_officer"
            elif verdict == "OWNER_UNAVAILABLE":
                owner = "ops"

            extra = {
                "purpose": purpose,
                "owner_name": owner_name,
                "scheduled_for": sched.isoformat() if sched else None,
                "duration_min": duration,
            }

            out.append(
                HandoffItem(
                    id=vid,
                    kind=purpose,
                    source="visit",
                    verdict=verdict,
                    priority=prio,
                    priority_score=round(score, 2),
                    point=point,
                    owner=owner,
                    reasons=reasons,
                    blast_radius=2,
                    reversibility="high",
                    extra=extra,
                )
            )
        return out

    def _classify_drift(
        self,
        drift: List[Dict[str, Any]],
        mult: float,
    ) -> List[HandoffItem]:
        out: List[HandoffItem] = []
        for idx, raw in enumerate(drift):
            did = str(raw.get("point_id", raw.get("id", f"drift#{idx}")))
            try:
                sev = float(raw.get("severity", 0.0))
            except (TypeError, ValueError):
                sev = 0.0
            label = str(raw.get("label", "drift"))
            point = _coerce_point(raw.get("point"))

            if sev >= 70:
                verdict = "DRIFT_INVESTIGATE"
                base_score = 60.0 + min(20.0, (sev - 70) * 0.5)
                reasons = [
                    {"code": "HIGH_DRIFT", "message": f"{label} severity {sev:.0f}"}
                ]
            elif sev >= 40:
                verdict = "DRIFT_WATCH"
                base_score = 38.0
                reasons = [
                    {
                        "code": "MODERATE_DRIFT",
                        "message": f"{label} severity {sev:.0f}",
                    }
                ]
            else:
                continue  # below threshold, skip noise

            score = _clamp(base_score * mult)
            out.append(
                HandoffItem(
                    id=did,
                    kind=label,
                    source="drift",
                    verdict=verdict,
                    priority=_bucket(score),
                    priority_score=round(score, 2),
                    point=point,
                    owner="data_steward",
                    reasons=reasons,
                    blast_radius=2,
                    reversibility="high",
                    extra={"severity": sev, "label": label},
                )
            )
        return out

    def _classify_gaps(
        self,
        gaps: List[Dict[str, Any]],
        mult: float,
    ) -> List[HandoffItem]:
        out: List[HandoffItem] = []
        for idx, raw in enumerate(gaps):
            gid = str(raw.get("id", f"gap#{idx}"))
            try:
                gscore = float(raw.get("gap_score", 0.0))
            except (TypeError, ValueError):
                gscore = 0.0
            label = str(raw.get("label", "coverage_gap"))
            point = _coerce_point(raw.get("point"))

            if gscore >= 70:
                verdict = "GAP_BACKFILL"
                base = 58.0 + min(15.0, (gscore - 70) * 0.4)
                reasons = [
                    {
                        "code": "PRIORITY_GAP",
                        "message": f"gap_score {gscore:.0f}",
                    }
                ]
            elif gscore >= 40:
                verdict = "GAP_NOTE"
                base = 34.0
                reasons = [
                    {
                        "code": "MODERATE_GAP",
                        "message": f"gap_score {gscore:.0f}",
                    }
                ]
            else:
                continue

            score = _clamp(base * mult)
            out.append(
                HandoffItem(
                    id=gid,
                    kind=label,
                    source="gap",
                    verdict=verdict,
                    priority=_bucket(score),
                    priority_score=round(score, 2),
                    point=point,
                    owner="ops",
                    reasons=reasons,
                    blast_radius=2,
                    reversibility="high",
                    extra={"gap_score": gscore, "label": label},
                )
            )
        return out

    def _classify_weather(
        self,
        weather: List[Dict[str, Any]],
        mult: float,
    ) -> List[HandoffItem]:
        out: List[HandoffItem] = []
        for idx, raw in enumerate(weather):
            wid = str(raw.get("id", f"weather#{idx}"))
            try:
                sev = int(raw.get("severity", 1))
            except (TypeError, ValueError):
                sev = 1
            sev = max(1, min(5, sev))
            kind = str(raw.get("kind", "weather"))
            until = _parse_dt(raw.get("until"))
            bbox = raw.get("region_bbox") or raw.get("bbox")
            point = None
            if bbox and len(bbox) == 4:
                try:
                    xmin, ymin, xmax, ymax = (float(v) for v in bbox)
                    point = ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
                except (TypeError, ValueError):
                    point = None

            if sev >= 4:
                verdict = "SUSPEND_FIELDWORK_IN_REGION"
                base = 72.0
            elif sev >= 3:
                verdict = "CAUTION_REGION"
                base = 48.0
            else:
                continue

            score = _clamp(base * mult)
            reasons = [
                {
                    "code": "WEATHER_ADVISORY",
                    "message": f"{kind} severity {sev}"
                    + (f" until {until.isoformat()}" if until else ""),
                }
            ]
            out.append(
                HandoffItem(
                    id=wid,
                    kind=kind,
                    source="weather",
                    verdict=verdict,
                    priority=_bucket(score),
                    priority_score=round(score, 2),
                    point=point,
                    owner="safety_officer",
                    reasons=reasons,
                    blast_radius=min(5, sev + 1),
                    reversibility="high",
                    extra={
                        "severity": sev,
                        "kind": kind,
                        "until": until.isoformat() if until else None,
                        "bbox": list(bbox) if bbox else None,
                    },
                )
            )
        return out

    # -- playbook ----------------------------------------------------------

    def _build_playbook(
        self,
        items: List[HandoffItem],
        crew_list: Optional[List[str]],
        risk_appetite: str,
    ) -> List[PlaybookAction]:
        actions: List[PlaybookAction] = []

        verdict_groups: Dict[str, List[HandoffItem]] = {}
        for it in items:
            verdict_groups.setdefault(it.verdict, []).append(it)

        p0_incidents = [
            it for it in items if it.source == "incident" and it.priority == "P0"
        ]
        escalates = verdict_groups.get("ESCALATE_NOW", [])
        weather_holds = verdict_groups.get("WEATHER_HOLD", [])
        owner_gaps = verdict_groups.get("OWNER_UNAVAILABLE", [])
        overdues = verdict_groups.get("OVERDUE_VISIT", [])
        gap_backfills = verdict_groups.get("GAP_BACKFILL", [])
        drift_investigate = verdict_groups.get("DRIFT_INVESTIGATE", [])
        drift_watch = verdict_groups.get("DRIFT_WATCH", [])
        caution = verdict_groups.get("CAUTION_REGION", [])

        if len(p0_incidents) >= 2:
            actions.append(
                PlaybookAction(
                    id="OPEN_INCIDENT_BRIDGE",
                    priority="P0",
                    label="Open an incident bridge for the handoff",
                    reason=f"{len(p0_incidents)} P0 incidents on the floor",
                    owner="shift_lead",
                    blast_radius=4,
                    reversibility="high",
                    related_ids=sorted(it.id for it in p0_incidents),
                )
            )

        if escalates:
            actions.append(
                PlaybookAction(
                    id="CALL_OUT_ON_CALL_ENGINEER",
                    priority="P0",
                    label="Page the on-call engineer before walking off shift",
                    reason="At least one ESCALATE_NOW incident requires hot handoff",
                    owner="on_call",
                    blast_radius=3,
                    reversibility="medium",
                    related_ids=sorted(it.id for it in escalates),
                )
            )

        if weather_holds:
            actions.append(
                PlaybookAction(
                    id="REROUTE_VISITS_AROUND_WEATHER",
                    priority="P1",
                    label="Re-route or postpone visits inside weather regions",
                    reason=f"{len(weather_holds)} visits sit inside an active weather region",
                    owner="ops",
                    blast_radius=3,
                    reversibility="high",
                    related_ids=sorted(it.id for it in weather_holds),
                )
            )

        if owner_gaps:
            actions.append(
                PlaybookAction(
                    id="BACKFILL_OWNER_GAPS",
                    priority="P1",
                    label="Reassign visits whose owner is not on shift",
                    reason=f"{len(owner_gaps)} visit(s) have an off-shift owner",
                    owner="ops",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=sorted(it.id for it in owner_gaps),
                )
            )

        if len(overdues) >= 2:
            actions.append(
                PlaybookAction(
                    id="SWEEP_OVERDUE_VISITS",
                    priority="P1",
                    label="Sweep overdue visits and confirm new ETAs",
                    reason=f"{len(overdues)} overdue visit(s) on the board",
                    owner="field_team",
                    blast_radius=3,
                    reversibility="high",
                    related_ids=sorted(it.id for it in overdues),
                )
            )

        if len(gap_backfills) >= 3:
            actions.append(
                PlaybookAction(
                    id="BATCH_GAP_BACKFILL",
                    priority="P2",
                    label="Batch coverage-gap backfills into one field run",
                    reason=f"{len(gap_backfills)} priority gaps queued",
                    owner="ops",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=sorted(it.id for it in gap_backfills),
                )
            )

        # Drift cluster: >=3 DRIFT_INVESTIGATE within 5 units of each other
        if len(drift_investigate) >= 3:
            clustered = self._has_drift_cluster(drift_investigate, radius=5.0)
            if clustered:
                actions.append(
                    PlaybookAction(
                        id="AUDIT_DRIFT_CLUSTER",
                        priority="P1",
                        label="Audit clustered drift findings before next ingest",
                        reason="3+ DRIFT_INVESTIGATE items within 5 units of each other",
                        owner="data_steward",
                        blast_radius=3,
                        reversibility="high",
                        related_ids=sorted(it.id for it in drift_investigate),
                    )
                )

        if len(drift_watch) >= 3:
            actions.append(
                PlaybookAction(
                    id="RECALIBRATE_DRIFT_BATCH",
                    priority="P2",
                    label="Schedule batch recalibration for low-grade drift",
                    reason=f"{len(drift_watch)} DRIFT_WATCH items accumulating",
                    owner="data_steward",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=sorted(it.id for it in drift_watch),
                )
            )

        if caution:
            actions.append(
                PlaybookAction(
                    id="MONITOR_WEATHER_WINDOW",
                    priority="P2",
                    label="Monitor weather-caution windows for downgrade",
                    reason=f"{len(caution)} region(s) under caution advisory",
                    owner="safety_officer",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=sorted(it.id for it in caution),
                )
            )

        if len(items) >= 6:
            actions.append(
                PlaybookAction(
                    id="HOLD_SHIFT_HUDDLE",
                    priority="P2",
                    label="Hold a 5-minute shift huddle before walk-off",
                    reason=f"{len(items)} open items to brief",
                    owner="shift_lead",
                    blast_radius=2,
                    reversibility="high",
                    related_ids=[],
                )
            )

        if not actions:
            actions.append(
                PlaybookAction(
                    id="CONFIRM_CREW_BRIEFING",
                    priority="P3",
                    label="Confirm verbal crew briefing and close the shift",
                    reason="No actionable open items",
                    owner="shift_lead",
                    blast_radius=1,
                    reversibility="high",
                    related_ids=[],
                )
            )

        # Dedupe by id (later wins shouldn't happen, but be safe), then sort.
        seen: Dict[str, PlaybookAction] = {}
        for a in actions:
            if a.id not in seen:
                seen[a.id] = a
        deduped = sorted(
            seen.values(), key=lambda a: (_PRIORITY_ORDER[a.priority], a.id)
        )
        return deduped

    def _tune_playbook(
        self,
        playbook: List[PlaybookAction],
        grade: str,
        risk_appetite: str,
    ) -> List[PlaybookAction]:
        out = list(playbook)
        if risk_appetite == "aggressive":
            has_high = any(a.priority in ("P0", "P1") for a in out)
            kept: List[PlaybookAction] = []
            p2 = [a for a in out if a.priority == "P2"]
            for a in out:
                if a.priority == "P3":
                    continue
                if a.priority == "P2" and has_high and len(p2) == 1:
                    continue
                kept.append(a)
            out = kept if kept else out  # keep at least something
        if risk_appetite == "cautious" and grade in ("C", "D", "F"):
            if not any(a.id == "SCHEDULE_FOLLOWUP_AUDIT" for a in out):
                out.append(
                    PlaybookAction(
                        id="SCHEDULE_FOLLOWUP_AUDIT",
                        priority="P2",
                        label="Schedule a follow-up audit next shift",
                        reason=f"Cautious posture: grade {grade}",
                        owner="shift_lead",
                        blast_radius=1,
                        reversibility="high",
                        related_ids=[],
                    )
                )
        return sorted(out, key=lambda a: (_PRIORITY_ORDER[a.priority], a.id))

    # -- insights / metrics ------------------------------------------------

    def _build_insights(
        self,
        items: List[HandoffItem],
        incidents: List[Dict[str, Any]],
        crew_list: Optional[List[str]],
    ) -> List[str]:
        insights: List[str] = []

        if not items:
            insights.append("QUIET_SHIFT")
            return insights

        if len(items) >= 10:
            insights.append("HIGH_LOAD_SHIFT")

        power_comm = sum(
            1 for it in items if it.source == "incident" and it.kind in {"power_failure", "comm_failure"}
        )
        if power_comm >= 2:
            insights.append("POWER_OR_COMM_OUTAGE_CLUSTER")

        stale = sum(1 for it in items if it.verdict == "STALE_INCIDENT")
        if stale >= 2:
            insights.append("STALE_BACKLOG")

        wd = sum(
            1
            for it in items
            if it.verdict in ("WEATHER_HOLD", "SUSPEND_FIELDWORK_IN_REGION")
        )
        if wd >= 3:
            insights.append("WEATHER_DOMINATED")

        if crew_list is not None and len(crew_list) <= 1:
            insights.append("THIN_CREW")

        if any(a == "AUDIT_DRIFT_CLUSTER" for a in []):  # placeholder; computed below
            pass
        drift_invest = [it for it in items if it.verdict == "DRIFT_INVESTIGATE"]
        if len(drift_invest) >= 3 and self._has_drift_cluster(drift_invest, 5.0):
            insights.append("DRIFT_CLUSTER")

        calib = sum(
            1 for it in items if it.source == "incident" and it.kind == "calibration_drift"
        )
        total_inc = sum(1 for it in items if it.source == "incident")
        if total_inc and calib / total_inc >= 0.5 and total_inc >= 2:
            insights.append("CALIBRATION_FOCUSED")

        access = sum(
            1 for it in items if it.source == "incident" and it.kind == "access_blocked"
        )
        if access >= 2:
            insights.append("ACCESS_PROBLEM_PATTERN")

        return sorted(set(insights))

    def _build_metrics(self, items: List[HandoffItem]) -> Dict[str, Any]:
        counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        by_source: Dict[str, int] = {}
        for it in items:
            counts[it.priority] += 1
            by_source[it.source] = by_source.get(it.source, 0) + 1
        return {
            "total_items": len(items),
            "by_priority": counts,
            "by_source": dict(sorted(by_source.items())),
        }

    # -- scoring -----------------------------------------------------------

    def _score(
        self,
        items: List[HandoffItem],
        insights: List[str],
        risk_appetite: str,
    ) -> float:
        counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        for it in items:
            counts[it.priority] += 1
        score = 100.0
        score -= 18.0 * counts["P0"]
        score -= 7.0 * counts["P1"]
        score -= 2.0 * counts["P2"]
        if "WEATHER_DOMINATED" in insights:
            score -= 10.0
        if "STALE_BACKLOG" in insights:
            score -= 8.0
        if risk_appetite == "cautious":
            score -= 5.0
        elif risk_appetite == "aggressive":
            score += 5.0
        return round(_clamp(score), 2)

    def _grade(
        self,
        score: float,
        items: List[HandoffItem],
        crew_list: Optional[List[str]],
        risk_appetite: str,
    ) -> str:
        stale = sum(1 for it in items if it.verdict == "STALE_INCIDENT")
        escalates = sum(1 for it in items if it.verdict == "ESCALATE_NOW")

        forced_f = False
        if stale >= 2:
            forced_f = True
        if (
            risk_appetite == "aggressive"
            and escalates >= 1
            and crew_list is None
        ):
            forced_f = True

        if forced_f or score < 35:
            return "F"
        if score < 50:
            return "D"
        if score < 65:
            return "C"
        if score < 80:
            return "B"
        return "A"

    def _summary(
        self,
        score: float,
        grade: str,
        items: List[HandoffItem],
        weather: List[Dict[str, Any]],
    ) -> str:
        counts = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
        by_source: Dict[str, int] = {}
        for it in items:
            counts[it.priority] += 1
            by_source[it.source] = by_source.get(it.source, 0) + 1
        visits = by_source.get("visit", 0)
        incidents = by_source.get("incident", 0)
        weather_n = sum(
            1 for w in weather if int(w.get("severity", 0) or 0) >= 3
        )
        weather_str = (
            f"; weather hold in {weather_n} region{'s' if weather_n != 1 else ''}"
            if weather_n
            else ""
        )
        return (
            f"HANDOFF {grade}/{int(score)} - {counts['P0']} P0, {counts['P1']} P1; "
            f"{visits} visits + {incidents} incidents{weather_str}"
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _has_drift_cluster(items: List[HandoffItem], radius: float) -> bool:
        pts = [it.point for it in items if it.point is not None]
        n = len(pts)
        if n < 3:
            return False
        # Find any point with >=2 other points within radius (3 close together)
        for i in range(n):
            close = 0
            for j in range(n):
                if i == j:
                    continue
                dx = pts[i][0] - pts[j][0]
                dy = pts[i][1] - pts[j][1]
                if math.hypot(dx, dy) <= radius:
                    close += 1
                    if close >= 2:
                        return True
        return False


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def simulate(report: HandoffReport, apply_top: int = 3) -> HandoffReport:
    """Return a projected report where the top-N playbook actions are applied.

    Each P0 -> -15 from score, P1 -> -8, P2 -> -3, P3 -> -1; with 0.85^i
    diminishing returns. Never mutates the input report.
    """
    if apply_top < 0:
        apply_top = 0
    new = copy.deepcopy(report)
    deltas = {"P0": 15.0, "P1": 8.0, "P2": 3.0, "P3": 1.0}
    score = new.handoff_score
    applied = 0
    for i, action in enumerate(new.playbook[:apply_top]):
        d = deltas.get(action.priority, 0.0) * (0.85 ** i)
        # The handoff score is "higher is better" so applying actions raises it.
        score = _clamp(score + d)
        applied += 1
    new.handoff_score = round(score, 2)
    new.grade = HandoffAdvisor()._grade(
        new.handoff_score, new.items, None, new.risk_appetite
    )
    new.summary = (
        new.summary
        + f" [simulated: top-{applied} actions, projected {new.grade}/{int(new.handoff_score)}]"
    )
    return new


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _serialise_item(it: HandoffItem) -> Dict[str, Any]:
    d = asdict(it)
    if d.get("point") is not None:
        d["point"] = list(d["point"])
    return d


def _serialise_action(a: PlaybookAction) -> Dict[str, Any]:
    return asdict(a)


def to_text(report: HandoffReport) -> str:
    lines: List[str] = []
    lines.append(report.summary)
    lines.append(f"Grade: {report.grade}    Score: {report.handoff_score:.1f}/100")
    lines.append(f"Risk appetite: {report.risk_appetite}")
    lines.append(f"Generated at: {report.generated_at}")
    lines.append("")
    if report.items:
        lines.append("Items:")
        for it in report.items:
            pt = f" @ ({it.point[0]:.2f},{it.point[1]:.2f})" if it.point else ""
            lines.append(
                f"  [{it.priority}] {it.verdict:<32} {it.id} ({it.source}/{it.kind}){pt}"
            )
    else:
        lines.append("Items: (none)")
    lines.append("")
    lines.append("Playbook:")
    for a in report.playbook:
        lines.append(f"  [{a.priority}] {a.label} - {a.reason} (owner: {a.owner})")
    if report.insights:
        lines.append("")
        lines.append("Insights:")
        for ins in report.insights:
            lines.append(f"  - {ins}")
    return "\n".join(lines)


def to_markdown(report: HandoffReport) -> str:
    parts: List[str] = []
    parts.append("# Shift Handoff Briefing")
    parts.append("")
    parts.append("## Summary")
    parts.append("")
    parts.append(f"- **Headline:** {report.summary}")
    parts.append(f"- **Grade:** {report.grade}")
    parts.append(f"- **Handoff score:** {report.handoff_score:.1f}/100")
    parts.append(f"- **Risk appetite:** {report.risk_appetite}")
    parts.append(f"- **Generated at:** {report.generated_at}")
    parts.append("")

    parts.append("## Items")
    parts.append("")
    if report.items:
        parts.append("| Priority | Verdict | ID | Source | Kind | Score | Owner |")
        parts.append("|---|---|---|---|---|---|---|")
        for it in report.items:
            parts.append(
                f"| {it.priority} | {it.verdict} | {it.id} | {it.source} | "
                f"{it.kind} | {it.priority_score:.1f} | {it.owner} |"
            )
    else:
        parts.append("_No open items._")
    parts.append("")

    parts.append("## Playbook")
    parts.append("")
    if report.playbook:
        parts.append("| Priority | Action | Owner | Reason |")
        parts.append("|---|---|---|---|")
        for a in report.playbook:
            parts.append(
                f"| {a.priority} | {a.label} | {a.owner} | {a.reason} |"
            )
    else:
        parts.append("_No actions queued._")
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


def to_json(report: HandoffReport) -> str:
    payload = {
        "handoff_score": report.handoff_score,
        "grade": report.grade,
        "summary": report.summary,
        "risk_appetite": report.risk_appetite,
        "generated_at": report.generated_at,
        "items": [_serialise_item(it) for it in report.items],
        "playbook": [_serialise_action(a) for a in report.playbook],
        "insights": list(report.insights),
        "metrics": report.metrics,
    }
    return json.dumps(payload, sort_keys=True, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_json(path: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array")
    return data


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vormap_handoff",
        description=(
            "Agentic shift-handoff advisor: synthesise incidents, visits, "
            "drift findings, coverage gaps and weather blockers into a "
            "P0-first briefing for the incoming shift."
        ),
    )
    p.add_argument("--incidents", help="JSON file: list of incident dicts")
    p.add_argument("--visits", help="JSON file: list of pending-visit dicts")
    p.add_argument("--drift", help="JSON file: list of recent-drift dicts")
    p.add_argument("--gaps", help="JSON file: list of coverage-gap dicts")
    p.add_argument(
        "--weather", help="JSON file: list of weather-blocker dicts"
    )
    p.add_argument(
        "--crew",
        help="Comma-separated list of crew ids on the incoming shift",
    )
    p.add_argument(
        "--risk",
        choices=("cautious", "balanced", "aggressive"),
        default="balanced",
    )
    p.add_argument(
        "--format",
        choices=("text", "md", "markdown", "json"),
        default="text",
    )
    p.add_argument("--output", help="Write to this file instead of stdout")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Best-effort UTF-8 on Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    incidents = _load_json(args.incidents)
    visits = _load_json(args.visits)
    drift = _load_json(args.drift)
    gaps = _load_json(args.gaps)
    weather = _load_json(args.weather)
    crew = [s.strip() for s in args.crew.split(",")] if args.crew else None

    advisor = HandoffAdvisor()
    report = advisor.scan(
        incidents=incidents,
        pending_visits=visits,
        recent_drift=drift,
        coverage_gaps=gaps,
        weather_blockers=weather,
        crew_available=crew,
        risk_appetite=args.risk,
    )

    fmt = args.format
    if fmt == "json":
        rendered = to_json(report)
    elif fmt in ("md", "markdown"):
        rendered = to_markdown(report)
    else:
        rendered = to_text(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
