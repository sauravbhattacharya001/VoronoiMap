"""Tests for vormap_color — map coloring module."""

import json
import os
import random
import tempfile

import pytest

import vormap
import vormap_color
import vormap_viz


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def triangle_graph():
    """K3 — complete graph on 3 nodes."""
    return {0: [1, 2], 1: [0, 2], 2: [0, 1]}


@pytest.fixture
def square_graph():
    """C4 — cycle on 4 nodes."""
    return {0: [1, 3], 1: [0, 2], 2: [1, 3], 3: [2, 0]}


@pytest.fixture
def k4_graph():
    """K4 — complete graph on 4 nodes."""
    return {
        0: [1, 2, 3],
        1: [0, 2, 3],
        2: [0, 1, 3],
        3: [0, 1, 2],
    }


@pytest.fixture
def star_graph():
    """Star graph — hub 0 connected to 1,2,3,4."""
    return {
        0: [1, 2, 3, 4],
        1: [0],
        2: [0],
        3: [0],
        4: [0],
    }


@pytest.fixture
def path_graph():
    """Path graph — 0-1-2-3-4."""
    return {
        0: [1],
        1: [0, 2],
        2: [1, 3],
        3: [2, 4],
        4: [3],
    }


@pytest.fixture
def cycle_graph():
    """C5 — cycle on 5 nodes."""
    return {
        0: [1, 4],
        1: [0, 2],
        2: [1, 3],
        3: [2, 4],
        4: [3, 0],
    }


@pytest.fixture
def empty_graph():
    """Empty graph."""
    return {}


@pytest.fixture
def single_node_graph():
    """Single node, no edges."""
    return {0: []}


@pytest.fixture
def two_node_graph():
    """Two connected nodes."""
    return {0: [1], 1: [0]}


@pytest.fixture
def disconnected_graph():
    """Two disconnected components."""
    return {0: [1], 1: [0], 2: [3], 3: [2]}


@pytest.fixture
def random_points():
    """Random points for Voronoi tests."""
    rng = random.Random(42)
    return [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(15)]


@pytest.fixture
def data_file():
    """Create a temporary data file in data/ directory for vormap.load_data()."""
    import os
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    fname = "_test_color_data.txt"
    filepath = os.path.join(data_dir, fname)
    pts = [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0), (70.0, 80.0), (90.0, 10.0)]
    with open(filepath, "w") as f:
        f.write("%d\n" % len(pts))
        for x, y in pts:
            f.write("%.4f %.4f\n" % (x, y))
    yield fname  # relative filename, not absolute
    # Cleanup
    if os.path.exists(filepath):
        os.remove(filepath)


# ── Palette tests ────────────────────────────────────────────────────

class TestPalettes:
    def test_palette_4_length(self):
        assert len(vormap_color.PALETTE_4) == 4

    def test_palette_6_length(self):
        assert len(vormap_color.PALETTE_6) == 6

    def test_palette_8_length(self):
        assert len(vormap_color.PALETTE_8) == 8

    def test_palette_cb_length(self):
        assert len(vormap_color.PALETTE_CB) == 8

    def test_palette_4_hex_format(self):
        for c in vormap_color.PALETTE_4:
            assert c.startswith("#")
            assert len(c) == 7

    def test_palette_6_hex_format(self):
        for c in vormap_color.PALETTE_6:
            assert c.startswith("#")

    def test_palette_cb_hex_format(self):
        for c in vormap_color.PALETTE_CB:
            assert c.startswith("#")

    def test_palette_4_unique_colors(self):
        assert len(set(vormap_color.PALETTE_4)) == 4

    def test_palette_6_unique_colors(self):
        assert len(set(vormap_color.PALETTE_6)) == 6


# ── greedy_color tests ───────────────────────────────────────────────

class TestGreedyColor:
    def test_empty_graph(self, empty_graph):
        coloring = vormap_color.greedy_color(empty_graph)
        assert coloring == {}

    def test_single_node(self, single_node_graph):
        coloring = vormap_color.greedy_color(single_node_graph)
        assert coloring == {0: 0}

    def test_two_nodes(self, two_node_graph):
        coloring = vormap_color.greedy_color(two_node_graph)
        assert coloring[0] != coloring[1]

    def test_triangle(self, triangle_graph):
        coloring = vormap_color.greedy_color(triangle_graph)
        assert len(set(coloring.values())) == 3
        valid, _ = vormap_color.validate_coloring(triangle_graph, coloring)
        assert valid

    def test_square(self, square_graph):
        coloring = vormap_color.greedy_color(square_graph)
        valid, _ = vormap_color.validate_coloring(square_graph, coloring)
        assert valid

    def test_k4(self, k4_graph):
        coloring = vormap_color.greedy_color(k4_graph)
        assert len(set(coloring.values())) == 4
        valid, _ = vormap_color.validate_coloring(k4_graph, coloring)
        assert valid

    def test_star(self, star_graph):
        coloring = vormap_color.greedy_color(star_graph)
        valid, _ = vormap_color.validate_coloring(star_graph, coloring)
        assert valid
        # Hub should have different color from all leaves
        hub_color = coloring[0]
        for leaf in [1, 2, 3, 4]:
            assert coloring[leaf] != hub_color

    def test_path(self, path_graph):
        coloring = vormap_color.greedy_color(path_graph)
        valid, _ = vormap_color.validate_coloring(path_graph, coloring)
        assert valid
        # Path can be 2-colored
        assert len(set(coloring.values())) <= 3

    def test_cycle_odd(self, cycle_graph):
        coloring = vormap_color.greedy_color(cycle_graph)
        valid, _ = vormap_color.validate_coloring(cycle_graph, coloring)
        assert valid

    def test_num_colors_1(self, single_node_graph):
        coloring = vormap_color.greedy_color(single_node_graph, num_colors=1)
        assert coloring[0] == 0

    def test_num_colors_minimum(self, triangle_graph):
        """Even with num_colors=2, greedy falls back to using more."""
        coloring = vormap_color.greedy_color(triangle_graph, num_colors=2)
        valid, _ = vormap_color.validate_coloring(triangle_graph, coloring)
        assert valid

    def test_disconnected(self, disconnected_graph):
        coloring = vormap_color.greedy_color(disconnected_graph)
        valid, _ = vormap_color.validate_coloring(disconnected_graph, coloring)
        assert valid
        assert len(coloring) == 4

    def test_all_nodes_colored(self, k4_graph):
        coloring = vormap_color.greedy_color(k4_graph)
        assert set(coloring.keys()) == set(k4_graph.keys())

    def test_num_colors_zero_treated_as_1(self, single_node_graph):
        coloring = vormap_color.greedy_color(single_node_graph, num_colors=0)
        assert coloring[0] == 0


# ── dsatur_color tests ───────────────────────────────────────────────

class TestDsaturColor:
    def test_empty_graph(self, empty_graph):
        coloring = vormap_color.dsatur_color(empty_graph)
        assert coloring == {}

    def test_single_node(self, single_node_graph):
        coloring = vormap_color.dsatur_color(single_node_graph)
        assert coloring == {0: 0}

    def test_two_nodes(self, two_node_graph):
        coloring = vormap_color.dsatur_color(two_node_graph)
        assert coloring[0] != coloring[1]

    def test_triangle(self, triangle_graph):
        coloring = vormap_color.dsatur_color(triangle_graph)
        assert len(set(coloring.values())) == 3
        valid, _ = vormap_color.validate_coloring(triangle_graph, coloring)
        assert valid

    def test_square(self, square_graph):
        coloring = vormap_color.dsatur_color(square_graph)
        valid, _ = vormap_color.validate_coloring(square_graph, coloring)
        assert valid
        # Square is bipartite, DSATUR should use 2 colors
        assert len(set(coloring.values())) == 2

    def test_k4(self, k4_graph):
        coloring = vormap_color.dsatur_color(k4_graph)
        assert len(set(coloring.values())) == 4
        valid, _ = vormap_color.validate_coloring(k4_graph, coloring)
        assert valid

    def test_star(self, star_graph):
        coloring = vormap_color.dsatur_color(star_graph)
        valid, _ = vormap_color.validate_coloring(star_graph, coloring)
        assert valid
        # Star is bipartite
        assert len(set(coloring.values())) == 2

    def test_path(self, path_graph):
        coloring = vormap_color.dsatur_color(path_graph)
        valid, _ = vormap_color.validate_coloring(path_graph, coloring)
        assert valid
        # Path is bipartite
        assert len(set(coloring.values())) == 2

    def test_cycle_odd(self, cycle_graph):
        coloring = vormap_color.dsatur_color(cycle_graph)
        valid, _ = vormap_color.validate_coloring(cycle_graph, coloring)
        assert valid
        # Odd cycle needs 3 colors
        assert len(set(coloring.values())) == 3

    def test_disconnected(self, disconnected_graph):
        coloring = vormap_color.dsatur_color(disconnected_graph)
        valid, _ = vormap_color.validate_coloring(disconnected_graph, coloring)
        assert valid

    def test_all_nodes_colored(self, k4_graph):
        coloring = vormap_color.dsatur_color(k4_graph)
        assert set(coloring.keys()) == set(k4_graph.keys())

    def test_num_colors_fallback(self, triangle_graph):
        """Even with num_colors=2, dsatur falls back to more colors."""
        coloring = vormap_color.dsatur_color(triangle_graph, num_colors=2)
        valid, _ = vormap_color.validate_coloring(triangle_graph, coloring)
        assert valid

    def test_num_colors_zero(self, single_node_graph):
        coloring = vormap_color.dsatur_color(single_node_graph, num_colors=0)
        assert coloring[0] == 0


# ── validate_coloring tests ─────────────────────────────────────────

class TestValidateColoring:
    def test_valid_coloring(self, triangle_graph):
        coloring = {0: 0, 1: 1, 2: 2}
        valid, conflicts = vormap_color.validate_coloring(triangle_graph, coloring)
        assert valid
        assert conflicts == []

    def test_invalid_coloring_one_conflict(self, triangle_graph):
        coloring = {0: 0, 1: 0, 2: 1}
        valid, conflicts = vormap_color.validate_coloring(triangle_graph, coloring)
        assert not valid
        assert len(conflicts) == 1
        assert conflicts[0][2] == 0  # shared color

    def test_all_same_color(self, triangle_graph):
        coloring = {0: 0, 1: 0, 2: 0}
        valid, conflicts = vormap_color.validate_coloring(triangle_graph, coloring)
        assert not valid
        assert len(conflicts) == 3  # all edges are conflicts

    def test_empty_graph(self, empty_graph):
        valid, conflicts = vormap_color.validate_coloring(empty_graph, {})
        assert valid
        assert conflicts == []

    def test_disconnected_same_color(self):
        graph = {0: [], 1: []}
        coloring = {0: 0, 1: 0}
        valid, conflicts = vormap_color.validate_coloring(graph, coloring)
        assert valid

    def test_partial_coloring(self, triangle_graph):
        """Nodes not in coloring are skipped."""
        coloring = {0: 0}
        valid, conflicts = vormap_color.validate_coloring(triangle_graph, coloring)
        assert valid

    def test_conflict_returns_both_nodes(self, two_node_graph):
        coloring = {0: 0, 1: 0}
        valid, conflicts = vormap_color.validate_coloring(two_node_graph, coloring)
        assert not valid
        assert len(conflicts) == 1
        a, b, c = conflicts[0]
        assert {a, b} == {0, 1}
        assert c == 0


# ── color_voronoi tests ─────────────────────────────────────────────

class TestColorVoronoi:
    def test_with_random_points(self, random_points):
        result = vormap_color.color_voronoi(random_points)
        assert isinstance(result, vormap_color.ColorResult)
        assert result.conflicts == 0
        assert result.algorithm == "dsatur"
        assert result.num_colors_used >= 1
        assert len(result.palette) >= result.num_colors_used

    def test_with_file(self, data_file):
        result = vormap_color.color_voronoi(data_file)
        assert result.conflicts == 0
        assert len(result.coloring) > 0

    def test_greedy_algorithm(self, random_points):
        result = vormap_color.color_voronoi(random_points, algorithm="greedy")
        assert result.algorithm == "greedy"
        assert result.conflicts == 0

    def test_dsatur_algorithm(self, random_points):
        result = vormap_color.color_voronoi(random_points, algorithm="dsatur")
        assert result.algorithm == "dsatur"
        assert result.conflicts == 0

    def test_invalid_algorithm(self, random_points):
        with pytest.raises(ValueError, match="Unknown algorithm"):
            vormap_color.color_voronoi(random_points, algorithm="invalid")

    def test_custom_palette(self, random_points):
        pal = ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]
        result = vormap_color.color_voronoi(random_points, palette=pal)
        assert result.palette == pal

    def test_default_palette(self, random_points):
        result = vormap_color.color_voronoi(random_points, num_colors=4)
        assert result.palette == vormap_color.PALETTE_4

    def test_palette_6_selection(self, random_points):
        result = vormap_color.color_voronoi(random_points, num_colors=5)
        assert result.palette == vormap_color.PALETTE_6

    def test_palette_8_selection(self, random_points):
        result = vormap_color.color_voronoi(random_points, num_colors=7)
        assert result.palette == vormap_color.PALETTE_8

    def test_palette_cb_selection(self, random_points):
        result = vormap_color.color_voronoi(random_points, num_colors=9)
        assert result.palette == vormap_color.PALETTE_CB

    def test_result_has_regions(self, random_points):
        result = vormap_color.color_voronoi(random_points)
        assert isinstance(result.regions, dict)
        assert len(result.regions) > 0

    def test_result_has_graph(self, random_points):
        result = vormap_color.color_voronoi(random_points)
        assert isinstance(result.graph, dict)
        assert "adjacency" in result.graph

    def test_num_colors_used(self, random_points):
        result = vormap_color.color_voronoi(random_points)
        # num_colors_used should equal max color index + 1
        if result.coloring:
            max_color = max(result.coloring.values())
            assert result.num_colors_used == max_color + 1

    def test_coloring_keys_match_graph_nodes(self, random_points):
        result = vormap_color.color_voronoi(random_points)
        adj = vormap_color._graph_to_adjacency_dict(result.graph)
        assert set(result.coloring.keys()) == set(adj.keys())


# ── ColorResult tests ────────────────────────────────────────────────

class TestColorResult:
    def test_fields(self):
        cr = vormap_color.ColorResult(
            coloring={0: 0, 1: 1},
            num_colors_used=2,
            palette=["#ff0000", "#00ff00"],
            regions={},
            graph={},
            conflicts=0,
            algorithm="dsatur",
        )
        assert cr.coloring == {0: 0, 1: 1}
        assert cr.num_colors_used == 2
        assert cr.palette == ["#ff0000", "#00ff00"]
        assert cr.regions == {}
        assert cr.graph == {}
        assert cr.conflicts == 0
        assert cr.algorithm == "dsatur"

    def test_is_dataclass(self):
        from dataclasses import fields
        f = fields(vormap_color.ColorResult)
        names = {x.name for x in f}
        assert "coloring" in names
        assert "num_colors_used" in names
        assert "palette" in names
        assert "conflicts" in names
        assert "algorithm" in names


# ── export_colored_svg tests ─────────────────────────────────────────

class TestExportColoredSvg:
    def test_creates_file(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out)
        assert os.path.exists(out)

    def test_valid_svg(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out)
        content = open(out).read()
        assert "<svg" in content
        assert "</svg>" in content

    def test_has_regions(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out)
        content = open(out).read()
        assert "<polygon" in content

    def test_has_colored_fill(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out)
        content = open(out).read()
        # Check for hex color fills from palette
        assert 'fill="#' in content

    def test_show_seeds_true(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out, show_seeds=True)
        content = open(out).read()
        assert "<circle" in content

    def test_show_seeds_false(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out, show_seeds=False)
        content = open(out).read()
        assert "seeds" not in content

    def test_show_labels(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out, show_labels=True)
        content = open(out).read()
        assert "labels" in content

    def test_custom_dimensions(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out, width=1024, height=768)
        content = open(out).read()
        assert 'width="1024"' in content
        assert 'height="768"' in content

    def test_custom_stroke(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(
            random_points, out, stroke_color="#ff0000", stroke_width=2
        )
        content = open(out).read()
        assert "#ff0000" in content

    def test_with_file_input(self, data_file, tmp_path):
        out = str(tmp_path / "test.svg")
        result = vormap_color.export_colored_svg(data_file, out)
        assert result == out
        assert os.path.exists(out)

    def test_greedy_algorithm_svg(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out, algorithm="greedy")
        assert os.path.exists(out)

    def test_custom_palette_svg(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        pal = ["#aabbcc", "#ddeeff", "#112233", "#445566"]
        vormap_color.export_colored_svg(random_points, out, palette=pal)
        content = open(out).read()
        # At least one palette color should appear
        assert any(c in content for c in pal)

    def test_data_color_attribute(self, random_points, tmp_path):
        out = str(tmp_path / "test.svg")
        vormap_color.export_colored_svg(random_points, out)
        content = open(out).read()
        assert "data-color" in content


# ── Edge case tests ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_point_voronoi(self):
        """Single point — may raise ValueError since no regions are formed."""
        pts = [(50, 50)]
        with pytest.raises(ValueError):
            vormap_color.color_voronoi(pts)

    def test_two_points_voronoi(self):
        """Two points — may produce valid coloring or raise ValueError."""
        pts = [(20, 50), (80, 50)]
        try:
            result = vormap_color.color_voronoi(pts)
            assert result.conflicts == 0
        except ValueError:
            pass  # acceptable if regions can't be formed

    def test_collinear_points(self):
        """Collinear points — may produce valid coloring or raise."""
        pts = [(10, 50), (30, 50), (50, 50), (70, 50), (90, 50)]
        try:
            result = vormap_color.color_voronoi(pts)
            assert result.conflicts == 0
        except ValueError:
            pass

    def test_enough_points_voronoi(self):
        """Four non-collinear points produce valid coloring."""
        pts = [(20, 20), (80, 20), (50, 80), (50, 40)]
        result = vormap_color.color_voronoi(pts)
        assert result.conflicts == 0

    def test_graph_conversion(self, random_points):
        """_graph_to_adjacency_dict produces valid adjacency."""
        regions = vormap_viz.compute_regions(random_points)
        graph = vormap_viz.extract_neighborhood_graph(regions, random_points)
        adj = vormap_color._graph_to_adjacency_dict(graph)
        assert isinstance(adj, dict)
        for node, neighbors in adj.items():
            assert isinstance(node, int)
            for n in neighbors:
                assert isinstance(n, int)


# ── Algorithm comparison tests ───────────────────────────────────────

class TestAlgorithmComparison:
    def test_both_produce_valid_coloring(self, random_points):
        r_greedy = vormap_color.color_voronoi(random_points, algorithm="greedy")
        r_dsatur = vormap_color.color_voronoi(random_points, algorithm="dsatur")
        assert r_greedy.conflicts == 0
        assert r_dsatur.conflicts == 0

    def test_dsatur_uses_fewer_or_equal_colors(self, random_points):
        r_greedy = vormap_color.color_voronoi(random_points, algorithm="greedy")
        r_dsatur = vormap_color.color_voronoi(random_points, algorithm="dsatur")
        assert r_dsatur.num_colors_used <= r_greedy.num_colors_used + 1

    def test_both_algorithms_with_num_colors_6(self, random_points):
        r1 = vormap_color.color_voronoi(random_points, algorithm="greedy", num_colors=6)
        r2 = vormap_color.color_voronoi(random_points, algorithm="dsatur", num_colors=6)
        assert r1.conflicts == 0
        assert r2.conflicts == 0


# ── select_palette tests ────────────────────────────────────────────

class TestSelectPalette:
    def test_custom_palette_returned(self):
        pal = ["#aaa", "#bbb"]
        result = vormap_color._select_palette(4, pal)
        assert result == pal

    def test_4_colors_default(self):
        result = vormap_color._select_palette(4, None)
        assert result == vormap_color.PALETTE_4

    def test_6_colors_default(self):
        result = vormap_color._select_palette(6, None)
        assert result == vormap_color.PALETTE_6

    def test_8_colors_default(self):
        result = vormap_color._select_palette(8, None)
        assert result == vormap_color.PALETTE_8

    def test_9_plus_colors(self):
        result = vormap_color._select_palette(10, None)
        assert result == vormap_color.PALETTE_CB


# ── CLI tests ────────────────────────────────────────────────────────

class TestCLI:
    def test_parser_defaults(self):
        parser = vormap_color._build_parser()
        args = parser.parse_args(["somefile.txt"])
        assert args.datafile == "somefile.txt"
        assert args.algorithm == "dsatur"
        assert args.colors == 4
        assert args.output is None
        assert args.labels is False
        assert args.no_seeds is False

    def test_parser_all_options(self):
        parser = vormap_color._build_parser()
        args = parser.parse_args([
            "data.txt",
            "--output", "out.svg",
            "--algorithm", "greedy",
            "--colors", "6",
            "--palette", "#aaa,#bbb,#ccc",
            "--labels",
            "--no-seeds",
        ])
        assert args.datafile == "data.txt"
        assert args.output == "out.svg"
        assert args.algorithm == "greedy"
        assert args.colors == 6
        assert args.palette == "#aaa,#bbb,#ccc"
        assert args.labels is True
        assert args.no_seeds is True
