"""Spatial Equilibrium Engine — autonomous equilibrium analysis for Voronoi tessellations.

Treats Voronoi cells as a physical system where each cell exerts "spatial
pressure" on its neighbors based on area imbalance, compactness difference,
and centroid distance.  The module finds equilibrium states, classifies them
as stable/unstable/saddle, maps basins of attraction, predicts perturbation
responses, and detects tipping points.

Six analysis engines run autonomously:

- **Force Field Computation** — net spatial force vectors per cell based on
  area-pressure from neighbors.
- **Equilibrium Classification** — identifies low-force cells and classifies
  stability via local Jacobian eigenvalue analysis.
- **Basin of Attraction Mapping** — assigns each cell to an equilibrium via
  gradient-following trajectories.
- **Perturbation Response Predictor** — forecasts displacement propagation
  depth, affected cells, energy change, and time-to-restabilize.
- **Tipping Point Detection** — finds cells near basin boundaries where
  small perturbations cause regime shifts.
- **Interactive HTML Dashboard** — 4-tab self-contained HTML visualization.

Usage (Python API)::

    from vormap_equilibrium import (
        analyze_equilibrium, EquilibriumReport,
        compute_force_field, classify_equilibria,
        map_basins, predict_perturbation, detect_tipping_points,
    )

    points = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
    bounds = (0, 1000, 0, 1000)

    report = analyze_equilibrium(points, bounds=bounds)
    print(report.summary())
    report.to_json("equilibrium.json")
    report.to_html("equilibrium.html")

    # Individual engines
    forces = compute_force_field(points, bounds=bounds)
    equilibria = classify_equilibria(points, bounds=bounds)
    basins = map_basins(points, bounds=bounds)
    response = predict_perturbation(points, cell_idx=0,
                                     displacement=(10, 5), bounds=bounds)
    tips = detect_tipping_points(points, bounds=bounds)

CLI::

    voronoimap datauni5.txt 5 --equilibrium
    voronoimap datauni5.txt 5 --equilibrium --equilibrium-json out.json
    voronoimap datauni5.txt 5 --equilibrium --equilibrium-html out.html
    voronoimap datauni5.txt 5 --equilibrium --perturb 0 --perturb-dx 10 --perturb-dy 5
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

try:
    from scipy.spatial import Voronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False

from vormap import validate_output_path

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
Point = Tuple[float, float]

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ForceVector:
    """Net spatial force on a cell."""
    cell_idx: int
    magnitude: float
    direction: float  # radians
    components: Tuple[float, float]  # (fx, fy)


@dataclass
class EquilibriumPoint:
    """A cell at or near equilibrium."""
    cell_idx: int
    classification: str  # "stable", "unstable", "saddle"
    net_force: float
    eigenvalues: Tuple[float, float]
    stability_margin: float


@dataclass
class Basin:
    """A basin of attraction around an equilibrium."""
    equilibrium_idx: int
    member_cells: List[int]
    size: int
    area_fraction: float


@dataclass
class PerturbationResponse:
    """Predicted response to a perturbation."""
    source_cell: int
    displacement: Tuple[float, float]
    affected_cells: List[int]
    propagation_depth: int
    energy_delta: float
    restabilize_steps: int


@dataclass
class TippingPoint:
    """A cell near a basin boundary."""
    cell_idx: int
    tipping_margin: float
    current_basin: int
    target_basin: int
    critical_direction: Tuple[float, float]


@dataclass
class EquilibriumReport:
    """Full equilibrium analysis report."""
    forces: List[ForceVector]
    equilibria: List[EquilibriumPoint]
    basins: List[Basin]
    perturbation_responses: List[PerturbationResponse]
    tipping_points: List[TippingPoint]
    system_energy: float
    global_stability_score: float  # 0-100

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            "=== Spatial Equilibrium Report ===",
            f"Cells analyzed: {len(self.forces)}",
            f"System energy: {self.system_energy:.4f}",
            f"Global stability score: {self.global_stability_score:.1f}/100",
            f"Equilibria found: {len(self.equilibria)}",
        ]
        stable = sum(1 for e in self.equilibria if e.classification == "stable")
        unstable = sum(1 for e in self.equilibria if e.classification == "unstable")
        saddle = sum(1 for e in self.equilibria if e.classification == "saddle")
        lines.append(f"  Stable: {stable}  Unstable: {unstable}  Saddle: {saddle}")
        lines.append(f"Basins: {len(self.basins)}")
        lines.append(f"Tipping points: {len(self.tipping_points)}")
        if self.perturbation_responses:
            lines.append(f"Perturbation responses: {len(self.perturbation_responses)}")
        return "\n".join(lines)

    def to_json(self, path: str) -> None:
        """Export report to JSON."""
        validate_output_path(path)
        data = {
            "system_energy": self.system_energy,
            "global_stability_score": self.global_stability_score,
            "forces": [
                {"cell_idx": f.cell_idx, "magnitude": f.magnitude,
                 "direction": f.direction, "components": list(f.components)}
                for f in self.forces
            ],
            "equilibria": [
                {"cell_idx": e.cell_idx, "classification": e.classification,
                 "net_force": e.net_force, "eigenvalues": list(e.eigenvalues),
                 "stability_margin": e.stability_margin}
                for e in self.equilibria
            ],
            "basins": [
                {"equilibrium_idx": b.equilibrium_idx,
                 "member_cells": b.member_cells, "size": b.size,
                 "area_fraction": b.area_fraction}
                for b in self.basins
            ],
            "perturbation_responses": [
                {"source_cell": p.source_cell,
                 "displacement": list(p.displacement),
                 "affected_cells": p.affected_cells,
                 "propagation_depth": p.propagation_depth,
                 "energy_delta": p.energy_delta,
                 "restabilize_steps": p.restabilize_steps}
                for p in self.perturbation_responses
            ],
            "tipping_points": [
                {"cell_idx": t.cell_idx, "tipping_margin": t.tipping_margin,
                 "current_basin": t.current_basin,
                 "target_basin": t.target_basin,
                 "critical_direction": list(t.critical_direction)}
                for t in self.tipping_points
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def to_html(self, path: str) -> None:
        """Export interactive HTML dashboard."""
        validate_output_path(path)
        html = _build_html_dashboard(self)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dist(a: Point, b: Point) -> float:
    """Euclidean distance."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _clip_polygon_to_bounds(vertices: List[Point], bounds: Tuple[float, float, float, float]) -> List[Point]:
    """Sutherland-Hodgman clipping of polygon to rectangular bounds."""
    xmin, xmax, ymin, ymax = bounds

    def clip_edge(poly, edge_fn, inside_fn):
        if not poly:
            return []
        out = []
        for i in range(len(poly)):
            curr = poly[i]
            prev = poly[i - 1]
            curr_in = inside_fn(curr)
            prev_in = inside_fn(prev)
            if curr_in:
                if not prev_in:
                    out.append(edge_fn(prev, curr))
                out.append(curr)
            elif prev_in:
                out.append(edge_fn(prev, curr))
        return out

    def intersect_left(a, b):
        if b[0] == a[0]:
            return (xmin, a[1])
        t = (xmin - a[0]) / (b[0] - a[0])
        return (xmin, a[1] + t * (b[1] - a[1]))

    def intersect_right(a, b):
        if b[0] == a[0]:
            return (xmax, a[1])
        t = (xmax - a[0]) / (b[0] - a[0])
        return (xmax, a[1] + t * (b[1] - a[1]))

    def intersect_bottom(a, b):
        if b[1] == a[1]:
            return (a[0], ymin)
        t = (ymin - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), ymin)

    def intersect_top(a, b):
        if b[1] == a[1]:
            return (a[0], ymax)
        t = (ymax - a[1]) / (b[1] - a[1])
        return (a[0] + t * (b[0] - a[0]), ymax)

    poly = list(vertices)
    poly = clip_edge(poly, intersect_left, lambda p: p[0] >= xmin)
    poly = clip_edge(poly, intersect_right, lambda p: p[0] <= xmax)
    poly = clip_edge(poly, intersect_bottom, lambda p: p[1] >= ymin)
    poly = clip_edge(poly, intersect_top, lambda p: p[1] <= ymax)
    return poly


def _polygon_area(vertices: List[Point]) -> float:
    """Shoelace formula for polygon area."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _polygon_centroid(vertices: List[Point]) -> Point:
    """Centroid of polygon."""
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n <= 2:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)
    cx, cy, a_sum = 0.0, 0.0, 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross
        a_sum += cross
    if abs(a_sum) < 1e-12:
        cx_m = sum(v[0] for v in vertices) / n
        cy_m = sum(v[1] for v in vertices) / n
        return (cx_m, cy_m)
    cx /= (3.0 * a_sum)
    cy /= (3.0 * a_sum)
    return (cx, cy)


def _compute_voronoi_cells(points: List[Point], bounds: Tuple[float, float, float, float]):
    """Compute bounded Voronoi cells. Returns (cells, adjacency).

    cells: list of dict with keys 'vertices', 'area', 'centroid'
    adjacency: dict mapping cell_idx -> list of neighbor cell indices
    """
    if not _HAS_SCIPY:
        raise ImportError("scipy is required for Voronoi computation")

    n = len(points)
    if n == 0:
        return [], {}
    if n == 1:
        xmin, xmax, ymin, ymax = bounds
        verts = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        area = (xmax - xmin) * (ymax - ymin)
        centroid = ((xmin + xmax) / 2, (ymin + ymax) / 2)
        return [{"vertices": verts, "area": area, "centroid": centroid}], {0: []}

    # Mirror points to handle boundary
    xmin, xmax, ymin, ymax = bounds
    mirrored = list(points)
    for px, py in points:
        mirrored.append((2 * xmin - px, py))
        mirrored.append((2 * xmax - px, py))
        mirrored.append((px, 2 * ymin - py))
        mirrored.append((px, 2 * ymax - py))

    vor = Voronoi(mirrored)

    cells = []
    adjacency: Dict[int, List[int]] = {i: [] for i in range(n)}

    for i in range(n):
        region_idx = vor.point_region[i]
        region = vor.regions[region_idx]
        if not region or -1 in region:
            # Fallback: assign bounds as cell
            verts = [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]
        else:
            verts = [tuple(vor.vertices[v]) for v in region]
        clipped = _clip_polygon_to_bounds(verts, bounds)
        if len(clipped) < 3:
            clipped = [(xmin, ymin), (xmax, ymin), (xmax, ymax)]
        area = _polygon_area(clipped)
        centroid = _polygon_centroid(clipped)
        cells.append({"vertices": clipped, "area": area, "centroid": centroid})

    # Build adjacency from Voronoi ridge_points
    for (p1, p2) in vor.ridge_points:
        if p1 < n and p2 < n:
            if p2 not in adjacency[p1]:
                adjacency[p1].append(p2)
            if p1 not in adjacency[p2]:
                adjacency[p2].append(p1)

    return cells, adjacency


def _default_bounds(points: List[Point]) -> Tuple[float, float, float, float]:
    """Compute default bounds with 10% padding."""
    if not points:
        return (0, 1000, 0, 1000)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    dx = max(xmax - xmin, 1.0) * 0.1
    dy = max(ymax - ymin, 1.0) * 0.1
    return (xmin - dx, xmax + dx, ymin - dy, ymax + dy)


# ---------------------------------------------------------------------------
# Engine 1: Force Field Computation
# ---------------------------------------------------------------------------

def compute_force_field(points: List[Point], bounds=None) -> List[ForceVector]:
    """Compute net spatial force on each cell from area-pressure of neighbors.

    Force on cell i from neighbor j: proportional to (area_j - area_i),
    directed from i's centroid toward j's centroid.
    """
    if not points:
        return []
    if bounds is None:
        bounds = _default_bounds(points)

    cells, adjacency = _compute_voronoi_cells(points, bounds)
    n = len(cells)
    mean_area = statistics.mean(c["area"] for c in cells) if n > 0 else 1.0

    forces = []
    for i in range(n):
        fx, fy = 0.0, 0.0
        ci = cells[i]["centroid"]
        ai = cells[i]["area"]
        neighbors = adjacency.get(i, [])
        for j in neighbors:
            cj = cells[j]["centroid"]
            aj = cells[j]["area"]
            # Pressure proportional to area difference, normalized
            pressure = (aj - ai) / mean_area
            # Direction from i to j
            dx = cj[0] - ci[0]
            dy = cj[1] - ci[1]
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 1e-12:
                continue
            fx += pressure * dx / dist
            fy += pressure * dy / dist
        mag = math.sqrt(fx * fx + fy * fy)
        direction = math.atan2(fy, fx)
        forces.append(ForceVector(
            cell_idx=i, magnitude=mag,
            direction=direction, components=(fx, fy)
        ))
    return forces


# ---------------------------------------------------------------------------
# Engine 2: Equilibrium Classification
# ---------------------------------------------------------------------------

def classify_equilibria(points: List[Point], bounds=None,
                        force_threshold: float = 0.1) -> List[EquilibriumPoint]:
    """Identify and classify equilibrium cells.

    A cell is near equilibrium if its net force magnitude < force_threshold.
    Classification uses finite-difference Jacobian eigenvalues.
    """
    if not points:
        return []
    if bounds is None:
        bounds = _default_bounds(points)

    forces = compute_force_field(points, bounds)
    if not forces:
        return []

    # Adaptive threshold: use fraction of max force
    max_force = max(f.magnitude for f in forces)
    threshold = max(force_threshold, max_force * 0.15)

    equilibria = []
    for fv in forces:
        if fv.magnitude <= threshold:
            # Approximate Jacobian via finite differences
            ev1, ev2 = _approximate_eigenvalues(points, fv.cell_idx, bounds)
            if ev1 < 0 and ev2 < 0:
                classification = "stable"
            elif ev1 > 0 and ev2 > 0:
                classification = "unstable"
            else:
                classification = "saddle"
            stability_margin = min(abs(ev1), abs(ev2))
            equilibria.append(EquilibriumPoint(
                cell_idx=fv.cell_idx,
                classification=classification,
                net_force=fv.magnitude,
                eigenvalues=(ev1, ev2),
                stability_margin=stability_margin,
            ))
    return equilibria


def _approximate_eigenvalues(points: List[Point], cell_idx: int,
                             bounds: Tuple[float, float, float, float],
                             delta: float = 1.0) -> Tuple[float, float]:
    """Approximate 2x2 Jacobian eigenvalues via finite differences."""
    px, py = points[cell_idx]

    def _force_at(pts):
        forces = compute_force_field(pts, bounds)
        if cell_idx < len(forces):
            return forces[cell_idx].components
        return (0.0, 0.0)

    # Base force
    f0x, f0y = _force_at(points)

    # Perturb x
    pts_dx = list(points)
    pts_dx[cell_idx] = (px + delta, py)
    fdx_x, fdx_y = _force_at(pts_dx)

    # Perturb y
    pts_dy = list(points)
    pts_dy[cell_idx] = (px, py + delta)
    fdy_x, fdy_y = _force_at(pts_dy)

    # Jacobian entries
    j11 = (fdx_x - f0x) / delta
    j12 = (fdy_x - f0x) / delta
    j21 = (fdx_y - f0y) / delta
    j22 = (fdy_y - f0y) / delta

    # Eigenvalues of 2x2 matrix
    trace = j11 + j22
    det = j11 * j22 - j12 * j21
    disc = trace * trace - 4 * det
    if disc < 0:
        # Complex eigenvalues: use real part
        real = trace / 2.0
        return (real, real)
    sqrt_disc = math.sqrt(disc)
    ev1 = (trace + sqrt_disc) / 2.0
    ev2 = (trace - sqrt_disc) / 2.0
    return (ev1, ev2)


# ---------------------------------------------------------------------------
# Engine 3: Basin of Attraction Mapping
# ---------------------------------------------------------------------------

def map_basins(points: List[Point], bounds=None,
               max_steps: int = 50) -> List[Basin]:
    """Map basins of attraction by gradient-following from each cell."""
    if not points:
        return []
    if bounds is None:
        bounds = _default_bounds(points)

    cells, adjacency = _compute_voronoi_cells(points, bounds)
    n = len(cells)
    forces = compute_force_field(points, bounds)

    # Find equilibria (low force cells)
    max_force = max((f.magnitude for f in forces), default=1.0)
    threshold = max(0.1, max_force * 0.15)
    eq_indices = [f.cell_idx for f in forces if f.magnitude <= threshold]

    if not eq_indices:
        # No clear equilibria: treat lowest-force cell as one
        eq_indices = [min(range(n), key=lambda i: forces[i].magnitude)]

    # Assign each cell to nearest equilibrium by gradient following
    basin_map: Dict[int, int] = {}  # cell_idx -> equilibrium_idx

    for i in range(n):
        current = i
        visited = set()
        for _ in range(max_steps):
            if current in eq_indices:
                basin_map[i] = current
                break
            if current in visited:
                # Cycle: assign to nearest equilibrium
                basin_map[i] = min(eq_indices,
                                   key=lambda e: _dist(cells[current]["centroid"],
                                                       cells[e]["centroid"]))
                break
            visited.add(current)
            # Move to neighbor with strongest pull (most force-aligned)
            neighbors = adjacency.get(current, [])
            if not neighbors:
                basin_map[i] = min(eq_indices,
                                   key=lambda e: _dist(cells[current]["centroid"],
                                                       cells[e]["centroid"]))
                break
            fv = forces[current]
            best_neighbor = current
            best_align = -float("inf")
            for nb in neighbors:
                dx = cells[nb]["centroid"][0] - cells[current]["centroid"][0]
                dy = cells[nb]["centroid"][1] - cells[current]["centroid"][1]
                d = math.sqrt(dx * dx + dy * dy)
                if d < 1e-12:
                    continue
                align = (fv.components[0] * dx + fv.components[1] * dy) / d
                if align > best_align:
                    best_align = align
                    best_neighbor = nb
            if best_neighbor == current:
                basin_map[i] = min(eq_indices,
                                   key=lambda e: _dist(cells[current]["centroid"],
                                                       cells[e]["centroid"]))
                break
            current = best_neighbor
        else:
            basin_map[i] = min(eq_indices,
                               key=lambda e: _dist(cells[current]["centroid"],
                                                   cells[e]["centroid"]))

    # Build Basin objects
    total_area = sum(c["area"] for c in cells)
    basins_dict: Dict[int, List[int]] = {}
    for cell_i, eq_i in basin_map.items():
        basins_dict.setdefault(eq_i, []).append(cell_i)

    basins = []
    for eq_idx, members in basins_dict.items():
        area_frac = sum(cells[m]["area"] for m in members) / total_area if total_area > 0 else 0
        basins.append(Basin(
            equilibrium_idx=eq_idx,
            member_cells=sorted(members),
            size=len(members),
            area_fraction=round(area_frac, 4),
        ))
    return basins


# ---------------------------------------------------------------------------
# Engine 4: Perturbation Response Predictor
# ---------------------------------------------------------------------------

def predict_perturbation(points: List[Point], cell_idx: int,
                         displacement: Tuple[float, float],
                         bounds=None) -> PerturbationResponse:
    """Predict response to displacing a cell's generator."""
    if bounds is None:
        bounds = _default_bounds(points)

    # Compute energy before
    forces_before = compute_force_field(points, bounds)
    energy_before = sum(f.magnitude ** 2 for f in forces_before)

    # Apply perturbation
    perturbed = list(points)
    px, py = perturbed[cell_idx]
    perturbed[cell_idx] = (px + displacement[0], py + displacement[1])

    # Compute energy after
    forces_after = compute_force_field(perturbed, bounds)
    energy_after = sum(f.magnitude ** 2 for f in forces_after)

    # Determine affected cells (those whose force changed significantly)
    affected = []
    propagation_depth = 0
    cells, adjacency = _compute_voronoi_cells(points, bounds)

    for i in range(len(forces_before)):
        if i >= len(forces_after):
            break
        delta_f = abs(forces_after[i].magnitude - forces_before[i].magnitude)
        if delta_f > 0.01:
            affected.append(i)

    # BFS to find propagation depth from source
    if affected:
        visited_depth: Dict[int, int] = {cell_idx: 0}
        queue = [cell_idx]
        while queue:
            curr = queue.pop(0)
            for nb in adjacency.get(curr, []):
                if nb not in visited_depth and nb in affected:
                    visited_depth[nb] = visited_depth[curr] + 1
                    queue.append(nb)
        if visited_depth:
            propagation_depth = max(visited_depth.values())

    # Estimate restabilization steps (proportional to energy delta)
    energy_delta = energy_after - energy_before
    restabilize = max(1, int(abs(energy_delta) * 10 + propagation_depth))

    return PerturbationResponse(
        source_cell=cell_idx,
        displacement=displacement,
        affected_cells=sorted(affected),
        propagation_depth=propagation_depth,
        energy_delta=round(energy_delta, 6),
        restabilize_steps=restabilize,
    )


# ---------------------------------------------------------------------------
# Engine 5: Tipping Point Detection
# ---------------------------------------------------------------------------

def detect_tipping_points(points: List[Point], bounds=None,
                          probe_steps: int = 8) -> List[TippingPoint]:
    """Find cells near basin boundaries where small perturbations cause shifts."""
    if not points or len(points) < 3:
        return []
    if bounds is None:
        bounds = _default_bounds(points)

    basins = map_basins(points, bounds)
    if len(basins) <= 1:
        return []

    # Build cell->basin lookup
    cell_basin: Dict[int, int] = {}
    for b in basins:
        for m in b.member_cells:
            cell_basin[m] = b.equilibrium_idx

    cells, adjacency = _compute_voronoi_cells(points, bounds)
    tipping_points = []

    for i in range(len(points)):
        neighbors = adjacency.get(i, [])
        # Check if any neighbor is in a different basin
        neighbor_basins = set(cell_basin.get(nb, -1) for nb in neighbors)
        my_basin = cell_basin.get(i, -1)
        other_basins = neighbor_basins - {my_basin, -1}

        if not other_basins:
            continue

        # Probe radial directions to find tipping margin
        target_basin = next(iter(other_basins))
        px, py = points[i]
        min_margin = float("inf")
        best_direction = (0.0, 0.0)

        for step in range(probe_steps):
            angle = 2 * math.pi * step / probe_steps
            dx = math.cos(angle)
            dy = math.sin(angle)

            # Binary search for tipping distance
            lo, hi = 0.0, 50.0
            for _ in range(10):
                mid = (lo + hi) / 2
                test_pts = list(points)
                test_pts[i] = (px + dx * mid, py + dy * mid)
                test_basins = map_basins(test_pts, bounds)
                test_cell_basin: Dict[int, int] = {}
                for b in test_basins:
                    for m in b.member_cells:
                        test_cell_basin[m] = b.equilibrium_idx
                if test_cell_basin.get(i, my_basin) != my_basin:
                    hi = mid
                else:
                    lo = mid
            if hi < min_margin:
                min_margin = hi
                best_direction = (dx, dy)

        tipping_points.append(TippingPoint(
            cell_idx=i,
            tipping_margin=round(min_margin, 4),
            current_basin=my_basin,
            target_basin=target_basin,
            critical_direction=best_direction,
        ))

    return tipping_points


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_equilibrium(points: List[Point], bounds=None,
                        perturbation_targets: Optional[List[Tuple[int, Tuple[float, float]]]] = None
                        ) -> EquilibriumReport:
    """Run full equilibrium analysis.

    Parameters
    ----------
    points : list of (x, y)
        Generator points for the Voronoi diagram.
    bounds : (xmin, xmax, ymin, ymax), optional
        Bounding rectangle. Auto-computed if None.
    perturbation_targets : list of (cell_idx, (dx, dy)), optional
        Cells to test perturbation response on.

    Returns
    -------
    EquilibriumReport
    """
    if bounds is None:
        bounds = _default_bounds(points)

    forces = compute_force_field(points, bounds)
    equilibria = classify_equilibria(points, bounds)
    basins = map_basins(points, bounds)

    # Perturbation responses
    responses = []
    if perturbation_targets:
        for cell_idx, displacement in perturbation_targets:
            if 0 <= cell_idx < len(points):
                resp = predict_perturbation(points, cell_idx, displacement, bounds)
                responses.append(resp)

    tipping = detect_tipping_points(points, bounds)

    # System energy and stability score
    system_energy = sum(f.magnitude ** 2 for f in forces)
    # Normalize: max possible energy approximation
    max_energy = len(forces) * (max((f.magnitude for f in forces), default=1.0) ** 2) if forces else 1.0
    normalized = system_energy / max_energy if max_energy > 0 else 0
    stability_score = round(100.0 * (1.0 - min(normalized, 1.0)), 1)

    return EquilibriumReport(
        forces=forces,
        equilibria=equilibria,
        basins=basins,
        perturbation_responses=responses,
        tipping_points=tipping,
        system_energy=round(system_energy, 6),
        global_stability_score=stability_score,
    )


# ---------------------------------------------------------------------------
# Engine 6: HTML Dashboard
# ---------------------------------------------------------------------------

def _build_html_dashboard(report: EquilibriumReport) -> str:
    """Build self-contained 4-tab HTML dashboard."""
    forces_json = json.dumps([
        {"idx": f.cell_idx, "mag": round(f.magnitude, 4),
         "dir": round(f.direction, 4), "fx": round(f.components[0], 4),
         "fy": round(f.components[1], 4)}
        for f in report.forces
    ])
    eq_json = json.dumps([
        {"idx": e.cell_idx, "cls": e.classification,
         "force": round(e.net_force, 4), "margin": round(e.stability_margin, 4)}
        for e in report.equilibria
    ])
    basins_json = json.dumps([
        {"eq": b.equilibrium_idx, "cells": b.member_cells,
         "size": b.size, "frac": b.area_fraction}
        for b in report.basins
    ])
    tips_json = json.dumps([
        {"idx": t.cell_idx, "margin": t.tipping_margin,
         "curr": t.current_basin, "target": t.target_basin}
        for t in report.tipping_points
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Spatial Equilibrium Dashboard</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #eee; }}
.tabs {{ display: flex; background: #16213e; border-bottom: 2px solid #0f3460; }}
.tab {{ padding: 12px 24px; cursor: pointer; border-bottom: 3px solid transparent; transition: all 0.2s; }}
.tab:hover {{ background: #1a2a4e; }}
.tab.active {{ border-bottom-color: #e94560; color: #e94560; }}
.panel {{ display: none; padding: 24px; max-width: 1200px; margin: 0 auto; }}
.panel.active {{ display: block; }}
.card {{ background: #16213e; border-radius: 8px; padding: 16px; margin: 12px 0; }}
.metric {{ display: inline-block; margin: 8px 16px; text-align: center; }}
.metric .value {{ font-size: 2em; color: #e94560; font-weight: bold; }}
.metric .label {{ font-size: 0.85em; color: #888; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #2a2a4e; }}
th {{ color: #e94560; }}
.stable {{ color: #4ecdc4; }}
.unstable {{ color: #ff6b6b; }}
.saddle {{ color: #ffd93d; }}
.score-bar {{ height: 20px; border-radius: 10px; background: #2a2a4e; overflow: hidden; }}
.score-fill {{ height: 100%; border-radius: 10px; background: linear-gradient(90deg, #e94560, #4ecdc4); }}
</style>
</head>
<body>
<div class="tabs">
  <div class="tab active" onclick="showTab(0)">Force Field</div>
  <div class="tab" onclick="showTab(1)">Equilibria</div>
  <div class="tab" onclick="showTab(2)">Basins</div>
  <div class="tab" onclick="showTab(3)">Tipping Points</div>
</div>

<div class="panel active" id="panel0">
  <div class="card">
    <div class="metric"><div class="value">{len(report.forces)}</div><div class="label">Cells</div></div>
    <div class="metric"><div class="value">{report.system_energy:.4f}</div><div class="label">System Energy</div></div>
    <div class="metric"><div class="value">{report.global_stability_score:.1f}</div><div class="label">Stability Score</div></div>
    <div style="margin-top:12px;">
      <div class="score-bar"><div class="score-fill" style="width:{report.global_stability_score}%"></div></div>
    </div>
  </div>
  <div class="card">
    <h3>Force Vectors</h3>
    <table><tr><th>Cell</th><th>Magnitude</th><th>Direction (rad)</th><th>Fx</th><th>Fy</th></tr>
    <tbody id="forceTable"></tbody></table>
  </div>
</div>

<div class="panel" id="panel1">
  <div class="card">
    <div class="metric"><div class="value">{len(report.equilibria)}</div><div class="label">Equilibria</div></div>
    <div class="metric"><div class="value">{sum(1 for e in report.equilibria if e.classification=='stable')}</div><div class="label">Stable</div></div>
    <div class="metric"><div class="value">{sum(1 for e in report.equilibria if e.classification=='unstable')}</div><div class="label">Unstable</div></div>
    <div class="metric"><div class="value">{sum(1 for e in report.equilibria if e.classification=='saddle')}</div><div class="label">Saddle</div></div>
  </div>
  <div class="card">
    <table><tr><th>Cell</th><th>Classification</th><th>Net Force</th><th>Stability Margin</th></tr>
    <tbody id="eqTable"></tbody></table>
  </div>
</div>

<div class="panel" id="panel2">
  <div class="card">
    <div class="metric"><div class="value">{len(report.basins)}</div><div class="label">Basins</div></div>
  </div>
  <div class="card">
    <table><tr><th>Equilibrium</th><th>Size</th><th>Area Fraction</th><th>Member Cells</th></tr>
    <tbody id="basinTable"></tbody></table>
  </div>
</div>

<div class="panel" id="panel3">
  <div class="card">
    <div class="metric"><div class="value">{len(report.tipping_points)}</div><div class="label">Tipping Points</div></div>
  </div>
  <div class="card">
    <table><tr><th>Cell</th><th>Tipping Margin</th><th>Current Basin</th><th>Target Basin</th></tr>
    <tbody id="tipTable"></tbody></table>
  </div>
</div>

<script>
const forces = {forces_json};
const eqs = {eq_json};
const basins = {basins_json};
const tips = {tips_json};

function showTab(i) {{
  document.querySelectorAll('.tab').forEach((t,j) => t.classList.toggle('active', j===i));
  document.querySelectorAll('.panel').forEach((p,j) => p.classList.toggle('active', j===i));
}}

function populate() {{
  let ft = document.getElementById('forceTable');
  forces.forEach(f => {{
    ft.innerHTML += `<tr><td>${{f.idx}}</td><td>${{f.mag.toFixed(4)}}</td><td>${{f.dir.toFixed(4)}}</td><td>${{f.fx.toFixed(4)}}</td><td>${{f.fy.toFixed(4)}}</td></tr>`;
  }});
  let et = document.getElementById('eqTable');
  eqs.forEach(e => {{
    let cls = e.cls === 'stable' ? 'stable' : e.cls === 'unstable' ? 'unstable' : 'saddle';
    et.innerHTML += `<tr><td>${{e.idx}}</td><td class="${{cls}}">${{e.cls}}</td><td>${{e.force.toFixed(4)}}</td><td>${{e.margin.toFixed(4)}}</td></tr>`;
  }});
  let bt = document.getElementById('basinTable');
  basins.forEach(b => {{
    bt.innerHTML += `<tr><td>${{b.eq}}</td><td>${{b.size}}</td><td>${{(b.frac*100).toFixed(1)}}%</td><td>${{b.cells.join(', ')}}</td></tr>`;
  }});
  let tt = document.getElementById('tipTable');
  tips.forEach(t => {{
    tt.innerHTML += `<tr><td>${{t.idx}}</td><td>${{t.margin.toFixed(4)}}</td><td>${{t.curr}}</td><td>${{t.target}}</td></tr>`;
  }});
}}
populate();
</script>
</body>
</html>"""
