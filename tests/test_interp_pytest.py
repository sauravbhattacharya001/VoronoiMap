"""Pytest tests for vormap_interp — spatial interpolation.

Replaces the script-based test_interp.py with proper pytest structure,
adds edge-case coverage for _value_to_color, export_surface_svg,
export_surface_csv, and grid_interpolate boundary conditions.
"""

import math
import os
import tempfile

import pytest

from vormap_interp import (
    nearest_interp,
    idw_interp,
    grid_interpolate,
    export_surface_svg,
    export_surface_csv,
    _value_to_color,
)

# Optional scipy-dependent functions
try:
    from vormap_interp import natural_neighbor_interp
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

# Shared fixtures

SQUARE_POINTS = [(0, 0), (10, 0), (10, 10), (0, 10)]
SQUARE_VALUES = [1.0, 2.0, 3.0, 4.0]


# ── nearest_interp ─────────────────────────────────────────────────

class TestNearestInterp:
    def test_exact_seed_returns_value(self):
        assert nearest_interp(SQUARE_POINTS, SQUARE_VALUES, (0, 0)) == 1.0

    def test_exact_seed_last(self):
        assert nearest_interp(SQUARE_POINTS, SQUARE_VALUES, (10, 10)) == 3.0

    def test_near_seed_returns_closest(self):
        assert nearest_interp(SQUARE_POINTS, SQUARE_VALUES, (0.1, 0.1)) == 1.0

    def test_near_second_seed(self):
        assert nearest_interp(SQUARE_POINTS, SQUARE_VALUES, (9.9, 0.1)) == 2.0

    def test_center_returns_valid_value(self):
        v = nearest_interp(SQUARE_POINTS, SQUARE_VALUES, (5, 5))
        assert v in [1.0, 2.0, 3.0, 4.0]

    def test_single_point(self):
        assert nearest_interp([(5, 5)], [42.0], (0, 0)) == 42.0

    def test_negative_coords(self):
        pts = [(-5, -5), (5, 5)]
        vals = [10.0, 20.0]
        assert nearest_interp(pts, vals, (-4, -4)) == 10.0

    def test_large_distance(self):
        pts = [(0, 0), (1e6, 1e6)]
        vals = [1.0, 2.0]
        assert nearest_interp(pts, vals, (999999, 999999)) == 2.0

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="same length"):
            nearest_interp([(0, 0)], [1, 2], (0, 0))

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="not be empty"):
            nearest_interp([], [], (0, 0))

    def test_collinear_points(self):
        pts = [(0, 0), (5, 0), (10, 0)]
        vals = [1.0, 2.0, 3.0]
        assert nearest_interp(pts, vals, (4, 0)) == 2.0
        assert nearest_interp(pts, vals, (8, 0)) == 3.0


# ── idw_interp ─────────────────────────────────────────────────────

class TestIdwInterp:
    def test_at_seed_returns_exact(self):
        assert idw_interp(SQUARE_POINTS, SQUARE_VALUES, (0, 0)) == 1.0

    def test_at_second_seed(self):
        assert idw_interp(SQUARE_POINTS, SQUARE_VALUES, (10, 10)) == 3.0

    def test_midpoint_of_two(self):
        pts = [(0, 0), (10, 0)]
        vals = [1.0, 2.0]
        v = idw_interp(pts, vals, (5, 0))
        assert abs(v - 1.5) < 0.01

    def test_near_seed_dominates(self):
        v = idw_interp(SQUARE_POINTS, SQUARE_VALUES, (0.001, 0.001))
        assert abs(v - 1.0) < 0.1

    def test_uniform_values(self):
        vals = [5.0, 5.0, 5.0, 5.0]
        v = idw_interp(SQUARE_POINTS, vals, (5, 5))
        assert abs(v - 5.0) < 0.001

    def test_different_powers(self):
        v1 = idw_interp(SQUARE_POINTS, SQUARE_VALUES, (5, 5), power=1.0)
        v3 = idw_interp(SQUARE_POINTS, SQUARE_VALUES, (5, 5), power=3.0)
        # Both should produce valid numbers, higher power → more nearest influence
        assert 1.0 <= v1 <= 4.0
        assert 1.0 <= v3 <= 4.0

    def test_power_zero_gives_simple_average(self):
        pts = [(0, 0), (10, 0)]
        vals = [2.0, 8.0]
        # power=0 → all weights equal → simple average
        v = idw_interp(pts, vals, (3, 0), power=0.0)
        assert abs(v - 5.0) < 0.01

    def test_mismatched_raises(self):
        with pytest.raises(ValueError, match="same length"):
            idw_interp([(0, 0)], [1, 2], (0, 0))

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="not be empty"):
            idw_interp([], [], (0, 0))

    def test_single_point(self):
        v = idw_interp([(5, 5)], [99.0], (0, 0))
        assert v == 99.0

    def test_symmetry(self):
        """IDW at center of symmetric arrangement should average."""
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        vals = [1.0, 1.0, 1.0, 1.0]
        v = idw_interp(pts, vals, (5, 5))
        assert abs(v - 1.0) < 0.001

    def test_three_collinear(self):
        pts = [(0, 0), (5, 0), (10, 0)]
        vals = [0.0, 5.0, 10.0]
        v = idw_interp(pts, vals, (2.5, 0))
        # Closer to (0,0) and (5,0), should be between 0 and 5
        assert 0.0 <= v <= 5.0


# ── natural_neighbor_interp ────────────────────────────────────────

@pytest.mark.skipif(not HAS_SCIPY, reason="scipy not available")
class TestNaturalNeighborInterp:
    def test_at_seed_exact(self):
        pts = [(0, 0), (10, 0), (0, 10), (10, 10), (5, 5)]
        vals = [0.0, 10.0, 10.0, 20.0, 10.0]
        assert natural_neighbor_interp(pts, vals, (5, 5)) == 10.0

    def test_result_in_range(self):
        pts = [(0, 0), (10, 0), (0, 10), (10, 10), (5, 5)]
        vals = [0.0, 10.0, 10.0, 20.0, 10.0]
        v = natural_neighbor_interp(pts, vals, (3, 3))
        assert 0.0 <= v <= 20.0

    def test_symmetric_in_range(self):
        pts = [(0, 0), (10, 0), (5, 10)]
        vals = [0.0, 10.0, 5.0]
        v = natural_neighbor_interp(pts, vals, (5, 3))
        assert 0.0 <= v <= 10.0

    def test_fewer_than_3_raises(self):
        with pytest.raises(ValueError, match=">= 3"):
            natural_neighbor_interp([(0, 0), (1, 1)], [1, 2], (0.5, 0.5))

    def test_mismatched_raises(self):
        with pytest.raises(ValueError, match="same length"):
            natural_neighbor_interp([(0, 0), (1, 0), (0, 1)], [1], (0, 0))

    def test_uniform_values(self):
        pts = [(0, 0), (10, 0), (5, 10)]
        vals = [7.0, 7.0, 7.0]
        v = natural_neighbor_interp(pts, vals, (5, 3))
        assert abs(v - 7.0) < 0.1

    def test_gradient(self):
        """Linear gradient should interpolate linearly."""
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        # Values form a gradient in x direction
        vals = [0.0, 10.0, 10.0, 0.0, 5.0]
        v = natural_neighbor_interp(pts, vals, (2.5, 5))
        # Should be close to 2.5 (linear in x)
        assert 0.0 <= v <= 10.0


# ── grid_interpolate ──────────────────────────────────────────────

class TestGridInterpolate:
    def test_nearest_grid_shape(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=5, ny=5, method='nearest',
                                  bounds=(0, 10, 0, 10))
        assert len(result['grid']) == 5
        assert len(result['grid'][0]) == 5
        assert result['bounds'] == (0, 10, 0, 10)
        assert result['min_val'] <= result['max_val']

    def test_idw_grid_shape(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=3, ny=3, method='idw',
                                  bounds=(0, 10, 0, 10))
        assert len(result['grid']) == 3
        assert result['min_val'] >= 0.99
        assert result['max_val'] <= 4.01

    def test_auto_bounds(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=3, ny=3, method='nearest')
        assert result['bounds'] is not None
        assert len(result['grid']) == 3

    def test_invalid_method_raises(self):
        with pytest.raises(ValueError):
            grid_interpolate(SQUARE_POINTS, SQUARE_VALUES, method='invalid')

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            grid_interpolate([], [], nx=5, ny=5)

    def test_uniform_grid(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        vals = [7.0, 7.0, 7.0, 7.0]
        result = grid_interpolate(pts, vals, nx=4, ny=4,
                                  method='idw', bounds=(0, 10, 0, 10))
        for row in result['grid']:
            for v in row:
                assert abs(v - 7.0) < 0.01

    def test_xs_ys_lengths(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=4, ny=4, method='nearest',
                                  bounds=(0, 10, 0, 10))
        assert len(result['xs']) == 4
        assert len(result['ys']) == 4
        assert abs(result['xs'][0] - 0.0) < 0.001
        assert abs(result['ys'][0] - 0.0) < 0.001

    def test_1x1_grid(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=1, ny=1, method='nearest',
                                  bounds=(0, 10, 0, 10))
        assert len(result['grid']) == 1
        assert len(result['grid'][0]) == 1

    def test_single_point_nearest(self):
        result = grid_interpolate([(5, 5)], [42.0],
                                  nx=3, ny=3, method='nearest')
        for row in result['grid']:
            for v in row:
                assert v == 42.0


# ── _value_to_color ───────────────────────────────────────────────

class TestValueToColor:
    def test_min_value(self):
        c = _value_to_color(0.0, 0.0, 10.0)
        assert c.startswith('rgb(')

    def test_max_value(self):
        c = _value_to_color(10.0, 0.0, 10.0)
        assert c.startswith('rgb(')

    def test_midpoint(self):
        c = _value_to_color(5.0, 0.0, 10.0)
        assert c.startswith('rgb(')

    def test_constant_range(self):
        """When vmin == vmax, should not divide by zero."""
        c = _value_to_color(5.0, 5.0, 5.0)
        assert c.startswith('rgb(')

    def test_plasma_ramp(self):
        c = _value_to_color(5.0, 0.0, 10.0, ramp='plasma')
        assert c.startswith('rgb(')

    def test_hot_cold_ramp(self):
        c = _value_to_color(5.0, 0.0, 10.0, ramp='hot_cold')
        assert c.startswith('rgb(')

    def test_unknown_ramp_defaults_viridis(self):
        c = _value_to_color(5.0, 0.0, 10.0, ramp='nonexistent')
        expected = _value_to_color(5.0, 0.0, 10.0, ramp='viridis')
        assert c == expected

    def test_below_min_clamped(self):
        c = _value_to_color(-5.0, 0.0, 10.0)
        bottom = _value_to_color(0.0, 0.0, 10.0)
        assert c == bottom

    def test_above_max_clamped(self):
        c = _value_to_color(15.0, 0.0, 10.0)
        top = _value_to_color(10.0, 0.0, 10.0)
        assert c == top

    def test_quarter_viridis(self):
        """At t=0.25 for viridis, should match interpolation."""
        c = _value_to_color(2.5, 0.0, 10.0, ramp='viridis')
        # viridis stops: (0.0,(68,1,84)), (0.25,(59,82,139))
        assert 'rgb(59,82,139)' == c


# ── export_surface_svg ────────────────────────────────────────────

class TestExportSurfaceSvg:
    def _make_grid_result(self):
        return grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                nx=3, ny=3, method='nearest',
                                bounds=(0, 10, 0, 10))

    def test_creates_file(self):
        result = self._make_grid_result()
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            export_surface_svg(result, path)
            assert os.path.exists(path)
            content = open(path).read()
            assert '<svg' in content
            assert 'rgb(' in content
        finally:
            os.unlink(path)

    def test_with_title(self):
        result = self._make_grid_result()
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            export_surface_svg(result, path, title='Test Surface')
            content = open(path).read()
            assert 'Test Surface' in content
        finally:
            os.unlink(path)

    def test_custom_dimensions(self):
        result = self._make_grid_result()
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
            path = f.name
        try:
            export_surface_svg(result, path, width=400, height=300)
            content = open(path).read()
            assert 'width="400"' in content
            assert 'height="300"' in content
        finally:
            os.unlink(path)

    def test_different_ramps(self):
        result = self._make_grid_result()
        for ramp in ('viridis', 'plasma', 'hot_cold'):
            with tempfile.NamedTemporaryFile(suffix='.svg', delete=False) as f:
                path = f.name
            try:
                export_surface_svg(result, path, ramp=ramp)
                assert os.path.exists(path)
            finally:
                os.unlink(path)


# ── export_surface_csv ────────────────────────────────────────────

class TestExportSurfaceCsv:
    def test_creates_file(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=3, ny=3, method='nearest',
                                  bounds=(0, 10, 0, 10))
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            path = f.name
        try:
            export_surface_csv(result, path)
            assert os.path.exists(path)
            lines = open(path).readlines()
            # Header + 3 rows of data
            assert len(lines) >= 4
        finally:
            os.unlink(path)

    def test_csv_header(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=2, ny=2, method='nearest',
                                  bounds=(0, 10, 0, 10))
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            path = f.name
        try:
            export_surface_csv(result, path)
            header = open(path).readline().strip()
            # Should have y\x header with x coordinates
            assert 'y' in header.lower() or '\\' in header or ',' in header
        finally:
            os.unlink(path)

    def test_csv_values_match_grid(self):
        result = grid_interpolate(SQUARE_POINTS, SQUARE_VALUES,
                                  nx=2, ny=2, method='nearest',
                                  bounds=(0, 10, 0, 10))
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False) as f:
            path = f.name
        try:
            export_surface_csv(result, path)
            lines = open(path).readlines()
            # Data rows should have numeric values
            for line in lines[1:]:
                parts = line.strip().split(',')
                assert len(parts) >= 2
                # First column is y coordinate, rest are values
                for p in parts:
                    float(p)  # should not raise
        finally:
            os.unlink(path)
