"""Tests for vormap_hull — Convex Hull & Bounding Geometry."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_hull import (
    BoundingCircleResult,
    BoundingRectResult,
    ConvexHullResult,
    HullAnalysis,
    bounding_circle,
    bounding_rect,
    convex_hull,
    export_json,
    export_svg,
    format_report,
    hull_analysis,
)


class TestConvexHull(unittest.TestCase):
    """Tests for convex_hull()."""

    def test_empty(self):
        h = convex_hull([])
        self.assertEqual(h.vertices, [])
        self.assertEqual(h.n_vertices, 0)

    def test_single_point(self):
        h = convex_hull([(5.0, 3.0)])
        self.assertEqual(h.n_vertices, 1)
        self.assertAlmostEqual(h.centroid[0], 5.0)
        self.assertAlmostEqual(h.centroid[1], 3.0)
        self.assertEqual(h.hull_ratio, 1.0)

    def test_two_points(self):
        h = convex_hull([(0.0, 0.0), (3.0, 4.0)])
        self.assertEqual(h.n_vertices, 2)
        self.assertAlmostEqual(h.diameter, 5.0)
        self.assertAlmostEqual(h.area, 0.0)

    def test_collinear_points(self):
        pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        h = convex_hull(pts)
        self.assertEqual(h.n_vertices, 2)
        self.assertAlmostEqual(h.area, 0.0)

    def test_triangle(self):
        pts = [(0.0, 0.0), (4.0, 0.0), (0.0, 3.0)]
        h = convex_hull(pts)
        self.assertEqual(h.n_vertices, 3)
        self.assertAlmostEqual(h.area, 6.0)
        self.assertAlmostEqual(h.diameter, 5.0)

    def test_square(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        h = convex_hull(pts)
        self.assertEqual(h.n_vertices, 4)
        self.assertAlmostEqual(h.area, 1.0)
        self.assertAlmostEqual(h.perimeter, 4.0)

    def test_square_compactness(self):
        """Square is less compact than a circle."""
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        h = convex_hull(pts)
        expected = 4.0 * math.pi * 1.0 / (4.0 ** 2)
        self.assertAlmostEqual(h.compactness, expected, places=4)
        self.assertLess(h.compactness, 1.0)

    def test_interior_points_excluded(self):
        """Interior points shouldn't appear on the hull."""
        pts = [
            (0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0),
            (5.0, 5.0), (3.0, 3.0), (7.0, 7.0),  # interior
        ]
        h = convex_hull(pts)
        self.assertEqual(h.n_vertices, 4)
        self.assertAlmostEqual(h.area, 100.0)

    def test_hull_ratio(self):
        pts = [
            (0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0),
            (5.0, 5.0),  # interior point
        ]
        h = convex_hull(pts)
        self.assertAlmostEqual(h.hull_ratio, 4 / 5)

    def test_diameter_pair(self):
        pts = [(0.0, 0.0), (3.0, 4.0), (1.0, 1.0)]
        h = convex_hull(pts)
        self.assertAlmostEqual(h.diameter, 5.0)
        self.assertIn((0.0, 0.0), h.diameter_pair)
        self.assertIn((3.0, 4.0), h.diameter_pair)

    def test_duplicate_points(self):
        pts = [(1.0, 1.0)] * 5
        h = convex_hull(pts)
        self.assertEqual(h.n_vertices, 1)

    def test_regular_hexagon_compactness(self):
        """Regular hexagon is more compact than a square."""
        pts = []
        for i in range(6):
            angle = math.pi / 3 * i
            pts.append((math.cos(angle), math.sin(angle)))
        h = convex_hull(pts)
        self.assertEqual(h.n_vertices, 6)
        self.assertGreater(h.compactness, 0.9)  # hexagon is close to circle

    def test_ccw_ordering(self):
        """Hull vertices should be in counter-clockwise order."""
        pts = [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)]
        h = convex_hull(pts)
        # Cross product of consecutive edges should be positive (CCW)
        verts = h.vertices
        for i in range(len(verts)):
            o = verts[i]
            a = verts[(i + 1) % len(verts)]
            b = verts[(i + 2) % len(verts)]
            cross = (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
            self.assertGreaterEqual(cross, 0)

    def test_to_dict(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        h = convex_hull(pts)
        d = h.to_dict()
        self.assertIn("vertices", d)
        self.assertIn("area", d)
        self.assertIn("compactness", d)
        self.assertEqual(len(d["vertices"]), 3)


class TestBoundingRect(unittest.TestCase):
    """Tests for bounding_rect()."""

    def test_empty(self):
        r = bounding_rect([])
        self.assertEqual(r.corners, [])

    def test_single_point(self):
        r = bounding_rect([(5.0, 5.0)])
        self.assertEqual(r.area, 0.0)

    def test_axis_aligned_square(self):
        pts = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        r = bounding_rect(pts)
        self.assertAlmostEqual(r.area, 4.0)
        self.assertAlmostEqual(r.width, 2.0)
        self.assertAlmostEqual(r.height, 2.0)
        self.assertAlmostEqual(r.aspect_ratio, 1.0)

    def test_rectangle_elongated(self):
        pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 1.0), (0.0, 1.0)]
        r = bounding_rect(pts)
        self.assertAlmostEqual(r.width, 1.0)
        self.assertAlmostEqual(r.height, 10.0)
        self.assertAlmostEqual(r.aspect_ratio, 0.1)

    def test_rotated_rectangle(self):
        """45° rotated square should have MBR area = 2 (not 4)."""
        s = math.sqrt(2) / 2
        pts = [(0.0, s), (s, 0.0), (0.0, -s), (-s, 0.0)]
        r = bounding_rect(pts)
        # MBR of 45° diamond = the diamond itself = area 2*s*s*2 = 2s²*2...
        # Actually a unit square rotated 45°: side=1, area=1
        # The diamond has diagonals sqrt(2) each → area = d1*d2/2 = 1
        # MBR should match the diamond area (it IS a rectangle)
        self.assertAlmostEqual(r.area, 1.0, places=3)

    def test_hull_fill_ratio(self):
        pts = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)]
        r = bounding_rect(pts)
        # Square hull fills rectangle perfectly
        self.assertAlmostEqual(r.hull_fill_ratio, 1.0, places=2)

    def test_center(self):
        pts = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)]
        r = bounding_rect(pts)
        self.assertAlmostEqual(r.center[0], 2.0, places=1)
        self.assertAlmostEqual(r.center[1], 1.0, places=1)

    def test_to_dict(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        r = bounding_rect(pts)
        d = r.to_dict()
        self.assertIn("corners", d)
        self.assertIn("width", d)
        self.assertIn("aspect_ratio", d)


class TestBoundingCircle(unittest.TestCase):
    """Tests for bounding_circle()."""

    def test_empty(self):
        c = bounding_circle([])
        self.assertEqual(c.radius, 0.0)

    def test_single_point(self):
        c = bounding_circle([(3.0, 4.0)])
        self.assertAlmostEqual(c.cx, 3.0)
        self.assertAlmostEqual(c.cy, 4.0)
        self.assertAlmostEqual(c.radius, 0.0)

    def test_two_points(self):
        c = bounding_circle([(0.0, 0.0), (4.0, 0.0)])
        self.assertAlmostEqual(c.cx, 2.0)
        self.assertAlmostEqual(c.cy, 0.0)
        self.assertAlmostEqual(c.radius, 2.0)

    def test_equilateral_triangle(self):
        s = 2.0
        pts = [
            (0.0, 0.0),
            (s, 0.0),
            (s / 2, s * math.sqrt(3) / 2),
        ]
        c = bounding_circle(pts)
        expected_r = s / math.sqrt(3)
        self.assertAlmostEqual(c.radius, expected_r, places=4)

    def test_square_enclosure(self):
        """Circle should enclose all points of a unit square."""
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        c = bounding_circle(pts)
        for p in pts:
            dist = math.sqrt((p[0] - c.cx) ** 2 + (p[1] - c.cy) ** 2)
            self.assertLessEqual(dist, c.radius + 1e-6)

    def test_all_enclosed(self):
        """All points should be within the bounding circle."""
        pts = [
            (0.0, 0.0), (5.0, 3.0), (2.0, 7.0),
            (8.0, 1.0), (3.0, 4.0), (6.0, 6.0),
        ]
        c = bounding_circle(pts)
        for p in pts:
            dist = math.sqrt((p[0] - c.cx) ** 2 + (p[1] - c.cy) ** 2)
            self.assertLessEqual(dist, c.radius + 1e-6)

    def test_hull_fill_ratio(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        c = bounding_circle(pts)
        self.assertGreater(c.hull_fill_ratio, 0)
        self.assertLessEqual(c.hull_fill_ratio, 1.0)

    def test_area_formula(self):
        pts = [(0.0, 0.0), (6.0, 0.0)]
        c = bounding_circle(pts)
        self.assertAlmostEqual(c.area, math.pi * 9.0)
        self.assertAlmostEqual(c.circumference, 2 * math.pi * 3.0)

    def test_to_dict(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        c = bounding_circle(pts)
        d = c.to_dict()
        self.assertIn("center", d)
        self.assertIn("radius", d)
        self.assertIn("circumference", d)


class TestHullAnalysis(unittest.TestCase):
    """Tests for hull_analysis()."""

    def test_full_analysis(self):
        pts = [
            (0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0),
            (5.0, 5.0),
        ]
        a = hull_analysis(pts)
        self.assertEqual(a.n_points, 5)
        self.assertEqual(a.hull.n_vertices, 4)
        self.assertAlmostEqual(a.hull.area, 100.0)
        self.assertGreater(a.dispersion, 0)

    def test_elongation_square(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        a = hull_analysis(pts)
        self.assertAlmostEqual(a.elongation, 0.0, places=2)

    def test_elongation_line(self):
        pts = [(0.0, 0.0), (100.0, 0.0), (50.0, 0.1)]
        a = hull_analysis(pts)
        self.assertGreater(a.elongation, 0.9)

    def test_to_dict(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
        a = hull_analysis(pts)
        d = a.to_dict()
        self.assertIn("hull", d)
        self.assertIn("bounding_rectangle", d)
        self.assertIn("bounding_circle", d)
        self.assertIn("dispersion", d)
        self.assertIn("elongation", d)


class TestFormatReport(unittest.TestCase):
    """Tests for format_report()."""

    def test_report_content(self):
        pts = [(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)]
        a = hull_analysis(pts)
        text = format_report(a)
        self.assertIn("Convex Hull", text)
        self.assertIn("Bounding Rectangle", text)
        self.assertIn("Bounding Circle", text)
        self.assertIn("Dispersion", text)
        self.assertIn("Elongation", text)
        self.assertIn("4", text)  # n_vertices

    def test_report_numeric_values(self):
        pts = [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]
        a = hull_analysis(pts)
        text = format_report(a)
        self.assertIn("6.00", text)  # area of 3-4-5 triangle


class TestExports(unittest.TestCase):
    """Tests for export_svg() and export_json()."""

    def setUp(self):
        self.pts = [
            (0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0),
            (5.0, 5.0), (3.0, 7.0),
        ]
        self.analysis = hull_analysis(self.pts)

    def test_export_svg(self):
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            export_svg(self.analysis, self.pts, path)
            with open(path) as f:
                content = f.read()
            self.assertIn('<svg', content)
            self.assertIn('Convex Hull', content)
            self.assertGreater(len(content), 500)
        finally:
            os.unlink(path)

    def test_export_svg_empty(self):
        """Should handle empty point set gracefully."""
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            empty = hull_analysis([])
            export_svg(empty, [], path)
            # Should not crash
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_export_json(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name
        try:
            export_json(self.analysis, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("hull", data)
            self.assertIn("bounding_rectangle", data)
            self.assertIn("bounding_circle", data)
            self.assertEqual(data["n_points"], 6)
        finally:
            os.unlink(path)

    def test_json_roundtrip_values(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name
        try:
            export_json(self.analysis, path)
            with open(path) as f:
                data = json.load(f)
            self.assertAlmostEqual(data["hull"]["area"], 100.0, places=2)
            self.assertEqual(data["hull"]["n_vertices"], 4)
        finally:
            os.unlink(path)


class TestLargePointSet(unittest.TestCase):
    """Tests with larger point sets."""

    def test_random_circle_points(self):
        """Points on a circle should have ~circular hull."""
        pts = []
        for i in range(100):
            angle = 2 * math.pi * i / 100
            pts.append((math.cos(angle) * 100, math.sin(angle) * 100))
        a = hull_analysis(pts)
        # All points should be on the hull
        self.assertEqual(a.hull.n_vertices, 100)
        # Compactness should be very close to 1
        self.assertGreater(a.hull.compactness, 0.99)
        # MBC radius should be ~100
        self.assertAlmostEqual(a.circle.radius, 100.0, places=0)

    def test_grid_points(self):
        """Regular grid should produce a square hull."""
        pts = [(float(x), float(y)) for x in range(10) for y in range(10)]
        a = hull_analysis(pts)
        # Hull should be a square with 4 vertices
        self.assertEqual(a.hull.n_vertices, 4)
        self.assertAlmostEqual(a.hull.area, 81.0)  # 9x9

    def test_many_interior_points(self):
        """Hull should only have boundary points."""
        import random
        random.seed(42)
        pts = [(random.uniform(0, 100), random.uniform(0, 100))
               for _ in range(500)]
        a = hull_analysis(pts)
        self.assertLess(a.hull.n_vertices, 500)
        self.assertGreater(a.hull.n_vertices, 3)
        self.assertGreater(a.hull.area, 0)
        # All points should be inside the bounding circle
        for p in pts:
            dist = math.sqrt((p[0] - a.circle.cx) ** 2
                             + (p[1] - a.circle.cy) ** 2)
            self.assertLessEqual(dist, a.circle.radius + 1e-3)


if __name__ == '__main__':
    unittest.main()
