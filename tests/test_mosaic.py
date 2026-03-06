"""Tests for vormap_mosaic — Voronoi mosaic image filter."""

import json
import math
import os
import random
import struct
import sys
import tempfile
import zlib
from collections import Counter

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vormap_mosaic import (
    VoronoiMosaic, MosaicResult,
    _place_grid, _place_random, _place_poisson_disk, _place_edge_aware,
    _read_png, _write_png, _parse_hex_color, _luminance, main,
)


# ── Helpers ──

def _make_pixels(w, h, color=(128, 128, 128)):
    """Solid-colour pixel list."""
    return [color] * (w * h)


def _make_gradient_pixels(w, h):
    """Gradient pixel list (left-dark, right-bright)."""
    pixels = []
    for y in range(h):
        for x in range(w):
            v = int(255 * x / max(1, w - 1))
            pixels.append((v, v, v))
    return pixels


def _make_checkerboard_pixels(w, h, block=4):
    """Checkerboard pattern for testing edge detection."""
    pixels = []
    for y in range(h):
        for x in range(w):
            if ((x // block) + (y // block)) % 2 == 0:
                pixels.append((255, 255, 255))
            else:
                pixels.append((0, 0, 0))
    return pixels


def _create_test_png(filepath, w, h, pixels=None):
    """Write a minimal test PNG."""
    if pixels is None:
        pixels = _make_pixels(w, h)
    _write_png(filepath, w, h, pixels)


# ── PNG reader/writer ──

class TestPngIO:
    def test_roundtrip(self, tmp_path):
        path = str(tmp_path / "test.png")
        pixels = [(r, g, b) for r in range(0, 256, 16)
                  for g in range(0, 256, 64) for b in range(0, 256, 64)]
        w, h = 16, len(pixels) // 16
        pixels = pixels[:w * h]
        _write_png(path, w, h, pixels)
        rw, rh, rpx = _read_png(path)
        assert rw == w
        assert rh == h
        assert len(rpx) == len(pixels)
        assert rpx == pixels

    def test_single_pixel(self, tmp_path):
        path = str(tmp_path / "1x1.png")
        _write_png(path, 1, 1, [(42, 99, 200)])
        w, h, px = _read_png(path)
        assert (w, h) == (1, 1)
        assert px == [(42, 99, 200)]

    def test_solid_red(self, tmp_path):
        path = str(tmp_path / "red.png")
        pixels = [(255, 0, 0)] * 100
        _write_png(path, 10, 10, pixels)
        w, h, px = _read_png(path)
        assert all(p == (255, 0, 0) for p in px)

    def test_invalid_png(self, tmp_path):
        path = str(tmp_path / "bad.png")
        with open(path, "wb") as f:
            f.write(b"not a png")
        with pytest.raises(ValueError, match="Not a valid PNG"):
            _read_png(path)


# ── Hex color parsing ──

class TestParseHexColor:
    def test_basic(self):
        assert _parse_hex_color("FF0000") == (255, 0, 0)
        assert _parse_hex_color("00FF00") == (0, 255, 0)
        assert _parse_hex_color("0000FF") == (0, 0, 255)

    def test_with_hash(self):
        assert _parse_hex_color("#FFFFFF") == (255, 255, 255)
        assert _parse_hex_color("#000000") == (0, 0, 0)

    def test_lowercase(self):
        assert _parse_hex_color("ff8800") == (255, 136, 0)

    def test_invalid(self):
        with pytest.raises(ValueError):
            _parse_hex_color("FFF")


# ── Luminance ──

class TestLuminance:
    def test_white(self):
        assert abs(_luminance(255, 255, 255) - 255.0) < 0.1

    def test_black(self):
        assert _luminance(0, 0, 0) == 0.0

    def test_green_dominant(self):
        assert _luminance(0, 255, 0) > _luminance(255, 0, 0)
        assert _luminance(0, 255, 0) > _luminance(0, 0, 255)


# ── Seed placement ──

class TestPlacement:
    def test_grid_count(self):
        seeds = _place_grid(100, 100, 25)
        assert len(seeds) > 0
        assert len(seeds) <= 25 * 2  # grid can over/undershoot slightly

    def test_grid_bounds(self):
        seeds = _place_grid(100, 100, 50)
        for x, y in seeds:
            assert 0 <= x < 100
            assert 0 <= y < 100

    def test_grid_no_jitter(self):
        seeds = _place_grid(100, 100, 25, jitter=0.0)
        for x, y in seeds:
            assert 0 <= x < 100
            assert 0 <= y < 100

    def test_random_count(self):
        seeds = _place_random(100, 100, 50)
        assert len(seeds) == 50

    def test_random_bounds(self):
        seeds = _place_random(100, 100, 50)
        for x, y in seeds:
            assert 0 <= x < 100
            assert 0 <= y < 100

    def test_poisson_bounds(self):
        seeds = _place_poisson_disk(100, 100, 30)
        assert len(seeds) > 0
        for x, y in seeds:
            assert 0 <= x < 100
            assert 0 <= y < 100

    def test_poisson_min_spacing(self):
        seeds = _place_poisson_disk(200, 200, 20)
        if len(seeds) >= 2:
            min_dist = float("inf")
            for i in range(len(seeds)):
                for j in range(i + 1, len(seeds)):
                    dx = seeds[i][0] - seeds[j][0]
                    dy = seeds[i][1] - seeds[j][1]
                    d = math.sqrt(dx * dx + dy * dy)
                    min_dist = min(min_dist, d)
            # Poisson disk should maintain some minimum spacing
            assert min_dist > 1

    def test_edge_aware_count(self):
        pixels = _make_checkerboard_pixels(50, 50)
        seeds = _place_edge_aware(50, 50, 30, pixels)
        assert len(seeds) == 30

    def test_edge_aware_bounds(self):
        pixels = _make_gradient_pixels(50, 50)
        seeds = _place_edge_aware(50, 50, 20, pixels)
        for x, y in seeds:
            assert 0 <= x < 50
            assert 0 <= y < 50


# ── VoronoiMosaic constructor ──

class TestVoronoiMosaicInit:
    def test_valid(self):
        mosaic = VoronoiMosaic(10, 10, _make_pixels(10, 10))
        assert mosaic.width == 10
        assert mosaic.height == 10

    def test_invalid_dimensions(self):
        with pytest.raises(ValueError, match="positive"):
            VoronoiMosaic(0, 10, [])
        with pytest.raises(ValueError, match="positive"):
            VoronoiMosaic(10, -1, [])

    def test_pixel_count_mismatch(self):
        with pytest.raises(ValueError, match="Expected"):
            VoronoiMosaic(10, 10, [(0, 0, 0)] * 50)


# ── Seed generation ──

class TestGenerateSeeds:
    def test_grid(self):
        mosaic = VoronoiMosaic(100, 100, _make_pixels(100, 100))
        seeds = mosaic.generate_seeds(25, "grid")
        assert len(seeds) > 0

    def test_random(self):
        mosaic = VoronoiMosaic(100, 100, _make_pixels(100, 100))
        seeds = mosaic.generate_seeds(25, "random")
        assert len(seeds) == 25

    def test_poisson(self):
        mosaic = VoronoiMosaic(100, 100, _make_pixels(100, 100))
        seeds = mosaic.generate_seeds(25, "poisson")
        assert len(seeds) > 0

    def test_edge_aware(self):
        mosaic = VoronoiMosaic(50, 50, _make_checkerboard_pixels(50, 50))
        seeds = mosaic.generate_seeds(20, "edge_aware")
        assert len(seeds) == 20

    def test_invalid_placement(self):
        mosaic = VoronoiMosaic(10, 10, _make_pixels(10, 10))
        with pytest.raises(ValueError, match="Unknown placement"):
            mosaic.generate_seeds(5, "nonexistent")

    def test_zero_seeds(self):
        mosaic = VoronoiMosaic(10, 10, _make_pixels(10, 10))
        with pytest.raises(ValueError, match="positive"):
            mosaic.generate_seeds(0)

    def test_reproducible(self):
        mosaic = VoronoiMosaic(50, 50, _make_pixels(50, 50))
        s1 = mosaic.generate_seeds(10, "random", rng_seed=42)
        s2 = mosaic.generate_seeds(10, "random", rng_seed=42)
        assert s1 == s2


# ── Pixel assignment ──

class TestAssignPixels:
    def test_all_assigned(self):
        mosaic = VoronoiMosaic(20, 20, _make_pixels(20, 20))
        seeds = [(5, 5), (15, 15)]
        assignment = mosaic.assign_pixels(seeds)
        assert len(assignment) == 400
        assert all(0 <= idx < 2 for idx in assignment)

    def test_empty_seeds(self):
        mosaic = VoronoiMosaic(10, 10, _make_pixels(10, 10))
        with pytest.raises(ValueError, match="empty"):
            mosaic.assign_pixels([])

    def test_single_seed(self):
        mosaic = VoronoiMosaic(10, 10, _make_pixels(10, 10))
        assignment = mosaic.assign_pixels([(5, 5)])
        assert all(idx == 0 for idx in assignment)

    def test_nearest_assignment(self):
        mosaic = VoronoiMosaic(10, 1, _make_pixels(10, 1))
        seeds = [(0, 0), (9, 0)]
        assignment = mosaic.assign_pixels(seeds)
        # Pixel at x=0 should be seed 0, pixel at x=9 should be seed 1
        assert assignment[0] == 0
        assert assignment[9] == 1


# ── Region colours ──

class TestRegionColors:
    def test_mean_solid(self):
        pixels = [(100, 150, 200)] * 100
        mosaic = VoronoiMosaic(10, 10, pixels)
        assignment = [0] * 100
        colors = mosaic.compute_region_colors(assignment, 1, "mean")
        assert colors[0] == (100, 150, 200)

    def test_median(self):
        pixels = [(0, 0, 0)] * 5 + [(255, 255, 255)] * 5
        mosaic = VoronoiMosaic(10, 1, pixels)
        assignment = [0] * 10
        colors = mosaic.compute_region_colors(assignment, 1, "median")
        # Median of [0,0,0,0,0,255,255,255,255,255] = 255 (index 5)
        assert colors[0][0] in (0, 255)

    def test_dominant(self):
        pixels = [(128, 0, 0)] * 8 + [(0, 255, 0)] * 2
        mosaic = VoronoiMosaic(10, 1, pixels)
        assignment = [0] * 10
        colors = mosaic.compute_region_colors(assignment, 1, "dominant")
        # Red should dominate
        assert colors[0][0] > colors[0][1]

    def test_invalid_mode(self):
        mosaic = VoronoiMosaic(10, 10, _make_pixels(10, 10))
        with pytest.raises(ValueError, match="Unknown color_mode"):
            mosaic.compute_region_colors([0] * 100, 1, "invalid")

    def test_empty_region_fallback(self):
        mosaic = VoronoiMosaic(10, 1, _make_pixels(10, 1))
        assignment = [0] * 10  # All assigned to region 0, region 1 is empty
        colors = mosaic.compute_region_colors(assignment, 2, "mean")
        assert colors[1] == (128, 128, 128)  # Gray fallback


# ── Rendering ──

class TestRender:
    def test_no_edges(self):
        pixels = [(100, 100, 100)] * 100
        mosaic = VoronoiMosaic(10, 10, pixels)
        assignment = [0] * 100
        colors = [(200, 200, 200)]
        output = mosaic.render(assignment, colors, draw_edges=False)
        assert all(p == (200, 200, 200) for p in output)

    def test_edges_drawn(self):
        pixels = _make_pixels(10, 10)
        mosaic = VoronoiMosaic(10, 10, pixels)
        # Two regions: left half = 0, right half = 1
        assignment = [0 if (i % 10) < 5 else 1 for i in range(100)]
        colors = [(255, 0, 0), (0, 0, 255)]
        output = mosaic.render(assignment, colors, draw_edges=True,
                               edge_color=(0, 0, 0))
        # The border between columns 4 and 5 should have black pixels
        border_pixels = [output[y * 10 + 4] for y in range(10)]
        assert any(p == (0, 0, 0) for p in border_pixels)


# ── Full pipeline ──

class TestCreateMosaic:
    def test_basic(self):
        pixels = _make_gradient_pixels(20, 20)
        mosaic = VoronoiMosaic(20, 20, pixels)
        result = mosaic.create_mosaic(n_seeds=4, placement="grid", rng_seed=1)
        assert isinstance(result, MosaicResult)
        assert result.width == 20
        assert result.height == 20
        assert len(result.pixels) == 400
        assert result.n_regions == len(result.seeds)

    def test_with_edges(self):
        pixels = _make_pixels(20, 20, (200, 100, 50))
        mosaic = VoronoiMosaic(20, 20, pixels)
        result = mosaic.create_mosaic(n_seeds=4, draw_edges=True)
        assert len(result.pixels) == 400

    def test_all_placements(self):
        pixels = _make_checkerboard_pixels(30, 30)
        mosaic = VoronoiMosaic(30, 30, pixels)
        for p in ["grid", "random", "poisson", "edge_aware"]:
            result = mosaic.create_mosaic(n_seeds=5, placement=p, rng_seed=42)
            assert result.placement == p
            assert result.n_regions > 0

    def test_all_color_modes(self):
        pixels = _make_gradient_pixels(20, 20)
        mosaic = VoronoiMosaic(20, 20, pixels)
        for mode in ["mean", "median", "dominant"]:
            result = mosaic.create_mosaic(n_seeds=4, color_mode=mode, rng_seed=1)
            assert result.color_mode == mode

    def test_stats_reasonable(self):
        pixels = _make_pixels(50, 50)
        mosaic = VoronoiMosaic(50, 50, pixels)
        result = mosaic.create_mosaic(n_seeds=10, rng_seed=1)
        assert result.avg_region_size > 0
        assert result.min_region_size >= 0
        assert result.max_region_size >= result.min_region_size


# ── MosaicResult ──

class TestMosaicResult:
    def _make_result(self):
        pixels = _make_gradient_pixels(20, 20)
        mosaic = VoronoiMosaic(20, 20, pixels)
        return mosaic.create_mosaic(n_seeds=4, rng_seed=42)

    def test_summary(self):
        result = self._make_result()
        s = result.summary()
        assert "Voronoi Mosaic" in s
        assert "20×20" in s
        assert "Region size" in s

    def test_to_dict(self):
        result = self._make_result()
        d = result.to_dict()
        assert d["width"] == 20
        assert d["height"] == 20
        assert "n_regions" in d
        assert "placement" in d

    def test_region_stats(self):
        result = self._make_result()
        stats = result.region_stats()
        assert len(stats) == result.n_regions
        for i, s in stats.items():
            assert "seed" in s
            assert "color" in s
            assert "size" in s

    def test_save_png(self, tmp_path):
        result = self._make_result()
        path = str(tmp_path / "output.png")
        result.save_png(path)
        assert os.path.exists(path)
        # Verify it's a valid PNG
        w, h, px = _read_png(path)
        assert w == 20
        assert h == 20
        assert len(px) == 400


# ── CLI ──

class TestCLI:
    def test_basic(self, tmp_path):
        inp = str(tmp_path / "input.png")
        out = str(tmp_path / "output.png")
        _create_test_png(inp, 20, 20)
        main([inp, out, "--seeds", "4", "--seed", "1"])
        assert os.path.exists(out)

    def test_with_edges(self, tmp_path):
        inp = str(tmp_path / "input.png")
        out = str(tmp_path / "output.png")
        _create_test_png(inp, 20, 20)
        main([inp, out, "--seeds", "4", "--edges", "--edge-color", "FF0000"])
        assert os.path.exists(out)

    def test_with_stats(self, tmp_path, capsys):
        inp = str(tmp_path / "input.png")
        out = str(tmp_path / "output.png")
        _create_test_png(inp, 20, 20)
        main([inp, out, "--seeds", "4", "--stats", "--seed", "1"])
        captured = capsys.readouterr()
        assert "Region" in captured.out

    def test_all_placements_cli(self, tmp_path):
        inp = str(tmp_path / "input.png")
        _create_test_png(inp, 20, 20, _make_gradient_pixels(20, 20))
        for p in ["grid", "random", "poisson", "edge_aware"]:
            out = str(tmp_path / f"out_{p}.png")
            main([inp, out, "--seeds", "4", "--placement", p, "--seed", "1"])
            assert os.path.exists(out)

    def test_color_modes_cli(self, tmp_path):
        inp = str(tmp_path / "input.png")
        _create_test_png(inp, 20, 20, _make_gradient_pixels(20, 20))
        for mode in ["mean", "median", "dominant"]:
            out = str(tmp_path / f"out_{mode}.png")
            main([inp, out, "--seeds", "4", "--color-mode", mode, "--seed", "1"])
            assert os.path.exists(out)


# ── Edge cases ──

class TestEdgeCases:
    def test_tiny_image(self):
        mosaic = VoronoiMosaic(2, 2, [(0, 0, 0), (255, 0, 0),
                                       (0, 255, 0), (0, 0, 255)])
        result = mosaic.create_mosaic(n_seeds=2, rng_seed=1)
        assert result.n_regions == 2

    def test_single_seed_mosaic(self):
        pixels = _make_gradient_pixels(10, 10)
        mosaic = VoronoiMosaic(10, 10, pixels)
        result = mosaic.create_mosaic(n_seeds=1, placement="random", rng_seed=1)
        assert result.n_regions == 1
        # All pixels should be the same colour (average of entire image)
        assert len(set(result.pixels)) == 1

    def test_many_seeds(self):
        pixels = _make_gradient_pixels(20, 20)
        mosaic = VoronoiMosaic(20, 20, pixels)
        result = mosaic.create_mosaic(n_seeds=100, placement="random", rng_seed=1)
        assert result.n_regions == 100

    def test_wide_image(self):
        pixels = _make_pixels(100, 5)
        mosaic = VoronoiMosaic(100, 5, pixels)
        result = mosaic.create_mosaic(n_seeds=10, rng_seed=1)
        assert result.width == 100
        assert result.height == 5

    def test_tall_image(self):
        pixels = _make_pixels(5, 100)
        mosaic = VoronoiMosaic(5, 100, pixels)
        result = mosaic.create_mosaic(n_seeds=10, rng_seed=1)
        assert result.width == 5
        assert result.height == 100

    def test_reproducible_full_pipeline(self):
        pixels = _make_gradient_pixels(20, 20)
        mosaic = VoronoiMosaic(20, 20, pixels)
        r1 = mosaic.create_mosaic(n_seeds=5, rng_seed=99)
        r2 = mosaic.create_mosaic(n_seeds=5, rng_seed=99)
        assert r1.pixels == r2.pixels
        assert r1.seeds == r2.seeds
