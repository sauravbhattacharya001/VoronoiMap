"""Contour line extraction from Voronoi cell values.

Generate isolines (contour lines) from scalar values associated with
Voronoi seed points.  Uses marching squares on an interpolated grid to
trace smooth contour paths at specified levels.

Functions
---------
- ``extract_contours`` — Trace contour lines from seed values.
- ``auto_levels`` — Compute evenly-spaced contour levels from data range.
- ``contour_to_geojson`` — Export contour lines as GeoJSON FeatureCollection.
- ``export_contour_svg`` — Render contour lines over Voronoi diagram as SVG.
- ``contour_length`` — Total length of a contour path.
- ``generate_contours`` — Convenience one-call pipeline: data → SVG + GeoJSON.

Example::

    from vormap_contour import generate_contours

    # Seeds with temperature values
    seeds = [(100, 200), (300, 400), (500, 100), (700, 350)]
    values = [25.0, 30.0, 22.0, 28.0]
    generate_contours(seeds, values, "temp_contours.svg",
                      levels=6, bounds=(0, 800, 0, 500))

CLI::

    python vormap_contour.py seeds.txt --values vals.txt --levels 10 -o contours.svg
"""

from __future__ import annotations

import argparse
import json
import math
import os
import xml.etree.ElementTree as ET

import vormap


# ---------- Marching Squares Tables ----------

# Index → list of edge pairs for each cell configuration (0–15).
# Each edge is encoded as (side, offset) where side is 0=bottom, 1=right,
# 2=top, 3=left and offset is the interpolation fraction along that edge.
_MS_EDGES = {
    0: [], 1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)],
    4: [(1, 2)], 5: [(3, 2), (1, 0)], 6: [(0, 2)], 7: [(3, 2)],
    8: [(2, 3)], 9: [(2, 0)], 10: [(0, 3), (2, 1)], 11: [(2, 1)],
    12: [(1, 3)], 13: [(1, 0)], 14: [(0, 3)], 15: [],
}


def _lerp(a: float, b: float, va: float, vb: float, level: float) -> float:
    """Linear interpolation fraction between two grid corners."""
    dv = vb - va
    if abs(dv) < 1e-12:
        return 0.5
    return max(0.0, min(1.0, (level - va) / dv))


def _march_squares(grid: list[list[float]], level: float,
                   x0: float, y0: float, dx: float, dy: float,
                   ) -> list[list[tuple[float, float]]]:
    """Trace contour segments at *level* through a regular grid.

    Uses marching squares to find line segments, then chains them into
    polylines.

    Parameters
    ----------
    grid : list of list of float
        2-D scalar field, ``grid[row][col]``.
    level : float
        Iso-value to trace.
    x0, y0 : float
        Origin (lower-left corner) of the grid.
    dx, dy : float
        Cell size in x and y.

    Returns
    -------
    list of list of (float, float)
        Each inner list is a polyline (sequence of (x, y) points).
    """
    rows = len(grid) - 1
    cols = len(grid[0]) - 1
    if rows < 1 or cols < 1:
        return []

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for r in range(rows):
        for c in range(cols):
            # Four corners: BL, BR, TR, TL
            bl = grid[r][c]
            br = grid[r][c + 1]
            tr = grid[r + 1][c + 1]
            tl = grid[r + 1][c]

            # Cell index (binary: TL TR BR BL)
            idx = 0
            if bl >= level:
                idx |= 1
            if br >= level:
                idx |= 2
            if tr >= level:
                idx |= 4
            if tl >= level:
                idx |= 8

            if idx == 0 or idx == 15:
                continue

            # Edge interpolation points
            cx = x0 + c * dx
            cy = y0 + r * dy

            def _edge_point(side: int) -> tuple[float, float]:
                if side == 0:  # bottom: BL → BR
                    t = _lerp(cx, cx + dx, bl, br, level)
                    return (cx + t * dx, cy)
                elif side == 1:  # right: BR → TR
                    t = _lerp(cy, cy + dy, br, tr, level)
                    return (cx + dx, cy + t * dy)
                elif side == 2:  # top: TL → TR
                    t = _lerp(cx, cx + dx, tl, tr, level)
                    return (cx + t * dx, cy + dy)
                else:  # left: BL → TL
                    t = _lerp(cy, cy + dy, bl, tl, level)
                    return (cx, cy + t * dy)

            # Saddle-point disambiguation (cases 5 and 10)
            if idx == 5:
                center = (bl + br + tr + tl) / 4.0
                if center >= level:
                    segments.append((_edge_point(3), _edge_point(2)))
                    segments.append((_edge_point(0), _edge_point(1)))
                else:
                    segments.append((_edge_point(3), _edge_point(0)))
                    segments.append((_edge_point(2), _edge_point(1)))
            elif idx == 10:
                center = (bl + br + tr + tl) / 4.0
                if center >= level:
                    segments.append((_edge_point(0), _edge_point(1)))
                    segments.append((_edge_point(2), _edge_point(3)))
                else:
                    segments.append((_edge_point(0), _edge_point(3)))
                    segments.append((_edge_point(2), _edge_point(1)))
            else:
                # Standard cases — one segment per cell
                edges = []
                for bit in range(4):
                    a_in = bool(idx & (1 << bit))
                    b_in = bool(idx & (1 << ((bit + 1) % 4)))
                    if a_in != b_in:
                        edges.append(bit)
                if len(edges) == 2:
                    segments.append((_edge_point(edges[0]), _edge_point(edges[1])))

    # Chain segments into polylines
    return _chain_segments(segments)


def _chain_segments(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    tol: float = 1e-6,
) -> list[list[tuple[float, float]]]:
    """Chain disconnected line segments into polylines."""
    if not segments:
        return []

    remaining = list(segments)
    polylines: list[list[tuple[float, float]]] = []

    while remaining:
        seg = remaining.pop(0)
        chain = [seg[0], seg[1]]

        changed = True
        while changed:
            changed = False
            for i, s in enumerate(remaining):
                head = chain[0]
                tail = chain[-1]
                if _pt_close(s[1], tail, tol):
                    chain.append(s[0])
                    # swap direction and append
                    chain[-2], chain[-1] = chain[-1], chain[-2]
                    chain.append(s[1])
                    remaining.pop(i)
                    changed = True
                    break
                elif _pt_close(s[0], tail, tol):
                    chain.append(s[1])
                    remaining.pop(i)
                    changed = True
                    break
                elif _pt_close(s[1], head, tol):
                    chain.insert(0, s[0])
                    remaining.pop(i)
                    changed = True
                    break
                elif _pt_close(s[0], head, tol):
                    chain.insert(0, s[1])
                    remaining.pop(i)
                    changed = True
                    break

        polylines.append(chain)

    return polylines


def _pt_close(a: tuple[float, float], b: tuple[float, float], tol: float) -> bool:
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


# ---------- Public API ----------

def auto_levels(values: list[float], n: int = 8) -> list[float]:
    """Compute *n* evenly-spaced contour levels spanning the data range.

    Parameters
    ----------
    values : list of float
        Scalar values associated with seed points.
    n : int
        Number of contour levels (default 8).

    Returns
    -------
    list of float
        Contour levels from min to max (exclusive of endpoints).
    """
    if not values or n < 1:
        return []
    lo, hi = min(values), max(values)
    if abs(hi - lo) < 1e-12:
        return []
    step = (hi - lo) / (n + 1)
    return [lo + step * (i + 1) for i in range(n)]


def contour_length(path: list[tuple[float, float]]) -> float:
    """Total Euclidean length of a contour polyline."""
    total = 0.0
    for i in range(len(path) - 1):
        dx = path[i + 1][0] - path[i][0]
        dy = path[i + 1][1] - path[i][1]
        total += math.sqrt(dx * dx + dy * dy)
    return total


def extract_contours(
    seeds: list[tuple[float, float]],
    values: list[float],
    *,
    levels: list[float] | int | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    resolution: int = 80,
) -> list[dict]:
    """Trace contour lines from seed-associated scalar values.

    Interpolates values onto a regular grid using inverse distance
    weighting, then runs marching squares at each contour level.

    Parameters
    ----------
    seeds : list of (float, float)
        Voronoi seed coordinates.
    values : list of float
        Scalar value per seed (same length as *seeds*).
    levels : list of float, int, or None
        Explicit contour levels, number of auto-levels, or None (default 8).
    bounds : (x_min, x_max, y_min, y_max) or None
        Spatial extent.  Inferred from seeds if None.
    resolution : int
        Grid resolution (number of cells along the longer axis).

    Returns
    -------
    list of dict
        Each dict has keys ``level`` (float), ``paths`` (list of polylines),
        and ``total_length`` (float).
    """
    if len(seeds) != len(values):
        raise ValueError(
            f"seeds ({len(seeds)}) and values ({len(values)}) must have equal length"
        )
    if len(seeds) < 3:
        raise ValueError("Need at least 3 seed points")

    # Determine bounds
    if bounds is None:
        xs = [s[0] for s in seeds]
        ys = [s[1] for s in seeds]
        margin_x = (max(xs) - min(xs)) * 0.05 or 10.0
        margin_y = (max(ys) - min(ys)) * 0.05 or 10.0
        bounds = (min(xs) - margin_x, max(xs) + margin_x,
                  min(ys) - margin_y, max(ys) + margin_y)

    x_min, x_max, y_min, y_max = bounds
    width = x_max - x_min
    height = y_max - y_min
    if width <= 0 or height <= 0:
        raise ValueError("Bounds must have positive width and height")

    # Grid dimensions
    if width >= height:
        nx = resolution
        ny = max(2, int(resolution * height / width))
    else:
        ny = resolution
        nx = max(2, int(resolution * width / height))

    dx = width / nx
    dy = height / ny

    # Interpolate onto grid using IDW
    grid = _idw_grid(seeds, values, x_min, y_min, dx, dy, nx + 1, ny + 1)

    # Determine levels
    if levels is None:
        level_list = auto_levels(values, 8)
    elif isinstance(levels, int):
        level_list = auto_levels(values, levels)
    else:
        level_list = sorted(levels)

    # Extract contours at each level
    results = []
    for lev in level_list:
        paths = _march_squares(grid, lev, x_min, y_min, dx, dy)
        total = sum(contour_length(p) for p in paths)
        results.append({
            "level": lev,
            "paths": paths,
            "total_length": total,
        })

    return results


def _idw_grid(
    seeds: list[tuple[float, float]],
    values: list[float],
    x0: float, y0: float,
    dx: float, dy: float,
    cols: int, rows: int,
    power: float = 2.0,
) -> list[list[float]]:
    """Inverse-distance-weighted interpolation onto a regular grid."""
    grid: list[list[float]] = []
    for r in range(rows):
        row: list[float] = []
        y = y0 + r * dy
        for c in range(cols):
            x = x0 + c * dx
            # Check for coincident seed
            total_w = 0.0
            total_v = 0.0
            exact = None
            for k, (sx, sy) in enumerate(seeds):
                d = math.sqrt((x - sx) ** 2 + (y - sy) ** 2)
                if d < 1e-10:
                    exact = values[k]
                    break
                w = 1.0 / (d ** power)
                total_w += w
                total_v += w * values[k]
            if exact is not None:
                row.append(exact)
            elif total_w > 0:
                row.append(total_v / total_w)
            else:
                row.append(0.0)
        grid.append(row)
    return grid


def contour_to_geojson(contours: list[dict]) -> dict:
    """Convert contour results to a GeoJSON FeatureCollection.

    Parameters
    ----------
    contours : list of dict
        Output from :func:`extract_contours`.

    Returns
    -------
    dict
        GeoJSON FeatureCollection with MultiLineString geometries.
    """
    features = []
    for c in contours:
        coords = [[(p[0], p[1]) for p in path] for path in c["paths"]
                  if len(path) >= 2]
        if not coords:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "level": c["level"],
                "total_length": round(c["total_length"], 2),
                "path_count": len(coords),
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": coords,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def export_contour_svg(
    contours: list[dict],
    output_path: str,
    *,
    bounds: tuple[float, float, float, float] | None = None,
    seeds: list[tuple[float, float]] | None = None,
    width: int = 800,
    height: int | None = None,
    show_labels: bool = True,
    colormap: str = "viridis",
) -> str:
    """Render contour lines as an SVG file.

    Parameters
    ----------
    contours : list of dict
        Output from :func:`extract_contours`.
    output_path : str
        Path for the SVG file.
    bounds : (x_min, x_max, y_min, y_max) or None
        Spatial extent.  Inferred from contour paths if None.
    seeds : list of (float, float) or None
        If provided, drawn as small circles.
    width : int
        SVG width in pixels.
    height : int or None
        SVG height (auto-calculated from aspect ratio if None).
    show_labels : bool
        Label contour lines with their level value.
    colormap : str
        Color scheme name ('viridis', 'plasma', 'terrain', 'grayscale').

    Returns
    -------
    str
        Absolute path of the written SVG.
    """
    out = vormap.validate_output_path(output_path, allow_absolute=True)

    # Collect all points to infer bounds
    if bounds is None:
        all_x: list[float] = []
        all_y: list[float] = []
        for c in contours:
            for path in c["paths"]:
                for p in path:
                    all_x.append(p[0])
                    all_y.append(p[1])
        if seeds:
            for s in seeds:
                all_x.append(s[0])
                all_y.append(s[1])
        if not all_x:
            raise ValueError("No contour data to render")
        margin = max((max(all_x) - min(all_x)), (max(all_y) - min(all_y))) * 0.05
        bounds = (min(all_x) - margin, max(all_x) + margin,
                  min(all_y) - margin, max(all_y) + margin)

    x_min, x_max, y_min, y_max = bounds
    bw = x_max - x_min
    bh = y_max - y_min
    if height is None:
        height = max(100, int(width * bh / bw)) if bw > 0 else width

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height),
                     viewBox=f"{x_min} {y_min} {bw} {bh}")
    # White background
    ET.SubElement(svg, "rect", x=str(x_min), y=str(y_min),
                  width=str(bw), height=str(bh), fill="white")

    # Color mapping
    levels = [c["level"] for c in contours]
    lo = min(levels) if levels else 0
    hi = max(levels) if levels else 1

    for c in contours:
        color = _level_color(c["level"], lo, hi, colormap)
        for path in c["paths"]:
            if len(path) < 2:
                continue
            # Flip y for SVG (y grows downward)
            pts = [(p[0], y_min + y_max - p[1]) for p in path]
            d = "M " + " L ".join(f"{p[0]:.2f} {p[1]:.2f}" for p in pts)
            ET.SubElement(svg, "path", d=d, fill="none",
                          stroke=color, **{"stroke-width": "1.5"})
            if show_labels and len(pts) >= 2:
                mid = pts[len(pts) // 2]
                label = ET.SubElement(
                    svg, "text", x=f"{mid[0]:.1f}", y=f"{mid[1]:.1f}",
                    fill=color, **{"font-size": f"{max(8, bw / 60):.1f}",
                                   "font-family": "sans-serif"})
                label.text = f"{c['level']:.1f}"

    # Draw seeds
    if seeds:
        for s in seeds:
            sy = y_min + y_max - s[1]
            ET.SubElement(svg, "circle", cx=f"{s[0]:.2f}", cy=f"{sy:.2f}",
                          r=f"{bw / 200:.2f}", fill="#333")

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(out, xml_declaration=True, encoding="unicode")
    return out


def _level_color(level: float, lo: float, hi: float, cmap: str) -> str:
    """Map a scalar level to an RGB hex color."""
    if abs(hi - lo) < 1e-12:
        t = 0.5
    else:
        t = (level - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))

    if cmap == "plasma":
        r = int(min(255, 13 + t * 230 + (1 - t) * 10))
        g = int(max(0, min(255, 8 + t * 90 + (1 - t) * 2)))
        b = int(max(0, min(255, 135 + (1 - t) * 100 - t * 80)))
    elif cmap == "terrain":
        if t < 0.25:
            r, g, b = 50, int(100 + t * 600), int(200 - t * 400)
        elif t < 0.5:
            r, g, b = int((t - 0.25) * 800), 200, 50
        elif t < 0.75:
            r, g, b = 200, int(200 - (t - 0.5) * 400), 50
        else:
            r, g, b = int(200 + (t - 0.75) * 220), int(100 + (t - 0.75) * 600), int(50 + (t - 0.75) * 800)
        r, g, b = min(255, r), min(255, g), min(255, b)
    elif cmap == "grayscale":
        v = int(40 + t * 180)
        r, g, b = v, v, v
    else:  # viridis-like
        r = int(max(0, min(255, 68 + t * 187)))
        g = int(max(0, min(255, 1 + t * 180 + (1 - t) * 50)))
        b = int(max(0, min(255, 84 + (1 - t) * 90 + t * 30)))

    return f"#{r:02x}{g:02x}{b:02x}"


def generate_contours(
    seeds: list[tuple[float, float]],
    values: list[float],
    output_path: str,
    *,
    levels: list[float] | int | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    resolution: int = 80,
    geojson_path: str | None = None,
    show_labels: bool = True,
    colormap: str = "viridis",
) -> dict:
    """One-call pipeline: extract contours and export SVG + optional GeoJSON.

    Parameters
    ----------
    seeds, values, levels, bounds, resolution
        Passed to :func:`extract_contours`.
    output_path : str
        SVG output path.
    geojson_path : str or None
        If provided, also writes GeoJSON.
    show_labels : bool
        Label contour lines in SVG.
    colormap : str
        Color scheme for SVG rendering.

    Returns
    -------
    dict
        Summary with keys: ``levels`` (int), ``total_paths`` (int),
        ``total_length`` (float), ``svg_path`` (str), ``geojson_path`` (str or None).
    """
    contours = extract_contours(seeds, values, levels=levels,
                                bounds=bounds, resolution=resolution)

    svg_out = export_contour_svg(contours, output_path, bounds=bounds,
                                 seeds=seeds, show_labels=show_labels,
                                 colormap=colormap)

    gj_out = None
    if geojson_path:
        gj = contour_to_geojson(contours)
        gj_out = vormap.validate_output_path(geojson_path, allow_absolute=True)
        with open(gj_out, "w") as f:
            json.dump(gj, f, indent=2)

    total_paths = sum(len(c["paths"]) for c in contours)
    total_len = sum(c["total_length"] for c in contours)

    return {
        "levels": len(contours),
        "total_paths": total_paths,
        "total_length": round(total_len, 2),
        "svg_path": svg_out,
        "geojson_path": gj_out,
    }


# ---------- CLI ----------

def _cli():
    parser = argparse.ArgumentParser(
        description="Generate contour lines from Voronoi seed values.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python vormap_contour.py seeds.txt --values vals.txt -o contours.svg",
    )
    parser.add_argument("seeds", help="File with seed coordinates (x y per line)")
    parser.add_argument("--values", required=True,
                        help="File with one scalar value per line (same order as seeds)")
    parser.add_argument("-o", "--output", default="contours.svg",
                        help="SVG output path (default: contours.svg)")
    parser.add_argument("--geojson", default=None,
                        help="Optional GeoJSON output path")
    parser.add_argument("--levels", type=int, default=8,
                        help="Number of contour levels (default: 8)")
    parser.add_argument("--resolution", type=int, default=80,
                        help="Grid resolution (default: 80)")
    parser.add_argument("--colormap", default="viridis",
                        choices=["viridis", "plasma", "terrain", "grayscale"],
                        help="Color scheme (default: viridis)")
    parser.add_argument("--no-labels", action="store_true",
                        help="Disable contour level labels")
    parser.add_argument("--bounds", type=float, nargs=4,
                        metavar=("XMIN", "XMAX", "YMIN", "YMAX"),
                        help="Explicit spatial bounds")
    args = parser.parse_args()

    # Load seeds
    seeds_path = vormap.validate_input_path(args.seeds, allow_absolute=True)
    seeds: list[tuple[float, float]] = []
    with open(seeds_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                seeds.append((float(parts[0]), float(parts[1])))

    # Load values
    vals_path = vormap.validate_input_path(args.values, allow_absolute=True)
    values: list[float] = []
    with open(vals_path) as f:
        for line in f:
            line = line.strip()
            if line:
                values.append(float(line))

    bounds = tuple(args.bounds) if args.bounds else None

    result = generate_contours(
        seeds, values, args.output,
        levels=args.levels,
        bounds=bounds,
        resolution=args.resolution,
        geojson_path=args.geojson,
        show_labels=not args.no_labels,
        colormap=args.colormap,
    )

    print(f"Contours: {result['levels']} levels, {result['total_paths']} paths")
    print(f"Total length: {result['total_length']:.1f}")
    print(f"SVG: {result['svg_path']}")
    if result["geojson_path"]:
        print(f"GeoJSON: {result['geojson_path']}")


if __name__ == "__main__":
    _cli()
