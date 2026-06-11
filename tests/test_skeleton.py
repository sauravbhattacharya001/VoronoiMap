"""Tests for vormap_skeleton — Voronoi skeleton (medial-axis) extraction."""

import json
import math

import pytest

from vormap_skeleton import (
    Skeleton,
    SkeletonEdge,
    SkeletonPath,
    SkeletonMetrics,
    extract_skeleton,
    skeleton_metrics,
    export_skeleton_json,
    export_skeleton_csv,
    export_skeleton_svg,
    format_metrics_table,
    _snap,
    _clearance,
    _parse_point,
    _demo_regions,
    _regions_from_seeds,
    main,
    build_arg_parser,
    DEFAULT_TOL,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def two_cell_regions():
    """Two cells sharing a single vertical edge from (5,0) to (5,10).

    Seeds at (0,5) and (10,5).  Only the shared edge is internal; every
    other polygon edge belongs to just one cell.
    """
    left = [(0, 0), (5, 0), (5, 10), (0, 10)]
    right = [(5, 0), (10, 0), (10, 10), (5, 10)]
    return {(0.0, 5.0): left, (10.0, 5.0): right}


@pytest.fixture
def grid_regions():
    """A 3x3 grid of seeds → a skeleton with interior junctions."""
    seeds = [(x * 100.0, y * 100.0)
             for y in range(3) for x in range(3)]
    return _regions_from_seeds(seeds)


@pytest.fixture
def skel(grid_regions):
    return extract_skeleton(grid_regions)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_snap_rounds_to_grid(self):
        assert _snap(5.24, 0.5) == 5.0
        assert _snap(5.26, 0.5) == 5.5
        assert _snap(-0.1, 0.5) == 0.0

    def test_clearance_picks_nearest_seed(self):
        seeds = [(0, 0), (10, 0)]
        # midpoint (5,0) is equidistant -> 5.0
        assert _clearance(5, 0, seeds) == pytest.approx(5.0)
        assert _clearance(1, 0, seeds) == pytest.approx(1.0)

    def test_clearance_empty_seeds(self):
        assert _clearance(1, 1, []) == 0.0

    def test_parse_point(self):
        assert _parse_point("3,4") == (3.0, 4.0)
        assert _parse_point(" 3 , 4 ") == (3.0, 4.0)

    def test_parse_point_bad(self):
        with pytest.raises(Exception):
            _parse_point("3;4")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

class TestExtraction:
    def test_single_internal_edge(self, two_cell_regions):
        skel = extract_skeleton(two_cell_regions)
        # The shared edge contributes exactly one skeleton edge / two nodes.
        assert skel.num_edges == 1
        assert skel.num_nodes == 2
        e = skel.edges[0]
        assert e.length == pytest.approx(10.0)
        # Midpoint (5,5) is distance 5 from both seeds.
        assert e.clearance == pytest.approx(5.0)
        # Both seeds own the edge.
        assert len(e.seeds) == 2

    def test_boundary_edges_excluded(self, two_cell_regions):
        skel = extract_skeleton(two_cell_regions)
        # 8 polygon edges total, only 1 is shared -> 7 boundary edges dropped.
        assert skel.num_edges == 1

    def test_empty_regions(self):
        skel = extract_skeleton({})
        assert skel.num_nodes == 0
        assert skel.num_edges == 0

    def test_degenerate_polygon_ignored(self):
        # A single-vertex / two-vertex "polygon" has no usable edges.
        skel = extract_skeleton({(0.0, 0.0): [(1, 1)], (5.0, 5.0): []})
        assert skel.num_edges == 0

    def test_grid_has_internal_skeleton(self, skel):
        assert skel.num_edges > 0
        assert skel.num_nodes > 0
        # Seeds preserved for context.
        assert len(skel.seeds) == 9

    def test_edge_endpoints_ordered(self, skel):
        for e in skel.edges:
            assert e.a < e.b

    def test_edge_length_matches_nodes(self, skel):
        for e in skel.edges:
            ax, ay = skel.nodes[e.a]
            bx, by = skel.nodes[e.b]
            assert e.length == pytest.approx(math.hypot(bx - ax, by - ay))

    def test_custom_tolerance(self, grid_regions):
        # A very large tolerance collapses distinct vertices together,
        # never increasing the node count beyond the default extraction.
        coarse = extract_skeleton(grid_regions, tol=5.0)
        fine = extract_skeleton(grid_regions, tol=DEFAULT_TOL)
        assert coarse.num_nodes <= fine.num_nodes


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

class TestGraphStructure:
    def test_degree_sums_to_twice_edges(self, skel):
        deg = skel.degree()
        assert sum(deg) == 2 * skel.num_edges

    def test_adjacency_symmetric(self, skel):
        adj = skel.adjacency()
        for i, neighbours in enumerate(adj):
            for j, _ in neighbours:
                assert any(k == i for k, _ in adj[j])

    def test_nearest_node(self, two_cell_regions):
        skel = extract_skeleton(two_cell_regions)
        # Nodes are (5,0) and (5,10); a point near the bottom snaps to (5,0).
        idx = skel.nearest_node((4, 1))
        assert skel.nodes[idx][1] == pytest.approx(0.0)

    def test_nearest_node_empty_raises(self):
        skel = extract_skeleton({})
        with pytest.raises(ValueError):
            skel.nearest_node((0, 0))


# ---------------------------------------------------------------------------
# Shortest path
# ---------------------------------------------------------------------------

class TestShortestPath:
    def test_single_edge_path(self, two_cell_regions):
        skel = extract_skeleton(two_cell_regions)
        path = skel.shortest_path((4, 0), (4, 10))
        assert path.reachable
        assert len(path.nodes) == 2
        assert path.length == pytest.approx(10.0)
        assert path.min_clearance == pytest.approx(5.0)
        # Coords align with node indices.
        assert path.coords[0] == skel.nodes[path.nodes[0]]

    def test_path_same_point(self, two_cell_regions):
        skel = extract_skeleton(two_cell_regions)
        path = skel.shortest_path((4, 0), (4, 0))
        assert path.reachable
        assert path.length == pytest.approx(0.0)

    def test_grid_path_reachable(self, skel):
        # Opposite corners of the grid skeleton should be connected.
        path = skel.shortest_path((10, 10), (190, 190))
        assert path.reachable
        assert path.length > 0

    def test_disconnected_returns_unreachable(self):
        # Two completely separate single-edge skeletons.
        nodes = [(0, 0), (0, 10), (100, 0), (100, 10)]
        edges = [
            SkeletonEdge(0, 1, 10.0, 5.0, (0, 1)),
            SkeletonEdge(2, 3, 10.0, 5.0, (2, 3)),
        ]
        s = Skeleton(nodes=nodes, edges=edges, seeds=[(0, 5), (100, 5)])
        path = s.shortest_path((0, 0), (100, 10))
        assert not path.reachable
        assert path.length == 0.0
        assert path.nodes == []

    def test_path_empty_skeleton_raises(self):
        skel = extract_skeleton({})
        with pytest.raises(ValueError):
            skel.shortest_path((0, 0), (1, 1))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_metrics_single_edge(self, two_cell_regions):
        m = skeleton_metrics(extract_skeleton(two_cell_regions))
        assert m.num_nodes == 2
        assert m.num_edges == 1
        assert m.total_length == pytest.approx(10.0)
        assert m.num_endpoints == 2      # both degree-1
        assert m.num_junctions == 0
        assert m.num_isolated == 0
        assert m.longest_corridor == pytest.approx(10.0)
        assert m.mean_clearance == pytest.approx(5.0)

    def test_metrics_empty(self):
        m = skeleton_metrics(extract_skeleton({}))
        assert m.num_nodes == 0
        assert m.total_length == 0.0
        assert m.mean_clearance == 0.0
        assert m.longest_corridor == 0.0

    def test_metrics_grid_has_junctions(self, skel):
        m = skeleton_metrics(skel)
        assert m.num_junctions >= 1
        assert m.num_branches >= 1
        assert m.min_clearance <= m.mean_clearance <= m.max_clearance

    def test_metrics_as_dict_roundtrip(self, skel):
        d = skeleton_metrics(skel).as_dict()
        assert d["num_nodes"] == skel.num_nodes
        assert d["num_edges"] == skel.num_edges
        assert "total_length" in d


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

class TestExporters:
    def test_json_export(self, skel, tmp_path):
        out = tmp_path / "skel.json"
        export_skeleton_json(skel, str(out))
        data = json.loads(out.read_text(encoding="utf-8"))
        assert len(data["nodes"]) == skel.num_nodes
        assert len(data["edges"]) == skel.num_edges
        assert data["metrics"]["num_edges"] == skel.num_edges
        # Edge schema.
        if data["edges"]:
            e = data["edges"][0]
            assert {"a", "b", "length", "clearance", "seeds"} <= set(e)

    def test_csv_export(self, skel, tmp_path):
        out = tmp_path / "skel.csv"
        export_skeleton_csv(skel, str(out))
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert lines[0] == "ax,ay,bx,by,length,clearance,seeds"
        assert len(lines) == skel.num_edges + 1

    def test_svg_export(self, skel, tmp_path):
        out = tmp_path / "skel.svg"
        export_skeleton_svg(skel, str(out))
        text = out.read_text(encoding="utf-8")
        assert text.startswith("<?xml")
        assert "<svg" in text and "</svg>" in text

    def test_svg_with_highlight_path(self, two_cell_regions, tmp_path):
        skel = extract_skeleton(two_cell_regions)
        path = skel.shortest_path((4, 0), (4, 10))
        out = tmp_path / "skel_path.svg"
        export_skeleton_svg(skel, str(out), highlight_path=path)
        text = out.read_text(encoding="utf-8")
        assert "<polyline" in text  # the highlighted route

    def test_format_metrics_table(self, skel):
        table = format_metrics_table(skel)
        assert "Voronoi Skeleton" in table
        assert "Total length" in table
        assert "Junctions" in table


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_demo_text(self, capsys):
        rc = main(["--demo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Voronoi Skeleton" in out

    def test_demo_json(self, capsys):
        rc = main(["--demo", "--format", "json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert "metrics" in payload
        assert payload["metrics"]["num_edges"] >= 0

    def test_demo_with_path(self, capsys):
        rc = main(["--demo", "--path", "100,100", "500,400"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Route:" in out

    def test_demo_path_json(self, capsys):
        rc = main(["--demo", "--format", "json", "--path", "100,100", "500,400"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert "path" in payload
        assert "reachable" in payload["path"]

    def test_demo_exports(self, capsys, tmp_path):
        j = tmp_path / "s.json"
        c = tmp_path / "s.csv"
        v = tmp_path / "s.svg"
        rc = main(["--demo", "--json", str(j), "--csv", str(c), "--svg", str(v)])
        assert rc == 0
        assert j.exists() and c.exists() and v.exists()

    def test_missing_datafile_errors(self):
        # argparse calls parser.error -> SystemExit
        with pytest.raises(SystemExit):
            main([])

    def test_bad_datafile_returns_error_code(self, capsys):
        rc = main(["/nonexistent/does-not-exist.txt"])
        assert rc == 2
        assert "error:" in capsys.readouterr().err

    def test_arg_parser_builds(self):
        p = build_arg_parser()
        ns = p.parse_args(["--demo", "--tol", "0.25"])
        assert ns.demo is True
        assert ns.tol == 0.25


# ---------------------------------------------------------------------------
# Demo regions
# ---------------------------------------------------------------------------

class TestDemoRegions:
    def test_demo_regions_nonempty(self):
        regions = _demo_regions()
        assert len(regions) == 12  # 4x3 grid
        skel = extract_skeleton(regions)
        assert skel.num_edges > 0
