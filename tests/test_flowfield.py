"""Tests for vormap_flowfield module."""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
import vormap_flowfield as ff


def test_poisson_disk_seeds():
    seeds = ff._poisson_disk_seeds(400, 400, 30)
    assert len(seeds) == 30
    for x, y in seeds:
        assert 0 <= x <= 400
        assert 0 <= y <= 400


def test_voronoi_cells():
    seeds = [(100, 100), (300, 300)]
    cells, boundary = ff._voronoi_cells(seeds, 400, 400, step=4)
    assert len(cells) == 2
    assert len(cells[0]) > 0
    assert len(cells[1]) > 0


def test_all_fields_run():
    for name, fn in ff.FIELDS.items():
        vx, vy = fn(100, 100, 400, 400)
        assert isinstance(vx, float)
        assert isinstance(vy, float)


def test_streamline_integration():
    pts = ff._integrate_streamline(ff._field_curl, 200, 200, 400, 400, steps=20)
    assert len(pts) >= 2


def test_svg_output():
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "test.svg")
        ff.main(["--seeds", "10", "--field", "curl", "--step", "8",
                 "--width", "100", "--height", "100", "-o", out, "--seed", "1"])
        assert os.path.exists(out)
        content = open(out).read()
        assert "<svg" in content
        assert "streamline" not in content or "</svg>" in content


def test_json_output():
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "test.json")
        ff.main(["--seeds", "10", "--field", "radial", "--step", "8",
                 "--width", "100", "--height", "100", "--json", out, "--seed", "1"])
        assert os.path.exists(out)
        data = json.load(open(out))
        assert data["width"] == 100
        assert len(data["cells"]) == 10
        assert "magnitude" in data["cells"][0]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  PASS  {name}")
    print("All tests passed.")
