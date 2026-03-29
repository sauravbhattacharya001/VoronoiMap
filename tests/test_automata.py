"""Tests for vormap_automata — Cellular Automata on Voronoi Tessellations."""

import json
import math
import os
import unittest
from collections import Counter

from vormap_automata import (
    AutomatonResult,
    StepSnapshot,
    build_automaton,
    export_automata_csv,
    export_automata_json,
    export_automata_svg,
    format_report,
    run,
    step,
    _shannon_entropy,
    _state_label,
    _state_color,
    _snapshot,
)


# ── Test Fixtures ────────────────────────────────────────────────

def _small_adjacency():
    """5-cell graph: ring topology (each cell has 2 neighbours)."""
    seeds = [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1)]
    adj = {
        (0, 0): [(1, 0), (1, 1)],
        (1, 0): [(0, 0), (2, 0)],
        (2, 0): [(1, 0), (2, 1)],
        (2, 1): [(2, 0), (1, 1)],
        (1, 1): [(2, 1), (0, 0)],
    }
    return adj


def _grid_adjacency():
    """3x3 grid adjacency (9 cells, each has 2-4 neighbours)."""
    seeds = [(i, j) for i in range(3) for j in range(3)]
    adj = {}
    for i in range(3):
        for j in range(3):
            neighbors = []
            for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                ni, nj = i + di, j + dj
                if 0 <= ni < 3 and 0 <= nj < 3:
                    neighbors.append((ni, nj))
            adj[(i, j)] = neighbors
    return adj


def _simple_regions():
    """Minimal regions dict for SVG export tests."""
    return {
        (0, 0): [(0, 0), (50, 0), (50, 50), (0, 50)],
        (1, 0): [(50, 0), (100, 0), (100, 50), (50, 50)],
        (2, 0): [(100, 0), (150, 0), (150, 50), (100, 50)],
        (2, 1): [(100, 50), (150, 50), (150, 100), (100, 100)],
        (1, 1): [(50, 50), (100, 50), (100, 100), (50, 100)],
    }


# ── Tests ────────────────────────────────────────────────────────

class TestHelpers(unittest.TestCase):
    def test_shannon_entropy_uniform(self):
        """Uniform distribution has max entropy."""
        counts = {0: 10, 1: 10}
        self.assertAlmostEqual(_shannon_entropy(counts, 20), 1.0, places=5)

    def test_shannon_entropy_single_state(self):
        """Single state has zero entropy."""
        counts = {0: 20}
        self.assertAlmostEqual(_shannon_entropy(counts, 20), 0.0)

    def test_shannon_entropy_empty(self):
        self.assertEqual(_shannon_entropy({}, 0), 0.0)

    def test_state_label_known(self):
        self.assertEqual(_state_label("game_of_life", 0), "dead")
        self.assertEqual(_state_label("game_of_life", 1), "alive")
        self.assertEqual(_state_label("forest_fire", 2), "burning")
        self.assertEqual(_state_label("epidemic", 1), "infected")

    def test_state_label_unknown_rule(self):
        self.assertEqual(_state_label("unknown_rule", 3), "state_3")

    def test_state_color(self):
        color = _state_color("game_of_life", 1)
        self.assertTrue(color.startswith("#"))
        self.assertEqual(len(color), 7)

    def test_snapshot_counts_changes(self):
        states = {(0, 0): 1, (1, 0): 0, (2, 0): 1}
        prev = {(0, 0): 0, (1, 0): 0, (2, 0): 1}
        snap = _snapshot(1, states, 3, prev)
        self.assertEqual(snap.changed, 1)  # only (0,0) changed
        self.assertEqual(snap.state_counts, {0: 1, 1: 2})

    def test_snapshot_step_zero_no_changes(self):
        states = {(0, 0): 1, (1, 0): 0}
        snap = _snapshot(0, states, 2)
        self.assertEqual(snap.changed, 0)


class TestBuildAutomaton(unittest.TestCase):
    def test_game_of_life_default(self):
        adj = _small_adjacency()
        states = build_automaton(adj, rule="game_of_life", seed=42)
        self.assertEqual(len(states), 5)
        self.assertTrue(all(v in (0, 1) for v in states.values()))

    def test_alive_fraction(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="game_of_life",
                                 alive_fraction=1.0, seed=42)
        self.assertEqual(sum(states.values()), 9)

    def test_forest_fire_init(self):
        adj = _small_adjacency()
        states = build_automaton(adj, rule="forest_fire",
                                 tree_fraction=0.6, seed=42)
        self.assertTrue(all(v in (0, 1) for v in states.values()))
        tree_count = sum(1 for v in states.values() if v == 1)
        self.assertGreater(tree_count, 0)

    def test_epidemic_init(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="epidemic",
                                 infected_fraction=0.2, seed=42)
        infected = sum(1 for v in states.values() if v == 1)
        self.assertGreater(infected, 0)

    def test_majority_init(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="majority",
                                 num_states=4, seed=42)
        self.assertTrue(all(0 <= v <= 3 for v in states.values()))

    def test_explicit_initial_states(self):
        adj = _small_adjacency()
        explicit = {s: 1 for s in adj}
        states = build_automaton(adj, initial_states=explicit)
        self.assertTrue(all(v == 1 for v in states.values()))

    def test_seed_reproducibility(self):
        adj = _grid_adjacency()
        s1 = build_automaton(adj, rule="game_of_life", seed=123)
        s2 = build_automaton(adj, rule="game_of_life", seed=123)
        self.assertEqual(s1, s2)

    def test_different_seeds_differ(self):
        adj = _grid_adjacency()
        s1 = build_automaton(adj, rule="game_of_life", seed=1)
        s2 = build_automaton(adj, rule="game_of_life", seed=999)
        # Very unlikely to be identical with 9 cells
        # (not guaranteed, but with these seeds they differ)
        self.assertNotEqual(s1, s2)


class TestStepGameOfLife(unittest.TestCase):
    def test_all_dead_stays_dead(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        new = step(states, adj, rule="game_of_life")
        self.assertTrue(all(v == 0 for v in new.values()))

    def test_all_alive_evolves(self):
        adj = _small_adjacency()
        states = {s: 1 for s in adj}
        new = step(states, adj, rule="game_of_life")
        # All neighbors alive → fraction = 1.0 > survive_hi (0.6) → die
        self.assertTrue(all(v == 0 for v in new.values()))

    def test_isolated_cell_no_neighbors(self):
        adj = {(0, 0): []}
        states = {(0, 0): 1}
        new = step(states, adj, rule="game_of_life")
        # No neighbors → fraction = 0, below survive_lo → die
        self.assertEqual(new[(0, 0)], 1)  # actually kept because n_neighbors=0

    def test_birth_condition(self):
        # Cell (1,0) has 2 neighbors: (0,0) and (2,0)
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        states[(0, 0)] = 1  # 1 alive neighbor for (1,0)
        new = step(states, adj, rule="game_of_life")
        # (1,0) has 1/2 = 0.5 fraction → in [0.25, 0.55] → birth
        self.assertEqual(new[(1, 0)], 1)


class TestStepMajority(unittest.TestCase):
    def test_consensus(self):
        adj = _small_adjacency()
        states = {s: 1 for s in adj}
        states[(0, 0)] = 0
        new = step(states, adj, rule="majority")
        # (0,0) has neighbors (1,0)=1, (1,1)=1 → majority=1
        self.assertEqual(new[(0, 0)], 1)

    def test_tie_keeps_current(self):
        adj = _small_adjacency()
        # (0,0) has neighbors (1,0) and (1,1)
        states = {(0, 0): 0, (1, 0): 0, (2, 0): 1, (2, 1): 1, (1, 1): 1}
        new = step(states, adj, rule="majority")
        # (0,0): neighbors are (1,0)=0, (1,1)=1 → tie → keep 0
        self.assertEqual(new[(0, 0)], 0)

    def test_no_neighbors(self):
        adj = {(0, 0): []}
        states = {(0, 0): 2}
        new = step(states, adj, rule="majority")
        self.assertEqual(new[(0, 0)], 2)


class TestStepForestFire(unittest.TestCase):
    def test_burning_becomes_empty(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        states[(0, 0)] = 2  # burning
        new = step(states, adj, rule="forest_fire", seed=42)
        self.assertEqual(new[(0, 0)], 0)

    def test_fire_spreads_to_tree(self):
        import random as _r
        adj = _small_adjacency()
        states = {s: 1 for s in adj}  # all trees
        states[(0, 0)] = 2  # one burning
        # With fire_spread=1.0, all tree neighbors WILL catch fire
        new = step(states, adj, rule="forest_fire",
                   rng=_r.Random(42), fire_spread=1.0, tree_growth=0, lightning=0)
        # Neighbors of (0,0) are (1,0) and (1,1) — both should be burning
        self.assertEqual(new[(1, 0)], 2)
        self.assertEqual(new[(1, 1)], 2)

    def test_no_spread_with_zero_prob(self):
        import random as _r
        adj = _small_adjacency()
        states = {s: 1 for s in adj}
        states[(0, 0)] = 2
        new = step(states, adj, rule="forest_fire",
                   rng=_r.Random(42), fire_spread=0.0, tree_growth=0, lightning=0)
        self.assertEqual(new[(1, 0)], 1)  # stays tree
        self.assertEqual(new[(1, 1)], 1)


class TestStepEpidemic(unittest.TestCase):
    def test_infected_recovers(self):
        import random as _r
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        states[(0, 0)] = 1
        # recovery_rate=1.0 → always recovers
        new = step(states, adj, rule="epidemic",
                   rng=_r.Random(42), infection_rate=0, recovery_rate=1.0)
        self.assertEqual(new[(0, 0)], 2)

    def test_infection_spreads(self):
        import random as _r
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        states[(0, 0)] = 1
        # infection_rate=1.0 → definitely infects neighbors
        new = step(states, adj, rule="epidemic",
                   rng=_r.Random(42), infection_rate=1.0, recovery_rate=0)
        # (1,0) and (1,1) are neighbors of (0,0) → should be infected
        self.assertEqual(new[(1, 0)], 1)
        self.assertEqual(new[(1, 1)], 1)

    def test_recovered_stays_recovered(self):
        import random as _r
        adj = _small_adjacency()
        states = {s: 2 for s in adj}  # all recovered
        new = step(states, adj, rule="epidemic",
                   rng=_r.Random(42), immunity_loss=0)
        self.assertTrue(all(v == 2 for v in new.values()))

    def test_sirs_immunity_loss(self):
        import random as _r
        adj = _small_adjacency()
        states = {s: 2 for s in adj}
        # immunity_loss=1.0 → all recovered become susceptible
        new = step(states, adj, rule="epidemic",
                   rng=_r.Random(42), immunity_loss=1.0, infection_rate=0, recovery_rate=0)
        self.assertTrue(all(v == 0 for v in new.values()))


class TestCustomRule(unittest.TestCase):
    def test_custom_transition(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}

        def toggle(states, adjacency, **kw):
            return {s: 1 - v for s, v in states.items()}

        new = step(states, adj, transition_fn=toggle)
        self.assertTrue(all(v == 1 for v in new.values()))

    def test_unknown_rule_raises(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        with self.assertRaises(ValueError):
            step(states, adj, rule="nonexistent")


class TestRun(unittest.TestCase):
    def test_run_returns_result(self):
        adj = _small_adjacency()
        states = build_automaton(adj, rule="game_of_life", seed=42)
        result = run(states, adj, rule="game_of_life", steps=10, seed=42)
        self.assertIsInstance(result, AutomatonResult)
        self.assertEqual(result.num_cells, 5)
        self.assertGreater(len(result.history), 0)
        self.assertLessEqual(result.steps_run, 10)

    def test_run_stops_on_convergence(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}  # all dead → will stay dead
        result = run(states, adj, rule="game_of_life", steps=100,
                     stop_on_convergence=True, seed=42)
        self.assertTrue(result.converged)
        self.assertLess(result.steps_run, 100)

    def test_run_no_convergence_stop(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        result = run(states, adj, rule="game_of_life", steps=5,
                     stop_on_convergence=False, seed=42)
        self.assertEqual(result.steps_run, 5)

    def test_run_records_history(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="majority", num_states=3, seed=42)
        result = run(states, adj, rule="majority", steps=10, seed=42,
                     num_states=3, record_interval=1)
        # History should have step 0 + up to 10 more
        self.assertGreaterEqual(len(result.history), 2)
        self.assertEqual(result.history[0].step, 0)

    def test_run_record_interval(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="majority", num_states=2, seed=42)
        result = run(states, adj, rule="majority", steps=10,
                     record_interval=5, stop_on_convergence=False, seed=42,
                     num_states=2)
        # Step 0 always recorded, then every 5: 5, 10
        steps_recorded = [s.step for s in result.history]
        self.assertIn(0, steps_recorded)
        self.assertIn(5, steps_recorded)
        self.assertIn(10, steps_recorded)

    def test_run_invalid_steps(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        with self.assertRaises(ValueError):
            run(states, adj, steps=0)

    def test_forest_fire_dynamics(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="forest_fire", tree_fraction=0.8, seed=42)
        # Ignite one cell
        first_tree = next(s for s, v in states.items() if v == 1)
        states[first_tree] = 2  # burning
        result = run(states, adj, rule="forest_fire", steps=20, seed=42,
                     fire_spread=0.5, tree_growth=0.0)
        # Fire should have spread and burned out
        self.assertIsInstance(result, AutomatonResult)
        self.assertEqual(result.rule, "forest_fire")

    def test_epidemic_dynamics(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="epidemic", infected_fraction=0.1, seed=42)
        result = run(states, adj, rule="epidemic", steps=30, seed=42,
                     infection_rate=0.5, recovery_rate=0.2)
        self.assertEqual(result.num_states, 3)
        # Should have some recovered cells by end
        final = result.final_counts
        self.assertIn(2, final)  # recovered

    def test_convergence_step(self):
        adj = _small_adjacency()
        states = {s: 0 for s in adj}
        result = run(states, adj, rule="game_of_life", steps=100, seed=42)
        self.assertIsNotNone(result.convergence_step)

    def test_summary_text(self):
        adj = _small_adjacency()
        states = build_automaton(adj, rule="game_of_life", seed=42)
        result = run(states, adj, rule="game_of_life", steps=5, seed=42)
        text = result.summary()
        self.assertIn("game_of_life", text)
        self.assertIn("Cells:", text)
        self.assertIn("Steps run:", text)


class TestExportJSON(unittest.TestCase):
    def test_json_export(self):
        adj = _small_adjacency()
        states = build_automaton(adj, rule="game_of_life", seed=42)
        result = run(states, adj, rule="game_of_life", steps=5, seed=42)

        path = f"_test_output_1.json"

        try:
            export_automata_json(result, path)
            with open(path) as f:
                data = json.load(f)

            self.assertEqual(data["rule"], "game_of_life")
            self.assertEqual(data["num_cells"], 5)
            self.assertIn("history", data)
            self.assertIn("final_states", data)
            self.assertGreater(len(data["history"]), 0)
        finally:
            os.unlink(path)

    def test_json_has_step_counts(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="majority", num_states=2, seed=42)
        result = run(states, adj, rule="majority", steps=3, seed=42, num_states=2)

        path = f"_test_output_2.json"

        try:
            export_automata_json(result, path)
            with open(path) as f:
                data = json.load(f)
            for entry in data["history"]:
                self.assertIn("step", entry)
                self.assertIn("state_counts", entry)
                self.assertIn("changed", entry)
                self.assertIn("entropy", entry)
        finally:
            os.unlink(path)


class TestExportCSV(unittest.TestCase):
    def test_csv_export(self):
        adj = _small_adjacency()
        states = build_automaton(adj, rule="game_of_life", seed=42)
        result = run(states, adj, rule="game_of_life", steps=5, seed=42)

        path = f"_test_output_3.csv"

        try:
            export_automata_csv(result, path)
            with open(path) as f:
                lines = f.read().strip().split("\n")

            self.assertGreater(len(lines), 1)
            header = lines[0].split(",")
            self.assertIn("step", header)
            self.assertIn("changed", header)
            self.assertIn("entropy", header)
        finally:
            os.unlink(path)


class TestExportSVG(unittest.TestCase):
    def test_svg_export(self):
        adj = _small_adjacency()
        regions = _simple_regions()
        data = list(adj.keys())
        states = build_automaton(adj, rule="game_of_life", seed=42)
        result = run(states, adj, rule="game_of_life", steps=5, seed=42)

        path = f"_test_output_4.svg"

        try:
            export_automata_svg(result, regions, data, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("polygon", content)
        finally:
            os.unlink(path)

    def test_svg_with_seeds(self):
        adj = _small_adjacency()
        regions = _simple_regions()
        data = list(adj.keys())
        states = build_automaton(adj, rule="forest_fire", seed=42)
        result = run(states, adj, rule="forest_fire", steps=3, seed=42)

        path = f"_test_output_5.svg"

        try:
            export_automata_svg(result, regions, data, path, show_seeds=True)
            with open(path) as f:
                content = f.read()
            self.assertIn("circle", content)
        finally:
            os.unlink(path)

    def test_svg_step_index(self):
        adj = _small_adjacency()
        regions = _simple_regions()
        data = list(adj.keys())
        states = build_automaton(adj, rule="game_of_life", seed=42)
        result = run(states, adj, rule="game_of_life", steps=5,
                     seed=42, stop_on_convergence=False)

        path = f"_test_output_6.svg"

        try:
            export_automata_svg(result, regions, data, path, step_index=0)
            with open(path) as f:
                content = f.read()
            self.assertIn("step 0", content)
        finally:
            os.unlink(path)

    def test_svg_empty_history_raises(self):
        regions = _simple_regions()
        data = list(regions.keys())
        result = AutomatonResult(
            rule="test", steps_run=0, num_cells=0, num_states=2, history=[]
        )
        with self.assertRaises(ValueError):
            export_automata_svg(result, regions, data, "out.svg")


class TestFormatReport(unittest.TestCase):
    def test_report_contains_key_info(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="epidemic", infected_fraction=0.1, seed=42)
        result = run(states, adj, rule="epidemic", steps=10, seed=42,
                     infection_rate=0.3, recovery_rate=0.1)
        report = format_report(result)
        self.assertIn("epidemic", report)
        self.assertIn("Step", report)
        self.assertIn("susceptible", report.lower())

    def test_report_long_run_truncated(self):
        adj = _grid_adjacency()
        states = build_automaton(adj, rule="majority", num_states=2, seed=42)
        result = run(states, adj, rule="majority", steps=50,
                     stop_on_convergence=False, seed=42, num_states=2)
        report = format_report(result)
        # Should show "..." for truncated steps
        self.assertIn("...", report)


class TestEdgeCases(unittest.TestCase):
    def test_single_cell(self):
        adj = {(0, 0): []}
        states = {(0, 0): 1}
        result = run(states, adj, rule="game_of_life", steps=5, seed=42)
        self.assertEqual(result.num_cells, 1)

    def test_two_cells(self):
        adj = {(0, 0): [(1, 0)], (1, 0): [(0, 0)]}
        states = {(0, 0): 1, (1, 0): 0}
        result = run(states, adj, rule="game_of_life", steps=5, seed=42)
        self.assertEqual(result.num_cells, 2)

    def test_all_same_state_converges(self):
        adj = _grid_adjacency()
        states = {s: 1 for s in adj}
        result = run(states, adj, rule="majority", steps=10, seed=42, num_states=2)
        # All same → should converge immediately
        self.assertTrue(result.converged)
        self.assertEqual(result.convergence_step, 1)


if __name__ == "__main__":
    unittest.main()
