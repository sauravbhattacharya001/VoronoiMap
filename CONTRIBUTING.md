# Contributing to VoronoiMap

Thank you for your interest in contributing! This guide covers everything you need to get started — from setting up your development environment to submitting pull requests.

## Code of Conduct

Please be respectful, constructive, and collaborative. We welcome contributors of all experience levels.

## Getting Started

### Prerequisites

- **Python 3.6+** (3.9+ recommended)
- **Git**
- **pip** (comes with Python)
- **GCC** (optional, only for Docker builds)

### Development Setup

```bash
# 1. Fork the repository on GitHub, then clone your fork
git clone https://github.com/<your-username>/VoronoiMap.git
cd VoronoiMap

# 2. Install in editable mode with dev dependencies
pip install -e ".[dev]"

# 3. Verify everything works
python -m pytest tests/ -v
```

The `[dev]` extra installs pytest, pytest-cov, NumPy, and SciPy — everything you need for development.

### Optional: Fast Mode

For faster nearest-neighbor queries and precise region computation, install the optional scientific dependencies:

```bash
pip install -e ".[fast]"
```

The test suite runs with and without SciPy — both paths are exercised.

## Project Structure

```
VoronoiMap/
├── vormap.py           # Core engine: NN queries, binary search, Voronoi estimation
├── vormap_viz.py       # Visualization: SVG, interactive HTML, GeoJSON export
├── tests/
│   ├── test_vormap.py       # Core function tests (geometry, data loading, security)
│   ├── test_vormap_core.py  # Extended core tests (isect, bin_search, CLI, edge cases)
│   └── test_vormap_viz.py   # Visualization tests (SVG, HTML, GeoJSON, color schemes)
├── docs-src/           # MkDocs documentation source
├── pyproject.toml      # Package configuration
├── requirements.txt    # Optional runtime dependencies
└── .github/            # CI/CD workflows, Copilot agent config
```

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `vormap.py` | Point loading, nearest-neighbor oracle, binary search boundary tracing, Voronoi region estimation |
| `vormap_viz.py` | Region computation (scipy + fallback), SVG/HTML/GeoJSON export, color schemes |

## Running Tests

```bash
# Run all tests with verbose output
python -m pytest tests/ -v

# Run with coverage report
python -m pytest tests/ -v --cov=vormap --cov-report=term-missing

# Run a specific test file
python -m pytest tests/test_vormap_core.py -v

# Run a specific test class or method
python -m pytest tests/test_vormap.py -v -k "TestGetNN"
python -m pytest tests/test_vormap_core.py::TestIsect::test_crossing_segments -v
```

**All tests must pass before submitting a PR.** The CI pipeline runs tests on Python 3.9, 3.11, and 3.12.

## Code Style

### General Guidelines

- Follow **PEP 8** conventions
- Use **descriptive variable names** — avoid single letters except in tight loops
- Add **docstrings** to all public functions (NumPy-style preferred)
- Keep functions focused — **one responsibility per function**
- Prefer **explicit over implicit** — don't rely on hidden state changes

### Formatting

- **Indentation:** 4 spaces (no tabs)
- **Line length:** 79 characters preferred, 99 max
- **Imports:** Standard library → third-party → local, separated by blank lines
- **Strings:** Double quotes for docstrings, single or double for everything else (be consistent within a file)

### Naming

- `snake_case` for functions and variables
- `PascalCase` for classes
- `UPPER_CASE` for module-level constants
- Prefix private helpers with `_` (e.g., `_clip_infinite_voronoi`)

## Making Changes

### Workflow

1. **Fork** the repository on GitHub
2. **Clone** your fork locally
3. Create a **feature branch** from `master`:
   ```bash
   git checkout -b feature/my-improvement
   ```
4. Make your changes, adding **tests** for new functionality
5. Run the **full test suite** and ensure it passes:
   ```bash
   python -m pytest tests/ -v
   ```
6. **Commit** with a clear, descriptive message (see below)
7. **Push** to your fork:
   ```bash
   git push origin feature/my-improvement
   ```
8. Open a **Pull Request** against `master`

### Commit Messages

Use the conventional format:

```
type: short description

Longer explanation if needed. Wrap at 72 characters.
Reference issues with #number.
```

**Types:** `feat`, `fix`, `test`, `docs`, `refactor`, `perf`, `chore`

**Examples:**
- `fix: handle NaN coordinates in load_data gracefully`
- `feat: add GeoJSON export with custom properties callback`
- `test: add 67 tests for core geometry functions`
- `perf: use KDTree for O(log n) nearest-neighbor queries`

### Pull Request Guidelines

- **One concern per PR** — don't mix unrelated changes
- **Include tests** for bug fixes (regression tests) and new features
- **Update documentation** if you change public APIs
- Fill out the **PR template** (checklist, description, testing notes)
- Keep PRs **focused and reviewable** (under 500 lines preferred)

## What We're Looking For

### High-Value Contributions

- 🐛 **Bug fixes** with regression tests
- ⚡ **Performance improvements** with benchmarks
- 🔒 **Security hardening** (input validation, path safety)
- 📝 **Documentation** improvements (docstrings, guides, examples)
- ✅ **Tests** for untested code paths
- ♻️ **Refactoring** that improves maintainability

### Research & Algorithms

- New **estimation strategies** or oracle types
- Alternative **region computation** methods
- **Statistical analysis** of estimation accuracy
- **Benchmarks** comparing different approaches

### Visualization

- New **color schemes** (add to `_COLOR_SCHEMES` in `vormap_viz.py`)
- **Export formats** (KML, Shapefile, etc.)
- Interactive features for the HTML export

## Testing Guidelines

### Writing Good Tests

```python
class TestMyFeature:
    def test_basic_case(self):
        """Clear description of what this tests."""
        result = my_function(normal_input)
        assert result == expected_output

    def test_edge_case(self):
        """Edge case: empty input should raise ValueError."""
        with pytest.raises(ValueError, match="descriptive message"):
            my_function(empty_input)

    def test_boundary_condition(self):
        """Boundary: single-element input should work."""
        result = my_function([single_item])
        assert result is not None
```

### Best Practices

- **Test behavior, not implementation** — tests should survive refactoring
- **One assertion per concept** — split complex assertions into separate tests
- **Use fixtures** for shared setup (see `conftest.py` patterns)
- **Clean up global state** — restore `IND_S/N/W/E` bounds and `_data_cache` after tests
- **Name tests descriptively** — `test_nan_coordinates_skipped` > `test_bad_input`
- **Cover error paths** — test that exceptions are raised correctly

### Test Organization

| File | What it tests |
|------|--------------|
| `test_vormap.py` | Core geometry, data loading, security, NN queries, bias fix |
| `test_vormap_core.py` | Line intersection, boundary search, CLI, constants, edge cases |
| `test_vormap_viz.py` | SVG/HTML/GeoJSON export, color schemes, region computation |

## Reporting Issues

When filing an issue, please include:

- **Python version** (`python --version`)
- **SciPy installed?** (`pip show scipy`)
- **OS** and version
- **Minimal reproduction** — smallest code/data that triggers the issue
- **Expected vs actual** behavior
- **Data file** (if applicable and not sensitive)
- **Full traceback** if an error occurred

## Architecture Notes

### Core Algorithm

VoronoiMap estimates Voronoi region counts using random point sampling:

1. **Load data** → parse point file, build KDTree (if scipy available)
2. **Random sampling** → pick random points in the search space
3. **NN query** → find nearest seed point (KDTree O(log n) or brute O(n))
4. **Binary search** → trace Voronoi region boundary via perpendicular bisector walking
5. **Area estimation** → Shoelace formula on traced polygon
6. **Statistical estimation** → total_area / sample_area ≈ region count

### Key Invariants

- `_data_cache` is keyed by filename — same file is never read twice
- `_kdtree_by_id` is keyed by `id(point_list)` — O(1) lookup per NN query
- Global bounds (`IND_S/N/W/E`) are set by `load_data()` with auto-detection
- `bin_search` always terminates (iteration limit + convergence check)
- Path traversal protection prevents reading outside `data/`

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

Questions? Open a [discussion](https://github.com/sauravbhattacharya001/VoronoiMap/issues) or reach out in an issue. We're happy to help!
