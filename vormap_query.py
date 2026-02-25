"""Point Location & Nearest Neighbor Query for Voronoi diagrams.

Provides fast spatial queries on Voronoi diagrams using KD-trees.
Supports nearest-neighbor lookup, k-nearest, radius search, point
location (which region contains a point), and distance-to-boundary.

Usage::

    from vormap_query import VoronoiIndex, export_query_json, export_query_svg

    idx = VoronoiIndex(seeds, regions)
    result = idx.nearest((300, 400))
    print(result.seed_index, result.distance)

    results = idx.nearest_k((300, 400), k=5)
    region = idx.locate((300, 400))
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

try:
    from scipy.spatial import cKDTree as KDTree
    _HAS_KDTREE = True
except ImportError:
    _HAS_KDTREE = False

Point = Tuple[float, float]
Polygon = List[Tuple[float, float]]
Regions = Dict[Point, Polygon]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    """Result of a nearest-neighbor query."""
    seed_index: int
    distance: float
    seed_coords: Tuple[float, float]


@dataclass
class QueryStats:
    """Statistics over a batch of query results."""
    count: int
    min_distance: float
    max_distance: float
    mean_distance: float
    median_distance: float
    std_distance: float


@dataclass
class CoverageResult:
    """Coverage analysis — how many query points fall in each region."""
    region_counts: Dict[int, int]
    total_points: int
    empty_regions: int
    max_count: int
    min_count: int


# ---------------------------------------------------------------------------
# Brute-force fallback
# ---------------------------------------------------------------------------

def _brute_nearest(seeds: List[Point], point: Point, k: int = 1):
    """Brute-force k-nearest neighbours (fallback when scipy absent)."""
    dists = []
    for i, s in enumerate(seeds):
        d = math.hypot(s[0] - point[0], s[1] - point[1])
        dists.append((d, i))
    dists.sort()
    return dists[:k]


def _brute_radius(seeds: List[Point], point: Point, radius: float):
    """Brute-force radius search."""
    results = []
    for i, s in enumerate(seeds):
        d = math.hypot(s[0] - point[0], s[1] - point[1])
        if d <= radius:
            results.append((d, i))
    results.sort()
    return results


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _point_to_segment_distance(px: float, py: float,
                               ax: float, ay: float,
                               bx: float, by: float) -> float:
    """Minimum distance from point (px, py) to segment (ax, ay)-(bx, by)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


# ---------------------------------------------------------------------------
# VoronoiIndex
# ---------------------------------------------------------------------------

class VoronoiIndex:
    """Spatial index over Voronoi seeds for fast queries.

    Parameters
    ----------
    seeds : sequence of (x, y)
        Seed point coordinates.
    regions : dict mapping seed (x, y) → polygon vertices, or ``None``.
        Required only for ``locate`` and ``distance_to_boundary``.
    """

    def __init__(self, seeds: Sequence[Point],
                 regions: Optional[Regions] = None) -> None:
        if not seeds:
            raise ValueError("seeds must be non-empty")
        self._seeds: List[Point] = [tuple(s) for s in seeds]
        self._regions = regions
        # Build ordered region list aligned with seed indices
        self._region_polys: Optional[List[Optional[Polygon]]] = None
        if regions is not None:
            self._region_polys = []
            for s in self._seeds:
                key = tuple(s)
                self._region_polys.append(regions.get(key))

        # Build KD-tree if available
        self._tree = None
        if _HAS_KDTREE and len(self._seeds) > 0:
            self._tree = KDTree(self._seeds)

    # -- single nearest ---------------------------------------------------

    def nearest(self, point: Point) -> QueryResult:
        """Return the nearest seed to *point*."""
        point = tuple(point)
        if self._tree is not None:
            d, i = self._tree.query(point)
            return QueryResult(seed_index=int(i), distance=float(d),
                               seed_coords=self._seeds[i])
        pairs = _brute_nearest(self._seeds, point, 1)
        d, i = pairs[0]
        return QueryResult(seed_index=i, distance=d,
                           seed_coords=self._seeds[i])

    # -- k-nearest --------------------------------------------------------

    def nearest_k(self, point: Point, k: int = 1) -> List[QueryResult]:
        """Return the *k* nearest seeds to *point*."""
        if k < 1:
            raise ValueError("k must be >= 1")
        k = min(k, len(self._seeds))
        point = tuple(point)
        if self._tree is not None:
            dists, idxs = self._tree.query(point, k=k)
            if k == 1:
                dists, idxs = [dists], [idxs]
            return [QueryResult(seed_index=int(i), distance=float(d),
                                seed_coords=self._seeds[i])
                    for d, i in zip(dists, idxs)]
        pairs = _brute_nearest(self._seeds, point, k)
        return [QueryResult(seed_index=i, distance=d,
                            seed_coords=self._seeds[i])
                for d, i in pairs]

    # -- locate (point-in-region) -----------------------------------------

    def locate(self, point: Point) -> Optional[int]:
        """Return the index of the Voronoi region containing *point*.

        Uses nearest-seed as a fast proxy (equivalent for standard
        Voronoi diagrams).  Returns ``None`` if regions are not provided.
        """
        result = self.nearest(point)
        return result.seed_index

    # -- batch queries ----------------------------------------------------

    def batch_query(self, points: Sequence[Point]) -> List[QueryResult]:
        """Nearest-seed query for each point in *points*."""
        return [self.nearest(p) for p in points]

    def batch_locate(self, points: Sequence[Point]) -> List[Optional[int]]:
        """Locate each point in *points*."""
        return [self.locate(p) for p in points]

    # -- radius search ----------------------------------------------------

    def within_radius(self, point: Point, radius: float) -> List[QueryResult]:
        """Return all seeds within *radius* of *point*."""
        if radius < 0:
            raise ValueError("radius must be >= 0")
        point = tuple(point)
        if self._tree is not None:
            idxs = self._tree.query_ball_point(point, radius)
            results = []
            for i in sorted(idxs):
                d = math.hypot(self._seeds[i][0] - point[0],
                               self._seeds[i][1] - point[1])
                results.append(QueryResult(seed_index=i, distance=d,
                                           seed_coords=self._seeds[i]))
            results.sort(key=lambda r: r.distance)
            return results
        pairs = _brute_radius(self._seeds, point, radius)
        return [QueryResult(seed_index=i, distance=d,
                            seed_coords=self._seeds[i])
                for d, i in pairs]

    # -- distance to boundary ---------------------------------------------

    def distance_to_boundary(self, point: Point) -> Optional[float]:
        """Minimum distance from *point* to the nearest region boundary edge.

        Requires regions to have been provided at construction time.
        Returns ``None`` if regions are unavailable.
        """
        if self._regions is None:
            return None
        px, py = point
        min_dist = float('inf')
        for poly in self._regions.values():
            if poly is None or len(poly) < 2:
                continue
            n = len(poly)
            for j in range(n):
                ax, ay = poly[j]
                bx, by = poly[(j + 1) % n]
                d = _point_to_segment_distance(px, py, ax, ay, bx, by)
                if d < min_dist:
                    min_dist = d
        return min_dist if min_dist < float('inf') else None

    # -- properties -------------------------------------------------------

    @property
    def num_seeds(self) -> int:
        """Number of seeds in the index."""
        return len(self._seeds)

    @property
    def seeds(self) -> List[Point]:
        """Copy of the seed list."""
        return list(self._seeds)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def query_stats(results: Sequence[QueryResult]) -> QueryStats:
    """Compute statistics over a batch of query results."""
    if not results:
        return QueryStats(count=0, min_distance=0.0, max_distance=0.0,
                          mean_distance=0.0, median_distance=0.0,
                          std_distance=0.0)
    dists = [r.distance for r in results]
    return QueryStats(
        count=len(dists),
        min_distance=min(dists),
        max_distance=max(dists),
        mean_distance=statistics.mean(dists),
        median_distance=statistics.median(dists),
        std_distance=statistics.pstdev(dists),
    )


def coverage_analysis(points: Sequence[Point],
                      index: VoronoiIndex) -> CoverageResult:
    """Analyse how many query points fall in each Voronoi region."""
    counts: Dict[int, int] = {}
    for p in points:
        r = index.locate(p)
        if r is not None:
            counts[r] = counts.get(r, 0) + 1
    # Ensure every seed is represented
    for i in range(index.num_seeds):
        counts.setdefault(i, 0)
    vals = list(counts.values())
    return CoverageResult(
        region_counts=counts,
        total_points=len(points),
        empty_regions=sum(1 for v in vals if v == 0),
        max_count=max(vals) if vals else 0,
        min_count=min(vals) if vals else 0,
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_query_json(results: Sequence[QueryResult], path: str) -> None:
    """Write query results to a JSON file."""
    data = [asdict(r) for r in results]
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def export_query_svg(seeds: Sequence[Point],
                     queries: Sequence[Point],
                     results: Sequence[QueryResult],
                     path: str,
                     width: int = 800,
                     height: int = 600) -> None:
    """Write an SVG showing query points and nearest-seed connections."""
    # Determine bounds
    all_pts = list(seeds) + list(queries)
    if not all_pts:
        all_pts = [(0, 0)]
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    margin = 20
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    data_w = x_max - x_min or 1
    data_h = y_max - y_min or 1
    scale = min((width - 2 * margin) / data_w,
                (height - 2 * margin) / data_h)

    def tx(x: float) -> float:
        return margin + (x - x_min) * scale

    def ty(y: float) -> float:
        return margin + (y - y_min) * scale

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    # Draw connection lines
    for q, r in zip(queries, results):
        sx, sy = tx(q[0]), ty(q[1])
        ex, ey = tx(r.seed_coords[0]), ty(r.seed_coords[1])
        lines.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" '
                     f'x2="{ex:.1f}" y2="{ey:.1f}" '
                     f'stroke="#999" stroke-width="1" stroke-dasharray="4,2"/>')

    # Draw seeds
    for s in seeds:
        cx, cy = tx(s[0]), ty(s[1])
        lines.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" '
                     f'fill="#2196F3"/>')

    # Draw query points
    for q in queries:
        cx, cy = tx(q[0]), ty(q[1])
        lines.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="3" '
                     f'fill="#F44336"/>')

    lines.append('</svg>')
    with open(path, 'w') as f:
        f.write('\n'.join(lines))


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _parse_query_point(s: str) -> Point:
    """Parse 'X,Y' string to a point tuple."""
    parts = s.split(',')
    if len(parts) != 2:
        raise ValueError(f"Expected X,Y format, got '{s}'")
    return (float(parts[0]), float(parts[1]))


def _load_batch_file(path: str) -> List[Point]:
    """Load query points from a CSV or JSON file."""
    if path.endswith('.json'):
        with open(path) as f:
            data = json.load(f)
        return [(p[0], p[1]) for p in data]
    # CSV: each line is X,Y
    points = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) >= 2:
                try:
                    points.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue  # skip header rows
    return points


def run_query_cli(args, data, regions) -> None:
    """Execute query-related CLI arguments.

    Called from ``vormap.main()`` after diagram computation.
    """
    seeds = list(data) if not isinstance(data, list) else data
    # Convert data to list of tuples
    seed_points: List[Point] = [(float(s[0]), float(s[1])) for s in seeds]

    idx = VoronoiIndex(seed_points, regions)

    all_results: List[QueryResult] = []
    all_queries: List[Point] = []

    # Single point query
    if args.query:
        pt = _parse_query_point(args.query)
        k = getattr(args, 'query_k', 1) or 1
        if k == 1:
            r = idx.nearest(pt)
            all_results.append(r)
            all_queries.append(pt)
            print(f'Nearest seed to ({pt[0]}, {pt[1]}): '
                  f'index={r.seed_index}, distance={r.distance:.4f}, '
                  f'coords=({r.seed_coords[0]}, {r.seed_coords[1]})')
        else:
            results = idx.nearest_k(pt, k)
            all_results.extend(results)
            all_queries.extend([pt] * len(results))
            for i, r in enumerate(results):
                print(f'  #{i+1}: index={r.seed_index}, '
                      f'distance={r.distance:.4f}, '
                      f'coords=({r.seed_coords[0]}, {r.seed_coords[1]})')

        # Region location
        loc = idx.locate(pt)
        print(f'Point located in region {loc}')

    # Radius search
    if args.query and getattr(args, 'query_radius', None):
        pt = _parse_query_point(args.query)
        radius = args.query_radius
        results = idx.within_radius(pt, radius)
        print(f'\n{len(results)} seeds within radius {radius} of ({pt[0]}, {pt[1]}):')
        for r in results:
            print(f'  index={r.seed_index}, distance={r.distance:.4f}')

    # Batch query
    if getattr(args, 'query_batch', None):
        points = _load_batch_file(args.query_batch)
        results = idx.batch_query(points)
        all_results.extend(results)
        all_queries.extend(points)
        stats = query_stats(results)
        print(f'\nBatch query: {stats.count} points')
        print(f'  min_distance={stats.min_distance:.4f}')
        print(f'  max_distance={stats.max_distance:.4f}')
        print(f'  mean_distance={stats.mean_distance:.4f}')
        print(f'  median_distance={stats.median_distance:.4f}')

    # JSON export
    if getattr(args, 'query_json', None) and all_results:
        export_query_json(all_results, args.query_json)
        print(f'Query results saved to {args.query_json}')

    # SVG export
    if getattr(args, 'query_svg', None) and all_results:
        export_query_svg(seed_points, all_queries, all_results,
                         args.query_svg)
        print(f'Query SVG saved to {args.query_svg}')
