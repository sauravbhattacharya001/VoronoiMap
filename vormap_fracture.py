"""Voronoi Fracture Simulator — generate realistic material fracture patterns.

Simulates fracture/shatter patterns (broken glass, cracked earth, ice)
using Voronoi tessellation with physics-inspired fragment properties:

1. Generate impact-biased seed points (denser near impact, sparser far).
2. Compute Voronoi regions as fracture fragments.
3. Assign fragment properties: area, mass, displacement vectors.
4. Export as SVG with crack lines, or as JSON fragment data.

Supports multiple fracture modes:
- **radial**: impact-based shattering (glass, windshield)
- **uniform**: even fracture (dried mud, ceramic)
- **directional**: stress-aligned cracks (ice, stone)
- **concentric**: ring-based fracture (bullet hole)

CLI usage
---------
::

    python vormap_fracture.py --mode radial --impact 500,300 --fragments 40 -o shatter.svg
    python vormap_fracture.py --mode uniform --fragments 25 --width 800 --height 600 -o cracks.svg
    python vormap_fracture.py --mode directional --angle 45 --fragments 30 -o ice.svg
    python vormap_fracture.py --mode concentric --impact 400,400 --rings 5 -o bullet.svg
    python vormap_fracture.py --mode radial --fragments 50 --json fragments.json
    python vormap_fracture.py --mode radial --fragments 80 --material glass -o glass.svg

"""

import argparse
import json
import math
import os
import random
from typing import List, Tuple, Optional, Dict, Any


# ── Optional dependency detection ──

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from scipy.spatial import Voronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Material presets ──

MATERIALS: Dict[str, Dict[str, Any]] = {
    "glass": {
        "crack_width": 1.5,
        "crack_color": "#1a1a2e",
        "fill_opacity": 0.08,
        "fill_base": "#b8d4e3",
        "edge_jitter": 0.3,
        "description": "Tempered glass shatter pattern",
    },
    "ceramic": {
        "crack_width": 2.0,
        "crack_color": "#3d2b1f",
        "fill_opacity": 0.05,
        "fill_base": "#f5e6d3",
        "edge_jitter": 0.1,
        "description": "Ceramic/pottery fracture",
    },
    "earth": {
        "crack_width": 3.0,
        "crack_color": "#5c4033",
        "fill_opacity": 0.12,
        "fill_base": "#c4a882",
        "edge_jitter": 0.5,
        "description": "Dried earth/mud cracks",
    },
    "ice": {
        "crack_width": 1.0,
        "crack_color": "#4a90b8",
        "fill_opacity": 0.04,
        "fill_base": "#e8f4fd",
        "edge_jitter": 0.2,
        "description": "Ice fracture pattern",
    },
    "stone": {
        "crack_width": 2.5,
        "crack_color": "#2f2f2f",
        "fill_opacity": 0.10,
        "fill_base": "#a0a0a0",
        "edge_jitter": 0.4,
        "description": "Stone/rock fracture",
    },
}


def _distance(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return math.sqrt((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2)


# ── Seed generators ──

def _radial_seeds(
    n: int,
    width: float,
    height: float,
    impact: Tuple[float, float],
    rng: random.Random,
) -> List[Tuple[float, float]]:
    """Generate seeds biased toward impact point (denser near center)."""
    seeds = []
    max_r = math.sqrt(width ** 2 + height ** 2) / 2
    for _ in range(n):
        # Use inverse-square distribution: more seeds near impact
        r = max_r * (rng.random() ** 0.5)
        theta = rng.uniform(0, 2 * math.pi)
        x = impact[0] + r * math.cos(theta)
        y = impact[1] + r * math.sin(theta)
        # Clamp to bounds with small margin
        x = max(1, min(width - 1, x))
        y = max(1, min(height - 1, y))
        seeds.append((x, y))
    return seeds


def _uniform_seeds(
    n: int, width: float, height: float, rng: random.Random,
) -> List[Tuple[float, float]]:
    """Uniformly distributed seeds with slight jitter for natural look."""
    seeds = []
    cols = max(1, int(math.sqrt(n * width / height)))
    rows = max(1, int(n / cols))
    cell_w = width / cols
    cell_h = height / rows
    for r in range(rows):
        for c in range(cols):
            if len(seeds) >= n:
                break
            cx = (c + 0.5) * cell_w + rng.uniform(-cell_w * 0.3, cell_w * 0.3)
            cy = (r + 0.5) * cell_h + rng.uniform(-cell_h * 0.3, cell_h * 0.3)
            cx = max(1, min(width - 1, cx))
            cy = max(1, min(height - 1, cy))
            seeds.append((cx, cy))
    # Fill remaining with random
    while len(seeds) < n:
        seeds.append((rng.uniform(1, width - 1), rng.uniform(1, height - 1)))
    return seeds[:n]


def _directional_seeds(
    n: int,
    width: float,
    height: float,
    angle_deg: float,
    rng: random.Random,
) -> List[Tuple[float, float]]:
    """Seeds stretched along a stress direction for directional fracture."""
    angle_rad = math.radians(angle_deg)
    seeds = []
    for _ in range(n):
        # Elongate distribution along the angle
        t = rng.gauss(0, 0.3)
        s = rng.gauss(0, 0.15)
        x = width / 2 + (t * math.cos(angle_rad) - s * math.sin(angle_rad)) * width
        y = height / 2 + (t * math.sin(angle_rad) + s * math.cos(angle_rad)) * height
        x = max(1, min(width - 1, x))
        y = max(1, min(height - 1, y))
        seeds.append((x, y))
    return seeds


def _concentric_seeds(
    n: int,
    width: float,
    height: float,
    impact: Tuple[float, float],
    rings: int,
    rng: random.Random,
) -> List[Tuple[float, float]]:
    """Seeds arranged in concentric rings for bullet-hole patterns."""
    seeds = []
    max_r = min(width, height) * 0.45
    per_ring = max(1, n // rings)
    for ring_i in range(rings):
        r = max_r * (ring_i + 1) / rings
        count = per_ring + (n - per_ring * rings if ring_i == rings - 1 else 0)
        for j in range(count):
            if len(seeds) >= n:
                break
            theta = (2 * math.pi * j / count) + rng.uniform(-0.2, 0.2)
            x = impact[0] + r * math.cos(theta) + rng.uniform(-r * 0.1, r * 0.1)
            y = impact[1] + r * math.sin(theta) + rng.uniform(-r * 0.1, r * 0.1)
            x = max(1, min(width - 1, x))
            y = max(1, min(height - 1, y))
            seeds.append((x, y))
    return seeds[:n]


# ── Pure-Python Voronoi (fallback without SciPy) ──

def _assign_voronoi_cells(
    seeds: List[Tuple[float, float]],
    width: float,
    height: float,
    resolution: int = 200,
) -> Dict[int, List[Tuple[float, float]]]:
    """Assign boundary points to Voronoi cells via pixel scanning.

    Returns dict mapping seed index → list of boundary (x, y) coords.
    Used as fallback when SciPy is not available.
    """
    step_x = width / resolution
    step_y = height / resolution
    grid: Dict[int, set] = {i: set() for i in range(len(seeds))}
    prev_row = [None] * resolution

    for ry in range(resolution):
        py = (ry + 0.5) * step_y
        prev_cell = None
        row_cells = []
        for rx in range(resolution):
            px = (rx + 0.5) * step_x
            # Find nearest seed
            best_i = 0
            best_d = float("inf")
            for i, (sx, sy) in enumerate(seeds):
                d = (px - sx) ** 2 + (py - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            row_cells.append(best_i)
            # Detect boundary pixels
            is_boundary = False
            if prev_cell is not None and prev_cell != best_i:
                is_boundary = True
            if prev_row[rx] is not None and prev_row[rx] != best_i:
                is_boundary = True
            if is_boundary:
                grid[best_i].add((round(px, 2), round(py, 2)))
                if prev_cell is not None and prev_cell != best_i:
                    grid[prev_cell].add((round(px, 2), round(py, 2)))
            prev_cell = best_i
        prev_row = row_cells

    # Convert sets to sorted point lists (convex hull order)
    result = {}
    for idx, pts in grid.items():
        if not pts:
            continue
        pt_list = list(pts)
        # Sort by angle from centroid for polygon ordering
        cx = sum(p[0] for p in pt_list) / len(pt_list)
        cy = sum(p[1] for p in pt_list) / len(pt_list)
        pt_list.sort(key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
        result[idx] = pt_list
    return result


def _scipy_voronoi_regions(
    seeds: List[Tuple[float, float]],
    width: float,
    height: float,
) -> Dict[int, List[Tuple[float, float]]]:
    """Compute Voronoi regions using SciPy, clipped to bounding box."""
    pts = np.array(seeds)
    # Mirror points for bounded Voronoi
    mirrored = np.concatenate([
        pts,
        np.column_stack([-pts[:, 0], pts[:, 1]]),
        np.column_stack([2 * width - pts[:, 0], pts[:, 1]]),
        np.column_stack([pts[:, 0], -pts[:, 1]]),
        np.column_stack([pts[:, 0], 2 * height - pts[:, 1]]),
    ])
    vor = Voronoi(mirrored)
    regions = {}
    for i in range(len(seeds)):
        region_idx = vor.point_region[i]
        region_verts = vor.regions[region_idx]
        if -1 in region_verts or not region_verts:
            continue
        verts = []
        for vi in region_verts:
            vx, vy = vor.vertices[vi]
            vx = max(0, min(width, vx))
            vy = max(0, min(height, vy))
            verts.append((round(vx, 2), round(vy, 2)))
        if len(verts) >= 3:
            regions[i] = verts
    return regions


# ── Fragment analysis ──

def _polygon_area(vertices: List[Tuple[float, float]]) -> float:
    """Shoelace formula for polygon area."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _polygon_centroid(vertices: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Compute centroid of a polygon."""
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    cx = sum(v[0] for v in vertices) / n
    cy = sum(v[1] for v in vertices) / n
    return (round(cx, 2), round(cy, 2))


def _jitter_edges(
    vertices: List[Tuple[float, float]],
    amount: float,
    rng: random.Random,
) -> List[Tuple[float, float]]:
    """Add natural jitter to polygon edges for organic crack look."""
    if amount <= 0:
        return vertices
    result = []
    for i, (x, y) in enumerate(vertices):
        jx = x + rng.uniform(-amount, amount)
        jy = y + rng.uniform(-amount, amount)
        result.append((round(jx, 2), round(jy, 2)))
    return result


# ── Main fracture function ──

def generate_fracture(
    width: float = 800,
    height: float = 600,
    fragments: int = 30,
    mode: str = "radial",
    impact: Optional[Tuple[float, float]] = None,
    angle: float = 0.0,
    rings: int = 5,
    material: str = "glass",
    seed: Optional[int] = None,
    edge_jitter: Optional[float] = None,
) -> Dict[str, Any]:
    """Generate a fracture pattern.

    Parameters
    ----------
    width, height : float
        Canvas dimensions.
    fragments : int
        Number of fracture fragments (Voronoi cells).
    mode : str
        Fracture mode: 'radial', 'uniform', 'directional', 'concentric'.
    impact : tuple or None
        Impact point (x, y) for radial/concentric modes. Defaults to center.
    angle : float
        Stress direction in degrees (for 'directional' mode).
    rings : int
        Number of concentric rings (for 'concentric' mode).
    material : str
        Material preset name (glass, ceramic, earth, ice, stone).
    seed : int or None
        Random seed for reproducibility.
    edge_jitter : float or None
        Override material's edge jitter amount.

    Returns
    -------
    dict
        Keys: 'fragments' (list of fragment dicts), 'metadata', 'seeds'.
    """
    rng = random.Random(seed)
    mat = MATERIALS.get(material, MATERIALS["glass"])

    if impact is None:
        impact = (width / 2, height / 2)

    jitter = edge_jitter if edge_jitter is not None else mat["edge_jitter"]

    # Generate seeds based on mode
    if mode == "radial":
        seeds = _radial_seeds(fragments, width, height, impact, rng)
    elif mode == "uniform":
        seeds = _uniform_seeds(fragments, width, height, rng)
    elif mode == "directional":
        seeds = _directional_seeds(fragments, width, height, angle, rng)
    elif mode == "concentric":
        seeds = _concentric_seeds(fragments, width, height, impact, rings, rng)
    else:
        raise ValueError(f"Unknown fracture mode: {mode!r}. "
                         f"Choose from: radial, uniform, directional, concentric")

    # Compute Voronoi regions
    if _HAS_SCIPY and _HAS_NUMPY:
        regions = _scipy_voronoi_regions(seeds, width, height)
    else:
        regions = _assign_voronoi_cells(seeds, width, height)

    # Build fragment data
    total_area = width * height
    frag_list = []
    for idx, verts in regions.items():
        jittered = _jitter_edges(verts, jitter, rng)
        area = _polygon_area(jittered)
        centroid = _polygon_centroid(jittered)
        # Displacement vector from impact
        dx = centroid[0] - impact[0]
        dy = centroid[1] - impact[1]
        dist = math.sqrt(dx ** 2 + dy ** 2) + 1e-9
        frag_list.append({
            "id": idx,
            "vertices": jittered,
            "area": round(area, 2),
            "area_pct": round(100 * area / total_area, 2) if total_area > 0 else 0,
            "centroid": centroid,
            "displacement": (round(dx, 2), round(dy, 2)),
            "distance_from_impact": round(dist, 2),
            "edge_count": len(jittered),
        })

    frag_list.sort(key=lambda f: f["distance_from_impact"])

    return {
        "metadata": {
            "width": width,
            "height": height,
            "mode": mode,
            "material": material,
            "material_props": mat,
            "impact": impact,
            "fragment_count": len(frag_list),
            "seed": seed,
        },
        "seeds": [(round(s[0], 2), round(s[1], 2)) for s in seeds],
        "fragments": frag_list,
    }


# ── SVG export ──

def fracture_to_svg(result: Dict[str, Any]) -> str:
    """Render fracture result as SVG string."""
    meta = result["metadata"]
    w, h = meta["width"], meta["height"]
    mat = meta["material_props"]
    crack_w = mat["crack_width"]
    crack_c = mat["crack_color"]
    fill_o = mat["fill_opacity"]
    fill_b = mat["fill_base"]

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
        f'  <rect width="{w}" height="{h}" fill="{fill_b}" opacity="0.3"/>',
    ]

    for frag in result["fragments"]:
        verts = frag["vertices"]
        if len(verts) < 3:
            continue
        points = " ".join(f"{v[0]},{v[1]}" for v in verts)
        # Vary fill opacity by distance from impact for depth effect
        max_dist = math.sqrt(w ** 2 + h ** 2)
        dist_ratio = min(1.0, frag["distance_from_impact"] / max_dist)
        opacity = fill_o * (1.0 + dist_ratio * 0.5)
        lines.append(
            f'  <polygon points="{points}" '
            f'fill="{fill_b}" fill-opacity="{opacity:.3f}" '
            f'stroke="{crack_c}" stroke-width="{crack_w}" '
            f'stroke-linejoin="round"/>'
        )

    # Draw impact marker for radial/concentric modes
    if meta["mode"] in ("radial", "concentric"):
        ix, iy = meta["impact"]
        lines.append(
            f'  <circle cx="{ix}" cy="{iy}" r="4" '
            f'fill="red" opacity="0.6"/>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


# ── JSON export ──

def fracture_to_json(result: Dict[str, Any]) -> str:
    """Export fracture data as JSON string."""
    return json.dumps(result, indent=2)


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(
        description="Voronoi Fracture Simulator — generate realistic fracture/shatter patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --mode radial --impact 500,300 --fragments 40 -o shatter.svg\n"
            "  %(prog)s --mode uniform --fragments 25 -o cracks.svg\n"
            "  %(prog)s --mode directional --angle 45 --fragments 30 -o ice.svg\n"
            "  %(prog)s --mode concentric --impact 400,400 --rings 5 -o bullet.svg\n"
            "  %(prog)s --mode radial --fragments 50 --json out.json\n"
            "  %(prog)s --mode radial --material earth --fragments 60 -o mud.svg\n"
        ),
    )
    parser.add_argument(
        "--mode", choices=["radial", "uniform", "directional", "concentric"],
        default="radial", help="Fracture mode (default: radial)",
    )
    parser.add_argument(
        "--fragments", type=int, default=30,
        help="Number of fracture fragments (default: 30)",
    )
    parser.add_argument(
        "--width", type=float, default=800, help="Canvas width (default: 800)",
    )
    parser.add_argument(
        "--height", type=float, default=600, help="Canvas height (default: 600)",
    )
    parser.add_argument(
        "--impact", type=str, default=None,
        help="Impact point as x,y (default: center)",
    )
    parser.add_argument(
        "--angle", type=float, default=0,
        help="Stress direction in degrees for directional mode (default: 0)",
    )
    parser.add_argument(
        "--rings", type=int, default=5,
        help="Concentric rings for concentric mode (default: 5)",
    )
    parser.add_argument(
        "--material", choices=list(MATERIALS.keys()), default="glass",
        help="Material preset (default: glass)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--edge-jitter", type=float, default=None,
        help="Edge jitter amount (overrides material default)",
    )
    parser.add_argument(
        "-o", "--output", type=str, default=None,
        help="Output SVG file path",
    )
    parser.add_argument(
        "--json", type=str, default=None, dest="json_output",
        help="Output JSON file path with fragment data",
    )
    parser.add_argument(
        "--list-materials", action="store_true",
        help="List available material presets and exit",
    )

    args = parser.parse_args()

    if args.list_materials:
        print("Available materials:")
        for name, props in MATERIALS.items():
            print(f"  {name:10s} — {props['description']}")
        return

    impact = None
    if args.impact:
        parts = args.impact.split(",")
        if len(parts) != 2:
            parser.error("--impact must be x,y (e.g., 400,300)")
        impact = (float(parts[0]), float(parts[1]))

    result = generate_fracture(
        width=args.width,
        height=args.height,
        fragments=args.fragments,
        mode=args.mode,
        impact=impact,
        angle=args.angle,
        rings=args.rings,
        material=args.material,
        seed=args.seed,
        edge_jitter=args.edge_jitter,
    )

    meta = result["metadata"]
    print(f"Fracture generated: {meta['fragment_count']} fragments "
          f"({meta['mode']} mode, {meta['material']} material)")

    if args.output:
        svg = fracture_to_svg(result)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"SVG written to {args.output}")

    if args.json_output:
        js = fracture_to_json(result)
        with open(args.json_output, "w", encoding="utf-8") as f:
            f.write(js)
        print(f"JSON written to {args.json_output}")

    if not args.output and not args.json_output:
        # Print summary to stdout
        print(f"\nCanvas: {meta['width']}×{meta['height']}")
        print(f"Impact: {meta['impact']}")
        print(f"\nTop 5 largest fragments:")
        by_area = sorted(result["fragments"], key=lambda f: f["area"], reverse=True)
        for frag in by_area[:5]:
            print(f"  Fragment {frag['id']:3d}: area={frag['area']:8.1f} "
                  f"({frag['area_pct']:5.2f}%) edges={frag['edge_count']} "
                  f"dist={frag['distance_from_impact']:.1f}")


if __name__ == "__main__":
    main()
