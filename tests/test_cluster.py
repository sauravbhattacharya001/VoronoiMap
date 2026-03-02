"""Tests for vormap_cluster — spatial clustering of Voronoi diagrams."""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap
import vormap_viz
import vormap_cluster
from vormap_cluster import (
    ClusterResult,
    cluster_regions,
    export_cluster_json,
    export_cluster_svg,
    format_cluster_table,
    generate_clusters,
    _build_stats_lookup,
    _metric_value,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def sample_data():
    """5 well-separated points for predictable Voronoi regions."""
    return [(100, 200), (300, 400), (500, 200), (700, 600), (900, 300)]


@pytest.fixture
def sample_regions(sample_data):
    vormap.set_bounds(0, 800, 0, 1000)
    return vormap_viz.compute_regions(sample_data)


@pytest.fixture
def sample_stats(sample_regions, sample_data):
    return vormap_viz.compute_region_stats(sample_regions, sample_data)


@pytest.fixture
def grid_data():
    """3x3 grid of points for uniform regions."""
    points = []
    for x in (200, 500, 800):
        for y in (200, 500, 800):
            points.append((x, y))
    return points


@pytest.fixture
def grid_regions(grid_data):
    vormap.set_bounds(0, 1000, 0, 1000)
    return vormap_viz.compute_regions(grid_data)


@pytest.fixture
def grid_stats(grid_regions, grid_data):
    return vormap_viz.compute_region_stats(grid_regions, grid_data)


# ── ClusterResult ───────────────────────────────────────────────────

class TestClusterResult:
    def test_default(self):
        r = ClusterResult()
        assert r.method == ""
        assert r.metric == "area"
        assert r.num_clusters == 0
        assert r.num_noise == 0
        assert r.clusters == []
        assert r.labels == {}
        assert r.params == {}

    def test_fields(self):
        r = ClusterResult(
            method="dbscan", metric="density",
            num_clusters=2, num_noise=1,
            clusters=[{"cluster_id": 0, "size": 3}],
            labels={"1.0,2.0": 0},
            params={"min_neighbors": 2},
        )
        assert r.method == "dbscan"
        assert r.num_clusters == 2
        assert r.num_noise == 1


# ── Helpers ─────────────────────────────────────────────────────────

class TestHelpers:
    def test_build_stats_lookup(self, sample_stats):
        lookup = _build_stats_lookup(sample_stats)
        assert len(lookup) == len(sample_stats)
        for stat in sample_stats:
            key = (stat["seed_x"], stat["seed_y"])
            assert key in lookup
            assert lookup[key]["area"] == stat["area"]

    def test_metric_value_area(self):
        stat = {"area": 1000.0, "compactness": 0.5, "vertex_count": 6}
        assert _metric_value(stat, "area") == 1000.0

    def test_metric_value_density(self):
        stat = {"area": 500.0, "compactness": 0.5, "vertex_count": 6}
        assert abs(_metric_value(stat, "density") - 0.002) < 1e-6

    def test_metric_value_compactness(self):
        stat = {"area": 500.0, "compactness": 0.75, "vertex_count": 6}
        assert _metric_value(stat, "compactness") == 0.75

    def test_metric_value_vertices(self):
        stat = {"area": 500.0, "compactness": 0.5, "vertex_count": 8}
        assert _metric_value(stat, "vertices") == 8

    def test_metric_value_unknown(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            _metric_value({"area": 1}, "invalid")

    def test_density_zero_area(self):
        stat = {"area": 0.0, "compactness": 0.5, "vertex_count": 3}
        assert _metric_value(stat, "density") == float("inf")


# ── Threshold clustering ───────────────────────────────────────────

class TestThresholdClustering:
    def test_basic(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
        )
        assert isinstance(result, ClusterResult)
        assert result.method == "threshold"
        assert result.metric == "area"
        assert result.num_clusters >= 1

    def test_auto_range(self, sample_stats, sample_regions, sample_data):
        """Without explicit range, should auto-compute mean +/- 1 std."""
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
        )
        assert "value_range" in result.params
        vr = result.params["value_range"]
        assert len(vr) == 2
        assert vr[0] < vr[1]

    def test_explicit_range(self, sample_stats, sample_regions, sample_data):
        # Very wide range should capture all cells into one cluster
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
            value_range=(0, 1e9),
        )
        # All cells should be in clusters (no noise)
        assert result.num_noise == 0
        total_in_clusters = sum(c["size"] for c in result.clusters)
        assert total_in_clusters == len(sample_data)

    def test_narrow_range_creates_noise(self, sample_stats, sample_regions, sample_data):
        # Very narrow range should exclude most cells
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
            value_range=(-1, -0.5),
        )
        # All cells should be noise
        total_labeled = sum(c["size"] for c in result.clusters)
        assert total_labeled == 0
        assert result.num_noise == len(sample_data)

    def test_compactness_metric(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="compactness",
        )
        assert result.metric == "compactness"
        assert result.num_clusters >= 0

    def test_cluster_summary_fields(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
            value_range=(0, 1e9),
        )
        for c in result.clusters:
            assert "cluster_id" in c
            assert "size" in c
            assert "seeds" in c
            assert "mean_area" in c
            assert "total_area" in c
            assert "min_area" in c
            assert "max_area" in c
            assert "mean_compactness" in c
            assert "centroid_x" in c
            assert "centroid_y" in c
            assert c["size"] > 0
            assert c["mean_area"] > 0
            assert c["total_area"] >= c["mean_area"]


# ── DBSCAN clustering ──────────────────────────────────────────────

class TestDBSCANClustering:
    def test_basic(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="dbscan", min_neighbors=2,
        )
        assert result.method == "dbscan"
        assert result.num_clusters >= 0
        assert result.params["min_neighbors"] == 2

    def test_low_min_neighbors(self, grid_stats, grid_regions, grid_data):
        """With low min_neighbors, most cells should cluster."""
        result = cluster_regions(
            grid_stats, grid_regions, grid_data,
            method="dbscan", min_neighbors=1,
        )
        total = sum(c["size"] for c in result.clusters)
        assert total >= 5  # Most of 9 cells should cluster

    def test_high_min_neighbors_creates_noise(self, sample_stats, sample_regions, sample_data):
        """With very high min_neighbors, all cells become noise."""
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="dbscan", min_neighbors=100,
        )
        assert result.num_noise == len(sample_data)
        assert result.num_clusters == 0

    def test_noise_cells(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="dbscan", min_neighbors=3,
        )
        # Some cells may be noise (label -1)
        noise_count = sum(1 for v in result.labels.values() if v == -1)
        assert noise_count == result.num_noise

    def test_labels_cover_all_seeds(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="dbscan", min_neighbors=2,
        )
        assert len(result.labels) == len(sample_data)


# ── Agglomerative clustering ──────────────────────────────────────

class TestAgglomerativeClustering:
    def test_basic(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=2,
        )
        assert result.method == "agglomerative"
        assert result.num_clusters == 2
        assert result.params["num_clusters"] == 2

    def test_single_cluster(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=1,
        )
        assert result.num_clusters == 1
        assert result.clusters[0]["size"] == len(sample_data)

    def test_max_clusters(self, sample_stats, sample_regions, sample_data):
        """Requesting more clusters than cells."""
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=100,
        )
        # Each cell is its own cluster
        assert result.num_clusters == len(sample_data)

    def test_no_noise(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=3,
        )
        assert result.num_noise == 0
        total = sum(c["size"] for c in result.clusters)
        assert total == len(sample_data)

    def test_cluster_ids_sequential(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=3,
        )
        ids = [c["cluster_id"] for c in result.clusters]
        assert ids == list(range(len(ids)))

    def test_density_metric(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", metric="density", num_clusters=2,
        )
        assert result.metric == "density"
        assert result.num_clusters == 2


# ── Validation ──────────────────────────────────────────────────────

class TestValidation:
    def test_unknown_method(self, sample_stats, sample_regions, sample_data):
        with pytest.raises(ValueError, match="Unknown clustering method"):
            cluster_regions(
                sample_stats, sample_regions, sample_data,
                method="invalid",
            )

    def test_unknown_metric(self, sample_stats, sample_regions, sample_data):
        with pytest.raises(ValueError, match="Unknown metric"):
            cluster_regions(
                sample_stats, sample_regions, sample_data,
                metric="invalid",
            )


# ── JSON export ─────────────────────────────────────────────────────

class TestExportJSON:
    def test_export(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_cluster_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert data["method"] == "threshold"
            assert data["metric"] == "area"
            assert "clusters" in data
            assert "labels" in data
            assert data["num_clusters"] == result.num_clusters
        finally:
            os.unlink(path)


# ── SVG export ──────────────────────────────────────────────────────

class TestExportSVG:
    def test_export(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
            value_range=(0, 1e9),
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_cluster_svg(result, sample_regions, sample_data, path)
            content = open(path).read()
            assert "<svg" in content
            assert "polygon" in content
            assert "circle" in content
        finally:
            os.unlink(path)

    def test_export_with_labels(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=2,
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_cluster_svg(
                result, sample_regions, sample_data, path,
                show_labels=True, title="Test Clusters",
            )
            content = open(path).read()
            assert "Test Clusters" in content
        finally:
            os.unlink(path)

    def test_export_with_noise(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="dbscan", min_neighbors=100,  # all noise
        )
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_cluster_svg(result, sample_regions, sample_data, path)
            content = open(path).read()
            assert "<svg" in content
            # Noise cells rendered in gray
            assert "#dddddd" in content
        finally:
            os.unlink(path)


# ── Text formatting ─────────────────────────────────────────────────

class TestFormatTable:
    def test_format(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="threshold", metric="area",
        )
        table = format_cluster_table(result)
        assert "Spatial Clustering Report" in table
        assert "threshold" in table
        assert "area" in table

    def test_format_dbscan_with_noise(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="dbscan", min_neighbors=100,
        )
        table = format_cluster_table(result)
        assert "Noise cells:" in table

    def test_format_agglomerative(self, sample_stats, sample_regions, sample_data):
        result = cluster_regions(
            sample_stats, sample_regions, sample_data,
            method="agglomerative", num_clusters=2,
        )
        table = format_cluster_table(result)
        assert "agglomerative" in table
        assert "C0" in table
        assert "C1" in table


# ── Grid data tests ─────────────────────────────────────────────────

class TestGridData:
    def test_uniform_grid_threshold(self, grid_stats, grid_regions, grid_data):
        """Uniform grid: wide threshold should capture all cells."""
        result = cluster_regions(
            grid_stats, grid_regions, grid_data,
            method="threshold", metric="area",
            value_range=(0, 1e9),
        )
        total = sum(c["size"] for c in result.clusters)
        assert total == len(grid_data)
        # All in one connected cluster since grid is connected
        assert result.num_clusters == 1

    def test_uniform_grid_agglomerative(self, grid_stats, grid_regions, grid_data):
        result = cluster_regions(
            grid_stats, grid_regions, grid_data,
            method="agglomerative", metric="area", num_clusters=3,
        )
        assert result.num_clusters == 3
        total = sum(c["size"] for c in result.clusters)
        assert total == len(grid_data)

    def test_grid_dbscan_low_min(self, grid_stats, grid_regions, grid_data):
        result = cluster_regions(
            grid_stats, grid_regions, grid_data,
            method="dbscan", min_neighbors=2,
        )
        # Interior points have 4 neighbors, edge points have 2-3
        # So most should be core cells → expect clustering
        total = sum(c["size"] for c in result.clusters)
        assert total >= 5


# ── Generate convenience ────────────────────────────────────────────

class TestGenerateClusters:
    def test_generate_json(self, tmp_path, monkeypatch):
        # Create test data file in a data/ subdir and run from there
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_file = data_dir / "test_points.txt"
        data_file.write_text("100 200\n300 400\n500 200\n700 600\n900 300\n")

        monkeypatch.chdir(tmp_path)
        out_json = str(tmp_path / "clusters.json")
        result = generate_clusters(
            "test_points.txt", out_json,
            method="agglomerative", num_clusters=2,
        )
        assert os.path.exists(out_json)
        with open(out_json) as f:
            data = json.load(f)
        assert data["num_clusters"] == 2

    def test_generate_svg(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_file = data_dir / "test_points.txt"
        data_file.write_text("100 200\n300 400\n500 200\n700 600\n900 300\n")

        monkeypatch.chdir(tmp_path)
        out_svg = str(tmp_path / "clusters.svg")
        result = generate_clusters(
            "test_points.txt", out_svg,
            method="dbscan", min_neighbors=2,
        )
        assert os.path.exists(out_svg)
        content = open(out_svg).read()
        assert "<svg" in content

    def test_generate_table(self, tmp_path, monkeypatch, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        data_file = data_dir / "test_points.txt"
        data_file.write_text("100 200\n300 400\n500 200\n700 600\n900 300\n")

        monkeypatch.chdir(tmp_path)
        result = generate_clusters("test_points.txt", fmt="table")
        captured = capsys.readouterr()
        assert "Spatial Clustering Report" in captured.out
