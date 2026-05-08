"""3-D Voronoi Tessellation Visualizer (vormap_voronoi3d).

Extends VoronoiMap into three dimensions.  Given a set of 3-D seed points
this module:

- Computes the **3-D Voronoi tessellation** via ``scipy.spatial.Voronoi``
- Calculates **cell volumes, surface areas, and neighbor counts**
- Detects **outlier cells** (abnormally large/small by z-score)
- Generates an **interactive HTML report** with a Three.js scene:
  orbit controls, cell wireframes, seed markers, volume heatmap coloring,
  click-to-inspect, cross-section slicer, and cell statistics panel.

Usage (CLI)::

    python vormap_voronoi3d.py                          # 40 random seeds
    python vormap_voronoi3d.py --seeds 100              # 100 random seeds
    python vormap_voronoi3d.py -i points3d.csv -o out.html
    python vormap_voronoi3d.py --preset crystal --seeds 60
    python vormap_voronoi3d.py --preset galaxy --seeds 200 --opacity 0.25

CSV format: ``x,y,z`` (one point per line, optional header row).

Presets:
    random    – uniform random in unit cube (default)
    crystal   – BCC lattice with Gaussian jitter
    galaxy    – spiral arm distribution
    cluster   – 4 Gaussian clusters
    shell     – points on nested spherical shells

Usage (Python API)::

    from vormap_voronoi3d import voronoi3d_analysis, export_voronoi3d_html

    seeds = [(0.1, 0.2, 0.3), (0.8, 0.7, 0.1), ...]
    result = voronoi3d_analysis(seeds)
    export_voronoi3d_html(result, "voronoi3d.html")

"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

try:
    import numpy as np
    from scipy.spatial import Voronoi as ScipyVoronoi, ConvexHull
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Data Classes ─────────────────────────────────────────────────────

@dataclass
class CellInfo:
    """Statistics for a single Voronoi cell."""
    index: int
    seed: Tuple[float, float, float]
    volume: float
    surface_area: float
    num_faces: int
    num_neighbors: int
    is_bounded: bool
    is_outlier: bool = False


@dataclass
class Voronoi3DResult:
    """Full analysis result."""
    seeds: List[Tuple[float, float, float]]
    cells: List[CellInfo]
    # Raw Voronoi data for HTML export
    vertices: list  # list of (x,y,z)
    regions: list   # list of list of vertex indices
    ridge_vertices: list  # list of pairs of vertex-index lists (faces)
    ridge_points: list    # list of pairs of seed indices
    bounds_min: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounds_max: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    outlier_count: int = 0
    stats: dict = field(default_factory=dict)


# ── Presets ──────────────────────────────────────────────────────────

def _preset_random(n: int) -> List[Tuple[float, float, float]]:
    return [(random.random(), random.random(), random.random()) for _ in range(n)]


def _preset_crystal(n: int) -> List[Tuple[float, float, float]]:
    """BCC lattice with jitter."""
    side = max(2, int(round(n ** (1 / 3))))
    pts = []
    for ix in range(side):
        for iy in range(side):
            for iz in range(side):
                x = (ix + 0.5) / side + random.gauss(0, 0.02)
                y = (iy + 0.5) / side + random.gauss(0, 0.02)
                z = (iz + 0.5) / side + random.gauss(0, 0.02)
                pts.append((max(0, min(1, x)), max(0, min(1, y)), max(0, min(1, z))))
                # BCC center
                if len(pts) < n:
                    cx = (ix + 1.0) / side + random.gauss(0, 0.02)
                    cy = (iy + 1.0) / side + random.gauss(0, 0.02)
                    cz = (iz + 1.0) / side + random.gauss(0, 0.02)
                    pts.append((max(0, min(1, cx)), max(0, min(1, cy)), max(0, min(1, cz))))
                if len(pts) >= n:
                    return pts[:n]
    return pts[:n]


def _preset_galaxy(n: int) -> List[Tuple[float, float, float]]:
    """Spiral arms."""
    pts = []
    arms = 3
    for i in range(n):
        arm = i % arms
        t = (i / n) * 4 * math.pi
        r = 0.1 + 0.35 * (i / n)
        angle = t + arm * (2 * math.pi / arms)
        x = 0.5 + r * math.cos(angle) + random.gauss(0, 0.03)
        y = 0.5 + r * math.sin(angle) + random.gauss(0, 0.03)
        z = 0.5 + random.gauss(0, 0.05)
        pts.append((max(0, min(1, x)), max(0, min(1, y)), max(0, min(1, z))))
    return pts


def _preset_cluster(n: int) -> List[Tuple[float, float, float]]:
    centers = [(0.25, 0.25, 0.25), (0.75, 0.75, 0.25),
               (0.25, 0.75, 0.75), (0.75, 0.25, 0.75)]
    pts = []
    for i in range(n):
        c = centers[i % len(centers)]
        pts.append(tuple(max(0, min(1, c[j] + random.gauss(0, 0.08))) for j in range(3)))
    return pts


def _preset_shell(n: int) -> List[Tuple[float, float, float]]:
    """Points on nested spherical shells."""
    pts = []
    radii = [0.15, 0.3, 0.45]
    for i in range(n):
        r = radii[i % len(radii)] + random.gauss(0, 0.01)
        theta = random.uniform(0, 2 * math.pi)
        phi = math.acos(random.uniform(-1, 1))
        x = 0.5 + r * math.sin(phi) * math.cos(theta)
        y = 0.5 + r * math.sin(phi) * math.sin(theta)
        z = 0.5 + r * math.cos(phi)
        pts.append((max(0, min(1, x)), max(0, min(1, y)), max(0, min(1, z))))
    return pts


PRESETS = {
    "random": _preset_random,
    "crystal": _preset_crystal,
    "galaxy": _preset_galaxy,
    "cluster": _preset_cluster,
    "shell": _preset_shell,
}


# ── Core Analysis ────────────────────────────────────────────────────

def voronoi3d_analysis(
    seeds: List[Tuple[float, float, float]],
    outlier_z: float = 2.0,
) -> Voronoi3DResult:
    """Compute 3-D Voronoi tessellation and cell statistics.

    Parameters
    ----------
    seeds : list of (x, y, z)
    outlier_z : float
        Z-score threshold for volume outlier detection.

    Returns
    -------
    Voronoi3DResult
    """
    if not _HAS_SCIPY:
        raise ImportError("scipy and numpy are required: pip install scipy numpy")
    if len(seeds) < 4:
        raise ValueError("At least 4 non-coplanar seeds are required for 3-D Voronoi")

    pts = np.array(seeds, dtype=float)
    vor = ScipyVoronoi(pts)

    vertices = [tuple(v) for v in vor.vertices]
    regions = [list(r) for r in vor.regions]
    ridge_vertices = [[int(x) for x in rv] for rv in vor.ridge_vertices]
    ridge_points = [[int(x) for x in rp] for rp in vor.ridge_points]

    bmin = tuple(pts.min(axis=0).tolist())
    bmax = tuple(pts.max(axis=0).tolist())

    # Compute cell volumes and surface areas
    cells: List[CellInfo] = []
    volumes = []
    for idx in range(len(seeds)):
        region_idx = vor.point_region[idx]
        region = vor.regions[region_idx]
        is_bounded = -1 not in region and len(region) > 0

        vol = 0.0
        sa = 0.0
        n_faces = 0

        if is_bounded and len(region) >= 4:
            try:
                cell_verts = vor.vertices[region]
                hull = ConvexHull(cell_verts)
                vol = hull.volume
                sa = hull.area
                n_faces = len(hull.simplices)
            except Exception:
                is_bounded = False

        # Count neighbors
        n_neighbors = 0
        for rp in vor.ridge_points:
            if idx in rp:
                n_neighbors += 1

        cell = CellInfo(
            index=idx,
            seed=seeds[idx],
            volume=vol,
            surface_area=sa,
            num_faces=n_faces,
            num_neighbors=n_neighbors,
            is_bounded=is_bounded,
        )
        cells.append(cell)
        if is_bounded and vol > 0:
            volumes.append(vol)

    # Outlier detection
    outlier_count = 0
    if volumes:
        mean_v = sum(volumes) / len(volumes)
        std_v = (sum((v - mean_v) ** 2 for v in volumes) / len(volumes)) ** 0.5
        if std_v > 0:
            for cell in cells:
                if cell.is_bounded and cell.volume > 0:
                    z = abs(cell.volume - mean_v) / std_v
                    if z > outlier_z:
                        cell.is_outlier = True
                        outlier_count += 1

    bounded_cells = [c for c in cells if c.is_bounded and c.volume > 0]
    stats = {}
    if bounded_cells:
        vols = [c.volume for c in bounded_cells]
        stats = {
            "total_seeds": len(seeds),
            "bounded_cells": len(bounded_cells),
            "unbounded_cells": len(seeds) - len(bounded_cells),
            "volume_min": min(vols),
            "volume_max": max(vols),
            "volume_mean": sum(vols) / len(vols),
            "volume_std": (sum((v - sum(vols) / len(vols)) ** 2 for v in vols) / len(vols)) ** 0.5,
            "outlier_count": outlier_count,
        }

    return Voronoi3DResult(
        seeds=seeds,
        cells=cells,
        vertices=vertices,
        regions=regions,
        ridge_vertices=ridge_vertices,
        ridge_points=ridge_points,
        bounds_min=bmin,
        bounds_max=bmax,
        outlier_count=outlier_count,
        stats=stats,
    )


# ── HTML Export ──────────────────────────────────────────────────────

def export_voronoi3d_html(result: Voronoi3DResult, path: str, opacity: float = 0.3) -> str:
    """Write interactive Three.js HTML visualization."""
    seeds_json = json.dumps(result.seeds)
    vertices_json = json.dumps(result.vertices)
    ridge_vertices_json = json.dumps(result.ridge_vertices)
    ridge_points_json = json.dumps(result.ridge_points)
    cells_json = json.dumps([
        {
            "index": c.index,
            "seed": list(c.seed),
            "volume": round(c.volume, 6),
            "surface_area": round(c.surface_area, 6),
            "num_faces": c.num_faces,
            "num_neighbors": c.num_neighbors,
            "is_bounded": c.is_bounded,
            "is_outlier": c.is_outlier,
        }
        for c in result.cells
    ])
    stats_json = json.dumps(result.stats, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VoronoiMap 3D Tessellation</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #0a0a1a; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; overflow: hidden; }}
  #container {{ width: 100vw; height: 100vh; }}
  #panel {{
    position: fixed; top: 10px; right: 10px; width: 320px;
    background: rgba(10,10,30,0.92); border: 1px solid #333; border-radius: 8px;
    padding: 16px; font-size: 13px; max-height: 90vh; overflow-y: auto;
    z-index: 100; backdrop-filter: blur(8px);
  }}
  #panel h2 {{ color: #6cf; margin-bottom: 10px; font-size: 16px; }}
  #panel h3 {{ color: #aaa; margin: 12px 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}
  .stat {{ display: flex; justify-content: space-between; padding: 3px 0; border-bottom: 1px solid #222; }}
  .stat-label {{ color: #888; }}
  .stat-value {{ color: #6cf; font-weight: 600; }}
  #cell-info {{ margin-top: 10px; padding: 10px; background: rgba(100,200,255,0.08); border-radius: 6px; display: none; }}
  #cell-info.active {{ display: block; }}
  .controls {{ margin-top: 12px; }}
  .controls label {{ display: block; margin: 6px 0 2px; color: #888; font-size: 12px; }}
  .controls input[type=range] {{ width: 100%; }}
  .controls select {{ width: 100%; background: #1a1a2e; color: #e0e0e0; border: 1px solid #333; padding: 4px; border-radius: 4px; }}
  .legend {{ display: flex; align-items: center; margin: 8px 0; }}
  .legend-bar {{ height: 12px; flex: 1; border-radius: 3px; background: linear-gradient(to right, #0000ff, #00ffff, #00ff00, #ffff00, #ff0000); }}
  .legend span {{ font-size: 11px; color: #888; margin: 0 4px; }}
  #help {{ position: fixed; bottom: 10px; left: 10px; background: rgba(10,10,30,0.85); padding: 10px 14px; border-radius: 6px; font-size: 12px; color: #666; z-index: 100; }}
</style>
</head>
<body>
<div id="container"></div>
<div id="panel">
  <h2>&#x1f9ca; 3D Voronoi Tessellation</h2>
  <h3>Statistics</h3>
  <div id="stats"></div>
  <div class="legend">
    <span>Small</span><div class="legend-bar"></div><span>Large</span>
  </div>
  <div class="controls">
    <label>Cell Opacity: <span id="opVal">{opacity}</span></label>
    <input type="range" id="opSlider" min="0" max="1" step="0.05" value="{opacity}">
    <label>Cross-Section Z</label>
    <input type="range" id="sliceSlider" min="0" max="1" step="0.01" value="1">
    <label>Color By</label>
    <select id="colorBy">
      <option value="volume">Volume</option>
      <option value="neighbors">Neighbors</option>
      <option value="surface_area">Surface Area</option>
    </select>
  </div>
  <div id="cell-info">
    <h3>Selected Cell</h3>
    <div id="cell-details"></div>
  </div>
</div>
<div id="help">Click seed to inspect &bull; Scroll to zoom &bull; Drag to orbit &bull; Shift+drag to pan</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script>
// OrbitControls inline (minimal)
THREE.OrbitControls=function(o,d){{this.object=o;this.domElement=d;this.enabled=!0;this.target=new THREE.Vector3;this.minDistance=0;this.maxDistance=Infinity;this.enableDamping=!0;this.dampingFactor=.08;this.rotateSpeed=.8;this.zoomSpeed=1.2;this.panSpeed=.8;var s=new THREE.Spherical,p=new THREE.Vector2,m=new THREE.Vector2,v=new THREE.Vector3,state=0;var offset=new THREE.Vector3;this.update=function(){{offset.copy(o.position).sub(this.target);s.setFromVector3(offset);s.theta+=this._dTheta||0;s.phi+=this._dPhi||0;s.phi=Math.max(.01,Math.min(Math.PI-.01,s.phi));s.radius*=this._scale||1;s.radius=Math.max(this.minDistance,Math.min(this.maxDistance,s.radius));this.target.add(v);offset.setFromSpherical(s);o.position.copy(this.target).add(offset);o.lookAt(this.target);if(this.enableDamping){{this._dTheta*=1-this.dampingFactor;this._dPhi*=1-this.dampingFactor;v.multiplyScalar(1-this.dampingFactor)}}else{{this._dTheta=0;this._dPhi=0;v.set(0,0,0)}}this._scale=1}};this._dTheta=0;this._dPhi=0;this._scale=1;var rotStart=new THREE.Vector2,panStart=new THREE.Vector2;d.addEventListener('mousedown',function(e){{if(!this.enabled)return;if(e.shiftKey){{state=2;panStart.set(e.clientX,e.clientY)}}else{{state=1;rotStart.set(e.clientX,e.clientY)}}e.preventDefault()}}.bind(this));d.addEventListener('mousemove',function(e){{if(!this.enabled||state===0)return;if(state===1){{this._dTheta-=(e.clientX-rotStart.x)/d.clientWidth*Math.PI*this.rotateSpeed;this._dPhi-=(e.clientY-rotStart.y)/d.clientHeight*Math.PI*this.rotateSpeed;rotStart.set(e.clientX,e.clientY)}}else if(state===2){{var dx=(e.clientX-panStart.x)/d.clientWidth,dy=(e.clientY-panStart.y)/d.clientHeight;var eye=new THREE.Vector3().copy(o.position).sub(this.target);var dist=eye.length()*this.panSpeed;var right=new THREE.Vector3().crossVectors(o.up,eye).normalize();var up=new THREE.Vector3().crossVectors(eye,right).normalize();v.add(right.multiplyScalar(-dx*dist));v.add(up.multiplyScalar(dy*dist));panStart.set(e.clientX,e.clientY)}}}}.bind(this));d.addEventListener('mouseup',function(){{state=0}});d.addEventListener('wheel',function(e){{if(!this.enabled)return;this._scale*=e.deltaY>0?1/this.zoomSpeed:this.zoomSpeed;e.preventDefault()}}.bind(this),{{passive:false}})}};

const seeds = {seeds_json};
const vertices = {vertices_json};
const ridgeVertices = {ridge_vertices_json};
const ridgePoints = {ridge_points_json};
const cellsData = {cells_json};
const stats = {stats_json};

// Stats panel
const statsEl = document.getElementById('stats');
const statLabels = {{
  total_seeds: 'Total Seeds', bounded_cells: 'Bounded Cells',
  unbounded_cells: 'Unbounded', volume_min: 'Min Volume',
  volume_max: 'Max Volume', volume_mean: 'Mean Volume',
  volume_std: 'Std Dev', outlier_count: 'Outliers'
}};
for (const [k, label] of Object.entries(statLabels)) {{
  if (stats[k] !== undefined) {{
    const v = typeof stats[k] === 'number' ? (stats[k] < 0.01 ? stats[k].toExponential(3) : stats[k].toFixed(4)) : stats[k];
    statsEl.innerHTML += `<div class="stat"><span class="stat-label">${{label}}</span><span class="stat-value">${{v}}</span></div>`;
  }}
}}

// Scene setup
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a0a1a);
const camera = new THREE.PerspectiveCamera(60, window.innerWidth / window.innerHeight, 0.01, 100);
camera.position.set(1.5, 1.5, 1.5);
const renderer = new THREE.WebGLRenderer({{ antialias: true }});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(window.devicePixelRatio);
document.getElementById('container').appendChild(renderer.domElement);
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.target.set(0.5, 0.5, 0.5);

// Lights
scene.add(new THREE.AmbientLight(0x404060, 0.6));
const dl = new THREE.DirectionalLight(0xffffff, 0.8);
dl.position.set(2, 3, 2);
scene.add(dl);

// Bounding box wireframe
const boxGeo = new THREE.BoxGeometry(1, 1, 1);
const boxEdge = new THREE.EdgesGeometry(boxGeo);
const boxLine = new THREE.LineSegments(boxEdge, new THREE.LineBasicMaterial({{ color: 0x333355 }}));
boxLine.position.set(0.5, 0.5, 0.5);
scene.add(boxLine);

// Axes
const axLen = 1.2;
[[0xff4444, [axLen,0,0]], [0x44ff44, [0,axLen,0]], [0x4444ff, [0,0,axLen]]].forEach(([c, e]) => {{
  const g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0), new THREE.Vector3(...e)]);
  scene.add(new THREE.Line(g, new THREE.LineBasicMaterial({{ color: c, transparent: true, opacity: 0.4 }})));
}});

// Color mapping
function heatColor(t) {{
  t = Math.max(0, Math.min(1, t));
  if (t < 0.25) return new THREE.Color(0, t * 4, 1);
  if (t < 0.5) return new THREE.Color(0, 1, 1 - (t - 0.25) * 4);
  if (t < 0.75) return new THREE.Color((t - 0.5) * 4, 1, 0);
  return new THREE.Color(1, 1 - (t - 0.75) * 4, 0);
}}

// Seed spheres
const seedGroup = new THREE.Group();
const seedMeshes = [];
let currentOpacity = {opacity};
let currentSlice = 1.0;
let currentColorBy = 'volume';

function getMetricRange(field) {{
  const bounded = cellsData.filter(c => c.is_bounded && c.volume > 0);
  if (!bounded.length) return [0, 1];
  const vals = bounded.map(c => c[field] || 0);
  return [Math.min(...vals), Math.max(...vals)];
}}

function colorForCell(cell) {{
  if (!cell.is_bounded || cell.volume <= 0) return new THREE.Color(0.3, 0.3, 0.3);
  const [mn, mx] = getMetricRange(currentColorBy);
  const val = cell[currentColorBy] || 0;
  const t = mx > mn ? (val - mn) / (mx - mn) : 0.5;
  return heatColor(t);
}}

seeds.forEach((s, i) => {{
  const geo = new THREE.SphereGeometry(0.008, 12, 12);
  const col = colorForCell(cellsData[i]);
  const mat = new THREE.MeshPhongMaterial({{ color: col, emissive: col.clone().multiplyScalar(0.3) }});
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(s[0], s[1], s[2]);
  mesh.userData = {{ cellIndex: i }};
  seedGroup.add(mesh);
  seedMeshes.push(mesh);
}});
scene.add(seedGroup);

// Ridge lines (Voronoi edges)
const ridgeGroup = new THREE.Group();
const ridgeMaterials = [];
ridgeVertices.forEach((rv, ri) => {{
  if (rv.includes(-1)) return;
  const pts = rv.map(vi => new THREE.Vector3(vertices[vi][0], vertices[vi][1], vertices[vi][2]));
  if (pts.length < 3) return;
  // Draw edges of the face polygon
  const rp = ridgePoints[ri];
  const avgCol = colorForCell(cellsData[rp[0]]).clone().lerp(colorForCell(cellsData[rp[1]]), 0.5);
  const mat = new THREE.LineBasicMaterial({{ color: avgCol, transparent: true, opacity: currentOpacity }});
  ridgeMaterials.push(mat);
  for (let j = 0; j < pts.length; j++) {{
    const g = new THREE.BufferGeometry().setFromPoints([pts[j], pts[(j + 1) % pts.length]]);
    const line = new THREE.Line(g, mat);
    line.userData = {{ maxZ: Math.max(pts[j].z, pts[(j + 1) % pts.length].z) }};
    ridgeGroup.add(line);
  }}
}});
scene.add(ridgeGroup);

// Controls
document.getElementById('opSlider').addEventListener('input', function() {{
  currentOpacity = parseFloat(this.value);
  document.getElementById('opVal').textContent = currentOpacity.toFixed(2);
  ridgeMaterials.forEach(m => {{ m.opacity = currentOpacity; }});
}});

document.getElementById('sliceSlider').addEventListener('input', function() {{
  currentSlice = parseFloat(this.value);
  ridgeGroup.children.forEach(c => {{
    c.visible = (c.userData.maxZ || 0) <= currentSlice;
  }});
  seedMeshes.forEach((m, i) => {{
    m.visible = seeds[i][2] <= currentSlice;
  }});
}});

document.getElementById('colorBy').addEventListener('change', function() {{
  currentColorBy = this.value;
  seedMeshes.forEach((m, i) => {{
    const col = colorForCell(cellsData[i]);
    m.material.color.copy(col);
    m.material.emissive.copy(col).multiplyScalar(0.3);
  }});
}});

// Raycaster for click-to-inspect
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
renderer.domElement.addEventListener('click', function(e) {{
  mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
  mouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  const hits = raycaster.intersectObjects(seedMeshes);
  const infoEl = document.getElementById('cell-info');
  const detEl = document.getElementById('cell-details');
  if (hits.length > 0) {{
    const ci = hits[0].object.userData.cellIndex;
    const c = cellsData[ci];
    infoEl.className = 'active';
    detEl.innerHTML = `
      <div class="stat"><span class="stat-label">Cell #</span><span class="stat-value">${{c.index}}</span></div>
      <div class="stat"><span class="stat-label">Seed</span><span class="stat-value">($${{c.seed.map(v=>v.toFixed(3)).join(', ')}})</span></div>
      <div class="stat"><span class="stat-label">Volume</span><span class="stat-value">${{c.volume.toFixed(6)}}</span></div>
      <div class="stat"><span class="stat-label">Surface Area</span><span class="stat-value">${{c.surface_area.toFixed(6)}}</span></div>
      <div class="stat"><span class="stat-label">Faces</span><span class="stat-value">${{c.num_faces}}</span></div>
      <div class="stat"><span class="stat-label">Neighbors</span><span class="stat-value">${{c.num_neighbors}}</span></div>
      <div class="stat"><span class="stat-label">Bounded</span><span class="stat-value">${{c.is_bounded ? '✅' : '❌'}}</span></div>
      <div class="stat"><span class="stat-label">Outlier</span><span class="stat-value">${{c.is_outlier ? '⚠️ Yes' : 'No'}}</span></div>
    `;
    // Highlight
    seedMeshes.forEach(m => {{ m.scale.set(1,1,1); }});
    hits[0].object.scale.set(2.5, 2.5, 2.5);
  }} else {{
    infoEl.className = '';
    seedMeshes.forEach(m => {{ m.scale.set(1,1,1); }});
  }}
}});

// Resize
window.addEventListener('resize', () => {{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});

// Animate
function animate() {{
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}}
animate();
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(os.path.abspath(path)) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return os.path.abspath(path)


# ── CSV I/O ──────────────────────────────────────────────────────────

def load_points_csv(path: str) -> List[Tuple[float, float, float]]:
    """Load 3-D points from CSV (x,y,z per row)."""
    pts = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0].strip().lower().startswith(("x", "#", "//")):
                continue
            try:
                pts.append((float(row[0]), float(row[1]), float(row[2])))
            except (ValueError, IndexError):
                continue
    return pts


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description="3-D Voronoi Tessellation Visualizer — extends VoronoiMap to three dimensions",
    )
    ap.add_argument("-i", "--input", help="CSV file with x,y,z points")
    ap.add_argument("-o", "--output", default="voronoi3d.html", help="Output HTML path (default: voronoi3d.html)")
    ap.add_argument("--seeds", type=int, default=40, help="Number of seed points (default: 40)")
    ap.add_argument("--preset", choices=list(PRESETS.keys()), default="random", help="Point distribution preset")
    ap.add_argument("--opacity", type=float, default=0.3, help="Initial cell wireframe opacity (0-1)")
    ap.add_argument("--outlier-z", type=float, default=2.0, help="Z-score threshold for outlier detection")
    ap.add_argument("--json", action="store_true", help="Also export cell stats as JSON")
    args = ap.parse_args(argv)

    print("[3D] VoronoiMap 3D Tessellation")
    print(f"   Preset: {args.preset} | Seeds: {args.seeds}")

    if args.input:
        seeds = load_points_csv(args.input)
        print(f"   Loaded {len(seeds)} points from {args.input}")
    else:
        seeds = PRESETS[args.preset](args.seeds)
        print(f"   Generated {len(seeds)} {args.preset} points")

    result = voronoi3d_analysis(seeds, outlier_z=args.outlier_z)

    print("\n[Stats] Results:")
    for k, v in result.stats.items():
        label = k.replace("_", " ").title()
        val = f"{v:.6f}" if isinstance(v, float) else str(v)
        print(f"   {label}: {val}")

    out_path = export_voronoi3d_html(result, args.output, opacity=args.opacity)
    print(f"\n[OK] HTML report: {out_path}")

    if args.json:
        json_path = args.output.rsplit(".", 1)[0] + ".json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "seeds": result.seeds,
                "stats": result.stats,
                "cells": [
                    {
                        "index": c.index, "seed": list(c.seed),
                        "volume": c.volume, "surface_area": c.surface_area,
                        "num_faces": c.num_faces, "num_neighbors": c.num_neighbors,
                        "is_bounded": c.is_bounded, "is_outlier": c.is_outlier,
                    }
                    for c in result.cells
                ],
            }, f, indent=2)
        print(f"   JSON export: {json_path}")


if __name__ == "__main__":
    main()
