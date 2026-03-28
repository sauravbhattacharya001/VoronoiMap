"""Spatial Data Quality Assessment for Voronoi diagrams.

Evaluates point datasets for distribution quality before Voronoi analysis.
Provides metrics on spacing uniformity, boundary effects, duplicate/near-
duplicate detection, density variation, and overall quality scoring with
actionable recommendations.

Seven quality checks are performed:

- **Spacing Statistics** — min/max/mean/median/std of nearest-neighbor
  distances, coefficient of variation.
- **Uniformity Index** — ratio of observed mean NN distance to expected
  mean NN distance under CSR (complete spatial randomness).
- **Duplicate Detection** — finds exact duplicates and near-duplicates
  within a configurable tolerance.
- **Boundary Proximity** — identifies points too close to domain edges,
  which cause unbounded or distorted Voronoi cells.
- **Density Variation** — quadrat-based density analysis detecting
  over-dense and sparse regions.
- **Isolation Detection** — flags points whose NN distance exceeds a
  threshold (potential data errors or extreme outliers).
- **Overall Quality Score** — weighted composite 0–100 score with
  letter grade and prioritised recommendations.

Usage (Python API)::

    import vormap
    from vormap_quality import assess_quality, QualityReport

    data = vormap.load_data("datauni5.txt")
    points = [(d["x"], d["y"]) for d in data]
    bounds = (0, 1000, 0, 2000)  # south, north, west, east

    report = assess_quality(points, bounds=bounds)
    print(f"Score: {report.score}/100 ({report.grade})")
    for r in report.recommendations:
        print(f"  - {r}")

    # Export
    report.to_json("quality.json")
    report.to_csv("quality.csv")
    report.to_svg("quality.svg")

CLI::

    voronoimap datauni5.txt 5 --quality
    voronoimap datauni5.txt 5 --quality-json quality.json
    voronoimap datauni5.txt 5 --quality-csv quality.csv
    voronoimap datauni5.txt 5 --quality-svg quality.svg
    voronoimap datauni5.txt 5 --quality --boundary-margin 0.05
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

from vormap import validate_output_path
from vormap_geometry import mean as _mean, median as _median, std as _std


# ── Helpers ─────────────────────────────────────────────────────────


from vormap_utils import euclidean as _euclidean


def _nn_distances(points):
    """Return list of nearest-neighbor distances for each point."""
    n = len(points)
    if n < 2:
        return [0.0] * n
    dists = []
    for i in range(n):
        min_d = float("inf")
        for j in range(n):
            if i != j:
                d = _euclidean(points[i], points[j])
                if d < min_d:
                    min_d = d
        dists.append(min_d)
    return dists


def _auto_bounds(points):
    """Derive bounding box from points with 5% padding."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad_x = max((max_x - min_x) * 0.05, 1.0)
    pad_y = max((max_y - min_y) * 0.05, 1.0)
    return (min_y - pad_y, max_y + pad_y, min_x - pad_x, max_x + pad_x)


# ── Result containers ──────────────────────────────────────────────


@dataclass
class SpacingStats:
    """Nearest-neighbor spacing statistics."""
    min_dist: float = 0.0
    max_dist: float = 0.0
    mean_dist: float = 0.0
    median_dist: float = 0.0
    std_dist: float = 0.0
    cv: float = 0.0  # coefficient of variation

    def to_dict(self):
        return {
            "min_distance": round(self.min_dist, 4),
            "max_distance": round(self.max_dist, 4),
            "mean_distance": round(self.mean_dist, 4),
            "median_distance": round(self.median_dist, 4),
            "std_distance": round(self.std_dist, 4),
            "coefficient_of_variation": round(self.cv, 4),
        }


@dataclass
class DuplicateInfo:
    """Duplicate / near-duplicate point information."""
    exact_count: int = 0
    near_count: int = 0
    exact_groups: list = field(default_factory=list)
    near_groups: list = field(default_factory=list)
    tolerance: float = 0.0

    def to_dict(self):
        return {
            "exact_duplicates": self.exact_count,
            "near_duplicates": self.near_count,
            "tolerance": self.tolerance,
            "exact_groups": self.exact_groups,
            "near_groups": self.near_groups,
        }


@dataclass
class BoundaryInfo:
    """Boundary proximity analysis."""
    margin_fraction: float = 0.05
    points_near_boundary: int = 0
    fraction_near_boundary: float = 0.0
    indices: list = field(default_factory=list)

    def to_dict(self):
        return {
            "margin_fraction": self.margin_fraction,
            "points_near_boundary": self.points_near_boundary,
            "fraction_near_boundary": round(self.fraction_near_boundary, 4),
            "point_indices": self.indices[:50],  # cap for readability
        }


@dataclass
class DensityInfo:
    """Quadrat-based density variation."""
    grid_size: int = 0
    cell_counts: list = field(default_factory=list)
    mean_density: float = 0.0
    max_density: float = 0.0
    min_density: float = 0.0
    cv: float = 0.0
    sparse_cells: int = 0
    dense_cells: int = 0

    def to_dict(self):
        return {
            "grid_size": self.grid_size,
            "mean_density": round(self.mean_density, 4),
            "max_density": self.max_density,
            "min_density": self.min_density,
            "density_cv": round(self.cv, 4),
            "sparse_cells": self.sparse_cells,
            "dense_cells": self.dense_cells,
        }


@dataclass
class IsolationInfo:
    """Isolated point detection."""
    threshold_multiplier: float = 3.0
    threshold_distance: float = 0.0
    isolated_count: int = 0
    indices: list = field(default_factory=list)

    def to_dict(self):
        return {
            "threshold_multiplier": self.threshold_multiplier,
            "threshold_distance": round(self.threshold_distance, 4),
            "isolated_count": self.isolated_count,
            "point_indices": self.indices[:50],
        }


@dataclass
class QualityReport:
    """Complete spatial data quality assessment report."""
    n_points: int = 0
    bounds: tuple = (0, 0, 0, 0)
    spacing: SpacingStats = field(default_factory=SpacingStats)
    uniformity_index: float = 0.0
    duplicates: DuplicateInfo = field(default_factory=DuplicateInfo)
    boundary: BoundaryInfo = field(default_factory=BoundaryInfo)
    density: DensityInfo = field(default_factory=DensityInfo)
    isolation: IsolationInfo = field(default_factory=IsolationInfo)
    score: float = 0.0
    grade: str = "F"
    recommendations: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "n_points": self.n_points,
            "bounds": {"south": self.bounds[0], "north": self.bounds[1],
                       "west": self.bounds[2], "east": self.bounds[3]},
            "score": round(self.score, 1),
            "grade": self.grade,
            "spacing": self.spacing.to_dict(),
            "uniformity_index": round(self.uniformity_index, 4),
            "duplicates": self.duplicates.to_dict(),
            "boundary": self.boundary.to_dict(),
            "density": self.density.to_dict(),
            "isolation": self.isolation.to_dict(),
            "recommendations": self.recommendations,
            "checks": self.checks,
        }

    def to_json(self, path, *, allow_absolute=False):
        """Export report as JSON."""
        safe = validate_output_path(path, allow_absolute=allow_absolute)
        with open(safe, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return safe

    def to_csv(self, path, *, allow_absolute=False):
        """Export key metrics as CSV."""
        safe = validate_output_path(path, allow_absolute=allow_absolute)
        rows = [
            ["metric", "value"],
            ["n_points", str(self.n_points)],
            ["score", str(round(self.score, 1))],
            ["grade", self.grade],
            ["uniformity_index", str(round(self.uniformity_index, 4))],
            ["min_distance", str(round(self.spacing.min_dist, 4))],
            ["max_distance", str(round(self.spacing.max_dist, 4))],
            ["mean_distance", str(round(self.spacing.mean_dist, 4))],
            ["median_distance", str(round(self.spacing.median_dist, 4))],
            ["spacing_cv", str(round(self.spacing.cv, 4))],
            ["exact_duplicates", str(self.duplicates.exact_count)],
            ["near_duplicates", str(self.duplicates.near_count)],
            ["boundary_points", str(self.boundary.points_near_boundary)],
            ["density_cv", str(round(self.density.cv, 4))],
            ["isolated_points", str(self.isolation.isolated_count)],
        ]
        with open(safe, "w") as f:
            for row in rows:
                f.write(",".join(row) + "\n")
        return safe

    def to_svg(self, path, *, allow_absolute=False, width=700, height=500):
        """Export a visual quality dashboard as SVG."""
        safe = validate_output_path(path, allow_absolute=allow_absolute)
        svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                         width=str(width), height=str(height),
                         viewBox=f"0 0 {width} {height}")

        # Background
        ET.SubElement(svg, "rect", x="0", y="0", width=str(width),
                      height=str(height), fill="#f8f9fa", rx="8")

        # Title
        title = ET.SubElement(svg, "text", x=str(width // 2), y="35",
                              fill="#212529")
        title.set("text-anchor", "middle")
        title.set("font-family", "Arial, sans-serif")
        title.set("font-size", "18")
        title.set("font-weight", "bold")
        title.text = f"Spatial Data Quality: {self.grade} ({self.score:.0f}/100)"

        # Score bar
        bar_y = 55
        bar_w = width - 80
        ET.SubElement(svg, "rect", x="40", y=str(bar_y),
                      width=str(bar_w), height="20", fill="#e9ecef", rx="4")
        score_color = _score_color(self.score)
        filled_w = max(1, int(bar_w * self.score / 100))
        ET.SubElement(svg, "rect", x="40", y=str(bar_y),
                      width=str(filled_w), height="20",
                      fill=score_color, rx="4")

        # Check scores as horizontal bars
        checks = list(self.checks.items())
        y_start = 100
        bar_height = 22
        gap = 8
        max_label_w = 180
        chart_w = width - max_label_w - 80

        for i, (name, val) in enumerate(checks):
            y = y_start + i * (bar_height + gap)
            # Label
            label = ET.SubElement(svg, "text", x=str(max_label_w - 5),
                                  y=str(y + bar_height - 6), fill="#495057")
            label.set("text-anchor", "end")
            label.set("font-family", "Arial, sans-serif")
            label.set("font-size", "13")
            label.text = name.replace("_", " ").title()

            # Background bar
            ET.SubElement(svg, "rect", x=str(max_label_w),
                          y=str(y), width=str(chart_w),
                          height=str(bar_height), fill="#e9ecef", rx="3")
            # Value bar
            vw = max(1, int(chart_w * val / 100))
            ET.SubElement(svg, "rect", x=str(max_label_w),
                          y=str(y), width=str(vw),
                          height=str(bar_height),
                          fill=_score_color(val), rx="3")
            # Score text
            sc = ET.SubElement(svg, "text",
                               x=str(max_label_w + vw + 5),
                               y=str(y + bar_height - 6), fill="#212529")
            sc.set("font-family", "Arial, sans-serif")
            sc.set("font-size", "12")
            sc.text = f"{val:.0f}"

        # Recommendations
        rec_y = y_start + len(checks) * (bar_height + gap) + 20
        rec_title = ET.SubElement(svg, "text", x="40", y=str(rec_y),
                                  fill="#212529")
        rec_title.set("font-family", "Arial, sans-serif")
        rec_title.set("font-size", "14")
        rec_title.set("font-weight", "bold")
        rec_title.text = "Recommendations"

        for j, rec in enumerate(self.recommendations[:5]):
            ry = rec_y + 20 + j * 18
            rt = ET.SubElement(svg, "text", x="50", y=str(ry),
                               fill="#6c757d")
            rt.set("font-family", "Arial, sans-serif")
            rt.set("font-size", "12")
            rt.text = f"• {rec[:80]}"

        tree = ET.ElementTree(svg)
        ET.indent(tree, space="  ")
        tree.write(safe, xml_declaration=True, encoding="unicode")
        return safe

    def summary(self):
        """Return a human-readable summary string."""
        lines = [
            f"Spatial Data Quality Report",
            f"==========================",
            f"Points: {self.n_points}",
            f"Score:  {self.score:.0f}/100 ({self.grade})",
            f"",
            f"Spacing:  mean={self.spacing.mean_dist:.2f}  "
            f"std={self.spacing.std_dist:.2f}  CV={self.spacing.cv:.3f}",
            f"Uniformity Index: {self.uniformity_index:.3f}  "
            f"(1.0 = perfectly regular)",
            f"Duplicates: {self.duplicates.exact_count} exact, "
            f"{self.duplicates.near_count} near "
            f"(tol={self.duplicates.tolerance:.2f})",
            f"Boundary points: {self.boundary.points_near_boundary} "
            f"({self.boundary.fraction_near_boundary:.1%})",
            f"Density CV: {self.density.cv:.3f}  "
            f"(sparse cells: {self.density.sparse_cells}, "
            f"dense cells: {self.density.dense_cells})",
            f"Isolated points: {self.isolation.isolated_count}",
            f"",
        ]
        if self.recommendations:
            lines.append("Recommendations:")
            for r in self.recommendations:
                lines.append(f"  - {r}")
        return "\n".join(lines)


def _score_color(score):
    """Map a 0–100 score to a color."""
    if score >= 80:
        return "#28a745"
    elif score >= 60:
        return "#ffc107"
    elif score >= 40:
        return "#fd7e14"
    else:
        return "#dc3545"


def _grade(score):
    if score >= 90:
        return "A"
    elif score >= 80:
        return "B"
    elif score >= 70:
        return "C"
    elif score >= 60:
        return "D"
    else:
        return "F"


# ── Core assessment ────────────────────────────────────────────────


def _check_spacing(nn_dists):
    """Compute spacing statistics from NN distances."""
    stats = SpacingStats()
    if not nn_dists:
        return stats, 0.0
    stats.min_dist = min(nn_dists)
    stats.max_dist = max(nn_dists)
    stats.mean_dist = _mean(nn_dists)
    s = sorted(nn_dists)
    stats.median_dist = _median(s)
    stats.std_dist = _std(nn_dists)
    stats.cv = stats.std_dist / stats.mean_dist if stats.mean_dist > 0 else 0.0
    # Score: lower CV is better. CV < 0.3 = excellent, > 1.0 = poor
    score = max(0, min(100, 100 - (stats.cv - 0.2) * 120))
    return stats, score


def _check_uniformity(nn_dists, n_points, area):
    """Compute uniformity index (Clark-Evans ratio)."""
    if n_points < 2 or area <= 0:
        return 0.0, 50.0
    observed_mean = _mean(nn_dists)
    expected_mean = 0.5 * math.sqrt(area / n_points)
    ui = observed_mean / expected_mean if expected_mean > 0 else 0.0
    # Score: 1.0 = random (ok), >1 = regular (great), <0.5 = very clustered
    if ui >= 1.0:
        score = min(100, 70 + (ui - 1.0) * 30)
    else:
        score = max(0, ui * 70)
    return ui, score


def _check_duplicates(points, tolerance=1.0):
    """Detect exact and near-duplicate points."""
    info = DuplicateInfo(tolerance=tolerance)
    n = len(points)
    exact_seen = set()
    near_seen = set()

    for i in range(n):
        for j in range(i + 1, n):
            d = _euclidean(points[i], points[j])
            if d == 0.0:
                exact_seen.add(i)
                exact_seen.add(j)
            elif d < tolerance:
                near_seen.add(i)
                near_seen.add(j)

    info.exact_count = len(exact_seen)
    info.near_count = len(near_seen - exact_seen)
    info.exact_groups = sorted(exact_seen)[:20]
    info.near_groups = sorted(near_seen - exact_seen)[:20]

    # Score: penalise duplicates heavily
    dup_frac = (info.exact_count + info.near_count * 0.5) / max(n, 1)
    score = max(0, 100 - dup_frac * 300)
    return info, score


def _check_boundary(points, bounds, margin_fraction=0.05):
    """Check how many points are too close to domain boundaries."""
    info = BoundaryInfo(margin_fraction=margin_fraction)
    s, n_b, w, e = bounds
    height = n_b - s
    width = e - w
    margin_y = height * margin_fraction
    margin_x = width * margin_fraction

    for i, (x, y) in enumerate(points):
        if (y - s < margin_y or n_b - y < margin_y or
                x - w < margin_x or e - x < margin_x):
            info.indices.append(i)

    info.points_near_boundary = len(info.indices)
    info.fraction_near_boundary = len(info.indices) / max(len(points), 1)
    # Score: some boundary points are normal; >30% is concerning
    score = max(0, 100 - max(0, info.fraction_near_boundary - 0.1) * 150)
    return info, score


def _check_density(points, bounds, grid_size=None):
    """Quadrat density analysis."""
    info = DensityInfo()
    s, n_b, w, e = bounds
    n = len(points)
    if grid_size is None:
        grid_size = max(2, min(10, int(math.sqrt(n) / 2)))
    info.grid_size = grid_size

    cell_w = (e - w) / grid_size
    cell_h = (n_b - s) / grid_size
    counts = [0] * (grid_size * grid_size)

    for x, y in points:
        col = min(grid_size - 1, max(0, int((x - w) / cell_w)))
        row = min(grid_size - 1, max(0, int((y - s) / cell_h)))
        counts[row * grid_size + col] += 1

    info.cell_counts = counts
    info.mean_density = _mean(counts) if counts else 0
    info.max_density = max(counts) if counts else 0
    info.min_density = min(counts) if counts else 0
    info.cv = (_std(counts) / info.mean_density) if info.mean_density > 0 else 0

    # Sparse = less than 20% of mean, dense = more than 300% of mean
    threshold_sparse = info.mean_density * 0.2
    threshold_dense = info.mean_density * 3.0
    info.sparse_cells = sum(1 for c in counts if c < threshold_sparse)
    info.dense_cells = sum(1 for c in counts if c > threshold_dense)

    # Score: lower CV is better
    score = max(0, min(100, 100 - (info.cv - 0.3) * 100))
    return info, score


def _check_isolation(points, nn_dists, multiplier=3.0):
    """Detect isolated points with NN distance > multiplier * median."""
    info = IsolationInfo(threshold_multiplier=multiplier)
    if not nn_dists:
        return info, 100.0
    med = _median(sorted(nn_dists))
    info.threshold_distance = med * multiplier

    for i, d in enumerate(nn_dists):
        if d > info.threshold_distance:
            info.indices.append(i)
    info.isolated_count = len(info.indices)

    frac = info.isolated_count / max(len(points), 1)
    score = max(0, 100 - frac * 400)
    return info, score


def assess_quality(points, *, bounds=None, dup_tolerance=None,
                   boundary_margin=0.05, isolation_multiplier=3.0,
                   grid_size=None):
    """Run all quality checks and produce a QualityReport.

    Parameters
    ----------
    points : list[tuple[float, float]]
        Input point coordinates (x, y).
    bounds : tuple or None
        Domain bounds (south, north, west, east). Auto-detected if None.
    dup_tolerance : float or None
        Near-duplicate distance tolerance. Defaults to 1% of domain diagonal.
    boundary_margin : float
        Fraction of domain size for boundary proximity check (default 0.05).
    isolation_multiplier : float
        NN distance multiplier for isolation detection (default 3.0).
    grid_size : int or None
        Grid size for quadrat density analysis. Auto-computed if None.

    Returns
    -------
    QualityReport
        Complete quality assessment with score, grade, and recommendations.
    """
    report = QualityReport()
    report.n_points = len(points)

    if len(points) < 2:
        report.score = 0
        report.grade = "F"
        report.recommendations = ["Need at least 2 points for analysis."]
        return report

    if bounds is None:
        bounds = _auto_bounds(points)
    report.bounds = bounds

    s, n_b, w, e = bounds
    area = (n_b - s) * (e - w)

    if dup_tolerance is None:
        diag = math.sqrt((n_b - s) ** 2 + (e - w) ** 2)
        dup_tolerance = diag * 0.01

    # Run checks
    nn_dists = _nn_distances(points)

    spacing, spacing_score = _check_spacing(nn_dists)
    report.spacing = spacing

    ui, ui_score = _check_uniformity(nn_dists, len(points), area)
    report.uniformity_index = ui

    dups, dup_score = _check_duplicates(points, dup_tolerance)
    report.duplicates = dups

    boundary, boundary_score = _check_boundary(points, bounds, boundary_margin)
    report.boundary = boundary

    density, density_score = _check_density(points, bounds, grid_size)
    report.density = density

    isolation, isolation_score = _check_isolation(points, nn_dists,
                                                   isolation_multiplier)
    report.isolation = isolation

    # Store individual check scores
    report.checks = {
        "spacing_uniformity": round(spacing_score, 1),
        "spatial_distribution": round(ui_score, 1),
        "duplicate_free": round(dup_score, 1),
        "boundary_clearance": round(boundary_score, 1),
        "density_evenness": round(density_score, 1),
        "no_isolation": round(isolation_score, 1),
    }

    # Weighted composite score
    weights = {
        "spacing_uniformity": 0.20,
        "spatial_distribution": 0.20,
        "duplicate_free": 0.20,
        "boundary_clearance": 0.10,
        "density_evenness": 0.20,
        "no_isolation": 0.10,
    }
    report.score = sum(report.checks[k] * weights[k] for k in weights)
    report.grade = _grade(report.score)

    # Recommendations
    recs = []
    if dup_score < 80:
        recs.append(f"Remove {dups.exact_count} duplicate points "
                    f"and check {dups.near_count} near-duplicates "
                    f"(tolerance {dup_tolerance:.2f}).")
    if spacing_score < 60:
        recs.append(f"High spacing variation (CV={spacing.cv:.2f}). "
                    f"Consider resampling for more uniform coverage.")
    if ui_score < 50:
        recs.append(f"Points are clustered (uniformity={ui:.2f}). "
                    f"Voronoi cells will be very unequal in size.")
    if boundary_score < 70:
        recs.append(f"{boundary.points_near_boundary} points "
                    f"({boundary.fraction_near_boundary:.0%}) are near "
                    f"domain edges — expect distorted boundary cells.")
    if density_score < 60:
        recs.append(f"Uneven density (CV={density.cv:.2f}): "
                    f"{density.sparse_cells} sparse and "
                    f"{density.dense_cells} over-dense quadrats.")
    if isolation_score < 80:
        recs.append(f"{isolation.isolated_count} isolated points detected "
                    f"(>{isolation.threshold_multiplier}× median NN distance). "
                    f"Verify these are not data errors.")
    if not recs:
        recs.append("Data quality looks good — no issues detected.")
    report.recommendations = recs

    return report
