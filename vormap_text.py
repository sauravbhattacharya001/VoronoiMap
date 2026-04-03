"""Voronoi Typography — render text as Voronoi cell mosaics.

Scatter seed points inside letter shapes derived from built-in bitmap
font glyphs, compute a Voronoi diagram, and clip cells to the letter
outlines.  The result is a decorative text rendering where each letter
is filled with a colorful Voronoi mosaic pattern.

Features:

- Built-in 5×7 bitmap font covering A–Z, 0–9, and common punctuation
- Configurable seed density, cell stroke, fill colormap
- Jitter control for organic vs. geometric aesthetics
- SVG export with per-letter grouping
- Interactive HTML export with hover highlighting
- Background cell rendering (negative-space mode)
- Multiple colormaps: viridis, plasma, warm, cool, pastel, mono, rainbow

Usage (Python API)::

    from vormap_text import render_text_voronoi, export_text_svg

    cells = render_text_voronoi("HELLO", seed_density=30)
    export_text_svg(cells, "hello_voronoi.svg")

    # With options
    cells = render_text_voronoi("VORONOI", seed_density=50,
                                 colormap="plasma", jitter=0.3)
    export_text_svg(cells, "voronoi_art.svg", background="#1a1a2e")

    # HTML with hover
    export_text_html(cells, "voronoi_text.html")

CLI::

    python vormap_text.py "HELLO" hello.svg
    python vormap_text.py "VORONOI" art.svg --density 50 --colormap plasma
    python vormap_text.py "HELLO" hello.html --format html --background "#1a1a2e"
    python vormap_text.py "ABC 123" out.svg --jitter 0.5 --stroke-width 0.5
    python vormap_text.py "MOSAIC" out.svg --negative --colormap warm

"""

import argparse
import hashlib
import math
import random
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Built-in 5x7 bitmap font (1 = filled pixel)
# ---------------------------------------------------------------------------

_FONT: Dict[str, List[List[int]]] = {}


def _f(ch: str, rows: List[str]) -> None:
    """Register a character glyph from compact strings ('X' = filled)."""
    _FONT[ch] = [[1 if c == "X" else 0 for c in row] for row in rows]


_f("A", ["_XXX_", "X___X", "X___X", "XXXXX", "X___X", "X___X", "X___X"])
_f("B", ["XXXX_", "X___X", "X___X", "XXXX_", "X___X", "X___X", "XXXX_"])
_f("C", ["_XXX_", "X___X", "X____", "X____", "X____", "X___X", "_XXX_"])
_f("D", ["XXXX_", "X___X", "X___X", "X___X", "X___X", "X___X", "XXXX_"])
_f("E", ["XXXXX", "X____", "X____", "XXXX_", "X____", "X____", "XXXXX"])
_f("F", ["XXXXX", "X____", "X____", "XXXX_", "X____", "X____", "X____"])
_f("G", ["_XXX_", "X___X", "X____", "X_XXX", "X___X", "X___X", "_XXX_"])
_f("H", ["X___X", "X___X", "X___X", "XXXXX", "X___X", "X___X", "X___X"])
_f("I", ["XXXXX", "__X__", "__X__", "__X__", "__X__", "__X__", "XXXXX"])
_f("J", ["__XXX", "___X_", "___X_", "___X_", "X__X_", "X__X_", "_XX__"])
_f("K", ["X___X", "X__X_", "X_X__", "XX___", "X_X__", "X__X_", "X___X"])
_f("L", ["X____", "X____", "X____", "X____", "X____", "X____", "XXXXX"])
_f("M", ["X___X", "XX_XX", "X_X_X", "X___X", "X___X", "X___X", "X___X"])
_f("N", ["X___X", "XX__X", "X_X_X", "X__XX", "X___X", "X___X", "X___X"])
_f("O", ["_XXX_", "X___X", "X___X", "X___X", "X___X", "X___X", "_XXX_"])
_f("P", ["XXXX_", "X___X", "X___X", "XXXX_", "X____", "X____", "X____"])
_f("Q", ["_XXX_", "X___X", "X___X", "X___X", "X_X_X", "X__X_", "_XX_X"])
_f("R", ["XXXX_", "X___X", "X___X", "XXXX_", "X_X__", "X__X_", "X___X"])
_f("S", ["_XXX_", "X___X", "X____", "_XXX_", "____X", "X___X", "_XXX_"])
_f("T", ["XXXXX", "__X__", "__X__", "__X__", "__X__", "__X__", "__X__"])
_f("U", ["X___X", "X___X", "X___X", "X___X", "X___X", "X___X", "_XXX_"])
_f("V", ["X___X", "X___X", "X___X", "X___X", "_X_X_", "_X_X_", "__X__"])
_f("W", ["X___X", "X___X", "X___X", "X_X_X", "X_X_X", "XX_XX", "X___X"])
_f("X", ["X___X", "X___X", "_X_X_", "__X__", "_X_X_", "X___X", "X___X"])
_f("Y", ["X___X", "X___X", "_X_X_", "__X__", "__X__", "__X__", "__X__"])
_f("Z", ["XXXXX", "____X", "___X_", "__X__", "_X___", "X____", "XXXXX"])
_f("0", ["_XXX_", "X__XX", "X_X_X", "X_X_X", "X_X_X", "XX__X", "_XXX_"])
_f("1", ["__X__", "_XX__", "__X__", "__X__", "__X__", "__X__", "XXXXX"])
_f("2", ["_XXX_", "X___X", "____X", "__XX_", "_X___", "X____", "XXXXX"])
_f("3", ["_XXX_", "X___X", "____X", "__XX_", "____X", "X___X", "_XXX_"])
_f("4", ["___X_", "__XX_", "_X_X_", "X__X_", "XXXXX", "___X_", "___X_"])
_f("5", ["XXXXX", "X____", "XXXX_", "____X", "____X", "X___X", "_XXX_"])
_f("6", ["_XXX_", "X____", "XXXX_", "X___X", "X___X", "X___X", "_XXX_"])
_f("7", ["XXXXX", "____X", "___X_", "__X__", "_X___", "_X___", "_X___"])
_f("8", ["_XXX_", "X___X", "X___X", "_XXX_", "X___X", "X___X", "_XXX_"])
_f("9", ["_XXX_", "X___X", "X___X", "_XXXX", "____X", "____X", "_XXX_"])
_f(" ", ["_____", "_____", "_____", "_____", "_____", "_____", "_____"])
_f("!", ["__X__", "__X__", "__X__", "__X__", "__X__", "_____", "__X__"])
_f(".", ["_____", "_____", "_____", "_____", "_____", "_____", "__X__"])
_f(",", ["_____", "_____", "_____", "_____", "_____", "__X__", "_X___"])
_f("-", ["_____", "_____", "_____", "XXXXX", "_____", "_____", "_____"])
_f("?", ["_XXX_", "X___X", "____X", "__XX_", "__X__", "_____", "__X__"])
_f(":", ["_____", "__X__", "_____", "_____", "_____", "__X__", "_____"])
_f("'", ["__X__", "__X__", "_____", "_____", "_____", "_____", "_____"])
_f('"', ["_X_X_", "_X_X_", "_____", "_____", "_____", "_____", "_____"])

# ---------------------------------------------------------------------------
# Colormaps
# ---------------------------------------------------------------------------

_COLORMAPS: Dict[str, List[str]] = {
    "viridis": ["#440154", "#482777", "#3e4989", "#31688e", "#26828e",
                "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
    "plasma":  ["#0d0887", "#46039f", "#7201a8", "#9c179e", "#bd3786",
                "#d8576b", "#ed7953", "#fb9f3a", "#fdca26", "#f0f921"],
    "warm":    ["#fee090", "#fdae61", "#f46d43", "#d73027", "#a50026",
                "#fee090", "#fdae61", "#f46d43", "#d73027", "#a50026"],
    "cool":    ["#e0f3f8", "#abd9e9", "#74add1", "#4575b4", "#313695",
                "#e0f3f8", "#abd9e9", "#74add1", "#4575b4", "#313695"],
    "pastel":  ["#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff",
                "#e8baff", "#ffb3ba", "#ffdfba", "#ffffba", "#baffc9"],
    "mono":    ["#333333", "#555555", "#777777", "#999999", "#bbbbbb",
                "#333333", "#555555", "#777777", "#999999", "#bbbbbb"],
    "rainbow": ["#e6194b", "#f58231", "#ffe119", "#3cb44b", "#42d4f4",
                "#4363d8", "#911eb4", "#f032e6", "#fabebe", "#9a6324"],
}


def _color_for_index(idx: int, colormap: str = "viridis") -> str:
    """Pick a color from the given colormap by index."""
    palette = _COLORMAPS.get(colormap, _COLORMAPS["viridis"])
    return palette[idx % len(palette)]


# ---------------------------------------------------------------------------
# Voronoi computation (simple 2D brute-force for moderate point counts)
# ---------------------------------------------------------------------------

@dataclass
class VoronoiCell:
    """A single Voronoi cell clipped to a letter boundary."""
    seed: Tuple[float, float]
    vertices: List[Tuple[float, float]]
    letter: str
    color: str = ""


def _point_in_bitmap(px: float, py: float, glyph: List[List[int]],
                     ox: float, oy: float, pw: float, ph: float) -> bool:
    """Check if a point falls inside a filled pixel of the bitmap glyph."""
    cols = len(glyph[0]) if glyph else 0
    rows = len(glyph) if glyph else 0
    if cols == 0 or rows == 0:
        return False
    col = int((px - ox) / pw * cols)
    row = int((py - oy) / ph * rows)
    if 0 <= row < rows and 0 <= col < cols:
        return glyph[row][col] == 1
    return False


def _scatter_seeds(glyph: List[List[int]], ox: float, oy: float,
                   pw: float, ph: float, density: int,
                   jitter: float, rng: random.Random) -> List[Tuple[float, float]]:
    """Scatter seed points inside filled pixels of a glyph."""
    seeds: List[Tuple[float, float]] = []
    cols = len(glyph[0]) if glyph else 0
    rows = len(glyph) if glyph else 0
    if cols == 0 or rows == 0:
        return seeds

    cell_w = pw / cols
    cell_h = ph / rows

    for r in range(rows):
        for c in range(cols):
            if glyph[r][c] == 1:
                # scatter density points per filled pixel
                for _ in range(density):
                    bx = ox + (c + rng.random()) * cell_w
                    by = oy + (r + rng.random()) * cell_h
                    if jitter > 0:
                        bx += rng.gauss(0, cell_w * jitter * 0.3)
                        by += rng.gauss(0, cell_h * jitter * 0.3)
                    seeds.append((bx, by))
    return seeds


def _voronoi_cells_clipped(seeds: List[Tuple[float, float]],
                           glyph: List[List[int]], ox: float, oy: float,
                           pw: float, ph: float, letter: str,
                           colormap: str) -> List[VoronoiCell]:
    """Compute Voronoi cells for seeds and clip to letter bitmap boundary.

    Uses a pixel-sampling approach: for each pixel in the bounding box,
    find the nearest seed, then build polygon outlines per cell via
    marching through the grid.
    """
    if not seeds:
        return []

    # Rasterize assignment grid
    res_x = max(int(pw * 2), 40)
    res_y = max(int(ph * 2), 56)
    dx = pw / res_x
    dy = ph / res_y

    # Build grid of nearest-seed assignments (only in filled pixels)
    grid = [[-1] * res_x for _ in range(res_y)]
    for gy in range(res_y):
        py = oy + (gy + 0.5) * dy
        for gx in range(res_x):
            px = ox + (gx + 0.5) * dx
            if not _point_in_bitmap(px, py, glyph, ox, oy, pw, ph):
                continue
            best_d = float("inf")
            best_i = -1
            for i, (sx, sy) in enumerate(seeds):
                d = (px - sx) ** 2 + (py - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            grid[gy][gx] = best_i

    # Extract cell boundary pixels and build convex-ish outlines
    cells: List[VoronoiCell] = []
    seed_pixels: Dict[int, List[Tuple[float, float]]] = {}

    for gy in range(res_y):
        for gx in range(res_x):
            sid = grid[gy][gx]
            if sid < 0:
                continue
            # Check if this is a boundary pixel (neighbor has different id or is empty)
            is_boundary = False
            for ny, nx in [(gy - 1, gx), (gy + 1, gx), (gy, gx - 1), (gy, gx + 1)]:
                if ny < 0 or ny >= res_y or nx < 0 or nx >= res_x:
                    is_boundary = True
                    break
                if grid[ny][nx] != sid:
                    is_boundary = True
                    break
            if is_boundary:
                px = ox + (gx + 0.5) * dx
                py = oy + (gy + 0.5) * dy
                seed_pixels.setdefault(sid, []).append((px, py))

    for sid, boundary_pts in seed_pixels.items():
        if len(boundary_pts) < 3:
            continue
        # Sort points by angle from centroid for polygon ordering
        cx = sum(p[0] for p in boundary_pts) / len(boundary_pts)
        cy = sum(p[1] for p in boundary_pts) / len(boundary_pts)
        sorted_pts = sorted(boundary_pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
        color = _color_for_index(sid, colormap)
        cells.append(VoronoiCell(
            seed=seeds[sid], vertices=sorted_pts, letter=letter, color=color
        ))

    return cells


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_text_voronoi(
    text: str,
    seed_density: int = 3,
    colormap: str = "viridis",
    jitter: float = 0.2,
    char_width: float = 60.0,
    char_height: float = 84.0,
    spacing: float = 10.0,
    seed: Optional[int] = None,
) -> List[VoronoiCell]:
    """Render text as Voronoi cells inside letter shapes.

    Parameters
    ----------
    text : str
        Text to render (uppercase letters, digits, basic punctuation).
    seed_density : int
        Number of Voronoi seeds per filled pixel of the bitmap font.
    colormap : str
        Color palette name (viridis, plasma, warm, cool, pastel, mono, rainbow).
    jitter : float
        Randomness of seed placement (0 = grid-like, 1 = very scattered).
    char_width : float
        Width of each character cell in SVG units.
    char_height : float
        Height of each character cell in SVG units.
    spacing : float
        Horizontal gap between characters.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    list of VoronoiCell
        All Voronoi cells across all letters.
    """
    rng = random.Random(seed)
    text = text.upper()
    all_cells: List[VoronoiCell] = []
    color_idx = 0

    for i, ch in enumerate(text):
        glyph = _FONT.get(ch)
        if glyph is None:
            continue  # skip unknown characters
        ox = i * (char_width + spacing)
        oy = 0.0

        seeds = _scatter_seeds(glyph, ox, oy, char_width, char_height,
                               seed_density, jitter, rng)
        cells = _voronoi_cells_clipped(seeds, glyph, ox, oy,
                                       char_width, char_height, ch, colormap)
        # Re-color with global index for consistency across letters
        for cell in cells:
            cell.color = _color_for_index(color_idx, colormap)
            color_idx += 1

        all_cells.extend(cells)

    return all_cells


def export_text_svg(
    cells: List[VoronoiCell],
    path: str,
    background: Optional[str] = None,
    stroke_color: str = "#ffffff",
    stroke_width: float = 0.8,
    width: Optional[float] = None,
    height: Optional[float] = None,
    negative: bool = False,
) -> str:
    """Export Voronoi text cells to an SVG file.

    Parameters
    ----------
    cells : list of VoronoiCell
        Output from ``render_text_voronoi``.
    path : str
        Output SVG file path.
    background : str or None
        Background color (default: white or dark for negative mode).
    stroke_color : str
        Cell border color.
    stroke_width : float
        Cell border width.
    width, height : float or None
        Override SVG dimensions (auto-calculated if None).
    negative : bool
        If True, render cells outside letters (background fill).

    Returns
    -------
    str
        The SVG content as a string.
    """
    if not cells:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"

    # Compute bounds
    all_x = [x for c in cells for x, y in c.vertices]
    all_y = [y for c in cells for x, y in c.vertices]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    pad = 10
    vw = (width or (max_x - min_x + 2 * pad))
    vh = (height or (max_y - min_y + 2 * pad))

    if background is None:
        background = "#1a1a2e" if negative else "#ffffff"

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=f"{vw:.1f}", height=f"{vh:.1f}",
                     viewBox=f"{min_x - pad:.1f} {min_y - pad:.1f} {vw:.1f} {vh:.1f}")

    # Background
    ET.SubElement(svg, "rect", x=f"{min_x - pad:.1f}", y=f"{min_y - pad:.1f}",
                  width=f"{vw:.1f}", height=f"{vh:.1f}", fill=background)

    # Group cells by letter
    groups: Dict[str, List[VoronoiCell]] = {}
    for cell in cells:
        groups.setdefault(cell.letter, []).append(cell)

    for letter, letter_cells in groups.items():
        g = ET.SubElement(svg, "g", id=f"letter-{letter}")
        for cell in letter_cells:
            if len(cell.vertices) < 3:
                continue
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in cell.vertices)
            fill = background if negative else cell.color
            ET.SubElement(g, "polygon", points=pts, fill=fill,
                          stroke=stroke_color, **{"stroke-width": f"{stroke_width}"})

    content = ET.tostring(svg, encoding="unicode", xml_declaration=False)
    content = '<?xml version="1.0" encoding="UTF-8"?>\n' + content

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    return content


def export_text_html(
    cells: List[VoronoiCell],
    path: str,
    background: Optional[str] = None,
    stroke_color: str = "#ffffff",
    stroke_width: float = 0.8,
    title: str = "Voronoi Typography",
) -> str:
    """Export Voronoi text as interactive HTML with hover effects.

    Parameters
    ----------
    cells : list of VoronoiCell
        Output from ``render_text_voronoi``.
    path : str
        Output HTML file path.
    background : str or None
        Background color.
    stroke_color : str
        Cell border color.
    stroke_width : float
        Cell border width.
    title : str
        HTML page title.

    Returns
    -------
    str
        The HTML content as a string.
    """
    if background is None:
        background = "#1a1a2e"

    # Build inline SVG
    svg_content = export_text_svg(cells, "", background=background,
                                  stroke_color=stroke_color,
                                  stroke_width=stroke_width) if False else ""

    # Generate SVG directly for embedding
    if not cells:
        svg_body = ""
        vb = "0 0 100 100"
    else:
        all_x = [x for c in cells for x, y in c.vertices]
        all_y = [y for c in cells for x, y in c.vertices]
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        pad = 10
        vw = max_x - min_x + 2 * pad
        vh = max_y - min_y + 2 * pad
        vb = f"{min_x - pad:.1f} {min_y - pad:.1f} {vw:.1f} {vh:.1f}"

        parts = [f'<rect x="{min_x - pad:.1f}" y="{min_y - pad:.1f}" '
                 f'width="{vw:.1f}" height="{vh:.1f}" fill="{background}"/>']

        for cell in cells:
            if len(cell.vertices) < 3:
                continue
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in cell.vertices)
            parts.append(
                f'<polygon points="{pts}" fill="{cell.color}" '
                f'stroke="{stroke_color}" stroke-width="{stroke_width}" '
                f'data-letter="{cell.letter}" class="vcell"/>'
            )
        svg_body = "\n".join(parts)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body {{ margin: 0; display: flex; justify-content: center; align-items: center;
         min-height: 100vh; background: {background}; font-family: sans-serif; }}
  svg {{ max-width: 95vw; max-height: 90vh; }}
  .vcell {{ transition: opacity 0.15s, transform 0.15s; cursor: pointer; }}
  .vcell:hover {{ opacity: 0.75; stroke-width: {stroke_width * 2}; }}
  .info {{ position: fixed; bottom: 12px; right: 12px; color: #888;
           font-size: 12px; }}
</style>
</head>
<body>
<svg viewBox="{vb}" xmlns="http://www.w3.org/2000/svg">
{svg_body}
</svg>
<div class="info">{len(cells)} cells · Voronoi Typography</div>
<script>
document.querySelectorAll('.vcell').forEach(el => {{
  el.addEventListener('mouseenter', () => {{
    const letter = el.dataset.letter;
    document.querySelectorAll('.vcell').forEach(c => {{
      c.style.opacity = c.dataset.letter === letter ? '1' : '0.3';
    }});
  }});
  el.addEventListener('mouseleave', () => {{
    document.querySelectorAll('.vcell').forEach(c => c.style.opacity = '1');
  }});
}});
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(
        description="Voronoi Typography — render text as Voronoi cell mosaics"
    )
    parser.add_argument("text", help="Text to render")
    parser.add_argument("output", help="Output file path (.svg or .html)")
    parser.add_argument("--density", type=int, default=3,
                        help="Seed points per filled font pixel (default: 3)")
    parser.add_argument("--colormap", default="viridis",
                        choices=list(_COLORMAPS.keys()),
                        help="Color palette (default: viridis)")
    parser.add_argument("--jitter", type=float, default=0.2,
                        help="Seed position randomness 0–1 (default: 0.2)")
    parser.add_argument("--char-width", type=float, default=60.0,
                        help="Character cell width in SVG units (default: 60)")
    parser.add_argument("--char-height", type=float, default=84.0,
                        help="Character cell height in SVG units (default: 84)")
    parser.add_argument("--spacing", type=float, default=10.0,
                        help="Gap between characters (default: 10)")
    parser.add_argument("--background", default=None,
                        help="Background color (default: auto)")
    parser.add_argument("--stroke-color", default="#ffffff",
                        help="Cell stroke color (default: #ffffff)")
    parser.add_argument("--stroke-width", type=float, default=0.8,
                        help="Cell stroke width (default: 0.8)")
    parser.add_argument("--format", choices=["svg", "html"], default=None,
                        help="Output format (auto-detected from extension)")
    parser.add_argument("--negative", action="store_true",
                        help="Negative space mode — fill background, not letters")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")

    args = parser.parse_args(argv)

    fmt = args.format
    if fmt is None:
        if args.output.lower().endswith(".html"):
            fmt = "html"
        else:
            fmt = "svg"

    cells = render_text_voronoi(
        text=args.text,
        seed_density=args.density,
        colormap=args.colormap,
        jitter=args.jitter,
        char_width=args.char_width,
        char_height=args.char_height,
        spacing=args.spacing,
        seed=args.seed,
    )

    print(f"Generated {len(cells)} Voronoi cells for '{args.text.upper()}'")

    if fmt == "html":
        export_text_html(cells, args.output, background=args.background,
                         stroke_color=args.stroke_color,
                         stroke_width=args.stroke_width)
    else:
        export_text_svg(cells, args.output, background=args.background,
                        stroke_color=args.stroke_color,
                        stroke_width=args.stroke_width,
                        negative=args.negative)

    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
