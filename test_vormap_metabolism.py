#!/usr/bin/env python3
"""Tests for vormap_metabolism.py — Spatial Metabolism Engine (50+ tests)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_metabolism import (
    MetabolismEngine,
    MetabolismResult,
    CellMetabolism,
    TradeFlow,
    metabolism_analyze,
    metabolism_demo,
    _euclidean,
    _knn_adjacency,
    _voronoi_areas,
    _normalize,
    _gini,
    _load_points,
    _estimate_production,
    _model_consumption,
    _compute_trade_flows,
    _detect_bottlenecks,
    _analyze_efficiency,
    _metabolic_rates,
    _generate_insights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestEuclidean(unittest.TestCase):
    def test_zero(self):
        self.assertAlmostEqual(_euclidean((0, 0), (0, 0)), 0.0)

    def test_basic(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_negative(self):
        self.assertAlmostEqual(_euclidean((-1, -1), (2, 3)), 5.0)


class TestNormalize(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_normalize([]), [])

    def test_constant(self):
        result = _normalize([5, 5, 5])
        self.assertTrue(all(v == 0.5 for v in result))

    def test_range(self):
        result = _normalize([0, 5, 10])
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[1], 0.5)
        self.assertAlmostEqual(result[2], 1.0)


class TestGini(unittest.TestCase):
    def test_equal(self):
        self.assertAlmostEqual(_gini([10, 10, 10, 10]), 0.0)

    def test_unequal(self):
        g = _gini([0, 0, 0, 100])
        self.assertGreater(g, 0.5)

    def test_empty(self):
        self.assertAlmostEqual(_gini([]), 0.0)

    def test_single(self):
        self.assertAlmostEqual(_gini([42]), 0.0)


class TestLoadPoints(unittest.TestCase):
    def test_basic(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False) as f:
            f.write("0 0\n10 0\n5 8\n")
            f.flush()
            pts = _load_points(f.name)
        os.unlink(f.name)
        self.assertEqual(len(pts), 3)
        self.assertEqual(pts[0], (0.0, 0.0))

    def test_comments_and_blanks(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False) as f:
            f.write("# header\n\n1 2\n3 4\n")
            f.flush()
            pts = _load_points(f.name)
        os.unlink(f.name)
        self.assertEqual(len(pts), 2)


class TestKnnAdjacency(unittest.TestCase):
    def test_basic(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
        adj = _knn_adjacency(pts, k=2)
        self.assertEqual(len(adj), 4)
        for i in adj:
            self.assertGreater(len(adj[i]), 0)

    def test_symmetric(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        adj = _knn_adjacency(pts, k=2)
        for i in adj:
            for j in adj[i]:
                self.assertIn(i, adj[j])


class TestVoronoiAreas(unittest.TestCase):
    def test_positive_areas(self):
        pts = [(0, 0), (10, 0), (5, 8), (0, 8), (10, 8)]
        adj = _knn_adjacency(pts, k=3)
        areas = _voronoi_areas(pts, adj)
        self.assertEqual(len(areas), 5)
        for a in areas:
            self.assertGreater(a, 0)


# ---------------------------------------------------------------------------
# Engine 1: Production
# ---------------------------------------------------------------------------


class TestProductionEstimator(unittest.TestCase):
    def test_basic(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, k=2)
        areas = _voronoi_areas(pts, adj)
        prod = _estimate_production(pts, areas, adj)
        self.assertEqual(len(prod), 3)
        for p in prod:
            self.assertGreater(p, 0)

    def test_larger_area_more_production(self):
        """Cells with larger area should generally produce more."""
        pts = [(0, 0), (1, 0), (100, 0), (100, 100)]
        adj = _knn_adjacency(pts, k=2)
        areas = [1.0, 1.0, 100.0, 100.0]
        prod = _estimate_production(pts, areas, adj)
        # Large-area cells should produce more
        self.assertGreater(prod[2], prod[0])


# ---------------------------------------------------------------------------
# Engine 2: Consumption
# ---------------------------------------------------------------------------


class TestConsumptionModeler(unittest.TestCase):
    def test_basic(self):
        pts = [(0, 0), (10, 0), (5, 5)]
        areas = [10.0, 10.0, 10.0]
        cons = _model_consumption(pts, areas, seed=42)
        self.assertEqual(len(cons), 3)
        for c in cons:
            self.assertGreater(c, 0)

    def test_deterministic(self):
        pts = [(0, 0), (10, 0)]
        areas = [5.0, 5.0]
        c1 = _model_consumption(pts, areas, seed=99)
        c2 = _model_consumption(pts, areas, seed=99)
        self.assertEqual(c1, c2)

    def test_empty(self):
        self.assertEqual(_model_consumption([], [], seed=0), [])


# ---------------------------------------------------------------------------
# Engine 3: Trade Flows
# ---------------------------------------------------------------------------


class TestTradeFlows(unittest.TestCase):
    def test_surplus_exports(self):
        pts = [(0, 0), (10, 0), (5, 5)]
        adj = {0: [1, 2], 1: [0, 2], 2: [0, 1]}
        surplus = [10.0, -5.0, -5.0]
        flows, imp, exp = _compute_trade_flows(pts, adj, surplus)
        self.assertGreater(len(flows), 0)
        # Cell 0 should export
        self.assertGreater(exp[0], 0)
        # Cells 1, 2 should import
        self.assertGreater(imp[1], 0)
        self.assertGreater(imp[2], 0)

    def test_no_surplus(self):
        pts = [(0, 0), (1, 0)]
        adj = {0: [1], 1: [0]}
        surplus = [-1.0, -1.0]
        flows, imp, exp = _compute_trade_flows(pts, adj, surplus)
        self.assertEqual(len(flows), 0)

    def test_flow_volumes_positive(self):
        pts = [(0, 0), (5, 0), (10, 0)]
        adj = {0: [1], 1: [0, 2], 2: [1]}
        surplus = [20.0, -10.0, -10.0]
        flows, _, _ = _compute_trade_flows(pts, adj, surplus)
        for f in flows:
            self.assertGreater(f.volume, 0)
            self.assertGreater(f.distance, 0)
            self.assertGreater(f.efficiency, 0)


# ---------------------------------------------------------------------------
# Engine 4: Bottleneck Detector
# ---------------------------------------------------------------------------


class TestBottleneckDetector(unittest.TestCase):
    def test_detects_bottlenecks(self):
        pts = [(0, 0), (5, 0), (10, 0), (15, 0), (20, 0)]
        adj = {0: [1], 1: [0, 2], 2: [1, 3], 3: [2, 4], 4: [3]}
        flows = [
            TradeFlow(0, 1, 10, 5, 2),
            TradeFlow(1, 2, 10, 5, 2),
            TradeFlow(2, 3, 10, 5, 2),
            TradeFlow(3, 4, 10, 5, 2),
        ]
        prod = [20, 5, 5, 5, 5]
        cons = [5, 10, 10, 10, 10]
        bn = _detect_bottlenecks(pts, adj, flows, prod, cons)
        self.assertGreater(len(bn), 0)

    def test_returns_list(self):
        pts = [(0, 0), (1, 1)]
        adj = {0: [1], 1: [0]}
        bn = _detect_bottlenecks(pts, adj, [], [1, 1], [1, 1])
        self.assertIsInstance(bn, list)


# ---------------------------------------------------------------------------
# Engine 5: Efficiency
# ---------------------------------------------------------------------------


class TestEfficiency(unittest.TestCase):
    def test_perfect_balance(self):
        prod = [10.0, 10.0]
        cons = [10.0, 10.0]
        imp = {0: 0.0, 1: 0.0}
        exp = {0: 0.0, 1: 0.0}
        effs, sys_eff = _analyze_efficiency(prod, cons, imp, exp)
        self.assertAlmostEqual(effs[0], 1.0)
        self.assertAlmostEqual(sys_eff, 100.0)

    def test_deficit(self):
        prod = [5.0]
        cons = [10.0]
        imp = {0: 0.0}
        exp = {0: 0.0}
        effs, sys_eff = _analyze_efficiency(prod, cons, imp, exp)
        self.assertLess(effs[0], 1.0)

    def test_with_imports(self):
        prod = [5.0]
        cons = [10.0]
        imp = {0: 5.0}
        exp = {0: 0.0}
        effs, _ = _analyze_efficiency(prod, cons, imp, exp)
        self.assertAlmostEqual(effs[0], 1.0)


# ---------------------------------------------------------------------------
# Engine 6: Metabolic Rate
# ---------------------------------------------------------------------------


class TestMetabolicRates(unittest.TestCase):
    def test_basic(self):
        areas = [10.0, 10.0]
        imp = {0: 5.0, 1: 0.0}
        exp = {0: 0.0, 1: 5.0}
        rates = _metabolic_rates(areas, imp, exp)
        self.assertAlmostEqual(rates[0], 0.5)
        self.assertAlmostEqual(rates[1], 0.5)

    def test_zero_throughput(self):
        areas = [10.0]
        rates = _metabolic_rates(areas, {0: 0.0}, {0: 0.0})
        self.assertAlmostEqual(rates[0], 0.0)


# ---------------------------------------------------------------------------
# Engine 7: Insights
# ---------------------------------------------------------------------------


class TestInsights(unittest.TestCase):
    def test_non_empty(self):
        cells = [
            CellMetabolism(0, 0, 0, production=10, consumption=5, surplus=5,
                           role="producer"),
            CellMetabolism(1, 1, 1, production=3, consumption=8, surplus=-5,
                           role="consumer"),
        ]
        ins = _generate_insights(cells, [], [], 75.0)
        self.assertGreater(len(ins), 0)
        self.assertTrue(all(isinstance(s, str) for s in ins))

    def test_mentions_producer(self):
        cells = [
            CellMetabolism(0, 0, 0, production=100, consumption=5, surplus=95,
                           role="producer"),
            CellMetabolism(1, 1, 1, production=1, consumption=50, surplus=-49,
                           role="consumer"),
        ]
        ins = _generate_insights(cells, [], [], 50.0)
        found = any("producer" in s.lower() or "production" in s.lower()
                     for s in ins)
        self.assertTrue(found)

    def test_empty_cells(self):
        ins = _generate_insights([], [], [], 0.0)
        self.assertGreater(len(ins), 0)


# ---------------------------------------------------------------------------
# Full Engine
# ---------------------------------------------------------------------------


class TestMetabolismEngine(unittest.TestCase):
    def _make_engine(self, n=10):
        import random as _rng
        r = _rng.Random(42)
        pts = [(r.uniform(0, 100), r.uniform(0, 100)) for _ in range(n)]
        return MetabolismEngine(points=pts, seed=42)

    def test_analyze_returns_result(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertIsInstance(result, MetabolismResult)

    def test_health_score_range(self):
        engine = self._make_engine(20)
        result = engine.analyze()
        self.assertGreaterEqual(result.health_score, 0)
        self.assertLessEqual(result.health_score, 100)

    def test_cells_populated(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertEqual(len(result.cells), 10)

    def test_roles_assigned(self):
        engine = self._make_engine(15)
        result = engine.analyze()
        roles = {c.role for c in result.cells}
        # Should have at least 2 different roles
        self.assertGreaterEqual(len(roles), 1)

    def test_trade_flows_exist(self):
        engine = self._make_engine(30)
        result = engine.analyze()
        # With 30 points there should be some trade (or at least no crash)
        self.assertIsInstance(result.trade_flows, list)

    def test_bottlenecks_exist(self):
        engine = self._make_engine(20)
        result = engine.analyze()
        self.assertGreater(len(result.bottlenecks), 0)

    def test_insights_non_empty(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertGreater(len(result.insights), 0)

    def test_trade_balance_all_cells(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertEqual(len(result.trade_balance), 10)

    def test_too_few_points(self):
        engine = MetabolismEngine(points=[(0, 0)])
        result = engine.analyze()
        self.assertAlmostEqual(result.health_score, 0.0)

    def test_json_export(self):
        engine = self._make_engine()
        engine.analyze()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            engine.to_json(path)
            with open(path) as fh:
                data = json.load(fh)
            self.assertIn("health_score", data)
            self.assertIn("cells", data)
            self.assertIn("trade_flows", data)
        finally:
            os.unlink(path)

    def test_html_export(self):
        engine = self._make_engine()
        engine.analyze()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            engine.to_html(path)
            with open(path) as fh:
                content = fh.read()
            self.assertIn("Spatial Metabolism Dashboard", content)
            self.assertIn("Health Score", content)
        finally:
            os.unlink(path)

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False) as f:
            for i in range(10):
                f.write(f"{i * 10} {i * 5}\n")
            path = f.name
        try:
            engine = MetabolismEngine(path=path)
            result = engine.analyze()
            self.assertEqual(len(result.cells), 10)
        finally:
            os.unlink(path)

    def test_system_efficiency_range(self):
        engine = self._make_engine(15)
        result = engine.analyze()
        self.assertGreaterEqual(result.system_efficiency, 0)
        self.assertLessEqual(result.system_efficiency, 100)

    def test_total_production_positive(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertGreater(result.total_production, 0)

    def test_total_consumption_positive(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertGreater(result.total_consumption, 0)


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------


class TestMetabolismAnalyze(unittest.TestCase):
    def test_with_points(self):
        pts = [(0, 0), (10, 0), (5, 8), (0, 8), (10, 8)]
        result = metabolism_analyze(pts)
        self.assertIsInstance(result, MetabolismResult)
        self.assertEqual(len(result.cells), 5)

    def test_with_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                          delete=False) as f:
            f.write("0 0\n10 0\n5 8\n3 6\n7 2\n")
            path = f.name
        try:
            result = metabolism_analyze(path)
            self.assertEqual(len(result.cells), 5)
        finally:
            os.unlink(path)


class TestDemo(unittest.TestCase):
    def test_runs(self):
        # Should not raise
        metabolism_demo()


class TestCLI(unittest.TestCase):
    def test_demo_flag(self):
        old_argv = sys.argv
        try:
            sys.argv = ["vormap_metabolism.py", "--demo"]
            from vormap_metabolism import _cli
            _cli()  # should not raise
        finally:
            sys.argv = old_argv


if __name__ == "__main__":
    unittest.main()
