
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


IND_S = 0.0
IND_N = 1000.0
IND_W = 0.0
IND_E = 2000.0
BIN_PREC = 1e-6

# Maximum vertices a single Voronoi region can have before we give up.
# Prevents infinite loops on degenerate point configurations.
MAX_VERTICES = 50


class Oracle:
    calls = 0


# Cache loaded data files so each file is read from disk exactly once.
# When scipy is available, a KDTree is stored alongside the point list.
_data_cache = {}


def load_data(filename):
    """Load point data from a file and cache it in memory.

    Returns a list of (lng, lat) tuples.  Subsequent calls with the same
    filename return the cached list without re-reading the file.

    When *scipy* is available a ``KDTree`` is also built and cached so
    that ``get_NN`` can use O(log n) lookups instead of O(n) scans.
    """
    if filename in _data_cache:
        return _data_cache[filename]

    points = []
    with open("data/" + filename, "r") as objf:
        for line in objf:
            if not line.strip():
                continue
            coord = line.split()
            if len(coord) < 2:
                continue
            points.append((float(coord[0]), float(coord[1])))

    if not points:
        raise ValueError("No valid points found in '%s'" % filename)

    _data_cache[filename] = points

    # Pre-build a KDTree for fast nearest-neighbor queries
    if _HAS_SCIPY:
        _kdtree_cache[filename] = KDTree(np.array(points))

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
        # Find the filename key for this data list so we can grab its tree.
        # Since load_data caches the *same* list object, identity check is O(1).
        tree = None
        for fname, cached_data in _data_cache.items():
            if cached_data is data:
                tree = _kdtree_cache.get(fname)
                break

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
    mindist = math.inf
    minlng = None
    minlat = None

    for slng, slat in data:
        dist = eudist(slng, slat, lng, lat)
        if (dist > 0 and dist <= mindist):
            mindist = dist
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


def new_dir(data, aplng, aplat, alng, alat, dlng, dlat):
    if (alng == dlng):
        m1 = math.inf
    else:
        m1 = float(alat - dlat) / (alng - dlng)

    a1 = math.atan(m1)
    #print "ANGLE A1", a1
    c1x = -1.1
    c1y = -2.5
    c2x = -3.3
    c2y = -5.7

    tth = 0.5
    th = math.atan(tth)
    #print "TH = ", th
    for _iter in range(NEW_DIR_MAX_ITER):
        #print "NewDirection"
        ac1 = a1 + th
        ac2 = ac1 + th
        ##print "C ANGLES=", ac1, ac2
        tac1 = math.tan(ac1)
        tac2 = math.tan(ac2)

        Bc1 = isect_B(dlng, dlat, tac1)
        ##print "Bc1", Bc1
        Bc2 = isect_B(dlng, dlat, tac2)
        ##print "Bc2", Bc2

        Bc1x, Bc1y = find_CXY(Bc1, aplng, aplat)
        Bc2x, Bc2y = find_CXY(Bc2, aplng, aplat)
        ##print "C BORDERS=", Bc1x, Bc1y, Bc2x, Bc2y
        c1x, c1y = bin_search(data, Bc1x, Bc1y, dlng, dlat, dlng, dlat)
        c2x, c2y = bin_search(data, Bc2x, Bc2y, dlng, dlat, dlng, dlat)
        ##print "INT C1, C2", c1x, c1y, c2x, c2y

        th /= 2
        if (collinear(alng, alat, c1x, c1y, c2x, c2y) is True):
            break

    if (c1x == alng):
        return math.inf
    ##print "C1 C2 A = ", c1x, c1y, c2x, c2y, alng, alat
    m = float(c1y - alat) / (c1x - alng)
    ##print "SLOPE=", m
    m_ = round(m, 2)
    if (m_ == -0.0):
        m_ = 0.0
    return m_


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

    m1 = (float)(y2 - y1) / (x2 - x1)
    m2 = (float)(y4 - y3) / (x4 - x3)

    if (m1 == m2):
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
    ret = []
    if math.isinf(dirn):
        ret.append(alng)
        ret.append(IND_N)
        ret.append(alng)
        ret.append(IND_S)
        return ret
    elif (dirn == 0):
        ret.append(IND_W)
        ret.append(alat)
        ret.append(IND_E)
        ret.append(alat)
        return ret
    else:
        xt = (float)((IND_N - alat) / dirn) + alng
        xb = (float)((IND_S - alat) / dirn) + alng
        yr = (dirn * (IND_E - alng)) + alat
        yl = (dirn * (IND_W - alng)) + alat

    if (xt <= IND_E and xt >= IND_W):
        ret.append(xt)
        ret.append(IND_N)
    if (xb <= IND_E and xb >= IND_W):
        ret.append(xb)
        ret.append(IND_S)

    if (yl <= IND_N and yl >= IND_S):
        ret.append(IND_W)
        ret.append(yl)
    if (yr <= IND_N and yr >= IND_S):
        ret.append(IND_E)
        ret.append(yr)

    if (len(ret) == 4):
        return ret
    else:
        raise RuntimeError(
            "Line from (%s, %s) with slope %s does not intersect search "
            "boundary [%s,%s]x[%s,%s]"
            % (alng, alat, dirn, IND_W, IND_E, IND_S, IND_N)
        )


def eudist(x1, y1, x2, y2):
    return math.sqrt(((x1 - x2) ** 2) + ((y1 - y2) ** 2))


BIN_SEARCH_MAX_ITER = 100  # ~2^100 precision, far beyond float64 range


def bin_search(data, x1, y1, x2, y2, dlng, dlat):
    """Binary search for a Voronoi boundary point between two positions.

    *data* is a list of (lng, lat) tuples (returned by ``load_data``).

    An iteration limit (``BIN_SEARCH_MAX_ITER``) prevents runaway loops
    when the search window stops shrinking — e.g. due to equidistant
    nearest-neighbor ties where both branches produce the same midpoint.
    100 iterations is far more than enough for float64 precision (the
    mantissa has only 52 bits).
    """
    xm = -1
    ym = -1

    for _ in range(BIN_SEARCH_MAX_ITER):
        if eudist(x1, y1, x2, y2) <= BIN_PREC:
            break

        xm = float(x1 + x2) / 2
        ym = float(y1 + y2) / 2
        lg, lt = get_NN(data, xm, ym)
        d1 = round(eudist(lg, lt, xm, ym), 2)
        d2 = round(eudist(xm, ym, dlng, dlat), 2)
        if(d1 == d2):
            x2 = xm
            y2 = ym
        else:
            x1 = xm
            y1 = ym

    if(xm != -1 and ym != -1):
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


def find_CXY(B, dlng, dlat):
    """Find the clockwise boundary endpoint for a Voronoi edge ray."""
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

    k = (float)(n) / d
    x4 = x3 - k * (y2 - y1)
    y4 = y3 + k * (x2 - x1)

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
    return Bx, By


def find_BXY(B, dlng, dlat):
    """Find the counter-clockwise boundary endpoint for a Voronoi edge ray."""
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

    k = (float)(n) / d
    x4 = x3 - k * (y2 - y1)
    y4 = y3 + k * (x2 - x1)

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


def find_a1(data, alng, alat, dlng, dlat, dirn):
    B = isect_B(alng, alat, dirn)
    #print "B VECTOR", B
    #print alng, alat
    if ((alng, alat) == (B[0], B[1])):
        Bx = B[2]
        By = B[3]
    elif ((alng, alat) == (B[2], B[3])):
        Bx = B[0]
        By = B[1]
    #elif ((B[2], B[3]) is (0, 8.74)):
        #Bx = 10
        #By = 8.74
        #print "HAHAHAHAHAHAHAHAHAHh", Bx, By
    else:
        t1, t2 = find_BXY(B, dlng, dlat)
        Bx = t1
        By = t2
        #print "B POINTS", Bx, By
    return bin_search(data, Bx, By, alng, alat, dlng, dlat)


def polygon_area(alng, alat):
    # calculate area using the Shoelace formula
    n = len(alat)
    area = 0
    for i in range(n - 1):
        area += alat[i] * alng[i + 1] - alat[i + 1] * alng[i]
    # close the polygon: last vertex back to first
    area += alat[n - 1] * alng[0] - alat[0] * alng[n - 1]
    area = (float)(area / 2)
    if(area < 0):
        area *= -1
    return round(area, 2)


def find_area(data, dlng, dlat):

    elng, elat = get_NN(data, dlng, dlat)
    alng, alat = mid_point(dlng, dlat, elng, elat)
    dirn = perp_dir(elng, elat, dlng, dlat)
    Oracle.calls = 0
    #print "E_POINT= ", elng, elat
    #print "A_POINT= ", alng, alat

    e0g = elng
    e0t = elat

    ag = []
    at = []
    ag.append(alng)
    at.append(alat)
    i = 0
    #print "VERTEX ADDED=", i, ag[i], at[i]
    d = []
    while (True):
        ag.append(0)
        at.append(0)
        #print "COUNTER========================================", i

        a_g, a_t = find_a1(data,
            ag[i], at[i], dlng, dlat, dirn)
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

        d.append(dirn1)

        #print "NEW DIR=", dirn1
        ##print "NEW E_POINT=", enlng, enlat
        #elng = enlng
        #elat = enlat
        if (i > 2):
            if (dirn == dirn1):
                break

            fin_isect = isect(
                ag[i + 1], at[i + 1], ag[i], at[i], e0g, e0t, dlng, dlat)

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
        #print
        #print
    #print ag
    #print at
    area = polygon_area(ag, at)
    #print "VERTICES= ", len(ag)
    ##print ag
    ##print at
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
    """
    data = load_data(FILENAME)

    best_estimate = None
    best_distance = float('inf')

    for attempt in range(MAX_RETRIES):
        Oracle.calls = 0
        S = []
        N = N1
        max_edges = 0
        avg_edges = 0
        sum_edges = 0
        for i in range(0, N):
            plng = random.uniform(IND_W, IND_E)
            plat = random.uniform(IND_S, IND_N)
            dlng, dlat = get_NN(data, plng, plat)

            area, v_edges = find_area(data, dlng, dlat)

            S.append(0)
            if (area != 0):
                S[i] = ((IND_N - IND_S) * (IND_E - IND_W)) / area
                sum_edges += v_edges
                if (v_edges > max_edges):
                    max_edges = v_edges
            else:
                N -= 1

        Sum = 0
        for i in range(0, N):
            Sum += S[i] / N
        if(N != 0):
            avg_edges = sum_edges / N

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


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Estimate Voronoi region count via random point sampling.',
        epilog='Example: python vormap.py datauni5.txt 5',
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

    args = parser.parse_args()

    for run in range(args.runs):
        result, max_e, avg_e = get_sum(args.datafile, args.n)
        print('Run %d: regions=%d  max_edges=%d  avg_edges=%.1f'
              % (run + 1, result, max_e, avg_e))
