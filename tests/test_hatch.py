"""Tests for vormap_hatch — Voronoi Hatch Pattern Generator."""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vormap_hatch as vh


def test_polygon_helpers():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    cx, cy = vh._polygon_centroid(square)
    assert abs(cx - 5) < 0.1 and abs(cy - 5) < 0.1
    bx1, by1, bx2, by2 = vh._polygon_bbox(square)
    assert bx1 == 0 and by1 == 0 and bx2 == 10 and by2 == 10
    assert vh._point_in_polygon(5, 5, square)
    assert not vh._point_in_polygon(15, 15, square)


def test_line_clip():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    square = vh._ensure_ccw(square)
    seg = vh._line_segment_clip(-5, 5, 15, 5, square)
    assert seg is not None
    x1, y1, x2, y2 = seg
    assert abs(x1 - 0) < 0.1 and abs(x2 - 10) < 0.1


def test_hatch_lines():
    poly = [(0, 0), (100, 0), (100, 100), (0, 100)]
    poly = vh._ensure_ccw(poly)
    segs = vh._hatch_lines(poly, 10, 0)
    assert len(segs) > 0
    for s in segs:
        assert len(s) == 4


def test_hatch_cross():
    poly = [(0, 0), (50, 0), (50, 50), (0, 50)]
    poly = vh._ensure_ccw(poly)
    segs = vh._hatch_cross(poly, 10, 45)
    assert len(segs) > 0


def test_hatch_dots():
    poly = [(0, 0), (50, 0), (50, 50), (0, 50)]
    poly = vh._ensure_ccw(poly)
    dots = vh._hatch_dots(poly, 10)
    assert len(dots) > 0
    for d in dots:
        assert len(d) == 2


def test_hatch_contour():
    poly = [(0, 0), (100, 0), (100, 100), (0, 100)]
    poly = vh._ensure_ccw(poly)
    contours = vh._hatch_contour(poly, 15)
    assert len(contours) >= 2


def test_hatch_random():
    poly = [(0, 0), (100, 0), (100, 100), (0, 100)]
    poly = vh._ensure_ccw(poly)
    segs = vh._hatch_random(poly, 10)
    assert len(segs) > 0


def test_hatch_zigzag():
    poly = [(0, 0), (100, 0), (100, 100), (0, 100)]
    poly = vh._ensure_ccw(poly)
    segs = vh._hatch_zigzag(poly, 10, 0)
    assert len(segs) > 0


def test_generate_hatch_all_styles():
    pts = [(50, 50), (150, 50), (100, 150), (50, 150), (150, 150)]
    for style in vh.HATCH_STYLES:
        result = vh.generate_hatch(pts, style=style, spacing=15)
        assert "cells" in result
        assert len(result["cells"]) > 0


def test_generate_with_values():
    pts = [(50, 50), (150, 50), (100, 150)]
    vals = [0.2, 0.5, 1.0]
    result = vh.generate_hatch(pts, style="lines", values=vals)
    assert len(result["cells"]) > 0


def test_svg_output():
    pts = [(50, 50), (150, 50), (100, 150)]
    result = vh.generate_hatch(pts, style="cross", spacing=10)
    svg = vh.to_svg(result)
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_json_output():
    pts = [(50, 50), (150, 50), (100, 150)]
    result = vh.generate_hatch(pts, style="dots", spacing=10)
    j = vh.to_json(result)
    data = json.loads(j)
    assert "cells" in data
    assert data["style"] == "dots"


def test_cli_demo(tmp_path):
    out = str(tmp_path / "demo.svg")
    sys.argv = ["vormap_hatch.py", "dummy", "--demo", "-o", out]
    vh.main()
    assert os.path.exists(out)
    content = open(out).read()
    assert "<svg" in content


def test_invalid_style():
    try:
        vh.generate_hatch([(0, 0)], style="nope")
        assert False, "Should raise"
    except ValueError:
        pass


if __name__ == "__main__":
    import tempfile, pathlib
    tmp = pathlib.Path(tempfile.mkdtemp())
    funcs = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in funcs:
        try:
            import inspect
            if "tmp_path" in inspect.signature(fn).parameters:
                fn(tmp)
            else:
                fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{passed}/{len(funcs)} passed")
