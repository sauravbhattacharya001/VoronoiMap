"""Voronoi Watershed & Flow Analysis (vormap_watershed).

Computes hydrological flow direction and accumulation on a Voronoi
tessellation where each cell has an elevation (or any scalar attribute)
value.  Identifies drainage basins (watersheds), pour points, flow
paths, ridgelines, and sink cells.

Useful for terrain analysis, surface runoff modelling, and any spatial
process where material flows downhill along a tessellation.

Approach
--------
Each Voronoi cell is treated as a node in a directed graph.  Flow
direction is assigned to the steepest-descent neighbor (D8 analogue on
irregular tessellations).  Accumulation counts upstream cells.  Basins
are groups of cells that drain to the same terminal sink.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_watershed import (
        watershed_analysis, export_watershed_svg,
        export_watershed_json, export_watershed_csv,
    )

    data = vormap.load_data("terrain.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = watershed_analysis(stats, attribute="area")
    print(result.summary())
    for basin in result.basins:
        print(f"Basin {basin.basin_id}: {basin.cell_count} cells, "
              f"sink at {basin.sink_index}")

    export_watershed_svg(result, regions, data, "watershed.svg")

CLI::

    python vormap_watershed.py terrain.txt --attribute area
    python vormap_watershed.py terrain.txt --svg watershed.svg
    python vormap_watershed.py terrain.txt --json watershed.json
    python vormap_watershed.py terrain.txt --csv watershed.csv

"""


import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from vormap import validate_output_path

from vormap_utils import polygon_centroid, polygon_area, euclidean as _dist

try:
    from vormap_geometry import edge_length as _geom_dist  # noqa: F401
except ImportError:  # pragma: no cover
    pass


# ── Data structures ─────────────────────────────────────────────────


@dataclass
class FlowCell:
    """Flow properties for a single Voronoi cell."""

    index: int
    centroid: Tuple[float, float]
    elevation: float
    flow_to: Optional[int] = None  # index of downstream neighbor
    slope: float = 0.0  # slope to downstream neighbor
    accumulation: int = 1  # upstream cell count (including self)
    basin_id: int = -1
    is_sink: bool = False
    is_ridge: bool = False
    distance_to_sink: float = 0.0
    flow_path_length: int = 0  # hops to sink


@dataclass
class Basin:
    """A drainage basin (watershed)."""

    basin_id: int
    sink_index: int
    sink_centroid: Tuple[float, float]
    sink_elevation: float
    cell_indices: List[int] = field(default_factory=list)
    cell_count: int = 0
    total_area: float = 0.0
    mean_elevation: float = 0.0
    max_elevation: float = 0.0
    max_accumulation: int = 0
    pour_point: Optional[int] = None  # lowest cell on basin boundary
    ridge_cells: List[int] = field(default_factory=list)


@dataclass
class FlowPath:
    """A traced flow path from a source cell to a sink."""

    source_index: int
    cell_indices: List[int] = field(default_factory=list)
    elevations: List[float] = field(default_factory=list)
    total_drop: float = 0.0
    path_length: float = 0.0  # Euclidean distance along path


@dataclass
class WatershedResult:
    """Complete watershed analysis result."""

    cells: List[FlowCell] = field(default_factory=list)
    basins: List[Basin] = field(default_factory=list)
    flow_paths: List[FlowPath] = field(default_factory=list)
    ridge_cells: List[int] = field(default_factory=list)
    sink_cells: List[int] = field(default_factory=list)
    total_cells: int = 0
    attribute: str = "area"

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            "=== Voronoi Watershed Analysis ===",
            f"Total cells:       {self.total_cells}",
            f"Drainage basins:   {len(self.basins)}",
            f"Sink cells:        {len(self.sink_cells)}",
            f"Ridge cells:       {len(self.ridge_cells)}",
            f"Attribute:         {self.attribute}",
        ]
        if self.basins:
            largest = max(self.basins, key=lambda b: b.cell_count)
            lines.append(
                f"Largest basin:     Basin {largest.basin_id} "
                f"({largest.cell_count} cells, sink at {largest.sink_index})"
            )
            max_acc = max(self.cells, key=lambda c: c.accumulation)
            lines.append(
                f"Max accumulation:  Cell {max_acc.index} "
                f"({max_acc.accumulation} upstream cells)"
            )
        return "\n".join(lines)


# ── Adjacency ───────────────────────────────────────────────────────


def _build_adjacency(stats: list) -> Dict[int, List[int]]:
    """Build neighbor adjacency from region stats (queen contiguity)."""
    adj: Dict[int, List[int]] = {}
    n = len(stats)
    # Use polygon edge sharing: two regions are neighbors if they share
    # two consecutive boundary vertices.
    for i in range(n):
        adj[i] = []

    # Build a map of edge (pair of rounded vertices) -> region indices
    edge_map: Dict[Tuple, List[int]] = {}
    for i, s in enumerate(stats):
        poly = s.get("polygon") or s.get("vertices", [])
        if not poly:
            continue
        for j in range(len(poly)):
            p1 = poly[j]
            p2 = poly[(j + 1) % len(poly)]
            # Round to avoid floating point issues
            key = _edge_key(p1, p2)
            edge_map.setdefault(key, []).append(i)

    for indices in edge_map.values():
        for a in indices:
            for b in indices:
                if a != b and b not in adj.get(a, []):
                    adj.setdefault(a, []).append(b)

    return adj


def _edge_key(p1, p2) -> Tuple:
    """Canonical edge key from two points (rounded, sorted)."""
    a = (round(p1[0], 4), round(p1[1], 4))
    b = (round(p2[0], 4), round(p2[1], 4))
    return (min(a, b), max(a, b))


# ── Centroid/distance helpers ───────────────────────────────────────


def _centroid(poly) -> Tuple[float, float]:
    """Compute centroid of a polygon."""
    if not poly:
        return (0.0, 0.0)
    return polygon_centroid(poly)



def _poly_area(poly) -> float:
    """Compute polygon area."""
    if not poly or len(poly) < 3:
        return 0.0
    return polygon_area(poly)


# ── Core analysis ───────────────────────────────────────────────────


def _get_elevation(stat: dict, attribute: str) -> float:
    """Extract the elevation value from a region stat."""
    if attribute == "area":
        poly = stat.get("polygon") or stat.get("vertices", [])
        return _poly_area(poly)
    return float(stat.get(attribute, 0))


def watershed_analysis(
    stats: list,
    attribute: str = "area",
    trace_paths: bool = True,
    max_path_traces: int = 50,
) -> WatershedResult:
    """Run watershed analysis on Voronoi region stats.

    Parameters
    ----------
    stats : list of dict
        Region statistics from ``vormap_viz.compute_region_stats``.
        Each dict should have ``polygon`` (or ``vertices``) and any
        numeric attribute to use as elevation.
    attribute : str
        Which attribute to use as the elevation surface.  Default
        ``"area"`` uses polygon area.
    trace_paths : bool
        Whether to trace flow paths from high-accumulation cells.
    max_path_traces : int
        Maximum number of flow paths to trace (from highest
        accumulation cells).

    Returns
    -------
    WatershedResult
    """
    n = len(stats)
    if n == 0:
        return WatershedResult(attribute=attribute)

    # 1. Build adjacency
    adj = _build_adjacency(stats)

    # 2. Create flow cells
    cells: List[FlowCell] = []
    for i, s in enumerate(stats):
        poly = s.get("polygon") or s.get("vertices", [])
        cells.append(FlowCell(
            index=i,
            centroid=_centroid(poly),
            elevation=_get_elevation(s, attribute),
        ))

    # 3. Assign flow direction (steepest descent)
    for i, cell in enumerate(cells):
        neighbors = adj.get(i, [])
        best_slope = 0.0
        best_neighbor = None
        for nb in neighbors:
            d = _dist(cell.centroid, cells[nb].centroid)
            if d < 1e-12:
                continue
            slope = (cell.elevation - cells[nb].elevation) / d
            if slope > best_slope:
                best_slope = slope
                best_neighbor = nb
        if best_neighbor is not None:
            cell.flow_to = best_neighbor
            cell.slope = best_slope
        else:
            cell.is_sink = True

    # 4. Detect ridges (cells where no neighbor flows TO this cell...
    #    actually: cells where ALL neighbors have lower elevation)
    inflow: Set[int] = set()
    for cell in cells:
        if cell.flow_to is not None:
            inflow.add(cell.flow_to)
    for cell in cells:
        if cell.index not in inflow and not cell.is_sink:
            cell.is_ridge = True

    # 5. Compute accumulation (count upstream cells)
    #    Sort by elevation descending, propagate accumulation downstream
    sorted_indices = sorted(range(n), key=lambda i: -cells[i].elevation)
    for i in sorted_indices:
        if cells[i].flow_to is not None:
            cells[cells[i].flow_to].accumulation += cells[i].accumulation

    # 6. Trace each cell to its sink → assign basin IDs
    sink_cells: List[int] = [c.index for c in cells if c.is_sink]

    # Map each sink to a basin ID
    sink_to_basin: Dict[int, int] = {}
    for bid, si in enumerate(sink_cells):
        sink_to_basin[si] = bid

    for cell in cells:
        # Follow flow_to chain to find sink
        current = cell.index
        visited: Set[int] = set()
        path: List[int] = []
        while current is not None and current not in visited:
            visited.add(current)
            path.append(current)
            if cells[current].is_sink:
                break
            current = cells[current].flow_to

        # current is the sink (or None for a cycle)
        if current is not None and cells[current].is_sink:
            bid = sink_to_basin[current]
            for ci in path:
                cells[ci].basin_id = bid
            cell.flow_path_length = len(path) - 1
            cell.distance_to_sink = sum(
                _dist(cells[path[j]].centroid, cells[path[j + 1]].centroid)
                for j in range(len(path) - 1)
            )

    # 7. Build basin summaries
    basins_dict: Dict[int, Basin] = {}
    for si in sink_cells:
        bid = sink_to_basin[si]
        basins_dict[bid] = Basin(
            basin_id=bid,
            sink_index=si,
            sink_centroid=cells[si].centroid,
            sink_elevation=cells[si].elevation,
        )

    for cell in cells:
        if cell.basin_id < 0:
            continue
        basin = basins_dict[cell.basin_id]
        basin.cell_indices.append(cell.index)
        basin.cell_count += 1
        if cell.is_ridge:
            basin.ridge_cells.append(cell.index)

    # Compute basin stats
    for basin in basins_dict.values():
        elevations = [cells[i].elevation for i in basin.cell_indices]
        basin.mean_elevation = sum(elevations) / len(elevations) if elevations else 0
        basin.max_elevation = max(elevations) if elevations else 0
        accumulations = [cells[i].accumulation for i in basin.cell_indices]
        basin.max_accumulation = max(accumulations) if accumulations else 0

        # Total area from polygons
        for i in basin.cell_indices:
            poly = stats[i].get("polygon") or stats[i].get("vertices", [])
            basin.total_area += _poly_area(poly)

        # Pour point: lowest-elevation cell on basin boundary
        #   (a boundary cell has at least one neighbor in a different basin)
        boundary_cells = []
        for i in basin.cell_indices:
            for nb in adj.get(i, []):
                if cells[nb].basin_id != basin.basin_id:
                    boundary_cells.append(i)
                    break
        if boundary_cells:
            basin.pour_point = min(
                boundary_cells, key=lambda i: cells[i].elevation
            )

    basins = sorted(basins_dict.values(), key=lambda b: -b.cell_count)

    # 8. Trace flow paths from highest-accumulation cells
    flow_paths: List[FlowPath] = []
    if trace_paths:
        top_cells = sorted(
            range(n), key=lambda i: -cells[i].accumulation
        )[:max_path_traces]
        for src in top_cells:
            if cells[src].is_sink:
                continue
            path_indices: List[int] = [src]
            path_elevations: List[float] = [cells[src].elevation]
            current = cells[src].flow_to
            visited_fp: Set[int] = {src}
            while current is not None and current not in visited_fp:
                visited_fp.add(current)
                path_indices.append(current)
                path_elevations.append(cells[current].elevation)
                if cells[current].is_sink:
                    break
                current = cells[current].flow_to
            if len(path_indices) > 1:
                fp = FlowPath(
                    source_index=src,
                    cell_indices=path_indices,
                    elevations=path_elevations,
                    total_drop=path_elevations[0] - path_elevations[-1],
                )
                fp.path_length = sum(
                    _dist(cells[path_indices[j]].centroid,
                          cells[path_indices[j + 1]].centroid)
                    for j in range(len(path_indices) - 1)
                )
                flow_paths.append(fp)

    ridge_cells = [c.index for c in cells if c.is_ridge]

    return WatershedResult(
        cells=cells,
        basins=basins,
        flow_paths=flow_paths,
        ridge_cells=ridge_cells,
        sink_cells=sink_cells,
        total_cells=n,
        attribute=attribute,
    )


# ── SVG export ──────────────────────────────────────────────────────


_BASIN_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#bab0ac", "#86bcb6", "#d4a6c8",
    "#6b9ac4", "#d37295", "#8cd17d", "#f1ce63",
]


def _basin_color(basin_id: int) -> str:
    return _BASIN_COLORS[basin_id % len(_BASIN_COLORS)]


def export_watershed_svg(
    result: WatershedResult,
    regions: list,
    data: dict,
    path: str,
    show_flow: bool = True,
    show_ridges: bool = True,
    width: int = 800,
    height: int = 600,
) -> str:
    """Export watershed analysis as SVG.

    Parameters
    ----------
    result : WatershedResult
    regions : list
        Regions from ``vormap_viz.compute_regions``.
    data : dict
        Data from ``vormap.load_data``.
    path : str
        Output SVG file path.
    show_flow : bool
        Draw flow direction arrows.
    show_ridges : bool
        Highlight ridge cells.
    width, height : int
        SVG canvas size.
    """
    validate_output_path(path)

    # Determine bounding box from data
    points = data.get("seeds", data.get("points", []))
    if not points:
        points = [(c.centroid[0], c.centroid[1]) for c in result.cells]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad = max((max_x - min_x), (max_y - min_y)) * 0.05

    vb_x = min_x - pad
    vb_y = min_y - pad
    vb_w = (max_x - min_x) + 2 * pad
    vb_h = (max_y - min_y) + 2 * pad

    root = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": f"{vb_x:.2f} {vb_y:.2f} {vb_w:.2f} {vb_h:.2f}",
    })

    # Background
    ET.SubElement(root, "rect", {
        "x": f"{vb_x:.2f}", "y": f"{vb_y:.2f}",
        "width": f"{vb_w:.2f}", "height": f"{vb_h:.2f}",
        "fill": "#f8f9fa",
    })

    # Draw basin-colored polygons
    stats_list = regions if isinstance(regions, list) else []
    for cell in result.cells:
        poly = None
        if cell.index < len(stats_list):
            r = stats_list[cell.index]
            poly = r.get("polygon") or r.get("vertices") if isinstance(r, dict) else None
        if not poly or len(poly) < 3:
            continue
        pts_str = " ".join(f"{p[0]:.2f},{p[1]:.2f}" for p in poly)
        color = _basin_color(cell.basin_id) if cell.basin_id >= 0 else "#cccccc"
        opacity = "0.5"
        if cell.is_sink:
            opacity = "0.9"
        ET.SubElement(root, "polygon", {
            "points": pts_str,
            "fill": color,
            "stroke": "#333333",
            "stroke-width": "0.5",
            "opacity": opacity,
        })

    # Flow direction arrows
    if show_flow:
        for cell in result.cells:
            if cell.flow_to is None:
                continue
            target = result.cells[cell.flow_to]
            cx, cy = cell.centroid
            tx, ty = target.centroid
            # Draw from cell toward target (shorten to 60%)
            mx = cx + (tx - cx) * 0.6
            my = cy + (ty - cy) * 0.6
            ET.SubElement(root, "line", {
                "x1": f"{cx:.2f}", "y1": f"{cy:.2f}",
                "x2": f"{mx:.2f}", "y2": f"{my:.2f}",
                "stroke": "#1a1a2e",
                "stroke-width": "0.8",
                "marker-end": "url(#arrow)",
                "opacity": "0.6",
            })

        # Arrowhead marker
        defs = ET.SubElement(root, "defs")
        marker = ET.SubElement(defs, "marker", {
            "id": "arrow",
            "markerWidth": "6", "markerHeight": "6",
            "refX": "5", "refY": "3",
            "orient": "auto",
        })
        ET.SubElement(marker, "path", {
            "d": "M0,0 L6,3 L0,6 Z",
            "fill": "#1a1a2e",
        })

    # Ridge cells — red dots
    if show_ridges:
        for ri in result.ridge_cells:
            cx, cy = result.cells[ri].centroid
            ET.SubElement(root, "circle", {
                "cx": f"{cx:.2f}", "cy": f"{cy:.2f}",
                "r": "3",
                "fill": "#e74c3c",
                "stroke": "#c0392b",
                "stroke-width": "0.5",
                "opacity": "0.8",
            })

    # Sink cells — blue squares
    for si in result.sink_cells:
        cx, cy = result.cells[si].centroid
        ET.SubElement(root, "rect", {
            "x": f"{cx - 4:.2f}", "y": f"{cy - 4:.2f}",
            "width": "8", "height": "8",
            "fill": "#2980b9",
            "stroke": "#1a5276",
            "stroke-width": "1",
        })

    # Flow paths — thick colored lines
    for fp in result.flow_paths[:10]:  # Top 10 only for clarity
        if len(fp.cell_indices) < 2:
            continue
        path_pts = [result.cells[i].centroid for i in fp.cell_indices]
        d = "M" + " L".join(f"{p[0]:.2f},{p[1]:.2f}" for p in path_pts)
        ET.SubElement(root, "path", {
            "d": d,
            "fill": "none",
            "stroke": "#2c3e50",
            "stroke-width": "2",
            "stroke-linecap": "round",
            "opacity": "0.7",
        })

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, xml_declaration=True, encoding="unicode")
    return path


# ── JSON export ─────────────────────────────────────────────────────


def export_watershed_json(result: WatershedResult, path: str) -> str:
    """Export watershed analysis as JSON."""
    validate_output_path(path, allow_absolute=True)
    doc = {
        "summary": {
            "totalCells": result.total_cells,
            "basinCount": len(result.basins),
            "sinkCount": len(result.sink_cells),
            "ridgeCount": len(result.ridge_cells),
            "attribute": result.attribute,
        },
        "basins": [
            {
                "basinId": b.basin_id,
                "sinkIndex": b.sink_index,
                "sinkCentroid": list(b.sink_centroid),
                "sinkElevation": round(b.sink_elevation, 4),
                "cellCount": b.cell_count,
                "totalArea": round(b.total_area, 2),
                "meanElevation": round(b.mean_elevation, 4),
                "maxElevation": round(b.max_elevation, 4),
                "maxAccumulation": b.max_accumulation,
                "pourPoint": b.pour_point,
            }
            for b in result.basins
        ],
        "cells": [
            {
                "index": c.index,
                "centroid": [round(c.centroid[0], 2), round(c.centroid[1], 2)],
                "elevation": round(c.elevation, 4),
                "flowTo": c.flow_to,
                "slope": round(c.slope, 6),
                "accumulation": c.accumulation,
                "basinId": c.basin_id,
                "isSink": c.is_sink,
                "isRidge": c.is_ridge,
            }
            for c in result.cells
        ],
        "flowPaths": [
            {
                "sourceIndex": fp.source_index,
                "cellCount": len(fp.cell_indices),
                "totalDrop": round(fp.total_drop, 4),
                "pathLength": round(fp.path_length, 2),
            }
            for fp in result.flow_paths
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return path


# ── CSV export ──────────────────────────────────────────────────────


def export_watershed_csv(result: WatershedResult, path: str) -> str:
    """Export per-cell watershed data as CSV."""
    validate_output_path(path, allow_absolute=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "cell_index", "centroid_x", "centroid_y", "elevation",
            "flow_to", "slope", "accumulation", "basin_id",
            "is_sink", "is_ridge", "distance_to_sink", "flow_path_length",
        ])
        for c in result.cells:
            writer.writerow([
                c.index,
                round(c.centroid[0], 2), round(c.centroid[1], 2),
                round(c.elevation, 4),
                c.flow_to if c.flow_to is not None else "",
                round(c.slope, 6),
                c.accumulation,
                c.basin_id,
                c.is_sink,
                c.is_ridge,
                round(c.distance_to_sink, 2),
                c.flow_path_length,
            ])
    return path


# ── CLI ─────────────────────────────────────────────────────────────


def main(argv: list = None):
    """Command-line interface."""
    parser = argparse.ArgumentParser(
        description="Voronoi Watershed & Flow Analysis",
    )
    parser.add_argument("datafile", help="Input data file (seed points)")
    parser.add_argument(
        "--attribute", default="area",
        help="Attribute to use as elevation surface (default: area)",
    )
    parser.add_argument("--svg", metavar="PATH", help="Export SVG")
    parser.add_argument("--json", metavar="PATH", help="Export JSON")
    parser.add_argument("--csv", metavar="PATH", help="Export CSV")
    parser.add_argument(
        "--no-flow", action="store_true",
        help="Omit flow direction arrows in SVG",
    )
    parser.add_argument(
        "--no-ridges", action="store_true",
        help="Omit ridge cell markers in SVG",
    )
    parser.add_argument(
        "--max-paths", type=int, default=50,
        help="Max flow paths to trace (default: 50)",
    )

    args = parser.parse_args(argv)

    from vormap import load_data
    import vormap_viz

    data = load_data(args.datafile)
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = watershed_analysis(
        stats,
        attribute=args.attribute,
        max_path_traces=args.max_paths,
    )

    print(result.summary())

    if args.svg:
        export_watershed_svg(
            result, stats, data, args.svg,
            show_flow=not args.no_flow,
            show_ridges=not args.no_ridges,
        )
        print(f"SVG written to {args.svg}")

    if args.json:
        export_watershed_json(result, args.json)
        print(f"JSON written to {args.json}")

    if args.csv:
        export_watershed_csv(result, args.csv)
        print(f"CSV written to {args.csv}")


if __name__ == "__main__":
    main()
