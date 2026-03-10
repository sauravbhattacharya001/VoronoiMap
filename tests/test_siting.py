"""Tests for vormap_siting — optimal facility siting."""

import json
import math
import unittest

from vormap_siting import (
    DemandPoint,
    SitingResult,
    SitingSuggestion,
    find_demand_sites,
    find_gap_sites,
    find_maxmin_sites,
)


class TestDemandPoint(unittest.TestCase):
    def test_defaults(self):
        d = DemandPoint(1, 2)
        self.assertEqual(d.x, 1)
        self.assertEqual(d.y, 2)
        self.assertEqual(d.weight, 1.0)

    def test_custom_weight(self):
        d = DemandPoint(3, 4, 10.0)
        self.assertEqual(d.weight, 10.0)


class TestSitingSuggestion(unittest.TestCase):
    def test_fields(self):
        s = SitingSuggestion(x=10, y=20, score=5.0, rank=1, reason="test")
        self.assertEqual(s.rank, 1)
        self.assertEqual(s.score, 5.0)


class TestSitingResult(unittest.TestCase):
    def test_summary(self):
        r = SitingResult(
            strategy="gap",
            sites=[SitingSuggestion(x=10, y=20, score=100, rank=1, reason="big gap")],
            existing_count=3,
            bounds=(0, 100, 0, 100),
        )
        s = r.summary()
        self.assertIn("gap", s)
        self.assertIn("Existing facilities: 3", s)
        self.assertIn("big gap", s)

    def test_to_dict(self):
        r = SitingResult(strategy="maxmin", sites=[], existing_count=5, bounds=None)
        d = r.to_dict()
        self.assertEqual(d["strategy"], "maxmin")
        self.assertEqual(d["existing_count"], 5)

    def test_to_json(self):
        r = SitingResult(strategy="gap", sites=[], existing_count=1, bounds=None)
        j = r.to_json()
        data = json.loads(j)
        self.assertEqual(data["strategy"], "gap")


class TestFindGapSites(unittest.TestCase):
    def test_basic(self):
        existing = [(100, 100), (900, 100), (500, 900)]
        result = find_gap_sites(existing, n_sites=2, bounds=(0, 1000, 0, 1000))
        self.assertEqual(result.strategy, "gap")
        self.assertEqual(len(result.sites), 2)
        self.assertEqual(result.existing_count, 3)
        # Sites should have positive scores (areas)
        for s in result.sites:
            self.assertGreater(s.score, 0)

    def test_single_point(self):
        result = find_gap_sites([(500, 500)], n_sites=1, bounds=(0, 1000, 0, 1000))
        self.assertEqual(len(result.sites), 1)
        # Only one cell, so site is near the existing point
        self.assertIsNotNone(result.sites[0].x)

    def test_gap_sites_avoid_existing(self):
        # Two points in top-left corner — gap should be far from them
        existing = [(50, 50), (100, 100)]
        result = find_gap_sites(existing, n_sites=1, bounds=(0, 1000, 0, 1000))
        site = result.sites[0]
        # Gap site should NOT be right next to existing points
        dist_to_nearest = min(
            math.sqrt((site.x - ex[0])**2 + (site.y - ex[1])**2)
            for ex in existing
        )
        self.assertGreater(dist_to_nearest, 50)

    def test_n_sites_exceeds_points(self):
        existing = [(100, 100), (900, 900)]
        result = find_gap_sites(existing, n_sites=5)
        # Should return at most len(existing) sites
        self.assertLessEqual(len(result.sites), len(existing))

    def test_stats(self):
        result = find_gap_sites(
            [(100, 100), (500, 500), (900, 900)],
            n_sites=1, bounds=(0, 1000, 0, 1000),
        )
        self.assertIn("total_area", result.stats)
        self.assertIn("mean_cell_area", result.stats)
        self.assertIn("area_ratio", result.stats)
        self.assertGreater(result.stats["total_area"], 0)

    def test_auto_bounds(self):
        existing = [(100, 200), (300, 400)]
        result = find_gap_sites(existing, n_sites=1)
        self.assertIsNotNone(result.bounds)

    def test_raises_no_points(self):
        with self.assertRaises(ValueError):
            find_gap_sites([], n_sites=1)

    def test_raises_n_sites_zero(self):
        with self.assertRaises(ValueError):
            find_gap_sites([(1, 1)], n_sites=0)


class TestFindDemandSites(unittest.TestCase):
    def test_basic(self):
        existing = [(100, 100)]
        demand = [
            DemandPoint(800, 800, 50),
            DemandPoint(810, 810, 30),
            DemandPoint(150, 150, 10),  # already served
        ]
        result = find_demand_sites(existing, demand, n_sites=1, radius=200)
        self.assertEqual(result.strategy, "demand")
        self.assertGreater(len(result.sites), 0)
        # The site should be near (800, 800) cluster
        site = result.sites[0]
        self.assertGreater(site.x, 500)
        self.assertGreater(site.y, 500)

    def test_all_served(self):
        existing = [(100, 100), (800, 800)]
        demand = [
            DemandPoint(110, 110, 10),
            DemandPoint(790, 790, 20),
        ]
        result = find_demand_sites(existing, demand, n_sites=1, radius=200)
        self.assertEqual(len(result.sites), 0)
        self.assertIn("message", result.stats)

    def test_multiple_sites(self):
        existing = [(500, 500)]
        demand = [
            DemandPoint(100, 100, 40),
            DemandPoint(900, 900, 60),
        ]
        result = find_demand_sites(existing, demand, n_sites=2, radius=150)
        self.assertEqual(len(result.sites), 2)

    def test_coverage_stats(self):
        existing = [(0, 0)]
        demand = [DemandPoint(500, 500, 100)]
        result = find_demand_sites(existing, demand, n_sites=1, radius=100)
        self.assertIn("final_coverage_pct", result.stats)

    def test_raises_no_existing(self):
        with self.assertRaises(ValueError):
            find_demand_sites([], [DemandPoint(1, 1)], n_sites=1)

    def test_raises_no_demand(self):
        with self.assertRaises(ValueError):
            find_demand_sites([(1, 1)], [], n_sites=1)

    def test_raises_bad_radius(self):
        with self.assertRaises(ValueError):
            find_demand_sites([(1, 1)], [DemandPoint(5, 5)], radius=0)


class TestFindMaxminSites(unittest.TestCase):
    def test_basic(self):
        existing = [(100, 100), (900, 100)]
        result = find_maxmin_sites(
            existing, n_sites=1, bounds=(0, 1000, 0, 1000), seed=42
        )
        self.assertEqual(len(result.sites), 1)
        site = result.sites[0]
        # Site should be far from both existing points
        d1 = math.sqrt((site.x - 100)**2 + (site.y - 100)**2)
        d2 = math.sqrt((site.x - 900)**2 + (site.y - 100)**2)
        min_d = min(d1, d2)
        self.assertGreater(min_d, 100)

    def test_reproducible_with_seed(self):
        existing = [(200, 200), (800, 800)]
        r1 = find_maxmin_sites(existing, n_sites=2, bounds=(0, 1000, 0, 1000), seed=123)
        r2 = find_maxmin_sites(existing, n_sites=2, bounds=(0, 1000, 0, 1000), seed=123)
        self.assertEqual(r1.sites[0].x, r2.sites[0].x)
        self.assertEqual(r1.sites[0].y, r2.sites[0].y)

    def test_different_seeds_differ(self):
        existing = [(200, 200), (800, 800)]
        r1 = find_maxmin_sites(existing, n_sites=1, bounds=(0, 1000, 0, 1000), seed=1)
        r2 = find_maxmin_sites(existing, n_sites=1, bounds=(0, 1000, 0, 1000), seed=999)
        # Very unlikely to pick exact same spot
        self.assertTrue(
            r1.sites[0].x != r2.sites[0].x or r1.sites[0].y != r2.sites[0].y
        )

    def test_multiple_sites_spread(self):
        existing = [(500, 500)]
        result = find_maxmin_sites(
            existing, n_sites=3, bounds=(0, 1000, 0, 1000), seed=42
        )
        self.assertEqual(len(result.sites), 3)
        # Each successive site should account for previous placements
        for s in result.sites:
            self.assertGreater(s.score, 0)

    def test_auto_bounds(self):
        result = find_maxmin_sites(
            [(100, 200), (300, 400)], n_sites=1, seed=42
        )
        self.assertIsNotNone(result.bounds)

    def test_stats(self):
        result = find_maxmin_sites(
            [(100, 100)], n_sites=1, bounds=(0, 1000, 0, 1000),
            n_candidates=100, seed=42,
        )
        self.assertIn("candidates_evaluated", result.stats)
        self.assertEqual(result.stats["candidates_evaluated"], 100)

    def test_raises_no_points(self):
        with self.assertRaises(ValueError):
            find_maxmin_sites([], n_sites=1)


class TestCLI(unittest.TestCase):
    def test_gap_cli(self):
        from io import StringIO
        import sys
        old_stdout = sys.stdout
        sys.stdout = buf = StringIO()
        try:
            from vormap_siting import main
            main(["gap", "--points", "100,100,900,900", "--n-sites", "1",
                  "--bounds", "0,1000,0,1000"])
        finally:
            sys.stdout = old_stdout
        self.assertIn("gap", buf.getvalue())

    def test_maxmin_cli_json(self):
        from io import StringIO
        import sys
        old_stdout = sys.stdout
        sys.stdout = buf = StringIO()
        try:
            from vormap_siting import main
            main(["maxmin", "--points", "100,100,900,900", "--n-sites", "2",
                  "--seed", "42", "--json"])
        finally:
            sys.stdout = old_stdout
        data = json.loads(buf.getvalue())
        self.assertEqual(data["strategy"], "maxmin")
        self.assertEqual(len(data["sites"]), 2)

    def test_demand_cli(self):
        from io import StringIO
        import sys
        old_stdout = sys.stdout
        sys.stdout = buf = StringIO()
        try:
            from vormap_siting import main
            main(["demand", "--points", "100,100",
                  "--demand", "800,800,50,810,810,30",
                  "--radius", "200", "--n-sites", "1"])
        finally:
            sys.stdout = old_stdout
        self.assertIn("demand", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
