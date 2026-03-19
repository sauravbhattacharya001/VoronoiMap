"""Voronoi-based maze generator.

Generates solvable mazes from Voronoi diagram cell adjacency graphs.
Each Voronoi cell becomes a maze room and shared edges become walls that
are selectively removed to carve passages, producing organic-looking
mazes with irregular, natural cell shapes.

Algorithms
----------
- **dfs** (recursive backtracker): deep, winding passages with long corridors.
- **kruskal**: balanced maze with many short dead-ends.
- **prim**: organic growth from a starting cell outward.

Functions
---------
- ``generate_maze``        — full pipeline: points → Voronoi → maze → export.
- ``build_maze_graph``     — extract adjacency and carve maze passages.
- ``solve_maze``           — BFS shortest-path solver between two cells.
- ``export_maze_svg``      — SVG visualisation with optional solution path.
- ``export_maze_json``     — JSON export of maze structure and solution.
- ``format_maze_report``   — human-readable text summary.

Usage
-----
CLI::

    python vormap_maze.py 80 --algorithm dfs --svg maze.svg
    python vormap_maze.py 200 --algorithm kruskal --solve --svg maze.svg
    python vormap_maze.py 150 --algorithm prim --start 0 --goal 149 --svg maze.svg --json maze.json
    python vormap_maze.py points.txt --algorithm dfs --solve --svg maze.svg

"""

import argparse
import json
import math
import os
import random
import sys
from collections import deque

import vormap
import vormap_viz

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ── Adjacency extraction ────────────────────────────────────────────

def _extract_adjacency(regions_list, tol=0.5):
    """Build cell adjacency from Voronoi region polygons.

    Parameters
    ----------
    regions_list : list of list of tuple
        Voronoi region polygons (indexed list).
    tol : float
        Tolerance for matching shared edge vertices.

    Returns
    -------
    adj : dict[int, set[int]]
        Adjacency mapping: cell index → set of neighbour indices.
    """
    # Round vertices to tolerance grid for matching
    def _round(pt):
        return (round(pt[0] / tol) * tol, round(pt[1] / tol) * tol)

    edge_to_cells = {}
    for idx, poly in enumerate(regions_list):
        n = len(poly)
        for i in range(n):
            p1 = _round(poly[i])
            p2 = _round(poly[(i + 1) % n])
            edge = tuple(sorted([p1, p2]))
            edge_to_cells.setdefault(edge, set()).add(idx)

    adj = {}
    for cells in edge_to_cells.values():
        cells = list(cells)
        if len(cells) == 2:
            a, b = cells[0], cells[1]
            if a != b:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)

    # Ensure every cell appears even if isolated
    for idx in range(len(regions_list)):
        adj.setdefault(idx, set())

    return adj


def _centroid(poly):
    """Compute centroid of a polygon given as list of (x, y) tuples."""
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    return (cx, cy)


# ── Maze carving algorithms ─────────────────────────────────────────

def _maze_dfs(adj, start, rng):
    """Recursive-backtracker (DFS) maze generation.

    Returns set of frozenset pairs representing carved passages.
    """
    passages = set()
    visited = {start}
    stack = [start]

    while stack:
        current = stack[-1]
        neighbours = [n for n in adj[current] if n not in visited]
        if neighbours:
            nxt = rng.choice(neighbours)
            passages.add(frozenset((current, nxt)))
            visited.add(nxt)
            stack.append(nxt)
        else:
            stack.pop()

    return passages


def _maze_kruskal(adj, rng):
    """Randomized Kruskal's algorithm for maze generation."""
    edges = []
    seen = set()
    for cell, neighbours in adj.items():
        for n in neighbours:
            key = frozenset((cell, n))
            if key not in seen:
                edges.append((cell, n))
                seen.add(key)

    rng.shuffle(edges)

    parent = {}
    rank = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx == ry:
            return False
        if rank.get(rx, 0) < rank.get(ry, 0):
            rx, ry = ry, rx
        parent[ry] = rx
        if rank.get(rx, 0) == rank.get(ry, 0):
            rank[rx] = rank.get(rx, 0) + 1
        return True

    passages = set()
    for a, b in edges:
        if union(a, b):
            passages.add(frozenset((a, b)))

    return passages


def _maze_prim(adj, start, rng):
    """Randomized Prim's algorithm for maze generation."""
    visited = {start}
    walls = []
    for n in adj[start]:
        walls.append((start, n))

    passages = set()
    while walls:
        idx = rng.randrange(len(walls))
        a, b = walls[idx]
        walls[idx] = walls[-1]
        walls.pop()

        if b in visited:
            continue

        visited.add(b)
        passages.add(frozenset((a, b)))
        for n in adj[b]:
            if n not in visited:
                walls.append((b, n))

    return passages


# ── Maze builder ─────────────────────────────────────────────────────

def build_maze_graph(regions, algorithm="dfs", start=0, seed=None):
    """Build a maze from Voronoi regions using cell adjacency.

    Parameters
    ----------
    regions : list of list of tuple
        Voronoi region polygons.
    algorithm : str
        One of ``'dfs'``, ``'kruskal'``, ``'prim'``.
    start : int
        Starting cell index (used by dfs and prim).
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    dict
        ``'adj'``: full adjacency, ``'passages'``: set of frozenset pairs,
        ``'walls'``: set of frozenset pairs (edges not in passages),
        ``'cell_count'``: int.
    """
    adj = _extract_adjacency(regions)
    rng = random.Random(seed)
    n_cells = len(regions)

    start = max(0, min(start, n_cells - 1))

    if algorithm == "kruskal":
        passages = _maze_kruskal(adj, rng)
    elif algorithm == "prim":
        passages = _maze_prim(adj, start, rng)
    else:
        passages = _maze_dfs(adj, start, rng)

    # Walls = adjacency edges NOT carved as passages
    all_edges = set()
    for cell, neighbours in adj.items():
        for n in neighbours:
            all_edges.add(frozenset((cell, n)))
    walls = all_edges - passages

    return {
        "adj": adj,
        "passages": passages,
        "walls": walls,
        "cell_count": n_cells,
    }


# ── Solver ───────────────────────────────────────────────────────────

def solve_maze(maze, start, goal):
    """BFS shortest-path solver through carved passages.

    Parameters
    ----------
    maze : dict
        Output from ``build_maze_graph``.
    start : int
        Start cell index.
    goal : int
        Goal cell index.

    Returns
    -------
    list of int or None
        Cell indices from start to goal, or None if unsolvable.
    """
    passages = maze["passages"]

    # Build passage adjacency
    pass_adj = {}
    for edge in passages:
        a, b = tuple(edge)
        pass_adj.setdefault(a, set()).add(b)
        pass_adj.setdefault(b, set()).add(a)

    if start not in pass_adj:
        return None

    visited = {start}
    queue = deque([(start, [start])])
    while queue:
        current, path = queue.popleft()
        if current == goal:
            return path
        for n in pass_adj.get(current, []):
            if n not in visited:
                visited.add(n)
                queue.append((n, path + [n]))

    return None


# ── SVG export ───────────────────────────────────────────────────────

def export_maze_svg(regions, maze, solution=None, width=800, height=800,
                    bg_color="#1a1a2e", wall_color="#e94560",
                    passage_color="#16213e", solution_color="#0f3460",
                    cell_fill="#16213e", start_color="#00b894",
                    goal_color="#fdcb6e", stroke_width=2.0):
    """Generate SVG visualisation of the Voronoi maze.

    Parameters
    ----------
    regions : list of list of tuple
        Voronoi region polygons.
    maze : dict
        Output from ``build_maze_graph``.
    solution : list of int or None
        Solution path (cell indices) to highlight.
    width, height : int
        SVG dimensions.

    Returns
    -------
    str
        SVG markup.
    """
    # Compute bounding box
    all_pts = [p for poly in regions for p in poly]
    min_x = min(p[0] for p in all_pts)
    max_x = max(p[0] for p in all_pts)
    min_y = min(p[1] for p in all_pts)
    max_y = max(p[1] for p in all_pts)
    pad = max((max_x - min_x), (max_y - min_y)) * 0.02
    min_x -= pad
    max_x += pad
    min_y -= pad
    max_y += pad

    def tx(x):
        return (x - min_x) / (max_x - min_x) * width

    def ty(y):
        return (y - min_y) / (max_y - min_y) * height

    solution_set = set(solution) if solution else set()
    solution_edges = set()
    if solution:
        for i in range(len(solution) - 1):
            solution_edges.add(frozenset((solution[i], solution[i + 1])))

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
                 f' viewBox="0 0 {width} {height}">')
    lines.append(f'<rect width="{width}" height="{height}" fill="{bg_color}"/>')

    # Draw cell fills
    for idx, poly in enumerate(regions):
        pts_str = " ".join(f"{tx(p[0]):.1f},{ty(p[1]):.1f}" for p in poly)
        if solution and idx in solution_set:
            fill = solution_color
        else:
            fill = cell_fill
        lines.append(f'<polygon points="{pts_str}" fill="{fill}" stroke="none"/>')

    # Draw walls (edges NOT in passages)
    edge_midpoints = {}
    for idx, poly in enumerate(regions):
        n = len(poly)
        for i in range(n):
            p1 = poly[i]
            p2 = poly[(i + 1) % n]
            edge_key = tuple(sorted([p1, p2]))
            edge_midpoints.setdefault(edge_key, []).append(idx)

    for edge_key, cells in edge_midpoints.items():
        if len(cells) == 2:
            a, b = cells[0], cells[1]
            pair = frozenset((a, b))
            if pair in maze["walls"]:
                p1, p2 = edge_key
                lines.append(
                    f'<line x1="{tx(p1[0]):.1f}" y1="{ty(p1[1]):.1f}" '
                    f'x2="{tx(p2[0]):.1f}" y2="{ty(p2[1]):.1f}" '
                    f'stroke="{wall_color}" stroke-width="{stroke_width}" stroke-linecap="round"/>'
                )

    # Draw boundary edges (edges with only one cell)
    for edge_key, cells in edge_midpoints.items():
        if len(cells) == 1:
            p1, p2 = edge_key
            lines.append(
                f'<line x1="{tx(p1[0]):.1f}" y1="{ty(p1[1]):.1f}" '
                f'x2="{tx(p2[0]):.1f}" y2="{ty(p2[1]):.1f}" '
                f'stroke="{wall_color}" stroke-width="{stroke_width}" stroke-linecap="round"/>'
            )

    # Draw solution path as line through centroids
    if solution and len(solution) > 1:
        centroids = [_centroid(regions[i]) for i in solution]
        path_d = "M" + " L".join(f"{tx(c[0]):.1f},{ty(c[1]):.1f}" for c in centroids)
        lines.append(
            f'<path d="{path_d}" fill="none" stroke="#ffeaa7" '
            f'stroke-width="{stroke_width * 2}" stroke-linecap="round" '
            f'stroke-linejoin="round" opacity="0.8"/>'
        )

    # Mark start and goal
    if solution:
        sc = _centroid(regions[solution[0]])
        gc = _centroid(regions[solution[-1]])
        r = max(width, height) * 0.015
        lines.append(f'<circle cx="{tx(sc[0]):.1f}" cy="{ty(sc[1]):.1f}" r="{r}" fill="{start_color}" opacity="0.9"/>')
        lines.append(f'<text x="{tx(sc[0]):.1f}" y="{ty(sc[1]) + r * 0.4:.1f}" text-anchor="middle" '
                     f'font-size="{r * 1.2:.0f}" fill="#fff" font-family="sans-serif">S</text>')
        lines.append(f'<circle cx="{tx(gc[0]):.1f}" cy="{ty(gc[1]):.1f}" r="{r}" fill="{goal_color}" opacity="0.9"/>')
        lines.append(f'<text x="{tx(gc[0]):.1f}" y="{ty(gc[1]) + r * 0.4:.1f}" text-anchor="middle" '
                     f'font-size="{r * 1.2:.0f}" fill="#333" font-family="sans-serif">G</text>')

    lines.append('</svg>')
    return "\n".join(lines)


# ── JSON export ──────────────────────────────────────────────────────

def export_maze_json(regions, maze, solution=None):
    """Export maze structure to JSON.

    Returns
    -------
    str
        JSON string.
    """
    centroids = [_centroid(poly) for poly in regions]
    passages_list = [sorted(tuple(e)) for e in maze["passages"]]
    walls_list = [sorted(tuple(e)) for e in maze["walls"]]

    data = {
        "cell_count": maze["cell_count"],
        "passage_count": len(maze["passages"]),
        "wall_count": len(maze["walls"]),
        "centroids": [{"x": round(c[0], 2), "y": round(c[1], 2)} for c in centroids],
        "passages": sorted(passages_list),
        "walls": sorted(walls_list),
    }
    if solution is not None:
        data["solution"] = solution
        data["solution_length"] = len(solution)

    return json.dumps(data, indent=2)


# ── Text report ──────────────────────────────────────────────────────

def format_maze_report(maze, solution=None, algorithm="dfs"):
    """Format a human-readable maze summary.

    Returns
    -------
    str
        Report text.
    """
    lines = []
    lines.append("═" * 50)
    lines.append("  VORONOI MAZE REPORT")
    lines.append("═" * 50)
    lines.append(f"  Algorithm      : {algorithm}")
    lines.append(f"  Cells (rooms)  : {maze['cell_count']}")
    lines.append(f"  Passages       : {len(maze['passages'])}")
    lines.append(f"  Walls          : {len(maze['walls'])}")

    total_edges = len(maze['passages']) + len(maze['walls'])
    if total_edges > 0:
        openness = len(maze['passages']) / total_edges * 100
        lines.append(f"  Openness       : {openness:.1f}%")

    if solution is not None:
        lines.append(f"  Solution steps : {len(solution)}")
        lines.append(f"  Solution path  : {' → '.join(str(s) for s in solution[:20])}"
                     + ("..." if len(solution) > 20 else ""))
    else:
        lines.append("  Solution       : not computed / unsolvable")

    lines.append("═" * 50)
    return "\n".join(lines)


# ── Pipeline ─────────────────────────────────────────────────────────

def generate_maze(n_points=100, algorithm="dfs", start=None, goal=None,
                  solve=True, seed=None, svg_path=None, json_path=None,
                  points=None, width=800, height=800):
    """Full pipeline: generate points → Voronoi → maze → solve → export.

    Parameters
    ----------
    n_points : int
        Number of seed points (ignored if *points* is provided).
    algorithm : str
        ``'dfs'``, ``'kruskal'``, or ``'prim'``.
    start : int or None
        Start cell (default: bottom-left-most cell).
    goal : int or None
        Goal cell (default: top-right-most cell).
    solve : bool
        Whether to solve the maze.
    seed : int or None
        Random seed.
    svg_path : str or None
        Write SVG to this path.
    json_path : str or None
        Write JSON to this path.
    points : list of tuple or None
        Custom seed points; if None, random points are generated.
    width, height : int
        SVG dimensions.

    Returns
    -------
    dict
        ``'maze'``, ``'regions'``, ``'solution'``, ``'report'``,
        ``'svg'`` (if requested), ``'json'`` (if requested).
    """
    rng = random.Random(seed)

    # Generate points if not provided
    if points is None:
        points = [(rng.uniform(50, 1950), rng.uniform(50, 950)) for _ in range(n_points)]

    # Set bounds
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    pad = 20
    vormap.IND_W = min(xs) - pad
    vormap.IND_E = max(xs) + pad
    vormap.IND_S = min(ys) - pad
    vormap.IND_N = max(ys) + pad

    # Generate Voronoi regions via vormap_viz
    regions_dict = vormap_viz.compute_regions(points)
    # Convert dict to indexed list (matching point order)
    seed_list = list(regions_dict.keys())
    regions = [regions_dict[s] for s in seed_list]

    # Build maze
    maze_start = start if start is not None else 0
    maze = build_maze_graph(regions, algorithm=algorithm, start=maze_start, seed=seed)

    # Auto-select start/goal by spatial extremes
    centroids = [_centroid(poly) for poly in regions]
    if start is None:
        # Bottom-left-most
        start = min(range(len(centroids)), key=lambda i: centroids[i][0] + centroids[i][1])
    if goal is None:
        # Top-right-most
        goal = max(range(len(centroids)), key=lambda i: centroids[i][0] + centroids[i][1])

    # Solve
    solution = None
    if solve:
        solution = solve_maze(maze, start, goal)

    # Report
    report = format_maze_report(maze, solution=solution, algorithm=algorithm)

    result = {
        "maze": maze,
        "regions": regions,
        "solution": solution,
        "report": report,
        "start": start,
        "goal": goal,
    }

    # SVG
    svg_str = export_maze_svg(regions, maze, solution=solution, width=width, height=height)
    result["svg"] = svg_str
    if svg_path:
        _safe_write(svg_path, svg_str)

    # JSON
    if json_path:
        json_str = export_maze_json(regions, maze, solution=solution)
        _safe_write(json_path, json_str)
        result["json"] = json_str

    return result


def _safe_write(filepath, content):
    """Write content to file with basic path validation."""
    filepath = vormap._validate_path(filepath, allow_absolute=True, label="Output file")
    with open(filepath, "w") as f:
        f.write(content)


# ── CLI ──────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(
        description="Voronoi Maze Generator — organic mazes from Voronoi diagrams"
    )
    parser.add_argument(
        "input", nargs="?", default=None,
        help="Number of points (int) or path to points file (txt/csv/json). Default: 100"
    )
    parser.add_argument("--algorithm", "-a", choices=["dfs", "kruskal", "prim"],
                        default="dfs", help="Maze generation algorithm (default: dfs)")
    parser.add_argument("--start", type=int, default=None, help="Start cell index")
    parser.add_argument("--goal", type=int, default=None, help="Goal cell index")
    parser.add_argument("--solve", action="store_true", default=False,
                        help="Solve the maze and show solution path")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--svg", default=None, help="Output SVG file path")
    parser.add_argument("--json", default=None, help="Output JSON file path")
    parser.add_argument("--width", type=int, default=800, help="SVG width (default: 800)")
    parser.add_argument("--height", type=int, default=800, help="SVG height (default: 800)")
    args = parser.parse_args()

    # Parse input
    points = None
    n_points = 100
    if args.input is not None:
        try:
            n_points = int(args.input)
        except ValueError:
            # Treat as file path
            data = vormap.load_data(args.input)
            points = [(p.x, p.y) for p in data]
            n_points = len(points)

    result = generate_maze(
        n_points=n_points,
        algorithm=args.algorithm,
        start=args.start,
        goal=args.goal,
        solve=args.solve,
        seed=args.seed,
        svg_path=args.svg,
        json_path=args.json,
        points=points,
        width=args.width,
        height=args.height,
    )

    print(result["report"])

    if args.svg:
        print(f"\n  SVG written to: {args.svg}")
    if args.json:
        print(f"  JSON written to: {args.json}")


if __name__ == "__main__":
    _cli()
