"""Voronoi density heatmap visualization.

Colors Voronoi regions by point density — smaller cells (higher density)
get warmer colors, larger cells (lower density) get cooler colors.
Supports SVG and interactive HTML output with a color legend.

Usage::

    from vormap_heatmap import export_heatmap_svg, export_heatmap_html
    import vormap, vormap_viz

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    export_heatmap_svg(regions, data, "heatmap.svg")
    export_heatmap_html(regions, data, "heatmap.html")

CLI::

    voronoimap datauni5.txt 5 --heatmap heatmap.svg
    voronoimap datauni5.txt 5 --heatmap-html heatmap.html
    voronoimap datauni5.txt 5 --heatmap heatmap.svg --heatmap-metric compactness
"""

import colorsys
import html as _html_mod
import json
import math
import xml.etree.ElementTree as ET

import vormap


# ── Color ramps ──────────────────────────────────────────────────────

def _viridis_approx(t):
    """Approximate viridis-like color for t in [0,1]."""
    r = max(0, min(1, -0.35 + 1.5 * t ** 2 + 0.9 * t ** 3))
    g = max(0, min(1, -0.05 + 0.8 * t - 0.2 * t ** 2))
    b = max(0, min(1, 0.5 + 0.5 * math.sin(math.pi * (0.7 - t))))
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _hot_cold(t):
    """Blue (cold/low) → white → red (hot/high) color ramp."""
    if t < 0.5:
        ratio = t * 2
        r, g, b = ratio, ratio, 1.0
    else:
        ratio = (t - 0.5) * 2
        r, g, b = 1.0, 1.0 - ratio, 1.0 - ratio
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _plasma_approx(t):
    """Plasma-like color ramp: dark blue → magenta → yellow."""
    r = max(0, min(1, 0.05 + 0.95 * t))
    g = max(0, min(1, -0.4 + 1.8 * t ** 2))
    b = max(0, min(1, 0.55 - 0.6 * t + 0.5 * math.sin(math.pi * t)))
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


_HEATMAP_RAMPS = {
    "hot_cold": _hot_cold,
    "viridis": _viridis_approx,
    "plasma": _plasma_approx,
}

DEFAULT_HEATMAP_RAMP = "hot_cold"


# ── Metrics ──────────────────────────────────────────────────────────

from vormap_geometry import (
    polygon_area as _compute_region_area,
    polygon_perimeter as _compute_perimeter,
    isoperimetric_quotient as _isoperimetric_quotient,
)


def _compute_metric(verts, metric):
    """Compute a metric value for a region's vertices."""
    if metric == "density":
        area = _compute_region_area(verts)
        return 1.0 / max(area, 1e-12)
    elif metric == "area":
        return _compute_region_area(verts)
    elif metric == "compactness":
        area = _compute_region_area(verts)
        perim = _compute_perimeter(verts)
        return _isoperimetric_quotient(area, perim)
    elif metric == "vertices":
        return float(len(verts))
    else:
        raise ValueError(
            "Unknown metric '%s'. Available: density, area, compactness, vertices" % metric
        )


# ── SVG export ───────────────────────────────────────────────────────

def export_heatmap_svg(
    regions,
    data,
    output_path,
    *,
    width=800,
    height=600,
    margin=40,
    color_ramp=DEFAULT_HEATMAP_RAMP,
    metric="density",
    show_points=True,
    show_values=False,
    stroke_color="#333333",
    stroke_width=0.8,
    point_radius=2.5,
    point_color="#000000",
    background="#ffffff",
    title=None,
):
    """Export a density heatmap SVG of Voronoi regions.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    output_path : str
        Path to write the SVG file.
    metric : str
        What to color by: 'density' (default, inverse area), 'area',
        'compactness', or 'vertices'.
    color_ramp : str
        Color ramp: 'hot_cold' (default), 'viridis', 'plasma'.
    show_values : bool
        If True, label each cell with its metric value.
    """
    if color_ramp not in _HEATMAP_RAMPS:
        raise ValueError(
            "Unknown color ramp '%s'. Available: %s"
            % (color_ramp, ", ".join(sorted(_HEATMAP_RAMPS)))
        )

    ramp_fn = _HEATMAP_RAMPS[color_ramp]

    sorted_seeds = sorted(regions.keys())
    metrics = {}
    for seed in sorted_seeds:
        metrics[seed] = _compute_metric(regions[seed], metric)

    values = list(metrics.values())
    min_val = min(values) if values else 0
    max_val = max(values) if values else 1
    val_range = max_val - min_val if max_val > min_val else 1.0

    from vormap_viz import _CoordinateTransform
    transform = _CoordinateTransform(
        regions, data, width=width, height=height, margin=margin,
    )
    tx = transform.tx
    ty = transform.ty

    svg = ET.Element("svg", {
        "xmlns": "http://www.w3.org/2000/svg",
        "width": str(width),
        "height": str(height),
        "viewBox": "0 0 %d %d" % (width, height),
    })

    style = ET.SubElement(svg, "style")
    style.text = (
        ".region { stroke: %s; stroke-width: %.1f; stroke-linejoin: round; }"
        " .seed { fill: %s; stroke: #fff; stroke-width: 0.8; }"
        " .val-label { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 9px; fill: #333; text-anchor: middle;"
        " dominant-baseline: central; pointer-events: none; }"
        " .title { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 16px; font-weight: 600; fill: #222;"
        " text-anchor: middle; }"
        " .legend-label { font-family: 'Helvetica Neue', Arial, sans-serif;"
        " font-size: 10px; fill: #333; }"
        % (vormap.sanitize_css_value(stroke_color), stroke_width,
           vormap.sanitize_css_value(point_color))
    )

    ET.SubElement(svg, "rect", {
        "width": str(width), "height": str(height), "fill": background,
    })

    if title:
        title_el = ET.SubElement(svg, "text", {
            "x": str(width / 2), "y": str(margin / 2 + 4), "class": "title",
        })
        title_el.text = title

    region_group = ET.SubElement(svg, "g", {"id": "heatmap-regions"})
    for seed in sorted_seeds:
        verts = regions[seed]
        t = (metrics[seed] - min_val) / val_range
        color = ramp_fn(t)
        points_str = " ".join(
            "%.2f,%.2f" % (tx(vx), ty(vy)) for vx, vy in verts
        )
        ET.SubElement(region_group, "polygon", {
            "points": points_str,
            "fill": color,
            "class": "region",
        })

    if show_values:
        label_group = ET.SubElement(svg, "g", {"id": "value-labels"})
        for seed in sorted_seeds:
            verts = regions[seed]
            cx = sum(v[0] for v in verts) / len(verts)
            cy = sum(v[1] for v in verts) / len(verts)
            val = metrics[seed]
            label = ET.SubElement(label_group, "text", {
                "x": "%.2f" % tx(cx),
                "y": "%.2f" % ty(cy),
                "class": "val-label",
            })
            if metric == "density":
                label.text = "%.1e" % val
            elif metric == "compactness":
                label.text = "%.2f" % val
            else:
                label.text = "%.1f" % val

    if show_points:
        point_group = ET.SubElement(svg, "g", {"id": "seed-points"})
        for pt in data:
            ET.SubElement(point_group, "circle", {
                "cx": "%.2f" % tx(pt[0]),
                "cy": "%.2f" % ty(pt[1]),
                "r": str(point_radius),
                "class": "seed",
            })

    # Color legend bar
    legend_x = width - margin - 20
    legend_y = margin + 30
    legend_h = height - 2 * margin - 60
    legend_w = 15
    n_steps = 50

    legend_group = ET.SubElement(svg, "g", {"id": "legend"})
    for i in range(n_steps):
        t = 1.0 - i / n_steps
        color = ramp_fn(t)
        step_h = legend_h / n_steps
        ET.SubElement(legend_group, "rect", {
            "x": str(legend_x),
            "y": "%.1f" % (legend_y + i * step_h),
            "width": str(legend_w),
            "height": "%.1f" % (step_h + 0.5),
            "fill": color,
            "stroke": "none",
        })

    ET.SubElement(legend_group, "rect", {
        "x": str(legend_x), "y": str(legend_y),
        "width": str(legend_w), "height": str(legend_h),
        "fill": "none", "stroke": "#333", "stroke-width": "0.5",
    })

    metric_labels = {
        "density": "Density", "area": "Area",
        "compactness": "Compactness", "vertices": "Vertices",
    }

    high_label = ET.SubElement(legend_group, "text", {
        "x": str(legend_x + legend_w + 4), "y": str(legend_y + 4),
        "class": "legend-label",
    })
    high_label.text = "High"

    low_label = ET.SubElement(legend_group, "text", {
        "x": str(legend_x + legend_w + 4), "y": str(legend_y + legend_h),
        "class": "legend-label",
    })
    low_label.text = "Low"

    title_label = ET.SubElement(legend_group, "text", {
        "x": str(legend_x + legend_w / 2), "y": str(legend_y - 8),
        "class": "legend-label", "text-anchor": "middle",
    })
    title_label.text = metric_labels.get(metric, metric.capitalize())

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="unicode", xml_declaration=True)


# ── Interactive HTML export ──────────────────────────────────────────

def export_heatmap_html(
    regions,
    data,
    output_path,
    *,
    width=800,
    height=600,
    margin=40,
    color_ramp=DEFAULT_HEATMAP_RAMP,
    metric="density",
    title=None,
):
    """Export an interactive HTML heatmap with tooltips and ramp switching."""
    from vormap_viz import _CoordinateTransform

    sorted_seeds = sorted(regions.keys())

    all_metrics = {}
    for m in ("density", "area", "compactness", "vertices"):
        vals = {}
        for seed in sorted_seeds:
            vals[seed] = _compute_metric(regions[seed], m)
        all_metrics[m] = vals

    transform = _CoordinateTransform(
        regions, data, width=width, height=height, margin=margin,
    )

    cell_data = []
    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        poly_pts = [{"x": round(transform.tx(vx), 2),
                      "y": round(transform.ty(vy), 2)} for vx, vy in verts]
        cell_data.append({
            "id": idx,
            "seed": [round(seed[0], 4), round(seed[1], 4)],
            "seedPx": [round(transform.tx(seed[0]), 2),
                       round(transform.ty(seed[1]), 2)],
            "points": poly_pts,
            "metrics": {m: round(all_metrics[m][seed], 6) for m in all_metrics},
        })

    html_title = title or "Voronoi Density Heatmap"

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>%s</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Helvetica Neue', Arial, sans-serif; background: #1a1a2e; color: #eee; display: flex; flex-direction: column; align-items: center; padding: 20px; }
  h1 { font-size: 20px; margin-bottom: 12px; }
  .controls { display: flex; gap: 16px; margin-bottom: 12px; }
  .controls label { font-size: 13px; }
  .controls select { padding: 4px 8px; border-radius: 4px; border: 1px solid #555; background: #16213e; color: #eee; }
  svg { border: 1px solid #333; border-radius: 6px; background: #0f3460; }
  .tooltip { position: absolute; background: rgba(0,0,0,0.85); color: #fff; padding: 8px 12px; border-radius: 6px; font-size: 12px; pointer-events: none; display: none; z-index: 10; line-height: 1.5; }
</style>
</head>
<body>
<h1>%s</h1>
<div class="controls">
  <label>Metric: <select id="metric-select">
    <option value="density">Density</option>
    <option value="area">Area</option>
    <option value="compactness">Compactness</option>
    <option value="vertices">Vertices</option>
  </select></label>
  <label>Color Ramp: <select id="ramp-select">
    <option value="hot_cold">Hot / Cold</option>
    <option value="viridis">Viridis</option>
    <option value="plasma">Plasma</option>
  </select></label>
</div>
<div style="position:relative">
  <svg id="heatmap" width="%d" height="%d" viewBox="0 0 %d %d"></svg>
  <div class="tooltip" id="tooltip"></div>
</div>
<script>
const cells = %s;
const svg = document.getElementById('heatmap');
const tooltip = document.getElementById('tooltip');
const metricSel = document.getElementById('metric-select');
const rampSel = document.getElementById('ramp-select');

function hotCold(t) {
  let r, g, b;
  if (t < 0.5) { const u = t * 2; r = u; g = u; b = 1; }
  else { const u = (t - 0.5) * 2; r = 1; g = 1 - u; b = 1 - u; }
  return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
}
function viridis(t) {
  const r = Math.max(0, Math.min(1, -0.35 + 1.5*t*t + 0.9*t*t*t));
  const g = Math.max(0, Math.min(1, -0.05 + 0.8*t - 0.2*t*t));
  const b = Math.max(0, Math.min(1, 0.5 + 0.5*Math.sin(Math.PI*(0.7-t))));
  return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
}
function plasma(t) {
  const r = Math.max(0, Math.min(1, 0.05 + 0.95*t));
  const g = Math.max(0, Math.min(1, -0.4 + 1.8*t*t));
  const b = Math.max(0, Math.min(1, 0.55 - 0.6*t + 0.5*Math.sin(Math.PI*t)));
  return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
}
const ramps = { hot_cold: hotCold, viridis: viridis, plasma: plasma };

function render() {
  const metric = metricSel.value;
  const rampFn = ramps[rampSel.value];
  const vals = cells.map(c => c.metrics[metric]);
  let mn = Infinity, mx = -Infinity;
  for (let i = 0; i < vals.length; i++) { if (vals[i] < mn) mn = vals[i]; if (vals[i] > mx) mx = vals[i]; }
  const range = mx > mn ? mx - mn : 1;

  while (svg.firstChild) svg.removeChild(svg.firstChild);

  cells.forEach((cell) => {
    const t = (cell.metrics[metric] - mn) / range;
    const pts = cell.points.map(p => p.x + ',' + p.y).join(' ');
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    poly.setAttribute('points', pts);
    poly.setAttribute('fill', rampFn(t));
    poly.setAttribute('stroke', '#222');
    poly.setAttribute('stroke-width', '0.6');
    poly.style.cursor = 'pointer';
    poly.addEventListener('mouseenter', () => {
      poly.setAttribute('stroke', '#fff');
      poly.setAttribute('stroke-width', '2');
      tooltip.style.display = 'block';
      tooltip.innerHTML = '<b>Seed:</b> (' + cell.seed[0] + ', ' + cell.seed[1] + ')<br>'
        + '<b>Density:</b> ' + cell.metrics.density.toExponential(2) + '<br>'
        + '<b>Area:</b> ' + cell.metrics.area.toFixed(2) + '<br>'
        + '<b>Compactness:</b> ' + cell.metrics.compactness.toFixed(3) + '<br>'
        + '<b>Vertices:</b> ' + cell.metrics.vertices;
    });
    poly.addEventListener('mousemove', (e) => {
      tooltip.style.left = (e.pageX + 12) + 'px';
      tooltip.style.top = (e.pageY - 10) + 'px';
    });
    poly.addEventListener('mouseleave', () => {
      poly.setAttribute('stroke', '#222');
      poly.setAttribute('stroke-width', '0.6');
      tooltip.style.display = 'none';
    });
    svg.appendChild(poly);

    const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    dot.setAttribute('cx', cell.seedPx[0]);
    dot.setAttribute('cy', cell.seedPx[1]);
    dot.setAttribute('r', '2.5');
    dot.setAttribute('fill', '#000');
    dot.setAttribute('stroke', '#fff');
    dot.setAttribute('stroke-width', '0.6');
    svg.appendChild(dot);
  });
}

metricSel.value = '%s';
rampSel.value = '%s';
metricSel.addEventListener('change', render);
rampSel.addEventListener('change', render);
render();
</script>
</body>
</html>"""

    # Escape user-supplied strings to prevent XSS injection.
    # html_title goes into HTML context (<title>, <h1>), metric and
    # color_ramp go into JavaScript string literals inside <script>.
    safe_title = _html_mod.escape(html_title)
    # Allowlist metric and color_ramp to prevent JS injection via %s
    # inside <script>.  Fall back to safe defaults.
    _VALID_METRICS = {"density", "area", "compactness", "vertices"}
    _VALID_RAMPS = {"hot_cold", "viridis", "plasma"}
    safe_metric = metric if metric in _VALID_METRICS else "density"
    safe_ramp = color_ramp if color_ramp in _VALID_RAMPS else "hot_cold"

    html = html % (safe_title, safe_title, width, height, width, height,
                   json.dumps(cell_data), safe_metric, safe_ramp)

    from vormap import validate_output_path
    output_path = validate_output_path(output_path, allow_absolute=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
