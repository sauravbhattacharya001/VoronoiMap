"""Voronoi symmetry analysis — detect rotational and reflective symmetry.

Analyses point patterns for approximate geometric symmetry about their
centroid.  Useful for crystal lattice detection, pattern classification,
and quality assessment of regular tessellations.

Measures:
    - **Rotational symmetry** — tests n-fold rotational symmetry (2–12)
      by comparing the point set against rotated copies.  Reports the
      best matching order and a 0–1 score.
    - **Reflective symmetry** — scans candidate mirror axes and scores
      how well the point set maps onto its reflection.  Reports the top
      axes and a 0–1 score.
    - **Radial distribution** — distance histogram from centroid, useful
      for shell-structure detection.
    - **Composite symmetry index** — weighted combination (0–100) of
      rotational and reflective scores.

Typical usage::

    import vormap
    from vormap_symmetry import symmetry_analysis, format_report

    data = vormap.load_data("crystal.txt")
    result = symmetry_analysis(data)
    print(format_report(result))

CLI::

    python vormap_symmetry.py crystal.txt
    python vormap_symmetry.py crystal.txt --json symmetry.json
    python vormap_symmetry.py crystal.txt --max-fold 8 --axes 180
"""

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import vormap

# ── Result containers ───────────────────────────────────────────────


@dataclass
class RotationalResult:
    """Result for a single n-fold rotational symmetry test."""
    fold: int
    score: float  # 0–1, 1 = perfect
    mean_displacement: float  # avg min-distance after rotation


@dataclass
class ReflectionAxis:
    """A candidate mirror axis."""
    angle_deg: float
    score: float  # 0–1
    mean_displacement: float


@dataclass
class RadialShell:
    """A detected radial shell (ring of points at similar distance)."""
    radius: float
    count: int
    spread: float  # std dev of distances in this shell


@dataclass
class SymmetryResult:
    """Complete symmetry analysis output."""
    centroid: Tuple[float, float]
    num_points: int
    # Rotational
    best_fold: int
    best_fold_score: float
    rotational_scores: List[RotationalResult]
    # Reflective
    best_axis: Optional[ReflectionAxis]
    reflective_score: float
    top_axes: List[ReflectionAxis]
    # Radial
    radial_shells: List[RadialShell]
    radial_uniformity: float  # 0–1, how evenly distributed radially
    # Composite
    symmetry_index: float  # 0–100


# ── Core analysis ───────────────────────────────────────────────────


def _centroid(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Compute centroid of a point set."""
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    return (cx, cy)


def _translate(points, dx, dy):
    """Shift all points."""
    return [(x + dx, y + dy) for x, y in points]


def _rotate(points, angle_rad):
    """Rotate points around origin by angle_rad."""
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in points]


def _reflect(points, axis_rad):
    """Reflect points across a line through origin at axis_rad."""
    cos2 = math.cos(2 * axis_rad)
    sin2 = math.sin(2 * axis_rad)
    return [(x * cos2 + y * sin2, x * sin2 - y * cos2) for x, y in points]


def _match_score(original, transformed, scale):
    """Score how well transformed matches original (0–1).

    For each transformed point, find nearest original point.
    Score = 1 - (mean_min_dist / scale), clamped to [0, 1].
    """
    if not original or not transformed:
        return 0.0, float('inf')

    try:
        import numpy as np
        from scipy.spatial import KDTree
        orig_arr = np.array(original)
        trans_arr = np.array(transformed)
        tree = KDTree(orig_arr)
        dists, _ = tree.query(trans_arr)
        mean_dist = float(np.mean(dists))
    except ImportError:
        # Fallback: brute force
        total = 0.0
        for tx, ty in transformed:
            min_d = float('inf')
            for ox, oy in original:
                d = math.hypot(tx - ox, ty - oy)
                if d < min_d:
                    min_d = d
            total += min_d
        mean_dist = total / len(transformed)

    score = max(0.0, 1.0 - mean_dist / scale) if scale > 0 else 0.0
    return score, mean_dist


def _characteristic_scale(points):
    """Estimate characteristic scale as mean nearest-neighbor distance."""
    if len(points) < 2:
        return 1.0
    try:
        import numpy as np
        from scipy.spatial import KDTree
        arr = np.array(points)
        tree = KDTree(arr)
        dists, _ = tree.query(arr, k=2)
        return float(np.mean(dists[:, 1]))
    except ImportError:
        # Sample-based brute force
        sample = points[:min(200, len(points))]
        total = 0.0
        for i, (px, py) in enumerate(sample):
            min_d = float('inf')
            for j, (qx, qy) in enumerate(points):
                if i == j:
                    continue
                d = math.hypot(px - qx, py - qy)
                if d < min_d:
                    min_d = d
            total += min_d
        return total / len(sample) if sample else 1.0


def _detect_shells(distances, num_bins=50):
    """Detect radial shells from distance-from-centroid values."""
    if not distances:
        return []
    max_d = max(distances)
    if max_d == 0:
        return [RadialShell(radius=0.0, count=len(distances), spread=0.0)]

    bin_width = max_d / num_bins
    bins = [[] for _ in range(num_bins)]
    for d in distances:
        idx = min(int(d / bin_width), num_bins - 1)
        bins[idx].append(d)

    # Find peaks: bins with more points than neighbours
    counts = [len(b) for b in bins]
    shells = []
    for i in range(num_bins):
        if counts[i] == 0:
            continue
        left = counts[i - 1] if i > 0 else 0
        right = counts[i + 1] if i < num_bins - 1 else 0
        if counts[i] >= left and counts[i] >= right and counts[i] >= 2:
            vals = bins[i]
            mean_r = sum(vals) / len(vals)
            spread = math.sqrt(sum((v - mean_r) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
            shells.append(RadialShell(radius=round(mean_r, 4), count=len(vals), spread=round(spread, 4)))

    return shells


def symmetry_analysis(data, *, max_fold=12, num_axes=360):
    """Run full symmetry analysis on loaded VoronoiMap data.

    Parameters
    ----------
    data : list
        Point data from ``vormap.load_data()``.  Each item is
        ``[x, y, ...]``.
    max_fold : int
        Maximum rotational fold to test (2–max_fold inclusive).
    num_axes : int
        Number of candidate reflection axes to test (evenly spaced).

    Returns
    -------
    SymmetryResult
    """
    points = [(float(p[0]), float(p[1])) for p in data]
    n = len(points)
    if n < 3:
        return SymmetryResult(
            centroid=(0, 0), num_points=n,
            best_fold=1, best_fold_score=0.0, rotational_scores=[],
            best_axis=None, reflective_score=0.0, top_axes=[],
            radial_shells=[], radial_uniformity=0.0, symmetry_index=0.0,
        )

    cx, cy = _centroid(points)
    centered = _translate(points, -cx, -cy)
    scale = _characteristic_scale(centered)

    # ── Rotational symmetry ──────────────────────────────────────
    rot_results = []
    for fold in range(2, max_fold + 1):
        angle = 2 * math.pi / fold
        rotated = _rotate(centered, angle)
        score, mean_d = _match_score(centered, rotated, scale)
        rot_results.append(RotationalResult(
            fold=fold, score=round(score, 4),
            mean_displacement=round(mean_d, 4),
        ))

    best_rot = max(rot_results, key=lambda r: r.score)

    # ── Reflective symmetry ──────────────────────────────────────
    axis_results = []
    for i in range(num_axes):
        angle = math.pi * i / num_axes  # 0 to π
        reflected = _reflect(centered, angle)
        score, mean_d = _match_score(centered, reflected, scale)
        axis_results.append(ReflectionAxis(
            angle_deg=round(180.0 * i / num_axes, 2),
            score=round(score, 4),
            mean_displacement=round(mean_d, 4),
        ))

    axis_results.sort(key=lambda a: a.score, reverse=True)
    top_axes = axis_results[:5]
    ref_score = top_axes[0].score if top_axes else 0.0

    # ── Radial analysis ──────────────────────────────────────────
    distances = [math.hypot(x, y) for x, y in centered]
    shells = _detect_shells(distances)

    # Radial uniformity: how evenly spread are angles?
    angles = [math.atan2(y, x) for x, y in centered]
    angles.sort()
    if len(angles) > 1:
        gaps = []
        for i in range(1, len(angles)):
            gaps.append(angles[i] - angles[i - 1])
        gaps.append(2 * math.pi - angles[-1] + angles[0])
        expected_gap = 2 * math.pi / len(angles)
        variance = sum((g - expected_gap) ** 2 for g in gaps) / len(gaps)
        max_var = expected_gap ** 2  # worst case
        radial_uni = max(0.0, 1.0 - math.sqrt(variance) / expected_gap)
    else:
        radial_uni = 0.0

    # ── Composite index ──────────────────────────────────────────
    sym_index = round(
        40 * best_rot.score + 40 * ref_score + 20 * radial_uni, 1
    )

    return SymmetryResult(
        centroid=(round(cx, 4), round(cy, 4)),
        num_points=n,
        best_fold=best_rot.fold,
        best_fold_score=best_rot.score,
        rotational_scores=rot_results,
        best_axis=top_axes[0] if top_axes else None,
        reflective_score=ref_score,
        top_axes=top_axes,
        radial_shells=shells,
        radial_uniformity=round(radial_uni, 4),
        symmetry_index=sym_index,
    )


# ── Formatting ──────────────────────────────────────────────────────


def format_report(result: SymmetryResult) -> str:
    """Human-readable symmetry report."""
    lines = [
        "=" * 60,
        "  VORONOI SYMMETRY ANALYSIS",
        "=" * 60,
        f"  Points analysed : {result.num_points}",
        f"  Centroid         : ({result.centroid[0]}, {result.centroid[1]})",
        "",
        "─── Rotational Symmetry ──────────────────────────────────",
        f"  Best fold  : {result.best_fold}-fold  (score {result.best_fold_score:.3f})",
        "",
    ]

    for r in result.rotational_scores:
        bar = "█" * int(r.score * 20)
        lines.append(f"  {r.fold:2d}-fold : {r.score:.3f}  {bar}")

    lines.append("")
    lines.append("─── Reflective Symmetry ─────────────────────────────────")
    lines.append(f"  Best score : {result.reflective_score:.3f}")
    if result.top_axes:
        lines.append("  Top axes:")
        for ax in result.top_axes:
            lines.append(f"    {ax.angle_deg:6.1f}°  score={ax.score:.3f}")

    lines.append("")
    lines.append("─── Radial Distribution ─────────────────────────────────")
    lines.append(f"  Angular uniformity : {result.radial_uniformity:.3f}")
    if result.radial_shells:
        lines.append(f"  Shells detected    : {len(result.radial_shells)}")
        for s in result.radial_shells[:8]:
            lines.append(f"    r={s.radius:8.2f}  n={s.count:3d}  σ={s.spread:.3f}")

    lines.append("")
    lines.append("─── Composite Score ─────────────────────────────────────")

    idx = result.symmetry_index
    if idx >= 80:
        label = "Highly symmetric"
    elif idx >= 50:
        label = "Moderately symmetric"
    elif idx >= 20:
        label = "Low symmetry"
    else:
        label = "Asymmetric / random"

    bar = "█" * int(idx / 2) + "░" * (50 - int(idx / 2))
    lines.append(f"  Symmetry Index : {idx:.1f} / 100  ({label})")
    lines.append(f"  [{bar}]")
    lines.append("")
    lines.append("  Weights: rotational=40%, reflective=40%, radial=20%")
    lines.append("=" * 60)

    return "\n".join(lines)


def to_dict(result: SymmetryResult) -> dict:
    """Serialise to JSON-friendly dict."""
    return {
        "centroid": list(result.centroid),
        "num_points": result.num_points,
        "best_fold": result.best_fold,
        "best_fold_score": result.best_fold_score,
        "rotational_scores": [
            {"fold": r.fold, "score": r.score, "mean_displacement": r.mean_displacement}
            for r in result.rotational_scores
        ],
        "reflective_score": result.reflective_score,
        "top_axes": [
            {"angle_deg": a.angle_deg, "score": a.score, "mean_displacement": a.mean_displacement}
            for a in result.top_axes
        ],
        "radial_shells": [
            {"radius": s.radius, "count": s.count, "spread": s.spread}
            for s in result.radial_shells
        ],
        "radial_uniformity": result.radial_uniformity,
        "symmetry_index": result.symmetry_index,
    }


# ── CLI ─────────────────────────────────────────────────────────────


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Analyse rotational & reflective symmetry of a Voronoi point set.",
    )
    parser.add_argument("datafile", help="Path to point data file")
    parser.add_argument("--max-fold", type=int, default=12,
                        help="Maximum rotational fold to test (default: 12)")
    parser.add_argument("--axes", type=int, default=360,
                        help="Number of reflection axes to scan (default: 360)")
    parser.add_argument("--json", metavar="FILE",
                        help="Write JSON results to FILE")
    args = parser.parse_args(argv)

    path = vormap.validate_input_path(args.datafile, allow_absolute=True)
    data = vormap.load_data(path)

    result = symmetry_analysis(data, max_fold=args.max_fold, num_axes=args.axes)
    print(format_report(result))

    if args.json:
        out_path = vormap.validate_input_path(args.json, allow_absolute=True)
        with open(out_path, "w") as f:
            json.dump(to_dict(result), f, indent=2)
        print(f"\nJSON written to {out_path}")


if __name__ == "__main__":
    main()
