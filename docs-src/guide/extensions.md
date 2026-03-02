# Extension Modules API

VoronoiMap ships with 16 extension modules beyond the core `vormap` module.
Each adds specialized spatial analysis, visualization, or export capability.

---

## Visualization — `vormap_viz`

The primary visualization module. Computes Voronoi regions and exports to
SVG, HTML, GeoJSON, and statistics formats.

### `compute_regions(data)`

Compute Voronoi region polygons for all seed points. Uses SciPy when
available, otherwise falls back to a manual tracing algorithm.

```python
from vormap import load_data
from vormap_viz import compute_regions

data = load_data("mypoints.txt")
regions = compute_regions(data)
# regions[i] = list of (x, y) vertices for the i-th seed
```

**Returns:** `list[list[tuple]]` — One polygon (vertex list) per seed.

### `export_svg(regions, data, output_path, ...)`

Export Voronoi regions as an SVG file with customizable color schemes.

```python
export_svg(regions, data, "output.svg", color_scheme="viridis", width=1000, height=800)
```

### `export_html(regions, data, output_path, ...)`

Export an interactive HTML visualization with tooltips and region
highlighting.

### `export_geojson(regions, data, output_path, ...)`

Export Voronoi regions as a GeoJSON FeatureCollection — useful for
integration with Leaflet, Mapbox, or QGIS.

### `compute_region_stats(regions, data)`

Compute per-region statistics: area, perimeter, vertex count,
isoperimetric quotient, centroid coordinates.

```python
stats = compute_region_stats(regions, data)
for s in stats:
    print(f"Region {s['index']}: area={s['area']:.1f}, vertices={s['vertices']}")
```

### `compute_summary_stats(region_stats)`

Aggregate summary (mean, median, std, min, max) over all region statistics.

### `lloyd_relaxation(data, iterations=10, *, bounds=None, callback=None)`

Apply Lloyd relaxation to make Voronoi regions more uniform. Iteratively
replaces each seed with its region's centroid.

```python
relaxed = lloyd_relaxation(data, iterations=20)
```

### Convenience Functions

| Function | Description |
|----------|-------------|
| `generate_diagram(datafile, output_path, ...)` | Load → compute → export SVG |
| `generate_geojson(datafile, output_path, ...)` | Load → compute → export GeoJSON |
| `generate_stats(datafile, output_path, ...)` | Load → compute → export stats |
| `generate_relaxed_diagram(datafile, output_path, iterations, ...)` | Load → relax → export |
| `export_relaxation_html(data, iterations, output_path, ...)` | Animated relaxation HTML |
| `format_stats_table(region_stats, ...)` | Human-readable stats table |
| `export_stats_csv(region_stats, output_path, ...)` | CSV export |
| `export_stats_json(region_stats, output_path, ...)` | JSON export |
| `list_color_schemes()` | List available color scheme names |

---

## Spatial Clustering — `vormap_cluster`

Cluster Voronoi cells by adjacency and spatial metrics.

### `cluster_regions(region_stats, regions, data, *, method="threshold", metric="area", ...)`

Cluster Voronoi cells using spatial adjacency and cell metrics.

```python
from vormap_cluster import cluster_regions

result = cluster_regions(stats, regions, data, method="dbscan", metric="area", eps=50.0)
print(f"{result.num_clusters} clusters found")
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `method` | `str` | `"threshold"` | Clustering algorithm: `"threshold"`, `"dbscan"`, `"agglomerative"` |
| `metric` | `str` | `"area"` | Metric to cluster on: `"area"`, `"perimeter"`, `"iq"`, `"vertices"` |
| `eps` | `float` | — | DBSCAN epsilon / agglomerative distance threshold |
| `min_cluster_size` | `int` | `2` | Minimum regions per cluster |

### `generate_clusters(datafile, output_path=None, *, ...)`

One-call convenience: load data, cluster, optionally export.

### Export Functions

| Function | Description |
|----------|-------------|
| `export_cluster_json(result, output_path)` | JSON export |
| `export_cluster_svg(result, regions, data, output_path, ...)` | SVG visualization |
| `format_cluster_table(result)` | Human-readable table |

---

## Spatial Queries — `vormap_query`

Build a spatial index over Voronoi seeds for efficient nearest-neighbor
and point-in-region queries.

### `VoronoiIndex` class

```python
from vormap_query import VoronoiIndex

# VoronoiIndex is created via run_query_cli or by constructing with seeds/regions
```

| Method | Description |
|--------|-------------|
| `nearest(point)` | Return the nearest seed to *point* |
| `nearest_k(point, k)` | Return the *k* nearest seeds |
| `locate(point)` | Return the index of the Voronoi region containing *point* |
| `within_radius(point, radius)` | All seeds within *radius* of *point* |
| `distance_to_boundary(point)` | Minimum distance to nearest region boundary |
| `batch_query(points)` | Nearest-seed query for each point |
| `batch_locate(points)` | Locate each point's containing region |

### `query_stats(results)`

Compute statistics over a batch of query results (distances, region distribution).

### `coverage_analysis(points, index)`

Analyse how many query points fall in each Voronoi region — useful for
understanding spatial coverage and identifying underserved areas.

### Export Functions

| Function | Description |
|----------|-------------|
| `export_query_json(results, path)` | JSON export |
| `export_query_svg(seeds, queries, results, path, ...)` | SVG visualization |

---

## Heatmaps — `vormap_heatmap`

Density heatmap generation for Voronoi diagrams.

### `export_heatmap_svg(regions, data, output_path, ...)`

Export a density heatmap SVG colored by a region metric (area, vertex count,
isoperimetric quotient, or perimeter).

```python
from vormap_heatmap import export_heatmap_svg

export_heatmap_svg(regions, data, "heatmap.svg", metric="area", ramp="viridis")
```

### `export_heatmap_html(regions, data, output_path, ...)`

Export an interactive HTML heatmap with tooltips and color-ramp switching.

---

## Outlier Detection — `vormap_outlier`

Detect spatial outliers in Voronoi region statistics using Z-score or IQR methods.

### `detect_outliers(stats, metrics=None, method="zscore", threshold=None)`

```python
from vormap_outlier import detect_outliers

result = detect_outliers(stats, metrics=["area", "iq"], method="iqr")
print(f"{result.outlier_count} outliers ({result.outlier_rate:.1%})")
print(result.format_report())
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `metrics` | `list[str]` | all | Metrics to check: `"area"`, `"perimeter"`, `"iq"`, `"vertices"` |
| `method` | `str` | `"zscore"` | Detection method: `"zscore"` or `"iqr"` |
| `threshold` | `float` | varies | Z-score threshold (default 2.0) or IQR multiplier (default 1.5) |

### Export Functions

| Function | Description |
|----------|-------------|
| `export_outlier_json(result, path)` | JSON export |
| `export_outlier_svg(result, regions, data, path, ...)` | SVG with outliers highlighted |
| `export_outlier_csv(result, path)` | CSV export |

---

## Spatial Interpolation — `vormap_interp`

Interpolate scalar values associated with Voronoi seed points.

### `nearest_interp(points, values, query)`

Return the value of the nearest seed point (piecewise constant).

### `idw_interp(points, values, query, power=2.0, epsilon=1e-12)`

Inverse distance weighted interpolation.

```python
from vormap_interp import idw_interp

result = idw_interp(
    points=[(0, 0), (10, 0), (5, 8)],
    values=[100, 200, 150],
    query=(5, 3),
    power=2.0
)
```

### `natural_neighbor_interp(points, values, query, fallback_idw=True)`

Sibson natural neighbor interpolation using stolen area weights.
Falls back to IDW if SciPy is unavailable.

### `grid_interpolate(points, values, nx=50, ny=50, bounds=None, method="idw", power=2.0)`

Interpolate values over a regular grid. Supports `"nearest"`, `"idw"`,
and `"natural"` methods.

### Export Functions

| Function | Description |
|----------|-------------|
| `export_surface_svg(grid_result, output_path, ...)` | SVG surface heatmap |
| `export_surface_csv(grid_result, output_path)` | CSV grid export |

---

## Spatial Pattern Analysis — `vormap_pattern`

Statistical tests for spatial randomness, clustering, and dispersion.

### `clark_evans_nni(points, bounds=None)`

Compute the Clark-Evans Nearest Neighbor Index. Values < 1 indicate
clustering, > 1 indicate dispersion, ≈ 1 indicate spatial randomness.

### `ripleys_k(points, radii=None, n_radii=20, bounds=None)`

Compute Ripley's K function and Besag's L function at multiple radii.
L(r) − r > 0 indicates clustering at scale r.

### `quadrat_analysis(points, rows=None, cols=None, bounds=None)`

Perform quadrat (grid cell) analysis using a chi-squared test for
complete spatial randomness.

### `mean_center(points)`

Compute the mean center (geographic centroid) of a point pattern.

### `standard_distance(points)`

Compute the standard distance (spatial spread) of a point set.

### `convex_hull_ratio(points, bounds=None)`

Ratio of convex hull area to bounding box area — measures point
distribution compactness.

### `analyze_pattern(points, bounds=None, ...)`

Run all pattern analyses and return a combined `PatternSummary`.

```python
from vormap_pattern import analyze_pattern, format_pattern_report

summary = analyze_pattern(points)
print(format_pattern_report(summary))
```

---

## Region Clipping — `vormap_clip`

Clip Voronoi regions to arbitrary convex boundary polygons.

### Boundary Constructors

```python
from vormap_clip import make_rectangle, make_circle, make_ellipse, make_regular_polygon

rect = make_rectangle(0, 0, 100, 100)
circle = make_circle(center=(50, 50), radius=40)
ellipse = make_ellipse(center=(50, 50), rx=40, ry=25)
hexagon = make_regular_polygon(center=(50, 50), radius=40, sides=6)
```

### `clip_all_regions(regions, data, boundary, ...)`

Clip all Voronoi regions to a boundary polygon. Returns a `ClipResult`
with clipped regions, area statistics, and boundary info.

### `point_in_polygon(point, polygon)`

Ray-casting point-in-polygon test.

### `load_boundary(filepath)`

Load boundary polygon from a text file (one `x y` per line).

---

## Graph Coloring — `vormap_color`

Four-color map coloring using graph coloring algorithms.

### `color_voronoi(data_or_file, algorithm="dsatur", num_colors=4, palette=None)`

One-call convenience: compute regions, extract adjacency graph, color it.

```python
from vormap_color import color_voronoi

result = color_voronoi("mypoints.txt", num_colors=4, palette="pastel")
```

### `greedy_color(graph, num_colors=4)`

Greedy graph coloring with random vertex ordering.

### `dsatur_color(graph, num_colors=4)`

DSATUR algorithm — assigns colors based on saturation degree (number of
distinct colors used by neighbors). Produces better results than greedy.

### `validate_coloring(graph, coloring)`

Verify that no adjacent nodes share a color.

---

## Diagram Comparison — `vormap_compare`

Compare two Voronoi diagrams for structural and geometric similarity.

### `DiagramSnapshot`

```python
from vormap_compare import DiagramSnapshot

snap_a = DiagramSnapshot.from_datafile("data1.txt")
snap_b = DiagramSnapshot.from_datafile("data2.txt")
```

### `compare_diagrams(snap_a, snap_b, max_match_distance=None)`

Full comparison: seed matching, area deltas, topology differences,
similarity score, and human-readable verdict.

```python
result = compare_diagrams(snap_a, snap_b)
print(result.summary())
```

### `match_seeds(seeds_a, seeds_b, max_distance=None)`

Greedy nearest-neighbor seed matching between two diagrams.

### `compare_areas(stats_a, stats_b, mapping)`

Compare region areas between matched seeds — returns per-region deltas
and aggregate statistics.

### `compare_topology(regions_a, data_a, regions_b, data_b, mapping)`

Compare neighbourhood topology (adjacency graph) between two diagrams.

---

## Edge Network — `vormap_edge`

Extract and analyze the primal edge network from Voronoi region boundaries.

### `extract_edge_network(regions, *, tol=0.5)`

Extract unique edges (shared boundaries between adjacent regions).

### `compute_edge_stats(network)`

Aggregate statistics: total edges, mean/median/std length, length
distribution, degree statistics.

### Export Functions

| Function | Description |
|----------|-------------|
| `export_edge_csv(network, output_path, ...)` | CSV edge list |
| `export_edge_json(network, output_path)` | JSON export |
| `export_edge_svg(network, output_path, ...)` | SVG visualization |
| `format_edge_stats(stats)` | Human-readable table |

---

## Neighborhood Graph — `vormap_graph`

Extract and analyze the dual (adjacency) graph from Voronoi regions.

### `extract_neighborhood_graph(regions, data=None, *, tol=0.5)`

Build the adjacency graph — nodes are seed points, edges connect
seeds whose Voronoi regions share a boundary.

### `compute_graph_stats(graph)`

Compute degree distribution, clustering coefficient, diameter, and
connected component statistics.

### `generate_graph(datafile, output_path=None, *, fmt="table")`

One-call convenience: load → compute → export.

---

## KML Export — `vormap_kml`

Export Voronoi regions as KML for Google Earth / Google Maps.

### `export_kml(regions, data, output_path, ...)`

Export Voronoi regions as a KML file with region styling and metadata.

### `generate_kml(datafile, output_path, ...)`

One-call convenience: load → compute → export KML.

---

## Power Diagrams — `vormap_power`

Weighted Voronoi diagrams (also called power diagrams or Laguerre diagrams).

### `assign_weights(seeds, method='uniform', *, value=1.0, ...)`

Generate a weight vector. Methods: `"uniform"`, `"random"`,
`"distance_based"`, `"density_based"`, `"custom"`.

### `compute_power_diagram(seeds, weights, mode='power', bounds=None, resolution=200)`

Compute a weighted Voronoi diagram. Returns a `PowerDiagramResult` with
per-cell areas, centroids, and statistics.

```python
from vormap_power import assign_weights, compute_power_diagram

seeds = [(100, 200), (300, 400), (500, 100)]
weights = assign_weights(seeds, method='random')
result = compute_power_diagram(seeds, weights)
print(result.summary())
```

### `weight_effect_analysis(seeds, weights, mode='power', ...)`

Compare weighted diagram against a uniform-weight baseline to quantify
the effect of weights on region sizes.

### Distance Modes

| Mode | Formula | Description |
|------|---------|-------------|
| `"power"` | d² − w | Standard power distance |
| `"multiplicative"` | d / w | Weight scales distance |
| `"additive"` | d − w | Weight offsets distance |

---

## Seed Generation — `vormap_seeds`

Generate seed point distributions for testing and experimentation.

### Generators

| Function | Description |
|----------|-------------|
| `random_uniform(n, ...)` | Uniformly distributed random points |
| `grid(...)` | Regular rectangular grid |
| `hexagonal(...)` | Hexagonal (honeycomb) grid |
| `jittered_grid(...)` | Regular grid with random perturbation |
| `poisson_disk(...)` | Well-spaced points via Bridson's algorithm |
| `halton(n, ...)` | Low-discrepancy Halton sequence |

All generators accept `x_min`, `x_max`, `y_min`, `y_max` bounds and an
optional `seed` parameter for reproducibility.

```python
from vormap_seeds import poisson_disk, save_seeds

points = poisson_disk(min_distance=20.0, seed=42)
save_seeds(points, "my_seeds.txt")
```

### `save_seeds(points, filename, header=True)`

Save seed points to a text file compatible with `vormap.py`.

### `load_seeds(filename)`

Load seed points from a text file.

---

## Territory Analysis — `vormap_territory`

Analyze territorial properties of Voronoi diagrams: border regions, interior
regions, size inequality, compactness metrics.

### `analyze_territories(regions, data, bounds=None, boundary_tolerance=0.05)`

Run full territorial analysis.

```python
from vormap_territory import analyze_territories, format_territory_report

analysis = analyze_territories(regions, data)
print(format_territory_report(analysis))
```

**Returns** a dict with:

- Per-region metrics: area, perimeter, compactness, border/interior classification,
  shared border lengths
- Aggregate metrics: Gini coefficient, mean compactness, border-to-interior ratio
- Territory classification (fragmented, balanced, dominated)

### Export Functions

| Function | Description |
|----------|-------------|
| `export_territory_json(analysis, output_path)` | JSON export |
| `export_territory_csv(analysis, output_path)` | CSV per-region metrics |
| `format_territory_report(analysis)` | Human-readable report |
