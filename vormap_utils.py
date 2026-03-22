"""Shared utility functions for VoronoiMap modules.

This module consolidates commonly duplicated helpers (distance calculations,
point-in-polygon tests, polygon centroids, etc.) so that individual modules
can import them instead of re-implementing them.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple, Union

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
Point = Tuple[float, float]
Polygon = Sequence[Point]


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------

def euclidean(x1: float, y1: float, x2: float, y2: float) -> float:
    """Euclidean distance between two points given as separate coordinates."""
    dx = x2 - x1
    dy = y2 - y1
    return math.sqrt(dx * dx + dy * dy)


def euclidean_pts(a: Point, b: Point) -> float:
    """Euclidean distance between two points given as (x, y) tuples."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def point_in_polygon(px: float, py: float, polygon: Polygon) -> bool:
    """Ray-casting point-in-polygon test.

    Parameters
    ----------
    px, py : float
        Test point coordinates.
    polygon : sequence of (float, float)
        Ordered polygon vertices.

    Returns
    -------
    bool
        True if (px, py) is inside the polygon.
    """
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def polygon_centroid(vertices: Polygon) -> Point:
    """Compute the centroid of a simple polygon using the shoelace-derived formula.

    Parameters
    ----------
    vertices : sequence of (float, float)
        Ordered polygon vertices (not necessarily closed).

    Returns
    -------
    (float, float)
        The (x, y) centroid. Falls back to arithmetic mean if area ≈ 0.
    """
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n <= 2:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)

    area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]
        area += cross
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross

    area *= 0.5
    if abs(area) < 1e-12:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)

    cx /= (6.0 * area)
    cy /= (6.0 * area)
    return (cx, cy)


def polygon_area_signed(vertices: Polygon) -> float:
    """Signed area of a simple polygon (positive = counter-clockwise)."""
    n = len(vertices)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return area / 2.0


def polygon_area_abs(vertices: Polygon) -> float:
    """Absolute area of a simple polygon."""
    return abs(polygon_area_signed(vertices))


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def mean(values: Sequence[float]) -> float:
    """Arithmetic mean. Returns 0.0 for empty sequences."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def std(values: Sequence[float], *, ddof: int = 0) -> float:
    """Standard deviation with optional degrees of freedom adjustment."""
    n = len(values)
    if n <= ddof:
        return 0.0
    m = mean(values)
    ss = sum((v - m) ** 2 for v in values)
    return math.sqrt(ss / (n - ddof))


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (0-100 scale)."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    k = (pct / 100.0) * (n - 1)
    lo = int(math.floor(k))
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


# ---------------------------------------------------------------------------
# Bounding-box helpers
# ---------------------------------------------------------------------------

def bounding_box(points: Sequence[Point]) -> Tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) for a collection of points."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))
