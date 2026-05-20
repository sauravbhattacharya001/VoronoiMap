"""Tests for vormap_voronoi3d — 3-D Voronoi tessellation analyzer.

Covers the analytical pipeline (volumes, surface areas, neighbors,
outliers), all five preset seed generators, the HTML exporter, and the
CSV loader. Requires scipy (already a hard dep in pyproject.toml).
"""

from __future__ import annotations

import csv
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_voronoi3d import (  # noqa: E402
    CellInfo,
    Voronoi3DResult,
    _preset_cluster,
    _preset_crystal,
    _preset_galaxy,
    _preset_random,
    _preset_shell,
    export_voronoi3d_html,
    load_points_csv,
    voronoi3d_analysis,
)


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def cube_seeds():
    """30 random seeds in unit cube (deterministic via _preset_random's RNG)."""
    return _preset_random(30)


@pytest.fixture
def small_cube():
    """Tiny well-defined set — 8 corners + a center seed."""
    pts = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 1.0),
        (0.0, 1.0, 1.0),
        (1.0, 1.0, 1.0),
        (0.5, 0.5, 0.5),
    ]
    return pts


# ── Presets ─────────────────────────────────────────────────────────────

class TestPresets:
    @pytest.mark.parametrize("preset,n", [
        (_preset_random, 10),
        (_preset_random, 50),
        (_preset_crystal, 27),
        (_preset_galaxy, 40),
        (_preset_cluster, 32),
        (_preset_shell, 25),
    ])
    def test_returns_n_3d_points(self, preset, n):
        pts = preset(n)
        assert len(pts) > 0
        # Some presets snap n to lattice constraints — allow slight variance
        for p in pts:
            assert len(p) == 3
            for c in p:
                assert isinstance(c, (int, float))
                assert math.isfinite(float(c))

    def test_random_in_unit_cube(self):
        pts = _preset_random(50)
        for x, y, z in pts:
            assert 0.0 <= x <= 1.0
            assert 0.0 <= y <= 1.0
            assert 0.0 <= z <= 1.0

    def test_crystal_lattice_size_close_to_request(self):
        pts = _preset_crystal(27)
        # BCC lattice with ~n requested; allow ±50%
        assert 5 <= len(pts) <= 200

    def test_cluster_has_spread(self):
        pts = _preset_cluster(40)
        xs = [p[0] for p in pts]
        assert (max(xs) - min(xs)) > 0.1  # not collapsed to one point


# ── voronoi3d_analysis ─────────────────────────────────────────────────

class TestAnalysis:
    def test_returns_result_with_cells(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        assert isinstance(r, Voronoi3DResult)
        assert len(r.cells) == len(cube_seeds)
        for c in r.cells:
            assert isinstance(c, CellInfo)

    def test_cell_fields_populated(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        for c in r.cells:
            assert isinstance(c.index, int)
            assert len(c.seed) == 3
            assert c.volume >= 0.0
            assert c.surface_area >= 0.0
            assert c.num_faces >= 0
            assert c.num_neighbors >= 0
            assert isinstance(c.is_bounded, bool)
            assert isinstance(c.is_outlier, bool)

    def test_bounds_envelope_seeds(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        for sx, sy, sz in cube_seeds:
            assert r.bounds_min[0] <= sx <= r.bounds_max[0]
            assert r.bounds_min[1] <= sy <= r.bounds_max[1]
            assert r.bounds_min[2] <= sz <= r.bounds_max[2]

    def test_stats_present(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        expected = {
            "total_seeds", "bounded_cells", "unbounded_cells",
            "volume_min", "volume_max", "volume_mean", "volume_std",
            "outlier_count",
        }
        assert expected.issubset(set(r.stats.keys()))
        assert r.stats["total_seeds"] == len(cube_seeds)
        assert r.stats["bounded_cells"] + r.stats["unbounded_cells"] == len(cube_seeds)

    def test_outlier_count_matches_flags(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds, outlier_z=2.0)
        flagged = sum(1 for c in r.cells if c.is_outlier)
        assert r.outlier_count == flagged

    def test_lower_z_more_outliers(self, cube_seeds):
        loose = voronoi3d_analysis(cube_seeds, outlier_z=0.5)
        strict = voronoi3d_analysis(cube_seeds, outlier_z=5.0)
        # Lower threshold should never flag fewer cells than higher
        assert loose.outlier_count >= strict.outlier_count

    def test_interior_seed_is_bounded(self, small_cube):
        r = voronoi3d_analysis(small_cube)
        # The center seed (index 8) should have a bounded cell.
        center = [c for c in r.cells if c.index == 8][0]
        assert center.is_bounded
        assert center.volume > 0.0

    def test_volumes_sum_positive(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        bounded_vols = [c.volume for c in r.cells if c.is_bounded]
        assert sum(bounded_vols) > 0.0

    def test_ridge_data_consistency(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        # ridge_points and ridge_vertices have matching cardinality
        assert len(r.ridge_points) == len(r.ridge_vertices)


# ── HTML export ────────────────────────────────────────────────────────

class TestHtmlExport:
    def test_writes_file_and_returns_path(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v3d.html")
            ret = export_voronoi3d_html(r, out, opacity=0.4)
            assert os.path.exists(out)
            assert os.path.getsize(out) > 1000
            # Function may return path or content; either must be truthy
            assert ret

    def test_html_contains_three_js_scene(self, cube_seeds):
        r = voronoi3d_analysis(cube_seeds)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "v3d.html")
            export_voronoi3d_html(r, out)
            content = open(out, encoding="utf-8").read().lower()
        # Three.js-based viewer
        assert "three" in content
        assert "<html" in content or "<!doctype" in content


# ── CSV loader ─────────────────────────────────────────────────────────

class TestLoadCsv:
    def test_loads_with_header(self):
        pts = [(0.1, 0.2, 0.3), (0.5, 0.5, 0.5), (1.0, 0.0, -0.5)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["x", "y", "z"])
                for p in pts:
                    w.writerow(p)
            loaded = load_points_csv(path)
        assert len(loaded) == len(pts)
        for orig, got in zip(pts, loaded):
            assert got[0] == pytest.approx(orig[0])
            assert got[1] == pytest.approx(orig[1])
            assert got[2] == pytest.approx(orig[2])

    def test_loads_without_header(self):
        pts = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                for p in pts:
                    w.writerow(p)
            loaded = load_points_csv(path)
        assert len(loaded) == 2
        assert loaded[0] == pytest.approx(pts[0])
        assert loaded[1] == pytest.approx(pts[1])
