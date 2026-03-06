"""Tests for vormap_generate — synthetic point pattern generator."""

import json
import math
import os
import tempfile

import vormap_generate


def test_list_patterns():
    patterns = vormap_generate.list_patterns()
    assert "poisson" in patterns
    assert "clustered" in patterns
    assert "regular" in patterns
    assert "inhibitory" in patterns
    assert "gradient" in patterns
    assert "mixed" in patterns


def test_poisson_count():
    pts = vormap_generate.generate_poisson(100, seed=1)
    assert len(pts) == 100


def test_poisson_bounds():
    bounds = (10, 20, 30, 40)
    pts = vormap_generate.generate_poisson(500, bounds=bounds, seed=2)
    for x, y in pts:
        assert 30 <= x <= 40
        assert 10 <= y <= 20


def test_clustered_count():
    pts = vormap_generate.generate_clustered(200, parents=5, seed=3)
    assert len(pts) == 200


def test_clustered_nni():
    """Clustered patterns should have NNI < 1."""
    pts = vormap_generate.generate_clustered(300, parents=5, radius=30, seed=4)
    summary = vormap_generate.pattern_summary(pts, "clustered")
    assert summary["nni"] < 1.0


def test_regular_approximate_count():
    pts = vormap_generate.generate_regular(100, seed=5)
    # Grid may not produce exactly n points
    assert 50 < len(pts) <= 100


def test_regular_nni():
    """Regular patterns should have NNI > 1."""
    pts = vormap_generate.generate_regular(200, jitter=0.05, seed=6)
    summary = vormap_generate.pattern_summary(pts, "regular")
    assert summary["nni"] > 1.0


def test_inhibitory_min_distance():
    min_dist = 50.0
    pts = vormap_generate.generate_inhibitory(50, min_dist=min_dist, seed=7)
    for i, (ax, ay) in enumerate(pts):
        for j, (bx, by) in enumerate(pts):
            if i != j:
                d = math.sqrt((ax - bx) ** 2 + (ay - by) ** 2)
                assert d >= min_dist - 1e-9


def test_gradient_more_points_at_high_end():
    pts = vormap_generate.generate_gradient(500, direction="horizontal",
                                            seed=8)
    # More points should be on the right (higher x)
    mid_x = 1000.0  # midpoint of default 0-2000
    left = sum(1 for x, y in pts if x < mid_x)
    right = sum(1 for x, y in pts if x >= mid_x)
    assert right > left


def test_mixed_has_both():
    pts = vormap_generate.generate_mixed(200, cluster_fraction=0.5, seed=9)
    assert len(pts) == 200


def test_generate_pattern_api():
    pts = vormap_generate.generate_pattern("poisson", n=50, seed=10)
    assert len(pts) == 50


def test_generate_pattern_unknown():
    try:
        vormap_generate.generate_pattern("nonexistent", n=10)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_seed_reproducibility():
    a = vormap_generate.generate_poisson(100, seed=42)
    b = vormap_generate.generate_poisson(100, seed=42)
    assert a == b


def test_export_txt():
    pts = vormap_generate.generate_poisson(10, seed=11)
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        path = f.name
    try:
        vormap_generate.export_txt(pts, path, allow_absolute=True)
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 10
        # Each line should have two floats
        x, y = lines[0].strip().split()
        float(x)
        float(y)
    finally:
        os.unlink(path)


def test_export_csv():
    pts = vormap_generate.generate_poisson(10, seed=12)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    try:
        vormap_generate.export_csv(pts, path, allow_absolute=True)
        with open(path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "x,y"
        assert len(lines) == 11  # header + 10
    finally:
        os.unlink(path)


def test_export_json():
    pts = vormap_generate.generate_poisson(10, seed=13)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        vormap_generate.export_json(pts, path, allow_absolute=True)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 10
        assert len(data[0]) == 2
    finally:
        os.unlink(path)


def test_pattern_summary():
    pts = vormap_generate.generate_poisson(100, seed=14)
    s = vormap_generate.pattern_summary(pts, "poisson")
    assert s["pattern"] == "poisson"
    assert s["count"] == 100
    assert s["nni"] is not None
    assert s["centroid"] is not None
    assert s["spread"] is not None


def test_pattern_summary_empty():
    s = vormap_generate.pattern_summary([], "empty")
    assert s["count"] == 0


def test_cli_stdout(capsys):
    vormap_generate.main(["poisson", "5", "--seed", "15"])
    out = capsys.readouterr().out
    lines = out.strip().split("\n")
    assert len(lines) == 5
