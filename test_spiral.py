"""Tests for vormap_spiral – Voronoi Spiral Pattern Generator."""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vormap_spiral import (
    fermat_spiral, archimedean_spiral, logarithmic_spiral, fibonacci_spiral,
    _normalise, to_svg, to_json, GOLDEN_ANGLE, main,
)


def test_fermat_count():
    pts = fermat_spiral(100)
    assert len(pts) == 100


def test_archimedean_count():
    pts = archimedean_spiral(50, turns=3)
    assert len(pts) == 50


def test_logarithmic_count():
    pts = logarithmic_spiral(80, turns=4, growth=0.2)
    assert len(pts) == 80


def test_fibonacci_count():
    pts = fibonacci_spiral(60)
    assert len(pts) == 60


def test_fermat_origin():
    """First seed of Fermat spiral should be at origin (r=0)."""
    pts = fermat_spiral(10)
    assert abs(pts[0][0]) < 1e-9
    assert abs(pts[0][1]) < 1e-9


def test_normalise_fits_canvas():
    pts = fermat_spiral(200)
    norm = _normalise(pts, 800, margin=0.05)
    for x, y in norm:
        assert 0 <= x <= 800
        assert 0 <= y <= 800


def test_svg_output():
    pts = _normalise(fermat_spiral(50), 400)
    svg = to_svg(pts, size=400, colormap="viridis")
    assert svg.startswith("<svg")
    assert "</svg>" in svg
    assert "<circle" in svg


def test_svg_voronoi_flag():
    pts = _normalise(fermat_spiral(20), 400)
    svg = to_svg(pts, size=400, voronoi=True)
    assert "</svg>" in svg


def test_json_output():
    pts = _normalise(archimedean_spiral(30, turns=2), 400)
    j = to_json(pts, "archimedean", {"seeds": 30, "turns": 2})
    data = json.loads(j)
    assert data["type"] == "voronoi_spiral"
    assert data["spiral"] == "archimedean"
    assert len(data["seeds"]) == 30


def test_cli_svg(tmp_path):
    out = str(tmp_path / "test.svg")
    main(["fermat", out, "--seeds", "50", "--size", "400"])
    assert os.path.exists(out)
    content = open(out).read()
    assert "<svg" in content


def test_cli_json(tmp_path):
    out = str(tmp_path / "test.json")
    main(["archimedean", out, "--seeds", "30", "--format", "json"])
    assert os.path.exists(out)
    data = json.loads(open(out).read())
    assert data["seed_count"] == 30


def test_cli_seeds_csv(tmp_path):
    out = str(tmp_path / "test.svg")
    csv = str(tmp_path / "seeds.csv")
    main(["fermat", out, "--seeds", "25", "--seeds-out", csv])
    assert os.path.exists(csv)
    lines = open(csv).readlines()
    assert lines[0].strip() == "x,y"
    assert len(lines) == 26  # header + 25 seeds


def test_golden_angle():
    expected = math.pi * (3 - math.sqrt(5))
    assert abs(GOLDEN_ANGLE - expected) < 1e-12


def test_rotation():
    pts1 = fermat_spiral(10, rotation=0)
    pts2 = fermat_spiral(10, rotation=math.pi)
    # With rotation, points should differ (except origin)
    for i in range(1, 10):
        assert abs(pts1[i][0] - pts2[i][0]) > 1e-6 or abs(pts1[i][1] - pts2[i][1]) > 1e-6
