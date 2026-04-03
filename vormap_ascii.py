"""Terminal (ASCII/Unicode) rendering of Voronoi diagrams.

Renders Voronoi diagrams directly in the terminal using Unicode block
characters and ANSI colors — no file export or external viewer needed.
Supports both colored and monochrome output, configurable canvas size,
and optional seed-point markers.

Usage (as module):
    import vormap
    import vormap_viz
    import vormap_ascii

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    vormap_ascii.render(regions, data)

Usage (CLI):
    voronoimap datauni5.txt 5 --ascii
    voronoimap datauni5.txt 5 --ascii --ascii-width 120 --ascii-height 40
    voronoimap datauni5.txt 5 --ascii --ascii-mono
"""

import sys
from itertools import chain

from vormap_utils import bounding_box


# ── ANSI color palettes ──────────────────────────────────────────────

# 16 distinct ANSI 256-color background codes for region coloring
_REGION_COLORS = [
    '\033[48;5;{}m'.format(c)
    for c in [
        24, 130, 34, 132, 30, 166, 36, 91,
        64, 95, 25, 172, 29, 97, 66, 136,
    ]
]

_SEED_MARKER = '\033[97;1m\u25CF\033[0m'  # bold white circle
_RESET = '\033[0m'


def _point_in_polygon(px, py, polygon):
    """Ray-casting point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _build_owner_grid(height, width, min_x, min_y, range_x, range_y, poly_list):
    """Build a 2D grid mapping each cell to its owning region index (-1 = none)."""
    grid = []
    max_h = max(height - 1, 1)
    max_w = max(width - 1, 1)
    for row in range(height):
        py = min_y + (row / max_h) * range_y
        grid_row = []
        for col in range(width):
            px = min_x + (col / max_w) * range_x
            owner = -1
            for i, poly in enumerate(poly_list):
                if _point_in_polygon(px, py, poly):
                    owner = i
                    break
            grid_row.append(owner)
        grid.append(grid_row)
    return grid


def _grid_coord(val, min_val, range_val, max_idx):
    """Map a world coordinate to an integer grid coordinate."""
    return int((val - min_val) / range_val * max_idx)


def render(regions, data, *, width=80, height=24, mono=False,
           show_seeds=True, file=None):
    """Render a Voronoi diagram to the terminal.

    Parameters
    ----------
    regions : dict
        Maps (x, y) seed → list of (x, y) polygon vertices.
    data : list of (float, float)
        All seed points.
    width : int
        Terminal columns to use (default: 80).
    height : int
        Terminal rows to use (default: 24).
    mono : bool
        If True, use monochrome box-drawing instead of colors.
    show_seeds : bool
        If True, mark seed point locations with a dot.
    file : file-like or None
        Output stream (default: sys.stdout).
    """
    if file is None:
        file = sys.stdout

    if not regions:
        print("No regions to render.", file=file)
        return

    # Compute bounding box from all polygon vertices
    min_x, min_y, max_x, max_y = bounding_box(
        chain.from_iterable(regions.values())
    )

    # Add small padding
    pad_x = (max_x - min_x) * 0.02 or 1.0
    pad_y = (max_y - min_y) * 0.02 or 1.0
    min_x -= pad_x
    max_x += pad_x
    min_y -= pad_y
    max_y += pad_y

    range_x = max_x - min_x
    range_y = max_y - min_y

    # Build ordered seed→polygon list for fast lookup
    seed_list = list(regions.keys())
    poly_list = [regions[s] for s in seed_list]
    n_regions = len(seed_list)

    # Map seed positions to grid coords for marker placement
    seed_grid = {}
    if show_seeds:
        for sx, sy in seed_list:
            gx = _grid_coord(sx, min_x, range_x, width - 1)
            gy = _grid_coord(sy, min_y, range_y, height - 1)
            seed_grid[(gx, gy)] = True

    # Monochrome boundary chars
    _BORDER = '·'
    _FILL_MONO = '#'
    _EMPTY = ' '

    # ── Build owner grid ───────────────────────────────────────────
    grid = _build_owner_grid(height, width, min_x, min_y, range_x, range_y, poly_list)

    # ── Compute region center positions for labels (mono mode) ─────
    label_grid = {}
    if mono:
        for i, seed in enumerate(seed_list):
            sx, sy = seed
            gx = _grid_coord(sx, min_x, range_x, width - 1)
            gy = _grid_coord(sy, min_y, range_y, height - 1)
            label_grid[(gx, gy)] = str(i % 10)  # single digit label

    # Per-region fill characters for mono mode
    _MONO_FILLS = '#@%&+=~:;'

    # ── Render ───────────────────────────────────────────────────────
    lines = []
    for row in range(height):
        line_parts = []
        for col in range(width):
            # Seed marker / region label
            if show_seeds and (col, row) in seed_grid:
                if mono and (col, row) in label_grid:
                    line_parts.append(label_grid[(col, row)])
                elif mono:
                    line_parts.append('*')
                else:
                    line_parts.append(_SEED_MARKER)
                continue

            owner = grid[row][col]

            if owner < 0:
                line_parts.append(_EMPTY)
                continue

            # Border detection from grid neighbors
            is_border = False
            if mono:
                for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                    nr, nc = row + dr, col + dc
                    if 0 <= nr < height and 0 <= nc < width:
                        if grid[nr][nc] != owner and grid[nr][nc] >= 0:
                            is_border = True
                            break

            if mono:
                fill = _MONO_FILLS[owner % len(_MONO_FILLS)]
                line_parts.append('.' if is_border else fill)
            else:
                color = _REGION_COLORS[owner % len(_REGION_COLORS)]
                line_parts.append(color + ' ' + _RESET)

        lines.append(''.join(line_parts))

    output = '\n'.join(lines)
    try:
        print(output, file=file)
    except UnicodeEncodeError:
        # Fall back to ASCII-safe output on terminals that can't handle Unicode
        import re
        safe = re.sub(r'[^\x00-\x7f]', '#', output)
        # Also strip ANSI escapes if encoding failed
        safe = re.sub(r'\033\[[0-9;]*m', '', safe)
        print(safe, file=file)

    # Legend
    print(file=file)
    print("Voronoi diagram: %d regions, %dx%d canvas" % (n_regions, width, height),
          file=file)
    if show_seeds:
        marker = '*' if mono else '●'
        print(f"  {marker} = seed point", file=file)


def render_to_string(regions, data, **kwargs):
    """Render to a string instead of stdout."""
    import io
    buf = io.StringIO()
    render(regions, data, file=buf, **kwargs)
    return buf.getvalue()
