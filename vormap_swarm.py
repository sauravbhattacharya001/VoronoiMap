"""Autonomous Particle Swarm Optimizer for Voronoi Point Layouts.

Uses Particle Swarm Optimization (PSO) to find optimal point placements
for various spatial objectives on a 2-D canvas.  Produces an interactive
HTML report with before/after Voronoi diagrams, fitness curve, and
proactive recommendations.

Objectives:
    * **max_spread**      — maximize minimum pairwise distance
    * **min_energy**      — minimize total pairwise repulsion energy (Σ 1/d)
    * **uniform_density** — minimize CV of nearest-neighbor distances
    * **coverage**        — minimize std-dev of Voronoi cell areas
    * **cluster_balance** — minimize imbalance across k-means clusters

Autonomous features:
    * **Auto-objective** (``--auto``) — quick pre-scan picks the objective
      with most room for improvement
    * **Adaptive swarm** — particle count auto-scales for large point sets
    * **Stagnation rescue** — random perturbation when progress stalls
    * **Convergence report** — improvement %, convergence iteration, tips

CLI examples::

    python vormap_swarm.py --objective max_spread --points 25 --output swarm.html
    python vormap_swarm.py --auto --points 30 --output result.html
    python vormap_swarm.py --points 20 --particles 40 --iterations 200

Programmatic::

    from vormap_swarm import SwarmOptimizer, export_html

    opt = SwarmOptimizer(n_points=20, objective="max_spread")
    result = opt.run()
    export_html(result, "output.html")
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from vormap_utils import euclidean as _dist

# ── Geometry helpers ────────────────────────────────────────────────

def _random_points(n: int, w: float, h: float) -> List[Tuple[float, float]]:
    return [(random.uniform(0, w), random.uniform(0, h)) for _ in range(n)]


def _nearest_neighbor_dists(pts: List[Tuple[float, float]]) -> List[float]:
    """O(n²) nearest-neighbor distances using squared-distance comparisons.

    Only computes sqrt once per point (for the final minimum), avoiding
    n-1 redundant sqrt calls per point.
    """
    _sqrt = math.sqrt
    n = len(pts)
    dists = []
    for i in range(n):
        pix, piy = pts[i]
        mn_sq = float("inf")
        for j in range(n):
            if i == j:
                continue
            dx = pix - pts[j][0]
            dy = piy - pts[j][1]
            d_sq = dx * dx + dy * dy
            if d_sq < mn_sq:
                mn_sq = d_sq
        dists.append(_sqrt(mn_sq))
    return dists


def _voronoi_areas(pts: List[Tuple[float, float]], w: float, h: float,
                   res: int = 80) -> List[float]:
    """Estimate Voronoi cell areas via grid rasterisation.

    Pre-extracts x/y into parallel lists to eliminate tuple unpacking
    and enumerate overhead in the O(res² × n) inner loop.
    """
    n = len(pts)
    counts = [0] * n
    sx = w / res
    sy = h / res
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    for gy in range(res):
        py = (gy + 0.5) * sy
        for gx in range(res):
            px = (gx + 0.5) * sx
            best_i = 0
            best_d = float("inf")
            for i in range(n):
                dx = px - xs[i]
                dy = py - ys[i]
                d = dx * dx + dy * dy
                if d < best_d:
                    best_d = d
                    best_i = i
            counts[best_i] += 1
    cell_area = sx * sy
    return [c * cell_area for c in counts]


# ── Objective functions ─────────────────────────────────────────────
# All are *minimisation* targets.  max_spread is negated min-dist.

def _obj_max_spread(pts: List[Tuple[float, float]], w: float, h: float) -> float:
    """Maximize minimum pairwise distance.

    Uses squared distances throughout to eliminate O(n²) sqrt calls;
    only one sqrt at the end for the final minimum distance.
    """
    mn_sq = float("inf")
    n = len(pts)
    for i in range(n):
        pix, piy = pts[i]
        for j in range(i + 1, n):
            dx = pix - pts[j][0]
            dy = piy - pts[j][1]
            d_sq = dx * dx + dy * dy
            if d_sq < mn_sq:
                mn_sq = d_sq
    return -math.sqrt(mn_sq)  # minimise negative → maximise min-dist


def _obj_min_energy(pts: List[Tuple[float, float]], w: float, h: float) -> float:
    """Minimize total pairwise repulsion energy (Σ 1/d).

    Inlines distance computation and uses 1/sqrt(d_sq) to avoid
    the overhead of math.hypot per pair.
    """
    _sqrt = math.sqrt
    total = 0.0
    n = len(pts)
    for i in range(n):
        pix, piy = pts[i]
        for j in range(i + 1, n):
            dx = pix - pts[j][0]
            dy = piy - pts[j][1]
            d_sq = dx * dx + dy * dy
            if d_sq > 1e-18:  # equivalent to d > 1e-9
                total += 1.0 / _sqrt(d_sq)
    return total


def _obj_uniform_density(pts: List[Tuple[float, float]], w: float, h: float) -> float:
    nn = _nearest_neighbor_dists(pts)
    mean = sum(nn) / len(nn)
    if mean < 1e-9:
        return 1e9
    var = sum((v - mean) ** 2 for v in nn) / len(nn)
    return math.sqrt(var) / mean  # CV


def _obj_coverage(pts: List[Tuple[float, float]], w: float, h: float) -> float:
    areas = _voronoi_areas(pts, w, h)
    mean = sum(areas) / len(areas)
    if mean < 1e-9:
        return 1e9
    var = sum((a - mean) ** 2 for a in areas) / len(areas)
    return math.sqrt(var) / mean


def _obj_cluster_balance(pts: List[Tuple[float, float]], w: float, h: float) -> float:
    k = max(2, len(pts) // 5)
    nc = min(k, len(pts))
    # simple k-means (10 iterations) with inlined distance + parallel coord arrays
    centroids = list(random.sample(pts, nc))
    cx_arr = [c[0] for c in centroids]
    cy_arr = [c[1] for c in centroids]
    buckets: Dict[int, List[Tuple[float, float]]] = defaultdict(list)
    for _ in range(10):
        buckets.clear()
        for p in pts:
            px, py = p
            best_c = 0
            best_d = float("inf")
            for ci in range(nc):
                dx = px - cx_arr[ci]
                dy = py - cy_arr[ci]
                d = dx * dx + dy * dy
                if d < best_d:
                    best_d = d
                    best_c = ci
            buckets[best_c].append(p)
        for ci in range(nc):
            b = buckets.get(ci)
            if b:
                nb = len(b)
                cx_arr[ci] = sum(p[0] for p in b) / nb
                cy_arr[ci] = sum(p[1] for p in b) / nb
    sizes = [len(buckets.get(c, [])) for c in range(nc)]
    mean = sum(sizes) / len(sizes)
    if mean < 1e-9:
        return 1e9
    var = sum((s - mean) ** 2 for s in sizes) / len(sizes)
    return math.sqrt(var) / mean


OBJECTIVES = {
    "max_spread": _obj_max_spread,
    "min_energy": _obj_min_energy,
    "uniform_density": _obj_uniform_density,
    "coverage": _obj_coverage,
    "cluster_balance": _obj_cluster_balance,
}

# ── PSO Engine ──────────────────────────────────────────────────────

class SwarmOptimizer:
    """Particle Swarm Optimizer for 2-D point layouts."""

    def __init__(self, n_points: int = 20, objective: Optional[str] = None,
                 n_particles: int = 30, n_iterations: int = 100,
                 width: float = 800, height: float = 600,
                 auto: bool = False, seed: Optional[int] = None):
        self.n_points = n_points
        self.width = width
        self.height = height
        self.n_particles = max(n_particles, n_points) if n_points > 30 else n_particles
        self.n_iterations = n_iterations
        self.auto = auto or (objective is None)
        self.obj_name = objective or "max_spread"
        self._seed = seed
        if seed is not None:
            random.seed(seed)
        self.initial_points: List[Tuple[float, float]] = _random_points(n_points, width, height)

    # ── helpers ──

    def _decode(self, flat: List[float]) -> List[Tuple[float, float]]:
        return [(flat[i * 2], flat[i * 2 + 1]) for i in range(self.n_points)]

    def _encode(self, pts: List[Tuple[float, float]]) -> List[float]:
        out: List[float] = []
        for x, y in pts:
            out.extend([x, y])
        return out

    def _clamp(self, pos: List[float]) -> None:
        for i in range(0, len(pos), 2):
            pos[i] = max(0, min(self.width, pos[i]))
            pos[i + 1] = max(0, min(self.height, pos[i + 1]))

    def _evaluate(self, flat: List[float], obj_fn) -> float:
        return obj_fn(self._decode(flat), self.width, self.height)

    # ── auto-objective ──

    def _auto_pick(self) -> str:
        base = self._encode(self.initial_points)
        scores: Dict[str, float] = {}
        for name, fn in OBJECTIVES.items():
            # quick 10-iteration mini-swarm
            best = self._evaluate(base, fn)
            for _ in range(10):
                trial = [v + random.gauss(0, 20) for v in base]
                self._clamp(trial)
                s = self._evaluate(trial, fn)
                if s < best:
                    best = s
            orig = self._evaluate(base, fn)
            improvement = (orig - best) / (abs(orig) + 1e-9)
            scores[name] = improvement
        pick = max(scores, key=lambda k: scores[k])
        return pick

    # ── main run ──

    def run(self) -> Dict[str, Any]:
        if self.auto:
            self.obj_name = self._auto_pick()

        obj_fn = OBJECTIVES[self.obj_name]
        dim = self.n_points * 2
        w_max, w_min = 0.9, 0.4
        c1, c2 = 2.0, 2.0

        # initialise particles
        positions: List[List[float]] = []
        velocities: List[List[float]] = []
        p_best_pos: List[List[float]] = []
        p_best_val: List[float] = []

        for _ in range(self.n_particles):
            pos = self._encode(_random_points(self.n_points, self.width, self.height))
            vel = [random.uniform(-5, 5) for _ in range(dim)]
            positions.append(pos)
            velocities.append(vel)
            val = self._evaluate(pos, obj_fn)
            p_best_pos.append(list(pos))
            p_best_val.append(val)

        g_best_idx = min(range(self.n_particles), key=lambda i: p_best_val[i])
        g_best_pos = list(p_best_pos[g_best_idx])
        g_best_val = p_best_val[g_best_idx]

        history: List[float] = [g_best_val]
        stagnation = 0
        converged_iter = self.n_iterations

        initial_fitness = self._evaluate(self._encode(self.initial_points), obj_fn)

        for it in range(self.n_iterations):
            w = w_max - (w_max - w_min) * it / max(1, self.n_iterations - 1)
            improved = False

            for pi in range(self.n_particles):
                for d in range(dim):
                    r1, r2 = random.random(), random.random()
                    velocities[pi][d] = (w * velocities[pi][d]
                                         + c1 * r1 * (p_best_pos[pi][d] - positions[pi][d])
                                         + c2 * r2 * (g_best_pos[d] - positions[pi][d]))
                    positions[pi][d] += velocities[pi][d]
                self._clamp(positions[pi])

                val = self._evaluate(positions[pi], obj_fn)
                if val < p_best_val[pi]:
                    p_best_val[pi] = val
                    p_best_pos[pi] = list(positions[pi])
                    if val < g_best_val:
                        g_best_val = val
                        g_best_pos = list(positions[pi])
                        improved = True

            history.append(g_best_val)

            if improved:
                stagnation = 0
            else:
                stagnation += 1

            if stagnation >= 15:
                # perturb worst 25%
                ranked = sorted(range(self.n_particles), key=lambda i: p_best_val[i])
                worst = ranked[-(self.n_particles // 4):]
                for wi in worst:
                    positions[wi] = self._encode(
                        _random_points(self.n_points, self.width, self.height))
                    velocities[wi] = [random.uniform(-5, 5) for _ in range(dim)]
                stagnation = 0

            if converged_iter == self.n_iterations and stagnation == 0 and it > 5:
                converged_iter = it

        best_points = self._decode(g_best_pos)
        improvement = (initial_fitness - g_best_val) / (abs(initial_fitness) + 1e-9) * 100

        recommendations = self._recommendations(improvement, converged_iter)

        return {
            "objective": self.obj_name,
            "auto_selected": self.auto,
            "n_points": self.n_points,
            "n_particles": self.n_particles,
            "n_iterations": self.n_iterations,
            "width": self.width,
            "height": self.height,
            "initial_points": self.initial_points,
            "best_points": best_points,
            "initial_fitness": initial_fitness,
            "best_fitness": g_best_val,
            "improvement_pct": round(improvement, 2),
            "converged_iteration": converged_iter,
            "history": history,
            "recommendations": recommendations,
        }

    def _recommendations(self, improvement: float, conv_iter: int) -> List[str]:
        tips = []
        if improvement < 5:
            tips.append("Low improvement — try more iterations or particles.")
        if conv_iter < self.n_iterations * 0.3:
            tips.append("Converged early — consider fewer iterations to save time.")
        if conv_iter >= self.n_iterations:
            tips.append("Did not fully converge — increase iterations for better results.")
        if self.n_points > 40:
            tips.append("Large point set — consider coverage or uniform_density objectives.")
        if self.obj_name == "max_spread":
            tips.append("For spread layouts, try combining with Lloyd relaxation (vormap_relax).")
        if self.obj_name == "coverage":
            tips.append("Compare results with vormap_evolve for evolutionary optimisation.")
        if not tips:
            tips.append("Good convergence! Experiment with different objectives for variety.")
        return tips


# ── HTML export ─────────────────────────────────────────────────────

def export_html(result: Dict[str, Any], path: str) -> None:
    """Write a self-contained interactive HTML report."""
    w = result["width"]
    h = result["height"]
    init_json = json.dumps(result["initial_points"])
    best_json = json.dumps(result["best_points"])
    hist_json = json.dumps(result["history"])
    recs_html = "".join(f"<li>{r}</li>" for r in result["recommendations"])

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Swarm Optimizer — {result["objective"]}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px}}
h1{{text-align:center;margin-bottom:10px;color:#58a6ff}}
.row{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;margin:16px 0}}
canvas{{background:#161b22;border:1px solid #30363d;border-radius:8px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;min-width:260px}}
.card h3{{color:#58a6ff;margin-bottom:8px}}
table{{width:100%;border-collapse:collapse}}
td,th{{padding:4px 8px;text-align:left;border-bottom:1px solid #21262d}}
th{{color:#8b949e}}
.recs li{{margin:4px 0;color:#d2a8ff}}
.btn{{background:#238636;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;margin:4px}}
.btn:hover{{background:#2ea043}}
</style></head><body>
<h1>🐝 Swarm Optimizer — {result["objective"]}</h1>
<p style="text-align:center;color:#8b949e">{result["n_points"]} points · {result["n_particles"]} particles · {result["n_iterations"]} iterations
{"· auto-selected" if result["auto_selected"] else ""}</p>
<div class="row">
<div><h3 style="text-align:center;color:#8b949e">Before</h3>
<canvas id="cBefore" width="{int(w)}" height="{int(h)}"></canvas></div>
<div><h3 style="text-align:center;color:#8b949e">After</h3>
<canvas id="cAfter" width="{int(w)}" height="{int(h)}"></canvas></div>
</div>
<div class="row">
<div class="card" style="min-width:500px">
<h3>📈 Fitness Curve</h3>
<canvas id="cChart" width="500" height="200"></canvas>
</div>
<div class="card">
<h3>📊 Stats</h3>
<table>
<tr><th>Objective</th><td>{result["objective"]}</td></tr>
<tr><th>Initial Fitness</th><td>{result["initial_fitness"]:.6f}</td></tr>
<tr><th>Best Fitness</th><td>{result["best_fitness"]:.6f}</td></tr>
<tr><th>Improvement</th><td>{result["improvement_pct"]}%</td></tr>
<tr><th>Converged At</th><td>Iteration {result["converged_iteration"]}</td></tr>
</table>
</div>
<div class="card">
<h3>💡 Recommendations</h3>
<ul class="recs">{recs_html}</ul>
</div>
</div>
<script>
const W={int(w)},H={int(h)};
const init={init_json};
const best={best_json};
const hist={hist_json};
function voronoi(ctx,pts,w,h){{
  const img=ctx.createImageData(w,h);
  const colors=pts.map((_,i)=>[(i*67+80)%256,(i*131+100)%256,(i*199+120)%256]);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){{
    let mi=0,md=1e18;
    for(let i=0;i<pts.length;i++){{
      const d=(x-pts[i][0])**2+(y-pts[i][1])**2;
      if(d<md){{md=d;mi=i}}
    }}
    const o=(y*w+x)*4;
    img.data[o]=colors[mi][0];img.data[o+1]=colors[mi][1];
    img.data[o+2]=colors[mi][2];img.data[o+3]=200;
  }}
  ctx.putImageData(img,0,0);
  ctx.fillStyle="#fff";
  pts.forEach(p=>{{ctx.beginPath();ctx.arc(p[0],p[1],3,0,Math.PI*2);ctx.fill()}});
}}
voronoi(document.getElementById("cBefore").getContext("2d"),init,W,H);
voronoi(document.getElementById("cAfter").getContext("2d"),best,W,H);
// chart
(()=>{{
  const c=document.getElementById("cChart"),ctx=c.getContext("2d");
  const cw=c.width,ch=c.height,pad=40;
  const mn=Math.min(...hist),mx=Math.max(...hist);
  const rng=mx-mn||1;
  ctx.strokeStyle="#30363d";ctx.beginPath();
  ctx.moveTo(pad,pad);ctx.lineTo(pad,ch-pad);ctx.lineTo(cw-pad,ch-pad);ctx.stroke();
  ctx.strokeStyle="#58a6ff";ctx.lineWidth=2;ctx.beginPath();
  hist.forEach((v,i)=>{{
    const x=pad+i/(hist.length-1)*(cw-2*pad);
    const y=ch-pad-(v-mn)/rng*(ch-2*pad);
    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
  }});ctx.stroke();
  ctx.fillStyle="#8b949e";ctx.font="11px system-ui";
  ctx.fillText("Iteration",cw/2-20,ch-5);
  ctx.save();ctx.translate(10,ch/2);ctx.rotate(-Math.PI/2);
  ctx.fillText("Fitness",0,0);ctx.restore();
}})();
</script></body></html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Autonomous Particle Swarm Optimizer for Voronoi layouts")
    ap.add_argument("--objective", choices=list(OBJECTIVES.keys()),
                    help="Optimisation objective (omit for auto)")
    ap.add_argument("--auto", action="store_true",
                    help="Auto-select best objective")
    ap.add_argument("--points", type=int, default=20,
                    help="Number of points (default 20)")
    ap.add_argument("--particles", type=int, default=30,
                    help="Swarm size (default 30)")
    ap.add_argument("--iterations", type=int, default=100,
                    help="PSO iterations (default 100)")
    ap.add_argument("--width", type=float, default=800)
    ap.add_argument("--height", type=float, default=600)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--output", "-o", default=None,
                    help="HTML report path")
    args = ap.parse_args()

    opt = SwarmOptimizer(
        n_points=args.points,
        objective=args.objective,
        n_particles=args.particles,
        n_iterations=args.iterations,
        width=args.width,
        height=args.height,
        auto=args.auto or args.objective is None,
        seed=args.seed,
    )

    print(f"[Swarm] Optimizer -- {opt.n_points} points, "
          f"{opt.n_particles} particles, {opt.n_iterations} iterations")
    if opt.auto:
        print("   Auto-selecting objective ...")

    result = opt.run()

    print(f"   Objective : {result['objective']}"
          f"{'  (auto-selected)' if result['auto_selected'] else ''}")
    print(f"   Fitness   : {result['initial_fitness']:.6f} -> {result['best_fitness']:.6f}")
    print(f"   Improvement: {result['improvement_pct']}%")
    print(f"   Converged : iteration {result['converged_iteration']}")
    for r in result["recommendations"]:
        print(f"   * {r}")

    if args.output:
        export_html(result, args.output)
        print(f"   📄 Report: {args.output}")


if __name__ == "__main__":
    main()
