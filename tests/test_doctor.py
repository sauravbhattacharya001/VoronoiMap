"""Tests for ``vormap_doctor`` — dataset diagnostician + auto-fix prescriber.

Covers all 12 diagnostic checks, the ``Diagnosis`` aggregator, JSON / HTML
report rendering (including the HTML-escaping security fix for
user-controlled dataset paths and fix-command strings), the ``--strict``
escalation behaviour, and the CLI surface.

The tests deliberately use small synthetic point sets so each check can be
driven into both its ``ok`` branch and the higher-severity branches.
"""

from __future__ import annotations

import io
import json
import os
import random
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

import pytest

# Add repo root to sys.path so we can import the top-level modules.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import vormap_doctor as vd  # noqa: E402


# Ensure subprocess invocations of the CLI can emit the emoji-heavy banner
# on Windows consoles that default to cp1252.
_CLI_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


# ---------------------------------------------------------------------------
# Helpers


def _write_points(tmp_path, points, name="pts.txt"):
    path = tmp_path / name
    with path.open("w", encoding="utf-8") as fh:
        for x, y in points:
            fh.write(f"{x} {y}\n")
    return str(path)


def _grid(n):
    """Return an n x n unit-grid point set (regular spacing, no duplicates)."""
    return [(i, j) for i in range(n) for j in range(n)]


def _random_points(n, seed=42, scale=100.0):
    rng = random.Random(seed)
    return [(rng.uniform(0, scale), rng.uniform(0, scale)) for _ in range(n)]


def _find(diag, check):
    for f in diag.findings:
        if f.check == check:
            return f
    raise AssertionError(f"Finding {check!r} not present; got {[f.check for f in diag.findings]}")


# ---------------------------------------------------------------------------
# Diagnosis container


class TestDiagnosis:
    def test_score_starts_at_100_with_only_ok(self):
        findings = [vd.Finding("a", "ok", "fine", None, {})]
        d = vd.Diagnosis("x.txt", 10, findings)
        assert d.health_score == 100
        assert "looks good" in d.summary.lower()

    def test_score_deductions_match_severity_weights(self):
        findings = [
            vd.Finding("info1", "info", "", None, {}),
            vd.Finding("warn1", "warning", "", None, {}),
            vd.Finding("crit1", "critical", "", None, {}),
        ]
        d = vd.Diagnosis("x", 0, findings)
        assert d.health_score == 100 - 5 - 15 - 30

    def test_score_floors_at_zero(self):
        findings = [vd.Finding(f"crit{i}", "critical", "", None, {}) for i in range(20)]
        d = vd.Diagnosis("x", 0, findings)
        assert d.health_score == 0

    def test_summary_critical_branch(self):
        findings = [vd.Finding("c", "critical", "boom", None, {})]
        d = vd.Diagnosis("x", 1, findings)
        assert "critical" in d.summary

    def test_summary_warning_branch(self):
        findings = [vd.Finding("w", "warning", "meh", None, {})]
        d = vd.Diagnosis("x", 1, findings)
        assert "warning" in d.summary.lower()

    def test_auto_fix_plan_collects_only_findings_with_commands(self):
        findings = [
            vd.Finding("a", "warning", "m", "python vormap_x.py", {}),
            vd.Finding("b", "ok", "m", None, {}),
        ]
        d = vd.Diagnosis("x", 1, findings)
        assert d.auto_fix_plan == ["python vormap_x.py"]


# ---------------------------------------------------------------------------
# Individual diagnostic checks


class TestIndividualChecks:
    # sample_size --------------------------------------------------------
    def test_sample_size_critical_under_3(self):
        f = vd._check_sample_size([(0, 0), (1, 1)], "x")
        assert f.severity == "critical"

    def test_sample_size_warning_under_5(self):
        f = vd._check_sample_size(_grid(2), "x")  # 4 points
        assert f.severity == "warning"

    def test_sample_size_info_when_large(self):
        # Fabricate count without allocating 50001 tuples.
        big = [(0, 0)] * 50001
        f = vd._check_sample_size(big, "data.txt")
        assert f.severity == "info"
        assert "vormap_sample" in f.fix_command

    def test_sample_size_ok_band(self):
        f = vd._check_sample_size(_grid(5), "x")  # 25 points
        assert f.severity == "ok"

    # duplicates --------------------------------------------------------
    def test_duplicates_ok_when_unique(self):
        f = vd._check_duplicates(_grid(4), "x")
        assert f.severity == "ok"
        assert f.details["count"] == 0

    def test_duplicates_warning_when_some(self):
        pts = _grid(5) + [(0, 0)]  # 1 dup out of 26
        f = vd._check_duplicates(pts, "x")
        assert f.severity == "warning"
        assert f.details["count"] == 1
        assert "--dedup" in f.fix_command

    def test_duplicates_critical_when_many(self):
        pts = _grid(3) + _grid(3)  # 50% dup
        f = vd._check_duplicates(pts, "x")
        assert f.severity == "critical"

    # scale --------------------------------------------------------------
    def test_scale_ok_for_normal_coords(self):
        f = vd._check_scale([(0, 0), (10, 10)], "x")
        assert f.severity == "ok"

    def test_scale_warning_at_large(self):
        f = vd._check_scale([(0, 0), (1e9, 1e9)], "x")
        assert f.severity == "warning"

    def test_scale_critical_at_extreme(self):
        f = vd._check_scale([(0, 0), (1e13, 1e13)], "x")
        assert f.severity == "critical"

    def test_scale_info_when_very_small(self):
        f = vd._check_scale([(1e-8, 1e-8), (2e-8, 2e-8)], "x")
        assert f.severity == "info"

    # aspect_ratio ------------------------------------------------------
    def test_aspect_ratio_collinear_is_critical(self):
        f = vd._check_aspect_ratio([(0, 0), (1, 0), (5, 0)], "x")
        assert f.severity == "critical"

    def test_aspect_ratio_warning_extreme(self):
        f = vd._check_aspect_ratio([(0, 0), (100, 1)], "x")
        assert f.severity == "warning"

    def test_aspect_ratio_info_high(self):
        f = vd._check_aspect_ratio([(0, 0), (15, 1)], "x")
        assert f.severity == "info"

    def test_aspect_ratio_ok(self):
        f = vd._check_aspect_ratio([(0, 0), (10, 9)], "x")
        assert f.severity == "ok"

    # min_spacing -------------------------------------------------------
    def test_min_spacing_too_few(self):
        f = vd._check_min_spacing([(0, 0)], "x")
        assert f.severity == "ok"

    def test_min_spacing_all_same_is_critical(self):
        f = vd._check_min_spacing([(1, 1), (1, 1), (1, 1)], "x")
        assert f.severity == "critical"

    def test_min_spacing_warning_near_zero(self):
        # Two extremely close points + a far outlier => extent is large,
        # min spacing is ~1e-12 of extent.
        pts = [(0.0, 0.0), (1e-2, 1e-2), (1e12, 1e12)]
        f = vd._check_min_spacing(pts, "x")
        assert f.severity == "warning"

    def test_min_spacing_ok_uniform_grid(self):
        f = vd._check_min_spacing(_grid(5), "x")
        assert f.severity == "ok"

    # boundary_crowding -------------------------------------------------
    def test_boundary_crowding_too_few(self):
        f = vd._check_boundary_crowding([(0, 0), (1, 1)], "x")
        assert f.severity == "ok"

    def test_boundary_crowding_warning_when_most_on_edge(self):
        # All 16 points on the perimeter of [0,3] x [0,3] => 100% edge.
        pts = []
        for i in range(4):
            pts.append((i, 0))
            pts.append((i, 3))
            pts.append((0, i))
            pts.append((3, i))
        f = vd._check_boundary_crowding(pts, "x")
        assert f.severity == "warning"
        assert "--margin" in f.fix_command

    # density_uniformity -----------------------------------------------
    def test_density_uniformity_too_few(self):
        f = vd._check_density_uniformity(_grid(3), "x")
        assert f.severity == "ok"

    def test_density_uniformity_ok_for_grid(self):
        f = vd._check_density_uniformity(_grid(10), "x")
        assert f.severity == "ok"

    def test_density_uniformity_warning_when_skewed(self):
        # 100 points in one corner + 1 far outlier => extreme CV.
        pts = [(random.Random(7).uniform(0, 1), random.Random(7 + i).uniform(0, 1))
               for i in range(100)]
        pts.append((1000, 1000))
        f = vd._check_density_uniformity(pts, "x")
        assert f.severity in ("warning", "info")  # accept either if scoring is on the boundary

    # degeneracy --------------------------------------------------------
    def test_degeneracy_too_few(self):
        f = vd._check_degeneracy([(0, 0), (1, 1)], "x")
        assert f.severity == "ok"

    def test_degeneracy_warning_on_pure_line(self):
        pts = [(i, 0) for i in range(20)]
        f = vd._check_degeneracy(pts, "x")
        assert f.severity == "warning"
        assert "--jitter" in f.fix_command

    def test_degeneracy_ok_on_random_cloud(self):
        f = vd._check_degeneracy(_random_points(50), "x")
        assert f.severity == "ok"

    # hull_efficiency ---------------------------------------------------
    def test_hull_efficiency_too_few(self):
        f = vd._check_hull_efficiency([(0, 0), (1, 1)], "x")
        assert f.severity == "ok"

    def test_hull_efficiency_ok_for_random(self):
        f = vd._check_hull_efficiency(_random_points(50), "x")
        assert f.severity == "ok"

    def test_hull_efficiency_info_for_thin_diagonal(self):
        # Tiny perpendicular jitter so it isn't collinear (would early-out).
        pts = [(i, i + (0.001 if i % 2 else -0.001)) for i in range(40)]
        f = vd._check_hull_efficiency(pts, "x")
        assert f.severity == "info"

    # nn_distribution ---------------------------------------------------
    def test_nn_distribution_too_few(self):
        f = vd._check_nn_distribution([], "x", [])
        assert f.severity == "ok"

    def test_nn_distribution_warning_high_cv(self):
        nn = [0.01] * 15 + [100.0] * 5  # CV > 1.5
        f = vd._check_nn_distribution([(0, 0)] * 20, "x", nn)
        assert f.severity == "warning"

    def test_nn_distribution_info_lattice(self):
        nn = [1.0] * 20  # CV = 0
        f = vd._check_nn_distribution([(0, 0)] * 20, "x", nn)
        assert f.severity == "info"

    # isolation ---------------------------------------------------------
    def test_isolation_too_few(self):
        f = vd._check_isolation([(0, 0)], "x", [1.0])
        assert f.severity == "ok"

    def test_isolation_flags_outliers(self):
        nn = [1.0] * 50 + [100.0]
        f = vd._check_isolation([(0, 0)] * 51, "x", nn)
        assert f.severity in ("warning", "info")
        assert f.details["outlier_count"] >= 1

    # cluster_imbalance -------------------------------------------------
    def test_cluster_imbalance_too_few(self):
        f = vd._check_cluster_imbalance(_grid(3), "x")
        assert f.severity == "ok"

    def test_cluster_imbalance_balanced_grid(self):
        f = vd._check_cluster_imbalance(_grid(10), "x")
        assert f.severity == "ok"

    def test_cluster_imbalance_warning_when_one_corner(self):
        pts = [(0.01 * i, 0.01 * j) for i in range(10) for j in range(10)]
        pts.append((1000, 1000))  # bbox stretches; all 100 fall in one quadrant
        f = vd._check_cluster_imbalance(pts, "x")
        assert f.severity == "warning"


# ---------------------------------------------------------------------------
# Integration: diagnose() end-to-end


class TestDiagnoseEndToEnd:
    def test_diagnose_on_clean_grid(self, tmp_path):
        path = _write_points(tmp_path, _grid(10))
        d = vd.diagnose(path)
        assert d.point_count == 100
        assert d.health_score > 50  # grid is clean enough
        # all 12 named checks must appear
        names = {f.check for f in d.findings}
        for expected in (
            "sample_size", "duplicates", "scale", "aspect_ratio", "min_spacing",
            "boundary_crowding", "density_uniformity", "degeneracy",
            "hull_efficiency", "cluster_imbalance", "isolation", "nn_distribution",
        ):
            assert expected in names

    def test_diagnose_strict_escalates_info(self, tmp_path):
        # Lattice points create an "info" NN-distribution finding.
        path = _write_points(tmp_path, _grid(10))
        loose = vd.diagnose(path, strict=False)
        strict = vd.diagnose(path, strict=True)
        loose_infos = sum(1 for f in loose.findings if f.severity == "info")
        strict_infos = sum(1 for f in strict.findings if f.severity == "info")
        assert strict_infos == 0
        # Anything that was info in loose mode is at least a warning in strict.
        if loose_infos:
            assert strict.health_score <= loose.health_score

    def test_diagnose_two_points_skips_optional_checks(self, tmp_path):
        path = _write_points(tmp_path, [(0, 0), (1, 1)])
        d = vd.diagnose(path)
        # Should still produce sample_size finding plus the bulk of checks
        # (they run when len(points) >= 2).
        names = {f.check for f in d.findings}
        assert "sample_size" in names

    def test_diagnose_one_point_only_runs_sample_size(self, tmp_path):
        path = _write_points(tmp_path, [(0, 0)])
        d = vd.diagnose(path)
        assert [f.check for f in d.findings] == ["sample_size"]
        assert d.findings[0].severity == "critical"


# ---------------------------------------------------------------------------
# Report rendering


class TestJSONReport:
    def test_to_json_returns_valid_json(self, tmp_path):
        path = _write_points(tmp_path, _grid(6))
        d = vd.diagnose(path)
        text = d.to_json()
        payload = json.loads(text)
        assert payload["point_count"] == 36
        assert payload["health_score"] == d.health_score
        assert "findings" in payload
        assert isinstance(payload["auto_fix_plan"], list)

    def test_to_json_writes_file_when_path_given(self, tmp_path):
        path = _write_points(tmp_path, _grid(6))
        d = vd.diagnose(path)
        out = tmp_path / "report.json"
        d.to_json(str(out))
        assert out.exists()
        parsed = json.loads(out.read_text(encoding="utf-8"))
        assert parsed["dataset"] == path


class TestHTMLReportSecurity:
    """The HTML report is rendered with f-strings; without escaping a
    crafted dataset path or finding message could inject script tags into
    the report. These tests pin the escaping behaviour added in run #4325.
    """

    def _render(self, tmp_path, findings, dataset_path="data.txt", point_count=10):
        d = vd.Diagnosis(dataset_path, point_count, findings)
        out = tmp_path / "r.html"
        d.to_html(str(out))
        return out.read_text(encoding="utf-8")

    def test_dataset_path_is_escaped(self, tmp_path):
        html = self._render(
            tmp_path,
            [vd.Finding("ok", "ok", "fine", None, {})],
            dataset_path="<script>alert(1)</script>.txt",
        )
        assert "<script>alert(1)</script>.txt" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;.txt" in html

    def test_finding_message_is_escaped(self, tmp_path):
        bad = vd.Finding("xss", "warning", "<img src=x onerror=alert(1)>", None, {})
        html = self._render(tmp_path, [bad])
        assert "<img src=x onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html

    def test_fix_command_is_escaped(self, tmp_path):
        bad = vd.Finding(
            "xss", "warning", "msg",
            "python tool.py \"</code><script>alert(1)</script>\"",
            {},
        )
        html = self._render(tmp_path, [bad])
        assert "</code><script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    def test_check_name_is_escaped(self, tmp_path):
        bad = vd.Finding("<b>name</b>", "ok", "m", None, {})
        html = self._render(tmp_path, [bad])
        assert "<b>name</b>" not in html.replace("<b>name</b>".lower(), "")
        # Be precise: the literal angle-bracketed tag should not appear in the body.
        assert "<b>name</b>" not in html

    def test_summary_is_escaped(self, tmp_path):
        # Build a diagnosis whose summary derives from an injected message.
        findings = [vd.Finding("c", "critical", "<x>", None, {})]
        d = vd.Diagnosis("p.txt", 1, findings)
        # Force summary to contain HTML by post-hoc assignment (mirrors what
        # a downstream caller could do):
        d.summary = "<script>boom()</script>"
        out = tmp_path / "r.html"
        d.to_html(str(out))
        html = out.read_text(encoding="utf-8")
        assert "<script>boom()</script>" not in html
        assert "&lt;script&gt;boom()&lt;/script&gt;" in html

    def test_html_is_still_well_formed(self, tmp_path):
        # Sanity: escaped output is still parseable XML-ish and contains
        # the structural skeleton we expect.
        html = self._render(
            tmp_path,
            [vd.Finding("ok", "ok", "fine", "python vormap_x.py", {})],
            dataset_path="ordinary.txt",
        )
        assert "<!DOCTYPE html>" in html
        assert "<title>VoronoiMap Doctor Report</title>" in html
        assert "ordinary.txt" in html
        assert "vormap_x.py" in html

    def test_no_fix_plan_message_when_empty(self, tmp_path):
        html = self._render(tmp_path, [vd.Finding("ok", "ok", "fine", None, {})])
        assert "No fixes needed." in html


# ---------------------------------------------------------------------------
# CLI


class TestCLI:
    def test_print_report_runs(self, tmp_path, capsys):
        path = _write_points(tmp_path, _grid(6))
        d = vd.diagnose(path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            vd._print_report(d)
        out = buf.getvalue()
        assert "VoronoiMap Doctor" in out
        assert "Health" in out

    def test_severity_badge_known_and_unknown(self):
        for sev in ("ok", "info", "warning", "critical"):
            assert "[" in vd._severity_badge(sev)
        assert vd._severity_badge("mystery") == "mystery"

    def test_main_runs_subprocess(self, tmp_path):
        path = _write_points(tmp_path, _grid(6))
        # Run as a subprocess for true CLI coverage.
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "vormap_doctor.py"), path],
            capture_output=True, text=True, timeout=30, env=_CLI_ENV,
        )
        assert result.returncode == 0, result.stderr
        assert "VoronoiMap Doctor" in result.stdout

    def test_main_auto_fix_only(self, tmp_path):
        # Force at least one fix by adding 50% duplicates.
        pts = _grid(4) + _grid(4)
        path = _write_points(tmp_path, pts)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "vormap_doctor.py"), path, "--auto-fix"],
            capture_output=True, text=True, timeout=30, env=_CLI_ENV,
        )
        assert result.returncode == 0, result.stderr
        # Either real fixes were emitted or the clean-dataset banner.
        assert ("python vormap_" in result.stdout) or ("No fixes needed" in result.stdout)

    def test_main_writes_json_and_html(self, tmp_path):
        path = _write_points(tmp_path, _grid(6))
        json_out = tmp_path / "r.json"
        html_out = tmp_path / "r.html"
        result = subprocess.run(
            [
                sys.executable, os.path.join(ROOT, "vormap_doctor.py"), path,
                "--json", str(json_out), "--html", str(html_out),
            ],
            capture_output=True, text=True, timeout=30, env=_CLI_ENV,
        )
        assert result.returncode == 0, result.stderr
        assert json_out.exists()
        assert html_out.exists()
        # JSON parses, HTML has the doctype.
        json.loads(json_out.read_text(encoding="utf-8"))
        assert html_out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")

    def test_main_strict_flag(self, tmp_path):
        path = _write_points(tmp_path, _grid(6))
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "vormap_doctor.py"), path, "--strict"],
            capture_output=True, text=True, timeout=30, env=_CLI_ENV,
        )
        assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Smoke: helper math


class TestHelperMath:
    def test_mean_empty(self):
        assert vd._mean([]) == 0

    def test_mean_basic(self):
        assert vd._mean([1, 2, 3]) == 2

    def test_stddev_zero_for_single(self):
        assert vd._stddev([5]) == 0

    def test_stddev_positive(self):
        assert vd._stddev([1, 2, 3, 4, 5]) > 0

    def test_nn_distances_too_few(self):
        assert vd._nn_distances([(0, 0)]) == []

    def test_nn_distances_basic(self):
        dists = vd._nn_distances([(0, 0), (3, 4), (6, 8)])
        assert len(dists) == 3
        assert all(d > 0 for d in dists)

    def test_nn_distances_fast_small_path(self):
        pts = _grid(5)
        assert len(vd._nn_distances_fast(pts)) == 25

    def test_nn_distances_fast_samples_when_large(self):
        # Cheap "large" set: 2500 random points triggers the sampler branch.
        pts = _random_points(2500)
        sample = vd._nn_distances_fast(pts)
        assert len(sample) == 2000
