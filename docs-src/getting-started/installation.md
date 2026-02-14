# Installation

## From PyPI (Recommended)

```bash
# Install the base package
pip install voronoimap

# Install with fast KDTree-accelerated nearest-neighbor lookups
pip install voronoimap[fast]
```

## From Source

```bash
# Clone the repository
git clone https://github.com/sauravbhattacharya001/VoronoiMap.git
cd VoronoiMap

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

## Dependencies

| Package | Required | Purpose |
|---------|----------|---------|
| Python 3.6+ | ✅ | Runtime |
| NumPy ≥ 1.20 | Optional | Array operations for KDTree |
| SciPy ≥ 1.7 | Optional | KDTree for O(log n) NN lookups |
| pytest ≥ 7.0 | Dev only | Test runner |
| pytest-cov ≥ 4.0 | Dev only | Coverage reporting |

### Optional Dependency Groups

=== "Base"
    ```bash
    pip install voronoimap
    ```
    Includes only the core algorithm. Uses brute-force O(n) nearest-neighbor.

=== "Fast"
    ```bash
    pip install voronoimap[fast]
    ```
    Adds NumPy + SciPy for O(log n) KDTree nearest-neighbor lookups. **Recommended** for datasets with more than ~100 points.

=== "Dev"
    ```bash
    pip install voronoimap[dev]
    ```
    Includes everything in `fast` plus pytest and coverage tools for development.

## Verify Installation

```bash
# Check that the CLI works
voronoimap --help

# Or from source
python vormap.py --help
```

Expected output:

```
usage: vormap.py [-h] [--runs RUNS] [--bounds S N W E] datafile n

Estimate Voronoi region count via random point sampling.

positional arguments:
  datafile          Point data filename inside the data/ directory
  n                 Expected number of Voronoi regions (sample size)

optional arguments:
  --runs RUNS       Number of independent estimation runs (default: 1)
  --bounds S N W E  Explicit search space boundaries
```
