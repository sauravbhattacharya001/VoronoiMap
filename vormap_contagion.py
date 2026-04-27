"""Autonomous Spatial Contagion Simulator on Voronoi Tessellations.

Models SIR (Susceptible → Infected → Recovered) epidemic / information
spread across Voronoi cells.  Each cell is a territory with a population;
infection propagates to neighbour cells via spatial adjacency.

Autonomous / agentic features:
    * **Outbreak Detection** — alerts when global infection rate crosses 25 %
    * **Hotspot Tracker** — identifies cells with highest infection each tick
    * **R0 Estimator** — estimates effective reproduction number from data
    * **Auto-Quarantine** (``--autopilot``) — isolates cells exceeding 50 %
      infection by cutting migration 80 %
    * **Vaccination Campaign** (``--autopilot``) — proactive S→R conversion
      in highest-risk neighbour cells after outbreak
    * **Epidemic Health Score** — composite 0-100 from recovery, containment, R0

Presets::

    flu      — beta=0.3  gamma=0.1   30 pts   fast classic spread
    zombie   — beta=0.6  gamma=0.02  20 pts   hard to recover
    rumor    — beta=0.4  gamma=0.3   40 pts   fast but loses interest
    pandemic — beta=0.25 gamma=0.05  50 pts   slow burn

CLI examples::

    python vormap_contagion.py --points 30 --ticks 100 --output contagion.html
    python vormap_contagion.py --preset flu --autopilot --output result.html
    python vormap_contagion.py --points 40 --beta 0.4 --gamma 0.15 --output spread.html

Programmatic::

    from vormap_contagion import ContagionSimulator, export_html
    sim = ContagionSimulator(n_points=30, ticks=100, beta=0.3, gamma=0.1)
    result = sim.run()
    export_html(result, "output.html")
"""

from __future__ import annotations

import argparse
import html as _html
import json
import math
import os
import random
from typing import Any, Dict, List, Optional, Tuple

from vormap_utils import euclidean as _dist

# ── Geometry helpers ────────────────────────────────────────────────

def _random_points(n: int, w: float = 500.0, h: float = 500.0) -> List[Tuple[float, float]]:
    return [(random.uniform(20, w - 20), random.uniform(20, h - 20)) for _ in range(n)]


def _build_adjacency(pts: List[Tuple[float, float]], threshold_factor: float = 2.0) -> Dict[int, List[int]]:
    n = len(pts)
    if n < 2:
        return {i: [] for i in range(n)}
    avg_nn = 0.0
    for i in range(n):
        mn = float("inf")
        for j in range(n):
            if i != j:
                d = _dist(pts[i], pts[j])
                if d < mn:
                    mn = d
        avg_nn += mn
    avg_nn /= n
    thresh = avg_nn * threshold_factor
    adj: Dict[int, List[int]] = {i: [] for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if _dist(pts[i], pts[j]) <= thresh:
                adj[i].append(j)
                adj[j].append(i)
    return adj


# ── Presets ─────────────────────────────────────────────────────────

PRESETS: Dict[str, Dict[str, Any]] = {
    "flu":      {"beta": 0.3,  "gamma": 0.1,  "n_points": 30, "migration_rate": 0.05},
    "zombie":   {"beta": 0.6,  "gamma": 0.02, "n_points": 20, "migration_rate": 0.08},
    "rumor":    {"beta": 0.4,  "gamma": 0.3,  "n_points": 40, "migration_rate": 0.06},
    "pandemic": {"beta": 0.25, "gamma": 0.05, "n_points": 50, "migration_rate": 0.03},
}


# ── Simulator ───────────────────────────────────────────────────────

class ContagionSimulator:
    """SIR contagion simulator across Voronoi cells."""

    def __init__(
        self,
        n_points: int = 30,
        ticks: int = 80,
        beta: float = 0.3,
        gamma: float = 0.1,
        migration_rate: float = 0.05,
        autopilot: bool = False,
        seed: Optional[int] = None,
    ) -> None:
        self.n_points = n_points
        self.ticks = ticks
        self.beta = beta
        self.gamma = gamma
        self.migration_rate = migration_rate
        self.autopilot = autopilot
        if seed is not None:
            random.seed(seed)

        self.pts = _random_points(n_points)
        self.adj = _build_adjacency(self.pts)
        self.pop = [100.0] * n_points  # each cell has 100 people

        # SIR state per cell
        self.S = [100.0] * n_points
        self.I = [0.0] * n_points
        self.R = [0.0] * n_points

        # Quarantine multiplier per cell (1.0 = normal, 0.2 = quarantined)
        self.q = [1.0] * n_points

        # seed patient zero in 1-3 cells
        n_seeds = random.randint(1, min(3, n_points))
        for idx in random.sample(range(n_points), n_seeds):
            infected = self.pop[idx] * 0.1
            self.S[idx] -= infected
            self.I[idx] += infected

    def run(self) -> Dict[str, Any]:
        history: List[Dict[str, List[float]]] = []
        alerts: List[Dict[str, Any]] = []
        global_sir: List[Tuple[float, float, float]] = []
        outbreak_detected = False
        vaccinated_cells: set = set()

        for t in range(self.ticks):
            # record state
            history.append({
                "S": list(self.S),
                "I": list(self.I),
                "R": list(self.R),
            })
            total_s = sum(self.S)
            total_i = sum(self.I)
            total_r = sum(self.R)
            total_n = total_s + total_i + total_r
            global_sir.append((total_s, total_i, total_r))

            infection_rate = total_i / total_n if total_n > 0 else 0

            # ── Autonomous: outbreak detection
            if not outbreak_detected and infection_rate > 0.25:
                outbreak_detected = True
                alerts.append({"tick": t, "type": "outbreak", "msg": f"⚠ Outbreak detected! Global infection rate {infection_rate:.1%}"})

            # ── Autonomous: hotspot tracker (top 3)
            cell_rates = [(self.I[i] / self.pop[i] if self.pop[i] > 0 else 0, i) for i in range(self.n_points)]
            cell_rates.sort(reverse=True)
            hotspots = [(idx, rate) for rate, idx in cell_rates[:3] if rate > 0.3]
            if hotspots and t % 10 == 0:
                ids = ", ".join(f"#{idx}({r:.0%})" for idx, r in hotspots)
                alerts.append({"tick": t, "type": "hotspot", "msg": f"🔥 Hotspots: {ids}"})

            # ── Autonomous: autopilot interventions
            if self.autopilot:
                for i in range(self.n_points):
                    rate_i = self.I[i] / self.pop[i] if self.pop[i] > 0 else 0
                    # auto-quarantine
                    if rate_i > 0.5 and self.q[i] > 0.3:
                        self.q[i] = 0.2
                        alerts.append({"tick": t, "type": "quarantine", "msg": f"🔒 Cell #{i} quarantined (infection {rate_i:.0%})"})
                    # lift quarantine when infection drops
                    elif rate_i < 0.1 and self.q[i] < 0.5:
                        self.q[i] = 1.0

                # vaccination campaign after outbreak
                if outbreak_detected:
                    # vaccinate top susceptible neighbours of hotspots
                    for idx, _ in hotspots:
                        for nb in self.adj.get(idx, []):
                            if nb not in vaccinated_cells and self.S[nb] > 20:
                                vax = min(self.S[nb] * 0.05, self.S[nb])
                                self.S[nb] -= vax
                                self.R[nb] += vax
                                vaccinated_cells.add(nb)
                                alerts.append({"tick": t, "type": "vaccination", "msg": f"💉 Vaccinated {vax:.0f} in cell #{nb}"})

            # ── SIR dynamics
            new_S = list(self.S)
            new_I = list(self.I)
            new_R = list(self.R)

            for i in range(self.n_points):
                N = self.pop[i]
                if N <= 0:
                    continue
                s, inf, r = self.S[i], self.I[i], self.R[i]
                # within-cell transmission
                new_infected = self.beta * s * inf / N
                new_recovered = self.gamma * inf
                new_S[i] -= new_infected
                new_I[i] += new_infected - new_recovered
                new_R[i] += new_recovered

            # cross-cell migration of infected
            migration_delta_I = [0.0] * self.n_points
            migration_delta_S = [0.0] * self.n_points
            for i in range(self.n_points):
                neighbours = self.adj.get(i, [])
                if not neighbours:
                    continue
                mig = self.migration_rate * self.q[i]
                outflow_I = new_I[i] * mig / len(neighbours)
                for nb in neighbours:
                    migration_delta_I[i] -= outflow_I
                    migration_delta_I[nb] += outflow_I

            for i in range(self.n_points):
                new_I[i] += migration_delta_I[i]
                # clamp
                new_S[i] = max(0.0, min(new_S[i], self.pop[i]))
                new_I[i] = max(0.0, min(new_I[i], self.pop[i]))
                new_R[i] = max(0.0, min(new_R[i], self.pop[i]))
                # normalize to pop
                total = new_S[i] + new_I[i] + new_R[i]
                if total > 0:
                    scale = self.pop[i] / total
                    new_S[i] *= scale
                    new_I[i] *= scale
                    new_R[i] *= scale

            self.S = new_S
            self.I = new_I
            self.R = new_R

        # final snapshot
        history.append({"S": list(self.S), "I": list(self.I), "R": list(self.R)})
        total_n = sum(self.pop)

        # ── R0 estimation (from initial growth)
        r0_est = self._estimate_r0(global_sir)

        # ── Peak infection
        peak_i = max(si[1] for si in global_sir)
        peak_tick = next(t for t, si in enumerate(global_sir) if si[1] == peak_i)
        peak_pct = peak_i / total_n if total_n > 0 else 0

        # ── Health score
        final_r = global_sir[-1][2] / total_n if total_n > 0 else 0
        final_i = global_sir[-1][1] / total_n if total_n > 0 else 0
        containment = 1.0 - peak_pct
        r0_score = max(0, 1.0 - (r0_est - 1.0) / 3.0) if r0_est > 0 else 1.0
        health = int(max(0, min(100, (final_r * 30 + containment * 40 + r0_score * 30))))

        return {
            "points": self.pts,
            "adjacency": {str(k): v for k, v in self.adj.items()},
            "history": history,
            "global_sir": global_sir,
            "alerts": alerts,
            "params": {
                "n_points": self.n_points, "ticks": self.ticks,
                "beta": self.beta, "gamma": self.gamma,
                "migration_rate": self.migration_rate,
                "autopilot": self.autopilot,
            },
            "stats": {
                "r0_estimate": round(r0_est, 2),
                "peak_infection_pct": round(peak_pct * 100, 1),
                "peak_tick": peak_tick,
                "final_recovered_pct": round(final_r * 100, 1),
                "final_infected_pct": round(final_i * 100, 1),
                "health_score": health,
                "total_alerts": len(alerts),
            },
        }

    def _estimate_r0(self, sir: List[Tuple[float, float, float]]) -> float:
        """Rough R0 from early exponential growth of I."""
        # find first 10 ticks with growing I
        growth_rates = []
        for t in range(1, min(len(sir), 15)):
            if sir[t - 1][1] > 1:
                gr = sir[t][1] / sir[t - 1][1]
                growth_rates.append(gr)
        if not growth_rates:
            return 0.0
        avg_growth = sum(growth_rates) / len(growth_rates)
        # R0 ≈ avg_growth / gamma  (rough)
        if self.gamma > 0:
            return round(avg_growth / (1.0 - self.gamma + self.gamma), 2)
        return avg_growth


# ── HTML export ─────────────────────────────────────────────────────

def export_html(result: Dict[str, Any], path: str) -> None:
    pts = result["points"]
    adj = result["adjacency"]
    history = result["history"]
    global_sir = result["global_sir"]
    alerts = result["alerts"]
    params = result["params"]
    stats = result["stats"]

    # Build compact JSON data
    data_json = json.dumps({
        "pts": [[round(x, 1), round(y, 1)] for x, y in pts],
        "adj": {k: v for k, v in adj.items()},
        "history": [
            {k: [round(v, 1) for v in vals] for k, vals in frame.items()}
            for frame in history
        ],
        "sir": [[round(s, 1), round(i, 1), round(r, 1)] for s, i, r in global_sir],
        "alerts": alerts,
        "params": params,
        "stats": stats,
    })

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Spatial Contagion Simulator</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:20px}}
h1{{color:#58a6ff;margin-bottom:4px;font-size:1.6em}}
.subtitle{{color:#8b949e;margin-bottom:16px;font-size:0.9em}}
.grid{{display:grid;grid-template-columns:520px 1fr;gap:16px;max-width:1100px}}
.panel{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}}
.panel h2{{color:#58a6ff;font-size:1.1em;margin-bottom:10px}}
svg{{display:block;margin:0 auto}}
#controls{{text-align:center;margin:10px 0}}
#controls button{{background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:4px 12px;margin:0 2px;border-radius:4px;cursor:pointer}}
#controls button:hover{{background:#30363d}}
#slider{{width:80%;margin:6px 0}}
#tickLabel{{color:#58a6ff;font-weight:bold}}
canvas{{width:100%;height:200px;display:block;margin:8px 0}}
.stat-row{{display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid #21262d}}
.stat-label{{color:#8b949e}}
.stat-val{{color:#f0f6fc;font-weight:bold}}
.alert-list{{max-height:250px;overflow-y:auto;font-size:0.85em}}
.alert-item{{padding:4px 0;border-bottom:1px solid #21262d}}
.alert-tick{{color:#58a6ff;font-weight:bold;margin-right:6px}}
.health{{font-size:2em;font-weight:bold;text-align:center;margin:8px 0}}
.legend{{display:flex;gap:16px;justify-content:center;margin:8px 0;font-size:0.85em}}
.legend span{{display:flex;align-items:center;gap:4px}}
.legend .dot{{width:12px;height:12px;border-radius:50%;display:inline-block}}
</style>
</head>
<body>
<h1>🦠 Spatial Contagion Simulator</h1>
<p class="subtitle">SIR model on Voronoi tessellation — {_html.escape(str(params['n_points']))} cells,
β={params['beta']}, γ={params['gamma']}, migration={params['migration_rate']}
{"| 🤖 Autopilot ON" if params['autopilot'] else ""}</p>

<div class="grid">
<div>
 <div class="panel">
  <h2>Spatial Map</h2>
  <div class="legend">
   <span><span class="dot" style="background:#3fb950"></span> Susceptible</span>
   <span><span class="dot" style="background:#f85149"></span> Infected</span>
   <span><span class="dot" style="background:#58a6ff"></span> Recovered</span>
  </div>
  <svg id="map" width="500" height="500" viewBox="0 0 500 500"></svg>
  <div id="controls">
   <button onclick="play(-1)">⏪</button>
   <button id="playBtn" onclick="togglePlay()">▶</button>
   <button onclick="play(1)">⏩</button>
   <br><input id="slider" type="range" min="0" max="{len(history)-1}" value="0">
   <br>Tick: <span id="tickLabel">0</span>
  </div>
 </div>
 <div class="panel" style="margin-top:16px">
  <h2>SIR Curve</h2>
  <canvas id="chart" height="200"></canvas>
 </div>
</div>
<div>
 <div class="panel">
  <h2>Epidemic Health Score</h2>
  <div class="health" style="color:{
    '#3fb950' if stats['health_score'] >= 70 else '#d29922' if stats['health_score'] >= 40 else '#f85149'
  }">{stats['health_score']}/100</div>
 </div>
 <div class="panel" style="margin-top:16px">
  <h2>Statistics</h2>
  <div class="stat-row"><span class="stat-label">R₀ Estimate</span><span class="stat-val">{stats['r0_estimate']}</span></div>
  <div class="stat-row"><span class="stat-label">Peak Infection</span><span class="stat-val">{stats['peak_infection_pct']}% (tick {stats['peak_tick']})</span></div>
  <div class="stat-row"><span class="stat-label">Final Recovered</span><span class="stat-val">{stats['final_recovered_pct']}%</span></div>
  <div class="stat-row"><span class="stat-label">Final Infected</span><span class="stat-val">{stats['final_infected_pct']}%</span></div>
  <div class="stat-row"><span class="stat-label">Total Alerts</span><span class="stat-val">{stats['total_alerts']}</span></div>
 </div>
 <div class="panel" style="margin-top:16px">
  <h2>🤖 Autonomous Alerts</h2>
  <div class="alert-list" id="alertList"></div>
 </div>
</div>
</div>

<script>
const D={data_json};
const pts=D.pts,adj=D.adj,hist=D.history,sir=D.sir,alerts=D.alerts;

// ── Map rendering
const svg=document.getElementById('map');
const slider=document.getElementById('slider');
const tickLabel=document.getElementById('tickLabel');

function cellColor(s,i,r){{
  const t=s+i+r||1;
  const rs=s/t,ri=i/t,rr=r/t;
  const red=Math.round(ri*248+rs*30+rr*88);
  const green=Math.round(ri*81+rs*185+rr*166);
  const blue=Math.round(ri*73+rs*80+rr*255);
  return`rgb(${{red}},${{green}},${{blue}})`;
}}

function drawMap(tick){{
  const frame=hist[tick];
  let h='';
  // edges
  for(let k in adj){{
    const i=parseInt(k);
    for(const j of adj[k]){{
      if(j>i)h+=`<line x1="${{pts[i][0]}}" y1="${{pts[i][1]}}" x2="${{pts[j][0]}}" y2="${{pts[j][1]}}" stroke="#30363d" stroke-width="0.5"/>`;
    }}
  }}
  // cells as circles
  const n=pts.length;
  for(let i=0;i<n;i++){{
    const c=cellColor(frame.S[i],frame.I[i],frame.R[i]);
    h+=`<circle cx="${{pts[i][0]}}" cy="${{pts[i][1]}}" r="14" fill="${{c}}" stroke="#484f58" stroke-width="0.5" opacity="0.9"><title>Cell #${{i}}\\nS:${{frame.S[i].toFixed(0)}} I:${{frame.I[i].toFixed(0)}} R:${{frame.R[i].toFixed(0)}}</title></circle>`;
  }}
  svg.innerHTML=h;
}}

slider.oninput=function(){{drawMap(+this.value);tickLabel.textContent=this.value}};
drawMap(0);

let playing=false,timer=null,dir=1;
function togglePlay(){{
  if(playing){{clearInterval(timer);playing=false;document.getElementById('playBtn').textContent='▶'}}
  else{{playing=true;document.getElementById('playBtn').textContent='⏸';dir=1;tick()}}
}}
function play(d){{dir=d;if(!playing)togglePlay()}}
function tick(){{
  let v=+slider.value+dir;
  if(v>=hist.length||v<0){{togglePlay();return}}
  slider.value=v;slider.oninput();
  timer=setTimeout(tick,80);
}}

// ── SIR chart
const canvas=document.getElementById('chart');
const ctx=canvas.getContext('2d');
canvas.width=canvas.offsetWidth*2;canvas.height=400;
ctx.scale(2,2);
const cw=canvas.offsetWidth,ch=200;
const maxN=sir.length>0?sir[0][0]+sir[0][1]+sir[0][2]:1;
function drawChart(){{
  ctx.clearRect(0,0,cw,ch);
  ctx.fillStyle='#0d1117';ctx.fillRect(0,0,cw,ch);
  const colors=['#3fb950','#f85149','#58a6ff'];
  for(let c=0;c<3;c++){{
    ctx.beginPath();ctx.strokeStyle=colors[c];ctx.lineWidth=1.5;
    for(let t=0;t<sir.length;t++){{
      const x=t/(sir.length-1)*cw;
      const y=ch-sir[t][c]/maxN*(ch-20)-10;
      t===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
    }}
    ctx.stroke();
  }}
  // labels
  ctx.font='11px sans-serif';
  ctx.fillStyle='#3fb950';ctx.fillText('S',4,15);
  ctx.fillStyle='#f85149';ctx.fillText('I',20,15);
  ctx.fillStyle='#58a6ff';ctx.fillText('R',34,15);
}}
drawChart();

// ── Alerts
const al=document.getElementById('alertList');
if(alerts.length===0)al.innerHTML='<div style="color:#8b949e">No alerts — epidemic contained.</div>';
else alerts.forEach(a=>{{
  const d=document.createElement('div');d.className='alert-item';
  d.innerHTML=`<span class="alert-tick">t=${{a.tick}}</span>${{a.msg}}`;
  al.appendChild(d);
}});
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Spatial Contagion Simulator on Voronoi tessellations")
    parser.add_argument("--points", type=int, default=30)
    parser.add_argument("--ticks", type=int, default=80)
    parser.add_argument("--beta", type=float, default=0.3)
    parser.add_argument("--gamma", type=float, default=0.1)
    parser.add_argument("--migration-rate", type=float, default=0.05)
    parser.add_argument("--preset", choices=list(PRESETS.keys()))
    parser.add_argument("--autopilot", action="store_true")
    parser.add_argument("--output", default="contagion_report.html")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()

    kw: Dict[str, Any] = {
        "n_points": args.points,
        "ticks": args.ticks,
        "beta": args.beta,
        "gamma": args.gamma,
        "migration_rate": args.migration_rate,
        "autopilot": args.autopilot,
        "seed": args.seed,
    }
    if args.preset:
        p = PRESETS[args.preset]
        kw.update({
            "n_points": p["n_points"],
            "beta": p["beta"],
            "gamma": p["gamma"],
            "migration_rate": p["migration_rate"],
        })

    sim = ContagionSimulator(**kw)
    result = sim.run()
    export_html(result, args.output)
    s = result["stats"]
    print(f"Contagion simulation complete -> {args.output}")
    print(f"   Health Score: {s['health_score']}/100 | R0={s['r0_estimate']} | Peak: {s['peak_infection_pct']}% at t={s['peak_tick']}")
    print(f"   Final: {s['final_recovered_pct']}% recovered, {s['final_infected_pct']}% infected | {s['total_alerts']} alerts")


if __name__ == "__main__":
    main()
