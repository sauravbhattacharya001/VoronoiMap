"""Tests for vormap_compare — seed matching, area comparison, scoring."""

import math
import pytest
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vormap_compare import (
    DiagramSnapshot,
    SeedMapping,
    AreaComparison,
    TopologyDiff,
    ComparisonResult,
    match_seeds,
    compare_areas,
    _compute_similarity_score,
    _get_verdict,
)


# ── match_seeds ──────────────────────────────────────────────────────


class TestMatchSeeds:
    """Greedy nearest-neighbor seed matching."""

    def test_identical_seeds(self):
        seeds = [(0, 0), (1, 1), (2, 2)]
        m = match_seeds(seeds, seeds)
        assert len(m.pairs) == 3
        assert m.unmatched_a == []
        assert m.unmatched_b == []
        assert m.mean_displacement == 0.0
        assert m.max_displacement == 0.0

    def test_empty_both(self):
        m = match_seeds([], [])
        assert len(m.pairs) == 0
        assert m.unmatched_a == []
        assert m.unmatched_b == []

    def test_empty_a(self):
        m = match_seeds([], [(1, 1)])
        assert m.unmatched_b == [0]
        assert len(m.pairs) == 0

    def test_empty_b(self):
        m = match_seeds([(1, 1)], [])
        assert m.unmatched_a == [0]
        assert len(m.pairs) == 0

    def test_displaced_seeds(self):
        a = [(0, 0), (10, 10)]
        b = [(0.1, 0.1), (10.1, 10.1)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 2
        # Each pair matched to nearest
        for idx_a, idx_b, d in m.pairs:
            assert idx_a == idx_b  # same index matched
            assert d == pytest.approx(math.sqrt(0.02), abs=1e-6)

    def test_max_distance_filtering(self):
        a = [(0, 0), (100, 100)]
        b = [(0.5, 0.5), (200, 200)]
        m = match_seeds(a, b, max_distance=1.0)
        assert len(m.pairs) == 1
        assert m.pairs[0][0] == 0
        assert m.pairs[0][1] == 0
        assert len(m.unmatched_a) == 1
        assert len(m.unmatched_b) == 1

    def test_different_count(self):
        a = [(0, 0), (1, 1), (2, 2)]
        b = [(0, 0), (1, 1)]
        m = match_seeds(a, b)
        assert len(m.pairs) == 2
        assert len(m.unmatched_a) == 1
        assert len(m.unmatched_b) == 0

    def test_greedy_matching_order(self):
        """Greedy should pick closest pair first."""
        a = [(0, 0), (10, 0)]
        b = [(1, 0), (9, 0)]
        m = match_seeds(a, b)
        # (0,0)→(1,0) d=1, (10,0)→(9,0) d=1
        matched_a = {p[0] for p in m.pairs}
        matched_b = {p[1] for p in m.pairs}
        assert matched_a == {0, 1}
        assert matched_b == {0, 1}


# ── compare_areas ────────────────────────────────────────────────────


class TestCompareAreas:
    def _make_stats(self, areas, start_index=1):
        return [
            {"region_index": start_index + i, "area": a}
            for i, a in enumerate(areas)
        ]

    def test_identical_areas(self):
        stats = self._make_stats([1.0, 2.0, 3.0])
        mapping = SeedMapping(pairs=[(0, 0, 0), (1, 1, 0), (2, 2, 0)])
        result = compare_areas(stats, stats, mapping)
        assert result.mean_abs_change == 0.0
        assert result.mean_pct_change == 0.0

    def test_no_pairs(self):
        stats_a = self._make_stats([1.0, 2.0])
        stats_b = self._make_stats([3.0])
        mapping = SeedMapping()
        result = compare_areas(stats_a, stats_b, mapping)
        assert result.total_area_a == 3.0
        assert result.total_area_b == 3.0

    def test_area_changes(self):
        stats_a = self._make_stats([10.0])
        stats_b = self._make_stats([15.0])
        mapping = SeedMapping(pairs=[(0, 0, 0)])
        result = compare_areas(stats_a, stats_b, mapping)
        assert result.mean_abs_change == pytest.approx(5.0)
        assert result.mean_pct_change == pytest.approx(50.0)
        assert result.max_abs_change == pytest.approx(5.0)

    def test_empty_stats(self):
        mapping = SeedMapping()
        result = compare_areas([], [], mapping)
        assert result.total_area_a == 0.0
        assert result.total_area_b == 0.0


# ── similarity scoring ──────────────────────────────────────────────


class TestSimilarityScore:
    def test_identical_diagrams(self):
        mapping = SeedMapping(pairs=[(0, 0, 0)], mean_displacement=0)
        area = AreaComparison(mean_pct_change=0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        score = _compute_similarity_score(
            mapping, area, topo, 1, 1, (0, 1, 0, 1)
        )
        assert score == 1.0

    def test_completely_different(self):
        mapping = SeedMapping(mean_displacement=100)
        area = AreaComparison(mean_pct_change=200)
        topo = TopologyDiff(jaccard_similarity=0.0)
        score = _compute_similarity_score(
            mapping, area, topo, 10, 1, (0, 1, 0, 1)
        )
        assert score == pytest.approx(0.015, abs=0.05)

    def test_no_pairs(self):
        mapping = SeedMapping()
        area = AreaComparison()
        topo = TopologyDiff(jaccard_similarity=0.0)
        score = _compute_similarity_score(mapping, area, topo, 5, 5, None)
        assert 0 <= score <= 1

    def test_different_seed_counts(self):
        mapping = SeedMapping(pairs=[(0, 0, 0)], mean_displacement=0)
        area = AreaComparison(mean_pct_change=0)
        topo = TopologyDiff(jaccard_similarity=1.0)
        # 1 vs 10 seeds → count_score = 0.1
        score = _compute_similarity_score(
            mapping, area, topo, 1, 10, (0, 1, 0, 1)
        )
        assert score < 1.0
        assert score > 0.5  # displacement + area + topo all perfect


# ── verdict ──────────────────────────────────────────────────────────


class TestVerdict:
    @pytest.mark.parametrize(
        "score, expected",
        [
            (1.0, "Nearly Identical"),
            (0.95, "Nearly Identical"),
            (0.90, "Very Similar"),
            (0.80, "Very Similar"),
            (0.70, "Moderately Similar"),
            (0.50, "Somewhat Different"),
            (0.30, "Very Different"),
            (0.10, "Completely Different"),
            (0.0, "Completely Different"),
        ],
    )
    def test_verdict_thresholds(self, score, expected):
        assert _get_verdict(score) == expected


# ── ComparisonResult ─────────────────────────────────────────────────


class TestComparisonResult:
    def test_summary_contains_score(self):
        r = ComparisonResult(similarity_score=0.85, verdict="Very Similar")
        s = r.summary()
        assert "85.0%" in s
        assert "Very Similar" in s

    def test_to_dict_keys(self):
        r = ComparisonResult(similarity_score=0.5, verdict="test")
        d = r.to_dict()
        assert "similarity_score" in d
        assert "seed_mapping" in d
        assert "area_comparison" in d
        assert "topology_diff" in d
        assert d["similarity_score"] == 0.5

    def test_to_dict_pairs_format(self):
        r = ComparisonResult(
            seed_mapping=SeedMapping(pairs=[(0, 1, 0.5)]),
            similarity_score=0.5,
            verdict="test",
        )
        d = r.to_dict()
        assert d["seed_mapping"]["matched_count"] == 1
        assert d["seed_mapping"]["pairs"][0]["distance"] == 0.5


# ── DiagramSnapshot ──────────────────────────────────────────────────


class TestDiagramSnapshot:
    def test_seed_count(self):
        snap = DiagramSnapshot(seeds=[(0, 0), (1, 1)])
        assert snap.seed_count == 2

    def test_region_count_empty(self):
        snap = DiagramSnapshot(seeds=[(0, 0)])
        assert snap.region_count == 0

    def test_region_count(self):
        snap = DiagramSnapshot(
            seeds=[(0, 0)], regions={(0, 0): [(0, 0), (1, 0), (0, 1)]}
        )
        assert snap.region_count == 1
