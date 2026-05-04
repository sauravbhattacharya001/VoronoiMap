#!/usr/bin/env python3
"""Spatial Hydrology Engine -- autonomous water flow & flood simulation on Voronoi tessellations.

Models hydrological systems across Voronoi cells: elevation-driven terrain,
drainage networks, precipitation collection, flow accumulation, flood risk,
and groundwater recharge -- enabling spatial watershed analysis on arbitrary
point sets.

Seven analysis engines:

- **Elevation Field Engine** -- Assigns elevation from position-based
  terrain generation (y-gradient, distance-from-center mountain effect,
  neighbor smoothing).  Classifies terrain: peak / ridge / slope / valley
  / basin.
- **Drainage Network Engine** -- Determines steepest-descent flow
  direction per cell.  Builds directed drainage graph.  Delineates
  drainage basins via sink tracing.  Computes Strahler stream order.
- **Precipitation Collector Engine** -- Assigns precipitation per cell
  using orographic effect (elevation boost), position variability, and
  randomness.  Computes effective runoff via terrain-based coefficient.
- **Flow Accumulation Engine** -- Traces water downstream through the
  drainage network.  Each cell accumulates its own runoff plus all
  upstream contributions.  Identifies major flow corridors.
- **Flood Risk Engine** -- Scores flood risk 0-100 per cell from flow
  accumulation, low elevation, upstream contributor count, and
  convergence proximity.  Classifies: safe / watch / warning / danger
  / critical.
- **Aquifer Recharge Engine** -- Estimates groundwater recharge
  potential from permeability proxy, precipitation, slope, and
  vegetation proxy.  Classifies: excellent / good / moderate / poor
  / negligible.
- **Autonomous Insight Generator** -- Composite hydrology health score
  0-100 (drainage efficiency, flood risk, aquifer coverage, flow
  connectivity).  Natural-language insights about watershed health.

Usage (Python API)::

    from vormap_hydrology import HydrologyEngine, hydrology_analyze, hydrology_demo

    result = hydrology_analyze("points.txt")
    print(f"Health: {result.health_score:.1f}/100")

    engine = HydrologyEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze()
    engine.to_html("hydrology.html")

    hydrology_demo()

CLI::

    python vormap_hydrology.py points.txt
    python vormap_hydrology.py points.txt --json out.json --html dash.html
    python vormap_hydrology.py --demo
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import os
import random
import sys
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CellHydrology:
    """Hydrological profile for a single spatial cell."""
    cell_id: int
    x: float
    y: float
    elevation: float = 0.0
    terrain_type: str = "slope"
    precipitation: float = 0.0
    runoff: float = 0.0
    flow_direction: int = -1  # neighbor cell_id or -1 for sink
    flow_accumulation: float = 0.0
    strahler_order: int = 0
    flood_risk: float = 0.0
    flood_class: str = "safe"
    recharge_potential: float = 0.0
    recharge_class: str = "moderate"
    basin_id: int = 0
    upstream_count: int = 0


@dataclass
class DrainageBasin:
    """A drainage basin grouping cells that flow to the same outlet."""
    basin_id: int
    cells: List[int] = field(default_factory=list)
    outlet_cell: int = 0
    total_area: float = 0.0
    total_precipitation: float = 0.0
    total_runoff: float = 0.0
    max_strahler_order: int = 0


@dataclass
class FlowCorridor:
    """A major flow corridor cell."""
    cell_id: int
    accumulation: float = 0.0
    strahler_order: int = 0
    is_confluence: bool = False


@dataclass
class HydrologyResult:
    """Full hydrology analysis result."""
    cells: List[CellHydrology] = field(default_factory=list)
    basins: List[DrainageBasin] = field(default_factory=list)
    flow_corridors: List[FlowCorridor] = field(default_factory=list)
    avg_elevation: float = 0.0
    total_precipitation: float = 0.0
    total_runoff: float = 0.0
    avg_flood_risk: float = 0.0
    avg_recharge: float = 0.0
    num_basins: int = 0
    health_score: float = 0.0
    insights: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _load_points(path: str) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    with open(path, "r") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _knn_adjacency(
    points: List[Tuple[float, float]], k: int = 6
) -> Dict[int, List[int]]:
    n = len(points)
    k = min(k, n - 1)
    adj: Dict[int, List[int]] = {i: [] for i in range(n)}
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append((j, _euclidean(points[i], points[j])))
        dists.sort(key=lambda t: t[1])
        for j, _ in dists[:k]:
            if j not in adj[i]:
                adj[i].append(j)
            if i not in adj[j]:
                adj[j].append(i)
    return adj


def _voronoi_areas(
    points: List[Tuple[float, float]], adj: Dict[int, List[int]]
) -> List[float]:
    n = len(points)
    areas: List[float] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if len(nbrs) < 3:
            areas.append(1.0)
            continue
        mids = []
        for j in nbrs:
            mx = (points[i][0] + points[j][0]) / 2.0
            my = (points[i][1] + points[j][1]) / 2.0
            mids.append((mx, my))
        cx = sum(m[0] for m in mids) / len(mids)
        cy = sum(m[1] for m in mids) / len(mids)
        mids.sort(key=lambda m: math.atan2(m[1] - cy, m[0] - cx))
        area = 0.0
        for idx in range(len(mids)):
            x1, y1 = mids[idx]
            x2, y2 = mids[(idx + 1) % len(mids)]
            area += x1 * y2 - x2 * y1
        areas.append(abs(area) / 2.0)
    return areas


def _normalize(values: List[float]) -> List[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


# ---------------------------------------------------------------------------
# Engine 1: Elevation Field
# ---------------------------------------------------------------------------


def _compute_elevation(
    points: List[Tuple[float, float]],
    adj: Dict[int, List[int]],
    areas: List[float],
    rng: random.Random,
) -> List[float]:
    """Assign elevation using y-gradient, center-distance mountain, and smoothing."""
    n = len(points)
    if n == 0:
        return []

    # Bounding box
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)

    raw: List[float] = []
    for i in range(n):
        # Y-gradient: higher y = higher elevation
        y_comp = (points[i][1] - min(ys)) / span * 40.0
        # Distance from center: closer = higher (mountain peak effect)
        dist_c = _euclidean(points[i], (cx, cy)) / span
        mount_comp = (1.0 - dist_c) * 30.0
        # Area influence: smaller cells = ridges (higher)
        area_norm = areas[i] / (max(areas) if max(areas) > 0 else 1.0)
        area_comp = (1.0 - area_norm) * 15.0
        # Random variation
        noise = rng.uniform(-5, 5)
        raw.append(y_comp + mount_comp + area_comp + noise)

    # Neighbor smoothing (one pass)
    smoothed: List[float] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if nbrs:
            avg_nb = sum(raw[j] for j in nbrs) / len(nbrs)
            smoothed.append(raw[i] * 0.7 + avg_nb * 0.3)
        else:
            smoothed.append(raw[i])

    return smoothed


def _classify_terrain(
    elevations: List[float],
    adj: Dict[int, List[int]],
) -> List[str]:
    """Classify each cell's terrain type."""
    n = len(elevations)
    types: List[str] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if not nbrs:
            types.append("slope")
            continue
        higher = sum(1 for j in nbrs if elevations[j] > elevations[i])
        lower = sum(1 for j in nbrs if elevations[j] < elevations[i])
        total = len(nbrs)

        if higher == 0:
            types.append("peak")
        elif lower == 0:
            types.append("basin")
        elif higher <= total * 0.25:
            types.append("ridge")
        elif lower <= total * 0.25:
            types.append("valley")
        else:
            types.append("slope")
    return types


# ---------------------------------------------------------------------------
# Engine 2: Drainage Network
# ---------------------------------------------------------------------------


def _build_drainage(
    points: List[Tuple[float, float]],
    elevations: List[float],
    adj: Dict[int, List[int]],
) -> Tuple[List[int], Dict[int, List[int]]]:
    """Build flow direction (steepest descent) and upstream adjacency.

    Returns (flow_dir, upstream) where flow_dir[i] = downstream neighbor
    or -1 for sinks, and upstream[i] = list of cells flowing into i.
    """
    n = len(points)
    flow_dir: List[int] = [-1] * n
    upstream: Dict[int, List[int]] = {i: [] for i in range(n)}

    for i in range(n):
        best_j = -1
        best_slope = 0.0
        for j in adj.get(i, []):
            dist = _euclidean(points[i], points[j])
            if dist < 1e-12:
                continue
            slope = (elevations[i] - elevations[j]) / dist
            if slope > best_slope:
                best_slope = slope
                best_j = j
        flow_dir[i] = best_j
        if best_j >= 0:
            upstream[best_j].append(i)

    return flow_dir, upstream


def _delineate_basins(
    flow_dir: List[int], n: int
) -> Tuple[List[int], Dict[int, int]]:
    """Trace each cell to its terminal sink. Returns (basin_id_per_cell, {sink_cell: basin_id})."""
    basin_of: List[int] = [-1] * n
    sink_to_basin: Dict[int, int] = {}
    next_basin = 0

    for i in range(n):
        if basin_of[i] >= 0:
            continue
        # Trace downstream
        path: List[int] = []
        visited_set: set = set()
        cur = i
        while cur >= 0 and cur not in visited_set:
            if basin_of[cur] >= 0:
                # Already assigned -- use that basin
                bid = basin_of[cur]
                for c in path:
                    basin_of[c] = bid
                path = []
                break
            visited_set.add(cur)
            path.append(cur)
            cur = flow_dir[cur]
        else:
            # cur is -1 (sink with flow_dir=-1) or cycle (self-loop)
            # The last cell in path is the sink
            if path:
                sink_cell = path[-1]
                if sink_cell in sink_to_basin:
                    bid = sink_to_basin[sink_cell]
                else:
                    bid = next_basin
                    sink_to_basin[sink_cell] = bid
                    next_basin += 1
                for c in path:
                    basin_of[c] = bid

        # Handle any remaining unassigned in path
        if path and basin_of[path[0]] < 0:
            sink_cell = path[-1]
            if sink_cell in sink_to_basin:
                bid = sink_to_basin[sink_cell]
            else:
                bid = next_basin
                sink_to_basin[sink_cell] = bid
                next_basin += 1
            for c in path:
                basin_of[c] = bid

    # Fallback for any still unassigned
    for i in range(n):
        if basin_of[i] < 0:
            basin_of[i] = 0

    return basin_of, sink_to_basin


def _compute_strahler(
    upstream: Dict[int, List[int]], n: int
) -> List[int]:
    """Compute Strahler stream order via bottom-up traversal."""
    order: List[int] = [0] * n

    # Find sources (no upstream)
    in_degree = {i: len(upstream.get(i, [])) for i in range(n)}
    processed = [False] * n
    queue: deque = deque()
    # Start from headwaters (cells with no upstream)
    for i in range(n):
        if not upstream.get(i, []):
            order[i] = 1
            queue.append(i)
            processed[i] = True

    # Process in topological order -- but we need downstream traversal
    # Actually Strahler is computed bottom-up, so let's do it recursively
    # with memoization
    order = [0] * n

    def _strahler(cell: int, depth: int = 0) -> int:
        if depth > n:
            return 1  # cycle guard
        if order[cell] > 0:
            return order[cell]
        ups = upstream.get(cell, [])
        if not ups:
            order[cell] = 1
            return 1
        child_orders = [_strahler(u, depth + 1) for u in ups]
        max_ord = max(child_orders)
        count_max = sum(1 for o in child_orders if o == max_ord)
        if count_max >= 2:
            order[cell] = max_ord + 1
        else:
            order[cell] = max_ord
        return order[cell]

    for i in range(n):
        if order[i] == 0:
            _strahler(i)

    return order


# ---------------------------------------------------------------------------
# Engine 3: Precipitation Collector
# ---------------------------------------------------------------------------


def _compute_precipitation(
    elevations: List[float],
    areas: List[float],
    terrain_types: List[str],
    rng: random.Random,
) -> Tuple[List[float], List[float]]:
    """Compute precipitation and runoff per cell.

    Returns (precipitation, runoff).
    """
    n = len(elevations)
    if n == 0:
        return [], []

    norm_elev = _normalize(elevations)
    precip: List[float] = []

    for i in range(n):
        # Base precipitation 20-80mm
        base = 40.0
        # Orographic boost: higher elevation = more rain
        orographic = norm_elev[i] * 30.0
        # Random variation
        noise = rng.uniform(-10, 10)
        precip.append(max(5.0, base + orographic + noise))

    # Runoff coefficient based on terrain
    coeff_map = {
        "peak": 0.85,
        "ridge": 0.75,
        "slope": 0.55,
        "valley": 0.35,
        "basin": 0.25,
    }

    runoff: List[float] = []
    for i in range(n):
        c = coeff_map.get(terrain_types[i], 0.5)
        runoff.append(precip[i] * c)

    return precip, runoff


# ---------------------------------------------------------------------------
# Engine 4: Flow Accumulation
# ---------------------------------------------------------------------------


def _compute_flow_accumulation(
    flow_dir: List[int],
    upstream: Dict[int, List[int]],
    runoff: List[float],
    n: int,
) -> Tuple[List[float], List[int]]:
    """Accumulate flow downstream. Returns (accumulation, upstream_count)."""
    accum: List[float] = [0.0] * n
    up_count: List[int] = [0] * n

    def _accumulate(cell: int, depth: int = 0) -> Tuple[float, int]:
        if depth > n:
            return runoff[cell] if cell < len(runoff) else 0.0, 0
        if accum[cell] > 0:
            return accum[cell], up_count[cell]
        ups = upstream.get(cell, [])
        total = runoff[cell] if cell < len(runoff) else 0.0
        count = 0
        for u in ups:
            a, c = _accumulate(u, depth + 1)
            total += a
            count += c + 1
        accum[cell] = total
        up_count[cell] = count
        return total, count

    for i in range(n):
        if accum[i] == 0:
            _accumulate(i)

    return accum, up_count


# ---------------------------------------------------------------------------
# Engine 5: Flood Risk
# ---------------------------------------------------------------------------


def _compute_flood_risk(
    elevations: List[float],
    flow_accum: List[float],
    up_counts: List[int],
    adj: Dict[int, List[int]],
    upstream: Dict[int, List[int]],
) -> Tuple[List[float], List[str]]:
    """Score flood risk 0-100 and classify."""
    n = len(elevations)
    if n == 0:
        return [], []

    norm_elev = _normalize(elevations)
    norm_accum = _normalize(flow_accum)
    norm_up = _normalize([float(c) for c in up_counts])

    risks: List[float] = []
    classes: List[str] = []

    for i in range(n):
        # Low elevation = more risk
        elev_risk = (1.0 - norm_elev[i]) * 30.0
        # High accumulation = more risk
        accum_risk = norm_accum[i] * 40.0
        # Many upstream = more risk
        upstream_risk = norm_up[i] * 15.0
        # Convergence: multiple upstream neighbors
        convergence = min(len(upstream.get(i, [])), 5) / 5.0 * 15.0

        risk = min(100.0, max(0.0, elev_risk + accum_risk + upstream_risk + convergence))
        risks.append(risk)

        if risk >= 80:
            classes.append("critical")
        elif risk >= 60:
            classes.append("danger")
        elif risk >= 40:
            classes.append("warning")
        elif risk >= 20:
            classes.append("watch")
        else:
            classes.append("safe")

    return risks, classes


# ---------------------------------------------------------------------------
# Engine 6: Aquifer Recharge
# ---------------------------------------------------------------------------


def _compute_recharge(
    elevations: List[float],
    precipitation: List[float],
    terrain_types: List[str],
    adj: Dict[int, List[int]],
    rng: random.Random,
) -> Tuple[List[float], List[str]]:
    """Estimate groundwater recharge potential 0-100."""
    n = len(elevations)
    if n == 0:
        return [], []

    norm_precip = _normalize(precipitation)

    # Permeability proxy from terrain
    perm_map = {
        "basin": 0.9,
        "valley": 0.75,
        "slope": 0.5,
        "ridge": 0.3,
        "peak": 0.15,
    }

    # Slope proxy: elevation difference with neighbors
    slopes: List[float] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if not nbrs:
            slopes.append(0.5)
            continue
        avg_diff = sum(abs(elevations[i] - elevations[j]) for j in nbrs) / len(nbrs)
        slopes.append(avg_diff)

    norm_slopes = _normalize(slopes)

    potentials: List[float] = []
    classes: List[str] = []

    for i in range(n):
        perm = perm_map.get(terrain_types[i], 0.5)
        # Permeability: 30%
        perm_score = perm * 30.0
        # Precipitation: 25%
        precip_score = norm_precip[i] * 25.0
        # Flat terrain (low slope) = more infiltration: 25%
        flatness = (1.0 - norm_slopes[i]) * 25.0
        # Vegetation proxy (mid-elevation has most vegetation): 20%
        norm_e = _normalize(elevations)
        veg = (1.0 - abs(norm_e[i] - 0.4)) * 20.0

        potential = min(100.0, max(0.0, perm_score + precip_score + flatness + veg))
        potentials.append(potential)

        if potential >= 80:
            classes.append("excellent")
        elif potential >= 60:
            classes.append("good")
        elif potential >= 40:
            classes.append("moderate")
        elif potential >= 20:
            classes.append("poor")
        else:
            classes.append("negligible")

    return potentials, classes


# ---------------------------------------------------------------------------
# Engine 7: Autonomous Insight Generator
# ---------------------------------------------------------------------------


def _generate_insights(
    cells: List[CellHydrology],
    basins: List[DrainageBasin],
    flow_corridors: List[FlowCorridor],
    health_score: float,
) -> List[str]:
    """Generate natural-language hydrology insights."""
    insights: List[str] = []
    n = len(cells)
    if n == 0:
        return ["No cells to analyze."]

    # Basin insights
    insights.append(f"Watershed contains {len(basins)} drainage basin(s) across {n} cells.")
    if basins:
        largest = max(basins, key=lambda b: len(b.cells))
        insights.append(
            f"Largest basin (#{largest.basin_id}) covers {len(largest.cells)} cells "
            f"with Strahler order {largest.max_strahler_order}."
        )

    # Flood risk distribution
    flood_classes = [c.flood_class for c in cells]
    critical = flood_classes.count("critical")
    danger = flood_classes.count("danger")
    if critical > 0:
        insights.append(
            f"⚠ {critical} cell(s) at CRITICAL flood risk -- "
            f"immediate mitigation recommended."
        )
    if danger > 0:
        insights.append(f"{danger} cell(s) at DANGER flood risk level.")

    safe_pct = flood_classes.count("safe") / n * 100 if n > 0 else 0
    insights.append(f"{safe_pct:.0f}% of cells classified as safe from flooding.")

    # Recharge insights
    recharge_classes = [c.recharge_class for c in cells]
    excellent = recharge_classes.count("excellent")
    if excellent > 0:
        insights.append(
            f"{excellent} cell(s) have excellent aquifer recharge potential."
        )
    negligible = recharge_classes.count("negligible")
    if negligible > 0:
        insights.append(
            f"{negligible} cell(s) have negligible recharge -- "
            f"consider permeable surface interventions."
        )

    # Flow corridor insights
    if flow_corridors:
        confluences = [c for c in flow_corridors if c.is_confluence]
        insights.append(
            f"{len(flow_corridors)} major flow corridor(s) identified, "
            f"{len(confluences)} confluence point(s)."
        )

    # Terrain distribution
    terrain_counts: Dict[str, int] = {}
    for c in cells:
        terrain_counts[c.terrain_type] = terrain_counts.get(c.terrain_type, 0) + 1
    dominant = max(terrain_counts, key=lambda k: terrain_counts[k])
    insights.append(f"Dominant terrain type: {dominant} ({terrain_counts[dominant]} cells).")

    # Health commentary
    if health_score >= 80:
        insights.append("Excellent watershed health -- well-distributed drainage and low flood risk.")
    elif health_score >= 60:
        insights.append("Good watershed health with minor areas of concern.")
    elif health_score >= 40:
        insights.append("Moderate watershed health -- attention needed for flood-prone areas.")
    elif health_score >= 20:
        insights.append("Poor watershed health -- significant flood risks and drainage issues.")
    else:
        insights.append("Critical watershed health -- major hydrological interventions needed.")

    return insights


def _compute_health_score(
    cells: List[CellHydrology],
    basins: List[DrainageBasin],
    flow_corridors: List[FlowCorridor],
) -> float:
    """Compute composite hydrology health score 0-100."""
    n = len(cells)
    if n == 0:
        return 50.0

    # 1. Drainage efficiency (25%): ratio of non-sink cells (connected drainage)
    connected = sum(1 for c in cells if c.flow_direction >= 0)
    drainage_eff = connected / n * 100.0

    # 2. Flood risk distribution (30%): lower average flood risk = healthier
    avg_risk = sum(c.flood_risk for c in cells) / n
    flood_score = max(0.0, 100.0 - avg_risk)

    # 3. Aquifer coverage (25%): higher average recharge = healthier
    avg_recharge = sum(c.recharge_potential for c in cells) / n
    recharge_score = avg_recharge

    # 4. Flow network connectivity (20%): basins with good Strahler order
    max_strahler = max((b.max_strahler_order for b in basins), default=1)
    connectivity = min(100.0, max_strahler / max(1, math.log2(n + 1)) * 100.0)

    health = (
        drainage_eff * 0.25
        + flood_score * 0.30
        + recharge_score * 0.25
        + connectivity * 0.20
    )
    return min(100.0, max(0.0, health))


# ---------------------------------------------------------------------------
# Main Engine Class
# ---------------------------------------------------------------------------


class HydrologyEngine:
    """Spatial Hydrology Engine -- autonomous water flow & flood simulation."""

    def __init__(
        self,
        path: Optional[str] = None,
        points: Optional[List[Tuple[float, float]]] = None,
        adj_k: int = 6,
        seed: int = 42,
    ):
        if path:
            self._points = _load_points(path)
        elif points:
            self._points = list(points)
        else:
            self._points = []
        self._adj_k = adj_k
        self._rng = random.Random(seed)
        self._result: Optional[HydrologyResult] = None

    def analyze(self) -> HydrologyResult:
        """Run all 7 analysis engines."""
        pts = self._points
        n = len(pts)

        if n == 0:
            self._result = HydrologyResult(health_score=50.0, insights=["No points provided."])
            return self._result

        # Build adjacency and areas
        adj = _knn_adjacency(pts, self._adj_k)
        areas = _voronoi_areas(pts, adj)

        # Engine 1: Elevation
        elevations = _compute_elevation(pts, adj, areas, self._rng)
        terrain_types = _classify_terrain(elevations, adj)

        # Engine 2: Drainage Network
        flow_dir, upstream = _build_drainage(pts, elevations, adj)
        basin_ids, sink_map = _delineate_basins(flow_dir, n)
        strahler = _compute_strahler(upstream, n)

        # Engine 3: Precipitation
        precip, runoff = _compute_precipitation(elevations, areas, terrain_types, self._rng)

        # Engine 4: Flow Accumulation
        flow_accum, up_counts = _compute_flow_accumulation(flow_dir, upstream, runoff, n)

        # Engine 5: Flood Risk
        flood_risks, flood_classes = _compute_flood_risk(
            elevations, flow_accum, up_counts, adj, upstream
        )

        # Engine 6: Aquifer Recharge
        recharge_pots, recharge_cls = _compute_recharge(
            elevations, precip, terrain_types, adj, self._rng
        )

        # Build cell objects
        cells: List[CellHydrology] = []
        for i in range(n):
            cells.append(CellHydrology(
                cell_id=i,
                x=pts[i][0],
                y=pts[i][1],
                elevation=elevations[i],
                terrain_type=terrain_types[i],
                precipitation=precip[i],
                runoff=runoff[i],
                flow_direction=flow_dir[i],
                flow_accumulation=flow_accum[i],
                strahler_order=strahler[i],
                flood_risk=flood_risks[i],
                flood_class=flood_classes[i],
                recharge_potential=recharge_pots[i],
                recharge_class=recharge_cls[i],
                basin_id=basin_ids[i],
                upstream_count=up_counts[i],
            ))

        # Build basin objects
        basins_dict: Dict[int, DrainageBasin] = {}
        for i in range(n):
            bid = basin_ids[i]
            if bid not in basins_dict:
                # Find outlet (sink) for this basin
                outlet = i
                for sink_cell, sbid in sink_map.items():
                    if sbid == bid:
                        outlet = sink_cell
                        break
                basins_dict[bid] = DrainageBasin(
                    basin_id=bid,
                    outlet_cell=outlet,
                )
            basins_dict[bid].cells.append(i)
            basins_dict[bid].total_area += areas[i]
            basins_dict[bid].total_precipitation += precip[i]
            basins_dict[bid].total_runoff += runoff[i]
            basins_dict[bid].max_strahler_order = max(
                basins_dict[bid].max_strahler_order, strahler[i]
            )

        basins = sorted(basins_dict.values(), key=lambda b: len(b.cells), reverse=True)

        # Build flow corridors (top cells by accumulation)
        threshold = sorted(flow_accum, reverse=True)[min(n - 1, max(0, n // 4))] if n > 0 else 0
        corridors: List[FlowCorridor] = []
        for i in range(n):
            if flow_accum[i] >= threshold and flow_accum[i] > 0:
                is_conf = len(upstream.get(i, [])) >= 2
                corridors.append(FlowCorridor(
                    cell_id=i,
                    accumulation=flow_accum[i],
                    strahler_order=strahler[i],
                    is_confluence=is_conf,
                ))
        corridors.sort(key=lambda c: c.accumulation, reverse=True)

        # Aggregates
        avg_elev = sum(elevations) / n if n > 0 else 0
        total_precip = sum(precip)
        total_runoff_val = sum(runoff)
        avg_flood = sum(flood_risks) / n if n > 0 else 0
        avg_rech = sum(recharge_pots) / n if n > 0 else 0

        # Engine 7: Health + Insights
        health = _compute_health_score(cells, basins, corridors)
        insights = _generate_insights(cells, basins, corridors, health)

        self._result = HydrologyResult(
            cells=cells,
            basins=basins,
            flow_corridors=corridors,
            avg_elevation=avg_elev,
            total_precipitation=total_precip,
            total_runoff=total_runoff_val,
            avg_flood_risk=avg_flood,
            avg_recharge=avg_rech,
            num_basins=len(basins),
            health_score=health,
            insights=insights,
        )
        return self._result

    def to_json(self, path: str) -> None:
        """Export result to JSON."""
        if not self._result:
            self.analyze()
        r = self._result
        assert r is not None
        data = {
            "health_score": r.health_score,
            "avg_elevation": r.avg_elevation,
            "total_precipitation": r.total_precipitation,
            "total_runoff": r.total_runoff,
            "avg_flood_risk": r.avg_flood_risk,
            "avg_recharge": r.avg_recharge,
            "num_basins": r.num_basins,
            "insights": r.insights,
            "cells": [asdict(c) for c in r.cells],
            "basins": [asdict(b) for b in r.basins],
            "flow_corridors": [asdict(fc) for fc in r.flow_corridors],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)

    def to_html(self, path: str) -> None:
        """Export interactive HTML dashboard."""
        if not self._result:
            self.analyze()
        r = self._result
        assert r is not None

        # Gauge SVG
        pct = r.health_score / 100.0
        angle = 180 + pct * 180
        rad = math.radians(angle)
        nx = 50 + 35 * math.cos(rad)
        ny = 55 + 35 * math.sin(rad)
        color = "#27ae60" if r.health_score >= 70 else "#f39c12" if r.health_score >= 40 else "#e74c3c"
        gauge_svg = (
            f'<svg width="120" height="80" viewBox="0 0 100 70">'
            f'<path d="M15 55 A35 35 0 0 1 85 55" fill="none" stroke="#eee" stroke-width="8"/>'
            f'<path d="M15 55 A35 35 0 0 1 85 55" fill="none" stroke="{color}" '
            f'stroke-width="8" stroke-dasharray="{pct * 110:.0f} 110"/>'
            f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="4" fill="{color}"/>'
            f'<text x="50" y="52" text-anchor="middle" font-size="14" '
            f'font-weight="bold" fill="{color}">{r.health_score:.0f}</text>'
            f'<text x="50" y="65" text-anchor="middle" font-size="8" fill="#888">Health</text>'
            f'</svg>'
        )

        # Cell rows
        rows_cells = ""
        for c in r.cells:
            rows_cells += (
                f"<tr><td>{c.cell_id}</td><td>{c.x:.1f}</td><td>{c.y:.1f}</td>"
                f"<td>{c.elevation:.1f}</td><td>{c.terrain_type}</td>"
                f"<td>{c.precipitation:.1f}</td><td>{c.runoff:.1f}</td>"
                f"<td>{c.flow_direction}</td><td>{c.flow_accumulation:.1f}</td>"
                f"<td>{c.strahler_order}</td><td>{c.flood_risk:.1f}</td>"
                f"<td>{html_mod.escape(c.flood_class)}</td>"
                f"<td>{c.recharge_potential:.1f}</td>"
                f"<td>{html_mod.escape(c.recharge_class)}</td>"
                f"<td>{c.basin_id}</td></tr>\n"
            )

        # Basin rows
        rows_basins = ""
        for b in r.basins:
            rows_basins += (
                f"<tr><td>{b.basin_id}</td><td>{len(b.cells)}</td>"
                f"<td>{b.outlet_cell}</td><td>{b.total_area:.1f}</td>"
                f"<td>{b.total_precipitation:.1f}</td><td>{b.total_runoff:.1f}</td>"
                f"<td>{b.max_strahler_order}</td></tr>\n"
            )

        # Corridor rows (top 20)
        rows_corridors = ""
        for fc in r.flow_corridors[:20]:
            rows_corridors += (
                f"<tr><td>{fc.cell_id}</td><td>{fc.accumulation:.1f}</td>"
                f"<td>{fc.strahler_order}</td>"
                f"<td>{'Yes' if fc.is_confluence else 'No'}</td></tr>\n"
            )

        insights_html = "".join(
            f"<li>{html_mod.escape(i)}</li>" for i in r.insights
        )

        html_content = f"""\
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Spatial Hydrology Dashboard</title>
<style>
body{{font-family:system-ui,sans-serif;margin:20px;background:#f5f5f5}}
h1{{color:#2c3e50}} h2{{color:#34495e;margin-top:0}}
.stats{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}}
.stat{{background:#fff;border-radius:8px;padding:16px 20px;box-shadow:0 1px 3px rgba(0,0,0,.1);text-align:center;min-width:100px}}
.stat .val{{font-size:24px;font-weight:bold;color:#2c3e50}} .stat .lbl{{font-size:12px;color:#888}}
.card{{background:#fff;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left}}
th{{background:#ecf0f1;font-weight:600}}
tr:nth-child(even){{background:#fafafa}}
ul{{line-height:1.8}}
</style></head><body>
<h1>&#x1F4A7; Spatial Hydrology Dashboard</h1>
<div class="stats">
<div class="stat">{gauge_svg}</div>
<div class="stat"><div class="val">{r.avg_elevation:.1f}</div><div class="lbl">Avg Elevation</div></div>
<div class="stat"><div class="val">{r.total_precipitation:.1f}mm</div><div class="lbl">Total Precipitation</div></div>
<div class="stat"><div class="val">{r.total_runoff:.1f}mm</div><div class="lbl">Total Runoff</div></div>
<div class="stat"><div class="val">{r.avg_flood_risk:.1f}</div><div class="lbl">Avg Flood Risk</div></div>
<div class="stat"><div class="val">{r.avg_recharge:.1f}</div><div class="lbl">Avg Recharge</div></div>
<div class="stat"><div class="val">{r.num_basins}</div><div class="lbl">Drainage Basins</div></div>
</div>
<div class="card"><h2>Autonomous Insights</h2><ul>{insights_html}</ul></div>
<div class="card"><h2>Cell Hydrology</h2>
<table><tr><th>ID</th><th>X</th><th>Y</th><th>Elev</th><th>Terrain</th>
<th>Precip</th><th>Runoff</th><th>Flow Dir</th><th>Accum</th>
<th>Strahler</th><th>Flood Risk</th><th>Flood Class</th>
<th>Recharge</th><th>Recharge Class</th><th>Basin</th></tr>
{rows_cells}</table></div>
<div class="card"><h2>Drainage Basins</h2>
<table><tr><th>Basin ID</th><th>Cells</th><th>Outlet</th><th>Area</th>
<th>Precipitation</th><th>Runoff</th><th>Max Strahler</th></tr>
{rows_basins}</table></div>
<div class="card"><h2>Flow Corridors (Top 20)</h2>
<table><tr><th>Cell</th><th>Accumulation</th><th>Strahler</th><th>Confluence</th></tr>
{rows_corridors}</table></div>
</body></html>"""

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_content)


# ---------------------------------------------------------------------------
# Public convenience API
# ---------------------------------------------------------------------------


def hydrology_analyze(
    path_or_points,
    adj_k: int = 6,
    seed: int = 42,
) -> HydrologyResult:
    """One-liner analysis -- accepts a file path or list of (x, y) tuples."""
    if isinstance(path_or_points, str):
        engine = HydrologyEngine(path=path_or_points, adj_k=adj_k, seed=seed)
    else:
        engine = HydrologyEngine(
            points=path_or_points, adj_k=adj_k, seed=seed)
    return engine.analyze()


def hydrology_demo() -> None:
    """Generate demo points, run analysis, print summary."""
    rng = random.Random(42)
    pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
    engine = HydrologyEngine(points=pts, seed=42)
    result = engine.analyze()

    print("=" * 60)
    print("  Spatial Hydrology Engine -- Demo")
    print("=" * 60)
    print(f"  Points:              {len(pts)}")
    print(f"  Health Score:        {result.health_score:.1f}/100")
    print(f"  Avg Elevation:       {result.avg_elevation:.1f}")
    print(f"  Total Precipitation: {result.total_precipitation:.1f}mm")
    print(f"  Total Runoff:        {result.total_runoff:.1f}mm")
    print(f"  Avg Flood Risk:      {result.avg_flood_risk:.1f}")
    print(f"  Avg Recharge:        {result.avg_recharge:.1f}")
    print(f"  Drainage Basins:     {result.num_basins}")
    print(f"  Flow Corridors:      {len(result.flow_corridors)}")
    print()
    print("  Insights:")
    for ins in result.insights:
        print(f"    * {ins}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    parser = argparse.ArgumentParser(
        description="Spatial Hydrology Engine -- autonomous water flow & flood simulation")
    parser.add_argument("points", nargs="?",
                        help="Path to points file (x y per line)")
    parser.add_argument("--json", dest="json_out",
                        help="Export result to JSON")
    parser.add_argument("--html", dest="html_out",
                        help="Export interactive HTML dashboard")
    parser.add_argument("--adj-k", type=int, default=6,
                        help="K for knn adjacency")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo with generated data")
    args = parser.parse_args()

    if args.demo:
        hydrology_demo()
        return

    if not args.points:
        parser.error("Must provide points file or --demo")

    engine = HydrologyEngine(
        path=args.points, adj_k=args.adj_k, seed=args.seed)
    result = engine.analyze()

    print(f"Hydrology Health: {result.health_score:.1f}/100")
    print(f"Elevation: {result.avg_elevation:.1f}  "
          f"Precipitation: {result.total_precipitation:.1f}mm  "
          f"Runoff: {result.total_runoff:.1f}mm")
    print(f"Flood Risk: {result.avg_flood_risk:.1f}  "
          f"Recharge: {result.avg_recharge:.1f}  "
          f"Basins: {result.num_basins}")

    for ins in result.insights:
        print(f"  * {ins}")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON: {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML: {args.html_out}")


if __name__ == "__main__":
    _cli()
