"""Voronoi Edge Network — extract and analyse polygon boundary edges.

The neighbourhood graph module (``vormap_graph``) handles the **dual**
graph: seed-to-seed adjacency (equivalent to the Delaunay triangulation).
This module handles the **primal** graph: the actual polygon boundary
edges (vertex-to-vertex) that make up the Voronoi tessellation itself.

Use cases include road-network modelling, drainage-pattern analysis,
material grain-boundary statistics, and topological skeletonisation.

Functions
---------
- ``extract_edge_network``  — build the primal vertex/edge graph
- ``compute_edge_stats``    — aggregate edge-network metrics
- ``format_edge_stats``     — human-readable text table
- ``export_edge_csv``       — CSV edge list with lengths
- ``export_edge_json``      — JSON with vertices, edges, and stats
- ``export_edge_svg``       — SVG visualisation of the edge network
"""

import math
import json
import xml.etree.ElementTree as ET

import vormap

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

from vormap_viz import compute_regions


# ── Helpers ──────────────────────────────────────────────────────────

def _round_vertex(v, tol=0.5):
    """Round a vertex to a tolerance grid so near-duplicates merge."""
    return (round(v[0] / tol) * tol, round(v[1] / tol) * tol)


def _edge_length(v1, v2):
    """Euclidean distance between two 2D points."""
    return math.sqrt((v1[0] - v2[0]) ** 2 + (v1[1] - v2[1]) ** 2)


def _edge_angle(v1, v2):
    """Angle of the edge from v1→v2 in degrees [0, 360)."""
    dx = v2[0] - v1[0]
    dy = v2[1] - v1[1]
    angle = math.degrees(math.atan2(dy, dx)) % 360
    return angle


# ── Core extraction ──────────────────────────────────────────────────

def extract_edge_network(regions, *, tol=0.5):
    """Extract the primal edge network from Voronoi regions.

    Walks every region polygon, merges near-duplicate vertices (within
    *tol*), and builds an undirected edge graph of boundary segments.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    tol : float
        Vertex-merging tolerance (default: 0.5).

    Returns
    -------
    dict
        - ``vertices`` (list): unique vertex coordinates ``(x, y)``
        - ``vertex_index`` (dict): maps ``(x, y)`` → index in *vertices*
        - ``edges`` (list of dict): each has ``i``, ``j`` (vertex indices),
          ``length``, ``angle``, ``regions`` (count of regions sharing
          the edge: 1 = boundary, 2 = interior)
        - ``adjacency`` (dict): maps vertex index → set of neighbour indices
        - ``num_vertices`` (int)
        - ``num_edges`` (int)
    """
    if not regions:
        raise ValueError("No regions to extract edges from")

    # Collect all edges with their region-sharing counts
    edge_counts = {}   # frozenset((v1, v2)) → region count
    edge_raw = {}      # frozenset → (v1, v2) in consistent order

    for seed, vertices in regions.items():
        n = len(vertices)
        if n < 2:
            continue
        for i in range(n):
            j = (i + 1) % n
            v1 = _round_vertex(vertices[i], tol)
            v2 = _round_vertex(vertices[j], tol)
            if v1 == v2:
                continue
            key = frozenset((v1, v2))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            if key not in edge_raw:
                # Store in consistent order (smaller first)
                edge_raw[key] = (min(v1, v2), max(v1, v2))

    # Build unique vertex list
    vertex_set = set()
    for key in edge_raw:
        v1, v2 = edge_raw[key]
        vertex_set.add(v1)
        vertex_set.add(v2)

    vertices = sorted(vertex_set)
    vertex_index = {v: idx for idx, v in enumerate(vertices)}

    # Build edge list
    edges = []
    adjacency = {idx: set() for idx in range(len(vertices))}

    for key in sorted(edge_raw.keys(), key=lambda k: sorted(k)):
        v1, v2 = edge_raw[key]
        i = vertex_index[v1]
        j = vertex_index[v2]
        length = _edge_length(v1, v2)
        angle = _edge_angle(v1, v2)
        region_count = edge_counts[key]

        edges.append({
            "i": i,
            "j": j,
            "v1": v1,
            "v2": v2,
            "length": round(length, 4),
            "angle": round(angle, 2),
            "regions": region_count,
        })
        adjacency[i].add(j)
        adjacency[j].add(i)

    return {
        "vertices": vertices,
        "vertex_index": vertex_index,
        "edges": edges,
        "adjacency": adjacency,
        "num_vertices": len(vertices),
        "num_edges": len(edges),
    }


# ── Statistics ───────────────────────────────────────────────────────

def compute_edge_stats(network):
    """Compute aggregate statistics for the edge network.

    Parameters
    ----------
    network : dict
        Output of ``extract_edge_network()``.

    Returns
    -------
    dict
        - ``num_vertices``, ``num_edges`` (int)
        - ``total_length`` (float): sum of all edge lengths
        - ``mean_length``, ``median_length``, ``min_length``, ``max_length``
        - ``std_length`` (float): standard deviation of edge lengths
        - ``num_boundary_edges`` (int): edges shared by 1 region (outer boundary)
        - ``num_interior_edges`` (int): edges shared by 2 regions
        - ``boundary_fraction`` (float): boundary / total edges
        - ``mean_degree`` (float): average vertex degree
        - ``max_degree`` (int): maximum vertex degree
        - ``num_junctions`` (int): vertices with degree ≥ 3
        - ``num_dead_ends`` (int): vertices with degree 1
        - ``num_isolated`` (int): vertices with degree 0
        - ``angle_entropy`` (float): Shannon entropy of angle distribution
          (8 bins of 45°) — higher = more isotropic
    """
    edges = network["edges"]
    adjacency = network["adjacency"]
    n_verts = network["num_vertices"]
    n_edges = network["num_edges"]

    if n_edges == 0:
        return {
            "num_vertices": n_verts,
            "num_edges": 0,
            "total_length": 0.0,
            "mean_length": 0.0,
            "median_length": 0.0,
            "min_length": 0.0,
            "max_length": 0.0,
            "std_length": 0.0,
            "num_boundary_edges": 0,
            "num_interior_edges": 0,
            "boundary_fraction": 0.0,
            "mean_degree": 0.0,
            "max_degree": 0,
            "num_junctions": 0,
            "num_dead_ends": 0,
            "num_isolated": n_verts,
            "angle_entropy": 0.0,
        }

    lengths = [e["length"] for e in edges]
    lengths_sorted = sorted(lengths)
    total = sum(lengths)
    mean = total / n_edges
    median_idx = n_edges // 2
    median = (lengths_sorted[median_idx] if n_edges % 2 == 1
              else (lengths_sorted[median_idx - 1] + lengths_sorted[median_idx]) / 2)

    variance = sum((l - mean) ** 2 for l in lengths) / n_edges
    std = math.sqrt(variance)

    boundary = sum(1 for e in edges if e["regions"] == 1)
    interior = sum(1 for e in edges if e["regions"] >= 2)

    degrees = [len(adjacency[i]) for i in range(n_verts)]
    mean_degree = sum(degrees) / n_verts if n_verts > 0 else 0.0
    max_degree = max(degrees) if degrees else 0
    junctions = sum(1 for d in degrees if d >= 3)
    dead_ends = sum(1 for d in degrees if d == 1)
    isolated = sum(1 for d in degrees if d == 0)

    # Angle entropy (8 bins of 45°)
    angle_bins = [0] * 8
    for e in edges:
        bin_idx = min(int(e["angle"] / 45.0), 7)
        angle_bins[bin_idx] += 1

    entropy = 0.0
    for count in angle_bins:
        if count > 0:
            p = count / n_edges
            entropy -= p * math.log2(p)

    return {
        "num_vertices": n_verts,
        "num_edges": n_edges,
        "total_length": round(total, 4),
        "mean_length": round(mean, 4),
        "median_length": round(median, 4),
        "min_length": round(lengths_sorted[0], 4),
        "max_length": round(lengths_sorted[-1], 4),
        "std_length": round(std, 4),
        "num_boundary_edges": boundary,
        "num_interior_edges": interior,
        "boundary_fraction": round(boundary / n_edges, 4) if n_edges > 0 else 0.0,
        "mean_degree": round(mean_degree, 4),
        "max_degree": max_degree,
        "num_junctions": junctions,
        "num_dead_ends": dead_ends,
        "num_isolated": isolated,
        "angle_entropy": round(entropy, 4),
    }


def format_edge_stats(stats):
    """Format edge-network statistics as a human-readable text table.

    Parameters
    ----------
    stats : dict
        Output of ``compute_edge_stats()``.

    Returns
    -------
    str
    """
    lines = [
        "╔══════════════════════════════════════════════╗",
        "║        Voronoi Edge Network Statistics       ║",
        "╠══════════════════════════════════════════════╣",
        "║  Topology                                    ║",
        "║    Vertices:           %6d                 ║" % stats["num_vertices"],
        "║    Edges:              %6d                 ║" % stats["num_edges"],
        "║    Interior edges:     %6d                 ║" % stats["num_interior_edges"],
        "║    Boundary edges:     %6d                 ║" % stats["num_boundary_edges"],
        "║    Boundary fraction:  %6.1f%%                ║" % (stats["boundary_fraction"] * 100),
        "╠══════════════════════════════════════════════╣",
        "║  Edge Lengths                                ║",
        "║    Total length:       %10.2f             ║" % stats["total_length"],
        "║    Mean:               %10.2f             ║" % stats["mean_length"],
        "║    Median:             %10.2f             ║" % stats["median_length"],
        "║    Min:                %10.2f             ║" % stats["min_length"],
        "║    Max:                %10.2f             ║" % stats["max_length"],
        "║    Std dev:            %10.2f             ║" % stats["std_length"],
        "╠══════════════════════════════════════════════╣",
        "║  Vertex Connectivity                         ║",
        "║    Mean degree:        %6.2f                 ║" % stats["mean_degree"],
        "║    Max degree:         %6d                 ║" % stats["max_degree"],
        "║    Junctions (deg≥3):  %6d                 ║" % stats["num_junctions"],
        "║    Dead ends (deg=1):  %6d                 ║" % stats["num_dead_ends"],
        "║    Isolated (deg=0):   %6d                 ║" % stats["num_isolated"],
        "╠══════════════════════════════════════════════╣",
        "║  Orientation                                 ║",
        "║    Angle entropy:      %6.2f bits            ║" % stats["angle_entropy"],
        "║    (max = 3.00 = perfectly isotropic)        ║",
        "╚══════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)


# ── Export: CSV ──────────────────────────────────────────────────────

def export_edge_csv(network, output_path, *, include_stats=True):
    """Export edge list as CSV with optional summary header.

    Parameters
    ----------
    network : dict
        Output of ``extract_edge_network()``.
    output_path : str
        Destination file path.
    include_stats : bool
        If True, append a commented summary section.
    """
    edges = network["edges"]
    lines = ["vertex_i,vertex_j,x1,y1,x2,y2,length,angle_deg,shared_regions"]

    for e in edges:
        lines.append("%d,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.2f,%d" % (
            e["i"], e["j"],
            e["v1"][0], e["v1"][1],
            e["v2"][0], e["v2"][1],
            e["length"], e["angle"], e["regions"],
        ))

    if include_stats:
        stats = compute_edge_stats(network)
        lines.append("")
        lines.append("# Edge Network Summary")
        lines.append("# vertices,%d" % stats["num_vertices"])
        lines.append("# edges,%d" % stats["num_edges"])
        lines.append("# total_length,%.4f" % stats["total_length"])
        lines.append("# mean_length,%.4f" % stats["mean_length"])
        lines.append("# boundary_edges,%d" % stats["num_boundary_edges"])
        lines.append("# interior_edges,%d" % stats["num_interior_edges"])
        lines.append("# junctions,%d" % stats["num_junctions"])
        lines.append("# dead_ends,%d" % stats["num_dead_ends"])

    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ── Export: JSON ─────────────────────────────────────────────────────

def export_edge_json(network, output_path):
    """Export edge network as structured JSON.

    Parameters
    ----------
    network : dict
        Output of ``extract_edge_network()``.
    output_path : str
        Destination file path.
    """
    stats = compute_edge_stats(network)

    result = {
        "vertices": [{"index": i, "x": v[0], "y": v[1]}
                     for i, v in enumerate(network["vertices"])],
        "edges": [
            {
                "vertex_i": e["i"],
                "vertex_j": e["j"],
                "length": e["length"],
                "angle_deg": e["angle"],
                "shared_regions": e["regions"],
                "is_boundary": e["regions"] == 1,
            }
            for e in network["edges"]
        ],
        "stats": stats,
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)


# ── Export: SVG ──────────────────────────────────────────────────────

def export_edge_svg(
    network,
    output_path,
    *,
    width=800,
    height=600,
    show_vertices=True,
    show_boundary=True,
    title=None,
):
    """Export the edge network as an SVG visualization.

    Interior edges (shared by 2 regions) are drawn in one color;
    boundary edges (shared by 1 region) in another.  Junction vertices
    (degree ≥ 3) are highlighted.

    Parameters
    ----------
    network : dict
        Output of ``extract_edge_network()``.
    output_path : str
        Destination SVG file path.
    width, height : int
        SVG canvas dimensions (default: 800×600).
    show_vertices : bool
        If True, draw vertex dots (junctions highlighted).
    show_boundary : bool
        If True, visually distinguish boundary vs interior edges.
    title : str or None
        Optional title shown at the top of the SVG.
    """
    vertices = network["vertices"]
    edges = network["edges"]
    adjacency = network["adjacency"]

    if not vertices:
        # Empty diagram — produce a minimal SVG
        root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                          width=str(width), height=str(height))
        tree = ET.ElementTree(root)
        tree.write(output_path, xml_declaration=True)
        return

    # Compute coordinate bounds
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)

    # Add padding
    pad_x = (x_max - x_min) * 0.05 or 10
    pad_y = (y_max - y_min) * 0.05 or 10
    x_min -= pad_x
    x_max += pad_x
    y_min -= pad_y
    y_max += pad_y

    data_w = x_max - x_min
    data_h = y_max - y_min

    def tx(x):
        return (x - x_min) / data_w * width

    def ty(y):
        return height - (y - y_min) / data_h * height

    root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                      width=str(width), height=str(height))

    # Background
    ET.SubElement(root, "rect", x="0", y="0",
                  width=str(width), height=str(height),
                  fill="#0f172a")

    # Title
    if title:
        t = ET.SubElement(root, "text", x=str(width // 2), y="24",
                          fill="#e2e8f0", **{"font-size": "16",
                          "font-family": "sans-serif",
                          "text-anchor": "middle"})
        t.text = title

    # Draw edges
    for e in edges:
        x1, y1 = tx(e["v1"][0]), ty(e["v1"][1])
        x2, y2 = tx(e["v2"][0]), ty(e["v2"][1])

        if show_boundary and e["regions"] == 1:
            color = "#f87171"   # red for boundary
            stroke_w = "1.5"
            opacity = "0.8"
        else:
            color = "#38bdf8"   # blue for interior
            stroke_w = "1"
            opacity = "0.6"

        ET.SubElement(root, "line",
                      x1="%.2f" % x1, y1="%.2f" % y1,
                      x2="%.2f" % x2, y2="%.2f" % y2,
                      stroke=color,
                      **{"stroke-width": stroke_w,
                         "stroke-opacity": opacity})

    # Draw vertices
    if show_vertices:
        for i, v in enumerate(vertices):
            cx, cy = tx(v[0]), ty(v[1])
            degree = len(adjacency[i])

            if degree >= 3:
                # Junction — larger, highlighted
                ET.SubElement(root, "circle",
                              cx="%.2f" % cx, cy="%.2f" % cy,
                              r="3", fill="#4ade80", opacity="0.9")
            elif degree == 1:
                # Dead end
                ET.SubElement(root, "circle",
                              cx="%.2f" % cx, cy="%.2f" % cy,
                              r="2.5", fill="#fbbf24", opacity="0.8")
            else:
                ET.SubElement(root, "circle",
                              cx="%.2f" % cx, cy="%.2f" % cy,
                              r="1.5", fill="#94a3b8", opacity="0.5")

    # Legend
    legend_y = height - 60
    items = [
        ("#38bdf8", "Interior edge"),
        ("#f87171", "Boundary edge"),
        ("#4ade80", "Junction (deg≥3)"),
        ("#fbbf24", "Dead end (deg=1)"),
    ]
    for idx, (color, label) in enumerate(items):
        lx = 20 + idx * 180
        ET.SubElement(root, "rect", x=str(lx), y=str(legend_y),
                      width="12", height="12", fill=color, rx="2")
        t = ET.SubElement(root, "text", x=str(lx + 18), y=str(legend_y + 10),
                          fill="#94a3b8", **{"font-size": "11",
                          "font-family": "sans-serif"})
        t.text = label

    # Stats summary
    stats = compute_edge_stats(network)
    summary_text = "%d vertices · %d edges · %.0f total length · %d junctions" % (
        stats["num_vertices"], stats["num_edges"],
        stats["total_length"], stats["num_junctions"],
    )
    st = ET.SubElement(root, "text", x=str(width // 2), y=str(height - 10),
                       fill="#64748b", **{"font-size": "10",
                       "font-family": "sans-serif",
                       "text-anchor": "middle"})
    st.text = summary_text

    tree = ET.ElementTree(root)
    tree.write(output_path, xml_declaration=True)
