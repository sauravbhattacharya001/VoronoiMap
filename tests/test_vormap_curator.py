"""Unit tests for vormap_curator."""

import json
import os
import sys


# Ensure repo root is on sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import vormap_curator as vc


def test_clean_dataset_grade_a():
    points = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0), (5.0, 5.0)]
    report = vc.curate(points)
    assert report.n_input == 5
    assert report.counts[vc.Verdict.KEEP] == 5
    assert report.grade == "A"
    assert report.score == 100.0
    assert report.n_kept == 5


def test_drops_nan_and_inf():
    points = [(0.0, 0.0), (float("nan"), 1.0), (1.0, float("inf")), (2.0, 2.0)]
    report = vc.curate(points)
    assert report.counts[vc.Verdict.DROP_INVALID] == 2
    assert any("non_finite_coord" in v.reasons for v in report.verdicts)
    cleaned = vc.apply_plan(points, report)
    assert (0.0, 0.0) in cleaned and (2.0, 2.0) in cleaned
    assert len(cleaned) == 2


def test_drops_malformed_row():
    points = [(0.0, 0.0), (1.0,), "garbage", (2.0, 2.0)]
    report = vc.curate(points)
    assert report.counts[vc.Verdict.DROP_INVALID] == 2


def test_exact_duplicates():
    points = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (5.0, 5.0)]
    report = vc.curate(points)
    assert report.counts[vc.Verdict.DROP_DUPLICATE] == 2
    assert report.counts[vc.Verdict.KEEP] + report.counts[vc.Verdict.REVIEW] == 2


def test_near_duplicate_merge():
    points = [(0.0, 0.0), (1e-9, 1e-9), (1e-9, 0.0), (50.0, 50.0)]
    report = vc.curate(points, near_duplicate_tol=1e-6)
    merges = [v for v in report.verdicts if v.verdict == vc.Verdict.MERGE]
    assert len(merges) == 2
    # representative is the lowest index in the cluster (0)
    for m in merges:
        assert m.merge_into == 0


def test_out_of_bounds():
    points = [(5.0, 5.0), (-1.0, 5.0), (500.0, 500.0)]
    report = vc.curate(points, bounds=(0.0, 100.0, 0.0, 100.0))
    assert report.counts[vc.Verdict.DROP_OUT_OF_BOUNDS] == 2


def test_isolation_flag():
    # Cluster of points + one extreme outlier
    pts = [(i * 1.0, 0.0) for i in range(10)] + [(10_000.0, 0.0)]
    report = vc.curate(pts, isolation_k=4.0)
    outlier = next(v for v in report.verdicts if v.x == 10_000.0)
    assert outlier.verdict == vc.Verdict.REVIEW
    assert any("isolated" in r for r in outlier.reasons)


def test_boundary_review():
    bounds = (0.0, 100.0, 0.0, 100.0)
    points = [(50.0, 50.0), (0.5, 50.0)]  # 0.5 is well inside the 2% pad zone
    report = vc.curate(points, bounds=bounds, boundary_pad_frac=0.05)
    near = next(v for v in report.verdicts if v.x == 0.5)
    assert near.verdict == vc.Verdict.REVIEW
    assert "near_boundary" in near.reasons


def test_low_precision_review():
    points = [(1.0, 2.0), (12.345, 67.890)]
    report = vc.curate(points, low_precision_max_digits=1)
    flagged = next(v for v in report.verdicts if v.x == 1.0)
    assert flagged.verdict == vc.Verdict.REVIEW
    assert any("low_precision" in r for r in flagged.reasons)


def test_playbook_orders_p0_first():
    points = [(float("nan"), 0.0), (0.0, 0.0), (0.0, 0.0)]
    report = vc.curate(points)
    tiers = [step.tier for step in report.playbook]
    assert tiers[0] == "P0"
    assert "P1" in tiers


def test_all_clear_playbook():
    points = [(0.0, 0.0), (10.0, 5.0), (5.0, 10.0)]
    report = vc.curate(points)
    assert len(report.playbook) == 1
    assert report.playbook[0].action == "all_clear"


def test_before_after_stats():
    pts = [(0.0, 0.0), (0.0, 0.0), (10.0, 10.0), (20.0, 0.0)]
    report = vc.curate(pts)
    assert report.before_stats["count"] == 4
    assert report.after_stats["count"] == 3


def test_format_text_runs():
    report = vc.curate([(0.0, 0.0), (1.0, 1.0)])
    out = vc.format_text(report)
    assert "Curator Report" in out
    assert "Grade" in out


def test_format_markdown_runs():
    report = vc.curate([(0.0, 0.0), (1.0, 1.0)])
    out = vc.format_markdown(report)
    assert out.startswith("# VoronoiMap Curator Report")
    assert "| Verdict | Count |" in out


def test_format_json_roundtrip():
    report = vc.curate([(0.0, 0.0), (1.0, 1.0), (float("nan"), 0.0)])
    blob = vc.format_json(report)
    data = json.loads(blob)
    assert data["n_input"] == 3
    assert data["counts"]["DROP_INVALID"] == 1
    assert "playbook" in data and isinstance(data["playbook"], list)


def test_cli_text(tmp_path, capsys):
    f = tmp_path / "pts.csv"
    f.write_text("x,y\n0,0\n10,10\n,bad\nnan,0\n5,5\n")
    rc = vc.main([str(f), "--format", "text"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Curator Report" in out
    assert "DROP_INVALID" in out


def test_cli_apply(tmp_path, capsys):
    f = tmp_path / "pts.csv"
    f.write_text("0,0\n0,0\n5,5\n10,10\n")
    out_file = tmp_path / "cleaned.csv"
    rc = vc.main([str(f), "--format", "json", "--apply", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    lines = out_file.read_text().strip().splitlines()
    # header + 3 unique points (one duplicate dropped)
    assert lines[0] == "x,y"
    assert len(lines) == 4


def test_empty_input():
    report = vc.curate([])
    assert report.n_input == 0
    assert report.n_kept == 0
    assert report.score == 0.0
    assert report.grade == "F"
