"""Tests for vormap_label — Automatic Label Placement."""

import json
import math
import os
import tempfile
import unittest

from vormap_label import (
    place_labels,
    LabelConfig,
    PlacedLabel,
    export_labeled_svg,
    export_labeled_html,
    export_labels_json,
    export_labels_csv,
    _polygon_area,
    _polygon_centroid,
    _point_in_polygon,
    _pole_of_inaccessibility,
    _dist_to_polygon_edge,
)


# --- Geometry helpers ---

class TestPolygonArea(unittest.TestCase):
    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertAlmostEqual(_polygon_area(sq), 1.0)

    def test_triangle(self):
        tri = [(0, 0), (4, 0), (0, 3)]
        self.assertAlmostEqual(_polygon_area(tri), 6.0)

    def test_empty(self):
        self.assertEqual(_polygon_area([]), 0.0)


class TestPolygonCentroid(unittest.TestCase):
    def test_square(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cx, cy = _polygon_centroid(sq)
        self.assertAlmostEqual(cx, 5.0)
        self.assertAlmostEqual(cy, 5.0)

    def test_single_point(self):
        cx, cy = _polygon_centroid([(3, 7)])
        self.assertAlmostEqual(cx, 3.0)
        self.assertAlmostEqual(cy, 7.0)


class TestPointInPolygon(unittest.TestCase):
    def test_inside(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(_point_in_polygon(5, 5, sq))

    def test_outside(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertFalse(_point_in_polygon(15, 5, sq))


class TestPOI(unittest.TestCase):
    def test_square(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        x, y, r = _pole_of_inaccessibility(sq, precision=0.1)
        self.assertAlmostEqual(x, 5.0, places=0)
        self.assertAlmostEqual(y, 5.0, places=0)
        self.assertGreater(r, 4.0)

    def test_degenerate(self):
        x, y, r = _pole_of_inaccessibility([(0, 0), (1, 1)], precision=0.1)
        self.assertEqual(r, 0.0)


# --- Label placement ---

class TestPlaceLabels(unittest.TestCase):
    def _make_grid(self):
        """4 square cells in a 2x2 grid."""
        seeds = [(25, 25), (75, 25), (25, 75), (75, 75)]
        regions = {
            (25, 25): [(0, 0), (50, 0), (50, 50), (0, 50)],
            (75, 25): [(50, 0), (100, 0), (100, 50), (50, 50)],
            (25, 75): [(0, 50), (50, 50), (50, 100), (0, 100)],
            (75, 75): [(50, 50), (100, 50), (100, 100), (50, 100)],
        }
        return seeds, regions

    def test_default_labels(self):
        seeds, regions = self._make_grid()
        labels = place_labels(seeds, regions)
        self.assertEqual(len(labels), 4)
        for l in labels:
            self.assertTrue(l.text.isdigit())
            self.assertGreater(l.font_size, 0)

    def test_poi_inside_cell(self):
        seeds, regions = self._make_grid()
        labels = place_labels(seeds, regions, LabelConfig(strategy="poi"))
        for l in labels:
            key = l.seed
            verts = regions[key]
            self.assertTrue(_point_in_polygon(l.x, l.y, verts))

    def test_centroid_strategy(self):
        seeds, regions = self._make_grid()
        labels = place_labels(seeds, regions, LabelConfig(strategy="centroid"))
        self.assertEqual(len(labels), 4)

    def test_custom_field(self):
        seeds, regions = self._make_grid()
        data = [{"name": "A"}, {"name": "B"}, {"name": "C"}, {"name": "D"}]
        labels = place_labels(seeds, regions, LabelConfig(label_field="name"), data)
        texts = {l.text for l in labels}
        self.assertEqual(texts, {"A", "B", "C", "D"})


# --- Export ---

class TestExportSVG(unittest.TestCase):
    def test_writes_file(self):
        seeds = [(25, 25), (75, 75)]
        regions = {
            (25, 25): [(0, 0), (50, 0), (50, 50), (0, 50)],
            (75, 75): [(50, 50), (100, 50), (100, 100), (50, 100)],
        }
        labels = place_labels(seeds, regions)
        path = "test_label_out.svg"
        try:
            svg = export_labeled_svg(seeds, regions, labels, path)
            self.assertIn("<svg", svg)
            self.assertIn("<text", svg)
            self.assertTrue(os.path.exists(path))
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportHTML(unittest.TestCase):
    def test_writes_file(self):
        seeds = [(25, 25), (75, 75)]
        regions = {
            (25, 25): [(0, 0), (50, 0), (50, 50), (0, 50)],
            (75, 75): [(50, 50), (100, 50), (100, 100), (50, 100)],
        }
        labels = place_labels(seeds, regions)
        path = "test_label_out.html"
        try:
            export_labeled_html(seeds, regions, labels, path)
            with open(path) as fh:
                html = fh.read()
            self.assertIn("canvas", html)
            self.assertIn("Labels", html)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportJSON(unittest.TestCase):
    def test_round_trip(self):
        seeds = [(50, 50)]
        regions = {(50, 50): [(0, 0), (100, 0), (100, 100), (0, 100)]}
        labels = place_labels(seeds, regions)
        path = "test_label_out.json"
        try:
            export_labels_json(labels, path)
            with open(path) as fh:
                data = json.load(fh)
            self.assertEqual(len(data), 1)
            self.assertIn("x", data[0])
            self.assertIn("text", data[0])
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestExportCSV(unittest.TestCase):
    def test_writes_header(self):
        seeds = [(50, 50)]
        regions = {(50, 50): [(0, 0), (100, 0), (100, 100), (0, 100)]}
        labels = place_labels(seeds, regions)
        path = "test_label_out.csv"
        try:
            export_labels_csv(labels, path)
            with open(path) as fh:
                lines = fh.readlines()
            self.assertTrue(lines[0].startswith("text,x,y"))
            self.assertEqual(len(lines), 2)  # header + 1 label
        finally:
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()
