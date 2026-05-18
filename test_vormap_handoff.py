"""Tests for vormap_handoff.HandoffAdvisor."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

import vormap_handoff as vh


FIXED_NOW = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)


def fixed_now() -> datetime:
    return FIXED_NOW


def make_advisor() -> vh.HandoffAdvisor:
    return vh.HandoffAdvisor(now_fn=fixed_now)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# basic shape
# ---------------------------------------------------------------------------


def test_empty_inputs_quiet_shift_grade_a():
    rep = make_advisor().scan()
    assert rep.items == []
    assert rep.grade == "A"
    assert "QUIET_SHIFT" in rep.insights
    assert rep.playbook  # at least the fallback action
    assert any(a.id == "CONFIRM_CREW_BRIEFING" for a in rep.playbook)


def test_low_severity_incident_triages_monitor():
    incidents = [
        {
            "id": "inc1",
            "point": (1.0, 1.0),
            "kind": "data_gap",
            "severity": 1,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=1)),
        }
    ]
    rep = make_advisor().scan(incidents=incidents)
    inc = next(it for it in rep.items if it.id == "inc1")
    assert inc.verdict == "MONITOR"
    assert inc.priority == "P3"


def test_power_failure_sev3_escalates_now_p0():
    incidents = [
        {
            "id": "inc-pwr",
            "point": (5, 5),
            "kind": "power_failure",
            "severity": 3,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=2)),
        }
    ]
    rep = make_advisor().scan(incidents=incidents)
    it = next(x for x in rep.items if x.id == "inc-pwr")
    assert it.verdict == "ESCALATE_NOW"
    assert it.priority == "P0"
    assert any(a.id == "CALL_OUT_ON_CALL_ENGINEER" for a in rep.playbook)


def test_stale_incident_promotes_to_p0_and_grades_f():
    incidents = [
        {
            "id": "inc-s1",
            "point": (0, 0),
            "kind": "sensor_offline",
            "severity": 3,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=100)),
        },
        {
            "id": "inc-s2",
            "point": (10, 10),
            "kind": "data_gap",
            "severity": 2,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=80)),
        },
    ]
    rep = make_advisor().scan(incidents=incidents)
    stale = [it for it in rep.items if it.verdict == "STALE_INCIDENT"]
    assert len(stale) == 2
    assert all(it.priority == "P0" for it in stale)
    assert "STALE_BACKLOG" in rep.insights
    assert rep.grade == "F"


def test_visit_weather_hold_when_point_inside_bbox():
    visits = [
        {
            "id": "v1",
            "point": (5.0, 5.0),
            "purpose": "calibrate",
            "owner": "alice",
            "scheduled_for": _iso(FIXED_NOW + timedelta(hours=2)),
        }
    ]
    weather = [
        {
            "id": "w1",
            "region_bbox": [0, 0, 10, 10],
            "kind": "storm",
            "severity": 4,
            "until": _iso(FIXED_NOW + timedelta(hours=6)),
        }
    ]
    rep = make_advisor().scan(pending_visits=visits, weather_blockers=weather)
    v = next(it for it in rep.items if it.id == "v1")
    assert v.verdict == "WEATHER_HOLD"
    assert any(a.id == "REROUTE_VISITS_AROUND_WEATHER" for a in rep.playbook)


def test_owner_unavailable_when_owner_not_in_crew():
    visits = [
        {
            "id": "v2",
            "point": (100, 100),
            "purpose": "repair",
            "owner": "carol",
            "scheduled_for": _iso(FIXED_NOW + timedelta(hours=3)),
        }
    ]
    rep = make_advisor().scan(pending_visits=visits, crew_available=["alice", "bob"])
    v = next(it for it in rep.items if it.id == "v2")
    assert v.verdict == "OWNER_UNAVAILABLE"
    assert any(a.id == "BACKFILL_OWNER_GAPS" for a in rep.playbook)


def test_overdue_visit_classified():
    visits = [
        {
            "id": "v3",
            "point": (200, 200),
            "purpose": "inspect",
            "owner": "alice",
            "scheduled_for": _iso(FIXED_NOW - timedelta(hours=3)),
        }
    ]
    rep = make_advisor().scan(pending_visits=visits)
    v = next(it for it in rep.items if it.id == "v3")
    assert v.verdict == "OVERDUE_VISIT"


def test_drift_investigate_vs_watch_thresholds():
    drift = [
        {"point_id": "d-hi", "point": (1, 1), "severity": 85, "label": "value"},
        {"point_id": "d-mid", "point": (50, 50), "severity": 50, "label": "value"},
        {"point_id": "d-low", "point": (60, 60), "severity": 20, "label": "value"},
    ]
    rep = make_advisor().scan(recent_drift=drift)
    by_id = {it.id: it for it in rep.items}
    assert by_id["d-hi"].verdict == "DRIFT_INVESTIGATE"
    assert by_id["d-mid"].verdict == "DRIFT_WATCH"
    assert "d-low" not in by_id  # below 40 -> filtered


def test_gap_backfill_threshold():
    gaps = [
        {"id": "g1", "point": (1, 1), "gap_score": 80, "label": "edge"},
        {"id": "g2", "point": (2, 2), "gap_score": 50, "label": "edge"},
        {"id": "g3", "point": (3, 3), "gap_score": 25, "label": "edge"},
    ]
    rep = make_advisor().scan(coverage_gaps=gaps)
    by_id = {it.id: it for it in rep.items}
    assert by_id["g1"].verdict == "GAP_BACKFILL"
    assert by_id["g2"].verdict == "GAP_NOTE"
    assert "g3" not in by_id


def test_drift_cluster_insight_when_three_close():
    drift = [
        {"point_id": f"d{i}", "point": (i * 0.5, 0.0), "severity": 80, "label": "v"}
        for i in range(4)
    ]
    rep = make_advisor().scan(recent_drift=drift)
    assert "DRIFT_CLUSTER" in rep.insights
    assert any(a.id == "AUDIT_DRIFT_CLUSTER" for a in rep.playbook)


def test_simulate_raises_score_monotonically():
    incidents = [
        {
            "id": f"i{i}",
            "point": (i, i),
            "kind": "sensor_offline",
            "severity": 3,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=2)),
        }
        for i in range(4)
    ]
    rep = make_advisor().scan(incidents=incidents)
    sim0 = vh.simulate(rep, apply_top=0)
    sim1 = vh.simulate(rep, apply_top=1)
    sim3 = vh.simulate(rep, apply_top=3)
    assert sim0.handoff_score == rep.handoff_score
    assert sim1.handoff_score >= sim0.handoff_score
    assert sim3.handoff_score >= sim1.handoff_score


def test_risk_appetite_monotonicity():
    incidents = [
        {
            "id": "i1",
            "point": (0, 0),
            "kind": "sensor_offline",
            "severity": 3,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=4)),
        },
        {
            "id": "i2",
            "point": (1, 1),
            "kind": "data_gap",
            "severity": 2,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=2)),
        },
    ]
    advisor = make_advisor()
    s_c = advisor.scan(incidents=incidents, risk_appetite="cautious").handoff_score
    s_b = advisor.scan(incidents=incidents, risk_appetite="balanced").handoff_score
    s_a = advisor.scan(incidents=incidents, risk_appetite="aggressive").handoff_score
    assert s_c <= s_b <= s_a


def test_json_round_trip_byte_stable():
    incidents = [
        {
            "id": "i1",
            "point": (3.0, 4.0),
            "kind": "calibration_drift",
            "severity": 2,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=5)),
        }
    ]
    rep1 = make_advisor().scan(incidents=incidents)
    rep2 = make_advisor().scan(incidents=incidents)
    j1 = vh.to_json(rep1)
    j2 = vh.to_json(rep2)
    assert j1 == j2
    parsed = json.loads(j1)
    assert parsed["grade"] == rep1.grade
    assert parsed["items"][0]["id"] == "i1"


def test_markdown_contains_all_sections():
    incidents = [
        {
            "id": "i1",
            "point": (0, 0),
            "kind": "data_gap",
            "severity": 2,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=2)),
        }
    ]
    rep = make_advisor().scan(incidents=incidents)
    md = vh.to_markdown(rep)
    for section in ("## Summary", "## Items", "## Playbook", "## Insights"):
        assert section in md


def test_no_input_mutation():
    incidents = [
        {
            "id": "i1",
            "point": (1, 1),
            "kind": "sensor_offline",
            "severity": 3,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=2)),
        }
    ]
    visits = [
        {
            "id": "v1",
            "point": (2, 2),
            "purpose": "repair",
            "owner": "alice",
            "scheduled_for": _iso(FIXED_NOW + timedelta(hours=4)),
        }
    ]
    inc_snapshot = copy.deepcopy(incidents)
    visit_snapshot = copy.deepcopy(visits)
    make_advisor().scan(incidents=incidents, pending_visits=visits)
    assert incidents == inc_snapshot
    assert visits == visit_snapshot


def test_high_load_shift_insight():
    incidents = [
        {
            "id": f"i{i}",
            "point": (i, i),
            "kind": "data_gap",
            "severity": 2,
            "opened_at": _iso(FIXED_NOW - timedelta(hours=2)),
        }
        for i in range(10)
    ]
    rep = make_advisor().scan(incidents=incidents)
    assert "HIGH_LOAD_SHIFT" in rep.insights
    assert any(a.id == "HOLD_SHIFT_HUDDLE" for a in rep.playbook)


def test_invalid_risk_appetite_raises():
    with pytest.raises(ValueError):
        make_advisor().scan(risk_appetite="reckless")
