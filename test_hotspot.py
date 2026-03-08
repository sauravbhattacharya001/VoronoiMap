"""Tests for vormap_hotspot — Getis-Ord Gi* hotspot detection."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_hotspot import (
    detect_hotspots,
    export_hotspot_svg,
    export_hotspot_json,
    export_hotspot_csv,
    build_queen_weights,
    build_distance_weights,
    build_knn_weights,
    HotspotResult,
    VALID_ATTRIBUTES,
    _gi_star,
    _classify_spot,
    _distance,
    _shared_edge_or_vertex,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _make_grid_data(rows=3, cols=3, spacing=10.0):
    """Create a regular grid of seed points."""
    return [(c * spacing, r * spacing) for r in range(rows) for c in range(cols)]


def _make_regions_from_grid(data, spacing=10.0):
    """Create simple square Voronoi regions for a grid of points."""
    regions = {}
    half = spacing / 2.0
    for pt in data:
        x, y = pt
        verts = [
            (x - half, y - half),
            (x + half, y - half),
            (x + half, y + half),
            (x - half, y + half),
        ]
        regions[pt] = verts
    return regions


def _make_stats_from_grid(data, spacing=10.0, areas=None):
    """Create region stats for grid points."""
    stats = []
    for i, pt in enumerate(data):
        area = areas[i] if areas else spacing * spacing
        stats.append({
            "region_index": i + 1,
            "seed_x": pt[0],
            "seed_y": pt[1],
            "area": area,
            "perimeter": 4 * spacing,
            "centroid_x": pt[0],
            "centroid_y": pt[1],
            "vertex_count": 4,
            "compactness": 0.785,
            "avg_edge_length": spacing,
        })
    return stats


# ── Weight construction tests ──────────────────────────────────────

class TestWeights(unittest.TestCase):

    def test_queen_weights_grid(self):
        data = _make_grid_data(3, 3)
        regions = _make_regions_from_grid(data)
        stats = _make_stats_from_grid(data)
        w = build_queen_weights(regions, stats)
        # Center cell (1,1) at index 4 should have 8 neighbors
        self.assertEqual(len(w[4]), 8)
        # Corner cell (0,0) at index 0 should have 3 neighbors
        self.assertEqual(len(w[0]), 3)

    def test_queen_weights_symmetric(self):
        data = _make_grid_data(3, 3)
        regions = _make_regions_from_grid(data)
        stats = _make_stats_from_grid(data)
        w = build_queen_weights(regions, stats)
        for i in w:
            for j in w[i]:
                self.assertIn(i, w[j], f"Asymmetric: {i} → {j}")

    def test_distance_weights_default(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        w = build_distance_weights(stats)
        # Should have at least direct neighbors
        self.assertTrue(len(w[4]) >= 4)

    def test_distance_weights_custom_threshold(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        w = build_distance_weights(stats, threshold=10.5)
        # Only orthogonal neighbors (not diagonal at ~14.14)
        self.assertEqual(len(w[4]), 4)

    def test_distance_weights_large_threshold(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        w = build_distance_weights(stats, threshold=100)
        # Everyone is everyone's neighbor
        for i in w:
            self.assertEqual(len(w[i]), 8)

    def test_knn_weights(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        w = build_knn_weights(stats, k=4)
        # Each cell should have at least 4 neighbors
        for i in w:
            self.assertTrue(len(w[i]) >= 4)

    def test_knn_weights_k1(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        w = build_knn_weights(stats, k=1)
        # Each cell has at least 1 neighbor
        for i in w:
            self.assertTrue(len(w[i]) >= 1)

    def test_knn_symmetric(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        w = build_knn_weights(stats, k=3)
        for i in w:
            for j in w[i]:
                self.assertIn(i, w[j])


# ── Gi* statistic tests ────────────────────────────────────────────

class TestGiStar(unittest.TestCase):

    def test_uniform_values_zero_z(self):
        values = [10.0] * 9
        weights = {i: {j for j in range(9) if j != i} for i in range(9)}
        z, p = _gi_star(values, weights, 0)
        self.assertAlmostEqual(z, 0.0, places=5)

    def test_high_cluster_positive_z(self):
        # Region 0 and its neighbors have high values
        values = [100.0, 100.0, 100.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        weights = {0: {1, 2}, 1: {0, 2}, 2: {0, 1},
                   3: set(), 4: set(), 5: set(), 6: set(), 7: set(), 8: set()}
        z, p = _gi_star(values, weights, 0)
        self.assertGreater(z, 0)

    def test_low_cluster_negative_z(self):
        values = [1.0, 1.0, 1.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0]
        weights = {0: {1, 2}, 1: {0, 2}, 2: {0, 1},
                   3: set(), 4: set(), 5: set(), 6: set(), 7: set(), 8: set()}
        z, p = _gi_star(values, weights, 0)
        self.assertLess(z, 0)

    def test_single_region(self):
        z, p = _gi_star([5.0], {}, 0)
        self.assertEqual(z, 0.0)
        self.assertEqual(p, 1.0)

    def test_p_value_range(self):
        values = [100.0, 50.0, 10.0, 10.0, 10.0]
        weights = {0: {1}, 1: {0, 2}, 2: {1, 3}, 3: {2, 4}, 4: {3}}
        _, p = _gi_star(values, weights, 0)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)


# ── Classification tests ───────────────────────────────────────────

class TestClassification(unittest.TestCase):

    def test_hotspot_99(self):
        self.assertEqual(_classify_spot(3.0, 0.005, 0.05), "hotspot_99")

    def test_coldspot_95(self):
        self.assertEqual(_classify_spot(-2.0, 0.04, 0.05), "coldspot_95")

    def test_not_significant(self):
        self.assertEqual(_classify_spot(0.5, 0.5, 0.05), "not_significant")

    def test_hotspot_90(self):
        self.assertEqual(_classify_spot(1.5, 0.08, 0.10), "hotspot_90")

    def test_coldspot_99(self):
        self.assertEqual(_classify_spot(-3.5, 0.001, 0.05), "coldspot_99")


# ── detect_hotspots tests ──────────────────────────────────────────

class TestDetectHotspots(unittest.TestCase):

    def test_basic_knn(self):
        data = _make_grid_data(3, 3)
        # Create a hot corner
        areas = [200, 180, 50, 190, 50, 50, 50, 50, 50]
        stats = _make_stats_from_grid(data, areas=areas)
        result = detect_hotspots(stats, attribute="area",
                                 weight_scheme="knn", k=3)
        self.assertIsInstance(result, HotspotResult)
        self.assertEqual(result.total, 9)
        self.assertEqual(result.attribute, "area")

    def test_basic_distance(self):
        data = _make_grid_data(3, 3)
        areas = [200, 180, 50, 190, 50, 50, 50, 50, 50]
        stats = _make_stats_from_grid(data, areas=areas)
        result = detect_hotspots(stats, attribute="area",
                                 weight_scheme="distance",
                                 distance_threshold=15)
        self.assertEqual(result.total, 9)

    def test_basic_queen(self):
        data = _make_grid_data(3, 3)
        regions = _make_regions_from_grid(data)
        areas = [200, 180, 50, 190, 50, 50, 50, 50, 50]
        stats = _make_stats_from_grid(data, areas=areas)
        result = detect_hotspots(stats, attribute="area",
                                 weight_scheme="queen", regions=regions)
        self.assertEqual(result.total, 9)

    def test_queen_requires_regions(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        with self.assertRaises(ValueError):
            detect_hotspots(stats, weight_scheme="queen")

    def test_invalid_attribute(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        with self.assertRaises(ValueError):
            detect_hotspots(stats, attribute="nonexistent",
                            weight_scheme="knn")

    def test_invalid_weight_scheme(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        with self.assertRaises(ValueError):
            detect_hotspots(stats, weight_scheme="invalid")

    def test_empty_stats(self):
        result = detect_hotspots([], weight_scheme="knn")
        self.assertEqual(result.total, 0)

    def test_hotspots_have_positive_z(self):
        data = _make_grid_data(4, 4)
        areas = [200, 180, 190, 170] + [10] * 12
        stats = _make_stats_from_grid(data, areas=areas)
        result = detect_hotspots(stats, attribute="area",
                                 weight_scheme="knn", k=4)
        for h in result.hotspots:
            self.assertGreater(h["z_score"], 0)

    def test_coldspots_have_negative_z(self):
        data = _make_grid_data(4, 4)
        areas = [10, 12, 11, 13] + [200] * 12
        stats = _make_stats_from_grid(data, areas=areas)
        result = detect_hotspots(stats, attribute="area",
                                 weight_scheme="knn", k=4)
        for c in result.coldspots:
            self.assertLess(c["z_score"], 0)

    def test_all_regions_counted(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        result = detect_hotspots(stats, weight_scheme="knn")
        total = len(result.hotspots) + len(result.coldspots) + len(result.not_significant)
        self.assertEqual(total, 9)

    def test_different_attributes(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        for attr in VALID_ATTRIBUTES:
            result = detect_hotspots(stats, attribute=attr,
                                     weight_scheme="knn")
            self.assertEqual(result.attribute, attr)

    def test_confidence_affects_results(self):
        data = _make_grid_data(4, 4)
        areas = [200, 180, 190, 170] + [10] * 12
        stats = _make_stats_from_grid(data, areas=areas)
        strict = detect_hotspots(stats, attribute="area",
                                 weight_scheme="knn", confidence=0.01)
        loose = detect_hotspots(stats, attribute="area",
                                weight_scheme="knn", confidence=0.10)
        self.assertLessEqual(strict.significant_count,
                             loose.significant_count)

    def test_region_entry_keys(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        result = detect_hotspots(stats, weight_scheme="knn")
        expected_keys = {"region_index", "seed_x", "seed_y", "value",
                         "z_score", "p_value", "classification", "neighbors"}
        for r in result.all_regions:
            self.assertEqual(set(r.keys()), expected_keys)


# ── Result methods ──────────────────────────────────────────────────

class TestHotspotResult(unittest.TestCase):

    def test_summary_text(self):
        data = _make_grid_data(3, 3)
        areas = [200, 180, 50, 190, 50, 50, 50, 50, 50]
        stats = _make_stats_from_grid(data, areas=areas)
        result = detect_hotspots(stats, weight_scheme="knn")
        text = result.summary_text()
        self.assertIn("Hotspot Analysis", text)
        self.assertIn("area", text)

    def test_to_dict(self):
        data = _make_grid_data(3, 3)
        stats = _make_stats_from_grid(data)
        result = detect_hotspots(stats, weight_scheme="knn")
        d = result.to_dict()
        self.assertIn("regions", d)
        self.assertEqual(d["total_regions"], 9)
        self.assertIn("significant_pct", d)

    def test_significant_pct(self):
        r = HotspotResult()
        self.assertEqual(r.significant_pct, 0.0)

    def test_properties(self):
        r = HotspotResult(hotspots=[{"a": 1}] * 3, coldspots=[{"a": 1}] * 2,
                          all_regions=[{"a": 1}] * 10)
        self.assertEqual(r.hotspot_count, 3)
        self.assertEqual(r.coldspot_count, 2)
        self.assertEqual(r.significant_count, 5)
        self.assertAlmostEqual(r.significant_pct, 50.0)


# ── Export tests ────────────────────────────────────────────────────

class TestExports(unittest.TestCase):

    def setUp(self):
        self.data = _make_grid_data(3, 3)
        self.regions = _make_regions_from_grid(self.data)
        areas = [200, 180, 50, 190, 50, 50, 50, 50, 50]
        self.stats = _make_stats_from_grid(self.data, areas=areas)
        self.result = detect_hotspots(self.stats, weight_scheme="knn")

    def test_svg_export(self):
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_hotspot_svg(self.result, self.regions, self.data, path)
            self.assertTrue(os.path.exists(path))
            content = open(path).read()
            self.assertIn("<svg", content)
            self.assertIn("polygon", content)
        finally:
            os.unlink(path)

    def test_svg_no_legend(self):
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_hotspot_svg(self.result, self.regions, self.data, path,
                               show_legend=False)
            content = open(path).read()
            self.assertIn("<svg", content)
        finally:
            os.unlink(path)

    def test_svg_empty_data(self):
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_hotspot_svg(self.result, {}, [], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_json_export(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_hotspot_json(self.result, path)
            with open(path) as f:
                d = json.load(f)
            self.assertEqual(d["total_regions"], 9)
            self.assertIn("regions", d)
        finally:
            os.unlink(path)

    def test_csv_export(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_hotspot_csv(self.result, path)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 10)  # header + 9 regions
            self.assertIn("z_score", lines[0])
        finally:
            os.unlink(path)


# ── Helper tests ────────────────────────────────────────────────────

class TestHelpers(unittest.TestCase):

    def test_distance(self):
        self.assertAlmostEqual(_distance((0, 0), (3, 4)), 5.0)

    def test_shared_edge(self):
        a = [(0, 0), (1, 0), (1, 1), (0, 1)]
        b = [(1, 0), (2, 0), (2, 1), (1, 1)]
        self.assertTrue(_shared_edge_or_vertex(a, b))

    def test_no_shared_edge(self):
        a = [(0, 0), (1, 0), (1, 1), (0, 1)]
        b = [(3, 3), (4, 3), (4, 4), (3, 4)]
        self.assertFalse(_shared_edge_or_vertex(a, b))


if __name__ == "__main__":
    unittest.main()
