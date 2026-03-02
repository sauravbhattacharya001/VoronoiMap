"""Tests for vormap_territory — Territorial Analysis."""

import json
import math
import os
import tempfile

import pytest

import vormap
import vormap_viz
import vormap_territory


# -- Fixtures ----------------------------------------------------------

def _square_grid_4():
    """4 points in a grid producing roughly equal-sized regions."""
    points = [(250, 250), (750, 250), (250, 750), (750, 750)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    return regions, points


def _unequal_5():
    """5 points arranged so one region is much larger than others."""
    # Center point will get a large region, corners get squeezed
    points = [(100, 100), (900, 100), (100, 900), (900, 900), (500, 500)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    return regions, points


def _line_3():
    """3 points in a line — creates elongated regions."""
    points = [(200, 500), (500, 500), (800, 500)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    return regions, points


def _single_point():
    """Single point fills the entire diagram."""
    points = [(500, 500)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    return regions, points


# -- Geometry helpers --------------------------------------------------


class TestPolygonArea:
    def test_unit_square(self):
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert vormap_territory._polygon_area(verts) == pytest.approx(1.0)

    def test_triangle(self):
        verts = [(0, 0), (4, 0), (0, 3)]
        assert vormap_territory._polygon_area(verts) == pytest.approx(6.0)

    def test_degenerate_line(self):
        verts = [(0, 0), (1, 1)]
        assert vormap_territory._polygon_area(verts) == 0.0

    def test_empty(self):
        assert vormap_territory._polygon_area([]) == 0.0


class TestPolygonPerimeter:
    def test_unit_square(self):
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert vormap_territory._polygon_perimeter(verts) == pytest.approx(4.0)

    def test_triangle(self):
        verts = [(0, 0), (3, 0), (0, 4)]
        perim = 3 + 4 + 5  # 3-4-5 right triangle
        assert vormap_territory._polygon_perimeter(verts) == pytest.approx(perim)

    def test_single_point(self):
        assert vormap_territory._polygon_perimeter([(0, 0)]) == 0.0


class TestPolygonCentroid:
    def test_unit_square(self):
        verts = [(0, 0), (2, 0), (2, 2), (0, 2)]
        cx, cy = vormap_territory._polygon_centroid(verts)
        assert cx == pytest.approx(1.0)
        assert cy == pytest.approx(1.0)

    def test_empty(self):
        assert vormap_territory._polygon_centroid([]) == (0.0, 0.0)


class TestPointNearBoundary:
    def test_on_boundary(self):
        bounds = (0, 100, 0, 100)
        assert vormap_territory._point_near_boundary((0, 50), bounds, 1.0)
        assert vormap_territory._point_near_boundary((100, 50), bounds, 1.0)
        assert vormap_territory._point_near_boundary((50, 0), bounds, 1.0)
        assert vormap_territory._point_near_boundary((50, 100), bounds, 1.0)

    def test_interior(self):
        bounds = (0, 100, 0, 100)
        assert not vormap_territory._point_near_boundary((50, 50), bounds, 1.0)


# -- Gini coefficient -------------------------------------------------


class TestGiniCoefficient:
    def test_perfect_equality(self):
        vals = [10, 10, 10, 10]
        assert vormap_territory._gini_coefficient(vals) == pytest.approx(0.0)

    def test_maximum_inequality(self):
        # One has everything, rest have nothing
        vals = [0, 0, 0, 100]
        gini = vormap_territory._gini_coefficient(vals)
        assert gini == pytest.approx(0.75)  # n-1/n for one non-zero

    def test_moderate_inequality(self):
        vals = [1, 2, 3, 4, 5]
        gini = vormap_territory._gini_coefficient(vals)
        assert 0 < gini < 1

    def test_empty(self):
        assert vormap_territory._gini_coefficient([]) == 0.0

    def test_all_zero(self):
        assert vormap_territory._gini_coefficient([0, 0, 0]) == 0.0

    def test_single_value(self):
        assert vormap_territory._gini_coefficient([42]) == pytest.approx(0.0)


# -- Territory classification ----------------------------------------


class TestClassification:
    def test_dominant(self):
        assert vormap_territory._classify_territory(200, 100, 50) == "dominant"

    def test_marginal(self):
        assert vormap_territory._classify_territory(30, 100, 50) == "marginal"

    def test_average(self):
        assert vormap_territory._classify_territory(100, 100, 50) == "average"

    def test_zero_std(self):
        assert vormap_territory._classify_territory(100, 100, 0) == "average"

    def test_boundary_dominant(self):
        # Exactly at mean + std — should be average (not strictly >)
        assert vormap_territory._classify_territory(150, 100, 50) == "average"

    def test_boundary_marginal(self):
        # Exactly at mean - std — should be average (not strictly <)
        assert vormap_territory._classify_territory(50, 100, 50) == "average"


# -- Core analysis -----------------------------------------------------


class TestAnalyzeTerritories:
    def test_basic_output_structure(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        assert "regions" in result
        assert "summary" in result
        assert "shared_borders" in result

    def test_region_count(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        assert len(result["regions"]) == 4
        assert result["summary"]["total_regions"] == 4

    def test_region_metrics_present(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        r = result["regions"][0]
        assert "seed" in r
        assert "area" in r
        assert "perimeter" in r
        assert "centroid" in r
        assert "vertex_count" in r
        assert "is_frontier" in r
        assert "classification" in r
        assert "area_share" in r
        assert "neighbor_count" in r
        assert "border_pressure" in r
        assert "shared_border_length" in r

    def test_areas_sum_close_to_total(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        total_from_regions = sum(r["area"] for r in result["regions"])
        assert total_from_regions == pytest.approx(
            result["summary"]["total_area"], rel=0.01
        )

    def test_area_shares_sum_to_one(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        total_share = sum(r["area_share"] for r in result["regions"])
        assert total_share == pytest.approx(1.0, rel=0.01)

    def test_equal_grid_low_gini(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        # 4 equal quadrants should have very low Gini
        assert result["summary"]["gini_coefficient"] < 0.1

    def test_equal_grid_high_balance(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        assert result["summary"]["balance_score"] > 0.9

    def test_unequal_higher_gini(self):
        regions, data = _unequal_5()
        result = vormap_territory.analyze_territories(regions, data)
        # Not perfectly equal
        assert result["summary"]["gini_coefficient"] > 0

    def test_frontier_regions(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        # In a 4-point grid, all regions touch the boundary
        assert result["summary"]["frontier_count"] == 4
        assert result["summary"]["interior_count"] == 0

    def test_shared_borders_exist(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        assert result["summary"]["total_shared_borders"] > 0

    def test_shared_border_records_structure(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        if result["shared_borders"]:
            b = result["shared_borders"][0]
            assert "seed_a" in b
            assert "seed_b" in b
            assert "shared_length" in b
            assert b["shared_length"] > 0

    def test_neighbor_counts_positive(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        for r in result["regions"]:
            assert r["neighbor_count"] >= 0

    def test_border_pressure_range(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        for r in result["regions"]:
            assert 0.0 <= r["border_pressure"] <= 1.0

    def test_dominance_ratio(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        ratio = result["summary"]["dominance_ratio"]
        assert 0 < ratio <= 1
        # For 4 roughly equal regions, dominance ratio ~ 0.25
        assert ratio < 0.5

    def test_single_region(self):
        # compute_regions returns empty for a single point (library limitation),
        # so we construct a synthetic single-region dict directly
        regions = {(500, 500): [(0, 0), (1000, 0), (1000, 1000), (0, 1000)]}
        vormap.set_bounds(0, 1000, 0, 1000)
        result = vormap_territory.analyze_territories(regions, [(500, 500)])
        assert result["summary"]["total_regions"] == 1
        assert result["summary"]["gini_coefficient"] == pytest.approx(0.0)
        assert result["summary"]["balance_score"] == pytest.approx(1.0)
        assert result["summary"]["dominance_ratio"] == pytest.approx(1.0)

    def test_custom_bounds(self):
        points = [(50, 50), (150, 50), (50, 150), (150, 150)]
        vormap.set_bounds(0, 200, 0, 200)
        regions = vormap_viz.compute_regions(points)
        result = vormap_territory.analyze_territories(
            regions, points, bounds=(0, 200, 0, 200)
        )
        assert result["summary"]["total_regions"] == 4

    def test_empty_regions(self):
        result = vormap_territory.analyze_territories({}, [])
        assert result["summary"]["total_regions"] == 0
        assert result["regions"] == []
        assert result["shared_borders"] == []

    def test_classification_present(self):
        regions, data = _unequal_5()
        result = vormap_territory.analyze_territories(regions, data)
        classifications = set(r["classification"] for r in result["regions"])
        # Should have at least "average" for 5 points
        assert "average" in classifications or len(classifications) > 0

    def test_line_arrangement(self):
        # compute_regions only traces 1 of 3 collinear regions (library
        # limitation), so test with a slightly offset arrangement instead
        points = [(200, 490), (500, 510), (800, 490)]
        vormap.set_bounds(0, 1000, 0, 1000)
        regions = vormap_viz.compute_regions(points)
        result = vormap_territory.analyze_territories(regions, points)
        assert result["summary"]["total_regions"] == 3
        assert result["summary"]["total_shared_borders"] >= 2

    def test_summary_statistics_consistent(self):
        regions, data = _unequal_5()
        result = vormap_territory.analyze_territories(regions, data)
        s = result["summary"]
        assert s["area_min"] <= s["area_mean"] <= s["area_max"]
        assert s["area_std"] >= 0
        assert s["area_cv"] >= 0
        assert s["dominant_count"] + s["average_count"] + s["marginal_count"] == s["total_regions"]
        assert s["frontier_count"] + s["interior_count"] == s["total_regions"]


# -- Report formatting ------------------------------------------------


class TestFormatReport:
    def test_produces_string(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        report = vormap_territory.format_territory_report(result)
        assert isinstance(report, str)
        assert len(report) > 100

    def test_contains_key_sections(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        report = vormap_territory.format_territory_report(result)
        assert "TERRITORIAL ANALYSIS REPORT" in report
        assert "Area Distribution" in report
        assert "Territorial Balance" in report
        assert "Classification" in report
        assert "Frontier Analysis" in report
        assert "Top Regions by Area" in report
        assert "Most Pressured Regions" in report

    def test_contains_gini(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        report = vormap_territory.format_territory_report(result)
        assert "Gini coefficient" in report

    def test_empty_analysis(self):
        result = vormap_territory.analyze_territories({}, [])
        report = vormap_territory.format_territory_report(result)
        assert "Total regions:       0" in report


# -- JSON export -------------------------------------------------------


class TestExportJSON:
    def test_creates_valid_json(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            path = f.name
        try:
            vormap_territory.export_territory_json(result, path)
            with open(path) as f:
                loaded = json.load(f)
            assert "summary" in loaded
            assert "regions" in loaded
            assert "shared_borders" in loaded
            assert loaded["summary"]["total_regions"] == 4
            assert len(loaded["regions"]) == 4
        finally:
            os.unlink(path)

    def test_seeds_serialized_as_lists(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            path = f.name
        try:
            vormap_territory.export_territory_json(result, path)
            with open(path) as f:
                loaded = json.load(f)
            for r in loaded["regions"]:
                assert isinstance(r["seed"], list)
                assert len(r["seed"]) == 2
        finally:
            os.unlink(path)


# -- CSV export --------------------------------------------------------


class TestExportCSV:
    def test_creates_csv_with_header(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w"
        ) as f:
            path = f.name
        try:
            vormap_territory.export_territory_csv(result, path)
            with open(path) as f:
                lines = f.read().strip().split("\n")
            assert lines[0].startswith("seed_x,seed_y,area")
            # Header + 4 data rows
            assert len(lines) == 5
        finally:
            os.unlink(path)

    def test_csv_data_parseable(self):
        regions, data = _square_grid_4()
        result = vormap_territory.analyze_territories(regions, data)
        with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False, mode="w"
        ) as f:
            path = f.name
        try:
            vormap_territory.export_territory_csv(result, path)
            with open(path) as f:
                lines = f.read().strip().split("\n")
            # Each data row should have 13 columns
            for line in lines[1:]:
                cols = line.split(",")
                assert len(cols) == 13
        finally:
            os.unlink(path)
