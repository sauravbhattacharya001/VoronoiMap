# Tutorials

Practical, step-by-step tutorials that demonstrate VoronoiMap's capabilities
through real-world spatial analysis workflows.

---

## Tutorial 1: Exploring a Point Dataset

Learn the core analysis loop: load data, generate diagrams, inspect statistics,
and iterate on placement.

### Step 1: Prepare Data

Create a data file `data/cities.txt` with longitude/latitude pairs:

```
-122.33 47.61
-118.24 34.05
-73.94 40.73
-87.63 41.88
-95.37 29.76
-80.19 25.76
-112.07 33.45
-77.04 38.91
-84.39 33.75
-93.27 44.98
```

### Step 2: Generate and Visualize

```python
from vormap import load_data
from vormap_viz import compute_regions, export_svg, export_html, compute_region_stats

data = load_data("cities.txt")
regions = compute_regions(data)
stats = compute_region_stats(regions, data)

# Static SVG export
export_svg(regions, data, "cities.svg", color_scheme="viridis")

# Interactive HTML — hover over regions to see metrics
export_html(regions, data, "cities.html")
```

### Step 3: Inspect Statistics

```python
from vormap_viz import compute_summary_stats, format_stats_table

summary = compute_summary_stats(stats)
print(format_stats_table(stats))
print(f"Mean area: {summary['area']['mean']:.1f}")
print(f"Area std-dev: {summary['area']['std']:.1f}")
```

Large variance in areas indicates an uneven distribution — some regions
dominate while others are small. This is expected for real-world city placement.

### Step 4: Apply Lloyd Relaxation

Relaxation nudges seeds toward their cell centroids, evening out cell sizes:

```python
from vormap_viz import lloyd_relaxation

relaxed = lloyd_relaxation(data, iterations=20)
relaxed_regions = compute_regions(relaxed)

# Compare original vs relaxed
export_svg(relaxed_regions, relaxed, "cities_relaxed.svg")
```

!!! tip "Relaxation Strength"
    More iterations = more uniform cells. 5–10 is subtle; 50+ approaches
    a centroidal Voronoi tessellation where all cells have nearly equal area.

---

## Tutorial 2: Spatial Pattern Analysis

Determine whether points are clustered, dispersed, or random using
statistical tests.

### Nearest-Neighbor Analysis

```python
from vormap import load_data
from vormap_nndist import compute_nndist

data = load_data("cities.txt")
result = compute_nndist(data)

print(f"Mean NN distance: {result['mean_nndist']:.2f}")
print(f"R-statistic: {result['r_statistic']:.3f}")
print(f"Pattern: {result['pattern']}")
```

| R-statistic | Interpretation |
|-------------|---------------|
| R < 1.0 | Clustered (points clump together) |
| R ≈ 1.0 | Random (Complete Spatial Randomness) |
| R > 1.0 | Dispersed (points repel each other) |

### Hotspot Detection

Identify statistically significant spatial concentrations:

```python
from vormap_hotspot import detect_hotspots

hotspots = detect_hotspots(data, significance=0.05)
for h in hotspots['hotspots']:
    print(f"Hotspot at ({h['x']:.2f}, {h['y']:.2f}), z-score: {h['z_score']:.2f}")
```

### Spatial Autocorrelation

Test whether nearby regions have similar attribute values (Moran's I):

```python
from vormap_autocorr import compute_autocorrelation

result = compute_autocorrelation(data, attribute_values)
print(f"Moran's I: {result['morans_i']:.4f}")
print(f"p-value: {result['p_value']:.4f}")
# Positive Moran's I → similar values cluster together
# Negative → dissimilar values are neighbors (checkerboard pattern)
```

---

## Tutorial 3: Simulation — Ecosystem Dynamics

VoronoiMap includes spatial simulators that run on the Voronoi topology.

### Ecosystem Simulator

```python
from vormap_ecosystem import Species, EcosystemSimulator

# Define species with growth rates and interactions
species = [
    Species("Grass",    color="green",  growth_rate=0.3),
    Species("Rabbits",  color="brown",  growth_rate=0.1),
    Species("Foxes",    color="red",    growth_rate=0.05),
]

sim = EcosystemSimulator(
    num_points=200,
    species=species,
    width=800,
    height=600,
)

# Run for 100 timesteps
result = sim.run(steps=100)
print(f"Final dominant species: {result['dominant_species']}")
print(f"Shannon diversity: {result['diversity']:.3f}")
```

### Contagion Simulation

Model disease spread or information diffusion across spatial regions:

```python
from vormap_contagion import simulate_contagion

result = simulate_contagion(
    data,
    infection_rate=0.3,
    recovery_rate=0.1,
    initial_infected=3,
    steps=50,
)
print(f"Peak infected: {result['peak_infected']} at step {result['peak_step']}")
print(f"Total ever infected: {result['total_infected']}")
```

### Cellular Automata

Run Conway's Game of Life (or custom rules) on Voronoi cells:

```python
from vormap_automata import run_automata

result = run_automata(
    data,
    rule="game_of_life",
    initial_density=0.3,
    steps=100,
)
```

---

## Tutorial 4: Export Workflows

### GeoJSON for Web Maps

Export Voronoi regions for use with Leaflet, Mapbox GL, or QGIS:

```python
from vormap_viz import compute_regions
from vormap_geojson import export_geojson

data = load_data("cities.txt")
regions = compute_regions(data)

export_geojson(regions, data, "cities.geojson")
# Load in any GIS tool or web map library
```

### KML for Google Earth

```python
from vormap_kml import export_kml

export_kml(regions, data, "cities.kml",
           name="US Cities Voronoi",
           description="Service area partitioning")
```

### 3D Mesh Export

Extrude Voronoi cells into 3D geometry based on attribute values:

```python
from vormap_mesh3d import export_mesh

export_mesh(regions, data, "cities.obj",
            height_values=population_values,
            scale=0.001)
# Import into Blender, Three.js, or any 3D viewer
```

### GPX Round-Trip

Import GPS tracks, analyze with Voronoi, and export back:

```python
from vormap_gpx import import_gpx, export_gpx

points = import_gpx("trail.gpx")
# ... spatial analysis ...
export_gpx(processed_points, "trail_analyzed.gpx")
```

---

## Tutorial 5: Artistic Rendering

VoronoiMap's generative art modules transform images and data into stylized outputs.

### Stained Glass Effect

```python
from vormap_stainedglass import render_stainedglass

render_stainedglass(
    "photo.jpg",
    output_path="stained.svg",
    num_cells=500,
    lead_width=2.0,
    color_mode="mean",
)
```

### Stippling (Photo → Dots)

Convert an image into stipple art using weighted Voronoi tessellation:

```python
from vormap_stipple import stipple_image

stipple_image(
    "portrait.jpg",
    output_path="stipple.svg",
    num_points=5000,
    iterations=30,
)
```

### Low-Poly Rendering

```python
from vormap_lowpoly import render_lowpoly

render_lowpoly(
    "landscape.jpg",
    output_path="lowpoly.svg",
    num_points=1000,
    edge_detection=True,
)
```

### Watercolor Effect

```python
from vormap_watercolor import render_watercolor

render_watercolor(
    "flowers.jpg",
    output_path="watercolor.svg",
    num_cells=300,
    bleed=0.15,
)
```

!!! tip "Artistic Module Tip"
    All art modules accept a `seed` parameter for reproducible results.
    Experiment with `num_cells` / `num_points` to control detail level —
    fewer cells → more abstract, more cells → more photorealistic.

---

## Tutorial 6: Benchmarking and Profiling

Measure VoronoiMap's performance on your hardware.

### Running Benchmarks

```bash
# Quick benchmark
python vormap_benchmark.py --sizes 50 100 500 1000

# Detailed with JSON export
python vormap_benchmark.py --sizes 100 500 1000 5000 --trials 10 --json results.json
```

### Programmatic Benchmarking

```python
from vormap_benchmark import run_benchmark, format_report

report = run_benchmark(sizes=[100, 500, 1000, 5000], trials=5, seed=42)
print(format_report(report))

# Scaling analysis
for t in report.timings:
    if t.operation == "voronoi_construction":
        print(f"{t.point_count} pts: {t.mean*1000:.1f}ms")
```

### Dataset Profiling

Get a comprehensive summary of any point dataset:

```python
from vormap_profile import profile_dataset

report = profile_dataset("cities.txt")
print(f"Points: {report['count']}")
print(f"Extent: {report['bounds']}")
print(f"Clustering tendency: {report['hopkins_statistic']:.3f}")
print(f"Recommended analysis: {report['recommendations']}")
```

---

## Tutorial 7: Coverage and Facility Siting

Solve spatial allocation problems — where to place facilities for maximum
coverage with minimum resources.

### Coverage Analysis

```python
from vormap_coverage import analyze_coverage

result = analyze_coverage(
    facility_points=data,
    demand_points=population_centers,
    service_radius=50.0,
)
print(f"Coverage: {result['coverage_pct']:.1f}%")
print(f"Underserved areas: {len(result['gaps'])}")
```

### Optimal Facility Placement

```python
from vormap_siting import find_optimal_sites

sites = find_optimal_sites(
    demand_points=population_centers,
    num_facilities=5,
    method="p_median",
)
for s in sites:
    print(f"Facility at ({s['x']:.2f}, {s['y']:.2f}), serves {s['demand_covered']} people")
```

### Dispatch Optimization

Assign service calls to the nearest available unit:

```python
from vormap_dispatch import optimize_dispatch

assignments = optimize_dispatch(
    units=ambulance_positions,
    calls=emergency_locations,
    method="nearest",
)
```

---

## Tutorial 8: Privacy-Preserving Analysis

Analyze spatial data while protecting individual locations.

```python
from vormap_privacy import anonymize_points

# Apply differential privacy — add calibrated noise
anonymized = anonymize_points(
    data,
    epsilon=1.0,          # privacy budget (lower = more private)
    mechanism="laplace",
)

# k-anonymity — ensure each region contains ≥ k points
kanon = anonymize_points(
    data,
    k=5,
    mechanism="k_anonymity",
)
```

!!! warning "Privacy Budget"
    Smaller epsilon values provide stronger privacy but more noise.
    For most applications, epsilon between 0.1 and 1.0 provides a
    good balance between utility and privacy.

---

## What's Next

- Browse the [Module Catalog](module-catalog.md) for the full list of 131 modules
- See the [Extension Modules API](extensions.md) for detailed function signatures
- Check the [Algorithm Overview](../algorithm/overview.md) for the theoretical foundation
- Read [Contributing](../contributing.md) to add your own modules
