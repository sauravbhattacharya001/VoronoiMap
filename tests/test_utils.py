"""Tests for vormap_utils — Shared utility functions."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_utils import (
    polygon_area,
    polygon_centroid,
    polygon_centroid_mean,
    euclidean,
    euclidean_xy,
    euclidean_coords,
    bounding_box,
    validate_points,
    compute_nn_distances,
)


# ── polygon_area ────────────────────────────────────────────────────

class TestPolygonArea:
    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert abs(polygon_area(sq) - 1.0) < 1e-10

    def test_triangle(self):
        tri = [(0, 0), (4, 0), (0, 3)]
        assert abs(polygon_area(tri) - 6.0) < 1e-10

    def test_empty(self):
        assert polygon_area([]) == 0.0


# ── polygon_centroid ────────────────────────────────────────────────

class TestPolygonCentroid:
    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        cx, cy = polygon_centroid(sq)
        assert abs(cx - 0.5) < 1e-10
        assert abs(cy - 0.5) < 1e-10

    def test_empty(self):
        assert polygon_centroid([]) == (0.0, 0.0)

    def test_single_point(self):
        cx, cy = polygon_centroid([(5, 7)])
        assert abs(cx - 5.0) < 1e-10
        assert abs(cy - 7.0) < 1e-10

    def test_two_points(self):
        cx, cy = polygon_centroid([(0, 0), (10, 10)])
        assert abs(cx - 5.0) < 1e-10
        assert abs(cy - 5.0) < 1e-10


class TestPolygonCentroidMean:
    def test_triangle(self):
        tri = [(0, 0), (6, 0), (3, 6)]
        cx, cy = polygon_centroid_mean(tri)
        assert abs(cx - 3.0) < 1e-10
        assert abs(cy - 2.0) < 1e-10


# ── euclidean distances ─────────────────────────────────────────────

class TestEuclidean:
    def test_zero_distance(self):
        assert euclidean((0, 0), (0, 0)) == 0.0

    def test_unit(self):
        assert abs(euclidean((0, 0), (3, 4)) - 5.0) < 1e-10

    def test_euclidean_xy(self):
        assert abs(euclidean_xy(0, 0, 3, 4) - 5.0) < 1e-10

    def test_euclidean_coords_alias(self):
        assert abs(euclidean_coords(1, 2, 4, 6) - 5.0) < 1e-10

    def test_symmetric(self):
        d1 = euclidean((1, 2), (5, 8))
        d2 = euclidean((5, 8), (1, 2))
        assert abs(d1 - d2) < 1e-10


# ── bounding_box ────────────────────────────────────────────────────

class TestBoundingBox:
    def test_basic(self):
        pts = [(1, 2), (5, 8), (3, 1)]
        assert bounding_box(pts) == (1, 1, 5, 8)

    def test_single_point(self):
        assert bounding_box([(3, 7)]) == (3, 7, 3, 7)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            bounding_box([])


# ── validate_points ─────────────────────────────────────────────────

class TestValidatePoints:
    def test_valid(self):
        result = validate_points([(1, 2), (3, 4)])
        assert result == [(1.0, 2.0), (3.0, 4.0)]

    def test_nan_raises(self):
        with pytest.raises(ValueError, match="NaN"):
            validate_points([(1, float("nan")), (3, 4)])

    def test_inf_raises(self):
        with pytest.raises(ValueError, match="Inf"):
            validate_points([(float("inf"), 0), (3, 4)])

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="2-element"):
            validate_points([(1, 2, 3), (4, 5)])

    def test_too_few_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            validate_points([(1, 2)])


# ── compute_nn_distances ────────────────────────────────────────────

class TestNNDistances:
    def test_simple(self):
        pts = [(0, 0), (3, 4), (6, 8)]
        dists = compute_nn_distances(pts)
        assert len(dists) == 3
        assert abs(dists[0] - 5.0) < 1e-6  # nearest to (0,0) is (3,4)

    def test_collinear(self):
        pts = [(0, 0), (1, 0), (3, 0)]
        dists = compute_nn_distances(pts)
        assert abs(dists[0] - 1.0) < 1e-6
        assert abs(dists[1] - 1.0) < 1e-6
        assert abs(dists[2] - 2.0) < 1e-6
