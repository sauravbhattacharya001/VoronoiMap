#!/usr/bin/env python3
"""vormap_dispatch - Autonomous Spatial Dispatch Optimizer

Assigns demand points to nearest Voronoi facilities, detects overloaded cells,
suggests optimal new facility placements, and generates interactive HTML reports.
Supports auto-dispatch mode that iteratively rebalances until utilization targets
are met.

Usage:
    python vormap_dispatch.py facilities.csv demands.csv --report dispatch.html
    python vormap_dispatch.py facilities.csv demands.csv --auto --target-util 0.8
    python vormap_dispatch.py facilities.csv demands.csv --json results.json
    python vormap_dispatch.py --demo
"""

import argparse
import csv
import json
import random
import sys
from collections import defaultdict

from vormap_utils import euclidean as _dist
from vormap_utils import polygon_area  # re-exported for callers/tests

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------
# Core dispatch logic
# ---------------------------------------------------------------------------

def assign_demand(facilities, demands, weights=None):
    """Assign each demand point to its nearest facility (Voronoi-natural).
    Returns dict: facility_idx -> list of demand indices."""
    assignments = defaultdict(list)
    for di, dp in enumerate(demands):
        best_fi = 0
        best_d = _dist(dp, facilities[0])
        for fi in range(1, len(facilities)):
            d = _dist(dp, facilities[fi])
            if d < best_d:
                best_d = d
                best_fi = fi
        assignments[best_fi].append(di)
    return dict(assignments)


def compute_metrics(facilities, demands, assignments, weights=None):
    """Compute dispatch quality metrics."""
    if weights is None:
        weights = [1.0] * len(demands)

    distances = []
    loads = []
    for fi in range(len(facilities)):
        demand_idxs = assignments.get(fi, [])
        load = sum(weights[di] for di in demand_idxs)
        loads.append(load)
        for di in demand_idxs:
            distances.append(_dist(demands[di], facilities[fi]))

    # Utilization
    total_load = sum(loads)
    avg_load = total_load / len(facilities) if facilities else 0
    max_load = max(loads) if loads else 0
    utilizations = [l / max(avg_load, 1e-9) for l in loads]

    # Distance stats
    distances_sorted = sorted(distances) if distances else [0]
    avg_dist = sum(distances) / len(distances) if distances else 0
    max_dist = max(distances) if distances else 0
    p95_idx = int(len(distances_sorted) * 0.95)
    p95_dist = distances_sorted[min(p95_idx, len(distances_sorted) - 1)]

    # Gini coefficient of loads
    gini = _gini(loads)

    return {
        "avg_distance": round(avg_dist, 2),
        "max_distance": round(max_dist, 2),
        "p95_distance": round(p95_dist, 2),
        "gini": round(gini, 4),
        "max_utilization": round(max(utilizations) if utilizations else 0, 2),
        "avg_load": round(avg_load, 2),
        "max_load": round(max_load, 2),
        "loads": [round(l, 2) for l in loads],
        "utilizations": [round(u, 2) for u in utilizations],
    }


def _gini(values):
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    total = sum(sorted_v)
    if total == 0:
        return 0.0
    cum = 0.0
    gini_sum = 0.0
    for i, v in enumerate(sorted_v):
        cum += v
        gini_sum += (2 * (i + 1) - n - 1) * v
    return gini_sum / (n * total)


def detect_overloaded(assignments, weights, capacity, num_facilities):
    """Return indices of facilities exceeding capacity."""
    overloaded = []
    for fi in range(num_facilities):
        demand_idxs = assignments.get(fi, [])
        load = sum(weights[di] for di in demand_idxs)
        if load > capacity:
            overloaded.append(fi)
    return overloaded


def suggest_new_facilities(facilities, demands, assignments, weights, overloaded, max_new):
    """Suggest new facility placements to relieve overloaded cells.
    For each overloaded cell (sorted by load desc), place a new facility at
    the centroid of the farthest half of demand points in that cell."""
    suggestions = []
    # Sort overloaded by load descending
    load_map = {}
    for fi in overloaded:
        idxs = assignments.get(fi, [])
        load_map[fi] = sum(weights[di] for di in idxs)
    ranked = sorted(overloaded, key=lambda fi: load_map[fi], reverse=True)

    for fi in ranked:
        if len(suggestions) >= max_new:
            break
        idxs = assignments.get(fi, [])
        if not idxs:
            continue
        fac = facilities[fi]
        # Sort demand by distance to facility, take the farthest half
        by_dist = sorted(idxs, key=lambda di: _dist(demands[di], fac), reverse=True)
        far_half = by_dist[: max(1, len(by_dist) // 2)]
        cx = sum(demands[di][0] for di in far_half) / len(far_half)
        cy = sum(demands[di][1] for di in far_half) / len(far_half)
        suggestions.append((round(cx, 4), round(cy, 4)))

    return suggestions


def auto_dispatch(facilities, demands, weights, capacity, target_util, max_new):
    """Iteratively add suggested facilities until utilization target is met."""
    facs = list(facilities)
    all_suggestions = []
    for iteration in range(max_new):
        assignments = assign_demand(facs, demands, weights)
        metrics = compute_metrics(facs, demands, assignments, weights)
        if metrics["max_utilization"] <= target_util:
            break
        overloaded = detect_overloaded(assignments, weights, capacity, len(facs))
        if not overloaded:
            break
        new = suggest_new_facilities(facs, demands, assignments, weights, overloaded, 1)
        if not new:
            break
        facs.append(new[0])
        all_suggestions.append(new[0])

    assignments = assign_demand(facs, demands, weights)
    metrics = compute_metrics(facs, demands, assignments, weights)
    return facs, assignments, metrics, all_suggestions


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def generate_html_report(facilities, demands, assignments, metrics, weights,
                         suggestions=None, original_count=0):
    """Generate self-contained interactive HTML report."""
    suggestions = suggestions or []

    # Compute bounding box
    all_pts = list(facilities) + list(demands)
    min_x = min(p[0] for p in all_pts) if all_pts else 0
    max_x = max(p[0] for p in all_pts) if all_pts else 100
    min_y = min(p[1] for p in all_pts) if all_pts else 0
    max_y = max(p[1] for p in all_pts) if all_pts else 100
    pad = max((max_x - min_x), (max_y - min_y)) * 0.08 or 10
    min_x -= pad; max_x += pad; min_y -= pad; max_y += pad
    w, h = 700, 700
    def tx(x): return (x - min_x) / (max_x - min_x) * w
    def ty(y): return h - (y - min_y) / (max_y - min_y) * h

    # Color palette
    colors = [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
        "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
        "#86bcb6", "#8cd17d", "#b6992d", "#499894", "#d37295",
        "#a0cbe8", "#ffbe7d", "#d4a6c8", "#fabfd2", "#d7b5a6",
    ]

    # SVG: facility-demand map
    svg_lines = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
                 f'style="background:#f8f9fa;border:1px solid #ddd;border-radius:8px;max-width:700px">']

    # Draw demand points colored by assignment
    for fi, idxs in assignments.items():
        c = colors[fi % len(colors)]
        for di in idxs:
            dx, dy = tx(demands[di][0]), ty(demands[di][1])
            svg_lines.append(f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="3" fill="{c}" opacity="0.5"/>')

    # Draw facilities
    for fi, fac in enumerate(facilities):
        fx, fy = tx(fac[0]), ty(fac[1])
        is_new = fi >= original_count and original_count > 0
        fill = "#2ecc71" if is_new else "#2c3e50"
        r = 9 if is_new else 7
        svg_lines.append(f'<circle cx="{fx:.1f}" cy="{fy:.1f}" r="{r}" fill="{fill}" stroke="#fff" stroke-width="2"/>')
        svg_lines.append(f'<text x="{fx:.1f}" y="{fy + 3:.1f}" text-anchor="middle" '
                         f'font-size="8" fill="#fff" font-weight="bold">{fi}</text>')

    # Draw suggested locations with star marker
    for sx, sy in suggestions:
        px, py = tx(sx), ty(sy)
        svg_lines.append(f'<polygon points="{px},{py-12} {px+4},{py-4} {px+12},{py-4} {px+5},{py+2} '
                         f'{px+8},{py+10} {px},{py+5} {px-8},{py+10} {px-5},{py+2} {px-12},{py-4} {px-4},{py-4}" '
                         f'fill="#e74c3c" stroke="#fff" stroke-width="1" opacity="0.8"/>')

    svg_lines.append('</svg>')
    svg_map = '\n'.join(svg_lines)

    # Utilization bar chart SVG
    bar_w = max(700, len(facilities) * 25)
    bar_h = 200
    bar_svg = [f'<svg viewBox="0 0 {bar_w} {bar_h}" xmlns="http://www.w3.org/2000/svg" '
               f'style="background:#f8f9fa;border:1px solid #ddd;border-radius:8px;max-width:{bar_w}px;overflow-x:auto">']
    if metrics["utilizations"]:
        bw = max(8, min(30, (bar_w - 40) // len(facilities) - 2))
        max_u = max(max(metrics["utilizations"]), 1.0)
        for i, u in enumerate(metrics["utilizations"]):
            bx = 20 + i * (bw + 2)
            bh = (u / max_u) * (bar_h - 40)
            by = bar_h - 20 - bh
            c = "#e74c3c" if u > 1.2 else "#f39c12" if u > 0.85 else "#2ecc71"
            bar_svg.append(f'<rect x="{bx}" y="{by:.1f}" width="{bw}" height="{bh:.1f}" fill="{c}" rx="2"/>')
            if len(facilities) <= 40:
                bar_svg.append(f'<text x="{bx + bw/2:.1f}" y="{bar_h - 5}" text-anchor="middle" font-size="8">{i}</text>')
    # Target line
    if metrics["utilizations"]:
        ty_line = bar_h - 20 - (0.85 / max_u) * (bar_h - 40)
        bar_svg.append(f'<line x1="10" y1="{ty_line:.1f}" x2="{bar_w-10}" y2="{ty_line:.1f}" '
                       f'stroke="#3498db" stroke-dasharray="5,3" stroke-width="1.5"/>')
        bar_svg.append(f'<text x="{bar_w-10}" y="{ty_line - 4:.1f}" text-anchor="end" font-size="9" fill="#3498db">target 0.85</text>')
    bar_svg.append('</svg>')
    bar_chart = '\n'.join(bar_svg)

    # Build HTML
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Dispatch Report - vormap_dispatch</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 2rem; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; color: #38bdf8; }}
  h2 {{ font-size: 1.2rem; margin: 1.5rem 0 0.8rem; color: #94a3b8; }}
  .subtitle {{ color: #64748b; margin-bottom: 2rem; }}
  .card {{ background: #1e293b; border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }}
  .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1rem; }}
  .metric {{ background: #0f172a; border-radius: 8px; padding: 1rem; text-align: center; }}
  .metric .value {{ font-size: 1.6rem; font-weight: 700; color: #38bdf8; }}
  .metric .label {{ font-size: 0.75rem; color: #64748b; margin-top: 0.3rem; }}
  svg {{ width: 100%; height: auto; }}
  .legend {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 0.5rem; font-size: 0.8rem; }}
  .legend span {{ display: flex; align-items: center; gap: 4px; }}
  .legend .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th, td {{ padding: 0.5rem; text-align: left; border-bottom: 1px solid #334155; }}
  th {{ color: #94a3b8; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
  .badge-red {{ background: #7f1d1d; color: #fca5a5; }}
  .badge-green {{ background: #14532d; color: #86efac; }}
  .badge-yellow {{ background: #713f12; color: #fde047; }}
</style></head><body>
<div class="container">
<h1>🚀 Spatial Dispatch Report</h1>
<p class="subtitle">vormap_dispatch — Autonomous Spatial Dispatch Optimizer</p>

<div class="card">
<h2>📊 Dispatch Metrics</h2>
<div class="metrics">
  <div class="metric"><div class="value">{len(facilities)}</div><div class="label">Facilities</div></div>
  <div class="metric"><div class="value">{len(demands)}</div><div class="label">Demand Points</div></div>
  <div class="metric"><div class="value">{metrics['avg_distance']}</div><div class="label">Avg Distance</div></div>
  <div class="metric"><div class="value">{metrics['p95_distance']}</div><div class="label">P95 Distance</div></div>
  <div class="metric"><div class="value">{metrics['max_distance']}</div><div class="label">Max Distance</div></div>
  <div class="metric"><div class="value">{metrics['gini']}</div><div class="label">Load Gini</div></div>
  <div class="metric"><div class="value">{metrics['max_utilization']}</div><div class="label">Max Utilization</div></div>
  <div class="metric"><div class="value">{len(suggestions)}</div><div class="label">Suggested New</div></div>
</div></div>

<div class="card">
<h2>🗺️ Facility-Demand Assignment Map</h2>
{svg_map}
<div class="legend">
  <span><span class="dot" style="background:#2c3e50"></span> Original facility</span>
  <span><span class="dot" style="background:#2ecc71"></span> New (suggested)</span>
  <span><span class="dot" style="background:#e74c3c"></span> ★ Suggested location</span>
  <span><span class="dot" style="background:#4e79a7;opacity:0.5"></span> Demand points</span>
</div></div>

<div class="card">
<h2>📈 Facility Utilization</h2>
{bar_chart}
<div class="legend">
  <span><span class="dot" style="background:#2ecc71"></span> Normal (&le;0.85)</span>
  <span><span class="dot" style="background:#f39c12"></span> Warning (0.85-1.2)</span>
  <span><span class="dot" style="background:#e74c3c"></span> Overloaded (&gt;1.2)</span>
</div></div>

<div class="card">
<h2>📋 Facility Details</h2>
<div style="max-height:400px;overflow-y:auto">
<table>
<tr><th>#</th><th>Position</th><th>Load</th><th>Utilization</th><th>Status</th></tr>
"""
    for fi in range(len(facilities)):
        load = metrics["loads"][fi] if fi < len(metrics["loads"]) else 0
        util = metrics["utilizations"][fi] if fi < len(metrics["utilizations"]) else 0
        pos = facilities[fi]
        if util > 1.2:
            badge = '<span class="badge badge-red">Overloaded</span>'
        elif util > 0.85:
            badge = '<span class="badge badge-yellow">Warning</span>'
        else:
            badge = '<span class="badge badge-green">OK</span>'
        is_new = " ⭐" if fi >= original_count and original_count > 0 else ""
        html += f'<tr><td>{fi}{is_new}</td><td>({pos[0]:.2f}, {pos[1]:.2f})</td>'
        html += f'<td>{load}</td><td>{util:.2f}x</td><td>{badge}</td></tr>\n'

    html += """</table></div></div>
"""

    if suggestions:
        html += '<div class="card"><h2>💡 Suggested New Facilities</h2><table>'
        html += '<tr><th>#</th><th>Position</th><th>Rationale</th></tr>'
        for i, (sx, sy) in enumerate(suggestions):
            html += f'<tr><td>New-{i+1}</td><td>({sx:.4f}, {sy:.4f})</td>'
            html += '<td>Centroid of far demand in overloaded cell</td></tr>'
        html += '</table></div>'

    html += """
<div class="card" style="text-align:center;color:#475569;font-size:0.8rem">
Generated by vormap_dispatch — Autonomous Spatial Dispatch Optimizer
</div></div></body></html>"""
    return html


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def read_csv_points(path):
    """Read x,y[,weight] CSV. Returns (points, weights)."""
    points = []
    weights = []
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            x = float(row.get('x', row.get('X', 0)))
            y = float(row.get('y', row.get('Y', 0)))
            w = float(row.get('weight', row.get('Weight', 1.0)))
            points.append((x, y))
            weights.append(w)
    return points, weights


def demo():
    """Run demo with synthetic data.

    Uses a local ``random.Random`` instance so the demo does not
    perturb the host process's global RNG state. See issue #192.
    """
    rng = random.Random(42)
    # Generate clustered facilities
    facilities = []
    for _ in range(25):
        facilities.append((rng.uniform(5, 95), rng.uniform(5, 95)))

    # Generate demand points with clusters (simulating population centers)
    demands = []
    weights = []
    centers = [(20, 20), (80, 30), (50, 70), (30, 80), (75, 75)]
    for _ in range(500):
        if rng.random() < 0.7:
            # Clustered demand
            cx, cy = rng.choice(centers)
            demands.append((cx + rng.gauss(0, 10), cy + rng.gauss(0, 10)))
        else:
            # Uniform demand
            demands.append((rng.uniform(0, 100), rng.uniform(0, 100)))
        weights.append(rng.uniform(0.5, 2.0))

    # Compute auto capacity
    total_weight = sum(weights)
    capacity = total_weight / len(facilities) * 1.5

    print("=== vormap_dispatch Demo ===")
    print(f"Facilities: {len(facilities)}")
    print(f"Demand points: {len(demands)}")
    print(f"Total demand weight: {total_weight:.1f}")
    print(f"Auto capacity per facility: {capacity:.1f}")
    print()

    # Initial assignment
    assignments = assign_demand(facilities, demands, weights)
    metrics = compute_metrics(facilities, demands, assignments, weights)
    print("--- Initial Assignment ---")
    print(f"  Avg distance:     {metrics['avg_distance']}")
    print(f"  P95 distance:     {metrics['p95_distance']}")
    print(f"  Max distance:     {metrics['max_distance']}")
    print(f"  Load Gini:        {metrics['gini']}")
    print(f"  Max utilization:  {metrics['max_utilization']}x")
    overloaded = detect_overloaded(assignments, weights, capacity, len(facilities))
    print(f"  Overloaded:       {len(overloaded)} facilities")
    print()

    # Auto-dispatch
    print("--- Auto-Dispatch ---")
    original_count = len(facilities)
    facs, assignments, metrics, suggestions = auto_dispatch(
        facilities, demands, weights, capacity, target_util=0.85, max_new=10
    )
    print(f"  New facilities suggested: {len(suggestions)}")
    print(f"  Total facilities:   {len(facs)}")
    print(f"  Avg distance:       {metrics['avg_distance']}")
    print(f"  P95 distance:       {metrics['p95_distance']}")
    print(f"  Max utilization:    {metrics['max_utilization']}x")
    print(f"  Load Gini:          {metrics['gini']}")
    print()

    if suggestions:
        print("  Suggested locations:")
        for i, (sx, sy) in enumerate(suggestions):
            print(f"    New-{i+1}: ({sx:.4f}, {sy:.4f})")
        print()

    # Generate report
    report_path = "dispatch_report.html"
    html = generate_html_report(facs, demands, assignments, metrics, weights,
                                suggestions, original_count)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Report saved to {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="vormap_dispatch — Autonomous Spatial Dispatch Optimizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_dispatch.py --demo
  python vormap_dispatch.py facilities.csv demands.csv --report dispatch.html
  python vormap_dispatch.py facilities.csv demands.csv --auto --target-util 0.8
  python vormap_dispatch.py facilities.csv demands.csv --json results.json
""",
    )
    parser.add_argument("facilities", nargs="?", help="Facilities CSV (x,y)")
    parser.add_argument("demands", nargs="?", help="Demand points CSV (x,y[,weight])")
    parser.add_argument("--capacity", type=float, default=0,
                        help="Max demand per facility (0=auto: total/n*1.5)")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-suggest new facilities until balanced")
    parser.add_argument("--max-new", type=int, default=10,
                        help="Max new facilities to suggest (default: 10)")
    parser.add_argument("--target-util", type=float, default=0.85,
                        help="Target max utilization ratio (default: 0.85)")
    parser.add_argument("--report", default="dispatch_report.html",
                        help="HTML report output path")
    parser.add_argument("--json", dest="json_path", default="",
                        help="Export results as JSON")
    parser.add_argument("--demo", action="store_true", help="Run with sample data")

    args = parser.parse_args()

    if args.demo:
        demo()
        return

    if not args.facilities or not args.demands:
        parser.error("Provide facilities and demands CSV files, or use --demo")

    facilities, _ = read_csv_points(args.facilities)
    demands, weights = read_csv_points(args.demands)

    if not facilities:
        print("Error: No facilities loaded.", file=sys.stderr)
        sys.exit(1)
    if not demands:
        print("Error: No demand points loaded.", file=sys.stderr)
        sys.exit(1)

    total_weight = sum(weights)
    capacity = args.capacity if args.capacity > 0 else total_weight / len(facilities) * 1.5

    print(f"Loaded {len(facilities)} facilities, {len(demands)} demand points")
    print(f"Capacity per facility: {capacity:.2f}")

    original_count = len(facilities)
    suggestions = []

    if args.auto:
        facilities, assignments, metrics, suggestions = auto_dispatch(
            facilities, demands, weights, capacity, args.target_util, args.max_new
        )
        print(f"Auto-dispatch: added {len(suggestions)} suggested facilities")
    else:
        assignments = assign_demand(facilities, demands, weights)
        metrics = compute_metrics(facilities, demands, assignments, weights)
        overloaded = detect_overloaded(assignments, weights, capacity, len(facilities))
        if overloaded:
            suggestions = suggest_new_facilities(
                facilities, demands, assignments, weights, overloaded, args.max_new
            )
            print(f"Detected {len(overloaded)} overloaded facilities")
            print(f"Suggested {len(suggestions)} new facility locations")

    print("\nMetrics:")
    print(f"  Avg distance:    {metrics['avg_distance']}")
    print(f"  P95 distance:    {metrics['p95_distance']}")
    print(f"  Max distance:    {metrics['max_distance']}")
    print(f"  Load Gini:       {metrics['gini']}")
    print(f"  Max utilization: {metrics['max_utilization']}x")

    # HTML report
    html = generate_html_report(facilities, demands, assignments, metrics, weights,
                                suggestions, original_count)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nReport saved to {args.report}")

    # JSON export
    if args.json_path:
        result = {
            "facilities": [{"x": f[0], "y": f[1]} for f in facilities],
            "metrics": metrics,
            "suggestions": [{"x": s[0], "y": s[1]} for s in suggestions],
            "assignments": {str(k): v for k, v in assignments.items()},
        }
        with open(args.json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"JSON saved to {args.json_path}")


if __name__ == "__main__":
    main()
