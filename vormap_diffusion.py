"""Spatial Diffusion Simulation over Voronoi Networks.

Simulates how a quantity or state spreads through a Voronoi tessellation
over discrete time steps.  Each cell has a scalar value that evolves based
on exchange with its neighbours through the adjacency graph.

Three diffusion models are supported:

- **heat** — classic heat equation diffusion: values flow from high to low
  proportionally to the gradient.  Models physical diffusion, pollution
  spread, temperature equilibration.
- **sir** — SIR (Susceptible-Infected-Recovered) epidemic model on the
  spatial network.  Each cell is in one of three states.  Models disease
  spread, information propagation, wildfire.
- **threshold** — threshold adoption model: a cell adopts (flips to 1)
  when the fraction of adopted neighbours exceeds a threshold.  Models
  innovation diffusion, social influence, technology adoption.

All models produce a time-series of per-cell states that can be
exported as JSON, CSV, or animated SVG for visualisation.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_diffusion import (
        heat_diffusion, sir_simulation, threshold_diffusion,
        export_diffusion_json, export_diffusion_csv,
        export_diffusion_svg,
    )

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    graph_info = vormap_viz.extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    # Heat diffusion from a single hot source
    initial = {seed: 0.0 for seed in adjacency}
    source = list(adjacency.keys())[0]
    initial[source] = 100.0

    result = heat_diffusion(initial, adjacency, steps=50, alpha=0.2)
    export_diffusion_json(result, "heat.json")
    export_diffusion_svg(result, regions, data, "heat.svg")

CLI::

    python vormap_diffusion.py data/points.txt --model heat --source 0 --steps 50
    python vormap_diffusion.py data/points.txt --model sir --beta 0.3 --gamma 0.1
    python vormap_diffusion.py data/points.txt --model threshold --threshold 0.4
    python vormap_diffusion.py data/points.txt --model heat --json diffusion.json
    python vormap_diffusion.py data/points.txt --model heat --csv diffusion.csv
    python vormap_diffusion.py data/points.txt --model heat --svg diffusion.svg
"""


import argparse
import csv
import json
import random as _random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import vormap


# ── Result containers ───────────────────────────────────────────────

@dataclass
class DiffusionFrame:
    """Snapshot of the simulation at a single time step.

    Attributes
    ----------
    step : int
        Time step index (0 = initial state).
    values : dict
        Maps seed (x, y) -> current value (float for heat/threshold,
        str for SIR: 'S'/'I'/'R').
    """
    step: int
    values: dict


@dataclass
class DiffusionResult:
    """Full result of a diffusion simulation.

    Attributes
    ----------
    model : str
        Model name ('heat', 'sir', 'threshold').
    frames : list of DiffusionFrame
        Time series of simulation states.
    seeds : list of tuple
        Ordered list of seed coordinates.
    params : dict
        Parameters used for the simulation.
    summary : dict
        Aggregate statistics of the simulation.
    """
    model: str
    frames: list
    seeds: list
    params: dict
    summary: dict = field(default_factory=dict)


# ── Heat Diffusion ──────────────────────────────────────────────────

def heat_diffusion(
    initial_values: Dict[Tuple[float, float], float],
    adjacency: Dict[Tuple[float, float], list],
    *,
    steps: int = 50,
    alpha: float = 0.2,
    boundary: str = "insulated",
) -> DiffusionResult:
    """Simulate heat equation diffusion on the Voronoi graph.

    At each time step, each cell's value is updated by exchanging a
    fraction *alpha* of its gradient with each neighbour::

        v_new[i] = v[i] + alpha * sum(v[j] - v[i] for j in neighbors[i])

    This is a discrete Laplacian diffusion (explicit Euler scheme).

    Parameters
    ----------
    initial_values : dict
        Maps seed (x, y) -> initial scalar value.
    adjacency : dict
        Maps seed -> list of neighbour seeds (from ``extract_neighborhood_graph``).
    steps : int
        Number of time steps to simulate (default 50).
    alpha : float
        Diffusion coefficient per neighbour per step (default 0.2).
        Must satisfy ``alpha * max_degree < 1`` for stability.
    boundary : str
        Boundary condition: 'insulated' (no flux at boundary) or
        'fixed' (boundary cells hold their initial values).

    Returns
    -------
    DiffusionResult
        Time series of cell values.

    Raises
    ------
    ValueError
        If alpha is non-positive or would cause numerical instability.
    """
    if alpha <= 0:
        raise ValueError("alpha must be positive")
    if steps < 1:
        raise ValueError("steps must be >= 1")

    seeds = sorted(initial_values.keys())
    max_degree = max(len(adjacency.get(s, [])) for s in seeds) if seeds else 0
    if max_degree > 0 and alpha * max_degree >= 1.0:
        raise ValueError(
            f"alpha={alpha} with max degree {max_degree} is unstable; "
            f"need alpha < {1.0 / max_degree:.4f}"
        )

    # Determine boundary cells: cells whose degree is strictly less than
    # the median degree — heuristic for cells on the convex hull.
    boundary_cells = set()
    if boundary == "fixed":
        degrees = sorted(len(adjacency.get(s, [])) for s in seeds)
        median_degree = degrees[len(degrees) // 2] if degrees else 0
        boundary_cells = {s for s in seeds if len(adjacency.get(s, [])) < median_degree}

    values = dict(initial_values)
    frames = [DiffusionFrame(step=0, values=dict(values))]

    for t in range(1, steps + 1):
        new_values = {}
        for seed in seeds:
            if seed in boundary_cells:
                new_values[seed] = initial_values[seed]
                continue
            neighbors = adjacency.get(seed, [])
            v = values[seed]
            delta = sum(values.get(tuple(n), v) - v for n in neighbors)
            new_values[seed] = v + alpha * delta
        values = new_values
        frames.append(DiffusionFrame(step=t, values=dict(values)))

    # Summary stats
    final_vals = list(values.values())
    init_vals = list(initial_values.values())
    summary = {
        "total_steps": steps,
        "initial_range": [min(init_vals), max(init_vals)] if init_vals else [0, 0],
        "final_range": [min(final_vals), max(final_vals)] if final_vals else [0, 0],
        "initial_mean": sum(init_vals) / max(len(init_vals), 1),
        "final_mean": sum(final_vals) / max(len(final_vals), 1),
        "convergence": max(final_vals) - min(final_vals) if final_vals else 0,
    }

    return DiffusionResult(
        model="heat",
        frames=frames,
        seeds=seeds,
        params={"alpha": alpha, "steps": steps, "boundary": boundary},
        summary=summary,
    )


# ── SIR Epidemic Model ─────────────────────────────────────────────

def sir_simulation(
    adjacency: Dict[Tuple[float, float], list],
    *,
    initial_infected: Optional[List[int]] = None,
    beta: float = 0.3,
    gamma: float = 0.1,
    steps: int = 100,
    seed: Optional[int] = None,
) -> DiffusionResult:
    """Simulate SIR epidemic model on the Voronoi graph.

    Each cell is in one of three states:
    - **S** (Susceptible): can become infected
    - **I** (Infected): can transmit to neighbours and can recover
    - **R** (Recovered): immune, cannot be re-infected

    At each step:
    - Each susceptible cell becomes infected with probability
      ``1 - (1 - beta)^k`` where k is the number of infected neighbours.
    - Each infected cell recovers with probability gamma.

    Parameters
    ----------
    adjacency : dict
        Maps seed -> list of neighbour seeds.
    initial_infected : list of int or None
        Indices of initially infected cells (0-based into sorted seed list).
        Defaults to [0] (first cell).
    beta : float
        Per-neighbour transmission probability per step (default 0.3).
    gamma : float
        Recovery probability per step (default 0.1).
    steps : int
        Maximum number of time steps (default 100).
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    DiffusionResult
        Time series of cell states ('S', 'I', 'R').
    """
    if not (0 <= beta <= 1):
        raise ValueError("beta must be in [0, 1]")
    if not (0 <= gamma <= 1):
        raise ValueError("gamma must be in [0, 1]")
    if steps < 1:
        raise ValueError("steps must be >= 1")

    rng = _random.Random(seed)
    seeds = sorted(adjacency.keys())

    if initial_infected is None:
        initial_infected = [0]

    # Initial state: all susceptible except infected
    states = {s: "S" for s in seeds}
    for idx in initial_infected:
        if 0 <= idx < len(seeds):
            states[seeds[idx]] = "I"

    frames = [DiffusionFrame(step=0, values=dict(states))]

    peak_infected = sum(1 for v in states.values() if v == "I")
    peak_step = 0

    for t in range(1, steps + 1):
        new_states = dict(states)
        for s in seeds:
            if states[s] == "S":
                # Count infected neighbours
                infected_neighbors = sum(
                    1 for n in adjacency.get(s, [])
                    if states.get(tuple(n), "S") == "I"
                )
                if infected_neighbors > 0:
                    # Probability of NOT getting infected from any neighbour
                    prob_safe = (1 - beta) ** infected_neighbors
                    if rng.random() > prob_safe:
                        new_states[s] = "I"
            elif states[s] == "I":
                if rng.random() < gamma:
                    new_states[s] = "R"
            # R stays R

        states = new_states
        frames.append(DiffusionFrame(step=t, values=dict(states)))

        current_infected = sum(1 for v in states.values() if v == "I")
        if current_infected > peak_infected:
            peak_infected = current_infected
            peak_step = t

        # Early termination if no infected cells remain
        if current_infected == 0:
            break

    final_counts = {"S": 0, "I": 0, "R": 0}
    for v in states.values():
        final_counts[v] += 1

    summary = {
        "total_steps": len(frames) - 1,
        "peak_infected": peak_infected,
        "peak_step": peak_step,
        "final_susceptible": final_counts["S"],
        "final_infected": final_counts["I"],
        "final_recovered": final_counts["R"],
        "attack_rate": round(final_counts["R"] / max(len(seeds), 1), 4),
    }

    return DiffusionResult(
        model="sir",
        frames=frames,
        seeds=seeds,
        params={
            "beta": beta, "gamma": gamma, "steps": steps,
            "initial_infected": initial_infected,
        },
        summary=summary,
    )


# ── Threshold Diffusion ────────────────────────────────────────────

def threshold_diffusion(
    adjacency: Dict[Tuple[float, float], list],
    *,
    initial_adopters: Optional[List[int]] = None,
    threshold: float = 0.4,
    steps: int = 100,
) -> DiffusionResult:
    """Simulate threshold adoption model on the Voronoi graph.

    A cell adopts (flips from 0 to 1) when the fraction of its
    neighbours that have adopted exceeds the threshold.  Once adopted,
    a cell stays adopted (irreversible).

    Parameters
    ----------
    adjacency : dict
        Maps seed -> list of neighbour seeds.
    initial_adopters : list of int or None
        Indices of initially adopted cells (0-based).  Defaults to [0].
    threshold : float
        Fraction of adopted neighbours needed to trigger adoption (default 0.4).
    steps : int
        Maximum number of time steps (default 100).

    Returns
    -------
    DiffusionResult
        Time series of adoption states (0 or 1).
    """
    if not (0 < threshold <= 1):
        raise ValueError("threshold must be in (0, 1]")
    if steps < 1:
        raise ValueError("steps must be >= 1")

    seeds = sorted(adjacency.keys())

    if initial_adopters is None:
        initial_adopters = [0]

    states = {s: 0 for s in seeds}
    for idx in initial_adopters:
        if 0 <= idx < len(seeds):
            states[seeds[idx]] = 1

    frames = [DiffusionFrame(step=0, values=dict(states))]

    for t in range(1, steps + 1):
        new_states = dict(states)
        changed = False
        for s in seeds:
            if states[s] == 1:
                continue  # Already adopted
            neighbors = adjacency.get(s, [])
            if not neighbors:
                continue
            adopted_frac = sum(
                1 for n in neighbors if states.get(tuple(n), 0) == 1
            ) / len(neighbors)
            if adopted_frac >= threshold:
                new_states[s] = 1
                changed = True

        states = new_states
        frames.append(DiffusionFrame(step=t, values=dict(states)))

        # Early termination if nothing changed (steady state)
        if not changed:
            break

    total_adopted = sum(1 for v in states.values() if v == 1)
    summary = {
        "total_steps": len(frames) - 1,
        "total_adopted": total_adopted,
        "adoption_rate": round(total_adopted / max(len(seeds), 1), 4),
        "converged": not changed if len(frames) > 1 else True,
    }

    return DiffusionResult(
        model="threshold",
        frames=frames,
        seeds=seeds,
        params={"threshold": threshold, "steps": steps,
                "initial_adopters": initial_adopters},
        summary=summary,
    )


# ── Export: JSON ────────────────────────────────────────────────────

def export_diffusion_json(result: DiffusionResult, output_path: str) -> str:
    """Export diffusion result as JSON.

    Parameters
    ----------
    result : DiffusionResult
        Output of any diffusion function.
    output_path : str
        Path to write the JSON file.

    Returns
    -------
    str
        The output path.
    """
    vormap.validate_output_path(output_path, allow_absolute=True)

    seed_list = [list(s) for s in result.seeds]
    seed_strs = [f"{s[0]},{s[1]}" for s in result.seeds]

    data = {
        "model": result.model,
        "params": result.params,
        "summary": result.summary,
        "seeds": seed_list,
        "frames": [],
    }

    for frame in result.frames:
        frame_values = []
        for s in result.seeds:
            v = frame.values.get(s, frame.values.get(tuple(s), 0))
            frame_values.append(v)
        data["frames"].append({
            "step": frame.step,
            "values": frame_values,
        })

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return output_path


# ── Export: CSV ─────────────────────────────────────────────────────

def export_diffusion_csv(result: DiffusionResult, output_path: str) -> str:
    """Export diffusion result as CSV.

    One row per cell per time step.

    Parameters
    ----------
    result : DiffusionResult
        Output of any diffusion function.
    output_path : str
        Path to write the CSV file.

    Returns
    -------
    str
        The output path.
    """
    vormap.validate_output_path(output_path, allow_absolute=True)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["step", "seed_x", "seed_y", "value"])
        for frame in result.frames:
            for s in result.seeds:
                v = frame.values.get(s, frame.values.get(tuple(s), 0))
                writer.writerow([frame.step, s[0], s[1], v])
    return output_path


# ── Export: Animated SVG ────────────────────────────────────────────

def _value_to_color(value, model, vmin=0.0, vmax=1.0):
    """Map a value to an RGB color string based on model type."""
    if model == "sir":
        colors = {"S": "#4dabf7", "I": "#ff6b6b", "R": "#69db7c"}
        return colors.get(value, "#adb5bd")
    elif model == "threshold":
        return "#ff6b6b" if value == 1 else "#4dabf7"
    else:
        # Heat: blue (cold) -> red (hot)
        if vmax == vmin:
            t = 0.5
        else:
            t = max(0, min(1, (value - vmin) / (vmax - vmin)))
        r = int(66 + t * (255 - 66))
        g = int(133 + (1 - abs(2 * t - 1)) * (200 - 133))
        b = int(244 - t * (244 - 66))
        return f"#{r:02x}{g:02x}{b:02x}"


def export_diffusion_svg(
    result: DiffusionResult,
    regions: dict,
    data: list,
    output_path: str,
    *,
    width: int = 800,
    height: int = 600,
    frame_duration_ms: int = 200,
) -> str:
    """Export diffusion simulation as animated SVG.

    Each frame of the simulation is rendered as a CSS animation
    keyframe, producing a looping animation of the diffusion process.

    Parameters
    ----------
    result : DiffusionResult
        Output of any diffusion function.
    regions : dict
        Output of ``compute_regions()``.
    data : list
        Seed points.
    output_path : str
        Path to write the SVG file.
    width, height : int
        SVG canvas dimensions.
    frame_duration_ms : int
        Duration of each frame in milliseconds (default 200).

    Returns
    -------
    str
        The output path.
    """
    vormap.validate_output_path(output_path, allow_absolute=True)

    if not regions or not result.frames:
        return output_path

    # Compute bounds
    all_x = []
    all_y = []
    for seed, verts in regions.items():
        for vx, vy in verts:
            all_x.append(vx)
            all_y.append(vy)

    if not all_x:
        return output_path

    margin = 20
    data_min_x, data_max_x = min(all_x), max(all_x)
    data_min_y, data_max_y = min(all_y), max(all_y)
    data_w = data_max_x - data_min_x or 1
    data_h = data_max_y - data_min_y or 1
    scale = min((width - 2 * margin) / data_w, (height - 2 * margin) / data_h)

    def tx(x):
        return margin + (x - data_min_x) * scale

    def ty(y):
        return height - margin - (y - data_min_y) * scale

    # Compute global value range for heat model
    vmin = float("inf")
    vmax = float("-inf")
    if result.model == "heat":
        for frame in result.frames:
            for v in frame.values.values():
                if isinstance(v, (int, float)):
                    vmin = min(vmin, v)
                    vmax = max(vmax, v)
        if vmin == float("inf"):
            vmin, vmax = 0.0, 1.0

    num_frames = len(result.frames)
    total_duration_s = num_frames * frame_duration_ms / 1000.0

    # Build SVG
    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "viewBox": f"0 0 {width} {height}",
        "width": str(width),
        "height": str(height),
    })

    # Background
    ET.SubElement(svg, "rect", {
        "width": str(width), "height": str(height),
        "fill": "#1a1a2e",
    })

    # Title
    title = ET.SubElement(svg, "text", {
        "x": str(width // 2), "y": "18",
        "text-anchor": "middle",
        "font-family": "monospace", "font-size": "14",
        "fill": "#e0e0e0",
    })
    model_names = {"heat": "Heat Diffusion", "sir": "SIR Epidemic", "threshold": "Threshold Adoption"}
    title.text = f"{model_names.get(result.model, result.model)} ({num_frames} steps)"

    # Build CSS animation keyframes for each cell
    style_parts = []
    seed_to_id = {}
    for i, seed in enumerate(result.seeds):
        cell_id = f"cell-{i}"
        seed_to_id[seed] = cell_id

        keyframes = []
        for fi, frame in enumerate(result.frames):
            pct = (fi / max(num_frames - 1, 1)) * 100
            v = frame.values.get(seed, frame.values.get(tuple(seed), 0))
            color = _value_to_color(v, result.model, vmin, vmax)
            keyframes.append(f"  {pct:.1f}% {{ fill: {color}; }}")

        anim_name = f"diff-{i}"
        style_parts.append(f"@keyframes {anim_name} {{\n" + "\n".join(keyframes) + "\n}")
        style_parts.append(
            f"#{cell_id} {{ animation: {anim_name} {total_duration_s:.1f}s linear infinite; }}"
        )

    style_el = ET.SubElement(svg, "style")
    style_el.text = "\n".join(style_parts)

    # Draw cells
    for seed in result.seeds:
        if seed not in regions and tuple(seed) not in regions:
            continue
        verts = regions.get(seed, regions.get(tuple(seed), []))
        if len(verts) < 3:
            continue

        points_str = " ".join(f"{tx(vx):.1f},{ty(vy):.1f}" for vx, vy in verts)
        cell_id = seed_to_id.get(seed, seed_to_id.get(tuple(seed)))

        initial_v = result.frames[0].values.get(seed, 0)
        initial_color = _value_to_color(initial_v, result.model, vmin, vmax)

        attrs = {
            "points": points_str,
            "fill": initial_color,
            "stroke": "#ffffff",
            "stroke-width": "0.5",
            "opacity": "0.85",
        }
        if cell_id:
            attrs["id"] = cell_id
        ET.SubElement(svg, "polygon", attrs)

    # Legend
    legend_y = height - 15
    if result.model == "sir":
        labels = [("Susceptible", "#4dabf7"), ("Infected", "#ff6b6b"), ("Recovered", "#69db7c")]
        for li, (label, color) in enumerate(labels):
            lx = 10 + li * 120
            ET.SubElement(svg, "rect", {
                "x": str(lx), "y": str(legend_y - 8),
                "width": "12", "height": "12",
                "fill": color, "rx": "2",
            })
            leg_text = ET.SubElement(svg, "text", {
                "x": str(lx + 16), "y": str(legend_y + 2),
                "font-family": "monospace", "font-size": "11",
                "fill": "#e0e0e0",
            })
            leg_text.text = label
    elif result.model == "threshold":
        labels = [("Not adopted", "#4dabf7"), ("Adopted", "#ff6b6b")]
        for li, (label, color) in enumerate(labels):
            lx = 10 + li * 120
            ET.SubElement(svg, "rect", {
                "x": str(lx), "y": str(legend_y - 8),
                "width": "12", "height": "12",
                "fill": color, "rx": "2",
            })
            leg_text = ET.SubElement(svg, "text", {
                "x": str(lx + 16), "y": str(legend_y + 2),
                "font-family": "monospace", "font-size": "11",
                "fill": "#e0e0e0",
            })
            leg_text.text = label
    else:
        # Heat: gradient bar
        for gi in range(100):
            gx = 10 + gi * 2
            t = gi / 99
            gc = _value_to_color(vmin + t * (vmax - vmin), "heat", vmin, vmax)
            ET.SubElement(svg, "rect", {
                "x": str(gx), "y": str(legend_y - 8),
                "width": "2", "height": "12",
                "fill": gc,
            })
        low_text = ET.SubElement(svg, "text", {
            "x": "10", "y": str(legend_y + 16),
            "font-family": "monospace", "font-size": "10",
            "fill": "#aaa",
        })
        low_text.text = f"{vmin:.1f}"
        high_text = ET.SubElement(svg, "text", {
            "x": "210", "y": str(legend_y + 16),
            "font-family": "monospace", "font-size": "10",
            "fill": "#aaa",
        })
        high_text.text = f"{vmax:.1f}"

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)
    return output_path


# ── Format report ───────────────────────────────────────────────────

def format_report(result: DiffusionResult) -> str:
    """Format a human-readable report of the diffusion simulation.

    Parameters
    ----------
    result : DiffusionResult

    Returns
    -------
    str
        Multi-line text summary.
    """
    lines = [
        f"=== Spatial Diffusion: {result.model.upper()} ===",
        "",
        "Parameters:",
    ]
    for k, v in result.params.items():
        lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append("Summary:")
    for k, v in result.summary.items():
        if isinstance(v, float):
            lines.append(f"  {k}: {v:.4f}")
        elif isinstance(v, list):
            lines.append(f"  {k}: [{', '.join(f'{x:.2f}' for x in v)}]")
        else:
            lines.append(f"  {k}: {v}")

    lines.append("")
    lines.append(f"Cells: {len(result.seeds)}")
    lines.append(f"Frames: {len(result.frames)}")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    """Command-line interface for diffusion simulation."""
    parser = argparse.ArgumentParser(
        description="Spatial diffusion simulation on Voronoi networks",
    )
    parser.add_argument("datafile", help="Point data file")
    parser.add_argument(
        "samples", nargs="?", type=int, default=5,
        help="Number of samples for Voronoi estimation (default: 5)",
    )
    parser.add_argument("--model", choices=["heat", "sir", "threshold"],
                        default="heat", help="Diffusion model")
    parser.add_argument("--steps", type=int, default=50,
                        help="Number of simulation steps")
    parser.add_argument("--source", type=int, nargs="*", default=None,
                        help="Source cell indices (0-based)")
    # Heat params
    parser.add_argument("--alpha", type=float, default=0.1,
                        help="Heat diffusion coefficient (default: 0.1)")
    parser.add_argument("--boundary", choices=["insulated", "fixed"],
                        default="insulated", help="Heat boundary condition")
    parser.add_argument("--initial-value", type=float, default=100.0,
                        help="Initial value for source cells (heat model)")
    # SIR params
    parser.add_argument("--beta", type=float, default=0.3,
                        help="SIR transmission probability")
    parser.add_argument("--gamma", type=float, default=0.1,
                        help="SIR recovery probability")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    # Threshold params
    parser.add_argument("--threshold", type=float, default=0.4,
                        help="Threshold adoption fraction")
    # Export
    parser.add_argument("--json", dest="json_path", help="Export JSON")
    parser.add_argument("--csv", dest="csv_path", help="Export CSV")
    parser.add_argument("--svg", dest="svg_path", help="Export animated SVG")

    args = parser.parse_args()

    # Load data and compute regions
    data = vormap.load_data(args.datafile)
    if not data:
        print("Error: no data points loaded")
        return

    from vormap_viz import compute_regions
    from vormap_graph import extract_neighborhood_graph

    regions = compute_regions(data)
    graph_info = extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    source_indices = args.source if args.source else [0]

    # Run simulation
    if args.model == "heat":
        initial = {seed: 0.0 for seed in adjacency}
        for idx in source_indices:
            seeds_list = sorted(adjacency.keys())
            if 0 <= idx < len(seeds_list):
                initial[seeds_list[idx]] = args.initial_value
        result = heat_diffusion(
            initial, adjacency,
            steps=args.steps, alpha=args.alpha,
            boundary=args.boundary,
        )
    elif args.model == "sir":
        result = sir_simulation(
            adjacency,
            initial_infected=source_indices,
            beta=args.beta, gamma=args.gamma,
            steps=args.steps, seed=args.seed,
        )
    elif args.model == "threshold":
        result = threshold_diffusion(
            adjacency,
            initial_adopters=source_indices,
            threshold=args.threshold,
            steps=args.steps,
        )
    else:
        print(f"Unknown model: {args.model}")
        return

    # Report
    print(format_report(result))

    # Exports
    if args.json_path:
        export_diffusion_json(result, args.json_path)
        print(f"JSON: {args.json_path}")

    if args.csv_path:
        export_diffusion_csv(result, args.csv_path)
        print(f"CSV: {args.csv_path}")

    if args.svg_path:
        export_diffusion_svg(result, regions, data, args.svg_path)
        print(f"SVG: {args.svg_path}")


if __name__ == "__main__":
    main()
