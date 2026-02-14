# Implementation Details

This page describes the key implementation decisions and internal architecture of VoronoiMap.

## Module Structure

The entire algorithm is implemented in a single module (`vormap.py`) for simplicity. The module contains:

```
vormap.py
├── Global state (bounds, caches)
├── Data loading (load_data, compute_bounds)
├── Nearest neighbor (get_NN, Oracle)
├── Geometry helpers (mid_point, perp_dir, collinear, eudist, polygon_area)
├── Voronoi construction (find_area, find_a1, new_dir, bin_search)
├── Line intersection (isect, isect_B, find_CXY, find_BXY)
├── Estimation (get_sum)
└── CLI (main)
```

## Caching Architecture

VoronoiMap uses a multi-level caching strategy to minimize redundant computation:

### Data Cache

```python
_data_cache = {}  # filename → list of (lng, lat) tuples
```

`load_data()` reads a file once and caches the parsed point list. Subsequent calls return the same list object (identity-preserving).

### KDTree Cache

```python
_kdtree_cache = {}      # filename → KDTree
_kdtree_by_id = {}      # id(data_list) → KDTree
```

When SciPy is available, a KDTree is built at load time. The `_kdtree_by_id` dict provides O(1) tree lookup in `get_NN()` using `id(data)` as the key, avoiding the older O(n) identity scan pattern.

## Binary Search for Boundaries

The core of Voronoi region discovery is binary search along rays:

```python
def bin_search(data, x1, y1, x2, y2, dlng, dlat):
    """Find the boundary between two Voronoi regions."""
    for _ in range(BIN_SEARCH_MAX_ITER):  # max 100 iterations
        if eudist(x1, y1, x2, y2) <= BIN_PREC:  # 1e-6 threshold
            break
        xm = (x1 + x2) / 2
        ym = (y1 + y2) / 2
        lg, lt = get_NN(data, xm, ym)
        # If midpoint's NN is the same as the target, narrow toward boundary
        # Otherwise, narrow toward the target
```

The iteration limit (100) far exceeds what's needed for float64 precision (52-bit mantissa ≈ 52 iterations), providing a safety margin.

## Boundary Intersection

`isect_B()` computes where a line from a point with a given slope intersects the search space boundary rectangle. It handles four cases:

1. **Vertical line** (`slope = inf`): Intersects north and south edges
2. **Horizontal line** (`slope = 0`): Intersects west and east edges
3. **General case**: Checks all four edges and returns the two that are hit

## Robustness Measures

### Collinearity Detection

The `collinear()` function uses cross-product with relative scaling:

```python
cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
scale = max(len1 * len2, 1e-12)
return abs(cross) / scale < eps
```

This handles both very large and very small coordinate ranges, unlike the previous slope-comparison approach.

### Parallel Line Detection

`isect()` uses relative tolerance for near-parallel lines:

```python
if abs(m1 - m2) < 1e-10 * max(abs(m1), abs(m2), 1.0):
    return -1, -1  # parallel, no intersection
```

This prevents wildly large intersection coordinates from floating-point near-misses.

### Degenerate Region Handling

- **Zero-area regions**: Excluded from the `get_sum()` average using a clean valid-estimates list
- **Excessive vertices**: `MAX_VERTICES = 50` limit prevents infinite polygon tracing
- **Division by zero**: Guards in `find_CXY()` and `find_BXY()` when boundary endpoints coincide

## Security Hardening

### Path Traversal Protection

```python
# Reject absolute paths
if os.path.isabs(filename):
    raise ValueError(...)

# Verify resolved path stays inside data/
data_dir = os.path.abspath("data")
resolved = os.path.abspath(os.path.join("data", filename))
if not resolved.startswith(data_dir + os.sep):
    raise ValueError(...)
```

### Input Sanitization

- Non-numeric lines are skipped (not crashed on)
- `NaN` and `Inf` coordinates are rejected via `math.isfinite()`
- Empty files raise `ValueError`

## Performance Characteristics

### With SciPy (Recommended)

| Dataset Size | NN Query | Full Estimation (k=10) |
|-------------|----------|----------------------|
| 10 points | ~1μs | ~50ms |
| 100 points | ~2μs | ~200ms |
| 1,000 points | ~3μs | ~500ms |
| 10,000 points | ~4μs | ~1s |

### Without SciPy

| Dataset Size | NN Query | Full Estimation (k=10) |
|-------------|----------|----------------------|
| 10 points | ~5μs | ~60ms |
| 100 points | ~50μs | ~600ms |
| 1,000 points | ~500μs | ~6s |
| 10,000 points | ~5ms | ~60s |

The KDTree provides the biggest speedup for large datasets where each NN query would otherwise be O(n).
