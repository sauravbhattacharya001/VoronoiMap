"""Tests for the neighbourhood graph (Delaunay dual) feature.

Tests cover graph extraction, graph statistics, JSON/CSV export,
SVG overlay visualization, and the CLI integration.
"""

import csv
import json
import math
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap
import vormap_viz


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def triangle_data():
    """Three well-separated points forming a triangle."""
    return [(100.0, 100.0), (300.0, 100.0), (200.0, 300.0)]


@pytest.fixture
def triangle_regions(triangle_data):
    """Voronoi regions for the triangle dataset."""
    vormap.set_bounds(-50, 450, -50, 450)
    return vormap_viz.compute_regions(triangle_data)


@pytest.fixture
def five_point_data():
    """Five points in a cross-like arrangement."""
    return [
        (200.0, 200.0),
        (800.0, 200.0),
        (500.0, 500.0),
        (200.0, 800.0),
        (800.0, 800.0),
    ]


@pytest.fixture
def five_point_regions(five_point_data):
    vormap.set_bounds(-100, 1100, -100, 1100)
    return vormap_viz.compute_regions(five_point_data)


@pytest.fixture
def simple_data_dir(tmp_path):
    """Create a temp data/ dir with a test dataset."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    test_file = data_dir / "test_graph.txt"
    test_file.write_text(
        "150.0 200.0\n"
        "850.0 150.0\n"
        "500.0 800.0\n"
        "200.0 600.0\n"
        "750.0 550.0\n"
    )
    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path, "test_graph.txt"
    os.chdir(old_cwd)
    vormap._data_cache.pop("test_graph.txt", None)
    vormap._kdtree_cache.pop("test_graph.txt", None)


# ── Graph extraction tests ───────────────────────────────────────────

class TestExtractNeighborhoodGraph:
    def test_empty_regions_raises(self):
        with pytest.raises(ValueError, match="No regions"):
            vormap_viz.extract_neighborhood_graph({})

    def test_triangle_graph_structure(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        assert graph["num_nodes"] == 3
        # Triangle: each pair is neighbours -> 3 edges
        assert graph["num_edges"] == 3

    def test_triangle_adjacency_symmetric(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        adj = graph["adjacency"]
        for seed, neighbors in adj.items():
            for neigh in neighbors:
                assert seed in adj[neigh], (
                    "Adjacency not symmetric: %s -> %s" % (seed, neigh)
                )

    def test_five_points_has_edges(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        assert graph["num_nodes"] == 5
        assert graph["num_edges"] >= 4  # at least spanning tree

    def test_edges_are_sorted(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        edges = graph["edges"]
        for i in range(len(edges) - 1):
            assert edges[i] <= edges[i + 1]

    def test_seed_indices_present(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        assert len(graph["seed_indices"]) >= 3
        for seed in graph["adjacency"]:
            assert seed in graph["seed_indices"]

    def test_data_none_uses_sorted_indices(self, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, data=None
        )
        assert graph["num_nodes"] == 3
        # Indices should be 0, 1, 2
        indices = set(graph["seed_indices"].values())
        assert indices == {0, 1, 2}

    def test_adjacency_lists_sorted(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        for seed, neighbors in graph["adjacency"].items():
            assert neighbors == sorted(neighbors)

    def test_no_self_loops(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        for seed, neighbors in graph["adjacency"].items():
            assert seed not in neighbors

    def test_edge_endpoints_in_adjacency(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        for s1, s2 in graph["edges"]:
            assert s2 in graph["adjacency"][s1]
            assert s1 in graph["adjacency"][s2]


# ── Graph statistics tests ───────────────────────────────────────────

class TestComputeGraphStats:
    def test_empty_graph(self):
        graph = {
            "adjacency": {},
            "edges": [],
            "seed_indices": {},
            "num_nodes": 0,
            "num_edges": 0,
        }
        stats = vormap_viz.compute_graph_stats(graph)
        assert stats["num_nodes"] == 0
        assert stats["num_edges"] == 0
        assert stats["density"] == 0.0
        assert stats["connected_components"] == 0
        assert not stats["is_connected"]

    def test_triangle_stats(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        stats = vormap_viz.compute_graph_stats(graph)

        assert stats["num_nodes"] == 3
        assert stats["num_edges"] == 3
        # Complete graph K3: density = 1.0
        assert stats["density"] == 1.0
        assert stats["min_degree"] == 2
        assert stats["max_degree"] == 2
        assert stats["mean_degree"] == 2.0
        # K3: clustering = 1.0 (every triangle is closed)
        assert stats["clustering_coefficient"] == 1.0
        assert stats["connected_components"] == 1
        assert stats["is_connected"]
        assert stats["diameter"] == 1
        assert stats["isolated_nodes"] == 0
        assert stats["leaf_nodes"] == 0

    def test_degree_distribution(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        stats = vormap_viz.compute_graph_stats(graph)
        # All 3 nodes have degree 2
        assert 2 in stats["degree_distribution"]
        assert stats["degree_distribution"][2] == 3

    def test_five_point_connected(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        stats = vormap_viz.compute_graph_stats(graph)
        assert stats["is_connected"]
        assert stats["connected_components"] == 1
        assert stats["diameter"] is not None
        assert stats["avg_path_length"] is not None
        assert stats["diameter"] >= 1
        assert stats["avg_path_length"] > 0

    def test_density_range(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        stats = vormap_viz.compute_graph_stats(graph)
        assert 0.0 <= stats["density"] <= 1.0

    def test_clustering_range(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        stats = vormap_viz.compute_graph_stats(graph)
        assert 0.0 <= stats["clustering_coefficient"] <= 1.0

    def test_single_node(self):
        """Graph with one node (no edges)."""
        graph = {
            "adjacency": {(0.0, 0.0): []},
            "edges": [],
            "seed_indices": {(0.0, 0.0): 0},
            "num_nodes": 1,
            "num_edges": 0,
        }
        stats = vormap_viz.compute_graph_stats(graph)
        assert stats["num_nodes"] == 1
        assert stats["isolated_nodes"] == 1
        assert stats["density"] == 0.0
        assert stats["connected_components"] == 1

    def test_disconnected_graph(self):
        """Two isolated nodes."""
        graph = {
            "adjacency": {(0.0, 0.0): [], (10.0, 10.0): []},
            "edges": [],
            "seed_indices": {(0.0, 0.0): 0, (10.0, 10.0): 1},
            "num_nodes": 2,
            "num_edges": 0,
        }
        stats = vormap_viz.compute_graph_stats(graph)
        assert stats["connected_components"] == 2
        assert not stats["is_connected"]
        assert stats["diameter"] is None
        assert stats["avg_path_length"] is None


# ── JSON export tests ────────────────────────────────────────────────

class TestExportGraphJson:
    def test_creates_valid_json(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_json(graph, path)
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert "nodes" in data
            assert "edges" in data
            assert "num_nodes" in data
            assert "num_edges" in data
        finally:
            os.unlink(path)

    def test_includes_stats_by_default(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_json(graph, path)
            with open(path) as f:
                data = json.load(f)
            assert "stats" in data
            assert "density" in data["stats"]
        finally:
            os.unlink(path)

    def test_excludes_stats_when_disabled(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_json(graph, path, include_stats=False)
            with open(path) as f:
                data = json.load(f)
            assert "stats" not in data
        finally:
            os.unlink(path)

    def test_node_structure(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_json(graph, path)
            with open(path) as f:
                data = json.load(f)
            for node in data["nodes"]:
                assert "index" in node
                assert "x" in node
                assert "y" in node
                assert "degree" in node
                assert "neighbors" in node
        finally:
            os.unlink(path)

    def test_edge_structure(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_json(graph, path)
            with open(path) as f:
                data = json.load(f)
            for edge in data["edges"]:
                assert "source" in edge
                assert "target" in edge
                assert "length" in edge
                assert edge["length"] > 0
        finally:
            os.unlink(path)

    def test_empty_graph_raises(self):
        graph = {
            "adjacency": {}, "edges": [], "seed_indices": {},
            "num_nodes": 0, "num_edges": 0,
        }
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No graph data"):
                vormap_viz.export_graph_json(graph, path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ── CSV export tests ─────────────────────────────────────────────────

class TestExportGraphCsv:
    def test_creates_valid_csv(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_csv(graph, path)
            assert os.path.exists(path)
            with open(path) as f:
                # Filter out comment lines starting with '#' or empty lines
                lines = [l for l in f if l.strip() and not l.startswith('#')]
            # Header + 3 data rows
            assert len(lines) == 4  # triangle has 3 edges + header
        finally:
            os.unlink(path)

    def test_csv_has_correct_columns(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_csv(graph, path)
            with open(path) as f:
                reader = csv.DictReader(f)
                row = next(reader)
            expected = {"source_index", "target_index", "source_x",
                        "source_y", "target_x", "target_y", "length"}
            assert set(row.keys()) == expected
        finally:
            os.unlink(path)

    def test_csv_includes_summary(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_csv(graph, path)
            content = open(path).read()
            assert "# Graph Summary" in content
            assert "# nodes:" in content
            assert "# edges:" in content
        finally:
            os.unlink(path)

    def test_csv_edge_lengths_positive(self, five_point_data, five_point_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            five_point_regions, five_point_data
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_csv(graph, path)
            with open(path) as f:
                # Skip comment lines
                data_lines = [l for l in f if l.strip() and not l.startswith('#')]
            import io
            reader = csv.DictReader(io.StringIO("".join(data_lines)))
            for row in reader:
                assert float(row["length"]) > 0
        finally:
            os.unlink(path)

    def test_empty_graph_raises(self):
        graph = {
            "adjacency": {}, "edges": [], "seed_indices": {},
            "num_nodes": 0, "num_edges": 0,
        }
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No graph data"):
                vormap_viz.export_graph_csv(graph, path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ── Stats table formatting tests ─────────────────────────────────────

class TestFormatGraphStatsTable:
    def test_returns_string(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        result = vormap_viz.format_graph_stats_table(graph)
        assert isinstance(result, str)

    def test_contains_key_metrics(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        result = vormap_viz.format_graph_stats_table(graph)
        assert "Nodes:" in result
        assert "Edges:" in result
        assert "Density:" in result
        assert "Clustering coeff:" in result
        assert "Degree distribution:" in result

    def test_shows_diameter_when_connected(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        result = vormap_viz.format_graph_stats_table(graph)
        assert "Diameter:" in result
        assert "Avg path length:" in result


# ── SVG overlay tests ────────────────────────────────────────────────

class TestExportGraphSvg:
    def test_creates_valid_svg(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path
            )
            assert os.path.exists(path)
            tree = ET.parse(path)
            root = tree.getroot()
            ns = "{http://www.w3.org/2000/svg}"
            assert root.tag == ns + "svg" or root.tag == "svg"
        finally:
            os.unlink(path)

    def test_contains_graph_edges(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path
            )
            content = open(path).read()
            assert "<line" in content
            # Should have 3 edges for triangle
            assert content.count('class="graph-edge"') == 3
        finally:
            os.unlink(path)

    def test_contains_graph_nodes(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path
            )
            content = open(path).read()
            assert content.count('class="graph-node"') == 3
        finally:
            os.unlink(path)

    def test_contains_voronoi_regions(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path
            )
            content = open(path).read()
            assert 'id="voronoi-regions"' in content
        finally:
            os.unlink(path)

    def test_no_voronoi_when_disabled(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path,
                show_voronoi=False
            )
            content = open(path).read()
            assert 'id="voronoi-regions"' not in content
        finally:
            os.unlink(path)

    def test_no_graph_when_disabled(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path,
                show_graph=False
            )
            content = open(path).read()
            assert 'class="graph-edge"' not in content
            assert 'class="graph-node"' not in content
        finally:
            os.unlink(path)

    def test_degree_labels(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path,
                show_degree_labels=True
            )
            content = open(path).read()
            assert 'class="degree-label"' in content
        finally:
            os.unlink(path)

    def test_title_in_svg(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path,
                title="Test Graph"
            )
            content = open(path).read()
            assert "Test Graph" in content
        finally:
            os.unlink(path)

    def test_custom_dimensions(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path,
                width=1200, height=900
            )
            tree = ET.parse(path)
            root = tree.getroot()
            assert root.get("width") == "1200"
            assert root.get("height") == "900"
        finally:
            os.unlink(path)

    def test_legend_present(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_graph_svg(
                triangle_regions, triangle_data, graph, path
            )
            content = open(path).read()
            assert 'class="legend"' in content
            assert "density" in content
        finally:
            os.unlink(path)

    def test_invalid_color_scheme_raises(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="Unknown color scheme"):
                vormap_viz.export_graph_svg(
                    triangle_regions, triangle_data, graph, path,
                    color_scheme="neon"
                )
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_all_color_schemes(self, triangle_data, triangle_regions):
        graph = vormap_viz.extract_neighborhood_graph(
            triangle_regions, triangle_data
        )
        for scheme in vormap_viz.list_color_schemes():
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
                path = f.name
            try:
                vormap_viz.export_graph_svg(
                    triangle_regions, triangle_data, graph, path,
                    color_scheme=scheme
                )
                assert os.path.exists(path)
                ET.parse(path)
            finally:
                os.unlink(path)


# ── One-call convenience function tests ──────────────────────────────

class TestGenerateGraph:
    def test_table_format(self, simple_data_dir):
        result = vormap_viz.generate_graph("test_graph.txt")
        assert isinstance(result, str)
        assert "Nodes:" in result

    def test_json_format_no_output(self, simple_data_dir):
        result = vormap_viz.generate_graph("test_graph.txt", fmt="json")
        assert isinstance(result, dict)
        assert "graph" in result
        assert "stats" in result

    def test_json_format_with_output(self, simple_data_dir):
        tmp, _ = simple_data_dir
        path = str(tmp / "graph.json")
        result = vormap_viz.generate_graph("test_graph.txt", path, fmt="json")
        assert result == path
        assert os.path.exists(path)

    def test_csv_format(self, simple_data_dir):
        tmp, _ = simple_data_dir
        path = str(tmp / "graph.csv")
        result = vormap_viz.generate_graph("test_graph.txt", path, fmt="csv")
        assert result == path
        assert os.path.exists(path)

    def test_csv_requires_output(self, simple_data_dir):
        with pytest.raises(ValueError, match="requires an output_path"):
            vormap_viz.generate_graph("test_graph.txt", fmt="csv")

    def test_svg_format(self, simple_data_dir):
        tmp, _ = simple_data_dir
        path = str(tmp / "graph.svg")
        result = vormap_viz.generate_graph("test_graph.txt", path, fmt="svg")
        assert result == path
        assert os.path.exists(path)

    def test_svg_requires_output(self, simple_data_dir):
        with pytest.raises(ValueError, match="requires an output_path"):
            vormap_viz.generate_graph("test_graph.txt", fmt="svg")


# ── Edge case tests ──────────────────────────────────────────────────

class TestGraphEdgeCases:
    def test_two_points(self):
        """Two points should form a single edge."""
        data = [(0.0, 0.0), (100.0, 100.0)]
        vormap.set_bounds(-50, 200, -50, 200)
        regions = vormap_viz.compute_regions(data)
        if len(regions) >= 2:
            graph = vormap_viz.extract_neighborhood_graph(regions, data)
            assert graph["num_edges"] >= 1

    def test_collinear_points(self):
        """Three collinear points should still produce a valid graph."""
        data = [(0.0, 0.0), (100.0, 0.0), (200.0, 0.0)]
        vormap.set_bounds(-50, 50, -50, 300)
        regions = vormap_viz.compute_regions(data)
        if len(regions) >= 2:
            graph = vormap_viz.extract_neighborhood_graph(regions, data)
            assert graph["num_nodes"] >= 2
            # Should have at least 1 edge
            assert graph["num_edges"] >= 1

    def test_graph_with_many_points(self):
        """Larger point sets should produce valid graphs."""
        import random
        random.seed(42)
        data = [(random.uniform(0, 1000), random.uniform(0, 1000))
                for _ in range(20)]
        vormap.set_bounds(-100, 1100, -100, 1100)
        regions = vormap_viz.compute_regions(data)
        graph = vormap_viz.extract_neighborhood_graph(regions, data)
        stats = vormap_viz.compute_graph_stats(graph)
        # Planar graph: E <= 3V - 6
        assert graph["num_edges"] <= 3 * graph["num_nodes"] - 6
        assert stats["is_connected"]
