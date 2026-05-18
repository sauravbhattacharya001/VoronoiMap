"""Tests for vormap_recommend — spatial analysis recommender."""

import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vormap_recommend as rec


def _write_points(pts, tmpdir=None):
    """Write points to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=".txt", dir=tmpdir)
    with os.fdopen(fd, "w") as f:
        for x, y in pts:
            f.write(f"{x} {y}\n")
    return path


class TestLoadPoints(unittest.TestCase):
    def test_basic_load(self):
        path = _write_points([(1, 2), (3, 4), (5, 6)])
        try:
            pts = rec._load_points(path)
            self.assertEqual(len(pts), 3)
            self.assertAlmostEqual(pts[0][0], 1.0)
            self.assertAlmostEqual(pts[2][1], 6.0)
        finally:
            os.unlink(path)

    def test_skip_comments_and_blanks(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("# header comment\n")
            f.write("\n")
            f.write("10 20\n")
            f.write("# another comment\n")
            f.write("30 40\n")
        try:
            pts = rec._load_points(path)
            self.assertEqual(len(pts), 2)
        finally:
            os.unlink(path)

    def test_malformed_lines_skipped(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("10 20\n")
            f.write("abc def\n")
            f.write("30 40\n")
        try:
            pts = rec._load_points(path)
            self.assertEqual(len(pts), 2)
        finally:
            os.unlink(path)

    def test_extra_columns_ignored(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("10 20 99 extra\n")
            f.write("30 40 88\n")
        try:
            pts = rec._load_points(path)
            self.assertEqual(len(pts), 2)
            self.assertAlmostEqual(pts[0], (10.0, 20.0))
        finally:
            os.unlink(path)


class TestBoundingBox(unittest.TestCase):
    def test_basic(self):
        pts = [(0, 0), (10, 5), (3, 8)]
        xmin, ymin, xmax, ymax = rec._bounding_box(pts)
        self.assertEqual(xmin, 0)
        self.assertEqual(ymin, 0)
        self.assertEqual(xmax, 10)
        self.assertEqual(ymax, 8)

    def test_single_point(self):
        pts = [(5, 7)]
        xmin, ymin, xmax, ymax = rec._bounding_box(pts)
        self.assertEqual(xmin, 5)
        self.assertEqual(xmax, 5)


class TestDistancesToNearest(unittest.TestCase):
    def test_two_points(self):
        pts = [(0, 0), (3, 4)]
        dists = rec._distances_to_nearest(pts)
        self.assertEqual(len(dists), 2)
        self.assertAlmostEqual(dists[0], 5.0)
        self.assertAlmostEqual(dists[1], 5.0)

    def test_three_collinear(self):
        pts = [(0, 0), (1, 0), (3, 0)]
        dists = rec._distances_to_nearest(pts)
        self.assertAlmostEqual(dists[0], 1.0)  # nearest to (1,0)
        self.assertAlmostEqual(dists[1], 1.0)  # nearest to (0,0)
        self.assertAlmostEqual(dists[2], 2.0)  # nearest to (1,0)


class TestHopkinsStatistic(unittest.TestCase):
    def test_range(self):
        rng = random.Random(123)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        h = rec._hopkins_statistic(pts, seed=99)
        self.assertGreaterEqual(h, 0.0)
        self.assertLessEqual(h, 1.0)

    def test_clustered_data_high_hopkins(self):
        """Tightly clustered data should give Hopkins > 0.5."""
        pts = []
        rng = random.Random(42)
        # Two tight clusters
        for _ in range(30):
            pts.append((rng.gauss(10, 0.5), rng.gauss(10, 0.5)))
        for _ in range(30):
            pts.append((rng.gauss(90, 0.5), rng.gauss(90, 0.5)))
        h = rec._hopkins_statistic(pts, seed=7)
        self.assertGreater(h, 0.5)

    def test_deterministic_with_seed(self):
        rng = random.Random(0)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(40)]
        h1 = rec._hopkins_statistic(pts, seed=42)
        h2 = rec._hopkins_statistic(pts, seed=42)
        self.assertEqual(h1, h2)


class TestNearestNeighborRatio(unittest.TestCase):
    def test_two_points_returns_one(self):
        r = rec._nearest_neighbor_ratio([(0, 0), (1, 0)])
        self.assertAlmostEqual(r, 1.0)

    def test_grid_regular(self):
        """A regular grid should have R > 1."""
        pts = [(i, j) for i in range(10) for j in range(10)]
        r = rec._nearest_neighbor_ratio(pts)
        self.assertGreater(r, 1.0)


class TestConvexHullArea(unittest.TestCase):
    def test_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        area = rec._convex_hull_area(pts)
        self.assertAlmostEqual(area, 100.0)

    def test_triangle(self):
        pts = [(0, 0), (4, 0), (0, 3)]
        area = rec._convex_hull_area(pts)
        self.assertAlmostEqual(area, 6.0)

    def test_collinear_returns_zero(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        area = rec._convex_hull_area(pts)
        self.assertAlmostEqual(area, 0.0)

    def test_fewer_than_three_unique(self):
        pts = [(5, 5), (5, 5)]
        area = rec._convex_hull_area(pts)
        self.assertEqual(area, 0.0)


class TestRecommend(unittest.TestCase):
    def test_too_few_points(self):
        path = _write_points([(1, 2)])
        try:
            recs = rec.recommend(path)
            self.assertEqual(len(recs), 1)
            self.assertIn("Only 1 points", recs[0]["reason"])
        finally:
            os.unlink(path)

    def test_two_points(self):
        path = _write_points([(0, 0), (1, 1)])
        try:
            recs = rec.recommend(path)
            self.assertEqual(recs[0]["tool"], "N/A")
        finally:
            os.unlink(path)

    def test_moderate_random_data(self):
        """50 random points should produce multiple recommendations."""
        rng = random.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        path = _write_points(pts)
        try:
            recs = rec.recommend(path)
            self.assertGreater(len(recs), 2)
            # Should include visualization for 50 points
            tools = [r["tool"] for r in recs]
            self.assertIn("vormap_viz", tools)
        finally:
            os.unlink(path)

    def test_top_parameter(self):
        rng = random.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        path = _write_points(pts)
        try:
            recs = rec.recommend(path, top=2)
            self.assertLessEqual(len(recs), 2)
        finally:
            os.unlink(path)

    def test_clustered_data_recommends_cluster(self):
        """Strongly clustered data should recommend cluster analysis."""
        pts = []
        rng = random.Random(42)
        for _ in range(40):
            pts.append((rng.gauss(10, 1), rng.gauss(10, 1)))
        for _ in range(40):
            pts.append((rng.gauss(90, 1), rng.gauss(90, 1)))
        path = _write_points(pts)
        try:
            recs = rec.recommend(path)
            tools = [r["tool"] for r in recs]
            self.assertIn("vormap_cluster", tools)
        finally:
            os.unlink(path)

    def test_regular_grid_recommends_regularity(self):
        """A regular grid should trigger regularity recommendation."""
        pts = [(i * 10, j * 10) for i in range(8) for j in range(8)]
        path = _write_points(pts)
        try:
            recs = rec.recommend(path)
            tools = [r["tool"] for r in recs]
            self.assertIn("vormap_regularity", tools)
        finally:
            os.unlink(path)

    def test_elongated_data_recommends_trend(self):
        """Elongated bounding box should trigger trend analysis."""
        rng = random.Random(42)
        pts = [(rng.uniform(0, 1000), rng.uniform(0, 10)) for _ in range(30)]
        path = _write_points(pts)
        try:
            recs = rec.recommend(path)
            tools = [r["tool"] for r in recs]
            self.assertIn("vormap_trend", tools)
        finally:
            os.unlink(path)

    def test_recommendations_have_required_keys(self):
        rng = random.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
        path = _write_points(pts)
        try:
            recs = rec.recommend(path)
            for r in recs:
                self.assertIn("priority", r)
                self.assertIn("tool", r)
                self.assertIn("command", r)
                self.assertIn("reason", r)
        finally:
            os.unlink(path)

    def test_priorities_are_sequential(self):
        rng = random.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        path = _write_points(pts)
        try:
            recs = rec.recommend(path)
            priorities = [r["priority"] for r in recs]
            for i in range(1, len(priorities)):
                self.assertGreaterEqual(priorities[i], priorities[i - 1])
        finally:
            os.unlink(path)


class TestFormatTable(unittest.TestCase):
    def test_basic_format(self):
        recs = [
            {"priority": 1, "tool": "vormap_cluster", "command": "python vormap_cluster.py data.txt", "reason": "Clustering detected."},
            {"priority": 2, "tool": "vormap_viz", "command": "python vormap.py data.txt 5", "reason": "Good starting point."},
        ]
        output = rec._format_table(recs)
        self.assertIn("vormap_cluster", output)
        self.assertIn("vormap_viz", output)
        self.assertIn("Clustering detected", output)


class TestFormatHtml(unittest.TestCase):
    def test_contains_html_structure(self):
        recs = [
            {"priority": 1, "tool": "vormap_kde", "command": "python vormap_kde.py data.txt", "reason": "KDE analysis."},
        ]
        html = rec._format_html(recs, "data.txt")
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("vormap_kde", html)
        self.assertIn("data.txt", html)


if __name__ == "__main__":
    unittest.main()
