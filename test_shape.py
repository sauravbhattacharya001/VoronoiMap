"""Tests for vormap_shape — Voronoi cell shape analysis."""

import csv
import json
import math
import os
import sys
import tempfile

import pytest

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vormap_shape import (
    analyze_cell,
    analyze_shapes,
    format_shape_report,
    export_shape_json,
    export_shape_csv,
    _classify_shape,
    _convex_hull,
    _distance,
    _mean,
    _std,
    _safe_div,
    _min_bounding_rectangle,
    _point_near_boundary,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

# A square cell (compact-ish, elongation ~1.0)
SQUARE_VERTS = [(0, 0), (10, 0), (10, 10), (0, 10)]
SQUARE_SEED = (5.0, 5.0)

# A narrow rectangle (elongated)
NARROW_VERTS = [(0, 0), (20, 0), (20, 2), (0, 2)]
NARROW_SEED = (10.0, 1.0)

# A regular hexagon (very compact)
def _hexagon(cx, cy, r):
    return [
        (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
        for a in range(0, 360, 60)
    ]

HEX_VERTS = _hexagon(50, 50, 10)
HEX_SEED = (50.0, 50.0)

# A triangle (moderate compactness)
TRIANGLE_VERTS = [(0, 0), (10, 0), (5, 8.66)]
TRIANGLE_SEED = (5.0, 2.89)

# Degenerate: too few vertices
DEGEN_VERTS = [(0, 0), (1, 1)]
DEGEN_SEED = (0.5, 0.5)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_distance(self):
        assert _distance((0, 0), (3, 4)) == pytest.approx(5.0)
        assert _distance((1, 1), (1, 1)) == 0.0

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_mean_values(self):
        assert _mean([2, 4, 6]) == pytest.approx(4.0)

    def test_std_single(self):
        assert _std([42]) == 0.0

    def test_std_values(self):
        assert _std([2, 4, 6]) == pytest.approx(1.6329931618, rel=1e-4)

    def test_safe_div_normal(self):
        assert _safe_div(10.0, 2.0) == pytest.approx(5.0)

    def test_safe_div_zero(self):
        assert _safe_div(10.0, 0.0) == 0.0

    def test_classify_compact(self):
        assert _classify_shape(0.85) == "compact"

    def test_classify_regular(self):
        assert _classify_shape(0.65) == "regular"

    def test_classify_moderate(self):
        assert _classify_shape(0.45) == "moderate"

    def test_classify_elongated(self):
        assert _classify_shape(0.25) == "elongated"

    def test_classify_highly_elongated(self):
        assert _classify_shape(0.10) == "highly_elongated"

    def test_point_near_boundary_true(self):
        assert _point_near_boundary((0.5, 50), (0, 100, 0, 100), 1.0)

    def test_point_near_boundary_false(self):
        assert not _point_near_boundary((50, 50), (0, 100, 0, 100), 1.0)


# ---------------------------------------------------------------------------
# Convex hull tests
# ---------------------------------------------------------------------------

class TestConvexHull:
    def test_square_hull(self):
        hull = _convex_hull([(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)])
        assert len(hull) == 4  # Interior point excluded

    def test_collinear_points(self):
        hull = _convex_hull([(0, 0), (1, 1), (2, 2)])
        assert len(hull) >= 2

    def test_two_points(self):
        hull = _convex_hull([(0, 0), (1, 1)])
        assert len(hull) == 2

    def test_single_point(self):
        hull = _convex_hull([(5, 5)])
        assert len(hull) == 1


# ---------------------------------------------------------------------------
# Minimum bounding rectangle tests
# ---------------------------------------------------------------------------

class TestMBR:
    def test_square_mbr(self):
        mbr = _min_bounding_rectangle(SQUARE_VERTS)
        assert mbr["width"] == pytest.approx(10.0, rel=0.01)
        assert mbr["height"] == pytest.approx(10.0, rel=0.01)
        assert mbr["area"] == pytest.approx(100.0, rel=0.01)

    def test_narrow_mbr(self):
        mbr = _min_bounding_rectangle(NARROW_VERTS)
        assert mbr["width"] == pytest.approx(20.0, rel=0.01)
        assert mbr["height"] == pytest.approx(2.0, rel=0.01)

    def test_degenerate_mbr(self):
        mbr = _min_bounding_rectangle(DEGEN_VERTS)
        assert mbr["area"] == 0.0

    def test_angle_range(self):
        mbr = _min_bounding_rectangle(HEX_VERTS)
        assert 0.0 <= mbr["angle"] < 180.0


# ---------------------------------------------------------------------------
# Per-cell analysis tests
# ---------------------------------------------------------------------------

class TestAnalyzeCell:
    def test_square_compactness(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        # Square IPQ = 4π×100 / 40² ≈ 0.785
        assert cell["compactness"] == pytest.approx(math.pi / 4, rel=0.01)

    def test_square_elongation(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert cell["elongation"] == pytest.approx(1.0, rel=0.01)

    def test_narrow_elongation(self):
        cell = analyze_cell(NARROW_SEED, NARROW_VERTS)
        assert cell["elongation"] == pytest.approx(10.0, rel=0.01)

    def test_hexagon_compactness(self):
        cell = analyze_cell(HEX_SEED, HEX_VERTS)
        # Regular hexagon IPQ ≈ 0.9069
        assert cell["compactness"] > 0.85

    def test_hexagon_shape_class(self):
        cell = analyze_cell(HEX_SEED, HEX_VERTS)
        assert cell["shape_class"] == "compact"

    def test_narrow_shape_class(self):
        cell = analyze_cell(NARROW_SEED, NARROW_VERTS)
        assert cell["shape_class"] in ("elongated", "highly_elongated", "moderate")

    def test_shape_index_circle_bound(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        # Shape index >= 1.0 for all shapes
        assert cell["shape_index"] >= 1.0

    def test_rectangularity_square(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert cell["rectangularity"] == pytest.approx(1.0, rel=0.01)

    def test_rectangularity_bounded(self):
        cell = analyze_cell(HEX_SEED, HEX_VERTS)
        assert 0.0 < cell["rectangularity"] <= 1.0

    def test_centroid_displacement_centered(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        # Seed is at centroid, displacement ~0
        assert cell["centroid_displacement"] == pytest.approx(0.0, abs=0.01)

    def test_centroid_displacement_offset(self):
        cell = analyze_cell((0.0, 0.0), SQUARE_VERTS)
        # Seed is at corner, displacement > 0
        assert cell["centroid_displacement"] > 0.5

    def test_orientation_range(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert 0.0 <= cell["orientation"] < 180.0

    def test_n_vertices(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert cell["n_vertices"] == 4

    def test_area_positive(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert cell["area"] == pytest.approx(100.0, rel=0.01)

    def test_perimeter_positive(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert cell["perimeter"] == pytest.approx(40.0, rel=0.01)

    def test_seed_preserved(self):
        cell = analyze_cell(SQUARE_SEED, SQUARE_VERTS)
        assert cell["seed"] == [5.0, 5.0]

    def test_triangle_cell(self):
        cell = analyze_cell(TRIANGLE_SEED, TRIANGLE_VERTS)
        assert cell["n_vertices"] == 3
        assert cell["compactness"] > 0

    def test_hexagon_elongation_near_one(self):
        cell = analyze_cell(HEX_SEED, HEX_VERTS)
        # Regular hexagon is nearly square in bounding box
        assert cell["elongation"] < 1.3


# ---------------------------------------------------------------------------
# Aggregate analysis tests
# ---------------------------------------------------------------------------

def _make_regions():
    """Create a small set of test regions."""
    return {
        SQUARE_SEED: SQUARE_VERTS,
        NARROW_SEED: NARROW_VERTS,
        HEX_SEED: HEX_VERTS,
        TRIANGLE_SEED: TRIANGLE_VERTS,
    }


class TestAnalyzeShapes:
    def test_cell_count(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert result["summary"]["cell_count"] == 4

    def test_cells_list(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert len(result["cells"]) == 4

    def test_distribution_sums(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        total = sum(result["distribution"].values())
        assert total == 4

    def test_summary_metrics_present(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        for m in ["compactness", "shape_index", "elongation",
                   "rectangularity", "centroid_displacement"]:
            assert m in result["summary"]
            assert "mean" in result["summary"][m]
            assert "std" in result["summary"][m]

    def test_most_compact_seed(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert result["summary"]["most_compact_seed"] is not None

    def test_most_elongated_seed(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert result["summary"]["most_elongated_seed"] is not None

    def test_empty_regions(self):
        result = analyze_shapes({}, bounds=(0, 100, 0, 100))
        assert result["summary"]["cell_count"] == 0
        assert result["cells"] == []

    def test_exclude_boundary(self):
        # Square and narrow cells are inside, hex is inside
        # Triangle seed at (5, 2.89) — vertices at boundary (0,0), (10,0)
        regions = _make_regions()
        result = analyze_shapes(
            regions, bounds=(0, 100, 0, 100),
            exclude_boundary=True, boundary_tolerance=0.5,
        )
        # Some cells should be excluded (those with vertices at x=0 or y=0)
        assert result["summary"]["cell_count"] < 4

    def test_mean_orientation(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert "mean_orientation" in result["summary"]

    def test_orientation_std(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert "orientation_std" in result["summary"]

    def test_mean_n_vertices(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert result["summary"]["mean_n_vertices"] > 0

    def test_degenerate_cells_skipped(self):
        regions = {DEGEN_SEED: DEGEN_VERTS}
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        assert result["summary"]["cell_count"] == 0


# ---------------------------------------------------------------------------
# Report tests
# ---------------------------------------------------------------------------

class TestReport:
    def test_report_contains_header(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        report = format_shape_report(result)
        assert "SHAPE ANALYSIS" in report

    def test_report_contains_metrics(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        report = format_shape_report(result)
        assert "Compactness" in report
        assert "Elongation" in report
        assert "Shape Index" in report

    def test_report_contains_distribution(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        report = format_shape_report(result)
        assert "Shape Class Distribution" in report

    def test_empty_report(self):
        result = analyze_shapes({}, bounds=(0, 100, 0, 100))
        report = format_shape_report(result)
        assert "No cells" in report

    def test_report_most_compact(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        report = format_shape_report(result)
        assert "Most compact" in report

    def test_report_orientation(self):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        report = format_shape_report(result)
        assert "orientation" in report.lower()


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------

class TestExportJSON:
    def test_json_roundtrip(self, tmp_path):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        path = str(tmp_path / "shapes.json")
        export_shape_json(result, path)

        with open(path) as f:
            loaded = json.load(f)
        assert loaded["summary"]["cell_count"] == 4
        assert len(loaded["cells"]) == 4

    def test_json_cell_fields(self, tmp_path):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        path = str(tmp_path / "shapes.json")
        export_shape_json(result, path)

        with open(path) as f:
            loaded = json.load(f)
        cell = loaded["cells"][0]
        for field in ["seed", "area", "compactness", "shape_index",
                       "elongation", "rectangularity", "shape_class"]:
            assert field in cell


class TestExportCSV:
    def test_csv_row_count(self, tmp_path):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        path = str(tmp_path / "shapes.csv")
        export_shape_csv(result, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 4

    def test_csv_columns(self, tmp_path):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        path = str(tmp_path / "shapes.csv")
        export_shape_csv(result, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert "seed_x" in row
        assert "compactness" in row
        assert "shape_class" in row

    def test_csv_empty_noop(self, tmp_path):
        result = analyze_shapes({}, bounds=(0, 100, 0, 100))
        path = str(tmp_path / "empty.csv")
        export_shape_csv(result, path)
        assert not os.path.exists(path)

    def test_csv_values_numeric(self, tmp_path):
        regions = _make_regions()
        result = analyze_shapes(regions, bounds=(0, 100, 0, 100))
        path = str(tmp_path / "shapes.csv")
        export_shape_csv(result, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                assert float(row["compactness"]) >= 0
                assert float(row["elongation"]) >= 1.0 or float(row["elongation"]) >= 0
