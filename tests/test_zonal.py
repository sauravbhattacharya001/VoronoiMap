"""Tests for vormap_zonal — zonal statistics for Voronoi tessellations."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vormap_zonal import (
    assign_zones, zonal_stats, export_csv, export_json, export_svg,
    _compute_stats, _nearest_seed_index, STAT_NAMES, main,
)


class TestNearestSeedIndex(unittest.TestCase):
    def test_single_seed(self):
        self.assertEqual(_nearest_seed_index([(0, 0)], 5, 5), 0)

    def test_chooses_closest(self):
        seeds = [(0, 0), (10, 10), (20, 20)]
        self.assertEqual(_nearest_seed_index(seeds, 9, 9), 1)

    def test_exact_match(self):
        seeds = [(5, 5), (10, 10)]
        self.assertEqual(_nearest_seed_index(seeds, 5, 5), 0)


class TestAssignZones(unittest.TestCase):
    def test_empty_observations(self):
        seeds = [(0, 0), (10, 10)]
        zones = assign_zones(seeds, [])
        self.assertEqual(zones, {0: [], 1: []})

    def test_empty_seeds_raises(self):
        with self.assertRaises(ValueError):
            assign_zones([], [(1, 1, 5.0)])

    def test_assigns_correctly(self):
        seeds = [(0, 0), (100, 100)]
        obs = [(1, 1, 10.0), (2, 2, 20.0), (99, 99, 30.0)]
        zones = assign_zones(seeds, obs)
        self.assertEqual(len(zones[0]), 2)
        self.assertEqual(len(zones[1]), 1)

    def test_multi_value_columns(self):
        seeds = [(0, 0)]
        obs = [(1, 1, 10.0, 20.0)]
        zones = assign_zones(seeds, obs, value_columns=2)
        self.assertEqual(zones[0], [(10.0, 20.0)])


class TestComputeStats(unittest.TestCase):
    def test_empty(self):
        s = _compute_stats([])
        for k in STAT_NAMES:
            self.assertIsNone(s[k])

    def test_single_value(self):
        s = _compute_stats([5.0])
        self.assertEqual(s['count'], 1)
        self.assertEqual(s['sum'], 5.0)
        self.assertEqual(s['mean'], 5.0)
        self.assertEqual(s['median'], 5.0)
        self.assertEqual(s['std'], 0.0)
        self.assertEqual(s['min'], 5.0)
        self.assertEqual(s['max'], 5.0)
        self.assertEqual(s['range'], 0.0)
        self.assertEqual(s['variance'], 0.0)

    def test_multiple_values(self):
        s = _compute_stats([2.0, 4.0, 6.0, 8.0])
        self.assertEqual(s['count'], 4)
        self.assertEqual(s['sum'], 20.0)
        self.assertEqual(s['mean'], 5.0)
        self.assertEqual(s['median'], 5.0)  # (4+6)/2
        self.assertAlmostEqual(s['std'], math.sqrt(5.0))
        self.assertEqual(s['min'], 2.0)
        self.assertEqual(s['max'], 8.0)
        self.assertEqual(s['range'], 6.0)

    def test_odd_count_median(self):
        s = _compute_stats([1.0, 3.0, 5.0])
        self.assertEqual(s['median'], 3.0)


class TestZonalStats(unittest.TestCase):
    def setUp(self):
        self.seeds = [(0, 0), (100, 100), (200, 0)]
        self.obs = [
            (5, 5, 10.0),
            (10, 10, 20.0),
            (95, 95, 30.0),
            (105, 105, 40.0),
            (195, 5, 50.0),
        ]

    def test_basic(self):
        results = zonal_stats(self.seeds, self.obs)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]['zone'], 0)
        self.assertEqual(results[0]['count'], 2)
        self.assertAlmostEqual(results[0]['mean'], 15.0)

    def test_filter_stats(self):
        results = zonal_stats(self.seeds, self.obs, stats=['count', 'mean'])
        for r in results:
            self.assertIn('count', r)
            self.assertIn('mean', r)
            self.assertNotIn('std', r)

    def test_unknown_stat_raises(self):
        with self.assertRaises(ValueError):
            zonal_stats(self.seeds, self.obs, stats=['bogus'])

    def test_empty_zone(self):
        seeds = [(0, 0), (1000, 1000)]
        obs = [(1, 1, 5.0)]
        results = zonal_stats(seeds, obs)
        # Zone 1 should have no observations
        self.assertIsNone(results[1]['count'])

    def test_multi_value(self):
        seeds = [(0, 0)]
        obs = [(1, 1, 10.0, 20.0), (2, 2, 30.0, 40.0)]
        results = zonal_stats(seeds, obs, value_columns=2)
        self.assertIn('v1_mean', results[0])
        self.assertIn('v2_mean', results[0])
        self.assertAlmostEqual(results[0]['v1_mean'], 20.0)
        self.assertAlmostEqual(results[0]['v2_mean'], 30.0)


class TestExportCSV(unittest.TestCase):
    def test_write_csv(self):
        results = [
            {'zone': 0, 'seed_x': 0, 'seed_y': 0, 'count': 2, 'mean': 15.0},
            {'zone': 1, 'seed_x': 10, 'seed_y': 10, 'count': 1, 'mean': 30.0},
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False) as f:
            path = f.name
        try:
            export_csv(results, path)
            with open(path) as f:
                lines = f.read().strip().split('\n')
            self.assertEqual(len(lines), 3)  # header + 2 rows
            self.assertIn('zone', lines[0])
        finally:
            os.unlink(path)

    def test_empty_results(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False) as f:
            path = f.name
        export_csv([], path)
        # File should not be written for empty results
        self.assertEqual(os.path.getsize(path), 0)
        os.unlink(path)


class TestExportJSON(unittest.TestCase):
    def test_write_json(self):
        results = [{'zone': 0, 'count': 5, 'mean': 10.0}]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                         delete=False) as f:
            path = f.name
        try:
            export_json(results, path)
            with open(path) as f:
                data = json.load(f)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['zone'], 0)
        finally:
            os.unlink(path)


class TestExportSVG(unittest.TestCase):
    def test_write_svg(self):
        seeds = [(0, 0), (100, 100)]
        results = [
            {'zone': 0, 'seed_x': 0, 'seed_y': 0, 'mean': 10.0},
            {'zone': 1, 'seed_x': 100, 'seed_y': 100, 'mean': 30.0},
        ]
        with tempfile.NamedTemporaryFile(mode='w', suffix='.svg',
                                         delete=False) as f:
            path = f.name
        try:
            export_svg(results, seeds, path, stat_key='mean',
                       title='Test Zonal Map')
            with open(path) as f:
                content = f.read()
            self.assertIn('<svg', content)
            self.assertIn('Zone 0', content)
            self.assertIn('Test Zonal Map', content)
        finally:
            os.unlink(path)

    def test_empty_seeds(self):
        # Should not crash
        export_svg([], [], 'dummy.svg')


class TestCLI(unittest.TestCase):
    def test_cli_basic(self):
        # Create temp seed and observation files
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False) as f:
            f.write('x,y\n0,0\n100,100\n')
            seeds_path = f.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv',
                                         delete=False) as f:
            f.write('x,y,value\n5,5,10\n95,95,30\n')
            obs_path = f.name
        csv_out = seeds_path + '_out.csv'
        json_out = seeds_path + '_out.json'

        try:
            main([seeds_path, obs_path, '--csv', csv_out, '--json', json_out])
            self.assertTrue(os.path.exists(csv_out))
            self.assertTrue(os.path.exists(json_out))
            with open(json_out) as f:
                data = json.load(f)
            self.assertEqual(len(data), 2)
        finally:
            for p in [seeds_path, obs_path, csv_out, json_out]:
                if os.path.exists(p):
                    os.unlink(p)


if __name__ == '__main__':
    unittest.main()
