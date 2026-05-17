"""Tests for vormap_redundancy."""

from __future__ import annotations

import json
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import vormap_redundancy as M  # noqa: E402


# ---------------------------------------------------------------------------
# Trivial / corner cases
# ---------------------------------------------------------------------------


def test_empty_input():
    r = M.analyse([])
    assert r.points_in == 0
    assert r.summary["points_in"] == 0
    assert r.summary["keep_count"] == 0
    assert r.verdicts == []
    assert r.grade == "A"
    assert r.playbook == []


def test_single_point():
    r = M.analyse([(0.0, 0.0)])
    assert r.points_in == 1
    assert len(r.verdicts) == 1
    v = r.verdicts[0]
    assert v.verdict == "KEEP"
    assert v.nearest_neighbor_index == -1
    assert math.isinf(v.nearest_neighbor_distance) or v.as_dict()["nearest_neighbor_distance"] is None


def test_two_distant_points():
    r = M.analyse([(0.0, 0.0), (100.0, 100.0)])
    # No merge, no oversampling
    assert r.summary["merge_count"] == 0
    assert r.summary["oversampled_count"] == 0
    assert r.grade == "A"


# ---------------------------------------------------------------------------
# Merge detection
# ---------------------------------------------------------------------------


def test_identical_points_merge_into_lower_index():
    pts = [(5.0, 5.0), (5.0, 5.0), (100.0, 100.0), (200.0, 200.0)]
    r = M.analyse(pts, merge_radius=0.01)
    merges = [v for v in r.verdicts if v.verdict == "REDUNDANT_MERGE"]
    assert len(merges) == 1
    assert merges[0].point_index == 1
    assert merges[0].merge_into == 0


def test_merge_pair_priority_p0_or_p1():
    pts = [(0.0, 0.0), (0.0001, 0.0001), (50.0, 50.0), (100.0, 100.0)]
    r = M.analyse(pts, merge_radius=0.001)
    merges = [v for v in r.verdicts if v.verdict == "REDUNDANT_MERGE"]
    assert merges, "expected a merge verdict"
    # Forced floor at ~70 = P1+, after risk_offset balanced
    assert merges[0].tier in ("P0", "P1")


def test_merge_radius_override_changes_results():
    pts = [(0.0, 0.0), (1.0, 0.0), (10.0, 10.0)]
    r_tight = M.analyse(pts, merge_radius=0.5)
    r_loose = M.analyse(pts, merge_radius=2.0)
    assert r_tight.summary["merge_count"] == 0
    assert r_loose.summary["merge_count"] == 1


# ---------------------------------------------------------------------------
# Oversampling / values
# ---------------------------------------------------------------------------


def _dense_cluster_plus_outliers():
    cluster = [(i * 0.2, j * 0.2) for i in range(5) for j in range(5)]  # 25 dense
    outliers = [(50.0, 50.0), (100.0, 100.0), (-50.0, -50.0), (-100.0, -100.0)]
    return cluster + outliers


def test_dense_cluster_triggers_oversampled():
    pts = _dense_cluster_plus_outliers()
    r = M.analyse(pts, merge_radius=0.001)
    assert r.summary["oversampled_count"] >= 1 or r.summary["relocate_count"] >= 1


def test_values_similar_triggers_retire_low_value():
    pts = _dense_cluster_plus_outliers()
    # values: all dense points read ~5.0, outliers different
    values = [5.0] * 25 + [99.0, -99.0, 50.0, -50.0]
    r = M.analyse(pts, values=values, value_epsilon=0.1, merge_radius=0.001)
    # Should produce at least one RETIRE_LOW_VALUE
    rlv = [v for v in r.verdicts if v.verdict == "RETIRE_LOW_VALUE"]
    assert rlv, "expected at least one RETIRE_LOW_VALUE in dense same-value cluster"


# ---------------------------------------------------------------------------
# Risk appetite monotonicity
# ---------------------------------------------------------------------------


def _retire_count(r):
    return sum(1 for v in r.verdicts
               if v.verdict in ("REDUNDANT_MERGE", "OVERSAMPLED", "RETIRE_LOW_VALUE"))


def test_risk_appetite_monotonicity():
    pts = _dense_cluster_plus_outliers()
    rc = _retire_count(M.analyse(pts, risk_appetite="cautious"))
    rb = _retire_count(M.analyse(pts, risk_appetite="balanced"))
    ra = _retire_count(M.analyse(pts, risk_appetite="aggressive"))
    # Aggressive should retire at least as many as balanced, which >= cautious.
    assert ra >= rb >= rc


# ---------------------------------------------------------------------------
# Grade
# ---------------------------------------------------------------------------


def test_grade_a_on_well_spread():
    pts = [(i * 10.0, j * 10.0) for i in range(4) for j in range(4)]
    r = M.analyse(pts)
    assert r.grade in ("A", "B")


def test_grade_f_on_heavy_duplicates():
    pts = [(0.0, 0.0)] * 6 + [(50.0, 50.0)] * 6
    r = M.analyse(pts, merge_radius=0.01)
    # 10 of 12 are duplicates -> ~83% -> F
    assert r.grade == "F"


# ---------------------------------------------------------------------------
# Cost savings & apply / simulate
# ---------------------------------------------------------------------------


def test_cost_savings_sum_matches():
    pts = [(0.0, 0.0), (0.0001, 0.0001), (0.0002, 0.0002),
           (50.0, 50.0), (100.0, 100.0)]
    costs = [10.0, 5.0, 7.0, 3.0, 4.0]
    r = M.analyse(pts, costs=costs, merge_radius=0.01)
    flagged = [v for v in r.verdicts
               if v.verdict in ("REDUNDANT_MERGE", "OVERSAMPLED", "RETIRE_LOW_VALUE")]
    expected = sum(costs[v.point_index] for v in flagged)
    assert math.isclose(r.summary["projected_cost_savings_total"], expected, rel_tol=1e-9, abs_tol=1e-9)


def test_apply_plan_reduces_count_and_keeps_keepers():
    pts = [(0.0, 0.0), (0.0001, 0.0001), (50.0, 50.0), (100.0, 100.0)]
    r = M.analyse(pts, merge_radius=0.01)
    cleaned = M.apply_plan(pts, r)
    assert len(cleaned) == len(pts) - r.summary["merge_count"] - r.summary["oversampled_count"] - r.summary["retire_low_value_count"]
    # Keeper (0,0) survives
    assert (0.0, 0.0) in cleaned


def test_simulate_reports_before_after():
    pts = [(0.0, 0.0), (0.0001, 0.0001), (50.0, 50.0), (100.0, 100.0)]
    r = M.analyse(pts, merge_radius=0.01, costs=[1.0, 2.0, 3.0, 4.0])
    sim = M.simulate(pts, r)
    assert sim["before_count"] == 4
    assert sim["after_count"] <= 4
    assert sim["cost_saved"] >= 0.0


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def test_format_json_byte_stable_and_sorted():
    pts = [(0.0, 0.0), (0.001, 0.001), (10.0, 10.0)]
    r1 = M.analyse(pts, merge_radius=0.01)
    r2 = M.analyse(pts, merge_radius=0.01)
    j1 = M.format_json(r1)
    j2 = M.format_json(r2)
    assert j1 == j2
    parsed = json.loads(j1)
    # Top-level keys sorted
    assert list(parsed.keys()) == sorted(parsed.keys())


def test_format_markdown_non_empty():
    pts = [(0.0, 0.0), (0.001, 0.001), (10.0, 10.0)]
    r = M.analyse(pts, merge_radius=0.01)
    md = M.format_markdown(r)
    assert "vormap_redundancy" in md
    assert "Summary" in md
    assert "|" in md  # tables present


def test_format_text_non_empty():
    r = M.analyse([(0.0, 0.0), (0.001, 0.001)])
    t = M.format_text(r)
    assert "vormap_redundancy" in t


# ---------------------------------------------------------------------------
# Playbook ordering / dedup
# ---------------------------------------------------------------------------


def test_playbook_p0_first_and_deduped():
    pts = _dense_cluster_plus_outliers()
    values = [5.0] * 25 + [99.0, -99.0, 50.0, -50.0]
    costs = [1.0] * len(pts)
    r = M.analyse(pts, values=values, value_epsilon=0.1, costs=costs, merge_radius=0.001)
    prio_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    ranks = [prio_rank[a["priority"]] for a in r.playbook]
    assert ranks == sorted(ranks), "playbook must be P0-first"
    ids = [a["id"] for a in r.playbook]
    assert len(ids) == len(set(ids)), "playbook must dedup by id"


# ---------------------------------------------------------------------------
# CLI / parser
# ---------------------------------------------------------------------------


def test_build_parser_help_exits_zero():
    parser = M.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_cli_smoke_via_main(tmp_path):
    csv_path = tmp_path / "pts.csv"
    csv_path.write_text("x,y,cost\n0,0,1\n0.001,0.001,1\n10,10,1\n20,20,1\n")
    out_path = tmp_path / "report.md"
    rc = M.main([str(csv_path), "--format", "md", "--costs-col", "cost",
                 "--merge-radius", "0.01", "--output", str(out_path)])
    assert rc == 0
    body = out_path.read_text(encoding="utf-8")
    assert "vormap_redundancy" in body
    assert "Summary" in body


def test_apply_writes_cleaned_csv(tmp_path):
    csv_path = tmp_path / "pts.csv"
    csv_path.write_text("x,y\n0,0\n0.001,0.001\n10,10\n20,20\n")
    cleaned_path = tmp_path / "cleaned.csv"
    rc = M.main([str(csv_path), "--format", "json", "--merge-radius", "0.01",
                 "--apply", str(cleaned_path),
                 "--output", str(tmp_path / "r.json")])
    assert rc == 0
    text = cleaned_path.read_text(encoding="utf-8")
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    # header + at most 3 rows (one merge dropped)
    assert lines[0].startswith("x")
    assert len(lines) - 1 < 4
