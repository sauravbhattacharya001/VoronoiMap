"""Voronoi 3D Mesh Exporter — extrude Voronoi cells into 3D geometry.

Generates 3D meshes from Voronoi diagrams by extruding each cell polygon
vertically based on its area, point density, or a custom height value.
Exports to OBJ and binary STL formats for use in 3D printing, game engines,
or visualization tools like Blender/MeshLab.

CLI usage
---------
::

    python vormap_mesh3d.py points.txt mesh.obj
    python vormap_mesh3d.py points.txt mesh.stl --height-mode density --scale 50
    python vormap_mesh3d.py data.csv model.obj --height-mode uniform --height 10
    python vormap_mesh3d.py pts.json out.obj --height-mode area --scale 0.01 --base 1

"""

import argparse
import math
import os
import struct
import sys
import warnings

import vormap
from vormap_utils import polygon_centroid_mean as _polygon_centroid


# ── Voronoi cell polygon tracer ─────────────────────────────────────


def _trace_cell_polygon(data, dlng, dlat, max_verts=200):
    """Trace the polygon boundary of a single Voronoi cell.

    Returns list of (x, y) vertices in order, or None if tracing fails.
    """
    try:
        elng, elat = vormap.get_NN(data, dlng, dlat)
    except Exception:
        return None

    alng, alat = vormap.mid_point(dlng, dlat, elng, elat)
    dirn = vormap.perp_dir(elng, elat, dlng, dlat)

    ag = [alng]
    at = [alat]
    i = 0

    try:
        while True:
            ag.append(0)
            at.append(0)

            a_g, a_t = vormap.find_a1(data, ag[i], at[i], dlng, dlat, dirn)
            if vormap.get_NN(data, a_g, a_t) == (dlng, dlat):
                ag[i + 1] = a_g
                at[i + 1] = a_t
            else:
                return None

            dirn1 = vormap.new_dir(data, ag[i], at[i], ag[i + 1], at[i + 1],
                                   dlng, dlat)

            if i > 2:
                if vormap._slopes_equal(dirn, dirn1):
                    break
                fin_isect = vormap.isect(
                    ag[i + 1], at[i + 1], ag[i], at[i], elng, elat, dlng, dlat)
                if fin_isect != (-1, -1):
                    break
                if i >= max_verts:
                    break

            dirn = dirn1
            i += 1
    except Exception:
        if len(ag) >= 3:
            pass  # use what we have
        else:
            return None

    return list(zip(ag, at))


# ── Height calculation ───────────────────────────────────────────────


def _compute_heights(cells, mode="area", scale=1.0, base=0.0, uniform_h=5.0):
    """Compute extrusion height for each cell.

    Parameters
    ----------
    cells : list of dict
        Each dict has 'polygon' (list of (x,y)) and 'area' (float).
    mode : str
        'area' — height proportional to cell area.
        'density' — height inversely proportional to area (dense = tall).
        'uniform' — all cells same height.
    scale : float
        Multiplier for computed heights.
    base : float
        Minimum height added to all cells.
    uniform_h : float
        Height when mode is 'uniform'.

    Returns
    -------
    list of float
    """
    if mode == "uniform":
        return [base + uniform_h * scale] * len(cells)

    areas = [c["area"] for c in cells]
    max_area = max(areas) if areas else 1.0
    if max_area == 0:
        max_area = 1.0

    heights = []
    for a in areas:
        if mode == "density":
            # Inverse — smaller area = taller
            h = (1.0 - a / max_area) * 10.0
        else:  # area
            h = (a / max_area) * 10.0
        heights.append(base + h * scale)

    return heights


# ── Triangulation ────────────────────────────────────────────────────


def _fan_triangulate(polygon):
    """Simple fan triangulation for a convex polygon.

    Returns list of (i, j, k) index triples.
    """
    n = len(polygon)
    if n < 3:
        return []
    tris = []
    for i in range(1, n - 1):
        tris.append((0, i, i + 1))
    return tris


## _polygon_centroid imported from vormap_utils


def _sort_polygon_ccw(polygon):
    """Sort polygon vertices counter-clockwise around centroid."""
    cx, cy = _polygon_centroid(polygon)
    return sorted(polygon, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))


# ── OBJ Export ───────────────────────────────────────────────────────


def export_obj(cells, heights, filepath):
    """Write a Wavefront OBJ file with extruded Voronoi cells.

    Each cell becomes a closed 3D solid (top face, bottom face, side walls).
    """
    filepath = vormap.validate_output_path(filepath, allow_absolute=True)

    lines = ["# VoronoiMap 3D Mesh Export", "# Cells: %d" % len(cells), ""]
    vertex_offset = 1  # OBJ is 1-indexed

    for idx, cell in enumerate(cells):
        poly = _sort_polygon_ccw(cell["polygon"])
        n = len(poly)
        if n < 3:
            continue

        h = heights[idx]
        lines.append("o cell_%d" % idx)

        # Bottom vertices (z=0)
        for x, y in poly:
            lines.append("v %.6f %.6f %.6f" % (x, y, 0.0))
        # Top vertices (z=h)
        for x, y in poly:
            lines.append("v %.6f %.6f %.6f" % (x, y, h))

        # Bottom face (reversed winding for outward normal downward)
        bottom = " ".join(str(vertex_offset + n - 1 - i) for i in range(n))
        lines.append("f " + bottom)

        # Top face
        top = " ".join(str(vertex_offset + n + i) for i in range(n))
        lines.append("f " + top)

        # Side walls
        for i in range(n):
            j = (i + 1) % n
            b0 = vertex_offset + i
            b1 = vertex_offset + j
            t0 = vertex_offset + n + i
            t1 = vertex_offset + n + j
            lines.append("f %d %d %d %d" % (b0, b1, t1, t0))

        vertex_offset += 2 * n
        lines.append("")

    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    return filepath


# ── STL Export ───────────────────────────────────────────────────────


def _normal(v0, v1, v2):
    """Compute face normal from three vertices."""
    ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    vx, vy, vz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length > 0:
        nx /= length
        ny /= length
        nz /= length
    return nx, ny, nz


def export_stl(cells, heights, filepath):
    """Write a binary STL file with extruded Voronoi cells."""
    filepath = vormap.validate_output_path(filepath, allow_absolute=True)

    triangles = []

    for idx, cell in enumerate(cells):
        poly = _sort_polygon_ccw(cell["polygon"])
        n = len(poly)
        if n < 3:
            continue

        h = heights[idx]
        bottom = [(x, y, 0.0) for x, y in poly]
        top = [(x, y, h) for x, y in poly]

        # Bottom face triangles (reversed winding)
        for i in range(1, n - 1):
            triangles.append((bottom[0], bottom[n - i], bottom[n - 1 - i]))

        # Top face triangles
        for i in range(1, n - 1):
            triangles.append((top[0], top[i], top[i + 1]))

        # Side wall triangles
        for i in range(n):
            j = (i + 1) % n
            b0, b1 = bottom[i], bottom[j]
            t0, t1 = top[i], top[j]
            triangles.append((b0, b1, t1))
            triangles.append((b0, t1, t0))

    # Write binary STL
    with open(filepath, "wb") as f:
        f.write(b"\0" * 80)  # header
        f.write(struct.pack("<I", len(triangles)))
        for v0, v1, v2 in triangles:
            n = _normal(v0, v1, v2)
            f.write(struct.pack("<fff", *n))
            f.write(struct.pack("<fff", *v0))
            f.write(struct.pack("<fff", *v1))
            f.write(struct.pack("<fff", *v2))
            f.write(struct.pack("<H", 0))  # attribute byte count

    return filepath


# ── JSON summary export ──────────────────────────────────────────────


def export_summary_json(cells, heights, filepath):
    """Write a JSON summary of the 3D mesh (cell count, bounds, stats)."""
    import json
    filepath = vormap.validate_output_path(filepath, allow_absolute=True)

    all_x = []
    all_y = []
    for c in cells:
        for x, y in c["polygon"]:
            all_x.append(x)
            all_y.append(y)

    summary = {
        "cells": len(cells),
        "triangles_approx": sum(
            max(0, (len(c["polygon"]) - 2) * 2 + len(c["polygon"]) * 2)
            for c in cells
        ),
        "height_range": [round(min(heights), 4), round(max(heights), 4)]
            if heights else [0, 0],
        "bounds": {
            "x": [round(min(all_x), 4), round(max(all_x), 4)] if all_x else [0, 0],
            "y": [round(min(all_y), 4), round(max(all_y), 4)] if all_y else [0, 0],
        },
    }

    with open(filepath, "w") as f:
        json.dump(summary, f, indent=2)
    return filepath


# ── Main pipeline ────────────────────────────────────────────────────


def generate_mesh(input_file, output_file, height_mode="area", scale=1.0,
                  base=0.0, uniform_height=5.0, sample=None, seed=None,
                  json_summary=False):
    """Generate a 3D mesh from a Voronoi point dataset.

    Parameters
    ----------
    input_file : str
        Path to point data (txt/csv/json/geojson).
    output_file : str
        Output path (.obj or .stl).
    height_mode : str
        'area', 'density', or 'uniform'.
    scale : float
        Height multiplier.
    base : float
        Minimum extrusion height.
    uniform_height : float
        Height when mode is 'uniform'.
    sample : int or None
        If set, randomly sample this many points from the dataset.
    seed : int or None
        Random seed for sampling.
    json_summary : bool
        If True, write a .json summary alongside the mesh.

    Returns
    -------
    dict
        Statistics: cells traced, failed, output path.
    """
    import random as rng

    data = vormap.load_data(input_file)
    points = list(data)

    if sample and sample < len(points):
        r = rng.Random(seed)
        points = r.sample(points, sample)

    # Trace polygons
    cells = []
    failed = 0
    for dlng, dlat in points:
        poly = _trace_cell_polygon(data, dlng, dlat)
        if poly and len(poly) >= 3:
            area = vormap.polygon_area(
                [p[0] for p in poly], [p[1] for p in poly])
            cells.append({"polygon": poly, "area": area, "seed": (dlng, dlat)})
        else:
            failed += 1

    if not cells:
        raise RuntimeError("No valid Voronoi cells could be traced.")

    heights = _compute_heights(cells, mode=height_mode, scale=scale,
                               base=base, uniform_h=uniform_height)

    ext = os.path.splitext(output_file)[1].lower()
    if ext == ".stl":
        out_path = export_stl(cells, heights, output_file)
    else:
        out_path = export_obj(cells, heights, output_file)

    stats = {
        "cells": len(cells),
        "failed": failed,
        "output": out_path,
        "format": ext.lstrip(".").upper() or "OBJ",
        "height_mode": height_mode,
    }

    if json_summary:
        json_path = os.path.splitext(output_file)[0] + "_summary.json"
        export_summary_json(cells, heights, json_path)
        stats["summary"] = json_path

    return stats


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="VoronoiMap 3D Mesh Exporter — extrude Voronoi cells "
                    "into OBJ/STL 3D geometry.")
    parser.add_argument("input", help="Input point file (txt/csv/json/geojson)")
    parser.add_argument("output", help="Output mesh file (.obj or .stl)")
    parser.add_argument("--height-mode", choices=["area", "density", "uniform"],
                        default="area",
                        help="How to compute cell heights (default: area)")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="Height multiplier (default: 1.0)")
    parser.add_argument("--base", type=float, default=0.0,
                        help="Minimum base height for all cells (default: 0)")
    parser.add_argument("--height", type=float, default=5.0,
                        help="Uniform height when --height-mode=uniform")
    parser.add_argument("--sample", type=int, default=None,
                        help="Randomly sample N points from the dataset")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for sampling")
    parser.add_argument("--json", action="store_true",
                        help="Also export a JSON summary file")

    args = parser.parse_args()

    print("VoronoiMap 3D Mesh Exporter")
    print("=" * 40)
    print("Input:       %s" % args.input)
    print("Output:      %s" % args.output)
    print("Height mode: %s" % args.height_mode)
    print("Scale:       %s" % args.scale)
    print()

    stats = generate_mesh(
        args.input, args.output,
        height_mode=args.height_mode,
        scale=args.scale,
        base=args.base,
        uniform_height=args.height,
        sample=args.sample,
        seed=args.seed,
        json_summary=args.json,
    )

    print("Done!")
    print("  Cells exported: %d" % stats["cells"])
    print("  Failed traces:  %d" % stats["failed"])
    print("  Format:         %s" % stats["format"])
    print("  Output:         %s" % stats["output"])
    if "summary" in stats:
        print("  Summary:        %s" % stats["summary"])


if __name__ == "__main__":
    main()
