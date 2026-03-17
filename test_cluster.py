"""Tests for vormap_cluster — spatial clustering of Voronoi cells."""

import json
import math
import os
import tempfile
import unittest

from vormap_cluster import (
    ClusterResult,
    _build_stats_lookup,
    _cluster_agglomerative,
    _cluster_dbscan,
    _cluster_threshold,
    _metric_value,
    cluster_regions,
    export_cluster_json,
    format_cluster_table,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _make_stats(seed, area=100.0, compactness=0.8, vertex_count=6):
    """Create a minimal region stats dict."""
    return {
        "seed_x": seed[0],
        "seed_y": seed[1],
        "area": area,
        "compactness": compactness,
        "vertex_count": vertex_count,
        "centroid_x": seed[0],
        "centroid_y": seed[1],
    }


def _grid_seeds(n=3):
    """Return an n×n grid of seed points spaced 10 apart."""
    return [(float(x * 10), float(y * 10)) for x in range(n) for y in range(n)]


def _chain_adjacency(seeds):
    """Build linear chain adjacency: seed[i] <-> seed[i+1]."""
    adj = {s: [] for s in seeds}
    for i in range(len(seeds) - 1):
        adj[seeds[i]].append(seeds[i + 1])
        adj[seeds[i + 1]].append(seeds[i])
    return adj


def _full_adjacency(seeds):
    """Every seed adjacent to every other seed."""
    adj = {s: [o for o in seeds if o != s] for s in seeds}
    return adj


def _grid_adjacency(n=3):
    """4-connected grid adjacency for an n×n grid."""
    seeds = _grid_seeds(n)
    seed_set = set(seeds)
    adj = {s: [] for s in seeds}
    for (x, y) in seeds:
        for dx, dy in [(10, 0), (-10, 0), (0, 10), (0, -10)]:
            nb = (x + dx, y + dy)
            if nb in seed_set:
                adj[(x, y)].append(nb)
    return adj


# ── _metric_value tests ────────────────────────────────────────────

class TestMetricValue(unittest.TestCase):

    def test_area(self):
        stat = _make_stats((0, 0), area=42.5)
        self.assertAlmostEqual(_metric_value(stat, "area"), 42.5)

    def test_density(self):
        stat = _make_stats((0, 0), area=4.0)
        self.assertAlmostEqual(_metric_value(stat, "density"), 0.25)

    def test_density_zero_area(self):
        stat = _make_stats((0, 0), area=0.0)
        self.assertEqual(_metric_value(stat, "density"), float("inf"))

    def test_compactness(self):
        stat = _make_stats((0, 0), compactness=0.65)
        self.assertAlmostEqual(_metric_value(stat, "compactness"), 0.65)

    def test_vertices(self):
        stat = _make_stats((0, 0), vertex_count=8)
        self.assertEqual(_metric_value(stat, "vertices"), 8)

    def test_unknown_metric_raises(self):
        stat = _make_stats((0, 0))
        with self.assertRaises(ValueError):
            _metric_value(stat, "nonsense")


# ── _build_stats_lookup tests ──────────────────────────────────────

class TestBuildStatsLookup(unittest.TestCase):

    def test_basic(self):
        stats = [_make_stats((1.0, 2.0), area=10), _make_stats((3.0, 4.0), area=20)]
        lookup = _build_stats_lookup(stats)
        self.assertEqual(len(lookup), 2)
        self.assertAlmostEqual(lookup[(1.0, 2.0)]["area"], 10)
        self.assertAlmostEqual(lookup[(3.0, 4.0)]["area"], 20)

    def test_empty(self):
        self.assertEqual(_build_stats_lookup([]), {})


# ── Threshold clustering tests ─────────────────────────────────────

class TestThresholdClustering(unittest.TestCase):

    def test_all_in_range_single_cluster(self):
        seeds = _grid_seeds(3)
        adj = _grid_adjacency(3)
        lookup = {s: _make_stats(s, area=50) for s in seeds}
        labels = _cluster_threshold(seeds, adj, lookup, "area", (0, 100))
        # All connected & in range → single cluster
        unique = {v for v in labels.values() if v >= 0}
        self.assertEqual(len(unique), 1)

    def test_none_in_range(self):
        seeds = _grid_seeds(2)
        adj = _grid_adjacency(2)
        lookup = {s: _make_stats(s, area=200) for s in seeds}
        labels = _cluster_threshold(seeds, adj, lookup, "area", (0, 50))
        # All out of range → all -1
        self.assertTrue(all(v == -1 for v in labels.values()))

    def test_two_disconnected_groups(self):
        """Two groups separated by out-of-range cells form separate clusters."""
        seeds = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
        adj = _chain_adjacency(seeds)
        lookup = {
            (0.0, 0.0): _make_stats((0, 0), area=10),
            (10.0, 0.0): _make_stats((10, 0), area=200),  # out of range
            (20.0, 0.0): _make_stats((20, 0), area=200),  # out of range
            (30.0, 0.0): _make_stats((30, 0), area=10),
        }
        labels = _cluster_threshold(seeds, adj, lookup, "area", (0, 50))
        self.assertNotEqual(labels[(0.0, 0.0)], -1)
        self.assertNotEqual(labels[(30.0, 0.0)], -1)
        self.assertNotEqual(labels[(0.0, 0.0)], labels[(30.0, 0.0)])
        self.assertEqual(labels[(10.0, 0.0)], -1)
        self.assertEqual(labels[(20.0, 0.0)], -1)


# ── DBSCAN clustering tests ───────────────────────────────────────

class TestDBSCANClustering(unittest.TestCase):

    def test_high_min_neighbors_all_noise(self):
        """If min_neighbors > max degree, everything is noise."""
        seeds = _grid_seeds(2)
        adj = _grid_adjacency(2)
        lookup = {s: _make_stats(s) for s in seeds}
        labels = _cluster_dbscan(seeds, adj, lookup, "area", min_neighbors=10)
        self.assertTrue(all(v == -1 for v in labels.values()))

    def test_single_cluster_low_threshold(self):
        """min_neighbors=1 on a connected graph → single cluster."""
        seeds = _grid_seeds(3)
        adj = _grid_adjacency(3)
        lookup = {s: _make_stats(s) for s in seeds}
        labels = _cluster_dbscan(seeds, adj, lookup, "area", min_neighbors=1)
        assigned = {v for v in labels.values() if v >= 0}
        self.assertEqual(len(assigned), 1)

    def test_core_and_border(self):
        """Center of 3x3 grid is core with min_neighbors=4; corners are border."""
        seeds = _grid_seeds(3)
        adj = _grid_adjacency(3)
        lookup = {s: _make_stats(s) for s in seeds}
        # Center (10,10) has 4 neighbors; corners have 2
        labels = _cluster_dbscan(seeds, adj, lookup, "area", min_neighbors=4)
        # Center should be in a cluster
        self.assertGreaterEqual(labels[(10.0, 10.0)], 0)

    def test_two_dense_groups(self):
        """Two fully-connected groups linked by a thin bridge → 2 clusters with high min_neighbors."""
        group_a = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
        group_b = [(100.0, 0.0), (110.0, 0.0), (100.0, 10.0), (110.0, 10.0)]
        bridge = (50.0, 0.0)
        seeds = group_a + [bridge] + group_b

        adj = {}
        # Fully connect each group
        for g in [group_a, group_b]:
            for s in g:
                adj[s] = [o for o in g if o != s]
        # Bridge connects to one node from each group
        adj[bridge] = [group_a[0], group_b[0]]
        adj[group_a[0]].append(bridge)
        adj[group_b[0]].append(bridge)

        lookup = {s: _make_stats(s) for s in seeds}
        labels = _cluster_dbscan(seeds, adj, lookup, "area", min_neighbors=3)

        # group_a and group_b should be in different clusters
        a_labels = {labels[s] for s in group_a if labels[s] >= 0}
        b_labels = {labels[s] for s in group_b if labels[s] >= 0}
        self.assertEqual(len(a_labels), 1, "group A should form one cluster")
        self.assertEqual(len(b_labels), 1, "group B should form one cluster")
        self.assertTrue(a_labels.isdisjoint(b_labels), "groups should be separate clusters")


# ── Agglomerative clustering tests ────────────────────────────────

class TestAgglomerativeClustering(unittest.TestCase):

    def test_merge_to_one(self):
        seeds = _grid_seeds(2)
        adj = _grid_adjacency(2)
        lookup = {s: _make_stats(s, area=50) for s in seeds}
        labels = _cluster_agglomerative(seeds, adj, lookup, "area", num_clusters=1)
        unique = set(labels.values())
        self.assertEqual(len(unique), 1)

    def test_target_cluster_count(self):
        seeds = _grid_seeds(3)
        adj = _grid_adjacency(3)
        lookup = {s: _make_stats(s, area=50) for s in seeds}
        labels = _cluster_agglomerative(seeds, adj, lookup, "area", num_clusters=3)
        unique = set(labels.values())
        self.assertEqual(len(unique), 3)

    def test_similar_metric_merged_first(self):
        """Seeds with similar area values should be merged before dissimilar ones."""
        s1, s2, s3 = (0.0, 0.0), (10.0, 0.0), (20.0, 0.0)
        seeds = [s1, s2, s3]
        adj = _chain_adjacency(seeds)
        lookup = {
            s1: _make_stats(s1, area=10),
            s2: _make_stats(s2, area=12),  # close to s1
            s3: _make_stats(s3, area=100),  # very different
        }
        labels = _cluster_agglomerative(seeds, adj, lookup, "area", num_clusters=2)
        # s1 and s2 should be in the same cluster, s3 separate
        self.assertEqual(labels[s1], labels[s2])
        self.assertNotEqual(labels[s1], labels[s3])

    def test_num_clusters_zero_treated_as_one(self):
        seeds = _grid_seeds(2)
        adj = _grid_adjacency(2)
        lookup = {s: _make_stats(s) for s in seeds}
        labels = _cluster_agglomerative(seeds, adj, lookup, "area", num_clusters=0)
        self.assertEqual(len(set(labels.values())), 1)


# ── cluster_regions (integration) tests ────────────────────────────

class TestClusterRegions(unittest.TestCase):

    def test_empty_input(self):
        result = cluster_regions([], {}, [], method="dbscan")
        self.assertIsInstance(result, ClusterResult)
        self.assertEqual(result.num_clusters, 0)

    def test_invalid_method_raises(self):
        with self.assertRaises(ValueError):
            cluster_regions([], {}, [], method="kmeans")

    def test_invalid_metric_raises(self):
        with self.assertRaises(ValueError):
            cluster_regions([], {}, [], metric="color")


# ── format_cluster_table tests ─────────────────────────────────────

class TestFormatClusterTable(unittest.TestCase):

    def test_basic_formatting(self):
        result = ClusterResult(
            method="threshold",
            metric="area",
            num_clusters=2,
            num_noise=1,
            clusters=[
                {"cluster_id": 0, "size": 3, "mean_area": 50.0,
                 "total_area": 150.0, "mean_compactness": 0.8,
                 "centroid_x": 10.0, "centroid_y": 20.0},
                {"cluster_id": 1, "size": 2, "mean_area": 80.0,
                 "total_area": 160.0, "mean_compactness": 0.6,
                 "centroid_x": 30.0, "centroid_y": 40.0},
            ],
            labels={},
            params={"method": "threshold", "metric": "area",
                    "value_range": [10, 100]},
        )
        text = format_cluster_table(result)
        self.assertIn("Clusters: 2", text)
        self.assertIn("Noise cells: 1", text)
        self.assertIn("C0", text)
        self.assertIn("C1", text)
        self.assertIn("threshold", text)

    def test_no_noise_omits_noise_line(self):
        result = ClusterResult(method="dbscan", metric="area",
                               num_clusters=1, num_noise=0,
                               clusters=[], labels={}, params={})
        text = format_cluster_table(result)
        self.assertNotIn("Noise", text)


# ── export_cluster_json tests ──────────────────────────────────────

class TestExportClusterJSON(unittest.TestCase):

    def test_roundtrip(self):
        result = ClusterResult(
            method="agglomerative",
            metric="compactness",
            num_clusters=1,
            num_noise=0,
            clusters=[{"cluster_id": 0, "size": 5, "mean_area": 100,
                       "total_area": 500, "mean_compactness": 0.9,
                       "centroid_x": 50, "centroid_y": 50,
                       "min_area": 80, "max_area": 120, "seeds": []}],
            labels={"1.0,2.0": 0, "3.0,4.0": 0},
            params={"method": "agglomerative", "num_clusters": 1},
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            export_cluster_json(result, tmp)
            with open(tmp, "r") as f:
                data = json.load(f)
            self.assertEqual(data["method"], "agglomerative")
            self.assertEqual(data["num_clusters"], 1)
            self.assertEqual(len(data["clusters"]), 1)
            self.assertIn("1.0,2.0", data["labels"])
        finally:
            os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()
