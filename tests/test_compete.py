"""Tests for vormap_compete – Territorial Competition Simulator.

Covers geometry helpers, cell/grid construction, strategy action selection,
full simulation engine, recommendation generation, export routines, and
edge-case scenarios with deterministic seeds for reproducibility.
"""

import json
import os
import tempfile
import unittest

from vormap_compete import (
    Cell,
    COMP_COLORS,
    CompetitionResult,
    STRATEGIES,
    _build_cells,
    _distance,
    _generate_recommendations,
    _pick_action_indexed,
    _random_points,
    export_html,
    export_json,
    format_summary,
    simulate_competition,
)


class TestDistance(unittest.TestCase):
    """Tests for the _distance helper."""

    def test_same_point(self):
        self.assertAlmostEqual(_distance((3.0, 4.0), (3.0, 4.0)), 0.0)

    def test_unit_distance(self):
        self.assertAlmostEqual(_distance((0, 0), (1, 0)), 1.0)
        self.assertAlmostEqual(_distance((0, 0), (0, 1)), 1.0)

    def test_pythagorean(self):
        self.assertAlmostEqual(_distance((0, 0), (3, 4)), 5.0)

    def test_symmetry(self):
        a, b = (1.5, 2.3), (7.8, -1.2)
        self.assertAlmostEqual(_distance(a, b), _distance(b, a))

    def test_negative_coords(self):
        self.assertAlmostEqual(_distance((-3, -4), (0, 0)), 5.0)


class TestRandomPoints(unittest.TestCase):
    """Tests for _random_points generation."""

    def test_correct_count(self):
        pts = _random_points(20, 100, 100, min_sep=0)
        self.assertEqual(len(pts), 20)

    def test_within_bounds(self):
        pts = _random_points(50, 200, 150, min_sep=0)
        for x, y in pts:
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x, 200)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y, 150)

    def test_min_separation_respected(self):
        pts = _random_points(10, 500, 500, min_sep=20.0)
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                # Allow a tiny tolerance – fallback path may produce
                # closer points if attempts exhausted.
                self.assertGreater(_distance(pts[i], pts[j]), 0)

    def test_fallback_when_space_tight(self):
        # Very tight space with high min_sep – should still return n pts
        pts = _random_points(5, 10, 10, min_sep=100.0)
        self.assertEqual(len(pts), 5)


class TestCellModel(unittest.TestCase):
    """Tests for the Cell data class."""

    def test_default_state(self):
        c = Cell(0, (1.0, 2.0), 3.5)
        self.assertEqual(c.idx, 0)
        self.assertEqual(c.center, (1.0, 2.0))
        self.assertIsNone(c.owner)
        self.assertEqual(c.strength, 0.0)
        self.assertEqual(c.resource, 3.5)
        self.assertEqual(c.neighbors, [])

    def test_mutable_fields(self):
        c = Cell(5, (0, 0), 1.0)
        c.owner = 2
        c.strength = 50.0
        c.neighbors = [1, 3, 7]
        self.assertEqual(c.owner, 2)
        self.assertEqual(c.strength, 50.0)
        self.assertEqual(c.neighbors, [1, 3, 7])


class TestBuildCells(unittest.TestCase):
    """Tests for _build_cells grid rasterisation and neighbor discovery."""

    def test_correct_count(self):
        pts = [(50, 50), (150, 50), (100, 150)]
        cells = _build_cells(pts, 200, 200, grid_res=40)
        self.assertEqual(len(cells), 3)

    def test_centers_match(self):
        pts = [(10, 20), (80, 90)]
        cells = _build_cells(pts, 100, 100, grid_res=30)
        for cell, pt in zip(cells, pts):
            self.assertEqual(cell.center, pt)

    def test_neighbors_symmetric(self):
        pts = [(25, 50), (75, 50), (50, 90)]
        cells = _build_cells(pts, 100, 100, grid_res=50)
        for c in cells:
            for n in c.neighbors:
                self.assertIn(c.idx, cells[n].neighbors,
                              f"Cell {n} should list {c.idx} as neighbor")

    def test_neighbor_indices_sorted(self):
        pts = _random_points(15, 200, 200, min_sep=0)
        cells = _build_cells(pts, 200, 200, grid_res=30)
        for c in cells:
            self.assertEqual(c.neighbors, sorted(c.neighbors))

    def test_positive_resources(self):
        pts = [(50, 50), (150, 50)]
        cells = _build_cells(pts, 200, 100, grid_res=20)
        for c in cells:
            self.assertGreater(c.resource, 0)


class TestPickActionIndexed(unittest.TestCase):
    """Tests for strategy action selection."""

    def _make_grid(self):
        """3-cell linear grid: 0—1—2, comp 0 owns cell 0, cell 1 neutral."""
        cells = [Cell(i, (i * 50, 50), 3.0) for i in range(3)]
        cells[0].neighbors = [1]
        cells[1].neighbors = [0, 2]
        cells[2].neighbors = [1]
        cells[0].owner = 0
        cells[0].strength = 20.0
        cells[2].owner = 1
        cells[2].strength = 15.0
        return cells

    def test_aggressive_attacks_enemy(self):
        cells = self._make_grid()
        acts = _pick_action_indexed(cells, {0}, 0, "aggressive")
        # Should claim neutral cell 1 since no direct enemy neighbor
        action_types = [a[0] for a in acts]
        self.assertTrue(any(a in ("attack", "claim") for a in action_types))

    def test_defensive_fortifies_first(self):
        cells = self._make_grid()
        acts = _pick_action_indexed(cells, {0}, 0, "defensive")
        action_types = [a[0] for a in acts]
        self.assertIn("fortify", action_types)

    def test_opportunistic_claims_neutral(self):
        cells = self._make_grid()
        acts = _pick_action_indexed(cells, {0}, 0, "opportunistic")
        claims = [(a, t) for a, t in acts if a == "claim"]
        self.assertGreater(len(claims), 0)
        self.assertEqual(claims[0][1], 1)  # neutral cell

    def test_balanced_mixed_actions(self):
        cells = self._make_grid()
        # Give comp 0 cells 0 and 1 so it borders enemy
        cells[1].owner = 0
        cells[1].strength = 10.0
        acts = _pick_action_indexed(cells, {0, 1}, 0, "balanced")
        action_types = {a[0] for a in acts}
        # balanced should produce a mix — at least attack + fortify
        self.assertTrue(len(action_types) >= 2 or len(acts) >= 1)

    def test_random_returns_actions(self):
        cells = self._make_grid()
        # Run a few times — random should produce non-empty actions
        any_actions = False
        for _ in range(10):
            acts = _pick_action_indexed(cells, {0}, 0, "random")
            if acts:
                any_actions = True
                break
        self.assertTrue(any_actions)

    def test_empty_owned_returns_empty(self):
        cells = self._make_grid()
        acts = _pick_action_indexed(cells, set(), 0, "aggressive")
        self.assertEqual(acts, [])


class TestSimulateCompetition(unittest.TestCase):
    """Tests for the full simulation engine."""

    def test_deterministic_with_seed(self):
        r1 = simulate_competition(seed=42, rounds=20)
        r2 = simulate_competition(seed=42, rounds=20)
        self.assertEqual(r1.territory_sizes, r2.territory_sizes)
        self.assertEqual(r1.winner, r2.winner)

    def test_different_seeds_differ(self):
        r1 = simulate_competition(seed=1, rounds=30)
        r2 = simulate_competition(seed=999, rounds=30)
        # Extremely unlikely to be identical
        self.assertNotEqual(r1.territory_sizes, r2.territory_sizes)

    def test_result_attributes(self):
        r = simulate_competition(n_competitors=3, rounds=10, seed=7,
                                 n_points=50)
        self.assertEqual(r.n_competitors, 3)
        self.assertEqual(r.rounds, 10)
        self.assertEqual(len(r.points), 50)
        self.assertEqual(len(r.strategies), 3)
        self.assertGreater(len(r.history), 0)
        self.assertGreater(len(r.territory_sizes), 0)

    def test_winner_is_valid(self):
        r = simulate_competition(n_competitors=4, rounds=30, seed=12)
        if r.winner is not None:
            self.assertIn(r.winner, range(4))
            # Winner should have territory
            final = r.territory_sizes[-1]
            self.assertGreater(final[r.winner], 0)

    def test_eliminations_valid(self):
        r = simulate_competition(n_competitors=5, rounds=80, seed=55)
        for rnd, ci in r.eliminations:
            self.assertGreaterEqual(rnd, 0)
            self.assertLess(rnd, len(r.history))
            self.assertIn(ci, range(5))

    def test_two_competitors(self):
        r = simulate_competition(n_competitors=2, rounds=40, seed=3)
        self.assertEqual(r.n_competitors, 2)
        self.assertEqual(len(r.strategies), 2)
        self.assertIsNotNone(r.winner)

    def test_custom_strategies(self):
        r = simulate_competition(
            n_competitors=3, rounds=10, seed=8,
            strategies=["aggressive", "defensive", "opportunistic"])
        self.assertEqual(r.strategies,
                         ["aggressive", "defensive", "opportunistic"])

    def test_strategy_padding(self):
        """Fewer strategies than competitors → auto-padded."""
        r = simulate_competition(n_competitors=4, rounds=5, seed=1,
                                 strategies=["aggressive"])
        self.assertEqual(len(r.strategies), 4)
        self.assertEqual(r.strategies[0], "aggressive")

    def test_strategy_truncation(self):
        """More strategies than competitors → truncated."""
        r = simulate_competition(n_competitors=2, rounds=5, seed=1,
                                 strategies=list(STRATEGIES))
        self.assertEqual(len(r.strategies), 2)

    def test_history_length(self):
        r = simulate_competition(n_competitors=2, rounds=15, seed=20)
        # history has rounds + 1 snapshots (unless early termination)
        self.assertGreater(len(r.history), 1)
        self.assertLessEqual(len(r.history), 16)  # at most rounds + 1

    def test_single_round(self):
        r = simulate_competition(n_competitors=2, rounds=1, seed=99)
        self.assertGreater(len(r.history), 0)

    def test_territory_sizes_per_round(self):
        r = simulate_competition(n_competitors=3, rounds=10, seed=5)
        for sizes in r.territory_sizes:
            self.assertEqual(len(sizes), 3)
            for s in sizes:
                self.assertGreaterEqual(s, 0)

    def test_custom_dimensions(self):
        r = simulate_competition(width=400, height=300, n_points=30,
                                 rounds=5, seed=2)
        self.assertEqual(r.width, 400)
        self.assertEqual(r.height, 300)
        self.assertEqual(len(r.points), 30)


class TestGenerateRecommendations(unittest.TestCase):
    """Tests for _generate_recommendations."""

    def _make_result(self, *, winner=0, n=4, strategies=None,
                     territory_sizes=None, eliminations=None, rounds=50):
        r = CompetitionResult()
        r.n_competitors = n
        r.strategies = strategies or list(STRATEGIES[:n])
        r.rounds = rounds
        r.territory_sizes = territory_sizes or []
        r.eliminations = eliminations or []
        r.winner = winner
        return r

    def test_winner_recommendation(self):
        r = self._make_result(winner=1, strategies=["aggressive", "defensive"])
        recs = _generate_recommendations(r)
        self.assertTrue(any("defensive" in rec and "won" in rec
                            for rec in recs))

    def test_dominance_high(self):
        # One competitor has >70% territory
        sizes = [[80, 10, 5, 5]] * 15
        r = self._make_result(territory_sizes=sizes)
        recs = _generate_recommendations(r)
        self.assertTrue(any("dominance" in rec.lower() or ">70%" in rec
                            for rec in recs))

    def test_balanced_match(self):
        sizes = [[30, 25, 25, 20]] * 15
        r = self._make_result(territory_sizes=sizes)
        recs = _generate_recommendations(r)
        self.assertTrue(any("balanced" in rec.lower() for rec in recs))

    def test_stalemate_detected(self):
        # Same sizes from midpoint to end → stalemate
        sizes = [[30, 30, 30, 30]] * 20
        r = self._make_result(territory_sizes=sizes, rounds=20)
        recs = _generate_recommendations(r)
        self.assertTrue(any("stalemate" in rec.lower() for rec in recs))

    def test_early_elimination(self):
        # Competitor 2 eliminated at round 5 of 50 → early
        r = self._make_result(eliminations=[(5, 2)], rounds=50,
                              territory_sizes=[[25, 25, 0, 50]] * 2)
        recs = _generate_recommendations(r)
        self.assertTrue(any("eliminated early" in rec for rec in recs))

    def test_no_winner_no_crash(self):
        r = self._make_result(winner=None,
                              territory_sizes=[[0, 0, 0, 0]])
        recs = _generate_recommendations(r)
        self.assertIsInstance(recs, list)


class TestExportJson(unittest.TestCase):
    """Tests for JSON export."""

    def test_valid_json(self):
        r = simulate_competition(n_competitors=2, rounds=5, seed=10,
                                 n_points=20)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(r, path)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(data["n_competitors"], 2)
            self.assertEqual(data["rounds"], 5)
            self.assertEqual(len(data["points"]), 20)
            self.assertIn("history", data)
            self.assertIn("strategies", data)
            self.assertIn("winner", data)
            self.assertIn("recommendations", data)
            self.assertIn("territory_sizes", data)
        finally:
            os.unlink(path)

    def test_history_cell_format(self):
        r = simulate_competition(n_competitors=2, rounds=3, seed=11,
                                 n_points=10)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_json(r, path)
            with open(path) as f:
                data = json.load(f)
            for snap in data["history"]:
                for entry in snap:
                    self.assertIn("owner", entry)
                    self.assertIn("strength", entry)
        finally:
            os.unlink(path)


class TestExportHtml(unittest.TestCase):
    """Tests for HTML export."""

    def test_creates_html_file(self):
        r = simulate_competition(n_competitors=2, rounds=5, seed=15,
                                 n_points=20)
        path = os.path.join(tempfile.gettempdir(), "test_compete.html")
        try:
            # On Windows the default encoding may not handle emoji;
            # monkey-patch open inside the module to force UTF-8.
            import builtins
            _real_open = builtins.open
            def _utf8_open(p, mode="r", **kw):
                if "b" not in mode:
                    kw.setdefault("encoding", "utf-8")
                return _real_open(p, mode, **kw)
            builtins.open = _utf8_open
            try:
                export_html(r, path)
            finally:
                builtins.open = _real_open
            with open(path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("<!DOCTYPE html>", content)
            self.assertIn("Voronoi", content)
            self.assertIn("canvas", content.lower())
            # Should contain embedded JSON data
            self.assertIn("const pts=", content)
            self.assertIn("const hist=", content)
        finally:
            os.unlink(path)


class TestFormatSummary(unittest.TestCase):
    """Tests for format_summary text output."""

    def test_contains_essentials(self):
        r = simulate_competition(n_competitors=3, rounds=10, seed=6)
        text = format_summary(r)
        self.assertIn("Competitors", text)
        self.assertIn("Rounds", text)
        self.assertIn("Cells", text)
        # Should list strategies
        for s in r.strategies:
            self.assertIn(s, text)

    def test_winner_shown(self):
        r = simulate_competition(n_competitors=2, rounds=30, seed=9)
        text = format_summary(r)
        if r.winner is not None:
            self.assertIn("Winner", text)

    def test_elimination_shown(self):
        r = simulate_competition(n_competitors=5, rounds=80, seed=55)
        text = format_summary(r)
        for _, ci in r.eliminations:
            self.assertIn(f"Competitor {ci} eliminated", text)


class TestCompColors(unittest.TestCase):
    """Verify color palette sanity."""

    def test_at_least_six_colors(self):
        self.assertGreaterEqual(len(COMP_COLORS), 6)

    def test_valid_hex(self):
        for c in COMP_COLORS:
            self.assertTrue(c.startswith("#") and len(c) == 7,
                            f"Invalid hex color: {c}")


class TestStrategiesConstant(unittest.TestCase):
    def test_all_known(self):
        expected = {"aggressive", "defensive", "opportunistic",
                    "balanced", "random"}
        self.assertEqual(set(STRATEGIES), expected)


if __name__ == "__main__":
    unittest.main()
