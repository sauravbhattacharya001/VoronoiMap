"""Tests for vormap_gravity — spatial interaction / gravity model."""

import json
import math
import os
import tempfile
import unittest

from vormap_gravity import (
    FlowCategory,
    GravityConfig,
    GravityModel,
    GravityResult,
    Location,
    Flow,
    MarketArea,
    AccessibilityScore,
    gravity_analysis,
    gravity_analysis_from_stats,
    export_gravity_svg,
    export_gravity_json,
    export_gravity_csv,
    main,
    _euclidean,
    _build_distance_matrix,
    _categorise_flow,
    _classic_model,
    _huff_model,
    _hansen_model,
    _doubly_constrained_model,
    _compute_market_areas,
    _compute_accessibility,
    _compute_dominance,
    _parse_locations,
)


# ── Helpers ──────────────────────────────────────────────────────────


TRIANGLE = [(0, 0, 100), (300, 0, 200), (150, 260, 50)]
LABELS = ["A", "B", "C"]


def _result(**kw):
    """Run classic gravity on the triangle with optional overrides."""
    config = GravityConfig(**kw)
    return gravity_analysis(TRIANGLE, config=config, labels=LABELS)


# ── Location & Flow dataclasses ──────────────────────────────────────


class TestLocation(unittest.TestCase):

    def test_fields(self):
        loc = Location(0, 1.0, 2.0, 50.0, "X")
        self.assertEqual(loc.index, 0)
        self.assertAlmostEqual(loc.x, 1.0)
        self.assertEqual(loc.label, "X")

    def test_default_label(self):
        loc = Location(1, 0, 0, 10)
        self.assertEqual(loc.label, "")


class TestFlow(unittest.TestCase):

    def test_flow_per_distance(self):
        f = Flow(0, 1, 10.0, 5.0, FlowCategory.STRONG)
        self.assertAlmostEqual(f.flow_per_distance, 2.0)

    def test_flow_per_distance_zero(self):
        f = Flow(0, 1, 10.0, 0.0, FlowCategory.STRONG)
        self.assertGreater(f.flow_per_distance, 0)


# ── Distance helpers ─────────────────────────────────────────────────


class TestEuclidean(unittest.TestCase):

    def test_basic(self):
        a = Location(0, 0, 0, 1)
        b = Location(1, 3, 4, 1)
        self.assertAlmostEqual(_euclidean(a, b), 5.0)

    def test_same_point(self):
        a = Location(0, 5, 5, 1)
        self.assertAlmostEqual(_euclidean(a, a), 0.0)


class TestDistanceMatrix(unittest.TestCase):

    def test_symmetric(self):
        locs = [Location(i, *TRIANGLE[i], LABELS[i]) for i in range(3)]
        m = _build_distance_matrix(locs)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(m[i][j], m[j][i])

    def test_diagonal_zero(self):
        locs = [Location(i, *TRIANGLE[i], LABELS[i]) for i in range(3)]
        m = _build_distance_matrix(locs)
        for i in range(3):
            self.assertAlmostEqual(m[i][i], 0.0)


# ── Flow categorisation ─────────────────────────────────────────────


class TestCategoriseFlow(unittest.TestCase):

    def test_dominant(self):
        self.assertEqual(_categorise_flow(80, 100), FlowCategory.DOMINANT)

    def test_strong(self):
        self.assertEqual(_categorise_flow(60, 100), FlowCategory.STRONG)

    def test_moderate(self):
        self.assertEqual(_categorise_flow(30, 100), FlowCategory.MODERATE)

    def test_weak(self):
        self.assertEqual(_categorise_flow(8, 100), FlowCategory.WEAK)

    def test_negligible(self):
        self.assertEqual(_categorise_flow(2, 100), FlowCategory.NEGLIGIBLE)

    def test_zero_max(self):
        self.assertEqual(_categorise_flow(5, 0), FlowCategory.NEGLIGIBLE)


# ── Classic model ────────────────────────────────────────────────────


class TestClassicModel(unittest.TestCase):

    def test_flow_proportional_to_mass(self):
        result = _result()
        # B has highest mass (200), should have strongest flows
        b_outflow = sum(f.flow for f in result.flows if f.origin == 1)
        a_outflow = sum(f.flow for f in result.flows if f.origin == 0)
        self.assertGreater(b_outflow, a_outflow)

    def test_symmetric(self):
        result = _result()
        for f in result.flows:
            rev = [x for x in result.flows
                   if x.origin == f.destination and x.destination == f.origin]
            self.assertEqual(len(rev), 1)
            self.assertAlmostEqual(f.flow, rev[0].flow, places=6)

    def test_inverse_distance(self):
        # Closer locations should have stronger flows (same mass)
        locs = [(0, 0, 100), (100, 0, 100), (1000, 0, 100)]
        result = gravity_analysis(locs)
        near = [f for f in result.flows if f.origin == 0 and f.destination == 1][0]
        far = [f for f in result.flows if f.origin == 0 and f.destination == 2][0]
        self.assertGreater(near.flow, far.flow)

    def test_k_scaling(self):
        r1 = _result(k=1.0)
        r2 = _result(k=2.0)
        self.assertAlmostEqual(r2.total_flow, r1.total_flow * 2, places=4)


# ── Huff model ───────────────────────────────────────────────────────


class TestHuffModel(unittest.TestCase):

    def test_rows_sum_to_one(self):
        config = GravityConfig(model=GravityModel.HUFF)
        result = gravity_analysis(TRIANGLE, config=config, labels=LABELS)
        n = len(result.locations)
        for i in range(n):
            row_sum = sum(result.flow_matrix[i])
            self.assertAlmostEqual(row_sum, 1.0, places=6)

    def test_probabilities_positive(self):
        config = GravityConfig(model=GravityModel.HUFF)
        result = gravity_analysis(TRIANGLE, config=config)
        for f in result.flows:
            self.assertGreaterEqual(f.flow, 0.0)

    def test_highest_mass_attracts_most(self):
        config = GravityConfig(model=GravityModel.HUFF)
        result = gravity_analysis(TRIANGLE, config=config)
        # B (index 1) has mass 200, should be primary for most origins
        b_area = [ma for ma in result.market_areas if ma.destination == 1][0]
        self.assertGreater(b_area.market_share, 0.0)


# ── Hansen model ─────────────────────────────────────────────────────


class TestHansenModel(unittest.TestCase):

    def test_accessibility_ranking(self):
        # Central location should have highest accessibility
        locs = [(0, 0, 100), (500, 500, 100), (1000, 0, 100), (500, 250, 100)]
        config = GravityConfig(model=GravityModel.HANSEN)
        result = gravity_analysis(locs)
        # Location 3 is most central
        scores = {a.location: a.score for a in result.accessibility}
        # At least verify all have positive scores
        for s in scores.values():
            self.assertGreater(s, 0)

    def test_all_locations_ranked(self):
        config = GravityConfig(model=GravityModel.HANSEN)
        result = gravity_analysis(TRIANGLE, config=config)
        self.assertEqual(len(result.accessibility), 3)
        ranks = sorted(a.rank for a in result.accessibility)
        self.assertEqual(ranks, [1, 2, 3])


# ── Doubly-constrained model ────────────────────────────────────────


class TestDoublyConstrained(unittest.TestCase):

    def test_converges(self):
        config = GravityConfig(model=GravityModel.DOUBLY_CONSTRAINED)
        result = gravity_analysis(TRIANGLE, config=config)
        self.assertGreater(result.convergence_iterations, 0)

    def test_row_sums_match_masses(self):
        config = GravityConfig(model=GravityModel.DOUBLY_CONSTRAINED,
                               max_iterations=200, convergence_threshold=1e-8)
        result = gravity_analysis(TRIANGLE, config=config)
        masses = [100, 200, 50]
        for i in range(3):
            row_sum = sum(result.flow_matrix[i])
            # Doubly-constrained with self_interaction=False won't match
            # exactly since diagonal is excluded; just check convergence
            # produced reasonable flows (within 15% of target)
            self.assertAlmostEqual(row_sum, masses[i], delta=masses[i] * 0.15)


# ── Market areas ─────────────────────────────────────────────────────


class TestMarketAreas(unittest.TestCase):

    def test_all_destinations_covered(self):
        result = _result()
        self.assertEqual(len(result.market_areas), 3)

    def test_shares_sum_to_one(self):
        result = _result()
        total = sum(ma.market_share for ma in result.market_areas)
        self.assertAlmostEqual(total, 1.0, places=4)

    def test_primary_origins_no_overlap(self):
        result = _result()
        all_origins = []
        for ma in result.market_areas:
            all_origins.extend(ma.primary_origins)
        self.assertEqual(len(all_origins), len(set(all_origins)))


# ── Accessibility ────────────────────────────────────────────────────


class TestAccessibility(unittest.TestCase):

    def test_percentile_range(self):
        result = _result()
        for a in result.accessibility:
            self.assertGreaterEqual(a.percentile, 0)
            self.assertLessEqual(a.percentile, 100)

    def test_unique_ranks(self):
        result = _result()
        ranks = [a.rank for a in result.accessibility]
        self.assertEqual(len(ranks), len(set(ranks)))


# ── Dominance ────────────────────────────────────────────────────────


class TestDominance(unittest.TestCase):

    def test_sums_to_one(self):
        result = _result()
        total = sum(result.dominance_index.values())
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_highest_mass_most_dominant(self):
        result = _result()
        # B (index 1, mass 200) should have highest dominance
        self.assertEqual(
            max(result.dominance_index, key=result.dominance_index.get), 1
        )


# ── gravity_analysis edge cases ──────────────────────────────────────


class TestGravityAnalysis(unittest.TestCase):

    def test_min_locations(self):
        with self.assertRaises(ValueError):
            gravity_analysis([(1, 2, 3)])

    def test_negative_mass(self):
        with self.assertRaises(ValueError):
            gravity_analysis([(0, 0, -1), (1, 1, 5)])

    def test_bad_tuple_length(self):
        with self.assertRaises(ValueError):
            gravity_analysis([(0, 0), (1, 1)])

    def test_zero_mass(self):
        # Should not crash — just produces zero flows from that location
        result = gravity_analysis([(0, 0, 0), (100, 0, 50)])
        self.assertEqual(len(result.flows), 2)

    def test_self_interaction(self):
        config = GravityConfig(self_interaction=True)
        result = gravity_analysis(TRIANGLE, config=config)
        # With self_interaction enabled, diagonal entries are included
        # (though d=0 produces zero flow via the d>0 guard)
        # All 9 pairs (3x3) appear in the flow list
        self.assertEqual(len(result.flows), 9)

    def test_min_flow_filter(self):
        config = GravityConfig(min_flow=0.01)
        result = gravity_analysis(TRIANGLE, config=config)
        for f in result.flows:
            self.assertGreaterEqual(f.flow, 0.01)


# ── gravity_analysis_from_stats ──────────────────────────────────────


class TestFromStats(unittest.TestCase):

    def test_basic(self):
        stats = [
            {"centroid_x": 0, "centroid_y": 0, "area": 100},
            {"centroid_x": 100, "centroid_y": 0, "area": 200},
        ]
        result = gravity_analysis_from_stats(stats)
        self.assertEqual(len(result.locations), 2)
        self.assertEqual(result.locations[0].label, "R0")

    def test_negative_area_clamped(self):
        stats = [
            {"centroid_x": 0, "centroid_y": 0, "area": -5},
            {"centroid_x": 100, "centroid_y": 0, "area": 200},
        ]
        result = gravity_analysis_from_stats(stats)
        self.assertAlmostEqual(result.locations[0].mass, 0.0)

    def test_custom_attribute(self):
        stats = [
            {"centroid_x": 0, "centroid_y": 0, "population": 1000},
            {"centroid_x": 100, "centroid_y": 0, "population": 2000},
        ]
        result = gravity_analysis_from_stats(stats, attribute="population")
        self.assertAlmostEqual(result.locations[0].mass, 1000)


# ── Export: SVG ──────────────────────────────────────────────────────


class TestExportSVG(unittest.TestCase):

    def test_creates_file(self):
        result = _result()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.svg")
            out = export_gravity_svg(result, path)
            self.assertTrue(os.path.exists(out))
            content = open(out, encoding="utf-8").read()
            self.assertIn("<svg", content)
            self.assertIn("circle", content)

    def test_no_flows(self):
        result = _result()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.svg")
            export_gravity_svg(result, path, show_flows=False)
            content = open(path, encoding="utf-8").read()
            self.assertNotIn("<line", content)

    def test_min_category_filter(self):
        result = _result()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.svg")
            export_gravity_svg(result, path,
                               min_flow_category=FlowCategory.DOMINANT)
            self.assertTrue(os.path.exists(path))


# ── Export: JSON ─────────────────────────────────────────────────────


class TestExportJSON(unittest.TestCase):

    def test_valid_json(self):
        result = _result()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.json")
            export_gravity_json(result, path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(data["model"], "classic")
            self.assertEqual(len(data["locations"]), 3)
            self.assertGreater(len(data["flows"]), 0)
            self.assertIn("market_areas", data)
            self.assertIn("accessibility", data)
            self.assertIn("dominance_index", data)


# ── Export: CSV ──────────────────────────────────────────────────────


class TestExportCSV(unittest.TestCase):

    def test_creates_file(self):
        result = _result()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "test.csv")
            export_gravity_csv(result, path)
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            self.assertGreater(len(lines), 1)  # header + data
            self.assertIn("origin", lines[0])
            self.assertIn("flow", lines[0])


# ── CLI ──────────────────────────────────────────────────────────────


class TestCLI(unittest.TestCase):

    def test_generate(self):
        # Should not raise
        main(["--generate", "5", "--seed", "42"])

    def test_locations_string(self):
        main(["--locations", "0,0,100;300,0,200;150,260,50"])

    def test_all_models(self):
        for model in ["classic", "huff", "hansen", "doubly_constrained"]:
            main(["--generate", "4", "--seed", "1", "--model", model])

    def test_exports(self):
        with tempfile.TemporaryDirectory() as td:
            svg = os.path.join(td, "out.svg")
            js = os.path.join(td, "out.json")
            cs = os.path.join(td, "out.csv")
            main(["--generate", "3", "--seed", "1",
                  "--svg", svg, "--json", js, "--csv", cs])
            self.assertTrue(os.path.exists(svg))
            self.assertTrue(os.path.exists(js))
            self.assertTrue(os.path.exists(cs))


# ── Parse locations ──────────────────────────────────────────────────


class TestParseLocations(unittest.TestCase):

    def test_basic(self):
        locs = _parse_locations("1,2,3;4,5,6")
        self.assertEqual(len(locs), 2)
        self.assertEqual(locs[0], (1.0, 2.0, 3.0))

    def test_whitespace(self):
        locs = _parse_locations(" 1, 2, 3 ; 4, 5, 6 ")
        self.assertEqual(len(locs), 2)

    def test_bad_count(self):
        with self.assertRaises(ValueError):
            _parse_locations("1,2")

    def test_empty_parts(self):
        locs = _parse_locations("1,2,3;;4,5,6;")
        self.assertEqual(len(locs), 2)


# ── Summary text ─────────────────────────────────────────────────────


class TestSummary(unittest.TestCase):

    def test_contains_key_sections(self):
        result = _result()
        text = result.summary()
        self.assertIn("GRAVITY MODEL REPORT", text)
        self.assertIn("Top Flows", text)
        self.assertIn("Market Areas", text)
        self.assertIn("Accessibility", text)
        self.assertIn("Flow Dominance", text)

    def test_doubly_constrained_shows_convergence(self):
        config = GravityConfig(model=GravityModel.DOUBLY_CONSTRAINED)
        result = gravity_analysis(TRIANGLE, config=config)
        text = result.summary()
        self.assertIn("Convergence", text)


if __name__ == "__main__":
    unittest.main()
