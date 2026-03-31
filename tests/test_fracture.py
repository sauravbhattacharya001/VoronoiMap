"""Tests for vormap_fracture — Voronoi Fracture Simulator."""

import json
import math
import os
import tempfile

import pytest

from vormap_fracture import (
    MATERIALS,
    generate_fracture,
    fracture_to_svg,
    fracture_to_json,
    _radial_seeds,
    _uniform_seeds,
    _directional_seeds,
    _concentric_seeds,
    _jitter_edges,
    _polygon_centroid,
    _assign_voronoi_cells,
)

import random


# ── Seed generators ──────────────────────────────────────────────────


class TestRadialSeeds:
    def test_count(self):
        seeds = _radial_seeds(50, 800, 600, (400, 300), random.Random(42))
        assert len(seeds) == 50

    def test_within_bounds(self):
        seeds = _radial_seeds(100, 800, 600, (400, 300), random.Random(42))
        for x, y in seeds:
            assert 1 <= x <= 799
            assert 1 <= y <= 599

    def test_biased_toward_impact(self):
        """Seeds should cluster near the impact point."""
        rng = random.Random(42)
        impact = (400, 300)
        seeds = _radial_seeds(500, 800, 600, impact, rng)
        dists = [math.sqrt((x - impact[0])**2 + (y - impact[1])**2) for x, y in seeds]
        median_dist = sorted(dists)[len(dists) // 2]
        max_possible = math.sqrt(800**2 + 600**2) / 2
        # Median should be well under half the max radius (biased distribution)
        assert median_dist < max_possible * 0.75

    def test_reproducible(self):
        s1 = _radial_seeds(20, 800, 600, (400, 300), random.Random(99))
        s2 = _radial_seeds(20, 800, 600, (400, 300), random.Random(99))
        assert s1 == s2


class TestUniformSeeds:
    def test_count(self):
        seeds = _uniform_seeds(25, 800, 600, random.Random(42))
        assert len(seeds) == 25

    def test_within_bounds(self):
        seeds = _uniform_seeds(50, 800, 600, random.Random(42))
        for x, y in seeds:
            assert 1 <= x <= 799
            assert 1 <= y <= 599

    def test_coverage(self):
        """Seeds should spread across the canvas, not cluster."""
        seeds = _uniform_seeds(100, 800, 600, random.Random(42))
        xs = [s[0] for s in seeds]
        ys = [s[1] for s in seeds]
        # Should cover most of the canvas
        assert max(xs) - min(xs) > 400
        assert max(ys) - min(ys) > 300


class TestDirectionalSeeds:
    def test_count(self):
        seeds = _directional_seeds(30, 800, 600, 45, random.Random(42))
        assert len(seeds) == 30

    def test_within_bounds(self):
        seeds = _directional_seeds(50, 800, 600, 0, random.Random(42))
        for x, y in seeds:
            assert 1 <= x <= 799
            assert 1 <= y <= 599


class TestConcentricSeeds:
    def test_count(self):
        seeds = _concentric_seeds(30, 800, 600, (400, 300), 5, random.Random(42))
        assert len(seeds) == 30

    def test_within_bounds(self):
        seeds = _concentric_seeds(40, 800, 600, (400, 300), 4, random.Random(42))
        for x, y in seeds:
            assert 1 <= x <= 799
            assert 1 <= y <= 599

    def test_ring_structure(self):
        """Seeds should form approximate rings at distinct radii."""
        impact = (400, 300)
        seeds = _concentric_seeds(100, 800, 600, impact, 5, random.Random(42))
        dists = sorted(math.sqrt((x - impact[0])**2 + (y - impact[1])**2) for x, y in seeds)
        # With 5 rings, we should see clusters at different distances
        assert len(set(round(d, -1) for d in dists)) > 3


# ── Helpers ──────────────────────────────────────────────────────────


class TestJitterEdges:
    def test_no_jitter(self):
        verts = [(0, 0), (10, 0), (10, 10)]
        result = _jitter_edges(verts, 0.0, random.Random(42))
        assert result == verts

    def test_jitter_changes_coords(self):
        verts = [(100, 100), (200, 100), (200, 200), (100, 200)]
        result = _jitter_edges(verts, 5.0, random.Random(42))
        assert len(result) == len(verts)
        assert result != verts  # Should be different with jitter

    def test_preserves_count(self):
        verts = [(i, i) for i in range(20)]
        result = _jitter_edges(verts, 2.0, random.Random(42))
        assert len(result) == 20


class TestPolygonCentroid:
    def test_square(self):
        verts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cx, cy = _polygon_centroid(verts)
        assert abs(cx - 5) < 0.1
        assert abs(cy - 5) < 0.1

    def test_triangle(self):
        verts = [(0, 0), (6, 0), (3, 6)]
        cx, cy = _polygon_centroid(verts)
        assert abs(cx - 3) < 0.1
        assert abs(cy - 2) < 0.1


# ── Fallback Voronoi ─────────────────────────────────────────────────


class TestAssignVoronoiCells:
    def test_produces_cells(self):
        seeds = [(200, 200), (600, 400)]
        cells = _assign_voronoi_cells(seeds, 800, 600, resolution=50)
        assert len(cells) > 0

    def test_cells_have_vertices(self):
        seeds = [(200, 150), (600, 450), (400, 300)]
        cells = _assign_voronoi_cells(seeds, 800, 600, resolution=50)
        for idx, verts in cells.items():
            assert len(verts) >= 3, f"Cell {idx} has too few vertices"


# ── generate_fracture ────────────────────────────────────────────────


class TestGenerateFracture:
    def test_default(self):
        result = generate_fracture(seed=42)
        assert "metadata" in result
        assert "fragments" in result
        assert "seeds" in result
        assert result["metadata"]["mode"] == "radial"
        assert result["metadata"]["material"] == "glass"

    def test_fragment_count(self):
        result = generate_fracture(fragments=20, seed=42)
        # May not produce exactly 20 due to Voronoi clipping, but should be close
        assert len(result["fragments"]) > 0
        assert len(result["fragments"]) <= 20

    @pytest.mark.parametrize("mode", ["radial", "uniform", "directional", "concentric"])
    def test_modes(self, mode):
        result = generate_fracture(mode=mode, fragments=15, seed=42)
        assert result["metadata"]["mode"] == mode
        assert len(result["fragments"]) > 0

    @pytest.mark.parametrize("material", list(MATERIALS.keys()))
    def test_materials(self, material):
        result = generate_fracture(material=material, fragments=10, seed=42)
        assert result["metadata"]["material"] == material

    def test_invalid_mode(self):
        with pytest.raises(ValueError, match="Unknown fracture mode"):
            generate_fracture(mode="explode")

    def test_custom_impact(self):
        result = generate_fracture(impact=(100, 100), seed=42)
        assert result["metadata"]["impact"] == (100, 100)

    def test_reproducible(self):
        r1 = generate_fracture(seed=42)
        r2 = generate_fracture(seed=42)
        assert r1["fragments"] == r2["fragments"]

    def test_fragment_properties(self):
        result = generate_fracture(fragments=10, seed=42)
        for frag in result["fragments"]:
            assert "id" in frag
            assert "vertices" in frag
            assert "area" in frag
            assert "area_pct" in frag
            assert "centroid" in frag
            assert "displacement" in frag
            assert "distance_from_impact" in frag
            assert "edge_count" in frag
            assert frag["area"] >= 0
            assert frag["edge_count"] >= 3
            assert len(frag["centroid"]) == 2
            assert len(frag["displacement"]) == 2

    def test_sorted_by_distance(self):
        result = generate_fracture(fragments=20, seed=42)
        dists = [f["distance_from_impact"] for f in result["fragments"]]
        assert dists == sorted(dists)

    def test_custom_edge_jitter(self):
        r_no_jitter = generate_fracture(fragments=10, seed=42, edge_jitter=0.0)
        r_jitter = generate_fracture(fragments=10, seed=42, edge_jitter=10.0)
        # Same fragment IDs but different vertex positions
        assert len(r_no_jitter["fragments"]) == len(r_jitter["fragments"])

    def test_custom_dimensions(self):
        result = generate_fracture(width=1920, height=1080, fragments=10, seed=42)
        assert result["metadata"]["width"] == 1920
        assert result["metadata"]["height"] == 1080

    def test_concentric_rings(self):
        result = generate_fracture(mode="concentric", rings=3, fragments=15, seed=42)
        assert len(result["fragments"]) > 0

    def test_directional_angle(self):
        result = generate_fracture(mode="directional", angle=90, fragments=10, seed=42)
        assert len(result["fragments"]) > 0


# ── SVG export ───────────────────────────────────────────────────────


class TestFractureToSvg:
    def test_valid_svg(self):
        result = generate_fracture(fragments=10, seed=42)
        svg = fracture_to_svg(result)
        assert svg.startswith("<svg")
        assert svg.strip().endswith("</svg>")

    def test_contains_polygons(self):
        result = generate_fracture(fragments=10, seed=42)
        svg = fracture_to_svg(result)
        assert "<polygon" in svg

    def test_radial_has_impact_marker(self):
        result = generate_fracture(mode="radial", fragments=10, seed=42)
        svg = fracture_to_svg(result)
        assert "<circle" in svg

    def test_uniform_no_impact_marker(self):
        result = generate_fracture(mode="uniform", fragments=10, seed=42)
        svg = fracture_to_svg(result)
        assert "<circle" not in svg

    def test_dimensions_in_svg(self):
        result = generate_fracture(width=1024, height=768, fragments=5, seed=42)
        svg = fracture_to_svg(result)
        assert 'width="1024"' in svg
        assert 'height="768"' in svg


# ── JSON export ──────────────────────────────────────────────────────


class TestFractureToJson:
    def test_valid_json(self):
        result = generate_fracture(fragments=10, seed=42)
        js = fracture_to_json(result)
        parsed = json.loads(js)
        assert "metadata" in parsed
        assert "fragments" in parsed

    def test_roundtrip(self):
        result = generate_fracture(fragments=10, seed=42)
        js = fracture_to_json(result)
        parsed = json.loads(js)
        assert len(parsed["fragments"]) == len(result["fragments"])


# ── MATERIALS constant ───────────────────────────────────────────────


class TestMaterials:
    @pytest.mark.parametrize("material", list(MATERIALS.keys()))
    def test_required_keys(self, material):
        mat = MATERIALS[material]
        for key in ["crack_width", "crack_color", "fill_opacity", "fill_base",
                     "edge_jitter", "description"]:
            assert key in mat, f"{material} missing key: {key}"

    def test_at_least_two_materials(self):
        assert len(MATERIALS) >= 2
