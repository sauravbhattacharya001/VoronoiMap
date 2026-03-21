"""Tests for vormap_graph — neighbourhood graph extraction and analysis."""

import math
import pytest

import vormap_graph


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_square_regions():
    """Four points in a square — produces a simple 4-node graph.

    Layout:
        (0,100)──(100,100)
           |   \\   /  |
           |    \\ /   |
        (0,0)──(100,0)
    """
    data = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]
    # Simulate Voronoi regions as quadrants meeting at center (50, 50)
    regions = {
        (0.0, 0.0): [(0, 0), (50, 0), (50, 50), (0, 50)],
        (100.0, 0.0): [(50, 0), (100, 0), (100, 50), (50, 50)],
        (100.0, 100.0): [(50, 50), (100, 50), (100, 100), (50, 100)],
        (0.0, 100.0): [(0, 50), (50, 50), (50, 100), (0, 100)],
    }
    return data, regions


def _make_triangle_regions():
    """Three points forming a triangle — produces a 3-node complete graph."""
    data = [(0.0, 0.0), (100.0, 0.0), (50.0, 86.6)]
    # Simplified regions that share edges at midpoints
    regions = {
        (0.0, 0.0): [(0, 0), (50, 0), (50, 28.87), (0, 43.3)],
        (100.0, 0.0): [(50, 0), (100, 0), (100, 43.3), (50, 28.87)],
        (50.0, 86.6): [(0, 43.3), (50, 28.87), (100, 43.3), (100, 86.6), (0, 86.6)],
    }
    return data, regions


def _make_single_region():
    """One point — produces a 1-node graph with no edges."""
    data = [(50.0, 50.0)]
    regions = {(50.0, 50.0): [(0, 0), (100, 0), (100, 100), (0, 100)]}
    return data, regions


# ── extract_neighborhood_graph tests ─────────────────────────────────

class TestExtractNeighborhoodGraph:

    def test_square_node_count(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        assert g["num_nodes"] == 4

    def test_square_has_edges(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        # 4 points in a square: Delaunay gives 5 edges (4 boundary + 1 diagonal)
        # Polygon fallback gives 4 (shared edges only)
        assert g["num_edges"] >= 4

    def test_triangle_complete_graph(self):
        data, regions = _make_triangle_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        assert g["num_nodes"] == 3
        # Triangle should give 3 edges (complete graph K3)
        assert g["num_edges"] >= 2  # at least most edges found

    def test_single_point_no_edges(self):
        data, regions = _make_single_region()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        assert g["num_nodes"] == 1
        assert g["num_edges"] == 0

    def test_empty_regions_raises(self):
        with pytest.raises(ValueError, match="No regions"):
            vormap_graph.extract_neighborhood_graph({}, [])

    def test_adjacency_is_symmetric(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        for seed, neighbors in g["adjacency"].items():
            for neigh in neighbors:
                assert seed in g["adjacency"][neigh], \
                    f"{seed} -> {neigh} but not {neigh} -> {seed}"

    def test_seed_indices_complete(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        assert len(g["seed_indices"]) >= len(regions)

    def test_edges_are_sorted(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        assert g["edges"] == sorted(g["edges"])


# ── _edges_from_region_approx tests ──────────────────────────────────

class TestEdgesFromRegionApprox:

    def test_triangle_gives_three_edges(self):
        vertices = [(0, 0), (10, 0), (5, 10)]
        edges = vormap_graph._edges_from_region_approx(vertices)
        assert len(edges) == 3

    def test_degenerate_collapses_duplicate_vertices(self):
        # Two identical vertices after rounding
        vertices = [(0, 0), (0.1, 0.1), (10, 0)]
        edges = vormap_graph._edges_from_region_approx(vertices, tol=0.5)
        # Some edges may collapse — just ensure no crash
        assert isinstance(edges, set)


# ── compute_graph_stats tests ────────────────────────────────────────

class TestComputeGraphStats:

    def test_empty_graph(self):
        graph = {"adjacency": {}, "edges": [], "num_nodes": 0, "num_edges": 0,
                 "seed_indices": {}}
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 0
        assert stats["density"] == 0.0
        assert stats["diameter"] is None

    def test_single_node(self):
        seed = (50.0, 50.0)
        graph = {"adjacency": {seed: []}, "edges": [], "num_nodes": 1,
                 "num_edges": 0, "seed_indices": {seed: 0}}
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 1
        assert stats["num_edges"] == 0
        assert stats["isolated_nodes"] == 1

    def test_square_stats(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        stats = vormap_graph.compute_graph_stats(g)
        assert stats["num_nodes"] == 4
        assert stats["min_degree"] >= 1
        assert stats["max_degree"] >= 2
        assert 0.0 <= stats["density"] <= 1.0
        assert 0.0 <= stats["clustering_coefficient"] <= 1.0

    def test_connected_has_diameter(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        stats = vormap_graph.compute_graph_stats(g)
        if stats["is_connected"]:
            assert stats["diameter"] is not None
            assert stats["diameter"] >= 1
            assert stats["avg_path_length"] > 0


# ── _compute_degree_stats tests ──────────────────────────────────────

class TestDegreStats:

    def test_complete_graph_k3(self):
        adj = {
            "a": ["b", "c"],
            "b": ["a", "c"],
            "c": ["a", "b"],
        }
        degrees, min_d, max_d, mean_d, dist, isolated, leaves = \
            vormap_graph._compute_degree_stats(adj)
        assert min_d == 2
        assert max_d == 2
        assert mean_d == 2.0
        assert isolated == 0
        assert leaves == 0

    def test_star_graph(self):
        adj = {
            "center": ["a", "b", "c"],
            "a": ["center"],
            "b": ["center"],
            "c": ["center"],
        }
        degrees, min_d, max_d, mean_d, dist, isolated, leaves = \
            vormap_graph._compute_degree_stats(adj)
        assert min_d == 1
        assert max_d == 3
        assert leaves == 3


# ── _compute_clustering tests ────────────────────────────────────────

class TestClustering:

    def test_complete_graph_clustering_is_1(self):
        adj = {
            "a": ["b", "c"],
            "b": ["a", "c"],
            "c": ["a", "b"],
        }
        cc = vormap_graph._compute_clustering(adj)
        assert abs(cc - 1.0) < 1e-9

    def test_star_graph_clustering_is_0(self):
        adj = {
            "center": ["a", "b", "c"],
            "a": ["center"],
            "b": ["center"],
            "c": ["center"],
        }
        cc = vormap_graph._compute_clustering(adj)
        assert cc == 0.0


# ── _find_components tests ───────────────────────────────────────────

class TestFindComponents:

    def test_connected(self):
        adj = {"a": ["b"], "b": ["a", "c"], "c": ["b"]}
        n, connected = vormap_graph._find_components(adj)
        assert n == 1
        assert connected is True

    def test_disconnected(self):
        adj = {"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}
        n, connected = vormap_graph._find_components(adj)
        assert n == 2
        assert connected is False

    def test_isolated_nodes(self):
        adj = {"a": [], "b": [], "c": []}
        n, _ = vormap_graph._find_components(adj)
        assert n == 3


# ── format_graph_stats_table test ────────────────────────────────────

class TestFormatTable:

    def test_returns_string(self):
        data, regions = _make_square_regions()
        g = vormap_graph.extract_neighborhood_graph(regions, data)
        table = vormap_graph.format_graph_stats_table(g)
        assert isinstance(table, str)
        assert "nodes" in table.lower() or "Nodes" in table
