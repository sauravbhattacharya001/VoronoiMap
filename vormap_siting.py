"""Optimal Facility Siting for Voronoi diagrams.

Suggests optimal locations for placing new facilities (points) based on
existing spatial data, demand points, and coverage constraints.  Uses
Voronoi cell geometry to identify underserved areas and rank candidate
locations.

Three siting strategies are supported:

- **Gap Filling** — places new facilities at the centroids of the
  largest Voronoi cells (underserved areas with sparse coverage).

- **Demand Weighted** — given demand points with weights, finds
  locations that maximise total demand coverage within a service radius.

- **Max-Min Distance** — maximises the minimum distance from any new
  facility to existing facilities (i.e. spread them out evenly).

Usage (Python API)::

    from vormap_siting import (
        find_gap_sites, find_demand_sites, find_maxmin_sites,
        SitingResult, DemandPoint,
    )

    existing = [(100, 200), (400, 300), (700, 500)]
    bounds = (0, 1000, 0, 1000)  # south, north, west, east

    # Gap filling: find the 3 largest Voronoi voids
    result = find_gap_sites(existing, n_sites=3, bounds=bounds)
    for s in result.sites:
        print(f"  ({s.x:.1f}, {s.y:.1f}) — gap area {s.score:.1f}")

    # Demand weighted: place near highest unserved demand
    demand = [DemandPoint(150, 250, 10), DemandPoint(800, 800, 50)]
    result = find_demand_sites(existing, demand, n_sites=2, radius=200)

    # Max-min distance: spread facilities evenly
    result = find_maxmin_sites(existing, n_sites=3, bounds=bounds)

CLI::

    voronoimap datauni5.txt 5 --siting gap --n-sites 3
    voronoimap datauni5.txt 5 --siting demand --demand-file demand.csv --radius 200
    voronoimap datauni5.txt 5 --siting maxmin --n-sites 4
    voronoimap datauni5.txt 5 --siting gap --n-sites 3 --siting-json output.json
"""

import json
import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from vormap_geometry import polygon_area, polygon_centroid


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class DemandPoint:
    """A spatial demand location with a weight."""
    x: float
    y: float
    weight: float = 1.0


@dataclass
class SitingSuggestion:
    """A single suggested facility location."""
    x: float
    y: float
    score: float
    rank: int = 0
    reason: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class SitingResult:
    """Container for siting analysis results."""
    strategy: str
    sites: List[SitingSuggestion]
    existing_count: int
    bounds: Optional[Tuple[float, float, float, float]]
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"=== Facility Siting ({self.strategy}) ===",
            f"Existing facilities: {self.existing_count}",
            f"Suggested sites: {len(self.sites)}",
        ]
        if self.bounds:
            s, n, w, e = self.bounds
            lines.append(f"Bounds: S={s:.1f} N={n:.1f} W={w:.1f} E={e:.1f}")
        lines.append("")
        for s in self.sites:
            lines.append(f"  #{s.rank}: ({s.x:.2f}, {s.y:.2f}) "
                         f"score={s.score:.4f} — {s.reason}")
        if self.stats:
            lines.append("")
            for k, v in self.stats.items():
                lines.append(f"  {k}: {v}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "existing_count": self.existing_count,
            "bounds": self.bounds,
            "sites": [
                {
                    "rank": s.rank, "x": s.x, "y": s.y,
                    "score": s.score, "reason": s.reason,
                    "metadata": s.metadata,
                }
                for s in self.sites
            ],
            "stats": self.stats,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ── Helpers ──────────────────────────────────────────────────────────


def _dist(a, b):
    """Euclidean distance between two points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _bounding_box(points):
    """Compute (south, north, west, east) from a list of (x, y)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    margin = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.1
    return (
        min(ys) - margin, max(ys) + margin,
        min(xs) - margin, max(xs) + margin,
    )


def _clamp(val, lo, hi):
    return max(lo, min(hi, val))


def _simple_voronoi_cells(points, bounds):
    """Approximate Voronoi cells via nearest-point assignment on a grid.

    Returns a dict mapping point index → list of (gx, gy) grid cells.
    This is a simple O(n*g) approach that avoids scipy dependency.
    """
    s, n, w, e = bounds
    grid_size = max(50, min(200, int(math.sqrt(len(points)) * 10)))
    dx = (e - w) / grid_size
    dy = (n - s) / grid_size
    cell_area = dx * dy

    cells = {i: [] for i in range(len(points))}

    for gy in range(grid_size):
        py = s + (gy + 0.5) * dy
        for gx in range(grid_size):
            px = w + (gx + 0.5) * dx
            best_i = 0
            best_d = _dist((px, py), points[0])
            for i in range(1, len(points)):
                d = _dist((px, py), points[i])
                if d < best_d:
                    best_d = d
                    best_i = i
            cells[best_i].append((px, py))

    areas = {}
    centroids = {}
    for i, grid_pts in cells.items():
        areas[i] = len(grid_pts) * cell_area
        if grid_pts:
            cx = sum(p[0] for p in grid_pts) / len(grid_pts)
            cy = sum(p[1] for p in grid_pts) / len(grid_pts)
            centroids[i] = (cx, cy)
        else:
            centroids[i] = points[i]

    return areas, centroids


# ── Siting strategies ───────────────────────────────────────────────


def find_gap_sites(
    existing: List[Tuple[float, float]],
    n_sites: int = 1,
    bounds: Optional[Tuple[float, float, float, float]] = None,
) -> SitingResult:
    """Find optimal sites by filling the largest Voronoi gaps.

    Computes an approximate Voronoi tessellation and ranks cells by
    area (largest = most underserved).  Suggests centroids of the
    biggest cells as new facility locations.

    Parameters
    ----------
    existing : list of (x, y)
        Existing facility locations.
    n_sites : int
        Number of sites to suggest.
    bounds : tuple or None
        (south, north, west, east). Auto-detected if None.

    Returns
    -------
    SitingResult
    """
    if not existing:
        raise ValueError("existing must contain at least one point")
    if n_sites < 1:
        raise ValueError("n_sites must be >= 1")

    bounds = bounds or _bounding_box(existing)
    areas, centroids = _simple_voronoi_cells(existing, bounds)

    # Sort by area descending — largest cells are the biggest gaps
    ranked = sorted(areas.items(), key=lambda kv: kv[1], reverse=True)

    sites = []
    for rank, (idx, area) in enumerate(ranked[:n_sites]):
        cx, cy = centroids[idx]
        sites.append(SitingSuggestion(
            x=cx, y=cy, score=area, rank=rank + 1,
            reason=f"Largest Voronoi gap (cell area={area:.1f})",
            metadata={"cell_index": idx, "cell_area": area},
        ))

    total_area = sum(areas.values())
    mean_area = total_area / len(areas) if areas else 0

    return SitingResult(
        strategy="gap",
        sites=sites,
        existing_count=len(existing),
        bounds=bounds,
        stats={
            "total_area": round(total_area, 2),
            "mean_cell_area": round(mean_area, 2),
            "max_cell_area": round(max(areas.values()), 2) if areas else 0,
            "min_cell_area": round(min(areas.values()), 2) if areas else 0,
            "area_ratio": round(
                max(areas.values()) / min(areas.values()), 2
            ) if areas and min(areas.values()) > 0 else float("inf"),
        },
    )


def find_demand_sites(
    existing: List[Tuple[float, float]],
    demand: List[DemandPoint],
    n_sites: int = 1,
    radius: float = 200.0,
) -> SitingResult:
    """Find sites that maximise coverage of demand points.

    Identifies demand points not adequately served by existing facilities
    (outside ``radius``), clusters them, and suggests locations that
    cover the most unserved demand weight.

    Parameters
    ----------
    existing : list of (x, y)
        Existing facility locations.
    demand : list of DemandPoint
        Demand locations with weights.
    n_sites : int
        Number of sites to suggest.
    radius : float
        Service radius — demand within this distance is "served".

    Returns
    -------
    SitingResult
    """
    if not existing:
        raise ValueError("existing must contain at least one point")
    if not demand:
        raise ValueError("demand must contain at least one point")
    if n_sites < 1:
        raise ValueError("n_sites must be >= 1")
    if radius <= 0:
        raise ValueError("radius must be > 0")

    # Identify unserved demand
    unserved = []
    total_demand = sum(d.weight for d in demand)
    served_weight = 0.0

    for d in demand:
        min_dist = min(_dist((d.x, d.y), e) for e in existing)
        if min_dist > radius:
            unserved.append(d)
        else:
            served_weight += d.weight

    if not unserved:
        return SitingResult(
            strategy="demand",
            sites=[],
            existing_count=len(existing),
            bounds=None,
            stats={
                "total_demand": total_demand,
                "served_demand": total_demand,
                "unserved_demand": 0,
                "coverage_pct": 100.0,
                "message": "All demand is already served",
            },
        )

    # Greedy: pick location that covers most unserved demand weight
    sites = []
    placed = list(existing)
    remaining = list(unserved)

    for rank in range(n_sites):
        if not remaining:
            break

        # Candidate = weighted centroid of remaining unserved demand
        best_loc = None
        best_coverage = 0.0
        best_covered = []

        # Try centroid of all remaining
        total_w = sum(d.weight for d in remaining)
        cx = sum(d.x * d.weight for d in remaining) / total_w
        cy = sum(d.y * d.weight for d in remaining) / total_w
        candidate = (cx, cy)

        covered = [d for d in remaining
                    if _dist((d.x, d.y), candidate) <= radius]
        coverage = sum(d.weight for d in covered)

        if coverage > best_coverage:
            best_loc = candidate
            best_coverage = coverage
            best_covered = covered

        # Also try each unserved point as a potential site
        for d in remaining:
            candidate = (d.x, d.y)
            covered = [dd for dd in remaining
                        if _dist((dd.x, dd.y), candidate) <= radius]
            cov = sum(dd.weight for dd in covered)
            if cov > best_coverage:
                best_loc = candidate
                best_coverage = cov
                best_covered = covered

        if best_loc is None:
            break

        sites.append(SitingSuggestion(
            x=best_loc[0], y=best_loc[1],
            score=best_coverage, rank=rank + 1,
            reason=f"Covers {best_coverage:.1f} unserved demand weight",
            metadata={
                "demand_covered": best_coverage,
                "points_covered": len(best_covered),
            },
        ))

        placed.append(best_loc)
        remaining = [d for d in remaining if d not in best_covered]

    unserved_weight = sum(d.weight for d in unserved)
    newly_served = sum(s.score for s in sites)

    return SitingResult(
        strategy="demand",
        sites=sites,
        existing_count=len(existing),
        bounds=None,
        stats={
            "total_demand": round(total_demand, 2),
            "initially_served": round(served_weight, 2),
            "unserved_demand": round(unserved_weight, 2),
            "newly_served": round(newly_served, 2),
            "final_coverage_pct": round(
                (served_weight + newly_served) / total_demand * 100, 1
            ) if total_demand > 0 else 0,
        },
    )


def find_maxmin_sites(
    existing: List[Tuple[float, float]],
    n_sites: int = 1,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    n_candidates: int = 500,
    seed: Optional[int] = None,
) -> SitingResult:
    """Find sites that maximise the minimum distance to existing points.

    Generates candidate locations and selects those with the greatest
    minimum distance to any existing facility — i.e. places new sites
    in the most "remote" areas for even spatial distribution.

    Parameters
    ----------
    existing : list of (x, y)
        Existing facility locations.
    n_sites : int
        Number of sites to suggest.
    bounds : tuple or None
        (south, north, west, east). Auto-detected if None.
    n_candidates : int
        Number of random candidate locations to evaluate.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    SitingResult
    """
    if not existing:
        raise ValueError("existing must contain at least one point")
    if n_sites < 1:
        raise ValueError("n_sites must be >= 1")

    bounds = bounds or _bounding_box(existing)
    s, n, w, e = bounds

    rng = random.Random(seed)
    placed = list(existing)
    sites = []

    for rank in range(n_sites):
        candidates = [
            (rng.uniform(w, e), rng.uniform(s, n))
            for _ in range(n_candidates)
        ]

        best_loc = None
        best_min_dist = -1.0

        for cx, cy in candidates:
            min_d = min(_dist((cx, cy), p) for p in placed)
            if min_d > best_min_dist:
                best_min_dist = min_d
                best_loc = (cx, cy)

        if best_loc is None:
            break

        sites.append(SitingSuggestion(
            x=best_loc[0], y=best_loc[1],
            score=best_min_dist, rank=rank + 1,
            reason=f"Max min-distance to nearest existing: {best_min_dist:.1f}",
            metadata={"min_distance": round(best_min_dist, 2)},
        ))
        placed.append(best_loc)

    return SitingResult(
        strategy="maxmin",
        sites=sites,
        existing_count=len(existing),
        bounds=bounds,
        stats={
            "candidates_evaluated": n_candidates,
            "avg_min_distance": round(
                sum(s.score for s in sites) / len(sites), 2
            ) if sites else 0,
        },
    )


# ── CLI ──────────────────────────────────────────────────────────────


def main(argv=None):
    """CLI entry point for siting analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Optimal facility siting for Voronoi point data",
    )
    parser.add_argument(
        "strategy", choices=["gap", "demand", "maxmin"],
        help="Siting strategy",
    )
    parser.add_argument(
        "--points", required=True,
        help="Comma-separated existing points: x1,y1,x2,y2,...",
    )
    parser.add_argument(
        "--n-sites", type=int, default=3,
        help="Number of sites to suggest (default: 3)",
    )
    parser.add_argument(
        "--bounds", default=None,
        help="Bounds: south,north,west,east",
    )
    parser.add_argument(
        "--radius", type=float, default=200.0,
        help="Service radius for demand strategy (default: 200)",
    )
    parser.add_argument(
        "--demand", default=None,
        help="Demand points: x1,y1,w1,x2,y2,w2,...",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for maxmin strategy",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output as JSON",
    )

    args = parser.parse_args(argv)

    # Parse points
    coords = [float(v) for v in args.points.split(",")]
    if len(coords) % 2 != 0:
        parser.error("--points must have an even number of values")
    existing = [(coords[i], coords[i + 1]) for i in range(0, len(coords), 2)]

    bounds = None
    if args.bounds:
        bvals = [float(v) for v in args.bounds.split(",")]
        if len(bvals) != 4:
            parser.error("--bounds must have exactly 4 values")
        bounds = tuple(bvals)

    if args.strategy == "gap":
        result = find_gap_sites(existing, n_sites=args.n_sites, bounds=bounds)
    elif args.strategy == "demand":
        if not args.demand:
            parser.error("--demand required for demand strategy")
        dvals = [float(v) for v in args.demand.split(",")]
        if len(dvals) % 3 != 0:
            parser.error("--demand must have values in groups of 3 (x,y,w)")
        demand = [
            DemandPoint(dvals[i], dvals[i + 1], dvals[i + 2])
            for i in range(0, len(dvals), 3)
        ]
        result = find_demand_sites(
            existing, demand, n_sites=args.n_sites, radius=args.radius
        )
    elif args.strategy == "maxmin":
        result = find_maxmin_sites(
            existing, n_sites=args.n_sites, bounds=bounds, seed=args.seed
        )
    else:
        parser.error(f"Unknown strategy: {args.strategy}")
        return

    if args.json_output:
        print(result.to_json())
    else:
        print(result.summary())


if __name__ == "__main__":
    main()
