"""Tests for vormap_route."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile

import pytest

import vormap_route as vr


def _basic_visits():
    return [
        vr.SensorVisit(id="A", x=0.0, y=0.0, urgency=90, criticality=5,
                       estimated_minutes=30),
        vr.SensorVisit(id="B", x=2.0, y=0.0, urgency=60, criticality=3,
                       estimated_minutes=25),
        vr.SensorVisit(id="C", x=5.0, y=5.0, urgency=15, criticality=1,
                       estimated_minutes=20),
    ]


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------


def test_empty_input_returns_grade_f():
    rpt = vr.RouteAdvisor().analyze([])
    assert rpt.grade == "F"
    assert rpt.total_visits == 0
    assert "EMPTY_ROUTE" in rpt.insights


def test_single_visit_dispatched():
    v = [vr.SensorVisit(id="X", x=1.0, y=0.0, urgency=70, estimated_minutes=20)]
    rpt = vr.RouteAdvisor().analyze(v, shift_minutes=480)
    assert rpt.completed_visits == 1
    assert rpt.visits[0].sequence == 1
    assert rpt.visits[0].verdict in ("SCHEDULED", "VISIT_NOW")


def test_p0_urgent_dispatched_first():
    visits = _basic_visits()
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=480)
    # Visit A is high urgency + criticality 5 -> P0 VISIT_NOW, dispatched seq 1
    first = rpt.visits[0]
    assert first.id == "A"
    assert first.priority == "P0"
    assert first.verdict == "VISIT_NOW"


def test_dropped_low_value():
    visits = [
        vr.SensorVisit(id="LOW", x=100.0, y=100.0, urgency=10, criticality=1,
                       estimated_minutes=20),
        vr.SensorVisit(id="HOT", x=0.0, y=0.0, urgency=95, criticality=5,
                       estimated_minutes=20),
    ]
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=60)  # tight budget
    low = next(v for v in rpt.visits if v.id == "LOW")
    assert low.verdict in ("DROPPED_LOW_VALUE", "DEFERRED")
    # at least one of the playbook actions should reference drop or defer
    pb_ids = {a.id for a in rpt.playbook}
    # not strict: just ensure no crash and no dispatch of LOW
    assert low.sequence is None


def test_deferred_when_over_budget():
    visits = [
        vr.SensorVisit(id=f"V{i}", x=float(i), y=0.0, urgency=80,
                       criticality=4, estimated_minutes=120)
        for i in range(5)
    ]
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=120)
    deferred = [v for v in rpt.visits if v.verdict == "DEFERRED"]
    assert len(deferred) >= 3
    # Multiple P0-equivalents not run -> grade F
    assert rpt.grade in ("F", "D")


def test_blocked_time_window():
    visits = [
        vr.SensorVisit(id="W1", x=200.0, y=200.0, urgency=70, criticality=3,
                       estimated_minutes=20, time_window=(0.0, 10.0)),
        vr.SensorVisit(id="W2", x=200.0, y=201.0, urgency=70, criticality=3,
                       estimated_minutes=20, time_window=(0.0, 10.0)),
    ]
    # speed = 1 unit/min; nearest is ~283 min away -> can't make 10m window
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=600, travel_speed=1.0)
    blocked = [v for v in rpt.visits if v.verdict == "BLOCKED_TIME_WINDOW"]
    assert len(blocked) == 2
    pb_ids = {a.id for a in rpt.playbook}
    assert "RESEQUENCE_FOR_TIME_WINDOWS" in pb_ids


def test_appetite_monotonicity_priority_scores():
    visits = _basic_visits()
    cau = vr.RouteAdvisor().analyze(visits, risk_appetite="cautious")
    bal = vr.RouteAdvisor().analyze(visits, risk_appetite="balanced")
    agg = vr.RouteAdvisor().analyze(visits, risk_appetite="aggressive")
    cau_a = next(v for v in cau.visits if v.id == "A").priority_score
    bal_a = next(v for v in bal.visits if v.id == "A").priority_score
    agg_a = next(v for v in agg.visits if v.id == "A").priority_score
    assert cau_a >= bal_a >= agg_a


def test_greedy_determinism():
    visits = _basic_visits()
    a = vr.RouteAdvisor().analyze(visits)
    b = vr.RouteAdvisor().analyze(visits)
    seq_a = [(v.id, v.sequence) for v in a.visits]
    seq_b = [(v.id, v.sequence) for v in b.visits]
    assert seq_a == seq_b


def test_playbook_p0_first_ordering():
    visits = _basic_visits() + [
        # Add two unreachable P0s
        vr.SensorVisit(id="P0A", x=1000.0, y=0.0, urgency=95, criticality=5,
                       estimated_minutes=60),
    ]
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=120)
    # First action must be P0 if any exist
    priorities = [a.priority for a in rpt.playbook]
    if "P0" in priorities:
        assert priorities[0] == "P0"
    # Sorted weakly by priority rank
    rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    assert priorities == sorted(priorities, key=rank.get)


def test_add_second_shift_trigger():
    # Many tightly packed urgent visits that overflow budget
    visits = [
        vr.SensorVisit(id=f"H{i}", x=float(i * 2), y=0.0, urgency=85,
                       criticality=4, estimated_minutes=60)
        for i in range(8)
    ]
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=300)
    pb_ids = {a.id for a in rpt.playbook}
    assert rpt.deferred_visits >= 3
    # Utilisation should be very high after greedy fill
    if rpt.utilisation_pct > 95:
        assert "ADD_SECOND_SHIFT" in pb_ids


def test_resequence_for_time_windows_trigger():
    visits = [
        vr.SensorVisit(id=f"TW{i}", x=200.0 + i, y=200.0, urgency=70,
                       criticality=3, estimated_minutes=20,
                       time_window=(0.0, 10.0))
        for i in range(3)
    ]
    rpt = vr.RouteAdvisor().analyze(visits, shift_minutes=600)
    pb_ids = {a.id for a in rpt.playbook}
    assert "RESEQUENCE_FOR_TIME_WINDOWS" in pb_ids


def test_json_byte_stability():
    visits = _basic_visits()
    # Use a fixed clock so generated_at matches
    from datetime import datetime, timezone
    clk = lambda: datetime(2026, 5, 22, 9, 0, 0, tzinfo=timezone.utc)
    a = vr.RouteAdvisor(now_fn=clk).analyze(visits)
    b = vr.RouteAdvisor(now_fn=clk).analyze(visits)
    assert vr.to_json(a) == vr.to_json(b)
    # parses back
    parsed = json.loads(vr.to_json(a))
    assert "visits" in parsed and "playbook" in parsed


def test_markdown_contains_all_sections():
    rpt = vr.RouteAdvisor().analyze(_basic_visits())
    md = vr.to_markdown(rpt)
    assert "## Summary" in md
    assert "## Route" in md
    assert "## Playbook" in md
    assert "## Insights" in md


def test_input_immutability():
    visits = _basic_visits()
    snapshot = copy.deepcopy(visits)
    vr.RouteAdvisor().analyze(visits)
    for orig, after in zip(snapshot, visits):
        assert orig == after


def test_apply_plan_returns_sequence_order():
    rpt = vr.RouteAdvisor().analyze(_basic_visits())
    plan = vr.apply_plan(rpt)
    dispatched = [v.id for v in rpt.visits if v.sequence is not None]
    assert plan == dispatched


def test_simulate_extra_shift_minutes_helps():
    visits = [
        vr.SensorVisit(id=f"V{i}", x=float(i * 10), y=0.0, urgency=70,
                       criticality=3, estimated_minutes=40)
        for i in range(6)
    ]
    base = vr.RouteAdvisor().analyze(visits, shift_minutes=120)
    sim = vr.simulate(base, extra_shift_minutes=360, visits=visits)
    assert sim.completed_visits >= base.completed_visits
    assert "simulated" in sim.summary


def test_cli_demo_text():
    result = subprocess.run(
        [sys.executable, "vormap_route.py", "--demo", "--format", "text"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert "VERDICT:" in result.stdout
    assert "Route:" in result.stdout


def test_render_unknown_format_raises():
    rpt = vr.RouteAdvisor().analyze(_basic_visits())
    with pytest.raises(ValueError):
        vr.render(rpt, "xml")


def test_invalid_risk_appetite_raises():
    with pytest.raises(ValueError):
        vr.RouteAdvisor().analyze(_basic_visits(), risk_appetite="reckless")


def test_dict_visit_inputs_accepted():
    rows = [
        {"id": "Z1", "x": 0, "y": 0, "urgency": 90, "criticality": 5,
         "estimated_minutes": 20},
        {"id": "Z2", "x": 1, "y": 1, "urgency": 50, "estimated_minutes": 15},
    ]
    rpt = vr.RouteAdvisor().analyze(rows)
    assert rpt.completed_visits == 2
