#!/usr/bin/env python3
"""Tests for vormap_genetics — Spatial Genetics Engine."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_genetics import (
    FixationEvent,
    GeneticsEngine,
    GeneticsResult,
    MigrationFlow,
    _euclidean,
    _knn_adjacency,
    _pearson,
    genetics_analyze,
    genetics_demo,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _grid_points(n=16):
    """4x4 grid."""
    pts = []
    for i in range(4):
        for j in range(4):
            pts.append((float(i * 10), float(j * 10)))
    return pts


def _write_points(pts, path):
    with open(path, "w") as f:
        for x, y in pts:
            f.write(f"{x} {y}\n")


# ---------------------------------------------------------------------------
# Engine 1 — Gene Pool Initializer
# ---------------------------------------------------------------------------

class TestGenePoolInit(unittest.TestCase):
    def test_all_loci_assigned(self):
        e = GeneticsEngine(points=_grid_points(), seed=1)
        cells = e._init_gene_pools()
        for c in cells:
            self.assertEqual(len(c.allele_freqs), 5)

    def test_freq_in_range(self):
        e = GeneticsEngine(points=_grid_points(), seed=2)
        cells = e._init_gene_pools()
        for c in cells:
            for locus, freq in c.allele_freqs.items():
                self.assertGreaterEqual(freq, 0.0)
                self.assertLessEqual(freq, 1.0)

    def test_environment_assignment(self):
        pts = [(0, 0), (0, 50), (0, 100)]
        e = GeneticsEngine(points=pts, seed=3)
        cells = e._init_gene_pools()
        envs = {c.environment for c in cells}
        self.assertTrue(len(envs) >= 2)

    def test_population_positive(self):
        e = GeneticsEngine(points=_grid_points(), seed=4)
        cells = e._init_gene_pools()
        for c in cells:
            self.assertGreater(c.population_size, 0)

    def test_custom_loci(self):
        e = GeneticsEngine(points=_grid_points(), loci=["a", "b"], seed=5)
        cells = e._init_gene_pools()
        for c in cells:
            self.assertEqual(set(c.allele_freqs.keys()), {"a", "b"})


# ---------------------------------------------------------------------------
# Engine 2 — Selection Pressure
# ---------------------------------------------------------------------------

class TestSelection(unittest.TestCase):
    def test_selection_changes_freqs(self):
        e = GeneticsEngine(points=[(0, 0), (0, 100)], seed=10)
        cells = e._init_gene_pools()
        before = {c.cell_id: dict(c.allele_freqs) for c in cells}
        e._apply_selection(cells)
        changed = False
        for c in cells:
            for locus in e.loci:
                if abs(c.allele_freqs[locus] - before[c.cell_id][locus]) > 1e-10:
                    changed = True
        self.assertTrue(changed)

    def test_freqs_stay_bounded(self):
        e = GeneticsEngine(points=_grid_points(), seed=11)
        cells = e._init_gene_pools()
        for _ in range(100):
            e._apply_selection(cells)
        for c in cells:
            for freq in c.allele_freqs.values():
                self.assertGreaterEqual(freq, 0.0)
                self.assertLessEqual(freq, 1.0)

    def test_fitness_values_set(self):
        e = GeneticsEngine(points=[(0, 0)], seed=12)
        cells = e._init_gene_pools()
        e._apply_selection(cells)
        c = cells[0]
        self.assertIsInstance(c.fitness_a, float)
        self.assertIsInstance(c.fitness_ab, float)
        self.assertIsInstance(c.fitness_b, float)


# ---------------------------------------------------------------------------
# Engine 3 — Migration
# ---------------------------------------------------------------------------

class TestMigration(unittest.TestCase):
    def test_migration_converges(self):
        pts = [(0, 0), (1, 0)]
        e = GeneticsEngine(points=pts, seed=20, migration_scale=0.5)
        cells = e._init_gene_pools()
        # Force different frequencies
        cells[0].allele_freqs["color"] = 0.9
        cells[1].allele_freqs["color"] = 0.1
        for _ in range(50):
            e._apply_migration(cells)
        diff = abs(cells[0].allele_freqs["color"] - cells[1].allele_freqs["color"])
        self.assertLess(diff, 0.1)

    def test_migration_returns_flows(self):
        e = GeneticsEngine(points=_grid_points(), seed=21)
        cells = e._init_gene_pools()
        flows = e._apply_migration(cells)
        self.assertGreater(len(flows), 0)
        self.assertIsInstance(flows[0], MigrationFlow)

    def test_genetic_distance_computed(self):
        e = GeneticsEngine(points=_grid_points(), seed=22)
        cells = e._init_gene_pools()
        flows = e._apply_migration(cells)
        for f in flows:
            self.assertGreaterEqual(f.genetic_distance, 0.0)

    def test_migration_rate_positive(self):
        e = GeneticsEngine(points=_grid_points(), seed=23)
        cells = e._init_gene_pools()
        flows = e._apply_migration(cells)
        for f in flows:
            self.assertGreater(f.rate, 0.0)


# ---------------------------------------------------------------------------
# Engine 4 — Genetic Drift
# ---------------------------------------------------------------------------

class TestDrift(unittest.TestCase):
    def test_drift_changes_freqs(self):
        e = GeneticsEngine(points=_grid_points(), seed=30)
        cells = e._init_gene_pools()
        before = [dict(c.allele_freqs) for c in cells]
        e._apply_drift(cells)
        changed = False
        for i, c in enumerate(cells):
            for locus in e.loci:
                if abs(c.allele_freqs[locus] - before[i][locus]) > 1e-10:
                    changed = True
        self.assertTrue(changed)

    def test_small_pop_drifts_more(self):
        pts = [(0, 0), (10, 0)]
        e = GeneticsEngine(points=pts, seed=31)
        cells = e._init_gene_pools()
        cells[0].population_size = 5
        cells[1].population_size = 10000
        # Run many drift rounds and measure variance
        freq_log_small = []
        freq_log_large = []
        for _ in range(200):
            cells[0].allele_freqs["color"] = 0.5
            cells[1].allele_freqs["color"] = 0.5
            e._apply_drift(cells)
            freq_log_small.append(cells[0].allele_freqs["color"])
            freq_log_large.append(cells[1].allele_freqs["color"])
        var_small = sum((x - 0.5) ** 2 for x in freq_log_small) / len(freq_log_small)
        var_large = sum((x - 0.5) ** 2 for x in freq_log_large) / len(freq_log_large)
        self.assertGreater(var_small, var_large)

    def test_freqs_stay_bounded(self):
        e = GeneticsEngine(points=_grid_points(), seed=32)
        cells = e._init_gene_pools()
        for _ in range(100):
            e._apply_drift(cells)
        for c in cells:
            for freq in c.allele_freqs.values():
                self.assertGreaterEqual(freq, 0.0)
                self.assertLessEqual(freq, 1.0)

    def test_drift_variance_set(self):
        e = GeneticsEngine(points=[(0, 0)], seed=33)
        cells = e._init_gene_pools()
        e._apply_drift(cells)
        self.assertGreater(cells[0].drift_variance, 0.0)


# ---------------------------------------------------------------------------
# Engine 5 — Mutation
# ---------------------------------------------------------------------------

class TestMutation(unittest.TestCase):
    def test_mutation_moves_freq(self):
        e = GeneticsEngine(points=[(0, 0)], seed=40, mutation_rate=0.1)
        cells = e._init_gene_pools()
        cells[0].allele_freqs["color"] = 0.0
        e._apply_mutation(cells)
        self.assertGreater(cells[0].allele_freqs["color"], 0.0)

    def test_mutation_bounded(self):
        e = GeneticsEngine(points=_grid_points(), seed=41, mutation_rate=0.01)
        cells = e._init_gene_pools()
        for _ in range(1000):
            e._apply_mutation(cells)
        for c in cells:
            for freq in c.allele_freqs.values():
                self.assertGreaterEqual(freq, 0.0)
                self.assertLessEqual(freq, 1.0)

    def test_equilibrium_freq(self):
        """With symmetric mutation, equilibrium should approach 0.5."""
        e = GeneticsEngine(points=[(0, 0)], seed=42, mutation_rate=0.1)
        cells = e._init_gene_pools()
        cells[0].allele_freqs["color"] = 0.0
        for _ in range(1000):
            e._apply_mutation(cells)
        self.assertAlmostEqual(cells[0].allele_freqs["color"], 0.5, delta=0.01)


# ---------------------------------------------------------------------------
# Engine 6 — Hardy-Weinberg Tester
# ---------------------------------------------------------------------------

class TestHardyWeinberg(unittest.TestCase):
    def test_hw_fields_set(self):
        e = GeneticsEngine(points=_grid_points(), seed=50)
        cells = e._init_gene_pools()
        e._test_hw(cells)
        for c in cells:
            self.assertIsInstance(c.hw_equilibrium, bool)
            self.assertIsInstance(c.hw_chi_squared, float)
            self.assertGreaterEqual(c.expected_het, 0.0)

    def test_most_cells_in_hw(self):
        """With no selection/drift, most cells should be in HW."""
        e = GeneticsEngine(points=_grid_points(), seed=51)
        cells = e._init_gene_pools()
        e._test_hw(cells)
        in_hw = sum(1 for c in cells if c.hw_equilibrium)
        self.assertGreater(in_hw, len(cells) // 2)

    def test_expected_het_range(self):
        e = GeneticsEngine(points=_grid_points(), seed=52)
        cells = e._init_gene_pools()
        e._test_hw(cells)
        for c in cells:
            self.assertGreaterEqual(c.expected_het, 0.0)
            self.assertLessEqual(c.expected_het, 0.5)


# ---------------------------------------------------------------------------
# Engine 7 — Insights
# ---------------------------------------------------------------------------

class TestInsights(unittest.TestCase):
    def test_insights_generated(self):
        e = GeneticsEngine(points=_grid_points(), seed=60)
        r = e.analyze()
        self.assertGreater(len(r.insights), 0)

    def test_fst_computed(self):
        e = GeneticsEngine(points=_grid_points(), seed=61)
        r = e.analyze()
        self.assertGreaterEqual(r.mean_fst, 0.0)
        self.assertLessEqual(r.mean_fst, 1.0)

    def test_heterozygosity_range(self):
        e = GeneticsEngine(points=_grid_points(), seed=62)
        r = e.analyze()
        self.assertGreaterEqual(r.global_heterozygosity, 0.0)

    def test_health_score_range(self):
        e = GeneticsEngine(points=_grid_points(), seed=63)
        r = e.analyze()
        self.assertGreaterEqual(r.health_score, 0.0)
        self.assertLessEqual(r.health_score, 100.0)

    def test_isolation_by_distance(self):
        e = GeneticsEngine(points=_grid_points(), seed=64)
        r = e.analyze()
        self.assertGreaterEqual(r.isolation_by_distance_r, -1.0)
        self.assertLessEqual(r.isolation_by_distance_r, 1.0)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

class TestFullPipeline(unittest.TestCase):
    def test_analyze_returns_result(self):
        e = GeneticsEngine(points=_grid_points(), seed=70)
        r = e.analyze()
        self.assertIsInstance(r, GeneticsResult)
        self.assertEqual(len(r.cells), 16)

    def test_generations_param(self):
        e = GeneticsEngine(points=_grid_points(), seed=71)
        r = e.analyze(generations=10)
        self.assertEqual(r.generations_simulated, 10)

    def test_fixation_tracking(self):
        """Run many generations on tiny pop to force fixation."""
        pts = [(0, 0), (100, 100)]
        e = GeneticsEngine(points=pts, seed=72)
        r = e.analyze(generations=500)
        # With tiny populations and many generations, likely some fixation
        for f in r.fixations:
            self.assertIsInstance(f, FixationEvent)
            self.assertIn(f.fixed_allele, ("A", "B"))

    def test_custom_seed_reproducible(self):
        pts = _grid_points()
        r1 = GeneticsEngine(points=pts, seed=99).analyze()
        r2 = GeneticsEngine(points=pts, seed=99).analyze()
        self.assertAlmostEqual(r1.mean_fst, r2.mean_fst)
        self.assertAlmostEqual(r1.health_score, r2.health_score)

    def test_different_seeds_differ(self):
        pts = _grid_points()
        r1 = GeneticsEngine(points=pts, seed=100).analyze()
        r2 = GeneticsEngine(points=pts, seed=200).analyze()
        # Very unlikely to be identical
        self.assertTrue(
            r1.mean_fst != r2.mean_fst or r1.health_score != r2.health_score
        )


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

class TestFileIO(unittest.TestCase):
    def test_load_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for x, y in _grid_points():
                f.write(f"{x} {y}\n")
            path = f.name
        try:
            e = GeneticsEngine(path=path, seed=80)
            r = e.analyze()
            self.assertEqual(len(r.cells), 16)
        finally:
            os.unlink(path)

    def test_genetics_analyze_func(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            for x, y in _grid_points():
                f.write(f"{x} {y}\n")
            path = f.name
        try:
            r = genetics_analyze(path, seed=81)
            self.assertIsInstance(r, GeneticsResult)
        finally:
            os.unlink(path)

    def test_json_export(self):
        e = GeneticsEngine(points=_grid_points(), seed=82)
        e.analyze()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            e.to_json(path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("health_score", data)
            self.assertIn("cells", data)
        finally:
            os.unlink(path)

    def test_html_export(self):
        e = GeneticsEngine(points=_grid_points(), seed=83)
        e.analyze()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        try:
            e.to_html(path)
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Spatial Genetics Dashboard", content)
            self.assertIn("Health Score", content)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

class TestDemo(unittest.TestCase):
    def test_demo_runs(self):
        r = genetics_demo(seed=42)
        self.assertIsInstance(r, GeneticsResult)
        self.assertGreater(len(r.cells), 0)

    def test_demo_reproducible(self):
        r1 = genetics_demo(seed=42)
        r2 = genetics_demo(seed=42)
        self.assertAlmostEqual(r1.health_score, r2.health_score)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def test_single_cell(self):
        e = GeneticsEngine(points=[(5, 5)], seed=90)
        r = e.analyze()
        self.assertEqual(len(r.cells), 1)
        self.assertGreaterEqual(r.health_score, 0.0)

    def test_two_cells(self):
        e = GeneticsEngine(points=[(0, 0), (10, 10)], seed=91)
        r = e.analyze()
        self.assertEqual(len(r.cells), 2)

    def test_identical_points(self):
        e = GeneticsEngine(points=[(5, 5), (5, 5), (5, 5)], seed=92)
        r = e.analyze()
        self.assertEqual(len(r.cells), 3)

    def test_no_points_raises(self):
        with self.assertRaises(ValueError):
            GeneticsEngine(points=[])

    def test_large_grid(self):
        pts = [(i * 5.0, j * 5.0) for i in range(10) for j in range(10)]
        e = GeneticsEngine(points=pts, seed=93)
        r = e.analyze(generations=10)
        self.assertEqual(len(r.cells), 100)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def test_euclidean(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_knn_adjacency_symmetric(self):
        pts = [(0, 0), (1, 0), (2, 0)]
        adj = _knn_adjacency(pts, k=2)
        for i in adj:
            for j in adj[i]:
                self.assertIn(i, adj[j])

    def test_pearson_perfect(self):
        r = _pearson([1, 2, 3], [1, 2, 3])
        self.assertAlmostEqual(r, 1.0)

    def test_pearson_inverse(self):
        r = _pearson([1, 2, 3], [3, 2, 1])
        self.assertAlmostEqual(r, -1.0)

    def test_pearson_short(self):
        r = _pearson([1], [1])
        self.assertEqual(r, 0.0)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):
    def test_argparse_demo(self):
        """Just ensure the module can be imported (CLI tested via subprocess)."""
        import vormap_genetics
        self.assertTrue(hasattr(vormap_genetics, "_main"))


if __name__ == "__main__":
    unittest.main()
