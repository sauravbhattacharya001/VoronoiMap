"""Terrain Erosion Simulation over Voronoi Networks.

Simulates hydraulic and thermal erosion on a Voronoi tessellation where
each cell has an elevation.  Sediment is transported from higher cells
to lower neighbours based on slope, modelling terrain weathering over
discrete time steps.

Two erosion models are provided:

- **hydraulic** — water-driven erosion: material is removed from cells
  proportional to the slope to neighbours and carried downhill.  Models
  river valley formation, rainfall erosion.
- **thermal** — talus/thermal erosion: material collapses when the slope
  between neighbours exceeds a talus angle threshold.  Models rockfall,
  scree slope formation.

Both models produce a time-series of per-cell elevations that can be
exported as JSON, CSV, or animated SVG.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_erosion import (
        hydraulic_erosion, thermal_erosion,
        export_erosion_json, export_erosion_csv,
        export_erosion_svg,
    )

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    graph_info = vormap_viz.extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    # Assign random elevations
    import random
    elevations = {seed: random.uniform(0, 100) for seed in adjacency}

    # Hydraulic erosion
    frames = hydraulic_erosion(adjacency, elevations, steps=50, rate=0.05)

    # Thermal erosion
    frames = thermal_erosion(adjacency, elevations, steps=50, talus=5.0)

    # Export
    export_erosion_json(frames, "erosion.json")
    export_erosion_csv(frames, "erosion.csv")

CLI::

    python vormap_erosion.py --model hydraulic --steps 50 --rate 0.05 datauni5.txt -o erosion.json
    python vormap_erosion.py --model thermal --steps 50 --talus 5.0 datauni5.txt --svg erosion.svg
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import sys

import vormap

# ---------------------------------------------------------------------------
# Erosion models
# ---------------------------------------------------------------------------

def _run_erosion(
    adjacency: dict,
    elevations: dict,
    steps: int,
    step_fn,
) -> list[dict]:
    """Generic erosion simulation driver.

    Eliminates duplicated frame-management boilerplate between erosion
    models.  Each model only needs to supply a *step_fn(current, adjacency)*
    that returns a delta dict of elevation changes.

    Parameters
    ----------
    adjacency : dict
        {seed: [neighbour_seeds, …]}
    elevations : dict
        {seed: float} initial terrain heights.
    steps : int
        Number of simulation steps.
    step_fn : callable(current, adjacency) -> dict
        Computes per-seed elevation deltas for one time step.

    Returns
    -------
    list[dict]
        One dict per step mapping seed → elevation.
    """
    if steps < 0:
        raise ValueError("steps must be non-negative")

    current = {s: float(v) for s, v in elevations.items()}
    frames: list[dict] = [copy.deepcopy(current)]

    for _ in range(steps):
        delta = step_fn(current, adjacency)
        for s in current:
            current[s] = max(0.0, current[s] + delta.get(s, 0.0))
        frames.append(copy.deepcopy(current))

    return frames


def _hydraulic_step(rate: float, sediment_capacity: float):
    """Return a step function for hydraulic erosion."""
    def step_fn(current, adjacency):
        delta: dict[object, float] = {s: 0.0 for s in current}
        for seed, nbrs in adjacency.items():
            h = current[seed]
            lower = [(n, h - current[n]) for n in nbrs if current[n] < h]
            if not lower:
                continue
            total_slope = sum(d for _, d in lower)
            if total_slope <= 0:
                continue
            eroded = min(rate * total_slope, sediment_capacity, h * 0.5)
            delta[seed] -= eroded
            for nbr, slope in lower:
                fraction = slope / total_slope
                delta[nbr] += eroded * fraction
        return delta
    return step_fn


def _thermal_step(talus: float, fraction: float):
    """Return a step function for thermal erosion."""
    def step_fn(current, adjacency):
        delta: dict[object, float] = {s: 0.0 for s in current}
        for seed, nbrs in adjacency.items():
            h = current[seed]
            for nbr in nbrs:
                diff = h - current[nbr]
                if diff > talus:
                    transfer = fraction * (diff - talus) * 0.5
                    delta[seed] -= transfer
                    delta[nbr] += transfer
        return delta
    return step_fn


def hydraulic_erosion(
    adjacency: dict,
    elevations: dict,
    *,
    steps: int = 50,
    rate: float = 0.05,
    sediment_capacity: float = 0.8,
) -> list[dict]:
    """Simulate hydraulic (water-driven) erosion.

    At each step, for every cell we look at lower neighbours.  Material is
    removed proportional to *rate* × (elevation difference) and deposited
    on lower neighbours, capped by *sediment_capacity* per step.

    Parameters
    ----------
    adjacency : dict
        {seed: [neighbour_seeds, …]}
    elevations : dict
        {seed: float} initial terrain heights.
    steps : int
        Number of simulation steps.
    rate : float
        Erosion rate coefficient (0–1).
    sediment_capacity : float
        Max sediment a cell can shed per step.

    Returns
    -------
    list[dict]
        One dict per step mapping seed → elevation.
    """
    if not (0 < rate <= 1):
        raise ValueError("rate must be in (0, 1]")
    return _run_erosion(adjacency, elevations, steps,
                        _hydraulic_step(rate, sediment_capacity))


def thermal_erosion(
    adjacency: dict,
    elevations: dict,
    *,
    steps: int = 50,
    talus: float = 5.0,
    fraction: float = 0.5,
) -> list[dict]:
    """Simulate thermal / talus erosion.

    Material collapses from a cell to a neighbour when the height
    difference exceeds the *talus* angle threshold.  A *fraction* of
    the excess is transferred.

    Parameters
    ----------
    adjacency : dict
        {seed: [neighbour_seeds, …]}
    elevations : dict
        {seed: float} initial terrain heights.
    steps : int
        Number of simulation steps.
    talus : float
        Maximum stable height difference.
    fraction : float
        Fraction of excess height transferred (0–1).

    Returns
    -------
    list[dict]
        One dict per step mapping seed → elevation.
    """
    if talus <= 0:
        raise ValueError("talus must be positive")
    if not (0 < fraction <= 1):
        raise ValueError("fraction must be in (0, 1]")
    return _run_erosion(adjacency, elevations, steps,
                        _thermal_step(talus, fraction))


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_erosion_json(frames: list[dict], path: str) -> None:
    """Export erosion frames to JSON."""
    path = vormap.validate_output_path(path, allow_absolute=True)
    serializable = []
    for frame in frames:
        serializable.append({str(k): round(v, 4) for k, v in frame.items()})
    with open(path, "w") as f:
        json.dump({"steps": len(frames), "frames": serializable}, f, indent=1)


def export_erosion_csv(frames: list[dict], path: str) -> None:
    """Export erosion frames to CSV (step, seed, elevation)."""
    path = vormap.validate_output_path(path, allow_absolute=True)
    with open(path, "w") as f:
        f.write("step,seed,elevation\n")
        for i, frame in enumerate(frames):
            for seed, elev in sorted(frame.items(), key=lambda x: str(x[0])):
                f.write(f"{i},{seed},{elev:.4f}\n")


def _cell_color(elevation: float, max_elev: float) -> str:
    """Map elevation to a green-to-brown color."""
    if max_elev <= 0:
        return "#228B22"
    t = min(elevation / max_elev, 1.0)
    # Green (low) → Brown (high)
    r = int(34 + t * (139 - 34))
    g = int(139 + t * (90 - 139))
    b = int(34 + t * (43 - 34))
    return f"#{r:02x}{g:02x}{b:02x}"


def export_erosion_svg(
    frames: list[dict],
    regions: dict,
    data: list,
    path: str,
    *,
    width: int = 800,
    height: int = 600,
    duration: float = 0.3,
) -> None:
    """Export animated SVG showing erosion over time.

    Each Voronoi cell is colored by elevation (green=low, brown=high)
    and the animation steps through frames.
    """
    if not frames:
        raise ValueError("No frames to export")

    seeds = list(frames[0].keys())
    max_elev = max(max(f.values()) for f in frames) or 1.0

    # Compute bounding box from data
    xs = [p[0] for p in data]
    ys = [p[1] for p in data]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    x_range = max_x - min_x or 1
    y_range = max_y - min_y or 1

    def tx(x):
        return 10 + (x - min_x) / x_range * (width - 20)

    def ty(y):
        return 10 + (max_y - y) / y_range * (height - 20)

    total_dur = len(frames) * duration

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" fill="#1a1a2e"/>',
    ]

    for seed in seeds:
        if seed not in regions:
            continue
        verts = regions[seed]
        if not verts:
            continue
        points = " ".join(f"{tx(v[0]):.1f},{ty(v[1]):.1f}" for v in verts)
        colors = ";".join(_cell_color(frames[i].get(seed, 0), max_elev) for i in range(len(frames)))
        lines.append(f'<polygon points="{points}" stroke="#333" stroke-width="0.5">')
        lines.append(
            f'  <animate attributeName="fill" values="{colors}" '
            f'dur="{total_dur:.1f}s" repeatCount="indefinite"/>'
        )
        lines.append("</polygon>")

    lines.append("</svg>")

    path = vormap.validate_output_path(path, allow_absolute=True)
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Terrain erosion simulation on Voronoi tessellations."
    )
    p.add_argument("input", help="Point data file")
    p.add_argument("--model", choices=["hydraulic", "thermal"], default="hydraulic")
    p.add_argument("--steps", type=int, default=50)
    p.add_argument("--rate", type=float, default=0.05, help="Hydraulic erosion rate")
    p.add_argument("--talus", type=float, default=5.0, help="Thermal talus threshold")
    p.add_argument("--fraction", type=float, default=0.5, help="Thermal transfer fraction")
    p.add_argument("--seed", type=int, default=42, help="Random seed for initial elevations")
    p.add_argument("--max-elevation", type=float, default=100.0)
    p.add_argument("-o", "--output", help="Output JSON file")
    p.add_argument("--csv", help="Output CSV file")
    p.add_argument("--svg", help="Output animated SVG file")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    import random as _random
    _random.seed(args.seed)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import vormap
    import vormap_viz

    data = vormap.load_data(args.input)
    regions = vormap_viz.compute_regions(data)
    graph_info = vormap_viz.extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    elevations = {seed: _random.uniform(0, args.max_elevation) for seed in adjacency}

    if args.model == "hydraulic":
        frames = hydraulic_erosion(adjacency, elevations, steps=args.steps, rate=args.rate)
    else:
        frames = thermal_erosion(
            adjacency, elevations, steps=args.steps,
            talus=args.talus, fraction=args.fraction,
        )

    print(f"Erosion simulation complete: {args.model}, {len(frames)} frames, {len(elevations)} cells")

    if args.output:
        export_erosion_json(frames, args.output)
        print(f"JSON → {args.output}")

    if args.csv:
        export_erosion_csv(frames, args.csv)
        print(f"CSV  → {args.csv}")

    if args.svg:
        export_erosion_svg(frames, regions, data, args.svg)
        print(f"SVG  → {args.svg}")

    if not args.output and not args.csv and not args.svg:
        # Print summary
        initial = frames[0]
        final = frames[-1]
        avg_i = sum(initial.values()) / len(initial)
        avg_f = sum(final.values()) / len(final)
        print(f"Average elevation: {avg_i:.2f} → {avg_f:.2f}")
        print(f"Max elevation: {max(initial.values()):.2f} → {max(final.values()):.2f}")
        print(f"Min elevation: {min(initial.values()):.2f} → {min(final.values()):.2f}")


if __name__ == "__main__":
    main()
