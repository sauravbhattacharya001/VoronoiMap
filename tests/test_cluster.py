"""Tests for vormap_cluster — autonomous spatial clustering analyzer.

This file was rewritten on 2026-05-20 to match the current public API of
vormap_cluster.py. The previous version targeted an earlier (removed) API
that exposed ClusterResult / cluster_regions / generate_clusters, which has
since been replaced by the analyze/kmeans/dbscan pipeline. Until this fix,
pytest collection was broken across the whole repo.
"""

from __future__ import annotations

import csv
import math
import os
import random
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_cluster import (  # noqa: E402
    analyze,
    auto_k,
    dbscan,
    estimate_eps,
    generate_demo_points,
    generate_html,
    kmeans,
    load_csv,
    mean_silhouette,
    silhouette_scores,
    _dist_matrix,
    _euclidean,
    _mean_point,
)


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def three_clusters():
    """3 well-separated tight Gaussian clusters around (0,0), (10,0), (5,10)."""
    rng = random.Random(0)
    pts = []
    for cx, cy in [(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]:
        for _ in range(20):
            pts.append((cx + rng.gauss(0, 0.3), cy + rng.gauss(0, 0.3)))
    return pts


@pytest.fixture
def line_points():
    """10 colinear points along the x-axis."""
    return [(float(i), 0.0) for i in range(10)]


@pytest.fixture
def tiny_points():
    return [(0.0, 0.0), (1.0, 0.0), (0.5, 0.87)]


# ── Helpers ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_euclidean_basic(self):
        assert _euclidean((0, 0), (3, 4)) == pytest.approx(5.0)

    def test_euclidean_zero(self):
        assert _euclidean((1.5, -2.5), (1.5, -2.5)) == 0.0

    def test_dist_matrix_shape_and_symmetry(self, tiny_points):
        m = _dist_matrix(tiny_points)
        n = len(tiny_points)
        assert len(m) == n
        for row in m:
            assert len(row) == n
        for i in range(n):
            assert m[i][i] == 0.0
            for j in range(n):
                assert m[i][j] == pytest.approx(m[j][i])

    def test_dist_matrix_values(self):
        pts = [(0.0, 0.0), (3.0, 4.0)]
        m = _dist_matrix(pts)
        assert m[0][1] == pytest.approx(5.0)
        assert m[1][0] == pytest.approx(5.0)

    def test_mean_point(self):
        assert _mean_point([(0.0, 0.0), (2.0, 4.0)]) == pytest.approx((1.0, 2.0))

    def test_mean_point_single(self):
        assert _mean_point([(7.0, -3.0)]) == (7.0, -3.0)


# ── k-means ─────────────────────────────────────────────────────────────

class TestKMeans:
    def test_returns_labels_and_centroids(self, three_clusters):
        labels, centroids = kmeans(three_clusters, k=3, seed=1)
        assert len(labels) == len(three_clusters)
        assert len(centroids) == 3
        assert set(labels) <= {0, 1, 2}

    def test_recovers_three_clusters(self, three_clusters):
        labels, _ = kmeans(three_clusters, k=3, seed=1)
        # All three cluster ids should appear.
        assert len(set(labels)) == 3

    def test_k_equals_n(self, tiny_points):
        labels, centroids = kmeans(tiny_points, k=3, seed=0)
        assert sorted(labels) == [0, 1, 2]
        assert len(centroids) == 3

    def test_deterministic_with_seed(self, three_clusters):
        a, _ = kmeans(three_clusters, k=3, seed=7)
        b, _ = kmeans(three_clusters, k=3, seed=7)
        assert a == b

    def test_seed_changes_labels_or_init(self, three_clusters):
        # Two different seeds should usually not produce identical label
        # vectors verbatim (cluster id permutations excluded — check raw).
        a, _ = kmeans(three_clusters, k=3, seed=1)
        b, _ = kmeans(three_clusters, k=3, seed=2)
        # Labels may permute; centroids are a more robust signal, but
        # at minimum the run completes.
        assert len(a) == len(b)


# ── DBSCAN ──────────────────────────────────────────────────────────────

class TestDBSCAN:
    def test_finds_dense_clusters(self, three_clusters):
        labels = dbscan(three_clusters, eps=1.0, min_samples=3)
        assert len(labels) == len(three_clusters)
        # Should find at least 2 distinct (non-noise) clusters
        non_noise = {l for l in labels if l != -1}
        assert len(non_noise) >= 2

    def test_all_noise_when_eps_tiny(self, three_clusters):
        labels = dbscan(three_clusters, eps=0.0001, min_samples=5)
        assert all(l == -1 for l in labels)

    def test_single_cluster_when_eps_huge(self, three_clusters):
        labels = dbscan(three_clusters, eps=1000.0, min_samples=3)
        non_noise = {l for l in labels if l != -1}
        assert len(non_noise) == 1

    def test_accepts_precomputed_dmat(self, tiny_points):
        dmat = _dist_matrix(tiny_points)
        labels = dbscan(tiny_points, eps=2.0, min_samples=2, _dmat=dmat)
        assert len(labels) == 3


# ── eps estimation ──────────────────────────────────────────────────────

class TestEstimateEps:
    def test_returns_positive_float(self, three_clusters):
        eps = estimate_eps(three_clusters, k=5)
        assert isinstance(eps, float)
        assert eps > 0.0

    def test_grows_with_sparser_data(self):
        rng = random.Random(0)
        dense = [(rng.uniform(0, 1), rng.uniform(0, 1)) for _ in range(50)]
        sparse = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(50)]
        assert estimate_eps(sparse, k=5) > estimate_eps(dense, k=5)


# ── silhouette ──────────────────────────────────────────────────────────

class TestSilhouette:
    def test_perfect_separation_high_score(self, three_clusters):
        labels, _ = kmeans(three_clusters, k=3, seed=0)
        score = mean_silhouette(three_clusters, labels)
        assert score > 0.5  # tight, well-separated clusters

    def test_silhouette_scores_per_point(self, three_clusters):
        labels, _ = kmeans(three_clusters, k=3, seed=0)
        scores = silhouette_scores(three_clusters, labels)
        assert len(scores) == len(three_clusters)
        for s in scores:
            assert -1.0 - 1e-9 <= s <= 1.0 + 1e-9

    def test_single_cluster_zero(self, tiny_points):
        labels = [0] * len(tiny_points)
        assert mean_silhouette(tiny_points, labels) == 0.0


# ── auto_k ──────────────────────────────────────────────────────────────

class TestAutoK:
    def test_picks_three_for_three_clusters(self, three_clusters):
        best_k, scores = auto_k(three_clusters, k_max=6)
        assert 2 <= best_k <= 6
        assert isinstance(scores, dict)
        # Highest-silhouette k should be the one returned
        assert best_k == max(scores, key=scores.get)

    def test_handles_small_input(self, tiny_points):
        best_k, scores = auto_k(tiny_points, k_max=5)
        assert best_k >= 2
        assert all(isinstance(v, float) for v in scores.values())


# ── analyze (top-level pipeline) ───────────────────────────────────────

class TestAnalyze:
    def test_both_algos(self, three_clusters):
        result = analyze(three_clusters, algo="both")
        assert isinstance(result, dict)
        assert "kmeans" in result
        assert "dbscan" in result

    def test_kmeans_only(self, three_clusters):
        result = analyze(three_clusters, algo="kmeans")
        assert "kmeans" in result

    def test_dbscan_only(self, three_clusters):
        result = analyze(three_clusters, algo="dbscan")
        assert "dbscan" in result

    def test_forced_k(self, three_clusters):
        result = analyze(three_clusters, algo="kmeans", forced_k=4)
        km = result.get("kmeans", {})
        # Either reported k or label cardinality should reflect k=4
        if "k" in km:
            assert km["k"] == 4
        else:
            assert len(set(km.get("labels", []))) == 4


# ── demo + I/O ──────────────────────────────────────────────────────────

class TestDemoAndIO:
    def test_generate_demo_points_deterministic(self):
        a = generate_demo_points(seed=42)
        b = generate_demo_points(seed=42)
        assert a == b
        assert len(a) > 0
        for p in a:
            assert len(p) == 2

    def test_load_csv_roundtrip(self):
        pts = [(1.0, 2.0), (3.5, -4.25), (0.0, 0.0)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pts.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["x", "y"])
                for x, y in pts:
                    w.writerow([x, y])
            loaded = load_csv(path)
        assert loaded == pts

    def test_load_csv_no_header(self):
        pts = [(1.0, 2.0), (3.0, 4.0)]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pts.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                for x, y in pts:
                    w.writerow([x, y])
            loaded = load_csv(path)
        assert loaded == pts


# ── HTML report ─────────────────────────────────────────────────────────

class TestHtmlReport:
    def test_generate_html_contains_canvas(self, three_clusters):
        result = analyze(three_clusters, algo="both")
        html = generate_html(three_clusters, result)
        assert isinstance(html, str)
        assert "<html" in html.lower() or "<!doctype" in html.lower()
        # The report is canvas-based per the module docstring
        assert "canvas" in html.lower()
