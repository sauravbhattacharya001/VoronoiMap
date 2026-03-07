"""Tests for vormap_merge — Region Merger."""

import json
import math
import os
import pytest
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_merge import (
    merge_regions,
    merge_summary,
    export_merge_svg,
    export_merge_json,
    export_merge_csv,
    MergeZone,
    MergeResult,
)


# ── Fixtures ─────────────────────────────────────────────────────────

def _cluster_points():
    """Three distinct clusters with different values."""
    points = [
        # Cluster A: low values near (100, 100)
        (100, 100), (110, 105), (105, 110),
        # Cluster B: mid values near (300, 300)
        (300, 300), (310, 305), (305, 310),
        # Cluster C: high values near (500, 100)
        (500, 100), (510, 105), (505, 110),
    ]
    values = [
        10, 12, 11,   # cluster A
        50, 52, 51,   # cluster B
        90, 92, 91,   # cluster C
    ]
    return points, values


def _simple_adjacency(points):
    """Build a simple adjacency for testing (connect nearest neighbours)."""
    adj = {tuple(p): set() for p in points}
    for i in range(len(points)):
        dists = []
        for j in range(len(points)):
            if i == j:
                continue
            d = math.hypot(points[i][0] - points[j][0],
                           points[i][1] - points[j][1])
            dists.append((d, j))
        dists.sort()
        # Connect to 2-3 nearest
        for _, j in dists[:3]:
            adj[tuple(points[i])].add(tuple(points[j]))
            adj[tuple(points[j])].add(tuple(points[i]))
    return adj


# ── MergeZone tests ──────────────────────────────────────────────────

class TestMergeZone:
    def test_basic_stats(self):
        z = MergeZone(zone_id=0, seeds=[(0, 0)], values=[10, 20, 30])
        assert z.mean_value == 20.0
        assert z.min_value == 10
        assert z.max_value == 30
        assert z.std_value == pytest.approx(math.sqrt(200 / 3), abs=0.01)

    def test_single_value(self):
        z = MergeZone(zone_id=0, seeds=[(0, 0)], values=[42])
        assert z.mean_value == 42.0
        assert z.std_value == 0.0

    def test_identical_values(self):
        z = MergeZone(zone_id=0, seeds=[(0, 0)], values=[5, 5, 5])
        assert z.mean_value == 5.0
        assert z.std_value == 0.0


# ── Input validation ────────────────────────────────────────────────

class TestMergeValidation:
    def test_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            merge_regions([(0, 0), (1, 1)], [1], target_zones=1)

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 2"):
            merge_regions([(0, 0)], [1], target_zones=1)

    def test_no_stopping_criterion(self):
        pts = [(0, 0), (1, 1)]
        adj = {(0, 0): {(1, 1)}, (1, 1): {(0, 0)}}
        with pytest.raises(ValueError, match="target_zones or threshold"):
            merge_regions(pts, [1, 2], adjacency=adj)

    def test_target_zones_too_large(self):
        pts = [(0, 0), (1, 1)]
        adj = {(0, 0): {(1, 1)}, (1, 1): {(0, 0)}}
        with pytest.raises(ValueError, match="target_zones must be"):
            merge_regions(pts, [1, 2], target_zones=3, adjacency=adj)

    def test_target_zones_zero(self):
        pts = [(0, 0), (1, 1)]
        adj = {(0, 0): {(1, 1)}, (1, 1): {(0, 0)}}
        with pytest.raises(ValueError, match="target_zones must be"):
            merge_regions(pts, [1, 2], target_zones=0, adjacency=adj)

    def test_negative_threshold(self):
        pts = [(0, 0), (1, 1)]
        adj = {(0, 0): {(1, 1)}, (1, 1): {(0, 0)}}
        with pytest.raises(ValueError, match="non-negative"):
            merge_regions(pts, [1, 2], threshold=-1, adjacency=adj)


# ── Core merge logic ────────────────────────────────────────────────

class TestMergeByTarget:
    def test_merge_to_one(self):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        assert result.merged_count == 1
        assert len(result.zones) == 1
        assert len(result.zones[0].seeds) == 3
        assert result.zones[0].mean_value == 2.0

    def test_merge_to_two(self):
        points = [(0, 0), (10, 0), (100, 0), (110, 0)]
        values = [1, 2, 100, 101]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (100, 0)},
            (100, 0): {(10, 0), (110, 0)},
            (110, 0): {(100, 0)},
        }
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        assert result.merged_count == 2
        # Similar values should be in the same zone
        zone_map = result.seed_to_zone
        assert zone_map[(0, 0)] == zone_map[(10, 0)]
        assert zone_map[(100, 0)] == zone_map[(110, 0)]
        assert zone_map[(0, 0)] != zone_map[(100, 0)]

    def test_target_equals_original(self):
        """No merging when target equals current count."""
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=3, adjacency=adj)
        assert result.merged_count == 3
        assert result.iterations == 0

    def test_merge_history_recorded(self):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        assert len(result.merge_history) == 2
        # Each entry: (step, zone_a, zone_b, distance)
        for step, za, zb, dist in result.merge_history:
            assert isinstance(dist, float)
            assert dist >= 0

    def test_method_is_target(self):
        points = [(0, 0), (10, 0)]
        values = [1, 2]
        adj = {(0, 0): {(10, 0)}, (10, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        assert result.method == 'target'


class TestMergeByThreshold:
    def test_threshold_stops_early(self):
        """High similarity threshold should stop before merging distant clusters."""
        points = [(0, 0), (10, 0), (100, 0), (110, 0)]
        values = [1, 2, 100, 101]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (100, 0)},
            (100, 0): {(10, 0), (110, 0)},
            (110, 0): {(100, 0)},
        }
        result = merge_regions(points, values, threshold=5.0, adjacency=adj)
        assert result.merged_count == 2  # Each pair merges, but pairs stay separate
        assert result.method == 'threshold'

    def test_zero_threshold_no_merging(self):
        """Zero threshold: only merge if values are exactly equal."""
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, threshold=0.0, adjacency=adj)
        assert result.merged_count == 3  # Nothing merged

    def test_large_threshold_merges_all(self):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 50, 100]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, threshold=1000, adjacency=adj)
        assert result.merged_count == 1

    def test_combined_target_and_threshold(self):
        """Both criteria: stop at whichever is hit first."""
        points = [(0, 0), (10, 0), (20, 0), (30, 0)]
        values = [1, 2, 100, 101]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0), (30, 0)},
            (30, 0): {(20, 0)},
        }
        # Target=3 but threshold=0.5 is more restrictive
        result = merge_regions(points, values, target_zones=3,
                               threshold=0.5, adjacency=adj)
        # Threshold 0.5 prevents merging distance-1 pairs
        assert result.merged_count >= 3


class TestMergeDisconnected:
    def test_disconnected_components(self):
        """Two disconnected pairs — can't merge across gap."""
        points = [(0, 0), (10, 0), (1000, 0), (1010, 0)]
        values = [1, 1, 1, 1]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0)},
            (1000, 0): {(1010, 0)},
            (1010, 0): {(1000, 0)},
        }
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        # Can merge within each pair but not across
        assert result.merged_count == 2


class TestMergeSeedToZone:
    def test_all_seeds_mapped(self):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        for pt in points:
            assert tuple(pt) in result.seed_to_zone

    def test_zone_ids_contiguous(self):
        points, values = _cluster_points()
        adj = _simple_adjacency(points)
        result = merge_regions(points, values, target_zones=3, adjacency=adj)
        zone_ids = sorted(z.zone_id for z in result.zones)
        assert zone_ids == list(range(len(result.zones)))


# ── Summary text ─────────────────────────────────────────────────────

class TestMergeSummary:
    def test_summary_contains_stats(self):
        points = [(0, 0), (10, 0)]
        values = [1, 2]
        adj = {(0, 0): {(10, 0)}, (10, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        text = merge_summary(result)
        assert "Region Merge Summary" in text
        assert "Original regions: 2" in text
        assert "Merged zones:     1" in text
        assert "Zone" in text

    def test_summary_merge_history(self):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        text = merge_summary(result)
        assert "Merge History" in text

    def test_summary_no_history_when_no_merges(self):
        points = [(0, 0), (10, 0)]
        values = [1, 2]
        adj = {(0, 0): {(10, 0)}, (10, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        text = merge_summary(result)
        assert "Merge History" not in text


# ── SVG export ───────────────────────────────────────────────────────

class TestExportSVG:
    def test_svg_file_created(self, tmp_path):
        points = [(0, 0), (100, 0), (50, 100)]
        values = [1, 2, 3]
        adj = _simple_adjacency(points)
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        path = str(tmp_path / "test.svg")
        os.chdir(tmp_path)
        export_merge_svg(result, "test.svg", points=points)
        assert os.path.exists(path)
        content = open(path).read()
        assert "<svg" in content
        assert "Z0" in content or "Z1" in content

    def test_svg_no_labels(self, tmp_path):
        points = [(0, 0), (100, 0)]
        values = [1, 2]
        adj = {(0, 0): {(100, 0)}, (100, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        os.chdir(tmp_path)
        export_merge_svg(result, "test.svg", show_labels=False, points=points)
        content = open(str(tmp_path / "test.svg")).read()
        assert "Z0" not in content

    def test_svg_no_seeds(self, tmp_path):
        points = [(0, 0), (100, 0)]
        values = [1, 2]
        adj = {(0, 0): {(100, 0)}, (100, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        os.chdir(tmp_path)
        export_merge_svg(result, "test.svg", show_seeds=False, points=points)
        content = open(str(tmp_path / "test.svg")).read()
        assert "<circle" not in content


# ── JSON export ──────────────────────────────────────────────────────

class TestExportJSON:
    def test_json_structure(self, tmp_path):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        os.chdir(tmp_path)
        export_merge_json(result, "test.json")
        with open(str(tmp_path / "test.json")) as f:
            data = json.load(f)
        assert data["original_count"] == 3
        assert data["merged_count"] == 2
        assert len(data["zones"]) == 2
        assert "merge_history" in data

    def test_json_zone_fields(self, tmp_path):
        points = [(0, 0), (10, 0)]
        values = [5, 15]
        adj = {(0, 0): {(10, 0)}, (10, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        os.chdir(tmp_path)
        export_merge_json(result, "test.json")
        with open(str(tmp_path / "test.json")) as f:
            data = json.load(f)
        zone = data["zones"][0]
        assert "seeds" in zone
        assert "mean" in zone
        assert "min" in zone
        assert "max" in zone
        assert "std" in zone
        assert zone["mean"] == 10.0


# ── CSV export ───────────────────────────────────────────────────────

class TestExportCSV:
    def test_csv_row_count(self, tmp_path):
        points = [(0, 0), (10, 0), (20, 0)]
        values = [1, 2, 3]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        os.chdir(tmp_path)
        export_merge_csv(result, "test.csv")
        with open(str(tmp_path / "test.csv")) as f:
            lines = f.readlines()
        assert len(lines) == 4  # header + 3 seed rows

    def test_csv_header(self, tmp_path):
        points = [(0, 0), (10, 0)]
        values = [1, 2]
        adj = {(0, 0): {(10, 0)}, (10, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        os.chdir(tmp_path)
        export_merge_csv(result, "test.csv")
        with open(str(tmp_path / "test.csv")) as f:
            header = f.readline().strip()
        assert "seed_x" in header
        assert "zone_id" in header
        assert "zone_mean" in header


# ── Edge cases ───────────────────────────────────────────────────────

class TestMergeEdgeCases:
    def test_two_points(self):
        points = [(0, 0), (10, 0)]
        values = [5, 5]
        adj = {(0, 0): {(10, 0)}, (10, 0): {(0, 0)}}
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        assert result.merged_count == 1

    def test_equal_values_merge_first(self):
        """When all distances are equal, still merges deterministically."""
        points = [(0, 0), (10, 0), (20, 0)]
        values = [5, 5, 5]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0)},
        }
        result = merge_regions(points, values, target_zones=1, adjacency=adj)
        assert result.merged_count == 1

    def test_original_count_preserved(self):
        points = [(0, 0), (10, 0), (20, 0), (30, 0)]
        values = [1, 1, 100, 100]
        adj = {
            (0, 0): {(10, 0)},
            (10, 0): {(0, 0), (20, 0)},
            (20, 0): {(10, 0), (30, 0)},
            (30, 0): {(20, 0)},
        }
        result = merge_regions(points, values, target_zones=2, adjacency=adj)
        assert result.original_count == 4

    def test_no_adjacency_no_regions_no_scipy(self):
        """Require adjacency or regions when scipy not available."""
        # We can't test this if scipy IS available, but we can test the
        # adjacency=None, regions=None path with a patched _HAS_SCIPY
        import vormap_merge
        original = vormap_merge._HAS_SCIPY
        try:
            vormap_merge._HAS_SCIPY = False
            with pytest.raises(ValueError, match="adjacency or regions"):
                merge_regions([(0, 0), (1, 1)], [1, 2], target_zones=1)
        finally:
            vormap_merge._HAS_SCIPY = original
