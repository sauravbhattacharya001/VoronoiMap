"""Tests for vormap_stipple — Voronoi Stippling module."""

import json
import math
import os
import sys
import tempfile

import pytest

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(__file__))

from vormap_stipple import (
    _load_image_density,
    _sample_initial_points,
    _points_in_polygon,
    _weighted_lloyd_step,
    stipple_image,
    points_to_svg,
    points_to_json,
)

import numpy as np
from PIL import Image


@pytest.fixture
def black_image(tmp_path):
    """Create a 100x100 solid black image."""
    img = Image.new("L", (100, 100), 0)
    p = tmp_path / "black.png"
    img.save(str(p))
    return str(p)


@pytest.fixture
def gradient_image(tmp_path):
    """Create a 100x100 left-to-right gradient (black→white)."""
    arr = np.tile(np.linspace(0, 255, 100).astype(np.uint8), (100, 1))
    img = Image.fromarray(arr, "L")
    p = tmp_path / "gradient.png"
    img.save(str(p))
    return str(p)


@pytest.fixture
def white_image(tmp_path):
    """Create a 100x100 solid white image."""
    img = Image.new("L", (100, 100), 255)
    p = tmp_path / "white.png"
    img.save(str(p))
    return str(p)


class TestLoadImageDensity:
    def test_black_image_high_density(self, black_image):
        density, w, h = _load_image_density(black_image)
        assert density.shape == (100, 100)
        assert np.allclose(density, 1.0)

    def test_white_image_low_density(self, white_image):
        density, w, h = _load_image_density(white_image)
        assert np.allclose(density, 0.0)

    def test_resize_large_image(self, tmp_path):
        img = Image.new("L", (2000, 1000), 128)
        p = tmp_path / "big.png"
        img.save(str(p))
        density, w, h = _load_image_density(str(p), max_dim=400)
        assert max(w, h) <= 400


class TestSampleInitialPoints:
    def test_correct_count(self):
        density = np.ones((50, 50))
        pts = _sample_initial_points(density, 200)
        assert pts.shape == (200, 2)

    def test_within_bounds(self):
        density = np.ones((80, 120))
        pts = _sample_initial_points(density, 500)
        assert pts[:, 0].min() >= 0
        assert pts[:, 0].max() < 120
        assert pts[:, 1].min() >= 0
        assert pts[:, 1].max() < 80

    def test_density_biased(self):
        """Points should cluster in the dark (high density) region."""
        density = np.zeros((100, 100))
        density[:50, :] = 1.0  # top half is dark
        pts = _sample_initial_points(density, 1000)
        top_count = (pts[:, 1] < 50).sum()
        assert top_count > 800  # vast majority in top half


class TestPointsInPolygon:
    def test_square(self):
        poly = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=float)
        pts = np.array([[5, 5], [-1, 5], [5, -1], [11, 5]], dtype=float)
        inside = _points_in_polygon(pts, poly)
        assert inside[0] == True
        assert inside[1] == False
        assert inside[2] == False
        assert inside[3] == False


class TestStippleImage:
    def test_basic_run(self, black_image):
        result = stipple_image(black_image, n_points=100, iterations=3)
        assert result["points"].shape == (100, 2)
        assert result["width"] == 100
        assert result["height"] == 100
        assert result["iterations_run"] <= 3

    def test_invert(self, white_image):
        result = stipple_image(white_image, n_points=50, iterations=2, invert=True)
        assert result["points"].shape == (50, 2)

    def test_gradient_distribution(self, gradient_image):
        result = stipple_image(gradient_image, n_points=500, iterations=5)
        pts = result["points"]
        # Left side (dark) should have more points than right side (light)
        left = (pts[:, 0] < 50).sum()
        right = (pts[:, 0] >= 50).sum()
        assert left > right


class TestSVGOutput:
    def test_svg_structure(self):
        pts = np.array([[10, 20], [30, 40]], dtype=float)
        svg = points_to_svg(pts, 100, 100)
        assert "<svg" in svg
        assert "</svg>" in svg
        assert "circle" in svg
        assert svg.count("<circle") == 2

    def test_custom_colors(self):
        pts = np.array([[5, 5]], dtype=float)
        svg = points_to_svg(pts, 50, 50, bg_color="black", dot_color="white")
        assert 'fill="black"' in svg
        assert 'fill="white"' in svg


class TestJSONOutput:
    def test_json_valid(self):
        pts = np.array([[1.5, 2.5], [3.5, 4.5]], dtype=float)
        js = points_to_json(pts, 100, 80)
        data = json.loads(js)
        assert data["width"] == 100
        assert data["height"] == 80
        assert data["n_points"] == 2
        assert len(data["points"]) == 2

    def test_extra_metadata(self):
        pts = np.array([[0, 0]], dtype=float)
        js = points_to_json(pts, 10, 10, {"iterations_run": 5})
        data = json.loads(js)
        assert data["iterations_run"] == 5
