"""Tests for vormap_noise – Voronoi noise generation module.

Covers all noise types, distance metrics, octaves, tiling, edge cases,
CLI interface, and export functionality.
"""

import math
import os
import subprocess
import sys
import tempfile

import pytest

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(__file__))

import vormap_noise as vn


# ─── Basic generation ─────────────────────────────────────────────────

class TestGenerateNoise:
    """Core generate_noise() tests."""

    def test_returns_correct_dimensions(self):
        g = vn.generate_noise(32, 16, seed_count=5, seed=1)
        rows = g if not vn._HAS_NUMPY else g.tolist()
        assert len(rows) == 16
        assert len(rows[0]) == 32

    def test_values_normalised_0_1(self):
        g = vn.generate_noise(32, 32, seed_count=10, seed=2)
        flat = [v for row in vn.noise_to_nested_list(g) for v in row]
        assert min(flat) >= 0.0 - 1e-9
        assert max(flat) <= 1.0 + 1e-9
        # Should actually hit 0 and 1
        assert min(flat) < 0.01
        assert max(flat) > 0.99

    def test_deterministic_with_seed(self):
        a = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, seed=42))
        b = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, seed=42))
        assert a == b

    def test_different_seeds_differ(self):
        a = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, seed=1))
        b = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, seed=2))
        assert a != b

    def test_minimal_1x1(self):
        g = vn.generate_noise(1, 1, seed_count=1, seed=0)
        vals = vn.noise_to_nested_list(g)
        assert len(vals) == 1 and len(vals[0]) == 1

    def test_wide_rectangle(self):
        g = vn.generate_noise(64, 8, seed_count=4, seed=0)
        rows = vn.noise_to_nested_list(g)
        assert len(rows) == 8
        assert len(rows[0]) == 64

    def test_tall_rectangle(self):
        g = vn.generate_noise(8, 64, seed_count=4, seed=0)
        rows = vn.noise_to_nested_list(g)
        assert len(rows) == 64
        assert len(rows[0]) == 8


# ─── Noise types ──────────────────────────────────────────────────────

class TestNoiseTypes:
    """Test F1, F2, F2-F1 noise types."""

    def test_f1_noise(self):
        g = vn.generate_noise(16, 16, seed_count=5, noise_type='f1', seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_f2_noise(self):
        g = vn.generate_noise(16, 16, seed_count=5, noise_type='f2', seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_f2_f1_noise(self):
        g = vn.generate_noise(16, 16, seed_count=5, noise_type='f2-f1', seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_f1_f2_differ(self):
        f1 = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, noise_type='f1', seed=1))
        f2 = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, noise_type='f2', seed=1))
        assert f1 != f2

    def test_f2f1_differs_from_f1(self):
        f1 = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, noise_type='f1', seed=1))
        f2f1 = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, noise_type='f2-f1', seed=1))
        assert f1 != f2f1

    def test_invalid_noise_type_raises(self):
        with pytest.raises(ValueError, match="noise_type"):
            vn.generate_noise(8, 8, noise_type='f3', seed=0)


# ─── Metrics ──────────────────────────────────────────────────────────

class TestMetrics:
    """Test distance metrics."""

    def test_euclidean(self):
        g = vn.generate_noise(16, 16, metric='euclidean', seed_count=5, seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_manhattan(self):
        g = vn.generate_noise(16, 16, metric='manhattan', seed_count=5, seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_chebyshev(self):
        g = vn.generate_noise(16, 16, metric='chebyshev', seed_count=5, seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_different_metrics_differ(self):
        e = vn.noise_to_nested_list(vn.generate_noise(16, 16, metric='euclidean', seed_count=5, seed=1))
        m = vn.noise_to_nested_list(vn.generate_noise(16, 16, metric='manhattan', seed_count=5, seed=1))
        c = vn.noise_to_nested_list(vn.generate_noise(16, 16, metric='chebyshev', seed_count=5, seed=1))
        assert e != m
        assert e != c

    def test_invalid_metric_raises(self):
        with pytest.raises(ValueError, match="metric"):
            vn.generate_noise(8, 8, metric='minkowski', seed=0)

    def test_euclidean_fn(self):
        assert vn._euclidean(0, 0, 3, 4) == pytest.approx(5.0)

    def test_manhattan_fn(self):
        assert vn._manhattan(0, 0, 3, 4) == 7

    def test_chebyshev_fn(self):
        assert vn._chebyshev(0, 0, 3, 4) == 4


# ─── Octaves / fBm ───────────────────────────────────────────────────

class TestOctaves:
    """Test multi-octave fractal noise."""

    def test_single_octave(self):
        g = vn.generate_noise(16, 16, seed_count=5, octaves=1, seed=1)
        assert vn.noise_to_nested_list(g) is not None

    def test_multi_octave(self):
        g = vn.generate_noise(16, 16, seed_count=5, octaves=4, seed=1)
        flat = [v for row in vn.noise_to_nested_list(g) for v in row]
        assert 0.0 <= min(flat)
        assert max(flat) <= 1.0 + 1e-9

    def test_more_octaves_differ(self):
        o1 = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, octaves=1, seed=1))
        o4 = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, octaves=4, seed=1))
        assert o1 != o4

    def test_persistence_effect(self):
        a = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, octaves=3, persistence=0.3, seed=1))
        b = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, octaves=3, persistence=0.8, seed=1))
        assert a != b

    def test_lacunarity_effect(self):
        a = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, octaves=3, lacunarity=1.5, seed=1))
        b = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, octaves=3, lacunarity=3.0, seed=1))
        assert a != b

    def test_invalid_octaves_raises(self):
        with pytest.raises(ValueError):
            vn.generate_noise(8, 8, octaves=0, seed=0)


# ─── Tiling ───────────────────────────────────────────────────────────

class TestTiling:
    """Test seamless tileable noise."""

    def test_tileable_runs(self):
        g = vn.generate_noise(32, 32, seed_count=10, tileable=True, seed=7)
        assert vn.noise_to_nested_list(g) is not None

    def test_tileable_normalised(self):
        g = vn.generate_noise(32, 32, seed_count=10, tileable=True, seed=7)
        flat = [v for row in vn.noise_to_nested_list(g) for v in row]
        assert min(flat) >= -1e-9
        assert max(flat) <= 1.0 + 1e-9

    def test_tileable_differs_from_non(self):
        t = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, tileable=True, seed=1))
        n = vn.noise_to_nested_list(vn.generate_noise(16, 16, seed_count=5, tileable=False, seed=1))
        assert t != n

    def test_tileable_with_octaves(self):
        g = vn.generate_noise(32, 32, seed_count=10, tileable=True, octaves=3, seed=5)
        flat = [v for row in vn.noise_to_nested_list(g) for v in row]
        assert min(flat) >= -1e-9
        assert max(flat) <= 1.0 + 1e-9


# ─── Edge cases & validation ─────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and input validation."""

    def test_zero_width_raises(self):
        with pytest.raises(ValueError):
            vn.generate_noise(0, 16, seed=0)

    def test_zero_height_raises(self):
        with pytest.raises(ValueError):
            vn.generate_noise(16, 0, seed=0)

    def test_zero_seed_count_raises(self):
        with pytest.raises(ValueError):
            vn.generate_noise(16, 16, seed_count=0, seed=0)

    def test_single_seed_f2_fallback(self):
        # With only 1 seed, F2 should fall back gracefully
        g = vn.generate_noise(8, 8, seed_count=1, noise_type='f2', seed=0)
        assert vn.noise_to_nested_list(g) is not None

    def test_single_seed_f2f1_zero(self):
        # F2-F1 with 1 seed should be all zeros
        g = vn.noise_to_nested_list(vn.generate_noise(8, 8, seed_count=1, noise_type='f2-f1', seed=0))
        flat = [v for row in g for v in row]
        assert all(v == 0.0 for v in flat)

    def test_large_seed_count(self):
        g = vn.generate_noise(16, 16, seed_count=200, seed=0)
        assert vn.noise_to_nested_list(g) is not None


# ─── Conversion helper ───────────────────────────────────────────────

class TestConversion:
    """Test noise_to_nested_list."""

    def test_list_passthrough(self):
        g = [[0.1, 0.2], [0.3, 0.4]]
        result = vn.noise_to_nested_list(g)
        assert result == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.skipif(not vn._HAS_NUMPY, reason="numpy required")
    def test_ndarray_conversion(self):
        import numpy as np
        arr = np.array([[0.1, 0.2], [0.3, 0.4]])
        result = vn.noise_to_nested_list(arr)
        assert isinstance(result, list)
        assert isinstance(result[0], list)
        assert result[0][0] == pytest.approx(0.1)


# ─── Image export ────────────────────────────────────────────────────

class TestImageExport:
    """Test save_noise_image and save_noise_raw."""

    def test_save_png(self, tmp_path):
        g = vn.generate_noise(16, 16, seed_count=5, seed=0)
        path = str(tmp_path / 'test.png')
        vn.save_noise_image(g, path)
        assert os.path.isfile(path)
        assert os.path.getsize(path) > 0

    def test_save_png_with_cmap(self, tmp_path):
        g = vn.generate_noise(16, 16, seed_count=5, seed=0)
        path = str(tmp_path / 'hot.png')
        vn.save_noise_image(g, path, cmap='hot')
        assert os.path.isfile(path)

    def test_save_raw(self, tmp_path):
        g = vn.generate_noise(8, 8, seed_count=3, seed=0)
        path = str(tmp_path / 'raw.txt')
        vn.save_noise_raw(g, path)
        with open(path) as f:
            lines = f.read().strip().split('\n')
        assert len(lines) == 8
        assert len(lines[0].split()) == 8

    def test_save_raw_values_in_range(self, tmp_path):
        g = vn.generate_noise(8, 8, seed_count=3, seed=0)
        path = str(tmp_path / 'raw2.txt')
        vn.save_noise_raw(g, path)
        with open(path) as f:
            for line in f:
                for v in line.split():
                    fv = float(v)
                    assert 0.0 <= fv <= 1.0 + 1e-6


# ─── Internal helpers ────────────────────────────────────────────────

class TestInternals:
    """Test internal helper functions."""

    def test_generate_seeds_count(self):
        rng = __import__('random').Random(0)
        seeds = vn._generate_seeds(10, 100, 100, rng)
        assert len(seeds) == 10
        assert all(0 <= s[0] < 100 and 0 <= s[1] < 100 for s in seeds)

    def test_tile_seeds_multiplies(self):
        seeds = [(10, 20), (30, 40)]
        tiled = vn._tile_seeds(seeds, 100, 100)
        assert len(tiled) == 18  # 2 * 9

    def test_normalize_uniform_grid(self):
        grid = [[0.0, 0.0], [0.0, 0.0]]
        result = vn._normalize_grid(grid)
        flat = [v for row in result for v in row]
        assert all(v == 0.0 for v in flat)

    def test_normalize_range(self):
        grid = [[1.0, 5.0], [3.0, 9.0]]
        result = vn._normalize_grid(grid)
        flat = [v for row in result for v in row]
        assert min(flat) == pytest.approx(0.0)
        assert max(flat) == pytest.approx(1.0)


# ─── CLI ──────────────────────────────────────────────────────────────

class TestCLI:
    """Test command-line interface."""

    def test_cli_default(self, tmp_path):
        out = str(tmp_path / 'out.png')
        vn.main(['-W', '16', '-H', '16', '-n', '5', '-s', '1', '-o', out])
        assert os.path.isfile(out)

    def test_cli_f2f1(self, tmp_path):
        out = str(tmp_path / 'edge.png')
        vn.main(['-W', '16', '-H', '16', '-t', 'f2-f1', '-s', '1', '-o', out])
        assert os.path.isfile(out)

    def test_cli_manhattan(self, tmp_path):
        out = str(tmp_path / 'man.png')
        vn.main(['-W', '16', '-H', '16', '-m', 'manhattan', '-s', '1', '-o', out])
        assert os.path.isfile(out)

    def test_cli_octaves(self, tmp_path):
        out = str(tmp_path / 'oct.png')
        vn.main(['-W', '16', '-H', '16', '--octaves', '3', '-s', '1', '-o', out])
        assert os.path.isfile(out)

    def test_cli_tileable(self, tmp_path):
        out = str(tmp_path / 'tile.png')
        vn.main(['-W', '16', '-H', '16', '--tileable', '-s', '1', '-o', out])
        assert os.path.isfile(out)

    def test_cli_raw_output(self, tmp_path):
        out = str(tmp_path / 'img.png')
        raw = str(tmp_path / 'raw.txt')
        vn.main(['-W', '8', '-H', '8', '-s', '1', '-o', out, '--raw-output', raw])
        assert os.path.isfile(out)
        assert os.path.isfile(raw)

    def test_cli_cmap(self, tmp_path):
        out = str(tmp_path / 'viridis.png')
        vn.main(['-W', '16', '-H', '16', '--cmap', 'viridis', '-s', '1', '-o', out])
        assert os.path.isfile(out)

    def test_cli_subprocess(self, tmp_path):
        out = str(tmp_path / 'sub.png')
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), 'vormap_noise.py'),
             '-W', '16', '-H', '16', '-s', '1', '-o', out],
            capture_output=True, text=True
        )
        assert result.returncode == 0
        assert os.path.isfile(out)


# ─── Combinations ────────────────────────────────────────────────────

class TestCombinations:
    """Test various parameter combinations."""

    @pytest.mark.parametrize("noise_type", ['f1', 'f2', 'f2-f1'])
    @pytest.mark.parametrize("metric", ['euclidean', 'manhattan', 'chebyshev'])
    def test_all_type_metric_combos(self, noise_type, metric):
        g = vn.generate_noise(8, 8, seed_count=5, noise_type=noise_type,
                              metric=metric, seed=1)
        flat = [v for row in vn.noise_to_nested_list(g) for v in row]
        assert 0.0 <= min(flat)
        assert max(flat) <= 1.0 + 1e-9

    def test_tileable_multi_octave_manhattan(self):
        g = vn.generate_noise(16, 16, seed_count=8, noise_type='f2-f1',
                              metric='manhattan', octaves=3, tileable=True, seed=99)
        flat = [v for row in vn.noise_to_nested_list(g) for v in row]
        assert 0.0 <= min(flat)
        assert max(flat) <= 1.0 + 1e-9
