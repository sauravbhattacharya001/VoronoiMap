"""Tests for vormap_drift."""
from __future__ import annotations

import json
import math

import pytest

from vormap_drift import (
    DriftReport,
    apply_plan,
    detect_drift,
    format_report,
)


def _grid(side: int = 4, span: float = 30.0):
    step = span / (side - 1)
    return [(i * step, j * step) for i in range(side) for j in range(side)]


def test_returns_drift_report_basic_shift_emerge_disappear():
    base = _grid(4)  # 16 points on 0..30 grid (step=10)
    cur = list(base)
    # nudge one point slightly (SHIFTED)
    cur[0] = (cur[0][0] + 4.0, cur[0][1] + 0.0)
    # delete one (DISAPPEARED)
    cur.pop(5)
    # add a totally new point far from any baseline (EMERGED)
    cur.append((100.0, 100.0))

    r = detect_drift(base, cur)
    assert isinstance(r, DriftReport)
    assert r.metrics["disappeared_count"] >= 1
    assert r.metrics["emerged_count"] >= 1
    # the shift is large enough vs match radius (median NN=10, radius=5, 0.3*5=1.5)
    assert r.metrics["shifted_count"] >= 1


def test_stable_when_current_equals_baseline():
    base = _grid(4)
    r = detect_drift(base, list(base))
    assert r.metrics["stable_count"] == len(base)
    assert r.metrics["disappeared_count"] == 0
    assert r.metrics["emerged_count"] == 0
    assert r.grade == "A"
    assert r.stability_score == 100.0


def test_all_disappeared_when_current_empty():
    base = _grid(3)
    r = detect_drift(base, [])
    assert r.metrics["disappeared_count"] == len(base)
    assert r.metrics["emerged_count"] == 0
    assert any(a["priority"] in ("P0", "P1") for a in r.playbook)


def test_all_emerged_when_baseline_empty():
    cur = _grid(3)
    r = detect_drift([], cur)
    assert r.metrics["emerged_count"] == len(cur)
    assert r.metrics["disappeared_count"] == 0


def test_value_drift_above_threshold_triggers_and_below_does_not():
    base = [(0.0, 0.0, 10.0), (1.0, 0.0, 10.0), (0.0, 1.0, 10.0), (1.0, 1.0, 10.0)]
    cur = [(0.0, 0.0, 13.0), (1.0, 0.0, 10.5), (0.0, 1.0, 10.5), (1.0, 1.0, 10.5)]
    r = detect_drift(base, cur, value_drift_threshold=0.25)
    # first point: 30% delta -> VALUE_DRIFT; others 5% -> STABLE
    drifts = [p for p in r.points if p.verdict == "VALUE_DRIFT"]
    assert len(drifts) == 1
    assert drifts[0].id == "b#0"


def test_shifted_classification_for_small_displacement():
    base = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
    cur = [(2.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
    # median NN of baseline = 10, match_radius = 5; displacement = 2 > 0.3*5=1.5
    r = detect_drift(base, cur)
    shifted = [p for p in r.points if p.verdict == "SHIFTED"]
    assert len(shifted) == 1
    assert shifted[0].id == "b#0"


def test_duplicate_emergence_when_two_new_points_cluster_far_from_baseline():
    base = _grid(4)  # NN=10, match_radius=5, dup_radius=2.5
    cur = list(base) + [(200.0, 200.0), (200.5, 200.5)]
    r = detect_drift(base, cur)
    dups = [p for p in r.points if p.verdict == "DUPLICATE_EMERGENCE"]
    assert len(dups) == 1


def test_risk_appetite_monotonicity_for_disappeared_point():
    base = _grid(4)
    cur = list(base)
    cur.pop(7)  # one disappeared
    rc = detect_drift(base, cur, risk_appetite="cautious")
    rb = detect_drift(base, cur, risk_appetite="balanced")
    ra = detect_drift(base, cur, risk_appetite="aggressive")
    sc = next(p.priority_score for p in rc.points if p.verdict == "DISAPPEARED")
    sb = next(p.priority_score for p in rb.points if p.verdict == "DISAPPEARED")
    sa = next(p.priority_score for p in ra.points if p.verdict == "DISAPPEARED")
    assert sc >= sb >= sa


def test_auto_match_radius_is_half_of_median_baseline_nn():
    # 4 collinear baseline points spaced 10 apart -> NN=10 for all
    base = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    r = detect_drift(base, list(base))
    assert math.isclose(r.match_radius, 5.0, rel_tol=1e-6)


def test_grade_a_when_mostly_stable():
    base = _grid(4)
    r = detect_drift(base, list(base))
    assert r.grade == "A"


def test_playbook_prioritized_p0_first_and_deduped():
    base = _grid(4)
    cur = list(base)
    cur.pop(0)
    cur.pop(0)
    cur.pop(0)
    r = detect_drift(base, cur)
    priorities = [a["priority"] for a in r.playbook]
    rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    nums = [rank[p] for p in priorities]
    assert nums == sorted(nums)
    ids = [a["id"] for a in r.playbook]
    assert len(ids) == len(set(ids))


def test_apply_plan_drops_duplicates_and_does_not_mutate():
    base = _grid(4)
    cur = list(base) + [(200.0, 200.0), (200.5, 200.5)]
    snapshot_base = list(base)
    snapshot_cur = list(cur)
    r = detect_drift(base, cur)
    cleaned = apply_plan(r, base, cur)
    assert len(cleaned) == len(cur) - 1  # one duplicate dropped
    assert base == snapshot_base
    assert cur == snapshot_cur


def test_json_renderer_is_byte_stable():
    base = _grid(4)
    cur = list(base)
    cur[0] = (cur[0][0] + 3.0, cur[0][1])

    def fixed_now():
        from datetime import datetime, timezone
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    r1 = detect_drift(base, cur, now=fixed_now)
    r2 = detect_drift(base, cur, now=fixed_now)
    s1 = format_report(r1, "json")
    s2 = format_report(r2, "json")
    assert s1 == s2
    # parse sanity
    parsed = json.loads(s1)
    assert "summary" in parsed and "playbook" in parsed


def test_markdown_renderer_contains_section_headers():
    base = _grid(3)
    cur = list(base)
    cur.pop(0)
    r = detect_drift(base, cur)
    md = format_report(r, "md")
    assert "## Overview" in md
    assert "## Counts" in md
    assert "## Playbook" in md


def test_text_headline_starts_with_drift_report():
    base = _grid(3)
    cur = list(base)
    r = detect_drift(base, cur)
    out = format_report(r, "text")
    assert r.summary.startswith("Drift report:")
    assert "Drift report:" in out


def test_invalid_risk_appetite_raises():
    with pytest.raises(ValueError):
        detect_drift([(0.0, 0.0)], [(0.0, 0.0)], risk_appetite="reckless")


def test_invalid_format_raises():
    r = detect_drift([(0.0, 0.0)], [(0.0, 0.0)])
    with pytest.raises(ValueError):
        format_report(r, "xml")
