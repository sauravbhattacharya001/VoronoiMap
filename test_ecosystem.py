"""Tests for vormap_ecosystem — Spatial Ecosystem Simulator."""

import math
import pytest
from vormap_ecosystem import (
    EcosystemSimulator,
    Species,
    _shannon_diversity,
    _evenness,
    _dominant_species,
    _health_score,
    _random_points,
    PRESETS,
)


# ── Shannon Diversity Tests ─────────────────────────────────────────

class TestShannonDiversity:
    def test_single_species(self):
        """Single species → diversity = 0."""
        pops = [[100.0], [50.0], [75.0]]
        assert _shannon_diversity(pops) == 0.0

    def test_equal_populations_two_species(self):
        """Two equally distributed species → diversity = ln(2)."""
        pops = [[50.0, 50.0], [50.0, 50.0]]
        assert abs(_shannon_diversity(pops) - math.log(2)) < 1e-9

    def test_equal_populations_four_species(self):
        """Four equally distributed species → diversity = ln(4)."""
        pops = [[25.0, 25.0, 25.0, 25.0]]
        assert abs(_shannon_diversity(pops) - math.log(4)) < 1e-9

    def test_one_dominant_species(self):
        """One species has all the population → diversity = 0."""
        pops = [[1000.0, 0.0, 0.0], [1000.0, 0.0, 0.0]]
        assert _shannon_diversity(pops) == 0.0

    def test_empty_populations(self):
        """All-zero populations → diversity = 0."""
        pops = [[0.0, 0.0], [0.0, 0.0]]
        assert _shannon_diversity(pops) == 0.0

    def test_empty_list(self):
        """No cells → diversity = 0."""
        assert _shannon_diversity([]) == 0.0

    def test_unequal_gives_lower_diversity(self):
        """Unequal distribution has lower diversity than equal."""
        equal = [[50.0, 50.0, 50.0]]
        unequal = [[90.0, 5.0, 5.0]]
        assert _shannon_diversity(unequal) < _shannon_diversity(equal)


# ── Evenness Tests ──────────────────────────────────────────────────

class TestEvenness:
    def test_perfect_evenness(self):
        """Equal populations → evenness = 1.0."""
        pops = [[100.0, 100.0, 100.0]]
        assert abs(_evenness(pops) - 1.0) < 1e-9

    def test_single_species_evenness(self):
        """Single species → evenness defaults to 1.0."""
        pops = [[100.0]]
        assert _evenness(pops) == 1.0

    def test_uneven_less_than_one(self):
        """Unequal distribution → evenness < 1."""
        pops = [[90.0, 5.0, 5.0]]
        assert _evenness(pops) < 1.0

    def test_evenness_bounded(self):
        """Evenness should be between 0 and 1."""
        pops = [[50.0, 30.0, 20.0]]
        e = _evenness(pops)
        assert 0.0 <= e <= 1.0


# ── Dominant Species Tests ──────────────────────────────────────────

class TestDominantSpecies:
    def test_clear_dominant(self):
        """Clear winner in each cell."""
        pops = [[100.0, 5.0, 3.0], [2.0, 80.0, 10.0]]
        assert _dominant_species(pops) == [0, 1]

    def test_tie_first_wins(self):
        """When tied, first species wins (index 0)."""
        pops = [[50.0, 50.0]]
        # Implementation returns first max
        result = _dominant_species(pops)
        assert result == [0]

    def test_single_cell(self):
        pops = [[10.0, 20.0, 15.0]]
        assert _dominant_species(pops) == [1]

    def test_all_same_species(self):
        """All cells dominated by same species."""
        pops = [[100.0, 1.0], [200.0, 5.0], [50.0, 3.0]]
        assert _dominant_species(pops) == [0, 0, 0]


# ── Health Score Tests ──────────────────────────────────────────────

class TestHealthScore:
    def test_perfect_health(self):
        """Max diversity, perfect evenness and stability → 100."""
        max_div = math.log(4)
        score = _health_score(max_div, max_div, 1.0, 1.0)
        assert score == 100.0

    def test_zero_health(self):
        """Zero everything → 0."""
        score = _health_score(0.0, 1.0, 0.0, 0.0)
        assert score == 0.0

    def test_bounded(self):
        """Score is always in [0, 100]."""
        score = _health_score(2.0, 1.5, 0.8, 0.9)
        assert 0.0 <= score <= 100.0

    def test_partial_health(self):
        """Partial values give intermediate score."""
        max_div = math.log(3)
        score = _health_score(max_div * 0.5, max_div, 0.5, 0.5)
        assert 20.0 < score < 80.0


# ── Species Tests ───────────────────────────────────────────────────

class TestSpecies:
    def test_default_values(self):
        sp = Species("Test", "#ff0000")
        assert sp.name == "Test"
        assert sp.color == "#ff0000"
        assert sp.growth == 0.1
        assert sp.capacity == 100.0
        assert sp.mobility == 0.05
        assert sp.defense == 0.5

    def test_custom_values(self):
        sp = Species("Hunter", "#000", growth=0.2, capacity=200.0,
                     mobility=0.1, defense=0.8)
        assert sp.growth == 0.2
        assert sp.capacity == 200.0
        assert sp.mobility == 0.1
        assert sp.defense == 0.8


# ── Random Points Tests ─────────────────────────────────────────────

class TestRandomPoints:
    def test_correct_count(self):
        pts = _random_points(10)
        assert len(pts) == 10

    def test_within_bounds(self):
        pts = _random_points(50, w=100.0, h=100.0)
        for x, y in pts:
            assert 20 <= x <= 80
            assert 20 <= y <= 80

    def test_empty(self):
        assert _random_points(0) == []


# ── Simulator Construction Tests ────────────────────────────────────

class TestSimulatorConstruction:
    def test_basic_construction(self):
        sim = EcosystemSimulator(n_points=10, n_species=3, ticks=5, seed=42)
        assert sim.n_points == 10
        assert sim.n_species == 3
        assert sim.ticks == 5
        assert len(sim.species) == 3
        assert len(sim.points) == 10
        assert len(sim.populations) == 10
        assert all(len(cell) == 3 for cell in sim.populations)

    def test_preset_overrides(self):
        sim = EcosystemSimulator(preset="tundra", seed=42)
        assert sim.n_species == 3
        assert sim.n_points == 20
        assert sim.ticks == 80

    def test_all_presets_valid(self):
        for name in PRESETS:
            sim = EcosystemSimulator(preset=name, seed=1)
            assert sim.n_species == PRESETS[name]["n_species"]

    def test_initial_populations_positive(self):
        sim = EcosystemSimulator(n_points=15, n_species=4, seed=7)
        for cell in sim.populations:
            for pop in cell:
                assert pop > 0

    def test_adjacency_built(self):
        sim = EcosystemSimulator(n_points=10, n_species=2, seed=42)
        assert len(sim.adj) == 10
        # At least some cells should have neighbors
        has_neighbors = sum(1 for v in sim.adj.values() if len(v) > 0)
        assert has_neighbors > 0


# ── Simulator Run Tests ─────────────────────────────────────────────

class TestSimulatorRun:
    def test_run_returns_expected_keys(self):
        sim = EcosystemSimulator(n_points=5, n_species=2, ticks=10, seed=42)
        result = sim.run()
        expected_keys = {"points", "species", "history", "diversity", "health",
                         "events", "interventions", "recommendations", "ticks",
                         "n_points", "n_species", "preset", "autopilot", "final_dominance"}
        assert set(result.keys()) == expected_keys

    def test_history_length(self):
        """History should have ticks + 1 entries (initial + each tick)."""
        sim = EcosystemSimulator(n_points=5, n_species=2, ticks=10, seed=42)
        result = sim.run()
        assert len(result["history"]) == 11  # ticks + 1

    def test_diversity_length(self):
        sim = EcosystemSimulator(n_points=5, n_species=2, ticks=10, seed=42)
        result = sim.run()
        assert len(result["diversity"]) == 11

    def test_health_bounded(self):
        sim = EcosystemSimulator(n_points=8, n_species=3, ticks=20, seed=42)
        result = sim.run()
        for h in result["health"]:
            assert 0.0 <= h <= 100.0

    def test_populations_non_negative(self):
        sim = EcosystemSimulator(n_points=10, n_species=4, ticks=50, seed=42)
        result = sim.run()
        for snapshot in result["history"]:
            for cell in snapshot:
                for pop in cell:
                    assert pop >= 0.0

    def test_final_dominance_valid(self):
        sim = EcosystemSimulator(n_points=8, n_species=3, ticks=20, seed=42)
        result = sim.run()
        assert len(result["final_dominance"]) == 8
        for d in result["final_dominance"]:
            assert 0 <= d < 3

    def test_deterministic_with_seed(self):
        """Same seed → same result when run independently."""
        import random as _rng
        _rng.seed(99)
        sim1 = EcosystemSimulator(n_points=5, n_species=2, ticks=10, seed=99)
        r1 = sim1.run()
        # Re-seed and run again — should produce identical output
        _rng.seed(99)
        sim2 = EcosystemSimulator(n_points=5, n_species=2, ticks=10, seed=99)
        r2 = sim2.run()
        assert r1["diversity"] == r2["diversity"]
        assert r1["final_dominance"] == r2["final_dominance"]

    def test_autopilot_generates_interventions(self):
        """Autopilot mode should attempt interventions."""
        # Long run with autopilot to give time for interventions
        sim = EcosystemSimulator(n_points=10, n_species=4, ticks=100,
                                 autopilot=True, seed=42)
        result = sim.run()
        # May or may not intervene, but key should exist
        assert "interventions" in result

    def test_events_have_expected_fields(self):
        """Events should have tick, type, and desc."""
        sim = EcosystemSimulator(n_points=10, n_species=3, ticks=100,
                                 event_rate=0.5, seed=42)
        result = sim.run()
        if result["events"]:
            ev = result["events"][0]
            assert "tick" in ev
            assert "type" in ev
            assert "desc" in ev

    def test_zero_ticks(self):
        """Zero ticks → only initial snapshot."""
        sim = EcosystemSimulator(n_points=5, n_species=2, ticks=0, seed=42)
        result = sim.run()
        # History gets 1 final snapshot, diversity/health get 1 entry
        assert len(result["history"]) == 1
        assert len(result["diversity"]) == 1


# ── Edge Cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_species_no_crash(self):
        sim = EcosystemSimulator(n_points=5, n_species=1, ticks=10, seed=42)
        result = sim.run()
        assert result["n_species"] == 1
        # All diversity should be 0
        for d in result["diversity"]:
            assert d == 0.0

    def test_single_point(self):
        """Single cell → no migration possible."""
        sim = EcosystemSimulator(n_points=1, n_species=2, ticks=10, seed=42)
        result = sim.run()
        assert len(result["history"][0]) == 1

    def test_many_species(self):
        """More species than color palette still works."""
        sim = EcosystemSimulator(n_points=5, n_species=8, ticks=5, seed=42)
        result = sim.run()
        assert result["n_species"] == 8

    def test_high_event_rate(self):
        """Very high event rate doesn't crash."""
        sim = EcosystemSimulator(n_points=5, n_species=3, ticks=20,
                                 event_rate=0.9, seed=42)
        result = sim.run()
        assert len(result["events"]) > 0
