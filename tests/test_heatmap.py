"""Tests for vormap_heatmap module."""

import math
import os
import tempfile

import pytest

import vormap_heatmap


# ── Test fixtures ────────────────────────────────────────────────────

def _sample_regions():
    """Create simple sample regions for testing."""
    # Three triangular regions
    regions = {
        (0.0, 0.0): [(0, 0), (100, 0), (50, 100)],
        (200.0, 200.0): [(150, 150), (250, 150), (200, 250)],
        (400.0, 100.0): [(350, 50), (450, 50), (400, 200)],
    }
    data = [(0.0, 0.0), (200.0, 200.0), (400.0, 100.0)]
    return regions, data


# ── Metric computation tests ────────────────────────────────────────

class TestMetrics:
    def test_density_metric(self):
        verts = [(0, 0), (100, 0), (100, 100), (0, 100)]  # area = 10000
        val = vormap_heatmap._compute_metric(verts, "density")
        assert abs(val - 1.0 / 10000) < 1e-10

    def test_area_metric(self):
        verts = [(0, 0), (100, 0), (100, 100), (0, 100)]
        val = vormap_heatmap._compute_metric(verts, "area")
        assert abs(val - 10000.0) < 0.01

    def test_compactness_metric(self):
        # Square: compactness = 4*pi*10000 / (400^2) = pi/4 ≈ 0.785
        verts = [(0, 0), (100, 0), (100, 100), (0, 100)]
        val = vormap_heatmap._compute_metric(verts, "compactness")
        assert abs(val - math.pi / 4) < 0.01

    def test_vertices_metric(self):
        verts = [(0, 0), (100, 0), (100, 100), (0, 100)]
        val = vormap_heatmap._compute_metric(verts, "vertices")
        assert val == 4.0

    def test_unknown_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            vormap_heatmap._compute_metric([], "bogus")


# ── Color ramp tests ────────────────────────────────────────────────

class TestColorRamps:
    def test_hot_cold_endpoints(self):
        blue = vormap_heatmap._hot_cold(0.0)
        red = vormap_heatmap._hot_cold(1.0)
        assert blue == "#0000ff"
        assert red == "#ff0000"

    def test_hot_cold_midpoint(self):
        white = vormap_heatmap._hot_cold(0.5)
        assert white == "#ffffff"

    def test_all_ramps_return_hex(self):
        for name, fn in vormap_heatmap._HEATMAP_RAMPS.items():
            for t in (0.0, 0.25, 0.5, 0.75, 1.0):
                color = fn(t)
                assert color.startswith("#"), f"{name}({t}) = {color}"
                assert len(color) == 7, f"{name}({t}) = {color}"


# ── SVG export tests ────────────────────────────────────────────────

class TestSVGExport:
    def test_basic_svg_export(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(regions, data, path)
            content = open(path).read()
            assert "<svg" in content
            assert "heatmap-regions" in content
            assert "legend" in content
        finally:
            os.unlink(path)

    def test_svg_with_values(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(
                regions, data, path, show_values=True, metric="area"
            )
            content = open(path).read()
            assert "value-labels" in content
        finally:
            os.unlink(path)

    def test_svg_all_ramps(self):
        regions, data = _sample_regions()
        for ramp in ("hot_cold", "viridis", "plasma"):
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
                path = f.name
            try:
                vormap_heatmap.export_heatmap_svg(
                    regions, data, path, color_ramp=ramp
                )
                assert os.path.getsize(path) > 100
            finally:
                os.unlink(path)

    def test_invalid_ramp_raises(self):
        regions, data = _sample_regions()
        with pytest.raises(ValueError, match="Unknown color ramp"):
            vormap_heatmap.export_heatmap_svg(
                regions, data, "out.svg", color_ramp="nope"
            )


# ── HTML export tests ───────────────────────────────────────────────

class TestHTMLExport:
    def test_basic_html_export(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            assert "metric-select" in content
            assert "ramp-select" in content
            assert "hotCold" in content
        finally:
            os.unlink(path)
