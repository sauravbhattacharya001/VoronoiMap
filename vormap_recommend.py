"""Spatial analysis recommender for Voronoi diagrams.

Examines point-pattern data and proactively recommends which VoronoiMap
analyses would be most insightful, ranked by relevance.  Acts as an
intelligent starting point for new datasets — instead of the user
guessing which tools to try, the recommender inspects the data and
tells them.

Checks:
- Point count & density → appropriate tools for scale
- Clustering tendency (Hopkins statistic) → cluster / hotspot tools
- Spatial regularity (nearest-neighbor ratio) → regularity / lattice tools
- Bounding-box aspect ratio → anisotropy / directional tools
- Outlier presence (IQR on nearest-neighbor distances) → outlier tools
- Convex hull area ratio → coverage / boundary tools

Usage (Python API)::

    from vormap_recommend import recommend
    recs = recommend("datauni5.txt")
    for r in recs:
        print(f"[{r['priority']}] {r['tool']} — {r['reason']}")

CLI::

    python vormap_recommend.py datauni5.txt
    python vormap_recommend.py datauni5.txt --json
    python vormap_recommend.py datauni5.txt --top 5
    python vormap_recommend.py datauni5.txt --html report.html
"""

import json
import math
import random
import sys
from collections import namedtuple

Recommendation = namedtuple("Recommendation", ["priority", "tool", "command", "reason"])


def _load_points(path):
    """Load 2D points from a whitespace-delimited text file."""
    points = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    points.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return points


def _bounding_box(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _distances_to_nearest(pts):
    """Brute-force nearest-neighbor distances (fine for <10k points)."""
    n = len(pts)
    dists = []
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if d < best:
                best = d
        dists.append(best)
    return dists


def _hopkins_statistic(pts, m=None, seed=42):
    """Estimate clustering tendency via the Hopkins statistic.
    
    H ≈ 0.5 → random, H → 1.0 → clustered, H → 0.0 → regular.
    """
    rng = random.Random(seed)
    n = len(pts)
    if m is None:
        m = min(n, max(10, n // 10))

    xmin, ymin, xmax, ymax = _bounding_box(pts)

    # Sample m data points
    sample_idx = rng.sample(range(n), m)

    def nn_dist(pt, candidates):
        return min(math.hypot(pt[0] - c[0], pt[1] - c[1]) for c in candidates)

    # u_i: distance from random point to nearest data point
    u_sum = 0.0
    for _ in range(m):
        rp = (rng.uniform(xmin, xmax), rng.uniform(ymin, ymax))
        u_sum += nn_dist(rp, pts)

    # w_i: distance from sampled data point to nearest OTHER data point
    w_sum = 0.0
    for idx in sample_idx:
        others = [pts[j] for j in range(n) if j != idx]
        w_sum += nn_dist(pts[idx], others)

    if u_sum + w_sum == 0:
        return 0.5
    return u_sum / (u_sum + w_sum)


def _nearest_neighbor_ratio(pts):
    """Clark-Evans R: ratio of observed mean NN distance to expected under CSR."""
    n = len(pts)
    if n < 3:
        return 1.0
    nn_dists = _distances_to_nearest(pts)
    observed = sum(nn_dists) / n
    xmin, ymin, xmax, ymax = _bounding_box(pts)
    area = (xmax - xmin) * (ymax - ymin)
    if area == 0:
        return 1.0
    density = n / area
    expected = 0.5 / math.sqrt(density)
    if expected == 0:
        return 1.0
    return observed / expected


def _convex_hull_area(pts):
    """Convex hull area via the shoelace formula on a Graham scan hull."""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    pts_sorted = sorted(set(pts))
    if len(pts_sorted) < 3:
        return 0.0

    lower = []
    for p in pts_sorted:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts_sorted):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]

    # Shoelace
    area = 0.0
    nh = len(hull)
    for i in range(nh):
        j = (i + 1) % nh
        area += hull[i][0] * hull[j][1]
        area -= hull[j][0] * hull[i][1]
    return abs(area) / 2.0


def recommend(path, top=None):
    """Analyze a point-data file and return ranked recommendations.

    Parameters
    ----------
    path : str
        Path to a whitespace-delimited point file (x y per line).
    top : int or None
        Return only the top-N recommendations.

    Returns
    -------
    list[dict]
        Each dict has keys: priority (1=highest), tool, command, reason.
    """
    pts = _load_points(path)
    n = len(pts)
    if n < 3:
        return [{"priority": 1, "tool": "N/A",
                 "command": "",
                 "reason": f"Only {n} points found — need at least 3 for analysis."}]

    recs = []

    # --- Basic stats ---
    xmin, ymin, xmax, ymax = _bounding_box(pts)
    bbox_w = xmax - xmin
    bbox_h = ymax - ymin
    bbox_area = bbox_w * bbox_h if bbox_w > 0 and bbox_h > 0 else 1.0
    density = n / bbox_area

    # --- Clustering tendency ---
    hopkins = _hopkins_statistic(pts) if n <= 5000 else 0.5
    nn_ratio = _nearest_neighbor_ratio(pts) if n <= 5000 else 1.0

    # --- NN distance stats ---
    nn_dists = _distances_to_nearest(pts) if n <= 5000 else []
    nn_iqr_outliers = 0
    if nn_dists:
        sorted_d = sorted(nn_dists)
        q1 = sorted_d[len(sorted_d) // 4]
        q3 = sorted_d[3 * len(sorted_d) // 4]
        iqr = q3 - q1
        fence_lo = q1 - 1.5 * iqr
        fence_hi = q3 + 1.5 * iqr
        nn_iqr_outliers = sum(1 for d in nn_dists if d < fence_lo or d > fence_hi)

    # --- Aspect ratio ---
    aspect = max(bbox_w, bbox_h) / max(min(bbox_w, bbox_h), 1e-9)

    # --- Hull coverage ---
    hull_area = _convex_hull_area(pts)
    coverage = hull_area / bbox_area if bbox_area > 0 else 1.0

    # === Generate recommendations ===
    pri = 1

    # Clustering detected
    if hopkins > 0.7:
        recs.append(Recommendation(
            pri, "vormap_cluster",
            f"python vormap_cluster.py {path}",
            f"Strong clustering tendency detected (Hopkins={hopkins:.2f}). "
            "Cluster analysis will reveal natural groupings."
        ))
        pri += 1
        recs.append(Recommendation(
            pri, "vormap_hotspot",
            f"python vormap_hotspot.py {path}",
            "Hotspot analysis can pinpoint density peaks in your clustered data."
        ))
        pri += 1

    # Regular pattern
    if nn_ratio > 1.3:
        recs.append(Recommendation(
            pri, "vormap_regularity",
            f"python vormap_regularity.py {path}",
            f"Pattern appears more regular than random (R={nn_ratio:.2f}). "
            "Regularity analysis can quantify lattice-like structure."
        ))
        pri += 1

    # Outliers present
    if nn_iqr_outliers > 0:
        pct = 100 * nn_iqr_outliers / n
        recs.append(Recommendation(
            pri, "vormap_outlier",
            f"python vormap_outlier.py {path}",
            f"{nn_iqr_outliers} spatial outliers detected ({pct:.1f}% of points). "
            "Outlier analysis will highlight anomalous regions."
        ))
        pri += 1

    # Large dataset → KDE
    if n > 100:
        recs.append(Recommendation(
            pri, "vormap_kde",
            f"python vormap_kde.py {path}",
            f"With {n} points, kernel density estimation will show smooth "
            "density surfaces for visual interpretation."
        ))
        pri += 1

    # Elongated bounding box → anisotropy
    if aspect > 2.0:
        recs.append(Recommendation(
            pri, "vormap_trend",
            f"python vormap_trend.py {path}",
            f"Data has an elongated spread (aspect={aspect:.1f}). "
            "Trend analysis can detect directional bias."
        ))
        pri += 1

    # Low coverage → boundary analysis
    if coverage < 0.6:
        recs.append(Recommendation(
            pri, "vormap_hull",
            f"python vormap_hull.py {path}",
            f"Convex hull covers only {coverage:.0%} of the bounding box. "
            "Hull analysis will characterize the distribution boundary."
        ))
        pri += 1

    # Always useful at moderate scale
    if n >= 10:
        recs.append(Recommendation(
            pri, "vormap_viz",
            f"python vormap.py {path} 5",
            "Standard Voronoi visualization — always a good starting point."
        ))
        pri += 1

    if n >= 20:
        recs.append(Recommendation(
            pri, "vormap_heatmap",
            f"python vormap_heatmap.py {path}",
            "Heatmap view provides intuitive density overview."
        ))
        pri += 1

    # Interactive exploration for small/medium datasets
    if n <= 500:
        recs.append(Recommendation(
            pri, "vormap_playground",
            f"python vormap.py {path} 5 --playground",
            "Interactive HTML playground for hands-on exploration."
        ))
        pri += 1

    # Spatial autocorrelation
    if n >= 15:
        recs.append(Recommendation(
            pri, "vormap_autocorr",
            f"python vormap_autocorr.py {path}",
            "Spatial autocorrelation (Moran's I) reveals whether nearby "
            "cells share similar properties."
        ))
        pri += 1

    results = [r._asdict() for r in recs]
    if top:
        results = results[:top]
    return results


def _format_table(recs):
    """Pretty-print recommendations as a text table."""
    lines = []
    lines.append(f"{'#':>3}  {'Tool':<22}  Reason")
    lines.append(f"{'─'*3}  {'─'*22}  {'─'*50}")
    for r in recs:
        lines.append(f"{r['priority']:>3}  {r['tool']:<22}  {r['reason']}")
        lines.append(f"     {'':22}  $ {r['command']}")
        lines.append("")
    return "\n".join(lines)


def _format_html(recs, path):
    """Generate a self-contained HTML report."""
    rows = ""
    for r in recs:
        bar_w = max(5, 100 - r["priority"] * 8)
        rows += f"""<tr>
  <td class="pri">{r['priority']}</td>
  <td class="tool">{r['tool']}</td>
  <td>{r['reason']}<br><code>{r['command']}</code>
    <div class="bar" style="width:{bar_w}%"></div>
  </td>
</tr>\n"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>VoronoiMap Recommender — {path}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2em auto;
         background: #0d1117; color: #c9d1d9; }}
  h1 {{ color: #58a6ff; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 8px; border-bottom: 2px solid #30363d; color: #8b949e; }}
  td {{ padding: 10px 8px; border-bottom: 1px solid #21262d; vertical-align: top; }}
  .pri {{ font-size: 1.5em; font-weight: bold; color: #58a6ff; width: 40px; text-align: center; }}
  .tool {{ font-family: monospace; color: #7ee787; white-space: nowrap; }}
  code {{ color: #d2a8ff; font-size: 0.85em; }}
  .bar {{ height: 4px; background: linear-gradient(90deg, #58a6ff, #7ee787); border-radius: 2px; margin-top: 6px; }}
</style></head><body>
<h1>🔍 Spatial Analysis Recommender</h1>
<p>Dataset: <code>{path}</code></p>
<table><tr><th>#</th><th>Tool</th><th>Why &amp; How</th></tr>
{rows}</table>
<p style="color:#484f58; margin-top:2em">Generated by vormap_recommend</p>
</body></html>"""


def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Analyze spatial data and recommend VoronoiMap tools."
    )
    p.add_argument("datafile", help="Point data file (x y per line)")
    p.add_argument("--top", type=int, default=None, help="Show only top-N recommendations")
    p.add_argument("--json", action="store_true", help="Output as JSON")
    p.add_argument("--html", metavar="FILE", help="Write HTML report to FILE")
    args = p.parse_args()

    recs = recommend(args.datafile, top=args.top)

    if args.html:
        html = _format_html(recs, args.datafile)
        with open(args.html, "w") as f:
            f.write(html)
        print(f"HTML report written to {args.html}")

    if args.json:
        print(json.dumps(recs, indent=2))
    elif not args.html:
        print(f"\n🔍 VoronoiMap Recommender — {args.datafile}\n")
        print(_format_table(recs))


if __name__ == "__main__":
    main()
