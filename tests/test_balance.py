"""Tests for vormap_balance - Autonomous Spatial Load Balancer.

Covers equity metrics (Gini, CV, Shannon entropy, equity score), the
bounding-box Voronoi cell sampler, every rebalancing strategy, the
auto-rebalance convergence loop, the recommendations engine, and the
small CSV / bbox / demo-data helpers used by the CLI.
"""

import csv
import json
import math
import os
import sys
import tempfile
import unittest

# Make the repo root importable when pytest is invoked from anywhere.
HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from vormap_balance import (
    STRATEGIES,
    _grid_resolution,
    _voronoi_cells_pure,
    _voronoi_cells_simple,
    auto_rebalance,
    coeff_of_variation,
    compute_bbox,
    equity_score,
    generate_demo_points,
    generate_html_report,
    generate_recommendations,
    gini_coefficient,
    max_entropy,
    read_points,
    rebalance_adaptive,
    rebalance_centroid,
    rebalance_repulsion,
    shannon_entropy,
    write_points,
)

import vormap_balance as _vb


# ---------------------------------------------------------------------------
# Equity metrics
# ---------------------------------------------------------------------------

class TestGini(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(gini_coefficient([]), 0.0)

    def test_all_zero(self):
        self.assertEqual(gini_coefficient([0, 0, 0]), 0.0)

    def test_perfectly_equal(self):
        # Identical values => Gini = 0.
        self.assertAlmostEqual(gini_coefficient([5, 5, 5, 5]), 0.0)

    def test_maximally_unequal(self):
        # One person has everything: Gini approaches (n-1)/n.
        g = gini_coefficient([0, 0, 0, 100])
        self.assertAlmostEqual(g, 0.75, places=6)

    def test_bounded_unit_interval(self):
        import random
        rng = random.Random(0)
        for _ in range(20):
            vals = [rng.random() for _ in range(rng.randint(2, 30))]
            g = gini_coefficient(vals)
            self.assertGreaterEqual(g, 0.0)
            self.assertLessEqual(g, 1.0)

    def test_scale_invariant(self):
        vals = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(
            gini_coefficient(vals), gini_coefficient([v * 7.5 for v in vals])
        )


class TestCoeffOfVariation(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(coeff_of_variation([]), 0.0)

    def test_zero_mean(self):
        self.assertEqual(coeff_of_variation([0, 0, 0]), 0.0)

    def test_constant(self):
        self.assertAlmostEqual(coeff_of_variation([4, 4, 4, 4]), 0.0)

    def test_known(self):
        # mean = 3, var = 2 (population), stdev = sqrt(2), cv = sqrt(2)/3
        self.assertAlmostEqual(
            coeff_of_variation([1, 2, 3, 4, 5]), math.sqrt(2) / 3
        )


class TestEntropy(unittest.TestCase):
    def test_zero_total(self):
        self.assertEqual(shannon_entropy([0, 0]), 0.0)
        self.assertEqual(shannon_entropy([]), 0.0)

    def test_uniform_equals_max(self):
        n = 8
        self.assertAlmostEqual(shannon_entropy([1] * n), max_entropy(n))

    def test_concentrated_is_low(self):
        h = shannon_entropy([10, 0, 0, 0])
        self.assertAlmostEqual(h, 0.0)

    def test_max_entropy(self):
        self.assertEqual(max_entropy(0), 0.0)
        self.assertAlmostEqual(max_entropy(4), 2.0)


class TestEquityScore(unittest.TestCase):
    def test_perfect(self):
        self.assertAlmostEqual(equity_score([7, 7, 7]), 100.0)

    def test_unequal_drops(self):
        self.assertLess(equity_score([1, 1, 1, 100]), 50.0)


# ---------------------------------------------------------------------------
# Bounding-box / demo helpers
# ---------------------------------------------------------------------------

class TestComputeBbox(unittest.TestCase):
    def test_margin_applied(self):
        pts = [(0.0, 0.0), (10.0, 10.0), (5.0, 5.0)]
        x0, y0, x1, y1 = compute_bbox(pts, margin=0.1)
        # Margin is 0.1 * range = 1.0 on each side.
        self.assertAlmostEqual(x0, -1.0)
        self.assertAlmostEqual(y0, -1.0)
        self.assertAlmostEqual(x1, 11.0)
        self.assertAlmostEqual(y1, 11.0)

    def test_zero_margin(self):
        pts = [(2.0, 3.0), (8.0, 9.0)]
        self.assertEqual(compute_bbox(pts, margin=0.0), (2.0, 3.0, 8.0, 9.0))


class TestDemoPoints(unittest.TestCase):
    def test_count_and_determinism(self):
        a = generate_demo_points(n=30, seed=42)
        b = generate_demo_points(n=30, seed=42)
        self.assertEqual(len(a), 30)
        self.assertEqual(a, b)

    def test_different_seeds_differ(self):
        a = generate_demo_points(n=30, seed=1)
        b = generate_demo_points(n=30, seed=2)
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------
# Voronoi cell sampler
# ---------------------------------------------------------------------------

class TestVoronoiCells(unittest.TestCase):
    def setUp(self):
        self.bbox = (0.0, 0.0, 1.0, 1.0)
        self.points = [(0.25, 0.25), (0.75, 0.25), (0.5, 0.75)]

    def test_shape(self):
        areas, centroids = _voronoi_cells_simple(self.points, self.bbox)
        self.assertEqual(len(areas), len(self.points))
        self.assertEqual(len(centroids), len(self.points))

    def test_total_area_close_to_bbox(self):
        areas, _ = _voronoi_cells_simple(self.points, self.bbox)
        total = sum(areas)
        bbox_area = (self.bbox[2] - self.bbox[0]) * (self.bbox[3] - self.bbox[1])
        # Discrete sampler — allow small slack.
        self.assertGreater(total, 0.95 * bbox_area)
        self.assertLessEqual(total, bbox_area + 1e-9)

    def test_centroids_inside_bbox(self):
        _, centroids = _voronoi_cells_simple(self.points, self.bbox)
        x0, y0, x1, y1 = self.bbox
        for cx, cy in centroids:
            self.assertGreaterEqual(cx, x0 - 1e-9)
            self.assertLessEqual(cx, x1 + 1e-9)
            self.assertGreaterEqual(cy, y0 - 1e-9)
            self.assertLessEqual(cy, y1 + 1e-9)

    def test_symmetric_two_point(self):
        # Two mirrored points should split the bbox roughly in half.
        bbox = (0.0, 0.0, 2.0, 2.0)
        areas, _ = _voronoi_cells_simple([(0.5, 1.0), (1.5, 1.0)], bbox)
        self.assertAlmostEqual(areas[0], areas[1], delta=0.05)

    def test_grid_resolution_bounds(self):
        # Lower bound preserved for small n; cap kicks in for large n.
        self.assertEqual(_grid_resolution(1), 80)
        self.assertEqual(_grid_resolution(0), 80)
        self.assertEqual(_grid_resolution(10), 80)
        self.assertLessEqual(_grid_resolution(1_000_000), 400)

    def test_pure_fallback_matches_simple_when_no_scipy(self):
        # Force the pure-Python path and confirm it matches the dispatcher
        # when scipy isn't available.
        bbox = (0.0, 0.0, 1.0, 1.0)
        pts = generate_demo_points(n=12, seed=99)
        pure_areas, pure_centroids = _voronoi_cells_pure(
            pts, bbox, _grid_resolution(len(pts))
        )
        # The pure helper must agree with itself when called via the simple
        # dispatcher's fallback branch (we temporarily disable scipy).
        saved_np, saved_tree = _vb._np, _vb._cKDTree
        try:
            _vb._cKDTree = None
            disp_areas, disp_centroids = _voronoi_cells_simple(pts, bbox)
        finally:
            _vb._np, _vb._cKDTree = saved_np, saved_tree
        for a, b in zip(pure_areas, disp_areas):
            self.assertAlmostEqual(a, b, places=10)
        for (ax, ay), (bx, by) in zip(pure_centroids, disp_centroids):
            self.assertAlmostEqual(ax, bx, places=10)
            self.assertAlmostEqual(ay, by, places=10)

    def test_kdtree_matches_pure(self):
        # When scipy is available, the accelerator must produce the same
        # areas/centroids as the pure-Python reference.
        if _vb._np is None or _vb._cKDTree is None:
            self.skipTest('scipy / numpy not installed')
        bbox = (0.0, 0.0, 1.0, 1.0)
        pts = generate_demo_points(n=20, seed=5)
        res = _grid_resolution(len(pts))
        pure_areas, pure_centroids = _voronoi_cells_pure(pts, bbox, res)
        kd_areas, kd_centroids = _vb._voronoi_cells_kdtree(pts, bbox, res)
        for a, b in zip(pure_areas, kd_areas):
            self.assertAlmostEqual(a, b, places=9)
        for (ax, ay), (bx, by) in zip(pure_centroids, kd_centroids):
            self.assertAlmostEqual(ax, bx, places=9)
            self.assertAlmostEqual(ay, by, places=9)


# ---------------------------------------------------------------------------
# Rebalancing strategies
# ---------------------------------------------------------------------------

class TestRebalancingStrategies(unittest.TestCase):
    def setUp(self):
        self.bbox = (0.0, 0.0, 1.0, 1.0)
        self.points = generate_demo_points(n=24, seed=7)
        self.areas, self.centroids = _voronoi_cells_simple(self.points, self.bbox)

    def _assert_in_bbox(self, pts):
        x0, y0, x1, y1 = self.bbox
        for px, py in pts:
            self.assertGreaterEqual(px, x0)
            self.assertLessEqual(px, x1)
            self.assertGreaterEqual(py, y0)
            self.assertLessEqual(py, y1)

    def test_centroid_clipped_to_bbox(self):
        out = rebalance_centroid(self.points, self.areas, self.centroids, self.bbox, strength=0.5)
        self.assertEqual(len(out), len(self.points))
        self._assert_in_bbox(out)

    def test_centroid_moves_toward_centroid(self):
        # With strength=1.0 the new point should equal the centroid (then clipped).
        out = rebalance_centroid(self.points, self.areas, self.centroids, self.bbox, strength=1.0)
        for new, c in zip(out, self.centroids):
            self.assertAlmostEqual(new[0], max(0.0, min(1.0, c[0])), places=9)
            self.assertAlmostEqual(new[1], max(0.0, min(1.0, c[1])), places=9)

    def test_centroid_strength_zero_is_noop(self):
        out = rebalance_centroid(self.points, self.areas, self.centroids, self.bbox, strength=0.0)
        for a, b in zip(out, self.points):
            self.assertAlmostEqual(a[0], b[0])
            self.assertAlmostEqual(a[1], b[1])

    def test_repulsion_in_bbox(self):
        out = rebalance_repulsion(self.points, self.areas, self.centroids, self.bbox, strength=0.3)
        self.assertEqual(len(out), len(self.points))
        self._assert_in_bbox(out)

    def test_adaptive_in_bbox(self):
        out = rebalance_adaptive(self.points, self.areas, self.centroids, self.bbox, strength=0.5)
        self.assertEqual(len(out), len(self.points))
        self._assert_in_bbox(out)

    def test_strategies_registry(self):
        self.assertIn('centroid', STRATEGIES)
        self.assertIn('repulsion', STRATEGIES)
        self.assertIn('adaptive', STRATEGIES)


# ---------------------------------------------------------------------------
# Auto-rebalance loop
# ---------------------------------------------------------------------------

class TestAutoRebalance(unittest.TestCase):
    def test_history_shape(self):
        pts = generate_demo_points(n=18, seed=3)
        bbox = compute_bbox(pts)
        final, history = auto_rebalance(
            pts, bbox, strategy='centroid', target_gini=0.0, max_iters=4
        )
        # History contains iteration 0 plus up to max_iters more entries.
        self.assertGreaterEqual(len(history), 2)
        self.assertLessEqual(len(history), 5)
        self.assertEqual(len(final), len(pts))
        for key in ('iteration', 'gini', 'cv', 'equity_score', 'entropy', 'points', 'areas'):
            self.assertIn(key, history[0])

    def test_target_gini_short_circuits(self):
        pts = generate_demo_points(n=12, seed=11)
        bbox = compute_bbox(pts)
        # A huge target is satisfied immediately after iteration 0.
        _, history = auto_rebalance(
            pts, bbox, strategy='adaptive', target_gini=1.0, max_iters=10
        )
        # Should stop after the first rebalanced iteration (iter 1) at the latest.
        self.assertLessEqual(len(history), 3)

    def test_clustered_input_improves_or_holds(self):
        pts = generate_demo_points(n=24, seed=42)
        bbox = compute_bbox(pts)
        _, history = auto_rebalance(
            pts, bbox, strategy='centroid', target_gini=0.0,
            max_iters=8, strength=0.6,
        )
        # Rebalancing should not make a clustered distribution worse overall.
        self.assertLessEqual(history[-1]['gini'], history[0]['gini'] + 1e-6)


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

class TestRecommendations(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(generate_recommendations([]), [])

    def _entry(self, gini, areas):
        return {
            'iteration': 0,
            'gini': gini,
            'cv': 0.0,
            'equity_score': 0.0,
            'entropy': 0.0,
            'points': [],
            'areas': areas,
        }

    def test_high_inequality_flagged(self):
        history = [self._entry(0.5, [1, 1, 1, 50])]
        recs = generate_recommendations(history)
        severities = {r['severity'] for r in recs}
        self.assertIn('HIGH', severities)

    def test_balanced_gets_ok(self):
        history = [self._entry(0.05, [10, 10, 10, 10])]
        recs = generate_recommendations(history)
        self.assertTrue(any(r['severity'] == 'OK' for r in recs))

    def test_stalled_progress_warns(self):
        # Long history with the same Gini = no progress.
        history = [self._entry(0.4, [1, 1, 1, 10]) for _ in range(8)]
        recs = generate_recommendations(history)
        self.assertTrue(any(r['severity'] == 'WARN' for r in recs))


# ---------------------------------------------------------------------------
# CSV I/O and HTML report
# ---------------------------------------------------------------------------

class TestCsvIO(unittest.TestCase):
    def test_roundtrip(self):
        pts = [(0.123456, 0.987654), (1.0, -2.5), (3.14, 2.72)]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'pts.csv')
            write_points(path, pts)
            read = read_points(path)
        self.assertEqual(len(read), len(pts))
        for (a, b), (c, d) in zip(read, pts):
            self.assertAlmostEqual(a, c, places=5)
            self.assertAlmostEqual(b, d, places=5)

    def test_reader_skips_comments_and_bad_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'mix.csv')
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['# this is a comment'])
                w.writerow(['x', 'y'])  # header (non-numeric, skipped)
                w.writerow([1.0, 2.0])
                w.writerow([])
                w.writerow(['oops'])
                w.writerow([3.5, 4.5])
            pts = read_points(path)
        self.assertEqual(pts, [(1.0, 2.0), (3.5, 4.5)])


class TestHtmlReport(unittest.TestCase):
    def test_renders_valid_html_with_embedded_data(self):
        pts = generate_demo_points(n=10, seed=0)
        bbox = compute_bbox(pts)
        _, history = auto_rebalance(
            pts, bbox, strategy='centroid', target_gini=0.0, max_iters=2
        )
        recs = generate_recommendations(history)
        html = generate_html_report(history, recs, bbox)
        self.assertTrue(html.startswith('<!DOCTYPE html>'))
        self.assertIn('Voronoi Spatial Balance Report', html)
        # The embedded JSON arrays must parse.
        # Pull the gini data assignment and ensure it's valid JSON.
        marker = 'const giniData = '
        idx = html.find(marker)
        self.assertGreater(idx, 0)
        tail = html[idx + len(marker):]
        end = tail.find(';')
        json.loads(tail[:end])


if __name__ == '__main__':
    unittest.main()
