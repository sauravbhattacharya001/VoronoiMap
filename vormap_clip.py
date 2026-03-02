"""Voronoi Region Clipping — clip regions to arbitrary boundaries.

Clips Voronoi regions to polygonal boundaries using the Sutherland-Hodgman
algorithm. Supports common boundary shapes (rectangles, circles, convex
polygons) and re-computes region statistics after clipping.

Usage::

    from vormap_clip import (
        clip_region, clip_all_regions, make_rectangle, make_circle,
        make_ellipse, ClipResult, ClipStats,
    )

    # Clip all regions to a circle
    boundary = make_circle(center=(500, 500), radius=400, segments=64)
    result = clip_all_regions(regions, data, boundary)
    print(result.summary)

    # CLI
    python vormap_clip.py data/points.txt --circle 500,500,400
    python vormap_clip.py data/points.txt --rect 100,100,900,900
    python vormap_clip.py data/points.txt --boundary boundary.txt --svg output.svg
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Polygon = List[Tuple[float, float]]
Point = Tuple[float, float]
Regions = Dict[Point, Polygon]


# ---------------------------------------------------------------------------
# Boundary generators
# ---------------------------------------------------------------------------

def make_rectangle(x_min: float, y_min: float, x_max: float, y_max: float) -> Polygon:
    """Create a rectangular boundary polygon (CCW winding)."""
    return [(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)]


def make_circle(center: Point, radius: float, segments: int = 64) -> Polygon:
    """Create a circular boundary approximated as a regular polygon."""
    cx, cy = center
    return [
        (cx + radius * math.cos(2 * math.pi * i / segments),
         cy + radius * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def make_ellipse(center: Point, rx: float, ry: float, segments: int = 64) -> Polygon:
    """Create an elliptical boundary."""
    cx, cy = center
    return [
        (cx + rx * math.cos(2 * math.pi * i / segments),
         cy + ry * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def make_regular_polygon(center: Point, radius: float, sides: int, rotation: float = 0.0) -> Polygon:
    """Create a regular polygon (hexagon, octagon, etc.)."""
    cx, cy = center
    return [
        (cx + radius * math.cos(2 * math.pi * i / sides + rotation),
         cy + radius * math.sin(2 * math.pi * i / sides + rotation))
        for i in range(sides)
    ]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

from vormap_geometry import polygon_area as _polygon_area


def _line_intersection(p1: Point, p2: Point, p3: Point, p4: Point) -> Point:
    """Compute intersection of line segments p1-p2 and p3-p4."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denom) < 1e-12:
        return ((x1 + x2) / 2, (y1 + y2) / 2)  # fallback for parallel lines
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def _is_inside(point: Point, edge_start: Point, edge_end: Point) -> bool:
    """Check if point is on the inside (left side) of a directed edge."""
    return ((edge_end[0] - edge_start[0]) * (point[1] - edge_start[1]) -
            (edge_end[1] - edge_start[1]) * (point[0] - edge_start[0])) >= 0


def point_in_polygon(point: Point, polygon: Polygon) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Sutherland-Hodgman clipper
# ---------------------------------------------------------------------------

def clip_polygon(subject: Polygon, clip: Polygon) -> Polygon:
    """Clip a subject polygon against a convex clip polygon.

    Uses the Sutherland-Hodgman algorithm. The clip polygon must be convex
    and have consistent winding (CCW recommended). Returns the clipped polygon
    vertices, or an empty list if the subject is entirely outside.
    """
    if not subject or not clip:
        return []

    output = list(subject)

    for i in range(len(clip)):
        if not output:
            return []

        edge_start = clip[i]
        edge_end = clip[(i + 1) % len(clip)]

        input_list = output
        output = []

        for j in range(len(input_list)):
            current = input_list[j]
            previous = input_list[j - 1]  # wraps to last element when j=0

            current_inside = _is_inside(current, edge_start, edge_end)
            previous_inside = _is_inside(previous, edge_start, edge_end)

            if current_inside:
                if not previous_inside:
                    output.append(_line_intersection(previous, current, edge_start, edge_end))
                output.append(current)
            elif previous_inside:
                output.append(_line_intersection(previous, current, edge_start, edge_end))

    return output


# ---------------------------------------------------------------------------
# Region clipping
# ---------------------------------------------------------------------------

def clip_region(vertices: Polygon, boundary: Polygon) -> Polygon:
    """Clip a single Voronoi region to a boundary polygon.

    Returns the clipped polygon vertices, or empty list if the region
    is entirely outside the boundary.
    """
    return clip_polygon(vertices, boundary)


def clip_all_regions(regions: Regions, data: list, boundary: Polygon,
                     *, remove_empty: bool = True) -> "ClipResult":
    """Clip all Voronoi regions to a boundary polygon.

    Args:
        regions: Dict of seed -> vertex list (from compute_regions).
        data: Original seed point data.
        boundary: Boundary polygon vertices (convex, CCW).
        remove_empty: If True, remove regions with no area after clipping.

    Returns:
        ClipResult with clipped regions, statistics, and metadata.
    """
    clipped: Regions = {}
    removed_seeds: list = []
    original_areas: dict = {}
    clipped_areas: dict = {}

    for seed, vertices in regions.items():
        original_areas[seed] = _polygon_area(vertices)
        result = clip_polygon(vertices, boundary)

        if result and len(result) >= 3:
            area = _polygon_area(result)
            if remove_empty and area < 1e-10:
                removed_seeds.append(seed)
            else:
                clipped[seed] = result
                clipped_areas[seed] = area
        else:
            removed_seeds.append(seed)

    # Check which seeds are inside the boundary
    seeds_inside = [s for s in data if point_in_polygon((s[0], s[1]), boundary)]
    seeds_outside = [s for s in data if not point_in_polygon((s[0], s[1]), boundary)]

    return ClipResult(
        regions=clipped,
        boundary=boundary,
        removed_seeds=removed_seeds,
        original_count=len(regions),
        clipped_count=len(clipped),
        seeds_inside_boundary=len(seeds_inside),
        seeds_outside_boundary=len(seeds_outside),
        original_areas=original_areas,
        clipped_areas=clipped_areas,
        boundary_area=_polygon_area(boundary),
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ClipStats:
    """Statistics about clipping results."""
    total_original_area: float
    total_clipped_area: float
    boundary_area: float
    area_retained_pct: float   # percentage of original area retained
    coverage_pct: float        # percentage of boundary area covered by regions
    regions_removed: int
    regions_retained: int
    min_area_ratio: float      # smallest clipped/original ratio
    max_area_ratio: float      # largest clipped/original ratio
    mean_area_ratio: float     # average clipped/original ratio


@dataclass
class ClipResult:
    """Complete result of a clipping operation."""
    regions: Regions
    boundary: Polygon
    removed_seeds: list
    original_count: int
    clipped_count: int
    seeds_inside_boundary: int
    seeds_outside_boundary: int
    original_areas: dict
    clipped_areas: dict
    boundary_area: float

    @property
    def stats(self) -> ClipStats:
        total_orig = sum(self.original_areas.values())
        total_clip = sum(self.clipped_areas.values())

        ratios: list[float] = []
        for seed, clipped_area in self.clipped_areas.items():
            orig = self.original_areas.get(seed, 0)
            if orig > 0:
                ratios.append(clipped_area / orig)

        return ClipStats(
            total_original_area=total_orig,
            total_clipped_area=total_clip,
            boundary_area=self.boundary_area,
            area_retained_pct=(total_clip / total_orig * 100) if total_orig > 0 else 0,
            coverage_pct=(total_clip / self.boundary_area * 100) if self.boundary_area > 0 else 0,
            regions_removed=self.original_count - self.clipped_count,
            regions_retained=self.clipped_count,
            min_area_ratio=min(ratios) if ratios else 0,
            max_area_ratio=max(ratios) if ratios else 0,
            mean_area_ratio=(sum(ratios) / len(ratios)) if ratios else 0,
        )

    @property
    def summary(self) -> str:
        s = self.stats
        lines = [
            "Clipping Summary",
            f"  Boundary: {len(self.boundary)} vertices, area={s.boundary_area:.1f}",
            f"  Regions:  {self.original_count} \u2192 {self.clipped_count} ({s.regions_removed} removed)",
            f"  Seeds:    {self.seeds_inside_boundary} inside, {self.seeds_outside_boundary} outside boundary",
            f"  Area:     {s.area_retained_pct:.1f}% of original retained",
            f"  Coverage: {s.coverage_pct:.1f}% of boundary covered",
        ]
        if s.min_area_ratio > 0:
            lines.append(
                f"  Ratios:   min={s.min_area_ratio:.3f}, max={s.max_area_ratio:.3f}, mean={s.mean_area_ratio:.3f}"
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        s = self.stats
        return {
            "original_count": self.original_count,
            "clipped_count": self.clipped_count,
            "removed_count": s.regions_removed,
            "seeds_inside": self.seeds_inside_boundary,
            "seeds_outside": self.seeds_outside_boundary,
            "boundary_area": round(s.boundary_area, 2),
            "total_original_area": round(s.total_original_area, 2),
            "total_clipped_area": round(s.total_clipped_area, 2),
            "area_retained_pct": round(s.area_retained_pct, 2),
            "coverage_pct": round(s.coverage_pct, 2),
            "min_area_ratio": round(s.min_area_ratio, 4),
            "max_area_ratio": round(s.max_area_ratio, 4),
            "mean_area_ratio": round(s.mean_area_ratio, 4),
        }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_clip_json(result: ClipResult, output_path: str) -> None:
    """Export clipping results as JSON."""
    import json
    data = result.to_dict()
    data["clipped_regions"] = [
        {
            "seed": list(seed),
            "vertices": [list(v) for v in verts],
            "area": round(_polygon_area(verts), 4),
        }
        for seed, verts in result.regions.items()
    ]
    data["boundary"] = [list(v) for v in result.boundary]
    from vormap import validate_output_path
    output_path = validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def export_clip_svg(result: ClipResult, output_path: str, *,
                    color_scheme: str = "earth",
                    show_boundary: bool = True,
                    show_seeds: bool = True,
                    show_removed: bool = False,
                    width: int = 800, height: int = 600) -> None:
    """Export clipped Voronoi diagram as SVG."""
    all_x = [v[0] for v in result.boundary]
    all_y = [v[1] for v in result.boundary]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    pad = max((max_x - min_x), (max_y - min_y)) * 0.05
    vb_x = min_x - pad
    vb_y = min_y - pad
    vb_w = (max_x - min_x) + 2 * pad
    vb_h = (max_y - min_y) + 2 * pad

    try:
        from vormap_viz import COLOR_SCHEMES
        colors = COLOR_SCHEMES.get(color_scheme, COLOR_SCHEMES.get("earth", []))
    except ImportError:
        colors = ["#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336",
                  "#00BCD4", "#FFEB3B", "#795548"]

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{vb_x} {vb_y} {vb_w} {vb_h}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]

    for i, (seed, verts) in enumerate(result.regions.items()):
        color = colors[i % len(colors)] if colors else "#ccc"
        points_str = " ".join(f"{v[0]},{v[1]}" for v in verts)
        lines.append(f'<polygon points="{points_str}" fill="{color}" '
                     f'fill-opacity="0.6" stroke="black" stroke-width="0.5"/>')

    if show_boundary:
        boundary_str = " ".join(f"{v[0]},{v[1]}" for v in result.boundary)
        lines.append(f'<polygon points="{boundary_str}" fill="none" '
                     f'stroke="red" stroke-width="2" stroke-dasharray="8,4"/>')

    if show_seeds:
        r = max(vb_w, vb_h) * 0.004
        for seed in result.regions:
            lines.append(f'<circle cx="{seed[0]}" cy="{seed[1]}" r="{r}" '
                         f'fill="black"/>')

    if show_removed:
        r = max(vb_w, vb_h) * 0.004
        for seed in result.removed_seeds:
            lines.append(f'<circle cx="{seed[0]}" cy="{seed[1]}" r="{r}" '
                         f'fill="gray" opacity="0.4"/>')

    lines.append('</svg>')

    from vormap import validate_output_path
    output_path = validate_output_path(output_path, allow_absolute=True)
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_boundary(filepath: str) -> Polygon:
    """Load boundary polygon from a file (one 'x y' per line)."""
    import os
    from vormap import validate_input_path

    resolved = validate_input_path(filepath, allow_absolute=True)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Boundary file not found: {filepath}")

    points: Polygon = []
    with open(resolved) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 2:
                points.append((float(parts[0]), float(parts[1])))

    if len(points) < 3:
        raise ValueError(f"Boundary must have at least 3 vertices, got {len(points)}")
    return points


def main(argv=None):
    """CLI entry point for clipping."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python vormap_clip.py",
        description="Clip Voronoi regions to a boundary polygon",
    )
    parser.add_argument("datafile", help="Input data file with seed points")
    parser.add_argument("--circle", metavar="CX,CY,R",
                        help="Clip to a circle (center_x,center_y,radius)")
    parser.add_argument("--rect", metavar="X1,Y1,X2,Y2",
                        help="Clip to a rectangle (min_x,min_y,max_x,max_y)")
    parser.add_argument("--boundary", metavar="FILE",
                        help="Clip to a custom polygon from a file (one x y per line)")
    parser.add_argument("--segments", type=int, default=64,
                        help="Circle approximation segments (default: 64)")
    parser.add_argument("--svg", metavar="FILE",
                        help="Export clipped diagram as SVG")
    parser.add_argument("--json", metavar="FILE",
                        help="Export clipping results as JSON")
    parser.add_argument("--color-scheme", default="earth",
                        help="SVG color scheme (default: earth)")
    parser.add_argument("--show-removed", action="store_true",
                        help="Show removed seeds in SVG output")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress summary output")

    args = parser.parse_args(argv)

    if not args.circle and not args.rect and not args.boundary:
        parser.error("Must specify --circle, --rect, or --boundary")

    import vormap
    from vormap_viz import compute_regions

    data = vormap.load_data(args.datafile)
    regions = compute_regions(data)

    if args.circle:
        parts = args.circle.split(",")
        if len(parts) != 3:
            parser.error("--circle expects CX,CY,R")
        cx, cy, r = float(parts[0]), float(parts[1]), float(parts[2])
        boundary = make_circle((cx, cy), r, segments=args.segments)
    elif args.rect:
        parts = args.rect.split(",")
        if len(parts) != 4:
            parser.error("--rect expects X1,Y1,X2,Y2")
        boundary = make_rectangle(float(parts[0]), float(parts[1]),
                                  float(parts[2]), float(parts[3]))
    else:
        boundary = load_boundary(args.boundary)

    result = clip_all_regions(regions, data, boundary)

    if not args.quiet:
        print(result.summary)

    if args.svg:
        export_clip_svg(result, args.svg,
                        color_scheme=args.color_scheme,
                        show_removed=args.show_removed)
        print(f"SVG written to {args.svg}")

    if args.json:
        export_clip_json(result, args.json)
        print(f"JSON written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
