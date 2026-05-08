"""Autonomous Terrain Sculptor for VoronoiMap.

Uses Voronoi cells as tectonic plates to sculpt realistic terrain through
plate tectonics simulation, hydraulic erosion, and thermal weathering.
Acts as an intelligent agent: analyzes terrain health, auto-tunes parameters,
and provides proactive recommendations for achieving realistic landscapes.

Simulation pipeline:
  1. Generate tectonic plates via Voronoi cell assignment
  2. Assign plate movement vectors and compute boundary interactions
  3. Build initial elevation from plate tectonics (convergent→mountains,
     divergent→rifts, transform→ridges)
  4. Apply hydraulic erosion (water particle simulation)
  5. Apply thermal erosion (slope-based weathering)
  6. Analyze terrain health and generate recommendations

Presets:
  - **archipelago**     — many small plates, low base elevation, mostly ocean
  - **pangaea**         — one dominant plate, high continental mass
  - **rift_valley**     — divergent boundary emphasis, deep valleys
  - **mountain_range**  — strong convergent boundaries, tall peaks
  - **plains**          — few plates, heavy erosion, flat result

CLI::

    python vormap_terraform.py
    python vormap_terraform.py --preset archipelago --output terrain.html
    python vormap_terraform.py --points 20 --erosion-steps 800 --auto-sculpt
    python vormap_terraform.py --preset mountain_range --json

"""

import argparse
import io
import json
import math
import os
import random
import sys
import html as html_mod

# Fix Windows console encoding for emoji
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith('cp'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Grid / math helpers
# ---------------------------------------------------------------------------

def _dist(ax, ay, bx, by):
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)






# ---------------------------------------------------------------------------
# Voronoi plate assignment (nearest-seed)
# ---------------------------------------------------------------------------

def _assign_plates(width, height, seeds):
    """Return grid[y][x] = plate_index via brute-force nearest seed."""
    grid = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            best_d = float("inf")
            best_i = 0
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            grid[y][x] = best_i
    return grid


# ---------------------------------------------------------------------------
# Tectonic elevation builder
# ---------------------------------------------------------------------------

def _build_tectonic_elevation(width, height, plate_grid, seeds, vectors, base_elev):
    """Compute elevation from plate boundary interactions."""
    n_plates = len(seeds)
    elev = [[base_elev] * width for _ in range(height)]

    # Precompute plate-pair interaction type
    interactions = {}
    for i in range(n_plates):
        for j in range(i + 1, n_plates):
            vx_i, vy_i = vectors[i]
            vx_j, vy_j = vectors[j]
            # relative motion toward each other
            dx = seeds[j][0] - seeds[i][0]
            dy = seeds[j][1] - seeds[i][1]
            d = math.sqrt(dx * dx + dy * dy) or 1.0
            dx /= d
            dy /= d
            # dot of relative velocity with connecting direction
            rel_vx = vx_i - vx_j
            rel_vy = vy_i - vy_j
            dot = rel_vx * dx + rel_vy * dy
            if dot > 0.3:
                interactions[(i, j)] = "convergent"
            elif dot < -0.3:
                interactions[(i, j)] = "divergent"
            else:
                interactions[(i, j)] = "transform"

    # For each cell compute distance to nearest plate boundary and interaction
    for y in range(height):
        for x in range(width):
            my_plate = plate_grid[y][x]
            # find nearest cell of different plate (approximate: check neighbours)
            min_bd = float("inf")
            neighbor_plate = my_plate
            for dy2 in range(-3, 4):
                for dx2 in range(-3, 4):
                    nx, ny = x + dx2, y + dy2
                    if 0 <= nx < width and 0 <= ny < height:
                        op = plate_grid[ny][nx]
                        if op != my_plate:
                            bd = abs(dx2) + abs(dy2)
                            if bd < min_bd:
                                min_bd = bd
                                neighbor_plate = op

            if min_bd < 7:
                key = (min(my_plate, neighbor_plate), max(my_plate, neighbor_plate))
                itype = interactions.get(key, "transform")
                proximity = 1.0 - min_bd / 7.0
                if itype == "convergent":
                    elev[y][x] += proximity * 0.6
                elif itype == "divergent":
                    elev[y][x] -= proximity * 0.4
                else:
                    elev[y][x] += proximity * 0.15

    return elev, interactions


# ---------------------------------------------------------------------------
# Hydraulic erosion
# ---------------------------------------------------------------------------

def _hydraulic_erosion(elev, width, height, steps, erosion_rate=0.03, deposition_rate=0.01, evap=0.02):
    """Particle-based hydraulic erosion."""
    eroded = [row[:] for row in elev]
    rivers = [[0] * width for _ in range(height)]

    for _ in range(steps):
        x = random.uniform(1, width - 2)
        y = random.uniform(1, height - 2)
        sediment = 0.0
        speed = 1.0
        water = 1.0

        for __ in range(80):
            ix, iy = int(x), int(y)
            if ix < 1 or ix >= width - 1 or iy < 1 or iy >= height - 1:
                break

            # gradient
            h = eroded[iy][ix]
            gx = eroded[iy][ix + 1] - eroded[iy][ix - 1]
            gy = eroded[iy + 1][ix] - eroded[iy - 1][ix]
            glen = math.sqrt(gx * gx + gy * gy) or 1.0

            # move downhill
            nx = x - gx / glen
            ny = y - gy / glen
            nix, niy = int(nx), int(ny)
            if nix < 0 or nix >= width or niy < 0 or niy >= height:
                break

            nh = eroded[niy][nix]
            diff = h - nh

            if diff > 0:
                # erode
                carry = min(diff, erosion_rate * speed * water)
                eroded[iy][ix] -= carry * 0.5
                sediment += carry
                speed = min(speed + diff * 0.5, 3.0)
            else:
                # deposit
                dep = min(sediment, deposition_rate)
                eroded[iy][ix] += dep
                sediment -= dep
                speed = max(speed * 0.5, 0.1)

            rivers[iy][ix] += 1
            water -= evap * water
            if water < 0.01:
                break
            x, y = nx, ny

    return eroded, rivers


# ---------------------------------------------------------------------------
# Thermal erosion
# ---------------------------------------------------------------------------

def _thermal_erosion(elev, width, height, steps, talus=0.05):
    """Slope-based thermal weathering."""
    result = [row[:] for row in elev]
    for _ in range(steps):
        for y in range(1, height - 1):
            for x in range(1, width - 1):
                h = result[y][x]
                max_diff = 0.0
                total_diff = 0.0
                diffs = []
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nh = result[y + dy][x + dx]
                        d = h - nh
                        if d > talus:
                            diffs.append((dy, dx, d))
                            total_diff += d
                            max_diff = max(max_diff, d)
                if total_diff > 0:
                    move = max_diff * 0.5
                    for dy, dx, d in diffs:
                        share = (d / total_diff) * move * 0.3
                        result[y][x] -= share
                        result[y + dy][x + dx] += share
    return result


# ---------------------------------------------------------------------------
# Terrain analysis / health
# ---------------------------------------------------------------------------

def _analyze_terrain(elev, width, height, rivers):
    stats = {}
    flat = [elev[y][x] for y in range(height) for x in range(width)]
    stats["min"] = min(flat)
    stats["max"] = max(flat)
    stats["mean"] = sum(flat) / len(flat)
    variance = sum((v - stats["mean"]) ** 2 for v in flat) / len(flat)
    stats["stddev"] = math.sqrt(variance)
    stats["range"] = stats["max"] - stats["min"]

    # roughness (avg abs local gradient)
    total_grad = 0.0
    cnt = 0
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            gx = abs(elev[y][x + 1] - elev[y][x - 1])
            gy = abs(elev[y + 1][x] - elev[y - 1][x])
            total_grad += (gx + gy) / 2
            cnt += 1
    stats["roughness"] = total_grad / cnt if cnt else 0

    # drainage density
    river_cells = sum(1 for y in range(height) for x in range(width) if rivers[y][x] > 2)
    stats["drainage_density"] = river_cells / (width * height)

    # ocean fraction (below 0)
    ocean = sum(1 for v in flat if v < 0)
    stats["ocean_fraction"] = ocean / len(flat)

    # findings
    findings = []
    if stats["range"] < 0.3:
        findings.append(("warning", "Terrain is very flat — range < 0.3"))
    if stats["roughness"] > 0.15:
        findings.append(("info", "High roughness — terrain is very rugged"))
    if stats["roughness"] < 0.005:
        findings.append(("warning", "Very low roughness — terrain is unrealistically smooth"))
    if stats["drainage_density"] < 0.01:
        findings.append(("warning", "Sparse drainage network — rivers barely formed"))
    if stats["drainage_density"] > 0.3:
        findings.append(("info", "Very dense drainage — lots of water flow"))
    if stats["ocean_fraction"] > 0.9:
        findings.append(("warning", "Over 90% ocean — very little landmass"))
    if stats["ocean_fraction"] < 0.05:
        findings.append(("info", "Almost no ocean — fully continental terrain"))
    if stats["stddev"] < 0.05:
        findings.append(("warning", "Very low elevation variance — terrain looks uniform"))

    # recommendations
    recs = []
    if stats["range"] < 0.3:
        recs.append("Increase tectonic activity (more plates or stronger convergence)")
    if stats["drainage_density"] < 0.01:
        recs.append("Run more erosion passes to carve river networks")
    if stats["roughness"] < 0.005:
        recs.append("Reduce thermal erosion or add more tectonic plates")
    if stats["ocean_fraction"] > 0.85:
        recs.append("Lower base elevation or use 'pangaea' preset for more land")
    if stats["roughness"] > 0.15:
        recs.append("Apply more thermal erosion to smooth extreme slopes")
    if not recs:
        recs.append("Terrain looks good! Try different presets for variety.")

    return stats, findings, recs


# ---------------------------------------------------------------------------
# Auto-sculpt: autonomous parameter tuning
# ---------------------------------------------------------------------------

def _auto_sculpt(width, height, seeds, vectors, base_elev, max_rounds=5):
    """Iteratively adjust parameters until terrain is 'realistic'."""
    erosion_steps = 400
    thermal_steps = 150

    best_elev = None
    best_rivers = None
    best_score = -1

    for rnd in range(max_rounds):
        print(f"  [auto-sculpt] round {rnd + 1}/{max_rounds} "
              f"(erosion={erosion_steps}, thermal={thermal_steps})")

        plate_grid = _assign_plates(width, height, seeds)
        elev, interactions = _build_tectonic_elevation(
            width, height, plate_grid, seeds, vectors, base_elev
        )
        elev, rivers = _hydraulic_erosion(elev, width, height, erosion_steps)
        elev = _thermal_erosion(elev, width, height, min(thermal_steps, 3), talus=0.04)

        stats, findings, _ = _analyze_terrain(elev, width, height, rivers)

        # score: prefer moderate range, roughness, drainage
        score = 0
        if 0.4 < stats["range"] < 1.5:
            score += 2
        if 0.01 < stats["roughness"] < 0.12:
            score += 2
        if 0.02 < stats["drainage_density"] < 0.25:
            score += 2
        if 0.1 < stats["ocean_fraction"] < 0.7:
            score += 1

        warnings = sum(1 for s, _ in findings if s == "warning")
        score -= warnings

        if score > best_score:
            best_score = score
            best_elev = elev
            best_rivers = rivers

        if score >= 5:
            print(f"  [auto-sculpt] achieved good score ({score}) — done")
            break

        # adjust
        if stats["range"] < 0.3:
            base_elev += 0.1
        if stats["drainage_density"] < 0.01:
            erosion_steps = int(erosion_steps * 1.5)
        if stats["roughness"] < 0.005:
            thermal_steps = max(thermal_steps - 50, 10)

    return best_elev, best_rivers


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------

def _generate_html(elev, width, height, rivers, plate_grid, seeds, vectors,
                   interactions, stats, findings, recs, preset_name):
    """Generate self-contained interactive HTML terrain report."""

    # Flatten elevation for JS
    flat_elev = []
    for y in range(height):
        for x in range(width):
            flat_elev.append(round(elev[y][x], 4))

    flat_rivers = []
    for y in range(height):
        for x in range(width):
            flat_rivers.append(rivers[y][x])

    flat_plates = []
    for y in range(height):
        for x in range(width):
            flat_plates.append(plate_grid[y][x])

    # Interaction map for JS
    imap = {}
    for (a, b), t in interactions.items():
        imap[f"{a}-{b}"] = t

    findings_html = ""
    for sev, msg in findings:
        badge = "⚠️" if sev == "warning" else "ℹ️"
        color = "#f59e0b" if sev == "warning" else "#3b82f6"
        findings_html += f'<div style="padding:6px 10px;margin:3px 0;background:{color}22;border-left:3px solid {color};border-radius:4px;font-size:13px">{badge} {html_mod.escape(msg)}</div>'

    recs_html = ""
    for r in recs:
        recs_html += f'<div style="padding:6px 10px;margin:3px 0;background:#10b98122;border-left:3px solid #10b981;border-radius:4px;font-size:13px">💡 {html_mod.escape(r)}</div>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>VoronoiMap Terraform — Terrain Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0}}
.header{{padding:20px 24px;background:#1e293b;border-bottom:1px solid #334155}}
.header h1{{font-size:22px;color:#f1f5f9}}
.header p{{font-size:13px;color:#94a3b8;margin-top:4px}}
.wrap{{display:flex;gap:16px;padding:16px}}
.left{{flex:1;min-width:0}}
.right{{width:320px;flex-shrink:0}}
canvas{{width:100%;border-radius:8px;background:#000;cursor:crosshair}}
.panel{{background:#1e293b;border-radius:8px;padding:14px;margin-bottom:12px;border:1px solid #334155}}
.panel h3{{font-size:14px;color:#94a3b8;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px}}
.stat{{display:flex;justify-content:space-between;padding:4px 0;font-size:13px}}
.stat .lbl{{color:#94a3b8}}.stat .val{{color:#f1f5f9;font-weight:600}}
.toggles label{{display:block;font-size:13px;padding:3px 0;cursor:pointer}}
.toggles input{{margin-right:6px}}
.presets button{{margin:3px;padding:5px 12px;border:1px solid #475569;background:#0f172a;color:#e2e8f0;border-radius:4px;cursor:pointer;font-size:12px}}
.presets button:hover{{background:#334155}}
.profile-box{{background:#1e293b;border-radius:8px;padding:10px;margin-top:12px;border:1px solid #334155}}
.profile-box canvas{{height:120px}}
</style></head><body>
<div class="header">
<h1>🏔️ VoronoiMap Terraform — Autonomous Terrain Sculptor</h1>
<p>Preset: <strong>{html_mod.escape(preset_name)}</strong> | {len(seeds)} plates | {width}×{height} grid</p>
</div>
<div class="wrap">
<div class="left">
<canvas id="terrain" width="{width}" height="{height}"></canvas>
<div class="profile-box"><h3 style="font-size:12px;color:#94a3b8;margin-bottom:4px">📐 Elevation Profile — click & drag on terrain</h3>
<canvas id="profile" width="{width}" height="120"></canvas></div>
</div>
<div class="right">
<div class="panel toggles"><h3>Overlays</h3>
<label><input type="checkbox" id="showPlates"> Plate boundaries</label>
<label><input type="checkbox" id="showRivers" checked> Rivers / drainage</label>
</div>
<div class="panel"><h3>Terrain Stats</h3>
<div class="stat"><span class="lbl">Min elevation</span><span class="val">{stats['min']:.3f}</span></div>
<div class="stat"><span class="lbl">Max elevation</span><span class="val">{stats['max']:.3f}</span></div>
<div class="stat"><span class="lbl">Mean elevation</span><span class="val">{stats['mean']:.3f}</span></div>
<div class="stat"><span class="lbl">Std deviation</span><span class="val">{stats['stddev']:.3f}</span></div>
<div class="stat"><span class="lbl">Roughness</span><span class="val">{stats['roughness']:.4f}</span></div>
<div class="stat"><span class="lbl">Drainage density</span><span class="val">{stats['drainage_density']:.3f}</span></div>
<div class="stat"><span class="lbl">Ocean fraction</span><span class="val">{stats['ocean_fraction']:.1%}</span></div>
</div>
<div class="panel"><h3>🏥 Health Findings</h3>{findings_html if findings_html else '<div style="font-size:13px;color:#6ee7b7">✅ No issues detected</div>'}</div>
<div class="panel"><h3>💡 Recommendations</h3>{recs_html}</div>
<div class="panel presets"><h3>Presets</h3>
<p style="font-size:11px;color:#64748b;margin-bottom:6px">Regenerate via CLI:</p>
<button onclick="alert('Run: python vormap_terraform.py --preset archipelago')">🏝️ Archipelago</button>
<button onclick="alert('Run: python vormap_terraform.py --preset pangaea')">🌍 Pangaea</button>
<button onclick="alert('Run: python vormap_terraform.py --preset rift_valley')">🕳️ Rift Valley</button>
<button onclick="alert('Run: python vormap_terraform.py --preset mountain_range')">⛰️ Mountain Range</button>
<button onclick="alert('Run: python vormap_terraform.py --preset plains')">🌾 Plains</button>
</div>
</div></div>
<script>
const W={width},H={height};
const elev=new Float32Array({json.dumps(flat_elev)});
const riv=new Int32Array({json.dumps(flat_rivers)});
const plates=new Int32Array({json.dumps(flat_plates)});
const seeds={json.dumps(seeds)};

function colorForElev(v,mn,mx){{
  let t=(v-mn)/(mx-mn||1);
  // ocean: deep blue to light blue (t<0.35)
  // land: green → brown → white
  let r,g,b;
  if(t<0.3){{r=10+t/0.3*30;g=20+t/0.3*60;b=100+t/0.3*100;}}
  else if(t<0.45){{let s=(t-0.3)/0.15;r=40+s*60;g=80+s*100;b=40+s*20;}}
  else if(t<0.65){{let s=(t-0.45)/0.2;r=100+s*80;g=180-s*60;b=60-s*30;}}
  else if(t<0.85){{let s=(t-0.65)/0.2;r=180+s*40;g=120-s*50;b=30+s*20;}}
  else{{let s=(t-0.85)/0.15;r=220+s*35;g=70+s*185;b=50+s*205;}}
  return[r|0,g|0,b|0];
}}

const tc=document.getElementById('terrain');
const ctx=tc.getContext('2d');
const pc=document.getElementById('profile');
const pctx=pc.getContext('2d');

let mn=elev[0],mx=elev[0];
for(let i=1;i<elev.length;i++){{if(elev[i]<mn)mn=elev[i];if(elev[i]>mx)mx=elev[i];}}

function render(){{
  const img=ctx.createImageData(W,H);
  const showP=document.getElementById('showPlates').checked;
  const showR=document.getElementById('showRivers').checked;
  const maxR=Math.max(...riv);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){{
    let i=y*W+x;
    let[r,g,b]=colorForElev(elev[i],mn,mx);
    // river overlay
    if(showR&&riv[i]>3){{
      let ri=Math.min(riv[i]/(maxR*0.3),1);
      r=r*(1-ri*0.7)+30*ri*0.7;
      g=g*(1-ri*0.7)+100*ri*0.7;
      b=b*(1-ri*0.7)+220*ri*0.7;
    }}
    // plate boundary
    if(showP&&x>0&&y>0&&x<W-1&&y<H-1){{
      let p=plates[i];
      if(plates[i-1]!==p||plates[i+1]!==p||plates[i-W]!==p||plates[i+W]!==p){{
        r=255;g=60;b=60;
      }}
    }}
    let o=i*4;
    img.data[o]=r|0;img.data[o+1]=g|0;img.data[o+2]=b|0;img.data[o+3]=255;
  }}
  ctx.putImageData(img,0,0);
  // seed markers
  ctx.fillStyle='rgba(255,255,255,0.6)';
  for(const[sx,sy]of seeds){{ctx.beginPath();ctx.arc(sx,sy,3,0,Math.PI*2);ctx.fill();}}
}}

render();
document.getElementById('showPlates').onchange=render;
document.getElementById('showRivers').onchange=render;

// Profile on drag
let drag=null;
tc.onmousedown=e=>{{const r=tc.getBoundingClientRect();drag={{x:Math.round(e.clientX-r.left)*W/r.width|0,y:Math.round(e.clientY-r.top)*H/r.height|0}}}};
tc.onmouseup=e=>{{
  if(!drag)return;
  const r=tc.getBoundingClientRect();
  const ex=Math.round(e.clientX-r.left)*W/r.width|0;
  const ey=Math.round(e.clientY-r.top)*H/r.height|0;
  // sample profile
  const steps=Math.max(Math.abs(ex-drag.x),Math.abs(ey-drag.y),1);
  const vals=[];
  for(let i=0;i<=steps;i++){{
    const sx=Math.round(drag.x+(ex-drag.x)*i/steps);
    const sy=Math.round(drag.y+(ey-drag.y)*i/steps);
    if(sx>=0&&sx<W&&sy>=0&&sy<H)vals.push(elev[sy*W+sx]);
  }}
  // draw profile
  pctx.clearRect(0,0,pc.width,pc.height);
  if(vals.length<2){{drag=null;return;}}
  let pmn=Math.min(...vals),pmx=Math.max(...vals);
  if(pmx===pmn)pmx=pmn+0.1;
  pctx.strokeStyle='#38bdf8';pctx.lineWidth=2;pctx.beginPath();
  for(let i=0;i<vals.length;i++){{
    const px=i/(vals.length-1)*pc.width;
    const py=pc.height-((vals[i]-pmn)/(pmx-pmn))*pc.height*0.85-5;
    if(i===0)pctx.moveTo(px,py);else pctx.lineTo(px,py);
  }}
  pctx.stroke();
  // zero line
  if(pmn<0&&pmx>0){{
    const zy=pc.height-((-pmn)/(pmx-pmn))*pc.height*0.85-5;
    pctx.strokeStyle='rgba(255,255,255,0.2)';pctx.lineWidth=1;
    pctx.beginPath();pctx.moveTo(0,zy);pctx.lineTo(pc.width,zy);pctx.stroke();
    pctx.fillStyle='#64748b';pctx.font='10px sans-serif';pctx.fillText('sea level',4,zy-3);
  }}
  drag=null;
}};
</script></body></html>"""


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESETS = {
    "archipelago": {"points": 18, "base_elev": -0.15, "erosion_steps": 600, "thermal_steps": 100},
    "pangaea": {"points": 8, "base_elev": 0.2, "erosion_steps": 500, "thermal_steps": 200},
    "rift_valley": {"points": 14, "base_elev": 0.05, "erosion_steps": 700, "thermal_steps": 80},
    "mountain_range": {"points": 10, "base_elev": 0.1, "erosion_steps": 400, "thermal_steps": 250},
    "plains": {"points": 6, "base_elev": 0.08, "erosion_steps": 900, "thermal_steps": 300},
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Terrain Sculptor — Voronoi tectonic plates + erosion simulation"
    )
    parser.add_argument("--points", type=int, default=12, help="Number of tectonic plates (default 12)")
    parser.add_argument("--width", type=int, default=200, help="Grid width (default 200)")
    parser.add_argument("--height", type=int, default=200, help="Grid height (default 200)")
    parser.add_argument("--erosion-steps", type=int, default=500, help="Hydraulic erosion iterations")
    parser.add_argument("--thermal-steps", type=int, default=200, help="Thermal erosion iterations")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), help="Terrain preset")
    parser.add_argument("--auto-sculpt", action="store_true", help="Enable autonomous parameter tuning")
    parser.add_argument("--output", default="terraform_report.html", help="Output HTML file")
    parser.add_argument("--json", action="store_true", help="Also export terrain data as JSON")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    width, height = args.width, args.height
    n_points = args.points
    erosion_steps = args.erosion_steps
    thermal_steps = args.thermal_steps
    base_elev = 0.0
    preset_name = args.preset or "custom"

    if args.preset:
        p = PRESETS[args.preset]
        n_points = p["points"]
        base_elev = p["base_elev"]
        erosion_steps = p["erosion_steps"]
        thermal_steps = p["thermal_steps"]

    print(f"🏔️  VoronoiMap Terraform — Autonomous Terrain Sculptor")
    print(f"   Grid: {width}×{height} | Plates: {n_points} | Preset: {preset_name}")

    # Generate plate seeds and movement vectors
    seeds = [(random.randint(10, width - 10), random.randint(10, height - 10))
             for _ in range(n_points)]
    vectors = [(random.uniform(-1, 1), random.uniform(-1, 1)) for _ in range(n_points)]

    # 1. Plate assignment
    print("   [1/5] Assigning tectonic plates...")
    plate_grid = _assign_plates(width, height, seeds)

    if args.auto_sculpt:
        print("   [AUTO] Autonomous sculpting enabled — tuning parameters...")
        elev, rivers = _auto_sculpt(width, height, seeds, vectors, base_elev)
        # Rebuild plate info for report
        _, interactions = _build_tectonic_elevation(
            width, height, plate_grid, seeds, vectors, base_elev
        )
    else:
        # 2. Tectonic elevation
        print("   [2/5] Computing tectonic elevation...")
        elev, interactions = _build_tectonic_elevation(
            width, height, plate_grid, seeds, vectors, base_elev
        )

        # 3. Hydraulic erosion
        print(f"   [3/5] Hydraulic erosion ({erosion_steps} steps)...")
        elev, rivers = _hydraulic_erosion(elev, width, height, erosion_steps)

        # 4. Thermal erosion (limited iterations on full grid)
        t_iters = min(thermal_steps, 3)  # cap full-grid passes for performance
        print(f"   [4/5] Thermal erosion ({t_iters} passes)...")
        elev = _thermal_erosion(elev, width, height, t_iters, talus=0.04)

    # 5. Analysis
    print("   [5/5] Analyzing terrain health...")
    stats, findings, recs = _analyze_terrain(elev, width, height, rivers)

    for sev, msg in findings:
        icon = "⚠️ " if sev == "warning" else "ℹ️ "
        print(f"         {icon}{msg}")
    for r in recs:
        print(f"         💡 {r}")

    # Generate HTML
    print(f"   Generating report → {args.output}")
    html_content = _generate_html(
        elev, width, height, rivers, plate_grid, seeds, vectors,
        interactions, stats, findings, recs, preset_name
    )
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"   ✅ Saved: {args.output}")

    # JSON export
    if args.json:
        json_path = args.output.replace(".html", ".json")
        data = {
            "width": width,
            "height": height,
            "n_plates": n_points,
            "preset": preset_name,
            "stats": stats,
            "findings": [{"severity": s, "message": m} for s, m in findings],
            "recommendations": recs,
            "seeds": seeds,
        }
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"   ✅ JSON: {json_path}")

    print("   Done! 🌍")


if __name__ == "__main__":
    main()
