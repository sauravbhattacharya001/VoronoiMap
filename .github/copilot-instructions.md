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

## Working in this Repo as a Copilot Agent

Fast feedback loop (in priority order):

1. **Syntax check the file you changed**
   `python -m py_compile vormap_<name>.py` — must pass before anything else.
2. **Run that module's tests, not the whole suite**
   `python -m pytest tests/test_<name>.py -x --tb=short` — usually <2s.
3. **Run fatal-only lint**
   `flake8 vormap.py vormap_*.py --select=E9,F63,F7,F82` — catches real bugs
   (undefined names, unused imports of `*`, syntax errors). Style warnings
   are advisory and the CI workflow already runs them with `--exit-zero`.
4. **Only then run the full suite**
   `python -m pytest tests/ -x --tb=short -q`

When modifying code:

- Check `vormap_geometry.py` and `vormap_utils.py` first — there is almost
  certainly already a helper for what you need (`polygon_area`,
  `bounding_box`, `validate_points`, `point_in_polygon`,
  `clip_polygon_to_rect`, `lerp`, …). Duplicating these is a review red flag.
- Respect optional dependencies. Modules that need numpy/scipy must guard
  imports and degrade gracefully — mirror the `_HAS_SCIPY` / `_HAS_NUMPY`
  pattern in `vormap.py`.
- Keep `(lng, lat)` ordering everywhere. Inverting it silently breaks
  every export module (`vormap_geojson`, `vormap_kml`, `vormap_gpx`).
- For tests that touch `vormap.load_data()`, clear `_data_cache` in a
  fixture and restore global bounds (`IND_S`, `IND_N`, `IND_W`, `IND_E`)
  on teardown — otherwise you will see flaky failures in unrelated tests.

What to avoid:

- Do **not** add top-level circular imports between `vormap_utils`,
  `vormap_geometry`, and `vormap.py`. Use lazy / function-local imports.
- Do **not** introduce a heavy new dependency (e.g. shapely, geopandas)
  without explicit discussion in the PR description — the project's value
  proposition is "no external frameworks beyond numpy/scipy".
- Do **not** rewrite a passing test to make a new behaviour pass. Add a
  new test instead and explain in the commit why the old assumption no
  longer holds.
