"""Extended tests for vormap_relax — covers untested paths.

Targets:
  - _clip_infinite_region (complex Voronoi clipping with infinite edges)
  - _clip_polygon_to_box edge cases (degenerate polygons, exact bounds)
  - _polygon_centroid edge cases (collinear points, two-vertex segments)
  - _generate_svg parameters (color schemes, show_seeds=False)
  - uniformity_score edge cases (identical points, collinear)
  - lloyd_relaxation advanced scenarios (tight tolerance, zero-padding bounds)
"""

import math
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_relax
from vormap_relax import (
    _polygon_centroid,
    _clip_polygon_to_box,
    _clip_infinite_region,
    _generate_svg,
    lloyd_relaxation,
    uniformity_score,
)


# ── _polygon_centroid extended ──────────────────────────────────────


class TestPolygonCentroidExtended(unittest.TestCase):

    def test_two_vertices(self):
        """Two-vertex segment — should return midpoint."""
        verts = np.array([[0, 0], [10, 0]], dtype=float)
        cx, cy = _polygon_centroid(verts)
        self.assertAlmostEqual(cx, 5.0, places=5)
        self.assertAlmostEqual(cy, 0.0, places=5)

    def test_collinear_points(self):
        """Collinear (zero-area) polygon — should fall back to mean."""
        verts = np.array([[0, 0], [5, 0], [10, 0]], dtype=float)
        cx, cy = _polygon_centroid(verts)
        self.assertAlmostEqual(cx, 5.0, places=5)
        self.assertAlmostEqual(cy, 0.0, places=5)

    def test_regular_hexagon(self):
        """Regular hexagon centroid should be at origin."""
        angles = [i * math.pi / 3 for i in range(6)]
        verts = np.array([[math.cos(a), math.sin(a)] for a in angles])
        cx, cy = _polygon_centroid(verts)
        self.assertAlmostEqual(cx, 0.0, places=4)
        self.assertAlmostEqual(cy, 0.0, places=4)

    def test_asymmetric_polygon(self):
        """Asymmetric L-shape polygon — centroid should be off-center."""
        verts = np.array([
            [0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2],
        ], dtype=float)
        cx, cy = _polygon_centroid(verts)
        # L-shape is heavier on the left/bottom
        self.assertLess(cx, 1.0)
        self.assertLess(cy, 1.0)

    def test_large_polygon(self):
        """Polygon with many vertices — circular approximation."""
        n = 100
        angles = [2 * math.pi * i / n for i in range(n)]
        verts = np.array([[10 + 5 * math.cos(a), 20 + 5 * math.sin(a)]
                          for a in angles])
        cx, cy = _polygon_centroid(verts)
        self.assertAlmostEqual(cx, 10.0, places=2)
        self.assertAlmostEqual(cy, 20.0, places=2)


# ── _clip_polygon_to_box extended ──────────────────────────────────


class TestClipPolygonToBoxExtended(unittest.TestCase):

    def test_completely_outside(self):
        """Polygon entirely outside the box → empty result."""
        verts = np.array([[10, 10], [20, 10], [20, 20], [10, 20]], dtype=float)
        bounds = (0, 5, 0, 5)
        clipped = _clip_polygon_to_box(verts, bounds)
        self.assertEqual(len(clipped), 0)

    def test_polygon_on_boundary(self):
        """Polygon exactly on box edge — should not be clipped away."""
        verts = np.array([[0, 0], [5, 0], [5, 5], [0, 5]], dtype=float)
        bounds = (0, 5, 0, 5)
        clipped = _clip_polygon_to_box(verts, bounds)
        self.assertGreaterEqual(len(clipped), 4)

    def test_triangle_clipped_to_corner(self):
        """Triangle straddling a box corner."""
        verts = np.array([[-1, 0.5], [0.5, -1], [0.5, 0.5]], dtype=float)
        bounds = (0, 10, 0, 10)
        clipped = _clip_polygon_to_box(verts, bounds)
        self.assertGreaterEqual(len(clipped), 3)
        for v in clipped:
            self.assertGreaterEqual(v[0], 0.0 - 1e-10)
            self.assertGreaterEqual(v[1], 0.0 - 1e-10)

    def test_large_polygon_small_box(self):
        """Large polygon much bigger than the box → result is the box."""
        verts = np.array([[-100, -100], [100, -100], [100, 100], [-100, 100]],
                         dtype=float)
        bounds = (0, 1, 0, 1)
        clipped = _clip_polygon_to_box(verts, bounds)
        self.assertGreaterEqual(len(clipped), 4)
        for v in clipped:
            self.assertGreaterEqual(v[0], -1e-10)
            self.assertLessEqual(v[0], 1.0 + 1e-10)
            self.assertGreaterEqual(v[1], -1e-10)
            self.assertLessEqual(v[1], 1.0 + 1e-10)

    def test_empty_polygon(self):
        """Empty input polygon."""
        verts = np.empty((0, 2))
        bounds = (0, 10, 0, 10)
        clipped = _clip_polygon_to_box(verts, bounds)
        self.assertEqual(len(clipped), 0)

    def test_clip_preserves_shape_integrity(self):
        """Clipping a diamond shape to a box should produce valid polygon."""
        verts = np.array([[5, 0], [10, 5], [5, 10], [0, 5]], dtype=float)
        bounds = (2, 8, 2, 8)
        clipped = _clip_polygon_to_box(verts, bounds)
        self.assertGreaterEqual(len(clipped), 4)


# ── _clip_infinite_region ──────────────────────────────────────────


class TestClipInfiniteRegion(unittest.TestCase):

    def test_finite_region(self):
        """A finite Voronoi region should clip correctly."""
        from scipy.spatial import Voronoi

        # 5x5 grid → all interior regions are finite
        pts = np.array([(i, j) for i in range(5) for j in range(5)],
                       dtype=float)
        vor = Voronoi(pts)
        bounds = (-1, 5, -1, 5)

        # Find an interior point (2,2) — its region should be fully finite
        idx = None
        for i, p in enumerate(pts):
            if p[0] == 2.0 and p[1] == 2.0:
                idx = i
                break
        self.assertIsNotNone(idx)

        region_idx = vor.point_region[idx]
        clipped = _clip_infinite_region(vor, region_idx, bounds)
        self.assertIsNotNone(clipped)
        self.assertGreaterEqual(len(clipped), 3)

    def test_infinite_region(self):
        """A boundary point's infinite region should still produce a polygon."""
        from scipy.spatial import Voronoi

        pts = np.array([(i, j) for i in range(4) for j in range(4)],
                       dtype=float)
        vor = Voronoi(pts)
        bounds = (-1, 4, -1, 4)

        # Corner point (0,0) should have an infinite region
        idx = 0
        for i, p in enumerate(pts):
            if p[0] == 0.0 and p[1] == 0.0:
                idx = i
                break

        region_idx = vor.point_region[idx]
        clipped = _clip_infinite_region(vor, region_idx, bounds)
        self.assertIsNotNone(clipped)
        self.assertGreaterEqual(len(clipped), 3)
        # Should be within bounds
        for v in clipped:
            self.assertGreaterEqual(v[0], bounds[0] - 0.1)
            self.assertLessEqual(v[0], bounds[1] + 0.1)

    def test_empty_region(self):
        """Requesting a degenerate region should return None."""
        from scipy.spatial import Voronoi

        pts = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=float)
        vor = Voronoi(pts)
        bounds = (-1, 2, -1, 2)

        # Find a region that's empty (index 0 in vor.regions is usually [])
        empty_idx = None
        for i, region in enumerate(vor.regions):
            if not region:
                empty_idx = i
                break

        if empty_idx is not None:
            clipped = _clip_infinite_region(vor, empty_idx, bounds)
            self.assertIsNone(clipped)

    def test_all_points_produce_cells(self):
        """Every point in a reasonable configuration should produce a cell."""
        from scipy.spatial import Voronoi

        rng = np.random.RandomState(42)
        pts = rng.uniform(10, 90, size=(20, 2))
        vor = Voronoi(pts)
        bounds = (0, 100, 0, 100)

        valid_count = 0
        for i in range(len(pts)):
            region_idx = vor.point_region[i]
            clipped = _clip_infinite_region(vor, region_idx, bounds)
            if clipped is not None and len(clipped) >= 3:
                valid_count += 1

        # Most points should produce valid cells
        self.assertGreaterEqual(valid_count, 15)


# ── SVG generation extended ─────────────────────────────────────────


class TestSVGExtended(unittest.TestCase):

    def _make_result(self, n=10, iters=3, seed=42):
        rng = np.random.RandomState(seed)
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]
        return lloyd_relaxation(points, iterations=iters)

    def test_no_seeds(self):
        """show_seeds=False should produce SVG without circle elements."""
        result = self._make_result()
        svg = _generate_svg(result, (0, 100, 0, 100), show_seeds=False)
        self.assertIn("<svg", svg)
        self.assertNotIn("<circle", svg)

    def test_color_schemes(self):
        """All built-in color schemes should produce valid SVG."""
        result = self._make_result()
        bounds = (0, 100, 0, 100)
        for scheme in ("pastel", "cool", "warm", "mono"):
            svg = _generate_svg(result, bounds, color_scheme=scheme)
            self.assertIn("<svg", svg)
            self.assertIn("</svg>", svg)

    def test_unknown_color_scheme_defaults(self):
        """Unknown scheme name should fall back to pastel."""
        result = self._make_result()
        svg = _generate_svg(result, (0, 100, 0, 100),
                            color_scheme="nonexistent")
        self.assertIn("<svg", svg)

    def test_custom_dimensions(self):
        """Custom width/height should be reflected in SVG."""
        result = self._make_result(n=5, iters=2)
        svg = _generate_svg(result, (0, 100, 0, 100),
                            width=400, height=300)
        self.assertIn('width="400"', svg)
        self.assertIn('height="300"', svg)

    def test_convergence_info_in_svg(self):
        """SVG should contain convergence information text."""
        result = self._make_result()
        svg = _generate_svg(result, (0, 100, 0, 100))
        self.assertIn("uniformity:", svg)

    def test_animated_with_single_step(self):
        """Animation with minimal history should still work."""
        result = self._make_result(n=5, iters=1)
        svg = _generate_svg(result, (0, 100, 0, 100), animate=True)
        self.assertIn("<svg", svg)


# ── uniformity_score extended ───────────────────────────────────────


class TestUniformityScoreExtended(unittest.TestCase):

    def test_identical_points(self):
        """All points at same location → score 0."""
        pts = [(5, 5)] * 10
        score = uniformity_score(pts)
        self.assertEqual(score, 0.0)

    def test_collinear_uniform(self):
        """Evenly spaced collinear points → high uniformity."""
        pts = [(i * 10, 0) for i in range(10)]
        score = uniformity_score(pts)
        self.assertGreater(score, 0.8)

    def test_cluster_with_outlier(self):
        """Tight cluster + distant outlier → low uniformity."""
        pts = [(0, 0), (0.1, 0), (0, 0.1), (0.1, 0.1), (100, 100)]
        score = uniformity_score(pts)
        self.assertLess(score, 0.5)

    def test_score_range(self):
        """Score should always be in [0, 1]."""
        import random
        rng = random.Random(99)
        for _ in range(5):
            pts = [(rng.uniform(-100, 100), rng.uniform(-100, 100))
                   for _ in range(50)]
            score = uniformity_score(pts)
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 1.0)

    def test_three_points_equilateral(self):
        """Equilateral triangle → perfectly uniform."""
        pts = [(0, 0), (10, 0), (5, 5 * math.sqrt(3))]
        score = uniformity_score(pts)
        self.assertGreater(score, 0.9)


# ── lloyd_relaxation extended ───────────────────────────────────────


class TestLloydRelaxationExtended(unittest.TestCase):

    def test_tight_tolerance_early_stop(self):
        """Very tight tolerance should converge for a regular grid."""
        pts = [(i * 10, j * 10) for i in range(5) for j in range(5)]
        result = lloyd_relaxation(pts, iterations=50, tolerance=0.001)
        # Regular grid barely moves → should converge very early
        self.assertTrue(result["converged"])
        self.assertLess(result["iterations"], 20)

    def test_large_tolerance_one_iteration(self):
        """Huge tolerance should stop after 1 iteration for small points."""
        pts = [(0, 0), (1, 0), (0.5, 1)]
        result = lloyd_relaxation(pts, iterations=100, tolerance=1e6)
        self.assertTrue(result["converged"])
        self.assertEqual(result["iterations"], 1)

    def test_history_length_matches(self):
        """History should have iterations + 1 entries (initial + each step)."""
        pts = [(i, j) for i in range(4) for j in range(4)]
        result = lloyd_relaxation(pts, iterations=5, tolerance=0.001)
        self.assertEqual(len(result["history"]),
                         result["iterations"] + 1)

    def test_displacements_decreasing(self):
        """Displacements should generally decrease over iterations."""
        import random
        rng = random.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
        result = lloyd_relaxation(pts, iterations=20, tolerance=0.001)
        disps = result["displacements"]
        if len(disps) >= 3:
            # Last displacement should be less than first
            self.assertLess(disps[-1], disps[0])

    def test_bounds_clamping(self):
        """Points near bounds edge should stay within bounds."""
        pts = [(0.1, 0.1), (0.9, 0.1), (0.5, 0.9), (0.1, 0.9), (0.9, 0.9)]
        bounds = (0, 1, 0, 1)
        result = lloyd_relaxation(pts, iterations=10, bounds=bounds)
        for x, y in result["points"]:
            self.assertGreaterEqual(x, bounds[0] - 1e-10)
            self.assertLessEqual(x, bounds[1] + 1e-10)
            self.assertGreaterEqual(y, bounds[2] - 1e-10)
            self.assertLessEqual(y, bounds[3] + 1e-10)

    def test_many_points(self):
        """50 points should relax without errors."""
        import random
        rng = random.Random(77)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        result = lloyd_relaxation(pts, iterations=5)
        self.assertEqual(len(result["points"]), 50)

    def test_callback_receives_numpy_array(self):
        """Callback should receive numpy array of points."""
        pts = [(i, j) for i in range(3) for j in range(3)]
        received = []
        def cb(step, points, disp):
            received.append((step, type(points).__name__, disp))
        lloyd_relaxation(pts, iterations=2, callback=cb)
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0][1], "ndarray")
        self.assertIsInstance(received[0][2], float)

    def test_exactly_three_points(self):
        """Minimum valid input (3 points) should work."""
        pts = [(0, 0), (10, 0), (5, 10)]
        result = lloyd_relaxation(pts, iterations=5)
        self.assertEqual(len(result["points"]), 3)
        self.assertIn("converged", result)


if __name__ == "__main__":
    unittest.main()
