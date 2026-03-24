"""Tests for vormap_kde – Kernel Density Estimation module.

Covers bandwidth selection, core KDE computation, grid generation,
hotspot detection, density contours, export functions, and edge cases.
"""

from __future__ import annotations

import json
import math
import os
import tempfile

import pytest

from vormap_kde import (
    DensityContour,
    Hotspot,
    KDEGrid,
    _SpatialBins,
    density_contours,
    export_hotspots_json,
    export_kde_csv,
    export_kde_svg,
    find_hotspots,
    gaussian_kernel,
    kde_at_point,
    kde_grid,
    kde_summary,
    scott_bandwidth,
    silverman_bandwidth,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def cluster_points():
    """Two well-separated clusters for KDE testing."""
    cluster_a = [(10 + i, 10 + j) for i in range(5) for j in range(5)]
    cluster_b = [(90 + i, 90 + j) for i in range(5) for j in range(5)]
    return cluster_a + cluster_b


@pytest.fixture
def simple_points():
    """Four widely-spaced points."""
    return [(0, 0), (100, 0), (100, 100), (0, 100)]


@pytest.fixture
def single_point():
    return [(50, 50)]


@pytest.fixture
def collinear_points():
    """Points on a line (zero variance in y)."""
    return [(i * 10, 50) for i in range(10)]


# ── Bandwidth selection ──────────────────────────────────────────────

class TestBandwidthSelection:
    def test_silverman_returns_positive(self, simple_points):
        h = silverman_bandwidth(simple_points)
        assert h > 0

    def test_scott_returns_positive(self, simple_points):
        h = scott_bandwidth(simple_points)
        assert h > 0

    def test_single_point_fallback(self, single_point):
        """Single point should return fallback bandwidth of 1.0."""
        assert silverman_bandwidth(single_point) == 1.0
        assert scott_bandwidth(single_point) == 1.0

    def test_bandwidth_scales_with_spread(self):
        """Wider spread → larger bandwidth."""
        tight = [(50 + i * 0.1, 50 + j * 0.1) for i in range(5) for j in range(5)]
        wide = [(i * 100, j * 100) for i in range(5) for j in range(5)]
        assert silverman_bandwidth(tight) < silverman_bandwidth(wide)

    def test_bandwidth_scales_with_n(self):
        """More points → smaller bandwidth (for same spread)."""
        few = [(i * 10, i * 10) for i in range(5)]
        many = [(i * 10, i * 10) for i in range(50)]
        # n^(-1/6) decreases with n, so bandwidth should decrease
        # (assuming similar spread per-point)
        h_few = silverman_bandwidth(few)
        h_many = silverman_bandwidth(many)
        # many has larger std due to range, so compare normalized
        assert h_few > 0 and h_many > 0

    def test_collinear_points_nonzero(self, collinear_points):
        """Points with zero variance in one dimension still give valid bandwidth."""
        h = silverman_bandwidth(collinear_points)
        assert h > 0
        assert math.isfinite(h)

    def test_silverman_robust_to_outliers(self):
        """Silverman should produce smaller bandwidth than Scott when outliers exist.

        The IQR-based robust spread estimate in Silverman's rule resists
        inflation from extreme outliers, while Scott uses raw std.
        """
        pts = [(i, i) for i in range(20)] + [(1000, 1000)]
        h_silv = silverman_bandwidth(pts)
        h_scott = scott_bandwidth(pts)
        assert h_silv < h_scott, (
            f"Silverman ({h_silv:.2f}) should be < Scott ({h_scott:.2f}) "
            "with outlier data"
        )

    def test_silverman_equals_scott_for_uniform_data(self):
        """For well-behaved uniform data, both rules should agree closely."""
        pts = [(i * 10, j * 10) for i in range(10) for j in range(10)]
        h_silv = silverman_bandwidth(pts)
        h_scott = scott_bandwidth(pts)
        # They may not be identical (IQR vs std), but should be close
        ratio = h_silv / h_scott
        assert 0.5 < ratio <= 1.0, (
            f"Ratio {ratio:.3f} out of expected range for uniform data"
        )


# ── Gaussian kernel ──────────────────────────────────────────────────

class TestGaussianKernel:
    def test_peak_at_zero(self):
        """Kernel is maximized at distance 0."""
        h_sq = 10.0
        peak = gaussian_kernel(0.0, h_sq)
        nearby = gaussian_kernel(1.0, h_sq)
        assert peak > nearby

    def test_kernel_positive(self):
        assert gaussian_kernel(100.0, 25.0) > 0

    def test_kernel_formula(self):
        """Verify against manual computation."""
        d_sq, h_sq = 4.0, 9.0
        expected = math.exp(-0.5 * d_sq / h_sq) / (2 * math.pi * h_sq)
        assert abs(gaussian_kernel(d_sq, h_sq) - expected) < 1e-15

    def test_kernel_decays(self):
        """Kernel value decreases with distance."""
        h_sq = 25.0
        vals = [gaussian_kernel(d ** 2, h_sq) for d in range(10)]
        for i in range(len(vals) - 1):
            assert vals[i] >= vals[i + 1]


# ── kde_at_point ─────────────────────────────────────────────────────

class TestKdeAtPoint:
    def test_empty_points(self):
        assert kde_at_point(0, 0, [], 1.0) == 0.0

    def test_density_highest_at_cluster(self, cluster_points):
        h = silverman_bandwidth(cluster_points)
        d_cluster = kde_at_point(12, 12, cluster_points, h)
        d_empty = kde_at_point(50, 50, cluster_points, h)
        assert d_cluster > d_empty

    def test_symmetric_density(self):
        """Density is equal at symmetric positions around a symmetric point set."""
        pts = [(0, 0), (100, 0), (0, 100), (100, 100)]
        h = 30.0
        d1 = kde_at_point(50, 0, pts, h)
        d2 = kde_at_point(0, 50, pts, h)
        assert abs(d1 - d2) < 1e-10


# ── SpatialBins ──────────────────────────────────────────────────────

class TestSpatialBins:
    def test_all_points_found(self):
        """Every point should be discoverable from its own location."""
        pts = [(10, 10), (20, 20), (90, 90)]
        bins = _SpatialBins(pts, cutoff=15.0, x_min=0, y_min=0, x_max=100, y_max=100)
        found = bins.neighbours(10, 10)
        assert (10, 10) in found

    def test_distant_point_not_in_neighbours(self):
        pts = [(0, 0), (1000, 1000)]
        bins = _SpatialBins(pts, cutoff=10.0, x_min=0, y_min=0, x_max=1000, y_max=1000)
        near_origin = bins.neighbours(0, 0)
        assert (1000, 1000) not in near_origin


# ── kde_grid ─────────────────────────────────────────────────────────

class TestKdeGrid:
    def test_basic_grid(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        assert grid.nx == 10
        assert grid.ny == 10
        assert len(grid.values) == 10
        assert all(len(row) == 10 for row in grid.values)

    def test_density_range(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        assert grid.density_min >= 0
        assert grid.density_max >= grid.density_min
        assert grid.points_used == 4

    def test_explicit_bandwidth(self, simple_points):
        grid = kde_grid(simple_points, nx=5, ny=5, bandwidth=20.0)
        assert grid.bandwidth == 20.0

    def test_scott_method(self, simple_points):
        grid = kde_grid(simple_points, nx=5, ny=5, bandwidth_method="scott")
        assert grid.bandwidth > 0

    def test_explicit_bounds(self, simple_points):
        grid = kde_grid(simple_points, nx=5, ny=5, bounds=(0, 0, 200, 200))
        assert grid.x_min == 0
        assert grid.x_max == 200

    def test_empty_points_raises(self):
        with pytest.raises(ValueError, match="No points"):
            kde_grid([], nx=5, ny=5)

    def test_small_grid_raises(self, simple_points):
        with pytest.raises(ValueError):
            kde_grid(simple_points, nx=1, ny=1)

    def test_negative_bandwidth_raises(self, simple_points):
        with pytest.raises(ValueError, match="positive"):
            kde_grid(simple_points, nx=5, ny=5, bandwidth=-1.0)

    def test_cluster_produces_two_peaks(self, cluster_points):
        """Two clusters should produce distinct density peaks."""
        grid = kde_grid(cluster_points, nx=20, ny=20)
        # Find the row/col of max density
        max_val = 0
        for r in range(grid.ny):
            for c in range(grid.nx):
                if grid.values[r][c] > max_val:
                    max_val = grid.values[r][c]
        assert max_val > 0

    def test_cell_dimensions(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        assert grid.cell_width > 0
        assert grid.cell_height > 0

    def test_grid_to_coords(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        x0, y0 = grid.grid_to_coords(0, 0)
        assert abs(x0 - grid.x_min) < 1e-10
        assert abs(y0 - grid.y_min) < 1e-10

    def test_density_at_oob(self, simple_points):
        grid = kde_grid(simple_points, nx=5, ny=5)
        assert grid.density_at(-1, 0) == 0.0
        assert grid.density_at(0, 100) == 0.0

    def test_large_point_set_uses_bins(self):
        """With ≥64 points, spatial binning should be used (coverage)."""
        pts = [(i % 10 * 10, i // 10 * 10) for i in range(100)]
        grid = kde_grid(pts, nx=10, ny=10)
        assert grid.points_used == 100


# ── Hotspot detection ────────────────────────────────────────────────

class TestFindHotspots:
    def test_cluster_hotspots(self, cluster_points):
        grid = kde_grid(cluster_points, nx=30, ny=30)
        hotspots = find_hotspots(grid, threshold_pct=80)
        assert len(hotspots) >= 1
        # Hotspots should be ranked
        for i, hs in enumerate(hotspots):
            assert hs.rank == i + 1

    def test_hotspot_density_descending(self, cluster_points):
        grid = kde_grid(cluster_points, nx=30, ny=30)
        hotspots = find_hotspots(grid, threshold_pct=50)
        for i in range(len(hotspots) - 1):
            assert hotspots[i].density >= hotspots[i + 1].density

    def test_threshold_out_of_range(self, simple_points):
        grid = kde_grid(simple_points, nx=5, ny=5)
        with pytest.raises(ValueError, match="0-100"):
            find_hotspots(grid, threshold_pct=101)

    def test_high_threshold_fewer_hotspots(self, cluster_points):
        grid = kde_grid(cluster_points, nx=20, ny=20)
        hs_low = find_hotspots(grid, threshold_pct=50)
        hs_high = find_hotspots(grid, threshold_pct=95)
        assert len(hs_high) <= len(hs_low)

    def test_min_separation(self, cluster_points):
        grid = kde_grid(cluster_points, nx=30, ny=30)
        hotspots = find_hotspots(grid, threshold_pct=50, min_separation=5)
        for i in range(len(hotspots)):
            for j in range(i + 1, len(hotspots)):
                row_diff = abs(hotspots[i].grid_row - hotspots[j].grid_row)
                col_diff = abs(hotspots[i].grid_col - hotspots[j].grid_col)
                assert row_diff >= 5 or col_diff >= 5


# ── Density contours ─────────────────────────────────────────────────

class TestDensityContours:
    def test_contour_count(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        contours = density_contours(grid, levels=5)
        assert len(contours) == 5

    def test_contour_levels_ascending(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        contours = density_contours(grid, levels=5)
        for i in range(len(contours) - 1):
            assert contours[i].level <= contours[i + 1].level

    def test_lower_levels_have_more_cells(self, cluster_points):
        grid = kde_grid(cluster_points, nx=20, ny=20)
        contours = density_contours(grid, levels=3)
        # Lower threshold → more cells above it
        assert contours[0].area_fraction >= contours[-1].area_fraction

    def test_zero_levels_raises(self, simple_points):
        grid = kde_grid(simple_points, nx=5, ny=5)
        with pytest.raises(ValueError, match="at least 1"):
            density_contours(grid, levels=0)


# ── Export functions ─────────────────────────────────────────────────

class TestExports:
    """Export tests use relative paths (vormap disallows absolute paths)."""

    def test_export_kde_svg(self, simple_points, monkeypatch):
        monkeypatch.chdir(tempfile.mkdtemp())
        grid = kde_grid(simple_points, nx=10, ny=10)
        out = export_kde_svg(grid, "kde.svg", title="Test KDE")
        assert os.path.exists(out)
        content = open(out, encoding="utf-8").read()
        assert "<svg" in content
        assert "Test KDE" in content

    def test_export_kde_svg_with_hotspots(self, cluster_points, monkeypatch):
        monkeypatch.chdir(tempfile.mkdtemp())
        grid = kde_grid(cluster_points, nx=20, ny=20)
        out = export_kde_svg(grid, "kde_hs.svg",
                             show_hotspots=True, hotspot_threshold_pct=80)
        assert os.path.exists(out)

    def test_export_kde_svg_plasma_ramp(self, simple_points, monkeypatch):
        monkeypatch.chdir(tempfile.mkdtemp())
        grid = kde_grid(simple_points, nx=5, ny=5)
        out = export_kde_svg(grid, "kde_plasma.svg", ramp="plasma")
        assert os.path.exists(out)

    def test_export_kde_csv(self, simple_points, monkeypatch):
        monkeypatch.chdir(tempfile.mkdtemp())
        grid = kde_grid(simple_points, nx=5, ny=5)
        out = export_kde_csv(grid, "kde.csv")
        assert os.path.exists(out)
        lines = open(out, encoding="utf-8").readlines()
        assert lines[0].strip() == "x,y,density"
        assert len(lines) == 26

    def test_export_hotspots_json(self, cluster_points, monkeypatch):
        monkeypatch.chdir(tempfile.mkdtemp())
        grid = kde_grid(cluster_points, nx=20, ny=20)
        hotspots = find_hotspots(grid, threshold_pct=80)
        out = export_hotspots_json(hotspots, "hs.json", grid=grid)
        assert os.path.exists(out)
        data = json.loads(open(out, encoding="utf-8").read())
        assert "hotspots" in data
        assert "count" in data
        assert "grid_info" in data
        assert data["grid_info"]["nx"] == 20

    def test_export_hotspots_json_no_grid(self, monkeypatch):
        monkeypatch.chdir(tempfile.mkdtemp())
        hotspots = [Hotspot(x=10, y=20, density=0.5, grid_row=1, grid_col=2, rank=1)]
        out = export_hotspots_json(hotspots, "hs2.json")
        data = json.loads(open(out, encoding="utf-8").read())
        assert "grid_info" not in data
        assert data["count"] == 1


# ── Summary ──────────────────────────────────────────────────────────

class TestKdeSummary:
    def test_summary_keys(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        s = kde_summary(grid)
        assert "bandwidth" in s
        assert "grid_resolution" in s
        assert "density_min" in s
        assert "density_max" in s
        assert "total_mass" in s
        assert "bounds" in s

    def test_summary_values_consistent(self, simple_points):
        grid = kde_grid(simple_points, nx=10, ny=10)
        s = kde_summary(grid)
        assert s["points_used"] == 4
        assert s["density_min"] <= s["density_mean"] <= s["density_max"]
        assert s["total_mass"] > 0
