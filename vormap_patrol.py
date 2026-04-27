"""Autonomous Spatial Patrol Planner for Voronoi Territories.

Generates optimal patrol routes through Voronoi regions with
threat-weighted priorities, coverage analysis, and proactive
patrol recommendations.  Brings *agentic awareness* to spatial
coverage planning — the tool autonomously identifies high-risk
zones, plans efficient patrol circuits, and recommends adjustments.

Features:
    * **Threat Heatmap** — assign threat levels to regions based on
      distance-to-edge, isolation, density voids, or custom weights.
    * **Route Planning** — nearest-neighbor + 2-opt TSP improvement
      for efficient patrol circuits.
    * **Coverage Analysis** — measures what percentage of territory
      is within patrol visibility radius at each step.
    * **Priority Zones** — auto-detect high-priority areas (sparse,
      peripheral, large-cell regions) that need more frequent passes.
    * **Patrol Schedules** — split routes into timed shifts with
      estimated durations and break recommendations.
    * **Proactive Insights** — autonomous recommendations for patrol
      gaps, redundant overlaps, and rebalancing suggestions.

CLI examples::

    python vormap_patrol.py data.txt --output patrol.html
    python vormap_patrol.py data.txt --threats threats.json --output patrol.html
    python vormap_patrol.py data.txt --shifts 3 --visibility 50 --output patrol.html
    python vormap_patrol.py data.txt --auto --output patrol.html

Programmatic::

    from vormap_patrol import PatrolPlanner, export_html

    planner = PatrolPlanner(points, width=500, height=500)
    result = planner.plan(shifts=2, visibility=40)
    export_html(result, "patrol.html")
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from vormap_utils import bounding_box as _bounding_box, euclidean as _dist

# ── Geometry helpers ────────────────────────────────────────────────


def _centroid(pts: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


# _bounding_box is now imported from vormap_utils (single-pass, memory-efficient)


# ── Voronoi cell computation (Fortune's algorithm not needed — use
#    brute-force assignment for moderate point counts) ───────────────


def _assign_cells(
    pts: List[Tuple[float, float]], w: float, h: float, res: int = 100
) -> Dict[int, List[Tuple[float, float]]]:
    """Assign grid sample points to nearest generator → approximate cells."""
    cells: Dict[int, List[Tuple[float, float]]] = {i: [] for i in range(len(pts))}
    dx = w / res
    dy = h / res
    for gx in range(res):
        for gy in range(res):
            sx = (gx + 0.5) * dx
            sy = (gy + 0.5) * dy
            best_i = 0
            best_d = float("inf")
            for i, p in enumerate(pts):
                d = (p[0] - sx) ** 2 + (p[1] - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            cells[best_i].append((sx, sy))
    return cells


def _cell_area(cell_pts: List[Tuple[float, float]], dx: float, dy: float) -> float:
    return len(cell_pts) * dx * dy


# ── Threat assessment ──────────────────────────────────────────────


def _compute_threats(
    pts: List[Tuple[float, float]],
    cells: Dict[int, List[Tuple[float, float]]],
    w: float,
    h: float,
    res: int,
    custom_threats: Optional[Dict[int, float]] = None,
) -> List[float]:
    """Compute threat score (0-1) for each point/cell.

    Factors:
      - Isolation (avg distance to k nearest neighbors)
      - Peripherality (distance to boundary)
      - Cell size (larger = harder to patrol)
    """
    n = len(pts)
    if n == 0:
        return []

    # Isolation: avg dist to 3 nearest neighbors
    k = min(3, n - 1)
    isolation = []
    for i, p in enumerate(pts):
        dists = sorted(_dist(p, pts[j]) for j in range(n) if j != i)
        isolation.append(sum(dists[:k]) / max(k, 1))

    # Peripherality: distance to nearest boundary edge
    peripherality = []
    for p in pts:
        edge_dist = min(p[0], p[1], w - p[0], h - p[1])
        peripherality.append(1.0 / (edge_dist + 1.0))

    # Cell size
    dx = w / res
    dy = h / res
    areas = [_cell_area(cells[i], dx, dy) for i in range(n)]

    # Normalize each factor to 0-1
    def _norm(vals: List[float]) -> List[float]:
        mn, mx = min(vals), max(vals)
        rng = mx - mn
        if rng < 1e-12:
            return [0.5] * len(vals)
        return [(v - mn) / rng for v in vals]

    iso_n = _norm(isolation)
    per_n = _norm(peripherality)
    area_n = _norm(areas)

    threats = []
    for i in range(n):
        base = 0.4 * iso_n[i] + 0.3 * per_n[i] + 0.3 * area_n[i]
        if custom_threats and i in custom_threats:
            base = 0.5 * base + 0.5 * custom_threats[i]
        threats.append(min(1.0, max(0.0, base)))

    return threats


# ── Route planning (TSP with 2-opt) ───────────────────────────────


def _nearest_neighbor_route(pts: List[Tuple[float, float]]) -> List[int]:
    """Greedy nearest-neighbor TSP starting from point 0."""
    n = len(pts)
    if n <= 1:
        return list(range(n))
    visited = [False] * n
    route = [0]
    visited[0] = True
    for _ in range(n - 1):
        curr = route[-1]
        best_j, best_d = -1, float("inf")
        for j in range(n):
            if not visited[j]:
                d = _dist(pts[curr], pts[j])
                if d < best_d:
                    best_d = d
                    best_j = j
        route.append(best_j)
        visited[best_j] = True
    return route


def _route_length(pts: List[Tuple[float, float]], route: List[int]) -> float:
    total = 0.0
    for i in range(len(route)):
        total += _dist(pts[route[i]], pts[route[(i + 1) % len(route)]])
    return total


def _two_opt(pts: List[Tuple[float, float]], route: List[int], max_iter: int = 1000) -> List[int]:
    """Improve route with 2-opt swaps."""
    n = len(route)
    if n < 4:
        return route
    best = route[:]
    best_len = _route_length(pts, best)
    improved = True
    iterations = 0
    while improved and iterations < max_iter:
        improved = False
        iterations += 1
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                new_route = best[:i] + best[i : j + 1][::-1] + best[j + 1 :]
                new_len = _route_length(pts, new_route)
                if new_len < best_len - 1e-9:
                    best = new_route
                    best_len = new_len
                    improved = True
                    break
            if improved:
                break
    return best


def _priority_weighted_route(
    pts: List[Tuple[float, float]], threats: List[float]
) -> List[int]:
    """Build route visiting high-threat points first, then 2-opt optimize."""
    n = len(pts)
    if n <= 2:
        return list(range(n))
    # Sort by threat descending to seed route
    order = sorted(range(n), key=lambda i: -threats[i])
    # Use nearest-neighbor from highest threat point
    visited = [False] * n
    route = [order[0]]
    visited[order[0]] = True
    for _ in range(n - 1):
        curr = route[-1]
        best_j, best_score = -1, float("inf")
        for j in range(n):
            if not visited[j]:
                d = _dist(pts[curr], pts[j])
                # Bias toward high-threat (lower cost for high threat)
                score = d * (1.0 - 0.5 * threats[j])
                if score < best_score:
                    best_score = score
                    best_j = j
        route.append(best_j)
        visited[best_j] = True
    return _two_opt(pts, route)


# ── Coverage analysis ──────────────────────────────────────────────


def _coverage_analysis(
    pts: List[Tuple[float, float]],
    route: List[int],
    w: float,
    h: float,
    visibility: float,
    grid_res: int = 50,
) -> Dict[str, Any]:
    """Compute what fraction of the area is covered by patrol route."""
    dx = w / grid_res
    dy = h / grid_res
    vis_sq = visibility * visibility
    covered = set()
    total = grid_res * grid_res

    for idx in route:
        px, py = pts[idx]
        # Mark grid cells within visibility
        gx_min = max(0, int((px - visibility) / dx))
        gx_max = min(grid_res - 1, int((px + visibility) / dx))
        gy_min = max(0, int((py - visibility) / dy))
        gy_max = min(grid_res - 1, int((py + visibility) / dy))
        for gx in range(gx_min, gx_max + 1):
            for gy in range(gy_min, gy_max + 1):
                cx = (gx + 0.5) * dx
                cy = (gy + 0.5) * dy
                if (px - cx) ** 2 + (py - cy) ** 2 <= vis_sq:
                    covered.add((gx, gy))

    pct = len(covered) / total * 100 if total > 0 else 0
    # Find uncovered zones (cluster uncovered cells)
    uncovered_cells = []
    for gx in range(grid_res):
        for gy in range(grid_res):
            if (gx, gy) not in covered:
                uncovered_cells.append(((gx + 0.5) * dx, (gy + 0.5) * dy))

    return {
        "coverage_pct": round(pct, 1),
        "covered_cells": len(covered),
        "total_cells": total,
        "uncovered_zones": uncovered_cells[:20],  # sample
    }


# ── Shift scheduling ──────────────────────────────────────────────


def _split_shifts(
    route: List[int], n_shifts: int, pts: List[Tuple[float, float]]
) -> List[Dict[str, Any]]:
    """Split route into roughly equal-distance shifts."""
    if n_shifts <= 1:
        length = _route_length(pts, route)
        return [{"shift": 1, "waypoints": route, "distance": round(length, 1)}]

    total_len = _route_length(pts, route)
    target = total_len / n_shifts
    shifts = []
    current_shift: List[int] = [route[0]]
    current_dist = 0.0
    shift_num = 1

    for i in range(len(route) - 1):
        seg = _dist(pts[route[i]], pts[route[i + 1]])
        current_dist += seg
        current_shift.append(route[i + 1])
        if current_dist >= target and shift_num < n_shifts:
            shifts.append({
                "shift": shift_num,
                "waypoints": current_shift[:],
                "distance": round(current_dist, 1),
            })
            shift_num += 1
            current_shift = [route[i + 1]]
            current_dist = 0.0

    if current_shift:
        shifts.append({
            "shift": shift_num,
            "waypoints": current_shift,
            "distance": round(current_dist, 1),
        })

    return shifts


# ── Proactive recommendations ─────────────────────────────────────


def _generate_recommendations(
    pts: List[Tuple[float, float]],
    threats: List[float],
    coverage: Dict[str, Any],
    route: List[int],
    shifts: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    recs: List[Dict[str, str]] = []

    # Coverage gaps
    if coverage["coverage_pct"] < 70:
        recs.append({
            "severity": "critical",
            "title": "Low Coverage",
            "detail": f"Only {coverage['coverage_pct']}% area covered. "
                      "Consider increasing visibility radius or adding patrol waypoints.",
        })
    elif coverage["coverage_pct"] < 90:
        recs.append({
            "severity": "warning",
            "title": "Moderate Coverage Gap",
            "detail": f"{coverage['coverage_pct']}% covered. "
                      "Consider supplementary sweep of uncovered zones.",
        })

    # High-threat areas visited late
    n = len(route)
    high_threats = [i for i in range(len(threats)) if threats[i] > 0.7]
    if high_threats:
        positions = {route[j]: j for j in range(n)}
        late_visits = [
            i for i in high_threats if positions.get(i, 0) > n * 0.7
        ]
        if late_visits:
            recs.append({
                "severity": "warning",
                "title": "High-Threat Zones Visited Late",
                "detail": f"{len(late_visits)} high-threat zone(s) are in the last 30% of route. "
                          "Consider reordering to prioritize these areas.",
            })

    # Shift imbalance
    if len(shifts) > 1:
        dists = [s["distance"] for s in shifts]
        if max(dists) > 0:
            imbalance = (max(dists) - min(dists)) / max(dists)
            if imbalance > 0.4:
                recs.append({
                    "severity": "info",
                    "title": "Shift Distance Imbalance",
                    "detail": f"Shift distances vary by {imbalance * 100:.0f}%. "
                              "Consider rebalancing shift boundaries.",
                })

    # Isolation clusters
    if len(pts) > 5:
        cx, cy = _centroid(pts)
        bb = _bounding_box(pts)
        far_pts = [i for i, p in enumerate(pts) if _dist(p, (cx, cy)) > max(
            bb[2] - bb[0],
            bb[3] - bb[1]
        ) * 0.4]
        if far_pts:
            recs.append({
                "severity": "info",
                "title": "Outlier Waypoints Detected",
                "detail": f"{len(far_pts)} point(s) far from center increase route length. "
                          "Consider separate patrol for outlier zone.",
            })

    if not recs:
        recs.append({
            "severity": "info",
            "title": "Patrol Looks Good",
            "detail": "No significant issues detected. Route coverage and priority ordering are healthy.",
        })

    return recs


# ── Main planner ───────────────────────────────────────────────────


class PatrolResult:
    """Container for patrol planning results."""

    def __init__(
        self,
        pts: List[Tuple[float, float]],
        w: float,
        h: float,
        route: List[int],
        threats: List[float],
        coverage: Dict[str, Any],
        shifts: List[Dict[str, Any]],
        recommendations: List[Dict[str, str]],
        route_length: float,
        visibility: float,
    ):
        self.pts = pts
        self.w = w
        self.h = h
        self.route = route
        self.threats = threats
        self.coverage = coverage
        self.shifts = shifts
        self.recommendations = recommendations
        self.route_length = route_length
        self.visibility = visibility

    def to_json(self, path: str) -> None:
        data = {
            "points": [{"x": p[0], "y": p[1]} for p in self.pts],
            "route": self.route,
            "route_length": self.route_length,
            "threats": [round(t, 3) for t in self.threats],
            "coverage": self.coverage,
            "shifts": self.shifts,
            "recommendations": self.recommendations,
            "canvas": {"width": self.w, "height": self.h},
            "visibility": self.visibility,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  ✓ JSON report → {path}")


class PatrolPlanner:
    """Autonomous spatial patrol route planner."""

    def __init__(
        self,
        pts: List[Tuple[float, float]],
        width: float = 500.0,
        height: float = 500.0,
        custom_threats: Optional[Dict[int, float]] = None,
    ):
        self.pts = pts
        self.w = width
        self.h = height
        self.custom_threats = custom_threats

    def plan(
        self,
        shifts: int = 1,
        visibility: float = 40.0,
        auto: bool = False,
    ) -> PatrolResult:
        n = len(self.pts)
        print(f"  Planning patrol for {n} waypoints on {self.w}×{self.h} canvas")

        # Cell assignment
        res = min(100, max(30, int(max(self.w, self.h) / 5)))
        cells = _assign_cells(self.pts, self.w, self.h, res)

        # Threat assessment
        threats = _compute_threats(
            self.pts, cells, self.w, self.h, res, self.custom_threats
        )
        high_count = sum(1 for t in threats if t > 0.7)
        print(f"  Threat assessment: {high_count} high-threat zones detected")

        # Auto visibility
        if auto and n > 1:
            # Set visibility to cover ~85% with current points
            avg_spacing = math.sqrt(self.w * self.h / max(n, 1))
            visibility = avg_spacing * 0.8
            print(f"  Auto visibility: {visibility:.1f}")

        # Route planning
        route = _priority_weighted_route(self.pts, threats)
        rl = _route_length(self.pts, route)
        print(f"  Route optimized: {rl:.1f} total distance ({n} stops)")

        # Coverage
        cov = _coverage_analysis(self.pts, route, self.w, self.h, visibility)
        print(f"  Coverage: {cov['coverage_pct']}%")

        # Shifts
        shift_list = _split_shifts(route, shifts, self.pts)
        print(f"  Shifts: {len(shift_list)}")

        # Recommendations
        recs = _generate_recommendations(
            self.pts, threats, cov, route, shift_list
        )
        for r in recs:
            icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(
                r["severity"], "·"
            )
            print(f"  {icon} {r['title']}: {r['detail']}")

        return PatrolResult(
            pts=self.pts,
            w=self.w,
            h=self.h,
            route=route,
            threats=threats,
            coverage=cov,
            shifts=shift_list,
            recommendations=recs,
            route_length=round(rl, 1),
            visibility=visibility,
        )


# ── HTML export ────────────────────────────────────────────────────


def export_html(result: PatrolResult, path: str) -> None:
    pts_js = json.dumps([{"x": p[0], "y": p[1]} for p in result.pts])
    route_js = json.dumps(result.route)
    threats_js = json.dumps([round(t, 3) for t in result.threats])
    shifts_js = json.dumps(result.shifts)
    recs_js = json.dumps(result.recommendations)
    coverage_js = json.dumps(result.coverage)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>VoronoiMap Patrol Planner</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0e17;color:#c8d6e5;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1a1a2e,#16213e);padding:24px 32px;border-bottom:1px solid #2a3a5a}}
.header h1{{font-size:1.6em;color:#e2e8f0;display:flex;align-items:center;gap:10px}}
.header h1 span{{font-size:1.2em}}
.header .subtitle{{color:#64748b;margin-top:4px;font-size:.9em}}
.dashboard{{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:24px 32px}}
.card{{background:#111827;border:1px solid #1e293b;border-radius:12px;padding:20px}}
.card h2{{color:#e2e8f0;font-size:1.1em;margin-bottom:12px;display:flex;align-items:center;gap:8px}}
.stat-row{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px}}
.stat{{background:#1a2332;padding:10px 16px;border-radius:8px;text-align:center;flex:1;min-width:80px}}
.stat .val{{font-size:1.4em;font-weight:700;color:#60a5fa}}
.stat .lbl{{font-size:.75em;color:#64748b;margin-top:2px}}
.full-width{{grid-column:1/-1}}
canvas{{border-radius:8px;display:block;width:100%}}
.rec{{padding:10px 14px;border-radius:8px;margin-bottom:8px;border-left:4px solid}}
.rec.critical{{background:#1c1017;border-color:#ef4444}}
.rec.warning{{background:#1c1a0f;border-color:#f59e0b}}
.rec.info{{background:#0f1a2c;border-color:#3b82f6}}
.rec .rec-title{{font-weight:600;color:#e2e8f0;font-size:.9em}}
.rec .rec-detail{{color:#94a3b8;font-size:.82em;margin-top:2px}}
.shift-table{{width:100%;border-collapse:collapse;margin-top:8px}}
.shift-table th,.shift-table td{{padding:8px 12px;text-align:left;border-bottom:1px solid #1e293b;font-size:.85em}}
.shift-table th{{color:#64748b;font-weight:600}}
.legend{{display:flex;gap:16px;margin-top:12px;flex-wrap:wrap}}
.legend-item{{display:flex;align-items:center;gap:6px;font-size:.8em;color:#94a3b8}}
.legend-dot{{width:12px;height:12px;border-radius:50%}}
.controls{{display:flex;gap:12px;margin-top:12px;align-items:center;flex-wrap:wrap}}
.btn{{background:#1e40af;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:.85em}}
.btn:hover{{background:#2563eb}}
.btn.active{{background:#059669}}
input[type=range]{{flex:1;max-width:200px}}
</style>
</head>
<body>
<div class="header">
  <h1><span>🛡️</span> Autonomous Patrol Planner</h1>
  <div class="subtitle">VoronoiMap — threat-weighted route optimization with coverage analysis</div>
</div>
<div class="dashboard">
  <div class="card full-width">
    <h2>📍 Patrol Map</h2>
    <canvas id="map" height="500"></canvas>
    <div class="controls">
      <button class="btn" id="btnAnimate">▶ Animate Patrol</button>
      <button class="btn" id="btnThreats">🔥 Toggle Threats</button>
      <button class="btn" id="btnCoverage">📡 Toggle Coverage</button>
      <label style="color:#64748b;font-size:.82em">Speed</label>
      <input type="range" id="speed" min="1" max="20" value="8">
    </div>
    <div class="legend">
      <div class="legend-item"><div class="legend-dot" style="background:#60a5fa"></div> Low Threat</div>
      <div class="legend-item"><div class="legend-dot" style="background:#f59e0b"></div> Medium Threat</div>
      <div class="legend-item"><div class="legend-dot" style="background:#ef4444"></div> High Threat</div>
      <div class="legend-item"><div class="legend-dot" style="background:rgba(96,165,250,0.15)"></div> Visibility Zone</div>
    </div>
  </div>

  <div class="card">
    <h2>📊 Statistics</h2>
    <div class="stat-row">
      <div class="stat"><div class="val">{len(result.pts)}</div><div class="lbl">Waypoints</div></div>
      <div class="stat"><div class="val">{result.route_length}</div><div class="lbl">Route Distance</div></div>
      <div class="stat"><div class="val">{result.coverage['coverage_pct']}%</div><div class="lbl">Coverage</div></div>
    </div>
    <div class="stat-row">
      <div class="stat"><div class="val">{result.visibility:.0f}</div><div class="lbl">Visibility</div></div>
      <div class="stat"><div class="val">{len(result.shifts)}</div><div class="lbl">Shifts</div></div>
      <div class="stat"><div class="val">{sum(1 for t in result.threats if t > 0.7)}</div><div class="lbl">High Threats</div></div>
    </div>
  </div>

  <div class="card">
    <h2>🧠 Recommendations</h2>
    <div id="recs"></div>
  </div>

  <div class="card">
    <h2>⏰ Shift Schedule</h2>
    <table class="shift-table">
      <tr><th>Shift</th><th>Stops</th><th>Distance</th><th>Est. Time</th></tr>
    </table>
    <div id="shifts"></div>
  </div>

  <div class="card">
    <h2>🔥 Threat Breakdown</h2>
    <canvas id="threatChart" height="200"></canvas>
  </div>
</div>

<script>
const pts={pts_js};
const route={route_js};
const threats={threats_js};
const shifts={shifts_js};
const recs={recs_js};
const coverage={coverage_js};
const W={result.w},H={result.h},VIS={result.visibility};

// Recs
const recsEl=document.getElementById('recs');
recs.forEach(r=>{{
  recsEl.innerHTML+=`<div class="rec ${{r.severity}}"><div class="rec-title">${{r.title}}</div><div class="rec-detail">${{r.detail}}</div></div>`;
}});

// Shifts table
const shiftTbl=document.querySelector('.shift-table');
shifts.forEach(s=>{{
  const speed=50;
  const mins=Math.round(s.distance/speed);
  const tr=document.createElement('tr');
  tr.innerHTML=`<td>Shift ${{s.shift}}</td><td>${{s.waypoints.length}}</td><td>${{s.distance}}</td><td>~${{mins}} min</td>`;
  shiftTbl.appendChild(tr);
}});

// Map
const canvas=document.getElementById('map');
const ctx=canvas.getContext('2d');
let showThreats=true,showCoverage=true;
let animIdx=-1,animating=false,animTimer=null;

function scaleX(x){{return x/W*canvas.width}}
function scaleY(y){{return y/H*canvas.height}}

function threatColor(t){{
  if(t>0.7)return'#ef4444';
  if(t>0.4)return'#f59e0b';
  return'#60a5fa';
}}

function draw(){{
  canvas.width=canvas.parentElement.clientWidth-40;
  canvas.height=Math.min(500,canvas.width*H/W);
  ctx.fillStyle='#0d1117';ctx.fillRect(0,0,canvas.width,canvas.height);

  // Grid coverage
  if(showCoverage&&animIdx>=0){{
    for(let i=0;i<=animIdx&&i<route.length;i++){{
      const p=pts[route[i]];
      const r=VIS/W*canvas.width;
      ctx.beginPath();ctx.arc(scaleX(p.x),scaleY(p.y),r,0,Math.PI*2);
      ctx.fillStyle='rgba(96,165,250,0.06)';ctx.fill();
    }}
  }}

  // Voronoi cells (approximate with threat colors)
  if(showThreats){{
    pts.forEach((p,i)=>{{
      const r=Math.sqrt(W*H/pts.length)/W*canvas.width*0.4;
      ctx.beginPath();ctx.arc(scaleX(p.x),scaleY(p.y),r,0,Math.PI*2);
      ctx.fillStyle=threatColor(threats[i])+'22';ctx.fill();
    }});
  }}

  // Route lines
  const maxI=animating&&animIdx>=0?Math.min(animIdx,route.length-1):route.length-1;
  ctx.strokeStyle='rgba(96,165,250,0.5)';ctx.lineWidth=1.5;ctx.setLineDash([4,4]);
  ctx.beginPath();
  for(let i=0;i<=maxI;i++){{
    const p=pts[route[i]];
    if(i===0)ctx.moveTo(scaleX(p.x),scaleY(p.y));
    else ctx.lineTo(scaleX(p.x),scaleY(p.y));
  }}
  ctx.stroke();ctx.setLineDash([]);

  // Points
  pts.forEach((p,i)=>{{
    ctx.beginPath();ctx.arc(scaleX(p.x),scaleY(p.y),5,0,Math.PI*2);
    ctx.fillStyle=threatColor(threats[i]);ctx.fill();
    ctx.strokeStyle='#0d1117';ctx.lineWidth=1.5;ctx.stroke();
  }});

  // Route numbers
  route.forEach((ri,ord)=>{{
    if(animating&&ord>animIdx)return;
    const p=pts[ri];
    ctx.fillStyle='#e2e8f0';ctx.font='bold 9px sans-serif';ctx.textAlign='center';
    ctx.fillText(ord+1,scaleX(p.x),scaleY(p.y)-9);
  }});

  // Active patrol indicator
  if(animIdx>=0&&animIdx<route.length){{
    const p=pts[route[animIdx]];
    ctx.beginPath();ctx.arc(scaleX(p.x),scaleY(p.y),10,0,Math.PI*2);
    ctx.strokeStyle='#22d3ee';ctx.lineWidth=2;ctx.stroke();
    ctx.beginPath();ctx.arc(scaleX(p.x),scaleY(p.y),VIS/W*canvas.width,0,Math.PI*2);
    ctx.strokeStyle='rgba(34,211,238,0.3)';ctx.lineWidth=1;ctx.stroke();
  }}
}}

draw();
window.addEventListener('resize',draw);

document.getElementById('btnAnimate').onclick=()=>{{
  if(animating){{
    clearInterval(animTimer);animating=false;animIdx=-1;
    document.getElementById('btnAnimate').textContent='▶ Animate Patrol';
    document.getElementById('btnAnimate').classList.remove('active');
    draw();return;
  }}
  animating=true;animIdx=0;
  document.getElementById('btnAnimate').textContent='⏹ Stop';
  document.getElementById('btnAnimate').classList.add('active');
  animTimer=setInterval(()=>{{
    animIdx++;
    if(animIdx>=route.length){{
      clearInterval(animTimer);animating=false;
      document.getElementById('btnAnimate').textContent='▶ Animate Patrol';
      document.getElementById('btnAnimate').classList.remove('active');
    }}
    draw();
  }},1000/(+document.getElementById('speed').value||8));
}};

document.getElementById('btnThreats').onclick=()=>{{showThreats=!showThreats;draw();}};
document.getElementById('btnCoverage').onclick=()=>{{showCoverage=!showCoverage;draw();}};

// Threat chart
const tc=document.getElementById('threatChart');
const tcx=tc.getContext('2d');
function drawThreat(){{
  tc.width=tc.parentElement.clientWidth-40;tc.height=200;
  const sorted=[...threats].sort((a,b)=>b-a);
  const bw=Math.max(2,tc.width/sorted.length-1);
  sorted.forEach((t,i)=>{{
    tcx.fillStyle=threatColor(t);
    const h=t*160;
    tcx.fillRect(i*(bw+1),180-h,bw,h);
  }});
  tcx.strokeStyle='#ffffff22';tcx.beginPath();
  tcx.moveTo(0,180-0.7*160);tcx.lineTo(tc.width,180-0.7*160);
  tcx.stroke();
  tcx.fillStyle='#ef444488';tcx.font='10px sans-serif';
  tcx.fillText('High threshold (0.7)',4,180-0.7*160-4);
}}
drawThreat();
</script>
</body></html>"""

    with open(path, "w") as f:
        f.write(html)
    print(f"  ✓ HTML report → {path}")


# ── File I/O ───────────────────────────────────────────────────────


def _load_points(path: str) -> Tuple[List[Tuple[float, float]], float, float]:
    """Load points from text file (x y per line or x,y per line)."""
    pts: List[Tuple[float, float]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    if not pts:
        raise ValueError(f"No points found in {path}")
    bb = _bounding_box(pts)
    w = bb[2] - bb[0]
    h = bb[3] - bb[1]
    # Add margin
    margin = max(w, h) * 0.1
    w += margin * 2
    h += margin * 2
    # Shift points so min is at margin
    pts = [(p[0] - bb[0] + margin, p[1] - bb[1] + margin) for p in pts]
    return pts, w, h


def _load_threats(path: str) -> Dict[int, float]:
    """Load custom threat weights from JSON {index: weight}."""
    with open(path) as f:
        data = json.load(f)
    return {int(k): float(v) for k, v in data.items()}


# ── CLI ────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Autonomous Spatial Patrol Planner for Voronoi Territories"
    )
    ap.add_argument("input", help="Point data file (x y per line)")
    ap.add_argument("--output", "-o", default="patrol_report.html", help="Output HTML path")
    ap.add_argument("--json", default=None, help="Also export JSON report")
    ap.add_argument("--threats", default=None, help="Custom threat weights JSON file")
    ap.add_argument("--shifts", type=int, default=1, help="Number of patrol shifts")
    ap.add_argument("--visibility", type=float, default=40.0, help="Patrol visibility radius")
    ap.add_argument("--auto", action="store_true", help="Auto-tune visibility radius")
    ap.add_argument("--width", type=float, default=None, help="Override canvas width")
    ap.add_argument("--height", type=float, default=None, help="Override canvas height")

    args = ap.parse_args()

    print("🛡️  VoronoiMap Patrol Planner")
    print("=" * 40)

    pts, w, h = _load_points(args.input)
    if args.width:
        w = args.width
    if args.height:
        h = args.height

    custom_threats = _load_threats(args.threats) if args.threats else None

    planner = PatrolPlanner(pts, w, h, custom_threats)
    result = planner.plan(
        shifts=args.shifts,
        visibility=args.visibility,
        auto=args.auto,
    )

    export_html(result, args.output)
    if args.json:
        result.to_json(args.json)

    print("=" * 40)
    print("✅ Patrol planning complete!")


if __name__ == "__main__":
    main()
