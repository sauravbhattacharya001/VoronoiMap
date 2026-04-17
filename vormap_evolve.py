"""Evolutionary point placement optimizer for Voronoi diagrams.

Uses a genetic algorithm to autonomously evolve point configurations
toward user-specified spatial objectives.  The optimizer acts as an
intelligent agent: given a goal (uniform spacing, tight clustering,
maximum coverage, etc.) it breeds, mutates, and selects configurations
over many generations until the target fitness is reached or a
generation budget is exhausted.

Objectives:
- **uniform**   — minimise coefficient of variation of cell areas
- **clustered** — maximise nearest-neighbour distance variance (tight groups)
- **coverage**  — maximise convex hull area relative to bounding box
- **spread**    — maximise minimum nearest-neighbour distance
- **balanced**  — minimise max-minus-min cell area (equitable partitioning)

Each generation: evaluate fitness → tournament select → crossover → mutate → elitism.

Usage (Python API)::

    from vormap_evolve import evolve
    best = evolve(n_points=30, width=500, height=500,
                  objective="uniform", generations=200)
    print(f"Fitness: {best['fitness']:.4f}")
    # best["points"] is a list of (x, y)

CLI::

    python vormap_evolve.py --points 30 --objective uniform
    python vormap_evolve.py --points 50 --objective spread --generations 300
    python vormap_evolve.py --points 40 --objective coverage --out evolved.txt
    python vormap_evolve.py --points 25 --objective balanced --html report.html
    python vormap_evolve.py --points 20 --objective clustered --json
"""

import json
import math
import random
import sys
from copy import deepcopy

from vormap_utils import compute_nn_distances, euclidean
from vormap_hull import convex_hull
from vormap_geometry import polygon_area as _polygon_area

try:
    import numpy as np
    from scipy.spatial import KDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

# ---------------------------------------------------------------------------
# Geometry helpers — delegates to shared modules where possible
# ---------------------------------------------------------------------------


def _convex_hull_area(pts):
    """Convex hull area via shared vormap_hull module."""
    if len(pts) < 3:
        return 0.0
    result = convex_hull(pts)
    return _polygon_area(result.vertices) if result.vertices else 0.0


def _voronoi_cell_areas(pts, width, height):
    """Approximate cell areas via nearest-seed assignment.

    Uses scipy KDTree with vectorised numpy grid construction and
    ``np.bincount`` for O(n log n) grid queries when available,
    falling back to brute-force O(n × grid) otherwise.
    """
    n_pts = len(pts)
    step = max(2, int(min(width, height) / 80))

    if _HAS_SCIPY and n_pts > 4:
        # Vectorised grid via np.meshgrid — avoids slow Python list-of-tuples
        gx = np.arange(0, width, step, dtype=float)
        gy = np.arange(0, height, step, dtype=float)
        mx, my = np.meshgrid(gx, gy)
        grid = np.column_stack((mx.ravel(), my.ravel()))

        tree = KDTree(pts)
        _, indices = tree.query(grid)

        # bincount is O(total) with no Python loop overhead
        counts = np.bincount(indices, minlength=n_pts)
        total = len(grid)
        cell_area = (width * height) / total if total else 1
        return (counts * cell_area).tolist()
    else:
        counts = [0] * n_pts
        total = 0
        for gy in range(0, height, step):
            for gx in range(0, width, step):
                best_i = 0
                best_d = float("inf")
                for i, (px, py) in enumerate(pts):
                    d = (gx - px) ** 2 + (gy - py) ** 2
                    if d < best_d:
                        best_d = d
                        best_i = i
                counts[best_i] += 1
                total += 1
    cell_area = (width * height) / total if total else 1
    return [c * cell_area for c in counts]


# ---------------------------------------------------------------------------
# Fitness functions
# ---------------------------------------------------------------------------

def _fitness_uniform(pts, w, h):
    areas = _voronoi_cell_areas(pts, w, h)
    mean_a = sum(areas) / len(areas) if areas else 1
    std_a = math.sqrt(sum((a - mean_a) ** 2 for a in areas) / len(areas)) if areas else 0
    cv = std_a / mean_a if mean_a > 0 else 999
    return 1.0 / (1.0 + cv)  # higher is better


def _fitness_clustered(pts, _w, _h):
    nn = compute_nn_distances(pts)
    mean_d = sum(nn) / len(nn) if nn else 1
    var_d = sum((d - mean_d) ** 2 for d in nn) / len(nn) if nn else 0
    return var_d / (mean_d ** 2 + 1e-9)


def _fitness_coverage(pts, w, h):
    hull = _convex_hull_area(pts)
    box = w * h
    return hull / box if box > 0 else 0


def _fitness_spread(pts, _w, _h):
    nn = compute_nn_distances(pts)
    return min(nn) if nn else 0


def _fitness_balanced(pts, w, h):
    areas = _voronoi_cell_areas(pts, w, h)
    if not areas:
        return 0
    rng = max(areas) - min(areas)
    mean_a = sum(areas) / len(areas)
    return 1.0 / (1.0 + rng / (mean_a + 1e-9))


_OBJECTIVES = {
    "uniform": _fitness_uniform,
    "clustered": _fitness_clustered,
    "coverage": _fitness_coverage,
    "spread": _fitness_spread,
    "balanced": _fitness_balanced,
}

# ---------------------------------------------------------------------------
# Genetic operators
# ---------------------------------------------------------------------------

def _random_individual(n, w, h):
    return [(random.uniform(0, w), random.uniform(0, h)) for _ in range(n)]


def _mutate(ind, w, h, rate=0.15, sigma_frac=0.05):
    sigma_x = w * sigma_frac
    sigma_y = h * sigma_frac
    return [
        (
            max(0, min(w, x + random.gauss(0, sigma_x))),
            max(0, min(h, y + random.gauss(0, sigma_y))),
        )
        if random.random() < rate
        else (x, y)
        for x, y in ind
    ]


def _crossover(a, b):
    """Uniform crossover — pick each point from parent A or B."""
    return [a[i] if random.random() < 0.5 else b[i] for i in range(len(a))]


def _tournament_select(pop, fits, k=3):
    contestants = random.sample(list(zip(pop, fits)), k)
    return max(contestants, key=lambda x: x[1])[0]


# ---------------------------------------------------------------------------
# Main evolution loop
# ---------------------------------------------------------------------------

def evolve(n_points=30, width=500, height=500, objective="uniform",
           pop_size=40, generations=150, elitism=2, seed=None,
           on_generation=None, stale_limit=20):
    """Run the GA and return the best individual with metadata.

    Parameters
    ----------
    on_generation : callable, optional
        ``fn(gen, best_fitness)`` called every generation for progress.
    stale_limit : int
        Generations without improvement before adaptive mutation kicks
        in (doubles the mutation rate and sigma).  Resets when a new
        best is found.  Default 20.

    Returns
    -------
    dict  with keys: points, fitness, objective, generations_run, history
    """
    if seed is not None:
        random.seed(seed)
    fit_fn = _OBJECTIVES.get(objective)
    if fit_fn is None:
        raise ValueError(f"Unknown objective '{objective}'. Choose from: {list(_OBJECTIVES)}")

    pop = [_random_individual(n_points, width, height) for _ in range(pop_size)]
    # Track fitness alongside individuals so elites are not re-evaluated.
    # None means "needs evaluation"; a float means "cached".
    fits: list = [None] * pop_size
    history = []
    best_ever = -float("inf")
    stale_gens = 0  # generations since last improvement

    for gen in range(generations):
        # Evaluate only individuals whose fitness is not cached.
        for i in range(len(pop)):
            if fits[i] is None:
                fits[i] = fit_fn(pop[i], width, height)

        ranked = sorted(zip(pop, fits), key=lambda x: x[1], reverse=True)
        best_fit = ranked[0][1]
        history.append(best_fit)

        # Adaptive mutation: track stagnation
        if best_fit > best_ever + 1e-12:
            best_ever = best_fit
            stale_gens = 0
        else:
            stale_gens += 1

        if on_generation:
            on_generation(gen, best_fit)

        # Adaptive mutation parameters: boost when stuck in local optimum
        if stale_gens >= stale_limit:
            mut_rate = min(0.40, 0.15 * (1 + stale_gens / stale_limit))
            mut_sigma = min(0.15, 0.05 * (1 + stale_gens / stale_limit))
        else:
            mut_rate = 0.15
            mut_sigma = 0.05

        # Build next generation: elites carry over with cached fitness.
        new_pop = []
        new_fits: list = []
        for i in range(min(elitism, len(ranked))):
            new_pop.append(deepcopy(ranked[i][0]))
            new_fits.append(ranked[i][1])  # fitness is unchanged

        # Fill the rest with offspring (fitness unknown → None).
        all_pop = [r[0] for r in ranked]
        all_fits = [r[1] for r in ranked]
        while len(new_pop) < pop_size:
            p1 = _tournament_select(all_pop, all_fits)
            p2 = _tournament_select(all_pop, all_fits)
            child = _crossover(p1, p2)
            child = _mutate(child, width, height, rate=mut_rate,
                            sigma_frac=mut_sigma)
            new_pop.append(child)
            new_fits.append(None)  # needs evaluation next generation
        pop = new_pop
        fits = new_fits

    # Final evaluation for any unevaluated individuals.
    for i in range(len(pop)):
        if fits[i] is None:
            fits[i] = fit_fn(pop[i], width, height)

    best_idx = max(range(len(fits)), key=lambda i: fits[i])
    return {
        "points": pop[best_idx],
        "fitness": fits[best_idx],
        "objective": objective,
        "generations_run": generations,
        "history": history,
    }


# ---------------------------------------------------------------------------
# Text output
# ---------------------------------------------------------------------------

def _points_to_text(pts):
    return "\n".join(f"{x:.2f}\t{y:.2f}" for x, y in pts)


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _html_report(result, width, height):
    pts = result["points"]
    obj = result["objective"]
    fit = result["fitness"]
    hist = result["history"]

    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#2563eb" opacity="0.7"/>'
        for x, y in pts
    )

    # sparkline for fitness history
    if hist:
        mn, mx = min(hist), max(hist)
        rng = mx - mn if mx != mn else 1
        spark_pts = " ".join(
            f"{i * (580 / max(len(hist) - 1, 1)) + 10:.1f},"
            f"{190 - (h - mn) / rng * 170:.1f}"
            for i, h in enumerate(hist)
        )
        spark_svg = (
            f'<svg width="600" height="200" style="background:#f9fafb;border-radius:8px">'
            f'<polyline points="{spark_pts}" fill="none" stroke="#2563eb" stroke-width="2"/>'
            f'<text x="10" y="15" font-size="12" fill="#666">Fitness over generations</text>'
            f'</svg>'
        )
    else:
        spark_svg = ""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>VoronoiMap Evolve — {obj}</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;color:#1e293b}}
h1{{color:#2563eb}} .stats{{display:flex;gap:2em;margin:1em 0}}
.stat{{background:#f1f5f9;padding:1em 1.5em;border-radius:8px}}
.stat b{{display:block;font-size:1.4em;color:#2563eb}}
svg{{display:block;margin:1em 0}}
</style></head><body>
<h1>🧬 Evolutionary Point Placement</h1>
<div class="stats">
  <div class="stat"><b>{len(pts)}</b>Points</div>
  <div class="stat"><b>{obj}</b>Objective</div>
  <div class="stat"><b>{fit:.4f}</b>Best Fitness</div>
  <div class="stat"><b>{len(hist)}</b>Generations</div>
</div>
<h2>Best Configuration</h2>
<svg width="{width}" height="{height}" style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px">
{dots}
</svg>
<h2>Fitness Curve</h2>
{spark_svg}
</body></html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Evolutionary point placement optimizer for Voronoi diagrams"
    )
    parser.add_argument("--points", type=int, default=30, help="Number of points (default 30)")
    parser.add_argument("--width", type=int, default=500, help="Canvas width (default 500)")
    parser.add_argument("--height", type=int, default=500, help="Canvas height (default 500)")
    parser.add_argument(
        "--objective",
        choices=list(_OBJECTIVES),
        default="uniform",
        help="Optimisation objective (default uniform)",
    )
    parser.add_argument("--pop", type=int, default=40, help="Population size (default 40)")
    parser.add_argument("--generations", type=int, default=150, help="Generations (default 150)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--out", type=str, default=None, help="Save best points to text file")
    parser.add_argument("--html", type=str, default=None, help="Save HTML report")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    def _progress(gen, fit):
        if gen % 25 == 0:
            print(f"  gen {gen:>4d}  fitness {fit:.6f}")

    result = evolve(
        n_points=args.points,
        width=args.width,
        height=args.height,
        objective=args.objective,
        pop_size=args.pop,
        generations=args.generations,
        seed=args.seed,
        on_generation=_progress,
    )

    if args.json:
        out = {
            "objective": result["objective"],
            "fitness": result["fitness"],
            "generations": result["generations_run"],
            "points": [[round(x, 2), round(y, 2)] for x, y in result["points"]],
        }
        print(json.dumps(out, indent=2))
    else:
        print(f"\nBest fitness ({result['objective']}): {result['fitness']:.6f}")
        print(f"   Generations: {result['generations_run']}")
        print(f"   Points: {len(result['points'])}")

    if args.out:
        with open(args.out, "w") as f:
            f.write(_points_to_text(result["points"]))
        print(f"   Saved points → {args.out}")

    if args.html:
        with open(args.html, "w") as f:
            f.write(_html_report(result, args.width, args.height))
        print(f"   Saved report → {args.html}")


if __name__ == "__main__":
    main()
