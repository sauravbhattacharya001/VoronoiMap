"""Tests for vormap_causality — Spatial Causality Engine."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_causality import (
    Intervention,
    CausalEffect,
    DIDResult,
    SyntheticControlResult,
    SpilloverCell,
    CausalityReport,
    analyze_causality,
    estimate_treatment_effect,
    difference_in_differences,
    synthetic_control,
    detect_spillovers,
    rank_interventions,
    ALL_METRICS,
    _compute_voronoi_cells,
    _apply_intervention,
    _auto_bounds,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

POINTS = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
BOUNDS = (0, 1000, 0, 1000)
ADD_IV = Intervention("add", points=[(500, 500), (300, 100)])
REMOVE_IV = Intervention("remove", points=[(400, 300)])
RELOCATE_IV = Intervention("relocate", points=[(100, 200)], targets=[(150, 250)])


# ---------------------------------------------------------------------------
# Intervention dataclass
# ---------------------------------------------------------------------------

class TestIntervention:
    def test_add_intervention(self):
        iv = Intervention("add", points=[(1, 2)])
        assert iv.type == "add"
        assert iv.label == "add_1pt"

    def test_remove_intervention(self):
        iv = Intervention("remove", points=[(1, 2), (3, 4)])
        assert iv.type == "remove"
        assert iv.label == "remove_2pts"

    def test_relocate_intervention(self):
        iv = Intervention("relocate", points=[(1, 2)], targets=[(3, 4)])
        assert iv.type == "relocate"

    def test_invalid_type(self):
        with pytest.raises(ValueError, match="Unknown intervention"):
            Intervention("destroy", points=[(1, 2)])

    def test_relocate_mismatched_targets(self):
        with pytest.raises(ValueError, match="relocate requires targets"):
            Intervention("relocate", points=[(1, 2), (3, 4)], targets=[(5, 6)])

    def test_relocate_no_targets(self):
        with pytest.raises(ValueError, match="relocate requires targets"):
            Intervention("relocate", points=[(1, 2)])

    def test_custom_label(self):
        iv = Intervention("add", points=[(1, 2)], label="my_add")
        assert iv.label == "my_add"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_auto_bounds(self):
        b = _auto_bounds([(0, 0), (100, 100)])
        south, north, west, east = b
        assert south < 0
        assert north > 100
        assert west < 0
        assert east > 100

    def test_auto_bounds_empty(self):
        b = _auto_bounds([])
        assert b == (0, 1000, 0, 1000)

    def test_auto_bounds_extra(self):
        b = _auto_bounds([(50, 50)], extra=[(200, 200)])
        assert b[1] > 200  # north > 200

    def test_apply_add(self):
        pts = [(1, 1), (2, 2)]
        result = _apply_intervention(pts, Intervention("add", [(3, 3)]))
        assert len(result) == 3
        assert (3, 3) in result

    def test_apply_remove(self):
        pts = [(1, 1), (2, 2), (3, 3)]
        result = _apply_intervention(pts, Intervention("remove", [(2, 2)]))
        assert len(result) == 2
        assert (2, 2) not in result

    def test_apply_relocate(self):
        pts = [(1, 1), (2, 2)]
        result = _apply_intervention(
            pts, Intervention("relocate", [(1, 1)], targets=[(5, 5)]))
        assert (5, 5) in result
        assert len(result) == 2

    def test_compute_voronoi_cells(self):
        cells = _compute_voronoi_cells(POINTS, BOUNDS)
        assert len(cells) == len(POINTS)
        for c in cells:
            assert "area" in c
            assert "compactness" in c
            assert "neighbors" in c
            assert "nn_distance" in c

    def test_compute_voronoi_empty(self):
        cells = _compute_voronoi_cells([], BOUNDS)
        assert cells == []

    def test_compute_voronoi_single(self):
        cells = _compute_voronoi_cells([(500, 500)], BOUNDS)
        assert len(cells) == 1
        assert cells[0]["area"] > 0


# ---------------------------------------------------------------------------
# Treatment effect estimation
# ---------------------------------------------------------------------------

class TestTreatmentEffect:
    def test_basic_add(self):
        e = estimate_treatment_effect(POINTS, ADD_IV, "area", BOUNDS)
        assert isinstance(e, CausalEffect)
        assert e.metric == "area"
        assert isinstance(e.ate, float)
        assert 0 <= e.confidence <= 1

    def test_basic_remove(self):
        e = estimate_treatment_effect(POINTS, REMOVE_IV, "area", BOUNDS)
        assert isinstance(e, CausalEffect)

    def test_basic_relocate(self):
        e = estimate_treatment_effect(POINTS, RELOCATE_IV, "area", BOUNDS)
        assert isinstance(e, CausalEffect)

    def test_all_metrics(self):
        for m in ALL_METRICS:
            e = estimate_treatment_effect(POINTS, ADD_IV, m, BOUNDS)
            assert e.metric == m

    def test_unknown_metric(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            estimate_treatment_effect(POINTS, ADD_IV, "bogus", BOUNDS)

    def test_add_increases_cell_count(self):
        # Adding points should change average area
        e = estimate_treatment_effect(POINTS, ADD_IV, "area", BOUNDS)
        # More points = smaller average area (ATE should be negative)
        assert e.ate < 0

    def test_remove_all_points(self):
        iv = Intervention("remove", points=list(POINTS))
        e = estimate_treatment_effect(POINTS, iv, "area", BOUNDS)
        assert isinstance(e, CausalEffect)

    def test_auto_bounds(self):
        e = estimate_treatment_effect(POINTS, ADD_IV, "area")
        assert isinstance(e, CausalEffect)

    def test_to_dict(self):
        e = estimate_treatment_effect(POINTS, ADD_IV, "area", BOUNDS)
        d = e.to_dict()
        assert "metric" in d
        assert "ate" in d
        assert "att" in d


# ---------------------------------------------------------------------------
# Difference-in-Differences
# ---------------------------------------------------------------------------

class TestDID:
    def test_basic(self):
        results = difference_in_differences(POINTS, ADD_IV, BOUNDS)
        assert len(results) == len(ALL_METRICS)
        for r in results:
            assert isinstance(r, DIDResult)

    def test_specific_metrics(self):
        results = difference_in_differences(POINTS, ADD_IV, BOUNDS,
                                            metrics=["area", "compactness"])
        assert len(results) == 2

    def test_parallel_trend_score(self):
        results = difference_in_differences(POINTS, ADD_IV, BOUNDS)
        for r in results:
            assert 0 <= r.parallel_trend_score <= 1

    def test_did_estimate_exists(self):
        results = difference_in_differences(POINTS, REMOVE_IV, BOUNDS)
        # DiD should produce a non-trivial estimate for at least one metric
        assert any(r.did_estimate != 0 for r in results)

    def test_to_dict(self):
        results = difference_in_differences(POINTS, ADD_IV, BOUNDS)
        d = results[0].to_dict()
        assert "did_estimate" in d
        assert "parallel_trend_score" in d

    def test_empty_after(self):
        iv = Intervention("remove", points=list(POINTS))
        results = difference_in_differences(POINTS, iv, BOUNDS)
        assert len(results) == len(ALL_METRICS)


# ---------------------------------------------------------------------------
# Synthetic Control
# ---------------------------------------------------------------------------

class TestSyntheticControl:
    def test_basic(self):
        results = synthetic_control(POINTS, ADD_IV, BOUNDS)
        assert len(results) == len(ALL_METRICS)
        for r in results:
            assert isinstance(r, SyntheticControlResult)

    def test_donor_weights(self):
        results = synthetic_control(POINTS, REMOVE_IV, BOUNDS)
        for r in results:
            if r.donor_weights:
                total = sum(r.donor_weights.values())
                assert abs(total - 1.0) < 0.01

    def test_fit_quality(self):
        results = synthetic_control(POINTS, ADD_IV, BOUNDS)
        for r in results:
            assert 0 <= r.fit_quality <= 1

    def test_to_dict(self):
        results = synthetic_control(POINTS, ADD_IV, BOUNDS)
        d = results[0].to_dict()
        assert "treatment_effect" in d
        assert "donor_weights" in d


# ---------------------------------------------------------------------------
# Spillover Detection
# ---------------------------------------------------------------------------

class TestSpillover:
    def test_basic(self):
        spills = detect_spillovers(POINTS, ADD_IV, hops=2, bounds=BOUNDS)
        assert isinstance(spills, list)
        for s in spills:
            assert isinstance(s, SpilloverCell)

    def test_hop_distances(self):
        spills = detect_spillovers(POINTS, ADD_IV, hops=2, bounds=BOUNDS)
        for s in spills:
            assert 1 <= s.hop_distance <= 2

    def test_intensity_normalized(self):
        spills = detect_spillovers(POINTS, ADD_IV, hops=2, bounds=BOUNDS)
        for s in spills:
            assert 0 <= s.intensity <= 1

    def test_single_hop(self):
        spills = detect_spillovers(POINTS, ADD_IV, hops=1, bounds=BOUNDS)
        for s in spills:
            assert s.hop_distance == 1

    def test_to_dict(self):
        spills = detect_spillovers(POINTS, ADD_IV, hops=2, bounds=BOUNDS)
        if spills:
            d = spills[0].to_dict()
            assert "cell_index" in d
            assert "intensity" in d

    def test_empty_after_remove_all(self):
        iv = Intervention("remove", points=list(POINTS))
        spills = detect_spillovers(POINTS, iv, bounds=BOUNDS)
        assert isinstance(spills, list)


# ---------------------------------------------------------------------------
# Intervention Ranking
# ---------------------------------------------------------------------------

class TestRanking:
    def test_basic(self):
        ranked = rank_interventions(POINTS, [ADD_IV, REMOVE_IV], bounds=BOUNDS)
        assert len(ranked) == 2
        assert ranked[0].rank == 1
        assert ranked[1].rank == 2

    def test_all_objectives(self):
        from vormap_causality import OBJECTIVES
        for obj in OBJECTIVES:
            ranked = rank_interventions(POINTS, [ADD_IV], objective=obj,
                                        bounds=BOUNDS)
            assert len(ranked) == 1

    def test_unknown_objective(self):
        with pytest.raises(ValueError, match="Unknown objective"):
            rank_interventions(POINTS, [ADD_IV], objective="nonsense")

    def test_to_dict(self):
        ranked = rank_interventions(POINTS, [ADD_IV], bounds=BOUNDS)
        d = ranked[0].to_dict()
        assert "score" in d
        assert "rank" in d


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

class TestAnalyzeCausality:
    def test_full_report(self):
        report = analyze_causality(POINTS, [ADD_IV, REMOVE_IV], bounds=BOUNDS)
        assert isinstance(report, CausalityReport)
        assert len(report.interventions) == 2
        assert len(report.effects) == 2
        assert len(report.did_results) == 2
        assert len(report.synthetic_control) == 2
        assert len(report.ranking) == 2

    def test_summary(self):
        report = analyze_causality(POINTS, [ADD_IV], bounds=BOUNDS)
        s = report.summary()
        assert "SPATIAL CAUSALITY REPORT" in s
        assert "add_2pts" in s

    def test_single_intervention(self):
        report = analyze_causality(POINTS, [RELOCATE_IV], bounds=BOUNDS)
        assert len(report.interventions) == 1


# ---------------------------------------------------------------------------
# Export formats
# ---------------------------------------------------------------------------

class TestExports:
    def test_to_json(self):
        report = analyze_causality(POINTS, [ADD_IV], bounds=BOUNDS)
        path = "_test_causality_out.json"
        try:
            report.to_json(path)
            with open(path) as fh:
                data = json.load(fh)
            assert "effects" in data
            assert "ranking" in data
            assert "interventions" in data
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_csv(self):
        report = analyze_causality(POINTS, [ADD_IV, REMOVE_IV], bounds=BOUNDS)
        path = "_test_causality_out.csv"
        try:
            report.to_csv(path)
            with open(path) as fh:
                content = fh.read()
            assert "intervention" in content
            assert "ate" in content
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_html(self):
        report = analyze_causality(POINTS, [ADD_IV, REMOVE_IV], bounds=BOUNDS)
        path = "_test_causality_out.html"
        try:
            report.to_html(path)
            with open(path) as fh:
                content = fh.read()
            assert "Spatial Causality" in content
            assert "Treatment Effects" in content
            assert "Spillover" in content
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_single_point(self):
        pts = [(500, 500)]
        iv = Intervention("add", points=[(200, 200)])
        report = analyze_causality(pts, [iv], bounds=BOUNDS)
        assert isinstance(report, CausalityReport)

    def test_two_points(self):
        pts = [(100, 100), (900, 900)]
        iv = Intervention("add", points=[(500, 500)])
        report = analyze_causality(pts, [iv], bounds=BOUNDS)
        assert len(report.effects) == 1

    def test_collinear_points(self):
        pts = [(100, 500), (300, 500), (500, 500), (700, 500)]
        iv = Intervention("add", points=[(400, 300)])
        report = analyze_causality(pts, [iv], bounds=BOUNDS)
        assert isinstance(report, CausalityReport)

    def test_empty_intervention_points(self):
        iv = Intervention("add", points=[])
        report = analyze_causality(POINTS, [iv], bounds=BOUNDS)
        assert isinstance(report, CausalityReport)

    def test_coincident_points(self):
        pts = [(500, 500), (500, 500), (100, 100)]
        iv = Intervention("remove", points=[(500, 500)])
        report = analyze_causality(pts, [iv], bounds=BOUNDS)
        assert isinstance(report, CausalityReport)

    def test_auto_bounds_mode(self):
        report = analyze_causality(POINTS, [ADD_IV])
        assert report.bounds is not None

    def test_custom_metrics(self):
        report = analyze_causality(POINTS, [ADD_IV], bounds=BOUNDS,
                                   metrics=["area", "neighbors"])
        assert len(report.effects[0]) == 2
