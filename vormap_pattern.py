"""Point pattern analysis for Voronoi seed distributions.

Statistical tools to characterize spatial point patterns as random,
clustered, or dispersed.  Useful for understanding the distribution
of Voronoi seed points and validating synthetic seed generators.

Analyses provided
-----------------
- **Clark-Evans Nearest Neighbor Index (NNI):**
  Ratio of observed mean nearest-neighbor distance to the expected
  value under complete spatial randomness (CSR).  R < 1 = clustered,
  R ~ 1 = random, R > 1 = dispersed.

- **Ripley's K and Besag's L function:**
  Multi-scale spatial analysis.  K(r) counts the expected number of
  extra points within distance *r* of a typical point, normalised by
  density.  L(r) = sqrt(K(r)/pi) - r linearises the comparison with
  CSR: L(r) > 0 = clustering at scale r, L(r) < 0 = dispersion.

- **Quadrat analysis:**
  Divides the study area into a grid and counts points per cell.
  Chi-squared test against the Poisson expectation under CSR,
  plus Variance-to-Mean Ratio (VMR): VMR > 1 = clustered,
  VMR ~ 1 = random, VMR < 1 = dispersed.

- **Mean center and standard distance:**
  Centroid and spatial spread of the point cloud.

- **Convex hull area ratio:**
  Ratio of convex hull area to bounding box area -- measures how
  much of the study region is actually utilised.
"""

from __future__ import annotations

import math
from collections import namedtuple

from vormap import eudist_pts
from vormap_geometry import cross_product_2d as _cross, normal_cdf as _normal_cdf


# -- Result types ----------------------------------------------------

NNIResult = namedtuple("NNIResult", [
    "observed_mean",    # mean observed nearest-neighbor distance
    "expected_mean",    # expected mean NN distance under CSR
    "nni",              # nearest-neighbor index (R)
    "z_score",          # standard normal z score
    "p_value",          # two-sided p-value (approximate)
    "interpretation",   # "clustered" / "random" / "dispersed"
    "n",                # number of points
])

RipleysResult = namedtuple("RipleysResult", [
    "radii",            # list of r values
    "k_values",         # K(r) for each radius
    "l_values",         # L(r) = sqrt(K(r)/pi) - r
    "csr_k",            # K(r) under CSR = pi r^2
    "density",          # point density lambda = n / area
    "n",                # number of points
    "area",             # study area
    "peak_clustering_r",  # radius of maximum L(r) (strongest clustering)
    "peak_clustering_l",  # maximum L(r) value
])

QuadratResult = namedtuple("QuadratResult", [
    "counts",           # list of point counts per cell (row-major)
    "rows",             # number of grid rows
    "cols",             # number of grid columns
    "chi_squared",      # chi-squared statistic
    "df",               # degrees of freedom
    "p_value",          # p-value from chi-squared test
    "vmr",              # variance-to-mean ratio
    "interpretation",   # "clustered" / "random" / "dispersed"
    "expected_count",   # expected count per cell under CSR
])

PatternSummary = namedtuple("PatternSummary", [
    "n",                # number of points
    "mean_center",      # (x, y) centroid
    "std_distance",     # standard distance (spatial spread)
    "hull_area_ratio",  # convex hull area / bounding box area
    "nni",              # NNIResult
    "quadrat",          # QuadratResult
    "ripleys",          # RipleysResult
    "overall",          # overall interpretation
])


# -- Helper utilities ------------------------------------------------

def _validate_points(points):
    """Validate and normalize a point list."""
    if not points or len(points) < 2:
        raise ValueError("Need at least 2 points for pattern analysis")
    pts = [(float(x), float(y)) for x, y in points]
    return pts


def _compute_nn_distances(points):
    """Compute nearest-neighbor distance for each point.

    Uses scipy KDTree when available (O(n log n)), otherwise brute
    force O(n^2).
    """
    n = len(points)
    try:
        from scipy.spatial import KDTree
        tree = KDTree(points)
        dists, _ = tree.query(points, k=2)  # k=2: self + nearest
        return [dists[i][1] for i in range(n)]
    except ImportError:
        pass

    # Brute force fallback
    nn_dists = []
    for i in range(n):
        min_d = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = eudist_pts(points[i], points[j])
            if d < min_d:
                min_d = d
        nn_dists.append(min_d)
    return nn_dists


def _compute_bounds(points):
    """Get bounding box (xmin, xmax, ymin, ymax) for a point set."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def _bounding_area(points, bounds=None):
    """Compute the bounding-box area of a point set."""
    if bounds:
        xmin, xmax, ymin, ymax = bounds
    else:
        xmin, xmax, ymin, ymax = _compute_bounds(points)
    return max((xmax - xmin) * (ymax - ymin), 1e-10)


def _chi2_survival(x, k):
    """Approximate survival function (1 - CDF) for chi-squared distribution.

    Uses the Wilson-Hilferty normal approximation for k > 0.
    """
    if x <= 0:
        return 1.0
    if k <= 0:
        return 0.0
    # Wilson-Hilferty approximation
    z = ((x / k) ** (1.0 / 3.0) - (1.0 - 2.0 / (9.0 * k))) / math.sqrt(2.0 / (9.0 * k))
    return 1.0 - _normal_cdf(z)


def _convex_hull_area(points):
    """Compute convex hull area using Andrew's monotone chain algorithm.

    Returns the area of the convex hull of the given 2D points.
    """
    pts = sorted(set(points))
    if len(pts) < 3:
        return 0.0

    # Build lower hull
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return 0.0

    # Shoelace formula
    area = 0.0
    n = len(hull)
    for i in range(n):
        j = (i + 1) % n
        area += hull[i][0] * hull[j][1]
        area -= hull[j][0] * hull[i][1]
    return abs(area) / 2.0


# -- Core analyses ---------------------------------------------------

def clark_evans_nni(points, bounds=None):
    """Compute the Clark-Evans Nearest Neighbor Index.

    Parameters
    ----------
    points : list of (x, y) tuples
        The point pattern to analyse.
    bounds : tuple of (xmin, xmax, ymin, ymax), optional
        Study area bounds.  If not given, derived from point extents.

    Returns
    -------
    NNIResult
        Named tuple with observed/expected means, NNI (R), z-score,
        p-value, and interpretation.
    """
    pts = _validate_points(points)
    n = len(pts)
    area = _bounding_area(pts, bounds)
    density = n / area

    # Observed mean nearest-neighbor distance
    nn_dists = _compute_nn_distances(pts)
    observed_mean = sum(nn_dists) / n

    # Expected mean NN distance under CSR: 1 / (2 * sqrt(lambda))
    expected_mean = 1.0 / (2.0 * math.sqrt(density))

    # NNI (R)
    nni = observed_mean / expected_mean if expected_mean > 0 else 0

    # Standard error under CSR
    se = 0.26136 / math.sqrt(n * density)

    # Z-score
    z = (observed_mean - expected_mean) / se if se > 0 else 0

    # Two-sided p-value
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))

    # Interpretation
    if nni < 0.8:
        interp = "clustered"
    elif nni > 1.2:
        interp = "dispersed"
    else:
        interp = "random"

    return NNIResult(
        observed_mean=round(observed_mean, 6),
        expected_mean=round(expected_mean, 6),
        nni=round(nni, 4),
        z_score=round(z, 4),
        p_value=round(p, 6),
        interpretation=interp,
        n=n,
    )


def ripleys_k(points, radii=None, n_radii=20, bounds=None):
    """Compute Ripley's K function and Besag's L function.

    Parameters
    ----------
    points : list of (x, y) tuples
        The point pattern.
    radii : list of float, optional
        Specific radii to evaluate.  If not given, *n_radii* equally
        spaced values from 0 to max_nn_distance * 2 are used.
    n_radii : int
        Number of radii when *radii* is not given (default 20).
    bounds : tuple of (xmin, xmax, ymin, ymax), optional
        Study area bounds.

    Returns
    -------
    RipleysResult
        Named tuple with radii, K values, L values, CSR K, density,
        and peak clustering information.
    """
    pts = _validate_points(points)
    n = len(pts)
    area = _bounding_area(pts, bounds)
    density = n / area

    if not isinstance(n_radii, int) or n_radii < 1:
        raise ValueError(f"n_radii must be a positive integer, got {n_radii}")
    if n_radii > 10000:
        raise ValueError(
            f"n_radii={n_radii} is excessive — max 10,000. "
            f"Each radius requires O(n²) computation."
        )

    if radii is None:
        nn_dists = _compute_nn_distances(pts)
        max_r = max(nn_dists) * 2.0
        if max_r <= 0:
            max_r = math.sqrt(area / n)
        radii = [max_r * (i + 1) / n_radii for i in range(n_radii)]

    k_values = []
    l_values = []
    csr_k = []

    for r in radii:
        r_sq = r * r
        count = 0
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                d_sq = (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2
                if d_sq <= r_sq:
                    count += 1
        k_r = (area / (n * (n - 1))) * count
        l_r = math.sqrt(k_r / math.pi) - r if k_r >= 0 else -r
        csr_kr = math.pi * r_sq

        k_values.append(round(k_r, 6))
        l_values.append(round(l_r, 6))
        csr_k.append(round(csr_kr, 6))

    # Find peak clustering
    peak_idx = 0
    peak_l = l_values[0]
    for i, lv in enumerate(l_values):
        if lv > peak_l:
            peak_l = lv
            peak_idx = i

    return RipleysResult(
        radii=[round(r, 6) for r in radii],
        k_values=k_values,
        l_values=l_values,
        csr_k=csr_k,
        density=round(density, 8),
        n=n,
        area=round(area, 4),
        peak_clustering_r=round(radii[peak_idx], 6),
        peak_clustering_l=round(peak_l, 6),
    )


def quadrat_analysis(points, rows=None, cols=None, bounds=None):
    """Perform quadrat (grid cell) analysis for spatial randomness.

    Parameters
    ----------
    points : list of (x, y) tuples
        The point pattern.
    rows, cols : int, optional
        Grid dimensions.  If not given, uses ceil(sqrt(n/2)) for each.
    bounds : tuple of (xmin, xmax, ymin, ymax), optional
        Study area bounds.

    Returns
    -------
    QuadratResult
        Named tuple with cell counts, chi-squared statistic, p-value,
        VMR, and interpretation.
    """
    pts = _validate_points(points)
    n = len(pts)

    if bounds:
        xmin, xmax, ymin, ymax = bounds
    else:
        xmin, xmax, ymin, ymax = _compute_bounds(pts)

    # Default grid size
    if rows is None:
        rows = max(2, math.ceil(math.sqrt(n / 2)))
    if cols is None:
        cols = max(2, math.ceil(math.sqrt(n / 2)))

    width = xmax - xmin
    height = ymax - ymin
    if width <= 0 or height <= 0:
        raise ValueError("Degenerate bounding box (zero width or height)")

    cell_w = width / cols
    cell_h = height / rows

    # Count points per cell
    counts = [0] * (rows * cols)
    for x, y in pts:
        c = min(int((x - xmin) / cell_w), cols - 1)
        r = min(int((y - ymin) / cell_h), rows - 1)
        counts[r * cols + c] += 1

    total_cells = rows * cols
    expected = n / total_cells

    # Chi-squared test
    chi2 = sum((c - expected) ** 2 / expected for c in counts) if expected > 0 else 0
    df = total_cells - 1
    p_value = _chi2_survival(chi2, df) if df > 0 else 1.0

    # Variance-to-Mean Ratio
    mean_count = sum(counts) / total_cells
    if mean_count > 0:
        variance = sum((c - mean_count) ** 2 for c in counts) / total_cells
        vmr = variance / mean_count
    else:
        vmr = 0.0

    # Interpretation
    if vmr < 0.7:
        interp = "dispersed"
    elif vmr > 1.5:
        interp = "clustered"
    else:
        interp = "random"

    return QuadratResult(
        counts=counts,
        rows=rows,
        cols=cols,
        chi_squared=round(chi2, 4),
        df=df,
        p_value=round(p_value, 6),
        vmr=round(vmr, 4),
        interpretation=interp,
        expected_count=round(expected, 4),
    )


def mean_center(points):
    """Compute the mean center (centroid) of a point pattern.

    Parameters
    ----------
    points : list of (x, y) tuples

    Returns
    -------
    tuple of (float, float)
        The (x, y) centroid.
    """
    pts = _validate_points(points)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return (round(cx, 6), round(cy, 6))


def standard_distance(points):
    """Compute the standard distance (spatial spread) of a point set.

    This is the spatial equivalent of standard deviation -- the average
    distance of points from the mean center.

    Parameters
    ----------
    points : list of (x, y) tuples

    Returns
    -------
    float
        The standard distance.
    """
    pts = _validate_points(points)
    cx, cy = mean_center(pts)
    n = len(pts)
    sum_sq = sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in pts)
    return round(math.sqrt(sum_sq / n), 6)


def convex_hull_ratio(points, bounds=None):
    """Compute the ratio of convex hull area to bounding box area.

    A ratio near 1.0 means points fill the study region; near 0 means
    they are concentrated in a small subregion.

    Parameters
    ----------
    points : list of (x, y) tuples
    bounds : tuple of (xmin, xmax, ymin, ymax), optional

    Returns
    -------
    float
        Ratio in [0, 1].
    """
    pts = _validate_points(points)
    hull = _convex_hull_area(pts)
    bbox = _bounding_area(pts, bounds)
    if bbox <= 0:
        return 0.0
    return round(min(hull / bbox, 1.0), 6)


# -- Combined analysis ----------------------------------------------

def analyze_pattern(points, bounds=None, quadrat_rows=None,
                    quadrat_cols=None, ripley_radii=None, n_radii=15):
    """Run all pattern analyses and return a combined summary.

    Parameters
    ----------
    points : list of (x, y) tuples
        The point pattern.
    bounds : tuple of (xmin, xmax, ymin, ymax), optional
        Study area bounds.
    quadrat_rows, quadrat_cols : int, optional
        Grid dimensions for quadrat analysis.
    ripley_radii : list of float, optional
        Specific radii for Ripley's K.
    n_radii : int
        Number of radii for Ripley's K (default 15).

    Returns
    -------
    PatternSummary
        Named tuple with all analysis results and an overall
        interpretation.
    """
    pts = _validate_points(points)

    nni_result = clark_evans_nni(pts, bounds)
    quad_result = quadrat_analysis(pts, quadrat_rows, quadrat_cols, bounds)
    rip_result = ripleys_k(pts, ripley_radii, n_radii, bounds)
    center = mean_center(pts)
    std_dist = standard_distance(pts)
    hull_ratio = convex_hull_ratio(pts, bounds)

    # Overall interpretation: majority vote of NNI and quadrat
    votes = {"clustered": 0, "random": 0, "dispersed": 0}
    votes[nni_result.interpretation] += 1
    votes[quad_result.interpretation] += 1
    # Ripley's L contributes based on peak value
    if rip_result.peak_clustering_l > 0.5:
        votes["clustered"] += 1
    elif rip_result.peak_clustering_l < -0.5:
        votes["dispersed"] += 1
    else:
        votes["random"] += 1

    overall = max(votes, key=votes.get)

    return PatternSummary(
        n=len(pts),
        mean_center=center,
        std_distance=std_dist,
        hull_area_ratio=hull_ratio,
        nni=nni_result,
        quadrat=quad_result,
        ripleys=rip_result,
        overall=overall,
    )


# -- Text formatting -------------------------------------------------

def format_pattern_report(summary):
    """Format a PatternSummary as a human-readable text report.

    Parameters
    ----------
    summary : PatternSummary

    Returns
    -------
    str
        Multi-line formatted report.
    """
    lines = []
    lines.append("=" * 60)
    lines.append("POINT PATTERN ANALYSIS")
    lines.append("=" * 60)
    lines.append("")

    lines.append(f"Points:           {summary.n}")
    lines.append(f"Mean center:      ({summary.mean_center[0]:.4f}, "
                 f"{summary.mean_center[1]:.4f})")
    lines.append(f"Std distance:     {summary.std_distance:.4f}")
    lines.append(f"Hull area ratio:  {summary.hull_area_ratio:.4f}")
    lines.append("")

    # NNI
    nni = summary.nni
    lines.append("--- Clark-Evans Nearest Neighbor Index ---")
    lines.append(f"  Observed mean NN dist:  {nni.observed_mean:.6f}")
    lines.append(f"  Expected (CSR):         {nni.expected_mean:.6f}")
    lines.append(f"  NNI (R):                {nni.nni:.4f}")
    lines.append(f"  Z-score:                {nni.z_score:.4f}")
    lines.append(f"  p-value:                {nni.p_value:.6f}")
    lines.append(f"  Interpretation:         {nni.interpretation.upper()}")
    lines.append("")

    # Quadrat
    quad = summary.quadrat
    lines.append(f"--- Quadrat Analysis ({quad.rows}x{quad.cols} grid) ---")
    lines.append(f"  Expected count/cell:    {quad.expected_count:.2f}")
    lines.append(f"  Chi-squared:            {quad.chi_squared:.4f}")
    lines.append(f"  Degrees of freedom:     {quad.df}")
    lines.append(f"  p-value:                {quad.p_value:.6f}")
    lines.append(f"  VMR:                    {quad.vmr:.4f}")
    lines.append(f"  Interpretation:         {quad.interpretation.upper()}")
    lines.append("")

    # Ripley's K summary
    rip = summary.ripleys
    lines.append("--- Ripley's K / Besag's L Function ---")
    lines.append(f"  Density (lambda):       {rip.density:.8f}")
    lines.append(f"  Study area:             {rip.area:.4f}")
    lines.append(f"  Peak clustering at r =  {rip.peak_clustering_r:.4f}")
    lines.append(f"  Peak L(r):              {rip.peak_clustering_l:.4f}")
    if rip.peak_clustering_l > 0:
        lines.append(f"  -> Clustering at scale {rip.peak_clustering_r:.2f}")
    elif rip.peak_clustering_l < 0:
        lines.append(f"  -> Dispersion at scale {rip.peak_clustering_r:.2f}")
    else:
        lines.append(f"  -> Consistent with CSR")
    lines.append("")

    # L(r) table (compact)
    lines.append("  r           K(r)          L(r)          CSR K(r)")
    lines.append("  " + "-" * 52)
    for i in range(len(rip.radii)):
        lines.append(f"  {rip.radii[i]:10.4f}  {rip.k_values[i]:12.4f}  "
                     f"{rip.l_values[i]:12.4f}  {rip.csr_k[i]:12.4f}")
    lines.append("")

    # Overall
    lines.append("--- Overall Assessment ---")
    overall_label = summary.overall.upper()
    if summary.overall == "clustered":
        lines.append(f"  Pattern: {overall_label}")
        lines.append("  Points tend to group together in patches.")
    elif summary.overall == "dispersed":
        lines.append(f"  Pattern: {overall_label}")
        lines.append("  Points tend to repel each other (more evenly spaced).")
    else:
        lines.append(f"  Pattern: {overall_label}")
        lines.append("  Points are consistent with complete spatial randomness (CSR).")
    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)


def generate_pattern_json(summary):
    """Convert a PatternSummary to a JSON-serialisable dictionary.

    Parameters
    ----------
    summary : PatternSummary

    Returns
    -------
    dict
    """
    return {
        "n": summary.n,
        "mean_center": {"x": summary.mean_center[0], "y": summary.mean_center[1]},
        "std_distance": summary.std_distance,
        "hull_area_ratio": summary.hull_area_ratio,
        "overall": summary.overall,
        "nni": {
            "observed_mean": summary.nni.observed_mean,
            "expected_mean": summary.nni.expected_mean,
            "nni": summary.nni.nni,
            "z_score": summary.nni.z_score,
            "p_value": summary.nni.p_value,
            "interpretation": summary.nni.interpretation,
        },
        "quadrat": {
            "rows": summary.quadrat.rows,
            "cols": summary.quadrat.cols,
            "chi_squared": summary.quadrat.chi_squared,
            "df": summary.quadrat.df,
            "p_value": summary.quadrat.p_value,
            "vmr": summary.quadrat.vmr,
            "interpretation": summary.quadrat.interpretation,
            "expected_count": summary.quadrat.expected_count,
            "counts": summary.quadrat.counts,
        },
        "ripleys": {
            "density": summary.ripleys.density,
            "area": summary.ripleys.area,
            "peak_clustering_r": summary.ripleys.peak_clustering_r,
            "peak_clustering_l": summary.ripleys.peak_clustering_l,
            "radii": summary.ripleys.radii,
            "k_values": summary.ripleys.k_values,
            "l_values": summary.ripleys.l_values,
            "csr_k": summary.ripleys.csr_k,
        },
    }
