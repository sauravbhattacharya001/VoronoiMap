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


def assign_cells_grid(width, height, seeds):
    """Assign each pixel to its nearest seed (brute-force Voronoi).

    Returns a 2D grid (list of lists) where ``grid[y][x]`` is the index
    of the nearest seed in *seeds*.

    Parameters
    ----------
    width, height : int
        Grid dimensions.
    seeds : list of (float, float)
        Seed coordinates.

    Returns
    -------
    list of list of int
    """
    grid = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            best_d = float("inf")
            best_i = 0
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            grid[y][x] = best_i
    return grid


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


def point_to_segment_distance(px: float, py: float,
                              ax: float, ay: float,
                              bx: float, by: float) -> float:
    """Minimum distance from point (px, py) to line segment (ax, ay)-(bx, by).

    Computes the perpendicular distance when the projection falls on the
    segment, otherwise returns the distance to the nearest endpoint.

    Parameters
    ----------
    px, py : float
        Query point coordinates.
    ax, ay : float
        Segment start coordinates.
    bx, by : float
        Segment end coordinates.

    Returns
    -------
    float
        Minimum distance from the point to the segment.
    """
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def dist_to_polygon_boundary(px: float, py: float, vertices) -> float:
    """Minimum distance from a point to any edge of a polygon.

    Iterates over all edges of *vertices* (treated as a closed polygon)
    and returns the smallest point-to-segment distance.

    Parameters
    ----------
    px, py : float
        Query point coordinates.
    vertices : list of (float, float)
        Ordered polygon vertices.

    Returns
    -------
    float
        Minimum distance to the polygon boundary.  Returns ``inf`` if
        the polygon has fewer than 2 vertices.
    """
    n = len(vertices)
    if n < 2:
        if n == 1:
            return math.hypot(px - vertices[0][0], py - vertices[0][1])
        return float("inf")
    min_d = float("inf")
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        d = point_to_segment_distance(px, py, ax, ay, bx, by)
        if d < min_d:
            min_d = d
    return min_d


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


# ── Linear algebra helpers (pure Python, no numpy) ──────────────────
# Centralised here so that vormap_regress, vormap_trend, and other modules
# that need matrix operations can share a single, well-tested implementation
# instead of duplicating code.


def mat_transpose(m):
    """Transpose a list-of-lists matrix."""
    if not m:
        return []
    return [[m[r][c] for r in range(len(m))] for c in range(len(m[0]))]


def mat_mul(a, b):
    """Multiply two matrices (list-of-lists)."""
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for k in range(cols_a):
            aik = a[i][k]
            for j in range(cols_b):
                result[i][j] += aik * b[k][j]
    return result


def mat_vec(m, v):
    """Multiply matrix by column vector, return vector."""
    return [sum(m[i][j] * v[j] for j in range(len(v))) for i in range(len(m))]


def mat_identity(n):
    """Return n×n identity matrix."""
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def lu_decompose(matrix):
    """LU decomposition with partial pivoting.

    Returns (L, U, perm) where *perm* is the row-permutation vector.
    """
    n = len(matrix)
    U = [row[:] for row in matrix]
    L = mat_identity(n)
    perm = list(range(n))

    for col in range(n):
        max_val = abs(U[col][col])
        max_row = col
        for row in range(col + 1, n):
            if abs(U[row][col]) > max_val:
                max_val = abs(U[row][col])
                max_row = row
        if max_row != col:
            U[col], U[max_row] = U[max_row], U[col]
            perm[col], perm[max_row] = perm[max_row], perm[col]
            for k in range(col):
                L[col][k], L[max_row][k] = L[max_row][k], L[col][k]

        if abs(U[col][col]) < 1e-14:
            continue

        for row in range(col + 1, n):
            factor = U[row][col] / U[col][col]
            L[row][col] = factor
            for k in range(col, n):
                U[row][k] -= factor * U[col][k]

    return L, U, perm


def lu_solve(L, U, perm, b):
    """Solve Ax = b given LU decomposition."""
    n = len(b)
    pb = [b[perm[i]] for i in range(n)]

    # Forward substitution: Ly = pb
    y = [0.0] * n
    for i in range(n):
        y[i] = pb[i] - sum(L[i][j] * y[j] for j in range(i))

    # Back substitution: Ux = y
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(U[i][i]) < 1e-14:
            x[i] = 0.0
        else:
            x[i] = (y[i] - sum(U[i][j] * x[j] for j in range(i + 1, n))) / U[i][i]

    return x


def mat_solve(A, b):
    """Solve Ax = b via LU decomposition with partial pivoting."""
    L, U, perm = lu_decompose(A)
    return lu_solve(L, U, perm, b)


def mat_invert(A):
    """Invert a square matrix via LU decomposition."""
    n = len(A)
    L, U, perm = lu_decompose(A)
    inv = []
    for col in range(n):
        e = [1.0 if i == col else 0.0 for i in range(n)]
        inv.append(lu_solve(L, U, perm, e))
    return mat_transpose(inv)


# ── Sutherland-Hodgman polygon clipping ─────────────────────────────

def clip_polygon_to_rect(poly, xmin, ymin, xmax, ymax):
    """Clip a polygon to an axis-aligned rectangle (Sutherland-Hodgman).

    Parameters
    ----------
    poly : list of (float, float)
        Input polygon vertices.
    xmin, ymin, xmax, ymax : float
        Bounding rectangle.

    Returns
    -------
    list of (float, float)
        Clipped polygon vertices.  Empty list if fully outside.
    """
    def _clip_edge(pts, inside_fn, intersect_fn):
        if not pts:
            return []
        out = []
        prev = pts[-1]
        prev_in = inside_fn(prev)
        for curr in pts:
            curr_in = inside_fn(curr)
            if curr_in:
                if not prev_in:
                    out.append(intersect_fn(prev, curr))
                out.append(curr)
            elif prev_in:
                out.append(intersect_fn(prev, curr))
            prev = curr
            prev_in = curr_in
        return out

    def _lerp_x(a, b, x):
        dx = b[0] - a[0]
        if abs(dx) < 1e-12:
            return (x, a[1])
        t = (x - a[0]) / dx
        return (x, a[1] + t * (b[1] - a[1]))

    def _lerp_y(a, b, y):
        dy = b[1] - a[1]
        if abs(dy) < 1e-12:
            return (a[0], y)
        t = (y - a[1]) / dy
        return (a[0] + t * (b[0] - a[0]), y)

    poly = _clip_edge(poly, lambda p: p[0] >= xmin, lambda a, b: _lerp_x(a, b, xmin))
    poly = _clip_edge(poly, lambda p: p[0] <= xmax, lambda a, b: _lerp_x(a, b, xmax))
    poly = _clip_edge(poly, lambda p: p[1] >= ymin, lambda a, b: _lerp_y(a, b, ymin))
    poly = _clip_edge(poly, lambda p: p[1] <= ymax, lambda a, b: _lerp_y(a, b, ymax))
    return poly


# ── shared numeric helpers ──────────────────────────────────────────

def clamp(v, lo=0, hi=255):
    """Clamp *v* to the range [*lo*, *hi*]."""
    return max(lo, min(hi, v))


def lerp(a: float, b: float, t: float) -> float:
    """Linearly interpolate between *a* and *b* at fraction *t*."""
    return a + (b - a) * t


def lerp_color(
    c1: Tuple[int, int, int],
    c2: Tuple[int, int, int],
    t: float,
) -> Tuple[int, int, int]:
    """Linearly interpolate between two RGB colours.  *t* is clamped to [0, 1]."""
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )
