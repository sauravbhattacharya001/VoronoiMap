"""Tests for vormap_benchmark module."""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_benchmark as vb


def test_operation_timing_basic():
    t = vb.OperationTiming("test_op", 100, [0.1, 0.2, 0.3])
    assert t.operation == "test_op"
    assert t.point_count == 100
    assert len(t.times) == 3
    assert abs(t.mean - 0.2) < 1e-9
    assert abs(t.median - 0.2) < 1e-9
    assert t.min_time == 0.1
    assert t.max_time == 0.3
    assert t.std_dev > 0


def test_operation_timing_single():
    t = vb.OperationTiming("test_op", 10, [0.5])
    assert t.mean == 0.5
    assert t.median == 0.5
    assert t.std_dev == 0.0


def test_operation_timing_even_count():
    t = vb.OperationTiming("test_op", 10, [0.1, 0.2, 0.3, 0.4])
    assert abs(t.median - 0.25) < 1e-9  # (0.2 + 0.3) / 2


def test_operation_timing_to_dict():
    t = vb.OperationTiming("nn", 50, [0.001, 0.002])
    d = t.to_dict()
    assert d["operation"] == "nn"
    assert d["point_count"] == 50
    assert d["trials"] == 2
    assert "mean_ms" in d
    assert "median_ms" in d
    assert "min_ms" in d
    assert "max_ms" in d
    assert "std_dev_ms" in d


def test_generate_points():
    pts = vb._generate_points(100, seed=42)
    assert len(pts) == 100
    for x, y in pts:
        assert 0.0 <= x <= 2000.0
        assert 0.0 <= y <= 1000.0


def test_generate_points_reproducible():
    a = vb._generate_points(50, seed=123)
    b = vb._generate_points(50, seed=123)
    assert a == b


def test_run_benchmark_small():
    report = vb.run_benchmark(sizes=[10, 20], trials=1, seed=99,
                              skip_estimate=True)
    assert isinstance(report, vb.BenchmarkReport)
    assert report.sizes == [10, 20]
    assert report.trials == 1
    assert report.seed == 99
    assert report.total_time > 0
    assert len(report.timings) > 0
    # Should have bounds + nn + area for each size = 3 * 2 = 6
    assert len(report.timings) == 6


def test_format_report():
    report = vb.run_benchmark(sizes=[10], trials=1, seed=42,
                              skip_estimate=True)
    text = vb.format_report(report)
    assert "VoronoiMap Performance Benchmark" in text
    assert "Bounds Detection" in text
    assert "Nearest-Neighbor" in text


def test_export_json():
    report = vb.run_benchmark(sizes=[10], trials=1, seed=42,
                              skip_estimate=True)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name

    try:
        vb.export_json(report, path)
        with open(path) as f:
            data = json.load(f)
        assert "meta" in data
        assert "results" in data
        assert data["meta"]["seed"] == 42
        assert len(data["results"]) > 0
    finally:
        os.remove(path)


def test_export_csv():
    report = vb.run_benchmark(sizes=[10], trials=1, seed=42,
                              skip_estimate=True)
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name

    try:
        vb.export_csv(report, path)
        with open(path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "operation,point_count,trials,mean_ms,median_ms,std_dev_ms,min_ms,max_ms"
        assert len(lines) > 1  # header + at least one data row
    finally:
        os.remove(path)


def test_benchmark_report_to_dict():
    report = vb.run_benchmark(sizes=[10], trials=1, seed=42,
                              skip_estimate=True)
    d = report.to_dict()
    assert "meta" in d
    assert "results" in d
    assert isinstance(d["results"], list)
    assert d["meta"]["trials_per_size"] == 1


def test_bench_with_estimate_sum():
    """EstimateSUM benchmark works for small point sets."""
    report = vb.run_benchmark(sizes=[15], trials=1, seed=42,
                              skip_estimate=False)
    ops = [t.operation for t in report.timings]
    assert "estimate_sum" in ops


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for test in tests:
        try:
            test()
            passed += 1
            print("  PASS  %s" % test.__name__)
        except Exception as e:
            failed += 1
            print("  FAIL  %s: %s" % (test.__name__, e))
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
