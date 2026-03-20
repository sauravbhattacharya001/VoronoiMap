"""Performance benchmark for VoronoiMap operations.

Measures execution time of core operations (Voronoi region construction,
nearest-neighbor lookup, area computation, EstimateSUM) at varying point
counts to help users understand scaling behavior on their hardware.

Example::

    from vormap_benchmark import run_benchmark
    report = run_benchmark(sizes=[50, 100, 200, 500])
    print(format_report(report))

CLI::

    python vormap_benchmark.py
    python vormap_benchmark.py --sizes 50 100 200 500 1000
    python vormap_benchmark.py --json benchmark.json
    python vormap_benchmark.py --csv benchmark.csv
    python vormap_benchmark.py --trials 5
    python vormap_benchmark.py --seed 42 --no-header
"""

import argparse
import json
import math
import os
import random
import sys
import tempfile
import time

import vormap


# ── Data Structures ──────────────────────────────────────────────────


class OperationTiming:
    """Timing result for a single operation at a given point count."""

    __slots__ = ("operation", "point_count", "times", "mean", "median",
                 "std_dev", "min_time", "max_time")

    def __init__(self, operation, point_count, times):
        self.operation = operation
        self.point_count = point_count
        self.times = list(times)
        self.mean = sum(self.times) / len(self.times) if self.times else 0.0
        sorted_t = sorted(self.times)
        n = len(sorted_t)
        if n == 0:
            self.median = 0.0
        elif n % 2 == 1:
            self.median = sorted_t[n // 2]
        else:
            self.median = (sorted_t[n // 2 - 1] + sorted_t[n // 2]) / 2.0
        self.min_time = min(self.times) if self.times else 0.0
        self.max_time = max(self.times) if self.times else 0.0
        if n > 1:
            variance = sum((t - self.mean) ** 2 for t in self.times) / (n - 1)
            self.std_dev = math.sqrt(variance)
        else:
            self.std_dev = 0.0

    def to_dict(self):
        return {
            "operation": self.operation,
            "point_count": self.point_count,
            "trials": len(self.times),
            "mean_ms": round(self.mean * 1000, 3),
            "median_ms": round(self.median * 1000, 3),
            "std_dev_ms": round(self.std_dev * 1000, 3),
            "min_ms": round(self.min_time * 1000, 3),
            "max_ms": round(self.max_time * 1000, 3),
        }


class BenchmarkReport:
    """Full benchmark report across multiple sizes and operations."""

    __slots__ = ("timings", "sizes", "trials", "seed", "has_scipy",
                 "python_version", "total_time")

    def __init__(self, timings, sizes, trials, seed, has_scipy,
                 python_version, total_time):
        self.timings = timings          # list of OperationTiming
        self.sizes = sizes
        self.trials = trials
        self.seed = seed
        self.has_scipy = has_scipy
        self.python_version = python_version
        self.total_time = total_time    # seconds

    def to_dict(self):
        return {
            "meta": {
                "trials_per_size": self.trials,
                "seed": self.seed,
                "scipy_available": self.has_scipy,
                "python_version": self.python_version,
                "total_benchmark_time_s": round(self.total_time, 2),
            },
            "results": [t.to_dict() for t in self.timings],
        }


# ── Point Generation ─────────────────────────────────────────────────


def _generate_points(n, bounds=(0.0, 1000.0, 0.0, 2000.0), seed=None):
    """Generate *n* random points within *bounds* (south, north, west, east)."""
    rng = random.Random(seed)
    south, north, west, east = bounds
    return [(rng.uniform(west, east), rng.uniform(south, north)) for _ in range(n)]


# ── Benchmark Operations ────────────────────────────────────────────


def _bench_nearest_neighbor(data, bounds, rng, trials):
    """Benchmark nearest-neighbor lookup speed."""
    south, north, west, east = bounds
    # Pre-generate query points so generation time is excluded
    queries = [(rng.uniform(west, east), rng.uniform(south, north))
               for _ in range(trials)]
    times = []
    for qx, qy in queries:
        t0 = time.perf_counter()
        vormap.get_NN(data, qx, qy)
        times.append(time.perf_counter() - t0)
    return times


def _bench_voronoi_area(data, bounds, trials):
    """Benchmark Voronoi region area computation for random points."""
    vormap.set_bounds(*bounds)
    # Pick random data points to compute area for (up to trials count)
    indices = list(range(len(data)))
    rng = random.Random(42)
    rng.shuffle(indices)
    pick = indices[:min(trials, len(data))]
    times = []
    for idx in pick:
        dx, dy = data[idx]
        t0 = time.perf_counter()
        try:
            vormap.find_area(data, dx, dy)
        except (RuntimeError, IndexError):
            pass  # some edge points may fail — skip timing
        else:
            times.append(time.perf_counter() - t0)
    return times if times else [0.0]


def _bench_estimate_sum(data, bounds, trials, tmpdir):
    """Benchmark the EstimateSUM algorithm."""
    # Write data to a temp file for get_sum()
    data_file = os.path.join(tmpdir, "_bench_data.txt")
    with open(data_file, "w") as f:
        for x, y in data:
            f.write("%.6f %.6f\n" % (x, y))

    vormap.set_bounds(*bounds)
    times = []
    sample_count = max(3, len(data) // 5)  # sample ~20% of points
    for _ in range(trials):
        t0 = time.perf_counter()
        try:
            vormap.get_sum(data_file, sample_count)
        except Exception:
            pass
        else:
            times.append(time.perf_counter() - t0)

    # Clean up
    try:
        os.remove(data_file)
    except OSError:
        pass

    return times if times else [0.0]


def _bench_bounds_detection(data, trials):
    """Benchmark auto-bounds computation."""
    times = []
    for _ in range(trials):
        t0 = time.perf_counter()
        vormap.compute_bounds(data)
        times.append(time.perf_counter() - t0)
    return times


# ── Main Benchmark Runner ───────────────────────────────────────────


DEFAULT_SIZES = [25, 50, 100, 200, 500]


def run_benchmark(sizes=None, trials=3, seed=42, verbose=False,
                  skip_estimate=False):
    """Run the full benchmark suite.

    Parameters
    ----------
    sizes : list of int or None
        Point counts to benchmark.  Defaults to ``[25, 50, 100, 200, 500]``.
    trials : int
        Number of trials per operation per size.
    seed : int
        Random seed for reproducibility.
    verbose : bool
        Print progress to stderr.
    skip_estimate : bool
        Skip the slow EstimateSUM benchmark.

    Returns
    -------
    BenchmarkReport
        Full benchmark results.
    """
    if sizes is None:
        sizes = list(DEFAULT_SIZES)

    sizes = sorted(sizes)
    timings = []
    bounds = (0.0, 1000.0, 0.0, 2000.0)
    rng = random.Random(seed)

    import platform
    py_version = platform.python_version()

    overall_start = time.perf_counter()

    # Use a secure temporary directory for benchmark data files instead
    # of writing to the source directory (predictable filenames in shared
    # locations are a symlink/TOCTOU risk).
    with tempfile.TemporaryDirectory(prefix="vormap_bench_") as tmpdir:
        for size in sizes:
            if verbose:
                print("Benchmarking %d points..." % size, file=sys.stderr)

            data = _generate_points(size, bounds=bounds, seed=seed + size)

            # 1. Bounds detection
            bt = _bench_bounds_detection(data, trials)
            timings.append(OperationTiming("compute_bounds", size, bt))

            # 2. Nearest-neighbor lookup
            nn_trials = max(trials, 10)  # NN is fast, use more trials
            nnt = _bench_nearest_neighbor(data, bounds, rng, nn_trials)
            timings.append(OperationTiming("nearest_neighbor", size, nnt))

            # 3. Voronoi area (per-region)
            area_trials = min(trials, size)
            at = _bench_voronoi_area(data, bounds, area_trials)
            timings.append(OperationTiming("voronoi_area", size, at))

            # 4. EstimateSUM (expensive — optional)
            if not skip_estimate and size <= 500:
                est_trials = min(trials, 2)
                et = _bench_estimate_sum(data, bounds, est_trials, tmpdir)
                timings.append(OperationTiming("estimate_sum", size, et))

    total = time.perf_counter() - overall_start

    return BenchmarkReport(
        timings=timings,
        sizes=sizes,
        trials=trials,
        seed=seed,
        has_scipy=vormap._HAS_SCIPY,
        python_version=py_version,
        total_time=total,
    )


# ── Formatting ───────────────────────────────────────────────────────


def format_report(report):
    """Format a benchmark report as a human-readable table.

    Returns
    -------
    str
        Formatted report string.
    """
    lines = []
    lines.append("=" * 72)
    lines.append("VoronoiMap Performance Benchmark")
    lines.append("=" * 72)
    lines.append("Python:        %s" % report.python_version)
    lines.append("SciPy KDTree:  %s" % ("yes" if report.has_scipy else "no (brute-force NN)"))
    lines.append("Trials/size:   %d" % report.trials)
    lines.append("Seed:          %s" % report.seed)
    lines.append("Total time:    %.2fs" % report.total_time)
    lines.append("")

    # Group by operation
    ops = {}
    for t in report.timings:
        ops.setdefault(t.operation, []).append(t)

    for op_name, op_timings in ops.items():
        label = {
            "compute_bounds": "Bounds Detection",
            "nearest_neighbor": "Nearest-Neighbor Lookup",
            "voronoi_area": "Voronoi Area (per region)",
            "estimate_sum": "EstimateSUM Algorithm",
        }.get(op_name, op_name)

        lines.append("-" * 72)
        lines.append("  %s" % label)
        lines.append("-" * 72)
        lines.append("  %-10s  %10s  %10s  %10s  %10s" % (
            "Points", "Mean (ms)", "Median", "Min", "Max"))
        lines.append("  %-10s  %10s  %10s  %10s  %10s" % (
            "------", "---------", "------", "---", "---"))

        for t in op_timings:
            d = t.to_dict()
            lines.append("  %-10d  %10.3f  %10.3f  %10.3f  %10.3f" % (
                d["point_count"], d["mean_ms"], d["median_ms"],
                d["min_ms"], d["max_ms"]))

        # Scaling analysis (how does time grow?)
        if len(op_timings) >= 2:
            first = op_timings[0]
            last = op_timings[-1]
            if first.mean > 0 and first.point_count > 0:
                size_ratio = last.point_count / first.point_count
                time_ratio = last.mean / first.mean if first.mean > 1e-9 else 0
                if time_ratio > 0 and size_ratio > 1:
                    exponent = math.log(time_ratio) / math.log(size_ratio)
                    complexity = "O(n^%.1f)" % exponent
                    lines.append("")
                    lines.append("  Empirical scaling: %s  (%.0fx points -> %.1fx time)"
                                 % (complexity, size_ratio, time_ratio))

        lines.append("")

    lines.append("=" * 72)
    return "\n".join(lines)


def export_json(report, filepath):
    """Export benchmark report to JSON."""
    path = vormap.validate_output_path(filepath, allow_absolute=True)
    with open(path, "w") as f:
        json.dump(report.to_dict(), f, indent=2)
    return path


def export_csv(report, filepath):
    """Export benchmark report to CSV."""
    path = vormap.validate_output_path(filepath, allow_absolute=True)
    with open(path, "w") as f:
        f.write("operation,point_count,trials,mean_ms,median_ms,std_dev_ms,min_ms,max_ms\n")
        for t in report.timings:
            d = t.to_dict()
            f.write("%s,%d,%d,%.3f,%.3f,%.3f,%.3f,%.3f\n" % (
                d["operation"], d["point_count"], d["trials"],
                d["mean_ms"], d["median_ms"], d["std_dev_ms"],
                d["min_ms"], d["max_ms"]))
    return path


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark VoronoiMap performance at various point counts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_benchmark.py
  python vormap_benchmark.py --sizes 50 100 200 500 1000
  python vormap_benchmark.py --json benchmark.json --csv benchmark.csv
  python vormap_benchmark.py --trials 5 --seed 123
  python vormap_benchmark.py --skip-estimate  # faster run""",
    )
    parser.add_argument(
        "--sizes", nargs="+", type=int, default=None,
        help="Point counts to benchmark (default: 25 50 100 200 500)")
    parser.add_argument(
        "--trials", type=int, default=3,
        help="Trials per operation per size (default: 3)")
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed (default: 42)")
    parser.add_argument(
        "--json", dest="json_out", metavar="FILE",
        help="Export results to JSON file")
    parser.add_argument(
        "--csv", dest="csv_out", metavar="FILE",
        help="Export results to CSV file")
    parser.add_argument(
        "--skip-estimate", action="store_true",
        help="Skip the (slow) EstimateSUM benchmark")
    parser.add_argument(
        "--no-header", action="store_true",
        help="Suppress header/footer in text output")
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Only output export files, no text report")

    args = parser.parse_args()

    report = run_benchmark(
        sizes=args.sizes,
        trials=args.trials,
        seed=args.seed,
        verbose=not args.quiet,
        skip_estimate=args.skip_estimate,
    )

    if not args.quiet:
        print(format_report(report))

    if args.json_out:
        path = export_json(report, args.json_out)
        print("JSON saved to %s" % path)

    if args.csv_out:
        path = export_csv(report, args.csv_out)
        print("CSV saved to %s" % path)


if __name__ == "__main__":
    main()
