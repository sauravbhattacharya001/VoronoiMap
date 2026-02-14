"""Voronoi diagram visualization — SVG export for VoronoiMap.

Generates SVG files showing Voronoi regions, seed points, and boundary
vertices.  Works with the existing vormap engine; no heavy dependencies
required (uses only the Python standard library for SVG generation).

When scipy is available, uses ``scipy.spatial.Voronoi`` for precise region
computation.  Falls back to the vormap binary-search tracer otherwise.

Usage (as module):
    import vormap
    import vormap_viz

    points = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(points)
    vormap_viz.export_svg(regions, points, "diagram.svg")

Usage (CLI):
    voronoimap datauni5.txt 5 --visualize output.svg
    voronoimap datauni5.txt 5 --visualize output.svg --color-scheme warm
"""

import colorsys
import math
import random
import xml.etree.ElementTree as ET

import vormap

try:
    import numpy as np
    from scipy.spatial import Voronoi as ScipyVoronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Color schemes ────────────────────────────────────────────────────

_COLOR_SCHEMES = {
    "pastel": lambda i, n: _hsl_color(i / max(n, 1), 0.65, 0.82),
    "warm": lambda i, n: _hsl_color(
        (i / max(n, 1)) * 0.15, 0.70, 0.75       # reds → oranges
    ),
    "cool": lambda i, n: _hsl_color(
        0.5 + (i / max(n, 1)) * 0.2, 0.60, 0.78  # cyans → blues
    ),
    "earth": lambda i, n: _hsl_color(
        0.08 + (i / max(n, 1)) * 0.1, 0.45, 0.65  # browns → greens
    ),
    "mono": lambda i, n: _gray_color(0.88 - 0.35 * (i / max(n, 1))),
    "rainbow": lambda i, n: _hsl_color(i / max(n, 1), 0.70, 0.68),
}

DEFAULT_COLOR_SCHEME = "pastel"


def _hsl_color(h, s, l):
    """Convert HSL (0-1 each) to a CSS hex color string."""
    r, g, b = colorsys.hls_to_rgb(h % 1.0, l, s)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _gray_color(lightness):
    """Generate a gray hex color from a 0-1 lightness value."""
    v = int(max(0, min(255, lightness * 255)))
    return "#%02x%02x%02x" % (v, v, v)


# ── Region computation ───────────────────────────────────────────────

def _clip_infinite_voronoi(vor, bounds):
    """Clip scipy Voronoi regions to a bounding box.

    Returns a dict mapping point index → list of (x, y) vertices.
    Infinite regions are clipped to the bounding box edges.
    """
    s, n, w, e = bounds
    center = np.array(vor.points.mean(axis=0))
    regions = {}

    for point_idx, region_idx in enumerate(vor.point_region):
        region = vor.regions[region_idx]
        if not region:
            continue

        if -1 not in region:
            # Finite region — just grab the vertices
            verts = [tuple(vor.vertices[v]) for v in region]
            regions[point_idx] = verts
            continue

        # Infinite region — need to clip
        finite_verts = []
        for i, v_idx in enumerate(region):
            if v_idx >= 0:
                finite_verts.append(tuple(vor.vertices[v_idx]))
            else:
                # Find the two ridges that bound this infinite edge
                # and project them to the boundary
                prev_idx = region[i - 1]
                next_idx = region[(i + 1) % len(region)]

                # Project from the finite vertex toward infinity
                for ridge_idx, (p1, p2) in enumerate(vor.ridge_points):
                    if point_idx not in (p1, p2):
                        continue
                    ridge_verts = vor.ridge_vertices[ridge_idx]
                    if -1 not in ridge_verts:
                        continue

                    finite_v = [v for v in ridge_verts if v >= 0]
                    if not finite_v:
                        continue

                    fv = vor.vertices[finite_v[0]]
                    # Direction: perpendicular to the ridge points midpoint
                    tangent = vor.points[p2] - vor.points[p1]
                    normal = np.array([-tangent[1], tangent[0]])
                    normal = normal / max(np.linalg.norm(normal), 1e-12)

                    # Ensure it points away from the center
                    midpoint = (vor.points[p1] + vor.points[p2]) / 2
                    if np.dot(normal, midpoint - center) < 0:
                        normal = -normal

                    # Project far enough to hit the boundary
                    far_point = fv + normal * max(e - w, n - s) * 2
                    # Clip to bounds
                    far_point[0] = max(w, min(e, far_point[0]))
                    far_point[1] = max(s, min(n, far_point[1]))
                    finite_verts.append(tuple(far_point))

        if len(finite_verts) >= 3:
            # Sort vertices clockwise around the centroid
            cx = sum(v[0] for v in finite_verts) / len(finite_verts)
            cy = sum(v[1] for v in finite_verts) / len(finite_verts)
            finite_verts.sort(key=lambda v: math.atan2(v[1] - cy, v[0] - cx))
            regions[point_idx] = finite_verts

    return regions


def _compute_regions_scipy(data):
    """Compute Voronoi regions using scipy (precise, handles all cases)."""
    points = np.array(data)
    vor = ScipyVoronoi(points)

    bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)
    idx_regions = _clip_infinite_voronoi(vor, bounds)

    # Re-key by (lng, lat) tuple
    regions = {}
    for idx, verts in idx_regions.items():
        seed = tuple(data[idx])
        regions[seed] = verts

    return regions


def _trace_region(data, dlng, dlat):
    """Trace the Voronoi region boundary for a single seed point.

    Returns a list of (x, y) vertices forming the polygon, or None if
    tracing fails (degenerate configuration).

    This is adapted from find_area() but captures vertex coordinates
    instead of just computing the area.
    """
    try:
        elng, elat = vormap.get_NN(data, dlng, dlat)
        alng, alat = vormap.mid_point(dlng, dlat, elng, elat)
        dirn = vormap.perp_dir(elng, elat, dlng, dlat)
    except (ValueError, RuntimeError):
        return None

    e0g = elng
    e0t = elat

    ag = [alng]
    at = [alat]
    i = 0

    while True:
        try:
            a_g, a_t = vormap.find_a1(data, ag[i], at[i], dlng, dlat, dirn)
            if vormap.get_NN(data, a_g, a_t) != (dlng, dlat):
                return None  # vertex doesn't map back — skip this region
        except (ValueError, RuntimeError):
            return None

        ag.append(a_g)
        at.append(a_t)

        try:
            dirn1 = vormap.new_dir(
                data, ag[i], at[i], ag[i + 1], at[i + 1], dlng, dlat
            )
        except (ValueError, RuntimeError, ZeroDivisionError):
            return None

        if i > 2:
            if dirn == dirn1:
                break

            fin_isect = vormap.isect(
                ag[i + 1], at[i + 1], ag[i], at[i], e0g, e0t, dlng, dlat
            )
            if fin_isect != (-1, -1):
                break

            if i >= vormap.MAX_VERTICES:
                break

        dirn = dirn1
        i += 1

    return list(zip(ag, at))


def _compute_regions_fallback(data):
    """Compute Voronoi regions using the vormap binary-search tracer."""
    regions = {}
    for dlng, dlat in data:
        verts = _trace_region(data, dlng, dlat)
        if verts and len(verts) >= 3:
            regions[(dlng, dlat)] = verts
    return regions


def compute_regions(data):
    """Compute Voronoi region polygons for all seed points.

    When scipy is available, uses ``scipy.spatial.Voronoi`` for precise
    region computation with boundary clipping.  Falls back to the vormap
    binary-search tracer otherwise.

    Parameters
    ----------
    data : list of (float, float)
        Seed points returned by ``vormap.load_data()``.

    Returns
    -------
    dict
        Maps ``(lng, lat)`` seed → list of ``(x, y)`` polygon vertices.
        Points whose regions couldn't be traced are omitted.
    """
    if _HAS_SCIPY and len(data) >= 3:
        return _compute_regions_scipy(data)
    return _compute_regions_fallback(data)


# ── SVG export ───────────────────────────────────────────────────────

def export_svg(
    regions,
    data,
    output_path,
    *,
    width=800,
    height=600,
    margin=40,
    color_scheme=DEFAULT_COLOR_SCHEME,
    show_points=True,
    show_labels=False,
    stroke_color="#444444",
    stroke_width=1.2,
    point_radius=3.5,
    point_color="#222222",
    background="#ffffff",
    title=None,
):
    """Export Voronoi regions as an SVG file.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points (used for point markers).
    output_path : str
        Path to write the SVG file.
    width, height : int
        SVG canvas dimensions in pixels.
    margin : int
        Padding around the diagram.
    color_scheme : str
        One of: pastel, warm, cool, earth, mono, rainbow.
    show_points : bool
        Whether to draw seed point markers.
    show_labels : bool
        Whether to label each region with its seed index.
    stroke_color : str
        CSS color for polygon borders.
    stroke_width : float
        Border thickness.
    point_radius : float
        Radius of seed point markers.
    point_color : str
        CSS color for seed point markers.
    background : str
        CSS color for the SVG background.
    title : str or None
        Optional title text displayed at the top of the diagram.
    """
    if color_scheme not in _COLOR_SCHEMES:
        raise ValueError(
            "Unknown color scheme '%s'. Available: %s"
            % (color_scheme, ", ".join(sorted(_COLOR_SCHEMES)))
        )

    color_fn = _COLOR_SCHEMES[color_scheme]

    # Compute coordinate transform: data space → SVG pixel space
    all_xs = [x for pt in data for x in [pt[0]]]
    all_ys = [y for pt in data for y in [pt[1]]]

    # Include region vertices in bounds calculation
    for verts in regions.values():
        for vx, vy in verts:
            all_xs.append(vx)
            all_ys.append(vy)

    if not all_xs or not all_ys:
        raise ValueError("No data to visualize")

    min_x, max_x = min(all_xs), max(all_xs)
    min_y, max_y = min(all_ys), max(all_ys)

    # Avoid division by zero for degenerate datasets
    range_x = max(max_x - min_x, 1e-6)
    range_y = max(max_y - min_y, 1e-6)

    draw_w = width - 2 * margin
    draw_h = height - 2 * margin

    # Uniform scaling to preserve aspect ratio
    scale = min(draw_w / range_x, draw_h / range_y)

    # Center the diagram
    offset_x = margin + (draw_w - range_x * scale) / 2
    offset_y = margin + (draw_h - range_y * scale) / 2

    def tx(x):
        return offset_x + (x - min_x) * scale

    def ty(y):
        # Flip Y axis so north is up
        return offset_y + (max_y - y) * scale

    # Build SVG
    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    })

    # Add metadata comment
    comment_text = (
        "Generated by VoronoiMap — %d regions from %d seed points"
        % (len(regions), len(data))
    )

    # Style definitions
    style = ET.SubElement(svg, "style")
    style.text = (
        ".region { stroke: %s; stroke-width: %.1f; stroke-linejoin: round; }"
        " .seed { fill: %s; stroke: #fff; stroke-width: 1; }"
        " .label { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 10px; fill: #333; text-anchor: middle;"
        " dominant-baseline: central; pointer-events: none; }"
        " .title { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 16px; font-weight: 600; fill: #222;"
        " text-anchor: middle; }"
        % (stroke_color, stroke_width, point_color)
    )

    # Background
    ET.SubElement(svg, "rect", {
        "width": str(width),
        "height": str(height),
        "fill": background,
    })

    # Title
    if title:
        title_el = ET.SubElement(svg, "text", {
            "x": str(width / 2),
            "y": str(margin / 2 + 4),
            "class": "title",
        })
        title_el.text = title

    # Draw regions
    region_group = ET.SubElement(svg, "g", {"id": "regions"})

    # Sort seeds for deterministic coloring
    sorted_seeds = sorted(regions.keys())

    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        points_str = " ".join(
            "%.2f,%.2f" % (tx(vx), ty(vy)) for vx, vy in verts
        )
        fill = color_fn(idx, len(sorted_seeds))

        poly = ET.SubElement(region_group, "polygon", {
            "points": points_str,
            "fill": fill,
            "class": "region",
        })
        poly.set("data-seed", "%.4f,%.4f" % seed)

    # Draw seed points
    if show_points:
        points_group = ET.SubElement(svg, "g", {"id": "seeds"})
        for px, py in data:
            ET.SubElement(points_group, "circle", {
                "cx": "%.2f" % tx(px),
                "cy": "%.2f" % ty(py),
                "r": str(point_radius),
                "class": "seed",
            })

    # Draw labels
    if show_labels:
        label_group = ET.SubElement(svg, "g", {"id": "labels"})
        for idx, (px, py) in enumerate(data):
            label = ET.SubElement(label_group, "text", {
                "x": "%.2f" % tx(px),
                "y": "%.2f" % ty(py),
                "class": "label",
            })
            label.text = str(idx + 1)

    # Write to file
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)

    return output_path


# ── Convenience: one-call diagram generation ─────────────────────────

def generate_diagram(
    datafile,
    output_path="voronoi.svg",
    **kwargs,
):
    """Load data, compute regions, and export SVG in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str
        Where to write the SVG.
    **kwargs
        Passed through to ``export_svg()``.

    Returns
    -------
    str
        Path to the generated SVG file.
    """
    data = vormap.load_data(datafile)
    regions = compute_regions(data)
    return export_svg(regions, data, output_path, **kwargs)


def list_color_schemes():
    """Return a sorted list of available color scheme names."""
    return sorted(_COLOR_SCHEMES.keys())
