"""Cellular Automata on Voronoi Tessellations.

Run discrete-state, rule-based automata on irregular Voronoi grids.
Unlike regular (square/hex) cellular automata, Voronoi cells have
variable neighbour counts, making the dynamics richer and more
realistic for spatial modelling.

Built-in rule sets
------------------
- **game_of_life** — Conway's Game of Life adapted for irregular grids.
  Uses fractional thresholds (proportion of live neighbours) instead of
  fixed counts, so it works with any neighbour count.
- **majority** — each cell adopts the most common state among its
  neighbours (ties keep the current state). Models consensus/voting.
- **forest_fire** — three-state model (empty → tree → burning → empty)
  with probabilistic ignition and growth.  Classic ecological model.
- **epidemic** — SIR model (susceptible → infected → recovered) with
  probabilistic infection spread via neighbours.
- **custom** — user supplies a transition function.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_automata import (
        build_automaton, step, run, AutomatonResult,
        export_automata_json, export_automata_csv, export_automata_svg,
    )

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    from vormap_graph import extract_neighborhood_graph
    graph_info = extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    # Game of Life with random initial state
    state = build_automaton(adjacency, rule="game_of_life", alive_fraction=0.3)
    result = run(state, adjacency, rule="game_of_life", steps=50)
    print(result.summary())

    # Forest fire
    state = build_automaton(adjacency, rule="forest_fire", tree_fraction=0.6)
    result = run(state, adjacency, rule="forest_fire", steps=100,
                 fire_spread=0.4, tree_growth=0.01)

    export_automata_svg(result, regions, data, "automata.svg")
    export_automata_json(result, "automata.json")

CLI::

    python vormap_automata.py datauni5.txt --rule game_of_life --steps 50 --alive 0.3
    python vormap_automata.py datauni5.txt --rule forest_fire --steps 100 --svg fire.svg
    python vormap_automata.py datauni5.txt --rule majority --states 3 --steps 30
    python vormap_automata.py datauni5.txt --rule epidemic --steps 80 --infection 0.3
"""


import argparse
import json
import math
import random
import statistics
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import vormap

# ── Data Structures ──────────────────────────────────────────────


@dataclass
class StepSnapshot:
    """State of the automaton at a single time step."""

    step: int
    states: Dict[Tuple[float, float], int]
    state_counts: Dict[int, int]
    changed: int  # cells that changed state this step
    entropy: float  # Shannon entropy of state distribution


@dataclass
class AutomatonResult:
    """Full result of an automaton run."""

    rule: str
    steps_run: int
    num_cells: int
    num_states: int
    history: List[StepSnapshot]
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def final_states(self) -> Dict[Tuple[float, float], int]:
        return self.history[-1].states if self.history else {}

    @property
    def final_counts(self) -> Dict[int, int]:
        return self.history[-1].state_counts if self.history else {}

    @property
    def converged(self) -> bool:
        """True if the last step had zero changes."""
        return len(self.history) >= 2 and self.history[-1].changed == 0

    @property
    def convergence_step(self) -> Optional[int]:
        """Step at which the automaton converged, or None."""
        for snap in self.history[1:]:
            if snap.changed == 0:
                return snap.step
        return None

    def summary(self) -> str:
        lines = [
            f"Voronoi Cellular Automaton — {self.rule}",
            f"{'=' * 48}",
            f"  Cells:          {self.num_cells}",
            f"  States:         {self.num_states}",
            f"  Steps run:      {self.steps_run}",
        ]
        if self.converged:
            lines.append(f"  Converged at:   step {self.convergence_step}")
        else:
            lines.append(f"  Converged:      no")

        if self.history:
            final = self.history[-1]
            lines.append(f"  Final entropy:  {final.entropy:.4f}")
            lines.append(f"  Final distribution:")
            for state in sorted(final.state_counts):
                count = final.state_counts[state]
                pct = count / self.num_cells * 100 if self.num_cells else 0
                label = _state_label(self.rule, state)
                lines.append(f"    {label}: {count} ({pct:.1f}%)")

        if self.params:
            lines.append(f"  Parameters:")
            for k, v in sorted(self.params.items()):
                lines.append(f"    {k}: {v}")

        return "\n".join(lines)


# ── Helpers ──────────────────────────────────────────────────────

def _state_label(rule: str, state: int) -> str:
    """Human-readable label for a state value."""
    labels = {
        "game_of_life": {0: "dead", 1: "alive"},
        "forest_fire": {0: "empty", 1: "tree", 2: "burning"},
        "epidemic": {0: "susceptible", 1: "infected", 2: "recovered"},
    }
    rule_labels = labels.get(rule, {})
    return rule_labels.get(state, f"state_{state}")


def _shannon_entropy(counts: Dict[int, int], total: int) -> float:
    """Shannon entropy in bits."""
    if total == 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h


def _snapshot(step_num: int, states: Dict, num_cells: int,
              prev_states: Optional[Dict] = None) -> StepSnapshot:
    """Create a StepSnapshot from current states."""
    counts: Dict[int, int] = Counter(states.values())
    changed = 0
    if prev_states is not None:
        for seed in states:
            if states[seed] != prev_states.get(seed):
                changed += 1
    return StepSnapshot(
        step=step_num,
        states=dict(states),
        state_counts=dict(counts),
        changed=changed,
        entropy=_shannon_entropy(counts, num_cells),
    )


# ── Initialization ───────────────────────────────────────────────

def build_automaton(
    adjacency: Dict[Tuple[float, float], list],
    *,
    rule: str = "game_of_life",
    num_states: int = 2,
    alive_fraction: float = 0.3,
    tree_fraction: float = 0.6,
    infected_fraction: float = 0.05,
    initial_states: Optional[Dict[Tuple[float, float], int]] = None,
    seed: Optional[int] = None,
) -> Dict[Tuple[float, float], int]:
    """Initialize cell states for an automaton.

    Parameters
    ----------
    adjacency : dict
        Voronoi adjacency graph (seed -> list of neighbour seeds).
    rule : str
        Rule set name (determines default initialization).
    num_states : int
        Number of discrete states (for majority rule).
    alive_fraction : float
        Fraction of cells initially alive (game_of_life).
    tree_fraction : float
        Fraction of cells initially trees (forest_fire).
    infected_fraction : float
        Fraction of cells initially infected (epidemic).
    initial_states : dict or None
        Explicit initial states; overrides random initialization.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    dict
        Maps seed (x, y) -> initial state (int).
    """
    rng = random.Random(seed)
    seeds = sorted(adjacency.keys())
    n = len(seeds)

    if initial_states is not None:
        states = {}
        for s in seeds:
            states[s] = initial_states.get(s, 0)
        return states

    states = {}

    if rule == "game_of_life":
        alive_count = max(1, int(n * alive_fraction))
        alive_seeds = set(rng.sample(seeds, min(alive_count, n)))
        for s in seeds:
            states[s] = 1 if s in alive_seeds else 0

    elif rule == "forest_fire":
        tree_count = max(1, int(n * tree_fraction))
        tree_seeds = set(rng.sample(seeds, min(tree_count, n)))
        for s in seeds:
            states[s] = 1 if s in tree_seeds else 0

    elif rule == "epidemic":
        infected_count = max(1, int(n * infected_fraction))
        infected_seeds = set(rng.sample(seeds, min(infected_count, n)))
        for s in seeds:
            states[s] = 1 if s in infected_seeds else 0

    elif rule == "majority":
        for s in seeds:
            states[s] = rng.randint(0, max(1, num_states - 1))

    else:
        for s in seeds:
            states[s] = rng.randint(0, max(1, num_states - 1))

    return states


# ── Rule Functions ───────────────────────────────────────────────

def _step_game_of_life(
    states: Dict, adjacency: Dict,
    birth_lo: float = 0.25, birth_hi: float = 0.55,
    survive_lo: float = 0.20, survive_hi: float = 0.60,
) -> Dict:
    """One step of Game of Life with fractional thresholds.

    Because Voronoi cells have variable neighbour counts, we use the
    *fraction* of live neighbours instead of absolute counts:

    - Dead cell becomes alive if fraction in [birth_lo, birth_hi]
    - Live cell survives if fraction in [survive_lo, survive_hi]
    """
    new_states = {}
    for seed, state in states.items():
        neighbors = adjacency.get(seed, [])
        n_neighbors = len(neighbors)
        if n_neighbors == 0:
            new_states[seed] = state
            continue

        alive_count = sum(1 for nb in neighbors if states.get(nb, 0) == 1)
        fraction = alive_count / n_neighbors

        if state == 0:
            # Birth
            new_states[seed] = 1 if birth_lo <= fraction <= birth_hi else 0
        else:
            # Survival
            new_states[seed] = 1 if survive_lo <= fraction <= survive_hi else 0

    return new_states


def _step_majority(states: Dict, adjacency: Dict, num_states: int = 2) -> Dict:
    """Majority rule: each cell adopts the most common neighbour state."""
    new_states = {}
    for seed, state in states.items():
        neighbors = adjacency.get(seed, [])
        if not neighbors:
            new_states[seed] = state
            continue

        counts: Counter = Counter()
        for nb in neighbors:
            counts[states.get(nb, 0)] += 1

        max_count = max(counts.values())
        candidates = [s for s, c in counts.items() if c == max_count]

        if len(candidates) == 1:
            new_states[seed] = candidates[0]
        else:
            # Tie: keep current state if it's among the tied, else pick lowest
            new_states[seed] = state if state in candidates else min(candidates)

    return new_states


def _step_forest_fire(
    states: Dict, adjacency: Dict, rng: random.Random,
    fire_spread: float = 0.3, tree_growth: float = 0.01,
    lightning: float = 0.001,
) -> Dict:
    """Forest fire model: empty(0) → tree(1) → burning(2) → empty(0).

    - Empty cells grow a tree with probability tree_growth.
    - Trees catch fire if any neighbour is burning (probability fire_spread
      per burning neighbour, combined).
    - Trees can also spontaneously ignite (lightning probability).
    - Burning cells become empty next step.
    """
    new_states = {}
    for seed, state in states.items():
        neighbors = adjacency.get(seed, [])

        if state == 2:
            # Burning → empty
            new_states[seed] = 0
        elif state == 1:
            # Tree: check for fire spread
            burning_neighbors = sum(1 for nb in neighbors if states.get(nb, 0) == 2)
            if burning_neighbors > 0:
                # Probability of NOT catching fire from any burning neighbor
                prob_safe = (1 - fire_spread) ** burning_neighbors
                if rng.random() > prob_safe:
                    new_states[seed] = 2
                    continue
            # Spontaneous ignition
            if rng.random() < lightning:
                new_states[seed] = 2
            else:
                new_states[seed] = 1
        else:
            # Empty → tree growth
            new_states[seed] = 1 if rng.random() < tree_growth else 0

    return new_states


def _step_epidemic(
    states: Dict, adjacency: Dict, rng: random.Random,
    infection_rate: float = 0.3, recovery_rate: float = 0.1,
    immunity_loss: float = 0.0,
) -> Dict:
    """SIR epidemic: susceptible(0) → infected(1) → recovered(2).

    - Susceptible cells get infected by infected neighbours
      (probability per infected neighbour, combined).
    - Infected cells recover with probability recovery_rate.
    - Recovered cells lose immunity with probability immunity_loss
      (0 = permanent immunity, SIR; >0 = SIRS).
    """
    new_states = {}
    for seed, state in states.items():
        neighbors = adjacency.get(seed, [])

        if state == 0:
            # Susceptible: check for infection
            infected_neighbors = sum(
                1 for nb in neighbors if states.get(nb, 0) == 1
            )
            if infected_neighbors > 0:
                prob_safe = (1 - infection_rate) ** infected_neighbors
                if rng.random() > prob_safe:
                    new_states[seed] = 1
                    continue
            new_states[seed] = 0

        elif state == 1:
            # Infected → recovered
            new_states[seed] = 2 if rng.random() < recovery_rate else 1

        else:
            # Recovered → susceptible (immunity loss)
            if immunity_loss > 0 and rng.random() < immunity_loss:
                new_states[seed] = 0
            else:
                new_states[seed] = 2

    return new_states


# ── Step & Run ───────────────────────────────────────────────────

def step(
    states: Dict[Tuple[float, float], int],
    adjacency: Dict[Tuple[float, float], list],
    *,
    rule: str = "game_of_life",
    transition_fn: Optional[Callable] = None,
    rng: Optional[random.Random] = None,
    **kwargs,
) -> Dict[Tuple[float, float], int]:
    """Advance the automaton by one step.

    Parameters
    ----------
    states : dict
        Current cell states (seed -> int).
    adjacency : dict
        Voronoi adjacency graph.
    rule : str
        Built-in rule name.
    transition_fn : callable or None
        Custom transition function: fn(states, adjacency, **kwargs) -> new_states.
    rng : Random or None
        Random generator for stochastic rules.
    **kwargs
        Rule-specific parameters.

    Returns
    -------
    dict
        New cell states.
    """
    if transition_fn is not None:
        return transition_fn(states, adjacency, **kwargs)

    if rule == "game_of_life":
        return _step_game_of_life(
            states, adjacency,
            birth_lo=kwargs.get("birth_lo", 0.25),
            birth_hi=kwargs.get("birth_hi", 0.55),
            survive_lo=kwargs.get("survive_lo", 0.20),
            survive_hi=kwargs.get("survive_hi", 0.60),
        )
    elif rule == "majority":
        return _step_majority(
            states, adjacency,
            num_states=kwargs.get("num_states", 2),
        )
    elif rule == "forest_fire":
        if rng is None:
            rng = random.Random()
        return _step_forest_fire(
            states, adjacency, rng,
            fire_spread=kwargs.get("fire_spread", 0.3),
            tree_growth=kwargs.get("tree_growth", 0.01),
            lightning=kwargs.get("lightning", 0.001),
        )
    elif rule == "epidemic":
        if rng is None:
            rng = random.Random()
        return _step_epidemic(
            states, adjacency, rng,
            infection_rate=kwargs.get("infection_rate", 0.3),
            recovery_rate=kwargs.get("recovery_rate", 0.1),
            immunity_loss=kwargs.get("immunity_loss", 0.0),
        )
    else:
        raise ValueError(f"Unknown rule: {rule}. Use game_of_life, majority, forest_fire, epidemic, or supply transition_fn.")


def run(
    initial_states: Dict[Tuple[float, float], int],
    adjacency: Dict[Tuple[float, float], list],
    *,
    rule: str = "game_of_life",
    steps: int = 50,
    transition_fn: Optional[Callable] = None,
    stop_on_convergence: bool = True,
    record_interval: int = 1,
    seed: Optional[int] = None,
    **kwargs,
) -> AutomatonResult:
    """Run the automaton for multiple steps.

    Parameters
    ----------
    initial_states : dict
        Starting cell states.
    adjacency : dict
        Voronoi adjacency graph.
    rule : str
        Built-in rule name.
    steps : int
        Maximum number of steps.
    transition_fn : callable or None
        Custom transition function.
    stop_on_convergence : bool
        Stop early if no cells change state (default True).
    record_interval : int
        Record a snapshot every N steps (default 1 = every step).
    seed : int or None
        Random seed for stochastic rules.
    **kwargs
        Rule-specific parameters passed to step().

    Returns
    -------
    AutomatonResult
        Full history of the run.
    """
    if steps < 1:
        raise ValueError("steps must be >= 1")

    rng = random.Random(seed)
    num_cells = len(initial_states)

    # Determine number of states from initial + rule
    max_state = max(initial_states.values()) if initial_states else 0
    if rule == "forest_fire" or rule == "epidemic":
        num_states = 3
    elif rule == "majority":
        num_states = max(kwargs.get("num_states", 2), max_state + 1)
    else:
        num_states = max(2, max_state + 1)

    history = []
    states = dict(initial_states)

    # Record step 0
    snap = _snapshot(0, states, num_cells)
    history.append(snap)

    params = {"rule": rule, "steps_requested": steps}
    params.update({k: v for k, v in kwargs.items() if not callable(v)})

    for s in range(1, steps + 1):
        prev = states
        states = step(states, adjacency, rule=rule,
                      transition_fn=transition_fn, rng=rng, **kwargs)

        if s % record_interval == 0 or s == steps:
            snap = _snapshot(s, states, num_cells, prev)
            history.append(snap)

            if stop_on_convergence and snap.changed == 0:
                break

    return AutomatonResult(
        rule=rule,
        steps_run=history[-1].step,
        num_cells=num_cells,
        num_states=num_states,
        history=history,
        params=params,
    )


# ── Export: JSON ─────────────────────────────────────────────────

def export_automata_json(result: AutomatonResult, output_path: str) -> str:
    """Export automaton result to JSON.

    Includes metadata, parameters, and per-step state counts (not full
    cell states, to keep file size reasonable).
    """
    vormap.validate_output_path(output_path)

    data = {
        "rule": result.rule,
        "num_cells": result.num_cells,
        "num_states": result.num_states,
        "steps_run": result.steps_run,
        "converged": result.converged,
        "convergence_step": result.convergence_step,
        "params": result.params,
        "history": [],
    }

    for snap in result.history:
        entry = {
            "step": snap.step,
            "state_counts": {str(k): v for k, v in sorted(snap.state_counts.items())},
            "changed": snap.changed,
            "entropy": round(snap.entropy, 6),
        }
        data["history"].append(entry)

    # Also include final cell states for visualization
    final_states = {}
    if result.history:
        for seed, state in result.history[-1].states.items():
            final_states[f"{seed[0]},{seed[1]}"] = state
    data["final_states"] = final_states

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    return output_path


# ── Export: CSV ──────────────────────────────────────────────────

def export_automata_csv(result: AutomatonResult, output_path: str) -> str:
    """Export per-step statistics to CSV."""
    vormap.validate_output_path(output_path)

    # Determine all state values
    all_states = sorted(set(
        s for snap in result.history for s in snap.state_counts
    ))

    header = ["step", "changed", "entropy"]
    for s in all_states:
        label = _state_label(result.rule, s)
        header.append(f"count_{label}")

    lines = [",".join(header)]
    for snap in result.history:
        row = [str(snap.step), str(snap.changed), f"{snap.entropy:.6f}"]
        for s in all_states:
            row.append(str(snap.state_counts.get(s, 0)))
        lines.append(",".join(row))

    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    return output_path


# ── Export: SVG ──────────────────────────────────────────────────

# Color palettes for different rules
_PALETTES = {
    "game_of_life": {0: "#f0f0f0", 1: "#2196F3"},
    "forest_fire": {0: "#d4a574", 1: "#4CAF50", 2: "#FF5722"},
    "epidemic": {0: "#81C784", 1: "#FF5252", 2: "#9E9E9E"},
    "majority": {
        0: "#E91E63", 1: "#2196F3", 2: "#4CAF50", 3: "#FF9800",
        4: "#9C27B0", 5: "#00BCD4", 6: "#795548", 7: "#607D8B",
    },
}


def _state_color(rule: str, state: int) -> str:
    """Get color for a state value."""
    palette = _PALETTES.get(rule, _PALETTES["majority"])
    return palette.get(state, "#BDBDBD")


def export_automata_svg(
    result: AutomatonResult,
    regions: dict,
    data: list,
    output_path: str,
    *,
    width: int = 800,
    height: int = 600,
    show_seeds: bool = False,
    step_index: int = -1,
) -> str:
    """Export the automaton state as an SVG diagram.

    Parameters
    ----------
    result : AutomatonResult
        Automaton run result.
    regions : dict
        Voronoi regions (seed -> vertex list).
    data : list
        Seed points.
    output_path : str
        Output SVG file path.
    width, height : int
        SVG canvas dimensions.
    show_seeds : bool
        Whether to draw seed points.
    step_index : int
        Which step to render (-1 = final, 0 = initial).

    Returns
    -------
    str
        Output file path.
    """
    vormap.validate_output_path(output_path)

    if not result.history:
        raise ValueError("No history to render")

    snap = result.history[step_index]
    states = snap.states

    # Find bounding box from all region vertices
    all_x = []
    all_y = []
    for seed, verts in regions.items():
        for vx, vy in verts:
            all_x.append(vx)
            all_y.append(vy)

    if not all_x:
        raise ValueError("No region vertices to render")

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    data_w = max_x - min_x or 1
    data_h = max_y - min_y or 1

    margin = 20
    scale_x = (width - 2 * margin) / data_w
    scale_y = (height - 2 * margin) / data_h
    scale = min(scale_x, scale_y)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        return margin + (y - min_y) * scale

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height))

    # Background
    ET.SubElement(svg, "rect", x="0", y="0",
                  width=str(width), height=str(height), fill="white")

    # Title
    title_text = f"{result.rule} — step {snap.step}"
    title = ET.SubElement(svg, "text", x=str(width // 2), y="16",
                          fill="#333", **{"font-size": "14", "font-family": "sans-serif",
                                         "text-anchor": "middle"})
    title.text = title_text

    # Render regions
    for seed, verts in regions.items():
        if len(verts) < 3:
            continue

        state = states.get(seed, 0)
        color = _state_color(result.rule, state)

        points = " ".join(f"{tx(vx):.1f},{ty(vy):.1f}" for vx, vy in verts)
        ET.SubElement(svg, "polygon", points=points,
                      fill=color, stroke="#666", **{"stroke-width": "0.5"})

    # Seed points
    if show_seeds:
        for pt in data:
            ET.SubElement(svg, "circle",
                          cx=f"{tx(pt[0]):.1f}", cy=f"{ty(pt[1]):.1f}",
                          r="2", fill="#333")

    # Legend
    legend_y = height - 18
    legend_x = 10
    all_states_in_snap = sorted(set(states.values()))
    for s in all_states_in_snap:
        color = _state_color(result.rule, s)
        ET.SubElement(svg, "rect", x=str(legend_x), y=str(legend_y),
                      width="12", height="12", fill=color, stroke="#666",
                      **{"stroke-width": "0.5"})
        label = ET.SubElement(svg, "text", x=str(legend_x + 16), y=str(legend_y + 10),
                              fill="#333", **{"font-size": "10", "font-family": "sans-serif"})
        label.text = _state_label(result.rule, s)
        legend_x += 16 + len(_state_label(result.rule, s)) * 6 + 12

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, xml_declaration=True, encoding="unicode")

    return output_path


# ── Report ───────────────────────────────────────────────────────

def format_report(result: AutomatonResult) -> str:
    """Generate a human-readable text report."""
    lines = [result.summary(), ""]

    # Activity timeline
    lines.append("Step-by-step activity:")
    lines.append(f"  {'Step':>6}  {'Changed':>8}  {'Entropy':>8}  Distribution")
    lines.append(f"  {'─' * 6}  {'─' * 8}  {'─' * 8}  {'─' * 30}")

    # Show at most 20 steps for readability
    show_steps = result.history
    if len(show_steps) > 20:
        # Show first 5, last 5, and some middle ones
        indices = list(range(5)) + list(range(len(show_steps) - 5, len(show_steps)))
        middle_step = len(show_steps) // 2
        if middle_step not in indices:
            indices.append(middle_step)
        indices = sorted(set(indices))
        show_steps = [result.history[i] for i in indices]

    prev_idx = -1
    for snap in show_steps:
        if snap.step - prev_idx > 1 and prev_idx >= 0:
            lines.append(f"  {'...':>6}")
        dist = "  ".join(
            f"{_state_label(result.rule, s)}={snap.state_counts.get(s, 0)}"
            for s in sorted(snap.state_counts)
        )
        lines.append(f"  {snap.step:>6}  {snap.changed:>8}  {snap.entropy:>8.4f}  {dist}")
        prev_idx = snap.step

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cellular Automata on Voronoi Tessellations"
    )
    parser.add_argument("datafile", help="Seed points file (txt/csv/json)")
    parser.add_argument("--rule", default="game_of_life",
                        choices=["game_of_life", "majority", "forest_fire", "epidemic"],
                        help="Automaton rule set (default: game_of_life)")
    parser.add_argument("--steps", type=int, default=50,
                        help="Number of simulation steps (default: 50)")
    parser.add_argument("--alive", type=float, default=0.3,
                        help="Initial alive fraction for game_of_life (default: 0.3)")
    parser.add_argument("--trees", type=float, default=0.6,
                        help="Initial tree fraction for forest_fire (default: 0.6)")
    parser.add_argument("--infected", type=float, default=0.05,
                        help="Initial infected fraction for epidemic (default: 0.05)")
    parser.add_argument("--states", type=int, default=3,
                        help="Number of states for majority rule (default: 3)")
    parser.add_argument("--fire-spread", type=float, default=0.3,
                        help="Fire spread probability (default: 0.3)")
    parser.add_argument("--tree-growth", type=float, default=0.01,
                        help="Tree growth probability (default: 0.01)")
    parser.add_argument("--infection", type=float, default=0.3,
                        help="Infection rate for epidemic (default: 0.3)")
    parser.add_argument("--recovery", type=float, default=0.1,
                        help="Recovery rate for epidemic (default: 0.1)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--svg", default=None, help="Output SVG file")
    parser.add_argument("--json", default=None, dest="json_out", help="Output JSON file")
    parser.add_argument("--csv", default=None, dest="csv_out", help="Output CSV file")

    args = parser.parse_args()

    import vormap_viz
    from vormap_graph import extract_neighborhood_graph

    data = vormap.load_data(args.datafile)
    regions = vormap_viz.compute_regions(data)
    graph_info = extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    # Build initial state
    init_kwargs = {"seed": args.seed}
    run_kwargs = {"seed": args.seed}

    if args.rule == "game_of_life":
        init_kwargs["alive_fraction"] = args.alive
    elif args.rule == "forest_fire":
        init_kwargs["tree_fraction"] = args.trees
        run_kwargs["fire_spread"] = args.fire_spread
        run_kwargs["tree_growth"] = args.tree_growth
    elif args.rule == "epidemic":
        init_kwargs["infected_fraction"] = args.infected
        run_kwargs["infection_rate"] = args.infection
        run_kwargs["recovery_rate"] = args.recovery
    elif args.rule == "majority":
        init_kwargs["num_states"] = args.states
        run_kwargs["num_states"] = args.states

    initial = build_automaton(adjacency, rule=args.rule, **init_kwargs)
    result = run(initial, adjacency, rule=args.rule, steps=args.steps, **run_kwargs)

    print(format_report(result))

    if args.svg:
        export_automata_svg(result, regions, data, args.svg)
        print(f"\nSVG written to {args.svg}")

    if args.json_out:
        export_automata_json(result, args.json_out)
        print(f"JSON written to {args.json_out}")

    if args.csv_out:
        export_automata_csv(result, args.csv_out)
        print(f"CSV written to {args.csv_out}")


if __name__ == "__main__":
    main()
