"""Tests for vormap_text — Voronoi Typography."""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_text import (
    render_text_voronoi,
    export_text_svg,
    export_text_html,
    VoronoiCell,
)


# ── render_text_voronoi ─────────────────────────────────────────────

class TestRenderText:
    def test_basic_render(self):
        cells = render_text_voronoi("HI")
        assert len(cells) > 0

    def test_single_letter(self):
        cells = render_text_voronoi("A")
        assert len(cells) > 0

    def test_returns_voronoi_cells(self):
        cells = render_text_voronoi("X")
        assert all(isinstance(c, VoronoiCell) for c in cells)

    def test_density_affects_count(self):
        lo = render_text_voronoi("X", seed_density=5)
        hi = render_text_voronoi("X", seed_density=50)
        assert len(hi) >= len(lo)

    def test_empty_string(self):
        cells = render_text_voronoi("")
        assert len(cells) == 0

    def test_digits(self):
        cells = render_text_voronoi("123")
        assert len(cells) > 0

    def test_space_handling(self):
        # Space should not crash, just add gap
        cells = render_text_voronoi("A B")
        assert len(cells) > 0

    def test_colormap_option(self):
        cells = render_text_voronoi("OK", colormap="plasma")
        assert len(cells) > 0


# ── SVG export ──────────────────────────────────────────────────────

class TestExportSVG:
    def test_creates_file(self, tmp_path):
        cells = render_text_voronoi("OK")
        fpath = str(tmp_path / "test.svg")
        export_text_svg(cells, fpath)
        assert os.path.isfile(fpath)
        content = open(fpath).read()
        assert "<svg" in content

    def test_with_background(self, tmp_path):
        cells = render_text_voronoi("BG")
        fpath = str(tmp_path / "bg.svg")
        export_text_svg(cells, fpath, background="#1a1a2e")
        content = open(fpath).read()
        assert "1a1a2e" in content


# ── HTML export ─────────────────────────────────────────────────────

class TestExportHTML:
    def test_creates_html(self, tmp_path):
        cells = render_text_voronoi("HI")
        fpath = str(tmp_path / "test.html")
        export_text_html(cells, fpath)
        assert os.path.isfile(fpath)
        content = open(fpath).read()
        assert "<html" in content.lower() or "<svg" in content
