# Module Catalog

Complete reference of all 138 VoronoiMap modules organized by functional area.
For detailed API documentation of core extension modules, see [Extension Modules API](extensions.md).

---

## Core

| Module | Description |
|--------|-------------|
| `vormap_utils` | Shared utility functions for VoronoiMap modules |
| `vormap_geometry` | Shared geometry and statistics helpers used across extension modules |
| `vormap_pipeline` | Batch analysis pipeline — chain multiple VoronoiMap tools together |
| `vormap_benchmark` | Performance benchmark for VoronoiMap operations |
| `vormap_profile` | Spatial data profiler — comprehensive dataset summary statistics |
| `vormap_quality` | Spatial data quality assessment for Voronoi diagrams |
| `vormap_doctor` | Diagram diagnostician — autonomous health checker and auto-fix prescriber |
| `vormap_recommend` | Spatial analysis recommender for Voronoi diagrams |

---

## Diagram Construction & Seeds

| Module | Description |
|--------|-------------|
| `vormap_seeds` | Seed point generators: uniform, grid, hexagonal, jittered, Poisson disk, Halton |
| `vormap_generate` | Synthetic point pattern generator |
| `vormap_relax` | Lloyd's relaxation — centroidal Voronoi tessellation |
| `vormap_evolve` | Evolutionary point placement optimizer |
| `vormap_merge` | Region merger — merge adjacent regions by attribute similarity |
| `vormap_clip` | Clip Voronoi regions to arbitrary convex boundary polygons |
| `vormap_power` | Power diagrams (weighted Voronoi / Laguerre diagrams) |
| `vormap_penrose` | Penrose tiling generator for Voronoi diagrams |

---

## Spatial Analysis

| Module | Description |
|--------|-------------|
| `vormap_cluster` | Cluster Voronoi cells by adjacency and spatial metrics |
| `vormap_pattern` | Statistical tests for spatial randomness, clustering, and dispersion |
| `vormap_outlier` | Detect spatial outliers via Z-score or IQR methods |
| `vormap_hotspot` | Spatial hotspot detection (Getis-Ord Gi*) |
| `vormap_autocorr` | Spatial autocorrelation analysis for Voronoi-partitioned data |
| `vormap_kde` | Kernel density estimation for spatial point data |
| `vormap_nndist` | Nearest-neighbor distance analysis for spatial point patterns |
| `vormap_fingerprint` | Spatial distribution fingerprinting — compact signatures for point patterns |
| `vormap_classify` | Spatial data classification for choropleth mapping |
| `vormap_regularity` | Voronoi entropy and regularity analysis |
| `vormap_symmetry` | Detect rotational and reflective symmetry in Voronoi diagrams |
| `vormap_fractal` | Fractal dimension analysis for Voronoi seed point patterns |
| `vormap_stability` | Measure diagram sensitivity to point perturbation |
| `vormap_crossval` | Leave-one-out cross-validation for spatial interpolation methods |
| `vormap_causality` | Spatial causality engine — counterfactual analysis, treatment effects, difference-in-differences, and synthetic control |
| `vormap_attention` | Spatial attention engine — autonomous analytical focus allocation via information density, change velocity, and surprise detection |
| `vormap_auction` | Spatial auction engine — sealed-bid, Vickrey, combinatorial, and ascending auction mechanisms for resource allocation |

---

## Geometry & Topology

| Module | Description |
|--------|-------------|
| `vormap_edge` | Extract and analyze the primal edge network from region boundaries |
| `vormap_graph` | Extract and analyze the dual (adjacency) graph |
| `vormap_delaunay` | Delaunay triangulation quality analysis |
| `vormap_hull` | Convex hull and bounding geometry analysis |
| `vormap_centroid` | Spatial center analysis for Voronoi point datasets |
| `vormap_shape` | Cell shape analysis — geometric shape metrics |
| `vormap_buffer` | Buffer zone analysis for Voronoi point datasets |
| `vormap_network` | Spatial network analysis via Delaunay triangulation |
| `vormap_transect` | Transect profiler — cross-section analysis along a line |
| `vormap_circlepack` | Largest inscribed circles per Voronoi cell |

---

## Interpolation & Regression

| Module | Description |
|--------|-------------|
| `vormap_interp` | Spatial interpolation: nearest, IDW, natural neighbor |
| `vormap_smooth` | Spatial smoothing of attribute values across cells |
| `vormap_contour` | Contour line extraction from Voronoi cell values |
| `vormap_regress` | Spatial regression analysis |
| `vormap_trend` | Trend surface analysis |
| `vormap_variogram` | Variogram analysis for Voronoi-based spatial data |
| `vormap_zonal` | Zonal statistics for Voronoi tessellations |
| `vormap_mapalgebra` | Voronoi map algebra — spatial operations on cell attribute layers |
| `vormap_isochrone` | Isochrone (travel-time zone) generator for Voronoi networks |

---

## Spatial Queries & Indexing

| Module | Description |
|--------|-------------|
| `vormap_query` | Spatial index for nearest-neighbor and point-in-region queries |
| `vormap_coverage` | Service area coverage analysis, gap detection, and optimal placement |
| `vormap_access` | Spatial accessibility analyzer |
| `vormap_sample` | Voronoi-based spatial sampling for survey and study design |
| `vormap_siting` | Optimal facility siting |

---

## Simulation & Dynamics

| Module | Description |
|--------|-------------|
| `vormap_automata` | Cellular automata on Voronoi tessellations |
| `vormap_contagion` | Spatial contagion simulator on Voronoi tessellations |
| `vormap_diffusion` | Spatial diffusion simulation over Voronoi networks |
| `vormap_ecosystem` | Spatial ecosystem simulator on Voronoi tessellations |
| `vormap_compete` | Territorial competition simulator |
| `vormap_erosion` | Terrain erosion simulation over Voronoi networks |
| `vormap_crystal` | Crystal growth simulator — anisotropic nucleation and growth |
| `vormap_montecarlo` | Monte Carlo spatial simulation |
| `vormap_gravity` | Spatial interaction and gravity model |
| `vormap_swarm` | Swarm intelligence engine — collective spatial optimization |
| `vormap_fracture` | Fracture simulator — realistic material fracture patterns |
| `vormap_watershed` | Watershed and flow analysis |
| `vormap_resilience` | Resilience analyzer — simulate failures and identify critical infrastructure |
| `vormap_equilibrium` | Spatial equilibrium engine — force fields, stability classification, basin mapping, and tipping point detection |
| `vormap_metabolism` | Spatial metabolism engine — resource production, consumption, trade flows, and bottleneck detection |
| `vormap_nervous` | Spatial nervous system — neural signal propagation, reflex arcs, rhythm analysis, and Hebbian plasticity |
| `vormap_maze` | Voronoi maze generator — DFS, Kruskal, and Prim algorithms with SVG export and BFS solver |

---

## Temporal Analysis

| Module | Description |
|--------|-------------|
| `vormap_temporal` | Voronoi temporal dynamics — track diagram evolution over time |
| `vormap_changedetect` | Spatial change detection for Voronoi point datasets |
| `vormap_forecast` | Spatial point pattern forecaster — predict future distributions |
| `vormap_morph` | Smooth animated interpolation between point configurations |
| `vormap_animate` | Animated HTML visualization of diagram evolution |
| `vormap_compare` | Compare two Voronoi diagrams for structural and geometric similarity |

---

## Visualization

| Module | Description |
|--------|-------------|
| `vormap_viz` | Primary visualization module — SVG, HTML, GeoJSON, statistics export |
| `vormap_heatmap` | Density heatmap generation |
| `vormap_color` | Four-color map coloring |
| `vormap_coloring` | Graph coloring for Voronoi diagrams |
| `vormap_gradient` | Gradient-fill renderer |
| `vormap_flowfield` | Flow-field visualizer — vector fields and streamlines |
| `vormap_label` | Automatic label placement |
| `vormap_cvd` | Color vision deficiency (CVD) simulator |
| `vormap_playground` | Interactive HTML Voronoi playground generator |
| `vormap_gallery` | Interactive HTML showcase of VoronoiMap art styles |
| `vormap_radar` | Spatial radar system for Voronoi point datasets |
| `vormap_report` | Self-contained HTML analysis report generator |

---

## Artistic & Generative

| Module | Description |
|--------|-------------|
| `vormap_ascii` | Terminal (ASCII/Unicode) rendering of Voronoi diagrams |
| `vormap_mosaic` | Mosaic image filter — stained-glass and pixel-art effects |
| `vormap_stainedglass` | Stained-glass renderer |
| `vormap_watercolor` | Watercolour painter |
| `vormap_lowpoly` | Low-poly image renderer |
| `vormap_stipple` | Stippling — image-to-dot-art via weighted Voronoi tessellation |
| `vormap_pixelart` | Pixel-art generator |
| `vormap_halftone` | Halftone renderer |
| `vormap_emboss` | Emboss and relief renderer |
| `vormap_hatch` | Hatch pattern generator — pen-plotter / engraving style |
| `vormap_crossstitch` | Cross-stitch pattern generator |
| `vormap_stringart` | String art generator |
| `vormap_spiral` | Spiral pattern generator |
| `vormap_kaleidoscope` | Kaleidoscope generator |
| `vormap_text` | Voronoi typography — render text as cell mosaics |
| `vormap_texture` | Seamless tileable texture generator |
| `vormap_tile` | Seamless tileable Voronoi pattern generator |
| `vormap_photomosaic` | Photo-mosaic renderer |
| `vormap_jigsaw` | Jigsaw puzzle generator — split images into puzzle pieces |
| `vormap_noise` | Worley (cellular) noise generator |
| `vormap_dream` | Synthetic distribution generator — learn and reproduce spatial patterns |
| `vormap_displacement` | Displacement and normal-map generator |

---

## 3D & Export

| Module | Description |
|--------|-------------|
| `vormap_voronoi3d` | 3-D Voronoi tessellation visualizer |
| `vormap_mesh3d` | 3D mesh exporter — extrude Voronoi cells into 3D geometry |
| `vormap_kml` | KML export for Google Earth / Google Maps |
| `vormap_geojson` | GeoJSON export (RFC 7946) |
| `vormap_gpx` | GPX import/export |
| `vormap_cartogram` | Area-proportional region distortion |
| `vormap_treemap` | Voronoi treemap — hierarchical Voronoi tessellations |

---

## Territory & Planning

| Module | Description |
|--------|-------------|
| `vormap_territory` | Territorial properties: border/interior regions, inequality, compactness |
| `vormap_landscape` | Landscape ecology metrics |
| `vormap_balance` | Spatial load balancer |
| `vormap_dispatch` | Spatial dispatch optimizer |
| `vormap_pathplan` | Path planner for obstacle-aware navigation |
| `vormap_patrol` | Spatial patrol planner for Voronoi territories |
| `vormap_terraform` | Terrain sculptor |
| `vormap_architect` | Spatial layout architect |
| `vormap_strategist` | Spatial strategy planner |

---

## Governance & Safety

| Module | Description |
|--------|-------------|
| `vormap_privacy` | Differential privacy and k-anonymity for point data |
| `vormap_guardian` | Spatial constraint guardian — constraint enforcement and auto-repair |
| `vormap_referee` | Spatial fairness referee |
| `vormap_sentinel` | Proactive distribution monitoring and anomaly alerting |
| `vormap_forensics` | Spatial anomaly forensics engine — multi-phase investigation |
| `vormap_narrative` | Spatial narrative generator — natural language analysis summaries |
| `vormap_negotiator` | Spatial conflict negotiator — multi-party conflict resolution |
