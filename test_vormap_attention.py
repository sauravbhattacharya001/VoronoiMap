#!/usr/bin/env python3
"""Tests for vormap_attention.py — Spatial Attention Engine (45+ tests)."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_attention import (
    AttentionEngine,
    AttentionResult,
    CellAttention,
    attention_analyze,
    attention_demo,
    _euclidean,
    _knn_adjacency,
    _voronoi_areas,
    _normalize,
    _shannon_entropy,
    _info_density,
    _change_velocity,
    _strategic_importance,
    _surprise,
    _convergence_zones,
    _DecayTracker,
    _load_points,
)


class TestHelpers(unittest.TestCase):
    """Test utility functions."""

    def test_euclidean_zero(self):
        self.assertAlmostEqual(_euclidean((0, 0), (0, 0)), 0.0)

    def test_euclidean_basic(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_normalize_empty(self):
        self.assertEqual(_normalize([]), [])

    def test_normalize_constant(self):
        result = _normalize([5, 5, 5])
        self.assertTrue(all(v == 0.5 for v in result))

    def test_normalize_range(self):
        result = _normalize([0, 5, 10])
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[1], 0.5)
        self.assertAlmostEqual(result[2], 1.0)

    def test_shannon_entropy_uniform(self):
        # Uniform distribution = max entropy = 1.0
        scores = [1.0, 1.0, 1.0, 1.0]
        self.assertAlmostEqual(_shannon_entropy(scores), 1.0, places=5)

    def test_shannon_entropy_concentrated(self):
        # All attention on one cell = low entropy
        scores = [100.0, 0.001, 0.001, 0.001]
        self.assertLess(_shannon_entropy(scores), 0.1)

    def test_shannon_entropy_empty(self):
        self.assertEqual(_shannon_entropy([]), 0.0)

    def test_knn_adjacency_single(self):
        adj = _knn_adjacency([(0, 0)])
        self.assertEqual(adj[0], [])

    def test_knn_adjacency_two(self):
        adj = _knn_adjacency([(0, 0), (1, 0)])
        self.assertIn(1, adj[0])
        self.assertIn(0, adj[1])

    def test_knn_adjacency_symmetric(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
        adj = _knn_adjacency(pts, k=2)
        for i in range(4):
            for j in adj[i]:
                self.assertIn(i, adj[j])

    def test_voronoi_areas_positive(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, k=2)
        areas = _voronoi_areas(pts, adj)
        self.assertTrue(all(a > 0 for a in areas))


class TestInfoDensity(unittest.TestCase):
    """Test information density engine."""

    def test_returns_correct_length(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
        adj = _knn_adjacency(pts, k=3)
        areas = _voronoi_areas(pts, adj)
        scores = _info_density(pts, adj, areas)
        self.assertEqual(len(scores), 4)

    def test_scores_in_range(self):
        pts = [(i, j) for i in range(5) for j in range(5)]
        adj = _knn_adjacency(pts, k=4)
        areas = _voronoi_areas(pts, adj)
        scores = _info_density(pts, adj, areas)
        for s in scores:
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)

    def test_empty(self):
        self.assertEqual(_info_density([], {}, []), [])


class TestChangeVelocity(unittest.TestCase):
    """Test change velocity engine."""

    def test_no_change(self):
        snap = [(0, 0), (1, 1)]
        scores = _change_velocity([snap, snap])
        self.assertTrue(all(s == 0.5 for s in scores))  # normalized constant

    def test_movement_detected(self):
        snap1 = [(0, 0), (1, 1)]
        snap2 = [(0, 0), (5, 5)]  # cell 1 moved a lot
        scores = _change_velocity([snap1, snap2])
        self.assertGreater(scores[1], scores[0])

    def test_single_snapshot(self):
        scores = _change_velocity([[(0, 0), (1, 1)]])
        self.assertEqual(scores, [0.0, 0.0])


class TestStrategicImportance(unittest.TestCase):
    """Test strategic importance (betweenness) engine."""

    def test_returns_correct_length(self):
        pts = [(0, 0), (5, 0), (10, 0), (5, 5)]
        adj = _knn_adjacency(pts, k=2)
        scores = _strategic_importance(pts, adj)
        self.assertEqual(len(scores), 4)

    def test_scores_in_range(self):
        pts = [(i * 10, 0) for i in range(10)]
        adj = _knn_adjacency(pts, k=3)
        scores = _strategic_importance(pts, adj)
        for s in scores:
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)

    def test_two_points(self):
        pts = [(0, 0), (1, 0)]
        adj = _knn_adjacency(pts, k=1)
        scores = _strategic_importance(pts, adj)
        self.assertEqual(len(scores), 2)


class TestSurprise(unittest.TestCase):
    """Test surprise detector engine."""

    def test_uniform_low_surprise(self):
        # Regular grid = low surprise
        pts = [(i, j) for i in range(4) for j in range(4)]
        adj = _knn_adjacency(pts, k=4)
        areas = _voronoi_areas(pts, adj)
        scores = _surprise(pts, adj, areas)
        # All scores should be relatively similar
        self.assertEqual(len(scores), 16)

    def test_single_point(self):
        scores = _surprise([(0, 0)], {0: []}, [1.0])
        self.assertEqual(scores, [0.0])

    def test_scores_in_range(self):
        pts = [(0, 0), (1, 0), (100, 100), (1, 1)]
        adj = _knn_adjacency(pts, k=2)
        areas = _voronoi_areas(pts, adj)
        scores = _surprise(pts, adj, areas)
        for s in scores:
            self.assertGreaterEqual(s, 0.0)
            self.assertLessEqual(s, 1.0)


class TestConvergenceZones(unittest.TestCase):
    """Test convergence zone detection."""

    def test_no_convergence(self):
        # All low scores
        cell_scores = [{"info_density": 0.1, "strategic_importance": 0.1, "surprise": 0.1}
                       for _ in range(5)]
        adj = {i: [j for j in range(5) if j != i] for i in range(5)}
        scores, zones = _convergence_zones(cell_scores, adj)
        self.assertEqual(zones, [])

    def test_convergence_detected(self):
        cell_scores = [{"info_density": 0.9, "strategic_importance": 0.9, "surprise": 0.9}
                       for _ in range(3)]
        cell_scores.append({"info_density": 0.1, "strategic_importance": 0.1, "surprise": 0.1})
        adj = {0: [1, 2], 1: [0, 2], 2: [0, 1], 3: []}
        scores, zones = _convergence_zones(cell_scores, adj)
        self.assertGreater(len(zones), 0)


class TestDecayTracker(unittest.TestCase):
    """Test attention decay mechanism."""

    def test_initial_factor_is_one(self):
        dt = _DecayTracker(half_life=3)
        self.assertEqual(dt.decay_factor(0), 1.0)

    def test_attended_suppresses(self):
        dt = _DecayTracker(half_life=3)
        dt.attend(0)
        dt.tick()
        factor = dt.decay_factor(0)
        self.assertLess(factor, 1.0)
        self.assertGreater(factor, 0.0)

    def test_decay_recovers(self):
        dt = _DecayTracker(half_life=3)
        dt.attend(0)
        for _ in range(10):
            dt.tick()
        factor = dt.decay_factor(0)
        self.assertGreater(factor, 0.9)

    def test_reset(self):
        dt = _DecayTracker(half_life=3)
        dt.attend(0)
        dt.tick()
        dt.reset()
        self.assertEqual(dt.decay_factor(0), 1.0)


class TestAttentionEngine(unittest.TestCase):
    """Test the main AttentionEngine class."""

    def setUp(self):
        self.points = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2),
                       (15, 5), (12, 12), (1, 10), (8, 15), (20, 20)]

    def test_analyze_basic(self):
        engine = AttentionEngine(points=self.points, k=3)
        result = engine.analyze()
        self.assertIsInstance(result, AttentionResult)
        self.assertEqual(len(result.cells), 10)

    def test_top_k(self):
        engine = AttentionEngine(points=self.points, k=3)
        result = engine.analyze()
        self.assertEqual(len(result.top_k), 3)

    def test_schedule_complete(self):
        engine = AttentionEngine(points=self.points, k=3)
        result = engine.analyze()
        self.assertEqual(len(result.schedule), 10)
        self.assertEqual(sorted(result.schedule), list(range(10)))

    def test_health_score_range(self):
        engine = AttentionEngine(points=self.points)
        result = engine.analyze()
        self.assertGreaterEqual(result.health_score, 0.0)
        self.assertLessEqual(result.health_score, 100.0)

    def test_attention_map(self):
        engine = AttentionEngine(points=self.points)
        result = engine.analyze()
        self.assertEqual(len(result.attention_map), 10)

    def test_attend_reduces_score(self):
        engine = AttentionEngine(points=self.points, k=3)
        result1 = engine.analyze()
        top_cell = result1.top_k[0]
        engine.attend(top_cell)
        result2 = engine.analyze()
        # After attending, score should decrease
        self.assertLessEqual(result2.attention_map[top_cell],
                             result1.attention_map[top_cell])

    def test_reset_decay(self):
        engine = AttentionEngine(points=self.points, k=3)
        engine.analyze()
        engine.attend(0)
        engine.reset_decay()
        result = engine.analyze()
        # After reset, all decay factors should be 1.0
        for c in result.cells:
            self.assertEqual(c.decay_factor, 1.0)

    def test_temporal_analysis(self):
        snap1 = [(0, 0), (1, 1), (2, 2)]
        snap2 = [(0, 0), (1, 2), (2, 3)]
        snap3 = [(0, 0), (1, 3), (2, 4)]
        engine = AttentionEngine(points=snap3, k=2)
        result = engine.analyze_temporal([snap1, snap2, snap3])
        self.assertIsInstance(result, AttentionResult)
        # Cell 0 didn't move, cells 1,2 did
        self.assertGreater(result.cells[1].change_velocity, result.cells[0].change_velocity)

    def test_empty_points(self):
        engine = AttentionEngine(points=[])
        result = engine.analyze()
        self.assertEqual(result.health_score, 0.0)
        self.assertEqual(result.cells, [])

    def test_single_point(self):
        engine = AttentionEngine(points=[(5, 5)])
        result = engine.analyze()
        self.assertEqual(len(result.cells), 1)

    def test_two_points(self):
        engine = AttentionEngine(points=[(0, 0), (10, 10)])
        result = engine.analyze()
        self.assertEqual(len(result.cells), 2)

    def test_collinear_points(self):
        pts = [(i, 0) for i in range(10)]
        engine = AttentionEngine(points=pts, k=3)
        result = engine.analyze()
        self.assertEqual(len(result.cells), 10)

    def test_duplicate_points(self):
        pts = [(5, 5)] * 5
        engine = AttentionEngine(points=pts)
        result = engine.analyze()
        self.assertEqual(len(result.cells), 5)

    def test_custom_weights(self):
        w = {"info_density": 1.0, "change_velocity": 0.0,
             "strategic_importance": 0.0, "surprise": 0.0,
             "convergence": 0.0, "decay": 0.0}
        engine = AttentionEngine(points=self.points, weights=w)
        result = engine.analyze()
        self.assertIsInstance(result, AttentionResult)

    def test_ranks_unique(self):
        engine = AttentionEngine(points=self.points)
        result = engine.analyze()
        ranks = [c.rank for c in result.cells]
        self.assertEqual(sorted(ranks), list(range(1, 11)))


class TestExport(unittest.TestCase):
    """Test JSON and HTML export."""

    def setUp(self):
        self.points = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        self.engine = AttentionEngine(points=self.points, k=3)
        self.engine.analyze()

    def test_json_export(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            self.engine.to_json(path)
            with open(path) as fh:
                data = json.load(fh)
            self.assertIn("health_score", data)
            self.assertIn("top_k", data)
            self.assertIn("cells", data)
            self.assertEqual(len(data["cells"]), 5)
        finally:
            os.unlink(path)

    def test_html_export(self):
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            path = f.name
        try:
            self.engine.to_html(path)
            with open(path) as fh:
                content = fh.read()
            self.assertIn("Spatial Attention Engine", content)
            self.assertIn("canvas", content)
            self.assertIn("health", content.lower())
        finally:
            os.unlink(path)


class TestFileIO(unittest.TestCase):
    """Test file loading and CLI-related paths."""

    def test_load_points(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("0 0\n10 5\n3.5 7.2\n# comment\n\n")
            path = f.name
        try:
            pts = _load_points(path)
            self.assertEqual(len(pts), 3)
            self.assertAlmostEqual(pts[2][0], 3.5)
        finally:
            os.unlink(path)

    def test_engine_from_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("0 0\n10 0\n5 8\n")
            path = f.name
        try:
            engine = AttentionEngine(path=path, k=2)
            result = engine.analyze()
            self.assertEqual(len(result.cells), 3)
        finally:
            os.unlink(path)


class TestConvenienceAPI(unittest.TestCase):
    """Test convenience functions."""

    def test_attention_analyze(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
        result = attention_analyze(pts, top_k=2)
        self.assertIsInstance(result, AttentionResult)
        self.assertEqual(len(result.top_k), 2)

    def test_attention_demo(self):
        # Should run without errors
        result = attention_demo()
        self.assertIsInstance(result, AttentionResult)
        self.assertGreater(len(result.cells), 0)
        # Clean up demo files
        for f in ["attention_demo.html", "attention_demo.json"]:
            if os.path.exists(f):
                os.unlink(f)


class TestEngineContributions(unittest.TestCase):
    """Test engine contribution reporting."""

    def test_contributions_present(self):
        pts = [(i, j) for i in range(5) for j in range(5)]
        engine = AttentionEngine(points=pts)
        result = engine.analyze()
        self.assertIn("info_density", result.engine_contributions)
        self.assertIn("strategic_importance", result.engine_contributions)
        self.assertIn("surprise", result.engine_contributions)
        self.assertIn("convergence", result.engine_contributions)
        self.assertIn("change_velocity", result.engine_contributions)


if __name__ == "__main__":
    unittest.main()
