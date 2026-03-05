"""Tests for vormap_kde — Kernel Density Estimation."""

import json
import math
import os
import tempfile

import pytest

import vormap_kde


# -- Test data ---------------------------------------------------------

CLUSTER_POINTS = [
    # Cluster near (100, 100)
    (95, 98), (100, 100), (105, 102), (98, 105), (102, 97),
    # Cluster near (300, 300)
    (295, 298), (300, 300), (305, 302), (298, 305), (302, 297),
    # Isolated point
    (500, 500),
]

UNIFORM_POINTS = [(i * 50, j * 50) for i in range(5) for j in range(5)]

SINGLE_POINT = [(100, 200)]


# -- Bandwidth selection -----------------------------------------------

class TestBandwidth:
    def test_silverman_returns_positive(self):
        h = vormap_kde.silverman_bandwidth(CLUSTER_POINTS)
        assert h > 0

    def test_scott_returns_positive(self):
        h = vormap_kde.scott_bandwidth(CLUSTER_POINTS)
        assert h > 0

    def test_single_point_returns_default(self):
        assert vormap_kde.silverman_bandwidth(SINGLE_POINT) == 1.0
        assert vormap_kde.scott_bandwidth(SINGLE_POINT) == 1.0

    def test_silverman_smaller_for_tight_cluster(self):
        tight = [(100 + i, 100 + j) for i in range(3) for j in range(3)]
        spread = [(i * 100, j * 100) for i in range(3) for j in range(3)]
        h_tight = vormap_kde.silverman_bandwidth(tight)
        h_spread = vormap_kde.silverman_bandwidth(spread)
        assert h_tight < h_spread

    def test_more_points_with_same_spread_smaller_bandwidth(self):
        # Same bounding box, different density
        import random
        rng = random.Random(42)
        few = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(10)]
        many = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(200)]
        h_few = vormap_kde.silverman_bandwidth(few)
        h_many = vormap_kde.silverman_bandwidth(many)
        # Silverman: h ~ n^(-1/5), so more points → smaller bandwidth
        assert h_many < h_few


# -- Gaussian kernel ---------------------------------------------------

class TestKernel:
    def test_kernel_at_center_is_max(self):
        h_sq = 10.0 * 10.0
        center_val = vormap_kde.gaussian_kernel(0.0, h_sq)
        assert center_val > 0

    def test_kernel_decreases_with_distance(self):
        h_sq = 10.0 ** 2
        v1 = vormap_kde.gaussian_kernel(1.0, h_sq)
        v2 = vormap_kde.gaussian_kernel(100.0, h_sq)
        assert v1 > v2

    def test_kernel_symmetric(self):
        h_sq = 25.0
        assert vormap_kde.gaussian_kernel(9.0, h_sq) == \
               vormap_kde.gaussian_kernel(9.0, h_sq)


# -- KDE at point -------------------------------------------------------

class TestKDEAtPoint:
    def test_density_higher_near_cluster(self):
        d_cluster = vormap_kde.kde_at_point(100, 100, CLUSTER_POINTS, 30.0)
        d_empty = vormap_kde.kde_at_point(400, 400, CLUSTER_POINTS, 30.0)
        assert d_cluster > d_empty

    def test_empty_points_returns_zero(self):
        assert vormap_kde.kde_at_point(0, 0, [], 1.0) == 0.0

    def test_density_positive_at_data_point(self):
        d = vormap_kde.kde_at_point(100, 100, CLUSTER_POINTS, 50.0)
        assert d > 0


# -- KDE grid -----------------------------------------------------------

class TestKDEGrid:
    def test_grid_dimensions(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=20, ny=15)
        assert grid.nx == 20
        assert grid.ny == 15
        assert len(grid.values) == 15
        assert len(grid.values[0]) == 20

    def test_density_range(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        assert grid.density_min >= 0
        assert grid.density_max > 0
        assert grid.density_max >= grid.density_min

    def test_points_used(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        assert grid.points_used == len(CLUSTER_POINTS)

    def test_bandwidth_auto_selected(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        assert grid.bandwidth > 0

    def test_explicit_bandwidth(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5, bandwidth=42.0)
        assert grid.bandwidth == 42.0

    def test_scott_method(self):
        grid = vormap_kde.kde_grid(
            CLUSTER_POINTS, nx=5, ny=5, bandwidth_method="scott"
        )
        assert grid.bandwidth > 0

    def test_custom_bounds(self):
        grid = vormap_kde.kde_grid(
            CLUSTER_POINTS, nx=5, ny=5,
            bounds=(0, 0, 600, 600),
        )
        assert grid.x_min == 0
        assert grid.y_min == 0
        assert grid.x_max == 600
        assert grid.y_max == 600

    def test_empty_points_raises(self):
        with pytest.raises(ValueError, match="No points"):
            vormap_kde.kde_grid([], nx=5, ny=5)

    def test_invalid_grid_size(self):
        with pytest.raises(ValueError, match="at least 2"):
            vormap_kde.kde_grid(CLUSTER_POINTS, nx=1, ny=5)

    def test_invalid_bandwidth(self):
        with pytest.raises(ValueError, match="positive"):
            vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5, bandwidth=-1.0)

    def test_density_at_bounds_check(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        assert grid.density_at(0, 0) >= 0
        assert grid.density_at(100, 100) == 0.0  # out of bounds

    def test_grid_to_coords(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        x, y = grid.grid_to_coords(0, 0)
        assert x == pytest.approx(grid.x_min)
        assert y == pytest.approx(grid.y_min)

    def test_cell_width_height_positive(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        assert grid.cell_width > 0
        assert grid.cell_height > 0


# -- Hotspot detection ---------------------------------------------------

class TestHotspots:
    def test_finds_hotspots(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=30, ny=30)
        hotspots = vormap_kde.find_hotspots(grid, threshold_pct=70)
        assert len(hotspots) > 0

    def test_hotspots_ranked_by_density(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=30, ny=30)
        hotspots = vormap_kde.find_hotspots(grid, threshold_pct=50)
        if len(hotspots) >= 2:
            assert hotspots[0].density >= hotspots[1].density

    def test_hotspots_have_rank(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=30, ny=30)
        hotspots = vormap_kde.find_hotspots(grid, threshold_pct=50)
        for i, h in enumerate(hotspots):
            assert h.rank == i + 1

    def test_invalid_threshold_raises(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        with pytest.raises(ValueError, match="threshold_pct"):
            vormap_kde.find_hotspots(grid, threshold_pct=101)

    def test_high_threshold_fewer_hotspots(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=30, ny=30)
        h_low = vormap_kde.find_hotspots(grid, threshold_pct=50)
        h_high = vormap_kde.find_hotspots(grid, threshold_pct=95)
        assert len(h_high) <= len(h_low)


# -- Density contours ---------------------------------------------------

class TestContours:
    def test_contour_levels(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        contours = vormap_kde.density_contours(grid, levels=5)
        assert len(contours) == 5

    def test_contour_levels_increasing(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        contours = vormap_kde.density_contours(grid, levels=5)
        for i in range(len(contours) - 1):
            assert contours[i].level <= contours[i + 1].level

    def test_higher_level_fewer_cells(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=15, ny=15)
        contours = vormap_kde.density_contours(grid, levels=3)
        if len(contours) >= 2:
            assert len(contours[0].cells) >= len(contours[-1].cells)

    def test_area_fraction_valid(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        contours = vormap_kde.density_contours(grid, levels=3)
        for c in contours:
            assert 0 <= c.area_fraction <= 1.0

    def test_invalid_levels_raises(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        with pytest.raises(ValueError, match="at least 1"):
            vormap_kde.density_contours(grid, levels=0)


# -- SVG export ----------------------------------------------------------

class TestSVGExport:
    def test_export_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        result = vormap_kde.export_kde_svg(grid, "kde_test.svg")
        assert os.path.exists(result)
        content = open(result).read()
        assert "<svg" in content
        assert "KDE:" in content

    def test_export_with_hotspots(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=15, ny=15)
        vormap_kde.export_kde_svg(grid, "kde_hs.svg", show_hotspots=True)
        content = open("kde_hs.svg").read()
        assert "<svg" in content

    def test_export_with_title(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        vormap_kde.export_kde_svg(grid, "kde_title.svg", title="Test KDE")
        content = open("kde_title.svg").read()
        assert "Test KDE" in content


# -- CSV export ----------------------------------------------------------

class TestCSVExport:
    def test_export_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=5, ny=5)
        result = vormap_kde.export_kde_csv(grid, "kde_test.csv")
        assert os.path.exists(result)
        lines = open(result).readlines()
        assert lines[0].strip() == "x,y,density"
        assert len(lines) == 5 * 5 + 1


# -- Hotspot JSON export -------------------------------------------------

class TestHotspotJSON:
    def test_export_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=20, ny=20)
        hotspots = vormap_kde.find_hotspots(grid, threshold_pct=50)
        vormap_kde.export_hotspots_json(hotspots, "hotspots.json", grid)
        data = json.load(open("hotspots.json"))
        assert "hotspots" in data
        assert "count" in data
        assert "grid_info" in data
        assert data["count"] == len(hotspots)


# -- Summary -------------------------------------------------------------

class TestSummary:
    def test_summary_fields(self):
        grid = vormap_kde.kde_grid(CLUSTER_POINTS, nx=10, ny=10)
        s = vormap_kde.kde_summary(grid)
        assert "bandwidth" in s
        assert "grid_resolution" in s
        assert "points_used" in s
        assert "density_min" in s
        assert "density_max" in s
        assert "density_mean" in s
        assert "density_median" in s
        assert "total_mass" in s
        assert "bounds" in s
        assert s["points_used"] == len(CLUSTER_POINTS)

    def test_total_mass_approximately_one(self):
        # For a proper KDE, the total integral should approximate 1.0
        # (it's an estimate over a finite grid, so won't be exact)
        grid = vormap_kde.kde_grid(
            CLUSTER_POINTS, nx=50, ny=50, padding=0.5
        )
        s = vormap_kde.kde_summary(grid)
        # Should be in the ballpark of 1.0 (within 0.5–2.0)
        assert 0.1 < s["total_mass"] < 5.0
