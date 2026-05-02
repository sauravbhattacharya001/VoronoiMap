#!/usr/bin/env python3
"""Spatial Nervous System Engine — autonomous neural signal propagation.

Models Voronoi cells as neurons in a spatial nervous system with
signal propagation, reflex arcs, rhythm detection, and Hebbian
plasticity.  Enables analysis of how information flows through
spatial tessellations, treating geometry as neural architecture.

Seven analysis engines:

- **Neuron Classifier** — Assigns each cell one of five neuron types
  (sensory / motor / interneuron / inhibitory / excitatory) based on
  spatial position, connectivity, and area.
- **Synapse Mapper** — Builds weighted, directed synaptic connections
  between neighbouring cells with excitatory/inhibitory classification
  and plasticity scores.
- **Signal Propagator** — Leaky integrate-and-fire simulation: inject
  a stimulus at any cell, propagate activation across timesteps, and
  record spike trains.
- **Reflex Arc Detector** — BFS from every sensory neuron to the
  nearest motor neuron, ranking paths by latency and identifying
  critical relay neurons.
- **Neural Rhythm Analyzer** — Runs multiple stimulus rounds and
  classifies oscillatory firing patterns as alpha, beta, or gamma.
- **Plasticity Engine** — Hebbian learning: strengthen synapses
  between co-active pairs, weaken rarely co-active ones.
- **Autonomous Insight Generator** — Natural-language insights,
  connectivity analysis, and a composite health score 0-100.

Usage (Python API)::

    from vormap_nervous import NervousSystemEngine, nervous_analyze, nervous_demo

    # Quick one-liner
    result = nervous_analyze("points.txt")
    print(f"Health: {result.health_score:.1f}/100")

    # Detailed API
    engine = NervousSystemEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze()
    engine.to_html("nervous.html")

    # Demo
    nervous_demo()

CLI::

    python vormap_nervous.py points.txt
    python vormap_nervous.py points.txt --json out.json --html dash.html
    python vormap_nervous.py points.txt --stimulate 0
    python vormap_nervous.py --demo
"""

from __future__ import annotations

import argparse
import collections
import html as html_mod
import json
import math
import os
import random
import sys
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class NeuronCell:
    """A single spatial neuron (Voronoi cell)."""
    cell_id: int
    x: float
    y: float
    neuron_type: str = "interneuron"
    resting_potential: float = 0.0
    threshold: float = 1.0
    neighbors: List[int] = field(default_factory=list)
    area: float = 1.0


@dataclass
class Synapse:
    """Directed synaptic connection between two neurons."""
    source: int
    target: int
    strength: float = 1.0
    synapse_type: str = "excitatory"
    plasticity: float = 0.5


@dataclass
class SpikeEvent:
    """A single neuron firing event."""
    cell_id: int
    timestep: int
    potential: float = 0.0


@dataclass
class ReflexArc:
    """Shortest sensory → motor pathway."""
    sensory_id: int
    motor_id: int
    path: List[int] = field(default_factory=list)
    latency: int = 0
    relay_neurons: List[int] = field(default_factory=list)


@dataclass
class NervousSystemResult:
    """Full nervous-system analysis result."""
    neurons: List[NeuronCell] = field(default_factory=list)
    synapses: List[Synapse] = field(default_factory=list)
    spike_trains: List[List[SpikeEvent]] = field(default_factory=list)
    reflex_arcs: List[ReflexArc] = field(default_factory=list)
    rhythms: Dict[str, int] = field(default_factory=dict)
    plasticity_changes: List[Tuple[int, int, float]] = field(default_factory=list)
    health_score: float = 0.0
    insights: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _load_points(path: str) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    with open(path, "r") as fh:
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


def _knn_adjacency(
    points: List[Tuple[float, float]], k: int = 6
) -> Dict[int, List[int]]:
    n = len(points)
    k = min(k, n - 1)
    adj: Dict[int, List[int]] = {i: [] for i in range(n)}
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append((j, _euclidean(points[i], points[j])))
        dists.sort(key=lambda t: t[1])
        for j, _ in dists[:k]:
            if j not in adj[i]:
                adj[i].append(j)
            if i not in adj[j]:
                adj[j].append(i)
    return adj


def _voronoi_areas(
    points: List[Tuple[float, float]], adj: Dict[int, List[int]]
) -> List[float]:
    n = len(points)
    areas: List[float] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if len(nbrs) < 3:
            areas.append(1.0)
            continue
        mids = []
        for j in nbrs:
            mx = (points[i][0] + points[j][0]) / 2.0
            my = (points[i][1] + points[j][1]) / 2.0
            mids.append((mx, my))
        cx = sum(m[0] for m in mids) / len(mids)
        cy = sum(m[1] for m in mids) / len(mids)
        mids.sort(key=lambda m: math.atan2(m[1] - cy, m[0] - cx))
        area = 0.0
        for idx in range(len(mids)):
            x1, y1 = mids[idx]
            x2, y2 = mids[(idx + 1) % len(mids)]
            area += x1 * y2 - x2 * y1
        areas.append(abs(area) / 2.0)
    return areas


def _centroid(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    if not points:
        return (0.0, 0.0)
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return (cx, cy)


def _bounding_box(points: List[Tuple[float, float]]):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class NervousSystemEngine:
    """Spatial Nervous System Engine."""

    def __init__(
        self,
        points: Optional[List[Tuple[float, float]]] = None,
        path: Optional[str] = None,
        stimulate: Optional[int] = None,
        k: int = 6,
        propagation_steps: int = 20,
        stimulus_rounds: int = 5,
    ):
        if path and not points:
            points = _load_points(path)
        if not points or len(points) < 1:
            raise ValueError("Need at least 1 point")
        self.points = points
        self.n = len(points)
        self.k = k
        self.stimulate_cell = stimulate
        self.propagation_steps = propagation_steps
        self.stimulus_rounds = stimulus_rounds
        self._result: Optional[NervousSystemResult] = None

    def analyze(self) -> NervousSystemResult:
        adj = _knn_adjacency(self.points, self.k)
        areas = _voronoi_areas(self.points, adj)

        # 1. Neuron classification
        neurons = self._classify_neurons(adj, areas)

        # 2. Synapse mapping
        synapses = self._map_synapses(neurons, adj)

        # 3. Signal propagation
        spike_trains = self._propagate_signals(neurons, synapses, adj)

        # 4. Reflex arc detection
        reflex_arcs = self._detect_reflex_arcs(neurons, adj)

        # 5. Rhythm analysis
        rhythms = self._analyze_rhythms(spike_trains)

        # 6. Plasticity
        plasticity_changes = self._apply_plasticity(synapses, spike_trains)

        # 7. Insights
        health_score, insights = self._generate_insights(
            neurons, synapses, spike_trains, reflex_arcs, rhythms
        )

        self._result = NervousSystemResult(
            neurons=neurons,
            synapses=synapses,
            spike_trains=spike_trains,
            reflex_arcs=reflex_arcs,
            rhythms=rhythms,
            plasticity_changes=plasticity_changes,
            health_score=health_score,
            insights=insights,
        )
        return self._result

    # -- Engine 1: Neuron Classifier --

    def _classify_neurons(
        self, adj: Dict[int, List[int]], areas: List[float]
    ) -> List[NeuronCell]:
        neurons: List[NeuronCell] = []
        center = _centroid(self.points)
        max_dist = max(
            (_euclidean(self.points[i], center) for i in range(self.n)),
            default=1.0,
        )
        if max_dist < 1e-12:
            max_dist = 1.0

        avg_neighbors = sum(len(adj[i]) for i in range(self.n)) / max(self.n, 1)
        avg_area = sum(areas) / max(self.n, 1)

        for i in range(self.n):
            dist_to_center = _euclidean(self.points[i], center) / max_dist
            n_neighbors = len(adj.get(i, []))
            cell_area = areas[i]

            # Classification logic
            if dist_to_center > 0.7:
                ntype = "sensory"
            elif n_neighbors >= avg_neighbors * 1.3 and cell_area >= avg_area:
                ntype = "motor"
            elif cell_area < avg_area * 0.6 and n_neighbors >= avg_neighbors:
                ntype = "inhibitory"
            elif cell_area > avg_area * 1.4 and n_neighbors < avg_neighbors * 0.8:
                ntype = "excitatory"
            else:
                ntype = "interneuron"

            threshold = 1.0 if ntype != "inhibitory" else 0.8
            resting = -0.1 if ntype == "inhibitory" else 0.0

            neurons.append(NeuronCell(
                cell_id=i,
                x=self.points[i][0],
                y=self.points[i][1],
                neuron_type=ntype,
                resting_potential=resting,
                threshold=threshold,
                neighbors=list(adj.get(i, [])),
                area=cell_area,
            ))
        return neurons

    # -- Engine 2: Synapse Mapper --

    def _map_synapses(
        self, neurons: List[NeuronCell], adj: Dict[int, List[int]]
    ) -> List[Synapse]:
        synapses: List[Synapse] = []
        for i in range(self.n):
            for j in adj.get(i, []):
                dist = _euclidean(self.points[i], self.points[j])
                base_strength = 1.0 / max(dist, 0.01)
                # Normalize strength to 0-1 range
                base_strength = min(base_strength, 2.0) / 2.0

                stype = "inhibitory" if neurons[i].neuron_type == "inhibitory" else "excitatory"

                # Plasticity: interneurons are most plastic
                plasticity = 0.5
                if neurons[i].neuron_type == "interneuron":
                    plasticity = 0.8
                elif neurons[i].neuron_type in ("sensory", "motor"):
                    plasticity = 0.3

                synapses.append(Synapse(
                    source=i,
                    target=j,
                    strength=base_strength,
                    synapse_type=stype,
                    plasticity=plasticity,
                ))
        return synapses

    # -- Engine 3: Signal Propagator --

    def _propagate_signals(
        self,
        neurons: List[NeuronCell],
        synapses: List[Synapse],
        adj: Dict[int, List[int]],
    ) -> List[List[SpikeEvent]]:
        # Build synapse lookup: source -> [(target, strength, type)]
        syn_map: Dict[int, List[Tuple[int, float, str]]] = {
            i: [] for i in range(self.n)
        }
        for s in synapses:
            syn_map[s.source].append((s.target, s.strength, s.synapse_type))

        all_trains: List[List[SpikeEvent]] = []

        # Determine stimulus cells
        if self.stimulate_cell is not None and 0 <= self.stimulate_cell < self.n:
            stim_cells = [self.stimulate_cell]
        else:
            # Stimulate from sensory neurons, or first cell
            sensory = [n.cell_id for n in neurons if n.neuron_type == "sensory"]
            stim_cells = sensory[:self.stimulus_rounds] if sensory else [0]

        for stim in stim_cells:
            train = self._run_propagation(stim, neurons, syn_map)
            all_trains.append(train)

        return all_trains

    def _run_propagation(
        self,
        stim_cell: int,
        neurons: List[NeuronCell],
        syn_map: Dict[int, List[Tuple[int, float, str]]],
    ) -> List[SpikeEvent]:
        potentials = [n.resting_potential for n in neurons]
        refractory = [0] * self.n  # refractory period counter
        train: List[SpikeEvent] = []

        # Inject stimulus
        potentials[stim_cell] = 2.0
        train.append(SpikeEvent(cell_id=stim_cell, timestep=0, potential=2.0))

        for t in range(1, self.propagation_steps + 1):
            new_potentials = list(potentials)
            fired = set()

            for i in range(self.n):
                if refractory[i] > 0:
                    refractory[i] -= 1
                    new_potentials[i] = neurons[i].resting_potential
                    continue

                if potentials[i] >= neurons[i].threshold:
                    fired.add(i)
                    train.append(SpikeEvent(cell_id=i, timestep=t, potential=potentials[i]))
                    new_potentials[i] = neurons[i].resting_potential
                    refractory[i] = 2  # refractory period

                    # Propagate to targets
                    for tgt, strength, stype in syn_map.get(i, []):
                        if refractory[tgt] <= 0:
                            delta = strength if stype == "excitatory" else -strength
                            new_potentials[tgt] += delta
                else:
                    # Leak toward resting
                    leak = 0.1
                    diff = potentials[i] - neurons[i].resting_potential
                    new_potentials[i] = neurons[i].resting_potential + diff * (1 - leak)

            potentials = new_potentials

        return train

    # -- Engine 4: Reflex Arc Detector --

    def _detect_reflex_arcs(
        self, neurons: List[NeuronCell], adj: Dict[int, List[int]]
    ) -> List[ReflexArc]:
        sensory_ids = [n.cell_id for n in neurons if n.neuron_type == "sensory"]
        motor_ids = set(n.cell_id for n in neurons if n.neuron_type == "motor")

        if not sensory_ids or not motor_ids:
            return []

        arcs: List[ReflexArc] = []
        for s_id in sensory_ids:
            # BFS to find nearest motor neuron
            visited = {s_id: None}
            queue = collections.deque([s_id])
            found_motor = None

            while queue and found_motor is None:
                current = queue.popleft()
                for nb in adj.get(current, []):
                    if nb not in visited:
                        visited[nb] = current
                        if nb in motor_ids:
                            found_motor = nb
                            break
                        queue.append(nb)

            if found_motor is not None:
                # Reconstruct path
                path = []
                node = found_motor
                while node is not None:
                    path.append(node)
                    node = visited[node]
                path.reverse()

                relay = [c for c in path[1:-1]]
                arcs.append(ReflexArc(
                    sensory_id=s_id,
                    motor_id=found_motor,
                    path=path,
                    latency=len(path) - 1,
                    relay_neurons=relay,
                ))

        # Sort by latency
        arcs.sort(key=lambda a: a.latency)
        return arcs

    # -- Engine 5: Neural Rhythm Analyzer --

    def _analyze_rhythms(
        self, spike_trains: List[List[SpikeEvent]]
    ) -> Dict[str, int]:
        rhythms: Dict[str, int] = {"alpha": 0, "beta": 0, "gamma": 0}

        for train in spike_trains:
            if len(train) < 2:
                continue

            # Group spikes by cell
            cell_spikes: Dict[int, List[int]] = {}
            for ev in train:
                cell_spikes.setdefault(ev.cell_id, []).append(ev.timestep)

            for cell_id, times in cell_spikes.items():
                if len(times) < 2:
                    continue
                intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
                avg_interval = sum(intervals) / len(intervals)

                if avg_interval >= 6:
                    rhythms["alpha"] += 1  # slow, regular
                elif avg_interval >= 3:
                    rhythms["beta"] += 1   # moderate
                else:
                    rhythms["gamma"] += 1  # fast, burst

        return rhythms

    # -- Engine 6: Plasticity Engine --

    def _apply_plasticity(
        self,
        synapses: List[Synapse],
        spike_trains: List[List[SpikeEvent]],
    ) -> List[Tuple[int, int, float]]:
        # Build co-activation counts
        coactive: Dict[Tuple[int, int], int] = {}
        active_counts: Dict[int, int] = {}

        for train in spike_trains:
            # Group by timestep
            by_step: Dict[int, set] = {}
            for ev in train:
                by_step.setdefault(ev.timestep, set()).add(ev.cell_id)

            for t, cells in by_step.items():
                for c in cells:
                    active_counts[c] = active_counts.get(c, 0) + 1
                cell_list = list(cells)
                for i in range(len(cell_list)):
                    for j in range(i + 1, len(cell_list)):
                        pair = (min(cell_list[i], cell_list[j]),
                                max(cell_list[i], cell_list[j]))
                        coactive[pair] = coactive.get(pair, 0) + 1

        changes: List[Tuple[int, int, float]] = []
        for syn in synapses:
            pair = (min(syn.source, syn.target), max(syn.source, syn.target))
            co_count = coactive.get(pair, 0)

            if co_count > 0:
                # Hebbian strengthening
                delta = syn.plasticity * 0.05 * co_count
                syn.strength = min(syn.strength + delta, 1.0)
                changes.append((syn.source, syn.target, delta))
            elif active_counts.get(syn.source, 0) > 0:
                # Weakening: source fired but target didn't co-fire
                delta = -syn.plasticity * 0.02
                syn.strength = max(syn.strength + delta, 0.01)
                changes.append((syn.source, syn.target, delta))

        return changes

    # -- Engine 7: Insight Generator --

    def _generate_insights(
        self,
        neurons: List[NeuronCell],
        synapses: List[Synapse],
        spike_trains: List[List[SpikeEvent]],
        reflex_arcs: List[ReflexArc],
        rhythms: Dict[str, int],
    ) -> Tuple[float, List[str]]:
        insights: List[str] = []

        # Type distribution
        type_counts: Dict[str, int] = {}
        for n in neurons:
            type_counts[n.neuron_type] = type_counts.get(n.neuron_type, 0) + 1
        dist_str = ", ".join(f"{k}: {v}" for k, v in sorted(type_counts.items()))
        insights.append(f"Neuron distribution: {dist_str}")

        # Synapse stats
        exc = sum(1 for s in synapses if s.synapse_type == "excitatory")
        inh = len(synapses) - exc
        insights.append(f"Synapses: {exc} excitatory, {inh} inhibitory ({len(synapses)} total)")

        # Signal reach
        total_cells_fired = set()
        for train in spike_trains:
            for ev in train:
                total_cells_fired.add(ev.cell_id)
        reach_pct = len(total_cells_fired) / max(self.n, 1) * 100
        insights.append(f"Signal reach: {len(total_cells_fired)}/{self.n} cells activated ({reach_pct:.0f}%)")

        # Reflex arcs
        if reflex_arcs:
            avg_lat = sum(a.latency for a in reflex_arcs) / len(reflex_arcs)
            insights.append(
                f"Reflex arcs: {len(reflex_arcs)} found, avg latency {avg_lat:.1f} hops, "
                f"fastest {reflex_arcs[0].latency} hops"
            )
        else:
            insights.append("No reflex arcs detected (missing sensory or motor neurons)")

        # Rhythms
        total_rhythm = sum(rhythms.values())
        if total_rhythm > 0:
            dominant = max(rhythms, key=lambda r: rhythms[r])
            insights.append(
                f"Dominant rhythm: {dominant} ({rhythms[dominant]}/{total_rhythm} oscillations)"
            )

        # Hub neurons (most connected)
        if neurons:
            hub = max(neurons, key=lambda n: len(n.neighbors))
            insights.append(
                f"Hub neuron: cell {hub.cell_id} ({hub.neuron_type}) with "
                f"{len(hub.neighbors)} connections"
            )

        # Health score
        # Components: connectivity (25), signal reach (25), reflex coverage (25), rhythm (25)
        connectivity_score = min(
            sum(len(n.neighbors) for n in neurons) / max(self.n * 3, 1) * 25, 25
        )
        reach_score = reach_pct / 100 * 25
        reflex_score = min(len(reflex_arcs) / max(type_counts.get("sensory", 1), 1) * 25, 25)
        rhythm_score = min(total_rhythm / max(self.n * 0.3, 1) * 25, 25) if total_rhythm > 0 else 0

        health = connectivity_score + reach_score + reflex_score + rhythm_score
        health = max(0.0, min(100.0, health))

        insights.append(f"Health breakdown: connectivity={connectivity_score:.0f}/25, "
                       f"reach={reach_score:.0f}/25, reflex={reflex_score:.0f}/25, "
                       f"rhythm={rhythm_score:.0f}/25")

        if health >= 80:
            insights.append("⚡ Excellent neural network — strong connectivity and signal propagation")
        elif health >= 50:
            insights.append("🔋 Moderate neural network — some regions may be under-connected")
        else:
            insights.append("⚠️ Weak neural network — limited signal reach or missing pathways")

        return health, insights

    # -- Serialization --

    def to_dict(self) -> dict:
        if not self._result:
            self.analyze()
        r = self._result
        assert r is not None
        return {
            "neurons": [asdict(n) for n in r.neurons],
            "synapses": [asdict(s) for s in r.synapses],
            "spike_trains": [
                [asdict(e) for e in train] for train in r.spike_trains
            ],
            "reflex_arcs": [asdict(a) for a in r.reflex_arcs],
            "rhythms": r.rhythms,
            "plasticity_changes": [
                {"source": s, "target": t, "delta": d}
                for s, t, d in r.plasticity_changes
            ],
            "health_score": r.health_score,
            "insights": r.insights,
        }

    def to_json(self, path: str) -> None:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    def to_html(self, path: str) -> None:
        if not self._result:
            self.analyze()
        r = self._result
        assert r is not None

        type_counts: Dict[str, int] = {}
        for n in r.neurons:
            type_counts[n.neuron_type] = type_counts.get(n.neuron_type, 0) + 1

        # Build spike heatmap data
        max_t = 0
        for train in r.spike_trains:
            for ev in train:
                if ev.timestep > max_t:
                    max_t = ev.timestep

        spike_grid: Dict[Tuple[int, int], float] = {}
        for train in r.spike_trains:
            for ev in train:
                spike_grid[(ev.cell_id, ev.timestep)] = ev.potential

        exc_count = sum(1 for s in r.synapses if s.synapse_type == "excitatory")
        inh_count = len(r.synapses) - exc_count

        # Health gauge color
        if r.health_score >= 80:
            gauge_color = "#22c55e"
        elif r.health_score >= 50:
            gauge_color = "#f59e0b"
        else:
            gauge_color = "#ef4444"

        neuron_colors = {
            "sensory": "#3b82f6",
            "motor": "#ef4444",
            "interneuron": "#a855f7",
            "inhibitory": "#6b7280",
            "excitatory": "#22c55e",
        }

        # Build pie chart SVG
        pie_svg = self._build_pie_svg(type_counts, neuron_colors)

        # Build heatmap rows
        heatmap_rows = ""
        display_cells = min(self.n, 30)
        display_steps = min(max_t + 1, 25)
        for i in range(display_cells):
            cells_html = f'<td style="padding:2px 6px;font-size:12px;">C{i}</td>'
            for t in range(display_steps):
                val = spike_grid.get((i, t), 0)
                if val > 0:
                    intensity = min(val / 2.0, 1.0)
                    bg = f"rgba(59,130,246,{intensity:.2f})"
                else:
                    bg = "#1e1e2e"
                cells_html += f'<td style="width:18px;height:18px;background:{bg};border:1px solid #2a2a3e;"></td>'
            heatmap_rows += f"<tr>{cells_html}</tr>\n"

        # Build reflex arcs table
        arcs_html = ""
        for arc in r.reflex_arcs[:15]:
            path_str = " → ".join(str(c) for c in arc.path)
            relay_str = ", ".join(str(c) for c in arc.relay_neurons) if arc.relay_neurons else "—"
            arcs_html += (
                f"<tr><td>{arc.sensory_id}</td><td>{arc.motor_id}</td>"
                f"<td>{arc.latency}</td><td style='font-size:11px;'>{path_str}</td>"
                f"<td>{relay_str}</td></tr>\n"
            )

        # Rhythm bars
        rhythm_max = max(r.rhythms.values(), default=1)
        rhythm_bars = ""
        rhythm_colors = {"alpha": "#3b82f6", "beta": "#f59e0b", "gamma": "#ef4444"}
        for rtype in ("alpha", "beta", "gamma"):
            count = r.rhythms.get(rtype, 0)
            width = count / max(rhythm_max, 1) * 100
            color = rhythm_colors.get(rtype, "#888")
            rhythm_bars += (
                f'<div style="margin:6px 0;">'
                f'<span style="display:inline-block;width:60px;color:#ccc;">{rtype}</span>'
                f'<div style="display:inline-block;width:{width:.0f}%;min-width:2px;'
                f'height:20px;background:{color};border-radius:4px;vertical-align:middle;"></div>'
                f' <span style="color:#999;font-size:12px;">{count}</span></div>\n'
            )

        insights_html = "\n".join(
            f"<li>{html_mod.escape(ins)}</li>" for ins in r.insights
        )

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Spatial Nervous System Dashboard</title>
<style>
  body {{ background:#0f0f1a; color:#e0e0e0; font-family:'Segoe UI',system-ui,sans-serif; margin:0; padding:20px; }}
  .container {{ max-width:1200px; margin:0 auto; }}
  h1 {{ color:#7c3aed; border-bottom:2px solid #7c3aed; padding-bottom:8px; }}
  h2 {{ color:#a78bfa; margin-top:30px; }}
  .card {{ background:#1a1a2e; border-radius:12px; padding:20px; margin:15px 0; border:1px solid #2a2a3e; }}
  .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:15px; }}
  .gauge {{ text-align:center; padding:20px; }}
  .gauge-value {{ font-size:48px; font-weight:bold; color:{gauge_color}; }}
  .gauge-label {{ font-size:14px; color:#999; margin-top:5px; }}
  table {{ border-collapse:collapse; width:100%; }}
  th {{ background:#2a2a3e; color:#a78bfa; padding:8px 12px; text-align:left; }}
  td {{ padding:6px 12px; border-bottom:1px solid #2a2a3e; }}
  .stat {{ text-align:center; }}
  .stat-value {{ font-size:28px; font-weight:bold; color:#7c3aed; }}
  .stat-label {{ font-size:12px; color:#888; }}
  ul {{ padding-left:20px; }}
  li {{ margin:4px 0; line-height:1.6; }}
</style>
</head>
<body>
<div class="container">
<h1>🧠 Spatial Nervous System Dashboard</h1>

<div class="grid">
  <div class="card gauge">
    <div class="gauge-value">{r.health_score:.0f}</div>
    <div class="gauge-label">Neural Health Score (0-100)</div>
  </div>
  <div class="card grid" style="grid-template-columns:1fr 1fr 1fr 1fr;">
    <div class="stat"><div class="stat-value">{self.n}</div><div class="stat-label">Neurons</div></div>
    <div class="stat"><div class="stat-value">{len(r.synapses)}</div><div class="stat-label">Synapses</div></div>
    <div class="stat"><div class="stat-value">{len(r.reflex_arcs)}</div><div class="stat-label">Reflex Arcs</div></div>
    <div class="stat"><div class="stat-value">{len(r.spike_trains)}</div><div class="stat-label">Stimuli</div></div>
  </div>
</div>

<h2>Neuron Types</h2>
<div class="card grid" style="grid-template-columns:1fr 1fr;">
  <div>{pie_svg}</div>
  <div style="padding:20px;">
    {"".join(f'<div style="margin:6px 0;"><span style="display:inline-block;width:14px;height:14px;background:{neuron_colors.get(t, "#888")};border-radius:3px;vertical-align:middle;margin-right:8px;"></span>{t}: <b>{c}</b></div>' for t, c in sorted(type_counts.items()))}
  </div>
</div>

<h2>Synapse Balance</h2>
<div class="card">
  <div style="display:flex;gap:20px;align-items:center;">
    <div style="flex:1;height:24px;background:linear-gradient(to right,#22c55e {exc_count/max(len(r.synapses),1)*100:.0f}%,#6b7280 0);border-radius:12px;"></div>
    <div style="color:#22c55e;">Excitatory: {exc_count}</div>
    <div style="color:#6b7280;">Inhibitory: {inh_count}</div>
  </div>
</div>

<h2>Signal Propagation Heatmap</h2>
<div class="card" style="overflow-x:auto;">
  <table>
    <tr><th></th>{"".join(f"<th style='font-size:11px;text-align:center;'>t{t}</th>" for t in range(display_steps))}</tr>
    {heatmap_rows}
  </table>
  <p style="color:#666;font-size:12px;margin-top:8px;">Blue intensity = activation potential. Showing {display_cells}/{self.n} cells, {display_steps} timesteps.</p>
</div>

<h2>Reflex Arcs</h2>
<div class="card" style="overflow-x:auto;">
  <table>
    <tr><th>Sensory</th><th>Motor</th><th>Latency</th><th>Path</th><th>Relay Neurons</th></tr>
    {arcs_html if arcs_html else "<tr><td colspan='5' style='color:#666;'>No reflex arcs detected</td></tr>"}
  </table>
</div>

<h2>Neural Rhythms</h2>
<div class="card">
  {rhythm_bars if rhythm_bars else "<p style='color:#666;'>No rhythmic patterns detected</p>"}
  <p style="color:#666;font-size:12px;margin-top:8px;">Alpha=slow periodic, Beta=moderate, Gamma=fast burst</p>
</div>

<h2>Insights</h2>
<div class="card">
  <ul>{insights_html}</ul>
</div>

<p style="color:#444;font-size:11px;text-align:center;margin-top:30px;">
  Generated by vormap_nervous — Spatial Nervous System Engine
</p>
</div>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_content)

    def _build_pie_svg(
        self, type_counts: Dict[str, int], colors: Dict[str, str]
    ) -> str:
        total = sum(type_counts.values())
        if total == 0:
            return "<svg></svg>"

        cx, cy, r = 100, 100, 80
        svg = f'<svg width="200" height="200" viewBox="0 0 200 200">'

        if len(type_counts) == 1:
            t = list(type_counts.keys())[0]
            svg += f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{colors.get(t, "#888")}"/>'
        else:
            start_angle = 0
            for t, c in sorted(type_counts.items()):
                sweep = c / total * 360
                end_angle = start_angle + sweep
                large = 1 if sweep > 180 else 0

                x1 = cx + r * math.cos(math.radians(start_angle - 90))
                y1 = cy + r * math.sin(math.radians(start_angle - 90))
                x2 = cx + r * math.cos(math.radians(end_angle - 90))
                y2 = cy + r * math.sin(math.radians(end_angle - 90))

                svg += (
                    f'<path d="M{cx},{cy} L{x1:.1f},{y1:.1f} '
                    f'A{r},{r} 0 {large},1 {x2:.1f},{y2:.1f} Z" '
                    f'fill="{colors.get(t, "#888")}"/>'
                )
                start_angle = end_angle

        svg += "</svg>"
        return svg

    def print_report(self) -> None:
        if not self._result:
            self.analyze()
        r = self._result
        assert r is not None

        print("=" * 60)
        print("  🧠 SPATIAL NERVOUS SYSTEM REPORT")
        print("=" * 60)
        print(f"\n  Neural Health Score: {r.health_score:.0f}/100\n")

        type_counts: Dict[str, int] = {}
        for n in r.neurons:
            type_counts[n.neuron_type] = type_counts.get(n.neuron_type, 0) + 1

        print("  Neuron Types:")
        for t, c in sorted(type_counts.items()):
            bar = "█" * c
            print(f"    {t:>12}: {bar} ({c})")

        exc = sum(1 for s in r.synapses if s.synapse_type == "excitatory")
        inh = len(r.synapses) - exc
        print(f"\n  Synapses: {exc} excitatory, {inh} inhibitory")

        if r.reflex_arcs:
            print(f"\n  Reflex Arcs ({len(r.reflex_arcs)}):")
            for arc in r.reflex_arcs[:5]:
                path_str = " → ".join(str(c) for c in arc.path)
                print(f"    S{arc.sensory_id}→M{arc.motor_id} (latency={arc.latency}): {path_str}")

        print(f"\n  Rhythms: α={r.rhythms.get('alpha', 0)} β={r.rhythms.get('beta', 0)} γ={r.rhythms.get('gamma', 0)}")

        print("\n  Insights:")
        for ins in r.insights:
            print(f"    • {ins}")
        print()


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def nervous_analyze(
    path: str, stimulate: Optional[int] = None
) -> NervousSystemResult:
    """One-liner: load points and return analysis."""
    engine = NervousSystemEngine(path=path, stimulate=stimulate)
    return engine.analyze()


def nervous_demo() -> NervousSystemResult:
    """Run a demo with sample points."""
    random.seed(42)
    pts = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(25)]
    engine = NervousSystemEngine(points=pts)
    result = engine.analyze()
    engine.print_report()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Spatial Nervous System Engine"
    )
    parser.add_argument("file", nargs="?", help="Points file")
    parser.add_argument("--json", dest="json_out", help="Write JSON output")
    parser.add_argument("--html", dest="html_out", help="Write HTML dashboard")
    parser.add_argument(
        "--stimulate", type=int, default=None,
        help="Inject stimulus at cell ID"
    )
    parser.add_argument("--demo", action="store_true", help="Run demo")
    args = parser.parse_args(argv)

    if args.demo:
        nervous_demo()
        return

    if not args.file:
        parser.error("Provide a points file or --demo")

    engine = NervousSystemEngine(path=args.file, stimulate=args.stimulate)
    engine.analyze()
    engine.print_report()

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"  JSON written to {args.json_out}")

    if args.html_out:
        engine.to_html(args.html_out)
        print(f"  HTML dashboard written to {args.html_out}")


if __name__ == "__main__":
    main()
