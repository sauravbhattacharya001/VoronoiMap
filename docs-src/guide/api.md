# Python API Reference

All public functions are importable from the `vormap` module.

## Core Functions

### `get_sum(filename, n)`

The main estimation algorithm. Samples random points, constructs Voronoi regions, and estimates the total number of data points.

```python
from vormap import get_sum

estimated_count, max_edges, avg_edges = get_sum("datauni5.txt", 5)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `filename` | `str` | Data filename inside `data/` directory |
| `n` | `int` | Sample size / expected region count |

**Returns:** `tuple[int, int, float]`

- `estimated_count` — Estimated number of Voronoi regions
- `max_edges` — Maximum vertices found on any region
- `avg_edges` — Average vertices per region

**Notes:**

- Automatically retries up to 50 times if the estimate falls outside the acceptance window
- The acceptance window widens by 5% per retry for convergence
- Returns the best estimate seen if max retries are exhausted
- Zero-area degenerate regions are excluded from the average (fixes #14)

---

### `load_data(filename, auto_bounds=True)`

Load point data from a file and cache it in memory. Subsequent calls return the cached list.

```python
from vormap import load_data

data = load_data("mypoints.txt")
print(f"Loaded {len(data)} points")
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `filename` | `str` | — | Filename inside `data/` directory |
| `auto_bounds` | `bool` | `True` | Auto-detect search bounds from data extents |

**Returns:** `list[tuple[float, float]]` — List of (longitude, latitude) tuples

**Raises:**

- `ValueError` — If the filename contains path traversal sequences (`..`, absolute paths)
- `ValueError` — If the file contains no valid points
- `FileNotFoundError` — If the file doesn't exist

**Security:** Filenames are validated to prevent directory traversal attacks. Only files within the `data/` directory are accessible.

---

### `find_area(data, lng, lat)`

Compute the Voronoi region area for the data point nearest to `(lng, lat)`.

```python
from vormap import load_data, find_area

data = load_data("mypoints.txt")
area, num_vertices = find_area(data, 100.5, 200.3)
print(f"Area: {area:.2f}, Vertices: {num_vertices}")
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `list` | Point list from `load_data()` |
| `lng` | `float` | Query longitude |
| `lat` | `float` | Query latitude |

**Returns:** `tuple[float, int]` — (area, vertex_count)

**Notes:**

- Traces the Voronoi region boundary by following perpendicular bisectors
- Uses binary search to find boundary intersection points
- Limited to `MAX_VERTICES` (50) per region to prevent infinite loops

---

### `get_NN(data, lng, lat)`

Find the nearest neighbor to `(lng, lat)` in the dataset.

```python
from vormap import load_data, get_NN

data = load_data("mypoints.txt")
nearest_lng, nearest_lat = get_NN(data, 500.0, 500.0)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `data` | `list` | Point list from `load_data()` |
| `lng` | `float` | Query longitude |
| `lat` | `float` | Query latitude |

**Returns:** `tuple[float, float]` — Nearest data point coordinates

**Performance:**

- **With SciPy:** O(log n) via KDTree — `_kdtree_by_id` provides O(1) tree lookup
- **Without SciPy:** O(n) brute-force scan

**Raises:** `ValueError` — If no valid neighbor found (e.g., query point is the only data point)

---

### `set_bounds(south, north, west, east)`

Manually set the search space boundaries.

```python
from vormap import set_bounds

set_bounds(south=-90, north=90, west=-180, east=180)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `south` | `float` | Southern boundary |
| `north` | `float` | Northern boundary |
| `west` | `float` | Western boundary |
| `east` | `float` | Eastern boundary |

---

### `compute_bounds(points, padding=0.1)`

Compute search space boundaries from a point set with padding.

```python
from vormap import compute_bounds

points = [(100, 200), (300, 400), (500, 600)]
south, north, west, east = compute_bounds(points, padding=0.15)
```

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `points` | `list` | — | List of (lng, lat) tuples |
| `padding` | `float` | 0.1 | Fraction of extent to add as padding on each side (at least 1.0 unit) |

**Returns:** `tuple[float, float, float, float]` — (south, north, west, east)

---

## Geometry Helpers

### `mid_point(x1, y1, x2, y2)`

Returns the midpoint of two 2D points, rounded to 2 decimal places.

### `perp_dir(x1, y1, x2, y2)`

Returns the slope of the line perpendicular to the segment from `(x1,y1)` to `(x2,y2)`. Returns `math.inf` for horizontal segments.

### `collinear(x1, y1, x2, y2, x3, y3, eps=1e-8)`

Tests whether three points are collinear using the cross-product method with relative tolerance.

### `eudist(x1, y1, x2, y2)`

Returns the Euclidean distance between two 2D points.

### `polygon_area(lngs, lats)`

Computes polygon area using the Shoelace formula. Takes parallel lists of x and y coordinates.

---

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `BIN_PREC` | `1e-6` | Binary search convergence threshold |
| `MAX_VERTICES` | `50` | Max vertices per Voronoi region |
| `MAX_RETRIES` | `50` | Max retry attempts in `get_sum()` |
| `BIN_SEARCH_MAX_ITER` | `100` | Binary search iteration limit |
| `NEW_DIR_MAX_ITER` | `200` | Direction-finding iteration limit |
