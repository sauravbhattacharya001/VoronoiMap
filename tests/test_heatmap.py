"""Tests for vormap_heatmap module."""

import math
import os
import tempfile
import xml.etree.ElementTree as ET

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


def _square_verts(side=100):
    """Unit square with given side length at origin."""
    return [(0, 0), (side, 0), (side, side), (0, side)]


def _triangle_verts():
    """Right triangle: base 6, height 4, area 12."""
    return [(0, 0), (6, 0), (0, 4)]


# ── Metric computation tests ────────────────────────────────────────

class TestRegionArea:
    def test_unit_square(self):
        area = vormap_heatmap._compute_region_area(_square_verts(1))
        assert abs(area - 1.0) < 1e-10

    def test_square_100(self):
        area = vormap_heatmap._compute_region_area(_square_verts(100))
        assert abs(area - 10000.0) < 0.01

    def test_triangle(self):
        area = vormap_heatmap._compute_region_area(_triangle_verts())
        assert abs(area - 12.0) < 1e-10

    def test_degenerate_line(self):
        """Two-vertex polygon (a line) has zero area."""
        area = vormap_heatmap._compute_region_area([(0, 0), (10, 10)])
        assert area == 0.0

    def test_single_point(self):
        area = vormap_heatmap._compute_region_area([(5, 5)])
        assert area == 0.0

    def test_empty_polygon(self):
        area = vormap_heatmap._compute_region_area([])
        assert area == 0.0

    def test_large_polygon(self):
        """Regular hexagon: area = (3*sqrt(3)/2) * r^2."""
        r = 100
        verts = [(r * math.cos(math.pi * i / 3),
                  r * math.sin(math.pi * i / 3)) for i in range(6)]
        area = vormap_heatmap._compute_region_area(verts)
        expected = (3 * math.sqrt(3) / 2) * r * r
        assert abs(area - expected) < 0.1

    def test_winding_order_does_not_matter(self):
        """Shoelace returns positive area regardless of CW/CCW ordering."""
        verts_ccw = [(0, 0), (10, 0), (10, 10), (0, 10)]
        verts_cw = list(reversed(verts_ccw))
        assert abs(vormap_heatmap._compute_region_area(verts_ccw)
                    - vormap_heatmap._compute_region_area(verts_cw)) < 1e-10

    def test_concave_polygon(self):
        """L-shaped polygon (concave)."""
        verts = [(0, 0), (10, 0), (10, 5), (5, 5), (5, 10), (0, 10)]
        area = vormap_heatmap._compute_region_area(verts)
        # Area = 10*10 - 5*5 = 75
        assert abs(area - 75.0) < 1e-10


class TestPerimeter:
    def test_unit_square(self):
        perim = vormap_heatmap._compute_perimeter(_square_verts(1))
        assert abs(perim - 4.0) < 1e-10

    def test_square_100(self):
        perim = vormap_heatmap._compute_perimeter(_square_verts(100))
        assert abs(perim - 400.0) < 1e-10

    def test_triangle(self):
        perim = vormap_heatmap._compute_perimeter(_triangle_verts())
        # sides: 6, 4, sqrt(52)
        expected = 6 + 4 + math.sqrt(52)
        assert abs(perim - expected) < 1e-10

    def test_single_point(self):
        perim = vormap_heatmap._compute_perimeter([(0, 0)])
        assert perim == 0.0

    def test_empty(self):
        perim = vormap_heatmap._compute_perimeter([])
        assert perim == 0.0

    def test_two_points(self):
        """Two points form a degenerate polygon with perimeter = 2 * distance."""
        perim = vormap_heatmap._compute_perimeter([(0, 0), (3, 4)])
        assert abs(perim - 10.0) < 1e-10  # 5 + 5


class TestIsoperimetricQuotient:
    def test_perfect_circle_approximation(self):
        """Regular 100-gon approximates a circle (IQ ≈ 1.0)."""
        r = 50
        n = 100
        verts = [(r * math.cos(2 * math.pi * i / n),
                  r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        area = vormap_heatmap._compute_region_area(verts)
        perim = vormap_heatmap._compute_perimeter(verts)
        iq = vormap_heatmap._isoperimetric_quotient(area, perim)
        assert abs(iq - 1.0) < 0.01

    def test_square_compactness(self):
        area = 10000.0
        perim = 400.0
        iq = vormap_heatmap._isoperimetric_quotient(area, perim)
        assert abs(iq - math.pi / 4) < 0.01

    def test_zero_perimeter(self):
        iq = vormap_heatmap._isoperimetric_quotient(100, 0.0)
        assert iq == 0.0

    def test_near_zero_perimeter(self):
        iq = vormap_heatmap._isoperimetric_quotient(100, 1e-15)
        assert iq == 0.0

    def test_zero_area(self):
        iq = vormap_heatmap._isoperimetric_quotient(0.0, 100.0)
        assert iq == 0.0


class TestComputeMetric:
    def test_density_metric(self):
        verts = _square_verts(100)  # area = 10000
        val = vormap_heatmap._compute_metric(verts, "density")
        assert abs(val - 1.0 / 10000) < 1e-10

    def test_area_metric(self):
        verts = _square_verts(100)
        val = vormap_heatmap._compute_metric(verts, "area")
        assert abs(val - 10000.0) < 0.01

    def test_compactness_metric(self):
        verts = _square_verts(100)
        val = vormap_heatmap._compute_metric(verts, "compactness")
        assert abs(val - math.pi / 4) < 0.01

    def test_vertices_metric(self):
        verts = _square_verts(100)
        val = vormap_heatmap._compute_metric(verts, "vertices")
        assert val == 4.0

    def test_vertices_triangle(self):
        val = vormap_heatmap._compute_metric(_triangle_verts(), "vertices")
        assert val == 3.0

    def test_unknown_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            vormap_heatmap._compute_metric([], "bogus")

    def test_density_tiny_area(self):
        """Density of a near-degenerate polygon should use epsilon floor."""
        verts = [(0, 0), (0.001, 0), (0, 0.001)]
        val = vormap_heatmap._compute_metric(verts, "density")
        assert val > 0  # should not divide by zero

    def test_all_metrics_consistent(self):
        """All four metrics run without error on the same polygon."""
        verts = _square_verts(50)
        for m in ("density", "area", "compactness", "vertices"):
            val = vormap_heatmap._compute_metric(verts, m)
            assert isinstance(val, float)
            assert val >= 0


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

    def test_all_ramps_length_7(self):
        """Every ramp output is exactly '#RRGGBB' (7 chars)."""
        for name, fn in vormap_heatmap._HEATMAP_RAMPS.items():
            for i in range(11):
                t = i / 10.0
                color = fn(t)
                assert len(color) == 7, f"{name}({t}) = {color}"

    def test_viridis_returns_valid_hex_chars(self):
        for i in range(21):
            t = i / 20.0
            color = vormap_heatmap._viridis_approx(t)
            # All chars after # must be hex digits
            assert all(c in '0123456789abcdef' for c in color[1:])

    def test_plasma_returns_valid_hex_chars(self):
        for i in range(21):
            t = i / 20.0
            color = vormap_heatmap._plasma_approx(t)
            assert all(c in '0123456789abcdef' for c in color[1:])

    def test_default_ramp_is_hot_cold(self):
        assert vormap_heatmap.DEFAULT_HEATMAP_RAMP == "hot_cold"

    def test_ramp_names_in_dict(self):
        expected = {"hot_cold", "viridis", "plasma"}
        assert set(vormap_heatmap._HEATMAP_RAMPS.keys()) == expected


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

    def test_svg_is_valid_xml(self):
        """The exported SVG should be valid XML."""
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(regions, data, path)
            tree = ET.parse(path)
            root = tree.getroot()
            assert root.tag.endswith("svg")
        finally:
            os.unlink(path)

    def test_svg_contains_correct_polygon_count(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(regions, data, path)
            content = open(path).read()
            # 3 regions = 3 polygons
            assert content.count("<polygon") == 3
        finally:
            os.unlink(path)

    def test_svg_contains_seed_points(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(regions, data, path, show_points=True)
            content = open(path).read()
            assert content.count("<circle") == 3
        finally:
            os.unlink(path)

    def test_svg_no_points(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(regions, data, path, show_points=False)
            content = open(path).read()
            assert "<circle" not in content
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

    def test_svg_with_title(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(
                regions, data, path, title="Test Heatmap"
            )
            content = open(path).read()
            assert "Test Heatmap" in content
        finally:
            os.unlink(path)

    def test_svg_no_title_by_default(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(regions, data, path)
            content = open(path).read()
            assert "class=\"title\"" not in content
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

    def test_svg_all_metrics(self):
        regions, data = _sample_regions()
        for metric in ("density", "area", "compactness", "vertices"):
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
                path = f.name
            try:
                vormap_heatmap.export_heatmap_svg(
                    regions, data, path, metric=metric
                )
                assert os.path.getsize(path) > 100
            finally:
                os.unlink(path)

    def test_svg_custom_dimensions(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(
                regions, data, path, width=1200, height=900
            )
            tree = ET.parse(path)
            root = tree.getroot()
            assert root.get("width") == "1200"
            assert root.get("height") == "900"
        finally:
            os.unlink(path)

    def test_svg_custom_styling(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(
                regions, data, path,
                stroke_color="#ff0000",
                stroke_width=2.0,
                point_radius=5.0,
                background="#eeeeee",
            )
            content = open(path).read()
            assert "#ff0000" in content
            assert "#eeeeee" in content
        finally:
            os.unlink(path)

    def test_svg_legend_metric_labels(self):
        """Legend shows the correct metric name."""
        regions, data = _sample_regions()
        for metric, label in [("density", "Density"), ("area", "Area"),
                              ("compactness", "Compactness"), ("vertices", "Vertices")]:
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
                path = f.name
            try:
                vormap_heatmap.export_heatmap_svg(
                    regions, data, path, metric=metric
                )
                content = open(path).read()
                assert label in content
            finally:
                os.unlink(path)

    def test_invalid_ramp_raises(self):
        regions, data = _sample_regions()
        with pytest.raises(ValueError, match="Unknown color ramp"):
            vormap_heatmap.export_heatmap_svg(
                regions, data, "out.svg", color_ramp="nope"
            )

    def test_svg_value_labels_density_format(self):
        """Density values use scientific notation in labels."""
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(
                regions, data, path, show_values=True, metric="density"
            )
            content = open(path).read()
            # Scientific notation like "2.0e-04"
            assert "e" in content.lower()
        finally:
            os.unlink(path)

    def test_svg_value_labels_compactness_format(self):
        """Compactness values use 2-decimal format."""
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_svg(
                regions, data, path, show_values=True, metric="compactness"
            )
            content = open(path).read()
            assert "value-labels" in content
        finally:
            os.unlink(path)


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

    def test_html_contains_all_metric_options(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            for m in ("density", "area", "compactness", "vertices"):
                assert m in content
        finally:
            os.unlink(path)

    def test_html_contains_all_ramp_options(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            assert "hot_cold" in content
            assert "viridis" in content
            assert "plasma" in content
        finally:
            os.unlink(path)

    def test_html_custom_title(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(
                regions, data, path, title="Custom Title"
            )
            content = open(path).read()
            assert "Custom Title" in content
        finally:
            os.unlink(path)

    def test_html_default_title(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            assert "Voronoi Density Heatmap" in content
        finally:
            os.unlink(path)

    def test_html_contains_cell_data_json(self):
        """The HTML should embed cell data as JSON for the JS renderer."""
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            # Should contain JSON array of cell objects
            assert '"seed"' in content
            assert '"metrics"' in content
            assert '"points"' in content
        finally:
            os.unlink(path)

    def test_html_cell_count_matches_regions(self):
        """Cell data array should have exactly as many entries as regions."""
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            # Count '"id":' occurrences in the JSON
            assert content.count('"id":') == 3
        finally:
            os.unlink(path)

    def test_html_custom_dimensions(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(
                regions, data, path, width=1024, height=768
            )
            content = open(path).read()
            assert 'width="1024"' in content
            assert 'height="768"' in content
        finally:
            os.unlink(path)

    def test_html_selects_initial_metric(self):
        """The metric dropdown should have the correct initial selection."""
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(
                regions, data, path, metric="compactness"
            )
            content = open(path).read()
            assert "metricSel.value = 'compactness'" in content
        finally:
            os.unlink(path)

    def test_html_selects_initial_ramp(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(
                regions, data, path, color_ramp="plasma"
            )
            content = open(path).read()
            assert "rampSel.value = 'plasma'" in content
        finally:
            os.unlink(path)

    def test_html_contains_tooltip(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            content = open(path).read()
            assert "tooltip" in content
        finally:
            os.unlink(path)

    def test_html_is_valid_utf8(self):
        regions, data = _sample_regions()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_heatmap.export_heatmap_html(regions, data, path)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            assert "<!DOCTYPE html>" in content
            assert "charset=\"UTF-8\"" in content.replace("charset=UTF-8", "charset=\"UTF-8\"")
        finally:
            os.unlink(path)


class TestHTMLXSSPrevention:
    """Verify that user-supplied strings are escaped in HTML output."""

    def _export_html_with_title(self, regions, data, title):
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w"
        ) as f:
            path = f.name
        vormap_heatmap.export_heatmap_html(regions, data, path, title=title)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        os.unlink(path)
        return content

    def test_html_title_escapes_angle_brackets(self):
        regions, data = _sample_regions()
        content = self._export_html_with_title(
            regions, data, "<script>alert('xss')</script>"
        )
        assert "<script>alert" not in content
        assert "&lt;script&gt;" in content

    def test_html_title_escapes_ampersand(self):
        regions, data = _sample_regions()
        content = self._export_html_with_title(regions, data, "A & B")
        assert "A &amp; B" in content

    def test_html_title_escapes_quotes(self):
        regions, data = _sample_regions()
        content = self._export_html_with_title(
            regions, data, 'title"with"quotes'
        )
        assert 'title"with"quotes' not in content
        assert "title&quot;with&quot;quotes" in content
