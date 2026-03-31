"""Interactive HTML Voronoi Playground generator.

Generates a standalone HTML file with an interactive canvas where users
can click to place points and watch Voronoi diagrams update in real-time.
Uses Fortune's algorithm in pure JavaScript — no external dependencies.

Usage:
    python vormap.py --playground playground.html
    python vormap.py points.csv --playground playground.html  # pre-load points
"""

import os

_PLAYGROUND_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VoronoiMap Playground</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#1a1a2e;color:#e0e0e0;display:flex;flex-direction:column;height:100vh;overflow:hidden}
header{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;background:#16213e;border-bottom:1px solid #0f3460}
header h1{font-size:1.1rem;color:#e94560}
.toolbar{display:flex;gap:8px;align-items:center}
.toolbar button{background:#0f3460;color:#e0e0e0;border:1px solid #e94560;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:.85rem;transition:background .2s}
.toolbar button:hover{background:#e94560;color:#fff}
.toolbar select,.toolbar input[type=number]{background:#0f3460;color:#e0e0e0;border:1px solid #0f3460;padding:4px 8px;border-radius:4px;font-size:.85rem}
.toolbar label{font-size:.8rem;color:#aaa}
#info{font-size:.75rem;color:#888;padding:4px 16px;background:#16213e;border-top:1px solid #0f3460;text-align:center}
canvas{flex:1;cursor:crosshair;display:block}
</style>
</head>
<body>
<header>
  <h1>&#9670; VoronoiMap Playground</h1>
  <div class="toolbar">
    <label>Points: <span id="count">0</span></label>
    <select id="colorScheme" title="Color scheme">
      <option value="area">Area gradient</option>
      <option value="random">Random</option>
      <option value="distance">Distance</option>
      <option value="mono">Monochrome</option>
    </select>
    <button id="btnRelax" title="Lloyd relaxation step">Relax</button>
    <button id="btnRandom">+10 Random</button>
    <button id="btnClear">Clear</button>
    <button id="btnExport">Export CSV</button>
  </div>
</header>
<canvas id="canvas"></canvas>
<div id="info">Click to add points &bull; Right-click to remove nearest &bull; Scroll to zoom &bull; Drag to pan</div>
<script>
"use strict";
const canvas=document.getElementById("canvas"),ctx=canvas.getContext("2d");
let W,H,points=__POINTS__,ox=0,oy=0,scale=1,dragging=false,dragStart={x:0,y:0},dragOx=0,dragOy=0;

function resize(){W=canvas.width=canvas.clientWidth;H=canvas.height=canvas.clientHeight;draw()}
window.addEventListener("resize",resize);

// --- Voronoi via Fortune's sweep (simplified brute-force for <5000 pts) ---
function voronoi(pts,w,h){
  // For each pixel approach is too slow; use cell-based: for each point find its polygon
  // We use a simple approach: Delaunay-free polygon clipping with half-plane intersection
  if(pts.length===0)return[];
  const cells=[];
  for(let i=0;i<pts.length;i++){
    let poly=[{x:0,y:0},{x:w,y:0},{x:w,y:h},{x:0,y:h}];
    for(let j=0;j<pts.length;j++){
      if(i===j)continue;
      poly=clipPolygon(poly,pts[i],pts[j]);
      if(poly.length===0)break;
    }
    cells.push(poly);
  }
  return cells;
}

function clipPolygon(poly,pi,pj){
  // Clip polygon to the half-plane closer to pi than pj
  const mx=(pi.x+pj.x)/2,my=(pi.y+pj.y)/2;
  const nx=pj.x-pi.x,ny=pj.y-pi.y;
  const out=[];
  for(let k=0;k<poly.length;k++){
    const a=poly[k],b=poly[(k+1)%poly.length];
    const da=(a.x-mx)*nx+(a.y-my)*ny;
    const db=(b.x-mx)*nx+(b.y-my)*ny;
    if(da<=0)out.push(a);
    if((da<0)!==(db<0)){
      const t=da/(da-db);
      out.push({x:a.x+t*(b.x-a.x),y:a.y+t*(b.y-a.y)});
    }
  }
  return out;
}

function polygonArea(poly){
  let a=0;
  for(let i=0;i<poly.length;i++){
    const j=(i+1)%poly.length;
    a+=poly[i].x*poly[j].y-poly[j].x*poly[i].y;
  }
  return Math.abs(a)/2;
}

function polygonCentroid(poly){
  let cx=0,cy=0,a=0;
  for(let i=0;i<poly.length;i++){
    const j=(i+1)%poly.length;
    const f=poly[i].x*poly[j].y-poly[j].x*poly[i].y;
    cx+=(poly[i].x+poly[j].x)*f;
    cy+=(poly[i].y+poly[j].y)*f;
    a+=f;
  }
  a/=2;
  if(Math.abs(a)<1e-10)return{x:poly[0]?.x||0,y:poly[0]?.y||0};
  return{x:cx/(6*a),y:cy/(6*a)};
}

// Color schemes
function hsl(h,s,l){return`hsl(${h},${s}%,${l}%)`}
function colorByScheme(scheme,i,area,dist,maxArea,maxDist){
  switch(scheme){
    case"random":return hsl((i*137.508)%360,65,45);
    case"area":{const t=maxArea>0?area/maxArea:0;return hsl(240-t*200,70,35+t*25)}
    case"distance":{const t=maxDist>0?dist/maxDist:0;return hsl(t*280,65,30+t*30)}
    case"mono":return hsl(220,10,25+((i%2)*8));
    default:return hsl(200,50,40);
  }
}

function draw(){
  ctx.save();
  ctx.clearRect(0,0,W,H);
  ctx.translate(ox,oy);
  ctx.scale(scale,scale);

  const scheme=document.getElementById("colorScheme").value;
  const vw=W/scale,vh=H/scale;

  if(points.length>0){
    // Map points to screen-independent coords
    const cells=voronoi(points,vw,vh);
    const areas=cells.map(c=>polygonArea(c));
    const dists=points.map(p=>Math.hypot(p.x-vw/2,p.y-vh/2));
    const maxArea=Math.max(...areas,1);
    const maxDist=Math.max(...dists,1);

    for(let i=0;i<cells.length;i++){
      const c=cells[i];
      if(c.length<3)continue;
      ctx.beginPath();
      ctx.moveTo(c[0].x,c[0].y);
      for(let j=1;j<c.length;j++)ctx.lineTo(c[j].x,c[j].y);
      ctx.closePath();
      ctx.fillStyle=colorByScheme(scheme,i,areas[i],dists[i],maxArea,maxDist);
      ctx.fill();
      ctx.strokeStyle="rgba(255,255,255,0.15)";
      ctx.lineWidth=1/scale;
      ctx.stroke();
    }
  }

  // Draw points
  for(const p of points){
    ctx.beginPath();
    ctx.arc(p.x,p.y,3/scale,0,Math.PI*2);
    ctx.fillStyle="#e94560";
    ctx.fill();
  }

  ctx.restore();
  document.getElementById("count").textContent=points.length;
}

function screenToWorld(sx,sy){return{x:(sx-ox)/scale,y:(sy-oy)/scale}}

canvas.addEventListener("click",e=>{
  if(e.button!==0)return;
  const p=screenToWorld(e.offsetX,e.offsetY);
  if(p.x>=0&&p.y>=0)points.push(p);
  draw();
});
canvas.addEventListener("contextmenu",e=>{
  e.preventDefault();
  if(points.length===0)return;
  const p=screenToWorld(e.offsetX,e.offsetY);
  let minD=Infinity,minI=0;
  points.forEach((pt,i)=>{const d=Math.hypot(pt.x-p.x,pt.y-p.y);if(d<minD){minD=d;minI=i}});
  points.splice(minI,1);
  draw();
});
canvas.addEventListener("mousedown",e=>{if(e.button===1||(e.button===0&&e.shiftKey)){dragging=true;dragStart={x:e.offsetX,y:e.offsetY};dragOx=ox;dragOy=oy;e.preventDefault()}});
canvas.addEventListener("mousemove",e=>{if(dragging){ox=dragOx+e.offsetX-dragStart.x;oy=dragOy+e.offsetY-dragStart.y;draw()}});
canvas.addEventListener("mouseup",()=>{dragging=false});
canvas.addEventListener("wheel",e=>{
  e.preventDefault();
  const z=e.deltaY<0?1.1:0.9;
  const mx=e.offsetX,my=e.offsetY;
  ox=mx-(mx-ox)*z;oy=my-(my-oy)*z;
  scale*=z;
  draw();
},{passive:false});

document.getElementById("btnClear").onclick=()=>{points=[];draw()};
document.getElementById("btnRandom").onclick=()=>{
  const vw=W/scale,vh=H/scale;
  for(let i=0;i<10;i++)points.push({x:Math.random()*vw,y:Math.random()*vh});
  draw();
};
document.getElementById("btnRelax").onclick=()=>{
  if(points.length<2)return;
  const vw=W/scale,vh=H/scale;
  const cells=voronoi(points,vw,vh);
  for(let i=0;i<points.length;i++){
    if(cells[i].length>=3){
      const c=polygonCentroid(cells[i]);
      points[i]={x:Math.max(0,Math.min(vw,c.x)),y:Math.max(0,Math.min(vh,c.y))};
    }
  }
  draw();
};
document.getElementById("btnExport").onclick=()=>{
  const csv="x,y\n"+points.map(p=>p.x.toFixed(2)+","+p.y.toFixed(2)).join("\n");
  const a=document.createElement("a");
  a.href="data:text/csv,"+encodeURIComponent(csv);
  a.download="voronoi_points.csv";
  a.click();
};
document.getElementById("colorScheme").onchange=draw;

resize();
</script>
</body>
</html>'''


def generate_playground(output_path, data=None, bounds=None):
    """Generate a standalone interactive Voronoi playground HTML file.

    Parameters
    ----------
    output_path : str
        Path for the output HTML file.
    data : list[tuple] or None
        Optional seed points as (x, y[, ...]) tuples.
    bounds : tuple or None
        (width, height) for the coordinate space. Defaults to auto-detect.

    Returns
    -------
    str
        The absolute path of the written file.
    """
    import json as _json

    if data:
        # Normalize points to a reasonable screen space
        xs = [p[0] for p in data]
        ys = [p[1] for p in data]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        x_range = x_max - x_min or 1
        y_range = y_max - y_min or 1
        # Map to 800x600 with padding
        pad = 40
        w, h = 800, 600
        js_points = [
            {"x": pad + (p[0] - x_min) / x_range * (w - 2 * pad),
             "y": pad + (p[1] - y_min) / y_range * (h - 2 * pad)}
            for p in data
        ]
    else:
        js_points = []

    html = _PLAYGROUND_HTML.replace('__POINTS__', _json.dumps(js_points))

    abs_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_path) or '.', exist_ok=True)
    with open(abs_path, 'w', encoding='utf-8') as f:
        f.write(html)

    return abs_path
