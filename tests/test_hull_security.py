"""Tests for vormap_hull — path validation security."""

import json
import os
import tempfile

import pytest

from vormap_hull import (
    convex_hull,
    hull_analysis,
    export_svg,
    export_json,
)


POINTS = [(0, 0), (10, 0), (5, 8.66), (3, 4), (7, 2), (1, 6), (9, 3)]


class TestHullPathValidation:
    """Verify that hull export functions validate paths to prevent traversal."""

    def _get_analysis(self):
        return hull_analysis(POINTS)

    def test_svg_rejects_traversal(self):
        analysis = self._get_analysis()
        with pytest.raises(ValueError, match="traversal"):
            export_svg(analysis, POINTS, "../../etc/passwd.svg")

    def test_json_rejects_traversal(self):
        analysis = self._get_analysis()
        with pytest.raises(ValueError, match="traversal"):
            export_json(analysis, "../../../tmp/evil.json")

    def test_svg_accepts_valid_absolute_path(self):
        analysis = self._get_analysis()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_svg(analysis, POINTS, path)
            assert os.path.exists(path)
            with open(path) as f:
                content = f.read()
            assert "<svg" in content
        finally:
            os.unlink(path)

    def test_json_accepts_valid_absolute_path(self):
        analysis = self._get_analysis()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(analysis, path)
            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert "bounding_rectangle" in data
        finally:
            os.unlink(path)

    def test_svg_rejects_empty_path(self):
        analysis = self._get_analysis()
        with pytest.raises(ValueError):
            export_svg(analysis, POINTS, "")

    def test_json_rejects_empty_path(self):
        analysis = self._get_analysis()
        with pytest.raises(ValueError):
            export_json(analysis, "")
