"""Tests for vormap_autocorr — Spatial autocorrelation (Moran's I)."""

import json
import math
import os
import tempfile
import unittest

from vormap_autocorr import (
    GlobalMoranResult,
    LISACell,
    LISAResult,
    _build_adjacency,
    _extract_metric_values,
    _mean,
    _row_standardize_weights,
    _std_pop,
    _z_to_p_two_tailed,
    export_autocorr_json,
    export_lisa_svg,
    format_global_report,
    format_lisa_summary,
    global_morans_i,
    local_morans_i,
)


class TestHelpers(unittest.TestCase):
    """Tests for internal helper functions."""

    def test_mean_empty(self):
        self.assertEqual(_mean([]), 0.0)

    def test_mean_single(self):
        self.assertEqual(_mean([5.0]), 5.0)

    def test_mean_multiple(self):
        self.assertAlmostEqual(_mean([2, 4, 6]), 4.0)

    def test_std_pop_empty(self):
        self.assertEqual(_std_pop([]), 0.0)

    def test_std_pop_single(self):
        self.assertEqual(_std_pop([42]), 0.0)

    def test_std_pop_uniform(self):
        self.assertAlmostEqual(_std_pop([5, 5, 5, 5]), 0.0)

    def test_std_pop_known(self):
        # population std of [2, 4, 6] = sqrt(8/3) ≈ 1.633
        self.assertAlmostEqual(_std_pop([2, 4, 6]), math.sqrt(8 / 3), places=5)

    def test_z_to_p_zero(self):
        # z=0 → p=1.0
        self.assertAlmostEqual(_z_to_p_two_tailed(0), 1.0)

    def test_z_to_p_large(self):
        # z=3.0 → p ≈ 0.0027
        p = _z_to_p_two_tailed(3.0)
        self.assertAlmostEqual(p, 0.0027, places=3)

    def test_z_to_p_symmetric(self):
        self.assertAlmostEqual(
            _z_to_p_two_tailed(2.0), _z_to_p_two_tailed(-2.0)
        )


class TestExtractMetric(unittest.TestCase):
    """Tests for _extract_metric_values."""

    def test_area(self):
        stats = [{"area": 100}, {"area": 200}, {"area": 150}]
        vals = _extract_metric_values(stats, "area")
        self.assertEqual(vals, [100.0, 200.0, 150.0])

    def test_density(self):
        stats = [{"density": 0.5}, {"density": 1.5}]
        vals = _extract_metric_values(stats, "density")
        self.assertEqual(vals, [0.5, 1.5])

    def test_missing_defaults_zero(self):
        stats = [{"area": 100}, {}]
        vals = _extract_metric_values(stats, "area")
        self.assertEqual(vals, [100.0, 0.0])

    def test_none_defaults_zero(self):
        stats = [{"area": None}]
        vals = _extract_metric_values(stats, "area")
        self.assertEqual(vals, [0.0])

    def test_invalid_metric_raises(self):
        with self.assertRaises(ValueError):
            _extract_metric_values([], "invalid_metric")

    def test_all_valid_metrics(self):
        stats = [{"area": 1, "density": 2, "compactness": 3, "perimeter": 4}]
        for m in ("area", "density", "compactness", "perimeter"):
            vals = _extract_metric_values(stats, m)
            self.assertEqual(len(vals), 1)


class TestBuildAdjacency(unittest.TestCase):
    """Tests for _build_adjacency."""

    def _square_regions(self):
        """Two adjacent squares sharing an edge."""
        return [
            [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
            [{"x": 1, "y": 0}, {"x": 2, "y": 0}, {"x": 2, "y": 1}, {"x": 1, "y": 1}],
        ]

    def test_adjacent_squares(self):
        regions = self._square_regions()
        data = [(0.5, 0.5), (1.5, 0.5)]
        adj = _build_adjacency(regions, data)
        self.assertIn(1, adj[0])
        self.assertIn(0, adj[1])

    def test_non_adjacent(self):
        regions = [
            [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
            [{"x": 5, "y": 5}, {"x": 6, "y": 5}, {"x": 6, "y": 6}, {"x": 5, "y": 6}],
        ]
        data = [(0.5, 0.5), (5.5, 5.5)]
        adj = _build_adjacency(regions, data)
        self.assertEqual(adj[0], [])
        self.assertEqual(adj[1], [])

    def test_tuple_vertices(self):
        regions = [
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            [(1, 0), (2, 0), (2, 1), (1, 1)],
        ]
        data = [(0.5, 0.5), (1.5, 0.5)]
        adj = _build_adjacency(regions, data)
        self.assertIn(1, adj[0])

    def test_three_cells_chain(self):
        # Three cells in a row: 0-1 adjacent, 1-2 adjacent, 0-2 not
        regions = [
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            [(1, 0), (2, 0), (2, 1), (1, 1)],
            [(2, 0), (3, 0), (3, 1), (2, 1)],
        ]
        data = [(0.5, 0.5), (1.5, 0.5), (2.5, 0.5)]
        adj = _build_adjacency(regions, data)
        self.assertIn(1, adj[0])
        self.assertNotIn(2, adj[0])
        self.assertIn(0, adj[1])
        self.assertIn(2, adj[1])


class TestRowStandardize(unittest.TestCase):
    """Tests for _row_standardize_weights."""

    def test_equal_weights(self):
        adj = {0: [1, 2], 1: [0], 2: [0]}
        w = _row_standardize_weights(adj, 3)
        self.assertAlmostEqual(w[0][1], 0.5)
        self.assertAlmostEqual(w[0][2], 0.5)
        self.assertAlmostEqual(w[1][0], 1.0)

    def test_isolate(self):
        adj = {0: [], 1: []}
        w = _row_standardize_weights(adj, 2)
        self.assertEqual(w[0], {})
        self.assertEqual(w[1], {})

    def test_row_sums_to_one(self):
        adj = {0: [1, 2, 3], 1: [0], 2: [0], 3: [0]}
        w = _row_standardize_weights(adj, 4)
        self.assertAlmostEqual(sum(w[0].values()), 1.0)


class TestGlobalMoransI(unittest.TestCase):
    """Tests for global_morans_i."""

    def _grid_regions(self):
        """2x2 grid of unit squares."""
        return [
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            [(1, 0), (2, 0), (2, 1), (1, 1)],
            [(0, 1), (1, 1), (1, 2), (0, 2)],
            [(1, 1), (2, 1), (2, 2), (1, 2)],
        ]

    def _grid_data(self):
        return [(0.5, 0.5), (1.5, 0.5), (0.5, 1.5), (1.5, 1.5)]

    def test_clustered_pattern(self):
        """Similar values near each other → positive I."""
        regions = self._grid_regions()
        data = self._grid_data()
        values = [100, 100, 1, 1]  # top-bottom clustering
        result = global_morans_i(values, regions, data)
        self.assertGreater(result.I, result.expected_I)
        self.assertEqual(result.n, 4)
        self.assertGreater(result.num_pairs, 0)

    def test_dispersed_pattern(self):
        """Dissimilar values near each other → negative I."""
        regions = self._grid_regions()
        data = self._grid_data()
        values = [100, 1, 1, 100]  # checkerboard
        result = global_morans_i(values, regions, data)
        self.assertLess(result.I, result.expected_I)

    def test_identical_values(self):
        """No variation → I = 0, interpretation random."""
        regions = self._grid_regions()
        data = self._grid_data()
        values = [50, 50, 50, 50]
        result = global_morans_i(values, regions, data)
        self.assertEqual(result.I, 0.0)
        self.assertEqual(result.interpretation, "random")

    def test_too_few_observations(self):
        with self.assertRaises(ValueError):
            global_morans_i([1, 2], [[], []], [(0, 0), (1, 1)])

    def test_length_mismatch(self):
        regions = self._grid_regions()
        data = self._grid_data()
        with self.assertRaises(ValueError):
            global_morans_i([1, 2, 3], regions, data)

    def test_result_fields(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = global_morans_i([10, 20, 30, 40], regions, data)
        self.assertIsInstance(result, GlobalMoranResult)
        self.assertEqual(result.n, 4)
        self.assertIsInstance(result.I, float)
        self.assertIsInstance(result.z, float)
        self.assertIsInstance(result.p, float)
        self.assertIn(result.interpretation, ("clustered", "dispersed", "random"))

    def test_expected_I_formula(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = global_morans_i([10, 20, 30, 40], regions, data)
        self.assertAlmostEqual(result.expected_I, -1.0 / 3)

    def test_p_value_range(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = global_morans_i([10, 20, 30, 40], regions, data)
        self.assertGreaterEqual(result.p, 0.0)
        self.assertLessEqual(result.p, 1.0)

    def test_metric_label(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = global_morans_i([1, 2, 3, 4], regions, data, metric="density")
        self.assertEqual(result.metric, "density")


class TestLocalMoransI(unittest.TestCase):
    """Tests for local_morans_i (LISA)."""

    def _grid_regions(self):
        return [
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            [(1, 0), (2, 0), (2, 1), (1, 1)],
            [(0, 1), (1, 1), (1, 2), (0, 2)],
            [(1, 1), (2, 1), (2, 2), (1, 2)],
        ]

    def _grid_data(self):
        return [(0.5, 0.5), (1.5, 0.5), (0.5, 1.5), (1.5, 1.5)]

    def test_result_structure(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = local_morans_i([10, 20, 30, 40], regions, data, permutations=99)
        self.assertIsInstance(result, LISAResult)
        self.assertEqual(len(result.cells), 4)
        for cell in result.cells:
            self.assertIsInstance(cell, LISACell)
            self.assertIn(cell.cluster_type, ("HH", "LL", "HL", "LH", "NS"))
            self.assertGreaterEqual(cell.p, 0.0)
            self.assertLessEqual(cell.p, 1.0)

    def test_counts_sum_to_n(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = local_morans_i([10, 50, 10, 50], regions, data, permutations=99)
        total = sum(result.counts.values())
        self.assertEqual(total, 4)

    def test_identical_values_all_ns(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = local_morans_i([5, 5, 5, 5], regions, data)
        for cell in result.cells:
            self.assertEqual(cell.cluster_type, "NS")

    def test_too_few_raises(self):
        with self.assertRaises(ValueError):
            local_morans_i([1, 2], [[], []], [(0, 0), (1, 1)])

    def test_length_mismatch_raises(self):
        regions = self._grid_regions()
        data = self._grid_data()
        with self.assertRaises(ValueError):
            local_morans_i([1, 2, 3], regions, data)

    def test_cell_coordinates(self):
        regions = self._grid_regions()
        data = self._grid_data()
        result = local_morans_i([10, 20, 30, 40], regions, data, permutations=99)
        self.assertAlmostEqual(result.cells[0].x, 0.5)
        self.assertAlmostEqual(result.cells[0].y, 0.5)

    def test_deterministic_with_seed(self):
        """Two runs with same data should produce same results (seeded RNG)."""
        regions = self._grid_regions()
        data = self._grid_data()
        vals = [10, 80, 20, 70]
        r1 = local_morans_i(vals, regions, data, permutations=99)
        r2 = local_morans_i(vals, regions, data, permutations=99)
        for c1, c2 in zip(r1.cells, r2.cells):
            self.assertAlmostEqual(c1.Ii, c2.Ii)
            self.assertAlmostEqual(c1.p, c2.p)

    def test_custom_significance(self):
        regions = self._grid_regions()
        data = self._grid_data()
        # With very strict significance, more cells should be NS
        r_strict = local_morans_i(
            [100, 1, 100, 1], regions, data,
            permutations=99, significance=0.001)
        r_loose = local_morans_i(
            [100, 1, 100, 1], regions, data,
            permutations=99, significance=0.50)
        self.assertGreaterEqual(
            r_strict.counts["NS"], r_loose.counts["NS"])

    def test_global_I_from_local(self):
        """Global I from local decomposition should be finite."""
        regions = self._grid_regions()
        data = self._grid_data()
        result = local_morans_i([10, 20, 30, 40], regions, data, permutations=99)
        self.assertTrue(math.isfinite(result.global_I))

    def test_dict_data_points(self):
        """Data as list of dicts should work."""
        regions = self._grid_regions()
        data = [{"x": 0.5, "y": 0.5}, {"x": 1.5, "y": 0.5},
                {"x": 0.5, "y": 1.5}, {"x": 1.5, "y": 1.5}]
        result = local_morans_i([10, 20, 30, 40], regions, data, permutations=99)
        self.assertEqual(len(result.cells), 4)
        self.assertAlmostEqual(result.cells[0].x, 0.5)


class TestFormatting(unittest.TestCase):
    """Tests for report formatting functions."""

    def test_format_global_report(self):
        result = GlobalMoranResult(
            I=0.45, expected_I=-0.1, variance=0.01,
            z=5.5, p=0.0001, n=10, num_pairs=20,
            interpretation="clustered", metric="area")
        text = format_global_report(result)
        self.assertIn("Moran's I", text)
        self.assertIn("0.450000", text)
        self.assertIn("CLUSTERED", text)
        self.assertIn("area", text)

    def test_format_global_dispersed(self):
        result = GlobalMoranResult(
            I=-0.8, expected_I=-0.1, variance=0.01,
            z=-7.0, p=0.0001, n=10, num_pairs=20,
            interpretation="dispersed", metric="density")
        text = format_global_report(result)
        self.assertIn("DISPERSED", text)

    def test_format_global_random(self):
        result = GlobalMoranResult(
            I=-0.05, expected_I=-0.1, variance=0.01,
            z=0.5, p=0.6, n=10, num_pairs=20,
            interpretation="random", metric="area")
        text = format_global_report(result)
        self.assertIn("RANDOM", text)

    def test_format_lisa_summary(self):
        result = LISAResult(
            cells=[], global_I=0.3, metric="area",
            significance_level=0.05, num_permutations=999,
            counts={"HH": 5, "LL": 3, "HL": 1, "LH": 1, "NS": 10})
        text = format_lisa_summary(result)
        self.assertIn("LISA", text)
        self.assertIn("Hot-Spot", text)
        self.assertIn("Cold-Spot", text)
        self.assertIn("5", text)  # HH count


class TestExportJSON(unittest.TestCase):
    """Tests for export_autocorr_json."""

    def test_export_global_only(self):
        result = GlobalMoranResult(
            I=0.3, expected_I=-0.1, variance=0.005,
            z=5.6, p=0.0001, n=20, num_pairs=40,
            interpretation="clustered", metric="area")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_autocorr_json(result, None, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("global_morans_i", data)
            self.assertNotIn("lisa", data)
            self.assertAlmostEqual(data["global_morans_i"]["I"], 0.3, places=1)
        finally:
            os.unlink(path)

    def test_export_with_lisa(self):
        global_r = GlobalMoranResult(
            I=0.3, expected_I=-0.1, variance=0.005,
            z=5.6, p=0.0001, n=3, num_pairs=3,
            interpretation="clustered", metric="area")
        lisa_r = LISAResult(
            cells=[
                LISACell(0, 1.0, 2.0, 100, 1.5, 0.8, "HH", 0.01, 2),
                LISACell(1, 3.0, 4.0, 10, -1.2, 0.5, "LL", 0.03, 1),
                LISACell(2, 5.0, 6.0, 50, 0.1, 0.01, "NS", 0.5, 2),
            ],
            global_I=0.3, metric="area",
            counts={"HH": 1, "LL": 1, "HL": 0, "LH": 0, "NS": 1})
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_autocorr_json(global_r, lisa_r, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("lisa", data)
            self.assertEqual(len(data["lisa"]["cells"]), 3)
            self.assertEqual(data["lisa"]["cells"][0]["cluster_type"], "HH")
        finally:
            os.unlink(path)


class TestExportLISASVG(unittest.TestCase):
    """Tests for export_lisa_svg."""

    def test_produces_valid_svg(self):
        regions = [
            [(0, 0), (1, 0), (1, 1), (0, 1)],
            [(1, 0), (2, 0), (2, 1), (1, 1)],
            [(0, 1), (1, 1), (1, 2), (0, 2)],
            [(1, 1), (2, 1), (2, 2), (1, 2)],
        ]
        data = [(0.5, 0.5), (1.5, 0.5), (0.5, 1.5), (1.5, 1.5)]
        lisa = LISAResult(
            cells=[
                LISACell(0, 0.5, 0.5, 100, 1.5, 0.8, "HH", 0.01, 2),
                LISACell(1, 1.5, 0.5, 10, -1.2, 0.5, "LL", 0.03, 2),
                LISACell(2, 0.5, 1.5, 80, 1.0, 0.3, "HL", 0.02, 2),
                LISACell(3, 1.5, 1.5, 20, -0.8, 0.2, "LH", 0.04, 2),
            ],
            global_I=0.3, metric="area",
            counts={"HH": 1, "LL": 1, "HL": 1, "LH": 1, "NS": 0})

        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_lisa_svg(lisa, regions, data, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("polygon", content)
            self.assertIn("#e31a1c", content)  # HH color
            self.assertIn("#2c7bb6", content)  # LL color
        finally:
            os.unlink(path)

    def test_svg_with_dict_data(self):
        regions = [
            [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
        ]
        data = [{"x": 0.5, "y": 0.5}]
        lisa = LISAResult(
            cells=[LISACell(0, 0.5, 0.5, 50, 0.0, 0.0, "NS", 0.5, 0)],
            global_I=0.0, metric="area",
            counts={"HH": 0, "LL": 0, "HL": 0, "LH": 0, "NS": 1})

        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_lisa_svg(lisa, regions, data, path)
            self.assertTrue(os.path.exists(path))
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
        finally:
            os.unlink(path)


class TestDataclasses(unittest.TestCase):
    """Tests for result dataclass defaults and fields."""

    def test_global_result_defaults(self):
        r = GlobalMoranResult(
            I=0.0, expected_I=0.0, variance=0.0,
            z=0.0, p=1.0, n=0, num_pairs=0,
            interpretation="random")
        self.assertEqual(r.metric, "area")

    def test_lisa_cell_fields(self):
        c = LISACell(0, 1.0, 2.0, 50.0, 0.5, 0.3, "HH", 0.01, 3)
        self.assertEqual(c.index, 0)
        self.assertEqual(c.cluster_type, "HH")
        self.assertEqual(c.num_neighbors, 3)

    def test_lisa_result_defaults(self):
        r = LISAResult()
        self.assertEqual(r.cells, [])
        self.assertEqual(r.global_I, 0.0)
        self.assertEqual(r.metric, "area")
        self.assertEqual(r.significance_level, 0.05)
        self.assertEqual(r.num_permutations, 999)


if __name__ == "__main__":
    unittest.main()
