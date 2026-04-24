#!/usr/bin/env python3
"""vormap_balance - Autonomous Spatial Load Balancer

Analyzes Voronoi cell area distributions and autonomously rebalances point
placements to achieve spatial equity. Uses Gini coefficient, coefficient of
variation, and entropy as equity metrics. Supports iterative auto-rebalancing
with convergence detection and generates interactive HTML reports.

Usage:
    python vormap_balance.py points.csv --auto --target-gini 0.15
    python vormap_balance.py points.csv --report balance_report.html
    python vormap_balance.py points.csv --max-iters 50 --strategy centroid
    python vormap_balance.py --demo
"""

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict

# ---------------------------------------------------------------------------
# Geometry helpers (no heavy deps)
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


def polygon_area(vertices):
    n = len(vertices)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        j = (i + 1) % n
        a += vertices[i][0] * vertices[j][1]
        a -= vertices[j][0] * vertices[i][1]
    return abs(a) / 2.0


def polygon_centroid(vertices):
    n = len(vertices)
    if n == 0:
        return (0, 0)
    if n == 1:
        return vertices[0]
    cx, cy, a6 = 0.0, 0.0, 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross
        a6 += cross
    if abs(a6) < 1e-12:
        return (sum(v[0] for v in vertices) / n, sum(v[1] for v in vertices) / n)
    a6 *= 3.0
    return (cx / a6, cy / a6)


# ---------------------------------------------------------------------------
# Simple Voronoi via bounding-box clipped cells (no scipy needed)
# ---------------------------------------------------------------------------

def _voronoi_cells_simple(points, bbox):
    """Approximate Voronoi cells by sampling + nearest-neighbor assignment."""
    xmin, ymin, xmax, ymax = bbox
    n = len(points)
    # Assign grid points to nearest site
    res = max(80, int(math.sqrt(n) * 20))
    dx = (xmax - xmin) / res
    dy = (ymax - ymin) / res
    cell_points = defaultdict(list)
    for gy in range(res):
        py = ymin + (gy + 0.5) * dy
        for gx in range(res):
            px = xmin + (gx + 0.5) * dx
            best_i, best_d = 0, float('inf')
            for i, (sx, sy) in enumerate(points):
                d = (px - sx) ** 2 + (py - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            cell_points[best_i].append((px, py))
    # Compute areas as fraction of bbox area
    total_area = (xmax - xmin) * (ymax - ymin)
    cell_area = dx * dy
    areas = []
    centroids = []
    for i in range(n):
        pts = cell_points.get(i, [])
        areas.append(len(pts) * cell_area)
        if pts:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            centroids.append((cx, cy))
        else:
            centroids.append(points[i])
    return areas, centroids


# ---------------------------------------------------------------------------
# Equity metrics
# ---------------------------------------------------------------------------

def gini_coefficient(values):
    if not values or all(v == 0 for v in values):
        return 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    cumsum = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(sorted_v):
        cumsum += v
        weighted_sum += (2 * (i + 1) - n - 1) * v
    return weighted_sum / (n * cumsum) if cumsum > 0 else 0.0


def coeff_of_variation(values):
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) / mean


def shannon_entropy(values):
    total = sum(values)
    if total == 0:
        return 0.0
    h = 0.0
    for v in values:
        if v > 0:
            p = v / total
            h -= p * math.log2(p)
    return h


def max_entropy(n):
    return math.log2(n) if n > 0 else 0.0


def equity_score(values):
    """0-100 score where 100 = perfectly balanced."""
    g = gini_coefficient(values)
    return max(0.0, (1.0 - g) * 100.0)


# ---------------------------------------------------------------------------
# Rebalancing strategies
# ---------------------------------------------------------------------------

def rebalance_centroid(points, areas, centroids, bbox, strength=0.5):
    """Move each point toward its cell centroid (Lloyd relaxation variant)."""
    xmin, ymin, xmax, ymax = bbox
    new_points = []
    for i, (px, py) in enumerate(points):
        cx, cy = centroids[i]
        nx = px + strength * (cx - px)
        ny = py + strength * (cy - py)
        nx = max(xmin, min(xmax, nx))
        ny = max(ymin, min(ymax, ny))
        new_points.append((nx, ny))
    return new_points


def rebalance_repulsion(points, areas, centroids, bbox, strength=0.3):
    """Push points away from dense regions toward sparse ones."""
    xmin, ymin, xmax, ymax = bbox
    n = len(points)
    mean_area = sum(areas) / n if n else 1.0
    new_points = list(points)
    for i in range(n):
        fx, fy = 0.0, 0.0
        ratio = areas[i] / mean_area if mean_area > 0 else 1.0
        if ratio < 0.5:
            # Point is in a cramped cell — push away from nearest neighbors
            for j in range(n):
                if i == j:
                    continue
                dx = points[i][0] - points[j][0]
                dy = points[i][1] - points[j][1]
                d2 = dx * dx + dy * dy
                if d2 < 1e-12:
                    continue
                d = math.sqrt(d2)
                force = strength * (1.0 - ratio) / d
                fx += dx / d * force
                fy += dy / d * force
        elif ratio > 1.5:
            # Point has too much space — pull toward centroid
            cx, cy = centroids[i]
            fx = strength * (cx - points[i][0]) * 0.5
            fy = strength * (cy - points[i][1]) * 0.5
        nx = points[i][0] + fx
        ny = points[i][1] + fy
        new_points[i] = (max(xmin, min(xmax, nx)), max(ymin, min(ymax, ny)))
    return new_points


def rebalance_adaptive(points, areas, centroids, bbox, strength=0.5):
    """Hybrid: use centroid for large cells, repulsion for small cells."""
    xmin, ymin, xmax, ymax = bbox
    n = len(points)
    mean_area = sum(areas) / n if n else 1.0
    new_points = []
    for i, (px, py) in enumerate(points):
        ratio = areas[i] / mean_area if mean_area > 0 else 1.0
        cx, cy = centroids[i]
        if ratio < 0.7:
            # Small cell: stronger push toward centroid + slight outward
            s = strength * 0.8
        elif ratio > 1.3:
            # Large cell: gentle pull toward centroid
            s = strength * 0.3
        else:
            s = strength * 0.5
        nx = px + s * (cx - px)
        ny = py + s * (cy - py)
        new_points.append((max(xmin, min(xmax, nx)), max(ymin, min(ymax, ny))))
    return new_points


STRATEGIES = {
    'centroid': rebalance_centroid,
    'repulsion': rebalance_repulsion,
    'adaptive': rebalance_adaptive,
}

# ---------------------------------------------------------------------------
# Auto-rebalance loop
# ---------------------------------------------------------------------------

def auto_rebalance(points, bbox, strategy='adaptive', target_gini=0.15,
                   max_iters=100, strength=0.5, verbose=False):
    history = []
    current = list(points)
    rebalance_fn = STRATEGIES.get(strategy, rebalance_adaptive)

    for iteration in range(max_iters + 1):
        areas, centroids = _voronoi_cells_simple(current, bbox)
        g = gini_coefficient(areas)
        cv = coeff_of_variation(areas)
        es = equity_score(areas)
        ent = shannon_entropy(areas)

        history.append({
            'iteration': iteration,
            'gini': round(g, 4),
            'cv': round(cv, 4),
            'equity_score': round(es, 2),
            'entropy': round(ent, 4),
            'points': [list(p) for p in current],
            'areas': [round(a, 4) for a in areas],
        })

        if verbose:
            print(f"  Iter {iteration:3d}: Gini={g:.4f}  CV={cv:.4f}  Equity={es:.1f}%  Entropy={ent:.4f}")

        if g <= target_gini and iteration > 0:
            if verbose:
                print(f"  ✓ Target Gini ({target_gini}) reached at iteration {iteration}")
            break

        if iteration == max_iters:
            if verbose:
                print(f"  ⚠ Max iterations ({max_iters}) reached. Gini={g:.4f}")
            break

        # Check convergence (Gini barely changing)
        if len(history) >= 5:
            recent = [h['gini'] for h in history[-5:]]
            if max(recent) - min(recent) < 0.001:
                if verbose:
                    print(f"  ✓ Converged at iteration {iteration} (Gini stable)")
                break

        current = rebalance_fn(current, areas, centroids, bbox, strength)

    return current, history


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def generate_recommendations(history):
    recs = []
    if not history:
        return recs
    first = history[0]
    last = history[-1]
    gini_drop = first['gini'] - last['gini']

    if last['gini'] > 0.3:
        recs.append({
            'severity': 'HIGH',
            'message': f"Distribution remains highly unequal (Gini={last['gini']:.3f}). "
                       f"Consider adding more points in sparse regions or removing outliers."
        })
    elif last['gini'] > 0.15:
        recs.append({
            'severity': 'MEDIUM',
            'message': f"Moderate inequality remains (Gini={last['gini']:.3f}). "
                       f"Try more iterations or a different strategy."
        })
    else:
        recs.append({
            'severity': 'OK',
            'message': f"Good spatial equity achieved (Gini={last['gini']:.3f})."
        })

    if gini_drop < 0.01 and len(history) > 5:
        recs.append({
            'severity': 'WARN',
            'message': "Rebalancing had minimal effect. Points may already be near-optimal "
                       "or the strategy may not suit this distribution."
        })

    # Detect if some cells are extremely small
    areas = last['areas']
    mean_a = sum(areas) / len(areas)
    tiny = [i for i, a in enumerate(areas) if a < mean_a * 0.2]
    if tiny:
        recs.append({
            'severity': 'HIGH',
            'message': f"{len(tiny)} cells are <20% of mean area. These points are clustered too tightly. "
                       f"Consider merging or redistributing them."
        })

    huge = [i for i, a in enumerate(areas) if a > mean_a * 3.0]
    if huge:
        recs.append({
            'severity': 'MEDIUM',
            'message': f"{len(huge)} cells are >3x mean area. These regions are under-served. "
                       f"Consider adding points in void areas."
        })

    return recs


# ---------------------------------------------------------------------------
# HTML Report
# ---------------------------------------------------------------------------

def generate_html_report(history, recommendations, bbox):
    first = history[0] if history else {}
    last = history[-1] if history else {}
    n_iters = len(history)
    n_points = len(last.get('points', []))

    gini_data = json.dumps([h['gini'] for h in history])
    equity_data = json.dumps([h['equity_score'] for h in history])
    cv_data = json.dumps([h['cv'] for h in history])

    initial_pts = json.dumps(history[0]['points']) if history else '[]'
    final_pts = json.dumps(last.get('points', []))
    bbox_json = json.dumps(list(bbox))

    recs_html = ""
    for r in recommendations:
        color = {'HIGH': '#ef4444', 'MEDIUM': '#f59e0b', 'WARN': '#f59e0b', 'OK': '#22c55e'}.get(r['severity'], '#6b7280')
        recs_html += f'<div style="border-left:4px solid {color};padding:8px 12px;margin:6px 0;background:#1a1a2e;border-radius:4px"><strong style="color:{color}">{r["severity"]}</strong> {r["message"]}</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voronoi Spatial Balance Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f23;color:#e0e0e0;padding:20px}}
h1{{text-align:center;font-size:1.8em;margin-bottom:6px;color:#00d4ff}}
.subtitle{{text-align:center;color:#888;margin-bottom:20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:20px}}
.card{{background:#16213e;border-radius:12px;padding:16px;border:1px solid #1a1a3e}}
.card h2{{font-size:1.1em;color:#00d4ff;margin-bottom:10px}}
.metric{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1a1a3e}}
.metric:last-child{{border:none}}
.metric .label{{color:#888}}
.metric .value{{font-weight:bold;color:#fff}}
.improve{{color:#22c55e}}
.degrade{{color:#ef4444}}
canvas{{width:100%;height:250px;display:block;margin-top:8px}}
.viz-canvas{{width:100%;height:300px}}
.recs{{margin-top:10px}}
</style>
</head>
<body>
<h1>⚖️ Voronoi Spatial Balance Report</h1>
<p class="subtitle">{n_points} points · {n_iters} iterations · Gini {first.get('gini','?')} → {last.get('gini','?')}</p>

<div class="grid">
  <div class="card">
    <h2>📊 Metrics Summary</h2>
    <div class="metric"><span class="label">Initial Gini</span><span class="value">{first.get('gini','—')}</span></div>
    <div class="metric"><span class="label">Final Gini</span><span class="value {'improve' if last.get('gini',1)<first.get('gini',0) else ''}">{last.get('gini','—')}</span></div>
    <div class="metric"><span class="label">Equity Score</span><span class="value">{last.get('equity_score','—')}%</span></div>
    <div class="metric"><span class="label">CV</span><span class="value">{last.get('cv','—')}</span></div>
    <div class="metric"><span class="label">Entropy</span><span class="value">{last.get('entropy','—')}</span></div>
    <div class="metric"><span class="label">Iterations</span><span class="value">{n_iters - 1}</span></div>
  </div>

  <div class="card">
    <h2>📈 Gini Over Iterations</h2>
    <canvas id="giniChart"></canvas>
  </div>

  <div class="card">
    <h2>🎯 Equity Score Over Iterations</h2>
    <canvas id="equityChart"></canvas>
  </div>

  <div class="card">
    <h2>🤖 Recommendations</h2>
    <div class="recs">{recs_html if recs_html else '<p style="color:#888">No recommendations.</p>'}</div>
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2>Before (Initial)</h2>
    <canvas id="beforeCanvas" class="viz-canvas"></canvas>
  </div>
  <div class="card">
    <h2>After (Rebalanced)</h2>
    <canvas id="afterCanvas" class="viz-canvas"></canvas>
  </div>
</div>

<script>
const giniData = {gini_data};
const equityData = {equity_data};
const cvData = {cv_data};
const initialPts = {initial_pts};
const finalPts = {final_pts};
const bbox = {bbox_json};

function drawLineChart(canvasId, data, color, label) {{
  const c = document.getElementById(canvasId);
  const ctx = c.getContext('2d');
  c.width = c.offsetWidth * 2; c.height = c.offsetHeight * 2;
  ctx.scale(2, 2);
  const w = c.offsetWidth, h = c.offsetHeight;
  const pad = 40;
  const mn = Math.min(...data), mx = Math.max(...data);
  const range = mx - mn || 1;

  ctx.fillStyle = '#16213e'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
  for (let i = 0; i <= 4; i++) {{
    const y = pad + (h - 2 * pad) * i / 4;
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(w - 10, y); ctx.stroke();
    ctx.fillStyle = '#666'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
    ctx.fillText((mx - range * i / 4).toFixed(3), pad - 4, y + 3);
  }}

  ctx.strokeStyle = color; ctx.lineWidth = 2;
  ctx.beginPath();
  data.forEach((v, i) => {{
    const x = pad + (w - pad - 10) * i / Math.max(data.length - 1, 1);
    const y = pad + (h - 2 * pad) * (1 - (v - mn) / range);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }});
  ctx.stroke();

  ctx.fillStyle = color; ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left';
  ctx.fillText(label, pad + 4, pad - 8);
}}

function drawPoints(canvasId, pts, color) {{
  const c = document.getElementById(canvasId);
  const ctx = c.getContext('2d');
  c.width = c.offsetWidth * 2; c.height = c.offsetHeight * 2;
  ctx.scale(2, 2);
  const w = c.offsetWidth, h = c.offsetHeight;
  const pad = 20;
  const [xmin, ymin, xmax, ymax] = bbox;
  const sx = (w - 2 * pad) / (xmax - xmin || 1);
  const sy = (h - 2 * pad) / (ymax - ymin || 1);
  const s = Math.min(sx, sy);

  ctx.fillStyle = '#16213e'; ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#333'; ctx.lineWidth = 0.5;
  ctx.strokeRect(pad, pad, (xmax-xmin)*s, (ymax-ymin)*s);

  pts.forEach(([x, y]) => {{
    const px = pad + (x - xmin) * s;
    const py = pad + (ymax - y) * s;  // flip y
    ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2);
    ctx.fillStyle = color; ctx.fill();
  }});
}}

drawLineChart('giniChart', giniData, '#00d4ff', 'Gini Coefficient');
drawLineChart('equityChart', equityData, '#22c55e', 'Equity Score (%)');
drawPoints('beforeCanvas', initialPts, '#ef4444');
drawPoints('afterCanvas', finalPts, '#22c55e');
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def read_points(path):
    points = []
    with open(path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].strip().startswith('#'):
                continue
            try:
                x, y = float(row[0]), float(row[1])
                points.append((x, y))
            except (ValueError, IndexError):
                continue
    return points


def write_points(path, points):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['x', 'y'])
        for x, y in points:
            writer.writerow([round(x, 6), round(y, 6)])


def compute_bbox(points, margin=0.05):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    dx = (xmax - xmin) * margin
    dy = (ymax - ymin) * margin
    return (xmin - dx, ymin - dy, xmax + dx, ymax + dy)


def generate_demo_points(n=30, seed=42):
    rng = random.Random(seed)
    points = []
    # Cluster 1: tight
    for _ in range(n // 3):
        points.append((rng.gauss(0.2, 0.05), rng.gauss(0.2, 0.05)))
    # Cluster 2: medium
    for _ in range(n // 3):
        points.append((rng.gauss(0.7, 0.12), rng.gauss(0.5, 0.12)))
    # Sparse fill
    for _ in range(n - 2 * (n // 3)):
        points.append((rng.uniform(0, 1), rng.uniform(0, 1)))
    return points


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Autonomous Spatial Load Balancer for Voronoi distributions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_balance.py points.csv --auto --target-gini 0.15
  python vormap_balance.py points.csv --report report.html --strategy centroid
  python vormap_balance.py --demo --report demo_report.html
  python vormap_balance.py points.csv --analyze-only
"""
    )
    parser.add_argument('input', nargs='?', help='Input CSV file (x,y columns)')
    parser.add_argument('--demo', action='store_true', help='Run with demo data')
    parser.add_argument('--auto', action='store_true', help='Enable autonomous rebalancing')
    parser.add_argument('--analyze-only', action='store_true', help='Only compute metrics, no rebalancing')
    parser.add_argument('--strategy', choices=['centroid', 'repulsion', 'adaptive'],
                        default='adaptive', help='Rebalancing strategy (default: adaptive)')
    parser.add_argument('--target-gini', type=float, default=0.15,
                        help='Target Gini coefficient (default: 0.15)')
    parser.add_argument('--max-iters', type=int, default=100,
                        help='Maximum rebalancing iterations (default: 100)')
    parser.add_argument('--strength', type=float, default=0.5,
                        help='Rebalancing strength 0-1 (default: 0.5)')
    parser.add_argument('--output', '-o', help='Output CSV with rebalanced points')
    parser.add_argument('--report', help='Generate interactive HTML report')
    parser.add_argument('--json', help='Export metrics history as JSON')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    if not args.input and not args.demo:
        parser.print_help()
        sys.exit(1)

    # Load points
    if args.demo:
        points = generate_demo_points()
        print(f"🎲 Generated {len(points)} demo points (clustered distribution)")
    else:
        if not os.path.exists(args.input):
            print(f"Error: File not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        points = read_points(args.input)
        if len(points) < 3:
            print("Error: Need at least 3 points", file=sys.stderr)
            sys.exit(1)
        print(f"📍 Loaded {len(points)} points from {args.input}")

    bbox = compute_bbox(points)

    # Initial analysis
    areas, centroids = _voronoi_cells_simple(points, bbox)
    g = gini_coefficient(areas)
    cv = coeff_of_variation(areas)
    es = equity_score(areas)
    ent = shannon_entropy(areas)

    print(f"\n📊 Initial Distribution Analysis:")
    print(f"   Gini Coefficient:     {g:.4f}")
    print(f"   Coeff. of Variation:  {cv:.4f}")
    print(f"   Equity Score:         {es:.1f}%")
    print(f"   Shannon Entropy:      {ent:.4f} / {max_entropy(len(points)):.4f}")

    if args.analyze_only:
        recs = generate_recommendations([{
            'iteration': 0, 'gini': round(g, 4), 'cv': round(cv, 4),
            'equity_score': round(es, 2), 'entropy': round(ent, 4),
            'points': [list(p) for p in points], 'areas': [round(a, 4) for a in areas]
        }])
        print(f"\n🤖 Recommendations:")
        for r in recs:
            print(f"   [{r['severity']}] {r['message']}")
        return

    # Rebalance
    if args.auto or args.report:
        print(f"\n⚖️ Auto-rebalancing (strategy={args.strategy}, target_gini={args.target_gini})...")
        final_points, history = auto_rebalance(
            points, bbox,
            strategy=args.strategy,
            target_gini=args.target_gini,
            max_iters=args.max_iters,
            strength=args.strength,
            verbose=args.verbose,
        )

        last = history[-1]
        improvement = g - last['gini']
        print(f"\n✅ Rebalancing complete:")
        print(f"   Gini:    {g:.4f} → {last['gini']:.4f} (Δ{improvement:+.4f})")
        print(f"   Equity:  {es:.1f}% → {last['equity_score']:.1f}%")
        print(f"   Iters:   {len(history) - 1}")

        recs = generate_recommendations(history)
        print(f"\n🤖 Recommendations:")
        for r in recs:
            print(f"   [{r['severity']}] {r['message']}")

        if args.output:
            write_points(args.output, final_points)
            print(f"\n💾 Rebalanced points saved to {args.output}")

        if args.report:
            html = generate_html_report(history, recs, bbox)
            with open(args.report, 'w') as f:
                f.write(html)
            print(f"📄 Report saved to {args.report}")

        if args.json:
            with open(args.json, 'w') as f:
                json.dump({'history': history, 'recommendations': [r for r in recs]}, f, indent=2)
            print(f"📋 JSON export saved to {args.json}")
    else:
        print("\nTip: Use --auto to enable autonomous rebalancing, or --analyze-only for metrics only.")


if __name__ == '__main__':
    main()
