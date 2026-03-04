"""Neighbourhood graph analysis for VoronoiMap.

Extracts and analyses the neighbourhood (adjacency) graph from Voronoi
regions.  The graph is the *dual* of the Voronoi diagram — equivalent to
the Delaunay triangulation for points in general position.

Functions
---------
- ``extract_neighborhood_graph`` — build the adjacency graph from regions.
- ``compute_graph_stats`` — compute 14 structural metrics.
- ``export_graph_json`` / ``export_graph_csv`` — file export.
- ``format_graph_stats_table`` — human-readable text table.
- ``export_graph_svg`` — SVG visualisation with Voronoi overlay.
- ``generate_graph`` — convenience one-call pipeline.

All public symbols are also re-exported from ``vormap_viz`` for backward
compatibility.
"""

import math
from collections import deque
import xml.etree.ElementTree as ET

import vormap

# scipy / numpy are optional — mirrors the check in vormap_viz.py
try:
    import numpy as np
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

# Imports from vormap_viz needed by export_graph_svg / generate_graph.
# These are intentionally late-style to avoid circular imports at
# module level — vormap_viz re-exports our symbols.
from vormap_viz import (
    compute_regions,
    _COLOR_SCHEMES,
    DEFAULT_COLOR_SCHEME,
)


# ── Private helpers ──────────────────────────────────────────────────

def _edges_from_region_approx(vertices, tol=0.5):
    """Return edge representations that tolerate small coordinate differences.

    Each edge is stored as a frozenset of two rounded vertex tuples.  The
    tolerance-based rounding ensures that edges from neighbouring regions
    are recognised as shared even when floating-point imprecision causes
    vertex coordinates to differ slightly.
    """
    n = len(vertices)
    edges = set()
    for i in range(n):
        j = (i + 1) % n
        v1 = (round(vertices[i][0] / tol) * tol,
              round(vertices[i][1] / tol) * tol)
        v2 = (round(vertices[j][0] / tol) * tol,
              round(vertices[j][1] / tol) * tol)
        if v1 != v2:
            edges.add(frozenset((v1, v2)))
    return edges


# ── Graph extraction ─────────────────────────────────────────────────

def extract_neighborhood_graph(regions, data=None, *, tol=0.5):
    """Extract the neighborhood (adjacency) graph from Voronoi regions.

    Two seed points are neighbours if their Voronoi regions share at
    least one edge.  This is the *dual graph* of the Voronoi diagram —
    equivalent to the Delaunay triangulation for points in general
    position.

    When *scipy* is available and *data* has >= 3 points, uses
    ``scipy.spatial.Delaunay`` for a fast, exact result.  Otherwise
    falls back to polygon-edge matching with tolerance *tol*.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed -> vertex list.
    data : list of (float, float) or None
        All seed points.  When provided and scipy is available, the
        Delaunay triangulation is used for exact adjacency.
    tol : float
        Vertex-coordinate rounding tolerance for the polygon-edge
        fallback method (default: 0.5).

    Returns
    -------
    dict
        A dict with keys:

        - ``adjacency`` (dict): maps each seed ``(x, y)`` to a sorted
          list of its neighbour seeds.
        - ``edges`` (list): list of ``((x1, y1), (x2, y2))`` edge tuples
          (each unordered pair appears exactly once).
        - ``seed_indices`` (dict): maps seed -> 0-based index in *data*
          (or in sorted-seed order when *data* is None).
        - ``num_nodes`` (int): number of seed points with regions.
        - ``num_edges`` (int): number of neighbourhood edges.

    Raises
    ------
    ValueError
        If *regions* is empty.

    Examples
    --------
    >>> import vormap, vormap_viz
    >>> data = vormap.load_data("datauni5.txt")
    >>> regions = vormap_viz.compute_regions(data)
    >>> graph = vormap_viz.extract_neighborhood_graph(regions, data)
    >>> graph["num_nodes"], graph["num_edges"]
    (5, 8)
    """
    if not regions:
        raise ValueError("No regions to build graph from")

    sorted_seeds = sorted(regions.keys())

    # Build seed -> index mapping
    seed_indices = {}
    if data is not None:
        for idx, pt in enumerate(data):
            seed_indices[tuple(pt)] = idx
        # Fill in any seeds not in data (shouldn't happen, but be safe)
        for seed in sorted_seeds:
            if seed not in seed_indices:
                seed_indices[seed] = len(seed_indices)
    else:
        for idx, seed in enumerate(sorted_seeds):
            seed_indices[seed] = idx

    adjacency = {seed: [] for seed in sorted_seeds}
    edge_set = set()

    # --- Fast path: scipy Delaunay ---
    if _HAS_SCIPY and data is not None and len(data) >= 3:
        try:
            from scipy.spatial import Delaunay
            points_array = np.array(data)
            tri = Delaunay(points_array)

            # Extract edges from simplices
            for simplex in tri.simplices:
                for i in range(3):
                    for j in range(i + 1, 3):
                        p1 = tuple(data[simplex[i]])
                        p2 = tuple(data[simplex[j]])
                        # Only include edges where both points have regions
                        if p1 in regions and p2 in regions:
                            edge = (min(p1, p2), max(p1, p2))
                            if edge not in edge_set:
                                edge_set.add(edge)
                                adjacency[p1].append(p2)
                                adjacency[p2].append(p1)
        except Exception:
            # Fall through to polygon-edge method
            adjacency = {seed: [] for seed in sorted_seeds}
            edge_set = set()

    # --- Fallback: polygon edge matching ---
    if not edge_set:
        seed_edges = {}
        for seed in sorted_seeds:
            seed_edges[seed] = _edges_from_region_approx(
                regions[seed], tol=tol
            )

        seeds_list = list(sorted_seeds)
        for i in range(len(seeds_list)):
            for j in range(i + 1, len(seeds_list)):
                s1 = seeds_list[i]
                s2 = seeds_list[j]
                shared = seed_edges[s1] & seed_edges[s2]
                if shared:
                    edge = (min(s1, s2), max(s1, s2))
                    if edge not in edge_set:
                        edge_set.add(edge)
                        adjacency[s1].append(s2)
                        adjacency[s2].append(s1)

    # Sort adjacency lists for deterministic output
    for seed in adjacency:
        adjacency[seed] = sorted(adjacency[seed])

    edges = sorted(edge_set)

    return {
        "adjacency": adjacency,
        "edges": edges,
        "seed_indices": seed_indices,
        "num_nodes": len(sorted_seeds),
        "num_edges": len(edges),
    }


# ── Graph statistics ─────────────────────────────────────────────────

def compute_graph_stats(graph):
    """Compute statistics for the neighbourhood graph.

    Parameters
    ----------
    graph : dict
        Output of ``extract_neighborhood_graph()``.

    Returns
    -------
    dict
        A dict with keys:

        - ``num_nodes`` (int): number of nodes.
        - ``num_edges`` (int): number of edges.
        - ``density`` (float): edge density (2E / N(N-1)).
        - ``min_degree`` (int): minimum node degree.
        - ``max_degree`` (int): maximum node degree.
        - ``mean_degree`` (float): average node degree.
        - ``degree_distribution`` (dict): degree -> count.
        - ``clustering_coefficient`` (float): global clustering coefficient
          (ratio of closed triplets to all triplets).
        - ``connected_components`` (int): number of connected components.
        - ``is_connected`` (bool): True if the graph has exactly one
          connected component.
        - ``diameter`` (int or None): longest shortest path (only computed
          for connected graphs; None if disconnected).
        - ``avg_path_length`` (float or None): average shortest path
          length (only for connected graphs).
        - ``isolated_nodes`` (int): nodes with degree 0.
        - ``leaf_nodes`` (int): nodes with degree 1.
    """
    adjacency = graph["adjacency"]
    n = graph["num_nodes"]
    m = graph["num_edges"]

    if n == 0:
        return {
            "num_nodes": 0, "num_edges": 0, "density": 0.0,
            "min_degree": 0, "max_degree": 0, "mean_degree": 0.0,
            "degree_distribution": {},
            "clustering_coefficient": 0.0,
            "connected_components": 0, "is_connected": False,
            "diameter": None, "avg_path_length": None,
            "isolated_nodes": 0, "leaf_nodes": 0,
        }

    # Degree stats
    degrees = {seed: len(neighbors) for seed, neighbors in adjacency.items()}
    degree_values = list(degrees.values())
    min_deg = min(degree_values)
    max_deg = max(degree_values)
    mean_deg = sum(degree_values) / n

    degree_dist = {}
    for d in degree_values:
        degree_dist[d] = degree_dist.get(d, 0) + 1

    isolated = sum(1 for d in degree_values if d == 0)
    leaves = sum(1 for d in degree_values if d == 1)

    # Density
    density = (2.0 * m) / (n * (n - 1)) if n > 1 else 0.0

    # Clustering coefficient (global)
    neighbor_set = {seed: set(neigh) for seed, neigh in adjacency.items()}
    total_triplets = 0
    closed_triplets = 0
    for seed, neighbors in adjacency.items():
        k = len(neighbors)
        if k < 2:
            continue
        total_triplets += k * (k - 1) // 2
        for i in range(len(neighbors)):
            for j in range(i + 1, len(neighbors)):
                if neighbors[j] in neighbor_set[neighbors[i]]:
                    closed_triplets += 1
    clustering = closed_triplets / total_triplets if total_triplets > 0 else 0.0

    # Connected components (BFS)
    visited = set()
    components = 0
    seeds_list = list(adjacency.keys())

    for start in seeds_list:
        if start in visited:
            continue
        components += 1
        queue = deque([start])
        visited.add(start)
        while queue:
            node = queue.popleft()
            for neigh in adjacency[node]:
                if neigh not in visited:
                    visited.add(neigh)
                    queue.append(neigh)

    is_connected = components == 1

    # Diameter and avg path length (BFS from each node, only if connected)
    diameter = None
    avg_path_length = None

    if is_connected and n > 1:
        # Double-BFS approximation for diameter: O(V+E) instead of O(V*(V+E))
        # Exact for trees, tight approximation for sparse planar graphs like
        # Voronoi duals.
        def _bfs_farthest(start):
            """BFS from start; return (farthest_node, max_distance, dist_map)."""
            dist = {start: 0}
            queue = deque([start])
            farthest = start
            max_d = 0
            while queue:
                node = queue.popleft()
                for neigh in adjacency[node]:
                    if neigh not in dist:
                        dist[neigh] = dist[node] + 1
                        queue.append(neigh)
                        if dist[neigh] > max_d:
                            max_d = dist[neigh]
                            farthest = neigh
            return farthest, max_d, dist

        u = seeds_list[0]
        v, _, _ = _bfs_farthest(u)
        _, diam, _ = _bfs_farthest(v)
        diameter = diam

        # Sampled average path length: pick up to 50 random sources for a
        # statistically sound O(K*(V+E)) estimate instead of O(V*(V+E)).
        import random as _rand
        sample_k = min(50, n)
        sample_sources = _rand.sample(seeds_list, sample_k) if n > sample_k else seeds_list
        total_dist = 0
        pair_count = 0
        for start in sample_sources:
            dist = {start: 0}
            queue = deque([start])
            while queue:
                node = queue.popleft()
                for neigh in adjacency[node]:
                    if neigh not in dist:
                        dist[neigh] = dist[node] + 1
                        queue.append(neigh)
                        total_dist += dist[neigh]
                        pair_count += 1
        avg_path_length = round(total_dist / pair_count, 4) if pair_count > 0 else 0.0

    return {
        "num_nodes": n,
        "num_edges": m,
        "density": round(density, 4),
        "min_degree": min_deg,
        "max_degree": max_deg,
        "mean_degree": round(mean_deg, 4),
        "degree_distribution": dict(sorted(degree_dist.items())),
        "clustering_coefficient": round(clustering, 4),
        "connected_components": components,
        "is_connected": is_connected,
        "diameter": diameter,
        "avg_path_length": avg_path_length,
        "isolated_nodes": isolated,
        "leaf_nodes": leaves,
    }


# ── Export functions ─────────────────────────────────────────────────

def export_graph_json(graph, output_path, *, include_stats=True):
    """Export the neighbourhood graph as a JSON file.

    The JSON contains nodes (with coordinates and degree), edges (with
    endpoint coordinates and Euclidean length), and optionally graph
    statistics.

    Parameters
    ----------
    graph : dict
        Output of ``extract_neighborhood_graph()``.
    output_path : str
        Path to write the JSON file.
    include_stats : bool
        If True (default), include graph statistics in the output.

    Returns
    -------
    str
        Path to the generated JSON file.
    """
    import json

    if graph["num_nodes"] == 0:
        raise ValueError("No graph data to export")

    adjacency = graph["adjacency"]
    seed_indices = graph["seed_indices"]

    nodes = []
    for seed in sorted(adjacency.keys()):
        idx = seed_indices.get(seed, -1)
        nodes.append({
            "index": idx,
            "x": seed[0],
            "y": seed[1],
            "degree": len(adjacency[seed]),
            "neighbors": [seed_indices.get(n, -1) for n in adjacency[seed]],
        })

    edges = []
    for s1, s2 in graph["edges"]:
        length = math.sqrt((s1[0] - s2[0]) ** 2 + (s1[1] - s2[1]) ** 2)
        edges.append({
            "source": seed_indices.get(s1, -1),
            "target": seed_indices.get(s2, -1),
            "source_x": s1[0],
            "source_y": s1[1],
            "target_x": s2[0],
            "target_y": s2[1],
            "length": round(length, 4),
        })

    output = {
        "nodes": nodes,
        "edges": edges,
        "num_nodes": graph["num_nodes"],
        "num_edges": graph["num_edges"],
    }

    if include_stats:
        output["stats"] = compute_graph_stats(graph)

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return output_path


def export_graph_csv(graph, output_path):
    """Export the neighbourhood graph as a CSV edge list.

    Each row contains source and target node indices, coordinates, and
    edge length.  A summary section is appended as comments.

    Parameters
    ----------
    graph : dict
        Output of ``extract_neighborhood_graph()``.
    output_path : str
        Path to write the CSV file.

    Returns
    -------
    str
        Path to the generated CSV file.
    """
    import csv

    if graph["num_nodes"] == 0:
        raise ValueError("No graph data to export")

    seed_indices = graph["seed_indices"]

    fieldnames = [
        "source_index", "target_index",
        "source_x", "source_y",
        "target_x", "target_y",
        "length",
    ]

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for s1, s2 in graph["edges"]:
            length = math.sqrt((s1[0] - s2[0]) ** 2 + (s1[1] - s2[1]) ** 2)
            writer.writerow({
                "source_index": seed_indices.get(s1, -1),
                "target_index": seed_indices.get(s2, -1),
                "source_x": s1[0],
                "source_y": s1[1],
                "target_x": s2[0],
                "target_y": s2[1],
                "length": round(length, 4),
            })

        # Summary
        stats = compute_graph_stats(graph)
        f.write("\n# Graph Summary\n")
        f.write("# nodes: %d\n" % stats["num_nodes"])
        f.write("# edges: %d\n" % stats["num_edges"])
        f.write("# density: %.4f\n" % stats["density"])
        f.write("# mean_degree: %.4f\n" % stats["mean_degree"])
        f.write("# clustering_coefficient: %.4f\n" % stats["clustering_coefficient"])
        f.write("# connected_components: %d\n" % stats["connected_components"])
        if stats["diameter"] is not None:
            f.write("# diameter: %d\n" % stats["diameter"])
        if stats["avg_path_length"] is not None:
            f.write("# avg_path_length: %.4f\n" % stats["avg_path_length"])

    return output_path


def format_graph_stats_table(graph):
    """Format graph statistics as a human-readable text table.

    Parameters
    ----------
    graph : dict
        Output of ``extract_neighborhood_graph()``.

    Returns
    -------
    str
        Formatted table string ready for printing.
    """
    stats = compute_graph_stats(graph)

    lines = [
        "Neighborhood Graph Statistics",
        "=" * 40,
        "",
        "  Nodes:              %d" % stats["num_nodes"],
        "  Edges:              %d" % stats["num_edges"],
        "  Density:            %.4f" % stats["density"],
        "",
        "  Min degree:         %d" % stats["min_degree"],
        "  Max degree:         %d" % stats["max_degree"],
        "  Mean degree:        %.2f" % stats["mean_degree"],
        "  Isolated nodes:     %d" % stats["isolated_nodes"],
        "  Leaf nodes:         %d" % stats["leaf_nodes"],
        "",
        "  Clustering coeff:   %.4f" % stats["clustering_coefficient"],
        "  Components:         %d" % stats["connected_components"],
        "  Connected:          %s" % ("Yes" if stats["is_connected"] else "No"),
    ]

    if stats["diameter"] is not None:
        lines.append("  Diameter:           %d" % stats["diameter"])
    if stats["avg_path_length"] is not None:
        lines.append("  Avg path length:    %.2f" % stats["avg_path_length"])

    lines.append("")
    lines.append("  Degree distribution:")
    for deg in sorted(stats["degree_distribution"]):
        count = stats["degree_distribution"][deg]
        bar = "\u2588" * count
        lines.append("    %2d: %3d  %s" % (deg, count, bar))

    return "\n".join(lines)


# ── SVG visualization ───────────────────────────────────────────────

def export_graph_svg(
    regions,
    data,
    graph,
    output_path,
    *,
    width=800,
    height=600,
    margin=40,
    color_scheme=DEFAULT_COLOR_SCHEME,
    show_voronoi=True,
    show_graph=True,
    show_degree_labels=False,
    edge_color="#e74c3c",
    edge_width=2.0,
    node_radius=5.0,
    node_color="#c0392b",
    title=None,
):
    """Export an SVG showing the Voronoi diagram with graph overlay.

    Draws the Voronoi regions as filled polygons (optionally) and
    overlays the neighbourhood graph edges and nodes.  This visualizes
    the Delaunay dual directly on top of the tessellation.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()``.
    data : list of (float, float)
        All seed points.
    graph : dict
        Output of ``extract_neighborhood_graph()``.
    output_path : str
        Path to write the SVG file.
    width, height, margin : int
        SVG canvas dimensions and padding.
    color_scheme : str
        Color scheme for Voronoi region fills.
    show_voronoi : bool
        Whether to draw the Voronoi regions (default: True).
    show_graph : bool
        Whether to draw the graph edges and nodes (default: True).
    show_degree_labels : bool
        If True, label each node with its degree.
    edge_color : str
        CSS color for graph edges.
    edge_width : float
        Stroke width for graph edges.
    node_radius : float
        Radius of graph node markers.
    node_color : str
        CSS color for graph node fills.
    title : str or None
        Optional title text.

    Returns
    -------
    str
        Path to the generated SVG file.
    """
    if color_scheme not in _COLOR_SCHEMES:
        raise ValueError(
            "Unknown color scheme '%s'. Available: %s"
            % (color_scheme, ", ".join(sorted(_COLOR_SCHEMES)))
        )

    color_fn = _COLOR_SCHEMES[color_scheme]

    # Coordinate transform
    all_xs = [pt[0] for pt in data]
    all_ys = [pt[1] for pt in data]
    for verts in regions.values():
        for vx, vy in verts:
            all_xs.append(vx)
            all_ys.append(vy)

    if not all_xs or not all_ys:
        raise ValueError("No data to visualize")

    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)
    range_x = max(max_x - min_x, 1e-6)
    range_y = max(max_y - min_y, 1e-6)
    draw_w = width - 2 * margin
    draw_h = height - 2 * margin
    scale = min(draw_w / range_x, draw_h / range_y)
    offset_x = margin + (draw_w - range_x * scale) / 2
    offset_y = margin + (draw_h - range_y * scale) / 2

    def tx(x):
        return offset_x + (x - min_x) * scale

    def ty(y):
        return offset_y + (max_y - y) * scale

    # Build SVG
    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    })

    style = ET.SubElement(svg, "style")
    style.text = (
        ".region { stroke: #888; stroke-width: 0.8; stroke-linejoin: round; opacity: 0.6; }"
        " .graph-edge { stroke: %s; stroke-width: %.1f; stroke-linecap: round; }"
        " .graph-node { fill: %s; stroke: #fff; stroke-width: 1.5; }"
        " .degree-label { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 9px; fill: #fff; text-anchor: middle;"
        " dominant-baseline: central; pointer-events: none;"
        " font-weight: 700; }"
        " .title { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 16px; font-weight: 600; fill: #222;"
        " text-anchor: middle; }"
        " .legend { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 11px; fill: #555; }"
        % (vormap.sanitize_css_value(edge_color), edge_width,
           vormap.sanitize_css_value(node_color))
    )

    # Background
    ET.SubElement(svg, "rect", {
        "width": str(width), "height": str(height), "fill": "#ffffff",
    })

    # Title
    if title:
        title_el = ET.SubElement(svg, "text", {
            "x": str(width / 2), "y": str(margin / 2 + 4), "class": "title",
        })
        title_el.text = title

    # Voronoi regions (semi-transparent background)
    if show_voronoi:
        voronoi_group = ET.SubElement(svg, "g", {"id": "voronoi-regions"})
        sorted_seeds = sorted(regions.keys())
        for idx, seed in enumerate(sorted_seeds):
            verts = regions[seed]
            points_str = " ".join(
                "%.2f,%.2f" % (tx(vx), ty(vy)) for vx, vy in verts
            )
            fill = color_fn(idx, len(sorted_seeds))
            ET.SubElement(voronoi_group, "polygon", {
                "points": points_str, "fill": fill, "class": "region",
            })

    # Graph edges
    if show_graph:
        edge_group = ET.SubElement(svg, "g", {"id": "graph-edges"})
        for s1, s2 in graph["edges"]:
            ET.SubElement(edge_group, "line", {
                "x1": "%.2f" % tx(s1[0]),
                "y1": "%.2f" % ty(s1[1]),
                "x2": "%.2f" % tx(s2[0]),
                "y2": "%.2f" % ty(s2[1]),
                "class": "graph-edge",
            })

        # Graph nodes
        node_group = ET.SubElement(svg, "g", {"id": "graph-nodes"})
        adjacency = graph["adjacency"]
        for seed in sorted(adjacency.keys()):
            ET.SubElement(node_group, "circle", {
                "cx": "%.2f" % tx(seed[0]),
                "cy": "%.2f" % ty(seed[1]),
                "r": str(node_radius),
                "class": "graph-node",
            })

        # Degree labels
        if show_degree_labels:
            label_group = ET.SubElement(svg, "g", {"id": "degree-labels"})
            for seed in sorted(adjacency.keys()):
                deg = len(adjacency[seed])
                label = ET.SubElement(label_group, "text", {
                    "x": "%.2f" % tx(seed[0]),
                    "y": "%.2f" % ty(seed[1]),
                    "class": "degree-label",
                })
                label.text = str(deg)

    # Legend
    stats = compute_graph_stats(graph)
    legend_y = height - 12
    legend = ET.SubElement(svg, "text", {
        "x": str(margin), "y": str(legend_y), "class": "legend",
    })
    legend.text = (
        "%d nodes \u00b7 %d edges \u00b7 density %.3f \u00b7 mean degree %.1f \u00b7 "
        "clustering %.3f"
        % (stats["num_nodes"], stats["num_edges"], stats["density"],
           stats["mean_degree"], stats["clustering_coefficient"])
    )

    # Write
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)
    return output_path


# ── Convenience entry point ──────────────────────────────────────────

def generate_graph(datafile, output_path=None, *, fmt="table"):
    """Load data, compute regions, extract graph, and export in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str or None
        Where to write the output.
    fmt : str
        Output format: "table", "json", "csv", or "svg".

    Returns
    -------
    str or dict
        File path (if output_path given), formatted string (table),
        or dict (json without output_path).
    """
    data = vormap.load_data(datafile)
    regions = compute_regions(data)
    graph = extract_neighborhood_graph(regions, data)

    if fmt == "json":
        if output_path is None:
            stats = compute_graph_stats(graph)
            return {"graph": graph, "stats": stats}
        return export_graph_json(graph, output_path)
    elif fmt == "csv":
        if output_path is None:
            raise ValueError("CSV format requires an output_path")
        return export_graph_csv(graph, output_path)
    elif fmt == "svg":
        if output_path is None:
            raise ValueError("SVG format requires an output_path")
        return export_graph_svg(regions, data, graph, output_path)
    else:
        return format_graph_stats_table(graph)
