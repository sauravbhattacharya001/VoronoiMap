"""Coverage Analyzer — service area coverage, gaps, and optimal site placement.

Analyzes how well a set of Voronoi sites "cover" a region within a given
service radius.  Useful for facility planning (hospitals, fire stations,
cell towers, delivery hubs) where every point in the area should be within
reach of at least one site.

Key metrics:

- **Coverage ratio**: fraction of the area within service radius of ≥1 site
- **Redundancy**: fraction of covered area served by ≥2 sites (overlap)
- **Gap detection**: identifies uncovered zones and their centroids
- **Optimal site**: suggests where to place the next site to maximize coverage

Usage (Python API)::

    import vormap
    from vormap_coverage import coverage_analysis, suggest_site

    data = vormap.load_data("datauni5.txt")
    points = [(d[0], d[1]) for d in data]

    result = coverage_analysis(points, radius=150.0, resolution=50)
    print(f"Coverage: {result.coverage_ratio:.1%}")
    print(f"Redundancy: {result.redundancy_ratio:.1%}")
    print(f"Gaps found: {len(result.gaps)}")
    for gap in result.gaps[:3]:
        print(f"  Gap at ({gap.centroid_x:.1f}, {gap.centroid_y:.1f}), "
              f"area ≈ {gap.area:.0f}")

    # Where should the next site go?
    suggestion = suggest_site(points, radius=150.0, resolution=50)
    print(f"Place next site at ({suggestion.x:.1f}, {suggestion.y:.1f})")
    print(f"Would cover {suggestion.new_cells} additional cells")

CLI::

    python vormap_coverage.py data/datauni5.txt --radius 150
    python vormap_coverage.py data/datauni5.txt --radius 150 --resolution 80
    python vormap_coverage.py data/datauni5.txt --radius 150 --json coverage.json
    python vormap_coverage.py data/datauni5.txt --radius 150 --csv coverage.csv
    python vormap_coverage.py data/datauni5.txt --radius 150 --heatmap coverage.svg
"""


import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from vormap_utils import euclidean_xy as _euclidean

from vormap import IND_S, IND_N, IND_W, IND_E, validate_output_path
from vormap_utils import euclidean_coords as _euclidean


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Gap:
    """An uncovered zone identified by grid sampling."""
    centroid_x: float
    centroid_y: float
    area: float  # approximate area (cell_size² × num_cells)
    cell_count: int
    cells: List[Tuple[int, int]] = field(default_factory=list, repr=False)


@dataclass
class SiteCoverage:
    """Per-site coverage statistics."""
    x: float
    y: float
    cells_covered: int  # grid cells uniquely or jointly covered
    exclusive_cells: int  # cells only this site covers


@dataclass
class SiteSuggestion:
    """Suggested placement for a new site."""
    x: float
    y: float
    new_cells: int  # additional cells that would be covered
    new_coverage_ratio: float  # projected coverage with this site


@dataclass
class CoverageResult:
    """Full coverage analysis result."""
    total_cells: int
    covered_cells: int
    uncovered_cells: int
    coverage_ratio: float
    multi_covered_cells: int  # cells covered by ≥2 sites
    redundancy_ratio: float  # multi_covered / covered (0 if nothing covered)
    avg_depth: float  # average number of covering sites per covered cell
    max_depth: int  # maximum number of sites covering any single cell
    radius: float
    resolution: int
    cell_size: float
    bounds: Tuple[float, float, float, float]  # (west, south, east, north)
    gaps: List[Gap]
    per_site: List[SiteCoverage]
    suggestion: Optional[SiteSuggestion]

    def to_dict(self) -> Dict:
        return {
            "total_cells": self.total_cells,
            "covered_cells": self.covered_cells,
            "uncovered_cells": self.uncovered_cells,
            "coverage_ratio": round(self.coverage_ratio, 4),
            "multi_covered_cells": self.multi_covered_cells,
            "redundancy_ratio": round(self.redundancy_ratio, 4),
            "avg_depth": round(self.avg_depth, 3),
            "max_depth": self.max_depth,
            "radius": self.radius,
            "resolution": self.resolution,
            "cell_size_x": round(self.cell_size, 4),
            "bounds": {
                "west": self.bounds[0],
                "south": self.bounds[1],
                "east": self.bounds[2],
                "north": self.bounds[3],
            },
            "gaps": [
                {
                    "centroid_x": round(g.centroid_x, 2),
                    "centroid_y": round(g.centroid_y, 2),
                    "area": round(g.area, 2),
                    "cell_count": g.cell_count,
                }
                for g in self.gaps
            ],
            "per_site": [
                {
                    "x": round(s.x, 2),
                    "y": round(s.y, 2),
                    "cells_covered": s.cells_covered,
                    "exclusive_cells": s.exclusive_cells,
                }
                for s in self.per_site
            ],
            "suggestion": (
                {
                    "x": round(self.suggestion.x, 2),
                    "y": round(self.suggestion.y, 2),
                    "new_cells": self.suggestion.new_cells,
                    "new_coverage_ratio": round(
                        self.suggestion.new_coverage_ratio, 4
                    ),
                }
                if self.suggestion
                else None
            ),
        }


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------


def _flood_fill(grid, rows, cols, r, c, visited):
    """Flood-fill connected uncovered cells.  Returns list of (r, c)."""
    stack = [(r, c)]
    component = []
    while stack:
        cr, cc = stack.pop()
        if cr < 0 or cr >= rows or cc < 0 or cc >= cols:
            continue
        if (cr, cc) in visited:
            continue
        if grid[cr][cc] != 0:
            continue
        visited.add((cr, cc))
        component.append((cr, cc))
        stack.append((cr - 1, cc))
        stack.append((cr + 1, cc))
        stack.append((cr, cc - 1))
        stack.append((cr, cc + 1))
    return component


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def coverage_analysis(
    points,
    radius,
    resolution=50,
    bounds=None,
    suggest=True,
):
    """Analyze service-area coverage for a set of sites.

    Parameters
    ----------
    points : list of (x, y) tuples
        Site locations.
    radius : float
        Service radius — a grid cell is "covered" if it is within this
        distance of at least one site.
    resolution : int
        Number of grid cells along the shorter axis.  Higher = more accurate
        but slower.  Default 50.
    bounds : tuple of (west, south, east, north) or None
        Analysis region.  Defaults to the current vormap bounds.
    suggest : bool
        If True, find the optimal location for one additional site.

    Returns
    -------
    CoverageResult
    """
    if not points:
        raise ValueError("coverage_analysis requires at least one point")
    if radius <= 0:
        raise ValueError("radius must be positive")
    if resolution < 2:
        raise ValueError("resolution must be at least 2")

    # Determine bounds
    if bounds is None:
        bounds = (IND_W, IND_S, IND_E, IND_N)
    west, south, east, north = bounds

    width = east - west
    height = north - south
    if width <= 0 or height <= 0:
        raise ValueError("bounds must have positive width and height")

    # Build grid
    shorter = min(width, height)
    cell_size = shorter / resolution
    cols = max(1, int(math.ceil(width / cell_size)))
    rows = max(1, int(math.ceil(height / cell_size)))
    total_cells = rows * cols

    # depth[r][c] = number of sites covering this cell
    depth = [[0] * cols for _ in range(rows)]
    # per_site_cells[site_idx] = set of (r, c) covered
    per_site_cells = [set() for _ in points]

    # Pre-compute grid cell centers
    # cell (r, c) center = (west + (c + 0.5) * cell_size,
    #                       south + (r + 0.5) * cell_size)

    # For each site, mark cells within radius.
    # Optimization: only check cells within the bounding box of the circle
    radius_sq = radius * radius
    for si, (px, py) in enumerate(points):
        # Grid bounding box of circle
        c_min = max(0, int((px - radius - west) / cell_size))
        c_max = min(cols - 1, int((px + radius - west) / cell_size))
        r_min = max(0, int((py - radius - south) / cell_size))
        r_max = min(rows - 1, int((py + radius - south) / cell_size))

        for r in range(r_min, r_max + 1):
            cy = south + (r + 0.5) * cell_size
            dy_sq = (py - cy) ** 2
            if dy_sq > radius_sq:
                continue
            for c in range(c_min, c_max + 1):
                cx = west + (c + 0.5) * cell_size
                if dy_sq + (px - cx) ** 2 <= radius_sq:
                    depth[r][c] += 1
                    per_site_cells[si].add((r, c))

    # Aggregate stats
    covered_cells = 0
    multi_covered = 0
    total_depth = 0
    max_depth = 0

    for r in range(rows):
        for c in range(cols):
            d = depth[r][c]
            if d > 0:
                covered_cells += 1
                total_depth += d
                if d > max_depth:
                    max_depth = d
                if d >= 2:
                    multi_covered += 1

    uncovered_cells = total_cells - covered_cells
    coverage_ratio = covered_cells / total_cells if total_cells > 0 else 0.0
    redundancy_ratio = (
        multi_covered / covered_cells if covered_cells > 0 else 0.0
    )
    avg_depth = total_depth / covered_cells if covered_cells > 0 else 0.0

    # Per-site stats
    # exclusive = cells where only this site reaches
    all_covered_by = {}  # (r,c) → set of site indices
    for si, cells in enumerate(per_site_cells):
        for rc in cells:
            if rc not in all_covered_by:
                all_covered_by[rc] = set()
            all_covered_by[rc].add(si)

    per_site = []
    for si, (px, py) in enumerate(points):
        exclusive = sum(
            1
            for rc in per_site_cells[si]
            if len(all_covered_by.get(rc, set())) == 1
        )
        per_site.append(
            SiteCoverage(
                x=px,
                y=py,
                cells_covered=len(per_site_cells[si]),
                exclusive_cells=exclusive,
            )
        )

    # Gap detection — flood-fill connected uncovered regions
    gaps = _find_gaps(depth, rows, cols, cell_size, west, south)

    # Optimal site suggestion
    suggestion = None
    if suggest and uncovered_cells > 0:
        suggestion = _find_best_site(
            depth, rows, cols, cell_size, west, south, radius,
            total_cells,
        )

    return CoverageResult(
        total_cells=total_cells,
        covered_cells=covered_cells,
        uncovered_cells=uncovered_cells,
        coverage_ratio=coverage_ratio,
        multi_covered_cells=multi_covered,
        redundancy_ratio=redundancy_ratio,
        avg_depth=avg_depth,
        max_depth=max_depth,
        radius=radius,
        resolution=resolution,
        cell_size=cell_size,
        bounds=bounds,
        gaps=gaps,
        per_site=per_site,
        suggestion=suggestion,
    )


def _find_gaps(depth, rows, cols, cell_size, west, south, min_cells=4):
    """Flood-fill connected uncovered regions into Gap objects.

    Ignores tiny gaps with fewer than *min_cells* cells (likely noise
    at region edges).
    """
    visited = set()
    gaps = []

    for r in range(rows):
        for c in range(cols):
            if depth[r][c] == 0 and (r, c) not in visited:
                component = _flood_fill(depth, rows, cols, r, c, visited)
                if len(component) < min_cells:
                    continue
                # Centroid of gap
                cx = sum(
                    west + (cc + 0.5) * cell_size for _, cc in component
                ) / len(component)
                cy = sum(
                    south + (cr + 0.5) * cell_size for cr, _ in component
                ) / len(component)
                area = len(component) * cell_size * cell_size
                gaps.append(
                    Gap(
                        centroid_x=cx,
                        centroid_y=cy,
                        area=area,
                        cell_count=len(component),
                        cells=component,
                    )
                )

    # Sort by area descending — biggest gap first
    gaps.sort(key=lambda g: g.area, reverse=True)
    return gaps


def _find_best_site(depth, rows, cols, cell_size, west, south, radius,
                    total_cells):
    """Find the grid cell that, if a new site were placed there, would
    cover the most currently-uncovered cells."""
    radius_sq = radius * radius
    best_count = 0
    best_r = 0
    best_c = 0

    # Sample every uncovered cell as a candidate site
    candidates = []
    for r in range(rows):
        for c in range(cols):
            if depth[r][c] == 0:
                candidates.append((r, c))

    # If too many candidates, subsample for speed
    if len(candidates) > 2000:
        import random
        random.shuffle(candidates)
        candidates = candidates[:2000]

    for cr, cc in candidates:
        cx = west + (cc + 0.5) * cell_size
        cy = south + (cr + 0.5) * cell_size

        # Count uncovered cells within radius
        c_min = max(0, int((cx - radius - west) / cell_size))
        c_max = min(cols - 1, int((cx + radius - west) / cell_size))
        r_min = max(0, int((cy - radius - south) / cell_size))
        r_max = min(rows - 1, int((cy + radius - south) / cell_size))

        count = 0
        for rr in range(r_min, r_max + 1):
            ry = south + (rr + 0.5) * cell_size
            dy_sq = (cy - ry) ** 2
            if dy_sq > radius_sq:
                continue
            for rc in range(c_min, c_max + 1):
                if depth[rr][rc] == 0:
                    rx = west + (rc + 0.5) * cell_size
                    if dy_sq + (cx - rx) ** 2 <= radius_sq:
                        count += 1

        if count > best_count:
            best_count = count
            best_r = cr
            best_c = cc

    if best_count == 0:
        return None

    sx = west + (best_c + 0.5) * cell_size
    sy = south + (best_r + 0.5) * cell_size
    covered_before = total_cells - sum(
        1 for r in range(rows) for c in range(cols) if depth[r][c] == 0
    )
    new_ratio = (covered_before + best_count) / total_cells

    return SiteSuggestion(
        x=sx, y=sy, new_cells=best_count, new_coverage_ratio=new_ratio,
    )


# ---------------------------------------------------------------------------
# Suggest API (convenience wrapper)
# ---------------------------------------------------------------------------

def suggest_site(points, radius, resolution=50, bounds=None):
    """Convenience function: just return the optimal next-site suggestion.

    Parameters
    ----------
    points, radius, resolution, bounds
        Same as ``coverage_analysis``.

    Returns
    -------
    SiteSuggestion or None
        None if the area is already fully covered.
    """
    result = coverage_analysis(
        points, radius, resolution=resolution, bounds=bounds, suggest=True,
    )
    return result.suggestion


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_json(result, path):
    """Export CoverageResult to a JSON file."""
    resolved = validate_output_path(path)
    with open(resolved, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)
    return resolved


def export_csv(result, path):
    """Export per-site coverage stats to CSV."""
    resolved = validate_output_path(path)
    with open(resolved, "w", encoding="utf-8") as f:
        f.write("x,y,cells_covered,exclusive_cells\n")
        for s in result.per_site:
            f.write(
                "%s,%s,%d,%d\n" % (round(s.x, 4), round(s.y, 4),
                                   s.cells_covered, s.exclusive_cells)
            )
    return resolved


def export_heatmap_svg(result, path, width=800):
    """Export a coverage depth heatmap as SVG.

    Uncovered cells are dark, single-covered cells are green, and
    multiply-covered cells shade toward blue.  Gap centroids are marked
    with red circles; the suggested site (if any) is marked with a star.
    """
    resolved = validate_output_path(path)

    west, south, east, north = result.bounds
    map_w = east - west
    map_h = north - south
    aspect = map_h / map_w if map_w > 0 else 1.0
    svg_w = width
    svg_h = int(width * aspect)
    cs = result.cell_size

    cols = int(math.ceil(map_w / cs))
    rows = int(math.ceil(map_h / cs))
    cell_w = svg_w / cols
    cell_h = svg_h / rows

    lines = []
    lines.append(
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'width="%d" height="%d" viewBox="0 0 %d %d">'
        % (svg_w, svg_h, svg_w, svg_h)
    )
    lines.append(
        '<rect width="100%%" height="100%%" fill="#1a1a2e"/>'
    )

    # Re-run grid to get depth per cell (result doesn't store the raw grid)
    # We could store it, but for SVG export the overhead is fine.
    # Actually, we reconstruct from per_site data:
    # Instead of re-running, just draw per-site circles and gaps.
    # But that loses per-cell depth.  Let's just re-compute quickly.

    # We already have the result — let's reconstruct depth from per_site
    depth = [[0] * cols for _ in range(rows)]
    radius_sq = result.radius * result.radius
    for s in result.per_site:
        c_min = max(0, int((s.x - result.radius - west) / cs))
        c_max = min(cols - 1, int((s.x + result.radius - west) / cs))
        r_min = max(0, int((s.y - result.radius - south) / cs))
        r_max = min(rows - 1, int((s.y + result.radius - south) / cs))
        for r in range(r_min, r_max + 1):
            cy = south + (r + 0.5) * cs
            dy_sq = (s.y - cy) ** 2
            if dy_sq > radius_sq:
                continue
            for c in range(c_min, c_max + 1):
                cx = west + (c + 0.5) * cs
                if dy_sq + (s.x - cx) ** 2 <= radius_sq:
                    depth[r][c] += 1

    max_d = result.max_depth if result.max_depth > 0 else 1

    for r in range(rows):
        for c in range(cols):
            d = depth[r][c]
            # SVG y is flipped (0 = top = north)
            sx = c * cell_w
            sy = (rows - 1 - r) * cell_h
            if d == 0:
                fill = "#2d2d44"
            elif d == 1:
                fill = "#2ecc71"
            else:
                # Blend green → blue based on depth
                t = min(1.0, (d - 1) / max(1, max_d - 1))
                gr = int(0x2e + (0x34 - 0x2e) * t)
                gg = int(0xcc * (1 - t))
                gb = int(0x71 + (0xff - 0x71) * t)
                fill = "#%02x%02x%02x" % (gr, gg, gb)
            lines.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" '
                'fill="%s" stroke="none"/>'
                % (sx, sy, cell_w + 0.5, cell_h + 0.5, fill)
            )

    # Sites as white dots
    for s in result.per_site:
        sx = (s.x - west) / map_w * svg_w
        sy = (1.0 - (s.y - south) / map_h) * svg_h
        lines.append(
            '<circle cx="%.1f" cy="%.1f" r="3" fill="white" '
            'stroke="#333" stroke-width="0.5"/>' % (sx, sy)
        )

    # Gap centroids as red circles
    for gap in result.gaps:
        gx = (gap.centroid_x - west) / map_w * svg_w
        gy = (1.0 - (gap.centroid_y - south) / map_h) * svg_h
        r = max(4, min(15, math.sqrt(gap.area) / 20))
        lines.append(
            '<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" '
            'stroke="#e74c3c" stroke-width="2" opacity="0.8"/>'
            % (gx, gy, r)
        )

    # Suggested site as gold star
    if result.suggestion:
        sx = (result.suggestion.x - west) / map_w * svg_w
        sy = (1.0 - (result.suggestion.y - south) / map_h) * svg_h
        # Simple 5-point star
        star_r = 8
        star_pts = []
        for i in range(10):
            angle = math.pi / 2 + i * math.pi / 5
            r = star_r if i % 2 == 0 else star_r * 0.4
            star_pts.append(
                "%.1f,%.1f" % (sx + r * math.cos(angle),
                               sy - r * math.sin(angle))
            )
        lines.append(
            '<polygon points="%s" fill="#f1c40f" stroke="#c0950a" '
            'stroke-width="1"/>' % " ".join(star_pts)
        )

    # Legend
    ly = svg_h - 60
    lines.append(
        '<rect x="10" y="%d" width="180" height="55" rx="5" '
        'fill="rgba(0,0,0,0.6)"/>' % ly
    )
    lines.append(
        '<text x="20" y="%d" font-size="11" fill="white">'
        'Coverage: %.1f%%</text>' % (ly + 15, result.coverage_ratio * 100)
    )
    lines.append(
        '<text x="20" y="%d" font-size="11" fill="white">'
        'Redundancy: %.1f%%</text>' % (ly + 30, result.redundancy_ratio * 100)
    )
    lines.append(
        '<text x="20" y="%d" font-size="11" fill="white">'
        'Gaps: %d</text>' % (ly + 45, len(result.gaps))
    )

    lines.append("</svg>")

    with open(resolved, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return resolved


# ---------------------------------------------------------------------------
# Text report
# ---------------------------------------------------------------------------

def render(result):
    """Render a human-readable text report."""
    lines = []
    lines.append("=" * 60)
    lines.append("  COVERAGE ANALYSIS REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append("  Radius:       %.2f" % result.radius)
    lines.append("  Resolution:   %d  (cell size: %.2f)" % (
        result.resolution, result.cell_size))
    lines.append("  Bounds:       W=%.1f S=%.1f E=%.1f N=%.1f" % result.bounds)
    lines.append("")
    lines.append("  ── Coverage ──")
    lines.append("  Total cells:      %d" % result.total_cells)
    lines.append("  Covered:          %d  (%.1f%%)" % (
        result.covered_cells, result.coverage_ratio * 100))
    lines.append("  Uncovered:        %d  (%.1f%%)" % (
        result.uncovered_cells, (1 - result.coverage_ratio) * 100))
    lines.append("  Multi-covered:    %d  (%.1f%% redundancy)" % (
        result.multi_covered_cells, result.redundancy_ratio * 100))
    lines.append("  Avg depth:        %.2f" % result.avg_depth)
    lines.append("  Max depth:        %d" % result.max_depth)
    lines.append("")

    if result.gaps:
        lines.append("  ── Gaps (%d found) ──" % len(result.gaps))
        for i, gap in enumerate(result.gaps[:10]):
            lines.append(
                "    #%d  centroid=(%.1f, %.1f)  area≈%.0f  cells=%d"
                % (i + 1, gap.centroid_x, gap.centroid_y, gap.area,
                   gap.cell_count)
            )
        if len(result.gaps) > 10:
            lines.append("    ... and %d more" % (len(result.gaps) - 10))
        lines.append("")

    if result.suggestion:
        s = result.suggestion
        lines.append("  ── Suggested Next Site ──")
        lines.append("    Location:   (%.2f, %.2f)" % (s.x, s.y))
        lines.append("    New cells:  %d" % s.new_cells)
        lines.append("    Projected:  %.1f%% coverage" % (
            s.new_coverage_ratio * 100))
        lines.append("")

    if result.per_site:
        lines.append("  ── Per-Site Breakdown (%d sites) ──" % len(
            result.per_site))
        # Sort by exclusive cells descending
        ranked = sorted(
            enumerate(result.per_site),
            key=lambda t: t[1].exclusive_cells,
            reverse=True,
        )
        for rank, (idx, s) in enumerate(ranked[:15]):
            lines.append(
                "    Site %2d  (%.1f, %.1f)  covered=%d  exclusive=%d"
                % (idx + 1, s.x, s.y, s.cells_covered, s.exclusive_cells)
            )
        if len(result.per_site) > 15:
            lines.append("    ... and %d more" % (len(result.per_site) - 15))

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Coverage Analyzer — service area coverage analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "datafile", help="Input data file (txt/csv/json/geojson)")
    parser.add_argument(
        "--radius", type=float, required=True,
        help="Service radius for coverage analysis")
    parser.add_argument(
        "--resolution", type=int, default=50,
        help="Grid resolution (cells along shorter axis, default: 50)")
    parser.add_argument(
        "--bounds", type=float, nargs=4,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Analysis bounds (default: auto from data)")
    parser.add_argument(
        "--json", dest="json_out", metavar="FILE",
        help="Export results to JSON")
    parser.add_argument(
        "--csv", dest="csv_out", metavar="FILE",
        help="Export per-site stats to CSV")
    parser.add_argument(
        "--heatmap", dest="heatmap_out", metavar="FILE",
        help="Export coverage heatmap as SVG")
    parser.add_argument(
        "--no-suggest", action="store_true",
        help="Skip optimal site suggestion (faster)")

    args = parser.parse_args()

    # Load data
    import vormap
    data = vormap.load_data(args.datafile)
    points = [(d[0], d[1]) for d in data]

    bounds = tuple(args.bounds) if args.bounds else None

    result = coverage_analysis(
        points,
        radius=args.radius,
        resolution=args.resolution,
        bounds=bounds,
        suggest=not args.no_suggest,
    )

    # Print text report
    print(render(result))

    # Exports
    if args.json_out:
        p = export_json(result, args.json_out)
        print("  → JSON exported to %s" % p)
    if args.csv_out:
        p = export_csv(result, args.csv_out)
        print("  → CSV exported to %s" % p)
    if args.heatmap_out:
        p = export_heatmap_svg(result, args.heatmap_out)
        print("  → SVG heatmap exported to %s" % p)


if __name__ == "__main__":
    main()
