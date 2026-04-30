"""Autonomous Spatial Ecosystem Simulator on Voronoi Tessellations.

Simulates an ecosystem where each Voronoi cell is a territory occupied by
competing species.  Species interact via Lotka-Volterra dynamics (competition,
predator-prey, mutualism) and migrate across cell boundaries.

Autonomous / agentic features:
    * **Extinction Early Warning** — alerts when a species drops below
      critical population across all cells
    * **Invasive Species Alert** — detects when one species dominates >70%
      of cells
    * **Biodiversity Index** — Shannon diversity per tick with decline alerts
    * **Auto-Intervention** (``--autopilot``) — corrective measures to
      maintain biodiversity (predator boosts, habitat restoration)
    * **Ecosystem Health Score** — composite 0-100 from diversity, stability,
      evenness

CLI examples::

    python vormap_ecosystem.py --points 30 --species 4 --ticks 100 --output eco.html
    python vormap_ecosystem.py --points 25 --autopilot --output result.html
    python vormap_ecosystem.py --preset rainforest --output rainforest.html

Programmatic::

    from vormap_ecosystem import EcosystemSimulator, export_html
    sim = EcosystemSimulator(n_points=30, n_species=4, ticks=100)
    result = sim.run()
    export_html(result, "ecosystem.html")
"""

from __future__ import annotations

import argparse
import html as _html
import json
import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from vormap_utils import build_distance_adjacency, euclidean as _dist

# ── Geometry helpers ────────────────────────────────────────────────

def _random_points(n: int, w: float = 500.0, h: float = 500.0) -> List[Tuple[float, float]]:
    return [(random.uniform(20, w - 20), random.uniform(20, h - 20)) for _ in range(n)]


def _build_adjacency(pts: List[Tuple[float, float]], threshold_factor: float = 2.0) -> Dict[int, List[int]]:
    """Build adjacency based on distance threshold (avg spacing × factor).

    Delegates to :func:`vormap_utils.build_distance_adjacency`.
    """
    return build_distance_adjacency(pts, threshold_factor)


# ── Species & Presets ───────────────────────────────────────────────

class Species:
    __slots__ = ("name", "color", "growth", "capacity", "mobility", "defense")

    def __init__(self, name: str, color: str, growth: float = 0.1,
                 capacity: float = 100.0, mobility: float = 0.05,
                 defense: float = 0.5):
        self.name = name
        self.color = color
        self.growth = growth
        self.capacity = capacity
        self.mobility = mobility
        self.defense = defense


_COLORS = ["#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c"]
_NAMES = ["Apex", "Herba", "Fungi", "Coral", "Avian", "Myco"]

PRESETS: Dict[str, Dict[str, Any]] = {
    "rainforest": {
        "n_species": 5, "n_points": 35, "ticks": 120,
        "species_overrides": [
            {"growth": 0.12, "capacity": 120, "mobility": 0.03, "defense": 0.4},
            {"growth": 0.15, "capacity": 80, "mobility": 0.06, "defense": 0.3},
            {"growth": 0.08, "capacity": 60, "mobility": 0.02, "defense": 0.7},
            {"growth": 0.10, "capacity": 90, "mobility": 0.04, "defense": 0.5},
            {"growth": 0.07, "capacity": 50, "mobility": 0.05, "defense": 0.6},
        ],
        "event_rate": 0.08,
    },
    "savanna": {
        "n_species": 4, "n_points": 25, "ticks": 100,
        "species_overrides": [
            {"growth": 0.15, "capacity": 150, "mobility": 0.07, "defense": 0.3},
            {"growth": 0.12, "capacity": 130, "mobility": 0.06, "defense": 0.4},
            {"growth": 0.05, "capacity": 40, "mobility": 0.02, "defense": 0.8},
            {"growth": 0.04, "capacity": 30, "mobility": 0.03, "defense": 0.7},
        ],
        "event_rate": 0.06,
    },
    "tundra": {
        "n_species": 3, "n_points": 20, "ticks": 80,
        "species_overrides": [
            {"growth": 0.06, "capacity": 60, "mobility": 0.04, "defense": 0.6},
            {"growth": 0.04, "capacity": 40, "mobility": 0.02, "defense": 0.8},
            {"growth": 0.03, "capacity": 30, "mobility": 0.01, "defense": 0.9},
        ],
        "event_rate": 0.10,
    },
    "reef": {
        "n_species": 5, "n_points": 30, "ticks": 100,
        "species_overrides": [
            {"growth": 0.10, "capacity": 80, "mobility": 0.05, "defense": 0.5},
            {"growth": 0.12, "capacity": 70, "mobility": 0.06, "defense": 0.4},
            {"growth": 0.08, "capacity": 90, "mobility": 0.03, "defense": 0.6},
            {"growth": 0.11, "capacity": 75, "mobility": 0.04, "defense": 0.5},
            {"growth": 0.09, "capacity": 65, "mobility": 0.07, "defense": 0.3},
        ],
        "event_rate": 0.05,
    },
}


# ── Metrics ─────────────────────────────────────────────────────────

def _shannon_diversity(populations: List[List[float]]) -> float:
    """Shannon diversity index across all cells."""
    totals = [0.0] * len(populations[0]) if populations else []
    for cell_pop in populations:
        for s in range(len(cell_pop)):
            totals[s] += cell_pop[s]
    grand = sum(totals)
    if grand <= 0:
        return 0.0
    h = 0.0
    for t in totals:
        if t > 0:
            p = t / grand
            h -= p * math.log(p)
    return h


def _evenness(populations: List[List[float]]) -> float:
    """Pielou's evenness J = H / ln(S)."""
    n_species = len(populations[0]) if populations else 0
    if n_species <= 1:
        return 1.0
    h = _shannon_diversity(populations)
    return h / math.log(n_species)


def _dominant_species(populations: List[List[float]]) -> List[int]:
    """Return index of dominant species per cell."""
    result = []
    for cell_pop in populations:
        best_idx = 0
        best_val = cell_pop[0]
        for s in range(1, len(cell_pop)):
            if cell_pop[s] > best_val:
                best_val = cell_pop[s]
                best_idx = s
        result.append(best_idx)
    return result


def _health_score(diversity: float, max_diversity: float,
                  evenness: float, stability: float) -> float:
    """Composite health 0-100."""
    div_score = (diversity / max_diversity * 40) if max_diversity > 0 else 0
    even_score = evenness * 30
    stab_score = stability * 30
    return min(100.0, max(0.0, div_score + even_score + stab_score))


# ── Simulator ───────────────────────────────────────────────────────

class EcosystemSimulator:
    def __init__(self, n_points: int = 30, n_species: int = 4,
                 ticks: int = 100, autopilot: bool = False,
                 preset: Optional[str] = None, seed: Optional[int] = None,
                 event_rate: float = 0.07):
        if seed is not None:
            random.seed(seed)

        if preset and preset in PRESETS:
            cfg = PRESETS[preset]
            n_species = cfg["n_species"]
            n_points = cfg["n_points"]
            ticks = cfg["ticks"]
            event_rate = cfg.get("event_rate", event_rate)
            overrides = cfg.get("species_overrides", [])
        else:
            overrides = []

        self.n_points = n_points
        self.n_species = n_species
        self.ticks = ticks
        self.autopilot = autopilot
        self.event_rate = event_rate
        self.preset = preset

        # Create species
        self.species: List[Species] = []
        for i in range(n_species):
            kw: Dict[str, Any] = {}
            if i < len(overrides):
                kw = dict(overrides[i])
            self.species.append(Species(
                name=_NAMES[i % len(_NAMES)],
                color=_COLORS[i % len(_COLORS)],
                **kw,
            ))

        # Generate points and adjacency
        self.points = _random_points(n_points)
        self.adj = _build_adjacency(self.points)

        # Initialize populations: each cell gets random pop for each species
        self.populations: List[List[float]] = []
        for _ in range(n_points):
            cell = []
            for sp in self.species:
                cell.append(random.uniform(5, sp.capacity * 0.5))
            self.populations.append(cell)

    def run(self) -> Dict[str, Any]:
        history: List[List[List[float]]] = []
        diversity_history: List[float] = []
        health_history: List[float] = []
        events: List[Dict[str, Any]] = []
        interventions: List[Dict[str, Any]] = []
        max_div = math.log(self.n_species) if self.n_species > 1 else 1.0

        prev_diversity = _shannon_diversity(self.populations)

        for t in range(self.ticks):
            # Snapshot
            snapshot = [list(c) for c in self.populations]
            history.append(snapshot)
            div = _shannon_diversity(self.populations)
            even = _evenness(self.populations)
            # stability: 1 - |delta_diversity / max_diversity|
            stab = max(0.0, 1.0 - abs(div - prev_diversity) / max(max_div, 0.01))
            hs = _health_score(div, max_div, even, stab)
            diversity_history.append(div)
            health_history.append(hs)
            prev_diversity = div

            # Random events
            if random.random() < self.event_rate:
                ev = self._random_event(t)
                if ev:
                    events.append(ev)

            # Autonomous alerts
            self._check_alerts(t, events)

            # Autopilot interventions
            if self.autopilot:
                iv = self._autopilot_step(t, div, max_div)
                if iv:
                    interventions.append(iv)

            # Lotka-Volterra dynamics within cells
            new_pops = [list(c) for c in self.populations]
            for ci in range(self.n_points):
                cell = self.populations[ci]
                total = sum(cell)
                for s in range(self.n_species):
                    sp = self.species[s]
                    pop = cell[s]
                    if pop < 0.01:
                        new_pops[ci][s] = 0.0
                        continue
                    # Competition pressure from other species
                    competition = 0.0
                    for s2 in range(self.n_species):
                        if s2 != s:
                            competition += cell[s2] * 0.001 * (1.0 - sp.defense)
                    growth = sp.growth * pop * (1.0 - (total / (sp.capacity * 1.5))) - competition
                    new_pops[ci][s] = max(0.0, pop + growth)

            # Migration
            for ci in range(self.n_points):
                for ni in self.adj[ci]:
                    for s in range(self.n_species):
                        sp = self.species[s]
                        gradient = self.populations[ci][s] - self.populations[ni][s]
                        if gradient > 0:
                            flow = gradient * sp.mobility * 0.1
                            flow = min(flow, new_pops[ci][s] * 0.3)
                            new_pops[ci][s] -= flow
                            new_pops[ni][s] += flow

            self.populations = new_pops

        # Final snapshot
        history.append([list(c) for c in self.populations])
        diversity_history.append(_shannon_diversity(self.populations))
        health_history.append(_health_score(
            diversity_history[-1], max_div,
            _evenness(self.populations),
            max(0.0, 1.0 - abs(diversity_history[-1] - prev_diversity) / max(max_div, 0.01))
        ))

        # Recommendations
        recs = self._recommendations(diversity_history, health_history, events)

        return {
            "points": self.points,
            "species": [{"name": s.name, "color": s.color} for s in self.species],
            "history": history,
            "diversity": diversity_history,
            "health": health_history,
            "events": events,
            "interventions": interventions,
            "recommendations": recs,
            "ticks": self.ticks,
            "n_points": self.n_points,
            "n_species": self.n_species,
            "preset": self.preset,
            "autopilot": self.autopilot,
            "final_dominance": _dominant_species(self.populations),
        }

    def _random_event(self, tick: int) -> Optional[Dict[str, Any]]:
        kind = random.choice(["drought", "disease", "mutation"])
        if kind == "drought":
            region = random.sample(range(self.n_points), min(5, self.n_points))
            for ci in region:
                for s in range(self.n_species):
                    self.populations[ci][s] *= 0.6
            return {"tick": tick, "type": "drought", "cells": len(region),
                    "desc": f"Drought hit {len(region)} cells — populations reduced 40%"}
        elif kind == "disease":
            # Target dominant species
            doms = _dominant_species(self.populations)
            counts = [0] * self.n_species
            for d in doms:
                counts[d] += 1
            target = counts.index(max(counts))
            for ci in range(self.n_points):
                self.populations[ci][target] *= 0.7
            return {"tick": tick, "type": "disease", "species": self.species[target].name,
                    "desc": f"Disease struck {self.species[target].name} — 30% population loss"}
        elif kind == "mutation":
            s = random.randrange(self.n_species)
            self.species[s].growth *= random.uniform(0.8, 1.3)
            return {"tick": tick, "type": "mutation", "species": self.species[s].name,
                    "desc": f"{self.species[s].name} mutated — growth rate changed"}
        return None

    def _check_alerts(self, tick: int, events: List[Dict[str, Any]]) -> None:
        # Extinction warning
        for s in range(self.n_species):
            total = sum(self.populations[ci][s] for ci in range(self.n_points))
            if 0 < total < self.n_points * 2:
                events.append({
                    "tick": tick, "type": "extinction_warning",
                    "species": self.species[s].name,
                    "desc": f"⚠ {self.species[s].name} near extinction (total pop: {total:.1f})"
                })

        # Invasive alert
        doms = _dominant_species(self.populations)
        counts = [0] * self.n_species
        for d in doms:
            counts[d] += 1
        for s in range(self.n_species):
            if counts[s] > self.n_points * 0.7:
                events.append({
                    "tick": tick, "type": "invasive_alert",
                    "species": self.species[s].name,
                    "desc": f"🚨 {self.species[s].name} dominates {counts[s]}/{self.n_points} cells — invasive!"
                })

    def _autopilot_step(self, tick: int, div: float, max_div: float) -> Optional[Dict[str, Any]]:
        if max_div <= 0:
            return None
        ratio = div / max_div
        if ratio < 0.5:
            # Boost weakest species
            totals = [0.0] * self.n_species
            for ci in range(self.n_points):
                for s in range(self.n_species):
                    totals[s] += self.populations[ci][s]
            weakest = totals.index(min(totals))
            boost = self.species[weakest].capacity * 0.3
            for ci in random.sample(range(self.n_points), min(5, self.n_points)):
                self.populations[ci][weakest] += boost
            return {"tick": tick, "action": "habitat_restoration",
                    "species": self.species[weakest].name,
                    "desc": f"Autopilot: boosted {self.species[weakest].name} in 5 cells"}
        return None

    def _recommendations(self, div_hist: List[float], health_hist: List[float],
                         events: List[Dict[str, Any]]) -> List[str]:
        recs = []
        if len(div_hist) > 10:
            early = sum(div_hist[:10]) / 10
            late = sum(div_hist[-10:]) / 10
            if late < early * 0.7:
                recs.append("Biodiversity declining — consider enabling --autopilot")
            if late > early * 1.1:
                recs.append("Biodiversity improving — ecosystem appears healthy")

        extinctions = [e for e in events if e["type"] == "extinction_warning"]
        if len(extinctions) > 5:
            recs.append(f"{len(extinctions)} extinction warnings — carrying capacity may be too low")

        invasives = [e for e in events if e["type"] == "invasive_alert"]
        if invasives:
            recs.append("Invasive species detected — increase competition pressure or mobility of other species")

        if health_hist and health_hist[-1] < 40:
            recs.append("Final health score below 40 — ecosystem is in critical condition")
        elif health_hist and health_hist[-1] > 75:
            recs.append("Final health score above 75 — ecosystem is thriving")

        if not recs:
            recs.append("Ecosystem completed simulation within normal parameters")
        return recs


# ── HTML export ─────────────────────────────────────────────────────

def export_html(result: Dict[str, Any], path: str) -> str:
    pts = result["points"]
    species = result["species"]
    n_species = result["n_species"]
    history = result["history"]
    diversity = result["diversity"]
    health = result["health"]
    events = result["events"]
    interventions = result["interventions"]
    recs = result["recommendations"]
    final_dom = result["final_dominance"]

    W, H = 500, 500

    # Build SVG cells (simple nearest-point coloring on a grid)
    svg_cells = ""
    grid = 5
    for gx in range(0, W, grid):
        for gy in range(0, H, grid):
            best_i = 0
            best_d = float("inf")
            for i, (px, py) in enumerate(pts):
                d = (gx - px) ** 2 + (gy - py) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            dom_s = final_dom[best_i]
            # Opacity from total population
            final_pop = history[-1][best_i]
            total = sum(final_pop)
            max_total = sum(sp["capacity"] if "capacity" in sp else 100 for sp in species)  # fallback
            opacity = min(1.0, max(0.15, total / (n_species * 80)))
            color = species[dom_s]["color"]
            svg_cells += f'<rect x="{gx}" y="{gy}" width="{grid}" height="{grid}" fill="{color}" opacity="{opacity:.2f}"/>'

    # Points
    svg_pts = ""
    for i, (px, py) in enumerate(pts):
        svg_pts += f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="#333" stroke="#fff" stroke-width="0.5"/>'

    # Population timeline data (aggregate per species per tick)
    pop_series: List[List[float]] = [[] for _ in range(n_species)]
    for frame in history:
        for s in range(n_species):
            total = sum(frame[ci][s] for ci in range(len(frame)))
            pop_series[s].append(total)

    # Build pop chart as SVG polylines
    max_pop = max(max(s) for s in pop_series) if pop_series and pop_series[0] else 1
    chart_w, chart_h = 600, 200
    n_frames = len(history)
    pop_lines = ""
    for s in range(n_species):
        points_str = ""
        for i, v in enumerate(pop_series[s]):
            x = i / max(1, n_frames - 1) * chart_w
            y = chart_h - (v / max_pop * (chart_h - 10))
            points_str += f"{x:.1f},{y:.1f} "
        pop_lines += f'<polyline points="{points_str.strip()}" fill="none" stroke="{species[s]["color"]}" stroke-width="2"/>'

    # Diversity chart
    max_div = max(diversity) if diversity else 1
    div_points = ""
    for i, v in enumerate(diversity):
        x = i / max(1, len(diversity) - 1) * chart_w
        y = chart_h - (v / max_div * (chart_h - 10))
        div_points += f"{x:.1f},{y:.1f} "
    div_line = f'<polyline points="{div_points.strip()}" fill="none" stroke="#2c3e50" stroke-width="2"/>'

    # Events log
    events_html = ""
    for ev in events[-50:]:  # last 50
        icon = {"drought": "🏜️", "disease": "🦠", "mutation": "🧬",
                "extinction_warning": "⚠️", "invasive_alert": "🚨"}.get(ev["type"], "📌")
        events_html += f'<tr><td>{ev["tick"]}</td><td>{icon} {_html.escape(ev["type"])}</td><td>{_html.escape(ev["desc"])}</td></tr>'

    # Interventions
    interv_html = ""
    for iv in interventions:
        interv_html += f'<tr><td>{iv["tick"]}</td><td>🔧 {_html.escape(iv["action"])}</td><td>{_html.escape(iv["desc"])}</td></tr>'

    # Recommendations
    recs_html = "".join(f"<li>{_html.escape(r)}</li>" for r in recs)

    # Health gauge
    final_health = health[-1] if health else 0
    gauge_color = "#2ecc71" if final_health > 70 else "#f39c12" if final_health > 40 else "#e74c3c"

    # Legend
    legend_html = "".join(
        f'<span style="display:inline-block;margin-right:12px;">'
        f'<span style="display:inline-block;width:14px;height:14px;background:{sp["color"]};'
        f'border-radius:3px;vertical-align:middle;margin-right:4px;"></span>{_html.escape(sp["name"])}</span>'
        for sp in species
    )

    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voronoi Ecosystem Simulation</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f1117;color:#c9d1d9;padding:24px}}
h1{{text-align:center;font-size:1.6rem;margin-bottom:4px;color:#58a6ff}}
.sub{{text-align:center;color:#8b949e;margin-bottom:20px;font-size:.9rem}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1200px;margin:0 auto}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}}
.card h2{{font-size:1rem;color:#58a6ff;margin-bottom:10px}}
svg{{width:100%;height:auto}}
table{{width:100%;border-collapse:collapse;font-size:.82rem}}
th,td{{padding:4px 8px;border-bottom:1px solid #21262d;text-align:left}}
th{{color:#8b949e}}
.gauge{{text-align:center;font-size:2.5rem;font-weight:bold;color:{gauge_color}}}
.gauge-label{{text-align:center;color:#8b949e;font-size:.85rem}}
ul{{padding-left:20px}}li{{margin-bottom:6px;font-size:.9rem}}
.legend{{text-align:center;margin:10px 0;font-size:.85rem}}
.full{{grid-column:1/-1}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<h1>🌿 Voronoi Ecosystem Simulator</h1>
<p class="sub">{result['n_points']} cells · {n_species} species · {result['ticks']} ticks{' · autopilot' if result['autopilot'] else ''}{(' · ' + result['preset']) if result['preset'] else ''}</p>
<div class="legend">{legend_html}</div>
<div class="grid">
<div class="card"><h2>🗺️ Territory Map (Final State)</h2>
<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{svg_cells}{svg_pts}</svg></div>
<div class="card"><h2>💚 Ecosystem Health</h2>
<div class="gauge">{final_health:.0f}</div><div class="gauge-label">Health Score (0-100)</div>
<br><h2>📊 Biodiversity (Shannon Index)</h2>
<svg viewBox="0 0 {chart_w} {chart_h}" xmlns="http://www.w3.org/2000/svg" style="background:#0d1117;border-radius:6px">
<line x1="0" y1="{chart_h}" x2="{chart_w}" y2="{chart_h}" stroke="#21262d"/>
{div_line}</svg></div>
<div class="card full"><h2>📈 Population Timeline</h2>
<svg viewBox="0 0 {chart_w} {chart_h}" xmlns="http://www.w3.org/2000/svg" style="background:#0d1117;border-radius:6px">
<line x1="0" y1="{chart_h}" x2="{chart_w}" y2="{chart_h}" stroke="#21262d"/>
{pop_lines}</svg></div>
<div class="card"><h2>📋 Event Log (last 50)</h2>
<div style="max-height:300px;overflow-y:auto"><table><tr><th>Tick</th><th>Type</th><th>Details</th></tr>{events_html}</table></div></div>
<div class="card"><h2>🔧 Interventions</h2>
{'<div style="max-height:300px;overflow-y:auto"><table><tr><th>Tick</th><th>Action</th><th>Details</th></tr>' + interv_html + '</table></div>' if interv_html else '<p style="color:#8b949e">No interventions (enable --autopilot)</p>'}</div>
<div class="card full"><h2>💡 Proactive Recommendations</h2><ul>{recs_html}</ul></div>
</div></body></html>"""

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True) if os.path.dirname(path) else None
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return os.path.abspath(path)


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Autonomous Spatial Ecosystem Simulator")
    ap.add_argument("--points", type=int, default=30, help="Number of Voronoi cells")
    ap.add_argument("--species", type=int, default=4, help="Number of species (3-6)")
    ap.add_argument("--ticks", type=int, default=100, help="Simulation ticks")
    ap.add_argument("--autopilot", action="store_true", help="Enable autonomous interventions")
    ap.add_argument("--preset", choices=list(PRESETS.keys()), help="Use a preset ecosystem")
    ap.add_argument("--seed", type=int, help="Random seed for reproducibility")
    ap.add_argument("--event-rate", type=float, default=0.07, help="Random event probability per tick")
    ap.add_argument("--output", "-o", default="ecosystem.html", help="Output HTML path")
    ap.add_argument("--json", action="store_true", help="Also export raw JSON data")
    args = ap.parse_args()

    n_species = max(2, min(6, args.species))
    sim = EcosystemSimulator(
        n_points=args.points, n_species=n_species, ticks=args.ticks,
        autopilot=args.autopilot, preset=args.preset, seed=args.seed,
        event_rate=args.event_rate,
    )
    print(f"Running ecosystem: {args.points} cells, {n_species} species, "
          f"{args.ticks} ticks{' [autopilot]' if args.autopilot else ''}"
          f"{(' [' + args.preset + ']') if args.preset else ''}")
    result = sim.run()

    out = export_html(result, args.output)
    print(f"Report: {out}")

    if args.json:
        jp = args.output.rsplit(".", 1)[0] + ".json"
        with open(jp, "w", encoding="utf-8") as f:
            json.dump({
                "diversity": result["diversity"],
                "health": result["health"],
                "events": result["events"],
                "interventions": result["interventions"],
                "recommendations": result["recommendations"],
            }, f, indent=2)
        print(f"JSON:   {os.path.abspath(jp)}")

    print(f"\nHealth: {result['health'][-1]:.0f}/100")
    print(f"Events: {len(result['events'])}")
    for r in result["recommendations"]:
        print(f"  - {r}")


if __name__ == "__main__":
    main()
