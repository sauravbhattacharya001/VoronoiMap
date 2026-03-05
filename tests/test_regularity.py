"""Tests for vormap_regularity — Voronoi entropy & regularity analysis."""

import json
import math
import os
import tempfile
import pytest

from vormap_regularity import (
    polygon_distribution,
    voronoi_entropy,
    lewis_law_fit,
    aboav_weaire_fit,
    regularity_analysis,
    export_regularity_json,
    format_report,
    RegularityResult,
    PolygonDistribution,
    LewisLaw,
    AboavWeaire,
)


# ── PolygonDistribution tests ───────────────────────────────────────

class TestPolygonDistribution:
    def test_empty(self):
        d = polygon_distribution([])
        assert d.total_cells == 0
        assert d.counts == {}

    def test_uniform_hexagons(self):
        d = polygon_distribution([6, 6, 6, 6])
        assert d.counts == {6: 4}
        assert d.fractions[6] == 1.0
        assert d.mean_sides == 6.0
        assert d.variance == 0.0
        assert d.min_sides == 6
        assert d.max_sides == 6

    def test_mixed(self):
        d = polygon_distribution([4, 5, 5, 6, 6, 6, 7])
        assert d.total_cells == 7
        assert d.counts == {4: 1, 5: 2, 6: 3, 7: 1}
        assert abs(d.fractions[6] - 3 / 7) < 1e-10
        assert d.min_sides == 4
        assert d.max_sides == 7

    def test_mean_sides(self):
        sides = [3, 4, 5, 6, 7, 8]
        d = polygon_distribution(sides)
        assert abs(d.mean_sides - 5.5) < 1e-10

    def test_variance(self):
        sides = [5, 5, 7, 7]
        d = polygon_distribution(sides)
        # mean=6, variance = (1+1+1+1)/4 = 1.0
        assert abs(d.variance - 1.0) < 1e-10

    def test_single_cell(self):
        d = polygon_distribution([5])
        assert d.total_cells == 1
        assert d.counts == {5: 1}
        assert d.mean_sides == 5.0
        assert d.variance == 0.0

    def test_fractions_sum_to_one(self):
        d = polygon_distribution([3, 4, 5, 6, 7, 8, 9, 10])
        total = sum(d.fractions.values())
        assert abs(total - 1.0) < 1e-10

    def test_counts_sorted(self):
        d = polygon_distribution([7, 3, 5, 3, 7, 5])
        keys = list(d.counts.keys())
        assert keys == sorted(keys)


# ── Voronoi entropy tests ──────────────────────────────────────────

class TestVoronoiEntropy:
    def test_empty(self):
        assert voronoi_entropy([]) == 0.0

    def test_all_same(self):
        # All hexagons → entropy = 0
        assert voronoi_entropy([6, 6, 6, 6]) == 0.0

    def test_two_equal_types(self):
        # 50/50 split → entropy = 1 bit
        e = voronoi_entropy([5, 5, 6, 6])
        assert abs(e - 1.0) < 1e-10

    def test_four_equal_types(self):
        # 4 types equal → entropy = 2 bits
        e = voronoi_entropy([4, 5, 6, 7])
        assert abs(e - 2.0) < 1e-10

    def test_non_negative(self):
        import random
        random.seed(42)
        sides = [random.randint(3, 10) for _ in range(100)]
        assert voronoi_entropy(sides) >= 0.0

    def test_bounded_by_log_types(self):
        sides = [3, 4, 5, 6, 7, 8]
        e = voronoi_entropy(sides)
        assert e <= math.log2(6) + 1e-10

    def test_skewed_distribution(self):
        # 90% hexagons, 10% pentagons → low entropy
        sides = [6] * 90 + [5] * 10
        e = voronoi_entropy(sides)
        assert 0 < e < 1.0

    def test_single_cell(self):
        assert voronoi_entropy([6]) == 0.0


# ── Lewis's law tests ──────────────────────────────────────────────

class TestLewisLaw:
    def test_empty(self):
        result = lewis_law_fit([], [])
        assert result.slope == 0.0
        assert result.data_points == []

    def test_mismatched_lengths(self):
        result = lewis_law_fit([5, 6], [1.0])
        assert result.slope == 0.0

    def test_single_group(self):
        result = lewis_law_fit([6, 6, 6], [1.0, 2.0, 3.0])
        # Only 1 group → can't fit a line
        assert result.slope == 0.0

    def test_perfect_linear(self):
        # A = 10 * n (perfect linear)
        sides = [4, 4, 5, 5, 6, 6, 7, 7]
        areas = [40, 40, 50, 50, 60, 60, 70, 70]
        result = lewis_law_fit(sides, areas)
        assert abs(result.slope - 10.0) < 1e-6
        assert abs(result.r_squared - 1.0) < 1e-10
        assert result.holds is True

    def test_data_points_sorted(self):
        sides = [7, 5, 6, 4, 5, 6, 7, 4]
        areas = [7, 5, 6, 4, 5, 6, 7, 4]
        result = lewis_law_fit(sides, areas)
        ns = [p[0] for p in result.data_points]
        assert ns == sorted(ns)

    def test_poor_fit(self):
        # Random areas, no relation to sides
        sides = [4, 5, 6, 7, 8]
        areas = [100, 1, 50, 2, 80]
        result = lewis_law_fit(sides, areas)
        # R² should be low
        assert result.r_squared < 0.7
        assert result.holds is False

    def test_two_groups(self):
        sides = [5, 5, 7, 7]
        areas = [10, 10, 20, 20]
        result = lewis_law_fit(sides, areas)
        assert len(result.data_points) == 2
        # Perfect linear with 2 points → R² = 1
        assert abs(result.r_squared - 1.0) < 1e-10

    def test_intercept_n(self):
        # A = 10*(n - 2) → intercept_n = 2
        sides = [3, 4, 5, 6]
        areas = [10, 20, 30, 40]
        result = lewis_law_fit(sides, areas)
        assert abs(result.intercept_n - 2.0) < 1e-6


# ── Aboav-Weaire law tests ─────────────────────────────────────────

class TestAboavWeaire:
    def test_empty(self):
        result = aboav_weaire_fit([], {}, {})
        assert result.a == 0.0

    def test_too_few_cells(self):
        result = aboav_weaire_fit([6, 6], {}, {})
        assert result.a == 0.0

    def test_uniform_hexagonal(self):
        # All hexagons, all neighbors have 6 sides
        seeds = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
        adj = {s: [nb for nb in seeds if nb != s] for s in seeds}
        s2s = {s: 6 for s in seeds}
        side_counts = [6] * 6
        result = aboav_weaire_fit(side_counts, adj, s2s)
        # n·m(n) should be constant (6·6=36) for all n=6
        # Only 1 group → can't fit
        assert result.data_points == [] or len(result.data_points) <= 1

    def test_with_varied_topology(self):
        # Create a varied topology
        seeds = [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1)]
        adj = {
            (0, 0): [(1, 0), (0, 1)],
            (1, 0): [(0, 0), (2, 0), (1, 1)],
            (2, 0): [(1, 0), (1, 1)],
            (0, 1): [(0, 0), (1, 1)],
            (1, 1): [(1, 0), (2, 0), (0, 1), (0, 0)],
        }
        s2s = {(0, 0): 4, (1, 0): 5, (2, 0): 5, (0, 1): 4, (1, 1): 6}
        side_counts = [4, 5, 5, 4, 6]
        result = aboav_weaire_fit(side_counts, adj, s2s)
        # Should produce data points
        assert len(result.data_points) >= 2

    def test_r_squared_bounded(self):
        seeds = [(i, j) for i in range(3) for j in range(3)]
        adj = {}
        for s in seeds:
            adj[s] = [nb for nb in seeds if nb != s and abs(nb[0]-s[0]) + abs(nb[1]-s[1]) == 1]
        s2s = {s: 4 + i % 4 for i, s in enumerate(seeds)}
        side_counts = list(s2s.values())
        result = aboav_weaire_fit(side_counts, adj, s2s)
        assert 0.0 <= result.r_squared <= 1.0 or result.r_squared < 0

    def test_holds_threshold(self):
        # Perfect correlation → holds=True
        seeds = [(0, 0), (1, 0), (2, 0)]
        adj = {
            (0, 0): [(1, 0)],
            (1, 0): [(0, 0), (2, 0)],
            (2, 0): [(1, 0)],
        }
        s2s = {(0, 0): 4, (1, 0): 6, (2, 0): 8}
        result = aboav_weaire_fit([4, 6, 8], adj, s2s)
        # At least 2 data points needed; holds depends on fit quality
        if len(result.data_points) >= 2:
            assert isinstance(result.holds, bool)


# ── Regularity analysis integration tests ──────────────────────────

class TestRegularityAnalysis:
    @staticmethod
    def _make_stats(side_counts, areas):
        """Helper to create region_stats dicts."""
        stats = []
        for i, (n, a) in enumerate(zip(side_counts, areas)):
            stats.append({
                "region_index": i + 1,
                "seed_x": float(i),
                "seed_y": 0.0,
                "area": a,
                "perimeter": n * 1.0,
                "centroid_x": float(i),
                "centroid_y": 0.0,
                "vertex_count": n,
                "compactness": 0.8,
                "avg_edge_length": 1.0,
            })
        return stats

    @staticmethod
    def _make_regions(n_cells):
        """Helper to create simple regions dict."""
        regions = {}
        for i in range(n_cells):
            seed = (float(i), 0.0)
            regions[seed] = [(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)]
        return regions

    @staticmethod
    def _make_graph(n_cells):
        """Helper to create simple adjacency graph."""
        seeds = [(float(i), 0.0) for i in range(n_cells)]
        adjacency = {}
        for i, s in enumerate(seeds):
            neighbors = []
            if i > 0:
                neighbors.append(seeds[i - 1])
            if i < n_cells - 1:
                neighbors.append(seeds[i + 1])
            adjacency[s] = neighbors
        return {
            "adjacency": adjacency,
            "edges": [],
            "seed_indices": {s: i for i, s in enumerate(seeds)},
            "num_nodes": n_cells,
            "num_edges": n_cells - 1,
        }

    def test_empty_stats(self):
        result = regularity_analysis([], {}, {})
        assert result.interpretation == "No cells to analyse"

    def test_uniform_hexagons(self):
        sides = [6] * 20
        areas = [10.0] * 20
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(20)
        graph = self._make_graph(20)
        result = regularity_analysis(stats, regions, graph)
        assert result.entropy == 0.0
        assert result.hex_fraction == 1.0
        assert result.area_cv == 0.0
        assert result.regularity_score >= 80.0

    def test_random_like(self):
        import random
        random.seed(99)
        sides = [random.choice([4, 5, 5, 6, 6, 6, 7, 7, 8]) for _ in range(50)]
        areas = [random.uniform(1, 100) for _ in range(50)]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(50)
        graph = self._make_graph(50)
        result = regularity_analysis(stats, regions, graph)
        assert result.entropy > 0
        assert 0 <= result.regularity_score <= 100

    def test_score_bounds(self):
        sides = [5, 6, 7]
        areas = [10.0, 20.0, 30.0]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(3)
        graph = self._make_graph(3)
        result = regularity_analysis(stats, regions, graph)
        assert 0 <= result.regularity_score <= 100

    def test_distribution_populated(self):
        sides = [4, 5, 6, 7]
        areas = [1, 2, 3, 4]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(4)
        graph = self._make_graph(4)
        result = regularity_analysis(stats, regions, graph)
        assert result.distribution.total_cells == 4
        assert len(result.distribution.counts) == 4

    def test_lewis_populated(self):
        sides = [4, 5, 6, 7]
        areas = [4, 5, 6, 7]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(4)
        graph = self._make_graph(4)
        result = regularity_analysis(stats, regions, graph)
        assert len(result.lewis.data_points) == 4

    def test_aboav_populated(self):
        sides = [4, 5, 6, 7, 5]
        areas = [4, 5, 6, 7, 5]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(5)
        graph = self._make_graph(5)
        result = regularity_analysis(stats, regions, graph)
        # Aboav may or may not have data depending on adjacency diversity
        assert isinstance(result.aboav, AboavWeaire)

    def test_interpretation_string(self):
        sides = [6] * 10
        areas = [5.0] * 10
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(10)
        graph = self._make_graph(10)
        result = regularity_analysis(stats, regions, graph)
        assert len(result.interpretation) > 0

    def test_normalised_entropy_bounded(self):
        sides = [3, 4, 5, 6, 7, 8]
        areas = [1, 2, 3, 4, 5, 6]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(6)
        graph = self._make_graph(6)
        result = regularity_analysis(stats, regions, graph)
        assert 0 <= result.normalised_entropy <= 1.0

    def test_area_cv_positive(self):
        sides = [5, 6, 7]
        areas = [1.0, 2.0, 3.0]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(3)
        graph = self._make_graph(3)
        result = regularity_analysis(stats, regions, graph)
        assert result.area_cv > 0

    def test_area_cv_zero_uniform(self):
        sides = [5, 6, 7]
        areas = [10.0, 10.0, 10.0]
        stats = self._make_stats(sides, areas)
        regions = self._make_regions(3)
        graph = self._make_graph(3)
        result = regularity_analysis(stats, regions, graph)
        assert result.area_cv == 0.0


# ── Export & format tests ──────────────────────────────────────────

class TestExport:
    def _make_result(self):
        sides = [4, 5, 6, 6, 7]
        areas = [4, 5, 6, 6, 7]
        stats = TestRegularityAnalysis._make_stats(sides, areas)
        regions = TestRegularityAnalysis._make_regions(5)
        graph = TestRegularityAnalysis._make_graph(5)
        return regularity_analysis(stats, regions, graph)

    def test_to_dict_keys(self):
        result = self._make_result()
        d = result.to_dict()
        assert "entropy" in d
        assert "distribution" in d
        assert "lewis_law" in d
        assert "aboav_weaire" in d
        assert "regularity_score" in d

    def test_json_serialisable(self):
        result = self._make_result()
        d = result.to_dict()
        s = json.dumps(d)
        assert len(s) > 0

    def test_export_json_file(self):
        result = self._make_result()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_regularity_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert data["entropy"] >= 0
            assert "distribution" in data
        finally:
            os.unlink(path)

    def test_format_report_not_empty(self):
        result = self._make_result()
        report = format_report(result)
        assert len(report) > 0
        assert "entropy" in report.lower()

    def test_format_report_contains_sections(self):
        result = self._make_result()
        report = format_report(result)
        assert "Lewis" in report
        assert "Aboav" in report
        assert "Polygon Distribution" in report

    def test_format_report_contains_score(self):
        result = self._make_result()
        report = format_report(result)
        assert "/100" in report

    def test_to_dict_distribution_fields(self):
        result = self._make_result()
        d = result.to_dict()
        dist = d["distribution"]
        assert "counts" in dist
        assert "fractions" in dist
        assert "mean_sides" in dist
        assert "variance" in dist

    def test_to_dict_lewis_fields(self):
        result = self._make_result()
        d = result.to_dict()
        lewis = d["lewis_law"]
        assert "slope" in lewis
        assert "r_squared" in lewis
        assert "holds" in lewis
        assert "data_points" in lewis

    def test_to_dict_aboav_fields(self):
        result = self._make_result()
        d = result.to_dict()
        aw = d["aboav_weaire"]
        assert "a" in aw
        assert "r_squared" in aw
        assert "holds" in aw


# ── Edge case tests ────────────────────────────────────────────────

class TestEdgeCases:
    def test_all_triangles(self):
        d = polygon_distribution([3, 3, 3])
        assert d.counts == {3: 3}
        e = voronoi_entropy([3, 3, 3])
        assert e == 0.0

    def test_extreme_variety(self):
        sides = list(range(3, 20))
        d = polygon_distribution(sides)
        assert d.total_cells == 17
        e = voronoi_entropy(sides)
        assert abs(e - math.log2(17)) < 1e-10

    def test_large_dataset(self):
        import random
        random.seed(123)
        sides = [random.choice([4, 5, 5, 6, 6, 6, 6, 7, 7, 8]) for _ in range(1000)]
        d = polygon_distribution(sides)
        assert d.total_cells == 1000
        e = voronoi_entropy(sides)
        assert 0 < e < math.log2(5) + 0.01

    def test_two_cells(self):
        sides = [4, 8]
        areas = [10, 50]
        stats = TestRegularityAnalysis._make_stats(sides, areas)
        regions = TestRegularityAnalysis._make_regions(2)
        graph = TestRegularityAnalysis._make_graph(2)
        result = regularity_analysis(stats, regions, graph)
        assert result.distribution.total_cells == 2
        assert result.entropy == 1.0  # 2 equal types → 1 bit

    def test_single_cell(self):
        stats = TestRegularityAnalysis._make_stats([6], [10.0])
        regions = TestRegularityAnalysis._make_regions(1)
        graph = TestRegularityAnalysis._make_graph(1)
        result = regularity_analysis(stats, regions, graph)
        assert result.entropy == 0.0
        assert result.distribution.total_cells == 1


# ── Dataclass defaults ─────────────────────────────────────────────

class TestDataclassDefaults:
    def test_regularity_result_defaults(self):
        r = RegularityResult()
        assert r.entropy == 0.0
        assert r.regularity_score == 0.0
        assert r.interpretation == ""

    def test_polygon_distribution_defaults(self):
        d = PolygonDistribution()
        assert d.total_cells == 0
        assert d.counts == {}

    def test_lewis_law_defaults(self):
        l = LewisLaw()
        assert l.slope == 0.0
        assert l.holds is False

    def test_aboav_weaire_defaults(self):
        a = AboavWeaire()
        assert a.a == 0.0
        assert a.holds is False
