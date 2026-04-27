#!/usr/bin/env python3
"""vormap_referee.py - Autonomous Spatial Fairness Referee

Analyzes Voronoi partitions for territorial equity, detects imbalanced
allocations, grades fairness A-F, and suggests redistricting via Lloyd
relaxation.  Generates interactive HTML reports.

Usage:
    python vormap_referee.py points.csv
    python vormap_referee.py points.csv --auto-fix
    python vormap_referee.py points.csv --weights w.csv
    python vormap_referee.py points.csv --watch
    python vormap_referee.py points.csv -o report.html
"""

import argparse, csv, hashlib, json, math, os, sys, time
from collections import defaultdict

from vormap_utils import euclidean as _dist

# ---------------------------------------------------------------------------
# Geometry helpers (pure Python, no deps)
# ---------------------------------------------------------------------------

def _bbox(pts, pad=0.10):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    dx = (max(xs) - min(xs)) * pad or 1.0
    dy = (max(ys) - min(ys)) * pad or 1.0
    return (min(xs) - dx, min(ys) - dy, max(xs) + dx, max(ys) + dy)

# Grid-based Voronoi: assigns each pixel to nearest generator
def _grid_voronoi(pts, bbox, res=500):
    x0, y0, x1, y1 = bbox
    sx, sy = (x1 - x0) / res, (y1 - y0) / res
    grid = [[0]*res for _ in range(res)]
    n = len(pts)
    areas = [0]*n
    cx = [0.0]*n; cy = [0.0]*n
    # Pre-extract coordinates to avoid per-pixel tuple unpacking
    gxs = [p[0] for p in pts]
    gys = [p[1] for p in pts]
    # Pre-compute column x-coords once
    col_x = [x0 + (c + 0.5) * sx for c in range(res)]
    for r in range(res):
        py = y0 + (r + 0.5) * sy
        row = grid[r]
        # Pre-compute (py - gy)^2 for all generators once per row
        dy2 = [(py - gys[i])**2 for i in range(n)]
        for c in range(res):
            px = col_x[c]
            best, bd = 0, float('inf')
            for i in range(n):
                d = (px - gxs[i])**2 + dy2[i]
                if d < bd:
                    bd = d; best = i
            row[c] = best
            areas[best] += 1
            cx[best] += px; cy[best] += py
    cell_area = sx * sy
    real_areas = [a * cell_area for a in areas]
    centroids = []
    for i in range(len(pts)):
        if areas[i] > 0:
            centroids.append((cx[i]/areas[i], cy[i]/areas[i]))
        else:
            centroids.append(pts[i])
    return grid, real_areas, centroids, areas

# Single-pass perimeter + neighbor adjacency from grid
# Merges two O(res²) scans into one, and fixes a bug where vertical
# adjacency previously only checked column res-2 (stale loop variable).
def _grid_perimeters_and_neighbors(grid, res, n, cell_area_per_pixel_side_x, cell_area_per_pixel_side_y):
    perims = [0.0]*n
    adj = [set() for _ in range(n)]
    avg_side = (cell_area_per_pixel_side_x + cell_area_per_pixel_side_y) / 2
    last_row = res - 1
    last_col = res - 1
    for r in range(res):
        row = grid[r]
        next_row = grid[r + 1] if r < last_row else None
        for c in range(res):
            v = row[c]
            is_boundary = (r == 0 or r == last_row or c == 0 or c == last_col)
            # Check right neighbor
            if c < last_col:
                nb = row[c + 1]
                if nb != v:
                    is_boundary = True
                    adj[v].add(nb); adj[nb].add(v)
            # Check bottom neighbor
            if next_row is not None:
                nb = next_row[c]
                if nb != v:
                    is_boundary = True
                    adj[v].add(nb); adj[nb].add(v)
            if is_boundary:
                perims[v] += avg_side
    return perims, [len(s) for s in adj]

def _gini(values):
    if not values: return 0.0
    s = sorted(values); n = len(s); total = sum(s)
    if total == 0: return 0.0
    cum = 0; gini_sum = 0
    for i, v in enumerate(s):
        cum += v
        gini_sum += (2*(i+1) - n - 1) * v
    return gini_sum / (n * total)

def _mean(v): return sum(v)/len(v) if v else 0
def _std(v):
    m = _mean(v)
    return math.sqrt(sum((x-m)**2 for x in v)/len(v)) if v else 0

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(pts, bbox, weights=None, res=500, do_autofix=False):
    n = len(pts)
    grid, areas, centroids, pixel_counts = _grid_voronoi(pts, bbox, res)
    x0, y0, x1, y1 = bbox
    sx, sy = (x1-x0)/res, (y1-y0)/res
    perims, neighbors = _grid_perimeters_and_neighbors(grid, res, n, sx, sy)

    # Compactness (Polsby-Popper)
    compactness = []
    for i in range(n):
        if perims[i] > 0:
            compactness.append(4*math.pi*areas[i]/(perims[i]**2))
        else:
            compactness.append(0)

    # Access fairness: distance from generator to cell centroid
    access = [_dist(pts[i], centroids[i]) for i in range(n)]

    # Equity metrics
    gini = _gini(areas)
    area_mean = _mean(areas); area_std = _std(areas)
    comp_mean = _mean(compactness)
    access_mean = _mean(access); access_std = _std(access)
    neigh_mean = _mean(neighbors); neigh_std = _std(neighbors)

    # Flags
    area_flags = [abs(areas[i]-area_mean) > 2*area_std for i in range(n)]
    compact_flags = [compactness[i] < 0.3 for i in range(n)]
    access_flags = [access[i] > access_mean + 2*access_std for i in range(n)]
    neigh_flags = [abs(neighbors[i]-neigh_mean) > 2*neigh_std for i in range(n)]

    # Weighted fairness
    weighted_fairness = None
    if weights and len(weights) == n:
        area_per_weight = [areas[i]/(weights[i] if weights[i] > 0 else 1) for i in range(n)]
        weighted_fairness = {"values": area_per_weight, "gini": _gini(area_per_weight)}

    # Composite score 0-100
    equity_score = max(0, 100 - gini*200)
    compact_score = min(100, comp_mean * 200)
    access_score = max(0, 100 - (access_mean / (math.hypot(x1-x0, y1-y0)/2)) * 200) if math.hypot(x1-x0,y1-y0) > 0 else 100
    neigh_score = max(0, 100 - (neigh_std/max(neigh_mean,1))*100)
    composite = 0.35*equity_score + 0.25*compact_score + 0.25*access_score + 0.15*neigh_score

    grade = 'A' if composite >= 85 else 'B' if composite >= 70 else 'C' if composite >= 55 else 'D' if composite >= 40 else 'F'

    # Auto-fix via Lloyd relaxation
    fix_pts = None
    fix_result = None
    if do_autofix:
        fix_pts = list(centroids)  # one Lloyd iteration
        _, fix_areas, fix_centroids, _ = _grid_voronoi(fix_pts, bbox, res)
        fix_gini = _gini(fix_areas)
        fix_result = {"points": fix_pts, "gini": fix_gini, "improvement": gini - fix_gini}

    return {
        "n": n, "bbox": bbox, "areas": areas, "centroids": centroids,
        "compactness": compactness, "access": access, "neighbors": neighbors,
        "gini": gini, "area_mean": area_mean, "area_std": area_std,
        "comp_mean": comp_mean, "access_mean": access_mean,
        "neigh_mean": neigh_mean, "neigh_std": neigh_std,
        "area_flags": area_flags, "compact_flags": compact_flags,
        "access_flags": access_flags, "neigh_flags": neigh_flags,
        "weighted": weighted_fairness,
        "equity_score": equity_score, "compact_score": compact_score,
        "access_score": access_score, "neigh_score": neigh_score,
        "composite": composite, "grade": grade,
        "fix": fix_result, "grid": grid, "res": res,
    }

# ---------------------------------------------------------------------------
# HTML Report
# ---------------------------------------------------------------------------

def _html_report(pts, result, output_path):
    n = result["n"]; bbox = result["bbox"]
    x0, y0, x1, y1 = bbox
    grade = result["grade"]; composite = result["composite"]
    grade_colors = {'A': '#22c55e', 'B': '#84cc16', 'C': '#eab308', 'D': '#f97316', 'F': '#ef4444'}
    gc = grade_colors.get(grade, '#888')

    cells_json = json.dumps([{
        "x": pts[i][0], "y": pts[i][1],
        "area": round(result["areas"][i], 4),
        "compact": round(result["compactness"][i], 4),
        "access": round(result["access"][i], 4),
        "neighbors": result["neighbors"][i],
        "flagged": result["area_flags"][i] or result["compact_flags"][i],
    } for i in range(n)])

    fix_json = "null"
    if result["fix"]:
        fix_json = json.dumps({
            "points": [[round(p[0],4), round(p[1],4)] for p in result["fix"]["points"]],
            "gini": round(result["fix"]["gini"], 4),
            "improvement": round(result["fix"]["improvement"], 4),
        })

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Spatial Fairness Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}}
h1{{text-align:center;margin-bottom:4px;font-size:1.6rem}}
.subtitle{{text-align:center;color:#94a3b8;margin-bottom:20px;font-size:.9rem}}
.grade-banner{{text-align:center;padding:18px;border-radius:12px;margin-bottom:20px;background:{gc}22;border:2px solid {gc}}}
.grade-letter{{font-size:3rem;font-weight:900;color:{gc}}}
.grade-score{{font-size:1.1rem;color:#cbd5e1}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}}
.card{{background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155}}
.card h3{{font-size:.95rem;color:#94a3b8;margin-bottom:10px}}
.metric{{font-size:1.4rem;font-weight:700}}
.bar-row{{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:.8rem}}
.bar{{height:14px;border-radius:3px;min-width:2px}}
canvas{{width:100%;border-radius:10px;background:#1e293b;border:1px solid #334155;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse;font-size:.82rem}}
th,td{{padding:6px 8px;text-align:left;border-bottom:1px solid #334155}}
th{{color:#94a3b8;font-weight:600}}
.flag{{color:#f87171;font-weight:700}}
.ok{{color:#4ade80}}
.fix-section{{background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155;margin-bottom:16px}}
</style></head><body>
<h1>🏛️ Spatial Fairness Referee</h1>
<p class="subtitle">{n} territories analyzed</p>
<div class="grade-banner">
  <div class="grade-letter">{grade}</div>
  <div class="grade-score">Composite Fairness Score: {composite:.1f}/100</div>
</div>
<canvas id="vc" height="400"></canvas>
<div class="grid">
  <div class="card"><h3>📊 Area Equity (Gini)</h3><div class="metric">{result['gini']:.3f}</div><p style="color:#94a3b8;font-size:.8rem;margin-top:4px">0=perfect equity, 1=total inequality</p><div style="margin-top:8px"><div style="background:#334155;border-radius:4px;height:10px;width:100%"><div style="background:{gc};height:10px;border-radius:4px;width:{result['equity_score']}%"></div></div></div></div>
  <div class="card"><h3>🔵 Compactness</h3><div class="metric">{result['comp_mean']:.3f}</div><p style="color:#94a3b8;font-size:.8rem;margin-top:4px">Avg Polsby-Popper ratio (1=circle)</p>
    <div style="margin-top:8px">{_compact_bars(result)}</div>
  </div>
  <div class="card"><h3>🎯 Access Fairness</h3><div class="metric">{result['access_mean']:.3f}</div><p style="color:#94a3b8;font-size:.8rem;margin-top:4px">Avg distance: generator→centroid</p></div>
  <div class="card"><h3>🔗 Neighbor Balance</h3><div class="metric">{result['neigh_mean']:.1f} ± {result['neigh_std']:.1f}</div><p style="color:#94a3b8;font-size:.8rem;margin-top:4px">Avg neighbors per cell</p></div>
</div>
<div class="card" style="margin-bottom:16px"><h3>📋 Cell Details</h3>
<table><tr><th>#</th><th>Area</th><th>Compact</th><th>Access</th><th>Neighbors</th><th>Status</th></tr>
{''.join(_cell_row(i, result) for i in range(n))}
</table></div>
{"" if not result["fix"] else _fix_section(result)}
<script>
const cells={cells_json};
const bbox=[{x0},{y0},{x1},{y1}];
const fix={fix_json};
const cv=document.getElementById('vc'),ctx=cv.getContext('2d');
cv.width=cv.offsetWidth;cv.height=400;
const W=cv.width,H=cv.height;
function tx(x){{return (x-bbox[0])/(bbox[2]-bbox[0])*W}}
function ty(y){{return H-(y-bbox[1])/(bbox[3]-bbox[1])*H}}
// Draw grid-based Voronoi
const maxA=Math.max(...cells.map(c=>c.area)),minA=Math.min(...cells.map(c=>c.area));
cells.forEach((c,i)=>{{
  const t=(c.area-minA)/(maxA-minA||1);
  const r=Math.floor(50+t*180),g=Math.floor(200-t*150),b=Math.floor(50+50*(1-t));
  ctx.fillStyle=c.flagged?'rgba(248,113,113,0.6)':`rgb(${{r}},${{g}},${{b}})`;
  ctx.beginPath();ctx.arc(tx(c.x),ty(c.y),Math.max(4,Math.sqrt(c.area/(maxA||1))*30),0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#e2e8f0';ctx.font='bold 10px system-ui';ctx.textAlign='center';
  ctx.fillText(i,tx(c.x),ty(c.y)-Math.max(4,Math.sqrt(c.area/(maxA||1))*30)-4);
}});
// Draw point labels
ctx.fillStyle='#94a3b8';ctx.font='9px system-ui';
// Fix overlay
if(fix){{
  ctx.strokeStyle='rgba(74,222,128,0.5)';ctx.setLineDash([4,4]);
  fix.points.forEach((p,i)=>{{
    ctx.beginPath();ctx.moveTo(tx(cells[i].x),ty(cells[i].y));
    ctx.lineTo(tx(p[0]),ty(p[1]));ctx.stroke();
    ctx.fillStyle='rgba(74,222,128,0.7)';ctx.beginPath();
    ctx.arc(tx(p[0]),ty(p[1]),3,0,Math.PI*2);ctx.fill();
  }});
}}
</script></body></html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def _compact_bars(result):
    rows = []
    for i, c in enumerate(result["compactness"]):
        color = '#ef4444' if c < 0.3 else '#eab308' if c < 0.5 else '#22c55e'
        w = max(2, c * 100)
        rows.append(f'<div class="bar-row"><span style="width:20px">{i}</span><div class="bar" style="width:{w}%;background:{color}"></div><span>{c:.2f}</span></div>')
    return '\n'.join(rows[:20]) + (f'\n<p style="color:#64748b;font-size:.75rem">...and {len(result["compactness"])-20} more</p>' if len(result["compactness"]) > 20 else '')

def _cell_row(i, r):
    flagged = r["area_flags"][i] or r["compact_flags"][i] or r["access_flags"][i]
    status = '<span class="flag">⚠ FLAGGED</span>' if flagged else '<span class="ok">✓</span>'
    return f'<tr><td>{i}</td><td>{r["areas"][i]:.3f}</td><td>{r["compactness"][i]:.3f}</td><td>{r["access"][i]:.3f}</td><td>{r["neighbors"][i]}</td><td>{status}</td></tr>\n'

def _fix_section(result):
    f = result["fix"]
    return f"""<div class="fix-section"><h3>🔧 Auto-Fix Recommendations (Lloyd Relaxation)</h3>
<p style="margin:8px 0;color:#94a3b8">One iteration of Lloyd relaxation applied. Gini: {f['gini']:.4f} (improvement: {f['improvement']:+.4f})</p>
<p style="color:#4ade80;font-weight:600">{"✅ Redistricting would improve equity!" if f['improvement'] > 0 else "⚠ Already near-optimal or relaxation didn't help."}</p>
<p style="margin-top:8px;color:#94a3b8;font-size:.85rem">Green arrows on the map show suggested point movements.</p></div>"""

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_points(path):
    pts = []
    with open(path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    pts.append((float(row[0]), float(row[1])))
                except ValueError:
                    continue
    return pts

def load_weights(path):
    w = []
    with open(path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if row:
                try:
                    w.append(float(row[0]))
                except ValueError:
                    continue
    return w

def _file_hash(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Autonomous Spatial Fairness Referee')
    ap.add_argument('points', help='CSV file with x,y coordinates')
    ap.add_argument('--weights', help='CSV file with weight per point')
    ap.add_argument('--auto-fix', action='store_true', help='Suggest redistricting via Lloyd relaxation')
    ap.add_argument('--watch', action='store_true', help='Re-analyze on file change')
    ap.add_argument('-o', '--output', default='fairness_report.html', help='Output HTML path')
    ap.add_argument('--resolution', type=int, default=500, help='Grid resolution (default 500)')
    ap.add_argument('--json', action='store_true', help='Print JSON summary to stdout')
    args = ap.parse_args()

    def run_once():
        pts = load_points(args.points)
        if len(pts) < 3:
            print('Error: need at least 3 points', file=sys.stderr); sys.exit(1)
        weights = load_weights(args.weights) if args.weights else None
        bbox = _bbox(pts)
        result = analyze(pts, bbox, weights, args.resolution, args.auto_fix)

        _html_report(pts, result, args.output)

        # Console summary
        print(f'\n🏛️  Spatial Fairness Referee — Grade: {result["grade"]} ({result["composite"]:.1f}/100)')
        print(f'   Territories: {result["n"]}')
        print(f'   Gini coefficient: {result["gini"]:.4f}')
        print(f'   Avg compactness: {result["comp_mean"]:.4f}')
        print(f'   Avg access dist: {result["access_mean"]:.4f}')
        print(f'   Flagged cells: {sum(1 for i in range(result["n"]) if result["area_flags"][i] or result["compact_flags"][i])}')
        if result["fix"]:
            print(f'   Auto-fix Gini improvement: {result["fix"]["improvement"]:+.4f}')
        if result["weighted"]:
            print(f'   Weighted Gini: {result["weighted"]["gini"]:.4f}')
        print(f'   Report: {args.output}\n')

        if args.json:
            summary = {
                "grade": result["grade"], "composite": round(result["composite"],2),
                "gini": round(result["gini"],4), "compactness": round(result["comp_mean"],4),
                "access": round(result["access_mean"],4), "n": result["n"],
            }
            print(json.dumps(summary, indent=2))

        return result

    result = run_once()

    if args.watch:
        print('👁️  Watching for changes... (Ctrl+C to stop)')
        prev_hash = _file_hash(args.points)
        prev_grade = result["grade"]
        while True:
            time.sleep(2)
            try:
                h = _file_hash(args.points)
                if h != prev_hash:
                    prev_hash = h
                    print(f'\n🔄 File changed, re-analyzing...')
                    result = run_once()
                    if result["grade"] < prev_grade:
                        print(f'⚠️  ALERT: Fairness degraded! {prev_grade} → {result["grade"]}')
                    prev_grade = result["grade"]
            except KeyboardInterrupt:
                print('\nStopped.'); break

if __name__ == '__main__':
    main()
