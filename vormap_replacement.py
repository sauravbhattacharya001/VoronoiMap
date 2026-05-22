#!/usr/bin/env python3
"""vormap_replacement - Agentic Per-Sensor Replacement / Swap-Out Advisor.

Distinct from siblings:

    * vormap_calibration  -> recalibrate readings (software fix)
    * vormap_redundancy   -> retire because another sensor covers it
    * vormap_sensorplanner -> add new sensors
    * vormap_drift / curator -> data-quality verdicts

This module answers the *hardware* question: which physical sensors
should be replaced, swapped to a backup unit, refurbished in place,
or decommissioned. It fuses age / lifetime / failure-history /
criticality / backup-availability into ranked actions a field team
can execute.

Pure stdlib. Deterministic. Never mutates inputs.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SensorAsset:
    """A physical sensor asset tracked for replacement planning."""

    sensor_id: str
    install_date: Optional[str] = None             # ISO date / datetime
    last_service_date: Optional[str] = None        # ISO date / datetime
    expected_lifetime_days: int = 1825             # default 5 years
    failure_count: int = 0                         # lifetime hard failures
    error_count_30d: int = 0                       # soft errors in last 30 days
    reading_count_30d: int = 0                     # total readings 30d (for error %)
    has_backup: bool = False                       # spare unit in stock or on shelf
    criticality: int = 3                           # 1 (low) .. 5 (mission-critical)
    replacement_cost_usd: float = 0.0              # 0 means unknown / not budgeted
    downtime_hours_if_failed: float = 0.0          # 0 means unknown
    x: Optional[float] = None
    y: Optional[float] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None

    @classmethod
    def from_record(cls, row: dict) -> "SensorAsset":
        kwargs = {}
        for fld in (
            "sensor_id", "install_date", "last_service_date",
            "manufacturer", "model",
        ):
            v = row.get(fld)
            if v not in (None, ""):
                kwargs[fld] = str(v)
        for fld in ("expected_lifetime_days", "failure_count",
                    "error_count_30d", "reading_count_30d", "criticality"):
            v = row.get(fld)
            if v not in (None, ""):
                kwargs[fld] = int(float(v))
        for fld in ("replacement_cost_usd", "downtime_hours_if_failed", "x", "y"):
            v = row.get(fld)
            if v not in (None, ""):
                kwargs[fld] = float(v)
        v = row.get("has_backup")
        if v not in (None, ""):
            kwargs["has_backup"] = str(v).strip().lower() in ("1", "true", "yes", "y", "t")
        if "sensor_id" not in kwargs:
            raise ValueError("sensor_id is required")
        return cls(**kwargs)


@dataclass
class ReplacementFinding:
    sensor_id: str
    verdict: str
    priority: str             # P0 / P1 / P2 / P3
    risk_score: float         # 0..100
    reasons: List[str] = field(default_factory=list)
    age_days: Optional[int] = None
    lifetime_pct: Optional[float] = None
    error_rate_30d: Optional[float] = None
    suggested_action: str = ""
    projected_savings_usd: float = 0.0    # cost avoided by acting
    notes: List[str] = field(default_factory=list)


@dataclass
class PlaybookAction:
    id: str
    priority: str
    label: str
    reason: str
    owner: str
    blast_radius: int                # 1..5
    reversibility: str               # low / medium / high
    sensor_ids: List[str] = field(default_factory=list)
    suggested_value: Optional[str] = None


@dataclass
class ReplacementReport:
    grade: str
    portfolio_risk: float
    headline: str
    findings: List[ReplacementFinding]
    playbook: List[PlaybookAction]
    insights: List[str]
    generated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEF_NOW: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if "T" in s or " " in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s + "T00:00:00+00:00")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _days_between(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 86400)


def _appetite_mult(risk_appetite: str) -> float:
    return {"cautious": 1.15, "balanced": 1.0, "aggressive": 0.85}.get(
        risk_appetite, 1.0
    )


def _priority_for(verdict: str) -> str:
    return {
        "REPLACE_NOW": "P0",
        "SWAP_TO_BACKUP": "P0",
        "REPLACE_SOON": "P1",
        "REFURBISH_IN_PLACE": "P2",
        "SCHEDULE_SERVICE": "P2",
        "DECOMMISSION": "P2",
        "MONITOR": "P3",
        "INSUFFICIENT_DATA": "P3",
    }.get(verdict, "P3")


# ---------------------------------------------------------------------------
# Core scorer
# ---------------------------------------------------------------------------


def _score_sensor(
    asset: SensorAsset,
    now: datetime,
    risk_appetite: str,
) -> ReplacementFinding:
    reasons: List[str] = []
    notes: List[str] = []

    install_dt = _parse_iso(asset.install_date)
    age_days = _days_between(install_dt, now) if install_dt else None
    lifetime_pct = (
        (age_days / asset.expected_lifetime_days) * 100.0
        if (age_days is not None and asset.expected_lifetime_days > 0)
        else None
    )

    error_rate = None
    if asset.reading_count_30d > 0:
        error_rate = asset.error_count_30d / asset.reading_count_30d

    # Insufficient data path
    if install_dt is None and asset.failure_count == 0 and asset.reading_count_30d == 0:
        return ReplacementFinding(
            sensor_id=asset.sensor_id,
            verdict="INSUFFICIENT_DATA",
            priority="P3",
            risk_score=0.0,
            reasons=["NO_TELEMETRY_OR_INSTALL_RECORD"],
            age_days=None,
            lifetime_pct=None,
            error_rate_30d=error_rate,
            suggested_action="register install date and start telemetry capture",
            notes=notes,
        )

    # --- Component scores -------------------------------------------------
    age_score = 0.0
    if lifetime_pct is not None:
        if lifetime_pct >= 120:
            age_score = 60.0
            reasons.append("PAST_EXPECTED_LIFETIME")
        elif lifetime_pct >= 100:
            age_score = 45.0
            reasons.append("AT_EXPECTED_LIFETIME")
        elif lifetime_pct >= 85:
            age_score = 30.0
            reasons.append("APPROACHING_END_OF_LIFE")
        elif lifetime_pct >= 60:
            age_score = 12.0
            reasons.append("MIDLIFE")
        else:
            age_score = 0.0

    failure_score = 0.0
    if asset.failure_count >= 5:
        failure_score = 55.0
        reasons.append("CHRONIC_FAILURES")
    elif asset.failure_count >= 3:
        failure_score = 35.0
        reasons.append("REPEATED_FAILURES")
    elif asset.failure_count >= 1:
        failure_score = 18.0
        reasons.append("PRIOR_FAILURE")

    error_score = 0.0
    if error_rate is not None:
        if error_rate >= 0.20:
            error_score = 50.0
            reasons.append("HIGH_ERROR_RATE")
        elif error_rate >= 0.10:
            error_score = 30.0
            reasons.append("ELEVATED_ERROR_RATE")
        elif error_rate >= 0.05:
            error_score = 15.0
            reasons.append("MILD_ERROR_RATE")

    crit_bonus = (asset.criticality - 3) * 5.0  # +/-10 over 1..5
    if asset.criticality >= 4:
        reasons.append("CRITICAL_ASSET")

    service_penalty = 0.0
    last_service = _parse_iso(asset.last_service_date)
    if last_service is not None:
        service_age = _days_between(last_service, now)
        if service_age >= 365:
            service_penalty = 10.0
            reasons.append("SERVICE_OVERDUE")
    elif install_dt is not None and age_days and age_days >= 365:
        service_penalty = 8.0
        reasons.append("NEVER_SERVICED")

    backup_dampener = -8.0 if asset.has_backup else 0.0
    if asset.has_backup:
        reasons.append("BACKUP_AVAILABLE")

    raw = age_score + failure_score + error_score + crit_bonus + service_penalty + backup_dampener
    risk_score = max(0.0, min(100.0, raw)) * _appetite_mult(risk_appetite)
    risk_score = max(0.0, min(100.0, risk_score))

    # --- Verdict ladder ---------------------------------------------------
    verdict = "MONITOR"
    suggested = "monitor in normal cadence"

    catastrophic = asset.failure_count >= 5 or (
        lifetime_pct is not None and lifetime_pct >= 130
    )
    near_eol = lifetime_pct is not None and lifetime_pct >= 85
    high_error = error_rate is not None and error_rate >= 0.10

    # Decommission: low criticality + replacement cost outweighs downtime value
    cost_outweighs_value = False
    if asset.replacement_cost_usd > 0 and asset.downtime_hours_if_failed >= 0:
        # crude proxy: downtime hours * 100 usd valuation
        downtime_value = asset.downtime_hours_if_failed * 100.0
        cost_outweighs_value = (
            asset.replacement_cost_usd > 5 * max(downtime_value, 50.0)
        )

    if catastrophic or risk_score >= 75:
        verdict = "REPLACE_NOW"
        suggested = "schedule field replacement within 48h"
        if asset.has_backup:
            verdict = "SWAP_TO_BACKUP"
            suggested = "swap to on-shelf backup unit immediately"
    elif near_eol and (high_error or asset.failure_count >= 1):
        verdict = "REPLACE_SOON"
        suggested = "schedule replacement in next maintenance window"
    elif near_eol and asset.criticality >= 4:
        verdict = "REPLACE_SOON"
        suggested = "proactively replace before failure (critical asset)"
        if "CRITICAL_ASSET" not in reasons:
            reasons.append("CRITICAL_ASSET")
    elif risk_score >= 45:
        verdict = "REFURBISH_IN_PLACE"
        suggested = "field service: clean + recalibrate + tighten connectors"
    elif (
        cost_outweighs_value
        and asset.criticality <= 2
        and risk_score < 25
    ):
        verdict = "DECOMMISSION"
        suggested = "retire sensor; cost outweighs operational value"
        reasons.append("LOW_VALUE_VS_COST")
    elif risk_score >= 25 or service_penalty > 0:
        verdict = "SCHEDULE_SERVICE"
        suggested = "add to next routine service sweep"

    # cautious: never downgrade replace_now or swap; aggressive: bump
    # mid-tier REPLACE_SOON down to REFURBISH when no backup and risk<70
    if risk_appetite == "aggressive" and verdict == "REPLACE_SOON" and risk_score < 60:
        verdict = "REFURBISH_IN_PLACE"
        suggested = "(aggressive) refurbish in place; defer replacement"

    if risk_appetite == "cautious" and verdict == "SCHEDULE_SERVICE" and asset.criticality >= 4:
        verdict = "REFURBISH_IN_PLACE"
        suggested = "(cautious) refurbish critical asset rather than wait"

    # projected savings: avoiding catastrophic downtime on critical sensors
    projected_savings = 0.0
    if verdict in ("REPLACE_NOW", "SWAP_TO_BACKUP", "REPLACE_SOON"):
        per_hour = 100.0
        projected_savings = max(0.0, asset.downtime_hours_if_failed * per_hour)

    return ReplacementFinding(
        sensor_id=asset.sensor_id,
        verdict=verdict,
        priority=_priority_for(verdict),
        risk_score=round(risk_score, 2),
        reasons=sorted(set(reasons)),
        age_days=age_days,
        lifetime_pct=round(lifetime_pct, 1) if lifetime_pct is not None else None,
        error_rate_30d=round(error_rate, 4) if error_rate is not None else None,
        suggested_action=suggested,
        projected_savings_usd=round(projected_savings, 2),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Playbook + portfolio
# ---------------------------------------------------------------------------


def _build_playbook(
    findings: List[ReplacementFinding],
    portfolio_risk: float,
    grade: str,
    risk_appetite: str,
) -> List[PlaybookAction]:
    by_verdict: Dict[str, List[ReplacementFinding]] = {}
    for f in findings:
        by_verdict.setdefault(f.verdict, []).append(f)

    actions: List[PlaybookAction] = []

    def ids(v: str) -> List[str]:
        return sorted(x.sensor_id for x in by_verdict.get(v, []))

    if by_verdict.get("REPLACE_NOW"):
        actions.append(PlaybookAction(
            id="EXECUTE_EMERGENCY_REPLACEMENT",
            priority="P0",
            label="Dispatch field crew for emergency replacements",
            reason="Sensors classified REPLACE_NOW are at or past catastrophic risk",
            owner="field_team",
            blast_radius=4,
            reversibility="low",
            sensor_ids=ids("REPLACE_NOW"),
        ))
    if by_verdict.get("SWAP_TO_BACKUP"):
        actions.append(PlaybookAction(
            id="SWAP_TO_BACKUP_UNITS",
            priority="P0",
            label="Swap failing sensors to on-shelf backup units",
            reason="Backup unit available; avoids full procurement cycle",
            owner="field_team",
            blast_radius=2,
            reversibility="high",
            sensor_ids=ids("SWAP_TO_BACKUP"),
        ))
    if by_verdict.get("REPLACE_SOON"):
        actions.append(PlaybookAction(
            id="QUEUE_REPLACEMENT_ORDERS",
            priority="P1",
            label="Order replacement units for end-of-life sensors",
            reason="Sensors approaching expected lifetime with degrading signals",
            owner="procurement",
            blast_radius=3,
            reversibility="medium",
            sensor_ids=ids("REPLACE_SOON"),
        ))
    if by_verdict.get("REFURBISH_IN_PLACE"):
        actions.append(PlaybookAction(
            id="FIELD_REFURBISH",
            priority="P2",
            label="Schedule in-place refurbish (clean / recalibrate / reseat)",
            reason="Risk elevated but unit not yet at end of life",
            owner="maintenance",
            blast_radius=2,
            reversibility="high",
            sensor_ids=ids("REFURBISH_IN_PLACE"),
        ))
    if by_verdict.get("SCHEDULE_SERVICE"):
        actions.append(PlaybookAction(
            id="ROUTINE_SERVICE_SWEEP",
            priority="P2",
            label="Add to next routine service sweep",
            reason="Lower-tier wear or overdue service interval",
            owner="maintenance",
            blast_radius=1,
            reversibility="high",
            sensor_ids=ids("SCHEDULE_SERVICE"),
        ))
    if by_verdict.get("DECOMMISSION"):
        actions.append(PlaybookAction(
            id="DECOMMISSION_LOW_VALUE_ASSETS",
            priority="P2",
            label="Decommission sensors whose replacement cost exceeds operational value",
            reason="Low criticality + replacement cost outweighs downtime value",
            owner="ops",
            blast_radius=2,
            reversibility="medium",
            sensor_ids=ids("DECOMMISSION"),
        ))

    # cross-portfolio actions
    p0_count = sum(1 for f in findings if f.priority == "P0")
    if p0_count >= 3:
        actions.append(PlaybookAction(
            id="OPEN_REPLACEMENT_WAR_ROOM",
            priority="P0",
            label="Open replacement war room for multi-sensor emergency",
            reason=f"{p0_count} sensors require immediate action",
            owner="ops",
            blast_radius=5,
            reversibility="medium",
        ))

    no_backup_p0 = [
        f.sensor_id for f in findings
        if f.verdict == "REPLACE_NOW" and "BACKUP_AVAILABLE" not in f.reasons
    ]
    if no_backup_p0:
        actions.append(PlaybookAction(
            id="EXPEDITE_BACKUP_PROCUREMENT",
            priority="P1",
            label="Expedite backup-stock procurement for critical sensors without spares",
            reason="REPLACE_NOW sensors have no on-shelf backup",
            owner="procurement",
            blast_radius=3,
            reversibility="medium",
            sensor_ids=sorted(no_backup_p0),
        ))

    if risk_appetite == "cautious" and grade in ("C", "D", "F"):
        actions.append(PlaybookAction(
            id="SCHEDULE_FLEET_REPLACEMENT_AUDIT",
            priority="P2",
            label="Schedule full-fleet replacement audit",
            reason="Cautious appetite + degraded portfolio grade",
            owner="ops",
            blast_radius=2,
            reversibility="high",
        ))

    if not actions:
        actions.append(PlaybookAction(
            id="MAINTAIN_OBSERVABILITY",
            priority="P3",
            label="Maintain current observability cadence",
            reason="Fleet is healthy; no replacement action required",
            owner="ops",
            blast_radius=1,
            reversibility="high",
        ))

    # aggressive trim of lone P3 fallback when actionable items exist
    if risk_appetite == "aggressive":
        has_action = any(a.priority in ("P0", "P1") for a in actions)
        if has_action:
            actions = [a for a in actions if a.priority != "P3"]

    # dedupe by id, P0-first, then id asc
    seen: Dict[str, PlaybookAction] = {}
    for a in actions:
        seen[a.id] = a
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(seen.values(), key=lambda x: (order.get(x.priority, 9), x.id))


def _insights(findings: List[ReplacementFinding]) -> List[str]:
    out: List[str] = []
    total = len(findings)
    if total == 0:
        return ["EMPTY_FLEET"]

    eol = sum(1 for f in findings if f.verdict in ("REPLACE_NOW", "REPLACE_SOON", "SWAP_TO_BACKUP"))
    if eol >= max(2, total // 4):
        out.append(f"END_OF_LIFE_WAVE:{eol}_OF_{total}")

    chronic = sum(1 for f in findings if "CHRONIC_FAILURES" in f.reasons)
    if chronic >= 2:
        out.append(f"CHRONIC_FAILURE_CLUSTER:{chronic}")

    critical_p0 = sum(1 for f in findings
                      if f.priority == "P0" and "CRITICAL_ASSET" in f.reasons)
    if critical_p0:
        out.append(f"CRITICAL_ASSETS_AT_RISK:{critical_p0}")

    backup_gap = sum(1 for f in findings
                     if f.verdict in ("REPLACE_NOW", "REPLACE_SOON")
                     and "BACKUP_AVAILABLE" not in f.reasons)
    if backup_gap >= 2:
        out.append(f"BACKUP_INVENTORY_GAP:{backup_gap}")

    overdue = sum(1 for f in findings if "SERVICE_OVERDUE" in f.reasons)
    if overdue >= 3:
        out.append(f"SERVICE_BACKLOG:{overdue}")

    healthy = sum(1 for f in findings if f.verdict == "MONITOR")
    if healthy == total:
        out.append("FLEET_HEALTHY")

    if not out:
        out.append("MIXED_FLEET_SIGNALS")
    return out


def _grade(portfolio_risk: float, findings: List[ReplacementFinding]) -> str:
    p0 = sum(1 for f in findings if f.priority == "P0")
    critical_p0 = sum(1 for f in findings
                      if f.priority == "P0" and "CRITICAL_ASSET" in f.reasons)
    if critical_p0 >= 1 or p0 >= 3 or portfolio_risk >= 75:
        return "F"
    if p0 >= 1 or portfolio_risk >= 55:
        return "D"
    if portfolio_risk >= 35:
        return "C"
    if portfolio_risk >= 18:
        return "B"
    return "A"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ReplacementAdvisor:
    def __init__(
        self,
        risk_appetite: str = "balanced",
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        if risk_appetite not in ("cautious", "balanced", "aggressive"):
            raise ValueError(
                f"risk_appetite must be cautious|balanced|aggressive, got {risk_appetite!r}"
            )
        self.risk_appetite = risk_appetite
        self._now_fn = now_fn or _DEF_NOW

    def analyze(self, assets: Sequence[SensorAsset]) -> ReplacementReport:
        # deep-copy to avoid mutating caller's input
        safe_assets = [copy.deepcopy(a) for a in assets]
        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        findings = [_score_sensor(a, now, self.risk_appetite) for a in safe_assets]
        findings.sort(key=lambda f: ({"P0": 0, "P1": 1, "P2": 2, "P3": 3}[f.priority],
                                     -f.risk_score, f.sensor_id))

        scored = [f for f in findings if f.verdict != "INSUFFICIENT_DATA"]
        if scored:
            portfolio_risk = round(sum(f.risk_score for f in scored) / len(scored), 2)
        else:
            portfolio_risk = 0.0

        grade = _grade(portfolio_risk, findings)
        playbook = _build_playbook(findings, portfolio_risk, grade, self.risk_appetite)
        insights = _insights(findings)

        p0 = sum(1 for f in findings if f.priority == "P0")
        p1 = sum(1 for f in findings if f.priority == "P1")
        headline = (
            f"VERDICT: grade={grade} N={len(findings)} "
            f"P0={p0} P1={p1} portfolio_risk={portfolio_risk}"
        )
        return ReplacementReport(
            grade=grade,
            portfolio_risk=portfolio_risk,
            headline=headline,
            findings=findings,
            playbook=playbook,
            insights=insights,
            generated_at=now.isoformat(),
        )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def to_json(report: ReplacementReport) -> str:
    return json.dumps(asdict(report), sort_keys=True, indent=2, default=str)


def to_text(report: ReplacementReport) -> str:
    lines = [report.headline, ""]
    lines.append(f"Generated: {report.generated_at}")
    lines.append(f"Portfolio risk: {report.portfolio_risk}  Grade: {report.grade}")
    lines.append("")
    lines.append("Findings:")
    for f in report.findings:
        lp = f"{f.lifetime_pct}%" if f.lifetime_pct is not None else "n/a"
        er = f"{f.error_rate_30d}" if f.error_rate_30d is not None else "n/a"
        lines.append(
            f"  [{f.priority}] {f.sensor_id} -> {f.verdict} "
            f"(risk={f.risk_score}, lifetime={lp}, err30d={er})"
        )
        if f.reasons:
            lines.append(f"      reasons: {', '.join(f.reasons)}")
        if f.suggested_action:
            lines.append(f"      action: {f.suggested_action}")
    lines.append("")
    lines.append("Playbook:")
    for a in report.playbook:
        lines.append(f"  [{a.priority}] {a.id}: {a.label}  (owner={a.owner})")
        lines.append(f"      reason: {a.reason}")
        if a.sensor_ids:
            lines.append(f"      sensors: {', '.join(a.sensor_ids)}")
    lines.append("")
    lines.append("Insights:")
    for ins in report.insights:
        lines.append(f"  - {ins}")
    return "\n".join(lines)


def to_markdown(report: ReplacementReport) -> str:
    out: List[str] = []
    out.append(f"# Sensor Replacement Advisor — {report.headline}")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| Metric | Value |")
    out.append("| --- | --- |")
    out.append(f"| Grade | {report.grade} |")
    out.append(f"| Portfolio risk | {report.portfolio_risk} |")
    out.append(f"| Sensors | {len(report.findings)} |")
    out.append(f"| Generated | {report.generated_at} |")
    out.append("")
    out.append("## Findings")
    out.append("")
    out.append("| Sensor | Priority | Verdict | Risk | Lifetime % | Err30d | Reasons |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for f in report.findings:
        lp = f"{f.lifetime_pct}" if f.lifetime_pct is not None else ""
        er = f"{f.error_rate_30d}" if f.error_rate_30d is not None else ""
        out.append(
            f"| {f.sensor_id} | {f.priority} | {f.verdict} | {f.risk_score} | "
            f"{lp} | {er} | {', '.join(f.reasons)} |"
        )
    out.append("")
    out.append("## Playbook")
    out.append("")
    out.append("| Priority | Action | Owner | Blast | Reason |")
    out.append("| --- | --- | --- | --- | --- |")
    for a in report.playbook:
        out.append(
            f"| {a.priority} | {a.label} | {a.owner} | {a.blast_radius} | {a.reason} |"
        )
    out.append("")
    out.append("## Insights")
    out.append("")
    for ins in report.insights:
        out.append(f"- {ins}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CSV loader + CLI
# ---------------------------------------------------------------------------


def load_assets_from_csv(path: str) -> List[SensorAsset]:
    out: List[SensorAsset] = []
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            out.append(SensorAsset.from_record(row))
    return out


def _stdout_utf8() -> None:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vormap_replacement",
        description="Agentic per-sensor replacement / swap-out advisor",
    )
    parser.add_argument("csv", nargs="?", help="CSV of sensor assets (see README)")
    parser.add_argument("--risk", choices=("cautious", "balanced", "aggressive"),
                        default="balanced")
    parser.add_argument("--format", choices=("text", "md", "json"), default="text")
    parser.add_argument("--output", help="Write rendered report to this path")
    parser.add_argument("--demo", action="store_true",
                        help="Run with a built-in demo fleet")
    args = parser.parse_args(argv)
    _stdout_utf8()

    if args.demo:
        assets = _demo_fleet()
    elif args.csv:
        assets = load_assets_from_csv(args.csv)
    else:
        parser.error("provide a csv path or --demo")
        return 2

    advisor = ReplacementAdvisor(risk_appetite=args.risk)
    report = advisor.analyze(assets)

    if args.format == "json":
        rendered = to_json(report)
    elif args.format == "md":
        rendered = to_markdown(report)
    else:
        rendered = to_text(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
        print(f"wrote {len(rendered)} bytes -> {args.output}")
    else:
        print(rendered)
    return 0


def _demo_fleet() -> List[SensorAsset]:
    now = datetime.now(timezone.utc)
    def ago(days: int) -> str:
        return (now.replace(hour=0, minute=0, second=0, microsecond=0)).isoformat()
    return [
        SensorAsset(
            sensor_id="S-001",
            install_date="2018-01-15",
            expected_lifetime_days=1825,
            failure_count=6,
            error_count_30d=80,
            reading_count_30d=400,
            has_backup=True,
            criticality=5,
            replacement_cost_usd=1500,
            downtime_hours_if_failed=24,
        ),
        SensorAsset(
            sensor_id="S-002",
            install_date="2020-06-01",
            expected_lifetime_days=1825,
            failure_count=2,
            error_count_30d=20,
            reading_count_30d=400,
            criticality=4,
        ),
        SensorAsset(
            sensor_id="S-003",
            install_date="2024-03-01",
            expected_lifetime_days=1825,
            failure_count=0,
            error_count_30d=2,
            reading_count_30d=600,
            criticality=2,
        ),
        SensorAsset(
            sensor_id="S-004",
            install_date="2017-01-01",
            expected_lifetime_days=1825,
            failure_count=1,
            error_count_30d=5,
            reading_count_30d=300,
            criticality=1,
            replacement_cost_usd=3000,
            downtime_hours_if_failed=1,
        ),
    ]


if __name__ == "__main__":
    sys.exit(main())
