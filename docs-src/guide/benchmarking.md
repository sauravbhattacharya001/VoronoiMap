# Benchmarking & Performance

VoronoiMap includes a built-in benchmark suite (`vormap_benchmark`) that measures
execution time of core operations at varying point counts, helping you understand
scaling behavior on your hardware.

## Quick Start

```bash
# Run with default settings (50, 100, 200, 500 points × 3 trials)
python vormap_benchmark.py

# Custom point counts
python vormap_benchmark.py --sizes 50 100 200 500 1000

# More trials for stable measurements
python vormap_benchmark.py --trials 10

# Reproducible results
python vormap_benchmark.py --seed 42

# Export results
python vormap_benchmark.py --json benchmark.json
python vormap_benchmark.py --csv benchmark.csv
```

## What Gets Benchmarked

The benchmark measures four core operations at each point count:

| Operation | What It Measures |
|-----------|-----------------|
| **Voronoi Construction** | Building the full Voronoi diagram from seed points |
| **Nearest-Neighbor Lookup** | Single NN query (KDTree when SciPy available, brute-force otherwise) |
| **Area Computation** | Computing the area of a single Voronoi cell |
| **EstimateSUM** | Full estimation pipeline end-to-end |

## Python API

```python
from vormap_benchmark import run_benchmark, format_report

# Run benchmark
report = run_benchmark(
    sizes=[50, 100, 200, 500, 1000],
    trials=5,
    seed=42,
)

# Print human-readable table
print(format_report(report))

# Access individual timings
for timing in report.timings:
    print(f"{timing.operation} @ {timing.point_count} pts: "
          f"{timing.mean * 1000:.1f}ms ± {timing.std_dev * 1000:.1f}ms")

# Export to dict (JSON-serializable)
import json
print(json.dumps(report.to_dict(), indent=2))
```

## Performance Tips

### Install SciPy for KDTree Acceleration

The single most impactful optimization. SciPy's KDTree provides O(log n) nearest-neighbor
lookups vs O(n) brute-force:

```bash
pip install voronoimap[fast]
# or
pip install scipy
```

!!! tip "Expect 5-10× speedup on datasets with >200 points"

### Scaling Behavior

| Point Count | Without SciPy | With SciPy |
|-------------|---------------|------------|
| 50          | ~10ms         | ~8ms       |
| 200         | ~80ms         | ~20ms      |
| 1000        | ~2s           | ~100ms     |
| 5000        | ~50s          | ~800ms     |

*(Approximate — actual times depend on hardware)*

### Memory Considerations

- VoronoiMap caches loaded data files in memory. For very large datasets,
  this avoids re-reading from disk on repeated queries.
- Each Voronoi cell stores its vertex list. With 50,000+ points, expect
  ~100MB of polygon data in memory.

### Reducing Estimation Variance

The `get_sum()` function produces statistical estimates. To reduce variance:

- **Increase sample size `n`** — More samples = tighter confidence intervals
- **Use `--runs` flag** — Average across multiple runs for stability
- **Larger search areas** — Padding the search bounds captures edge cells better

```python
# Multiple runs for averaged estimates
results = []
for _ in range(10):
    est, _, _ = get_sum("data.txt", n=20)
    results.append(est)
avg_estimate = sum(results) / len(results)
```

## CI Benchmark Tracking

You can integrate benchmarks into CI to catch performance regressions:

```yaml
# .github/workflows/benchmark.yml
- name: Run benchmarks
  run: python vormap_benchmark.py --json benchmark.json --seed 42
- name: Upload results
  uses: actions/upload-artifact@v4
  with:
    name: benchmark-results
    path: benchmark.json
```
