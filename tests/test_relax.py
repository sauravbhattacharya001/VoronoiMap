"""Tests for vormap_relax — Lloyd's Relaxation."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_relax


class TestPolygonCentroid(unittest.TestCase):
    """Test the _polygon_centroid helper."""

    def test_square(self):
        import numpy as np
        verts = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=float)
        cx, cy = vormap_relax._polygon_centroid(verts)
        self.assertAlmostEqual(cx, 0.5, places=5)
        self.assertAlmostEqual(cy, 0.5, places=5)

    def test_triangle(self):
        import numpy as np
        verts = np.array([[0, 0], [6, 0], [3, 6]], dtype=float)
        cx, cy = vormap_relax._polygon_centroid(verts)
        self.assertAlmostEqual(cx, 3.0, places=5)
        self.assertAlmostEqual(cy, 2.0, places=5)

    def test_single_point(self):
        import numpy as np
        verts = np.array([[5, 7]], dtype=float)
        cx, cy = vormap_relax._polygon_centroid(verts)
        self.assertAlmostEqual(cx, 5.0)
        self.assertAlmostEqual(cy, 7.0)

    def test_empty(self):
        import numpy as np
        cx, cy = vormap_relax._polygon_centroid(np.empty((0, 2)))
        self.assertEqual(cx, 0.0)
        self.assertEqual(cy, 0.0)


class TestClipPolygonToBox(unittest.TestCase):
    """Test Sutherland-Hodgman clipping."""

    def test_fully_inside(self):
        import numpy as np
        verts = np.array([[1, 1], [2, 1], [2, 2], [1, 2]], dtype=float)
        bounds = (0, 3, 0, 3)
        clipped = vormap_relax._clip_polygon_to_box(verts, bounds)
        self.assertEqual(len(clipped), 4)

    def test_partial_clip(self):
        import numpy as np
        verts = np.array([[-1, 0], [1, 0], [1, 1], [-1, 1]], dtype=float)
        bounds = (0, 2, -1, 2)
        clipped = vormap_relax._clip_polygon_to_box(verts, bounds)
        # Left edge should be clipped at x=0
        self.assertTrue(all(v[0] >= 0 for v in clipped))
        self.assertGreaterEqual(len(clipped), 3)


class TestLloydRelaxation(unittest.TestCase):
    """Test the core relaxation algorithm."""

    def test_basic_relaxation(self):
        import random
        rng = random.Random(42)
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(20)]
        result = vormap_relax.lloyd_relaxation(points, iterations=5)

        self.assertIn("points", result)
        self.assertIn("history", result)
        self.assertIn("displacements", result)
        self.assertIn("converged", result)
        self.assertIn("iterations", result)
        self.assertEqual(len(result["points"]), 20)
        self.assertGreater(len(result["history"]), 1)

    def test_convergence(self):
        # Regular grid should converge quickly
        points = [(i * 10, j * 10) for i in range(5) for j in range(5)]
        result = vormap_relax.lloyd_relaxation(
            points, iterations=100, tolerance=0.01)
        # Should converge or at least finish
        self.assertLessEqual(result["iterations"], 100)

    def test_too_few_points(self):
        with self.assertRaises(ValueError):
            vormap_relax.lloyd_relaxation([(0, 0), (1, 1)])

    def test_callback(self):
        import random
        rng = random.Random(99)
        points = [(rng.uniform(0, 50), rng.uniform(0, 50)) for _ in range(10)]
        steps = []
        def cb(step, pts, disp):
            steps.append(step)
        vormap_relax.lloyd_relaxation(points, iterations=3, callback=cb)
        self.assertEqual(steps, [1, 2, 3])

    def test_custom_bounds(self):
        import random
        rng = random.Random(7)
        points = [(rng.uniform(10, 90), rng.uniform(10, 90)) for _ in range(15)]
        bounds = (0, 100, 0, 100)
        result = vormap_relax.lloyd_relaxation(
            points, iterations=5, bounds=bounds)
        # All points should stay within bounds
        for x, y in result["points"]:
            self.assertGreaterEqual(x, bounds[0])
            self.assertLessEqual(x, bounds[1])
            self.assertGreaterEqual(y, bounds[2])
            self.assertLessEqual(y, bounds[3])

    def test_uniformity_improves(self):
        """Relaxation should generally improve uniformity for random points."""
        import random
        rng = random.Random(123)
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
        init_score = vormap_relax.uniformity_score(points)
        result = vormap_relax.lloyd_relaxation(points, iterations=20)
        final_score = vormap_relax.uniformity_score(result["points"])
        # Allow tiny regression due to boundary effects, but generally improve
        self.assertGreaterEqual(final_score, init_score - 0.1)


class TestUniformityScore(unittest.TestCase):
    """Test uniformity metric."""

    def test_regular_grid(self):
        points = [(i * 10, j * 10) for i in range(5) for j in range(5)]
        score = vormap_relax.uniformity_score(points)
        self.assertGreater(score, 0.7)

    def test_single_point(self):
        score = vormap_relax.uniformity_score([(5, 5)])
        self.assertEqual(score, 1.0)

    def test_two_points(self):
        score = vormap_relax.uniformity_score([(0, 0), (10, 10)])
        self.assertEqual(score, 1.0)


class TestSVGOutput(unittest.TestCase):
    """Test SVG generation."""

    def test_svg_generated(self):
        import random
        rng = random.Random(55)
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(10)]
        result = vormap_relax.lloyd_relaxation(points, iterations=3)
        bounds = (0, 100, 0, 100)
        svg = vormap_relax._generate_svg(result, bounds)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertIn("Lloyd relaxation", svg)

    def test_svg_animated(self):
        import random
        rng = random.Random(55)
        points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(10)]
        result = vormap_relax.lloyd_relaxation(points, iterations=3)
        bounds = (0, 100, 0, 100)
        svg = vormap_relax._generate_svg(result, bounds, animate=True)
        self.assertIn("animate", svg)


class TestCLIOutputs(unittest.TestCase):
    """Test file output from CLI-like usage."""

    def test_text_output(self):
        import random
        rng = random.Random(77)
        points = [(rng.uniform(0, 50), rng.uniform(0, 50)) for _ in range(8)]
        result = vormap_relax.lloyd_relaxation(points, iterations=3)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as f:
            for x, y in result["points"]:
                f.write(f"{x:.6f} {y:.6f}\n")
            tmp = f.name

        try:
            with open(tmp) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 8)
            for line in lines:
                parts = line.strip().split()
                self.assertEqual(len(parts), 2)
                float(parts[0])
                float(parts[1])
        finally:
            os.unlink(tmp)

    def test_json_output(self):
        import random
        rng = random.Random(88)
        points = [(rng.uniform(0, 50), rng.uniform(0, 50)) for _ in range(8)]
        result = vormap_relax.lloyd_relaxation(points, iterations=3)

        init_score = vormap_relax.uniformity_score(points)
        final_score = vormap_relax.uniformity_score(result["points"])

        out = {
            "points": result["points"],
            "displacements": result["displacements"],
            "converged": result["converged"],
            "iterations": result["iterations"],
            "uniformity": {
                "initial": init_score,
                "final": final_score,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as f:
            json.dump(out, f)
            tmp = f.name

        try:
            with open(tmp) as f:
                loaded = json.load(f)
            self.assertIn("points", loaded)
            self.assertIn("uniformity", loaded)
            self.assertEqual(len(loaded["points"]), 8)
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
