"""Tests for multi-format data input (CSV, JSON, GeoJSON).

Covers: _detect_format, _parse_points_csv, _parse_points_json,
        _parse_points_geojson, load_data with various formats.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap


@pytest.fixture(autouse=True)
def _clean_cache():
    """Clear file cache before each test."""
    vormap._file_cache.clear()
    old = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
    yield
    vormap._file_cache.clear()
    vormap.set_bounds(*old)


# ── Format detection ─────────────────────────────────────────────────

def test_detect_format_txt(tmp_path):
    f = tmp_path / "points.txt"
    f.write_text("100 200\n300 400\n")
    assert vormap._detect_format(str(f)) == "txt"


def test_detect_format_csv(tmp_path):
    f = tmp_path / "points.csv"
    f.write_text("x,y\n100,200\n300,400\n")
    assert vormap._detect_format(str(f)) == "csv"


def test_detect_format_json(tmp_path):
    f = tmp_path / "points.json"
    f.write_text(json.dumps([[100, 200], [300, 400]]))
    assert vormap._detect_format(str(f)) == "json"


def test_detect_format_geojson_ext(tmp_path):
    f = tmp_path / "points.geojson"
    f.write_text("{}")
    assert vormap._detect_format(str(f)) == "geojson"


def test_detect_format_json_with_geojson_content(tmp_path):
    f = tmp_path / "points.json"
    fc = {"type": "FeatureCollection", "features": []}
    f.write_text(json.dumps(fc))
    assert vormap._detect_format(str(f)) == "geojson"


# ── CSV parsing ──────────────────────────────────────────────────────

def test_parse_csv_xy_headers(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("x,y\n100.5,200.3\n450.2,750.1\n")
    pts = vormap._parse_points_csv(str(f))
    assert len(pts) == 2
    assert pts[0] == (100.5, 200.3)
    assert pts[1] == (450.2, 750.1)


def test_parse_csv_lnglat_headers(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("lng,lat\n-122.3,47.6\n-73.9,40.7\n")
    pts = vormap._parse_points_csv(str(f))
    assert len(pts) == 2
    assert pts[0] == (-122.3, 47.6)


def test_parse_csv_longitude_latitude(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("id,longitude,latitude,name\n1,-122.3,47.6,Seattle\n2,-73.9,40.7,NYC\n")
    pts = vormap._parse_points_csv(str(f))
    assert len(pts) == 2


def test_parse_csv_no_header(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("100,200\n300,400\n")
    pts = vormap._parse_points_csv(str(f))
    assert len(pts) == 2


def test_parse_csv_skips_nan(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("x,y\n100,200\nnan,300\n400,500\n")
    pts = vormap._parse_points_csv(str(f))
    assert len(pts) == 2


# ── JSON parsing ─────────────────────────────────────────────────────

def test_parse_json_array_of_arrays(tmp_path):
    f = tmp_path / "data.json"
    f.write_text(json.dumps([[100, 200], [300, 400], [500, 600]]))
    pts = vormap._parse_points_json(str(f))
    assert len(pts) == 3
    assert pts[0] == (100.0, 200.0)


def test_parse_json_array_of_objects(tmp_path):
    f = tmp_path / "data.json"
    data = [{"x": 100, "y": 200}, {"lng": 300, "lat": 400}]
    f.write_text(json.dumps(data))
    pts = vormap._parse_points_json(str(f))
    assert len(pts) == 2


def test_parse_json_skips_invalid(tmp_path):
    f = tmp_path / "data.json"
    data = [[100, 200], "bad", [300, 400], {"z": 5}]
    f.write_text(json.dumps(data))
    pts = vormap._parse_points_json(str(f))
    assert len(pts) == 2


# ── GeoJSON parsing ──────────────────────────────────────────────────

def test_parse_geojson_feature_collection(tmp_path):
    f = tmp_path / "data.geojson"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100.5, 200.3]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [450.2, 750.1]}, "properties": {}},
        ]
    }
    f.write_text(json.dumps(fc))
    pts = vormap._parse_points_geojson(str(f))
    assert len(pts) == 2
    assert pts[0] == (100.5, 200.3)


def test_parse_geojson_single_feature(tmp_path):
    f = tmp_path / "data.geojson"
    feat = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [10, 20]}, "properties": {}}
    f.write_text(json.dumps(feat))
    pts = vormap._parse_points_geojson(str(f))
    assert len(pts) == 1


def test_parse_geojson_skips_non_point(tmp_path):
    f = tmp_path / "data.geojson"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100, 200]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}, "properties": {}},
        ]
    }
    f.write_text(json.dumps(fc))
    pts = vormap._parse_points_geojson(str(f))
    assert len(pts) == 1


# ── Integration: load_data with formats ──────────────────────────────

def test_load_data_csv(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    f = data_dir / "test.csv"
    f.write_text("x,y\n100,200\n300,400\n500,600\n")

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        pts = vormap.load_data("test.csv")
        assert len(pts) == 3
        assert pts[0] == (100.0, 200.0)
    finally:
        os.chdir(old_cwd)


def test_load_data_json(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    f = data_dir / "test.json"
    f.write_text(json.dumps([[100, 200], [300, 400]]))

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        pts = vormap.load_data("test.json")
        assert len(pts) == 2
    finally:
        os.chdir(old_cwd)


def test_load_data_geojson(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    f = data_dir / "test.geojson"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [100, 200]}, "properties": {}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [300, 400]}, "properties": {}},
        ]
    }
    f.write_text(json.dumps(fc))

    old_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        pts = vormap.load_data("test.geojson")
        assert len(pts) == 2
    finally:
        os.chdir(old_cwd)
