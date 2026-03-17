"""Tests for vormap_buffer – Buffer Zone Analysis."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_buffer import (
    analyze_buffers,
    BufferReport,
    BufferOverlap,
    BufferContainment,
    _dist,
    _circle_area,
    _circle_overlap_area,
    print_buffer_report,
)


class TestGeometryHelpers(unittest.TestCase):
    def test_dist_same_point(self):
        self.assertAlmostEqual(_dist((0, 0), (0, 0)), 0.0)

    def test_dist_known(self):
        self.assertAlmostEqual(_dist((0, 0), (3, 4)), 5.0)

    def test_circle_area(self):
        self.assertAlmostEqual(_circle_area(10), math.pi * 100)

    def test_no_overlap(self):
        self.assertAlmostEqual(_circle_overlap_area(100, 10, 10), 0.0)

    def test_full_containment(self):
        self.assertAlmostEqual(
            _circle_overlap_area(0, 10, 5), _circle_area(5)
        )

    def test_partial_overlap(self):
        area = _circle_overlap_area(10, 10, 10)
        self.assertGreater(area, 0)
        self.assertLess(area, _circle_area(10))


class TestAnalyzeBuffers(unittest.TestCase):
    def test_empty(self):
        r = analyze_buffers([])
        self.assertEqual(r.point_count, 0)

    def test_single_point(self):
        r = analyze_buffers([(5, 5)], radius=10)
        self.assertEqual(r.point_count, 1)
        self.assertEqual(len(r.overlaps), 0)
        self.assertEqual(r.isolation_count, 1)

    def test_two_close_points(self):
        r = analyze_buffers([(0, 0), (5, 0)], radius=10)
        self.assertEqual(len(r.overlaps), 1)
        self.assertGreater(r.overlaps[0].overlap_area, 0)
        self.assertEqual(r.isolation_count, 0)

    def test_two_far_points(self):
        r = analyze_buffers([(0, 0), (500, 0)], radius=10)
        self.assertEqual(len(r.overlaps), 0)
        self.assertEqual(r.isolation_count, 2)

    def test_containment(self):
        r = analyze_buffers([(0, 0), (3, 0)], radius=10)
        self.assertGreater(len(r.containments), 0)

    def test_proximity_matrix_size(self):
        pts = [(i * 10, 0) for i in range(5)]
        r = analyze_buffers(pts, radius=15)
        self.assertEqual(len(r.proximity_matrix), 5)
        self.assertEqual(len(r.proximity_matrix[0]), 5)

    def test_coverage_ratio_range(self):
        pts = [(i * 10, j * 10) for i in range(3) for j in range(3)]
        r = analyze_buffers(pts, radius=20)
        self.assertGreaterEqual(r.coverage_ratio, 0)
        self.assertLessEqual(r.coverage_ratio, 1.0)

    def test_mean_neighbors(self):
        pts = [(0, 0), (5, 0), (500, 0)]
        r = analyze_buffers(pts, radius=10)
        self.assertGreater(r.mean_neighbors, 0)

    def test_multi_ring(self):
        pts = [(0, 0), (30, 0), (70, 0)]
        r = analyze_buffers(pts, radii=[25, 50, 100])
        self.assertEqual(len(r.ring_summary), 3)
        self.assertEqual(r.radius, 100)

    def test_ring_summary_fields(self):
        pts = [(0, 0), (10, 0)]
        r = analyze_buffers(pts, radii=[20, 50])
        for rs in r.ring_summary:
            self.assertIn("inner_radius", rs)
            self.assertIn("outer_radius", rs)
            self.assertIn("ring_area", rs)
            self.assertIn("avg_points_in_ring", rs)


class TestBufferReport(unittest.TestCase):
    def setUp(self):
        self.pts = [(i * 20, j * 20) for i in range(3) for j in range(3)]
        self.report = analyze_buffers(self.pts, radius=25)
        self._files: list = []

    def tearDown(self):
        for f in self._files:
            if os.path.exists(f):
                os.unlink(f)

    def _relpath(self, suffix: str) -> str:
        import uuid
        name = f"_test_buffer_{uuid.uuid4().hex[:8]}{suffix}"
        self._files.append(name)
        return name

    def test_to_dict(self):
        d = self.report.to_dict()
        self.assertIn("radius", d)
        self.assertIn("coverage_ratio", d)
        self.assertIn("overlaps", d)

    def test_to_json(self):
        path = self._relpath(".json")
        self.report.to_json(path)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(data["point_count"], 9)

    def test_to_csv(self):
        path = self._relpath(".csv")
        text = self.report.to_csv(path)
        self.assertIn("radius", text)
        self.assertIn("coverage_ratio", text)

    def test_to_svg(self):
        path = self._relpath(".svg")
        svg = self.report.to_svg(path)
        self.assertIn("<svg", svg)
        self.assertIn("Buffer Zones", svg)


class TestOverlapDataclass(unittest.TestCase):
    def test_to_dict(self):
        o = BufferOverlap(0, 1, (0, 0), (5, 0), 5.0, 10.0, 0.5)
        d = o.to_dict()
        self.assertEqual(d["index_a"], 0)
        self.assertEqual(d["overlap_pct"], 0.5)


class TestContainmentDataclass(unittest.TestCase):
    def test_to_dict(self):
        c = BufferContainment(0, 1, (0, 0), (3, 0), 3.0)
        d = c.to_dict()
        self.assertEqual(d["container_index"], 0)
        self.assertEqual(d["distance"], 3.0)


class TestPrintReport(unittest.TestCase):
    def test_print_no_error(self):
        pts = [(0, 0), (10, 0), (20, 0)]
        r = analyze_buffers(pts, radius=15)
        print_buffer_report(r)  # should not raise

    def test_print_with_rings(self):
        pts = [(0, 0), (10, 0)]
        r = analyze_buffers(pts, radii=[10, 20, 50])
        print_buffer_report(r)

    def test_print_empty(self):
        r = analyze_buffers([])
        print_buffer_report(r)


class TestEdgeCases(unittest.TestCase):
    def test_coincident_points(self):
        r = analyze_buffers([(5, 5), (5, 5)], radius=10)
        self.assertEqual(len(r.overlaps), 1)
        self.assertAlmostEqual(r.overlaps[0].overlap_pct, 1.0)

    def test_large_radius(self):
        pts = [(0, 0), (100, 0)]
        r = analyze_buffers(pts, radius=1000)
        self.assertEqual(len(r.containments), 2)

    def test_single_ring(self):
        r = analyze_buffers([(0, 0), (10, 0)], radii=[50])
        self.assertEqual(len(r.ring_summary), 1)

    def test_symmetry(self):
        pts = [(0, 0), (10, 0)]
        r = analyze_buffers(pts, radius=20)
        m = r.proximity_matrix
        self.assertAlmostEqual(m[0][1], m[1][0])


if __name__ == "__main__":
    unittest.main()
