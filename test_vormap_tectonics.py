#!/usr/bin/env python3
"""Tests for Spatial Tectonics Engine."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_tectonics import (
    TectonicsEngine,
    TectonicsResult,
    tectonics_analyze,
    tectonics_demo,
    _euclidean,
    _knn_adjacency,
    _voronoi_areas,
    _normalize,
    _assign_plates,
    _compute_drift,
    _gini_coeff,
)

import random


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEMO_POINTS = [(i * 10, j * 10) for i in range(5) for j in range(5)]  # 25 pts grid


class TestHelpers(unittest.TestCase):
    def test_euclidean_zero(self):
        self.assertAlmostEqual(_euclidean((0, 0), (0, 0)), 0.0)

    def test_euclidean_basic(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_knn_adjacency_symmetric(self):
        pts = [(0, 0), (1, 0), (0, 1)]
        adj = _knn_adjacency(pts, k=2)
        for i in adj:
            for j in adj[i]:
                self.assertIn(i, adj[j])

    def test_knn_adjacency_all_connected(self):
        pts = [(0, 0), (1, 0), (2, 0), (3, 0)]
        adj = _knn_adjacency(pts, k=2)
        for i in range(len(pts)):
            self.assertGreater(len(adj[i]), 0)

    def test_voronoi_areas_positive(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        for a in areas:
            self.assertGreater(a, 0)

    def test_normalize_uniform(self):
        vals = [5.0, 5.0, 5.0]
        n = _normalize(vals)
        self.assertEqual(n, [0.5, 0.5, 0.5])

    def test_normalize_range(self):
        vals = [1.0, 2.0, 3.0]
        n = _normalize(vals)
        self.assertAlmostEqual(n[0], 0.0)
        self.assertAlmostEqual(n[-1], 1.0)

    def test_gini_equal(self):
        self.assertAlmostEqual(_gini_coeff([1.0, 1.0, 1.0]), 0.0)

    def test_gini_unequal(self):
        g = _gini_coeff([0.0, 0.0, 0.0, 100.0])
        self.assertGreater(g, 0.5)


# ---------------------------------------------------------------------------
# Engine: Plate Assignment
# ---------------------------------------------------------------------------


class TestPlateAssignment(unittest.TestCase):
    def test_all_cells_assigned(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        plates = _assign_plates(pts, adj, areas, 3, rng)
        assigned = set()
        for p in plates:
            assigned.update(p.cells)
        self.assertEqual(assigned, set(range(len(pts))))

    def test_correct_plate_count(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        plates = _assign_plates(pts, adj, areas, 4, rng)
        self.assertEqual(len(plates), 4)

    def test_plate_types_valid(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        plates = _assign_plates(pts, adj, areas, 3, rng)
        for p in plates:
            self.assertIn(p.plate_type, ("continental", "oceanic"))

    def test_single_plate(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        plates = _assign_plates(pts, adj, areas, 1, rng)
        self.assertEqual(len(plates), 1)
        self.assertEqual(len(plates[0].cells), len(pts))

    def test_more_plates_than_points(self):
        pts = [(0, 0), (1, 0), (0, 1)]
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        plates = _assign_plates(pts, adj, areas, 10, rng)
        # Should cap at n
        self.assertEqual(len(plates), 3)


# ---------------------------------------------------------------------------
# Engine: Drift Vectors
# ---------------------------------------------------------------------------


class TestDriftVectors(unittest.TestCase):
    def test_drift_assigned(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        plates = _assign_plates(pts, adj, areas, 3, rng)
        _compute_drift(plates, rng)
        for p in plates:
            self.assertGreater(p.drift_speed, 0)
            self.assertTrue(0 <= p.drift_direction <= 360)

    def test_drift_reproducible(self):
        pts = DEMO_POINTS
        adj = _knn_adjacency(pts)
        areas = _voronoi_areas(pts, adj)
        speeds = []
        for _ in range(2):
            rng = random.Random(42)
            plates = _assign_plates(pts, adj, areas, 3, rng)
            _compute_drift(plates, rng)
            speeds.append([p.drift_speed for p in plates])
        self.assertEqual(speeds[0], speeds[1])


# ---------------------------------------------------------------------------
# Engine: Boundary Classification
# ---------------------------------------------------------------------------


class TestBoundaryClassifier(unittest.TestCase):
    def test_boundaries_detected(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertGreater(len(result.boundaries), 0)

    def test_boundary_types_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        for bd in result.boundaries:
            self.assertIn(bd.boundary_type, ("convergent", "divergent", "transform"))

    def test_boundary_cells_on_different_plates(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        cell_plate = {c.cell_id: c.plate_id for c in result.cells}
        for bd in result.boundaries:
            self.assertNotEqual(cell_plate[bd.cell_a], cell_plate[bd.cell_b])

    def test_boundary_speed_non_negative(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        for bd in result.boundaries:
            self.assertGreaterEqual(bd.relative_speed, 0)

    def test_single_plate_no_boundaries(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=1)
        result = engine.analyze()
        self.assertEqual(len(result.boundaries), 0)


# ---------------------------------------------------------------------------
# Engine: Seismic Activity
# ---------------------------------------------------------------------------


class TestSeismicActivity(unittest.TestCase):
    def test_events_generated(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        self.assertGreater(len(result.seismic_events), 0)

    def test_probabilities_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        for ev in result.seismic_events:
            self.assertGreaterEqual(ev.probability, 0.0)
            self.assertLessEqual(ev.probability, 1.0)

    def test_magnitudes_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        for ev in result.seismic_events:
            self.assertGreaterEqual(ev.magnitude, 0.0)
            self.assertLessEqual(ev.magnitude, 9.5)

    def test_depth_classes_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        for ev in result.seismic_events:
            self.assertIn(ev.depth_class, ("shallow", "intermediate", "deep"))


# ---------------------------------------------------------------------------
# Engine: Volcanic Activity
# ---------------------------------------------------------------------------


class TestVolcanicActivity(unittest.TestCase):
    def test_volcanic_zones_generated(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        self.assertGreater(len(result.volcanic_zones), 0)

    def test_zone_types_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        for vz in result.volcanic_zones:
            self.assertIn(vz.zone_type, ("subduction", "rift", "hotspot"))

    def test_magma_types_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        for vz in result.volcanic_zones:
            self.assertIn(vz.magma_type, ("basaltic", "andesitic", "rhyolitic"))

    def test_eruption_probability_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        for vz in result.volcanic_zones:
            self.assertGreaterEqual(vz.eruption_probability, 0.0)
            self.assertLessEqual(vz.eruption_probability, 1.0)


# ---------------------------------------------------------------------------
# Engine: Insights & Health
# ---------------------------------------------------------------------------


class TestInsights(unittest.TestCase):
    def test_health_score_range(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertGreaterEqual(result.health_score, 0.0)
        self.assertLessEqual(result.health_score, 100.0)

    def test_insights_non_empty(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertGreater(len(result.insights), 0)

    def test_tectonic_regimes_valid(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        valid_regimes = {"stable_craton", "active_margin", "rift_zone", "hotspot", "collision_zone"}
        for c in result.cells:
            self.assertIn(c.tectonic_regime, valid_regimes)


# ---------------------------------------------------------------------------
# Full Engine Integration
# ---------------------------------------------------------------------------


class TestEngineIntegration(unittest.TestCase):
    def test_analyze_returns_result(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertIsInstance(result, TectonicsResult)

    def test_all_cells_present(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertEqual(len(result.cells), len(DEMO_POINTS))

    def test_plates_present(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertEqual(len(result.plates), 3)

    def test_avg_stress_non_negative(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        result = engine.analyze()
        self.assertGreaterEqual(result.avg_stress, 0.0)

    def test_deterministic(self):
        r1 = TectonicsEngine(points=DEMO_POINTS, num_plates=3, seed=99).analyze()
        r2 = TectonicsEngine(points=DEMO_POINTS, num_plates=3, seed=99).analyze()
        self.assertEqual(r1.health_score, r2.health_score)
        self.assertEqual(len(r1.boundaries), len(r2.boundaries))
        self.assertEqual(len(r1.seismic_events), len(r2.seismic_events))

    def test_different_seeds_differ(self):
        r1 = TectonicsEngine(points=DEMO_POINTS, num_plates=3, seed=1).analyze()
        r2 = TectonicsEngine(points=DEMO_POINTS, num_plates=3, seed=999).analyze()
        # Plates will differ due to different seeds
        cells1 = {c.cell_id: c.plate_id for c in r1.cells}
        cells2 = {c.cell_id: c.plate_id for c in r2.cells}
        self.assertNotEqual(cells1, cells2)

    def test_total_seismic_energy(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=4)
        result = engine.analyze()
        self.assertGreater(result.total_seismic_energy, 0)


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases(unittest.TestCase):
    def test_empty_points(self):
        engine = TectonicsEngine(points=[])
        result = engine.analyze()
        self.assertIsInstance(result, TectonicsResult)
        self.assertEqual(len(result.cells), 0)

    def test_single_point(self):
        engine = TectonicsEngine(points=[(5, 5)], num_plates=1)
        result = engine.analyze()
        self.assertEqual(len(result.cells), 1)
        self.assertEqual(len(result.plates), 1)

    def test_two_points(self):
        engine = TectonicsEngine(points=[(0, 0), (10, 0)], num_plates=2)
        result = engine.analyze()
        self.assertEqual(len(result.cells), 2)

    def test_collinear_points(self):
        pts = [(i, 0) for i in range(10)]
        engine = TectonicsEngine(points=pts, num_plates=3)
        result = engine.analyze()
        self.assertEqual(len(result.cells), 10)
        self.assertGreaterEqual(result.health_score, 0.0)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization(unittest.TestCase):
    def test_to_dict(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        d = engine.to_dict()
        self.assertIn("cells", d)
        self.assertIn("plates", d)
        self.assertIn("health_score", d)

    def test_to_json(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        engine.analyze()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            engine.to_json(path)
            with open(path) as fh:
                data = json.load(fh)
            self.assertIn("health_score", data)
        finally:
            os.unlink(path)

    def test_to_html(self):
        engine = TectonicsEngine(points=DEMO_POINTS, num_plates=3)
        engine.analyze()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            engine.to_html(path)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            self.assertIn("Spatial Tectonics Dashboard", content)
            self.assertIn("Health Score", content)
        finally:
            os.unlink(path)

    def test_file_load(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for x, y in DEMO_POINTS:
                f.write(f"{x} {y}\n")
            path = f.name
        try:
            engine = TectonicsEngine(path=path, num_plates=3)
            result = engine.analyze()
            self.assertEqual(len(result.cells), len(DEMO_POINTS))
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Demo & CLI
# ---------------------------------------------------------------------------


class TestDemoAndCLI(unittest.TestCase):
    def test_demo_runs(self):
        result = tectonics_demo()
        self.assertIsInstance(result, TectonicsResult)
        self.assertGreater(len(result.plates), 0)

    def test_tectonics_analyze_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for x, y in DEMO_POINTS:
                f.write(f"{x} {y}\n")
            path = f.name
        try:
            result = tectonics_analyze(path, num_plates=3)
            self.assertIsInstance(result, TectonicsResult)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
