"""Tests for vormap_compare — Voronoi Diagram Comparator."""

import json
import math
import os
import tempfile

import pytest

from vormap import eudist_pts
from vormap_compare import (
    AreaComparison,
    ComparisonResult,
    DiagramSnapshot,
    SeedMapping,
    TopologyDiff,
    _compute_similarity_score,
    _get_verdict,
    compare_areas,
    compare_diagrams,
    export_comparison_json,
    match_seeds,
)


# ── helpers ──────────────────────────────────────────────────────────


def _make_snapshot(seeds, regions=None, region_stats=None, bounds=None):
    """Build a DiagramSnapshot manually for testing."""
    return DiagramSnapshot(
        seeds=list(seeds),
        regions=regions or {},
        region_stats=region_stats or [],
        bounds=bounds,
    )


def _make_stats(areas):
    """Build minimal region_stats list from a list of areas."""
    return [{"area": a, "region_index": i + 1} for i, a in enumerate(areas)]


# ═══════════════════════════════════════════════════════════════════
# TestEuclideanDistance
# ═══════════════════════════════════════════════════════════════════


class TestEuclideanDistance:
    def test_zero_distance_same_point(self):
        assert eudist_pts((1, 2), (1, 2)) == 0.0

    def test_known_distance_345(self):
        assert eudist_pts((0, 0), (3, 4)) == pytest.approx(5.0)

    def test_symmetry(self):
        d1 = eudist_pts((1, 2), (4, 6))
        d2 = eudist_pts((4, 6), (1, 2))
        assert d1 == pytest.approx(d2)

    def test_negative_coordinates(self):
        assert eudist_pts((-1, -1), (2, 3)) == pytest.approx(5.0)

    def test_float_coordinates(self):
        d = eudist_pts((0.0, 0.0), (1.0, 1.0))
        assert d == pytest.approx(math.sqrt(2))


# ═══════════════════════════════════════════════════════════════════
# TestSeedMapping
# ═══════════════════════════════════════════════════════════════════


class TestSeedMapping:
    def test_empty_inputs(self):
        m = match_seeds([], [])
        assert m.pairs == []
        assert m.unmatched_a == []
        assert m.unmatched_b == []
        assert m.mean_displacement == 0.0

    def test_empty_a(self):
        m = match_seeds([], [(1, 2), (3, 4)])
        assert m.pairs == []
        assert m.unmatched_b == [0, 1]

    def test_empty_b(self):
        m = match_seeds([(1, 2)], [])
        assert m.pairs == []
        assert m.unmatched_a == [0]

    def test_single_seed_exact_match(self):
        m = match_seeds([(5, 5)], [(5, 5)])
        assert len(m.pairs) == 1
        assert m.pairs[0][2] == 0.0
        assert m.mean_displacement == 0.0
        assert m.max_displacement == 0.0

    def test_two_seeds_matched_correctly(self):
        a = [(0, 0), (10, 10)]
        b = [(0.1, 0.1), (10.1, 10.1)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 2
        # pair 0 matched to 0, pair 1 matched to 1
        assert m.pairs[0][0] == 0 and m.pairs[0][1] == 0
        assert m.pairs[1][0] == 1 and m.pairs[1][1] == 1

    def test_greedy_nearest_neighbor_ordering(self):
        # B has seeds swapped — greedy should match nearest
        a = [(0, 0), (10, 0)]
        b = [(10.5, 0), (0.5, 0)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 2
        # a[0]→b[1] (0.5 away), a[1]→b[0] (0.5 away)
        assert m.pairs[0] == (0, 1, pytest.approx(0.5))
        assert m.pairs[1] == (1, 0, pytest.approx(0.5))

    def test_max_distance_filters_far_seeds(self):
        a = [(0, 0)]
        b = [(100, 100)]
        m = match_seeds(a, b, max_distance=1.0)
        assert len(m.pairs) == 0
        assert m.unmatched_a == [0]
        assert m.unmatched_b == [0]

    def test_max_distance_allows_close_seeds(self):
        a = [(0, 0)]
        b = [(0.5, 0)]
        m = match_seeds(a, b, max_distance=1.0)
        assert len(m.pairs) == 1

    def test_unmatched_a_when_b_has_fewer(self):
        a = [(0, 0), (1, 1), (2, 2)]
        b = [(0, 0)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 1
        assert len(m.unmatched_a) == 2

    def test_unmatched_b_when_a_has_fewer(self):
        a = [(0, 0)]
        b = [(0, 0), (5, 5), (10, 10)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 1
        assert len(m.unmatched_b) == 2

    def test_mean_max_displacement(self):
        a = [(0, 0), (10, 0)]
        b = [(1, 0), (13, 0)]
        m = match_seeds(a, b)
        assert m.mean_displacement == pytest.approx(2.0)
        assert m.max_displacement == pytest.approx(3.0)

    def test_identical_seeds_zero_displacement(self):
        seeds = [(1, 2), (3, 4), (5, 6)]
        m = match_seeds(seeds, seeds)
        assert m.mean_displacement == 0.0
        assert m.max_displacement == 0.0
        assert len(m.pairs) == 3

    def test_disjoint_with_max_distance(self):
        a = [(0, 0), (1, 0)]
        b = [(100, 100), (200, 200)]
        m = match_seeds(a, b, max_distance=5.0)
        assert len(m.pairs) == 0
        assert m.unmatched_a == [0, 1]
        assert m.unmatched_b == [0, 1]


# ═══════════════════════════════════════════════════════════════════
# TestAreaComparison
# ═══════════════════════════════════════════════════════════════════


class TestAreaComparison:
    def test_no_matched_pairs_totals_only(self):
        stats_a = _make_stats([10, 20])
        stats_b = _make_stats([30])
        mapping = SeedMapping()
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.total_area_a == 30
        assert ac.total_area_b == 30
        assert ac.changes == []

    def test_identical_areas_zero_change(self):
        stats_a = _make_stats([10, 20])
        stats_b = _make_stats([10, 20])
        mapping = SeedMapping(pairs=[(0, 0, 0.0), (1, 1, 0.0)])
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.mean_abs_change == 0.0
        assert ac.mean_pct_change == 0.0

    def test_area_increase_detected(self):
        stats_a = _make_stats([10])
        stats_b = _make_stats([15])
        mapping = SeedMapping(pairs=[(0, 0, 0.0)])
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.changes[0][4] == 5  # abs_change
        assert ac.changes[0][5] == pytest.approx(50.0)  # pct_change

    def test_area_decrease_detected(self):
        stats_a = _make_stats([20])
        stats_b = _make_stats([10])
        mapping = SeedMapping(pairs=[(0, 0, 0.0)])
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.changes[0][4] == 10

    def test_percentage_change_correct(self):
        stats_a = _make_stats([100])
        stats_b = _make_stats([125])
        mapping = SeedMapping(pairs=[(0, 0, 0.0)])
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.mean_pct_change == pytest.approx(25.0)

    def test_max_change_identified(self):
        stats_a = _make_stats([10, 20, 30])
        stats_b = _make_stats([10, 40, 30])
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0), (1, 1, 0.0), (2, 2, 0.0)]
        )
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.max_abs_change == 20
        assert ac.max_pct_change == pytest.approx(100.0)

    def test_total_areas_summed(self):
        stats_a = _make_stats([10, 20, 30])
        stats_b = _make_stats([15, 25, 35])
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0), (1, 1, 0.0), (2, 2, 0.0)]
        )
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.total_area_a == 60
        assert ac.total_area_b == 75

    def test_empty_stats(self):
        mapping = SeedMapping()
        ac = compare_areas([], [], mapping)
        assert ac.total_area_a == 0.0
        assert ac.total_area_b == 0.0

    def test_zero_area_no_division_error(self):
        stats_a = _make_stats([0])
        stats_b = _make_stats([5])
        mapping = SeedMapping(pairs=[(0, 0, 0.0)])
        ac = compare_areas(stats_a, stats_b, mapping)
        assert ac.mean_pct_change == 0.0  # can't compute % of 0


# ═══════════════════════════════════════════════════════════════════
# TestGetVerdict
# ═══════════════════════════════════════════════════════════════════


class TestGetVerdict:
    def test_nearly_identical(self):
        assert _get_verdict(0.95) == "Nearly Identical"
        assert _get_verdict(1.0) == "Nearly Identical"

    def test_very_similar(self):
        assert _get_verdict(0.80) == "Very Similar"
        assert _get_verdict(0.94) == "Very Similar"

    def test_moderately_similar(self):
        assert _get_verdict(0.60) == "Moderately Similar"
        assert _get_verdict(0.79) == "Moderately Similar"

    def test_somewhat_different(self):
        assert _get_verdict(0.40) == "Somewhat Different"
        assert _get_verdict(0.59) == "Somewhat Different"

    def test_very_different(self):
        assert _get_verdict(0.20) == "Very Different"
        assert _get_verdict(0.39) == "Very Different"

    def test_completely_different(self):
        assert _get_verdict(0.0) == "Completely Different"
        assert _get_verdict(0.19) == "Completely Different"


# ═══════════════════════════════════════════════════════════════════
# TestComputeSimilarityScore
# ═══════════════════════════════════════════════════════════════════


class TestComputeSimilarityScore:
    def test_perfect_scores(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 10, 0, 10)
        )
        assert score == pytest.approx(1.0)

    def test_zero_displacement_high_disp_score(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 10, 0, 10)
        )
        # disp_score should be 1.0
        assert score >= 0.9

    def test_high_area_change_low_score(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=200.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 10, 0, 10)
        )
        # area_score should be 0.0
        assert score < 1.0

    def test_seed_count_mismatch_reduced(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score_same = _compute_similarity_score(
            mapping, area, topo, 5, 5, (0, 10, 0, 10)
        )
        score_diff = _compute_similarity_score(
            mapping, area, topo, 5, 10, (0, 10, 0, 10)
        )
        assert score_diff < score_same

    def test_bounds_none_handled(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, None
        )
        # bounds None, pairs exist → disp_score = 1.0
        assert score == pytest.approx(1.0)

    def test_no_pairs_disp_score_zero(self):
        mapping = SeedMapping()
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 10, 0, 10)
        )
        # disp_score = 0.0 when no pairs
        assert score < 1.0

    def test_score_clamped_0_1(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 10, 0, 10)
        )
        assert 0.0 <= score <= 1.0


# ═══════════════════════════════════════════════════════════════════
# TestComparisonResult
# ═══════════════════════════════════════════════════════════════════


class TestComparisonResult:
    def test_summary_returns_string(self):
        r = ComparisonResult(
            similarity_score=0.85, verdict="Very Similar"
        )
        s = r.summary()
        assert isinstance(s, str)
        assert "Similarity Score" in s
        assert "Very Similar" in s

    def test_summary_contains_key_metrics(self):
        r = ComparisonResult(
            seed_mapping=SeedMapping(
                pairs=[(0, 0, 1.0)],
                mean_displacement=1.0,
                max_displacement=1.0,
            ),
            area_comparison=AreaComparison(mean_abs_change=5.0),
            topology_diff=TopologyDiff(
                neighbor_changes=2, jaccard_similarity=0.8
            ),
            similarity_score=0.75,
            verdict="Moderately Similar",
        )
        s = r.summary()
        assert "Matched pairs: 1" in s
        assert "Neighbor changes: 2" in s

    def test_to_dict_has_expected_keys(self):
        r = ComparisonResult(similarity_score=0.5, verdict="test")
        d = r.to_dict()
        assert "similarity_score" in d
        assert "verdict" in d
        assert "seed_mapping" in d
        assert "area_comparison" in d
        assert "topology_diff" in d

    def test_to_dict_values_rounded(self):
        r = ComparisonResult(
            similarity_score=0.123456789,
            seed_mapping=SeedMapping(
                pairs=[(0, 0, 1.123456789)],
                mean_displacement=1.123456789,
                max_displacement=2.123456789,
            ),
        )
        d = r.to_dict()
        assert d["similarity_score"] == 0.1235
        assert d["seed_mapping"]["mean_displacement"] == 1.123457

    def test_to_dict_pairs_serialised(self):
        r = ComparisonResult(
            seed_mapping=SeedMapping(pairs=[(0, 1, 2.5)])
        )
        d = r.to_dict()
        assert d["seed_mapping"]["pairs"] == [
            {"idx_a": 0, "idx_b": 1, "distance": 2.5}
        ]


# ═══════════════════════════════════════════════════════════════════
# TestDiagramSnapshot
# ═══════════════════════════════════════════════════════════════════


class TestDiagramSnapshot:
    def test_seed_count_property(self):
        snap = _make_snapshot([(1, 2), (3, 4)])
        assert snap.seed_count == 2

    def test_region_count_property(self):
        snap = _make_snapshot(
            [(1, 2)], regions={(1, 2): [(0, 0), (1, 0), (1, 1)]}
        )
        assert snap.region_count == 1

    def test_empty_seed_list(self):
        snap = _make_snapshot([])
        assert snap.seed_count == 0
        assert snap.region_count == 0

    def test_bounds_none_by_default(self):
        snap = _make_snapshot([(0, 0)])
        assert snap.bounds is None

    def test_bounds_stored(self):
        snap = _make_snapshot([(0, 0)], bounds=(0, 10, 0, 20))
        assert snap.bounds == (0, 10, 0, 20)

    def test_from_data_creates_snapshot(self):
        """from_data should produce regions and stats via vormap/vormap_viz."""
        seeds = [(100, 100), (200, 200), (300, 100), (200, 50)]
        snap = DiagramSnapshot.from_data(seeds, bounds=(0, 400, 0, 400))
        assert snap.seed_count == 4
        assert snap.region_count >= 1
        assert len(snap.region_stats) >= 1
        assert snap.bounds is not None


# ═══════════════════════════════════════════════════════════════════
# TestCompareDiagrams  (integration)
# ═══════════════════════════════════════════════════════════════════


class TestCompareDiagrams:
    def test_identical_diagrams_high_score(self):
        seeds = [(100, 100), (200, 200), (300, 100), (200, 50)]
        snap = DiagramSnapshot.from_data(seeds, bounds=(0, 400, 0, 400))
        result = compare_diagrams(snap, snap)
        assert result.similarity_score >= 0.95
        assert result.seed_mapping.mean_displacement == 0.0

    def test_slightly_displaced_seeds_high_score(self):
        seeds_a = [(100, 100), (200, 200), (300, 100)]
        seeds_b = [(101, 101), (201, 201), (301, 101)]
        snap_a = DiagramSnapshot.from_data(
            seeds_a, bounds=(0, 400, 0, 400)
        )
        snap_b = DiagramSnapshot.from_data(
            seeds_b, bounds=(0, 400, 0, 400)
        )
        result = compare_diagrams(snap_a, snap_b)
        assert result.similarity_score >= 0.5

    def test_completely_different_seeds_low_score(self):
        seeds_a = [(10, 10), (20, 20), (30, 30)]
        seeds_b = [(350, 350), (360, 360), (370, 370)]
        snap_a = DiagramSnapshot.from_data(
            seeds_a, bounds=(0, 400, 0, 400)
        )
        snap_b = DiagramSnapshot.from_data(
            seeds_b, bounds=(0, 400, 0, 400)
        )
        result = compare_diagrams(snap_a, snap_b)
        assert result.similarity_score < 0.9

    def test_different_seed_counts_penalised(self):
        seeds_a = [(100, 100), (200, 200), (300, 100)]
        seeds_b = [(100, 100)]
        snap_a = DiagramSnapshot.from_data(
            seeds_a, bounds=(0, 400, 0, 400)
        )
        snap_b = DiagramSnapshot.from_data(
            seeds_b, bounds=(0, 400, 0, 400)
        )
        result = compare_diagrams(snap_a, snap_b)
        # Should be lower than identical
        assert result.similarity_score < 0.95

    def test_max_match_distance_works(self):
        seeds_a = [(0, 0), (100, 100)]
        seeds_b = [(0.1, 0.1), (300, 300)]
        snap_a = _make_snapshot(seeds_a, bounds=(0, 400, 0, 400))
        snap_b = _make_snapshot(seeds_b, bounds=(0, 400, 0, 400))
        result = compare_diagrams(snap_a, snap_b, max_match_distance=1.0)
        assert len(result.seed_mapping.pairs) == 1
        assert len(result.seed_mapping.unmatched_a) == 1
        assert len(result.seed_mapping.unmatched_b) == 1

    def test_export_comparison_json_writes_valid_json(self):
        seeds = [(100, 100), (200, 200), (300, 100)]
        snap = DiagramSnapshot.from_data(seeds, bounds=(0, 400, 0, 400))
        result = compare_diagrams(snap, snap)
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            path = f.name
        try:
            export_comparison_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert "similarity_score" in data
            assert "verdict" in data
            assert "seed_mapping" in data
        finally:
            os.unlink(path)

    def test_no_regions_still_works(self):
        snap_a = _make_snapshot([(0, 0), (1, 1)], bounds=(0, 10, 0, 10))
        snap_b = _make_snapshot([(0, 0), (1, 1)], bounds=(0, 10, 0, 10))
        result = compare_diagrams(snap_a, snap_b)
        assert result.similarity_score >= 0.0
        assert result.topology_diff.jaccard_similarity == 0.0

    def test_result_has_verdict(self):
        snap = _make_snapshot([(0, 0)], bounds=(0, 10, 0, 10))
        result = compare_diagrams(snap, snap)
        assert result.verdict != ""


# ═══════════════════════════════════════════════════════════════════
# TestTopologyDiff  (via manual snapshots)
# ═══════════════════════════════════════════════════════════════════


class TestTopologyDiff:
    """Topology comparison tests using from_data snapshots."""

    def test_identical_diagrams_perfect_jaccard(self):
        seeds = [(100, 100), (200, 200), (300, 100), (200, 50)]
        snap = DiagramSnapshot.from_data(seeds, bounds=(0, 400, 0, 400))
        from vormap_compare import compare_topology

        mapping = match_seeds(snap.seeds, snap.seeds)
        td = compare_topology(
            snap.regions, snap.seeds,
            snap.regions, snap.seeds,
            mapping,
        )
        assert td.jaccard_similarity == pytest.approx(1.0)
        assert td.new_edges == []
        assert td.lost_edges == []

    def test_edge_counts_positive(self):
        seeds = [(100, 100), (200, 200), (300, 100), (200, 50)]
        snap = DiagramSnapshot.from_data(seeds, bounds=(0, 400, 0, 400))
        from vormap_compare import compare_topology

        mapping = match_seeds(snap.seeds, snap.seeds)
        td = compare_topology(
            snap.regions, snap.seeds,
            snap.regions, snap.seeds,
            mapping,
        )
        assert td.edge_count_a > 0
        assert td.edge_count_b > 0

    def test_empty_mapping_no_crash(self):
        seeds_a = [(100, 100), (200, 200), (300, 100)]
        seeds_b = [(150, 150), (250, 250), (350, 150)]
        snap_a = DiagramSnapshot.from_data(
            seeds_a, bounds=(0, 400, 0, 400)
        )
        snap_b = DiagramSnapshot.from_data(
            seeds_b, bounds=(0, 400, 0, 400)
        )
        from vormap_compare import compare_topology

        mapping = SeedMapping()  # no pairs
        td = compare_topology(
            snap_a.regions, snap_a.seeds,
            snap_b.regions, snap_b.seeds,
            mapping,
        )
        assert td.jaccard_similarity == 1.0  # empty union → 1.0
        assert td.neighbor_changes == 0

    def test_different_diagrams_changes_detected(self):
        seeds_a = [(100, 100), (200, 200), (300, 100)]
        seeds_b = [(100, 100), (200, 200), (150, 300)]
        snap_a = DiagramSnapshot.from_data(
            seeds_a, bounds=(0, 400, 0, 400)
        )
        snap_b = DiagramSnapshot.from_data(
            seeds_b, bounds=(0, 400, 0, 400)
        )
        from vormap_compare import compare_topology

        mapping = match_seeds(snap_a.seeds, snap_b.seeds)
        td = compare_topology(
            snap_a.regions, snap_a.seeds,
            snap_b.regions, snap_b.seeds,
            mapping,
        )
        # At least some edges should exist
        assert td.edge_count_a > 0
        assert td.edge_count_b > 0

    def test_topology_diff_default_values(self):
        td = TopologyDiff()
        assert td.neighbor_changes == 0
        assert td.jaccard_similarity == 0.0
        assert td.new_edges == []
        assert td.lost_edges == []


# ═══════════════════════════════════════════════════════════════════
# Additional edge-case / coverage tests
# ═══════════════════════════════════════════════════════════════════


class TestMatchSeedsEdgeCases:
    def test_single_seed_in_both(self):
        m = match_seeds([(0, 0)], [(0, 0)])
        assert len(m.pairs) == 1
        assert m.unmatched_a == []
        assert m.unmatched_b == []

    def test_three_vs_two(self):
        a = [(0, 0), (5, 5), (10, 10)]
        b = [(0.1, 0.1), (10.1, 10.1)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 2
        assert len(m.unmatched_a) == 1
        assert m.unmatched_b == []

    def test_pairs_sorted_by_idx_a(self):
        a = [(10, 10), (0, 0)]
        b = [(0.1, 0.1), (10.1, 10.1)]
        m = match_seeds(a, b)
        indices_a = [p[0] for p in m.pairs]
        assert indices_a == sorted(indices_a)

    def test_max_distance_zero(self):
        a = [(0, 0)]
        b = [(0, 0)]
        m = match_seeds(a, b, max_distance=0.0)
        assert len(m.pairs) == 1

    def test_max_distance_zero_no_match(self):
        a = [(0, 0)]
        b = [(0.001, 0)]
        m = match_seeds(a, b, max_distance=0.0)
        assert len(m.pairs) == 0


class TestAreaComparisonEdgeCases:
    def test_mismatched_idx_out_of_range(self):
        stats_a = _make_stats([10])
        stats_b = _make_stats([20])
        # idx beyond range
        mapping = SeedMapping(pairs=[(5, 5, 0.0)])
        ac = compare_areas(stats_a, stats_b, mapping)
        # change not added since idx out of range
        assert ac.changes == []
        assert ac.total_area_a == 10
        assert ac.total_area_b == 20


class TestSimilarityScoreEdgeCases:
    def test_zero_diagonal_bounds(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 0.0)], mean_displacement=0.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        # bounds where south==north, west==east → diagonal = 0
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (5, 5, 5, 5)
        )
        assert score == pytest.approx(1.0)

    def test_large_displacement_low_score(self):
        mapping = SeedMapping(
            pairs=[(0, 0, 100.0)], mean_displacement=100.0
        )
        area = AreaComparison(mean_pct_change=0.0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        # diagonal ~14.14, 100/14.14 * 10 >> 1 → disp_score = 0
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 10, 0, 10)
        )
        assert score < 1.0


class TestCompareAreasIndexMismatch:
    """Regression tests for issue #27: compare_areas index mismatch."""

    def test_compare_areas_with_contiguous_stats(self):
        """Normal case: all seeds have regions, stats indices match."""
        stats_a = [
            {"region_index": 1, "area": 10.0},
            {"region_index": 2, "area": 20.0},
            {"region_index": 3, "area": 30.0},
        ]
        stats_b = [
            {"region_index": 1, "area": 12.0},
            {"region_index": 2, "area": 18.0},
            {"region_index": 3, "area": 33.0},
        ]
        mapping = SeedMapping(
            pairs=[(0, 0, 0.1), (1, 1, 0.2), (2, 2, 0.3)],
            mean_displacement=0.2,
            max_displacement=0.3,
        )
        result = compare_areas(stats_a, stats_b, mapping)
        assert len(result.changes) == 3
        # First pair: area_a=10, area_b=12 → abs_change=2
        assert result.changes[0][2] == 10.0  # area_a
        assert result.changes[0][3] == 12.0  # area_b

    def test_compare_areas_with_gap_in_stats(self):
        """Issue #27: seed #1 has no region, stats_a has a gap."""
        # Seeds: [0, 1, 2] but seed #1 didn't produce a region
        stats_a = [
            {"region_index": 1, "area": 10.0},   # seed index 0
            # seed index 1 is MISSING — no region
            {"region_index": 3, "area": 30.0},   # seed index 2
        ]
        stats_b = [
            {"region_index": 1, "area": 12.0},
            {"region_index": 2, "area": 22.0},
            {"region_index": 3, "area": 33.0},
        ]
        mapping = SeedMapping(
            pairs=[(0, 0, 0.1), (1, 1, 0.2), (2, 2, 0.3)],
            mean_displacement=0.2,
            max_displacement=0.3,
        )
        result = compare_areas(stats_a, stats_b, mapping)
        # Pair (1,1) should be skipped (seed index 1 has no stats in A)
        assert len(result.changes) == 2
        # Pair (0,0): area_a=10, area_b=12
        assert result.changes[0][2] == 10.0
        assert result.changes[0][3] == 12.0
        # Pair (2,2): area_a=30, area_b=33 — NOT stats_a[2] which would be wrong
        assert result.changes[1][2] == 30.0
        assert result.changes[1][3] == 33.0

    def test_compare_areas_with_gap_in_both_stats(self):
        """Both diagrams have missing regions at different indices."""
        stats_a = [
            {"region_index": 1, "area": 10.0},   # seed index 0
            {"region_index": 3, "area": 30.0},   # seed index 2 (gap at 1)
        ]
        stats_b = [
            {"region_index": 2, "area": 22.0},   # seed index 1 (gap at 0)
            {"region_index": 3, "area": 33.0},   # seed index 2
        ]
        mapping = SeedMapping(
            pairs=[(0, 0, 0.1), (1, 1, 0.2), (2, 2, 0.3)],
            mean_displacement=0.2,
            max_displacement=0.3,
        )
        result = compare_areas(stats_a, stats_b, mapping)
        # Pair (0,0): A has stats but B doesn't → skipped
        # Pair (1,1): A doesn't have stats → skipped
        # Pair (2,2): both have stats → included
        assert len(result.changes) == 1
        assert result.changes[0][2] == 30.0  # area_a for seed index 2
        assert result.changes[0][3] == 33.0  # area_b for seed index 2

    def test_compare_areas_no_pairs_still_works(self):
        """No matched pairs returns empty comparison with totals."""
        stats_a = [{"region_index": 1, "area": 10.0}]
        stats_b = [{"region_index": 1, "area": 20.0}]
        mapping = SeedMapping()
        result = compare_areas(stats_a, stats_b, mapping)
        assert len(result.changes) == 0
        assert result.total_area_a == 10.0
        assert result.total_area_b == 20.0

    def test_compare_areas_all_missing_stats_returns_totals(self):
        """All matched seeds have no stats → no changes but totals computed."""
        stats_a = [{"region_index": 5, "area": 50.0}]
        stats_b = [{"region_index": 5, "area": 60.0}]
        # Pairs reference indices 0,1 — neither exists in stats
        mapping = SeedMapping(
            pairs=[(0, 0, 0.1), (1, 1, 0.2)],
            mean_displacement=0.15,
            max_displacement=0.2,
        )
        result = compare_areas(stats_a, stats_b, mapping)
        assert len(result.changes) == 0
        assert result.total_area_a == 50.0
        assert result.total_area_b == 60.0
