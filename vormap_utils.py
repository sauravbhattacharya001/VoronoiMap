"""Shared utility functions for VoronoiMap modules.

Centralises commonly duplicated helpers (polygon area, bounding box,
point validation) so individual modules can import from one place.

``polygon_area`` and ``polygon_centroid`` are canonical in
:mod:`vormap_geometry`; they are re-exported here for backward
compatibility so existing ``from vormap_utils import …`` lines
continue to work.
"""

from typing import List, Tuple

# Re-export from the canonical location to eliminate duplicate code.
from vormap_geometry import polygon_area, polygon_centroid  # noqa: F401


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
    import math
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    return math.sqrt(dx * dx + dy * dy)


def bounding_box(points):
    """Return (x_min, y_min, x_max, y_max) for a list of (x, y) points."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


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
    import math
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
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
            raise ValueError(f"Point at index {i} has NaN or Inf coordinates: {pt}")
        validated.append((x, y))
    if len(validated) < 2:
        raise ValueError(f"Need at least 2 valid points, got {len(validated)}")
    return validated


def compute_nn_distances(points):
    """Compute nearest-neighbor distance for each point.

    Uses scipy KDTree when available (O(n log n)), otherwise brute
    force O(n^2).

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
        return [dists[i][1] for i in range(n)]
    except ImportError:
        pass

    # Brute force fallback
    import math
    nn_dists = []
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = math.hypot(points[i][0] - points[j][0],
                           points[i][1] - points[j][1])
            if d < best:
                best = d
        nn_dists.append(best)
    return nn_dists
