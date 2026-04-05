"""Voronoi flow-field visualiser — vector fields & streamlines over diagrams.

Generate, analyse and render 2-D vector (flow / wind) fields tied to
Voronoi seed layouts.  Useful for spatial-analysis overlays, terrain
drainage visualisation, or purely decorative generative art.

Field types
-----------
- **gradient**  – field derived from a scalar potential surface
                  (height = f(x,y)).  Vectors point uphill.
- **curl**      – divergence-free rotational field (incompressible flow).
- **random**    – Perlin-style smooth random field.
- **radial**    – field radiating outward (or inward) from a focus point.
- **dipole**    – two-pole source/sink field.

Outputs
-------
- **SVG** with Voronoi cells coloured by field magnitude plus streamline
  curves rendered as smooth polylines.
- **JSON** with per-cell vector data (angle, magnitude) for downstream use.

CLI
---
::

    python vormap_flowfield.py --seeds 80 --field gradient -o flow.svg
    python vormap_flowfield.py --seeds 120 --field curl --streamlines 60 -o curl.svg
    python vormap_flowfield.py --seeds 200 --field random --json flow.json
    python vormap_flowfield.py --seeds 100 --field dipole --width 800 --height 600 -o dipole.svg

"""

import argparse
import json
import math
import os
import random as _rand
import sys

import vormap

# ---------------------------------------------------------------------------
# Voronoi computation (same KD-free approach used elsewhere in VoronoiMap)
# ---------------------------------------------------------------------------

def _voronoi_cells(seeds, width, height, step=2):
    """Assign pixels to nearest seed → region membership + boundary flag."""
    n = len(seeds)
    cells = {i: [] for i in range(n)}
    boundary = set()
    for y in range(0, height, step):
        for x in range(0, width, step):
            best = -1
            best_d = float("inf")
            second_d = float("inf")
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    second_d = best_d
                    best_d = d
                    best = i
                elif d < second_d:
                    second_d = d
            cells[best].append((x, y))
            if math.sqrt(second_d) - math.sqrt(best_d) < step * 1.5:
                boundary.add((x, y))
    return cells, boundary


def _poisson_disk_seeds(width, height, n, rng=None):
    """Quick Poisson-ish seed placement via dart throwing."""
    rng = rng or _rand.Random()
    r = math.sqrt(width * height / (n * math.pi)) * 0.8
    pts = []
    attempts = 0
    while len(pts) < n and attempts < n * 30:
        cx, cy = rng.uniform(10, width - 10), rng.uniform(10, height - 10)
        ok = True
        for px, py in pts:
            if (cx - px) ** 2 + (cy - py) ** 2 < r * r:
                ok = False
                break
        if ok:
            pts.append((cx, cy))
        attempts += 1
    # Fill remainder randomly
    while len(pts) < n:
        pts.append((rng.uniform(10, width - 10), rng.uniform(10, height - 10)))
    return pts


# ---------------------------------------------------------------------------
# Field generators
# ---------------------------------------------------------------------------

def _field_gradient(x, y, w, h):
    """Gradient of a multi-peak scalar surface."""
    # Scalar: sum of Gaussians
    peaks = [(w * 0.3, h * 0.3, 1.0), (w * 0.7, h * 0.6, 0.8),
             (w * 0.5, h * 0.8, 0.6)]
    dx, dy = 0.0, 0.0
    for px, py, amp in peaks:
        sx, sy = w * 0.25, h * 0.25
        ex = math.exp(-((x - px) ** 2 / (2 * sx * sx) + (y - py) ** 2 / (2 * sy * sy)))
        dx += amp * ex * (-(x - px) / (sx * sx))
        dy += amp * ex * (-(y - py) / (sy * sy))
    return -dx, -dy  # uphill


def _field_curl(x, y, w, h):
    """Divergence-free rotational field around centre."""
    cx, cy = w / 2, h / 2
    rx, ry = x - cx, y - cy
    r = math.sqrt(rx * rx + ry * ry) + 1e-9
    scale = 1.0 / (1.0 + r / (min(w, h) * 0.3))
    return -ry * scale, rx * scale


def _noise2d(x, y, seed=42):
    """Simple value-noise via hashing (no external deps)."""
    def _hash(ix, iy):
        n = ix * 374761393 + iy * 668265263 + seed
        n = (n ^ (n >> 13)) * 1274126177
        return ((n ^ (n >> 16)) & 0x7fffffff) / 0x7fffffff
    ix, iy = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - ix, y - iy
    fx2 = fx * fx * (3 - 2 * fx)
    fy2 = fy * fy * (3 - 2 * fy)
    a = _hash(ix, iy)
    b = _hash(ix + 1, iy)
    c = _hash(ix, iy + 1)
    d = _hash(ix + 1, iy + 1)
    return a + (b - a) * fx2 + (c - a) * fy2 + (a - b - c + d) * fx2 * fy2


def _field_random(x, y, w, h):
    """Smooth random field via value noise."""
    scale = 4.0 / min(w, h)
    vx = _noise2d(x * scale, y * scale, seed=42) * 2 - 1
    vy = _noise2d(x * scale + 100, y * scale + 100, seed=42) * 2 - 1
    return vx, vy


def _field_radial(x, y, w, h):
    """Radial outward field from centre."""
    cx, cy = w / 2, h / 2
    rx, ry = x - cx, y - cy
    r = math.sqrt(rx * rx + ry * ry) + 1e-9
    return rx / r, ry / r


def _field_dipole(x, y, w, h):
    """Source + sink dipole field."""
    src = (w * 0.3, h * 0.5)
    snk = (w * 0.7, h * 0.5)
    vx, vy = 0.0, 0.0
    for (px, py), sign in [(src, 1.0), (snk, -1.0)]:
        rx, ry = x - px, y - py
        r2 = rx * rx + ry * ry + 1e-4
        r = math.sqrt(r2)
        vx += sign * rx / (r2 + 1)
        vy += sign * ry / (r2 + 1)
    return vx, vy


FIELDS = {
    "gradient": _field_gradient,
    "curl": _field_curl,
    "random": _field_random,
    "radial": _field_radial,
    "dipole": _field_dipole,
}

# ---------------------------------------------------------------------------
# Streamline integration (RK2)
# ---------------------------------------------------------------------------

def _integrate_streamline(field_fn, x0, y0, w, h, steps=120, dt=2.0):
    """Trace a streamline via 2nd-order Runge-Kutta."""
    pts = [(x0, y0)]
    x, y = x0, y0
    for _ in range(steps):
        vx1, vy1 = field_fn(x, y, w, h)
        mag = math.sqrt(vx1 * vx1 + vy1 * vy1) + 1e-12
        vx1, vy1 = vx1 / mag, vy1 / mag
        mx, my = x + vx1 * dt * 0.5, y + vy1 * dt * 0.5
        vx2, vy2 = field_fn(mx, my, w, h)
        mag2 = math.sqrt(vx2 * vx2 + vy2 * vy2) + 1e-12
        vx2, vy2 = vx2 / mag2, vy2 / mag2
        x += vx2 * dt
        y += vy2 * dt
        if x < -10 or x > w + 10 or y < -10 or y > h + 10:
            break
        pts.append((x, y))
    return pts


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def _mag_color(mag, max_mag):
    """Map magnitude 0..max_mag to a blue→cyan→green→yellow→red ramp."""
    t = min(mag / (max_mag + 1e-12), 1.0)
    # 5-stop ramp
    stops = [
        (0.0, (30, 60, 180)),
        (0.25, (0, 180, 220)),
        (0.5, (30, 200, 80)),
        (0.75, (240, 220, 30)),
        (1.0, (220, 50, 30)),
    ]
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            f = (t - t0) / (t1 - t0 + 1e-12)
            r = int(c0[0] + (c1[0] - c0[0]) * f)
            g = int(c0[1] + (c1[1] - c0[1]) * f)
            b = int(c0[2] + (c1[2] - c0[2]) * f)
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#dc3220"


# ---------------------------------------------------------------------------
# SVG renderer
# ---------------------------------------------------------------------------

def render_svg(seeds, cells, boundary, field_fn, streamlines, width, height):
    """Render Voronoi cells + flow field + streamlines as SVG."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="#0a0a1a"/>',
    ]

    # Per-cell average magnitude
    cell_mags = {}
    for idx, pts in cells.items():
        if not pts:
            cell_mags[idx] = 0
            continue
        mag_sum = 0
        for px, py in pts[::max(1, len(pts) // 20)]:
            vx, vy = field_fn(px, py, width, height)
            mag_sum += math.sqrt(vx * vx + vy * vy)
        cell_mags[idx] = mag_sum / max(1, len(pts[::max(1, len(pts) // 20)]))

    max_mag = max(cell_mags.values()) if cell_mags else 1

    # Draw cells as coloured point clusters (fast)
    step = 2
    parts.append('<g opacity="0.6">')
    for idx, pts in cells.items():
        col = _mag_color(cell_mags[idx], max_mag)
        for px, py in pts:
            parts.append(f'<rect x="{px}" y="{py}" width="{step}" height="{step}" fill="{col}"/>')
    parts.append("</g>")

    # Boundaries
    parts.append('<g opacity="0.15">')
    for bx, by in boundary:
        parts.append(f'<rect x="{bx}" y="{by}" width="{step}" height="{step}" fill="#fff"/>')
    parts.append("</g>")

    # Per-cell arrows (one per cell at centroid)
    parts.append('<g stroke="#fff" stroke-width="1.2" fill="none" opacity="0.5">')
    for idx, (sx, sy) in enumerate(seeds):
        vx, vy = field_fn(sx, sy, width, height)
        mag = math.sqrt(vx * vx + vy * vy) + 1e-12
        arrow_len = min(25, mag / max_mag * 25 + 5)
        ux, uy = vx / mag, vy / mag
        ex, ey = sx + ux * arrow_len, sy + uy * arrow_len
        parts.append(f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}"/>')
        # arrowhead
        ax = ex - ux * 4 + uy * 3
        ay = ey - uy * 4 - ux * 3
        bx_ = ex - ux * 4 - uy * 3
        by_ = ey - uy * 4 + ux * 3
        parts.append(f'<polygon points="{ex:.1f},{ey:.1f} {ax:.1f},{ay:.1f} {bx_:.1f},{by_:.1f}" '
                     f'fill="#fff" stroke="none"/>')
    parts.append("</g>")

    # Streamlines
    parts.append('<g fill="none" stroke-width="1.5" opacity="0.8">')
    for sl in streamlines:
        if len(sl) < 3:
            continue
        col = _mag_color(_rand.random() * max_mag * 0.8, max_mag)
        d = f"M{sl[0][0]:.1f},{sl[0][1]:.1f}"
        for px, py in sl[1:]:
            d += f" L{px:.1f},{py:.1f}"
        parts.append(f'<path d="{d}" stroke="{col}"/>')
    parts.append("</g>")

    # Seed dots
    parts.append('<g fill="#fff" opacity="0.4">')
    for sx, sy in seeds:
        parts.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="2"/>')
    parts.append("</g>")

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def export_json(seeds, cells, field_fn, width, height):
    """Export per-cell vector data."""
    data = {"width": width, "height": height, "cells": []}
    for idx, (sx, sy) in enumerate(seeds):
        vx, vy = field_fn(sx, sy, width, height)
        mag = math.sqrt(vx * vx + vy * vy)
        angle = math.degrees(math.atan2(vy, vx))
        data["cells"].append({
            "seed": [round(sx, 2), round(sy, 2)],
            "vector": [round(vx, 4), round(vy, 4)],
            "magnitude": round(mag, 4),
            "angle_deg": round(angle, 2),
            "pixel_count": len(cells.get(idx, [])),
        })
    return data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Voronoi flow-field visualiser")
    ap.add_argument("--seeds", type=int, default=80, help="Number of Voronoi seeds")
    ap.add_argument("--field", choices=list(FIELDS.keys()), default="gradient",
                    help="Field type (default: gradient)")
    ap.add_argument("--streamlines", type=int, default=40,
                    help="Number of streamlines to trace")
    ap.add_argument("--width", type=int, default=600, help="Canvas width")
    ap.add_argument("--height", type=int, default=600, help="Canvas height")
    ap.add_argument("--step", type=int, default=2,
                    help="Pixel step for cell rasterisation (lower = finer, slower)")
    ap.add_argument("--seed", type=int, default=None, help="Random seed")
    ap.add_argument("-o", "--output", default=None, help="Output SVG file")
    ap.add_argument("--json", default=None, help="Output JSON file")
    args = ap.parse_args(argv)

    rng = _rand.Random(args.seed)
    _rand.seed(args.seed)

    w, h = args.width, args.height
    print(f"Generating {args.seeds} seeds on {w}x{h} canvas...")
    seeds = _poisson_disk_seeds(w, h, args.seeds, rng)

    print(f"Computing Voronoi cells (step={args.step})...")
    cells, boundary = _voronoi_cells(seeds, w, h, step=args.step)

    field_fn = FIELDS[args.field]

    print(f"Tracing {args.streamlines} streamlines ({args.field} field)...")
    slines = []
    for _ in range(args.streamlines):
        x0 = rng.uniform(10, w - 10)
        y0 = rng.uniform(10, h - 10)
        slines.append(_integrate_streamline(field_fn, x0, y0, w, h))

    if args.output:
        svg = render_svg(seeds, cells, boundary, field_fn, slines, w, h)
        safe_out = vormap.validate_output_path(args.output, allow_absolute=True)
        with open(safe_out, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"SVG written -> {safe_out}")

    if args.json:
        data = export_json(seeds, cells, field_fn, w, h)
        safe_json = vormap.validate_output_path(args.json, allow_absolute=True)
        with open(safe_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"JSON written -> {safe_json}")

    if not args.output and not args.json:
        # Preview stats
        data = export_json(seeds, cells, field_fn, w, h)
        mags = [c["magnitude"] for c in data["cells"]]
        print(f"\nField: {args.field}")
        print(f"Cells: {len(seeds)}")
        print(f"Streamlines: {len(slines)}")
        print(f"Magnitude  min={min(mags):.4f}  max={max(mags):.4f}  "
              f"avg={sum(mags)/len(mags):.4f}")
        print("\nUse -o flow.svg to render, or --json flow.json to export data.")


if __name__ == "__main__":
    main()
