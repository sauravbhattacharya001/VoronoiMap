"""Automatic Label Placement for Voronoi Diagrams.

Places text labels inside Voronoi cells at optimal positions using the
pole of inaccessibility algorithm (the point inside a polygon furthest
from any edge).  This produces clean, non-overlapping labels centered
within each cell — essential for cartographic visualization.

Three placement strategies:

- **Pole of Inaccessibility (POI)** — optimal interior point furthest
  from polygon edges.  Best visual quality; works well for irregular cells.
- **Centroid** — geometric center of the cell polygon.  Fast but may
  fall outside concave cells.
- **Visual Center** — POI with fallback to centroid for degenerate cells.

Features:

- Automatic font sizing relative to cell area
- Label collision detection and priority-based removal
- Custom label text from data attributes or auto-generated IDs
- SVG and interactive HTML export with hover highlighting
- CSV/JSON export of label positions

Usage (Python API)::

    import vormap
    from vormap_label import place_labels, LabelConfig, export_labeled_svg

    data = vormap.load_data("datauni5.txt")
    seeds, regions = vormap.estimate(data, 5)

    # Place labels with default settings
    labels = place_labels(seeds, regions)
    for lbl in labels:
        print(f"Cell {lbl.text} at ({lbl.x:.1f}, {lbl.y:.1f})")

    # Export labeled SVG
    export_labeled_svg(seeds, regions, labels, "labeled.svg")

    # Interactive HTML with hover
    export_labeled_html(seeds, regions, labels, "labeled.html")

CLI::

    voronoimap datauni5.txt 5 --labels
    voronoimap datauni5.txt 5 --labels-svg labeled.svg
    voronoimap datauni5.txt 5 --labels-html labeled.html
    voronoimap datauni5.txt 5 --labels-json positions.json
    voronoimap datauni5.txt 5 --labels --strategy poi
    voronoimap datauni5.txt 5 --labels --label-field name
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from vormap import validate_output_path
from vormap_geometry import polygon_area as _polygon_area, polygon_centroid as _polygon_centroid


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LabelConfig:
    """Configuration for label placement.

    Attributes
    ----------
    strategy : str
        Placement strategy: ``'poi'``, ``'centroid'``, or ``'visual'``.
    font_size_min : float
        Minimum font size in SVG units.
    font_size_max : float
        Maximum font size in SVG units.
    font_family : str
        CSS font family.
    font_color : str
        Label text color.
    show_index : bool
        If True and no label_field, show 1-based index.
    label_field : str or None
        Attribute name to use as label text (from data dicts).
    collision_remove : bool
        Remove labels that overlap other labels.
    padding : float
        Padding factor (0-1) — shrinks effective cell for placement.
    """

    strategy: str = "poi"
    font_size_min: float = 8.0
    font_size_max: float = 24.0
    font_family: str = "sans-serif"
    font_color: str = "#222222"
    show_index: bool = True
    label_field: Optional[str] = None
    collision_remove: bool = True
    padding: float = 0.1


@dataclass
class PlacedLabel:
    """A label placed inside a Voronoi cell.

    Attributes
    ----------
    seed : tuple
        Seed point ``(x, y)`` of the cell.
    x : float
        Label x position.
    y : float
        Label y position.
    text : str
        Label text content.
    font_size : float
        Computed font size.
    cell_area : float
        Area of the Voronoi cell.
    max_radius : float
        Distance from label point to nearest edge (inscribed radius).
    """

    seed: Tuple[float, float]
    x: float
    y: float
    text: str
    font_size: float
    cell_area: float
    max_radius: float


# ---------------------------------------------------------------------------
def _point_to_segment_dist(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
) -> float:
    """Distance from point (px, py) to segment (ax, ay)-(bx, by)."""
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _dist_to_polygon_edge(
    px: float, py: float, vertices: List[Tuple[float, float]]
) -> float:
    """Minimum distance from a point to any edge of a polygon."""
    n = len(vertices)
    if n < 2:
        if n == 1:
            return math.hypot(px - vertices[0][0], py - vertices[0][1])
        return float("inf")
    min_d = float("inf")
    for i in range(n):
        j = (i + 1) % n
        d = _point_to_segment_dist(
            px, py,
            vertices[i][0], vertices[i][1],
            vertices[j][0], vertices[j][1],
        )
        if d < min_d:
            min_d = d
    return min_d


def _point_in_polygon(
    px: float, py: float, vertices: List[Tuple[float, float]]
) -> bool:
    """Ray casting point-in-polygon test."""
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        yi, yj = vertices[i][1], vertices[j][1]
        xi, xj = vertices[i][0], vertices[j][0]
        if ((yi > py) != (yj > py)) and (
            px < (xj - xi) * (py - yi) / (yj - yi + 1e-30) + xi
        ):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Pole of Inaccessibility (iterative grid refinement)
# ---------------------------------------------------------------------------

def _pole_of_inaccessibility(
    vertices: List[Tuple[float, float]],
    precision: float = 1.0,
) -> Tuple[float, float, float]:
    """Find pole of inaccessibility using iterative grid search.

    Returns ``(x, y, distance)`` where distance is the radius of the
    largest inscribed circle.
    """
    if len(vertices) < 3:
        cx, cy = _polygon_centroid(vertices)
        return (cx, cy, 0.0)

    # Bounding box
    min_x = min(v[0] for v in vertices)
    max_x = max(v[0] for v in vertices)
    min_y = min(v[1] for v in vertices)
    max_y = max(v[1] for v in vertices)

    width = max_x - min_x
    height = max_y - min_y
    if width < 1e-12 or height < 1e-12:
        cx, cy = _polygon_centroid(vertices)
        return (cx, cy, 0.0)

    cell_size = max(width, height)
    h = cell_size / 2.0

    # Priority queue as sorted list (simple approach)
    # Each cell: (neg_max_dist, cx, cy, half_size)
    # neg_max_dist for min-heap behavior via sorting

    def _cell_dist(cx: float, cy: float) -> float:
        if _point_in_polygon(cx, cy, vertices):
            return _dist_to_polygon_edge(cx, cy, vertices)
        return -_dist_to_polygon_edge(cx, cy, vertices)

    # Centroid as initial best
    centroid = _polygon_centroid(vertices)
    best_x, best_y = centroid
    best_dist = _cell_dist(best_x, best_y)

    # Initial grid
    cells = []
    x = min_x
    while x < max_x:
        y = min_y
        while y < max_y:
            cx, cy = x + h, y + h
            d = _cell_dist(cx, cy)
            if d > best_dist:
                best_x, best_y, best_dist = cx, cy, d
            cells.append((cx, cy, h, d))
            y += cell_size
        x += cell_size

    # Iterative refinement
    iterations = 0
    max_iterations = 50
    while cells and iterations < max_iterations:
        iterations += 1
        next_cells = []
        for cx, cy, ch, cd in cells:
            # Max possible distance in this cell
            max_possible = cd + ch * math.sqrt(2)
            if max_possible <= best_dist + precision:
                continue
            # Subdivide
            nh = ch / 2.0
            for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                nx, ny = cx + dx * nh, cy + dy * nh
                d = _cell_dist(nx, ny)
                if d > best_dist:
                    best_x, best_y, best_dist = nx, ny, d
                next_cells.append((nx, ny, nh, d))
        cells = next_cells

    return (best_x, best_y, max(best_dist, 0.0))


# ---------------------------------------------------------------------------
# Label placement
# ---------------------------------------------------------------------------

def place_labels(
    seeds: list,
    regions: dict,
    config: Optional[LabelConfig] = None,
    data: Optional[list] = None,
) -> List[PlacedLabel]:
    """Place labels inside Voronoi cells.

    Parameters
    ----------
    seeds : list of (float, float)
        Seed point coordinates.
    regions : dict
        Maps seed ``(x, y)`` → vertex list.
    config : LabelConfig, optional
        Label configuration.  Uses defaults if not provided.
    data : list of dict, optional
        Original data dicts for label_field lookup.

    Returns
    -------
    list of PlacedLabel
        Placed labels sorted by cell area (largest first).
    """
    if config is None:
        config = LabelConfig()

    labels: List[PlacedLabel] = []
    areas = []

    for idx, seed in enumerate(seeds):
        key = tuple(seed) if not isinstance(seed, tuple) else seed
        verts = regions.get(key, [])
        if not verts or len(verts) < 3:
            continue

        area = _polygon_area(verts)
        areas.append(area)

        # Placement
        if config.strategy == "centroid":
            lx, ly = _polygon_centroid(verts)
            radius = _dist_to_polygon_edge(lx, ly, verts) if _point_in_polygon(lx, ly, verts) else 0.0
        elif config.strategy == "poi":
            precision = max(1.0, math.sqrt(area) * 0.01)
            lx, ly, radius = _pole_of_inaccessibility(verts, precision)
        else:  # visual
            precision = max(1.0, math.sqrt(area) * 0.01)
            lx, ly, radius = _pole_of_inaccessibility(verts, precision)
            if not _point_in_polygon(lx, ly, verts):
                lx, ly = _polygon_centroid(verts)
                radius = 0.0

        # Label text
        if config.label_field and data and idx < len(data):
            text = str(data[idx].get(config.label_field, idx + 1))
        elif config.show_index:
            text = str(idx + 1)
        else:
            text = ""

        labels.append(PlacedLabel(
            seed=key,
            x=lx,
            y=ly,
            text=text,
            font_size=0.0,  # computed below
            cell_area=area,
            max_radius=radius,
        ))

    # Compute font sizes relative to area range
    if labels and areas:
        min_area = min(a for a in areas if a > 0) if any(a > 0 for a in areas) else 1.0
        max_area = max(areas) if areas else 1.0
        area_range = max_area - min_area if max_area > min_area else 1.0

        for lbl in labels:
            t = (lbl.cell_area - min_area) / area_range if area_range > 0 else 0.5
            lbl.font_size = config.font_size_min + t * (config.font_size_max - config.font_size_min)
            # Clamp to inscribed radius
            if lbl.max_radius > 0:
                max_font = lbl.max_radius * 0.8
                if lbl.font_size > max_font:
                    lbl.font_size = max(config.font_size_min, max_font)

    # Collision removal
    if config.collision_remove and labels:
        labels.sort(key=lambda l: l.cell_area, reverse=True)
        kept: List[PlacedLabel] = []
        for lbl in labels:
            overlap = False
            for existing in kept:
                dist = math.hypot(lbl.x - existing.x, lbl.y - existing.y)
                min_sep = (lbl.font_size + existing.font_size) * 0.6
                if dist < min_sep:
                    overlap = True
                    break
            if not overlap:
                kept.append(lbl)
        labels = kept

    return labels


# ---------------------------------------------------------------------------
# Export: SVG
# ---------------------------------------------------------------------------

_CELL_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#bab0ac",
]


def export_labeled_svg(
    seeds: list,
    regions: dict,
    labels: List[PlacedLabel],
    path: str,
    width: int = 800,
    height: int = 800,
    bg_color: str = "#ffffff",
) -> str:
    """Export Voronoi diagram with labels as SVG.

    Returns the SVG string and writes to *path*.
    """
    validate_output_path(path)

    # Compute bounds
    all_pts = []
    for verts in regions.values():
        all_pts.extend(verts)
    if not all_pts:
        all_pts = [(0, 0), (100, 100)]

    min_x = min(p[0] for p in all_pts)
    max_x = max(p[0] for p in all_pts)
    min_y = min(p[1] for p in all_pts)
    max_y = max(p[1] for p in all_pts)
    pad = max((max_x - min_x), (max_y - min_y)) * 0.05
    vb_x, vb_y = min_x - pad, min_y - pad
    vb_w = (max_x - min_x) + 2 * pad
    vb_h = (max_y - min_y) + 2 * pad

    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": f"{vb_x:.2f} {vb_y:.2f} {vb_w:.2f} {vb_h:.2f}",
    })

    # Background
    ET.SubElement(svg, "rect", {
        "x": f"{vb_x:.2f}", "y": f"{vb_y:.2f}",
        "width": f"{vb_w:.2f}", "height": f"{vb_h:.2f}",
        "fill": bg_color,
    })

    # Cells
    for idx, seed in enumerate(seeds):
        key = tuple(seed) if not isinstance(seed, tuple) else seed
        verts = regions.get(key, [])
        if not verts or len(verts) < 3:
            continue
        pts_str = " ".join(f"{v[0]:.2f},{v[1]:.2f}" for v in verts)
        ET.SubElement(svg, "polygon", {
            "points": pts_str,
            "fill": _CELL_COLORS[idx % len(_CELL_COLORS)],
            "fill-opacity": "0.3",
            "stroke": "#333333",
            "stroke-width": f"{max(vb_w, vb_h) * 0.002:.2f}",
        })

    # Seed dots
    dot_r = max(vb_w, vb_h) * 0.004
    for seed in seeds:
        key = tuple(seed) if not isinstance(seed, tuple) else seed
        ET.SubElement(svg, "circle", {
            "cx": f"{key[0]:.2f}",
            "cy": f"{key[1]:.2f}",
            "r": f"{dot_r:.2f}",
            "fill": "#333333",
        })

    # Labels
    # Scale font sizes to viewBox
    scale = max(vb_w, vb_h) / 800.0
    for lbl in labels:
        fs = lbl.font_size * scale
        ET.SubElement(svg, "text", {
            "x": f"{lbl.x:.2f}",
            "y": f"{lbl.y:.2f}",
            "text-anchor": "middle",
            "dominant-baseline": "central",
            "font-family": "sans-serif",
            "font-size": f"{fs:.1f}",
            "fill": "#222222",
            "font-weight": "600",
        }).text = lbl.text

    tree = ET.ElementTree(svg)
    ET.indent(tree)
    xml_str = ET.tostring(svg, encoding="unicode", xml_declaration=False)
    xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str

    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_str)

    return xml_str


# ---------------------------------------------------------------------------
# Export: Interactive HTML
# ---------------------------------------------------------------------------

def export_labeled_html(
    seeds: list,
    regions: dict,
    labels: List[PlacedLabel],
    path: str,
    title: str = "VoronoiMap — Labeled Diagram",
) -> str:
    """Export interactive HTML with hover-highlighted labeled cells."""
    validate_output_path(path)

    cells_js = []
    for idx, seed in enumerate(seeds):
        key = tuple(seed) if not isinstance(seed, tuple) else seed
        verts = regions.get(key, [])
        if not verts or len(verts) < 3:
            continue
        cells_js.append({
            "seed": list(key),
            "vertices": [[round(v[0], 2), round(v[1], 2)] for v in verts],
            "color": _CELL_COLORS[idx % len(_CELL_COLORS)],
            "area": round(_polygon_area(verts), 2),
        })

    labels_js = [
        {
            "x": round(l.x, 2),
            "y": round(l.y, 2),
            "text": l.text,
            "fontSize": round(l.font_size, 1),
            "area": round(l.cell_area, 2),
        }
        for l in labels
    ]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #1a1a2e; font-family: system-ui, sans-serif; display: flex;
       flex-direction: column; align-items: center; min-height: 100vh; color: #e0e0e0; }}
h1 {{ margin: 20px 0 10px; font-size: 1.4rem; color: #e0e0e0; }}
.controls {{ margin-bottom: 12px; display: flex; gap: 12px; align-items: center; }}
.controls label {{ font-size: 0.85rem; }}
canvas {{ border: 1px solid #444; border-radius: 8px; cursor: crosshair; background: #fff; }}
#tooltip {{ position: fixed; background: rgba(0,0,0,0.85); color: #fff; padding: 8px 12px;
            border-radius: 6px; font-size: 0.8rem; pointer-events: none; display: none;
            z-index: 100; max-width: 220px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="controls">
  <label><input type="checkbox" id="showLabels" checked> Labels</label>
  <label><input type="checkbox" id="showSeeds" checked> Seeds</label>
  <label><input type="checkbox" id="darkCells"> Dark cells</label>
</div>
<canvas id="c" width="800" height="800"></canvas>
<div id="tooltip"></div>
<script>
const cells = {json.dumps(cells_js)};
const labels = {json.dumps(labels_js)};

const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
const tooltip = document.getElementById('tooltip');

let hoveredCell = -1;

function getBounds() {{
  let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
  cells.forEach(c => c.vertices.forEach(v => {{
    if(v[0]<minX) minX=v[0]; if(v[0]>maxX) maxX=v[0];
    if(v[1]<minY) minY=v[1]; if(v[1]>maxY) maxY=v[1];
  }}));
  const pad = Math.max(maxX-minX, maxY-minY)*0.05;
  return {{ minX: minX-pad, minY: minY-pad, w: (maxX-minX)+2*pad, h: (maxY-minY)+2*pad }};
}}

const bounds = getBounds();

function toCanvas(x, y) {{
  return [(x - bounds.minX) / bounds.w * 800, (y - bounds.minY) / bounds.h * 800];
}}

function toData(cx, cy) {{
  return [cx / 800 * bounds.w + bounds.minX, cy / 800 * bounds.h + bounds.minY];
}}

function pointInPoly(px, py, verts) {{
  let inside = false;
  for (let i = 0, j = verts.length - 1; i < verts.length; j = i++) {{
    const xi = verts[i][0], yi = verts[i][1];
    const xj = verts[j][0], yj = verts[j][1];
    if ((yi > py) !== (yj > py) && px < (xj - xi) * (py - yi) / (yj - yi) + xi)
      inside = !inside;
  }}
  return inside;
}}

function draw() {{
  const showLabels = document.getElementById('showLabels').checked;
  const showSeeds = document.getElementById('showSeeds').checked;
  const darkCells = document.getElementById('darkCells').checked;
  ctx.clearRect(0, 0, 800, 800);
  ctx.fillStyle = darkCells ? '#1a1a2e' : '#ffffff';
  ctx.fillRect(0, 0, 800, 800);

  // Draw cells
  cells.forEach((cell, i) => {{
    ctx.beginPath();
    cell.vertices.forEach((v, vi) => {{
      const [cx, cy] = toCanvas(v[0], v[1]);
      vi === 0 ? ctx.moveTo(cx, cy) : ctx.lineTo(cx, cy);
    }});
    ctx.closePath();
    ctx.fillStyle = cell.color + (i === hoveredCell ? '80' : '40');
    ctx.fill();
    ctx.strokeStyle = darkCells ? '#666' : '#333';
    ctx.lineWidth = i === hoveredCell ? 2.5 : 1;
    ctx.stroke();
  }});

  // Seeds
  if (showSeeds) {{
    cells.forEach(cell => {{
      const [cx, cy] = toCanvas(cell.seed[0], cell.seed[1]);
      ctx.beginPath();
      ctx.arc(cx, cy, 3, 0, Math.PI * 2);
      ctx.fillStyle = darkCells ? '#eee' : '#333';
      ctx.fill();
    }});
  }}

  // Labels
  if (showLabels) {{
    const scale = 800 / Math.max(bounds.w, bounds.h);
    labels.forEach(lbl => {{
      const [cx, cy] = toCanvas(lbl.x, lbl.y);
      const fs = Math.max(10, Math.min(28, lbl.fontSize * scale * 0.8));
      ctx.font = `600 ${{fs}}px system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = darkCells ? '#e0e0e0' : '#222';
      ctx.fillText(lbl.text, cx, cy);
    }});
  }}
}}

canvas.addEventListener('mousemove', e => {{
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const [dx, dy] = toData(mx, my);
  let found = -1;
  for (let i = 0; i < cells.length; i++) {{
    if (pointInPoly(dx, dy, cells[i].vertices)) {{ found = i; break; }}
  }}
  if (found !== hoveredCell) {{
    hoveredCell = found;
    draw();
  }}
  if (found >= 0) {{
    tooltip.style.display = 'block';
    tooltip.style.left = (e.clientX + 12) + 'px';
    tooltip.style.top = (e.clientY - 8) + 'px';
    const c = cells[found];
    const lbl = labels.find(l => Math.abs(l.area - c.area) < 1);
    tooltip.innerHTML = `<b>Cell ${{lbl ? lbl.text : found+1}}</b><br>` +
      `Area: ${{c.area.toFixed(1)}}<br>Seed: (${{c.seed[0].toFixed(1)}}, ${{c.seed[1].toFixed(1)}})`;
  }} else {{
    tooltip.style.display = 'none';
  }}
}});

canvas.addEventListener('mouseleave', () => {{
  hoveredCell = -1;
  tooltip.style.display = 'none';
  draw();
}});

document.querySelectorAll('.controls input').forEach(el => el.addEventListener('change', draw));
draw();
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    return html


# ---------------------------------------------------------------------------
# Export: JSON / CSV
# ---------------------------------------------------------------------------

def export_labels_json(labels: List[PlacedLabel], path: str) -> None:
    """Export label positions as JSON."""
    validate_output_path(path)
    data = [
        {
            "text": l.text,
            "x": round(l.x, 4),
            "y": round(l.y, 4),
            "font_size": round(l.font_size, 2),
            "cell_area": round(l.cell_area, 4),
            "max_radius": round(l.max_radius, 4),
            "seed_x": round(l.seed[0], 4),
            "seed_y": round(l.seed[1], 4),
        }
        for l in labels
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def export_labels_csv(labels: List[PlacedLabel], path: str) -> None:
    """Export label positions as CSV."""
    validate_output_path(path)
    lines = ["text,x,y,font_size,cell_area,max_radius,seed_x,seed_y"]
    for l in labels:
        lines.append(
            f"{l.text},{l.x:.4f},{l.y:.4f},{l.font_size:.2f},"
            f"{l.cell_area:.4f},{l.max_radius:.4f},"
            f"{l.seed[0]:.4f},{l.seed[1]:.4f}"
        )
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def add_label_args(parser) -> None:
    """Add label-related CLI arguments to an argparse parser."""
    grp = parser.add_argument_group("Label Placement")
    grp.add_argument("--labels", action="store_true", help="Print label positions to stdout")
    grp.add_argument("--labels-svg", metavar="PATH", help="Export labeled SVG")
    grp.add_argument("--labels-html", metavar="PATH", help="Export interactive labeled HTML")
    grp.add_argument("--labels-json", metavar="PATH", help="Export label positions as JSON")
    grp.add_argument("--labels-csv", metavar="PATH", help="Export label positions as CSV")
    grp.add_argument(
        "--label-strategy",
        choices=["poi", "centroid", "visual"],
        default="poi",
        help="Label placement strategy (default: poi)",
    )
    grp.add_argument("--label-field", metavar="FIELD", help="Data attribute to use as label text")


def run_labels(args, seeds, regions, data=None) -> bool:
    """Execute label commands from parsed CLI args.  Returns True if any ran."""
    wants = any(getattr(args, a, None) for a in [
        "labels", "labels_svg", "labels_html", "labels_json", "labels_csv",
    ])
    if not wants:
        return False

    config = LabelConfig(
        strategy=getattr(args, "label_strategy", "poi"),
        label_field=getattr(args, "label_field", None),
    )
    labels = place_labels(seeds, regions, config, data)

    if getattr(args, "labels", False):
        print(f"\n{'Label':<8} {'X':>10} {'Y':>10} {'FontSize':>10} {'Area':>12} {'Radius':>10}")
        print("-" * 66)
        for l in labels:
            print(f"{l.text:<8} {l.x:>10.2f} {l.y:>10.2f} {l.font_size:>10.1f} {l.cell_area:>12.2f} {l.max_radius:>10.2f}")
        print(f"\nTotal labels placed: {len(labels)}")

    if getattr(args, "labels_svg", None):
        export_labeled_svg(seeds, regions, labels, args.labels_svg)
        print(f"Labeled SVG → {args.labels_svg}")

    if getattr(args, "labels_html", None):
        export_labeled_html(seeds, regions, labels, args.labels_html)
        print(f"Labeled HTML → {args.labels_html}")

    if getattr(args, "labels_json", None):
        export_labels_json(labels, args.labels_json)
        print(f"Labels JSON → {args.labels_json}")

    if getattr(args, "labels_csv", None):
        export_labels_csv(labels, args.labels_csv)
        print(f"Labels CSV → {args.labels_csv}")

    return True


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import vormap

    parser = argparse.ArgumentParser(
        description="Automatic label placement for Voronoi diagrams",
    )
    parser.add_argument("datafile", help="Input data file")
    parser.add_argument("nseeds", type=int, help="Number of seed points")
    add_label_args(parser)
    args = parser.parse_args()

    # Default to --labels if nothing specific requested
    if not any(getattr(args, a, None) for a in [
        "labels", "labels_svg", "labels_html", "labels_json", "labels_csv",
    ]):
        args.labels = True

    data = vormap.load_data(args.datafile)
    seeds, regions = vormap.estimate(data, args.nseeds)
    run_labels(args, seeds, regions, data)
