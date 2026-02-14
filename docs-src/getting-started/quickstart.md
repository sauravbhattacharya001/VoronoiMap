# Quick Start

This guide walks you through your first VoronoiMap estimation in under 5 minutes.

## 1. Prepare Your Data

Create a `data/` directory and add a point data file. Each line has space-separated longitude and latitude:

```bash
mkdir data
```

Create `data/sample5.txt`:

```
100.0 200.0
400.0 600.0
800.0 300.0
200.0 800.0
700.0 700.0
```

## 2. Run from the Command Line

```bash
# Estimate count from the 5-point dataset
voronoimap sample5.txt 5

# Multiple runs for better accuracy
voronoimap sample5.txt 5 --runs 3
```

Output:

```
5 7 5.2 142
Run 1: regions=5  max_edges=7  avg_edges=5.2
```

The output shows:

- **regions** — Estimated number of Voronoi regions (should be close to the actual count)
- **max_edges** — Maximum number of vertices found on any single region
- **avg_edges** — Average number of vertices per region
- **oracle calls** — How many nearest-neighbor queries were made

## 3. Use the Python API

```python
from vormap import get_sum, load_data, find_area, get_NN

# --- Estimation ---
estimated, max_edges, avg_edges = get_sum("sample5.txt", 5)
print(f"Estimated {estimated} objects")
print(f"Max edges: {max_edges}, Avg edges: {avg_edges:.1f}")

# --- Direct data access ---
data = load_data("sample5.txt")
print(f"Loaded {len(data)} points")

# Find nearest neighbor to an arbitrary point
nearest = get_NN(data, 500.0, 500.0)
print(f"Nearest to (500, 500): {nearest}")

# Compute area of the Voronoi region around a data point
area, vertices = find_area(data, 400.0, 600.0)
print(f"Region area: {area:.2f}, Vertices: {vertices}")
```

## 4. Custom Boundaries

By default, VoronoiMap auto-detects the search space from data extents. You can override this:

=== "CLI"
    ```bash
    voronoimap sample5.txt 5 --bounds 0 1000 0 1000
    ```

=== "Python"
    ```python
    from vormap import set_bounds, load_data

    set_bounds(south=0, north=1000, west=0, east=1000)
    data = load_data("sample5.txt", auto_bounds=False)
    ```

## Next Steps

- [CLI Reference](../guide/cli.md) — All command-line options
- [Python API](../guide/api.md) — Full function documentation
- [Algorithm Overview](../algorithm/overview.md) — How the estimation works
