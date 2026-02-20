"""Tests for Voronoi diagram SVG visualization (vormap_viz).

These tests verify region computation, SVG generation, color schemes,
the one-call generate_diagram() convenience function, and GeoJSON export.
"""

import json
import math
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap
import vormap_viz


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def simple_data_dir(tmp_path):
    """Create a temp data/ dir with a well-separated point dataset."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Asymmetric arrangement to avoid degenerate equidistant configs
    test_file = data_dir / "test_viz.txt"
    test_file.write_text(
        "150.0 200.0\n"
        "850.0 150.0\n"
        "500.0 800.0\n"
        "200.0 600.0\n"
        "750.0 550.0\n"
    )

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    yield tmp_path, "test_viz.txt"
    os.chdir(old_cwd)
    # Clean up caches
    vormap._data_cache.pop("test_viz.txt", None)
    vormap._kdtree_cache.pop("test_viz.txt", None)


@pytest.fixture
def sample_regions():
    """Sample pre-computed regions for SVG export tests."""
    data = [(100.0, 100.0), (300.0, 100.0), (200.0, 300.0)]
    regions = {
        (100.0, 100.0): [(0, 0), (200, 0), (150, 200), (0, 200)],
        (300.0, 100.0): [(200, 0), (400, 0), (400, 200), (150, 200)],
        (200.0, 300.0): [(0, 200), (150, 200), (400, 200), (400, 400), (0, 400)],
    }
    return regions, data


# ── Color scheme tests ───────────────────────────────────────────────

class TestColorSchemes:
    def test_all_schemes_return_valid_hex(self):
        """Every color scheme should return '#rrggbb' format."""
        for name in vormap_viz.list_color_schemes():
            fn = vormap_viz._COLOR_SCHEMES[name]
            for i in range(5):
                color = fn(i, 5)
                assert color.startswith("#"), f"{name} scheme returned {color}"
                assert len(color) == 7, f"{name} scheme returned {color}"

    def test_list_color_schemes(self):
        schemes = vormap_viz.list_color_schemes()
        assert "pastel" in schemes
        assert "rainbow" in schemes
        assert "mono" in schemes
        assert len(schemes) == 6

    def test_invalid_scheme_raises(self, sample_regions):
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="Unknown color scheme"):
                vormap_viz.export_svg(regions, data, path, color_scheme="neon")
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ── SVG export tests ─────────────────────────────────────────────────

class TestExportSvg:
    def test_creates_valid_svg(self, sample_regions):
        """SVG should be valid XML with expected structure."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path)
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0

            tree = ET.parse(path)
            root = tree.getroot()
            ns = "{http://www.w3.org/2000/svg}"
            assert root.tag == ns + "svg" or root.tag == "svg"
        finally:
            os.unlink(path)

    def test_contains_polygons(self, sample_regions):
        """SVG should contain one polygon per region."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path)
            content = open(path).read()
            assert content.count("<polygon") == len(regions)
        finally:
            os.unlink(path)

    def test_contains_seed_circles(self, sample_regions):
        """SVG should contain one circle per data point."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path, show_points=True)
            content = open(path).read()
            assert content.count("<circle") == len(data)
        finally:
            os.unlink(path)

    def test_no_points_when_disabled(self, sample_regions):
        """Seed points should be omitted when show_points=False."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path, show_points=False)
            content = open(path).read()
            assert "<circle" not in content
        finally:
            os.unlink(path)

    def test_labels_when_enabled(self, sample_regions):
        """Labels should appear when show_labels=True."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path, show_labels=True)
            content = open(path).read()
            # Should have label text elements
            assert 'class="label"' in content
        finally:
            os.unlink(path)

    def test_title_in_svg(self, sample_regions):
        """Title text should appear in SVG when provided."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path, title="Test Diagram")
            content = open(path).read()
            assert "Test Diagram" in content
        finally:
            os.unlink(path)

    def test_custom_dimensions(self, sample_regions):
        """SVG should respect custom width and height."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path, width=1200, height=900)
            tree = ET.parse(path)
            root = tree.getroot()
            assert root.get("width") == "1200"
            assert root.get("height") == "900"
        finally:
            os.unlink(path)

    def test_different_color_schemes(self, sample_regions):
        """All color schemes should produce valid SVG."""
        regions, data = sample_regions
        for scheme in vormap_viz.list_color_schemes():
            with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
                path = f.name
            try:
                vormap_viz.export_svg(regions, data, path, color_scheme=scheme)
                assert os.path.exists(path)
                ET.parse(path)  # should be valid XML
            finally:
                os.unlink(path)

    def test_empty_regions_raises(self):
        """Should raise when there's nothing to visualize."""
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No data"):
                vormap_viz.export_svg({}, [], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_background_color(self, sample_regions):
        """Background rect should use the specified color."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_svg(regions, data, path, background="#1a1a2e")
            content = open(path).read()
            assert "#1a1a2e" in content
        finally:
            os.unlink(path)


# ── Region computation tests ────────────────────────────────────────

class TestComputeRegions:
    def test_returns_dict(self, simple_data_dir):
        """compute_regions should return a dict mapping seeds → vertices."""
        _, filename = simple_data_dir
        data = vormap.load_data(filename)
        regions = vormap_viz.compute_regions(data)

        assert isinstance(regions, dict)
        # At least some regions should be traced
        assert len(regions) >= 1

    def test_region_keys_are_data_points(self, simple_data_dir):
        """Region keys should be original data points."""
        _, filename = simple_data_dir
        data = vormap.load_data(filename)
        regions = vormap_viz.compute_regions(data)

        for key in regions:
            assert key in data

    def test_region_vertices_are_tuples(self, simple_data_dir):
        """Each region should have a list of (x, y) tuples."""
        _, filename = simple_data_dir
        data = vormap.load_data(filename)
        regions = vormap_viz.compute_regions(data)

        for seed, verts in regions.items():
            assert len(verts) >= 3, "Region needs at least 3 vertices"
            for v in verts:
                assert len(v) == 2
                assert isinstance(v[0], (int, float))
                assert isinstance(v[1], (int, float))


# ── Generate diagram (one-call) ─────────────────────────────────────

class TestGenerateDiagram:
    def test_one_call_generates_svg(self, simple_data_dir):
        """generate_diagram() should create a valid SVG file."""
        tmp_path, filename = simple_data_dir
        output = str(tmp_path / "output.svg")

        result = vormap_viz.generate_diagram(filename, output)
        assert result == output
        assert os.path.exists(output)
        assert os.path.getsize(output) > 0
        ET.parse(output)  # should be valid XML


# ── Interactive HTML export tests ────────────────────────────────────

class TestExportHtml:
    def test_creates_html_file(self, sample_regions):
        """export_html should create a non-empty HTML file."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            result = vormap_viz.export_html(regions, data, path)
            assert result == path
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_html_contains_doctype(self, sample_regions):
        """Generated HTML should start with DOCTYPE."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert content.strip().startswith("<!DOCTYPE html>")
        finally:
            os.unlink(path)

    def test_html_contains_region_data(self, sample_regions):
        """HTML should embed all region data as JSON."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            # Should contain seed coordinates from our test data
            assert "100.0" in content
            assert "300.0" in content
        finally:
            os.unlink(path)

    def test_html_contains_canvas(self, sample_regions):
        """HTML should include a canvas element."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert "<canvas" in content
        finally:
            os.unlink(path)

    def test_html_contains_color_scheme_selector(self, sample_regions):
        """HTML should include color scheme dropdown."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert "pastel" in content.lower()
            assert "rainbow" in content.lower()
            assert "<select" in content
        finally:
            os.unlink(path)

    def test_html_custom_title(self, sample_regions):
        """Custom title should appear in the HTML."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path, title="My Custom Voronoi")
            content = open(path, encoding="utf-8").read()
            assert "My Custom Voronoi" in content
        finally:
            os.unlink(path)

    def test_html_initial_color_scheme(self, sample_regions):
        """Initial color scheme should be set in the JS."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path, color_scheme="cool")
            content = open(path, encoding="utf-8").read()
            assert '"cool"' in content
        finally:
            os.unlink(path)

    def test_html_has_zoom_controls(self, sample_regions):
        """HTML should contain zoom in/out/reset buttons."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert "zoom-in" in content
            assert "zoom-out" in content
            assert "zoom-reset" in content
        finally:
            os.unlink(path)

    def test_html_has_theme_toggle(self, sample_regions):
        """HTML should include a dark/light theme toggle."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert "theme-btn" in content
        finally:
            os.unlink(path)

    def test_html_has_tooltip(self, sample_regions):
        """HTML should include a tooltip element."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert "tooltip" in content
        finally:
            os.unlink(path)

    def test_html_empty_regions_raises(self):
        """Should raise ValueError when regions are empty."""
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No regions"):
                vormap_viz.export_html({}, [], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_html_region_area_calculation(self, sample_regions):
        """Region area should be computed and embedded in JSON."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            # Our test regions have non-zero area
            assert '"area"' in content
            assert '"vertexCount"' in content
        finally:
            os.unlink(path)

    def test_html_all_color_schemes(self, sample_regions):
        """All 6 color schemes should be accepted as initial."""
        regions, data = sample_regions
        for scheme in vormap_viz.list_color_schemes():
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
                path = f.name
            try:
                vormap_viz.export_html(regions, data, path, color_scheme=scheme)
                assert os.path.exists(path)
                assert os.path.getsize(path) > 1000  # should be a real HTML file
            finally:
                os.unlink(path)

    def test_html_contains_stats(self, sample_regions):
        """HTML should display region/point count stats."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_html(regions, data, path)
            content = open(path, encoding="utf-8").read()
            assert "stats" in content
        finally:
            os.unlink(path)


class TestComputeRegionArea:
    def test_triangle_area(self):
        """Shoelace formula should compute correct area for a triangle."""
        # Triangle with vertices (0,0), (4,0), (0,3) => area = 6.0
        verts = [(0, 0), (4, 0), (0, 3)]
        assert vormap_viz._compute_region_area(verts) == 6.0

    def test_square_area(self):
        """Area of a unit square should be 1.0."""
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert vormap_viz._compute_region_area(verts) == 1.0

    def test_degenerate_area(self):
        """Fewer than 3 vertices should return 0."""
        assert vormap_viz._compute_region_area([(0, 0), (1, 1)]) == 0.0
        assert vormap_viz._compute_region_area([]) == 0.0


# ── GeoJSON export tests ─────────────────────────────────────────────

class TestExportGeoJson:
    def test_creates_valid_geojson(self, sample_regions):
        """GeoJSON should be valid JSON with FeatureCollection type."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path)
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            assert geojson["type"] == "FeatureCollection"
            assert "features" in geojson
        finally:
            os.unlink(path)

    def test_polygon_count(self, sample_regions):
        """Should contain one Polygon feature per region."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path, include_seeds=False)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            polygons = [f for f in geojson["features"]
                        if f["geometry"]["type"] == "Polygon"]
            assert len(polygons) == len(regions)
        finally:
            os.unlink(path)

    def test_seed_points_included_by_default(self, sample_regions):
        """Seed points should be included as Point features by default."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            points = [f for f in geojson["features"]
                      if f["geometry"]["type"] == "Point"]
            assert len(points) == len(data)
        finally:
            os.unlink(path)

    def test_seed_points_excluded(self, sample_regions):
        """Seed points should be omitted when include_seeds=False."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path, include_seeds=False)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            points = [f for f in geojson["features"]
                      if f["geometry"]["type"] == "Point"]
            assert len(points) == 0
        finally:
            os.unlink(path)

    def test_polygon_rings_are_closed(self, sample_regions):
        """GeoJSON polygon rings must be closed (first == last vertex)."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path, include_seeds=False)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            for feature in geojson["features"]:
                ring = feature["geometry"]["coordinates"][0]
                assert ring[0] == ring[-1], "Polygon ring is not closed"
        finally:
            os.unlink(path)

    def test_region_properties(self, sample_regions):
        """Each region feature should have standard properties."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path, include_seeds=False)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            for feature in geojson["features"]:
                props = feature["properties"]
                assert "region_index" in props
                assert "seed_x" in props
                assert "seed_y" in props
                assert "area" in props
                assert "vertex_count" in props
                assert props["area"] > 0
                assert props["vertex_count"] >= 3
        finally:
            os.unlink(path)

    def test_seed_point_properties(self, sample_regions):
        """Each seed point feature should have type='seed'."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            points = [f for f in geojson["features"]
                      if f["geometry"]["type"] == "Point"]
            for pt in points:
                assert pt["properties"]["type"] == "seed"
                assert "point_index" in pt["properties"]
                assert "has_region" in pt["properties"]
        finally:
            os.unlink(path)

    def test_custom_properties_fn(self, sample_regions):
        """User-supplied properties_fn should merge into feature properties."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            def add_label(seed, verts, idx):
                return {"label": "Region %d" % (idx + 1), "custom": True}

            vormap_viz.export_geojson(
                regions, data, path,
                include_seeds=False,
                properties_fn=add_label,
            )
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            for feature in geojson["features"]:
                assert "label" in feature["properties"]
                assert feature["properties"]["custom"] is True
        finally:
            os.unlink(path)

    def test_crs_included(self, sample_regions):
        """CRS object should be present when crs_name is provided."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(
                regions, data, path,
                crs_name="urn:ogc:def:crs:EPSG::4326",
            )
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            assert "crs" in geojson
            assert geojson["crs"]["type"] == "name"
            assert geojson["crs"]["properties"]["name"] == \
                "urn:ogc:def:crs:EPSG::4326"
        finally:
            os.unlink(path)

    def test_crs_omitted_by_default(self, sample_regions):
        """CRS should not be present when crs_name is not provided."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            assert "crs" not in geojson
        finally:
            os.unlink(path)

    def test_empty_regions_raises(self):
        """Should raise ValueError when regions are empty."""
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No regions"):
                vormap_viz.export_geojson({}, [], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_return_value(self, sample_regions):
        """export_geojson should return the output path."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            result = vormap_viz.export_geojson(regions, data, path)
            assert result == path
        finally:
            os.unlink(path)

    def test_coordinates_match_seed(self, sample_regions):
        """Seed coordinates in properties should match the actual data."""
        regions, data = sample_regions
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_geojson(regions, data, path, include_seeds=False)
            with open(path, encoding="utf-8") as fh:
                geojson = json.load(fh)
            for feature in geojson["features"]:
                seed_x = feature["properties"]["seed_x"]
                seed_y = feature["properties"]["seed_y"]
                assert (seed_x, seed_y) in regions
        finally:
            os.unlink(path)


class TestGenerateGeoJson:
    def test_one_call_generates_geojson(self, simple_data_dir):
        """generate_geojson() should create a valid GeoJSON file."""
        tmp_path, filename = simple_data_dir
        output = str(tmp_path / "output.geojson")

        result = vormap_viz.generate_geojson(filename, output)
        assert result == output
        assert os.path.exists(output)
        with open(output, encoding="utf-8") as fh:
            geojson = json.load(fh)
        assert geojson["type"] == "FeatureCollection"
        assert len(geojson["features"]) > 0


# ── Region statistics tests ──────────────────────────────────────────

class TestComputePerimeter:
    def test_triangle_perimeter(self):
        """Perimeter of a right triangle (3-4-5)."""
        verts = [(0, 0), (3, 0), (0, 4)]
        p = vormap_viz._compute_perimeter(verts)
        assert abs(p - 12.0) < 1e-6

    def test_square_perimeter(self):
        """Perimeter of a unit square should be 4.0."""
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        p = vormap_viz._compute_perimeter(verts)
        assert abs(p - 4.0) < 1e-6

    def test_degenerate_perimeter(self):
        """Fewer than 2 vertices → perimeter 0."""
        assert vormap_viz._compute_perimeter([(0, 0)]) == 0.0
        assert vormap_viz._compute_perimeter([]) == 0.0


class TestComputeCentroid:
    def test_square_centroid(self):
        """Centroid of a unit square at origin should be (0.5, 0.5)."""
        verts = [(0, 0), (1, 0), (1, 1), (0, 1)]
        cx, cy = vormap_viz._compute_centroid(verts)
        assert abs(cx - 0.5) < 1e-4
        assert abs(cy - 0.5) < 1e-4

    def test_triangle_centroid(self):
        """Centroid of triangle (0,0), (6,0), (0,6) → (2, 2)."""
        verts = [(0, 0), (6, 0), (0, 6)]
        cx, cy = vormap_viz._compute_centroid(verts)
        assert abs(cx - 2.0) < 1e-3
        assert abs(cy - 2.0) < 1e-3

    def test_empty(self):
        """Empty list → (0, 0)."""
        assert vormap_viz._compute_centroid([]) == (0.0, 0.0)

    def test_single_point(self):
        """Single point returns itself."""
        cx, cy = vormap_viz._compute_centroid([(5.0, 3.0)])
        assert abs(cx - 5.0) < 1e-4
        assert abs(cy - 3.0) < 1e-4


class TestIsoperimetricQuotient:
    def test_circle_approximation(self):
        """A regular polygon with many sides should approach 1.0."""
        # 100-sided regular polygon with radius 1
        n = 100
        verts = [(math.cos(2 * math.pi * i / n),
                  math.sin(2 * math.pi * i / n)) for i in range(n)]
        area = vormap_viz._compute_region_area(verts)
        perimeter = vormap_viz._compute_perimeter(verts)
        iq = vormap_viz._isoperimetric_quotient(area, perimeter)
        assert 0.99 < iq <= 1.0

    def test_square_compactness(self):
        """A square has IQ = π/4 ≈ 0.7854."""
        area = 1.0
        perimeter = 4.0
        iq = vormap_viz._isoperimetric_quotient(area, perimeter)
        assert abs(iq - math.pi / 4) < 1e-6

    def test_zero_perimeter(self):
        """Zero perimeter → 0.0."""
        assert vormap_viz._isoperimetric_quotient(1.0, 0.0) == 0.0


class TestComputeRegionStats:
    def test_returns_list_of_dicts(self, sample_regions):
        """compute_region_stats should return a list of dicts."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        assert isinstance(stats, list)
        assert len(stats) == len(regions)
        for s in stats:
            assert isinstance(s, dict)

    def test_required_keys(self, sample_regions):
        """Each stat dict should have all required keys."""
        regions, data = sample_regions
        expected_keys = {
            "region_index", "seed_x", "seed_y", "area", "perimeter",
            "centroid_x", "centroid_y", "vertex_count", "compactness",
            "avg_edge_length",
        }
        stats = vormap_viz.compute_region_stats(regions, data)
        for s in stats:
            assert set(s.keys()) == expected_keys

    def test_areas_positive(self, sample_regions):
        """All region areas should be positive."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        for s in stats:
            assert s["area"] > 0

    def test_perimeters_positive(self, sample_regions):
        """All perimeters should be positive."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        for s in stats:
            assert s["perimeter"] > 0

    def test_compactness_in_range(self, sample_regions):
        """Compactness should be between 0 and 1."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        for s in stats:
            assert 0 < s["compactness"] <= 1.0

    def test_sorted_by_index(self, sample_regions):
        """Stats should be sorted by region_index."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        indices = [s["region_index"] for s in stats]
        assert indices == sorted(indices)

    def test_vertex_count_matches(self, sample_regions):
        """Vertex count should match actual region vertices."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        for s in stats:
            seed = (s["seed_x"], s["seed_y"])
            assert s["vertex_count"] == len(regions[seed])


class TestComputeSummaryStats:
    def test_returns_dict(self, sample_regions):
        """compute_summary_stats should return a dict."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        summary = vormap_viz.compute_summary_stats(stats)
        assert isinstance(summary, dict)

    def test_required_keys(self, sample_regions):
        """Summary should have all required keys."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        summary = vormap_viz.compute_summary_stats(stats)
        expected = {
            "region_count", "total_area", "mean_area", "median_area",
            "min_area", "max_area", "std_area", "mean_perimeter",
            "min_perimeter", "max_perimeter", "mean_vertices",
            "mean_compactness", "area_range", "coefficient_of_variation",
        }
        assert set(summary.keys()) == expected

    def test_region_count(self, sample_regions):
        """Region count should match input length."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        summary = vormap_viz.compute_summary_stats(stats)
        assert summary["region_count"] == len(regions)

    def test_total_area_is_sum(self, sample_regions):
        """Total area should be sum of individual areas."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        summary = vormap_viz.compute_summary_stats(stats)
        expected = sum(s["area"] for s in stats)
        assert abs(summary["total_area"] - expected) < 0.01

    def test_empty_input(self):
        """Empty input should return zeros."""
        summary = vormap_viz.compute_summary_stats([])
        assert summary["region_count"] == 0
        assert summary["total_area"] == 0.0

    def test_min_max_area(self, sample_regions):
        """Min/max area should match extremes of individual areas."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        summary = vormap_viz.compute_summary_stats(stats)
        areas = [s["area"] for s in stats]
        assert summary["min_area"] == round(min(areas), 4)
        assert summary["max_area"] == round(max(areas), 4)

    def test_area_range(self, sample_regions):
        """area_range should be max − min."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        summary = vormap_viz.compute_summary_stats(stats)
        expected = summary["max_area"] - summary["min_area"]
        assert abs(summary["area_range"] - expected) < 0.01


class TestExportStatsCsv:
    def test_creates_csv_file(self, sample_regions):
        """export_stats_csv should create a non-empty CSV file."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            result = vormap_viz.export_stats_csv(stats, path)
            assert result == path
            assert os.path.exists(path)
            assert os.path.getsize(path) > 0
        finally:
            os.unlink(path)

    def test_csv_has_header(self, sample_regions):
        """CSV should start with a header row."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_stats_csv(stats, path)
            with open(path, encoding="utf-8") as fh:
                header = fh.readline().strip()
            assert "region_index" in header
            assert "area" in header
            assert "perimeter" in header
            assert "compactness" in header
        finally:
            os.unlink(path)

    def test_csv_row_count(self, sample_regions):
        """CSV should have one row per region (plus header)."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_stats_csv(stats, path, include_summary=False)
            with open(path, encoding="utf-8") as fh:
                lines = [l for l in fh.readlines() if l.strip()]
            # header + data rows
            assert len(lines) == 1 + len(stats)
        finally:
            os.unlink(path)

    def test_csv_includes_summary(self, sample_regions):
        """CSV should include commented summary when enabled."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_stats_csv(stats, path, include_summary=True)
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            assert "# Summary Statistics" in content
            assert "# mean_area:" in content
        finally:
            os.unlink(path)

    def test_csv_parseable(self, sample_regions):
        """CSV should be parseable with csv module."""
        import csv
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_stats_csv(stats, path, include_summary=False)
            with open(path, encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            assert len(rows) == len(stats)
            for row in rows:
                assert float(row["area"]) > 0
        finally:
            os.unlink(path)

    def test_empty_raises(self):
        """Empty stats should raise ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No region"):
                vormap_viz.export_stats_csv([], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportStatsJson:
    def test_creates_json_file(self, sample_regions):
        """export_stats_json should create a valid JSON file."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            result = vormap_viz.export_stats_json(stats, path)
            assert result == path
            with open(path, encoding="utf-8") as fh:
                output = json.load(fh)
            assert "regions" in output
            assert len(output["regions"]) == len(stats)
        finally:
            os.unlink(path)

    def test_json_includes_summary(self, sample_regions):
        """JSON should include summary by default."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_stats_json(stats, path)
            with open(path, encoding="utf-8") as fh:
                output = json.load(fh)
            assert "summary" in output
            assert output["summary"]["region_count"] == len(stats)
        finally:
            os.unlink(path)

    def test_json_without_summary(self, sample_regions):
        """JSON should omit summary when disabled."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_viz.export_stats_json(stats, path, include_summary=False)
            with open(path, encoding="utf-8") as fh:
                output = json.load(fh)
            assert "summary" not in output
        finally:
            os.unlink(path)

    def test_empty_raises(self):
        """Empty stats should raise ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No region"):
                vormap_viz.export_stats_json([], path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestFormatStatsTable:
    def test_returns_string(self, sample_regions):
        """format_stats_table should return a string."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        result = vormap_viz.format_stats_table(stats)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_table_has_header(self, sample_regions):
        """Table should include column headers."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        result = vormap_viz.format_stats_table(stats)
        assert "Area" in result
        assert "Perimeter" in result
        assert "Compact" in result

    def test_table_has_data_rows(self, sample_regions):
        """Table should include one data row per region."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        result = vormap_viz.format_stats_table(stats, summary=False)
        lines = [l for l in result.split("\n") if l.strip() and not l.startswith("-")]
        # header + data rows
        assert len(lines) == 1 + len(stats)

    def test_table_has_summary(self, sample_regions):
        """Table should include summary when enabled."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        result = vormap_viz.format_stats_table(stats, summary=True)
        assert "Summary:" in result
        assert "Total area:" in result
        assert "Mean compactness:" in result

    def test_table_without_summary(self, sample_regions):
        """Table should not include summary when disabled."""
        regions, data = sample_regions
        stats = vormap_viz.compute_region_stats(regions, data)
        result = vormap_viz.format_stats_table(stats, summary=False)
        assert "Summary:" not in result

    def test_empty_message(self):
        """Empty stats should return a message."""
        result = vormap_viz.format_stats_table([])
        assert "No regions" in result


class TestGenerateStats:
    def test_table_format(self, simple_data_dir):
        """generate_stats with table format returns a string."""
        _, filename = simple_data_dir
        result = vormap_viz.generate_stats(filename, fmt="table")
        assert isinstance(result, str)
        assert "Area" in result

    def test_json_format_no_path(self, simple_data_dir):
        """generate_stats with json format and no path returns a dict."""
        _, filename = simple_data_dir
        result = vormap_viz.generate_stats(filename, fmt="json")
        assert isinstance(result, dict)
        assert "regions" in result
        assert "summary" in result

    def test_csv_format_with_path(self, simple_data_dir):
        """generate_stats with csv format exports to file."""
        tmp_path, filename = simple_data_dir
        output = str(tmp_path / "stats.csv")
        result = vormap_viz.generate_stats(filename, output, fmt="csv")
        assert result == output
        assert os.path.exists(output)

    def test_json_format_with_path(self, simple_data_dir):
        """generate_stats with json format exports to file."""
        tmp_path, filename = simple_data_dir
        output = str(tmp_path / "stats.json")
        result = vormap_viz.generate_stats(filename, output, fmt="json")
        assert result == output
        assert os.path.exists(output)

    def test_csv_requires_path(self, simple_data_dir):
        """CSV format without output_path should raise ValueError."""
        _, filename = simple_data_dir
        with pytest.raises(ValueError, match="requires"):
            vormap_viz.generate_stats(filename, fmt="csv")
