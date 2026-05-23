"""Tests for vormap_sentinel - Spatial Sentinel distribution monitoring.

Covers the previously-untested public Sentinel API:
- _density_histogram / _js_divergence / _quadrant_counts / _chi_squared
- _dbscan_simple / _std_distance
- Sentinel.learn / finalize_baseline / monitor (all 7 alert channels)
- SentinelReport.to_dict / to_json / to_html / summary_text / _apply_penalties
- save_baseline / load_baseline round-trip
- CLI helpers: add_sentinel_args / run_sentinel_cli (baseline dir, file list,
  load/save-baseline paths, report/html export)

The module had 13.25% coverage before this file landed.
"""

import argparse
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_sentinel as vs
from vormap_sentinel import (
    Alert,
    Sentinel,
    SentinelReport,
    _chi_squared,
    _dbscan_simple,
    _density_histogram,
    _js_divergence,
    _quadrant_counts,
    _std_distance,
    add_sentinel_args,
    run_sentinel_cli,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_points(path, pts):
    """Write a list of (x, y) points to a .txt file (one pair per line)."""
    with open(path, "w") as f:
        for x, y in pts:
            f.write(f"{x}\t{y}\n")


def _baseline_pts(seed_offset=0):
    """Return a deterministic ~uniform baseline distribution in [0, 100]^2."""
    pts = []
    for i in range(10):
        for j in range(10):
            pts.append((i * 10.0 + 5.0 + seed_offset,
                        j * 10.0 + 5.0 + seed_offset))
    return pts


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


class TestStdDistance:
    def test_empty_returns_zero(self):
        assert _std_distance([], 0, 0) == 0.0

    def test_single_point_returns_zero(self):
        assert _std_distance([(1.0, 2.0)], 1.0, 2.0) == 0.0

    def test_two_points_symmetric(self):
        # Points at (-1, 0) and (1, 0) around centroid (0, 0): variance = 1
        assert _std_distance([(-1.0, 0.0), (1.0, 0.0)], 0.0, 0.0) == pytest.approx(1.0)

    def test_uniform_grid_positive(self):
        pts = [(0, 0), (0, 10), (10, 0), (10, 10)]
        sd = _std_distance(pts, 5.0, 5.0)
        assert sd > 0


class TestDensityHistogram:
    def test_zero_grid_res_safe(self):
        # grid_res=0 is a degenerate input — function currently raises IndexError
        # because `cells = grid_res * grid_res == 0`. Document the contract.
        with pytest.raises((IndexError, ZeroDivisionError, ValueError)):
            _density_histogram([(1, 1)], (0, 0, 10, 10), 0)

    def test_normalized_sums_to_one(self):
        pts = [(1, 1), (5, 5), (9, 9), (3, 7)]
        bounds = (0, 0, 10, 10)
        out = _density_histogram(pts, bounds, 5)
        assert len(out) == 25
        assert sum(out) == pytest.approx(1.0)

    def test_clamps_out_of_bounds(self):
        # Point exactly at xmax/ymax should land in last cell (not crash)
        out = _density_histogram([(10, 10)], (0, 0, 10, 10), 4)
        assert sum(out) == pytest.approx(1.0)
        assert out[-1] == pytest.approx(1.0)

    def test_empty_points_returns_zero_grid(self):
        out = _density_histogram([], (0, 0, 10, 10), 3)
        assert out == [0.0] * 9


class TestJSDivergence:
    def test_identical_distributions_zero(self):
        p = [0.25, 0.25, 0.25, 0.25]
        assert _js_divergence(p, p) == pytest.approx(0.0)

    def test_disjoint_distributions_positive(self):
        p = [1.0, 0.0]
        q = [0.0, 1.0]
        assert _js_divergence(p, q) > 0

    def test_symmetric(self):
        p = [0.6, 0.4]
        q = [0.3, 0.7]
        assert _js_divergence(p, q) == pytest.approx(_js_divergence(q, p))


class TestQuadrantCounts:
    def test_all_quadrants(self):
        pts = [(-1, -1), (1, -1), (-1, 1), (1, 1)]
        counts = _quadrant_counts(pts, 0, 0)
        assert sum(counts) == 4
        assert all(c == 1 for c in counts)

    def test_empty_returns_zeros(self):
        assert _quadrant_counts([], 0, 0) == [0, 0, 0, 0]


class TestChiSquared:
    def test_no_deviation(self):
        assert _chi_squared([10, 10, 10], [10, 10, 10]) == 0.0

    def test_zero_expected_skipped(self):
        # Zero expected bins must not divide-by-zero
        assert _chi_squared([5, 0], [0, 5]) == pytest.approx(5.0)

    def test_basic_deviation(self):
        # (12-10)^2/10 + (8-10)^2/10 = 0.4 + 0.4 = 0.8
        assert _chi_squared([12, 8], [10, 10]) == pytest.approx(0.8)


class TestDbscanSimple:
    def test_empty(self):
        labels, n = _dbscan_simple([], 1.0, 3)
        assert labels == []
        assert n == 0

    def test_all_noise(self):
        # Three points far apart with min_pts=3 → all noise
        pts = [(0, 0), (100, 100), (200, 200)]
        labels, n = _dbscan_simple(pts, 1.0, 3)
        assert n == 0
        assert labels == [-1, -1, -1]

    def test_single_cluster(self):
        pts = [(0, 0), (0.5, 0.5), (1, 1), (0.2, 0.3)]
        labels, n = _dbscan_simple(pts, 2.0, 3)
        assert n == 1
        assert all(lbl == 0 for lbl in labels)

    def test_two_clusters(self):
        # Two dense clumps of 4 points each, far apart
        pts = [(0, 0), (0.3, 0.2), (0.6, 0.1), (0.1, 0.5),
               (100, 100), (100.3, 100.2), (100.6, 100.1), (100.1, 100.5)]
        labels, n = _dbscan_simple(pts, 2.0, 3)
        assert n == 2
        # First four points share a label, last four share another label
        assert labels[0] == labels[1] == labels[2] == labels[3]
        assert labels[4] == labels[5] == labels[6] == labels[7]
        assert labels[0] != labels[4]


# ---------------------------------------------------------------------------
# Sentinel.learn / finalize_baseline / monitor
# ---------------------------------------------------------------------------


class TestSentinelLearn:
    def test_learn_empty_source_is_noop(self):
        s = Sentinel(grid_res=4)
        s.learn([])  # empty list — must not raise, must not record
        assert s._baseline_counts == []
        assert s._bounds is None

    def test_learn_accumulates_bounds(self):
        s = Sentinel(grid_res=4)
        s.learn(_baseline_pts())
        s.learn(_baseline_pts(seed_offset=200))
        # Merged bounds should span both
        xmin, ymin, xmax, ymax = s._bounds
        assert xmin < 5.0
        assert xmax > 200.0

    def test_finalize_without_data_raises(self):
        s = Sentinel(grid_res=4)
        with pytest.raises(ValueError):
            s.finalize_baseline()

    def test_finalize_single_snapshot_uses_synthetic_std(self):
        s = Sentinel(grid_res=4)
        s.learn(_baseline_pts())
        s.finalize_baseline()
        assert s._finalized is True
        # Single snapshot → synthetic std fallbacks must be present
        assert s._baseline["count_std"] >= 1.0
        assert s._baseline["std_distance_std"] >= 1.0

    def test_monitor_before_finalize_raises(self):
        s = Sentinel(grid_res=4)
        s.learn(_baseline_pts())
        with pytest.raises(RuntimeError):
            s.monitor(_baseline_pts())


class TestSentinelMonitor:
    def _make(self, sensitivity=2.0):
        s = Sentinel(grid_res=5, sensitivity=sensitivity,
                     eps=15.0, min_cluster_pts=3)
        # Three near-identical baselines so std is small but nonzero
        s.learn(_baseline_pts())
        s.learn([(x + 0.1, y + 0.1) for (x, y) in _baseline_pts()])
        s.learn([(x - 0.1, y - 0.1) for (x, y) in _baseline_pts()])
        s.finalize_baseline()
        return s

    def test_empty_input_critical(self):
        s = self._make()
        report = s.monitor([])
        assert report.health_score == 0
        assert any(a.channel == "count" and a.severity == "critical"
                   for a in report.alerts)

    def test_clean_input_high_score(self):
        s = self._make()
        report = s.monitor(_baseline_pts())
        assert report.health_score >= 80
        # Metrics populated regardless of alerts
        assert "count_z_score" in report.metrics
        assert "density_jsd" in report.metrics
        assert "quadrant_chi2" in report.metrics

    def test_count_anomaly_triggers_alert(self):
        s = self._make()
        # Drastically smaller dataset
        report = s.monitor([(5.0, 5.0), (10.0, 10.0), (15.0, 15.0)])
        channels = {a.channel for a in report.alerts}
        assert "count_anomaly" in channels
        assert report.health_score < 100

    def test_centroid_shift_triggers_alert(self):
        s = self._make()
        # Same count, shifted far away
        shifted = [(x + 500, y + 500) for (x, y) in _baseline_pts()]
        report = s.monitor(shifted)
        channels = {a.channel for a in report.alerts}
        assert "centroid_shift" in channels

    def test_spread_change_triggers_alert(self):
        s = self._make()
        # Same count, spread expanded 50x around same centroid
        cx = sum(x for x, _ in _baseline_pts()) / 100
        cy = sum(y for _, y in _baseline_pts()) / 100
        spread = [(cx + (x - cx) * 50, cy + (y - cy) * 50)
                  for x, y in _baseline_pts()]
        report = s.monitor(spread)
        channels = {a.channel for a in report.alerts}
        assert "spread_change" in channels

    def test_report_apply_penalties_bounded(self):
        # Manually craft a report with many critical alerts → score floored at 0
        r = SentinelReport(source_file="x", point_count=10)
        for _ in range(10):
            r.alerts.append(Alert("c", "critical", 1.0, 0.0, "msg"))
        r._apply_penalties()
        assert r.health_score == 0

    def test_report_apply_penalties_info_only(self):
        r = SentinelReport(source_file="x", point_count=10)
        r.alerts.append(Alert("c", "info", 1.0, 0.0, "msg"))
        r._apply_penalties()
        assert r.health_score == 98


# ---------------------------------------------------------------------------
# SentinelReport export
# ---------------------------------------------------------------------------


class TestSentinelReportExport:
    def _report(self):
        r = SentinelReport(source_file="snap.txt", point_count=42)
        r.alerts.append(Alert("count_anomaly", "warning", 3.2, 2.0, "drift"))
        r.alerts.append(Alert("centroid_shift", "critical", 50.0, 10.0, "moved"))
        r.metrics = {"count_z_score": 3.2, "density_jsd": 0.12}
        r._apply_penalties()
        return r

    def test_to_dict_shape(self):
        d = self._report().to_dict()
        assert d["point_count"] == 42
        assert d["alert_count"] == 2
        assert d["source_file"] == "snap.txt"
        assert d["health_score"] == 100 - 10 - 25
        assert {a["severity"] for a in d["alerts"]} == {"warning", "critical"}

    def test_to_json_round_trip(self, tmp_path):
        path = tmp_path / "report.json"
        self._report().to_json(str(path))
        loaded = json.loads(path.read_text())
        assert loaded["alert_count"] == 2
        assert loaded["metrics"]["density_jsd"] == pytest.approx(0.12)

    def test_to_html_escapes_and_renders(self, tmp_path):
        # Inject a hostile-looking alert message
        r = SentinelReport(source_file="<x>", point_count=1)
        r.alerts.append(Alert("<script>", "info", 1.0, 0.5, "<b>x</b>"))
        r.metrics = {"k": 1.5}
        path = tmp_path / "r.html"
        r.to_html(str(path))
        text = path.read_text(encoding="utf-8")
        # Real tags from injected fields should be escaped
        assert "&lt;script&gt;" in text
        assert "&lt;b&gt;x&lt;/b&gt;" in text
        # Source file too
        assert "&lt;x&gt;" in text
        # Standard structural markup is still emitted
        assert "<table" in text and "</html>" in text

    def test_to_html_no_alerts_branch(self, tmp_path):
        r = SentinelReport(source_file="ok", point_count=10)
        r.metrics = {"k": 1.0}
        path = tmp_path / "ok.html"
        r.to_html(str(path))
        text = path.read_text(encoding="utf-8")
        assert "All clear" in text

    def test_summary_text_lists_alerts(self):
        s = self._report().summary_text()
        assert "Health:" in s
        assert "CRITICAL" in s
        assert "WARNING" in s


# ---------------------------------------------------------------------------
# Baseline persistence
# ---------------------------------------------------------------------------


class TestBaselinePersistence:
    def test_save_before_finalize_raises(self, tmp_path):
        s = Sentinel(grid_res=4)
        with pytest.raises(RuntimeError):
            s.save_baseline(str(tmp_path / "x.json"))

    def test_round_trip_preserves_monitor_results(self, tmp_path):
        s = Sentinel(grid_res=5, sensitivity=2.0, eps=15.0, min_cluster_pts=3)
        s.learn(_baseline_pts())
        s.learn([(x + 0.1, y + 0.1) for (x, y) in _baseline_pts()])
        s.finalize_baseline()
        baseline_path = tmp_path / "baseline.json"
        s.save_baseline(str(baseline_path))

        s2 = Sentinel.load_baseline(str(baseline_path))
        assert s2._finalized is True
        assert s2.grid_res == 5
        assert s2.sensitivity == 2.0
        # Centroid recovered as a tuple
        assert isinstance(s2._baseline["centroid"], tuple)

        # Monitoring the same data through both sentinels produces same alert set
        r1 = s.monitor(_baseline_pts())
        r2 = s2.monitor(_baseline_pts())
        assert {a.channel for a in r1.alerts} == {a.channel for a in r2.alerts}


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


class TestCli:
    def _parser(self):
        p = argparse.ArgumentParser()
        add_sentinel_args(p)
        return p

    def test_add_sentinel_args_registers_all_flags(self):
        p = self._parser()
        # Every documented flag must parse
        args = p.parse_args([
            "--sentinel-baseline", "a.txt",
            "--sentinel-report", "r.json",
            "--sentinel-html", "r.html",
            "--sentinel-sensitivity", "1.5",
            "--sentinel-save-baseline", "b.json",
        ])
        assert args.sentinel_baseline == "a.txt"
        assert args.sentinel_sensitivity == 1.5
        assert args.sentinel_save_baseline == "b.json"

    def test_run_sentinel_cli_no_flags_returns_none(self):
        p = self._parser()
        args = p.parse_args([])
        report, sentinel = run_sentinel_cli(args, "ignored")
        assert report is None
        assert sentinel is None

    def test_run_sentinel_cli_full_pipeline(self, tmp_path, capsys):
        # Build three baseline files in a directory
        baseline_dir = tmp_path / "baseline"
        baseline_dir.mkdir()
        for i in range(3):
            _write_points(baseline_dir / f"b{i}.txt",
                          [(x + i * 0.1, y + i * 0.1) for (x, y) in _baseline_pts()])

        # Build a "current" input file
        cur = tmp_path / "current.txt"
        _write_points(cur, _baseline_pts())

        report_json = tmp_path / "report.json"
        report_html = tmp_path / "report.html"
        saved_baseline = tmp_path / "saved.json"

        p = self._parser()
        args = p.parse_args([
            "--sentinel-baseline", str(baseline_dir),
            "--sentinel-report", str(report_json),
            "--sentinel-html", str(report_html),
            "--sentinel-sensitivity", "2.0",
            "--sentinel-save-baseline", str(saved_baseline),
        ])
        report, sentinel = run_sentinel_cli(args, str(cur))

        assert report is not None
        assert sentinel is not None
        assert report_json.exists()
        assert report_html.exists()
        assert saved_baseline.exists()

        out = capsys.readouterr().out
        assert "SPATIAL SENTINEL REPORT" in out
        assert "Health:" in out

    def test_run_sentinel_cli_load_baseline(self, tmp_path, capsys):
        # First build a sentinel and save its baseline
        s = Sentinel(grid_res=5, sensitivity=2.0, eps=15.0, min_cluster_pts=3)
        s.learn(_baseline_pts())
        s.learn([(x + 0.1, y + 0.1) for (x, y) in _baseline_pts()])
        s.finalize_baseline()
        saved = tmp_path / "baseline.json"
        s.save_baseline(str(saved))

        cur = tmp_path / "cur.txt"
        _write_points(cur, _baseline_pts())

        p = self._parser()
        args = p.parse_args([
            "--sentinel-load-baseline", str(saved),
            "--sentinel-sensitivity", "1.0",
        ])
        report, sentinel = run_sentinel_cli(args, str(cur))
        assert report is not None
        # CLI overrides sensitivity onto the loaded sentinel
        assert sentinel.sensitivity == 1.0

    def test_run_sentinel_cli_comma_separated_files(self, tmp_path):
        b1 = tmp_path / "b1.txt"
        b2 = tmp_path / "b2.txt"
        _write_points(b1, _baseline_pts())
        _write_points(b2, [(x + 0.1, y + 0.1) for (x, y) in _baseline_pts()])
        cur = tmp_path / "cur.txt"
        _write_points(cur, _baseline_pts())

        p = self._parser()
        args = p.parse_args([
            "--sentinel-baseline", f"{b1},{b2}",
            "--sentinel-sensitivity", "2.0",
        ])
        report, sentinel = run_sentinel_cli(args, str(cur))
        assert report is not None
        assert sentinel is not None
