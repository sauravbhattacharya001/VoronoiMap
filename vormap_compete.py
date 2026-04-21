"""Territorial Competition Simulator for Voronoi Diagrams.

Simulates autonomous agents competing for spatial dominance on a
Voronoi tessellation.  Each competitor owns cells, accumulates
resources, and decides each turn whether to expand, defend, or
consolidate — producing an animated HTML replay with analytics.

Strategies:
    * **aggressive**  — always attacks the weakest adjacent enemy
    * **defensive**    — fortifies owned border cells first
    * **opportunistic** — grabs neutral cells before fighting
    * **balanced**     — mixed expand / fortify each turn
    * **random**       — picks a random valid action

Example CLI::

    python vormap_compete.py --competitors 4 --rounds 50 \\
        --strategies aggressive,defensive,balanced,opportunistic \\
        --width 800 --height 600 --output competition.html

Programmatic::

    from vormap_compete import simulate_competition, export_html

    result = simulate_competition(n_competitors=4, rounds=40)
    export_html(result, "competition.html")
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# ── Geometry helpers ────────────────────────────────────────────────

def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _random_points(n: int, w: float, h: float,
                   min_sep: float = 0.0) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    for _ in range(n * 50):
        p = (random.uniform(0, w), random.uniform(0, h))
        if min_sep > 0 and any(_distance(p, q) < min_sep for q in pts):
            continue
        pts.append(p)
        if len(pts) == n:
            break
    while len(pts) < n:
        pts.append((random.uniform(0, w), random.uniform(0, h)))
    return pts


# ── Region / cell model ────────────────────────────────────────────

class Cell:
    """One Voronoi region in the competition grid."""
    __slots__ = ("idx", "center", "owner", "strength", "resource",
                 "neighbors")

    def __init__(self, idx: int, center: Tuple[float, float],
                 resource: float):
        self.idx = idx
        self.center = center
        self.owner: Optional[int] = None      # competitor id or None
        self.strength: float = 0.0
        self.resource: float = resource
        self.neighbors: List[int] = []


def _build_cells(points: List[Tuple[float, float]],
                 w: float, h: float,
                 grid_res: int = 80) -> List[Cell]:
    """Build Voronoi cells and neighbor graph via grid rasterisation.

    Uses spatial bucketing so each grid pixel only checks nearby seed
    points instead of all *n* seeds, reducing the inner loop from
    O(grid_res² · n) to roughly O(grid_res² · k) where k ≪ n.
    """
    n = len(points)
    # --- spatial bucket index for seed points --------------------------------
    # Bucket size chosen so the expected number of seeds per bucket is small.
    _bk = max(1, int(math.sqrt(n) * 0.8))  # buckets per axis
    bw, bh = w / _bk, h / _bk
    buckets: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, (px, py) in enumerate(points):
        bx = min(int(px / bw), _bk - 1)
        by = min(int(py / bh), _bk - 1)
        buckets[(bx, by)].append(i)

    # assign each grid pixel to nearest point using bucket lookup
    owner_grid = [[0] * grid_res for _ in range(grid_res)]
    dx, dy = w / grid_res, h / grid_res
    # search radius in buckets — 2 is generous for typical distributions
    _SR = 2
    for gy in range(grid_res):
        cy = (gy + 0.5) * dy
        by0 = min(int(cy / bh), _bk - 1)
        for gx in range(grid_res):
            cx = (gx + 0.5) * dx
            bx0 = min(int(cx / bw), _bk - 1)
            best, bd = 0, float("inf")
            for by in range(max(0, by0 - _SR), min(_bk, by0 + _SR + 1)):
                for bx in range(max(0, bx0 - _SR), min(_bk, bx0 + _SR + 1)):
                    for i in buckets.get((bx, by), ()):
                        d = (cx - points[i][0]) ** 2 + (cy - points[i][1]) ** 2
                        if d < bd:
                            bd, best = d, i
            owner_grid[gy][gx] = best

    # discover neighbors
    adj: Dict[int, set] = defaultdict(set)
    for gy in range(grid_res):
        for gx in range(grid_res):
            c = owner_grid[gy][gx]
            for ddy, ddx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = gy + ddy, gx + ddx
                if 0 <= ny < grid_res and 0 <= nx < grid_res:
                    nb = owner_grid[ny][nx]
                    if nb != c:
                        adj[c].add(nb)

    cells = []
    for i, p in enumerate(points):
        res = random.uniform(1.0, 5.0)
        c = Cell(i, p, res)
        c.neighbors = sorted(adj.get(i, set()))
        cells.append(c)
    return cells


# ── Strategies ──────────────────────────────────────────────────────

STRATEGIES = ("aggressive", "defensive", "opportunistic", "balanced",
              "random")

_STRAT_ATTACK_BONUS = {
    "aggressive": 1.3,
    "defensive": 0.9,
    "opportunistic": 1.0,
    "balanced": 1.1,
    "random": 1.0,
}


def _pick_action_indexed(cells: List[Cell], owned_idxs: set,
                         comp_id: int,
                         strategy: str) -> List[Tuple[str, int]]:
    """Return actions using a pre-built ownership set (avoids full scan)."""
    if not owned_idxs:
        return []

    border_own = [cells[i] for i in owned_idxs
                  if any(cells[n].owner != comp_id
                         for n in cells[i].neighbors)]
    targets_neutral = []
    targets_enemy = []
    for c in border_own:
        for n in c.neighbors:
            nb = cells[n]
            if nb.owner is None:
                targets_neutral.append(n)
            elif nb.owner != comp_id:
                targets_enemy.append(n)

    actions: List[Tuple[str, int]] = []

    if strategy == "aggressive":
        if targets_enemy:
            t = min(targets_enemy, key=lambda i: cells[i].strength)
            actions.append(("attack", t))
        elif targets_neutral:
            actions.append(("claim", targets_neutral[0]))
        for c in border_own[:2]:
            actions.append(("fortify", c.idx))

    elif strategy == "defensive":
        for c in border_own[:3]:
            actions.append(("fortify", c.idx))
        if targets_neutral:
            actions.append(("claim", targets_neutral[0]))

    elif strategy == "opportunistic":
        for t in targets_neutral[:3]:
            actions.append(("claim", t))
        if not targets_neutral and targets_enemy:
            t = min(targets_enemy, key=lambda i: cells[i].strength)
            actions.append(("attack", t))

    elif strategy == "balanced":
        if targets_neutral:
            actions.append(("claim", targets_neutral[0]))
        if targets_enemy:
            t = min(targets_enemy, key=lambda i: cells[i].strength)
            actions.append(("attack", t))
        if border_own:
            actions.append(("fortify", border_own[0].idx))

    else:  # random
        pool: List[Tuple[str, int]] = []
        pool += [("claim", t) for t in targets_neutral]
        pool += [("attack", t) for t in targets_enemy]
        pool += [("fortify", c.idx) for c in border_own]
        if pool:
            random.shuffle(pool)
            actions = pool[:2]

    return actions


# ── Simulation engine ───────────────────────────────────────────────

class CompetitionResult:
    """Holds full simulation history and analytics."""
    def __init__(self):
        self.points: List[Tuple[float, float]] = []
        self.width: float = 0
        self.height: float = 0
        self.n_competitors: int = 0
        self.strategies: List[str] = []
        self.rounds: int = 0
        # per-round snapshot: list of (owner|None, strength) per cell
        self.history: List[List[Tuple[Optional[int], float]]] = []
        self.territory_sizes: List[List[int]] = []  # [round][comp] = count
        self.eliminations: List[Tuple[int, int]] = []  # (round, comp_id)
        self.winner: Optional[int] = None
        self.recommendations: List[str] = []


def simulate_competition(
    n_competitors: int = 4,
    rounds: int = 50,
    strategies: Optional[List[str]] = None,
    n_points: int = 120,
    width: float = 800,
    height: float = 600,
    seed: Optional[int] = None,
) -> CompetitionResult:
    """Run a full territorial competition and return results."""
    if seed is not None:
        random.seed(seed)

    if strategies is None:
        strategies = list(STRATEGIES[:n_competitors])
    while len(strategies) < n_competitors:
        strategies.append(random.choice(STRATEGIES))
    strategies = strategies[:n_competitors]

    points = _random_points(n_points, width, height,
                            min_sep=min(width, height) / (n_points ** 0.5))
    cells = _build_cells(points, width, height)

    # assign starting cells — pick spread-out points
    starts = []
    remaining = list(range(len(cells)))
    random.shuffle(remaining)
    for ci in range(n_competitors):
        if not remaining:
            break
        best = remaining[0]
        if starts:
            best = max(remaining,
                       key=lambda i: min(_distance(cells[i].center,
                                                   cells[s].center)
                                         for s in starts))
        starts.append(best)
        remaining.remove(best)
        cells[best].owner = ci
        cells[best].strength = 20.0

    result = CompetitionResult()
    result.points = points
    result.width = width
    result.height = height
    result.n_competitors = n_competitors
    result.strategies = list(strategies)
    result.rounds = rounds

    alive = set(range(n_competitors))

    # --- ownership index: avoid O(cells) full scans each round ----
    owned_sets: List[set] = [set() for _ in range(n_competitors)]
    for c in cells:
        if c.owner is not None:
            owned_sets[c.owner].add(c.idx)

    for rnd in range(rounds):
        # snapshot before actions
        snap = [(c.owner, c.strength) for c in cells]
        result.history.append(snap)

        sizes = [len(s) for s in owned_sets]
        result.territory_sizes.append(sizes)

        # check eliminations
        for ci in list(alive):
            if sizes[ci] == 0:
                alive.discard(ci)
                result.eliminations.append((rnd, ci))

        if len(alive) <= 1:
            break

        # resource accumulation (only iterate owned cells)
        income = [0.0] * n_competitors
        for ci in alive:
            for idx in owned_sets[ci]:
                income[ci] += cells[idx].resource

        # each competitor acts
        order = list(alive)
        random.shuffle(order)
        for ci in order:
            acts = _pick_action_indexed(cells, owned_sets[ci], ci,
                                        strategies[ci])
            budget = income[ci]
            bonus = _STRAT_ATTACK_BONUS[strategies[ci]]
            for act, tgt in acts:
                if budget <= 0:
                    break
                tc = cells[tgt]
                if act == "claim" and tc.owner is None:
                    tc.owner = ci
                    tc.strength = min(10.0, budget)
                    owned_sets[ci].add(tgt)
                    budget -= 5
                elif act == "attack" and tc.owner is not None and tc.owner != ci:
                    power = min(budget, 15.0) * bonus
                    old_owner = tc.owner
                    if power > tc.strength:
                        owned_sets[old_owner].discard(tgt)
                        tc.owner = ci
                        tc.strength = power - tc.strength
                        owned_sets[ci].add(tgt)
                    else:
                        tc.strength -= power * 0.5
                    budget -= 10
                elif act == "fortify" and tc.owner == ci:
                    add = min(budget, 8.0)
                    tc.strength = min(100.0, tc.strength + add)
                    budget -= add

        # natural decay
        for c in cells:
            if c.owner is not None:
                c.strength = max(0, c.strength - 0.3)
                if c.strength <= 0:
                    owned_sets[c.owner].discard(c.idx)
                    c.owner = None

    # final snapshot
    snap = [(c.owner, c.strength) for c in cells]
    result.history.append(snap)
    sizes = [len(s) for s in owned_sets]
    result.territory_sizes.append(sizes)

    # determine winner
    if alive:
        result.winner = max(alive, key=lambda i: sizes[i])

    # recommendations
    result.recommendations = _generate_recommendations(result)
    return result


def _generate_recommendations(r: CompetitionResult) -> List[str]:
    recs = []
    if r.winner is not None:
        ws = r.strategies[r.winner]
        recs.append(f"Strategy '{ws}' (Competitor {r.winner}) won — "
                    f"effective for this point distribution.")
    # stalemate check
    if len(r.territory_sizes) > 10:
        last = r.territory_sizes[-1]
        mid = r.territory_sizes[len(r.territory_sizes) // 2]
        if last == mid:
            recs.append("Stalemate detected — consider adding more "
                        "competitors or points for dynamic play.")
    # dominance
    if r.territory_sizes:
        final = r.territory_sizes[-1]
        total = sum(final)
        if total > 0:
            dom = max(final) / total
            if dom > 0.7:
                recs.append("High dominance (>70%) — one competitor "
                            "controlled most territory. Try defensive "
                            "counters.")
            elif dom < 0.35 and len([s for s in final if s > 0]) > 2:
                recs.append("Balanced match — no single dominant force. "
                            "Good competitive diversity.")
    if r.eliminations:
        early = [e for e in r.eliminations if e[0] < r.rounds * 0.3]
        if early:
            ids = [str(e[1]) for e in early]
            recs.append(f"Competitors {', '.join(ids)} eliminated early — "
                        f"consider better starting positions.")
    return recs


# ── Export ──────────────────────────────────────────────────────────

def export_json(result: CompetitionResult, path: str) -> None:
    """Export full competition history as JSON."""
    data = {
        "width": result.width,
        "height": result.height,
        "points": result.points,
        "n_competitors": result.n_competitors,
        "strategies": result.strategies,
        "rounds": result.rounds,
        "winner": result.winner,
        "eliminations": result.eliminations,
        "territory_sizes": result.territory_sizes,
        "recommendations": result.recommendations,
        "history": [
            [{"owner": o, "strength": round(s, 2)} for o, s in snap]
            for snap in result.history
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  JSON → {os.path.abspath(path)}")


COMP_COLORS = [
    "#e74c3c", "#3498db", "#2ecc71", "#f39c12", "#9b59b6", "#1abc9c",
]


def export_html(result: CompetitionResult, path: str) -> None:
    """Export interactive HTML replay with Canvas animation."""
    hist_json = json.dumps([
        [{"o": o, "s": round(s, 1)} for o, s in snap]
        for snap in result.history
    ])
    pts_json = json.dumps(result.points)
    sizes_json = json.dumps(result.territory_sizes)
    strats_json = json.dumps(result.strategies)
    recs_json = json.dumps(result.recommendations)
    colors_json = json.dumps(COMP_COLORS[:result.n_competitors])

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Voronoi Competition — Territorial Replay</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;
      display:flex;flex-direction:column;align-items:center;padding:20px}}
h1{{margin-bottom:8px;font-size:1.6rem}}
.info{{font-size:.85rem;opacity:.7;margin-bottom:12px}}
canvas{{border:1px solid #333;border-radius:6px;margin-bottom:10px}}
.controls{{display:flex;gap:8px;align-items:center;margin-bottom:14px}}
.controls button{{background:#222;color:#eee;border:1px solid #444;
  padding:6px 14px;border-radius:4px;cursor:pointer;font-size:.85rem}}
.controls button:hover{{background:#333}}
#roundLabel{{min-width:90px;text-align:center;font-variant-numeric:tabular-nums}}
.chart-wrap{{margin:16px 0}}
table{{border-collapse:collapse;margin:12px 0;font-size:.85rem}}
th,td{{padding:4px 12px;border:1px solid #333;text-align:center}}
th{{background:#1a1a2e}}
.recs{{max-width:700px;text-align:left;margin:12px 0;font-size:.85rem}}
.recs li{{margin:4px 0}}
</style></head><body>
<h1>🏴 Voronoi Territorial Competition</h1>
<p class="info">{result.n_competitors} competitors · {result.rounds} rounds ·
{len(result.points)} cells</p>
<canvas id="cv" width="{int(result.width)}" height="{int(result.height)}"></canvas>
<div class="controls">
  <button id="bPlay">▶ Play</button>
  <button id="bStep">⏭ Step</button>
  <button id="bReset">⏮ Reset</button>
  <span id="roundLabel">Round 0</span>
  <input id="speed" type="range" min="50" max="600" value="200" title="Speed">
</div>
<div class="chart-wrap"><canvas id="chart" width="700" height="200"></canvas></div>
<table id="stats"></table>
<div class="recs"><strong>Recommendations:</strong><ul id="recList"></ul></div>
<script>
const W={int(result.width)},H={int(result.height)};
const pts={pts_json};
const hist={hist_json};
const sizes={sizes_json};
const strats={strats_json};
const recs={recs_json};
const colors={colors_json};
const NC={result.n_competitors};
const cv=document.getElementById("cv"),ctx=cv.getContext("2d");
const chart=document.getElementById("chart"),cctx=chart.getContext("2d");

// precompute nearest-point grid (Voronoi rasterization)
const GR=2;
const gridW=Math.ceil(W/GR),gridH=Math.ceil(H/GR);
const cellMap=new Int16Array(gridW*gridH);
for(let gy=0;gy<gridH;gy++)for(let gx=0;gx<gridW;gx++){{
  let cx=(gx+.5)*GR,cy=(gy+.5)*GR,best=0,bd=1e18;
  for(let i=0;i<pts.length;i++){{
    let dx=cx-pts[i][0],dy=cy-pts[i][1],d=dx*dx+dy*dy;
    if(d<bd){{bd=d;best=i;}}
  }}
  cellMap[gy*gridW+gx]=best;
}}

let frame=0,playing=false,timer=null;

function draw(f){{
  const snap=hist[f];
  // color lookup per cell owner
  const img=ctx.createImageData(W,H);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){{
    let gi=Math.floor(y/GR)*gridW+Math.floor(x/GR);
    let ci=cellMap[gi],s=snap[ci];
    let r=30,g=30,b=40,a=255;
    if(s.o!==null){{
      let hex=colors[s.o];
      r=parseInt(hex.slice(1,3),16);
      g=parseInt(hex.slice(3,5),16);
      b=parseInt(hex.slice(5,7),16);
      let bright=0.4+0.6*(s.s/100);
      r=Math.round(r*bright);g=Math.round(g*bright);b=Math.round(b*bright);
    }}
    let idx=(y*W+x)*4;
    img.data[idx]=r;img.data[idx+1]=g;img.data[idx+2]=b;img.data[idx+3]=a;
  }}
  ctx.putImageData(img,0,0);
  // draw cell centers
  ctx.fillStyle="rgba(255,255,255,0.35)";
  for(let p of pts){{ctx.beginPath();ctx.arc(p[0],p[1],1.5,0,6.28);ctx.fill();}}
  document.getElementById("roundLabel").textContent="Round "+f;
}}

function drawChart(){{
  cctx.clearRect(0,0,700,200);
  cctx.fillStyle="#13131f";cctx.fillRect(0,0,700,200);
  let maxS=Math.max(...sizes.flat(),1);
  let n=sizes.length;
  for(let c=0;c<NC;c++){{
    cctx.strokeStyle=colors[c];cctx.lineWidth=2;
    cctx.beginPath();
    for(let i=0;i<n;i++){{
      let x=10+(i/(n-1||1))*680, y=190-(sizes[i][c]/maxS)*170;
      i===0?cctx.moveTo(x,y):cctx.lineTo(x,y);
    }}
    cctx.stroke();
  }}
  cctx.fillStyle="#888";cctx.font="11px system-ui";
  cctx.fillText("Territory Size Over Time",280,14);
}}

function buildStats(){{
  let last=sizes[sizes.length-1];
  let html="<tr><th>Competitor</th><th>Strategy</th><th>Cells</th><th>%</th></tr>";
  let total=last.reduce((a,b)=>a+b,0)||1;
  for(let c=0;c<NC;c++){{
    let pct=(last[c]/total*100).toFixed(1);
    html+=`<tr><td style="color:${{colors[c]}}">■ ${{c}}</td><td>${{strats[c]}}</td>`+
          `<td>${{last[c]}}</td><td>${{pct}}%</td></tr>`;
  }}
  document.getElementById("stats").innerHTML=html;
}}

function buildRecs(){{
  let ul=document.getElementById("recList");
  recs.forEach(r=>{{let li=document.createElement("li");li.textContent=r;ul.appendChild(li);}});
}}

function step(){{if(frame<hist.length-1){{frame++;draw(frame);drawChart();buildStats();}}
  else{{stop();}}
}}
function play(){{if(playing)return;playing=true;
  timer=setInterval(step,parseInt(document.getElementById("speed").value));}}
function stop(){{playing=false;clearInterval(timer);}}

document.getElementById("bPlay").onclick=()=>playing?stop():play();
document.getElementById("bStep").onclick=()=>{{stop();step();}};
document.getElementById("bReset").onclick=()=>{{stop();frame=0;draw(0);drawChart();buildStats();}};
document.getElementById("speed").oninput=()=>{{if(playing){{stop();play();}}}};

draw(0);drawChart();buildStats();buildRecs();
</script></body></html>"""

    with open(path, "w") as f:
        f.write(html)
    print(f"  HTML → {os.path.abspath(path)}")


def format_summary(result: CompetitionResult) -> str:
    """Return a text summary of the competition."""
    lines = ["═══ Voronoi Territorial Competition ═══", ""]
    lines.append(f"  Competitors : {result.n_competitors}")
    lines.append(f"  Rounds      : {result.rounds}")
    lines.append(f"  Cells       : {len(result.points)}")
    lines.append("")
    if result.territory_sizes:
        final = result.territory_sizes[-1]
        total = sum(final) or 1
        for i in range(result.n_competitors):
            pct = final[i] / total * 100
            bar = "█" * int(pct / 3)
            lines.append(f"  [{i}] {result.strategies[i]:14s} "
                         f"{final[i]:4d} cells ({pct:5.1f}%) {bar}")
    lines.append("")
    if result.eliminations:
        for rnd, ci in result.eliminations:
            lines.append(f"  ☠  Competitor {ci} eliminated at round {rnd}")
        lines.append("")
    if result.winner is not None:
        lines.append(f"  🏆  Winner: Competitor {result.winner} "
                     f"({result.strategies[result.winner]})")
    lines.append("")
    for rec in result.recommendations:
        lines.append(f"  💡 {rec}")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Voronoi Territorial Competition Simulator")
    ap.add_argument("--competitors", type=int, default=4,
                    help="Number of competitors (2-6, default 4)")
    ap.add_argument("--rounds", type=int, default=50,
                    help="Simulation rounds (default 50)")
    ap.add_argument("--strategies",
                    help="Comma-separated strategies "
                         f"({', '.join(STRATEGIES)})")
    ap.add_argument("--points", type=int, default=120,
                    help="Number of Voronoi cells (default 120)")
    ap.add_argument("--width", type=float, default=800)
    ap.add_argument("--height", type=float, default=600)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--output", default="competition.html",
                    help="Output HTML file (default competition.html)")
    ap.add_argument("--json", default=None,
                    help="Also export JSON history")
    args = ap.parse_args()

    strats = None
    if args.strategies:
        strats = [s.strip().lower() for s in args.strategies.split(",")]
        for s in strats:
            if s not in STRATEGIES:
                ap.error(f"Unknown strategy '{s}'. "
                         f"Choose from: {', '.join(STRATEGIES)}")

    result = simulate_competition(
        n_competitors=args.competitors,
        rounds=args.rounds,
        strategies=strats,
        n_points=args.points,
        width=args.width,
        height=args.height,
        seed=args.seed,
    )

    print(format_summary(result))
    print()
    export_html(result, args.output)
    if args.json:
        export_json(result, args.json)


if __name__ == "__main__":
    main()
