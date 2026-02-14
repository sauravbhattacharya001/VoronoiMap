# Copilot Instructions — VoronoiMap

## Project Overview

VoronoiMap is a Python library/CLI tool that estimates aggregate statistics of unknown point sets in 2D Euclidean space using nearest-neighbor oracles and Voronoi partitioning. It was built for a research paper on partial-information estimation.

## Architecture

- **`vormap.py`** — Single-module implementation containing all logic:
  - **Geometry helpers:** `mid_point`, `eudist`, `collinear`, `perp_dir`, `polygon_area` — pure math, no side effects
  - **Data loading:** `load_data()` reads from `data/` directory, caches results in `_data_cache`; builds a `KDTree` when scipy is available
  - **Nearest neighbor:** `get_NN()` uses KDTree (O(log n)) when scipy is available, falls back to brute-force O(n) scan
  - **Voronoi estimation:** `find_area()` approximates a single Voronoi cell via binary search along perpendicular bisectors; `get_sum()` orchestrates the full estimation loop
  - **CLI entrypoint:** `__main__` block at bottom — `python vormap.py <datafile> <known_count> [--runs N] [--bounds S N W E]`

- **`tests/test_vormap.py`** — pytest test suite covering geometry helpers, data loading, NN queries, and bounds computation

## Key Design Decisions

- **Optional scipy dependency:** The module works without numpy/scipy (brute-force NN), but uses KDTree when available. Check `_HAS_SCIPY` flag.
- **Global boundaries:** `IND_S`, `IND_N`, `IND_W`, `IND_E` define the search space. `set_bounds()` modifies them; `load_data(auto_bounds=True)` auto-detects from data.
- **`MAX_VERTICES = 50`** caps Voronoi cell complexity to prevent infinite loops on degenerate inputs.
- **`BIN_PREC = 1e-6`** is the binary search convergence threshold.
- **File caching:** `_data_cache` and `_kdtree_cache` dicts prevent re-reading files.

## Conventions

- Pure Python 3.6+ compatible (no f-strings required, but acceptable)
- No external frameworks — just stdlib + optional numpy/scipy
- Functions use `(lng, lat)` tuple ordering (longitude first, then latitude)
- All coordinates are in abstract Euclidean units (not geographic degrees)
- Tests use pytest; run with `python -m pytest tests/ -v`
- Linting: `flake8` with max line length 120, max complexity 15

## How to Test

```bash
# Create test data (tests expect data/ directory)
mkdir -p data
echo -e "100.0 200.0\n300.0 400.0\n500.0 600.0" > data/test_points.txt

# Run tests
python -m pytest tests/ -v --tb=short

# Lint (syntax errors and undefined names only)
flake8 vormap.py --select=E9,F63,F7,F82

# Full lint (non-blocking)
flake8 vormap.py --max-complexity=15 --max-line-length=120
```

## Common Pitfalls

- Data files must be in a `data/` subdirectory relative to CWD
- `load_data()` caches results — clear `_data_cache` in tests to avoid cross-test pollution
- The estimation algorithm is probabilistic — tests on `get_sum()` should use wide tolerances
- `find_area()` can return `None` for degenerate point configurations (collinear points, points outside bounds)
- When modifying global bounds (`IND_S` etc.), always restore them in test teardown
