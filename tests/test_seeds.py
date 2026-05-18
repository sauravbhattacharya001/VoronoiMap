"""Tests for vormap_seeds — Seed point generators."""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_seeds import (
    random_uniform,
    grid,
    hexagonal,
    jittered_grid,
    poisson_disk,
    halton,
    save_seeds,
    load_seeds,
)


# ── random_uniform ──────────────────────────────────────────────────

class TestRandomUniform:
    def test_correct_count(self):
        pts = random_uniform(50, seed=42)
        assert len(pts) == 50

    def test_within_bounds(self):
        pts = random_uniform(100, 10, 20, 30, 40, seed=1)
        for x, y in pts:
            assert 10 <= x <= 20
            assert 30 <= y <= 40

    def test_reproducible(self):
        a = random_uniform(10, seed=99)
        b = random_uniform(10, seed=99)
        assert a == b

    def test_invalid_n(self):
        with pytest.raises(ValueError):
            random_uniform(0)

    def test_invalid_bounds(self):
        with pytest.raises(ValueError):
            random_uniform(10, 100, 0, 0, 100)  # x_min > x_max


# ── grid ────────────────────────────────────────────────────────────

class TestGrid:
    def test_correct_count(self):
        pts = grid(rows=5, cols=5)
        assert len(pts) == 25

    def test_single_row(self):
        pts = grid(rows=1, cols=4)
        assert len(pts) == 4
        ys = {y for _, y in pts}
        assert len(ys) == 1  # all same y

    def test_margin(self):
        pts = grid(0, 100, 0, 100, rows=3, cols=3, margin=10)
        for x, y in pts:
            assert 10 <= x <= 90
            assert 10 <= y <= 90

    def test_excessive_margin_raises(self):
        with pytest.raises(ValueError):
            grid(0, 10, 0, 10, rows=2, cols=2, margin=6)


# ── hexagonal ───────────────────────────────────────────────────────

class TestHexagonal:
    def test_produces_points(self):
        pts = hexagonal(0, 500, 0, 500, spacing=100)
        assert len(pts) > 0

    def test_within_bounds(self):
        pts = hexagonal(0, 500, 0, 500, spacing=50)
        for x, y in pts:
            assert 0 <= x <= 500
            assert 0 <= y <= 500

    def test_invalid_spacing(self):
        with pytest.raises(ValueError):
            hexagonal(spacing=0)


# ── jittered_grid ───────────────────────────────────────────────────

class TestJitteredGrid:
    def test_correct_count(self):
        pts = jittered_grid(rows=4, cols=4, seed=42)
        assert len(pts) == 16

    def test_reproducible(self):
        a = jittered_grid(rows=3, cols=3, seed=7)
        b = jittered_grid(rows=3, cols=3, seed=7)
        assert a == b

    def test_differs_from_grid(self):
        g = grid(rows=3, cols=3)
        j = jittered_grid(rows=3, cols=3, jitter=0.5, seed=42)
        assert g != j  # jitter should move points


# ── poisson_disk ────────────────────────────────────────────────────

class TestPoissonDisk:
    def test_produces_points(self):
        pts = poisson_disk(0, 200, 0, 200, min_dist=30, seed=42)
        assert len(pts) > 0

    def test_minimum_distance(self):
        pts = poisson_disk(0, 300, 0, 300, min_dist=40, seed=42)
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[i][0] - pts[j][0]
                dy = pts[i][1] - pts[j][1]
                dist = math.hypot(dx, dy)
                assert dist >= 40 - 1e-6

    def test_reproducible(self):
        a = poisson_disk(0, 200, 0, 200, min_dist=30, seed=5)
        b = poisson_disk(0, 200, 0, 200, min_dist=30, seed=5)
        assert a == b


# ── halton ──────────────────────────────────────────────────────────

class TestHalton:
    def test_correct_count(self):
        pts = halton(20, 0, 100, 0, 100)
        assert len(pts) == 20

    def test_within_bounds(self):
        pts = halton(50, 10, 90, 20, 80)
        for x, y in pts:
            assert 10 <= x <= 90
            assert 20 <= y <= 80

    def test_low_discrepancy(self):
        # Halton should be more uniform than random — check quadrant balance
        pts = halton(100, 0, 100, 0, 100)
        quads = [0, 0, 0, 0]
        for x, y in pts:
            q = (1 if x >= 50 else 0) + (2 if y >= 50 else 0)
            quads[q] += 1
        # Each quadrant should have roughly 25 points
        for c in quads:
            assert 15 <= c <= 35


# ── save_seeds / load_seeds ─────────────────────────────────────────

class TestSaveLoad:
    def test_roundtrip(self, tmp_path):
        pts = [(10.5, 20.3), (30.1, 40.7), (50.0, 60.0)]
        fpath = str(tmp_path / "seeds.txt")
        save_seeds(pts, fpath)
        loaded = load_seeds(fpath)
        assert len(loaded) == len(pts)
        for (x1, y1), (x2, y2) in zip(pts, loaded):
            assert abs(x1 - x2) < 0.01
            assert abs(y1 - y2) < 0.01
