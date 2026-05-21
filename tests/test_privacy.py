"""Tests for vormap_privacy - Spatial Privacy Engine.

Covers: load/save helpers, laplace_noise, k_anonymize, grid_snap, donut_mask,
optimize_privacy across all 4 methods, audit_privacy (grades A-F),
_mean_displacement / _mean_displacement_nn, and the CLI for every subcommand.
"""

import json
import math
import os
import random
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vormap_privacy as vp


# ---------------------------------------------------------------------------
# Fixtures

@pytest.fixture
def small_cluster():
    """Small tight cluster around (50, 50) - easy to reason about."""
    random.seed(0)
    return [(50.0 + random.uniform(-1, 1), 50.0 + random.uniform(-1, 1))
            for _ in range(20)]


@pytest.fixture
def grid_points():
    """10x10 grid of points."""
    return [(float(x), float(y)) for x in range(10) for y in range(10)]


@pytest.fixture
def points_file(tmp_path, grid_points):
    p = tmp_path / "pts.txt"
    with open(p, "w") as f:
        for x, y in grid_points:
            f.write(f"{x:.3f} {y:.3f}\n")
    return p


# ---------------------------------------------------------------------------
# I/O helpers

def test_load_points_roundtrip(tmp_path):
    pts = [(1.0, 2.0), (3.5, -7.25), (0.0, 0.0)]
    f = tmp_path / "pts.txt"
    vp._save_points(pts, str(f))
    loaded = vp._load_points(str(f))
    assert len(loaded) == 3
    for (a, b), (c, d) in zip(pts, loaded):
        assert math.isclose(a, c, abs_tol=1e-5)
        assert math.isclose(b, d, abs_tol=1e-5)


def test_load_points_skips_comments_and_blank_lines(tmp_path):
    f = tmp_path / "pts.txt"
    f.write_text("# comment\n\n1 2\n  \nbogus line\n3 4 5\n")
    pts = vp._load_points(str(f))
    # "bogus line" -> ValueError swallowed; "3 4 5" keeps first two
    assert pts == [(1.0, 2.0), (3.0, 4.0)]


# ---------------------------------------------------------------------------
# laplace_noise

def test_laplace_noise_empty():
    assert vp.laplace_noise([]) == []


def test_laplace_noise_shape_and_perturbation(small_cluster):
    out = vp.laplace_noise(small_cluster, epsilon=1.0, seed=42)
    assert len(out) == len(small_cluster)
    # not identical (vanishingly unlikely)
    assert any(o != p for o, p in zip(out, small_cluster))


def test_laplace_noise_seed_is_reproducible(small_cluster):
    a = vp.laplace_noise(small_cluster, epsilon=0.5, seed=123)
    b = vp.laplace_noise(small_cluster, epsilon=0.5, seed=123)
    assert a == b


def test_laplace_noise_smaller_epsilon_means_more_noise(small_cluster):
    """Lower eps -> larger expected displacement."""
    strong = vp.laplace_noise(small_cluster, epsilon=0.05, seed=7)
    weak = vp.laplace_noise(small_cluster, epsilon=5.0, seed=7)
    d_strong = vp._mean_displacement(small_cluster, strong)
    d_weak = vp._mean_displacement(small_cluster, weak)
    assert d_strong > d_weak


# ---------------------------------------------------------------------------
# k_anonymize

def test_k_anonymize_empty():
    assert vp.k_anonymize([], k=5) == []


def test_k_anonymize_invalid_k_passthrough(small_cluster):
    assert vp.k_anonymize(small_cluster, k=0) == list(small_cluster)


def test_k_anonymize_reduces_count(grid_points):
    out = vp.k_anonymize(grid_points, k=5)
    # 100 points clustered into groups of >=5 -> at most 20 cluster centroids
    assert 0 < len(out) <= len(grid_points) // 5


def test_k_anonymize_covers_all_input(grid_points):
    """No input point should be left ungrouped (leftover-merge branch)."""
    # Choose k that doesn't divide evenly to exercise leftover merge.
    out = vp.k_anonymize(grid_points, k=7)
    assert len(out) >= 1
    # Centroids must be inside the bounding box of inputs
    for cx, cy in out:
        assert 0 <= cx <= 9
        assert 0 <= cy <= 9


# ---------------------------------------------------------------------------
# grid_snap

def test_grid_snap_empty_or_invalid():
    assert vp.grid_snap([], cell_size=5) == []
    pts = [(1.0, 2.0)]
    assert vp.grid_snap(pts, cell_size=0) == pts
    assert vp.grid_snap(pts, cell_size=-1) == pts


def test_grid_snap_dedupes(grid_points):
    out = vp.grid_snap(grid_points, cell_size=5.0)
    # 10x10 unit grid snapped to cell=5 -> handful of unique cells
    assert len(out) < len(grid_points)
    # all snapped to multiples of 5
    for x, y in out:
        assert x % 5 == 0
        assert y % 5 == 0


# ---------------------------------------------------------------------------
# donut_mask

def test_donut_mask_empty():
    assert vp.donut_mask([]) == []


def test_donut_mask_displaces_within_band(small_cluster):
    out = vp.donut_mask(small_cluster, r_min=10.0, r_max=15.0, seed=99)
    assert len(out) == len(small_cluster)
    for (x, y), (xo, yo) in zip(out, small_cluster):
        d = math.hypot(x - xo, y - yo)
        # allow a tiny float epsilon on the boundary
        assert 10.0 - 1e-6 <= d <= 15.0 + 1e-6


def test_donut_mask_seed_reproducible(small_cluster):
    a = vp.donut_mask(small_cluster, r_min=2, r_max=4, seed=1)
    b = vp.donut_mask(small_cluster, r_min=2, r_max=4, seed=1)
    assert a == b


# ---------------------------------------------------------------------------
# displacement helpers

def test_mean_displacement_equal_counts():
    a = [(0.0, 0.0), (10.0, 0.0)]
    b = [(3.0, 4.0), (10.0, 0.0)]
    assert math.isclose(vp._mean_displacement(a, b), 2.5)


def test_mean_displacement_falls_back_to_nn_on_count_mismatch():
    a = [(0.0, 0.0), (10.0, 10.0), (20.0, 20.0)]
    b = [(0.0, 0.0), (20.0, 20.0)]
    # NN distances: 0, sqrt(200), 0 -> mean ~ 4.71
    got = vp._mean_displacement(a, b)
    assert math.isclose(got, math.hypot(10, 10) / 3, rel_tol=1e-6)


def test_mean_displacement_nn_empty():
    assert vp._mean_displacement_nn([], [(1.0, 1.0)]) == 0.0
    assert vp._mean_displacement_nn([(1.0, 1.0)], []) == 0.0


# ---------------------------------------------------------------------------
# optimize_privacy

def test_optimize_privacy_laplace_respects_budget(grid_points):
    res = vp.optimize_privacy(grid_points, method="laplace",
                              max_distortion=2.0, seed=1)
    assert res.method == "laplace"
    assert res.distortion <= 2.0 + 1e-3 or res.best_param >= 99  # converged on hi
    assert len(res.points) == len(grid_points)


def test_optimize_privacy_kanon(grid_points):
    res = vp.optimize_privacy(grid_points, method="k-anon",
                              max_distortion=5.0, seed=2)
    assert res.method == "k-anon"
    assert isinstance(res.best_param, int)
    assert res.best_param >= 2


def test_optimize_privacy_grid(grid_points):
    res = vp.optimize_privacy(grid_points, method="grid",
                              max_distortion=3.0)
    assert res.method == "grid"
    assert res.best_param > 0


def test_optimize_privacy_donut(grid_points):
    res = vp.optimize_privacy(grid_points, method="donut",
                              max_distortion=5.0, seed=3)
    assert res.method == "donut"
    assert res.best_param > 0


def test_optimize_privacy_unknown_method(grid_points):
    with pytest.raises(ValueError, match="Unknown method"):
        vp.optimize_privacy(grid_points, method="bogus")


# ---------------------------------------------------------------------------
# audit_privacy

def test_audit_privacy_identical_is_high_risk(small_cluster):
    """anonymized == original -> every point re-identifiable -> grade F."""
    report = vp.audit_privacy(small_cluster, list(small_cluster))
    assert report.privacy_grade == "F"
    assert report.re_id_risk == pytest.approx(1.0, abs=1e-6)
    assert report.mean_displacement == 0.0
    assert report.point_count_original == len(small_cluster)
    assert report.point_count_anonymized == len(small_cluster)


def test_audit_privacy_far_displacement_is_safe(small_cluster):
    """Shift everything by 1000 units -> nobody re-identifiable -> grade A."""
    shifted = [(x + 1000, y + 1000) for x, y in small_cluster]
    report = vp.audit_privacy(small_cluster, shifted, threshold=1.0)
    assert report.privacy_grade == "A"
    assert report.re_id_risk == 0.0
    assert report.mean_displacement > 100


def test_audit_privacy_handles_empty_inputs():
    report = vp.audit_privacy([], [])
    assert report.point_count_original == 0
    assert report.point_count_anonymized == 0
    # No re-identifications possible
    assert report.re_id_risk == 0.0


@pytest.mark.parametrize("risk_target,expected_grade", [
    (0.0, "A"),
    (0.10, "B"),
    (0.20, "C"),
    (0.40, "D"),
    (0.80, "F"),
])
def test_audit_privacy_grade_thresholds(risk_target, expected_grade):
    """Synthesize anonymized sets that hit each grade boundary."""
    n = 100
    orig = [(float(i), 0.0) for i in range(n)]
    # The first `risk_target * n` anonymized points sit exactly on originals;
    # the rest are far away. threshold=0.5 ensures only exact matches re-id.
    n_match = int(round(risk_target * n))
    anon = [(float(i), 0.0) for i in range(n_match)] + \
           [(float(i) + 10_000, 0.0) for i in range(n - n_match)]
    report = vp.audit_privacy(orig, anon, threshold=0.5)
    assert report.privacy_grade == expected_grade


# ---------------------------------------------------------------------------
# HTML report

def test_audit_html_renders(small_cluster):
    anon = vp.laplace_noise(small_cluster, epsilon=0.5, seed=4)
    report = vp.audit_privacy(small_cluster, anon)
    html = vp._audit_html(report, small_cluster, anon)
    assert "<html" in html.lower() or "<!doctype" in html.lower() or "<svg" in html.lower() or len(html) > 100
    # Grade should appear somewhere in the report body
    assert report.privacy_grade in html


# ---------------------------------------------------------------------------
# CLI

def _run_cli(*args, cwd=None):
    """Invoke the privacy CLI in a subprocess so argparse gets a fresh argv."""
    cmd = [sys.executable, "-m", "vormap_privacy", *args]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(cmd, capture_output=True, text=True,
                          cwd=cwd or repo_root, env=env, timeout=60)


def test_cli_laplace(points_file, tmp_path):
    out = tmp_path / "out.txt"
    r = _run_cli("laplace", str(points_file), "--epsilon", "1.0",
                 "--seed", "7", "-o", str(out))
    assert r.returncode == 0, r.stderr
    assert out.exists()
    loaded = vp._load_points(str(out))
    assert len(loaded) == 100


def test_cli_kanon(points_file, tmp_path):
    out = tmp_path / "out.txt"
    r = _run_cli("k-anon", str(points_file), "--k", "10", "-o", str(out))
    assert r.returncode == 0, r.stderr
    loaded = vp._load_points(str(out))
    assert 0 < len(loaded) <= 10


def test_cli_grid(points_file, tmp_path):
    out = tmp_path / "out.txt"
    r = _run_cli("grid", str(points_file), "--cell-size", "5", "-o", str(out))
    assert r.returncode == 0, r.stderr
    loaded = vp._load_points(str(out))
    assert len(loaded) < 100


def test_cli_donut(points_file, tmp_path):
    out = tmp_path / "out.txt"
    r = _run_cli("donut", str(points_file), "--rmin", "1", "--rmax", "3",
                 "--seed", "5", "-o", str(out))
    assert r.returncode == 0, r.stderr
    loaded = vp._load_points(str(out))
    assert len(loaded) == 100


def test_cli_optimize_with_json(points_file, tmp_path):
    out = tmp_path / "anon.txt"
    js = tmp_path / "result.json"
    r = _run_cli("optimize", str(points_file), "--method", "grid",
                 "--max-distortion", "3.0", "-o", str(out), "--json", str(js))
    assert r.returncode == 0, r.stderr
    assert out.exists()
    assert js.exists()
    data = json.loads(js.read_text())
    assert data["method"] == "grid"
    assert "best_param" in data and "distortion" in data


def test_cli_audit(points_file, tmp_path):
    anon = tmp_path / "anon.txt"
    pts = vp._load_points(str(points_file))
    vp._save_points(vp.laplace_noise(pts, epsilon=0.2, seed=8), str(anon))
    js = tmp_path / "audit.json"
    html = tmp_path / "audit.html"
    r = _run_cli("audit", str(points_file), str(anon),
                 "--json", str(js), "--html", str(html))
    assert r.returncode == 0, r.stderr
    assert js.exists() and html.exists()
    audit = json.loads(js.read_text())
    assert "privacy_grade" in audit
    assert audit["privacy_grade"] in {"A", "B", "C", "D", "F"}


def test_cli_help_subcommand_required():
    r = _run_cli()
    assert r.returncode != 0
    assert "required" in (r.stderr + r.stdout).lower() or "usage" in (r.stderr + r.stdout).lower()
