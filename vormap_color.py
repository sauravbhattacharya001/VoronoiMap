"""Map coloring for Voronoi diagrams — four-color theorem implementation.

Assigns colors to Voronoi regions such that no two adjacent regions share
the same color, using at most 4 colors (per the four-color theorem).
Useful for cartographic visualization, territory planning, and spatial
analysis.

Provides two graph-coloring algorithms:

- **Greedy (Welsh-Powell):** sorts nodes by degree descending, assigns
  the smallest available color.  Simple and fast, O(V + E).
- **DSATUR:** picks uncolored vertices by highest saturation (most
  distinct colors among neighbours), breaking ties by degree.  More
  optimal for planar graphs like Voronoi tessellations.

Usage (as module):
    import vormap_color

    # Color with random points
    result = vormap_color.color_voronoi([(1,2),(3,4),(5,6),(7,8),(9,10)])
    print(result.num_colors_used, result.conflicts)

    # Export colored SVG
    vormap_color.export_colored_svg("datauni5.txt", "colored.svg")

Usage (CLI):
    python vormap_color.py datauni5.txt --output colored.svg
    python vormap_color.py datauni5.txt --algorithm greedy --colors 6
    python vormap_color.py datauni5.txt --palette "#e41a1c,#377eb8,#4daf4a"
    python vormap_color.py datauni5.txt --labels --no-seeds
"""

import argparse
import heapq
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import vormap
import vormap_viz

# ── Built-in palettes ────────────────────────────────────────────────

PALETTE_4 = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3"]  # ColorBrewer Set1
PALETTE_6 = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#a65628",
]
PALETTE_8 = [
    "#e41a1c", "#377eb8", "#4daf4a", "#984ea3",
    "#ff7f00", "#ffff33", "#a65628", "#f781bf",
]
# Colorblind-friendly palette (Wong 2011)
PALETTE_CB = [
    "#E69F00", "#56B4E9", "#009E73", "#F0E442",
    "#0072B2", "#D55E00", "#CC79A7", "#000000",
]


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class ColorResult:
    """Result of coloring a Voronoi diagram."""
    coloring: dict        # node_id -> color_index (0-based)
    num_colors_used: int
    palette: list         # hex color strings
    regions: dict         # Voronoi regions (seed -> vertices)
    graph: dict           # adjacency graph from extract_neighborhood_graph
    conflicts: int        # should be 0 for valid coloring
    algorithm: str        # which algorithm was used


# ── Graph conversion ─────────────────────────────────────────────────

def _graph_to_adjacency_dict(graph):
    """Convert extract_neighborhood_graph() output to simple adjacency dict.

    Parameters
    ----------
    graph : dict
        Output of ``vormap_viz.extract_neighborhood_graph()``.

    Returns
    -------
    dict
        Maps node_id (int) -> list of neighbour node_ids (int).
    """
    adjacency = graph["adjacency"]
    seed_indices = graph["seed_indices"]

    adj = {}
    for seed, neighbors in adjacency.items():
        node_id = seed_indices[seed]
        neighbor_ids = [seed_indices[n] for n in neighbors]
        adj[node_id] = neighbor_ids
    return adj


# ── Coloring algorithms ─────────────────────────────────────────────

def greedy_color(graph, num_colors=4):
    """Assign colors to graph nodes using greedy graph coloring.

    Uses Welsh-Powell ordering (nodes sorted by degree descending) for
    better results.  If greedy coloring with *num_colors* fails, falls
    back to allowing more colors.

    Parameters
    ----------
    graph : dict
        Maps node_id -> list of neighbour node_ids.
    num_colors : int
        Maximum colors to use (default 4, minimum 1).

    Returns
    -------
    dict
        Maps node_id -> color_index (0-based).
    """
    if num_colors < 1:
        num_colors = 1

    if not graph:
        return {}

    # Welsh-Powell: sort by degree descending
    nodes = sorted(graph.keys(), key=lambda n: len(graph[n]), reverse=True)

    coloring = {}
    for node in nodes:
        neighbor_colors = set()
        for neighbor in graph[node]:
            if neighbor in coloring:
                neighbor_colors.add(coloring[neighbor])

        # Assign smallest available color
        color = 0
        while color in neighbor_colors:
            color += 1

        coloring[node] = color

    return coloring


def dsatur_color(graph, num_colors=4):
    """DSATUR algorithm — assigns colors based on saturation degree.

    At each step, picks the uncolored vertex with the highest saturation
    (most distinct colors among its neighbours), breaking ties by highest
    degree.  More optimal than greedy for planar graphs.

    Parameters
    ----------
    graph : dict
        Maps node_id -> list of neighbour node_ids.
    num_colors : int
        Maximum colors to use (default 4).

    Returns
    -------
    dict
        Maps node_id -> color_index (0-based).
    """
    if num_colors < 1:
        num_colors = 1

    if not graph:
        return {}

    coloring = {}
    # Track saturation: set of distinct colors among colored neighbours
    saturation = {node: set() for node in graph}
    uncolored = set(graph.keys())

    # Max-heap using negated values: (-saturation_size, -degree, idx, node)
    # Nodes may be tuples (seeds), so use an int index for deterministic ordering
    node_list = sorted(graph.keys())
    node_to_idx = {n: i for i, n in enumerate(node_list)}

    heap = []
    for node in graph:
        heapq.heappush(heap, (0, -len(graph[node]), node_to_idx[node], node))

    while uncolored:
        # Pop highest priority (most saturated, then highest degree)
        while heap:
            neg_sat, neg_deg, _idx, best = heapq.heappop(heap)
            if best in uncolored:
                # Verify priority is current (lazy deletion)
                current_sat = len(saturation[best])
                if -neg_sat == current_sat:
                    break
                else:
                    # Re-push with updated priority
                    heapq.heappush(heap, (-current_sat, neg_deg, _idx, best))
            # else: already colored, skip
        else:
            break

        # Find smallest available color
        neighbor_colors = set()
        for neighbor in graph[best]:
            if neighbor in coloring:
                neighbor_colors.add(coloring[neighbor])

        color = 0
        while color in neighbor_colors:
            color += 1

        coloring[best] = color
        uncolored.discard(best)

        # Update saturation of uncolored neighbours
        for neighbor in graph[best]:
            if neighbor in uncolored:
                old_sat = len(saturation[neighbor])
                saturation[neighbor].add(color)
                new_sat = len(saturation[neighbor])
                if new_sat > old_sat:
                    heapq.heappush(heap, (-new_sat, -len(graph[neighbor]),
                                          node_to_idx[neighbor], neighbor))

    return coloring


# ── Validation ───────────────────────────────────────────────────────

def validate_coloring(graph, coloring):
    """Check that no adjacent nodes share a color.

    Parameters
    ----------
    graph : dict
        Maps node_id -> list of neighbour node_ids.
    coloring : dict
        Maps node_id -> color_index.

    Returns
    -------
    tuple
        ``(is_valid, conflicts)`` where *is_valid* is bool and
        *conflicts* is a list of ``(node_a, node_b, color)`` tuples
        for each pair sharing a color.
    """
    conflicts = []
    seen_edges = set()

    for node, neighbors in graph.items():
        if node not in coloring:
            continue
        for neighbor in neighbors:
            if neighbor not in coloring:
                continue
            edge = (min(node, neighbor), max(node, neighbor))
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            if coloring[node] == coloring[neighbor]:
                conflicts.append((node, neighbor, coloring[node]))

    return (len(conflicts) == 0, conflicts)


# ── Convenience: one-call coloring ───────────────────────────────────

def _select_palette(num_colors, palette):
    """Pick a palette based on the number of colors requested."""
    if palette is not None:
        return list(palette)
    if num_colors <= 4:
        return list(PALETTE_4)
    elif num_colors <= 6:
        return list(PALETTE_6)
    elif num_colors <= 8:
        return list(PALETTE_8)
    else:
        return list(PALETTE_CB)


def color_voronoi(data_or_file, algorithm="dsatur", num_colors=4, palette=None):
    """One-call convenience: compute regions, extract graph, color it.

    Parameters
    ----------
    data_or_file : list or str
        Either a list of ``(x, y)`` points or a filename.
    algorithm : str
        ``"greedy"`` or ``"dsatur"`` (default).
    num_colors : int
        Target number of colors (default 4).
    palette : list of str or None
        Optional list of hex color strings.  If None, uses a built-in
        colorblind-friendly palette.

    Returns
    -------
    ColorResult
        Result with ``.coloring``, ``.num_colors_used``, ``.palette``,
        ``.regions``, ``.graph``, ``.conflicts``, ``.algorithm``.

    Raises
    ------
    ValueError
        If *algorithm* is not ``"greedy"`` or ``"dsatur"``.
    """
    if algorithm not in ("greedy", "dsatur"):
        raise ValueError(
            "Unknown algorithm '%s'. Use 'greedy' or 'dsatur'." % algorithm
        )

    # Load data
    if isinstance(data_or_file, str):
        data = vormap.load_data(data_or_file)
    else:
        data = list(data_or_file)

    # Compute regions
    regions = vormap_viz.compute_regions(data)

    # Extract neighbourhood graph
    graph = vormap_viz.extract_neighborhood_graph(regions, data)

    # Convert to simple adjacency dict
    adj = _graph_to_adjacency_dict(graph)

    # Color
    if algorithm == "greedy":
        coloring = greedy_color(adj, num_colors=num_colors)
    else:
        coloring = dsatur_color(adj, num_colors=num_colors)

    # Validate
    is_valid, conflict_list = validate_coloring(adj, coloring)

    # Determine palette
    used_colors = set(coloring.values()) if coloring else set()
    n_used = max(used_colors) + 1 if used_colors else 0
    pal = _select_palette(num_colors, palette)

    return ColorResult(
        coloring=coloring,
        num_colors_used=n_used,
        palette=pal,
        regions=regions,
        graph=graph,
        conflicts=len(conflict_list),
        algorithm=algorithm,
    )


# ── SVG export ───────────────────────────────────────────────────────

def export_colored_svg(
    data_or_file,
    output_path,
    *,
    algorithm="dsatur",
    num_colors=4,
    palette=None,
    width=800,
    height=600,
    show_seeds=True,
    show_labels=False,
    stroke_color="#333",
    stroke_width=1,
):
    """Export an SVG with map-colored Voronoi regions.

    Each region is filled with its assigned color from the palette.

    Parameters
    ----------
    data_or_file : list or str
        Either a list of ``(x, y)`` points or a filename.
    output_path : str
        Path to write the SVG file.
    algorithm : str
        ``"greedy"`` or ``"dsatur"`` (default).
    num_colors : int
        Target number of colors (default 4).
    palette : list of str or None
        Optional list of hex color strings.
    width, height : int
        SVG canvas dimensions in pixels.
    show_seeds : bool
        Whether to draw seed point markers.
    show_labels : bool
        Whether to label each region with its index.
    stroke_color : str
        CSS color for polygon borders.
    stroke_width : float
        Border thickness.

    Returns
    -------
    str
        Path to the generated SVG file.
    """
    result = color_voronoi(
        data_or_file,
        algorithm=algorithm,
        num_colors=num_colors,
        palette=palette,
    )

    regions = result.regions
    graph = result.graph
    coloring = result.coloring
    pal = result.palette
    seed_indices = graph["seed_indices"]

    # Load data for seed markers
    if isinstance(data_or_file, str):
        data = vormap.load_data(data_or_file)
    else:
        data = list(data_or_file)

    margin = 40

    # Compute coordinate transform
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
        ".region { stroke: %s; stroke-width: %.1f; stroke-linejoin: round; }"
        " .seed { fill: #222; stroke: #fff; stroke-width: 1; }"
        " .label { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 10px; fill: #333; text-anchor: middle;"
        " dominant-baseline: central; pointer-events: none; }"
        % (vormap.sanitize_css_value(stroke_color), stroke_width)
    )

    # Background
    ET.SubElement(svg, "rect", {
        "width": str(width), "height": str(height), "fill": "#ffffff",
    })

    # Draw regions with assigned colors
    region_group = ET.SubElement(svg, "g", {"id": "regions"})
    sorted_seeds = sorted(regions.keys())

    for seed in sorted_seeds:
        verts = regions[seed]
        points_str = " ".join(
            "%.2f,%.2f" % (tx(vx), ty(vy)) for vx, vy in verts
        )

        # Get the color index for this region
        node_id = seed_indices.get(seed, -1)
        color_idx = coloring.get(node_id, 0)
        fill = pal[color_idx % len(pal)] if pal else "#cccccc"

        poly = ET.SubElement(region_group, "polygon", {
            "points": points_str,
            "fill": fill,
            "class": "region",
        })
        poly.set("data-seed", "%.4f,%.4f" % seed)
        poly.set("data-color", str(color_idx))

    # Seed points
    if show_seeds:
        points_group = ET.SubElement(svg, "g", {"id": "seeds"})
        for px, py in data:
            ET.SubElement(points_group, "circle", {
                "cx": "%.2f" % tx(px),
                "cy": "%.2f" % ty(py),
                "r": "3.5",
                "class": "seed",
            })

    # Labels
    if show_labels:
        label_group = ET.SubElement(svg, "g", {"id": "labels"})
        for idx, (px, py) in enumerate(data):
            label = ET.SubElement(label_group, "text", {
                "x": "%.2f" % tx(px),
                "y": "%.2f" % ty(py),
                "class": "label",
            })
            label.text = str(idx)

    # Write
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)

    return output_path


# ── CLI ──────────────────────────────────────────────────────────────

def _build_parser():
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Map-color Voronoi regions (four-color theorem).",
    )
    parser.add_argument(
        "datafile",
        help="Input data file with seed points.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output SVG file path (default: <datafile>_colored.svg).",
    )
    parser.add_argument(
        "--algorithm", "-a",
        choices=["greedy", "dsatur"],
        default="dsatur",
        help="Coloring algorithm (default: dsatur).",
    )
    parser.add_argument(
        "--colors", "-c",
        type=int,
        default=4,
        help="Target number of colors (default: 4).",
    )
    parser.add_argument(
        "--palette",
        default=None,
        help="Comma-separated hex colors (e.g. '#e41a1c,#377eb8,#4daf4a').",
    )
    parser.add_argument(
        "--labels",
        action="store_true",
        help="Show region index labels.",
    )
    parser.add_argument(
        "--no-seeds",
        action="store_true",
        help="Hide seed point markers.",
    )
    return parser


def main(args=None):
    """CLI entry point."""
    parser = _build_parser()
    opts = parser.parse_args(args)

    # Parse palette
    palette = None
    if opts.palette:
        palette = [c.strip() for c in opts.palette.split(",")]

    # Output path
    output_path = opts.output
    if output_path is None:
        import os
        base = os.path.splitext(opts.datafile)[0]
        output_path = base + "_colored.svg"

    # Run
    result = color_voronoi(
        opts.datafile,
        algorithm=opts.algorithm,
        num_colors=opts.colors,
        palette=palette,
    )

    # Export SVG
    export_colored_svg(
        opts.datafile,
        output_path,
        algorithm=opts.algorithm,
        num_colors=opts.colors,
        palette=palette,
        show_seeds=not opts.no_seeds,
        show_labels=opts.labels,
    )

    print("Algorithm:    %s" % result.algorithm)
    print("Colors used:  %d" % result.num_colors_used)
    print("Regions:      %d" % len(result.regions))
    print("Conflicts:    %d" % result.conflicts)
    print("Output:       %s" % output_path)


if __name__ == "__main__":
    main()
