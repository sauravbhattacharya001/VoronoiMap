"""Voronoi diagram visualization — SVG export for VoronoiMap.

Generates SVG files showing Voronoi regions, seed points, and boundary
vertices.  Works with the existing vormap engine; no heavy dependencies
required (uses only the Python standard library for SVG generation).

When scipy is available, uses ``scipy.spatial.Voronoi`` for precise region
computation.  Falls back to the vormap binary-search tracer otherwise.

Usage (as module):
    import vormap
    import vormap_viz

    points = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(points)
    vormap_viz.export_svg(regions, points, "diagram.svg")

Usage (CLI):
    voronoimap datauni5.txt 5 --visualize output.svg
    voronoimap datauni5.txt 5 --visualize output.svg --color-scheme warm
"""

import colorsys
import math
import random
import xml.etree.ElementTree as ET

import vormap

try:
    import numpy as np
    from scipy.spatial import Voronoi as ScipyVoronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Color schemes ────────────────────────────────────────────────────

_COLOR_SCHEMES = {
    "pastel": lambda i, n: _hsl_color(i / max(n, 1), 0.65, 0.82),
    "warm": lambda i, n: _hsl_color(
        (i / max(n, 1)) * 0.15, 0.70, 0.75       # reds → oranges
    ),
    "cool": lambda i, n: _hsl_color(
        0.5 + (i / max(n, 1)) * 0.2, 0.60, 0.78  # cyans → blues
    ),
    "earth": lambda i, n: _hsl_color(
        0.08 + (i / max(n, 1)) * 0.1, 0.45, 0.65  # browns → greens
    ),
    "mono": lambda i, n: _gray_color(0.88 - 0.35 * (i / max(n, 1))),
    "rainbow": lambda i, n: _hsl_color(i / max(n, 1), 0.70, 0.68),
}

DEFAULT_COLOR_SCHEME = "pastel"


def _hsl_color(h, s, l):
    """Convert HSL (0-1 each) to a CSS hex color string."""
    r, g, b = colorsys.hls_to_rgb(h % 1.0, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _gray_color(lightness):
    """Generate a gray hex color from a 0-1 lightness value."""
    v = int(max(0, min(255, lightness * 255)))
    return "#%02x%02x%02x" % (v, v, v)


# ── Region computation ───────────────────────────────────────────────

def _clip_infinite_voronoi(vor, bounds):
    """Clip scipy Voronoi regions to a bounding box.

    Returns a dict mapping point index → list of (x, y) vertices.
    Infinite regions are clipped to the bounding box edges.
    """
    s, n, w, e = bounds
    center = np.array(vor.points.mean(axis=0))
    regions = {}

    for point_idx, region_idx in enumerate(vor.point_region):
        region = vor.regions[region_idx]
        if not region:
            continue

        if -1 not in region:
            # Finite region — just grab the vertices
            verts = [tuple(vor.vertices[v]) for v in region]
            regions[point_idx] = verts
            continue

        # Infinite region — need to clip
        finite_verts = []
        for i, v_idx in enumerate(region):
            if v_idx >= 0:
                finite_verts.append(tuple(vor.vertices[v_idx]))
            else:
                # Find the two ridges that bound this infinite edge
                # and project them to the boundary
                prev_idx = region[i - 1]
                next_idx = region[(i + 1) % len(region)]

                # Project from the finite vertex toward infinity
                for ridge_idx, (p1, p2) in enumerate(vor.ridge_points):
                    if point_idx not in (p1, p2):
                        continue
                    ridge_verts = vor.ridge_vertices[ridge_idx]
                    if -1 not in ridge_verts:
                        continue

                    finite_v = [v for v in ridge_verts if v >= 0]
                    if not finite_v:
                        continue

                    fv = vor.vertices[finite_v[0]]
                    # Direction: perpendicular to the ridge points midpoint
                    tangent = vor.points[p2] - vor.points[p1]
                    normal = np.array([-tangent[1], tangent[0]])
                    normal = normal / max(np.linalg.norm(normal), 1e-12)

                    # Ensure it points away from the center
                    midpoint = (vor.points[p1] + vor.points[p2]) / 2
                    if np.dot(normal, midpoint - center) < 0:
                        normal = -normal

                    # Project far enough to hit the boundary
                    far_point = fv + normal * max(e - w, n - s) * 2
                    # Clip to bounds
                    far_point[0] = max(w, min(e, far_point[0]))
                    far_point[1] = max(s, min(n, far_point[1]))
                    finite_verts.append(tuple(far_point))

        if len(finite_verts) >= 3:
            # Sort vertices clockwise around the centroid
            cx = sum(v[0] for v in finite_verts) / len(finite_verts)
            cy = sum(v[1] for v in finite_verts) / len(finite_verts)
            finite_verts.sort(key=lambda v: math.atan2(v[1] - cy, v[0] - cx))
            regions[point_idx] = finite_verts

    return regions


def _compute_regions_scipy(data):
    """Compute Voronoi regions using scipy (precise, handles all cases)."""
    points = np.array(data)
    vor = ScipyVoronoi(points)

    bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
    idx_regions = _clip_infinite_voronoi(vor, bounds)

    # Re-key by (lng, lat) tuple
    regions = {}
    for idx, verts in idx_regions.items():
        seed = tuple(data[idx])
        regions[seed] = verts

    return regions


def _trace_region(data, dlng, dlat):
    """Trace the Voronoi region boundary for a single seed point.

    Returns a list of (x, y) vertices forming the polygon, or None if
    tracing fails (degenerate configuration).

    This is adapted from find_area() but captures vertex coordinates
    instead of just computing the area.
    """
    try:
        elng, elat = vormap.get_NN(data, dlng, dlat)
        alng, alat = vormap.mid_point(dlng, dlat, elng, elat)
        dirn = vormap.perp_dir(elng, elat, dlng, dlat)
    except (ValueError, RuntimeError):
        return None

    e0g = elng
    e0t = elat

    ag = [alng]
    at = [alat]
    i = 0

    while True:
        try:
            a_g, a_t = vormap.find_a1(data, ag[i], at[i], dlng, dlat, dirn)
            if vormap.get_NN(data, a_g, a_t) != (dlng, dlat):
                return None  # vertex doesn't map back — skip this region
        except (ValueError, RuntimeError):
            return None

        ag.append(a_g)
        at.append(a_t)

        try:
            dirn1 = vormap.new_dir(
                data, ag[i], at[i], ag[i + 1], at[i + 1], dlng, dlat
            )
        except (ValueError, RuntimeError, ZeroDivisionError):
            return None

        if i > 2:
            if dirn == dirn1:
                break

            fin_isect = vormap.isect(
                ag[i + 1], at[i + 1], ag[i], at[i], e0g, e0t, dlng, dlat
            )
            if fin_isect != (-1, -1):
                break

            if i >= vormap.MAX_VERTICES:
                break

        dirn = dirn1
        i += 1

    return list(zip(ag, at))


def _compute_regions_fallback(data):
    """Compute Voronoi regions using the vormap binary-search tracer."""
    regions = {}
    for dlng, dlat in data:
        verts = _trace_region(data, dlng, dlat)
        if verts and len(verts) >= 3:
            regions[(dlng, dlat)] = verts
    return regions


def compute_regions(data):
    """Compute Voronoi region polygons for all seed points.

    When scipy is available, uses ``scipy.spatial.Voronoi`` for precise
    region computation with boundary clipping.  Falls back to the vormap
    binary-search tracer otherwise.

    Parameters
    ----------
    data : list of (float, float)
        Seed points returned by ``vormap.load_data()``.

    Returns
    -------
    dict
        Maps ``(lng, lat)`` seed → list of ``(x, y)`` polygon vertices.
        Points whose regions couldn't be traced are omitted.
    """
    if _HAS_SCIPY and len(data) >= 3:
        return _compute_regions_scipy(data)
    return _compute_regions_fallback(data)


# ── SVG export ───────────────────────────────────────────────────────

def export_svg(
    regions,
    data,
    output_path,
    *,
    width=800,
    height=600,
    margin=40,
    color_scheme=DEFAULT_COLOR_SCHEME,
    show_points=True,
    show_labels=False,
    stroke_color="#444444",
    stroke_width=1.2,
    point_radius=3.5,
    point_color="#222222",
    background="#ffffff",
    title=None,
):
    """Export Voronoi regions as an SVG file.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points (used for point markers).
    output_path : str
        Path to write the SVG file.
    width, height : int
        SVG canvas dimensions in pixels.
    margin : int
        Padding around the diagram.
    color_scheme : str
        One of: pastel, warm, cool, earth, mono, rainbow.
    show_points : bool
        Whether to draw seed point markers.
    show_labels : bool
        Whether to label each region with its seed index.
    stroke_color : str
        CSS color for polygon borders.
    stroke_width : float
        Border thickness.
    point_radius : float
        Radius of seed point markers.
    point_color : str
        CSS color for seed point markers.
    background : str
        CSS color for the SVG background.
    title : str or None
        Optional title text displayed at the top of the diagram.
    """
    if color_scheme not in _COLOR_SCHEMES:
        raise ValueError(
            "Unknown color scheme '%s'. Available: %s"
            % (color_scheme, ", ".join(sorted(_COLOR_SCHEMES)))
        )

    color_fn = _COLOR_SCHEMES[color_scheme]

    # Compute coordinate transform: data space → SVG pixel space
    all_xs = [x for pt in data for x in [pt[0]]]
    all_ys = [y for pt in data for y in [pt[1]]]

    # Include region vertices in bounds calculation
    for verts in regions.values():
        for vx, vy in verts:
            all_xs.append(vx)
            all_ys.append(vy)

    if not all_xs or not all_ys:
        raise ValueError("No data to visualize")

    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)

    # Avoid division by zero for degenerate datasets
    range_x = max(max_x - min_x, 1e-6)
    range_y = max(max_y - min_y, 1e-6)

    draw_w = width - 2 * margin
    draw_h = height - 2 * margin

    # Uniform scaling to preserve aspect ratio
    scale = min(draw_w / range_x, draw_h / range_y)

    # Center the diagram
    offset_x = margin + (draw_w - range_x * scale) / 2
    offset_y = margin + (draw_h - range_y * scale) / 2

    def tx(x):
        return offset_x + (x - min_x) * scale

    def ty(y):
        # Flip Y axis so north is up
        return offset_y + (max_y - y) * scale

    # Build SVG
    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    })

    # Add metadata comment
    comment_text = (
        "Generated by VoronoiMap — %d regions from %d seed points"
        % (len(regions), len(data))
    )

    # Style definitions
    style = ET.SubElement(svg, "style")
    style.text = (
        ".region { stroke: %s; stroke-width: %.1f; stroke-linejoin: round; }"
        " .seed { fill: %s; stroke: #fff; stroke-width: 1; }"
        " .label { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 10px; fill: #333; text-anchor: middle;"
        " dominant-baseline: central; pointer-events: none; }"
        " .title { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 16px; font-weight: 600; fill: #222;"
        " text-anchor: middle; }"
        % (stroke_color, stroke_width, point_color)
    )

    # Background
    ET.SubElement(svg, "rect", {
        "width": str(width),
        "height": str(height),
        "fill": background,
    })

    # Title
    if title:
        title_el = ET.SubElement(svg, "text", {
            "x": str(width / 2),
            "y": str(margin / 2 + 4),
            "class": "title",
        })
        title_el.text = title

    # Draw regions
    region_group = ET.SubElement(svg, "g", {"id": "regions"})

    # Sort seeds for deterministic coloring
    sorted_seeds = sorted(regions.keys())

    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        points_str = " ".join(
            "%.2f,%.2f" % (tx(vx), ty(vy)) for vx, vy in verts
        )
        fill = color_fn(idx, len(sorted_seeds))

        poly = ET.SubElement(region_group, "polygon", {
            "points": points_str,
            "fill": fill,
            "class": "region",
        })
        poly.set("data-seed", "%.4f,%.4f" % seed)

    # Draw seed points
    if show_points:
        points_group = ET.SubElement(svg, "g", {"id": "seeds"})
        for px, py in data:
            ET.SubElement(points_group, "circle", {
                "cx": "%.2f" % tx(px),
                "cy": "%.2f" % ty(py),
                "r": str(point_radius),
                "class": "seed",
            })

    # Draw labels
    if show_labels:
        label_group = ET.SubElement(svg, "g", {"id": "labels"})
        for idx, (px, py) in enumerate(data):
            label = ET.SubElement(label_group, "text", {
                "x": "%.2f" % tx(px),
                "y": "%.2f" % ty(py),
                "class": "label",
            })
            label.text = str(idx + 1)

    # Write to file
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)

    return output_path


# ── Convenience: one-call diagram generation ─────────────────────────

def generate_diagram(
    datafile,
    output_path="voronoi.svg",
    **kwargs,
):
    """Load data, compute regions, and export SVG in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str
        Where to write the SVG.
    **kwargs
        Passed through to ``export_svg()``.

    Returns
    -------
    str
        Path to the generated SVG file.
    """
    data = vormap.load_data(datafile)
    regions = compute_regions(data)
    return export_svg(regions, data, output_path, **kwargs)


def list_color_schemes():
    """Return a sorted list of available color scheme names."""
    return sorted(_COLOR_SCHEMES.keys())


# ── Interactive HTML export ──────────────────────────────────────────

def _compute_region_area(vertices):
    """Compute polygon area using the Shoelace formula."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def export_html(
    regions,
    data,
    output_path,
    *,
    width=960,
    height=700,
    color_scheme=DEFAULT_COLOR_SCHEME,
    title=None,
):
    """Export an interactive HTML visualization of Voronoi regions.

    The generated HTML file is self-contained (zero external dependencies)
    and supports:

    - **Pan & zoom** via mouse wheel / drag (Canvas 2D transforms)
    - **Hover tooltips** showing region index, seed coordinates, area,
      and vertex count
    - **Live color scheme switching** between all 6 built-in schemes
    - **Region highlighting** on hover
    - **Zoom controls** (+/−/reset buttons)
    - **Dark/light theme toggle**
    - **Responsive layout** that fills the browser window

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    output_path : str
        Path to write the HTML file.
    width, height : int
        Initial canvas dimensions (resizes to fit window).
    color_scheme : str
        Initial color scheme (pastel, warm, cool, earth, mono, rainbow).
    title : str or None
        Optional title displayed at the top.

    Returns
    -------
    str
        Path to the generated HTML file.
    """
    if not regions:
        raise ValueError("No regions to visualize")

    # Prepare region data as JSON-serializable structures
    sorted_seeds = sorted(regions.keys())
    region_list = []
    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        area = _compute_region_area(verts)
        # Find seed index in original data
        try:
            data_idx = data.index(seed)
        except ValueError:
            data_idx = idx

        region_list.append({
            "seed": list(seed),
            "vertices": [list(v) for v in verts],
            "area": round(area, 2),
            "vertexCount": len(verts),
            "dataIndex": data_idx,
        })

    import json
    regions_json = json.dumps(region_list)
    points_json = json.dumps([list(p) for p in data])
    title_text = title or ("Voronoi Diagram — %d regions" % len(regions))

    html = _INTERACTIVE_HTML_TEMPLATE.replace("{{REGIONS_JSON}}", regions_json)
    html = html.replace("{{POINTS_JSON}}", points_json)
    html = html.replace("{{TITLE}}", title_text)
    html = html.replace("{{INITIAL_SCHEME}}", color_scheme)
    html = html.replace("{{WIDTH}}", str(width))
    html = html.replace("{{HEIGHT}}", str(height))

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


_INTERACTIVE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;overflow:hidden;
  transition:background .3s,color .3s}
body.light{background:#f5f5f5;color:#222}
body.dark{background:#1a1a2e;color:#e0e0e0}
#header{display:flex;align-items:center;justify-content:space-between;
  padding:8px 16px;gap:12px;flex-wrap:wrap;z-index:10;position:relative}
body.light #header{background:#fff;border-bottom:1px solid #ddd}
body.dark #header{background:#16213e;border-bottom:1px solid #333}
h1{font-size:16px;font-weight:600;white-space:nowrap}
.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
select,button{font-size:13px;padding:4px 10px;border-radius:4px;cursor:pointer;
  border:1px solid #aaa;background:#fff;color:#222;transition:all .2s}
body.dark select,body.dark button{background:#0f3460;color:#e0e0e0;border-color:#555}
button:hover{opacity:0.85}
.zoom-btn{width:32px;font-size:16px;font-weight:bold;text-align:center;padding:4px}
#canvas-wrap{position:relative;flex:1;overflow:hidden}
canvas{display:block;cursor:grab}
canvas.grabbing{cursor:grabbing}
#tooltip{position:fixed;pointer-events:none;padding:8px 12px;border-radius:6px;
  font-size:12px;line-height:1.5;white-space:nowrap;opacity:0;
  transition:opacity .15s;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.25)}
body.light #tooltip{background:#222;color:#fff}
body.dark #tooltip{background:#e8e8e8;color:#111}
#stats{font-size:12px;opacity:0.7;white-space:nowrap}
</style>
</head>
<body class="light">
<div id="header">
  <h1>{{TITLE}}</h1>
  <div class="controls">
    <label>Color: <select id="scheme">
      <option value="pastel">Pastel</option>
      <option value="warm">Warm</option>
      <option value="cool">Cool</option>
      <option value="earth">Earth</option>
      <option value="mono">Mono</option>
      <option value="rainbow">Rainbow</option>
    </select></label>
    <button class="zoom-btn" id="zoom-in" title="Zoom in">+</button>
    <button class="zoom-btn" id="zoom-out" title="Zoom out">−</button>
    <button id="zoom-reset" title="Reset view">Reset</button>
    <button id="theme-btn" title="Toggle dark/light">🌙</button>
    <span id="stats"></span>
  </div>
</div>
<div id="canvas-wrap"><canvas id="c"></canvas></div>
<div id="tooltip"></div>

<script>
(function(){
"use strict";

var regions = {{REGIONS_JSON}};
var points  = {{POINTS_JSON}};
var initialScheme = "{{INITIAL_SCHEME}}";

// ── Color scheme generators ──
function hslToHex(h,s,l){
  h%=1; if(h<0)h+=1;
  var c=(1-Math.abs(2*l-1))*s, x=c*(1-Math.abs((h*6)%2-1)), m=l-c/2;
  var r,g,b;
  if(h<1/6){r=c;g=x;b=0}else if(h<2/6){r=x;g=c;b=0}
  else if(h<3/6){r=0;g=c;b=x}else if(h<4/6){r=0;g=x;b=c}
  else if(h<5/6){r=x;g=0;b=c}else{r=c;g=0;b=x}
  r=Math.round((r+m)*255);g=Math.round((g+m)*255);b=Math.round((b+m)*255);
  return "#"+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
}
function grayHex(l){var v=Math.max(0,Math.min(255,Math.round(l*255)));
  return "#"+((1<<24)+(v<<16)+(v<<8)+v).toString(16).slice(1)}

var schemes = {
  pastel:  function(i,n){return hslToHex(i/Math.max(n,1),.65,.82)},
  warm:    function(i,n){return hslToHex((i/Math.max(n,1))*.15,.70,.75)},
  cool:    function(i,n){return hslToHex(.5+(i/Math.max(n,1))*.2,.60,.78)},
  earth:   function(i,n){return hslToHex(.08+(i/Math.max(n,1))*.1,.45,.65)},
  mono:    function(i,n){return grayHex(.88-.35*(i/Math.max(n,1)))},
  rainbow: function(i,n){return hslToHex(i/Math.max(n,1),.70,.68)}
};

var curScheme = initialScheme;

// ── Data bounds ──
var allX=[], allY=[];
points.forEach(function(p){allX.push(p[0]);allY.push(p[1])});
regions.forEach(function(r){r.vertices.forEach(function(v){allX.push(v[0]);allY.push(v[1])})});
var minX=Math.min.apply(null,allX), maxX=Math.max.apply(null,allX);
var minY=Math.min.apply(null,allY), maxY=Math.max.apply(null,allY);
var rangeX=Math.max(maxX-minX,1e-6), rangeY=Math.max(maxY-minY,1e-6);

// ── Canvas setup ──
var canvas=document.getElementById("c"), ctx=canvas.getContext("2d");
var wrap=document.getElementById("canvas-wrap");
var tooltip=document.getElementById("tooltip");

function resize(){
  canvas.width=wrap.clientWidth;
  canvas.height=wrap.clientHeight;
  draw();
}
window.addEventListener("resize",resize);

// ── Transform state ──
var panX=0, panY=0, zoom=1;

function fitView(){
  var cw=canvas.width, ch=canvas.height, margin=50;
  var dw=cw-2*margin, dh=ch-2*margin;
  zoom=Math.min(dw/rangeX, dh/rangeY);
  panX=margin+(dw-rangeX*zoom)/2;
  panY=margin+(dh-rangeY*zoom)/2;
}

function tx(x){return panX+(x-minX)*zoom}
function ty(y){return panY+(maxY-y)*zoom}  // flip Y

// ── Hit testing ──
var hoverIdx=-1;

function pointInPoly(px,py,verts){
  var inside=false, n=verts.length;
  for(var i=0,j=n-1;i<n;j=i++){
    var xi=tx(verts[i][0]),yi=ty(verts[i][1]);
    var xj=tx(verts[j][0]),yj=ty(verts[j][1]);
    if(((yi>py)!==(yj>py))&&(px<(xj-xi)*(py-yi)/(yj-yi)+xi))
      inside=!inside;
  }
  return inside;
}

function findRegion(mx,my){
  for(var i=0;i<regions.length;i++){
    if(pointInPoly(mx,my,regions[i].vertices)) return i;
  }
  return -1;
}

// ── Drawing ──
function draw(){
  var cw=canvas.width, ch=canvas.height;
  var isDark=document.body.classList.contains("dark");
  ctx.clearRect(0,0,cw,ch);
  ctx.fillStyle=isDark?"#1a1a2e":"#f5f5f5";
  ctx.fillRect(0,0,cw,ch);

  var colorFn=schemes[curScheme]||schemes.pastel;
  var n=regions.length;

  // Draw regions
  for(var i=0;i<n;i++){
    var r=regions[i], verts=r.vertices;
    ctx.beginPath();
    ctx.moveTo(tx(verts[0][0]),ty(verts[0][1]));
    for(var j=1;j<verts.length;j++) ctx.lineTo(tx(verts[j][0]),ty(verts[j][1]));
    ctx.closePath();
    ctx.fillStyle=colorFn(i,n);
    if(i===hoverIdx){
      // Brighten on hover
      ctx.globalAlpha=0.85;
      ctx.fill();
      ctx.globalAlpha=1;
      ctx.strokeStyle=isDark?"#fff":"#000";
      ctx.lineWidth=2.5;
    } else {
      ctx.fill();
      ctx.strokeStyle=isDark?"#555":"#444";
      ctx.lineWidth=1;
    }
    ctx.stroke();
  }

  // Draw seed points
  ctx.fillStyle=isDark?"#e0e0e0":"#222";
  ctx.strokeStyle=isDark?"#1a1a2e":"#fff";
  ctx.lineWidth=1;
  for(var i=0;i<points.length;i++){
    ctx.beginPath();
    ctx.arc(tx(points[i][0]),ty(points[i][1]),3.5*Math.min(zoom/5+0.5,1.5),0,Math.PI*2);
    ctx.fill();
    ctx.stroke();
  }
}

// ── Mouse interaction: pan & zoom ──
var dragging=false, dragStartX=0, dragStartY=0, panStartX=0, panStartY=0;

canvas.addEventListener("mousedown",function(e){
  if(e.button===0){
    dragging=true;
    dragStartX=e.clientX; dragStartY=e.clientY;
    panStartX=panX; panStartY=panY;
    canvas.classList.add("grabbing");
  }
});
window.addEventListener("mousemove",function(e){
  if(dragging){
    panX=panStartX+(e.clientX-dragStartX);
    panY=panStartY+(e.clientY-dragStartY);
    draw();
  }
});
window.addEventListener("mouseup",function(){
  dragging=false;
  canvas.classList.remove("grabbing");
});

canvas.addEventListener("wheel",function(e){
  e.preventDefault();
  var rect=canvas.getBoundingClientRect();
  var mx=e.clientX-rect.left, my=e.clientY-rect.top;
  var factor=e.deltaY<0?1.15:1/1.15;
  // Zoom centered on cursor
  panX=mx-factor*(mx-panX);
  panY=my-factor*(my-panY);
  zoom*=factor;
  draw();
},{passive:false});

// ── Hover / tooltip ──
canvas.addEventListener("mousemove",function(e){
  if(dragging) return;
  var rect=canvas.getBoundingClientRect();
  var mx=e.clientX-rect.left, my=e.clientY-rect.top;
  var idx=findRegion(mx,my);
  if(idx!==hoverIdx){
    hoverIdx=idx;
    draw();
  }
  if(idx>=0){
    var r=regions[idx];
    tooltip.innerHTML="<b>Region #"+(r.dataIndex+1)+"</b><br>"+
      "Seed: ("+r.seed[0].toFixed(2)+", "+r.seed[1].toFixed(2)+")<br>"+
      "Area: "+r.area.toFixed(2)+" sq units<br>"+
      "Vertices: "+r.vertexCount;
    tooltip.style.opacity="1";
    tooltip.style.left=(e.clientX+14)+"px";
    tooltip.style.top=(e.clientY+14)+"px";
  } else {
    tooltip.style.opacity="0";
  }
});
canvas.addEventListener("mouseleave",function(){
  hoverIdx=-1;
  tooltip.style.opacity="0";
  draw();
});

// ── Controls ──
var schemeSelect=document.getElementById("scheme");
schemeSelect.value=initialScheme;
schemeSelect.addEventListener("change",function(){
  curScheme=this.value;
  draw();
});

document.getElementById("zoom-in").addEventListener("click",function(){
  var cx=canvas.width/2, cy=canvas.height/2;
  panX=cx-1.3*(cx-panX); panY=cy-1.3*(cy-panY);
  zoom*=1.3; draw();
});
document.getElementById("zoom-out").addEventListener("click",function(){
  var cx=canvas.width/2, cy=canvas.height/2;
  panX=cx-(cx-panX)/1.3; panY=cy-(cy-panY)/1.3;
  zoom/=1.3; draw();
});
document.getElementById("zoom-reset").addEventListener("click",function(){
  fitView(); draw();
});

// Theme toggle
var themeBtn=document.getElementById("theme-btn");
themeBtn.addEventListener("click",function(){
  var isDark=document.body.classList.toggle("dark");
  document.body.classList.toggle("light",!isDark);
  themeBtn.textContent=isDark?"☀️":"🌙";
  draw();
});

// Stats
document.getElementById("stats").textContent=
  regions.length+" regions · "+points.length+" points";

// Init
resize();
fitView();
draw();
})();
</script>
</body>
</html>"""
