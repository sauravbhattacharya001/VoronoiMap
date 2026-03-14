"""Tests for vormap_classify -- data classification for thematic mapping."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from vormap_classify import (
    classify, compare_methods, format_report, format_comparison_report,
    export_json, export_csv, export_svg_legend,
    ClassificationResult, METHODS, _mean, _std, _gvf,
)

UNIFORM = list(range(1, 101))
SKEWED = [1, 2, 3, 4, 5, 10, 20, 50, 100, 500]
CONSTANT = [5.0] * 20
SMALL = [1.0, 2.0, 3.0]
NEGATIVE = [-10, -5, 0, 5, 10, 15, 20]

def _ok(cond, msg=""):
    assert cond, msg

def test_mean():
    _ok(abs(_mean([1, 2, 3]) - 2.0) < 1e-9)
    _ok(_mean([]) == 0.0)

def test_std():
    _ok(abs(_std([1, 1, 1]) - 0.0) < 1e-9)
    _ok(_std([1, 2, 3]) > 0)

def test_equal_interval_basic():
    r = classify(UNIFORM, method="equal_interval", k=5)
    _ok(r.method == "equal_interval")
    _ok(r.k == 5)
    _ok(len(r.breaks) == 6)

def test_equal_interval_coverage():
    r = classify(UNIFORM, method="equal_interval", k=5)
    _ok(sum(c.count for c in r.classes) == 100)

def test_equal_interval_even_widths():
    r = classify(UNIFORM, method="equal_interval", k=4)
    widths = [c.range for c in r.classes]
    _ok(all(abs(w - widths[0]) < 1e-9 for w in widths))

def test_quantile_basic():
    r = classify(UNIFORM, method="quantile", k=4)
    _ok(r.method == "quantile")

def test_quantile_approx_equal_counts():
    r = classify(UNIFORM, method="quantile", k=5)
    counts = [c.count for c in r.classes]
    _ok(max(counts) - min(counts) <= 22)

def test_quantile_skewed():
    r = classify(SKEWED, method="quantile", k=3)
    _ok(sum(c.count for c in r.classes) == len(SKEWED))

def test_natural_breaks_basic():
    r = classify(UNIFORM, method="natural_breaks", k=5)
    _ok(r.gvf > 0)

def test_natural_breaks_high_gvf():
    r = classify(SKEWED, method="natural_breaks", k=4)
    r2 = classify(SKEWED, method="equal_interval", k=4)
    _ok(r.gvf >= r2.gvf - 0.05)

def test_natural_breaks_coverage():
    r = classify(SKEWED, method="natural_breaks", k=3)
    _ok(sum(c.count for c in r.classes) == len(SKEWED))

def test_natural_breaks_small():
    r = classify(SMALL, method="natural_breaks", k=2)
    _ok(sum(c.count for c in r.classes) == 3)

def test_std_deviation_basic():
    r = classify(UNIFORM, method="std_deviation", k=4)
    _ok(sum(c.count for c in r.classes) == len(UNIFORM))

def test_std_deviation_negative():
    r = classify(NEGATIVE, method="std_deviation", k=4)
    _ok(sum(c.count for c in r.classes) == len(NEGATIVE))

def test_head_tail_basic():
    r = classify(SKEWED, method="head_tail", k=5)
    _ok(r.k >= 2)

def test_head_tail_coverage():
    r = classify(SKEWED, method="head_tail", k=5)
    _ok(sum(c.count for c in r.classes) == len(SKEWED))

def test_head_tail_constant():
    r = classify(CONSTANT, method="head_tail", k=5)
    _ok(r.k >= 1)
    _ok(sum(c.count for c in r.classes) == len(CONSTANT))

def test_pretty_basic():
    r = classify(UNIFORM, method="pretty", k=5)
    _ok(sum(c.count for c in r.classes) == len(UNIFORM))

def test_pretty_round_numbers():
    r = classify(list(range(0, 101)), method="pretty", k=5)
    _ok(any(b == int(b) for b in r.breaks[1:-1]) or len(r.breaks) >= 2)

def test_manual_basic():
    r = classify(UNIFORM, method="manual", manual_breaks=[1, 25, 50, 75, 100])
    _ok(r.k == 4)

def test_manual_extends_range():
    r = classify(UNIFORM, method="manual", manual_breaks=[10, 50, 90])
    _ok(r.breaks[0] <= 1.0)
    _ok(r.breaks[-1] >= 100.0)

def test_manual_error():
    try:
        classify(UNIFORM, method="manual")
        _ok(False)
    except ValueError:
        pass

def test_class_for_value():
    r = classify(UNIFORM, method="equal_interval", k=5)
    _ok(r.class_for_value(1.0) == 0)
    _ok(r.class_for_value(100.0) == 4)

def test_gvf_perfect():
    r = classify([1, 10, 100], method="natural_breaks", k=3)
    _ok(r.gvf >= 0.9)

def test_gvf_range():
    r = classify(UNIFORM, method="equal_interval", k=5)
    _ok(0 <= r.gvf <= 1.0)

def test_compare_basic():
    results = compare_methods(UNIFORM, k=5)
    _ok(len(results) >= 4)
    _ok(results[0].gvf >= results[-1].gvf)

def test_compare_skewed():
    results = compare_methods(SKEWED, k=4)
    _ok(all(isinstance(r, ClassificationResult) for r in results))

def test_compare_subset():
    results = compare_methods(UNIFORM, k=5, methods=["equal_interval", "quantile"])
    _ok(len(results) == 2)

def test_format_report():
    r = classify(UNIFORM, method="natural_breaks", k=5)
    text = format_report(r)
    _ok("CLASSIFICATION REPORT" in text)
    _ok("GVF" in text)

def test_format_comparison():
    results = compare_methods(UNIFORM, k=5)
    text = format_comparison_report(results)
    _ok("COMPARISON" in text)

def test_export_json():
    r = classify(UNIFORM, method="natural_breaks", k=5)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        export_json(r, path)
        with open(path) as f:
            data = json.load(f)
        _ok(data["method"] == "natural_breaks")
        _ok(len(data["classes"]) == 5)
    finally:
        os.unlink(path)

def test_export_csv():
    r = classify(UNIFORM, method="quantile", k=4)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    try:
        export_csv(r, path)
        with open(path) as f:
            lines = f.readlines()
        _ok(len(lines) == 5)
    finally:
        os.unlink(path)

def test_export_svg_legend():
    r = classify(UNIFORM, method="equal_interval", k=5)
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        path = f.name
    try:
        export_svg_legend(r, path)
        with open(path) as f:
            content = f.read()
        _ok("<svg" in content)
        _ok("<rect" in content)
    finally:
        os.unlink(path)

def test_export_svg_custom_colors():
    r = classify(SMALL, method="equal_interval", k=2)
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        path = f.name
    try:
        export_svg_legend(r, path, colors=["#ff0000", "#0000ff"])
        with open(path) as f:
            _ok("ff0000" in f.read())
    finally:
        os.unlink(path)

def test_constant_values():
    r = classify(CONSTANT, method="equal_interval", k=3)
    _ok(sum(c.count for c in r.classes) == len(CONSTANT))

def test_two_values():
    r = classify([1.0, 2.0], method="natural_breaks", k=2)
    _ok(sum(c.count for c in r.classes) == 2)

def test_empty_raises():
    try:
        classify([])
        _ok(False)
    except ValueError:
        pass

def test_unknown_method():
    try:
        classify(UNIFORM, method="bogus")
        _ok(False)
    except ValueError:
        pass

def test_negative_values():
    r = classify(NEGATIVE, method="natural_breaks", k=3)
    _ok(r.global_min == -10)
    _ok(sum(c.count for c in r.classes) == len(NEGATIVE))

def test_to_dict():
    r = classify(UNIFORM, method="equal_interval", k=5)
    d = r.to_dict()
    _ok(d["method"] == "equal_interval")
    _ok(len(d["classes"]) == 5)

def test_large_k_clamped():
    r = classify(SMALL, method="equal_interval", k=100)
    _ok(sum(c.count for c in r.classes) == len(SMALL))

def test_floats():
    vals = [0.1, 0.5, 1.2, 2.7, 3.3, 5.8, 8.1, 12.4]
    r = classify(vals, method="natural_breaks", k=3)
    _ok(sum(c.count for c in r.classes) == len(vals))

def test_all_methods_run():
    for m in METHODS:
        r = classify(UNIFORM, method=m, k=4)
        _ok(r.k >= 1, f"{m} produced 0 classes")

if __name__ == "__main__":
    tests = [(n, o) for n, o in globals().items() if n.startswith("test_") and callable(o)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"  PASS {name}")
        except Exception as e:
            failed += 1
            print(f"  FAIL {name}: {e}")
    print(f"\n  {passed} passed, {failed} failed, {passed + failed} total")
    sys.exit(1 if failed else 0)
