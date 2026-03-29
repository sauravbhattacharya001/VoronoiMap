"""Tests for vormap_classify module."""

import csv
import json
import os
import tempfile
import unittest
from pathlib import Path

import vormap_classify


class TestValidation(unittest.TestCase):
    def test_empty_values(self):
        with self.assertRaises(ValueError):
            vormap_classify.classify([], method="jenks")

    def test_none_values_filtered(self):
        result = vormap_classify.classify([1, None, 3, None, 5], n_classes=2)
        self.assertEqual(len(result.assignments), 3)

    def test_nan_filtered(self):
        result = vormap_classify.classify([1.0, float('nan'), 3.0], n_classes=2)
        self.assertEqual(len(result.assignments), 2)

    def test_invalid_method(self):
        with self.assertRaises(ValueError):
            vormap_classify.classify([1, 2, 3], method="bogus")

    def test_n_classes_below_two(self):
        with self.assertRaises(ValueError):
            vormap_classify.classify([1, 2, 3], n_classes=1)


class TestJenks(unittest.TestCase):
    def test_basic(self):
        values = [1, 2, 3, 10, 11, 12, 20, 21, 22]
        result = vormap_classify.classify(values, n_classes=3, method="jenks")
        self.assertEqual(result.method, "jenks")
        self.assertEqual(result.n_classes, 3)
        self.assertEqual(len(result.breaks), 2)
        self.assertIsNotNone(result.gvf)
        self.assertGreater(result.gvf, 0.8)

    def test_two_classes(self):
        values = [1, 2, 3, 100, 200, 300]
        result = vormap_classify.classify(values, n_classes=2, method="jenks")
        self.assertEqual(result.n_classes, 2)
        self.assertEqual(len(result.breaks), 1)

    def test_uniform_data(self):
        values = [5, 5, 5, 5, 5]
        result = vormap_classify.classify(values, n_classes=3, method="jenks")
        self.assertAlmostEqual(result.gvf, 1.0, places=2)

    def test_more_classes_than_values(self):
        values = [1, 2]
        result = vormap_classify.classify(values, n_classes=5, method="jenks")
        self.assertGreaterEqual(result.n_classes, 1)

    def test_gvf_range(self):
        values = list(range(100))
        result = vormap_classify.classify(values, n_classes=5, method="jenks")
        self.assertGreaterEqual(result.gvf, 0.0)
        self.assertLessEqual(result.gvf, 1.0)


class TestQuantile(unittest.TestCase):
    def test_equal_count(self):
        values = list(range(1, 13))
        result = vormap_classify.classify(values, n_classes=4, method="quantile")
        self.assertEqual(result.method, "quantile")
        counts = [s["count"] for s in result.class_summaries]
        self.assertTrue(all(c >= 2 for c in counts))

    def test_three_classes(self):
        values = list(range(30))
        result = vormap_classify.classify(values, n_classes=3, method="quantile")
        self.assertEqual(len(result.breaks), 2)

    def test_single_value_repeated(self):
        values = [7, 7, 7, 7, 7]
        result = vormap_classify.classify(values, n_classes=3, method="quantile")
        self.assertGreaterEqual(result.n_classes, 1)


class TestEqualInterval(unittest.TestCase):
    def test_basic(self):
        values = list(range(0, 100))
        result = vormap_classify.classify(values, n_classes=5, method="equal_interval")
        self.assertEqual(result.method, "equal_interval")
        for i in range(len(result.breaks) - 1):
            gap = result.breaks[i + 1] - result.breaks[i]
            self.assertAlmostEqual(gap, result.breaks[1] - result.breaks[0], places=6)

    def test_uniform_values(self):
        values = [10, 10, 10]
        result = vormap_classify.classify(values, n_classes=3, method="equal_interval")
        self.assertEqual(len(result.breaks), 0)


class TestStdDev(unittest.TestCase):
    def test_basic(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90]
        result = vormap_classify.classify(values, n_classes=5, method="stddev")
        self.assertEqual(result.method, "stddev")
        self.assertGreater(len(result.breaks), 0)

    def test_zero_variance(self):
        values = [42, 42, 42]
        result = vormap_classify.classify(values, n_classes=3, method="stddev")
        self.assertEqual(len(result.breaks), 0)


class TestHeadTail(unittest.TestCase):
    def test_power_law(self):
        values = [1] * 100 + [10] * 20 + [100] * 5 + [1000] * 1
        result = vormap_classify.classify(values, method="headtail")
        self.assertEqual(result.method, "headtail")
        self.assertGreater(len(result.breaks), 0)

    def test_uniform_data(self):
        values = list(range(100))
        result = vormap_classify.classify(values, method="headtail")
        self.assertGreaterEqual(result.n_classes, 1)


class TestPretty(unittest.TestCase):
    def test_basic(self):
        values = list(range(1, 101))
        result = vormap_classify.classify(values, n_classes=5, method="pretty")
        self.assertEqual(result.method, "pretty")
        for bp in result.breaks:
            self.assertTrue(bp == int(bp) or bp * 10 == int(bp * 10))

    def test_small_range(self):
        values = [0.1, 0.3, 0.5, 0.7, 0.9]
        result = vormap_classify.classify(values, n_classes=3, method="pretty")
        self.assertGreater(len(result.breaks), 0)


class TestManual(unittest.TestCase):
    def test_basic(self):
        values = [1, 5, 10, 15, 20]
        result = vormap_classify.classify(values, method="manual", breaks=[5, 15])
        self.assertEqual(result.method, "manual")
        self.assertEqual(result.n_classes, 3)
        self.assertEqual(result.breaks, [5, 15])

    def test_no_breaks_raises(self):
        with self.assertRaises(ValueError):
            vormap_classify.classify([1, 2, 3], method="manual")

    def test_assignments(self):
        values = [1, 10, 20, 30]
        result = vormap_classify.classify(values, method="manual", breaks=[10, 25])
        self.assertEqual(result.assignments[0], 0)
        self.assertEqual(result.assignments[1], 1)
        self.assertEqual(result.assignments[2], 1)
        self.assertEqual(result.assignments[3], 2)


class TestClassificationResult(unittest.TestCase):
    def test_label_for(self):
        result = vormap_classify.classify(list(range(100)), n_classes=3, method="equal_interval")
        label1 = result.label_for(1)
        self.assertTrue("\u2013" in label1 or "-" in label1)
        self.assertIn("<", result.label_for(0))

    def test_label_out_of_range(self):
        result = vormap_classify.classify([1, 2, 3], n_classes=2)
        with self.assertRaises(ValueError):
            result.label_for(5)

    def test_summaries(self):
        values = [1, 2, 3, 10, 20, 30]
        result = vormap_classify.classify(values, n_classes=2, method="jenks")
        total_count = sum(s["count"] for s in result.class_summaries)
        self.assertEqual(total_count, len(values))


class TestCompareMethods(unittest.TestCase):
    def test_all_methods(self):
        values = list(range(1, 51))
        results = vormap_classify.compare_methods(values, n_classes=4)
        self.assertIn("jenks", results)
        self.assertIn("quantile", results)
        self.assertNotIn("manual", results)

    def test_specific_methods(self):
        values = list(range(1, 51))
        results = vormap_classify.compare_methods(values, n_classes=3, methods=["jenks", "quantile"])
        self.assertEqual(len(results), 2)


class TestSVG(unittest.TestCase):
    def test_generates_svg(self):
        values = list(range(100))
        result = vormap_classify.classify(values, n_classes=4)
        svg = vormap_classify._classification_svg(result, values)
        self.assertIn("<svg", svg)
        self.assertIn("</svg>", svg)
        self.assertIn("jenks", svg)


class TestExportCSV(unittest.TestCase):
    def test_export(self):
        values = [1, 5, 10, 20, 50]
        result = vormap_classify.classify(values, n_classes=3)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "out.csv")
            vormap_classify.export_csv(values, result, path)
            with open(path) as f:
                reader = csv.reader(f)
                header = next(reader)
                self.assertEqual(header, ["value", "class", "label"])
                rows = list(reader)
                self.assertEqual(len(rows), 5)


class TestCLI(unittest.TestCase):
    def test_text_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = os.path.join(tmpdir, "vals.txt")
            with open(data_file, "w") as f:
                for v in range(1, 21):
                    f.write(f"{v}\n")
            vormap_classify.main([data_file, "--method", "quantile", "--classes", "4"])

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = os.path.join(tmpdir, "vals.txt")
            with open(data_file, "w") as f:
                for v in [10, 20, 30, 40, 50]:
                    f.write(f"{v}\n")
            vormap_classify.main([data_file, "--json"])

    def test_csv_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = os.path.join(tmpdir, "data.csv")
            with open(data_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["name", "population"])
                for i in range(20):
                    writer.writerow([f"city_{i}", (i + 1) * 1000])
            vormap_classify.main([data_file, "--column", "population", "--classes", "3"])

    def test_svg_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = os.path.join(tmpdir, "vals.txt")
            svg_file = os.path.join(tmpdir, "hist.svg")
            with open(data_file, "w") as f:
                for v in range(1, 51):
                    f.write(f"{v}\n")
            vormap_classify.main([data_file, "--svg", svg_file])
            self.assertTrue(os.path.exists(svg_file))
            content = Path(svg_file).read_text()
            self.assertIn("<svg", content)


if __name__ == "__main__":
    unittest.main()
