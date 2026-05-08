#!/usr/bin/env python3
"""Tests for Spatial Hydrology Engine."""

import json
import math
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_hydrology import (
    CellHydrology,
    DrainageBasin,
    FlowCorridor,
    HydrologyEngine,
    HydrologyResult,
    hydrology_analyze,
    hydrology_demo,
    _euclidean,
    _knn_adjacency,
    _voronoi_areas,
    _normalize,
    _compute_elevation,
    _classify_terrain,
    _build_drainage,
    _delineate_basins,
    _compute_strahler,
    _compute_precipitation,
    _compute_flow_accumulation,
    _compute_flood_risk,
    _compute_recharge,
    _generate_insights,
    _compute_health_score,
)


def _make_points(n=20, seed=42):
    rng = random.Random(seed)
    return [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]


def _quick_engine(n=20, seed=42):
    pts = _make_points(n, seed)
    engine = HydrologyEngine(points=pts, seed=seed)
    return engine, engine.analyze()


class TestEuclidean(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_same_point(self):
        self.assertAlmostEqual(_euclidean((5, 5), (5, 5)), 0.0)


class TestKnnAdjacency(unittest.TestCase):
    def test_symmetric(self):
        pts = _make_points(10)
        adj = _knn_adjacency(pts, 4)
        for i, nbrs in adj.items():
            for j in nbrs:
                self.assertIn(i, adj[j])

    def test_all_connected(self):
        pts = _make_points(10)
        adj = _knn_adjacency(pts, 4)
        for i in range(10):
            self.assertGreater(len(adj[i]), 0)


class TestNormalize(unittest.TestCase):
    def test_range(self):
        vals = [10, 20, 30, 40, 50]
        normed = _normalize(vals)
        self.assertAlmostEqual(min(normed), 0.0)
        self.assertAlmostEqual(max(normed), 1.0)

    def test_empty(self):
        self.assertEqual(_normalize([]), [])

    def test_constant(self):
        normed = _normalize([5, 5, 5])
        self.assertTrue(all(v == 0.5 for v in normed))


class TestElevation(unittest.TestCase):
    def test_elevation_count(self):
        pts = _make_points(15)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        self.assertEqual(len(elev), 15)

    def test_elevation_variation(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        self.assertGreater(max(elev) - min(elev), 0)


class TestTerrainClassification(unittest.TestCase):
    def test_valid_types(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        types = _classify_terrain(elev, adj)
        valid = {"peak", "ridge", "slope", "valley", "basin"}
        for t in types:
            self.assertIn(t, valid)

    def test_count_matches(self):
        pts = _make_points(15)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        types = _classify_terrain(elev, adj)
        self.assertEqual(len(types), 15)


class TestDrainage(unittest.TestCase):
    def test_flow_directions(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        flow_dir, upstream = _build_drainage(pts, elev, adj)
        self.assertEqual(len(flow_dir), 20)
        # At least one sink
        sinks = [i for i in range(20) if flow_dir[i] == -1]
        self.assertGreater(len(sinks), 0)

    def test_flow_downhill(self):
        """Flow direction should point to a lower-elevation neighbor."""
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        flow_dir, _ = _build_drainage(pts, elev, adj)
        for i in range(20):
            if flow_dir[i] >= 0:
                self.assertLess(elev[flow_dir[i]], elev[i])


class TestBasins(unittest.TestCase):
    def test_all_assigned(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        flow_dir, _ = _build_drainage(pts, elev, adj)
        basin_ids, _ = _delineate_basins(flow_dir, 20)
        for bid in basin_ids:
            self.assertGreaterEqual(bid, 0)

    def test_every_cell_in_one_basin(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        flow_dir, _ = _build_drainage(pts, elev, adj)
        basin_ids, _ = _delineate_basins(flow_dir, 20)
        self.assertEqual(len(basin_ids), 20)


class TestStrahler(unittest.TestCase):
    def test_positive_orders(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        _, upstream = _build_drainage(pts, elev, adj)
        orders = _compute_strahler(upstream, 20)
        for o in orders:
            self.assertGreaterEqual(o, 1)

    def test_headwaters_order_one(self):
        pts = _make_points(20)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        _, upstream = _build_drainage(pts, elev, adj)
        orders = _compute_strahler(upstream, 20)
        for i in range(20):
            if not upstream.get(i, []):
                self.assertEqual(orders[i], 1)


class TestPrecipitation(unittest.TestCase):
    def test_positive_values(self):
        pts = _make_points(15)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        types = _classify_terrain(elev, adj)
        precip, runoff = _compute_precipitation(elev, areas, types, rng)
        for p in precip:
            self.assertGreater(p, 0)
        for r in runoff:
            self.assertGreater(r, 0)

    def test_runoff_less_than_precip(self):
        pts = _make_points(15)
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        rng = random.Random(42)
        elev = _compute_elevation(pts, adj, areas, rng)
        types = _classify_terrain(elev, adj)
        precip, runoff = _compute_precipitation(elev, areas, types, rng)
        for p, r in zip(precip, runoff):
            self.assertLessEqual(r, p)


class TestFlowAccumulation(unittest.TestCase):
    def test_accumulation_geq_runoff(self):
        _, result = _quick_engine(20)
        for c in result.cells:
            self.assertGreaterEqual(c.flow_accumulation, c.runoff - 0.01)

    def test_sinks_have_max_accum(self):
        """Sinks should have highest accumulation in their basin."""
        _, result = _quick_engine(20)
        # Group by basin
        basins: dict = {}
        for c in result.cells:
            basins.setdefault(c.basin_id, []).append(c)
        for bid, cells in basins.items():
            sink = [c for c in cells if c.flow_direction == -1]
            if sink:
                max_accum = max(c.flow_accumulation for c in cells)
                for s in sink:
                    self.assertAlmostEqual(s.flow_accumulation, max_accum, places=1)


class TestFloodRisk(unittest.TestCase):
    def test_risk_range(self):
        _, result = _quick_engine(20)
        for c in result.cells:
            self.assertGreaterEqual(c.flood_risk, 0)
            self.assertLessEqual(c.flood_risk, 100)

    def test_valid_classes(self):
        _, result = _quick_engine(20)
        valid = {"safe", "watch", "warning", "danger", "critical"}
        for c in result.cells:
            self.assertIn(c.flood_class, valid)

    def test_class_matches_risk(self):
        _, result = _quick_engine(20)
        for c in result.cells:
            if c.flood_risk >= 80:
                self.assertEqual(c.flood_class, "critical")
            elif c.flood_risk < 20:
                self.assertEqual(c.flood_class, "safe")


class TestRecharge(unittest.TestCase):
    def test_recharge_range(self):
        _, result = _quick_engine(20)
        for c in result.cells:
            self.assertGreaterEqual(c.recharge_potential, 0)
            self.assertLessEqual(c.recharge_potential, 100)

    def test_valid_classes(self):
        _, result = _quick_engine(20)
        valid = {"excellent", "good", "moderate", "poor", "negligible"}
        for c in result.cells:
            self.assertIn(c.recharge_class, valid)


class TestHealthScore(unittest.TestCase):
    def test_range(self):
        _, result = _quick_engine(20)
        self.assertGreaterEqual(result.health_score, 0)
        self.assertLessEqual(result.health_score, 100)

    def test_deterministic(self):
        _, r1 = _quick_engine(20, seed=99)
        _, r2 = _quick_engine(20, seed=99)
        self.assertAlmostEqual(r1.health_score, r2.health_score)


class TestInsights(unittest.TestCase):
    def test_non_empty(self):
        _, result = _quick_engine(20)
        self.assertGreater(len(result.insights), 0)

    def test_all_strings(self):
        _, result = _quick_engine(20)
        for ins in result.insights:
            self.assertIsInstance(ins, str)
            self.assertGreater(len(ins), 0)


class TestBasinResult(unittest.TestCase):
    def test_basin_count(self):
        _, result = _quick_engine(20)
        self.assertGreater(result.num_basins, 0)

    def test_total_cells_in_basins(self):
        _, result = _quick_engine(20)
        total = sum(len(b.cells) for b in result.basins)
        self.assertEqual(total, 20)

    def test_basin_runoff_positive(self):
        _, result = _quick_engine(20)
        for b in result.basins:
            self.assertGreater(b.total_runoff, 0)


class TestFlowCorridors(unittest.TestCase):
    def test_corridors_exist(self):
        _, result = _quick_engine(20)
        self.assertGreater(len(result.flow_corridors), 0)

    def test_sorted_by_accumulation(self):
        _, result = _quick_engine(20)
        accums = [c.accumulation for c in result.flow_corridors]
        self.assertEqual(accums, sorted(accums, reverse=True))


class TestEdgeCases(unittest.TestCase):
    def test_single_point(self):
        engine = HydrologyEngine(points=[(5, 5)])
        result = engine.analyze()
        self.assertEqual(len(result.cells), 1)
        self.assertGreaterEqual(result.health_score, 0)

    def test_two_points(self):
        engine = HydrologyEngine(points=[(0, 0), (10, 10)])
        result = engine.analyze()
        self.assertEqual(len(result.cells), 2)

    def test_collinear_points(self):
        pts = [(i * 10, 0) for i in range(5)]
        engine = HydrologyEngine(points=pts)
        result = engine.analyze()
        self.assertEqual(len(result.cells), 5)

    def test_empty_points(self):
        engine = HydrologyEngine(points=[])
        result = engine.analyze()
        self.assertEqual(len(result.cells), 0)
        self.assertGreater(len(result.insights), 0)


class TestJsonExport(unittest.TestCase):
    def test_json_created(self):
        engine, _ = _quick_engine(15)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            engine.to_json(path)
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                data = json.load(fh)
            self.assertIn("health_score", data)
            self.assertIn("cells", data)
            self.assertIn("basins", data)
            self.assertGreater(len(data["cells"]), 0)
        finally:
            os.unlink(path)


class TestHtmlExport(unittest.TestCase):
    def test_html_created(self):
        engine, _ = _quick_engine(15)
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            engine.to_html(path)
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                content = fh.read()
            self.assertIn("Spatial Hydrology Dashboard", content)
            self.assertGreater(len(content), 500)
        finally:
            os.unlink(path)


class TestConvenienceAPI(unittest.TestCase):
    def test_hydrology_analyze_points(self):
        pts = _make_points(10)
        result = hydrology_analyze(pts)
        self.assertIsInstance(result, HydrologyResult)
        self.assertEqual(len(result.cells), 10)

    def test_hydrology_analyze_file(self):
        pts = _make_points(10)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for x, y in pts:
                f.write(f"{x} {y}\n")
            path = f.name
        try:
            result = hydrology_analyze(path)
            self.assertEqual(len(result.cells), 10)
        finally:
            os.unlink(path)


class TestDemo(unittest.TestCase):
    def test_demo_runs(self):
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            hydrology_demo()
            output = sys.stdout.getvalue()
            self.assertIn("Hydrology Engine", output)
            self.assertIn("Health Score", output)
        finally:
            sys.stdout = old_stdout


class TestAggregates(unittest.TestCase):
    def test_total_precipitation(self):
        _, result = _quick_engine(20)
        expected = sum(c.precipitation for c in result.cells)
        self.assertAlmostEqual(result.total_precipitation, expected, places=1)

    def test_total_runoff(self):
        _, result = _quick_engine(20)
        expected = sum(c.runoff for c in result.cells)
        self.assertAlmostEqual(result.total_runoff, expected, places=1)

    def test_avg_flood_risk(self):
        _, result = _quick_engine(20)
        expected = sum(c.flood_risk for c in result.cells) / 20
        self.assertAlmostEqual(result.avg_flood_risk, expected, places=1)


class TestLargerDataset(unittest.TestCase):
    def test_50_points(self):
        _, result = _quick_engine(50, seed=123)
        self.assertEqual(len(result.cells), 50)
        self.assertGreater(result.num_basins, 0)
        self.assertGreater(len(result.flow_corridors), 0)

    def test_reproducible(self):
        _, r1 = _quick_engine(30, seed=77)
        _, r2 = _quick_engine(30, seed=77)
        for c1, c2 in zip(r1.cells, r2.cells):
            self.assertAlmostEqual(c1.elevation, c2.elevation)
            self.assertAlmostEqual(c1.flood_risk, c2.flood_risk)


if __name__ == "__main__":
    unittest.main()
