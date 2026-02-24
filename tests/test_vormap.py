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


# ── Refactored get_sum (issue #14 fix) ──────────────────────────────

class TestGetSumBiasFix:
    """Verify that the refactored get_sum correctly excludes zero-area
    regions from the estimation average (issue #14)."""

    def test_valid_estimates_only(self):
        """Confirm that zero-area samples don't pollute the average.

        We monkeypatch find_area to return area=0 for one point and
        verify the estimate uses only the valid sample.
        """
        import unittest.mock as mock

        # Two-point dataset: both at known positions
        test_data = [(100.0, 100.0), (900.0, 900.0)]

        # Set bounds tightly
        old_bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
        vormap.set_bounds(0, 1000, 0, 1000)

        # Mock load_data to return our test points
        with mock.patch.object(vormap, 'load_data', return_value=test_data):
            # Mock find_area: first call returns 0 area, second returns valid
            with mock.patch.object(
                vormap, 'find_area',
                side_effect=[(0.0, 4), (500000.0, 6)]
            ):
                with mock.patch.object(
                    vormap, 'get_NN',
                    side_effect=[(100.0, 100.0), (900.0, 900.0)]
                ):
                    result, max_e, avg_e = vormap.get_sum("dummy.txt", 2)

        vormap.set_bounds(*old_bounds)

        # With the fix, only the valid estimate (area=500000) is used.
        # Estimate = total_area / area = 1000000 / 500000 = 2.0
        # Since Sum (2.0) <= N1 (2), the function returns int(2) + 1 = 3
        # The key verification: result is based on 1 valid sample, not
        # corrupted by the zero-area sample being included in the average.
        assert result == 3
        assert avg_e == 6.0  # only valid sample's edges counted

    def test_kdtree_by_id_populated(self, tmp_path):
        """Verify that _kdtree_by_id is populated on load_data."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "kdtest.txt").write_text("1.0 2.0\n3.0 4.0\n")

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            vormap._data_cache.pop("kdtest.txt", None)
            vormap._kdtree_cache.pop("kdtest.txt", None)
            points = vormap.load_data("kdtest.txt")

            if vormap._HAS_SCIPY:
                assert id(points) in vormap._kdtree_by_id
        finally:
            os.chdir(old_cwd)
            vormap._data_cache.pop("kdtest.txt", None)
            vormap._kdtree_cache.pop("kdtest.txt", None)
            vormap._kdtree_by_id.pop(id(points), None)


# ── isect (line-segment intersection) ────────────────────────────

class TestIsect:
    """Tests for the isect() line-segment intersection function."""

    def test_crossing_segments(self):
        """Two segments that cross → intersection point."""
        x, y = vormap.isect(0, 0, 10, 10, 0, 10, 10, 0)
        assert abs(x - 5.0) < 0.01
        assert abs(y - 5.0) < 0.01

    def test_parallel_horizontal(self):
        """Two parallel horizontal segments → (-1, -1)."""
        assert vormap.isect(0, 0, 10, 0, 0, 5, 10, 5) == (-1, -1)

    def test_parallel_vertical(self):
        """Two parallel vertical segments → (-1, -1)."""
        assert vormap.isect(0, 0, 0, 10, 5, 0, 5, 10) == (-1, -1)

    def test_t_intersection(self):
        """One segment endpoint touches the other segment."""
        # Horizontal seg (0,5)→(10,5), vertical seg (5,0)→(5,5)
        x, y = vormap.isect(0, 5, 10, 5, 5, 0, 5, 5)
        assert abs(x - 5.0) < 0.01
        assert abs(y - 5.0) < 0.01

    def test_non_intersecting(self):
        """Segments that don't intersect → (-1, -1)."""
        assert vormap.isect(0, 0, 1, 1, 5, 5, 6, 6) == (-1, -1)

    def test_vertical_crosses_horizontal(self):
        """Vertical segment crosses horizontal segment."""
        x, y = vormap.isect(5, 0, 5, 10, 0, 5, 10, 5)
        assert abs(x - 5.0) < 0.01
        assert abs(y - 5.0) < 0.01

    def test_shared_endpoint(self):
        """Two segments share an endpoint."""
        x, y = vormap.isect(0, 0, 5, 5, 5, 5, 10, 0)
        assert abs(x - 5.0) < 0.01
        assert abs(y - 5.0) < 0.01

    def test_collinear_overlapping(self):
        """Collinear overlapping segments → (-1, -1) (near-parallel)."""
        assert vormap.isect(0, 0, 10, 10, 5, 5, 15, 15) == (-1, -1)

    def test_diagonal_crossing(self):
        """Two diagonal segments crossing."""
        x, y = vormap.isect(0, 0, 10, 10, 10, 0, 0, 10)
        assert abs(x - 5.0) < 0.01
        assert abs(y - 5.0) < 0.01

    def test_first_vertical_second_general(self):
        """First segment vertical, second at an angle."""
        x, y = vormap.isect(3, 0, 3, 10, 0, 2, 6, 8)
        assert abs(x - 3.0) < 0.01
        assert abs(y - 5.0) < 0.01

    def test_non_intersecting_far_apart(self):
        """Two segments that are far apart → (-1, -1)."""
        assert vormap.isect(0, 0, 1, 1, 10, 10, 11, 11) == (-1, -1)


# ── _slopes_equal ────────────────────────────────────────────────

class TestSlopesEqual:
    """Tests for _slopes_equal() slope comparison."""

    def test_identical_finite(self):
        assert vormap._slopes_equal(2.5, 2.5) is True

    def test_different_slopes(self):
        assert vormap._slopes_equal(1.0, 2.0) is False

    def test_both_infinite(self):
        assert vormap._slopes_equal(math.inf, math.inf) is True

    def test_one_infinite_one_finite(self):
        assert vormap._slopes_equal(math.inf, 1.0) is False

    def test_nearly_equal_within_tolerance(self):
        assert vormap._slopes_equal(1.0, 1.0 + 1e-7) is True

    def test_zero_slopes(self):
        assert vormap._slopes_equal(0.0, 0.0) is True

    def test_negative_infinite(self):
        assert vormap._slopes_equal(-math.inf, -math.inf) is True

    def test_negative_slopes(self):
        assert vormap._slopes_equal(-3.0, -3.0) is True


# ── eudist_sq ────────────────────────────────────────────────────

class TestEudistSq:
    """Tests for eudist_sq() squared Euclidean distance."""

    def test_same_point(self):
        assert vormap.eudist_sq(5, 5, 5, 5) == 0

    def test_unit_distance(self):
        assert vormap.eudist_sq(0, 0, 1, 0) == 1

    def test_345_triangle(self):
        assert vormap.eudist_sq(0, 0, 3, 4) == 25

    def test_negative_coordinates(self):
        assert vormap.eudist_sq(-1, -2, -4, -6) == 25


# ── eudist_pts ───────────────────────────────────────────────────

class TestEudistPts:
    """Tests for eudist_pts() Euclidean distance between point tuples."""

    def test_same_point(self):
        assert vormap.eudist_pts((3, 4), (3, 4)) == 0.0

    def test_known_distance(self):
        d = vormap.eudist_pts((0, 0), (3, 4))
        assert abs(d - 5.0) < 1e-9

    def test_negative_coordinates(self):
        d = vormap.eudist_pts((-1, -1), (2, 3))
        expected = math.hypot(3, 4)  # 5.0
        assert abs(d - expected) < 1e-9


# ── isect_B (boundary intersection) ─────────────────────────────

class TestIsectB:
    """Tests for isect_B() boundary intersection.

    Requires vormap.set_bounds() to be called first.
    """

    def setup_method(self):
        self.old_bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
        vormap.set_bounds(0, 100, 0, 100)

    def teardown_method(self):
        vormap.set_bounds(*self.old_bounds)

    def test_vertical_line(self):
        """Vertical line (dirn=inf) → north and south boundary."""
        B = vormap.isect_B(50, 50, math.inf)
        assert len(B) == 4
        assert B[0] == 50
        assert B[1] == 100  # north
        assert B[2] == 50
        assert B[3] == 0    # south

    def test_horizontal_line(self):
        """Horizontal line (dirn=0) → west and east boundary."""
        B = vormap.isect_B(50, 50, 0)
        assert len(B) == 4
        assert B[0] == 0    # west
        assert B[1] == 50
        assert B[2] == 100  # east
        assert B[3] == 50

    def test_diagonal_through_center(self):
        """Diagonal through near-center (slope=1)."""
        B = vormap.isect_B(50, 40, 1.0)
        assert len(B) == 4

    def test_steep_positive_slope(self):
        """Steep positive slope."""
        B = vormap.isect_B(50, 50, 5.0)
        assert len(B) == 4

    def test_result_always_4_elements(self):
        """Various slopes all return exactly 4 elements."""
        for slope in [0.5, -0.5, 2.0, -2.0, 0.1, -0.1]:
            B = vormap.isect_B(50, 50, slope)
            assert len(B) == 4, f"slope={slope} returned {len(B)} elements"


# ── find_CXY / find_BXY ─────────────────────────────────────────

class TestFindCXYBXY:
    """Tests for find_CXY() and find_BXY() boundary endpoint selection."""

    def setup_method(self):
        self.old_bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
        vormap.set_bounds(0, 100, 0, 100)

    def teardown_method(self):
        vormap.set_bounds(*self.old_bounds)

    def test_cxy_returns_point(self):
        """CXY returns a valid (x, y) tuple."""
        B = vormap.isect_B(50, 40, 1.0)
        cx, cy = vormap.find_CXY(B, 50, 40)
        assert isinstance(cx, (int, float))
        assert isinstance(cy, (int, float))

    def test_bxy_returns_point(self):
        """BXY returns a valid (x, y) tuple."""
        B = vormap.isect_B(50, 40, 1.0)
        bx, by = vormap.find_BXY(B, 50, 40)
        assert isinstance(bx, (int, float))
        assert isinstance(by, (int, float))

    def test_cxy_bxy_are_different(self):
        """CXY and BXY should return different boundary endpoints."""
        B = vormap.isect_B(50, 50, 0)  # horizontal line
        cx, cy = vormap.find_CXY(B, 60, 30)  # query point off-line
        bx, by = vormap.find_BXY(B, 60, 30)
        assert (cx, cy) != (bx, by)

    def test_endpoints_are_from_boundary(self):
        """Both CXY and BXY endpoints come from the B array."""
        B = vormap.isect_B(50, 50, 0)  # horizontal line
        cx, cy = vormap.find_CXY(B, 60, 30)
        bx, by = vormap.find_BXY(B, 60, 30)
        # Each result should be one of the two boundary points
        p1 = (B[0], B[1])
        p2 = (B[2], B[3])
        assert (cx, cy) in (p1, p2)
        assert (bx, by) in (p1, p2)


# ── polygon_area (additional tests) ─────────────────────────────

class TestPolygonAreaExtended:
    """Extended tests for polygon_area() shoelace formula."""

    def test_unit_square(self):
        lngs = [0, 1, 1, 0]
        lats = [0, 0, 1, 1]
        area = vormap.polygon_area(lngs, lats)
        assert abs(area - 1.0) < 0.01

    def test_triangle_345(self):
        lngs = [0, 4, 0]
        lats = [0, 0, 3]
        area = vormap.polygon_area(lngs, lats)
        assert abs(area - 6.0) < 0.01

    def test_degenerate_line(self):
        """Degenerate polygon (all points on a line) → area 0."""
        lngs = [0, 5, 10]
        lats = [0, 5, 10]
        area = vormap.polygon_area(lngs, lats)
        assert abs(area) < 0.01

    def test_irregular_polygon(self):
        """Irregular quadrilateral with known area."""
        # Rectangle 2×3 = area 6
        lngs = [0, 2, 2, 0]
        lats = [0, 0, 3, 3]
        area = vormap.polygon_area(lngs, lats)
        assert abs(area - 6.0) < 0.01


# ── Oracle ───────────────────────────────────────────────────────

class TestOracle:
    """Tests for the Oracle class (nearest-neighbor queries)."""

    def test_single_data_point(self):
        """With one data point, get_NN always returns it (if query differs)."""
        data = [(5, 5)]
        lng, lat = vormap.get_NN(data, 1, 1)
        assert (lng, lat) == (5, 5)

    def test_nearest_of_two(self):
        """Returns the closest of two data points."""
        data = [(0, 0), (100, 100)]
        lng, lat = vormap.get_NN(data, 1, 1)
        assert (lng, lat) == (0, 0)

    def test_negative_coordinates(self):
        """Works with negative coordinates."""
        data = [(-10, -10), (10, 10)]
        lng, lat = vormap.get_NN(data, -9, -9)
        assert (lng, lat) == (-10, -10)

    def test_oracle_calls_counter(self):
        """Oracle.calls counter increments on each get_NN call."""
        data = [(0, 0), (10, 10)]
        before = vormap.Oracle.calls
        vormap.get_NN(data, 5, 5)
        assert vormap.Oracle.calls == before + 1
