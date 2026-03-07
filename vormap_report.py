"""Generate a self-contained HTML analysis report for a Voronoi diagram.

Combines region statistics, density heatmap, neighbourhood graph metrics,
and area distribution into a single, attractive HTML document with
embedded SVG visualizations. Supports dark mode automatically.

Example::

    from vormap_report import VoronoiReport

    report = VoronoiReport(seed_points, voronoi_regions, bounds)
    report.generate("analysis_report.html")

CLI usage::

    voronoimap datauni5.txt 5 --report analysis.html
    voronoimap datauni5.txt 5 --report analysis.html --report-title "My Study"
"""

import math
import os
import datetime


def _escape_html(text):
    """Escape HTML special characters."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _format_number(value, decimals=2):
    """Format a number with specified decimal places."""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return f"{value:.{decimals}f}"
    return str(value)


def _polygon_area(vertices):
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


def _polygon_perimeter(vertices):
    """Compute polygon perimeter."""
    n = len(vertices)
    if n < 2:
        return 0.0
    perimeter = 0.0
    for i in range(n):
        j = (i + 1) % n
        dx = vertices[j][0] - vertices[i][0]
        dy = vertices[j][1] - vertices[i][1]
        perimeter += math.sqrt(dx * dx + dy * dy)
    return perimeter


def _polygon_centroid(vertices):
    """Compute polygon centroid."""
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    if n == 1:
        return vertices[0]
    cx, cy, a_sum = 0.0, 0.0, 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = vertices[i][0] * vertices[j][1] - vertices[j][0] * vertices[i][1]
        cx += (vertices[i][0] + vertices[j][0]) * cross
        cy += (vertices[i][1] + vertices[j][1]) * cross
        a_sum += cross
    if abs(a_sum) < 1e-12:
        xs = sum(v[0] for v in vertices) / n
        ys = sum(v[1] for v in vertices) / n
        return (xs, ys)
    a_sum *= 3.0
    return (cx / a_sum, cy / a_sum)


def _mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values):
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _color_lerp(t, ramp="viridis"):
    """Interpolate a color ramp at position t in [0, 1]."""
    t = max(0.0, min(1.0, t))
    ramps = {
        "viridis": [
            (68, 1, 84), (72, 35, 116), (64, 67, 135), (52, 94, 141),
            (33, 145, 140), (94, 201, 98), (253, 231, 37),
        ],
        "hot_cold": [
            (49, 54, 149), (69, 117, 180), (116, 173, 209),
            (255, 255, 191), (253, 174, 97), (244, 109, 67), (215, 48, 39),
        ],
    }
    colors = ramps.get(ramp, ramps["viridis"])
    n = len(colors) - 1
    idx = t * n
    i = min(int(idx), n - 1)
    f = idx - i
    r = int(colors[i][0] + f * (colors[i + 1][0] - colors[i][0]))
    g = int(colors[i][1] + f * (colors[i + 1][1] - colors[i][1]))
    b = int(colors[i][2] + f * (colors[i + 1][2] - colors[i][2]))
    return f"rgb({r},{g},{b})"


class VoronoiReport:
    """Generates a comprehensive HTML analysis report for Voronoi diagrams.

    Parameters
    ----------
    seed_points : list of (float, float) or list of dict with 'x','y' keys
        The Voronoi seed/generator points.
    regions : list of list of (float, float)
        Voronoi region polygons (lists of vertex coordinates).
    bounds : tuple of (south, north, west, east)
        Search space boundaries.
    title : str
        Report title.
    """

    def __init__(self, seed_points, regions, bounds,
                 title="Voronoi Analysis Report"):
        self.seeds = []
        for p in seed_points:
            if isinstance(p, dict):
                self.seeds.append((p['x'], p['y']))
            else:
                self.seeds.append((p[0], p[1]))
        self.regions = list(regions)
        self.bounds = bounds
        self.title = title
        self._stats = None

    def _compute_stats(self):
        if self._stats is not None:
            return self._stats
        stats = []
        for i, region in enumerate(self.regions):
            area = _polygon_area(region)
            perim = _polygon_perimeter(region)
            cx, cy = _polygon_centroid(region)
            nverts = len(region)
            compactness = (
                (4 * math.pi * area / (perim * perim)) if perim > 0 else 0.0
            )
            stats.append({
                "index": i, "area": area, "perimeter": perim,
                "centroid_x": cx, "centroid_y": cy,
                "vertices": nverts, "compactness": compactness,
            })
        self._stats = stats
        return stats

    def _compute_adjacency(self):
        adj = {i: set() for i in range(len(self.regions))}
        vertex_map = {}
        for i, region in enumerate(self.regions):
            for v in region:
                key = (round(v[0], 4), round(v[1], 4))
                if key not in vertex_map:
                    vertex_map[key] = set()
                vertex_map[key].add(i)
        for region_ids in vertex_map.values():
            rlist = list(region_ids)
            for a in range(len(rlist)):
                for b in range(a + 1, len(rlist)):
                    adj[rlist[a]].add(rlist[b])
                    adj[rlist[b]].add(rlist[a])
        return adj

    def _summary_stats(self):
        stats = self._compute_stats()
        areas = [s["area"] for s in stats]
        perimeters = [s["perimeter"] for s in stats]
        compactness = [s["compactness"] for s in stats]
        verts = [s["vertices"] for s in stats]
        s, n, w, e = self.bounds
        total_area = (n - s) * (e - w)
        return {
            "num_regions": len(stats),
            "total_bounds_area": total_area,
            "area_mean": _mean(areas), "area_std": _std(areas),
            "area_median": _median(areas),
            "area_min": min(areas) if areas else 0,
            "area_max": max(areas) if areas else 0,
            "perim_mean": _mean(perimeters), "perim_std": _std(perimeters),
            "compact_mean": _mean(compactness),
            "verts_mean": _mean(verts),
            "verts_min": min(verts) if verts else 0,
            "verts_max": max(verts) if verts else 0,
        }

    def _render_svg_diagram(self, width=600, height=450):
        s, n, w, e = self.bounds
        bw, bh = e - w, n - s
        if bw == 0 or bh == 0:
            return "<p>Cannot render: zero-size bounds</p>"
        stats = self._compute_stats()
        areas = [st["area"] for st in stats]
        values = [(1.0 / a if a > 0 else 0) for a in areas]
        vmin = min(values) if values else 0
        vmax = max(values) if values else 1
        vrange = vmax - vmin if vmax > vmin else 1.0

        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'style="max-width:100%;height:auto;border:1px solid #ddd;'
            f'border-radius:8px;">'
        ]

        from vormap_geometry import SVGCoordinateTransform
        ct = SVGCoordinateTransform(
            (w, e), (s, n), width, height, margin=0, mode="stretch")
        tx = ct.tx
        ty = ct.ty

        for i, region in enumerate(self.regions):
            if len(region) < 3:
                continue
            t = (values[i] - vmin) / vrange if vrange > 0 else 0.5
            color = _color_lerp(t)
            pts = " ".join(f"{tx(v[0]):.1f},{ty(v[1]):.1f}" for v in region)
            lines.append(
                f'<polygon points="{pts}" fill="{color}" '
                f'stroke="#fff" stroke-width="0.5" opacity="0.85">'
                f'<title>Region {i}: area={_format_number(areas[i])}'
                f'</title></polygon>'
            )
        for i, (sx_pt, sy_pt) in enumerate(self.seeds):
            lines.append(
                f'<circle cx="{tx(sx_pt):.1f}" cy="{ty(sy_pt):.1f}" '
                f'r="3" fill="#222" stroke="#fff" stroke-width="0.5"/>'
            )
        lines.append('</svg>')
        return "\n".join(lines)

    def _render_area_histogram(self, width=500, height=200, bins=15):
        stats = self._compute_stats()
        areas = sorted(s["area"] for s in stats)
        if not areas:
            return "<p>No data</p>"
        lo, hi = areas[0], areas[-1]
        if lo == hi:
            hi = lo + 1
        bw = (hi - lo) / bins
        counts = [0] * bins
        for a in areas:
            idx = min(int((a - lo) / bw), bins - 1)
            counts[idx] += 1
        mc = max(counts) if counts else 1
        pl, pb = 50, 30
        cw, ch = width - pl - 10, height - pb - 10
        bar_w = cw / bins
        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'style="max-width:100%;height:auto;">'
        ]
        for i, c in enumerate(counts):
            bh = (c / mc * ch) if mc > 0 else 0
            x = pl + i * bar_w
            y = height - pb - bh
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w-1:.1f}" '
                f'height="{bh:.1f}" fill="#4C72B0" opacity="0.8">'
                f'<title>{_format_number(lo+i*bw)} - '
                f'{_format_number(lo+(i+1)*bw)}: {c}</title></rect>'
            )
        lines.append(
            f'<line x1="{pl}" y1="{height-pb}" x2="{width-10}" '
            f'y2="{height-pb}" stroke="#333" stroke-width="1"/>'
        )
        for i in range(0, bins + 1, max(1, bins // 5)):
            x = pl + i * bar_w
            lines.append(
                f'<text x="{x:.0f}" y="{height-10}" font-size="9" '
                f'text-anchor="middle" fill="#555">'
                f'{_format_number(lo+i*bw, 0)}</text>'
            )
        lines.append('</svg>')
        return "\n".join(lines)

    def _render_degree_chart(self, width=400, height=200):
        adj = self._compute_adjacency()
        degrees = [len(nb) for nb in adj.values()]
        if not degrees:
            return "<p>No data</p>"
        md = max(degrees)
        dist = [0] * (md + 1)
        for d in degrees:
            dist[d] += 1
        mc = max(dist) if dist else 1
        pl, pb = 40, 30
        cw, ch = width - pl - 10, height - pb - 10
        bar_w = cw / (md + 1) if md > 0 else cw
        lines = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" '
            f'style="max-width:100%;height:auto;">'
        ]
        for i, c in enumerate(dist):
            bh = (c / mc * ch) if mc > 0 else 0
            x = pl + i * bar_w
            y = height - pb - bh
            lines.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" '
                f'width="{max(bar_w-1,1):.1f}" height="{bh:.1f}" '
                f'fill="#E07B39" opacity="0.85">'
                f'<title>Degree {i}: {c}</title></rect>'
            )
        lines.append(
            f'<line x1="{pl}" y1="{height-pb}" x2="{width-10}" '
            f'y2="{height-pb}" stroke="#333" stroke-width="1"/>'
        )
        lines.append(
            f'<text x="{width//2}" y="{height-5}" font-size="10" '
            f'text-anchor="middle" fill="#555">Degree</text>'
        )
        lines.append('</svg>')
        return "\n".join(lines)

    def generate(self, output_path, allow_absolute=False):
        """Generate the HTML report and write to *output_path*.

        Returns the resolved output path.
        """
        from vormap import validate_output_path
        resolved = validate_output_path(
            output_path, allow_absolute=allow_absolute
        )

        stats = self._compute_stats()
        summary = self._summary_stats()
        adj = self._compute_adjacency()
        degrees = [len(nb) for nb in adj.values()]
        avg_deg = _mean(degrees) if degrees else 0
        max_deg = max(degrees) if degrees else 0

        diagram_svg = self._render_svg_diagram()
        histogram_svg = self._render_area_histogram()
        degree_svg = self._render_degree_chart()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        sorted_stats = sorted(stats, key=lambda s: s["area"], reverse=True)
        table_rows = []
        for s in sorted_stats[:50]:
            table_rows.append(
                f'<tr><td>{s["index"]}</td>'
                f'<td>{_format_number(s["area"])}</td>'
                f'<td>{_format_number(s["perimeter"])}</td>'
                f'<td>{_format_number(s["compactness"], 3)}</td>'
                f'<td>{s["vertices"]}</td>'
                f'<td>({_format_number(s["centroid_x"])},'
                f' {_format_number(s["centroid_y"])})</td></tr>'
            )
        if len(stats) > 50:
            table_rows.append(
                f'<tr><td colspan="6" style="text-align:center;color:#888;">'
                f'... and {len(stats)-50} more regions</td></tr>'
            )

        title_esc = _escape_html(self.title)
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc}</title>
<style>
:root{{--bg:#f8f9fa;--card:#fff;--text:#212529;--muted:#6c757d;--border:#dee2e6;--accent:#4C72B0}}
@media(prefers-color-scheme:dark){{:root{{--bg:#1a1a2e;--card:#16213e;--text:#e0e0e0;--muted:#9a9a9a;--border:#333;--accent:#7ba4d9}}}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);line-height:1.6;padding:20px}}
.c{{max-width:1100px;margin:0 auto}}
h1{{font-size:1.8rem;margin-bottom:4px}}
.sub{{color:var(--muted);font-size:.9rem;margin-bottom:24px}}
.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:24px}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card h2{{font-size:1.1rem;margin-bottom:12px;color:var(--accent)}}
.sg{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}
.sl{{font-size:.75rem;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.sv{{font-size:1.2rem;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
th,td{{padding:8px 10px;text-align:left;border-bottom:1px solid var(--border)}}
th{{font-weight:600;color:var(--muted);font-size:.75rem;text-transform:uppercase}}
tr:hover{{background:rgba(76,114,176,.05)}}
.fw{{grid-column:1/-1}}
.sc{{text-align:center;overflow-x:auto}}
footer{{text-align:center;color:var(--muted);font-size:.8rem;margin-top:32px;padding-top:16px;border-top:1px solid var(--border)}}
</style>
</head>
<body>
<div class="c">
<h1>{title_esc}</h1>
<p class="sub">Generated {timestamp} &middot; {summary['num_regions']} regions &middot; Bounds ({_format_number(self.bounds[2])}, {_format_number(self.bounds[0])}) &rarr; ({_format_number(self.bounds[3])}, {_format_number(self.bounds[1])})</p>
<div class="g">
<div class="card"><h2>Summary</h2><div class="sg">
<div><div class="sl">Regions</div><div class="sv">{summary['num_regions']}</div></div>
<div><div class="sl">Bounds Area</div><div class="sv">{_format_number(summary['total_bounds_area'])}</div></div>
<div><div class="sl">Mean Area</div><div class="sv">{_format_number(summary['area_mean'])}</div></div>
<div><div class="sl">Std Dev</div><div class="sv">{_format_number(summary['area_std'])}</div></div>
<div><div class="sl">Median Area</div><div class="sv">{_format_number(summary['area_median'])}</div></div>
<div><div class="sl">Area Range</div><div class="sv">{_format_number(summary['area_min'])} &ndash; {_format_number(summary['area_max'])}</div></div>
</div></div>
<div class="card"><h2>Graph Metrics</h2><div class="sg">
<div><div class="sl">Avg Degree</div><div class="sv">{_format_number(avg_deg)}</div></div>
<div><div class="sl">Max Degree</div><div class="sv">{max_deg}</div></div>
<div><div class="sl">Edges</div><div class="sv">{sum(degrees)//2}</div></div>
<div><div class="sl">Avg Compactness</div><div class="sv">{_format_number(summary['compact_mean'],3)}</div></div>
<div><div class="sl">Mean Perimeter</div><div class="sv">{_format_number(summary['perim_mean'])}</div></div>
<div><div class="sl">Vertices Range</div><div class="sv">{summary['verts_min']} &ndash; {summary['verts_max']}</div></div>
</div></div>
</div>
<div class="g"><div class="card fw"><h2>Density Heatmap</h2><div class="sc">{diagram_svg}</div></div></div>
<div class="g">
<div class="card"><h2>Area Distribution</h2><div class="sc">{histogram_svg}</div></div>
<div class="card"><h2>Degree Distribution</h2><div class="sc">{degree_svg}</div></div>
</div>
<div class="g"><div class="card fw"><h2>Region Details</h2><div style="overflow-x:auto"><table>
<thead><tr><th>Region</th><th>Area</th><th>Perimeter</th><th>Compactness</th><th>Vertices</th><th>Centroid</th></tr></thead>
<tbody>{"".join(table_rows)}</tbody></table></div></div></div>
<footer>Generated by <strong>VoronoiMap</strong> &middot; <a href="https://github.com/sauravbhattacharya001/VoronoiMap" style="color:var(--accent)">GitHub</a></footer>
</div></body></html>"""

        out_dir = os.path.dirname(resolved)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(html)
        return resolved


def generate_report(seed_points, regions, bounds, output_path,
                    title="Voronoi Analysis Report", allow_absolute=False):
    """Convenience function to generate a report in one call."""
    report = VoronoiReport(seed_points, regions, bounds, title=title)
    return report.generate(output_path, allow_absolute=allow_absolute)
