"""Voronoi-based spatial sampling for survey and study design.

Generates spatially stratified random samples using Voronoi regions
as strata. This is a real technique used in ecology, urban planning,
and geostatistics for designing efficient survey plans that ensure
spatial coverage while maintaining statistical validity.

Sampling strategies:
    - ``stratified_random``: Random points within each Voronoi region
      (proportional, equal, or custom allocation)
    - ``systematic_grid``: Regular grid points filtered to each region
    - ``centroid_based``: Region centroids with optional jitter
    - ``boundary_focused``: Samples concentrated near region boundaries
      for transition zone studies
    - ``density_weighted``: Allocation based on point density or weights
    - ``adaptive``: More samples in heterogeneous regions, fewer in
      uniform regions (based on area variance)

Supports export of sample plans with region metadata for field use.

Example::

    from vormap_sample import SpatialSampler

    sampler = SpatialSampler(seed_points, voronoi_regions)
    plan = sampler.stratified_random(samples_per_region=5, seed=42)
    sampler.export_sample_plan(plan, "fieldwork_plan.csv")
"""

import math
import random as _random
from vormap_geometry import polygon_area, polygon_centroid


def _point_in_polygon(x, y, vertices):
    """Ray-casting point-in-polygon test.

    Parameters
    ----------
    x, y : float
        Test point coordinates.
    vertices : list[tuple[float, float]]
        Ordered polygon vertices.

    Returns
    -------
    bool
        True if (x, y) is inside the polygon.
    """
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _polygon_bbox(vertices):
    """Axis-aligned bounding box of a polygon.

    Returns
    -------
    tuple[float, float, float, float]
        (x_min, x_max, y_min, y_max)
    """
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return min(xs), max(xs), min(ys), max(ys)


def _random_point_in_polygon(vertices, rng, max_attempts=1000):
    """Generate a uniformly random point inside a polygon via rejection sampling.

    Parameters
    ----------
    vertices : list[tuple[float, float]]
        Polygon vertices.
    rng : random.Random
        Random number generator.
    max_attempts : int
        Maximum rejection attempts before raising.

    Returns
    -------
    tuple[float, float]
        Random point inside the polygon.

    Raises
    ------
    RuntimeError
        If max_attempts exceeded (extremely thin or degenerate polygon).
    """
    x_min, x_max, y_min, y_max = _polygon_bbox(vertices)
    for _ in range(max_attempts):
        x = rng.uniform(x_min, x_max)
        y = rng.uniform(y_min, y_max)
        if _point_in_polygon(x, y, vertices):
            return (x, y)
    raise RuntimeError(
        f"Failed to generate point in polygon after {max_attempts} attempts. "
        "Polygon may be degenerate or extremely thin."
    )


def _edge_length(v1, v2):
    """Euclidean distance between two points."""
    dx = v2[0] - v1[0]
    dy = v2[1] - v1[1]
    return math.sqrt(dx * dx + dy * dy)


def _polygon_perimeter(vertices):
    """Compute polygon perimeter."""
    n = len(vertices)
    if n < 2:
        return 0.0
    total = 0.0
    for i in range(n):
        j = (i + 1) % n
        total += _edge_length(vertices[i], vertices[j])
    return total


class SpatialSampler:
    """Voronoi-based spatial sampling engine.

    Parameters
    ----------
    seed_points : list[tuple[float, float]]
        Original Voronoi seed (generator) points.
    regions : list[list[tuple[float, float]]]
        Voronoi region polygons. Each region is a list of (x, y) vertices.
        The i-th region corresponds to the i-th seed point.
    labels : list[str], optional
        Human-readable labels for each region.

    Raises
    ------
    ValueError
        If seed_points and regions have different lengths.
    """

    def __init__(self, seed_points, regions, labels=None):
        if len(seed_points) != len(regions):
            raise ValueError(
                f"seed_points ({len(seed_points)}) and regions ({len(regions)}) "
                "must have the same length"
            )
        self.seed_points = list(seed_points)
        self.regions = [list(r) for r in regions]
        self.labels = labels or [f"Region_{i}" for i in range(len(regions))]
        self._areas = None
        self._centroids = None

    @property
    def areas(self):
        """Lazily computed region areas."""
        if self._areas is None:
            self._areas = [polygon_area(r) for r in self.regions]
        return self._areas

    @property
    def centroids(self):
        """Lazily computed region centroids."""
        if self._centroids is None:
            self._centroids = [polygon_centroid(r) for r in self.regions]
        return self._centroids

    @property
    def n_regions(self):
        """Number of Voronoi regions."""
        return len(self.regions)

    def stratified_random(self, samples_per_region=None, total_samples=None,
                          allocation="proportional", weights=None, *,
                          seed=None):
        """Generate stratified random samples within Voronoi regions.

        Each region acts as a stratum. Points are uniformly distributed
        within each region via rejection sampling.

        Parameters
        ----------
        samples_per_region : int, optional
            Fixed number of samples per region (equal allocation).
            Mutually exclusive with total_samples.
        total_samples : int, optional
            Total samples to distribute across regions according to
            the allocation strategy. Mutually exclusive with samples_per_region.
        allocation : str
            How to distribute total_samples across regions:
            - "proportional": proportional to region area (Neyman allocation)
            - "equal": same number per region
            - "optimal": proportional to area × perimeter (favoring complex regions)
        weights : list[float], optional
            Custom per-region weights for allocation. Overrides allocation strategy.
        seed : int, optional
            Random seed for reproducibility.

        Returns
        -------
        SamplePlan
            Contains sample points with region assignments and metadata.

        Raises
        ------
        ValueError
            If neither samples_per_region nor total_samples is specified.
        """
        if samples_per_region is None and total_samples is None:
            raise ValueError("Specify either samples_per_region or total_samples")
        if samples_per_region is not None and total_samples is not None:
            raise ValueError("Specify only one of samples_per_region or total_samples")

        rng = _random.Random(seed)

        if samples_per_region is not None:
            counts = [samples_per_region] * self.n_regions
        else:
            counts = self._allocate(total_samples, allocation, weights)

        samples = []
        for i, region in enumerate(self.regions):
            area = self.areas[i]
            if area < 1e-12 or counts[i] == 0:
                continue
            for j in range(counts[i]):
                pt = _random_point_in_polygon(region, rng)
                samples.append(SamplePoint(
                    x=pt[0], y=pt[1],
                    region_index=i,
                    region_label=self.labels[i],
                    sample_id=f"{self.labels[i]}_s{j}",
                    method="stratified_random"
                ))

        return SamplePlan(samples=samples, method="stratified_random",
                          n_regions=self.n_regions, seed=seed)

    def systematic_grid(self, spacing, *, seed=None):
        """Generate systematic grid samples within each Voronoi region.

        Creates a regular grid over the bounding box and assigns each
        grid point to its containing region.

        Parameters
        ----------
        spacing : float
            Grid spacing in coordinate units. Must be > 0.
        seed : int, optional
            Random seed for grid origin offset (randomized start).

        Returns
        -------
        SamplePlan
        """
        if spacing <= 0:
            raise ValueError("Spacing must be positive")

        rng = _random.Random(seed)

        # Global bounding box
        all_x = [v[0] for r in self.regions for v in r]
        all_y = [v[1] for r in self.regions for v in r]
        x_min, x_max = min(all_x), max(all_x)
        y_min, y_max = min(all_y), max(all_y)

        # Random offset for unbiased systematic sampling
        x_offset = rng.uniform(0, spacing)
        y_offset = rng.uniform(0, spacing)

        # Generate grid points
        grid_points = []
        x = x_min + x_offset
        while x <= x_max:
            y = y_min + y_offset
            while y <= y_max:
                grid_points.append((x, y))
                y += spacing
            x += spacing

        # Assign to regions
        samples = []
        region_counters = [0] * self.n_regions
        for pt in grid_points:
            for i, region in enumerate(self.regions):
                if _point_in_polygon(pt[0], pt[1], region):
                    sid = f"{self.labels[i]}_g{region_counters[i]}"
                    region_counters[i] += 1
                    samples.append(SamplePoint(
                        x=pt[0], y=pt[1],
                        region_index=i,
                        region_label=self.labels[i],
                        sample_id=sid,
                        method="systematic_grid"
                    ))
                    break  # Each point belongs to one region

        return SamplePlan(samples=samples, method="systematic_grid",
                          n_regions=self.n_regions, seed=seed)

    def centroid_based(self, jitter=0.0, *, seed=None):
        """Sample at region centroids with optional random jitter.

        Parameters
        ----------
        jitter : float
            Maximum jitter distance (uniform in each axis). 0 = exact centroids.
        seed : int, optional
            Random seed for jitter.

        Returns
        -------
        SamplePlan
        """
        if jitter < 0:
            raise ValueError("Jitter must be non-negative")

        rng = _random.Random(seed)
        samples = []

        for i, centroid in enumerate(self.centroids):
            cx, cy = centroid
            if jitter > 0:
                cx += rng.uniform(-jitter, jitter)
                cy += rng.uniform(-jitter, jitter)
            samples.append(SamplePoint(
                x=cx, y=cy,
                region_index=i,
                region_label=self.labels[i],
                sample_id=f"{self.labels[i]}_c0",
                method="centroid"
            ))

        return SamplePlan(samples=samples, method="centroid",
                          n_regions=self.n_regions, seed=seed)

    def boundary_focused(self, samples_per_edge=2, buffer_distance=None,
                         *, seed=None):
        """Generate samples near region boundaries for transition studies.

        Places samples along region edges with optional inward buffer,
        useful for studying transition zones between regions.

        Parameters
        ----------
        samples_per_edge : int
            Number of samples per polygon edge.
        buffer_distance : float, optional
            Inward offset from boundary. If None, uses 5% of mean edge length.
        seed : int, optional
            Random seed.

        Returns
        -------
        SamplePlan
        """
        if samples_per_edge < 1:
            raise ValueError("samples_per_edge must be >= 1")

        rng = _random.Random(seed)
        samples = []

        for i, region in enumerate(self.regions):
            n_verts = len(region)
            if n_verts < 3:
                continue

            centroid = self.centroids[i]
            mean_edge = _polygon_perimeter(region) / n_verts
            buf = buffer_distance if buffer_distance is not None else mean_edge * 0.05

            counter = 0
            for vi in range(n_verts):
                vj = (vi + 1) % n_verts
                v1, v2 = region[vi], region[vj]

                for s in range(samples_per_edge):
                    t = (s + 1) / (samples_per_edge + 1)
                    # Point on edge
                    ex = v1[0] + t * (v2[0] - v1[0])
                    ey = v1[1] + t * (v2[1] - v1[1])
                    # Offset inward toward centroid
                    if buf > 0:
                        dx = centroid[0] - ex
                        dy = centroid[1] - ey
                        dist = math.sqrt(dx * dx + dy * dy)
                        if dist > 1e-12:
                            ex += buf * dx / dist
                            ey += buf * dy / dist
                    # Small random perturbation
                    ex += rng.uniform(-buf * 0.1, buf * 0.1)
                    ey += rng.uniform(-buf * 0.1, buf * 0.1)

                    samples.append(SamplePoint(
                        x=ex, y=ey,
                        region_index=i,
                        region_label=self.labels[i],
                        sample_id=f"{self.labels[i]}_b{counter}",
                        method="boundary_focused"
                    ))
                    counter += 1

        return SamplePlan(samples=samples, method="boundary_focused",
                          n_regions=self.n_regions, seed=seed)

    def density_weighted(self, total_samples, density_values, *, seed=None):
        """Allocate samples proportional to a density or importance field.

        Parameters
        ----------
        total_samples : int
            Total number of samples to generate.
        density_values : list[float]
            Per-region density/importance values. Higher values get more samples.
        seed : int, optional
            Random seed.

        Returns
        -------
        SamplePlan

        Raises
        ------
        ValueError
            If density_values length doesn't match number of regions.
        """
        if len(density_values) != self.n_regions:
            raise ValueError(
                f"density_values ({len(density_values)}) must match "
                f"number of regions ({self.n_regions})"
            )
        if total_samples < 1:
            raise ValueError("total_samples must be >= 1")

        counts = self._allocate(total_samples, "proportional",
                                weights=density_values)
        rng = _random.Random(seed)

        samples = []
        for i, region in enumerate(self.regions):
            area = self.areas[i]
            if area < 1e-12 or counts[i] == 0:
                continue
            for j in range(counts[i]):
                pt = _random_point_in_polygon(region, rng)
                samples.append(SamplePoint(
                    x=pt[0], y=pt[1],
                    region_index=i,
                    region_label=self.labels[i],
                    sample_id=f"{self.labels[i]}_d{j}",
                    method="density_weighted"
                ))

        return SamplePlan(samples=samples, method="density_weighted",
                          n_regions=self.n_regions, seed=seed)

    def adaptive(self, total_samples, heterogeneity_values=None, *, seed=None):
        """Adaptive sampling: more samples in heterogeneous regions.

        Regions with higher heterogeneity (variance, complexity) receive
        more samples. If heterogeneity_values are not provided, uses
        a shape complexity metric (perimeter²/area ratio).

        Parameters
        ----------
        total_samples : int
            Total number of samples.
        heterogeneity_values : list[float], optional
            Per-region heterogeneity measures. If None, uses shape complexity.
        seed : int, optional
            Random seed.

        Returns
        -------
        SamplePlan
        """
        if total_samples < 1:
            raise ValueError("total_samples must be >= 1")

        if heterogeneity_values is None:
            # Use shape complexity: perimeter² / area (higher = more complex)
            heterogeneity_values = []
            for i, region in enumerate(self.regions):
                area = self.areas[i]
                perim = _polygon_perimeter(region)
                if area > 1e-12:
                    complexity = (perim * perim) / area
                else:
                    complexity = 0.0
                heterogeneity_values.append(complexity)
        elif len(heterogeneity_values) != self.n_regions:
            raise ValueError(
                f"heterogeneity_values ({len(heterogeneity_values)}) must "
                f"match number of regions ({self.n_regions})"
            )

        # Weight by area × heterogeneity for balanced adaptive allocation
        weights = []
        for i in range(self.n_regions):
            w = self.areas[i] * max(heterogeneity_values[i], 0.001)
            weights.append(w)

        counts = self._allocate(total_samples, "proportional", weights=weights)
        rng = _random.Random(seed)

        samples = []
        for i, region in enumerate(self.regions):
            area = self.areas[i]
            if area < 1e-12 or counts[i] == 0:
                continue
            for j in range(counts[i]):
                pt = _random_point_in_polygon(region, rng)
                samples.append(SamplePoint(
                    x=pt[0], y=pt[1],
                    region_index=i,
                    region_label=self.labels[i],
                    sample_id=f"{self.labels[i]}_a{j}",
                    method="adaptive"
                ))

        return SamplePlan(samples=samples, method="adaptive",
                          n_regions=self.n_regions, seed=seed)

    def _allocate(self, total, allocation, weights=None):
        """Distribute total samples across regions.

        Returns
        -------
        list[int]
            Per-region sample counts that sum to total.
        """
        n = self.n_regions
        if allocation == "equal":
            base = total // n
            remainder = total % n
            counts = [base] * n
            # Distribute remainder to largest regions
            area_order = sorted(range(n), key=lambda i: self.areas[i],
                                reverse=True)
            for i in range(remainder):
                counts[area_order[i]] += 1
            return counts

        # Weighted allocation (proportional or optimal or custom weights)
        if weights is not None:
            w = [max(0.0, v) for v in weights]
        elif allocation == "optimal":
            w = []
            for i in range(n):
                area = self.areas[i]
                perim = _polygon_perimeter(self.regions[i])
                w.append(area * perim)
        else:  # proportional
            w = list(self.areas)

        total_w = sum(w)
        if total_w < 1e-12:
            return self._allocate(total, "equal")

        # Largest-remainder method for integer allocation
        raw = [(w[i] / total_w) * total for i in range(n)]
        floor_counts = [int(f) for f in raw]
        remainders = [(raw[i] - floor_counts[i], i) for i in range(n)]
        allocated = sum(floor_counts)
        remainders.sort(key=lambda x: -x[0])
        for i in range(total - allocated):
            floor_counts[remainders[i][1]] += 1

        return floor_counts

    def export_sample_plan(self, plan, filepath, format="csv"):
        """Export a sample plan to CSV or GeoJSON for field use.

        Parameters
        ----------
        plan : SamplePlan
            The sample plan to export.
        filepath : str
            Output file path.
        format : str
            "csv" or "geojson".
        """
        if format == "csv":
            self._export_csv(plan, filepath)
        elif format == "geojson":
            self._export_geojson(plan, filepath)
        else:
            raise ValueError(f"Unsupported format: {format}. Use 'csv' or 'geojson'.")

    def _export_csv(self, plan, filepath):
        """Export sample plan as CSV."""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("sample_id,x,y,region_index,region_label,method\n")
            for s in plan.samples:
                f.write(f"{s.sample_id},{s.x:.6f},{s.y:.6f},"
                        f"{s.region_index},{s.region_label},{s.method}\n")

    def _export_geojson(self, plan, filepath):
        """Export sample plan as GeoJSON FeatureCollection."""
        import json
        features = []
        for s in plan.samples:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [s.x, s.y]
                },
                "properties": {
                    "sample_id": s.sample_id,
                    "region_index": s.region_index,
                    "region_label": s.region_label,
                    "method": s.method
                }
            })
        geojson = {
            "type": "FeatureCollection",
            "features": features
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(geojson, f, indent=2)

    def coverage_analysis(self, plan):
        """Analyze spatial coverage of a sample plan.

        Returns per-region statistics and overall coverage metrics.

        Parameters
        ----------
        plan : SamplePlan

        Returns
        -------
        dict
            Coverage statistics including samples/region, density, gaps.
        """
        region_counts = [0] * self.n_regions
        for s in plan.samples:
            if 0 <= s.region_index < self.n_regions:
                region_counts[s.region_index] += 1

        total_area = sum(self.areas)
        covered_area = sum(a for a, c in zip(self.areas, region_counts) if c > 0)
        uncovered = [i for i, c in enumerate(region_counts) if c == 0]

        densities = []
        for i in range(self.n_regions):
            area = self.areas[i]
            density = region_counts[i] / area if area > 1e-12 else 0.0
            densities.append(density)

        mean_density = sum(densities) / len(densities) if densities else 0.0
        density_cv = 0.0
        if mean_density > 1e-12 and len(densities) > 1:
            variance = sum((d - mean_density) ** 2 for d in densities) / len(densities)
            density_cv = math.sqrt(variance) / mean_density

        return {
            "total_samples": len(plan.samples),
            "n_regions": self.n_regions,
            "covered_regions": self.n_regions - len(uncovered),
            "uncovered_regions": uncovered,
            "coverage_pct": (self.n_regions - len(uncovered)) / self.n_regions * 100
                            if self.n_regions > 0 else 0,
            "area_coverage_pct": covered_area / total_area * 100
                                 if total_area > 1e-12 else 0,
            "samples_per_region": region_counts,
            "sample_density": densities,
            "mean_density": mean_density,
            "density_cv": density_cv,  # Coefficient of variation (lower = more uniform)
        }


class SamplePoint:
    """A single sample location with metadata.

    Attributes
    ----------
    x, y : float
        Sample coordinates.
    region_index : int
        Index of the containing Voronoi region.
    region_label : str
        Human-readable region label.
    sample_id : str
        Unique sample identifier.
    method : str
        Sampling method that generated this point.
    """

    __slots__ = ('x', 'y', 'region_index', 'region_label', 'sample_id', 'method')

    def __init__(self, x, y, region_index, region_label, sample_id, method):
        self.x = x
        self.y = y
        self.region_index = region_index
        self.region_label = region_label
        self.sample_id = sample_id
        self.method = method

    def to_dict(self):
        return {
            'x': self.x, 'y': self.y,
            'region_index': self.region_index,
            'region_label': self.region_label,
            'sample_id': self.sample_id,
            'method': self.method,
        }

    def __repr__(self):
        return (f"SamplePoint({self.sample_id}, x={self.x:.4f}, "
                f"y={self.y:.4f}, region={self.region_label})")


class SamplePlan:
    """A collection of sample points with metadata.

    Attributes
    ----------
    samples : list[SamplePoint]
        All sample locations.
    method : str
        Sampling strategy used.
    n_regions : int
        Number of Voronoi regions in the design.
    seed : int or None
        Random seed for reproducibility.
    """

    def __init__(self, samples, method, n_regions, seed=None):
        self.samples = list(samples)
        self.method = method
        self.n_regions = n_regions
        self.seed = seed

    @property
    def n_samples(self):
        return len(self.samples)

    def points(self):
        """Return sample locations as (x, y) tuples."""
        return [(s.x, s.y) for s in self.samples]

    def by_region(self):
        """Group samples by region index.

        Returns
        -------
        dict[int, list[SamplePoint]]
        """
        groups = {}
        for s in self.samples:
            groups.setdefault(s.region_index, []).append(s)
        return groups

    def summary(self):
        """Human-readable summary string."""
        by_r = self.by_region()
        counts = [len(by_r.get(i, [])) for i in range(self.n_regions)]
        min_c = min(counts) if counts else 0
        max_c = max(counts) if counts else 0
        mean_c = sum(counts) / len(counts) if counts else 0
        return (
            f"SamplePlan: {self.n_samples} samples across {self.n_regions} regions "
            f"(method={self.method}, min={min_c}, max={max_c}, "
            f"mean={mean_c:.1f}, seed={self.seed})"
        )

    def __repr__(self):
        return self.summary()
