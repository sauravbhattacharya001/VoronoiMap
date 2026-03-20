"""Synthetic point pattern generator for VoronoiMap.

Generates test datasets with known spatial distributions for
benchmarking, demos, and spatial statistics validation.

Patterns:
    - ``poisson``: Complete Spatial Randomness (CSR / homogeneous Poisson)
    - ``clustered``: Thomas (Poisson cluster) process — parent-child clusters
    - ``regular``: Jittered grid — approximately uniform spacing
    - ``inhibitory``: Hard-core (Simple Sequential Inhibition) — minimum distance
    - ``gradient``: Inhomogeneous Poisson with a linear density gradient
    - ``mixed``: Combination of clusters + background noise

Example::

    from vormap_generate import generate_pattern
    points = generate_pattern("clustered", n=200, seed=42)

CLI::

    python vormap_generate.py poisson 500 --output points.txt
    python vormap_generate.py clustered 300 --parents 10 --radius 50
    python vormap_generate.py inhibitory 200 --min-dist 20
    python vormap_generate.py mixed 400 --cluster-fraction 0.6
    python vormap_generate.py gradient 500 --direction diagonal
"""

import argparse
import json
import math
import os
import random
import sys

import vormap

try:
    import scipy  # noqa: F401
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Generators ───────────────────────────────────────────────────────


def generate_poisson(n, bounds=(0, 1000, 0, 2000), seed=None):
    """Complete Spatial Randomness (homogeneous Poisson process).

    Points are uniformly distributed within *bounds* (south, north, west, east).

    Parameters
    ----------
    n : int
        Number of points.
    bounds : tuple
        (south, north, west, east) bounding box.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    list of (float, float)
        Generated (x, y) coordinates.
    """
    rng = random.Random(seed)
    s, n_bound, w, e = bounds
    return [(rng.uniform(w, e), rng.uniform(s, n_bound)) for _ in range(n)]


def generate_clustered(n, parents=None, radius=50.0,
                       bounds=(0, 1000, 0, 2000), seed=None):
    """Thomas (Poisson cluster) process.

    Generates *parents* cluster centres uniformly, then distributes
    child points around each centre with a Gaussian offset (std = *radius*).
    Points outside *bounds* are rejected and resampled.

    Parameters
    ----------
    n : int
        Total number of child points.
    parents : int or None
        Number of cluster centres (default: sqrt(n)).
    radius : float
        Standard deviation of child scatter around parent.
    bounds : tuple
        (south, north, west, east) bounding box.
    seed : int or None
        Random seed.

    Returns
    -------
    list of (float, float)
    """
    rng = random.Random(seed)
    s, n_bound, w, e = bounds
    if parents is None:
        parents = max(2, int(math.sqrt(n)))

    centres = [(rng.uniform(w, e), rng.uniform(s, n_bound))
               for _ in range(parents)]
    points = []
    max_attempts = n * 20
    attempts = 0
    while len(points) < n and attempts < max_attempts:
        cx, cy = rng.choice(centres)
        px = cx + rng.gauss(0, radius)
        py = cy + rng.gauss(0, radius)
        if w <= px <= e and s <= py <= n_bound:
            points.append((px, py))
        attempts += 1
    return points


def generate_regular(n, jitter=0.15, bounds=(0, 1000, 0, 2000), seed=None):
    """Jittered regular grid.

    Computes a grid with approximately *n* cells, then offsets each point
    by a random amount proportional to *jitter* × cell size.

    Parameters
    ----------
    n : int
        Approximate number of points.
    jitter : float
        Jitter fraction (0 = perfect grid, 1 = cell-width randomness).
    bounds : tuple
        (south, north, west, east) bounding box.
    seed : int or None
        Random seed.

    Returns
    -------
    list of (float, float)
    """
    rng = random.Random(seed)
    s, n_bound, w, e = bounds
    width = e - w
    height = n_bound - s
    area = width * height
    cell_size = math.sqrt(area / max(n, 1))
    cols = max(1, int(width / cell_size))
    rows = max(1, int(height / cell_size))
    dx = width / cols
    dy = height / rows

    points = []
    for r in range(rows):
        for c in range(cols):
            px = w + (c + 0.5) * dx + rng.uniform(-jitter, jitter) * dx
            py = s + (r + 0.5) * dy + rng.uniform(-jitter, jitter) * dy
            px = max(w, min(e, px))
            py = max(s, min(n_bound, py))
            points.append((px, py))
    return points[:n]


def generate_inhibitory(n, min_dist=None, bounds=(0, 1000, 0, 2000),
                        seed=None):
    """Simple Sequential Inhibition (hard-core) process.

    Places points one at a time, rejecting any that fall within *min_dist*
    of an existing point.  Stops after *n* points or when placement fails
    repeatedly.

    Parameters
    ----------
    n : int
        Target number of points.
    min_dist : float or None
        Minimum inter-point distance (default: auto from area).
    bounds : tuple
        (south, north, west, east) bounding box.
    seed : int or None
        Random seed.

    Returns
    -------
    list of (float, float)
    """
    rng = random.Random(seed)
    s, n_bound, w, e = bounds
    width = e - w
    height = n_bound - s
    if min_dist is None:
        # pack circles: area / n ≈ π r²  → r ≈ sqrt(area / (π n)) * 0.7
        min_dist = math.sqrt(width * height / (math.pi * max(n, 1))) * 0.7

    # Use a grid-based spatial hash for O(1) expected proximity checks
    # instead of brute-force O(n) scan per candidate.  The grid cell size
    # equals min_dist so only the 3×3 neighborhood needs checking.
    cell_size = min_dist
    grid = {}  # (col, row) -> list of (x, y)

    def _grid_key(x, y):
        """Map a point to its spatial hash grid cell index."""
        return (int((x - w) / cell_size), int((y - s) / cell_size))

    def _is_too_close(px, py):
        """Check if a point is within *min_dist* of any existing point in the grid."""
        gc, gr = _grid_key(px, py)
        min_dist_sq = min_dist ** 2
        for dc in range(-1, 2):
            for dr in range(-1, 2):
                cell = grid.get((gc + dc, gr + dr))
                if cell:
                    for qx, qy in cell:
                        if (px - qx) ** 2 + (py - qy) ** 2 < min_dist_sq:
                            return True
        return False

    points = []
    max_attempts = n * 100
    attempts = 0
    while len(points) < n and attempts < max_attempts:
        px = rng.uniform(w, e)
        py = rng.uniform(s, n_bound)
        if not _is_too_close(px, py):
            points.append((px, py))
            key = _grid_key(px, py)
            if key not in grid:
                grid[key] = []
            grid[key].append((px, py))
        attempts += 1
    return points


def generate_gradient(n, direction="horizontal",
                      bounds=(0, 1000, 0, 2000), seed=None):
    """Inhomogeneous Poisson process with a linear density gradient.

    Intensity increases along the specified *direction*: points are denser
    at the high end.  Uses thinning (rejection sampling) against a linear
    intensity function.

    Parameters
    ----------
    n : int
        Expected number of points.
    direction : str
        "horizontal" (left→right), "vertical" (bottom→top), or "diagonal".
    bounds : tuple
        (south, north, west, east) bounding box.
    seed : int or None
        Random seed.

    Returns
    -------
    list of (float, float)
    """
    rng = random.Random(seed)
    s, n_bound, w, e = bounds
    width = e - w
    height = n_bound - s

    def intensity(x, y):
        """Compute the gradient intensity at a point for density-biased sampling.

        Returns a value in [0, 1] where higher values increase the
        probability of accepting a candidate point.
        """
        if direction == "horizontal":
            return (x - w) / width
        elif direction == "vertical":
            return (y - s) / height
        else:  # diagonal
            return ((x - w) / width + (y - s) / height) / 2.0

    # Use an adaptive loop instead of fixed oversampling.
    # The old approach generated a fixed 2.5×n candidates and thinned,
    # which could return far fewer than n points because the average
    # acceptance rate of the linear intensity is only ~0.5 (expected
    # yield ≈ 1.25n, with high variance for small n or unlucky seeds).
    points = []
    max_attempts = n * 50
    attempts = 0
    while len(points) < n and attempts < max_attempts:
        x = rng.uniform(w, e)
        y = rng.uniform(s, n_bound)
        if rng.random() < intensity(x, y):
            points.append((x, y))
        attempts += 1
    return points


def generate_mixed(n, cluster_fraction=0.5, parents=None, radius=50.0,
                   bounds=(0, 1000, 0, 2000), seed=None):
    """Mixed pattern: clusters + uniform background noise.

    Parameters
    ----------
    n : int
        Total number of points.
    cluster_fraction : float
        Fraction of points in clusters (0-1).
    parents : int or None
        Number of cluster centres.
    radius : float
        Cluster scatter radius.
    bounds : tuple
        (south, north, west, east) bounding box.
    seed : int or None
        Random seed.

    Returns
    -------
    list of (float, float)
    """
    n_clustered = int(n * cluster_fraction)
    n_random = n - n_clustered
    rng = random.Random(seed)
    seed1 = rng.randint(0, 2**31)
    seed2 = rng.randint(0, 2**31)
    clustered = generate_clustered(n_clustered, parents=parents,
                                   radius=radius, bounds=bounds, seed=seed1)
    uniform = generate_poisson(n_random, bounds=bounds, seed=seed2)
    result = clustered + uniform
    rng2 = random.Random(seed)
    rng2.shuffle(result)
    return result


# ── Unified API ──────────────────────────────────────────────────────

_GENERATORS = {
    "poisson": generate_poisson,
    "clustered": generate_clustered,
    "regular": generate_regular,
    "inhibitory": generate_inhibitory,
    "gradient": generate_gradient,
    "mixed": generate_mixed,
}


def generate_pattern(pattern, n=100, bounds=(0, 1000, 0, 2000),
                     seed=None, **kwargs):
    """Generate a point pattern by name.

    Parameters
    ----------
    pattern : str
        One of: poisson, clustered, regular, inhibitory, gradient, mixed.
    n : int
        Number of points.
    bounds : tuple
        (south, north, west, east).
    seed : int or None
        Random seed.
    **kwargs
        Pattern-specific parameters (radius, parents, min_dist, etc.).

    Returns
    -------
    list of (float, float)

    Raises
    ------
    ValueError
        If *pattern* is not recognized.
    """
    if pattern not in _GENERATORS:
        raise ValueError(
            "Unknown pattern '%s'. Choose from: %s"
            % (pattern, ", ".join(sorted(_GENERATORS)))
        )
    return _GENERATORS[pattern](n, bounds=bounds, seed=seed, **kwargs)


def list_patterns():
    """Return available pattern names."""
    return sorted(_GENERATORS.keys())


# ── Export helpers ───────────────────────────────────────────────────


def export_txt(points, filepath, allow_absolute=False):
    """Write points to a space-separated text file (vormap native format).

    Parameters
    ----------
    points : list of (float, float)
    filepath : str
    allow_absolute : bool
    """
    resolved = vormap.validate_output_path(filepath,
                                           allow_absolute=allow_absolute)
    with open(resolved, "w", encoding="utf-8") as f:
        for x, y in points:
            f.write("%.6f %.6f\n" % (x, y))


def export_csv(points, filepath, allow_absolute=False):
    """Write points to CSV with x,y headers."""
    resolved = vormap.validate_output_path(filepath,
                                           allow_absolute=allow_absolute)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write("x,y\n")
        for x, y in points:
            f.write("%.6f,%.6f\n" % (x, y))


def export_json(points, filepath, allow_absolute=False):
    """Write points as a JSON array of [x, y] pairs."""
    resolved = vormap.validate_output_path(filepath,
                                           allow_absolute=allow_absolute)
    data = [[round(x, 6), round(y, 6)] for x, y in points]
    with open(resolved, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def pattern_summary(points, pattern_name="unknown"):
    """Compute summary statistics for a generated pattern.

    Returns
    -------
    dict
        Keys: pattern, count, centroid, spread, bbox, nni (nearest-neighbor
        index — <1 clustered, ~1 random, >1 regular).
    """
    if not points:
        return {"pattern": pattern_name, "count": 0}

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    spread_x = max(xs) - min(xs)
    spread_y = max(ys) - min(ys)

    # Nearest-neighbor index (Clark-Evans R)
    n = len(points)
    if n > 1:
        if _HAS_SCIPY:
            # O(n log n) using scipy KDTree
            from scipy.spatial import cKDTree
            tree = cKDTree(points)
            dists, _ = tree.query(points, k=2)  # k=2: self + nearest
            nn_dists = dists[:, 1].tolist()
        else:
            # Fallback: grid-accelerated nearest-neighbor search.
            # Partition points into cells of adaptive size to achieve
            # ~O(n) expected time instead of brute-force O(n²).
            _xs = [p[0] for p in points]
            _ys = [p[1] for p in points]
            _grid_size = max(1, int(math.sqrt(n)))
            _x_min, _x_max = min(_xs), max(_xs)
            _y_min, _y_max = min(_ys), max(_ys)
            _cell_w = (_x_max - _x_min) / _grid_size if _x_max > _x_min else 1.0
            _cell_h = (_y_max - _y_min) / _grid_size if _y_max > _y_min else 1.0

            _grid = {}
            for idx, (px, py) in enumerate(points):
                ci = min(int((px - _x_min) / _cell_w), _grid_size - 1)
                cj = min(int((py - _y_min) / _cell_h), _grid_size - 1)
                _grid.setdefault((ci, cj), []).append(idx)

            nn_dists = []
            for i, (px, py) in enumerate(points):
                ci = min(int((px - _x_min) / _cell_w), _grid_size - 1)
                cj = min(int((py - _y_min) / _cell_h), _grid_size - 1)
                min_d = float("inf")
                # Search expanding rings of neighboring cells
                for ring in range(max(_grid_size, _grid_size) + 1):
                    if ring > 1 and min_d < (ring - 1) * min(_cell_w, _cell_h):
                        break  # Can't find closer point in further cells
                    for di in range(-ring, ring + 1):
                        for dj in range(-ring, ring + 1):
                            if max(abs(di), abs(dj)) != ring and ring > 0:
                                continue  # Only check the new ring
                            cell = _grid.get((ci + di, cj + dj))
                            if cell:
                                for j in cell:
                                    if j != i:
                                        d = math.sqrt((px - points[j][0]) ** 2 + (py - points[j][1]) ** 2)
                                        if d < min_d:
                                            min_d = d
                    if min_d < float("inf") and ring >= 1:
                        # Check if we can prune: nearest found is closer
                        # than any point in further rings
                        if min_d <= ring * min(_cell_w, _cell_h):
                            break
                nn_dists.append(min_d)
        mean_nn = sum(nn_dists) / n
        area = spread_x * spread_y if spread_x * spread_y > 0 else 1.0
        expected_nn = 0.5 * math.sqrt(area / n)
        nni = mean_nn / expected_nn if expected_nn > 0 else 0
    else:
        nni = None

    return {
        "pattern": pattern_name,
        "count": n,
        "centroid": (round(cx, 2), round(cy, 2)),
        "spread": (round(spread_x, 2), round(spread_y, 2)),
        "bbox": (round(min(xs), 2), round(max(xs), 2),
                 round(min(ys), 2), round(max(ys), 2)),
        "nni": round(nni, 4) if nni is not None else None,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def _build_parser():
    """Build the argparse parser for the vormap_generate CLI."""
    parser = argparse.ArgumentParser(
        prog="vormap_generate",
        description="Generate synthetic point patterns for VoronoiMap.",
        epilog=(
            "Examples:\n"
            "  %(prog)s poisson 500 -o random_points.txt\n"
            "  %(prog)s clustered 300 --parents 10 --radius 50\n"
            "  %(prog)s inhibitory 200 --min-dist 20\n"
            "  %(prog)s gradient 500 --direction diagonal -f csv\n"
            "  %(prog)s mixed 400 --cluster-fraction 0.6 --stats\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("pattern", choices=sorted(_GENERATORS.keys()),
                        help="Spatial pattern to generate.")
    parser.add_argument("n", type=int, help="Number of points.")
    parser.add_argument("-o", "--output", default=None,
                        help="Output file (default: stdout as txt).")
    parser.add_argument("-f", "--format", choices=["txt", "csv", "json"],
                        default="txt",
                        help="Output format (default: txt).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility.")
    parser.add_argument("--bounds",
                        help="Bounding box: south,north,west,east "
                             "(default: 0,1000,0,2000).")
    # Pattern-specific
    parser.add_argument("--parents", type=int, default=None,
                        help="Number of cluster centres (clustered/mixed).")
    parser.add_argument("--radius", type=float, default=50.0,
                        help="Cluster scatter radius (clustered/mixed).")
    parser.add_argument("--jitter", type=float, default=0.15,
                        help="Jitter fraction for regular grid (0-1).")
    parser.add_argument("--min-dist", type=float, default=None,
                        help="Minimum distance for inhibitory process.")
    parser.add_argument("--direction", default="horizontal",
                        choices=["horizontal", "vertical", "diagonal"],
                        help="Gradient direction.")
    parser.add_argument("--cluster-fraction", type=float, default=0.5,
                        help="Fraction of clustered points (mixed).")
    parser.add_argument("--stats", action="store_true",
                        help="Print pattern summary statistics.")
    return parser


def main(argv=None):
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Parse bounds
    if args.bounds:
        parts = [float(x) for x in args.bounds.split(",")]
        if len(parts) != 4:
            parser.error("--bounds requires 4 values: south,north,west,east")
        bounds = tuple(parts)
    else:
        bounds = (0, 1000, 0, 2000)

    # Build kwargs based on pattern
    kwargs = {}
    if args.pattern in ("clustered", "mixed"):
        kwargs["parents"] = args.parents
        kwargs["radius"] = args.radius
    if args.pattern == "regular":
        kwargs["jitter"] = args.jitter
    if args.pattern == "inhibitory":
        kwargs["min_dist"] = args.min_dist
    if args.pattern == "gradient":
        kwargs["direction"] = args.direction
    if args.pattern == "mixed":
        kwargs["cluster_fraction"] = args.cluster_fraction

    points = generate_pattern(args.pattern, n=args.n, bounds=bounds,
                              seed=args.seed, **kwargs)

    # Stats
    if args.stats:
        summary = pattern_summary(points, args.pattern)
        print("Pattern: %s" % summary["pattern"])
        print("Points:  %d" % summary["count"])
        if summary.get("centroid"):
            print("Centroid: (%.2f, %.2f)" % summary["centroid"])
        if summary.get("spread"):
            print("Spread:  %.2f x %.2f" % summary["spread"])
        if summary.get("nni") is not None:
            nni = summary["nni"]
            label = ("clustered" if nni < 0.8
                     else "regular" if nni > 1.2
                     else "random")
            print("NNI:     %.4f (%s)" % (nni, label))
        print()

    # Output
    if args.output:
        fmt = args.format
        exporters = {"txt": export_txt, "csv": export_csv,
                     "json": export_json}
        exporters[fmt](points, args.output, allow_absolute=True)
        print("Generated %d %s points → %s (%s)"
              % (len(points), args.pattern, args.output, fmt))
    else:
        # stdout
        if args.format == "json":
            data = [[round(x, 6), round(y, 6)] for x, y in points]
            json.dump(data, sys.stdout, indent=2)
            print()
        elif args.format == "csv":
            print("x,y")
            for x, y in points:
                print("%.6f,%.6f" % (x, y))
        else:
            for x, y in points:
                print("%.6f %.6f" % (x, y))


if __name__ == "__main__":
    main()
