#!/usr/bin/env python3
"""Spatial Attention Engine — autonomous analytical focus allocation.

Decides WHERE is interesting in a Voronoi diagram without being told.
Allocates "attention" (analytical focus priority) across cells/regions
based on information density, change velocity, strategic importance,
and surprise — making the diagram self-aware about which areas deserve
deeper analysis.

This is an *agentic* capability — the engine autonomously identifies
what matters, suppresses already-explored regions via inhibition-of-return
decay, and produces a priority schedule for downstream analysis.

Seven attention engines:

- **Information Density** — Measures how much "information" each cell
  carries based on area variance relative to neighbors, neighbor count
  deviation, and local clustering coefficient.
- **Change Velocity** — Given multiple snapshots, detects which regions
  are changing fastest via displacement tracking and area flux.
- **Strategic Importance** — Identifies cells at topological chokepoints:
  bridges between clusters and high-betweenness nodes.
- **Surprise Detector** — Cells that violate expected spatial
  autocorrelation (local Moran-like statistic).
- **Convergence Zones** — Areas where multiple attention signals overlap.
- **Attention Decay** — Temporal inhibition-of-return: recently-attended
  areas are suppressed, forcing exploration of neglected regions.
- **Autonomous Scheduler** — Combines all engines into a weighted priority
  queue and produces an ordered attention schedule.

Usage (Python API)::

    from vormap_attention import AttentionEngine, attention_analyze, attention_demo

    # Quick one-liner
    result = attention_analyze("points.txt", top_k=5)
    print(f"Health: {result.health_score:.1f}/100")
    for cid in result.top_k:
        print(f"  Cell {cid}: score {result.attention_map[cid]:.3f}")

    # Detailed API
    engine = AttentionEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze()
    engine.to_html("attention.html")

    # Temporal analysis
    snaps = [[(0,0),(1,1)], [(0,0.5),(1,1.5)], [(0,1),(1,2)]]
    result = engine.analyze_temporal(snaps)

    # Attention decay
    engine.attend(0)  # mark cell 0 as explored
    result2 = engine.analyze()  # cell 0 will be suppressed

CLI::

    python vormap_attention.py points.txt
    python vormap_attention.py points.txt --top-k 5
    python vormap_attention.py points.txt --temporal snap1.txt,snap2.txt
    python vormap_attention.py points.txt --json out.json --html dash.html
    python vormap_attention.py --demo
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import random
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CellAttention:
    """Attention profile for a single spatial cell."""
    cell_id: int
    x: float
    y: float
    info_density: float = 0.0
    change_velocity: float = 0.0
    strategic_importance: float = 0.0
    surprise: float = 0.0
    convergence: float = 0.0
    decay_factor: float = 1.0
    composite_score: float = 0.0
    rank: int = 0


@dataclass
class AttentionResult:
    """Full attention analysis result."""
    cells: List[CellAttention] = field(default_factory=list)
    schedule: List[int] = field(default_factory=list)
    attention_map: Dict[int, float] = field(default_factory=dict)
    top_k: List[int] = field(default_factory=list)
    neglected: List[int] = field(default_factory=list)
    convergence_zones: List[List[int]] = field(default_factory=list)
    health_score: float = 0.0
    engine_contributions: Dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _load_points(path: str) -> List[Tuple[float, float]]:
    """Load points from a whitespace-separated text file."""
    pts: List[Tuple[float, float]] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _knn_adjacency(points: List[Tuple[float, float]], k: int = 6) -> Dict[int, List[int]]:
    """Build k-nearest-neighbor adjacency (symmetric)."""
    n = len(points)
    if n <= 1:
        return {i: [] for i in range(n)}
    actual_k = min(k, n - 1)
    adj: Dict[int, set] = {i: set() for i in range(n)}
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append((_euclidean(points[i], points[j]), j))
        dists.sort()
        for _, j in dists[:actual_k]:
            adj[i].add(j)
            adj[j].add(i)
    return {i: sorted(v) for i, v in adj.items()}


def _voronoi_areas(points: List[Tuple[float, float]], adj: Dict[int, List[int]]) -> List[float]:
    """Approximate Voronoi cell area using average distance to neighbors squared."""
    n = len(points)
    areas = []
    for i in range(n):
        if not adj[i]:
            areas.append(1.0)
            continue
        avg_dist = sum(_euclidean(points[i], points[j]) for j in adj[i]) / len(adj[i])
        areas.append(avg_dist ** 2)
    return areas


def _normalize(values: List[float]) -> List[float]:
    """Min-max normalize to [0, 1]."""
    if not values:
        return []
    mn, mx = min(values), max(values)
    if mx - mn < 1e-12:
        return [0.5] * len(values)
    return [(v - mn) / (mx - mn) for v in values]


def _shannon_entropy(scores: List[float]) -> float:
    """Shannon entropy of attention distribution (normalized 0-1)."""
    total = sum(scores)
    if total < 1e-12 or len(scores) <= 1:
        return 0.0
    probs = [s / total for s in scores if s > 0]
    if not probs:
        return 0.0
    h = -sum(p * math.log(p) for p in probs)
    max_h = math.log(len(scores))
    if max_h < 1e-12:
        return 0.0
    return h / max_h


# ---------------------------------------------------------------------------
# Engine 1: Information Density
# ---------------------------------------------------------------------------

def _info_density(points: List[Tuple[float, float]], adj: Dict[int, List[int]],
                  areas: List[float]) -> List[float]:
    """Score cells by local information density."""
    n = len(points)
    if n == 0:
        return []
    scores = []
    for i in range(n):
        neighbors = adj[i]
        # Component 1: area deviation from neighbors
        if neighbors:
            neighbor_areas = [areas[j] for j in neighbors]
            mean_area = sum(neighbor_areas) / len(neighbor_areas)
            area_dev = abs(areas[i] - mean_area) / (mean_area + 1e-12)
        else:
            area_dev = 0.0

        # Component 2: neighbor count deviation
        if n > 1:
            avg_degree = sum(len(adj[j]) for j in range(n)) / n
            degree_dev = abs(len(neighbors) - avg_degree) / (avg_degree + 1e-12)
        else:
            degree_dev = 0.0

        # Component 3: local clustering coefficient
        if len(neighbors) >= 2:
            links = 0
            for a_idx in range(len(neighbors)):
                for b_idx in range(a_idx + 1, len(neighbors)):
                    if neighbors[b_idx] in adj[neighbors[a_idx]]:
                        links += 1
            max_links = len(neighbors) * (len(neighbors) - 1) / 2
            clustering = links / max_links
        else:
            clustering = 0.0

        scores.append((area_dev + degree_dev + (1 - clustering)) / 3.0)
    return _normalize(scores)


# ---------------------------------------------------------------------------
# Engine 2: Change Velocity
# ---------------------------------------------------------------------------

def _change_velocity(snapshots: List[List[Tuple[float, float]]]) -> List[float]:
    """Score cells by how much they move across snapshots."""
    if len(snapshots) < 2:
        n = len(snapshots[0]) if snapshots else 0
        return [0.0] * n

    # Use last snapshot as reference
    ref = snapshots[-1]
    n = len(ref)
    velocities = [0.0] * n

    for i in range(n):
        total_disp = 0.0
        count = 0
        for s_idx in range(len(snapshots) - 1):
            if i < len(snapshots[s_idx]):
                d = _euclidean(snapshots[s_idx][i], ref[i])
                total_disp += d
                count += 1
        if count > 0:
            velocities[i] = total_disp / count

    return _normalize(velocities)


# ---------------------------------------------------------------------------
# Engine 3: Strategic Importance
# ---------------------------------------------------------------------------

def _strategic_importance(points: List[Tuple[float, float]],
                          adj: Dict[int, List[int]]) -> List[float]:
    """Approximate betweenness centrality via sampled BFS."""
    n = len(points)
    if n <= 2:
        return [0.5] * n

    betweenness = [0.0] * n
    sample_size = min(20, n)
    sources = random.Random(0).sample(range(n), sample_size)

    for s in sources:
        # BFS from s
        dist = [-1] * n
        dist[s] = 0
        pred: Dict[int, List[int]] = {i: [] for i in range(n)}
        queue = collections.deque([s])
        order = []
        sigma = [0.0] * n
        sigma[s] = 1.0

        while queue:
            v = queue.popleft()
            order.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        delta = [0.0] * n
        for w in reversed(order):
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
            if w != s:
                betweenness[w] += delta[w]

    return _normalize(betweenness)


# ---------------------------------------------------------------------------
# Engine 4: Surprise Detector
# ---------------------------------------------------------------------------

def _surprise(points: List[Tuple[float, float]], adj: Dict[int, List[int]],
              areas: List[float]) -> List[float]:
    """Local Moran-like surprise: how different is a cell from its neighbors."""
    n = len(points)
    if n <= 1:
        return [0.0] * n

    global_mean = sum(areas) / n
    global_var = sum((a - global_mean) ** 2 for a in areas) / n
    if global_var < 1e-12:
        return [0.0] * n

    scores = []
    for i in range(n):
        if not adj[i]:
            scores.append(0.0)
            continue
        neighbor_mean = sum(areas[j] for j in adj[i]) / len(adj[i])
        z_i = (areas[i] - global_mean) / math.sqrt(global_var)
        z_neighbors = (neighbor_mean - global_mean) / math.sqrt(global_var)
        # Negative spatial autocorrelation = surprise
        local_moran = z_i * z_neighbors
        # Low or negative Moran = surprising
        scores.append(max(0.0, -local_moran))

    return _normalize(scores)


# ---------------------------------------------------------------------------
# Engine 5: Convergence Zones
# ---------------------------------------------------------------------------

def _convergence_zones(cell_scores: List[Dict[str, float]],
                       adj: Dict[int, List[int]],
                       threshold: float = 0.7) -> Tuple[List[float], List[List[int]]]:
    """Find regions where multiple engines agree on high attention."""
    n = len(cell_scores)
    convergence_scores = []
    engines = ["info_density", "strategic_importance", "surprise"]

    for i in range(n):
        high_count = sum(1 for e in engines if cell_scores[i].get(e, 0) > threshold)
        convergence_scores.append(high_count / len(engines))

    # Find connected components of high-convergence cells
    zones: List[List[int]] = []
    visited = set()
    for i in range(n):
        if i in visited or convergence_scores[i] < 0.5:
            continue
        # BFS to find zone
        zone = []
        queue = collections.deque([i])
        visited.add(i)
        while queue:
            v = queue.popleft()
            zone.append(v)
            for w in adj[v]:
                if w not in visited and convergence_scores[w] >= 0.5:
                    visited.add(w)
                    queue.append(w)
        if zone:
            zones.append(sorted(zone))

    return _normalize(convergence_scores) if convergence_scores else [], zones


# ---------------------------------------------------------------------------
# Engine 6: Attention Decay
# ---------------------------------------------------------------------------

class _DecayTracker:
    """Tracks attention history with exponential decay."""

    def __init__(self, half_life: int = 3):
        self.half_life = half_life
        self.attend_counts: Dict[int, int] = {}  # cell_id -> cycles since attended
        self._attended: set = set()

    def attend(self, cell_id: int):
        self._attended.add(cell_id)
        self.attend_counts[cell_id] = 0

    def tick(self):
        """Advance one analysis cycle."""
        for cid in list(self.attend_counts.keys()):
            self.attend_counts[cid] += 1

    def decay_factor(self, cell_id: int) -> float:
        """Returns 0-1: 1 means full attention available, 0 means fully suppressed."""
        if cell_id not in self.attend_counts:
            return 1.0
        cycles = self.attend_counts[cell_id]
        return 1.0 - math.exp(-0.693 * cycles / self.half_life)

    def reset(self):
        self.attend_counts.clear()
        self._attended.clear()


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS = {
    "info_density": 0.2,
    "change_velocity": 0.15,
    "strategic_importance": 0.25,
    "surprise": 0.2,
    "convergence": 0.1,
    "decay": 0.1,
}


class AttentionEngine:
    """Autonomous spatial attention allocation engine."""

    def __init__(self, points: Optional[List[Tuple[float, float]]] = None,
                 path: Optional[str] = None, k: int = 10,
                 adj_k: int = 6, weights: Optional[Dict[str, float]] = None,
                 half_life: int = 3):
        if points is not None:
            self.points = list(points)
        elif path is not None:
            self.points = _load_points(path)
        else:
            self.points = []

        self.k = k
        self.adj_k = adj_k
        self.weights = weights or dict(_DEFAULT_WEIGHTS)
        self._decay = _DecayTracker(half_life=half_life)
        self._last_result: Optional[AttentionResult] = None

    def analyze(self) -> AttentionResult:
        """Single-snapshot attention analysis."""
        return self._run_analysis(snapshots=None)

    def analyze_temporal(self, snapshots: List[List[Tuple[float, float]]]) -> AttentionResult:
        """Multi-snapshot temporal analysis."""
        return self._run_analysis(snapshots=snapshots)

    def attend(self, cell_id: int):
        """Mark a cell as attended (triggers decay for next cycle)."""
        self._decay.attend(cell_id)

    def reset_decay(self):
        """Clear attention history."""
        self._decay.reset()

    def _run_analysis(self, snapshots: Optional[List[List[Tuple[float, float]]]] = None) -> AttentionResult:
        n = len(self.points)
        if n == 0:
            return AttentionResult(health_score=0.0)

        # Build spatial structures
        adj = _knn_adjacency(self.points, k=self.adj_k)
        areas = _voronoi_areas(self.points, adj)

        # Run engines
        info_scores = _info_density(self.points, adj, areas)
        strategic_scores = _strategic_importance(self.points, adj)
        surprise_scores = _surprise(self.points, adj, areas)

        if snapshots and len(snapshots) >= 2:
            velocity_scores = _change_velocity(snapshots)
            # Pad/trim to match current points
            if len(velocity_scores) < n:
                velocity_scores.extend([0.0] * (n - len(velocity_scores)))
            velocity_scores = velocity_scores[:n]
        else:
            velocity_scores = [0.0] * n

        # Build per-cell score dicts for convergence
        cell_score_dicts = []
        for i in range(n):
            cell_score_dicts.append({
                "info_density": info_scores[i],
                "strategic_importance": strategic_scores[i],
                "surprise": surprise_scores[i],
            })

        conv_scores, zones = _convergence_zones(cell_score_dicts, adj)
        if not conv_scores:
            conv_scores = [0.0] * n

        # Decay factors
        self._decay.tick()
        decay_factors = [self._decay.decay_factor(i) for i in range(n)]

        # Composite scoring
        w = self.weights
        total_weight = sum(w.values()) or 1.0
        cells: List[CellAttention] = []
        composites = []

        for i in range(n):
            raw = (
                w.get("info_density", 0) * info_scores[i]
                + w.get("change_velocity", 0) * velocity_scores[i]
                + w.get("strategic_importance", 0) * strategic_scores[i]
                + w.get("surprise", 0) * surprise_scores[i]
                + w.get("convergence", 0) * conv_scores[i]
            ) / total_weight
            composite = raw * decay_factors[i]
            composites.append(composite)

            cells.append(CellAttention(
                cell_id=i,
                x=self.points[i][0],
                y=self.points[i][1],
                info_density=info_scores[i],
                change_velocity=velocity_scores[i],
                strategic_importance=strategic_scores[i],
                surprise=surprise_scores[i],
                convergence=conv_scores[i],
                decay_factor=decay_factors[i],
                composite_score=composite,
                rank=0,
            ))

        # Rank cells
        ranked = sorted(range(n), key=lambda i: composites[i], reverse=True)
        for rank_idx, cell_idx in enumerate(ranked):
            cells[cell_idx].rank = rank_idx + 1

        # Build result
        schedule = ranked
        attention_map = {i: composites[i] for i in range(n)}
        top_k = ranked[:self.k]
        neglected = [i for i in range(n) if decay_factors[i] >= 0.95 and composites[i] < 0.3]

        # Engine contributions
        engine_sums = {
            "info_density": sum(info_scores) / (n or 1),
            "change_velocity": sum(velocity_scores) / (n or 1),
            "strategic_importance": sum(strategic_scores) / (n or 1),
            "surprise": sum(surprise_scores) / (n or 1),
            "convergence": sum(conv_scores) / (n or 1),
        }

        # Health score: entropy of attention distribution
        health = _shannon_entropy(composites) * 100.0

        result = AttentionResult(
            cells=cells,
            schedule=schedule,
            attention_map=attention_map,
            top_k=top_k,
            neglected=neglected,
            convergence_zones=zones,
            health_score=round(health, 1),
            engine_contributions=engine_sums,
        )
        self._last_result = result
        return result

    def to_json(self, path: str) -> str:
        """Export last result to JSON."""
        result = self._last_result or self.analyze()
        data = {
            "health_score": result.health_score,
            "top_k": result.top_k,
            "schedule": result.schedule[:20],
            "neglected": result.neglected[:10],
            "convergence_zones": result.convergence_zones,
            "engine_contributions": result.engine_contributions,
            "cells": [asdict(c) for c in result.cells],
        }
        text = json.dumps(data, indent=2)
        with open(path, "w") as fh:
            fh.write(text)
        return text

    def to_html(self, path: str) -> str:
        """Export interactive HTML dashboard."""
        result = self._last_result or self.analyze()
        cells_json = json.dumps([asdict(c) for c in result.cells])
        contrib_json = json.dumps(result.engine_contributions)
        top_k_json = json.dumps(result.top_k)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spatial Attention Engine — Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;padding:24px}}
h1{{font-size:1.6rem;margin-bottom:8px;color:#58a6ff}}
.subtitle{{color:#8b949e;margin-bottom:24px;font-size:0.95rem}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}}
.card h2{{font-size:1rem;color:#79c0ff;margin-bottom:12px}}
.health{{font-size:2.4rem;font-weight:700;text-align:center;padding:16px}}
.health.good{{color:#3fb950}}.health.mid{{color:#d29922}}.health.low{{color:#f85149}}
canvas{{width:100%;height:300px;border-radius:6px;background:#0d1117}}
table{{width:100%;border-collapse:collapse;font-size:0.85rem}}
th,td{{padding:6px 8px;border-bottom:1px solid #21262d;text-align:left}}
th{{color:#8b949e;font-weight:500}}
.bar-container{{display:flex;align-items:center;gap:8px}}
.bar{{height:12px;border-radius:3px;background:#58a6ff}}
.tag{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.75rem;margin:2px;background:#1f6feb33;color:#58a6ff}}
</style>
</head>
<body>
<h1>🎯 Spatial Attention Engine</h1>
<p class="subtitle">Autonomous analytical focus allocation — {len(result.cells)} cells analyzed</p>

<div class="grid">
<div class="card">
<h2>Attention Health Score</h2>
<div class="health {'good' if result.health_score >= 70 else 'mid' if result.health_score >= 40 else 'low'}">{result.health_score:.1f}/100</div>
<p style="text-align:center;color:#8b949e;font-size:0.85rem">Shannon entropy of attention distribution</p>
</div>
<div class="card">
<h2>Engine Contributions</h2>
<div id="contrib"></div>
</div>
</div>

<div class="grid">
<div class="card">
<h2>Attention Heatmap</h2>
<canvas id="heatmap"></canvas>
</div>
<div class="card">
<h2>Priority Schedule (Top 10)</h2>
<table>
<tr><th>Rank</th><th>Cell</th><th>Score</th><th>Top Engine</th></tr>
{"".join(f'<tr><td>{c.rank}</td><td>#{c.cell_id}</td><td>{c.composite_score:.3f}</td><td>{max([(c.info_density,"Info"),(c.strategic_importance,"Strategic"),(c.surprise,"Surprise"),(c.convergence,"Convergence")], key=lambda x:x[0])[1]}</td></tr>' for c in sorted(result.cells, key=lambda c: c.rank)[:10])}
</table>
</div>
</div>

<div class="card" style="margin-bottom:16px">
<h2>Convergence Zones</h2>
{"<p>".join(f'Zone {i+1}: cells {", ".join(str(c) for c in z)}' for i, z in enumerate(result.convergence_zones[:5])) or '<p style="color:#8b949e">No convergence zones detected (threshold: 3+ engines > 0.7)</p>'}
</div>

<div class="card">
<h2>Neglected Regions</h2>
<div>{"".join(f'<span class="tag">Cell #{cid}</span>' for cid in result.neglected[:15]) or '<span style="color:#8b949e">All regions receiving adequate attention</span>'}</div>
</div>

<script>
const cells={cells_json};
const contrib={contrib_json};
const topK={top_k_json};

// Draw heatmap
const canvas=document.getElementById('heatmap');
const ctx=canvas.getContext('2d');
canvas.width=canvas.offsetWidth*2;canvas.height=600;
ctx.scale(2,2);
const w=canvas.offsetWidth,h=300;
if(cells.length>0){{
  const xs=cells.map(c=>c.x),ys=cells.map(c=>c.y);
  const minX=Math.min(...xs),maxX=Math.max(...xs);
  const minY=Math.min(...ys),maxY=Math.max(...ys);
  const rangeX=maxX-minX||1,rangeY=maxY-minY||1;
  const pad=20;
  cells.forEach(c=>{{
    const px=pad+(c.x-minX)/rangeX*(w-2*pad);
    const py=pad+(c.y-minY)/rangeY*(h-2*pad);
    const score=c.composite_score;
    const r=Math.round(88+167*score);
    const g=Math.round(166-66*score);
    const b=Math.round(255-155*score);
    const radius=4+score*8;
    ctx.beginPath();ctx.arc(px,py,radius,0,Math.PI*2);
    ctx.fillStyle=`rgba(${{r}},${{g}},${{b}},${{0.6+score*0.4}})`;
    ctx.fill();
    if(topK.includes(c.cell_id)){{
      ctx.strokeStyle='#f0e68c';ctx.lineWidth=2;ctx.stroke();
    }}
  }});
}}

// Engine contribution bars
const contribDiv=document.getElementById('contrib');
const maxC=Math.max(...Object.values(contrib))||1;
Object.entries(contrib).forEach(([name,val])=>{{
  const pct=(val/maxC*100).toFixed(0);
  contribDiv.innerHTML+=`<div class="bar-container"><span style="width:130px;font-size:0.8rem">${{name}}</span><div class="bar" style="width:${{pct}}%"></div><span style="font-size:0.8rem">${{val.toFixed(3)}}</span></div>`;
}});
</script>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_content)
        return html_content


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------

def attention_analyze(path_or_points, top_k: int = 10, **kwargs) -> AttentionResult:
    """One-liner convenience function."""
    if isinstance(path_or_points, str):
        engine = AttentionEngine(path=path_or_points, k=top_k, **kwargs)
    else:
        engine = AttentionEngine(points=path_or_points, k=top_k, **kwargs)
    return engine.analyze()


def attention_demo():
    """Generate random points and run full analysis."""
    rng = random.Random(42)
    # Create clustered point pattern
    points = []
    # Cluster 1
    for _ in range(15):
        points.append((rng.gauss(20, 3), rng.gauss(20, 3)))
    # Cluster 2
    for _ in range(10):
        points.append((rng.gauss(60, 5), rng.gauss(50, 5)))
    # Scattered
    for _ in range(8):
        points.append((rng.uniform(0, 80), rng.uniform(0, 80)))
    # Outlier
    points.append((90, 90))

    engine = AttentionEngine(points=points, k=5)
    result = engine.analyze()

    print("=" * 60)
    print("  SPATIAL ATTENTION ENGINE — Demo")
    print("=" * 60)
    print(f"\n  Points: {len(points)}")
    print(f"  Health Score: {result.health_score:.1f}/100")
    print("\n  Top-5 Priority Cells:")
    for cid in result.top_k:
        c = result.cells[cid]
        print(f"    #{cid:2d} ({c.x:.1f}, {c.y:.1f}) "
              f"score={c.composite_score:.3f} "
              f"[info={c.info_density:.2f} strat={c.strategic_importance:.2f} "
              f"surprise={c.surprise:.2f}]")
    print(f"\n  Convergence Zones: {len(result.convergence_zones)}")
    print(f"  Neglected Cells: {len(result.neglected)}")
    print("\n  Engine Contributions:")
    for eng, val in result.engine_contributions.items():
        print(f"    {eng:25s}: {val:.3f}")

    # Export
    out_html = "attention_demo.html"
    out_json = "attention_demo.json"
    engine.to_html(out_html)
    engine.to_json(out_json)
    print(f"\n  Exported: {out_html}, {out_json}")
    print("=" * 60)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    parser = argparse.ArgumentParser(
        description="Spatial Attention Engine — autonomous analytical focus allocation")
    parser.add_argument("points", nargs="?", help="Path to points file (x y per line)")
    parser.add_argument("--top-k", type=int, default=10, help="Number of top cells to highlight")
    parser.add_argument("--temporal", help="Comma-separated snapshot files for temporal analysis")
    parser.add_argument("--json", dest="json_out", help="Export result to JSON")
    parser.add_argument("--html", dest="html_out", help="Export interactive HTML dashboard")
    parser.add_argument("--adj-k", type=int, default=6, help="K for knn adjacency")
    parser.add_argument("--demo", action="store_true", help="Run demo with generated data")
    args = parser.parse_args()

    if args.demo:
        attention_demo()
        return

    if not args.points:
        parser.error("Must provide points file or --demo")

    engine = AttentionEngine(path=args.points, k=args.top_k, adj_k=args.adj_k)

    if args.temporal:
        snap_paths = [p.strip() for p in args.temporal.split(",")]
        snapshots = [_load_points(p) for p in snap_paths]
        result = engine.analyze_temporal(snapshots)
    else:
        result = engine.analyze()

    # Print summary
    print(f"Attention Health: {result.health_score:.1f}/100")
    print(f"Top-{args.top_k} cells: {result.top_k}")
    if result.convergence_zones:
        print(f"Convergence zones: {len(result.convergence_zones)}")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON: {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML: {args.html_out}")


if __name__ == "__main__":
    _cli()
