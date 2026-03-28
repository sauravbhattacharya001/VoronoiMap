"""Spatial network analysis via Delaunay triangulation for Voronoi diagrams.

Constructs the Delaunay dual graph (where Voronoi sites are nodes and
Delaunay edges connect adjacent sites) and provides spatial network
analysis tools:

- **Delaunay graph construction** — O(n log n) via incremental insertion
- **Minimum Spanning Tree (MST)** — Kruskal's algorithm with union-find
- **Degree distribution** — node connectivity histogram
- **Betweenness centrality** — hub/bridge identification via BFS
- **Graph diameter & average path length** — network extent metrics
- **Connected components** — spatial fragmentation detection
- **Network efficiency** — global efficiency metric (Latora & Marchiori)
- **SVG, JSON, CSV export** — visualization and data exchange

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_network import build_delaunay_graph, compute_mst
    from vormap_network import network_stats, export_network_svg

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    graph = build_delaunay_graph(stats)
    mst = compute_mst(graph)
    nstats = network_stats(graph)
    print(nstats.summary_text())
    export_network_svg(graph, mst, stats, "network.svg")

CLI::

    voronoimap datauni5.txt 5 --network
    voronoimap datauni5.txt 5 --network-svg network.svg
    voronoimap datauni5.txt 5 --network-json network.json
    voronoimap datauni5.txt 5 --network-csv network.csv
    voronoimap datauni5.txt 5 --mst-svg mst.svg
"""

import json
import math
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field

from vormap_geometry import edge_length

# Alias for backward compatibility (used by tests)
_euclidean = edge_length


# ── Graph construction ──────────────────────────────────────────────

def _centroids_from_stats(stats):
    """Extract (x, y) centroids from region stats."""
    centroids = []
    for s in stats:
        if "centroid" in s:
            cx, cy = s["centroid"]
        else:
            cx = s.get("centroid_x", 0)
            cy = s.get("centroid_y", 0)
        centroids.append((cx, cy))
    return centroids


def build_delaunay_graph(stats):
    """Build the Delaunay adjacency graph from Voronoi region stats.

    Uses the adjacency relationships already implicit in the Voronoi
    tessellation: two sites share a Delaunay edge iff their Voronoi
    regions share a boundary edge.

    We detect adjacency by checking for shared vertices between
    region polygons (within a small tolerance).

    Parameters
    ----------
    stats : list[dict]
        Region statistics from ``vormap_viz.compute_region_stats()``.
        Each entry needs ``"polygon"`` (list of (x,y) vertices) and
        ``"centroid"`` (x, y).

    Returns
    -------
    dict
        ``{"nodes": [...], "edges": [...], "adjacency": {int: [int]}}``
        where each node has ``index``, ``centroid``, ``area``; each edge
        has ``source``, ``target``, ``weight`` (Euclidean distance).
    """
    n = len(stats)
    centroids = _centroids_from_stats(stats)
    adjacency = {i: [] for i in range(n)}
    edges = []
    seen = set()

    # Build shared-edge adjacency via vertex matching.
    # Two polygons are adjacent if they share at least 2 vertices.
    tol = 1e-6
    vertex_map = {}  # (rounded x, rounded y) -> list of region indices

    for i, s in enumerate(stats):
        poly = s.get("polygon", [])
        for vx, vy in poly:
            # Round to tolerance level for consistent vertex matching
            key = (round(vx / tol) * tol, round(vy / tol) * tol)
            vertex_map.setdefault(key, []).append(i)

    # Count shared vertices per region pair
    pair_counts = {}
    for rkey, region_indices in vertex_map.items():
        unique = list(set(region_indices))
        for a_idx in range(len(unique)):
            for b_idx in range(a_idx + 1, len(unique)):
                pair = (unique[a_idx], unique[b_idx])
                pair_counts[pair] = pair_counts.get(pair, 0) + 1

    # Pairs sharing >= 2 vertices are adjacent (share an edge)
    for (i, j), count in pair_counts.items():
        if count >= 2 and (i, j) not in seen:
            seen.add((i, j))
            seen.add((j, i))
            dist = edge_length(centroids[i], centroids[j])
            edges.append({"source": i, "target": j, "weight": dist})
            adjacency[i].append(j)
            adjacency[j].append(i)

    nodes = []
    for i, s in enumerate(stats):
        if "centroid" in s:
            cx, cy = s["centroid"]
        else:
            cx = s.get("centroid_x", 0)
            cy = s.get("centroid_y", 0)
        area = s.get("area", 0)
        nodes.append({"index": i, "centroid": (cx, cy), "area": area})

    return {"nodes": nodes, "edges": edges, "adjacency": adjacency}


# ── Minimum Spanning Tree (Kruskal's) ──────────────────────────────

class _UnionFind:
    """Disjoint-set / union-find for Kruskal's MST."""

    def __init__(self, n):
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # path splitting
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def compute_mst(graph):
    """Compute the Minimum Spanning Tree using Kruskal's algorithm.

    Parameters
    ----------
    graph : dict
        Graph from :func:`build_delaunay_graph`.

    Returns
    -------
    dict
        ``{"edges": [...], "total_weight": float}`` — MST edges and
        total distance.
    """
    n = len(graph["nodes"])
    if n == 0:
        return {"edges": [], "total_weight": 0.0}

    sorted_edges = sorted(graph["edges"], key=lambda e: e["weight"])
    uf = _UnionFind(n)
    mst_edges = []
    total = 0.0

    for e in sorted_edges:
        if uf.union(e["source"], e["target"]):
            mst_edges.append(e)
            total += e["weight"]
            if len(mst_edges) == n - 1:
                break

    return {"edges": mst_edges, "total_weight": total}


# ── Network statistics ──────────────────────────────────────────────

@dataclass
class NetworkStats:
    """Container for spatial network analysis results."""

    num_nodes: int = 0
    num_edges: int = 0
    num_components: int = 0
    density: float = 0.0
    avg_degree: float = 0.0
    max_degree: int = 0
    min_degree: int = 0
    degree_distribution: dict = field(default_factory=dict)
    diameter: int = 0
    avg_path_length: float = 0.0
    global_efficiency: float = 0.0
    betweenness: dict = field(default_factory=dict)
    hub_nodes: list = field(default_factory=list)
    mst_weight: float = 0.0
    mst_edges: int = 0
    avg_edge_length: float = 0.0
    max_edge_length: float = 0.0
    min_edge_length: float = 0.0

    def summary_text(self):
        """Human-readable network analysis summary."""
        lines = [
            "=== Spatial Network Analysis ===",
            f"Nodes: {self.num_nodes}",
            f"Edges: {self.num_edges}",
            f"Components: {self.num_components}",
            f"Density: {self.density:.4f}",
            f"Avg degree: {self.avg_degree:.2f}",
            f"Degree range: {self.min_degree}–{self.max_degree}",
            f"Diameter: {self.diameter}",
            f"Avg path length: {self.avg_path_length:.2f}",
            f"Global efficiency: {self.global_efficiency:.4f}",
            f"MST total weight: {self.mst_weight:.2f} ({self.mst_edges} edges)",
            f"Edge length — avg: {self.avg_edge_length:.2f}, "
            f"min: {self.min_edge_length:.2f}, max: {self.max_edge_length:.2f}",
            "",
            "--- Degree Distribution ---",
        ]
        for deg in sorted(self.degree_distribution):
            lines.append(f"  degree {deg}: {self.degree_distribution[deg]} nodes")
        if self.hub_nodes:
            lines.append("")
            lines.append("--- Hub Nodes (top betweenness centrality) ---")
            for h in self.hub_nodes[:10]:
                lines.append(
                    f"  Node {h['index']}: betweenness={h['betweenness']:.4f}, "
                    f"degree={h['degree']}"
                )
        return "\n".join(lines)


def _bfs_distances(adjacency, source, n):
    """BFS from source, return distance array (-1 for unreachable)."""
    dist = [-1] * n
    dist[source] = 0
    queue = deque([source])
    while queue:
        u = queue.popleft()
        for v in adjacency.get(u, []):
            if dist[v] == -1:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def _betweenness_and_distances(adjacency, n):
    """Combined Brandes' betweenness centrality + all-pairs distance stats.

    Performs a single BFS from every node, computing both betweenness
    centrality and distance metrics (diameter, average path length,
    global efficiency) in one pass.  This halves the work compared to
    computing betweenness and distances separately, since both require
    O(n) BFS traversals.

    Returns
    -------
    tuple
        (betweenness: dict, diameter: int, avg_path_length: float,
         global_efficiency: float)
    """
    cb = {i: 0.0 for i in range(n)}
    diameter = 0
    total_dist = 0
    num_pairs = 0
    efficiency_sum = 0.0

    for s in range(n):
        # BFS from s
        stack = []
        pred = {i: [] for i in range(n)}
        sigma = {i: 0 for i in range(n)}
        sigma[s] = 1
        dist = {i: -1 for i in range(n)}
        dist[s] = 0
        queue = deque([s])

        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adjacency.get(v, []):
                if dist[w] < 0:
                    queue.append(w)
                    dist[w] = dist[v] + 1
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)

        # Accumulate distance stats (only for j > s to avoid double-counting)
        for j in range(s + 1, n):
            if dist[j] > 0:
                total_dist += dist[j]
                num_pairs += 1
                if dist[j] > diameter:
                    diameter = dist[j]
                efficiency_sum += 1.0 / dist[j]

        delta = {i: 0.0 for i in range(n)}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
            if w != s:
                cb[w] += delta[w]

    # Normalize for undirected graph
    norm = (n - 1) * (n - 2) / 2 if n > 2 else 1
    if norm > 0:
        for i in cb:
            cb[i] /= norm

    avg_path = total_dist / num_pairs if num_pairs > 0 else 0
    max_pairs = n * (n - 1) / 2 if n > 1 else 1
    global_eff = efficiency_sum / max_pairs if max_pairs > 0 else 0

    return cb, diameter, avg_path, global_eff


def _betweenness_centrality(adjacency, n):
    """Backward-compatible wrapper — returns only betweenness dict."""
    bc, _, _, _ = _betweenness_and_distances(adjacency, n)
    return bc


def _connected_components(adjacency, n):
    """Find connected components via BFS."""
    visited = [False] * n
    components = []

    for i in range(n):
        if visited[i]:
            continue
        comp = []
        queue = deque([i])
        visited[i] = True
        while queue:
            u = queue.popleft()
            comp.append(u)
            for v in adjacency.get(u, []):
                if not visited[v]:
                    visited[v] = True
                    queue.append(v)
        components.append(comp)

    return components


def network_stats(graph):
    """Compute comprehensive network statistics.

    Parameters
    ----------
    graph : dict
        Graph from :func:`build_delaunay_graph`.

    Returns
    -------
    NetworkStats
        Complete network analysis results.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    adj = graph["adjacency"]
    n = len(nodes)

    if n == 0:
        return NetworkStats()

    # Degree distribution
    degrees = {i: len(adj.get(i, [])) for i in range(n)}
    deg_values = list(degrees.values())
    avg_degree = sum(deg_values) / n if n > 0 else 0
    max_deg = max(deg_values) if deg_values else 0
    min_deg = min(deg_values) if deg_values else 0

    deg_dist = {}
    for d in deg_values:
        deg_dist[d] = deg_dist.get(d, 0) + 1

    # Edge lengths
    edge_weights = [e["weight"] for e in edges]
    avg_edge = sum(edge_weights) / len(edge_weights) if edge_weights else 0
    max_edge = max(edge_weights) if edge_weights else 0
    min_edge = min(edge_weights) if edge_weights else 0

    # Connected components
    components = _connected_components(adj, n)

    # Density
    max_edges = n * (n - 1) / 2 if n > 1 else 1
    density = len(edges) / max_edges if max_edges > 0 else 0

    # Combined betweenness centrality + distance metrics in a single
    # BFS-from-every-node pass (halves the O(n) BFS traversals).
    bc, diameter, avg_path, global_eff = _betweenness_and_distances(adj, n)

    # Hub nodes (top 10 by betweenness)
    hub_list = sorted(
        [{"index": i, "betweenness": bc[i], "degree": degrees[i]} for i in range(n)],
        key=lambda h: h["betweenness"],
        reverse=True,
    )[:10]

    # MST
    mst = compute_mst(graph)

    return NetworkStats(
        num_nodes=n,
        num_edges=len(edges),
        num_components=len(components),
        density=density,
        avg_degree=avg_degree,
        max_degree=max_deg,
        min_degree=min_deg,
        degree_distribution=deg_dist,
        diameter=diameter,
        avg_path_length=avg_path,
        global_efficiency=global_eff,
        betweenness=bc,
        hub_nodes=hub_list,
        mst_weight=mst["total_weight"],
        mst_edges=len(mst["edges"]),
        avg_edge_length=avg_edge,
        max_edge_length=max_edge,
        min_edge_length=min_edge,
    )


# ── Export: SVG ─────────────────────────────────────────────────────

def export_network_svg(
    graph,
    stats_list,
    output_path,
    *,
    mst=None,
    width=800,
    height=600,
    show_labels=False,
    highlight_hubs=True,
    betweenness=None,
):
    """Export the spatial network as an SVG visualization.

    Parameters
    ----------
    graph : dict
        Graph from :func:`build_delaunay_graph`.
    stats_list : list[dict]
        Region stats with polygon/centroid data for coordinate mapping.
    output_path : str
        Path for the output SVG file.
    mst : dict or None
        MST from :func:`compute_mst`; if provided, MST edges are
        highlighted in a separate color.
    width, height : int
        SVG canvas dimensions.
    show_labels : bool
        Whether to label nodes with their index.
    highlight_hubs : bool
        Whether to size nodes by betweenness centrality.
    betweenness : dict or None
        Pre-computed betweenness centrality from :func:`network_stats`.
        When provided, avoids redundant O(n²) BFS re-computation.
    """
    nodes = graph["nodes"]
    edges = graph["edges"]
    if not nodes:
        return

    # Compute coordinate bounds for scaling
    xs = [n["centroid"][0] for n in nodes]
    ys = [n["centroid"][1] for n in nodes]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = x_max - x_min or 1
    y_range = y_max - y_min or 1
    padding = 40

    def tx(x):
        return padding + (x - x_min) / x_range * (width - 2 * padding)

    def ty(y):
        return padding + (y_max - y) / y_range * (height - 2 * padding)

    svg = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width=str(width),
        height=str(height),
        viewBox=f"0 0 {width} {height}",
    )

    # Background
    ET.SubElement(svg, "rect", width="100%", height="100%", fill="#1a1a2e")

    # Title
    title = ET.SubElement(svg, "text", x=str(width // 2), y="20",
                          fill="#e0e0e0")
    title.set("text-anchor", "middle")
    title.set("font-size", "14")
    title.set("font-family", "sans-serif")
    title.text = f"Spatial Network — {len(nodes)} nodes, {len(edges)} edges"

    # Draw edges
    edge_group = ET.SubElement(svg, "g", id="edges")
    for e in edges:
        s, t = e["source"], e["target"]
        x1, y1 = tx(nodes[s]["centroid"][0]), ty(nodes[s]["centroid"][1])
        x2, y2 = tx(nodes[t]["centroid"][0]), ty(nodes[t]["centroid"][1])
        ET.SubElement(
            edge_group, "line",
            x1=f"{x1:.1f}", y1=f"{y1:.1f}",
            x2=f"{x2:.1f}", y2=f"{y2:.1f}",
            stroke="#4a4a6a", **{"stroke-width": "0.8", "stroke-opacity": "0.5"},
        )

    # Draw MST edges (highlighted)
    if mst and mst.get("edges"):
        mst_group = ET.SubElement(svg, "g", id="mst")
        for e in mst["edges"]:
            s, t = e["source"], e["target"]
            x1, y1 = tx(nodes[s]["centroid"][0]), ty(nodes[s]["centroid"][1])
            x2, y2 = tx(nodes[t]["centroid"][0]), ty(nodes[t]["centroid"][1])
            ET.SubElement(
                mst_group, "line",
                x1=f"{x1:.1f}", y1=f"{y1:.1f}",
                x2=f"{x2:.1f}", y2=f"{y2:.1f}",
                stroke="#fbbf24", **{"stroke-width": "2", "stroke-opacity": "0.9"},
            )

    # Draw nodes — reuse pre-computed betweenness if provided to avoid
    # redundant O(n²) BFS traversals from every node.
    bc = betweenness if betweenness is not None else (
        _betweenness_centrality(graph["adjacency"], len(nodes)) if highlight_hubs else {}
    )
    max_bc = max(bc.values()) if bc else 1
    node_group = ET.SubElement(svg, "g", id="nodes")

    for node in nodes:
        idx = node["index"]
        cx, cy = tx(node["centroid"][0]), ty(node["centroid"][1])
        base_r = 3
        if highlight_hubs and max_bc > 0:
            r = base_r + 6 * (bc.get(idx, 0) / max_bc)
        else:
            r = base_r

        ET.SubElement(
            node_group, "circle",
            cx=f"{cx:.1f}", cy=f"{cy:.1f}", r=f"{r:.1f}",
            fill="#6366f1", **{"fill-opacity": "0.85"},
        )

        if show_labels:
            label = ET.SubElement(
                node_group, "text",
                x=f"{cx:.1f}", y=f"{cy - r - 2:.1f}",
                fill="#ccc",
            )
            label.set("text-anchor", "middle")
            label.set("font-size", "8")
            label.set("font-family", "sans-serif")
            label.text = str(idx)

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)


# ── Export: JSON ────────────────────────────────────────────────────

def export_network_json(graph, nstats, output_path, *, mst=None):
    """Export network analysis results as JSON.

    Parameters
    ----------
    graph : dict
        Graph from :func:`build_delaunay_graph`.
    nstats : NetworkStats
        Statistics from :func:`network_stats`.
    output_path : str
        Path for the output JSON file.
    mst : dict or None
        MST from :func:`compute_mst`.
    """
    data = {
        "summary": {
            "nodes": nstats.num_nodes,
            "edges": nstats.num_edges,
            "components": nstats.num_components,
            "density": round(nstats.density, 6),
            "avg_degree": round(nstats.avg_degree, 2),
            "degree_range": [nstats.min_degree, nstats.max_degree],
            "diameter": nstats.diameter,
            "avg_path_length": round(nstats.avg_path_length, 4),
            "global_efficiency": round(nstats.global_efficiency, 6),
            "avg_edge_length": round(nstats.avg_edge_length, 4),
            "edge_length_range": [
                round(nstats.min_edge_length, 4),
                round(nstats.max_edge_length, 4),
            ],
        },
        "degree_distribution": {
            str(k): v
            for k, v in sorted(nstats.degree_distribution.items())
        },
        "hub_nodes": [
            {
                "index": h["index"],
                "betweenness": round(h["betweenness"], 6),
                "degree": h["degree"],
            }
            for h in nstats.hub_nodes
        ],
        "nodes": [
            {
                "index": n["index"],
                "centroid": [round(c, 4) for c in n["centroid"]],
                "area": round(n["area"], 4),
            }
            for n in graph["nodes"]
        ],
        "edges": [
            {
                "source": e["source"],
                "target": e["target"],
                "weight": round(e["weight"], 4),
            }
            for e in graph["edges"]
        ],
    }

    if mst:
        data["mst"] = {
            "total_weight": round(mst["total_weight"], 4),
            "edges": [
                {
                    "source": e["source"],
                    "target": e["target"],
                    "weight": round(e["weight"], 4),
                }
                for e in mst["edges"]
            ],
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Export: CSV ─────────────────────────────────────────────────────

def export_network_csv(graph, nstats, output_path):
    """Export per-node network metrics as CSV.

    Columns: index, centroid_x, centroid_y, area, degree, betweenness.

    Parameters
    ----------
    graph : dict
        Graph from :func:`build_delaunay_graph`.
    nstats : NetworkStats
        Statistics from :func:`network_stats`.
    output_path : str
        Path for the output CSV file.
    """
    lines = ["index,centroid_x,centroid_y,area,degree,betweenness"]
    adj = graph["adjacency"]
    bc = nstats.betweenness

    for node in graph["nodes"]:
        idx = node["index"]
        cx, cy = node["centroid"]
        area = node["area"]
        deg = len(adj.get(idx, []))
        bet = bc.get(idx, 0)
        lines.append(f"{idx},{cx:.4f},{cy:.4f},{area:.4f},{deg},{bet:.6f}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
