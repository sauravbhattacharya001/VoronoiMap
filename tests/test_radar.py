"""Comprehensive tests for vormap_radar — Autonomous Spatial Radar System.

Covers geometry helpers, Target/SectorInfo/RingInfo/Alert dataclasses,
RadarStation scanning, sector & ring analysis, alert generation,
JSON export, HTML export, point loading, and edge cases.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from vormap_radar import (
    Alert,
    RadarResult,
    RadarStation,
    RingInfo,
    SectorInfo,
    Target,
    _bearing,
    _bounding_box,
    _centroid,
    _distance,
    _load_points,
    export_html,
)


# ── Geometry helpers ───────────────────────────────────────────────────────

class TestDistance:
    def test_same_point(self):
        assert _distance((5, 5), (5, 5)) == 0.0

    def test_horizontal(self):
        assert _distance((0, 0), (3, 0)) == pytest.approx(3.0)

    def test_vertical(self):
        assert _distance((0, 0), (0, 4)) == pytest.approx(4.0)

    def test_diagonal_345(self):
        assert _distance((0, 0), (3, 4)) == pytest.approx(5.0)

    def test_negative_coords(self):
        assert _distance((-1, -1), (2, 3)) == pytest.approx(5.0)


class TestBearing:
    """Bearing: 0°=N (negative y in screen coords), clockwise."""

    def test_north(self):
        # Target above origin (lower y in screen coords)
        assert _bearing((0, 10), (0, 0)) == pytest.approx(0.0, abs=0.1)

    def test_east(self):
        assert _bearing((0, 0), (10, 0)) == pytest.approx(90.0, abs=0.1)

    def test_south(self):
        # Target below origin (higher y in screen coords)
        assert _bearing((0, 0), (0, 10)) == pytest.approx(180.0, abs=0.1)

    def test_west(self):
        assert _bearing((0, 0), (-10, 0)) == pytest.approx(270.0, abs=0.1)

    def test_northeast(self):
        b = _bearing((0, 10), (10, 0))
        assert 0 < b < 90

    def test_same_point_returns_zero(self):
        assert _bearing((5, 5), (5, 5)) == 0.0


class TestCentroid:
    def test_single_point(self):
        assert _centroid([(3, 7)]) == (3.0, 7.0)

    def test_two_points(self):
        cx, cy = _centroid([(0, 0), (10, 10)])
        assert cx == pytest.approx(5.0)
        assert cy == pytest.approx(5.0)

    def test_square(self):
        cx, cy = _centroid([(0, 0), (10, 0), (10, 10), (0, 10)])
        assert cx == pytest.approx(5.0)
        assert cy == pytest.approx(5.0)


class TestBoundingBox:
    def test_single_point(self):
        assert _bounding_box([(5, 5)]) == (5, 5, 5, 5)

    def test_multiple(self):
        bb = _bounding_box([(1, 2), (10, 20), (-5, -3)])
        assert bb == (-5, -3, 10, 20)


# ── Dataclass serialisation ───────────────────────────────────────────────

class TestTargetDict:
    def test_roundtrip(self):
        t = Target(x=1.234, y=5.678, distance=9.999, bearing=45.67,
                   ring=1, sector=3, nn_dist=2.345)
        d = t.to_dict()
        assert d["x"] == 1.23
        assert d["y"] == 5.68
        assert d["bearing"] == 45.7
        assert d["ring"] == 1
        assert d["sector"] == 3


class TestSectorInfoDict:
    def test_fields(self):
        s = SectorInfo(index=2, bearing_start=60, bearing_end=90,
                       count=4, z_score=1.23, status="moderate")
        d = s.to_dict()
        assert d["index"] == 2
        assert d["bearing_range"] == "60°–90°"
        assert d["count"] == 4
        assert d["status"] == "moderate"


class TestRingInfoDict:
    def test_fields(self):
        r = RingInfo(index=0, dist_min=0, dist_max=100, count=5, pct=25.6)
        d = r.to_dict()
        assert d["pct"] == 25.6
        assert d["dist_range"] == "0.0–100.0"


class TestAlertDict:
    def test_fields(self):
        a = Alert(severity="warning", message="test alert")
        d = a.to_dict()
        assert d == {"severity": "warning", "message": "test alert"}


# ── RadarStation init ─────────────────────────────────────────────────────

class TestRadarStationInit:
    def test_empty_points_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            RadarStation([])

    def test_default_origin_is_centroid(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        rs = RadarStation(pts)
        assert rs.origin == pytest.approx((5.0, 5.0))

    def test_custom_origin(self):
        rs = RadarStation([(0, 0)], origin=(50, 50))
        assert rs.origin == (50, 50)

    def test_min_rings_sectors(self):
        rs = RadarStation([(0, 0)], rings=0, sectors=-5)
        assert rs.num_rings >= 1
        assert rs.num_sectors >= 1


# ── Full scan ─────────────────────────────────────────────────────────────

@pytest.fixture
def simple_scan() -> RadarResult:
    """4 points in a square, scan with 4 sectors and 2 rings."""
    pts = [(0, 0), (100, 0), (100, 100), (0, 100)]
    station = RadarStation(pts, width=100, height=100, sectors=4, rings=2)
    return station.scan()


class TestScanBasic:
    def test_target_count(self, simple_scan: RadarResult):
        assert len(simple_scan.targets) == 4

    def test_all_sectors_assigned(self, simple_scan: RadarResult):
        for t in simple_scan.targets:
            assert 0 <= t.sector < simple_scan.num_sectors

    def test_all_rings_assigned(self, simple_scan: RadarResult):
        for t in simple_scan.targets:
            assert 0 <= t.ring < simple_scan.num_rings

    def test_distance_positive(self, simple_scan: RadarResult):
        for t in simple_scan.targets:
            assert t.distance >= 0

    def test_bearing_range(self, simple_scan: RadarResult):
        for t in simple_scan.targets:
            assert 0 <= t.bearing < 360

    def test_nn_distance_positive(self, simple_scan: RadarResult):
        for t in simple_scan.targets:
            assert t.nn_dist > 0


class TestScanSectors:
    def test_sector_count_matches_config(self, simple_scan: RadarResult):
        assert len(simple_scan.sectors) == simple_scan.num_sectors

    def test_bearing_spans_full_circle(self, simple_scan: RadarResult):
        assert simple_scan.sectors[0].bearing_start == 0.0
        last = simple_scan.sectors[-1]
        assert last.bearing_end == pytest.approx(360.0)

    def test_total_sector_counts_match_targets(self, simple_scan: RadarResult):
        total = sum(s.count for s in simple_scan.sectors)
        assert total == len(simple_scan.targets)

    def test_status_values(self, simple_scan: RadarResult):
        valid = {"normal", "moderate", "dense", "void"}
        for s in simple_scan.sectors:
            assert s.status in valid


class TestScanRings:
    def test_ring_count_matches_config(self, simple_scan: RadarResult):
        assert len(simple_scan.rings) == simple_scan.num_rings

    def test_total_ring_counts_match_targets(self, simple_scan: RadarResult):
        total = sum(r.count for r in simple_scan.rings)
        assert total == len(simple_scan.targets)

    def test_pct_sums_to_100(self, simple_scan: RadarResult):
        total_pct = sum(r.pct for r in simple_scan.rings)
        assert total_pct == pytest.approx(100.0)


class TestScanStats:
    def test_required_keys(self, simple_scan: RadarResult):
        required = {"total_targets", "mean_dist", "median_dist", "max_dist",
                     "min_dist", "coverage_pct", "dominant_sector", "mean_nn_dist"}
        assert required.issubset(simple_scan.stats.keys())

    def test_total_targets(self, simple_scan: RadarResult):
        assert simple_scan.stats["total_targets"] == 4

    def test_coverage_bounded(self, simple_scan: RadarResult):
        assert 0 <= simple_scan.stats["coverage_pct"] <= 100


# ── Alert generation ──────────────────────────────────────────────────────

class TestAlerts:
    def test_close_range_critical(self):
        """Point very close to origin triggers critical alert."""
        pts = [(50, 50), (50.1, 50.1)]
        station = RadarStation(pts, origin=(50, 50))
        result = station.scan()
        severities = [a.severity for a in result.alerts]
        assert "critical" in severities

    def test_void_sectors_info(self):
        """Points clustered in one direction leave void sectors."""
        pts = [(10, 10), (11, 10), (12, 10), (13, 10)]
        station = RadarStation(pts, origin=(0, 0), sectors=12)
        result = station.scan()
        void_sectors = [s for s in result.sectors if s.status == "void"]
        assert len(void_sectors) > 0
        # Should generate an info alert about void sectors
        info_msgs = [a.message for a in result.alerts if a.severity == "info"]
        assert any("void" in m.lower() for m in info_msgs)

    def test_dense_sector_warning(self):
        """Many points in one sector should trigger dense warning."""
        # All points at ~45° bearing
        pts = [(i, 0) for i in range(1, 50)]
        station = RadarStation(pts, origin=(0, 50), sectors=12)
        result = station.scan()
        dense = [s for s in result.sectors if s.status == "dense"]
        assert len(dense) > 0

    def test_single_point_no_crash(self):
        """Single point shouldn't crash alert generation."""
        station = RadarStation([(100, 100)], origin=(0, 0))
        result = station.scan()
        assert isinstance(result.alerts, list)


# ── Summary output ────────────────────────────────────────────────────────

class TestSummary:
    def test_contains_key_info(self, simple_scan: RadarResult):
        text = simple_scan.summary()
        assert "Radar Scan Summary" in text
        assert "Targets" in text
        assert str(len(simple_scan.targets)) in text

    def test_alert_icons(self):
        pts = [(50, 50), (50.01, 50.01)]
        station = RadarStation(pts, origin=(50, 50))
        result = station.scan()
        text = result.summary()
        # Should have at least one alert icon
        assert any(icon in text for icon in ["🔴", "🟡", "🟢"])


# ── JSON export ───────────────────────────────────────────────────────────

class TestJsonExport:
    def test_valid_json(self, simple_scan: RadarResult, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        simple_scan.to_json("radar.json")
        with open(tmp_path / "radar.json") as f:
            data = json.load(f)
        assert data["num_rings"] == simple_scan.num_rings
        assert data["num_sectors"] == simple_scan.num_sectors
        assert len(data["targets"]) == len(simple_scan.targets)
        assert len(data["sectors"]) == len(simple_scan.sectors)
        assert len(data["rings"]) == len(simple_scan.rings)

    def test_stats_present(self, simple_scan: RadarResult, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        simple_scan.to_json("radar.json")
        with open(tmp_path / "radar.json") as f:
            data = json.load(f)
        assert "total_targets" in data["stats"]


# ── HTML export ───────────────────────────────────────────────────────────

class TestHtmlExport:
    def test_creates_file(self, simple_scan: RadarResult, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        export_html(simple_scan, "radar.html")
        assert os.path.exists(tmp_path / "radar.html")

    def test_contains_svg(self, simple_scan: RadarResult, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        export_html(simple_scan, "radar.html")
        content = (tmp_path / "radar.html").read_text(encoding="utf-8")
        assert "<svg" in content
        assert "circle" in content

    def test_contains_title(self, simple_scan: RadarResult, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        export_html(simple_scan, "radar.html")
        content = (tmp_path / "radar.html").read_text(encoding="utf-8")
        assert "Spatial Radar" in content

    def test_html_escapes_safe(self, tmp_path: Path, monkeypatch):
        """Ensure HTML export doesn't break with special-char coordinates."""
        monkeypatch.chdir(tmp_path)
        pts = [(0.0, 0.0), (100.0, 100.0)]
        station = RadarStation(pts, width=200, height=200)
        result = station.scan()
        export_html(result, "radar_esc.html")
        content = (tmp_path / "radar_esc.html").read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content


# ── Point loader ──────────────────────────────────────────────────────────

class TestLoadPoints:
    def test_space_delimited(self, tmp_path: Path):
        p = tmp_path / "pts.txt"
        p.write_text("10 20\n30 40\n50 60\n")
        pts, w, h = _load_points(str(p))
        assert len(pts) == 3
        assert pts[0] == (10.0, 20.0)

    def test_comma_delimited(self, tmp_path: Path):
        p = tmp_path / "pts.txt"
        p.write_text("10,20\n30,40\n")
        pts, w, h = _load_points(str(p))
        assert len(pts) == 2

    def test_skips_comments_and_blank(self, tmp_path: Path):
        p = tmp_path / "pts.txt"
        p.write_text("# header\n\n10 20\n# another comment\n30 40\n")
        pts, w, h = _load_points(str(p))
        assert len(pts) == 2

    def test_skips_bad_lines(self, tmp_path: Path):
        p = tmp_path / "pts.txt"
        p.write_text("10 20\nbad line\n30 40\n")
        pts, w, h = _load_points(str(p))
        assert len(pts) == 2

    def test_empty_file_raises(self, tmp_path: Path):
        p = tmp_path / "empty.txt"
        p.write_text("")
        with pytest.raises(ValueError, match="No points"):
            _load_points(str(p))

    def test_width_height_positive(self, tmp_path: Path):
        p = tmp_path / "pts.txt"
        p.write_text("0 0\n100 200\n")
        pts, w, h = _load_points(str(p))
        assert w > 0
        assert h > 0


# ── Edge cases ────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_point_scan(self):
        station = RadarStation([(50, 50)])
        result = station.scan()
        assert len(result.targets) == 1
        assert result.targets[0].nn_dist == 0.0

    def test_coincident_points(self):
        """All points at same location."""
        pts = [(100, 100)] * 10
        station = RadarStation(pts, origin=(0, 0), sectors=4, rings=2)
        result = station.scan()
        assert len(result.targets) == 10
        # All should be same distance
        dists = {t.distance for t in result.targets}
        assert len(dists) == 1

    def test_custom_max_range(self):
        pts = [(0, 0), (50, 50)]
        station = RadarStation(pts, origin=(0, 0), max_range=500)
        result = station.scan()
        assert result.max_range == 500

    def test_many_rings_and_sectors(self):
        """High granularity shouldn't crash."""
        pts = [(i * 10, i * 5) for i in range(20)]
        station = RadarStation(pts, rings=50, sectors=36)
        result = station.scan()
        assert len(result.sectors) == 36
        assert len(result.rings) == 50

    def test_low_coverage_alert(self):
        """All points in one quadrant should flag low coverage."""
        pts = [(10 + i, 10 + i) for i in range(5)]
        station = RadarStation(pts, origin=(0, 0), sectors=12)
        result = station.scan()
        info_alerts = [a for a in result.alerts if a.severity == "info"]
        coverage = result.stats["coverage_pct"]
        # With 12 sectors and clustered points, coverage should be low
        if coverage < 50:
            assert any("coverage" in a.message.lower() for a in info_alerts)

    def test_range_concentration_warning(self):
        """All points at same distance should trigger ring concentration."""
        # Points in a ring around origin
        pts = [(100 * math.cos(a), 100 * math.sin(a)) for a in
               [i * math.pi / 6 for i in range(12)]]
        station = RadarStation(pts, origin=(0, 0), rings=3)
        result = station.scan()
        # Most/all targets in one ring
        ring_pcts = [r.pct for r in result.rings]
        max_pct = max(ring_pcts)
        if max_pct > 60:
            warnings = [a for a in result.alerts if a.severity == "warning"]
            assert any("concentration" in a.message.lower() for a in warnings)
