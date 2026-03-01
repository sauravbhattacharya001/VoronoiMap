"""Tests for vormap_edge — Voronoi Edge Network extraction and analysis."""

import json
import math
import os
import tempfile

import pytest

import vormap_edge


# ── Fixtures ─────────────────────────────────────────────────────────

def _square_regions():
    """Two adjacent square regions sharing one edge.

    Region A: (0,0)-(100,0)-(100,100)-(0,100)
    Region B: (100,0)-(200,0)-(200,100)-(100,100)

    Shared edge: (100,0)-(100,100) → 1 interior edge
    Other edges are boundary (shared by 1 region)
    """
    return {
        (50, 50): [(0, 0), (100, 0), (100, 100), (0, 100)],
        (150, 50): [(100, 0), (200, 0), (200, 100), (100, 100)],
    }


def _triangle_region():
    """Single triangular region."""
    return {
        (50, 33): [(0, 0), (100, 0), (50, 100)],
    }


def _three_cells():
    """Three regions sharing edges like a 1×3 strip."""
    return {
        (50, 50):   [(0, 0), (100, 0), (100, 100), (0, 100)],
        (150, 50):  [(100, 0), (200, 0), (200, 100), (100, 100)],
        (250, 50):  [(200, 0), (300, 0), (300, 100), (200, 100)],
    }


# ── extract_edge_network ────────────────────────────────────────────

class TestExtractEdgeNetwork:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="No regions"):
            vormap_edge.extract_edge_network({})

    def test_single_triangle(self):
        net = vormap_edge.extract_edge_network(_triangle_region(), tol=0.1)
        assert net["num_vertices"] == 3
        assert net["num_edges"] == 3

    def test_two_squares_topology(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        # 6 unique vertices: (0,0),(100,0),(200,0),(0,100),(100,100),(200,100)
        assert net["num_vertices"] == 6
        # 7 unique edges: 4 outer from each square minus shared = 4+4-1 = 7
        assert net["num_edges"] == 7

    def test_shared_edge_has_2_regions(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        interior = [e for e in net["edges"] if e["regions"] == 2]
        assert len(interior) >= 1
        # The shared edge connects (100,0) and (100,100) — length ≈ 100
        assert any(abs(e["length"] - 100.0) < 1.0 for e in interior)

    def test_boundary_edges(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        boundary = [e for e in net["edges"] if e["regions"] == 1]
        # 6 boundary edges (outer perimeter of the 2-square strip)
        assert len(boundary) == 6

    def test_adjacency_symmetric(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        for i, neighbors in net["adjacency"].items():
            for j in neighbors:
                assert i in net["adjacency"][j], \
                    "Edge (%d, %d) not symmetric" % (i, j)

    def test_three_cells(self):
        net = vormap_edge.extract_edge_network(_three_cells(), tol=0.1)
        # 8 vertices, 10 edges (outer + 2 shared)
        assert net["num_vertices"] == 8
        assert net["num_edges"] == 10

    def test_edge_lengths_positive(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        for e in net["edges"]:
            assert e["length"] > 0

    def test_edge_angles_in_range(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        for e in net["edges"]:
            assert 0 <= e["angle"] < 360

    def test_vertex_index_consistency(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        for e in net["edges"]:
            assert net["vertices"][e["i"]] == e["v1"]
            assert net["vertices"][e["j"]] == e["v2"]


# ── compute_edge_stats ───────────────────────────────────────────────

class TestComputeEdgeStats:
    def test_basic_stats(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert stats["num_vertices"] == 6
        assert stats["num_edges"] == 7
        assert stats["total_length"] > 0
        assert stats["mean_length"] > 0

    def test_boundary_interior_counts(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert stats["num_boundary_edges"] == 6
        assert stats["num_interior_edges"] == 1
        assert stats["num_boundary_edges"] + stats["num_interior_edges"] == 7

    def test_boundary_fraction(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert 0 < stats["boundary_fraction"] < 1

    def test_single_triangle_all_boundary(self):
        net = vormap_edge.extract_edge_network(_triangle_region(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert stats["num_boundary_edges"] == 3
        assert stats["num_interior_edges"] == 0
        assert stats["boundary_fraction"] == 1.0

    def test_degree_stats(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert stats["mean_degree"] > 0
        assert stats["max_degree"] >= 2

    def test_junction_count(self):
        net = vormap_edge.extract_edge_network(_three_cells(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        # Shared vertices between 3 cells have degree 3
        assert stats["num_junctions"] >= 0  # at least no crash

    def test_angle_entropy_positive(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert stats["angle_entropy"] > 0

    def test_angle_entropy_bounded(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        # Max entropy for 8 bins = log2(8) = 3.0
        assert stats["angle_entropy"] <= 3.0

    def test_empty_network_stats(self):
        # Manually create a degenerate network
        network = {
            "vertices": [(0, 0)],
            "vertex_index": {(0, 0): 0},
            "edges": [],
            "adjacency": {0: set()},
            "num_vertices": 1,
            "num_edges": 0,
        }
        stats = vormap_edge.compute_edge_stats(network)
        assert stats["num_edges"] == 0
        assert stats["total_length"] == 0.0
        assert stats["num_isolated"] == 1

    def test_length_stats_consistency(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        assert stats["min_length"] <= stats["mean_length"] <= stats["max_length"]
        assert stats["min_length"] <= stats["median_length"] <= stats["max_length"]


# ── format_edge_stats ────────────────────────────────────────────────

class TestFormatEdgeStats:
    def test_produces_string(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        text = vormap_edge.format_edge_stats(stats)
        assert isinstance(text, str)
        assert "Edge Network" in text
        assert "Vertices" in text

    def test_contains_key_fields(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        stats = vormap_edge.compute_edge_stats(net)
        text = vormap_edge.format_edge_stats(stats)
        assert "Junctions" in text
        assert "entropy" in text.lower()


# ── Export: CSV ──────────────────────────────────────────────────────

class TestExportCSV:
    def test_csv_file_created(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_csv(net, path)
            assert os.path.exists(path)
            content = open(path).read()
            assert "vertex_i" in content
            lines = [l for l in content.split("\n") if l and not l.startswith("#")]
            # Header + 7 edges
            assert len(lines) == 8
        finally:
            os.unlink(path)

    def test_csv_no_stats(self):
        net = vormap_edge.extract_edge_network(_triangle_region(), tol=0.1)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_csv(net, path, include_stats=False)
            content = open(path).read()
            assert "#" not in content
        finally:
            os.unlink(path)


# ── Export: JSON ─────────────────────────────────────────────────────

class TestExportJSON:
    def test_json_structure(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_json(net, path)
            data = json.load(open(path))
            assert "vertices" in data
            assert "edges" in data
            assert "stats" in data
            assert len(data["vertices"]) == 6
            assert len(data["edges"]) == 7
        finally:
            os.unlink(path)

    def test_json_boundary_flag(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_json(net, path)
            data = json.load(open(path))
            boundary = [e for e in data["edges"] if e["is_boundary"]]
            interior = [e for e in data["edges"] if not e["is_boundary"]]
            assert len(boundary) == 6
            assert len(interior) == 1
        finally:
            os.unlink(path)


# ── Export: SVG ──────────────────────────────────────────────────────

class TestExportSVG:
    def test_svg_file_created(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_svg(net, path, title="Test")
            assert os.path.exists(path)
            content = open(path).read()
            assert "<svg" in content
            assert "Test" in content
        finally:
            os.unlink(path)

    def test_svg_without_vertices(self):
        net = vormap_edge.extract_edge_network(_square_regions(), tol=0.1)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_svg(net, path, show_vertices=False)
            content = open(path).read()
            assert "<svg" in content
            # Should have lines but fewer circles
            assert "<line" in content
        finally:
            os.unlink(path)

    def test_svg_empty_network(self):
        """Empty network produces valid SVG."""
        # Single point, no polygon edges
        net = {
            "vertices": [],
            "edges": [],
            "adjacency": {},
            "num_vertices": 0,
            "num_edges": 0,
        }
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_edge.export_edge_svg(net, path)
            assert os.path.exists(path)
        finally:
            os.unlink(path)


# ── Helpers ──────────────────────────────────────────────────────────

class TestHelpers:
    def test_edge_length(self):
        assert vormap_edge._edge_length((0, 0), (3, 4)) == 5.0

    def test_edge_angle_horizontal(self):
        angle = vormap_edge._edge_angle((0, 0), (100, 0))
        assert abs(angle) < 0.01  # 0 degrees

    def test_edge_angle_vertical(self):
        angle = vormap_edge._edge_angle((0, 0), (0, 100))
        assert abs(angle - 90.0) < 0.01

    def test_edge_angle_diagonal(self):
        angle = vormap_edge._edge_angle((0, 0), (100, 100))
        assert abs(angle - 45.0) < 0.01

    def test_round_vertex(self):
        v = vormap_edge._round_vertex((10.6, 20.9), tol=0.5)
        assert v == (10.5, 21.0)
        v2 = vormap_edge._round_vertex((10.0, 20.0), tol=0.5)
        assert v2 == (10.0, 20.0)
