"""Penrose Tiling Generator for Voronoi diagrams.

Generates aperiodic Penrose tilings (P2 kite-dart and P3 thin-thick rhombus)
via recursive subdivision, then optionally overlays Voronoi seeds at tile
centroids for hybrid spatial analysis.

Tiling types:
- **P2** (kite & dart) – classic Penrose kite-and-dart decomposition
- **P3** (rhombus)     – thin and thick rhombus tiling

Features:
- Recursive subdivision to arbitrary depth (1-10)
- SVG and JSON export
- Voronoi seed extraction from tile centroids
- Colormap support (golden, cool, warm, mono, rainbow)
- Optional tile labels and edge highlighting

CLI usage
---------
::

    python vormap_penrose.py P2 penrose.svg --depth 5 --size 800
    python vormap_penrose.py P3 penrose.json --depth 4 --format json
    python vormap_penrose.py P2 penrose.svg --colormap cool --labels
    python vormap_penrose.py P3 penrose.svg --depth 6 --seeds-out seeds.csv

"""

import argparse
import json
import math
import os
import sys

# Golden ratio
PHI = (1 + math.sqrt(5)) / 2
INV_PHI = 1.0 / PHI

# ── Colormaps ────────────────────────────────────────────────────────────

COLORMAPS = {
    "golden": {
        "kite": "#DAA520", "dart": "#FFD700",
        "thick": "#DAA520", "thin": "#FFD700",
    },
    "cool": {
        "kite": "#4A90D9", "dart": "#7EC8E3",
        "thick": "#4A90D9", "thin": "#7EC8E3",
    },
    "warm": {
        "kite": "#E74C3C", "dart": "#F39C12",
        "thick": "#E74C3C", "thin": "#F39C12",
    },
    "mono": {
        "kite": "#555555", "dart": "#999999",
        "thick": "#555555", "thin": "#999999",
    },
    "rainbow": {
        "kite": "#9B59B6", "dart": "#2ECC71",
        "thick": "#E67E22", "thin": "#3498DB",
    },
}

DEFAULT_COLORMAP = "golden"


# ── Triangle representation ──────────────────────────────────────────────
# Penrose subdivision works on triangles.  Each triangle is:
#   (kind, A, B, C)  where kind is an int 0-3 and A/B/C are (x, y) tuples.
#
# P2 (kite-dart) half-triangles:
#   0 = acute half (part of kite)
#   1 = obtuse half (part of dart)
#
# P3 (rhombus) half-triangles:
#   2 = thick half
#   3 = thin half

def _subdivide_p2(triangles):
    """One level of P2 (kite-dart) subdivision."""
    result = []
    for kind, a, b, c in triangles:
        if kind == 0:
            # Acute half -> split
            p = (a[0] + (b[0] - a[0]) * INV_PHI,
                 a[1] + (b[1] - a[1]) * INV_PHI)
            result.append((0, c, p, b))
            result.append((1, c, a, p))
        else:
            # Obtuse half -> split
            q = (b[0] + (a[0] - b[0]) * INV_PHI,
                 b[1] + (a[1] - b[1]) * INV_PHI)
            r = (b[0] + (c[0] - b[0]) * INV_PHI,
                 b[1] + (c[1] - b[1]) * INV_PHI)
            result.append((1, r, c, a))
            result.append((1, q, r, b))
            result.append((0, r, q, a))
    return result


def _subdivide_p3(triangles):
    """One level of P3 (rhombus) subdivision."""
    result = []
    for kind, a, b, c in triangles:
        if kind == 2:
            # Thick half
            p = (a[0] + (b[0] - a[0]) * INV_PHI,
                 a[1] + (b[1] - a[1]) * INV_PHI)
            result.append((2, c, p, b))
            result.append((3, c, a, p))
        else:
            # Thin half
            q = (b[0] + (a[0] - b[0]) * INV_PHI,
                 b[1] + (a[1] - b[1]) * INV_PHI)
            r = (b[0] + (c[0] - b[0]) * INV_PHI,
                 b[1] + (c[1] - b[1]) * INV_PHI)
            result.append((3, r, c, a))
            result.append((3, q, r, b))
            result.append((2, r, q, a))
    return result


def _initial_wheel(tiling_type, cx, cy, radius):
    """Create initial decagonal wheel of triangles."""
    triangles = []
    for i in range(10):
        angle1 = (2 * i - 1) * math.pi / 10
        angle2 = (2 * i + 1) * math.pi / 10
        b = (cx + radius * math.cos(angle1), cy + radius * math.sin(angle1))
        c = (cx + radius * math.cos(angle2), cy + radius * math.sin(angle2))
        center = (cx, cy)
        if tiling_type == "P2":
            if i % 2 == 0:
                triangles.append((0, center, c, b))
            else:
                triangles.append((0, center, b, c))
        else:  # P3
            if i % 2 == 0:
                triangles.append((2, center, c, b))
            else:
                triangles.append((2, center, b, c))
    return triangles


def generate_penrose(tiling_type="P2", depth=5, size=800):
    """Generate Penrose tiling triangles.

    Parameters
    ----------
    tiling_type : str
        'P2' for kite-dart, 'P3' for rhombus.
    depth : int
        Subdivision depth (1-10).
    size : float
        Canvas size in pixels.

    Returns
    -------
    list of (kind, A, B, C) triangles, plus metadata dict.
    """
    if tiling_type not in ("P2", "P3"):
        raise ValueError("tiling_type must be 'P2' or 'P3', got %r" % tiling_type)
    depth = max(1, min(10, int(depth)))

    cx, cy = size / 2, size / 2
    radius = size * 0.6

    triangles = _initial_wheel(tiling_type, cx, cy, radius)

    subdivide = _subdivide_p2 if tiling_type == "P2" else _subdivide_p3
    for _ in range(depth):
        triangles = subdivide(triangles)

    meta = {
        "tiling_type": tiling_type,
        "depth": depth,
        "size": size,
        "num_triangles": len(triangles),
    }
    return triangles, meta


def triangles_to_tiles(triangles, tiling_type="P2"):
    """Pair half-triangles back into full tiles (kite/dart or rhombus).

    Returns list of dicts with 'type', 'vertices' (4 points), 'centroid'.
    """
    # Group by matching shared edge (B-C midpoint + kind parity)
    # Simplified: just pair consecutive same-kind triangles
    tiles = []
    used = set()
    for i, (k1, a1, b1, c1) in enumerate(triangles):
        if i in used:
            continue
        # Find matching half
        best_j = None
        best_dist = float("inf")
        mid1 = ((b1[0] + c1[0]) / 2, (b1[1] + c1[1]) / 2)
        for j in range(i + 1, min(i + 50, len(triangles))):
            if j in used:
                continue
            k2, a2, b2, c2 = triangles[j]
            if k2 != k1:
                continue
            mid2 = ((b2[0] + c2[0]) / 2, (b2[1] + c2[1]) / 2)
            d = math.hypot(mid1[0] - mid2[0], mid1[1] - mid2[1])
            if d < best_dist:
                best_dist = d
                best_j = j
        if best_j is not None and best_dist < 1e-6:
            _, a2, b2, c2 = triangles[best_j]
            used.add(i)
            used.add(best_j)
            # Combine into quad
            pts = [a1, b1, a2, c1]
            cx = sum(p[0] for p in pts) / 4
            cy = sum(p[1] for p in pts) / 4
            if tiling_type == "P2":
                tile_type = "kite" if k1 == 0 else "dart"
            else:
                tile_type = "thick" if k1 == 2 else "thin"
            tiles.append({
                "type": tile_type,
                "vertices": pts,
                "centroid": (cx, cy),
            })
        else:
            # Unpaired triangle -> render as-is
            pts = [a1, b1, c1]
            cx = sum(p[0] for p in pts) / 3
            cy = sum(p[1] for p in pts) / 3
            if tiling_type == "P2":
                tile_type = "kite" if k1 == 0 else "dart"
            else:
                tile_type = "thick" if k1 == 2 else "thin"
            tiles.append({
                "type": tile_type,
                "vertices": pts,
                "centroid": (cx, cy),
            })
    return tiles


def extract_seeds(tiles):
    """Extract Voronoi seed points from tile centroids.

    Returns list of (x, y) tuples.
    """
    return [t["centroid"] for t in tiles]


# ── SVG export ───────────────────────────────────────────────────────────

def _svg_polygon(vertices, fill, stroke="#333", stroke_width=0.5, opacity=0.85):
    points = " ".join("%.2f,%.2f" % (x, y) for x, y in vertices)
    return '<polygon points="%s" fill="%s" stroke="%s" stroke-width="%.1f" opacity="%.2f"/>' % (
        points, fill, stroke, stroke_width, opacity
    )


def export_svg(tiles, meta, colormap_name=DEFAULT_COLORMAP, labels=False):
    """Render tiles to SVG string."""
    size = meta["size"]
    cmap = COLORMAPS.get(colormap_name, COLORMAPS[DEFAULT_COLORMAP])
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">' % (
            size, size, size, size),
        '<rect width="100%%" height="100%%" fill="#1a1a2e"/>',
    ]
    for tile in tiles:
        fill = cmap.get(tile["type"], "#888888")
        lines.append(_svg_polygon(tile["vertices"], fill))
        if labels:
            cx, cy = tile["centroid"]
            lines.append(
                '<text x="%.1f" y="%.1f" font-size="6" fill="white" '
                'text-anchor="middle" dominant-baseline="middle">%s</text>' % (
                    cx, cy, tile["type"][0].upper())
            )
    lines.append("</svg>")
    return "\n".join(lines)


def export_json(tiles, meta):
    """Export tiles and metadata as JSON."""
    serializable_tiles = []
    for t in tiles:
        serializable_tiles.append({
            "type": t["type"],
            "vertices": [list(v) for v in t["vertices"]],
            "centroid": list(t["centroid"]),
        })
    return json.dumps({
        "meta": meta,
        "tiles": serializable_tiles,
    }, indent=2)


def export_seeds_csv(tiles):
    """Export tile centroids as CSV (x,y)."""
    lines = ["x,y"]
    for t in tiles:
        lines.append("%.4f,%.4f" % t["centroid"])
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Penrose Tiling Generator — aperiodic P2/P3 tilings with SVG/JSON export"
    )
    parser.add_argument("type", choices=["P2", "P3"],
                        help="Tiling type: P2 (kite-dart) or P3 (rhombus)")
    parser.add_argument("output", help="Output file path (.svg or .json)")
    parser.add_argument("--depth", type=int, default=5,
                        help="Subdivision depth 1-10 (default: 5)")
    parser.add_argument("--size", type=int, default=800,
                        help="Canvas size in pixels (default: 800)")
    parser.add_argument("--colormap", choices=list(COLORMAPS.keys()),
                        default=DEFAULT_COLORMAP,
                        help="Color scheme (default: golden)")
    parser.add_argument("--labels", action="store_true",
                        help="Show tile type labels")
    parser.add_argument("--format", choices=["svg", "json"], default=None,
                        help="Output format (auto-detected from extension)")
    parser.add_argument("--seeds-out", default=None,
                        help="Export tile centroids as CSV seeds file")

    args = parser.parse_args(argv)

    # Determine format
    fmt = args.format
    if fmt is None:
        if args.output.lower().endswith(".json"):
            fmt = "json"
        else:
            fmt = "svg"

    print("Generating %s Penrose tiling (depth=%d, size=%d)..." % (
        args.type, args.depth, args.size))

    triangles, meta = generate_penrose(args.type, args.depth, args.size)
    tiles = triangles_to_tiles(triangles, args.type)
    meta["num_tiles"] = len(tiles)

    print("  %d triangles -> %d tiles" % (meta["num_triangles"], len(tiles)))

    if fmt == "json":
        content = export_json(tiles, meta)
    else:
        content = export_svg(tiles, meta, args.colormap, args.labels)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(content)
    print("  Written: %s" % args.output)

    if args.seeds_out:
        csv_content = export_seeds_csv(tiles)
        with open(args.seeds_out, "w", encoding="utf-8") as f:
            f.write(csv_content)
        print("  Seeds CSV: %s (%d points)" % (args.seeds_out, len(tiles)))

    print("Done.")


if __name__ == "__main__":
    main()
