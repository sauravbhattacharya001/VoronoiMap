"""Shared utility functions for VoronoiMap modules.

Centralises commonly duplicated helpers (polygon area, bounding box,
point validation) so individual modules can import from one place.

``polygon_area`` and ``polygon_centroid`` are re-exported from
:mod:`vormap_geometry` (the canonical implementation) to avoid
maintaining two copies of the Shoelace formula.
"""

from typing import List, Tuple

# Re-export canonical geometry implementations to keep a single source
# of truth while preserving the public API of this module.
from vormap_geometry import polygon_area, polygon_centroid  # noqa: F401


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
