"""Tests for vormap_smooth — spatial smoothing module."""

import csv
import json
import math
import os
import sys
import unittest

# Ensure repo root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from vormap_smooth import (
    SmoothConfig,
    SmoothResult,
    smooth_attributes,
    export_csv,
    export_json,
    _compute_distance,
    _smooth_once,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_grid_data(n=3):
    """Create an n×n grid of points."""
    return [(float(x * 100), float(y * 100)) for y in range(n) for x in range(n)]


def _make_regions_from_grid(data, n=3):
    """Create simple square regions for a grid (no real Voronoi needed)."""
    regions = {}
    half = 50.0
    for pt in data:
        x, y = pt
        regions[pt] = [
            (x - half, y - half),
            (x + half, y - half),
            (x + half, y + half),
            (x - half, y + half),
        ]
    return regions


def _make_adjacency_grid(data, n=3):
    """Build adjacency for an n×n grid (4-connected)."""
    adj = {}
    idx = {}
    for i, pt in enumerate(data):
        r, c = i // n, i % n
        idx[(r, c)] = pt
        adj[pt] = []

    for i, pt in enumerate(data):
        r, c = i // n, i % n
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if (nr, nc) in idx:
                adj[pt].append(idx[(nr, nc)])
    return adj


# ── Config tests ─────────────────────────────────────────────────────

class TestSmoothConfig(unittest.TestCase):
    def test_defaults(self):
        c = SmoothConfig()
        self.assertEqual(c.method, "mean")
        self.assertEqual(c.iterations, 1)
        self.assertEqual(c.alpha, 1.0)
        self.assertTrue(c.include_self)

    def test_invalid_method(self):
        with self.assertRaises(ValueError):
            SmoothConfig(method="cubic")

    def test_invalid_iterations(self):
        with self.assertRaises(ValueError):
            SmoothConfig(iterations=0)

    def test_invalid_alpha(self):
        with self.assertRaises(ValueError):
            SmoothConfig(alpha=1.5)
        with self.assertRaises(ValueError):
            SmoothConfig(alpha=-0.1)

    def test_invalid_sigma(self):
        with self.assertRaises(ValueError):
            SmoothConfig(sigma=0)

    def test_invalid_power(self):
        with self.assertRaises(ValueError):
            SmoothConfig(power=-1)

    def test_valid_methods(self):
        for m in ["mean", "median", "gaussian", "inverse_distance"]:
            c = SmoothConfig(method=m)
            self.assertEqual(c.method, m)


# ── Distance ─────────────────────────────────────────────────────────

class TestDistance(unittest.TestCase):
    def test_same_point(self):
        self.assertAlmostEqual(_compute_distance((0, 0), (0, 0)), 0.0)

    def test_known_distance(self):
        self.assertAlmostEqual(_compute_distance((0, 0), (3, 4)), 5.0)


# ── Smooth once ──────────────────────────────────────────────────────

class TestSmoothOnce(unittest.TestCase):
    def test_mean_uniform(self):
        """All same values → no change."""
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 10.0 for pt in data}
        config = SmoothConfig(method="mean")
        new_vals, max_change = _smooth_once(values, adj, list(values.keys()), config)
        self.assertAlmostEqual(max_change, 0.0)
        for v in new_vals.values():
            self.assertAlmostEqual(v, 10.0)

    def test_mean_smoothing(self):
        """Center spike gets smoothed."""
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 0.0 for pt in data}
        center = (100.0, 100.0)
        values[center] = 100.0
        config = SmoothConfig(method="mean", include_self=True)
        new_vals, max_change = _smooth_once(values, adj, list(values.keys()), config)
        # Center had 4 neighbours all at 0, self at 100 → mean = 20
        self.assertAlmostEqual(new_vals[center], 20.0)
        self.assertGreater(max_change, 0)

    def test_median_smoothing(self):
        """Median should be robust to outlier."""
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 10.0 for pt in data}
        center = (100.0, 100.0)
        values[center] = 1000.0  # outlier
        config = SmoothConfig(method="median", include_self=True)
        new_vals, _ = _smooth_once(values, adj, list(values.keys()), config)
        # Center: self=1000, 4 neighbours at 10 → sorted [10,10,10,10,1000] → median=10
        self.assertAlmostEqual(new_vals[center], 10.0)

    def test_alpha_blending(self):
        """Alpha=0 should preserve original."""
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: float(i) for i, pt in enumerate(data)}
        config = SmoothConfig(method="mean", alpha=0.0)
        new_vals, max_change = _smooth_once(values, adj, list(values.keys()), config)
        self.assertAlmostEqual(max_change, 0.0)
        for pt in data:
            self.assertAlmostEqual(new_vals[pt], values[pt])

    def test_no_neighbours(self):
        """Point with no neighbours stays unchanged."""
        seed = (50.0, 50.0)
        values = {seed: 42.0}
        adj = {seed: []}
        config = SmoothConfig()
        new_vals, _ = _smooth_once(values, adj, [seed], config)
        self.assertAlmostEqual(new_vals[seed], 42.0)

    def test_exclude_self(self):
        """include_self=False excludes own value."""
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 0.0 for pt in data}
        center = (100.0, 100.0)
        values[center] = 100.0
        config = SmoothConfig(method="mean", include_self=False)
        new_vals, _ = _smooth_once(values, adj, list(values.keys()), config)
        # Center: 4 neighbours all at 0 (self excluded) → mean = 0
        self.assertAlmostEqual(new_vals[center], 0.0)


# ── SmoothResult ─────────────────────────────────────────────────────

class TestSmoothResult(unittest.TestCase):
    def test_summary(self):
        result = SmoothResult(
            original={(0, 0): 10.0, (1, 1): 20.0},
            smoothed={(0, 0): 12.0, (1, 1): 18.0},
            config=SmoothConfig(),
            iterations_applied=1,
            convergence=[2.0],
        )
        s = result.summary()
        self.assertIn("mean", s)
        self.assertIn("2", s)

    def test_delta_map(self):
        result = SmoothResult(
            original={(0, 0): 10.0, (1, 1): 20.0},
            smoothed={(0, 0): 15.0, (1, 1): 18.0},
        )
        dm = result.delta_map()
        self.assertAlmostEqual(dm[(0, 0)], 5.0)
        self.assertAlmostEqual(dm[(1, 1)], 2.0)


# ── Export ───────────────────────────────────────────────────────────

class TestExport(unittest.TestCase):
    def setUp(self):
        self.result = SmoothResult(
            original={(10.0, 20.0): 5.0, (30.0, 40.0): 15.0},
            smoothed={(10.0, 20.0): 7.0, (30.0, 40.0): 13.0},
            config=SmoothConfig(method="mean", iterations=2),
            iterations_applied=2,
            convergence=[3.0, 1.0],
        )
        self.csv_path = "test_smooth_out.csv"
        self.json_path = "test_smooth_out.json"

    def tearDown(self):
        for p in [self.csv_path, self.json_path]:
            if os.path.exists(p):
                os.remove(p)

    def test_csv_export(self):
        export_csv(self.result, self.csv_path)
        self.assertTrue(os.path.exists(self.csv_path))
        with open(self.csv_path) as f:
            reader = csv.reader(f)
            header = next(reader)
            self.assertEqual(header, ["x", "y", "original", "smoothed", "change"])
            rows = list(reader)
            self.assertEqual(len(rows), 2)

    def test_json_export(self):
        export_json(self.result, self.json_path)
        self.assertTrue(os.path.exists(self.json_path))
        with open(self.json_path) as f:
            obj = json.load(f)
        self.assertEqual(obj["config"]["method"], "mean")
        self.assertEqual(len(obj["data"]), 2)
        self.assertEqual(obj["summary"]["points"], 2)


# ── Gaussian & IDW ───────────────────────────────────────────────────

class TestGaussianSmoothing(unittest.TestCase):
    def test_gaussian_uniform(self):
        """Uniform values → no change with Gaussian."""
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 5.0 for pt in data}
        config = SmoothConfig(method="gaussian", sigma=100.0)
        new_vals, max_change = _smooth_once(values, adj, list(values.keys()), config)
        self.assertAlmostEqual(max_change, 0.0, places=6)

    def test_gaussian_weights_decrease_with_distance(self):
        """Closer neighbours should have more influence in Gaussian."""
        # 3 points in a line: A at 0, B at 50, C at 200
        a, b, c = (0.0, 0.0), (50.0, 0.0), (200.0, 0.0)
        values = {a: 0.0, b: 0.0, c: 100.0}
        adj = {a: [b], b: [a, c], c: [b]}
        config = SmoothConfig(method="gaussian", sigma=50.0, include_self=False)
        new_vals, _ = _smooth_once(values, adj, [a, b, c], config)
        # B is influenced by both A(0) and C(100), but A is closer
        # so smoothed B should be < 50
        self.assertLess(new_vals[b], 50.0)


class TestIDWSmoothing(unittest.TestCase):
    def test_idw_uniform(self):
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 7.0 for pt in data}
        config = SmoothConfig(method="inverse_distance")
        new_vals, max_change = _smooth_once(values, adj, list(values.keys()), config)
        self.assertAlmostEqual(max_change, 0.0, places=6)


# ── Multiple iterations ─────────────────────────────────────────────

class TestMultipleIterations(unittest.TestCase):
    def test_convergence_decreases(self):
        """Max change should generally decrease with iterations."""
        # We test _smooth_once manually for 3 iterations
        data = _make_grid_data(3)
        adj = _make_adjacency_grid(data, 3)
        values = {pt: 0.0 for pt in data}
        values[(100.0, 100.0)] = 100.0
        config = SmoothConfig(method="mean")

        changes = []
        current = dict(values)
        for _ in range(3):
            current, mc = _smooth_once(current, adj, list(current.keys()), config)
            changes.append(mc)

        # First change should be largest
        self.assertEqual(changes[0], max(changes))


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases(unittest.TestCase):
    def test_single_point(self):
        """Single point with no neighbours."""
        seed = (50.0, 50.0)
        values = {seed: 42.0}
        adj = {seed: []}
        config = SmoothConfig()
        new_vals, mc = _smooth_once(values, adj, [seed], config)
        self.assertAlmostEqual(new_vals[seed], 42.0)
        self.assertAlmostEqual(mc, 0.0)

    def test_two_points(self):
        """Two connected points should average."""
        a, b = (0.0, 0.0), (100.0, 0.0)
        values = {a: 0.0, b: 100.0}
        adj = {a: [b], b: [a]}
        config = SmoothConfig(method="mean", include_self=True)
        new_vals, _ = _smooth_once(values, adj, [a, b], config)
        self.assertAlmostEqual(new_vals[a], 50.0)
        self.assertAlmostEqual(new_vals[b], 50.0)

    def test_missing_neighbour_values(self):
        """Neighbours not in values dict should be skipped."""
        a, b, c = (0.0, 0.0), (100.0, 0.0), (200.0, 0.0)
        values = {a: 10.0, b: 20.0}  # c has no value
        adj = {a: [b, c], b: [a, c]}
        config = SmoothConfig(method="mean", include_self=True)
        new_vals, _ = _smooth_once(values, adj, [a, b], config)
        # a: self=10, b=20 → mean=15
        self.assertAlmostEqual(new_vals[a], 15.0)


if __name__ == "__main__":
    unittest.main()
