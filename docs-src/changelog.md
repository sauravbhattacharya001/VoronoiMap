# Changelog

All notable changes to VoronoiMap are documented here.

## [1.55.0] - 2026-06-04

### Security

- **Fix path traversal in pipeline `data_file` config** (CWE-22)
- **doctor:** Escape user-controlled fields in HTML report

### Added

- **vormap_failover** — Agentic single-sensor outage impact & contingency advisor
- **vormap_alarmdedup** — Agentic alarm dedup & correlation advisor
- **vormap_route** — Agentic field-visit route planner advisor
- **vormap_replacement** — Agentic per-sensor replacement / swap-out advisor

### Performance

- **referee:** Vectorize grid Voronoi & perimeter scans (5-30× faster)
- **assign_cells_grid / build_distance_adjacency:** Optimized with numpy/scipy
- **resilience:** Vectorize `_voronoi_cell_areas`, dedupe post-removal metrics

### Fixed

- **montecarlo:** Fix undefined `rng`/`rng_np` NameError in CSR simulation loop
- **rng:** Stop reseeding global random in public APIs (closes [#194](https://github.com/sauravbhattacharya001/VoronoiMap/issues/194), [#192](https://github.com/sauravbhattacharya001/VoronoiMap/issues/192))
- **crystal:** Allow absolute output paths in save_image/save_animation
- **texture:** Coerce float colour values to int before bytearray write
- **tests:** Repair broken `test_cluster.py` against current API

### Tests

- referee: 28-test suite for spatial fairness
- sentinel: 13% → 99% coverage + fix Windows export crash
- privacy: 40-test suite + fix Windows UTF-8 writes
- morph: 26-test suite
- voronoi3d: 22-test suite
- balance: unit tests + KDTree-accelerated cell sampler

### Refactoring

- Replace global `random.seed()`/`np.random.seed()` with local RNG instances
- calibration: split 230-line `_classify_sensor` into focused detectors
- nn-distances: delegate to vormap_utils KDTree fast path
- Drop dead imports, unused locals, empty f-strings

---

## [1.54.0] - 2026-05-19

### Performance

- **vormap_causality Voronoi grid: 5.8× faster.** `_compute_voronoi_cells` rewired from O(res² × n) pure-Python to numpy/scipy: one `cKDTree.query(k=1)` for grid ownership, `np.bincount` for counts, vectorized diff masks for adjacency. Test suite drops from ~15.2s to ~2.6s.

### Added

- **vormap_calibration** — Per-sensor calibration drift advisor. Surfaces sensors needing re-calibration based on configurable thresholds.

### Tests

- vormap_penrose: 27-test suite (P2/P3 subdivision, tile geometry, export, CLI)
- vormap_tile: 28-test suite (palette generation, geometry, rendering, CLI)

---

## [1.53.0] - 2026-05-18

### Added

- **Spatial Hydrology Engine (`vormap_hydrology`)** — Autonomous water-flow, drainage-basin, and flood-propagation simulation
- **Spatial Tectonics Engine (`vormap_tectonics`)** — Autonomous tectonic-plate dynamics: boundaries, drift, stress
- **Spatial Genetics Engine (`vormap_genetics`)** — Autonomous population genetics: allele drift, gene flow, selection
- **vormap_curator** — Agentic spatial dataset curator (30-test suite)
- **vormap_sensorplanner** — Budget-aware agentic sensor-placement advisor
- **vormap_redundancy** — Agentic sensor retirement/merge advisor
- **vormap_drift** — Agentic temporal-drift advisor for evolving point patterns
- **vormap_handoff** — Agentic shift-handoff briefing generator
- Executive brief generator — Autonomous one-shot executive summary

### Performance

- **kmeans hot loop:** Rewritten in squared-distance space; eliminates `math.sqrt` per (point, centroid) per iteration

### Fixed

- Multi-neighbour migration overwrites; demo HTML degenerate points
- Order-independent ecosystem migration via delta accumulation
- 4 `NameError` bugs in CLI modules

### Docs

- Module catalog: 131 → 142 modules; new Agentic Advisors section
- New Simulation Engines documentation page (15 modules)

### CI

- Docker workflow: weekly rebuild, SBOM attestation via `attest-build-provenance`, Trivy SARIF reporting

---

## [1.52.0] - 2026-05-03

### Added

- **Spatial Weather Engine** — Autonomous atmospheric simulation (pressure, temperature, humidity, wind, fronts, precipitation, seasonal cycles, climate zones)
- **Spatial Governance Engine** — Autonomous democratic decision-making (proposals, deliberation, weighted voting, quorum, amendments, constitutional constraints)
- Advanced Modules documentation guide

### Tests

- 50 tests for vormap_compete territorial competition simulator
- Full test suites for weather engine (585 lines) and governance engine (600 lines)

### CI

- Docker workflow: added weekly rebuild schedule, SBOM attestation, Trivy SARIF reporting

---

## [1.51.0] - 2026-05-02

### Added

- **Spatial Nervous System Engine** — Autonomous neural signal propagation through Voronoi networks
- **Spatial Metabolism Engine (`vormap_metabolism`)** — Autonomous resource flow, consumption, transport, and equilibrium
- **Spatial Attention Engine** — Autonomous analytical focus allocation by region significance
- Comprehensive tutorials guide (8 hands-on tutorials)

### Fixed

- **Causality:** Wrong-point deletion in `_apply_intervention` remove when targets are close or duplicated (fixes [#186](https://github.com/sauravbhattacharya001/VoronoiMap/issues/186))
- **Contagion:** No-op R₀ denominator in `_estimate_r0` producing invalid reproduction numbers

### Tests

- 102 tests for 5 previously untested modules (cvd, seeds, utils, variogram, text)
- 43 tests for EcosystemSimulator

### Docs

- 7 missing modules added to catalog (131 → 138)

### Maintenance

- Removed 58 unused stdlib imports across 45 modules

---

## [1.50.0] - 2026-04-30

### Added

- **`VoronoiEstimator` class** — Per-instance state for safe concurrency (fixes [#172](https://github.com/sauravbhattacharya001/VoronoiMap/issues/172))
  - Per-instance bounds, file cache, KDTree index
  - Eliminates global mutable `IND_S/N/W/E` state
  - Thread-safe, prevents cross-dataset contamination
  - Instance methods: `set_bounds`, `load_data`, `clear_cache`, `get_NN`, `isect_B`, `ind_area`, `get_sum`
- Backward compatible: module-level functions still work via shared global `_default` instance

---

## [1.0.0] - 2026-02-14

### Fixed

- **Statistical bias in `get_sum()`** - Zero-area degenerate regions no longer corrupt the estimation average. The old code decremented `N` but left zero entries in the summation array at wrong indices, causing systematic downward bias. Now uses a clean valid-estimates-only list. (Fixes [#14](https://github.com/sauravbhattacharya001/VoronoiMap/issues/14))

### Changed

- **O(1) KDTree lookup in `get_NN()`** - Replaced O(n) identity scan over `_data_cache` with `_kdtree_by_id` dictionary keyed by `id(data)`. This runs on every NN query, so the speedup is meaningful for large datasets with many queries.
- **Cleaned up `new_dir()`** - Removed dead variable assignments (`c1x`, `c1y`, `c2x`, `c2y`) that were set to magic numbers but never read.

### Added

- Comprehensive MkDocs documentation site with:
  - Installation guide with dependency groups
  - Quick start tutorial
  - Full CLI reference
  - Python API documentation for all public functions
  - Algorithm overview with mathematical notation
  - Implementation details and architecture docs
- Regression tests for the `get_sum()` bias fix
- Test for `_kdtree_by_id` cache population

---

## [0.9.0] - 2026-02-13

### Added

- CI/CD workflow with lint, test, and coverage stages
- GitHub Pages deployment for interactive demo
- Copilot agent setup (`.github/copilot-setup-steps.yml`)
- CodeQL security scanning
- Dependabot configuration
- Docker workflow
- Branch protection rules
- Professional README with badges, API docs, and project structure

### Fixed

- Path traversal vulnerability in `load_data()` (prevents `../../etc/passwd`)
- NaN/Inf coordinate rejection
- Near-parallel line intersection producing wildly large coordinates
- Binary search convergence guard (100 iteration limit)
- Voronoi vertex count limit (`MAX_VERTICES = 50`)

### Changed

- KDTree-accelerated nearest neighbor (O(log n) with SciPy)
- Auto-detect search bounds from data extents
- Cross-product collinearity test (replaces fragile slope comparison)
- Iterative retry loop in `get_sum()` (replaces recursive calls)
