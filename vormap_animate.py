"""Voronoi Animation -- animated HTML visualization of diagram evolution.

Generates self-contained HTML files with frame-by-frame playback of
Voronoi diagrams evolving over multiple point snapshots.  Uses the
HTML5 Canvas API for smooth rendering with play/pause, speed control,
scrubbing, and optional trail effects.

Pairs naturally with ``vormap_temporal`` for change analysis and
``vormap_generate`` for creating test sequences.

Usage (module)::

    from vormap_animate import animate, AnimationConfig

    snapshots = [
        [(100, 200), (300, 400), (500, 100)],
        [(110, 210), (300, 400), (500, 100), (700, 300)],
        [(120, 220), (500, 100), (700, 300)],
    ]
    animate(snapshots, "evolution.html")

    # With config
    cfg = AnimationConfig(width=1024, height=768, fps=4, trails=True)
    animate(snapshots, "evolution.html", config=cfg)

Usage (CLI)::

    python vormap_animate.py snap1.txt snap2.txt snap3.txt -o evolution.html
    python vormap_animate.py snap1.txt snap2.txt --fps 8 --trails
    python vormap_animate.py snap1.txt snap2.txt --color-scheme warm
    python vormap_animate.py snap1.txt snap2.txt --interpolate 5

"""

import argparse
import html as _html_mod
import json
import math
import os
import re
import sys

try:
    from vormap_viz import compute_regions, _COLOR_SCHEMES, _hsl_color
    _HAS_VIZ = True
except ImportError:
    _HAS_VIZ = False


# ── Security: sanitize values injected into HTML/CSS/JS context ──────

_CSS_COLOR_RE = re.compile(
    r"^(?:#[0-9a-fA-F]{3,8}|(?:rgb|hsl)a?\([^)]{1,80}\)|[a-zA-Z]{1,30})$"
)


def _sanitize_css_color(value, default="#ffffff"):
    """Validate a CSS color value to prevent injection into HTML templates.

    Only allows hex colors, rgb/hsl functions with limited length, and
    simple named colors.  Returns *default* if the value looks suspicious.
    """
    if not isinstance(value, str) or not _CSS_COLOR_RE.match(value.strip()):
        return default
    return value.strip()


# ── Configuration ────────────────────────────────────────────────────

class AnimationConfig:
    """Configuration for Voronoi animation rendering.

    Parameters
    ----------
    width : int
        Canvas width in pixels (default 900).
    height : int
        Canvas height in pixels (default 700).
    fps : int
        Frames per second for playback (default 2).
    color_scheme : str
        Color scheme name (pastel, warm, cool, earth, mono, rainbow).
    show_points : bool
        Draw seed point markers (default True).
    show_labels : bool
        Show point index labels (default False).
    trails : bool
        Draw fading trails showing seed movement (default False).
    interpolate : int
        Number of interpolated frames between snapshots (default 0).
        Higher values produce smoother transitions.
    title : str or None
        Optional title displayed above the animation.
    background : str
        Canvas background color (default '#ffffff').
    stroke_color : str
        Region boundary color (default '#444444').
    stroke_width : float
        Region boundary width (default 1.2).
    point_radius : float
        Seed point marker radius (default 4.0).
    loop : bool
        Loop animation (default True).
    """

    def __init__(
        self,
        *,
        width=900,
        height=700,
        fps=2,
        color_scheme="pastel",
        show_points=True,
        show_labels=False,
        trails=False,
        interpolate=0,
        title=None,
        background="#ffffff",
        stroke_color="#444444",
        stroke_width=1.2,
        point_radius=4.0,
        loop=True,
    ):
        self.width = width
        self.height = height
        self.fps = max(1, fps)
        self.color_scheme = color_scheme
        self.show_points = show_points
        self.show_labels = show_labels
        self.trails = trails
        self.interpolate = max(0, interpolate)
        self.title = title
        self.background = _sanitize_css_color(background, "#ffffff")
        self.stroke_color = _sanitize_css_color(stroke_color, "#444444")
        self.stroke_width = stroke_width
        self.point_radius = point_radius
        self.loop = loop


# ── Core helpers ─────────────────────────────────────────────────────

def _compute_bounds(all_snapshots):
    """Find bounding box across all snapshots."""
    all_x = []
    all_y = []
    for snap in all_snapshots:
        for x, y in snap:
            all_x.append(x)
            all_y.append(y)
    if not all_x:
        return 0, 0, 100, 100
    margin_frac = 0.05
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    rx = max(max_x - min_x, 1e-6) * margin_frac
    ry = max(max_y - min_y, 1e-6) * margin_frac
    return min_x - rx, min_y - ry, max_x + rx, max_y + ry


def _lerp_point(p1, p2, t):
    """Linear interpolation between two points."""
    return (p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t)


def _match_points(prev, curr, radius=None):
    """Match points between frames using nearest-neighbor.

    Returns list of (prev_idx or None, curr_idx) pairs.
    """
    if radius is None:
        # Auto radius: 20% of average extent
        all_pts = prev + curr
        if len(all_pts) < 2:
            return [(None, i) for i in range(len(curr))]
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        extent = max(max(xs) - min(xs), max(ys) - min(ys), 1e-6)
        radius = extent * 0.2

    matched_prev = set()
    matched_curr = set()
    pairs = []

    # Greedy nearest match
    distances = []
    for ci, cp in enumerate(curr):
        for pi, pp in enumerate(prev):
            d = math.hypot(cp[0] - pp[0], cp[1] - pp[1])
            if d <= radius:
                distances.append((d, pi, ci))
    distances.sort()

    for d, pi, ci in distances:
        if pi not in matched_prev and ci not in matched_curr:
            pairs.append((pi, ci))
            matched_prev.add(pi)
            matched_curr.add(ci)

    # Unmatched current points (births)
    for ci in range(len(curr)):
        if ci not in matched_curr:
            pairs.append((None, ci))

    return pairs


def _interpolate_snapshots(snapshots, n_between):
    """Insert interpolated frames between each pair of snapshots."""
    if n_between <= 0 or len(snapshots) < 2:
        return list(snapshots)

    result = [snapshots[0]]
    for i in range(len(snapshots) - 1):
        prev, curr = snapshots[i], snapshots[i + 1]
        matches = _match_points(prev, curr)

        for step in range(1, n_between + 1):
            t = step / (n_between + 1)
            interp = []
            for prev_idx, curr_idx in matches:
                cp = curr[curr_idx]
                if prev_idx is not None:
                    pp = prev[prev_idx]
                    interp.append(_lerp_point(pp, cp, t))
                else:
                    # Birth: fade in (just appear at position)
                    interp.append(cp)
            result.append(interp)
        result.append(curr)

    return result


def _build_frame_data(snapshots, config):
    """Build serializable frame data for all snapshots."""
    expanded = _interpolate_snapshots(snapshots, config.interpolate)
    bounds = _compute_bounds(expanded)
    min_x, min_y, max_x, max_y = bounds

    range_x = max(max_x - min_x, 1e-6)
    range_y = max(max_y - min_y, 1e-6)
    margin = 40

    draw_w = config.width - 2 * margin
    draw_h = config.height - 2 * margin
    scale = min(draw_w / range_x, draw_h / range_y)
    off_x = margin + (draw_w - range_x * scale) / 2
    off_y = margin + (draw_h - range_y * scale) / 2

    def to_canvas(pt):
        return (
            off_x + (pt[0] - min_x) * scale,
            off_y + (max_y - pt[1]) * scale,  # flip Y
        )

    # Generate colors for max number of points
    max_pts = max(len(s) for s in expanded) if expanded else 0
    scheme = _COLOR_SCHEMES.get(config.color_scheme, _COLOR_SCHEMES["pastel"]) if _HAS_VIZ else lambda i, n: _hsl_color_fallback(i, n)

    frames = []
    for snap in expanded:
        if not _HAS_VIZ or len(snap) < 3:
            # Without viz, just render points (no region polygons)
            regions_data = []
        else:
            try:
                regions = compute_regions(snap)
                regions_data = []
                for seed in snap:
                    verts = regions.get(seed, [])
                    canvas_verts = [to_canvas(v) for v in verts]
                    regions_data.append(canvas_verts)
            except Exception:
                regions_data = [[] for _ in snap]

        canvas_pts = [to_canvas(p) for p in snap]
        n = len(snap)
        colors = [scheme(i, n) for i in range(n)]

        frames.append({
            "points": [[round(x, 1), round(y, 1)] for x, y in canvas_pts],
            "regions": [[[round(x, 1), round(y, 1)] for x, y in r] for r in regions_data],
            "colors": colors,
        })

    return frames


def _hsl_color_fallback(i, n):
    """Simple HSL color without viz module."""
    import colorsys
    h = i / max(n, 1)
    r, g, b = colorsys.hls_to_rgb(h % 1.0, 0.82, 0.65)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def _load_points(filepath):
    """Load points from a text file (x y per line, or x,y).

    Supports space-separated and comma-separated formats.
    """
    points = []
    with open(filepath, 'r', encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.replace(',', ' ').split()
            if len(parts) >= 2:
                try:
                    x, y = float(parts[0]), float(parts[1])
                    points.append((x, y))
                except ValueError:
                    continue
    if not points:
        raise ValueError("No valid points found in '{}'".format(filepath))
    return points


# ── HTML generation ──────────────────────────────────────────────────

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; display: flex; flex-direction: column;
         align-items: center; min-height: 100vh; padding: 20px; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 12px; color: #a0c4ff; }}
  .container {{ background: #16213e; border-radius: 12px; padding: 20px;
               box-shadow: 0 8px 32px rgba(0,0,0,0.3); max-width: {cwidth}px; }}
  canvas {{ border-radius: 8px; display: block; cursor: pointer; }}
  .controls {{ display: flex; align-items: center; gap: 12px; margin-top: 14px;
              flex-wrap: wrap; justify-content: center; }}
  button {{ background: #0f3460; border: 1px solid #533483; color: #e0e0e0;
           padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 0.9rem;
           transition: background 0.2s; }}
  button:hover {{ background: #533483; }}
  button.active {{ background: #533483; border-color: #a0c4ff; }}
  .slider-group {{ display: flex; align-items: center; gap: 6px; }}
  input[type=range] {{ width: 140px; accent-color: #533483; }}
  .info {{ font-size: 0.85rem; color: #8899aa; margin-top: 8px; text-align: center; }}
  .frame-counter {{ font-variant-numeric: tabular-nums; min-width: 70px; text-align: center; }}
</style>
</head>
<body>
{title_html}
<div class="container">
  <canvas id="cv" width="{cwidth}" height="{cheight}"></canvas>
  <div class="controls">
    <button id="btnPlay" class="active">&#9654; Play</button>
    <button id="btnStep">&gt;|</button>
    <div class="slider-group">
      <label>Speed</label>
      <input type="range" id="speedSlider" min="1" max="20" value="{fps}">
    </div>
    <div class="slider-group">
      <label>Frame</label>
      <input type="range" id="frameSlider" min="0" max="{maxframe}" value="0">
    </div>
    <span class="frame-counter" id="counter">1 / {nframes}</span>
  </div>
  <div class="info">{info_text}</div>
</div>
<script>
(function() {{
const FRAMES = {frames_json};
const CFG = {{
  bg: "{bg}", stroke: "{stroke}", strokeW: {strokeW},
  ptR: {ptR}, showPts: {showPts}, showLabels: {showLabels},
  trails: {trails}, loop: {loop}
}};
const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const btnPlay = document.getElementById('btnPlay');
const btnStep = document.getElementById('btnStep');
const speedSlider = document.getElementById('speedSlider');
const frameSlider = document.getElementById('frameSlider');
const counter = document.getElementById('counter');

let frame = 0, playing = true, fps = {fps}, timer = null;
let trailPts = [];  // previous frame points for trail rendering

function drawFrame(idx) {{
  const f = FRAMES[idx];
  ctx.fillStyle = CFG.bg;
  ctx.fillRect(0, 0, cv.width, cv.height);

  // Draw trails
  if (CFG.trails && trailPts.length > 0 && f.points.length > 0) {{
    ctx.globalAlpha = 0.15;
    ctx.strokeStyle = '#888';
    ctx.lineWidth = 1;
    for (let i = 0; i < Math.min(trailPts.length, f.points.length); i++) {{
      ctx.beginPath();
      ctx.moveTo(trailPts[i][0], trailPts[i][1]);
      ctx.lineTo(f.points[i][0], f.points[i][1]);
      ctx.stroke();
    }}
    ctx.globalAlpha = 1.0;
  }}

  // Draw regions
  for (let i = 0; i < f.regions.length; i++) {{
    const r = f.regions[i];
    if (r.length < 3) continue;
    ctx.beginPath();
    ctx.moveTo(r[0][0], r[0][1]);
    for (let j = 1; j < r.length; j++) ctx.lineTo(r[j][0], r[j][1]);
    ctx.closePath();
    ctx.fillStyle = f.colors[i] || '#ccc';
    ctx.globalAlpha = 0.55;
    ctx.fill();
    ctx.globalAlpha = 1.0;
    ctx.strokeStyle = CFG.stroke;
    ctx.lineWidth = CFG.strokeW;
    ctx.stroke();
  }}

  // Draw points
  if (CFG.showPts) {{
    for (let i = 0; i < f.points.length; i++) {{
      ctx.beginPath();
      ctx.arc(f.points[i][0], f.points[i][1], CFG.ptR, 0, Math.PI * 2);
      ctx.fillStyle = '#222';
      ctx.fill();
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }}
  }}

  // Draw labels
  if (CFG.showLabels) {{
    ctx.font = '11px monospace';
    ctx.fillStyle = '#333';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    for (let i = 0; i < f.points.length; i++) {{
      ctx.fillText(i.toString(), f.points[i][0] + CFG.ptR + 2, f.points[i][1] - 2);
    }}
  }}

  trailPts = f.points.slice();
  counter.textContent = (idx + 1) + ' / ' + FRAMES.length;
  frameSlider.value = idx;
}}

function tick() {{
  drawFrame(frame);
  frame++;
  if (frame >= FRAMES.length) {{
    if (CFG.loop) {{ frame = 0; trailPts = []; }}
    else {{ frame = FRAMES.length - 1; togglePlay(false); }}
  }}
}}

function togglePlay(state) {{
  playing = state;
  btnPlay.textContent = playing ? '\u23f8 Pause' : '\u25b6 Play';
  btnPlay.classList.toggle('active', playing);
  clearInterval(timer);
  if (playing) timer = setInterval(tick, 1000 / fps);
}}

btnPlay.addEventListener('click', () => togglePlay(!playing));
btnStep.addEventListener('click', () => {{
  togglePlay(false);
  drawFrame(frame);
  frame = (frame + 1) % FRAMES.length;
}});

speedSlider.addEventListener('input', () => {{
  fps = parseInt(speedSlider.value);
  if (playing) {{ clearInterval(timer); timer = setInterval(tick, 1000 / fps); }}
}});

frameSlider.addEventListener('input', () => {{
  frame = parseInt(frameSlider.value);
  trailPts = [];
  drawFrame(frame);
}});

cv.addEventListener('click', () => togglePlay(!playing));

// Start
timer = setInterval(tick, 1000 / fps);
}})();
</script>
</body>
</html>"""


# ── Public API ───────────────────────────────────────────────────────

def animate(snapshots, output_path, *, config=None):
    """Generate an animated HTML visualization of Voronoi diagram evolution.

    Parameters
    ----------
    snapshots : list of list of (float, float)
        Sequence of point sets representing the diagram at each time step.
        Each snapshot is a list of ``(x, y)`` seed coordinates.
    output_path : str
        Path for the output HTML file.
    config : AnimationConfig or None
        Rendering configuration (uses defaults if None).

    Returns
    -------
    dict
        Summary with keys: ``frames``, ``snapshots``, ``output_path``,
        ``interpolated_frames``.

    Raises
    ------
    ValueError
        If fewer than 2 snapshots are provided.
    """
    if len(snapshots) < 2:
        raise ValueError("At least 2 snapshots are required for animation")

    if config is None:
        config = AnimationConfig()

    frames = _build_frame_data(snapshots, config)

    title = config.title or "Voronoi Animation"
    title_html = '<h1>{}</h1>'.format(_html_mod.escape(title)) if config.title else ''

    n_orig = len(snapshots)
    n_total = len(frames)
    info_parts = [
        "{} original snapshots".format(n_orig),
        "{} total frames".format(n_total),
    ]
    if config.interpolate > 0:
        info_parts.append("{} interpolated per transition".format(config.interpolate))
    info_text = " · ".join(info_parts)

    html = _HTML_TEMPLATE.format(
        title=_html_mod.escape(title),
        title_html=title_html,
        cwidth=config.width,
        cheight=config.height,
        fps=config.fps,
        maxframe=max(len(frames) - 1, 0),
        nframes=len(frames),
        frames_json=json.dumps(frames, separators=(',', ':')),
        bg=config.background,
        stroke=config.stroke_color,
        strokeW=config.stroke_width,
        ptR=config.point_radius,
        showPts='true' if config.show_points else 'false',
        showLabels='true' if config.show_labels else 'false',
        trails='true' if config.trails else 'false',
        loop='true' if config.loop else 'false',
        info_text=info_text,
    )

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as fh:
        fh.write(html)

    return {
        "frames": n_total,
        "snapshots": n_orig,
        "output_path": os.path.abspath(output_path),
        "interpolated_frames": n_total - n_orig if config.interpolate > 0 else 0,
    }


def animate_from_files(file_paths, output_path, *, config=None):
    """Load snapshots from text files and generate animation.

    Each file should contain one point per line as ``x y`` or ``x,y``.

    Parameters
    ----------
    file_paths : list of str
        Paths to snapshot files.
    output_path : str
        Path for the output HTML file.
    config : AnimationConfig or None
        Rendering configuration.

    Returns
    -------
    dict
        Same as :func:`animate`.
    """
    snapshots = []
    for fp in file_paths:
        pts = _load_points(fp)
        snapshots.append(pts)
    return animate(snapshots, output_path, config=config)


# ── CLI ──────────────────────────────────────────────────────────────

def _build_parser():
    p = argparse.ArgumentParser(
        description="Generate animated HTML visualization of Voronoi diagram evolution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_animate.py snap1.txt snap2.txt snap3.txt -o anim.html
  python vormap_animate.py snap1.txt snap2.txt --fps 8 --trails
  python vormap_animate.py snap1.txt snap2.txt --interpolate 5 --color-scheme warm
  python vormap_animate.py snap1.txt snap2.txt --width 1200 --height 800 --title "Growth"
""",
    )
    p.add_argument("snapshots", nargs="+", help="Snapshot files (one point per line: x y)")
    p.add_argument("-o", "--output", default="voronoi_animation.html", help="Output HTML file")
    p.add_argument("--width", type=int, default=900, help="Canvas width (default 900)")
    p.add_argument("--height", type=int, default=700, help="Canvas height (default 700)")
    p.add_argument("--fps", type=int, default=2, help="Playback speed (default 2)")
    p.add_argument("--color-scheme", default="pastel",
                   choices=["pastel", "warm", "cool", "earth", "mono", "rainbow"],
                   help="Color scheme (default pastel)")
    p.add_argument("--trails", action="store_true", help="Show movement trails")
    p.add_argument("--interpolate", type=int, default=0,
                   help="Interpolated frames between snapshots (default 0)")
    p.add_argument("--title", help="Animation title")
    p.add_argument("--labels", action="store_true", help="Show point index labels")
    p.add_argument("--no-points", action="store_true", help="Hide seed point markers")
    p.add_argument("--no-loop", action="store_true", help="Don't loop animation")
    p.add_argument("--background", default="#ffffff", help="Background color")
    return p


def main(argv=None):
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if len(args.snapshots) < 2:
        parser.error("At least 2 snapshot files are required")

    config = AnimationConfig(
        width=args.width,
        height=args.height,
        fps=args.fps,
        color_scheme=args.color_scheme,
        show_points=not args.no_points,
        show_labels=args.labels,
        trails=args.trails,
        interpolate=args.interpolate,
        title=args.title,
        background=args.background,
        loop=not args.no_loop,
    )

    result = animate_from_files(args.snapshots, args.output, config=config)
    print("Animation saved: {}".format(result["output_path"]))
    print("  {} snapshots -> {} frames".format(result["snapshots"], result["frames"]))
    if result["interpolated_frames"]:
        print("  {} interpolated frames".format(result["interpolated_frames"]))


if __name__ == "__main__":
    main()
