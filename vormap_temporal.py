"""Voronoi Temporal Dynamics -- track how diagrams evolve over time.

Analyses sequences of point snapshots to identify cell birth, death,
split, merge, migration, and area change events.  Useful for tracking
facility movements, ecological territory shifts, urban growth, and
any spatial process where Voronoi regions change over time.

Usage::

    from vormap_temporal import (
        temporal_analysis, TemporalResult, CellEvent,
    )

    # Three time snapshots of point positions
    snapshots = [
        [(100, 200), (300, 400), (500, 100)],
        [(110, 210), (300, 400), (500, 100), (700, 300)],
        [(120, 220), (500, 100), (700, 300)],
    ]
    result = temporal_analysis(snapshots, match_radius=50.0)
    print(result.summary())

    for event in result.events:
        print(f"t={event.time_step}: {event.event_type} at {event.seed}")

    # CLI
    python vormap_temporal.py snap1.txt snap2.txt snap3.txt --match-radius 50
    python vormap_temporal.py snap1.txt snap2.txt --json temporal.json
    python vormap_temporal.py snap1.txt snap2.txt --csv temporal.csv

CLI supports 2+ snapshot files.  Each file uses the standard vormap
point format (one ``x y`` pair per line).
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import vormap
from vormap_viz import compute_regions, compute_region_stats


# -- Data classes -----------------------------------------------------

@dataclass
class CellEvent:
    """A discrete event in the life of a Voronoi cell.

    Attributes
    ----------
    time_step : int
        0-based index of the transition (step i -> i+1).
    event_type : str
        One of: "birth", "death", "migrate", "grow", "shrink", "stable".
    seed : tuple of (float, float)
        Seed coordinates at the time of the event.
    prev_seed : tuple of (float, float) or None
        Seed coordinates at the previous step (None for births).
    area_before : float
        Cell area before the transition (0 for births).
    area_after : float
        Cell area after the transition (0 for deaths).
    area_change : float
        Absolute area change.
    area_change_pct : float
        Percentage area change relative to the before area.
    migration_dist : float
        Euclidean distance the seed moved between steps.
    details : str
        Human-readable description of the event.
    """
    time_step: int = 0
    event_type: str = ""
    seed: Tuple[float, float] = (0.0, 0.0)
    prev_seed: Optional[Tuple[float, float]] = None
    area_before: float = 0.0
    area_after: float = 0.0
    area_change: float = 0.0
    area_change_pct: float = 0.0
    migration_dist: float = 0.0
    details: str = ""


@dataclass
class TransitionStats:
    """Summary statistics for a single time step transition.

    Attributes
    ----------
    step : int
        0-based transition index (step i -> i+1).
    births : int
        Number of new cells appearing.
    deaths : int
        Number of cells disappearing.
    migrations : int
        Number of cells whose seed moved.
    mean_migration : float
        Average migration distance across all matched cells.
    max_migration : float
        Maximum migration distance.
    mean_area_change_pct : float
        Average absolute area change percentage.
    total_cells_before : int
        Number of cells at step i.
    total_cells_after : int
        Number of cells at step i+1.
    jaccard_index : float
        Set similarity of seeds between steps (intersection / union).
    """
    step: int = 0
    births: int = 0
    deaths: int = 0
    migrations: int = 0
    mean_migration: float = 0.0
    max_migration: float = 0.0
    mean_area_change_pct: float = 0.0
    total_cells_before: int = 0
    total_cells_after: int = 0
    jaccard_index: float = 0.0


@dataclass
class CellTrajectory:
    """The full trajectory of a single cell across all time steps.

    Attributes
    ----------
    initial_seed : tuple of (float, float)
        Seed position at first appearance.
    birth_step : int
        Time step when the cell first appeared.
    death_step : int or None
        Time step when the cell disappeared (None if still alive).
    positions : list of (float, float)
        Seed position at each step where the cell existed.
    areas : list of float
        Cell area at each step where the cell existed.
    total_migration : float
        Total cumulative distance the seed moved.
    lifespan : int
        Number of steps the cell existed.
    """
    initial_seed: Tuple[float, float] = (0.0, 0.0)
    birth_step: int = 0
    death_step: Optional[int] = None
    positions: List[Tuple[float, float]] = field(default_factory=list)
    areas: List[float] = field(default_factory=list)
    total_migration: float = 0.0
    lifespan: int = 0


@dataclass
class TemporalResult:
    """Complete result of a temporal Voronoi analysis.

    Attributes
    ----------
    num_snapshots : int
        Number of input time snapshots.
    events : list of CellEvent
        All detected events across all transitions.
    transitions : list of TransitionStats
        Per-transition summary statistics.
    trajectories : list of CellTrajectory
        Per-cell trajectories across time.
    overall_stability : float
        0-1 score (1 = perfectly stable, 0 = completely different).
    match_radius : float
        Radius used for seed matching between steps.
    """
    num_snapshots: int = 0
    events: List[CellEvent] = field(default_factory=list)
    transitions: List[TransitionStats] = field(default_factory=list)
    trajectories: List[CellTrajectory] = field(default_factory=list)
    overall_stability: float = 0.0
    match_radius: float = 0.0

    def summary(self) -> str:
        """Human-readable summary of the temporal analysis."""
        lines = []
        lines.append(f"Temporal Voronoi Analysis: {self.num_snapshots} snapshots")
        lines.append(f"Match radius: {self.match_radius:.1f}")
        lines.append(f"Overall stability: {self.overall_stability:.3f}")
        lines.append("")

        total_births = sum(t.births for t in self.transitions)
        total_deaths = sum(t.deaths for t in self.transitions)
        total_events = len(self.events)

        lines.append(f"Total events: {total_events}")
        lines.append(f"  Births: {total_births}")
        lines.append(f"  Deaths: {total_deaths}")
        lines.append(f"  Trajectories tracked: {len(self.trajectories)}")

        if self.trajectories:
            lifespans = [t.lifespan for t in self.trajectories]
            mean_life = sum(lifespans) / len(lifespans)
            max_life = max(lifespans)
            lines.append(f"  Mean lifespan: {mean_life:.1f} steps")
            lines.append(f"  Max lifespan: {max_life} steps")

        lines.append("")
        for ts in self.transitions:
            lines.append(
                f"Step {ts.step} -> {ts.step + 1}: "
                f"{ts.total_cells_before} -> {ts.total_cells_after} cells, "
                f"+{ts.births} -{ts.deaths}, "
                f"Jaccard={ts.jaccard_index:.3f}"
            )
            if ts.mean_migration > 0:
                lines.append(
                    f"  Migration: mean={ts.mean_migration:.2f}, "
                    f"max={ts.max_migration:.2f}"
                )

        return "\n".join(lines)


# -- Helpers ----------------------------------------------------------

def _euclidean(a, b):
    """Euclidean distance between two 2D points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _polygon_area(vertices):
    """Shoelace formula for polygon area (unsigned)."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _match_seeds(seeds_a, seeds_b, radius):
    """Match seeds between two snapshots using nearest-neighbor within radius.

    Uses a greedy closest-first matching strategy to avoid ambiguity.

    Returns
    -------
    matches : dict
        Maps index in seeds_a to index in seeds_b.
    unmatched_a : list of int
        Indices in seeds_a with no match (deaths).
    unmatched_b : list of int
        Indices in seeds_b with no match (births).
    """
    n_a = len(seeds_a)
    n_b = len(seeds_b)

    # Compute all pairwise distances within radius
    candidates = []
    for i in range(n_a):
        for j in range(n_b):
            d = _euclidean(seeds_a[i], seeds_b[j])
            if d <= radius:
                candidates.append((d, i, j))

    # Greedy closest-first matching
    candidates.sort()
    matched_a = set()
    matched_b = set()
    matches = {}

    for d, i, j in candidates:
        if i in matched_a or j in matched_b:
            continue
        matches[i] = j
        matched_a.add(i)
        matched_b.add(j)

    unmatched_a = [i for i in range(n_a) if i not in matched_a]
    unmatched_b = [j for j in range(n_b) if j not in matched_b]

    return matches, unmatched_a, unmatched_b


def _get_cell_areas(points):
    """Compute Voronoi cell areas for a set of points.

    Returns a dict mapping seed tuple to area.
    """
    if len(points) < 2:
        return {}
    # Recompute bounds so that compute_regions covers all points —
    # without this, snapshots whose extents differ from the initial
    # data produce silently incorrect cell areas.
    s, n, w, e = vormap.compute_bounds(points)
    vormap.set_bounds(s, n, w, e)
    regions = compute_regions(points)
    areas = {}
    for seed, vertices in regions.items():
        areas[seed] = _polygon_area(vertices)
    return areas


# -- Core analysis ----------------------------------------------------

def temporal_analysis(
    snapshots,
    *,
    match_radius=50.0,
    area_change_threshold=10.0,
):
    """Analyse how a Voronoi diagram evolves across time snapshots.

    Parameters
    ----------
    snapshots : list of list of (x, y)
        Two or more point-set snapshots in chronological order.
    match_radius : float
        Maximum distance to consider two seeds as the "same" cell
        across consecutive snapshots.  Default 50.0.
    area_change_threshold : float
        Percentage area change below which a cell is considered
        "stable" rather than "growing" or "shrinking".  Default 10.0.

    Returns
    -------
    TemporalResult
        Full analysis including events, transitions, and trajectories.

    Raises
    ------
    ValueError
        If fewer than 2 snapshots are provided or any snapshot is empty.
    """
    if len(snapshots) < 2:
        raise ValueError("At least 2 snapshots required for temporal analysis")
    for i, snap in enumerate(snapshots):
        if not snap:
            raise ValueError(f"Snapshot {i} is empty")

    # Normalize points to tuples
    norm_snapshots = [
        [(float(p[0]), float(p[1])) for p in snap]
        for snap in snapshots
    ]

    # Compute areas for each snapshot
    snapshot_areas = [_get_cell_areas(snap) for snap in norm_snapshots]

    all_events = []
    all_transitions = []

    # Track cell identity across steps
    active_trajectories = {}
    trajectories = []

    # Initialize trajectories for first snapshot
    for i, seed in enumerate(norm_snapshots[0]):
        traj = CellTrajectory(
            initial_seed=seed,
            birth_step=0,
            positions=[seed],
            areas=[snapshot_areas[0].get(seed, 0.0)],
            lifespan=1,
        )
        trajectories.append(traj)
        active_trajectories[i] = len(trajectories) - 1

    # Process each transition
    for step in range(len(norm_snapshots) - 1):
        seeds_a = norm_snapshots[step]
        seeds_b = norm_snapshots[step + 1]
        areas_a = snapshot_areas[step]
        areas_b = snapshot_areas[step + 1]

        matches, deaths_idx, births_idx = _match_seeds(
            seeds_a, seeds_b, match_radius
        )

        step_events = []
        migration_dists = []
        area_changes = []
        new_active = {}

        # Process matched cells
        for idx_a, idx_b in matches.items():
            seed_a = seeds_a[idx_a]
            seed_b = seeds_b[idx_b]
            area_a = areas_a.get(seed_a, 0.0)
            area_b = areas_b.get(seed_b, 0.0)
            dist = _euclidean(seed_a, seed_b)
            migration_dists.append(dist)

            a_change = area_b - area_a
            a_change_pct = (
                abs(a_change) / area_a * 100.0 if area_a > 0 else 0.0
            )
            area_changes.append(a_change_pct)

            # Determine event type
            if a_change_pct > area_change_threshold and a_change > 0:
                event_type = "grow"
                details = (
                    f"Area grew {a_change_pct:.1f}% "
                    f"({area_a:.1f} -> {area_b:.1f})"
                )
            elif a_change_pct > area_change_threshold and a_change < 0:
                event_type = "shrink"
                details = (
                    f"Area shrank {a_change_pct:.1f}% "
                    f"({area_a:.1f} -> {area_b:.1f})"
                )
            elif dist > 0.01:
                event_type = "migrate"
                details = f"Migrated {dist:.2f} units"
            else:
                event_type = "stable"
                details = "No significant change"

            step_events.append(CellEvent(
                time_step=step,
                event_type=event_type,
                seed=seed_b,
                prev_seed=seed_a,
                area_before=area_a,
                area_after=area_b,
                area_change=a_change,
                area_change_pct=a_change_pct,
                migration_dist=dist,
                details=details,
            ))

            # Update trajectory
            traj_idx = active_trajectories.get(idx_a)
            if traj_idx is not None:
                traj = trajectories[traj_idx]
                traj.positions.append(seed_b)
                traj.areas.append(area_b)
                traj.total_migration += dist
                traj.lifespan += 1
                new_active[idx_b] = traj_idx

        # Process deaths
        for idx_a in deaths_idx:
            seed_a = seeds_a[idx_a]
            area_a = areas_a.get(seed_a, 0.0)
            step_events.append(CellEvent(
                time_step=step,
                event_type="death",
                seed=seed_a,
                prev_seed=seed_a,
                area_before=area_a,
                area_after=0.0,
                area_change=-area_a,
                area_change_pct=100.0,
                migration_dist=0.0,
                details=f"Cell disappeared (area was {area_a:.1f})",
            ))
            traj_idx = active_trajectories.get(idx_a)
            if traj_idx is not None:
                trajectories[traj_idx].death_step = step + 1

        # Process births
        for idx_b in births_idx:
            seed_b = seeds_b[idx_b]
            area_b = areas_b.get(seed_b, 0.0)
            step_events.append(CellEvent(
                time_step=step,
                event_type="birth",
                seed=seed_b,
                prev_seed=None,
                area_before=0.0,
                area_after=area_b,
                area_change=area_b,
                area_change_pct=0.0,
                migration_dist=0.0,
                details=f"New cell appeared (area {area_b:.1f})",
            ))
            traj = CellTrajectory(
                initial_seed=seed_b,
                birth_step=step + 1,
                positions=[seed_b],
                areas=[area_b],
                lifespan=1,
            )
            trajectories.append(traj)
            new_active[idx_b] = len(trajectories) - 1

        active_trajectories = new_active
        all_events.extend(step_events)

        # Compute transition statistics
        mean_mig = (
            sum(migration_dists) / len(migration_dists)
            if migration_dists else 0.0
        )
        max_mig = max(migration_dists) if migration_dists else 0.0
        mean_area_chg = (
            sum(area_changes) / len(area_changes)
            if area_changes else 0.0
        )

        intersection = len(matches)
        union = len(seeds_a) + len(seeds_b) - intersection
        jaccard = intersection / union if union > 0 else 1.0

        all_transitions.append(TransitionStats(
            step=step,
            births=len(births_idx),
            deaths=len(deaths_idx),
            migrations=sum(1 for d in migration_dists if d > 0.01),
            mean_migration=mean_mig,
            max_migration=max_mig,
            mean_area_change_pct=mean_area_chg,
            total_cells_before=len(seeds_a),
            total_cells_after=len(seeds_b),
            jaccard_index=jaccard,
        ))

    # Overall stability: average Jaccard across transitions
    overall = (
        sum(t.jaccard_index for t in all_transitions) / len(all_transitions)
        if all_transitions else 1.0
    )

    return TemporalResult(
        num_snapshots=len(snapshots),
        events=all_events,
        transitions=all_transitions,
        trajectories=trajectories,
        overall_stability=overall,
        match_radius=match_radius,
    )


# -- Export -----------------------------------------------------------

def export_json(result, path):
    """Export temporal analysis to JSON.

    Parameters
    ----------
    result : TemporalResult
        Output of ``temporal_analysis()``.
    path : str
        Output file path.
    """
    validated = vormap.validate_output_path(path)
    data = {
        "num_snapshots": result.num_snapshots,
        "match_radius": result.match_radius,
        "overall_stability": round(result.overall_stability, 4),
        "events": [
            {
                "time_step": e.time_step,
                "event_type": e.event_type,
                "seed": list(e.seed),
                "prev_seed": list(e.prev_seed) if e.prev_seed else None,
                "area_before": round(e.area_before, 2),
                "area_after": round(e.area_after, 2),
                "area_change": round(e.area_change, 2),
                "area_change_pct": round(e.area_change_pct, 2),
                "migration_dist": round(e.migration_dist, 2),
                "details": e.details,
            }
            for e in result.events
        ],
        "transitions": [
            {
                "step": t.step,
                "births": t.births,
                "deaths": t.deaths,
                "migrations": t.migrations,
                "mean_migration": round(t.mean_migration, 4),
                "max_migration": round(t.max_migration, 4),
                "mean_area_change_pct": round(t.mean_area_change_pct, 2),
                "cells_before": t.total_cells_before,
                "cells_after": t.total_cells_after,
                "jaccard_index": round(t.jaccard_index, 4),
            }
            for t in result.transitions
        ],
        "trajectories": [
            {
                "initial_seed": list(tr.initial_seed),
                "birth_step": tr.birth_step,
                "death_step": tr.death_step,
                "positions": [list(p) for p in tr.positions],
                "areas": [round(a, 2) for a in tr.areas],
                "total_migration": round(tr.total_migration, 2),
                "lifespan": tr.lifespan,
            }
            for tr in result.trajectories
        ],
    }
    with open(validated, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def export_csv(result, path):
    """Export temporal events to CSV.

    Parameters
    ----------
    result : TemporalResult
        Output of ``temporal_analysis()``.
    path : str
        Output file path.
    """
    validated = vormap.validate_output_path(path)
    with open(validated, "w", encoding="utf-8") as f:
        f.write(
            "time_step,event_type,seed_x,seed_y,"
            "prev_seed_x,prev_seed_y,"
            "area_before,area_after,area_change,area_change_pct,"
            "migration_dist,details\n"
        )
        for e in result.events:
            prev_x = e.prev_seed[0] if e.prev_seed else ""
            prev_y = e.prev_seed[1] if e.prev_seed else ""
            details = e.details.replace('"', '""')
            f.write(
                f"{e.time_step},{e.event_type},"
                f"{e.seed[0]:.4f},{e.seed[1]:.4f},"
                f"{prev_x},{prev_y},"
                f"{e.area_before:.2f},{e.area_after:.2f},"
                f"{e.area_change:.2f},{e.area_change_pct:.2f},"
                f"{e.migration_dist:.4f},\"{details}\"\n"
            )


# -- CLI --------------------------------------------------------------

def main(argv=None):
    """CLI entry point for temporal Voronoi analysis.

    Usage::

        python vormap_temporal.py snap1.txt snap2.txt [snap3.txt ...] [options]

    Options:
        --match-radius R     Seed matching radius (default: 50.0)
        --area-threshold P   Area change threshold % (default: 10.0)
        --json FILE          Export results to JSON
        --csv FILE           Export events to CSV
    """
    args = argv if argv is not None else sys.argv[1:]

    snapshot_files = []
    match_radius = 50.0
    area_threshold = 10.0
    json_path = None
    csv_path = None

    i = 0
    while i < len(args):
        if args[i] == "--match-radius" and i + 1 < len(args):
            match_radius = float(args[i + 1])
            i += 2
        elif args[i] == "--area-threshold" and i + 1 < len(args):
            area_threshold = float(args[i + 1])
            i += 2
        elif args[i] == "--json" and i + 1 < len(args):
            json_path = args[i + 1]
            i += 2
        elif args[i] == "--csv" and i + 1 < len(args):
            csv_path = args[i + 1]
            i += 2
        elif args[i].startswith("--"):
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)
        else:
            snapshot_files.append(args[i])
            i += 1

    if len(snapshot_files) < 2:
        print(
            "Usage: python vormap_temporal.py snap1.txt snap2.txt "
            "[snap3.txt ...] [--match-radius R] [--json FILE] [--csv FILE]",
            file=sys.stderr,
        )
        sys.exit(1)

    snapshots = []
    for filepath in snapshot_files:
        validated = vormap.validate_input_path(filepath, allow_absolute=True)
        data = vormap.load_data(validated)
        points = [(d["x"], d["y"]) if isinstance(d, dict) else (d[0], d[1])
                  for d in data]
        snapshots.append(points)

    result = temporal_analysis(
        snapshots,
        match_radius=match_radius,
        area_change_threshold=area_threshold,
    )

    print(result.summary())

    if json_path:
        export_json(result, json_path)
        print(f"\nJSON exported to: {json_path}")

    if csv_path:
        export_csv(result, csv_path)
        print(f"\nCSV exported to: {csv_path}")


if __name__ == "__main__":
    main()
