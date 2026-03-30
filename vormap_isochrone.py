"""Isochrone (travel-time zone) generator for Voronoi networks.

Computes reachability zones from one or more source cells across the
Voronoi adjacency graph, producing concentric bands that show how far
a quantity can travel within discrete time budgets.  Useful for:

- **Service-area analysis** — e.g. how far can an ambulance reach in
  5, 10, 15 minutes from a station?
- **Accessibility mapping** — walkability / transit reach from a point.
- **Market catchment** — delivery zones from a warehouse.
- **Flood / fire spread estimation** — wavefront over terrain.

Edge weights can be uniform (hop-count), Euclidean distance between
seeds, or user-supplied (e.g. inverse speed, terrain friction).

Outputs:

- Per-cell travel cost dictionary.
- Band assignments (which time-bracket each cell falls into).
- JSON, CSV, and coloured SVG exports.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_isochrone import (
        compute_isochrone, assign_bands, export_isochrone_json,
        export_isochrone_csv, export_isochrone_svg,
    )

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    graph_info = vormap_viz.extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]
    seeds = {i: (data["x"][i], data["y"][i]) for i in range(len(data["x"]))}

    costs = compute_isochrone(adjacency, seeds, sources=[0])
    bands = assign_bands(costs, breaks=[5, 10, 20, 50])
    export_isochrone_svg(bands, regions, data, "isochrone.svg")

CLI::

    python vormap_isochrone.py data/points.txt --source 0 --breaks 5,10,20,50
    python vormap_isochrone.py data/points.txt --source 0 --weight euclidean --svg iso.svg
    python vormap_isochrone.py data/points.txt --source 0,3,7 --breaks 10,25 --json iso.json
"""

import argparse
import csv as _csv
import heapq
import json
import math
import sys
import xml.etree.ElementTree as ET
from vormap_utils import euclidean_xy as _euclidean

import vormap

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BAND_COLORS = [
    "#2166ac",  # closest — dark blue
    "#67a9cf",
    "#d1e5f0",
    "#fddbc7",
    "#ef8a62",
    "#b2182b",  # farthest — dark red
    "#67001f",
    "#fee08b",
    "#a6d96a",
    "#1a9850",
]



# ---------------------------------------------------------------------------
# Core algorithm — Dijkstra from multiple sources
# ---------------------------------------------------------------------------


def compute_isochrone(adjacency, seeds, sources, weight="euclidean",
                      custom_weights=None, max_cost=None):
    """Dijkstra shortest-path from *sources* over the adjacency graph.

    Parameters
    ----------
    adjacency : dict[int, list[int]]
        Cell id → list of neighbour cell ids.
    seeds : dict[int, tuple[float, float]]
        Cell id → (x, y) coordinates of the Voronoi seed.
    sources : list[int]
        One or more source cell ids (cost = 0).
    weight : str
        ``"hop"`` for unit weights, ``"euclidean"`` for seed distance,
        ``"custom"`` to use *custom_weights*.
    custom_weights : dict[tuple[int,int], float] | None
        Edge (a, b) → cost.  Used when *weight* = ``"custom"``.
    max_cost : float | None
        Stop expanding cells beyond this cost (prune far zones).

    Returns
    -------
    dict[int, float]
        Cell id → minimum travel cost from any source.
    """
    dist = {}
    heap = []
    for s in sources:
        dist[s] = 0.0
        heapq.heappush(heap, (0.0, s))

    while heap:
        cost, u = heapq.heappop(heap)
        if cost > dist.get(u, float("inf")):
            continue
        if max_cost is not None and cost > max_cost:
            continue
        for v in adjacency.get(u, []):
            if weight == "hop":
                w = 1.0
            elif weight == "euclidean":
                ux, uy = seeds[u]
                vx, vy = seeds[v]
                w = _euclidean(ux, uy, vx, vy)
            elif weight == "custom" and custom_weights is not None:
                w = custom_weights.get((u, v), custom_weights.get((v, u), 1.0))
            else:
                w = 1.0
            new_cost = cost + w
            if new_cost < dist.get(v, float("inf")):
                dist[v] = new_cost
                heapq.heappush(heap, (new_cost, v))

    return dist


# ---------------------------------------------------------------------------
# Band assignment
# ---------------------------------------------------------------------------


def assign_bands(costs, breaks):
    """Classify cells into bands based on cost breakpoints.

    Parameters
    ----------
    costs : dict[int, float]
        Cell id → travel cost.
    breaks : list[float]
        Sorted ascending breakpoints.  A cell with cost ≤ breaks[0] is
        band 0, cost ≤ breaks[1] is band 1, etc.  Cells beyond the last
        break are band ``len(breaks)``.

    Returns
    -------
    dict[int, int]
        Cell id → band index.
    """
    bands = {}
    for cell, cost in costs.items():
        b = len(breaks)  # default: beyond all breaks
        for i, br in enumerate(breaks):
            if cost <= br:
                b = i
                break
        bands[cell] = b
    return bands


# ---------------------------------------------------------------------------
# Export — JSON
# ---------------------------------------------------------------------------


def export_isochrone_json(costs, bands, path):
    """Write isochrone results to JSON."""
    data = {
        "cells": [
            {"id": cid, "cost": round(costs[cid], 6), "band": bands.get(cid, -1)}
            for cid in sorted(costs)
        ]
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# Export — CSV
# ---------------------------------------------------------------------------


def export_isochrone_csv(costs, bands, path):
    """Write isochrone results to CSV."""
    with open(path, "w", newline="") as f:
        writer = _csv.writer(f)
        writer.writerow(["cell_id", "cost", "band"])
        for cid in sorted(costs):
            writer.writerow([cid, round(costs[cid], 6), bands.get(cid, -1)])
    return path


# ---------------------------------------------------------------------------
# Export — SVG
# ---------------------------------------------------------------------------


def _polygon_path(vertices):
    if not vertices:
        return ""
    parts = [f"M{vertices[0][0]:.2f},{vertices[0][1]:.2f}"]
    for x, y in vertices[1:]:
        parts.append(f"L{x:.2f},{y:.2f}")
    parts.append("Z")
    return "".join(parts)


def export_isochrone_svg(bands, regions, data, path, width=800, height=800,
                         breaks=None, title="Isochrone Map"):
    """Render coloured Voronoi cells by band to SVG.

    Parameters
    ----------
    bands : dict[int, int]
        Cell id → band index (from :func:`assign_bands`).
    regions : list[list[tuple]]
        Voronoi region polygons (from ``vormap_viz.compute_regions``).
    data : dict
        Original point data (from ``vormap.load_data``).
    path : str
        Output SVG file path.
    width, height : int
        SVG canvas size.
    breaks : list[float] | None
        Break values for legend labels.
    title : str
        Title rendered at top of SVG.
    """
    xs = data["x"]
    ys = data["y"]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = 0.05 * max(max_x - min_x or 1, max_y - min_y or 1)
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad
    sx = width / (max_x - min_x) if max_x != min_x else 1
    sy = height / (max_y - min_y) if max_y != min_y else 1

    def tx(x):
        return (x - min_x) * sx

    def ty(y):
        return height - (y - min_y) * sy

    n_bands = max(bands.values(), default=0) + 1

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                      width=str(width + 160), height=str(height + 60))

    # title
    t = ET.SubElement(svg, "text", x=str(width // 2), y="24",
                       fill="#333")
    t.set("text-anchor", "middle")
    t.set("font-size", "18")
    t.set("font-family", "sans-serif")
    t.text = title

    g = ET.SubElement(svg, "g", transform="translate(0,40)")

    # regions
    for idx, region in enumerate(regions):
        band = bands.get(idx, n_bands)
        color = _BAND_COLORS[band % len(_BAND_COLORS)] if band < n_bands else "#e0e0e0"
        verts = [(tx(vx), ty(vy)) for vx, vy in region]
        d = _polygon_path(verts)
        if d:
            ET.SubElement(g, "path", d=d, fill=color, stroke="#333",
                          **{"stroke-width": "0.5", "opacity": "0.85"})

    # source markers
    for idx, band in bands.items():
        if band == 0:
            cx, cy = tx(xs[idx]), ty(ys[idx])
            ET.SubElement(g, "circle", cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                          r="5", fill="#000", stroke="#fff",
                          **{"stroke-width": "1.5"})

    # legend
    lx = width + 20
    ly = 10
    for i in range(n_bands):
        color = _BAND_COLORS[i % len(_BAND_COLORS)]
        ET.SubElement(g, "rect", x=str(lx), y=str(ly + i * 22),
                      width="14", height="14", fill=color,
                      stroke="#333", **{"stroke-width": "0.5"})
        label_el = ET.SubElement(g, "text", x=str(lx + 20),
                                  y=str(ly + i * 22 + 12))
        label_el.set("font-size", "12")
        label_el.set("font-family", "sans-serif")
        label_el.set("fill", "#333")
        if breaks and i < len(breaks):
            label_el.text = f"≤ {breaks[i]}"
        elif breaks and i == len(breaks):
            label_el.text = f"> {breaks[-1]}"
        else:
            label_el.text = f"Band {i}"

    # unreachable legend
    ET.SubElement(g, "rect", x=str(lx), y=str(ly + n_bands * 22),
                  width="14", height="14", fill="#e0e0e0",
                  stroke="#333", **{"stroke-width": "0.5"})
    unr = ET.SubElement(g, "text", x=str(lx + 20),
                         y=str(ly + n_bands * 22 + 12))
    unr.set("font-size", "12")
    unr.set("font-family", "sans-serif")
    unr.set("fill", "#999")
    unr.text = "Unreachable"

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(path, xml_declaration=True, encoding="unicode")
    return path


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------


def format_report(costs, bands, breaks, sources):
    """Return a human-readable isochrone summary."""
    lines = ["Isochrone Report", "=" * 40]
    lines.append(f"Sources: {sources}")
    lines.append(f"Breaks:  {breaks}")
    lines.append(f"Total cells reached: {len(costs)}")

    n_bands = max(bands.values(), default=-1) + 1
    for i in range(n_bands):
        count = sum(1 for b in bands.values() if b == i)
        if breaks and i < len(breaks):
            label = f"≤ {breaks[i]}"
        elif breaks and i == len(breaks):
            label = f"> {breaks[-1]}"
        else:
            label = f"Band {i}"
        lines.append(f"  {label}: {count} cells")

    if costs:
        lines.append(f"Max cost: {max(costs.values()):.4f}")
        lines.append(f"Mean cost: {sum(costs.values()) / len(costs):.4f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Compute isochrone (travel-time) zones on a Voronoi network."
    )
    parser.add_argument("input", help="Input point file (txt/csv/json)")
    parser.add_argument("--source", default="0",
                        help="Comma-separated source cell indices (default: 0)")
    parser.add_argument("--weight", choices=["hop", "euclidean"], default="euclidean",
                        help="Edge weight mode (default: euclidean)")
    parser.add_argument("--breaks", default="5,10,20,50",
                        help="Comma-separated cost breakpoints for bands")
    parser.add_argument("--max-cost", type=float, default=None,
                        help="Maximum cost to expand (prune far cells)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="Export results to JSON")
    parser.add_argument("--csv", dest="csv_path", default=None,
                        help="Export results to CSV")
    parser.add_argument("--svg", dest="svg_path", default=None,
                        help="Export coloured SVG map")
    parser.add_argument("--report", action="store_true",
                        help="Print text report to stdout")
    args = parser.parse_args()

    # Load data and build adjacency
    try:
        import vormap_viz
    except ImportError:
        print("Error: vormap_viz is required. Run from the VoronoiMap directory.",
              file=sys.stderr)
        sys.exit(1)

    data = vormap.load_data(args.input)
    regions = vormap_viz.compute_regions(data)
    graph_info = vormap_viz.extract_neighborhood_graph(regions, data)
    adjacency = graph_info["adjacency"]

    seeds = {i: (data["x"][i], data["y"][i]) for i in range(len(data["x"]))}
    sources = [int(s.strip()) for s in args.source.split(",")]
    breaks = [float(b.strip()) for b in args.breaks.split(",")]

    costs = compute_isochrone(adjacency, seeds, sources,
                              weight=args.weight, max_cost=args.max_cost)
    bands = assign_bands(costs, breaks)

    if args.json_path:
        export_isochrone_json(costs, bands, args.json_path)
        print(f"JSON → {args.json_path}")

    if args.csv_path:
        export_isochrone_csv(costs, bands, args.csv_path)
        print(f"CSV  → {args.csv_path}")

    if args.svg_path:
        export_isochrone_svg(bands, regions, data, args.svg_path,
                             breaks=breaks)
        print(f"SVG  → {args.svg_path}")

    if args.report or not (args.json_path or args.csv_path or args.svg_path):
        print(format_report(costs, bands, breaks, sources))


if __name__ == "__main__":
    main()
