"""Tests for vormap_temporal -- Voronoi temporal dynamics analysis."""

import json
import math
import os
import tempfile

import pytest

from vormap_temporal import (
    CellEvent,
    CellTrajectory,
    TemporalResult,
    TransitionStats,
    _euclidean,
    _match_seeds,
    _get_cell_areas,
    export_csv,
    export_json,
    temporal_analysis,
)
from vormap_geometry import polygon_area as _polygon_area
from vormap_viz import compute_regions


# -- Test helpers -----------------------------------------------------

def _make_grid(n=3, spacing=100.0, offset_x=0.0, offset_y=0.0):
    """Create an n x n grid of points."""
    return [
        (x * spacing + offset_x, y * spacing + offset_y)
        for x in range(n) for y in range(n)
    ]


def _shift_points(points, dx, dy):
    """Shift all points by (dx, dy)."""
    return [(p[0] + dx, p[1] + dy) for p in points]


# -- Helper tests -----------------------------------------------------

class TestEuclidean:
    def test_same_point(self):
        assert _euclidean((0, 0), (0, 0)) == 0.0

    def test_unit_distance(self):
        assert _euclidean((0, 0), (1, 0)) == 1.0

    def test_diagonal(self):
        d = _euclidean((0, 0), (3, 4))
        assert abs(d - 5.0) < 1e-10

    def test_negative_coords(self):
        d = _euclidean((-1, -1), (2, 3))
        assert abs(d - 5.0) < 1e-10


class TestPolygonArea:
    def test_triangle(self):
        verts = [(0, 0), (4, 0), (0, 3)]
        assert abs(_polygon_area(verts) - 6.0) < 1e-10

    def test_square(self):
        verts = [(0, 0), (2, 0), (2, 2), (0, 2)]
        assert abs(_polygon_area(verts) - 4.0) < 1e-10

    def test_degenerate(self):
        assert _polygon_area([(0, 0), (1, 1)]) == 0.0
        assert _polygon_area([]) == 0.0


class TestMatchSeeds:
    def test_identical_seeds(self):
        seeds = [(0, 0), (100, 100), (200, 200)]
        matches, deaths, births = _match_seeds(seeds, seeds, 10.0)
        assert len(matches) == 3
        assert deaths == []
        assert births == []

    def test_all_deaths(self):
        a = [(0, 0), (100, 100)]
        b = [(500, 500), (600, 600)]
        matches, deaths, births = _match_seeds(a, b, 10.0)
        assert len(matches) == 0
        assert len(deaths) == 2
        assert len(births) == 2

    def test_partial_match(self):
        a = [(0, 0), (100, 100), (200, 200)]
        b = [(5, 5), (200, 200), (500, 500)]
        matches, deaths, births = _match_seeds(a, b, 20.0)
        assert 0 in matches  # (0,0) -> (5,5)
        assert 2 in matches  # (200,200) -> (200,200)
        assert 1 in deaths   # (100,100) unmatched
        assert len(births) == 1  # (500,500) is new

    def test_greedy_closest_first(self):
        """Closer pairs should be matched first."""
        a = [(0, 0), (8, 0)]
        b = [(7, 0)]  # dist 7 from a[0], dist 1 from a[1]
        matches, deaths, births = _match_seeds(a, b, 20.0)
        # a[1]=(8,0) is closer to b[0]=(7,0) — should be matched
        assert matches[1] == 0
        assert 0 in deaths

    def test_empty_lists(self):
        matches, deaths, births = _match_seeds([], [], 10.0)
        assert matches == {}
        assert deaths == []
        assert births == []


class TestGetCellAreas:
    def test_basic_grid(self):
        points = _make_grid(3, 100.0)
        areas = _get_cell_areas(points)
        assert len(areas) > 0
        for seed, area in areas.items():
            assert area >= 0

    def test_single_point(self):
        areas = _get_cell_areas([(0, 0)])
        assert areas == {}


# -- Core analysis tests ----------------------------------------------

class TestTemporalAnalysis:
    def test_identical_snapshots(self):
        pts = _make_grid(3, 100.0)
        result = temporal_analysis([pts, pts], match_radius=10.0)
        assert result.num_snapshots == 2
        assert result.overall_stability == 1.0
        assert len(result.transitions) == 1
        assert result.transitions[0].births == 0
        assert result.transitions[0].deaths == 0

    def test_small_migration(self):
        pts1 = [(100, 100), (300, 300), (500, 500)]
        pts2 = [(105, 105), (305, 305), (505, 505)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        assert result.num_snapshots == 2
        assert result.overall_stability == 1.0
        # All cells should be matched (no births/deaths)
        assert result.transitions[0].births == 0
        assert result.transitions[0].deaths == 0
        # All cells migrated
        migrate_events = [e for e in result.events if e.event_type == "migrate"]
        assert len(migrate_events) >= 1

    def test_birth_and_death(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(100, 100), (700, 700)]  # (300,300) dies, (700,700) born
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        births = [e for e in result.events if e.event_type == "birth"]
        deaths = [e for e in result.events if e.event_type == "death"]
        assert len(births) == 1
        assert len(deaths) == 1
        assert result.transitions[0].births == 1
        assert result.transitions[0].deaths == 1

    def test_three_snapshots(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(110, 110), (310, 310)]
        pts3 = [(120, 120), (320, 320)]
        result = temporal_analysis(
            [pts1, pts2, pts3], match_radius=50.0
        )
        assert result.num_snapshots == 3
        assert len(result.transitions) == 2

    def test_growing_point_set(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(100, 100), (300, 300), (500, 500)]
        pts3 = [(100, 100), (300, 300), (500, 500), (700, 700)]
        result = temporal_analysis(
            [pts1, pts2, pts3], match_radius=50.0
        )
        total_births = sum(t.births for t in result.transitions)
        assert total_births == 2  # One birth per step

    def test_shrinking_point_set(self):
        pts1 = [(100, 100), (300, 300), (500, 500)]
        pts2 = [(100, 100), (300, 300)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        assert result.transitions[0].deaths == 1

    def test_complete_replacement(self):
        pts1 = [(100, 100), (200, 200)]
        pts2 = [(600, 600), (700, 700)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        assert result.transitions[0].births == 2
        assert result.transitions[0].deaths == 2
        assert result.overall_stability == 0.0

    def test_match_radius_affects_matching(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(130, 130), (330, 330)]  # moved 42 units

        # With large radius: matched
        r1 = temporal_analysis([pts1, pts2], match_radius=100.0)
        assert r1.transitions[0].deaths == 0

        # With small radius: not matched
        r2 = temporal_analysis([pts1, pts2], match_radius=10.0)
        assert r2.transitions[0].deaths == 2

    def test_validation_too_few_snapshots(self):
        with pytest.raises(ValueError, match="At least 2"):
            temporal_analysis([[(0, 0)]])

    def test_validation_empty_snapshot(self):
        with pytest.raises(ValueError, match="empty"):
            temporal_analysis([[(0, 0)], []])


# -- Trajectory tests -------------------------------------------------

class TestTrajectories:
    def test_stable_trajectory(self):
        pts = [(100, 100), (300, 300)]
        result = temporal_analysis([pts, pts, pts], match_radius=10.0)
        # Should have 2 trajectories, each with lifespan 3
        long_lived = [t for t in result.trajectories if t.lifespan == 3]
        assert len(long_lived) == 2

    def test_trajectory_lifespan(self):
        pts1 = [(100, 100)]
        pts2 = [(100, 100), (500, 500)]
        pts3 = [(100, 100), (500, 500)]
        result = temporal_analysis([pts1, pts2, pts3], match_radius=50.0)
        lifespans = sorted([t.lifespan for t in result.trajectories])
        assert 3 in lifespans  # original point lives all 3 steps
        assert 2 in lifespans  # new point lives 2 steps

    def test_trajectory_migration(self):
        pts1 = [(100, 100)]
        pts2 = [(110, 100)]
        pts3 = [(120, 100)]
        result = temporal_analysis([pts1, pts2, pts3], match_radius=50.0)
        traj = result.trajectories[0]
        assert abs(traj.total_migration - 20.0) < 1e-6
        assert len(traj.positions) == 3

    def test_trajectory_death_step(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(100, 100)]  # (300,300) dies
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        dead = [t for t in result.trajectories if t.death_step is not None]
        assert len(dead) == 1
        assert dead[0].death_step == 1

    def test_trajectory_birth_step(self):
        pts1 = [(100, 100)]
        pts2 = [(100, 100), (500, 500)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        born_later = [t for t in result.trajectories if t.birth_step == 1]
        assert len(born_later) == 1


# -- Transition stats tests -------------------------------------------

class TestTransitionStats:
    def test_jaccard_identical(self):
        pts = _make_grid(3, 100.0)
        result = temporal_analysis([pts, pts], match_radius=10.0)
        assert result.transitions[0].jaccard_index == 1.0

    def test_jaccard_no_overlap(self):
        pts1 = [(100, 100)]
        pts2 = [(900, 900)]
        result = temporal_analysis([pts1, pts2], match_radius=10.0)
        assert result.transitions[0].jaccard_index == 0.0

    def test_cell_counts(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(100, 100), (300, 300), (500, 500)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        assert result.transitions[0].total_cells_before == 2
        assert result.transitions[0].total_cells_after == 3


# -- Event type tests -------------------------------------------------

class TestEventTypes:
    def test_stable_event(self):
        pts = [(100, 100), (300, 300)]
        result = temporal_analysis([pts, pts], match_radius=10.0)
        stable = [e for e in result.events if e.event_type == "stable"]
        assert len(stable) == 2

    def test_birth_event_fields(self):
        pts1 = [(100, 100), (300, 300), (500, 100)]
        pts2 = [(100, 100), (300, 300), (500, 100), (300, 100)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        births = [e for e in result.events if e.event_type == "birth"]
        assert len(births) == 1
        b = births[0]
        assert b.prev_seed is None
        assert b.area_before == 0.0
        assert b.area_after >= 0  # may be 0 for edge cells
        assert "New cell" in b.details

    def test_death_event_fields(self):
        pts1 = [(100, 100), (500, 500)]
        pts2 = [(100, 100)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        deaths = [e for e in result.events if e.event_type == "death"]
        assert len(deaths) == 1
        d = deaths[0]
        assert d.area_after == 0.0
        assert "disappeared" in d.details


# -- Summary tests ----------------------------------------------------

class TestSummary:
    def test_summary_contains_key_info(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(110, 110), (300, 300), (500, 500)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)
        summary = result.summary()
        assert "Temporal Voronoi Analysis" in summary
        assert "2 snapshots" in summary
        assert "stability" in summary.lower()
        assert "Births" in summary
        assert "Deaths" in summary

    def test_summary_empty_trajectories(self):
        pts1 = [(100, 100)]
        pts2 = [(900, 900)]
        result = temporal_analysis([pts1, pts2], match_radius=10.0)
        summary = result.summary()
        assert "Temporal" in summary


# -- Export tests -----------------------------------------------------

class TestExportJSON:
    def test_export_creates_file(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(110, 110), (310, 310)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)

        path = "_test_temporal_out.json"
        try:
            export_json(result, path)
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert data["num_snapshots"] == 2
            assert "events" in data
            assert "transitions" in data
            assert "trajectories" in data
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_export_event_structure(self):
        pts1 = [(100, 100)]
        pts2 = [(100, 100), (500, 500)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)

        path = "_test_temporal_ev.json"
        try:
            export_json(result, path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for ev in data["events"]:
                assert "time_step" in ev
                assert "event_type" in ev
                assert "seed" in ev
                assert "details" in ev
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportCSV:
    def test_export_creates_file(self):
        pts1 = [(100, 100), (300, 300)]
        pts2 = [(110, 110), (310, 310)]
        result = temporal_analysis([pts1, pts2], match_radius=50.0)

        path = "_test_temporal_out.csv"
        try:
            export_csv(result, path)
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) >= 2  # header + at least 1 event
            header = lines[0].strip()
            assert "time_step" in header
            assert "event_type" in header
            assert "migration_dist" in header
        finally:
            if os.path.exists(path):
                os.unlink(path)


# -- Data class tests -------------------------------------------------

class TestDataClasses:
    def test_cell_event_defaults(self):
        e = CellEvent()
        assert e.time_step == 0
        assert e.event_type == ""
        assert e.migration_dist == 0.0

    def test_transition_stats_defaults(self):
        t = TransitionStats()
        assert t.births == 0
        assert t.jaccard_index == 0.0

    def test_cell_trajectory_defaults(self):
        t = CellTrajectory()
        assert t.positions == []
        assert t.death_step is None
        assert t.lifespan == 0

    def test_temporal_result_defaults(self):
        r = TemporalResult()
        assert r.num_snapshots == 0
        assert r.events == []
        assert r.overall_stability == 0.0


class TestDuplicateCoordinates:
    """Regression tests for issue #119 -- duplicate seed coordinates."""

    def test_get_cell_areas_with_duplicates(self):
        """_get_cell_areas should return one entry per point index,
        even when two points share the same coordinates."""
        points = [(100.0, 100.0), (300.0, 300.0), (100.0, 100.0)]
        areas = _get_cell_areas(points)
        # Must have entries for all 3 indices
        assert 0 in areas
        assert 1 in areas
        assert 2 in areas
        assert len(areas) == 3

    def test_temporal_analysis_duplicate_seeds(self):
        """temporal_analysis should not crash or lose data when
        snapshots contain co-located seeds."""
        snap1 = [(100, 100), (300, 300), (100, 100)]
        snap2 = [(110, 110), (300, 300), (110, 110)]
        result = temporal_analysis([snap1, snap2], match_radius=50.0)
        # Should have events for all 3 cells, no crashes
        assert result.num_snapshots == 2
        assert len(result.events) == 3  # one event per cell
