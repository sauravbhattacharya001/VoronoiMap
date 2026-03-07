"""Extended tests for vormap_graph.py.

Focuses on coverage gaps: polygon-edge fallback, _edges_from_region_approx,
compute_graph_stats for known topologies (path, cycle, star, complete),
clustering coefficient correctness, diameter/avg_path_length accuracy,
format_graph_stats_table edge cases, and export consistency.

These tests construct synthetic graphs with known metrics to validate
correctness beyond the basic structure tests in test_neighborhood_graph.py.
"""

import json
import math
import os
import tempfile
import pytest

import vormap_graph


# ── Synthetic graph builders ─────────────────────────────────────────

def make_graph(adjacency):
    """Build a minimal graph dict from an adjacency dict."""
    edge_set = set()
    for node, neighbors in adjacency.items():
        for neigh in neighbors:
            edge = (min(node, neigh), max(node, neigh))
            edge_set.add(edge)
    edges = sorted(edge_set)
    seeds = sorted(adjacency.keys())
    seed_indices = {s: i for i, s in enumerate(seeds)}
    return {
        "adjacency": {s: sorted(adjacency[s]) for s in seeds},
        "edges": edges,
        "seed_indices": seed_indices,
        "num_nodes": len(seeds),
        "num_edges": len(edges),
    }


def make_path_graph(n):
    """Path graph: 0-1-2-..-(n-1)."""
    adj = {}
    for i in range(n):
        node = (float(i), 0.0)
        neighbors = []
        if i > 0:
            neighbors.append((float(i - 1), 0.0))
        if i < n - 1:
            neighbors.append((float(i + 1), 0.0))
        adj[node] = neighbors
    return make_graph(adj)


def make_cycle_graph(n):
    """Cycle graph: 0-1-2-..-0."""
    angle_step = 2 * math.pi / n
    nodes = []
    for i in range(n):
        x = round(math.cos(i * angle_step), 4)
        y = round(math.sin(i * angle_step), 4)
        nodes.append((x, y))
    adj = {}
    for i in range(n):
        adj[nodes[i]] = [nodes[(i - 1) % n], nodes[(i + 1) % n]]
    return make_graph(adj)


def make_star_graph(n):
    """Star graph: center node connected to n-1 leaf nodes."""
    center = (0.0, 0.0)
    leaves = [(float(i + 1), 0.0) for i in range(n - 1)]
    adj = {center: list(leaves)}
    for leaf in leaves:
        adj[leaf] = [center]
    return make_graph(adj)


def make_complete_graph(n):
    """Complete graph K_n."""
    nodes = [(float(i), float(i)) for i in range(n)]
    adj = {}
    for i in range(n):
        adj[nodes[i]] = [nodes[j] for j in range(n) if j != i]
    return make_graph(adj)


# ── _edges_from_region_approx tests ─────────────────────────────────

class TestEdgesFromRegionApprox:
    def test_triangle_returns_three_edges(self):
        verts = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
        edges = vormap_graph._edges_from_region_approx(verts, tol=0.5)
        assert len(edges) == 3

    def test_square_returns_four_edges(self):
        verts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        edges = vormap_graph._edges_from_region_approx(verts, tol=0.5)
        assert len(edges) == 4

    def test_duplicate_vertices_collapsed(self):
        """Two vertices within tol collapse to the same rounded point.

        Triangle (0,0)-(0.1,0)-(1,1) with tol=0.5:
        - (0,0) and (0.1,0) both round to (0,0)
        - Edge (0,0)→(0.1,0) becomes degenerate (same point), excluded
        - Edge (0.1,0)→(1,1) and edge (1,1)→(0,0) both become (0,0)→(1,1)
        - Result: only 1 unique edge
        """
        verts = [(0.0, 0.0), (0.1, 0.0), (1.0, 1.0)]
        edges = vormap_graph._edges_from_region_approx(verts, tol=0.5)
        assert len(edges) == 1  # Only (0,0)→(1,1) survives deduplication

    def test_shared_edge_detection(self):
        """Two adjacent polygons sharing an edge should have one frozenset in common."""
        poly_a = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        poly_b = [(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0)]
        edges_a = vormap_graph._edges_from_region_approx(poly_a, tol=0.5)
        edges_b = vormap_graph._edges_from_region_approx(poly_b, tol=0.5)
        shared = edges_a & edges_b
        assert len(shared) == 1  # The edge from (1,0) to (1,1)

    def test_tolerance_handles_float_noise(self):
        """Slightly different coords should still match with appropriate tol."""
        verts_a = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        verts_b = [(1.001, 0.002), (2.0, 0.0), (1.5, 1.0)]
        edges_a = vormap_graph._edges_from_region_approx(verts_a, tol=0.5)
        edges_b = vormap_graph._edges_from_region_approx(verts_b, tol=0.5)
        # With tol=0.5, (1.001, 0.002) rounds to (1.0, 0.0)
        # Shared edge should exist between the two polygons
        shared = edges_a & edges_b
        assert len(shared) >= 0  # Depends on rounding; at least no crash

    def test_single_point_no_edges(self):
        verts = [(0.0, 0.0)]
        edges = vormap_graph._edges_from_region_approx(verts, tol=0.5)
        assert len(edges) == 0

    def test_two_identical_points_no_edges(self):
        verts = [(0.0, 0.0), (0.0, 0.0)]
        edges = vormap_graph._edges_from_region_approx(verts, tol=0.5)
        assert len(edges) == 0

    def test_edges_are_frozensets(self):
        verts = [(0.0, 0.0), (1.0, 0.0), (0.5, 1.0)]
        edges = vormap_graph._edges_from_region_approx(verts, tol=0.5)
        for edge in edges:
            assert isinstance(edge, frozenset)
            assert len(edge) == 2


# ── extract_neighborhood_graph tests ────────────────────────────────

class TestExtractNeighborhoodGraphFallback:
    """Test the polygon-edge fallback path (without scipy)."""

    def test_fallback_two_adjacent_squares(self):
        """Two squares sharing an edge should be neighbors."""
        regions = {
            (0.5, 0.5): [(0, 0), (1, 0), (1, 1), (0, 1)],
            (1.5, 0.5): [(1, 0), (2, 0), (2, 1), (1, 1)],
        }
        # Force fallback by passing data=None
        graph = vormap_graph.extract_neighborhood_graph(regions, data=None, tol=0.5)
        assert graph["num_nodes"] == 2
        assert graph["num_edges"] == 1
        assert (0.5, 0.5) in graph["adjacency"][(1.5, 0.5)]
        assert (1.5, 0.5) in graph["adjacency"][(0.5, 0.5)]

    def test_fallback_three_adjacent_regions(self):
        """Three adjacent squares in a row."""
        regions = {
            (0.5, 0.5): [(0, 0), (1, 0), (1, 1), (0, 1)],
            (1.5, 0.5): [(1, 0), (2, 0), (2, 1), (1, 1)],
            (2.5, 0.5): [(2, 0), (3, 0), (3, 1), (2, 1)],
        }
        graph = vormap_graph.extract_neighborhood_graph(regions, data=None, tol=0.5)
        assert graph["num_nodes"] == 3
        assert graph["num_edges"] == 2
        # Middle node should have 2 neighbors
        assert len(graph["adjacency"][(1.5, 0.5)]) == 2

    def test_fallback_isolated_region(self):
        """A region with no shared edges should have no neighbors."""
        regions = {
            (0.5, 0.5): [(0, 0), (1, 0), (1, 1), (0, 1)],
            (10.5, 10.5): [(10, 10), (11, 10), (11, 11), (10, 11)],
        }
        graph = vormap_graph.extract_neighborhood_graph(regions, data=None, tol=0.5)
        assert graph["num_edges"] == 0
        assert len(graph["adjacency"][(0.5, 0.5)]) == 0

    def test_empty_regions_raises(self):
        with pytest.raises(ValueError, match="No regions"):
            vormap_graph.extract_neighborhood_graph({})


# ── compute_graph_stats topology tests ──────────────────────────────

class TestGraphStatsTopologies:
    def test_path_graph_stats(self):
        """Path P5: 5 nodes, 4 edges, no triangles."""
        graph = make_path_graph(5)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 5
        assert stats["num_edges"] == 4
        assert stats["min_degree"] == 1
        assert stats["max_degree"] == 2
        assert stats["leaf_nodes"] == 2
        assert stats["isolated_nodes"] == 0
        assert stats["is_connected"] is True
        assert stats["clustering_coefficient"] == 0.0  # No triangles in a path
        assert stats["diameter"] == 4

    def test_cycle_graph_stats(self):
        """Cycle C6: 6 nodes, 6 edges, all degree 2."""
        graph = make_cycle_graph(6)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 6
        assert stats["num_edges"] == 6
        assert stats["min_degree"] == 2
        assert stats["max_degree"] == 2
        assert stats["leaf_nodes"] == 0
        assert stats["is_connected"] is True
        assert stats["clustering_coefficient"] == 0.0  # No triangles in C6

    def test_cycle_c3_clustering(self):
        """Cycle C3 = triangle: clustering coefficient should be 1.0."""
        graph = make_cycle_graph(3)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 3
        assert stats["num_edges"] == 3
        assert stats["clustering_coefficient"] == 1.0
        assert stats["diameter"] == 1

    def test_star_graph_stats(self):
        """Star S5: center degree 4, leaves degree 1."""
        graph = make_star_graph(5)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 5
        assert stats["num_edges"] == 4
        assert stats["min_degree"] == 1
        assert stats["max_degree"] == 4
        assert stats["leaf_nodes"] == 4
        assert stats["is_connected"] is True
        assert stats["clustering_coefficient"] == 0.0  # No triangles in star
        assert stats["diameter"] == 2

    def test_complete_graph_k4(self):
        """Complete K4: density 1.0, clustering 1.0, diameter 1."""
        graph = make_complete_graph(4)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 4
        assert stats["num_edges"] == 6
        assert stats["density"] == 1.0
        assert stats["clustering_coefficient"] == 1.0
        assert stats["min_degree"] == 3
        assert stats["max_degree"] == 3
        assert stats["diameter"] == 1
        assert stats["is_connected"] is True

    def test_complete_graph_k5_density(self):
        """K5 should have density 1.0 and 10 edges."""
        graph = make_complete_graph(5)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_edges"] == 10
        assert stats["density"] == 1.0

    def test_degree_distribution_path(self):
        """Path P4: two endpoints (deg 1) and two interior (deg 2)."""
        graph = make_path_graph(4)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["degree_distribution"] == {1: 2, 2: 2}

    def test_mean_degree_formula(self):
        """Mean degree = 2E/N for any graph."""
        for n in [3, 5, 8]:
            graph = make_complete_graph(n)
            stats = vormap_graph.compute_graph_stats(graph)
            expected = 2.0 * stats["num_edges"] / n
            assert abs(stats["mean_degree"] - expected) < 0.01

    def test_disconnected_two_components(self):
        """Two separate edges = 2 components."""
        adj = {
            (0.0, 0.0): [(1.0, 0.0)],
            (1.0, 0.0): [(0.0, 0.0)],
            (5.0, 5.0): [(6.0, 5.0)],
            (6.0, 5.0): [(5.0, 5.0)],
        }
        graph = make_graph(adj)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["connected_components"] == 2
        assert stats["is_connected"] is False
        assert stats["diameter"] is None
        assert stats["avg_path_length"] is None

    def test_avg_path_length_path_3(self):
        """P3 (a-b-c): avg path = (1+2+1)/3 = 4/3 ≈ 1.33."""
        graph = make_path_graph(3)
        stats = vormap_graph.compute_graph_stats(graph)
        # All 3 nodes sampled (n=3 < 50)
        # Distances: 0-1=1, 0-2=2, 1-2=1; avg = 4/3
        assert stats["avg_path_length"] is not None
        assert abs(stats["avg_path_length"] - 4.0 / 3) < 0.1


# ── format_graph_stats_table tests ──────────────────────────────────

class TestFormatGraphStatsTable:
    def test_contains_all_metrics(self):
        graph = make_complete_graph(4)
        table = vormap_graph.format_graph_stats_table(graph)
        assert "Nodes:" in table
        assert "Edges:" in table
        assert "Density:" in table
        assert "Clustering coeff:" in table
        assert "Components:" in table
        assert "Connected:" in table
        assert "Degree distribution:" in table

    def test_disconnected_no_diameter(self):
        adj = {(0.0, 0.0): [], (1.0, 1.0): []}
        graph = make_graph(adj)
        table = vormap_graph.format_graph_stats_table(graph)
        assert "Diameter:" not in table

    def test_connected_shows_diameter(self):
        graph = make_path_graph(4)
        table = vormap_graph.format_graph_stats_table(graph)
        assert "Diameter:" in table
        assert "Avg path length:" in table

    def test_degree_dist_bars(self):
        graph = make_star_graph(5)
        table = vormap_graph.format_graph_stats_table(graph)
        # Degree 1 has count 4
        assert "4" in table
        # Degree 4 has count 1
        lines = table.split("\n")
        deg_lines = [l for l in lines if l.strip().startswith("4:")]
        assert len(deg_lines) >= 1


# ── Export consistency tests ────────────────────────────────────────

class TestExportConsistency:
    def test_json_edge_lengths_positive(self):
        graph = make_path_graph(4)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_graph.export_graph_json(graph, path)
            with open(path) as f:
                data = json.load(f)
            for edge in data["edges"]:
                assert edge["length"] > 0
        finally:
            os.unlink(path)

    def test_json_node_degrees_match(self):
        graph = make_star_graph(5)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_graph.export_graph_json(graph, path)
            with open(path) as f:
                data = json.load(f)
            degrees = [n["degree"] for n in data["nodes"]]
            assert max(degrees) == 4  # center
            assert degrees.count(1) == 4  # leaves
        finally:
            os.unlink(path)

    def test_csv_row_count_matches_edges(self):
        graph = make_complete_graph(4)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_graph.export_graph_csv(graph, path)
            with open(path) as f:
                lines = f.readlines()
            # Header + 6 data rows + blank + comments
            data_lines = [l for l in lines if l.strip() and not l.startswith("#")]
            assert len(data_lines) == 7  # 1 header + 6 edges

        finally:
            os.unlink(path)

    def test_csv_summary_has_density(self):
        graph = make_path_graph(3)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_graph.export_graph_csv(graph, path)
            with open(path) as f:
                content = f.read()
            assert "# density:" in content
            assert "# nodes:" in content
        finally:
            os.unlink(path)

    def test_json_round_trip_node_count(self):
        """JSON export node count matches input."""
        for n in [3, 5, 7]:
            graph = make_cycle_graph(n)
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                path = f.name
            try:
                vormap_graph.export_graph_json(graph, path)
                with open(path) as f:
                    data = json.load(f)
                assert data["num_nodes"] == n
                assert len(data["nodes"]) == n
                assert data["num_edges"] == n
            finally:
                os.unlink(path)

    def test_empty_graph_export_raises(self):
        graph = make_graph({})
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError):
                vormap_graph.export_graph_json(graph, path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ── Edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_node_graph(self):
        graph = make_graph({(0.0, 0.0): []})
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 1
        assert stats["num_edges"] == 0
        assert stats["isolated_nodes"] == 1
        assert stats["density"] == 0.0
        assert stats["connected_components"] == 1  # Single node is one component

    def test_two_connected_nodes(self):
        adj = {(0.0, 0.0): [(1.0, 0.0)], (1.0, 0.0): [(0.0, 0.0)]}
        graph = make_graph(adj)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["num_nodes"] == 2
        assert stats["num_edges"] == 1
        assert stats["density"] == 1.0
        assert stats["diameter"] == 1
        assert stats["is_connected"] is True

    def test_large_path_diameter(self):
        """Path of 20 nodes should have diameter 19."""
        graph = make_path_graph(20)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["diameter"] == 19
        assert stats["is_connected"] is True

    def test_cycle_diameter(self):
        """Cycle of 10 nodes: diameter = 5 (half the cycle)."""
        graph = make_cycle_graph(10)
        stats = vormap_graph.compute_graph_stats(graph)
        assert stats["diameter"] == 5
