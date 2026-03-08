"""Tests for vormap_network — spatial network analysis."""

import json
import math
import os
import sys
import tempfile
import unittest

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vormap_network import (
    build_delaunay_graph,
    compute_mst,
    network_stats,
    export_network_svg,
    export_network_json,
    export_network_csv,
    _euclidean,
    _UnionFind,
    _bfs_distances,
    _betweenness_centrality,
    _connected_components,
)


def _make_grid_stats(rows=3, cols=3, spacing=100):
    """Create a grid of square Voronoi-like regions for testing.

    Each region is a unit square with known adjacency — neighbors share
    exactly one edge (2 vertices).  Returns stats dicts compatible with
    build_delaunay_graph.
    """
    stats = []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * spacing, r * spacing
            poly = [
                (x0, y0),
                (x0 + spacing, y0),
                (x0 + spacing, y0 + spacing),
                (x0, y0 + spacing),
            ]
            cx = x0 + spacing / 2
            cy = y0 + spacing / 2
            stats.append({
                "polygon": poly,
                "centroid": (cx, cy),
                "area": spacing * spacing,
            })
    return stats


def _make_triangle_stats():
    """Three equilateral-ish regions sharing edges."""
    # Three regions in a triangle arrangement, sharing edges pairwise.
    stats = [
        {
            "polygon": [(0, 0), (100, 0), (50, 50)],
            "centroid": (50, 16.67),
            "area": 2500,
        },
        {
            "polygon": [(100, 0), (150, 50), (50, 50)],
            "centroid": (100, 33.33),
            "area": 2500,
        },
        {
            "polygon": [(50, 50), (150, 50), (100, 100)],
            "centroid": (100, 66.67),
            "area": 2500,
        },
    ]
    return stats


class TestEuclidean(unittest.TestCase):
    def test_same_point(self):
        self.assertAlmostEqual(_euclidean((0, 0), (0, 0)), 0.0)

    def test_known_distance(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_negative_coords(self):
        self.assertAlmostEqual(_euclidean((-1, -1), (2, 3)), 5.0)


class TestUnionFind(unittest.TestCase):
    def test_initial_state(self):
        uf = _UnionFind(5)
        for i in range(5):
            self.assertEqual(uf.find(i), i)

    def test_union_and_find(self):
        uf = _UnionFind(5)
        self.assertTrue(uf.union(0, 1))
        self.assertEqual(uf.find(0), uf.find(1))

    def test_redundant_union(self):
        uf = _UnionFind(5)
        uf.union(0, 1)
        self.assertFalse(uf.union(0, 1))

    def test_transitive_union(self):
        uf = _UnionFind(5)
        uf.union(0, 1)
        uf.union(1, 2)
        self.assertEqual(uf.find(0), uf.find(2))


class TestBFS(unittest.TestCase):
    def test_linear_graph(self):
        adj = {0: [1], 1: [0, 2], 2: [1]}
        dists = _bfs_distances(adj, 0, 3)
        self.assertEqual(dists, [0, 1, 2])

    def test_disconnected(self):
        adj = {0: [1], 1: [0], 2: []}
        dists = _bfs_distances(adj, 0, 3)
        self.assertEqual(dists[2], -1)


class TestBetweenness(unittest.TestCase):
    def test_star_center_highest(self):
        # Star: 0 connected to 1,2,3,4
        adj = {0: [1, 2, 3, 4], 1: [0], 2: [0], 3: [0], 4: [0]}
        bc = _betweenness_centrality(adj, 5)
        # Center node should have highest betweenness
        self.assertEqual(max(bc, key=bc.get), 0)

    def test_path_middle_highest(self):
        # Path: 0-1-2-3-4
        adj = {0: [1], 1: [0, 2], 2: [1, 3], 3: [2, 4], 4: [3]}
        bc = _betweenness_centrality(adj, 5)
        # Middle node (2) should have highest betweenness
        self.assertEqual(max(bc, key=bc.get), 2)


class TestConnectedComponents(unittest.TestCase):
    def test_single_component(self):
        adj = {0: [1], 1: [0, 2], 2: [1]}
        comps = _connected_components(adj, 3)
        self.assertEqual(len(comps), 1)

    def test_two_components(self):
        adj = {0: [1], 1: [0], 2: [3], 3: [2]}
        comps = _connected_components(adj, 4)
        self.assertEqual(len(comps), 2)

    def test_all_isolated(self):
        adj = {0: [], 1: [], 2: []}
        comps = _connected_components(adj, 3)
        self.assertEqual(len(comps), 3)


class TestBuildDelaunayGraph(unittest.TestCase):
    def test_grid_3x3(self):
        stats = _make_grid_stats(3, 3)
        graph = build_delaunay_graph(stats)
        self.assertEqual(len(graph["nodes"]), 9)
        self.assertGreater(len(graph["edges"]), 0)
        # Corner cells should have degree 2, edge cells 3, center 4
        adj = graph["adjacency"]
        # Center node (index 4 in a 3x3 grid) should have 4 neighbors
        self.assertEqual(len(adj[4]), 4)
        # Corner nodes (0, 2, 6, 8) should have 2 neighbors
        for corner in [0, 2, 6, 8]:
            self.assertEqual(len(adj[corner]), 2)

    def test_triangle(self):
        stats = _make_triangle_stats()
        graph = build_delaunay_graph(stats)
        self.assertEqual(len(graph["nodes"]), 3)
        # Regions 0-1 share (100,0) and (50,50) → edge
        # Regions 1-2 share (150,50) and (50,50) → edge
        # Regions 0-2 share only (50,50) → no edge (need 2 shared vertices)
        self.assertEqual(len(graph["edges"]), 2)

    def test_empty(self):
        graph = build_delaunay_graph([])
        self.assertEqual(len(graph["nodes"]), 0)
        self.assertEqual(len(graph["edges"]), 0)

    def test_single_region(self):
        stats = [{"polygon": [(0, 0), (1, 0), (1, 1), (0, 1)],
                  "centroid": (0.5, 0.5), "area": 1}]
        graph = build_delaunay_graph(stats)
        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(len(graph["edges"]), 0)

    def test_edge_weights_positive(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        for e in graph["edges"]:
            self.assertGreater(e["weight"], 0)


class TestComputeMST(unittest.TestCase):
    def test_grid_mst(self):
        stats = _make_grid_stats(3, 3)
        graph = build_delaunay_graph(stats)
        mst = compute_mst(graph)
        # MST of n nodes has n-1 edges
        self.assertEqual(len(mst["edges"]), 8)
        self.assertGreater(mst["total_weight"], 0)

    def test_empty_graph(self):
        graph = {"nodes": [], "edges": [], "adjacency": {}}
        mst = compute_mst(graph)
        self.assertEqual(len(mst["edges"]), 0)
        self.assertEqual(mst["total_weight"], 0)

    def test_mst_subset_of_edges(self):
        stats = _make_grid_stats(2, 3)
        graph = build_delaunay_graph(stats)
        mst = compute_mst(graph)
        graph_edge_set = {(e["source"], e["target"]) for e in graph["edges"]}
        for e in mst["edges"]:
            pair = (e["source"], e["target"])
            rev = (e["target"], e["source"])
            self.assertTrue(pair in graph_edge_set or rev in graph_edge_set)

    def test_mst_total_weight_minimum(self):
        # For a grid, MST should pick shorter edges
        stats = _make_grid_stats(2, 2, spacing=100)
        graph = build_delaunay_graph(stats)
        mst = compute_mst(graph)
        # All edges in a regular grid are same length → MST weight = 3 * 100
        self.assertAlmostEqual(mst["total_weight"], 300.0, places=0)


class TestNetworkStats(unittest.TestCase):
    def test_grid_stats(self):
        stats = _make_grid_stats(3, 3)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        self.assertEqual(ns.num_nodes, 9)
        self.assertGreater(ns.num_edges, 0)
        self.assertEqual(ns.num_components, 1)
        self.assertGreater(ns.density, 0)
        self.assertGreater(ns.avg_degree, 0)
        self.assertGreater(ns.diameter, 0)
        self.assertGreater(ns.avg_path_length, 0)
        self.assertGreater(ns.global_efficiency, 0)

    def test_empty(self):
        graph = {"nodes": [], "edges": [], "adjacency": {}}
        ns = network_stats(graph)
        self.assertEqual(ns.num_nodes, 0)

    def test_summary_text(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        text = ns.summary_text()
        self.assertIn("Spatial Network Analysis", text)
        self.assertIn("Nodes:", text)
        self.assertIn("Degree Distribution", text)

    def test_hub_nodes_sorted(self):
        stats = _make_grid_stats(3, 3)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        if len(ns.hub_nodes) > 1:
            for i in range(len(ns.hub_nodes) - 1):
                self.assertGreaterEqual(
                    ns.hub_nodes[i]["betweenness"],
                    ns.hub_nodes[i + 1]["betweenness"],
                )


class TestExportSVG(unittest.TestCase):
    def test_svg_creates_file(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        mst = compute_mst(graph)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_network_svg(graph, stats, path, mst=mst)
            self.assertTrue(os.path.exists(path))
            content = open(path).read()
            self.assertIn("<svg", content)
            self.assertIn("circle", content)
            self.assertIn("line", content)
        finally:
            os.unlink(path)

    def test_svg_with_labels(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_network_svg(graph, stats, path, show_labels=True)
            content = open(path).read()
            self.assertIn("text", content)
        finally:
            os.unlink(path)

    def test_svg_empty_graph(self):
        graph = {"nodes": [], "edges": [], "adjacency": {}}
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_network_svg(graph, [], path)
            # Should not crash — just create empty/minimal SVG or skip
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportJSON(unittest.TestCase):
    def test_json_creates_file(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        mst = compute_mst(graph)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_network_json(graph, ns, path, mst=mst)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("summary", data)
            self.assertIn("nodes", data)
            self.assertIn("edges", data)
            self.assertIn("mst", data)
            self.assertEqual(data["summary"]["nodes"], 4)
        finally:
            os.unlink(path)

    def test_json_without_mst(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_network_json(graph, ns, path)
            with open(path) as f:
                data = json.load(f)
            self.assertNotIn("mst", data)
        finally:
            os.unlink(path)


class TestExportCSV(unittest.TestCase):
    def test_csv_creates_file(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_network_csv(graph, ns, path)
            lines = open(path).readlines()
            self.assertEqual(lines[0].strip(), "index,centroid_x,centroid_y,area,degree,betweenness")
            self.assertEqual(len(lines), 5)  # header + 4 nodes
        finally:
            os.unlink(path)

    def test_csv_values_correct(self):
        stats = _make_grid_stats(2, 2)
        graph = build_delaunay_graph(stats)
        ns = network_stats(graph)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_network_csv(graph, ns, path)
            lines = open(path).readlines()
            row0 = lines[1].strip().split(",")
            self.assertEqual(row0[0], "0")  # index
            self.assertEqual(len(row0), 6)  # 6 columns
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
