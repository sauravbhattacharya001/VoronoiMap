# Copilot Instructions — VoronoiMap

## Project Overview

VoronoiMap is a comprehensive Python library/CLI for Voronoi-based spatial analysis. It started as a research tool for estimating aggregate statistics of unknown point sets using nearest-neighbor oracles, and has grown into a 130+ module ecosystem covering spatial analytics, visualization, simulation, and generative art.

## Architecture

### Core Module

- **`vormap.py`** (~113KB) — Original estimation engine:
  - Geometry helpers: `mid_point`, `eudist`, `collinear`, `perp_dir`, `polygon_area`
  - Data loading: `load_data()` reads from `data/`, caches via `_data_cache`, builds `KDTree` when scipy available
  - Nearest neighbor: `get_NN()` — KDTree O(log n) or brute-force O(n)
  - Voronoi estimation: `find_area()` approximates cells via binary search; `get_sum()` orchestrates full estimation
  - CLI: `python vormap.py <datafile> <known_count> [--runs N] [--bounds S N W E]`

### Extension Modules (129 files: `vormap_*.py`)

All follow the pattern `vormap_{name}.py` and are independently importable. Key categories:

**Spatial Analysis:**
`vormap_cluster` (k-means/DBSCAN), `vormap_hotspot` (Getis-Ord Gi*), `vormap_kde` (kernel density), `vormap_autocorr` (Moran's I), `vormap_nndist` (nearest-neighbor distance), `vormap_coverage` (service area analysis), `vormap_access` (spatial accessibility), `vormap_changedetect`, `vormap_fingerprint` (distribution signatures), `vormap_variogram` (spatial dependence)

**Geometry & Topology:**
`vormap_geometry` (shared primitives), `vormap_utils` (re-exports + helpers), `vormap_delaunay` (mesh quality), `vormap_hull` (convex hull), `vormap_edge` (primal graph), `vormap_graph` (dual/adjacency graph), `vormap_buffer` (proximity zones), `vormap_clip` (Sutherland-Hodgman clipping), `vormap_merge`

**Visualization:**
`vormap_viz` (~73KB, main SVG/HTML renderer), `vormap_heatmap`, `vormap_contour`, `vormap_color`/`vormap_coloring`, `vormap_cartogram`, `vormap_gradient`, `vormap_label`, `vormap_ascii` (terminal rendering), `vormap_animate`

**Simulation:**
`vormap_diffusion`, `vormap_contagion` (SIR epidemic), `vormap_ecosystem` (Lotka-Volterra), `vormap_compete` (territorial competition), `vormap_automata` (cellular automata), `vormap_erosion` (terrain weathering), `vormap_crystal` (nucleation/growth), `vormap_gravity`

**Optimization:**
`vormap_evolve` (genetic algorithm), `vormap_relax` (Lloyd's), `vormap_balance` (load balancing), `vormap_dispatch`, `vormap_siting`, `vormap_pathplan`, `vormap_swarm` (PSO)

**Generative Art:**
`vormap_mosaic`, `vormap_lowpoly`, `vormap_stipple`, `vormap_halftone`, `vormap_watercolor`, `vormap_stainedglass`, `vormap_pixelart`, `vormap_circlepack`, `vormap_penrose`, `vormap_kaleidoscope`, `vormap_crossstitch`, `vormap_stringart`, `vormap_fractal`, `vormap_dream`

**Autonomous Agents:**
`vormap_doctor` (diagnostics + auto-fix), `vormap_guardian` (monitoring), `vormap_sentinel`, `vormap_architect` (layout generation), `vormap_strategist`, `vormap_patrol`, `vormap_referee`

**I/O & Export:**
`vormap_geojson`, `vormap_kml`, `vormap_gpx`, `vormap_displacement` (normal maps), `vormap_mesh3d`, `vormap_report`

### Shared Utilities

- **`vormap_utils.py`** — Shared helpers (polygon_area, bounding box, point validation). Re-exports from `vormap_geometry` for backward compatibility. Uses lazy imports to avoid circular deps.
- **`vormap_geometry.py`** — Canonical geometry primitives (polygon_area, polygon_centroid, line intersection, etc.)

## Key Design Decisions

- **Optional scipy/numpy:** Core works without them (`_HAS_SCIPY`, `_HAS_NUMPY` flags). Many extension modules require numpy.
- **Global boundaries:** `IND_S`, `IND_N`, `IND_W`, `IND_E` define search space in `vormap.py`. `set_bounds()` modifies them.
- **`MAX_VERTICES = 50`** caps cell complexity; `BIN_PREC = 1e-6` is binary search threshold.
- **File caching:** `_data_cache` and `_kdtree_cache` prevent re-reads.
- **Coordinate convention:** `(lng, lat)` tuple ordering (longitude first). Abstract Euclidean units.
- **Each extension is self-contained:** Can be imported independently; only depends on `vormap_utils`/`vormap_geometry` for shared primitives.

## Conventions

- Python 3.8+ (f-strings, walrus operator acceptable)
- Type hints encouraged (from `typing` module)
- Docstrings: triple-quoted, module-level docstring at top of every file
- Tests: pytest in `tests/` directory, one test file per module (`test_{name}.py`)
- Linting: flake8, max-line-length 120, max-complexity 15
- No external frameworks beyond numpy/scipy
- `_` prefix for internal/private helpers

## How to Test

```bash
# Install dependencies
pip install -r requirements.txt
pip install -e .

# Run all tests
python -m pytest tests/ -v --tb=short

# Run tests for a specific module
python -m pytest tests/test_cluster.py -v

# Lint (fatal errors only)
flake8 vormap.py vormap_*.py --select=E9,F63,F7,F82

# Full lint (advisory)
flake8 vormap.py vormap_*.py --max-complexity=15 --max-line-length=120
```

## Common Pitfalls

- Data files must be in `data/` subdirectory relative to CWD
- `load_data()` caches results — clear `_data_cache` in tests
- Estimation algorithm is probabilistic — use wide tolerances in tests for `get_sum()`
- `find_area()` can return `None` for degenerate configurations
- When modifying global bounds, always restore them in test teardown
- Lazy imports in `vormap_utils` avoid circular deps — don't add top-level imports between geometry/utils
- Many modules use `__all__` to control exports — respect it
- Extension modules may import from each other (e.g. `vormap_graph` imports `vormap_geometry`) — watch for circular imports

## Adding New Modules

1. Create `vormap_{name}.py` with module-level docstring
2. Import shared helpers from `vormap_utils` or `vormap_geometry`
3. Add `tests/test_{name}.py` with comprehensive pytest coverage
4. Ensure `python -m py_compile vormap_{name}.py` passes
5. Keep the module self-contained; minimize cross-module coupling
