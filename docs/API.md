# VoronoiMap API Reference

> Complete reference for all public functions and classes.

## Table of Contents

- [Core (`vormap`)](#vormap)
- [Access (`vormap_access`)](#vormap_access)
- [Autocorr (`vormap_autocorr`)](#vormap-autocorr)
- [Clip (`vormap_clip`)](#vormap-clip)
- [Cluster (`vormap_cluster`)](#vormap-cluster)
- [Color (`vormap_color`)](#vormap-color)
- [Compare (`vormap_compare`)](#vormap-compare)
- [Coverage (`vormap_coverage`)](#vormap_coverage)
- [Edge (`vormap_edge`)](#vormap-edge)
- [Generate (`vormap_generate`)](#vormap_generate)
- [GeoJSON (`vormap_geojson`)](#vormap_geojson)
- [Geometry (`vormap_geometry`)](#vormap-geometry)
- [Graph (`vormap_graph`)](#vormap-graph)
- [Heatmap (`vormap_heatmap`)](#vormap-heatmap)
- [Hotspot (`vormap_hotspot`)](#vormap_hotspot)
- [Hull (`vormap_hull`)](#vormap-hull)
- [Interp (`vormap_interp`)](#vormap-interp)
- [Kde (`vormap_kde`)](#vormap-kde)
- [Kml (`vormap_kml`)](#vormap-kml)
- [Landscape (`vormap_landscape`)](#vormap_landscape)
- [Merge (`vormap_merge`)](#vormap_merge)
- [Mosaic (`vormap_mosaic`)](#vormap_mosaic)
- [Network (`vormap_network`)](#vormap_network)
- [Nndist (`vormap_nndist`)](#vormap-nndist)
- [Outlier (`vormap_outlier`)](#vormap-outlier)
- [Pathplan (`vormap_pathplan`)](#vormap_pathplan)
- [Pattern (`vormap_pattern`)](#vormap-pattern)
- [Power (`vormap_power`)](#vormap-power)
- [Query (`vormap_query`)](#vormap-query)
- [Regularity (`vormap_regularity`)](#vormap_regularity)
- [Relax (`vormap_relax`)](#vormap_relax)
- [Report (`vormap_report`)](#vormap_report)
- [Sample (`vormap_sample`)](#vormap_sample)
- [Seeds (`vormap_seeds`)](#vormap-seeds)
- [Stability (`vormap_stability`)](#vormap-stability)
- [Temporal (`vormap_temporal`)](#vormap_temporal)
- [Territory (`vormap_territory`)](#vormap-territory)
- [Transect (`vormap_transect`)](#vormap_transect)
- [Trend (`vormap_trend`)](#vormap_trend)
- [Variogram (`vormap_variogram`)](#vormap_variogram)
- [Viz (`vormap_viz`)](#vormap-viz)

---

## vormap

| Function | Description |
|----------|-------------|
| `validate_input_path` | Validate a file path against path traversal attacks. Ensures *filepath* resolves to a location insid |
| `validate_output_path` | Validate an output file path against path traversal attacks. Works like :func:`validate_input_path`  |
| `compute_bounds` | Compute search space boundaries from a set of points. Returns (south, north, west, east) with *paddi |
| `set_bounds` | Manually set the search space boundaries (globals). |
| `sanitize_css_value` | Sanitize a string intended for use as a CSS property value. Prevents CSS injection attacks when user |
| `load_data` | Load point data from a file and cache it in memory. Returns a list of (lng, lat) tuples.  Subsequent |
| `get_NN` | Return the nearest neighbor (lng, lat) from pre-loaded point data. When *scipy* is available this us |
| `mid_point` | *(no description)* |
| `perp_dir` | *(no description)* |
| `collinear` | Test whether three points are collinear using the cross-product. The previous implementation compare |
| `new_dir` | *(no description)* |
| `isect` | *(no description)* |
| `isect_B` | Find two boundary intersection points for a line through (alng, alat). |
| `eudist_sq` | Return the *squared* Euclidean distance between two points. Avoids the expensive ``math.sqrt`` call. |
| `eudist` | Euclidean distance between two points. Uses ``math.hypot`` which is implemented in C and also avoids |
| `eudist_pts` | Euclidean distance between two points given as (x, y) tuples. Convenience wrapper around ``eudist``  |
| `bin_search` | Binary search for a Voronoi boundary point between two positions. *data* is a list of (lng, lat) tup |
| `find_CXY` | Find the clockwise boundary endpoint for a Voronoi edge ray. |
| `find_BXY` | Find the counter-clockwise boundary endpoint for a Voronoi edge ray. |
| `find_a1` | Find the next Voronoi vertex along the boundary from (alng, alat). |
| `polygon_area` | Calculate polygon area using the Shoelace formula. Uses a single zip-based loop to avoid per-iterati |
| `find_area` | Compute the area and vertex count of the Voronoi region for a data point. Traces the Voronoi region  |
| `get_sum` | Estimate the number of Voronoi regions by random point sampling. Loads the data file once via ``load |
| `main` | CLI entry point for VoronoiMap estimation. |

### Signatures

```python
def validate_input_path(filepath, *, base_dir=None, allow_absolute=False)
def validate_output_path(filepath, *, base_dir=None, allow_absolute=False)
def compute_bounds(points, padding=0.1)
def set_bounds(south, north, west, east)
def sanitize_css_value(value)
def load_data(filename, auto_bounds=True)
def get_NN(data, lng, lat)
def mid_point(x1, y1, x2, y2)
def perp_dir(x1, y1, x2, y2)
def collinear(x1, y1, x2, y2, x3, y3, eps=1e-8)
def new_dir(data, aplng, aplat, alng, alat, dlng, dlat)
def isect(x1, y1, x2, y2, x3, y3, x4, y4)
def isect_B(alng, alat, dirn)
def eudist_sq(x1, y1, x2, y2)
def eudist(x1, y1, x2, y2)
def eudist_pts(p1, p2)
def bin_search(data, x1, y1, x2, y2, dlng, dlat)
def find_CXY(B, dlng, dlat)
def find_BXY(B, dlng, dlat)
def find_a1(data, alng, alat, dlng, dlat, dirn)
def polygon_area(alng, alat)
def find_area(data, dlng, dlat)
def get_sum(FILENAME, N1, _depth=0)
def main()
```

## vormap_autocorr

*Spatial autocorrelation analysis for Voronoi-partitioned point data.*

| Function | Description |
|----------|-------------|
| `global_morans_i` | *(no description)* |
| `local_morans_i` | *(no description)* |
| `format_global_report` | Format a human-readable report for global Moran's I. |
| `format_lisa_summary` | Format a summary table for LISA results. |
| `export_autocorr_json` | *(no description)* |
| `export_lisa_svg` | *(no description)* |

### Signatures

```python
def global_morans_i( values: List[float],
def local_morans_i( values: List[float],
def format_global_report(result: GlobalMoranResult) -> str:
def format_lisa_summary(result: LISAResult) -> str:
def export_autocorr_json( global_result: GlobalMoranResult,
def export_lisa_svg( result: LISAResult,
```

## vormap_clip

*Voronoi Region Clipping — clip regions to arbitrary boundaries.*

| Function | Description |
|----------|-------------|
| `make_rectangle` | Create a rectangular boundary polygon (CCW winding). |
| `make_circle` | Create a circular boundary approximated as a regular polygon. |
| `make_ellipse` | Create an elliptical boundary. |
| `make_regular_polygon` | Create a regular polygon (hexagon, octagon, etc.). |
| `point_in_polygon` | Ray-casting point-in-polygon test. |
| `clip_polygon` | Clip a subject polygon against a convex clip polygon. Uses the Sutherland-Hodgman algorithm. The cli |
| `clip_region` | Clip a single Voronoi region to a boundary polygon. Returns the clipped polygon vertices, or empty l |
| `clip_all_regions` | *(no description)* |
| `export_clip_json` | Export clipping results as JSON. |
| `export_clip_svg` | *(no description)* |
| `load_boundary` | Load boundary polygon from a file (one 'x y' per line). |
| `main` | CLI entry point for clipping. |

### Signatures

```python
def make_rectangle(x_min: float, y_min: float, x_max: float, y_max: float) -> Polygon:
def make_circle(center: Point, radius: float, segments: int = 64) -> Polygon:
def make_ellipse(center: Point, rx: float, ry: float, segments: int = 64) -> Polygon:
def make_regular_polygon(center: Point, radius: float, sides: int, rotation: float = 0.0) -> Polygon:
def point_in_polygon(point: Point, polygon: Polygon) -> bool:
def clip_polygon(subject: Polygon, clip: Polygon) -> Polygon:
def clip_region(vertices: Polygon, boundary: Polygon) -> Polygon:
def clip_all_regions(regions: Regions, data: list, boundary: Polygon,
def export_clip_json(result: ClipResult, output_path: str) -> None:
def export_clip_svg(result: ClipResult, output_path: str, *,
def load_boundary(filepath: str) -> Polygon:
def main(argv=None)
```

## vormap_cluster

*Spatial clustering for Voronoi diagrams.*

| Function | Description |
|----------|-------------|
| `cluster_regions` | *(no description)* |
| `export_cluster_json` | Export clustering results to a JSON file. Parameters ---------- result : ClusterResult Output of ``c |
| `export_cluster_svg` | *(no description)* |
| `format_cluster_table` | Format clustering results as a human-readable text table. Parameters ---------- result : ClusterResu |
| `generate_clusters` | *(no description)* |

### Signatures

```python
def cluster_regions(region_stats, regions, data, *, method="threshold", metric="area", value_range=None, min_neighbors=2, num_clusters=3)
def export_cluster_json(result, output_path)
def export_cluster_svg(result, regions, data, output_path, *, width=800, height=600, show_labels=False, title=None)
def format_cluster_table(result)
def generate_clusters(datafile, output_path=None, *, method="threshold", metric="area", value_range=None, min_neighbors=2, num_clusters=3, fmt="table")
```

## vormap_color

*Map coloring for Voronoi diagrams — four-color theorem implementation.*

| Function | Description |
|----------|-------------|
| `greedy_color` | Assign colors to graph nodes using greedy graph coloring. Uses Welsh-Powell ordering (nodes sorted b |
| `dsatur_color` | DSATUR algorithm — assigns colors based on saturation degree. At each step, picks the uncolored vert |
| `validate_coloring` | Check that no adjacent nodes share a color. Parameters ---------- graph : dict Maps node_id -> list  |
| `color_voronoi` | One-call convenience: compute regions, extract graph, color it. Parameters ---------- data_or_file : |
| `export_colored_svg` | *(no description)* |
| `main` | CLI entry point. |

### Signatures

```python
def greedy_color(graph, num_colors=4)
def dsatur_color(graph, num_colors=4)
def validate_coloring(graph, coloring)
def color_voronoi(data_or_file, algorithm="dsatur", num_colors=4, palette=None)
def export_colored_svg( data_or_file, output_path, *, algorithm="dsatur", num_colors=4, palette=None, width=800, height=600, show_seeds=True, show_labels=False, stroke_color="#333", stroke_width=1, )
def main(args=None)
```

## vormap_compare

*Voronoi Diagram Comparison & Difference Analysis.*

| Function | Description |
|----------|-------------|
| `match_seeds` | Match seeds between two diagrams using greedy nearest-neighbor. Sorts all pairwise distances and gre |
| `compare_areas` | Compare region areas between matched seeds. Parameters ---------- stats_a : list of dict Region stat |
| `compare_topology` | Compare neighbourhood topology between two diagrams. Extracts neighbourhood graphs for both diagrams |
| `compare_diagrams` | Compare two Voronoi diagram snapshots. Parameters ---------- snap_a : DiagramSnapshot First diagram. |
| `export_comparison_json` | Export comparison result to JSON file. Parameters ---------- result : ComparisonResult output_path : |

### Signatures

```python
def match_seeds(seeds_a, seeds_b, max_distance=None)
def compare_areas(stats_a, stats_b, mapping)
def compare_topology(regions_a, data_a, regions_b, data_b, mapping)
def compare_diagrams(snap_a, snap_b, max_match_distance=None)
def export_comparison_json(result, output_path)
```

## vormap_edge

*Voronoi Edge Network — extract and analyse polygon boundary edges.*

| Function | Description |
|----------|-------------|
| `extract_edge_network` | Extract the primal edge network from Voronoi regions. Walks every region polygon, merges near-duplic |
| `compute_edge_stats` | Compute aggregate statistics for the edge network. Parameters ---------- network : dict Output of `` |
| `format_edge_stats` | Format edge-network statistics as a human-readable text table. Parameters ---------- stats : dict Ou |
| `export_edge_csv` | Export edge list as CSV with optional summary header. Parameters ---------- network : dict Output of |
| `export_edge_json` | Export edge network as structured JSON. Parameters ---------- network : dict Output of ``extract_edg |
| `export_edge_svg` | *(no description)* |

### Signatures

```python
def extract_edge_network(regions, *, tol=0.5)
def compute_edge_stats(network)
def format_edge_stats(stats)
def export_edge_csv(network, output_path, *, include_stats=True)
def export_edge_json(network, output_path)
def export_edge_svg( network, output_path, *, width=800, height=600, show_vertices=True, show_boundary=True, title=None, )
```

## vormap_geometry

*Shared geometry helpers used across VoronoiMap extension modules.*

| Function | Description |
|----------|-------------|
| `polygon_area` | Compute polygon area using the Shoelace formula. Parameters ---------- vertices : list[tuple[float,  |
| `polygon_perimeter` | Compute the perimeter of a polygon. Parameters ---------- vertices : list[tuple[float, float]] Order |
| `polygon_centroid` | Centroid of a simple polygon using the Shoelace-weighted formula. Parameters ---------- vertices : l |
| `isoperimetric_quotient` | Compute the isoperimetric quotient (circularity measure). IQ = 4 * pi * area / perimeter^2 A perfect |
| `edge_length` | Euclidean distance between two 2D vertices. Parameters ---------- v1, v2 : tuple[float, float] Retur |
| `build_data_index` | Build a coordinate-to-index lookup for seed point data. Only records the *first* occurrence of each  |

### Signatures

```python
def polygon_area(vertices)
def polygon_perimeter(vertices)
def polygon_centroid(vertices)
def isoperimetric_quotient(area, perimeter)
def edge_length(v1, v2)
def build_data_index(data)
```

## vormap_graph

*Neighbourhood graph analysis for VoronoiMap.*

| Function | Description |
|----------|-------------|
| `extract_neighborhood_graph` | Extract the neighborhood (adjacency) graph from Voronoi regions. Two seed points are neighbours if t |
| `compute_graph_stats` | Compute statistics for the neighbourhood graph. Parameters ---------- graph : dict Output of ``extra |
| `export_graph_json` | Export the neighbourhood graph as a JSON file. The JSON contains nodes (with coordinates and degree) |
| `export_graph_csv` | Export the neighbourhood graph as a CSV edge list. Each row contains source and target node indices, |
| `format_graph_stats_table` | Format graph statistics as a human-readable text table. Parameters ---------- graph : dict Output of |
| `export_graph_svg` | *(no description)* |
| `generate_graph` | Load data, compute regions, extract graph, and export in one call. Parameters ---------- datafile :  |

### Signatures

```python
def extract_neighborhood_graph(regions, data=None, *, tol=0.5)
def compute_graph_stats(graph)
def export_graph_json(graph, output_path, *, include_stats=True)
def export_graph_csv(graph, output_path)
def format_graph_stats_table(graph)
def export_graph_svg( regions, data, graph, output_path, *, width=800, height=600, margin=40, color_scheme=DEFAULT_COLOR_SCHEME, show_voronoi=True, show_graph=True, show_degree_labels=False, edge_color="#e74c3c", edge_width=2.0, node_radius=5.0, node_color="#c0392b", title=None, )
def generate_graph(datafile, output_path=None, *, fmt="table")
```

## vormap_heatmap

*Voronoi density heatmap visualization.*

| Function | Description |
|----------|-------------|
| `export_heatmap_svg` | *(no description)* |
| `export_heatmap_html` | *(no description)* |

### Signatures

```python
def export_heatmap_svg( regions, data, output_path, *, width=800, height=600, margin=40, color_ramp=DEFAULT_HEATMAP_RAMP, metric="density", show_points=True, show_values=False, stroke_color="#333333", stroke_width=0.8, point_radius=2.5, point_color="#000000", background="#ffffff", title=None, )
def export_heatmap_html( regions, data, output_path, *, width=800, height=600, margin=40, color_ramp=DEFAULT_HEATMAP_RAMP, metric="density", title=None, )
```

## vormap_hull

*Convex hull and bounding geometry analysis for point sets.*

| Function | Description |
|----------|-------------|
| `convex_hull` | Compute the convex hull of a 2D point set. Uses Andrew's monotone chain algorithm — O(n log n). Para |
| `bounding_rect` | *(no description)* |
| `bounding_circle` | *(no description)* |
| `hull_analysis` | Run full convex hull + bounding geometry analysis. Parameters ---------- points : list of (float, fl |
| `format_report` | Format a human-readable hull analysis report. |
| `export_svg` | *(no description)* |
| `export_json` | Export hull analysis results as JSON. |

### Signatures

```python
def convex_hull(points: List[Tuple[float, float]]) -> ConvexHullResult:
def bounding_rect(points: List[Tuple[float, float]],
def bounding_circle(points: List[Tuple[float, float]],
def hull_analysis(points: List[Tuple[float, float]]) -> HullAnalysis:
def format_report(analysis: HullAnalysis) -> str:
def export_svg(analysis: HullAnalysis,
def export_json(analysis: HullAnalysis, output_path: str) -> None:
```

## vormap_interp

*Spatial interpolation using Voronoi natural neighbor weights.*

| Function | Description |
|----------|-------------|
| `nearest_interp` | Return the value of the nearest seed point. |
| `idw_interp` | Inverse distance weighted interpolation. |
| `natural_neighbor_interp` | Sibson natural neighbor interpolation. Inserts the query point into the Voronoi diagram and computes |
| `grid_interpolate` | *(no description)* |
| `export_surface_svg` | *(no description)* |
| `export_surface_csv` | Export interpolated surface as a CSV grid. |
| `run_interp_cli` | Execute interpolation commands from CLI args. |

### Signatures

```python
def nearest_interp(points, values, query)
def idw_interp(points, values, query, power=2.0, epsilon=1e-12)
def natural_neighbor_interp(points, values, query, fallback_idw=True)
def grid_interpolate(points, values, nx=50, ny=50, bounds=None, method='natural', power=2.0)
def export_surface_svg(grid_result, output_path, width=800, height=600, ramp='viridis', title=None)
def export_surface_csv(grid_result, output_path)
def run_interp_cli(args, data)
```

## vormap_kde

*Kernel Density Estimation (KDE) for spatial point data.*

| Function | Description |
|----------|-------------|
| `silverman_bandwidth` | Silverman's rule-of-thumb bandwidth for 2D Gaussian KDE. h = (4 / (3n))^(1/5) * mean_std |
| `scott_bandwidth` | Scott's rule bandwidth for 2D Gaussian KDE. h = n^(-1/6) * mean_std |
| `gaussian_kernel` | 2D Gaussian kernel: K(d) = (1/(2*pi*h^2)) * exp(-d^2/(2*h^2)). |
| `kde_at_point` | *(no description)* |
| `kde_grid` | *(no description)* |
| `find_hotspots` | *(no description)* |
| `density_contours` | Extract iso-density contour levels from the KDE grid. |
| `export_kde_svg` | *(no description)* |
| `export_kde_csv` | Export KDE grid as CSV with columns: x, y, density. |
| `export_hotspots_json` | *(no description)* |
| `kde_summary` | Generate a statistical summary of the KDE grid. |

### Signatures

```python
def silverman_bandwidth(points: list[tuple[float, float]]) -> float:
def scott_bandwidth(points: list[tuple[float, float]]) -> float:
def gaussian_kernel(dist_sq: float, h_sq: float) -> float:
def kde_at_point( qx: float, qy: float,
def kde_grid( points: list[tuple[float, float]],
def find_hotspots( grid: KDEGrid,
def density_contours(grid: KDEGrid, levels: int = 5) -> list[DensityContour]:
def export_kde_svg( grid: KDEGrid,
def export_kde_csv(grid: KDEGrid, output_path: str) -> str:
def export_hotspots_json( hotspots: list[Hotspot],
def kde_summary(grid: KDEGrid) -> dict:
```

## vormap_kml

*KML export for VoronoiMap — generates Google Earth KML files.*

| Function | Description |
|----------|-------------|
| `export_kml` | *(no description)* |
| `generate_kml` | *(no description)* |

### Signatures

```python
def export_kml( regions, data, output_path, *, include_seeds=True, color_scheme='pastel', region_names_fn=None, )
def generate_kml( datafile, output_path='voronoi.kml', *, include_seeds=True, color_scheme='pastel', )
```

## vormap_nndist

*Nearest-neighbor distance analysis for spatial point patterns.*

| Function | Description |
|----------|-------------|
| `nn_distances` | *(no description)* |
| `clark_evans` | *(no description)* |
| `g_function` | *(no description)* |
| `distance_summary` | *(no description)* |
| `export_nn_csv` | Export NN distance data to CSV. Parameters ---------- summary : DistanceSummary Result from :func:`d |
| `export_nn_json` | Export any result object to JSON (uses to_dict()). Parameters ---------- result : object Any result  |

### Signatures

```python
def nn_distances( points: list,
def clark_evans( points: list,
def g_function( points: list,
def distance_summary( points: list,
def export_nn_csv(summary: DistanceSummary, path: str) -> str:
def export_nn_json(result: object, path: str) -> str:
```

## vormap_outlier

*Spatial outlier detection for Voronoi diagrams.*

| Function | Description |
|----------|-------------|
| `detect_outliers` | Detect spatial outliers in Voronoi region statistics. Parameters ---------- stats : list of dict Reg |
| `export_outlier_json` | Export outlier detection results as JSON. Parameters ---------- result : OutlierResult From ``detect |
| `export_outlier_svg` | Export an SVG highlighting outlier regions in red. Parameters ---------- result : OutlierResult From |
| `export_outlier_csv` | Export outlier data as CSV for spreadsheet analysis. Parameters ---------- result : OutlierResult Fr |

### Signatures

```python
def detect_outliers(stats, metrics=None, method="zscore", threshold=None)
def export_outlier_json(result, path)
def export_outlier_svg(result, regions, data, path, width=800, height=600)
def export_outlier_csv(result, path)
```

## vormap_pattern

*Point pattern analysis for Voronoi seed distributions.*

| Function | Description |
|----------|-------------|
| `clark_evans_nni` | Compute the Clark-Evans Nearest Neighbor Index. Parameters ---------- points : list of (x, y) tuples |
| `ripleys_k` | Compute Ripley's K function and Besag's L function. Parameters ---------- points : list of (x, y) tu |
| `quadrat_analysis` | Perform quadrat (grid cell) analysis for spatial randomness. Parameters ---------- points : list of  |
| `mean_center` | Compute the mean center (centroid) of a point pattern. Parameters ---------- points : list of (x, y) |
| `standard_distance` | Compute the standard distance (spatial spread) of a point set. This is the spatial equivalent of sta |
| `convex_hull_ratio` | Compute the ratio of convex hull area to bounding box area. A ratio near 1.0 means points fill the s |
| `analyze_pattern` | *(no description)* |
| `format_pattern_report` | Format a PatternSummary as a human-readable text report. Parameters ---------- summary : PatternSumm |
| `generate_pattern_json` | Convert a PatternSummary to a JSON-serialisable dictionary. Parameters ---------- summary : PatternS |

### Signatures

```python
def clark_evans_nni(points, bounds=None)
def ripleys_k(points, radii=None, n_radii=20, bounds=None)
def quadrat_analysis(points, rows=None, cols=None, bounds=None)
def mean_center(points)
def standard_distance(points)
def convex_hull_ratio(points, bounds=None)
def analyze_pattern(points, bounds=None, quadrat_rows=None, quadrat_cols=None, ripley_radii=None, n_radii=15)
def format_pattern_report(summary)
def generate_pattern_json(summary)
```

## vormap_power

*Weighted (Power) Voronoi Diagrams.*

| Function | Description |
|----------|-------------|
| `assign_weights` | *(no description)* |
| `weighted_distance` | Compute weighted distance from point (px, py) to site (sx, sy). Parameters ---------- px, py : float |
| `compute_weighted_nn` | Find the nearest seed to *point* using weighted distance. Parameters ---------- point : tuple[float, |
| `batch_weighted_nn` | Assign each point in *points* to its nearest weighted seed. Returns list of ``(index, seed, weighted |
| `compute_power_regions` | *(no description)* |
| `compute_power_diagram` | *(no description)* |
| `weight_effect_analysis` | *(no description)* |
| `export_power_json` | Export PowerDiagramResult to JSON. Returns the JSON string; writes to *path* if given. |
| `export_power_svg` | *(no description)* |
| `main` | Command-line interface for weighted Voronoi diagrams. |

### Signatures

```python
def assign_weights(seeds, method='uniform', *, value=1.0, min_w=0.1, max_w=10.0, seed=None, center=None, sigma=None, direction='x')
def weighted_distance(px, py, sx, sy, w, mode='power')
def compute_weighted_nn(point, seeds, weights, mode='power')
def batch_weighted_nn(points, seeds, weights, mode='power')
def compute_power_regions(seeds, weights, mode='power', bounds=None, resolution=200)
def compute_power_diagram(seeds, weights, mode='power', bounds=None, resolution=200)
def weight_effect_analysis(seeds, weights, mode='power', bounds=None, resolution=200)
def export_power_json(result, path=None)
def export_power_svg(result, path=None, width=800, height=600, color_scheme='viridis', show_weights=True, show_seeds=True)
def main()
```

## vormap_query

*Point Location & Nearest Neighbor Query for Voronoi diagrams.*

| Function | Description |
|----------|-------------|
| `query_stats` | Compute statistics over a batch of query results. |
| `coverage_analysis` | *(no description)* |
| `export_query_json` | Write query results to a JSON file. |
| `export_query_svg` | *(no description)* |
| `run_query_cli` | Execute query-related CLI arguments. Called from ``vormap.main()`` after diagram computation. |

### Signatures

```python
def query_stats(results: Sequence[QueryResult]) -> QueryStats:
def coverage_analysis(points: Sequence[Point],
def export_query_json(results: Sequence[QueryResult], path: str) -> None:
def export_query_svg(seeds: Sequence[Point],
def run_query_cli(args, data, regions) -> None:
```

## vormap_seeds

*Seed point generators for Voronoi diagrams.*

| Function | Description |
|----------|-------------|
| `random_uniform` | *(no description)* |
| `grid` | *(no description)* |
| `hexagonal` | *(no description)* |
| `jittered_grid` | *(no description)* |
| `poisson_disk` | *(no description)* |
| `halton` | *(no description)* |
| `save_seeds` | Save seed points to a text file compatible with ``vormap.py``. Output format is one point per line:  |
| `load_seeds` | Load seed points from a text file. Supports files with or without a header count line. Lines with ex |
| `main` | Command-line interface for seed point generation. |

### Signatures

```python
def random_uniform(n, x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0, *, seed=None)
def grid(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0, *, rows=10, cols=10, margin=0.0)
def hexagonal(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0, *, spacing=100.0, margin=0.0)
def jittered_grid(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0, *, rows=10, cols=10, jitter=0.5, margin=0.0, seed=None)
def poisson_disk(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0, *, min_dist=50.0, max_attempts=30, seed=None)
def halton(n, x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0, *, bases=(2, 3), skip=0)
def save_seeds(points, filename, header=True)
def load_seeds(filename)
def main()
```

## vormap_stability

*Voronoi Stability Analysis - measure diagram sensitivity to point perturbation.*

| Function | Description |
|----------|-------------|
| `stability_analysis` | *(no description)* |
| `export_json` | Export stability results to a JSON file. Parameters ---------- result : StabilityResult Output of `` |
| `export_csv` | Export per-cell stability metrics to a CSV file. Parameters ---------- result : StabilityResult Outp |
| `export_svg` | *(no description)* |
| `main` | Command-line interface for Voronoi stability analysis. |

### Signatures

```python
def stability_analysis( data: List[Tuple[float, float]],
def export_json(result: StabilityResult, output_path: str) -> str:
def export_csv(result: StabilityResult, output_path: str) -> str:
def export_svg( result: StabilityResult,
def main(argv=None)
```

## vormap_territory

*Territorial Analysis for Voronoi Diagrams.*

| Function | Description |
|----------|-------------|
| `analyze_territories` | *(no description)* |
| `format_territory_report` | Format a human-readable territorial analysis report. Parameters ---------- analysis : dict Output fr |
| `export_territory_json` | *(no description)* |
| `export_territory_csv` | *(no description)* |

### Signatures

```python
def analyze_territories( regions: Dict[Tuple[float, float], List[Tuple[float, float]]],
def format_territory_report(analysis: Dict[str, Any]) -> str:
def export_territory_json( analysis: Dict[str, Any],
def export_territory_csv( analysis: Dict[str, Any],
```

## vormap_viz

*Voronoi diagram visualization — SVG, HTML & GeoJSON export for VoronoiMap.*

| Function | Description |
|----------|-------------|
| `compute_regions` | Compute Voronoi region polygons for all seed points. When scipy is available, uses ``scipy.spatial.V |
| `export_svg` | *(no description)* |
| `generate_diagram` | *(no description)* |
| `list_color_schemes` | Return a sorted list of available color scheme names. |
| `export_html` | *(no description)* |
| `export_geojson` | *(no description)* |
| `generate_geojson` | *(no description)* |
| `compute_region_stats` | Compute detailed statistics for each Voronoi region. Parameters ---------- regions : dict Output of  |
| `compute_summary_stats` | Compute aggregate summary statistics across all regions. Parameters ---------- region_stats : list o |
| `export_stats_csv` | Export per-region statistics as a CSV file. Parameters ---------- region_stats : list of dict Output |
| `export_stats_json` | Export per-region statistics as a JSON file. Parameters ---------- region_stats : list of dict Outpu |
| `format_stats_table` | Format region statistics as a human-readable text table. Parameters ---------- region_stats : list o |
| `generate_stats` | Load data, compute regions, and export statistics in one call. Parameters ---------- datafile : str  |
| `lloyd_relaxation` | Apply Lloyd relaxation to make Voronoi regions more uniform. Lloyd's algorithm iteratively moves eac |
| `generate_relaxed_diagram` | *(no description)* |
| `export_relaxation_html` | *(no description)* |

### Signatures

```python
def compute_regions(data)
def export_svg( regions, data, output_path, *, width=800, height=600, margin=40, color_scheme=DEFAULT_COLOR_SCHEME, show_points=True, show_labels=False, stroke_color="#444444", stroke_width=1.2, point_radius=3.5, point_color="#222222", background="#ffffff", title=None, )
def generate_diagram( datafile, output_path="voronoi.svg", **kwargs, )
def list_color_schemes()
def export_html( regions, data, output_path, *, width=960, height=700, color_scheme=DEFAULT_COLOR_SCHEME, title=None, )
def export_geojson( regions, data, output_path, *, include_seeds=True, properties_fn=None, crs_name=None, )
def generate_geojson( datafile, output_path="voronoi.geojson", **kwargs, )
def compute_region_stats(regions, data)
def compute_summary_stats(region_stats)
def export_stats_csv(region_stats, output_path, *, include_summary=True)
def export_stats_json(region_stats, output_path, *, include_summary=True)
def format_stats_table(region_stats, *, summary=True)
def generate_stats(datafile, output_path=None, *, fmt="table")
def lloyd_relaxation(data, iterations=10, *, bounds=None, callback=None)
def generate_relaxed_diagram( datafile, output_path="voronoi_relaxed.svg", iterations=10, **kwargs, )
def export_relaxation_html( data, iterations=10, output_path="relaxation.html", *, width=960, height=700, color_scheme=DEFAULT_COLOR_SCHEME, title=None, bounds=None, )
```

---

## vormap_access

> Spatial Accessibility Analyzer for Voronoi diagrams.

| Function | Description |
|----------|-------------|
| `proximity_scores` | Compute proximity-based accessibility scores. |
| `gravity_scores` | Compute gravity-model accessibility scores. |
| `two_step_fca` | Two-Step Floating Catchment Area method (Luo & Wang 2003). |
| `enhanced_two_step_fca` | Enhanced Two-Step Floating Catchment Area (Luo & Qi 2009). |
| `access_inequality` | Compute access inequality metrics from a list of scores. |
| `accessibility_report` | Run a full accessibility analysis and produce a report. |
| `export_json` | Export accessibility report to JSON. |
| `export_csv` | Export accessibility scores to CSV. |
| `main` | CLI entry point for spatial accessibility analysis. |

---

## vormap_coverage

> Coverage Analyzer — service area coverage, gaps, and optimal site placement.

| Function | Description |
|----------|-------------|
| `coverage_analysis` | Analyze service-area coverage for a set of sites. |
| `suggest_site` | Return the optimal next-site suggestion. |
| `export_json` | Export CoverageResult to a JSON file. |
| `export_csv` | Export per-site coverage stats to CSV. |
| `export_heatmap_svg` | Export a coverage depth heatmap as SVG. |
| `render` | Render a human-readable text report. |
| `main` | CLI entry point. |

---

## vormap_generate

> Synthetic point pattern generator for VoronoiMap.

| Function | Description |
|----------|-------------|
| `generate_poisson` | Complete Spatial Randomness (homogeneous Poisson process). |
| `generate_clustered` | Thomas (Poisson cluster) process. |
| `generate_regular` | Jittered regular grid. |
| `generate_inhibitory` | Simple Sequential Inhibition (hard-core) process. |
| `generate_gradient` | Inhomogeneous Poisson process with a linear density gradient. |
| `generate_mixed` | Mixed pattern: clusters + uniform background noise. |
| `generate_pattern` | Generate a point pattern by name. |
| `list_patterns` | Return available pattern names. |
| `export_txt` | Write points to a space-separated text file (vormap native format). |
| `export_csv` | Write points to CSV with x,y headers. |
| `export_json` | Write points as a JSON array of [x, y] pairs. |
| `pattern_summary` | Compute summary statistics for a generated pattern. |
| `main` | CLI entry point. |

---

## vormap_geojson

> GeoJSON export — generates RFC 7946 GeoJSON files.

| Function | Description |
|----------|-------------|
| `export_geojson` | Export Voronoi regions as a GeoJSON FeatureCollection. |
| `generate_geojson` | Load data, compute regions, and export GeoJSON in one call. |
| `main` | CLI entry point for GeoJSON export. |

---

## vormap_hotspot

> Spatial hotspot detection using Getis-Ord Gi* statistic.

| Function | Description |
|----------|-------------|
| `build_queen_weights` | Build queen contiguity weight matrix. |
| `build_distance_weights` | Build distance-band weight matrix. |
| `build_knn_weights` | Build k-nearest-neighbor weight matrix. |
| `detect_hotspots` | Detect spatial hotspots and coldspots using Getis-Ord Gi*. |
| `export_hotspot_svg` | Export hotspot map as SVG. |
| `export_hotspot_json` | Export hotspot results as JSON. |
| `export_hotspot_csv` | Export hotspot results as CSV. |

---

## vormap_landscape

> Landscape Ecology Metrics for Voronoi Diagrams.

| Function | Description |
|----------|-------------|
| `compute_patch_metrics` | Compute metrics for each patch. |
| `compute_class_metrics` | Compute class-level metrics. |
| `compute_landscape_metrics` | Compute landscape-level aggregate metrics. |
| `analyze_landscape` | Run full landscape ecology analysis. |
| `format_landscape_report` | Format analysis as human-readable text report. |
| `export_landscape_json` | Export analysis to JSON file. |
| `export_landscape_csv` | Export patch-level metrics to CSV file. |

---

## vormap_merge

> Region Merger — merge adjacent Voronoi regions by attribute similarity.

| Function | Description |
|----------|-------------|
| `merge_regions` | Merge adjacent Voronoi regions based on attribute similarity. |
| `merge_summary` | Return a formatted text summary of merged zones. |
| `export_merge_svg` | Export a colour-coded SVG of merged zones. |
| `export_merge_json` | Export merge result as JSON. |
| `export_merge_csv` | Export seed-to-zone mapping as CSV. |

---

## vormap_mosaic

> Voronoi mosaic image filter — stained-glass and pixel-art effects.

| Function | Description |
|----------|-------------|
| `create_mosaic` | Full pipeline: generate seeds, assign pixels, colour, render. |
| `generate_seeds` | Generate seed points for the mosaic. |
| `assign_pixels` | Assign each pixel to its nearest seed index. |
| `compute_region_colors` | Compute the fill colour for each region. |
| `render` | Render the mosaic image. |
| `save_png` | Save the mosaic as a PNG file. |
| `main` | CLI entry point. |

---

## vormap_network

> Spatial network analysis via Delaunay triangulation.

| Function | Description |
|----------|-------------|
| `build_delaunay_graph` | Build the Delaunay adjacency graph from Voronoi region stats. |
| `compute_mst` | Compute the Minimum Spanning Tree using Kruskal's algorithm. |
| `network_stats` | Compute comprehensive network statistics. |
| `export_network_svg` | Export the spatial network as an SVG visualization. |
| `export_network_json` | Export network analysis results as JSON. |
| `export_network_csv` | Export per-node network metrics as CSV. |

---

## vormap_pathplan

> Voronoi-based path planner for obstacle-aware navigation.

| Function | Description |
|----------|-------------|
| `build_roadmap` | Build a navigation roadmap from Voronoi edges of obstacle points. |
| `find_path` | Find a path from start to goal through the Voronoi roadmap. |
| `compute_path_stats` | Compute detailed statistics about a path. |
| `export_path_json` | Export path and roadmap data to JSON. |
| `export_path_csv` | Export waypoints to CSV. |
| `export_path_svg` | Generate an SVG visualisation of the path through obstacles. |
| `format_path_report` | Generate a human-readable text report of the path. |
| `plan_path` | One-call convenience function for the full path planning pipeline. |
| `main` | CLI entry point. |

---

## vormap_regularity

> Voronoi entropy and regularity analysis.

| Function | Description |
|----------|-------------|
| `polygon_distribution` | Compute polygon type distribution from side counts. |
| `voronoi_entropy` | Shannon entropy of side-count distribution (bits). |
| `lewis_law_fit` | Fit Lewis's law: mean_area(n) = slope * n + offset. |
| `aboav_weaire_fit` | Fit the Aboav-Weaire law from adjacency data. |
| `regularity_analysis` | Run full Voronoi regularity and entropy analysis. |
| `export_regularity_json` | Write regularity analysis to JSON file. |
| `format_report` | Format a human-readable regularity report. |

---

## vormap_relax

> Lloyd's Relaxation — Centroidal Voronoi Tessellation.

| Function | Description |
|----------|-------------|
| `lloyd_relaxation` | Apply Lloyd's relaxation to a set of 2D points. |
| `uniformity_score` | Measure spatial uniformity of a point set (0 = clustered, 1 = uniform). |
| `main` | CLI entry point for Lloyd's relaxation. |

---

## vormap_report

> Generate a self-contained HTML analysis report for a Voronoi diagram.

| Function | Description |
|----------|-------------|
| `generate_report` | Convenience function to generate a report in one call. |
| `generate` | Generate the HTML report and write to output_path. |

---

## vormap_sample

> Voronoi-based spatial sampling for survey and study design.

| Function | Description |
|----------|-------------|
| `stratified_random` | Generate stratified random samples within Voronoi regions. |
| `systematic_grid` | Generate systematic grid samples within each Voronoi region. |
| `centroid_based` | Sample at region centroids with optional random jitter. |
| `boundary_focused` | Generate samples near region boundaries for transition studies. |
| `density_weighted` | Allocate samples proportional to a density or importance field. |
| `adaptive` | Adaptive sampling: more samples in heterogeneous regions. |
| `export_sample_plan` | Export a sample plan to CSV or GeoJSON for field use. |
| `coverage_analysis` | Analyze spatial coverage of a sample plan. |

---

## vormap_temporal

> Voronoi Temporal Dynamics — track how diagrams evolve over time.

| Function | Description |
|----------|-------------|
| `temporal_analysis` | Analyse how a Voronoi diagram evolves across time snapshots. |
| `export_json` | Export temporal analysis to JSON. |
| `export_csv` | Export temporal events to CSV. |
| `main` | CLI entry point for temporal Voronoi analysis. |

---

## vormap_transect

> Transect Profiler — cross-section analysis along a line through Voronoi diagrams.

| Function | Description |
|----------|-------------|
| `create_transect` | Create a transect from coordinate pairs or tuples. |
| `analyse_transect` | Analyse a transect across a Voronoi diagram. |
| `export_transect_svg` | Export a transect profile as an SVG cross-section diagram. |
| `export_transect_json` | Export transect analysis to JSON. |
| `export_transect_csv` | Export transect crossings to CSV. |

---

## vormap_trend

> Trend Surface Analysis for Voronoi diagrams.

| Function | Description |
|----------|-------------|
| `fit_trend_surface` | Fit a polynomial trend surface to region attributes. |
| `compare_trends` | Compare trend surfaces across polynomial orders. |
| `predict_at` | Predict the trend value at an arbitrary (x, y) location. |
| `predict_grid` | Generate a grid of predicted trend values. |
| `export_trend_svg` | Export trend surface or residuals as SVG. |
| `export_trend_csv` | Export trend analysis results to CSV. |
| `export_trend_json` | Export trend analysis results to JSON. |

---

## vormap_variogram

> Variogram analysis for Voronoi-based spatial data.

| Function | Description |
|----------|-------------|
| `evaluate_model` | Evaluate a fitted variogram model at distance h. |
| `experimental_variogram` | Compute an experimental (empirical) variogram. |
| `fit_variogram` | Fit a theoretical variogram model to experimental data. |
| `auto_fit` | Try all model types and return the best fit (lowest RMSE). |
| `variogram_surface` | Compute directional variograms to detect anisotropy. |
| `variogram_cloud` | Compute the variogram cloud (all pairwise semivariances). |
| `export_variogram_svg` | Export an SVG plot of the experimental variogram with optional model curve. |
| `export_variogram_csv` | Export variogram data as CSV. |
| `export_variogram_json` | Export variogram data as JSON. |
| `variogram_summary` | Generate a human-readable variogram summary. |
