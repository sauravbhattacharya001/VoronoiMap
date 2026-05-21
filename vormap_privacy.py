"""Voronoi Spatial Privacy Engine — differential privacy & k-anonymity for point data.

Protects individual locations in spatial datasets while preserving
aggregate statistical properties.  Useful for publishing GPS traces,
medical facility locations, crime data, or any sensitive point pattern.

Techniques:

- **Laplace noise** — calibrated eps-differential privacy via Laplace mechanism
- **k-anonymity** — greedy NN clustering; each cluster has >= k points
- **Grid snapping** — discretize to grid cells, collapse duplicates
- **Donut masking** — displace within [r_min, r_max] annulus
- **Privacy optimizer** — auto-search best params for a utility target
- **Privacy audit** — compare original vs anonymized, grade re-id risk

Python API::

    from vormap_privacy import (laplace_noise, k_anonymize, grid_snap,
                                 donut_mask, optimize_privacy, audit_privacy)

    private = laplace_noise(points, epsilon=0.5)
    private = k_anonymize(points, k=5)
    private = grid_snap(points, cell_size=10)
    private = donut_mask(points, r_min=5, r_max=20)
    params, pts = optimize_privacy(points, method="laplace", max_distortion=15.0)
    report = audit_privacy(original, anonymized)
    print(report.privacy_grade, report.re_id_risk)

CLI::

    python vormap_privacy.py laplace data.txt --epsilon 0.5 -o private.txt
    python vormap_privacy.py k-anon data.txt --k 5 -o private.txt
    python vormap_privacy.py grid data.txt --cell-size 10 -o private.txt
    python vormap_privacy.py donut data.txt --rmin 5 --rmax 20 -o private.txt
    python vormap_privacy.py optimize data.txt --method laplace --max-distortion 15
    python vormap_privacy.py audit original.txt anonymized.txt --html report.html
"""

import json
import math
import random
import statistics
from collections import namedtuple

from vormap_utils import bounding_box as _bounding_box, euclidean as _dist, polygon_centroid_mean as _centroid

# ---------------------------------------------------------------------------
# Data helpers

def _load_points(path):
    """Load whitespace-separated x y points, one per line."""
    pts = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _save_points(pts, path):
    """Write points as x y lines."""
    with open(path, "w") as fh:
        for x, y in pts:
            fh.write(f"{x:.6f} {y:.6f}\n")


def _nn_distances(pts):
    """Compute nearest-neighbor distance for every point (brute force)."""
    dists = []
    n = len(pts)
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i != j:
                d = _dist(pts[i], pts[j])
                if d < best:
                    best = d
        dists.append(best if best < float("inf") else 0.0)
    return dists


def _hopkins(pts, bbox, m=None):
    """Hopkins statistic — 0.5 = uniform random, >0.7 = clustered."""
    n = len(pts)
    if n < 4:
        return 0.5
    if m is None:
        m = min(n, 30)
    min_x, min_y, max_x, max_y = bbox
    w, h = max_x - min_x, max_y - min_y
    if w == 0 or h == 0:
        return 0.5

    sample_idx = random.sample(range(n), m)
    u_sum = 0.0
    w_sum = 0.0
    for idx in sample_idx:
        # u_i: distance from random point to nearest data point
        rx = random.uniform(min_x, max_x)
        ry = random.uniform(min_y, max_y)
        u_min = min(_dist((rx, ry), pts[j]) for j in range(n))
        u_sum += u_min * u_min
        # w_i: distance from sample point to nearest other data point
        w_min = float("inf")
        for j in range(n):
            if j != idx:
                d = _dist(pts[idx], pts[j])
                if d < w_min:
                    w_min = d
        w_sum += w_min * w_min

    denom = u_sum + w_sum
    return u_sum / denom if denom > 0 else 0.5


# ---------------------------------------------------------------------------
# Privacy techniques

def laplace_noise(points, epsilon=1.0, seed=None):
    """Add Laplace noise calibrated to eps-differential privacy.

    Sensitivity is estimated from the bounding box diagonal.  Each
    coordinate gets independent Laplace(0, sensitivity/eps) noise.
    """
    if not points:
        return []
    if seed is not None:
        random.seed(seed)
    bbox = _bounding_box(points)
    diag = math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])
    sensitivity = diag / max(len(points), 1)
    scale = sensitivity / max(epsilon, 1e-12)

    def _lap():
        u = random.random() - 0.5
        return -scale * (1 if u >= 0 else -1) * math.log(1 - 2 * abs(u) + 1e-15)

    return [(x + _lap(), y + _lap()) for x, y in points]


def k_anonymize(points, k=5):
    """Greedy spatial k-anonymity: cluster into groups of >= k, return centroids."""
    if not points or k < 1:
        return list(points)
    remaining = list(range(len(points)))
    clusters = []

    while len(remaining) >= k:
        # pick the point with the smallest NN distance as seed
        seed = remaining[0]
        best_nn = float("inf")
        for i in remaining:
            for j in remaining:
                if i != j:
                    d = _dist(points[i], points[j])
                    if d < best_nn:
                        best_nn = d
                        seed = i
            if best_nn == 0:
                break

        # gather k-1 nearest neighbors of seed from remaining
        dists = [(j, _dist(points[seed], points[j])) for j in remaining if j != seed]
        dists.sort(key=lambda x: x[1])
        cluster = [seed] + [d[0] for d in dists[: k - 1]]
        clusters.append(cluster)
        remain_set = set(remaining) - set(cluster)
        remaining = [r for r in remaining if r in remain_set]

    # leftover points: merge into nearest existing cluster
    if remaining and clusters:
        for idx in remaining:
            best_c = 0
            best_d = float("inf")
            for ci, cl in enumerate(clusters):
                c = _centroid([points[i] for i in cl])
                d = _dist(points[idx], c)
                if d < best_d:
                    best_d = d
                    best_c = ci
            clusters[best_c].append(idx)

    return [_centroid([points[i] for i in cl]) for cl in clusters]


def grid_snap(points, cell_size=10.0):
    """Snap points to grid cells and deduplicate."""
    if not points or cell_size <= 0:
        return list(points)
    seen = set()
    result = []
    for x, y in points:
        gx = round(x / cell_size) * cell_size
        gy = round(y / cell_size) * cell_size
        key = (gx, gy)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def donut_mask(points, r_min=5.0, r_max=20.0, seed=None):
    """Displace each point by random distance in [r_min, r_max], random angle."""
    if not points:
        return []
    if seed is not None:
        random.seed(seed)
    result = []
    for x, y in points:
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(r_min, r_max)
        result.append((x + dist * math.cos(angle), y + dist * math.sin(angle)))
    return result


# ---------------------------------------------------------------------------
# Privacy optimizer (agentic auto-search)

_OptResult = namedtuple("OptResult", ["method", "best_param", "distortion", "points"])


def _mean_displacement(orig, priv):
    """Mean displacement between matched point pairs."""
    if len(orig) != len(priv):
        # for k-anon / grid the count changes — use NN matching
        return _mean_displacement_nn(orig, priv)
    total = sum(_dist(a, b) for a, b in zip(orig, priv))
    return total / max(len(orig), 1)


def _mean_displacement_nn(orig, priv):
    """Mean nearest-neighbor displacement when counts differ."""
    if not orig or not priv:
        return 0.0
    total = 0.0
    for p in orig:
        best = min(_dist(p, q) for q in priv)
        total += best
    return total / len(orig)


def optimize_privacy(points, method="laplace", max_distortion=15.0, seed=None):
    """Autonomous binary search for optimal privacy parameter.

    Finds the strongest privacy (smallest eps, largest k, largest cell)
    that keeps mean displacement <= max_distortion.

    Returns ``_OptResult(method, best_param, distortion, points)``.
    """
    if seed is not None:
        random.seed(seed)

    def _trial(param):
        if method == "laplace":
            priv = laplace_noise(points, epsilon=param, seed=42)
        elif method == "k-anon":
            priv = k_anonymize(points, k=int(param))
        elif method == "grid":
            priv = grid_snap(points, cell_size=param)
        elif method == "donut":
            priv = donut_mask(points, r_min=param * 0.25, r_max=param, seed=42)
        else:
            raise ValueError(f"Unknown method: {method}")
        return priv, _mean_displacement(points, priv)

    # Binary search bounds — direction depends on method
    if method == "laplace":
        # lower eps = more privacy; search for smallest eps with distortion <= target
        lo, hi = 0.01, 100.0
        for _ in range(30):
            mid = (lo + hi) / 2
            _, d = _trial(mid)
            if d <= max_distortion:
                hi = mid  # can go more private
            else:
                lo = mid
        best = hi
        priv, dist = _trial(best)
        return _OptResult(method, round(best, 4), round(dist, 2), priv)

    elif method == "k-anon":
        lo, hi = 2, len(points)
        best_k, best_priv, best_dist = 2, list(points), 0.0
        for _ in range(20):
            mid = (lo + hi) // 2
            if mid < 2:
                mid = 2
            priv, d = _trial(mid)
            if d <= max_distortion:
                best_k, best_priv, best_dist = mid, priv, d
                lo = mid + 1
            else:
                hi = mid - 1
            if lo > hi:
                break
        return _OptResult(method, best_k, round(best_dist, 2), best_priv)

    elif method == "grid":
        lo, hi = 0.1, 500.0
        for _ in range(30):
            mid = (lo + hi) / 2
            _, d = _trial(mid)
            if d <= max_distortion:
                lo = mid
            else:
                hi = mid
        best = lo
        priv, dist = _trial(best)
        return _OptResult(method, round(best, 2), round(dist, 2), priv)

    elif method == "donut":
        lo, hi = 0.1, 500.0
        for _ in range(30):
            mid = (lo + hi) / 2
            _, d = _trial(mid)
            if d <= max_distortion:
                lo = mid
            else:
                hi = mid
        best = lo
        priv, dist = _trial(best)
        return _OptResult(method, round(best, 2), round(dist, 2), priv)

    raise ValueError(f"Unknown method: {method}")


# ---------------------------------------------------------------------------
# Privacy audit

AuditReport = namedtuple(
    "AuditReport",
    [
        "mean_displacement",
        "max_displacement",
        "nn_dist_change",
        "hopkins_original",
        "hopkins_anonymized",
        "re_id_risk",
        "privacy_grade",
        "point_count_original",
        "point_count_anonymized",
    ],
)


def audit_privacy(original, anonymized, threshold=None):
    """Audit anonymized dataset against original.

    ``threshold`` for re-identification is auto-set to median NN distance
    of the original if not provided.
    """
    bbox_o = _bounding_box(original) if original else (0, 0, 1, 1)
    bbox_a = _bounding_box(anonymized) if anonymized else (0, 0, 1, 1)
    bbox = (
        min(bbox_o[0], bbox_a[0]),
        min(bbox_o[1], bbox_a[1]),
        max(bbox_o[2], bbox_a[2]),
        max(bbox_o[3], bbox_a[3]),
    )

    # displacements (NN-based when counts differ)
    displacements = []
    for p in original:
        if anonymized:
            best = min(_dist(p, q) for q in anonymized)
        else:
            best = 0.0
        displacements.append(best)

    mean_disp = statistics.mean(displacements) if displacements else 0.0
    max_disp = max(displacements) if displacements else 0.0

    # NN distance distributions
    nn_orig = _nn_distances(original) if len(original) > 1 else [0.0]
    nn_anon = _nn_distances(anonymized) if len(anonymized) > 1 else [0.0]
    nn_change = abs(statistics.mean(nn_anon) - statistics.mean(nn_orig))

    # Hopkins
    hop_o = _hopkins(original, bbox)
    hop_a = _hopkins(anonymized, bbox)

    # Re-identification risk
    if threshold is None:
        threshold = statistics.median(nn_orig) if nn_orig else 1.0
    re_id = 0
    for p in original:
        if anonymized and min(_dist(p, q) for q in anonymized) <= threshold:
            re_id += 1
    risk = re_id / max(len(original), 1)

    # Grade
    if risk <= 0.05:
        grade = "A"
    elif risk <= 0.15:
        grade = "B"
    elif risk <= 0.30:
        grade = "C"
    elif risk <= 0.50:
        grade = "D"
    else:
        grade = "F"

    return AuditReport(
        mean_displacement=round(mean_disp, 4),
        max_displacement=round(max_disp, 4),
        nn_dist_change=round(nn_change, 4),
        hopkins_original=round(hop_o, 4),
        hopkins_anonymized=round(hop_a, 4),
        re_id_risk=round(risk, 4),
        privacy_grade=grade,
        point_count_original=len(original),
        point_count_anonymized=len(anonymized),
    )


# ---------------------------------------------------------------------------
# HTML report

def _audit_html(report, original, anonymized):
    """Generate self-contained HTML audit report."""
    bbox = (0, 0, 100, 100)
    all_pts = original + anonymized
    if all_pts:
        bbox = _bounding_box(all_pts)
    min_x, min_y, max_x, max_y = bbox
    w = max(max_x - min_x, 1)
    h = max(max_y - min_y, 1)
    pad = 20
    svg_w, svg_h = 400, 400
    sx = (svg_w - 2 * pad) / w
    sy = (svg_h - 2 * pad) / h
    s = min(sx, sy)

    def tx(x):
        return pad + (x - min_x) * s

    def ty(y):
        return svg_h - pad - (y - min_y) * s

    # Original SVG
    orig_dots = "".join(
        f'<circle cx="{tx(x):.1f}" cy="{ty(y):.1f}" r="3" fill="#2563eb" opacity="0.7"/>'
        for x, y in original
    )

    # Anonymized SVG
    anon_dots = "".join(
        f'<circle cx="{tx(x):.1f}" cy="{ty(y):.1f}" r="3" fill="#dc2626" opacity="0.7"/>'
        for x, y in anonymized
    )

    # Displacement arrows (sample up to 200)
    arrows = ""
    sample = original if len(original) <= 200 else random.sample(original, 200)
    for p in sample:
        if anonymized:
            best_q = min(anonymized, key=lambda q: _dist(p, q))
            d = _dist(p, best_q)
            if d > 0:
                arrows += (
                    f'<line x1="{tx(p[0]):.1f}" y1="{ty(p[1]):.1f}" '
                    f'x2="{tx(best_q[0]):.1f}" y2="{ty(best_q[1]):.1f}" '
                    f'stroke="#9333ea" stroke-width="1" opacity="0.3"/>'
                )

    # Risk gauge color
    risk_pct = report.re_id_risk * 100
    if risk_pct <= 15:
        gauge_color = "#22c55e"
    elif risk_pct <= 40:
        gauge_color = "#eab308"
    else:
        gauge_color = "#ef4444"

    grade_colors = {"A": "#22c55e", "B": "#84cc16", "C": "#eab308", "D": "#f97316", "F": "#ef4444"}
    gc = grade_colors.get(report.privacy_grade, "#6b7280")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Spatial Privacy Audit Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}}
h1{{font-size:1.8rem;margin-bottom:8px}}
.subtitle{{color:#94a3b8;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
.card{{background:#1e293b;border-radius:12px;padding:20px}}
.card h2{{font-size:1rem;color:#94a3b8;margin-bottom:12px}}
svg{{width:100%;height:auto;background:#0f172a;border-radius:8px}}
table{{width:100%;border-collapse:collapse}}
td,th{{padding:8px 12px;text-align:left;border-bottom:1px solid #334155}}
th{{color:#94a3b8;font-weight:500}}
.grade{{font-size:4rem;font-weight:700;text-align:center;line-height:1}}
.gauge-bar{{height:20px;border-radius:10px;background:#334155;margin-top:8px;overflow:hidden}}
.gauge-fill{{height:100%;border-radius:10px;transition:width .5s}}
.metric{{font-size:1.5rem;font-weight:600}}
</style>
</head>
<body>
<h1>🔒 Spatial Privacy Audit</h1>
<p class="subtitle">Original: {report.point_count_original} pts -> Anonymized: {report.point_count_anonymized} pts</p>

<div class="grid">
  <div class="card">
    <h2>Original Dataset</h2>
    <svg viewBox="0 0 {svg_w} {svg_h}">{orig_dots}</svg>
  </div>
  <div class="card">
    <h2>Anonymized Dataset</h2>
    <svg viewBox="0 0 {svg_w} {svg_h}">{anon_dots}</svg>
  </div>
</div>

<div class="grid">
  <div class="card">
    <h2>Displacement Vectors</h2>
    <svg viewBox="0 0 {svg_w} {svg_h}">
      {arrows}
      {orig_dots}
    </svg>
  </div>
  <div class="card" style="text-align:center">
    <h2>Privacy Grade</h2>
    <div class="grade" style="color:{gc}">{report.privacy_grade}</div>
    <p style="margin-top:8px;color:#94a3b8">Re-identification Risk</p>
    <div class="metric" style="color:{gauge_color}">{risk_pct:.1f}%</div>
    <div class="gauge-bar">
      <div class="gauge-fill" style="width:{min(risk_pct, 100):.0f}%;background:{gauge_color}"></div>
    </div>
  </div>
</div>

<div class="card">
  <h2>Metrics</h2>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Mean Displacement</td><td class="metric">{report.mean_displacement:.4f}</td></tr>
    <tr><td>Max Displacement</td><td class="metric">{report.max_displacement:.4f}</td></tr>
    <tr><td>NN Distance Change</td><td class="metric">{report.nn_dist_change:.4f}</td></tr>
    <tr><td>Hopkins (Original)</td><td>{report.hopkins_original:.4f}</td></tr>
    <tr><td>Hopkins (Anonymized)</td><td>{report.hopkins_anonymized:.4f}</td></tr>
    <tr><td>Re-identification Risk</td><td style="color:{gauge_color}">{risk_pct:.1f}%</td></tr>
    <tr><td>Privacy Grade</td><td style="color:{gc};font-weight:700">{report.privacy_grade}</td></tr>
  </table>
</div>

<p style="margin-top:16px;color:#475569;text-align:center;font-size:0.8rem">
  Generated by VoronoiMap vormap_privacy · Spatial Privacy Engine
</p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI

def _cli():
    import argparse

    parser = argparse.ArgumentParser(
        prog="vormap_privacy",
        description="Spatial Privacy Engine — differential privacy & k-anonymity for point data",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- laplace
    p_lap = sub.add_parser("laplace", help="Add Laplace noise (eps-differential privacy)")
    p_lap.add_argument("input", help="Input point file")
    p_lap.add_argument("--epsilon", type=float, default=1.0, help="Privacy budget eps (default 1.0)")
    p_lap.add_argument("-o", "--output", help="Output point file")
    p_lap.add_argument("--seed", type=int, help="Random seed")

    # -- k-anon
    p_ka = sub.add_parser("k-anon", help="Spatial k-anonymity via clustering")
    p_ka.add_argument("input", help="Input point file")
    p_ka.add_argument("--k", type=int, default=5, help="Minimum cluster size (default 5)")
    p_ka.add_argument("-o", "--output", help="Output point file")

    # -- grid
    p_gr = sub.add_parser("grid", help="Grid snapping with deduplication")
    p_gr.add_argument("input", help="Input point file")
    p_gr.add_argument("--cell-size", type=float, default=10.0, help="Grid cell size (default 10)")
    p_gr.add_argument("-o", "--output", help="Output point file")

    # -- donut
    p_dn = sub.add_parser("donut", help="Donut masking (annulus displacement)")
    p_dn.add_argument("input", help="Input point file")
    p_dn.add_argument("--rmin", type=float, default=5.0, help="Minimum displacement radius")
    p_dn.add_argument("--rmax", type=float, default=20.0, help="Maximum displacement radius")
    p_dn.add_argument("-o", "--output", help="Output point file")
    p_dn.add_argument("--seed", type=int, help="Random seed")

    # -- optimize
    p_opt = sub.add_parser("optimize", help="Auto-search optimal privacy parameters")
    p_opt.add_argument("input", help="Input point file")
    p_opt.add_argument("--method", choices=["laplace", "k-anon", "grid", "donut"], default="laplace")
    p_opt.add_argument("--max-distortion", type=float, default=15.0, help="Max mean displacement")
    p_opt.add_argument("-o", "--output", help="Output anonymized point file")
    p_opt.add_argument("--json", dest="json_out", help="Save results as JSON")
    p_opt.add_argument("--seed", type=int, help="Random seed")

    # -- audit
    p_au = sub.add_parser("audit", help="Audit anonymized dataset against original")
    p_au.add_argument("original", help="Original point file")
    p_au.add_argument("anonymized", help="Anonymized point file")
    p_au.add_argument("--threshold", type=float, help="Re-id distance threshold")
    p_au.add_argument("--json", dest="json_out", help="Save audit as JSON")
    p_au.add_argument("--html", dest="html_out", help="Save audit as HTML report")

    args = parser.parse_args()

    if args.command == "laplace":
        pts = _load_points(args.input)
        result = laplace_noise(pts, epsilon=args.epsilon, seed=args.seed)
        print(f"[OK] Applied Laplace noise (eps={args.epsilon}) to {len(pts)} points")
        print(f"  Mean displacement: {_mean_displacement(pts, result):.4f}")
        if args.output:
            _save_points(result, args.output)
            print(f"  Saved to {args.output}")

    elif args.command == "k-anon":
        pts = _load_points(args.input)
        result = k_anonymize(pts, k=args.k)
        print(f"[OK] k-anonymized {len(pts)} points -> {len(result)} clusters (k={args.k})")
        print(f"  Mean displacement: {_mean_displacement(pts, result):.4f}")
        if args.output:
            _save_points(result, args.output)
            print(f"  Saved to {args.output}")

    elif args.command == "grid":
        pts = _load_points(args.input)
        result = grid_snap(pts, cell_size=args.cell_size)
        print(f"[OK] Grid-snapped {len(pts)} points -> {len(result)} cells (size={args.cell_size})")
        if args.output:
            _save_points(result, args.output)
            print(f"  Saved to {args.output}")

    elif args.command == "donut":
        pts = _load_points(args.input)
        result = donut_mask(pts, r_min=args.rmin, r_max=args.rmax, seed=args.seed)
        print(f"[OK] Donut-masked {len(pts)} points (r=[{args.rmin}, {args.rmax}])")
        print(f"  Mean displacement: {_mean_displacement(pts, result):.4f}")
        if args.output:
            _save_points(result, args.output)
            print(f"  Saved to {args.output}")

    elif args.command == "optimize":
        pts = _load_points(args.input)
        print(f"[>>] Searching optimal {args.method} params (max distortion={args.max_distortion})...")
        res = optimize_privacy(pts, method=args.method, max_distortion=args.max_distortion, seed=args.seed)
        print(f"[OK] Best {res.method} param: {res.best_param}  (distortion: {res.distortion})")
        if args.output:
            _save_points(res.points, args.output)
            print(f"  Saved {len(res.points)} points to {args.output}")
        if args.json_out:
            data = {"method": res.method, "best_param": res.best_param, "distortion": res.distortion}
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"  JSON saved to {args.json_out}")

    elif args.command == "audit":
        orig = _load_points(args.original)
        anon = _load_points(args.anonymized)
        print(f"[>>] Auditing {len(orig)} original vs {len(anon)} anonymized points...")
        report = audit_privacy(orig, anon, threshold=args.threshold)
        print(f"  Privacy Grade: {report.privacy_grade}")
        print(f"  Re-identification Risk: {report.re_id_risk * 100:.1f}%")
        print(f"  Mean Displacement: {report.mean_displacement:.4f}")
        print(f"  Max Displacement: {report.max_displacement:.4f}")
        print(f"  NN Distance Change: {report.nn_dist_change:.4f}")
        print(f"  Hopkins Original: {report.hopkins_original:.4f}")
        print(f"  Hopkins Anonymized: {report.hopkins_anonymized:.4f}")
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(report._asdict(), f, indent=2)
            print(f"  JSON saved to {args.json_out}")
        if args.html_out:
            html = _audit_html(report, orig, anon)
            with open(args.html_out, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"  HTML report saved to {args.html_out}")


if __name__ == "__main__":
    _cli()
