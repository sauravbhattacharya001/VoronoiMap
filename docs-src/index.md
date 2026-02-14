# VoronoiMap

**Estimate aggregate statistics of unknown point sets using Voronoi partitioning and nearest-neighbor oracles.**

[![CI](https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/ci.yml/badge.svg)](https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/github/license/sauravbhattacharya001/VoronoiMap)](LICENSE)
[![Python 3.6+](https://img.shields.io/badge/python-3.6%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)

---

## What is VoronoiMap?

VoronoiMap implements the **EstimateSUM** algorithm — a method where a partially informed attacker estimates aggregate statistics of an unknown set of objects embedded in the Euclidean plane, using only a nearest-neighbor oracle and Voronoi partitioning.

The algorithm discovers data points by sampling random locations, queries a nearest-neighbor oracle, then constructs Voronoi regions around each discovered point. By computing the ratio of the total search area to individual cell areas, it produces a statistical estimate of the total object count.

## Key Features

- **Voronoi Region Estimation** — Approximate Voronoi cells via binary search along perpendicular bisectors
- **Adaptive Boundary Detection** — Auto-detect search space boundaries from data with configurable padding
- **KDTree Acceleration** — O(log n) nearest-neighbor lookups when SciPy is installed
- **Data Caching** — Files loaded once and cached for subsequent queries
- **Robust Geometry** — Cross-product collinearity tests, relative tolerance parallel-line detection
- **Security Hardened** — Path traversal protection, NaN/Inf rejection, bounded iterations
- **CLI & API** — Full-featured command-line interface and importable Python API

## Quick Example

```python
from vormap import get_sum, load_data, find_area

# Estimate object count in a dataset
estimated_count, max_edges, avg_edges = get_sum("datauni5.txt", 5)
print(f"Estimated {estimated_count} objects")

# Compute area of a single Voronoi region
data = load_data("datauni5.txt")
area, vertices = find_area(data, 100.5, 200.3)
```

```bash
# Command-line usage
voronoimap datauni5.txt 5 --runs 3
```

## Interactive Demo

Try the [live interactive demo](https://sauravbhattacharya001.github.io/VoronoiMap/demo/) — click to add points and see Voronoi regions computed in real time.

## Research Paper

📄 [Full Report (PDF)](http://docdro.id/5HXe2wV)

## Next Steps

- [Installation](getting-started/installation.md) — Get VoronoiMap set up
- [Quick Start](getting-started/quickstart.md) — Run your first estimation
- [Algorithm Overview](algorithm/overview.md) — Understand how it works
- [API Reference](guide/api.md) — Full function documentation
