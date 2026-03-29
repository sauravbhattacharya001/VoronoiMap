"""Tests for vormap_access — Spatial Accessibility Analyzer."""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_access import (
    AccessScore,
    SupplyPoint,
    InequalityResult,
    proximity_scores,
    gravity_scores,
    two_step_fca,
    enhanced_two_step_fca,
    access_inequality,
    accessibility_report,
    export_json,
    export_csv,
    _euclidean,
    _gaussian_decay,
    _inverse_power_decay,
    _step_decay,
    _parse_supply,
    _parse_demand,
    _rank_and_classify,
    _supply_utilization,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def simple_demand():
    """4 demand points in a grid."""
    return [(0, 0), (100, 0), (0, 100), (100, 100)]


@pytest.fixture
def simple_supply():
    """2 supply points with capacity."""
    return [(50, 50, 10), (200, 200, 5)]


@pytest.fixture
def clustered_demand():
    """Demand clustered near origin."""
    return [(10, 10), (20, 20), (15, 25), (25, 15), (500, 500)]


@pytest.fixture
def uniform_supply():
    """3 equal-capacity supply points."""
    return [(0, 0, 1), (100, 0, 1), (50, 100, 1)]


# ── Distance & Decay Tests ───────────────────────────────────────────


class TestDistanceDecay:
    def test_euclidean_basic(self):
        assert _euclidean(0, 0, 3, 4) == 5.0

    def test_euclidean_same_point(self):
        assert _euclidean(10, 20, 10, 20) == 0.0

    def test_euclidean_negative(self):
        d = _euclidean(-3, -4, 0, 0)
        assert abs(d - 5.0) < 1e-9

    def test_gaussian_decay_zero_distance(self):
        assert _gaussian_decay(0, 100) == 1.0

    def test_gaussian_decay_positive(self):
        w = _gaussian_decay(50, 100)
        assert 0 < w < 1
        # At d=bandwidth, weight = exp(-0.5) ≈ 0.606
        w2 = _gaussian_decay(100, 100)
        assert abs(w2 - math.exp(-0.5)) < 1e-9

    def test_gaussian_decay_zero_bandwidth(self):
        assert _gaussian_decay(10, 0) == 0.0

    def test_gaussian_decay_monotonic(self):
        w1 = _gaussian_decay(10, 100)
        w2 = _gaussian_decay(50, 100)
        w3 = _gaussian_decay(100, 100)
        assert w1 > w2 > w3 > 0

    def test_inverse_power_basic(self):
        w = _inverse_power_decay(10, beta=1.0)
        assert abs(w - 0.1) < 1e-9

    def test_inverse_power_min_dist(self):
        w = _inverse_power_decay(0, beta=1.0, min_dist=5.0)
        assert abs(w - 0.2) < 1e-9

    def test_inverse_power_beta_2(self):
        w = _inverse_power_decay(10, beta=2.0)
        assert abs(w - 0.01) < 1e-9

    def test_step_decay_within_first_zone(self):
        # catchment=300, 3 zones: [0-100) → 1.0
        assert _step_decay(50, 300, 3) == 1.0

    def test_step_decay_second_zone(self):
        assert _step_decay(150, 300, 3) == 0.68

    def test_step_decay_third_zone(self):
        assert _step_decay(250, 300, 3) == 0.22

    def test_step_decay_outside_catchment(self):
        assert _step_decay(301, 300, 3) == 0.0


# ── Input Parsing Tests ──────────────────────────────────────────────


class TestInputParsing:
    def test_parse_supply_xy(self):
        result = _parse_supply([(10, 20), (30, 40)])
        assert len(result) == 2
        assert result[0].capacity == 1.0

    def test_parse_supply_xyc(self):
        result = _parse_supply([(10, 20, 5)])
        assert result[0].capacity == 5.0

    def test_parse_supply_objects(self):
        sp = SupplyPoint(1, 2, 3)
        result = _parse_supply([sp])
        assert result[0] is sp

    def test_parse_supply_invalid(self):
        with pytest.raises(ValueError):
            _parse_supply([(10,)])

    def test_parse_demand_basic(self):
        result = _parse_demand([(1, 2), (3, 4)])
        assert result == [(1.0, 2.0), (3.0, 4.0)]

    def test_parse_demand_invalid(self):
        with pytest.raises(ValueError):
            _parse_demand([(1,)])


# ── Proximity Scoring Tests ──────────────────────────────────────────


class TestProximityScores:
    def test_basic_scoring(self, simple_demand, simple_supply):
        scores = proximity_scores(simple_demand, simple_supply)
        assert len(scores) == 4
        for s in scores:
            assert s.score > 0
            assert s.rank > 0
            assert s.level in ("excellent", "good", "moderate", "poor", "critical")

    def test_closer_is_better(self):
        demand = [(50, 50), (500, 500)]
        supply = [(50, 55, 1)]  # very close to first demand
        scores = proximity_scores(demand, supply, k=1)
        # First demand point should score higher
        s_map = {(s.x, s.y): s for s in scores}
        assert s_map[(50, 50)].score > s_map[(500, 500)].score

    def test_k_parameter(self):
        demand = [(0, 0)]
        supply = [(10, 0, 1), (20, 0, 1), (30, 0, 1)]
        s1 = proximity_scores(demand, supply, k=1)
        s3 = proximity_scores(demand, supply, k=3)
        assert s3[0].score > s1[0].score  # more supply = higher access

    def test_inverse_decay(self):
        demand = [(0, 0)]
        supply = [(10, 0, 1)]
        scores = proximity_scores(demand, supply, k=1, decay="inverse", beta=1.0)
        assert len(scores) == 1
        assert scores[0].score > 0

    def test_empty_demand_raises(self):
        with pytest.raises(ValueError, match="No demand"):
            proximity_scores([], [(1, 1)])

    def test_empty_supply_raises(self):
        with pytest.raises(ValueError, match="No supply"):
            proximity_scores([(0, 0)], [])

    def test_nearest_distance(self):
        demand = [(0, 0)]
        supply = [(3, 4, 1)]
        scores = proximity_scores(demand, supply, k=1)
        assert abs(scores[0].nearest_supply_dist - 5.0) < 1e-9

    def test_auto_bandwidth(self):
        demand = [(0, 0), (100, 100)]
        supply = [(50, 50, 1)]
        scores = proximity_scores(demand, supply, k=1, decay="gaussian")
        assert all(s.score > 0 for s in scores)


# ── Gravity Model Tests ──────────────────────────────────────────────


class TestGravityScores:
    def test_basic_gravity(self, simple_demand, simple_supply):
        scores = gravity_scores(simple_demand, simple_supply)
        assert len(scores) == 4
        assert all(s.score > 0 for s in scores)

    def test_capacity_matters(self):
        demand = [(0, 0)]
        supply_low = [(10, 0, 1)]
        supply_high = [(10, 0, 10)]
        s_low = gravity_scores(demand, supply_low)
        s_high = gravity_scores(demand, supply_high)
        assert s_high[0].score > s_low[0].score

    def test_distance_matters(self):
        demand = [(0, 0)]
        supply_near = [(10, 0, 1)]
        supply_far = [(1000, 0, 1)]
        s_near = gravity_scores(demand, supply_near)
        s_far = gravity_scores(demand, supply_far)
        assert s_near[0].score > s_far[0].score

    def test_beta_effect(self):
        demand = [(0, 0)]
        supply = [(100, 0, 1)]
        s1 = gravity_scores(demand, supply, beta=1.0)
        s2 = gravity_scores(demand, supply, beta=2.0)
        # Higher beta = steeper distance decay = lower score for distant supply
        assert s1[0].score > s2[0].score

    def test_all_reachable(self, simple_demand, simple_supply):
        scores = gravity_scores(simple_demand, simple_supply)
        assert all(s.reachable_supply_count == 2 for s in scores)


# ── 2SFCA Tests ──────────────────────────────────────────────────────


class TestTwoStepFCA:
    def test_basic_2sfca(self, simple_demand, simple_supply):
        scores = two_step_fca(simple_demand, simple_supply, catchment=200)
        assert len(scores) == 4
        for s in scores:
            assert s.score >= 0

    def test_within_catchment_positive(self):
        demand = [(0, 0)]
        supply = [(10, 0, 10)]
        scores = two_step_fca(demand, supply, catchment=100)
        assert scores[0].score > 0

    def test_outside_catchment_zero(self):
        demand = [(0, 0)]
        supply = [(1000, 0, 10)]
        scores = two_step_fca(demand, supply, catchment=100)
        assert scores[0].score == 0

    def test_demand_weights(self):
        demand = [(0, 0), (50, 0)]
        supply = [(25, 0, 10)]
        # Equal weights
        s_eq = two_step_fca(demand, supply, catchment=200, demand_weights=[1, 1])
        # Heavier weight on first point
        s_wt = two_step_fca(demand, supply, catchment=200, demand_weights=[10, 1])
        # With more weighted demand, the ratio decreases for both
        assert s_wt[0].score < s_eq[0].score

    def test_wrong_weight_length_raises(self):
        with pytest.raises(ValueError, match="demand_weights length"):
            two_step_fca([(0, 0)], [(1, 1, 1)], catchment=100,
                         demand_weights=[1, 2, 3])

    def test_zero_catchment_raises(self):
        with pytest.raises(ValueError, match="Catchment must be positive"):
            two_step_fca([(0, 0)], [(1, 1, 1)], catchment=0)

    def test_negative_catchment_raises(self):
        with pytest.raises(ValueError, match="Catchment must be positive"):
            two_step_fca([(0, 0)], [(1, 1, 1)], catchment=-10)

    def test_multiple_supply(self):
        demand = [(50, 50)]
        supply = [(0, 0, 5), (100, 0, 5), (50, 100, 5)]
        scores = two_step_fca(demand, supply, catchment=200)
        assert scores[0].reachable_supply_count == 3

    def test_reachable_count(self):
        demand = [(0, 0)]
        supply = [(10, 0, 1), (1000, 0, 1)]
        scores = two_step_fca(demand, supply, catchment=100)
        assert scores[0].reachable_supply_count == 1


# ── Enhanced 2SFCA Tests ─────────────────────────────────────────────


class TestEnhancedTwoStepFCA:
    def test_basic_e2sfca(self, simple_demand, simple_supply):
        scores = enhanced_two_step_fca(simple_demand, simple_supply, catchment=200)
        assert len(scores) == 4

    def test_step_decay_mode(self):
        demand = [(0, 0), (100, 0)]
        supply = [(50, 0, 10)]
        scores = enhanced_two_step_fca(demand, supply, catchment=200,
                                       decay="step", zones=3)
        assert all(s.score >= 0 for s in scores)

    def test_gaussian_decay_mode(self):
        demand = [(0, 0), (100, 0)]
        supply = [(50, 0, 10)]
        scores = enhanced_two_step_fca(demand, supply, catchment=200,
                                       decay="gaussian")
        assert all(s.score >= 0 for s in scores)

    def test_closer_scores_higher_with_decay(self):
        demand = [(10, 0), (180, 0)]
        supply = [(0, 0, 10)]
        scores = enhanced_two_step_fca(demand, supply, catchment=200)
        s_map = {(s.x, s.y): s for s in scores}
        # Closer demand should have higher access
        assert s_map[(10, 0)].score > s_map[(180, 0)].score

    def test_outside_catchment_zero_e2sfca(self):
        demand = [(0, 0)]
        supply = [(1000, 0, 10)]
        scores = enhanced_two_step_fca(demand, supply, catchment=100)
        assert scores[0].score == 0

    def test_e2sfca_vs_2sfca_differ(self):
        # Use points at different distances from supply so decay matters
        demand = [(10, 0), (190, 0)]
        supply = [(0, 0, 10)]
        s_basic = two_step_fca(demand, supply, catchment=200)
        s_enh = enhanced_two_step_fca(demand, supply, catchment=200)
        # In 2SFCA both demand points get equal weight (binary catchment)
        # In E2SFCA the closer point gets more weight via decay
        basic_map = {(s.x, s.y): s.score for s in s_basic}
        enh_map = {(s.x, s.y): s.score for s in s_enh}
        # 2SFCA: both should be equal (both within catchment, equal weight)
        assert abs(basic_map[(10, 0)] - basic_map[(190, 0)]) < 1e-6
        # E2SFCA: closer point should score higher
        assert enh_map[(10, 0)] > enh_map[(190, 0)]


# ── Access Inequality Tests ──────────────────────────────────────────


class TestAccessInequality:
    def test_perfect_equality(self):
        result = access_inequality([10, 10, 10, 10, 10])
        assert result.gini == 0.0
        assert result.cv == 0.0

    def test_high_inequality(self):
        result = access_inequality([0, 0, 0, 0, 100])
        assert result.gini > 0.5

    def test_basic_stats(self):
        result = access_inequality([2, 4, 6, 8, 10])
        assert result.mean_score == 6.0
        assert result.median_score == 6.0
        assert result.min_score == 2.0
        assert result.max_score == 10.0

    def test_lorenz_curve(self):
        result = access_inequality([1, 2, 3, 4, 5])
        assert result.lorenz_x[0] == 0.0
        assert result.lorenz_x[-1] == 1.0
        assert result.lorenz_y[0] == 0.0
        assert result.lorenz_y[-1] == 1.0

    def test_quintile_shares(self):
        result = access_inequality([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        assert len(result.quintile_shares) == 5
        assert abs(sum(result.quintile_shares) - 1.0) < 0.05

    def test_single_score(self):
        result = access_inequality([42])
        assert result.gini == 0.0
        assert result.mean_score == 42.0

    def test_two_scores(self):
        result = access_inequality([0, 10])
        assert result.gini > 0
        assert result.mean_score == 5.0

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No scores"):
            access_inequality([])

    def test_all_zeros(self):
        result = access_inequality([0, 0, 0])
        assert result.gini == 0.0

    def test_cv_positive(self):
        result = access_inequality([1, 5, 10, 20])
        assert result.cv > 0


# ── Ranking & Classification Tests ────────────────────────────────────


class TestRankAndClassify:
    def test_ranking_order(self):
        scores = [AccessScore(x=0, y=0, score=10),
                  AccessScore(x=1, y=1, score=50),
                  AccessScore(x=2, y=2, score=30)]
        _rank_and_classify(scores)
        assert scores[0].rank == 1
        assert scores[0].score == 50
        assert scores[1].rank == 2
        assert scores[-1].rank == 3

    def test_all_levels_present(self):
        scores = [AccessScore(x=0, y=0, score=i) for i in range(10)]
        _rank_and_classify(scores)
        levels = {s.level for s in scores}
        assert "excellent" in levels
        assert "critical" in levels

    def test_level_distribution(self):
        scores = [AccessScore(x=0, y=0, score=i) for i in range(100)]
        _rank_and_classify(scores)
        level_counts = {}
        for s in scores:
            level_counts[s.level] = level_counts.get(s.level, 0) + 1
        # Each level should get ~20% of points
        for level in ["excellent", "good", "moderate", "poor", "critical"]:
            assert level_counts.get(level, 0) == 20


# ── Supply Utilization Tests ──────────────────────────────────────────


class TestSupplyUtilization:
    def test_basic_utilization(self):
        demand = [(0, 0), (10, 10), (20, 20)]
        supply = [SupplyPoint(15, 15, 2)]
        util = _supply_utilization(demand, supply, catchment=50)
        assert len(util) == 1
        assert util[0]["demand_in_catchment"] == 3

    def test_overloaded(self):
        demand = [(0, 0), (5, 5), (10, 10), (15, 15), (20, 20)]
        supply = [SupplyPoint(10, 10, 1)]
        util = _supply_utilization(demand, supply, catchment=100)
        assert util[0]["utilization_level"] == "overloaded"

    def test_low_utilization(self):
        demand = [(500, 500)]  # far from supply
        supply = [SupplyPoint(0, 0, 100)]
        util = _supply_utilization(demand, supply, catchment=10)
        assert util[0]["demand_in_catchment"] == 0
        assert util[0]["utilization_level"] == "low"


# ── Full Report Tests ─────────────────────────────────────────────────


class TestAccessibilityReport:
    def test_report_e2sfca(self, simple_demand, simple_supply):
        report = accessibility_report(simple_demand, simple_supply,
                                      catchment=200, method="e2sfca")
        assert report.method == "e2sfca"
        assert report.demand_count == 4
        assert report.supply_count == 2
        assert len(report.scores) == 4
        assert report.inequality is not None
        assert len(report.summary) > 0

    def test_report_proximity(self, simple_demand, simple_supply):
        report = accessibility_report(simple_demand, simple_supply,
                                      method="proximity")
        assert report.method == "proximity"

    def test_report_gravity(self, simple_demand, simple_supply):
        report = accessibility_report(simple_demand, simple_supply,
                                      method="gravity")
        assert report.method == "gravity"

    def test_report_2sfca(self, simple_demand, simple_supply):
        report = accessibility_report(simple_demand, simple_supply,
                                      catchment=200, method="2sfca")
        assert report.method == "2sfca"

    def test_unknown_method_raises(self, simple_demand, simple_supply):
        with pytest.raises(ValueError, match="Unknown method"):
            accessibility_report(simple_demand, simple_supply, method="unknown")

    def test_underserved_and_hotspots(self):
        demand = [(i * 10, 0) for i in range(10)]
        supply = [(0, 0, 10)]
        report = accessibility_report(demand, supply, catchment=200,
                                      method="gravity")
        assert len(report.underserved) > 0
        assert len(report.hotspots) > 0

    def test_summary_contains_method(self, simple_demand, simple_supply):
        report = accessibility_report(simple_demand, simple_supply,
                                      catchment=200, method="2sfca")
        assert "2SFCA" in report.summary

    def test_supply_utilization_in_report(self, simple_demand, simple_supply):
        report = accessibility_report(simple_demand, simple_supply,
                                      catchment=200)
        assert len(report.supply_utilization) == 2


# ── Export Tests ──────────────────────────────────────────────────────


class TestExport:
    def test_export_json(self, simple_demand, simple_supply, tmp_path):
        report = accessibility_report(simple_demand, simple_supply,
                                      catchment=200, method="gravity")
        path = str(tmp_path / "test.json")
        export_json(report, path)
        with open(path) as f:
            data = json.load(f)
        assert data["method"] == "gravity"
        assert len(data["scores"]) == 4
        assert "gini" in data["inequality"]

    def test_export_csv(self, simple_demand, simple_supply, tmp_path):
        report = accessibility_report(simple_demand, simple_supply,
                                      catchment=200, method="proximity")
        path = str(tmp_path / "test.csv")
        export_csv(report, path)
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 5  # header + 4 data rows
        assert "score" in lines[0]


# ── Edge Cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_demand_single_supply(self):
        scores = gravity_scores([(0, 0)], [(10, 0, 1)])
        assert len(scores) == 1
        assert scores[0].score > 0

    def test_coincident_demand_supply(self):
        """Supply and demand at same location."""
        scores = proximity_scores([(50, 50)], [(50, 50, 10)], k=1)
        assert scores[0].score > 0  # Should handle zero distance

    def test_many_demand_few_supply(self):
        demand = [(i * 10, j * 10) for i in range(10) for j in range(10)]
        supply = [(50, 50, 100)]
        scores = two_step_fca(demand, supply, catchment=200)
        assert len(scores) == 100

    def test_many_supply_few_demand(self):
        demand = [(50, 50)]
        supply = [(i * 20, 0, 1) for i in range(20)]
        scores = gravity_scores(demand, supply)
        assert scores[0].reachable_supply_count == 20

    def test_all_supply_unreachable(self):
        demand = [(0, 0)]
        supply = [(10000, 10000, 10)]
        scores = two_step_fca(demand, supply, catchment=10)
        assert scores[0].score == 0
        assert scores[0].reachable_supply_count == 0

    def test_large_capacity_difference(self):
        demand = [(0, 0)]
        supply = [(10, 0, 1), (20, 0, 1000)]
        scores = gravity_scores(demand, supply)
        # Should handle large capacity differences without overflow
        assert scores[0].score > 0

    def test_negative_coordinates(self):
        demand = [(-100, -200)]
        supply = [(-90, -190, 5)]
        scores = proximity_scores(demand, supply)
        assert scores[0].score > 0

    def test_float_precision(self):
        demand = [(0.001, 0.002)]
        supply = [(0.003, 0.004, 1.0)]
        scores = gravity_scores(demand, supply)
        assert scores[0].score > 0
