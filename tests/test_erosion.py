"""Tests for vormap_erosion module."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_erosion import (
    hydraulic_erosion,
    thermal_erosion,
    export_erosion_json,
    export_erosion_csv,
)

import pytest


def _simple_graph():
    """3-node linear graph with elevations."""
    adj = {"A": ["B"], "B": ["A", "C"], "C": ["B"]}
    elev = {"A": 100.0, "B": 50.0, "C": 10.0}
    return adj, elev


class TestHydraulicErosion:
    def test_basic_run(self):
        adj, elev = _simple_graph()
        frames = hydraulic_erosion(adj, elev, steps=10, rate=0.1)
        assert len(frames) == 11  # initial + 10 steps

    def test_material_flows_downhill(self):
        adj, elev = _simple_graph()
        frames = hydraulic_erosion(adj, elev, steps=20, rate=0.1)
        # A should lose height, C should gain
        assert frames[-1]["A"] < elev["A"]
        assert frames[-1]["C"] > elev["C"]

    def test_zero_steps(self):
        adj, elev = _simple_graph()
        frames = hydraulic_erosion(adj, elev, steps=0)
        assert len(frames) == 1
        assert frames[0] == {s: float(v) for s, v in elev.items()}

    def test_no_negative_elevations(self):
        adj, elev = _simple_graph()
        frames = hydraulic_erosion(adj, elev, steps=200, rate=0.5)
        for frame in frames:
            for v in frame.values():
                assert v >= 0.0

    def test_invalid_rate(self):
        adj, elev = _simple_graph()
        with pytest.raises(ValueError):
            hydraulic_erosion(adj, elev, rate=0)
        with pytest.raises(ValueError):
            hydraulic_erosion(adj, elev, rate=1.5)


class TestThermalErosion:
    def test_basic_run(self):
        adj, elev = _simple_graph()
        frames = thermal_erosion(adj, elev, steps=10, talus=5.0)
        assert len(frames) == 11

    def test_steep_slopes_flatten(self):
        adj = {"A": ["B"], "B": ["A"]}
        elev = {"A": 100.0, "B": 0.0}
        frames = thermal_erosion(adj, elev, steps=50, talus=5.0, fraction=0.5)
        diff = abs(frames[-1]["A"] - frames[-1]["B"])
        assert diff < abs(elev["A"] - elev["B"])

    def test_within_talus_no_change(self):
        adj = {"A": ["B"], "B": ["A"]}
        elev = {"A": 10.0, "B": 8.0}
        frames = thermal_erosion(adj, elev, steps=10, talus=5.0)
        assert frames[-1]["A"] == pytest.approx(10.0)
        assert frames[-1]["B"] == pytest.approx(8.0)

    def test_invalid_talus(self):
        adj, elev = _simple_graph()
        with pytest.raises(ValueError):
            thermal_erosion(adj, elev, talus=0)


class TestExport:
    def test_json_export(self):
        adj, elev = _simple_graph()
        frames = hydraulic_erosion(adj, elev, steps=3)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_erosion_json(frames, path)
            with open(path) as f:
                data = json.load(f)
            assert data["steps"] == 4
            assert len(data["frames"]) == 4
        finally:
            os.unlink(path)

    def test_csv_export(self):
        adj, elev = _simple_graph()
        frames = hydraulic_erosion(adj, elev, steps=2)
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_erosion_csv(frames, path)
            with open(path) as f:
                lines = f.readlines()
            assert lines[0].strip() == "step,seed,elevation"
            # 3 frames × 3 seeds + header = 10
            assert len(lines) == 10
        finally:
            os.unlink(path)
