"""Tests for vormap_mapalgebra — Voronoi Map Algebra."""

import json
import math
import os
import tempfile
import unittest

from vormap_mapalgebra import (
    CellLayer,
    ZonalResult,
    export_algebra_csv,
    export_algebra_json,
    export_zonal_csv,
    focal_count,
    focal_diversity,
    focal_majority,
    focal_max,
    focal_mean,
    focal_median,
    focal_min,
    focal_range,
    focal_slope,
    focal_std,
    focal_sum,
    format_algebra_report,
    layer_stack,
    local_abs,
    local_add,
    local_apply,
    local_clamp,
    local_divide,
    local_log,
    local_max,
    local_min,
    local_multiply,
    local_normalise,
    local_offset,
    local_power,
    local_reclassify,
    local_scale,
    local_standardise,
    local_subtract,
    local_threshold,
    main,
    weighted_overlay,
    zonal_apply,
    zonal_stats,
)


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_layer(values=None, adj=None, name="test", nodata=None):
    """Build a small test layer.  Default: 5-cell linear chain."""
    if values is None:
        values = {0: 10.0, 1: 20.0, 2: 30.0, 3: 40.0, 4: 50.0}
    if adj is None:
        adj = {
            0: [1], 1: [0, 2], 2: [1, 3], 3: [2, 4], 4: [3],
        }
    return CellLayer.from_dict(values, adj, name=name, nodata=nodata)


def _make_grid_layer():
    """2x2 grid with adjacency."""
    #  0 -- 1
    #  |    |
    #  2 -- 3
    return _make_layer(
        {0: 1.0, 1: 2.0, 2: 3.0, 3: 4.0},
        {0: [1, 2], 1: [0, 3], 2: [0, 3], 3: [1, 2]},
    )


# ── CellLayer ────────────────────────────────────────────────────────

class TestCellLayer(unittest.TestCase):
    def test_from_dict(self):
        layer = _make_layer()
        self.assertEqual(len(layer), 5)
        self.assertEqual(layer.values[0], 10.0)

    def test_valid_cells(self):
        layer = _make_layer(nodata=-999)
        layer.values[2] = -999
        valid = layer.valid_cells()
        self.assertNotIn(2, valid)
        self.assertEqual(len(valid), 4)

    def test_valid_cells_nan(self):
        layer = _make_layer()
        layer.values[1] = float("nan")
        valid = layer.valid_cells()
        self.assertNotIn(1, valid)

    def test_valid_neighbours(self):
        layer = _make_layer()
        nbrs = layer.valid_neighbours(2)
        self.assertEqual(sorted(nbrs), [1, 3])

    def test_copy(self):
        original = _make_layer()
        copy = original.copy("copy")
        copy.values[0] = 999
        self.assertEqual(original.values[0], 10.0)
        self.assertEqual(copy.name, "copy")

    def test_stats(self):
        layer = _make_layer()
        s = layer.stats()
        self.assertEqual(s["count"], 5)
        self.assertEqual(s["min"], 10.0)
        self.assertEqual(s["max"], 50.0)
        self.assertAlmostEqual(s["mean"], 30.0)
        self.assertGreater(s["std"], 0)

    def test_stats_empty(self):
        layer = _make_layer({}, {})
        s = layer.stats()
        self.assertEqual(s["count"], 0)

    def test_to_dict_roundtrip(self):
        layer = _make_layer(nodata=-999)
        d = layer.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["nodata"], -999)
        self.assertIn("values", d)
        self.assertIn("adjacency", d)

    def test_json_roundtrip(self):
        layer = _make_layer()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            layer.to_json(path)
            loaded = CellLayer.from_json(path)
            self.assertEqual(len(loaded), len(layer))
            self.assertEqual(loaded.values[0], layer.values[0])
        finally:
            os.unlink(path)

    def test_repr(self):
        layer = _make_layer()
        self.assertIn("test", repr(layer))
        self.assertIn("5", repr(layer))


# ── Local Operations ─────────────────────────────────────────────────

class TestLocalOps(unittest.TestCase):
    def test_add(self):
        a = _make_layer({0: 1.0, 1: 2.0}, {0: [1], 1: [0]})
        b = _make_layer({0: 10.0, 1: 20.0}, {0: [1], 1: [0]})
        result = local_add(a, b)
        self.assertEqual(result.values[0], 11.0)
        self.assertEqual(result.values[1], 22.0)

    def test_subtract(self):
        a = _make_layer({0: 10.0, 1: 20.0}, {0: [1], 1: [0]})
        b = _make_layer({0: 3.0, 1: 5.0}, {0: [1], 1: [0]})
        result = local_subtract(a, b)
        self.assertEqual(result.values[0], 7.0)

    def test_multiply(self):
        a = _make_layer({0: 3.0, 1: 4.0}, {0: [1], 1: [0]})
        b = _make_layer({0: 2.0, 1: 5.0}, {0: [1], 1: [0]})
        result = local_multiply(a, b)
        self.assertEqual(result.values[0], 6.0)
        self.assertEqual(result.values[1], 20.0)

    def test_divide(self):
        a = _make_layer({0: 10.0, 1: 20.0}, {0: [1], 1: [0]})
        b = _make_layer({0: 2.0, 1: 5.0}, {0: [1], 1: [0]})
        result = local_divide(a, b)
        self.assertEqual(result.values[0], 5.0)
        self.assertEqual(result.values[1], 4.0)

    def test_divide_by_zero(self):
        a = _make_layer({0: 10.0}, {0: []})
        b = _make_layer({0: 0.0}, {0: []})
        result = local_divide(a, b)
        self.assertTrue(math.isnan(result.values[0]))

    def test_local_max(self):
        a = _make_layer({0: 5.0, 1: 3.0}, {0: [1], 1: [0]})
        b = _make_layer({0: 2.0, 1: 7.0}, {0: [1], 1: [0]})
        result = local_max(a, b)
        self.assertEqual(result.values[0], 5.0)
        self.assertEqual(result.values[1], 7.0)

    def test_local_min(self):
        a = _make_layer({0: 5.0, 1: 3.0}, {0: [1], 1: [0]})
        b = _make_layer({0: 2.0, 1: 7.0}, {0: [1], 1: [0]})
        result = local_min(a, b)
        self.assertEqual(result.values[0], 2.0)
        self.assertEqual(result.values[1], 3.0)

    def test_scale(self):
        layer = _make_layer({0: 5.0, 1: 10.0}, {0: [1], 1: [0]})
        result = local_scale(layer, 3.0)
        self.assertEqual(result.values[0], 15.0)
        self.assertEqual(result.values[1], 30.0)

    def test_offset(self):
        layer = _make_layer({0: 5.0}, {0: []})
        result = local_offset(layer, 100.0)
        self.assertEqual(result.values[0], 105.0)

    def test_abs(self):
        layer = _make_layer({0: -5.0, 1: 3.0}, {0: [1], 1: [0]})
        result = local_abs(layer)
        self.assertEqual(result.values[0], 5.0)
        self.assertEqual(result.values[1], 3.0)

    def test_power(self):
        layer = _make_layer({0: 3.0}, {0: []})
        result = local_power(layer, 2.0)
        self.assertAlmostEqual(result.values[0], 9.0)

    def test_log(self):
        layer = _make_layer({0: math.e, 1: 1.0}, {0: [1], 1: [0]})
        result = local_log(layer)
        self.assertAlmostEqual(result.values[0], 1.0, places=5)
        self.assertAlmostEqual(result.values[1], 0.0, places=5)

    def test_log_negative(self):
        layer = _make_layer({0: -5.0}, {0: []})
        result = local_log(layer)
        self.assertTrue(math.isnan(result.values[0]))

    def test_normalise(self):
        layer = _make_layer()  # 10, 20, 30, 40, 50
        result = local_normalise(layer)
        self.assertAlmostEqual(result.values[0], 0.0)
        self.assertAlmostEqual(result.values[4], 1.0)
        self.assertAlmostEqual(result.values[2], 0.5)

    def test_normalise_constant(self):
        layer = _make_layer({0: 5.0, 1: 5.0}, {0: [1], 1: [0]})
        result = local_normalise(layer)
        self.assertEqual(result.values[0], 0.0)

    def test_standardise(self):
        layer = _make_layer()
        result = local_standardise(layer)
        vals = [result.values[c] for c in result.valid_cells()]
        self.assertAlmostEqual(sum(vals) / len(vals), 0.0, places=5)

    def test_threshold(self):
        layer = _make_layer()
        result = local_threshold(layer, 30.0)
        self.assertEqual(result.values[0], 0.0)
        self.assertEqual(result.values[2], 1.0)
        self.assertEqual(result.values[4], 1.0)

    def test_clamp(self):
        layer = _make_layer()
        result = local_clamp(layer, 20.0, 40.0)
        self.assertEqual(result.values[0], 20.0)
        self.assertEqual(result.values[2], 30.0)
        self.assertEqual(result.values[4], 40.0)

    def test_reclassify(self):
        layer = _make_layer()
        result = local_reclassify(layer, [20, 40])
        self.assertEqual(result.values[0], 0.0)  # 10 < 20
        self.assertEqual(result.values[1], 1.0)  # 20 >= 20, < 40
        self.assertEqual(result.values[3], 2.0)  # 40 >= 40

    def test_reclassify_with_labels(self):
        layer = _make_layer({0: 5.0, 1: 15.0, 2: 25.0}, {0: [1], 1: [0, 2], 2: [1]})
        result = local_reclassify(layer, [10, 20], labels=[100.0, 200.0, 300.0])
        self.assertEqual(result.values[0], 100.0)
        self.assertEqual(result.values[1], 200.0)
        self.assertEqual(result.values[2], 300.0)

    def test_reclassify_bad_labels(self):
        layer = _make_layer()
        with self.assertRaises(ValueError):
            local_reclassify(layer, [20], labels=[1.0, 2.0, 3.0])

    def test_apply(self):
        layer = _make_layer({0: 4.0, 1: 9.0}, {0: [1], 1: [0]})
        result = local_apply(layer, math.sqrt, "sqrt")
        self.assertAlmostEqual(result.values[0], 2.0)
        self.assertAlmostEqual(result.values[1], 3.0)

    def test_binary_nodata_propagation(self):
        a = _make_layer({0: 10.0, 1: -999}, {0: [1], 1: [0]}, nodata=-999)
        b = _make_layer({0: 5.0, 1: 5.0}, {0: [1], 1: [0]})
        result = local_add(a, b)
        self.assertEqual(result.values[0], 15.0)
        self.assertEqual(result.values[1], -999)


# ── Focal Operations ─────────────────────────────────────────────────

class TestFocalOps(unittest.TestCase):
    def test_focal_mean(self):
        layer = _make_layer()  # 10, 20, 30, 40, 50 in chain
        result = focal_mean(layer)
        # Cell 0: mean(10, 20) = 15
        self.assertAlmostEqual(result.values[0], 15.0)
        # Cell 2: mean(20, 30, 40) = 30
        self.assertAlmostEqual(result.values[2], 30.0)

    def test_focal_mean_exclude_self(self):
        layer = _make_layer()
        result = focal_mean(layer, include_self=False)
        # Cell 0: only neighbour is 1 (20) → mean = 20
        self.assertAlmostEqual(result.values[0], 20.0)

    def test_focal_median(self):
        layer = _make_grid_layer()  # 1, 2, 3, 4
        result = focal_median(layer)
        # Cell 0: values = [1, 2, 3] → median = 2
        self.assertAlmostEqual(result.values[0], 2.0)

    def test_focal_max(self):
        layer = _make_layer()
        result = focal_max(layer)
        # Cell 2: max(20, 30, 40) = 40
        self.assertEqual(result.values[2], 40.0)

    def test_focal_min(self):
        layer = _make_layer()
        result = focal_min(layer)
        # Cell 2: min(20, 30, 40) = 20
        self.assertEqual(result.values[2], 20.0)

    def test_focal_range(self):
        layer = _make_layer()
        result = focal_range(layer)
        # Cell 2: range(20, 30, 40) = 20
        self.assertEqual(result.values[2], 20.0)

    def test_focal_std(self):
        layer = _make_layer()
        result = focal_std(layer)
        # All values should be non-negative
        for v in result.values.values():
            self.assertGreaterEqual(v, 0)

    def test_focal_sum(self):
        layer = _make_layer()
        result = focal_sum(layer)
        # Cell 0: 10 + 20 = 30
        self.assertEqual(result.values[0], 30.0)

    def test_focal_count(self):
        layer = _make_layer()
        result = focal_count(layer)
        # Cell 0: self + 1 neighbour = 2
        self.assertEqual(result.values[0], 2.0)
        # Cell 2: self + 2 neighbours = 3
        self.assertEqual(result.values[2], 3.0)

    def test_focal_majority(self):
        layer = _make_layer({0: 1.0, 1: 1.0, 2: 2.0, 3: 1.0}, {
            0: [1, 2], 1: [0, 3], 2: [0, 3], 3: [1, 2],
        })
        result = focal_majority(layer)
        # Cell 0: values [1, 1, 2] → majority = 1
        self.assertEqual(result.values[0], 1.0)

    def test_focal_diversity(self):
        layer = _make_grid_layer()
        result = focal_diversity(layer)
        # Cell 0: values [1, 2, 3] → 3 unique
        self.assertEqual(result.values[0], 3.0)

    def test_focal_slope(self):
        layer = _make_layer({0: 0.0, 1: 10.0, 2: 10.0}, {0: [1], 1: [0, 2], 2: [1]})
        result = focal_slope(layer)
        self.assertEqual(result.values[0], 10.0)  # |0 - 10| = 10
        self.assertEqual(result.values[1], 10.0)  # max(|10-0|, |10-10|) = 10


# ── Zonal Operations ─────────────────────────────────────────────────

class TestZonalOps(unittest.TestCase):
    def test_zonal_stats(self):
        layer = _make_layer()
        zones = {0: "A", 1: "A", 2: "B", 3: "B", 4: "B"}
        stats = zonal_stats(layer, zones)
        self.assertEqual(stats["A"].count, 2)
        self.assertAlmostEqual(stats["A"].mean, 15.0)
        self.assertEqual(stats["B"].count, 3)
        self.assertAlmostEqual(stats["B"].mean, 40.0)

    def test_zonal_stats_range(self):
        layer = _make_layer()
        zones = {0: "X", 1: "X", 2: "X"}
        stats = zonal_stats(layer, zones)
        self.assertAlmostEqual(stats["X"].range, 20.0)  # 30 - 10

    def test_zonal_stats_dominant(self):
        layer = _make_layer({0: 1.0, 1: 2.0, 2: 2.0, 3: 1.0}, {
            0: [1], 1: [0, 2], 2: [1, 3], 3: [2],
        })
        zones = {0: "Z", 1: "Z", 2: "Z", 3: "Z"}
        stats = zonal_stats(layer, zones)
        # 1.0 and 2.0 each appear 2 times; Counter.most_common picks one
        self.assertIn(stats["Z"].dominant, [1.0, 2.0])

    def test_zonal_apply(self):
        layer = _make_layer()
        zones = {0: "A", 1: "A", 2: "B", 3: "B", 4: "B"}
        result = zonal_apply(layer, zones, "mean")
        self.assertAlmostEqual(result.values[0], 15.0)
        self.assertAlmostEqual(result.values[1], 15.0)
        self.assertAlmostEqual(result.values[2], 40.0)

    def test_zonal_apply_sum(self):
        layer = _make_layer({0: 10.0, 1: 20.0}, {0: [1], 1: [0]})
        zones = {0: "X", 1: "X"}
        result = zonal_apply(layer, zones, "sum")
        self.assertAlmostEqual(result.values[0], 30.0)

    def test_zonal_apply_bad_stat(self):
        layer = _make_layer()
        with self.assertRaises(ValueError):
            zonal_apply(layer, {0: "A"}, "nonsense")

    def test_zonal_nodata(self):
        layer = _make_layer(nodata=-999)
        layer.values[1] = -999
        zones = {0: "A", 1: "A", 2: "A"}
        stats = zonal_stats(layer, zones)
        self.assertEqual(stats["A"].count, 2)  # cell 1 excluded

    def test_zonal_result_to_dict(self):
        r = ZonalResult(zone_id="Z", count=3, total=30.0, mean=10.0)
        d = r.to_dict()
        self.assertEqual(d["zone_id"], "Z")
        self.assertEqual(d["count"], 3)


# ── Layer Combination ────────────────────────────────────────────────

class TestLayerCombination(unittest.TestCase):
    def test_weighted_overlay(self):
        a = _make_layer({0: 10.0, 1: 20.0}, {0: [1], 1: [0]}, name="a")
        b = _make_layer({0: 100.0, 1: 200.0}, {0: [1], 1: [0]}, name="b")
        result = weighted_overlay([a, b], [1.0, 1.0])
        # Both normalised to [0, 1], equal weights
        self.assertAlmostEqual(result.values[0], 0.0)  # both are min
        self.assertAlmostEqual(result.values[1], 1.0)  # both are max

    def test_weighted_overlay_unequal(self):
        a = _make_layer({0: 0.0, 1: 100.0}, {0: [1], 1: [0]}, name="a")
        b = _make_layer({0: 100.0, 1: 0.0}, {0: [1], 1: [0]}, name="b")
        # weight a 3x more than b
        result = weighted_overlay([a, b], [3.0, 1.0])
        # cell 0: a_norm=0, b_norm=1; weighted=0*0.75+1*0.25=0.25
        self.assertAlmostEqual(result.values[0], 0.25)
        # cell 1: a_norm=1, b_norm=0; weighted=1*0.75+0*0.25=0.75
        self.assertAlmostEqual(result.values[1], 0.75)

    def test_weighted_overlay_bad_weights(self):
        a = _make_layer({0: 1.0}, {0: []})
        with self.assertRaises(ValueError):
            weighted_overlay([a], [0.0])

    def test_weighted_overlay_mismatched(self):
        a = _make_layer({0: 1.0}, {0: []})
        with self.assertRaises(ValueError):
            weighted_overlay([a], [1.0, 2.0])

    def test_weighted_overlay_empty(self):
        with self.assertRaises(ValueError):
            weighted_overlay([], [])

    def test_layer_stack(self):
        a = _make_layer({0: 1.0, 1: 2.0, 2: 3.0}, {0: [1], 1: [0, 2], 2: [1]})
        b = _make_layer({0: 10.0, 1: 20.0}, {0: [1], 1: [0]})
        stacked = layer_stack([a, b])
        # Only common cells (0, 1)
        self.assertIn(0, stacked)
        self.assertIn(1, stacked)
        self.assertNotIn(2, stacked)
        self.assertEqual(stacked[0], [1.0, 10.0])

    def test_layer_stack_empty(self):
        self.assertEqual(layer_stack([]), {})


# ── Export ────────────────────────────────────────────────────────────

class TestExport(unittest.TestCase):
    def test_export_algebra_json(self):
        layer = _make_layer()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_algebra_json(layer, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("values", data)
        finally:
            os.unlink(path)

    def test_export_algebra_csv(self):
        layer = _make_layer()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            export_algebra_csv(layer, path)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(lines[0].strip(), "cell,value")
            self.assertEqual(len(lines), 6)  # header + 5 cells
        finally:
            os.unlink(path)

    def test_export_zonal_csv(self):
        results = {
            "A": ZonalResult(zone_id="A", count=2, total=30.0, mean=15.0),
            "B": ZonalResult(zone_id="B", count=3, total=120.0, mean=40.0),
        }
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            path = f.name
        try:
            export_zonal_csv(results, path)
            with open(path) as f:
                lines = f.readlines()
            self.assertEqual(lines[0].strip(), "zone_id,count,sum,mean,min,max,std,range,dominant")
            self.assertEqual(len(lines), 3)  # header + 2 zones
        finally:
            os.unlink(path)


# ── Report ───────────────────────────────────────────────────────────

class TestReport(unittest.TestCase):
    def test_format_report(self):
        layer = _make_layer()
        report = format_algebra_report(layer)
        self.assertIn("Layer: test", report)
        self.assertIn("Cells: 5", report)
        self.assertIn("Mean:", report)

    def test_format_report_detail(self):
        layer = _make_layer()
        report = format_algebra_report(layer, detail=True)
        self.assertIn("Cell values:", report)


# ── CLI ──────────────────────────────────────────────────────────────

class TestCLI(unittest.TestCase):
    def _write_layer(self, layer):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump(layer.to_dict(), f)
            return f.name

    def test_stats(self, ):
        path = self._write_layer(_make_layer())
        try:
            main(["stats", path])  # Should not raise
        finally:
            os.unlink(path)

    def test_stats_json(self, ):
        path = self._write_layer(_make_layer())
        try:
            main(["stats", path, "--json"])
        finally:
            os.unlink(path)

    def test_focal_mean_cli(self):
        layer = _make_layer()
        path = self._write_layer(layer)
        out = path + ".out.json"
        try:
            main(["focal-mean", path, "-o", out])
            loaded = CellLayer.from_json(out)
            self.assertEqual(len(loaded), 5)
        finally:
            os.unlink(path)
            if os.path.exists(out):
                os.unlink(out)

    def test_threshold_cli(self):
        path = self._write_layer(_make_layer())
        try:
            main(["threshold", path, "--value", "30"])
        finally:
            os.unlink(path)

    def test_reclassify_cli(self):
        path = self._write_layer(_make_layer())
        try:
            main(["reclassify", path, "--breaks", "20,40"])
        finally:
            os.unlink(path)

    def test_normalise_cli(self):
        path = self._write_layer(_make_layer())
        try:
            main(["normalise", path])
        finally:
            os.unlink(path)

    def test_no_command(self):
        main([])  # Should print help, not crash


if __name__ == "__main__":
    unittest.main()
