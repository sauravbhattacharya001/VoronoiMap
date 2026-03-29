"""Tests for vormap_diffusion — spatial diffusion simulation."""

import json
import math
import os
import tempfile
import unittest

from vormap_diffusion import (
    DiffusionFrame,
    DiffusionResult,
    heat_diffusion,
    sir_simulation,
    threshold_diffusion,
    export_diffusion_json,
    export_diffusion_csv,
    export_diffusion_svg,
    format_report,
)


def _simple_adjacency():
    """Build a simple 4-cell adjacency graph (square pattern).

    Topology:
        A -- B
        |    |
        C -- D
    """
    A = (0.0, 0.0)
    B = (100.0, 0.0)
    C = (0.0, 100.0)
    D = (100.0, 100.0)
    adj = {
        A: [B, C],
        B: [A, D],
        C: [A, D],
        D: [B, C],
    }
    return adj, [A, B, C, D]


def _line_adjacency(n=5):
    """Build a linear chain: 0 -- 1 -- 2 -- ... -- n-1."""
    seeds = [(float(i * 100), 0.0) for i in range(n)]
    adj = {s: [] for s in seeds}
    for i in range(n - 1):
        adj[seeds[i]].append(seeds[i + 1])
        adj[seeds[i + 1]].append(seeds[i])
    return adj, seeds


def _simple_regions(seeds):
    """Build minimal square regions around each seed."""
    regions = {}
    for s in seeds:
        x, y = s
        regions[s] = [
            (x - 25, y - 25), (x + 25, y - 25),
            (x + 25, y + 25), (x - 25, y + 25),
        ]
    return regions


# ── Heat Diffusion Tests ────────────────────────────────────────────

class TestHeatDiffusion(unittest.TestCase):

    def test_basic_heat(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0

        result = heat_diffusion(initial, adj, steps=10, alpha=0.2)

        self.assertEqual(result.model, "heat")
        self.assertEqual(len(result.frames), 11)  # 0 + 10 steps
        self.assertEqual(result.frames[0].step, 0)
        self.assertEqual(result.frames[10].step, 10)

    def test_heat_conservation(self):
        """Total heat should be conserved (insulated boundary)."""
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0
        total_initial = sum(initial.values())

        result = heat_diffusion(initial, adj, steps=20, alpha=0.2)

        for frame in result.frames:
            total = sum(frame.values.values())
            self.assertAlmostEqual(total, total_initial, places=6,
                                   msg=f"Heat not conserved at step {frame.step}")

    def test_heat_diffuses(self):
        """Source value should decrease as heat spreads."""
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0

        result = heat_diffusion(initial, adj, steps=5, alpha=0.2)

        # Source should lose heat
        self.assertLess(result.frames[5].values[seeds[0]], 100.0)
        # Neighbours should gain heat
        self.assertGreater(result.frames[5].values[seeds[1]], 0.0)

    def test_heat_convergence(self):
        """After many steps, all values should approach the mean."""
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0
        mean_val = 100.0 / 4

        result = heat_diffusion(initial, adj, steps=200, alpha=0.2)

        final = result.frames[-1].values
        for s in seeds:
            self.assertAlmostEqual(final[s], mean_val, places=1)

    def test_heat_invalid_alpha(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        with self.assertRaises(ValueError):
            heat_diffusion(initial, adj, alpha=0)
        with self.assertRaises(ValueError):
            heat_diffusion(initial, adj, alpha=-0.1)

    def test_heat_unstable_alpha(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        # max degree = 2, alpha=0.6 -> 0.6*2 = 1.2 >= 1.0
        with self.assertRaises(ValueError):
            heat_diffusion(initial, adj, alpha=0.6)

    def test_heat_invalid_steps(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        with self.assertRaises(ValueError):
            heat_diffusion(initial, adj, steps=0)

    def test_heat_fixed_boundary(self):
        """Fixed boundary cells should hold their initial values."""
        adj, seeds = _line_adjacency(5)
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0

        result = heat_diffusion(initial, adj, steps=20, alpha=0.3,
                                boundary="fixed")

        # Endpoints have degree 1, avg degree ~1.6, so < 0.5*1.6=0.8
        # They should hold their initial values
        for frame in result.frames:
            self.assertEqual(frame.values[seeds[0]], 100.0)
            self.assertEqual(frame.values[seeds[-1]], 0.0)

    def test_heat_summary(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0

        result = heat_diffusion(initial, adj, steps=5, alpha=0.2)

        self.assertIn("total_steps", result.summary)
        self.assertIn("initial_range", result.summary)
        self.assertIn("final_range", result.summary)
        self.assertEqual(result.summary["total_steps"], 5)

    def test_heat_line_gradient(self):
        """Heat should propagate along a line chain."""
        adj, seeds = _line_adjacency(5)
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0

        result = heat_diffusion(initial, adj, steps=3, alpha=0.3)

        # After a few steps, should see a gradient
        final = result.frames[-1].values
        for i in range(len(seeds) - 1):
            self.assertGreaterEqual(final[seeds[i]], final[seeds[i + 1]])


# ── SIR Model Tests ────────────────────────────────────────────────

class TestSIRSimulation(unittest.TestCase):

    def test_basic_sir(self):
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, steps=20, beta=0.5, gamma=0.2, seed=42)

        self.assertEqual(result.model, "sir")
        self.assertGreaterEqual(len(result.frames), 2)  # At least initial + 1

    def test_sir_initial_state(self):
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, initial_infected=[0], steps=1, seed=42)

        initial = result.frames[0].values
        self.assertEqual(initial[seeds[0]], "I")
        for s in seeds[1:]:
            self.assertEqual(initial[s], "S")

    def test_sir_multiple_infected(self):
        adj, seeds = _simple_adjacency()
        sorted_seeds = sorted(adj.keys())
        result = sir_simulation(adj, initial_infected=[0, 2], steps=1, seed=42)

        initial = result.frames[0].values
        self.assertEqual(initial[sorted_seeds[0]], "I")
        self.assertEqual(initial[sorted_seeds[2]], "I")

    def test_sir_no_negative_counts(self):
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, steps=50, seed=42)

        for frame in result.frames:
            counts = {"S": 0, "I": 0, "R": 0}
            for v in frame.values.values():
                counts[v] += 1
            total = counts["S"] + counts["I"] + counts["R"]
            self.assertEqual(total, len(seeds))

    def test_sir_early_termination(self):
        """Simulation should stop when no infected cells remain."""
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, steps=1000, beta=0.9, gamma=0.9, seed=42)

        # Should terminate well before 1000 steps
        self.assertLess(len(result.frames), 1000)

    def test_sir_zero_beta(self):
        """With beta=0, infection should not spread."""
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, beta=0.0, gamma=0.1, steps=20, seed=42)

        # Only cell 0 should ever be infected/recovered
        final = result.frames[-1].values
        for s in seeds[1:]:
            self.assertEqual(final[s], "S")

    def test_sir_invalid_params(self):
        adj, seeds = _simple_adjacency()
        with self.assertRaises(ValueError):
            sir_simulation(adj, beta=-0.1)
        with self.assertRaises(ValueError):
            sir_simulation(adj, beta=1.5)
        with self.assertRaises(ValueError):
            sir_simulation(adj, gamma=-0.1)

    def test_sir_summary(self):
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, steps=50, seed=42)

        self.assertIn("peak_infected", result.summary)
        self.assertIn("attack_rate", result.summary)
        self.assertIn("final_susceptible", result.summary)

    def test_sir_reproducible(self):
        """Same seed should produce same results."""
        adj, seeds = _simple_adjacency()
        r1 = sir_simulation(adj, steps=20, seed=123)
        r2 = sir_simulation(adj, steps=20, seed=123)

        for f1, f2 in zip(r1.frames, r2.frames):
            self.assertEqual(f1.values, f2.values)


# ── Threshold Model Tests ──────────────────────────────────────────

class TestThresholdDiffusion(unittest.TestCase):

    def test_basic_threshold(self):
        adj, seeds = _simple_adjacency()
        result = threshold_diffusion(adj, steps=10, threshold=0.4)

        self.assertEqual(result.model, "threshold")
        self.assertGreaterEqual(len(result.frames), 2)

    def test_threshold_initial_state(self):
        adj, seeds = _simple_adjacency()
        result = threshold_diffusion(adj, initial_adopters=[0], steps=1)

        initial = result.frames[0].values
        self.assertEqual(initial[seeds[0]], 1)
        for s in seeds[1:]:
            self.assertEqual(initial[s], 0)

    def test_threshold_spreading(self):
        """With low threshold and high connectivity, adoption should spread."""
        adj, seeds = _simple_adjacency()
        # threshold=0.4, cell 0 adopted -> neighbours have 1/2 = 0.5 > 0.4
        result = threshold_diffusion(
            adj, initial_adopters=[0], threshold=0.4, steps=10
        )
        final = result.frames[-1].values
        # At least one neighbour should have adopted
        adopted = sum(1 for v in final.values() if v == 1)
        self.assertGreater(adopted, 1)

    def test_threshold_high_barrier(self):
        """With very high threshold, adoption should not spread from 1 cell."""
        adj, seeds = _simple_adjacency()
        # threshold=1.0 means ALL neighbours must be adopted
        result = threshold_diffusion(
            adj, initial_adopters=[0], threshold=1.0, steps=10
        )
        final = result.frames[-1].values
        # Only cell 0 should be adopted
        adopted = sum(1 for v in final.values() if v == 1)
        self.assertEqual(adopted, 1)

    def test_threshold_irreversible(self):
        """Once adopted, a cell should stay adopted."""
        adj, seeds = _simple_adjacency()
        result = threshold_diffusion(
            adj, initial_adopters=[0], threshold=0.3, steps=20
        )
        for fi in range(len(result.frames) - 1):
            curr = result.frames[fi].values
            nxt = result.frames[fi + 1].values
            for s in seeds:
                if curr[s] == 1:
                    self.assertEqual(nxt[s], 1,
                                     f"Cell {s} unadopted at step {fi + 1}")

    def test_threshold_early_termination(self):
        """Should stop when no more adoptions occur."""
        adj, seeds = _simple_adjacency()
        result = threshold_diffusion(
            adj, initial_adopters=[0], threshold=1.0, steps=100
        )
        # Should terminate early since cell 0 alone can't trigger any adoption
        self.assertLess(len(result.frames), 100)

    def test_threshold_invalid_params(self):
        adj, seeds = _simple_adjacency()
        with self.assertRaises(ValueError):
            threshold_diffusion(adj, threshold=0)
        with self.assertRaises(ValueError):
            threshold_diffusion(adj, threshold=1.5)

    def test_threshold_summary(self):
        adj, seeds = _simple_adjacency()
        result = threshold_diffusion(adj, steps=10, threshold=0.4)

        self.assertIn("total_adopted", result.summary)
        self.assertIn("adoption_rate", result.summary)
        self.assertIn("converged", result.summary)


# ── Export Tests ────────────────────────────────────────────────────

class TestExports(unittest.TestCase):

    def _get_heat_result(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0
        return heat_diffusion(initial, adj, steps=3, alpha=0.2), seeds

    def test_export_json(self):
        result, seeds = self._get_heat_result()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_diffusion_json(result, path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["model"], "heat")
            self.assertEqual(len(data["frames"]), 4)
            self.assertEqual(len(data["seeds"]), 4)
        finally:
            os.unlink(path)

    def test_export_csv(self):
        result, seeds = self._get_heat_result()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            export_diffusion_csv(result, path)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            # Header + (4 cells * 4 frames)
            self.assertEqual(len(lines), 1 + 4 * 4)
            self.assertIn("step", lines[0])
        finally:
            os.unlink(path)

    def test_export_svg(self):
        result, seeds = self._get_heat_result()
        regions = _simple_regions(seeds)
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_diffusion_svg(result, regions, list(seeds), path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("@keyframes", content)
            self.assertIn("Heat Diffusion", content)
        finally:
            os.unlink(path)

    def test_export_sir_svg(self):
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, steps=5, seed=42)
        regions = _simple_regions(list(seeds))
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_diffusion_svg(result, regions, list(seeds), path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("SIR Epidemic", content)
            self.assertIn("Susceptible", content)
        finally:
            os.unlink(path)


# ── Report Tests ────────────────────────────────────────────────────

class TestFormatReport(unittest.TestCase):

    def test_heat_report(self):
        adj, seeds = _simple_adjacency()
        initial = {s: 0.0 for s in seeds}
        initial[seeds[0]] = 100.0
        result = heat_diffusion(initial, adj, steps=5, alpha=0.2)

        report = format_report(result)
        self.assertIn("HEAT", report)
        self.assertIn("alpha", report)
        self.assertIn("total_steps", report)

    def test_sir_report(self):
        adj, seeds = _simple_adjacency()
        result = sir_simulation(adj, steps=10, seed=42)

        report = format_report(result)
        self.assertIn("SIR", report)
        self.assertIn("beta", report)
        self.assertIn("peak_infected", report)

    def test_threshold_report(self):
        adj, seeds = _simple_adjacency()
        result = threshold_diffusion(adj, steps=5, threshold=0.4)

        report = format_report(result)
        self.assertIn("THRESHOLD", report)
        self.assertIn("total_adopted", report)


if __name__ == "__main__":
    unittest.main()
