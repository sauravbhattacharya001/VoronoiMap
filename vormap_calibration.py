#!/usr/bin/env python3
"""vormap_calibration - Agentic Per-Sensor Calibration Drift Advisor.

Time-series sibling to vormap_drift (which compares two point-set snapshots).
This module operates on per-sensor reading streams to detect:

    * BIAS_DRIFT       - mean shift vs reference or first-half baseline
    * GAIN_DRIFT       - variance inflation or collapse
    * STICKY_SENSOR    - constant value runs
    * NOISE_SPIKE      - recent stddev inflation without bias shift
    * OUT_OF_RANGE     - clamping at expected min/max
    * INSUFFICIENT_DATA
    * CALM

Pure stdlib, no pydantic. Deterministic. Never mutates input sensors.
"""

from __future__ import annotations

import argparse
import copy
import csv
import io
import json
import math
import os
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, List, Optional, Tuple

try:  # opportunistic numpy
    import numpy as np  # type: ignore  # noqa: F401
    _HAVE_NUMPY = True
except Exception:  # pragma: no cover - numpy not required
    _HAVE_NUMPY = False


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SensorTimeSeries:
    """A single sensor with timestamped scalar readings."""

    sensor_id: str
    readings: List[Tuple[float, float]] = field(default_factory=list)
    x: Optional[float] = None
    y: Optional[float] = None
    reference_value: Optional[float] = None
    expected_range: Optional[Tuple[float, float]] = None

    @classmethod
    def from_records(cls, sensor_id: str, rows: Iterable[dict]) -> "SensorTimeSeries":
        readings: List[Tuple[float, float]] = []
        x = y = ref = None
        emin = emax = None
        for row in rows:
            ts = float(row["timestamp"])
            val = float(row["value"])
            readings.append((ts, val))
            if x is None and row.get("x") not in (None, ""):
                x = float(row["x"])
            if y is None and row.get("y") not in (None, ""):
                y = float(row["y"])
            if ref is None and row.get("reference_value") not in (None, ""):
                ref = float(row["reference_value"])
            if emin is None and row.get("expected_min") not in (None, ""):
                emin = float(row["expected_min"])
            if emax is None and row.get("expected_max") not in (None, ""):
                emax = float(row["expected_max"])
        readings.sort(key=lambda r: r[0])
        exp = (emin, emax) if (emin is not None and emax is not None) else None
        return cls(
            sensor_id=sensor_id,
            readings=readings,
            x=x,
            y=y,
            reference_value=ref,
            expected_range=exp,
        )


@dataclass
class Reason:
    code: str
    weight: float
    detail: str


@dataclass
class SensorVerdict:
    sensor_id: str
    verdict: str
    priority: str
    calibration_risk: float
    reasons: List[Reason] = field(default_factory=list)
    n_readings: int = 0
    mean: Optional[float] = None
    stdev: Optional[float] = None


@dataclass
class PlaybookAction:
    id: str
    priority: str
    label: str
    reason: str
    owner: str
    blast_radius: int
    reversibility: str
    sensor_ids: List[str] = field(default_factory=list)


@dataclass
class PortfolioSummary:
    total_sensors: int = 0
    calm_count: int = 0
    bias_count: int = 0
    gain_count: int = 0
    sticky_count: int = 0
    noise_count: int = 0
    out_of_range_count: int = 0
    insufficient_count: int = 0
    mean_calibration_risk: float = 0.0
    worst_calibration_risk: float = 0.0
    portfolio_grade: str = "A"


@dataclass
class CalibrationReport:
    summary: PortfolioSummary
    per_sensor: List[SensorVerdict] = field(default_factory=list)
    playbook: List[PlaybookAction] = field(default_factory=list)
    insights: List[str] = field(default_factory=list)
    risk_appetite: str = "balanced"
    generated_at: str = ""
    headline: str = ""


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


_BASE_SEVERITY = {
    "CALM": 5.0,
    "INSUFFICIENT_DATA": 20.0,
    "NOISE_SPIKE": 45.0,
    "STICKY_SENSOR": 50.0,
    "GAIN_DRIFT": 55.0,
    "BIAS_DRIFT": 60.0,
    "OUT_OF_RANGE": 70.0,
}

_RISK_MULT = {"cautious": 1.15, "balanced": 1.0, "aggressive": 0.85}


def _safe_stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    try:
        return statistics.pstdev(values)
    except statistics.StatisticsError:
        return 0.0


def _classify_sensor(
    sensor: SensorTimeSeries,
    *,
    drift_threshold: float,
    sticky_run: int,
    appetite_mult: float,
) -> SensorVerdict:
    readings = list(sensor.readings)
    n = len(readings)
    reasons: List[Reason] = []

    if n < 5:
        reasons.append(
            Reason(
                code="INSUFFICIENT_HISTORY",
                weight=20.0,
                detail=f"only {n} readings (need >=5)",
            )
        )
        risk = round(min(100.0, _BASE_SEVERITY["INSUFFICIENT_DATA"] * appetite_mult), 2)
        return SensorVerdict(
            sensor_id=sensor.sensor_id,
            verdict="INSUFFICIENT_DATA",
            priority=_bucket(risk),
            calibration_risk=risk,
            reasons=reasons,
            n_readings=n,
        )

    values = [v for _, v in readings]
    mean = statistics.fmean(values)
    sd = _safe_stdev(values)

    # ----- Out-of-range / clamping -----
    out_of_range = False
    if sensor.expected_range is not None:
        emin, emax = sensor.expected_range
        max_hits = sum(1 for v in values if v >= emax - 1e-9)
        min_hits = sum(1 for v in values if v <= emin + 1e-9)
        if max_hits / n >= 0.20:
            reasons.append(
                Reason(
                    code="CLAMPING_AT_MAX",
                    weight=70.0,
                    detail=f"{max_hits}/{n} readings at/above expected max {emax}",
                )
            )
            out_of_range = True
        if min_hits / n >= 0.20:
            reasons.append(
                Reason(
                    code="CLAMPING_AT_MIN",
                    weight=70.0,
                    detail=f"{min_hits}/{n} readings at/below expected min {emin}",
                )
            )
            out_of_range = True

    # ----- Sticky detection -----
    sticky = False
    run = 1
    longest_run = 1
    for i in range(1, n):
        if abs(values[i] - values[i - 1]) < 1e-12:
            run += 1
            longest_run = max(longest_run, run)
        else:
            run = 1
    tail = values[-min(sticky_run, n) :]
    tail_sd = _safe_stdev(tail) if len(tail) >= 2 else 0.0
    if longest_run >= sticky_run:
        reasons.append(
            Reason(
                code="STICKY_CONSTANT_VALUE",
                weight=50.0,
                detail=f"{longest_run} consecutive identical readings",
            )
        )
        sticky = True
    elif sd > 0 and tail_sd < sd * 0.02 and len(tail) >= sticky_run:
        reasons.append(
            Reason(
                code="STICKY_CONSTANT_VALUE",
                weight=50.0,
                detail=f"last {len(tail)} readings have ~zero stdev",
            )
        )
        sticky = True

    # ----- Bias detection -----
    bias = False
    if sensor.reference_value is not None:
        delta = mean - sensor.reference_value
        bound = max(drift_threshold, 2.0 * sd)
        if abs(delta) > bound:
            reasons.append(
                Reason(
                    code="BIAS_VS_REFERENCE",
                    weight=60.0,
                    detail=f"mean {mean:.4g} vs reference {sensor.reference_value:.4g} (delta {delta:+.4g})",
                )
            )
            bias = True

    half = n // 2
    first = values[:half]
    second = values[half:]
    if half >= 2:
        m1 = statistics.fmean(first)
        m2 = statistics.fmean(second)
        sd1 = _safe_stdev(first)
        sd2 = _safe_stdev(second)
        pooled = max(sd1, sd2, 1e-9)
        delta = m2 - m1
        if abs(delta) > drift_threshold and abs(delta) > 2.0 * pooled:
            reasons.append(
                Reason(
                    code="BIAS_VS_BASELINE",
                    weight=60.0,
                    detail=f"first-half mean {m1:.4g} vs second-half {m2:.4g} (delta {delta:+.4g})",
                )
            )
            bias = True

        # ----- Gain drift -----
        gain = False
        if sd1 > 1e-9:
            ratio = sd2 / sd1
            if ratio >= 3.0:
                reasons.append(
                    Reason(
                        code="STD_INFLATION_3X",
                        weight=55.0,
                        detail=f"stdev grew {ratio:.2f}x (first {sd1:.4g} -> second {sd2:.4g})",
                    )
                )
                gain = True
            elif ratio >= 2.0:
                reasons.append(
                    Reason(
                        code="STD_INFLATION_2X",
                        weight=50.0,
                        detail=f"stdev grew {ratio:.2f}x (first {sd1:.4g} -> second {sd2:.4g})",
                    )
                )
                gain = True
            elif ratio <= 0.5:
                reasons.append(
                    Reason(
                        code="STD_COLLAPSE",
                        weight=50.0,
                        detail=f"stdev shrank to {ratio:.2f}x baseline (possible sensor freezing)",
                    )
                )
                gain = True
        elif sd2 > drift_threshold:
            reasons.append(
                Reason(
                    code="STD_INFLATION_3X",
                    weight=55.0,
                    detail=f"baseline stdev ~0 but recent stdev {sd2:.4g}",
                )
            )
            gain = True

        # ----- Noise burst (recent quarter window) -----
        recent_window = max(3, n // 4)
        recent = values[-recent_window:]
        recent_sd = _safe_stdev(recent)
        baseline = values[: max(2, n - recent_window)]
        baseline_sd = _safe_stdev(baseline)
        if baseline_sd > 1e-9 and recent_sd >= 3.0 * baseline_sd and not bias:
            reasons.append(
                Reason(
                    code="RECENT_NOISE_BURST",
                    weight=45.0,
                    detail=f"last {len(recent)} readings stdev {recent_sd:.4g} vs baseline {baseline_sd:.4g}",
                )
            )
    else:
        gain = False

    # ----- Verdict ladder (most severe wins) -----
    verdict = "CALM"
    base = _BASE_SEVERITY["CALM"]
    # out_of_range trumps everything else by base severity
    codes = {r.code for r in reasons}
    if {"CLAMPING_AT_MAX", "CLAMPING_AT_MIN"} & codes:
        verdict = "OUT_OF_RANGE"
        base = _BASE_SEVERITY["OUT_OF_RANGE"]
    elif {"BIAS_VS_REFERENCE", "BIAS_VS_BASELINE"} & codes:
        verdict = "BIAS_DRIFT"
        base = _BASE_SEVERITY["BIAS_DRIFT"]
    elif {"STD_INFLATION_3X", "STD_INFLATION_2X", "STD_COLLAPSE"} & codes:
        verdict = "GAIN_DRIFT"
        base = _BASE_SEVERITY["GAIN_DRIFT"]
    elif "STICKY_CONSTANT_VALUE" in codes:
        verdict = "STICKY_SENSOR"
        base = _BASE_SEVERITY["STICKY_SENSOR"]
    elif "RECENT_NOISE_BURST" in codes:
        verdict = "NOISE_SPIKE"
        base = _BASE_SEVERITY["NOISE_SPIKE"]

    if verdict == "CALM":
        reasons.append(Reason(code="HEALTHY", weight=5.0, detail="no calibration anomalies detected"))

    # ----- Risk score -----
    if reasons:
        top = max(r.weight for r in reasons)
        rest = sorted([r.weight for r in reasons], reverse=True)[1:]
        risk = top + 0.4 * min(sum(rest), 60.0)
    else:
        risk = base
    risk = round(max(0.0, min(100.0, risk * appetite_mult)), 2)

    return SensorVerdict(
        sensor_id=sensor.sensor_id,
        verdict=verdict,
        priority=_bucket(risk),
        calibration_risk=risk,
        reasons=reasons,
        n_readings=n,
        mean=round(mean, 6),
        stdev=round(sd, 6),
    )


def _bucket(risk: float) -> str:
    if risk >= 75:
        return "P0"
    if risk >= 55:
        return "P1"
    if risk >= 35:
        return "P2"
    return "P3"


_PRIO_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


# ---------------------------------------------------------------------------
# Advisor
# ---------------------------------------------------------------------------


class CalibrationAdvisor:
    def __init__(
        self,
        *,
        risk_appetite: str = "balanced",
        drift_threshold: float = 0.5,
        sticky_run: int = 5,
        now_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        if risk_appetite not in _RISK_MULT:
            raise ValueError(f"unknown risk_appetite: {risk_appetite!r}")
        if drift_threshold < 0:
            raise ValueError("drift_threshold must be non-negative")
        if sticky_run < 2:
            raise ValueError("sticky_run must be >= 2")
        self.risk_appetite = risk_appetite
        self.drift_threshold = drift_threshold
        self.sticky_run = sticky_run
        self._now_fn = now_fn or time.time

    # ---- main entry point ----
    def analyze(self, sensors: List[SensorTimeSeries]) -> CalibrationReport:
        sensors = copy.deepcopy(list(sensors))
        mult = _RISK_MULT[self.risk_appetite]
        per: List[SensorVerdict] = []
        for s in sensors:
            per.append(
                _classify_sensor(
                    s,
                    drift_threshold=self.drift_threshold,
                    sticky_run=self.sticky_run,
                    appetite_mult=mult,
                )
            )
        # deterministic order: priority asc, risk desc, id asc
        per.sort(key=lambda v: (_PRIO_ORDER[v.priority], -v.calibration_risk, v.sensor_id))

        summary = self._summarize(per)
        playbook = self._build_playbook(per, summary)
        insights = self._insights(per, summary)

        ts = datetime.fromtimestamp(self._now_fn(), tz=timezone.utc).isoformat()
        head = self._headline(summary, per)
        return CalibrationReport(
            summary=summary,
            per_sensor=per,
            playbook=playbook,
            insights=insights,
            risk_appetite=self.risk_appetite,
            generated_at=ts,
            headline=head,
        )

    # ---- summary ----
    @staticmethod
    def _summarize(per: List[SensorVerdict]) -> PortfolioSummary:
        s = PortfolioSummary()
        s.total_sensors = len(per)
        if not per:
            s.portfolio_grade = "A"
            return s
        for v in per:
            if v.verdict == "CALM":
                s.calm_count += 1
            elif v.verdict == "BIAS_DRIFT":
                s.bias_count += 1
            elif v.verdict == "GAIN_DRIFT":
                s.gain_count += 1
            elif v.verdict == "STICKY_SENSOR":
                s.sticky_count += 1
            elif v.verdict == "NOISE_SPIKE":
                s.noise_count += 1
            elif v.verdict == "OUT_OF_RANGE":
                s.out_of_range_count += 1
            elif v.verdict == "INSUFFICIENT_DATA":
                s.insufficient_count += 1
        risks = [v.calibration_risk for v in per]
        s.mean_calibration_risk = round(sum(risks) / len(risks), 2)
        s.worst_calibration_risk = round(max(risks), 2)

        p0 = [v for v in per if v.priority == "P0"]
        p0_bias = [v for v in p0 if v.verdict == "BIAS_DRIFT"]
        if (p0_bias and len(p0) >= 1) or len(p0) >= 2:
            grade = "F"
        elif len(p0) >= 1:
            grade = "D"
        elif s.mean_calibration_risk >= 40:
            grade = "C"
        elif s.mean_calibration_risk >= 20:
            grade = "B"
        else:
            grade = "A"
        s.portfolio_grade = grade
        return s

    # ---- playbook ----
    def _build_playbook(
        self, per: List[SensorVerdict], summary: PortfolioSummary
    ) -> List[PlaybookAction]:
        actions: List[PlaybookAction] = []

        def ids(verdict: str) -> List[str]:
            return sorted(v.sensor_id for v in per if v.verdict == verdict)

        bias_ids = ids("BIAS_DRIFT")
        sticky_ids = ids("STICKY_SENSOR")
        noise_ids = ids("NOISE_SPIKE")
        oor_ids = ids("OUT_OF_RANGE")
        gain_ids = ids("GAIN_DRIFT")
        insuf_ids = ids("INSUFFICIENT_DATA")

        if bias_ids:
            actions.append(
                PlaybookAction(
                    id="RECALIBRATE_BIASED_SENSORS",
                    priority="P0",
                    label="Recalibrate biased sensors against known references",
                    reason=f"{len(bias_ids)} sensor(s) show mean drift beyond threshold",
                    owner="field_team",
                    blast_radius=3,
                    reversibility="medium",
                    sensor_ids=bias_ids,
                )
            )

        suspect = len(sticky_ids) + len(oor_ids) + len(bias_ids)
        if summary.total_sensors and suspect / summary.total_sensors >= 0.5:
            quarantine = sorted(set(sticky_ids + oor_ids + bias_ids))
            actions.append(
                PlaybookAction(
                    id="QUARANTINE_SENSOR_DATA",
                    priority="P0",
                    label="Quarantine suspect sensor data pending review",
                    reason=f"{len(quarantine)}/{summary.total_sensors} sensors look untrustworthy",
                    owner="data_steward",
                    blast_radius=4,
                    reversibility="medium",
                    sensor_ids=quarantine,
                )
            )

        if sticky_ids:
            actions.append(
                PlaybookAction(
                    id="INSPECT_STICKY_SENSORS",
                    priority="P1",
                    label="Physically inspect sensors reporting constant values",
                    reason=f"{len(sticky_ids)} sensor(s) appear stuck",
                    owner="maintenance",
                    blast_radius=2,
                    reversibility="high",
                    sensor_ids=sticky_ids,
                )
            )

        if noise_ids:
            actions.append(
                PlaybookAction(
                    id="INVESTIGATE_NOISE_BURST",
                    priority="P1",
                    label="Investigate recent noise burst on flagged sensors",
                    reason=f"{len(noise_ids)} sensor(s) show recent stddev inflation",
                    owner="ops",
                    blast_radius=2,
                    reversibility="high",
                    sensor_ids=noise_ids,
                )
            )

        if len(oor_ids) >= 2:
            actions.append(
                PlaybookAction(
                    id="EXPAND_DYNAMIC_RANGE",
                    priority="P1",
                    label="Expand or recalibrate dynamic range for clamping sensors",
                    reason=f"{len(oor_ids)} sensor(s) clamping at expected min/max",
                    owner="maintenance",
                    blast_radius=3,
                    reversibility="medium",
                    sensor_ids=oor_ids,
                )
            )

        if len(gain_ids) >= 3:
            actions.append(
                PlaybookAction(
                    id="REGRESS_BASELINE_DRIFT",
                    priority="P2",
                    label="Regress baseline against gain-drifted sensors",
                    reason=f"{len(gain_ids)} sensor(s) show variance drift",
                    owner="data_steward",
                    blast_radius=2,
                    reversibility="high",
                    sensor_ids=gain_ids,
                )
            )

        if len(insuf_ids) >= 3:
            actions.append(
                PlaybookAction(
                    id="INCREASE_SAMPLING_DENSITY",
                    priority="P2",
                    label="Increase sampling cadence for sparse sensors",
                    reason=f"{len(insuf_ids)} sensor(s) have <5 readings",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    sensor_ids=insuf_ids,
                )
            )

        if self.risk_appetite == "cautious" and summary.portfolio_grade in {"C", "D", "F"}:
            actions.append(
                PlaybookAction(
                    id="SCHEDULE_CALIBRATION_AUDIT",
                    priority="P2",
                    label="Schedule a fleet-wide calibration audit",
                    reason=f"portfolio grade {summary.portfolio_grade} under cautious posture",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    sensor_ids=[],
                )
            )

        has_higher = any(a.priority in {"P0", "P1"} for a in actions)
        include_fallback = not (self.risk_appetite == "aggressive" and has_higher)
        if not actions or include_fallback:
            actions.append(
                PlaybookAction(
                    id="MAINTAIN_FLEET_OBSERVABILITY",
                    priority="P3",
                    label="Maintain calibration observability cadence",
                    reason="no urgent actions required",
                    owner="ops",
                    blast_radius=1,
                    reversibility="high",
                    sensor_ids=[],
                )
            )

        # dedupe by id, P0-first, id asc
        seen = set()
        ordered: List[PlaybookAction] = []
        for a in sorted(actions, key=lambda x: (_PRIO_ORDER[x.priority], x.id)):
            if a.id in seen:
                continue
            seen.add(a.id)
            ordered.append(a)
        return ordered

    # ---- insights ----
    @staticmethod
    def _insights(per: List[SensorVerdict], summary: PortfolioSummary) -> List[str]:
        out: List[str] = []
        if not per:
            return ["EMPTY_FLEET"]
        if summary.bias_count >= 3:
            out.append("BIAS_FLEET_PATTERN")
        if summary.sticky_count >= 2:
            out.append("STICKY_SENSOR_CLUSTER")
        if summary.out_of_range_count >= 2:
            out.append("RANGE_SATURATION")
        if summary.insufficient_count / summary.total_sensors >= 0.30:
            out.append("SPARSE_TELEMETRY")
        if summary.calm_count / summary.total_sensors >= 0.80:
            out.append("HEALTHY_FLEET")
        if not out:
            out.append("MIXED_FLEET_SIGNALS")
        return out

    # ---- headline ----
    @staticmethod
    def _headline(summary: PortfolioSummary, per: List[SensorVerdict]) -> str:
        if summary.total_sensors == 0:
            return "VERDICT: grade=A no sensors supplied"
        p0 = sum(1 for v in per if v.priority == "P0")
        p1 = sum(1 for v in per if v.priority == "P1")
        return (
            f"VERDICT: grade={summary.portfolio_grade} "
            f"N={summary.total_sensors} P0={p0} P1={p1} "
            f"mean_risk={summary.mean_calibration_risk}"
        )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _reasons_str(reasons: List[Reason], limit: int = 3) -> str:
    return "; ".join(r.code for r in reasons[:limit])


def to_text(report: CalibrationReport) -> str:
    lines: List[str] = []
    lines.append(report.headline)
    lines.append("")
    s = report.summary
    lines.append(
        f"Portfolio: total={s.total_sensors} calm={s.calm_count} bias={s.bias_count} "
        f"gain={s.gain_count} sticky={s.sticky_count} noise={s.noise_count} "
        f"out_of_range={s.out_of_range_count} insufficient={s.insufficient_count}"
    )
    lines.append(
        f"Risk: mean={s.mean_calibration_risk} worst={s.worst_calibration_risk} grade={s.portfolio_grade}"
    )
    lines.append("")
    lines.append("Sensors (priority order):")
    if not report.per_sensor:
        lines.append("  (none)")
    for v in report.per_sensor:
        lines.append(
            f"  [{v.priority}] {v.sensor_id} - {v.verdict} risk={v.calibration_risk} "
            f"n={v.n_readings} reasons=({_reasons_str(v.reasons)})"
        )
    lines.append("")
    lines.append("Playbook:")
    for a in report.playbook:
        lines.append(f"  [{a.priority}] {a.id} ({a.owner}, blast={a.blast_radius}) - {a.label}")
        lines.append(f"        why: {a.reason}")
    lines.append("")
    lines.append("Insights:")
    for ins in report.insights:
        lines.append(f"  - {ins}")
    return "\n".join(lines)


def to_markdown(report: CalibrationReport) -> str:
    s = report.summary
    out: List[str] = []
    out.append(f"# Calibration Advisor Report")
    out.append("")
    out.append(f"_{report.headline}_")
    out.append("")
    out.append("## Summary")
    out.append("")
    out.append("| metric | value |")
    out.append("| --- | --- |")
    out.append(f"| total sensors | {s.total_sensors} |")
    out.append(f"| calm | {s.calm_count} |")
    out.append(f"| bias drift | {s.bias_count} |")
    out.append(f"| gain drift | {s.gain_count} |")
    out.append(f"| sticky | {s.sticky_count} |")
    out.append(f"| noise spike | {s.noise_count} |")
    out.append(f"| out of range | {s.out_of_range_count} |")
    out.append(f"| insufficient data | {s.insufficient_count} |")
    out.append(f"| mean risk | {s.mean_calibration_risk} |")
    out.append(f"| worst risk | {s.worst_calibration_risk} |")
    out.append(f"| grade | {s.portfolio_grade} |")
    out.append(f"| risk_appetite | {report.risk_appetite} |")
    out.append("")
    out.append("## Sensors")
    out.append("")
    out.append("| priority | sensor_id | verdict | risk | n | reasons |")
    out.append("| --- | --- | --- | --- | --- | --- |")
    if not report.per_sensor:
        out.append("| - | - | - | - | - | (no sensors) |")
    for v in report.per_sensor:
        out.append(
            f"| {v.priority} | {v.sensor_id} | {v.verdict} | {v.calibration_risk} | "
            f"{v.n_readings} | {_reasons_str(v.reasons)} |"
        )
    out.append("")
    out.append("## Playbook")
    out.append("")
    out.append("| priority | id | owner | blast | reversibility | label | sensors |")
    out.append("| --- | --- | --- | --- | --- | --- | --- |")
    for a in report.playbook:
        ids = ",".join(a.sensor_ids) if a.sensor_ids else "-"
        out.append(
            f"| {a.priority} | {a.id} | {a.owner} | {a.blast_radius} | {a.reversibility} | "
            f"{a.label} | {ids} |"
        )
    out.append("")
    out.append("## Insights")
    out.append("")
    for ins in report.insights:
        out.append(f"- {ins}")
    out.append("")
    return "\n".join(out)


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, (CalibrationReport, SensorVerdict, PortfolioSummary, PlaybookAction, Reason)):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def to_json(report: CalibrationReport) -> str:
    return json.dumps(_to_jsonable(report), sort_keys=True, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_csv(path: str) -> List[SensorTimeSeries]:
    buckets: dict[str, list[dict]] = {}
    with open(path, "r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sid = row["sensor_id"]
            buckets.setdefault(sid, []).append(row)
    return [SensorTimeSeries.from_records(sid, rows) for sid, rows in sorted(buckets.items())]


def _utf8_stdout() -> None:
    if os.name == "nt":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _utf8_stdout()
    p = argparse.ArgumentParser(description="Agentic per-sensor calibration drift advisor.")
    p.add_argument("csv", help="CSV: sensor_id,timestamp,value[,x,y,reference_value,expected_min,expected_max]")
    p.add_argument("--risk", choices=["cautious", "balanced", "aggressive"], default="balanced")
    p.add_argument("--drift-threshold", type=float, default=0.5)
    p.add_argument("--sticky-run", type=int, default=5)
    p.add_argument("--format", choices=["text", "md", "markdown", "json"], default="text")
    p.add_argument("--output", default=None, help="optional path; default stdout")
    args = p.parse_args(argv)

    sensors = _load_csv(args.csv)
    advisor = CalibrationAdvisor(
        risk_appetite=args.risk,
        drift_threshold=args.drift_threshold,
        sticky_run=args.sticky_run,
    )
    report = advisor.analyze(sensors)

    if args.format == "text":
        rendered = to_text(report)
    elif args.format in ("md", "markdown"):
        rendered = to_markdown(report)
    else:
        rendered = to_json(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(rendered)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
