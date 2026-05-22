#!/usr/bin/env python3
"""vormap_alarmdedup - Agentic Alarm Deduplication & Correlation Advisor.

Operational sibling to vormap_curator / vormap_handoff / vormap_drift /
vormap_calibration / vormap_route / vormap_redundancy / vormap_replacement
/ vormap_sensorplanner.

Where vormap_handoff fuses *streams* into a shift briefing, and
vormap_calibration audits individual sensors, this advisor sits one
layer deeper. Real field telemetry produces alarm *storms* - a single
fault often fires dozens of duplicate events across nearby sensors and
across time. Sending all of them to the shift lead is noise.

Given a list of raw :class:`AlarmEvent` records, the advisor:

* Deduplicates within a per-sensor cooldown window (FLAPPING / DUPLICATE).
* Correlates spatially+temporally co-occurring alarms into incident
  CLUSTERs (with a representative root_id and member_ids).
* Tags each incident with a verdict (PAGE_ONCALL / DISPATCH_TODAY /
  WATCH / SUPPRESS / FLAPPING_TUNE_HYSTERESIS / NOISE), a 0-100
  priority_score and a P0..P3 priority bucket.
* Emits a P0-first deduped playbook for the operator (page on-call,
  open incident bridge, tune hysteresis, expand cluster radius,
  silence flapping sensor, audit category-wide drift, etc).
* Surfaces portfolio insights (alarm storm? single-failure cascade?
  category dominance? quiet shift?) and an A-F grade.
* Renders to text / markdown / byte-stable JSON.

Pure stdlib, deterministic given a fixed ``now_fn`` callable, never
mutates inputs.

CLI::

    python vormap_alarmdedup.py alarms.csv --radius 5 --window-sec 300 \\
        --risk balanced --format md

    python vormap_alarmdedup.py --demo --format text
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
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RISK_APPETITES = ("cautious", "balanced", "aggressive")

# Severity ordering: higher value = more severe.
_SEVERITIES = ("info", "warning", "minor", "major", "critical")
_SEV_RANK = {s: i for i, s in enumerate(_SEVERITIES)}

_VERDICTS = (
    "PAGE_ONCALL",
    "DISPATCH_TODAY",
    "WATCH",
    "FLAPPING_TUNE_HYSTERESIS",
    "SUPPRESS",
    "NOISE",
)

_VERDICT_BASE_SCORE = {
    "PAGE_ONCALL": 90.0,
    "DISPATCH_TODAY": 65.0,
    "FLAPPING_TUNE_HYSTERESIS": 55.0,
    "WATCH": 35.0,
    "SUPPRESS": 15.0,
    "NOISE": 5.0,
}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _appetite_score_mult(risk: str) -> float:
    return {"cautious": 1.15, "balanced": 1.0, "aggressive": 0.85}[risk]


def _appetite_window_mult(risk: str) -> float:
    # cautious widens the correlation window (correlate more aggressively);
    # aggressive trims it (only the tightest co-occurrences become incidents).
    return {"cautious": 1.25, "balanced": 1.0, "aggressive": 0.80}[risk]


def _euclid(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _parse_ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(float(v), tz=timezone.utc)
    s = str(v).strip()
    if not s:
        raise ValueError("empty timestamp")
    # accept Z-suffix
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _normalize_severity(s: Any) -> str:
    if s is None:
        return "warning"
    t = str(s).strip().lower()
    if t in _SEV_RANK:
        return t
    # common aliases
    aliases = {
        "low": "info",
        "medium": "warning",
        "med": "warning",
        "moderate": "minor",
        "high": "major",
        "sev1": "critical",
        "sev2": "major",
        "sev3": "minor",
        "sev4": "warning",
        "sev5": "info",
        "p0": "critical",
        "p1": "major",
        "p2": "minor",
        "p3": "warning",
        "p4": "info",
    }
    return aliases.get(t, "warning")


def _stable_id(prefix: str, key: str) -> str:
    # Deterministic short id for clusters.
    h = 0
    for ch in key:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return f"{prefix}-{h:08x}"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AlarmEvent:
    id: str
    sensor_id: str
    x: float
    y: float
    timestamp: datetime
    severity: str = "warning"
    category: str = "generic"
    message: str = ""
    value: Optional[float] = None

    @classmethod
    def from_record(cls, row: Dict[str, Any]) -> "AlarmEvent":
        return cls(
            id=str(row["id"]),
            sensor_id=str(row.get("sensor_id", row["id"])),
            x=float(row["x"]),
            y=float(row["y"]),
            timestamp=_parse_ts(row["timestamp"]),
            severity=_normalize_severity(row.get("severity")),
            category=str(row.get("category", "generic")),
            message=str(row.get("message", "")),
            value=(float(row["value"]) if row.get("value") not in (None, "", "None") else None),
        )


@dataclass
class IncidentCluster:
    id: str
    verdict: str
    priority: str  # P0..P3
    priority_score: float
    root_alarm_id: str
    root_sensor_id: str
    centroid: Tuple[float, float]
    member_alarm_ids: List[str]
    member_sensor_ids: List[str]
    member_count: int
    span_meters: float
    duration_sec: float
    first_seen: str  # ISO
    last_seen: str  # ISO
    severity: str
    category: str
    reasons: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class PlaybookAction:
    id: str
    priority: str
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    related_cluster_ids: List[str] = field(default_factory=list)


@dataclass
class AlarmDedupReport:
    summary: str
    grade: str
    risk_appetite: str
    radius_meters: float
    window_seconds: float
    cooldown_seconds: float
    total_alarms: int
    deduped_alarm_count: int
    suppressed_alarm_count: int
    incident_count: int
    p0_count: int
    p1_count: int
    p2_count: int
    p3_count: int
    noise_ratio: float
    incidents: List[IncidentCluster]
    playbook: List[PlaybookAction]
    insights: List[str]
    generated_at: str


# ---------------------------------------------------------------------------
# Core grouping logic
# ---------------------------------------------------------------------------


def _group_dedup_per_sensor(
    events: List[AlarmEvent], cooldown_sec: float
) -> Tuple[List[AlarmEvent], Dict[str, int], Dict[str, int]]:
    """Collapse near-back-to-back alarms from the same sensor.

    Returns (kept_events, flap_counts_per_sensor, suppressed_counts_per_sensor).
    A sensor with >=3 alarms in one cooldown window is flagged as flapping.
    """
    by_sensor: Dict[str, List[AlarmEvent]] = {}
    for ev in events:
        by_sensor.setdefault(ev.sensor_id, []).append(ev)

    kept: List[AlarmEvent] = []
    flap_counts: Dict[str, int] = {}
    suppressed: Dict[str, int] = {}

    for sid, lst in by_sensor.items():
        lst.sort(key=lambda e: (e.timestamp, e.id))
        last_keep: Optional[AlarmEvent] = None
        window_count = 0
        window_start: Optional[datetime] = None
        for ev in lst:
            if last_keep is None:
                kept.append(ev)
                last_keep = ev
                window_start = ev.timestamp
                window_count = 1
                continue
            delta = (ev.timestamp - last_keep.timestamp).total_seconds()
            if delta < cooldown_sec:
                # Suppress as duplicate. If new event is more severe, upgrade
                # the kept event in place (we already deep-copied inputs).
                suppressed[sid] = suppressed.get(sid, 0) + 1
                if _SEV_RANK[ev.severity] > _SEV_RANK[last_keep.severity]:
                    last_keep.severity = ev.severity
                if window_start is not None and (
                    ev.timestamp - window_start
                ).total_seconds() <= cooldown_sec:
                    window_count += 1
                else:
                    window_start = ev.timestamp
                    window_count = 1
                if window_count >= 3:
                    flap_counts[sid] = max(flap_counts.get(sid, 0), window_count)
            else:
                kept.append(ev)
                last_keep = ev
                window_start = ev.timestamp
                window_count = 1
    kept.sort(key=lambda e: (e.timestamp, e.id))
    return kept, flap_counts, suppressed


def _cluster_alarms(
    events: List[AlarmEvent], radius: float, window_sec: float
) -> List[List[AlarmEvent]]:
    """Greedy spatio-temporal clustering.

    Two alarms are co-incident if they are within `radius` meters AND
    within `window_sec` of each other, AND share the same category
    (categories are usually distinct fault types -> don't merge a
    pressure alarm with a temperature alarm even at the same location).
    """
    n = len(events)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        a, b = find(i), find(j)
        if a != b:
            parent[a] = b

    # O(n^2) is fine for alarm volumes (typically <500/shift).
    for i in range(n):
        for j in range(i + 1, n):
            a, b = events[i], events[j]
            if a.category != b.category:
                continue
            if _euclid((a.x, a.y), (b.x, b.y)) > radius:
                continue
            if abs((a.timestamp - b.timestamp).total_seconds()) > window_sec:
                continue
            union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out: List[List[AlarmEvent]] = []
    for ids in groups.values():
        out.append([events[k] for k in ids])
    # Deterministic order: earliest first_seen, then by root sensor id.
    out.sort(
        key=lambda g: (
            min(e.timestamp for e in g),
            sorted(e.sensor_id for e in g)[0],
        )
    )
    return out


# ---------------------------------------------------------------------------
# Advisor
# ---------------------------------------------------------------------------


class AlarmDedupAdvisor:
    """Deduplicate and correlate raw alarms into actionable incidents."""

    def __init__(self, now_fn: Optional[Callable[[], datetime]] = None) -> None:
        self._now_fn = now_fn or _utc_now

    def analyze(
        self,
        alarms: Sequence[Any],
        *,
        radius_meters: float = 5.0,
        window_seconds: float = 300.0,
        cooldown_seconds: float = 60.0,
        risk_appetite: str = "balanced",
    ) -> AlarmDedupReport:
        if risk_appetite not in _RISK_APPETITES:
            raise ValueError(
                f"risk_appetite must be one of {_RISK_APPETITES}, got {risk_appetite!r}"
            )
        if radius_meters < 0 or window_seconds < 0 or cooldown_seconds < 0:
            raise ValueError("radius/window/cooldown must be >= 0")

        # Deep-copy everything; coerce inputs.
        normalised: List[AlarmEvent] = []
        for a in alarms:
            if isinstance(a, AlarmEvent):
                normalised.append(copy.deepcopy(a))
            else:
                normalised.append(AlarmEvent.from_record(copy.deepcopy(dict(a))))

        total_alarms = len(normalised)

        # Modulate the temporal correlation window with risk appetite.
        eff_window = window_seconds * _appetite_window_mult(risk_appetite)
        eff_radius = radius_meters * _appetite_window_mult(risk_appetite)

        kept, flap_counts, suppressed = _group_dedup_per_sensor(
            normalised, cooldown_seconds
        )
        suppressed_total = sum(suppressed.values())
        deduped_count = len(kept)

        groups = _cluster_alarms(kept, eff_radius, eff_window)

        incidents: List[IncidentCluster] = []
        for grp in groups:
            grp_sorted = sorted(grp, key=lambda e: (e.timestamp, e.id))
            root = max(
                grp_sorted,
                key=lambda e: (_SEV_RANK[e.severity], -grp_sorted.index(e)),
            )
            xs = [e.x for e in grp_sorted]
            ys = [e.y for e in grp_sorted]
            centroid = (sum(xs) / len(xs), sum(ys) / len(ys))
            span = max(
                (
                    _euclid((a.x, a.y), (b.x, b.y))
                    for i, a in enumerate(grp_sorted)
                    for b in grp_sorted[i + 1 :]
                ),
                default=0.0,
            )
            first_seen = grp_sorted[0].timestamp
            last_seen = grp_sorted[-1].timestamp
            duration_sec = (last_seen - first_seen).total_seconds()

            # Pick verdict + reasons.
            reasons: List[Dict[str, str]] = []
            sev_rank = _SEV_RANK[root.severity]
            member_sids = sorted({e.sensor_id for e in grp_sorted})
            is_flapping_root = flap_counts.get(root.sensor_id, 0) >= 3

            if len(grp_sorted) >= 5 and sev_rank >= _SEV_RANK["major"]:
                verdict = "PAGE_ONCALL"
                reasons.append(
                    {"code": "STORM_OF_MAJOR_ALARMS",
                     "detail": f"{len(grp_sorted)} co-located major+ alarms"}
                )
            elif sev_rank >= _SEV_RANK["critical"]:
                verdict = "PAGE_ONCALL"
                reasons.append(
                    {"code": "CRITICAL_SEVERITY", "detail": "root alarm is critical"}
                )
            elif len(member_sids) >= 3 and sev_rank >= _SEV_RANK["minor"]:
                verdict = "DISPATCH_TODAY"
                reasons.append(
                    {"code": "MULTI_SENSOR_CLUSTER",
                     "detail": f"{len(member_sids)} sensors in cluster"}
                )
            elif is_flapping_root:
                verdict = "FLAPPING_TUNE_HYSTERESIS"
                reasons.append(
                    {"code": "FLAPPING",
                     "detail": f"sensor {root.sensor_id} fired {flap_counts[root.sensor_id]}x in cooldown window"}
                )
            elif sev_rank >= _SEV_RANK["minor"]:
                verdict = "WATCH"
                reasons.append(
                    {"code": "ELEVATED_SEVERITY", "detail": "single minor/major alarm"}
                )
            elif sev_rank <= _SEV_RANK["info"]:
                verdict = "NOISE"
                reasons.append({"code": "INFO_ONLY", "detail": "info-level event"})
            else:
                verdict = "SUPPRESS"
                reasons.append(
                    {"code": "LOW_PRIORITY", "detail": "warning-level isolated alarm"}
                )

            if span > radius_meters and len(grp_sorted) >= 2:
                reasons.append(
                    {"code": "WIDE_SPATIAL_SPAN",
                     "detail": f"span={span:.1f}m exceeds radius={radius_meters}m"}
                )

            base = _VERDICT_BASE_SCORE[verdict]
            count_bonus = min(15.0, math.log2(max(len(grp_sorted), 1) + 1) * 6.0)
            sensor_bonus = min(10.0, (len(member_sids) - 1) * 3.0)
            score = _clamp(
                (base + count_bonus + sensor_bonus) * _appetite_score_mult(risk_appetite)
            )

            # Priority bucket (P0 ladder pinned for paging/critical regardless of appetite).
            if verdict == "PAGE_ONCALL":
                priority = "P0"
            elif verdict == "DISPATCH_TODAY":
                priority = "P1" if score < 75 else "P0"
            elif verdict == "FLAPPING_TUNE_HYSTERESIS":
                priority = "P1"
            elif verdict == "WATCH":
                priority = "P2"
            elif verdict == "SUPPRESS":
                priority = "P3"
            else:
                priority = "P3"

            cid = _stable_id("INC", f"{root.sensor_id}|{root.category}|{first_seen.isoformat()}")
            incidents.append(
                IncidentCluster(
                    id=cid,
                    verdict=verdict,
                    priority=priority,
                    priority_score=round(score, 2),
                    root_alarm_id=root.id,
                    root_sensor_id=root.sensor_id,
                    centroid=(round(centroid[0], 4), round(centroid[1], 4)),
                    member_alarm_ids=sorted(e.id for e in grp_sorted),
                    member_sensor_ids=member_sids,
                    member_count=len(grp_sorted),
                    span_meters=round(span, 3),
                    duration_sec=round(duration_sec, 3),
                    first_seen=first_seen.isoformat(),
                    last_seen=last_seen.isoformat(),
                    severity=root.severity,
                    category=root.category,
                    reasons=reasons,
                )
            )

        # Deterministic ordering.
        incidents.sort(key=lambda i: (i.priority, -i.priority_score, i.id))

        # Bucket counts.
        p0 = sum(1 for i in incidents if i.priority == "P0")
        p1 = sum(1 for i in incidents if i.priority == "P1")
        p2 = sum(1 for i in incidents if i.priority == "P2")
        p3 = sum(1 for i in incidents if i.priority == "P3")
        noise_ratio = (
            sum(1 for i in incidents if i.verdict in ("SUPPRESS", "NOISE"))
            / max(len(incidents), 1)
        )

        # ---- playbook ----
        playbook = self._build_playbook(
            incidents,
            flap_counts=flap_counts,
            suppressed=suppressed,
            risk_appetite=risk_appetite,
            total_alarms=total_alarms,
            deduped_count=deduped_count,
        )

        # ---- insights ----
        insights = self._build_insights(
            incidents,
            flap_counts=flap_counts,
            suppressed_total=suppressed_total,
            total_alarms=total_alarms,
            noise_ratio=noise_ratio,
        )

        # ---- grade ----
        grade = self._grade(p0=p0, p1=p1, noise_ratio=noise_ratio, total=total_alarms)

        # ---- summary headline ----
        summary = (
            f"VERDICT: grade={grade} alarms={total_alarms} -> incidents={len(incidents)} "
            f"(P0={p0} P1={p1} P2={p2} P3={p3}) noise_ratio={noise_ratio:.2f} "
            f"dedup={total_alarms - deduped_count} suppressed={suppressed_total}"
        )

        return AlarmDedupReport(
            summary=summary,
            grade=grade,
            risk_appetite=risk_appetite,
            radius_meters=float(radius_meters),
            window_seconds=float(window_seconds),
            cooldown_seconds=float(cooldown_seconds),
            total_alarms=total_alarms,
            deduped_alarm_count=deduped_count,
            suppressed_alarm_count=suppressed_total,
            incident_count=len(incidents),
            p0_count=p0,
            p1_count=p1,
            p2_count=p2,
            p3_count=p3,
            noise_ratio=round(noise_ratio, 3),
            incidents=incidents,
            playbook=playbook,
            insights=insights,
            generated_at=self._now_fn().isoformat(),
        )

    # ---- internals -------------------------------------------------------

    def _build_playbook(
        self,
        incidents: List[IncidentCluster],
        *,
        flap_counts: Dict[str, int],
        suppressed: Dict[str, int],
        risk_appetite: str,
        total_alarms: int,
        deduped_count: int,
    ) -> List[PlaybookAction]:
        actions: List[PlaybookAction] = []
        p0_incidents = [i for i in incidents if i.priority == "P0"]
        page_incidents = [i for i in incidents if i.verdict == "PAGE_ONCALL"]
        dispatch_incidents = [i for i in incidents if i.verdict == "DISPATCH_TODAY"]
        flap_incidents = [
            i for i in incidents if i.verdict == "FLAPPING_TUNE_HYSTERESIS"
        ]
        wide_incidents = [
            i
            for i in incidents
            if any(r.get("code") == "WIDE_SPATIAL_SPAN" for r in i.reasons)
        ]
        # Category dominance.
        cat_counts: Dict[str, int] = {}
        for i in incidents:
            cat_counts[i.category] = cat_counts.get(i.category, 0) + 1
        dominant_cat: Optional[str] = None
        if cat_counts:
            dominant_cat = max(cat_counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if cat_counts[dominant_cat] < max(3, len(incidents) // 2):
                dominant_cat = None

        if page_incidents:
            actions.append(
                PlaybookAction(
                    id="PAGE_ONCALL_ENGINEER",
                    priority="P0",
                    label="Page on-call engineer immediately",
                    reason=f"{len(page_incidents)} incident(s) at critical/storm severity",
                    owner="shift_lead",
                    blast_radius=4,
                    reversibility="high",
                    related_cluster_ids=[i.id for i in page_incidents],
                )
            )
        if len(p0_incidents) >= 2:
            actions.append(
                PlaybookAction(
                    id="OPEN_INCIDENT_BRIDGE",
                    priority="P0",
                    label="Open incident bridge / war room",
                    reason=f"{len(p0_incidents)} P0 incidents in this window",
                    owner="ops",
                    blast_radius=5,
                    reversibility="high",
                    related_cluster_ids=[i.id for i in p0_incidents],
                )
            )
        if dispatch_incidents:
            actions.append(
                PlaybookAction(
                    id="DISPATCH_FIELD_TEAM",
                    priority="P1",
                    label="Dispatch field team to multi-sensor clusters",
                    reason=f"{len(dispatch_incidents)} cluster(s) span multiple sensors",
                    owner="field_team",
                    blast_radius=3,
                    reversibility="high",
                    related_cluster_ids=[i.id for i in dispatch_incidents],
                )
            )
        if flap_incidents:
            actions.append(
                PlaybookAction(
                    id="TUNE_HYSTERESIS_FLAPPING",
                    priority="P1",
                    label="Tune hysteresis on flapping sensors",
                    reason=(
                        f"{len(flap_incidents)} sensor(s) firing inside cooldown - "
                        "raise threshold or add debounce"
                    ),
                    owner="data_steward",
                    blast_radius=2,
                    reversibility="high",
                    related_cluster_ids=[i.id for i in flap_incidents],
                )
            )
        if wide_incidents:
            actions.append(
                PlaybookAction(
                    id="EXPAND_CORRELATION_RADIUS",
                    priority="P2",
                    label="Review correlation radius - clusters span widely",
                    reason=(
                        f"{len(wide_incidents)} cluster(s) span > configured radius; "
                        "may be under-correlated"
                    ),
                    owner="ops",
                    blast_radius=2,
                    reversibility="high",
                    related_cluster_ids=[i.id for i in wide_incidents],
                )
            )
        if dominant_cat:
            related = [i.id for i in incidents if i.category == dominant_cat]
            actions.append(
                PlaybookAction(
                    id=f"AUDIT_CATEGORY_{dominant_cat.upper()}",
                    priority="P2",
                    label=f"Audit category '{dominant_cat}' fleet-wide",
                    reason=(
                        f"category {dominant_cat!r} dominates incidents "
                        f"({cat_counts[dominant_cat]}/{len(incidents)})"
                    ),
                    owner="data_steward",
                    blast_radius=3,
                    reversibility="high",
                    related_cluster_ids=related,
                )
            )
        if total_alarms > 0 and deduped_count <= total_alarms / 3:
            actions.append(
                PlaybookAction(
                    id="REVIEW_ALARM_SOURCES_FOR_NOISE",
                    priority="P2",
                    label="Review noisy alarm sources",
                    reason=(
                        f"{total_alarms - deduped_count}/{total_alarms} raw alarms "
                        "collapsed - upstream sources are noisy"
                    ),
                    owner="data_steward",
                    blast_radius=2,
                    reversibility="high",
                    related_cluster_ids=[],
                )
            )

        # Cautious-only audit; aggressive trims fallbacks.
        if risk_appetite == "cautious" and incidents:
            actions.append(
                PlaybookAction(
                    id="SCHEDULE_DEDUP_AUDIT",
                    priority="P2",
                    label="Schedule alarm-dedup config audit",
                    reason="cautious appetite: re-tune radius/window/cooldown next sprint",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    related_cluster_ids=[],
                )
            )

        # Fallback P3 if no actions.
        if not actions:
            actions.append(
                PlaybookAction(
                    id="MAINTAIN_MONITORING",
                    priority="P3",
                    label="Quiet shift - maintain monitoring",
                    reason="no actionable incidents detected",
                    owner="shift_lead",
                    blast_radius=1,
                    reversibility="high",
                    related_cluster_ids=[],
                )
            )

        if risk_appetite == "aggressive":
            actions = [
                a
                for a in actions
                if not (
                    a.priority == "P3"
                    and any(x.priority in ("P0", "P1") for x in actions)
                )
            ]
            actions = [
                a
                for a in actions
                if not (
                    a.priority == "P2"
                    and a.id in {"REVIEW_ALARM_SOURCES_FOR_NOISE", "EXPAND_CORRELATION_RADIUS"}
                    and any(x.priority in ("P0", "P1") for x in actions)
                )
            ]

        # Stable order: priority asc, then id asc.
        _order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        actions.sort(key=lambda a: (_order[a.priority], a.id))
        return actions

    def _build_insights(
        self,
        incidents: List[IncidentCluster],
        *,
        flap_counts: Dict[str, int],
        suppressed_total: int,
        total_alarms: int,
        noise_ratio: float,
    ) -> List[str]:
        out: List[str] = []
        if total_alarms == 0:
            out.append("NO_ALARMS")
            return out
        if total_alarms >= 50 and suppressed_total >= total_alarms / 2:
            out.append("ALARM_STORM_DETECTED")
        if any(
            i.member_count >= 5 and i.verdict == "PAGE_ONCALL" for i in incidents
        ):
            out.append("SINGLE_FAILURE_CASCADE")
        if flap_counts:
            out.append(f"FLAPPING_SENSORS:{len(flap_counts)}")
        if noise_ratio >= 0.5 and incidents:
            out.append("HIGH_NOISE_RATIO")
        if not incidents:
            out.append("ALL_ALARMS_SUPPRESSED")
        # Category dominance signal (>50% of incidents share one category).
        if incidents:
            cat_counts: Dict[str, int] = {}
            for i in incidents:
                cat_counts[i.category] = cat_counts.get(i.category, 0) + 1
            top_cat, top_n = max(cat_counts.items(), key=lambda kv: (kv[1], kv[0]))
            if top_n >= max(3, len(incidents) // 2):
                out.append(f"CATEGORY_DOMINANCE:{top_cat}")
        if not out:
            out.append("HEALTHY_OPERATIONS")
        return out

    def _grade(self, *, p0: int, p1: int, noise_ratio: float, total: int) -> str:
        if total == 0:
            return "A"
        if p0 >= 2:
            return "F"
        if p0 >= 1:
            return "D"
        if p1 >= 3 or noise_ratio >= 0.6:
            return "C"
        if p1 >= 1 or noise_ratio >= 0.3:
            return "B"
        return "A"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _as_dict(report: AlarmDedupReport) -> Dict[str, Any]:
    return asdict(report)


def to_json(report: AlarmDedupReport) -> str:
    return json.dumps(_as_dict(report), sort_keys=True, indent=2, default=str)


def to_text(report: AlarmDedupReport) -> str:
    lines: List[str] = []
    lines.append(report.summary)
    lines.append("")
    lines.append("== Incidents ==")
    if not report.incidents:
        lines.append("  (none)")
    for inc in report.incidents:
        lines.append(
            f"  [{inc.priority}] {inc.id} {inc.verdict} score={inc.priority_score:.1f} "
            f"sev={inc.severity} cat={inc.category} n={inc.member_count} "
            f"sensors={len(inc.member_sensor_ids)} span={inc.span_meters:.1f}m "
            f"dur={inc.duration_sec:.0f}s"
        )
        for r in inc.reasons:
            lines.append(f"      - {r.get('code')}: {r.get('detail','')}")
    lines.append("")
    lines.append("== Playbook ==")
    for a in report.playbook:
        lines.append(
            f"  [{a.priority}] {a.id}: {a.label}"
        )
        lines.append(f"      reason: {a.reason}")
        lines.append(
            f"      owner={a.owner} blast={a.blast_radius} rev={a.reversibility}"
        )
    lines.append("")
    lines.append("== Insights ==")
    for ins in report.insights:
        lines.append(f"  - {ins}")
    return "\n".join(lines)


def _md_escape(s: str) -> str:
    return str(s).replace("|", r"\|")


def to_markdown(report: AlarmDedupReport) -> str:
    lines: List[str] = []
    lines.append(f"# Alarm Dedup Report ({report.grade})")
    lines.append("")
    lines.append(report.summary)
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    rows = [
        ("grade", report.grade),
        ("risk_appetite", report.risk_appetite),
        ("radius_meters", report.radius_meters),
        ("window_seconds", report.window_seconds),
        ("cooldown_seconds", report.cooldown_seconds),
        ("total_alarms", report.total_alarms),
        ("deduped_alarm_count", report.deduped_alarm_count),
        ("suppressed_alarm_count", report.suppressed_alarm_count),
        ("incident_count", report.incident_count),
        ("P0", report.p0_count),
        ("P1", report.p1_count),
        ("P2", report.p2_count),
        ("P3", report.p3_count),
        ("noise_ratio", report.noise_ratio),
    ]
    for k, v in rows:
        lines.append(f"| {_md_escape(k)} | {_md_escape(v)} |")

    lines.append("")
    lines.append("## Incidents")
    lines.append("")
    lines.append(
        "| id | priority | verdict | score | sev | cat | n | sensors | span(m) | dur(s) | first_seen |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    if not report.incidents:
        lines.append("| _none_ |  |  |  |  |  |  |  |  |  |  |")
    for i in report.incidents:
        lines.append(
            f"| {_md_escape(i.id)} | {i.priority} | {i.verdict} | {i.priority_score:.1f} | "
            f"{i.severity} | {_md_escape(i.category)} | {i.member_count} | "
            f"{len(i.member_sensor_ids)} | {i.span_meters:.1f} | {i.duration_sec:.0f} | "
            f"{_md_escape(i.first_seen)} |"
        )

    lines.append("")
    lines.append("## Playbook")
    lines.append("")
    lines.append("| priority | id | label | owner | blast | reversibility |")
    lines.append("|---|---|---|---|---|---|")
    for a in report.playbook:
        lines.append(
            f"| {a.priority} | {_md_escape(a.id)} | {_md_escape(a.label)} | "
            f"{_md_escape(a.owner)} | {a.blast_radius} | {_md_escape(a.reversibility)} |"
        )

    lines.append("")
    lines.append("## Insights")
    lines.append("")
    if not report.insights:
        lines.append("- _(none)_")
    for ins in report.insights:
        lines.append(f"- {_md_escape(ins)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_alarms_csv(path: str) -> List[Dict[str, Any]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _demo_alarms() -> List[AlarmEvent]:
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)

    def at(secs: float) -> datetime:
        return base + timedelta(seconds=secs)

    rows = [
        # Cluster A: pressure storm at (10, 10) across 4 sensors, critical.
        ("a1", "S1", 10.0, 10.0, at(0), "critical", "pressure", "PT alarm"),
        ("a2", "S2", 10.5, 10.2, at(5), "major", "pressure", "PT alarm"),
        ("a3", "S3", 9.8, 10.1, at(12), "major", "pressure", "PT alarm"),
        ("a4", "S4", 10.2, 9.9, at(30), "minor", "pressure", "PT alarm"),
        ("a5", "S1", 10.0, 10.0, at(45), "critical", "pressure", "PT alarm"),
        ("a6", "S1", 10.0, 10.0, at(50), "critical", "pressure", "PT alarm"),
        # Cluster B: temperature warning, single sensor flapping.
        ("a7", "S10", 50.0, 50.0, at(60), "warning", "temperature", "TT high"),
        ("a8", "S10", 50.0, 50.0, at(70), "warning", "temperature", "TT high"),
        ("a9", "S10", 50.0, 50.0, at(80), "warning", "temperature", "TT high"),
        ("a10", "S10", 50.0, 50.0, at(95), "warning", "temperature", "TT high"),
        # Cluster C: lone info, far away.
        ("a11", "S20", 200.0, 200.0, at(120), "info", "diagnostic", "self-test"),
        # Cluster D: multi-sensor leak, minor.
        ("a12", "S30", 100.0, 100.0, at(200), "minor", "leak", "leak detected"),
        ("a13", "S31", 100.4, 100.3, at(210), "minor", "leak", "leak detected"),
        ("a14", "S32", 99.6, 100.1, at(220), "minor", "leak", "leak detected"),
    ]
    return [
        AlarmEvent(
            id=r[0], sensor_id=r[1], x=r[2], y=r[3], timestamp=r[4],
            severity=r[5], category=r[6], message=r[7],
        )
        for r in rows
    ]


def _main(argv: Optional[List[str]] = None) -> int:
    # Windows: ensure UTF-8 stdout for safety with markdown bullets / arrows.
    try:
        if sys.platform.startswith("win") and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(
        prog="vormap_alarmdedup",
        description="Agentic alarm deduplication & correlation advisor.",
    )
    p.add_argument("input", nargs="?", help="CSV of alarms (id,sensor_id,x,y,timestamp,severity,category,...)")
    p.add_argument("--demo", action="store_true", help="Use built-in 14-alarm demo dataset")
    p.add_argument("--radius", type=float, default=5.0, help="spatial correlation radius (m)")
    p.add_argument("--window-sec", type=float, default=300.0, help="temporal correlation window (s)")
    p.add_argument("--cooldown-sec", type=float, default=60.0, help="per-sensor dedup cooldown (s)")
    p.add_argument(
        "--risk",
        choices=_RISK_APPETITES,
        default="balanced",
        help="risk appetite (default: balanced)",
    )
    p.add_argument(
        "--format",
        choices=("text", "md", "markdown", "json"),
        default="text",
        help="output format",
    )
    p.add_argument("--output", help="write to file instead of stdout")
    args = p.parse_args(argv)

    if args.demo:
        alarms: Sequence[Any] = _demo_alarms()
    elif args.input:
        alarms = _load_alarms_csv(args.input)
    else:
        p.error("provide a CSV path or --demo")
        return 2  # pragma: no cover

    advisor = AlarmDedupAdvisor()
    report = advisor.analyze(
        alarms,
        radius_meters=args.radius,
        window_seconds=args.window_sec,
        cooldown_seconds=args.cooldown_sec,
        risk_appetite=args.risk,
    )

    if args.format == "json":
        out = to_json(report)
    elif args.format in ("md", "markdown"):
        out = to_markdown(report)
    else:
        out = to_text(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
    else:
        print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
