"""Transect Profiler — cross-section analysis along a line through Voronoi diagrams.

Draw a transect line (or multi-segment path) across a Voronoi diagram and
analyse the regions it crosses: crossing distances, region properties, density
gradients, and transition points.  Useful for geological transects, urban
corridor studies, ecological surveys, and spatial trend analysis.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_transect import (
        create_transect, analyse_transect,
        export_transect_svg, export_transect_json,
        export_transect_csv,
    )

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    # Simple A→B transect
    transect = create_transect((100, 200), (1800, 700))
    result = analyse_transect(transect, regions, data, stats)
    print(result.summary())

    # Multi-segment path
    path_transect = create_transect((100, 200), (900, 500), (1800, 700))
    result = analyse_transect(path_transect, regions, data, stats)

    export_transect_svg(result, regions, data, "transect.svg")
    export_transect_json(result, "transect.json")
    export_transect_csv(result, "transect.csv")

CLI::

    voronoimap datauni5.txt 5 --transect 100,200,1800,700
    voronoimap datauni5.txt 5 --transect 100,200,900,500,1800,700
    voronoimap datauni5.txt 5 --transect 100,200,1800,700 --transect-svg transect.svg
    voronoimap datauni5.txt 5 --transect 100,200,1800,700 --transect-json transect.json
    voronoimap datauni5.txt 5 --transect 100,200,1800,700 --transect-csv transect.csv
"""

import csv
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from vormap import eudist_pts, validate_output_path
from vormap_utils import point_in_polygon as _point_in_polygon


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class TransectPoint:
    """A single waypoint in the transect path."""
    x: float
    y: float


@dataclass
class Transect:
    """A transect path defined by two or more waypoints."""
    waypoints: list  # list of TransectPoint

    @property
    def total_length(self):
        """Total path length across all segments."""
        length = 0.0
        for i in range(len(self.waypoints) - 1):
            a, b = self.waypoints[i], self.waypoints[i + 1]
            length += math.hypot(b.x - a.x, b.y - a.y)
        return length

    @property
    def segment_count(self):
        return max(0, len(self.waypoints) - 1)


@dataclass
class TransectCrossing:
    """A single region crossing along the transect."""
    region_index: int          # 1-based index from stats
    seed: tuple                # (x, y) of the region seed
    entry_point: tuple         # (x, y) where transect enters region
    exit_point: tuple          # (x, y) where transect exits region
    entry_distance: float      # cumulative distance at entry
    exit_distance: float       # cumulative distance at exit
    crossing_length: float     # distance traversed within this region
    area: float                # region area
    density: float             # 1 / area (points per unit area)
    compactness: float         # isoperimetric quotient
    centroid_distance: float   # distance from transect midpoint to region centroid


@dataclass
class TransectResult:
    """Full analysis result for a transect."""
    transect: Transect
    crossings: list            # list of TransectCrossing
    total_length: float
    regions_crossed: int
    mean_crossing_length: float
    density_range: tuple       # (min_density, max_density)
    density_gradient: float    # slope of density along transect (linear fit)
    dominant_region_index: int  # region with longest crossing

    def summary(self):
        """Human-readable summary string."""
        lines = [
            "Transect Analysis Summary",
            "=" * 40,
            "Path length:          %.2f" % self.total_length,
            "Segments:             %d" % self.transect.segment_count,
            "Regions crossed:      %d" % self.regions_crossed,
            "Mean crossing length: %.2f" % self.mean_crossing_length,
            "Density range:        %.6f – %.6f" % self.density_range,
            "Density gradient:     %.8f per unit" % self.density_gradient,
            "Dominant region:      #%d" % self.dominant_region_index,
            "",
            "Crossings:",
            "-" * 40,
        ]
        for c in self.crossings:
            lines.append(
                "  Region #%d: %.2f units (%.1f%% of transect), "
                "density=%.6f"
                % (
                    c.region_index,
                    c.crossing_length,
                    100.0 * c.crossing_length / self.total_length
                    if self.total_length > 0 else 0,
                    c.density,
                )
            )
        return "\n".join(lines)


# ── Geometry helpers ─────────────────────────────────────────────────

def _segments_intersect(p1, p2, p3, p4):
    """Find intersection point of segments (p1→p2) and (p3→p4).

    Returns (x, y, t) where t is the parameter along p1→p2 [0,1],
    or None if segments don't intersect.
    """
    dx1 = p2[0] - p1[0]
    dy1 = p2[1] - p1[1]
    dx2 = p4[0] - p3[0]
    dy2 = p4[1] - p3[1]

    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-12:
        return None  # parallel or coincident

    dx3 = p3[0] - p1[0]
    dy3 = p3[1] - p1[1]

    t = (dx3 * dy2 - dy3 * dx2) / denom
    u = (dx3 * dy1 - dy3 * dx1) / denom

    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        ix = p1[0] + t * dx1
        iy = p1[1] + t * dy1
        return (ix, iy, t)
    return None



def _find_region_for_point(px, py, regions):
    """Find which region contains a point, return seed or None."""
    # First try nearest-seed heuristic (fast for convex Voronoi cells)
    best_seed = None
    best_dist = float("inf")
    for seed in regions:
        d = (seed[0] - px) ** 2 + (seed[1] - py) ** 2
        if d < best_dist:
            best_dist = d
            best_seed = seed

    if best_seed and _point_in_polygon(px, py, regions[best_seed]):
        return best_seed

    # Fallback: test all regions
    for seed, verts in regions.items():
        if _point_in_polygon(px, py, verts):
            return seed
    return best_seed  # nearest seed as last resort


def _transect_region_intersections(p1, p2, polygon):
    """Find all intersection points of segment p1→p2 with polygon edges.

    Returns sorted list of (t, x, y) where t ∈ [0,1] is the parameter
    along p1→p2.
    """
    intersections = []
    n = len(polygon)
    for i in range(n):
        p3 = polygon[i]
        p4 = polygon[(i + 1) % n]
        result = _segments_intersect(p1, p2, p3, p4)
        if result:
            ix, iy, t = result
            # Deduplicate very close intersections
            is_dup = False
            for existing in intersections:
                if abs(existing[0] - t) < 1e-9:
                    is_dup = True
                    break
            if not is_dup:
                intersections.append((t, ix, iy))
    intersections.sort()
    return intersections


# ── Core functions ───────────────────────────────────────────────────

def create_transect(*points):
    """Create a transect from coordinate pairs or tuples.

    Parameters
    ----------
    *points : tuple of (float, float)
        Two or more (x, y) waypoints defining the transect path.

    Returns
    -------
    Transect

    Raises
    ------
    ValueError
        If fewer than 2 points are provided.

    Examples
    --------
    >>> t = create_transect((100, 200), (1800, 700))
    >>> t = create_transect((0, 0), (500, 500), (1000, 0))
    """
    if len(points) < 2:
        raise ValueError("Transect requires at least 2 waypoints, got %d" % len(points))
    waypoints = []
    for p in points:
        if len(p) != 2:
            raise ValueError("Each waypoint must be (x, y), got %r" % (p,))
        x, y = float(p[0]), float(p[1])
        if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
            raise ValueError("Waypoint coordinates must be finite, got (%s, %s)" % (x, y))
        waypoints.append(TransectPoint(x, y))
    return Transect(waypoints=waypoints)


def analyse_transect(transect, regions, data, stats=None):
    """Analyse a transect across a Voronoi diagram.

    Walks along the transect path segment by segment, detecting which
    regions are crossed and computing per-crossing metrics.

    Parameters
    ----------
    transect : Transect
        The transect path (from ``create_transect``).
    regions : dict
        Output of ``vormap_viz.compute_regions()``.
    data : list of (float, float)
        Seed points.
    stats : list of dict or None
        Output of ``vormap_viz.compute_region_stats()``.  If None,
        basic area stats are computed inline.

    Returns
    -------
    TransectResult
    """
    # Build lookup: seed → stats dict
    stats_lookup = {}
    if stats:
        for s in stats:
            key = (s["seed_x"], s["seed_y"])
            stats_lookup[key] = s

    # Build seed → index lookup
    data_index = {}
    for i, pt in enumerate(data):
        data_index[tuple(pt)] = i + 1  # 1-based

    crossings = []
    cumulative_dist = 0.0

    for seg_idx in range(transect.segment_count):
        wp_a = transect.waypoints[seg_idx]
        wp_b = transect.waypoints[seg_idx + 1]
        p1 = (wp_a.x, wp_a.y)
        p2 = (wp_b.x, wp_b.y)
        seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if seg_len < 1e-12:
            continue

        # Collect all intersection points with all region polygons
        all_transitions = []  # (t_global, x, y)
        all_transitions.append((0.0, p1[0], p1[1]))
        all_transitions.append((1.0, p2[0], p2[1]))

        for seed, verts in regions.items():
            for t, ix, iy in _transect_region_intersections(p1, p2, verts):
                all_transitions.append((t, ix, iy))

        # Sort by parameter t
        all_transitions.sort()

        # Deduplicate close transitions
        deduped = [all_transitions[0]]
        for tr in all_transitions[1:]:
            if abs(tr[0] - deduped[-1][0]) > 1e-9:
                deduped.append(tr)
        all_transitions = deduped

        # Walk between consecutive transition points
        for i in range(len(all_transitions) - 1):
            t1, x1, y1 = all_transitions[i]
            t2, x2, y2 = all_transitions[i + 1]

            mid_x = (x1 + x2) / 2.0
            mid_y = (y1 + y2) / 2.0

            seed = _find_region_for_point(mid_x, mid_y, regions)
            if seed is None:
                continue

            crossing_len = math.hypot(x2 - x1, y2 - y1)
            if crossing_len < 1e-10:
                continue

            entry_dist = cumulative_dist + t1 * seg_len
            exit_dist = cumulative_dist + t2 * seg_len

            region_idx = data_index.get(tuple(seed), 0)
            st = stats_lookup.get(tuple(seed), {})
            area = st.get("area", 0.0)
            compactness = st.get("compactness", 0.0)
            cx = st.get("centroid_x", seed[0])
            cy = st.get("centroid_y", seed[1])

            if area <= 0:
                # Compute area inline from vertices
                verts = regions[seed]
                a = 0.0
                nv = len(verts)
                for vi in range(nv):
                    vx1, vy1 = verts[vi]
                    vx2, vy2 = verts[(vi + 1) % nv]
                    a += vx1 * vy2 - vx2 * vy1
                area = abs(a) / 2.0

            density = 1.0 / area if area > 0 else 0.0
            centroid_mid_x = (x1 + x2) / 2.0
            centroid_mid_y = (y1 + y2) / 2.0
            centroid_dist = math.hypot(cx - centroid_mid_x, cy - centroid_mid_y)

            # Merge with previous crossing if same region
            if crossings and crossings[-1].seed == tuple(seed):
                prev = crossings[-1]
                crossings[-1] = TransectCrossing(
                    region_index=prev.region_index,
                    seed=prev.seed,
                    entry_point=prev.entry_point,
                    exit_point=(x2, y2),
                    entry_distance=prev.entry_distance,
                    exit_distance=exit_dist,
                    crossing_length=prev.crossing_length + crossing_len,
                    area=prev.area,
                    density=prev.density,
                    compactness=prev.compactness,
                    centroid_distance=centroid_dist,
                )
            else:
                crossings.append(TransectCrossing(
                    region_index=region_idx,
                    seed=tuple(seed),
                    entry_point=(x1, y1),
                    exit_point=(x2, y2),
                    entry_distance=entry_dist,
                    exit_distance=exit_dist,
                    crossing_length=crossing_len,
                    area=area,
                    density=density,
                    compactness=compactness,
                    centroid_distance=centroid_dist,
                ))

        cumulative_dist += seg_len

    # Aggregate stats
    total_length = transect.total_length
    n_crossed = len(crossings)
    mean_crossing = (
        sum(c.crossing_length for c in crossings) / n_crossed
        if n_crossed > 0 else 0.0
    )

    densities = [c.density for c in crossings] if crossings else [0.0]
    density_range = (min(densities), max(densities))

    # Linear density gradient (slope of density vs distance along transect)
    density_gradient = _compute_density_gradient(crossings)

    dominant_idx = 0
    if crossings:
        dominant = max(crossings, key=lambda c: c.crossing_length)
        dominant_idx = dominant.region_index

    return TransectResult(
        transect=transect,
        crossings=crossings,
        total_length=total_length,
        regions_crossed=n_crossed,
        mean_crossing_length=round(mean_crossing, 4),
        density_range=(round(density_range[0], 8), round(density_range[1], 8)),
        density_gradient=round(density_gradient, 10),
        dominant_region_index=dominant_idx,
    )


def _compute_density_gradient(crossings):
    """Fit a linear slope to density vs cumulative distance."""
    if len(crossings) < 2:
        return 0.0
    n = len(crossings)
    xs = [(c.entry_distance + c.exit_distance) / 2.0 for c in crossings]
    ys = [c.density for c in crossings]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if abs(den) < 1e-15:
        return 0.0
    return num / den


# ── Export: SVG ──────────────────────────────────────────────────────

def export_transect_svg(result, regions, data, output_path,
                        *, width=900, height=400, margin=40):
    """Export a transect profile as an SVG cross-section diagram.

    Renders a 1-D strip showing each crossed region as a coloured band
    proportional to crossing length, with density encoded as colour
    intensity.

    Parameters
    ----------
    result : TransectResult
    regions : dict
    data : list
    output_path : str
    width, height : int
    margin : int
    """
    safe_path = validate_output_path(output_path, allow_absolute=True)

    plot_w = width - 2 * margin
    plot_h = height - 2 * margin
    total_len = result.total_length

    if total_len <= 0 or not result.crossings:
        # Empty diagram
        svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                         width=str(width), height=str(height))
        ET.SubElement(svg, "rect", width=str(width), height=str(height),
                      fill="#ffffff")
        txt = ET.SubElement(svg, "text", x=str(width // 2), y=str(height // 2))
        txt.set("text-anchor", "middle")
        txt.set("font-family", "sans-serif")
        txt.set("font-size", "14")
        txt.text = "No crossings found"
        tree = ET.ElementTree(svg)
        tree.write(safe_path, xml_declaration=True, encoding="unicode")
        return

    # Colour scale: density → blue intensity
    min_d = result.density_range[0]
    max_d = result.density_range[1]
    d_span = max_d - min_d if max_d > min_d else 1.0

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height))
    ET.SubElement(svg, "rect", width=str(width), height=str(height),
                  fill="#ffffff")

    # Title
    title_el = ET.SubElement(svg, "text", x=str(width // 2), y="20")
    title_el.set("text-anchor", "middle")
    title_el.set("font-family", "sans-serif")
    title_el.set("font-size", "14")
    title_el.set("font-weight", "bold")
    title_el.text = "Transect Profile — %d regions, %.1f units" % (
        result.regions_crossed, total_len)

    # Draw bands
    for crossing in result.crossings:
        x_start = margin + (crossing.entry_distance / total_len) * plot_w
        band_w = (crossing.crossing_length / total_len) * plot_w
        if band_w < 0.5:
            band_w = 0.5

        # Colour: intensity by density
        norm = (crossing.density - min_d) / d_span if d_span > 0 else 0.5
        r = int(60 + (1 - norm) * 160)
        g = int(100 + (1 - norm) * 120)
        b = int(180 + norm * 75)
        fill = "#%02x%02x%02x" % (r, g, b)

        rect = ET.SubElement(svg, "rect",
                             x="%.2f" % x_start,
                             y=str(margin),
                             width="%.2f" % band_w,
                             height=str(plot_h))
        rect.set("fill", fill)
        rect.set("stroke", "#333333")
        rect.set("stroke-width", "0.5")

        # Label if band wide enough
        if band_w > 30:
            lbl = ET.SubElement(svg, "text",
                                x="%.2f" % (x_start + band_w / 2),
                                y=str(margin + plot_h // 2))
            lbl.set("text-anchor", "middle")
            lbl.set("dominant-baseline", "middle")
            lbl.set("font-family", "sans-serif")
            lbl.set("font-size", "10")
            lbl.set("fill", "#ffffff" if norm > 0.5 else "#222222")
            lbl.text = "#%d" % crossing.region_index

    # Distance axis
    for i in range(6):
        frac = i / 5.0
        x = margin + frac * plot_w
        d = frac * total_len
        tick = ET.SubElement(svg, "line",
                             x1="%.1f" % x, y1=str(margin + plot_h),
                             x2="%.1f" % x, y2=str(margin + plot_h + 6))
        tick.set("stroke", "#333")
        tick.set("stroke-width", "1")
        lbl = ET.SubElement(svg, "text",
                            x="%.1f" % x,
                            y=str(margin + plot_h + 18))
        lbl.set("text-anchor", "middle")
        lbl.set("font-family", "sans-serif")
        lbl.set("font-size", "10")
        lbl.text = "%.0f" % d

    # Legend
    legend_y = height - 12
    leg = ET.SubElement(svg, "text", x=str(margin), y=str(legend_y))
    leg.set("font-family", "sans-serif")
    leg.set("font-size", "9")
    leg.set("fill", "#666666")
    leg.text = "Colour = density (darker = denser) | Distance along transect →"

    tree = ET.ElementTree(svg)
    tree.write(safe_path, xml_declaration=True, encoding="unicode")


# ── Export: JSON ─────────────────────────────────────────────────────

def export_transect_json(result, output_path):
    """Export transect analysis to JSON.

    Parameters
    ----------
    result : TransectResult
    output_path : str
    """
    safe_path = validate_output_path(output_path, allow_absolute=True)

    obj = {
        "transect": {
            "waypoints": [
                {"x": wp.x, "y": wp.y} for wp in result.transect.waypoints
            ],
            "total_length": round(result.total_length, 4),
            "segments": result.transect.segment_count,
        },
        "summary": {
            "regions_crossed": result.regions_crossed,
            "mean_crossing_length": result.mean_crossing_length,
            "density_range": list(result.density_range),
            "density_gradient": result.density_gradient,
            "dominant_region": result.dominant_region_index,
        },
        "crossings": [
            {
                "region_index": c.region_index,
                "seed": list(c.seed),
                "entry_point": list(c.entry_point),
                "exit_point": list(c.exit_point),
                "entry_distance": round(c.entry_distance, 4),
                "exit_distance": round(c.exit_distance, 4),
                "crossing_length": round(c.crossing_length, 4),
                "area": round(c.area, 4),
                "density": round(c.density, 8),
                "compactness": round(c.compactness, 4),
                "centroid_distance": round(c.centroid_distance, 4),
            }
            for c in result.crossings
        ],
    }

    with open(safe_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)


# ── Export: CSV ──────────────────────────────────────────────────────

def export_transect_csv(result, output_path):
    """Export transect crossings to CSV.

    Parameters
    ----------
    result : TransectResult
    output_path : str
    """
    safe_path = validate_output_path(output_path, allow_absolute=True)

    fieldnames = [
        "region_index", "seed_x", "seed_y",
        "entry_x", "entry_y", "exit_x", "exit_y",
        "entry_distance", "exit_distance", "crossing_length",
        "area", "density", "compactness", "centroid_distance",
    ]

    with open(safe_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in result.crossings:
            writer.writerow({
                "region_index": c.region_index,
                "seed_x": c.seed[0],
                "seed_y": c.seed[1],
                "entry_x": c.entry_point[0],
                "entry_y": c.entry_point[1],
                "exit_x": c.exit_point[0],
                "exit_y": c.exit_point[1],
                "entry_distance": round(c.entry_distance, 4),
                "exit_distance": round(c.exit_distance, 4),
                "crossing_length": round(c.crossing_length, 4),
                "area": round(c.area, 4),
                "density": round(c.density, 8),
                "compactness": round(c.compactness, 4),
                "centroid_distance": round(c.centroid_distance, 4),
            })
