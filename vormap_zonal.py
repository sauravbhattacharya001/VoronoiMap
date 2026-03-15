"""Zonal statistics for Voronoi tessellations.

Assigns observation points to their nearest Voronoi seed (zone) and
computes aggregate statistics per zone.  This is the spatial equivalent
of a GROUP BY — a fundamental GIS operation for summarising point data
across Voronoi regions.

Supports:
- Per-zone statistics: count, sum, mean, median, std, min, max, range, variance
- Multiple value fields (multi-band)
- CSV and JSON export of results
- SVG choropleth map coloured by any statistic
- CLI interface

Functions
---------
- ``assign_zones`` — Map each observation to its nearest seed zone.
- ``zonal_stats`` — Compute per-zone statistics.
- ``export_csv`` — Write zonal statistics to CSV.
- ``export_json`` — Write zonal statistics to JSON.
- ``export_svg`` — Choropleth SVG coloured by a chosen statistic.

Example::

    from vormap_zonal import zonal_stats, export_csv

    seeds = [(100, 200), (300, 400), (500, 100)]
    observations = [(110, 210, 25.0), (120, 190, 30.0),
                    (310, 390, 18.0), (490, 110, 22.0)]
    # observations are (x, y, value)

    stats = zonal_stats(seeds, observations)
    export_csv(stats, 'zonal_output.csv')
"""

import math
import os
import json
import argparse
import sys

try:
    import numpy as np
    from scipy.spatial import KDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


STAT_NAMES = ('count', 'sum', 'mean', 'median', 'std', 'min', 'max',
              'range', 'variance')


def _nearest_seed_index(seeds, x, y):
    """Return the index of the nearest seed to (x, y) via brute force."""
    best_i = 0
    best_d = float('inf')
    for i, (sx, sy) in enumerate(seeds):
        d = (x - sx) ** 2 + (y - sy) ** 2
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def assign_zones(seeds, observations, value_columns=1):
    """Assign each observation to its nearest Voronoi seed zone.

    Parameters
    ----------
    seeds : list of (x, y) tuples
        Voronoi seed (generator) coordinates.
    observations : list of tuples
        Each tuple is ``(x, y, v1, v2, ...)`` where the first two
        elements are coordinates and the rest are value fields.
    value_columns : int
        Number of value fields after x, y (default 1).

    Returns
    -------
    dict
        Mapping from seed index → list of value tuples.
        Each value tuple contains *value_columns* floats.
    """
    if not seeds:
        raise ValueError("seeds must not be empty")
    if not observations:
        return {i: [] for i in range(len(seeds))}

    zones = {i: [] for i in range(len(seeds))}

    # Use KDTree for fast assignment if available
    if _HAS_SCIPY:
        seed_arr = np.array(seeds)
        tree = KDTree(seed_arr)
        for obs in observations:
            x, y = obs[0], obs[1]
            vals = tuple(float(v) for v in obs[2:2 + value_columns])
            _, idx = tree.query([x, y])
            zones[int(idx)].append(vals)
    else:
        for obs in observations:
            x, y = obs[0], obs[1]
            vals = tuple(float(v) for v in obs[2:2 + value_columns])
            idx = _nearest_seed_index(seeds, x, y)
            zones[idx].append(vals)

    return zones


def _compute_stats(values):
    """Compute statistics for a list of numeric values.

    Returns a dict with keys from STAT_NAMES.  Returns all-None for
    empty input.
    """
    if not values:
        return {k: None for k in STAT_NAMES}

    n = len(values)
    s = sum(values)
    mean = s / n
    sorted_v = sorted(values)
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0
    mn = sorted_v[0]
    mx = sorted_v[-1]
    variance = sum((v - mean) ** 2 for v in values) / n if n > 0 else 0.0
    std = math.sqrt(variance)

    return {
        'count': n,
        'sum': s,
        'mean': mean,
        'median': median,
        'std': std,
        'min': mn,
        'max': mx,
        'range': mx - mn,
        'variance': variance,
    }


def zonal_stats(seeds, observations, value_columns=1, stats=None):
    """Compute per-zone aggregate statistics.

    Parameters
    ----------
    seeds : list of (x, y)
        Voronoi seed coordinates.
    observations : list of tuples
        ``(x, y, v1, v2, ...)`` observation points with values.
    value_columns : int
        Number of value fields (default 1).
    stats : list of str or None
        Which statistics to compute.  None = all.

    Returns
    -------
    list of dict
        One dict per zone with keys: ``zone`` (index), ``seed_x``,
        ``seed_y``, and per-value-column statistics.
    """
    if stats is not None:
        for s in stats:
            if s not in STAT_NAMES:
                raise ValueError("Unknown statistic '%s'. Choose from: %s"
                                 % (s, ', '.join(STAT_NAMES)))

    zones = assign_zones(seeds, observations, value_columns)
    results = []

    for i, (sx, sy) in enumerate(seeds):
        entry = {'zone': i, 'seed_x': sx, 'seed_y': sy}
        zone_vals = zones[i]

        for col in range(value_columns):
            col_values = [v[col] for v in zone_vals] if zone_vals else []
            col_stats = _compute_stats(col_values)

            prefix = '' if value_columns == 1 else 'v%d_' % (col + 1)
            for stat_name, stat_val in col_stats.items():
                if stats is None or stat_name in stats:
                    entry[prefix + stat_name] = stat_val

        results.append(entry)

    return results


def export_csv(results, filepath):
    """Write zonal statistics to CSV.

    Parameters
    ----------
    results : list of dict
        Output from :func:`zonal_stats`.
    filepath : str
        Path to write the CSV file.
    """
    if not results:
        return

    keys = list(results[0].keys())
    lines = [','.join(keys)]
    for row in results:
        vals = []
        for k in keys:
            v = row.get(k)
            if v is None:
                vals.append('')
            elif isinstance(v, float):
                vals.append('%.6f' % v)
            else:
                vals.append(str(v))
        lines.append(','.join(vals))

    with open(filepath, 'w', newline='') as f:
        f.write('\n'.join(lines) + '\n')


def export_json(results, filepath, indent=2):
    """Write zonal statistics to JSON.

    Parameters
    ----------
    results : list of dict
        Output from :func:`zonal_stats`.
    filepath : str
        Path to write the JSON file.
    """
    with open(filepath, 'w') as f:
        json.dump(results, f, indent=indent)


def export_svg(results, seeds, filepath, stat_key='mean',
               width=800, height=600, title=None):
    """Generate a choropleth SVG showing per-zone statistics.

    Each seed is drawn as a circle coloured on a blue-to-red gradient
    based on the chosen statistic.

    Parameters
    ----------
    results : list of dict
        Output from :func:`zonal_stats`.
    seeds : list of (x, y)
        Original seed coordinates (used for positioning).
    filepath : str
        Path to write the SVG file.
    stat_key : str
        Statistic key to colour by (default ``'mean'``).
    width, height : int
        SVG canvas dimensions.
    title : str or None
        Optional title above the map.
    """
    # Gather stat values for colour scaling
    stat_values = [r.get(stat_key) for r in results]
    numeric_vals = [v for v in stat_values if v is not None]
    if not numeric_vals:
        vmin = vmax = 0
    else:
        vmin = min(numeric_vals)
        vmax = max(numeric_vals)

    # Compute coordinate transform
    if not seeds:
        return
    xs = [s[0] for s in seeds]
    ys = [s[1] for s in seeds]
    data_w = max(xs) - min(xs) or 1
    data_h = max(ys) - min(ys) or 1
    margin = 60
    scale_x = (width - 2 * margin) / data_w
    scale_y = (height - 2 * margin) / data_h
    scale = min(scale_x, scale_y)
    ox = min(xs)
    oy = min(ys)

    def tx(x):
        return margin + (x - ox) * scale

    def ty(y):
        return height - margin - (y - oy) * scale

    def colour(val):
        if val is None or vmax == vmin:
            return '#cccccc'
        t = (val - vmin) / (vmax - vmin)
        r = int(50 + 205 * t)
        b = int(50 + 205 * (1 - t))
        return '#%02x30%02x' % (r, b)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d"'
        ' viewBox="0 0 %d %d">' % (width, height, width, height),
        '<rect width="100%%" height="100%%" fill="#f8f8f8"/>',
    ]

    if title:
        lines.append(
            '<text x="%d" y="30" text-anchor="middle" font-size="16"'
            ' font-family="sans-serif" font-weight="bold">%s</text>'
            % (width // 2, title)
        )

    # Draw zone circles
    radius = max(8, min(30, int(scale * data_w / len(seeds) * 0.4)))
    for i, (sx, sy) in enumerate(seeds):
        val = stat_values[i]
        cx, cy = tx(sx), ty(sy)
        fill = colour(val)
        label = '%.2f' % val if val is not None else 'N/A'
        lines.append(
            '<circle cx="%.1f" cy="%.1f" r="%d" fill="%s" stroke="#333"'
            ' stroke-width="1" opacity="0.85">'
            '<title>Zone %d: %s=%s</title></circle>'
            % (cx, cy, radius, fill, i, stat_key, label)
        )
        # Small label
        lines.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" font-size="9"'
            ' font-family="sans-serif" fill="#222">%s</text>'
            % (cx, cy + 3, label)
        )

    # Legend
    legend_y = height - 25
    for j in range(11):
        t = j / 10.0
        lx = margin + j * 30
        fill = colour(vmin + t * (vmax - vmin) if vmax != vmin else vmin)
        lines.append(
            '<rect x="%.0f" y="%d" width="28" height="12" fill="%s"/>'
            % (lx, legend_y, fill)
        )
    lines.append(
        '<text x="%d" y="%d" font-size="9" font-family="sans-serif">'
        '%.1f</text>' % (margin, legend_y - 2, vmin)
    )
    lines.append(
        '<text x="%d" y="%d" font-size="9" font-family="sans-serif">'
        '%.1f</text>' % (margin + 310, legend_y - 2, vmax)
    )

    lines.append('</svg>')

    with open(filepath, 'w') as f:
        f.write('\n'.join(lines))


def main(argv=None):
    """CLI entry point for zonal statistics."""
    parser = argparse.ArgumentParser(
        description='Compute zonal statistics for Voronoi tessellations.'
    )
    parser.add_argument('seeds_file',
                        help='CSV/JSON file of seed points (x,y per row)')
    parser.add_argument('observations_file',
                        help='CSV file of observations (x,y,value per row)')
    parser.add_argument('--values', type=int, default=1,
                        help='Number of value columns after x,y (default: 1)')
    parser.add_argument('--stats', nargs='+', choices=STAT_NAMES,
                        help='Statistics to compute (default: all)')
    parser.add_argument('--csv', metavar='FILE',
                        help='Export results to CSV')
    parser.add_argument('--json', metavar='FILE',
                        help='Export results to JSON')
    parser.add_argument('--svg', metavar='FILE',
                        help='Export choropleth SVG')
    parser.add_argument('--color-by', default='mean',
                        help='Statistic for SVG colouring (default: mean)')
    parser.add_argument('--title',
                        help='SVG chart title')

    args = parser.parse_args(argv)

    # Load seeds
    seeds = _load_points_file(args.seeds_file)
    # Load observations (with values)
    observations = _load_observations_file(args.observations_file,
                                           args.values)

    results = zonal_stats(seeds, observations,
                          value_columns=args.values,
                          stats=args.stats)

    # Print summary table
    _print_table(results)

    if args.csv:
        export_csv(results, args.csv)
        print("CSV written to %s" % args.csv)
    if args.json:
        export_json(results, args.json)
        print("JSON written to %s" % args.json)
    if args.svg:
        export_svg(results, seeds, args.svg,
                   stat_key=args.color_by, title=args.title)
        print("SVG written to %s" % args.svg)


def _load_points_file(filepath):
    """Load (x, y) points from CSV or JSON."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.json':
        with open(filepath) as f:
            data = json.load(f)
        return [(float(p[0]), float(p[1])) for p in data]
    else:
        # CSV
        points = []
        with open(filepath) as f:
            for lineno, line in enumerate(f):
                line = line.strip()
                if not line or (lineno == 0 and not line[0].isdigit()):
                    continue  # skip header or empty
                parts = line.split(',')
                points.append((float(parts[0]), float(parts[1])))
        return points


def _load_observations_file(filepath, value_columns):
    """Load observations (x, y, v1, ...) from CSV."""
    observations = []
    with open(filepath) as f:
        for lineno, line in enumerate(f):
            line = line.strip()
            if not line or (lineno == 0 and not line[0].isdigit()):
                continue
            parts = line.split(',')
            row = tuple(float(p) for p in parts[:2 + value_columns])
            observations.append(row)
    return observations


def _print_table(results):
    """Print a formatted summary table to stdout."""
    if not results:
        print("No results.")
        return

    keys = list(results[0].keys())
    col_widths = {k: max(len(k), 10) for k in keys}

    # Header
    header = ' | '.join(k.rjust(col_widths[k]) for k in keys)
    print(header)
    print('-' * len(header))

    for row in results:
        cells = []
        for k in keys:
            v = row.get(k)
            if v is None:
                cells.append(''.rjust(col_widths[k]))
            elif isinstance(v, float):
                cells.append(('%.4f' % v).rjust(col_widths[k]))
            else:
                cells.append(str(v).rjust(col_widths[k]))
        print(' | '.join(cells))


if __name__ == '__main__':
    main()
