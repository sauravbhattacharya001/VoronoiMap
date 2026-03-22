"""Spatial Data Profiler — comprehensive dataset summary statistics.

Like ``pandas.describe()`` but for spatial point datasets.  Generates a
rich profile including point count, bounding box, centroid, spatial
extent, density, nearest-neighbor statistics, coordinate distribution
analysis, quadrant breakdown, and outlier detection.

Usage (library)::

    from vormap_profile import profile_data
    report = profile_data("cities.csv")
    print(report["summary"])

Usage (CLI)::

    python vormap_profile.py data.csv
    python vormap_profile.py data.csv --format json --output profile.json
    python vormap_profile.py data.csv --format html --output profile.html
    python vormap_profile.py data.csv --format csv --output profile.csv

Supports ``.txt``, ``.csv``, ``.json``, and ``.geojson`` inputs via
:func:`vormap.load_data`.
"""

import math
import json
import os
import sys
from vormap_utils import euclidean_pts as _euclidean

try:
    from vormap import load_data
except ImportError:
    # Allow standalone testing
    load_data = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _percentile(sorted_vals, p):
    """Compute the *p*-th percentile (0–100) from a sorted list."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_vals) else f
    d = k - f
    return sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f])


def _std(values, mean):
    """Population standard deviation."""
    if len(values) < 2:
        return 0.0
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _skewness(values, mean, sd):
    """Sample skewness (Fisher)."""
    n = len(values)
    if n < 3 or sd == 0:
        return 0.0
    return (sum(((v - mean) / sd) ** 3 for v in values) * n
            / ((n - 1) * (n - 2)))


def _kurtosis(values, mean, sd):
    """Excess kurtosis."""
    n = len(values)
    if n < 4 or sd == 0:
        return 0.0
    m4 = sum(((v - mean) / sd) ** 4 for v in values) / n
    return m4 - 3.0


# ---------------------------------------------------------------------------
# Core profiler
# ---------------------------------------------------------------------------

def profile_points(points):
    """Profile a list of ``(x, y)`` tuples and return a dict of statistics.

    Parameters
    ----------
    points : list of (float, float)
        The point dataset.

    Returns
    -------
    dict
        Nested dictionary with sections: ``basic``, ``bounds``, ``centroid``,
        ``x_stats``, ``y_stats``, ``spacing``, ``quadrants``, ``density``,
        ``outliers``, ``duplicates``, ``summary`` (formatted text).
    """
    if not points:
        return {"error": "No points provided", "summary": "No points provided."}

    n = len(points)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    # --- Basic ---
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = x_max - x_min
    y_range = y_max - y_min
    area = x_range * y_range if x_range > 0 and y_range > 0 else 0.0
    diagonal = math.sqrt(x_range ** 2 + y_range ** 2)

    # --- Centroid ---
    cx = sum(xs) / n
    cy = sum(ys) / n

    # --- Coordinate distributions ---
    def _coord_stats(vals, label):
        sv = sorted(vals)
        mean = sum(vals) / len(vals)
        sd = _std(vals, mean)
        return {
            "label": label,
            "min": sv[0],
            "max": sv[-1],
            "mean": round(mean, 6),
            "median": _percentile(sv, 50),
            "std": round(sd, 6),
            "skewness": round(_skewness(vals, mean, sd), 4),
            "kurtosis": round(_kurtosis(vals, mean, sd), 4),
            "q1": _percentile(sv, 25),
            "q3": _percentile(sv, 75),
            "iqr": round(_percentile(sv, 75) - _percentile(sv, 25), 6),
            "p5": _percentile(sv, 5),
            "p95": _percentile(sv, 95),
        }

    x_stats = _coord_stats(xs, "X")
    y_stats = _coord_stats(ys, "Y")

    # --- Nearest-neighbor distances ---
    nn_dists = []
    if n <= 10000:
        for i, p in enumerate(points):
            best = float('inf')
            for j, q in enumerate(points):
                if i != j:
                    d = _euclidean(p, q)
                    if d < best:
                        best = d
            nn_dists.append(best)
    else:
        # Sample 2000 random points for NN
        import random as _rnd
        sample_idx = _rnd.sample(range(n), min(2000, n))
        for i in sample_idx:
            best = float('inf')
            for j in range(n):
                if i != j:
                    d = _euclidean(points[i], points[j])
                    if d < best:
                        best = d
            nn_dists.append(best)

    nn_sorted = sorted(nn_dists)
    nn_mean = sum(nn_dists) / len(nn_dists) if nn_dists else 0
    nn_sd = _std(nn_dists, nn_mean) if nn_dists else 0

    spacing = {
        "nn_min": round(nn_sorted[0], 6) if nn_sorted else 0,
        "nn_max": round(nn_sorted[-1], 6) if nn_sorted else 0,
        "nn_mean": round(nn_mean, 6),
        "nn_median": round(_percentile(nn_sorted, 50), 6) if nn_sorted else 0,
        "nn_std": round(nn_sd, 6),
        "nn_cv": round(nn_sd / nn_mean, 4) if nn_mean > 0 else 0,
        "sampled": n > 10000,
    }

    # --- Quadrant breakdown ---
    q_ne, q_nw, q_se, q_sw = 0, 0, 0, 0
    for x, y in points:
        if x >= cx and y >= cy:
            q_ne += 1
        elif x < cx and y >= cy:
            q_nw += 1
        elif x >= cx and y < cy:
            q_se += 1
        else:
            q_sw += 1

    quadrants = {
        "NE": q_ne, "NW": q_nw, "SE": q_se, "SW": q_sw,
        "balance": round(min(q_ne, q_nw, q_se, q_sw) /
                         max(q_ne, q_nw, q_se, q_sw, 1), 4),
    }

    # --- Density ---
    density = {
        "points_per_unit_area": round(n / area, 6) if area > 0 else 0,
        "area": round(area, 6),
        "mean_spacing_theoretical": round(1.0 / math.sqrt(n / area), 6) if area > 0 else 0,
    }

    # --- Duplicates ---
    seen = set()
    dup_count = 0
    for p in points:
        if p in seen:
            dup_count += 1
        seen.add(p)

    duplicates = {
        "count": dup_count,
        "percentage": round(100.0 * dup_count / n, 2),
    }

    # --- Outliers (IQR method on NN distances) ---
    outlier_indices = []
    if nn_dists:
        q1_nn = _percentile(nn_sorted, 25)
        q3_nn = _percentile(nn_sorted, 75)
        iqr_nn = q3_nn - q1_nn
        upper = q3_nn + 1.5 * iqr_nn
        for idx, d in enumerate(nn_dists):
            if d > upper:
                outlier_indices.append(idx)

    outliers = {
        "count": len(outlier_indices),
        "threshold_nn_distance": round(
            (_percentile(nn_sorted, 75) + 1.5 *
             (_percentile(nn_sorted, 75) - _percentile(nn_sorted, 25))), 6
        ) if nn_sorted else 0,
        "indices": outlier_indices[:20],  # Cap for readability
    }

    # --- Clark-Evans R (spatial randomness test) ---
    r_obs = nn_mean
    r_exp = 1.0 / (2.0 * math.sqrt(n / area)) if area > 0 else 0
    clark_evans_r = round(r_obs / r_exp, 4) if r_exp > 0 else 0
    # R < 1 → clustered, R ≈ 1 → random, R > 1 → dispersed
    if clark_evans_r < 0.8:
        pattern = "clustered"
    elif clark_evans_r > 1.2:
        pattern = "dispersed"
    else:
        pattern = "random"

    spatial_pattern = {
        "clark_evans_r": clark_evans_r,
        "pattern": pattern,
        "r_observed": round(r_obs, 6),
        "r_expected": round(r_exp, 6),
    }

    # --- Build summary text ---
    lines = [
        "=" * 60,
        "  SPATIAL DATA PROFILE",
        "=" * 60,
        "",
        f"  Points:          {n:,}",
        f"  Duplicates:      {dup_count} ({duplicates['percentage']}%)",
        f"  Bounding Box:    X [{x_min:.4f}, {x_max:.4f}]  "
        f"Y [{y_min:.4f}, {y_max:.4f}]",
        f"  Extent:          {x_range:.4f} × {y_range:.4f}  "
        f"(diagonal {diagonal:.4f})",
        f"  Area:            {area:.4f}",
        f"  Centroid:        ({cx:.4f}, {cy:.4f})",
        "",
        "  ── Coordinate Statistics ──",
        f"  X  mean={x_stats['mean']:.4f}  std={x_stats['std']:.4f}  "
        f"skew={x_stats['skewness']:.3f}  kurt={x_stats['kurtosis']:.3f}",
        f"  Y  mean={y_stats['mean']:.4f}  std={y_stats['std']:.4f}  "
        f"skew={y_stats['skewness']:.3f}  kurt={y_stats['kurtosis']:.3f}",
        "",
        "  ── Nearest-Neighbor Spacing ──",
        f"  Min={spacing['nn_min']:.4f}  Max={spacing['nn_max']:.4f}  "
        f"Mean={spacing['nn_mean']:.4f}  CV={spacing['nn_cv']:.3f}",
        "",
        "  ── Spatial Pattern ──",
        f"  Clark-Evans R:   {clark_evans_r:.4f}  → {pattern}",
        "",
        "  ── Quadrant Balance ──",
        f"  NE={q_ne}  NW={q_nw}  SE={q_se}  SW={q_sw}  "
        f"(balance={quadrants['balance']:.3f})",
        "",
        f"  ── Density ──",
        f"  {density['points_per_unit_area']:.6f} points/unit²",
        "",
        f"  Spatial outliers: {outliers['count']}",
        "=" * 60,
    ]
    summary = "\n".join(lines)

    return {
        "basic": {"count": n, "diagonal": round(diagonal, 6)},
        "bounds": {
            "x_min": x_min, "x_max": x_max,
            "y_min": y_min, "y_max": y_max,
            "x_range": round(x_range, 6),
            "y_range": round(y_range, 6),
        },
        "centroid": {"x": round(cx, 6), "y": round(cy, 6)},
        "x_stats": x_stats,
        "y_stats": y_stats,
        "spacing": spacing,
        "spatial_pattern": spatial_pattern,
        "quadrants": quadrants,
        "density": density,
        "duplicates": duplicates,
        "outliers": outliers,
        "summary": summary,
    }


def profile_data(filename):
    """Profile a data file by loading it via :func:`vormap.load_data`.

    Parameters
    ----------
    filename : str
        Path to a point data file (``.txt``, ``.csv``, ``.json``, ``.geojson``).

    Returns
    -------
    dict
        Profile dictionary (see :func:`profile_points`).
    """
    if load_data is None:
        raise ImportError("vormap.load_data is required")
    points = load_data(filename)
    return profile_points(points)


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def _to_json(report, indent=2):
    """Serialize report to JSON string (exclude summary text)."""
    out = {k: v for k, v in report.items() if k != "summary"}
    return json.dumps(out, indent=indent)


def _to_csv(report):
    """Flatten report to CSV rows (key,value)."""
    rows = ["key,value"]

    def _flatten(d, prefix=""):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                _flatten(v, key)
            elif isinstance(v, list):
                rows.append(f"{key},{len(v)} items")
            else:
                rows.append(f"{key},{v}")

    for section, data in report.items():
        if section == "summary":
            continue
        if isinstance(data, dict):
            _flatten(data, section)
        else:
            rows.append(f"{section},{data}")
    return "\n".join(rows)


def _to_html(report):
    """Generate a self-contained HTML profile page."""
    r = report
    basic = r.get("basic", {})
    bounds = r.get("bounds", {})
    centroid = r.get("centroid", {})
    xs = r.get("x_stats", {})
    ys = r.get("y_stats", {})
    sp = r.get("spacing", {})
    pat = r.get("spatial_pattern", {})
    quad = r.get("quadrants", {})
    dens = r.get("density", {})
    dupl = r.get("duplicates", {})
    outl = r.get("outliers", {})

    pattern_color = {
        "clustered": "#e74c3c",
        "random": "#f39c12",
        "dispersed": "#27ae60",
    }.get(pat.get("pattern", ""), "#95a5a6")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spatial Data Profile</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 2rem; }}
  h1 {{ text-align: center; margin-bottom: 2rem; font-size: 1.8rem; color: #38bdf8; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
           gap: 1.2rem; max-width: 1200px; margin: 0 auto; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 1.2rem;
           border: 1px solid #334155; }}
  .card h2 {{ font-size: 0.85rem; text-transform: uppercase; color: #94a3b8;
              margin-bottom: 0.8rem; letter-spacing: 0.05em; }}
  .stat {{ display: flex; justify-content: space-between; padding: 0.3rem 0;
           border-bottom: 1px solid #334155; }}
  .stat:last-child {{ border-bottom: none; }}
  .stat .label {{ color: #94a3b8; font-size: 0.9rem; }}
  .stat .value {{ color: #f1f5f9; font-weight: 600; font-size: 0.9rem; }}
  .badge {{ display: inline-block; padding: 0.2rem 0.7rem; border-radius: 20px;
            font-size: 0.8rem; font-weight: 600; }}
  .quadrant-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 4px;
                    margin-top: 0.5rem; }}
  .quad-cell {{ text-align: center; padding: 0.6rem; border-radius: 6px;
                background: #334155; font-weight: 600; }}
</style>
</head>
<body>
<h1>📊 Spatial Data Profile</h1>
<div class="grid">

  <div class="card">
    <h2>Overview</h2>
    <div class="stat"><span class="label">Points</span>
      <span class="value">{basic.get('count', 0):,}</span></div>
    <div class="stat"><span class="label">Duplicates</span>
      <span class="value">{dupl.get('count', 0)} ({dupl.get('percentage', 0)}%)</span></div>
    <div class="stat"><span class="label">Area</span>
      <span class="value">{dens.get('area', 0):,.2f}</span></div>
    <div class="stat"><span class="label">Density</span>
      <span class="value">{dens.get('points_per_unit_area', 0):.6f} pts/unit²</span></div>
    <div class="stat"><span class="label">Spatial Outliers</span>
      <span class="value">{outl.get('count', 0)}</span></div>
  </div>

  <div class="card">
    <h2>Bounding Box</h2>
    <div class="stat"><span class="label">X range</span>
      <span class="value">[{bounds.get('x_min', 0):.4f}, {bounds.get('x_max', 0):.4f}]</span></div>
    <div class="stat"><span class="label">Y range</span>
      <span class="value">[{bounds.get('y_min', 0):.4f}, {bounds.get('y_max', 0):.4f}]</span></div>
    <div class="stat"><span class="label">Extent</span>
      <span class="value">{bounds.get('x_range', 0):.4f} × {bounds.get('y_range', 0):.4f}</span></div>
    <div class="stat"><span class="label">Diagonal</span>
      <span class="value">{basic.get('diagonal', 0):.4f}</span></div>
    <div class="stat"><span class="label">Centroid</span>
      <span class="value">({centroid.get('x', 0):.4f}, {centroid.get('y', 0):.4f})</span></div>
  </div>

  <div class="card">
    <h2>X Distribution</h2>
    <div class="stat"><span class="label">Mean ± Std</span>
      <span class="value">{xs.get('mean', 0):.4f} ± {xs.get('std', 0):.4f}</span></div>
    <div class="stat"><span class="label">Median</span>
      <span class="value">{xs.get('median', 0):.4f}</span></div>
    <div class="stat"><span class="label">IQR</span>
      <span class="value">{xs.get('iqr', 0):.4f}</span></div>
    <div class="stat"><span class="label">Skewness</span>
      <span class="value">{xs.get('skewness', 0):.3f}</span></div>
    <div class="stat"><span class="label">Kurtosis</span>
      <span class="value">{xs.get('kurtosis', 0):.3f}</span></div>
  </div>

  <div class="card">
    <h2>Y Distribution</h2>
    <div class="stat"><span class="label">Mean ± Std</span>
      <span class="value">{ys.get('mean', 0):.4f} ± {ys.get('std', 0):.4f}</span></div>
    <div class="stat"><span class="label">Median</span>
      <span class="value">{ys.get('median', 0):.4f}</span></div>
    <div class="stat"><span class="label">IQR</span>
      <span class="value">{ys.get('iqr', 0):.4f}</span></div>
    <div class="stat"><span class="label">Skewness</span>
      <span class="value">{ys.get('skewness', 0):.3f}</span></div>
    <div class="stat"><span class="label">Kurtosis</span>
      <span class="value">{ys.get('kurtosis', 0):.3f}</span></div>
  </div>

  <div class="card">
    <h2>Nearest-Neighbor Spacing</h2>
    <div class="stat"><span class="label">Min</span>
      <span class="value">{sp.get('nn_min', 0):.4f}</span></div>
    <div class="stat"><span class="label">Mean</span>
      <span class="value">{sp.get('nn_mean', 0):.4f}</span></div>
    <div class="stat"><span class="label">Max</span>
      <span class="value">{sp.get('nn_max', 0):.4f}</span></div>
    <div class="stat"><span class="label">CV</span>
      <span class="value">{sp.get('nn_cv', 0):.3f}</span></div>
  </div>

  <div class="card">
    <h2>Spatial Pattern</h2>
    <div class="stat"><span class="label">Clark-Evans R</span>
      <span class="value">{pat.get('clark_evans_r', 0):.4f}</span></div>
    <div class="stat"><span class="label">Pattern</span>
      <span class="value"><span class="badge" style="background:{pattern_color};color:#fff">
        {pat.get('pattern', '?').upper()}</span></span></div>
    <div class="stat"><span class="label">R observed</span>
      <span class="value">{pat.get('r_observed', 0):.6f}</span></div>
    <div class="stat"><span class="label">R expected</span>
      <span class="value">{pat.get('r_expected', 0):.6f}</span></div>
  </div>

  <div class="card">
    <h2>Quadrant Balance</h2>
    <div class="quadrant-grid">
      <div class="quad-cell">NW<br>{quad.get('NW', 0)}</div>
      <div class="quad-cell">NE<br>{quad.get('NE', 0)}</div>
      <div class="quad-cell">SW<br>{quad.get('SW', 0)}</div>
      <div class="quad-cell">SE<br>{quad.get('SE', 0)}</div>
    </div>
    <div class="stat" style="margin-top:0.5rem">
      <span class="label">Balance score</span>
      <span class="value">{quad.get('balance', 0):.3f}</span></div>
  </div>

</div>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for the spatial data profiler."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Spatial Data Profiler — comprehensive dataset summary.",
        epilog="Example: python vormap_profile.py data/cities.csv --format html -o profile.html",
    )
    parser.add_argument("datafile", help="Point data file (.txt/.csv/.json/.geojson)")
    parser.add_argument(
        "--format", "-f", choices=["text", "json", "csv", "html"],
        default="text", help="Output format (default: text)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Write output to file instead of stdout",
    )
    args = parser.parse_args()

    if load_data is None:
        print("Error: vormap module not found. Run from the VoronoiMap directory.", file=sys.stderr)
        sys.exit(1)

    report = profile_data(args.datafile)

    if args.format == "json":
        content = _to_json(report)
    elif args.format == "csv":
        content = _to_csv(report)
    elif args.format == "html":
        content = _to_html(report)
    else:
        content = report["summary"]

    if args.output:
        out_path = os.path.abspath(args.output)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"Profile written to {out_path}")
    else:
        print(content)


if __name__ == "__main__":
    main()
