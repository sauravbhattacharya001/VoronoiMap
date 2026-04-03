"""Shared utility functions for VoronoiMap modules.

Centralises commonly duplicated helpers (polygon area, bounding box,
point validation) so individual modules can import from one place.

``polygon_area`` and ``polygon_centroid`` are canonical in
:mod:`vormap_geometry`; they are re-exported here for backward
compatibility so existing ``from vormap_utils import …`` lines
continue to work.
"""

import math
from typing import List, Tuple

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# polygon_area is defined in vormap_geometry with full precision.
# Re-export it here for backward compatibility.
# NOTE: Lazy import to avoid circular dependency (vormap_geometry imports
# from vormap_utils).
def polygon_area(vertices):
    """Re-export of ``vormap_geometry.polygon_area`` — see that module for docs."""
    from vormap_geometry import polygon_area as _pa
    return _pa(vertices)


def polygon_centroid(vertices):
    """Area-weighted centroid of a polygon using the shoelace approach.

    Parameters
    ----------
    vertices : list of (x, y)
        Ordered polygon vertices.

    Returns
    -------
    tuple of (float, float)
        Centroid coordinates. Returns (0, 0) for degenerate input.
    """
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n <= 2:
        return polygon_centroid_mean(vertices)

    # For large polygons (> 64 vertices), numpy vectorization wins.
    # For the typical Voronoi polygon (5-20 vertices), array construction
    # overhead makes numpy slower than a plain Python loop.
    if _HAS_NUMPY and n > 64:
        pts = np.asarray(vertices)
        x = pts[:, 0]
        y = pts[:, 1]
        x_next = np.roll(x, -1)
        y_next = np.roll(y, -1)
        cross = x * y_next - x_next * y
        signed_area = cross.sum() * 0.5
        if abs(signed_area) < 1e-12:
            return polygon_centroid_mean(vertices)
        cx = ((x + x_next) * cross).sum() / (6.0 * signed_area)
        cy = ((y + y_next) * cross).sum() / (6.0 * signed_area)
        return (float(cx), float(cy))

    cx = 0.0
    cy = 0.0
    signed_area = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross
        signed_area += cross
    signed_area *= 0.5
    if abs(signed_area) < 1e-12:
        return polygon_centroid_mean(vertices)
    cx /= (6.0 * signed_area)
    cy /= (6.0 * signed_area)
    return (cx, cy)


def polygon_centroid_mean(vertices):
    """Centroid of a polygon as the simple geometric mean of vertices.

    Unlike :func:`polygon_centroid` (which uses the shoelace-derived
    area-weighted formula), this returns the arithmetic mean of the
    vertex coordinates.  Useful when a quick approximate centroid is
    sufficient or when the polygon may be degenerate.

    Parameters
    ----------
    vertices : list of (x, y)

    Returns
    -------
    tuple of (float, float)
    """
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    cx = sum(v[0] for v in vertices) / n
    cy = sum(v[1] for v in vertices) / n
    return (cx, cy)


def euclidean_coords(x1, y1, x2, y2):
    """Euclidean distance between two 2D points given as separate coordinates.

    .. deprecated::
        Use :func:`euclidean_xy` instead.  ``euclidean_coords`` is kept as a
        thin alias for backward compatibility.

    Parameters
    ----------
    x1, y1 : float
        First point coordinates.
    x2, y2 : float
        Second point coordinates.

    Returns
    -------
    float
        Euclidean distance.
    """
    return euclidean_xy(x1, y1, x2, y2)


def euclidean(p1, p2):
    """Euclidean distance between two 2D points.

    Parameters
    ----------
    p1, p2 : tuple of (float, float)
        2D point coordinates.

    Returns
    -------
    float
        Euclidean distance.
    """
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def euclidean_xy(x1: float, y1: float, x2: float, y2: float) -> float:
    """Euclidean distance between two 2D points given as scalar coordinates.

    This is the scalar-argument variant of :func:`euclidean`, provided to
    eliminate duplicated private ``_euclidean`` helpers across modules.

    Parameters
    ----------
    x1, y1 : float
        First point coordinates.
    x2, y2 : float
        Second point coordinates.

    Returns
    -------
    float
        Euclidean distance.
    """
    return math.hypot(x2 - x1, y2 - y1)


def bounding_box(points):
    """Return (x_min, y_min, x_max, y_max) for a list of (x, y) points.

    Uses a single pass over the points instead of building intermediate
    lists, which halves memory usage for large point sets.
    """
    it = iter(points)
    try:
        first = next(it)
    except StopIteration:
        raise ValueError("bounding_box requires at least one point")
    x_min = x_max = first[0]
    y_min = y_max = first[1]
    for p in it:
        px, py = p[0], p[1]
        if px < x_min:
            x_min = px
        elif px > x_max:
            x_max = px
        if py < y_min:
            y_min = py
        elif py > y_max:
            y_max = py
    return x_min, y_min, x_max, y_max


def validate_points(points: list) -> List[Tuple[float, float]]:
    """Validate and normalize a list of (x, y) points.

    Accepts lists/tuples of two numeric values.  Rejects NaN, Inf,
    non-numeric, and wrong-length entries.

    Returns
    -------
    list of (float, float)
        Validated point list.

    Raises
    ------
    ValueError
        If any point is invalid or fewer than 2 points remain.
    """
    _isnan = math.isnan
    _isinf = math.isinf
    validated = []
    for i, pt in enumerate(points):
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError(
                f"Point at index {i} must be a 2-element sequence, got {type(pt).__name__}"
            )
        try:
            x, y = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            raise ValueError(f"Point at index {i} has non-numeric coordinates: {pt}")
        if _isnan(x) or _isnan(y) or _isinf(x) or _isinf(y):
            raise ValueError(f"Point at index {i} has NaN or Inf coordinates: {pt}")
        validated.append((x, y))
    if len(validated) < 2:
        raise ValueError(f"Need at least 2 valid points, got {len(validated)}")
    return validated


def compute_nn_distances(points):
    """Compute nearest-neighbor distance for each point.

    Uses scipy KDTree when available (O(n log n)), otherwise brute
    force O(n²).

    Parameters
    ----------
    points : list of (x, y)

    Returns
    -------
    list of float
        Nearest-neighbor distance for each point.
    """
    n = len(points)
    try:
        from scipy.spatial import KDTree
        tree = KDTree(points)
        dists, _ = tree.query(points, k=2)  # k=2: self + nearest
        # Return column slice directly — avoids O(n) Python list
        # comprehension; dists[:, 1] is a numpy array that behaves
        # like a list for all downstream uses.
        return dists[:, 1].tolist()
    except ImportError:
        pass

    # Brute force fallback — use squared distances to avoid sqrt in the
    # inner loop (sqrt is monotonic so ordering is preserved), then take
    # a single sqrt per point for the final result.
    nn_dists = []
    for i in range(n):
        xi, yi = points[i]
        best_sq = float("inf")
        for j in range(n):
            if i == j:
                continue
            dx = xi - points[j][0]
            dy = yi - points[j][1]
            dsq = dx * dx + dy * dy
            if dsq < best_sq:
                best_sq = dsq
        nn_dists.append(math.sqrt(best_sq))
    return nn_dists


def point_in_polygon(px: float, py: float, vertices) -> bool:
    """Ray-casting point-in-polygon test.

    Parameters
    ----------
    px, py : float
        Test point coordinates.
    vertices : list[tuple[float, float]]
        Ordered polygon vertices (open or closed).

    Returns
    -------
    bool
        ``True`` if the point lies inside the polygon.
    """
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        yi, yj = vertices[i][1], vertices[j][1]
        xi, xj = vertices[i][0], vertices[j][0]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi + 1e-30) + xi
        ):
            inside = not inside
        j = i
    return inside
