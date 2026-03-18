"""Tests for vormap_ascii — terminal rendering of Voronoi diagrams."""

import io
import math
import sys
import pytest

import vormap_ascii


# ── Helpers ──────────────────────────────────────────────────────────

def _square_regions():
    """Two adjacent square regions sharing an edge at x=0.5."""
    return {
        (0.25, 0.5): [(0, 0), (0.5, 0), (0.5, 1), (0, 1)],
        (0.75, 0.5): [(0.5, 0), (1, 0), (1, 1), (0.5, 1)],
    }


def _single_region():
    """One triangular region."""
    return {
        (0.5, 0.33): [(0, 0), (1, 0), (0.5, 1)],
    }


def _diamond_region():
    """Single diamond-shaped region for concave-testing edge cases."""
    return {
        (0.5, 0.5): [(0.5, 0), (1, 0.5), (0.5, 1), (0, 0.5)],
    }


# ── _point_in_polygon ───────────────────────────────────────────────

class TestPointInPolygon:
    """Unit tests for the ray-casting helper."""

    def test_inside_square(self):
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert vormap_ascii._point_in_polygon(0.5, 0.5, square)

    def test_outside_square(self):
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert not vormap_ascii._point_in_polygon(2.0, 2.0, square)

    def test_inside_triangle(self):
        tri = [(0, 0), (2, 0), (1, 2)]
        assert vormap_ascii._point_in_polygon(1.0, 0.5, tri)

    def test_outside_triangle(self):
        tri = [(0, 0), (2, 0), (1, 2)]
        assert not vormap_ascii._point_in_polygon(0.0, 2.0, tri)

    def test_concave_polygon(self):
        # L-shaped polygon
        poly = [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)]
        assert vormap_ascii._point_in_polygon(0.5, 0.5, poly)
        assert vormap_ascii._point_in_polygon(0.5, 1.5, poly)
        # Inside the "notch" — should be outside
        assert not vormap_ascii._point_in_polygon(1.5, 1.5, poly)

    def test_empty_polygon(self):
        assert not vormap_ascii._point_in_polygon(0, 0, [])

    def test_degenerate_line(self):
        # Two points — degenerate polygon, never inside
        assert not vormap_ascii._point_in_polygon(0.5, 0, [(0, 0), (1, 0)])


# ── render ───────────────────────────────────────────────────────────

class TestRender:
    """Integration tests for the render() function."""

    def test_basic_output(self):
        """render() produces non-empty output to a file stream."""
        buf = io.StringIO()
        regions = _square_regions()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=40, height=10, file=buf)
        output = buf.getvalue()
        assert len(output) > 0
        assert "2 regions" in output

    def test_mono_mode(self):
        """Monochrome mode uses no ANSI escape codes."""
        buf = io.StringIO()
        regions = _square_regions()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=40, height=10, mono=True, file=buf)
        output = buf.getvalue()
        assert '\033[' not in output

    def test_color_mode_has_ansi(self):
        """Color mode includes ANSI escape sequences."""
        buf = io.StringIO()
        regions = _square_regions()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=40, height=10, mono=False, file=buf)
        output = buf.getvalue()
        assert '\033[' in output

    def test_no_seeds(self):
        """show_seeds=False omits seed markers."""
        buf = io.StringIO()
        regions = _square_regions()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=40, height=10,
                            show_seeds=False, mono=True, file=buf)
        output = buf.getvalue()
        assert '*' not in output  # no seed markers in mono

    def test_single_region(self):
        """Works with a single region."""
        buf = io.StringIO()
        regions = _single_region()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=20, height=10, file=buf)
        output = buf.getvalue()
        assert "1 regions" in output

    def test_empty_regions(self):
        """Empty regions dict prints a message instead of crashing."""
        buf = io.StringIO()
        vormap_ascii.render({}, [], width=20, height=10, file=buf)
        assert "No regions" in buf.getvalue()

    def test_custom_dimensions(self):
        """Output respects width/height parameters."""
        buf = io.StringIO()
        regions = _square_regions()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=60, height=20, mono=True, file=buf)
        lines = buf.getvalue().split('\n')
        # Canvas lines (before legend) should be height lines
        canvas_lines = [l for l in lines[:20] if l.strip()]
        assert len(canvas_lines) >= 15  # most rows should have content

    def test_legend_includes_canvas_size(self):
        buf = io.StringIO()
        regions = _square_regions()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=50, height=15, file=buf)
        assert "50x15" in buf.getvalue()

    def test_diamond_region(self):
        """Diamond shape renders without error."""
        buf = io.StringIO()
        regions = _diamond_region()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=30, height=15, mono=True, file=buf)
        output = buf.getvalue()
        assert "1 regions" in output


# ── render_to_string ─────────────────────────────────────────────────

class TestRenderToString:
    """Tests for the string-output convenience wrapper."""

    def test_returns_string(self):
        regions = _square_regions()
        data = list(regions.keys())
        result = vormap_ascii.render_to_string(regions, data, width=30, height=10)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_mono_kwarg_forwarded(self):
        regions = _square_regions()
        data = list(regions.keys())
        result = vormap_ascii.render_to_string(regions, data, width=30, height=10, mono=True)
        assert '\033[' not in result

    def test_matches_render_output(self):
        """render_to_string produces same output as render(file=...)."""
        regions = _square_regions()
        data = list(regions.keys())
        buf = io.StringIO()
        vormap_ascii.render(regions, data, width=30, height=10, mono=True, file=buf)
        direct = buf.getvalue()
        wrapper = vormap_ascii.render_to_string(regions, data, width=30, height=10, mono=True)
        assert direct == wrapper


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    """Boundary and robustness tests."""

    def test_minimal_canvas(self):
        """1x1 canvas doesn't crash."""
        buf = io.StringIO()
        regions = _single_region()
        data = list(regions.keys())
        vormap_ascii.render(regions, data, width=1, height=1, file=buf)
        assert "1 regions" in buf.getvalue()

    def test_large_canvas(self):
        """Large canvas renders without error."""
        regions = _square_regions()
        data = list(regions.keys())
        result = vormap_ascii.render_to_string(regions, data, width=200, height=60, mono=True)
        assert "200x60" in result

    def test_many_regions(self):
        """Handles more regions than the color palette size (16)."""
        import random
        random.seed(42)
        regions = {}
        for i in range(20):
            cx, cy = random.uniform(0, 10), random.uniform(0, 10)
            # Small square region around each center
            d = 0.3
            regions[(cx, cy)] = [
                (cx - d, cy - d), (cx + d, cy - d),
                (cx + d, cy + d), (cx - d, cy + d),
            ]
        data = list(regions.keys())
        result = vormap_ascii.render_to_string(regions, data, width=60, height=30)
        assert "20 regions" in result

    def test_collinear_seeds(self):
        """Seeds on a line still render something."""
        regions = {
            (0.25, 0.5): [(0, 0), (0.5, 0), (0.5, 1), (0, 1)],
            (0.75, 0.5): [(0.5, 0), (1, 0), (1, 1), (0.5, 1)],
        }
        data = [(0.25, 0.5), (0.75, 0.5)]
        result = vormap_ascii.render_to_string(regions, data, width=40, height=10, mono=True)
        assert len(result) > 0
