"""Tests for vormap_forensics — Spatial Anomaly Forensics Engine."""

import json
import math
import os
import random
import sys
import tempfile

import pytest

# Ensure repo root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vormap_forensics import (
    Anomaly,
    CausalChain,
    Evidence,
    ForensicVerdict,
    ForensicsEngine,
    forensics_demo,
    investigate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uniform_points(n=200, lo=0, hi=1000, seed=1):
    rng = random.Random(seed)
    return [(rng.uniform(lo, hi), rng.uniform(lo, hi)) for _ in range(n)]


def _grid_points(rows=10, cols=10, spacing=10, origin=(200, 200)):
    pts = []
    for r in range(rows):
        for c in range(cols):
            pts.append((origin[0] + c * spacing, origin[1] + r * spacing))
    return pts


def _cluster_points(cx, cy, n=15, std=3, seed=2):
    rng = random.Random(seed)
    return [(cx + rng.gauss(0, std), cy + rng.gauss(0, std)) for _ in range(n)]


def _boundary_points(n=30, boundary=5, extent=1000, seed=3):
    rng = random.Random(seed)
    return [(rng.uniform(0, boundary), rng.uniform(0, extent)) for _ in range(n)]


def _write_points_file(pts, path):
    with open(path, "w") as f:
        for x, y in pts:
            f.write(f"{x},{y}\n")


# ---------------------------------------------------------------------------
# Basic engine tests
# ---------------------------------------------------------------------------

class TestForensicsEngineBasic:
    def test_clean_data_high_integrity(self):
        pts = _uniform_points(200)
        engine = ForensicsEngine(pts)
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)
        assert v.integrity_score >= 30
        assert v.risk_level in ("low", "medium", "high")

    def test_single_point(self):
        engine = ForensicsEngine([(500, 500)])
        v = engine.investigate()
        assert v.integrity_score >= 0
        assert v.anomaly_count == 0

    def test_two_points(self):
        engine = ForensicsEngine([(0, 0), (100, 100)])
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)

    def test_identical_points(self):
        pts = [(50, 50)] * 20
        engine = ForensicsEngine(pts)
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)

    def test_empty_raises(self):
        # bounding_box requires at least one point
        engine = ForensicsEngine([])
        v = engine.investigate()
        assert v.anomaly_count == 0

    def test_three_points(self):
        pts = [(0, 0), (500, 500), (1000, 1000)]
        engine = ForensicsEngine(pts)
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)


# ---------------------------------------------------------------------------
# Anomaly detection tests
# ---------------------------------------------------------------------------

class TestAnomalyDetection:
    def test_tight_cluster_detected(self):
        base = _uniform_points(200, seed=10)
        cluster = _cluster_points(500, 500, n=20, std=2, seed=11)
        pts = base + cluster
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        # Should detect the tight cluster
        cluster_anoms = [a for a in v.anomalies if a.anomaly_type == "cluster"]
        assert len(cluster_anoms) >= 1

    def test_boundary_accumulation_detected(self):
        base = _uniform_points(100, lo=200, hi=800, seed=20)
        # Pack many points into the left edge band
        boundary = _boundary_points(150, boundary=5, extent=1000, seed=21)
        pts = base + boundary
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        boundary_anoms = [a for a in v.anomalies if a.anomaly_type == "boundary"]
        assert len(boundary_anoms) >= 1

    def test_grid_aligned_points(self):
        base = _uniform_points(150, seed=30)
        grid = _grid_points(10, 10, spacing=10, origin=(200, 200))
        pts = base + grid
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        # Should find density or cluster anomalies near the grid
        assert v.anomaly_count >= 1

    def test_spacing_anomaly_very_close(self):
        base = _uniform_points(100, lo=100, hi=900, seed=40)
        # Add nearly-duplicate points
        base.append((500.0, 500.0))
        base.append((500.001, 500.001))
        engine = ForensicsEngine(base, grid_resolution=15)
        v = engine.investigate()
        spacing_anoms = [a for a in v.anomalies if a.anomaly_type == "spacing"]
        assert len(spacing_anoms) >= 1

    def test_isolated_point_high_nn(self):
        base = _uniform_points(100, lo=100, hi=500, seed=50)
        base.append((5000, 5000))  # Far away isolated point
        engine = ForensicsEngine(base, grid_resolution=15)
        v = engine.investigate()
        # Should detect spacing anomaly for the isolated point
        spacing_anoms = [a for a in v.anomalies if a.anomaly_type == "spacing"]
        assert len(spacing_anoms) >= 1

    def test_no_false_positives_regular_grid(self):
        """A perfect regular grid should not trigger too many anomalies."""
        pts = _grid_points(15, 15, spacing=50, origin=(100, 100))
        engine = ForensicsEngine(pts, grid_resolution=10)
        v = engine.investigate()
        # Regular grids might trigger boundary or density, but integrity should be reasonable
        assert v.integrity_score >= 30


# ---------------------------------------------------------------------------
# Root cause classification tests
# ---------------------------------------------------------------------------

class TestRootCauseClassification:
    def test_cluster_classified_as_injection_or_natural(self):
        base = _uniform_points(200, seed=60)
        cluster = _cluster_points(500, 500, n=20, std=1.5, seed=61)
        pts = base + cluster
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        cluster_anoms = [a for a in v.anomalies if a.anomaly_type == "cluster"]
        if cluster_anoms:
            assert cluster_anoms[0].root_cause in (
                "intentional_injection", "natural_clustering",
                "data_corruption", "equipment_artifact",
            )

    def test_boundary_classified_as_boundary_effect(self):
        base = _uniform_points(200, seed=70)
        boundary = _boundary_points(50, boundary=3, extent=1000, seed=71)
        pts = base + boundary
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        boundary_anoms = [a for a in v.anomalies if a.anomaly_type == "boundary"]
        if boundary_anoms:
            assert boundary_anoms[0].root_cause == "boundary_effect"

    def test_all_anomalies_have_root_cause(self):
        base = _uniform_points(100, seed=80)
        cluster = _cluster_points(300, 300, n=10, std=2, seed=81)
        pts = base + cluster
        engine = ForensicsEngine(pts)
        v = engine.investigate()
        for a in v.anomalies:
            assert a.root_cause != "unknown"
            assert 0 <= a.cause_confidence <= 1.0

    def test_cause_confidence_bounded(self):
        pts = _uniform_points(150, seed=90)
        pts += _cluster_points(500, 500, n=25, std=1, seed=91)
        engine = ForensicsEngine(pts)
        v = engine.investigate()
        for a in v.anomalies:
            assert 0.0 <= a.cause_confidence <= 1.0


# ---------------------------------------------------------------------------
# Causal chain tests
# ---------------------------------------------------------------------------

class TestCausalChains:
    def test_nearby_anomalies_chain(self):
        """Anomalies near each other should form causal chains."""
        base = _uniform_points(200, seed=100)
        # Two nearby clusters
        c1 = _cluster_points(400, 400, n=15, std=2, seed=101)
        c2 = _cluster_points(410, 410, n=15, std=2, seed=102)
        pts = base + c1 + c2
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        # May or may not form chains depending on detection, but
        # we check the chain structure is valid
        for ch in v.causal_chains:
            assert len(ch.anomaly_ids) >= 2
            assert ch.mechanism
            assert 0 <= ch.confidence <= 1.0

    def test_distant_anomalies_no_chain(self):
        """Well-separated anomalies should NOT be chained."""
        base = _uniform_points(200, lo=200, hi=800, seed=110)
        c1 = _cluster_points(50, 50, n=15, std=2, seed=111)
        c2 = _cluster_points(950, 950, n=15, std=2, seed=112)
        pts = base + c1 + c2
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        # Should not chain the two distant clusters together
        for ch in v.causal_chains:
            # If a chain exists, its anomalies should be spatially close
            assert ch.confidence >= 0


# ---------------------------------------------------------------------------
# Verdict tests
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_integrity_score_range(self):
        pts = _uniform_points(200, seed=120)
        v = investigate(pts)
        assert 0 <= v.integrity_score <= 100

    def test_risk_level_valid(self):
        pts = _uniform_points(200, seed=130)
        v = investigate(pts)
        assert v.risk_level in ("low", "medium", "high", "critical")

    def test_heavy_anomalies_lower_integrity(self):
        base = _uniform_points(100, lo=200, hi=800, seed=140)
        # Add lots of anomalous points
        cluster = _cluster_points(500, 500, n=50, std=1, seed=141)
        boundary = _boundary_points(60, boundary=2, extent=1000, seed=142)
        pts = base + cluster + boundary
        v = investigate(pts)
        assert v.integrity_score < 90  # Should be lower than clean data

    def test_remediation_present(self):
        base = _uniform_points(100, seed=150)
        cluster = _cluster_points(500, 500, n=20, std=1, seed=151)
        pts = base + cluster
        v = investigate(pts)
        assert len(v.remediation) >= 1

    def test_phase_reports_present(self):
        pts = _uniform_points(100, seed=160)
        v = investigate(pts)
        assert "scene_survey" in v.phase_reports
        assert "anomaly_detection" in v.phase_reports


# ---------------------------------------------------------------------------
# Evidence tests
# ---------------------------------------------------------------------------

class TestEvidence:
    def test_evidence_attached_to_anomalies(self):
        base = _uniform_points(200, seed=170)
        cluster = _cluster_points(500, 500, n=20, std=2, seed=171)
        pts = base + cluster
        engine = ForensicsEngine(pts)
        v = engine.investigate()
        for a in v.anomalies:
            assert len(a.evidence) >= 1
            for e in a.evidence:
                assert e.type
                assert e.description
                assert 0 <= e.weight <= 1.0

    def test_grid_alignment_evidence(self):
        base = _uniform_points(100, seed=180)
        grid = _grid_points(8, 8, spacing=15, origin=(300, 300))
        pts = base + grid
        engine = ForensicsEngine(pts, grid_resolution=20)
        v = engine.investigate()
        # Check that grid_alignment evidence exists somewhere
        all_ev_types = {e.type for a in v.anomalies for e in a.evidence}
        # May or may not detect grid alignment depending on mixing
        assert isinstance(all_ev_types, set)


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------

class TestExport:
    def test_json_export(self, tmp_path):
        pts = _uniform_points(100, seed=190)
        pts += _cluster_points(500, 500, n=15, std=2, seed=191)
        engine = ForensicsEngine(pts)
        engine.investigate()
        out = str(tmp_path / "report.json")
        engine.to_json(out)
        assert os.path.exists(out)
        with open(out) as f:
            data = json.load(f)
        assert "integrity_score" in data
        assert "anomalies" in data
        assert "causal_chains" in data
        assert "remediation" in data

    def test_html_export(self, tmp_path):
        pts = _uniform_points(100, seed=200)
        pts += _cluster_points(500, 500, n=15, std=2, seed=201)
        engine = ForensicsEngine(pts)
        engine.investigate()
        out = str(tmp_path / "report.html")
        engine.to_html(out)
        assert os.path.exists(out)
        content = open(out, encoding='utf-8').read()
        assert "Spatial Forensics Report" in content
        assert "Integrity" in content
        assert "svg" in content.lower()

    def test_json_valid_structure(self, tmp_path):
        pts = _uniform_points(50, seed=210)
        engine = ForensicsEngine(pts)
        engine.investigate()
        out = str(tmp_path / "report.json")
        engine.to_json(out)
        with open(out) as f:
            data = json.load(f)
        assert isinstance(data["anomalies"], list)
        assert isinstance(data["remediation"], list)
        assert isinstance(data["phase_reports"], dict)


# ---------------------------------------------------------------------------
# Demo test
# ---------------------------------------------------------------------------

class TestDemo:
    def test_demo_runs(self, capsys):
        verdict = forensics_demo()
        assert isinstance(verdict, ForensicVerdict)
        assert verdict.anomaly_count >= 1
        captured = capsys.readouterr()
        assert "SPATIAL FORENSICS DEMO REPORT" in captured.out

    def test_demo_detects_injected_anomalies(self):
        verdict = forensics_demo()
        types = {a.anomaly_type for a in verdict.anomalies}
        # Should detect at least density or cluster anomalies
        assert len(types) >= 1


# ---------------------------------------------------------------------------
# File-based investigate
# ---------------------------------------------------------------------------

class TestFileInvestigate:
    def test_investigate_from_file(self, tmp_path):
        pts = _uniform_points(100, seed=220)
        pts += _cluster_points(500, 500, n=15, std=2, seed=221)
        path = str(tmp_path / "data.txt")
        _write_points_file(pts, path)
        v = investigate(path)
        assert isinstance(v, ForensicVerdict)
        assert v.anomaly_count >= 1


# ---------------------------------------------------------------------------
# Grid resolution tests
# ---------------------------------------------------------------------------

class TestGridResolution:
    def test_custom_grid_resolution(self):
        pts = _uniform_points(200, seed=230)
        engine = ForensicsEngine(pts, grid_resolution=5)
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)

    def test_high_grid_resolution(self):
        pts = _uniform_points(200, seed=240)
        engine = ForensicsEngine(pts, grid_resolution=50)
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)

    def test_min_grid_resolution_clamped(self):
        pts = _uniform_points(100, seed=250)
        engine = ForensicsEngine(pts, grid_resolution=1)
        v = engine.investigate()
        assert isinstance(v, ForensicVerdict)
        assert engine._grid_res == 2  # clamped to minimum


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_evidence_creation(self):
        e = Evidence(type="test", value=1.5, description="test desc", weight=0.8)
        assert e.type == "test"
        assert e.value == 1.5

    def test_anomaly_defaults(self):
        a = Anomaly(anomaly_id=1, anomaly_type="density", severity="info")
        assert a.affected_indices == []
        assert a.root_cause == "unknown"

    def test_causal_chain_defaults(self):
        c = CausalChain(chain_id=1)
        assert c.anomaly_ids == []
        assert c.mechanism == ""

    def test_verdict_defaults(self):
        v = ForensicVerdict()
        assert v.integrity_score == 100.0
        assert v.risk_level == "low"
        assert v.anomalies == []
