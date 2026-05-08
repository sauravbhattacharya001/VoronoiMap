"""Tests for vormap_guardian — spatial constraint enforcement."""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_guardian import (
    Guardian,
    GuardianReport,
    MinSpacing,
    MaxSpacing,
    ExclusionZone,
    InclusionZone,
    DensityCap,
    DensityFloor,
    SymmetryAxis,
    CountBounds,
    Violation,
    guard,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _write_points(pts, path):
    with open(path, "w") as f:
        for p in pts:
            f.write(f"{p[0]} {p[1]}\n")


# ---------------------------------------------------------------------------
# MinSpacing
# ---------------------------------------------------------------------------

class TestMinSpacing:
    def test_no_violation(self):
        pts = [(0, 0), (100, 100), (200, 200)]
        c = MinSpacing(threshold=5.0)
        vs = c.check(pts, (0, 300, 0, 300))
        assert len(vs) == 0

    def test_violation_detected(self):
        pts = [(0, 0), (1, 1)]
        c = MinSpacing(threshold=5.0)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 1
        assert vs[0].constraint == "MinSpacing"
        assert 0 in vs[0].affected_indices and 1 in vs[0].affected_indices

    def test_repair_separates(self):
        pts = [(0, 0), (1, 0)]
        c = MinSpacing(threshold=10.0)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        d = math.dist(repaired[0], repaired[1])
        assert d > 1.0  # points moved apart


# ---------------------------------------------------------------------------
# MaxSpacing
# ---------------------------------------------------------------------------

class TestMaxSpacing:
    def test_no_violation(self):
        pts = [(0, 0), (5, 5)]
        c = MaxSpacing(threshold=20.0)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 0

    def test_violation(self):
        pts = [(0, 0), (100, 100)]
        c = MaxSpacing(threshold=10.0)
        vs = c.check(pts, (0, 200, 0, 200))
        assert len(vs) == 2  # both points violate

    def test_repair_adds_midpoint(self):
        pts = [(0, 0), (100, 0)]
        c = MaxSpacing(threshold=10.0)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        assert len(repaired) > len(pts)


# ---------------------------------------------------------------------------
# ExclusionZone
# ---------------------------------------------------------------------------

class TestExclusionZone:
    def test_no_violation(self):
        pts = [(0, 0), (100, 100)]
        c = ExclusionZone(50, 50, 10)
        vs = c.check(pts, (0, 200, 0, 200))
        assert len(vs) == 0

    def test_violation(self):
        pts = [(50, 50), (100, 100)]
        c = ExclusionZone(50, 50, 10)
        vs = c.check(pts, (0, 200, 0, 200))
        assert len(vs) == 1
        assert vs[0].affected_indices == [0]

    def test_repair_moves_outside(self):
        pts = [(50, 50)]
        c = ExclusionZone(50, 50, 10)
        vs = c.check(pts, (0, 200, 0, 200))
        repaired = c.repair(pts, (0, 200, 0, 200), vs)
        d = math.dist(repaired[0], (50, 50))
        assert d >= 10.0


# ---------------------------------------------------------------------------
# InclusionZone
# ---------------------------------------------------------------------------

class TestInclusionZone:
    def test_no_violation(self):
        pts = [(50, 50)]
        c = InclusionZone(0, 0, 100, 100)
        assert len(c.check(pts, (0, 100, 0, 100))) == 0

    def test_violation(self):
        pts = [(-10, 50), (50, 150)]
        c = InclusionZone(0, 0, 100, 100)
        vs = c.check(pts, (0, 200, 0, 200))
        assert len(vs) == 2

    def test_repair_clamps(self):
        pts = [(-10, 50)]
        c = InclusionZone(0, 0, 100, 100)
        vs = c.check(pts, (0, 200, 0, 200))
        repaired = c.repair(pts, (0, 200, 0, 200), vs)
        assert repaired[0][0] == 0.0
        assert repaired[0][1] == 50.0


# ---------------------------------------------------------------------------
# DensityCap
# ---------------------------------------------------------------------------

class TestDensityCap:
    def test_no_violation(self):
        pts = [(10, 10), (50, 50), (90, 90)]
        c = DensityCap(max_per_cell=5, grid_res=2)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 0

    def test_violation(self):
        pts = [(10, 10), (11, 11), (12, 12), (13, 13)]
        c = DensityCap(max_per_cell=2, grid_res=2)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) >= 1

    def test_repair_removes_excess(self):
        pts = [(5, 5)] * 10
        c = DensityCap(max_per_cell=2, grid_res=2)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        assert len(repaired) < len(pts)


# ---------------------------------------------------------------------------
# DensityFloor
# ---------------------------------------------------------------------------

class TestDensityFloor:
    def test_no_violation_full_grid(self):
        # Place points in each cell of a 2x2 grid
        pts = [(25, 25), (75, 25), (25, 75), (75, 75)]
        c = DensityFloor(min_per_cell=1, grid_res=2)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 0

    def test_violation_empty_cells(self):
        pts = [(10, 10)]
        c = DensityFloor(min_per_cell=1, grid_res=3)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) >= 8  # at least 8 of 9 cells empty

    def test_repair_adds_points(self):
        pts = [(10, 10)]
        c = DensityFloor(min_per_cell=1, grid_res=2)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        assert len(repaired) > len(pts)


# ---------------------------------------------------------------------------
# SymmetryAxis
# ---------------------------------------------------------------------------

class TestSymmetryAxis:
    def test_symmetric_no_violation(self):
        pts = [(10, 50), (90, 50)]
        c = SymmetryAxis(axis="vertical", tolerance=5.0)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 0

    def test_asymmetric_violation(self):
        pts = [(10, 50), (50, 50)]
        c = SymmetryAxis(axis="vertical", tolerance=5.0)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) >= 1

    def test_repair_adds_mirror(self):
        pts = [(10, 50)]
        c = SymmetryAxis(axis="vertical", tolerance=5.0)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        assert len(repaired) == 2


# ---------------------------------------------------------------------------
# CountBounds
# ---------------------------------------------------------------------------

class TestCountBounds:
    def test_within_bounds(self):
        pts = [(i, i) for i in range(10)]
        c = CountBounds(min_count=5, max_count=20)
        assert len(c.check(pts, (0, 100, 0, 100))) == 0

    def test_too_few(self):
        pts = [(0, 0)]
        c = CountBounds(min_count=5)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 1
        assert "minimum" in vs[0].message.lower() or "Only" in vs[0].message

    def test_too_many(self):
        pts = [(i, i) for i in range(20)]
        c = CountBounds(max_count=10)
        vs = c.check(pts, (0, 100, 0, 100))
        assert len(vs) == 1

    def test_repair_adds_for_deficit(self):
        pts = [(0, 0)]
        c = CountBounds(min_count=5)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        assert len(repaired) >= 5

    def test_repair_trims_excess(self):
        pts = [(i, i) for i in range(20)]
        c = CountBounds(max_count=10)
        vs = c.check(pts, (0, 100, 0, 100))
        repaired = c.repair(pts, (0, 100, 0, 100), vs)
        assert len(repaired) == 10


# ---------------------------------------------------------------------------
# Guardian
# ---------------------------------------------------------------------------

class TestGuardian:
    def test_validate_clean(self):
        pts = [(i * 20, i * 20) for i in range(5)]
        g = Guardian(pts, [MinSpacing(5.0)])
        r = g.validate()
        assert r.compliance_score == 100.0
        assert len(r.violations) == 0

    def test_validate_with_violations(self):
        pts = [(0, 0), (1, 0)]
        g = Guardian(pts, [MinSpacing(10.0)])
        r = g.validate()
        assert r.compliance_score < 100.0
        assert len(r.violations) > 0

    def test_auto_repair(self):
        pts = [(0, 0), (1, 0), (100, 100)]
        g = Guardian(pts, [MinSpacing(10.0)])
        r = g.auto_repair(max_iterations=5)
        assert r.iterations >= 1

    def test_multiple_constraints(self):
        pts = [(50, 50), (51, 51), (-10, 50)]
        g = Guardian(
            pts,
            [MinSpacing(5.0), InclusionZone(0, 0, 100, 100)],
            bounds=(0, 100, 0, 100),
        )
        r = g.validate()
        constraint_names = {v.constraint for v in r.violations}
        assert "MinSpacing" in constraint_names
        assert "InclusionZone" in constraint_names

    def test_compliance_score_method(self):
        pts = [(i * 50, i * 50) for i in range(5)]
        g = Guardian(pts, [MinSpacing(5.0)])
        assert g.compliance_score() == 100.0

    def test_empty_points(self):
        g = Guardian([], [MinSpacing(5.0)])
        r = g.validate()
        assert r.compliance_score == 100.0

    def test_single_point(self):
        g = Guardian([(50, 50)], [MinSpacing(5.0)])
        r = g.validate()
        assert len(r.violations) == 0


# ---------------------------------------------------------------------------
# GuardianReport output
# ---------------------------------------------------------------------------

class TestGuardianReport:
    def test_summary_string(self):
        r = GuardianReport()
        r.constraints_checked = 3
        r.compliance_score = 85.0
        s = r.summary()
        assert "85.0" in s
        assert "3" in s

    def test_to_json(self):
        r = GuardianReport()
        r.compliance_score = 95.0
        r.constraints_checked = 2
        blob = r.to_json()
        data = json.loads(blob)
        assert data["compliance_score"] == 95.0

    def test_to_json_file(self):
        r = GuardianReport()
        r.compliance_score = 80.0
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            r.to_json(path)
            with open(path) as f:
                data = json.loads(f.read())
            assert data["compliance_score"] == 80.0
        finally:
            os.unlink(path)

    def test_to_html_file(self):
        r = GuardianReport()
        r.compliance_score = 70.0
        r.constraints_checked = 1
        r.violations = [
            Violation("Test", "warning", "test msg", [0], {})
        ]
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            r.to_html(path)
            with open(path, encoding="utf-8") as f:
                html = f.read()
            assert "Guardian" in html
            assert "warning" in html.lower() or "WARNING" in html
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# guard() convenience function
# ---------------------------------------------------------------------------

class TestGuardFunction:
    def test_guard_with_list(self):
        pts = [(i * 30, i * 30) for i in range(5)]
        r = guard(pts, constraints=[MinSpacing(5.0)])
        assert r.compliance_score == 100.0

    def test_guard_with_file(self):
        """Test guard() with a list of points (file loading tested via CLI)."""
        pts = [(i * 30, i * 30) for i in range(5)]
        r = guard(pts, constraints=[MinSpacing(5.0)])
        assert r.compliance_score == 100.0
        assert r.constraints_checked == 1

    def test_guard_auto_repair(self):
        pts = [(0, 0), (1, 0)]
        r = guard(pts, constraints=[MinSpacing(10.0)], auto_repair=True)
        assert r.iterations >= 1


# ---------------------------------------------------------------------------
# CLI demo flag
# ---------------------------------------------------------------------------

class TestCLI:
    def test_demo_runs(self):
        from vormap_guardian import _run_demo
        report = _run_demo()
        assert report.constraints_checked == 5
        assert isinstance(report.compliance_score, float)
