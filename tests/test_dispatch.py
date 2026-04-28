"""Tests for vormap_dispatch — Autonomous Spatial Dispatch Optimizer.

Covers: geometry helpers, demand assignment, metric computation,
overload detection, facility suggestion, auto-dispatch, and Gini
coefficient calculations.
"""

import math
import pytest

# ── Module import ───────────────────────────────────────────────────
from vormap_dispatch import (
    _cross,
    _gini,
    assign_demand,
    auto_dispatch,
    compute_metrics,
    convex_hull,
    detect_overloaded,
    polygon_area,
    suggest_new_facilities,
)


# ── Geometry helpers ────────────────────────────────────────────────

class TestCross:
    """Cross-product helper used by convex hull."""

    def test_positive_turn(self):
        assert _cross((0, 0), (1, 0), (0, 1)) > 0  # left turn

    def test_negative_turn(self):
        assert _cross((0, 0), (0, 1), (1, 0)) < 0  # right turn

    def test_collinear(self):
        assert _cross((0, 0), (1, 1), (2, 2)) == 0


class TestConvexHull:
    def test_empty(self):
        assert convex_hull([]) == []

    def test_single_point(self):
        assert convex_hull([(5, 5)]) == [(5, 5)]

    def test_two_points(self):
        hull = convex_hull([(0, 0), (1, 1)])
        assert len(hull) == 2

    def test_duplicates_ignored(self):
        hull = convex_hull([(0, 0), (0, 0), (1, 1)])
        assert len(hull) == 2

    def test_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
        hull = convex_hull(pts)
        # Interior point (5,5) should be excluded
        assert (5, 5) not in hull
        assert len(hull) == 4

    def test_triangle(self):
        pts = [(0, 0), (10, 0), (5, 10)]
        hull = convex_hull(pts)
        assert len(hull) == 3


class TestPolygonArea:
    def test_empty(self):
        assert polygon_area([]) == 0.0

    def test_two_points(self):
        assert polygon_area([(0, 0), (1, 1)]) == 0.0

    def test_unit_square(self):
        area = polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)])
        assert abs(area - 1.0) < 1e-9

    def test_rectangle(self):
        area = polygon_area([(0, 0), (4, 0), (4, 3), (0, 3)])
        assert abs(area - 12.0) < 1e-9

    def test_triangle(self):
        area = polygon_area([(0, 0), (6, 0), (3, 4)])
        assert abs(area - 12.0) < 1e-9

    def test_order_invariant(self):
        # Shoelace formula gives same absolute area for CW and CCW
        cw = polygon_area([(0, 0), (0, 1), (1, 1), (1, 0)])
        ccw = polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)])
        assert abs(cw - ccw) < 1e-9


# ── Gini coefficient ───────────────────────────────────────────────

class TestGini:
    def test_all_zero(self):
        assert _gini([0, 0, 0]) == 0.0

    def test_empty(self):
        assert _gini([]) == 0.0

    def test_perfect_equality(self):
        # All values equal → Gini = 0
        assert abs(_gini([10, 10, 10, 10])) < 1e-9

    def test_max_inequality(self):
        # One person has everything → Gini approaches 1
        g = _gini([0, 0, 0, 100])
        assert g > 0.7

    def test_moderate_inequality(self):
        g = _gini([1, 2, 3, 4, 5])
        assert 0.1 < g < 0.5

    def test_single_value(self):
        assert _gini([42]) == 0.0


# ── Demand assignment ───────────────────────────────────────────────

class TestAssignDemand:
    def test_basic_nearest(self):
        facilities = [(0, 0), (10, 10)]
        demands = [(1, 1), (9, 9), (5, 0), (5, 10)]
        assignments = assign_demand(facilities, demands)
        assert 0 in assignments[0]  # (1,1) → facility 0
        assert 1 in assignments[1]  # (9,9) → facility 1

    def test_single_facility(self):
        facilities = [(5, 5)]
        demands = [(0, 0), (10, 10), (3, 7)]
        assignments = assign_demand(facilities, demands)
        assert len(assignments[0]) == 3

    def test_all_demand_assigned(self):
        facilities = [(0, 0), (50, 50), (100, 100)]
        demands = [(i, i) for i in range(20)]
        assignments = assign_demand(facilities, demands)
        total = sum(len(v) for v in assignments.values())
        assert total == 20

    def test_equidistant_tie_breaking(self):
        # Equidistant demand should go to first facility encountered
        facilities = [(0, 0), (10, 0)]
        demands = [(5, 0)]
        assignments = assign_demand(facilities, demands)
        # Should be assigned to one of them (deterministic)
        total = sum(len(v) for v in assignments.values())
        assert total == 1


# ── Compute metrics ─────────────────────────────────────────────────

class TestComputeMetrics:
    def test_basic_metrics(self):
        facilities = [(0, 0), (10, 0)]
        demands = [(1, 0), (2, 0), (9, 0), (8, 0)]
        assignments = assign_demand(facilities, demands)
        metrics = compute_metrics(facilities, demands, assignments)
        assert "avg_distance" in metrics
        assert "max_distance" in metrics
        assert "p95_distance" in metrics
        assert "gini" in metrics
        assert "max_utilization" in metrics
        assert "loads" in metrics
        assert len(metrics["loads"]) == 2

    def test_balanced_load(self):
        facilities = [(0, 0), (100, 0)]
        demands = [(1, 0), (2, 0), (99, 0), (98, 0)]
        assignments = assign_demand(facilities, demands)
        metrics = compute_metrics(facilities, demands, assignments)
        # Balanced → low Gini, utilization near 1.0
        assert metrics["gini"] < 0.1
        assert 0.8 <= metrics["max_utilization"] <= 1.2

    def test_unbalanced_load(self):
        facilities = [(0, 0), (100, 0)]
        demands = [(1, 0), (2, 0), (3, 0), (4, 0), (99, 0)]  # 4 vs 1
        assignments = assign_demand(facilities, demands)
        weights = [1.0] * 5
        metrics = compute_metrics(facilities, demands, assignments, weights)
        # Facility 0 has more load than facility 1
        assert metrics["loads"][0] > metrics["loads"][1]

    def test_weighted_demands(self):
        facilities = [(0, 0), (10, 0)]
        demands = [(1, 0), (9, 0)]
        assignments = assign_demand(facilities, demands)
        # Give very different weights
        weights = [10.0, 1.0]
        metrics = compute_metrics(facilities, demands, assignments, weights)
        assert metrics["avg_load"] == pytest.approx(5.5, abs=0.1)

    def test_empty_facility(self):
        facilities = [(0, 0), (10, 0), (1000, 1000)]
        demands = [(1, 0), (9, 0)]
        assignments = assign_demand(facilities, demands)
        metrics = compute_metrics(facilities, demands, assignments)
        assert len(metrics["loads"]) == 3
        assert metrics["loads"][2] == 0  # distant facility gets nothing

    def test_single_demand(self):
        facilities = [(0, 0)]
        demands = [(5, 5)]
        assignments = assign_demand(facilities, demands)
        metrics = compute_metrics(facilities, demands, assignments)
        assert metrics["avg_distance"] == pytest.approx(math.hypot(5, 5), abs=0.1)


# ── Overload detection ──────────────────────────────────────────────

class TestDetectOverloaded:
    def test_no_overload(self):
        assignments = {0: [0, 1], 1: [2, 3]}
        weights = [1, 1, 1, 1]
        result = detect_overloaded(assignments, weights, capacity=5, num_facilities=2)
        assert result == []

    def test_one_overloaded(self):
        assignments = {0: [0, 1, 2, 3], 1: [4]}
        weights = [3, 3, 3, 3, 1]
        result = detect_overloaded(assignments, weights, capacity=10, num_facilities=2)
        assert 0 in result
        assert 1 not in result

    def test_all_overloaded(self):
        assignments = {0: [0, 1], 1: [2, 3]}
        weights = [10, 10, 10, 10]
        result = detect_overloaded(assignments, weights, capacity=5, num_facilities=2)
        assert len(result) == 2

    def test_empty_facility_not_overloaded(self):
        assignments = {0: [0]}
        weights = [1]
        result = detect_overloaded(assignments, weights, capacity=5, num_facilities=3)
        assert result == []


# ── Suggest new facilities ──────────────────────────────────────────

class TestSuggestNewFacilities:
    def test_suggests_for_overloaded(self):
        facilities = [(0, 0), (100, 0)]
        demands = [(1, 0), (2, 0), (30, 0), (40, 0), (99, 0)]
        weights = [5, 5, 5, 5, 1]
        assignments = assign_demand(facilities, demands)
        overloaded = [0]  # facility 0 is overloaded
        suggestions = suggest_new_facilities(
            facilities, demands, assignments, weights, overloaded, max_new=2
        )
        assert len(suggestions) >= 1
        # Suggestion should be somewhere in the facility-0 demand region
        assert 0 <= suggestions[0][0] <= 100
        assert suggestions[0][1] is not None

    def test_max_new_respected(self):
        facilities = [(0, 0)]
        demands = [(i, 0) for i in range(20)]
        weights = [1] * 20
        assignments = {0: list(range(20))}
        suggestions = suggest_new_facilities(
            facilities, demands, assignments, weights, [0], max_new=1
        )
        assert len(suggestions) == 1

    def test_empty_overloaded_cell(self):
        facilities = [(0, 0), (10, 0)]
        demands = [(1, 0)]
        weights = [1]
        assignments = assign_demand(facilities, demands)
        # Facility 1 has no demand but is in overloaded list
        suggestions = suggest_new_facilities(
            facilities, demands, assignments, weights, [1], max_new=5
        )
        assert len(suggestions) == 0

    def test_suggestion_is_centroid_of_far_half(self):
        # With points at (0,0), (2,0), (4,0), (6,0)
        # Far half of distance from facility at (0,0) = (4,0) and (6,0)
        # Centroid of far half = (5, 0)
        facilities = [(0, 0)]
        demands = [(0, 0), (2, 0), (4, 0), (6, 0)]
        weights = [1, 1, 1, 1]
        assignments = {0: [0, 1, 2, 3]}
        suggestions = suggest_new_facilities(
            facilities, demands, assignments, weights, [0], max_new=1
        )
        assert len(suggestions) == 1
        assert abs(suggestions[0][0] - 5.0) < 0.01
        assert abs(suggestions[0][1] - 0.0) < 0.01


# ── Auto dispatch ───────────────────────────────────────────────────

class TestAutoDispatch:
    def test_already_balanced(self):
        facilities = [(0, 0), (10, 0)]
        demands = [(1, 0), (9, 0)]
        weights = [1.0, 1.0]
        capacity = 5.0
        facs, assignments, metrics, suggestions = auto_dispatch(
            facilities, demands, weights, capacity, target_util=2.0, max_new=5
        )
        assert len(suggestions) == 0
        assert len(facs) == 2

    def test_adds_facilities_when_overloaded(self):
        # One facility, many demands → should add facilities
        facilities = [(50, 50)]
        demands = [(i * 10, i * 10) for i in range(10)]
        weights = [10.0] * 10
        capacity = 20  # very low → overloaded
        facs, assignments, metrics, suggestions = auto_dispatch(
            facilities, demands, weights, capacity, target_util=0.85, max_new=5
        )
        assert len(facs) > 1
        assert len(suggestions) > 0

    def test_max_new_limits_additions(self):
        facilities = [(50, 50)]
        demands = [(i, i) for i in range(50)]
        weights = [5.0] * 50
        capacity = 10
        facs, _, _, suggestions = auto_dispatch(
            facilities, demands, weights, capacity, target_util=0.1, max_new=3
        )
        assert len(suggestions) <= 3

    def test_returns_valid_metrics(self):
        facilities = [(0, 0), (100, 100)]
        demands = [(10, 10), (90, 90), (50, 50)]
        weights = [1.0, 1.0, 1.0]
        capacity = 10
        facs, assignments, metrics, _ = auto_dispatch(
            facilities, demands, weights, capacity, target_util=0.85, max_new=5
        )
        assert "avg_distance" in metrics
        assert "gini" in metrics
        assert metrics["avg_distance"] >= 0
        assert metrics["gini"] >= 0


# ── Integration: full pipeline ──────────────────────────────────────

class TestDispatchIntegration:
    def test_full_pipeline(self):
        """End-to-end: assign → metrics → detect → suggest → auto-dispatch."""
        import random
        random.seed(123)
        facilities = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(5)]
        demands = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(100)]
        weights = [random.uniform(0.5, 3.0) for _ in range(100)]
        total_w = sum(weights)
        capacity = total_w / len(facilities) * 1.5

        # Step 1: assign
        assignments = assign_demand(facilities, demands, weights)
        assert sum(len(v) for v in assignments.values()) == 100

        # Step 2: metrics
        metrics = compute_metrics(facilities, demands, assignments, weights)
        assert metrics["avg_distance"] > 0
        assert len(metrics["loads"]) == 5

        # Step 3: overload detect
        overloaded = detect_overloaded(assignments, weights, capacity, len(facilities))
        assert isinstance(overloaded, list)

        # Step 4: auto dispatch
        facs, final_assign, final_metrics, suggestions = auto_dispatch(
            facilities, demands, weights, capacity, target_util=0.85, max_new=5
        )
        assert len(facs) >= 5
        # Final assignment covers all demands
        assert sum(len(v) for v in final_assign.values()) == 100

    def test_metrics_monotonically_improve_with_more_facilities(self):
        """Adding facilities should generally reduce max distance."""
        import random
        random.seed(456)
        facilities = [(50, 50)]
        demands = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(50)]
        weights = [1.0] * 50

        assignments = assign_demand(facilities, demands)
        m1 = compute_metrics(facilities, demands, assignments)

        # Add a second facility
        facilities2 = facilities + [(25, 25)]
        assignments2 = assign_demand(facilities2, demands)
        m2 = compute_metrics(facilities2, demands, assignments2)

        # Average distance should decrease with more facilities
        assert m2["avg_distance"] <= m1["avg_distance"]
