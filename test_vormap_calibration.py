"""Tests for vormap_calibration."""

from __future__ import annotations

import json
import math
import random
from typing import List

import pytest

from vormap_calibration import (
    CalibrationAdvisor,
    SensorTimeSeries,
    to_json,
    to_markdown,
    to_text,
)


def _calm(n: int = 20, base: float = 10.0, jitter: float = 0.05) -> SensorTimeSeries:
    rng = random.Random(42)
    readings = [(float(i), base + rng.uniform(-jitter, jitter)) for i in range(n)]
    return SensorTimeSeries(sensor_id="calm-1", readings=readings, reference_value=base)


def test_empty_fleet_grade_a_and_insight():
    rep = CalibrationAdvisor().analyze([])
    assert rep.summary.total_sensors == 0
    assert rep.summary.portfolio_grade == "A"
    assert "EMPTY_FLEET" in rep.insights


def test_single_calm_sensor_is_calm():
    rep = CalibrationAdvisor().analyze([_calm()])
    assert len(rep.per_sensor) == 1
    v = rep.per_sensor[0]
    assert v.verdict == "CALM"
    assert v.priority == "P3"
    assert v.calibration_risk < 20


def test_bias_drift_vs_reference():
    rng = random.Random(0)
    # mean far from reference, low noise
    readings = [(float(i), 20.0 + rng.uniform(-0.05, 0.05)) for i in range(30)]
    sensor = SensorTimeSeries(sensor_id="bias-1", readings=readings, reference_value=10.0)
    rep = CalibrationAdvisor().analyze([sensor])
    v = rep.per_sensor[0]
    assert v.verdict == "BIAS_DRIFT"
    assert v.priority in {"P0", "P1"}
    assert any(r.code == "BIAS_VS_REFERENCE" for r in v.reasons)
    # the playbook always escalates bias to P0
    assert any(a.id == "RECALIBRATE_BIASED_SENSORS" and a.priority == "P0" for a in rep.playbook)


def test_gain_drift_std_inflation():
    rng = random.Random(1)
    first = [(float(i), 10.0 + rng.uniform(-0.05, 0.05)) for i in range(20)]
    second = [(float(20 + i), 10.0 + rng.uniform(-3.0, 3.0)) for i in range(20)]
    sensor = SensorTimeSeries(sensor_id="gain-1", readings=first + second)
    rep = CalibrationAdvisor().analyze([sensor])
    v = rep.per_sensor[0]
    assert v.verdict in {"GAIN_DRIFT", "NOISE_SPIKE"}
    # we explicitly want GAIN_DRIFT here (large vs first half is the dominant signal)
    assert v.verdict == "GAIN_DRIFT"
    assert any(r.code.startswith("STD_INFLATION") for r in v.reasons)


def test_sticky_sensor_detected():
    readings = [(float(i), 7.0) for i in range(30)]
    sensor = SensorTimeSeries(sensor_id="stuck-1", readings=readings)
    rep = CalibrationAdvisor().analyze([sensor])
    v = rep.per_sensor[0]
    assert v.verdict == "STICKY_SENSOR"
    assert any(r.code == "STICKY_CONSTANT_VALUE" for r in v.reasons)


def test_noise_spike_without_bias():
    rng = random.Random(2)
    base = [(float(i), 10.0 + rng.uniform(-0.05, 0.05)) for i in range(30)]
    # add high-noise tail but zero-mean so no bias shift
    tail = [(float(30 + i), 10.0 + rng.uniform(-2.0, 2.0)) for i in range(10)]
    sensor = SensorTimeSeries(sensor_id="noise-1", readings=base + tail)
    rep = CalibrationAdvisor().analyze([sensor])
    v = rep.per_sensor[0]
    # depending on how the second-half stats land, this can be GAIN_DRIFT (broader)
    # or NOISE_SPIKE; we accept either provided no bias reason triggered.
    assert v.verdict in {"NOISE_SPIKE", "GAIN_DRIFT"}
    assert not any(r.code.startswith("BIAS_") for r in v.reasons)


def test_out_of_range_clamping():
    readings = [(float(i), 100.0) for i in range(20)] + [(float(20 + i), 50.0) for i in range(5)]
    sensor = SensorTimeSeries(
        sensor_id="oor-1",
        readings=readings,
        expected_range=(0.0, 100.0),
    )
    rep = CalibrationAdvisor().analyze([sensor])
    v = rep.per_sensor[0]
    assert v.verdict == "OUT_OF_RANGE"
    assert v.priority in {"P0", "P1"}


def test_insufficient_data_verdict():
    sensor = SensorTimeSeries(sensor_id="thin-1", readings=[(0.0, 1.0), (1.0, 1.1)])
    rep = CalibrationAdvisor().analyze([sensor])
    v = rep.per_sensor[0]
    assert v.verdict == "INSUFFICIENT_DATA"


def test_risk_appetite_monotonicity():
    rng = random.Random(3)
    readings = [(float(i), 12.0 + rng.uniform(-0.05, 0.05)) for i in range(30)]
    s = SensorTimeSeries(sensor_id="b", readings=readings, reference_value=10.0)
    risks = {}
    for app in ("aggressive", "balanced", "cautious"):
        rep = CalibrationAdvisor(risk_appetite=app).analyze([s])
        risks[app] = rep.per_sensor[0].calibration_risk
    assert risks["aggressive"] <= risks["balanced"] <= risks["cautious"]


def test_deterministic_json_byte_equal():
    sensors = [_calm(), SensorTimeSeries(sensor_id="stuck", readings=[(i, 1.0) for i in range(20)])]
    a = CalibrationAdvisor(now_fn=lambda: 1700000000.0)
    b = CalibrationAdvisor(now_fn=lambda: 1700000000.0)
    j1 = to_json(a.analyze(sensors))
    j2 = to_json(b.analyze(sensors))
    assert j1 == j2
    # also valid JSON
    parsed = json.loads(j1)
    assert "summary" in parsed and "per_sensor" in parsed and "playbook" in parsed


def test_markdown_has_all_sections_always():
    rep = CalibrationAdvisor().analyze([])
    md = to_markdown(rep)
    for header in ("## Summary", "## Sensors", "## Playbook", "## Insights"):
        assert header in md


def test_text_renderer_non_empty():
    rep = CalibrationAdvisor().analyze([_calm()])
    txt = to_text(rep)
    assert "VERDICT" in txt
    assert "Playbook" in txt
    assert "Insights" in txt


def test_playbook_p0_first_ordering():
    # construct portfolio with both P0 (bias) and P3 (calm)
    rng = random.Random(4)
    biased = SensorTimeSeries(
        sensor_id="bias-A",
        readings=[(float(i), 20.0 + rng.uniform(-0.05, 0.05)) for i in range(30)],
        reference_value=10.0,
    )
    rep = CalibrationAdvisor().analyze([biased, _calm()])
    priorities = [a.priority for a in rep.playbook]
    # all P0 actions appear before P1/P2/P3
    p_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    nums = [p_order[p] for p in priorities]
    assert nums == sorted(nums)
    assert nums[0] == 0  # at least one P0


def test_never_mutates_input():
    sensor = _calm()
    sensors = [sensor]
    snapshot = [(t, v) for t, v in sensor.readings]
    adv = CalibrationAdvisor()
    r1 = adv.analyze(sensors)
    r2 = adv.analyze(sensors)
    # input list is unchanged
    assert sensors[0].readings == snapshot
    # results match
    assert r1.summary.portfolio_grade == r2.summary.portfolio_grade
    assert r1.per_sensor[0].calibration_risk == r2.per_sensor[0].calibration_risk


def test_cautious_adds_schedule_audit_when_grade_low():
    rng = random.Random(5)
    biased = SensorTimeSeries(
        sensor_id="bias-X",
        readings=[(float(i), 20.0 + rng.uniform(-0.05, 0.05)) for i in range(30)],
        reference_value=10.0,
    )
    rep = CalibrationAdvisor(risk_appetite="cautious").analyze([biased])
    assert rep.summary.portfolio_grade in {"C", "D", "F"}
    ids = {a.id for a in rep.playbook}
    assert "SCHEDULE_CALIBRATION_AUDIT" in ids


def test_aggressive_trims_fallback_when_higher_present():
    rng = random.Random(6)
    biased = SensorTimeSeries(
        sensor_id="bias-Y",
        readings=[(float(i), 20.0 + rng.uniform(-0.05, 0.05)) for i in range(30)],
        reference_value=10.0,
    )
    rep_b = CalibrationAdvisor(risk_appetite="balanced").analyze([biased])
    rep_a = CalibrationAdvisor(risk_appetite="aggressive").analyze([biased])
    ids_b = {a.id for a in rep_b.playbook}
    ids_a = {a.id for a in rep_a.playbook}
    assert "MAINTAIN_FLEET_OBSERVABILITY" in ids_b
    assert "MAINTAIN_FLEET_OBSERVABILITY" not in ids_a


def test_from_records_classmethod():
    rows = [
        {"timestamp": "1", "value": "10.0", "reference_value": "10.0", "expected_min": "0", "expected_max": "100"},
        {"timestamp": "2", "value": "10.1"},
        {"timestamp": "3", "value": "10.2"},
    ]
    s = SensorTimeSeries.from_records("s1", rows)
    assert s.sensor_id == "s1"
    assert len(s.readings) == 3
    assert s.reference_value == 10.0
    assert s.expected_range == (0.0, 100.0)
