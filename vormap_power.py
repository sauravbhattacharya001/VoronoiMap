"""Weighted (Power) Voronoi Diagrams.

Extends standard Voronoi diagrams by assigning weights to seed points.
In a power diagram, the cell boundary between sites *i* and *j* is
determined by the *power distance*: ``d(p, s_i)^2 - w_i`` rather than
just ``d(p, s_i)``.  Sites with larger weights claim more territory.

Three weighting modes are supported:

- **power** (default): Power distance ``d² - w``.  Weights represent
  squared radii; larger weight → larger cell.
- **multiplicative**: Weighted distance ``d / w``.  Weights act as
  scaling factors; larger weight → larger cell.
- **additive**: Weighted distance ``d - w``.  Weights are subtracted
  from Euclidean distance.

Key functions:

- ``compute_weighted_nn()``: Find nearest site using weighted distance.
- ``compute_power_regions()``: Build Voronoi regions with weighted seeds.
- ``compute_power_diagram()``: One-call convenience returning full result.
- ``assign_weights()``: Generate weight vectors (uniform, random,
  proportional, inverse, gaussian, linear_gradient).
- ``weight_effect_analysis()``: Compare weighted vs unweighted diagrams.
- ``export_power_json()``: Export full diagram with weight metadata.
- ``export_power_svg()``: SVG visualization with weight-proportional markers.

Example::

    from vormap_power import compute_power_diagram, assign_weights

    seeds = [(100, 200), (300, 400), (500, 100), (700, 600)]
    weights = [10, 50, 30, 20]
    result = compute_power_diagram(seeds, weights)
    print(result.summary())
"""

import math
import json
import random as _random


# ---------------------------------------------------------------------------
# Weight assignment helpers
# ---------------------------------------------------------------------------

def assign_weights(seeds, method='uniform', *, value=1.0, min_w=0.1,
                   max_w=10.0, seed=None, center=None, sigma=None,
                   direction='x'):
    """Generate a weight vector for *seeds*.

    Parameters
    ----------
    seeds : list[tuple[float, float]]
        Seed point coordinates.
    method : str
        One of ``'uniform'``, ``'random'``, ``'proportional'``,
        ``'inverse'``, ``'gaussian'``, ``'linear_gradient'``.
    value : float
        Weight value for ``'uniform'`` method.
    min_w, max_w : float
        Range for ``'random'`` method.
    seed : int, optional
        Random seed for reproducibility.
    center : tuple[float, float], optional
        Center point for ``'gaussian'`` and ``'proportional'`` methods.
        Defaults to centroid of seeds.
    sigma : float, optional
        Standard deviation for ``'gaussian'`` method.
        Defaults to half the diagonal of the bounding box.
    direction : str
        Axis for ``'linear_gradient'``: ``'x'``, ``'y'``, or ``'diagonal'``.

    Returns
    -------
    list[float]
        Weight vector with one entry per seed.

    Raises
    ------
    ValueError
        If *seeds* is empty or *method* is unknown.
    """
    if not seeds:
        raise ValueError("seeds must not be empty")
    n = len(seeds)

    if method == 'uniform':
        if value <= 0:
            raise ValueError("uniform weight value must be positive")
        return [float(value)] * n

    if method == 'random':
        if min_w <= 0 or max_w <= 0:
            raise ValueError("random weight bounds must be positive")
        if min_w > max_w:
            raise ValueError("min_w must be <= max_w")
        rng = _random.Random(seed)
        return [rng.uniform(min_w, max_w) for _ in range(n)]

    # Compute centroid if needed
    if center is None:
        cx = sum(s[0] for s in seeds) / n
        cy = sum(s[1] for s in seeds) / n
        center = (cx, cy)

    if method == 'proportional':
        # Weight proportional to distance from center
        dists = [math.sqrt((s[0] - center[0]) ** 2 +
                           (s[1] - center[1]) ** 2) for s in seeds]
        max_d = max(dists) if max(dists) > 0 else 1.0
        return [max(min_w, (d / max_d) * max_w) for d in dists]

    if method == 'inverse':
        # Weight inversely proportional to distance from center
        dists = [math.sqrt((s[0] - center[0]) ** 2 +
                           (s[1] - center[1]) ** 2) for s in seeds]
        max_d = max(dists) if max(dists) > 0 else 1.0
        return [max(min_w, (1.0 - d / max_d) * max_w) for d in dists]

    if method == 'gaussian':
        # Weight follows a Gaussian centered at *center*
        if sigma is None:
            xs = [s[0] for s in seeds]
            ys = [s[1] for s in seeds]
            diag = math.sqrt((max(xs) - min(xs)) ** 2 +
                             (max(ys) - min(ys)) ** 2)
            sigma = max(diag / 2.0, 1.0)
        weights = []
        for s in seeds:
            dsq = (s[0] - center[0]) ** 2 + (s[1] - center[1]) ** 2
            w = max_w * math.exp(-dsq / (2.0 * sigma * sigma))
            weights.append(max(min_w, w))
        return weights

    if method == 'linear_gradient':
        if direction not in ('x', 'y', 'diagonal'):
            raise ValueError("direction must be 'x', 'y', or 'diagonal'")
        if direction == 'x':
            vals = [s[0] for s in seeds]
        elif direction == 'y':
            vals = [s[1] for s in seeds]
        else:
            vals = [s[0] + s[1] for s in seeds]
        lo, hi = min(vals), max(vals)
        span = hi - lo if hi > lo else 1.0
        return [min_w + (max_w - min_w) * ((v - lo) / span) for v in vals]

    raise ValueError(f"Unknown weight method: {method!r}. "
                     f"Use uniform/random/proportional/inverse/"
                     f"gaussian/linear_gradient.")


# ---------------------------------------------------------------------------
# Weighted distance functions
# ---------------------------------------------------------------------------

def _power_distance(px, py, sx, sy, w):
    """Power distance: d² - w."""
    return (px - sx) ** 2 + (py - sy) ** 2 - w


def _multiplicative_distance(px, py, sx, sy, w):
    """Multiplicative weighted distance: d / w."""
    d = math.sqrt((px - sx) ** 2 + (py - sy) ** 2)
    return d / w if w > 0 else math.inf


def _additive_distance(px, py, sx, sy, w):
    """Additive weighted distance: d - w."""
    d = math.sqrt((px - sx) ** 2 + (py - sy) ** 2)
    return d - w


_DISTANCE_FUNCS = {
    'power': _power_distance,
    'multiplicative': _multiplicative_distance,
    'additive': _additive_distance,
}


def weighted_distance(px, py, sx, sy, w, mode='power'):
    """Compute weighted distance from point (px, py) to site (sx, sy).

    Parameters
    ----------
    px, py : float
        Query point coordinates.
    sx, sy : float
        Site (seed) coordinates.
    w : float
        Site weight.
    mode : str
        ``'power'``, ``'multiplicative'``, or ``'additive'``.

    Returns
    -------
    float
        Weighted distance value.
    """
    fn = _DISTANCE_FUNCS.get(mode)
    if fn is None:
        raise ValueError(f"Unknown mode: {mode!r}. "
                         f"Use power/multiplicative/additive.")
    return fn(px, py, sx, sy, w)


def compute_weighted_nn(point, seeds, weights, mode='power'):
    """Find the nearest seed to *point* using weighted distance.

    Parameters
    ----------
    point : tuple[float, float]
        Query point.
    seeds : list[tuple[float, float]]
        Seed coordinates.
    weights : list[float]
        Per-seed weights.
    mode : str
        Weighting mode.

    Returns
    -------
    tuple
        ``(index, seed, weighted_dist)`` of the nearest site.

    Raises
    ------
    ValueError
        If inputs are empty or lengths mismatch.
    """
    if not seeds:
        raise ValueError("seeds must not be empty")
    if len(seeds) != len(weights):
        raise ValueError("seeds and weights must have the same length")

    px, py = point
    fn = _DISTANCE_FUNCS.get(mode)
    if fn is None:
        raise ValueError(f"Unknown mode: {mode!r}")

    best_idx = 0
    best_dist = fn(px, py, seeds[0][0], seeds[0][1], weights[0])
    for i in range(1, len(seeds)):
        d = fn(px, py, seeds[i][0], seeds[i][1], weights[i])
        if d < best_dist:
            best_dist = d
            best_idx = i

    return best_idx, seeds[best_idx], best_dist


def batch_weighted_nn(points, seeds, weights, mode='power'):
    """Assign each point in *points* to its nearest weighted seed.

    Returns list of ``(index, seed, weighted_dist)`` tuples.
    """
    if not seeds:
        raise ValueError("seeds must not be empty")
    if len(seeds) != len(weights):
        raise ValueError("seeds and weights must have the same length")
    return [compute_weighted_nn(p, seeds, weights, mode) for p in points]


# ---------------------------------------------------------------------------
# Power Voronoi region computation
# ---------------------------------------------------------------------------

from vormap_geometry import (
    polygon_area as _polygon_area,
    polygon_centroid as _polygon_centroid,
    polygon_perimeter as _polygon_perimeter,
)


def compute_power_regions(seeds, weights, mode='power', bounds=None,
                          resolution=200):
    """Compute weighted Voronoi regions via grid sampling.

    This uses a pixel-sampling approach: a grid of points is created
    within the bounds, each assigned to the nearest weighted seed.
    The region boundary is then traced from the grid assignment.

    Parameters
    ----------
    seeds : list[tuple[float, float]]
        Seed coordinates.
    weights : list[float]
        Per-seed weights.
    mode : str
        ``'power'``, ``'multiplicative'``, or ``'additive'``.
    bounds : tuple, optional
        ``(x_min, x_max, y_min, y_max)``.  Auto-computed if None.
    resolution : int
        Grid resolution (higher = more precise boundaries).

    Returns
    -------
    list[list[tuple[float, float]]]
        List of polygon vertex lists, one per seed.
    """
    if not seeds:
        return []
    if len(seeds) != len(weights):
        raise ValueError("seeds and weights must have the same length")
    if mode not in _DISTANCE_FUNCS:
        raise ValueError(f"Unknown mode: {mode!r}")

    n = len(seeds)
    if n == 1:
        if bounds is None:
            x, y = seeds[0]
            bounds = (x - 100, x + 100, y - 100, y + 100)
        x_min, x_max, y_min, y_max = bounds
        return [[(x_min, y_min), (x_max, y_min),
                 (x_max, y_max), (x_min, y_max)]]

    # Determine bounds
    if bounds is None:
        xs = [s[0] for s in seeds]
        ys = [s[1] for s in seeds]
        pad_x = max((max(xs) - min(xs)) * 0.15, 10.0)
        pad_y = max((max(ys) - min(ys)) * 0.15, 10.0)
        bounds = (min(xs) - pad_x, max(xs) + pad_x,
                  min(ys) - pad_y, max(ys) + pad_y)

    x_min, x_max, y_min, y_max = bounds
    fn = _DISTANCE_FUNCS[mode]

    # Grid assignment
    resolution = max(resolution, 20)
    dx = (x_max - x_min) / resolution
    dy = (y_max - y_min) / resolution

    # Assign each grid cell to nearest weighted seed
    grid = []
    for iy in range(resolution):
        row = []
        py = y_min + (iy + 0.5) * dy
        for ix in range(resolution):
            px = x_min + (ix + 0.5) * dx
            best = 0
            best_d = fn(px, py, seeds[0][0], seeds[0][1], weights[0])
            for k in range(1, n):
                d = fn(px, py, seeds[k][0], seeds[k][1], weights[k])
                if d < best_d:
                    best_d = d
                    best = k
            row.append(best)
        grid.append(row)

    # Extract region polygons via marching squares (convex hull of cells)
    regions = []
    for k in range(n):
        pts = []
        for iy in range(resolution):
            for ix in range(resolution):
                if grid[iy][ix] == k:
                    cx = x_min + (ix + 0.5) * dx
                    cy = y_min + (iy + 0.5) * dy
                    # Check if boundary cell
                    is_boundary = False
                    for diy in (-1, 0, 1):
                        for dix in (-1, 0, 1):
                            if diy == 0 and dix == 0:
                                continue
                            ny, nx = iy + diy, ix + dix
                            if (ny < 0 or ny >= resolution or
                                    nx < 0 or nx >= resolution or
                                    grid[ny][nx] != k):
                                is_boundary = True
                                break
                        if is_boundary:
                            break
                    if is_boundary:
                        pts.append((cx, cy))

        if not pts:
            regions.append([])
            continue

        # Convex hull (Graham scan)
        hull = _convex_hull(pts)
        regions.append(hull)

    return regions


def _convex_hull(points):
    """Compute convex hull using Andrew's monotone chain."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class WeightedSeed:
    """A seed point with its weight and computed region metrics."""
    __slots__ = ('x', 'y', 'weight', 'index', 'region', 'area',
                 'perimeter', 'centroid')

    def __init__(self, x, y, weight, index, region=None):
        self.x = x
        self.y = y
        self.weight = weight
        self.index = index
        self.region = region or []
        self.area = _polygon_area(self.region) if self.region else 0.0
        self.perimeter = _polygon_perimeter(self.region) if self.region else 0.0
        self.centroid = _polygon_centroid(self.region) if self.region else (x, y)

    def to_dict(self):
        return {
            'index': self.index,
            'x': self.x,
            'y': self.y,
            'weight': self.weight,
            'area': round(self.area, 4),
            'perimeter': round(self.perimeter, 4),
            'centroid': [round(self.centroid[0], 4),
                         round(self.centroid[1], 4)],
            'vertices': [[round(v[0], 4), round(v[1], 4)]
                         for v in self.region],
        }


class PowerDiagramResult:
    """Container for a weighted Voronoi diagram computation."""

    def __init__(self, seeds, weights, regions, mode, bounds, resolution):
        self.mode = mode
        self.bounds = bounds
        self.resolution = resolution
        self.weighted_seeds = []
        for i, (s, w, r) in enumerate(zip(seeds, weights, regions)):
            self.weighted_seeds.append(WeightedSeed(s[0], s[1], w, i, r))

    @property
    def num_seeds(self):
        return len(self.weighted_seeds)

    @property
    def total_area(self):
        return sum(ws.area for ws in self.weighted_seeds)

    @property
    def weights(self):
        return [ws.weight for ws in self.weighted_seeds]

    @property
    def seeds(self):
        return [(ws.x, ws.y) for ws in self.weighted_seeds]

    @property
    def regions(self):
        return [ws.region for ws in self.weighted_seeds]

    @property
    def areas(self):
        return [ws.area for ws in self.weighted_seeds]

    def weight_area_correlation(self):
        """Pearson correlation between weights and areas."""
        ws = [s.weight for s in self.weighted_seeds if s.area > 0]
        ars = [s.area for s in self.weighted_seeds if s.area > 0]
        if len(ws) < 2:
            return 0.0
        n = len(ws)
        mean_w = sum(ws) / n
        mean_a = sum(ars) / n
        cov = sum((w - mean_w) * (a - mean_a) for w, a in zip(ws, ars)) / n
        std_w = math.sqrt(sum((w - mean_w) ** 2 for w in ws) / n)
        std_a = math.sqrt(sum((a - mean_a) ** 2 for a in ars) / n)
        if std_w < 1e-12 or std_a < 1e-12:
            return 0.0
        return cov / (std_w * std_a)

    def weight_stats(self):
        """Summary statistics for weights."""
        ws = self.weights
        n = len(ws)
        if n == 0:
            return {}
        mean = sum(ws) / n
        sorted_w = sorted(ws)
        median = (sorted_w[n // 2] if n % 2 == 1
                  else (sorted_w[n // 2 - 1] + sorted_w[n // 2]) / 2.0)
        variance = sum((w - mean) ** 2 for w in ws) / n
        return {
            'count': n,
            'min': min(ws),
            'max': max(ws),
            'mean': round(mean, 4),
            'median': round(median, 4),
            'std_dev': round(math.sqrt(variance), 4),
            'total': round(sum(ws), 4),
            'range': round(max(ws) - min(ws), 4),
        }

    def area_stats(self):
        """Summary statistics for region areas."""
        areas = [ws.area for ws in self.weighted_seeds]
        n = len(areas)
        if n == 0:
            return {}
        non_zero = [a for a in areas if a > 0]
        mean = sum(areas) / n
        sorted_a = sorted(areas)
        median = (sorted_a[n // 2] if n % 2 == 1
                  else (sorted_a[n // 2 - 1] + sorted_a[n // 2]) / 2.0)
        variance = sum((a - mean) ** 2 for a in areas) / n
        return {
            'count': n,
            'non_empty': len(non_zero),
            'empty': n - len(non_zero),
            'min': round(min(areas), 4),
            'max': round(max(areas), 4),
            'mean': round(mean, 4),
            'median': round(median, 4),
            'std_dev': round(math.sqrt(variance), 4),
            'total': round(sum(areas), 4),
        }

    def largest_cell(self):
        """Return the WeightedSeed with the largest area."""
        if not self.weighted_seeds:
            return None
        return max(self.weighted_seeds, key=lambda ws: ws.area)

    def smallest_cell(self):
        """Return the WeightedSeed with the smallest non-empty area."""
        non_empty = [ws for ws in self.weighted_seeds if ws.area > 0]
        if not non_empty:
            return None
        return min(non_empty, key=lambda ws: ws.area)

    def dominance_ratio(self):
        """Ratio of largest to smallest non-empty area."""
        non_empty = [ws.area for ws in self.weighted_seeds if ws.area > 0]
        if len(non_empty) < 2:
            return 1.0
        return max(non_empty) / min(non_empty)

    def find_cell(self, point):
        """Find which cell contains *point* using weighted distance."""
        fn = _DISTANCE_FUNCS[self.mode]
        best_idx = 0
        best_d = fn(point[0], point[1],
                     self.weighted_seeds[0].x, self.weighted_seeds[0].y,
                     self.weighted_seeds[0].weight)
        for i in range(1, len(self.weighted_seeds)):
            ws = self.weighted_seeds[i]
            d = fn(point[0], point[1], ws.x, ws.y, ws.weight)
            if d < best_d:
                best_d = d
                best_idx = i
        return self.weighted_seeds[best_idx]

    def summary(self):
        """Human-readable summary string."""
        lines = [
            f"Power Voronoi Diagram ({self.mode} mode)",
            f"  Seeds: {self.num_seeds}",
            f"  Resolution: {self.resolution}",
        ]
        ws = self.weight_stats()
        if ws:
            lines.append(f"  Weights: min={ws['min']}, max={ws['max']}, "
                         f"mean={ws['mean']}, std={ws['std_dev']}")
        astats = self.area_stats()
        if astats:
            lines.append(f"  Areas: non_empty={astats['non_empty']}, "
                         f"min={astats['min']}, max={astats['max']}, "
                         f"mean={astats['mean']}")
        corr = self.weight_area_correlation()
        lines.append(f"  Weight-Area correlation: {corr:.4f}")
        lines.append(f"  Dominance ratio: {self.dominance_ratio():.2f}")
        return '\n'.join(lines)

    def to_dict(self):
        return {
            'mode': self.mode,
            'bounds': list(self.bounds),
            'resolution': self.resolution,
            'num_seeds': self.num_seeds,
            'weight_stats': self.weight_stats(),
            'area_stats': self.area_stats(),
            'weight_area_correlation': round(self.weight_area_correlation(), 4),
            'dominance_ratio': round(self.dominance_ratio(), 4),
            'seeds': [ws.to_dict() for ws in self.weighted_seeds],
        }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_power_diagram(seeds, weights, mode='power', bounds=None,
                          resolution=200):
    """Compute a weighted Voronoi diagram.

    Parameters
    ----------
    seeds : list[tuple[float, float]]
        Seed point coordinates.
    weights : list[float]
        Per-seed weights (must be positive for multiplicative mode).
    mode : str
        ``'power'``, ``'multiplicative'``, or ``'additive'``.
    bounds : tuple, optional
        ``(x_min, x_max, y_min, y_max)``.
    resolution : int
        Grid resolution for region computation.

    Returns
    -------
    PowerDiagramResult
        Full result with regions, statistics, and analysis.

    Raises
    ------
    ValueError
        On invalid inputs.
    """
    if not seeds:
        raise ValueError("seeds must not be empty")
    if not weights:
        raise ValueError("weights must not be empty")
    if len(seeds) != len(weights):
        raise ValueError("seeds and weights must have the same length")
    if mode not in _DISTANCE_FUNCS:
        raise ValueError(f"Unknown mode: {mode!r}")
    if mode == 'multiplicative' and any(w <= 0 for w in weights):
        raise ValueError("All weights must be positive for multiplicative mode")

    # Auto-compute bounds
    if bounds is None:
        xs = [s[0] for s in seeds]
        ys = [s[1] for s in seeds]
        pad_x = max((max(xs) - min(xs)) * 0.15, 10.0)
        pad_y = max((max(ys) - min(ys)) * 0.15, 10.0)
        bounds = (min(xs) - pad_x, max(xs) + pad_x,
                  min(ys) - pad_y, max(ys) + pad_y)

    regions = compute_power_regions(seeds, weights, mode, bounds, resolution)
    return PowerDiagramResult(seeds, weights, regions, mode, bounds, resolution)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def weight_effect_analysis(seeds, weights, mode='power', bounds=None,
                           resolution=200):
    """Compare weighted diagram against uniform-weight baseline.

    Returns a dict with per-seed area changes and overall metrics.
    """
    if not seeds or not weights:
        raise ValueError("seeds and weights must not be empty")

    uniform_weights = [1.0] * len(seeds)
    baseline = compute_power_diagram(seeds, uniform_weights, mode, bounds,
                                     resolution)
    weighted = compute_power_diagram(seeds, weights, mode, bounds, resolution)

    changes = []
    for i in range(len(seeds)):
        ba = baseline.weighted_seeds[i].area
        wa = weighted.weighted_seeds[i].area
        pct = ((wa - ba) / ba * 100.0) if ba > 0 else 0.0
        changes.append({
            'index': i,
            'seed': list(seeds[i]),
            'weight': weights[i],
            'baseline_area': round(ba, 4),
            'weighted_area': round(wa, 4),
            'area_change': round(wa - ba, 4),
            'change_pct': round(pct, 2),
        })

    # Gini coefficient of area distribution
    def gini(values):
        if not values or all(v == 0 for v in values):
            return 0.0
        vals = sorted(values)
        n = len(vals)
        total = sum(vals)
        if total == 0:
            return 0.0
        cum = sum((2 * (i + 1) - n - 1) * v for i, v in enumerate(vals))
        return cum / (n * total)

    return {
        'mode': mode,
        'num_seeds': len(seeds),
        'baseline_gini': round(gini(baseline.areas), 4),
        'weighted_gini': round(gini(weighted.areas), 4),
        'weight_area_correlation': round(weighted.weight_area_correlation(), 4),
        'changes': changes,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_power_json(result, path=None):
    """Export PowerDiagramResult to JSON.

    Returns the JSON string; writes to *path* if given.
    """
    data = result.to_dict()
    text = json.dumps(data, indent=2)
    if path:
        from vormap import validate_output_path
        path = validate_output_path(path, allow_absolute=True)
        with open(path, 'w') as f:
            f.write(text)
    return text


def export_power_svg(result, path=None, width=800, height=600,
                     color_scheme='viridis', show_weights=True,
                     show_seeds=True):
    """Export PowerDiagramResult as SVG.

    Parameters
    ----------
    result : PowerDiagramResult
    path : str, optional
        File path to write.  If None, returns SVG string only.
    width, height : int
        SVG dimensions.
    color_scheme : str
        ``'viridis'``, ``'plasma'``, ``'warm'``, ``'cool'``,
        ``'earth'``, ``'pastel'``.
    show_weights : bool
        Show weight labels on seeds.
    show_seeds : bool
        Show seed point markers.

    Returns
    -------
    str
        SVG markup.
    """
    schemes = {
        'viridis': ['#440154', '#482777', '#3f4a8a', '#31688e', '#26828e',
                     '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725'],
        'plasma': ['#0d0887', '#46039f', '#7201a8', '#9c179e', '#bd3786',
                    '#d8576b', '#ed7953', '#fb9f3a', '#fdca26', '#f0f921'],
        'warm': ['#fee090', '#fdae61', '#f46d43', '#d73027', '#a50026',
                  '#fff7bc', '#fee391', '#fec44f', '#fe9929', '#ec7014'],
        'cool': ['#f7fbff', '#deebf7', '#c6dbef', '#9ecae1', '#6baed6',
                  '#4292c6', '#2171b5', '#08519c', '#08306b', '#041f4d'],
        'earth': ['#8c510a', '#bf812d', '#dfc27d', '#f6e8c3', '#c7eae5',
                   '#80cdc1', '#35978f', '#01665e', '#003c30', '#543005'],
        'pastel': ['#fbb4ae', '#b3cde3', '#ccebc5', '#decbe4', '#fed9a6',
                    '#ffffcc', '#e5d8bd', '#fddaec', '#f2f2f2', '#b3e2cd'],
    }
    colors = schemes.get(color_scheme, schemes['viridis'])

    x_min, x_max, y_min, y_max = result.bounds
    sx = width / (x_max - x_min) if x_max > x_min else 1
    sy = height / (y_max - y_min) if y_max > y_min else 1
    scale = min(sx, sy) * 0.9
    ox = width * 0.05
    oy = height * 0.05

    def tx(x):
        return ox + (x - x_min) * scale

    def ty(y):
        return oy + (y_max - y) * scale  # flip y

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}">']
    parts.append(f'<rect width="{width}" height="{height}" fill="white"/>')

    # Draw regions
    for ws in result.weighted_seeds:
        if not ws.region:
            continue
        ci = ws.index % len(colors)
        points_str = ' '.join(f'{tx(v[0]):.2f},{ty(v[1]):.2f}'
                              for v in ws.region)
        parts.append(f'<polygon points="{points_str}" '
                     f'fill="{colors[ci]}" fill-opacity="0.6" '
                     f'stroke="#333" stroke-width="1"/>')

    # Draw seeds
    if show_seeds:
        max_w = max(ws.weight for ws in result.weighted_seeds)
        min_w = min(ws.weight for ws in result.weighted_seeds)
        w_range = max_w - min_w if max_w > min_w else 1.0

        for ws in result.weighted_seeds:
            r = 3 + 7 * (ws.weight - min_w) / w_range
            parts.append(f'<circle cx="{tx(ws.x):.2f}" cy="{ty(ws.y):.2f}" '
                         f'r="{r:.1f}" fill="black" fill-opacity="0.8"/>')
            if show_weights:
                parts.append(
                    f'<text x="{tx(ws.x):.2f}" '
                    f'y="{ty(ws.y) - r - 2:.2f}" '
                    f'text-anchor="middle" font-size="10" '
                    f'fill="#333">w={ws.weight:.1f}</text>')

    parts.append('</svg>')
    svg = '\n'.join(parts)
    if path:
        from vormap import validate_output_path
        path = validate_output_path(path, allow_absolute=True)
        with open(path, 'w') as f:
            f.write(svg)
    return svg


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def main():
    """Command-line interface for weighted Voronoi diagrams."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Weighted (Power) Voronoi Diagram Generator')
    parser.add_argument('input', nargs='?',
                        help='Input file with seed points (x y per line)')
    parser.add_argument('--seeds', type=int, default=10,
                        help='Number of random seeds (default: 10)')
    parser.add_argument('--mode', choices=['power', 'multiplicative',
                                           'additive'],
                        default='power', help='Weighting mode')
    parser.add_argument('--weight-method',
                        choices=['uniform', 'random', 'proportional',
                                 'inverse', 'gaussian', 'linear_gradient'],
                        default='random', help='Weight assignment method')
    parser.add_argument('--min-weight', type=float, default=1.0)
    parser.add_argument('--max-weight', type=float, default=10.0)
    parser.add_argument('--resolution', type=int, default=200)
    parser.add_argument('--seed', type=int, default=None,
                        help='Random seed for reproducibility')
    parser.add_argument('--json', dest='json_path', metavar='PATH',
                        help='Export JSON to file')
    parser.add_argument('--svg', dest='svg_path', metavar='PATH',
                        help='Export SVG to file')
    parser.add_argument('--color-scheme', default='viridis',
                        choices=['viridis', 'plasma', 'warm', 'cool',
                                 'earth', 'pastel'])
    parser.add_argument('--analyze', action='store_true',
                        help='Run weight effect analysis')
    parser.add_argument('--quiet', action='store_true')

    args = parser.parse_args()

    # Load or generate seeds
    if args.input:
        from vormap import validate_input_path
        resolved_input = validate_input_path(args.input, allow_absolute=True)
        seeds = []
        with open(resolved_input) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    seeds.append((float(parts[0]), float(parts[1])))
        if not seeds:
            print("Error: no valid seed points found in input file.")
            sys.exit(1)
    else:
        rng = _random.Random(args.seed)
        seeds = [(rng.uniform(0, 1000), rng.uniform(0, 1000))
                 for _ in range(args.seeds)]

    # Assign weights
    weights = assign_weights(seeds, method=args.weight_method,
                             min_w=args.min_weight, max_w=args.max_weight,
                             seed=args.seed)

    # Compute
    result = compute_power_diagram(seeds, weights, mode=args.mode,
                                   resolution=args.resolution)

    if not args.quiet:
        print(result.summary())

    if args.json_path:
        export_power_json(result, args.json_path)
        if not args.quiet:
            print(f"\nJSON exported to {args.json_path}")

    if args.svg_path:
        export_power_svg(result, args.svg_path,
                         color_scheme=args.color_scheme)
        if not args.quiet:
            print(f"SVG exported to {args.svg_path}")

    if args.analyze:
        analysis = weight_effect_analysis(seeds, weights, mode=args.mode,
                                          resolution=args.resolution)
        if not args.quiet:
            print(f"\n--- Weight Effect Analysis ---")
            print(f"  Baseline Gini: {analysis['baseline_gini']}")
            print(f"  Weighted Gini: {analysis['weighted_gini']}")
            print(f"  Correlation:   {analysis['weight_area_correlation']}")
            for c in analysis['changes']:
                print(f"  Seed {c['index']}: w={c['weight']:.1f}, "
                      f"area {c['baseline_area']:.0f} → "
                      f"{c['weighted_area']:.0f} ({c['change_pct']:+.1f}%)")


if __name__ == '__main__':
    main()
