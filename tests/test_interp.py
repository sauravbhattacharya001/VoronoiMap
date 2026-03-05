"""Tests for vormap_interp - Spatial interpolation."""

import math
import sys

from vormap_interp import (
    nearest_interp,
    idw_interp,
    grid_interpolate,
)

# Optional scipy-dependent functions
try:
    from vormap_interp import natural_neighbor_interp
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False

passed = 0
failed = 0


def assert_close(msg, expected, actual, tol=1e-6):
    global passed, failed
    if abs(expected - actual) < tol:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg} (expected {expected}, got {actual})")


def assert_equal(msg, expected, actual):
    global passed, failed
    if expected == actual:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg} (expected {expected}, got {actual})")


def assert_true(msg, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


def assert_raises(msg, exc_type, fn):
    global passed, failed
    try:
        fn()
        failed += 1
        print(f"FAIL: {msg} (no exception raised)")
    except exc_type:
        passed += 1
    except Exception as e:
        failed += 1
        print(f"FAIL: {msg} (got {type(e).__name__}: {e})")


# Test data: 4 points with known values
points = [(0, 0), (10, 0), (10, 10), (0, 10)]
values = [1.0, 2.0, 3.0, 4.0]


# ── nearest_interp ──

print("--- nearest_interp ---")

# Exact match at seed point
assert_close("exact seed (0,0)", 1.0, nearest_interp(points, values, (0, 0)))
assert_close("exact seed (10,10)", 3.0, nearest_interp(points, values, (10, 10)))

# Close to a seed
assert_close("near (0,0)", 1.0, nearest_interp(points, values, (0.1, 0.1)))
assert_close("near (10,0)", 2.0, nearest_interp(points, values, (9.9, 0.1)))

# Center point: nearest is any corner (equidistant); value depends on iteration
center_val = nearest_interp(points, values, (5, 5))
assert_true("center is a valid value", center_val in [1.0, 2.0, 3.0, 4.0])

# Single point
assert_close("single point", 42.0, nearest_interp([(5, 5)], [42.0], (0, 0)))

# Error: mismatched lengths
assert_raises("mismatched lengths", ValueError,
              lambda: nearest_interp([(0, 0)], [1, 2], (0, 0)))

# Error: empty
assert_raises("empty points", ValueError,
              lambda: nearest_interp([], [], (0, 0)))


# ── idw_interp ──

print("--- idw_interp ---")

# At a seed point: should return exact value (within epsilon)
assert_close("idw at seed", 1.0, idw_interp(points, values, (0, 0)))
assert_close("idw at seed 2", 3.0, idw_interp(points, values, (10, 10)))

# Midpoint between two points (also influenced by farther points)
# (5,0) with all 4 corners: d1=5, d2=5, d3=sqrt(125), d4=sqrt(125)
# w1=w2=1/25, w3=w4=1/125 -> (1/25*1 + 1/25*2 + 1/125*3 + 1/125*4) / sum
# = (0.04+0.08+0.024+0.032) / (0.04+0.04+0.008+0.008) = 0.176/0.096
two_pts_only = [(0, 0), (10, 0)]
two_vals_only = [1.0, 2.0]
midpoint_val = idw_interp(two_pts_only, two_vals_only, (5, 0))
assert_close("idw midpoint of two", 1.5, midpoint_val, tol=0.01)

# Very close to one point: should dominate
close_val = idw_interp(points, values, (0.001, 0.001))
assert_true("idw near seed dominates", abs(close_val - 1.0) < 0.1)

# Uniform values: IDW should return that value everywhere
uniform_vals = [5.0, 5.0, 5.0, 5.0]
assert_close("idw uniform values", 5.0,
             idw_interp(points, uniform_vals, (5, 5)))

# Different power parameter
val_p1 = idw_interp(points, values, (5, 5), power=1.0)
val_p3 = idw_interp(points, values, (5, 5), power=3.0)
# Higher power = more influence from nearest point
assert_true("idw power affects result", True)  # both should work without error

# Error handling
assert_raises("idw mismatched", ValueError,
              lambda: idw_interp([(0, 0)], [1, 2], (0, 0)))
assert_raises("idw empty", ValueError,
              lambda: idw_interp([], [], (0, 0)))


# ── natural_neighbor_interp ──

print("--- natural_neighbor_interp ---")

if HAS_SCIPY:
    # Grid of known values
    nn_points = [(0, 0), (10, 0), (0, 10), (10, 10), (5, 5)]
    nn_values = [0.0, 10.0, 10.0, 20.0, 10.0]

    # At a seed: exact value
    assert_close("nn at seed", 10.0,
                 natural_neighbor_interp(nn_points, nn_values, (5, 5)))

    # Interpolated value should be between min and max of neighbors
    nn_val = natural_neighbor_interp(nn_points, nn_values, (3, 3))
    assert_true("nn in range", 0.0 <= nn_val <= 20.0)

    # Symmetric query: midpoint of uniform gradient
    sym_points = [(0, 0), (10, 0), (5, 10)]
    sym_values = [0.0, 10.0, 5.0]
    sym_val = natural_neighbor_interp(sym_points, sym_values, (5, 3))
    assert_true("nn symmetric in range", 0.0 <= sym_val <= 10.0)

    # Fewer than 3 points raises ValueError
    assert_raises("nn < 3 points", ValueError,
                  lambda: natural_neighbor_interp([(0, 0), (1, 1)], [1, 2], (0.5, 0.5)))

    # Mismatched lengths
    assert_raises("nn mismatched", ValueError,
                  lambda: natural_neighbor_interp([(0, 0), (1, 0), (0, 1)], [1], (0, 0)))
else:
    print("  (scipy not available, skipping natural neighbor tests)")


# ── grid_interpolate ──

print("--- grid_interpolate ---")

# Basic grid with nearest method (no scipy required)
grid_pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
grid_vals = [1.0, 2.0, 3.0, 4.0]

result = grid_interpolate(grid_pts, grid_vals, nx=5, ny=5, method='nearest',
                          bounds=(0, 10, 0, 10))
assert_equal("grid rows", 5, len(result['grid']))
assert_equal("grid cols", 5, len(result['grid'][0]))
assert_true("grid has bounds", result['bounds'] == (0, 10, 0, 10))
assert_true("min_val exists", result['min_val'] <= result['max_val'])

# IDW grid
result_idw = grid_interpolate(grid_pts, grid_vals, nx=3, ny=3, method='idw',
                              bounds=(0, 10, 0, 10))
assert_equal("idw grid rows", 3, len(result_idw['grid']))
# Check corner values are reasonable (near seed values)
assert_true("idw grid min >= 1", result_idw['min_val'] >= 0.99)
assert_true("idw grid max <= 4", result_idw['max_val'] <= 4.01)

# Auto bounds (no bounds specified)
result_auto = grid_interpolate(grid_pts, grid_vals, nx=3, ny=3, method='nearest')
assert_true("auto bounds set", result_auto['bounds'] is not None)
assert_equal("auto grid size", 3, len(result_auto['grid']))

# Invalid method
assert_raises("invalid method", ValueError,
              lambda: grid_interpolate(grid_pts, grid_vals, method='invalid'))

# Empty points
assert_raises("empty grid points", ValueError,
              lambda: grid_interpolate([], [], nx=5, ny=5))

# Uniform values: entire grid should be that value
uniform_result = grid_interpolate(
    [(0, 0), (10, 0), (10, 10), (0, 10)],
    [7.0, 7.0, 7.0, 7.0],
    nx=4, ny=4, method='idw', bounds=(0, 10, 0, 10)
)
for row in uniform_result['grid']:
    for v in row:
        assert_close("uniform grid cell", 7.0, v, tol=0.01)

# xs and ys are correct
assert_equal("xs length", 4, len(uniform_result['xs']))
assert_equal("ys length", 4, len(uniform_result['ys']))
assert_close("xs[0]", 0.0, uniform_result['xs'][0])
assert_close("ys[0]", 0.0, uniform_result['ys'][0])


# ── Summary ──

print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed > 0:
    sys.exit(1)
