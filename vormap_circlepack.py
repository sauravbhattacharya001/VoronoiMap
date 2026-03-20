"""Voronoi Circle Packing — largest inscribed circles per cell.

Computes the largest circle that fits inside each Voronoi cell and
renders the result as SVG or interactive HTML.  Useful for spatial
analysis, coverage visualization, and geometric aesthetics.

Features:
- Largest inscribed circle per cell via iterative pole-of-inaccessibility
- Packing efficiency stats (circle area / cell area)
- SVG export with regions, circles, and optional labels
- Interactive HTML export with hover tooltips
- Multiple fill modes: solid, gradient, efficiency-heatmap

Usage (module)::

    import vormap, vormap_viz
    from vormap_circlepack import circle_pack, export_svg, export_html

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    packing = circle_pack(regions)
    export_svg(packing, regions, data, "circles.svg")
    export_html(packing, regions, data, "circles.html")

CLI::

    voronoimap datauni5.txt 5 --circlepack circles.svg
    voronoimap datauni5.txt 5 --circlepack-html circles.html
    voronoimap datauni5.txt 5 --circlepack circles.svg --circlepack-fill heatmap
"""

import argparse
import html as _html_mod
import math
import xml.etree.ElementTree as ET

import vormap

try:
    import vormap_viz
except ImportError:
    vormap_viz = None


# ── Geometry helpers ─────────────────────────────────────────────────

def _polygon_area(vertices):
    """Shoelace formula for polygon area."""
    n = len(vertices)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x0, y0 = vertices[i]
        x1, y1 = vertices[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _polygon_centroid(vertices):
    """Centroid of a simple polygon."""
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n <= 2:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)
    a6 = 0.0
    cx = cy = 0.0
    for i in range(n):
        x0, y0 = vertices[i]
        x1, y1 = vertices[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a6 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a6) < 1e-12:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)
    a6 *= 3.0
    return (cx / a6, cy / a6)


def _point_to_segment_dist(px, py, ax, ay, bx, by):
    """Distance from point (px,py) to line segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _point_in_polygon(px, py, vertices):
    """Ray-casting point-in-polygon test."""
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _dist_to_polygon_boundary(px, py, vertices):
    """Minimum distance from a point to the polygon boundary."""
    n = len(vertices)
    min_d = float('inf')
    for i in range(n):
        ax, ay = vertices[i]
        bx, by = vertices[(i + 1) % n]
        d = _point_to_segment_dist(px, py, ax, ay, bx, by)
        if d < min_d:
            min_d = d
    return min_d


def _largest_inscribed_circle(vertices, iterations=16):
    """Find the largest inscribed circle via iterative grid refinement.

    Uses a pole-of-inaccessibility approach: subdivide the bounding box,
    keep the point with maximum distance to the polygon boundary,
    progressively refine the search grid.

    Returns (cx, cy, radius).
    """
    if len(vertices) < 3:
        if len(vertices) == 0:
            return (0.0, 0.0, 0.0)
        cx = sum(v[0] for v in vertices) / len(vertices)
        cy = sum(v[1] for v in vertices) / len(vertices)
        return (cx, cy, 0.0)

    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    best_x, best_y, best_d = _polygon_centroid(vertices)[0], _polygon_centroid(vertices)[1], 0.0
    if _point_in_polygon(best_x, best_y, vertices):
        best_d = _dist_to_polygon_boundary(best_x, best_y, vertices)

    step_x = (max_x - min_x)
    step_y = (max_y - min_y)

    for _it in range(iterations):
        grid = 10
        sx = step_x / grid
        sy = step_y / grid
        search_cx = best_x - step_x / 2
        search_cy = best_y - step_y / 2

        for gi in range(grid + 1):
            for gj in range(grid + 1):
                px = search_cx + gi * sx
                py = search_cy + gj * sy
                if not _point_in_polygon(px, py, vertices):
                    continue
                d = _dist_to_polygon_boundary(px, py, vertices)
                if d > best_d:
                    best_d = d
                    best_x = px
                    best_y = py

        step_x *= 0.5
        step_y *= 0.5

    return (best_x, best_y, best_d)


# ── Core packing computation ────────────────────────────────────────

def circle_pack(regions, *, iterations=16):
    """Compute largest inscribed circles for all Voronoi regions.

    Parameters
    ----------
    regions : dict
        Output of ``vormap_viz.compute_regions()`` — seed → vertex list.
    iterations : int
        Refinement iterations for circle search (higher = more precise).

    Returns
    -------
    dict
        Maps seed → dict with keys:
        - ``cx``, ``cy``: circle center
        - ``radius``: inscribed circle radius
        - ``cell_area``: polygon area
        - ``circle_area``: π r²
        - ``efficiency``: circle_area / cell_area (0-1)
    """
    result = {}
    for seed, verts in regions.items():
        if len(verts) < 3:
            continue
        cx, cy, r = _largest_inscribed_circle(verts, iterations=iterations)
        cell_area = _polygon_area(verts)
        circle_area = math.pi * r * r
        eff = circle_area / cell_area if cell_area > 0 else 0.0
        result[seed] = {
            "cx": cx, "cy": cy, "radius": r,
            "cell_area": cell_area, "circle_area": circle_area,
            "efficiency": min(1.0, eff),
        }
    return result


def packing_stats(packing):
    """Aggregate packing statistics.

    Returns dict with total_cells, mean/min/max efficiency,
    total_cell_area, total_circle_area, overall_efficiency.
    """
    if not packing:
        return {"total_cells": 0}
    effs = [v["efficiency"] for v in packing.values()]
    total_cell = sum(v["cell_area"] for v in packing.values())
    total_circle = sum(v["circle_area"] for v in packing.values())
    return {
        "total_cells": len(packing),
        "mean_efficiency": sum(effs) / len(effs),
        "min_efficiency": min(effs),
        "max_efficiency": max(effs),
        "total_cell_area": total_cell,
        "total_circle_area": total_circle,
        "overall_efficiency": total_circle / total_cell if total_cell > 0 else 0.0,
    }


# ── Color helpers ────────────────────────────────────────────────────

def _eff_color(eff):
    """Efficiency → color: red (low) → yellow (mid) → green (high)."""
    if eff < 0.5:
        r = 1.0
        g = eff * 2
    else:
        r = 1.0 - (eff - 0.5) * 2
        g = 1.0
    return "#%02x%02x%02x" % (int(r * 220), int(g * 220), 60)


def _pastel_color(i, n):
    """Simple pastel palette."""
    import colorsys
    h = (i / max(n, 1)) % 1.0
    r, g, b = colorsys.hls_to_rgb(h, 0.82, 0.65)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


# ── SVG export ───────────────────────────────────────────────────────

def export_svg(
    packing, regions, data, output_path, *,
    width=800, height=600, margin=40,
    fill_mode="heatmap",
    show_circles=True, show_regions=True,
    circle_opacity=0.6, circle_stroke="#333",
    region_stroke="#666", region_stroke_width=1.0,
    background="#ffffff", title=None,
):
    """Export circle packing as SVG.

    Parameters
    ----------
    packing : dict
        Output of ``circle_pack()``.
    regions : dict
        Voronoi regions from ``compute_regions()``.
    data : list
        Seed points.
    output_path : str
        SVG file path.
    fill_mode : str
        ``"heatmap"`` (efficiency colors), ``"pastel"``, or ``"solid"``
    """
    safe_path = vormap._validate_path(output_path, allow_absolute=True, label="Output file")

    all_x = []
    all_y = []
    for verts in regions.values():
        for x, y in verts:
            all_x.append(x)
            all_y.append(y)
    if not all_x:
        return

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    data_w = max_x - min_x or 1
    data_h = max_y - min_y or 1
    scale = min((width - 2 * margin) / data_w, (height - 2 * margin) / data_h)

    def tx(x): return margin + (x - min_x) * scale
    def ty(y): return margin + (max_y - y) * scale  # flip Y
    def ts(r): return r * scale

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                      width=str(width), height=str(height))
    ET.SubElement(svg, "rect", width=str(width), height=str(height), fill=background)

    if title:
        t = ET.SubElement(svg, "text", x=str(width // 2), y="24",
                          fill="#333", **{"text-anchor": "middle", "font-size": "16",
                                          "font-family": "sans-serif"})
        t.text = title

    seeds = list(packing.keys())
    n = len(seeds)

    # Draw regions
    if show_regions:
        for i, seed in enumerate(seeds):
            if seed not in regions:
                continue
            verts = regions[seed]
            pts = " ".join("%.1f,%.1f" % (tx(x), ty(y)) for x, y in verts)
            if fill_mode == "heatmap":
                fill = _eff_color(packing[seed]["efficiency"])
            elif fill_mode == "pastel":
                fill = _pastel_color(i, n)
            else:
                fill = "#e8e8e8"
            ET.SubElement(svg, "polygon", points=pts, fill=fill,
                          stroke=region_stroke, **{"stroke-width": str(region_stroke_width),
                                                    "fill-opacity": "0.4"})

    # Draw circles
    if show_circles:
        for seed in seeds:
            p = packing[seed]
            ET.SubElement(svg, "circle",
                          cx="%.1f" % tx(p["cx"]),
                          cy="%.1f" % ty(p["cy"]),
                          r="%.1f" % ts(p["radius"]),
                          fill=_eff_color(p["efficiency"]),
                          stroke=circle_stroke,
                          **{"stroke-width": "1.2",
                             "fill-opacity": str(circle_opacity)})

    # Legend
    ly = height - 20
    for eff_val, label in [(0.0, "0%"), (0.5, "50%"), (1.0, "100%")]:
        lx = width - 140 + eff_val * 100
        ET.SubElement(svg, "rect", x="%.0f" % lx, y=str(ly),
                      width="10", height="10", fill=_eff_color(eff_val))
        t = ET.SubElement(svg, "text", x="%.0f" % (lx + 12), y=str(ly + 9),
                          fill="#555", **{"font-size": "9", "font-family": "sans-serif"})
        t.text = label

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(safe_path, xml_declaration=True, encoding="unicode")


# ── HTML export ──────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ margin:0; background:#1a1a2e; color:#eee; font-family:system-ui,sans-serif; }}
  .top {{ display:flex; gap:24px; padding:12px 20px; background:#16213e; align-items:center; flex-wrap:wrap; }}
  .top h1 {{ margin:0; font-size:1.3em; }}
  .stat {{ background:#0f3460; padding:6px 14px; border-radius:6px; font-size:0.9em; }}
  .stat b {{ color:#e94560; }}
  canvas {{ display:block; margin:20px auto; border-radius:8px; background:#fff; cursor:crosshair; }}
  #tooltip {{ position:fixed; background:#16213e; color:#eee; padding:8px 12px; border-radius:6px;
              font-size:0.85em; pointer-events:none; display:none; border:1px solid #e94560; z-index:10; }}
</style></head><body>
<div class="top">
  <h1>&#x25CE; Voronoi Circle Packing</h1>
  <div class="stat">Cells: <b>{n_cells}</b></div>
  <div class="stat">Mean Efficiency: <b>{mean_eff:.1%}</b></div>
  <div class="stat">Min: <b>{min_eff:.1%}</b></div>
  <div class="stat">Max: <b>{max_eff:.1%}</b></div>
  <div class="stat">Overall: <b>{overall_eff:.1%}</b></div>
</div>
<canvas id="c" width="{cw}" height="{ch}"></canvas>
<div id="tooltip"></div>
<script>
const data = {json_data};
const W={cw}, H={ch}, M=40;
const xs=data.map(d=>d.verts.map(v=>v[0])).flat();
const ys=data.map(d=>d.verts.map(v=>v[1])).flat();
const mnx=Math.min(...xs), mxx=Math.max(...xs), mny=Math.min(...ys), mxy=Math.max(...ys);
const dw=mxx-mnx||1, dh=mxy-mny||1;
const sc=Math.min((W-2*M)/dw,(H-2*M)/dh);
function tx(x){{return M+(x-mnx)*sc;}}
function ty(y){{return M+(mxy-y)*sc;}}
function effColor(e){{
  let r,g; if(e<.5){{r=1;g=e*2;}}else{{r=1-(e-.5)*2;g=1;}}
  return `rgb(${{Math.round(r*220)}},${{Math.round(g*220)}},60)`;
}}
const ctx=document.getElementById('c').getContext('2d');
function draw(){{
  ctx.clearRect(0,0,W,H);
  // regions
  data.forEach(d=>{{
    ctx.beginPath();
    d.verts.forEach((v,i)=>{{i?ctx.lineTo(tx(v[0]),ty(v[1])):ctx.moveTo(tx(v[0]),ty(v[1]));}}); ctx.closePath();
    ctx.fillStyle=effColor(d.eff); ctx.globalAlpha=0.3; ctx.fill();
    ctx.globalAlpha=1; ctx.strokeStyle='#888'; ctx.lineWidth=0.8; ctx.stroke();
  }});
  // circles
  data.forEach(d=>{{
    ctx.beginPath(); ctx.arc(tx(d.cx),ty(d.cy),d.r*sc,0,Math.PI*2);
    ctx.fillStyle=effColor(d.eff); ctx.globalAlpha=0.5; ctx.fill();
    ctx.globalAlpha=1; ctx.strokeStyle='#333'; ctx.lineWidth=1.2; ctx.stroke();
  }});
}}
draw();
const tip=document.getElementById('tooltip');
document.getElementById('c').addEventListener('mousemove',e=>{{
  const rect=e.target.getBoundingClientRect();
  const mx=e.clientX-rect.left, my=e.clientY-rect.top;
  let hit=null;
  for(const d of data){{
    const dx=mx-tx(d.cx), dy=my-ty(d.cy);
    if(dx*dx+dy*dy<=(d.r*sc)*(d.r*sc)){{hit=d;break;}}
  }}
  if(hit){{
    tip.style.display='block'; tip.style.left=(e.clientX+12)+'px'; tip.style.top=(e.clientY+12)+'px';
    tip.innerHTML=`Seed: (${{hit.seed[0].toFixed(1)}}, ${{hit.seed[1].toFixed(1)}})<br>Radius: ${{hit.r.toFixed(2)}}<br>Efficiency: ${{(hit.eff*100).toFixed(1)}}%<br>Cell Area: ${{hit.cellArea.toFixed(1)}}`;
  }}else{{tip.style.display='none';}}
}});
document.getElementById('c').addEventListener('mouseleave',()=>{{tip.style.display='none';}});
</script></body></html>
"""


def export_html(
    packing, regions, data, output_path, *,
    width=900, height=700, title="Voronoi Circle Packing",
):
    """Export interactive HTML circle packing visualization."""
    import json as _json
    safe_path = vormap._validate_path(output_path, allow_absolute=True, label="Output file")

    stats = packing_stats(packing)
    json_data = []
    for seed, info in packing.items():
        if seed not in regions:
            continue
        json_data.append({
            "seed": list(seed),
            "cx": info["cx"], "cy": info["cy"], "r": info["radius"],
            "eff": info["efficiency"],
            "cellArea": info["cell_area"],
            "verts": [list(v) for v in regions[seed]],
        })

    html = _HTML_TEMPLATE.format(
        title=_html_mod.escape(title),
        n_cells=stats.get("total_cells", 0),
        mean_eff=stats.get("mean_efficiency", 0),
        min_eff=stats.get("min_efficiency", 0),
        max_eff=stats.get("max_efficiency", 0),
        overall_eff=stats.get("overall_efficiency", 0),
        cw=width, ch=height,
        json_data=_json.dumps(json_data),
    )
    with open(safe_path, "w", encoding="utf-8") as f:
        f.write(html)


# ── CLI integration ──────────────────────────────────────────────────

def main(argv=None):
    """Standalone CLI for circle packing."""
    parser = argparse.ArgumentParser(
        description="Voronoi Circle Packing — inscribed circles per cell"
    )
    parser.add_argument("datafile", help="Input data file")
    parser.add_argument("k", nargs="?", type=int, default=5,
                        help="Number of seed columns (default: 5)")
    parser.add_argument("--svg", metavar="PATH", help="SVG output path")
    parser.add_argument("--html", metavar="PATH", help="HTML output path")
    parser.add_argument("--fill", choices=["heatmap", "pastel", "solid"],
                        default="heatmap", help="Fill mode (default: heatmap)")
    parser.add_argument("--stats", action="store_true", help="Print packing stats")
    parser.add_argument("--iterations", type=int, default=16,
                        help="Circle search refinement iterations")
    args = parser.parse_args(argv)

    data = vormap.load_data(args.datafile)
    if vormap_viz is None:
        print("Error: vormap_viz required for region computation")
        return 1
    regions = vormap_viz.compute_regions(data)
    packing = circle_pack(regions, iterations=args.iterations)

    if args.stats or (not args.svg and not args.html):
        stats = packing_stats(packing)
        print("Circle Packing Statistics")
        print("=" * 35)
        print(f"  Cells:            {stats['total_cells']}")
        print(f"  Mean Efficiency:  {stats.get('mean_efficiency', 0):.1%}")
        print(f"  Min Efficiency:   {stats.get('min_efficiency', 0):.1%}")
        print(f"  Max Efficiency:   {stats.get('max_efficiency', 0):.1%}")
        print(f"  Overall:          {stats.get('overall_efficiency', 0):.1%}")
        print(f"  Total Cell Area:  {stats.get('total_cell_area', 0):.1f}")
        print(f"  Total Circle Area:{stats.get('total_circle_area', 0):.1f}")

    if args.svg:
        export_svg(packing, regions, data, args.svg, fill_mode=args.fill)
        print(f"SVG written to {args.svg}")
    if args.html:
        export_html(packing, regions, data, args.html)
        print(f"HTML written to {args.html}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
