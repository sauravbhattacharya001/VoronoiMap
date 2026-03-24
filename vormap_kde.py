"""Kernel Density Estimation (KDE) for spatial point data.

Computes a smooth, continuous probability density surface from discrete
point locations using Gaussian kernels.  Unlike the cell-based heatmap
module (``vormap_heatmap``), KDE produces a statistically principled
density estimate independent of the Voronoi tessellation.

Analyses provided
-----------------
- **Gaussian KDE** at arbitrary query points or over a regular grid.
- **Bandwidth selection**: Silverman's rule-of-thumb, Scott's rule, or
  user-specified.
- **Density surface** export as SVG heatmap or CSV grid.
- **Hotspot detection**: find local maxima in the density surface.
- **Density contours**: extract iso-density levels.

Example::

    from vormap_kde import kde_grid, export_kde_svg, find_hotspots

    points = [(100, 200), (110, 210), (300, 400), (500, 100)]

    grid = kde_grid(points, nx=80, ny=80)
    export_kde_svg(grid, "density.svg")

    hotspots = find_hotspots(grid, threshold_pct=90)

CLI::

    voronoimap datauni5.txt 5 --kde-svg density.svg
    voronoimap datauni5.txt 5 --kde-csv density.csv
    voronoimap datauni5.txt 5 --kde-svg density.svg --kde-bandwidth 25
    voronoimap datauni5.txt 5 --kde-svg density.svg --kde-hotspots hotspots.json
"""

from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import vormap


# -- Result types -----------------------------------------------------

@dataclass
class KDEGrid:
    """Regular grid of density estimates."""
    values: list[list[float]]       # values[row][col], row 0 = south
    nx: int
    ny: int
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    bandwidth: float
    density_min: float = 0.0
    density_max: float = 0.0
    points_used: int = 0

    @property
    def cell_width(self) -> float:
        return (self.x_max - self.x_min) / max(1, self.nx - 1)

    @property
    def cell_height(self) -> float:
        return (self.y_max - self.y_min) / max(1, self.ny - 1)

    def density_at(self, row: int, col: int) -> float:
        """Get density at a grid cell (bounds-checked)."""
        if 0 <= row < self.ny and 0 <= col < self.nx:
            return self.values[row][col]
        return 0.0

    def grid_to_coords(self, row: int, col: int) -> tuple[float, float]:
        """Convert grid indices to spatial coordinates."""
        x = self.x_min + col * self.cell_width
        y = self.y_min + row * self.cell_height
        return x, y


@dataclass
class Hotspot:
    """A local density maximum."""
    x: float
    y: float
    density: float
    grid_row: int
    grid_col: int
    rank: int = 0


@dataclass
class DensityContour:
    """An iso-density level with associated cells."""
    level: float
    cells: list[tuple[int, int]]    # (row, col) pairs at or above this level
    area_fraction: float            # fraction of grid area at or above this level


# -- Bandwidth selection -----------------------------------------------

def _sample_std(vals: list[float]) -> float:
    """Compute sample standard deviation (Bessel-corrected)."""
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(max(var, 1e-12))


def _iqr(vals: list[float]) -> float:
    """Compute interquartile range (Q3 - Q1) of a list of values."""
    s = sorted(vals)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    return q3 - q1


def _extract_spread(points: list[tuple[float, float]]) -> tuple[float, float]:
    """Return (mean_std, mean_robust_spread) from 2D point coordinates.

    mean_std is the average of per-axis sample standard deviations.
    mean_robust_spread uses min(std, IQR/1.34) per axis (Silverman's
    robust scale estimate to handle heavy-tailed or skewed data).
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    sx, sy = _sample_std(xs), _sample_std(ys)
    mean_std = (sx + sy) / 2.0

    iqr_x, iqr_y = _iqr(xs), _iqr(ys)
    robust_x = min(sx, iqr_x / 1.34) if iqr_x > 0 else sx
    robust_y = min(sy, iqr_y / 1.34) if iqr_y > 0 else sy
    mean_robust = (robust_x + robust_y) / 2.0

    return mean_std, mean_robust


def silverman_bandwidth(points: list[tuple[float, float]]) -> float:
    """Silverman's rule-of-thumb bandwidth for 2D Gaussian KDE.

    Uses the robust scale estimate min(std, IQR/1.34) per axis to
    avoid over-smoothing multimodal or heavy-tailed distributions.

    h = n^(-1/6) * mean(min(std_i, IQR_i / 1.34))
    """
    n = len(points)
    if n < 2:
        return 1.0

    _, mean_robust = _extract_spread(points)
    h = (n ** (-1.0 / 6.0)) * mean_robust
    return max(h, 1e-6)


def scott_bandwidth(points: list[tuple[float, float]]) -> float:
    """Scott's rule bandwidth for 2D Gaussian KDE.

    Uses sample standard deviation directly (no IQR robustification).

    h = n^(-1/6) * mean_std
    """
    n = len(points)
    if n < 2:
        return 1.0

    mean_std, _ = _extract_spread(points)
    h = (n ** (-1.0 / 6.0)) * mean_std
    return max(h, 1e-6)


# -- Spatial binning for grid KDE acceleration --------------------------

class _SpatialBins:
    """Partition points into a regular grid of bins for fast neighbour lookup.

    When computing KDE over a grid, each query point only needs points
    within ``cutoff`` distance.  Instead of iterating all *n* source
    points per query (O(nx * ny * n)), we iterate only the bins that
    overlap the cutoff circle – giving O(nx * ny * k) where k << n
    for datasets much larger than the bandwidth.

    The bin cell size equals the cutoff radius so each query needs at
    most a 3×3 neighbourhood of bins.
    """

    __slots__ = ("_bins", "_bx", "_by", "_ox", "_oy", "_cell")

    def __init__(
        self,
        points: list[tuple[float, float]],
        cutoff: float,
        x_min: float, y_min: float,
        x_max: float, y_max: float,
    ) -> None:
        cell = max(cutoff, 1e-9)
        self._cell = cell
        self._ox = x_min
        self._oy = y_min
        self._bx = max(1, int(math.ceil((x_max - x_min) / cell)))
        self._by = max(1, int(math.ceil((y_max - y_min) / cell)))

        bins: dict[int, list[tuple[float, float]]] = {}
        for px, py in points:
            bx = min(int((px - x_min) / cell), self._bx - 1)
            by = min(int((py - y_min) / cell), self._by - 1)
            key = by * self._bx + bx
            if key in bins:
                bins[key].append((px, py))
            else:
                bins[key] = [(px, py)]
        self._bins = bins

    def neighbours(self, qx: float, qy: float) -> list[tuple[float, float]]:
        """Return all points in the 3×3 bin neighbourhood of (qx, qy)."""
        bx = min(int((qx - self._ox) / self._cell), self._bx - 1)
        by = min(int((qy - self._oy) / self._cell), self._by - 1)
        out: list[tuple[float, float]] = []
        for dy in (-1, 0, 1):
            ry = by + dy
            if ry < 0 or ry >= self._by:
                continue
            for dx in (-1, 0, 1):
                rx = bx + dx
                if rx < 0 or rx >= self._bx:
                    continue
                key = ry * self._bx + rx
                if key in self._bins:
                    out.extend(self._bins[key])
        return out


# -- Core KDE computation ----------------------------------------------

def gaussian_kernel(dist_sq: float, h_sq: float) -> float:
    """2D Gaussian kernel: K(d) = (1/(2*pi*h^2)) * exp(-d^2/(2*h^2))."""
    return math.exp(-0.5 * dist_sq / h_sq) / (2.0 * math.pi * h_sq)


def kde_at_point(
    qx: float, qy: float,
    points: list[tuple[float, float]],
    bandwidth: float,
) -> float:
    """Compute KDE density estimate at a single query point."""
    n = len(points)
    if n == 0:
        return 0.0

    h_sq = bandwidth * bandwidth
    cutoff_sq = (4.0 * bandwidth) ** 2
    _exp = math.exp
    neg_half_inv_h_sq = -0.5 / h_sq
    coeff = 1.0 / (2.0 * math.pi * h_sq)

    total = 0.0
    for px, py in points:
        dx = qx - px
        dy = qy - py
        d_sq = dx * dx + dy * dy
        if d_sq <= cutoff_sq:
            total += _exp(d_sq * neg_half_inv_h_sq)

    return total * coeff / n


def kde_grid(
    points: list[tuple[float, float]],
    nx: int = 50,
    ny: int = 50,
    bandwidth: float | None = None,
    bandwidth_method: str = "silverman",
    bounds: tuple[float, float, float, float] | None = None,
    padding: float = 0.1,
) -> KDEGrid:
    """Compute KDE over a regular grid.

    Args:
        points: Input point locations.
        nx, ny: Grid resolution.
        bandwidth: Explicit bandwidth (overrides method).
        bandwidth_method: "silverman" or "scott".
        bounds: (x_min, y_min, x_max, y_max). Auto-computed if None.
        padding: Fraction of range to add as padding (default 10%).

    Returns:
        KDEGrid with density estimates.
    """
    if not points:
        raise ValueError("No points provided for KDE")
    if nx < 2 or ny < 2:
        raise ValueError("Grid resolution must be at least 2x2")
    vormap.validate_grid_size(nx, ny, caller="kde_grid")

    if bandwidth is not None:
        if bandwidth <= 0:
            raise ValueError("Bandwidth must be positive")
        h = bandwidth
    elif bandwidth_method == "scott":
        h = scott_bandwidth(points)
    else:
        h = silverman_bandwidth(points)

    if bounds is not None:
        x_min, y_min, x_max, y_max = bounds
    else:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        dx = max((x_max - x_min) * padding, h)
        dy = max((y_max - y_min) * padding, h)
        x_min -= dx
        x_max += dx
        y_min -= dy
        y_max += dy

    values = []
    d_min = float("inf")
    d_max = 0.0

    # Build spatial index for O(1)-neighbourhood point lookup.
    # The cutoff radius is 4*bandwidth (matching kde_at_point).
    cutoff = 4.0 * h
    n = len(points)
    h_sq = h * h
    cutoff_sq = cutoff * cutoff

    # For small point sets (< 64), spatial binning overhead isn't worth it.
    use_bins = n >= 64
    if use_bins:
        bins = _SpatialBins(points, cutoff, x_min, y_min, x_max, y_max)

    inv_n = 1.0 / n
    coeff = 1.0 / (2.0 * math.pi * h_sq)

    # Pre-compute x-coordinates to avoid repeated division in the inner loop.
    x_coords = [x_min + col * (x_max - x_min) / (nx - 1) for col in range(nx)]

    # Hoist frequently-called functions to local variables — in CPython,
    # local name lookups (LOAD_FAST) are ~40% faster than attribute
    # lookups (LOAD_ATTR) and global lookups (LOAD_GLOBAL).  In the
    # tight nx*ny*k inner loop this is a measurable win.
    _exp = math.exp
    _neg_half_inv_h_sq = -0.5 / h_sq
    _get_neighbours = bins.neighbours if use_bins else None
    _y_step = (y_max - y_min) / (ny - 1)

    for row in range(ny):
        y = y_min + row * _y_step
        row_vals = []
        if _get_neighbours is not None:
            for col in range(nx):
                x = x_coords[col]
                total = 0.0
                for px, py in _get_neighbours(x, y):
                    dx = x - px
                    dy = y - py
                    d_sq = dx * dx + dy * dy
                    if d_sq <= cutoff_sq:
                        total += _exp(d_sq * _neg_half_inv_h_sq)
                d = total * coeff * inv_n
                row_vals.append(d)
                if d < d_min:
                    d_min = d
                if d > d_max:
                    d_max = d
        else:
            for col in range(nx):
                x = x_coords[col]
                total = 0.0
                for px, py in points:
                    dx = x - px
                    dy = y - py
                    d_sq = dx * dx + dy * dy
                    if d_sq <= cutoff_sq:
                        total += _exp(d_sq * _neg_half_inv_h_sq)
                d = total * coeff * inv_n
                row_vals.append(d)
                if d < d_min:
                    d_min = d
                if d > d_max:
                    d_max = d
        values.append(row_vals)

    return KDEGrid(
        values=values, nx=nx, ny=ny,
        x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max,
        bandwidth=h, density_min=d_min, density_max=d_max,
        points_used=len(points),
    )


# -- Hotspot detection --------------------------------------------------

def find_hotspots(
    grid: KDEGrid,
    threshold_pct: float = 90.0,
    min_separation: int = 2,
) -> list[Hotspot]:
    """Find local density maxima (hotspots) in the KDE grid.

    A cell is a local maximum if its density is greater than or equal
    to all 8 neighbors (with at least one strict inequality) and is
    above the percentile threshold.
    """
    if threshold_pct < 0 or threshold_pct > 100:
        raise ValueError("threshold_pct must be 0-100")

    all_vals = sorted(v for row in grid.values for v in row)
    if not all_vals:
        return []

    # Use (n-1) * pct/100 for standard percentile indexing, matching
    # the convention in vormap_geometry.percentile().  Fixes #47.
    idx = int((len(all_vals) - 1) * threshold_pct / 100.0)
    idx = max(0, min(idx, len(all_vals) - 1))
    threshold = all_vals[idx]

    candidates = []
    for r in range(1, grid.ny - 1):
        for c in range(1, grid.nx - 1):
            val = grid.values[r][c]
            if val < threshold:
                continue

            is_max = True
            has_strict = False
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    if dr == 0 and dc == 0:
                        continue
                    neighbor = grid.values[r + dr][c + dc]
                    if neighbor > val:
                        is_max = False
                        break
                    if neighbor < val:
                        has_strict = True
                if not is_max:
                    break
            # Require at least one neighbor strictly less to avoid
            # marking every cell in a uniform-density region
            is_max = is_max and has_strict

            if is_max:
                x, y = grid.grid_to_coords(r, c)
                candidates.append(Hotspot(
                    x=x, y=y, density=val,
                    grid_row=r, grid_col=c,
                ))

    candidates.sort(key=lambda h: h.density, reverse=True)

    selected = []
    for cand in candidates:
        too_close = False
        for sel in selected:
            if (abs(cand.grid_row - sel.grid_row) < min_separation and
                    abs(cand.grid_col - sel.grid_col) < min_separation):
                too_close = True
                break
        if not too_close:
            cand.rank = len(selected) + 1
            selected.append(cand)

    return selected


# -- Density contours ---------------------------------------------------

def density_contours(grid: KDEGrid, levels: int = 5) -> list[DensityContour]:
    """Extract iso-density contour levels from the KDE grid.

    Uses a single pass over the grid to classify each cell into all
    applicable contour levels, replacing the previous O(levels × nx × ny)
    approach with O(nx × ny + levels).
    """
    if levels < 1:
        raise ValueError("levels must be at least 1")

    d_range = grid.density_max - grid.density_min or 1.0
    if d_range <= 0:
        return []

    total_cells = grid.nx * grid.ny

    # Pre-compute level thresholds.
    level_vals = [
        grid.density_min + d_range * i / levels
        for i in range(1, levels + 1)
    ]

    # Single pass: for each cell, determine the highest level it meets
    # and add it to that level and all lower levels.  We accumulate cell
    # lists per level, then build contours.
    level_cells: list[list[tuple[int, int]]] = [[] for _ in range(levels)]

    for r in range(grid.ny):
        row = grid.values[r]
        for c in range(grid.nx):
            val = row[c]
            # level_vals is ascending; the cell meets level li if
            # val >= level_vals[li].  Once val < a threshold, it
            # won't meet any higher threshold either.
            for li in range(levels - 1, -1, -1):
                if val >= level_vals[li]:
                    # Cell meets this level and all levels below it.
                    for lj in range(li + 1):
                        level_cells[lj].append((r, c))
                    break

    contours = []
    for li in range(levels):
        cells = level_cells[li]
        contours.append(DensityContour(
            level=level_vals[li], cells=cells,
            area_fraction=len(cells) / total_cells if total_cells > 0 else 0.0,
        ))

    return contours


# -- SVG export ---------------------------------------------------------

_VIRIDIS = [
    (68, 1, 84), (72, 35, 116), (64, 67, 135), (52, 94, 141),
    (33, 145, 140), (53, 183, 121), (109, 205, 89), (180, 222, 44),
    (253, 231, 37),
]

_PLASMA = [
    (13, 8, 135), (84, 2, 163), (139, 10, 165), (185, 50, 137),
    (219, 92, 104), (244, 136, 73), (254, 188, 43), (240, 249, 33),
]

_HOT_COLD = [
    (49, 54, 149), (69, 117, 180), (116, 173, 209), (171, 217, 233),
    (255, 255, 191), (253, 174, 97), (244, 109, 67), (215, 48, 39),
    (165, 0, 38),
]


def _color_ramp(t: float, ramp: str = "viridis") -> str:
    """Map [0, 1] to an RGB hex color."""
    if ramp == "plasma":
        stops = _PLASMA
    elif ramp == "hot_cold":
        stops = _HOT_COLD
    else:
        stops = _VIRIDIS

    t = max(0.0, min(1.0, t))
    n = len(stops) - 1
    idx = t * n
    lo = int(idx)
    hi = min(lo + 1, n)
    frac = idx - lo

    r = int(stops[lo][0] + (stops[hi][0] - stops[lo][0]) * frac)
    g = int(stops[lo][1] + (stops[hi][1] - stops[lo][1]) * frac)
    b = int(stops[lo][2] + (stops[hi][2] - stops[lo][2]) * frac)
    return f"#{r:02x}{g:02x}{b:02x}"


def export_kde_svg(
    grid: KDEGrid,
    output_path: str,
    width: int = 800,
    height: int = 600,
    ramp: str = "viridis",
    show_hotspots: bool = False,
    hotspot_threshold_pct: float = 90.0,
    title: str | None = None,
) -> str:
    """Export KDE density surface as an SVG heatmap."""
    output_path = vormap.validate_output_path(output_path)

    margin = 60
    plot_w = width - 2 * margin
    plot_h = height - 2 * margin

    d_range = grid.density_max - grid.density_min
    if d_range == 0:
        d_range = 1.0

    cell_w = plot_w / grid.nx
    cell_h = plot_h / grid.ny

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                      width=str(width), height=str(height))

    ET.SubElement(svg, "rect", width=str(width), height=str(height),
                  fill="white")

    if title:
        t = ET.SubElement(svg, "text", x=str(width // 2), y="25",
                          fill="black")
        t.set("text-anchor", "middle")
        t.set("font-size", "16")
        t.set("font-family", "sans-serif")
        t.text = vormap.sanitize_css_value(title)

    for row in range(grid.ny):
        for col in range(grid.nx):
            d = grid.values[row][col]
            t = (d - grid.density_min) / d_range
            svg_x = margin + col * cell_w
            svg_y = margin + (grid.ny - 1 - row) * cell_h
            color = _color_ramp(t, ramp)
            ET.SubElement(svg, "rect",
                          x=f"{svg_x:.1f}", y=f"{svg_y:.1f}",
                          width=f"{cell_w + 0.5:.1f}",
                          height=f"{cell_h + 0.5:.1f}",
                          fill=color, stroke="none")

    if show_hotspots:
        hotspots = find_hotspots(grid, threshold_pct=hotspot_threshold_pct)
        for hs in hotspots[:10]:
            svg_x = margin + hs.grid_col * cell_w + cell_w / 2
            svg_y = margin + (grid.ny - 1 - hs.grid_row) * cell_h + cell_h / 2
            ET.SubElement(svg, "circle",
                          cx=f"{svg_x:.1f}", cy=f"{svg_y:.1f}",
                          r="6", fill="none", stroke="white",
                          **{"stroke-width": "2"})
            label = ET.SubElement(svg, "text",
                                  x=f"{svg_x:.1f}",
                                  y=f"{svg_y - 10:.1f}",
                                  fill="white")
            label.set("text-anchor", "middle")
            label.set("font-size", "10")
            label.set("font-family", "sans-serif")
            label.text = f"#{hs.rank}"

    legend_x = width - margin + 10
    legend_h = plot_h
    legend_w = 15
    legend_steps = 50
    step_h = legend_h / legend_steps

    for i in range(legend_steps):
        t = i / (legend_steps - 1)
        color = _color_ramp(t, ramp)
        ly = margin + (legend_steps - 1 - i) * step_h
        ET.SubElement(svg, "rect",
                      x=str(legend_x), y=f"{ly:.1f}",
                      width=str(legend_w), height=f"{step_h + 1:.1f}",
                      fill=color, stroke="none")

    for val, label_text in [
        (grid.density_max, f"{grid.density_max:.2e}"),
        (grid.density_min, f"{grid.density_min:.2e}"),
    ]:
        t = (val - grid.density_min) / d_range
        ly = margin + (1 - t) * legend_h
        lt = ET.SubElement(svg, "text",
                           x=str(legend_x + legend_w + 5),
                           y=f"{ly + 4:.1f}",
                           fill="black")
        lt.set("font-size", "9")
        lt.set("font-family", "sans-serif")
        lt.text = label_text

    info = ET.SubElement(svg, "text",
                         x=str(margin), y=str(height - 10),
                         fill="#666")
    info.set("font-size", "10")
    info.set("font-family", "sans-serif")
    info.text = (f"KDE: {grid.points_used} points, "
                 f"bandwidth={grid.bandwidth:.2f}, "
                 f"grid={grid.nx}\u00d7{grid.ny}")

    tree = ET.ElementTree(svg)
    ET.indent(tree)
    tree.write(output_path, xml_declaration=True, encoding="utf-8")
    return output_path


# -- CSV export ---------------------------------------------------------

def export_kde_csv(grid: KDEGrid, output_path: str) -> str:
    """Export KDE grid as CSV with columns: x, y, density."""
    output_path = vormap.validate_output_path(output_path)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("x,y,density\n")
        for row in range(grid.ny):
            for col in range(grid.nx):
                x, y = grid.grid_to_coords(row, col)
                d = grid.values[row][col]
                f.write(f"{x:.4f},{y:.4f},{d:.8e}\n")

    return output_path


# -- Hotspot JSON export ------------------------------------------------

def export_hotspots_json(
    hotspots: list[Hotspot],
    output_path: str,
    grid: KDEGrid | None = None,
) -> str:
    """Export hotspots as JSON."""
    output_path = vormap.validate_output_path(output_path)

    data = {
        "hotspots": [
            {
                "rank": h.rank,
                "x": round(h.x, 4),
                "y": round(h.y, 4),
                "density": h.density,
                "grid_row": h.grid_row,
                "grid_col": h.grid_col,
            }
            for h in hotspots
        ],
        "count": len(hotspots),
    }

    if grid:
        data["grid_info"] = {
            "bandwidth": round(grid.bandwidth, 4),
            "nx": grid.nx, "ny": grid.ny,
            "density_min": grid.density_min,
            "density_max": grid.density_max,
            "points_used": grid.points_used,
        }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return output_path


# -- Summary ------------------------------------------------------------

def kde_summary(grid: KDEGrid) -> dict:
    """Generate a statistical summary of the KDE grid."""
    all_vals = [v for row in grid.values for v in row]
    total_mass = sum(all_vals) * grid.cell_width * grid.cell_height

    sorted_vals = sorted(all_vals)
    n = len(sorted_vals)

    return {
        "bandwidth": round(grid.bandwidth, 4),
        "grid_resolution": f"{grid.nx}x{grid.ny}",
        "points_used": grid.points_used,
        "density_min": grid.density_min,
        "density_max": grid.density_max,
        "density_mean": sum(all_vals) / n if n else 0,
        "density_median": sorted_vals[n // 2] if n else 0,
        "total_mass": round(total_mass, 6),
        "bounds": {
            "x_min": round(grid.x_min, 4),
            "x_max": round(grid.x_max, 4),
            "y_min": round(grid.y_min, 4),
            "y_max": round(grid.y_max, 4),
        },
    }
