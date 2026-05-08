#!/usr/bin/env python3
"""vormap_architect - Autonomous Spatial Layout Architect

Generates optimal point placements satisfying competing spatial constraints
using simulated annealing. Auto-detects infeasible constraints, negotiates
trade-offs, and provides proactive improvement recommendations.

Usage:
    python vormap_architect.py --num-points 50 --min-spacing 20 --coverage-target 0.85
    python vormap_architect.py --density-map center-heavy --forbidden-zones '[{"x":400,"y":300,"r":80}]'
    python vormap_architect.py --num-points 80 --width 1000 --height 800 --output layout.html
"""

import argparse
import json
import math
import random
import sys
import os
from collections import OrderedDict

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def distance(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def min_pairwise_distance(points):
    if len(points) < 2:
        return float("inf")
    md = float("inf")
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            d = distance(points[i], points[j])
            if d < md:
                md = d
    return md


def coverage_fraction(points, width, height, radius=None):
    """Estimate fraction of area within *radius* of any point via grid sampling."""
    if radius is None:
        radius = math.sqrt(width * height / max(len(points), 1)) * 0.8
    step = max(width, height) / 80
    covered = 0
    total = 0
    y = step / 2
    while y < height:
        x = step / 2
        while x < width:
            total += 1
            for p in points:
                if distance((x, y), p) <= radius:
                    covered += 1
                    break
            x += step
        y += step
    return covered / max(total, 1), radius


def in_forbidden(point, zones):
    for z in zones:
        if distance(point, (z["x"], z["y"])) < z["r"]:
            return True
    return False


def forbidden_violations(points, zones):
    return sum(1 for p in points if in_forbidden(p, zones))


def density_score(points, width, height, mode):
    """Score how well point density matches the requested distribution."""
    if not points or mode == "uniform":
        # For uniform, measure coefficient of variation of NN distances
        if len(points) < 3:
            return 1.0
        dists = []
        for i, p in enumerate(points):
            nn = min(distance(p, q) for j, q in enumerate(points) if j != i)
            dists.append(nn)
        mean_d = sum(dists) / len(dists)
        if mean_d == 0:
            return 0.0
        std_d = math.sqrt(sum((d - mean_d) ** 2 for d in dists) / len(dists))
        cv = std_d / mean_d
        return max(0.0, 1.0 - cv)

    cx, cy = width / 2, height / 2
    max_r = math.sqrt(cx ** 2 + cy ** 2)
    rel_dists = [math.sqrt((p[0] - cx) ** 2 + (p[1] - cy) ** 2) / max_r for p in points]

    if mode == "center-heavy":
        # More points should be near center → lower average relative distance is better
        avg = sum(rel_dists) / len(rel_dists)
        return max(0.0, 1.0 - avg * 2)
    elif mode == "edge-heavy":
        avg = sum(rel_dists) / len(rel_dists)
        return max(0.0, avg * 2 - 0.5)
    elif mode == "clustered":
        # Favour lower average NN distance (tighter clusters)
        dists = []
        for i, p in enumerate(points):
            nn = min(distance(p, q) for j, q in enumerate(points) if j != i)
            dists.append(nn)
        mean_d = sum(dists) / len(dists)
        expected = math.sqrt(width * height / len(points)) * 0.5
        return max(0.0, 1.0 - mean_d / max(expected * 3, 1))
    return 1.0


# ---------------------------------------------------------------------------
# Feasibility check
# ---------------------------------------------------------------------------

def check_feasibility(num_points, width, height, min_spacing, zones):
    """Return (feasible: bool, messages: list[str])."""
    msgs = []
    # Rough packing limit (hex packing)
    area = width * height
    for z in zones:
        area -= math.pi * z["r"] ** 2
    area = max(area, 1)
    pack_limit = int(area / (min_spacing ** 2 * math.sqrt(3) / 2))
    if num_points > pack_limit:
        msgs.append(
            f"Infeasible: {num_points} points with min-spacing {min_spacing} "
            f"in {width}x{height} (max ~{pack_limit}). "
            f"Suggestion: reduce to {pack_limit} points or lower min-spacing to "
            f"{max(1, int(math.sqrt(area / num_points * math.sqrt(3) / 2)))}"
        )
    # Check if forbidden zones consume most area
    fz_area = sum(math.pi * z["r"] ** 2 for z in zones)
    if fz_area > 0.8 * width * height:
        msgs.append(
            f"Forbidden zones consume {fz_area / (width * height) * 100:.0f}% of area — "
            "very little space for points."
        )
    return len(msgs) == 0, msgs


# ---------------------------------------------------------------------------
# Initial placement
# ---------------------------------------------------------------------------

def initial_placement(num, w, h, mode, zones, rng):
    points = []
    attempts = 0
    while len(points) < num and attempts < num * 200:
        attempts += 1
        if mode == "center-heavy":
            x = rng.gauss(w / 2, w / 5)
            y = rng.gauss(h / 2, h / 5)
        elif mode == "edge-heavy":
            angle = rng.uniform(0, 2 * math.pi)
            r = max(rng.gauss(1.0, 0.15), 0.4)
            x = w / 2 + math.cos(angle) * r * w / 2
            y = h / 2 + math.sin(angle) * r * h / 2
        elif mode == "clustered":
            # 3-5 cluster centres
            if not hasattr(initial_placement, '_centres') or len(initial_placement._centres) == 0:
                nc = rng.randint(3, 5)
                initial_placement._centres = [(rng.uniform(w * 0.2, w * 0.8),
                                               rng.uniform(h * 0.2, h * 0.8)) for _ in range(nc)]
            c = rng.choice(initial_placement._centres)
            x = rng.gauss(c[0], w / 10)
            y = rng.gauss(c[1], h / 10)
        else:
            x = rng.uniform(0, w)
            y = rng.uniform(0, h)
        x = max(0, min(w, x))
        y = max(0, min(h, y))
        if not in_forbidden((x, y), zones):
            points.append((x, y))
    # Reset cached centres
    if hasattr(initial_placement, '_centres'):
        del initial_placement._centres
    return points


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_score(points, width, height, min_spacing, coverage_target, zones, density_mode):
    """Return (total_score, details_dict).  Higher = better, max ~100."""
    details = {}

    # 1. Min spacing
    md = min_pairwise_distance(points)
    spacing_ok = md >= min_spacing
    spacing_score = min(1.0, md / max(min_spacing, 1))
    details["min_spacing"] = {"value": md, "target": min_spacing,
                              "ok": spacing_ok, "score": spacing_score}

    # 2. Coverage
    cov, cov_r = coverage_fraction(points, width, height)
    cov_ok = cov >= coverage_target
    cov_score = min(1.0, cov / max(coverage_target, 0.01))
    details["coverage"] = {"value": cov, "target": coverage_target,
                           "radius": cov_r, "ok": cov_ok, "score": cov_score}

    # 3. Density
    ds = density_score(points, width, height, density_mode)
    details["density"] = {"value": ds, "mode": density_mode,
                          "ok": ds >= 0.6, "score": ds}

    # 4. Forbidden zones
    fv = forbidden_violations(points, zones)
    fz_score = 1.0 if fv == 0 else max(0.0, 1.0 - fv / max(len(points), 1))
    details["forbidden"] = {"violations": fv, "ok": fv == 0, "score": fz_score}

    # Weighted total
    total = (spacing_score * 30 + cov_score * 30 + ds * 20 + fz_score * 20)
    return total, details


# ---------------------------------------------------------------------------
# Simulated annealing optimiser
# ---------------------------------------------------------------------------

def optimise(points, width, height, min_spacing, coverage_target, zones,
             density_mode, iterations, rng):
    history = []
    best_score, best_details = compute_score(
        points, width, height, min_spacing, coverage_target, zones, density_mode)
    best_points = list(points)
    t0 = 1.0
    for it in range(iterations):
        t = t0 * (1 - it / iterations)
        idx = rng.randint(0, len(points) - 1)
        old = points[idx]
        move_r = max(5, (width + height) / 4 * t)
        nx = old[0] + rng.gauss(0, move_r)
        ny = old[1] + rng.gauss(0, move_r)
        nx = max(0, min(width, nx))
        ny = max(0, min(height, ny))
        points[idx] = (nx, ny)
        score, details = compute_score(
            points, width, height, min_spacing, coverage_target, zones, density_mode)
        delta = score - best_score
        if delta > 0 or (t > 0 and rng.random() < math.exp(delta / max(t * 30, 0.01))):
            if score > best_score:
                best_score = score
                best_details = details
                best_points = list(points)
        else:
            points[idx] = old
        if it % max(1, iterations // 200) == 0:
            history.append({"iter": it, "score": best_score})
    history.append({"iter": iterations, "score": best_score})
    return best_points, best_score, best_details, history


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------

def generate_recommendations(points, width, height, min_spacing, coverage_target,
                             zones, density_mode, details):
    recs = []
    # Coverage recommendation
    cov = details["coverage"]["value"]
    if cov < coverage_target:
        extra = max(1, int((coverage_target - cov) / 0.02))
        recs.append(f"Add ~{extra} more points to improve coverage from "
                    f"{cov:.2f} toward {coverage_target:.2f}")
    elif cov > coverage_target + 0.1:
        removable = max(1, int((cov - coverage_target) / 0.03))
        recs.append(f"Could remove ~{removable} points and still meet coverage target")

    # Spacing recommendation
    md = details["min_spacing"]["value"]
    if md < min_spacing:
        recs.append(f"Min spacing {md:.1f} is below target {min_spacing}. "
                    f"Consider reducing to --min-spacing {int(md)} or removing points")

    # Density
    ds = details["density"]["score"]
    if ds < 0.6:
        recs.append(f"Density match is low ({ds:.2f}). Try more iterations "
                    f"or adjust --density-map")

    # Crowding: find tightest pair
    if len(points) >= 2:
        tight_i, tight_j, tight_d = 0, 1, float("inf")
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                d = distance(points[i], points[j])
                if d < tight_d:
                    tight_i, tight_j, tight_d = i, j, d
        if tight_d < min_spacing * 1.2:
            recs.append(f"Points #{tight_i} and #{tight_j} are tightly packed "
                        f"({tight_d:.1f}); relocating one would improve spacing")

    if not recs:
        recs.append("Layout looks optimal — no improvements suggested")
    return recs


# ---------------------------------------------------------------------------
# Voronoi cells (simple Fortune's / half-plane approach for HTML)
# ---------------------------------------------------------------------------

def voronoi_edges_js(points, width, height):
    """Generate JavaScript array of Voronoi edge data for Canvas rendering.
    Uses pixel-grid nearest-point ownership (simple but effective for viz)."""
    # For the HTML report we let the browser compute Voronoi via Canvas pixel test
    # so we just pass points — the JS side handles it.
    return ""


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def generate_html(points, width, height, details, history, recs, zones,
                  density_mode, iterations, seed, score):
    zones_js = json.dumps(zones)
    points_js = json.dumps([(round(p[0], 2), round(p[1], 2)) for p in points])
    history_js = json.dumps(history)
    recs_html = "".join(f"<li>{r}</li>" for r in recs)

    def gauge(label, val, target_val=None, ok=True):
        pct = min(100, max(0, val * 100))
        color = "#4caf50" if ok else ("#ff9800" if val > 0.5 else "#f44336")
        tgt = f" / target {target_val}" if target_val is not None else ""
        return f"""<div class="gauge">
          <svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="52" fill="none"
          stroke="#333" stroke-width="8"/>
          <circle cx="60" cy="60" r="52" fill="none" stroke="{color}"
          stroke-width="8" stroke-dasharray="{pct * 3.267} 326.7"
          stroke-linecap="round" transform="rotate(-90 60 60)"/>
          <text x="60" y="58" text-anchor="middle" fill="#eee"
          font-size="18" font-weight="bold">{pct:.0f}%</text>
          <text x="60" y="78" text-anchor="middle" fill="#aaa"
          font-size="10">{label}{tgt}</text></svg></div>"""

    gauges = "".join([
        gauge("Spacing", details["min_spacing"]["score"],
              f"≥{details['min_spacing']['target']}", details["min_spacing"]["ok"]),
        gauge("Coverage", details["coverage"]["score"],
              f"≥{details['coverage']['target']}", details["coverage"]["ok"]),
        gauge("Density", details["density"]["score"], details["density"]["mode"],
              details["density"]["ok"]),
        gauge("Zones", details["forbidden"]["score"], None, details["forbidden"]["ok"]),
    ])

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Spatial Layout Architect Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#1a1a2e;color:#eee;font-family:'Segoe UI',system-ui,sans-serif;padding:20px}}
h1{{color:#e94560;margin-bottom:4px}}
.sub{{color:#888;margin-bottom:20px;font-size:14px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:#16213e;border-radius:12px;padding:20px}}
.card h2{{color:#e94560;font-size:16px;margin-bottom:12px}}
canvas{{background:#0f3460;border-radius:8px;width:100%;cursor:crosshair}}
.gauges{{display:flex;gap:12px;flex-wrap:wrap;justify-content:center}}
.gauge svg{{width:110px;height:110px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:6px 10px;border-bottom:1px solid #333}}
th{{color:#e94560}}
ul{{padding-left:18px}}li{{margin-bottom:6px;font-size:14px}}
.health{{font-size:48px;font-weight:bold;text-align:center;padding:20px;
  background:linear-gradient(135deg,#e94560,#0f3460);border-radius:12px;margin-bottom:20px}}
</style></head><body>
<h1>🏗️ Spatial Layout Architect</h1>
<p class="sub">Points: {len(points)} | Iterations: {iterations} |
Seed: {seed} | Density: {density_mode}</p>

<div class="health">{score:.0f}/100</div>

<div class="grid">
<div class="card"><h2>Layout Visualization</h2>
<canvas id="cv" width="{width}" height="{height}"></canvas></div>
<div class="card"><h2>Constraint Satisfaction</h2>
<div class="gauges">{gauges}</div>
<table style="margin-top:16px">
<tr><th>Metric</th><th>Value</th><th>Target</th><th>Status</th></tr>
<tr><td>Min Spacing</td><td>{details['min_spacing']['value']:.1f}</td>
<td>≥{details['min_spacing']['target']}</td>
<td>{'✅' if details['min_spacing']['ok'] else '⚠️'}</td></tr>
<tr><td>Coverage</td><td>{details['coverage']['value']:.2f}</td>
<td>≥{details['coverage']['target']}</td>
<td>{'✅' if details['coverage']['ok'] else '⚠️'}</td></tr>
<tr><td>Density Match</td><td>{details['density']['score']:.2f}</td>
<td>{details['density']['mode']}</td>
<td>{'✅' if details['density']['ok'] else '⚠️'}</td></tr>
<tr><td>Zone Violations</td><td>{details['forbidden']['violations']}</td>
<td>0</td>
<td>{'✅' if details['forbidden']['ok'] else '❌'}</td></tr>
</table></div>
</div>

<div class="grid">
<div class="card"><h2>Convergence</h2>
<canvas id="chart" width="700" height="300"></canvas></div>
<div class="card"><h2>Recommendations</h2><ul>{recs_html}</ul></div>
</div>

<script>
const pts={points_js};
const zones={zones_js};
const hist={history_js};
const W={width},H={height};

// Draw layout
(function(){{
const cv=document.getElementById('cv');
const ctx=cv.getContext('2d');
const sx=cv.clientWidth/W,sy=cv.clientHeight/H;
cv.width=cv.clientWidth;cv.height=cv.clientHeight;
ctx.scale(sx,sy);

// Forbidden zones
ctx.fillStyle='rgba(244,67,54,0.2)';ctx.strokeStyle='#f44336';ctx.lineWidth=2;
zones.forEach(z=>{{ctx.beginPath();ctx.arc(z.x,z.y,z.r,0,Math.PI*2);ctx.fill();ctx.stroke();}});

// Voronoi cells (pixel ownership)
if(pts.length>0){{
const img=ctx.createImageData(Math.ceil(W),Math.ceil(H));
const colors=pts.map((_,i)=>[((i*67+80)%200)+55,((i*131+40)%200)+55,((i*199+100)%200)+55]);
for(let py=0;py<Math.ceil(H);py++)for(let px=0;px<Math.ceil(W);px++){{
let mi=0,md=1e9;
for(let i=0;i<pts.length;i++){{const dx=px-pts[i][0],dy=py-pts[i][1],d=dx*dx+dy*dy;if(d<md){{md=d;mi=i;}}}}
const off=(py*Math.ceil(W)+px)*4;
img.data[off]=colors[mi][0];img.data[off+1]=colors[mi][1];
img.data[off+2]=colors[mi][2];img.data[off+3]=40;
}}
ctx.putImageData(img,0,0);
}}

// Points
ctx.fillStyle='#e94560';
pts.forEach(p=>{{ctx.beginPath();ctx.arc(p[0],p[1],4,0,Math.PI*2);ctx.fill();}});
}})();

// Convergence chart
(function(){{
const cv=document.getElementById('chart');
const ctx=cv.getContext('2d');
cv.width=cv.clientWidth;cv.height=cv.clientHeight;
const pad=40,cw=cv.width-pad*2,ch=cv.height-pad*2;
if(hist.length<2)return;
const maxS=Math.max(...hist.map(h=>h.score)),minS=Math.min(...hist.map(h=>h.score));
const maxI=hist[hist.length-1].iter;
ctx.strokeStyle='#333';ctx.lineWidth=1;
for(let i=0;i<=4;i++){{const y=pad+ch*(1-i/4);
ctx.beginPath();ctx.moveTo(pad,y);ctx.lineTo(pad+cw,y);ctx.stroke();
ctx.fillStyle='#888';ctx.font='11px sans-serif';
ctx.fillText((minS+(maxS-minS)*i/4).toFixed(1),2,y+4);}}
ctx.strokeStyle='#e94560';ctx.lineWidth=2;ctx.beginPath();
hist.forEach((h,i)=>{{const x=pad+cw*h.iter/maxI,y=pad+ch*(1-(h.score-minS)/Math.max(maxS-minS,1));
i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);}});ctx.stroke();
ctx.fillStyle='#888';ctx.font='12px sans-serif';
ctx.fillText('Iterations →',pad+cw/2-30,cv.height-4);
}})();
</script></body></html>"""
    return html


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_summary(points, score, details, recs, output_file):
    print("\n=== Spatial Layout Architect Report ===")
    print(f"Points: {len(points)} | Health Score: {score:.0f}/100\n")
    print("Constraints:")

    ms = details["min_spacing"]
    tag = "✓" if ms["ok"] else ("~" if ms["score"] > 0.7 else "✗")
    print(f"  [{tag}] Min Spacing: {ms['value']:.1f} (target: ≥{ms['target']})")

    cv = details["coverage"]
    tag = "✓" if cv["ok"] else ("~" if cv["score"] > 0.7 else "✗")
    print(f"  [{tag}] Coverage: {cv['value']:.2f} (target: ≥{cv['target']})")

    ds = details["density"]
    tag = "✓" if ds["ok"] else "~"
    print(f"  [{tag}] Density Match: {ds['score']:.2f} ({ds['mode']})")

    fz = details["forbidden"]
    tag = "✓" if fz["ok"] else "✗"
    print(f"  [{tag}] Forbidden Zones: {fz['violations']} violations")

    print("\nRecommendations:")
    for r in recs:
        print(f"  → {r}")
    print(f"\nReport: {output_file}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Spatial Layout Architect — optimal point placement "
                    "with constraint satisfaction, trade-off negotiation, and "
                    "proactive recommendations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--num-points", type=int, default=50,
                        help="Number of points to place (default: 50)")
    parser.add_argument("--width", type=float, default=800,
                        help="Bounding box width (default: 800)")
    parser.add_argument("--height", type=float, default=600,
                        help="Bounding box height (default: 600)")
    parser.add_argument("--min-spacing", type=float, default=20,
                        help="Minimum distance between points (default: 20)")
    parser.add_argument("--coverage-target", type=float, default=0.85,
                        help="Target area coverage fraction 0-1 (default: 0.85)")
    parser.add_argument("--forbidden-zones", type=str, default="[]",
                        help='JSON array of forbidden circles: [{"x":..,"y":..,"r":..}]')
    parser.add_argument("--density-map", choices=["uniform", "center-heavy",
                        "edge-heavy", "clustered"], default="uniform",
                        help="Desired density distribution (default: uniform)")
    parser.add_argument("--iterations", type=int, default=500,
                        help="Max optimisation iterations (default: 500)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--output", type=str, default="architect_report.html",
                        help="Output HTML report path (default: architect_report.html)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randint(0, 999999)
    rng = random.Random(seed)

    try:
        zones = json.loads(args.forbidden_zones)
    except json.JSONDecodeError:
        print("Error: --forbidden-zones must be valid JSON", file=sys.stderr)
        sys.exit(1)

    # Feasibility check
    feasible, msgs = check_feasibility(
        args.num_points, args.width, args.height, args.min_spacing, zones)
    if not feasible:
        print("⚠️  Constraint Negotiator — detected issues:")
        for m in msgs:
            print(f"  • {m}")
        print("  Proceeding with best-effort optimisation...\n")

    # Place & optimise
    points = initial_placement(
        args.num_points, args.width, args.height, args.density_map, zones, rng)
    if len(points) < args.num_points:
        print(f"Warning: could only place {len(points)}/{args.num_points} points "
              f"(forbidden zones may be too large)")

    points, score, details, history = optimise(
        points, args.width, args.height, args.min_spacing, args.coverage_target,
        zones, args.density_map, args.iterations, rng)

    recs = generate_recommendations(
        points, args.width, args.height, args.min_spacing, args.coverage_target,
        zones, args.density_map, details)

    # Output
    print_summary(points, score, details, recs, args.output)

    html = generate_html(points, args.width, args.height, details, history, recs,
                         zones, args.density_map, args.iterations, seed, score)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Report written to {args.output}")


if __name__ == "__main__":
    main()
