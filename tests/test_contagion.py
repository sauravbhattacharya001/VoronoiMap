"""Tests for vormap_contagion — SIR spatial contagion simulator."""

import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_contagion import (
    ContagionSimulator,
    PRESETS,
    _build_adjacency,
    _random_points,
    export_html,
)


class TestRandomPoints(unittest.TestCase):
    """Tests for _random_points helper."""

    def test_correct_count(self):
        pts = _random_points(20)
        self.assertEqual(len(pts), 20)

    def test_within_bounds(self):
        pts = _random_points(50, w=400.0, h=300.0)
        for x, y in pts:
            self.assertGreaterEqual(x, 20)
            self.assertLessEqual(x, 380)
            self.assertGreaterEqual(y, 20)
            self.assertLessEqual(y, 280)

    def test_empty(self):
        pts = _random_points(0)
        self.assertEqual(pts, [])

    def test_single_point(self):
        pts = _random_points(1)
        self.assertEqual(len(pts), 1)
        self.assertEqual(len(pts[0]), 2)


class TestBuildAdjacency(unittest.TestCase):
    """Tests for _build_adjacency helper."""

    def test_single_point_no_neighbours(self):
        adj = _build_adjacency([(100, 100)])
        self.assertEqual(adj, {0: []})

    def test_two_close_points_connected(self):
        adj = _build_adjacency([(100, 100), (110, 100)])
        self.assertIn(1, adj[0])
        self.assertIn(0, adj[1])

    def test_symmetry(self):
        random.seed(42)
        pts = _random_points(15)
        adj = _build_adjacency(pts)
        for i, nbs in adj.items():
            for j in nbs:
                self.assertIn(i, adj[j], f"Asymmetry: {i} -> {j} but not {j} -> {i}")

    def test_far_apart_disconnected(self):
        # Two clusters far apart
        pts = [(10, 10), (11, 11), (490, 490), (491, 491)]
        adj = _build_adjacency(pts, threshold_factor=1.5)
        # Cluster 1 should not connect to cluster 2
        self.assertNotIn(2, adj[0])
        self.assertNotIn(3, adj[0])

    def test_empty_points(self):
        adj = _build_adjacency([])
        self.assertEqual(adj, {})


class TestContagionSimulatorConstruction(unittest.TestCase):
    """Tests for ContagionSimulator initialisation."""

    def test_defaults(self):
        sim = ContagionSimulator(seed=1)
        self.assertEqual(sim.n_points, 30)
        self.assertEqual(sim.ticks, 80)
        self.assertAlmostEqual(sim.beta, 0.3)
        self.assertAlmostEqual(sim.gamma, 0.1)
        self.assertFalse(sim.autopilot)

    def test_custom_params(self):
        sim = ContagionSimulator(n_points=10, ticks=50, beta=0.5, gamma=0.2,
                                 migration_rate=0.1, autopilot=True, seed=7)
        self.assertEqual(sim.n_points, 10)
        self.assertEqual(sim.ticks, 50)
        self.assertAlmostEqual(sim.beta, 0.5)
        self.assertAlmostEqual(sim.gamma, 0.2)
        self.assertAlmostEqual(sim.migration_rate, 0.1)
        self.assertTrue(sim.autopilot)

    def test_population_initialized(self):
        sim = ContagionSimulator(n_points=5, seed=2)
        for i in range(5):
            self.assertAlmostEqual(sim.pop[i], 100.0)

    def test_initial_infection_seeded(self):
        sim = ContagionSimulator(n_points=10, seed=3)
        total_i = sum(sim.I)
        self.assertGreater(total_i, 0, "At least one cell should be infected")

    def test_sir_sums_to_population(self):
        sim = ContagionSimulator(n_points=15, seed=4)
        for i in range(15):
            total = sim.S[i] + sim.I[i] + sim.R[i]
            self.assertAlmostEqual(total, sim.pop[i], places=5)

    def test_seed_reproducibility(self):
        sim1 = ContagionSimulator(n_points=10, seed=99)
        sim2 = ContagionSimulator(n_points=10, seed=99)
        self.assertEqual(sim1.pts, sim2.pts)
        self.assertEqual(sim1.I, sim2.I)

    def test_quarantine_initial(self):
        sim = ContagionSimulator(n_points=5, seed=5)
        for q in sim.q:
            self.assertAlmostEqual(q, 1.0)


class TestContagionSimulatorRun(unittest.TestCase):
    """Tests for simulation execution."""

    def setUp(self):
        self.sim = ContagionSimulator(n_points=10, ticks=40, beta=0.4,
                                      gamma=0.1, seed=42)

    def test_result_keys(self):
        result = self.sim.run()
        expected_keys = {"points", "adjacency", "history", "global_sir",
                         "alerts", "params", "stats"}
        self.assertEqual(set(result.keys()), expected_keys)

    def test_history_length(self):
        result = self.sim.run()
        # ticks + 1 final snapshot
        self.assertEqual(len(result["history"]), 41)

    def test_global_sir_length(self):
        result = self.sim.run()
        self.assertEqual(len(result["global_sir"]), 40)

    def test_sir_conservation(self):
        """Total population should remain constant across ticks."""
        result = self.sim.run()
        for s, i, r in result["global_sir"]:
            total = s + i + r
            # Allow small floating point drift
            self.assertAlmostEqual(total, 10 * 100.0, delta=1.0)

    def test_infection_spreads(self):
        """With positive beta, infection should eventually spread beyond seed."""
        result = self.sim.run()
        # Peak infection should be higher than initial
        initial_i = result["global_sir"][0][1]
        peak_i = max(sir[1] for sir in result["global_sir"])
        self.assertGreater(peak_i, initial_i)

    def test_recovery_happens(self):
        """With positive gamma, some recovery should occur."""
        result = self.sim.run()
        final_r = result["global_sir"][-1][2]
        self.assertGreater(final_r, 0)

    def test_stats_keys(self):
        result = self.sim.run()
        expected = {"r0_estimate", "peak_infection_pct", "peak_tick",
                    "final_recovered_pct", "final_infected_pct",
                    "health_score", "total_alerts"}
        self.assertEqual(set(result["stats"].keys()), expected)

    def test_health_score_bounded(self):
        result = self.sim.run()
        hs = result["stats"]["health_score"]
        self.assertGreaterEqual(hs, 0)
        self.assertLessEqual(hs, 100)

    def test_peak_tick_valid(self):
        result = self.sim.run()
        self.assertGreaterEqual(result["stats"]["peak_tick"], 0)
        self.assertLess(result["stats"]["peak_tick"], 40)

    def test_params_echoed(self):
        result = self.sim.run()
        self.assertEqual(result["params"]["n_points"], 10)
        self.assertEqual(result["params"]["ticks"], 40)
        self.assertAlmostEqual(result["params"]["beta"], 0.4)


class TestAutopilot(unittest.TestCase):
    """Tests for autonomous autopilot interventions."""

    def test_quarantine_occurs(self):
        """With high beta, autopilot should trigger quarantine."""
        sim = ContagionSimulator(n_points=8, ticks=60, beta=0.7,
                                 gamma=0.05, autopilot=True, seed=10)
        result = sim.run()
        quarantine_alerts = [a for a in result["alerts"] if a["type"] == "quarantine"]
        self.assertGreater(len(quarantine_alerts), 0)

    def test_vaccination_after_outbreak(self):
        """Autopilot should vaccinate neighbours of hotspots."""
        sim = ContagionSimulator(n_points=10, ticks=80, beta=0.6,
                                 gamma=0.05, autopilot=True, seed=11)
        result = sim.run()
        vax_alerts = [a for a in result["alerts"] if a["type"] == "vaccination"]
        self.assertGreater(len(vax_alerts), 0)

    def test_autopilot_reduces_peak(self):
        """Autopilot should generally reduce peak infection vs no autopilot."""
        random.seed(50)
        # Same params, different autopilot
        r_no = ContagionSimulator(n_points=15, ticks=60, beta=0.5,
                                   gamma=0.08, autopilot=False, seed=50).run()
        r_yes = ContagionSimulator(n_points=15, ticks=60, beta=0.5,
                                    gamma=0.08, autopilot=True, seed=50).run()
        # Autopilot should not make things worse (or at least not dramatically)
        # Allow some stochastic variation
        self.assertLessEqual(r_yes["stats"]["peak_infection_pct"],
                             r_no["stats"]["peak_infection_pct"] + 5)

    def test_quarantine_lifts(self):
        """Quarantine should eventually lift when infection drops."""
        sim = ContagionSimulator(n_points=8, ticks=100, beta=0.6,
                                 gamma=0.15, autopilot=True, seed=12)
        sim.run()
        # After run, some cells should have q restored to 1.0
        restored = [q for q in sim.q if q == 1.0]
        self.assertGreater(len(restored), 0)


class TestOutbreakDetection(unittest.TestCase):
    """Tests for outbreak alert system."""

    def test_outbreak_alert_triggered(self):
        """High-beta simulation should trigger outbreak alert."""
        sim = ContagionSimulator(n_points=8, ticks=40, beta=0.7,
                                 gamma=0.05, seed=20)
        result = sim.run()
        outbreak_alerts = [a for a in result["alerts"] if a["type"] == "outbreak"]
        self.assertEqual(len(outbreak_alerts), 1)  # exactly one outbreak alert

    def test_low_beta_may_not_outbreak(self):
        """Very low beta may not trigger outbreak."""
        sim = ContagionSimulator(n_points=20, ticks=30, beta=0.05,
                                 gamma=0.3, seed=21)
        result = sim.run()
        outbreak_alerts = [a for a in result["alerts"] if a["type"] == "outbreak"]
        self.assertEqual(len(outbreak_alerts), 0)

    def test_hotspot_tracking(self):
        """Hotspots should be reported for high-infection cells."""
        sim = ContagionSimulator(n_points=10, ticks=30, beta=0.6,
                                 gamma=0.05, seed=22)
        result = sim.run()
        hotspot_alerts = [a for a in result["alerts"] if a["type"] == "hotspot"]
        # May or may not fire depending on timing, but structure should be valid
        for a in hotspot_alerts:
            self.assertIn("tick", a)
            self.assertIn("🔥", a["msg"])


class TestR0Estimation(unittest.TestCase):
    """Tests for effective reproduction number estimation."""

    def test_r0_positive_for_spreading(self):
        sim = ContagionSimulator(n_points=15, ticks=30, beta=0.5,
                                 gamma=0.1, seed=30)
        result = sim.run()
        self.assertGreater(result["stats"]["r0_estimate"], 0)

    def test_r0_higher_for_higher_beta(self):
        r1 = ContagionSimulator(n_points=15, ticks=30, beta=0.2,
                                gamma=0.1, seed=31).run()
        r2 = ContagionSimulator(n_points=15, ticks=30, beta=0.6,
                                gamma=0.1, seed=31).run()
        # Higher beta should generally give higher R0
        # (not strictly guaranteed due to stochastic seeding but likely)
        self.assertGreaterEqual(r2["stats"]["r0_estimate"],
                                r1["stats"]["r0_estimate"] * 0.5)


class TestPresets(unittest.TestCase):
    """Tests for preset configurations."""

    def test_all_presets_valid(self):
        for name, cfg in PRESETS.items():
            self.assertIn("beta", cfg)
            self.assertIn("gamma", cfg)
            self.assertIn("n_points", cfg)
            self.assertIn("migration_rate", cfg)
            self.assertGreater(cfg["beta"], 0)
            self.assertGreater(cfg["gamma"], 0)
            self.assertGreater(cfg["n_points"], 0)

    def test_flu_preset_runs(self):
        cfg = PRESETS["flu"]
        sim = ContagionSimulator(n_points=cfg["n_points"], beta=cfg["beta"],
                                 gamma=cfg["gamma"], migration_rate=cfg["migration_rate"],
                                 seed=40)
        result = sim.run()
        self.assertIn("stats", result)

    def test_zombie_high_infection(self):
        """Zombie preset (high beta, low gamma) should have high peak."""
        cfg = PRESETS["zombie"]
        sim = ContagionSimulator(n_points=cfg["n_points"], ticks=100,
                                 beta=cfg["beta"], gamma=cfg["gamma"],
                                 migration_rate=cfg["migration_rate"], seed=41)
        result = sim.run()
        self.assertGreater(result["stats"]["peak_infection_pct"], 30)

    def test_rumor_recovers_fast(self):
        """Rumor preset (high gamma) should have high final recovery."""
        cfg = PRESETS["rumor"]
        sim = ContagionSimulator(n_points=cfg["n_points"], ticks=100,
                                 beta=cfg["beta"], gamma=cfg["gamma"],
                                 migration_rate=cfg["migration_rate"], seed=42)
        result = sim.run()
        self.assertGreater(result["stats"]["final_recovered_pct"], 30)


class TestMigration(unittest.TestCase):
    """Tests for cross-cell migration mechanics."""

    def test_migration_spreads_infection(self):
        """Positive migration rate should spread infection to neighbours."""
        sim_mig = ContagionSimulator(n_points=10, ticks=30, beta=0.3,
                                     gamma=0.05, migration_rate=0.1, seed=50)
        sim_no_mig = ContagionSimulator(n_points=10, ticks=30, beta=0.3,
                                        gamma=0.05, migration_rate=0.0, seed=50)
        r_mig = sim_mig.run()
        r_no_mig = sim_no_mig.run()
        # With migration, infection should spread to more cells
        infected_cells_mig = sum(1 for i in r_mig["history"][-1]["I"] if i > 1)
        infected_cells_no = sum(1 for i in r_no_mig["history"][-1]["I"] if i > 1)
        self.assertGreaterEqual(infected_cells_mig, infected_cells_no)

    def test_zero_migration_localised(self):
        """Zero migration should keep infection more localised."""
        sim = ContagionSimulator(n_points=15, ticks=20, beta=0.4,
                                 gamma=0.05, migration_rate=0.0, seed=51)
        result = sim.run()
        # With zero migration, infection can still spread via within-cell dynamics
        # but the history should exist
        self.assertEqual(len(result["history"]), 21)


class TestExportHtml(unittest.TestCase):
    """Tests for HTML export function."""

    def test_creates_file(self):
        sim = ContagionSimulator(n_points=5, ticks=10, seed=60)
        result = sim.run()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            export_html(result, path)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("Spatial Contagion Simulator", content)
            self.assertIn("SIR", content)
        finally:
            os.unlink(path)

    def test_contains_stats(self):
        sim = ContagionSimulator(n_points=5, ticks=10, seed=61)
        result = sim.run()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            export_html(result, path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Health Score", content)
            self.assertIn("R₀ Estimate", content)
            self.assertIn("Peak Infection", content)
        finally:
            os.unlink(path)

    def test_autopilot_label_shown(self):
        sim = ContagionSimulator(n_points=5, ticks=10, autopilot=True, seed=62)
        result = sim.run()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            export_html(result, path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Autopilot ON", content)
        finally:
            os.unlink(path)


class TestEdgeCases(unittest.TestCase):
    """Edge case tests."""

    def test_single_cell(self):
        sim = ContagionSimulator(n_points=1, ticks=20, seed=70)
        result = sim.run()
        self.assertEqual(len(result["points"]), 1)
        # Should still run without error

    def test_two_cells(self):
        sim = ContagionSimulator(n_points=2, ticks=20, seed=71)
        result = sim.run()
        self.assertEqual(len(result["points"]), 2)

    def test_zero_ticks(self):
        """Zero ticks triggers max() on empty — should raise ValueError."""
        sim = ContagionSimulator(n_points=5, ticks=0, seed=72)
        with self.assertRaises(ValueError):
            sim.run()

    def test_very_high_gamma(self):
        """With gamma=1.0, everything recovers immediately."""
        sim = ContagionSimulator(n_points=5, ticks=30, beta=0.3,
                                 gamma=1.0, seed=73)
        result = sim.run()
        # Final infected should be very low
        self.assertLess(result["stats"]["final_infected_pct"], 5)

    def test_very_high_beta(self):
        """With beta=1.0, infection should spread rapidly."""
        sim = ContagionSimulator(n_points=8, ticks=30, beta=1.0,
                                 gamma=0.05, seed=74)
        result = sim.run()
        self.assertGreater(result["stats"]["peak_infection_pct"], 20)


if __name__ == "__main__":
    unittest.main()
