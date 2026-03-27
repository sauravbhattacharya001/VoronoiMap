
import random
import math
import os
import warnings

# ── Resource limits to prevent denial-of-service via crafted input ───
# These can be overridden by callers before loading data.
MAX_INPUT_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
MAX_POINT_COUNT = 10_000_000  # 10 million points

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


def _validate_path(filepath, *, base_dir=None, allow_absolute=False, label="File"):
    """Shared implementation for path traversal validation.

    Ensures *filepath* resolves to a location inside *base_dir* (default:
    current working directory).  Rejects absolute paths and ``..``
    traversals that escape the boundary.

    When *allow_absolute* is True **and** *filepath* is already absolute,
    the containment check is skipped — the caller is trusted to provide
    an explicit path (e.g. from CLI ``argparse``).  Relative paths with
    ``..`` segments are still validated against *base_dir* even when
    *allow_absolute* is True.

    Parameters
    ----------
    filepath : str
        The path to validate.
    base_dir : str or None
        Directory the path must resolve within.  Defaults to ``os.getcwd()``.
    allow_absolute : bool
        If True, accept absolute paths without containment check.
    label : str
        Human-readable label for error messages (e.g. "File", "Output file").

    Returns
    -------
    str
        The resolved absolute path, safe to open.

    Raises
    ------
    ValueError
        If the path escapes *base_dir* or is absolute when not allowed.
    """
    if not filepath:
        raise ValueError("%s path must not be empty" % label)

    if os.path.isabs(filepath):
        if not allow_absolute:
            raise ValueError(
                "Absolute paths are not allowed: '%s'" % filepath
            )
        return os.path.abspath(filepath)

    if base_dir is None:
        base_dir = os.getcwd()

    abs_base = os.path.abspath(base_dir)
    resolved = os.path.abspath(os.path.join(abs_base, filepath))

    if not resolved.startswith(abs_base + os.sep) and resolved != abs_base:
        raise ValueError(
            "%s path traversal detected — '%s' resolves outside '%s'"
            % (label, filepath, base_dir)
        )

    return resolved


def validate_input_path(filepath, *, base_dir=None, allow_absolute=False):
    """Validate a file path against path traversal attacks.

    Ensures *filepath* resolves to a location inside *base_dir* (default:
    current working directory).  See :func:`_validate_path` for details.
    """
    return _validate_path(
        filepath, base_dir=base_dir, allow_absolute=allow_absolute, label="File"
    )


def validate_output_path(filepath, *, base_dir=None, allow_absolute=False):
    """Validate an output file path against path traversal attacks.

    Works like :func:`validate_input_path` but for write operations.
    See :func:`_validate_path` for details.
    """
    return _validate_path(
        filepath, base_dir=base_dir, allow_absolute=allow_absolute, label="Output file"
    )


def compute_bounds(points, padding=0.1):
    """Compute search space boundaries from a set of points.

    Returns (south, north, west, east) with *padding* fraction added
    on each side so Voronoi cells near the boundary are not clipped.

    Raises ValueError if *points* is empty.
    """
    if not points:
        raise ValueError("Cannot compute bounds from an empty point list.")
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


import re as _re

_CSS_VALUE_RE = _re.compile(r'^[a-zA-Z0-9#_.,() /%+-]+$')


def sanitize_css_value(value):
    """Sanitize a string intended for use as a CSS property value.

    Prevents CSS injection attacks when user-supplied color or style
    parameters are interpolated into ``<style>`` blocks inside SVG output.
    For example, a malicious ``edge_color`` like
    ``"red; } svg { display: none; } .x {"`` could break out of the CSS
    rule and inject arbitrary styles.

    Only allows safe characters: alphanumerics, ``#``, ``_``, ``.``,
    ``,``, ``(``, ``)``, spaces, ``%``, ``+``, ``-``, and ``/``.
    Characters like ``{``, ``}``, ``;``, ``:``, ``<``, ``>``, ``"``,
    ``'``, and ``\\`` are rejected.

    Parameters
    ----------
    value : str
        The CSS value to sanitize.

    Returns
    -------
    str
        The validated value (unchanged if safe).

    Raises
    ------
    ValueError
        If the value contains unsafe characters.
    """
    s = str(value)
    if not _CSS_VALUE_RE.match(s):
        raise ValueError(
            "Unsafe CSS value rejected: %r — only alphanumerics, "
            "#, _, ., and common CSS characters are allowed" % s
        )
    return s

# Maximum vertices a single Voronoi region can have before we give up.
# Prevents infinite loops on degenerate point configurations.
MAX_VERTICES = 50

# Maximum total cells in a grid (nx * ny) to prevent memory exhaustion.
# At ~8 bytes per float, 4M cells ≈ 32 MB — well within reason.
# A 2000×2000 grid is sufficient for any practical visualization.
MAX_GRID_CELLS = 4_000_000


def validate_grid_size(nx: int, ny: int, *, caller: str = ""):
    """Raise ValueError if nx * ny exceeds MAX_GRID_CELLS.

    Prevents denial-of-service via absurdly large grid requests
    (e.g. nx=100000, ny=100000 → 10 billion cells).

    Parameters
    ----------
    nx, ny : int
        Grid dimensions.
    caller : str
        Name of the calling function, for error messages.
    """
    total = nx * ny
    if total > MAX_GRID_CELLS:
        label = f" in {caller}" if caller else ""
        raise ValueError(
            f"Grid size {nx}x{ny} = {total:,} cells exceeds maximum "
            f"of {MAX_GRID_CELLS:,}{label}. "
            f"Reduce nx/ny or increase vormap.MAX_GRID_CELLS if you "
            f"have sufficient memory."
        )


class Oracle:
    """Counter for tracking the number of geometric oracle calls.

    Used to measure computational cost of spatial operations.
    The class-level ``calls`` attribute is incremented each time a
    distance or nearest-neighbor query is performed, enabling
    algorithm efficiency analysis.
    """
    __slots__ = ()
    calls = 0


# Unified cache: filename → { 'points': list, 'tree': KDTree | None }
# Eliminates the old _kdtree_by_id dict that used id() as keys,
# which was unsafe — Python can reuse object ids after GC, leading
# to stale cache hits returning wrong KDTrees.  (Fixes #28)
_file_cache = {}

# O(1) reverse lookup: id(points_list) → KDTree.
# Eliminates the O(n) scan in get_NN that iterated through every
# _file_cache entry on every single nearest-neighbor call.
_tree_by_data_id = {}


# ---------------------------------------------------------------------------
# Backward-compatible cache views
# ---------------------------------------------------------------------------
# Tests and external code may reference the old cache dicts directly
# (e.g. ``vormap._data_cache.pop(...)``).  These thin wrappers proxy
# reads/writes into the unified ``_file_cache`` so existing code keeps
# working without modification.

class _DataCacheView(dict):
    """Dict-like view over _file_cache that exposes filename → points."""

    def __contains__(self, key):
        return key in _file_cache

    def __getitem__(self, key):
        return _file_cache[key]['points']

    def get(self, key, default=None):
        entry = _file_cache.get(key)
        return entry['points'] if entry else default

    def pop(self, key, *args):
        entry = _file_cache.pop(key, *args)
        if isinstance(entry, dict):
            return entry['points']
        return entry

    def clear(self):
        _file_cache.clear()
        _tree_by_data_id.clear()


class _KDTreeCacheView(dict):
    """Dict-like view over _file_cache that exposes filename → KDTree."""

    def __contains__(self, key):
        return key in _file_cache and _file_cache[key].get('tree') is not None

    def __getitem__(self, key):
        return _file_cache[key]['tree']

    def get(self, key, default=None):
        entry = _file_cache.get(key)
        return entry['tree'] if entry else default

    def pop(self, key, *args):
        entry = _file_cache.get(key)
        if entry is not None:
            tree = entry.get('tree')
            entry['tree'] = None
            return tree
        if args:
            return args[0]
        raise KeyError(key)

    def clear(self):
        for entry in _file_cache.values():
            entry['tree'] = None


class _KDTreeByIdView(dict):
    """Backward-compat view for the removed _kdtree_by_id dict.

    Supports ``id(points) in vormap._kdtree_by_id`` and
    ``vormap._kdtree_by_id.pop(id(points), None)`` used in tests.
    """

    def __contains__(self, obj_id):
        for entry in _file_cache.values():
            if id(entry['points']) == obj_id and entry.get('tree') is not None:
                return True
        return False

    def get(self, obj_id, default=None):
        for entry in _file_cache.values():
            if id(entry['points']) == obj_id:
                return entry.get('tree', default)
        return default

    def pop(self, obj_id, *args):
        for entry in _file_cache.values():
            if id(entry['points']) == obj_id:
                tree = entry.get('tree')
                entry['tree'] = None
                return tree
        if args:
            return args[0]
        raise KeyError(obj_id)


_data_cache = _DataCacheView()
_kdtree_cache = _KDTreeCacheView()
_kdtree_by_id = _KDTreeByIdView()


def _check_point_limit(points, filename="<unknown>"):
    """Raise if point list exceeds MAX_POINT_COUNT (resource exhaustion guard)."""
    if len(points) > MAX_POINT_COUNT:
        raise ValueError(
            "File '%s' contains %d points, exceeding the %d limit. "
            "Increase vormap.MAX_POINT_COUNT to override."
            % (filename, len(points), MAX_POINT_COUNT)
        )


def _detect_format(filepath):
    """Detect file format from extension and content sniffing.

    Returns one of: 'txt', 'csv', 'json', 'geojson'.
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.geojson':
        return 'geojson'
    if ext == '.json':
        # Peek at content to distinguish JSON array from GeoJSON
        try:
            with open(filepath, 'r') as f:
                # Read first non-whitespace chars to detect GeoJSON
                content = f.read(2048)
                content_stripped = content.lstrip()
                if '"type"' in content and ('"FeatureCollection"' in content
                                            or '"Feature"' in content):
                    return 'geojson'
        except (IOError, OSError):
            pass
        return 'json'
    if ext == '.csv':
        return 'csv'
    # Default: space-separated text
    return 'txt'


def _parse_points_txt(filepath):
    """Parse space-separated text file (original format)."""
    points = []
    with open(filepath, 'r') as objf:
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
                continue
            if not (math.isfinite(lng_val) and math.isfinite(lat_val)):
                continue
            points.append((lng_val, lat_val))
    return points


def _parse_points_csv(filepath):
    """Parse CSV file with headers. Looks for x/y, lng/lat, lon/lat columns.

    Supports common column names: x/y, lng/lat, lon/lat, longitude/latitude,
    easting/northing. Falls back to first two numeric columns if no known
    headers are found.

    Opens the file only once — the dialect is sniffed from a buffered
    sample and the same file handle is rewound for streaming rows.
    """
    import csv

    HEADER_PAIRS = [
        ('x', 'y'), ('lng', 'lat'), ('lon', 'lat'),
        ('longitude', 'latitude'), ('easting', 'northing'),
        ('long', 'lat'),
    ]

    points = []
    with open(filepath, 'r', newline='') as f:
        # Sniff the dialect once from a sample, then rewind
        sample = f.read(4096)
        f.seek(0)

        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = 'excel'

        reader = csv.reader(f, dialect)

        # Read first row to detect headers
        try:
            first_row = next(reader)
        except StopIteration:
            return points

        # Check if first row is a recognised header
        header = [h.strip().lower() for h in first_row]
        x_col, y_col = None, None

        for xname, yname in HEADER_PAIRS:
            if xname in header and yname in header:
                x_col = header.index(xname)
                y_col = header.index(yname)
                break

        if x_col is None:
            # Check if first row is numeric data (headerless file)
            try:
                float(first_row[0])
                float(first_row[1])
                # First row is data — parse it now, then continue
                x_col, y_col = 0, 1
                lng_val = float(first_row[0].strip())
                lat_val = float(first_row[1].strip())
                if math.isfinite(lng_val) and math.isfinite(lat_val):
                    points.append((lng_val, lat_val))
            except (ValueError, IndexError):
                # Non-numeric, non-recognised header — skip it
                x_col, y_col = 0, 1

        # Stream remaining rows from the same file handle & dialect
        min_cols = max(x_col, y_col) + 1
        for row in reader:
            if len(row) < min_cols:
                continue
            try:
                lng_val = float(row[x_col].strip())
                lat_val = float(row[y_col].strip())
            except (ValueError, OverflowError):
                continue
            if not (math.isfinite(lng_val) and math.isfinite(lat_val)):
                continue
            points.append((lng_val, lat_val))

    return points


def _parse_points_json(filepath):
    """Parse JSON file — array of [x, y] pairs or array of {x, y} objects."""
    import json

    with open(filepath, 'r') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("JSON file must contain an array of points")

    points = []
    for item in data:
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                lng_val = float(item[0])
                lat_val = float(item[1])
            elif isinstance(item, dict):
                # Try common key names
                lng_val = None
                lat_val = None
                for xk in ('x', 'lng', 'lon', 'long', 'longitude', 'easting'):
                    if xk in item:
                        lng_val = float(item[xk])
                        break
                for yk in ('y', 'lat', 'latitude', 'northing'):
                    if yk in item:
                        lat_val = float(item[yk])
                        break
                if lng_val is None or lat_val is None:
                    continue
            else:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        if not (math.isfinite(lng_val) and math.isfinite(lat_val)):
            continue
        points.append((lng_val, lat_val))

    return points


def _parse_points_geojson(filepath):
    """Parse GeoJSON FeatureCollection or array of Features with Point geometry."""
    import json

    with open(filepath, 'r') as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("GeoJSON file must be a JSON object")

    features = []
    if data.get('type') == 'FeatureCollection':
        features = data.get('features', [])
    elif data.get('type') == 'Feature':
        features = [data]
    else:
        raise ValueError("GeoJSON must be a FeatureCollection or Feature")

    points = []
    for feat in features:
        geom = feat.get('geometry') if isinstance(feat, dict) else None
        if not geom or not isinstance(geom, dict):
            continue
        if geom.get('type') != 'Point':
            continue
        coords = geom.get('coordinates')
        if not coords or len(coords) < 2:
            continue
        try:
            lng_val = float(coords[0])
            lat_val = float(coords[1])
        except (ValueError, TypeError, OverflowError):
            continue
        if not (math.isfinite(lng_val) and math.isfinite(lat_val)):
            continue
        points.append((lng_val, lat_val))

    return points


def load_data(filename, auto_bounds=True):
    """Load point data from a file and cache it in memory.

    Returns a list of (lng, lat) tuples.  Subsequent calls with the same
    filename return the cached list without re-reading the file.

    Supports multiple input formats, auto-detected from file extension:

    - ``.txt`` — Space-separated ``x y`` per line (original format)
    - ``.csv`` — CSV with headers (x/y, lng/lat, lon/lat, longitude/latitude)
    - ``.json`` — JSON array of ``[x, y]`` pairs or ``{"x": ..., "y": ...}`` objects
    - ``.geojson`` — GeoJSON FeatureCollection with Point geometries

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

    if filename in _file_cache:
        return _file_cache[filename]['points']

    # Strip leading "data/" or "data\" prefix if present, since
    # validate_input_path already joins with base_dir="data".
    # This prevents path doubling (data/data/file.txt) when users
    # pass "data/file.txt" instead of just "file.txt".  (Fixes #36)
    clean_name = filename
    for prefix in ("data/", "data\\"):
        if clean_name.startswith(prefix):
            clean_name = clean_name[len(prefix):]
            break

    # Validate path stays inside data/ directory
    resolved = validate_input_path(clean_name, base_dir="data")

    # Guard against oversized files that could exhaust memory (DoS)
    file_size = os.path.getsize(resolved)
    if file_size > MAX_INPUT_FILE_SIZE:
        raise ValueError(
            "Input file '%s' is %.1f MB, exceeding the %d MB limit. "
            "Increase vormap.MAX_INPUT_FILE_SIZE to override."
            % (filename, file_size / (1024 * 1024),
               MAX_INPUT_FILE_SIZE // (1024 * 1024))
        )

    # Auto-detect format and parse
    fmt = _detect_format(resolved)
    if fmt == 'csv':
        points = _parse_points_csv(resolved)
    elif fmt == 'json':
        points = _parse_points_json(resolved)
    elif fmt == 'geojson':
        points = _parse_points_geojson(resolved)
    else:
        points = _parse_points_txt(resolved)

    if not points:
        raise ValueError("No valid points found in '%s'" % filename)

    _check_point_limit(points, filename)

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

    tree = None
    if _HAS_SCIPY:
        tree = KDTree(np.array(points))

    _file_cache[filename] = {'points': points, 'tree': tree}

    # Register in the O(1) reverse-lookup cache so get_NN can find the
    # KDTree for a data list without scanning _file_cache.
    if tree is not None:
        _tree_by_data_id[id(points)] = tree

    return points


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
        # O(1) lookup via identity-keyed reverse cache.  Falls back to
        # the old linear scan only if the data list was created outside
        # load_data() (e.g. in tests).
        tree = _tree_by_data_id.get(id(data))
        if tree is None:
            for entry in _file_cache.values():
                if entry['points'] is data:
                    tree = entry['tree']
                    break

        if tree is None and len(data) >= 20:
            # Build a KDTree on-the-fly for datasets not loaded via
            # load_data() (e.g. constructed in tests or by extension
            # modules).  The O(n log n) build cost is amortized over
            # subsequent O(log n) queries — far better than repeated
            # O(n) brute-force scans.  Only worth it for non-trivial
            # datasets (>= 20 points).
            tree = KDTree(data)
            _tree_by_data_id[id(data)] = tree

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
        # Inlined eudist_sq to avoid function call overhead per data point.
        _dx = slng - lng
        _dy = slat - lat
        dsq = _dx * _dx + _dy * _dy
        if dsq > 0 and dsq <= mindist_sq:
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
    """Return the midpoint of two 2D points, rounded to 2 decimal places.

    Parameters
    ----------
    x1, y1 : float
        Coordinates of the first point.
    x2, y2 : float
        Coordinates of the second point.

    Returns
    -------
    tuple[float, float]
        The (x, y) midpoint.
    """
    return round(((float)(x1 + x2) / 2), 2), round(((float)(y1 + y2) / 2), 2)


def perp_dir(x1, y1, x2, y2):
    """Compute the slope of the perpendicular bisector of a line segment.

    Returns the negative reciprocal of the segment's slope. If the
    segment is horizontal (``y1 == y2``), returns ``math.inf``.

    Parameters
    ----------
    x1, y1 : float
        Coordinates of the first endpoint.
    x2, y2 : float
        Coordinates of the second endpoint.

    Returns
    -------
    float
        Slope of the perpendicular direction, rounded to 2 decimal places.
    """
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
    lines).  ``+inf`` and ``-inf`` are treated as *different* slopes
    because they represent opposite directions.  For finite slopes,
    uses a relative tolerance comparison to avoid the fragility of
    exact float equality or rounding.
    """
    if math.isinf(m1) and math.isinf(m2):
        # +inf vs -inf are different directions; only match same sign
        return (m1 > 0) == (m2 > 0)
    if math.isinf(m1) or math.isinf(m2):
        return False
    return abs(m1 - m2) <= rtol * max(abs(m1), abs(m2), 1.0)


def new_dir(data, aplng, aplat, alng, alat, dlng, dlat):
    """Find a new Voronoi edge direction by iterative angular refinement.

    Starting from the slope between points ``(alng, alat)`` and
    ``(dlng, dlat)``, rotates the search angle in progressively smaller
    increments to locate the Voronoi edge direction emanating from
    ``(aplng, aplat)``.  Uses binary search and collinearity checks
    to converge on the correct slope.

    Parameters
    ----------
    data : list
        Point dataset for nearest-neighbor lookups.
    aplng, aplat : float
        Anchor point (Voronoi vertex) coordinates.
    alng, alat : float
        Starting direction reference point.
    dlng, dlat : float
        Second direction reference point.

    Returns
    -------
    float
        Slope of the new Voronoi edge direction (``math.inf`` if vertical).
    """
    if (alng == dlng):
        m1 = math.inf
    else:
        m1 = float(alat - dlat) / (alng - dlng)

    a1 = math.atan(m1)
    tth = 0.5
    th = math.atan(tth)
    # Local references for hot-loop functions — avoids module-level
    # dict lookups on every iteration (~200 iterations typical).
    _isect_B = isect_B
    _find_CXY = find_CXY
    _bin_search = bin_search
    _collinear = collinear
    _math_tan = math.tan
    prev_c1 = None
    for _iter in range(NEW_DIR_MAX_ITER):
        # When th is below float64 meaningful precision the angular
        # difference between ac1 and ac2 vanishes — further iterations
        # only burn CPU without refining the result.
        if th < 1e-12:
            break

        ac1 = a1 + th
        ac2 = ac1 + th
        tac1 = _math_tan(ac1)
        tac2 = _math_tan(ac2)

        Bc1 = _isect_B(dlng, dlat, tac1)
        Bc1x, Bc1y = _find_CXY(Bc1, aplng, aplat)
        c1x, c1y = _bin_search(data, Bc1x, Bc1y, dlng, dlat, dlng, dlat)

        # If c1 is identical to the previous iteration's c1, the search
        # has converged — the boundary point isn't moving any more.
        if prev_c1 is not None and c1x == prev_c1[0] and c1y == prev_c1[1]:
            break
        prev_c1 = (c1x, c1y)

        Bc2 = _isect_B(dlng, dlat, tac2)
        Bc2x, Bc2y = _find_CXY(Bc2, aplng, aplat)

        # Skip the expensive second bin_search when the two boundary
        # query points are nearly identical — they'll produce the same
        # Voronoi boundary point, making the collinearity check trivially
        # true.  Threshold chosen to be well above BIN_PREC (1e-6).
        _dx = Bc1x - Bc2x
        _dy = Bc1y - Bc2y
        if _dx * _dx + _dy * _dy < 1e-8:
            break

        c2x, c2y = _bin_search(data, Bc2x, Bc2y, dlng, dlat, dlng, dlat)

        th /= 2
        if _collinear(alng, alat, c1x, c1y, c2x, c2y) is True:
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


def _between(a, b, v):
    """True if *v* lies between *a* and *b* (inclusive, either order)."""
    return (a >= v and b <= v) or (a <= v and b >= v)


def isect(x1, y1, x2, y2, x3, y3, x4, y4):
    """Find the intersection of segments (x1,y1)-(x2,y2) and (x3,y3)-(x4,y4).

    Returns the intersection point as ``(x, y)`` or ``(-1, -1)`` when
    the segments do not intersect (or are parallel/coincident).
    """
    if (x1 == x2 and x3 == x4):
        return -1, -1

    if (x1 == x2 and x3 != x4):
        m = float(y4 - y3) / (x4 - x3)
        y_test = m * (x1 - x3) + y3
        if _between(y1, y2, y_test) and _between(y3, y4, y_test):
            return x1, y_test
        return -1, -1

    if (x1 != x2 and x3 == x4):
        m = float(y2 - y1) / (x2 - x1)
        y_test = m * (x3 - x1) + y1
        if _between(y1, y2, y_test) and _between(y3, y4, y_test):
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

    if (_between(x1, x2, x_test) and _between(x3, x4, x_test)
            and _between(y1, y2, y_test1) and _between(y3, y4, y_test2)):
        return round(x_test, 2), round(y_test1, 2)
    return -1, -1


def isect_B(alng, alat, dirn):
    """Find two boundary intersection points for a line through (alng, alat).

    Returns a flat list ``[x1, y1, x2, y2]`` of the two intersection
    points with the search boundary ``[IND_W, IND_E] × [IND_S, IND_N]``.
    """
    if math.isinf(dirn):
        return [alng, IND_N, alng, IND_S]
    if dirn == 0:
        return [IND_W, alat, IND_E, alat]

    # Compute intersections with all four boundary edges
    xt = float(IND_N - alat) / dirn + alng  # top edge (y = IND_N)
    xb = float(IND_S - alat) / dirn + alng  # bottom edge (y = IND_S)
    yr = dirn * (IND_E - alng) + alat       # right edge (x = IND_E)
    yl = dirn * (IND_W - alng) + alat       # left edge (x = IND_W)

    # Collect points that actually lie on the boundary, deduplicating
    # corners where two edges share a vertex (e.g. (IND_W, IND_N) is
    # on both the top and left edges).  Without dedup, corner-passing
    # lines produce 6+ values → RuntimeError.  (Fixes #42)
    candidates = []
    if IND_W <= xt <= IND_E:
        candidates.append((xt, IND_N))
    if IND_W <= xb <= IND_E:
        candidates.append((xb, IND_S))
    if IND_S <= yl <= IND_N:
        candidates.append((IND_W, yl))
    if IND_S <= yr <= IND_N:
        candidates.append((IND_E, yr))

    # Deduplicate: two boundary edges can share a corner point
    seen = set()
    ret = []
    for pt in candidates:
        # Round to avoid floating-point near-duplicates at corners
        key = (round(pt[0], 10), round(pt[1], 10))
        if key not in seen:
            seen.add(key)
            ret.extend(pt)

    if len(ret) == 4:
        return ret

    # Single intersection (tangent to corner): duplicate it so callers
    # get the expected 4-element format
    if len(ret) == 2:
        return ret + ret

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

    bin_prec_sq = BIN_PREC * BIN_PREC
    # Local references avoid global/module-level lookups on every iteration.
    _get_NN = get_NN
    for _ in range(BIN_SEARCH_MAX_ITER):
        # Inlined eudist_sq for convergence check — saves a function call
        # per iteration (~100 iterations × thousands of bin_search calls).
        _dx = x1 - x2
        _dy = y1 - y2
        if _dx * _dx + _dy * _dy <= bin_prec_sq:
            break

        xm = (x1 + x2) * 0.5
        ym = (y1 + y2) * 0.5
        lg, lt = _get_NN(data, xm, ym)
        # Inlined eudist_sq for both distance computations.
        _dx1 = lg - xm
        _dy1 = lt - ym
        d1_sq = _dx1 * _dx1 + _dy1 * _dy1
        _dx2 = xm - dlng
        _dy2 = ym - dlat
        d2_sq = _dx2 * _dx2 + _dy2 * _dy2
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

    When numpy is available, uses vectorized cross-products for O(n)
    performance with low constant factor.  Falls back to a scalar loop
    otherwise.
    """
    n = len(alat)
    if n < 2:
        return 0.0

    # For large polygons (> 64 vertices), numpy vectorization wins.
    # For the typical Voronoi polygon (5-20 vertices), the overhead of
    # array construction, element-wise ops, and float conversion makes
    # numpy *slower* than a plain Python loop.  Benchmarked: scalar loop
    # is ~2-3x faster for n < 64 with CPython 3.10+.
    if _HAS_SCIPY and n > 64:
        x = np.asarray(alng)
        y = np.asarray(alat)
        y_shifted = np.empty_like(y)
        y_shifted[:-1] = y[1:]
        y_shifted[-1] = y[0]
        x_shifted = np.empty_like(x)
        x_shifted[:-1] = x[1:]
        x_shifted[-1] = x[0]
        area = np.abs(np.dot(x, y_shifted) - np.dot(y, x_shifted)) * 0.5
        return round(float(area), 2)

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

    # Cache find_area results keyed by data point — random samples
    # often map to the same nearest neighbor, and find_area is the
    # most expensive call (traces the full Voronoi region boundary).
    # Shared across retry attempts because the Voronoi diagram is
    # deterministic: same data point always produces the same region.
    # This avoids redundant O(k) boundary traces when retries resample
    # points that hit the same nearest neighbors.
    _area_cache = {}

    for attempt in range(MAX_RETRIES):
        Oracle.calls = 0
        max_edges = 0
        sum_edges = 0
        # Use running sums instead of collecting into a list — avoids
        # O(N1) list allocation and a second pass for the mean.
        sum_estimates = 0.0
        valid_count = 0

        # --- Batch nearest-neighbor lookup when scipy is available ---
        # Querying the KDTree with all N1 points at once is ~10-50x faster
        # than N1 individual Python-level calls due to numpy vectorisation
        # and reduced interpreter overhead.
        if _HAS_SCIPY:
            tree = _tree_by_data_id.get(id(data))
            if tree is None:
                for entry in _file_cache.values():
                    if entry['points'] is data:
                        tree = entry['tree']
                        break
            if tree is not None:
                sample_pts = np.column_stack([
                    np.random.uniform(IND_W, IND_E, N1),
                    np.random.uniform(IND_S, IND_N, N1),
                ])
                dists, idxs = tree.query(sample_pts, k=2)
                # Pick the first neighbor with dist > 0 (skip self-matches).
                # Vectorised selection replaces the Python for-loop for ~10x
                # speedup on large N1 values.
                data_arr = np.asarray(data)
                use_second = dists[:, 0] == 0
                chosen_idx = np.where(use_second, idxs[:, 1], idxs[:, 0])
                nn_coords = [tuple(row) for row in data_arr[chosen_idx]]
                Oracle.calls += N1
            else:
                nn_coords = None  # fall back to per-point loop
        else:
            nn_coords = None

        for i in range(N1):
            if nn_coords is not None:
                dlng, dlat = nn_coords[i]
            else:
                plng = random.uniform(IND_W, IND_E)
                plat = random.uniform(IND_S, IND_N)
                dlng, dlat = get_NN(data, plng, plat)

            cache_key = (dlng, dlat)
            if cache_key in _area_cache:
                area, v_edges = _area_cache[cache_key]
            else:
                area, v_edges = find_area(data, dlng, dlat)
                _area_cache[cache_key] = (area, v_edges)

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


# ── Extracted command handlers ─────────────────────────────────────
# Each handler encapsulates one logical CLI command that was previously
# inlined in main().  This reduces main() by ~300 lines and makes each
# command independently testable.


def _cmd_generate(args):
    """Handle --generate: create synthetic point patterns."""
    import vormap_generate

    gen_bounds = tuple(args.bounds) if args.bounds else (0, 1000, 0, 2000)

    gen_kwargs = {}
    if args.generate in ('clustered', 'mixed'):
        gen_kwargs['parents'] = args.generate_parents
        gen_kwargs['radius'] = args.generate_radius
    if args.generate == 'regular':
        gen_kwargs['jitter'] = args.generate_jitter
    if args.generate == 'inhibitory':
        gen_kwargs['min_dist'] = args.generate_min_dist
    if args.generate == 'gradient':
        gen_kwargs['direction'] = args.generate_direction
    if args.generate == 'mixed':
        gen_kwargs['cluster_fraction'] = args.generate_cluster_fraction

    points = vormap_generate.generate_pattern(
        args.generate, n=args.generate_n, bounds=gen_bounds,
        seed=args.generate_seed, **gen_kwargs)

    if args.generate_stats:
        summary = vormap_generate.pattern_summary(points, args.generate)
        print('Pattern: %s' % summary['pattern'])
        print('Points:  %d' % summary['count'])
        if summary.get('centroid'):
            print('Centroid: (%.2f, %.2f)' % summary['centroid'])
        if summary.get('spread'):
            print('Spread:  %.2f x %.2f' % summary['spread'])
        if summary.get('nni') is not None:
            nni = summary['nni']
            label = ('clustered' if nni < 0.8
                     else 'regular' if nni > 1.2
                     else 'random')
            print('NNI:     %.4f (%s)' % (nni, label))
        print()

    if args.generate_output:
        ext = args.generate_output.rsplit('.', 1)[-1].lower()
        if ext == 'csv':
            vormap_generate.export_csv(points, args.generate_output,
                                       allow_absolute=True)
        elif ext == 'json':
            vormap_generate.export_json(points, args.generate_output,
                                        allow_absolute=True)
        else:
            vormap_generate.export_txt(points, args.generate_output,
                                       allow_absolute=True)
        print('Generated %d %s points -> %s'
              % (len(points), args.generate, args.generate_output))
    else:
        for x, y in points:
            print('%.6f %.6f' % (x, y))


def _cmd_stats(args, regions, data):
    """Handle --stats / --stats-csv / --stats-json."""
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


def _cmd_cluster(args, regions, data):
    """Handle --cluster / --cluster-svg / --cluster-json."""
    import vormap_cluster
    import vormap_viz

    region_stats = vormap_viz.compute_region_stats(regions, data)

    # Parse value range if given
    value_range = None
    if args.cluster_range:
        parts = args.cluster_range.split(",")
        if len(parts) == 2:
            value_range = (float(parts[0]), float(parts[1]))

    cluster_result = vormap_cluster.cluster_regions(
        region_stats, regions, data,
        method=args.cluster_method,
        metric=args.cluster_metric,
        value_range=value_range,
        min_neighbors=args.cluster_min_neighbors,
        num_clusters=args.cluster_count,
    )
    print('Clustering: %d clusters (%s, %s)'
          % (cluster_result.num_clusters, args.cluster_method,
             args.cluster_metric))
    if cluster_result.num_noise > 0:
        print('  Noise cells: %d' % cluster_result.num_noise)

    if args.cluster:
        print()
        print(vormap_cluster.format_cluster_table(cluster_result))

    if args.cluster_json:
        vormap_cluster.export_cluster_json(
            cluster_result, args.cluster_json)
        print('Cluster JSON saved to %s' % args.cluster_json)

    if args.cluster_svg:
        vormap_cluster.export_cluster_svg(
            cluster_result, regions, data, args.cluster_svg,
            width=args.svg_width, height=args.svg_height,
            show_labels=args.cluster_labels,
            title='Spatial Clusters (%s, %s) — %s (%d points)'
                  % (args.cluster_method, args.cluster_metric,
                     args.datafile, len(data)),
        )
        print('Cluster SVG saved to %s' % args.cluster_svg)


def _cmd_edge_network(args, regions, data):
    """Handle --edge-network / --edge-csv / --edge-json / --edge-svg."""
    import vormap_edge

    network = vormap_edge.extract_edge_network(regions)
    stats = vormap_edge.compute_edge_stats(network)
    print('Edge network: %d vertices, %d edges (%.0f total length)'
          % (stats['num_vertices'], stats['num_edges'],
             stats['total_length']))

    if args.edge_network:
        print()
        print(vormap_edge.format_edge_stats(stats))

    if args.edge_csv:
        vormap_edge.export_edge_csv(network, args.edge_csv)
        print('Edge CSV saved to %s' % args.edge_csv)

    if args.edge_json:
        vormap_edge.export_edge_json(network, args.edge_json)
        print('Edge JSON saved to %s' % args.edge_json)

    if args.edge_svg:
        vormap_edge.export_edge_svg(
            network,
            args.edge_svg,
            width=args.svg_width,
            height=args.svg_height,
            title='Edge Network — %s (%d vertices, %d edges)'
                  % (args.datafile, stats['num_vertices'],
                     stats['num_edges']),
        )
        print('Edge network SVG saved to %s' % args.edge_svg)


def _cmd_kde(args, data):
    """Handle --kde-svg / --kde-csv / --kde-hotspots."""
    import vormap_kde

    points = [(p[0], p[1]) for p in data]
    grid = vormap_kde.kde_grid(
        points,
        nx=args.kde_nx,
        ny=args.kde_ny,
        bandwidth=args.kde_bandwidth,
        bandwidth_method=args.kde_bandwidth_method,
    )

    summary = vormap_kde.kde_summary(grid)
    print('KDE: bandwidth=%.4f  density_range=[%.2e, %.2e]  mass=%.4f'
          % (summary['bandwidth'], summary['density_min'],
             summary['density_max'], summary['total_mass']))

    if args.kde_svg:
        vormap_kde.export_kde_svg(
            grid,
            args.kde_svg,
            width=args.svg_width,
            height=args.svg_height,
            ramp=args.kde_ramp,
            show_hotspots=args.kde_show_hotspots,
            title='KDE Density — %s (%d points, h=%.2f)'
                  % (args.datafile, len(points), grid.bandwidth),
        )
        print('KDE SVG saved to %s' % args.kde_svg)

    if args.kde_csv:
        vormap_kde.export_kde_csv(grid, args.kde_csv)
        print('KDE CSV saved to %s' % args.kde_csv)

    if args.kde_hotspots:
        hotspots = vormap_kde.find_hotspots(grid)
        vormap_kde.export_hotspots_json(hotspots, args.kde_hotspots, grid)
        print('Found %d hotspots, saved to %s'
              % (len(hotspots), args.kde_hotspots))


def _cmd_autocorr(args, regions, data):
    """Handle --autocorr / --autocorr-json / --lisa-svg."""
    import vormap_autocorr
    import vormap_viz

    region_stats = vormap_viz.compute_region_stats(regions, data)
    ac_values = vormap_autocorr._extract_metric_values(
        region_stats, args.autocorr_metric)

    global_result = vormap_autocorr.global_morans_i(
        ac_values, regions, data, metric=args.autocorr_metric)

    if args.autocorr:
        print()
        print(vormap_autocorr.format_global_report(global_result))

    lisa_result = None
    if args.autocorr_json or args.lisa_svg:
        lisa_result = vormap_autocorr.local_morans_i(
            ac_values, regions, data,
            metric=args.autocorr_metric,
            permutations=args.lisa_permutations,
            significance=args.lisa_significance,
        )
        print()
        print(vormap_autocorr.format_lisa_summary(lisa_result))

    if args.autocorr_json:
        vormap_autocorr.export_autocorr_json(
            global_result, lisa_result, args.autocorr_json)
        print('Autocorrelation JSON saved to %s' % args.autocorr_json)

    if args.lisa_svg:
        vormap_autocorr.export_lisa_svg(
            lisa_result, regions, data, args.lisa_svg,
            width=args.svg_width, height=args.svg_height,
            title="LISA Cluster Map (%s) — %s (%d points)"
                  % (args.autocorr_metric, args.datafile, len(data)),
        )
        print('LISA SVG saved to %s' % args.lisa_svg)


def _cmd_hull(args, data):
    """Handle --hull / --hull-json / --hull-svg."""
    import vormap_hull

    # data is a list of (lng, lat) tuples from load_data()
    pts = list(data)
    analysis = vormap_hull.hull_analysis(pts)

    if args.hull:
        print(vormap_hull.format_report(analysis))

    if args.hull_json:
        vormap_hull.export_json(analysis, args.hull_json)
        print('Hull JSON saved to %s' % args.hull_json)

    if args.hull_svg:
        vormap_hull.export_svg(analysis, pts, args.hull_svg)
        print('Hull SVG saved to %s' % args.hull_svg)


def _cmd_visualize(args, regions, data):
    """Handle --visualize, --ascii, --interactive: render Voronoi diagrams."""
    if args.visualize:
        import vormap_viz
        vormap_viz.export_svg(
            regions, data, args.visualize,
            width=args.svg_width, height=args.svg_height,
            color_scheme=args.color_scheme, show_labels=args.show_labels,
            title='Voronoi Diagram — %s (%d points)' % (args.datafile, len(data)),
        )
        print('SVG saved to %s' % args.visualize)

    if args.ascii:
        import vormap_ascii
        vormap_ascii.render(
            regions, data,
            width=args.ascii_width, height=args.ascii_height,
            mono=args.ascii_mono,
        )

    if args.interactive:
        import vormap_viz
        vormap_viz.export_html(
            regions, data, args.interactive,
            width=args.svg_width, height=args.svg_height,
            color_scheme=args.color_scheme,
            title='Voronoi Diagram — %s (%d points)' % (args.datafile, len(data)),
        )
        print('Interactive HTML saved to %s' % args.interactive)


def _cmd_export_geo(args, regions, data):
    """Handle --geojson and --kml exports."""
    if args.geojson:
        import vormap_viz
        vormap_viz.export_geojson(
            regions, data, args.geojson,
            include_seeds=not args.no_seeds, crs_name=args.crs,
        )
        print('GeoJSON saved to %s' % args.geojson)

    if args.kml:
        import vormap_kml
        vormap_kml.export_kml(
            regions, data, args.kml,
            include_seeds=not args.no_seeds,
            color_scheme=args.color_scheme,
        )
        print('KML saved to %s' % args.kml)


def _cmd_relax_animate(args, data):
    """Handle --relax-animate: Lloyd relaxation animation."""
    if not args.relax_animate:
        return
    import vormap_viz
    original_data = load_data(args.datafile)
    iters = args.relax if args.relax else 10
    print('Generating relaxation animation (%d iterations)...' % iters)
    vormap_viz.export_relaxation_html(
        original_data, iterations=iters,
        output_path=args.relax_animate,
        width=args.svg_width, height=args.svg_height,
        color_scheme=args.color_scheme,
        title='Lloyd Relaxation — %s (%d points)' % (args.datafile, len(original_data)),
    )
    print('Relaxation animation saved to %s' % args.relax_animate)


def _cmd_graph(args, regions, data):
    """Handle --graph, --graph-json, --graph-csv, --graph-svg."""
    if not (args.graph or args.graph_json or args.graph_csv or args.graph_svg):
        return
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
            regions, data, graph, args.graph_svg,
            width=args.svg_width, height=args.svg_height,
            color_scheme=args.color_scheme,
            show_degree_labels=args.graph_labels,
            title='Neighbourhood Graph — %s (%d points, %d edges)'
                  % (args.datafile, len(data), graph['num_edges']),
        )
        print('Graph SVG saved to %s' % args.graph_svg)


def _cmd_heatmap(args, regions, data):
    """Handle --heatmap and --heatmap-html."""
    if not (args.heatmap or args.heatmap_html):
        return
    import vormap_heatmap
    heatmap_title = ('Voronoi Heatmap (%s) — %s (%d points)'
                     % (args.heatmap_metric, args.datafile, len(data)))
    if args.heatmap:
        vormap_heatmap.export_heatmap_svg(
            regions, data, args.heatmap,
            width=args.svg_width, height=args.svg_height,
            color_ramp=args.heatmap_ramp, metric=args.heatmap_metric,
            show_values=args.heatmap_values, title=heatmap_title,
        )
        print('Heatmap SVG saved to %s' % args.heatmap)
    if args.heatmap_html:
        vormap_heatmap.export_heatmap_html(
            regions, data, args.heatmap_html,
            width=args.svg_width, height=args.svg_height,
            color_ramp=args.heatmap_ramp, metric=args.heatmap_metric,
            title=heatmap_title,
        )
        print('Interactive heatmap HTML saved to %s' % args.heatmap_html)


def _cmd_pattern(args, data):
    """Handle --pattern and --pattern-json."""
    if not (args.pattern or args.pattern_json):
        return
    import vormap_pattern as vp
    bounds_tuple = None
    if args.bounds:
        s, n, w, e = args.bounds
        bounds_tuple = (w, e, s, n)
    summary = vp.analyze_pattern(data, bounds=bounds_tuple)
    if args.pattern:
        print(vp.format_pattern_report(summary))
    if args.pattern_json:
        import json
        result = vp.generate_pattern_json(summary)
        with open(args.pattern_json, 'w') as f:
            json.dump(result, f, indent=2)
        print('Pattern analysis JSON saved to %s' % args.pattern_json)


def _cmd_query(args, data, regions):
    """Handle --query and --query-batch."""
    if not (args.query or args.query_batch):
        return
    import vormap_query
    vormap_query.run_query_cli(args, data, regions)


def _cmd_interp(args, data):
    """Handle --interp-values and related interpolation flags."""
    if not args.interp_values:
        return
    import vormap_interp
    vormap_interp.run_interp_cli(args, data)


def _cmd_crossval(args, data, parser):
    """Handle --crossval, --crossval-csv, --crossval-svg."""
    if not (args.crossval or args.crossval_csv or args.crossval_svg):
        return
    if not args.interp_values:
        parser.error('--crossval requires --interp-values')
    import vormap_crossval
    vormap_crossval.run_crossval_cli(args, data)


def _cmd_regress(args, regions, data):
    """Handle --regress, --regress-gwr, and related flags."""
    regress_requested = (
        args.regress or args.regress_gwr or args.regress_svg
        or args.regress_json or args.regress_csv
    )
    if not regress_requested:
        return
    import vormap_regress
    vormap_regress.run_regress_cli(args, regions, data)


def _cmd_report(args, data, regions):
    """Handle --report: HTML analysis report."""
    if not args.report:
        return
    import vormap_report
    report_title = (
        args.report_title
        or 'Voronoi Analysis Report — %s (%d points)'
           % (args.datafile, len(data))
    )
    vormap_report.generate_report(
        data, regions,
        (IND_S, IND_N, IND_W, IND_E),
        args.report, title=report_title, allow_absolute=True,
    )
    print('HTML report saved to %s' % args.report)


def _cmd_centers(args, data):
    """Handle --centers and related flags."""
    centers_requested = (
        args.centers or args.centers_json or args.centers_csv
        or args.centers_svg
    )
    if not centers_requested:
        return
    import vormap_centroid
    # data is a list of (lng, lat) tuples from load_data()
    pts = list(data)
    cr = vormap_centroid.analyze_centers(
        pts, bounds=(IND_S, IND_N, IND_W, IND_E),
    )
    if args.centers:
        print(cr.summary())
    if args.centers_json:
        cr.to_json(args.centers_json)
        print('Centers JSON saved to %s' % args.centers_json)
    if args.centers_csv:
        cr.to_csv(args.centers_csv)
        print('Centers CSV saved to %s' % args.centers_csv)
    if args.centers_svg:
        cr.to_svg(args.centers_svg)
        print('Centers SVG saved to %s' % args.centers_svg)


def _cmd_buffers(args, data):
    """Handle --buffers and related flags."""
    buf_radius = args.buffers if args.buffers else args.buffer_radius
    buffers_requested = (
        args.buffers is not None or args.buffers_json
        or args.buffers_csv or args.buffers_svg
    )
    if not buffers_requested:
        return
    import vormap_buffer
    # data is a list of (lng, lat) tuples from load_data()
    pts = list(data)
    radii = None
    if args.buffer_rings:
        radii = [float(r.strip()) for r in args.buffer_rings.split(",")]
    br = vormap_buffer.analyze_buffers(pts, radius=buf_radius, radii=radii)
    if args.buffers is not None:
        vormap_buffer.print_buffer_report(br)
    if args.buffers_json:
        br.to_json(args.buffers_json)
        print('Buffer analysis JSON saved to %s' % args.buffers_json)
    if args.buffers_csv:
        br.to_csv(args.buffers_csv)
        print('Buffer analysis CSV saved to %s' % args.buffers_csv)
    if args.buffers_svg:
        br.to_svg(args.buffers_svg)
        print('Buffer analysis SVG saved to %s' % args.buffers_svg)


def _cmd_circlepack(args, regions, data):
    """Handle --circlepack, --circlepack-html, --circlepack-stats."""
    if not (args.circlepack or args.circlepack_html or args.circlepack_stats):
        return
    import vormap_circlepack
    packing = vormap_circlepack.circle_pack(regions)
    if args.circlepack_stats or (not args.circlepack and not args.circlepack_html):
        stats = vormap_circlepack.packing_stats(packing)
        print('Circle Packing Statistics')
        print('=' * 35)
        print('  Cells:            %d' % stats['total_cells'])
        print('  Mean Efficiency:  %.1f%%' % (stats.get('mean_efficiency', 0) * 100))
        print('  Min Efficiency:   %.1f%%' % (stats.get('min_efficiency', 0) * 100))
        print('  Max Efficiency:   %.1f%%' % (stats.get('max_efficiency', 0) * 100))
        print('  Overall:          %.1f%%' % (stats.get('overall_efficiency', 0) * 100))
    if args.circlepack:
        vormap_circlepack.export_svg(
            packing, regions, data, args.circlepack,
            fill_mode=args.circlepack_fill,
        )
        print('Circle packing SVG saved to %s' % args.circlepack)
    if args.circlepack_html:
        vormap_circlepack.export_html(packing, regions, data, args.circlepack_html)
        print('Circle packing HTML saved to %s' % args.circlepack_html)


def _build_parser():
    """Construct the argparse parser for the VoronoiMap CLI.

    Extracted from main() to keep the entry point focused on execution
    logic and to allow programmatic access to the parser (e.g. for
    generating documentation or testing argument parsing).
    """
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
        '--ascii',
        action='store_true',
        help='Render Voronoi diagram in the terminal using Unicode '
             'characters and ANSI colors. No file output needed.',
    )
    parser.add_argument(
        '--ascii-width',
        type=int,
        default=80,
        help='Terminal canvas width in columns (default: 80).',
    )
    parser.add_argument(
        '--ascii-height',
        type=int,
        default=24,
        help='Terminal canvas height in rows (default: 24).',
    )
    parser.add_argument(
        '--ascii-mono',
        action='store_true',
        help='Use monochrome box-drawing instead of ANSI colors.',
    )
    parser.add_argument(
        '--geojson',
        metavar='OUTPUT',
        help='Export Voronoi regions as GeoJSON for use in GIS tools '
             '(QGIS, Mapbox, Leaflet, Google Earth). Provide the output '
             'file path (e.g. diagram.geojson).',
    )
    parser.add_argument(
        '--kml',
        metavar='OUTPUT',
        help='Export Voronoi regions as KML for Google Earth. Provide '
             'the output file path (e.g. diagram.kml).',
    )
    parser.add_argument(
        '--no-seeds',
        action='store_true',
        help='When exporting GeoJSON/KML, omit seed points (include only region polygons).',
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

    # ── Heatmap arguments ──
    parser.add_argument(
        '--heatmap',
        metavar='OUTPUT',
        help='Export a density heatmap SVG — Voronoi cells colored by '
             'a spatial metric (density, area, compactness, vertices).',
    )
    parser.add_argument(
        '--heatmap-html',
        metavar='OUTPUT',
        help='Export an interactive HTML density heatmap with tooltips, '
             'metric switching, and color ramp controls.',
    )
    parser.add_argument(
        '--heatmap-metric',
        default='density',
        choices=['density', 'area', 'compactness', 'vertices'],
        help='Metric to visualize in the heatmap (default: density).',
    )
    parser.add_argument(
        '--heatmap-ramp',
        default='hot_cold',
        choices=['hot_cold', 'viridis', 'plasma'],
        help='Color ramp for the heatmap (default: hot_cold).',
    )
    parser.add_argument(
        '--heatmap-values',
        action='store_true',
        help='Show metric values as text labels inside each heatmap cell.',
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

    # ── Interpolation arguments ──
    parser.add_argument(
        '--interp-values',
        metavar='FILE',
        help='File with one scalar value per line (matching seed point order). '
             'Enables spatial interpolation commands.',
    )
    parser.add_argument(
        '--interp-query',
        metavar='X,Y',
        help='Interpolate value at a single point (e.g. --interp-query 300,400). '
             'Requires --interp-values.',
    )
    parser.add_argument(
        '--interp-method',
        default='natural',
        choices=['natural', 'idw', 'nearest'],
        help='Interpolation method (default: natural). '
             'natural=Sibson natural neighbor, idw=inverse distance weighting, '
             'nearest=nearest seed value.',
    )
    parser.add_argument(
        '--interp-surface-svg',
        metavar='OUTPUT',
        help='Export interpolated surface as an SVG heatmap grid.',
    )
    parser.add_argument(
        '--interp-surface-csv',
        metavar='OUTPUT',
        help='Export interpolated surface as a CSV grid.',
    )
    parser.add_argument(
        '--interp-nx',
        type=int, default=50,
        help='Grid columns for surface interpolation (default: 50).',
    )
    parser.add_argument(
        '--interp-ny',
        type=int, default=50,
        help='Grid rows for surface interpolation (default: 50).',
    )
    parser.add_argument(
        '--interp-power',
        type=float, default=2.0,
        help='Power parameter for IDW interpolation (default: 2.0).',
    )
    parser.add_argument(
        '--interp-ramp',
        default='viridis',
        choices=['viridis', 'plasma', 'hot_cold'],
        help='Color ramp for surface SVG (default: viridis).',
    )

    # ── Cross-validation arguments ──
    parser.add_argument(
        '--crossval',
        action='store_true',
        help='Run leave-one-out cross-validation for interpolation methods. '
             'Requires --interp-values.',
    )
    parser.add_argument(
        '--crossval-method',
        default=None,
        choices=['nearest', 'idw', 'natural'],
        help='Evaluate a single method (default: compare all three).',
    )
    parser.add_argument(
        '--crossval-csv',
        metavar='OUTPUT',
        help='Export cross-validation results to CSV.',
    )
    parser.add_argument(
        '--crossval-svg',
        metavar='OUTPUT',
        help='Export cross-validation comparison bar chart as SVG.',
    )

    # ── Spatial clustering arguments ──
    parser.add_argument(
        '--cluster',
        action='store_true',
        help='Run spatial clustering on Voronoi cells and print '
             'a summary table to stdout.',
    )
    parser.add_argument(
        '--cluster-svg',
        metavar='OUTPUT',
        help='Export spatial clustering results as an SVG visualization '
             'with distinct colors per cluster.',
    )
    parser.add_argument(
        '--cluster-json',
        metavar='OUTPUT',
        help='Export spatial clustering results as a JSON file.',
    )
    parser.add_argument(
        '--cluster-method',
        default='threshold',
        choices=['threshold', 'dbscan', 'agglomerative'],
        help='Clustering method (default: threshold). '
             'threshold=connected components within metric range, '
             'dbscan=density-based on adjacency graph, '
             'agglomerative=bottom-up merging by similarity.',
    )
    parser.add_argument(
        '--cluster-metric',
        default='area',
        choices=['area', 'density', 'compactness', 'vertices'],
        help='Cell metric for clustering (default: area).',
    )
    parser.add_argument(
        '--cluster-range',
        metavar='MIN,MAX',
        help='Value range for threshold method (e.g. --cluster-range 0,50000). '
             'Defaults to mean +/- 1 std dev.',
    )
    parser.add_argument(
        '--cluster-min-neighbors',
        type=int,
        default=2,
        help='Minimum adjacency degree for DBSCAN core cells (default: 2).',
    )
    parser.add_argument(
        '--cluster-count',
        type=int,
        default=3,
        help='Target number of clusters for agglomerative method (default: 3).',
    )
    parser.add_argument(
        '--cluster-labels',
        action='store_true',
        help='Show cluster ID labels inside each cell in the SVG.',
    )

    # ── Edge network arguments ──
    parser.add_argument(
        '--edge-network',
        action='store_true',
        help='Print edge network statistics (vertices, edges, lengths, '
             'connectivity, orientation entropy) to stdout.',
    )
    parser.add_argument(
        '--edge-csv',
        metavar='OUTPUT',
        help='Export the Voronoi edge network as a CSV edge list with '
             'vertex coordinates, lengths, angles, and boundary flags.',
    )
    parser.add_argument(
        '--edge-json',
        metavar='OUTPUT',
        help='Export the Voronoi edge network as a JSON file with '
             'vertices, edges, and aggregate statistics.',
    )
    parser.add_argument(
        '--edge-svg',
        metavar='OUTPUT',
        help='Export an SVG visualization of the Voronoi edge network '
             'with interior/boundary edges, junctions, and dead ends '
             'color-coded.',
    )

    # ── KDE arguments ──
    parser.add_argument(
        '--kde-svg',
        metavar='OUTPUT',
        help='Export a kernel density estimation (KDE) surface as an '
             'SVG heatmap. Produces a smooth density surface from '
             'point locations using Gaussian kernels.',
    )
    parser.add_argument(
        '--kde-csv',
        metavar='OUTPUT',
        help='Export the KDE density grid as a CSV file with '
             'x, y, density columns.',
    )
    parser.add_argument(
        '--kde-bandwidth',
        type=float,
        default=None,
        help='Explicit KDE bandwidth (Gaussian kernel width). '
             'If omitted, uses Silverman\'s rule-of-thumb.',
    )
    parser.add_argument(
        '--kde-bandwidth-method',
        choices=['silverman', 'scott'],
        default='silverman',
        help='Bandwidth selection method when --kde-bandwidth is not '
             'specified (default: silverman).',
    )
    parser.add_argument(
        '--kde-nx',
        type=int,
        default=50,
        help='KDE grid columns (default: 50).',
    )
    parser.add_argument(
        '--kde-ny',
        type=int,
        default=50,
        help='KDE grid rows (default: 50).',
    )
    parser.add_argument(
        '--kde-ramp',
        choices=['viridis', 'plasma', 'hot_cold'],
        default='viridis',
        help='Color ramp for KDE SVG (default: viridis).',
    )
    parser.add_argument(
        '--kde-hotspots',
        metavar='OUTPUT',
        help='Detect density hotspots and export as JSON.',
    )
    parser.add_argument(
        '--kde-show-hotspots',
        action='store_true',
        help='Mark detected hotspots on the KDE SVG output.',
    )

    # ── Spatial autocorrelation (Moran's I) ──────────────────────────
    parser.add_argument(
        '--autocorr',
        action='store_true',
        help="Compute global Moran's I spatial autocorrelation test.",
    )
    parser.add_argument(
        '--autocorr-metric',
        default='area',
        help="Metric for autocorrelation analysis: area, density, "
             "compactness, perimeter (default: area).",
    )
    parser.add_argument(
        '--autocorr-json',
        metavar='OUTPUT',
        help="Export autocorrelation results (global + LISA) as JSON.",
    )
    parser.add_argument(
        '--lisa-svg',
        metavar='OUTPUT',
        help='Export LISA cluster map as SVG (HH/LL/HL/LH/NS).',
    )
    parser.add_argument(
        '--lisa-permutations',
        type=int,
        default=999,
        help='Number of permutations for LISA pseudo p-values (default: 999).',
    )
    parser.add_argument(
        '--lisa-significance',
        type=float,
        default=0.05,
        help='Significance level for LISA cluster classification (default: 0.05).',
    )

    # ── Convex hull & bounding geometry ──────────────────────────────
    parser.add_argument(
        '--hull',
        action='store_true',
        help='Print convex hull and bounding geometry analysis '
             '(hull area/perimeter, MBR, MBC, shape metrics).',
    )
    parser.add_argument(
        '--hull-json',
        metavar='OUTPUT',
        help='Export hull analysis as JSON.',
    )
    parser.add_argument(
        '--hull-svg',
        metavar='OUTPUT',
        help='Export hull visualization as SVG (hull polygon, '
             'minimum bounding rectangle, minimum bounding circle, '
             'diameter line, centroid).',
    )

    # ── Spatial regression ──────────────────────────────────────────
    parser.add_argument(
        '--regress',
        metavar='FORMULA',
        help='Fit OLS spatial regression. Formula: y~x1+x2 '
             '(e.g. area~compactness+vertex_count).',
    )
    parser.add_argument(
        '--regress-gwr',
        metavar='FORMULA',
        help='Fit Geographically Weighted Regression. Same formula syntax.',
    )
    parser.add_argument(
        '--bandwidth',
        type=float,
        default=None,
        help='GWR bandwidth (distance). Auto-computed if omitted.',
    )
    parser.add_argument(
        '--kernel',
        choices=['gaussian', 'bisquare'],
        default='gaussian',
        help='GWR kernel function (default: gaussian).',
    )
    parser.add_argument(
        '--regress-svg',
        metavar='OUTPUT',
        help='Export regression residual map as SVG.',
    )
    parser.add_argument(
        '--regress-json',
        metavar='OUTPUT',
        help='Export regression results as JSON.',
    )
    parser.add_argument(
        '--regress-csv',
        metavar='OUTPUT',
        help='Export per-observation regression data as CSV.',
    )
    parser.add_argument(
        '--regress-show',
        default='residuals',
        help='SVG display: residuals, fitted, cooks_d, local_r2, or a '
             'coefficient name (default: residuals).',
    )

    # ── Spatial center analysis ─────────────────────────────────────
    parser.add_argument(
        '--centers',
        action='store_true',
        help='Print spatial center analysis (mean/median/weighted '
             'center, standard distance, deviational ellipse).',
    )
    parser.add_argument(
        '--centers-json',
        metavar='OUTPUT',
        help='Export center analysis as JSON.',
    )
    parser.add_argument(
        '--centers-csv',
        metavar='OUTPUT',
        help='Export center analysis as CSV.',
    )
    parser.add_argument(
        '--centers-svg',
        metavar='OUTPUT',
        help='Export center analysis visualization as SVG '
             '(centers, standard distance circle, deviational ellipse).',
    )

    # ── Buffer zone analysis ───────────────────────────────────────
    parser.add_argument(
        '--buffers',
        metavar='RADIUS',
        type=float,
        default=None,
        help='Run buffer zone analysis with the given radius. '
             'Prints overlap, containment, and coverage statistics.',
    )
    parser.add_argument(
        '--buffers-json',
        metavar='OUTPUT',
        help='Export buffer zone analysis as JSON.',
    )
    parser.add_argument(
        '--buffers-csv',
        metavar='OUTPUT',
        help='Export buffer zone analysis as CSV.',
    )
    parser.add_argument(
        '--buffers-svg',
        metavar='OUTPUT',
        help='Export buffer zone analysis visualization as SVG.',
    )
    parser.add_argument(
        '--buffer-radius',
        metavar='RADIUS',
        type=float,
        default=50.0,
        help='Buffer radius for --buffers-json/csv/svg (default: 50).',
    )
    parser.add_argument(
        '--buffer-rings',
        metavar='RADII',
        default=None,
        help='Comma-separated radii for multi-ring buffer analysis '
             '(e.g. 25,50,100).',
    )

    # ── HTML analysis report ────────────────────────────────────────
    parser.add_argument(
        '--report',
        metavar='OUTPUT',
        help='Generate a self-contained HTML analysis report combining '
             'summary statistics, density heatmap, area distribution, '
             'degree distribution, and per-region details table. '
             'Provide the output file path (e.g. report.html).',
    )
    parser.add_argument(
        '--report-title',
        default=None,
        metavar='TITLE',
        help='Custom title for the HTML report (default: auto-generated).',
    )

    # ── Synthetic point pattern generator ──────────────────────────────
    parser.add_argument(
        '--generate',
        metavar='PATTERN',
        choices=['poisson', 'clustered', 'regular', 'inhibitory',
                 'gradient', 'mixed'],
        help='Generate a synthetic point dataset instead of loading '
             'from a file. Writes to --generate-output (or stdout). '
             'Patterns: poisson, clustered, regular, inhibitory, '
             'gradient, mixed.',
    )
    parser.add_argument(
        '--generate-n',
        type=int,
        default=200,
        help='Number of points to generate (default: 200).',
    )
    parser.add_argument(
        '--generate-output',
        metavar='OUTPUT',
        help='Output file for generated points (default: stdout). '
             'Format detected from extension (.csv, .json, .txt).',
    )
    parser.add_argument(
        '--generate-seed',
        type=int,
        default=None,
        help='Random seed for reproducible generation.',
    )
    parser.add_argument(
        '--generate-parents',
        type=int,
        default=None,
        help='Number of cluster centres (clustered/mixed patterns).',
    )
    parser.add_argument(
        '--generate-radius',
        type=float,
        default=50.0,
        help='Cluster scatter radius (clustered/mixed, default: 50).',
    )
    parser.add_argument(
        '--generate-jitter',
        type=float,
        default=0.15,
        help='Jitter fraction for regular grid (0-1, default: 0.15).',
    )
    parser.add_argument(
        '--generate-min-dist',
        type=float,
        default=None,
        help='Minimum inter-point distance for inhibitory process.',
    )
    parser.add_argument(
        '--generate-direction',
        default='horizontal',
        choices=['horizontal', 'vertical', 'diagonal'],
        help='Gradient direction (default: horizontal).',
    )
    parser.add_argument(
        '--generate-cluster-fraction',
        type=float,
        default=0.5,
        help='Fraction of clustered points for mixed pattern (default: 0.5).',
    )
    parser.add_argument(
        '--generate-stats',
        action='store_true',
        help='Print summary statistics (centroid, spread, NNI) for '
             'the generated pattern.',
    )

    # ── Circle packing arguments ─────────────────────────────────────
    parser.add_argument(
        '--circlepack',
        metavar='OUTPUT',
        help='Export a circle packing SVG — largest inscribed circles '
             'per Voronoi cell with efficiency heatmap.',
    )
    parser.add_argument(
        '--circlepack-html',
        metavar='OUTPUT',
        help='Export an interactive HTML circle packing visualization '
             'with hover tooltips and packing statistics.',
    )
    parser.add_argument(
        '--circlepack-fill',
        default='heatmap',
        choices=['heatmap', 'pastel', 'solid'],
        help='Fill mode for circle packing SVG (default: heatmap).',
    )
    parser.add_argument(
        '--circlepack-stats',
        action='store_true',
        help='Print circle packing efficiency statistics to stdout.',
    )

    return parser


def main():
    """CLI entry point for VoronoiMap estimation."""
    parser = _build_parser()
    args = parser.parse_args()

    # ── Handle --generate before normal flow ─────────────────────────
    if args.generate:
        _cmd_generate(args)
        return

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
    # Commands that need Voronoi region computation (expensive)
    needs_regions = any([
        args.visualize, args.interactive, args.geojson, args.kml,
        args.stats, args.stats_csv, args.stats_json,
        args.relax_animate,
        args.graph, args.graph_json, args.graph_csv, args.graph_svg,
        args.query, args.query_batch,
        args.heatmap, args.heatmap_html,
        args.interp_values,
        args.edge_network, args.edge_csv, args.edge_json, args.edge_svg,
        args.cluster, args.cluster_svg, args.cluster_json,
        args.autocorr, args.autocorr_json, args.lisa_svg,
        args.report,
        args.crossval, args.crossval_csv, args.crossval_svg,
        args.ascii,
        args.circlepack, args.circlepack_html, args.circlepack_stats,
    ])

    # Commands that need data loaded but not full region computation
    needs_data_only = any([
        args.hull, args.hull_json, args.hull_svg,
        args.centers, args.centers_json, args.centers_csv, args.centers_svg,
        args.buffers is not None, args.buffers_json,
        args.buffers_csv, args.buffers_svg,
        args.kde_svg, args.kde_csv, args.kde_hotspots,
        args.pattern, args.pattern_json,
    ])

    data = None
    regions = None

    # Load data for data-only commands even when regions aren't needed
    if needs_data_only and not needs_regions:
        data = load_data(args.datafile)

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

    # Visualization outputs
    _cmd_visualize(args, regions, data)

    # GeoJSON / KML export
    _cmd_export_geo(args, regions, data)

    # Region statistics
    if args.stats or args.stats_csv or args.stats_json:
        _cmd_stats(args, regions, data)

    # Lloyd relaxation animation
    _cmd_relax_animate(args, data)

    # Neighbourhood graph
    _cmd_graph(args, regions, data)

    # Density heatmap
    _cmd_heatmap(args, regions, data)

    # Point pattern analysis
    _cmd_pattern(args, data)

    # Point location & nearest-neighbor query
    _cmd_query(args, data, regions)

    # Spatial interpolation
    _cmd_interp(args, data)

    # Cross-validation
    _cmd_crossval(args, data, parser)

    # Spatial clustering
    if args.cluster or args.cluster_svg or args.cluster_json:
        _cmd_cluster(args, regions, data)

    # Edge network analysis
    if args.edge_network or args.edge_csv or args.edge_json or args.edge_svg:
        _cmd_edge_network(args, regions, data)

    # Kernel Density Estimation
    kde_requested = (args.kde_svg or args.kde_csv or args.kde_hotspots)
    if kde_requested:
        _cmd_kde(args, data)

    # Spatial autocorrelation (Moran's I)
    autocorr_requested = (
        args.autocorr or args.autocorr_json or args.lisa_svg
    )
    if autocorr_requested:
        _cmd_autocorr(args, regions, data)

    # Spatial regression
    _cmd_regress(args, regions, data)

    # HTML analysis report
    _cmd_report(args, data, regions)

    # Spatial center analysis
    _cmd_centers(args, data)

    # Convex hull & bounding geometry
    if args.hull or args.hull_json or args.hull_svg:
        _cmd_hull(args, data)

    # Buffer zone analysis
    _cmd_buffers(args, data)

    # Circle packing
    _cmd_circlepack(args, regions, data)


if __name__ == '__main__':
    main()
