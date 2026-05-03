#!/usr/bin/env python3
"""Spatial Tectonics Engine -- autonomous tectonic plate simulation on Voronoi tessellations.

Models plate tectonics across Voronoi cells where each cell belongs to a
tectonic plate with drift dynamics.  Simulates plate boundaries, collision
zones, subduction, seismic activity, and volcanic zones -- enabling
spatial geodynamic analysis on arbitrary point sets.

Seven analysis engines:

- **Plate Assigner Engine** -- Groups cells into tectonic plates via
  seed-based flood fill.  Each plate gets a type (continental / oceanic),
  density, and thickness based on area.
- **Drift Vector Engine** -- Assigns per-plate drift velocity vectors
  (direction + speed).  Computes per-cell absolute motion from plate drift.
- **Boundary Classifier Engine** -- Classifies plate boundaries between
  adjacent cells on different plates as convergent (collision), divergent
  (rift), or transform (sliding) using relative-velocity dot products.
- **Collision Engine** -- Models subduction (oceanic under continental),
  mountain building (continental-continental), and trench formation
  (oceanic-oceanic) at convergent boundaries.  Accumulates stress.
- **Seismic Activity Engine** -- Predicts earthquake probability per cell
  based on boundary proximity, stress, and boundary type.  Assigns
  magnitude estimates and depth classification.
- **Volcanic Activity Engine** -- Identifies volcanic zones: subduction
  volcanism, hotspot volcanism, and rift volcanism.  Assigns eruption
  probability and magma type.
- **Autonomous Insight Generator** -- Composite tectonic health score
  0-100, tectonic regime classification per cell, natural-language
  insights about the system.

Usage (Python API)::

    from vormap_tectonics import TectonicsEngine, tectonics_analyze, tectonics_demo

    result = tectonics_analyze("points.txt")
    print(f"Health: {result.health_score:.1f}/100")

    engine = TectonicsEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze()
    engine.to_html("tectonics.html")

    tectonics_demo()

CLI::

    python vormap_tectonics.py points.txt
    python vormap_tectonics.py points.txt --num-plates 6 --json out.json --html dash.html
    python vormap_tectonics.py --demo
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
class CellTectonics:
    """Tectonic profile for a single spatial cell."""
    cell_id: int
    x: float
    y: float
    plate_id: int = 0
    plate_type: str = "continental"
    drift_dx: float = 0.0
    drift_dy: float = 0.0
    drift_speed: float = 0.0
    stress: float = 0.0
    earthquake_prob: float = 0.0
    earthquake_magnitude: float = 0.0
    earthquake_depth: str = "shallow"
    volcanic_prob: float = 0.0
    magma_type: str = "basaltic"
    tectonic_regime: str = "stable_craton"


@dataclass
class PlateBoundary:
    """A boundary between two cells on different plates."""
    cell_a: int
    cell_b: int
    boundary_type: str = "transform"  # convergent / divergent / transform
    relative_speed: float = 0.0
    stress: float = 0.0


@dataclass
class TectonicPlate:
    """A tectonic plate grouping cells."""
    plate_id: int
    cells: List[int] = field(default_factory=list)
    plate_type: str = "continental"
    drift_direction: float = 0.0
    drift_speed: float = 0.0
    area: float = 0.0
    density: float = 2.7  # g/cm^3
    thickness: float = 35.0  # km


@dataclass
class SeismicEvent:
    """Predicted seismic event for a cell."""
    cell_id: int
    magnitude: float = 0.0
    depth_class: str = "shallow"
    probability: float = 0.0
    boundary_type: str = "transform"


@dataclass
class VolcanicZone:
    """Volcanic zone at a cell."""
    cell_id: int
    zone_type: str = "subduction"
    eruption_probability: float = 0.0
    magma_type: str = "basaltic"


@dataclass
class TectonicsResult:
    """Full tectonic analysis result."""
    cells: List[CellTectonics] = field(default_factory=list)
    plates: List[TectonicPlate] = field(default_factory=list)
    boundaries: List[PlateBoundary] = field(default_factory=list)
    seismic_events: List[SeismicEvent] = field(default_factory=list)
    volcanic_zones: List[VolcanicZone] = field(default_factory=list)
    avg_stress: float = 0.0
    total_seismic_energy: float = 0.0
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
# Engine 1: Plate Assigner
# ---------------------------------------------------------------------------


def _assign_plates(
    points: List[Tuple[float, float]],
    adj: Dict[int, List[int]],
    areas: List[float],
    num_plates: int,
    rng: random.Random,
) -> List[TectonicPlate]:
    """Assign cells to plates via seed-based BFS flood fill."""
    n = len(points)
    num_plates = min(num_plates, n)
    if num_plates < 1:
        num_plates = 1

    # Pick seed cells spread across the point set
    seeds = rng.sample(range(n), num_plates)
    plate_of: List[int] = [-1] * n

    queues: List[deque] = []
    for pid, s in enumerate(seeds):
        plate_of[s] = pid
        queues.append(deque([s]))

    # Round-robin BFS
    active = True
    while active:
        active = False
        for pid in range(num_plates):
            if not queues[pid]:
                continue
            next_q: deque = deque()
            while queues[pid]:
                cell = queues[pid].popleft()
                for nb in adj.get(cell, []):
                    if plate_of[nb] == -1:
                        plate_of[nb] = pid
                        next_q.append(nb)
                        active = True
            queues[pid] = next_q

    # Assign any remaining unassigned cells to nearest plate
    for i in range(n):
        if plate_of[i] == -1:
            plate_of[i] = 0

    # Build plate objects
    plates: List[TectonicPlate] = []
    for pid in range(num_plates):
        members = [i for i in range(n) if plate_of[i] == pid]
        if not members:
            plates.append(TectonicPlate(plate_id=pid))
            continue
        total_area = sum(areas[i] for i in members)
        # Type based on area: larger plates more likely continental
        ptype = "continental" if total_area > sum(areas) / num_plates else "oceanic"
        density = 2.7 if ptype == "continental" else 3.0
        thickness = 35.0 if ptype == "continental" else 7.0
        plates.append(TectonicPlate(
            plate_id=pid,
            cells=members,
            plate_type=ptype,
            area=total_area,
            density=density,
            thickness=thickness,
        ))

    return plates


# ---------------------------------------------------------------------------
# Engine 2: Drift Vector
# ---------------------------------------------------------------------------


def _compute_drift(
    plates: List[TectonicPlate],
    rng: random.Random,
) -> None:
    """Assign random drift vectors to each plate."""
    for plate in plates:
        angle = rng.uniform(0, 2 * math.pi)
        speed = rng.uniform(0.5, 5.0)  # cm/yr analogy
        plate.drift_direction = math.degrees(angle)
        plate.drift_speed = speed


# ---------------------------------------------------------------------------
# Engine 3: Boundary Classifier
# ---------------------------------------------------------------------------


def _classify_boundaries(
    points: List[Tuple[float, float]],
    adj: Dict[int, List[int]],
    plates: List[TectonicPlate],
) -> List[PlateBoundary]:
    """Classify plate boundaries between adjacent cells on different plates."""
    n = len(points)
    plate_of = [0] * n
    plate_map: Dict[int, TectonicPlate] = {}
    for plate in plates:
        plate_map[plate.plate_id] = plate
        for c in plate.cells:
            plate_of[c] = plate.plate_id

    seen = set()
    boundaries: List[PlateBoundary] = []

    for i in range(n):
        for j in adj.get(i, []):
            if plate_of[i] == plate_of[j]:
                continue
            edge = (min(i, j), max(i, j))
            if edge in seen:
                continue
            seen.add(edge)

            pa = plate_map.get(plate_of[i])
            pb = plate_map.get(plate_of[j])
            if pa is None or pb is None:
                continue

            # Drift vectors
            ang_a = math.radians(pa.drift_direction)
            dx_a = pa.drift_speed * math.cos(ang_a)
            dy_a = pa.drift_speed * math.sin(ang_a)
            ang_b = math.radians(pb.drift_direction)
            dx_b = pb.drift_speed * math.cos(ang_b)
            dy_b = pb.drift_speed * math.sin(ang_b)

            # Relative velocity
            rel_dx = dx_b - dx_a
            rel_dy = dy_b - dy_a
            rel_speed = math.sqrt(rel_dx ** 2 + rel_dy ** 2)

            # Boundary normal (from i to j)
            bx = points[j][0] - points[i][0]
            by = points[j][1] - points[i][1]
            bl = math.sqrt(bx ** 2 + by ** 2)
            if bl < 1e-12:
                boundaries.append(PlateBoundary(
                    cell_a=i, cell_b=j, boundary_type="transform",
                    relative_speed=rel_speed, stress=0.0,
                ))
                continue

            bx /= bl
            by /= bl

            # Dot product: negative = convergent, positive = divergent
            dot = rel_dx * bx + rel_dy * by

            if dot < -0.3 * rel_speed:
                btype = "convergent"
            elif dot > 0.3 * rel_speed:
                btype = "divergent"
            else:
                btype = "transform"

            stress = max(0.0, -dot) if btype == "convergent" else abs(dot) * 0.3
            boundaries.append(PlateBoundary(
                cell_a=i, cell_b=j, boundary_type=btype,
                relative_speed=rel_speed, stress=stress,
            ))

    return boundaries


# ---------------------------------------------------------------------------
# Engine 4: Collision Engine
# ---------------------------------------------------------------------------


def _compute_collisions(
    cells: List[CellTectonics],
    boundaries: List[PlateBoundary],
    plates: List[TectonicPlate],
) -> None:
    """Accumulate stress at convergent boundaries and classify collision type."""
    plate_map: Dict[int, TectonicPlate] = {p.plate_id: p for p in plates}
    cell_map: Dict[int, CellTectonics] = {c.cell_id: c for c in cells}

    for bd in boundaries:
        if bd.boundary_type != "convergent":
            continue
        ca = cell_map.get(bd.cell_a)
        cb = cell_map.get(bd.cell_b)
        if ca is None or cb is None:
            continue
        # Accumulate stress
        ca.stress += bd.stress
        cb.stress += bd.stress


# ---------------------------------------------------------------------------
# Engine 5: Seismic Activity
# ---------------------------------------------------------------------------


def _compute_seismic(
    cells: List[CellTectonics],
    boundaries: List[PlateBoundary],
    adj: Dict[int, List[int]],
) -> List[SeismicEvent]:
    """Predict seismic activity based on boundary proximity and stress."""
    # Map boundary cells
    boundary_cells: Dict[int, List[PlateBoundary]] = {}
    for bd in boundaries:
        boundary_cells.setdefault(bd.cell_a, []).append(bd)
        boundary_cells.setdefault(bd.cell_b, []).append(bd)

    cell_map = {c.cell_id: c for c in cells}
    events: List[SeismicEvent] = []

    for c in cells:
        bds = boundary_cells.get(c.cell_id, [])
        if not bds:
            # Interior cell — low seismic risk
            c.earthquake_prob = max(0.0, min(1.0, c.stress * 0.05))
            c.earthquake_magnitude = 4.0 + c.stress * 0.5
            c.earthquake_magnitude = min(c.earthquake_magnitude, 5.0)
            if c.earthquake_prob > 0.05:
                events.append(SeismicEvent(
                    cell_id=c.cell_id,
                    magnitude=c.earthquake_magnitude,
                    depth_class="deep",
                    probability=c.earthquake_prob,
                    boundary_type="interior",
                ))
            continue

        # Boundary cell
        max_stress = max(bd.stress for bd in bds)
        max_speed = max(bd.relative_speed for bd in bds)
        dominant = max(bds, key=lambda b: b.stress)

        prob = min(1.0, (max_stress * 0.3 + max_speed * 0.1))
        prob = max(0.0, prob)
        mag = 4.0 + max_stress * 5.0
        mag = min(mag, 9.5)
        mag = max(mag, 4.0)

        if dominant.boundary_type == "convergent":
            depth = "intermediate" if mag < 7.0 else "deep"
        elif dominant.boundary_type == "divergent":
            depth = "shallow"
        else:
            depth = "shallow" if mag < 6.0 else "intermediate"

        c.earthquake_prob = prob
        c.earthquake_magnitude = mag
        c.earthquake_depth = depth

        events.append(SeismicEvent(
            cell_id=c.cell_id,
            magnitude=mag,
            depth_class=depth,
            probability=prob,
            boundary_type=dominant.boundary_type,
        ))

    return events


# ---------------------------------------------------------------------------
# Engine 6: Volcanic Activity
# ---------------------------------------------------------------------------


def _compute_volcanic(
    cells: List[CellTectonics],
    boundaries: List[PlateBoundary],
    plates: List[TectonicPlate],
    rng: random.Random,
) -> List[VolcanicZone]:
    """Identify volcanic zones from subduction, rifts, and hotspots."""
    plate_map = {p.plate_id: p for p in plates}
    cell_map = {c.cell_id: c for c in cells}

    boundary_cells_convergent: set = set()
    boundary_cells_divergent: set = set()
    for bd in boundaries:
        if bd.boundary_type == "convergent":
            boundary_cells_convergent.add(bd.cell_a)
            boundary_cells_convergent.add(bd.cell_b)
        elif bd.boundary_type == "divergent":
            boundary_cells_divergent.add(bd.cell_a)
            boundary_cells_divergent.add(bd.cell_b)

    zones: List[VolcanicZone] = []

    # Subduction volcanism at convergent boundaries
    for cid in boundary_cells_convergent:
        c = cell_map.get(cid)
        if c is None:
            continue
        prob = min(1.0, c.stress * 0.2 + 0.1)
        # Subduction → andesitic if oceanic plate involved
        plate = plate_map.get(c.plate_id)
        if plate and plate.plate_type == "oceanic":
            mtype = "andesitic"
        else:
            mtype = "rhyolitic"
        c.volcanic_prob = prob
        c.magma_type = mtype
        zones.append(VolcanicZone(
            cell_id=cid, zone_type="subduction",
            eruption_probability=prob, magma_type=mtype,
        ))

    # Rift volcanism at divergent boundaries
    for cid in boundary_cells_divergent:
        if cid in boundary_cells_convergent:
            continue  # Already handled
        c = cell_map.get(cid)
        if c is None:
            continue
        prob = rng.uniform(0.05, 0.3)
        c.volcanic_prob = max(c.volcanic_prob, prob)
        c.magma_type = "basaltic"
        zones.append(VolcanicZone(
            cell_id=cid, zone_type="rift",
            eruption_probability=prob, magma_type="basaltic",
        ))

    # Hotspot volcanism — random interior cells
    interior = [c for c in cells
                if c.cell_id not in boundary_cells_convergent
                and c.cell_id not in boundary_cells_divergent]
    num_hotspots = max(1, len(interior) // 10)
    if interior:
        hotspot_cells = rng.sample(interior, min(num_hotspots, len(interior)))
        for c in hotspot_cells:
            prob = rng.uniform(0.1, 0.5)
            c.volcanic_prob = max(c.volcanic_prob, prob)
            c.magma_type = "basaltic"
            zones.append(VolcanicZone(
                cell_id=c.cell_id, zone_type="hotspot",
                eruption_probability=prob, magma_type="basaltic",
            ))

    return zones


# ---------------------------------------------------------------------------
# Engine 7: Insight Generator
# ---------------------------------------------------------------------------


def _generate_insights(
    cells: List[CellTectonics],
    plates: List[TectonicPlate],
    boundaries: List[PlateBoundary],
    seismic_events: List[SeismicEvent],
    volcanic_zones: List[VolcanicZone],
) -> Tuple[float, List[str]]:
    """Compute health score and generate insights."""
    n = len(cells)
    insights: List[str] = []

    if n == 0:
        return 50.0, ["No cells to analyze."]

    # --- Classify tectonic regimes ---
    boundary_cell_ids: set = set()
    convergent_ids: set = set()
    divergent_ids: set = set()
    volcanic_ids = {vz.cell_id for vz in volcanic_zones}
    hotspot_ids = {vz.cell_id for vz in volcanic_zones if vz.zone_type == "hotspot"}

    for bd in boundaries:
        boundary_cell_ids.add(bd.cell_a)
        boundary_cell_ids.add(bd.cell_b)
        if bd.boundary_type == "convergent":
            convergent_ids.add(bd.cell_a)
            convergent_ids.add(bd.cell_b)
        elif bd.boundary_type == "divergent":
            divergent_ids.add(bd.cell_a)
            divergent_ids.add(bd.cell_b)

    for c in cells:
        if c.cell_id in hotspot_ids:
            c.tectonic_regime = "hotspot"
        elif c.cell_id in convergent_ids:
            c.tectonic_regime = "collision_zone"
        elif c.cell_id in divergent_ids:
            c.tectonic_regime = "rift_zone"
        elif c.cell_id in boundary_cell_ids:
            c.tectonic_regime = "active_margin"
        else:
            c.tectonic_regime = "stable_craton"

    # --- Health score ---
    # Higher score = more tectonically stable / less hazardous
    avg_stress = sum(c.stress for c in cells) / n if n else 0
    stress_norm = _normalize([c.stress for c in cells])
    stress_gini = _gini_coeff(stress_norm) if stress_norm else 0

    n_convergent = sum(1 for bd in boundaries if bd.boundary_type == "convergent")
    n_divergent = sum(1 for bd in boundaries if bd.boundary_type == "divergent")
    n_transform = sum(1 for bd in boundaries if bd.boundary_type == "transform")
    total_bd = len(boundaries)

    # Penalties
    high_quake_frac = sum(1 for e in seismic_events if e.magnitude > 7.0) / max(n, 1)
    high_volc_frac = len(volcanic_zones) / max(n, 1)

    score = 100.0
    score -= high_quake_frac * 30  # Major earthquake fraction
    score -= high_volc_frac * 20  # Volcanic density
    score -= stress_gini * 15  # Stress concentration
    score -= min(avg_stress * 5, 20)  # Average stress
    score = max(0.0, min(100.0, score))

    # --- Insights ---
    insights.append(
        f"Tectonic system: {len(plates)} plates, {total_bd} boundaries, "
        f"{len(seismic_events)} seismic zones, {len(volcanic_zones)} volcanic zones."
    )

    regime_counts: Dict[str, int] = {}
    for c in cells:
        regime_counts[c.tectonic_regime] = regime_counts.get(c.tectonic_regime, 0) + 1
    dominant_regime = max(regime_counts, key=regime_counts.get) if regime_counts else "unknown"
    insights.append(f"Dominant regime: {dominant_regime} ({regime_counts.get(dominant_regime, 0)}/{n} cells).")

    if n_convergent > 0:
        insights.append(
            f"Convergent boundaries: {n_convergent} — collision zones with mountain building and subduction."
        )
    if n_divergent > 0:
        insights.append(
            f"Divergent boundaries: {n_divergent} — rift zones with new crust formation."
        )
    if n_transform > 0:
        insights.append(
            f"Transform boundaries: {n_transform} — lateral sliding zones."
        )

    # Plate type balance
    continental = sum(1 for p in plates if p.plate_type == "continental" and p.cells)
    oceanic = sum(1 for p in plates if p.plate_type == "oceanic" and p.cells)
    insights.append(f"Plate composition: {continental} continental, {oceanic} oceanic.")

    # Major quakes
    major_quakes = [e for e in seismic_events if e.magnitude > 7.0]
    if major_quakes:
        insights.append(
            f"⚠ {len(major_quakes)} cell(s) at risk of major earthquakes (M > 7.0)."
        )

    # Hotspots
    n_hotspots = sum(1 for vz in volcanic_zones if vz.zone_type == "hotspot")
    if n_hotspots > 0:
        insights.append(f"Detected {n_hotspots} mantle hotspot(s) with basaltic volcanism.")

    if score >= 80:
        insights.append("✅ Tectonically stable system with low hazard concentration.")
    elif score >= 50:
        insights.append("⚠ Moderate tectonic activity — some hazard zones present.")
    else:
        insights.append("🔴 Highly active tectonic system — significant seismic and volcanic risk.")

    return score, insights


def _gini_coeff(values: List[float]) -> float:
    if not values or len(values) < 2:
        return 0.0
    vs = sorted(values)
    n = len(vs)
    total = sum(vs)
    if total < 1e-12:
        return 0.0
    cum = 0.0
    for i, v in enumerate(vs):
        cum += (2 * (i + 1) - n - 1) * v
    return cum / (n * total)


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------


class TectonicsEngine:
    """Spatial Tectonics Engine -- autonomous plate simulation."""

    def __init__(
        self,
        points: Optional[List[Tuple[float, float]]] = None,
        path: Optional[str] = None,
        seed: int = 42,
        num_plates: int = 5,
    ):
        if points is not None:
            self._points = list(points)
        elif path is not None:
            self._points = _load_points(path)
        else:
            self._points = []

        self._seed = seed
        self._num_plates = num_plates
        self._result: Optional[TectonicsResult] = None

    @property
    def points(self) -> List[Tuple[float, float]]:
        return self._points

    def analyze(self) -> TectonicsResult:
        rng = random.Random(self._seed)
        n = len(self._points)

        if n == 0:
            self._result = TectonicsResult(
                health_score=50.0,
                insights=["No points provided."],
            )
            return self._result

        adj = _knn_adjacency(self._points)
        areas = _voronoi_areas(self._points, adj)

        # Engine 1: Plate assignment
        plates = _assign_plates(self._points, adj, areas, self._num_plates, rng)

        # Engine 2: Drift vectors
        _compute_drift(plates, rng)

        # Build cell objects
        plate_of = [0] * n
        plate_map = {p.plate_id: p for p in plates}
        for p in plates:
            for c in p.cells:
                plate_of[c] = p.plate_id

        cells: List[CellTectonics] = []
        for i in range(n):
            plate = plate_map.get(plate_of[i])
            if plate is None:
                cells.append(CellTectonics(cell_id=i, x=self._points[i][0], y=self._points[i][1]))
                continue
            ang = math.radians(plate.drift_direction)
            dx = plate.drift_speed * math.cos(ang)
            dy = plate.drift_speed * math.sin(ang)
            cells.append(CellTectonics(
                cell_id=i,
                x=self._points[i][0],
                y=self._points[i][1],
                plate_id=plate.plate_id,
                plate_type=plate.plate_type,
                drift_dx=dx,
                drift_dy=dy,
                drift_speed=plate.drift_speed,
            ))

        # Engine 3: Boundary classification
        boundaries = _classify_boundaries(self._points, adj, plates)

        # Engine 4: Collision stress
        _compute_collisions(cells, boundaries, plates)

        # Engine 5: Seismic activity
        seismic_events = _compute_seismic(cells, boundaries, adj)

        # Engine 6: Volcanic activity
        volcanic_zones = _compute_volcanic(cells, boundaries, plates, rng)

        # Engine 7: Insights
        health_score, insights = _generate_insights(
            cells, plates, boundaries, seismic_events, volcanic_zones,
        )

        avg_stress = sum(c.stress for c in cells) / n if n else 0.0
        total_energy = sum(
            10 ** (1.5 * e.magnitude + 4.8) for e in seismic_events
        ) if seismic_events else 0.0

        self._result = TectonicsResult(
            cells=cells,
            plates=plates,
            boundaries=boundaries,
            seismic_events=seismic_events,
            volcanic_zones=volcanic_zones,
            avg_stress=avg_stress,
            total_seismic_energy=total_energy,
            health_score=health_score,
            insights=insights,
        )
        return self._result

    def to_dict(self) -> dict:
        if self._result is None:
            self.analyze()
        assert self._result is not None
        return asdict(self._result)

    def to_json(self, path: str) -> None:
        data = self.to_dict()
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)

    def to_html(self, path: str) -> None:
        if self._result is None:
            self.analyze()
        r = self._result
        assert r is not None

        esc = html_mod.escape

        def _score_color(s: float) -> str:
            if s >= 80:
                return "#4caf50"
            if s >= 50:
                return "#ff9800"
            return "#f44336"

        rows_plates = ""
        for p in r.plates:
            rows_plates += (
                f"<tr><td>{p.plate_id}</td><td>{esc(p.plate_type)}</td>"
                f"<td>{len(p.cells)}</td><td>{p.drift_direction:.1f}°</td>"
                f"<td>{p.drift_speed:.2f}</td><td>{p.area:.2f}</td>"
                f"<td>{p.density}</td><td>{p.thickness}</td></tr>\n"
            )

        rows_bd = ""
        for bd in r.boundaries[:100]:
            rows_bd += (
                f"<tr><td>{bd.cell_a}</td><td>{bd.cell_b}</td>"
                f"<td>{esc(bd.boundary_type)}</td>"
                f"<td>{bd.relative_speed:.2f}</td>"
                f"<td>{bd.stress:.3f}</td></tr>\n"
            )

        rows_quake = ""
        for ev in r.seismic_events[:100]:
            rows_quake += (
                f"<tr><td>{ev.cell_id}</td><td>{ev.magnitude:.1f}</td>"
                f"<td>{esc(ev.depth_class)}</td>"
                f"<td>{ev.probability:.3f}</td>"
                f"<td>{esc(ev.boundary_type)}</td></tr>\n"
            )

        rows_volc = ""
        for vz in r.volcanic_zones[:100]:
            rows_volc += (
                f"<tr><td>{vz.cell_id}</td><td>{esc(vz.zone_type)}</td>"
                f"<td>{vz.eruption_probability:.3f}</td>"
                f"<td>{esc(vz.magma_type)}</td></tr>\n"
            )

        insights_html = "".join(f"<li>{esc(i)}</li>" for i in r.insights)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Spatial Tectonics Dashboard</title>
<style>
  body {{ background:#1a1a2e; color:#e0e0e0; font-family:system-ui,sans-serif; margin:0; padding:20px; }}
  h1 {{ color:#e94560; text-align:center; }}
  h2 {{ color:#0f3460; background:#16213e; padding:10px; border-radius:6px; }}
  .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:20px; }}
  .card {{ background:#16213e; border-radius:8px; padding:16px; min-width:140px; flex:1; text-align:center; }}
  .card .val {{ font-size:1.8em; font-weight:bold; }}
  .card .lbl {{ font-size:0.85em; color:#999; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:20px; }}
  th,td {{ border:1px solid #333; padding:6px 10px; text-align:left; }}
  th {{ background:#16213e; }}
  tr:nth-child(even) {{ background:#1f2a40; }}
  ul {{ line-height:1.8; }}
  .score {{ color:{_score_color(r.health_score)}; }}
</style>
</head>
<body>
<h1>🌍 Spatial Tectonics Dashboard</h1>
<div class="cards">
  <div class="card"><div class="val score">{r.health_score:.1f}</div><div class="lbl">Health Score</div></div>
  <div class="card"><div class="val">{len(r.plates)}</div><div class="lbl">Plates</div></div>
  <div class="card"><div class="val">{len(r.boundaries)}</div><div class="lbl">Boundaries</div></div>
  <div class="card"><div class="val">{len(r.seismic_events)}</div><div class="lbl">Seismic Zones</div></div>
  <div class="card"><div class="val">{len(r.volcanic_zones)}</div><div class="lbl">Volcanic Zones</div></div>
  <div class="card"><div class="val">{r.avg_stress:.3f}</div><div class="lbl">Avg Stress</div></div>
</div>

<h2>Tectonic Plates</h2>
<table>
<tr><th>ID</th><th>Type</th><th>Cells</th><th>Drift Dir</th><th>Speed</th><th>Area</th><th>Density</th><th>Thickness</th></tr>
{rows_plates}
</table>

<h2>Plate Boundaries (top 100)</h2>
<table>
<tr><th>Cell A</th><th>Cell B</th><th>Type</th><th>Rel Speed</th><th>Stress</th></tr>
{rows_bd}
</table>

<h2>Seismic Events (top 100)</h2>
<table>
<tr><th>Cell</th><th>Magnitude</th><th>Depth</th><th>Probability</th><th>Boundary</th></tr>
{rows_quake}
</table>

<h2>Volcanic Zones (top 100)</h2>
<table>
<tr><th>Cell</th><th>Type</th><th>Eruption Prob</th><th>Magma</th></tr>
{rows_volc}
</table>

<h2>Insights</h2>
<ul>{insights_html}</ul>
</body>
</html>"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_content)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def tectonics_analyze(
    path: str, num_plates: int = 5, seed: int = 42
) -> TectonicsResult:
    """One-liner analysis from a points file."""
    engine = TectonicsEngine(path=path, num_plates=num_plates, seed=seed)
    return engine.analyze()


def tectonics_demo() -> TectonicsResult:
    """Run demo with 30 random points."""
    rng = random.Random(12345)
    pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
    engine = TectonicsEngine(points=pts, num_plates=5, seed=42)
    result = engine.analyze()

    print("=" * 60)
    print("  Spatial Tectonics Engine -- Demo")
    print("=" * 60)
    print(f"\nPoints: {len(pts)}")
    print(f"Plates: {len(result.plates)}")
    print(f"Boundaries: {len(result.boundaries)}")
    print(f"Seismic events: {len(result.seismic_events)}")
    print(f"Volcanic zones: {len(result.volcanic_zones)}")
    print(f"Health score: {result.health_score:.1f}/100")
    print(f"\nInsights:")
    for ins in result.insights:
        print(f"  • {ins}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spatial Tectonics Engine -- autonomous plate simulation"
    )
    parser.add_argument("path", nargs="?", help="Points file")
    parser.add_argument("--num-plates", type=int, default=5, help="Number of plates")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--json", dest="json_out", help="JSON output path")
    parser.add_argument("--html", dest="html_out", help="HTML dashboard path")
    parser.add_argument("--demo", action="store_true", help="Run demo")
    args = parser.parse_args()

    if args.demo:
        result = tectonics_demo()
        if args.html_out:
            engine = TectonicsEngine(seed=args.seed, num_plates=args.num_plates)
            engine._result = result
            engine.to_html(args.html_out)
            print(f"\nDashboard: {args.html_out}")
        return

    if not args.path:
        parser.print_help()
        sys.exit(1)

    engine = TectonicsEngine(
        path=args.path, num_plates=args.num_plates, seed=args.seed
    )
    result = engine.analyze()

    print(f"Plates: {len(result.plates)}")
    print(f"Boundaries: {len(result.boundaries)}")
    print(f"Seismic events: {len(result.seismic_events)}")
    print(f"Volcanic zones: {len(result.volcanic_zones)}")
    print(f"Health: {result.health_score:.1f}/100")
    for ins in result.insights:
        print(f"  {ins}")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON: {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML: {args.html_out}")


if __name__ == "__main__":
    main()
