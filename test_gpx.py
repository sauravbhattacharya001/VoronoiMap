"""Tests for vormap_gpx — GPX import/export module."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from vormap_gpx import load_gpx, export_gpx, gpx_info


# ── Sample GPX content ─────────────────────────────────────────────

SAMPLE_GPX = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test"
     xmlns="http://www.topografix.com/GPX/1/1">
  <wpt lat="47.6062" lon="-122.3321">
    <name>Seattle</name>
    <desc>The Emerald City</desc>
    <ele>56.0</ele>
  </wpt>
  <wpt lat="45.5152" lon="-122.6784">
    <name>Portland</name>
    <ele>15.2</ele>
  </wpt>
  <trk>
    <name>Trail</name>
    <trkseg>
      <trkpt lat="47.6" lon="-122.3">
        <ele>100.0</ele>
      </trkpt>
      <trkpt lat="47.5" lon="-122.2">
        <ele>200.0</ele>
      </trkpt>
    </trkseg>
  </trk>
  <rte>
    <name>Route 1</name>
    <rtept lat="46.0" lon="-121.0">
      <name>Start</name>
    </rtept>
    <rtept lat="46.5" lon="-121.5">
      <name>End</name>
    </rtept>
  </rte>
</gpx>
"""

SAMPLE_GPX_NO_NS = """\
<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test">
  <wpt lat="40.7128" lon="-74.0060">
    <name>NYC</name>
  </wpt>
</gpx>
"""


def _write_temp(content, suffix=".gpx"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


def test_load_all():
    path = _write_temp(SAMPLE_GPX)
    try:
        points, meta = load_gpx(path, source="all")
        assert len(points) == 6  # 2 wpt + 2 trkpt + 2 rtept
        # Check first waypoint
        assert abs(points[0][0] - (-122.3321)) < 1e-4
        assert abs(points[0][1] - 47.6062) < 1e-4
        assert meta[0]["name"] == "Seattle"
        assert meta[0]["desc"] == "The Emerald City"
        assert abs(meta[0]["ele"] - 56.0) < 0.01
    finally:
        os.unlink(path)


def test_load_waypoints_only():
    path = _write_temp(SAMPLE_GPX)
    try:
        points, meta = load_gpx(path, source="waypoints")
        assert len(points) == 2
        assert meta[0]["name"] == "Seattle"
        assert meta[1]["name"] == "Portland"
    finally:
        os.unlink(path)


def test_load_tracks_only():
    path = _write_temp(SAMPLE_GPX)
    try:
        points, meta = load_gpx(path, source="tracks")
        assert len(points) == 2
        assert abs(points[0][1] - 47.6) < 1e-4
    finally:
        os.unlink(path)


def test_load_routes_only():
    path = _write_temp(SAMPLE_GPX)
    try:
        points, meta = load_gpx(path, source="routes")
        assert len(points) == 2
        assert meta[0]["name"] == "Start"
    finally:
        os.unlink(path)


def test_load_no_namespace():
    path = _write_temp(SAMPLE_GPX_NO_NS)
    try:
        points, meta = load_gpx(path, source="all")
        assert len(points) == 1
        assert abs(points[0][0] - (-74.0060)) < 1e-4
    finally:
        os.unlink(path)


def test_export_and_reimport():
    original = [(-122.33, 47.61), (-122.68, 45.52)]
    names = ["Seattle", "Portland"]

    fd, path = tempfile.mkstemp(suffix=".gpx")
    os.close(fd)
    try:
        export_gpx(original, path, names=names)
        points, meta = load_gpx(path)
        assert len(points) == 2
        assert abs(points[0][0] - (-122.33)) < 1e-6
        assert abs(points[0][1] - 47.61) < 1e-6
        assert meta[0]["name"] == "Seattle"
    finally:
        os.unlink(path)


def test_export_with_elevations():
    points = [(0.0, 0.0), (1.0, 1.0)]
    elevations = [100.5, 200.0]

    fd, path = tempfile.mkstemp(suffix=".gpx")
    os.close(fd)
    try:
        export_gpx(points, path, elevations=elevations)
        reimported, meta = load_gpx(path)
        assert len(reimported) == 2
        assert abs(meta[0]["ele"] - 100.5) < 0.01
    finally:
        os.unlink(path)


def test_gpx_info():
    path = _write_temp(SAMPLE_GPX)
    try:
        info = gpx_info(path)
        assert info["waypoints"] == 2
        assert info["tracks"] == 1
        assert info["track_points"] == 2
        assert info["routes"] == 1
        assert info["route_points"] == 2
        assert info["total_points"] == 6
        assert info["bbox"] is not None
        assert info["bbox"]["min_lat"] < info["bbox"]["max_lat"]
    finally:
        os.unlink(path)


def test_invalid_gpx():
    path = _write_temp("<not>valid</gpx>", suffix=".gpx")
    try:
        try:
            load_gpx(path)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass
    finally:
        os.unlink(path)


def test_file_not_found():
    try:
        load_gpx("/nonexistent/file.gpx")
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print("  %s ..." % t.__name__, end=" ")
        t()
        print("OK")
    print("\nAll %d tests passed!" % len(tests))
