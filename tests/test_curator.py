"""Tests for ``vormap_curator`` - agentic spatial dataset curator.

Covers:
* Verdict assignment (KEEP / REVIEW / MERGE / DROP_DUPLICATE /
  DROP_INVALID / DROP_OUT_OF_BOUNDS).
* Priority ordering of verdicts.
* Aggregate health score / grade.
* Playbook tiers (P0/P1/P2/P3).
* Near-duplicate clustering selects lowest-index representative.
* Bounds + boundary-zone behaviour.
* Isolation detection.
* Low-precision detection (CSV-style rounded coords).
* ``apply_plan`` + before/after spacing stats.
* All three renderers (text / markdown / json) produce non-empty,
  well-formed output.
* CLI entry point round-trips through a temp CSV and writes
  ``--apply`` output.
* Edge cases: empty input, malformed rows, single point, all dropped.
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import vormap_curator as vc
from vormap_curator import (
    CurationReport,
    PlaybookStep,
    PointVerdict,
    Verdict,
    apply_plan,
    curate,
    format_json,
    format_markdown,
    format_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _by_index(report: CurationReport) -> dict:
    return {v.index: v for v in report.verdicts}


# ---------------------------------------------------------------------------
# Basic verdicts
# ---------------------------------------------------------------------------

def test_keep_simple_clean_points():
    r = curate([(0.5, 0.5), (10.0, 10.0), (20.0, 20.0)])
    by_idx = _by_index(r)
    assert by_idx[0].verdict == Verdict.KEEP
    assert by_idx[1].verdict == Verdict.KEEP
    assert by_idx[2].verdict == Verdict.KEEP
    assert r.n_input == 3
    assert r.n_kept == 3
    assert r.counts[Verdict.KEEP] == 3
    assert r.score == pytest.approx(100.0)
    assert r.grade == "A"


def test_drop_invalid_nan_and_inf():
    r = curate([(float("nan"), 0.0), (0.0, float("inf")), (1.0, 1.0)])
    by_idx = _by_index(r)
    assert by_idx[0].verdict == Verdict.DROP_INVALID
    assert by_idx[1].verdict == Verdict.DROP_INVALID
    assert by_idx[2].verdict == Verdict.KEEP
    # Invalid points have the highest priority.
    assert by_idx[0].priority >= by_idx[2].priority


def test_drop_invalid_malformed_rows():
    # A scalar (non-sequence) and a single-element tuple are both malformed.
    r = curate([42, (1.0,), (5.0, 5.0)])
    by_idx = _by_index(r)
    assert by_idx[0].verdict == Verdict.DROP_INVALID
    assert by_idx[1].verdict == Verdict.DROP_INVALID
    assert "malformed_row" in by_idx[0].reasons
    assert by_idx[2].verdict == Verdict.KEEP


def test_drop_exact_duplicates_keeps_first():
    r = curate([(3.0, 3.0), (3.0, 3.0), (3.0, 3.0), (9.0, 9.0)])
    by_idx = _by_index(r)
    assert by_idx[0].verdict == Verdict.KEEP
    assert by_idx[1].verdict == Verdict.DROP_DUPLICATE
    assert by_idx[2].verdict == Verdict.DROP_DUPLICATE
    assert by_idx[1].merge_into == 0
    assert by_idx[2].merge_into == 0
    assert r.counts[Verdict.DROP_DUPLICATE] == 2


def test_drop_out_of_bounds():
    r = curate(
        [(50.0, 50.0), (-1.0, 50.0), (50.0, 200.0)],
        bounds=(0.0, 100.0, 0.0, 100.0),  # south,north,west,east
    )
    by_idx = _by_index(r)
    assert by_idx[0].verdict in (Verdict.KEEP, Verdict.REVIEW)
    assert by_idx[1].verdict == Verdict.DROP_OUT_OF_BOUNDS
    assert by_idx[2].verdict == Verdict.DROP_OUT_OF_BOUNDS


def test_merge_near_duplicates_picks_lowest_index_rep():
    # Three points within tolerance of each other; index 0 should win.
    pts = [(0.0, 0.0), (1e-9, 0.0), (0.0, 1e-9), (50.0, 50.0)]
    r = curate(pts, near_duplicate_tol=1e-6)
    by_idx = _by_index(r)
    assert by_idx[0].verdict in (Verdict.KEEP, Verdict.REVIEW)
    assert by_idx[1].verdict == Verdict.MERGE
    assert by_idx[2].verdict == Verdict.MERGE
    assert by_idx[1].merge_into == 0
    assert by_idx[2].merge_into == 0
    assert by_idx[3].verdict in (Verdict.KEEP, Verdict.REVIEW)


def test_merge_disabled_when_tol_nonpositive():
    pts = [(0.0, 0.0), (1e-12, 0.0), (1.0, 1.0)]
    r = curate(pts, near_duplicate_tol=0.0)
    by_idx = _by_index(r)
    # No merges should be issued when tolerance is non-positive.
    assert by_idx[1].verdict != Verdict.MERGE
    assert r.counts[Verdict.MERGE] == 0


# ---------------------------------------------------------------------------
# REVIEW reasons
# ---------------------------------------------------------------------------

def test_review_near_boundary():
    # A point sitting close to the east edge should land in the boundary zone.
    pts = [(50.0, 50.0), (99.5, 50.0)]
    r = curate(pts, bounds=(0.0, 100.0, 0.0, 100.0), boundary_pad_frac=0.05)
    by_idx = _by_index(r)
    assert by_idx[1].verdict == Verdict.REVIEW
    assert any("near_boundary" in reason for reason in by_idx[1].reasons)


def test_review_isolation_outlier():
    # A cluster plus one far-out point that should trip isolation.
    pts = [(0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5), (1000.0, 1000.0)]
    r = curate(pts, isolation_k=2.0)
    by_idx = _by_index(r)
    assert by_idx[4].verdict == Verdict.REVIEW
    assert any("isolated" in reason for reason in by_idx[4].reasons)


def test_review_low_precision():
    pts = [(1.0, 2.0), (3.0, 4.0)]  # zero digits past decimal
    r = curate(pts, low_precision_max_digits=0)
    by_idx = _by_index(r)
    assert by_idx[0].verdict == Verdict.REVIEW
    assert any("low_precision" in reason for reason in by_idx[0].reasons)


def test_low_precision_disabled_by_default():
    pts = [(1.0, 2.0), (3.0, 4.0)]
    r = curate(pts)  # low_precision_max_digits=-1 (disabled)
    by_idx = _by_index(r)
    for v in by_idx.values():
        assert not any("low_precision" in reason for reason in v.reasons)


# ---------------------------------------------------------------------------
# Aggregate, ordering, and playbook
# ---------------------------------------------------------------------------

def test_verdicts_sorted_by_priority_desc():
    pts = [
        (50.0, 50.0),                # KEEP
        (50.0, 50.0),                # DROP_DUPLICATE
        (float("nan"), 1.0),         # DROP_INVALID
        (-10.0, 50.0),               # DROP_OUT_OF_BOUNDS
    ]
    r = curate(pts, bounds=(0.0, 100.0, 0.0, 100.0))
    priorities = [v.priority for v in r.verdicts]
    assert priorities == sorted(priorities, reverse=True)
    # First verdict should be the highest-priority DROP_INVALID.
    assert r.verdicts[0].verdict == Verdict.DROP_INVALID


def test_grade_scale_a_to_f():
    # All-clean -> A
    a = curate([(i * 1.0, i * 1.0) for i in range(10)])
    assert a.grade == "A"
    # All-invalid -> F (score 0)
    f = curate([(float("nan"), 1.0)] * 5)
    assert f.grade == "F"
    assert f.score == pytest.approx(0.0)


def test_playbook_contains_expected_tiers():
    pts = [
        (50.0, 50.0),                # KEEP
        (50.0, 50.0),                # DROP_DUPLICATE
        (float("nan"), 0.0),         # DROP_INVALID
        (200.0, 50.0),               # DROP_OUT_OF_BOUNDS
        (50.0, 50.0 + 1e-9),         # near-dup of #0 -> MERGE... but #0 was 50,50 and #4 is 50,50+1e-9
    ]
    # Drop the duplicate first so MERGE can fire on the third copy.
    r = curate(pts, bounds=(0.0, 100.0, 0.0, 100.0), near_duplicate_tol=1e-6)
    tiers = {step.tier for step in r.playbook}
    actions = {step.action for step in r.playbook}
    assert "P0" in tiers
    assert "drop_invalid" in actions
    assert "drop_out_of_bounds" in actions
    assert "drop_exact_duplicates" in actions


def test_playbook_all_clear_when_perfect():
    r = curate([(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)])
    assert len(r.playbook) == 1
    assert r.playbook[0].action == "all_clear"
    assert r.playbook[0].tier == "P3"


# ---------------------------------------------------------------------------
# Before/after stats and apply_plan
# ---------------------------------------------------------------------------

def test_apply_plan_drops_everything_but_keep_and_review():
    pts = [
        (0.0, 0.0),                  # KEEP
        (0.0, 0.0),                  # DROP_DUPLICATE
        (float("nan"), 1.0),         # DROP_INVALID
        (50.0, 50.0),                # KEEP
    ]
    r = curate(pts)
    cleaned = apply_plan(pts, r)
    assert (0.0, 0.0) in cleaned
    assert (50.0, 50.0) in cleaned
    assert len(cleaned) == 2


def test_before_after_stats_present_by_default():
    r = curate([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)])
    assert r.before_stats is not None
    assert r.after_stats is not None
    assert r.before_stats["count"] >= r.after_stats["count"] or \
        r.before_stats["count"] == r.after_stats["count"]


def test_before_after_stats_skipped_when_disabled():
    r = curate([(0.0, 0.0), (1.0, 1.0)], compute_after_stats=False)
    assert r.before_stats is None
    assert r.after_stats is None


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def test_format_text_smoke():
    r = curate([(0.0, 0.0), (1.0, 1.0), (float("nan"), 0.0)])
    out = format_text(r)
    assert "VoronoiMap Curator Report" in out
    assert "Grade" in out
    assert "Playbook:" in out


def test_format_markdown_smoke():
    r = curate([(0.0, 0.0), (1.0, 1.0), (float("nan"), 0.0)])
    out = format_markdown(r)
    assert out.startswith("# VoronoiMap Curator Report")
    assert "| Verdict | Count |" in out


def test_format_json_roundtrip():
    r = curate([(0.0, 0.0), (1.0, 1.0), (float("nan"), 0.0)])
    blob = format_json(r)
    parsed = json.loads(blob)
    assert parsed["n_input"] == 3
    assert parsed["grade"] == r.grade
    assert isinstance(parsed["verdicts"], list)
    assert all("verdict" in v and "priority" in v for v in parsed["verdicts"])


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_input():
    r = curate([])
    assert r.n_input == 0
    assert r.n_kept == 0
    assert r.score == pytest.approx(0.0)
    assert r.verdicts == []
    # Playbook should not be empty - it falls through to "all_clear".
    assert r.playbook
    assert r.playbook[0].action == "all_clear"


def test_single_point_no_neighbours():
    r = curate([(5.0, 5.0)])
    assert r.n_input == 1
    assert r.verdicts[0].verdict == Verdict.KEEP
    assert r.score == pytest.approx(100.0)


def test_all_points_invalid_grade_f():
    r = curate([(float("nan"), 0.0), (float("inf"), 0.0)])
    assert r.grade == "F"
    assert r.n_kept == 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_text_output_and_apply(tmp_path, capsys):
    src = tmp_path / "points.csv"
    src.write_text("x,y\n0,0\n0,0\nnan,1\n50,50\n200,50\n", encoding="utf-8")
    out_csv = tmp_path / "cleaned.csv"

    rc = vc.main([
        str(src),
        "--bounds", "0,100,0,100",
        "--format", "text",
        "--apply", str(out_csv),
    ])
    assert rc == 0

    captured = capsys.readouterr()
    assert "VoronoiMap Curator Report" in captured.out
    # CSV loader skips the literal "nan" header-style cell, so only 4 numeric rows survive parsing.
    assert "Input points :" in captured.out

    # Cleaned CSV exists, has header, and only contains in-bounds, unique, finite points.
    cleaned_text = out_csv.read_text(encoding="utf-8")
    lines = [ln for ln in cleaned_text.splitlines() if ln.strip()]
    assert lines[0] == "x,y"
    body = lines[1:]
    assert body  # at least one row
    for row in body:
        x, y = (float(v) for v in row.split(","))
        assert 0 <= x <= 100 and 0 <= y <= 100
        assert math.isfinite(x) and math.isfinite(y)


def test_cli_json_output(tmp_path, capsys):
    src = tmp_path / "points.csv"
    src.write_text("0,0\n10,10\n", encoding="utf-8")
    rc = vc.main([str(src), "--format", "json"])
    assert rc == 0
    blob = capsys.readouterr().out
    parsed = json.loads(blob)
    assert parsed["n_input"] == 2
    assert parsed["grade"] == "A"


def test_cli_missing_input_returns_2(tmp_path, capsys):
    rc = vc.main([str(tmp_path / "does_not_exist.csv")])
    assert rc == 2


def test_cli_parse_bounds_bad_spec(tmp_path):
    src = tmp_path / "points.csv"
    src.write_text("0,0\n", encoding="utf-8")
    # 3 floats instead of 4 should raise argparse.ArgumentTypeError -> SystemExit(2).
    with pytest.raises(SystemExit):
        vc.main([str(src), "--bounds", "0,100,0"])


# ---------------------------------------------------------------------------
# Dataclass plumbing
# ---------------------------------------------------------------------------

def test_pointverdict_to_dict_roundtrips():
    pv = PointVerdict(index=3, x=1.5, y=2.5, verdict=Verdict.KEEP,
                      priority=0, reasons=["ok"], merge_into=None)
    d = pv.to_dict()
    assert d["index"] == 3
    assert d["verdict"] == Verdict.KEEP
    assert d["reasons"] == ["ok"]


def test_playbookstep_to_dict():
    step = PlaybookStep("P0", "drop_invalid", 5, "remove invalid rows")
    d = step.to_dict()
    assert d == {
        "tier": "P0",
        "action": "drop_invalid",
        "count": 5,
        "detail": "remove invalid rows",
    }
