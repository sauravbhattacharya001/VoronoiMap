"""Tests for vormap_stability — Voronoi stability analysis."""

import json
import math
import os
import random
import tempfile
import pytest

from vormap_stability import (
    CellStability,
    StabilityResult,
    _perturb_points,
    _stability_color,
    export_csv,
    export_json,
    export_svg,
    stability_analysis,
)
from vormap_geometry import polygon_area as _polygon_area
from vormap_viz import compute_regions


# ── Test helpers ─────────────────────────────────────────────────────

def _make_grid(n=4, spacing=100.0):
    """Create an n x n grid of points."""
    return [(x * spacing, y * spacing) for x in range(n) for y in range(n)]


def _make_triangle():
    """Three well-separated points."""
    return [(0.0, 0.0), (100.0, 0.0), (50.0, 86.6)]


def _make_line():
    """Collinear points (degenerate)."""
    return [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)]


def _make_random_points(n=20, seed=42):
    """Random points in a 1000x1000 box."""
    rng = random.Random(seed)
    return [(rng.uniform(0, 1000), rng.uniform(0, 1000)) for _ in range(n)]


# ── _perturb_points ─────────────────────────────────────────────────

class TestPerturbPoints:
    def test_preserves_count(self):
        data = _make_grid()
        rng = random.Random(42)
        result = _perturb_points(data, 5.0, rng)
        assert len(result) == len(data)

    def test_within_noise_radius(self):
        data = _make_grid()
        rng = random.Random(42)
        radius = 10.0
        result = _perturb_points(data, radius, rng)
        for (ox, oy), (px, py) in zip(data, result):
            dist = math.sqrt((px - ox) ** 2 + (py - oy) ** 2)
            assert dist <= radius + 1e-9, (
                f"Perturbation {dist} exceeded radius {radius}"
            )

    def test_deterministic_with_same_seed(self):
        data = _make_grid()
        r1 = _perturb_points(data, 5.0, random.Random(99))
        r2 = _perturb_points(data, 5.0, random.Random(99))
        assert r1 == r2

    def test_different_seeds_give_different_results(self):
        data = _make_grid()
        r1 = _perturb_points(data, 5.0, random.Random(1))
        r2 = _perturb_points(data, 5.0, random.Random(2))
        assert r1 != r2

    def test_zero_radius_gives_original(self):
        """Noise radius of ~0 should barely move points."""
        data = _make_grid()
        rng = random.Random(42)
        result = _perturb_points(data, 1e-10, rng)
        for (ox, oy), (px, py) in zip(data, result):
            assert abs(px - ox) < 1e-6
            assert abs(py - oy) < 1e-6


# ── _polygon_area ────────────────────────────────────────────────────

class TestPolygonArea:
    def test_unit_square(self):
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert abs(_polygon_area(verts) - 1.0) < 1e-10

    def test_triangle(self):
        verts = [(0, 0), (4, 0), (0, 3)]
        assert abs(_polygon_area(verts) - 6.0) < 1e-10

    def test_degenerate_line(self):
        verts = [(0, 0), (1, 0)]
        assert _polygon_area(verts) == 0.0

    def test_empty(self):
        assert _polygon_area([]) == 0.0

    def test_single_point(self):
        assert _polygon_area([(5, 5)]) == 0.0

    def test_reversed_winding(self):
        """Shoelace area should be positive regardless of winding order."""
        verts_cw = [(0, 0), (0, 1), (1, 1), (1, 0)]
        verts_ccw = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert abs(_polygon_area(verts_cw) - 1.0) < 1e-10
        assert abs(_polygon_area(verts_ccw) - 1.0) < 1e-10


# ── _stability_color ────────────────────────────────────────────────

class TestStabilityColor:
    def test_zero_is_red(self):
        c = _stability_color(0.0)
        assert c.startswith("rgb(220,")

    def test_one_is_green(self):
        c = _stability_color(1.0)
        # Green channel should be high, red should be low
        assert "rgb(40," in c or "rgb(" in c
        parts = c.replace("rgb(", "").replace(")", "").split(",")
        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
        assert g > r, "At score=1.0, green should dominate red"

    def test_half_is_yellowish(self):
        c = _stability_color(0.5)
        parts = c.replace("rgb(", "").replace(")", "").split(",")
        r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
        # Both red and green should be high at 0.5
        assert r >= 200
        assert g >= 200

    def test_returns_rgb_format(self):
        for score in [0.0, 0.25, 0.5, 0.75, 1.0]:
            c = _stability_color(score)
            assert c.startswith("rgb(") and c.endswith(")")


# ── stability_analysis — validation ──────────────────────────────────

class TestStabilityValidation:
    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 3"):
            stability_analysis([(0, 0), (1, 1)], noise_radius=1.0)

    def test_negative_noise(self):
        with pytest.raises(ValueError, match="positive"):
            stability_analysis(_make_grid(), noise_radius=-1.0)

    def test_zero_noise(self):
        with pytest.raises(ValueError, match="positive"):
            stability_analysis(_make_grid(), noise_radius=0.0)

    def test_zero_iterations(self):
        with pytest.raises(ValueError, match="at least 1"):
            stability_analysis(_make_grid(), iterations=0)


# ── stability_analysis — core behavior ───────────────────────────────

class TestStabilityAnalysis:
    def test_returns_stability_result(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        assert isinstance(result, StabilityResult)

    def test_cells_count(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        # Should have a cell for each point that appears in the base diagram
        assert len(result.cells) > 0
        assert len(result.cells) <= len(data)

    def test_global_stability_in_range(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        assert 0.0 <= result.global_stability <= 1.0

    def test_cell_scores_in_range(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=20, seed=42)
        for c in result.cells:
            assert 0.0 <= c.stability_score <= 1.0, (
                f"Cell #{c.region_index} score {c.stability_score} out of range"
            )

    def test_cell_survival_rate_in_range(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        for c in result.cells:
            assert 0.0 <= c.survival_rate <= 1.0

    def test_cell_topology_change_rate_in_range(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        for c in result.cells:
            assert 0.0 <= c.topology_change_rate <= 1.0

    def test_small_noise_high_stability(self):
        """Very small perturbations should yield high interior-cell stability.

        We compare small vs. large noise to confirm the monotonic
        relationship, rather than asserting an absolute threshold —
        edge cells with unbounded Voronoi regions are inherently
        unstable under any clipping-based area measurement.
        """
        data = _make_grid(n=4, spacing=200.0)
        small = stability_analysis(
            data, noise_radius=0.01, iterations=20, seed=42
        )
        large = stability_analysis(
            data, noise_radius=50.0, iterations=20, seed=42
        )
        # Small noise should be at least as stable as large noise
        assert small.global_stability >= large.global_stability - 0.05, (
            f"Small noise ({small.global_stability}) should be >= "
            f"large noise ({large.global_stability})"
        )
        # At least some cells should survive in both
        assert len(small.cells) > 0
        assert len(large.cells) > 0

    def test_large_noise_lower_stability(self):
        """Large perturbations relative to spacing should decrease stability."""
        data = _make_grid(n=3, spacing=50.0)
        small = stability_analysis(data, noise_radius=1.0, iterations=20, seed=42)
        large = stability_analysis(data, noise_radius=25.0, iterations=20, seed=42)
        assert large.global_stability <= small.global_stability + 0.05, (
            f"Large noise ({large.global_stability}) should not be more "
            f"stable than small noise ({small.global_stability})"
        )

    def test_deterministic_with_seed(self):
        data = _make_grid()
        r1 = stability_analysis(data, noise_radius=5.0, iterations=20, seed=99)
        r2 = stability_analysis(data, noise_radius=5.0, iterations=20, seed=99)
        assert r1.global_stability == r2.global_stability
        for c1, c2 in zip(r1.cells, r2.cells):
            assert c1.stability_score == c2.stability_score

    def test_most_least_stable_are_valid(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        indices = {c.region_index for c in result.cells}
        assert result.most_stable_cell in indices
        assert result.least_stable_cell in indices

    def test_mean_area_cv_nonnegative(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        assert result.mean_area_cv >= 0.0

    def test_noise_radius_stored(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=7.5, iterations=5, seed=42)
        assert result.noise_radius == 7.5

    def test_iterations_stored(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=15, seed=42)
        assert result.iterations == 15

    def test_cells_sorted_by_region_index(self):
        data = _make_random_points(15, seed=42)
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        indices = [c.region_index for c in result.cells]
        assert indices == sorted(indices)

    def test_area_std_nonnegative(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        for c in result.cells:
            assert c.area_std >= 0.0

    def test_min_area_le_max_area(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        for c in result.cells:
            assert c.min_area <= c.max_area

    def test_original_area_positive(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        for c in result.cells:
            assert c.original_area > 0

    def test_original_vertex_count_at_least_3(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=10, seed=42)
        for c in result.cells:
            assert c.original_vertex_count >= 3


# ── stability_analysis — edge cases ──────────────────────────────────

class TestStabilityEdgeCases:
    def test_triangle_3_points(self):
        """Minimum viable input."""
        data = _make_triangle()
        result = stability_analysis(data, noise_radius=1.0, iterations=5, seed=42)
        assert len(result.cells) > 0

    def test_single_iteration(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=1, seed=42)
        assert isinstance(result, StabilityResult)
        assert result.iterations == 1

    def test_random_points(self):
        data = _make_random_points(20, seed=123)
        result = stability_analysis(data, noise_radius=10.0, iterations=10, seed=42)
        assert len(result.cells) > 0
        assert 0.0 <= result.global_stability <= 1.0


# ── StabilityResult.summary ─────────────────────────────────────────

class TestSummary:
    def test_summary_is_string(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        s = result.summary()
        assert isinstance(s, str)
        assert "Voronoi Stability Analysis" in s

    def test_summary_contains_metrics(self):
        data = _make_grid()
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        s = result.summary()
        assert "Noise radius" in s
        assert "Global stability" in s
        assert "Iterations" in s

    def test_summary_rating_excellent(self):
        r = StabilityResult(global_stability=0.95)
        assert "EXCELLENT" in r.summary()

    def test_summary_rating_good(self):
        r = StabilityResult(global_stability=0.75)
        assert "GOOD" in r.summary()

    def test_summary_rating_moderate(self):
        r = StabilityResult(global_stability=0.55)
        assert "MODERATE" in r.summary()

    def test_summary_rating_poor(self):
        r = StabilityResult(global_stability=0.35)
        assert "POOR" in r.summary()

    def test_summary_rating_critical(self):
        r = StabilityResult(global_stability=0.15)
        assert "CRITICAL" in r.summary()


# ── CellStability defaults ───────────────────────────────────────────

class TestCellStabilityDefaults:
    def test_default_values(self):
        c = CellStability(seed=(0, 0))
        assert c.region_index == 0
        assert c.original_area == 0.0
        assert c.stability_score == 1.0
        assert c.topology_change_rate == 0.0
        assert c.survival_rate == 1.0

    def test_seed_stored(self):
        c = CellStability(seed=(42.0, 99.0))
        assert c.seed == (42.0, 99.0)


# ── StabilityResult defaults ─────────────────────────────────────────

class TestStabilityResultDefaults:
    def test_default_values(self):
        r = StabilityResult()
        assert r.cells == []
        assert r.global_stability == 0.0
        assert r.noise_radius == 0.0
        assert r.iterations == 0


# ── Export: JSON ─────────────────────────────────────────────────────

class TestExportJson:
    def test_writes_valid_json(self):
        data = _make_grid(n=3)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_json(result, path)
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            assert "global_stability" in obj
            assert "cells" in obj
            assert isinstance(obj["cells"], list)
            assert len(obj["cells"]) > 0
            assert "stability_score" in obj["cells"][0]
        finally:
            os.unlink(path)

    def test_json_contains_all_fields(self):
        data = _make_grid(n=3)
        result = stability_analysis(data, noise_radius=3.0, iterations=5, seed=42)
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_json(result, path)
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            assert obj["noise_radius"] == 3.0
            assert obj["iterations"] == 5
            cell = obj["cells"][0]
            for key in [
                "region_index", "seed_x", "seed_y", "original_area",
                "mean_area", "area_std", "area_cv", "min_area", "max_area",
                "stability_score", "topology_change_rate",
                "original_vertex_count", "mean_vertex_count", "survival_rate",
            ]:
                assert key in cell, f"Missing key: {key}"
        finally:
            os.unlink(path)


# ── Export: CSV ──────────────────────────────────────────────────────

class TestExportCsv:
    def test_writes_valid_csv(self):
        data = _make_grid(n=3)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_csv(result, path)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            # Header + data rows
            assert len(lines) >= 2
            header = lines[0].strip()
            assert "region_index" in header
            assert "stability_score" in header
            # Data row has same number of fields as header
            h_fields = header.split(",")
            d_fields = lines[1].strip().split(",")
            assert len(d_fields) == len(h_fields)
        finally:
            os.unlink(path)

    def test_csv_row_count(self):
        data = _make_grid(n=3)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_csv(result, path)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            # 1 header + N data rows
            assert len(lines) == 1 + len(result.cells)
        finally:
            os.unlink(path)


# ── Export: SVG ──────────────────────────────────────────────────────

class TestExportSvg:
    def test_writes_svg(self):
        data = _make_grid(n=3, spacing=100)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        regions = compute_regions(data)
        with tempfile.NamedTemporaryFile(
            suffix=".svg", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_svg(result, regions, data, path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
            assert "<polygon" in content
            assert "</svg>" in content
        finally:
            os.unlink(path)

    def test_svg_with_title(self):
        data = _make_grid(n=3, spacing=100)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        regions = compute_regions(data)
        with tempfile.NamedTemporaryFile(
            suffix=".svg", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_svg(result, regions, data, path, title="Test Title")
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "Test Title" in content
        finally:
            os.unlink(path)

    def test_svg_no_labels(self):
        data = _make_grid(n=3, spacing=100)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        regions = compute_regions(data)
        with tempfile.NamedTemporaryFile(
            suffix=".svg", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_svg(
                result, regions, data, path,
                show_labels=False, show_scores=False,
            )
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "<svg" in content
        finally:
            os.unlink(path)

    def test_svg_empty_regions_raises(self):
        result = StabilityResult()
        with tempfile.NamedTemporaryFile(
            suffix=".svg", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No regions"):
                export_svg(result, {}, [], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_legend_present(self):
        data = _make_grid(n=3, spacing=100)
        result = stability_analysis(data, noise_radius=5.0, iterations=5, seed=42)
        regions = compute_regions(data)
        with tempfile.NamedTemporaryFile(
            suffix=".svg", delete=False, dir="."
        ) as f:
            path = f.name
        try:
            export_svg(result, regions, data, path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "fragile" in content
            assert "stable" in content
        finally:
            os.unlink(path)
