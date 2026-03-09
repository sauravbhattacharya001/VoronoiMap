"""Voronoi-based path planner for obstacle-aware navigation.

Computes maximum-clearance paths through obstacle fields using Voronoi
edges as a navigation roadmap.  Voronoi edges are equidistant from their
nearest obstacle points, making them natural corridors of maximum
clearance for robot or vehicle navigation.

Functions
---------
- ``build_roadmap``       — extract the Voronoi edge graph as a roadmap.
- ``find_path``           — A* shortest/safest path between two points.
- ``compute_path_stats``  — distance, clearance, and safety metrics.
- ``export_path_json``    — export path + roadmap to JSON.
- ``export_path_csv``     — export waypoints to CSV.
- ``export_path_svg``     — SVG visualisation with obstacles, roadmap, path.
- ``format_path_report``  — human-readable text report.
- ``plan_path``           — convenience one-call pipeline.

The planner supports two modes:
  - **shortest**: minimise total path distance along Voronoi edges.
  - **safest**: maximise minimum clearance (distance to nearest obstacle)
    along the path, with tie-breaking by distance.

Usage
-----
CLI::

    python vormap_pathplan.py obstacles.txt --start 0.2,0.3 --goal 0.8,0.7
    python vormap_pathplan.py obstacles.csv --start 0.1,0.1 --goal 0.9,0.9 --mode safest
    python vormap_pathplan.py obstacles.json --start 0.5,0.2 --goal 0.5,0.8 --svg path.svg

API::

    import vormap, vormap_pathplan as pp
    data = vormap.load_data('obstacles.txt')
    roadmap = pp.build_roadmap(data)
    path = pp.find_path(roadmap, (0.2, 0.3), (0.8, 0.7))
    stats = pp.compute_path_stats(path, roadmap)
    svg = pp.export_path_svg(roadmap, path, data)
"""

import argparse
import heapq
import json
import math
import sys
import xml.etree.ElementTree as ET

import vormap
from vormap_geometry import std as _geometry_std

# scipy is optional — used for Voronoi computation
try:
    from scipy.spatial import Voronoi
    import numpy as np
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Data Classes ─────────────────────────────────────────────────

class RoadmapNode:
    """A node in the Voronoi roadmap (a Voronoi vertex)."""

    __slots__ = ('x', 'y', 'clearance', 'index')

    def __init__(self, x, y, clearance, index):
        self.x = x
        self.y = y
        self.clearance = clearance  # distance to nearest obstacle
        self.index = index

    def pos(self):
        return (self.x, self.y)


class RoadmapEdge:
    """An edge in the Voronoi roadmap connecting two vertices."""

    __slots__ = ('node_a', 'node_b', 'length', 'min_clearance')

    def __init__(self, node_a, node_b, length, min_clearance):
        self.node_a = node_a
        self.node_b = node_b
        self.length = length
        self.min_clearance = min_clearance


class Roadmap:
    """The full Voronoi-edge navigation roadmap."""

    __slots__ = ('nodes', 'edges', 'adjacency', 'bounds', 'obstacle_points')

    def __init__(self):
        self.nodes = []           # list of RoadmapNode
        self.edges = []           # list of RoadmapEdge
        self.adjacency = {}       # node_index → [(neighbour_index, edge)]
        self.bounds = None        # (south, north, west, east)
        self.obstacle_points = [] # [(x, y), ...]


class PathResult:
    """Result of a pathfinding query."""

    __slots__ = ('waypoints', 'node_indices', 'total_distance',
                 'min_clearance', 'avg_clearance', 'found', 'mode',
                 'start', 'goal', 'start_node', 'goal_node')

    def __init__(self):
        self.waypoints = []       # [(x, y), ...]
        self.node_indices = []    # [int, ...]
        self.total_distance = 0.0
        self.min_clearance = float('inf')
        self.avg_clearance = 0.0
        self.found = False
        self.mode = 'shortest'
        self.start = (0, 0)
        self.goal = (0, 0)
        self.start_node = -1
        self.goal_node = -1


# ── Roadmap Construction ────────────────────────────────────────

def build_roadmap(data, *, clip_to_bounds=True):
    """Build a navigation roadmap from Voronoi edges of obstacle points.

    Parameters
    ----------
    data : dict
        Loaded point data from ``vormap.load_data()``.
    clip_to_bounds : bool
        If True, discard Voronoi vertices outside the data bounds.

    Returns
    -------
    Roadmap
        The navigation roadmap with nodes, edges, and adjacency.

    Raises
    ------
    RuntimeError
        If scipy is not installed or fewer than 3 points are provided.
    """
    if not _HAS_SCIPY:
        raise RuntimeError('scipy is required for Voronoi computation')

    points_raw = data.get('data', [])
    if len(points_raw) < 3:
        raise ValueError('Need at least 3 obstacle points for Voronoi diagram')

    # Extract (x, y) coordinates — vormap stores [lng, lat, ...]
    obstacle_pts = [(p[0], p[1]) for p in points_raw]
    pts_array = np.array(obstacle_pts)

    # Compute Voronoi diagram
    vor = Voronoi(pts_array)

    # Determine bounds
    bounds = vormap.compute_bounds(points_raw)
    south, north, west, east = bounds

    roadmap = Roadmap()
    roadmap.bounds = bounds
    roadmap.obstacle_points = obstacle_pts

    # Build nodes from Voronoi vertices
    vertex_to_node = {}  # vor vertex index → roadmap node index
    for vi, (vx, vy) in enumerate(vor.vertices):
        # Optionally clip vertices outside bounds
        if clip_to_bounds:
            if vx < west or vx > east or vy < south or vy > north:
                continue

        # Clearance = distance to nearest obstacle point
        dists = np.sqrt(np.sum((pts_array - [vx, vy]) ** 2, axis=1))
        clearance = float(np.min(dists))

        node = RoadmapNode(vx, vy, clearance, len(roadmap.nodes))
        roadmap.nodes.append(node)
        vertex_to_node[vi] = node.index

    # Build edges from Voronoi ridges (edges between vertices)
    for v1_idx, v2_idx in vor.ridge_vertices:
        # Skip ridges that go to infinity (-1)
        if v1_idx < 0 or v2_idx < 0:
            continue
        if v1_idx not in vertex_to_node or v2_idx not in vertex_to_node:
            continue

        ni = vertex_to_node[v1_idx]
        nj = vertex_to_node[v2_idx]
        na = roadmap.nodes[ni]
        nb = roadmap.nodes[nj]

        length = math.hypot(na.x - nb.x, na.y - nb.y)
        min_clr = min(na.clearance, nb.clearance)

        edge = RoadmapEdge(ni, nj, length, min_clr)
        roadmap.edges.append(edge)

        roadmap.adjacency.setdefault(ni, []).append((nj, edge))
        roadmap.adjacency.setdefault(nj, []).append((ni, edge))

    return roadmap


# ── Pathfinding ──────────────────────────────────────────────────

def _nearest_node(roadmap, x, y):
    """Find the roadmap node closest to (x, y)."""
    best_idx = -1
    best_dist = float('inf')
    for node in roadmap.nodes:
        d = math.hypot(node.x - x, node.y - y)
        if d < best_dist:
            best_dist = d
            best_idx = node.index
    return best_idx, best_dist


def find_path(roadmap, start, goal, *, mode='shortest'):
    """Find a path from start to goal through the Voronoi roadmap.

    Parameters
    ----------
    roadmap : Roadmap
        The navigation roadmap from ``build_roadmap()``.
    start : tuple
        (x, y) start coordinates.
    goal : tuple
        (x, y) goal coordinates.
    mode : str
        ``'shortest'`` — minimise total distance.
        ``'safest'``  — maximise minimum clearance along the path,
        with tie-breaking by shorter distance.

    Returns
    -------
    PathResult
        The computed path with waypoints, distance, and clearance info.
    """
    result = PathResult()
    result.start = start
    result.goal = goal
    result.mode = mode

    if not roadmap.nodes:
        return result

    # Snap start and goal to nearest roadmap nodes
    start_idx, start_dist = _nearest_node(roadmap, start[0], start[1])
    goal_idx, goal_dist = _nearest_node(roadmap, goal[0], goal[1])

    result.start_node = start_idx
    result.goal_node = goal_idx

    if start_idx < 0 or goal_idx < 0:
        return result

    if start_idx == goal_idx:
        node = roadmap.nodes[start_idx]
        result.waypoints = [start, node.pos(), goal]
        result.node_indices = [start_idx]
        result.total_distance = start_dist + goal_dist
        result.min_clearance = node.clearance
        result.avg_clearance = node.clearance
        result.found = True
        return result

    # A* search
    if mode == 'safest':
        path_nodes = _astar_safest(roadmap, start_idx, goal_idx, goal)
    else:
        path_nodes = _astar_shortest(roadmap, start_idx, goal_idx, goal)

    if path_nodes is None:
        return result

    # Build result
    result.found = True
    result.node_indices = path_nodes
    result.waypoints = [start]

    total_dist = start_dist  # distance from start to first node
    clearances = []

    for i, ni in enumerate(path_nodes):
        node = roadmap.nodes[ni]
        result.waypoints.append(node.pos())
        clearances.append(node.clearance)

        if i > 0:
            prev = roadmap.nodes[path_nodes[i - 1]]
            total_dist += math.hypot(node.x - prev.x, node.y - prev.y)

    total_dist += goal_dist  # distance from last node to goal
    result.waypoints.append(goal)

    result.total_distance = total_dist
    result.min_clearance = min(clearances) if clearances else 0
    result.avg_clearance = (sum(clearances) / len(clearances)
                            if clearances else 0)

    return result


def _astar_shortest(roadmap, start_idx, goal_idx, goal):
    """A* with Euclidean heuristic for shortest-distance path."""
    goal_node = roadmap.nodes[goal_idx]
    gx, gy = goal_node.x, goal_node.y

    # Priority queue: (f_score, counter, node_index)
    counter = 0
    open_set = [(0, counter, start_idx)]
    came_from = {}
    g_score = {start_idx: 0.0}

    while open_set:
        _, _, current = heapq.heappop(open_set)

        if current == goal_idx:
            return _reconstruct_path(came_from, current)

        for neighbour, edge in roadmap.adjacency.get(current, []):
            tentative_g = g_score[current] + edge.length

            if tentative_g < g_score.get(neighbour, float('inf')):
                came_from[neighbour] = current
                g_score[neighbour] = tentative_g
                n = roadmap.nodes[neighbour]
                h = math.hypot(n.x - gx, n.y - gy)
                counter += 1
                heapq.heappush(open_set, (tentative_g + h, counter, neighbour))

    return None  # no path found


def _astar_safest(roadmap, start_idx, goal_idx, goal):
    """A* variant that maximises minimum clearance along the path.

    Uses negative min-clearance as the primary cost (so higher clearance
    is preferred), with distance as a tie-breaker.
    """
    # Priority: (-min_clearance_so_far, distance, counter, node_index)
    counter = 0
    start_clr = roadmap.nodes[start_idx].clearance
    open_set = [(-start_clr, 0.0, counter, start_idx)]
    came_from = {}
    best_clr = {start_idx: start_clr}
    best_dist = {start_idx: 0.0}

    while open_set:
        neg_clr, dist, _, current = heapq.heappop(open_set)
        current_clr = -neg_clr

        if current == goal_idx:
            return _reconstruct_path(came_from, current)

        # Skip if we've already found a better path to this node
        if current_clr < best_clr.get(current, -1):
            continue

        for neighbour, edge in roadmap.adjacency.get(current, []):
            n_node = roadmap.nodes[neighbour]
            new_clr = min(current_clr, n_node.clearance)
            new_dist = dist + edge.length

            old_clr = best_clr.get(neighbour, -1)
            old_dist = best_dist.get(neighbour, float('inf'))

            # Prefer higher min-clearance; break ties with shorter distance
            if (new_clr > old_clr or
                    (new_clr == old_clr and new_dist < old_dist)):
                came_from[neighbour] = current
                best_clr[neighbour] = new_clr
                best_dist[neighbour] = new_dist
                counter += 1
                heapq.heappush(open_set,
                               (-new_clr, new_dist, counter, neighbour))

    return None


def _reconstruct_path(came_from, current):
    """Trace back the path from goal to start."""
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path


# ── Path Statistics ──────────────────────────────────────────────

def compute_path_stats(path_result, roadmap):
    """Compute detailed statistics about a path.

    Parameters
    ----------
    path_result : PathResult
        Result from ``find_path()``.
    roadmap : Roadmap
        The roadmap used for pathfinding.

    Returns
    -------
    dict
        Statistics including distance, clearance, efficiency, segment
        count, straight-line distance, and detour ratio.
    """
    if not path_result.found:
        return {'found': False}

    sx, sy = path_result.start
    gx, gy = path_result.goal
    straight_line = math.hypot(gx - sx, gy - sy)

    # Compute per-segment distances
    segments = []
    for i in range(len(path_result.waypoints) - 1):
        ax, ay = path_result.waypoints[i]
        bx, by = path_result.waypoints[i + 1]
        segments.append(math.hypot(bx - ax, by - ay))

    total_dist = sum(segments)
    detour_ratio = (total_dist / straight_line
                    if straight_line > 1e-12 else float('inf'))

    # Clearance along path nodes
    clearances = [roadmap.nodes[ni].clearance
                  for ni in path_result.node_indices]

    return {
        'found': True,
        'mode': path_result.mode,
        'total_distance': total_dist,
        'straight_line_distance': straight_line,
        'detour_ratio': detour_ratio,
        'num_waypoints': len(path_result.waypoints),
        'num_roadmap_nodes': len(path_result.node_indices),
        'num_segments': len(segments),
        'min_clearance': min(clearances) if clearances else 0,
        'max_clearance': max(clearances) if clearances else 0,
        'avg_clearance': (sum(clearances) / len(clearances)
                          if clearances else 0),
        'clearance_std': _geometry_std(clearances),
        'segment_lengths': segments,
        'start': path_result.start,
        'goal': path_result.goal,
    }


# ── Export Functions ─────────────────────────────────────────────

def export_path_json(path_result, roadmap, stats=None):
    """Export path and roadmap data to a JSON-serialisable dict.

    Parameters
    ----------
    path_result : PathResult
        Result from ``find_path()``.
    roadmap : Roadmap
        The navigation roadmap.
    stats : dict, optional
        Pre-computed stats from ``compute_path_stats()``.

    Returns
    -------
    dict
        JSON-ready dictionary with path, roadmap, and stats.
    """
    if stats is None:
        stats = compute_path_stats(path_result, roadmap)

    return {
        'path': {
            'found': path_result.found,
            'mode': path_result.mode,
            'start': list(path_result.start),
            'goal': list(path_result.goal),
            'waypoints': [list(w) for w in path_result.waypoints],
            'total_distance': path_result.total_distance,
            'min_clearance': path_result.min_clearance,
            'avg_clearance': path_result.avg_clearance,
        },
        'roadmap': {
            'num_nodes': len(roadmap.nodes),
            'num_edges': len(roadmap.edges),
            'bounds': list(roadmap.bounds) if roadmap.bounds else None,
        },
        'stats': stats,
    }


def export_path_csv(path_result):
    """Export waypoints to CSV string.

    Returns
    -------
    str
        CSV with columns: step, x, y, type (start/waypoint/goal).
    """
    lines = ['step,x,y,type']
    for i, (wx, wy) in enumerate(path_result.waypoints):
        if i == 0:
            label = 'start'
        elif i == len(path_result.waypoints) - 1:
            label = 'goal'
        else:
            label = 'waypoint'
        lines.append(f'{i},{wx:.6f},{wy:.6f},{label}')
    return '\n'.join(lines)


def export_path_svg(roadmap, path_result, data=None, *,
                    width=800, height=600, show_roadmap=True):
    """Generate an SVG visualisation of the path through obstacles.

    Parameters
    ----------
    roadmap : Roadmap
        The navigation roadmap.
    path_result : PathResult
        The computed path.
    data : dict, optional
        Original data from ``vormap.load_data()`` (for bounds).
    width, height : int
        SVG dimensions in pixels.
    show_roadmap : bool
        Whether to draw all roadmap edges (light grey).

    Returns
    -------
    str
        SVG markup string.
    """
    bounds = roadmap.bounds
    if bounds is None:
        bounds = (0, 1, 0, 1)
    south, north, west, east = bounds

    # Add margin
    dx = (east - west) * 0.05 or 0.1
    dy = (north - south) * 0.05 or 0.1
    west -= dx
    east += dx
    south -= dy
    north += dy

    range_x = east - west
    range_y = north - south

    def tx(x):
        return (x - west) / range_x * width

    def ty(y):
        return height - (y - south) / range_y * height

    svg = ET.Element('svg', xmlns='http://www.w3.org/2000/svg',
                     width=str(width), height=str(height),
                     viewBox=f'0 0 {width} {height}')

    # Background
    ET.SubElement(svg, 'rect', x='0', y='0', width=str(width),
                  height=str(height), fill='#fafafa')

    # Title
    title = ET.SubElement(svg, 'text', x=str(width // 2), y='20',
                          fill='#333')
    title.set('text-anchor', 'middle')
    title.set('font-family', 'sans-serif')
    title.set('font-size', '14')
    mode_label = path_result.mode.capitalize()
    title.text = f'Voronoi Path Planner — {mode_label} Path'

    # Roadmap edges (light grey)
    if show_roadmap:
        for edge in roadmap.edges:
            na = roadmap.nodes[edge.node_a]
            nb = roadmap.nodes[edge.node_b]
            ET.SubElement(svg, 'line',
                          x1=f'{tx(na.x):.1f}', y1=f'{ty(na.y):.1f}',
                          x2=f'{tx(nb.x):.1f}', y2=f'{ty(nb.y):.1f}',
                          stroke='#ddd', **{'stroke-width': '1'})

    # Roadmap nodes (small dots)
    for node in roadmap.nodes:
        ET.SubElement(svg, 'circle',
                      cx=f'{tx(node.x):.1f}', cy=f'{ty(node.y):.1f}',
                      r='2', fill='#ccc')

    # Obstacle points (red)
    for ox, oy in roadmap.obstacle_points:
        ET.SubElement(svg, 'circle',
                      cx=f'{tx(ox):.1f}', cy=f'{ty(oy):.1f}',
                      r='4', fill='#e53e3e', opacity='0.7')

    # Path (blue polyline)
    if path_result.found and len(path_result.waypoints) >= 2:
        points_str = ' '.join(
            f'{tx(wx):.1f},{ty(wy):.1f}'
            for wx, wy in path_result.waypoints
        )
        ET.SubElement(svg, 'polyline', points=points_str,
                      fill='none', stroke='#3182ce',
                      **{'stroke-width': '3', 'stroke-linecap': 'round',
                         'stroke-linejoin': 'round'})

        # Path nodes (blue dots)
        for ni in path_result.node_indices:
            node = roadmap.nodes[ni]
            ET.SubElement(svg, 'circle',
                          cx=f'{tx(node.x):.1f}', cy=f'{ty(node.y):.1f}',
                          r='3.5', fill='#3182ce', opacity='0.8')

    # Start marker (green)
    sx, sy = path_result.start
    ET.SubElement(svg, 'circle',
                  cx=f'{tx(sx):.1f}', cy=f'{ty(sy):.1f}',
                  r='7', fill='#38a169', stroke='white',
                  **{'stroke-width': '2'})
    start_label = ET.SubElement(svg, 'text',
                                x=f'{tx(sx) + 10:.1f}',
                                y=f'{ty(sy) + 4:.1f}',
                                fill='#38a169')
    start_label.set('font-family', 'sans-serif')
    start_label.set('font-size', '11')
    start_label.set('font-weight', 'bold')
    start_label.text = 'START'

    # Goal marker (orange)
    gx, gy = path_result.goal
    ET.SubElement(svg, 'circle',
                  cx=f'{tx(gx):.1f}', cy=f'{ty(gy):.1f}',
                  r='7', fill='#dd6b20', stroke='white',
                  **{'stroke-width': '2'})
    goal_label = ET.SubElement(svg, 'text',
                               x=f'{tx(gx) + 10:.1f}',
                               y=f'{ty(gy) + 4:.1f}',
                               fill='#dd6b20')
    goal_label.set('font-family', 'sans-serif')
    goal_label.set('font-size', '11')
    goal_label.set('font-weight', 'bold')
    goal_label.text = 'GOAL'

    # Legend
    legend_y = height - 15
    legend = ET.SubElement(svg, 'text', x='10', y=str(legend_y),
                           fill='#666')
    legend.set('font-family', 'sans-serif')
    legend.set('font-size', '10')
    if path_result.found:
        legend.text = (f'Distance: {path_result.total_distance:.3f} | '
                       f'Min clearance: {path_result.min_clearance:.3f} | '
                       f'Nodes: {len(path_result.node_indices)}')
    else:
        legend.text = 'No path found between start and goal'

    return ET.tostring(svg, encoding='unicode')


def format_path_report(path_result, roadmap, stats=None):
    """Generate a human-readable text report of the path.

    Parameters
    ----------
    path_result : PathResult
        Result from ``find_path()``.
    roadmap : Roadmap
        The navigation roadmap.
    stats : dict, optional
        Pre-computed stats.

    Returns
    -------
    str
        Formatted text report.
    """
    if stats is None:
        stats = compute_path_stats(path_result, roadmap)

    lines = [
        '╔══════════════════════════════════════════════╗',
        '║       Voronoi Path Planner — Report         ║',
        '╚══════════════════════════════════════════════╝',
        '',
    ]

    lines.append(f'  Mode:           {path_result.mode}')
    lines.append(f'  Start:          ({path_result.start[0]:.4f}, '
                 f'{path_result.start[1]:.4f})')
    lines.append(f'  Goal:           ({path_result.goal[0]:.4f}, '
                 f'{path_result.goal[1]:.4f})')
    lines.append(f'  Path found:     {"Yes" if path_result.found else "No"}')
    lines.append('')

    if not path_result.found:
        lines.append('  No path could be found between start and goal.')
        lines.append('  The start or goal may be in a disconnected region.')
        return '\n'.join(lines)

    lines.append('  ── Distance ──')
    lines.append(f'  Total distance:       {stats["total_distance"]:.4f}')
    lines.append(f'  Straight-line dist:   '
                 f'{stats["straight_line_distance"]:.4f}')
    lines.append(f'  Detour ratio:         {stats["detour_ratio"]:.2f}x')
    lines.append('')

    lines.append('  ── Clearance ──')
    lines.append(f'  Min clearance:        {stats["min_clearance"]:.4f}')
    lines.append(f'  Max clearance:        {stats["max_clearance"]:.4f}')
    lines.append(f'  Avg clearance:        {stats["avg_clearance"]:.4f}')
    lines.append(f'  Clearance std dev:    {stats["clearance_std"]:.4f}')
    lines.append('')

    lines.append('  ── Path Structure ──')
    lines.append(f'  Waypoints:            {stats["num_waypoints"]}')
    lines.append(f'  Roadmap nodes used:   {stats["num_roadmap_nodes"]}')
    lines.append(f'  Segments:             {stats["num_segments"]}')
    lines.append('')

    lines.append('  ── Roadmap ──')
    lines.append(f'  Total roadmap nodes:  {len(roadmap.nodes)}')
    lines.append(f'  Total roadmap edges:  {len(roadmap.edges)}')
    lines.append(f'  Obstacle points:      {len(roadmap.obstacle_points)}')
    lines.append('')

    # Waypoint table
    lines.append('  ── Waypoints ──')
    lines.append(f'  {"#":>4}  {"X":>10}  {"Y":>10}  {"Type":<10}')
    lines.append(f'  {"─" * 4}  {"─" * 10}  {"─" * 10}  {"─" * 10}')
    for i, (wx, wy) in enumerate(path_result.waypoints):
        if i == 0:
            label = 'start'
        elif i == len(path_result.waypoints) - 1:
            label = 'goal'
        else:
            label = 'waypoint'
        lines.append(f'  {i:>4}  {wx:>10.4f}  {wy:>10.4f}  {label:<10}')

    return '\n'.join(lines)


# ── Convenience Pipeline ─────────────────────────────────────────

def plan_path(data, start, goal, *, mode='shortest',
              svg_path=None, json_path=None, csv_path=None,
              report=True):
    """One-call convenience function for the full path planning pipeline.

    Parameters
    ----------
    data : dict
        Loaded point data from ``vormap.load_data()``.
    start : tuple
        (x, y) start coordinates.
    goal : tuple
        (x, y) goal coordinates.
    mode : str
        ``'shortest'`` or ``'safest'``.
    svg_path : str, optional
        File path to write SVG visualisation.
    json_path : str, optional
        File path to write JSON export.
    csv_path : str, optional
        File path to write CSV waypoints.
    report : bool
        If True, print the text report to stdout.

    Returns
    -------
    tuple
        (PathResult, Roadmap, dict) — path, roadmap, stats.
    """
    roadmap = build_roadmap(data)
    path_result = find_path(roadmap, start, goal, mode=mode)
    stats = compute_path_stats(path_result, roadmap)

    if report:
        print(format_path_report(path_result, roadmap, stats))

    if svg_path:
        svg_content = export_path_svg(roadmap, path_result, data)
        with open(svg_path, 'w', encoding='utf-8') as f:
            f.write(svg_content)
        print(f'\n  SVG written to {svg_path}')

    if json_path:
        json_data = export_path_json(path_result, roadmap, stats)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2)
        print(f'  JSON written to {json_path}')

    if csv_path:
        csv_content = export_path_csv(path_result)
        with open(csv_path, 'w', encoding='utf-8') as f:
            f.write(csv_content)
        print(f'  CSV written to {csv_path}')

    return path_result, roadmap, stats


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Voronoi-based path planner for obstacle-aware navigation')
    parser.add_argument('input', help='Input obstacle points file '
                        '(txt, csv, json, geojson)')
    parser.add_argument('--start', required=True,
                        help='Start coordinates as x,y (e.g. 0.2,0.3)')
    parser.add_argument('--goal', required=True,
                        help='Goal coordinates as x,y (e.g. 0.8,0.7)')
    parser.add_argument('--mode', choices=['shortest', 'safest'],
                        default='shortest',
                        help='Pathfinding mode (default: shortest)')
    parser.add_argument('--svg', metavar='FILE',
                        help='Write SVG visualisation to FILE')
    parser.add_argument('--json', metavar='FILE',
                        help='Write JSON export to FILE')
    parser.add_argument('--csv', metavar='FILE',
                        help='Write CSV waypoints to FILE')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress text report')

    args = parser.parse_args()

    # Parse coordinates
    try:
        sx, sy = (float(v) for v in args.start.split(','))
    except ValueError:
        parser.error(f'Invalid start coordinates: {args.start}')
        return

    try:
        gx, gy = (float(v) for v in args.goal.split(','))
    except ValueError:
        parser.error(f'Invalid goal coordinates: {args.goal}')
        return

    data = vormap.load_data(args.input)
    plan_path(data, (sx, sy), (gx, gy),
              mode=args.mode,
              svg_path=args.svg,
              json_path=args.json,
              csv_path=args.csv,
              report=not args.quiet)


if __name__ == '__main__':
    main()
