"""Tests for vormap_referee - the spatial fairness referee.

Covers:
  * Both numpy/scipy fast path and pure-Python fallback for _grid_voronoi
    and _grid_perimeters_and_neighbors agree exactly on random inputs.
  * The full analyze() pipeline returns sensible structure and grades.
  * load_points / load_weights handle malformed CSV rows gracefully.
  * The HTML report writer produces a self-contained document.
  * The CLI exits non-zero on too-few input points.
"""

from __future__ import annotations

import csv
import json
import math
import os
import random
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import vormap_referee as ref


REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pts(n, seed=0):
    rng = random.Random(seed)
    return [(rng.uniform(0.0, 10.0), rng.uniform(0.0, 10.0)) for _ in range(n)]


# ---------------------------------------------------------------------------
# _bbox / _gini / _mean / _std
# ---------------------------------------------------------------------------

def test_bbox_includes_padding_and_all_points():
    pts = [(0.0, 0.0), (10.0, 5.0), (3.0, -2.0)]
    x0, y0, x1, y1 = ref._bbox(pts, pad=0.10)
    assert x0 < 0.0 and y0 < -2.0
    assert x1 > 10.0 and y1 > 5.0
    # Padding is symmetric on both ends.
    dx_left = -x0 - 0.0
    dx_right = x1 - 10.0
    assert dx_left == pytest.approx(dx_right)


def test_bbox_degenerate_points_does_not_divide_by_zero():
    pts = [(2.0, 2.0), (2.0, 2.0)]
    x0, y0, x1, y1 = ref._bbox(pts)
    # All four corners should be finite distinct numbers.
    assert x1 > x0
    assert y1 > y0


def test_gini_perfect_equality_is_zero():
    assert ref._gini([5.0, 5.0, 5.0, 5.0]) == pytest.approx(0.0)


def test_gini_total_inequality_high():
    # One unit holds everything: gini approaches (n-1)/n.
    values = [0.0, 0.0, 0.0, 100.0]
    g = ref._gini(values)
    assert g == pytest.approx((len(values) - 1) / len(values), abs=1e-9)


def test_gini_empty_or_zero_total():
    assert ref._gini([]) == 0.0
    assert ref._gini([0.0, 0.0, 0.0]) == 0.0


def test_mean_and_std_basic():
    assert ref._mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    # Population stddev of {1,2,3} == sqrt(2/3)
    assert ref._std([1.0, 2.0, 3.0]) == pytest.approx(math.sqrt(2.0 / 3.0))
    assert ref._mean([]) == 0
    assert ref._std([]) == 0


# ---------------------------------------------------------------------------
# _grid_voronoi: fast path vs reference path agreement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_grid_voronoi_numpy_matches_pure_python(seed):
    pts = _make_pts(random.Random(seed).randint(3, 18), seed=seed)
    bbox = ref._bbox(pts)
    res = 48  # small but non-trivial; keeps the test fast

    grid_py, areas_py, centroids_py, pc_py = ref._grid_voronoi_py(pts, bbox, res)
    grid_np, areas_np, centroids_np, pc_np = ref._grid_voronoi_np(pts, bbox, res)

    assert grid_py == grid_np, "pixel assignments must match exactly"
    assert pc_py == pc_np
    for a, b in zip(areas_py, areas_np):
        assert a == pytest.approx(b, abs=1e-12)
    for c_py, c_np in zip(centroids_py, centroids_np):
        assert c_py[0] == pytest.approx(c_np[0], abs=1e-9)
        assert c_py[1] == pytest.approx(c_np[1], abs=1e-9)


def test_grid_voronoi_dispatches_to_numpy_when_available():
    pts = _make_pts(5, seed=11)
    bbox = ref._bbox(pts)
    if ref._np is None:
        pytest.skip("numpy not installed; fast path unavailable")
    fast = ref._grid_voronoi(pts, bbox, 24)
    direct = ref._grid_voronoi_np(pts, bbox, 24)
    assert fast[0] == direct[0]
    assert fast[1] == direct[1]


def test_grid_voronoi_assignment_is_nearest_generator():
    pts = [(0.0, 0.0), (10.0, 10.0)]
    bbox = ref._bbox(pts)
    grid, areas, _, _ = ref._grid_voronoi(pts, bbox, res=30)
    # Lower-left pixel must belong to generator 0; upper-right to generator 1.
    assert grid[0][0] == 0
    assert grid[-1][-1] == 1
    # Both cells should claim positive area.
    assert areas[0] > 0 and areas[1] > 0


# ---------------------------------------------------------------------------
# _grid_perimeters_and_neighbors: numeric agreement & topology sanity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_perimeters_and_neighbors_numpy_matches_pure_python(seed):
    pts = _make_pts(random.Random(seed).randint(3, 14), seed=seed)
    bbox = ref._bbox(pts)
    res = 48
    grid, _, _, _ = ref._grid_voronoi_py(pts, bbox, res)
    sx = (bbox[2] - bbox[0]) / res
    sy = (bbox[3] - bbox[1]) / res

    perim_py, deg_py = ref._grid_perimeters_and_neighbors_py(grid, res, len(pts), sx, sy)
    perim_np, deg_np = ref._grid_perimeters_and_neighbors_np(grid, res, len(pts), sx, sy)

    for a, b in zip(perim_py, perim_np):
        assert a == pytest.approx(b, abs=1e-12)
    assert deg_py == deg_np


def test_neighbor_degree_two_generator_split_is_one():
    # Two well-separated generators: each is the other's only neighbor.
    pts = [(0.0, 0.0), (10.0, 10.0)]
    bbox = ref._bbox(pts)
    grid, _, _, _ = ref._grid_voronoi(pts, bbox, res=24)
    sx = (bbox[2] - bbox[0]) / 24
    sy = (bbox[3] - bbox[1]) / 24
    perims, degree = ref._grid_perimeters_and_neighbors(grid, 24, len(pts), sx, sy)
    assert degree == [1, 1]
    assert all(p > 0 for p in perims)


# ---------------------------------------------------------------------------
# analyze() integration
# ---------------------------------------------------------------------------

def test_analyze_returns_expected_keys_and_grade():
    pts = _make_pts(8, seed=42)
    bbox = ref._bbox(pts)
    result = ref.analyze(pts, bbox, res=64)
    for key in (
        "n", "areas", "centroids", "compactness", "access", "neighbors",
        "gini", "composite", "grade", "equity_score", "compact_score",
        "access_score", "neigh_score", "fix",
    ):
        assert key in result, f"missing key: {key}"
    assert result["n"] == 8
    assert result["grade"] in {"A", "B", "C", "D", "F"}
    assert 0.0 <= result["composite"] <= 100.0
    assert result["fix"] is None  # do_autofix=False by default


def test_analyze_with_weights_includes_weighted_block():
    pts = _make_pts(6, seed=3)
    bbox = ref._bbox(pts)
    weights = [1.0, 2.0, 1.0, 3.0, 1.0, 2.0]
    result = ref.analyze(pts, bbox, weights=weights, res=48)
    assert result["weighted"] is not None
    assert "gini" in result["weighted"]
    assert len(result["weighted"]["values"]) == len(pts)


def test_analyze_auto_fix_reports_lloyd_iteration():
    # Pathological clustering: 5 points squeezed into a corner forces a
    # high-gini partition, so one Lloyd iteration should noticeably help.
    pts = [(0.1, 0.1), (0.2, 0.15), (0.15, 0.2), (0.12, 0.18), (9.0, 9.0)]
    bbox = ref._bbox(pts)
    result = ref.analyze(pts, bbox, res=64, do_autofix=True)
    assert result["fix"] is not None
    assert "improvement" in result["fix"]
    assert len(result["fix"]["points"]) == len(pts)


def test_analyze_handles_zero_weight_without_division_error():
    pts = _make_pts(4, seed=8)
    bbox = ref._bbox(pts)
    weights = [0.0, 0.0, 0.0, 0.0]
    # Must not raise ZeroDivisionError.
    result = ref.analyze(pts, bbox, weights=weights, res=32)
    assert result["weighted"] is not None


# ---------------------------------------------------------------------------
# load_points / load_weights
# ---------------------------------------------------------------------------

def test_load_points_skips_malformed_rows(tmp_path):
    p = tmp_path / "pts.csv"
    p.write_text(
        "1.0,2.0\n"
        "not,a,number\n"          # ValueError -> skip
        "3.5,4.5,ignored\n"        # extra column OK; takes first two
        "\n"                         # empty row -> skip (len < 2)
        "9,10\n",
    )
    pts = ref.load_points(str(p))
    assert pts == [(1.0, 2.0), (3.5, 4.5), (9.0, 10.0)]


def test_load_weights_skips_non_numeric(tmp_path):
    w = tmp_path / "w.csv"
    w.write_text("1.0\nfoo\n2.5\n\n3\n")
    weights = ref.load_weights(str(w))
    assert weights == [1.0, 2.5, 3.0]


def test_file_hash_changes_with_content(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("hello")
    h1 = ref._file_hash(str(p))
    p.write_text("world")
    h2 = ref._file_hash(str(p))
    assert h1 != h2
    assert len(h1) == 32  # md5 hex digest


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def test_html_report_writes_self_contained_document(tmp_path):
    pts = _make_pts(5, seed=99)
    bbox = ref._bbox(pts)
    result = ref.analyze(pts, bbox, res=40)
    out = tmp_path / "report.html"
    ref._html_report(pts, result, str(out))
    text = out.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "Spatial Fairness" in text
    assert "<canvas" in text
    # JSON payload for the JS visualization must be embedded.
    assert "const cells=" in text


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

def test_cli_rejects_too_few_points(tmp_path):
    p = tmp_path / "small.csv"
    p.write_text("1,1\n2,2\n")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "vormap_referee.py"), str(p),
         "-o", str(tmp_path / "out.html")],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode != 0
    assert "at least 3" in (result.stderr + result.stdout)
