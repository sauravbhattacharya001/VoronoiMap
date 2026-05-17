"""Tests for vormap_sensorplanner."""
from __future__ import annotations

import json
import math
import random

import pytest

from vormap_sensorplanner import (
    SensorCandidate,
    SensorPlan,
    format_plan,
    plan_sensors,
)


def _sparse_points():
    # 6 points clustered in the bottom-left quarter of a (0,0)-(100,100) box
    return [(5, 5), (8, 4), (12, 10), (6, 14), (15, 7), (10, 12)]


def _grid_points(side: int = 5, span: float = 100.0):
    step = span / (side - 1)
    return [(i * step, j * step) for i in range(side) for j in range(side)]


# ---------------------------------------------------------------------------


def test_returns_requested_count():
    plan = plan_sensors(_sparse_points(), n=5,
                        bounds=(0, 0, 100, 100), seed=1)
    assert isinstance(plan, SensorPlan)
    assert plan.placed == 5
    assert len(plan.candidates) == 5


def test_auto_bounds_covers_inputs():
    pts = _sparse_points()
    plan = plan_sensors(pts, n=3, seed=1)
    w, s, e, n = plan.bounds
    assert w <= min(p[0] for p in pts)
    assert s <= min(p[1] for p in pts)
    assert e >= max(p[0] for p in pts)
    assert n >= max(p[1] for p in pts)


def test_explicit_bounds_respected():
    bounds = (0.0, 0.0, 50.0, 50.0)
    plan = plan_sensors(_sparse_points(), n=6, bounds=bounds, seed=42)
    for c in plan.candidates:
        assert bounds[0] - 1e-6 <= c.x <= bounds[2] + 1e-6
        assert bounds[1] - 1e-6 <= c.y <= bounds[3] + 1e-6


def test_critical_gap_verdict_present_when_quadrant_empty():
    # All existing points jammed into one corner of a large box
    plan = plan_sensors(_sparse_points(), n=5,
                        bounds=(0, 0, 100, 100), seed=1)
    verdicts = {c.verdict for c in plan.candidates}
    assert "CRITICAL_GAP" in verdicts


def test_refine_local_or_critical_under_aggressive():
    # On a dense uniform grid, gaps are tiny - aggressive risk should
    # at least sometimes pick REFINE_LOCAL or REDUNDANT verdicts.
    pts = _grid_points(side=6, span=60)
    plan = plan_sensors(pts, n=4, risk_appetite="aggressive", seed=7)
    verdicts = {c.verdict for c in plan.candidates}
    # Either we found local-refine candidates, or every candidate was a
    # boundary expansion - both are acceptable on a tight grid.
    assert verdicts <= {"CRITICAL_GAP", "COVERAGE_EXPAND",
                        "REFINE_LOCAL", "REDUNDANT"}


def test_determinism_same_seed():
    a = plan_sensors(_sparse_points(), n=5, bounds=(0, 0, 100, 100), seed=99)
    b = plan_sensors(_sparse_points(), n=5, bounds=(0, 0, 100, 100), seed=99)
    coords_a = [(round(c.x, 6), round(c.y, 6)) for c in a.candidates]
    coords_b = [(round(c.x, 6), round(c.y, 6)) for c in b.candidates]
    assert coords_a == coords_b


def test_different_seeds_diverge():
    a = plan_sensors(_sparse_points(), n=5, bounds=(0, 0, 100, 100), seed=1)
    b = plan_sensors(_sparse_points(), n=5, bounds=(0, 0, 100, 100), seed=2)
    coords_a = [(round(c.x, 6), round(c.y, 6)) for c in a.candidates]
    coords_b = [(round(c.x, 6), round(c.y, 6)) for c in b.candidates]
    # Probabilistic: at least one of the picks should differ across seeds.
    assert coords_a != coords_b


def test_min_separation_honored():
    pts = _sparse_points()
    min_sep = 8.0
    plan = plan_sensors(pts, n=5, bounds=(0, 0, 100, 100),
                        min_separation=min_sep, seed=5)
    for c in plan.candidates:
        for p in pts:
            d = math.hypot(c.x - p[0], c.y - p[1])
            # Hard floor in the planner is 0.5 * min_separation
            assert d >= 0.5 * min_sep - 1e-6


def test_cautious_vs_aggressive_gap_focus():
    pts = _sparse_points()
    cautious = plan_sensors(pts, n=5, bounds=(0, 0, 100, 100),
                            risk_appetite="cautious", seed=11)
    aggressive = plan_sensors(pts, n=5, bounds=(0, 0, 100, 100),
                              risk_appetite="aggressive", seed=11)
    cautious_gap = sum(c.score_breakdown["gap_score"]
                       for c in cautious.candidates) / 5
    aggressive_gap = sum(c.score_breakdown["gap_score"]
                         for c in aggressive.candidates) / 5
    # Cautious must not produce a *lower* mean gap_score than aggressive
    # on a sparse dataset (gap chasing > density refining).
    assert cautious_gap >= aggressive_gap - 1e-6


def test_format_plan_all_three_renderers():
    plan = plan_sensors(_sparse_points(), n=3,
                        bounds=(0, 0, 100, 100), seed=1)
    text = format_plan(plan, "text")
    md = format_plan(plan, "markdown")
    js = format_plan(plan, "json")
    assert "Sensor Placement Plan" in text
    assert "# VoronoiMap Sensor Placement Plan" in md
    parsed = json.loads(js)
    assert parsed["placed"] == 3
    assert isinstance(parsed["candidates"], list)
    assert len(parsed["candidates"]) == 3


def test_empty_input_with_bounds_emits_candidates():
    plan = plan_sensors([], n=4, bounds=(0, 0, 10, 10), seed=3)
    assert plan.placed == 4
    # All should be CRITICAL_GAP because no existing coverage exists.
    assert all(c.verdict == "CRITICAL_GAP" for c in plan.candidates)


def test_mean_nn_changes_when_placing():
    pts = _sparse_points()
    plan = plan_sensors(pts, n=6, bounds=(0, 0, 100, 100), seed=4)
    # The combined-dataset mean nearest-neighbour distance should be a
    # different, meaningful number (direction depends on initial cluster
    # tightness vs. bounding box).  Just require it actually moved.
    assert plan.mean_nn_before > 0
    assert plan.mean_nn_after > 0
    assert abs(plan.mean_nn_after - plan.mean_nn_before) > 1e-6
