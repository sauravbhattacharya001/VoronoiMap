"""Tests for vormap_alarmdedup."""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from vormap_alarmdedup import (
    AlarmDedupAdvisor,
    AlarmEvent,
    _demo_alarms,
    to_json,
    to_markdown,
    to_text,
)


def _fixed_now():
    return datetime(2026, 5, 22, 16, 0, tzinfo=timezone.utc)


def _basic_advisor():
    return AlarmDedupAdvisor(now_fn=_fixed_now)


# ---------- input validation ----------


def test_empty_input_grade_a():
    rep = _basic_advisor().analyze([])
    assert rep.total_alarms == 0
    assert rep.incident_count == 0
    assert rep.grade == "A"
    assert "NO_ALARMS" in rep.insights


def test_invalid_risk_appetite():
    with pytest.raises(ValueError):
        _basic_advisor().analyze([], risk_appetite="reckless")


def test_negative_params_rejected():
    with pytest.raises(ValueError):
        _basic_advisor().analyze([], radius_meters=-1)


# ---------- demo dataset ----------


def test_demo_produces_incidents():
    rep = _basic_advisor().analyze(_demo_alarms())
    assert rep.total_alarms == 14
    assert rep.incident_count >= 3
    # pressure storm should be P0 (critical severity).
    assert rep.p0_count >= 1


def test_demo_grades_f_or_d():
    rep = _basic_advisor().analyze(_demo_alarms())
    assert rep.grade in ("F", "D")


def test_demo_page_oncall_action_present():
    rep = _basic_advisor().analyze(_demo_alarms())
    ids = {a.id for a in rep.playbook}
    assert "PAGE_ONCALL_ENGINEER" in ids


# ---------- dedup / flapping ----------


def test_per_sensor_cooldown_dedup():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("e1", "S", 0, 0, base, severity="warning", category="x"),
        AlarmEvent("e2", "S", 0, 0, base + timedelta(seconds=10), severity="warning", category="x"),
        AlarmEvent("e3", "S", 0, 0, base + timedelta(seconds=20), severity="warning", category="x"),
    ]
    rep = _basic_advisor().analyze(evs, cooldown_seconds=60)
    assert rep.deduped_alarm_count == 1
    assert rep.suppressed_alarm_count == 2


def test_flapping_detected_and_tuned():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent(f"e{i}", "FLAP", 0, 0, base + timedelta(seconds=i * 5), severity="warning", category="x")
        for i in range(5)
    ]
    rep = _basic_advisor().analyze(evs, cooldown_seconds=60)
    assert any("FLAPPING_SENSORS" in ins for ins in rep.insights)
    ids = {a.id for a in rep.playbook}
    assert "TUNE_HYSTERESIS_FLAPPING" in ids


def test_severity_upgrades_in_dedup():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("e1", "S", 0, 0, base, severity="warning", category="x"),
        AlarmEvent("e2", "S", 0, 0, base + timedelta(seconds=5), severity="critical", category="x"),
    ]
    rep = _basic_advisor().analyze(evs, cooldown_seconds=60)
    assert rep.incident_count == 1
    assert rep.incidents[0].severity == "critical"


# ---------- clustering ----------


def test_spatial_cluster_distinct_from_far_sensor():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("a1", "S1", 0, 0, base, severity="major", category="x"),
        AlarmEvent("a2", "S2", 1, 1, base + timedelta(seconds=10), severity="major", category="x"),
        AlarmEvent("a3", "S3", 500, 500, base + timedelta(seconds=5), severity="major", category="x"),
    ]
    rep = _basic_advisor().analyze(evs, radius_meters=5, window_seconds=300, cooldown_seconds=0)
    assert rep.incident_count == 2


def test_different_categories_do_not_merge():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("a1", "S1", 0, 0, base, severity="major", category="pressure"),
        AlarmEvent("a2", "S2", 0, 0, base, severity="major", category="temperature"),
    ]
    rep = _basic_advisor().analyze(evs, radius_meters=5, cooldown_seconds=0)
    assert rep.incident_count == 2


def test_temporal_window_separation():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("a1", "S1", 0, 0, base, severity="major", category="x"),
        AlarmEvent("a2", "S2", 0, 0, base + timedelta(hours=2), severity="major", category="x"),
    ]
    rep = _basic_advisor().analyze(evs, window_seconds=300, cooldown_seconds=0)
    assert rep.incident_count == 2


# ---------- verdict ladder ----------


def test_critical_single_becomes_page_oncall():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [AlarmEvent("a1", "S1", 0, 0, base, severity="critical", category="x")]
    rep = _basic_advisor().analyze(evs)
    assert rep.incidents[0].verdict == "PAGE_ONCALL"
    assert rep.incidents[0].priority == "P0"


def test_info_only_classified_noise():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [AlarmEvent("a1", "S1", 0, 0, base, severity="info", category="x")]
    rep = _basic_advisor().analyze(evs)
    assert rep.incidents[0].verdict == "NOISE"


def test_multi_sensor_minor_becomes_dispatch_today():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("a1", "S1", 0, 0, base, severity="minor", category="x"),
        AlarmEvent("a2", "S2", 1, 1, base + timedelta(seconds=10), severity="minor", category="x"),
        AlarmEvent("a3", "S3", 2, 0, base + timedelta(seconds=20), severity="minor", category="x"),
    ]
    rep = _basic_advisor().analyze(evs, radius_meters=10, cooldown_seconds=0)
    assert rep.incidents[0].verdict == "DISPATCH_TODAY"


# ---------- risk appetite ----------


def test_cautious_widens_window():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = [
        AlarmEvent("a1", "S1", 0, 0, base, severity="major", category="x"),
        AlarmEvent("a2", "S2", 1, 1, base + timedelta(seconds=350), severity="major", category="x"),
    ]
    rep_bal = _basic_advisor().analyze(evs, window_seconds=300, cooldown_seconds=0, risk_appetite="balanced")
    rep_cau = _basic_advisor().analyze(evs, window_seconds=300, cooldown_seconds=0, risk_appetite="cautious")
    # cautious widens window to 375s -> should merge; balanced (300s) does not.
    assert rep_bal.incident_count == 2
    assert rep_cau.incident_count == 1


def test_aggressive_trims_p3_fallback():
    rep = _basic_advisor().analyze(_demo_alarms(), risk_appetite="aggressive")
    p3_actions = [a for a in rep.playbook if a.priority == "P3"]
    # Should not have lonely P3 maintain action when P0/P1 exist.
    if any(a.priority in ("P0", "P1") for a in rep.playbook):
        assert not p3_actions


# ---------- determinism ----------


def test_deterministic_outputs():
    a = _basic_advisor().analyze(_demo_alarms())
    b = _basic_advisor().analyze(_demo_alarms())
    assert to_json(a) == to_json(b)


def test_inputs_not_mutated():
    evs = _demo_alarms()
    snap = [(e.id, e.severity, e.timestamp) for e in evs]
    _basic_advisor().analyze(evs)
    after = [(e.id, e.severity, e.timestamp) for e in evs]
    assert snap == after


# ---------- renderers ----------


def test_to_text_contains_sections():
    rep = _basic_advisor().analyze(_demo_alarms())
    txt = to_text(rep)
    assert "== Incidents ==" in txt
    assert "== Playbook ==" in txt
    assert "== Insights ==" in txt


def test_to_markdown_contains_tables():
    rep = _basic_advisor().analyze(_demo_alarms())
    md = to_markdown(rep)
    assert "## Summary" in md
    assert "## Incidents" in md
    assert "## Playbook" in md
    assert "## Insights" in md
    assert "|" in md


def test_to_json_byte_stable():
    rep = _basic_advisor().analyze(_demo_alarms())
    s1 = to_json(rep)
    s2 = to_json(rep)
    assert s1 == s2
    parsed = json.loads(s1)
    assert parsed["grade"] == rep.grade
    assert parsed["incident_count"] == rep.incident_count


# ---------- record ingest ----------


def test_from_record_iso_timestamp():
    rows = [
        {"id": "a1", "sensor_id": "S1", "x": 0, "y": 0, "timestamp": "2026-05-22T09:00:00Z",
         "severity": "major", "category": "x"},
    ]
    rep = _basic_advisor().analyze(rows)
    assert rep.total_alarms == 1
    assert rep.incidents[0].severity == "major"


def test_severity_alias_normalization():
    rows = [
        {"id": "a1", "sensor_id": "S1", "x": 0, "y": 0, "timestamp": "2026-05-22T09:00:00Z",
         "severity": "P1", "category": "x"},
    ]
    rep = _basic_advisor().analyze(rows)
    # P1 alias -> major
    assert rep.incidents[0].severity == "major"


# ---------- insights ----------


def test_alarm_storm_insight():
    base = datetime(2026, 5, 22, 9, 0, tzinfo=timezone.utc)
    evs = []
    for i in range(60):
        # Same sensor, all within cooldown -> heavy suppression.
        evs.append(
            AlarmEvent(f"e{i}", "S1", 0, 0, base + timedelta(seconds=i), severity="warning", category="x")
        )
    rep = _basic_advisor().analyze(evs, cooldown_seconds=120)
    assert "ALARM_STORM_DETECTED" in rep.insights
