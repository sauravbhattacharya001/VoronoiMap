"""Tests for vormap_classify — spatial classification of Voronoi regions."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_classify import (
    classify_by_rules, classify_by_breaks, spatial_smooth,
    classify_multi_metric, ClassificationResult,
    export_classified_json, export_classified_svg,
    get_class_regions, reclassify, confusion_matrix, agreement_score,
    _extract_metric, _apply_op, _mean, _std,
    _natural_breaks, _equal_interval_breaks, _quantile_breaks,
    _std_dev_breaks,
)


def _make_stats(n=10, base_area=10000):
    """Create mock region stats for testing."""
    stats = []
    for i in range(n):
        area = base_area * (i + 1)
        stats.append({
            "area": area,
            "compactness": 0.3 + 0.05 * i,
            "perimeter": math.sqrt(area) * 4,
            "num_vertices": 4 + i % 4,
            "neighbors": list(range(max(0, i - 1), min(n, i + 2))),
            "centroid": (100.0 + i * 50, 200.0 + i * 30),
            "seed": (100.0 + i * 50, 200.0 + i * 30),
        })
    return stats


def _make_regions(n=10):
    """Create mock regions with neighbor info."""
    regions = []
    for i in range(n):
        neighbors = []
        if i > 0:
            neighbors.append(i)  # 1-based
        if i < n - 1:
            neighbors.append(i + 2)  # 1-based
        regions.append({
            "vertices": [(i * 10, 0), (i * 10 + 10, 0),
                         (i * 10 + 10, 10), (i * 10, 10)],
            "neighbors": neighbors,
        })
    return regions


class TestMetricExtraction(unittest.TestCase):

    def test_extract_area(self):
        stats = _make_stats(5)
        vals = _extract_metric(stats, "area")
        self.assertEqual(len(vals), 5)
        self.assertEqual(vals[0], 10000)
        self.assertEqual(vals[4], 50000)

    def test_extract_compactness(self):
        stats = _make_stats(3)
        vals = _extract_metric(stats, "compactness")
        self.assertEqual(len(vals), 3)
        self.assertAlmostEqual(vals[0], 0.3)

    def test_extract_perimeter(self):
        stats = _make_stats(3)
        vals = _extract_metric(stats, "perimeter")
        self.assertEqual(len(vals), 3)
        self.assertGreater(vals[0], 0)

    def test_extract_vertices(self):
        stats = _make_stats(4)
        vals = _extract_metric(stats, "vertices")
        self.assertTrue(all(isinstance(v, float) for v in vals))

    def test_extract_neighbors(self):
        stats = _make_stats(5)
        vals = _extract_metric(stats, "neighbors")
        self.assertEqual(len(vals), 5)

    def test_extract_centroid(self):
        stats = _make_stats(3)
        cx = _extract_metric(stats, "centroid_x")
        cy = _extract_metric(stats, "centroid_y")
        self.assertEqual(len(cx), 3)
        self.assertEqual(len(cy), 3)

    def test_extract_unknown_metric(self):
        stats = [{"custom_val": 42}]
        vals = _extract_metric(stats, "custom_val")
        self.assertEqual(vals[0], 42)

    def test_extract_empty(self):
        vals = _extract_metric([], "area")
        self.assertEqual(vals, [])


class TestOperators(unittest.TestCase):

    def test_greater_than(self):
        self.assertTrue(_apply_op(">", 10, 5))
        self.assertFalse(_apply_op(">", 5, 10))

    def test_less_than(self):
        self.assertTrue(_apply_op("<", 3, 7))
        self.assertFalse(_apply_op("<", 7, 3))

    def test_equal(self):
        self.assertTrue(_apply_op("==", 5.0, 5.0))
        self.assertFalse(_apply_op("==", 5.0, 6.0))

    def test_not_equal(self):
        self.assertTrue(_apply_op("!=", 5, 6))
        self.assertFalse(_apply_op("!=", 5.0, 5.0))

    def test_gte(self):
        self.assertTrue(_apply_op(">=", 5, 5))
        self.assertTrue(_apply_op(">=", 6, 5))

    def test_lte(self):
        self.assertTrue(_apply_op("<=", 5, 5))
        self.assertTrue(_apply_op("<=", 4, 5))

    def test_between(self):
        self.assertTrue(_apply_op("between", 5, [3, 7]))
        self.assertFalse(_apply_op("between", 10, [3, 7]))

    def test_unknown_op(self):
        with self.assertRaises(ValueError):
            _apply_op("~", 5, 3)


class TestHelpers(unittest.TestCase):

    def test_mean(self):
        self.assertAlmostEqual(_mean([1, 2, 3, 4, 5]), 3.0)
        self.assertEqual(_mean([]), 0.0)

    def test_std(self):
        self.assertAlmostEqual(_std([1, 1, 1, 1]), 0.0)
        self.assertGreater(_std([1, 2, 3, 4, 5]), 0)
        self.assertEqual(_std([5]), 0.0)
        self.assertEqual(_std([]), 0.0)


class TestRuleClassification(unittest.TestCase):

    def test_basic_rules(self):
        stats = _make_stats(5)
        rules = [
            {"label": "large", "metric": "area", "op": ">", "value": 30000},
            {"label": "small", "metric": "area", "op": "<", "value": 20000},
        ]
        result = classify_by_rules(stats, rules)
        self.assertEqual(len(result.labels), 5)
        self.assertEqual(result.labels[0], "small")  # 10000
        self.assertEqual(result.labels[3], "large")  # 40000

    def test_default_label(self):
        stats = _make_stats(3)
        rules = [
            {"label": "big", "metric": "area", "op": ">", "value": 100000},
        ]
        result = classify_by_rules(stats, rules, default_label="normal")
        self.assertTrue(all(lb == "normal" for lb in result.labels))

    def test_first_match_wins(self):
        stats = [{"area": 50000, "seed": (0, 0)}]
        rules = [
            {"label": "first", "metric": "area", "op": ">", "value": 10000},
            {"label": "second", "metric": "area", "op": ">", "value": 20000},
        ]
        result = classify_by_rules(stats, rules)
        self.assertEqual(result.labels[0], "first")

    def test_priority_ordering(self):
        stats = [{"area": 50000, "seed": (0, 0)}]
        rules = [
            {"label": "low_pri", "metric": "area", "op": ">", "value": 10000, "priority": 2},
            {"label": "high_pri", "metric": "area", "op": ">", "value": 10000, "priority": 1},
        ]
        result = classify_by_rules(stats, rules)
        self.assertEqual(result.labels[0], "high_pri")

    def test_empty_stats(self):
        result = classify_by_rules([], [])
        self.assertEqual(len(result.labels), 0)
        self.assertEqual(result.method, "rules")

    def test_result_has_summary(self):
        stats = _make_stats(5)
        rules = [{"label": "big", "metric": "area", "op": ">", "value": 30000}]
        result = classify_by_rules(stats, rules)
        self.assertIn("total_regions", result.summary)
        self.assertEqual(result.summary["total_regions"], 5)

    def test_between_operator(self):
        stats = [{"area": 15000, "seed": (0, 0)},
                 {"area": 25000, "seed": (0, 0)},
                 {"area": 35000, "seed": (0, 0)}]
        rules = [{"label": "mid", "metric": "area", "op": "between", "value": [10000, 30000]}]
        result = classify_by_rules(stats, rules)
        self.assertEqual(result.labels[0], "mid")
        self.assertEqual(result.labels[1], "mid")
        self.assertEqual(result.labels[2], "unclassified")

    def test_label_set_preserves_order(self):
        stats = _make_stats(5)
        rules = [
            {"label": "big", "metric": "area", "op": ">", "value": 40000},
            {"label": "small", "metric": "area", "op": "<", "value": 20000},
        ]
        result = classify_by_rules(stats, rules)
        # small should appear first in label_set since region 0 matches first
        self.assertEqual(result.label_set[0], "small")

    def test_region_details(self):
        stats = _make_stats(3)
        rules = [{"label": "x", "metric": "area", "op": ">", "value": 0}]
        result = classify_by_rules(stats, rules)
        self.assertEqual(len(result.region_details), 3)
        self.assertEqual(result.region_details[0]["region_index"], 1)


class TestBreakClassification(unittest.TestCase):

    def test_natural_breaks(self):
        stats = _make_stats(10)
        result = classify_by_breaks(stats, metric="area", n_classes=3, method="natural")
        self.assertEqual(len(result.labels), 10)
        self.assertGreater(len(result.label_set), 1)

    def test_equal_breaks(self):
        stats = _make_stats(10)
        result = classify_by_breaks(stats, metric="area", n_classes=4, method="equal")
        self.assertEqual(len(result.labels), 10)

    def test_quantile_breaks(self):
        stats = _make_stats(10)
        result = classify_by_breaks(stats, metric="area", n_classes=4, method="quantile")
        self.assertEqual(len(result.labels), 10)

    def test_stddev_breaks(self):
        stats = _make_stats(10)
        result = classify_by_breaks(stats, metric="area", n_classes=3, method="stddev")
        self.assertEqual(len(result.labels), 10)

    def test_custom_labels(self):
        stats = _make_stats(6)
        result = classify_by_breaks(stats, metric="area", n_classes=3,
                                    labels=["low", "medium", "high"])
        for lb in result.labels:
            self.assertIn(lb, ["low", "medium", "high"])

    def test_n_classes_too_small(self):
        with self.assertRaises(ValueError):
            classify_by_breaks(_make_stats(5), n_classes=1)

    def test_n_classes_too_large(self):
        with self.assertRaises(ValueError):
            classify_by_breaks(_make_stats(5), n_classes=11)

    def test_unknown_method(self):
        with self.assertRaises(ValueError):
            classify_by_breaks(_make_stats(5), method="unknown")

    def test_empty_stats(self):
        result = classify_by_breaks([], n_classes=3)
        self.assertEqual(len(result.labels), 0)

    def test_breaks_in_summary(self):
        stats = _make_stats(10)
        result = classify_by_breaks(stats, n_classes=3)
        self.assertIn("breaks", result.summary)
        self.assertIsInstance(result.summary["breaks"], list)

    def test_metric_value_in_details(self):
        stats = _make_stats(5)
        result = classify_by_breaks(stats, metric="area", n_classes=2)
        for d in result.region_details:
            self.assertIn("metric_value", d)

    def test_compactness_metric(self):
        stats = _make_stats(10)
        result = classify_by_breaks(stats, metric="compactness", n_classes=3)
        self.assertEqual(len(result.labels), 10)


class TestBreakAlgorithms(unittest.TestCase):

    def test_natural_breaks_basic(self):
        values = [1, 2, 3, 10, 11, 12, 20, 21, 22]
        breaks = _natural_breaks(values, 3)
        self.assertGreater(len(breaks), 0)
        self.assertLessEqual(len(breaks), 2)

    def test_natural_breaks_single_class(self):
        self.assertEqual(_natural_breaks([1, 2, 3], 1), [])

    def test_natural_breaks_empty(self):
        self.assertEqual(_natural_breaks([], 3), [])

    def test_equal_interval_basic(self):
        values = [0, 10, 20, 30, 40]
        breaks = _equal_interval_breaks(values, 4)
        self.assertEqual(len(breaks), 3)
        self.assertAlmostEqual(breaks[0], 10.0)

    def test_equal_interval_empty(self):
        self.assertEqual(_equal_interval_breaks([], 3), [])

    def test_equal_interval_single_class(self):
        self.assertEqual(_equal_interval_breaks([1, 2, 3], 1), [])

    def test_equal_interval_uniform(self):
        breaks = _equal_interval_breaks([5, 5, 5, 5], 3)
        self.assertEqual(breaks, [])

    def test_quantile_basic(self):
        values = list(range(100))
        breaks = _quantile_breaks(values, 4)
        self.assertGreater(len(breaks), 0)

    def test_quantile_empty(self):
        self.assertEqual(_quantile_breaks([], 3), [])

    def test_stddev_basic(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        breaks = _std_dev_breaks(values, 3)
        self.assertGreater(len(breaks), 0)

    def test_stddev_uniform(self):
        breaks = _std_dev_breaks([5, 5, 5, 5], 3)
        self.assertEqual(breaks, [])


class TestSpatialSmoothing(unittest.TestCase):

    def test_smooth_basic(self):
        stats = _make_stats(5)
        regions = _make_regions(5)
        rules = [
            {"label": "a", "metric": "area", "op": "<", "value": 25000},
            {"label": "b", "metric": "area", "op": ">=", "value": 25000},
        ]
        result = classify_by_rules(stats, rules)
        smoothed = spatial_smooth(result, regions, iterations=1)
        self.assertEqual(len(smoothed.labels), 5)

    def test_smooth_preserves_length(self):
        stats = _make_stats(8)
        regions = _make_regions(8)
        result = classify_by_breaks(stats, n_classes=3)
        smoothed = spatial_smooth(result, regions, iterations=2)
        self.assertEqual(len(smoothed.labels), len(result.labels))

    def test_smooth_empty(self):
        result = ClassificationResult()
        smoothed = spatial_smooth(result, [], iterations=1)
        self.assertEqual(len(smoothed.labels), 0)

    def test_smooth_method_name(self):
        stats = _make_stats(5)
        regions = _make_regions(5)
        result = classify_by_rules(stats, [{"label": "x", "metric": "area", "op": ">", "value": 0}])
        smoothed = spatial_smooth(result, regions, iterations=3)
        self.assertIn("smooth(3)", smoothed.method)

    def test_smooth_no_neighbors(self):
        stats = _make_stats(3)
        regions = [{"vertices": [], "neighbors": []} for _ in range(3)]
        result = classify_by_rules(stats,
                                   [{"label": "x", "metric": "area", "op": ">", "value": 0}])
        smoothed = spatial_smooth(result, regions, iterations=1)
        self.assertEqual(smoothed.labels, result.labels)


class TestMultiMetric(unittest.TestCase):

    def test_basic(self):
        stats = _make_stats(10)
        result = classify_multi_metric(stats,
                                       {"area": 0.7, "compactness": 0.3},
                                       n_classes=3)
        self.assertEqual(len(result.labels), 10)
        self.assertIn("metric_weights", result.summary)

    def test_empty(self):
        result = classify_multi_metric([], {"area": 1.0})
        self.assertEqual(len(result.labels), 0)

    def test_no_weights(self):
        result = classify_multi_metric(_make_stats(5), {})
        self.assertEqual(len(result.labels), 0)

    def test_single_metric(self):
        stats = _make_stats(5)
        result = classify_multi_metric(stats, {"area": 1.0}, n_classes=2)
        self.assertEqual(len(result.labels), 5)

    def test_combined_score_in_details(self):
        stats = _make_stats(5)
        result = classify_multi_metric(stats, {"area": 1.0}, n_classes=2)
        for d in result.region_details:
            self.assertIn("combined_score", d)
            self.assertGreaterEqual(d["combined_score"], 0)
            self.assertLessEqual(d["combined_score"], 1)


class TestReclassify(unittest.TestCase):

    def test_basic(self):
        stats = _make_stats(4)
        result = classify_by_rules(stats,
                                   [{"label": "old", "metric": "area", "op": ">", "value": 0}])
        remapped = reclassify(result, {"old": "new"})
        self.assertTrue(all(lb == "new" for lb in remapped.labels))

    def test_partial_remap(self):
        stats = _make_stats(5)
        rules = [
            {"label": "a", "metric": "area", "op": "<", "value": 25000},
            {"label": "b", "metric": "area", "op": ">=", "value": 25000},
        ]
        result = classify_by_rules(stats, rules)
        remapped = reclassify(result, {"a": "alpha"})
        self.assertNotIn("a", remapped.labels)
        self.assertIn("alpha", remapped.labels)
        self.assertIn("b", remapped.labels)

    def test_method_name(self):
        result = ClassificationResult(labels=["x"], label_set=["x"],
                                       region_details=[{"label": "x"}],
                                       method="test")
        remapped = reclassify(result, {"x": "y"})
        self.assertIn("reclassified", remapped.method)


class TestConfusionMatrix(unittest.TestCase):

    def test_identical(self):
        r = ClassificationResult(labels=["a", "b", "a"])
        matrix = confusion_matrix(r, r)
        self.assertEqual(matrix[("a", "a")], 2)
        self.assertEqual(matrix[("b", "b")], 1)

    def test_different(self):
        r1 = ClassificationResult(labels=["a", "a", "b"])
        r2 = ClassificationResult(labels=["a", "b", "b"])
        matrix = confusion_matrix(r1, r2)
        self.assertEqual(matrix[("a", "a")], 1)
        self.assertEqual(matrix[("a", "b")], 1)
        self.assertEqual(matrix[("b", "b")], 1)


class TestAgreementScore(unittest.TestCase):

    def test_perfect_agreement(self):
        r = ClassificationResult(labels=["a", "b", "c"])
        self.assertEqual(agreement_score(r, r), 100.0)

    def test_no_agreement(self):
        r1 = ClassificationResult(labels=["a", "a", "a"])
        r2 = ClassificationResult(labels=["b", "b", "b"])
        self.assertEqual(agreement_score(r1, r2), 0.0)

    def test_partial_agreement(self):
        r1 = ClassificationResult(labels=["a", "b", "c", "d"])
        r2 = ClassificationResult(labels=["a", "b", "x", "y"])
        self.assertEqual(agreement_score(r1, r2), 50.0)

    def test_empty(self):
        r = ClassificationResult(labels=[])
        self.assertEqual(agreement_score(r, r), 0.0)


class TestGetClassRegions(unittest.TestCase):

    def test_basic(self):
        result = ClassificationResult(labels=["a", "b", "a", "c", "a"])
        indices = get_class_regions(result, "a")
        self.assertEqual(indices, [1, 3, 5])

    def test_no_match(self):
        result = ClassificationResult(labels=["a", "b"])
        self.assertEqual(get_class_regions(result, "z"), [])


class TestExportJSON(unittest.TestCase):

    def test_export(self):
        stats = _make_stats(5)
        result = classify_by_rules(stats,
                                   [{"label": "x", "metric": "area", "op": ">", "value": 0}])
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            export_classified_json(result, path)
            with open(path) as f:
                data = json.load(f)
            self.assertIn("method", data)
            self.assertIn("regions", data)
            self.assertEqual(len(data["regions"]), 5)
        finally:
            os.unlink(path)


class TestExportSVG(unittest.TestCase):

    def test_export(self):
        stats = _make_stats(4)
        regions = _make_regions(4)
        result = classify_by_rules(stats,
                                   [{"label": "x", "metric": "area", "op": ">", "value": 0}])
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
            path = f.name
        try:
            export_classified_svg(result, regions, stats, path)
            with open(path) as f:
                content = f.read()
            self.assertIn("<svg", content)
            self.assertIn("polygon", content)
        finally:
            os.unlink(path)

    def test_empty_regions(self):
        result = ClassificationResult(labels=[], label_set=[])
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
            path = f.name
        try:
            export_classified_svg(result, [], [], path)
            self.assertTrue(os.path.exists(path))
        finally:
            os.unlink(path)

    def test_custom_palette(self):
        stats = _make_stats(3)
        regions = _make_regions(3)
        result = classify_by_rules(stats,
                                   [{"label": "x", "metric": "area", "op": ">", "value": 0}])
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False, mode="w") as f:
            path = f.name
        try:
            export_classified_svg(result, regions, stats, path,
                                  palette=["#FF0000"])
            with open(path) as f:
                content = f.read()
            self.assertIn("#FF0000", content)
        finally:
            os.unlink(path)


class TestClassificationResult(unittest.TestCase):

    def test_defaults(self):
        r = ClassificationResult()
        self.assertEqual(r.labels, [])
        self.assertEqual(r.label_set, [])
        self.assertEqual(r.method, "")

    def test_with_data(self):
        r = ClassificationResult(
            labels=["a", "b"],
            label_set=["a", "b"],
            method="test",
        )
        self.assertEqual(len(r.labels), 2)

    def test_config_default(self):
        r = ClassificationResult()
        self.assertEqual(r.config, {})


class TestEndToEnd(unittest.TestCase):

    def test_rules_then_smooth(self):
        stats = _make_stats(10)
        regions = _make_regions(10)
        rules = [
            {"label": "dense", "metric": "area", "op": "<", "value": 30000},
            {"label": "sparse", "metric": "area", "op": ">=", "value": 30000},
        ]
        result = classify_by_rules(stats, rules)
        smoothed = spatial_smooth(result, regions, iterations=2)
        self.assertEqual(len(smoothed.labels), 10)
        for lb in smoothed.labels:
            self.assertIn(lb, ["dense", "sparse", "unclassified"])

    def test_breaks_then_reclassify(self):
        stats = _make_stats(8)
        result = classify_by_breaks(stats, n_classes=4)
        remapped = reclassify(result, {"class_1": "low", "class_4": "high"})
        self.assertIn("low", remapped.labels)

    def test_two_methods_agreement(self):
        stats = _make_stats(10)
        r1 = classify_by_breaks(stats, metric="area", n_classes=2, method="equal")
        r2 = classify_by_breaks(stats, metric="area", n_classes=2, method="quantile")
        score = agreement_score(r1, r2)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_confusion_matrix_integration(self):
        stats = _make_stats(10)
        r1 = classify_by_breaks(stats, n_classes=3, method="natural")
        r2 = classify_by_breaks(stats, n_classes=3, method="equal")
        matrix = confusion_matrix(r1, r2)
        total = sum(matrix.values())
        self.assertEqual(total, 10)


if __name__ == "__main__":
    unittest.main()
