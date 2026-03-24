"""Voronoi String Art Generator.

Renders a Voronoi diagram as string art: pins are placed at Voronoi seed
positions and along cell edges, then coloured threads are drawn between
connected pins following the Delaunay triangulation. The result looks like
the nail-and-thread artwork popular in DIY craft.

Features
--------
- Configurable **pin count** and **thread density**
- Multiple frame shapes: **circle**, **square**, **hexagon**
- Thread colormaps: **rainbow**, **gradient**, **mono**, **warm**, **cool**
- Optional **background board** colour (dark wood, cork, white, black)
- Thread **opacity** and **thickness** controls
- Boundary pins along the frame edge for a finished look
- SVG output with optional JSON pin/thread data export

CLI usage
---------
::

    python vormap_stringart.py 800 800 art.svg
    python vormap_stringart.py 800 800 art.svg --seeds 50 --frame circle --colormap rainbow
    python vormap_stringart.py 600 600 art.svg --board dark-wood --threads 3 --opacity 0.6
    python vormap_stringart.py 800 800 art.json --format json

Programmatic usage
------------------
::

    import vormap_stringart

    result = vormap_stringart.generate(800, 800, num_seeds=40, frame="circle")
    vormap_stringart.save_svg(result, "art.svg")
    vormap_stringart.save_json(result, "art.json")

"""

import argparse
import json
import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Board colours
# ---------------------------------------------------------------------------

BOARD_COLORS: Dict[str, str] = {
    "dark-wood": "#3B2314",
    "light-wood": "#C4A56E",
    "cork": "#B5835A",
    "black": "#1A1A1A",
    "white": "#F5F5F0",
    "slate": "#4A5568",
}

# ---------------------------------------------------------------------------
# Thread colormaps
# ---------------------------------------------------------------------------

COLORMAPS: Dict[str, List[str]] = {
    "rainbow": ["#FF0000", "#FF7700", "#FFFF00", "#00CC00", "#0066FF", "#8B00FF"],
    "warm": ["#FF4136", "#FF851B", "#FFDC00", "#FF6B6B", "#E8590C"],
    "cool": ["#0074D9", "#7FDBFF", "#39CCCC", "#3D9970", "#2ECC40"],
    "mono": ["#CCCCCC", "#AAAAAA", "#888888", "#666666", "#444444"],
    "gradient": ["#FF6B6B", "#C06C84", "#6C5B7B", "#355C7D", "#2C3E50"],
    "neon": ["#FF00FF", "#00FFFF", "#FFFF00", "#FF0080", "#80FF00"],
    "earth": ["#8B4513", "#A0522D", "#CD853F", "#DEB887", "#D2691E"],
    "pastel": ["#FFB3BA", "#BAFFC9", "#BAE1FF", "#FFFFBA", "#E8BAFF"],
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Pin:
    """A pin (nail) position."""
    x: float
    y: float
    is_boundary: bool = False
    index: int = 0


@dataclass
class Thread:
    """A thread connecting two pins."""
    pin_a: int  # index
    pin_b: int  # index
    color: str = "#CCCCCC"
    opacity: float = 0.7
    thickness: float = 0.8


@dataclass
class StringArtResult:
    """Complete string art result."""
    width: int
    height: int
    pins: List[Pin] = field(default_factory=list)
    threads: List[Thread] = field(default_factory=list)
    board_color: str = "#1A1A1A"
    frame_shape: str = "circle"
    seed_points: List[Tuple[float, float]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Voronoi / Delaunay helpers (minimal, no external deps)
# ---------------------------------------------------------------------------


def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _circumcircle(p1, p2, p3):
    """Return (cx, cy, r) of circumscribed circle, or None if degenerate."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def _bowyer_watson(points: List[Tuple[float, float]], width: int, height: int) -> List[Tuple[int, int, int]]:
    """Simple Bowyer-Watson Delaunay triangulation. Returns index triples."""
    # Super-triangle
    margin = max(width, height) * 10
    st = [(-margin, -margin), (2 * width + margin, -margin), (width / 2, 2 * height + margin)]
    all_pts = list(st) + list(points)
    triangles = [(0, 1, 2)]

    for i in range(3, len(all_pts)):
        px, py = all_pts[i]
        bad = []
        for tri in triangles:
            cc = _circumcircle(all_pts[tri[0]], all_pts[tri[1]], all_pts[tri[2]])
            if cc and math.hypot(px - cc[0], py - cc[1]) < cc[2]:
                bad.append(tri)

        # Find boundary polygon
        edges = []
        for tri in bad:
            for e in [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]:
                shared = False
                for other in bad:
                    if other is tri:
                        continue
                    oe = {(other[0], other[1]), (other[1], other[2]), (other[2], other[0]),
                          (other[1], other[0]), (other[2], other[1]), (other[0], other[2])}
                    if e in oe or (e[1], e[0]) in oe:
                        shared = True
                        break
                if not shared:
                    edges.append(e)

        for tri in bad:
            triangles.remove(tri)

        for e in edges:
            triangles.append((e[0], e[1], i))

    # Filter out triangles that reference super-triangle vertices
    result = []
    for tri in triangles:
        if tri[0] < 3 or tri[1] < 3 or tri[2] < 3:
            continue
        result.append((tri[0] - 3, tri[1] - 3, tri[2] - 3))
    return result


def _delaunay_edges(points: List[Tuple[float, float]], width: int, height: int) -> List[Tuple[int, int]]:
    """Return unique edges from Delaunay triangulation."""
    tris = _bowyer_watson(points, width, height)
    edges = set()
    for a, b, c in tris:
        for e in [(min(a, b), max(a, b)), (min(b, c), max(b, c)), (min(a, c), max(a, c))]:
            edges.add(e)
    return list(edges)


# ---------------------------------------------------------------------------
# Frame boundary pin generation
# ---------------------------------------------------------------------------


def _circle_boundary_pins(cx: float, cy: float, radius: float, count: int) -> List[Tuple[float, float]]:
    pins = []
    for i in range(count):
        angle = 2 * math.pi * i / count
        pins.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pins


def _square_boundary_pins(x0: float, y0: float, x1: float, y1: float, per_side: int) -> List[Tuple[float, float]]:
    pins = []
    for i in range(per_side):
        t = i / per_side
        pins.append((x0 + t * (x1 - x0), y0))  # top
        pins.append((x1, y0 + t * (y1 - y0)))    # right
        pins.append((x1 - t * (x1 - x0), y1))    # bottom
        pins.append((x0, y1 - t * (y1 - y0)))    # left
    return pins


def _hexagon_boundary_pins(cx: float, cy: float, radius: float, per_side: int) -> List[Tuple[float, float]]:
    pins = []
    corners = []
    for i in range(6):
        angle = math.pi / 6 + i * math.pi / 3
        corners.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    for i in range(6):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 6]
        for j in range(per_side):
            t = j / per_side
            pins.append((ax + t * (bx - ax), ay + t * (by - ay)))
    return pins


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------


def generate(
    width: int = 800,
    height: int = 800,
    num_seeds: int = 40,
    frame: str = "circle",
    colormap: str = "rainbow",
    board: str = "dark-wood",
    thread_opacity: float = 0.7,
    thread_thickness: float = 0.8,
    boundary_pins: int = 60,
    seed: Optional[int] = None,
) -> StringArtResult:
    """Generate a Voronoi string art pattern.

    Parameters
    ----------
    width, height : int
        Canvas size in pixels.
    num_seeds : int
        Number of interior seed points.
    frame : str
        Frame shape — ``circle``, ``square``, or ``hexagon``.
    colormap : str
        Thread color scheme name.
    board : str
        Board background colour name or hex code.
    thread_opacity : float
        Thread opacity (0–1).
    thread_thickness : float
        Thread stroke width in SVG units.
    boundary_pins : int
        Number of pins along the frame boundary.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    StringArtResult
    """
    rng = random.Random(seed)

    cx, cy = width / 2, height / 2
    margin = min(width, height) * 0.08
    radius = min(width, height) / 2 - margin

    # Generate seed points inside the frame
    seed_points: List[Tuple[float, float]] = []
    for _ in range(num_seeds * 10):
        if len(seed_points) >= num_seeds:
            break
        x = rng.uniform(margin, width - margin)
        y = rng.uniform(margin, height - margin)
        if frame == "circle":
            if _distance((x, y), (cx, cy)) > radius:
                continue
        elif frame == "hexagon":
            # Rough hexagon containment
            dx, dy = abs(x - cx), abs(y - cy)
            if dx > radius * 0.866 or dy > radius or dx * 0.5 + dy * 0.866 > radius * 0.866:
                continue
        seed_points.append((x, y))

    # Boundary pins
    if frame == "circle":
        bp = _circle_boundary_pins(cx, cy, radius, boundary_pins)
    elif frame == "hexagon":
        bp = _hexagon_boundary_pins(cx, cy, radius, boundary_pins // 6 or 10)
    else:  # square
        bp = _square_boundary_pins(margin, margin, width - margin, height - margin, boundary_pins // 4 or 15)

    # Combine all points: seed points first, then boundary
    all_points = list(seed_points) + bp
    pins: List[Pin] = []
    for i, (px, py) in enumerate(all_points):
        pins.append(Pin(x=px, y=py, is_boundary=(i >= len(seed_points)), index=i))

    # Delaunay edges → threads
    edges = _delaunay_edges(all_points, width, height)

    # Assign colours
    colors = COLORMAPS.get(colormap, COLORMAPS["rainbow"])

    threads: List[Thread] = []
    for a, b in edges:
        # Color based on midpoint angle from centre
        mx = (all_points[a][0] + all_points[b][0]) / 2
        my = (all_points[a][1] + all_points[b][1]) / 2
        angle = math.atan2(my - cy, mx - cx)
        idx = int(((angle + math.pi) / (2 * math.pi)) * len(colors)) % len(colors)
        threads.append(Thread(
            pin_a=a,
            pin_b=b,
            color=colors[idx],
            opacity=thread_opacity,
            thickness=thread_thickness,
        ))

    board_color = BOARD_COLORS.get(board, board if board.startswith("#") else BOARD_COLORS["dark-wood"])

    return StringArtResult(
        width=width,
        height=height,
        pins=pins,
        threads=threads,
        board_color=board_color,
        frame_shape=frame,
        seed_points=seed_points,
    )


# ---------------------------------------------------------------------------
# SVG output
# ---------------------------------------------------------------------------


def _svg_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_svg(result: StringArtResult) -> str:
    """Render a StringArtResult to an SVG string."""
    w, h = result.width, result.height
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
        f'  <rect width="{w}" height="{h}" fill="{result.board_color}"/>',
    ]

    # Draw frame outline
    cx, cy = w / 2, h / 2
    margin = min(w, h) * 0.08
    radius = min(w, h) / 2 - margin
    if result.frame_shape == "circle":
        lines.append(f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{radius:.1f}" fill="none" '
                     f'stroke="#888888" stroke-width="2" opacity="0.3"/>')
    elif result.frame_shape == "hexagon":
        pts = []
        for i in range(6):
            a = math.pi / 6 + i * math.pi / 3
            pts.append(f"{cx + radius * math.cos(a):.1f},{cy + radius * math.sin(a):.1f}")
        lines.append(f'  <polygon points="{" ".join(pts)}" fill="none" '
                     f'stroke="#888888" stroke-width="2" opacity="0.3"/>')
    else:
        x0, y0 = margin, margin
        x1, y1 = w - margin, h - margin
        lines.append(f'  <rect x="{x0:.1f}" y="{y0:.1f}" width="{x1-x0:.1f}" height="{y1-y0:.1f}" '
                     f'fill="none" stroke="#888888" stroke-width="2" opacity="0.3"/>')

    # Draw threads
    for t in result.threads:
        pa = result.pins[t.pin_a]
        pb = result.pins[t.pin_b]
        lines.append(
            f'  <line x1="{pa.x:.1f}" y1="{pa.y:.1f}" x2="{pb.x:.1f}" y2="{pb.y:.1f}" '
            f'stroke="{t.color}" stroke-width="{t.thickness}" opacity="{t.opacity}" '
            f'stroke-linecap="round"/>'
        )

    # Draw pins
    for p in result.pins:
        r = 2.5 if not p.is_boundary else 1.8
        fill = "#C0C0C0" if not p.is_boundary else "#808080"
        lines.append(f'  <circle cx="{p.x:.1f}" cy="{p.y:.1f}" r="{r}" fill="{fill}" opacity="0.9"/>')

    lines.append('</svg>')
    return "\n".join(lines)


def save_svg(result: StringArtResult, path: str) -> None:
    """Save string art as SVG."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_svg(result))


def save_json(result: StringArtResult, path: str) -> None:
    """Save pin/thread data as JSON for external renderers."""
    data = {
        "width": result.width,
        "height": result.height,
        "frame": result.frame_shape,
        "board_color": result.board_color,
        "pins": [{"x": p.x, "y": p.y, "boundary": p.is_boundary, "index": p.index} for p in result.pins],
        "threads": [
            {"from": t.pin_a, "to": t.pin_b, "color": t.color,
             "opacity": t.opacity, "thickness": t.thickness}
            for t in result.threads
        ],
        "seed_points": [{"x": x, "y": y} for x, y in result.seed_points],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi String Art Generator — nail-and-thread diagrams from Voronoi seeds",
    )
    parser.add_argument("width", type=int, help="Canvas width in pixels")
    parser.add_argument("height", type=int, help="Canvas height in pixels")
    parser.add_argument("output", help="Output file path (.svg or .json)")
    parser.add_argument("--seeds", type=int, default=40, help="Number of interior seed points (default: 40)")
    parser.add_argument("--frame", choices=["circle", "square", "hexagon"], default="circle",
                        help="Frame shape (default: circle)")
    parser.add_argument("--colormap", choices=list(COLORMAPS.keys()), default="rainbow",
                        help="Thread color scheme (default: rainbow)")
    parser.add_argument("--board", default="dark-wood",
                        help="Board colour name or hex code (default: dark-wood)")
    parser.add_argument("--opacity", type=float, default=0.7, help="Thread opacity 0-1 (default: 0.7)")
    parser.add_argument("--thickness", type=float, default=0.8, help="Thread thickness (default: 0.8)")
    parser.add_argument("--boundary-pins", type=int, default=60,
                        help="Number of pins along frame boundary (default: 60)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--format", choices=["svg", "json"], default=None,
                        help="Output format (auto-detected from extension)")
    args = parser.parse_args()

    result = generate(
        width=args.width,
        height=args.height,
        num_seeds=args.seeds,
        frame=args.frame,
        colormap=args.colormap,
        board=args.board,
        thread_opacity=args.opacity,
        thread_thickness=args.thickness,
        boundary_pins=args.boundary_pins,
        seed=args.seed,
    )

    fmt = args.format
    if fmt is None:
        fmt = "json" if args.output.lower().endswith(".json") else "svg"

    if fmt == "json":
        save_json(result, args.output)
    else:
        save_svg(result, args.output)

    print(f"String art saved to {args.output}")
    print(f"  {len(result.pins)} pins, {len(result.threads)} threads")
    print(f"  Frame: {result.frame_shape}, Colormap: {args.colormap}")


if __name__ == "__main__":
    main()
