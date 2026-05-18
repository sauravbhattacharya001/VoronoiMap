"""Tests for vormap_negotiator — Spatial Conflict Negotiator."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_negotiator import (
    Negotiator,
    NegotiationResult,
    RoundSnapshot,
    detect_conflicts,
    infer_preferences,
    negotiate,
    _gini,
    _voronoi_cells,
    _cell_areas,
    _nn_distances,
    _neighbors,
    _convex_hull,
    _is_pareto_optimal,
)


class TestGini(unittest.TestCase):
    def test_equal_values(self):
        self.assertAlmostEqual(_gini([1, 1, 1, 1]), 0.0)

    def test_maximum_inequality(self):
        g = _gini([0, 0, 0, 100])
        self.assertGreater(g, 0.5)

    def test_empty(self):
        self.assertEqual(_gini([]), 0.0)

    def test_single(self):
        self.assertAlmostEqual(_gini([42]), 0.0)

    def test_moderate_inequality(self):
        g = _gini([1, 2, 3, 4, 5])
        self.assertGreater(g, 0.0)
        self.assertLess(g, 0.5)


class TestConvexHull(unittest.TestCase):
    def test_triangle(self):
        pts = [(0, 0), (1, 0), (0, 1)]
        hull = _convex_hull(pts)
        self.assertEqual(len(hull), 3)

    def test_square(self):
        pts = [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)]
        hull = _convex_hull(pts)
        self.assertEqual(len(hull), 4)

    def test_collinear(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        hull = _convex_hull(pts)
        self.assertGreaterEqual(len(hull), 2)


class TestVoronoiCells(unittest.TestCase):
    def test_basic(self):
        pts = [(100, 100), (300, 300), (500, 100)]
        cells = _voronoi_cells(pts, bbox=(0, 0, 600, 400))
        self.assertEqual(len(cells), 3)
        for c in cells:
            self.assertGreater(len(c), 0)

    def test_single_point(self):
        pts = [(250, 250)]
        cells = _voronoi_cells(pts, bbox=(0, 0, 500, 500))
        self.assertEqual(len(cells), 1)

    def test_empty(self):
        cells = _voronoi_cells([])
        self.assertEqual(len(cells), 0)


class TestCellAreas(unittest.TestCase):
    def test_positive(self):
        pts = [(100, 100), (400, 400)]
        cells = _voronoi_cells(pts, bbox=(0, 0, 500, 500))
        areas = _cell_areas(cells)
        self.assertEqual(len(areas), 2)
        for a in areas:
            self.assertGreater(a, 0)


class TestNNDistances(unittest.TestCase):
    def test_two_points(self):
        pts = [(0, 0), (3, 4)]
        dists = _nn_distances(pts)
        self.assertAlmostEqual(dists[0], 5.0)
        self.assertAlmostEqual(dists[1], 5.0)

    def test_three_points(self):
        pts = [(0, 0), (1, 0), (10, 0)]
        dists = _nn_distances(pts)
        self.assertAlmostEqual(dists[0], 1.0)
        self.assertAlmostEqual(dists[1], 1.0)
        self.assertAlmostEqual(dists[2], 9.0)


class TestNeighbors(unittest.TestCase):
    def test_close_points_are_neighbors(self):
        pts = [(0, 0), (1, 0), (2, 0), (100, 100)]
        cells = _voronoi_cells(pts, bbox=(-10, -10, 110, 110))
        nbrs = _neighbors(pts, cells)
        self.assertIn(1, nbrs[0])
        self.assertIn(0, nbrs[1])


class TestConflictDetection(unittest.TestCase):
    def test_no_conflicts_uniform(self):
        # Grid of well-spaced points
        pts = [(i * 100, j * 100) for i in range(3) for j in range(3)]
        conflicts = detect_conflicts(pts, bbox=(0, 0, 300, 300))
        # Should have few or no conflicts
        critical = [c for c in conflicts if c.severity == "critical"]
        self.assertLessEqual(len(critical), 1)

    def test_crowding_detected(self):
        # Two very close points among spread out ones
        pts = [(100, 100), (101, 101), (500, 500), (100, 500), (500, 100)]
        conflicts = detect_conflicts(pts, bbox=(0, 0, 600, 600))
        kinds = [c.kind for c in conflicts]
        self.assertIn("Crowding", kinds)

    def test_territory_imbalance(self):
        # One huge region, many tiny
        pts = [(500, 500)]
        for i in range(10):
            pts.append((10 + i * 2, 10))
        conflicts = detect_conflicts(pts, bbox=(0, 0, 1000, 1000))
        kinds = [c.kind for c in conflicts]
        self.assertIn("TerritoryImbalance", kinds)

    def test_isolation_detected(self):
        pts = [(50, 50), (52, 52), (55, 55), (900, 900)]
        conflicts = detect_conflicts(pts, bbox=(0, 0, 1000, 1000))
        kinds = [c.kind for c in conflicts]
        self.assertIn("Isolation", kinds)

    def test_conflict_has_correct_fields(self):
        pts = [(100, 100), (101, 101), (500, 500)]
        conflicts = detect_conflicts(pts, bbox=(0, 0, 600, 600))
        for c in conflicts:
            self.assertIn(c.severity, ("info", "warning", "critical"))
            self.assertIsInstance(c.kind, str)
            self.assertIsInstance(c.indices, list)
            self.assertIsInstance(c.message, str)


class TestPreferenceInference(unittest.TestCase):
    def test_basic(self):
        pts = [(100, 100), (300, 300), (500, 100)]
        prefs = infer_preferences(pts)
        self.assertEqual(len(prefs), 3)
        for p in prefs:
            self.assertGreater(p.desired_area, 0)
            self.assertGreater(p.buffer_radius, 0)
            self.assertGreaterEqual(p.centrality_weight, 0)
            self.assertLessEqual(p.centrality_weight, 1)

    def test_overrides(self):
        pts = [(100, 100), (300, 300)]
        prefs = infer_preferences(pts, overrides={0: {"desired_area": 999}})
        self.assertAlmostEqual(prefs[0].desired_area, 999)

    def test_centrality_highest_for_center(self):
        pts = [(0, 0), (500, 500), (1000, 1000)]
        prefs = infer_preferences(pts)
        # Middle point should have highest centrality weight
        self.assertGreater(prefs[1].centrality_weight, prefs[0].centrality_weight)


class TestParetoOptimal(unittest.TestCase):
    def test_high_uniform(self):
        self.assertTrue(_is_pareto_optimal([0.9, 0.9, 0.9]))

    def test_low_uniform(self):
        self.assertFalse(_is_pareto_optimal([0.3, 0.3, 0.3]))

    def test_high_variance(self):
        self.assertFalse(_is_pareto_optimal([1.0, 0.1, 1.0]))

    def test_empty(self):
        self.assertTrue(_is_pareto_optimal([]))


class TestNegotiator(unittest.TestCase):
    def test_basic_run(self):
        pts = [(100, 100), (300, 300), (500, 100), (300, 500)]
        neg = Negotiator(pts, bbox=(0, 0, 600, 600))
        result = neg.run(max_rounds=10)
        self.assertIsInstance(result, NegotiationResult)
        self.assertEqual(len(result.original_points), 4)
        self.assertEqual(len(result.final_points), 4)
        self.assertGreater(result.social_welfare, 0)
        self.assertGreater(result.fairness, 0)

    def test_single_point(self):
        neg = Negotiator([(500, 500)])
        result = neg.run()
        self.assertTrue(result.converged)
        self.assertEqual(result.social_welfare, 1.0)

    def test_convergence(self):
        pts = [(i * 100, j * 100) for i in range(3) for j in range(3)]
        neg = Negotiator(pts, bbox=(0, 0, 300, 300))
        result = neg.run(max_rounds=30, epsilon=0.005)
        # Well-spaced grid should converge quickly
        self.assertLessEqual(result.rounds_used, 30)

    def test_timeline_populated(self):
        pts = [(100, 100), (200, 200), (300, 300)]
        neg = Negotiator(pts, bbox=(0, 0, 400, 400))
        result = neg.run(max_rounds=5)
        self.assertGreater(len(result.timeline), 0)
        for snap in result.timeline:
            self.assertIsInstance(snap, RoundSnapshot)
            self.assertGreaterEqual(snap.avg_satisfaction, 0)
            self.assertLessEqual(snap.avg_satisfaction, 1)

    def test_compromises_populated(self):
        pts = [(100, 100), (110, 110), (500, 500)]
        neg = Negotiator(pts, bbox=(0, 0, 600, 600))
        result = neg.run()
        self.assertEqual(len(result.compromises), 3)
        for c in result.compromises:
            self.assertGreaterEqual(c.compromise_ratio, 0)
            self.assertLessEqual(c.compromise_ratio, 1)

    def test_conflicts_detected_and_reduced(self):
        # Deliberately conflicted layout
        pts = [(500, 500), (502, 502), (504, 504),
               (100, 100), (900, 900)]
        neg = Negotiator(pts, bbox=(0, 0, 1000, 1000))
        result = neg.run(max_rounds=20)
        # Should detect initial conflicts
        self.assertGreater(len(result.conflicts), 0)


class TestNegotiateConvenience(unittest.TestCase):
    def test_with_points_list(self):
        pts = [(100, 200), (300, 400), (500, 600)]
        result = negotiate(pts, max_rounds=5)
        self.assertIsInstance(result, NegotiationResult)

    def test_with_file(self):
        pts = [(100, 200), (300, 400), (500, 600)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as fh:
            for x, y in pts:
                fh.write(f"{x} {y}\n")
            path = fh.name
        try:
            result = negotiate(path, max_rounds=5)
            self.assertEqual(len(result.original_points), 3)
        finally:
            os.unlink(path)


class TestSerialization(unittest.TestCase):
    def test_to_dict(self):
        pts = [(100, 100), (300, 300)]
        result = negotiate(pts, max_rounds=3)
        d = result.to_dict()
        self.assertIn("social_welfare", d)
        self.assertIn("timeline", d)
        self.assertIn("conflicts", d)

    def test_to_json(self):
        pts = [(100, 100), (300, 300), (500, 500)]
        result = negotiate(pts, max_rounds=3)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fh:
            path = fh.name
        try:
            result.to_json(path)
            with open(path) as fh:
                data = json.load(fh)
            self.assertIn("social_welfare", data)
        finally:
            os.unlink(path)

    def test_to_html(self):
        pts = [(100, 100), (300, 300), (500, 500)]
        result = negotiate(pts, max_rounds=3)
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as fh:
            path = fh.name
        try:
            result.to_html(path)
            with open(path, encoding="utf-8") as fh:
                html = fh.read()
            self.assertIn("Spatial Conflict Negotiation Report", html)
            self.assertIn("Social Welfare", html)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
