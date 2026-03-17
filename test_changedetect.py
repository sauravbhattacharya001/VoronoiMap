"""Tests for vormap_changedetect — Spatial Change Detection module."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_changedetect import (
    detect_changes,
    ChangeReport,
    ShiftedPoint,
    DensityCell,
    _dist,
    _bearing,
    _compass,
    _match_points,
    _compute_density_grid,
)


class TestGeometryHelpers(unittest.TestCase):
    def test_dist_zero(self):
        self.assertAlmostEqual(_dist((0, 0), (0, 0)), 0.0)

    def test_dist_known(self):
        self.assertAlmostEqual(_dist((0, 0), (3, 4)), 5.0)

    def test_bearing_north(self):
        self.assertAlmostEqual(_bearing((0, 0), (0, 10)), 0.0)

    def test_bearing_east(self):
        self.assertAlmostEqual(_bearing((0, 0), (10, 0)), 90.0)

    def test_bearing_south(self):
        self.assertAlmostEqual(_bearing((0, 0), (0, -10)), 180.0)

    def test_bearing_west(self):
        self.assertAlmostEqual(_bearing((0, 0), (-10, 0)), 270.0)

    def test_compass_directions(self):
        self.assertEqual(_compass(0), "N")
        self.assertEqual(_compass(45), "NE")
        self.assertEqual(_compass(90), "E")
        self.assertEqual(_compass(135), "SE")
        self.assertEqual(_compass(180), "S")
        self.assertEqual(_compass(225), "SW")
        self.assertEqual(_compass(270), "W")
        self.assertEqual(_compass(315), "NW")


class TestMatchPoints(unittest.TestCase):
    def test_exact_match(self):
        pts = [(0, 0), (10, 10)]
        matched, added, removed = _match_points(pts, pts, 1.0)
        self.assertEqual(len(matched), 2)
        self.assertEqual(len(added), 0)
        self.assertEqual(len(removed), 0)

    def test_all_added(self):
        matched, added, removed = _match_points([], [(1, 1), (2, 2)], 5.0)
        self.assertEqual(len(added), 2)
        self.assertEqual(len(matched), 0)

    def test_all_removed(self):
        matched, added, removed = _match_points([(1, 1)], [], 5.0)
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(matched), 0)

    def test_threshold_respected(self):
        before = [(0, 0)]
        after = [(100, 100)]
        matched, added, removed = _match_points(before, after, 5.0)
        self.assertEqual(len(matched), 0)
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(added), 1)

    def test_close_match(self):
        before = [(0, 0)]
        after = [(1, 1)]
        matched, added, removed = _match_points(before, after, 5.0)
        self.assertEqual(len(matched), 1)


class TestDensityGrid(unittest.TestCase):
    def test_empty(self):
        result = _compute_density_grid([], [])
        self.assertEqual(result, [])

    def test_basic_grid(self):
        before = [(0, 0), (5, 5)]
        after = [(0, 0), (5, 5), (3, 3)]
        cells = _compute_density_grid(before, after, grid_size=2)
        self.assertEqual(len(cells), 4)

    def test_density_change_detected(self):
        before = [(0, 0), (10, 10)]
        after = [(0, 0), (10, 10), (3, 3), (7, 7)]
        cells = _compute_density_grid(before, after, grid_size=2)
        # More points after than before
        has_increase = any(c.change > 0 for c in cells)
        self.assertTrue(has_increase)


class TestDetectChanges(unittest.TestCase):
    def test_empty_datasets(self):
        report = detect_changes([], [])
        self.assertEqual(report.before_count, 0)
        self.assertEqual(report.after_count, 0)
        self.assertEqual(report.summary["stability_score"], 1.0)

    def test_identical_datasets(self):
        pts = [(0, 0), (10, 10), (20, 20)]
        report = detect_changes(pts, pts, match_threshold=1.0)
        self.assertEqual(len(report.added), 0)
        self.assertEqual(len(report.removed), 0)
        self.assertEqual(len(report.shifted), 0)
        self.assertEqual(report.summary["stable_count"], 3)

    def test_all_added(self):
        report = detect_changes([], [(5, 5), (10, 10)])
        self.assertEqual(len(report.added), 2)
        self.assertEqual(report.summary["added_count"], 2)

    def test_all_removed(self):
        report = detect_changes([(5, 5), (10, 10)], [])
        self.assertEqual(len(report.removed), 2)
        self.assertEqual(report.summary["removed_count"], 2)

    def test_shifted_detected(self):
        before = [(0, 0), (10, 10)]
        after = [(0, 3), (10, 13)]  # shifted north by 3
        report = detect_changes(before, after, match_threshold=5.0)
        self.assertEqual(len(report.shifted), 2)
        for s in report.shifted:
            self.assertAlmostEqual(s.distance, 3.0, places=1)
            self.assertEqual(s.compass, "N")

    def test_mixed_changes(self):
        before = [(0, 0), (10, 10), (20, 20)]
        after = [(0, 0), (10, 12), (30, 30)]  # stable, shifted, added+removed
        report = detect_changes(before, after, match_threshold=5.0)
        self.assertGreater(len(report.shifted), 0)
        self.assertEqual(report.summary["change_rate"] > 0, True)

    def test_stability_score_range(self):
        before = [(i, i) for i in range(10)]
        after = [(i + 100, i + 100) for i in range(10)]
        report = detect_changes(before, after, match_threshold=1.0)
        self.assertGreaterEqual(report.summary["stability_score"], 0.0)
        self.assertLessEqual(report.summary["stability_score"], 1.0)

    def test_dominant_direction(self):
        before = [(i, 0) for i in range(5)]
        after = [(i, 5) for i in range(5)]  # all shift north
        report = detect_changes(before, after, match_threshold=10.0)
        self.assertEqual(report.summary["dominant_direction"], "N")

    def test_mean_displacement(self):
        before = [(0, 0)]
        after = [(3, 4)]  # distance = 5
        report = detect_changes(before, after, match_threshold=10.0)
        self.assertAlmostEqual(report.summary["mean_displacement"], 5.0, places=2)


class TestExportJSON(unittest.TestCase):
    def test_json_export(self):
        before = [(0, 0), (10, 10)]
        after = [(0, 1), (20, 20)]
        report = detect_changes(before, after, match_threshold=5.0)
        path = "test_change_out.json"
        try:
            report.to_json(path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("summary", data)
            self.assertIn("shifted", data)
            self.assertIn("added", data)
            self.assertIn("removed", data)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_to_dict_structure(self):
        report = detect_changes([(0, 0)], [(1, 1)], match_threshold=5.0)
        d = report.to_dict()
        self.assertEqual(d["before_count"], 1)
        self.assertEqual(d["after_count"], 1)


class TestExportCSV(unittest.TestCase):
    def test_csv_export(self):
        before = [(0, 0), (10, 10)]
        after = [(0, 2), (20, 20)]
        report = detect_changes(before, after, match_threshold=5.0)
        path = "test_change_out.csv"
        try:
            report.to_csv(path)
            with open(path) as f:
                content = f.read()
            self.assertIn("shifted", content)
            self.assertIn("section", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportSVG(unittest.TestCase):
    def test_svg_export(self):
        before = [(0, 0), (10, 10), (20, 20)]
        after = [(0, 2), (10, 10), (30, 30)]
        report = detect_changes(before, after, match_threshold=5.0)
        path = "test_change_out.svg"
        try:
            report.to_svg(path)
            with open(path) as f:
                content = f.read()
            self.assertIn("svg", content)
            self.assertIn("Change Detection", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_empty(self):
        report = detect_changes([], [])
        path = "test_change_empty.svg"
        try:
            report.to_svg(path)
            self.assertTrue(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestChangeReport(unittest.TestCase):
    def test_report_attributes(self):
        report = detect_changes([(0, 0)], [(5, 5)], match_threshold=10.0)
        self.assertEqual(report.before_count, 1)
        self.assertEqual(report.after_count, 1)
        self.assertEqual(report.match_threshold, 10.0)

    def test_max_displacement(self):
        before = [(0, 0), (10, 10)]
        after = [(3, 4), (10, 15)]  # displacements: 5, 5
        report = detect_changes(before, after, match_threshold=10.0)
        self.assertGreater(report.summary["max_displacement"], 0)

    def test_density_cells_in_summary(self):
        before = [(i, i) for i in range(10)]
        after = [(i, i) for i in range(5, 15)]
        report = detect_changes(before, after, match_threshold=2.0)
        self.assertIn("density_increase_cells", report.summary)
        self.assertIn("density_decrease_cells", report.summary)


if __name__ == "__main__":
    unittest.main()
