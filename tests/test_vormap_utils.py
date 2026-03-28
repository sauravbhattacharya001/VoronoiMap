"""Tests for vormap_utils — shared utility functions."""

import math
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import vormap_geometry first to break circular import
import vormap_geometry  # noqa: F401
import vormap_utils


class TestPolygonCentroidMean:
    """Tests for polygon_centroid_mean (arithmetic mean of vertices)."""

    def test_empty(self):
        assert vormap_utils.polygon_centroid_mean([]) == (0.0, 0.0)

    def test_single_point(self):
        assert vormap_utils.polygon_centroid_mean([(3.0, 4.0)]) == (3.0, 4.0)

    def test_square(self):
        verts = [(0, 0), (2, 0), (2, 2), (0, 2)]
        cx, cy = vormap_utils.polygon_centroid_mean(verts)
        assert cx == pytest.approx(1.0)
        assert cy == pytest.approx(1.0)

    def test_triangle(self):
        verts = [(0, 0), (6, 0), (3, 6)]
        cx, cy = vormap_utils.polygon_centroid_mean(verts)
        assert cx == pytest.approx(3.0)
        assert cy == pytest.approx(2.0)

    def test_negative_coords(self):
        verts = [(-4, -2), (4, -2), (4, 2), (-4, 2)]
        cx, cy = vormap_utils.polygon_centroid_mean(verts)
        assert cx == pytest.approx(0.0)
        assert cy == pytest.approx(0.0)


class TestEuclidean:
    """Tests for euclidean distance."""

    def test_same_point(self):
        assert vormap_utils.euclidean((0, 0), (0, 0)) == 0.0

    def test_unit_distance(self):
        assert vormap_utils.euclidean((0, 0), (1, 0)) == pytest.approx(1.0)

    def test_diagonal(self):
        assert vormap_utils.euclidean((0, 0), (3, 4)) == pytest.approx(5.0)

    def test_negative_coords(self):
        assert vormap_utils.euclidean((-1, -1), (2, 3)) == pytest.approx(5.0)

    def test_symmetry(self):
        d1 = vormap_utils.euclidean((1, 2), (4, 6))
        d2 = vormap_utils.euclidean((4, 6), (1, 2))
        assert d1 == pytest.approx(d2)


class TestBoundingBox:
    """Tests for bounding_box."""

    def test_simple(self):
        pts = [(1, 2), (3, 4), (0, 5)]
        assert vormap_utils.bounding_box(pts) == (0, 2, 3, 5)

    def test_single_point(self):
        assert vormap_utils.bounding_box([(7, 3)]) == (7, 3, 7, 3)

    def test_negative(self):
        pts = [(-5, -3), (2, 1)]
        assert vormap_utils.bounding_box(pts) == (-5, -3, 2, 1)


class TestValidatePoints:
    """Tests for validate_points."""

    def test_valid_tuples(self):
        result = vormap_utils.validate_points([(1, 2), (3, 4)])
        assert result == [(1.0, 2.0), (3.0, 4.0)]

    def test_valid_lists(self):
        result = vormap_utils.validate_points([[1, 2], [3, 4]])
        assert result == [(1.0, 2.0), (3.0, 4.0)]

    def test_string_numbers(self):
        """Numeric strings should be coerced to float."""
        result = vormap_utils.validate_points([("1.5", "2.5"), ("3", "4")])
        assert result == [(1.5, 2.5), (3.0, 4.0)]

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 2"):
            vormap_utils.validate_points([(1, 2)])

    def test_wrong_length(self):
        with pytest.raises(ValueError, match="2-element"):
            vormap_utils.validate_points([(1, 2, 3), (4, 5)])

    def test_non_numeric(self):
        with pytest.raises(ValueError, match="non-numeric"):
            vormap_utils.validate_points([("a", "b"), (1, 2)])

    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="NaN"):
            vormap_utils.validate_points([(float('nan'), 1), (2, 3)])

    def test_inf_rejected(self):
        with pytest.raises(ValueError, match="Inf"):
            vormap_utils.validate_points([(float('inf'), 1), (2, 3)])

    def test_non_sequence(self):
        with pytest.raises(ValueError, match="2-element"):
            vormap_utils.validate_points([42, (1, 2)])


class TestComputeNNDistances:
    """Tests for compute_nn_distances."""

    def test_two_points(self):
        pts = [(0, 0), (3, 4)]
        dists = vormap_utils.compute_nn_distances(pts)
        assert len(dists) == 2
        assert dists[0] == pytest.approx(5.0)
        assert dists[1] == pytest.approx(5.0)

    def test_collinear_points(self):
        pts = [(0, 0), (1, 0), (3, 0)]
        dists = vormap_utils.compute_nn_distances(pts)
        assert dists[0] == pytest.approx(1.0)  # nearest to (1,0)
        assert dists[1] == pytest.approx(1.0)  # nearest to (0,0)
        assert dists[2] == pytest.approx(2.0)  # nearest to (1,0)

    def test_square_grid(self):
        pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
        dists = vormap_utils.compute_nn_distances(pts)
        for d in dists:
            assert d == pytest.approx(1.0)
