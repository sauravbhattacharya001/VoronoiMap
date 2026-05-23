"""Tests for vormap_failover."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest

import vormap_failover as vf


FIXED_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _fixed_now() -> datetime:
    return FIXED_NOW


def _dense_grid():
    pts = []
    for i in range(3):
        for j in range(3):
            pts.append(vf.FleetSensor(f"g{i}{j}", float(i), float(j)))
    return pts


# ---------------------------------------------------------------------------
# Empty / tiny fleet
# ---------------------------------------------------------------------------

def test_empty_fleet_grade_a():
    r = vf.analyze([], now_fn=_fixed_now)
    assert r.sensor_count == 0
    assert r.grade == "A"
    assert any(a["id"] == "BOOTSTRAP_FLEET" for a in r.playbook)
    assert "EMPTY_FLEET" in r.insights


def test_single_sensor_insufficient():
    r = vf.analyze([vf.FleetSensor("only", 0, 0)], now_fn=_fixed_now)
    assert r.sensor_count == 1
    assert r.scenarios[0].verdict == "INSUFFICIENT_DATA"
    assert "FLEET_TOO_SMALL" in r.insights


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def test_isolated_failure_detected():
    pts = _dense_grid() + [vf.FleetSensor("loner", 20.0, 20.0, criticality=5)]
    r = vf.analyze(pts, now_fn=_fixed_now)
    loner = next(sc for sc in r.scenarios if sc.sid == "loner")
    assert loner.verdict == "ISOLATED_FAILURE"
    assert loner.priority == "P0"
    assert "BACKUP_VERY_DISTANT" in loner.reasons


def test_graceful_failover_in_dense_grid():
    r = vf.analyze(_dense_grid(), now_fn=_fixed_now)
    verdicts = {sc.sid: sc.verdict for sc in r.scenarios}
    # Most should be REDUNDANT_OUTAGE; none isolated.
    assert "ISOLATED_FAILURE" not in verdicts.values()


def test_critical_asset_p0_forces_F():
    pts = [
        vf.FleetSensor("a", 0, 0),
        vf.FleetSensor("b", 1, 0),
        vf.FleetSensor("crit", 100, 100, criticality=5),
    ]
    r = vf.analyze(pts, now_fn=_fixed_now)
    assert r.grade == "F"
    assert "CRITICAL_ASSET_SPOF:1" in r.insights


def test_has_backup_link_lowers_score():
    base = [
        vf.FleetSensor("a", 0, 0, criticality=4),
        vf.FleetSensor("b", 0.5, 0, criticality=3),
        vf.FleetSensor("c", 0, 0.5, criticality=3),
    ]
    with_link = [
        vf.FleetSensor("a", 0, 0, criticality=4, has_backup_link=True),
        vf.FleetSensor("b", 0.5, 0, criticality=3),
        vf.FleetSensor("c", 0, 0.5, criticality=3),
    ]
    r1 = vf.analyze(base, now_fn=_fixed_now)
    r2 = vf.analyze(with_link, now_fn=_fixed_now)
    s1 = next(sc for sc in r1.scenarios if sc.sid == "a")
    s2 = next(sc for sc in r2.scenarios if sc.sid == "a")
    assert s2.priority_score < s1.priority_score


# ---------------------------------------------------------------------------
# Risk appetite
# ---------------------------------------------------------------------------

def test_cautious_increases_scores():
    pts = _dense_grid() + [vf.FleetSensor("far", 5, 5, criticality=3)]
    r_balanced = vf.analyze(pts, risk_appetite="balanced", now_fn=_fixed_now)
    r_cautious = vf.analyze(pts, risk_appetite="cautious", now_fn=_fixed_now)
    s_b = next(sc for sc in r_balanced.scenarios if sc.sid == "far")
    s_c = next(sc for sc in r_cautious.scenarios if sc.sid == "far")
    assert s_c.priority_score >= s_b.priority_score


def test_aggressive_lowers_scores():
    pts = _dense_grid() + [vf.FleetSensor("far", 4, 4, criticality=3)]
    r_balanced = vf.analyze(pts, risk_appetite="balanced", now_fn=_fixed_now)
    r_aggr = vf.analyze(pts, risk_appetite="aggressive", now_fn=_fixed_now)
    s_b = next(sc for sc in r_balanced.scenarios if sc.sid == "far")
    s_a = next(sc for sc in r_aggr.scenarios if sc.sid == "far")
    assert s_a.priority_score <= s_b.priority_score


def test_invalid_risk_appetite_raises():
    with pytest.raises(ValueError):
        vf.analyze([], risk_appetite="reckless")


# ---------------------------------------------------------------------------
# Backups, neighbour load
# ---------------------------------------------------------------------------

def test_k_backups_respected():
    pts = _dense_grid()
    r = vf.analyze(pts, k_backups=2, now_fn=_fixed_now)
    for sc in r.scenarios:
        assert len(sc.backups) <= 2


def test_overloaded_backup_insight():
    # Star: 5 sensors close to a center; remove center -> primary backup is center for all.
    pts = [vf.FleetSensor("hub", 0, 0)]
    for i in range(5):
        # Tight spokes very close to hub, far from each other.
        pts.append(vf.FleetSensor(f"spoke{i}", 0.1 * math.cos(i * 1.2566), 0.1 * math.sin(i * 1.2566)))
    # Plus a few outliers so the spokes are clearly closer to hub than to each other.
    pts.append(vf.FleetSensor("x1", 10, 10))
    pts.append(vf.FleetSensor("x2", -10, -10))
    r = vf.analyze(pts, now_fn=_fixed_now)
    assert any(row["primary_for_count"] >= 4 for row in r.neighbour_load)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def test_text_render_contains_headline():
    r = vf.analyze(_dense_grid(), now_fn=_fixed_now)
    out = vf.to_text(r)
    assert "VERDICT:" in out
    assert "Playbook:" in out
    assert "Insights:" in out


def test_markdown_render_has_all_sections():
    r = vf.analyze(_dense_grid(), now_fn=_fixed_now)
    md = vf.to_markdown(r)
    for hdr in ("## Summary", "## Scenarios", "## Neighbour load", "## Playbook", "## Insights"):
        assert hdr in md


def test_json_byte_stable_and_roundtrips():
    r = vf.analyze(_dense_grid(), now_fn=_fixed_now)
    j1 = vf.to_json(r)
    j2 = vf.to_json(r)
    assert j1 == j2
    payload = json.loads(j1)
    assert payload["sensor_count"] == 9
    assert "scenarios" in payload


# ---------------------------------------------------------------------------
# Determinism / immutability
# ---------------------------------------------------------------------------

def test_determinism_identical_inputs():
    pts = _dense_grid()
    r1 = vf.analyze(pts, now_fn=_fixed_now)
    r2 = vf.analyze(copy.deepcopy(pts), now_fn=_fixed_now)
    assert vf.to_json(r1) == vf.to_json(r2)


def test_inputs_not_mutated():
    pts = _dense_grid()
    snap = copy.deepcopy(pts)
    vf.analyze(pts, now_fn=_fixed_now)
    assert pts == snap


def test_duplicate_sid_raises():
    pts = [vf.FleetSensor("a", 0, 0), vf.FleetSensor("a", 1, 1)]
    with pytest.raises(ValueError):
        vf.analyze(pts, now_fn=_fixed_now)


# ---------------------------------------------------------------------------
# from_record + dict input
# ---------------------------------------------------------------------------

def test_dict_records_accepted():
    recs = [
        {"sid": "a", "x": 0, "y": 0, "criticality": 4},
        {"sid": "b", "x": 1, "y": 0},
        {"sid": "c", "x": 0, "y": 1, "has_backup_link": "true"},
    ]
    r = vf.analyze(recs, now_fn=_fixed_now)
    assert r.sensor_count == 3
    c = next(s for s in r.scenarios if s.sid == "c")
    assert isinstance(c.priority_score, float)


def test_from_record_requires_sid():
    with pytest.raises(ValueError):
        vf.FleetSensor.from_record({"x": 0, "y": 0})


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------

def test_simulate_reduces_risk():
    pts = _dense_grid() + [vf.FleetSensor("loner", 20, 20, criticality=5)]
    r = vf.analyze(pts, now_fn=_fixed_now)
    sim = vf.simulate(r, apply_top_n=2)
    assert sim["after_portfolio_risk"] <= sim["before_portfolio_risk"]
    assert isinstance(sim["applied"], list)


def test_simulate_does_not_mutate_report():
    pts = _dense_grid()
    r = vf.analyze(pts, now_fn=_fixed_now)
    before = vf.to_json(r)
    vf.simulate(r, apply_top_n=3)
    after = vf.to_json(r)
    assert before == after


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

def test_cli_demo_text(capsys):
    rc = vf.main(["--demo", "--format", "text"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "VERDICT:" in out


def test_cli_demo_json(capsys):
    rc = vf.main(["--demo", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["sensor_count"] == 7


# math import added at runtime via test
import math  # noqa: E402
