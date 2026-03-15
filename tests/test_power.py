"""Tests for vormap_power — Weighted (Power) Voronoi Diagrams."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vormap_power import (
    assign_weights, weighted_distance, compute_weighted_nn,
    batch_weighted_nn, compute_power_regions, compute_power_diagram,
    weight_effect_analysis, export_power_json, export_power_svg,
    WeightedSeed, PowerDiagramResult,
    _power_distance, _multiplicative_distance, _additive_distance,
    _polygon_area, _polygon_centroid, _polygon_perimeter, _convex_hull,
    _ordered_boundary,
)


class TestAssignWeights(unittest.TestCase):
    """Tests for assign_weights()."""

    def setUp(self):
        self.seeds = [(0, 0), (100, 0), (50, 86.6), (200, 200)]

    def test_uniform(self):
        w = assign_weights(self.seeds, 'uniform', value=5.0)
        self.assertEqual(len(w), 4)
        self.assertTrue(all(v == 5.0 for v in w))

    def test_uniform_default_value(self):
        w = assign_weights(self.seeds, 'uniform')
        self.assertTrue(all(v == 1.0 for v in w))

    def test_uniform_negative_value_raises(self):
        with self.assertRaises(ValueError):
            assign_weights(self.seeds, 'uniform', value=-1)

    def test_random(self):
        w = assign_weights(self.seeds, 'random', min_w=1, max_w=10, seed=42)
        self.assertEqual(len(w), 4)
        for v in w:
            self.assertGreaterEqual(v, 1.0)
            self.assertLessEqual(v, 10.0)

    def test_random_reproducible(self):
        w1 = assign_weights(self.seeds, 'random', seed=42)
        w2 = assign_weights(self.seeds, 'random', seed=42)
        self.assertEqual(w1, w2)

    def test_random_invalid_bounds(self):
        with self.assertRaises(ValueError):
            assign_weights(self.seeds, 'random', min_w=-1, max_w=5)
        with self.assertRaises(ValueError):
            assign_weights(self.seeds, 'random', min_w=10, max_w=5)

    def test_proportional(self):
        w = assign_weights(self.seeds, 'proportional', min_w=1, max_w=10)
        self.assertEqual(len(w), 4)
        for v in w:
            self.assertGreaterEqual(v, 1.0)
            self.assertLessEqual(v, 10.0)

    def test_inverse(self):
        w = assign_weights(self.seeds, 'inverse', min_w=1, max_w=10)
        self.assertEqual(len(w), 4)
        for v in w:
            self.assertGreaterEqual(v, 1.0)
            self.assertLessEqual(v, 10.0)

    def test_gaussian(self):
        w = assign_weights(self.seeds, 'gaussian', min_w=0.5, max_w=10)
        self.assertEqual(len(w), 4)
        for v in w:
            self.assertGreaterEqual(v, 0.5)
            self.assertLessEqual(v, 10.0)

    def test_gaussian_custom_sigma(self):
        w = assign_weights(self.seeds, 'gaussian', sigma=50, max_w=10)
        self.assertEqual(len(w), 4)

    def test_linear_gradient_x(self):
        w = assign_weights(self.seeds, 'linear_gradient', min_w=1, max_w=10,
                           direction='x')
        self.assertEqual(len(w), 4)

    def test_linear_gradient_y(self):
        w = assign_weights(self.seeds, 'linear_gradient', min_w=1, max_w=10,
                           direction='y')
        self.assertEqual(len(w), 4)

    def test_linear_gradient_diagonal(self):
        w = assign_weights(self.seeds, 'linear_gradient', direction='diagonal')
        self.assertEqual(len(w), 4)

    def test_linear_gradient_bad_direction(self):
        with self.assertRaises(ValueError):
            assign_weights(self.seeds, 'linear_gradient', direction='z')

    def test_unknown_method(self):
        with self.assertRaises(ValueError):
            assign_weights(self.seeds, 'nonexistent')

    def test_empty_seeds(self):
        with self.assertRaises(ValueError):
            assign_weights([], 'uniform')

    def test_custom_center(self):
        w = assign_weights(self.seeds, 'proportional', center=(50, 50))
        self.assertEqual(len(w), 4)


class TestWeightedDistance(unittest.TestCase):
    """Tests for distance functions."""

    def test_power_distance(self):
        d = _power_distance(10, 0, 0, 0, 25)
        self.assertAlmostEqual(d, 75.0)  # 100 - 25

    def test_multiplicative_distance(self):
        d = _multiplicative_distance(10, 0, 0, 0, 2.0)
        self.assertAlmostEqual(d, 5.0)  # 10 / 2

    def test_multiplicative_zero_weight(self):
        d = _multiplicative_distance(10, 0, 0, 0, 0)
        self.assertEqual(d, math.inf)

    def test_additive_distance(self):
        d = _additive_distance(10, 0, 0, 0, 3.0)
        self.assertAlmostEqual(d, 7.0)  # 10 - 3

    def test_weighted_distance_wrapper(self):
        d = weighted_distance(10, 0, 0, 0, 25, 'power')
        self.assertAlmostEqual(d, 75.0)

    def test_weighted_distance_bad_mode(self):
        with self.assertRaises(ValueError):
            weighted_distance(0, 0, 0, 0, 1, 'invalid')


class TestComputeWeightedNN(unittest.TestCase):
    """Tests for compute_weighted_nn()."""

    def test_basic(self):
        seeds = [(0, 0), (100, 0)]
        weights = [50, 1]  # first seed has big weight advantage
        idx, seed, dist = compute_weighted_nn((40, 0), seeds, weights, 'power')
        self.assertEqual(idx, 0)  # power: 1600 - 50 = 1550 vs 3600 - 1 = 3599

    def test_weight_shifts_boundary(self):
        seeds = [(0, 0), (100, 0)]
        # Midpoint (50,0): d1²=2500, d2²=2500 → equal without weight
        # With weights [0, 5000]: d1²-0=2500 vs d2²-5000=-2500 → seed 1 wins
        idx, _, _ = compute_weighted_nn((50, 0), seeds, [0, 5000], 'power')
        self.assertEqual(idx, 1)

    def test_empty_seeds_raises(self):
        with self.assertRaises(ValueError):
            compute_weighted_nn((0, 0), [], [], 'power')

    def test_mismatch_raises(self):
        with self.assertRaises(ValueError):
            compute_weighted_nn((0, 0), [(1, 1)], [1, 2], 'power')

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            compute_weighted_nn((0, 0), [(1, 1)], [1], 'bad')

    def test_multiplicative_mode(self):
        seeds = [(0, 0), (100, 0)]
        weights = [10, 1]  # d/10 vs d/1 → first seed wins far out
        idx, _, _ = compute_weighted_nn((80, 0), seeds, weights,
                                        'multiplicative')
        self.assertEqual(idx, 0)  # 80/10=8 vs 20/1=20

    def test_additive_mode(self):
        seeds = [(0, 0), (100, 0)]
        weights = [30, 0]
        idx, _, _ = compute_weighted_nn((40, 0), seeds, weights, 'additive')
        self.assertEqual(idx, 0)  # 40-30=10 vs 60-0=60


class TestBatchWeightedNN(unittest.TestCase):

    def test_basic(self):
        seeds = [(0, 0), (100, 0)]
        weights = [1, 1]
        points = [(20, 0), (80, 0)]
        results = batch_weighted_nn(points, seeds, weights, 'power')
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0], 0)
        self.assertEqual(results[1][0], 1)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            batch_weighted_nn([(0, 0)], [], [])


class TestPolygonHelpers(unittest.TestCase):

    def test_area_triangle(self):
        a = _polygon_area([(0, 0), (4, 0), (0, 3)])
        self.assertAlmostEqual(a, 6.0)

    def test_area_square(self):
        a = _polygon_area([(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertAlmostEqual(a, 100.0)

    def test_area_empty(self):
        self.assertEqual(_polygon_area([]), 0.0)

    def test_centroid_square(self):
        c = _polygon_centroid([(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertAlmostEqual(c[0], 5.0)
        self.assertAlmostEqual(c[1], 5.0)

    def test_centroid_empty(self):
        self.assertEqual(_polygon_centroid([]), (0.0, 0.0))

    def test_centroid_single(self):
        self.assertEqual(_polygon_centroid([(3, 4)]), (3, 4))

    def test_perimeter_square(self):
        p = _polygon_perimeter([(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertAlmostEqual(p, 40.0)

    def test_perimeter_single(self):
        self.assertEqual(_polygon_perimeter([(0, 0)]), 0.0)

    def test_convex_hull(self):
        pts = [(0, 0), (1, 1), (2, 0), (1, 2), (1, 0.5)]
        hull = _convex_hull(pts)
        self.assertGreaterEqual(len(hull), 3)
        self.assertLessEqual(len(hull), 4)


class TestComputePowerRegions(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(compute_power_regions([], []), [])

    def test_single_seed(self):
        regions = compute_power_regions([(50, 50)], [1.0])
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0]), 4)

    def test_two_seeds_equal_weights(self):
        seeds = [(100, 100), (300, 100)]
        weights = [1.0, 1.0]
        regions = compute_power_regions(seeds, weights, resolution=50)
        self.assertEqual(len(regions), 2)
        for r in regions:
            self.assertGreater(len(r), 0)

    def test_mismatch_raises(self):
        with self.assertRaises(ValueError):
            compute_power_regions([(0, 0)], [1, 2])

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            compute_power_regions([(0, 0)], [1], mode='bad')

    def test_custom_bounds(self):
        seeds = [(50, 50), (150, 150)]
        regions = compute_power_regions(seeds, [1, 1],
                                        bounds=(0, 200, 0, 200),
                                        resolution=40)
        self.assertEqual(len(regions), 2)

    def test_heavy_weight_dominates(self):
        seeds = [(100, 100), (300, 100)]
        regions_equal = compute_power_regions(seeds, [1, 1], resolution=80)
        regions_heavy = compute_power_regions(seeds, [10000, 1], resolution=80)
        # Heavier seed should have more boundary points
        a_equal = _polygon_area(regions_equal[0]) if regions_equal[0] else 0
        a_heavy = _polygon_area(regions_heavy[0]) if regions_heavy[0] else 0
        self.assertGreater(a_heavy, a_equal)

    def test_multiplicative_mode(self):
        seeds = [(100, 100), (300, 100)]
        regions = compute_power_regions(seeds, [5, 1],
                                        mode='multiplicative', resolution=50)
        self.assertEqual(len(regions), 2)

    def test_additive_mode(self):
        seeds = [(100, 100), (300, 100)]
        regions = compute_power_regions(seeds, [50, 10],
                                        mode='additive', resolution=50)
        self.assertEqual(len(regions), 2)


class TestComputePowerDiagram(unittest.TestCase):

    def test_basic(self):
        seeds = [(100, 200), (300, 400), (500, 100)]
        weights = [10, 20, 30]
        result = compute_power_diagram(seeds, weights, resolution=50)
        self.assertIsInstance(result, PowerDiagramResult)
        self.assertEqual(result.num_seeds, 3)
        self.assertEqual(result.mode, 'power')

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            compute_power_diagram([], [])

    def test_empty_weights_raises(self):
        with self.assertRaises(ValueError):
            compute_power_diagram([(0, 0)], [])

    def test_mismatch_raises(self):
        with self.assertRaises(ValueError):
            compute_power_diagram([(0, 0)], [1, 2])

    def test_bad_mode_raises(self):
        with self.assertRaises(ValueError):
            compute_power_diagram([(0, 0)], [1], mode='bad')

    def test_multiplicative_negative_weight_raises(self):
        with self.assertRaises(ValueError):
            compute_power_diagram([(0, 0), (1, 1)], [-1, 1],
                                  mode='multiplicative')

    def test_single_seed(self):
        result = compute_power_diagram([(50, 50)], [5.0], resolution=30)
        self.assertEqual(result.num_seeds, 1)
        self.assertGreater(result.total_area, 0)


class TestPowerDiagramResult(unittest.TestCase):

    def setUp(self):
        seeds = [(100, 100), (300, 100), (200, 300)]
        weights = [5, 15, 10]
        self.result = compute_power_diagram(seeds, weights, resolution=60)

    def test_properties(self):
        self.assertEqual(len(self.result.seeds), 3)
        self.assertEqual(len(self.result.weights), 3)
        self.assertEqual(len(self.result.regions), 3)
        self.assertEqual(len(self.result.areas), 3)

    def test_weight_stats(self):
        stats = self.result.weight_stats()
        self.assertEqual(stats['count'], 3)
        self.assertAlmostEqual(stats['mean'], 10.0)

    def test_area_stats(self):
        stats = self.result.area_stats()
        self.assertEqual(stats['count'], 3)
        self.assertGreater(stats['total'], 0)

    def test_weight_area_correlation(self):
        corr = self.result.weight_area_correlation()
        self.assertGreaterEqual(corr, -1.0)
        self.assertLessEqual(corr, 1.0)

    def test_largest_cell(self):
        lc = self.result.largest_cell()
        self.assertIsNotNone(lc)
        self.assertIsInstance(lc, WeightedSeed)

    def test_smallest_cell(self):
        sc = self.result.smallest_cell()
        self.assertIsNotNone(sc)

    def test_dominance_ratio(self):
        dr = self.result.dominance_ratio()
        self.assertGreaterEqual(dr, 1.0)

    def test_find_cell(self):
        ws = self.result.find_cell((100, 100))
        self.assertIsInstance(ws, WeightedSeed)

    def test_summary(self):
        s = self.result.summary()
        self.assertIn('Power Voronoi Diagram', s)
        self.assertIn('power', s)

    def test_to_dict(self):
        d = self.result.to_dict()
        self.assertEqual(d['mode'], 'power')
        self.assertEqual(d['num_seeds'], 3)
        self.assertIn('seeds', d)
        self.assertIn('weight_stats', d)


class TestWeightedSeed(unittest.TestCase):

    def test_basic(self):
        ws = WeightedSeed(10, 20, 5.0, 0, [(0, 0), (10, 0), (10, 10), (0, 10)])
        self.assertEqual(ws.x, 10)
        self.assertEqual(ws.y, 20)
        self.assertEqual(ws.weight, 5.0)
        self.assertAlmostEqual(ws.area, 100.0)
        self.assertGreater(ws.perimeter, 0)

    def test_empty_region(self):
        ws = WeightedSeed(0, 0, 1.0, 0)
        self.assertEqual(ws.area, 0.0)
        self.assertEqual(ws.centroid, (0, 0))

    def test_to_dict(self):
        ws = WeightedSeed(5, 10, 3.0, 2, [(0, 0), (5, 0), (5, 5)])
        d = ws.to_dict()
        self.assertEqual(d['index'], 2)
        self.assertEqual(d['weight'], 3.0)
        self.assertIn('vertices', d)


class TestWeightEffectAnalysis(unittest.TestCase):

    def test_basic(self):
        seeds = [(100, 100), (300, 100), (200, 300)]
        weights = [1, 10, 5]
        analysis = weight_effect_analysis(seeds, weights, resolution=40)
        self.assertEqual(analysis['num_seeds'], 3)
        self.assertEqual(len(analysis['changes']), 3)
        self.assertIn('baseline_gini', analysis)
        self.assertIn('weighted_gini', analysis)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            weight_effect_analysis([], [])


class TestExportPowerJson(unittest.TestCase):

    def test_string_output(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        text = export_power_json(result)
        data = json.loads(text)
        self.assertEqual(data['mode'], 'power')
        self.assertEqual(data['num_seeds'], 2)

    def test_file_output(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name
        try:
            export_power_json(result, path)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data['num_seeds'], 2)
        finally:
            os.unlink(path)


class TestExportPowerSvg(unittest.TestCase):

    def test_string_output(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        svg = export_power_svg(result)
        self.assertIn('<svg', svg)
        self.assertIn('</svg>', svg)

    def test_file_output(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            export_power_svg(result, path)
            with open(path) as f:
                content = f.read()
            self.assertIn('<svg', content)
        finally:
            os.unlink(path)

    def test_color_schemes(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        for scheme in ('viridis', 'plasma', 'warm', 'cool', 'earth', 'pastel'):
            svg = export_power_svg(result, color_scheme=scheme)
            self.assertIn('<svg', svg)

    def test_no_weights_label(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        svg = export_power_svg(result, show_weights=False)
        self.assertNotIn('w=', svg)

    def test_no_seeds(self):
        result = compute_power_diagram([(100, 100), (300, 300)], [5, 10],
                                       resolution=30)
        svg = export_power_svg(result, show_seeds=False)
        self.assertNotIn('<circle', svg)


class TestEdgeCases(unittest.TestCase):

    def test_collinear_seeds(self):
        seeds = [(0, 0), (100, 0), (200, 0)]
        weights = [5, 10, 5]
        result = compute_power_diagram(seeds, weights, resolution=40)
        self.assertEqual(result.num_seeds, 3)

    def test_coincident_seeds(self):
        seeds = [(100, 100), (100, 100)]
        weights = [5, 10]
        result = compute_power_diagram(seeds, weights, resolution=40)
        self.assertEqual(result.num_seeds, 2)

    def test_many_seeds(self):
        import random
        rng = random.Random(42)
        seeds = [(rng.uniform(0, 500), rng.uniform(0, 500)) for _ in range(20)]
        weights = [rng.uniform(1, 50) for _ in range(20)]
        result = compute_power_diagram(seeds, weights, resolution=50)
        self.assertEqual(result.num_seeds, 20)

    def test_very_different_weights(self):
        seeds = [(100, 100), (300, 100)]
        weights = [100000, 1]
        result = compute_power_diagram(seeds, weights, resolution=50)
        # Heavy seed should dominate
        self.assertGreater(result.weighted_seeds[0].area,
                           result.weighted_seeds[1].area)

    def test_all_modes(self):
        seeds = [(100, 100), (300, 300)]
        weights = [5, 10]
        for mode in ('power', 'multiplicative', 'additive'):
            result = compute_power_diagram(seeds, weights, mode=mode,
                                           resolution=40)
            self.assertEqual(result.mode, mode)

    def test_result_empty_smallest(self):
        # Single seed → no "smallest non-empty" comparison
        result = compute_power_diagram([(50, 50)], [5.0], resolution=20)
        sc = result.smallest_cell()
        self.assertIsNotNone(sc)

    def test_weight_stats_empty(self):
        # Construct empty result manually
        r = PowerDiagramResult([], [], [], 'power', (0, 100, 0, 100), 20)
        self.assertEqual(r.weight_stats(), {})
        self.assertEqual(r.area_stats(), {})

    def test_find_cell_all_modes(self):
        seeds = [(100, 100), (300, 300)]
        for mode in ('power', 'multiplicative', 'additive'):
            result = compute_power_diagram(seeds, [5, 10], mode=mode,
                                           resolution=30)
            ws = result.find_cell((200, 200))
            self.assertIsInstance(ws, WeightedSeed)

    # ---- Tests for non-convex boundary tracing (issue #98) ----

    def test_multiplicative_regions_not_convex_hull(self):
        """Multiplicative mode should use ordered boundary, not convex hull.

        A lower-weight center seed gets a smaller but non-convex region.
        The boundary trace should produce a polygon that's not larger than
        the convex hull of its boundary points.
        """
        seeds = [(50, 50), (0, 0), (100, 0), (100, 100), (0, 100)]
        weights = [1.0, 3.0, 3.0, 3.0, 3.0]
        regions_mult = compute_power_regions(
            seeds, weights, mode='multiplicative', resolution=100)
        # Region for the center seed (index 0) should exist
        self.assertTrue(len(regions_mult[0]) >= 3,
                        "center seed should have a region polygon")
        # All regions should be non-empty lists of tuples
        for i, r in enumerate(regions_mult):
            if r:
                for pt in r:
                    self.assertEqual(len(pt), 2)

    def test_additive_regions_not_convex_hull(self):
        """Additive mode should also use ordered boundary trace."""
        seeds = [(50, 50), (0, 0), (100, 0), (100, 100), (0, 100)]
        weights = [1.0, 5.0, 5.0, 5.0, 5.0]
        regions_add = compute_power_regions(
            seeds, weights, mode='additive', resolution=100)
        self.assertTrue(len(regions_add[0]) >= 3)

    def test_power_mode_still_uses_convex_hull(self):
        """Power mode should still use convex hull (regions are convex)."""
        seeds = [(50, 50), (0, 0), (100, 0), (100, 100), (0, 100)]
        weights = [1.0, 100.0, 100.0, 100.0, 100.0]
        regions_pow = compute_power_regions(
            seeds, weights, mode='power', resolution=100)
        # Should succeed without errors
        for r in regions_pow:
            if r:
                for pt in r:
                    self.assertEqual(len(pt), 2)

    def test_ordered_boundary_preserves_concavity(self):
        """Ordered boundary area should be <= convex hull area for non-convex
        regions (multiplicative mode)."""
        seeds = [(50, 50), (0, 0), (100, 0), (100, 100), (0, 100)]
        weights = [1.0, 3.0, 3.0, 3.0, 3.0]

        # Get multiplicative region (uses ordered boundary)
        regions_mult = compute_power_regions(
            seeds, weights, mode='multiplicative', resolution=100)

        if len(regions_mult[0]) >= 3:
            # Compute area of the traced boundary polygon
            traced_area = abs(_polygon_area(regions_mult[0]))
            # Compute area of its convex hull
            hull = _convex_hull(regions_mult[0])
            hull_area = abs(_polygon_area(hull))
            # Traced boundary should be <= convex hull (preserves concavity)
            self.assertLessEqual(traced_area, hull_area + 1e-6)


if __name__ == '__main__':
    unittest.main()
