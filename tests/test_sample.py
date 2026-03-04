"""Tests for vormap_sample — Voronoi-based spatial sampling."""

import math
import os
import sys
import tempfile
import json
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vormap_sample import (
    SpatialSampler, SamplePoint, SamplePlan,
    _point_in_polygon, _polygon_bbox, _random_point_in_polygon
)


# ── Test fixtures ────────────────────────────────────────────────

def make_square(cx, cy, size):
    """Create a square polygon centered at (cx, cy)."""
    h = size / 2
    return [(cx - h, cy - h), (cx + h, cy - h),
            (cx + h, cy + h), (cx - h, cy + h)]


def make_test_regions():
    """Create 4 square regions in a 2x2 grid (total 200x200)."""
    regions = [
        make_square(50, 50, 100),    # bottom-left
        make_square(150, 50, 100),   # bottom-right
        make_square(50, 150, 100),   # top-left
        make_square(150, 150, 100),  # top-right
    ]
    seeds = [(50, 50), (150, 50), (50, 150), (150, 150)]
    return seeds, regions


def make_unequal_regions():
    """Create regions of different sizes for allocation testing."""
    regions = [
        make_square(50, 50, 100),    # area = 10000
        make_square(200, 50, 200),   # area = 40000
        make_square(50, 250, 50),    # area = 2500
    ]
    seeds = [(50, 50), (200, 50), (50, 250)]
    return seeds, regions


# ── _point_in_polygon ────────────────────────────────────────────

class TestPointInPolygon:
    def test_inside_square(self):
        sq = make_square(0, 0, 10)
        assert _point_in_polygon(0, 0, sq) is True

    def test_outside_square(self):
        sq = make_square(0, 0, 10)
        assert _point_in_polygon(20, 20, sq) is False

    def test_near_edge(self):
        sq = make_square(0, 0, 10)
        assert _point_in_polygon(4.9, 0, sq) is True

    def test_outside_by_margin(self):
        sq = make_square(0, 0, 10)
        assert _point_in_polygon(6, 0, sq) is False

    def test_triangle(self):
        tri = [(0, 0), (10, 0), (5, 10)]
        assert _point_in_polygon(5, 3, tri) is True
        assert _point_in_polygon(0, 10, tri) is False


# ── _polygon_bbox ────────────────────────────────────────────────

class TestPolygonBbox:
    def test_square(self):
        sq = make_square(5, 5, 10)
        x_min, x_max, y_min, y_max = _polygon_bbox(sq)
        assert x_min == 0
        assert x_max == 10
        assert y_min == 0
        assert y_max == 10


# ── _random_point_in_polygon ────────────────────────────────────

class TestRandomPointInPolygon:
    def test_generates_inside(self):
        import random
        sq = make_square(50, 50, 100)
        rng = random.Random(42)
        for _ in range(20):
            pt = _random_point_in_polygon(sq, rng)
            assert _point_in_polygon(pt[0], pt[1], sq)

    def test_raises_for_degenerate(self):
        import random
        line = [(0, 0), (10, 0), (10, 0)]  # degenerate
        rng = random.Random(42)
        with pytest.raises(RuntimeError, match="degenerate"):
            _random_point_in_polygon(line, rng, max_attempts=10)


# ── SpatialSampler init ─────────────────────────────────────────

class TestSamplerInit:
    def test_basic(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        assert sampler.n_regions == 4
        assert len(sampler.labels) == 4

    def test_custom_labels(self):
        seeds, regions = make_test_regions()
        labels = ["A", "B", "C", "D"]
        sampler = SpatialSampler(seeds, regions, labels=labels)
        assert sampler.labels == labels

    def test_mismatched_lengths(self):
        seeds = [(0, 0)]
        regions = [make_square(0, 0, 10), make_square(5, 5, 10)]
        with pytest.raises(ValueError, match="same length"):
            SpatialSampler(seeds, regions)

    def test_areas_lazy(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        assert sampler._areas is None
        areas = sampler.areas
        assert len(areas) == 4
        assert all(a > 0 for a in areas)

    def test_centroids_lazy(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        assert sampler._centroids is None
        centroids = sampler.centroids
        assert len(centroids) == 4


# ── stratified_random ────────────────────────────────────────────

class TestStratifiedRandom:
    def test_equal_allocation(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=5, seed=42)
        assert plan.n_samples == 20
        assert plan.method == "stratified_random"
        by_r = plan.by_region()
        assert all(len(by_r[i]) == 5 for i in range(4))

    def test_proportional_allocation(self):
        seeds, regions = make_unequal_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(total_samples=100,
                                         allocation="proportional", seed=42)
        by_r = plan.by_region()
        # Largest region (index 1, area 40000) should get the most
        assert len(by_r.get(1, [])) > len(by_r.get(2, []))

    def test_total_samples_correct(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(total_samples=50, seed=42)
        assert plan.n_samples == 50

    def test_reproducible(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan1 = sampler.stratified_random(samples_per_region=3, seed=99)
        plan2 = sampler.stratified_random(samples_per_region=3, seed=99)
        pts1 = plan1.points()
        pts2 = plan2.points()
        assert pts1 == pts2

    def test_error_both_params(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match="only one"):
            sampler.stratified_random(samples_per_region=5, total_samples=20)

    def test_error_no_params(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match="Specify either"):
            sampler.stratified_random()

    def test_points_inside_regions(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=10, seed=42)
        for s in plan.samples:
            assert _point_in_polygon(s.x, s.y, regions[s.region_index])


# ── systematic_grid ──────────────────────────────────────────────

class TestSystematicGrid:
    def test_generates_grid(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.systematic_grid(spacing=25, seed=42)
        assert plan.n_samples > 0
        assert plan.method == "systematic_grid"

    def test_all_points_in_regions(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.systematic_grid(spacing=30, seed=42)
        for s in plan.samples:
            assert _point_in_polygon(s.x, s.y, regions[s.region_index])

    def test_invalid_spacing(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match="positive"):
            sampler.systematic_grid(spacing=0)

    def test_finer_grid_more_samples(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        coarse = sampler.systematic_grid(spacing=50, seed=42)
        fine = sampler.systematic_grid(spacing=20, seed=42)
        assert fine.n_samples > coarse.n_samples


# ── centroid_based ───────────────────────────────────────────────

class TestCentroidBased:
    def test_no_jitter(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.centroid_based(jitter=0)
        assert plan.n_samples == 4
        for s in plan.samples:
            centroid = sampler.centroids[s.region_index]
            assert abs(s.x - centroid[0]) < 1e-6
            assert abs(s.y - centroid[1]) < 1e-6

    def test_with_jitter(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.centroid_based(jitter=10, seed=42)
        assert plan.n_samples == 4
        # Points should be near but not exactly at centroids
        for s in plan.samples:
            centroid = sampler.centroids[s.region_index]
            dist = math.sqrt((s.x - centroid[0])**2 + (s.y - centroid[1])**2)
            assert dist <= 10 * math.sqrt(2) + 1e-6

    def test_negative_jitter(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match="non-negative"):
            sampler.centroid_based(jitter=-1)


# ── boundary_focused ─────────────────────────────────────────────

class TestBoundaryFocused:
    def test_generates_samples(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.boundary_focused(samples_per_edge=2, seed=42)
        # 4 regions × 4 edges × 2 samples = 32
        assert plan.n_samples == 32
        assert plan.method == "boundary_focused"

    def test_invalid_samples_per_edge(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match=">= 1"):
            sampler.boundary_focused(samples_per_edge=0)

    def test_custom_buffer(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.boundary_focused(samples_per_edge=1,
                                         buffer_distance=5, seed=42)
        assert plan.n_samples == 16  # 4 regions × 4 edges × 1


# ── density_weighted ─────────────────────────────────────────────

class TestDensityWeighted:
    def test_weighted_allocation(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        # Give region 0 much higher density
        densities = [100, 1, 1, 1]
        plan = sampler.density_weighted(total_samples=40,
                                         density_values=densities, seed=42)
        by_r = plan.by_region()
        assert len(by_r.get(0, [])) > len(by_r.get(1, []))

    def test_mismatched_densities(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match="must match"):
            sampler.density_weighted(total_samples=20, density_values=[1, 2])

    def test_total_preserved(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.density_weighted(total_samples=25,
                                         density_values=[1, 2, 3, 4], seed=42)
        assert plan.n_samples == 25


# ── adaptive ─────────────────────────────────────────────────────

class TestAdaptive:
    def test_auto_heterogeneity(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.adaptive(total_samples=40, seed=42)
        assert plan.n_samples == 40
        assert plan.method == "adaptive"

    def test_custom_heterogeneity(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        het = [10, 1, 1, 1]  # Region 0 is most heterogeneous
        plan = sampler.adaptive(total_samples=40,
                                heterogeneity_values=het, seed=42)
        by_r = plan.by_region()
        assert len(by_r.get(0, [])) > len(by_r.get(1, []))

    def test_invalid_total(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        with pytest.raises(ValueError, match=">= 1"):
            sampler.adaptive(total_samples=0)


# ── SamplePlan ───────────────────────────────────────────────────

class TestSamplePlan:
    def test_by_region(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=3, seed=42)
        by_r = plan.by_region()
        assert len(by_r) == 4
        assert all(len(by_r[i]) == 3 for i in range(4))

    def test_points(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=2, seed=42)
        pts = plan.points()
        assert len(pts) == 8
        assert all(isinstance(p, tuple) and len(p) == 2 for p in pts)

    def test_summary(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=2, seed=42)
        summary = plan.summary()
        assert "8 samples" in summary
        assert "4 regions" in summary


# ── SamplePoint ──────────────────────────────────────────────────

class TestSamplePoint:
    def test_to_dict(self):
        sp = SamplePoint(x=1.5, y=2.5, region_index=0,
                         region_label="A", sample_id="A_s0",
                         method="test")
        d = sp.to_dict()
        assert d['x'] == 1.5
        assert d['region_label'] == "A"

    def test_repr(self):
        sp = SamplePoint(x=1.5, y=2.5, region_index=0,
                         region_label="Zone_A", sample_id="Zone_A_s0",
                         method="test")
        r = repr(sp)
        assert "Zone_A_s0" in r


# ── Export ────────────────────────────────────────────────────────

class TestExport:
    def test_csv_export(self, tmp_path):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=2, seed=42)
        filepath = str(tmp_path / "plan.csv")
        sampler.export_sample_plan(plan, filepath, format="csv")
        with open(filepath) as f:
            lines = f.readlines()
        assert lines[0].startswith("sample_id")
        assert len(lines) == 9  # header + 8 samples

    def test_geojson_export(self, tmp_path):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=2, seed=42)
        filepath = str(tmp_path / "plan.geojson")
        sampler.export_sample_plan(plan, filepath, format="geojson")
        with open(filepath) as f:
            data = json.load(f)
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 8

    def test_invalid_format(self, tmp_path):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=1, seed=42)
        with pytest.raises(ValueError, match="Unsupported format"):
            sampler.export_sample_plan(plan, str(tmp_path / "x"), format="xml")


# ── Coverage analysis ────────────────────────────────────────────

class TestCoverageAnalysis:
    def test_full_coverage(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=5, seed=42)
        cov = sampler.coverage_analysis(plan)
        assert cov["total_samples"] == 20
        assert cov["covered_regions"] == 4
        assert cov["coverage_pct"] == 100
        assert len(cov["uncovered_regions"]) == 0

    def test_partial_coverage(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        # Create a plan with samples only in first 2 regions
        samples = [
            SamplePoint(50, 50, 0, "R0", "s0", "test"),
            SamplePoint(150, 50, 1, "R1", "s1", "test"),
        ]
        plan = SamplePlan(samples, "test", 4)
        cov = sampler.coverage_analysis(plan)
        assert cov["covered_regions"] == 2
        assert cov["coverage_pct"] == 50
        assert set(cov["uncovered_regions"]) == {2, 3}

    def test_density_uniformity(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        plan = sampler.stratified_random(samples_per_region=10, seed=42)
        cov = sampler.coverage_analysis(plan)
        # Equal regions with equal samples → low CV
        assert cov["density_cv"] < 0.01


# ── Allocation ───────────────────────────────────────────────────

class TestAllocation:
    def test_equal_sums_to_total(self):
        seeds, regions = make_unequal_regions()
        sampler = SpatialSampler(seeds, regions)
        counts = sampler._allocate(10, "equal")
        assert sum(counts) == 10

    def test_proportional_sums_to_total(self):
        seeds, regions = make_unequal_regions()
        sampler = SpatialSampler(seeds, regions)
        counts = sampler._allocate(100, "proportional")
        assert sum(counts) == 100

    def test_proportional_reflects_area(self):
        seeds, regions = make_unequal_regions()
        sampler = SpatialSampler(seeds, regions)
        counts = sampler._allocate(100, "proportional")
        # Region 1 (area 40000) should get ~76% of samples
        assert counts[1] > counts[0] > counts[2]

    def test_custom_weights(self):
        seeds, regions = make_test_regions()
        sampler = SpatialSampler(seeds, regions)
        counts = sampler._allocate(20, "proportional", weights=[10, 0, 0, 0])
        assert counts[0] == 20

    def test_optimal_allocation(self):
        seeds, regions = make_unequal_regions()
        sampler = SpatialSampler(seeds, regions)
        counts = sampler._allocate(50, "optimal")
        assert sum(counts) == 50
