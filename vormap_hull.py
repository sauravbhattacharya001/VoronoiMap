"""Convex hull and bounding geometry analysis for point sets.

Computes the convex hull of seed points plus spatial enclosures:
minimum bounding rectangle (MBR), minimum bounding circle (MBC),
and derived shape metrics.  Useful for understanding the spatial
extent, spread, and compactness of point distributions.

Usage (Python API)::

    import vormap
    from vormap_hull import convex_hull, bounding_rect, bounding_circle

    data = vormap.load_data("datauni5.txt")
    points = [(d["x"], d["y"]) for d in data]

    hull = convex_hull(points)
    print(f"Hull vertices: {hull.n_vertices}")
    print(f"Area: {hull.area:.2f}  Perimeter: {hull.perimeter:.2f}")
    print(f"Compactness: {hull.compactness:.4f}")

    rect = bounding_rect(points)
    print(f"MBR: {rect.width:.1f} x {rect.height:.1f}")
    print(f"Aspect ratio: {rect.aspect_ratio:.2f}")
    print(f"Orientation: {rect.angle_deg:.1f}°")

    circle = bounding_circle(points)
    print(f"MBC: center=({circle.cx:.1f}, {circle.cy:.1f}), "
          f"radius={circle.radius:.1f}")

    # Full analysis report
    from vormap_hull import hull_analysis, format_report
    result = hull_analysis(points)
    print(format_report(result))

CLI::

    voronoimap datauni5.txt 5 --hull
    voronoimap datauni5.txt 5 --hull --hull-json hull.json
    voronoimap datauni5.txt 5 --hull-svg hull.svg
"""

import json
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from vormap import validate_output_path


# ── Helpers ─────────────────────────────────────────────────────────

def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _cross(o: Tuple[float, float], a: Tuple[float, float],
           b: Tuple[float, float]) -> float:
    """Cross product of vectors OA and OB (positive = counter-clockwise)."""
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _polygon_area(vertices: List[Tuple[float, float]]) -> float:
    """Shoelace formula for polygon area (unsigned)."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _polygon_perimeter(vertices: List[Tuple[float, float]]) -> float:
    n = len(vertices)
    if n < 2:
        return 0.0
    total = 0.0
    for i in range(n):
        j = (i + 1) % n
        total += _dist(vertices[i], vertices[j])
    return total


def _polygon_centroid(vertices: List[Tuple[float, float]]
                      ) -> Tuple[float, float]:
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n <= 2:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)
    cx = cy = 0.0
    a = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = (vertices[i][0] * vertices[j][1]
                 - vertices[j][0] * vertices[i][1])
        a += cross
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross
    a /= 2.0
    if abs(a) < 1e-12:
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (cx, cy)
    cx /= (6.0 * a)
    cy /= (6.0 * a)
    return (cx, cy)


# ── Convex Hull (Andrew's monotone chain) ───────────────────────────

@dataclass
class ConvexHullResult:
    """Result of convex hull computation."""

    vertices: List[Tuple[float, float]]
    """Hull vertices in counter-clockwise order."""

    n_vertices: int = 0
    """Number of vertices on the hull."""

    area: float = 0.0
    """Area enclosed by the hull."""

    perimeter: float = 0.0
    """Perimeter (circumference) of the hull."""

    centroid: Tuple[float, float] = (0.0, 0.0)
    """Centroid of the hull polygon."""

    compactness: float = 0.0
    """Isoperimetric quotient: 4π·area / perimeter².
    1.0 for a perfect circle, lower for elongated shapes."""

    diameter: float = 0.0
    """Maximum distance between any two hull vertices (farthest pair)."""

    diameter_pair: Tuple[Tuple[float, float], Tuple[float, float]] = (
        (0.0, 0.0), (0.0, 0.0))
    """The two hull vertices that define the diameter."""

    hull_ratio: float = 0.0
    """Fraction of input points that lie on the hull (n_vertices / n_total)."""

    def to_dict(self) -> dict:
        return {
            "vertices": [{"x": v[0], "y": v[1]} for v in self.vertices],
            "n_vertices": self.n_vertices,
            "area": round(self.area, 4),
            "perimeter": round(self.perimeter, 4),
            "centroid": {"x": round(self.centroid[0], 4),
                         "y": round(self.centroid[1], 4)},
            "compactness": round(self.compactness, 6),
            "diameter": round(self.diameter, 4),
            "diameter_pair": [
                {"x": self.diameter_pair[0][0],
                 "y": self.diameter_pair[0][1]},
                {"x": self.diameter_pair[1][0],
                 "y": self.diameter_pair[1][1]},
            ],
            "hull_ratio": round(self.hull_ratio, 6),
        }


def convex_hull(points: List[Tuple[float, float]]) -> ConvexHullResult:
    """Compute the convex hull of a 2D point set.

    Uses Andrew's monotone chain algorithm — O(n log n).

    Parameters
    ----------
    points : list of (float, float)
        Input point coordinates.

    Returns
    -------
    ConvexHullResult
        Hull vertices (CCW order) and derived metrics.
    """
    n = len(points)
    if n == 0:
        return ConvexHullResult(vertices=[])
    if n == 1:
        return ConvexHullResult(
            vertices=list(points), n_vertices=1,
            centroid=points[0], hull_ratio=1.0,
        )

    sorted_pts = sorted(set(points))

    if len(sorted_pts) == 1:
        return ConvexHullResult(
            vertices=[sorted_pts[0]], n_vertices=1,
            centroid=sorted_pts[0], hull_ratio=1.0,
        )

    # Build lower hull
    lower: List[Tuple[float, float]] = []
    for p in sorted_pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    # Build upper hull
    upper: List[Tuple[float, float]] = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    # Concatenate; last point of each half is omitted (it's the first of next)
    hull = lower[:-1] + upper[:-1]

    if len(hull) == 2:
        # Degenerate: collinear points
        d = _dist(hull[0], hull[1])
        return ConvexHullResult(
            vertices=hull, n_vertices=2,
            perimeter=2.0 * d, diameter=d,
            diameter_pair=(hull[0], hull[1]),
            centroid=((hull[0][0] + hull[1][0]) / 2,
                      (hull[0][1] + hull[1][1]) / 2),
            hull_ratio=2 / n if n > 0 else 1.0,
        )

    area = _polygon_area(hull)
    perimeter = _polygon_perimeter(hull)
    centroid = _polygon_centroid(hull)

    compactness = 0.0
    if perimeter > 0:
        compactness = 4.0 * math.pi * area / (perimeter ** 2)

    # Diameter: farthest pair (rotating calipers for large hulls,
    # brute force is fine for typical point sets)
    diameter = 0.0
    dp = (hull[0], hull[0])
    nh = len(hull)
    for i in range(nh):
        for j in range(i + 1, nh):
            d = _dist(hull[i], hull[j])
            if d > diameter:
                diameter = d
                dp = (hull[i], hull[j])

    return ConvexHullResult(
        vertices=hull,
        n_vertices=len(hull),
        area=area,
        perimeter=perimeter,
        centroid=centroid,
        compactness=compactness,
        diameter=diameter,
        diameter_pair=dp,
        hull_ratio=len(hull) / n if n > 0 else 1.0,
    )


# ── Minimum Bounding Rectangle (rotating calipers) ─────────────────

@dataclass
class BoundingRectResult:
    """Minimum-area bounding rectangle."""

    corners: List[Tuple[float, float]]
    """Four corners of the rectangle in CCW order."""

    width: float = 0.0
    """Shorter side length."""

    height: float = 0.0
    """Longer side length."""

    area: float = 0.0
    """Rectangle area."""

    perimeter: float = 0.0
    """Rectangle perimeter."""

    angle_deg: float = 0.0
    """Rotation angle (degrees from horizontal), in [0, 90)."""

    aspect_ratio: float = 0.0
    """Width / height (always ≤ 1.0)."""

    center: Tuple[float, float] = (0.0, 0.0)
    """Center point of the rectangle."""

    hull_fill_ratio: float = 0.0
    """Hull area / rectangle area (how tightly the hull fills its MBR)."""

    def to_dict(self) -> dict:
        return {
            "corners": [{"x": round(c[0], 4), "y": round(c[1], 4)}
                        for c in self.corners],
            "width": round(self.width, 4),
            "height": round(self.height, 4),
            "area": round(self.area, 4),
            "perimeter": round(self.perimeter, 4),
            "angle_deg": round(self.angle_deg, 4),
            "aspect_ratio": round(self.aspect_ratio, 6),
            "center": {"x": round(self.center[0], 4),
                       "y": round(self.center[1], 4)},
            "hull_fill_ratio": round(self.hull_fill_ratio, 6),
        }


def bounding_rect(points: List[Tuple[float, float]],
                   hull: Optional[ConvexHullResult] = None
                   ) -> BoundingRectResult:
    """Compute the minimum-area bounding rectangle.

    Tries all hull edge orientations (O(n) after hull is built).

    Parameters
    ----------
    points : list of (float, float)
        Input points.
    hull : ConvexHullResult, optional
        Pre-computed hull (avoids recomputation).

    Returns
    -------
    BoundingRectResult
    """
    if not points:
        return BoundingRectResult(corners=[])

    if hull is None:
        hull = convex_hull(points)

    verts = hull.vertices
    if len(verts) <= 1:
        p = verts[0] if verts else (0.0, 0.0)
        return BoundingRectResult(
            corners=[p, p, p, p], center=p,
        )

    if len(verts) == 2:
        a, b = verts
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        d = _dist(a, b)
        return BoundingRectResult(
            corners=[a, b, b, a], width=0.0, height=d,
            area=0.0, perimeter=2.0 * d,
            center=mid, aspect_ratio=0.0,
        )

    best_area = float('inf')
    best_rect = None

    n = len(verts)
    for i in range(n):
        # Edge direction
        j = (i + 1) % n
        ex = verts[j][0] - verts[i][0]
        ey = verts[j][1] - verts[i][1]
        edge_len = math.sqrt(ex * ex + ey * ey)
        if edge_len < 1e-12:
            continue

        # Unit vectors along and perpendicular to edge
        ux, uy = ex / edge_len, ey / edge_len
        vx, vy = -uy, ux

        # Project all hull points
        min_u = max_u = min_v = max_v = None
        for p in verts:
            pu = p[0] * ux + p[1] * uy
            pv = p[0] * vx + p[1] * vy
            if min_u is None:
                min_u = max_u = pu
                min_v = max_v = pv
            else:
                min_u = min(min_u, pu)
                max_u = max(max_u, pu)
                min_v = min(min_v, pv)
                max_v = max(max_v, pv)

        w = max_u - min_u
        h = max_v - min_v
        area = w * h

        if area < best_area:
            best_area = area
            # Reconstruct corners in world space
            c0 = (min_u * ux + min_v * vx, min_u * uy + min_v * vy)
            c1 = (max_u * ux + min_v * vx, max_u * uy + min_v * vy)
            c2 = (max_u * ux + max_v * vx, max_u * uy + max_v * vy)
            c3 = (min_u * ux + max_v * vx, min_u * uy + max_v * vy)

            width = min(w, h)
            height = max(w, h)
            angle = math.degrees(math.atan2(uy, ux)) % 180
            if angle >= 90:
                angle -= 90

            cx = (c0[0] + c2[0]) / 2
            cy = (c0[1] + c2[1]) / 2

            best_rect = BoundingRectResult(
                corners=[c0, c1, c2, c3],
                width=width, height=height,
                area=area,
                perimeter=2.0 * (w + h),
                angle_deg=angle,
                aspect_ratio=width / height if height > 0 else 0.0,
                center=(cx, cy),
                hull_fill_ratio=hull.area / area if area > 0 else 0.0,
            )

    return best_rect or BoundingRectResult(corners=[])


# ── Minimum Bounding Circle (Welzl's algorithm) ────────────────────

@dataclass
class BoundingCircleResult:
    """Minimum enclosing circle."""

    cx: float = 0.0
    """Center x coordinate."""

    cy: float = 0.0
    """Center y coordinate."""

    radius: float = 0.0
    """Circle radius."""

    area: float = 0.0
    """Circle area (π r²)."""

    circumference: float = 0.0
    """Circle circumference (2π r)."""

    hull_fill_ratio: float = 0.0
    """Hull area / circle area."""

    n_boundary: int = 0
    """Number of input points on the circle boundary (2 or 3)."""

    def to_dict(self) -> dict:
        return {
            "center": {"x": round(self.cx, 4), "y": round(self.cy, 4)},
            "radius": round(self.radius, 4),
            "area": round(self.area, 4),
            "circumference": round(self.circumference, 4),
            "hull_fill_ratio": round(self.hull_fill_ratio, 6),
            "n_boundary": self.n_boundary,
        }


def _circle_from_1(p: Tuple[float, float]):
    return (p[0], p[1], 0.0)


def _circle_from_2(a: Tuple[float, float], b: Tuple[float, float]):
    cx = (a[0] + b[0]) / 2
    cy = (a[1] + b[1]) / 2
    r = _dist(a, b) / 2
    return (cx, cy, r)


def _circle_from_3(a: Tuple[float, float], b: Tuple[float, float],
                    c: Tuple[float, float]):
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        # Collinear fallback: use the two farthest points
        dab = _dist(a, b)
        dbc = _dist(b, c)
        dac = _dist(a, c)
        if dab >= dbc and dab >= dac:
            return _circle_from_2(a, b)
        elif dbc >= dab and dbc >= dac:
            return _circle_from_2(b, c)
        else:
            return _circle_from_2(a, c)
    ux = ((ax * ax + ay * ay) * (by - cy)
          + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx)
          + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    r = _dist((ux, uy), a)
    return (ux, uy, r)


def _in_circle(circ, p: Tuple[float, float]) -> bool:
    return _dist((circ[0], circ[1]), p) <= circ[2] + 1e-9


def _welzl(pts: list, boundary: list) -> tuple:
    """Welzl's algorithm (iterative with explicit stack to avoid recursion depth)."""
    import random as _rand

    # Shuffle once at the top level for expected linear time
    pts = list(pts)
    _rand.shuffle(pts)

    def _make_circle(bnd):
        if len(bnd) == 0:
            return (0.0, 0.0, 0.0)
        elif len(bnd) == 1:
            return _circle_from_1(bnd[0])
        elif len(bnd) == 2:
            return _circle_from_2(bnd[0], bnd[1])
        else:
            return _circle_from_3(bnd[0], bnd[1], bnd[2])

    n = len(pts)
    # Explicit stack frames: (index, boundary, circle_so_far)
    # We process points 0..n-1; at each point we either keep the
    # current circle or restart from scratch with the point on boundary.
    circ = _make_circle(boundary)
    for i in range(n):
        if not _in_circle(circ, pts[i]):
            # Restart with pts[i] added to boundary, considering only pts[0..i-1]
            circ = _make_circle(boundary + [pts[i]])
            for j in range(i):
                if not _in_circle(circ, pts[j]):
                    circ = _make_circle(boundary + [pts[i], pts[j]])
                    for k in range(j):
                        if not _in_circle(circ, pts[k]):
                            circ = _circle_from_3(pts[i], pts[j], pts[k])

    return circ


def bounding_circle(points: List[Tuple[float, float]],
                    hull: Optional[ConvexHullResult] = None
                    ) -> BoundingCircleResult:
    """Compute the minimum enclosing circle (MBC).

    Uses Welzl's algorithm on the convex hull vertices (since the MBC
    of a point set equals the MBC of its convex hull).

    Parameters
    ----------
    points : list of (float, float)
        Input points.
    hull : ConvexHullResult, optional
        Pre-computed hull.

    Returns
    -------
    BoundingCircleResult
    """
    if not points:
        return BoundingCircleResult()

    if hull is None:
        hull = convex_hull(points)

    verts = hull.vertices
    if len(verts) == 0:
        return BoundingCircleResult()
    if len(verts) == 1:
        return BoundingCircleResult(
            cx=verts[0][0], cy=verts[0][1],
            n_boundary=1,
        )

    cx, cy, r = _welzl(list(verts), [])

    area = math.pi * r * r
    circ = 2.0 * math.pi * r

    # Count boundary points (within tolerance of radius)
    n_boundary = sum(
        1 for p in verts
        if abs(_dist((cx, cy), p) - r) < 1e-6
    )

    return BoundingCircleResult(
        cx=cx, cy=cy, radius=r,
        area=area, circumference=circ,
        hull_fill_ratio=hull.area / area if area > 0 else 0.0,
        n_boundary=n_boundary,
    )


# ── Combined analysis ──────────────────────────────────────────────

@dataclass
class HullAnalysis:
    """Combined convex hull + bounding geometry analysis."""

    hull: ConvexHullResult
    rect: BoundingRectResult
    circle: BoundingCircleResult

    n_points: int = 0
    """Total number of input points."""

    dispersion: float = 0.0
    """Mean distance from centroid to all points / hull diameter.
    Higher = more dispersed, lower = more concentrated."""

    elongation: float = 0.0
    """1 - (MBR width / MBR height). 0 = square, 1 = very elongated."""

    def to_dict(self) -> dict:
        return {
            "n_points": self.n_points,
            "hull": self.hull.to_dict(),
            "bounding_rectangle": self.rect.to_dict(),
            "bounding_circle": self.circle.to_dict(),
            "dispersion": round(self.dispersion, 6),
            "elongation": round(self.elongation, 6),
        }


def hull_analysis(points: List[Tuple[float, float]]) -> HullAnalysis:
    """Run full convex hull + bounding geometry analysis.

    Parameters
    ----------
    points : list of (float, float)

    Returns
    -------
    HullAnalysis
    """
    hull = convex_hull(points)
    rect = bounding_rect(points, hull=hull)
    circle = bounding_circle(points, hull=hull)

    # Dispersion: mean distance from centroid / diameter
    dispersion = 0.0
    if hull.diameter > 0 and points:
        cx, cy = hull.centroid
        mean_dist = sum(_dist(p, (cx, cy)) for p in points) / len(points)
        dispersion = mean_dist / hull.diameter

    # Elongation from MBR aspect ratio
    elongation = 1.0 - rect.aspect_ratio if rect.aspect_ratio > 0 else 0.0

    return HullAnalysis(
        hull=hull, rect=rect, circle=circle,
        n_points=len(points),
        dispersion=dispersion,
        elongation=elongation,
    )


# ── Text report ─────────────────────────────────────────────────────

def format_report(analysis: HullAnalysis) -> str:
    """Format a human-readable hull analysis report."""
    h = analysis.hull
    r = analysis.rect
    c = analysis.circle

    lines = [
        "Convex Hull & Bounding Geometry Analysis",
        "=" * 45,
        "",
        f"Input points:      {analysis.n_points}",
        "",
        "── Convex Hull ──",
        f"  Vertices:        {h.n_vertices}",
        f"  Area:            {h.area:,.2f}",
        f"  Perimeter:       {h.perimeter:,.2f}",
        f"  Centroid:        ({h.centroid[0]:.2f}, {h.centroid[1]:.2f})",
        f"  Compactness:     {h.compactness:.4f}  (1.0 = circle)",
        f"  Diameter:        {h.diameter:,.2f}",
        f"  Hull ratio:      {h.hull_ratio:.2%}  (points on hull)",
        "",
        "── Minimum Bounding Rectangle ──",
        f"  Dimensions:      {r.width:,.2f} × {r.height:,.2f}",
        f"  Area:            {r.area:,.2f}",
        f"  Aspect ratio:    {r.aspect_ratio:.4f}",
        f"  Orientation:     {r.angle_deg:.1f}°",
        f"  Hull fill:       {r.hull_fill_ratio:.2%}",
        "",
        "── Minimum Bounding Circle ──",
        f"  Center:          ({c.cx:.2f}, {c.cy:.2f})",
        f"  Radius:          {c.radius:,.2f}",
        f"  Area:            {c.area:,.2f}",
        f"  Hull fill:       {c.hull_fill_ratio:.2%}",
        f"  Boundary pts:    {c.n_boundary}",
        "",
        "── Shape Metrics ──",
        f"  Dispersion:      {analysis.dispersion:.4f}  (mean_dist / diameter)",
        f"  Elongation:      {analysis.elongation:.4f}  (0 = square, 1 = line)",
    ]
    return "\n".join(lines)


# ── SVG export ──────────────────────────────────────────────────────

def export_svg(analysis: HullAnalysis,
               points: List[Tuple[float, float]],
               output_path: str,
               *,
               width: int = 800,
               height: int = 600,
               margin: int = 40) -> None:
    """Export convex hull + bounding geometry as an SVG visualization.

    Shows points, convex hull polygon, minimum bounding rectangle,
    minimum bounding circle, centroid, and diameter line.
    """
    output_path = validate_output_path(output_path, allow_absolute=True)
    if not points:
        return

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    data_w = max_x - min_x or 1.0
    data_h = max_y - min_y or 1.0

    draw_w = width - 2 * margin
    draw_h = height - 2 * margin
    scale = min(draw_w / data_w, draw_h / data_h)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        # Flip Y axis (SVG y grows down)
        return height - margin - (y - min_y) * scale

    from xml.etree.ElementTree import Element, SubElement, tostring

    svg = Element('svg', xmlns='http://www.w3.org/2000/svg',
                  width=str(width), height=str(height),
                  viewBox=f'0 0 {width} {height}')

    # Background
    SubElement(svg, 'rect', x='0', y='0',
               width=str(width), height=str(height),
               fill='#f8f9fa')

    # Title
    title = SubElement(svg, 'text', x=str(width // 2), y='20',
                       fill='#333')
    title.set('text-anchor', 'middle')
    title.set('font-size', '14')
    title.set('font-family', 'sans-serif')
    title.text = (f'Convex Hull ({analysis.hull.n_vertices} vertices, '
                  f'area={analysis.hull.area:,.0f})')

    # Minimum bounding circle
    c = analysis.circle
    SubElement(svg, 'circle',
               cx=f'{tx(c.cx):.2f}', cy=f'{ty(c.cy):.2f}',
               r=f'{c.radius * scale:.2f}',
               fill='none', stroke='#e8d44d',
               opacity='0.5')
    SubElement(svg, 'circle',
               cx=f'{tx(c.cx):.2f}', cy=f'{ty(c.cy):.2f}',
               r=f'{c.radius * scale:.2f}',
               fill='#e8d44d', opacity='0.08',
               stroke='none')

    # Minimum bounding rectangle
    r = analysis.rect
    if r.corners:
        pts_str = ' '.join(
            f'{tx(p[0]):.2f},{ty(p[1]):.2f}' for p in r.corners
        )
        SubElement(svg, 'polygon', points=pts_str,
                   fill='#4dabf7', opacity='0.1',
                   stroke='#4dabf7')
        SubElement(svg, 'polygon', points=pts_str,
                   fill='none', stroke='#4dabf7',
                   opacity='0.6')

    # Convex hull polygon
    h = analysis.hull
    if len(h.vertices) >= 3:
        pts_str = ' '.join(
            f'{tx(v[0]):.2f},{ty(v[1]):.2f}' for v in h.vertices
        )
        SubElement(svg, 'polygon', points=pts_str,
                   fill='#51cf66', opacity='0.15',
                   stroke='#2b8a3e')
        SubElement(svg, 'polygon', points=pts_str,
                   fill='none', stroke='#2b8a3e',
                   opacity='0.8')

    # Diameter line
    dp = h.diameter_pair
    SubElement(svg, 'line',
               x1=f'{tx(dp[0][0]):.2f}', y1=f'{ty(dp[0][1]):.2f}',
               x2=f'{tx(dp[1][0]):.2f}', y2=f'{ty(dp[1][1]):.2f}',
               stroke='#e03131', opacity='0.7')
    SubElement(svg, 'line',
               x1=f'{tx(dp[0][0]):.2f}', y1=f'{ty(dp[0][1]):.2f}',
               x2=f'{tx(dp[1][0]):.2f}', y2=f'{ty(dp[1][1]):.2f}',
               stroke='#e03131', opacity='0.7')

    # Points
    for p in points:
        SubElement(svg, 'circle',
                   cx=f'{tx(p[0]):.2f}', cy=f'{ty(p[1]):.2f}',
                   r='3', fill='#495057')

    # Hull vertices (highlighted)
    for v in h.vertices:
        SubElement(svg, 'circle',
                   cx=f'{tx(v[0]):.2f}', cy=f'{ty(v[1]):.2f}',
                   r='5', fill='#2b8a3e', opacity='0.7')

    # Centroid marker
    SubElement(svg, 'circle',
               cx=f'{tx(h.centroid[0]):.2f}', cy=f'{ty(h.centroid[1]):.2f}',
               r='6', fill='none', stroke='#e03131')
    SubElement(svg, 'line',
               x1=f'{tx(h.centroid[0]) - 4:.2f}',
               y1=f'{ty(h.centroid[1]):.2f}',
               x2=f'{tx(h.centroid[0]) + 4:.2f}',
               y2=f'{ty(h.centroid[1]):.2f}',
               stroke='#e03131')
    SubElement(svg, 'line',
               x1=f'{tx(h.centroid[0]):.2f}',
               y1=f'{ty(h.centroid[1]) - 4:.2f}',
               x2=f'{tx(h.centroid[0]):.2f}',
               y2=f'{ty(h.centroid[1]) + 4:.2f}',
               stroke='#e03131')

    # Legend
    legend_y = height - 15
    items = [
        ('#2b8a3e', 'Convex Hull'),
        ('#4dabf7', 'Min Bounding Rect'),
        ('#e8d44d', 'Min Bounding Circle'),
        ('#e03131', 'Diameter / Centroid'),
    ]
    lx = 15
    for color, label in items:
        SubElement(svg, 'rect', x=str(lx), y=str(legend_y - 8),
                   width='12', height='12', fill=color, opacity='0.7')
        t = SubElement(svg, 'text', x=str(lx + 16), y=str(legend_y + 2),
                       fill='#333')
        t.set('font-size', '11')
        t.set('font-family', 'sans-serif')
        t.text = label
        lx += len(label) * 7 + 30

    raw = tostring(svg, encoding='unicode')
    with open(output_path, 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(raw)


# ── JSON export ─────────────────────────────────────────────────────

def export_json(analysis: HullAnalysis, output_path: str) -> None:
    """Export hull analysis results as JSON."""
    output_path = validate_output_path(output_path, allow_absolute=True)
    with open(output_path, 'w') as f:
        json.dump(analysis.to_dict(), f, indent=2)
