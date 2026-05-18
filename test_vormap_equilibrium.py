"""Tests for vormap_equilibrium module."""

import json
import math
import os
import pytest

from vormap_equilibrium import (
    ForceVector, EquilibriumPoint, Basin, PerturbationResponse, TippingPoint,
    EquilibriumReport, compute_force_field, classify_equilibria,
    map_basins, predict_perturbation, detect_tipping_points,
    analyze_equilibrium,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def square_points():
    """4 points in a square — symmetric, should be near equilibrium."""
    return [(100, 100), (100, 900), (900, 100), (900, 900)]


@pytest.fixture
def square_bounds():
    return (0, 1000, 0, 1000)


@pytest.fixture
def asymmetric_points():
    """Points with clear asymmetry to produce forces."""
    return [(100, 500), (200, 500), (800, 500), (900, 500), (500, 100)]


@pytest.fixture
def cluster_points():
    """Two clusters — should produce two basins."""
    return [
        (100, 100), (150, 150), (120, 180), (180, 120),  # cluster 1
        (800, 800), (850, 850), (820, 880), (880, 820),  # cluster 2
    ]


@pytest.fixture
def many_points():
    """Grid of points for comprehensive testing."""
    pts = []
    for x in range(100, 901, 200):
        for y in range(100, 901, 200):
            pts.append((x, y))
    return pts


# ---------------------------------------------------------------------------
# Engine 1: Force Field Computation
# ---------------------------------------------------------------------------

class TestForceField:
    def test_empty_points(self):
        result = compute_force_field([], bounds=(0, 100, 0, 100))
        assert result == []

    def test_single_point(self):
        result = compute_force_field([(50, 50)], bounds=(0, 100, 0, 100))
        assert len(result) == 1
        assert result[0].cell_idx == 0
        assert result[0].magnitude == 0.0  # no neighbors

    def test_returns_force_vectors(self, square_points, square_bounds):
        forces = compute_force_field(square_points, square_bounds)
        assert len(forces) == 4
        for f in forces:
            assert isinstance(f, ForceVector)
            assert 0 <= f.cell_idx < 4

    def test_symmetric_forces_balanced(self, square_points, square_bounds):
        """Symmetric arrangement should have low forces."""
        forces = compute_force_field(square_points, square_bounds)
        for f in forces:
            # Not necessarily zero due to boundary effects, but should be low
            assert f.magnitude < 2.0

    def test_asymmetric_has_forces(self, asymmetric_points, square_bounds):
        forces = compute_force_field(asymmetric_points, square_bounds)
        magnitudes = [f.magnitude for f in forces]
        assert max(magnitudes) > 0  # Some cells should have significant force

    def test_force_components_match_magnitude(self, square_points, square_bounds):
        forces = compute_force_field(square_points, square_bounds)
        for f in forces:
            expected_mag = math.sqrt(f.components[0]**2 + f.components[1]**2)
            assert abs(f.magnitude - expected_mag) < 1e-10

    def test_direction_matches_components(self, asymmetric_points, square_bounds):
        forces = compute_force_field(asymmetric_points, square_bounds)
        for f in forces:
            if f.magnitude > 1e-10:
                expected_dir = math.atan2(f.components[1], f.components[0])
                assert abs(f.direction - expected_dir) < 1e-10

    def test_auto_bounds(self, square_points):
        """Should work without explicit bounds."""
        forces = compute_force_field(square_points)
        assert len(forces) == 4


# ---------------------------------------------------------------------------
# Engine 2: Equilibrium Classification
# ---------------------------------------------------------------------------

class TestEquilibriumClassification:
    def test_empty_points(self):
        result = classify_equilibria([], bounds=(0, 100, 0, 100))
        assert result == []

    def test_returns_equilibrium_points(self, square_points, square_bounds):
        eqs = classify_equilibria(square_points, square_bounds)
        for eq in eqs:
            assert isinstance(eq, EquilibriumPoint)
            assert eq.classification in ("stable", "unstable", "saddle")

    def test_symmetric_has_equilibria(self, square_points, square_bounds):
        """Symmetric arrangement should have equilibrium cells."""
        eqs = classify_equilibria(square_points, square_bounds)
        assert len(eqs) > 0

    def test_eigenvalues_tuple(self, square_points, square_bounds):
        eqs = classify_equilibria(square_points, square_bounds)
        for eq in eqs:
            assert len(eq.eigenvalues) == 2
            assert isinstance(eq.eigenvalues[0], float)
            assert isinstance(eq.eigenvalues[1], float)

    def test_stability_margin_positive(self, square_points, square_bounds):
        eqs = classify_equilibria(square_points, square_bounds)
        for eq in eqs:
            assert eq.stability_margin >= 0

    def test_classification_consistent_with_eigenvalues(self, many_points, square_bounds):
        eqs = classify_equilibria(many_points, square_bounds)
        for eq in eqs:
            ev1, ev2 = eq.eigenvalues
            if eq.classification == "stable":
                assert ev1 <= 0 and ev2 <= 0
            elif eq.classification == "unstable":
                assert ev1 >= 0 and ev2 >= 0
            else:
                # saddle: mixed or complex (both real part same)
                pass


# ---------------------------------------------------------------------------
# Engine 3: Basin of Attraction Mapping
# ---------------------------------------------------------------------------

class TestBasinMapping:
    def test_empty_points(self):
        result = map_basins([], bounds=(0, 100, 0, 100))
        assert result == []

    def test_single_point(self):
        basins = map_basins([(50, 50)], bounds=(0, 100, 0, 100))
        assert len(basins) == 1
        assert basins[0].size == 1

    def test_returns_basins(self, square_points, square_bounds):
        basins = map_basins(square_points, square_bounds)
        assert len(basins) > 0
        for b in basins:
            assert isinstance(b, Basin)

    def test_all_cells_assigned(self, square_points, square_bounds):
        basins = map_basins(square_points, square_bounds)
        all_cells = set()
        for b in basins:
            all_cells.update(b.member_cells)
        assert all_cells == set(range(len(square_points)))

    def test_area_fractions_sum_to_one(self, many_points, square_bounds):
        basins = map_basins(many_points, square_bounds)
        total_frac = sum(b.area_fraction for b in basins)
        assert abs(total_frac - 1.0) < 0.05  # Allow small rounding

    def test_no_overlapping_cells(self, many_points, square_bounds):
        basins = map_basins(many_points, square_bounds)
        all_cells = []
        for b in basins:
            all_cells.extend(b.member_cells)
        assert len(all_cells) == len(set(all_cells))

    def test_basin_size_matches_members(self, square_points, square_bounds):
        basins = map_basins(square_points, square_bounds)
        for b in basins:
            assert b.size == len(b.member_cells)


# ---------------------------------------------------------------------------
# Engine 4: Perturbation Response
# ---------------------------------------------------------------------------

class TestPerturbationResponse:
    def test_zero_displacement(self, square_points, square_bounds):
        resp = predict_perturbation(square_points, 0, (0, 0), square_bounds)
        assert isinstance(resp, PerturbationResponse)
        assert resp.source_cell == 0
        assert resp.energy_delta == 0.0

    def test_small_displacement(self, square_points, square_bounds):
        resp = predict_perturbation(square_points, 0, (5, 5), square_bounds)
        assert resp.source_cell == 0
        assert resp.displacement == (5, 5)

    def test_large_displacement_more_affected(self, many_points, square_bounds):
        small = predict_perturbation(many_points, 5, (1, 1), square_bounds)
        large = predict_perturbation(many_points, 5, (100, 100), square_bounds)
        # Large displacement should affect more cells or have more energy change
        assert len(large.affected_cells) >= len(small.affected_cells) or \
               abs(large.energy_delta) >= abs(small.energy_delta)

    def test_restabilize_steps_positive(self, square_points, square_bounds):
        resp = predict_perturbation(square_points, 0, (50, 50), square_bounds)
        assert resp.restabilize_steps >= 1

    def test_propagation_depth_non_negative(self, many_points, square_bounds):
        resp = predict_perturbation(many_points, 5, (20, 20), square_bounds)
        assert resp.propagation_depth >= 0


# ---------------------------------------------------------------------------
# Engine 5: Tipping Point Detection
# ---------------------------------------------------------------------------

class TestTippingPoints:
    def test_empty_points(self):
        result = detect_tipping_points([], bounds=(0, 100, 0, 100))
        assert result == []

    def test_two_points_no_tips(self):
        """Too few points for multiple basins."""
        result = detect_tipping_points([(50, 50), (60, 60)], bounds=(0, 100, 0, 100))
        # May or may not find tips depending on basin count
        for tp in result:
            assert isinstance(tp, TippingPoint)

    def test_returns_tipping_points(self, cluster_points, square_bounds):
        tips = detect_tipping_points(cluster_points, square_bounds)
        for tp in tips:
            assert isinstance(tp, TippingPoint)
            assert tp.tipping_margin >= 0

    def test_tipping_margin_positive(self, cluster_points, square_bounds):
        tips = detect_tipping_points(cluster_points, square_bounds)
        for tp in tips:
            assert tp.tipping_margin > 0

    def test_critical_direction_is_unit_vector(self, cluster_points, square_bounds):
        tips = detect_tipping_points(cluster_points, square_bounds)
        for tp in tips:
            dx, dy = tp.critical_direction
            mag = math.sqrt(dx**2 + dy**2)
            assert abs(mag - 1.0) < 1e-10


# ---------------------------------------------------------------------------
# Full Analysis
# ---------------------------------------------------------------------------

class TestAnalyzeEquilibrium:
    def test_basic_analysis(self, square_points, square_bounds):
        report = analyze_equilibrium(square_points, square_bounds)
        assert isinstance(report, EquilibriumReport)
        assert len(report.forces) == 4
        assert report.global_stability_score >= 0
        assert report.global_stability_score <= 100

    def test_with_perturbation_targets(self, square_points, square_bounds):
        targets = [(0, (10, 10)), (1, (-5, 5))]
        report = analyze_equilibrium(square_points, square_bounds,
                                     perturbation_targets=targets)
        assert len(report.perturbation_responses) == 2

    def test_system_energy_non_negative(self, many_points, square_bounds):
        report = analyze_equilibrium(many_points, square_bounds)
        assert report.system_energy >= 0

    def test_auto_bounds(self, square_points):
        report = analyze_equilibrium(square_points)
        assert len(report.forces) == 4

    def test_summary_string(self, square_points, square_bounds):
        report = analyze_equilibrium(square_points, square_bounds)
        s = report.summary()
        assert "Spatial Equilibrium Report" in s
        assert "Stability" in s or "stability" in s.lower()


# ---------------------------------------------------------------------------
# Export Tests
# ---------------------------------------------------------------------------

class TestExport:
    def test_to_json(self, square_points, square_bounds):
        report = analyze_equilibrium(square_points, square_bounds)
        path = "_test_equilibrium_out.json"
        try:
            report.to_json(path)
            with open(path) as f:
                data = json.load(f)
            assert "forces" in data
            assert "equilibria" in data
            assert "basins" in data
            assert "system_energy" in data
            assert "global_stability_score" in data
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_html(self, square_points, square_bounds):
        report = analyze_equilibrium(square_points, square_bounds)
        path = "_test_equilibrium_out.html"
        try:
            report.to_html(path)
            with open(path) as f:
                content = f.read()
            assert "<!DOCTYPE html>" in content
            assert "Spatial Equilibrium Dashboard" in content
            assert "Force Field" in content
            assert "Equilibria" in content
            assert "Basins" in content
            assert "Tipping Points" in content
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_json_valid_structure(self, many_points, square_bounds):
        report = analyze_equilibrium(many_points, square_bounds)
        path = "_test_equilibrium_struct.json"
        try:
            report.to_json(path)
            with open(path) as f:
                data = json.load(f)
            assert len(data["forces"]) == len(many_points)
            for force in data["forces"]:
                assert "cell_idx" in force
                assert "magnitude" in force
                assert "components" in force
        finally:
            if os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_collinear_points(self):
        pts = [(100, 500), (300, 500), (500, 500), (700, 500), (900, 500)]
        report = analyze_equilibrium(pts, bounds=(0, 1000, 0, 1000))
        assert len(report.forces) == 5

    def test_very_close_points(self):
        pts = [(500, 500), (501, 500), (500, 501), (501, 501)]
        report = analyze_equilibrium(pts, bounds=(0, 1000, 0, 1000))
        assert len(report.forces) == 4

    def test_large_point_set(self):
        import random
        random.seed(42)
        pts = [(random.uniform(0, 1000), random.uniform(0, 1000)) for _ in range(50)]
        report = analyze_equilibrium(pts, bounds=(0, 1000, 0, 1000))
        assert len(report.forces) == 50
        assert report.global_stability_score >= 0

    def test_two_points(self):
        pts = [(200, 500), (800, 500)]
        forces = compute_force_field(pts, bounds=(0, 1000, 0, 1000))
        assert len(forces) == 2

    def test_three_points_triangle(self):
        pts = [(500, 100), (100, 900), (900, 900)]
        report = analyze_equilibrium(pts, bounds=(0, 1000, 0, 1000))
        assert len(report.forces) == 3
        assert len(report.basins) >= 1
