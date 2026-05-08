"""Voronoi Morph -- smooth animated interpolation between point configurations.

Generates self-contained HTML files that animate a Voronoi diagram morphing
from one point set to another.  Uses greedy nearest-neighbour matching to
pair source → target points, with configurable easing functions and an
autonomous discovery mode that generates interesting target layouts.

Pairs naturally with ``vormap_animate`` for frame-by-frame playback and
``vormap_generate`` for creating test point sets.

Usage (module)::

    from vormap_morph import morph, MorphConfig

    src = [(100, 200), (300, 400), (500, 100)]
    tgt = [(150, 250), (350, 350), (450, 150), (700, 300)]
    morph(src, tgt, "morph.html")

    cfg = MorphConfig(easing="elastic", duration_seconds=5)
    morph(src, tgt, "morph.html", config=cfg)

    # Autonomous mode -- auto-discover interesting target
    from vormap_morph import auto_morph
    auto_morph(src, "morph.html")

Usage (CLI)::

    python vormap_morph.py source.txt target.txt -o morph.html
    python vormap_morph.py source.txt target.txt --easing elastic --fps 30
    python vormap_morph.py source.txt --auto -o morph.html
    python vormap_morph.py --demo -o morph.html
"""

from __future__ import annotations

import argparse
import html as _html
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from vormap_utils import euclidean as _dist

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EASING_FUNCTIONS = ("linear", "ease_in_out", "elastic", "bounce", "cubic_bezier")

COLOR_SCHEMES = {
    "cool": ["#264653", "#2a9d8f", "#e9c46a", "#f4a261", "#e76f51",
             "#457b9d", "#1d3557", "#a8dadc", "#48cae4", "#023e8a"],
    "warm": ["#d62828", "#f77f00", "#fcbf49", "#eae2b7", "#9b2226",
             "#ae2012", "#bb3e03", "#ca6702", "#ee9b00", "#e9d8a6"],
    "pastel": ["#cdb4db", "#ffc8dd", "#ffafcc", "#bde0fe", "#a2d2ff",
               "#b5e48c", "#d9ed92", "#ffd6ff", "#e7c6ff", "#c8b6ff"],
    "neon": ["#ff006e", "#fb5607", "#ffbe0b", "#3a86ff", "#8338ec",
             "#06d6a0", "#118ab2", "#ef476f", "#ffd166", "#073b4c"],
    "earth": ["#606c38", "#283618", "#fefae0", "#dda15e", "#bc6c25",
              "#588157", "#3a5a40", "#a3b18a", "#dad7cd", "#344e41"],
}

DEFAULT_SCHEME = "cool"


@dataclass
class MorphConfig:
    """Configuration for morph animation."""

    width: int = 800
    height: int = 600
    fps: int = 30
    duration_seconds: float = 3.0
    easing: str = "ease_in_out"
    color_scheme: str = DEFAULT_SCHEME
    show_trails: bool = False
    loop: bool = True

    @property
    def num_frames(self) -> int:
        return max(2, int(self.fps * self.duration_seconds))


# ---------------------------------------------------------------------------
# Easing helpers (pure Python, also emitted as JS)
# ---------------------------------------------------------------------------

def _ease_linear(t: float) -> float:
    return t


def _ease_in_out(t: float) -> float:
    return 3 * t * t - 2 * t * t * t


def _ease_elastic(t: float) -> float:
    if t == 0 or t == 1:
        return t
    p = 0.3
    s = p / 4
    return math.pow(2, -10 * t) * math.sin((t - s) * (2 * math.pi) / p) + 1


def _ease_bounce(t: float) -> float:
    if t < 1 / 2.75:
        return 7.5625 * t * t
    elif t < 2 / 2.75:
        t -= 1.5 / 2.75
        return 7.5625 * t * t + 0.75
    elif t < 2.5 / 2.75:
        t -= 2.25 / 2.75
        return 7.5625 * t * t + 0.9375
    else:
        t -= 2.625 / 2.75
        return 7.5625 * t * t + 0.984375


def _ease_cubic_bezier(t: float) -> float:
    # Default cubic-bezier(0.42, 0, 0.58, 1) -- standard ease-in-out curve
    # Approximate with simple formula
    return t * t * (3 - 2 * t)


EASING_MAP = {
    "linear": _ease_linear,
    "ease_in_out": _ease_in_out,
    "elastic": _ease_elastic,
    "bounce": _ease_bounce,
    "cubic_bezier": _ease_cubic_bezier,
}


# ---------------------------------------------------------------------------
# Point matching (greedy nearest-neighbour)
# ---------------------------------------------------------------------------

Point = Tuple[float, float]


def match_points(
    source: Sequence[Point], target: Sequence[Point]
) -> List[Tuple[Optional[int], Optional[int]]]:
    """Greedy nearest-neighbour matching of source → target points.

    Returns list of (src_idx | None, tgt_idx | None) pairs.  Unmatched
    source points get ``(src_idx, None)`` and unmatched target points
    get ``(None, tgt_idx)``.
    """
    src = list(range(len(source)))
    tgt = list(range(len(target)))
    pairs: List[Tuple[Optional[int], Optional[int]]] = []
    used_src: set = set()
    used_tgt: set = set()

    # Build all pairwise distances, greedily match closest
    candidates = []
    for si in src:
        for ti in tgt:
            candidates.append((_dist(source[si], target[ti]), si, ti))
    candidates.sort()

    for _, si, ti in candidates:
        if si in used_src or ti in used_tgt:
            continue
        pairs.append((si, ti))
        used_src.add(si)
        used_tgt.add(ti)

    # Unmatched extras
    for si in src:
        if si not in used_src:
            pairs.append((si, None))
    for ti in tgt:
        if ti not in used_tgt:
            pairs.append((None, ti))

    return pairs


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

def interpolate_points(
    source: Sequence[Point],
    target: Sequence[Point],
    matching: List[Tuple[Optional[int], Optional[int]]],
    t: float,
    easing: str = "ease_in_out",
    width: int = 800,
    height: int = 600,
) -> List[Tuple[float, float, float]]:
    """Return interpolated (x, y, opacity) at parameter *t* ∈ [0, 1]."""
    ease_fn = EASING_MAP.get(easing, _ease_in_out)
    et = ease_fn(max(0.0, min(1.0, t)))
    result: List[Tuple[float, float, float]] = []

    for si, ti in matching:
        if si is not None and ti is not None:
            sx, sy = source[si]
            tx, ty = target[ti]
            x = sx + (tx - sx) * et
            y = sy + (ty - sy) * et
            result.append((x, y, 1.0))
        elif si is not None:
            # Fading out -- drift toward edge
            sx, sy = source[si]
            edge_x = width / 2 + (sx - width / 2) * 1.5
            edge_y = height / 2 + (sy - height / 2) * 1.5
            x = sx + (edge_x - sx) * et
            y = sy + (edge_y - sy) * et
            result.append((x, y, max(0.0, 1.0 - et)))
        else:
            assert ti is not None
            tx, ty = target[ti]
            edge_x = width / 2 + (tx - width / 2) * 1.5
            edge_y = height / 2 + (ty - height / 2) * 1.5
            x = edge_x + (tx - edge_x) * et
            y = edge_y + (ty - edge_y) * et
            result.append((x, y, min(1.0, et)))

    return result


# ---------------------------------------------------------------------------
# Auto-morph target generators
# ---------------------------------------------------------------------------

def _gen_grid(n: int, w: int, h: int) -> List[Point]:
    cols = max(1, int(math.sqrt(n * w / h)))
    rows = max(1, math.ceil(n / cols))
    dx = w / (cols + 1)
    dy = h / (rows + 1)
    pts: List[Point] = []
    for i in range(n):
        r, c = divmod(i, cols)
        pts.append((dx * (c + 1), dy * (r + 1)))
    return pts


def _gen_spiral(n: int, w: int, h: int) -> List[Point]:
    cx, cy = w / 2, h / 2
    pts: List[Point] = []
    for i in range(n):
        t = i / max(1, n - 1)
        angle = t * 4 * math.pi
        radius = t * min(w, h) * 0.4
        pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def _gen_clustered(n: int, w: int, h: int) -> List[Point]:
    k = max(2, min(5, n // 3))
    centers = [(random.uniform(w * 0.2, w * 0.8), random.uniform(h * 0.2, h * 0.8)) for _ in range(k)]
    pts: List[Point] = []
    spread = min(w, h) * 0.08
    for i in range(n):
        cx, cy = centers[i % k]
        pts.append((cx + random.gauss(0, spread), cy + random.gauss(0, spread)))
    return pts


def _gen_circle(n: int, w: int, h: int) -> List[Point]:
    cx, cy = w / 2, h / 2
    r = min(w, h) * 0.38
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]


_TARGET_GENERATORS = {
    "grid": _gen_grid,
    "spiral": _gen_spiral,
    "clustered": _gen_clustered,
    "circle": _gen_circle,
}


def _pick_best_target(source: Sequence[Point], w: int, h: int) -> List[Point]:
    """Pick the target layout that maximises total displacement (most visual)."""
    n = len(source)
    best_dist = -1.0
    best: List[Point] = list(source)
    for name, gen in _TARGET_GENERATORS.items():
        tgt = gen(n, w, h)
        matching = match_points(list(source), tgt)
        total = sum(
            _dist(source[si], tgt[ti])
            for si, ti in matching
            if si is not None and ti is not None
        )
        if total > best_dist:
            best_dist = total
            best = tgt
    return best


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Voronoi Morph</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#1a1a2e;color:#eee;font-family:system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;padding:20px}
h1{font-size:1.4rem;margin-bottom:12px;color:#e0e0e0}
canvas{border:1px solid #333;border-radius:6px;cursor:crosshair}
.controls{display:flex;align-items:center;gap:12px;margin-top:14px;flex-wrap:wrap;justify-content:center}
button{background:#2a9d8f;color:#fff;border:none;border-radius:4px;padding:6px 16px;cursor:pointer;font-size:.9rem}
button:hover{background:#21867a}
label{font-size:.85rem;color:#aaa}
select,input[type=range]{cursor:pointer}
.info{font-size:.8rem;color:#888;margin-top:8px}
</style>
</head>
<body>
<h1>&#x1f300; Voronoi Morph</h1>
<canvas id="c" width="__WIDTH__" height="__HEIGHT__"></canvas>
<div class="controls">
  <button id="playBtn">&#9654; Play</button>
  <label>Speed <input id="speed" type="range" min="0.25" max="4" step="0.25" value="1"></label>
  <label>Frame <input id="scrub" type="range" min="0" max="__MAXFRAME__" value="0"></label>
  <label>Easing <select id="easing">__EASING_OPTIONS__</select></label>
  <label><input id="loop" type="checkbox" __LOOP_CHECKED__> Loop</label>
  <label><input id="trails" type="checkbox" __TRAILS_CHECKED__> Trails</label>
</div>
<div class="info" id="info">Frame 0 / __MAXFRAME__</div>
<script>
(function(){
const W=__WIDTH__,H=__HEIGHT__;
const src=__SOURCE_JSON__;
const tgt=__TARGET_JSON__;
const matching=__MATCHING_JSON__;
const colors=__COLORS_JSON__;
const numFrames=__NUM_FRAMES__;

const canvas=document.getElementById('c');
const ctx=canvas.getContext('2d');
const playBtn=document.getElementById('playBtn');
const speedSlider=document.getElementById('speed');
const scrub=document.getElementById('scrub');
const easingSel=document.getElementById('easing');
const loopChk=document.getElementById('loop');
const trailsChk=document.getElementById('trails');
const info=document.getElementById('info');

// Easing functions
const easings={
  linear:t=>t,
  ease_in_out:t=>3*t*t-2*t*t*t,
  elastic:t=>{if(t===0||t===1)return t;const p=0.3,s=p/4;return Math.pow(2,-10*t)*Math.sin((t-s)*(2*Math.PI)/p)+1},
  bounce:t=>{if(t<1/2.75)return 7.5625*t*t;if(t<2/2.75){t-=1.5/2.75;return 7.5625*t*t+0.75}if(t<2.5/2.75){t-=2.25/2.75;return 7.5625*t*t+0.9375}t-=2.625/2.75;return 7.5625*t*t+0.984375},
  cubic_bezier:t=>t*t*(3-2*t)
};

let playing=false,frame=0,speed=1,trailPts=[];

function getEase(){return easings[easingSel.value]||easings.ease_in_out}

function interp(t){
  const e=getEase();
  const et=e(Math.max(0,Math.min(1,t)));
  const pts=[];
  for(const[si,ti]of matching){
    if(si!==null&&ti!==null){
      const sx=src[si][0],sy=src[si][1],tx=tgt[ti][0],ty=tgt[ti][1];
      pts.push([sx+(tx-sx)*et,sy+(ty-sy)*et,1]);
    }else if(si!==null){
      const sx=src[si][0],sy=src[si][1];
      const ex=W/2+(sx-W/2)*1.5,ey=H/2+(sy-H/2)*1.5;
      pts.push([sx+(ex-sx)*et,sy+(ey-sy)*et,Math.max(0,1-et)]);
    }else{
      const tx=tgt[ti][0],ty=tgt[ti][1];
      const ex=W/2+(tx-W/2)*1.5,ey=H/2+(ty-H/2)*1.5;
      pts.push([ex+(tx-ex)*et,ey+(ty-ey)*et,Math.min(1,et)]);
    }
  }
  return pts;
}

function draw(){
  const t=frame/Math.max(1,numFrames-1);
  const pts=interp(t);

  // Voronoi via nearest-point pixel coloring (downsampled for speed)
  const step=4;
  const imgData=ctx.createImageData(W,H);
  const d=imgData.data;
  for(let y=0;y<H;y+=step){
    for(let x=0;x<W;x+=step){
      let minD=Infinity,ci=0;
      for(let i=0;i<pts.length;i++){
        if(pts[i][2]<0.05)continue;
        const dx=x-pts[i][0],dy=y-pts[i][1];
        const dd=dx*dx+dy*dy;
        if(dd<minD){minD=dd;ci=i}
      }
      const hex=colors[ci%colors.length];
      const r=parseInt(hex.slice(1,3),16),g=parseInt(hex.slice(3,5),16),b=parseInt(hex.slice(5,7),16);
      const a=Math.round(pts[ci%pts.length][2]*255);
      for(let dy2=0;dy2<step&&y+dy2<H;dy2++){
        for(let dx2=0;dx2<step&&x+dx2<W;dx2++){
          const idx=((y+dy2)*W+(x+dx2))*4;
          d[idx]=r;d[idx+1]=g;d[idx+2]=b;d[idx+3]=a;
        }
      }
    }
  }
  ctx.putImageData(imgData,0,0);

  // Draw cell edges (darken boundaries)
  // Draw points
  for(let i=0;i<pts.length;i++){
    if(pts[i][2]<0.05)continue;
    ctx.beginPath();
    ctx.arc(pts[i][0],pts[i][1],3,0,2*Math.PI);
    ctx.fillStyle=`rgba(255,255,255,${pts[i][2]})`;
    ctx.fill();
  }

  // Trails
  if(trailsChk.checked){
    trailPts.push(pts.map(p=>[p[0],p[1]]));
    if(trailPts.length>20)trailPts.shift();
    ctx.globalAlpha=0.15;
    for(const tp of trailPts){
      for(const p of tp){
        ctx.beginPath();
        ctx.arc(p[0],p[1],2,0,2*Math.PI);
        ctx.fillStyle='#fff';
        ctx.fill();
      }
    }
    ctx.globalAlpha=1;
  }else{
    trailPts=[];
  }

  info.textContent=`Frame ${frame} / ${numFrames-1}  |  t = ${t.toFixed(3)}  |  ${easingSel.value}`;
  scrub.value=frame;
}

let animId=null;
let lastTime=0;
function animate(ts){
  if(!playing){animId=null;return}
  const elapsed=ts-lastTime;
  const interval=1000/(30*speed);
  if(elapsed>=interval){
    lastTime=ts;
    frame++;
    if(frame>=numFrames){
      if(loopChk.checked){frame=0;trailPts=[]}
      else{frame=numFrames-1;playing=false;playBtn.textContent='\u25b6 Play'}
    }
    draw();
  }
  animId=requestAnimationFrame(animate);
}

playBtn.onclick=()=>{
  playing=!playing;
  playBtn.textContent=playing?'\u23f8 Pause':'\u25b6 Play';
  if(playing){if(frame>=numFrames-1)frame=0;lastTime=performance.now();animId=requestAnimationFrame(animate)}
};
speedSlider.oninput=()=>{speed=parseFloat(speedSlider.value)};
scrub.oninput=()=>{frame=parseInt(scrub.value);draw()};
easingSel.onchange=()=>draw();

draw();
})();
</script>
</body>
</html>"""


def _generate_html(
    source: Sequence[Point],
    target: Sequence[Point],
    matching: List[Tuple[Optional[int], Optional[int]]],
    config: MorphConfig,
) -> str:
    """Build self-contained HTML string."""
    scheme = COLOR_SCHEMES.get(config.color_scheme, COLOR_SCHEMES[DEFAULT_SCHEME])
    easing_opts = "".join(
        f'<option value="{e}"{"selected" if e == config.easing else ""}>{e}</option>'
        for e in EASING_FUNCTIONS
    )
    # Convert matching to JSON-safe (None → null)
    match_json = json.dumps(matching)

    html = _HTML_TEMPLATE
    html = html.replace("__WIDTH__", str(config.width))
    html = html.replace("__HEIGHT__", str(config.height))
    html = html.replace("__MAXFRAME__", str(config.num_frames - 1))
    html = html.replace("__NUM_FRAMES__", str(config.num_frames))
    html = html.replace("__SOURCE_JSON__", json.dumps([[round(x, 2), round(y, 2)] for x, y in source]))
    html = html.replace("__TARGET_JSON__", json.dumps([[round(x, 2), round(y, 2)] for x, y in target]))
    html = html.replace("__MATCHING_JSON__", match_json)
    html = html.replace("__COLORS_JSON__", json.dumps(scheme))
    html = html.replace("__EASING_OPTIONS__", easing_opts)
    html = html.replace("__LOOP_CHECKED__", "checked" if config.loop else "")
    html = html.replace("__TRAILS_CHECKED__", "checked" if config.show_trails else "")
    return html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def morph(
    source: Sequence[Point],
    target: Sequence[Point],
    output: str | os.PathLike = "morph.html",
    config: Optional[MorphConfig] = None,
) -> str:
    """Generate a Voronoi morph animation HTML file.

    Parameters
    ----------
    source : sequence of (x, y)
        Starting point configuration.
    target : sequence of (x, y)
        Ending point configuration.
    output : path-like
        Destination HTML file.
    config : MorphConfig, optional
        Animation parameters.

    Returns
    -------
    str
        Absolute path to the generated HTML file.
    """
    cfg = config or MorphConfig()
    matching = match_points(list(source), list(target))
    html = _generate_html(source, target, matching, cfg)
    out = Path(output).resolve()
    out.write_text(html, encoding="utf-8")
    return str(out)


def auto_morph(
    source: Sequence[Point],
    output: str | os.PathLike = "morph.html",
    config: Optional[MorphConfig] = None,
) -> str:
    """Auto-discover an interesting target and generate morph animation.

    Tries grid, spiral, clustered, and circle layouts and picks the one
    with the most total point displacement for visual impact.
    """
    cfg = config or MorphConfig()
    target = _pick_best_target(source, cfg.width, cfg.height)
    return morph(source, target, output, cfg)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _read_points(path: str) -> List[Point]:
    """Read points from a text file (one ``x y`` or ``x,y`` pair per line)."""
    pts: List[Point] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                pts.append((float(parts[0]), float(parts[1])))
    return pts


def _demo_points(n: int, w: int, h: int) -> Tuple[List[Point], List[Point]]:
    """Generate random source and spiral target for demo."""
    rng = random.Random(42)
    src = [(rng.uniform(w * 0.1, w * 0.9), rng.uniform(h * 0.1, h * 0.9)) for _ in range(n)]
    tgt = _gen_spiral(n, w, h)
    return src, tgt


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi Morph -- smooth animated interpolation between point sets",
    )
    parser.add_argument("files", nargs="*", help="Source (and optionally target) point files")
    parser.add_argument("-o", "--output", default="morph.html", help="Output HTML path")
    parser.add_argument("--auto", action="store_true", help="Auto-discover target layout")
    parser.add_argument("--demo", action="store_true", help="Run built-in demo")
    parser.add_argument("--width", type=int, default=800)
    parser.add_argument("--height", type=int, default=600)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--duration", type=float, default=3.0, help="Duration in seconds")
    parser.add_argument("--easing", choices=EASING_FUNCTIONS, default="ease_in_out")
    parser.add_argument("--color-scheme", choices=list(COLOR_SCHEMES), default=DEFAULT_SCHEME)
    parser.add_argument("--trails", action="store_true", help="Show point movement trails")
    parser.add_argument("--no-loop", action="store_true", help="Disable animation looping")

    args = parser.parse_args(argv)

    cfg = MorphConfig(
        width=args.width,
        height=args.height,
        fps=args.fps,
        duration_seconds=args.duration,
        easing=args.easing,
        color_scheme=args.color_scheme,
        show_trails=args.trails,
        loop=not args.no_loop,
    )

    if args.demo:
        src, tgt = _demo_points(25, cfg.width, cfg.height)
        out = morph(src, tgt, args.output, cfg)
        print(f"Demo morph → {out}")
        return

    if not args.files:
        parser.error("Provide point file(s) or use --demo")

    source = _read_points(args.files[0])
    if not source:
        parser.error(f"No points found in {args.files[0]}")

    if args.auto or len(args.files) == 1:
        out = auto_morph(source, args.output, cfg)
        print(f"Auto morph → {out}")
    else:
        target = _read_points(args.files[1])
        if not target:
            parser.error(f"No points found in {args.files[1]}")
        out = morph(source, target, args.output, cfg)
        print(f"Morph → {out}")


if __name__ == "__main__":
    main()
