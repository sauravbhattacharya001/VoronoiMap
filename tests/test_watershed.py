"""Tests for vormap_watershed — Voronoi Watershed & Flow Analysis."""

import csv
import json
import os
import tempfile
import unittest

from vormap_watershed import (
    FlowCell,
    FlowPath,
    Basin,
    WatershedResult,
    watershed_analysis,
    export_watershed_json,
    export_watershed_csv,
    _build_adjacency,
    _edge_key,
    _centroid,
    _dist,
    _poly_area,
)


def _make_stats(polygons, areas=None):
    """Helper: build fake region stats dicts from polygon lists."""
    stats = []
    for i, poly in enumerate(polygons):
        s = {"polygon": poly}
        if areas:
            s["area"] = areas[i]
        stats.append(s)
    return stats


def _triangle_grid():
    """3 cells arranged in a triangle — center at different elevations.

    Cell 0: top-left     (high elevation / large area)
    Cell 1: top-right    (medium)
    Cell 2: bottom-center (low — the sink)
    """
    polys = [
        [(0, 0), (100, 0), (50, 50)],      # area ~2500
        [(100, 0), (200, 0), (150, 50)],    # area ~2500
        [(50, 50), (150, 50), (100, 100)],  # area ~2500
    ]
    # Make areas different to create flow gradient
    # Enlarge cell 0 to make it highest, shrink cell 2 for sink
    polys = [
        [(0, 0), (120, 0), (60, 80)],       # large
        [(120, 0), (200, 0), (160, 60)],     # medium
        [(60, 80), (160, 60), (110, 130)],   # small — sink
    ]
    return _make_stats(polys)


def _linear_chain():
    """4 cells in a line, descending elevation (area).

    Cell 0 (highest) → 1 → 2 → 3 (lowest = sink)
    Each shares an edge with its neighbor.
    """
    polys = [
        [(0, 0), (100, 0), (100, 50), (0, 50)],
        [(100, 0), (200, 0), (200, 50), (100, 50)],
        [(200, 0), (300, 0), (300, 50), (200, 50)],
        [(300, 0), (400, 0), (400, 50), (300, 50)],
    ]
    # Increasing size = decreasing area for flow
    # Actually we want area to decrease 0→3 for "downhill"
    # Let's just make them different heights
    polys = [
        [(0, 0), (100, 0), (100, 100), (0, 100)],       # area=10000
        [(100, 0), (180, 0), (180, 100), (100, 100)],    # area=8000
        [(180, 0), (240, 0), (240, 100), (180, 100)],    # area=6000
        [(240, 0), (280, 0), (280, 100), (240, 100)],    # area=4000
    ]
    return _make_stats(polys)


class TestDataStructures(unittest.TestCase):

    def test_flow_cell_defaults(self):
        c = FlowCell(index=0, centroid=(1.0, 2.0), elevation=5.0)
        self.assertIsNone(c.flow_to)
        self.assertEqual(c.slope, 0.0)
        self.assertEqual(c.accumulation, 1)
        self.assertEqual(c.basin_id, -1)
        self.assertFalse(c.is_sink)
        self.assertFalse(c.is_ridge)

    def test_basin_defaults(self):
        b = Basin(basin_id=0, sink_index=3, sink_centroid=(1, 2),
                  sink_elevation=0.5)
        self.assertEqual(b.cell_count, 0)
        self.assertEqual(b.total_area, 0.0)
        self.assertIsNone(b.pour_point)

    def test_flow_path_defaults(self):
        fp = FlowPath(source_index=5)
        self.assertEqual(fp.total_drop, 0.0)
        self.assertEqual(fp.path_length, 0.0)

    def test_watershed_result_summary_empty(self):
        r = WatershedResult()
        s = r.summary()
        self.assertIn("Total cells:       0", s)
        self.assertIn("Drainage basins:   0", s)


class TestHelpers(unittest.TestCase):

    def test_edge_key_canonical(self):
        k1 = _edge_key((10, 20), (30, 40))
        k2 = _edge_key((30, 40), (10, 20))
        self.assertEqual(k1, k2)

    def test_edge_key_rounding(self):
        k1 = _edge_key((10.00001, 20.00002), (30, 40))
        k2 = _edge_key((10.0, 20.0), (30, 40))
        self.assertEqual(k1, k2)

    def test_centroid_simple(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        cx, cy = _centroid(poly)
        self.assertAlmostEqual(cx, 5.0, places=1)
        self.assertAlmostEqual(cy, 5.0, places=1)

    def test_centroid_empty(self):
        cx, cy = _centroid([])
        self.assertEqual(cx, 0.0)
        self.assertEqual(cy, 0.0)

    def test_dist(self):
        self.assertAlmostEqual(_dist((0, 0), (3, 4)), 5.0)
        self.assertAlmostEqual(_dist((1, 1), (1, 1)), 0.0)

    def test_poly_area_square(self):
        poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertAlmostEqual(_poly_area(poly), 100.0)

    def test_poly_area_triangle(self):
        poly = [(0, 0), (10, 0), (5, 10)]
        self.assertAlmostEqual(_poly_area(poly), 50.0)

    def test_poly_area_empty(self):
        self.assertEqual(_poly_area([]), 0.0)
        self.assertEqual(_poly_area([(1, 2)]), 0.0)


class TestAdjacency(unittest.TestCase):

    def test_linear_chain_adjacency(self):
        stats = _linear_chain()
        adj = _build_adjacency(stats)
        # Cell 0 and 1 share edge at x=100
        self.assertIn(1, adj[0])
        self.assertIn(0, adj[1])
        # Cell 1 and 2 share edge at x=180
        self.assertIn(2, adj[1])
        self.assertIn(1, adj[2])
        # Cell 0 and 2 don't share an edge
        self.assertNotIn(2, adj[0])

    def test_no_self_adjacency(self):
        stats = _linear_chain()
        adj = _build_adjacency(stats)
        for i, neighbors in adj.items():
            self.assertNotIn(i, neighbors)


class TestWatershedAnalysis(unittest.TestCase):

    def test_empty_stats(self):
        result = watershed_analysis([])
        self.assertEqual(result.total_cells, 0)
        self.assertEqual(len(result.basins), 0)

    def test_single_cell_is_sink(self):
        stats = _make_stats([[(0, 0), (10, 0), (10, 10), (0, 10)]])
        result = watershed_analysis(stats)
        self.assertEqual(result.total_cells, 1)
        self.assertTrue(result.cells[0].is_sink)
        self.assertEqual(len(result.sink_cells), 1)
        self.assertEqual(len(result.basins), 1)

    def test_linear_chain_flow_direction(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        # Flow should go 0→1→2→3 (decreasing area)
        self.assertIsNotNone(result.cells[0].flow_to)
        # Cell 3 should be a sink (smallest area)
        self.assertTrue(result.cells[3].is_sink)
        self.assertIsNone(result.cells[3].flow_to)

    def test_linear_chain_single_basin(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        # All cells should drain to same sink → one basin
        self.assertEqual(len(result.basins), 1)
        basin = result.basins[0]
        self.assertEqual(basin.cell_count, 4)
        self.assertEqual(basin.sink_index, 3)

    def test_accumulation_increases_downstream(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        # Sink cell should have highest accumulation
        sink = result.cells[3]
        self.assertGreaterEqual(sink.accumulation, 4)

    def test_ridge_cells_identified(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        # Cell 0 is the ridge (highest, no inflow)
        self.assertTrue(result.cells[0].is_ridge)
        self.assertIn(0, result.ridge_cells)

    def test_flow_paths_traced(self):
        stats = _linear_chain()
        result = watershed_analysis(stats, trace_paths=True)
        self.assertGreater(len(result.flow_paths), 0)
        # At least one path should end at the sink
        for fp in result.flow_paths:
            self.assertGreater(len(fp.cell_indices), 1)
            self.assertGreaterEqual(fp.total_drop, 0)

    def test_no_flow_paths_when_disabled(self):
        stats = _linear_chain()
        result = watershed_analysis(stats, trace_paths=False)
        self.assertEqual(len(result.flow_paths), 0)

    def test_slope_positive_for_downhill(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        for cell in result.cells:
            if cell.flow_to is not None:
                self.assertGreater(cell.slope, 0)

    def test_basin_stats_computed(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        basin = result.basins[0]
        self.assertGreater(basin.total_area, 0)
        self.assertGreater(basin.mean_elevation, 0)
        self.assertGreater(basin.max_accumulation, 0)

    def test_distance_to_sink(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        # Sink has 0 distance to itself
        sink = result.cells[3]
        self.assertEqual(sink.distance_to_sink, 0.0)
        # Non-sink cells have positive distance
        if result.cells[0].basin_id >= 0:
            self.assertGreater(result.cells[0].distance_to_sink, 0)

    def test_summary_text(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        s = result.summary()
        self.assertIn("Voronoi Watershed Analysis", s)
        self.assertIn("Total cells:", s)
        self.assertIn("Drainage basins:", s)
        self.assertIn("Sink cells:", s)
        self.assertIn("Ridge cells:", s)
        self.assertIn("Largest basin:", s)
        self.assertIn("Max accumulation:", s)

    def test_max_path_traces_limit(self):
        stats = _linear_chain()
        result = watershed_analysis(stats, max_path_traces=1)
        self.assertLessEqual(len(result.flow_paths), 1)


class TestExportJSON(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_json_export_structure(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        path = os.path.join(self.tmpdir, "ws.json")
        export_watershed_json(result, path)

        with open(path) as f:
            doc = json.load(f)

        self.assertIn("summary", doc)
        self.assertIn("basins", doc)
        self.assertIn("cells", doc)
        self.assertIn("flowPaths", doc)

        self.assertEqual(doc["summary"]["totalCells"], 4)
        self.assertGreater(len(doc["basins"]), 0)
        self.assertEqual(len(doc["cells"]), 4)

    def test_json_basin_fields(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        path = os.path.join(self.tmpdir, "ws.json")
        export_watershed_json(result, path)

        with open(path) as f:
            doc = json.load(f)

        basin = doc["basins"][0]
        for key in ["basinId", "sinkIndex", "sinkCentroid", "cellCount",
                     "totalArea", "meanElevation", "maxAccumulation"]:
            self.assertIn(key, basin)

    def test_json_cell_fields(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        path = os.path.join(self.tmpdir, "ws.json")
        export_watershed_json(result, path)

        with open(path) as f:
            doc = json.load(f)

        cell = doc["cells"][0]
        for key in ["index", "centroid", "elevation", "flowTo",
                     "slope", "accumulation", "basinId", "isSink", "isRidge"]:
            self.assertIn(key, cell)


class TestExportCSV(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_csv_export_header_and_rows(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        path = os.path.join(self.tmpdir, "ws.csv")
        export_watershed_csv(result, path)

        with open(path) as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        self.assertIn("cell_index", header)
        self.assertIn("elevation", header)
        self.assertIn("accumulation", header)
        self.assertIn("basin_id", header)
        self.assertEqual(len(rows), 4)

    def test_csv_sink_marked(self):
        stats = _linear_chain()
        result = watershed_analysis(stats)
        path = os.path.join(self.tmpdir, "ws.csv")
        export_watershed_csv(result, path)

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        sink_rows = [r for r in rows if r["is_sink"] == "True"]
        self.assertEqual(len(sink_rows), 1)


class TestMultipleBasins(unittest.TestCase):
    """Test with disconnected cell groups that form separate basins."""

    def _two_basins(self):
        """Two groups of cells with no shared edges → 2 basins.
        Within each group, cells have different areas to create flow."""
        polys = [
            # Group A (left): cell 0 larger → flows to cell 1 (smaller)
            [(0, 0), (80, 0), (80, 50), (0, 50)],     # area=4000
            [(80, 0), (120, 0), (120, 50), (80, 50)],  # area=2000 (sink)
            # Group B (right, separated): cell 2 larger → flows to cell 3
            [(300, 0), (380, 0), (380, 50), (300, 50)],  # area=4000
            [(380, 0), (420, 0), (420, 50), (380, 50)],  # area=2000 (sink)
        ]
        return _make_stats(polys)

    def test_two_basins_detected(self):
        stats = self._two_basins()
        result = watershed_analysis(stats)
        self.assertEqual(len(result.basins), 2)

    def test_two_basins_cells_assigned(self):
        stats = self._two_basins()
        result = watershed_analysis(stats)
        basin_ids = set(c.basin_id for c in result.cells if c.basin_id >= 0)
        self.assertEqual(len(basin_ids), 2)


class TestExportSVG(unittest.TestCase):
    """Tests for export_watershed_svg."""

    def _simple_result(self):
        polys = [
            [(0, 0), (50, 0), (50, 50), (0, 50)],
            [(50, 0), (100, 0), (100, 50), (50, 50)],
        ]
        stats = _make_stats(polys)
        data = {"bounds": (0, 100, 0, 50)}
        return watershed_analysis(stats), stats, data

    def test_svg_is_valid_xml(self):
        from vormap_watershed import export_watershed_svg
        result, stats, data = self._simple_result()
        path = "test_ws_output.svg"
        try:
            export_watershed_svg(result, stats, data, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("</svg>", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_contains_basins(self):
        from vormap_watershed import export_watershed_svg
        result, stats, data = self._simple_result()
        path = "test_ws_polygons.svg"
        try:
            export_watershed_svg(result, stats, data, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<polygon", content)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_svg_returns_path(self):
        from vormap_watershed import export_watershed_svg
        result, stats, data = self._simple_result()
        path = "test_ws_retval.svg"
        try:
            out = export_watershed_svg(result, stats, data, path)
            self.assertEqual(out, path)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestGetElevation(unittest.TestCase):
    """Tests for _get_elevation helper."""

    def test_default_uses_area(self):
        from vormap_watershed import _get_elevation
        stat = {"polygon": [(0,0),(10,0),(10,10),(0,10)]}
        # area of 10x10 square = 100
        self.assertAlmostEqual(_get_elevation(stat, "area"), 100.0)

    def test_missing_attribute_falls_back_to_area(self):
        from vormap_watershed import _get_elevation
        stat = {"area": 10.0, "polygon": [(0,0),(1,0),(1,1),(0,1)]}
        # Non-existent attribute should fall back
        elev = _get_elevation(stat, "nonexistent")
        self.assertIsInstance(elev, float)

    def test_custom_attribute(self):
        from vormap_watershed import _get_elevation
        stat = {"area": 10.0, "density": 99.5, "polygon": [(0,0),(1,0),(1,1),(0,1)]}
        self.assertAlmostEqual(_get_elevation(stat, "density"), 99.5)


class TestCLI(unittest.TestCase):
    """Tests for the main() CLI entry point."""

    def test_help_flag(self):
        from vormap_watershed import main
        with self.assertRaises(SystemExit) as ctx:
            main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_missing_data_file(self):
        from vormap_watershed import main
        with self.assertRaises((FileNotFoundError, SystemExit)):
            main(["nonexistent_ws_test_12345.json"])


if __name__ == "__main__":
    unittest.main()
