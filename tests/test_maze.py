"""Tests for vormap_maze — Voronoi maze generator."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import vormap
import vormap_viz
import vormap_maze


def _make_regions(n=50, seed=42):
    """Generate Voronoi regions for testing."""
    import random
    rng = random.Random(seed)
    points = [(rng.uniform(50, 1950), rng.uniform(50, 950)) for _ in range(n)]
    vormap.IND_W = 30; vormap.IND_E = 1970
    vormap.IND_S = 30; vormap.IND_N = 970
    regions_dict = vormap_viz.compute_regions(points)
    seed_list = list(regions_dict.keys())
    regions = [regions_dict[s] for s in seed_list]
    return regions, points


def test_adjacency_extraction():
    regions, _ = _make_regions()
    adj = vormap_maze._extract_adjacency(regions)
    assert len(adj) == len(regions)
    for cell, neighbours in adj.items():
        for n in neighbours:
            assert cell in adj[n], "Adjacency must be symmetric"


def test_dfs_maze():
    regions, _ = _make_regions()
    maze = vormap_maze.build_maze_graph(regions, algorithm="dfs", seed=42)
    assert maze["cell_count"] == len(regions)
    # spanning tree: passages = cells - 1
    assert len(maze["passages"]) == len(regions) - 1


def test_kruskal_maze():
    regions, _ = _make_regions()
    maze = vormap_maze.build_maze_graph(regions, algorithm="kruskal", seed=42)
    assert len(maze["passages"]) == len(regions) - 1


def test_prim_maze():
    regions, _ = _make_regions()
    maze = vormap_maze.build_maze_graph(regions, algorithm="prim", seed=42)
    assert len(maze["passages"]) == len(regions) - 1


def test_solve():
    regions, _ = _make_regions()
    maze = vormap_maze.build_maze_graph(regions, algorithm="dfs", seed=42)
    sol = vormap_maze.solve_maze(maze, 0, len(regions) - 1)
    assert sol is not None
    assert sol[0] == 0
    assert sol[-1] == len(regions) - 1


def test_svg_export():
    regions, _ = _make_regions(30)
    maze = vormap_maze.build_maze_graph(regions, algorithm="dfs", seed=1)
    sol = vormap_maze.solve_maze(maze, 0, 29)
    svg = vormap_maze.export_maze_svg(regions, maze, solution=sol)
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_json_export():
    regions, _ = _make_regions(30)
    maze = vormap_maze.build_maze_graph(regions, algorithm="dfs", seed=1)
    sol = vormap_maze.solve_maze(maze, 0, 29)
    j = vormap_maze.export_maze_json(regions, maze, solution=sol)
    data = json.loads(j)
    assert data["cell_count"] == 30
    assert "passages" in data
    assert "solution" in data


def test_report():
    regions, _ = _make_regions(20)
    maze = vormap_maze.build_maze_graph(regions, algorithm="kruskal", seed=5)
    report = vormap_maze.format_maze_report(maze, algorithm="kruskal")
    assert "VORONOI MAZE REPORT" in report
    assert "kruskal" in report


def test_generate_pipeline():
    result = vormap_maze.generate_maze(n_points=40, algorithm="dfs", solve=True, seed=99)
    assert result["solution"] is not None
    assert "svg" in result
    assert "report" in result


def test_generate_with_files():
    with tempfile.TemporaryDirectory() as td:
        svg_p = os.path.join(td, "maze.svg")
        json_p = os.path.join(td, "maze.json")
        result = vormap_maze.generate_maze(
            n_points=25, algorithm="prim", solve=True, seed=7,
            svg_path=svg_p, json_path=json_p
        )
        assert os.path.exists(svg_p)
        assert os.path.exists(json_p)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception as e:
                print(f"  FAIL  {name}: {e}")
