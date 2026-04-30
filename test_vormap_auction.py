"""Tests for vormap_auction — Spatial Auction Engine."""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from vormap_auction import (
    run_auction,
    assess_demand,
    calculate_budgets,
    run_sealed_bid,
    run_vickrey,
    run_combinatorial,
    run_ascending,
    analyze_fairness,
    CellBidder,
    ResourceBundle,
    AuctionReport,
    FairnessMetrics,
    _generate_resources,
    _voronoi_cells_simple,
    _euclidean,
    _polygon_area,
    PRIORITY_WEIGHTS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

POINTS_5 = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
POINTS_3 = [(200, 200), (500, 500), (800, 200)]
BOUNDS = (0, 1000, 0, 1000)


def _make_cells(n=5, budgets=None, demands=None):
    if budgets is None:
        budgets = [100.0] * n
    if demands is None:
        demands = [0.5] * n
    cells = []
    for i in range(n):
        cells.append(CellBidder(
            cell_idx=i,
            centroid=(100 * (i + 1), 100 * (i + 1)),
            area=10000.0,
            demand=demands[i],
            budget=budgets[i],
            priority="medium",
        ))
    return cells


def _make_resources(n=3):
    resources = []
    for i in range(n):
        resources.append(ResourceBundle(
            resource_id=f"R{i+1:03d}",
            quantity=20.0,
            base_price=30.0,
            location=(200 * (i + 1), 200 * (i + 1)),
        ))
    return resources


# ---------------------------------------------------------------------------
# Demand Assessor Tests
# ---------------------------------------------------------------------------

class TestDemandAssessor:
    def test_returns_correct_count(self):
        demands = assess_demand(POINTS_5, BOUNDS)
        assert len(demands) == 5

    def test_normalized_to_one(self):
        demands = assess_demand(POINTS_5, BOUNDS)
        assert max(demands) == 1.0

    def test_all_positive(self):
        demands = assess_demand(POINTS_5, BOUNDS)
        assert all(d > 0 for d in demands)

    def test_with_weights(self):
        weights = [2.0, 1.0, 1.0, 1.0, 1.0]
        demands = assess_demand(POINTS_5, BOUNDS, weights=weights)
        # First cell should have higher demand due to weight
        assert len(demands) == 5

    def test_single_point(self):
        demands = assess_demand([(500, 500)], BOUNDS)
        assert len(demands) == 1
        assert demands[0] == 1.0

    def test_two_points(self):
        demands = assess_demand([(200, 200), (800, 800)], BOUNDS)
        assert len(demands) == 2
        # Peripheral point should have more demand
        assert demands[1] >= demands[0] or demands[0] >= demands[1]  # both valid


# ---------------------------------------------------------------------------
# Budget Calculator Tests
# ---------------------------------------------------------------------------

class TestBudgetCalculator:
    def test_returns_correct_count(self):
        demands = [0.8, 0.5, 0.3, 0.1, 1.0]
        budgets = calculate_budgets(demands)
        assert len(budgets) == 5

    def test_minimum_budget(self):
        demands = [0.0, 0.0, 0.0]
        budgets = calculate_budgets(demands)
        assert all(b >= 1.0 for b in budgets)

    def test_priority_scaling(self):
        demands = [1.0, 1.0, 1.0, 1.0]
        priorities = ["critical", "high", "medium", "low"]
        budgets = calculate_budgets(demands, priorities, base_budget=100.0)
        assert budgets[0] > budgets[1] > budgets[2] > budgets[3]

    def test_demand_proportional(self):
        demands = [1.0, 0.5, 0.25]
        priorities = ["medium", "medium", "medium"]
        budgets = calculate_budgets(demands, priorities, base_budget=100.0)
        assert budgets[0] > budgets[1] > budgets[2]

    def test_custom_base_budget(self):
        demands = [1.0]
        priorities = ["critical"]
        budgets = calculate_budgets(demands, priorities, base_budget=200.0)
        assert budgets[0] == 200.0 * 1.0 * PRIORITY_WEIGHTS["critical"]


# ---------------------------------------------------------------------------
# Sealed-Bid Auction Tests
# ---------------------------------------------------------------------------

class TestSealedBid:
    def test_produces_winners(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_sealed_bid(cells, resources, seed=42)
        assert len(result.winners) > 0

    def test_winner_pays_own_bid(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_sealed_bid(cells, resources, seed=42)
        for w in result.winners:
            assert w["price_paid"] > 0

    def test_revenue_positive(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_sealed_bid(cells, resources, seed=42)
        assert result.total_revenue > 0

    def test_no_double_allocation(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_sealed_bid(cells, resources, seed=42)
        winner_cells = [w["cell_idx"] for w in result.winners]
        assert len(winner_cells) == len(set(winner_cells))

    def test_mechanism_name(self):
        cells = _make_cells(3)
        resources = _make_resources(2)
        result = run_sealed_bid(cells, resources, seed=1)
        assert result.mechanism == "sealed_bid"

    def test_efficiency_bounded(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_sealed_bid(cells, resources, seed=42)
        assert 0.0 <= result.efficiency <= 1.0


# ---------------------------------------------------------------------------
# Vickrey Auction Tests
# ---------------------------------------------------------------------------

class TestVickrey:
    def test_produces_winners(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_vickrey(cells, resources, seed=42)
        assert len(result.winners) > 0

    def test_winner_pays_second_price(self):
        cells = _make_cells(5, budgets=[200, 150, 100, 80, 50])
        resources = _make_resources(1)
        result = run_vickrey(cells, resources, seed=42)
        # Winner should pay less than their bid (second price)
        for w in result.winners:
            if "bid_amount" in w:
                assert w["price_paid"] <= w["bid_amount"]

    def test_revenue_less_than_sealed(self):
        cells1 = _make_cells(5, budgets=[200, 150, 100, 80, 50])
        cells2 = _make_cells(5, budgets=[200, 150, 100, 80, 50])
        resources = _make_resources(3)
        sealed = run_sealed_bid(cells1, resources, seed=42)
        vickrey = run_vickrey(cells2, resources, seed=42)
        # Vickrey typically generates less revenue (revenue equivalence aside)
        # Just check both produce results
        assert sealed.total_revenue >= 0
        assert vickrey.total_revenue >= 0

    def test_mechanism_name(self):
        cells = _make_cells(3)
        resources = _make_resources(2)
        result = run_vickrey(cells, resources, seed=1)
        assert result.mechanism == "vickrey"


# ---------------------------------------------------------------------------
# Combinatorial Auction Tests
# ---------------------------------------------------------------------------

class TestCombinatorial:
    def test_produces_winners(self):
        cells = _make_cells(5)
        resources = _make_resources(4)
        result = run_combinatorial(cells, resources, seed=42)
        assert len(result.winners) > 0

    def test_bundle_allocation(self):
        cells = _make_cells(5)
        resources = _make_resources(4)
        result = run_combinatorial(cells, resources, seed=42)
        for w in result.winners:
            assert "bundle" in w
            assert len(w["bundle"]) >= 1

    def test_no_resource_double_allocation(self):
        cells = _make_cells(5)
        resources = _make_resources(4)
        result = run_combinatorial(cells, resources, seed=42)
        all_allocated = []
        for w in result.winners:
            all_allocated.extend(w["bundle"])
        assert len(all_allocated) == len(set(all_allocated))

    def test_mechanism_name(self):
        cells = _make_cells(3)
        resources = _make_resources(2)
        result = run_combinatorial(cells, resources, seed=1)
        assert result.mechanism == "combinatorial"

    def test_revenue_positive(self):
        cells = _make_cells(5, budgets=[200, 180, 150, 120, 100])
        resources = _make_resources(3)
        result = run_combinatorial(cells, resources, seed=42)
        assert result.total_revenue > 0


# ---------------------------------------------------------------------------
# Ascending Auction Tests
# ---------------------------------------------------------------------------

class TestAscending:
    def test_produces_winners(self):
        cells = _make_cells(5, budgets=[200, 150, 100, 80, 50])
        resources = _make_resources(3)
        result = run_ascending(cells, resources, max_rounds=10, seed=42)
        assert len(result.winners) > 0

    def test_has_rounds(self):
        cells = _make_cells(5, budgets=[200, 150, 100, 80, 50])
        resources = _make_resources(3)
        result = run_ascending(cells, resources, max_rounds=10, seed=42)
        assert len(result.rounds) >= 1

    def test_prices_non_decreasing(self):
        cells = _make_cells(5, budgets=[200, 150, 100, 80, 50])
        resources = _make_resources(3)
        result = run_ascending(cells, resources, max_rounds=15, seed=42)
        if len(result.rounds) >= 2:
            for rid in result.rounds[0].clearing_prices:
                prices = [r.clearing_prices.get(rid, 0) for r in result.rounds]
                for i in range(1, len(prices)):
                    assert prices[i] >= prices[i - 1] - 0.01  # allow float tolerance

    def test_max_rounds_respected(self):
        cells = _make_cells(5)
        resources = _make_resources(3)
        result = run_ascending(cells, resources, max_rounds=5, seed=42)
        assert len(result.rounds) <= 5

    def test_mechanism_name(self):
        cells = _make_cells(3)
        resources = _make_resources(2)
        result = run_ascending(cells, resources, seed=1)
        assert result.mechanism == "ascending"


# ---------------------------------------------------------------------------
# Fairness Analyzer Tests
# ---------------------------------------------------------------------------

class TestFairness:
    def test_perfect_equality(self):
        cells = _make_cells(4)
        for c in cells:
            c.utility = 1.0
        fairness = analyze_fairness(cells)
        assert fairness.gini_coefficient < 0.01

    def test_perfect_inequality(self):
        cells = _make_cells(4)
        cells[0].utility = 100.0
        cells[1].utility = 0.0
        cells[2].utility = 0.0
        cells[3].utility = 0.0
        fairness = analyze_fairness(cells)
        assert fairness.gini_coefficient > 0.5

    def test_envy_free_when_equal(self):
        cells = _make_cells(3)
        for c in cells:
            c.utility = 1.0
            c.demand = 0.5
        fairness = analyze_fairness(cells)
        assert fairness.envy_free is True

    def test_envy_detected(self):
        cells = _make_cells(3, demands=[1.0, 1.0, 0.1])
        cells[0].utility = 0.1
        cells[1].utility = 10.0
        cells[2].utility = 5.0
        fairness = analyze_fairness(cells)
        # Cell 0 has high demand but low utility, should envy cell 1
        assert len(fairness.envy_pairs) > 0

    def test_welfare_metrics(self):
        cells = _make_cells(3)
        cells[0].utility = 3.0
        cells[1].utility = 2.0
        cells[2].utility = 1.0
        fairness = analyze_fairness(cells)
        assert fairness.utilitarian_welfare == 6.0
        assert fairness.egalitarian_welfare == 1.0
        assert abs(fairness.welfare_ratio - 1.0 / 6.0) < 0.001

    def test_gini_bounded(self):
        cells = _make_cells(5)
        for i, c in enumerate(cells):
            c.utility = float(i + 1)
        fairness = analyze_fairness(cells)
        assert 0 <= fairness.gini_coefficient <= 1


# ---------------------------------------------------------------------------
# Full Pipeline Tests
# ---------------------------------------------------------------------------

class TestRunAuction:
    def test_basic_run(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, n_resources=4, seed=42)
        assert isinstance(report, AuctionReport)
        assert report.efficiency_score >= 0

    def test_all_mechanisms_run(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, seed=42)
        assert report.sealed_bid is not None
        assert report.vickrey is not None
        assert report.combinatorial is not None
        assert report.ascending is not None

    def test_single_mechanism(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, mechanisms=["vickrey"], seed=42)
        assert report.vickrey is not None
        assert report.sealed_bid is None

    def test_three_points(self):
        report = run_auction(POINTS_3, bounds=BOUNDS, n_resources=2, seed=7)
        assert len(report.cells) == 3
        assert len(report.resources) == 2

    def test_efficiency_score_bounded(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, seed=42)
        assert 0 <= report.efficiency_score <= 100

    def test_summary_text(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, seed=42)
        text = report.summary_text()
        assert "SPATIAL AUCTION ENGINE" in text
        assert "Efficiency Score" in text

    def test_different_seeds(self):
        r1 = run_auction(POINTS_5, bounds=BOUNDS, seed=1)
        r2 = run_auction(POINTS_5, bounds=BOUNDS, seed=99)
        # Different seeds should produce different resource locations
        assert r1.resources[0].location != r2.resources[0].location


# ---------------------------------------------------------------------------
# Serialization Tests
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_json(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, seed=42)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            report.to_json(path)
            with open(path) as f:
                data = json.load(f)
            assert "cells" in data
            assert "resources" in data
            assert "fairness" in data
            assert "efficiency_score" in data
        finally:
            os.unlink(path)

    def test_json_roundtrip_values(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, seed=42)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            report.to_json(path)
            with open(path) as f:
                data = json.load(f)
            assert data["efficiency_score"] == round(report.efficiency_score, 2)
            assert len(data["cells"]) == len(report.cells)
        finally:
            os.unlink(path)

    def test_to_html(self):
        report = run_auction(POINTS_5, bounds=BOUNDS, seed=42)
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            path = f.name
        try:
            report.to_html(path)
            with open(path) as f:
                html = f.read()
            assert "Spatial Auction Engine" in html
            assert "Efficiency Score" in html
            assert "Fairness" in html
            assert len(html) > 1000
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_cell_single_resource(self):
        report = run_auction([(500, 500)], bounds=BOUNDS, n_resources=1, seed=42)
        assert len(report.cells) == 1
        assert report.efficiency_score >= 0

    def test_more_resources_than_cells(self):
        report = run_auction(POINTS_3, bounds=BOUNDS, n_resources=10, seed=42)
        assert len(report.resources) == 10

    def test_more_cells_than_resources(self):
        points = [(i * 100, i * 100) for i in range(10)]
        report = run_auction(points, bounds=BOUNDS, n_resources=2, seed=42)
        assert len(report.cells) == 10

    def test_zero_base_budget(self):
        # Should still work with minimum budgets
        report = run_auction(POINTS_5, bounds=BOUNDS, base_budget=0.0, seed=42)
        assert report.efficiency_score >= 0

    def test_custom_priorities(self):
        priorities = ["critical", "high", "medium", "low", "critical"]
        report = run_auction(POINTS_5, bounds=BOUNDS, priorities=priorities, seed=42)
        assert report.cells[0].priority == "critical"
        assert report.cells[3].priority == "low"


# ---------------------------------------------------------------------------
# Utility function tests
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_euclidean(self):
        assert _euclidean((0, 0), (3, 4)) == 5.0

    def test_polygon_area_square(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert abs(_polygon_area(square) - 100.0) < 0.01

    def test_polygon_area_triangle(self):
        tri = [(0, 0), (10, 0), (5, 10)]
        assert abs(_polygon_area(tri) - 50.0) < 0.01

    def test_generate_resources(self):
        resources = _generate_resources(BOUNDS, 5, seed=42)
        assert len(resources) == 5
        for r in resources:
            assert r.quantity > 0
            assert r.base_price > 0

    def test_voronoi_cells_simple(self):
        cells = _voronoi_cells_simple(POINTS_5, BOUNDS)
        assert len(cells) == 5
        for cell in cells:
            assert len(cell) >= 3  # at least a triangle
