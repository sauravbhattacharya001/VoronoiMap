"""Autonomous Spatial Dreamer — generate synthetic distributions from learned patterns.

The dreamer analyzes an input point dataset, learns its spatial "personality"
(density profile, clustering behavior, regularity, anisotropy), then generates
novel synthetic point sets that share the same spatial DNA but are entirely new.

Think of it as "dreaming" new spatial configurations inspired by real data —
useful for augmentation, simulation, what-if analysis, and creative exploration.

**Modes:**

- **dream**     — generate one synthetic distribution (default)
- **series**    — generate N variations with increasing novelty
- **interpolate** — blend between two datasets' spatial personalities
- **hallucinate** — push novelty to extremes (creative mode)

**Learned features:**

- Local density profile via KDE on a grid
- Nearest-neighbor distance distribution (mean, std, skew)
- Cluster structure (centroid offsets, spreads)
- Angular distribution from centroid
- Bounding shape and aspect ratio
- Point count and convex hull efficiency

Usage (CLI)::

    python vormap_dream.py data.txt                          # dream one variation
    python vormap_dream.py data.txt --novelty 0.5            # 50% novelty
    python vormap_dream.py data.txt --series 5 --html        # 5 variations + report
    python vormap_dream.py --interpolate a.txt b.txt --steps 4  # blend A→B
    python vormap_dream.py data.txt --hallucinate --html     # extreme creativity
    python vormap_dream.py data.txt --out dream.txt          # save to file
    python vormap_dream.py data.txt --json                   # JSON output
    python vormap_dream.py data.txt --seed 42                # reproducible

Usage (Python API)::

    from vormap_dream import dream, dream_series, interpolate_dreams
    pts = dream("data.txt", novelty=0.3)
    series = dream_series("data.txt", count=5)
    blended = interpolate_dreams("a.txt", "b.txt", steps=4)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict

from vormap_utils import load_points, compute_nn_distances, euclidean

# ---------------------------------------------------------------------------
# Spatial personality extraction
# ---------------------------------------------------------------------------


def _centroid(pts):
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _bounding_box(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _angular_profile(pts, sectors=12):
    """Fraction of points in each angular sector from centroid."""
    cx, cy = _centroid(pts)
    counts = [0] * sectors
    for x, y in pts:
        angle = math.atan2(y - cy, x - cx)
        if angle < 0:
            angle += 2 * math.pi
        idx = min(int(angle / (2 * math.pi) * sectors), sectors - 1)
        counts[idx] += 1
    total = max(sum(counts), 1)
    return [c / total for c in counts]


def _nn_stats(pts):
    """Nearest-neighbor distance statistics."""
    dists = compute_nn_distances(pts)
    if not dists:
        return {"mean": 0, "std": 0, "skew": 0}
    n = len(dists)
    mean = sum(dists) / n
    var = sum((d - mean) ** 2 for d in dists) / max(n - 1, 1)
    std = math.sqrt(var)
    if std > 0 and n > 2:
        skew = sum(((d - mean) / std) ** 3 for d in dists) / n
    else:
        skew = 0.0
    return {"mean": mean, "std": std, "skew": skew}


def _density_grid(pts, bbox, grid_size=8):
    """Compute a density grid (counts per cell)."""
    x0, y0, x1, y1 = bbox
    w = max(x1 - x0, 1e-9)
    h = max(y1 - y0, 1e-9)
    grid = [[0] * grid_size for _ in range(grid_size)]
    for x, y in pts:
        ci = min(int((x - x0) / w * grid_size), grid_size - 1)
        ri = min(int((y - y0) / h * grid_size), grid_size - 1)
        grid[ri][ci] += 1
    # Normalize
    total = max(len(pts), 1)
    return [[c / total for c in row] for row in grid]


def _simple_clusters(pts, k=None):
    """Simple k-means clustering. Auto-pick k if not given."""
    n = len(pts)
    if n < 3:
        return [{"center": _centroid(pts), "spread": 0, "weight": 1.0}]
    if k is None:
        k = max(1, min(int(math.sqrt(n / 2)), 8))
    rng = random.Random(42)
    centers = [pts[i] for i in rng.sample(range(n), min(k, n))]

    for _ in range(30):
        clusters = defaultdict(list)
        for p in pts:
            best = min(range(len(centers)), key=lambda i: euclidean(p, centers[i]))
            clusters[best].append(p)
        new_centers = []
        for i in range(len(centers)):
            if clusters[i]:
                new_centers.append(_centroid(clusters[i]))
            else:
                new_centers.append(centers[i])
        if new_centers == centers:
            break
        centers = new_centers

    result = []
    for i, c in enumerate(centers):
        members = clusters.get(i, [])
        if members:
            spread = math.sqrt(sum(euclidean(p, c) ** 2 for p in members) / len(members))
            result.append({"center": c, "spread": spread, "weight": len(members) / n})
        else:
            result.append({"center": c, "spread": 0, "weight": 0})
    return [r for r in result if r["weight"] > 0]


def extract_personality(pts):
    """Extract spatial personality from a point set."""
    bbox = _bounding_box(pts)
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0

    # Normalize points to [0,1]x[0,1] for personality
    norm_pts = [((x - x0) / max(w, 1e-9), (y - y0) / max(h, 1e-9)) for x, y in pts]

    clusters = _simple_clusters(norm_pts)
    nn = _nn_stats(norm_pts)
    angular = _angular_profile(norm_pts)
    density = _density_grid(norm_pts, (0, 0, 1, 1))

    return {
        "n_points": len(pts),
        "aspect_ratio": w / max(h, 1e-9),
        "bbox": bbox,
        "clusters": clusters,
        "nn_stats": nn,
        "angular_profile": angular,
        "density_grid": density,
    }


# ---------------------------------------------------------------------------
# Dream generation
# ---------------------------------------------------------------------------


def _sample_from_personality(personality, novelty, rng):
    """Generate a point set from a learned personality with novelty control."""
    n = personality["n_points"]
    clusters = personality["clusters"]
    density = personality["density_grid"]
    angular = personality["angular_profile"]
    nn_mean = personality["nn_stats"]["mean"]

    points = []
    grid_size = len(density)

    # Strategy: blend cluster-based and density-based sampling
    # Low novelty = faithful to clusters; high novelty = more random exploration
    cluster_weight = max(0.0, 1.0 - novelty * 1.5)
    density_weight = 1.0 - cluster_weight

    for _ in range(n):
        if rng.random() < cluster_weight and clusters:
            # Sample from a cluster
            weights = [c["weight"] for c in clusters]
            total_w = sum(weights)
            r = rng.random() * total_w
            cum = 0
            chosen = clusters[0]
            for c in clusters:
                cum += c["weight"]
                if r <= cum:
                    chosen = c
                    break
            cx, cy = chosen["center"]
            spread = chosen["spread"] * (1.0 + novelty * 0.5)
            x = cx + rng.gauss(0, spread + 1e-6)
            y = cy + rng.gauss(0, spread + 1e-6)
        else:
            # Sample from density grid with noise
            flat = []
            for ri in range(grid_size):
                for ci in range(grid_size):
                    flat.append((ri, ci, density[ri][ci]))
            total_d = sum(f[2] for f in flat)
            if total_d > 0:
                r = rng.random() * total_d
                cum = 0
                ri, ci = 0, 0
                for fri, fci, fd in flat:
                    cum += fd
                    if r <= cum:
                        ri, ci = fri, fci
                        break
                x = (ci + rng.random()) / grid_size
                y = (ri + rng.random()) / grid_size
            else:
                x = rng.random()
                y = rng.random()

            # Add novelty jitter
            jitter = novelty * 0.15
            x += rng.gauss(0, jitter)
            y += rng.gauss(0, jitter)

        points.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y))))

    # Apply repulsion pass to respect NN distance character.
    # Uses a grid-based spatial index for O(n) average-case neighbor
    # lookups instead of the previous O(n²) brute-force per iteration.
    if nn_mean > 0 and len(points) > 1:
        target_nn = nn_mean * (1.0 + (novelty - 0.5) * 0.3)
        half_target = target_nn * 0.5
        # Grid cell size = search radius so only 9 cells need checking
        cell_size = max(half_target, 1e-9)

        for _ in range(5):
            # Rebuild spatial grid each iteration (points move)
            grid = defaultdict(list)
            for idx, (px, py) in enumerate(points):
                gx = int(px / cell_size)
                gy = int(py / cell_size)
                grid[(gx, gy)].append(idx)

            for i in range(len(points)):
                px, py = points[i]
                gx = int(px / cell_size)
                gy = int(py / cell_size)
                min_d = float("inf")
                closest = -1
                # Search 3×3 neighborhood
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j in grid.get((gx + dx, gy + dy), ()):
                            if j == i:
                                continue
                            d = euclidean(points[i], points[j])
                            if d < min_d:
                                min_d = d
                                closest = j
                if min_d < half_target and closest >= 0:
                    ddx = px - points[closest][0]
                    ddy = py - points[closest][1]
                    dist = max(math.sqrt(ddx * ddx + ddy * ddy), 1e-9)
                    push = (half_target - min_d) * 0.3
                    nx = px + ddx / dist * push
                    ny = py + ddy / dist * push
                    points[i] = (max(0, min(1, nx)), max(0, min(1, ny)))

    return points


def _denormalize(pts, bbox, aspect_ratio):
    """Scale points from [0,1]^2 back to original coordinate space."""
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    return [(x * w + x0, y * h + y0) for x, y in pts]


def dream(source, novelty=0.3, seed=None):
    """Generate a dreamed point distribution from source data.

    Args:
        source: filename or list of (x,y) tuples
        novelty: 0.0 = faithful reproduction, 1.0 = wild variation
        seed: random seed for reproducibility

    Returns:
        list of (x, y) tuples
    """
    if isinstance(source, str):
        pts = load_points(source)
    else:
        pts = list(source)
    if len(pts) < 2:
        return pts

    rng = random.Random(seed)
    personality = extract_personality(pts)
    dream_pts = _sample_from_personality(personality, novelty, rng)
    return _denormalize(dream_pts, personality["bbox"], personality["aspect_ratio"])


def dream_series(source, count=5, novelty_range=(0.1, 0.9), seed=None):
    """Generate a series of dreams with increasing novelty."""
    results = []
    for i in range(count):
        if count > 1:
            t = i / (count - 1)
            nov = novelty_range[0] + t * (novelty_range[1] - novelty_range[0])
        else:
            nov = (novelty_range[0] + novelty_range[1]) / 2
        s = seed + i if seed is not None else None
        pts = dream(source, novelty=nov, seed=s)
        results.append({"novelty": round(nov, 3), "points": pts})
    return results


def interpolate_dreams(source_a, source_b, steps=4, seed=None):
    """Blend between two datasets' spatial personalities."""
    pts_a = load_points(source_a) if isinstance(source_a, str) else list(source_a)
    pts_b = load_points(source_b) if isinstance(source_b, str) else list(source_b)

    pers_a = extract_personality(pts_a)
    pers_b = extract_personality(pts_b)

    results = []
    for i in range(steps):
        t = i / max(steps - 1, 1)
        rng = random.Random(seed + i if seed is not None else None)

        # Interpolate cluster structures
        blended_clusters = []
        n_clust = max(len(pers_a["clusters"]), len(pers_b["clusters"]))
        for ci in range(n_clust):
            ca = pers_a["clusters"][ci % len(pers_a["clusters"])]
            cb = pers_b["clusters"][ci % len(pers_b["clusters"])]
            cx = ca["center"][0] * (1 - t) + cb["center"][0] * t
            cy = ca["center"][1] * (1 - t) + cb["center"][1] * t
            sp = ca["spread"] * (1 - t) + cb["spread"] * t
            wt = ca["weight"] * (1 - t) + cb["weight"] * t
            blended_clusters.append({"center": (cx, cy), "spread": sp, "weight": wt})

        # Interpolate density grid
        ga = pers_a["density_grid"]
        gb = pers_b["density_grid"]
        gs = len(ga)
        blended_density = [
            [ga[r][c] * (1 - t) + gb[r][c] * t for c in range(gs)]
            for r in range(gs)
        ]

        n_pts = int(pers_a["n_points"] * (1 - t) + pers_b["n_points"] * t)
        nn_mean = pers_a["nn_stats"]["mean"] * (1 - t) + pers_b["nn_stats"]["mean"] * t

        blended_pers = {
            "n_points": n_pts,
            "clusters": blended_clusters,
            "density_grid": blended_density,
            "nn_stats": {"mean": nn_mean, "std": 0, "skew": 0},
            "angular_profile": pers_a["angular_profile"],
            "bbox": pers_a["bbox"],
            "aspect_ratio": pers_a["aspect_ratio"],
        }

        dream_pts = _sample_from_personality(blended_pers, 0.2, rng)
        bbox_a = pers_a["bbox"]
        bbox_b = pers_b["bbox"]
        blended_bbox = tuple(
            bbox_a[i] * (1 - t) + bbox_b[i] * t for i in range(4)
        )
        pts = _denormalize(dream_pts, blended_bbox, blended_pers["aspect_ratio"])
        results.append({"blend": round(t, 3), "points": pts})

    return results


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _generate_html(original_pts, dreams, title="Spatial Dreamer Report"):
    """Generate interactive HTML report with SVG visualizations."""
    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0a0f;color:#e0e0e0;padding:2rem}}
h1{{text-align:center;font-size:2rem;margin-bottom:.5rem;background:linear-gradient(90deg,#a78bfa,#6dd5ed);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.subtitle{{text-align:center;color:#888;margin-bottom:2rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1.5rem;max-width:1400px;margin:0 auto}}
.card{{background:#14141f;border-radius:12px;padding:1.5rem;border:1px solid #252540}}
.card h3{{color:#a78bfa;margin-bottom:.5rem;font-size:1rem}}
.card .meta{{color:#888;font-size:.85rem;margin-bottom:1rem}}
svg{{width:100%;border-radius:8px;background:#0d0d18}}
circle{{transition:r .2s}}
circle:hover{{r:4;fill:#6dd5ed !important}}
.legend{{display:flex;gap:1rem;justify-content:center;margin:1.5rem 0;flex-wrap:wrap}}
.legend span{{display:flex;align-items:center;gap:.4rem;font-size:.85rem}}
.legend .dot{{width:10px;height:10px;border-radius:50%;display:inline-block}}
.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:.5rem;margin-top:.5rem}}
.stat{{background:#1a1a2e;padding:.5rem;border-radius:6px;text-align:center}}
.stat .val{{font-size:1.1rem;color:#a78bfa;font-weight:700}}
.stat .lbl{{font-size:.75rem;color:#888}}
</style></head><body>
<h1>🌙 Spatial Dreamer</h1>
<p class="subtitle">Autonomous dream generation from spatial DNA</p>
<div class="legend">
  <span><span class="dot" style="background:#6dd5ed"></span> Original</span>
  <span><span class="dot" style="background:#a78bfa"></span> Dreamed</span>
</div>
<div class="grid">
"""

    def _svg(pts, color, bbox):
        x0, y0, x1, y1 = bbox
        w, h = max(x1 - x0, 1), max(y1 - y0, 1)
        pad = 10
        vw, vh = 300, 300
        svg = f'<svg viewBox="0 0 {vw} {vh}" xmlns="http://www.w3.org/2000/svg">'
        for x, y in pts:
            sx = pad + (x - x0) / w * (vw - 2 * pad)
            sy = pad + (y - y0) / h * (vh - 2 * pad)
            svg += f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="2.5" fill="{color}" opacity="0.7"/>'
        svg += '</svg>'
        return svg

    bbox = _bounding_box(original_pts) if original_pts else (0, 0, 100, 100)
    # Expand bbox for all dreams
    all_pts = list(original_pts)
    for d in dreams:
        all_pts.extend(d.get("points", []))
    if all_pts:
        bbox = _bounding_box(all_pts)

    html += f'<div class="card"><h3>Original</h3>'
    html += f'<div class="meta">{len(original_pts)} points</div>'
    html += _svg(original_pts, "#6dd5ed", bbox)
    html += '</div>'

    for i, d in enumerate(dreams):
        pts = d.get("points", [])
        label = d.get("novelty", d.get("blend", "?"))
        html += f'<div class="card"><h3>Dream #{i+1}</h3>'
        html += f'<div class="meta">novelty/blend: {label} · {len(pts)} points</div>'
        html += _svg(pts, "#a78bfa", bbox)

        # Quick stats
        nn = _nn_stats(pts) if len(pts) > 1 else {"mean": 0, "std": 0}
        html += '<div class="stats">'
        html += f'<div class="stat"><div class="val">{len(pts)}</div><div class="lbl">Points</div></div>'
        html += f'<div class="stat"><div class="val">{nn["mean"]:.3f}</div><div class="lbl">NN Mean</div></div>'
        html += f'<div class="stat"><div class="val">{nn["std"]:.3f}</div><div class="lbl">NN Std</div></div>'
        html += '</div></div>'

    html += '</div></body></html>'
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Spatial Dreamer — generate synthetic distributions from learned patterns"
    )
    parser.add_argument("source", nargs="?", help="Input point file")
    parser.add_argument("--novelty", type=float, default=0.3, help="Novelty level 0-1 (default: 0.3)")
    parser.add_argument("--series", type=int, metavar="N", help="Generate N variations with increasing novelty")
    parser.add_argument("--interpolate", nargs=2, metavar=("A", "B"), help="Interpolate between two datasets")
    parser.add_argument("--steps", type=int, default=4, help="Interpolation steps (default: 4)")
    parser.add_argument("--hallucinate", action="store_true", help="Extreme creativity mode (novelty=0.95)")
    parser.add_argument("--seed", type=int, help="Random seed for reproducibility")
    parser.add_argument("--out", metavar="FILE", help="Save dreamed points to file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--html", nargs="?", const="dream_report.html", metavar="FILE", help="Generate interactive HTML report")
    args = parser.parse_args()

    if args.interpolate:
        results = interpolate_dreams(args.interpolate[0], args.interpolate[1],
                                     steps=args.steps, seed=args.seed)
        if args.html:
            pts_a = load_points(args.interpolate[0])
            html = _generate_html(pts_a, results, "Spatial Dreamer — Interpolation")
            with open(args.html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"HTML report: {args.html}")
        elif args.json:
            out = [{"blend": r["blend"], "points": r["points"]} for r in results]
            print(json.dumps(out, indent=2))
        else:
            for r in results:
                print(f"\n--- Blend: {r['blend']} ({len(r['points'])} points) ---")
                for x, y in r["points"][:10]:
                    print(f"  {x:.2f}, {y:.2f}")
                if len(r["points"]) > 10:
                    print(f"  ... and {len(r['points']) - 10} more")
        return

    if not args.source:
        parser.error("source file required (unless using --interpolate)")

    novelty = 0.95 if args.hallucinate else args.novelty

    if args.series:
        results = dream_series(args.source, count=args.series, seed=args.seed)
        if args.html:
            original = load_points(args.source)
            html = _generate_html(original, results, "Spatial Dreamer — Series")
            with open(args.html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"HTML report: {args.html}")
        elif args.json:
            print(json.dumps(results, indent=2))
        else:
            for r in results:
                print(f"\n--- Novelty: {r['novelty']} ({len(r['points'])} points) ---")
                for x, y in r["points"][:10]:
                    print(f"  {x:.2f}, {y:.2f}")
                if len(r["points"]) > 10:
                    print(f"  ... and {len(r['points']) - 10} more")
    else:
        pts = dream(args.source, novelty=novelty, seed=args.seed)
        if args.html:
            original = load_points(args.source)
            html = _generate_html(original, [{"novelty": novelty, "points": pts}],
                                  "Spatial Dreamer")
            with open(args.html, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"HTML report: {args.html}")
        elif args.json:
            print(json.dumps({"novelty": novelty, "points": pts}, indent=2))
        else:
            for x, y in pts:
                print(f"{x:.2f}, {y:.2f}")

        if args.out:
            with open(args.out, "w") as f:
                for x, y in pts:
                    f.write(f"{x:.4f} {y:.4f}\n")
            print(f"Saved {len(pts)} points to {args.out}")


if __name__ == "__main__":
    main()
