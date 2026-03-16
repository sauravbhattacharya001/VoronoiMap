"""Spatial Center Analysis for Voronoi point datasets.

Computes central tendency measures and dispersion metrics for point
distributions — essential spatial statistics for understanding where
the "center" of activity lies and how spread out points are.

Six analyses are performed:

- **Mean Center** — arithmetic mean of X/Y coordinates (geographic centroid).
- **Weighted Mean Center** — mean center weighted by a value attribute.
- **Median Center** — the point minimizing sum of Euclidean distances to
  all other points (Weber point), computed iteratively.
- **Central Feature** — the actual data point closest to the mean center.
- **Standard Distance** — spatial equivalent of standard deviation; radius
  within which ~68% of points fall.
- **Standard Deviational Ellipse** — orientation (angle), semi-major and
  semi-minor axes capturing directional spread.

Usage (Python API)::

    import vormap
    from vormap_centroid import analyze_centers, CenterReport

    data = vormap.load_data("datauni5.txt")
    points = [(d["x"], d["y"]) for d in data]

    report = analyze_centers(points)
    print(f"Mean center: ({report.mean_center[0]:.2f}, {report.mean_center[1]:.2f})")
    print(f"Standard distance: {report.standard_distance:.2f}")
    print(f"Ellipse angle: {report.ellipse_angle:.1f}°")

    # With weights
    weights = [d.get("value", 1) for d in data]
    report = analyze_centers(points, weights=weights)

    # Export
    report.to_json("centers.json")
    report.to_csv("centers.csv")
    report.to_svg("centers.svg")

CLI::

    voronoimap datauni5.txt 5 --centers
    voronoimap datauni5.txt 5 --centers-json centers.json
    voronoimap datauni5.txt 5 --centers-csv centers.csv
    voronoimap datauni5.txt 5 --centers-svg centers.svg
    voronoimap datauni5.txt 5 --centers --weights values.txt
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

from vormap import validate_output_path


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CenterReport:
    """Results of spatial center analysis."""

    mean_center: Tuple[float, float] = (0.0, 0.0)
    weighted_mean_center: Optional[Tuple[float, float]] = None
    median_center: Tuple[float, float] = (0.0, 0.0)
    central_feature: Tuple[float, float] = (0.0, 0.0)
    central_feature_index: int = 0
    standard_distance: float = 0.0
    standard_distance_weighted: Optional[float] = None
    ellipse_angle: float = 0.0          # degrees, clockwise from north
    ellipse_semi_major: float = 0.0
    ellipse_semi_minor: float = 0.0
    ellipse_rotation: float = 0.0       # radians
    point_count: int = 0
    bounds: Optional[Tuple[float, float, float, float]] = None  # S, N, W, E

    # Summary helpers
    def summary(self) -> str:
        """Return a human-readable summary string."""
        lines = [
            f"Spatial Center Analysis ({self.point_count} points)",
            f"  Mean center:       ({self.mean_center[0]:.4f}, {self.mean_center[1]:.4f})",
        ]
        if self.weighted_mean_center is not None:
            lines.append(
                f"  Weighted mean:     ({self.weighted_mean_center[0]:.4f}, "
                f"{self.weighted_mean_center[1]:.4f})"
            )
        lines += [
            f"  Median center:     ({self.median_center[0]:.4f}, {self.median_center[1]:.4f})",
            f"  Central feature:   ({self.central_feature[0]:.4f}, "
            f"{self.central_feature[1]:.4f})  [index {self.central_feature_index}]",
            f"  Standard distance: {self.standard_distance:.4f}",
        ]
        if self.standard_distance_weighted is not None:
            lines.append(f"  Weighted std dist: {self.standard_distance_weighted:.4f}")
        lines += [
            f"  Ellipse angle:     {self.ellipse_angle:.1f}°",
            f"  Ellipse semi-major:{self.ellipse_semi_major:.4f}",
            f"  Ellipse semi-minor:{self.ellipse_semi_minor:.4f}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Export helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Serialise report to a plain dict."""
        d: Dict[str, Any] = {
            "point_count": self.point_count,
            "mean_center": {"x": self.mean_center[0], "y": self.mean_center[1]},
            "median_center": {"x": self.median_center[0], "y": self.median_center[1]},
            "central_feature": {
                "x": self.central_feature[0],
                "y": self.central_feature[1],
                "index": self.central_feature_index,
            },
            "standard_distance": self.standard_distance,
            "ellipse": {
                "angle_degrees": self.ellipse_angle,
                "semi_major": self.ellipse_semi_major,
                "semi_minor": self.ellipse_semi_minor,
                "rotation_radians": self.ellipse_rotation,
            },
        }
        if self.weighted_mean_center is not None:
            d["weighted_mean_center"] = {
                "x": self.weighted_mean_center[0],
                "y": self.weighted_mean_center[1],
            }
        if self.standard_distance_weighted is not None:
            d["standard_distance_weighted"] = self.standard_distance_weighted
        if self.bounds is not None:
            d["bounds"] = {
                "south": self.bounds[0], "north": self.bounds[1],
                "west": self.bounds[2], "east": self.bounds[3],
            }
        return d

    def to_json(self, path: str) -> None:
        """Write report as JSON."""
        safe = validate_output_path(path)
        with open(safe, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2)

    def to_csv(self, path: str) -> None:
        """Write key metrics as CSV."""
        safe = validate_output_path(path)
        rows = [
            ("metric", "x_or_value", "y"),
            ("mean_center", f"{self.mean_center[0]:.6f}", f"{self.mean_center[1]:.6f}"),
            ("median_center", f"{self.median_center[0]:.6f}", f"{self.median_center[1]:.6f}"),
            ("central_feature", f"{self.central_feature[0]:.6f}", f"{self.central_feature[1]:.6f}"),
            ("standard_distance", f"{self.standard_distance:.6f}", ""),
            ("ellipse_angle_deg", f"{self.ellipse_angle:.2f}", ""),
            ("ellipse_semi_major", f"{self.ellipse_semi_major:.6f}", ""),
            ("ellipse_semi_minor", f"{self.ellipse_semi_minor:.6f}", ""),
        ]
        if self.weighted_mean_center is not None:
            rows.insert(2, (
                "weighted_mean_center",
                f"{self.weighted_mean_center[0]:.6f}",
                f"{self.weighted_mean_center[1]:.6f}",
            ))
        if self.standard_distance_weighted is not None:
            rows.append(("standard_distance_weighted",
                         f"{self.standard_distance_weighted:.6f}", ""))
        with open(safe, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(",".join(row) + "\n")

    def to_svg(self, path: str, *, width: int = 600, height: int = 500) -> None:
        """Render centers and ellipse as SVG visualisation."""
        safe = validate_output_path(path)

        pad = 40
        pw, ph = width - 2 * pad, height - 2 * pad

        # Determine data bounds
        if self.bounds is not None:
            s, n, w, e = self.bounds
        else:
            xs = [self.mean_center[0], self.median_center[0], self.central_feature[0]]
            ys = [self.mean_center[1], self.median_center[1], self.central_feature[1]]
            if self.weighted_mean_center:
                xs.append(self.weighted_mean_center[0])
                ys.append(self.weighted_mean_center[1])
            margin = max(self.standard_distance * 2, 1)
            w, e = min(xs) - margin, max(xs) + margin
            s, n = min(ys) - margin, max(ys) + margin

        dx = e - w if e != w else 1.0
        dy = n - s if n != s else 1.0

        def tx(x: float) -> float:
            return pad + (x - w) / dx * pw

        def ty(y: float) -> float:
            return pad + ph - (y - s) / dy * ph

        svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                         width=str(width), height=str(height))
        ET.SubElement(svg, "rect", x="0", y="0", width=str(width),
                      height=str(height), fill="#f8f9fa")

        # Ellipse
        cx, cy = tx(self.mean_center[0]), ty(self.mean_center[1])
        rx = self.ellipse_semi_major / dx * pw
        ry = self.ellipse_semi_minor / dy * ph
        angle = -self.ellipse_angle  # SVG uses clockwise
        ET.SubElement(svg, "ellipse", cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                      rx=f"{rx:.1f}", ry=f"{ry:.1f}",
                      fill="rgba(70,130,180,0.15)", stroke="#4682B4",
                      transform=f"rotate({angle:.1f} {cx:.1f} {cy:.1f})")

        # Standard distance circle
        sr = self.standard_distance / dx * pw
        ET.SubElement(svg, "circle", cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                      r=f"{sr:.1f}", fill="none", stroke="#999",
                      **{"stroke-dasharray": "5,5"})

        # Points
        markers = [
            (self.mean_center, "#e74c3c", "Mean"),
            (self.median_center, "#2ecc71", "Median"),
            (self.central_feature, "#f39c12", "Central"),
        ]
        if self.weighted_mean_center is not None:
            markers.insert(1, (self.weighted_mean_center, "#9b59b6", "Weighted"))

        for (px, py), color, label in markers:
            mx, my = tx(px), ty(py)
            ET.SubElement(svg, "circle", cx=f"{mx:.1f}", cy=f"{my:.1f}",
                          r="6", fill=color, stroke="#fff")
            ET.SubElement(svg, "text", x=f"{mx + 10:.1f}", y=f"{my + 4:.1f}",
                          fill=color).text = label

        # Title
        title = ET.SubElement(svg, "text", x=str(width // 2), y="20")
        title.set("text-anchor", "middle")
        title.set("font-size", "14")
        title.set("font-weight", "bold")
        title.text = f"Spatial Centers ({self.point_count} points)"

        tree = ET.ElementTree(svg)
        ET.indent(tree, space="  ")
        tree.write(safe, xml_declaration=True, encoding="unicode")


# ---------------------------------------------------------------------------
# Core algorithms
# ---------------------------------------------------------------------------

def _mean_center(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Compute arithmetic mean center."""
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    return (sx / n, sy / n)


def _weighted_mean_center(
    points: List[Tuple[float, float]],
    weights: List[float],
) -> Tuple[float, float]:
    """Compute weighted mean center."""
    tw = sum(weights)
    if tw == 0:
        return _mean_center(points)
    wx = sum(p[0] * w for p, w in zip(points, weights))
    wy = sum(p[1] * w for p, w in zip(points, weights))
    return (wx / tw, wy / tw)


def _median_center(
    points: List[Tuple[float, float]],
    *,
    max_iter: int = 1000,
    tol: float = 1e-8,
) -> Tuple[float, float]:
    """Compute spatial median (Weber point) via Weiszfeld's algorithm."""
    cx, cy = _mean_center(points)
    for _ in range(max_iter):
        wx, wy, wsum = 0.0, 0.0, 0.0
        for px, py in points:
            d = math.hypot(px - cx, py - cy)
            if d < 1e-12:
                continue
            w = 1.0 / d
            wx += px * w
            wy += py * w
            wsum += w
        if wsum == 0:
            break
        nx, ny = wx / wsum, wy / wsum
        if math.hypot(nx - cx, ny - cy) < tol:
            cx, cy = nx, ny
            break
        cx, cy = nx, ny
    return (cx, cy)


def _central_feature(
    points: List[Tuple[float, float]],
    center: Tuple[float, float],
) -> Tuple[int, Tuple[float, float]]:
    """Find the data point closest to the given center."""
    best_i, best_d = 0, float("inf")
    for i, (px, py) in enumerate(points):
        d = math.hypot(px - center[0], py - center[1])
        if d < best_d:
            best_d = d
            best_i = i
    return best_i, points[best_i]


def _standard_distance(
    points: List[Tuple[float, float]],
    center: Tuple[float, float],
    weights: Optional[List[float]] = None,
) -> float:
    """Compute standard distance (spatial std deviation from center)."""
    if weights is not None:
        tw = sum(weights)
        if tw == 0:
            weights = None
    if weights is None:
        n = len(points)
        ssq = sum((p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2
                   for p in points)
        return math.sqrt(ssq / n)
    else:
        tw = sum(weights)
        ssq = sum(w * ((p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2)
                   for p, w in zip(points, weights))
        return math.sqrt(ssq / tw)


def _deviational_ellipse(
    points: List[Tuple[float, float]],
    center: Tuple[float, float],
) -> Tuple[float, float, float, float]:
    """Compute standard deviational ellipse.

    Returns (angle_degrees, semi_major, semi_minor, rotation_radians).
    Angle is clockwise from the positive Y-axis (north).
    """
    n = len(points)
    if n < 3:
        return (0.0, 0.0, 0.0, 0.0)

    # Deviations from center
    dx = [p[0] - center[0] for p in points]
    dy = [p[1] - center[1] for p in points]

    sum_dx2 = sum(x * x for x in dx)
    sum_dy2 = sum(y * y for y in dy)
    sum_dxdy = sum(x * y for x, y in zip(dx, dy))

    # Rotation angle (theta) — angle of the major axis
    a = sum_dx2 - sum_dy2
    b = math.sqrt(a * a + 4 * sum_dxdy * sum_dxdy)
    # Two eigenvalues of the covariance-like matrix
    # theta = 0.5 * atan2(2 * sum_dxdy, sum_dx2 - sum_dy2)
    theta = math.atan2(2 * sum_dxdy, a) / 2.0

    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    # Rotate deviations
    rx = [x * cos_t + y * sin_t for x, y in zip(dx, dy)]
    ry = [-x * sin_t + y * cos_t for x, y in zip(dx, dy)]

    sx = math.sqrt(sum(x * x for x in rx) / n)
    sy = math.sqrt(sum(y * y for y in ry) / n)

    # Ensure major >= minor
    if sx >= sy:
        semi_major, semi_minor = sx, sy
    else:
        semi_major, semi_minor = sy, sx
        theta += math.pi / 2

    # Convert to degrees clockwise from north
    angle_deg = math.degrees(theta) % 360

    return (angle_deg, semi_major, semi_minor, theta)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_centers(
    points: List[Tuple[float, float]],
    *,
    weights: Optional[List[float]] = None,
    bounds: Optional[Tuple[float, float, float, float]] = None,
) -> CenterReport:
    """Run full spatial center analysis on *points*.

    Parameters
    ----------
    points : list of (x, y) tuples
        The point dataset.
    weights : list of float, optional
        Per-point weights for weighted mean center and standard distance.
    bounds : (south, north, west, east), optional
        Domain bounds for SVG rendering.

    Returns
    -------
    CenterReport
    """
    if not points:
        return CenterReport()

    report = CenterReport(point_count=len(points), bounds=bounds)

    report.mean_center = _mean_center(points)
    report.median_center = _median_center(points)

    idx, cf = _central_feature(points, report.mean_center)
    report.central_feature = cf
    report.central_feature_index = idx

    report.standard_distance = _standard_distance(points, report.mean_center)

    if weights is not None and len(weights) == len(points):
        report.weighted_mean_center = _weighted_mean_center(points, weights)
        report.standard_distance_weighted = _standard_distance(
            points, report.weighted_mean_center, weights
        )

    angle, smaj, smin, rot = _deviational_ellipse(points, report.mean_center)
    report.ellipse_angle = angle
    report.ellipse_semi_major = smaj
    report.ellipse_semi_minor = smin
    report.ellipse_rotation = rot

    return report
