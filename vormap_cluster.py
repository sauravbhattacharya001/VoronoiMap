"""Spatial clustering for Voronoi diagrams.

Groups Voronoi cells into clusters based on spatial adjacency and cell
metrics (area, density, compactness).  Three methods are supported:

- **threshold** — connected components of cells whose metric falls
  within a user-defined range.  Fast and interpretable.
- **dbscan** — density-based clustering using the adjacency graph
  instead of Euclidean distance.  Identifies dense cores, border
  cells, and noise (outlier cells) without requiring a target cluster
  count.
- **agglomerative** — bottom-up hierarchical merging of adjacent
  cells by metric similarity.  Stops when a target number of clusters
  is reached or when the merge cost exceeds a threshold.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_cluster import cluster_regions, export_cluster_json, export_cluster_svg

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = cluster_regions(stats, regions, data, method="dbscan", min_neighbors=2)
    for c in result.clusters:
        print(f"Cluster {c['cluster_id']}: {c['size']} cells, "
              f"avg area {c['mean_area']:.1f}")

    export_cluster_svg(result, regions, data, "clusters.svg")
    export_cluster_json(result, "clusters.json")

CLI::

    voronoimap datauni5.txt 5 --cluster
    voronoimap datauni5.txt 5 --cluster --cluster-method dbscan --cluster-min-neighbors 2
    voronoimap datauni5.txt 5 --cluster-svg clusters.svg
    voronoimap datauni5.txt 5 --cluster-json clusters.json
    voronoimap datauni5.txt 5 --cluster --cluster-method agglomerative --cluster-count 3
    voronoimap datauni5.txt 5 --cluster --cluster-method threshold --cluster-metric area --cluster-range 0,50000
"""

import json
import math
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field

import vormap
from vormap_viz import (
    compute_regions,
    compute_region_stats,
    _COLOR_SCHEMES,
    DEFAULT_COLOR_SCHEME,
)
from vormap_graph import extract_neighborhood_graph


# ── Result container ────────────────────────────────────────────────

@dataclass
class ClusterResult:
    """Container for spatial clustering results.

    Attributes
    ----------
    method : str
        Clustering method used ("threshold", "dbscan", "agglomerative").
    metric : str
        Cell metric used for clustering ("area", "density", "compactness").
    num_clusters : int
        Number of clusters found (excluding noise).
    num_noise : int
        Number of cells classified as noise (DBSCAN only).
    clusters : list of dict
        Per-cluster summaries with keys: cluster_id, size, seeds,
        mean_area, total_area, mean_compactness, centroid_x, centroid_y.
    labels : dict
        Maps seed (x, y) tuple to cluster label (int, -1 for noise).
    params : dict
        Parameters used for clustering.
    """
    method: str = ""
    metric: str = "area"
    num_clusters: int = 0
    num_noise: int = 0
    clusters: list = field(default_factory=list)
    labels: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)


# ── Adjacency helpers ───────────────────────────────────────────────

def _build_adjacency(regions, data):
    """Build adjacency dict from regions using the neighbourhood graph."""
    graph = extract_neighborhood_graph(regions, data)
    return graph["adjacency"]


def _build_stats_lookup(region_stats):
    """Build seed -> stats dict from region_stats list."""
    lookup = {}
    for stat in region_stats:
        seed = (stat["seed_x"], stat["seed_y"])
        lookup[seed] = stat
    return lookup


def _metric_value(stat, metric):
    """Extract a metric value from a region stats dict."""
    if metric == "area":
        return stat["area"]
    elif metric == "density":
        area = stat["area"]
        return 1.0 / area if area > 0 else float("inf")
    elif metric == "compactness":
        return stat["compactness"]
    elif metric == "vertices":
        return stat["vertex_count"]
    else:
        raise ValueError("Unknown metric: %s" % metric)


# ── Threshold clustering ───────────────────────────────────────────

def _cluster_threshold(seeds, adjacency, stats_lookup, metric,
                       value_range):
    """Connected components of cells whose metric falls in value_range.

    Parameters
    ----------
    seeds : list of (float, float)
        All seed points.
    adjacency : dict
        seed -> list of neighbor seeds.
    stats_lookup : dict
        seed -> region stats dict.
    metric : str
        Which metric to threshold on.
    value_range : tuple of (float, float)
        (min_val, max_val) inclusive range.

    Returns
    -------
    dict
        seed -> cluster_id (-1 for out-of-range cells).
    """
    lo, hi = value_range

    # Mark which seeds are within the threshold
    in_range = set()
    for seed in seeds:
        if seed in stats_lookup:
            val = _metric_value(stats_lookup[seed], metric)
            if lo <= val <= hi:
                in_range.add(seed)

    # BFS to find connected components among in-range cells
    labels = {seed: -1 for seed in seeds}
    cluster_id = 0
    visited = set()

    for seed in seeds:
        if seed not in in_range or seed in visited:
            continue
        # BFS from this seed
        queue = deque([seed])
        visited.add(seed)
        while queue:
            current = queue.popleft()
            labels[current] = cluster_id
            for neighbor in adjacency.get(current, []):
                nb = tuple(neighbor) if not isinstance(neighbor, tuple) else neighbor
                if nb in in_range and nb not in visited:
                    visited.add(nb)
                    queue.append(nb)
        cluster_id += 1

    return labels


# ── DBSCAN clustering ──────────────────────────────────────────────

def _cluster_dbscan(seeds, adjacency, stats_lookup, metric,
                    min_neighbors):
    """Graph-based DBSCAN using Voronoi adjacency.

    Instead of Euclidean distance, uses the adjacency graph to define
    neighborhoods.  A cell is a *core* cell if it has at least
    *min_neighbors* adjacent cells.  Core cells connected through
    other core cells form a cluster.  Non-core cells adjacent to a
    core cell are *border* cells (assigned to the cluster).  The rest
    are *noise* (label -1).

    Parameters
    ----------
    seeds : list of (float, float)
    adjacency : dict
    stats_lookup : dict
    metric : str
        Not directly used for distance — adjacency defines
        neighborhoods.  Metric can be used for future weighted
        variants.
    min_neighbors : int
        Minimum adjacency degree to be a core cell.

    Returns
    -------
    dict
        seed -> cluster_id (-1 for noise).
    """
    # Identify core cells
    core_seeds = set()
    for seed in seeds:
        neighbors = adjacency.get(seed, [])
        if len(neighbors) >= min_neighbors:
            core_seeds.add(seed)

    labels = {seed: -1 for seed in seeds}
    cluster_id = 0
    visited = set()

    # BFS from each unvisited core cell
    for seed in seeds:
        if seed not in core_seeds or seed in visited:
            continue

        queue = deque([seed])
        visited.add(seed)

        while queue:
            current = queue.popleft()
            labels[current] = cluster_id

            for neighbor in adjacency.get(current, []):
                nb = tuple(neighbor) if not isinstance(neighbor, tuple) else neighbor
                if nb in visited:
                    continue
                visited.add(nb)

                if nb in core_seeds:
                    # Core neighbor — expand the cluster
                    queue.append(nb)
                    labels[nb] = cluster_id
                elif nb in stats_lookup:
                    # Border cell — assign to cluster but don't expand
                    labels[nb] = cluster_id

        cluster_id += 1

    return labels


# ── Agglomerative clustering ──────────────────────────────────────

def _cluster_agglomerative(seeds, adjacency, stats_lookup, metric,
                           num_clusters):
    """Agglomerative clustering by metric similarity on the adjacency graph.

    Starts with each cell as its own cluster.  At each step, merges the
    two adjacent clusters whose mean metric values are most similar.
    Stops when the target number of clusters is reached.

    Parameters
    ----------
    seeds : list of (float, float)
    adjacency : dict
    stats_lookup : dict
    metric : str
    num_clusters : int
        Target number of clusters (>= 1).

    Returns
    -------
    dict
        seed -> cluster_id.
    """
    if num_clusters < 1:
        num_clusters = 1

    # Initialize: each seed is its own cluster
    seed_to_cluster = {}
    cluster_members = {}  # cluster_id -> set of seeds
    cluster_metric_sum = {}  # cluster_id -> sum of metric values
    cluster_size = {}

    for i, seed in enumerate(seeds):
        if seed not in stats_lookup:
            continue
        seed_to_cluster[seed] = i
        cluster_members[i] = {seed}
        cluster_metric_sum[i] = _metric_value(stats_lookup[seed], metric)
        cluster_size[i] = 1

    n_clusters = len(cluster_members)

    # Build edge set from adjacency (each pair once)
    seen_edges = set()
    edges = []
    for seed in seeds:
        for neighbor in adjacency.get(seed, []):
            nb = tuple(neighbor) if not isinstance(neighbor, tuple) else neighbor
            pair = (min(seed, nb), max(seed, nb))
            if pair not in seen_edges and seed in seed_to_cluster and nb in seed_to_cluster:
                seen_edges.add(pair)
                edges.append(pair)

    while n_clusters > num_clusters:
        # Find the merge with smallest metric distance between
        # adjacent cluster means
        best_cost = float("inf")
        best_merge = None  # (cluster_a, cluster_b)

        for s1, s2 in edges:
            c1 = seed_to_cluster.get(s1)
            c2 = seed_to_cluster.get(s2)
            if c1 is None or c2 is None or c1 == c2:
                continue
            mean1 = cluster_metric_sum[c1] / cluster_size[c1]
            mean2 = cluster_metric_sum[c2] / cluster_size[c2]
            cost = abs(mean1 - mean2)
            if cost < best_cost:
                best_cost = cost
                best_merge = (c1, c2)

        if best_merge is None:
            break  # No more adjacent clusters to merge

        c_keep, c_remove = best_merge
        # Merge c_remove into c_keep
        for seed in cluster_members[c_remove]:
            seed_to_cluster[seed] = c_keep
        cluster_members[c_keep] |= cluster_members[c_remove]
        cluster_metric_sum[c_keep] += cluster_metric_sum[c_remove]
        cluster_size[c_keep] += cluster_size[c_remove]
        del cluster_members[c_remove]
        del cluster_metric_sum[c_remove]
        del cluster_size[c_remove]
        n_clusters -= 1

    # Renumber clusters 0..n-1
    old_ids = sorted(cluster_members.keys())
    remap = {old: new for new, old in enumerate(old_ids)}
    labels = {}
    for seed, cid in seed_to_cluster.items():
        labels[seed] = remap.get(cid, 0)

    return labels


# ── Main clustering entry point ────────────────────────────────────

def cluster_regions(region_stats, regions, data, *,
                    method="threshold", metric="area",
                    value_range=None, min_neighbors=2,
                    num_clusters=3):
    """Cluster Voronoi cells using spatial adjacency and cell metrics.

    Parameters
    ----------
    region_stats : list of dict
        Output of ``compute_region_stats()``.
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    method : str
        Clustering method: "threshold", "dbscan", or "agglomerative".
    metric : str
        Cell metric: "area", "density", "compactness", or "vertices".
    value_range : tuple of (float, float) or None
        For threshold method: (min, max) inclusive range.
        If None, defaults to [mean - 1*std, mean + 1*std].
    min_neighbors : int
        For DBSCAN: minimum adjacency degree to be a core cell.
    num_clusters : int
        For agglomerative: target number of clusters.

    Returns
    -------
    ClusterResult
        Container with cluster assignments, summaries, and metadata.

    Raises
    ------
    ValueError
        If method or metric is unrecognized.
    """
    if method not in ("threshold", "dbscan", "agglomerative"):
        raise ValueError("Unknown clustering method: %s" % method)
    if metric not in ("area", "density", "compactness", "vertices"):
        raise ValueError("Unknown metric: %s" % metric)

    stats_lookup = _build_stats_lookup(region_stats)
    adjacency = _build_adjacency(regions, data)

    # Normalize adjacency keys to tuples
    norm_adj = {}
    for seed, neighbors in adjacency.items():
        key = tuple(seed) if not isinstance(seed, tuple) else seed
        norm_adj[key] = [
            tuple(n) if not isinstance(n, tuple) else n
            for n in neighbors
        ]

    seeds = [tuple(s) if not isinstance(s, tuple) else s
             for s in sorted(regions.keys())]

    # Auto-compute value_range for threshold if not given
    if method == "threshold" and value_range is None:
        values = [_metric_value(stats_lookup[s], metric)
                  for s in seeds if s in stats_lookup]
        if values:
            mean_v = sum(values) / len(values)
            std_v = math.sqrt(
                sum((v - mean_v) ** 2 for v in values) / len(values)
            ) if len(values) > 1 else 0.0
            value_range = (mean_v - std_v, mean_v + std_v)
        else:
            value_range = (0.0, float("inf"))

    # Run clustering
    if method == "threshold":
        labels = _cluster_threshold(
            seeds, norm_adj, stats_lookup, metric, value_range)
    elif method == "dbscan":
        labels = _cluster_dbscan(
            seeds, norm_adj, stats_lookup, metric, min_neighbors)
    elif method == "agglomerative":
        labels = _cluster_agglomerative(
            seeds, norm_adj, stats_lookup, metric, num_clusters)
    else:
        labels = {s: -1 for s in seeds}

    # Build cluster summaries
    cluster_groups = {}  # cluster_id -> list of seeds
    num_noise = 0
    for seed, label in labels.items():
        if label == -1:
            num_noise += 1
            continue
        cluster_groups.setdefault(label, []).append(seed)

    clusters = []
    for cid in sorted(cluster_groups.keys()):
        members = cluster_groups[cid]
        areas = []
        compactnesses = []
        cx_sum = 0.0
        cy_sum = 0.0
        total_area = 0.0
        for seed in members:
            stat = stats_lookup.get(seed, {})
            a = stat.get("area", 0.0)
            areas.append(a)
            total_area += a
            compactnesses.append(stat.get("compactness", 0.0))
            cx_sum += stat.get("centroid_x", seed[0])
            cy_sum += stat.get("centroid_y", seed[1])

        n = len(members)
        clusters.append({
            "cluster_id": cid,
            "size": n,
            "seeds": [(s[0], s[1]) for s in members],
            "mean_area": sum(areas) / n if n else 0.0,
            "total_area": total_area,
            "min_area": min(areas) if areas else 0.0,
            "max_area": max(areas) if areas else 0.0,
            "mean_compactness": (sum(compactnesses) / n if n else 0.0),
            "centroid_x": round(cx_sum / n, 4) if n else 0.0,
            "centroid_y": round(cy_sum / n, 4) if n else 0.0,
        })

    params = {"method": method, "metric": metric}
    if method == "threshold":
        params["value_range"] = list(value_range) if value_range else None
    elif method == "dbscan":
        params["min_neighbors"] = min_neighbors
    elif method == "agglomerative":
        params["num_clusters"] = num_clusters

    return ClusterResult(
        method=method,
        metric=metric,
        num_clusters=len(clusters),
        num_noise=num_noise,
        clusters=clusters,
        labels={"%s,%s" % (k[0], k[1]): v for k, v in labels.items()},
        params=params,
    )


# ── Export: JSON ────────────────────────────────────────────────────

def export_cluster_json(result, output_path):
    """Export clustering results to a JSON file.

    Parameters
    ----------
    result : ClusterResult
        Output of ``cluster_regions()``.
    output_path : str
        Path to write the JSON file.
    """
    safe_path = vormap.validate_output_path(
        output_path, allow_absolute=True)

    out = {
        "method": result.method,
        "metric": result.metric,
        "num_clusters": result.num_clusters,
        "num_noise": result.num_noise,
        "params": result.params,
        "clusters": result.clusters,
        "labels": result.labels,
    }
    with open(safe_path, "w") as f:
        json.dump(out, f, indent=2)


# ── Export: SVG ─────────────────────────────────────────────────────

# Cluster colors — distinct palette for up to 12 clusters, then
# falls back to HSL-based generation.
_CLUSTER_PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#bab0ac", "#86bcb6", "#8cd17d",
]


def _cluster_color(cluster_id, total_clusters):
    """Return an SVG fill color for a cluster."""
    if cluster_id < 0:
        return "#dddddd"  # noise
    if cluster_id < len(_CLUSTER_PALETTE):
        return _CLUSTER_PALETTE[cluster_id]
    # Generate via HSL
    hue = (cluster_id * 137.508) % 360  # golden angle
    return "hsl(%d, 65%%, 55%%)" % int(hue)


def export_cluster_svg(result, regions, data, output_path, *,
                       width=800, height=600,
                       show_labels=False,
                       title=None):
    """Export clustering results as an SVG visualization.

    Each cluster is rendered in a distinct color.  Noise cells
    (DBSCAN label -1) are rendered in gray.

    Parameters
    ----------
    result : ClusterResult
        Output of ``cluster_regions()``.
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    output_path : str
        Path to write the SVG file.
    width : int
        SVG canvas width (default: 800).
    height : int
        SVG canvas height (default: 600).
    show_labels : bool
        If True, label each cell with its cluster ID.
    title : str or None
        Optional diagram title.
    """
    safe_path = vormap.validate_output_path(
        output_path, allow_absolute=True)

    # Compute coordinate transform (data space → SVG space)
    all_xs = [s[0] for s in data]
    all_ys = [s[1] for s in data]
    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)
    data_w = max_x - min_x or 1.0
    data_h = max_y - min_y or 1.0
    margin = 40
    draw_w = width - 2 * margin
    draw_h = height - 2 * margin
    scale_x = draw_w / data_w
    scale_y = draw_h / data_h
    scale = min(scale_x, scale_y)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        # Flip Y axis (SVG Y increases downward)
        return margin + (max_y - y) * scale

    # Build seed -> cluster_id lookup from result.labels
    label_lookup = {}
    for key_str, cid in result.labels.items():
        parts = key_str.split(",")
        if len(parts) == 2:
            try:
                seed = (float(parts[0]), float(parts[1]))
                label_lookup[seed] = cid
            except ValueError:
                pass

    # Create SVG
    svg_attrs = {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    }
    svg = ET.Element("svg", svg_attrs)

    # Background
    ET.SubElement(svg, "rect", {
        "width": str(width), "height": str(height),
        "fill": "white",
    })

    # Title
    if title:
        title_el = ET.SubElement(svg, "text", {
            "x": str(width // 2), "y": "20",
            "text-anchor": "middle",
            "font-family": "Arial, sans-serif",
            "font-size": "14", "font-weight": "bold",
            "fill": "#333",
        })
        title_el.text = title

    # Draw regions colored by cluster
    for seed, verts in regions.items():
        seed_t = tuple(seed) if not isinstance(seed, tuple) else seed
        cid = label_lookup.get(seed_t, -1)
        color = _cluster_color(cid, result.num_clusters)

        points_str = " ".join(
            "%.1f,%.1f" % (tx(v[0]), ty(v[1])) for v in verts
        )
        ET.SubElement(svg, "polygon", {
            "points": points_str,
            "fill": color,
            "stroke": "#333",
            "stroke-width": "1",
            "opacity": "0.75",
        })

        if show_labels:
            cx = sum(v[0] for v in verts) / len(verts)
            cy = sum(v[1] for v in verts) / len(verts)
            label_el = ET.SubElement(svg, "text", {
                "x": "%.1f" % tx(cx),
                "y": "%.1f" % ty(cy),
                "text-anchor": "middle",
                "dominant-baseline": "central",
                "font-family": "Arial, sans-serif",
                "font-size": "10",
                "fill": "#000",
            })
            label_el.text = str(cid)

    # Draw seed points
    for pt in data:
        ET.SubElement(svg, "circle", {
            "cx": "%.1f" % tx(pt[0]),
            "cy": "%.1f" % ty(pt[1]),
            "r": "3",
            "fill": "#000",
        })

    # Legend
    legend_y = height - 20
    legend_x = margin
    for cid in range(result.num_clusters):
        color = _cluster_color(cid, result.num_clusters)
        ET.SubElement(svg, "rect", {
            "x": str(legend_x), "y": str(legend_y - 10),
            "width": "12", "height": "12",
            "fill": color, "stroke": "#333", "stroke-width": "0.5",
        })
        lbl = ET.SubElement(svg, "text", {
            "x": str(legend_x + 16), "y": str(legend_y),
            "font-family": "Arial, sans-serif",
            "font-size": "10", "fill": "#333",
        })
        cluster_info = next(
            (c for c in result.clusters if c["cluster_id"] == cid), None)
        size = cluster_info["size"] if cluster_info else 0
        lbl.text = "C%d (%d)" % (cid, size)
        legend_x += 70

    if result.num_noise > 0:
        ET.SubElement(svg, "rect", {
            "x": str(legend_x), "y": str(legend_y - 10),
            "width": "12", "height": "12",
            "fill": "#dddddd", "stroke": "#333", "stroke-width": "0.5",
        })
        noise_lbl = ET.SubElement(svg, "text", {
            "x": str(legend_x + 16), "y": str(legend_y),
            "font-family": "Arial, sans-serif",
            "font-size": "10", "fill": "#999",
        })
        noise_lbl.text = "noise (%d)" % result.num_noise
        legend_x += 80

    # Write SVG
    tree = ET.ElementTree(svg)
    tree.write(safe_path, xml_declaration=True, encoding="unicode")


# ── Text formatting ─────────────────────────────────────────────────

def format_cluster_table(result):
    """Format clustering results as a human-readable text table.

    Parameters
    ----------
    result : ClusterResult
        Output of ``cluster_regions()``.

    Returns
    -------
    str
        Formatted table string.
    """
    lines = []
    lines.append("Spatial Clustering Report")
    lines.append("=" * 60)
    lines.append("Method: %s" % result.method)
    lines.append("Metric: %s" % result.metric)
    lines.append("Clusters: %d" % result.num_clusters)
    if result.num_noise > 0:
        lines.append("Noise cells: %d" % result.num_noise)
    lines.append("")

    if result.params:
        lines.append("Parameters:")
        for k, v in result.params.items():
            if k not in ("method", "metric"):
                lines.append("  %s: %s" % (k, v))
        lines.append("")

    # Cluster table header
    header = "%-10s  %5s  %12s  %12s  %12s  %10s" % (
        "Cluster", "Size", "Mean Area", "Total Area", "Compactness", "Centroid")
    lines.append(header)
    lines.append("-" * len(header))

    for c in result.clusters:
        lines.append("%-10s  %5d  %12.2f  %12.2f  %12.4f  (%7.1f, %7.1f)" % (
            "C%d" % c["cluster_id"],
            c["size"],
            c["mean_area"],
            c["total_area"],
            c["mean_compactness"],
            c["centroid_x"],
            c["centroid_y"],
        ))

    return "\n".join(lines)


# ── Convenience pipeline ────────────────────────────────────────────

def generate_clusters(datafile, output_path=None, *,
                      method="threshold", metric="area",
                      value_range=None, min_neighbors=2,
                      num_clusters=3, fmt="table"):
    """One-call convenience function: load data, cluster, optionally export.

    Parameters
    ----------
    datafile : str
        Path to point data file.
    output_path : str or None
        If given, export to this path (format inferred from extension:
        .json, .svg, or text table to stdout).
    method : str
        Clustering method.
    metric : str
        Cell metric.
    value_range : tuple or None
        For threshold method.
    min_neighbors : int
        For DBSCAN method.
    num_clusters : int
        For agglomerative method.
    fmt : str
        Output format when no output_path: "table" or "json".

    Returns
    -------
    ClusterResult
    """
    data = vormap.load_data(datafile)
    regions = compute_regions(data)
    region_stats = compute_region_stats(regions, data)

    result = cluster_regions(
        region_stats, regions, data,
        method=method, metric=metric,
        value_range=value_range,
        min_neighbors=min_neighbors,
        num_clusters=num_clusters,
    )

    if output_path:
        if output_path.endswith(".json"):
            export_cluster_json(result, output_path)
        elif output_path.endswith(".svg"):
            export_cluster_svg(result, regions, data, output_path,
                               title="Spatial Clusters — %s (%s, %s)"
                               % (datafile, method, metric))
        else:
            # Text table to file
            with open(output_path, "w") as f:
                f.write(format_cluster_table(result))
    else:
        if fmt == "json":
            print(json.dumps({
                "method": result.method,
                "metric": result.metric,
                "num_clusters": result.num_clusters,
                "num_noise": result.num_noise,
                "clusters": result.clusters,
            }, indent=2))
        else:
            print(format_cluster_table(result))

    return result
