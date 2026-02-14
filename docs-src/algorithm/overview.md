# Algorithm Overview

VoronoiMap implements the **EstimateSUM** algorithm for estimating the cardinality (size) of an unknown point set using only nearest-neighbor oracle access.

## Problem Setting

Given:

- A bounded 2D search space $[W, E] \times [S, N]$
- An unknown set of points $P = \{p_1, p_2, \ldots, p_n\}$ within the space
- A **nearest-neighbor oracle** $\text{NN}(q)$ that, given any query point $q$, returns the point in $P$ closest to $q$

**Goal:** Estimate $n = |P|$ — the number of points — using as few oracle queries as possible.

## Algorithm Steps

The algorithm works by sampling random points, discovering data points via the oracle, constructing approximate Voronoi regions, and using area ratios to estimate the total count.

### Step 1: Random Sampling

Sample a point $(p_x, p_y)$ uniformly at random within the search bounds:

$$p_x \sim \text{Uniform}(W, E), \quad p_y \sim \text{Uniform}(S, N)$$

### Step 2: Oracle Query

Query the nearest-neighbor oracle to find the closest data point:

$$d = \text{NN}(p_x, p_y)$$

This reveals a data point without knowing the full dataset.

### Step 3: Voronoi Region Discovery

For the discovered point $d$, trace the boundary of its Voronoi region using binary search:

1. **Find a boundary edge:** Compute the perpendicular bisector between $d$ and its nearest neighbor $e$. Binary search along this bisector to find the exact boundary point.

2. **Follow the boundary:** From each boundary vertex, find the next vertex by:
   - Searching along new directions from the current vertex
   - Using perpendicular bisectors to adjacent regions
   - Detecting when the polygon closes (direction matches initial direction, or intersection with the first edge is found)

3. **Collect vertices:** The boundary vertices form a polygon approximating the Voronoi cell.

### Step 4: Area Computation

Compute the area of the discovered Voronoi region using the **Shoelace formula**:

$$A = \frac{1}{2} \left| \sum_{i=0}^{n-1} (x_i y_{i+1} - x_{i+1} y_i) \right|$$

### Step 5: Estimation

The key insight: if data points are distributed across the search space, each Voronoi cell area is approximately $\frac{\text{Total Area}}{n}$. Therefore:

$$\hat{n}_i = \frac{(N - S)(E - W)}{A_i}$$

Averaging over $k$ samples:

$$\hat{n} = \frac{1}{k} \sum_{i=1}^{k} \hat{n}_i$$

## Convergence

The algorithm uses a **retry mechanism** with an adaptive acceptance window:

- Initial window: $[0.5 \cdot k, 1.5 \cdot k]$
- Window widens by 5% per retry
- Maximum 50 retries
- Best estimate is tracked and returned if retries are exhausted

## Complexity

| Operation | With SciPy | Without SciPy |
|-----------|-----------|---------------|
| Nearest-neighbor query | $O(\log n)$ | $O(n)$ |
| Binary search (per edge) | $O(B \cdot \text{NN})$ | $O(B \cdot \text{NN})$ |
| Total per sample | $O(V \cdot B \cdot \log n)$ | $O(V \cdot B \cdot n)$ |

Where $B$ = binary search iterations (~100), $V$ = vertices per region (~5-8 typical), $n$ = dataset size.

## Diagram

```
┌─────────────────────────────────────────────────────────────┐
│  Search Space [W, E] × [S, N]                              │
│                                                             │
│     ●───────────────────●                                   │
│     │   Voronoi Cell    │    ★ Random sample point          │
│     │                   │    ↓                              │
│     │     ◆ d           │    NN query → discovers ◆         │
│     │   (data point)    │                                   │
│     │                   │    Binary search along bisectors  │
│     ●───────────────────●    → discovers cell boundary ●    │
│                                                             │
│     Area(cell) → Estimate = Total_Area / Cell_Area          │
└─────────────────────────────────────────────────────────────┘
```

## References

- Original research paper: [Full Report (PDF)](http://docdro.id/5HXe2wV)
- Voronoi diagrams: [Wikipedia](https://en.wikipedia.org/wiki/Voronoi_diagram)
- Nearest-neighbor search: [Wikipedia](https://en.wikipedia.org/wiki/Nearest_neighbor_search)
