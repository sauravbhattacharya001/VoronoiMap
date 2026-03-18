"""Graph coloring for Voronoi diagrams — VoronoiMap.

Assigns colors to Voronoi cells so that no two adjacent cells share the
same color, following the four-color theorem.  Produces clean, readable
map visualizations suitable for cartography, thematic maps, and posters.

Algorithms
----------
- ``greedy`` — fast greedy coloring with optional ordering strategies.
- ``dsatur`` — degree-of-saturation heuristic (good results, still fast).
- ``welsh_powell`` — order by descending degree, then greedy.

Output formats
--------------
- Color index map (dict: seed → int).
- SVG export with configurable palettes.
- HTML interactive viewer with hover/click.

Palettes
--------
- ``classic`` — 4 high-contrast map colors (red/blue/green/yellow).
- ``pastel`` — soft pastels for print-friendly maps.
- ``earth`` — natural/geographic tones.
- ``bold`` — saturated modern palette.
- ``mono`` — grayscale shades.
- ``custom`` — user-supplied list of hex colors.

Example::

    import vormap, vormap_viz
    from vormap_coloring import color_voronoi, export_colored_svg

    points = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(points)
    coloring = color_voronoi(regions, points, algorithm="dsatur")
    export_colored_svg(regions, points, coloring, "colored_map.svg")

CLI::

    python vormap_coloring.py datauni5.txt -o colored.svg
    python vormap_coloring.py datauni5.txt --palette earth --algorithm welsh_powell
    python vormap_coloring.py datauni5.txt --html colored.html
    python vormap_coloring.py datauni5.txt --stats
"""

import argparse
import html as _html_mod
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

import vormap

# ── Palettes ─────────────────────────────────────────────────────────

PALETTES = {
    "classic": ["#e6194b", "#3cb44b", "#4363d8", "#ffe119"],
    "pastel": ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6", "#ffffcc"],
    "earth": ["#8c510a", "#d8b365", "#5ab4ac", "#01665e", "#c7eae5", "#f6e8c3"],
    "bold": ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628"],
    "mono": ["#f7f7f7", "#cccccc", "#969696", "#636363", "#252525"],
    "tableau": [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
        "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
        "#9c755f", "#bab0ac",
    ],
}

DEFAULT_PALETTE = "classic"


# ── Graph coloring algorithms ────────────────────────────────────────

def _build_adjacency(regions, data):
    """Build adjacency dict {index: set of neighbor indices} from regions."""
    import vormap_graph
    graph = vormap_graph.extract_neighborhood_graph(regions, data)
    adj = {}
    for i in range(len(data)):
        adj[i] = set()
    for edge in graph["edges"]:
        a, b = edge
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    return adj


def _greedy_coloring(adj, order=None):
    """Greedy graph coloring given an adjacency dict.

    Parameters
    ----------
    adj : dict
        {node: set of neighbors}.
    order : list or None
        Node processing order.  If None, uses sorted node list.

    Returns
    -------
    dict
        {node: color_index} with 0-based color indices.
    """
    if order is None:
        order = sorted(adj.keys())
    coloring = {}
    for node in order:
        used = {coloring[nb] for nb in adj.get(node, set()) if nb in coloring}
        c = 0
        while c in used:
            c += 1
        coloring[node] = c
    return coloring


def color_greedy(adj):
    """Greedy coloring with default (index) ordering."""
    return _greedy_coloring(adj)


def color_welsh_powell(adj):
    """Welsh-Powell coloring — order nodes by descending degree."""
    order = sorted(adj.keys(), key=lambda n: len(adj.get(n, set())), reverse=True)
    return _greedy_coloring(adj, order)


def color_dsatur(adj):
    """DSatur (degree of saturation) graph coloring.

    At each step, picks the uncolored node with the most distinct colors
    among its neighbors (breaking ties by highest degree).  Typically
    produces near-optimal colorings.

    Returns
    -------
    dict
        {node: color_index}.
    """
    nodes = set(adj.keys())
    coloring = {}
    saturation = {n: set() for n in nodes}  # colors used by neighbors

    while len(coloring) < len(nodes):
        # Pick uncolored node with highest saturation, break ties by degree
        uncolored = nodes - set(coloring.keys())
        best = max(uncolored, key=lambda n: (len(saturation[n]), len(adj.get(n, set()))))

        used = {coloring[nb] for nb in adj.get(best, set()) if nb in coloring}
        c = 0
        while c in used:
            c += 1
        coloring[best] = c

        # Update saturation counts for neighbors
        for nb in adj.get(best, set()):
            if nb not in coloring:
                saturation[nb].add(c)

    return coloring


ALGORITHMS = {
    "greedy": color_greedy,
    "welsh_powell": color_welsh_powell,
    "dsatur": color_dsatur,
}


# ── High-level API ───────────────────────────────────────────────────

def color_voronoi(regions, data, *, algorithm="dsatur"):
    """Color Voronoi cells so no two adjacent cells share a color.

    Parameters
    ----------
    regions : dict
        Voronoi regions from ``compute_regions()``.
    data : list of (float, float)
        Seed points.
    algorithm : str
        One of ``"greedy"``, ``"welsh_powell"``, ``"dsatur"``.

    Returns
    -------
    dict
        {cell_index: color_index} — 0-based color indices.
    """
    if algorithm not in ALGORITHMS:
        raise ValueError("Unknown algorithm %r — choose from %s"
                         % (algorithm, ", ".join(sorted(ALGORITHMS))))
    adj = _build_adjacency(regions, data)
    return ALGORITHMS[algorithm](adj)


def coloring_stats(coloring, adj):
    """Compute statistics about a coloring.

    Returns
    -------
    dict
        Keys: ``num_colors``, ``num_cells``, ``color_counts``,
        ``is_valid`` (no two neighbors share a color),
        ``balance`` (std dev of color counts).
    """
    num_colors = max(coloring.values()) + 1 if coloring else 0
    counts = {}
    for c in coloring.values():
        counts[c] = counts.get(c, 0) + 1

    # Validate
    valid = True
    conflicts = 0
    for node, color in coloring.items():
        for nb in adj.get(node, set()):
            if nb in coloring and coloring[nb] == color:
                valid = False
                conflicts += 1
    conflicts //= 2  # each conflict counted twice

    mean = len(coloring) / num_colors if num_colors else 0
    variance = sum((cnt - mean) ** 2 for cnt in counts.values()) / num_colors if num_colors else 0

    return {
        "num_colors": num_colors,
        "num_cells": len(coloring),
        "color_counts": counts,
        "is_valid": valid,
        "conflicts": conflicts,
        "balance_stddev": round(math.sqrt(variance), 2),
    }


# ── SVG export ───────────────────────────────────────────────────────

def _resolve_palette(palette_name, num_colors, custom_colors=None):
    """Get a list of hex color strings for the given palette."""
    if custom_colors:
        colors = list(custom_colors)
    elif palette_name in PALETTES:
        colors = list(PALETTES[palette_name])
    else:
        colors = list(PALETTES[DEFAULT_PALETTE])

    # Extend if more colors needed than palette provides
    while len(colors) < num_colors:
        # Generate additional distinguishable colors via golden-angle hue rotation
        hue = (len(colors) * 137.508) % 360
        r, g, b = _hsl_to_rgb(hue / 360.0, 0.6, 0.65)
        colors.append("#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255)))
    return colors


def _hsl_to_rgb(h, s, l):
    """Convert HSL [0-1] to RGB [0-1]."""
    if s == 0:
        return l, l, l

    def hue2rgb(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p

    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    return hue2rgb(p, q, h + 1/3), hue2rgb(p, q, h), hue2rgb(p, q, h - 1/3)


def export_colored_svg(regions, data, coloring, output_path, *,
                       palette="classic", custom_colors=None,
                       width=800, height=600,
                       show_seeds=True, show_edges=True,
                       title=None, background="#ffffff"):
    """Export a colored Voronoi diagram as SVG.

    Parameters
    ----------
    regions : dict
        Voronoi regions.
    data : list of (float, float)
        Seed points.
    coloring : dict
        {cell_index: color_index} from ``color_voronoi``.
    output_path : str
        Destination SVG file path.
    palette : str
        Named palette or ``"custom"`` with *custom_colors*.
    width, height : int
        SVG dimensions.
    show_seeds : bool
        Draw seed points.
    show_edges : bool
        Draw cell edges.
    title : str or None
        SVG title text.
    background : str
        Background color.
    """
    vormap.validate_output_path(output_path)

    num_colors = (max(coloring.values()) + 1) if coloring else 1
    colors = _resolve_palette(palette, num_colors, custom_colors)

    # Compute bounding box from data
    if data:
        xs = [p[0] for p in data]
        ys = [p[1] for p in data]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
    else:
        min_x, max_x, min_y, max_y = 0, 1000, 0, 2000

    pad = max(max_x - min_x, max_y - min_y) * 0.05
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad

    data_w = max_x - min_x or 1
    data_h = max_y - min_y or 1

    def tx(x):
        return (x - min_x) / data_w * width

    def ty(y):
        return (1 - (y - min_y) / data_h) * height  # flip y

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height),
                     viewBox="0 0 %d %d" % (width, height))

    # Background
    ET.SubElement(svg, "rect", x="0", y="0",
                  width=str(width), height=str(height), fill=background)

    # Title
    if title:
        t = ET.SubElement(svg, "text", x=str(width // 2), y="20",
                          fill="#333333")
        t.set("text-anchor", "middle")
        t.set("font-family", "sans-serif")
        t.set("font-size", "14")
        t.text = title

    # Draw colored regions
    seeds = list(regions.keys())
    for idx, seed in enumerate(seeds):
        verts = regions[seed]
        if not verts or len(verts) < 3:
            continue
        color_idx = coloring.get(idx, 0)
        fill = colors[color_idx % len(colors)]

        points_str = " ".join("%.1f,%.1f" % (tx(v[0]), ty(v[1])) for v in verts)
        attrs = {"points": points_str, "fill": fill, "fill-opacity": "0.7"}
        if show_edges:
            attrs["stroke"] = "#333333"
            attrs["stroke-width"] = "0.5"
        else:
            attrs["stroke"] = "none"
        ET.SubElement(svg, "polygon", **attrs)

    # Draw seed points
    if show_seeds:
        for pt in data:
            ET.SubElement(svg, "circle",
                          cx="%.1f" % tx(pt[0]),
                          cy="%.1f" % ty(pt[1]),
                          r="2", fill="#000000", opacity="0.5")

    # Legend
    legend_y = height - 10 - num_colors * 18
    if legend_y < 30:
        legend_y = 30
    for i in range(num_colors):
        ly = legend_y + i * 18
        ET.SubElement(svg, "rect", x="10", y=str(ly),
                      width="14", height="14", fill=colors[i],
                      stroke="#333", **{"stroke-width": "0.5"})
        label = ET.SubElement(svg, "text", x="30", y=str(ly + 12),
                              fill="#333333")
        label.set("font-family", "sans-serif")
        label.set("font-size", "11")
        count = sum(1 for c in coloring.values() if c == i)
        label.text = "Color %d (%d cells)" % (i + 1, count)

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, xml_declaration=True, encoding="unicode")


# ── HTML interactive export ──────────────────────────────────────────

def export_colored_html(regions, data, coloring, output_path, *,
                        palette="classic", custom_colors=None,
                        title="Voronoi Graph Coloring"):
    """Export an interactive HTML viewer with hover highlighting.

    Parameters
    ----------
    regions : dict
        Voronoi regions.
    data : list of (float, float)
        Seed points.
    coloring : dict
        {cell_index: color_index}.
    output_path : str
        Destination HTML file path.
    palette : str
        Named palette.
    title : str
        Page title.
    """
    vormap.validate_output_path(output_path)

    num_colors = (max(coloring.values()) + 1) if coloring else 1
    colors = _resolve_palette(palette, num_colors, custom_colors)

    seeds = list(regions.keys())
    cells_json = []
    for idx, seed in enumerate(seeds):
        verts = regions[seed]
        if not verts or len(verts) < 3:
            continue
        color_idx = coloring.get(idx, 0)
        cells_json.append({
            "id": idx,
            "seed": list(seed) if isinstance(seed, tuple) else seed,
            "color": colors[color_idx % len(colors)],
            "colorIdx": color_idx,
            "vertices": [[round(v[0], 2), round(v[1], 2)] for v in verts],
        })

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>%(title)s</title>
<style>
body { margin: 0; font-family: system-ui, sans-serif; background: #1a1a2e; color: #eee; }
h1 { text-align: center; padding: 12px 0 4px; font-size: 1.3em; }
#info { text-align: center; font-size: 0.85em; color: #aaa; margin-bottom: 8px; }
canvas { display: block; margin: 0 auto; border: 1px solid #333; cursor: crosshair; }
#tooltip { position: fixed; background: rgba(0,0,0,0.85); color: #fff; padding: 6px 10px;
           border-radius: 4px; font-size: 0.8em; pointer-events: none; display: none; }
</style>
</head>
<body>
<h1>%(title)s</h1>
<div id="info">%(num_cells)d cells · %(num_colors)d colors · hover to inspect</div>
<canvas id="c" width="900" height="650"></canvas>
<div id="tooltip"></div>
<script>
const cells = %(cells_json)s;
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const tip = document.getElementById('tooltip');

// Compute bounds
let minX=Infinity, maxX=-Infinity, minY=Infinity, maxY=-Infinity;
cells.forEach(c => c.vertices.forEach(v => {
    if(v[0]<minX) minX=v[0]; if(v[0]>maxX) maxX=v[0];
    if(v[1]<minY) minY=v[1]; if(v[1]>maxY) maxY=v[1];
}));
const pad = Math.max(maxX-minX, maxY-minY)*0.05;
minX-=pad; maxX+=pad; minY-=pad; maxY+=pad;
const dw=maxX-minX||1, dh=maxY-minY||1;
function tx(x){return (x-minX)/dw*canvas.width}
function ty(y){return (1-(y-minY)/dh)*canvas.height}

let highlighted = -1;

function draw() {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    ctx.fillStyle='#1a1a2e'; ctx.fillRect(0,0,canvas.width,canvas.height);
    cells.forEach(c => {
        ctx.beginPath();
        c.vertices.forEach((v,i) => i===0 ? ctx.moveTo(tx(v[0]),ty(v[1])) : ctx.lineTo(tx(v[0]),ty(v[1])));
        ctx.closePath();
        ctx.fillStyle = c.id===highlighted ? '#ffffff' : c.color;
        ctx.globalAlpha = c.id===highlighted ? 0.9 : 0.7;
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle='#222'; ctx.lineWidth=0.5; ctx.stroke();
    });
}

function pointInPoly(x,y,verts){
    let inside=false;
    for(let i=0,j=verts.length-1;i<verts.length;j=i++){
        const xi=tx(verts[i][0]),yi=ty(verts[i][1]),xj=tx(verts[j][0]),yj=ty(verts[j][1]);
        if((yi>y)!==(yj>y)&&x<(xj-xi)*(y-yi)/(yj-yi)+xi) inside=!inside;
    }
    return inside;
}

canvas.addEventListener('mousemove', e => {
    const rect=canvas.getBoundingClientRect();
    const mx=e.clientX-rect.left, my=e.clientY-rect.top;
    let found=-1;
    for(const c of cells){if(pointInPoly(mx,my,c.vertices)){found=c.id;break;}}
    if(found!==highlighted){highlighted=found;draw();}
    if(found>=0){
        const c=cells.find(x=>x.id===found);
        tip.style.display='block';
        tip.style.left=(e.clientX+12)+'px'; tip.style.top=(e.clientY+12)+'px';
        tip.innerHTML='Cell '+c.id+'<br>Color '+(c.colorIdx+1)+' ('+c.color+')';
    } else {tip.style.display='none';}
});

draw();
</script>
</body>
</html>""" % {
        "title": _html_mod.escape(title),
        "num_cells": len(cells_json),
        "num_colors": num_colors,
        "cells_json": json.dumps(cells_json),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv=None):
    """CLI entry point for Voronoi graph coloring."""
    parser = argparse.ArgumentParser(
        description="Graph-color Voronoi diagrams — no two adjacent cells share a color.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python vormap_coloring.py datauni5.txt -o colored.svg
  python vormap_coloring.py datauni5.txt --palette earth --algorithm welsh_powell
  python vormap_coloring.py datauni5.txt --html colored.html --palette tableau
  python vormap_coloring.py datauni5.txt --stats --json coloring.json
""",
    )
    parser.add_argument("datafile", help="Input point data file.")
    parser.add_argument("-o", "--output", help="Output SVG file path.")
    parser.add_argument("--html", help="Output interactive HTML file path.")
    parser.add_argument("--json", help="Export coloring as JSON {index: color}.")
    parser.add_argument("--algorithm", choices=sorted(ALGORITHMS),
                        default="dsatur", help="Coloring algorithm (default: dsatur).")
    parser.add_argument("--palette", choices=sorted(PALETTES),
                        default=DEFAULT_PALETTE, help="Color palette (default: classic).")
    parser.add_argument("--colors", nargs="+", metavar="HEX",
                        help="Custom hex colors (e.g. #ff0000 #00ff00 #0000ff).")
    parser.add_argument("--width", type=int, default=800, help="SVG width (default: 800).")
    parser.add_argument("--height", type=int, default=600, help="SVG height (default: 600).")
    parser.add_argument("--no-seeds", action="store_true", help="Hide seed points.")
    parser.add_argument("--no-edges", action="store_true", help="Hide cell edges.")
    parser.add_argument("--title", help="Title text for SVG/HTML.")
    parser.add_argument("--stats", action="store_true", help="Print coloring statistics.")

    args = parser.parse_args(argv)

    # Load data
    data = vormap.load_data(args.datafile)
    if len(data) < 3:
        print("Error: need at least 3 points, got %d." % len(data), file=sys.stderr)
        sys.exit(1)

    # Compute regions
    import vormap_viz
    regions = vormap_viz.compute_regions(data)

    # Color
    coloring = color_voronoi(regions, data, algorithm=args.algorithm)
    adj = _build_adjacency(regions, data)
    stats = coloring_stats(coloring, adj)

    print("Colored %d cells using %d colors (algorithm: %s)"
          % (stats["num_cells"], stats["num_colors"], args.algorithm))
    if not stats["is_valid"]:
        print("WARNING: %d coloring conflicts detected!" % stats["conflicts"])

    if args.stats:
        print()
        print("  Valid:     %s" % stats["is_valid"])
        print("  Colors:    %d" % stats["num_colors"])
        print("  Cells:     %d" % stats["num_cells"])
        print("  Balance:   σ = %.2f" % stats["balance_stddev"])
        print("  Distribution:")
        for c_idx in sorted(stats["color_counts"]):
            print("    Color %d: %d cells" % (c_idx + 1, stats["color_counts"][c_idx]))

    if args.output:
        export_colored_svg(
            regions, data, coloring, args.output,
            palette=args.palette if not args.colors else "custom",
            custom_colors=args.colors,
            width=args.width, height=args.height,
            show_seeds=not args.no_seeds,
            show_edges=not args.no_edges,
            title=args.title or ("Voronoi Graph Coloring — %s (%d cells, %d colors)"
                                 % (args.datafile, stats["num_cells"], stats["num_colors"])),
        )
        print("SVG saved to %s" % args.output)

    if args.html:
        export_colored_html(
            regions, data, coloring, args.html,
            palette=args.palette if not args.colors else "custom",
            custom_colors=args.colors,
            title=args.title or ("Voronoi Graph Coloring — %s" % args.datafile),
        )
        print("HTML saved to %s" % args.html)

    if args.json:
        vormap.validate_output_path(args.json)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"algorithm": args.algorithm, "num_colors": stats["num_colors"],
                       "coloring": {str(k): v for k, v in coloring.items()},
                       "stats": stats}, f, indent=2)
        print("JSON saved to %s" % args.json)


if __name__ == "__main__":
    main()
