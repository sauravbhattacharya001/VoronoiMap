"""Tests for Voronoi diagram SVG visualization (vormap_viz).

These tests verify region computation, SVG generation, color schemes,
and the one-call generate_diagram() convenience function.
"""

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
