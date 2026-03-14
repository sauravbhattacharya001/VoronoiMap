"""Tests for vormap_landscape — Landscape Ecology Metrics."""

import json
import math
import os
import tempfile

import pytest

import vormap
import vormap_viz
import vormap_landscape


# -- Fixtures ----------------------------------------------------------

def _setup_4_classes():
    """4 points, each assigned a different class."""
    points = [(250, 250), (750, 250), (250, 750), (750, 750)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    classes = {
        (250, 250): "forest",
        (750, 250): "urban",
        (250, 750): "water",
        (750, 750): "farm",
    }
    return regions, points, classes


def _setup_2_classes():
    """4 points, alternating between 2 classes."""
    points = [(250, 250), (750, 250), (250, 750), (750, 750)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    classes = {
        (250, 250): "forest",
        (750, 250): "urban",
        (250, 750): "forest",
        (750, 750): "urban",
    }
    return regions, points, classes


def _setup_single_class():
    """4 points, all same class."""
    points = [(250, 250), (750, 250), (250, 750), (750, 750)]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    classes = {pt: "forest" for pt in points}
    return regions, points, classes


def _setup_many_classes():
    """9 points in a grid with 3 classes in patterns."""
    points = [
        (167, 167), (500, 167), (833, 167),
        (167, 500), (500, 500), (833, 500),
        (167, 833), (500, 833), (833, 833),
    ]
    vormap.set_bounds(0, 1000, 0, 1000)
    regions = vormap_viz.compute_regions(points)
    labels = ["forest", "urban", "water"] * 3
    classes = {pt: labels[i] for i, pt in enumerate(points)}
    return regions, points, classes


# -- analyze_landscape -------------------------------------------------

class TestAnalyzeLandscape:
    def test_returns_analysis_object(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert isinstance(result, vormap_landscape.LandscapeAnalysis)

    def test_patch_count_matches_regions(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert len(result.patch_metrics) == len(regions)

    def test_class_count_matches_unique_classes(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert len(result.class_metrics) == 4

    def test_two_classes(self):
        regions, data, classes = _setup_2_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert len(result.class_metrics) == 2
        assert result.landscape_metrics.num_classes == 2

    def test_single_class(self):
        regions, data, classes = _setup_single_class()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.landscape_metrics.num_classes == 1
        cm = result.class_metrics["forest"]
        assert cm.patch_count == 4
        assert abs(cm.percent_landscape - 100.0) < 0.5


# -- Patch-level metrics -----------------------------------------------

class TestPatchMetrics:
    def test_areas_are_positive(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert pm.area > 0

    def test_shape_index_at_least_one(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert pm.shape_index >= 1.0

    def test_core_area_at_most_area(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert pm.core_area <= pm.area + 0.01

    def test_core_area_index_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert 0.0 <= pm.core_area_index <= 1.0

    def test_edge_contrast_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert 0.0 <= pm.edge_contrast <= 1.0

    def test_fractal_dimension_reasonable(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert 1.0 <= pm.fractal_dimension <= 2.5

    def test_compactness_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert 0.0 <= pm.compactness <= 1.01

    def test_all_same_class_zero_edge_contrast(self):
        regions, data, classes = _setup_single_class()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert pm.edge_contrast == 0.0

    def test_class_neighbors_at_most_neighbors(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for pm in result.patch_metrics:
            assert pm.class_neighbors <= pm.neighbors


# -- Class-level metrics ------------------------------------------------

class TestClassMetrics:
    def test_percent_landscape_sums_to_100(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        total = sum(cm.percent_landscape for cm in result.class_metrics.values())
        assert abs(total - 100.0) < 1.0

    def test_largest_patch_index_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for cm in result.class_metrics.values():
            assert 0 < cm.largest_patch_index <= 100

    def test_cohesion_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for cm in result.class_metrics.values():
            assert 0 <= cm.cohesion <= 100.1

    def test_aggregation_same_class(self):
        regions, data, classes = _setup_single_class()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        cm = result.class_metrics["forest"]
        assert cm.aggregation_index == 100.0

    def test_patch_density_positive(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for cm in result.class_metrics.values():
            assert cm.patch_density > 0

    def test_mean_shape_positive(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for cm in result.class_metrics.values():
            assert cm.mean_shape_index > 0

    def test_std_patch_area_nonneg(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        for cm in result.class_metrics.values():
            assert cm.std_patch_area >= 0


# -- Landscape-level metrics -------------------------------------------

class TestLandscapeMetrics:
    def test_total_area_positive(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.landscape_metrics.total_area > 0

    def test_shannon_diversity_equal_classes(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        expected = math.log(4)
        assert abs(result.landscape_metrics.shannon_diversity - expected) < 0.2

    def test_shannon_diversity_single_class_zero(self):
        regions, data, classes = _setup_single_class()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert abs(result.landscape_metrics.shannon_diversity) < 0.01

    def test_simpson_diversity_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert 0 <= result.landscape_metrics.simpson_diversity <= 1

    def test_evenness_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert 0 <= result.landscape_metrics.evenness <= 1.01

    def test_evenness_high_for_equal_classes(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.landscape_metrics.evenness > 0.8

    def test_contagion_bounded(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert 0 <= result.landscape_metrics.contagion <= 100

    def test_contagion_high_for_single_class(self):
        regions, data, classes = _setup_single_class()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.landscape_metrics.contagion >= 90

    def test_dominance_nonneg(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.landscape_metrics.dominance >= 0

    def test_edge_density_positive(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.landscape_metrics.edge_density > 0


# -- Fragmentation summary ---------------------------------------------

class TestFragmentation:
    def test_fragmentation_keys(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        fs = result.fragmentation_summary
        assert "level" in fs
        assert "diversity" in fs
        assert "shape_complexity" in fs
        assert "core_preservation" in fs
        assert "spatial_pattern" in fs

    def test_single_class_low_diversity(self):
        regions, data, classes = _setup_single_class()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.fragmentation_summary["diversity"] == "Low"

    def test_many_classes_higher_diversity(self):
        regions, data, classes = _setup_many_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        assert result.fragmentation_summary["diversity"] in ("Moderate", "High")


# -- Format report ------------------------------------------------------

class TestFormatReport:
    def test_report_is_string(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        report = vormap_landscape.format_landscape_report(result)
        assert isinstance(report, str)
        assert "LANDSCAPE ECOLOGY" in report

    def test_report_contains_classes(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        report = vormap_landscape.format_landscape_report(result)
        assert "[forest]" in report
        assert "[urban]" in report

    def test_report_contains_metrics(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        report = vormap_landscape.format_landscape_report(result)
        assert "Shannon" in report
        assert "Simpson" in report
        assert "Contagion" in report


# -- JSON export --------------------------------------------------------

class TestJsonExport:
    def test_export_creates_file(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_landscape.export_landscape_json(result, path)
            assert os.path.exists(path)
            with open(path) as f:
                loaded = json.load(f)
            assert "landscape" in loaded
            assert "classes" in loaded
            assert "patches" in loaded
            assert "fragmentation" in loaded
        finally:
            os.unlink(path)

    def test_json_class_count(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_landscape.export_landscape_json(result, path)
            with open(path) as f:
                loaded = json.load(f)
            assert len(loaded["classes"]) == 4
        finally:
            os.unlink(path)

    def test_json_patch_count(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_landscape.export_landscape_json(result, path)
            with open(path) as f:
                loaded = json.load(f)
            assert len(loaded["patches"]) == 4
        finally:
            os.unlink(path)


# -- CSV export ---------------------------------------------------------

class TestCsvExport:
    def test_export_creates_file(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_landscape.export_landscape_csv(result, path)
            assert os.path.exists(path)
            with open(path) as f:
                lines = f.readlines()
            assert len(lines) == 5  # header + 4 rows
            assert "seed_x" in lines[0]
        finally:
            os.unlink(path)

    def test_csv_columns(self):
        regions, data, classes = _setup_4_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_landscape.export_landscape_csv(result, path)
            with open(path) as f:
                header = f.readline().strip()
            cols = header.split(",")
            assert "class" in cols
            assert "shape_index" in cols
            assert "core_area" in cols
        finally:
            os.unlink(path)


# -- Many-class integration -------------------------------------------

class TestManyClasses:
    def test_nine_points_three_classes(self):
        regions, data, classes = _setup_many_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        lm = result.landscape_metrics
        assert lm.num_patches == 9
        assert lm.num_classes == 3
        for cm in result.class_metrics.values():
            assert cm.patch_count == 3

    def test_percent_sums_to_100(self):
        regions, data, classes = _setup_many_classes()
        result = vormap_landscape.analyze_landscape(regions, data, classes)
        total = sum(cm.percent_landscape for cm in result.class_metrics.values())
        assert abs(total - 100.0) < 1.0


# -- Helper function tests ---------------------------------------------

class TestHelpers:
    def test_shape_index_circle_like(self):
        r = 100
        area = math.pi * r * r
        perim = 2 * math.pi * r
        si = vormap_landscape._shape_index(area, perim)
        assert abs(si - 1.0) < 0.01

    def test_shape_index_zero_area(self):
        assert vormap_landscape._shape_index(0, 100) == 0.0

    def test_fractal_dim_zero_area(self):
        assert vormap_landscape._fractal_dimension(0, 100) == 0.0

    def test_fractal_dim_square_patch(self):
        """A perfect square (area=100, perimeter=40) should give FRAC=1.0."""
        import math
        result = vormap_landscape._fractal_dimension(100, 40)
        expected = 2 * math.log(0.25 * 40) / math.log(100)  # 1.0
        assert abs(result - expected) < 1e-10
        assert abs(result - 1.0) < 1e-10

    def test_core_area_zero_depth(self):
        area = 10000
        perim = 400
        assert vormap_landscape._core_area(area, perim, 0) == area

    def test_core_area_large_depth_clamped(self):
        # With huge depth, the pi*depth^2 term dominates and result
        # is clamped to [0, area]
        result = vormap_landscape._core_area(100, 40, 1000)
        assert 0 <= result <= 100

    def test_core_area_moderate_depth_reduces(self):
        # Moderate depth should reduce core area
        area = 10000
        perim = 400
        result = vormap_landscape._core_area(area, perim, 10)
        assert result < area

    def test_fragmentation_level_low(self):
        level = vormap_landscape._fragmentation_level(0.3, 1.1, 0.8)
        assert level == "Low"

    def test_fragmentation_level_high(self):
        level = vormap_landscape._fragmentation_level(0.9, 1.8, 0.2)
        assert level == "High"
