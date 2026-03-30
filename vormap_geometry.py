"""Shared geometry and statistics helpers used across VoronoiMap extension modules.

Extracted to eliminate copy-pasted implementations of the Shoelace formula,
polygon perimeter, centroid, and related computations that were duplicated
in 4+ modules.

Statistics helpers (``mean``, ``std``, ``percentile``, ``normal_cdf``) were
likewise copied into vormap_nndist, vormap_outlier, vormap_autocorr, and
vormap_pattern.  They now live here as the single source of truth.
"""

import math

from vormap_utils import polygon_centroid  # noqa: F401
from vormap_utils import euclidean  # noqa: F401


def polygon_area(vertices):
    """Compute the area of a polygon using the Shoelace formula.

    Unlike ``vormap.polygon_area(alng, alat)`` which rounds to 2 decimal
    places (suitable for its geographic use-case), this version returns
    full-precision results for geometric analysis.

    Parameters
    ----------
    vertices : list[tuple[float, float]]
        Ordered polygon vertices.

    Returns
    -------
    float
        Absolute area of the polygon. Returns 0.0 for fewer than 3 vertices.
    """
    n = len(vertices)
    if n < 3:
        return 0.0
    area = vertices[-1][1] * vertices[0][0] - vertices[0][1] * vertices[-1][0]
    for i in range(n - 1):
        area += vertices[i][1] * vertices[i + 1][0] - vertices[i + 1][1] * vertices[i][0]
    return abs(area) * 0.5


def polygon_perimeter(vertices):
    """Compute the perimeter of a polygon.

    Parameters
    ----------
    vertices : list[tuple[float, float]]
        Ordered polygon vertices.

    Returns
    -------
    float
        Sum of edge lengths. Returns 0.0 for fewer than 2 vertices.
    """
    n = len(vertices)
    if n < 2:
        return 0.0
    p = 0.0
    for i in range(n):
        j = (i + 1) % n
        dx = vertices[j][0] - vertices[i][0]
        dy = vertices[j][1] - vertices[i][1]
        p += math.sqrt(dx * dx + dy * dy)
    return p


# polygon_centroid is imported from vormap_utils above


def isoperimetric_quotient(area, perimeter):
    """Compute the isoperimetric quotient (circularity measure).

    IQ = 4 * pi * area / perimeter^2

    A perfect circle has IQ = 1.0. Lower values indicate less compact shapes.

    Parameters
    ----------
    area : float
    perimeter : float

    Returns
    -------
    float
        Value in [0, 1]. Returns 0.0 if perimeter is zero.
    """
    if perimeter < 1e-12:
        return 0.0
    return 4.0 * math.pi * area / (perimeter * perimeter)


def edge_length(v1, v2):
    """Euclidean distance between two 2D vertices.

    Parameters
    ----------
    v1, v2 : tuple[float, float]

    Returns
    -------
    float
    """
    dx = v2[0] - v1[0]
    dy = v2[1] - v1[1]
    return math.sqrt(dx * dx + dy * dy)


def build_data_index(data):
    """Build a coordinate-to-index lookup for seed point data.

    Only records the *first* occurrence of each seed coordinate, matching
    the semantics of the previous ``list.index()`` approach.  Building
    the dict is O(n); each subsequent lookup is O(1).

    Parameters
    ----------
    data : list[tuple[float, float]]
        Seed points.

    Returns
    -------
    dict[tuple, int]
        Maps coordinate tuples to their index in the data list.
    """
    lookup = {}
    for i, pt in enumerate(data):
        key = tuple(pt)
        if key not in lookup:
            lookup[key] = i
    return lookup


# ── Statistics helpers ─────────────────────────────────────────────


def mean(values):
    """Arithmetic mean, returning 0.0 for empty sequences.

    Parameters
    ----------
    values : list[float]

    Returns
    -------
    float
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


def std(values, population=True, mean_val=None):
    """Standard deviation.

    Parameters
    ----------
    values : list[float]
    population : bool
        If True (default) use population (N) denominator.
        If False use sample (N-1) denominator.
    mean_val : float or None
        Pre-computed mean to avoid redundant computation. When None the
        mean is calculated internally.

    Returns
    -------
    float
        Returns 0.0 when fewer than 2 values are provided.
    """
    n = len(values)
    if n < 2:
        return 0.0
    m = mean_val if mean_val is not None else mean(values)
    denom = n if population else (n - 1)
    return math.sqrt(sum((v - m) ** 2 for v in values) / denom)


def median(values):
    """Median of a list of numbers.

    Parameters
    ----------
    values : list[float]

    Returns
    -------
    float
        Median value, or 0.0 for empty input.
    """
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def cross_product_2d(o, a, b):
    """2D cross product of vectors OA and OB.

    Positive result means counter-clockwise turn from OA to OB.

    Parameters
    ----------
    o, a, b : tuple[float, float]
        Three 2D points.

    Returns
    -------
    float
    """
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def percentile(sorted_vals, p):
    """Compute the *p*-th percentile (0–100) via linear interpolation.

    Parameters
    ----------
    sorted_vals : list[float]
        **Must** be pre-sorted in ascending order.
    p : float
        Percentile in [0, 100].

    Returns
    -------
    float
    """
    if not sorted_vals:
        return 0.0
    idx = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def normal_cdf(x):
    """Approximate the standard normal CDF.

    Uses the Abramowitz & Stegun 7.1.26 rational approximation, accurate
    to ~1.5 × 10⁻⁷ for all *x*.

    Parameters
    ----------
    x : float

    Returns
    -------
    float
        Cumulative probability Φ(x).
    """
    if x < 0:
        return 1.0 - normal_cdf(-x)
    p = 0.2316419
    b1 = 0.319381530
    b2 = -0.356563782
    b3 = 1.781477937
    b4 = -1.821255978
    b5 = 1.330274429
    t = 1.0 / (1.0 + p * x)
    pdf = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    poly = t * (b1 + t * (b2 + t * (b3 + t * (b4 + t * b5))))
    return 1.0 - pdf * poly


# ── SVG coordinate transform ────────────────────────────────────────


class SVGCoordinateTransform:
    """Map data-space coordinates to SVG pixel-space.

    Provides ``tx(x)`` and ``ty(y)`` methods that 14+ VoronoiMap modules
    previously defined as inline closures.  Centralising the transform
    eliminates ~28 duplicated closure definitions and keeps scaling
    behaviour consistent.

    Supports two scaling modes:

    * **uniform** (default): aspect-preserving with centring, matching
      the ``_CoordinateTransform`` class in ``vormap_viz``.
    * **stretch**: independent x/y scaling to fill the canvas, matching
      the inline pattern used in ``vormap_edge``, ``vormap_outlier``, etc.

    Parameters
    ----------
    x_bounds : tuple[float, float]
        (min_x, max_x) of the data.
    y_bounds : tuple[float, float]
        (min_y, max_y) of the data.
    width : int or float
        SVG canvas width in pixels.
    height : int or float
        SVG canvas height in pixels.
    margin : int or float
        Pixel margin around the drawable area (default 40).
    mode : str
        ``"uniform"`` for aspect-preserving or ``"stretch"`` for
        independent x/y scaling.
    pad_fraction : float
        Fraction of data range to add as padding before computing the
        transform (default 0).  Applied equally on all sides.  Useful
        for the stretch pattern where modules typically pad by 5%.
    """

    __slots__ = ("_min_x", "_max_y", "_scale", "_scale_x", "_scale_y",
                 "_offset_x", "_offset_y", "_mode", "_width", "_height",
                 "_margin")

    def __init__(self, x_bounds, y_bounds, width, height, *,
                 margin=40, mode="uniform", pad_fraction=0.0):
        min_x, max_x = x_bounds
        min_y, max_y = y_bounds

        # Apply optional padding
        if pad_fraction > 0:
            pad_x = (max_x - min_x) * pad_fraction
            pad_y = (max_y - min_y) * pad_fraction
            if pad_x == 0:
                pad_x = 10.0
            if pad_y == 0:
                pad_y = 10.0
            min_x -= pad_x
            max_x += pad_x
            min_y -= pad_y
            max_y += pad_y

        range_x = max(max_x - min_x, 1e-6)
        range_y = max(max_y - min_y, 1e-6)

        self._min_x = min_x
        self._max_y = max_y
        self._width = width
        self._height = height
        self._margin = margin
        self._mode = mode

        draw_w = width - 2 * margin
        draw_h = height - 2 * margin

        if mode == "stretch":
            self._scale_x = draw_w / range_x if draw_w > 0 else 1.0
            self._scale_y = draw_h / range_y if draw_h > 0 else 1.0
            self._offset_x = margin
            self._offset_y = margin
            self._scale = min(self._scale_x, self._scale_y)
        else:
            scale = min(draw_w / range_x, draw_h / range_y)
            self._scale = scale
            self._scale_x = scale
            self._scale_y = scale
            self._offset_x = margin + (draw_w - range_x * scale) / 2
            self._offset_y = margin + (draw_h - range_y * scale) / 2

    def tx(self, x):
        """Transform a data-space X to pixel-space X."""
        if self._mode == "stretch":
            return self._offset_x + (x - self._min_x) * self._scale_x
        return self._offset_x + (x - self._min_x) * self._scale

    def ty(self, y):
        """Transform a data-space Y to pixel-space Y (Y-flipped for SVG)."""
        if self._mode == "stretch":
            return self._offset_y + (self._max_y - y) * self._scale_y
        return self._offset_y + (self._max_y - y) * self._scale

    @classmethod
    def from_points(cls, points, width, height, **kwargs):
        """Convenience constructor from a list of (x, y) points.

        Parameters
        ----------
        points : list[tuple[float, float]]
            Seed points or any coordinate data.
        width, height : int or float
            SVG canvas dimensions.
        **kwargs
            Forwarded to ``__init__`` (margin, mode, pad_fraction).
        """
        if not points:
            raise ValueError("Cannot create transform from empty point list")
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return cls((min(xs), max(xs)), (min(ys), max(ys)),
                   width, height, **kwargs)

    @property
    def scale(self):
        """Uniform scale factor (aspect-preserving modes).

        For ``stretch`` mode, returns ``min(scale_x, scale_y)`` which can
        be used as a reasonable approximation for radii and other isotropic
        quantities.
        """
        return self._scale
