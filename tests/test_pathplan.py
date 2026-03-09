"""Tests for vormap_pathplan — Voronoi path planner."""

import json
import math
import sys
import os
import unittest

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vormap_pathplan as pp

# Skip all tests if scipy is not available
try:
    from scipy.spatial import Voronoi
    import numpy as np
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


def _make_data(points, padding=0.1):
    """Create a minimal vormap data dict from raw (x, y) points."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad_x = (max_x - min_x) * padding
    pad_y = (max_y - min_y) * padding
    return {
        'data': [[p[0], p[1]] for p in points],
        'south': min_y - pad_y,
        'north': max_y + pad_y,
        'west': min_x - pad_x,
        'east': max_x + pad_x,
    }


# ── Grid of obstacle points for a simple, predictable layout ────
# 4x4 grid creates a Voronoi diagram with clear internal structure
GRID_POINTS = [(i, j) for i in range(4) for j in range(4)]
GRID_DATA = _make_data(GRID_POINTS)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestBuildRoadmap(unittest.TestCase):

    def test_basic_construction(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        self.assertIsInstance(roadmap, pp.Roadmap)
        self.assertGreater(len(roadmap.nodes), 0)
        self.assertGreater(len(roadmap.edges), 0)
        self.assertEqual(len(roadmap.obstacle_points), 16)

    def test_nodes_have_positive_clearance(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        for node in roadmap.nodes:
            self.assertGreater(node.clearance, 0)

    def test_adjacency_is_symmetric(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        for ni, neighbours in roadmap.adjacency.items():
            for nj, edge in neighbours:
                # nj should have ni as a neighbour too
                nj_neighbours = {n for n, _ in roadmap.adjacency.get(nj, [])}
                self.assertIn(ni, nj_neighbours)

    def test_edge_length_positive(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        for edge in roadmap.edges:
            self.assertGreater(edge.length, 0)

    def test_min_clearance_on_edge(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        for edge in roadmap.edges:
            na = roadmap.nodes[edge.node_a]
            nb = roadmap.nodes[edge.node_b]
            expected = min(na.clearance, nb.clearance)
            self.assertAlmostEqual(edge.min_clearance, expected)

    def test_too_few_points_raises(self):
        data = _make_data([(0, 0), (1, 1)])
        with self.assertRaises(ValueError):
            pp.build_roadmap(data)

    def test_bounds_set(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        self.assertIsNotNone(roadmap.bounds)
        self.assertEqual(len(roadmap.bounds), 4)

    def test_clip_to_bounds(self):
        roadmap_clipped = pp.build_roadmap(GRID_DATA, clip_to_bounds=True)
        roadmap_unclipped = pp.build_roadmap(GRID_DATA, clip_to_bounds=False)
        # Unclipped may have more nodes (vertices outside bounds included)
        self.assertGreaterEqual(len(roadmap_unclipped.nodes),
                                len(roadmap_clipped.nodes))


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestFindPath(unittest.TestCase):

    def setUp(self):
        self.roadmap = pp.build_roadmap(GRID_DATA)

    def test_shortest_path_found(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5),
                              mode='shortest')
        self.assertTrue(result.found)
        self.assertGreater(len(result.waypoints), 0)

    def test_safest_path_found(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5),
                              mode='safest')
        self.assertTrue(result.found)
        self.assertGreater(result.min_clearance, 0)

    def test_path_starts_and_ends_correctly(self):
        start = (0.5, 0.5)
        goal = (2.5, 2.5)
        result = pp.find_path(self.roadmap, start, goal)
        self.assertTrue(result.found)
        self.assertEqual(result.waypoints[0], start)
        self.assertEqual(result.waypoints[-1], goal)

    def test_same_start_and_goal(self):
        # Start and goal snap to the same node
        result = pp.find_path(self.roadmap, (0.5, 0.5), (0.5, 0.5))
        self.assertTrue(result.found)

    def test_total_distance_positive(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5))
        self.assertGreater(result.total_distance, 0)

    def test_shortest_is_shorter_or_equal_to_safest(self):
        start = (0.5, 0.5)
        goal = (2.5, 2.5)
        shortest = pp.find_path(self.roadmap, start, goal, mode='shortest')
        safest = pp.find_path(self.roadmap, start, goal, mode='safest')
        # Shortest path should have less or equal distance
        self.assertLessEqual(shortest.total_distance,
                             safest.total_distance + 1e-9)

    def test_safest_has_higher_or_equal_min_clearance(self):
        start = (0.5, 0.5)
        goal = (2.5, 2.5)
        shortest = pp.find_path(self.roadmap, start, goal, mode='shortest')
        safest = pp.find_path(self.roadmap, start, goal, mode='safest')
        self.assertGreaterEqual(safest.min_clearance,
                                shortest.min_clearance - 1e-9)

    def test_empty_roadmap(self):
        empty = pp.Roadmap()
        result = pp.find_path(empty, (0, 0), (1, 1))
        self.assertFalse(result.found)

    def test_mode_stored_in_result(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5),
                              mode='safest')
        self.assertEqual(result.mode, 'safest')


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestPathStats(unittest.TestCase):

    def setUp(self):
        self.roadmap = pp.build_roadmap(GRID_DATA)

    def test_stats_for_found_path(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5))
        stats = pp.compute_path_stats(result, self.roadmap)
        self.assertTrue(stats['found'])
        self.assertGreater(stats['total_distance'], 0)
        self.assertGreater(stats['straight_line_distance'], 0)
        self.assertGreaterEqual(stats['detour_ratio'], 1.0)

    def test_stats_for_unfound_path(self):
        result = pp.PathResult()  # not found
        stats = pp.compute_path_stats(result, self.roadmap)
        self.assertFalse(stats['found'])

    def test_clearance_stats(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5))
        stats = pp.compute_path_stats(result, self.roadmap)
        self.assertGreater(stats['min_clearance'], 0)
        self.assertGreaterEqual(stats['max_clearance'],
                                stats['min_clearance'])
        self.assertGreaterEqual(stats['avg_clearance'],
                                stats['min_clearance'])
        self.assertGreaterEqual(stats['clearance_std'], 0)

    def test_segment_count(self):
        result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5))
        stats = pp.compute_path_stats(result, self.roadmap)
        self.assertEqual(stats['num_segments'],
                         len(result.waypoints) - 1)
        self.assertEqual(stats['num_waypoints'],
                         len(result.waypoints))


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestExportJson(unittest.TestCase):

    def setUp(self):
        self.roadmap = pp.build_roadmap(GRID_DATA)
        self.result = pp.find_path(self.roadmap, (0.5, 0.5), (2.5, 2.5))

    def test_json_structure(self):
        data = pp.export_path_json(self.result, self.roadmap)
        self.assertIn('path', data)
        self.assertIn('roadmap', data)
        self.assertIn('stats', data)

    def test_json_serialisable(self):
        data = pp.export_path_json(self.result, self.roadmap)
        text = json.dumps(data)
        self.assertIsInstance(text, str)

    def test_json_path_fields(self):
        data = pp.export_path_json(self.result, self.roadmap)
        p = data['path']
        self.assertTrue(p['found'])
        self.assertEqual(p['mode'], 'shortest')
        self.assertEqual(len(p['start']), 2)
        self.assertEqual(len(p['goal']), 2)
        self.assertGreater(len(p['waypoints']), 0)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestExportCsv(unittest.TestCase):

    def test_csv_header(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        csv = pp.export_path_csv(result)
        lines = csv.strip().split('\n')
        self.assertEqual(lines[0], 'step,x,y,type')
        self.assertGreater(len(lines), 2)

    def test_csv_start_and_goal_labels(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        csv = pp.export_path_csv(result)
        lines = csv.strip().split('\n')
        self.assertIn('start', lines[1])
        self.assertIn('goal', lines[-1])


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestExportSvg(unittest.TestCase):

    def test_svg_is_valid_xml(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        svg = pp.export_path_svg(roadmap, result)
        from xml.etree.ElementTree import fromstring
        root = fromstring(svg)
        self.assertEqual(root.tag, '{http://www.w3.org/2000/svg}svg')

    def test_svg_contains_polyline_for_found_path(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        svg = pp.export_path_svg(roadmap, result)
        self.assertIn('polyline', svg)

    def test_svg_without_roadmap(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        svg = pp.export_path_svg(roadmap, result, show_roadmap=False)
        # Should still have the path polyline
        self.assertIn('polyline', svg)

    def test_svg_no_path(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.PathResult()
        result.start = (0.5, 0.5)
        result.goal = (2.5, 2.5)
        svg = pp.export_path_svg(roadmap, result)
        # Should still be valid SVG
        self.assertIn('svg', svg)
        self.assertIn('No path', svg)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestFormatReport(unittest.TestCase):

    def test_report_contains_key_sections(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        report = pp.format_path_report(result, roadmap)
        self.assertIn('Distance', report)
        self.assertIn('Clearance', report)
        self.assertIn('Waypoints', report)
        self.assertIn('Roadmap', report)

    def test_report_unfound_path(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.PathResult()
        result.start = (0, 0)
        result.goal = (1, 1)
        report = pp.format_path_report(result, roadmap)
        self.assertIn('No path', report)

    def test_report_mode_displayed(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5),
                              mode='safest')
        report = pp.format_path_report(result, roadmap)
        self.assertIn('safest', report)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestNearestNode(unittest.TestCase):

    def test_finds_closest_node(self):
        roadmap = pp.build_roadmap(GRID_DATA)
        # Pick a node position and query near it
        node = roadmap.nodes[0]
        idx, dist = pp._nearest_node(roadmap, node.x, node.y)
        self.assertEqual(idx, 0)
        self.assertAlmostEqual(dist, 0, places=5)

    def test_empty_roadmap(self):
        empty = pp.Roadmap()
        idx, dist = pp._nearest_node(empty, 0, 0)
        self.assertEqual(idx, -1)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestDifferentPointLayouts(unittest.TestCase):

    def test_triangle(self):
        """3 points forming a triangle — minimal valid input."""
        data = _make_data([(0, 0), (1, 0), (0.5, 1)])
        roadmap = pp.build_roadmap(data)
        self.assertGreater(len(roadmap.nodes), 0)

    def test_dense_random(self):
        """50 random points."""
        import random
        random.seed(42)
        pts = [(random.random() * 10, random.random() * 10)
               for _ in range(50)]
        data = _make_data(pts)
        roadmap = pp.build_roadmap(data)
        result = pp.find_path(roadmap, (1, 1), (9, 9))
        self.assertTrue(result.found)

    def test_collinear_plus_one(self):
        """Nearly collinear points + one offset point."""
        pts = [(i, 0) for i in range(5)] + [(2, 1)]
        data = _make_data(pts)
        roadmap = pp.build_roadmap(data)
        self.assertGreater(len(roadmap.nodes), 0)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestPlanPath(unittest.TestCase):

    def test_plan_path_returns_tuple(self):
        result, roadmap, stats = pp.plan_path(
            GRID_DATA, (0.5, 0.5), (2.5, 2.5), report=False)
        self.assertIsInstance(result, pp.PathResult)
        self.assertIsInstance(roadmap, pp.Roadmap)
        self.assertIsInstance(stats, dict)
        self.assertTrue(result.found)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestStdHelper(unittest.TestCase):

    def test_empty_list(self):
        from vormap_geometry import std as _std
        self.assertEqual(_std([]), 0.0)

    def test_single_value(self):
        from vormap_geometry import std as _std
        self.assertEqual(_std([5.0]), 0.0)

    def test_known_values(self):
        from vormap_geometry import std as _std
        # std of [2, 4, 4, 4, 5, 5, 7, 9] = 2.0
        self.assertAlmostEqual(_std([2, 4, 4, 4, 5, 5, 7, 9]), 2.0)


@unittest.skipUnless(HAS_SCIPY, 'scipy required')
class TestPathPlannerEdgeCases(unittest.TestCase):
    """Edge-case and regression tests for the Voronoi path planner."""

    def test_coincident_start_and_goal_returns_found(self):
        """Start == goal at an arbitrary point should always produce a path."""
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (1.5, 1.5), (1.5, 1.5))
        self.assertTrue(result.found)
        self.assertEqual(result.waypoints[0], (1.5, 1.5))
        self.assertEqual(result.waypoints[-1], (1.5, 1.5))

    def test_start_and_goal_outside_bounds(self):
        """Points far outside the data bounds should still snap to nearest node."""
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (-100, -100), (100, 100))
        self.assertTrue(result.found)
        self.assertGreater(result.total_distance, 0)

    def test_safest_path_clearance_never_zero_on_valid_graph(self):
        """On a valid roadmap, safest path clearance should be positive."""
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5), mode='safest')
        self.assertTrue(result.found)
        self.assertGreater(result.min_clearance, 0)

    def test_all_nodes_have_valid_positions(self):
        """Every roadmap node should have finite coordinates."""
        roadmap = pp.build_roadmap(GRID_DATA)
        for node in roadmap.nodes:
            self.assertTrue(math.isfinite(node.x))
            self.assertTrue(math.isfinite(node.y))
            self.assertTrue(math.isfinite(node.clearance))

    def test_path_waypoints_are_ordered_start_to_goal(self):
        """First waypoint is start, last is goal, intermediates are roadmap nodes."""
        roadmap = pp.build_roadmap(GRID_DATA)
        start, goal = (0.3, 0.3), (2.7, 2.7)
        result = pp.find_path(roadmap, start, goal)
        self.assertTrue(result.found)
        self.assertEqual(result.waypoints[0], start)
        self.assertEqual(result.waypoints[-1], goal)
        # Intermediate waypoints should match roadmap node positions
        for i, ni in enumerate(result.node_indices):
            node = roadmap.nodes[ni]
            wp = result.waypoints[i + 1]  # +1 because waypoints[0] = start
            self.assertAlmostEqual(wp[0], node.x, places=10)
            self.assertAlmostEqual(wp[1], node.y, places=10)

    def test_reverse_path_has_same_distance(self):
        """Path A→B and B→A should have the same total distance (undirected graph)."""
        roadmap = pp.build_roadmap(GRID_DATA)
        ab = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        ba = pp.find_path(roadmap, (2.5, 2.5), (0.5, 0.5))
        self.assertTrue(ab.found and ba.found)
        self.assertAlmostEqual(ab.total_distance, ba.total_distance, places=6)

    def test_csv_export_roundtrips_waypoints(self):
        """CSV export should contain all waypoints."""
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        csv = pp.export_path_csv(result)
        lines = csv.strip().split('\n')
        # Header + one line per waypoint
        self.assertEqual(len(lines), 1 + len(result.waypoints))

    def test_json_export_stats_consistency(self):
        """JSON export stats should match compute_path_stats output."""
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        stats = pp.compute_path_stats(result, roadmap)
        data = pp.export_path_json(result, roadmap, stats)
        self.assertEqual(data['stats']['found'], True)
        self.assertAlmostEqual(data['stats']['total_distance'],
                               stats['total_distance'], places=6)

    def test_large_point_set_performance(self):
        """100-point random set should produce a valid path without error."""
        import random
        random.seed(99)
        pts = [(random.uniform(0, 100), random.uniform(0, 100))
               for _ in range(100)]
        data = _make_data(pts)
        roadmap = pp.build_roadmap(data)
        result = pp.find_path(roadmap, (5, 5), (95, 95))
        self.assertTrue(result.found)
        self.assertGreater(len(result.waypoints), 2)

    def test_svg_custom_dimensions(self):
        """SVG export respects custom width/height."""
        roadmap = pp.build_roadmap(GRID_DATA)
        result = pp.find_path(roadmap, (0.5, 0.5), (2.5, 2.5))
        svg = pp.export_path_svg(roadmap, result, width=400, height=300)
        from xml.etree.ElementTree import fromstring
        root = fromstring(svg)
        self.assertEqual(root.get('width'), '400')
        self.assertEqual(root.get('height'), '300')


if __name__ == '__main__':
    unittest.main()
