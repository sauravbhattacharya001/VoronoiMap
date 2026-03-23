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
- **Region Statistics** — Per-region metrics (area, perimeter, centroid, compactness), aggregate summary, CSV/JSON export
- **Density Heatmap** — Color Voronoi cells by density, area, compactness, or vertex count with 3 color ramps (hot/cold, viridis, plasma); SVG and interactive HTML export
- **Lloyd Relaxation** — Iterative centroid smoothing for uniform tessellations with animated HTML visualization
- **Neighbourhood Graph** — Delaunay dual adjacency extraction with 14 graph metrics, degree distribution, clustering coefficient


## Module Catalog (53 Modules)

VoronoiMap has grown into a comprehensive spatial analysis toolkit. Here is every module organized by category:

### Core

| Module | Description |
|--------|-------------|
| `vormap` | Core algorithm: EstimateSUM, Voronoi construction, boundary detection, KDTree acceleration |
| `vormap_viz` | SVG + interactive HTML visualization with color schemes and dark/light themes |
| `vormap_geometry` | Computational geometry primitives: area, centroid, perimeter, intersection, clipping |
| `vormap_seeds` | Seed point generators: grid, hexagonal, random, Poisson disk, clustered |
| `vormap_generate` | Synthetic point pattern generator: 6 patterns, NNI stats, CLI |
| `vormap_pipeline` | Pipeline runner: chain multiple analysis steps into automated workflows |

### Visualization & Export

| Module | Description |
|--------|-------------|
| `vormap_heatmap` | Density heatmap: color cells by density, area, compactness with 3 color ramps |
| `vormap_color` | Map coloring: four-color theorem for conflict-free cell coloring |
| `vormap_animate` | Animated HTML visualization of diagram evolution over time |
| `vormap_mosaic` | Image mosaic filter: stained-glass and pixel-art effects |
| `vormap_report` | Self-contained HTML analysis report with stats, histograms, region table |
| `vormap_geojson` | GeoJSON export (RFC 7946) for GIS tools (QGIS, Mapbox, Leaflet, ArcGIS) |
| `vormap_kml` | KML export for Google Earth visualization |
| `vormap_gpx` | GPX import/export: load waypoints/tracks/routes, export points for GPS devices |

### Spatial Statistics

| Module | Description |
|--------|-------------|
| `vormap_autocorr` | Spatial autocorrelation: Moran's I, Geary's C, local indicators (LISA) |
| `vormap_hotspot` | Hotspot detection: Getis-Ord Gi* statistic, significance testing |
| `vormap_pattern` | Point pattern analysis: Clark-Evans NNI, Ripley's K/L, quadrat analysis |
| `vormap_nndist` | Nearest-neighbor distance analysis: distribution, expected vs observed |
| `vormap_outlier` | Spatial outlier detection: z-score and IQR methods on cell metrics |
| `vormap_trend` | Trend surface analysis: 1st/2nd/3rd order polynomial OLS fitting |
| `vormap_variogram` | Semivariogram analysis: empirical variogram, model fitting, kriging support |
| `vormap_kde` | Kernel density estimation: Gaussian KDE with Silverman/Scott bandwidth |
| `vormap_regularity` | Voronoi entropy, Lewis's law, Aboav-Weaire law, regularity index |
| `vormap_montecarlo` | Monte Carlo spatial simulation: CSR testing, confidence envelopes |

### Graph & Network

| Module | Description |
|--------|-------------|
| `vormap_graph` | Neighbourhood graph: adjacency extraction, 14 structural metrics |
| `vormap_network` | Delaunay network: MST, betweenness centrality, components, efficiency |
| `vormap_edge` | Edge analysis: boundary lengths, shared edges, edge density |

### Shape & Geometry

| Module | Description |
|--------|-------------|
| `vormap_shape` | Cell shape analysis: IPQ compactness, elongation, rectangularity, orientation |
| `vormap_hull` | Convex hull and bounding geometry: area ratios, convexity metrics |
| `vormap_clip` | Region clipping: clip Voronoi cells to arbitrary polygon boundaries |
| `vormap_power` | Weighted (power) Voronoi diagrams: site weights affect cell sizes |
| `vormap_relax` | Lloyd's relaxation: iterative centroid smoothing for uniform tessellations |
| `vormap_stability` | Stability analysis: sensitivity to point perturbation |

### Spatial Operations

| Module | Description |
|--------|-------------|
| `vormap_interp` | Spatial interpolation: natural neighbor weights for smooth surfaces |
| `vormap_mapalgebra` | Map algebra: spatial operations (add/multiply/reclassify) on cell layers |
| `vormap_merge` | Region merger: merge adjacent cells by attribute similarity |
| `vormap_contour` | Contour extraction: isolines, IDW interpolation, 4 colormaps |
| `vormap_query` | Spatial queries: KD-tree nearest-neighbor, k-nearest, radius, point location |
| `vormap_sample` | Spatial sampling: stratified, systematic, centroid, boundary, density-weighted |
| `vormap_transect` | Transect profiler: cross-section analysis along lines through the diagram |
| `vormap_compare` | Diagram comparison: seed displacement, area change, topology difference |

### Clustering & Classification

| Module | Description |
|--------|-------------|
| `vormap_cluster` | Spatial clustering: threshold, DBSCAN, agglomerative methods |
| `vormap_classify` | Data classification: equal interval, quantile, natural breaks (Jenks), std dev |

### Simulation & Modeling

| Module | Description |
|--------|-------------|
| `vormap_diffusion` | Spatial diffusion: heat equation, SIR epidemic, threshold cascade |
| `vormap_automata` | Cellular automata: Game of Life, majority, forest fire, epidemic on irregular grids |
| `vormap_gravity` | Gravity models: classic, Huff, Hansen, doubly-constrained spatial interaction |
| `vormap_watershed` | Watershed & flow: flow direction, accumulation, basin delineation |

### Planning & Territory

| Module | Description |
|--------|-------------|
| `vormap_territory` | Territorial analysis: dominance, border pressure, balance metrics |
| `vormap_landscape` | Landscape ecology: FRAGSTATS-style patch/class/landscape metrics |
| `vormap_access` | Spatial accessibility: travel time, service area coverage |
| `vormap_coverage` | Coverage analysis: service gaps, overlap, optimal site placement |
| `vormap_siting` | Facility siting: gap filling, demand-weighted, max-min distance placement |
| `vormap_pathplan` | Path planning: obstacle-aware navigation using Voronoi edge roadmaps |
| `vormap_fractal` | Fractal dimension analysis of boundary complexity |
| `vormap_temporal` | Temporal dynamics: track diagram evolution over time series |


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

# Compute per-region statistics (area, perimeter, centroid, compactness)
voronoimap datauni5.txt 5 --stats

# Export statistics as CSV or JSON
voronoimap datauni5.txt 5 --stats-csv stats.csv
voronoimap datauni5.txt 5 --stats-json stats.json

# Lloyd relaxation — smooth Voronoi regions toward uniformity
voronoimap datauni5.txt 5 --relax 10

# Animated relaxation visualization (play/pause, step slider, convergence graph)
voronoimap datauni5.txt 5 --relax-animate relaxation.html

# Neighbourhood graph analysis
voronoimap datauni5.txt 5 --graph

# Export graph as JSON, CSV, or SVG overlay
voronoimap datauni5.txt 5 --graph-json graph.json
voronoimap datauni5.txt 5 --graph-csv graph.csv
voronoimap datauni5.txt 5 --graph-svg graph.svg --graph-labels
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

### Region Statistics

### Density Heatmap

Color Voronoi cells by spatial metrics to visualize clustering and uniformity:

```bash
# SVG heatmap colored by point density (inverse area)
voronoimap datauni5.txt 5 --heatmap heatmap.svg

# Interactive HTML with metric/ramp switching and hover tooltips
voronoimap datauni5.txt 5 --heatmap-html heatmap.html

# Color by compactness instead of density
voronoimap datauni5.txt 5 --heatmap heatmap.svg --heatmap-metric compactness

# Use viridis color ramp and show values in cells
voronoimap datauni5.txt 5 --heatmap heatmap.svg --heatmap-ramp viridis --heatmap-values
```

**Available metrics:** `density` (default — inverse area, hot = clustered), `area`, `compactness` (isoperimetric quotient), `vertices`

**Available color ramps:** `hot_cold` (default — blue → white → red), `viridis`, `plasma`

```python
# Python API
import vormap, vormap_viz, vormap_heatmap

data = vormap.load_data("datauni5.txt")
regions = vormap_viz.compute_regions(data)
vormap_heatmap.export_heatmap_svg(regions, data, "heatmap.svg", metric="density")
vormap_heatmap.export_heatmap_html(regions, data, "heatmap.html")
```

```python
import vormap
import vormap_viz

data = vormap.load_data("datauni5.txt")
regions = vormap_viz.compute_regions(data)

# Per-region metrics: area, perimeter, centroid, compactness, vertex count
region_stats = vormap_viz.compute_region_stats(regions, data)
for stat in region_stats:
    print(f"Seed ({stat['seed_x']:.1f}, {stat['seed_y']:.1f}): "
          f"area={stat['area']:.2f}, compactness={stat['compactness']:.3f}")

# Aggregate summary (mean/median/min/max/std area, coefficient of variation)
summary = vormap_viz.compute_summary_stats(region_stats)
print(f"Mean area: {summary['mean_area']:.2f}, CV: {summary['cv']:.3f}")

# Export as CSV, JSON, or formatted table
vormap_viz.export_stats_csv(region_stats, "stats.csv")
vormap_viz.export_stats_json(region_stats, "stats.json")
print(vormap_viz.format_stats_table(region_stats))

# One-call convenience
vormap_viz.generate_stats("datauni5.txt", "stats.csv", fmt="csv")
```

### Lloyd Relaxation

```python
import vormap
import vormap_viz

data = vormap.load_data("datauni5.txt")

# Iterative centroid-based smoothing (Lloyd's algorithm)
# Moves seeds toward region centroids for more uniform tessellations
result = vormap_viz.lloyd_relaxation(data, iterations=10)
print(f"Converged: {result['converged']}, final movement: {result['movements'][-1]:.6f}")

# Access full history of seed positions at each iteration
for i, step in enumerate(result['history']):
    print(f"Iteration {i}: max_movement={result['movements'][i]:.6f}")

# One-call: generate relaxed SVG diagram
vormap_viz.generate_relaxed_diagram("datauni5.txt", "relaxed.svg", iterations=15)

# Animated HTML visualization with play/pause, speed control, step slider,
# convergence graph, ghost dots, color schemes, keyboard shortcuts
vormap_viz.export_relaxation_html(data, result, "relaxation.html")
```

### Neighbourhood Graph

```python
import vormap
import vormap_viz

data = vormap.load_data("datauni5.txt")
regions = vormap_viz.compute_regions(data)

# Extract adjacency graph (Delaunay dual via shared Voronoi edges)
graph = vormap_viz.extract_neighborhood_graph(regions, data)

# 14 graph metrics: density, clustering coefficient, diameter, avg path length,
# degree distribution, connected components, isolated/leaf nodes, etc.
stats = vormap_viz.compute_graph_stats(graph)
print(f"Density: {stats['density']:.3f}, Clustering: {stats['clustering_coefficient']:.3f}")
print(f"Components: {stats['num_components']}, Diameter: {stats['diameter']}")

# Export as JSON (nodes with degree/neighbors, edges with lengths, stats)
vormap_viz.export_graph_json(graph, "graph.json")

# Export as CSV edge list
vormap_viz.export_graph_csv(graph, "graph.csv")

# SVG overlay: Voronoi regions + red graph edges + node markers
vormap_viz.export_graph_svg(regions, data, graph, "graph.svg", show_labels=True)

# Formatted stats table with degree histogram
print(vormap_viz.format_graph_stats_table(graph))

# One-call convenience
vormap_viz.generate_graph("datauni5.txt", "graph.json", fmt="json")
```

## 📚 API Reference

> **Full API documentation:** See [docs/API.md](docs/API.md) for complete reference of all 25 modules and 150+ functions.

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

### Statistics Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `compute_region_stats` | `(regions, data) → [dict, ...]` | Per-region metrics: area, perimeter, centroid, compactness (isoperimetric quotient), vertex count, avg edge length. |
| `compute_summary_stats` | `(region_stats) → dict` | Aggregate summary: mean/median/min/max/std area, coefficient of variation, coverage ratio. |
| `export_stats_csv` | `(region_stats, path, **opts) → None` | Export region statistics as CSV with optional summary section. |
| `export_stats_json` | `(region_stats, path, **opts) → None` | Export region statistics as JSON with optional summary. |
| `format_stats_table` | `(region_stats, **opts) → str` | Format statistics as a human-readable text table. |
| `generate_stats` | `(datafile, path, **opts) → dict` | One-call convenience: load → compute → export stats (table/csv/json). |

### Lloyd Relaxation Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `lloyd_relaxation` | `(data, iterations, **opts) → dict` | Iterative centroid smoothing. Returns history, movements, convergence flag. Supports bounds clamping, early termination, callbacks. |
| `generate_relaxed_diagram` | `(datafile, path, **opts) → path` | One-call: load → relax → export SVG of the relaxed diagram. |
| `export_relaxation_html` | `(data, result, path, **opts) → path` | Animated HTML: play/pause, speed control, step slider, convergence graph, ghost dots, color schemes, dark/light theme, keyboard shortcuts. |

### Neighbourhood Graph Functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `extract_neighborhood_graph` | `(regions, data, **opts) → dict` | Extract adjacency graph from Voronoi regions (Delaunay dual via shared edges). |
| `compute_graph_stats` | `(graph) → dict` | 14 graph metrics: density, clustering coefficient, diameter, avg path length, degree distribution, connected components, isolated/leaf nodes. |
| `export_graph_json` | `(graph, path, **opts) → None` | Export graph as JSON (nodes with degree/neighbors, edges with lengths, stats). |
| `export_graph_csv` | `(graph, path) → None` | Export graph as CSV edge list with summary. |
| `export_graph_svg` | `(regions, data, graph, path, **opts) → path` | SVG overlay: Voronoi regions + red graph edges + node markers + optional degree labels. |
| `format_graph_stats_table` | `(graph) → str` | Formatted stats table with degree histogram. |
| `generate_graph` | `(datafile, path, **opts) → dict` | One-call convenience: load → compute → export graph (table/json/csv). |

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

## Project Structure

```
VoronoiMap/
├── vormap.py                    # Core algorithm implementation
├── vormap_viz.py                # SVG + interactive HTML visualization
├── vormap_*.py (50 modules)     # Spatial analysis toolkit (see catalog above)
├── test_*.py (14 test files)    # Unit & integration tests
├── requirements.txt             # Optional dependencies
├── pyproject.toml               # Package configuration
├── Dockerfile                   # Container build
├── LICENSE                      # MIT License
├── data/                        # Point data files
├── docs/
│   └── index.html               # Interactive demo (GitHub Pages)
└── .github/
    ├── copilot-instructions.md  # Copilot agent context
    ├── copilot-setup-steps.yml  # Copilot agent setup
    ├── dependabot.yml           # Dependency updates
    └── workflows/
        ├── ci.yml               # CI: lint + test + coverage
        ├── codeql.yml           # Security analysis
        ├── docker.yml           # Docker build & push
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
