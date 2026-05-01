#!/usr/bin/env python3
"""Spatial Swarm Intelligence Engine — autonomous collective spatial optimization.

Treats Voronoi cells as intelligent swarm agents that sense neighbors,
propagate signals, and collectively solve spatial problems through emergent
behavior.  Five behavior modes simulate distinct collective-intelligence
phenomena on any point dataset.

This is an *agentic* capability — the engine autonomously runs a swarm
simulation where each cell acts as an independent agent communicating via
local signals, producing emergent global behavior that no single cell could
achieve alone.

Five behavior modes:

- **Consensus** — cells vote on spatial classifications and converge toward
  agreement; faction boundaries emerge at opinion frontiers.
- **Balance** — cells redistribute energy to minimize variance; stubborn
  hotspots are identified where balancing stalls.
- **Alert** — anomaly cells broadcast alert signals that propagate through
  the neighbor network with decay; relay bottleneck cells are identified.
- **Territory** — weighted cells negotiate influence zones; stable borders
  and contested zones emerge.
- **Pathfind** — stigmergic pheromone signals discover optimal routes
  through the spatial network; highway corridors emerge.

Usage (Python API)::

    from vormap_swarm import SwarmEngine, swarm_simulate, swarm_demo

    # Quick one-liner
    result = swarm_simulate("points.txt", behavior="consensus")
    print(f"Converged: {result.convergence_history[-1]:.1%} in {result.ticks_run} ticks")

    # Detailed API
    engine = SwarmEngine(points=[(0,0),(10,0),(5,8)], behavior="balance")
    result = engine.run()
    engine.to_json("swarm.json")
    engine.to_html("swarm.html")

    # Demo all behaviors
    swarm_demo()

CLI::

    python vormap_swarm.py points.txt --behavior consensus
    python vormap_swarm.py points.txt --behavior balance --max-ticks 200
    python vormap_swarm.py points.txt --json result.json --html dashboard.html
    python vormap_swarm.py --demo
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import random
import textwrap
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vormap_utils import build_distance_adjacency as _build_distance_adjacency

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

CellAgent = collections.namedtuple(
    "CellAgent",
    ["cell_id", "center", "area", "neighbors", "energy", "role", "signals", "memory"],
)

Signal = collections.namedtuple(
    "Signal",
    ["source_id", "signal_type", "strength", "payload", "timestamp", "hops"],
)

SwarmState = collections.namedtuple(
    "SwarmState",
    ["tick", "agents", "active_signals", "global_metrics", "convergence"],
)

SwarmResult = collections.namedtuple(
    "SwarmResult",
    [
        "behavior_type",
        "ticks_run",
        "final_state",
        "convergence_history",
        "emergent_patterns",
        "recommendations",
        "tick_snapshots",
    ],
)

EmergentPattern = collections.namedtuple(
    "EmergentPattern",
    ["pattern_type", "description", "involved_cells", "confidence"],
)

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _dist(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _approx_area(center, neighbors_centers, bounds):
    """Rough Voronoi cell area estimate via Monte-Carlo or bounding box fraction."""
    if not neighbors_centers:
        dx = bounds[2] - bounds[0]
        dy = bounds[3] - bounds[1]
        return dx * dy
    # Use midpoint polygon approximation
    midpoints = []
    for nc in neighbors_centers:
        mx = (center[0] + nc[0]) / 2.0
        my = (center[1] + nc[1]) / 2.0
        midpoints.append((mx, my))
    # Sort by angle from center
    midpoints.sort(key=lambda p: math.atan2(p[1] - center[1], p[0] - center[0]))
    # Shoelace area
    area = 0.0
    n = len(midpoints)
    for i in range(n):
        j = (i + 1) % n
        area += midpoints[i][0] * midpoints[j][1]
        area -= midpoints[j][0] * midpoints[i][1]
    return abs(area) / 2.0


def _build_adjacency(points, k_factor=2.0):
    """Build neighbor graph: two points are neighbors if distance < k_factor * avg_spacing.

    Delegates to :func:`vormap_utils.build_distance_adjacency`.
    """
    return _build_distance_adjacency(points, k_factor)


def _bounds(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


# ---------------------------------------------------------------------------
# Swarm Engine
# ---------------------------------------------------------------------------

BEHAVIORS = ("consensus", "balance", "alert", "territory", "pathfind")


class SwarmEngine:
    """Autonomous swarm intelligence simulator for Voronoi cell agents."""

    def __init__(
        self,
        points=None,
        behavior="consensus",
        max_ticks=100,
        signal_decay=0.85,
        seed=42,
    ):
        if points is None:
            points = []
        if behavior not in BEHAVIORS:
            raise ValueError(
                f"Unknown behavior {behavior!r}; choose from {BEHAVIORS}"
            )
        self.points = list(points)
        self.behavior = behavior
        self.max_ticks = max(1, int(max_ticks))
        self.signal_decay = float(signal_decay)
        self.rng = random.Random(seed)
        self._result = None

    # -- initialization -----------------------------------------------------

    def _init_agents(self):
        pts = self.points
        n = len(pts)
        if n == 0:
            return [], {}

        adj = _build_adjacency(pts)
        bds = _bounds(pts)
        agents = []
        for i in range(n):
            nb_centers = [pts[j] for j in adj[i]]
            area = _approx_area(pts[i], nb_centers, bds)
            energy = self.rng.uniform(0, 100)
            if self.behavior == "consensus":
                role = self.rng.randint(0, 4)
            elif self.behavior == "territory":
                role = self.rng.uniform(1, 10)  # weight
            else:
                role = 0
            agents.append(
                CellAgent(
                    cell_id=i,
                    center=pts[i],
                    area=area,
                    neighbors=list(adj[i]),
                    energy=energy,
                    role=role,
                    signals=[],
                    memory={},
                )
            )
        return agents, adj

    # -- behavior tick functions --------------------------------------------

    def _tick_consensus(self, agents, tick):
        """Each cell adopts majority opinion of neighbors."""
        new_agents = []
        for ag in agents:
            if not ag.neighbors:
                new_agents.append(ag)
                continue
            opinions = [agents[nb].role for nb in ag.neighbors]
            opinions.append(ag.role)
            counts = collections.Counter(opinions)
            majority = counts.most_common(1)[0][0]
            new_agents.append(ag._replace(role=majority))
        # Convergence = fraction with most popular opinion
        all_roles = [a.role for a in new_agents]
        if not all_roles:
            return new_agents, 1.0
        most_common_count = collections.Counter(all_roles).most_common(1)[0][1]
        convergence = most_common_count / len(all_roles)
        return new_agents, convergence

    def _tick_balance(self, agents, tick):
        """Cells with above-avg energy transfer to below-avg neighbors."""
        n = len(agents)
        if n == 0:
            return agents, 1.0
        avg_e = sum(a.energy for a in agents) / n
        new_energy = [a.energy for a in agents]
        transfer_rate = 0.2
        for ag in agents:
            if ag.energy > avg_e:
                for nb in ag.neighbors:
                    if agents[nb].energy < avg_e:
                        transfer = (ag.energy - avg_e) * transfer_rate / max(1, len(ag.neighbors))
                        new_energy[ag.cell_id] -= transfer
                        new_energy[nb] += transfer
        new_agents = [ag._replace(energy=new_energy[ag.cell_id]) for ag in agents]
        # Convergence = 1 - normalized variance
        if n <= 1:
            return new_agents, 1.0
        mean_e = sum(new_energy) / n
        var = sum((e - mean_e) ** 2 for e in new_energy) / n
        max_var = 2500  # max theoretical variance for [0,100]
        convergence = max(0.0, 1.0 - var / max_var)
        return new_agents, convergence

    def _tick_alert(self, agents, signals, tick):
        """Propagate alert signals through neighbor network."""
        alerted = set()
        for ag in agents:
            if ag.signals:
                alerted.add(ag.cell_id)

        new_signals = []
        for sig in signals:
            if sig.strength < 0.01:
                continue
            source_agent = agents[sig.source_id]
            for nb in source_agent.neighbors:
                if nb not in alerted:
                    new_sig = Signal(
                        source_id=nb,
                        signal_type="alert",
                        strength=sig.strength * self.signal_decay,
                        payload=sig.payload,
                        timestamp=tick,
                        hops=sig.hops + 1,
                    )
                    new_signals.append(new_sig)
                    alerted.add(nb)

        new_agents = []
        for ag in agents:
            if ag.cell_id in alerted:
                new_sigs = ag.signals + [s for s in new_signals if s.source_id == ag.cell_id]
                new_agents.append(ag._replace(signals=new_sigs))
            else:
                new_agents.append(ag)

        n = len(agents)
        convergence = len(alerted) / n if n > 0 else 1.0
        return new_agents, new_signals, convergence

    def _tick_territory(self, agents, tick):
        """High-weight cells recruit low-weight neighbors."""
        new_agents = list(agents)
        territory = {ag.cell_id: ag.cell_id for ag in agents}
        # Initialize territories from memory or self
        for ag in agents:
            territory[ag.cell_id] = ag.memory.get("owner", ag.cell_id)

        for ag in agents:
            for nb in ag.neighbors:
                nb_ag = agents[nb]
                if ag.role > nb_ag.role * 1.3:
                    # Stronger cell claims neighbor
                    territory[nb] = territory[ag.cell_id]

        new_agents_out = []
        for ag in agents:
            mem = dict(ag.memory)
            mem["owner"] = territory[ag.cell_id]
            new_agents_out.append(ag._replace(memory=mem))

        # Convergence = fraction of cells in largest territory
        owner_counts = collections.Counter(territory.values())
        if not owner_counts:
            return new_agents_out, 1.0
        largest = owner_counts.most_common(1)[0][1]
        convergence = largest / len(agents) if agents else 1.0
        return new_agents_out, convergence

    def _tick_pathfind(self, agents, tick, source_id, target_id):
        """Stigmergic pathfinding: pheromone flows from source toward target."""
        n = len(agents)
        if n == 0:
            return agents, 1.0

        target_center = agents[target_id].center
        pheromone = [ag.memory.get("pheromone", 0.0) for ag in agents]

        # Source emits pheromone
        pheromone[source_id] = max(pheromone[source_id], 1.0)

        # Propagate: each cell shares pheromone with neighbors closer to target
        new_pheromone = list(pheromone)
        for ag in agents:
            if pheromone[ag.cell_id] > 0.01:
                my_dist = _dist(ag.center, target_center)
                for nb in ag.neighbors:
                    nb_dist = _dist(agents[nb].center, target_center)
                    if nb_dist < my_dist:
                        spread = pheromone[ag.cell_id] * 0.5 * self.signal_decay
                        new_pheromone[nb] = max(new_pheromone[nb], spread)

        # Decay all pheromone
        new_pheromone = [p * self.signal_decay for p in new_pheromone]

        new_agents = []
        for ag in agents:
            mem = dict(ag.memory)
            mem["pheromone"] = new_pheromone[ag.cell_id]
            new_agents.append(ag._replace(memory=mem))

        # Convergence = pheromone at target / pheromone at source
        src_p = max(new_pheromone[source_id], 0.001)
        tgt_p = new_pheromone[target_id]
        convergence = min(1.0, tgt_p / src_p) if src_p > 0 else 0.0
        return new_agents, convergence

    # -- main run -----------------------------------------------------------

    def run(self):
        """Execute swarm simulation and return SwarmResult."""
        if not self.points:
            empty_state = SwarmState(0, [], [], {}, 1.0)
            self._result = SwarmResult(
                self.behavior, 0, empty_state, [1.0], [], [], []
            )
            return self._result

        agents, adj = self._init_agents()
        convergence_history = []
        tick_snapshots = []
        signals = []

        # For alert mode: inject anomalies
        if self.behavior == "alert":
            n = len(agents)
            anomaly_count = max(1, int(n * 0.05))
            anomaly_ids = self.rng.sample(range(n), min(anomaly_count, n))
            initial_signals = []
            for aid in anomaly_ids:
                sig = Signal(aid, "alert", 1.0, {"anomaly": True}, 0, 0)
                initial_signals.append(sig)
                agents[aid] = agents[aid]._replace(
                    signals=[sig], memory={"anomaly": True}
                )
            signals = initial_signals

        # For pathfind: pick source/target
        source_id = target_id = 0
        if self.behavior == "pathfind" and len(agents) >= 2:
            source_id = 0
            target_id = len(agents) - 1

        for tick in range(1, self.max_ticks + 1):
            if self.behavior == "consensus":
                agents, conv = self._tick_consensus(agents, tick)
            elif self.behavior == "balance":
                agents, conv = self._tick_balance(agents, tick)
            elif self.behavior == "alert":
                agents, signals, conv = self._tick_alert(agents, signals, tick)
            elif self.behavior == "territory":
                agents, conv = self._tick_territory(agents, tick)
            elif self.behavior == "pathfind":
                agents, conv = self._tick_pathfind(agents, tick, source_id, target_id)
            else:
                conv = 1.0

            convergence_history.append(conv)

            # Save snapshot every 5 ticks or first/last
            if tick == 1 or tick % 5 == 0 or tick == self.max_ticks:
                snapshot = SwarmState(
                    tick=tick,
                    agents=[self._agent_summary(a) for a in agents],
                    active_signals=len(signals) if self.behavior == "alert" else 0,
                    global_metrics=self._global_metrics(agents),
                    convergence=conv,
                )
                tick_snapshots.append(snapshot)

            # Early stop if converged
            if conv >= 0.95 and tick >= 3:
                break

        # Detect emergent patterns
        patterns = self._detect_patterns(agents)

        # Generate recommendations
        recommendations = self._generate_recommendations(agents, convergence_history, patterns)

        final_state = SwarmState(
            tick=tick,
            agents=[self._agent_summary(a) for a in agents],
            active_signals=len(signals) if self.behavior == "alert" else 0,
            global_metrics=self._global_metrics(agents),
            convergence=convergence_history[-1] if convergence_history else 1.0,
        )

        self._result = SwarmResult(
            behavior_type=self.behavior,
            ticks_run=tick,
            final_state=final_state,
            convergence_history=convergence_history,
            emergent_patterns=patterns,
            recommendations=recommendations,
            tick_snapshots=tick_snapshots,
        )
        return self._result

    # -- analysis helpers ---------------------------------------------------

    def _agent_summary(self, ag):
        """Lightweight agent summary for snapshots."""
        return {
            "id": ag.cell_id,
            "center": ag.center,
            "energy": round(ag.energy, 2),
            "role": ag.role,
            "signal_count": len(ag.signals),
            "memory_keys": list(ag.memory.keys()),
        }

    def _global_metrics(self, agents):
        if not agents:
            return {}
        energies = [a.energy for a in agents]
        n = len(energies)
        mean_e = sum(energies) / n
        var_e = sum((e - mean_e) ** 2 for e in energies) / n
        roles = [a.role for a in agents]
        role_dist = dict(collections.Counter(roles))
        return {
            "agent_count": n,
            "mean_energy": round(mean_e, 2),
            "energy_variance": round(var_e, 2),
            "role_distribution": {str(k): v for k, v in role_dist.items()},
            "total_signals": sum(len(a.signals) for a in agents),
        }

    def _detect_patterns(self, agents):
        """Detect emergent patterns based on behavior type."""
        patterns = []
        if not agents:
            return patterns

        if self.behavior == "consensus":
            # Detect faction boundaries: cells whose neighbors disagree
            boundary_cells = []
            for ag in agents:
                neighbor_roles = set(agents[nb].role for nb in ag.neighbors)
                if len(neighbor_roles) > 1:
                    boundary_cells.append(ag.cell_id)
            if boundary_cells:
                patterns.append(EmergentPattern(
                    "faction_boundary",
                    f"{len(boundary_cells)} cells at opinion frontiers",
                    boundary_cells,
                    min(1.0, len(boundary_cells) / max(1, len(agents))),
                ))
            # Detect dominant faction
            roles = [a.role for a in agents]
            counter = collections.Counter(roles)
            dominant, count = counter.most_common(1)[0]
            if count > len(agents) * 0.6:
                patterns.append(EmergentPattern(
                    "dominant_faction",
                    f"Opinion {dominant} dominates with {count}/{len(agents)} cells",
                    [a.cell_id for a in agents if a.role == dominant],
                    count / len(agents),
                ))

        elif self.behavior == "balance":
            # Detect stubborn hotspots: cells still far from mean
            energies = [a.energy for a in agents]
            mean_e = sum(energies) / len(energies)
            std_e = math.sqrt(sum((e - mean_e) ** 2 for e in energies) / len(energies))
            hotspots = [a.cell_id for a in agents if abs(a.energy - mean_e) > 2 * max(std_e, 0.1)]
            if hotspots:
                patterns.append(EmergentPattern(
                    "stubborn_hotspot",
                    f"{len(hotspots)} cells resist energy balancing",
                    hotspots,
                    min(1.0, len(hotspots) / max(1, len(agents))),
                ))

        elif self.behavior == "alert":
            # Detect relay bottlenecks: cells with most signals relayed
            signal_counts = [(a.cell_id, len(a.signals)) for a in agents]
            signal_counts.sort(key=lambda x: -x[1])
            if signal_counts and signal_counts[0][1] > 0:
                top_relays = [cid for cid, cnt in signal_counts[:max(1, len(agents) // 10)]
                              if cnt > 0]
                patterns.append(EmergentPattern(
                    "relay_bottleneck",
                    f"{len(top_relays)} cells are critical signal relays",
                    top_relays,
                    0.8,
                ))
            # Detect unreached cells
            unreached = [a.cell_id for a in agents if not a.signals]
            if unreached:
                patterns.append(EmergentPattern(
                    "alert_shadow",
                    f"{len(unreached)} cells never received alert signals",
                    unreached,
                    len(unreached) / len(agents),
                ))

        elif self.behavior == "territory":
            # Detect contested zones: cells whose neighbors have different owners
            contested = []
            for ag in agents:
                my_owner = ag.memory.get("owner", ag.cell_id)
                for nb in ag.neighbors:
                    nb_owner = agents[nb].memory.get("owner", nb)
                    if nb_owner != my_owner:
                        contested.append(ag.cell_id)
                        break
            if contested:
                patterns.append(EmergentPattern(
                    "contested_zone",
                    f"{len(contested)} cells at territory borders",
                    contested,
                    min(1.0, len(contested) / max(1, len(agents))),
                ))

        elif self.behavior == "pathfind":
            # Detect highway corridors: cells with highest pheromone
            pheromones = [(a.cell_id, a.memory.get("pheromone", 0)) for a in agents]
            pheromones.sort(key=lambda x: -x[1])
            threshold = 0.1
            highway = [cid for cid, p in pheromones if p > threshold]
            if highway:
                patterns.append(EmergentPattern(
                    "highway_corridor",
                    f"{len(highway)} cells form pheromone highway",
                    highway,
                    min(1.0, len(highway) / max(1, len(agents))),
                ))

        return patterns

    def _generate_recommendations(self, agents, history, patterns):
        """Generate actionable recommendations."""
        recs = []
        if not agents:
            return ["Add more data points for meaningful swarm analysis."]

        if self.behavior == "consensus":
            if history and history[-1] < 0.5:
                recs.append("Low consensus — consider weighted voting or leader nodes.")
            for p in patterns:
                if p.pattern_type == "faction_boundary":
                    recs.append(f"Faction boundary at {len(p.involved_cells)} cells — "
                                "investigate spatial feature causing opinion divide.")

        elif self.behavior == "balance":
            for p in patterns:
                if p.pattern_type == "stubborn_hotspot":
                    recs.append(f"{len(p.involved_cells)} stubborn hotspots — "
                                "increase transfer rate or add relay nodes.")
            if history and history[-1] > 0.95:
                recs.append("Excellent energy balance achieved.")

        elif self.behavior == "alert":
            for p in patterns:
                if p.pattern_type == "alert_shadow":
                    recs.append(f"{len(p.involved_cells)} unreached cells — "
                                "add relay infrastructure in shadowed regions.")
                if p.pattern_type == "relay_bottleneck":
                    recs.append(f"Critical bottleneck relays found — "
                                "add redundant paths to avoid single-point failure.")

        elif self.behavior == "territory":
            for p in patterns:
                if p.pattern_type == "contested_zone":
                    recs.append(f"{len(p.involved_cells)} contested border cells — "
                                "consider mediation or buffer zones.")

        elif self.behavior == "pathfind":
            for p in patterns:
                if p.pattern_type == "highway_corridor":
                    recs.append(f"Highway corridor: {len(p.involved_cells)} cells — "
                                "optimize these cells as primary transit route.")

        if not recs:
            recs.append("Swarm simulation completed successfully.")
        return recs

    # -- export -------------------------------------------------------------

    def to_json(self, path):
        """Export result to JSON."""
        if self._result is None:
            raise RuntimeError("Run the simulation first via .run()")
        data = {
            "behavior_type": self._result.behavior_type,
            "ticks_run": self._result.ticks_run,
            "convergence_history": [round(c, 4) for c in self._result.convergence_history],
            "emergent_patterns": [
                {
                    "pattern_type": p.pattern_type,
                    "description": p.description,
                    "involved_cells": p.involved_cells,
                    "confidence": round(p.confidence, 4),
                }
                for p in self._result.emergent_patterns
            ],
            "recommendations": self._result.recommendations,
            "final_state": {
                "tick": self._result.final_state.tick,
                "convergence": round(self._result.final_state.convergence, 4),
                "global_metrics": self._result.final_state.global_metrics,
                "agents": self._result.final_state.agents,
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def to_html(self, path):
        """Export interactive HTML dashboard."""
        if self._result is None:
            raise RuntimeError("Run the simulation first via .run()")
        r = self._result
        snapshots_json = json.dumps([
            {
                "tick": s.tick,
                "convergence": round(s.convergence, 4),
                "agents": s.agents,
                "metrics": s.global_metrics,
            }
            for s in r.tick_snapshots
        ])
        patterns_json = json.dumps([
            {"type": p.pattern_type, "desc": p.description,
             "cells": p.involved_cells, "conf": round(p.confidence, 2)}
            for p in r.emergent_patterns
        ])
        recs_json = json.dumps(r.recommendations)
        points_json = json.dumps([list(p) for p in self.points])

        html = _HTML_TEMPLATE.replace("{{BEHAVIOR}}", r.behavior_type)
        html = html.replace("{{TICKS}}", str(r.ticks_run))
        html = html.replace("{{CONVERGENCE}}", f"{r.convergence_history[-1]:.1%}" if r.convergence_history else "N/A")
        html = html.replace("{{SNAPSHOTS}}", snapshots_json)
        html = html.replace("{{PATTERNS}}", patterns_json)
        html = html.replace("{{RECS}}", recs_json)
        html = html.replace("{{POINTS}}", points_json)
        html = html.replace("{{CONV_HISTORY}}", json.dumps([round(c, 4) for c in r.convergence_history]))

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = textwrap.dedent("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Swarm Intelligence — {{BEHAVIOR}}</title>
<style>
:root{--bg:#1a1a2e;--fg:#e0e0e0;--accent:#00d4ff;--panel:#16213e;--border:#0f3460}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:var(--bg);color:var(--fg);padding:20px}
h1{color:var(--accent);margin-bottom:8px;font-size:1.6em}
.subtitle{color:#888;margin-bottom:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px}
.panel h2{color:var(--accent);font-size:1.1em;margin-bottom:10px}
.stat{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #ffffff10}
.stat-label{color:#999}.stat-value{font-weight:bold}
svg{width:100%;height:300px}
.controls{display:flex;gap:10px;align-items:center;margin:10px 0}
.controls button{background:var(--accent);color:#000;border:none;border-radius:4px;
  padding:6px 14px;cursor:pointer;font-weight:bold}
.controls button:hover{opacity:0.8}
input[type=range]{flex:1}
.pattern{padding:8px;margin:4px 0;background:#ffffff08;border-radius:4px;border-left:3px solid var(--accent)}
.rec{padding:6px 0;border-bottom:1px solid #ffffff08}
.theme-toggle{position:fixed;top:10px;right:10px;cursor:pointer;background:var(--panel);
  border:1px solid var(--border);border-radius:50%;width:36px;height:36px;display:flex;
  align-items:center;justify-content:center;font-size:18px}
.light{--bg:#f5f5f5;--fg:#222;--panel:#fff;--border:#ddd;--accent:#0066cc}
</style>
</head>
<body>
<div class="theme-toggle" onclick="document.body.classList.toggle('light')">🌓</div>
<h1>🐝 Swarm Intelligence Engine</h1>
<p class="subtitle">Behavior: <strong>{{BEHAVIOR}}</strong> — {{TICKS}} ticks — Convergence: {{CONVERGENCE}}</p>

<div class="grid">
  <div class="panel">
    <h2>📊 Spatial Map</h2>
    <svg id="mapSvg" viewBox="0 0 500 500"></svg>
    <div class="controls">
      <button id="playBtn" onclick="togglePlay()">▶ Play</button>
      <input type="range" id="tickSlider" min="0" max="0" value="0" oninput="showTick(+this.value)">
      <span id="tickLabel">Tick 0</span>
    </div>
  </div>
  <div class="panel">
    <h2>📈 Convergence</h2>
    <svg id="convSvg" viewBox="0 0 500 300"></svg>
  </div>
  <div class="panel">
    <h2>🔍 Emergent Patterns</h2>
    <div id="patterns"></div>
  </div>
  <div class="panel">
    <h2>💡 Recommendations</h2>
    <div id="recs"></div>
  </div>
</div>

<script>
const snapshots={{SNAPSHOTS}};
const patterns={{PATTERNS}};
const recs={{RECS}};
const points={{POINTS}};
const convHistory={{CONV_HISTORY}};
const behavior="{{BEHAVIOR}}";

// Colors for roles/states
const COLORS=["#ff6b6b","#4ecdc4","#45b7d1","#96ceb4","#ffd93d","#ff8a5c","#a8e6cf","#dda0dd"];

function drawMap(snapIdx){
  const svg=document.getElementById("mapSvg");
  svg.innerHTML="";
  if(!points.length)return;
  const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
  const mnx=Math.min(...xs),mxx=Math.max(...xs),mny=Math.min(...ys),mxy=Math.max(...ys);
  const dx=mxx-mnx||1,dy=mxy-mny||1,pad=20,scale=Math.min((500-2*pad)/dx,(500-2*pad)/dy);
  const snap=snapshots[snapIdx]||snapshots[0];
  if(!snap)return;
  const agents=snap.agents||[];
  agents.forEach(function(a,i){
    const px=pad+(points[i][0]-mnx)*scale;
    const py=pad+(points[i][1]-mny)*scale;
    let col=COLORS[0];
    if(behavior==="consensus")col=COLORS[Math.floor(a.role)%COLORS.length];
    else if(behavior==="balance"){const t=Math.min(a.energy/100,1);col="rgb("+Math.floor(255*t)+","+Math.floor(100+155*(1-t))+",100)";}
    else if(behavior==="alert")col=a.signal_count>0?"#ff6b6b":"#4ecdc4";
    else if(behavior==="territory")col=COLORS[(a.memory_keys.indexOf("owner")>=0?Math.floor(a.role)%COLORS.length:i%COLORS.length)];
    else if(behavior==="pathfind")col=a.memory_keys.indexOf("pheromone")>=0?"#ffd93d":"#4ecdc4";
    const c=document.createElementNS("http://www.w3.org/2000/svg","circle");
    c.setAttribute("cx",px);c.setAttribute("cy",py);c.setAttribute("r",6);
    c.setAttribute("fill",col);c.setAttribute("opacity","0.85");
    const title=document.createElementNS("http://www.w3.org/2000/svg","title");
    title.textContent="Cell "+a.id+" | Energy: "+a.energy+" | Role: "+a.role;
    c.appendChild(title);svg.appendChild(c);
  });
}

function drawConv(){
  const svg=document.getElementById("convSvg");svg.innerHTML="";
  if(!convHistory.length)return;
  const n=convHistory.length,w=500,h=300,pad=40;
  // Axes
  const ax=document.createElementNS("http://www.w3.org/2000/svg","line");
  ax.setAttribute("x1",pad);ax.setAttribute("y1",h-pad);ax.setAttribute("x2",w-10);ax.setAttribute("y2",h-pad);
  ax.setAttribute("stroke","#666");svg.appendChild(ax);
  const ay=document.createElementNS("http://www.w3.org/2000/svg","line");
  ay.setAttribute("x1",pad);ay.setAttribute("y1",10);ay.setAttribute("x2",pad);ay.setAttribute("y2",h-pad);
  ay.setAttribute("stroke","#666");svg.appendChild(ay);
  // Line
  let pts=[];
  for(let i=0;i<n;i++){
    const x=pad+(w-pad-10)*i/(Math.max(1,n-1));
    const y=(h-pad)-convHistory[i]*(h-pad-10);
    pts.push(x+","+y);
  }
  const pl=document.createElementNS("http://www.w3.org/2000/svg","polyline");
  pl.setAttribute("points",pts.join(" "));pl.setAttribute("fill","none");
  pl.setAttribute("stroke","#00d4ff");pl.setAttribute("stroke-width","2");
  svg.appendChild(pl);
  // Labels
  const lbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  lbl.setAttribute("x",w/2);lbl.setAttribute("y",h-5);lbl.setAttribute("text-anchor","middle");
  lbl.setAttribute("fill","#888");lbl.setAttribute("font-size","12");lbl.textContent="Tick";
  svg.appendChild(lbl);
}

function showTick(idx){
  document.getElementById("tickLabel").textContent="Tick "+(snapshots[idx]?snapshots[idx].tick:0);
  drawMap(idx);
}

let playing=false,playTimer=null;
function togglePlay(){
  playing=!playing;
  document.getElementById("playBtn").textContent=playing?"⏸ Pause":"▶ Play";
  if(playing){
    playTimer=setInterval(function(){
      const s=document.getElementById("tickSlider");
      let v=+s.value+1;if(v>=snapshots.length)v=0;
      s.value=v;showTick(v);
    },400);
  }else{clearInterval(playTimer);}
}

// Init
document.getElementById("tickSlider").max=Math.max(0,snapshots.length-1);
drawMap(0);drawConv();

const pd=document.getElementById("patterns");
patterns.forEach(function(p){
  pd.innerHTML+='<div class="pattern"><strong>'+p.type+'</strong> ('+Math.round(p.conf*100)+'%)<br>'+p.desc+'</div>';
});

const rd=document.getElementById("recs");
recs.forEach(function(r){rd.innerHTML+='<div class="rec">• '+r+'</div>';});
</script>
</body>
</html>
""")


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def _load_points(path):
    pts = []
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
    return pts


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def swarm_simulate(points_or_file, behavior="consensus", **kwargs):
    """Run a swarm simulation on points (list or file path).

    Returns a ``SwarmResult`` named tuple.
    """
    if isinstance(points_or_file, str):
        pts = _load_points(points_or_file)
    else:
        pts = list(points_or_file)
    engine = SwarmEngine(pts, behavior=behavior, **kwargs)
    return engine.run()


def swarm_demo():
    """Run all five behaviors on synthetic data and print summaries."""
    rng = random.Random(99)
    pts = [(rng.uniform(0, 500), rng.uniform(0, 500)) for _ in range(80)]

    print("=" * 60)
    print("  SPATIAL SWARM INTELLIGENCE — DEMO")
    print("=" * 60)

    for beh in BEHAVIORS:
        engine = SwarmEngine(pts, behavior=beh, max_ticks=60, seed=42)
        result = engine.run()
        final_conv = result.convergence_history[-1] if result.convergence_history else 0
        print(f"\n  [{beh.upper()}]")
        print(f"    Ticks: {result.ticks_run}")
        print(f"    Convergence: {final_conv:.1%}")
        print(f"    Patterns: {len(result.emergent_patterns)}")
        for p in result.emergent_patterns:
            print(f"      • {p.pattern_type}: {p.description}")
        print(f"    Recommendations:")
        for r in result.recommendations:
            print(f"      → {r}")

    print(f"\n{'=' * 60}\n")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_cli():
    p = argparse.ArgumentParser(
        description="Spatial Swarm Intelligence Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", nargs="?", help="Point data file (one 'x y' per line)")
    p.add_argument(
        "--behavior", "-b", choices=BEHAVIORS, default="consensus",
        help="Swarm behavior mode (default: consensus)",
    )
    p.add_argument("--max-ticks", type=int, default=100, help="Maximum ticks (default 100)")
    p.add_argument("--signal-decay", type=float, default=0.85, help="Signal decay rate (default 0.85)")
    p.add_argument("--seed", type=int, default=42, help="Random seed (default 42)")
    p.add_argument("--json", dest="json_out", help="Export JSON report")
    p.add_argument("--html", dest="html_out", help="Export HTML dashboard")
    p.add_argument("--demo", action="store_true", help="Run demo with all behaviors")
    return p


def main(argv=None):
    parser = _build_cli()
    args = parser.parse_args(argv)

    if args.demo:
        swarm_demo()
        return

    if not args.input:
        parser.error("Input file required (or use --demo)")

    pts = _load_points(args.input)
    engine = SwarmEngine(
        pts,
        behavior=args.behavior,
        max_ticks=args.max_ticks,
        signal_decay=args.signal_decay,
        seed=args.seed,
    )
    result = engine.run()

    final_conv = result.convergence_history[-1] if result.convergence_history else 0
    print(f"\n{'=' * 60}")
    print(f"  SWARM INTELLIGENCE REPORT — {args.behavior.upper()}")
    print(f"{'=' * 60}")
    print(f"  Ticks:       {result.ticks_run}")
    print(f"  Convergence: {final_conv:.1%}")
    print(f"  Patterns:    {len(result.emergent_patterns)}")
    for p in result.emergent_patterns:
        print(f"    [{p.pattern_type}] {p.description} (confidence {p.confidence:.0%})")
    print(f"  Recommendations:")
    for r in result.recommendations:
        print(f"    • {r}")
    print(f"{'=' * 60}\n")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON report saved to {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML dashboard saved to {args.html_out}")


if __name__ == "__main__":
    main()
