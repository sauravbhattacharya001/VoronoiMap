"""Tests for vormap_hull — convex hull and bounding geometry."""

import math
import pytest
from vormap_hull import (
    convex_hull,
    bounding_rect,
    bounding_circle,
    hull_analysis,
    format_report,
    ConvexHullResult,
)


# ── convex_hull ─────────────────────────────────────────────────────

class TestConvexHull:
    def test_empty(self):
        r = convex_hull([])
        assert r.n_vertices == 0
        assert r.vertices == []

    def test_single_point(self):
        r = convex_hull([(5, 5)])
        assert r.n_vertices == 1
        assert r.vertices == [(5, 5)]
        assert r.hull_ratio == 1.0

    def test_two_points(self):
        r = convex_hull([(0, 0), (3, 4)])
        assert r.n_vertices == 2
        assert r.area == pytest.approx(0.0)
        assert r.perimeter == pytest.approx(10.0)  # 2 * distance(5)

    def test_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        r = convex_hull(pts)
        assert r.n_vertices == 4
        assert r.area == pytest.approx(100.0)
        assert r.perimeter == pytest.approx(40.0)
        assert r.hull_ratio == pytest.approx(1.0)

    def test_interior_points_excluded(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        r = convex_hull(pts)
        assert r.n_vertices == 4
        assert r.hull_ratio == pytest.approx(4 / 5)

    def test_duplicate_points(self):
        pts = [(0, 0), (0, 0), (1, 0), (1, 1), (0, 1)]
        r = convex_hull(pts)
        assert r.n_vertices == 4
        assert r.area == pytest.approx(1.0)

    def test_collinear_points(self):
        pts = [(0, 0), (1, 1), (2, 2), (3, 3)]
        r = convex_hull(pts)
        assert r.n_vertices == 2
        assert r.area == pytest.approx(0.0)

    def test_diameter(self):
        pts = [(0, 0), (3, 0), (3, 4), (0, 4)]
        r = convex_hull(pts)
        assert r.diameter == pytest.approx(5.0)  # diagonal

    def test_compactness_circle_like(self):
        # Regular polygon approximates circle → compactness near 1
        n = 100
        pts = [(math.cos(2 * math.pi * i / n),
                math.sin(2 * math.pi * i / n)) for i in range(n)]
        r = convex_hull(pts)
        assert r.compactness > 0.95

    def test_compactness_elongated(self):
        # Very elongated shape → low compactness
        pts = [(0, 0), (1000, 0), (1000, 1), (0, 1)]
        r = convex_hull(pts)
        assert r.compactness < 0.1

    def test_centroid_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        r = convex_hull(pts)
        assert r.centroid[0] == pytest.approx(5.0, abs=0.5)
        assert r.centroid[1] == pytest.approx(5.0, abs=0.5)

    def test_to_dict(self):
        r = convex_hull([(0, 0), (1, 0), (1, 1), (0, 1)])
        d = r.to_dict()
        assert "vertices" in d
        assert "area" in d
        assert "compactness" in d
        assert d["n_vertices"] == 4


# ── bounding_rect ───────────────────────────────────────────────────

class TestBoundingRect:
    def test_axis_aligned_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        r = bounding_rect(pts)
        assert r.width == pytest.approx(10.0, abs=0.5)
        assert r.height == pytest.approx(10.0, abs=0.5)
        assert r.aspect_ratio == pytest.approx(1.0, abs=0.1)

    def test_rectangle_area(self):
        pts = [(0, 0), (4, 0), (4, 3), (0, 3)]
        r = bounding_rect(pts)
        assert r.area == pytest.approx(12.0, abs=1.0)


# ── bounding_circle ─────────────────────────────────────────────────

class TestBoundingCircle:
    def test_two_points(self):
        c = bounding_circle([(0, 0), (10, 0)])
        assert c.radius == pytest.approx(5.0)
        assert c.cx == pytest.approx(5.0)
        assert c.cy == pytest.approx(0.0)

    def test_encloses_all_points(self):
        pts = [(0, 0), (10, 0), (5, 8), (2, 3), (7, 1)]
        c = bounding_circle(pts)
        for x, y in pts:
            dist = math.sqrt((x - c.cx) ** 2 + (y - c.cy) ** 2)
            assert dist <= c.radius + 1e-6


# ── hull_analysis + format_report ───────────────────────────────────

class TestHullAnalysis:
    def test_runs_without_error(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        result = hull_analysis(pts)
        assert hasattr(result, "hull") or hasattr(result, "convex_hull")
        # Should have bounding geometry attributes
        report = format_report(result)
        assert len(report) > 50

    def test_format_report(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        result = hull_analysis(pts)
        report = format_report(result)
        assert "Hull" in report or "hull" in report.lower()
        assert len(report) > 50
