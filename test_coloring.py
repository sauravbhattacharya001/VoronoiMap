"""Tests for vormap_coloring — graph coloring for Voronoi diagrams."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import vormap_coloring


class TestAlgorithms(unittest.TestCase):
    """Test coloring algorithms on a simple adjacency graph."""

    def setUp(self):
        # Triangle: 0-1, 1-2, 0-2
        self.adj = {0: {1, 2}, 1: {0, 2}, 2: {0, 1}}

    def test_greedy(self):
        c = vormap_coloring.color_greedy(self.adj)
        self.assertEqual(len(c), 3)
        for n, color in c.items():
            for nb in self.adj[n]:
                self.assertNotEqual(c[n], c[nb], "neighbors share color")

    def test_welsh_powell(self):
        c = vormap_coloring.color_welsh_powell(self.adj)
        self.assertEqual(len(c), 3)
        for n in c:
            for nb in self.adj[n]:
                self.assertNotEqual(c[n], c[nb])

    def test_dsatur(self):
        c = vormap_coloring.color_dsatur(self.adj)
        self.assertEqual(len(c), 3)
        for n in c:
            for nb in self.adj[n]:
                self.assertNotEqual(c[n], c[nb])

    def test_dsatur_uses_3_colors_for_triangle(self):
        c = vormap_coloring.color_dsatur(self.adj)
        self.assertEqual(len(set(c.values())), 3)


class TestPalettes(unittest.TestCase):
    def test_known_palettes(self):
        for name in vormap_coloring.PALETTES:
            colors = vormap_coloring._resolve_palette(name, 4)
            self.assertGreaterEqual(len(colors), 4)

    def test_palette_extension(self):
        colors = vormap_coloring._resolve_palette("classic", 10)
        self.assertEqual(len(colors), 10)

    def test_custom_colors(self):
        custom = ["#aaa", "#bbb", "#ccc"]
        colors = vormap_coloring._resolve_palette("classic", 3, custom)
        self.assertEqual(colors[:3], custom)


class TestColoringStats(unittest.TestCase):
    def test_valid_coloring(self):
        adj = {0: {1}, 1: {0}}
        coloring = {0: 0, 1: 1}
        stats = vormap_coloring.coloring_stats(coloring, adj)
        self.assertTrue(stats["is_valid"])
        self.assertEqual(stats["num_colors"], 2)
        self.assertEqual(stats["conflicts"], 0)

    def test_invalid_coloring(self):
        adj = {0: {1}, 1: {0}}
        coloring = {0: 0, 1: 0}
        stats = vormap_coloring.coloring_stats(coloring, adj)
        self.assertFalse(stats["is_valid"])
        self.assertEqual(stats["conflicts"], 1)


class TestHSL(unittest.TestCase):
    def test_grayscale(self):
        r, g, b = vormap_coloring._hsl_to_rgb(0, 0, 0.5)
        self.assertAlmostEqual(r, 0.5)
        self.assertAlmostEqual(g, 0.5)
        self.assertAlmostEqual(b, 0.5)


class TestCLI(unittest.TestCase):
    def test_unknown_algorithm_raises(self):
        with self.assertRaises(ValueError):
            vormap_coloring.color_voronoi({}, [], algorithm="bogus")


if __name__ == "__main__":
    unittest.main()
