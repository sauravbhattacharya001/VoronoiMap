"""Fractal dimension analysis for Voronoi seed point patterns.

Quantifies the spatial complexity and self-similarity of point
distributions using fractal geometry.  Complements regularity and
pattern analysis with scale-independent complexity metrics.

Analyses provided
-----------------
- **Box-counting dimension (D₀):**
  Partition the bounding box into grids of decreasing cell size ε
  and count occupied cells N(ε).  D₀ = -lim(log N / log ε).
  D₀ ≈ 0 for a single point, ≈ 1 for points on a line, ≈ 2 for
  points filling the plane.

- **Lacunarity (Λ):**
  Measures "gappiness" — how heterogeneously space is filled.
  High Λ → large voids between clusters.  Low Λ → uniform coverage.
  Computed via the gliding-box method: Λ(ε) = σ²(n)/μ²(n) + 1
  where n is the count per box.

- **Multifractal spectrum (Rényi dimensions D_q):**
  Generalised dimensions D_q for integer q from -5 to +5.
  D₀ = capacity dimension, D₁ = information dimension,
  D₂ = correlation dimension.  A flat D_q spectrum implies a
  monofractal; varying D_q implies multifractal structure.

- **Correlation dimension (D₂):**
  From the Grassberger-Procaccia algorithm.  Counts point pairs
  within distance r and fits log C(r) vs log r.

- **Boundary fractal dimension:**
  Box-counting dimension of Voronoi cell edges.  Smooth boundaries
  → D ≈ 1; irregular/jagged → D > 1.

Typical usage::

    import vormap, vormap_viz
    from vormap_fractal import fractal_analysis, format_report

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = fractal_analysis(data, stats, regions)
    print(format_report(result))
    export_json(result, "fractal.json")

CLI::

    voronoimap datauni5.txt 5 --fractal
    voronoimap datauni5.txt 5 --fractal-json fractal.json
"""


import json
import math


# ── Box-Counting Dimension ───────────────────────────────────

from vormap_utils import bounding_box as _bounding_box


def box_count(points, num_scales=15):
    """Compute box-counting data for a set of 2D points.

    Parameters
    ----------
    points : list of (x, y)
        Coordinates to analyse.
    num_scales : int
        Number of grid resolutions to evaluate (default 15).

    Returns
    -------
    dict with keys:
        dimension : float   — estimated box-counting dimension D₀
        scales    : list    — log(1/ε) values
        counts    : list    — log(N(ε)) values
        r_squared : float   — R² of the linear fit
        raw       : list    — (epsilon, N) pairs
    """
    if not points or len(points) < 2:
        raise ValueError("Need at least 2 points for box-counting")

    x_min, y_min, x_max, y_max = _bounding_box(points)
    dx = x_max - x_min
    dy = y_max - y_min
    if dx == 0 and dy == 0:
        raise ValueError("All points are identical — cannot compute fractal dimension")
    side = max(dx, dy)
    if side == 0:
        side = max(dx, dy, 1.0)

    # Generate scales from side/2 down to side/2^num_scales
    epsilons = []
    for i in range(1, num_scales + 1):
        eps = side / (2 ** i)
        if eps > 0:
            epsilons.append(eps)

    n_points = len(points)
    raw = []
    log_inv_eps = []
    log_n = []

    for eps in epsilons:
        occupied = set()
        for x, y in points:
            col = int((x - x_min) / eps) if eps > 0 else 0
            row = int((y - y_min) / eps) if eps > 0 else 0
            occupied.add((row, col))
        n = len(occupied)
        if n > 0:
            raw.append((eps, n))

    # Trim the saturation regime: when N(ε) ≈ n_points, each point
    # is in its own box and the scaling relationship breaks down.
    # Keep only scales where N < 90% of n_points.
    for eps, n in raw:
        if n < 0.9 * n_points:
            log_inv_eps.append(math.log(1.0 / eps))
            log_n.append(math.log(n))

    # If trimming removed everything, use all raw data
    if len(log_inv_eps) < 2:
        log_inv_eps = []
        log_n = []
        for eps, n in raw:
            log_inv_eps.append(math.log(1.0 / eps))
            log_n.append(math.log(n))

    if len(log_inv_eps) < 2:
        return {
            "dimension": 0.0,
            "scales": [],
            "counts": [],
            "r_squared": 0.0,
            "raw": raw,
        }

    # Linear regression: log(N) = D * log(1/eps) + c
    slope, intercept, r_sq = _linear_regression(log_inv_eps, log_n)

    return {
        "dimension": round(slope, 4),
        "scales": [round(v, 6) for v in log_inv_eps],
        "counts": [round(v, 6) for v in log_n],
        "r_squared": round(r_sq, 6),
        "raw": raw,
    }


# ── Lacunarity ───────────────────────────────────────────────

def lacunarity(points, num_scales=12):
    """Compute lacunarity Λ(ε) via the gliding-box method.

    Lacunarity measures the "gappiness" or heterogeneity of the
    spatial distribution.  Higher values indicate more clustering
    and void structure.

    Parameters
    ----------
    points : list of (x, y)
    num_scales : int

    Returns
    -------
    dict with keys:
        mean_lacunarity : float — average Λ across scales
        by_scale        : list  — [{epsilon, lacunarity, mean_count, var_count}]
        classification  : str   — 'uniform' | 'moderate' | 'heterogeneous' | 'highly_clustered'
    """
    if not points or len(points) < 2:
        raise ValueError("Need at least 2 points for lacunarity")

    x_min, y_min, x_max, y_max = _bounding_box(points)
    side = max(x_max - x_min, y_max - y_min)
    if side == 0:
        return {
            "mean_lacunarity": 1.0,
            "by_scale": [],
            "classification": "uniform",
        }

    results = []
    for i in range(1, num_scales + 1):
        eps = side / (2 ** i)
        if eps <= 0:
            continue

        # Count points per box
        box_counts = {}
        n_cols = max(1, int(math.ceil(side / eps)))
        n_rows = n_cols
        total_boxes = n_rows * n_cols

        # Skip scales where boxes >> points (lacunarity becomes
        # dominated by empty-box artefacts at very fine scales).
        if total_boxes > len(points) * 10:
            continue

        for x, y in points:
            col = min(int((x - x_min) / eps), n_cols - 1)
            row = min(int((y - y_min) / eps), n_rows - 1)
            key = (row, col)
            box_counts[key] = box_counts.get(key, 0) + 1

        # Include empty boxes
        counts = list(box_counts.values())
        n_empty = total_boxes - len(counts)
        counts.extend([0] * n_empty)

        if len(counts) == 0:
            continue

        mean_n = sum(counts) / len(counts)
        if mean_n == 0:
            results.append({
                "epsilon": round(eps, 6),
                "lacunarity": 1.0,
                "mean_count": 0.0,
                "var_count": 0.0,
            })
            continue

        var_n = sum((c - mean_n) ** 2 for c in counts) / len(counts)
        lam = var_n / (mean_n * mean_n) + 1.0

        results.append({
            "epsilon": round(eps, 6),
            "lacunarity": round(lam, 6),
            "mean_count": round(mean_n, 6),
            "var_count": round(var_n, 6),
        })

    if not results:
        mean_lac = 1.0
    else:
        mean_lac = sum(r["lacunarity"] for r in results) / len(results)

    # Classify
    if mean_lac < 1.2:
        cls = "uniform"
    elif mean_lac < 2.0:
        cls = "moderate"
    elif mean_lac < 5.0:
        cls = "heterogeneous"
    else:
        cls = "highly_clustered"

    return {
        "mean_lacunarity": round(mean_lac, 4),
        "by_scale": results,
        "classification": cls,
    }


# ── Correlation Dimension ────────────────────────────────────

def correlation_dimension(points, num_radii=20):
    """Grassberger-Procaccia correlation dimension D₂.

    Counts the fraction of point pairs within distance r for a
    range of radii, then fits log C(r) vs log r.

    Parameters
    ----------
    points : list of (x, y)
    num_radii : int

    Returns
    -------
    dict with keys:
        dimension : float
        r_squared : float
        radii     : list of float
        log_r     : list of float
        log_c     : list of float
    """
    n = len(points)
    if n < 3:
        raise ValueError("Need at least 3 points for correlation dimension")

    # Compute all pairwise distances
    dists = []
    for i in range(n):
        for j in range(i + 1, n):
            dx = points[i][0] - points[j][0]
            dy = points[i][1] - points[j][1]
            dists.append(math.sqrt(dx * dx + dy * dy))

    if not dists:
        return {"dimension": 0.0, "r_squared": 0.0, "radii": [], "log_r": [], "log_c": []}

    dists.sort()
    d_max = dists[-1]
    d_min = dists[0] if dists[0] > 0 else d_max / 1000.0

    if d_max <= d_min:
        return {"dimension": 0.0, "r_squared": 0.0, "radii": [], "log_r": [], "log_c": []}

    n_pairs = n * (n - 1) / 2.0

    # Log-spaced radii
    log_min = math.log(d_min)
    log_max = math.log(d_max)
    radii = []
    for i in range(num_radii):
        r = math.exp(log_min + (log_max - log_min) * i / (num_radii - 1))
        radii.append(r)

    log_r = []
    log_c = []

    # Use sorted distances for efficient counting
    for r in radii:
        # Binary search for count of distances <= r
        lo, hi = 0, len(dists)
        while lo < hi:
            mid = (lo + hi) // 2
            if dists[mid] <= r:
                lo = mid + 1
            else:
                hi = mid
        count = lo
        if count > 0:
            c_r = count / n_pairs
            log_r.append(math.log(r))
            log_c.append(math.log(c_r))

    if len(log_r) < 2:
        return {"dimension": 0.0, "r_squared": 0.0, "radii": radii, "log_r": [], "log_c": []}

    slope, _, r_sq = _linear_regression(log_r, log_c)

    return {
        "dimension": round(slope, 4),
        "r_squared": round(r_sq, 6),
        "radii": [round(r, 6) for r in radii],
        "log_r": [round(v, 6) for v in log_r],
        "log_c": [round(v, 6) for v in log_c],
    }


# ── Multifractal Spectrum ────────────────────────────────────

def multifractal_spectrum(points, q_range=None, num_scales=12):
    """Compute generalised Rényi dimensions D_q.

    D₀ = capacity (box-counting), D₁ = information, D₂ = correlation.
    A flat spectrum → monofractal; varying spectrum → multifractal.

    Parameters
    ----------
    points : list of (x, y)
    q_range : list of int, optional
        Moment orders (default -5..+5).
    num_scales : int

    Returns
    -------
    dict with keys:
        dimensions  : dict mapping q → D_q
        is_multifractal : bool
        spectrum_width  : float  — D_{-5} - D_{+5}
        d0, d1, d2      : float — key dimensions
    """
    if not points or len(points) < 2:
        raise ValueError("Need at least 2 points for multifractal analysis")

    if q_range is None:
        q_range = list(range(-5, 6))

    x_min, y_min, x_max, y_max = _bounding_box(points)
    side = max(x_max - x_min, y_max - y_min)
    if side == 0:
        return {
            "dimensions": {q: 0.0 for q in q_range},
            "is_multifractal": False,
            "spectrum_width": 0.0,
            "d0": 0.0, "d1": 0.0, "d2": 0.0,
        }

    n_total = len(points)

    # Build scales — skip scales where boxes ≈ n_points (saturation)
    epsilons = []
    for i in range(1, num_scales + 1):
        eps = side / (2 ** i)
        if eps > 0:
            n_boxes = int(math.ceil(side / eps)) ** 2
            if n_boxes <= n_total * 10:
                epsilons.append(eps)

    dimensions = {}

    for q in q_range:
        log_inv_eps = []
        log_partition = []

        for eps in epsilons:
            # Count points per box
            box_counts = {}
            for x, y in points:
                col = int((x - x_min) / eps)
                row = int((y - y_min) / eps)
                key = (row, col)
                box_counts[key] = box_counts.get(key, 0) + 1

            probs = [c / n_total for c in box_counts.values()]

            if q == 1:
                # D₁ = information dimension (limit as q→1)
                # H(ε) = -Σ pᵢ log(pᵢ)
                h = -sum(p * math.log(p) for p in probs if p > 0)
                log_inv_eps.append(math.log(1.0 / eps))
                log_partition.append(h)
            else:
                # D_q = 1/(q-1) * lim log(Σ pᵢ^q) / log(ε)
                partition = sum(p ** q for p in probs if p > 0)
                if partition > 0:
                    log_inv_eps.append(math.log(1.0 / eps))
                    log_partition.append(math.log(partition))

        if len(log_inv_eps) < 2:
            dimensions[q] = 0.0
            continue

        if q == 1:
            # D₁ = slope of H(ε) vs log(1/ε)
            slope, _, _ = _linear_regression(log_inv_eps, log_partition)
            dimensions[q] = round(slope, 4)
        elif q == 0:
            # D₀ = slope of log(N) vs log(1/ε) directly (box-counting)
            slope, _, _ = _linear_regression(log_inv_eps, log_partition)
            dimensions[q] = round(slope, 4)
        else:
            slope, _, _ = _linear_regression(log_inv_eps, log_partition)
            d_q = slope / (q - 1)
            dimensions[q] = round(d_q, 4)

    d0 = dimensions.get(0, 0.0)
    d1 = dimensions.get(1, 0.0)
    d2 = dimensions.get(2, 0.0)

    q_min = min(q_range)
    q_max = max(q_range)
    width = abs(dimensions.get(q_min, 0.0) - dimensions.get(q_max, 0.0))
    is_multi = width > 0.1  # threshold for multifractality

    return {
        "dimensions": dimensions,
        "is_multifractal": is_multi,
        "spectrum_width": round(width, 4),
        "d0": d0,
        "d1": d1,
        "d2": d2,
    }


# ── Boundary Fractal Dimension ───────────────────────────────

def boundary_dimension(regions, num_scales=12):
    """Box-counting dimension of Voronoi cell boundaries.

    Rasterises all cell edges and applies box-counting.
    Smooth cell edges → D ≈ 1; irregular → D > 1.

    Parameters
    ----------
    regions : list of dict
        Each region dict should have 'vertices' key (list of (x, y)).
    num_scales : int

    Returns
    -------
    dict with dimension, r_squared, and per-scale data.
    """
    # Extract all boundary segments
    boundary_points = []
    for region in regions:
        verts = region.get("vertices", [])
        if not verts:
            continue
        for i in range(len(verts)):
            x0, y0 = verts[i][0], verts[i][1]
            x1, y1 = verts[(i + 1) % len(verts)][0], verts[(i + 1) % len(verts)][1]
            # Sample points densely along edge
            seg_len = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2)
            n_samples = max(10, int(seg_len * 100))
            for j in range(n_samples):
                t = j / n_samples
                boundary_points.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))

    if len(boundary_points) < 2:
        return {"dimension": 1.0, "r_squared": 0.0, "raw": []}

    return box_count(boundary_points, num_scales=num_scales)


# ── Combined Analysis ────────────────────────────────────────

def fractal_analysis(data, stats=None, regions=None, options=None):
    """Run all fractal analyses on a point dataset.

    Parameters
    ----------
    data : dict
        Parsed point data (from vormap.load_data).  Must have 'points'
        key with list of dicts having 'lat' and 'long' keys.
    stats : dict, optional
        Region statistics (from vormap_viz.compute_region_stats).
    regions : list, optional
        Region data (from vormap_viz.compute_regions).
    options : dict, optional
        - num_scales (int): grid resolutions (default 12)
        - num_radii (int): correlation dimension radii (default 20)
        - q_range (list of int): multifractal moment orders

    Returns
    -------
    dict — complete fractal analysis results
    """
    opts = options or {}
    num_scales = opts.get("num_scales", 12)
    num_radii = opts.get("num_radii", 20)
    q_range = opts.get("q_range", list(range(-5, 6)))

    # Extract points
    points = _extract_points(data)

    if len(points) < 3:
        raise ValueError("Need at least 3 points for fractal analysis")

    # Run analyses
    bc = box_count(points, num_scales=num_scales)
    lac = lacunarity(points, num_scales=num_scales)
    corr = correlation_dimension(points, num_radii=num_radii)
    mf = multifractal_spectrum(points, q_range=q_range, num_scales=num_scales)

    result = {
        "num_points": len(points),
        "box_counting": bc,
        "lacunarity": lac,
        "correlation": corr,
        "multifractal": mf,
        "summary": {
            "box_counting_dimension": bc["dimension"],
            "correlation_dimension": corr["dimension"],
            "information_dimension": mf["d1"],
            "mean_lacunarity": lac["mean_lacunarity"],
            "lacunarity_class": lac["classification"],
            "is_multifractal": mf["is_multifractal"],
            "spectrum_width": mf["spectrum_width"],
        },
    }

    # Boundary analysis if regions provided
    if regions:
        bd = boundary_dimension(regions, num_scales=num_scales)
        result["boundary"] = bd
        result["summary"]["boundary_dimension"] = bd["dimension"]

    # Interpretation
    result["interpretation"] = _interpret(result["summary"])

    return result


# ── Reporting ────────────────────────────────────────────────

def format_report(result):
    """Format fractal analysis results as a human-readable text report.

    Parameters
    ----------
    result : dict
        Output of fractal_analysis().

    Returns
    -------
    str — formatted report
    """
    s = result.get("summary", {})
    lines = [
        "╔══════════════════════════════════════════╗",
        "║     Fractal Dimension Analysis Report    ║",
        "╚══════════════════════════════════════════╝",
        "",
        f"  Points analysed: {result.get('num_points', '?')}",
        "",
        "  ── Dimensions ──────────────────────────",
        f"  Box-counting (D₀):    {s.get('box_counting_dimension', '?'):.4f}"
        f"   (R² = {result.get('box_counting', {}).get('r_squared', 0):.4f})",
        f"  Information  (D₁):    {s.get('information_dimension', '?'):.4f}",
        f"  Correlation  (D₂):    {s.get('correlation_dimension', '?'):.4f}"
        f"   (R² = {result.get('correlation', {}).get('r_squared', 0):.4f})",
    ]

    if "boundary_dimension" in s:
        lines.append(
            f"  Boundary     (D_b):   {s['boundary_dimension']:.4f}"
            f"   (R² = {result.get('boundary', {}).get('r_squared', 0):.4f})"
        )

    lines.extend([
        "",
        "  ── Lacunarity ──────────────────────────",
        f"  Mean Λ:               {s.get('mean_lacunarity', '?'):.4f}",
        f"  Classification:       {s.get('lacunarity_class', '?')}",
        "",
        "  ── Multifractal ────────────────────────",
        f"  Spectrum width:       {s.get('spectrum_width', 0):.4f}",
        f"  Multifractal:         {'Yes' if s.get('is_multifractal') else 'No'}",
    ])

    # D_q table
    mf = result.get("multifractal", {})
    dims = mf.get("dimensions", {})
    if dims:
        lines.append("")
        lines.append("  q    D_q")
        lines.append("  ──   ────────")
        for q in sorted(dims.keys()):
            marker = " ←" if q in (0, 1, 2) else ""
            lines.append(f"  {q:+d}   {dims[q]:.4f}{marker}")

    # Interpretation
    interp = result.get("interpretation", [])
    if interp:
        lines.extend(["", "  ── Interpretation ──────────────────────"])
        for item in interp:
            lines.append(f"  • {item}")

    lines.append("")
    return "\n".join(lines)


def export_json(result, path):
    """Export fractal analysis results to JSON.

    Parameters
    ----------
    result : dict
        Output of fractal_analysis().
    path : str
        Output file path.
    """
    # Strip raw data that's too verbose for export
    export = {}
    for key, value in result.items():
        if key in ("box_counting", "correlation", "boundary"):
            # Keep everything except large raw arrays
            filtered = {k: v for k, v in value.items() if k != "raw"}
            export[key] = filtered
        else:
            export[key] = value

    with open(path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, default=str)
    return path


# ── Internal Helpers ─────────────────────────────────────────

def _extract_points(data):
    """Extract (x, y) coordinates from vormap data dict."""
    if isinstance(data, dict):
        pts = data.get("points", [])
        return [(p.get("long", p.get("x", 0)),
                 p.get("lat", p.get("y", 0))) for p in pts]
    if isinstance(data, (list, tuple)):
        return [(p[0], p[1]) for p in data]
    raise ValueError("data must be a dict with 'points' key or a list of (x,y)")


def _linear_regression(xs, ys):
    """Simple OLS linear regression. Returns (slope, intercept, r_squared)."""
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0

    sx = sum(xs)
    sy = sum(ys)
    sx2 = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sy2 = sum(y * y for y in ys)

    denom = n * sx2 - sx * sx
    if abs(denom) < 1e-15:
        return 0.0, 0.0, 0.0

    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n

    # R²
    ss_tot = sy2 - (sy * sy) / n
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_sq = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return slope, intercept, max(0.0, r_sq)


def _interpret(summary):
    """Generate human-readable interpretation bullets."""
    interp = []
    d0 = summary.get("box_counting_dimension", 0)

    if d0 < 0.5:
        interp.append("Very low box-counting dimension — points are highly concentrated")
    elif d0 < 1.2:
        interp.append("Points approximate a 1D structure (line or curve)")
    elif d0 < 1.8:
        interp.append(f"Box-counting D₀ = {d0:.2f} — moderate space-filling, partial plane coverage")
    else:
        interp.append(f"Box-counting D₀ = {d0:.2f} — near-complete plane filling")

    lac_class = summary.get("lacunarity_class", "")
    lac_val = summary.get("mean_lacunarity", 1.0)
    if lac_class == "uniform":
        interp.append(f"Low lacunarity (Λ = {lac_val:.2f}) — points fill space uniformly")
    elif lac_class == "moderate":
        interp.append(f"Moderate lacunarity (Λ = {lac_val:.2f}) — some spatial heterogeneity")
    elif lac_class == "heterogeneous":
        interp.append(f"High lacunarity (Λ = {lac_val:.2f}) — significant voids and clusters")
    elif lac_class == "highly_clustered":
        interp.append(f"Very high lacunarity (Λ = {lac_val:.2f}) — extreme clustering with large empty regions")

    if summary.get("is_multifractal"):
        w = summary.get("spectrum_width", 0)
        interp.append(f"Multifractal structure detected (width = {w:.2f}) — "
                       "different regions have different scaling behaviour")
    else:
        interp.append("Monofractal — uniform scaling across all regions")

    bd = summary.get("boundary_dimension")
    if bd is not None:
        if bd < 1.05:
            interp.append(f"Very smooth cell boundaries (D_b = {bd:.2f})")
        elif bd < 1.2:
            interp.append(f"Slightly irregular cell boundaries (D_b = {bd:.2f})")
        else:
            interp.append(f"Complex, jagged cell boundaries (D_b = {bd:.2f})")

    return interp


# ── CLI ──────────────────────────────────────────────────────

def _cli():
    """Command-line interface for fractal analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="vormap_fractal",
        description="Fractal dimension analysis for Voronoi point patterns",
    )
    parser.add_argument("input", help="Input data file (CSV/JSON with lat,long columns)")
    parser.add_argument("-o", "--output", help="JSON output path")
    parser.add_argument("-s", "--scales", type=int, default=12,
                        help="Number of grid scales (default 12)")
    parser.add_argument("-r", "--radii", type=int, default=20,
                        help="Number of correlation radii (default 20)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress text report")
    args = parser.parse_args()

    # Load points from CSV or JSON
    points = _load_points_file(args.input)
    data = {"points": [{"long": x, "lat": y} for x, y in points]}

    result = fractal_analysis(data, options={
        "num_scales": args.scales,
        "num_radii": args.radii,
    })

    if not args.quiet:
        print(format_report(result))

    if args.output:
        export_json(result, args.output)
        print(f"\nExported to {args.output}")


def _load_points_file(path):
    """Load points from a CSV or JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    # Try JSON
    if content.startswith(("{", "[")):
        obj = json.loads(content)
        if isinstance(obj, list):
            return [(p.get("long", p.get("x", 0)),
                     p.get("lat", p.get("y", 0))) for p in obj]
        if isinstance(obj, dict) and "points" in obj:
            return [(p.get("long", p.get("x", 0)),
                     p.get("lat", p.get("y", 0))) for p in obj["points"]]

    # CSV: expect header row with lat/long or x/y
    lines = content.split("\n")
    header = lines[0].lower().replace('"', '').split(",")
    x_idx = None
    y_idx = None
    for i, h in enumerate(header):
        h = h.strip()
        if h in ("long", "longitude", "x", "lng"):
            x_idx = i
        elif h in ("lat", "latitude", "y"):
            y_idx = i

    if x_idx is None or y_idx is None:
        # No header — assume x,y columns
        x_idx, y_idx = 0, 1

    points = []
    start = 1 if any(h.strip() in ("long", "longitude", "x", "lng", "lat", "latitude", "y")
                     for h in header) else 0
    for line in lines[start:]:
        parts = line.strip().split(",")
        if len(parts) > max(x_idx, y_idx):
            try:
                points.append((float(parts[x_idx]), float(parts[y_idx])))
            except ValueError:
                continue
    return points


if __name__ == "__main__":
    _cli()
