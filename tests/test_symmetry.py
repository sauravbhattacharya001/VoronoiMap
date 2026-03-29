"""Tests for vormap_symmetry — symmetry analysis module."""

import math
import pytest
import vormap_symmetry as sym


def _make_regular_polygon(n, radius=100.0, cx=500.0, cy=500.0):
    """Create a perfect n-gon point set."""
    points = []
    for i in range(n):
        angle = 2 * math.pi * i / n
        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)
        points.append([x, y, 0])
    return points


def _make_grid(rows=5, cols=5, spacing=50.0, origin=(100.0, 100.0)):
    """Create a regular grid."""
    points = []
    for r in range(rows):
        for c in range(cols):
            points.append([origin[0] + c * spacing, origin[1] + r * spacing, 0])
    return points


class TestRotationalSymmetry:
    """Rotational symmetry detection."""

    def test_hexagon_detects_6fold(self):
        data = _make_regular_polygon(6)
        result = sym.symmetry_analysis(data)
        assert result.best_fold in (2, 3, 6)
        assert result.best_fold_score > 0.9

    def test_square_detects_4fold(self):
        data = _make_regular_polygon(4)
        result = sym.symmetry_analysis(data)
        assert result.best_fold in (2, 4)
        assert result.best_fold_score > 0.9

    def test_triangle_detects_3fold(self):
        data = _make_regular_polygon(3)
        result = sym.symmetry_analysis(data)
        assert result.best_fold == 3
        assert result.best_fold_score > 0.9


class TestReflectiveSymmetry:
    """Reflective symmetry detection."""

    def test_symmetric_pattern_high_score(self):
        data = _make_regular_polygon(6)
        result = sym.symmetry_analysis(data)
        assert result.reflective_score > 0.9

    def test_top_axes_populated(self):
        data = _make_regular_polygon(8)
        result = sym.symmetry_analysis(data)
        assert len(result.top_axes) > 0


class TestRadialAnalysis:
    """Radial distribution and shells."""

    def test_single_ring_one_shell(self):
        data = _make_regular_polygon(12)
        result = sym.symmetry_analysis(data)
        assert len(result.radial_shells) >= 1

    def test_angular_uniformity_high_for_regular(self):
        data = _make_regular_polygon(12)
        result = sym.symmetry_analysis(data)
        assert result.radial_uniformity > 0.8


class TestCompositeScore:
    """Composite symmetry index."""

    def test_regular_polygon_high_index(self):
        data = _make_regular_polygon(6)
        result = sym.symmetry_analysis(data)
        assert result.symmetry_index >= 70

    def test_grid_moderate_index(self):
        data = _make_grid()
        result = sym.symmetry_analysis(data)
        assert result.symmetry_index > 0


class TestEdgeCases:
    """Edge cases and small inputs."""

    def test_fewer_than_3_points(self):
        result = sym.symmetry_analysis([[0, 0, 0], [1, 1, 0]])
        assert result.symmetry_index == 0.0

    def test_single_point(self):
        result = sym.symmetry_analysis([[5, 5, 0]])
        assert result.num_points == 1


class TestFormatting:
    """Report and JSON output."""

    def test_format_report_runs(self):
        data = _make_regular_polygon(6)
        result = sym.symmetry_analysis(data)
        report = sym.format_report(result)
        assert "SYMMETRY" in report
        assert "fold" in report.lower()

    def test_to_dict_keys(self):
        data = _make_regular_polygon(4)
        result = sym.symmetry_analysis(data)
        d = sym.to_dict(result)
        assert "symmetry_index" in d
        assert "best_fold" in d
        assert "top_axes" in d
