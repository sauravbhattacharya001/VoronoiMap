
import random
import sys
import math
import warnings

try:
    import numpy as np
    from scipy.spatial import KDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# Default search space boundaries — overridden by auto-detection in
# load_data() or explicitly via set_bounds() / CLI --bounds.
IND_S = 0.0
IND_N = 1000.0
IND_W = 0.0
IND_E = 2000.0
BIN_PREC = 1e-6


def compute_bounds(points, padding=0.1):
    """Compute search space boundaries from a set of points.

    Returns (south, north, west, east) with *padding* fraction added
    on each side so Voronoi cells near the boundary are not clipped.
    """
    lngs, lats = zip(*points)
    w, e = min(lngs), max(lngs)
    s, n = min(lats), max(lats)
    pad_x = max((e - w) * padding, 1.0)  # at least 1 unit padding
    pad_y = max((n - s) * padding, 1.0)
    return s - pad_y, n + pad_y, w - pad_x, e + pad_x


def set_bounds(south, north, west, east):
    """Manually set the search space boundaries (globals)."""
    global IND_S, IND_N, IND_W, IND_E
    IND_S, IND_N, IND_W, IND_E = south, north, west, east

# Maximum vertices a single Voronoi region can have before we give up.
# Prevents infinite loops on degenerate point configurations.
MAX_VERTICES = 50


class Oracle:
    __slots__ = ()
    calls = 0


# Cache loaded data files so each file is read from disk exactly once.
# When scipy is available, a KDTree is stored alongside the point list.
_data_cache = {}

# Maps id(data_list) → KDTree for O(1) lookup in get_NN.
# This replaces the old O(n) identity scan over _data_cache.
_kdtree_by_id = {}


def load_data(filename, auto_bounds=True):
    """Load point data from a file and cache it in memory.

    Returns a list of (lng, lat) tuples.  Subsequent calls with the same
    filename return the cached list without re-reading the file.

    When *auto_bounds* is True (the default) the search space boundaries
    are automatically computed from the data extents with 10 % padding.
    This fixes issue #11 — datasets outside the default 1000×2000 region
    no longer produce silently incorrect results.

    When *scipy* is available a ``KDTree`` is also built and cached so
    that ``get_NN`` can use O(log n) lookups instead of O(n) scans.

    Raises ``ValueError`` if *filename* contains path traversal sequences
    (e.g. ``..``, absolute paths) that would escape the ``data/`` directory.
    """
    global IND_S, IND_N, IND_W, IND_E

    if filename in _data_cache:
        return _data_cache[filename]

    # --- Path traversal protection ---
    # Reject filenames that attempt to escape the data/ directory.
    # This prevents reading arbitrary files on the filesystem via crafted
    # filenames like "../../etc/passwd" or "/etc/shadow".
    import os
    if os.path.isabs(filename):
        raise ValueError(
            "Absolute paths are not allowed: '%s'" % filename
        )
    # Normalise and check that the resolved path stays inside data/
    data_dir = os.path.abspath("data")
    resolved = os.path.abspath(os.path.join("data", filename))
    if not resolved.startswith(data_dir + os.sep) and resolved != data_dir:
        raise ValueError(
            "Path traversal detected — filename '%s' resolves outside "
            "the data/ directory" % filename
        )

    points = []
    with open(resolved, "r") as objf:
        for line in objf:
            if not line.strip():
                continue
            coord = line.split()
            if len(coord) < 2:
                continue
            try:
                lng_val = float(coord[0])
                lat_val = float(coord[1])
            except (ValueError, OverflowError):
                continue  # skip malformed lines instead of crashing
            if not (math.isfinite(lng_val) and math.isfinite(lat_val)):
                continue  # reject NaN/Inf coordinates
            points.append((lng_val, lat_val))

    if not points:
        raise ValueError("No valid points found in '%s'" % filename)

    # Warn if any points fall outside the current search bounds
    out_of_bounds = [
        (lng, lat) for lng, lat in points
        if lng < IND_W or lng > IND_E or lat < IND_S or lat > IND_N
    ]
    if out_of_bounds:
        warnings.warn(
            "%d of %d points in '%s' fall outside the current search bounds "
            "[%.1f, %.1f] x [%.1f, %.1f]. %s"
            % (
                len(out_of_bounds), len(points), filename,
                IND_W, IND_E, IND_S, IND_N,
                "Auto-adjusting bounds." if auto_bounds
                else "Results may be incorrect.",
            )
        )

    # Auto-detect bounds from data so arbitrary datasets work out of the box
    if auto_bounds:
        IND_S, IND_N, IND_W, IND_E = compute_bounds(points)

    _data_cache[filename] = points

    # Pre-build a KDTree for fast nearest-neighbor queries
    if _HAS_SCIPY:
        tree = KDTree(np.array(points))
        _kdtree_cache[filename] = tree
        _kdtree_by_id[id(points)] = tree

    return points


# Separate cache for KDTree objects (keyed by filename like _data_cache).
_kdtree_cache = {}


def get_NN(data, lng, lat):
    """Return the nearest neighbor (lng, lat) from pre-loaded point data.

    When *scipy* is available this uses a KDTree for O(log n) lookups.
    Falls back to a brute-force O(n) scan otherwise.

    The original ``dist > 0`` guard is preserved so that exact matches
    (query point *is* a data point) are skipped, matching the original
    semantics used by the Voronoi boundary search.

    Raises ValueError if no valid neighbor is found.
    """
    Oracle.calls += 1

    # --- Fast path: KDTree lookup ---
    if _HAS_SCIPY:
        # O(1) lookup by list identity — replaces the old O(n) scan
        # over _data_cache that ran on every single NN query.
        tree = _kdtree_by_id.get(id(data))

        if tree is not None:
            # Query the 2 closest points — if the nearest is the query point
            # itself (dist ≈ 0), we return the second-nearest instead.
            k = min(2, len(data))
            dists, idxs = tree.query([lng, lat], k=k)
            if k == 1:
                dists = [dists]
                idxs = [idxs]
            for d, idx in zip(dists, idxs):
                if d > 0:
                    return data[idx]
            raise ValueError(
                "No valid nearest neighbor found for query (%s, %s)"
                % (lng, lat)
            )

    # --- Fallback: brute-force scan ---
    # Use squared distance to avoid sqrt on every comparison.
    # The ordering is identical because sqrt is monotonically increasing.
    mindist_sq = math.inf
    minlng = None
    minlat = None

    for slng, slat in data:
        dsq = eudist_sq(slng, slat, lng, lat)
        if (dsq > 0 and dsq <= mindist_sq):
            mindist_sq = dsq
            minlat = slat
            minlng = slng

    if minlng is None or minlat is None:
        raise ValueError(
            "No valid nearest neighbor found for query (%s, %s)"
            % (lng, lat)
        )
    return minlng, minlat


def mid_point(x1, y1, x2, y2):
    return round(((float)(x1 + x2) / 2), 2), round(((float)(y1 + y2) / 2), 2)


def perp_dir(x1, y1, x2, y2):
    if (y2 != y1):
        return round(((float)(x2 - x1) / (y1 - y2)), 2)
    return math.inf


def collinear(x1, y1, x2, y2, x3, y3, eps=1e-8):
    """Test whether three points are collinear using the cross-product.

    The previous implementation compared rounded slopes, which is fragile:
    it can produce false positives for nearly-parallel segments and false
    negatives when small coordinate differences amplify rounding error.

    The cross-product ``(x2-x1)*(y3-y1) - (y2-y1)*(x3-x1)`` is zero iff
    the points are exactly collinear.  We compare its magnitude against
    *eps* scaled by the lengths of the two vectors so the tolerance is
    relative, not absolute — this avoids misclassification for both very
    large and very small coordinate ranges.
    """
    cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)

    # Scale tolerance by the magnitude of the two edge vectors so the
    # check works regardless of coordinate range.
    len1 = math.hypot(x2 - x1, y2 - y1)
    len2 = math.hypot(x3 - x1, y3 - y1)
    scale = max(len1 * len2, 1e-12)

    return abs(cross) / scale < eps


NEW_DIR_MAX_ITER = 200


def _slopes_equal(m1, m2, rtol=1e-6):
    """Test whether two slopes are effectively equal.

    Handles the special case where both slopes are infinite (vertical
    lines).  For finite slopes, uses a relative tolerance comparison
    to avoid the fragility of exact float equality or rounding.
    """
    if math.isinf(m1) and math.isinf(m2):
        return True
    if math.isinf(m1) or math.isinf(m2):
        return False
    return abs(m1 - m2) <= rtol * max(abs(m1), abs(m2), 1.0)


def new_dir(data, aplng, aplat, alng, alat, dlng, dlat):
    if (alng == dlng):
        m1 = math.inf
    else:
        m1 = float(alat - dlat) / (alng - dlng)

    a1 = math.atan(m1)
    tth = 0.5
    th = math.atan(tth)
    for _iter in range(NEW_DIR_MAX_ITER):
        ac1 = a1 + th
        ac2 = ac1 + th
        tac1 = math.tan(ac1)
        tac2 = math.tan(ac2)

        Bc1 = isect_B(dlng, dlat, tac1)
        Bc2 = isect_B(dlng, dlat, tac2)

        Bc1x, Bc1y = find_CXY(Bc1, aplng, aplat)
        Bc2x, Bc2y = find_CXY(Bc2, aplng, aplat)
        c1x, c1y = bin_search(data, Bc1x, Bc1y, dlng, dlat, dlng, dlat)
        c2x, c2y = bin_search(data, Bc2x, Bc2y, dlng, dlat, dlng, dlat)

        th /= 2
        if (collinear(alng, alat, c1x, c1y, c2x, c2y) is True):
            break

    if (c1x == alng):
        return math.inf
    m = float(c1y - alat) / (c1x - alng)
    # Return the full-precision slope instead of rounding to 2 decimal
    # places.  The old ``round(m, 2)`` caused incorrect polygon tracing:
    # two genuinely different directions could round to the same value
    # (premature loop termination → incomplete polygon) or the same
    # direction could round differently across iterations (missed match).
    # Callers now use epsilon comparison via ``_slopes_equal()``.
    if m == 0.0 or m == -0.0:
        m = 0.0
    return m


def isect(x1, y1, x2, y2, x3, y3, x4, y4):

    if (x1 == x2 and x3 == x4):
        return -1, -1

    if (x1 == x2 and x3 != x4):
        m = float(y4 - y3) / (x4 - x3)
        y_test = m * (x1 - x3) + y3
        if (y1 >= y_test and y2 <= y_test) or (y1 <= y_test and y2 >= y_test):
            if (y3 >= y_test and y4 <= y_test) or (y3 <= y_test and y4 >= y_test):
                return x1, y_test
        return -1, -1

    if (x1 != x2 and x3 == x4):
        m = float(y2 - y1) / (x2 - x1)
        y_test = m * (x3 - x1) + y1
        if (y1 >= y_test and y2 <= y_test) or (y1 <= y_test and y2 >= y_test):
            if (y3 >= y_test and y4 <= y_test) or (y3 <= y_test and y4 >= y_test):
                return x3, y_test
        return -1, -1

    m1 = float(y2 - y1) / (x2 - x1)
    m2 = float(y4 - y3) / (x4 - x3)

    # Use relative tolerance instead of exact equality to catch
    # near-parallel lines that differ only due to floating-point
    # rounding.  Without this, (m1 - m2) can be tiny but non-zero,
    # producing wildly large intersection coordinates.  (fixes #13)
    if abs(m1 - m2) < 1e-10 * max(abs(m1), abs(m2), 1.0):
        return -1, -1

    c1 = y1 - m1 * x1
    c2 = y3 - m2 * x3

    x_test = float(c2 - c1) / (m1 - m2)
    y_test1 = m1 * (x_test - x1) + y1
    y_test2 = m2 * (x_test - x3) + y3

    if ((x1 >= x_test and x2 <= x_test) or (x1 <= x_test and x2 >= x_test)):
        if (x3 >= x_test and x4 <= x_test) or (x3 <= x_test and x4 >= x_test):
            if ((y1 >= y_test1 and y2 <= y_test1) or (y1 <= y_test1 and y2 >= y_test1)):
                if (y3 >= y_test2 and y4 <= y_test2) or (y3 <= y_test2 and y4 >= y_test2):
                    return round(x_test, 2), round(y_test1, 2)
    return -1, -1


def isect_B(alng, alat, dirn):
    """Find two boundary intersection points for a line through (alng, alat)."""
    ret = []
    if math.isinf(dirn):
        ret.append(alng)
        ret.append(IND_N)
        ret.append(alng)
        ret.append(IND_S)
        return ret
    elif dirn == 0:
        ret.append(IND_W)
        ret.append(alat)
        ret.append(IND_E)
        ret.append(alat)
        return ret
    else:
        xt = float(IND_N - alat) / dirn + alng
        xb = float(IND_S - alat) / dirn + alng
        yr = dirn * (IND_E - alng) + alat
        yl = dirn * (IND_W - alng) + alat

    if IND_W <= xt <= IND_E:
        ret.append(xt)
        ret.append(IND_N)
    if IND_W <= xb <= IND_E:
        ret.append(xb)
        ret.append(IND_S)

    if IND_S <= yl <= IND_N:
        ret.append(IND_W)
        ret.append(yl)
    if IND_S <= yr <= IND_N:
        ret.append(IND_E)
        ret.append(yr)

    if len(ret) == 4:
        return ret
    else:
        raise RuntimeError(
            "Line from (%s, %s) with slope %s does not intersect search "
            "boundary [%s,%s]x[%s,%s]"
            % (alng, alat, dirn, IND_W, IND_E, IND_S, IND_N)
        )


def eudist_sq(x1, y1, x2, y2):
    """Return the *squared* Euclidean distance between two points.

    Avoids the expensive ``math.sqrt`` call.  Use this whenever you
    only need to *compare* distances (sorting, nearest-neighbor checks,
    convergence tests) — the ordering is preserved because sqrt is
    monotonically increasing.

    The original ``eudist`` is kept for the few call sites that need the
    actual distance value (e.g. ``bin_search`` convergence tolerance).
    """
    dx = x1 - x2
    dy = y1 - y2
    return dx * dx + dy * dy


def eudist(x1, y1, x2, y2):
    """Euclidean distance between two points.

    Uses ``math.hypot`` which is implemented in C and also avoids
    overflow/underflow for very large/small coordinate differences.
    """
    return math.hypot(x1 - x2, y1 - y2)


def eudist_pts(p1, p2):
    """Euclidean distance between two points given as (x, y) tuples.

    Convenience wrapper around ``eudist`` for code that works with
    point tuples rather than separate coordinates.
    """
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


BIN_SEARCH_MAX_ITER = 100  # ~2^100 precision, far beyond float64 range


def bin_search(data, x1, y1, x2, y2, dlng, dlat):
    """Binary search for a Voronoi boundary point between two positions.

    *data* is a list of (lng, lat) tuples (returned by ``load_data``).

    An iteration limit (``BIN_SEARCH_MAX_ITER``) prevents runaway loops
    when the search window stops shrinking — e.g. due to equidistant
    nearest-neighbor ties where both branches produce the same midpoint.
    100 iterations is far more than enough for float64 precision (the
    mantissa has only 52 bits).

    The distance comparison uses a relative epsilon tolerance instead of
    rounding to 2 decimal places.  The old ``round(..., 2)`` approach
    caused the binary search to take incorrect branches when the two
    distances differed by less than 0.005 — the rounding made them
    appear equal (or vice versa), leading to misidentified Voronoi
    boundary points and incorrect region polygons.
    """
    xm = -1
    ym = -1

    for _ in range(BIN_SEARCH_MAX_ITER):
        if eudist(x1, y1, x2, y2) <= BIN_PREC:
            break

        xm = float(x1 + x2) / 2
        ym = float(y1 + y2) / 2
        lg, lt = get_NN(data, xm, ym)
        # Use squared distances for comparison — avoids two sqrt calls
        # per iteration.  The relative epsilon test is equivalent because
        # |sqrt(a) - sqrt(b)| / max(sqrt(a), sqrt(b))  ≈
        # |a - b| / (2 * max(a, b))  for a ≈ b, so we adjust the
        # tolerance accordingly.
        d1_sq = eudist_sq(lg, lt, xm, ym)
        d2_sq = eudist_sq(xm, ym, dlng, dlat)
        if abs(d1_sq - d2_sq) <= 2e-9 * max(d1_sq, d2_sq, 1e-24):
            x2 = xm
            y2 = ym
        else:
            x1 = xm
            y1 = ym

    if xm != -1 and ym != -1:
        xm = round(xm, 2)
        ym = round(ym, 2)
        if (xm == -0.0):
            xm = 0.0
        if (ym == -0.0):
            ym = 0.0
        return xm, ym
    else:
        raise RuntimeError(
            "Binary search failed to converge for query (%s, %s)"
            % (dlng, dlat)
        )


def _find_boundary_endpoint(B, dlng, dlat, clockwise=True):
    """Find the boundary endpoint for a Voronoi edge ray.

    Args:
        B: Boundary segment (x1, y1, x2, y2).
        dlng: Query point x-coordinate.
        dlat: Query point y-coordinate.
        clockwise: If True, find clockwise endpoint (CXY behavior).
                   If False, find counter-clockwise endpoint (BXY behavior).
    """
    x1 = B[0]
    y1 = B[1]
    x2 = B[2]
    y2 = B[3]
    x3 = dlng
    y3 = dlat

    n = ((y2 - y1) * (x3 - x1) - (x2 - x1) * (y3 - y1))
    d = ((y2 - y1) ** 2 + (x2 - x1) ** 2)

    # Guard against division by zero when both boundary endpoints coincide
    # (e.g. when the line passes through a corner of the search region).
    if d < 1e-12:
        return x1, y1

    k = float(n) / d
    x4 = x3 - k * (y2 - y1)
    y4 = y3 + k * (x2 - x1)

    # The clockwise/counter-clockwise logic differs only in which
    # comparisons select endpoint 1 vs endpoint 2. Rather than
    # duplicating the entire if-tree, we branch on the direction flag.
    if clockwise:
        # CXY behavior
        if (x4 > x3):
            if (y2 < y1):
                By = y2
                Bx = x2
            else:
                By = y1
                Bx = x1
        elif (x4 < x3):
            if (y2 > y1):
                By = y2
                Bx = x2
            else:
                By = y1
                Bx = x1
        elif (x4 == x3):
            if (y4 > y3):
                if (x1 > x2):
                    Bx = x1
                    By = y1
                else:
                    Bx = x2
                    By = y2
            elif (y4 < y3):
                if (x1 < x2):
                    Bx = x1
                    By = y1
                else:
                    Bx = x2
                    By = y2
            else:
                # Degenerate case: projection coincides with query point
                Bx = x1
                By = y1
    else:
        # BXY behavior — comparisons flipped
        if (x4 > x3):
            if (y2 > y1):
                By = y2
                Bx = x2
            else:
                By = y1
                Bx = x1
        elif (x4 < x3):
            if (y2 < y1):
                By = y2
                Bx = x2
            else:
                By = y1
                Bx = x1
        elif (x4 == x3):
            if (y4 > y3):
                if (x1 < x2):
                    Bx = x1
                    By = y1
                else:
                    Bx = x2
                    By = y2
            elif (y4 < y3):
                if (x1 > x2):
                    Bx = x1
                    By = y1
                else:
                    Bx = x2
                    By = y2
            else:
                # Degenerate case: projection coincides with query point
                Bx = x1
                By = y1
    return Bx, By


def find_CXY(B, dlng, dlat):
    """Find the clockwise boundary endpoint for a Voronoi edge ray."""
    return _find_boundary_endpoint(B, dlng, dlat, clockwise=True)


def find_BXY(B, dlng, dlat):
    """Find the counter-clockwise boundary endpoint for a Voronoi edge ray."""
    return _find_boundary_endpoint(B, dlng, dlat, clockwise=False)


def find_a1(data, alng, alat, dlng, dlat, dirn):
    """Find the next Voronoi vertex along the boundary from (alng, alat)."""
    B = isect_B(alng, alat, dirn)
    if (alng, alat) == (B[0], B[1]):
        Bx = B[2]
        By = B[3]
    elif (alng, alat) == (B[2], B[3]):
        Bx = B[0]
        By = B[1]
    else:
        Bx, By = find_BXY(B, dlng, dlat)
    return bin_search(data, Bx, By, alng, alat, dlng, dlat)


def polygon_area(alng, alat):
    """Calculate polygon area using the Shoelace formula.

    Uses a single zip-based loop to avoid per-iteration index arithmetic,
    and an ``abs()`` instead of a conditional multiply for the sign flip.
    """
    n = len(alat)
    if n < 2:
        return 0.0
    area = alat[-1] * alng[0] - alat[0] * alng[-1]  # closing edge
    for i in range(n - 1):
        area += alat[i] * alng[i + 1] - alat[i + 1] * alng[i]
    return round(abs(area) * 0.5, 2)


def find_area(data, dlng, dlat):
    """Compute the area and vertex count of the Voronoi region for a data point.

    Traces the Voronoi region boundary by iteratively finding vertices
    along the perpendicular bisector edges until the polygon closes.
    """
    elng, elat = get_NN(data, dlng, dlat)
    alng, alat = mid_point(dlng, dlat, elng, elat)
    dirn = perp_dir(elng, elat, dlng, dlat)

    ag = [alng]
    at = [alat]
    i = 0
    while True:
        ag.append(0)
        at.append(0)

        a_g, a_t = find_a1(data, ag[i], at[i], dlng, dlat, dirn)
        if get_NN(data, a_g, a_t) == (dlng, dlat):
            ag[i + 1] = a_g
            at[i + 1] = a_t
        else:
            raise RuntimeError(
                "Voronoi vertex (%s, %s) does not map back to data point (%s, %s)"
                % (a_g, a_t, dlng, dlat)
            )

        dirn1 = new_dir(data,
            ag[i], at[i], ag[i + 1], at[i + 1], dlng, dlat)

        if i > 2:
            if _slopes_equal(dirn, dirn1):
                break

            fin_isect = isect(
                ag[i + 1], at[i + 1], ag[i], at[i], elng, elat, dlng, dlat)

            if fin_isect != (-1, -1):
                break

            if i >= MAX_VERTICES:
                warnings.warn(
                    "Voronoi region for point (%s, %s) exceeded %d vertices; "
                    "polygon may be incomplete"
                    % (dlng, dlat, MAX_VERTICES)
                )
                break

        dirn = dirn1
        i += 1

    area = polygon_area(ag, at)
    return area, len(ag)


MAX_RETRIES = 50


def get_sum(FILENAME, N1, _depth=0):
    """Estimate the number of Voronoi regions by random point sampling.

    Loads the data file once via ``load_data`` (cached) and passes the
    in-memory point list through all subsequent calls, eliminating
    redundant disk I/O.

    Uses an iterative retry loop (instead of recursion) to avoid stack
    overflow.  Tracks the best estimate seen so far and returns it when
    max retries are exhausted.  The acceptance window widens slightly on
    each retry so the algorithm converges even on difficult distributions.

    Fixes issue #14: zero-area regions are excluded from the estimate
    using a clean valid-only list rather than the old index-mismatch
    pattern where ``N`` was decremented but zero entries remained in
    the summation array.
    """
    data = load_data(FILENAME)
    total_area = (IND_N - IND_S) * (IND_E - IND_W)

    best_estimate = None
    best_distance = float('inf')

    for attempt in range(MAX_RETRIES):
        Oracle.calls = 0
        max_edges = 0
        sum_edges = 0
        # Use running sums instead of collecting into a list — avoids
        # O(N1) list allocation and a second pass for the mean.
        sum_estimates = 0.0
        valid_count = 0

        for i in range(N1):
            plng = random.uniform(IND_W, IND_E)
            plat = random.uniform(IND_S, IND_N)
            dlng, dlat = get_NN(data, plng, plat)

            area, v_edges = find_area(data, dlng, dlat)

            if area > 0:
                sum_estimates += total_area / area
                valid_count += 1
                sum_edges += v_edges
                if v_edges > max_edges:
                    max_edges = v_edges

        # Compute mean over valid samples only (fixes #14 bias)
        if valid_count > 0:
            Sum = sum_estimates / valid_count
            avg_edges = sum_edges / valid_count
        else:
            Sum = 0
            avg_edges = 0

        # Track the closest estimate we've seen
        dist = abs(Sum - N1)
        if dist < best_distance:
            best_distance = dist
            best_estimate = (Sum, max_edges, avg_edges)

        # Widen acceptance window slightly each retry (5% per attempt)
        lo_factor = max(0.2, 0.5 - 0.05 * attempt)
        hi_factor = min(3.0, 1.5 + 0.05 * attempt)

        if (N1 * lo_factor <= Sum <= N1 * hi_factor):
            if (Sum <= N1):
                print(int(Sum) + 1, max_edges, avg_edges, Oracle.calls)
                return int(Sum) + 1, max_edges, avg_edges
            else:
                print(int(Sum), max_edges, avg_edges, Oracle.calls)
                return int(Sum), max_edges, avg_edges

    # Exhausted retries — return best estimate seen
    print("Warning: max retries (%d) reached, returning best estimate" % MAX_RETRIES)
    est_sum, est_max_e, est_avg_e = best_estimate
    result = int(est_sum) + (1 if est_sum <= N1 else 0)
    print(result, est_max_e, est_avg_e)
    return result, est_max_e, est_avg_e


def main():
    """CLI entry point for VoronoiMap estimation."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Estimate Voronoi region count via random point sampling.',
        epilog='Example: voronoimap datauni5.txt 5',
    )
    parser.add_argument(
        'datafile',
        help='Point data filename inside the data/ directory (e.g. datauni5.txt)',
    )
    parser.add_argument(
        'n',
        type=int,
        help='Expected number of Voronoi regions (sample size)',
    )
    parser.add_argument(
        '--runs',
        type=int,
        default=1,
        help='Number of independent estimation runs (default: 1)',
    )
    parser.add_argument(
        '--bounds',
        nargs=4,
        type=float,
        metavar=('S', 'N', 'W', 'E'),
        help='Explicit search space boundaries: south north west east. '
             'Disables auto-detection from data.',
    )
    parser.add_argument(
        '--visualize',
        metavar='OUTPUT',
        help='Generate an SVG visualization of the Voronoi diagram. '
             'Provide the output file path (e.g. diagram.svg).',
    )
    parser.add_argument(
        '--color-scheme',
        default='pastel',
        choices=['pastel', 'warm', 'cool', 'earth', 'mono', 'rainbow'],
        help='Color scheme for SVG visualization (default: pastel).',
    )
    parser.add_argument(
        '--show-labels',
        action='store_true',
        help='Label each Voronoi region with its seed index in the SVG.',
    )
    parser.add_argument(
        '--svg-width',
        type=int,
        default=800,
        help='SVG canvas width in pixels (default: 800).',
    )
    parser.add_argument(
        '--svg-height',
        type=int,
        default=600,
        help='SVG canvas height in pixels (default: 600).',
    )
    parser.add_argument(
        '--interactive',
        metavar='OUTPUT',
        help='Generate an interactive HTML visualization with pan/zoom, '
             'hover tooltips, and live color switching. Provide the output '
             'file path (e.g. diagram.html).',
    )
    parser.add_argument(
        '--geojson',
        metavar='OUTPUT',
        help='Export Voronoi regions as GeoJSON for use in GIS tools '
             '(QGIS, Mapbox, Leaflet, Google Earth). Provide the output '
             'file path (e.g. diagram.geojson).',
    )
    parser.add_argument(
        '--no-seeds',
        action='store_true',
        help='When exporting GeoJSON, omit seed points (include only region polygons).',
    )
    parser.add_argument(
        '--crs',
        metavar='CRS',
        help='CRS identifier for GeoJSON export (e.g. '
             '"urn:ogc:def:crs:EPSG::4326"). Omitted by default per RFC 7946.',
    )
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Print a table of per-region statistics (area, perimeter, '
             'centroid, compactness, vertex count) to stdout.',
    )
    parser.add_argument(
        '--stats-csv',
        metavar='OUTPUT',
        help='Export per-region statistics as a CSV file. Includes a '
             'commented summary section at the bottom.',
    )
    parser.add_argument(
        '--stats-json',
        metavar='OUTPUT',
        help='Export per-region statistics as a JSON file with both '
             'per-region data and aggregate summary.',
    )
    parser.add_argument(
        '--relax',
        type=int,
        metavar='N',
        help='Apply N iterations of Lloyd relaxation before visualization. '
             'Moves seed points to Voronoi cell centroids, producing '
             'more uniform regions.',
    )
    parser.add_argument(
        '--relax-animate',
        metavar='OUTPUT',
        help='Generate an animated HTML visualization of the Lloyd '
             'relaxation process with play/pause controls, step slider, '
             'and convergence graph. Provide the output file path '
             '(e.g. relaxation.html).',
    )
    parser.add_argument(
        '--graph',
        action='store_true',
        help='Print the neighbourhood graph statistics to stdout. '
             'Shows degree distribution, clustering coefficient, '
             'diameter, connected components, and more.',
    )
    parser.add_argument(
        '--graph-json',
        metavar='OUTPUT',
        help='Export the neighbourhood graph as a JSON file with '
             'nodes, edges (with lengths), and graph statistics.',
    )
    parser.add_argument(
        '--graph-csv',
        metavar='OUTPUT',
        help='Export the neighbourhood graph as a CSV edge list.',
    )
    parser.add_argument(
        '--graph-svg',
        metavar='OUTPUT',
        help='Export an SVG showing the Voronoi diagram with the '
             'neighbourhood graph (Delaunay dual) overlaid. Red edges '
             'connect seed points that share a Voronoi boundary.',
    )
    parser.add_argument(
        '--graph-labels',
        action='store_true',
        help='Show node degree labels in the graph SVG overlay.',
    )

    # ── Point pattern analysis arguments ──
    parser.add_argument(
        '--pattern',
        action='store_true',
        help='Run point pattern analysis on the seed points and print '
             'a text report (Clark-Evans NNI, quadrat analysis, '
             "Ripley's K/L function).",
    )
    parser.add_argument(
        '--pattern-json',
        metavar='OUTPUT',
        help='Export point pattern analysis as a JSON file.',
    )

    # ── Point location & nearest-neighbor query arguments ──
    parser.add_argument(
        '--query',
        metavar='X,Y',
        help='Query a single point (e.g. --query 300,400).',
    )
    parser.add_argument(
        '--query-k',
        type=int,
        default=1,
        metavar='K',
        help='Number of nearest seeds to return (default: 1).',
    )
    parser.add_argument(
        '--query-batch',
        metavar='FILE',
        help='Batch query from a CSV or JSON file of points.',
    )
    parser.add_argument(
        '--query-radius',
        type=float,
        metavar='R',
        help='Return all seeds within radius R of the query point.',
    )
    parser.add_argument(
        '--query-json',
        metavar='PATH',
        help='Export query results as JSON.',
    )
    parser.add_argument(
        '--query-svg',
        metavar='PATH',
        help='Export an SVG showing query points and nearest-seed connections.',
    )

    args = parser.parse_args()

    # Apply explicit bounds if given (disables auto-detection)
    if args.bounds:
        set_bounds(*args.bounds)
        # Load data without auto-bounds since the user specified them
        load_data(args.datafile, auto_bounds=False)

    for run in range(args.runs):
        result, max_e, avg_e = get_sum(args.datafile, args.n)
        print('Run %d: regions=%d  max_edges=%d  avg_edges=%.1f'
              % (run + 1, result, max_e, avg_e))

    # ── Shared data + region computation ─────────────────────────────
    # Load data and compute regions once, shared across all output
    # formats.  Previously each output section loaded and computed
    # independently, which (a) wasted work and (b) caused --relax to
    # only apply to --visualize, silently ignoring relaxation for
    # --interactive, --geojson, --stats, and --graph outputs.
    needs_regions = any([
        args.visualize, args.interactive, args.geojson,
        args.stats, args.stats_csv, args.stats_json,
        args.relax_animate,
        args.graph, args.graph_json, args.graph_csv, args.graph_svg,
        args.query, args.query_batch,
    ])

    data = None
    regions = None

    if needs_regions:
        import vormap_viz

        data = load_data(args.datafile)

        # Apply Lloyd relaxation if requested — this now applies to
        # ALL output formats, not just --visualize.
        if args.relax:
            print('Applying %d iterations of Lloyd relaxation...' % args.relax)
            relax_result = vormap_viz.lloyd_relaxation(
                data, iterations=args.relax
            )
            data = relax_result['points']
            regions = relax_result['regions']
            print('Relaxation complete after %d iterations (converged=%s)'
                  % (relax_result['total_iterations'],
                     relax_result['converged']))
        else:
            print('Computing Voronoi regions...')
            regions = vormap_viz.compute_regions(data)

        print('Traced %d of %d regions' % (len(regions), len(data)))

    # SVG visualization
    if args.visualize:
        import vormap_viz

        vormap_viz.export_svg(
            regions,
            data,
            args.visualize,
            width=args.svg_width,
            height=args.svg_height,
            color_scheme=args.color_scheme,
            show_labels=args.show_labels,
            title='Voronoi Diagram — %s (%d points)'
                  % (args.datafile, len(data)),
        )
        print('SVG saved to %s' % args.visualize)

    # Interactive HTML visualization
    if args.interactive:
        import vormap_viz

        vormap_viz.export_html(
            regions,
            data,
            args.interactive,
            width=args.svg_width,
            height=args.svg_height,
            color_scheme=args.color_scheme,
            title='Voronoi Diagram — %s (%d points)'
                  % (args.datafile, len(data)),
        )
        print('Interactive HTML saved to %s' % args.interactive)

    # GeoJSON export
    if args.geojson:
        import vormap_viz

        vormap_viz.export_geojson(
            regions,
            data,
            args.geojson,
            include_seeds=not args.no_seeds,
            crs_name=args.crs,
        )
        print('GeoJSON saved to %s' % args.geojson)

    # Region statistics
    if args.stats or args.stats_csv or args.stats_json:
        import vormap_viz

        region_stats = vormap_viz.compute_region_stats(regions, data)

        if args.stats:
            print()
            print(vormap_viz.format_stats_table(region_stats))

        if args.stats_csv:
            vormap_viz.export_stats_csv(region_stats, args.stats_csv)
            print('Statistics CSV saved to %s' % args.stats_csv)

        if args.stats_json:
            vormap_viz.export_stats_json(region_stats, args.stats_json)
            print('Statistics JSON saved to %s' % args.stats_json)

    # Lloyd relaxation animation
    if args.relax_animate:
        import vormap_viz

        # Animation always starts from original (unrelaxed) data to
        # show the full relaxation process.
        original_data = load_data(args.datafile)
        iters = args.relax if args.relax else 10
        print('Generating relaxation animation (%d iterations)...' % iters)

        vormap_viz.export_relaxation_html(
            original_data,
            iterations=iters,
            output_path=args.relax_animate,
            width=args.svg_width,
            height=args.svg_height,
            color_scheme=args.color_scheme,
            title='Lloyd Relaxation — %s (%d points)'
                  % (args.datafile, len(original_data)),
        )
        print('Relaxation animation saved to %s' % args.relax_animate)

    # Neighbourhood graph
    if args.graph or args.graph_json or args.graph_csv or args.graph_svg:
        import vormap_viz

        graph = vormap_viz.extract_neighborhood_graph(regions, data)
        print('Graph: %d nodes, %d edges' % (graph['num_nodes'], graph['num_edges']))

        if args.graph:
            print()
            print(vormap_viz.format_graph_stats_table(graph))

        if args.graph_json:
            vormap_viz.export_graph_json(graph, args.graph_json)
            print('Graph JSON saved to %s' % args.graph_json)

        if args.graph_csv:
            vormap_viz.export_graph_csv(graph, args.graph_csv)
            print('Graph CSV saved to %s' % args.graph_csv)

        if args.graph_svg:
            vormap_viz.export_graph_svg(
                regions,
                data,
                graph,
                args.graph_svg,
                width=args.svg_width,
                height=args.svg_height,
                color_scheme=args.color_scheme,
                show_degree_labels=args.graph_labels,
                title='Neighbourhood Graph — %s (%d points, %d edges)'
                      % (args.datafile, len(data), graph['num_edges']),
            )
            print('Graph SVG saved to %s' % args.graph_svg)

    # ── Point pattern analysis ──
    if args.pattern or args.pattern_json:
        import vormap_pattern as vp
        bounds_tuple = None
        if args.bounds:
            s, n, w, e = args.bounds
            bounds_tuple = (w, e, s, n)
        else:
            bounds_tuple = None  # auto-derive from points

        summary = vp.analyze_pattern(data, bounds=bounds_tuple)

        if args.pattern:
            print(vp.format_pattern_report(summary))

        if args.pattern_json:
            import json
            result = vp.generate_pattern_json(summary)
            with open(args.pattern_json, 'w') as f:
                json.dump(result, f, indent=2)
            print('Pattern analysis JSON saved to %s' % args.pattern_json)

    # ── Point location & nearest-neighbor query ──
    if args.query or args.query_batch:
        import vormap_query
        vormap_query.run_query_cli(args, data, regions)


if __name__ == '__main__':
    main()
