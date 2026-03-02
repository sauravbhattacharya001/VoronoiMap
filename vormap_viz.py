"""Voronoi diagram visualization — SVG, HTML & GeoJSON export for VoronoiMap.

Generates SVG files showing Voronoi regions, seed points, and boundary
vertices.  Works with the existing vormap engine; no heavy dependencies
required (uses only the Python standard library for SVG generation).

GeoJSON export produces a standard FeatureCollection that can be loaded
directly into GIS tools (QGIS, Mapbox, Leaflet, Google Earth, ArcGIS)
or consumed by any geospatial pipeline.

When scipy is available, uses ``scipy.spatial.Voronoi`` for precise region
computation.  Falls back to the vormap binary-search tracer otherwise.

Lloyd relaxation iteratively moves seed points to their Voronoi cell
centroids, producing increasingly uniform tessellations — useful for
mesh optimization, procedural generation, and spatial analysis.

Usage (as module):
    import vormap
    import vormap_viz

    points = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(points)
    vormap_viz.export_svg(regions, points, "diagram.svg")
    vormap_viz.export_geojson(regions, points, "diagram.geojson")

    # Lloyd relaxation for uniform regions
    result = vormap_viz.lloyd_relaxation(points, iterations=20)
    vormap_viz.export_svg(result["regions"], result["points"], "relaxed.svg")

Usage (CLI):
    voronoimap datauni5.txt 5 --visualize output.svg
    voronoimap datauni5.txt 5 --visualize output.svg --color-scheme warm
    voronoimap datauni5.txt 5 --geojson output.geojson
"""

import colorsys
import html as _html_mod
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


# ── Common helpers ───────────────────────────────────────────────────


def _build_data_index(data):
    """Build a dictionary mapping seed (tuple) → index for O(1) lookups.

    Only records the *first* occurrence of each seed coordinate, matching
    the semantics of the previous ``list.index()`` approach.  Building
    the dict is O(n); each subsequent lookup is O(1).  Functions that
    previously called ``_data_index()`` in a loop (O(n) per call → O(n²)
    total) now call this once and use dict.get() for O(n) total.
    """
    lookup = {}
    for i, pt in enumerate(data):
        key = tuple(pt)
        if key not in lookup:
            lookup[key] = i
    return lookup


def _data_index(seed, data, fallback):
    """Find the index of *seed* in *data*, returning *fallback* on miss.

    This pattern was duplicated in export_html, export_geojson, and
    compute_region_stats.  Centralising it avoids repeated try/except
    blocks and keeps seed-lookup semantics consistent.

    .. note::
        For batch lookups, prefer :func:`_build_data_index` + ``dict.get()``
        to avoid O(n) per call.
    """
    try:
        return data.index(seed)
    except ValueError:
        return fallback


class _CoordinateTransform:
    """Map data-space coordinates to pixel-space for SVG/canvas rendering.

    Computes uniform (aspect-preserving) scaling from data bounds to a
    ``(width × height)`` canvas with *margin* pixels of padding.  The
    diagram is centred within the available space and the Y-axis is
    flipped so that *north is up*.

    Previously this logic lived inline in ``export_svg``.  Extracting
    it makes the transform reusable for any future pixel-based export
    and keeps ``export_svg`` focused on SVG construction.
    """

    __slots__ = ("_min_x", "_max_y", "_scale", "_offset_x", "_offset_y")

    def __init__(self, regions, data, *, width, height, margin):
        all_xs = [pt[0] for pt in data]
        all_ys = [pt[1] for pt in data]

        for verts in regions.values():
            for vx, vy in verts:
                all_xs.append(vx)
                all_ys.append(vy)

        if not all_xs or not all_ys:
            raise ValueError("No data to visualize")

        min_x, max_x = min(all_xs), max(all_xs)
        min_y, max_y = min(all_ys), max(all_ys)

        range_x = max(max_x - min_x, 1e-6)
        range_y = max(max_y - min_y, 1e-6)

        draw_w = width - 2 * margin
        draw_h = height - 2 * margin

        scale = min(draw_w / range_x, draw_h / range_y)

        self._min_x = min_x
        self._max_y = max_y
        self._scale = scale
        self._offset_x = margin + (draw_w - range_x * scale) / 2
        self._offset_y = margin + (draw_h - range_y * scale) / 2

    def tx(self, x):
        """Transform a data-space X coordinate to pixel-space."""
        return self._offset_x + (x - self._min_x) * self._scale

    def ty(self, y):
        """Transform a data-space Y coordinate to pixel-space (Y-flipped)."""
        return self._offset_y + (self._max_y - y) * self._scale


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
    binary-search tracer otherwise (including when scipy fails on
    degenerate configurations like collinear points).

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
        try:
            return _compute_regions_scipy(data)
        except Exception:
            # Fall back to vormap tracer on degenerate configurations
            # (e.g. collinear points cause QhullError)
            pass
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

    # Build coordinate transform
    transform = _CoordinateTransform(
        regions, data, width=width, height=height, margin=margin,
    )
    tx = transform.tx
    ty = transform.ty

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
        % (vormap.sanitize_css_value(stroke_color), stroke_width,
           vormap.sanitize_css_value(point_color))
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


# ── Interactive HTML export ──────────────────────────────────────────

def _compute_region_area(vertices):
    """Compute polygon area using the Shoelace formula."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def export_html(
    regions,
    data,
    output_path,
    *,
    width=960,
    height=700,
    color_scheme=DEFAULT_COLOR_SCHEME,
    title=None,
):
    """Export an interactive HTML visualization of Voronoi regions.

    The generated HTML file is self-contained (zero external dependencies)
    and supports:

    - **Pan & zoom** via mouse wheel / drag (Canvas 2D transforms)
    - **Hover tooltips** showing region index, seed coordinates, area,
      and vertex count
    - **Live color scheme switching** between all 6 built-in schemes
    - **Region highlighting** on hover
    - **Zoom controls** (+/−/reset buttons)
    - **Dark/light theme toggle**
    - **Responsive layout** that fills the browser window

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    output_path : str
        Path to write the HTML file.
    width, height : int
        Initial canvas dimensions (resizes to fit window).
    color_scheme : str
        Initial color scheme (pastel, warm, cool, earth, mono, rainbow).
    title : str or None
        Optional title displayed at the top.

    Returns
    -------
    str
        Path to the generated HTML file.
    """
    if not regions:
        raise ValueError("No regions to visualize")

    # Prepare region data as JSON-serializable structures
    sorted_seeds = sorted(regions.keys())
    data_lookup = _build_data_index(data)
    region_list = []
    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        area = _compute_region_area(verts)
        data_idx = data_lookup.get(tuple(seed), idx)

        region_list.append({
            "seed": list(seed),
            "vertices": [list(v) for v in verts],
            "area": round(area, 2),
            "vertexCount": len(verts),
            "dataIndex": data_idx,
        })

    import json
    regions_json = json.dumps(region_list)
    points_json = json.dumps([list(p) for p in data])
    title_text = title or ("Voronoi Diagram — %d regions" % len(regions))

    html = _INTERACTIVE_HTML_TEMPLATE.replace("{{REGIONS_JSON}}", regions_json)
    html = html.replace("{{POINTS_JSON}}", points_json)
    html = html.replace("{{TITLE}}", _html_mod.escape(title_text))
    html = html.replace("{{INITIAL_SCHEME}}", _html_mod.escape(color_scheme))
    html = html.replace("{{WIDTH}}", str(width))
    html = html.replace("{{HEIGHT}}", str(height))

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


# ── GeoJSON export ───────────────────────────────────────────────────

def export_geojson(
    regions,
    data,
    output_path,
    *,
    include_seeds=True,
    properties_fn=None,
    crs_name=None,
):
    """Export Voronoi regions as a GeoJSON FeatureCollection.

    Produces a standard GeoJSON file that can be loaded into GIS tools
    like QGIS, Mapbox, Leaflet, Google Earth, ArcGIS, or consumed by
    any geospatial pipeline.

    Each Voronoi region is a Feature with geometry type ``Polygon``.
    Seed points can optionally be included as ``Point`` features.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    output_path : str
        Path to write the GeoJSON file.
    include_seeds : bool
        If True (default), include seed points as separate Point features
        in addition to the region polygons.
    properties_fn : callable or None
        Optional function ``(seed, vertices, data_index) → dict`` that
        returns extra properties to attach to each region Feature.
        The returned dict is merged into the default properties.
    crs_name : str or None
        Optional CRS identifier (e.g. ``"urn:ogc:def:crs:EPSG::4326"``).
        If provided, a top-level ``"crs"`` object is included (GeoJSON
        2008 style).  Omitted by default per RFC 7946 (assumes WGS84).

    Returns
    -------
    str
        Path to the generated GeoJSON file.

    Raises
    ------
    ValueError
        If *regions* is empty.

    Examples
    --------
    >>> import vormap, vormap_viz
    >>> data = vormap.load_data("datauni5.txt")
    >>> regions = vormap_viz.compute_regions(data)
    >>> vormap_viz.export_geojson(regions, data, "voronoi.geojson")
    'voronoi.geojson'

    With custom properties:

    >>> def add_label(seed, verts, idx):
    ...     return {"label": f"Region {idx + 1}"}
    >>> vormap_viz.export_geojson(regions, data, "labeled.geojson",
    ...                           properties_fn=add_label)
    """
    import json

    if not regions:
        raise ValueError("No regions to export")

    sorted_seeds = sorted(regions.keys())
    data_lookup = _build_data_index(data)
    features = []

    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        data_idx = data_lookup.get(tuple(seed), idx)

        area = _compute_region_area(verts)

        # GeoJSON Polygon: exterior ring must be closed (first == last)
        ring = [[v[0], v[1]] for v in verts]
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])

        properties = {
            "region_index": data_idx + 1,
            "seed_x": seed[0],
            "seed_y": seed[1],
            "area": round(area, 4),
            "vertex_count": len(verts),
        }

        # Merge user-supplied properties
        if properties_fn is not None:
            extra = properties_fn(seed, verts, data_idx)
            if extra and isinstance(extra, dict):
                properties.update(extra)

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [ring],
            },
            "properties": properties,
        }
        features.append(feature)

    # Optionally include seed points as Point features
    if include_seeds:
        for idx, (px, py) in enumerate(data):
            seed_in_regions = (px, py) in regions
            point_feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [px, py],
                },
                "properties": {
                    "point_index": idx + 1,
                    "type": "seed",
                    "has_region": seed_in_regions,
                },
            }
            features.append(point_feature)

    collection = {
        "type": "FeatureCollection",
        "features": features,
    }

    # Optional CRS (GeoJSON 2008 style, useful for non-WGS84 data)
    if crs_name:
        collection["crs"] = {
            "type": "name",
            "properties": {
                "name": crs_name,
            },
        }

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(collection, f, indent=2, ensure_ascii=False)

    return output_path


def generate_geojson(
    datafile,
    output_path="voronoi.geojson",
    **kwargs,
):
    """Load data, compute regions, and export GeoJSON in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str
        Where to write the GeoJSON file.
    **kwargs
        Passed through to ``export_geojson()``.

    Returns
    -------
    str
        Path to the generated GeoJSON file.
    """
    data = vormap.load_data(datafile)
    regions = compute_regions(data)
    return export_geojson(regions, data, output_path, **kwargs)


# ── Region statistics & CSV/JSON export ──────────────────────────────

def _compute_perimeter(vertices):
    """Compute the perimeter of a polygon from its vertex list."""
    n = len(vertices)
    if n < 2:
        return 0.0
    perimeter = 0.0
    for i in range(n):
        j = (i + 1) % n
        dx = vertices[j][0] - vertices[i][0]
        dy = vertices[j][1] - vertices[i][1]
        perimeter += math.sqrt(dx * dx + dy * dy)
    return perimeter


def _compute_centroid(vertices):
    """Compute the centroid of a polygon using the standard formula.

    Returns (cx, cy).  For a simple polygon with signed area A, the
    centroid is:
        cx = 1/(6A) * Σ (xi + xi+1)(xi*yi+1 - xi+1*yi)
        cy = 1/(6A) * Σ (yi + yi+1)(xi*yi+1 - xi+1*yi)
    """
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        # Degenerate: just average the points
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (round(cx, 4), round(cy, 4))

    signed_area = 0.0
    cx = 0.0
    cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]
        signed_area += cross
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross

    signed_area *= 0.5
    if abs(signed_area) < 1e-12:
        # Degenerate polygon — fall back to simple average
        cx = sum(v[0] for v in vertices) / n
        cy = sum(v[1] for v in vertices) / n
        return (round(cx, 4), round(cy, 4))

    cx /= (6.0 * signed_area)
    cy /= (6.0 * signed_area)
    return (round(cx, 4), round(cy, 4))


def _isoperimetric_quotient(area, perimeter):
    """Compute the isoperimetric quotient (compactness / circularity).

    IQ = 4π·A / P²   —   equals 1.0 for a perfect circle, less for
    irregular shapes.  Returns 0.0 if the perimeter is zero.
    """
    if perimeter <= 0:
        return 0.0
    return 4.0 * math.pi * area / (perimeter * perimeter)


def compute_region_stats(regions, data):
    """Compute detailed statistics for each Voronoi region.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.

    Returns
    -------
    list of dict
        One dict per region (sorted by data index) with keys:

        - ``region_index`` (int): 1-based index in the original data
        - ``seed_x``, ``seed_y`` (float): seed coordinates
        - ``area`` (float): polygon area (Shoelace formula)
        - ``perimeter`` (float): polygon perimeter
        - ``centroid_x``, ``centroid_y`` (float): polygon centroid
        - ``vertex_count`` (int): number of polygon vertices
        - ``compactness`` (float): isoperimetric quotient (4πA/P²)
        - ``avg_edge_length`` (float): perimeter / vertex_count
    """
    stats = []
    sorted_seeds = sorted(regions.keys())
    data_lookup = _build_data_index(data)

    for seed in sorted_seeds:
        verts = regions[seed]
        data_idx = data_lookup.get(tuple(seed), len(stats))

        area = _compute_region_area(verts)
        perimeter = _compute_perimeter(verts)
        centroid = _compute_centroid(verts)
        compactness = _isoperimetric_quotient(area, perimeter)
        n_verts = len(verts)
        avg_edge = perimeter / n_verts if n_verts > 0 else 0.0

        stats.append({
            "region_index": data_idx + 1,
            "seed_x": seed[0],
            "seed_y": seed[1],
            "area": round(area, 4),
            "perimeter": round(perimeter, 4),
            "centroid_x": centroid[0],
            "centroid_y": centroid[1],
            "vertex_count": n_verts,
            "compactness": round(compactness, 4),
            "avg_edge_length": round(avg_edge, 4),
        })

    # Sort by region_index for consistent output
    stats.sort(key=lambda s: s["region_index"])
    return stats


def compute_summary_stats(region_stats):
    """Compute aggregate summary statistics across all regions.

    Parameters
    ----------
    region_stats : list of dict
        Output of ``compute_region_stats()``.

    Returns
    -------
    dict
        Summary with keys:

        - ``region_count`` (int)
        - ``total_area`` (float)
        - ``mean_area``, ``median_area``, ``min_area``, ``max_area`` (float)
        - ``std_area`` (float): standard deviation of areas
        - ``mean_perimeter``, ``min_perimeter``, ``max_perimeter`` (float)
        - ``mean_vertices`` (float)
        - ``mean_compactness`` (float)
        - ``area_range`` (float): max_area − min_area
        - ``coefficient_of_variation`` (float): std_area / mean_area
    """
    if not region_stats:
        return {
            "region_count": 0,
            "total_area": 0.0,
            "mean_area": 0.0,
            "median_area": 0.0,
            "min_area": 0.0,
            "max_area": 0.0,
            "std_area": 0.0,
            "mean_perimeter": 0.0,
            "min_perimeter": 0.0,
            "max_perimeter": 0.0,
            "mean_vertices": 0.0,
            "mean_compactness": 0.0,
            "area_range": 0.0,
            "coefficient_of_variation": 0.0,
        }

    areas = [s["area"] for s in region_stats]
    perimeters = [s["perimeter"] for s in region_stats]
    vertices = [s["vertex_count"] for s in region_stats]
    compactnesses = [s["compactness"] for s in region_stats]

    n = len(areas)
    total_area = sum(areas)
    mean_area = total_area / n
    sorted_areas = sorted(areas)
    if n % 2 == 1:
        median_area = sorted_areas[n // 2]
    else:
        median_area = (sorted_areas[n // 2 - 1] + sorted_areas[n // 2]) / 2.0

    variance = sum((a - mean_area) ** 2 for a in areas) / n
    std_area = math.sqrt(variance)
    cv = std_area / mean_area if mean_area > 0 else 0.0

    return {
        "region_count": n,
        "total_area": round(total_area, 4),
        "mean_area": round(mean_area, 4),
        "median_area": round(median_area, 4),
        "min_area": round(min(areas), 4),
        "max_area": round(max(areas), 4),
        "std_area": round(std_area, 4),
        "mean_perimeter": round(sum(perimeters) / n, 4),
        "min_perimeter": round(min(perimeters), 4),
        "max_perimeter": round(max(perimeters), 4),
        "mean_vertices": round(sum(vertices) / n, 2),
        "mean_compactness": round(sum(compactnesses) / n, 4),
        "area_range": round(max(areas) - min(areas), 4),
        "coefficient_of_variation": round(cv, 4),
    }


def export_stats_csv(region_stats, output_path, *, include_summary=True):
    """Export per-region statistics as a CSV file.

    Parameters
    ----------
    region_stats : list of dict
        Output of ``compute_region_stats()``.
    output_path : str
        Path to write the CSV file.
    include_summary : bool
        If True (default), append a summary row at the bottom with
        aggregate statistics (prefixed with ``#`` as a comment).

    Returns
    -------
    str
        Path to the generated CSV file.
    """
    import csv

    if not region_stats:
        raise ValueError("No region statistics to export")

    fieldnames = [
        "region_index", "seed_x", "seed_y", "area", "perimeter",
        "centroid_x", "centroid_y", "vertex_count", "compactness",
        "avg_edge_length",
    ]

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in region_stats:
            writer.writerow(row)

        if include_summary:
            summary = compute_summary_stats(region_stats)
            f.write("\n# Summary Statistics\n")
            f.write("# regions: %d\n" % summary["region_count"])
            f.write("# total_area: %.4f\n" % summary["total_area"])
            f.write("# mean_area: %.4f\n" % summary["mean_area"])
            f.write("# median_area: %.4f\n" % summary["median_area"])
            f.write("# min_area: %.4f\n" % summary["min_area"])
            f.write("# max_area: %.4f\n" % summary["max_area"])
            f.write("# std_area: %.4f\n" % summary["std_area"])
            f.write("# mean_perimeter: %.4f\n" % summary["mean_perimeter"])
            f.write("# mean_vertices: %.2f\n" % summary["mean_vertices"])
            f.write("# mean_compactness: %.4f\n" % summary["mean_compactness"])
            f.write("# coefficient_of_variation: %.4f\n"
                    % summary["coefficient_of_variation"])

    return output_path


def export_stats_json(region_stats, output_path, *, include_summary=True):
    """Export per-region statistics as a JSON file.

    Parameters
    ----------
    region_stats : list of dict
        Output of ``compute_region_stats()``.
    output_path : str
        Path to write the JSON file.
    include_summary : bool
        If True (default), include an aggregate summary alongside
        the per-region data.

    Returns
    -------
    str
        Path to the generated JSON file.
    """
    import json

    if not region_stats:
        raise ValueError("No region statistics to export")

    output = {"regions": region_stats}
    if include_summary:
        output["summary"] = compute_summary_stats(region_stats)

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return output_path


def format_stats_table(region_stats, *, summary=True):
    """Format region statistics as a human-readable text table.

    Parameters
    ----------
    region_stats : list of dict
        Output of ``compute_region_stats()``.
    summary : bool
        If True (default), append summary statistics below the table.

    Returns
    -------
    str
        Formatted table string ready for printing.
    """
    if not region_stats:
        return "No regions to display."

    # Column definitions: (header, key, width, format)
    columns = [
        ("#", "region_index", 5, "%d"),
        ("Seed X", "seed_x", 10, "%.2f"),
        ("Seed Y", "seed_y", 10, "%.2f"),
        ("Area", "area", 12, "%.2f"),
        ("Perimeter", "perimeter", 12, "%.2f"),
        ("Centroid X", "centroid_x", 11, "%.2f"),
        ("Centroid Y", "centroid_y", 11, "%.2f"),
        ("Verts", "vertex_count", 6, "%d"),
        ("Compact", "compactness", 9, "%.4f"),
        ("Avg Edge", "avg_edge_length", 10, "%.2f"),
    ]

    # Header
    header = " | ".join(h.ljust(w) for h, _, w, _ in columns)
    separator = "-+-".join("-" * w for _, _, w, _ in columns)

    lines = [header, separator]

    for row in region_stats:
        cells = []
        for _, key, width, fmt in columns:
            val = fmt % row[key]
            cells.append(val.ljust(width))
        lines.append(" | ".join(cells))

    if summary:
        s = compute_summary_stats(region_stats)
        lines.append(separator)
        lines.append("")
        lines.append("Summary:")
        lines.append("  Regions: %d" % s["region_count"])
        lines.append("  Total area: %.2f" % s["total_area"])
        lines.append("  Area — mean: %.2f, median: %.2f, "
                      "min: %.2f, max: %.2f, std: %.2f"
                      % (s["mean_area"], s["median_area"],
                         s["min_area"], s["max_area"], s["std_area"]))
        lines.append("  Perimeter — mean: %.2f, min: %.2f, max: %.2f"
                      % (s["mean_perimeter"], s["min_perimeter"],
                         s["max_perimeter"]))
        lines.append("  Mean vertices: %.1f" % s["mean_vertices"])
        lines.append("  Mean compactness: %.4f  (1.0 = perfect circle)"
                      % s["mean_compactness"])
        lines.append("  Coefficient of variation: %.4f"
                      % s["coefficient_of_variation"])

    return "\n".join(lines)


def generate_stats(datafile, output_path=None, *, fmt="table"):
    """Load data, compute regions, and export statistics in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str or None
        Where to write the output. If None, returns the formatted
        string (for "table" format) or the stats dict (for "json").
    fmt : str
        Output format: "table", "csv", or "json".

    Returns
    -------
    str or dict
        File path (if output_path given), formatted string (table),
        or dict (json without output_path).
    """
    data = vormap.load_data(datafile)
    regions = compute_regions(data)
    region_stats = compute_region_stats(regions, data)

    if fmt == "csv":
        if output_path is None:
            raise ValueError("CSV format requires an output_path")
        return export_stats_csv(region_stats, output_path)
    elif fmt == "json":
        if output_path is None:
            return {
                "regions": region_stats,
                "summary": compute_summary_stats(region_stats),
            }
        return export_stats_json(region_stats, output_path)
    else:  # table
        table = format_stats_table(region_stats)
        if output_path:
            output_path = vormap.validate_output_path(output_path, allow_absolute=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(table)
            return output_path
        return table


# ── Lloyd relaxation ──────────────────────────────────────────────────

def lloyd_relaxation(data, iterations=10, *, bounds=None, callback=None):
    """Apply Lloyd relaxation to make Voronoi regions more uniform.

    Lloyd's algorithm iteratively moves each seed point to the centroid
    of its Voronoi cell, producing increasingly regular tessellations.
    This is widely used in procedural generation, mesh optimization,
    texture synthesis, and spatial analysis.

    Parameters
    ----------
    data : list of (float, float)
        Initial seed points (e.g. from ``vormap.load_data()``).
    iterations : int
        Number of relaxation iterations (default: 10). More iterations
        produce more uniform results but take longer.
    bounds : tuple of (south, north, west, east) or None
        Bounding box for the Voronoi regions. If None, uses the current
        ``vormap`` global bounds.
    callback : callable or None
        Optional function called after each iteration with signature
        ``callback(iteration, points, regions, convergence)``.
        Useful for progress reporting or recording animation frames.

    Returns
    -------
    dict
        A dict with keys:

        - ``points`` (list): final relaxed seed points
        - ``regions`` (dict): final Voronoi regions
        - ``history`` (list): list of dicts per iteration, each with:
            - ``iteration`` (int): 0-based iteration number
            - ``points`` (list): seed positions at this step
            - ``regions`` (dict): Voronoi regions at this step
            - ``convergence`` (float): max displacement of any seed
        - ``total_iterations`` (int): number of iterations performed
        - ``converged`` (bool): True if converged early (convergence < threshold)

    Raises
    ------
    ValueError
        If *data* has fewer than 2 points or *iterations* < 1.

    Examples
    --------
    >>> import vormap, vormap_viz
    >>> data = vormap.load_data("datauni5.txt")
    >>> result = vormap_viz.lloyd_relaxation(data, iterations=20)
    >>> relaxed_points = result["points"]
    >>> relaxed_regions = result["regions"]
    """
    if len(data) < 2:
        raise ValueError("Lloyd relaxation requires at least 2 points")
    if iterations < 1:
        raise ValueError("iterations must be >= 1, got %d" % iterations)

    if bounds is None:
        bounds = (vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E)

    s, n, w, e = bounds
    convergence_threshold = 1e-6 * max(e - w, n - s)

    # Work with a mutable copy
    current_points = [tuple(p) for p in data]
    history = []
    converged = False

    for it in range(iterations):
        # Compute Voronoi regions for current points
        regions = compute_regions(current_points)

        # Move each point to its region's centroid
        new_points = []
        max_displacement = 0.0

        for i, pt in enumerate(current_points):
            if pt in regions:
                cx, cy = _compute_centroid(regions[pt])
                # Clamp to bounds to prevent drift outside the domain
                cx = max(w, min(e, cx))
                cy = max(s, min(n, cy))
                disp = math.sqrt((cx - pt[0]) ** 2 + (cy - pt[1]) ** 2)
                max_displacement = max(max_displacement, disp)
                new_points.append((cx, cy))
            else:
                # Point has no region (edge case) — keep it in place
                new_points.append(pt)

        history.append({
            "iteration": it,
            "points": list(current_points),
            "regions": dict(regions),
            "convergence": max_displacement,
        })

        if callback is not None:
            callback(it, current_points, regions, max_displacement)

        current_points = new_points

        if max_displacement < convergence_threshold:
            converged = True
            break

    # Final regions with the relaxed points
    final_regions = compute_regions(current_points)

    # Append final state to history
    history.append({
        "iteration": len(history),
        "points": list(current_points),
        "regions": dict(final_regions),
        "convergence": 0.0,
    })

    return {
        "points": current_points,
        "regions": final_regions,
        "history": history,
        "total_iterations": len(history) - 1,
        "converged": converged,
    }


def generate_relaxed_diagram(
    datafile,
    output_path="voronoi_relaxed.svg",
    iterations=10,
    **kwargs,
):
    """Load data, apply Lloyd relaxation, and export SVG in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str
        Where to write the SVG.
    iterations : int
        Number of relaxation iterations.
    **kwargs
        Passed through to ``export_svg()``.

    Returns
    -------
    str
        Path to the generated SVG file.
    """
    data = vormap.load_data(datafile)
    result = lloyd_relaxation(data, iterations=iterations)
    return export_svg(
        result["regions"],
        result["points"],
        output_path,
        **kwargs,
    )


def export_relaxation_html(
    data,
    iterations=10,
    output_path="relaxation.html",
    *,
    width=960,
    height=700,
    color_scheme=DEFAULT_COLOR_SCHEME,
    title=None,
    bounds=None,
):
    """Export an animated HTML visualization of Lloyd relaxation.

    Generates a self-contained HTML file that shows the relaxation
    process step by step with play/pause controls, a step slider,
    and convergence graph.

    Parameters
    ----------
    data : list of (float, float)
        Initial seed points.
    iterations : int
        Number of relaxation iterations to compute.
    output_path : str
        Path to write the HTML file.
    width, height : int
        Canvas dimensions.
    color_scheme : str
        Color scheme for the Voronoi regions.
    title : str or None
        Optional title.
    bounds : tuple or None
        Bounding box (south, north, west, east). Uses vormap globals if None.

    Returns
    -------
    str
        Path to the generated HTML file.
    """
    if len(data) < 2:
        raise ValueError("Need at least 2 points for relaxation animation")

    # Run the relaxation and collect all frames
    result = lloyd_relaxation(data, iterations=iterations, bounds=bounds)

    # Serialize frames for JavaScript
    import json

    frames = []
    for entry in result["history"]:
        frame_regions = []
        sorted_seeds = sorted(entry["regions"].keys())
        for seed in sorted_seeds:
            verts = entry["regions"][seed]
            frame_regions.append({
                "seed": list(seed),
                "vertices": [list(v) for v in verts],
            })
        frames.append({
            "iteration": entry["iteration"],
            "points": [list(p) for p in entry["points"]],
            "regions": frame_regions,
            "convergence": round(entry["convergence"], 6),
        })

    frames_json = json.dumps(frames)
    title_text = title or (
        "Lloyd Relaxation — %d points, %d iterations"
        % (len(data), result["total_iterations"])
    )

    html = _RELAXATION_HTML_TEMPLATE.replace("{{FRAMES_JSON}}", frames_json)
    html = html.replace("{{TITLE}}", _html_mod.escape(title_text))
    html = html.replace("{{INITIAL_SCHEME}}", _html_mod.escape(color_scheme))
    html = html.replace("{{WIDTH}}", str(width))
    html = html.replace("{{HEIGHT}}", str(height))
    html = html.replace("{{TOTAL_ITERATIONS}}", str(result["total_iterations"]))
    html = html.replace("{{CONVERGED}}", json.dumps(result["converged"]))

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path


_RELAXATION_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;overflow:hidden;
  transition:background .3s,color .3s}
body.light{background:#f5f5f5;color:#222}
body.dark{background:#1a1a2e;color:#e0e0e0}
#header{display:flex;align-items:center;justify-content:space-between;
  padding:8px 16px;gap:12px;flex-wrap:wrap;z-index:10;position:relative}
body.light #header{background:#fff;border-bottom:1px solid #ddd}
body.dark #header{background:#16213e;border-bottom:1px solid #333}
h1{font-size:15px;font-weight:600;white-space:nowrap}
.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
select,button,input[type=range]{font-size:13px;padding:4px 10px;border-radius:4px;
  cursor:pointer;border:1px solid #aaa;background:#fff;color:#222;transition:all .2s}
body.dark select,body.dark button{background:#0f3460;color:#e0e0e0;border-color:#555}
button:hover{opacity:0.85}
.play-btn{min-width:36px;font-size:16px}
#slider{width:180px;padding:2px}
#step-label{font-size:12px;font-weight:600;min-width:90px;text-align:center}
#convergence-label{font-size:11px;opacity:0.7;min-width:120px}
#canvas-wrap{position:relative;flex:1;display:flex;flex-direction:column}
canvas{display:block;cursor:grab;flex:1}
canvas.grabbing{cursor:grabbing}
#conv-canvas{height:80px;min-height:60px;border-top:1px solid #ddd;flex-shrink:0}
body.dark #conv-canvas{border-top-color:#333}
#tooltip{position:fixed;pointer-events:none;padding:8px 12px;border-radius:6px;
  font-size:12px;line-height:1.5;white-space:nowrap;opacity:0;
  transition:opacity .15s;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.25)}
body.light #tooltip{background:#222;color:#fff}
body.dark #tooltip{background:#e8e8e8;color:#111}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;
  font-weight:600;margin-left:6px}
.badge.converged{background:#2ecc71;color:#fff}
.badge.running{background:#3498db;color:#fff}
</style>
</head>
<body class="light">
<div id="header">
  <h1>{{TITLE}}
    <span id="status-badge" class="badge running">running</span>
  </h1>
  <div class="controls">
    <button class="play-btn" id="play-btn" title="Play/Pause">▶</button>
    <input type="range" id="slider" min="0" max="{{TOTAL_ITERATIONS}}" value="0">
    <span id="step-label">Step 0 / {{TOTAL_ITERATIONS}}</span>
    <span id="convergence-label">Δ = —</span>
    <label>Speed: <select id="speed">
      <option value="2000">Slow</option>
      <option value="800" selected>Normal</option>
      <option value="300">Fast</option>
      <option value="100">Turbo</option>
    </select></label>
    <label>Color: <select id="scheme">
      <option value="pastel">Pastel</option>
      <option value="warm">Warm</option>
      <option value="cool">Cool</option>
      <option value="earth">Earth</option>
      <option value="mono">Mono</option>
      <option value="rainbow">Rainbow</option>
    </select></label>
    <button id="theme-btn" title="Toggle dark/light">🌙</button>
  </div>
</div>
<div id="canvas-wrap">
  <canvas id="c"></canvas>
  <canvas id="conv-canvas"></canvas>
</div>
<div id="tooltip"></div>

<script>
(function(){
"use strict";

var frames = {{FRAMES_JSON}};
var converged = {{CONVERGED}};
var initialScheme = "{{INITIAL_SCHEME}}";

// ── Color schemes ──
function hslToHex(h,s,l){
  h%=1;if(h<0)h+=1;
  var c=(1-Math.abs(2*l-1))*s,x=c*(1-Math.abs((h*6)%2-1)),m=l-c/2;
  var r,g,b;
  if(h<1/6){r=c;g=x;b=0}else if(h<2/6){r=x;g=c;b=0}
  else if(h<3/6){r=0;g=c;b=x}else if(h<4/6){r=0;g=x;b=c}
  else if(h<5/6){r=x;g=0;b=c}else{r=c;g=0;b=x}
  r=Math.round((r+m)*255);g=Math.round((g+m)*255);b=Math.round((b+m)*255);
  return "#"+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
}
function grayHex(l){var v=Math.max(0,Math.min(255,Math.round(l*255)));
  return "#"+((1<<24)+(v<<16)+(v<<8)+v).toString(16).slice(1)}

var schemes={
  pastel:function(i,n){return hslToHex(i/Math.max(n,1),.65,.82)},
  warm:function(i,n){return hslToHex((i/Math.max(n,1))*.15,.70,.75)},
  cool:function(i,n){return hslToHex(.5+(i/Math.max(n,1))*.2,.60,.78)},
  earth:function(i,n){return hslToHex(.08+(i/Math.max(n,1))*.1,.45,.65)},
  mono:function(i,n){return grayHex(.88-.35*(i/Math.max(n,1)))},
  rainbow:function(i,n){return hslToHex(i/Math.max(n,1),.70,.68)}
};

var curScheme=initialScheme;
var curFrame=0;
var playing=false;
var timer=null;
var speed=800;

// ── Data bounds from all frames ──
var allX=[],allY=[];
frames.forEach(function(f){
  f.points.forEach(function(p){allX.push(p[0]);allY.push(p[1])});
  f.regions.forEach(function(r){r.vertices.forEach(function(v){allX.push(v[0]);allY.push(v[1])})});
});
var minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
for(var _i=0;_i<allX.length;_i++){if(allX[_i]<minX)minX=allX[_i];if(allX[_i]>maxX)maxX=allX[_i]}
for(var _i=0;_i<allY.length;_i++){if(allY[_i]<minY)minY=allY[_i];if(allY[_i]>maxY)maxY=allY[_i]}
var rangeX=Math.max(maxX-minX,1e-6),rangeY=Math.max(maxY-minY,1e-6);

// ── Canvas setup ──
var canvas=document.getElementById("c"),ctx=canvas.getContext("2d");
var convCanvas=document.getElementById("conv-canvas"),convCtx=convCanvas.getContext("2d");
var wrap=document.getElementById("canvas-wrap");
var tooltip=document.getElementById("tooltip");

function resize(){
  canvas.width=wrap.clientWidth;
  canvas.height=wrap.clientHeight-convCanvas.offsetHeight;
  convCanvas.width=wrap.clientWidth;
  fitView();
  draw();
  drawConvergence();
}
window.addEventListener("resize",resize);

// ── Transform ──
var panX=0,panY=0,zoom=1;
function fitView(){
  var cw=canvas.width,ch=canvas.height,margin=50;
  var dw=cw-2*margin,dh=ch-2*margin;
  zoom=Math.min(dw/rangeX,dh/rangeY);
  panX=margin+(dw-rangeX*zoom)/2;
  panY=margin+(dh-rangeY*zoom)/2;
}
function tx(x){return panX+(x-minX)*zoom}
function ty(y){return panY+(maxY-y)*zoom}

// ── Hit test ──
var hoverIdx=-1;
function pointInPoly(px,py,verts){
  var inside=false,n=verts.length;
  for(var i=0,j=n-1;i<n;j=i++){
    var xi=tx(verts[i][0]),yi=ty(verts[i][1]);
    var xj=tx(verts[j][0]),yj=ty(verts[j][1]);
    if(((yi>py)!==(yj>py))&&(px<(xj-xi)*(py-yi)/(yj-yi)+xi))inside=!inside;
  }
  return inside;
}
function findRegion(mx,my){
  var f=frames[curFrame];
  for(var i=0;i<f.regions.length;i++){
    if(pointInPoly(mx,my,f.regions[i].vertices))return i;
  }
  return -1;
}

// ── Draw main canvas ──
function draw(){
  var cw=canvas.width,ch=canvas.height;
  var isDark=document.body.classList.contains("dark");
  ctx.clearRect(0,0,cw,ch);
  ctx.fillStyle=isDark?"#1a1a2e":"#f5f5f5";
  ctx.fillRect(0,0,cw,ch);

  var f=frames[curFrame];
  var colorFn=schemes[curScheme]||schemes.pastel;
  var n=f.regions.length;

  // Regions
  for(var i=0;i<n;i++){
    var r=f.regions[i],verts=r.vertices;
    ctx.beginPath();
    ctx.moveTo(tx(verts[0][0]),ty(verts[0][1]));
    for(var j=1;j<verts.length;j++)ctx.lineTo(tx(verts[j][0]),ty(verts[j][1]));
    ctx.closePath();
    ctx.fillStyle=colorFn(i,n);
    if(i===hoverIdx){ctx.globalAlpha=0.85;ctx.fill();ctx.globalAlpha=1;
      ctx.strokeStyle=isDark?"#fff":"#000";ctx.lineWidth=2.5;
    }else{ctx.fill();ctx.strokeStyle=isDark?"#555":"#444";ctx.lineWidth=1;}
    ctx.stroke();
  }

  // Seed points
  var pts=f.points;
  ctx.fillStyle=isDark?"#e0e0e0":"#222";
  ctx.strokeStyle=isDark?"#1a1a2e":"#fff";
  ctx.lineWidth=1;
  for(var i=0;i<pts.length;i++){
    ctx.beginPath();
    ctx.arc(tx(pts[i][0]),ty(pts[i][1]),3.5*Math.min(zoom/5+.5,1.5),0,Math.PI*2);
    ctx.fill();ctx.stroke();
  }

  // Draw previous positions as ghost dots (faded)
  if(curFrame>0){
    var prev=frames[curFrame-1].points;
    ctx.globalAlpha=0.25;
    ctx.fillStyle=isDark?"#e74c3c":"#c0392b";
    for(var i=0;i<prev.length;i++){
      ctx.beginPath();
      ctx.arc(tx(prev[i][0]),ty(prev[i][1]),2.5,0,Math.PI*2);
      ctx.fill();
    }
    ctx.globalAlpha=1;
  }
}

// ── Draw convergence chart ──
function drawConvergence(){
  var cw=convCanvas.width,ch=convCanvas.height;
  var isDark=document.body.classList.contains("dark");
  convCtx.clearRect(0,0,cw,ch);
  convCtx.fillStyle=isDark?"#16213e":"#fff";
  convCtx.fillRect(0,0,cw,ch);

  var convs=frames.map(function(f){return f.convergence});
  var maxConv=0;for(var _i=0;_i<convs.length;_i++){if(convs[_i]>0&&convs[_i]>maxConv)maxConv=convs[_i]}
  if(!maxConv||maxConv<=0)maxConv=1;

  var pad=40,gw=cw-2*pad,gh=ch-25;
  var n=convs.length;
  if(n<2)return;

  // Axes
  convCtx.strokeStyle=isDark?"#555":"#ccc";
  convCtx.lineWidth=1;
  convCtx.beginPath();
  convCtx.moveTo(pad,5);convCtx.lineTo(pad,gh);convCtx.lineTo(pad+gw,gh);
  convCtx.stroke();

  // Label
  convCtx.fillStyle=isDark?"#aaa":"#666";
  convCtx.font="10px sans-serif";
  convCtx.textAlign="center";
  convCtx.fillText("Convergence (max displacement per step)",cw/2,ch-3);
  convCtx.textAlign="right";
  convCtx.fillText(maxConv.toFixed(2),pad-4,12);
  convCtx.fillText("0",pad-4,gh);

  // Line
  convCtx.beginPath();
  convCtx.strokeStyle="#3498db";
  convCtx.lineWidth=2;
  for(var i=0;i<n;i++){
    var x=pad+(i/(n-1))*gw;
    var y=gh-(convs[i]/maxConv)*(gh-10);
    if(i===0)convCtx.moveTo(x,y);else convCtx.lineTo(x,y);
  }
  convCtx.stroke();

  // Dots
  for(var i=0;i<n;i++){
    var x=pad+(i/(n-1))*gw;
    var y=gh-(convs[i]/maxConv)*(gh-10);
    convCtx.beginPath();
    convCtx.arc(x,y,3,0,Math.PI*2);
    convCtx.fillStyle=i===curFrame?"#e74c3c":"#3498db";
    convCtx.fill();
  }

  // Current step marker
  var cx=pad+(curFrame/(n-1))*gw;
  convCtx.strokeStyle="#e74c3c";
  convCtx.lineWidth=1;
  convCtx.setLineDash([4,3]);
  convCtx.beginPath();
  convCtx.moveTo(cx,5);convCtx.lineTo(cx,gh);
  convCtx.stroke();
  convCtx.setLineDash([]);
}

// ── Controls ──
var slider=document.getElementById("slider");
var stepLabel=document.getElementById("step-label");
var convLabel=document.getElementById("convergence-label");
var playBtn=document.getElementById("play-btn");
var statusBadge=document.getElementById("status-badge");

function updateUI(){
  var f=frames[curFrame];
  slider.value=curFrame;
  stepLabel.textContent="Step "+f.iteration+" / "+frames[frames.length-1].iteration;
  if(f.convergence>0)convLabel.textContent="\u0394 = "+f.convergence.toFixed(4);
  else convLabel.textContent="\u0394 = 0 (done)";

  if(converged){statusBadge.textContent="converged";statusBadge.className="badge converged";}
  else{statusBadge.textContent="complete";statusBadge.className="badge running";}

  draw();
  drawConvergence();
}

slider.addEventListener("input",function(){
  curFrame=parseInt(this.value);
  updateUI();
});

playBtn.addEventListener("click",function(){
  if(playing){stopPlay();}else{startPlay();}
});

function startPlay(){
  playing=true;
  playBtn.textContent="\u23F8";
  if(curFrame>=frames.length-1)curFrame=0;
  tick();
}
function stopPlay(){
  playing=false;
  playBtn.textContent="\u25B6";
  if(timer){clearTimeout(timer);timer=null;}
}
function tick(){
  if(!playing)return;
  curFrame++;
  if(curFrame>=frames.length){curFrame=frames.length-1;stopPlay();return;}
  updateUI();
  timer=setTimeout(tick,speed);
}

document.getElementById("speed").addEventListener("change",function(){
  speed=parseInt(this.value);
});
document.getElementById("scheme").value=initialScheme;
document.getElementById("scheme").addEventListener("change",function(){
  curScheme=this.value;draw();
});

// Theme
var themeBtn=document.getElementById("theme-btn");
themeBtn.addEventListener("click",function(){
  var isDark=document.body.classList.toggle("dark");
  document.body.classList.toggle("light",!isDark);
  themeBtn.textContent=isDark?"\u2600\uFE0F":"\uD83C\uDF19";
  draw();drawConvergence();
});

// Pan & zoom
var dragging=false,dragStartX=0,dragStartY=0,panStartX=0,panStartY=0;
canvas.addEventListener("mousedown",function(e){
  if(e.button===0){dragging=true;dragStartX=e.clientX;dragStartY=e.clientY;
    panStartX=panX;panStartY=panY;canvas.classList.add("grabbing");}
});
window.addEventListener("mousemove",function(e){
  if(dragging){panX=panStartX+(e.clientX-dragStartX);panY=panStartY+(e.clientY-dragStartY);draw();}
});
window.addEventListener("mouseup",function(){dragging=false;canvas.classList.remove("grabbing");});
canvas.addEventListener("wheel",function(e){
  e.preventDefault();
  var rect=canvas.getBoundingClientRect();
  var mx=e.clientX-rect.left,my=e.clientY-rect.top;
  var factor=e.deltaY<0?1.15:1/1.15;
  panX=mx-factor*(mx-panX);panY=my-factor*(my-panY);
  zoom*=factor;draw();
},{passive:false});

// Hover
canvas.addEventListener("mousemove",function(e){
  if(dragging)return;
  var rect=canvas.getBoundingClientRect();
  var mx=e.clientX-rect.left,my=e.clientY-rect.top;
  var idx=findRegion(mx,my);
  if(idx!==hoverIdx){hoverIdx=idx;draw();}
  if(idx>=0){
    var r=frames[curFrame].regions[idx];
    tooltip.innerHTML="<b>Region</b><br>Seed: ("+r.seed[0].toFixed(2)+", "+r.seed[1].toFixed(2)+")";
    tooltip.style.opacity="1";
    tooltip.style.left=(e.clientX+14)+"px";
    tooltip.style.top=(e.clientY+14)+"px";
  }else{tooltip.style.opacity="0";}
});
canvas.addEventListener("mouseleave",function(){hoverIdx=-1;tooltip.style.opacity="0";draw();});

// Keyboard
document.addEventListener("keydown",function(e){
  if(e.key===" "||e.key==="k"){e.preventDefault();if(playing)stopPlay();else startPlay();}
  if(e.key==="ArrowRight"&&curFrame<frames.length-1){curFrame++;updateUI();}
  if(e.key==="ArrowLeft"&&curFrame>0){curFrame--;updateUI();}
  if(e.key==="Home"){curFrame=0;updateUI();}
  if(e.key==="End"){curFrame=frames.length-1;updateUI();}
});

// Init
resize();
updateUI();
})();
</script>
</body>
</html>"""


_INTERACTIVE_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;overflow:hidden;
  transition:background .3s,color .3s}
body.light{background:#f5f5f5;color:#222}
body.dark{background:#1a1a2e;color:#e0e0e0}
#header{display:flex;align-items:center;justify-content:space-between;
  padding:8px 16px;gap:12px;flex-wrap:wrap;z-index:10;position:relative}
body.light #header{background:#fff;border-bottom:1px solid #ddd}
body.dark #header{background:#16213e;border-bottom:1px solid #333}
h1{font-size:16px;font-weight:600;white-space:nowrap}
.controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
select,button{font-size:13px;padding:4px 10px;border-radius:4px;cursor:pointer;
  border:1px solid #aaa;background:#fff;color:#222;transition:all .2s}
body.dark select,body.dark button{background:#0f3460;color:#e0e0e0;border-color:#555}
button:hover{opacity:0.85}
.zoom-btn{width:32px;font-size:16px;font-weight:bold;text-align:center;padding:4px}
#canvas-wrap{position:relative;flex:1;overflow:hidden}
canvas{display:block;cursor:grab}
canvas.grabbing{cursor:grabbing}
#tooltip{position:fixed;pointer-events:none;padding:8px 12px;border-radius:6px;
  font-size:12px;line-height:1.5;white-space:nowrap;opacity:0;
  transition:opacity .15s;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.25)}
body.light #tooltip{background:#222;color:#fff}
body.dark #tooltip{background:#e8e8e8;color:#111}
#stats{font-size:12px;opacity:0.7;white-space:nowrap}
</style>
</head>
<body class="light">
<div id="header">
  <h1>{{TITLE}}</h1>
  <div class="controls">
    <label>Color: <select id="scheme">
      <option value="pastel">Pastel</option>
      <option value="warm">Warm</option>
      <option value="cool">Cool</option>
      <option value="earth">Earth</option>
      <option value="mono">Mono</option>
      <option value="rainbow">Rainbow</option>
    </select></label>
    <button class="zoom-btn" id="zoom-in" title="Zoom in">+</button>
    <button class="zoom-btn" id="zoom-out" title="Zoom out">−</button>
    <button id="zoom-reset" title="Reset view">Reset</button>
    <button id="theme-btn" title="Toggle dark/light">🌙</button>
    <span id="stats"></span>
  </div>
</div>
<div id="canvas-wrap"><canvas id="c"></canvas></div>
<div id="tooltip"></div>

<script>
(function(){
"use strict";

var regions = {{REGIONS_JSON}};
var points  = {{POINTS_JSON}};
var initialScheme = "{{INITIAL_SCHEME}}";

// ── Color scheme generators ──
function hslToHex(h,s,l){
  h%=1; if(h<0)h+=1;
  var c=(1-Math.abs(2*l-1))*s, x=c*(1-Math.abs((h*6)%2-1)), m=l-c/2;
  var r,g,b;
  if(h<1/6){r=c;g=x;b=0}else if(h<2/6){r=x;g=c;b=0}
  else if(h<3/6){r=0;g=c;b=x}else if(h<4/6){r=0;g=x;b=c}
  else if(h<5/6){r=x;g=0;b=c}else{r=c;g=0;b=x}
  r=Math.round((r+m)*255);g=Math.round((g+m)*255);b=Math.round((b+m)*255);
  return "#"+((1<<24)+(r<<16)+(g<<8)+b).toString(16).slice(1);
}
function grayHex(l){var v=Math.max(0,Math.min(255,Math.round(l*255)));
  return "#"+((1<<24)+(v<<16)+(v<<8)+v).toString(16).slice(1)}

var schemes = {
  pastel:  function(i,n){return hslToHex(i/Math.max(n,1),.65,.82)},
  warm:    function(i,n){return hslToHex((i/Math.max(n,1))*.15,.70,.75)},
  cool:    function(i,n){return hslToHex(.5+(i/Math.max(n,1))*.2,.60,.78)},
  earth:   function(i,n){return hslToHex(.08+(i/Math.max(n,1))*.1,.45,.65)},
  mono:    function(i,n){return grayHex(.88-.35*(i/Math.max(n,1)))},
  rainbow: function(i,n){return hslToHex(i/Math.max(n,1),.70,.68)}
};

var curScheme = initialScheme;

// ── Data bounds ──
var allX=[], allY=[];
points.forEach(function(p){allX.push(p[0]);allY.push(p[1])});
regions.forEach(function(r){r.vertices.forEach(function(v){allX.push(v[0]);allY.push(v[1])})});
var minX=Infinity,maxX=-Infinity,minY=Infinity,maxY=-Infinity;
for(var _i=0;_i<allX.length;_i++){if(allX[_i]<minX)minX=allX[_i];if(allX[_i]>maxX)maxX=allX[_i]}
for(var _i=0;_i<allY.length;_i++){if(allY[_i]<minY)minY=allY[_i];if(allY[_i]>maxY)maxY=allY[_i]}
var rangeX=Math.max(maxX-minX,1e-6), rangeY=Math.max(maxY-minY,1e-6);

// ── Canvas setup ──
var canvas=document.getElementById("c"), ctx=canvas.getContext("2d");
var wrap=document.getElementById("canvas-wrap");
var tooltip=document.getElementById("tooltip");

function resize(){
  canvas.width=wrap.clientWidth;
  canvas.height=wrap.clientHeight;
  draw();
}
window.addEventListener("resize",resize);

// ── Transform state ──
var panX=0, panY=0, zoom=1;

function fitView(){
  var cw=canvas.width, ch=canvas.height, margin=50;
  var dw=cw-2*margin, dh=ch-2*margin;
  zoom=Math.min(dw/rangeX, dh/rangeY);
  panX=margin+(dw-rangeX*zoom)/2;
  panY=margin+(dh-rangeY*zoom)/2;
}

function tx(x){return panX+(x-minX)*zoom}
function ty(y){return panY+(maxY-y)*zoom}  // flip Y

// ── Hit testing ──
var hoverIdx=-1;

function pointInPoly(px,py,verts){
  var inside=false, n=verts.length;
  for(var i=0,j=n-1;i<n;j=i++){
    var xi=tx(verts[i][0]),yi=ty(verts[i][1]);
    var xj=tx(verts[j][0]),yj=ty(verts[j][1]);
    if(((yi>py)!==(yj>py))&&(px<(xj-xi)*(py-yi)/(yj-yi)+xi))
      inside=!inside;
  }
  return inside;
}

function findRegion(mx,my){
  for(var i=0;i<regions.length;i++){
    if(pointInPoly(mx,my,regions[i].vertices)) return i;
  }
  return -1;
}

// ── Drawing ──
function draw(){
  var cw=canvas.width, ch=canvas.height;
  var isDark=document.body.classList.contains("dark");
  ctx.clearRect(0,0,cw,ch);
  ctx.fillStyle=isDark?"#1a1a2e":"#f5f5f5";
  ctx.fillRect(0,0,cw,ch);

  var colorFn=schemes[curScheme]||schemes.pastel;
  var n=regions.length;

  // Draw regions
  for(var i=0;i<n;i++){
    var r=regions[i], verts=r.vertices;
    ctx.beginPath();
    ctx.moveTo(tx(verts[0][0]),ty(verts[0][1]));
    for(var j=1;j<verts.length;j++) ctx.lineTo(tx(verts[j][0]),ty(verts[j][1]));
    ctx.closePath();
    ctx.fillStyle=colorFn(i,n);
    if(i===hoverIdx){
      // Brighten on hover
      ctx.globalAlpha=0.85;
      ctx.fill();
      ctx.globalAlpha=1;
      ctx.strokeStyle=isDark?"#fff":"#000";
      ctx.lineWidth=2.5;
    } else {
      ctx.fill();
      ctx.strokeStyle=isDark?"#555":"#444";
      ctx.lineWidth=1;
    }
    ctx.stroke();
  }

  // Draw seed points
  ctx.fillStyle=isDark?"#e0e0e0":"#222";
  ctx.strokeStyle=isDark?"#1a1a2e":"#fff";
  ctx.lineWidth=1;
  for(var i=0;i<points.length;i++){
    ctx.beginPath();
    ctx.arc(tx(points[i][0]),ty(points[i][1]),3.5*Math.min(zoom/5+0.5,1.5),0,Math.PI*2);
    ctx.fill();
    ctx.stroke();
  }
}

// ── Mouse interaction: pan & zoom ──
var dragging=false, dragStartX=0, dragStartY=0, panStartX=0, panStartY=0;

canvas.addEventListener("mousedown",function(e){
  if(e.button===0){
    dragging=true;
    dragStartX=e.clientX; dragStartY=e.clientY;
    panStartX=panX; panStartY=panY;
    canvas.classList.add("grabbing");
  }
});
window.addEventListener("mousemove",function(e){
  if(dragging){
    panX=panStartX+(e.clientX-dragStartX);
    panY=panStartY+(e.clientY-dragStartY);
    draw();
  }
});
window.addEventListener("mouseup",function(){
  dragging=false;
  canvas.classList.remove("grabbing");
});

canvas.addEventListener("wheel",function(e){
  e.preventDefault();
  var rect=canvas.getBoundingClientRect();
  var mx=e.clientX-rect.left, my=e.clientY-rect.top;
  var factor=e.deltaY<0?1.15:1/1.15;
  // Zoom centered on cursor
  panX=mx-factor*(mx-panX);
  panY=my-factor*(my-panY);
  zoom*=factor;
  draw();
},{passive:false});

// ── Hover / tooltip ──
canvas.addEventListener("mousemove",function(e){
  if(dragging) return;
  var rect=canvas.getBoundingClientRect();
  var mx=e.clientX-rect.left, my=e.clientY-rect.top;
  var idx=findRegion(mx,my);
  if(idx!==hoverIdx){
    hoverIdx=idx;
    draw();
  }
  if(idx>=0){
    var r=regions[idx];
    tooltip.innerHTML="<b>Region #"+(r.dataIndex+1)+"</b><br>"+
      "Seed: ("+r.seed[0].toFixed(2)+", "+r.seed[1].toFixed(2)+")<br>"+
      "Area: "+r.area.toFixed(2)+" sq units<br>"+
      "Vertices: "+r.vertexCount;
    tooltip.style.opacity="1";
    tooltip.style.left=(e.clientX+14)+"px";
    tooltip.style.top=(e.clientY+14)+"px";
  } else {
    tooltip.style.opacity="0";
  }
});
canvas.addEventListener("mouseleave",function(){
  hoverIdx=-1;
  tooltip.style.opacity="0";
  draw();
});

// ── Controls ──
var schemeSelect=document.getElementById("scheme");
schemeSelect.value=initialScheme;
schemeSelect.addEventListener("change",function(){
  curScheme=this.value;
  draw();
});

document.getElementById("zoom-in").addEventListener("click",function(){
  var cx=canvas.width/2, cy=canvas.height/2;
  panX=cx-1.3*(cx-panX); panY=cy-1.3*(cy-panY);
  zoom*=1.3; draw();
});
document.getElementById("zoom-out").addEventListener("click",function(){
  var cx=canvas.width/2, cy=canvas.height/2;
  panX=cx-(cx-panX)/1.3; panY=cy-(cy-panY)/1.3;
  zoom/=1.3; draw();
});
document.getElementById("zoom-reset").addEventListener("click",function(){
  fitView(); draw();
});

// Theme toggle
var themeBtn=document.getElementById("theme-btn");
themeBtn.addEventListener("click",function(){
  var isDark=document.body.classList.toggle("dark");
  document.body.classList.toggle("light",!isDark);
  themeBtn.textContent=isDark?"☀️":"🌙";
  draw();
});

// Stats
document.getElementById("stats").textContent=
  regions.length+" regions · "+points.length+" points";

// Init
resize();
fitView();
draw();
})();
</script>
</body>
</html>"""


# ── Neighborhood graph (re-exported from vormap_graph) ────────────────

from vormap_graph import (  # noqa: E402, F401
    _edges_from_region_approx,
    extract_neighborhood_graph,
    compute_graph_stats,
    export_graph_json,
    export_graph_csv,
    format_graph_stats_table,
    export_graph_svg,
    generate_graph,
)
