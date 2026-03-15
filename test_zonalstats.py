"""Tests for vormap_zonalstats — Zonal Statistics module."""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vormap_zonalstats as zs


# ── Helpers ──────────────────────────────────────────────────────────

def _square_regions():
    """Create simple square Voronoi-like regions for testing."""
    # Two zones: left half [0,500]x[0,1000], right half [500,1000]x[0,1000]
    regions = {
        (250.0, 500.0): [(0, 0), (500, 0), (500, 1000), (0, 1000)],
        (750.0, 500.0): [(500, 0), (1000, 0), (1000, 1000), (500, 1000)],
    }
    seeds = [(250.0, 500.0), (750.0, 500.0)]
    return regions, seeds


def _sample_observations():
    """Observations spread across two zones."""
    return [
        # Left zone observations
        (100, 300, 10.0),
        (200, 600, 20.0),
        (400, 800, 30.0),
        (250, 500, 15.0),
        # Right zone observations
        (600, 200, 50.0),
        (700, 500, 60.0),
        (900, 900, 70.0),
    ]


# ── Point-in-polygon ────────────────────────────────────────────────

def test_point_in_polygon_inside():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert zs._point_in_polygon(5, 5, square) is True


def test_point_in_polygon_outside():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert zs._point_in_polygon(15, 5, square) is False


def test_point_in_polygon_degenerate():
    assert zs._point_in_polygon(0, 0, [(1, 1)]) is False
    assert zs._point_in_polygon(0, 0, []) is False


# ── Statistics helpers ───────────────────────────────────────────────

def test_median_odd():
    assert zs._median([3, 1, 2]) == 2


def test_median_even():
    assert zs._median([1, 2, 3, 4]) == 2.5


def test_percentile():
    vals = list(range(1, 101))
    assert abs(zs._percentile(vals, 50) - 50.5) < 0.01
    assert zs._percentile(vals, 0) == 1
    assert zs._percentile(vals, 100) == 100


def test_std():
    vals = [2, 4, 4, 4, 5, 5, 7, 9]
    m = sum(vals) / len(vals)
    s = zs._std(vals, m)
    assert abs(s - 2.0) < 0.01


def test_std_single():
    assert zs._std([42.0], 42.0) == 0.0


# ── Zone stats computation ──────────────────────────────────────────

def test_compute_zone_stats_empty():
    s = zs._compute_zone_stats([])
    assert s["count"] == 0
    assert s["mean"] is None
    assert s["sum"] == 0.0


def test_compute_zone_stats_basic():
    s = zs._compute_zone_stats([10, 20, 30])
    assert s["count"] == 3
    assert abs(s["mean"] - 20.0) < 0.001
    assert abs(s["sum"] - 60.0) < 0.001
    assert s["min"] == 10.0
    assert s["max"] == 30.0


def test_compute_zone_stats_percentiles():
    s = zs._compute_zone_stats(list(range(1, 101)), percentiles=[25, 75])
    assert "p25" in s
    assert "p75" in s
    assert abs(s["p25"] - 25.75) < 0.5
    assert abs(s["p75"] - 75.25) < 0.5


# ── Zonal statistics (integration) ──────────────────────────────────

def test_zonal_statistics_basic():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs)

    assert len(stats) == 2

    # Find left and right zones
    left = next(s for s in stats if s["seed_x"] == 250.0)
    right = next(s for s in stats if s["seed_x"] == 750.0)

    assert left["count"] == 4
    assert right["count"] == 3
    assert abs(left["mean"] - 18.75) < 0.01  # (10+20+30+15)/4
    assert abs(right["mean"] - 60.0) < 0.01  # (50+60+70)/3


def test_zonal_statistics_with_percentiles():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs, percentiles=(25, 75))
    for s in stats:
        if s["count"] > 0:
            assert "p25" in s
            assert "p75" in s


def test_zonal_statistics_empty_zone():
    regions, seeds = _square_regions()
    # All observations in left zone only
    obs = [(100, 300, 10.0), (200, 600, 20.0)]
    stats = zs.zonal_statistics(regions, seeds, obs)
    right = next(s for s in stats if s["seed_x"] == 750.0)
    assert right["count"] == 0
    assert right["mean"] is None


def test_zonal_density():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs)
    for s in stats:
        if s["count"] > 0 and s["area"] > 0:
            assert s["density"] > 0


# ── Observation loading ─────────────────────────────────────────────

def test_load_observations():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("# header comment\n")
        f.write("100 200 42.0\n")
        f.write("300 400 55.5\n")
        f.write("\n")
        f.write("# another comment\n")
        f.write("500 600 10\n")
        tmp = f.name

    try:
        obs = zs.load_observations(tmp)
        assert len(obs) == 3
        assert obs[0] == (100.0, 200.0, 42.0)
        assert obs[2] == (500.0, 600.0, 10.0)
    finally:
        os.unlink(tmp)


# ── CSV export ───────────────────────────────────────────────────────

def test_export_csv():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        tmp = f.name

    try:
        path = zs.export_zonal_csv(stats, tmp)
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "zone_id" in content
        assert "mean" in content
        lines = content.strip().split("\n")
        assert len(lines) == 3  # header + 2 zones
    finally:
        os.unlink(tmp)


# ── JSON export ──────────────────────────────────────────────────────

def test_export_json():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs)

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp = f.name

    try:
        path = zs.export_zonal_json(stats, tmp)
        with open(path) as f:
            data = json.load(f)
        assert "summary" in data
        assert data["summary"]["total_zones"] == 2
        assert data["summary"]["total_observations"] == 7
        assert len(data["zones"]) == 2
    finally:
        os.unlink(tmp)


# ── SVG choropleth export ───────────────────────────────────────────

def test_export_choropleth_svg():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs)

    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        tmp = f.name

    try:
        path = zs.export_choropleth_svg(
            stats, regions, seeds, tmp,
            stat="mean", title="Test Choropleth",
        )
        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "<svg" in content
        assert "polygon" in content
        assert "Test Choropleth" in content
    finally:
        os.unlink(tmp)


def test_export_choropleth_different_stats():
    regions, seeds = _square_regions()
    obs = _sample_observations()
    stats = zs.zonal_statistics(regions, seeds, obs)

    for stat_name in ["count", "sum", "density", "max"]:
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            tmp = f.name
        try:
            path = zs.export_choropleth_svg(stats, regions, seeds, tmp, stat=stat_name)
            assert os.path.exists(path)
        finally:
            os.unlink(tmp)


# ── Color mapping ────────────────────────────────────────────────────

def test_value_to_color():
    c = zs._value_to_color(0, 0, 100)
    assert c.startswith("#")
    assert len(c) == 7

    c_low = zs._value_to_color(0, 0, 100)
    c_high = zs._value_to_color(100, 0, 100)
    assert c_low != c_high


def test_value_to_color_equal_range():
    c = zs._value_to_color(50, 50, 50)
    assert c == "#4393c3"


# ── Runner ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in test_funcs:
        try:
            fn()
            passed += 1
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")

    print(f"\n{passed} passed, {failed} failed out of {passed + failed}")
    sys.exit(1 if failed else 0)
