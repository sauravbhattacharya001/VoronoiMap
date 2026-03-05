"""Nearest-neighbor distance analysis for spatial point patterns.

Computes k-nearest-neighbor distances, the Clark–Evans index for
spatial randomness testing, and the empirical G-function (nearest-
neighbor cumulative distribution).  Useful for answering "are these
points clustered, random, or dispersed?"

Usage (Python API)::

    import vormap
    from vormap_nndist import nn_distances, clark_evans, g_function

    data = vormap.load_data("datauni5.txt")
    points = [(d["x"], d["y"]) for d in data]

    # k-nearest-neighbor distances
    dists = nn_distances(points, k=1)
    for d in dists[:5]:
        print(f"Point ({d['x']:.1f}, {d['y']:.1f}): "
              f"NN dist = {d['distances'][0]:.2f}")

    # Clark-Evans spatial randomness test
    result = clark_evans(points, area=1000 * 2000)
    print(f"R = {result.R:.3f}  (z = {result.z:.2f}, p = {result.p:.4f})")

    # G-function (empirical CDF of NN distances)
    gf = g_function(points, steps=50)
    for step in gf[:5]:
        print(f"d = {step['d']:.1f}  G(d) = {step['G']:.3f}")

CLI::

    voronoimap datauni5.txt 5 --nn-distances
    voronoimap datauni5.txt 5 --nn-distances --nn-k 3
    voronoimap datauni5.txt 5 --clark-evans
    voronoimap datauni5.txt 5 --g-function
    voronoimap datauni5.txt 5 --nn-csv nndist.csv
    voronoimap datauni5.txt 5 --nn-json nndist.json
"""

import json
import bisect
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from vormap import validate_output_path
from vormap_geometry import mean as _mean, std as _std, percentile as _percentile_sorted, normal_cdf as _normal_cdf

try:
    from scipy.spatial import cKDTree as _KDTree
    _HAS_KDTREE = True
except ImportError:
    _HAS_KDTREE = False


# ── Helpers ─────────────────────────────────────────────────────────

# _mean, _std, _normal_cdf are re-exported from vormap_geometry via the
# import aliases above.  _percentile wraps the shared sorted-input version
# to preserve the original unsorted-input interface used by callers.


def _percentile(values: list, p: float) -> float:
    """Percentile (0–100) via linear interpolation.  Accepts unsorted input."""
    return _percentile_sorted(sorted(values), p) if values else 0.0


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two 2D points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _median(values: list) -> float:
    """Median of a list of numbers."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _validate_points(points: list) -> List[Tuple[float, float]]:
    """Validate and normalize a list of (x, y) points.

    Accepts lists/tuples of two numeric values.  Rejects NaN, Inf,
    non-numeric, and wrong-length entries.

    Returns
    -------
    list of (float, float)
        Validated point list.

    Raises
    ------
    ValueError
        If any point is invalid or fewer than 2 points remain.
    """
    validated = []
    for i, pt in enumerate(points):
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError(
                f"Point at index {i} must be a 2-element sequence, got {type(pt).__name__}"
            )
        try:
            x, y = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            raise ValueError(
                f"Point at index {i} has non-numeric coordinates: {pt}"
            )
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
            raise ValueError(
                f"Point at index {i} has NaN/Inf coordinates: ({x}, {y})"
            )
        validated.append((x, y))
    if len(validated) < 2:
        raise ValueError(
            f"At least 2 points required for distance analysis, got {len(validated)}"
        )
    return validated


# ── Core: k-Nearest Neighbor Distances ──────────────────────────────

def _knn_brute(points, k):
    """Brute-force kNN — O(n² log k).  No external dependencies.

    Returns a list of ``(distances, indices)`` tuples, one per point.
    Each distances/indices pair is sorted by ascending distance.
    """
    n = len(points)
    k_actual = min(k, n - 1)
    result = []
    for i in range(n):
        pairs = []
        for j in range(n):
            if i == j:
                continue
            pairs.append((_euclidean(points[i], points[j]), j))
        pairs.sort(key=lambda p: p[0])
        top_k = pairs[:k_actual]
        result.append(([p[0] for p in top_k], [p[1] for p in top_k]))
    return result


def _nn1_brute(points):
    """Brute-force 1-nearest-neighbor distances — O(n²).

    Returns a list of floats: the nearest-neighbor distance for each
    point.  Specialised single-purpose helper used by ``clark_evans()``
    and ``g_function()`` to avoid the overhead of full kNN when only
    the 1-NN distance is needed.
    """
    n = len(points)
    result = []
    for i in range(n):
        min_d = float("inf")
        for j in range(n):
            if i == j:
                continue
            d = _euclidean(points[i], points[j])
            if d < min_d:
                min_d = d
        result.append(min_d)
    return result


def nn_distances(
    points: list,
    k: int = 1,
) -> List[dict]:
    """Compute k-nearest-neighbor distances for each point.

    Parameters
    ----------
    points : list of (x, y)
        2D point coordinates.
    k : int
        Number of nearest neighbors (default 1).

    Returns
    -------
    list of dict
        Each dict has:
        - ``x``, ``y``: point coordinates
        - ``distances``: list of k nearest-neighbor distances (ascending)
        - ``neighbors``: list of k neighbor indices (0-based)
    """
    pts = _validate_points(points)
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    n = len(pts)
    k_actual = min(k, n - 1)

    # Use KDTree for O(n log n) when scipy is available;
    # fall back to O(n²) brute-force otherwise.
    if _HAS_KDTREE and n >= 4:
        import numpy as np
        arr = np.array(pts, dtype=float)
        tree = _KDTree(arr)
        # query k+1 because the first result is the point itself (dist=0)
        dists, indices = tree.query(arr, k=k_actual + 1)
        result = []
        for i in range(n):
            # Skip self (index 0) — distances are sorted ascending
            nn_dists = dists[i, 1:k_actual + 1].tolist()
            nn_idxs = indices[i, 1:k_actual + 1].tolist()
            result.append({
                "x": pts[i][0],
                "y": pts[i][1],
                "distances": nn_dists,
                "neighbors": nn_idxs,
            })
        return result

    # Brute-force fallback for small n or no scipy
    brute = _knn_brute(pts, k_actual)
    result = []
    for i in range(n):
        nn_dists, nn_idxs = brute[i]
        result.append({
            "x": pts[i][0],
            "y": pts[i][1],
            "distances": nn_dists,
            "neighbors": nn_idxs,
        })
    return result


# ── Core: Clark–Evans Index ─────────────────────────────────────────

@dataclass
class ClarkEvansResult:
    """Result of the Clark–Evans nearest-neighbor index test.

    Attributes
    ----------
    R : float
        The Clark–Evans ratio.  R < 1 indicates clustering, R ≈ 1
        indicates spatial randomness, R > 1 indicates dispersion.
    mean_nn : float
        Observed mean nearest-neighbor distance.
    expected_nn : float
        Expected mean NN distance under complete spatial randomness.
    z : float
        Standard normal z-statistic.
    p : float
        Two-tailed p-value (approximation using the Abramowitz & Stegun
        rational formula for the normal CDF).
    n : int
        Number of points.
    density : float
        Point density (n / area).
    interpretation : str
        Human-readable interpretation: "clustered", "random", or
        "dispersed".
    """
    R: float = 0.0
    mean_nn: float = 0.0
    expected_nn: float = 0.0
    z: float = 0.0
    p: float = 1.0
    n: int = 0
    density: float = 0.0
    interpretation: str = "unknown"

    def format_report(self) -> str:
        """Return a human-readable text report."""
        lines = [
            "=" * 56,
            "  Clark–Evans Nearest-Neighbor Index",
            "=" * 56,
            "",
            f"  Points:           {self.n}",
            f"  Density:          {self.density:.6f} pts/unit²",
            f"  Mean NN dist:     {self.mean_nn:.4f}",
            f"  Expected (CSR):   {self.expected_nn:.4f}",
            f"  R index:          {self.R:.4f}",
            f"  z-statistic:      {self.z:.4f}",
            f"  p-value:          {self.p:.6f}",
            f"  Interpretation:   {self.interpretation}",
            "",
        ]
        if self.R < 1:
            lines.append("  → Points are more CLUSTERED than expected by chance.")
        elif self.R > 1:
            lines.append("  → Points are more DISPERSED than expected by chance.")
        else:
            lines.append("  → Point pattern is consistent with RANDOM placement.")
        lines.append("")
        lines.append("=" * 56)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "R": self.R,
            "mean_nn": self.mean_nn,
            "expected_nn": self.expected_nn,
            "z": self.z,
            "p": self.p,
            "n": self.n,
            "density": self.density,
            "interpretation": self.interpretation,
        }


def clark_evans(
    points: list,
    area: Optional[float] = None,
) -> ClarkEvansResult:
    """Compute the Clark–Evans nearest-neighbor index.

    The index R = r̄_obs / r̄_exp where r̄_exp = 1 / (2 √(n/A)).
    R < 1 indicates clustering, R > 1 indicates dispersion.

    Parameters
    ----------
    points : list of (x, y)
        2D point coordinates.
    area : float or None
        Study area in square units.  If None, estimated from the
        bounding box of the points (with 5% padding).

    Returns
    -------
    ClarkEvansResult
        Full statistical result including R, z, and p-value.
    """
    pts = _validate_points(points)
    n = len(pts)

    # Compute 1-NN distances — O(n log n) with KDTree, O(n²) fallback
    if _HAS_KDTREE and n >= 4:
        import numpy as np
        tree = _KDTree(np.array(pts, dtype=float))
        dists, _ = tree.query(np.array(pts), k=2)  # k=2: self + nearest
        nn1 = dists[:, 1].tolist()
    else:
        nn1 = _nn1_brute(pts)

    mean_nn = _mean(nn1)

    # Estimate area from bounding box if not provided
    if area is None:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        # 5% padding on each side
        w = max(w * 1.1, 1e-10)
        h = max(h * 1.1, 1e-10)
        area = w * h

    if area <= 0:
        raise ValueError(f"Study area must be positive, got {area}")

    density = n / area
    expected_nn = 1.0 / (2.0 * math.sqrt(density))
    R = mean_nn / expected_nn if expected_nn > 0 else 0.0

    # Standard error under CSR: SE = 0.26136 / sqrt(n * density)
    se = 0.26136 / math.sqrt(n * density) if n * density > 0 else 1.0
    z = (mean_nn - expected_nn) / se if se > 0 else 0.0

    # Two-tailed p-value
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))

    if p < 0.05:
        interpretation = "clustered" if R < 1 else "dispersed"
    else:
        interpretation = "random"

    return ClarkEvansResult(
        R=R,
        mean_nn=mean_nn,
        expected_nn=expected_nn,
        z=z,
        p=p,
        n=n,
        density=density,
        interpretation=interpretation,
    )


# ── Core: G-function ────────────────────────────────────────────────

@dataclass
class GFunctionResult:
    """Result of the empirical G-function computation.

    Attributes
    ----------
    steps : list of dict
        Each dict has ``d`` (distance) and ``G`` (cumulative proportion
        of points whose nearest neighbor is within distance d).
    theoretical : list of dict
        CSR theoretical G-function values at the same distances:
        G_csr(d) = 1 − exp(−λ π d²).
    nn_distances : list of float
        Sorted 1-NN distances.
    summary : dict
        Distance statistics: mean, median, std, min, max.
    """
    steps: list = field(default_factory=list)
    theoretical: list = field(default_factory=list)
    nn_distances: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def format_report(self) -> str:
        """Return a human-readable text report."""
        lines = [
            "=" * 56,
            "  G-function (Nearest-Neighbor CDF)",
            "=" * 56,
            "",
            f"  Points:      {len(self.nn_distances)}",
            f"  Mean NN:     {self.summary.get('mean', 0):.4f}",
            f"  Median NN:   {self.summary.get('median', 0):.4f}",
            f"  Std NN:      {self.summary.get('std', 0):.4f}",
            f"  Min NN:      {self.summary.get('min', 0):.4f}",
            f"  Max NN:      {self.summary.get('max', 0):.4f}",
            "",
            f"  {'Distance':>10}  {'G(d)':>8}  {'G_csr(d)':>10}",
            f"  {'─' * 10}  {'─' * 8}  {'─' * 10}",
        ]
        for emp, theo in zip(self.steps, self.theoretical):
            lines.append(
                f"  {emp['d']:10.4f}  {emp['G']:8.4f}  {theo['G']:10.4f}"
            )
        lines.append("")
        lines.append("=" * 56)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "steps": self.steps,
            "theoretical": self.theoretical,
            "nn_distances": self.nn_distances,
            "summary": self.summary,
        }


def g_function(
    points: list,
    steps: int = 50,
    area: Optional[float] = None,
) -> GFunctionResult:
    """Compute the empirical G-function (NN cumulative distribution).

    G(d) = proportion of points whose nearest neighbor is ≤ d.
    Under complete spatial randomness (CSR): G_csr(d) = 1 − exp(−λπd²).

    If the empirical G rises faster than theoretical, the pattern is
    clustered.  If it rises slower, points are dispersed.

    Parameters
    ----------
    points : list of (x, y)
        2D point coordinates.
    steps : int
        Number of distance bins (default 50).
    area : float or None
        Study area for the theoretical CSR curve.  Estimated from
        bounding box if not provided.

    Returns
    -------
    GFunctionResult
        Empirical and theoretical G-function values plus NN distance
        summary statistics.
    """
    pts = _validate_points(points)
    n = len(pts)
    if steps < 1:
        raise ValueError(f"steps must be >= 1, got {steps}")

    # Compute 1-NN distances — O(n log n) with KDTree, O(n²) fallback
    if _HAS_KDTREE and n >= 4:
        import numpy as np
        tree = _KDTree(np.array(pts, dtype=float))
        dists, _ = tree.query(np.array(pts), k=2)  # k=2: self + nearest
        nn1 = dists[:, 1].tolist()
    else:
        nn1 = _nn1_brute(pts)

    nn1_sorted = sorted(nn1)

    # Summary statistics
    summary = {
        "mean": _mean(nn1),
        "median": _median(nn1),
        "std": _std(nn1),
        "min": nn1_sorted[0] if nn1_sorted else 0.0,
        "max": nn1_sorted[-1] if nn1_sorted else 0.0,
    }

    # Estimate area if not provided
    if area is None:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        w = max(max(xs) - min(xs), 1e-10) * 1.1
        h = max(max(ys) - min(ys), 1e-10) * 1.1
        area = w * h

    density = n / area if area > 0 else 0.0

    # Build empirical G(d) at evenly spaced distances
    d_max = nn1_sorted[-1] * 1.2 if nn1_sorted else 1.0
    d_values = [d_max * i / steps for i in range(steps + 1)]

    empirical = []
    theoretical = []
    for d in d_values:
        # Empirical: proportion of NN distances <= d (binary search on sorted list)
        count = bisect.bisect_right(nn1_sorted, d)
        g_emp = count / n
        empirical.append({"d": d, "G": g_emp})

        # Theoretical CSR: G(d) = 1 - exp(-lambda * pi * d^2)
        g_csr = 1.0 - math.exp(-density * math.pi * d * d)
        theoretical.append({"d": d, "G": g_csr})

    return GFunctionResult(
        steps=empirical,
        theoretical=theoretical,
        nn_distances=nn1_sorted,
        summary=summary,
    )


# ── Distance Distribution Summary ──────────────────────────────────

@dataclass
class DistanceSummary:
    """Summary statistics for pairwise or NN distances.

    Attributes
    ----------
    point_count : int
        Number of input points.
    k : int
        Number of nearest neighbors computed.
    distances : list of dict
        Per-point NN distance records.
    stats : dict
        Aggregate statistics: mean, median, std, min, max, q1, q3.
    histogram : list of dict
        Binned distance distribution with ``bin_start``, ``bin_end``,
        ``count``, and ``proportion``.
    """
    point_count: int = 0
    k: int = 1
    distances: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    histogram: list = field(default_factory=list)

    def format_report(self) -> str:
        """Return a human-readable text report."""
        lines = [
            "=" * 56,
            "  Nearest-Neighbor Distance Summary",
            "=" * 56,
            "",
            f"  Points:      {self.point_count}",
            f"  k:           {self.k}",
            f"  Mean:        {self.stats.get('mean', 0):.4f}",
            f"  Median:      {self.stats.get('median', 0):.4f}",
            f"  Std:         {self.stats.get('std', 0):.4f}",
            f"  Min:         {self.stats.get('min', 0):.4f}",
            f"  Max:         {self.stats.get('max', 0):.4f}",
            f"  Q1:          {self.stats.get('q1', 0):.4f}",
            f"  Q3:          {self.stats.get('q3', 0):.4f}",
            "",
            f"  {'Bin':>16}  {'Count':>6}  {'Proportion':>10}",
            f"  {'─' * 16}  {'─' * 6}  {'─' * 10}",
        ]
        for h in self.histogram:
            bin_label = f"[{h['bin_start']:.2f}, {h['bin_end']:.2f})"
            lines.append(
                f"  {bin_label:>16}  {h['count']:6d}  {h['proportion']:10.4f}"
            )
        lines.append("")
        lines.append("=" * 56)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "point_count": self.point_count,
            "k": self.k,
            "stats": self.stats,
            "histogram": self.histogram,
            "distances": self.distances,
        }


def distance_summary(
    points: list,
    k: int = 1,
    bins: int = 10,
) -> DistanceSummary:
    """Compute NN distance statistics and a histogram.

    Parameters
    ----------
    points : list of (x, y)
        2D point coordinates.
    k : int
        Number of nearest neighbors (default 1).
    bins : int
        Number of histogram bins (default 10).

    Returns
    -------
    DistanceSummary
        Full distance statistics with histogram.
    """
    nn_data = nn_distances(points, k=k)
    all_dists = []
    for entry in nn_data:
        all_dists.extend(entry["distances"])

    if not all_dists:
        return DistanceSummary(point_count=len(points), k=k)

    stats = {
        "mean": _mean(all_dists),
        "median": _median(all_dists),
        "std": _std(all_dists),
        "min": min(all_dists),
        "max": max(all_dists),
        "q1": _percentile(all_dists, 25),
        "q3": _percentile(all_dists, 75),
    }

    # Build histogram
    lo = min(all_dists)
    hi = max(all_dists)
    bin_width = (hi - lo) / bins if hi > lo else 1.0
    if bin_width == 0:
        bin_width = 1.0

    histogram = []
    total = len(all_dists)
    for i in range(bins):
        b_start = lo + i * bin_width
        b_end = lo + (i + 1) * bin_width
        if i == bins - 1:
            count = sum(1 for d in all_dists if b_start <= d <= b_end)
        else:
            count = sum(1 for d in all_dists if b_start <= d < b_end)
        histogram.append({
            "bin_start": b_start,
            "bin_end": b_end,
            "count": count,
            "proportion": count / total if total > 0 else 0.0,
        })

    return DistanceSummary(
        point_count=len(points),
        k=k,
        distances=nn_data,
        stats=stats,
        histogram=histogram,
    )


# ── Export ───────────────────────────────────────────────────────────

def export_nn_csv(summary: DistanceSummary, path: str) -> str:
    """Export NN distance data to CSV.

    Parameters
    ----------
    summary : DistanceSummary
        Result from :func:`distance_summary`.
    path : str
        Output file path.

    Returns
    -------
    str
        Absolute path to the written file.
    """
    import os
    path = validate_output_path(path, allow_absolute=True)
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    lines = ["x,y,k,nn_distance,neighbor_index"]
    for entry in summary.distances:
        for di, (dist, nbr) in enumerate(
            zip(entry["distances"], entry["neighbors"])
        ):
            lines.append(
                f"{entry['x']},{entry['y']},{di + 1},{dist:.6f},{nbr}"
            )
    with open(abs_path, "w", newline="") as f:
        f.write("\n".join(lines) + "\n")
    return abs_path


def export_nn_json(result: object, path: str) -> str:
    """Export any result object to JSON (uses to_dict()).

    Parameters
    ----------
    result : object
        Any result dataclass with a ``to_dict()`` method
        (DistanceSummary, ClarkEvansResult, or GFunctionResult).
    path : str
        Output file path.

    Returns
    -------
    str
        Absolute path to the written file.
    """
    import os
    path = validate_output_path(path, allow_absolute=True)
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    with open(abs_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return abs_path
