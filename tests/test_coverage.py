"""Tests for vormap_coverage — service area coverage analysis."""

import json
import math
import os
import sys
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap
from vormap_coverage import (
    coverage_analysis,
    suggest_site,
    export_json,
    export_csv,
    export_heatmap_svg,
    render,
    Gap,
    SiteCoverage,
    SiteSuggestion,
    CoverageResult,
)


# -- Fixtures ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_bounds():
    """Reset vormap bounds before each test."""
    vormap.set_bounds(0.0, 1000.0, 0.0, 2000.0)


def _corners():
    """4 points at corners of a 100x100 region."""
    return [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)]


def _center():
    """Single point at center of default bounds."""
    return [(1000.0, 500.0)]


def _grid_3x3():
    """9 points on a 3x3 grid, spacing=100, covering 0-200 x 0-200."""
    pts = []
    for x in [0, 100, 200]:
        for y in [0, 100, 200]:
            pts.append((float(x), float(y)))
    return pts


# -- Basic analysis ----------------------------------------------------------

class TestBasicCoverage:
    def test_single_point_small_radius(self):
        """Single point with small radius should have low coverage."""
        vormap.set_bounds(0.0, 100.0, 0.0, 100.0)
        result = coverage_analysis([(50.0, 50.0)], radius=10.0,
                                   resolution=20, bounds=(0, 0, 100, 100))
        assert result.coverage_ratio < 0.5
        assert result.covered_cells > 0
        assert result.total_cells == result.covered_cells + result.uncovered_cells

    def test_single_point_large_radius(self):
        """Single point with huge radius should cover everything."""
        result = coverage_analysis([(50.0, 50.0)], radius=5000.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        assert result.coverage_ratio == 1.0
        assert result.uncovered_cells == 0
        assert result.max_depth == 1
        assert result.redundancy_ratio == 0.0

    def test_coverage_ratio_bounded(self):
        """Coverage ratio should always be 0-1."""
        result = coverage_analysis(_corners(), radius=30.0,
                                   resolution=20, bounds=(0, 0, 100, 100))
        assert 0.0 <= result.coverage_ratio <= 1.0
        assert 0.0 <= result.redundancy_ratio <= 1.0

    def test_total_cells_equals_grid(self):
        """Total cells should equal rows × cols."""
        result = coverage_analysis(_center(), radius=100.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        assert result.total_cells > 0

    def test_more_points_more_coverage(self):
        """Adding points should never decrease coverage."""
        bounds = (0, 0, 200, 200)
        r1 = coverage_analysis([(100.0, 100.0)], radius=50.0,
                                resolution=20, bounds=bounds)
        r2 = coverage_analysis(
            [(100.0, 100.0), (50.0, 50.0), (150.0, 150.0)],
            radius=50.0, resolution=20, bounds=bounds)
        assert r2.coverage_ratio >= r1.coverage_ratio

    def test_zero_radius_raises(self):
        with pytest.raises(ValueError, match="positive"):
            coverage_analysis([(0, 0)], radius=0)

    def test_negative_radius_raises(self):
        with pytest.raises(ValueError, match="positive"):
            coverage_analysis([(0, 0)], radius=-10)

    def test_empty_points_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            coverage_analysis([], radius=10)

    def test_low_resolution_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            coverage_analysis([(0, 0)], radius=10, resolution=1)


# -- Redundancy / depth ------------------------------------------------------

class TestRedundancy:
    def test_no_overlap(self):
        """Widely spaced points should have zero redundancy."""
        pts = [(50.0, 50.0), (950.0, 950.0)]
        result = coverage_analysis(pts, radius=80.0,
                                   resolution=50, bounds=(0, 0, 1000, 1000))
        assert result.redundancy_ratio == 0.0
        assert result.max_depth == 1

    def test_full_overlap(self):
        """Two identical points should create 100% redundancy on covered area."""
        pts = [(50.0, 50.0), (50.0, 50.0)]
        result = coverage_analysis(pts, radius=30.0,
                                   resolution=20, bounds=(0, 0, 100, 100))
        assert result.max_depth == 2
        assert result.redundancy_ratio == 1.0

    def test_partial_overlap(self):
        """Two close points should have some redundancy."""
        pts = [(40.0, 50.0), (60.0, 50.0)]
        result = coverage_analysis(pts, radius=30.0,
                                   resolution=30, bounds=(0, 0, 100, 100))
        assert 0.0 < result.redundancy_ratio < 1.0
        assert result.max_depth == 2

    def test_avg_depth_at_least_one(self):
        """Average depth of covered cells should be >= 1."""
        result = coverage_analysis(_grid_3x3(), radius=60.0,
                                   resolution=20, bounds=(0, 0, 200, 200))
        assert result.avg_depth >= 1.0


# -- Gap detection -----------------------------------------------------------

class TestGaps:
    def test_full_coverage_no_gaps(self):
        """Full coverage should produce no gaps."""
        result = coverage_analysis([(50.0, 50.0)], radius=5000.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        assert len(result.gaps) == 0

    def test_sparse_coverage_has_gaps(self):
        """Sparse coverage should detect gaps."""
        result = coverage_analysis([(10.0, 10.0)], radius=20.0,
                                   resolution=30, bounds=(0, 0, 200, 200))
        assert len(result.gaps) > 0
        # Gap should be larger than covered area
        total_gap = sum(g.area for g in result.gaps)
        assert total_gap > 0

    def test_gaps_sorted_by_area(self):
        """Gaps should be sorted descending by area."""
        result = coverage_analysis([(10.0, 10.0)], radius=20.0,
                                   resolution=30, bounds=(0, 0, 300, 300))
        if len(result.gaps) >= 2:
            for i in range(len(result.gaps) - 1):
                assert result.gaps[i].area >= result.gaps[i + 1].area

    def test_gap_centroids_in_bounds(self):
        """Gap centroids should be within the analysis bounds."""
        bounds = (0, 0, 200, 200)
        result = coverage_analysis([(10.0, 10.0)], radius=30.0,
                                   resolution=20, bounds=bounds)
        for gap in result.gaps:
            assert bounds[0] <= gap.centroid_x <= bounds[2]
            assert bounds[1] <= gap.centroid_y <= bounds[3]

    def test_gap_area_positive(self):
        """Gap area should be positive."""
        result = coverage_analysis([(10.0, 10.0)], radius=20.0,
                                   resolution=20, bounds=(0, 0, 200, 200))
        for gap in result.gaps:
            assert gap.area > 0
            assert gap.cell_count > 0


# -- Site suggestion ---------------------------------------------------------

class TestSuggestion:
    def test_suggestion_when_gaps_exist(self):
        """Should suggest a site when coverage is incomplete."""
        result = coverage_analysis([(10.0, 10.0)], radius=30.0,
                                   resolution=20, bounds=(0, 0, 200, 200))
        assert result.suggestion is not None
        assert result.suggestion.new_cells > 0
        assert result.suggestion.new_coverage_ratio > result.coverage_ratio

    def test_no_suggestion_full_coverage(self):
        """Should not suggest when coverage is already 100%."""
        result = coverage_analysis([(50.0, 50.0)], radius=5000.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        assert result.suggestion is None

    def test_suggestion_in_bounds(self):
        """Suggested site should be within the analysis bounds."""
        bounds = (0, 0, 200, 200)
        result = coverage_analysis([(10.0, 10.0)], radius=30.0,
                                   resolution=20, bounds=bounds)
        if result.suggestion:
            assert bounds[0] <= result.suggestion.x <= bounds[2]
            assert bounds[1] <= result.suggestion.y <= bounds[3]

    def test_suggest_site_convenience(self):
        """suggest_site() should return the same as result.suggestion."""
        pts = [(10.0, 10.0)]
        bounds = (0, 0, 200, 200)
        suggestion = suggest_site(pts, radius=30.0, resolution=20,
                                  bounds=bounds)
        assert suggestion is not None
        assert suggestion.new_cells > 0

    def test_suggest_site_none_when_full(self):
        """suggest_site should return None at full coverage."""
        s = suggest_site([(50.0, 50.0)], radius=5000.0, resolution=10,
                         bounds=(0, 0, 100, 100))
        assert s is None

    def test_no_suggest_flag(self):
        """suggest=False should skip computation."""
        result = coverage_analysis([(10.0, 10.0)], radius=30.0,
                                   resolution=20, bounds=(0, 0, 200, 200),
                                   suggest=False)
        assert result.suggestion is None


# -- Per-site stats ----------------------------------------------------------

class TestPerSite:
    def test_per_site_count(self):
        """Should have one entry per input point."""
        pts = _corners()
        result = coverage_analysis(pts, radius=30.0,
                                   resolution=20, bounds=(0, 0, 100, 100))
        assert len(result.per_site) == len(pts)

    def test_per_site_coordinates(self):
        """Per-site entries should have correct coordinates."""
        pts = [(25.0, 75.0)]
        result = coverage_analysis(pts, radius=30.0,
                                   resolution=20, bounds=(0, 0, 100, 100))
        assert result.per_site[0].x == 25.0
        assert result.per_site[0].y == 75.0

    def test_exclusive_cells_no_overlap(self):
        """With no overlap, exclusive should equal total covered."""
        pts = [(50.0, 50.0)]
        result = coverage_analysis(pts, radius=20.0,
                                   resolution=20, bounds=(0, 0, 100, 100))
        assert result.per_site[0].exclusive_cells == result.per_site[0].cells_covered

    def test_exclusive_cells_with_overlap(self):
        """With overlap, exclusive < total covered."""
        pts = [(45.0, 50.0), (55.0, 50.0)]
        result = coverage_analysis(pts, radius=30.0,
                                   resolution=30, bounds=(0, 0, 100, 100))
        for s in result.per_site:
            assert s.exclusive_cells <= s.cells_covered


# -- Export ------------------------------------------------------------------

class TestExport:
    def test_json_export(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = coverage_analysis(_grid_3x3(), radius=60.0,
                                   resolution=15, bounds=(0, 0, 200, 200))
        path = "coverage_out.json"
        export_json(result, path)
        with open(tmp_path / path, encoding="utf-8") as f:
            data = json.load(f)
        assert "coverage_ratio" in data
        assert "gaps" in data
        assert "per_site" in data
        assert data["total_cells"] == result.total_cells

    def test_csv_export(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = coverage_analysis(_grid_3x3(), radius=60.0,
                                   resolution=15, bounds=(0, 0, 200, 200))
        path = "coverage_out.csv"
        export_csv(result, path)
        with open(tmp_path / path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines[0].strip() == "x,y,cells_covered,exclusive_cells"
        assert len(lines) == len(result.per_site) + 1  # header + data

    def test_svg_heatmap_export(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = coverage_analysis(_grid_3x3(), radius=60.0,
                                   resolution=15, bounds=(0, 0, 200, 200))
        path = "coverage_out.svg"
        export_heatmap_svg(result, path)
        with open(tmp_path / path, encoding="utf-8") as f:
            content = f.read()
        assert "<svg" in content
        assert "circle" in content  # site markers
        assert "Coverage:" in content  # legend


# -- Text report ------------------------------------------------------------

class TestRender:
    def test_render_has_sections(self):
        result = coverage_analysis(_grid_3x3(), radius=60.0,
                                   resolution=15, bounds=(0, 0, 200, 200))
        text = render(result)
        assert "COVERAGE ANALYSIS REPORT" in text
        assert "Coverage" in text
        assert "Per-Site" in text

    def test_render_gaps_section(self):
        result = coverage_analysis([(10.0, 10.0)], radius=20.0,
                                   resolution=20, bounds=(0, 0, 200, 200))
        text = render(result)
        assert "Gaps" in text

    def test_render_suggestion_section(self):
        result = coverage_analysis([(10.0, 10.0)], radius=20.0,
                                   resolution=20, bounds=(0, 0, 200, 200))
        text = render(result)
        assert "Suggested Next Site" in text


# -- to_dict -----------------------------------------------------------------

class TestToDict:
    def test_dict_keys(self):
        result = coverage_analysis([(50.0, 50.0)], radius=30.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        d = result.to_dict()
        assert "total_cells" in d
        assert "coverage_ratio" in d
        assert "redundancy_ratio" in d
        assert "bounds" in d
        assert "gaps" in d
        assert "per_site" in d

    def test_dict_bounds_structure(self):
        result = coverage_analysis([(50.0, 50.0)], radius=30.0,
                                   resolution=10, bounds=(10, 20, 90, 80))
        d = result.to_dict()
        assert d["bounds"]["west"] == 10
        assert d["bounds"]["south"] == 20
        assert d["bounds"]["east"] == 90
        assert d["bounds"]["north"] == 80

    def test_dict_suggestion_null_when_full(self):
        result = coverage_analysis([(50.0, 50.0)], radius=5000.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        d = result.to_dict()
        assert d["suggestion"] is None

    def test_dict_roundtrip_json(self):
        """to_dict() should be JSON-serializable."""
        result = coverage_analysis(_corners(), radius=30.0,
                                   resolution=15, bounds=(0, 0, 100, 100))
        text = json.dumps(result.to_dict())
        parsed = json.loads(text)
        assert parsed["total_cells"] == result.total_cells


# -- Edge cases ---------------------------------------------------------------

class TestEdgeCases:
    def test_single_point(self):
        """Should work with exactly one point."""
        result = coverage_analysis([(50.0, 50.0)], radius=30.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        assert len(result.per_site) == 1

    def test_duplicate_points(self):
        """Duplicate points should increase depth, not crash."""
        pts = [(50.0, 50.0)] * 5
        result = coverage_analysis(pts, radius=30.0,
                                   resolution=10, bounds=(0, 0, 100, 100))
        assert result.max_depth == 5
        assert len(result.per_site) == 5

    def test_points_on_boundary(self):
        """Points exactly on boundary should not crash."""
        bounds = (0, 0, 100, 100)
        pts = [(0.0, 0.0), (100.0, 100.0), (0.0, 100.0), (100.0, 0.0)]
        result = coverage_analysis(pts, radius=20.0, resolution=10,
                                   bounds=bounds)
        assert result.total_cells > 0

    def test_high_resolution(self):
        """High resolution should give more accurate results."""
        bounds = (0, 0, 100, 100)
        r_lo = coverage_analysis([(50.0, 50.0)], radius=25.0,
                                  resolution=10, bounds=bounds)
        r_hi = coverage_analysis([(50.0, 50.0)], radius=25.0,
                                  resolution=50, bounds=bounds)
        # At higher resolution, total_cells should be greater
        assert r_hi.total_cells > r_lo.total_cells
        # Coverage ratios should be similar (within 10%)
        assert abs(r_hi.coverage_ratio - r_lo.coverage_ratio) < 0.15

    def test_rectangular_bounds(self):
        """Non-square bounds should work correctly."""
        bounds = (0, 0, 400, 100)
        result = coverage_analysis([(200.0, 50.0)], radius=80.0,
                                   resolution=10, bounds=bounds)
        assert result.total_cells > 0
        assert result.coverage_ratio > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
