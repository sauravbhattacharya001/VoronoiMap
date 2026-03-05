"""Tests for vormap_geometry - Shared geometry helpers."""

import math
import sys

from vormap_geometry import (
    polygon_area,
    polygon_perimeter,
    polygon_centroid,
    isoperimetric_quotient,
    edge_length,
    build_data_index,
)

passed = 0
failed = 0


def assert_close(msg, expected, actual, tol=1e-9):
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


# ── polygon_area ──

print("--- polygon_area ---")

# Unit square
square = [(0, 0), (1, 0), (1, 1), (0, 1)]
assert_close("unit square area", 1.0, polygon_area(square))

# Triangle (0,0)-(4,0)-(0,3) -> area = 6.0
triangle = [(0, 0), (4, 0), (0, 3)]
assert_close("triangle area", 6.0, polygon_area(triangle))

# Reversed winding should give same area (absolute value)
reversed_sq = list(reversed(square))
assert_close("reversed square area", 1.0, polygon_area(reversed_sq))

# Degenerate: fewer than 3 vertices
assert_close("empty polygon area", 0.0, polygon_area([]))
assert_close("single point area", 0.0, polygon_area([(1, 1)]))
assert_close("two points area", 0.0, polygon_area([(0, 0), (1, 1)]))

# Regular hexagon centered at origin with radius 1
# Area = 3*sqrt(3)/2 ≈ 2.598
hex_pts = [(math.cos(math.pi / 3 * i), math.sin(math.pi / 3 * i)) for i in range(6)]
expected_hex_area = 3 * math.sqrt(3) / 2
assert_close("hexagon area", expected_hex_area, polygon_area(hex_pts), tol=1e-6)

# Large rectangle
big_rect = [(0, 0), (1000, 0), (1000, 500), (0, 500)]
assert_close("large rectangle area", 500000.0, polygon_area(big_rect))

# Collinear points (degenerate polygon) -> area 0
collinear = [(0, 0), (1, 1), (2, 2)]
assert_close("collinear points area", 0.0, polygon_area(collinear))


# ── polygon_perimeter ──

print("--- polygon_perimeter ---")

# Unit square perimeter = 4
assert_close("unit square perimeter", 4.0, polygon_perimeter(square))

# Triangle: 4 + 3 + 5 = 12
assert_close("3-4-5 triangle perimeter", 12.0, polygon_perimeter(triangle))

# Degenerate cases
assert_close("empty perimeter", 0.0, polygon_perimeter([]))
assert_close("single point perimeter", 0.0, polygon_perimeter([(0, 0)]))

# Two points: distance there + back
two_pts = [(0, 0), (3, 4)]
assert_close("two points perimeter", 10.0, polygon_perimeter(two_pts))


# ── polygon_centroid ──

print("--- polygon_centroid ---")

# Unit square centroid = (0.5, 0.5)
cx, cy = polygon_centroid(square)
assert_close("square centroid x", 0.5, cx)
assert_close("square centroid y", 0.5, cy)

# Triangle centroid = (4/3, 1)
cx, cy = polygon_centroid(triangle)
assert_close("triangle centroid x", 4.0 / 3.0, cx, tol=1e-6)
assert_close("triangle centroid y", 1.0, cy, tol=1e-6)

# Empty
cx, cy = polygon_centroid([])
assert_close("empty centroid x", 0.0, cx)
assert_close("empty centroid y", 0.0, cy)

# Single point
cx, cy = polygon_centroid([(3, 7)])
assert_close("single point centroid x", 3.0, cx)
assert_close("single point centroid y", 7.0, cy)

# Two points: midpoint
cx, cy = polygon_centroid([(2, 4), (8, 10)])
assert_close("two points centroid x", 5.0, cx)
assert_close("two points centroid y", 7.0, cy)

# Collinear points: fallback to arithmetic mean
cx, cy = polygon_centroid([(0, 0), (1, 1), (2, 2)])
assert_close("collinear centroid x", 1.0, cx)
assert_close("collinear centroid y", 1.0, cy)

# Symmetric polygon: centroid at origin
sym = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
cx, cy = polygon_centroid(sym)
assert_close("symmetric centroid x", 0.0, cx, tol=1e-9)
assert_close("symmetric centroid y", 0.0, cy, tol=1e-9)


# ── isoperimetric_quotient ──

print("--- isoperimetric_quotient ---")

# Circle: IQ = 1.0
circle_area = math.pi * 5**2
circle_perim = 2 * math.pi * 5
assert_close("circle IQ", 1.0, isoperimetric_quotient(circle_area, circle_perim), tol=1e-9)

# Square: IQ = pi/4 ≈ 0.785
sq_iq = isoperimetric_quotient(1.0, 4.0)
assert_close("square IQ", math.pi / 4, sq_iq, tol=1e-6)

# Zero perimeter
assert_close("zero perimeter IQ", 0.0, isoperimetric_quotient(1.0, 0.0))

# Long thin rectangle (very non-circular)
thin_iq = isoperimetric_quotient(1.0, 202.0)  # 1x100 rectangle
assert_true("thin rectangle IQ < 0.01", thin_iq < 0.01)


# ── edge_length ──

print("--- edge_length ---")

assert_close("3-4-5 edge", 5.0, edge_length((0, 0), (3, 4)))
assert_close("horizontal edge", 7.0, edge_length((1, 2), (8, 2)))
assert_close("vertical edge", 3.0, edge_length((5, 1), (5, 4)))
assert_close("zero edge", 0.0, edge_length((1, 1), (1, 1)))
assert_close("diagonal unit", math.sqrt(2), edge_length((0, 0), (1, 1)))


# ── build_data_index ──

print("--- build_data_index ---")

data = [(1.0, 2.0), (3.0, 4.0), (5.0, 6.0)]
idx = build_data_index(data)
assert_equal("index size", 3, len(idx))
assert_equal("first point index", 0, idx[(1.0, 2.0)])
assert_equal("last point index", 2, idx[(5.0, 6.0)])

# Duplicates: first occurrence wins
dup_data = [(1, 1), (2, 2), (1, 1), (3, 3)]
dup_idx = build_data_index(dup_data)
assert_equal("dup index size", 3, len(dup_idx))
assert_equal("dup first wins", 0, dup_idx[(1, 1)])

# Empty
empty_idx = build_data_index([])
assert_equal("empty index", 0, len(empty_idx))


# ── Summary ──

print(f"\n=== Results: {passed} passed, {failed} failed ===")
if failed > 0:
    sys.exit(1)
