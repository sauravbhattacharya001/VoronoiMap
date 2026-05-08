"""Tests for vormap_resilience — Spatial Resilience Analyzer."""

import json
import math
import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_resilience import (
    ResilienceAnalyzer,
    ResilienceResult,
    PointImpact,
    CascadeStep,
    RedundancySuggestion,
    _gini_coefficient,
    _voronoi_cell_areas,
    _build_adjacency,
    _polygon_area,
    _demo,
    analyze_resilience,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _grid_points(rows=4, cols=4, spacing=100):
    """Generate a regular grid of points."""
    pts = []
    for r in range(rows):
        for c in range(cols):
            pts.append((c * spacing + 50.0, r * spacing + 50.0))
    return pts


def _write_points(pts, path):
    with open(path, "w") as f:
        for x, y in pts:
            f.write("%.4f,%.4f\n" % (x, y))


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_gini_equal():
    """Equal values → Gini = 0."""
    assert abs(_gini_coefficient([10, 10, 10, 10])) < 0.01


def test_gini_unequal():
    """Highly unequal → Gini close to 1."""
    g = _gini_coefficient([0, 0, 0, 1000])
    assert g > 0.5


def test_gini_empty():
    assert _gini_coefficient([]) == 0.0


def test_polygon_area_triangle():
    tri = [(0, 0), (4, 0), (0, 3)]
    assert abs(_polygon_area(tri) - 6.0) < 0.01


def test_polygon_area_square():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert abs(_polygon_area(sq) - 100.0) < 0.01


def test_build_adjacency():
    pts = [(0, 0), (1, 0), (0, 1), (1, 1)]
    adj = _build_adjacency(pts)
    # All points should be connected in some way
    assert all(len(adj[i]) > 0 for i in range(4))


def test_voronoi_cell_areas_sum():
    """Cell areas should roughly sum to bounding area."""
    pts = _grid_points(3, 3, 100)
    bounds = (0, 0, 350, 350)
    areas = _voronoi_cell_areas(pts, bounds)
    assert len(areas) == 9
    total = sum(areas)
    expected = 350 * 350
    # Allow 10% tolerance due to grid sampling
    assert abs(total - expected) / expected < 0.1


def test_analyzer_basic():
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(cascade_depth=2, top_k=5)
    assert isinstance(result, ResilienceResult)
    assert 0 <= result.resilience_score <= 100
    assert result.point_count == 9
    assert len(result.critical_points) <= 5
    assert len(result.cascade_analysis) <= 2


def test_analyzer_min_points():
    """Should raise with fewer than 3 points."""
    try:
        ResilienceAnalyzer([(0, 0), (1, 1)])
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_impact_scores_bounded():
    pts = _grid_points(4, 4, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(top_k=16)
    for cp in result.critical_points:
        assert 0 <= cp.impact_score <= 1.0
        assert cp.neighbors_affected >= 0
        assert 0 <= cp.connectivity_loss <= 1.0


def test_what_if():
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    _ = ra.analyze()  # initialize areas
    wi = ra.what_if([0, 4])
    assert wi["remaining_count"] == 7
    assert 0 <= wi["resilience_after"] <= 100
    assert wi["total_coverage_lost"] > 0


def test_what_if_invalid():
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    _ = ra.analyze()
    wi = ra.what_if([999, -1])
    assert "error" in wi


def test_to_dict():
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(cascade_depth=1, top_k=3)
    d = result.to_dict()
    assert "resilience_score" in d
    assert "critical_points" in d
    assert "cascade_analysis" in d
    assert "recommendations" in d


def test_to_json(tmp_path=None):
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(cascade_depth=1, top_k=3)
    out = os.path.join(tmp_path, "resilience.json")
    result.to_json(out)
    assert os.path.exists(out)
    with open(out) as f:
        data = json.load(f)
    assert data["point_count"] == 9


def test_to_html(tmp_path=None):
    if tmp_path is None:
        tmp_path = tempfile.mkdtemp()
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(cascade_depth=1, top_k=3)
    out = os.path.join(tmp_path, "resilience.html")
    result.to_html(out)
    assert os.path.exists(out)
    with open(out) as f:
        content = f.read()
    assert "Spatial Resilience Report" in content
    assert "score" in content.lower()


def test_redundancy_suggestions():
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(suggest_redundancy=True, top_k=5)
    # Should have some suggestions
    assert len(result.redundancy_suggestions) > 0
    for rs in result.redundancy_suggestions:
        assert rs.impact_reduction > 0


def test_cascade_degradation():
    """Resilience should generally decrease through cascade."""
    pts = _grid_points(4, 4, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze(cascade_depth=4, top_k=16)
    if len(result.cascade_analysis) >= 2:
        # Not strictly monotonic due to stochastic effects, but
        # first step should have higher resilience than last
        first = result.cascade_analysis[0].resilience_after
        last = result.cascade_analysis[-1].resilience_after
        assert first >= last or abs(first - last) < 20  # allow some noise


def test_demo_runs():
    result = _demo()
    assert isinstance(result, ResilienceResult)
    assert result.resilience_score >= 0


def test_analyze_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                      delete=False) as f:
        for x, y in _grid_points(3, 3, 100):
            f.write("%.4f,%.4f\n" % (x, y))
        path = f.name
    try:
        result = analyze_resilience(path, cascade_depth=1, top_k=3)
        assert isinstance(result, ResilienceResult)
        assert result.point_count == 9
    finally:
        os.unlink(path)


def test_recommendations_present():
    pts = _grid_points(3, 3, 100)
    ra = ResilienceAnalyzer(pts)
    result = ra.analyze()
    assert len(result.recommendations) > 0
    assert all(isinstance(r, str) for r in result.recommendations)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print("  PASS  %s" % t.__name__)
            passed += 1
        except Exception as e:
            print("  FAIL  %s: %s" % (t.__name__, e))
            failed += 1
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
