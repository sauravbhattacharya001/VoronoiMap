<p align="center">
  <h1 align="center">🗺️ VoronoiMap</h1>
  <p align="center">
    <strong>Estimate aggregate statistics of unknown point sets using Voronoi partitioning and nearest-neighbor oracles</strong>
  </p>
  <p align="center">
    <a href="https://pypi.org/project/voronoimap/"><img src="https://img.shields.io/pypi/v/voronoimap?color=blue&logo=pypi&logoColor=white" alt="PyPI"></a>
    <a href="https://pepy.tech/project/voronoimap"><img src="https://img.shields.io/pepy/dt/voronoimap?color=blue&logo=pypi&logoColor=white" alt="PyPI Downloads"></a>
    <a href="https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/ci.yml"><img src="https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/docker.yml"><img src="https://github.com/sauravbhattacharya001/VoronoiMap/actions/workflows/docker.yml/badge.svg" alt="Docker"></a>
    <a href="https://codecov.io/gh/sauravbhattacharya001/VoronoiMap"><img src="https://codecov.io/gh/sauravbhattacharya001/VoronoiMap/graph/badge.svg" alt="codecov"></a>
    <a href="https://github.com/sauravbhattacharya001/VoronoiMap/releases/latest"><img src="https://img.shields.io/github/v/release/sauravbhattacharya001/VoronoiMap?logo=github&color=green" alt="GitHub Release"></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/sauravbhattacharya001/VoronoiMap?color=blue" alt="License"></a>
    <img src="https://img.shields.io/badge/python-3.6%2B-3776ab?logo=python&logoColor=white" alt="Python 3.6+">
    <a href="https://sauravbhattacharya001.github.io/VoronoiMap/"><img src="https://img.shields.io/badge/demo-live-brightgreen?logo=github-pages&logoColor=white" alt="Live Demo"></a>
    <a href="https://github.com/sauravbhattacharya001/VoronoiMap/pkgs/container/voronoimap"><img src="https://img.shields.io/badge/ghcr.io-container-blue?logo=docker&logoColor=white" alt="GHCR"></a>
    <a href="https://github.com/sauravbhattacharya001/VoronoiMap/stargazers"><img src="https://img.shields.io/github/stars/sauravbhattacharya001/VoronoiMap?style=flat&logo=github" alt="Stars"></a>
    <img src="https://img.shields.io/github/repo-size/sauravbhattacharya001/VoronoiMap" alt="Repo size">
  </p>
</p>

---

## 📖 Overview

VoronoiMap implements the **EstimateSUM** algorithm — a method where a partially informed attacker estimates aggregate statistics of an unknown set of objects embedded in the Euclidean plane, using only a nearest-neighbor oracle and Voronoi partitioning.

The algorithm discovers data points by sampling random locations, queries a nearest-neighbor oracle, then constructs Voronoi regions around each discovered point. By computing the ratio of the total search area to individual cell areas, it produces a statistical estimate of the total object count.

📄 **Research Paper:** *Estimating Aggregate Statistics of Unknown Point Sets Using Voronoi Partitioning and Nearest-Neighbor Oracles* (PDF originally hosted on Docdro — link no longer available)

🌐 **Live Demo:** [Interactive Voronoi Visualization](https://sauravbhattacharya001.github.io/VoronoiMap/)

## ✨ Features

- **Voronoi Region Estimation** — Approximate Voronoi cells via binary search along perpendicular bisectors
- **Adaptive Boundary Detection** — Auto-detect search space boundaries from data with configurable padding
- **KDTree Acceleration** — O(log n) nearest-neighbor lookups when SciPy is installed (falls back to O(n) brute-force)
- **Data Caching** — Files loaded once from disk and cached in memory for subsequent queries
- **Robust Geometry** — Cross-product collinearity tests, relative tolerance parallel-line detection, convergence-bounded loops
- **Security Hardened** — Path traversal protection, NaN/Inf rejection, malformed input handling
- **SVG Export** — Static SVG visualization with 6 color schemes
- **Interactive HTML Export** — Pan/zoom, hover tooltips, live color switching, dark/light theme toggle
- **GeoJSON Export** — Standard FeatureCollection for GIS tools (QGIS, Mapbox, Leaflet, Google Earth, ArcGIS)
- **CLI & API** — Full-featured command-line interface and importable Python API

## 🔧 Installation

### From PyPI (Recommended)

```bash
# Install the base package
pip install voronoimap

# Install with fast KDTree-accelerated nearest-neighbor lookups
pip install voronoimap[fast]
```

### From Source

```bash
# Clone the repository
git clone https://github.com/sauravbhattacharya001/VoronoiMap.git
cd VoronoiMap

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

**Dependencies:**

| Package | Required | Purpose |
|---------|----------|---------|
| Python 3.6+ | ✅ | Runtime |
| NumPy ≥ 1.20 | Optional | Array operations for KDTree |
| SciPy ≥ 1.7 | Optional | KDTree for fast NN lookups |
| pytest ≥ 7.0 | Dev only | Test runner |

## 🚀 Quick Start

### Data Format

Place data files in a `data/` directory. Each file contains one point per line with space-separated longitude and latitude:

```
100.5 200.3
450.2 750.1
800.0 500.0
```

### Command Line

```bash
# After pip install:
voronoimap datauni5.txt 5

# Multiple independent runs for better accuracy
voronoimap datauni5.txt 5 --runs 3

# Custom search space boundaries (south north west east)
voronoimap datauni5.txt 5 --bounds 0 500 0 1000

# Or run directly from source:
python vormap.py datauni5.txt 5

# Generate static SVG visualization
voronoimap datauni5.txt 5 --visualize diagram.svg --color-scheme rainbow

# Generate interactive HTML visualization (pan/zoom, tooltips, theme toggle)
voronoimap datauni5.txt 5 --interactive diagram.html

# Export as GeoJSON for GIS tools (QGIS, Mapbox, Leaflet, etc.)
voronoimap datauni5.txt 5 --geojson voronoi.geojson

# GeoJSON without seed points (polygons only)
voronoimap datauni5.txt 5 --geojson voronoi.geojson --no-seeds

# GeoJSON with explicit CRS
voronoimap datauni5.txt 5 --geojson voronoi.geojson --crs "urn:ogc:def:crs:EPSG::4326"
```

### Python API

```python
from vormap import get_sum, find_area, get_NN, load_data, set_bounds

# Estimate the number of objects in a dataset
estimated_count, max_edges, avg_edges = get_sum("datauni5.txt", 5)
print(f"Estimated {estimated_count} objects")

# Load data for direct function calls
data = load_data("datauni5.txt")

# Find area of a single Voronoi region
area, num_vertices = find_area(data, 100.5, 200.3)
print(f"Region area: {area}, vertices: {num_vertices}")

# Query nearest neighbor
nearest_lng, nearest_lat = get_NN(data, 500.0, 500.0)

# Manually set search bounds
set_bounds(south=0, north=500, west=0, east=1000)
```

### Visualization

```python
import vormap
import vormap_viz

# Load data and compute regions
data = vormap.load_data("datauni5.txt")
regions = vormap_viz.compute_regions(data)

# Static SVG export
vormap_viz.export_svg(regions, data, "diagram.svg", color_scheme="rainbow")

# Interactive HTML export (pan/zoom, hover tooltips, color switching, dark mode)
vormap_viz.export_html(regions, data, "diagram.html", title="My Voronoi Diagram")

# GeoJSON export for GIS tools
vormap_viz.export_geojson(regions, data, "voronoi.geojson")

# GeoJSON without seed points, with custom CRS
vormap_viz.export_geojson(regions, data, "voronoi.geojson",
                          include_seeds=False,
                          crs_name="urn:ogc:def:crs:EPSG::4326")

# One-call GeoJSON generation
vormap_viz.generate_geojson("datauni5.txt", "quick.geojson")

# One-call SVG generation
vormap_viz.generate_diagram("datauni5.txt", "quick.svg")
```

## 📚 API Reference

### Core Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_sum` | `(filename, n) → (estimate, max_edges, avg_edges)` | Core estimation algorithm. Samples `n` random points, builds Voronoi cells, returns estimated total count. |
| `load_data` | `(filename, auto_bounds=True) → [(lng, lat), ...]` | Load point data from `data/` directory. Caches results. Auto-detects search bounds. |
| `find_area` | `(data, lng, lat) → (area, vertices)` | Compute the Voronoi region area around the nearest neighbor of the given coordinates. |
| `get_NN` | `(data, lng, lat) → (lng, lat)` | Find nearest neighbor. Uses KDTree O(log n) if SciPy available, O(n) otherwise. |
| `set_bounds` | `(south, north, west, east) → None` | Manually set the search space boundaries. |
| `compute_bounds` | `(points, padding=0.1) → (s, n, w, e)` | Compute bounds from points with padding. |

### Visualization Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `compute_regions` | `(data) → {seed: [(x,y), ...]}` | Compute Voronoi region polygons for all seed points. Uses SciPy when available. |
| `export_svg` | `(regions, data, path, **opts) → path` | Export static SVG with color schemes, labels, and custom dimensions. |
| `export_html` | `(regions, data, path, **opts) → path` | Export interactive HTML with pan/zoom, hover tooltips, live color switching, and dark/light theme. |
| `export_geojson` | `(regions, data, path, **opts) → path` | Export GeoJSON FeatureCollection for GIS tools (QGIS, Mapbox, Leaflet, Google Earth). |
| `generate_diagram` | `(datafile, path, **opts) → path` | One-call convenience: load → compute → export SVG. |
| `generate_geojson` | `(datafile, path, **opts) → path` | One-call convenience: load → compute → export GeoJSON. |
| `list_color_schemes` | `() → [str, ...]` | List available color schemes (pastel, warm, cool, earth, mono, rainbow). |

### Geometry Helpers

| Function | Description |
|----------|-------------|
| `mid_point(x1, y1, x2, y2)` | Midpoint of two points |
| `perp_dir(x1, y1, x2, y2)` | Perpendicular slope to a line segment |
| `collinear(x1, y1, x2, y2, x3, y3)` | Cross-product collinearity test |
| `eudist(x1, y1, x2, y2)` | Euclidean distance |
| `polygon_area(lngs, lats)` | Shoelace formula for polygon area |

## ⚙️ Algorithm

```
┌─────────────────────────────────────────────────┐
│  1. Sample random point (px, py) in bounds      │
│  2. Query oracle: nearest neighbor → (nx, ny)   │
│  3. Binary search along perpendicular bisectors  │
│     to find Voronoi region boundary vertices     │
│  4. Compute region area via Shoelace formula     │
│  5. Estimate = total_area / cell_area            │
│  6. Average over N samples                       │
└─────────────────────────────────────────────────┘
```

**Key Parameters:**

| Parameter | Default | Description |
|-----------|---------|-------------|
| `IND_S / IND_N` | 0.0 / 1000.0 | South / North search bounds |
| `IND_W / IND_E` | 0.0 / 2000.0 | West / East search bounds |
| `BIN_PREC` | 1e-6 | Binary search convergence threshold |
| `MAX_VERTICES` | 50 | Max vertices per Voronoi cell |
| `MAX_RETRIES` | 50 | Max retry attempts for estimation |
| `BIN_SEARCH_MAX_ITER` | 100 | Binary search iteration limit |

## 🏗️ Project Structure

```
VoronoiMap/
├── vormap.py                    # Core algorithm implementation
├── vormap_viz.py                # SVG + interactive HTML visualization
├── requirements.txt             # Optional dependencies
├── .coveragerc                  # Coverage configuration
├── LICENSE                      # MIT License
├── data/                        # Point data files (not tracked)
├── docs/
│   └── index.html               # Interactive demo page (GitHub Pages)
├── tests/
│   ├── test_vormap.py           # Unit & security tests
│   └── test_vormap_viz.py       # Visualization tests (SVG + HTML)
└── .github/
    ├── copilot-instructions.md  # Copilot agent context
    ├── copilot-setup-steps.yml  # Copilot agent setup
    └── workflows/
        ├── ci.yml               # CI: lint + test + coverage
        └── pages.yml            # GitHub Pages deployment
```

## 🧪 Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=vormap --cov-report=term-missing

# Run only security tests
python -m pytest tests/test_vormap.py -v -k "PathTraversal or MalformedInput"
```

## 🔒 Security

VoronoiMap includes security hardening in its data loading pipeline:

- **Path traversal protection** — Filenames are validated to prevent directory escape (`../`, absolute paths)
- **Input sanitization** — NaN, Inf, and non-numeric coordinates are silently skipped
- **Bounded iterations** — All loops have explicit iteration limits to prevent denial-of-service
- **Convergence guards** — Binary search and vertex discovery have failsafes against infinite loops

## 🤝 Contributing

Contributions are welcome! See the **[Contributing Guide](CONTRIBUTING.md)** for full details on setup, code style, testing, and submitting PRs.

**Quick start:**

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes with tests
4. Run the test suite (`python -m pytest tests/ -v`)
5. Commit and push (`git push origin feature/my-feature`)
6. Open a Pull Request

## 📝 Tech Stack

- **Language:** Python 3.6+
- **Algorithms:** Voronoi partitioning, binary search, nearest-neighbor oracle, Shoelace formula
- **Performance:** SciPy KDTree for O(log n) lookups
- **Testing:** pytest + pytest-cov + Codecov
- **CI/CD:** GitHub Actions (lint, test, coverage, Pages deployment)

## 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

<p align="center">
  <sub>Built with 🧮 by <a href="https://github.com/sauravbhattacharya001">Saurav Bhattacharya</a></sub>
</p>
