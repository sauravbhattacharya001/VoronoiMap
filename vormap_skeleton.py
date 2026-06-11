"""Voronoi Skeleton (medial-axis roadmap) extraction and export.

The internal edges of a Voronoi diagram — the segments shared between two
adjacent cells — form the diagram's *skeleton*: a network of curves that
are locally equidistant from the two nearest seeds.  This skeleton is the
classic **maximum-clearance roadmap** used in motion planning: travelling
along it keeps you as far as possible from the surrounding sites.

This module turns the finite Voronoi cells produced by
:func:`vormap_viz.compute_regions` into a traversable graph and exposes:

* :class:`Skeleton` — nodes (Voronoi vertices), edges (internal segments,
  each annotated with length, midpoint clearance and the two seeds it
  separates), and per-node degree.
* :func:`skeleton_metrics` — total length, junction / endpoint / isolated
  node counts, branch count, mean / min / max clearance, and the single
  longest corridor (edge).
* :meth:`Skeleton.shortest_path` — Dijkstra shortest path *along the
  skeleton* between two arbitrary points (each snapped to the nearest
  skeleton node), yielding a maximum-clearance route.
* Exporters: :func:`export_skeleton_json`, :func:`export_skeleton_csv`,
  :func:`export_skeleton_svg` (skeleton drawn over the cells).

Unlike :mod:`vormap_graph` / :mod:`vormap_network` (which build the
*Delaunay dual* — seed-to-seed adjacency) this module works on the Voronoi
*edge* network itself, i.e. the medial axis between the seeds.

Usage::

    import vormap_viz
    from vormap_skeleton import extract_skeleton, skeleton_metrics

    seeds = [(100, 100), (300, 100), (200, 300), (400, 400)]
    regions = vormap_viz.compute_regions(seeds)
    skel = extract_skeleton(regions)

    m = skeleton_metrics(skel)
    print(m.total_length, m.num_junctions, m.mean_clearance)

    path = skel.shortest_path((120, 120), (390, 390))
    print(path.length, path.min_clearance, path.nodes)

Command line::

    python vormap_skeleton.py data.txt 12 --json skel.json --svg skel.svg
    python vormap_skeleton.py --demo --path 120,120 390,390
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
Polygon = List[Tuple[float, float]]
Regions = Dict[Point, Polygon]

# Default coordinate-merge tolerance.  Voronoi vertices shared by adjacent
# cells are computed independently and can differ by tiny floating-point
# amounts; snapping to a grid of this size reunites them into one node.
DEFAULT_TOL = 0.5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SkeletonEdge:
    """One internal Voronoi segment between two skeleton nodes.

    Attributes
    ----------
    a, b : int
        Indices into :attr:`Skeleton.nodes` (``a < b``).
    length : float
        Euclidean length of the segment.
    clearance : float
        Distance from the segment midpoint to the nearest seed — how much
        room a traveller has when standing on this edge.
    seeds : tuple of int
        Indices of the (up to two) seeds whose cells share this edge.
    """
    a: int
    b: int
    length: float
    clearance: float
    seeds: Tuple[int, ...]


@dataclass
class Skeleton:
    """Traversable Voronoi skeleton graph.

    Parameters
    ----------
    nodes : list of (x, y)
        Unique Voronoi vertices (skeleton junctions / endpoints).
    edges : list of :class:`SkeletonEdge`
        Internal Voronoi segments connecting the nodes.
    seeds : list of (x, y)
        The originating seed points (for clearance / SVG context).
    """
    nodes: List[Point]
    edges: List[SkeletonEdge]
    seeds: List[Point] = field(default_factory=list)

    # -- basic accessors --------------------------------------------------

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def num_edges(self) -> int:
        return len(self.edges)

    def degree(self) -> List[int]:
        """Return the degree (edge count) of every node, indexed by node."""
        deg = [0] * len(self.nodes)
        for e in self.edges:
            deg[e.a] += 1
            deg[e.b] += 1
        return deg

    def adjacency(self) -> List[List[Tuple[int, float]]]:
        """Return adjacency list: ``adj[i] = [(neighbour, edge_length), ...]``."""
        adj: List[List[Tuple[int, float]]] = [[] for _ in self.nodes]
        for e in self.edges:
            adj[e.a].append((e.b, e.length))
            adj[e.b].append((e.a, e.length))
        return adj

    # -- queries ----------------------------------------------------------

    def nearest_node(self, point: Point) -> int:
        """Return the index of the skeleton node closest to *point*."""
        if not self.nodes:
            raise ValueError("skeleton has no nodes")
        px, py = float(point[0]), float(point[1])
        best_i = 0
        best_d = float("inf")
        for i, (nx, ny) in enumerate(self.nodes):
            d = (nx - px) ** 2 + (ny - py) ** 2
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    def shortest_path(self, start: Point, goal: Point) -> "SkeletonPath":
        """Shortest path along the skeleton between two points.

        *start* and *goal* are each snapped to their nearest skeleton node,
        then Dijkstra finds the minimum-total-length route.  The returned
        path reports its own length and the minimum clearance encountered
        (the tightest squeeze along the route).

        Returns an empty :class:`SkeletonPath` when no route exists (the
        skeleton is disconnected between the two endpoints).
        """
        if not self.nodes:
            raise ValueError("skeleton has no nodes")
        s = self.nearest_node(start)
        g = self.nearest_node(goal)
        adj = self.adjacency()

        # Edge lookup for clearance reporting along the chosen path.
        edge_by_pair: Dict[Tuple[int, int], SkeletonEdge] = {}
        for e in self.edges:
            edge_by_pair[(e.a, e.b)] = e
            edge_by_pair[(e.b, e.a)] = e

        dist = [float("inf")] * len(self.nodes)
        prev: List[Optional[int]] = [None] * len(self.nodes)
        dist[s] = 0.0
        pq: List[Tuple[float, int]] = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            if u == g:
                break
            for v, w in adj[u]:
                nd = d + w
                if nd < dist[v]:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        if dist[g] == float("inf"):
            return SkeletonPath(nodes=[], coords=[], length=0.0,
                                min_clearance=0.0, reachable=False)

        # Reconstruct node path.
        order: List[int] = []
        cur: Optional[int] = g
        while cur is not None:
            order.append(cur)
            cur = prev[cur]
        order.reverse()

        coords = [self.nodes[i] for i in order]
        min_clear = float("inf")
        for i in range(len(order) - 1):
            e = edge_by_pair.get((order[i], order[i + 1]))
            if e is not None:
                min_clear = min(min_clear, e.clearance)
        if min_clear == float("inf"):
            min_clear = 0.0
        return SkeletonPath(nodes=order, coords=coords, length=dist[g],
                            min_clearance=min_clear, reachable=True)


@dataclass
class SkeletonPath:
    """A route along the Voronoi skeleton."""
    nodes: List[int]
    coords: List[Point]
    length: float
    min_clearance: float
    reachable: bool


@dataclass
class SkeletonMetrics:
    """Summary statistics describing a :class:`Skeleton`."""
    num_nodes: int
    num_edges: int
    total_length: float
    num_junctions: int      # degree >= 3
    num_endpoints: int      # degree == 1
    num_isolated: int       # degree == 0
    num_branches: int       # edges incident to a junction
    mean_clearance: float
    min_clearance: float
    max_clearance: float
    longest_corridor: float

    def as_dict(self) -> Dict[str, float]:
        return {
            "num_nodes": self.num_nodes,
            "num_edges": self.num_edges,
            "total_length": self.total_length,
            "num_junctions": self.num_junctions,
            "num_endpoints": self.num_endpoints,
            "num_isolated": self.num_isolated,
            "num_branches": self.num_branches,
            "mean_clearance": self.mean_clearance,
            "min_clearance": self.min_clearance,
            "max_clearance": self.max_clearance,
            "longest_corridor": self.longest_corridor,
        }


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _snap(value: float, tol: float) -> float:
    """Round *value* to the nearest *tol* grid line."""
    return round(value / tol) * tol


def extract_skeleton(regions: Regions, *, tol: float = DEFAULT_TOL) -> Skeleton:
    """Build a :class:`Skeleton` from Voronoi *regions*.

    *regions* is a mapping ``seed -> polygon`` as returned by
    :func:`vormap_viz.compute_regions`.  An edge of a polygon is part of
    the skeleton when it is shared by **two** cells (an internal Voronoi
    edge); boundary edges that belong to a single cell are excluded.

    Parameters
    ----------
    regions : dict
        ``{(sx, sy): [(x, y), ...], ...}``.
    tol : float
        Coordinate-merge tolerance for reuniting near-duplicate vertices.

    Returns
    -------
    Skeleton
    """
    seeds: List[Point] = [tuple(map(float, s)) for s in regions.keys()]

    # Inverted index: snapped undirected edge -> set of owning seed indices.
    # Also remember a representative (un-snapped) endpoint pair per edge so
    # the rendered geometry stays faithful to the original vertices.
    edge_owners: Dict[Tuple[Tuple[float, float], Tuple[float, float]], set] = {}
    edge_repr: Dict[Tuple[Tuple[float, float], Tuple[float, float]], Tuple[Point, Point]] = {}

    for si, (seed, poly) in enumerate(regions.items()):
        if not poly or len(poly) < 2:
            continue
        n = len(poly)
        for i in range(n):
            v1 = poly[i]
            v2 = poly[(i + 1) % n]
            sv1 = (_snap(v1[0], tol), _snap(v1[1], tol))
            sv2 = (_snap(v2[0], tol), _snap(v2[1], tol))
            if sv1 == sv2:
                continue  # degenerate edge
            key = (sv1, sv2) if sv1 <= sv2 else (sv2, sv1)
            owners = edge_owners.get(key)
            if owners is None:
                owners = set()
                edge_owners[key] = owners
                edge_repr[key] = (tuple(map(float, v1)), tuple(map(float, v2)))
            owners.add(si)

    # Assign a node index to every snapped endpoint that appears on an
    # internal edge.
    node_index: Dict[Tuple[float, float], int] = {}
    nodes: List[Point] = []

    def _node_id(snapped: Tuple[float, float], representative: Point) -> int:
        idx = node_index.get(snapped)
        if idx is None:
            idx = len(nodes)
            node_index[snapped] = idx
            nodes.append((float(representative[0]), float(representative[1])))
        return idx

    edges: List[SkeletonEdge] = []
    for key, owners in edge_owners.items():
        if len(owners) < 2:
            continue  # boundary edge — not part of the skeleton
        (sv1, sv2) = key
        (rv1, rv2) = edge_repr[key]
        a = _node_id(sv1, rv1)
        b = _node_id(sv2, rv2)
        if a == b:
            continue
        if a > b:
            a, b = b, a
        ax, ay = nodes[a]
        bx, by = nodes[b]
        length = math.hypot(bx - ax, by - ay)
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        clearance = _clearance(mx, my, seeds)
        edges.append(SkeletonEdge(a=a, b=b, length=length,
                                  clearance=clearance,
                                  seeds=tuple(sorted(owners))))

    return Skeleton(nodes=nodes, edges=edges, seeds=seeds)


def _clearance(px: float, py: float, seeds: Sequence[Point]) -> float:
    """Distance from ``(px, py)`` to the nearest seed."""
    best = float("inf")
    for sx, sy in seeds:
        d = math.hypot(sx - px, sy - py)
        if d < best:
            best = d
    return 0.0 if best == float("inf") else best


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def skeleton_metrics(skel: Skeleton) -> SkeletonMetrics:
    """Compute summary :class:`SkeletonMetrics` for *skel*."""
    deg = skel.degree()
    num_junctions = sum(1 for d in deg if d >= 3)
    num_endpoints = sum(1 for d in deg if d == 1)
    num_isolated = sum(1 for d in deg if d == 0)

    junction_nodes = {i for i, d in enumerate(deg) if d >= 3}
    num_branches = sum(1 for e in skel.edges
                       if e.a in junction_nodes or e.b in junction_nodes)

    total_length = math.fsum(e.length for e in skel.edges)
    if skel.edges:
        clearances = [e.clearance for e in skel.edges]
        mean_clearance = math.fsum(clearances) / len(clearances)
        min_clearance = min(clearances)
        max_clearance = max(clearances)
        longest_corridor = max(e.length for e in skel.edges)
    else:
        mean_clearance = min_clearance = max_clearance = 0.0
        longest_corridor = 0.0

    return SkeletonMetrics(
        num_nodes=skel.num_nodes,
        num_edges=skel.num_edges,
        total_length=total_length,
        num_junctions=num_junctions,
        num_endpoints=num_endpoints,
        num_isolated=num_isolated,
        num_branches=num_branches,
        mean_clearance=mean_clearance,
        min_clearance=min_clearance,
        max_clearance=max_clearance,
        longest_corridor=longest_corridor,
    )


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def _resolve_output(path: str) -> str:
    """Validate an output path using vormap's traversal guard when present."""
    try:
        from vormap import validate_output_path
        return validate_output_path(path, allow_absolute=True)
    except Exception:
        return path


def export_skeleton_json(skel: Skeleton, path: str) -> str:
    """Write the skeleton (nodes, edges, metrics) to a JSON file."""
    metrics = skeleton_metrics(skel)
    payload = {
        "nodes": [{"x": x, "y": y} for (x, y) in skel.nodes],
        "edges": [
            {
                "a": e.a,
                "b": e.b,
                "length": e.length,
                "clearance": e.clearance,
                "seeds": list(e.seeds),
            }
            for e in skel.edges
        ],
        "metrics": metrics.as_dict(),
    }
    resolved = _resolve_output(path)
    with open(resolved, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def export_skeleton_csv(skel: Skeleton, path: str) -> str:
    """Write the skeleton edge list as CSV.

    Columns: ``ax,ay,bx,by,length,clearance,seeds`` (``seeds`` is a
    space-separated list of seed indices).
    """
    lines = ["ax,ay,bx,by,length,clearance,seeds"]
    for e in skel.edges:
        ax, ay = skel.nodes[e.a]
        bx, by = skel.nodes[e.b]
        seeds = " ".join(str(s) for s in e.seeds)
        lines.append(f"{ax:.6f},{ay:.6f},{bx:.6f},{by:.6f},"
                     f"{e.length:.6f},{e.clearance:.6f},{seeds}")
    resolved = _resolve_output(path)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def export_skeleton_svg(skel: Skeleton, path: str, *,
                        width: int = 800, height: int = 600,
                        show_seeds: bool = True,
                        highlight_path: Optional[SkeletonPath] = None) -> str:
    """Render the skeleton over its seeds as an SVG.

    Skeleton edges are drawn in dark red; junctions (degree >= 3) as larger
    dots and endpoints (degree 1) as small dots.  When *highlight_path* is
    supplied its route is overlaid as a thick green polyline.
    """
    pts: List[Point] = list(skel.nodes) + list(skel.seeds)
    if not pts:
        pts = [(0.0, 0.0)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    margin = 24
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    data_w = (x_max - x_min) or 1.0
    data_h = (y_max - y_min) or 1.0
    scale = min((width - 2 * margin) / data_w,
                (height - 2 * margin) / data_h)

    def tx(x: float) -> float:
        return margin + (x - x_min) * scale

    def ty(y: float) -> float:
        return margin + (y - y_min) * scale

    out: List[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    # Skeleton edges.
    for e in skel.edges:
        ax, ay = skel.nodes[e.a]
        bx, by = skel.nodes[e.b]
        out.append(
            f'<line x1="{tx(ax):.1f}" y1="{ty(ay):.1f}" '
            f'x2="{tx(bx):.1f}" y2="{ty(by):.1f}" '
            'stroke="#b71c1c" stroke-width="1.5"/>'
        )

    # Highlighted route.
    if highlight_path is not None and highlight_path.reachable \
            and len(highlight_path.coords) >= 2:
        pts_attr = " ".join(
            f"{tx(x):.1f},{ty(y):.1f}" for (x, y) in highlight_path.coords
        )
        out.append(
            f'<polyline points="{pts_attr}" fill="none" '
            'stroke="#2e7d32" stroke-width="4" stroke-linejoin="round" '
            'stroke-linecap="round" opacity="0.85"/>'
        )

    # Seeds.
    if show_seeds:
        for sx, sy in skel.seeds:
            out.append(
                f'<circle cx="{tx(sx):.1f}" cy="{ty(sy):.1f}" r="3" '
                'fill="#1565c0"/>'
            )

    # Skeleton nodes by role.
    deg = skel.degree()
    for i, (nx, ny) in enumerate(skel.nodes):
        d = deg[i]
        if d >= 3:
            out.append(f'<circle cx="{tx(nx):.1f}" cy="{ty(ny):.1f}" r="4" '
                       'fill="#b71c1c"/>')
        elif d == 1:
            out.append(f'<circle cx="{tx(nx):.1f}" cy="{ty(ny):.1f}" r="2.5" '
                       'fill="#ef6c00"/>')

    out.append("</svg>")
    resolved = _resolve_output(path)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path


def format_metrics_table(skel: Skeleton) -> str:
    """Return a human-readable text summary of the skeleton metrics."""
    m = skeleton_metrics(skel)
    rows = [
        ("Nodes", f"{m.num_nodes}"),
        ("Edges", f"{m.num_edges}"),
        ("Total length", f"{m.total_length:.2f}"),
        ("Junctions (deg>=3)", f"{m.num_junctions}"),
        ("Endpoints (deg==1)", f"{m.num_endpoints}"),
        ("Isolated nodes", f"{m.num_isolated}"),
        ("Branch edges", f"{m.num_branches}"),
        ("Mean clearance", f"{m.mean_clearance:.2f}"),
        ("Min clearance", f"{m.min_clearance:.2f}"),
        ("Max clearance", f"{m.max_clearance:.2f}"),
        ("Longest corridor", f"{m.longest_corridor:.2f}"),
    ]
    label_w = max(len(r[0]) for r in rows)
    out = ["Voronoi Skeleton", "=" * 32]
    for label, value in rows:
        out.append(f"{label.ljust(label_w)} : {value}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Region helpers / CLI
# ---------------------------------------------------------------------------

def _demo_regions() -> Regions:
    """Build a small, deterministic set of Voronoi regions for ``--demo``.

    A 4x3 jittered grid of seeds gives a skeleton with several junctions
    and endpoints, so the demo exercises every metric.
    """
    seeds: List[Point] = []
    jitter = [0.0, 18.0, -12.0, 9.0, -7.0, 14.0]
    k = 0
    for gy in range(3):
        for gx in range(4):
            jx = jitter[k % len(jitter)]
            jy = jitter[(k + 3) % len(jitter)]
            seeds.append((80.0 + gx * 160.0 + jx, 80.0 + gy * 160.0 + jy))
            k += 1
    return _regions_from_seeds(seeds)


def _regions_from_seeds(seeds: Sequence[Point]) -> Regions:
    """Compute finite Voronoi regions for *seeds* via :mod:`vormap_viz`."""
    import vormap_viz
    return vormap_viz.compute_regions([tuple(map(float, s)) for s in seeds])


def _load_seed_file(path: str) -> List[Point]:
    """Load whitespace- or comma-separated ``x y`` points from a file."""
    try:
        from vormap import validate_input_path
        resolved = validate_input_path(path, allow_absolute=True)
    except Exception:
        resolved = path
    pts: List[Point] = []
    with open(resolved, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _regions_from_datafile(datafile: str, n: Optional[int]) -> Regions:
    """Resolve a CLI ``datafile``/``n`` pair into Voronoi regions.

    Tries :func:`vormap.load_data` first (honours the ``data/`` directory
    and bounds auto-detection); falls back to reading raw ``x y`` rows.
    When *n* is given and smaller than the dataset, the first *n* points
    are used (matching the core CLI's ``n`` semantics for quick previews).
    """
    points: List[Point]
    try:
        import vormap
        points = [tuple(map(float, p)) for p in vormap.load_data(datafile)]
    except Exception:
        points = _load_seed_file(datafile)
    if not points:
        raise ValueError(f"no usable points found in '{datafile}'")
    if n is not None and 0 < n < len(points):
        points = points[:n]
    return _regions_from_seeds(points)


def _parse_point(text: str) -> Point:
    """Parse an ``'x,y'`` CLI argument into a point tuple."""
    parts = text.replace(" ", "").split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected 'x,y', got '{text}'")
    return (float(parts[0]), float(parts[1]))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vormap_skeleton",
        description="Extract and export the Voronoi skeleton (medial-axis "
                    "roadmap) of a point set.",
    )
    p.add_argument("datafile", nargs="?",
                   help="point data file (omit when using --demo)")
    p.add_argument("n", nargs="?", type=int, default=None,
                   help="optional cap on number of points to use")
    p.add_argument("--demo", action="store_true",
                   help="use a built-in jittered-grid point set")
    p.add_argument("--tol", type=float, default=DEFAULT_TOL,
                   help=f"vertex-merge tolerance (default {DEFAULT_TOL})")
    p.add_argument("--json", dest="json_out", metavar="FILE",
                   help="write nodes/edges/metrics as JSON")
    p.add_argument("--csv", dest="csv_out", metavar="FILE",
                   help="write the skeleton edge list as CSV")
    p.add_argument("--svg", dest="svg_out", metavar="FILE",
                   help="render the skeleton over its seeds as SVG")
    p.add_argument("--path", nargs=2, type=_parse_point,
                   metavar=("X1,Y1", "X2,Y2"),
                   help="compute a max-clearance route between two points")
    p.add_argument("--format", choices=("text", "json"), default="text",
                   help="stdout format for the metrics summary")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.demo and not args.datafile:
        parser.error("a datafile is required unless --demo is given")

    try:
        if args.demo:
            regions = _demo_regions()
        else:
            regions = _regions_from_datafile(args.datafile, args.n)
    except Exception as exc:  # pragma: no cover - surfaced to CLI user
        print(f"error: {exc}", file=sys.stderr)
        return 2

    skel = extract_skeleton(regions, tol=args.tol)
    metrics = skeleton_metrics(skel)

    path_result: Optional[SkeletonPath] = None
    if args.path:
        path_result = skel.shortest_path(args.path[0], args.path[1])

    if args.format == "json":
        payload: Dict[str, object] = {"metrics": metrics.as_dict()}
        if path_result is not None:
            payload["path"] = {
                "reachable": path_result.reachable,
                "length": path_result.length,
                "min_clearance": path_result.min_clearance,
                "nodes": path_result.nodes,
                "coords": [list(c) for c in path_result.coords],
            }
        print(json.dumps(payload, indent=2))
    else:
        print(format_metrics_table(skel))
        if path_result is not None:
            print()
            if path_result.reachable:
                print(f"Route: {len(path_result.nodes)} nodes, "
                      f"length={path_result.length:.2f}, "
                      f"min_clearance={path_result.min_clearance:.2f}")
            else:
                print("Route: unreachable (skeleton is disconnected "
                      "between the endpoints)")

    if args.json_out:
        export_skeleton_json(skel, args.json_out)
        print(f"JSON written to {args.json_out}", file=sys.stderr)
    if args.csv_out:
        export_skeleton_csv(skel, args.csv_out)
        print(f"CSV written to {args.csv_out}", file=sys.stderr)
    if args.svg_out:
        export_skeleton_svg(skel, args.svg_out, highlight_path=path_result)
        print(f"SVG written to {args.svg_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
