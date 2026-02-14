# Changelog

All notable changes to VoronoiMap are documented here.

## [1.0.0] — 2026-02-14

### Fixed

- **Statistical bias in `get_sum()`** — Zero-area degenerate regions no longer corrupt the estimation average. The old code decremented `N` but left zero entries in the summation array at wrong indices, causing systematic downward bias. Now uses a clean valid-estimates-only list. (Fixes [#14](https://github.com/sauravbhattacharya001/VoronoiMap/issues/14))

### Changed

- **O(1) KDTree lookup in `get_NN()`** — Replaced O(n) identity scan over `_data_cache` with `_kdtree_by_id` dictionary keyed by `id(data)`. This runs on every NN query, so the speedup is meaningful for large datasets with many queries.
- **Cleaned up `new_dir()`** — Removed dead variable assignments (`c1x`, `c1y`, `c2x`, `c2y`) that were set to magic numbers but never read.

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

## [0.9.0] — 2026-02-13

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
