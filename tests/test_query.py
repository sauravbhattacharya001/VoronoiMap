"""Tests for vormap_query — Point Location & Nearest Neighbor Query."""

import json
import math
import os
import tempfile

import pytest

from vormap_query import (
    VoronoiIndex,
    QueryResult,
    QueryStats,
    CoverageResult,
    query_stats,
    coverage_analysis,
    export_query_json,
    export_query_svg,
    _parse_query_point,
    _load_batch_file,
    _point_to_segment_distance,
    _brute_nearest,
    _brute_radius,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_seeds():
    return [(0, 0), (10, 0), (5, 8), (10, 10), (0, 10)]


@pytest.fixture
def simple_regions(simple_seeds):
    # Fake rectangular regions for testing
    return {s: [(s[0]-1, s[1]-1), (s[0]+1, s[1]-1),
                (s[0]+1, s[1]+1), (s[0]-1, s[1]+1)]
            for s in simple_seeds}


@pytest.fixture
def index(simple_seeds, simple_regions):
    return VoronoiIndex(simple_seeds, simple_regions)


@pytest.fixture
def index_no_regions(simple_seeds):
    return VoronoiIndex(simple_seeds)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_create_index(self, simple_seeds):
        idx = VoronoiIndex(simple_seeds)
        assert idx.num_seeds == 5

    def test_empty_seeds_raises(self):
        with pytest.raises(ValueError):
            VoronoiIndex([])

    def test_single_seed(self):
        idx = VoronoiIndex([(5, 5)])
        assert idx.num_seeds == 1

    def test_seeds_property(self, simple_seeds):
        idx = VoronoiIndex(simple_seeds)
        assert idx.seeds == simple_seeds

    def test_seeds_property_is_copy(self, simple_seeds):
        idx = VoronoiIndex(simple_seeds)
        s = idx.seeds
        s.append((99, 99))
        assert idx.num_seeds == 5


# ---------------------------------------------------------------------------
# Nearest
# ---------------------------------------------------------------------------

class TestNearest:
    def test_exact_seed(self, index):
        r = index.nearest((0, 0))
        assert r.seed_index == 0
        assert r.distance == 0.0
        assert r.seed_coords == (0, 0)

    def test_closest_seed(self, index):
        r = index.nearest((1, 0))
        assert r.seed_index == 0
        assert r.distance == pytest.approx(1.0)

    def test_another_seed(self, index):
        r = index.nearest((9, 0))
        assert r.seed_index == 1

    def test_midpoint(self, index):
        # Midpoint of (0,0) and (10,0) is (5,0), equidistant
        r = index.nearest((5, 0))
        assert r.seed_index in (0, 1)
        assert r.distance == pytest.approx(5.0)

    def test_far_point(self, index):
        r = index.nearest((100, 100))
        assert r.distance > 0

    def test_negative_coords(self, index):
        r = index.nearest((-1, -1))
        assert r.seed_index == 0

    def test_result_type(self, index):
        r = index.nearest((0, 0))
        assert isinstance(r, QueryResult)

    def test_single_seed_nearest(self):
        idx = VoronoiIndex([(5, 5)])
        r = idx.nearest((0, 0))
        assert r.seed_index == 0
        assert r.distance == pytest.approx(math.hypot(5, 5))


# ---------------------------------------------------------------------------
# K-Nearest
# ---------------------------------------------------------------------------

class TestNearestK:
    def test_k1(self, index):
        results = index.nearest_k((0, 0), k=1)
        assert len(results) == 1
        assert results[0].seed_index == 0

    def test_k3(self, index):
        results = index.nearest_k((5, 5), k=3)
        assert len(results) == 3
        # Distances should be sorted
        dists = [r.distance for r in results]
        assert dists == sorted(dists)

    def test_k_all(self, index):
        results = index.nearest_k((5, 5), k=5)
        assert len(results) == 5

    def test_k_exceeds_seeds(self, index):
        results = index.nearest_k((5, 5), k=100)
        assert len(results) == 5  # capped at num_seeds

    def test_k_zero_raises(self, index):
        with pytest.raises(ValueError):
            index.nearest_k((0, 0), k=0)

    def test_k_negative_raises(self, index):
        with pytest.raises(ValueError):
            index.nearest_k((0, 0), k=-1)

    def test_k2_single_seed(self):
        idx = VoronoiIndex([(5, 5)])
        results = idx.nearest_k((0, 0), k=2)
        assert len(results) == 1

    def test_k_results_are_query_results(self, index):
        results = index.nearest_k((0, 0), k=2)
        for r in results:
            assert isinstance(r, QueryResult)


# ---------------------------------------------------------------------------
# Locate
# ---------------------------------------------------------------------------

class TestLocate:
    def test_locate_on_seed(self, index):
        r = index.locate((0, 0))
        assert r == 0

    def test_locate_near_seed(self, index):
        r = index.locate((0.1, 0.1))
        assert r == 0

    def test_locate_near_another(self, index):
        r = index.locate((10, 10))
        assert r == 3

    def test_locate_returns_int(self, index):
        r = index.locate((5, 5))
        assert isinstance(r, int)


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

class TestBatch:
    def test_batch_query(self, index):
        pts = [(0, 0), (10, 0), (5, 8)]
        results = index.batch_query(pts)
        assert len(results) == 3
        assert results[0].seed_index == 0
        assert results[1].seed_index == 1
        assert results[2].seed_index == 2

    def test_batch_query_empty(self, index):
        results = index.batch_query([])
        assert results == []

    def test_batch_locate(self, index):
        pts = [(0, 0), (10, 10)]
        locs = index.batch_locate(pts)
        assert locs == [0, 3]

    def test_batch_locate_empty(self, index):
        locs = index.batch_locate([])
        assert locs == []

    def test_batch_large(self, index):
        import random
        random.seed(42)
        pts = [(random.uniform(-5, 15), random.uniform(-5, 15)) for _ in range(100)]
        results = index.batch_query(pts)
        assert len(results) == 100


# ---------------------------------------------------------------------------
# Radius
# ---------------------------------------------------------------------------

class TestWithinRadius:
    def test_radius_includes_self(self, index):
        results = index.within_radius((0, 0), 0.1)
        assert len(results) == 1
        assert results[0].seed_index == 0

    def test_radius_zero(self, index):
        results = index.within_radius((0, 0), 0.0)
        assert len(results) == 1  # exact match

    def test_radius_large(self, index):
        results = index.within_radius((5, 5), 100)
        assert len(results) == 5

    def test_radius_none_found(self, index):
        results = index.within_radius((100, 100), 0.1)
        assert len(results) == 0

    def test_radius_sorted_by_distance(self, index):
        results = index.within_radius((5, 5), 20)
        dists = [r.distance for r in results]
        assert dists == sorted(dists)

    def test_negative_radius_raises(self, index):
        with pytest.raises(ValueError):
            index.within_radius((0, 0), -1)

    def test_radius_partial(self, index):
        # Only (0,0) and (10,0) within radius 6 of (5,0)
        results = index.within_radius((5, 0), 6)
        idxs = {r.seed_index for r in results}
        assert 0 in idxs
        assert 1 in idxs


# ---------------------------------------------------------------------------
# Distance to boundary
# ---------------------------------------------------------------------------

class TestDistanceToBoundary:
    def test_at_seed_center(self, index):
        d = index.distance_to_boundary((0, 0))
        assert d is not None
        assert d == pytest.approx(1.0)

    def test_on_boundary_edge(self, index):
        d = index.distance_to_boundary((1, 0))
        assert d is not None
        assert d == pytest.approx(0.0, abs=1e-10)

    def test_outside_all(self, index):
        d = index.distance_to_boundary((100, 100))
        assert d is not None
        assert d > 0

    def test_no_regions(self, index_no_regions):
        d = index_no_regions.distance_to_boundary((0, 0))
        assert d is None


# ---------------------------------------------------------------------------
# Geometry helper
# ---------------------------------------------------------------------------

class TestPointToSegment:
    def test_on_segment(self):
        assert _point_to_segment_distance(5, 0, 0, 0, 10, 0) == pytest.approx(0.0)

    def test_perpendicular(self):
        assert _point_to_segment_distance(5, 3, 0, 0, 10, 0) == pytest.approx(3.0)

    def test_at_endpoint(self):
        assert _point_to_segment_distance(0, 0, 0, 0, 10, 0) == pytest.approx(0.0)

    def test_beyond_endpoint(self):
        assert _point_to_segment_distance(-1, 0, 0, 0, 10, 0) == pytest.approx(1.0)

    def test_degenerate_segment(self):
        assert _point_to_segment_distance(3, 4, 0, 0, 0, 0) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Brute-force fallback
# ---------------------------------------------------------------------------

class TestBruteForce:
    def test_brute_nearest(self):
        seeds = [(0, 0), (10, 0), (5, 5)]
        result = _brute_nearest(seeds, (1, 0), 1)
        assert result[0][1] == 0  # index 0

    def test_brute_nearest_k(self):
        seeds = [(0, 0), (10, 0), (5, 5)]
        result = _brute_nearest(seeds, (5, 0), 2)
        assert len(result) == 2

    def test_brute_radius(self):
        seeds = [(0, 0), (10, 0), (5, 5)]
        result = _brute_radius(seeds, (0, 0), 1)
        assert len(result) == 1
        assert result[0][1] == 0


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestQueryStats:
    def test_stats_basic(self):
        results = [
            QueryResult(0, 1.0, (0, 0)),
            QueryResult(1, 3.0, (10, 0)),
            QueryResult(2, 5.0, (5, 8)),
        ]
        s = query_stats(results)
        assert s.count == 3
        assert s.min_distance == pytest.approx(1.0)
        assert s.max_distance == pytest.approx(5.0)
        assert s.mean_distance == pytest.approx(3.0)
        assert s.median_distance == pytest.approx(3.0)

    def test_stats_empty(self):
        s = query_stats([])
        assert s.count == 0
        assert s.min_distance == 0.0

    def test_stats_single(self):
        s = query_stats([QueryResult(0, 2.5, (0, 0))])
        assert s.count == 1
        assert s.mean_distance == pytest.approx(2.5)
        assert s.std_distance == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

class TestCoverage:
    def test_coverage(self, index):
        pts = [(0, 0), (0.1, 0.1), (10, 0), (10, 10)]
        c = coverage_analysis(pts, index)
        assert c.total_points == 4
        assert c.region_counts[0] == 2  # (0,0) and (0.1,0.1)
        assert c.region_counts[1] == 1
        assert c.region_counts[3] == 1

    def test_coverage_empty_regions(self, index):
        pts = [(0, 0)]
        c = coverage_analysis(pts, index)
        assert c.empty_regions == 4  # 4 of 5 regions have 0 points

    def test_coverage_no_points(self, index):
        c = coverage_analysis([], index)
        assert c.total_points == 0
        assert c.empty_regions == 5


# ---------------------------------------------------------------------------
# Export JSON
# ---------------------------------------------------------------------------

class TestExportJson:
    def test_export_json(self, tmp_path):
        results = [QueryResult(0, 1.5, (0, 0)), QueryResult(1, 2.5, (10, 0))]
        path = str(tmp_path / "results.json")
        export_query_json(results, path)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]['seed_index'] == 0
        assert data[0]['distance'] == pytest.approx(1.5)

    def test_export_json_empty(self, tmp_path):
        path = str(tmp_path / "empty.json")
        export_query_json([], path)
        with open(path) as f:
            data = json.load(f)
        assert data == []


# ---------------------------------------------------------------------------
# Export SVG
# ---------------------------------------------------------------------------

class TestExportSvg:
    def test_export_svg(self, tmp_path):
        seeds = [(0, 0), (10, 0)]
        queries = [(5, 5)]
        results = [QueryResult(0, 7.07, (0, 0))]
        path = str(tmp_path / "query.svg")
        export_query_svg(seeds, queries, results, path)
        content = open(path).read()
        assert '<svg' in content
        assert '<circle' in content
        assert '<line' in content

    def test_export_svg_empty(self, tmp_path):
        path = str(tmp_path / "empty.svg")
        export_query_svg([], [], [], path)
        content = open(path).read()
        assert '<svg' in content

    def test_export_svg_custom_size(self, tmp_path):
        seeds = [(0, 0)]
        path = str(tmp_path / "sized.svg")
        export_query_svg(seeds, [], [], path, width=400, height=300)
        content = open(path).read()
        assert 'width="400"' in content
        assert 'height="300"' in content


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

class TestParseHelpers:
    def test_parse_point(self):
        assert _parse_query_point("3.5,4.2") == (3.5, 4.2)

    def test_parse_point_int(self):
        assert _parse_query_point("10,20") == (10.0, 20.0)

    def test_parse_point_invalid(self):
        with pytest.raises(ValueError):
            _parse_query_point("bad")

    def test_load_batch_csv(self, tmp_path):
        p = tmp_path / "pts.csv"
        p.write_text("1,2\n3,4\n5,6\n")
        pts = _load_batch_file(str(p))
        assert len(pts) == 3
        assert pts[0] == (1.0, 2.0)

    def test_load_batch_csv_with_header(self, tmp_path):
        p = tmp_path / "pts.csv"
        p.write_text("x,y\n1,2\n3,4\n")
        pts = _load_batch_file(str(p))
        assert len(pts) == 2

    def test_load_batch_json(self, tmp_path):
        p = tmp_path / "pts.json"
        p.write_text(json.dumps([[1, 2], [3, 4]]))
        pts = _load_batch_file(str(p))
        assert len(pts) == 2
        assert pts[0] == (1, 2)


# ---------------------------------------------------------------------------
# Collinear seeds
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_collinear_seeds(self):
        seeds = [(0, 0), (5, 0), (10, 0)]
        idx = VoronoiIndex(seeds)
        r = idx.nearest((3, 0))
        assert r.seed_index in (0, 1)

    def test_duplicate_seeds(self):
        seeds = [(5, 5), (5, 5), (10, 10)]
        idx = VoronoiIndex(seeds)
        r = idx.nearest((5, 5))
        assert r.distance == pytest.approx(0.0)

    def test_two_seeds(self):
        idx = VoronoiIndex([(0, 0), (10, 0)])
        r = idx.nearest((3, 0))
        assert r.seed_index == 0

    def test_large_coordinates(self):
        seeds = [(1e6, 1e6), (1e6 + 1, 1e6)]
        idx = VoronoiIndex(seeds)
        r = idx.nearest((1e6 + 0.3, 1e6))
        assert r.seed_index == 0

    def test_negative_coordinates(self):
        seeds = [(-10, -10), (10, 10)]
        idx = VoronoiIndex(seeds)
        r = idx.nearest((-9, -9))
        assert r.seed_index == 0
