"""Smoke tests for vormap core functions.

These tests verify that the fundamental building blocks work correctly
without requiring large datasets or long-running estimation loops.
"""

import math
import os
import sys
import tempfile

import pytest

# Ensure the repo root is on sys.path so vormap can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap


# ── Geometry helpers ──────────────────────────────────────────────────

class TestMidPoint:
    def test_basic(self):
        mx, my = vormap.mid_point(0, 0, 10, 10)
        assert mx == 5.0
        assert my == 5.0

    def test_negative_coords(self):
        mx, my = vormap.mid_point(-4, -6, 4, 6)
        assert mx == 0.0
        assert my == 0.0


class TestEudist:
    def test_same_point(self):
        assert vormap.eudist(3, 4, 3, 4) == 0.0

    def test_unit_distance(self):
        assert vormap.eudist(0, 0, 1, 0) == 1.0

    def test_diagonal(self):
        d = vormap.eudist(0, 0, 3, 4)
        assert abs(d - 5.0) < 1e-9


class TestCollinear:
    def test_collinear_points(self):
        assert vormap.collinear(0, 0, 1, 1, 2, 2) is True

    def test_non_collinear_points(self):
        assert vormap.collinear(0, 0, 1, 1, 2, 0) is False

    def test_horizontal_line(self):
        assert vormap.collinear(0, 5, 10, 5, 20, 5) is True

    def test_vertical_line(self):
        assert vormap.collinear(3, 0, 3, 10, 3, 20) is True


class TestPerpDir:
    def test_horizontal_segment(self):
        # Perpendicular to horizontal line → infinite slope
        assert vormap.perp_dir(0, 5, 10, 5) == math.inf

    def test_vertical_segment(self):
        # Perpendicular to vertical line → slope 0
        result = vormap.perp_dir(5, 0, 5, 10)
        assert result == 0.0

    def test_diagonal_segment(self):
        result = vormap.perp_dir(0, 0, 10, 10)
        assert result == -1.0


class TestPolygonArea:
    def test_unit_square(self):
        # Square with corners at (0,0), (1,0), (1,1), (0,1)
        lngs = [0, 1, 1, 0]
        lats = [0, 0, 1, 1]
        area = vormap.polygon_area(lngs, lats)
        assert abs(area - 1.0) < 0.01

    def test_triangle(self):
        lngs = [0, 4, 0]
        lats = [0, 0, 3]
        area = vormap.polygon_area(lngs, lats)
        assert abs(area - 6.0) < 0.01


# ── Data loading & bounds ────────────────────────────────────────────

class TestComputeBounds:
    def test_basic_bounds(self):
        points = [(100, 200), (300, 400), (500, 600)]
        s, n, w, e = vormap.compute_bounds(points)
        # Should have 10% padding (at least 1 unit)
        assert s < 200
        assert n > 600
        assert w < 100
        assert e > 500

    def test_single_point(self):
        points = [(50, 50)]
        s, n, w, e = vormap.compute_bounds(points)
        # With only one point, range is 0, so padding is 1 unit min
        assert s <= 49.0
        assert n >= 51.0


class TestLoadData:
    def test_load_from_data_dir(self, tmp_path):
        """Verify load_data reads from data/ subdirectory and caches."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        test_file = data_dir / "test_load.txt"
        test_file.write_text("10.0 20.0\n30.0 40.0\n50.0 60.0\n")

        # Temporarily change working directory
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            # Clear cache for this test
            vormap._data_cache.pop("test_load.txt", None)
            vormap._kdtree_cache.pop("test_load.txt", None)

            points = vormap.load_data("test_load.txt")
            assert len(points) == 3
            assert points[0] == (10.0, 20.0)
            assert points[2] == (50.0, 60.0)

            # Second call should return cached data
            points2 = vormap.load_data("test_load.txt")
            assert points2 is points  # same object (cached)
        finally:
            os.chdir(old_cwd)
            vormap._data_cache.pop("test_load.txt", None)
            vormap._kdtree_cache.pop("test_load.txt", None)


# ── Nearest neighbor ─────────────────────────────────────────────────

class TestGetNN:
    def setup_method(self):
        """Create a simple point set for NN queries."""
        self.points = [(0, 0), (10, 0), (0, 10), (10, 10)]

    def test_nearest_to_origin_region(self):
        # Point (1, 1) should be closest to (0, 0)
        # Temporarily register in cache for KDTree path
        lng, lat = vormap.get_NN(self.points, 1, 1)
        assert (lng, lat) == (0, 0)

    def test_nearest_to_far_corner(self):
        lng, lat = vormap.get_NN(self.points, 9, 9)
        assert (lng, lat) == (10, 10)


# ── Set bounds ───────────────────────────────────────────────────────

class TestSetBounds:
    def test_set_and_read(self):
        old = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
        try:
            vormap.set_bounds(-10, 10, -20, 20)
            assert vormap.IND_S == -10
            assert vormap.IND_N == 10
            assert vormap.IND_W == -20
            assert vormap.IND_E == 20
        finally:
            vormap.set_bounds(*old)


# ── Security: path traversal ────────────────────────────────────────

class TestPathTraversal:
    """Verify that load_data rejects filenames that escape data/."""

    def test_reject_dot_dot(self, tmp_path):
        """Relative path traversal via '..' must be rejected."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(ValueError, match="[Pp]ath traversal"):
                vormap.load_data("../../etc/passwd")
        finally:
            os.chdir(old_cwd)

    def test_reject_absolute_path(self, tmp_path):
        """Absolute paths must be rejected outright."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(ValueError, match="[Aa]bsolute"):
                vormap.load_data("/etc/passwd")
        finally:
            os.chdir(old_cwd)

    def test_reject_dot_dot_backslash(self, tmp_path):
        """Path traversal with mixed separators must be rejected."""
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            with pytest.raises(ValueError, match="[Pp]ath traversal|[Aa]bsolute"):
                vormap.load_data("..\\..\\windows\\system32\\config\\sam")
        finally:
            os.chdir(old_cwd)

    def test_normal_filename_accepted(self, tmp_path):
        """A plain filename inside data/ should work normally."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "safe.txt").write_text("1.0 2.0\n3.0 4.0\n")

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            vormap._data_cache.pop("safe.txt", None)
            vormap._kdtree_cache.pop("safe.txt", None)
            points = vormap.load_data("safe.txt")
            assert len(points) == 2
        finally:
            os.chdir(old_cwd)
            vormap._data_cache.pop("safe.txt", None)
            vormap._kdtree_cache.pop("safe.txt", None)


# ── Security: malformed input ───────────────────────────────────────

class TestMalformedInput:
    """Verify load_data handles malformed/malicious data gracefully."""

    def test_nan_coordinates_skipped(self, tmp_path):
        """NaN coordinates should be silently skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "bad.txt").write_text("nan inf\n1.0 2.0\n")

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            vormap._data_cache.pop("bad.txt", None)
            vormap._kdtree_cache.pop("bad.txt", None)
            points = vormap.load_data("bad.txt")
            assert len(points) == 1
            assert points[0] == (1.0, 2.0)
        finally:
            os.chdir(old_cwd)
            vormap._data_cache.pop("bad.txt", None)
            vormap._kdtree_cache.pop("bad.txt", None)

    def test_non_numeric_lines_skipped(self, tmp_path):
        """Lines with non-numeric content should be skipped."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "mixed.txt").write_text(
            "hello world\n1.0 2.0\n__import__('os').system('ls')\n3.0 4.0\n"
        )

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            vormap._data_cache.pop("mixed.txt", None)
            vormap._kdtree_cache.pop("mixed.txt", None)
            points = vormap.load_data("mixed.txt")
            assert len(points) == 2
        finally:
            os.chdir(old_cwd)
            vormap._data_cache.pop("mixed.txt", None)
            vormap._kdtree_cache.pop("mixed.txt", None)
