"""Zonal Statistics — aggregate observations within Voronoi regions.

Given a set of Voronoi seed points defining regions and a separate set of
observation points with numeric values, compute aggregate statistics (mean,
sum, min, max, median, std, count, percentiles) for the observations that
fall inside each Voronoi zone.

This is a fundamental GIS operation (analogous to ``rasterstats`` or QGIS
Zonal Statistics) applied to the Voronoi tessellation.

Usage (Python API)::

    import vormap
    from vormap_viz import compute_regions
    from vormap_zonalstats import (
        zonal_statistics, export_zonal_csv, export_zonal_json,
        export_choropleth_svg,
    )

    # Define zones from seed points
    seeds = vormap.load_data("seeds.txt")
    regions = compute_regions(seeds)

    # Observation points with values  [(x, y, value), ...]
    observations = [(150, 250, 42.0), (160, 260, 38.0), ...]

    stats = zonal_statistics(regions, seeds, observations)
    for s in stats:
        print(f"Zone {s['zone_id']}: n={s['count']}, mean={s['mean']:.2f}")

    export_zonal_csv(stats, "zonal.csv")
    export_choropleth_svg(stats, regions, seeds, "choropleth.svg")

CLI::

    python vormap_zonalstats.py seeds.txt observations.txt
    python vormap_zonalstats.py seeds.txt obs.txt --csv zonal.csv
    python vormap_zonalstats.py seeds.txt obs.txt --json zonal.json
    python vormap_zonalstats.py seeds.txt obs.txt --svg choropleth.svg
    python vormap_zonalstats.py seeds.txt obs.txt --percentiles 10 25 75 90
    python vormap_zonalstats.py seeds.txt obs.txt --stat mean --svg map.svg

Observation file format: one ``x y value`` triple per line (whitespace-
separated).  Lines starting with ``#`` are ignored.
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import vormap
from vormap_geometry import polygon_area, polygon_centroid


# ── Point-in-polygon ────────────────────────────────────────────────

def _point_in_polygon(px, py, vertices):
    """Ray-casting point-in-polygon test."""
    n = len(vertices)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ── Assign observations to zones ────────────────────────────────────

def _assign_observations(regions, observations):
    """Assign each observation to the Voronoi zone containing it.

    Uses a KDTree (when scipy is available) for O(log n) nearest-seed
    lookup per observation, then verifies with a point-in-polygon test
    on the nearest seed's region and its immediate neighbours.  Falls
    back to brute-force for small zone counts or when scipy is absent.

    This reduces assignment cost from O(obs * zones) to approximately
    O(obs * log(zones)) for large datasets.
    """
    zone_values = {seed: [] for seed in regions}
    seeds = list(regions.keys())

    # Fast path: use KDTree to narrow candidate zones
    try:
        from scipy.spatial import cKDTree
        _use_kdtree = len(seeds) > 20
    except ImportError:
        _use_kdtree = False

    if _use_kdtree:
        import numpy as np
        seed_array = np.array(seeds, dtype=float)
        tree = cKDTree(seed_array)
        # Query k nearest seeds; check polygon containment on those
        k = min(len(seeds), 5)

        for ox, oy, val in observations:
            _, idxs = tree.query([ox, oy], k=k)
            if k == 1:
                idxs = [int(idxs)]
            else:
                idxs = [int(i) for i in idxs]

            assigned = False
            for idx in idxs:
                seed = seeds[idx]
                if _point_in_polygon(ox, oy, regions[seed]):
                    zone_values[seed].append(val)
                    assigned = True
                    break
            if not assigned:
                # Nearest seed fallback
                zone_values[seeds[idxs[0]]].append(val)
    else:
        for ox, oy, val in observations:
            assigned = False
            for seed in seeds:
                verts = regions[seed]
                if _point_in_polygon(ox, oy, verts):
                    zone_values[seed].append(val)
                    assigned = True
                    break
            if not assigned:
                best_seed = min(seeds, key=lambda s: (s[0] - ox) ** 2 + (s[1] - oy) ** 2)
                zone_values[best_seed].append(val)

    return zone_values


# ── Statistics computation ──────────────────────────────────────────

def _median(values):
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _percentile(values, p):
    """Linear interpolation percentile (0-100 scale)."""
    s = sorted(values)
    n = len(s)
    if n == 1:
        return s[0]
    k = (p / 100.0) * (n - 1)
    lo = int(math.floor(k))
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return s[lo] + frac * (s[hi] - s[lo])


def _std(values, mean):
    if len(values) < 2:
        return 0.0
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _compute_zone_stats(values, percentiles=()):
    """Compute aggregate statistics for a list of values."""
    if not values:
        result = {
            "count": 0,
            "sum": 0.0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "std": None,
            "range": None,
        }
        for p in percentiles:
            result["p%d" % int(p)] = None
        return result

    s = sum(values)
    m = s / len(values)
    result = {
        "count": len(values),
        "sum": round(s, 6),
        "mean": round(m, 6),
        "median": round(_median(values), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
        "std": round(_std(values, m), 6),
        "range": round(max(values) - min(values), 6),
    }
    for p in percentiles:
        result["p%d" % int(p)] = round(_percentile(values, p), 6)
    return result


# ── Public API ──────────────────────────────────────────────────────

def zonal_statistics(regions, seeds, observations, *, percentiles=()):
    """Compute zonal statistics for observations within Voronoi regions.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed -> vertex list.
    seeds : list of (float, float)
        All Voronoi seed points.
    observations : list of (float, float, float)
        Observation triples ``(x, y, value)``.
    percentiles : sequence of float
        Additional percentiles to compute (0-100 scale).

    Returns
    -------
    list of dict
        One entry per zone with keys: ``zone_id``, ``seed_x``, ``seed_y``,
        ``area``, ``count``, ``density``, ``sum``, ``mean``, ``median``,
        ``min``, ``max``, ``std``, ``range``, and any requested percentiles.
    """
    zone_values = _assign_observations(regions, observations)

    results = []
    sorted_seeds = sorted(regions.keys())

    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        area = polygon_area(verts)
        vals = zone_values[seed]
        stats = _compute_zone_stats(vals, percentiles)
        stats["zone_id"] = idx + 1
        stats["seed_x"] = round(seed[0], 6)
        stats["seed_y"] = round(seed[1], 6)
        stats["area"] = round(area, 4)
        stats["density"] = round(len(vals) / area, 8) if area > 0 else 0.0
        results.append(stats)

    return results


def load_observations(filepath):
    """Load observation points from a text file.

    Format: one ``x y value`` triple per line. Lines starting with ``#``
    or empty lines are skipped.
    """
    safe = vormap.validate_input_path(filepath, allow_absolute=True)
    observations = []
    with open(safe, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:
                    x, y, val = float(parts[0]), float(parts[1]), float(parts[2])
                    observations.append((x, y, val))
                except ValueError:
                    continue
    return observations


# ── Export: CSV ──────────────────────────────────────────────────────

def export_zonal_csv(stats, output_path):
    """Export zonal statistics to CSV.

    Returns the absolute path of the written file.
    """
    if not stats:
        raise ValueError("No zonal statistics to export")

    safe = vormap.validate_input_path(output_path, allow_absolute=True)

    fixed = ["zone_id", "seed_x", "seed_y", "area", "density",
             "count", "sum", "mean", "median", "min", "max", "std", "range"]
    pct_cols = sorted(k for k in stats[0] if k.startswith("p") and k[1:].isdigit())
    columns = fixed + pct_cols

    with open(safe, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in stats:
            writer.writerow(row)

    return os.path.abspath(safe)


# ── Export: JSON ────────────────────────────────────────────────────

def export_zonal_json(stats, output_path):
    """Export zonal statistics to JSON."""
    if not stats:
        raise ValueError("No zonal statistics to export")

    safe = vormap.validate_input_path(output_path, allow_absolute=True)

    total_obs = sum(s["count"] for s in stats)
    non_empty = sum(1 for s in stats if s["count"] > 0)

    output = {
        "summary": {
            "total_zones": len(stats),
            "non_empty_zones": non_empty,
            "total_observations": total_obs,
        },
        "zones": stats,
    }

    with open(safe, "w") as f:
        json.dump(output, f, indent=2)

    return os.path.abspath(safe)


# ── Export: SVG Choropleth ──────────────────────────────────────────

def _value_to_color(value, vmin, vmax, stat="mean"):
    """Map a value to a color using a sequential palette."""
    if vmin == vmax:
        return "#4393c3"
    t = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))

    if t < 0.25:
        r, g, b = 49, 130, 189
        r2, g2, b2 = 107, 174, 214
        f = t / 0.25
    elif t < 0.5:
        r, g, b = 107, 174, 214
        r2, g2, b2 = 253, 208, 102
        f = (t - 0.25) / 0.25
    elif t < 0.75:
        r, g, b = 253, 208, 102
        r2, g2, b2 = 239, 138, 71
        f = (t - 0.5) / 0.25
    else:
        r, g, b = 239, 138, 71
        r2, g2, b2 = 215, 48, 39
        f = (t - 0.75) / 0.25

    cr = int(r + (r2 - r) * f)
    cg = int(g + (g2 - g) * f)
    cb = int(b + (b2 - b) * f)
    return "#%02x%02x%02x" % (cr, cg, cb)


def export_choropleth_svg(stats, regions, seeds, output_path, *,
                          width=900, height=700, margin=50,
                          stat="mean", title=None):
    """Export a choropleth SVG map colored by a zonal statistic.

    Parameters
    ----------
    stats : list of dict
        Output of ``zonal_statistics()``.
    regions : dict
        Voronoi regions (seed -> vertices).
    seeds : list of (float, float)
        Seed points.
    output_path : str
        SVG file path.
    stat : str
        Statistic to visualize: ``mean``, ``sum``, ``count``, ``density``,
        ``median``, ``min``, ``max``, ``std``, or any percentile key.
    title : str or None
        Optional title for the map.

    Returns
    -------
    str
        Absolute path of the written SVG file.
    """
    safe = vormap.validate_input_path(output_path, allow_absolute=True)

    # Build lookup: (seed_x, seed_y) -> stat value
    stat_map = {}
    for s in stats:
        key = (s["seed_x"], s["seed_y"])
        stat_map[key] = s.get(stat)

    # Compute bounds
    all_xs, all_ys = [], []
    for verts in regions.values():
        for x, y in verts:
            all_xs.append(x)
            all_ys.append(y)

    if not all_xs:
        raise ValueError("No region vertices to plot")

    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)
    data_w = max_x - min_x or 1.0
    data_h = max_y - min_y or 1.0
    draw_w = width - 2 * margin
    draw_h = height - 2 * margin - 60
    scale = min(draw_w / data_w, draw_h / data_h)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        return margin + (max_y - y) * scale

    # Color range
    vals = [v for v in stat_map.values() if v is not None]
    vmin = min(vals) if vals else 0
    vmax = max(vals) if vals else 1

    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">' % (width, height),
        '<rect width="%d" height="%d" fill="#ffffff"/>' % (width, height),
    ]

    if title:
        lines.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" font-size="16" '
            'font-weight="bold" font-family="sans-serif">%s</text>'
            % (width / 2, margin / 2 + 5, title)
        )

    sorted_seeds_list = sorted(regions.keys())
    for seed in sorted_seeds_list:
        verts = regions[seed]
        val = stat_map.get((round(seed[0], 6), round(seed[1], 6)))
        if val is None:
            val = stat_map.get(seed)

        if val is not None:
            fill = _value_to_color(val, vmin, vmax, stat)
        else:
            fill = "#cccccc"

        points_str = " ".join("%.1f,%.1f" % (tx(x), ty(y)) for x, y in verts)
        lines.append(
            '<polygon points="%s" fill="%s" stroke="#333333" '
            'stroke-width="0.5" opacity="0.85"/>' % (points_str, fill)
        )

    for seed in sorted_seeds_list:
        sx, sy = tx(seed[0]), ty(seed[1])
        lines.append('<circle cx="%.1f" cy="%.1f" r="2" fill="#000"/>' % (sx, sy))

    # Legend bar
    legend_y = height - 45
    legend_x = margin
    legend_w = width - 2 * margin
    n_stops = 20
    for i in range(n_stops):
        t = i / n_stops
        v = vmin + t * (vmax - vmin)
        c = _value_to_color(v, vmin, vmax, stat)
        bx = legend_x + (i / n_stops) * legend_w
        bw = legend_w / n_stops + 1
        lines.append(
            '<rect x="%.1f" y="%d" width="%.1f" height="15" fill="%s"/>'
            % (bx, legend_y, bw, c)
        )

    lines.append(
        '<text x="%d" y="%d" font-size="11" font-family="sans-serif">%.2f</text>'
        % (legend_x, legend_y + 30, vmin)
    )
    lines.append(
        '<text x="%d" y="%d" text-anchor="end" font-size="11" '
        'font-family="sans-serif">%.2f</text>'
        % (legend_x + legend_w, legend_y + 30, vmax)
    )
    lines.append(
        '<text x="%.1f" y="%d" text-anchor="middle" font-size="11" '
        'font-weight="bold" font-family="sans-serif">%s</text>'
        % (legend_x + legend_w / 2, legend_y + 30, stat.upper())
    )

    lines.append("</svg>")

    with open(safe, "w") as f:
        f.write("\n".join(lines))

    return os.path.abspath(safe)


# ── CLI ─────────────────────────────────────────────────────────────

def _cli():
    """Command-line interface for zonal statistics."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Zonal Statistics -- aggregate observations within Voronoi regions",
    )
    parser.add_argument("seeds", help="Seed points file (vormap format)")
    parser.add_argument("observations", help="Observation file (x y value per line)")
    parser.add_argument("--csv", metavar="PATH", help="Export CSV")
    parser.add_argument("--json", metavar="PATH", help="Export JSON")
    parser.add_argument("--svg", metavar="PATH", help="Export choropleth SVG")
    parser.add_argument(
        "--stat", default="mean",
        help="Statistic for choropleth coloring (default: mean)",
    )
    parser.add_argument(
        "--percentiles", nargs="+", type=float, default=[],
        help="Additional percentiles to compute (e.g. 10 25 75 90)",
    )
    parser.add_argument("--title", help="SVG map title")

    args = parser.parse_args()

    from vormap_viz import compute_regions

    seeds = vormap.load_data(args.seeds)
    observations = load_observations(args.observations)
    regions = compute_regions(seeds)

    print("Loaded %d seed points, %d observations" % (len(seeds), len(observations)))

    stats = zonal_statistics(
        regions, seeds, observations,
        percentiles=tuple(args.percentiles),
    )

    non_empty = [s for s in stats if s["count"] > 0]
    empty = len(stats) - len(non_empty)
    print("\nZonal Statistics Summary")
    print("=" * 60)
    print("Total zones:        %d" % len(stats))
    print("Non-empty zones:    %d" % len(non_empty))
    print("Empty zones:        %d" % empty)
    print("Total observations: %d" % sum(s["count"] for s in stats))
    print()

    top = sorted(stats, key=lambda s: s["count"], reverse=True)[:10]
    print("%6s %7s %10s %10s %10s %10s %10s" % ("Zone", "Count", "Mean", "Std", "Min", "Max", "Density"))
    print("%6s %7s %10s %10s %10s %10s %10s" % ("-" * 6, "-" * 7, "-" * 10, "-" * 10, "-" * 10, "-" * 10, "-" * 10))
    for s in top:
        mean_s = "%.4f" % s["mean"] if s["mean"] is not None else "N/A"
        std_s = "%.4f" % s["std"] if s["std"] is not None else "N/A"
        min_s = "%.4f" % s["min"] if s["min"] is not None else "N/A"
        max_s = "%.4f" % s["max"] if s["max"] is not None else "N/A"
        print("%6d %7d %10s %10s %10s %10s %10.6f" % (s["zone_id"], s["count"], mean_s, std_s, min_s, max_s, s["density"]))

    if args.csv:
        path = export_zonal_csv(stats, args.csv)
        print("\nCSV exported: %s" % path)

    if args.json:
        path = export_zonal_json(stats, args.json)
        print("JSON exported: %s" % path)

    if args.svg:
        path = export_choropleth_svg(
            stats, regions, seeds, args.svg,
            stat=args.stat, title=args.title,
        )
        print("SVG choropleth exported: %s" % path)


if __name__ == "__main__":
    _cli()
