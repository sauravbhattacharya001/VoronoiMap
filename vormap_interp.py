"""Spatial interpolation using Voronoi natural neighbor weights.

Implements natural neighbor interpolation (Sibson interpolation) for
estimating scalar values at arbitrary query locations based on values
assigned to Voronoi seed points.  The interpolation weights are
proportional to the area of the Voronoi cell that would be "stolen"
from each existing seed if the query point were inserted.

Also provides inverse distance weighting (IDW) and nearest-neighbor
interpolation as simpler alternatives, plus a grid interpolation
function for generating continuous surface maps.

Functions
---------
- ``nearest_interp`` — Value of the nearest seed point.
- ``idw_interp`` — Inverse distance weighted interpolation.
- ``natural_neighbor_interp`` — Sibson natural neighbor interpolation.
- ``grid_interpolate`` — Interpolate over a regular grid for surface maps.
- ``export_surface_svg`` — SVG heatmap of interpolated surface.
- ``export_surface_csv`` — CSV grid of interpolated values.

Example::

    from vormap_interp import natural_neighbor_interp, grid_interpolate

    # Seed points with associated values (e.g. temperature)
    points = [(100, 200), (300, 400), (500, 100)]
    values = [25.0, 30.0, 22.0]

    # Interpolate at a query location
    v = natural_neighbor_interp(points, values, (250, 300))

    # Generate a surface grid
    grid = grid_interpolate(points, values, nx=50, ny=50,
                            bounds=(0, 600, 0, 500))
"""

from __future__ import annotations

import math
import csv
import xml.etree.ElementTree as ET

from vormap import validate_output_path, validate_input_path

try:
    from scipy.spatial import Voronoi as ScipyVoronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def nearest_interp(points, values, query):
    """Return the value of the nearest seed point."""
    if len(points) != len(values):
        raise ValueError("points and values must have the same length")
    if not points:
        raise ValueError("points must not be empty")
    best_d = float('inf')
    best_v = values[0]
    qx, qy = query
    for (px, py), v in zip(points, values):
        d = (px - qx) ** 2 + (py - qy) ** 2
        if d < best_d:
            best_d = d
            best_v = v
    return best_v


def idw_interp(points, values, query, power=2.0, epsilon=1e-12):
    """Inverse distance weighted interpolation."""
    if len(points) != len(values):
        raise ValueError("points and values must have the same length")
    if not points:
        raise ValueError("points must not be empty")
    qx, qy = query
    weights = []
    for (px, py), v in zip(points, values):
        d = math.sqrt((px - qx) ** 2 + (py - qy) ** 2)
        if d < epsilon:
            return v
        weights.append((1.0 / d ** power, v))
    w_sum = sum(w for w, _ in weights)
    return sum(w * v / w_sum for w, v in weights)


from vormap_geometry import polygon_area as _polygon_area


def _voronoi_cell_areas(points_array):
    """Compute finite Voronoi cell areas using scipy."""
    import numpy as np
    vor = ScipyVoronoi(points_array)
    areas = {}
    for idx, region_idx in enumerate(vor.point_region):
        region = vor.regions[region_idx]
        if -1 in region or len(region) == 0:
            areas[idx] = None
        else:
            verts = [vor.vertices[i] for i in region]
            if not verts:
                areas[idx] = None
                continue
            cx = sum(v[0] for v in verts) / len(verts)
            cy = sum(v[1] for v in verts) / len(verts)
            verts.sort(key=lambda v: math.atan2(v[1] - cy, v[0] - cx))
            areas[idx] = _polygon_area(verts)
    return areas


def natural_neighbor_interp(points, values, query, fallback_idw=True):
    """Sibson natural neighbor interpolation.

    Inserts the query point into the Voronoi diagram and computes
    interpolation weights from the area "stolen" from each neighbor's
    cell.  Requires scipy for Voronoi computation.
    """
    if len(points) != len(values):
        raise ValueError("points and values must have the same length")
    if len(points) < 3:
        raise ValueError("natural neighbor interpolation requires >= 3 points")
    if not _HAS_SCIPY:
        if fallback_idw:
            return idw_interp(points, values, query)
        raise ImportError("scipy is required for natural neighbor interpolation")

    import numpy as np
    qx, qy = query
    for i, (px, py) in enumerate(points):
        if abs(px - qx) < 1e-12 and abs(py - qy) < 1e-12:
            return values[i]

    pts = np.array(points, dtype=float)
    areas_orig = _voronoi_cell_areas(pts)
    pts_new = np.vstack([pts, [[qx, qy]]])
    areas_new = _voronoi_cell_areas(pts_new)

    query_area = areas_new.get(len(points))
    if query_area is None or query_area < 1e-15:
        return idw_interp(points, values, query)

    weights = {}
    for i in range(len(points)):
        orig = areas_orig.get(i)
        new = areas_new.get(i)
        if orig is not None and new is not None:
            stolen = orig - new
            if stolen > 1e-15:
                weights[i] = stolen

    if not weights:
        return idw_interp(points, values, query)

    w_sum = sum(weights.values())
    return sum(weights[i] * values[i] for i in weights) / w_sum


def _natural_neighbor_interp_precomputed(pts_array, areas_orig, values, query):
    """Natural neighbor interpolation with precomputed original areas.

    Avoids recomputing the original Voronoi diagram on every query,
    cutting the per-query cost roughly in half for grid interpolation.
    """
    import numpy as np
    qx, qy = query
    n = len(values)

    # Exact-point short-circuit
    for i in range(n):
        if abs(pts_array[i, 0] - qx) < 1e-12 and abs(pts_array[i, 1] - qy) < 1e-12:
            return values[i]

    pts_new = np.vstack([pts_array, [[qx, qy]]])
    areas_new = _voronoi_cell_areas(pts_new)

    query_area = areas_new.get(n)
    if query_area is None or query_area < 1e-15:
        return idw_interp(
            [(pts_array[i, 0], pts_array[i, 1]) for i in range(n)],
            values, query)

    weights = {}
    for i in range(n):
        orig = areas_orig.get(i)
        new = areas_new.get(i)
        if orig is not None and new is not None:
            stolen = orig - new
            if stolen > 1e-15:
                weights[i] = stolen

    if not weights:
        return idw_interp(
            [(pts_array[i, 0], pts_array[i, 1]) for i in range(n)],
            values, query)

    w_sum = sum(weights.values())
    return sum(weights[i] * values[i] for i in weights) / w_sum


def grid_interpolate(points, values, nx=50, ny=50, bounds=None,
                     method='natural', power=2.0):
    """Interpolate values over a regular grid."""
    if not points:
        raise ValueError("points must not be empty")
    from vormap import validate_grid_size
    validate_grid_size(nx, ny, caller="grid_interpolate")
    if bounds is None:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        pad_x = (max(xs) - min(xs)) * 0.1 or 10.0
        pad_y = (max(ys) - min(ys)) * 0.1 or 10.0
        bounds = (min(xs) - pad_x, max(xs) + pad_x,
                  min(ys) - pad_y, max(ys) + pad_y)

    xmin, xmax, ymin, ymax = bounds
    dx = (xmax - xmin) / max(nx - 1, 1)
    dy = (ymax - ymin) / max(ny - 1, 1)
    x_coords = [xmin + i * dx for i in range(nx)]
    y_coords = [ymin + j * dy for j in range(ny)]

    # For natural neighbor interpolation, precompute the original Voronoi
    # diagram once instead of rebuilding it for every grid cell.
    # On a 50x50 grid this eliminates 2,500 redundant Voronoi constructions.
    if method == 'natural' and _HAS_SCIPY and len(points) >= 3:
        import numpy as np
        pts_array = np.array(points, dtype=float)
        areas_orig = _voronoi_cell_areas(pts_array)
        interp_fn = lambda q: _natural_neighbor_interp_precomputed(
            pts_array, areas_orig, values, q)
    else:
        interp_fn = {
            'natural': lambda q: natural_neighbor_interp(points, values, q),
            'idw': lambda q: idw_interp(points, values, q, power=power),
            'nearest': lambda q: nearest_interp(points, values, q),
        }.get(method)

    if interp_fn is None:
        raise ValueError("method must be 'natural', 'idw', or 'nearest'")

    grid = []
    vmin, vmax = float('inf'), float('-inf')
    for y in y_coords:
        row = []
        for x in x_coords:
            v = interp_fn((x, y))
            row.append(v)
            if v < vmin: vmin = v
            if v > vmax: vmax = v
        grid.append(row)

    return {'grid': grid, 'xs': x_coords, 'ys': y_coords,
            'bounds': bounds, 'min_val': vmin, 'max_val': vmax}


def _value_to_color(val, vmin, vmax, ramp='viridis'):
    """Map a scalar value to an RGB color string."""
    t = 0.5 if vmax - vmin < 1e-15 else (val - vmin) / (vmax - vmin)
    t = max(0.0, min(1.0, t))
    ramps = {
        'viridis': [(0.0,(68,1,84)),(0.25,(59,82,139)),(0.5,(33,145,140)),(0.75,(94,201,98)),(1.0,(253,231,37))],
        'plasma': [(0.0,(13,8,135)),(0.25,(126,3,168)),(0.5,(204,71,120)),(0.75,(248,149,64)),(1.0,(240,249,33))],
        'hot_cold': [(0.0,(49,54,149)),(0.25,(116,173,209)),(0.5,(255,255,191)),(0.75,(244,109,67)),(1.0,(165,0,38))],
    }
    stops = ramps.get(ramp, ramps['viridis'])
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]; t1, c1 = stops[i + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            r = int(c0[0] + (c1[0] - c0[0]) * f)
            g = int(c0[1] + (c1[1] - c0[1]) * f)
            b = int(c0[2] + (c1[2] - c0[2]) * f)
            return f'rgb({r},{g},{b})'
    return f'rgb({stops[-1][1][0]},{stops[-1][1][1]},{stops[-1][1][2]})'


def export_surface_svg(grid_result, output_path, width=800, height=600,
                       ramp='viridis', title=None):
    """Export interpolated surface as an SVG heatmap."""
    output_path = validate_output_path(output_path, allow_absolute=True)
    grid = grid_result['grid']
    ny = len(grid)
    nx = len(grid[0]) if ny > 0 else 0
    vmin, vmax = grid_result['min_val'], grid_result['max_val']
    margin = 40
    draw_w = width - 2 * margin
    draw_h = height - 2 * margin - (30 if title else 0)
    cell_w = draw_w / max(nx, 1)
    cell_h = draw_h / max(ny, 1)
    y_offset = margin + (30 if title else 0)

    svg = ET.Element('svg', xmlns='http://www.w3.org/2000/svg',
                     width=str(width), height=str(height))
    ET.SubElement(svg, 'rect', width=str(width), height=str(height), fill='white')
    if title:
        t = ET.SubElement(svg, 'text', x=str(width // 2), y='25', fill='#333')
        t.set('text-anchor', 'middle'); t.set('font-family', 'sans-serif')
        t.set('font-size', '16'); t.set('font-weight', 'bold'); t.text = title

    for j, row in enumerate(grid):
        for i, val in enumerate(row):
            ET.SubElement(svg, 'rect',
                          x=str(margin + i * cell_w), y=str(y_offset + j * cell_h),
                          width=str(cell_w + 0.5), height=str(cell_h + 0.5),
                          fill=_value_to_color(val, vmin, vmax, ramp))

    bar_y = y_offset + draw_h + 10
    bar_w = draw_w * 0.6
    bar_x = margin + (draw_w - bar_w) / 2
    for i in range(int(bar_w)):
        c = _value_to_color(vmin + (vmax - vmin) * i / bar_w, vmin, vmax, ramp)
        ET.SubElement(svg, 'rect', x=str(bar_x + i), y=str(bar_y), width='1', height='12', fill=c)
    for label_val, anchor in [(vmin, 'start'), (vmax, 'end'), ((vmin + vmax) / 2, 'middle')]:
        lx = bar_x + bar_w * (label_val - vmin) / max(vmax - vmin, 1e-15)
        t = ET.SubElement(svg, 'text', x=str(lx), y=str(bar_y + 24), fill='#666')
        t.set('text-anchor', anchor); t.set('font-family', 'sans-serif'); t.set('font-size', '10')
        t.text = f'{label_val:.2f}'

    tree = ET.ElementTree(svg)
    ET.indent(tree, space='  ')
    tree.write(output_path, xml_declaration=True, encoding='unicode')


def export_surface_csv(grid_result, output_path):
    """Export interpolated surface as a CSV grid."""
    grid = grid_result['grid']
    xs, ys = grid_result['xs'], grid_result['ys']
    output_path = validate_output_path(output_path, allow_absolute=True)
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['y\\x'] + [f'{x:.4f}' for x in xs])
        for j, row in enumerate(grid):
            writer.writerow([f'{ys[j]:.4f}'] + [f'{v:.6f}' for v in row])


def run_interp_cli(args, data):
    """Execute interpolation commands from CLI args."""
    interp_path = validate_input_path(args.interp_values, allow_absolute=True)
    with open(interp_path, 'r') as f:
        raw = [line.strip() for line in f if line.strip()]
    values = [float(v) for v in raw]
    if len(values) != len(data):
        raise ValueError(f"Number of values ({len(values)}) != seed points ({len(data)})")

    method = getattr(args, 'interp_method', 'natural')
    nx = getattr(args, 'interp_nx', 50)
    ny = getattr(args, 'interp_ny', 50)
    power = getattr(args, 'interp_power', 2.0)
    ramp = getattr(args, 'interp_ramp', 'viridis')

    if args.interp_query:
        parts = args.interp_query.split(',')
        qx, qy = float(parts[0]), float(parts[1])
        fn = {'natural': lambda q: natural_neighbor_interp(data, values, q),
              'idw': lambda q: idw_interp(data, values, q, power=power),
              'nearest': lambda q: nearest_interp(data, values, q)}[method]
        print(f'Interpolated value at ({qx}, {qy}): {fn((qx, qy)):.6f}')
        print(f'Method: {method}')

    if args.interp_surface_svg or args.interp_surface_csv:
        print(f'Generating {nx}x{ny} interpolation grid (method={method})...')
        grid_result = grid_interpolate(data, values, nx=nx, ny=ny,
                                       method=method, power=power)
        print(f'Value range: {grid_result["min_val"]:.4f} to {grid_result["max_val"]:.4f}')
        if args.interp_surface_svg:
            export_surface_svg(grid_result, args.interp_surface_svg, ramp=ramp,
                               title=f'Interpolated Surface ({method}, {nx}x{ny})')
            print(f'Surface SVG saved to {args.interp_surface_svg}')
        if args.interp_surface_csv:
            export_surface_csv(grid_result, args.interp_surface_csv)
            print(f'Surface CSV saved to {args.interp_surface_csv}')
