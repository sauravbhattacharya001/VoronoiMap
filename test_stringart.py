"""Tests for vormap_stringart — Voronoi String Art Generator."""

import json
import math
import os
import tempfile

import vormap_stringart


class TestGenerate:
    def test_default_generation(self):
        result = vormap_stringart.generate(800, 800, seed=42)
        assert result.width == 800
        assert result.height == 800
        assert len(result.pins) > 0
        assert len(result.threads) > 0
        assert result.frame_shape == "circle"

    def test_seed_reproducibility(self):
        r1 = vormap_stringart.generate(400, 400, seed=123)
        r2 = vormap_stringart.generate(400, 400, seed=123)
        assert len(r1.pins) == len(r2.pins)
        assert len(r1.threads) == len(r2.threads)
        for a, b in zip(r1.pins, r2.pins):
            assert abs(a.x - b.x) < 1e-9
            assert abs(a.y - b.y) < 1e-9

    def test_circle_frame(self):
        result = vormap_stringart.generate(600, 600, frame="circle", seed=1)
        assert result.frame_shape == "circle"
        assert any(p.is_boundary for p in result.pins)

    def test_square_frame(self):
        result = vormap_stringart.generate(600, 600, frame="square", seed=1)
        assert result.frame_shape == "square"

    def test_hexagon_frame(self):
        result = vormap_stringart.generate(600, 600, frame="hexagon", seed=1)
        assert result.frame_shape == "hexagon"

    def test_all_colormaps(self):
        for cmap in vormap_stringart.COLORMAPS:
            result = vormap_stringart.generate(400, 400, colormap=cmap, num_seeds=10, seed=1)
            assert len(result.threads) > 0

    def test_board_colors(self):
        for board in vormap_stringart.BOARD_COLORS:
            result = vormap_stringart.generate(400, 400, board=board, num_seeds=10, seed=1)
            assert result.board_color == vormap_stringart.BOARD_COLORS[board]

    def test_custom_board_hex(self):
        result = vormap_stringart.generate(400, 400, board="#FF0000", seed=1)
        assert result.board_color == "#FF0000"

    def test_thread_properties(self):
        result = vormap_stringart.generate(400, 400, thread_opacity=0.5, thread_thickness=1.5, seed=1)
        for t in result.threads:
            assert t.opacity == 0.5
            assert t.thickness == 1.5

    def test_boundary_pin_count(self):
        result = vormap_stringart.generate(400, 400, boundary_pins=30, num_seeds=10, seed=1)
        boundary_count = sum(1 for p in result.pins if p.is_boundary)
        assert boundary_count > 0

    def test_pins_have_indices(self):
        result = vormap_stringart.generate(400, 400, seed=1)
        indices = [p.index for p in result.pins]
        assert indices == list(range(len(result.pins)))

    def test_thread_refs_valid(self):
        result = vormap_stringart.generate(400, 400, seed=1)
        n = len(result.pins)
        for t in result.threads:
            assert 0 <= t.pin_a < n
            assert 0 <= t.pin_b < n


class TestSVG:
    def test_svg_render(self):
        result = vormap_stringart.generate(400, 400, num_seeds=10, seed=1)
        svg = vormap_stringart.render_svg(result)
        assert svg.startswith("<svg")
        assert "</svg>" in svg
        assert "<line" in svg
        assert "<circle" in svg

    def test_save_svg(self):
        result = vormap_stringart.generate(400, 400, num_seeds=10, seed=1)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            vormap_stringart.save_svg(result, path)
            assert os.path.getsize(path) > 100
            with open(path) as fh:
                content = fh.read()
            assert "<svg" in content
        finally:
            os.unlink(path)


class TestJSON:
    def test_save_json(self):
        result = vormap_stringart.generate(400, 400, num_seeds=10, seed=1)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            vormap_stringart.save_json(result, path)
            with open(path) as fh:
                data = json.load(fh)
            assert "pins" in data
            assert "threads" in data
            assert data["width"] == 400
            assert len(data["pins"]) == len(result.pins)
            assert len(data["threads"]) == len(result.threads)
        finally:
            os.unlink(path)


class TestDelaunay:
    def test_edges_unique(self):
        result = vormap_stringart.generate(400, 400, num_seeds=20, seed=1)
        edge_set = set()
        for t in result.threads:
            e = (min(t.pin_a, t.pin_b), max(t.pin_a, t.pin_b))
            assert e not in edge_set, f"Duplicate edge {e}"
            edge_set.add(e)

    def test_no_self_loops(self):
        result = vormap_stringart.generate(400, 400, num_seeds=20, seed=1)
        for t in result.threads:
            assert t.pin_a != t.pin_b


if __name__ == "__main__":
    # Simple runner
    import sys
    failures = 0
    for cls_name, cls in [("TestGenerate", TestGenerate), ("TestSVG", TestSVG),
                          ("TestJSON", TestJSON), ("TestDelaunay", TestDelaunay)]:
        obj = cls()
        for name in dir(obj):
            if name.startswith("test_"):
                try:
                    getattr(obj, name)()
                    print(f"  PASS {cls_name}.{name}")
                except Exception as e:
                    print(f"  FAIL {cls_name}.{name}: {e}")
                    failures += 1
    sys.exit(1 if failures else 0)
