"""Spatial smoothing of attribute values across Voronoi cells.

Smooth noisy spatial data by averaging attribute values over Voronoi
neighbourhoods.  Supports multiple smoothing methods:

- **mean** — simple average of neighbour values
- **median** — robust to outliers
- **gaussian** — distance-weighted Gaussian kernel
- **inverse_distance** — inverse-distance-weighted (IDW) average

Useful for denoising spatial measurements, creating smoother surfaces
for visualisation, and pre-processing data for interpolation.

Usage
-----
CLI::

    python vormap_smooth.py data/sample.txt -o smoothed.csv
    python vormap_smooth.py data/sample.txt --method gaussian --iterations 3
    python vormap_smooth.py data/sample.txt --method median --alpha 0.5

Library::

    from vormap_smooth import smooth_attributes, SmoothConfig

    config = SmoothConfig(method="gaussian", iterations=2, sigma=50.0)
    result = smooth_attributes(data, regions, values, config)
    print(result.summary())
"""

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import vormap
from vormap_utils import euclidean as _compute_distance

# Try importing graph extraction
try:
    from vormap_graph import extract_neighborhood_graph
    _HAS_GRAPH = True
except ImportError:
    _HAS_GRAPH = False

# Try importing region computation
try:
    from vormap_viz import compute_regions
    _HAS_VIZ = True
except ImportError:
    _HAS_VIZ = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


@dataclass
class SmoothConfig:
    """Configuration for spatial smoothing.

    Parameters
    ----------
    method : str
        Smoothing method: 'mean', 'median', 'gaussian', 'inverse_distance'.
    iterations : int
        Number of smoothing passes (default 1). More passes = smoother.
    alpha : float
        Blending factor 0-1. 0 = keep original, 1 = full smoothing.
        Default 1.0.
    sigma : float
        Bandwidth for Gaussian kernel (in coordinate units). Default 100.0.
    power : float
        Power parameter for inverse-distance weighting. Default 2.0.
    include_self : bool
        Whether to include the cell's own value in the smoothing
        computation (default True).
    """
    method: str = "mean"
    iterations: int = 1
    alpha: float = 1.0
    sigma: float = 100.0
    power: float = 2.0
    include_self: bool = True

    def __post_init__(self):
        valid = {"mean", "median", "gaussian", "inverse_distance"}
        if self.method not in valid:
            raise ValueError(
                "Unknown method '%s'. Choose from: %s"
                % (self.method, ", ".join(sorted(valid)))
            )
        if self.iterations < 1:
            raise ValueError("iterations must be >= 1, got %d" % self.iterations)
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be in [0, 1], got %s" % self.alpha)
        if self.sigma <= 0:
            raise ValueError("sigma must be > 0, got %s" % self.sigma)
        if self.power <= 0:
            raise ValueError("power must be > 0, got %s" % self.power)


@dataclass
class SmoothResult:
    """Result of spatial smoothing.

    Attributes
    ----------
    original : dict
        Seed -> original value mapping.
    smoothed : dict
        Seed -> smoothed value mapping.
    config : SmoothConfig
        Configuration used.
    iterations_applied : int
        Actual iterations performed.
    convergence : list of float
        Max absolute change per iteration.
    """
    original: Dict[Tuple[float, float], float] = field(default_factory=dict)
    smoothed: Dict[Tuple[float, float], float] = field(default_factory=dict)
    config: SmoothConfig = field(default_factory=SmoothConfig)
    iterations_applied: int = 0
    convergence: List[float] = field(default_factory=list)

    def summary(self) -> str:
        """Return a human-readable summary."""
        lines = [
            "Spatial Smoothing Summary",
            "=" * 40,
            "Method:        %s" % self.config.method,
            "Iterations:    %d" % self.iterations_applied,
            "Alpha:         %.2f" % self.config.alpha,
            "Points:        %d" % len(self.smoothed),
        ]
        if self.convergence:
            lines.append("Convergence:   %s" % ", ".join(
                "%.4f" % c for c in self.convergence
            ))

        # Stats on change
        if self.original and self.smoothed:
            diffs = []
            for k in self.original:
                if k in self.smoothed:
                    diffs.append(abs(self.smoothed[k] - self.original[k]))
            if diffs:
                lines.extend([
                    "",
                    "Change Statistics",
                    "-" * 40,
                    "Mean change:   %.4f" % (sum(diffs) / len(diffs)),
                    "Max change:    %.4f" % max(diffs),
                    "Min change:    %.4f" % min(diffs),
                ])
        return "\n".join(lines)

    def delta_map(self) -> Dict[Tuple[float, float], float]:
        """Return seed -> absolute change mapping."""
        return {
            k: abs(self.smoothed[k] - self.original[k])
            for k in self.original if k in self.smoothed
        }



def _smooth_mean(vals, dists, config):
    """Arithmetic mean of values (ignores distances)."""
    return sum(vals) / len(vals)


def _smooth_median(vals, dists, config):
    """Median of values (robust to outliers)."""
    sorted_vals = sorted(vals)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0


def _smooth_gaussian(vals, dists, config):
    """Distance-weighted Gaussian kernel average."""
    two_sigma_sq = 2.0 * config.sigma * config.sigma
    weights = [math.exp(-(d * d) / two_sigma_sq) for d in dists]
    total_w = sum(weights)
    if total_w <= 0:
        return None  # signal fallback to original value
    return sum(v * w for v, w in zip(vals, weights)) / total_w


def _smooth_inverse_distance(vals, dists, config):
    """Inverse-distance-weighted average."""
    weights = [
        1e12 if d < 1e-12 else 1.0 / (d ** config.power)
        for d in dists
    ]
    total_w = sum(weights)
    if total_w <= 0:
        return None  # signal fallback to original value
    return sum(v * w for v, w in zip(vals, weights)) / total_w


# Dispatch table for smoothing strategies — add new methods here.
_SMOOTH_STRATEGIES = {
    "mean": _smooth_mean,
    "median": _smooth_median,
    "gaussian": _smooth_gaussian,
    "inverse_distance": _smooth_inverse_distance,
}


def _precompute_distances(adjacency, seeds_set):
    """Pre-compute distances between each seed and its neighbours.

    Building this cache once avoids redundant ``math.sqrt`` calls on
    every smoothing iteration — a significant win when ``iterations``
    is large (e.g. gaussian/IDW smoothing with 10+ passes).

    Parameters
    ----------
    adjacency : dict
        Seed -> list of neighbour seeds.
    seeds_set : set
        Set of seed points that have values.

    Returns
    -------
    dict
        Mapping ``(seed, neighbour) -> float`` distance.
    """
    cache = {}
    for seed in seeds_set:
        for nb in adjacency.get(seed, []):
            if nb in seeds_set:
                key = (seed, nb)
                if key not in cache:
                    cache[key] = _compute_distance(seed, nb)
    return cache


def _smooth_once(values, adjacency, seeds, config, dist_cache=None):
    """Apply one smoothing pass.

    Parameters
    ----------
    values : dict
        Seed -> current value.
    adjacency : dict
        Seed -> list of neighbour seeds.
    seeds : list
        All seed points.
    config : SmoothConfig
        Smoothing configuration.
    dist_cache : dict or None
        Pre-computed ``(seed, neighbour) -> distance`` mapping.
        When provided, avoids recomputing Euclidean distances each
        iteration.

    Returns
    -------
    dict
        Seed -> new smoothed value.
    float
        Max absolute change.
    """
    strategy = _SMOOTH_STRATEGIES[config.method]
    new_values = {}
    max_change = 0.0
    use_cache = dist_cache is not None

    for seed in seeds:
        if seed not in values:
            continue

        neighbours = adjacency.get(seed, [])
        if not neighbours:
            new_values[seed] = values[seed]
            continue

        # Collect neighbour values and distances
        nvals = []
        ndists = []
        for nb in neighbours:
            if nb in values:
                nvals.append(values[nb])
                if use_cache:
                    ndists.append(dist_cache.get((seed, nb), _compute_distance(seed, nb)))
                else:
                    ndists.append(_compute_distance(seed, nb))

        if not nvals:
            new_values[seed] = values[seed]
            continue

        if config.include_self:
            nvals_with_self = [values[seed]] + nvals
            ndists_with_self = [0.0] + ndists
        else:
            nvals_with_self = nvals
            ndists_with_self = ndists

        # Compute smoothed value via strategy function
        smoothed = strategy(nvals_with_self, ndists_with_self, config)
        if smoothed is None:
            smoothed = values[seed]

        # Blend with original
        blended = config.alpha * smoothed + (1.0 - config.alpha) * values[seed]
        change = abs(blended - values[seed])
        if change > max_change:
            max_change = change
        new_values[seed] = blended

    return new_values, max_change


def smooth_attributes(data, regions, values, config=None):
    """Smooth attribute values across Voronoi cell neighbourhoods.

    Parameters
    ----------
    data : list of (float, float)
        Seed points (from ``vormap.load_data``).
    regions : dict
        Voronoi regions (from ``compute_regions``).
    values : dict or list
        Attribute values. If dict: seed -> value. If list: value per
        seed point in same order as *data*.
    config : SmoothConfig or None
        Configuration. Uses defaults if None.

    Returns
    -------
    SmoothResult
        The smoothing result with original and smoothed values.

    Raises
    ------
    ValueError
        If *values* length doesn't match *data*, or inputs are empty.
    """
    if config is None:
        config = SmoothConfig()

    if not data:
        raise ValueError("data must not be empty")
    if not regions:
        raise ValueError("regions must not be empty")

    # Convert list values to dict
    if isinstance(values, (list, tuple)):
        if len(values) != len(data):
            raise ValueError(
                "values list length (%d) must match data length (%d)"
                % (len(values), len(data))
            )
        val_dict = {tuple(data[i]): float(values[i]) for i in range(len(data))}
    elif isinstance(values, dict):
        val_dict = {k: float(v) for k, v in values.items()}
    else:
        raise ValueError("values must be a dict or list, got %s" % type(values))

    # Build adjacency graph
    if not _HAS_GRAPH:
        raise ImportError(
            "vormap_graph is required for spatial smoothing. "
            "Ensure vormap_graph.py is available."
        )

    graph = extract_neighborhood_graph(regions, data)
    adjacency = graph["adjacency"]

    # Store originals
    original = dict(val_dict)
    seeds = list(val_dict.keys())

    # Pre-compute inter-seed distances once (avoids redundant sqrt calls
    # across iterations — especially beneficial for gaussian/IDW with
    # many passes).
    dist_cache = _precompute_distances(adjacency, set(seeds))

    # Iterative smoothing
    current = dict(val_dict)
    convergence = []

    for i in range(config.iterations):
        current, max_change = _smooth_once(
            current, adjacency, seeds, config, dist_cache
        )
        convergence.append(max_change)

    return SmoothResult(
        original=original,
        smoothed=current,
        config=config,
        iterations_applied=config.iterations,
        convergence=convergence,
    )


def smooth_from_file(filename, value_file=None, config=None, value_column=0):
    """Convenience: load data from file, generate values, and smooth.

    Parameters
    ----------
    filename : str
        Path to point data file (passed to ``vormap.load_data``).
    value_file : str or None
        Optional CSV file with attribute values. If None, uses y-coordinate
        as the attribute (useful for testing/demos).
    config : SmoothConfig or None
        Smoothing configuration.
    value_column : int
        Column index for values in the CSV file (default 0).

    Returns
    -------
    SmoothResult
    """
    data = vormap.load_data(filename)

    if not _HAS_VIZ:
        raise ImportError(
            "vormap_viz is required for region computation. "
            "Ensure vormap_viz.py is available."
        )

    regions = compute_regions(data)

    if value_file is not None:
        resolved = vormap.validate_input_path(
            value_file, base_dir="data", allow_absolute=True
        )
        values = {}
        with open(resolved, "r") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    x, y = float(row[0]), float(row[1])
                    val = float(row[value_column + 2]) if len(row) > value_column + 2 else float(row[-1])
                    values[(x, y)] = val
    else:
        # Default: use y-coordinate as attribute value
        values = {tuple(pt): pt[1] for pt in data}

    return smooth_attributes(data, regions, values, config)


def export_csv(result, output_path):
    """Export smoothed values to CSV.

    Columns: x, y, original, smoothed, change
    """
    validated = vormap.validate_output_path(output_path)
    with open(validated, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["x", "y", "original", "smoothed", "change"])
        for seed in sorted(result.original.keys()):
            orig = result.original[seed]
            smooth = result.smoothed.get(seed, orig)
            writer.writerow([
                seed[0], seed[1],
                "%.6f" % orig,
                "%.6f" % smooth,
                "%.6f" % abs(smooth - orig),
            ])


def export_json(result, output_path):
    """Export smoothed values to JSON."""
    validated = vormap.validate_output_path(output_path)
    records = []
    for seed in sorted(result.original.keys()):
        orig = result.original[seed]
        smooth = result.smoothed.get(seed, orig)
        records.append({
            "x": seed[0],
            "y": seed[1],
            "original": round(orig, 6),
            "smoothed": round(smooth, 6),
            "change": round(abs(smooth - orig), 6),
        })

    output = {
        "config": {
            "method": result.config.method,
            "iterations": result.config.iterations,
            "alpha": result.config.alpha,
        },
        "summary": {
            "points": len(records),
            "convergence": [round(c, 6) for c in result.convergence],
        },
        "data": records,
    }

    with open(validated, "w") as f:
        json.dump(output, f, indent=2)


def export_svg(result, regions, output_path, *, width=800, height=600,
               colormap="heat", show_original=False):
    """Export a smoothed heatmap as SVG.

    Colors cells by their smoothed (or original) attribute value.

    Parameters
    ----------
    result : SmoothResult
    regions : dict
        Voronoi regions for polygon rendering.
    output_path : str
    width, height : int
    colormap : str
        Color scheme: 'heat' (red-yellow), 'cool' (blue-green),
        'diverging' (blue-white-red), 'grayscale'.
    show_original : bool
        If True, color by original values instead of smoothed.
    """
    validated = vormap.validate_output_path(output_path)

    values = result.original if show_original else result.smoothed
    if not values:
        return

    vmin = min(values.values())
    vmax = max(values.values())
    vrange = vmax - vmin if vmax > vmin else 1.0

    # Compute bounding box from regions
    all_x = []
    all_y = []
    for verts in regions.values():
        for vx, vy in verts:
            all_x.append(vx)
            all_y.append(vy)

    if not all_x:
        return

    data_xmin, data_xmax = min(all_x), max(all_x)
    data_ymin, data_ymax = min(all_y), max(all_y)
    data_w = data_xmax - data_xmin or 1.0
    data_h = data_ymax - data_ymin or 1.0

    margin = 20
    scale_x = (width - 2 * margin) / data_w
    scale_y = (height - 2 * margin) / data_h
    scale = min(scale_x, scale_y)

    def tx(x):
        return margin + (x - data_xmin) * scale

    def ty(y):
        return margin + (data_ymax - y) * scale  # flip y

    def value_color(v):
        t = (v - vmin) / vrange
        t = max(0.0, min(1.0, t))
        if colormap == "heat":
            r = int(255 * min(1.0, t * 2))
            g = int(255 * min(1.0, t * 3 - 1)) if t > 0.33 else 0
            b = 0
            return "rgb(%d,%d,%d)" % (r, g, b)
        elif colormap == "cool":
            r = 0
            g = int(255 * t)
            b = int(255 * (1.0 - t))
            return "rgb(%d,%d,%d)" % (r, g, b)
        elif colormap == "diverging":
            if t < 0.5:
                s = t * 2
                r = int(255 * s)
                g = int(255 * s)
                b = 255
            else:
                s = (t - 0.5) * 2
                r = 255
                g = int(255 * (1.0 - s))
                b = int(255 * (1.0 - s))
            return "rgb(%d,%d,%d)" % (r, g, b)
        else:  # grayscale
            c = int(255 * t)
            return "rgb(%d,%d,%d)" % (c, c, c)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"'
        ' viewBox="0 0 %d %d">' % (width, height, width, height),
        '<rect width="100%%" height="100%%" fill="white"/>',
    ]

    # Draw cells
    for seed, verts in sorted(regions.items()):
        if seed not in values:
            continue
        val = values[seed]
        color = value_color(val)
        pts = " ".join("%.1f,%.1f" % (tx(vx), ty(vy)) for vx, vy in verts)
        lines.append(
            '<polygon points="%s" fill="%s" stroke="#333" stroke-width="0.5"'
            ' opacity="0.85"/>' % (pts, color)
        )

    # Draw seed points
    for seed in sorted(values.keys()):
        sx, sy = tx(seed[0]), ty(seed[1])
        lines.append(
            '<circle cx="%.1f" cy="%.1f" r="2" fill="black" opacity="0.5"/>'
            % (sx, sy)
        )

    # Legend
    legend_x = width - 120
    legend_y = 30
    for i in range(10):
        t = i / 9.0
        v = vmin + t * vrange
        color = value_color(v)
        y = legend_y + i * 15
        lines.append(
            '<rect x="%d" y="%d" width="15" height="13" fill="%s" '
            'stroke="#666" stroke-width="0.5"/>' % (legend_x, y, color)
        )
        lines.append(
            '<text x="%d" y="%d" font-size="9" fill="#333">%.1f</text>'
            % (legend_x + 20, y + 10, v)
        )

    title = "Smoothed" if not show_original else "Original"
    lines.append(
        '<text x="%d" y="%d" font-size="11" font-weight="bold" fill="#333">'
        '%s (%s, iter=%d)</text>'
        % (10, 15, title, result.config.method, result.config.iterations)
    )

    lines.append("</svg>")

    with open(validated, "w") as f:
        f.write("\n".join(lines))


# ── CLI ──────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        description="Spatial smoothing of Voronoi cell attributes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python vormap_smooth.py data/sample.txt\n"
            "  python vormap_smooth.py data/sample.txt --method gaussian --iterations 3\n"
            "  python vormap_smooth.py data/sample.txt --method median --alpha 0.5 -o out.csv\n"
            "  python vormap_smooth.py data/sample.txt --svg smooth.svg --colormap cool\n"
        ),
    )
    p.add_argument("datafile", help="Point data file")
    p.add_argument("-o", "--output", help="Output CSV file")
    p.add_argument("--json", dest="json_output", help="Output JSON file")
    p.add_argument("--svg", dest="svg_output", help="Output SVG heatmap")
    p.add_argument(
        "--method", default="mean",
        choices=["mean", "median", "gaussian", "inverse_distance"],
        help="Smoothing method (default: mean)",
    )
    p.add_argument(
        "--iterations", type=int, default=1,
        help="Number of smoothing passes (default: 1)",
    )
    p.add_argument(
        "--alpha", type=float, default=1.0,
        help="Blending factor 0-1 (default: 1.0)",
    )
    p.add_argument(
        "--sigma", type=float, default=100.0,
        help="Gaussian kernel bandwidth (default: 100.0)",
    )
    p.add_argument(
        "--power", type=float, default=2.0,
        help="IDW power parameter (default: 2.0)",
    )
    p.add_argument(
        "--no-self", dest="include_self", action="store_false",
        help="Exclude cell's own value from smoothing",
    )
    p.add_argument(
        "--colormap", default="heat",
        choices=["heat", "cool", "diverging", "grayscale"],
        help="SVG colormap (default: heat)",
    )
    p.add_argument(
        "--values", dest="value_file",
        help="CSV file with attribute values (x, y, value columns)",
    )
    return p


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    config = SmoothConfig(
        method=args.method,
        iterations=args.iterations,
        alpha=args.alpha,
        sigma=args.sigma,
        power=args.power,
        include_self=args.include_self,
    )

    result = smooth_from_file(
        args.datafile,
        value_file=args.value_file,
        config=config,
    )

    print(result.summary())

    if args.output:
        export_csv(result, args.output)
        print("\nCSV exported to: %s" % args.output)

    if args.json_output:
        export_json(result, args.json_output)
        print("JSON exported to: %s" % args.json_output)

    if args.svg_output:
        if not _HAS_VIZ:
            print("Warning: vormap_viz required for SVG export")
        else:
            data = vormap.load_data(args.datafile)
            regions = compute_regions(data)
            export_svg(result, regions, args.svg_output, colormap=args.colormap)
            print("SVG exported to: %s" % args.svg_output)


if __name__ == "__main__":
    main()
