"""Spatial Distribution Fingerprinting — compact signatures for point patterns.

Compute a multi-dimensional "fingerprint" vector that captures the essential
spatial characteristics of a 2-D point distribution.  Fingerprints enable
rapid comparison and matching: two datasets with similar spatial structure
will have similar fingerprints regardless of translation, rotation, or scale.

Fingerprint dimensions (19 values):

- **NN statistics** — mean, std, skewness, kurtosis of nearest-neighbor distances
- **Quadrat VMR** — variance-to-mean ratio of quadrat counts
- **Hopkins statistic** — spatial randomness measure (0 = regular, 0.5 = CSR, 1 = clustered)
- **Clark-Evans R** — observed / expected NN distance (< 1 clustered, > 1 regular)
- **Angular entropy** — entropy of angles from centroid (12 sectors)
- **Ripley K deviation** — mean deviation from CSR at 5 radii
- **Hull efficiency** — convex hull area / bounding-box area
- **Lacunarity** — box-counting lacunarity at 3 scales
- **Fractal dimension** — box-counting dimension estimate

Auto-classification labels: Clustered, Regular, Random/CSR, Dispersed, Multi-scale.

Usage (CLI)::

    python vormap_fingerprint.py data.txt                   # print fingerprint
    python vormap_fingerprint.py data.txt --json             # JSON output
    python vormap_fingerprint.py data.txt --save fp.json     # save fingerprint
    python vormap_fingerprint.py --compare fp1.json fp2.json # similarity
    python vormap_fingerprint.py --match t.json lib/*.json   # best match
    python vormap_fingerprint.py --batch dir/ --html         # batch + matrix
    python vormap_fingerprint.py data.txt --html             # interactive report

Usage (Python API)::

    from vormap_fingerprint import fingerprint, compare, classify
    fp = fingerprint("data.txt")
    print(fp["vector"])
    print(classify(fp))
    sim = compare(fp1, fp2)
"""

from __future__ import annotations

import argparse
import glob
import html as _html
import json
import math
import os
import random
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from vormap_utils import euclidean as _dist

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_points(path: str) -> List[Tuple[float, float]]:
    """Read x,y points from text file (skip comments and blank lines)."""
    pts: List[Tuple[float, float]] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _nn_distances(pts: List[Tuple[float, float]]) -> List[float]:
    """Brute-force nearest-neighbor distances."""
    n = len(pts)
    dists = []
    for i in range(n):
        best = float("inf")
        for j in range(n):
            if i != j:
                d = _dist(pts[i], pts[j])
                if d < best:
                    best = d
        dists.append(best)
    return dists


def _mean(v):
    return sum(v) / len(v) if v else 0.0

def _var(v, m=None):
    if not v:
        return 0.0
    if m is None:
        m = _mean(v)
    return sum((x - m) ** 2 for x in v) / len(v)

def _std(v, m=None):
    return math.sqrt(_var(v, m))

def _skewness(v):
    m = _mean(v)
    s = _std(v, m)
    if s == 0 or len(v) < 3:
        return 0.0
    n = len(v)
    return sum(((x - m) / s) ** 3 for x in v) / n

def _kurtosis(v):
    m = _mean(v)
    s = _std(v, m)
    if s == 0 or len(v) < 4:
        return 0.0
    n = len(v)
    return sum(((x - m) / s) ** 4 for x in v) / n - 3.0


# ---------------------------------------------------------------------------
# Fingerprint dimensions
# ---------------------------------------------------------------------------

def _nn_stats(pts):
    """NN mean, std, skewness, kurtosis (normalized by bbox diagonal)."""
    if len(pts) < 2:
        return [0.0, 0.0, 0.0, 0.0]
    xmin, ymin, xmax, ymax = _bbox(pts)
    diag = math.hypot(xmax - xmin, ymax - ymin)
    if diag == 0:
        diag = 1.0
    dists = [d / diag for d in _nn_distances(pts)]
    return [_mean(dists), _std(dists), _skewness(dists), _kurtosis(dists)]


def _quadrat_vmr(pts, k=5):
    """Variance-to-mean ratio of quadrat counts (k x k grid)."""
    if len(pts) < 2:
        return 0.0
    xmin, ymin, xmax, ymax = _bbox(pts)
    dx = (xmax - xmin) or 1.0
    dy = (ymax - ymin) or 1.0
    counts = Counter()
    for x, y in pts:
        ci = min(int((x - xmin) / dx * k), k - 1)
        cj = min(int((y - ymin) / dy * k), k - 1)
        counts[(ci, cj)] += 1
    vals = [counts.get((i, j), 0) for i in range(k) for j in range(k)]
    m = _mean(vals)
    if m == 0:
        return 0.0
    return _var(vals, m) / m


def _hopkins(pts, m_samples=None):
    """Hopkins statistic for CSR testing."""
    n = len(pts)
    if n < 5:
        return 0.5
    if m_samples is None:
        m_samples = min(n // 3, 50)
    if m_samples < 1:
        m_samples = 1
    xmin, ymin, xmax, ymax = _bbox(pts)
    dx = (xmax - xmin) or 1.0
    dy = (ymax - ymin) or 1.0

    rng = random.Random(42)
    sample_idx = rng.sample(range(n), m_samples)

    # U: distances from random points to nearest data point
    u_sum = 0.0
    for _ in range(m_samples):
        rx = xmin + rng.random() * dx
        ry = ymin + rng.random() * dy
        best = float("inf")
        for p in pts:
            d = _dist((rx, ry), p)
            if d < best:
                best = d
        u_sum += best * best

    # W: distances from sample data points to nearest other data point
    w_sum = 0.0
    for idx in sample_idx:
        best = float("inf")
        for j, p in enumerate(pts):
            if j != idx:
                d = _dist(pts[idx], p)
                if d < best:
                    best = d
        w_sum += best * best

    denom = u_sum + w_sum
    if denom == 0:
        return 0.5
    return u_sum / denom


def _clark_evans(pts):
    """Clark-Evans R statistic."""
    n = len(pts)
    if n < 2:
        return 1.0
    xmin, ymin, xmax, ymax = _bbox(pts)
    area = max((xmax - xmin) * (ymax - ymin), 1e-12)
    nn = _nn_distances(pts)
    obs = _mean(nn)
    exp = 0.5 * math.sqrt(area / n)
    if exp == 0:
        return 1.0
    return obs / exp


def _angular_entropy(pts, n_sectors=12):
    """Entropy of angles from centroid."""
    n = len(pts)
    if n < 3:
        return 0.0
    cx = _mean([p[0] for p in pts])
    cy = _mean([p[1] for p in pts])
    counts = [0] * n_sectors
    for x, y in pts:
        a = math.atan2(y - cy, x - cx)
        idx = int((a + math.pi) / (2 * math.pi) * n_sectors) % n_sectors
        counts[idx] += 1
    total = sum(counts)
    if total == 0:
        return 0.0
    ent = 0.0
    for c in counts:
        if c > 0:
            p = c / total
            ent -= p * math.log(p)
    # Normalize by max entropy
    max_ent = math.log(n_sectors)
    return ent / max_ent if max_ent > 0 else 0.0


def _ripley_k_deviation(pts, n_radii=5):
    """Mean deviation of Ripley's K from CSR expectation at multiple radii."""
    n = len(pts)
    if n < 5:
        return 0.0
    xmin, ymin, xmax, ymax = _bbox(pts)
    area = max((xmax - xmin) * (ymax - ymin), 1e-12)
    diag = math.hypot(xmax - xmin, ymax - ymin)
    r_max = diag * 0.25
    radii = [r_max * (i + 1) / n_radii for i in range(n_radii)]

    devs = []
    for r in radii:
        count = 0
        for i in range(n):
            for j in range(i + 1, n):
                if _dist(pts[i], pts[j]) <= r:
                    count += 1
        k_obs = area * 2 * count / (n * (n - 1)) if n > 1 else 0
        k_csr = math.pi * r * r
        dev = (k_obs - k_csr) / k_csr if k_csr > 0 else 0
        devs.append(dev)
    return _mean([abs(d) for d in devs])


def _hull_efficiency(pts):
    """Convex hull area / bounding-box area."""
    n = len(pts)
    if n < 3:
        return 0.0
    xmin, ymin, xmax, ymax = _bbox(pts)
    bbox_area = (xmax - xmin) * (ymax - ymin)
    if bbox_area == 0:
        return 0.0
    # Graham scan
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    sorted_pts = sorted(set(pts))
    if len(sorted_pts) < 3:
        return 0.0
    lower = []
    for p in sorted_pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    # Shoelace area
    ha = 0.0
    hn = len(hull)
    for i in range(hn):
        j = (i + 1) % hn
        ha += hull[i][0] * hull[j][1]
        ha -= hull[j][0] * hull[i][1]
    ha = abs(ha) / 2.0
    return ha / bbox_area


def _lacunarity(pts, scales=(3, 5, 10)):
    """Box-counting lacunarity at multiple scales."""
    if len(pts) < 2:
        return [0.0] * len(scales)
    xmin, ymin, xmax, ymax = _bbox(pts)
    dx = (xmax - xmin) or 1.0
    dy = (ymax - ymin) or 1.0
    results = []
    for s in scales:
        counts = Counter()
        for x, y in pts:
            ci = min(int((x - xmin) / dx * s), s - 1)
            cj = min(int((y - ymin) / dy * s), s - 1)
            counts[(ci, cj)] += 1
        vals = [counts.get((i, j), 0) for i in range(s) for j in range(s)]
        m = _mean(vals)
        if m == 0:
            results.append(0.0)
        else:
            v = _var(vals, m)
            results.append((v / (m * m)) + 1.0)
    return results


def _fractal_dimension(pts, n_scales=8):
    """Box-counting fractal dimension estimate."""
    if len(pts) < 3:
        return 0.0
    xmin, ymin, xmax, ymax = _bbox(pts)
    dx = (xmax - xmin) or 1.0
    dy = (ymax - ymin) or 1.0
    sizes = []
    box_counts = []
    for k in range(1, n_scales + 1):
        s = 2 ** k
        occupied = set()
        for x, y in pts:
            ci = min(int((x - xmin) / dx * s), s - 1)
            cj = min(int((y - ymin) / dy * s), s - 1)
            occupied.add((ci, cj))
        if len(occupied) > 0:
            sizes.append(1.0 / s)
            box_counts.append(len(occupied))
    if len(sizes) < 2:
        return 0.0
    # Linear regression on log-log
    lx = [math.log(s) for s in sizes]
    ly = [math.log(c) for c in box_counts]
    mx = _mean(lx)
    my = _mean(ly)
    num = sum((lx[i] - mx) * (ly[i] - my) for i in range(len(lx)))
    den = sum((lx[i] - mx) ** 2 for i in range(len(lx)))
    if den == 0:
        return 0.0
    slope = num / den
    return -slope  # fractal dimension


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

DIMENSION_NAMES = [
    "nn_mean", "nn_std", "nn_skewness", "nn_kurtosis",
    "quadrat_vmr", "hopkins", "clark_evans", "angular_entropy",
    "ripley_k_dev", "hull_efficiency",
    "lacunarity_3", "lacunarity_5", "lacunarity_10",
    "fractal_dim",
]


def fingerprint(source, pts=None) -> Dict[str, Any]:
    """Compute spatial fingerprint for a point dataset.

    Parameters
    ----------
    source : str
        File path or descriptive label.
    pts : list, optional
        Pre-loaded points; if None, read from *source* file.

    Returns
    -------
    dict with keys: name, n_points, vector, dimensions, classification.
    """
    if pts is None:
        pts = _read_points(source)

    n = len(pts)
    if n < 3:
        vec = [0.0] * len(DIMENSION_NAMES)
        return {
            "name": os.path.basename(source),
            "n_points": n,
            "vector": vec,
            "dimensions": dict(zip(DIMENSION_NAMES, vec)),
            "classification": {"label": "Insufficient data", "confidence": 0},
        }

    # Subsample for expensive O(n²) operations
    sample = pts
    if n > 500:
        rng = random.Random(42)
        sample = rng.sample(pts, 500)

    nn = _nn_stats(sample)
    vmr = _quadrat_vmr(pts)
    hop = _hopkins(sample)
    ce = _clark_evans(sample)
    ae = _angular_entropy(pts)
    rk = _ripley_k_deviation(sample)
    he = _hull_efficiency(pts)
    lac = _lacunarity(pts)
    fd = _fractal_dimension(pts)

    vec = nn + [vmr, hop, ce, ae, rk, he] + lac + [fd]
    dims = dict(zip(DIMENSION_NAMES, vec))
    cls = classify_vector(dims)

    return {
        "name": os.path.basename(source) if isinstance(source, str) else "data",
        "n_points": n,
        "vector": vec,
        "dimensions": dims,
        "classification": cls,
    }


def classify_vector(dims: Dict[str, float]) -> Dict[str, Any]:
    """Auto-classify a fingerprint into pattern categories."""
    hop = dims.get("hopkins", 0.5)
    ce = dims.get("clark_evans", 1.0)
    vmr = dims.get("quadrat_vmr", 1.0)

    scores = {
        "Clustered": 0.0,
        "Regular": 0.0,
        "Random/CSR": 0.0,
        "Dispersed": 0.0,
        "Multi-scale": 0.0,
    }

    # Hopkins: >0.7 clustered, ~0.5 random, <0.3 regular
    if hop > 0.7:
        scores["Clustered"] += 3.0
    elif hop > 0.55:
        scores["Clustered"] += 1.0
    elif 0.4 <= hop <= 0.6:
        scores["Random/CSR"] += 2.0
    elif hop < 0.3:
        scores["Regular"] += 3.0
    else:
        scores["Regular"] += 1.0

    # Clark-Evans: <0.5 strongly clustered, ~1 random, >1.5 regular
    if ce < 0.5:
        scores["Clustered"] += 3.0
    elif ce < 0.8:
        scores["Clustered"] += 1.5
    elif 0.8 <= ce <= 1.2:
        scores["Random/CSR"] += 2.0
    elif ce > 1.5:
        scores["Regular"] += 3.0
        scores["Dispersed"] += 1.5
    else:
        scores["Dispersed"] += 1.0

    # VMR: ~1 random, >>1 clustered, <<1 regular
    if vmr > 3.0:
        scores["Clustered"] += 2.0
        scores["Multi-scale"] += 1.0
    elif vmr > 1.5:
        scores["Clustered"] += 1.0
    elif 0.7 <= vmr <= 1.3:
        scores["Random/CSR"] += 1.5
    elif vmr < 0.5:
        scores["Regular"] += 2.0

    # Lacunarity spread indicates multi-scale
    l3 = dims.get("lacunarity_3", 0)
    l10 = dims.get("lacunarity_10", 0)
    if l3 > 0 and l10 > 0 and abs(l3 - l10) / max(l3, 0.01) > 0.5:
        scores["Multi-scale"] += 2.0

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    conf = int(scores[best] / total * 100) if total > 0 else 0
    return {"label": best, "confidence": min(conf, 99)}


def compare(fp1: Dict, fp2: Dict) -> Dict[str, Any]:
    """Compare two fingerprints. Returns similarity 0-100 and per-dimension deltas."""
    v1 = fp1["vector"]
    v2 = fp2["vector"]
    n = min(len(v1), len(v2))

    # Cosine similarity
    dot = sum(v1[i] * v2[i] for i in range(n))
    mag1 = math.sqrt(sum(x * x for x in v1[:n]))
    mag2 = math.sqrt(sum(x * x for x in v2[:n]))
    cos_sim = dot / (mag1 * mag2) if mag1 > 0 and mag2 > 0 else 0.0

    # Weighted Euclidean (higher weight for more discriminating dims)
    weights = [2, 1, 0.5, 0.5, 2, 3, 2.5, 1, 2, 1, 1.5, 1.5, 1.5, 1]
    while len(weights) < n:
        weights.append(1.0)
    w_dist = math.sqrt(sum(weights[i] * (v1[i] - v2[i]) ** 2 for i in range(n)))
    # Normalize to 0-100 similarity
    eucl_sim = max(0, 100 - w_dist * 20)

    score = 0.6 * cos_sim * 100 + 0.4 * eucl_sim
    score = max(0.0, min(100.0, score))

    deltas = {}
    for i, name in enumerate(DIMENSION_NAMES[:n]):
        deltas[name] = round(v2[i] - v1[i], 4)

    return {
        "similarity": round(score, 1),
        "cosine": round(cos_sim, 4),
        "weighted_distance": round(w_dist, 4),
        "deltas": deltas,
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _radar_svg(fps: List[Dict], width=500, height=500) -> str:
    """Generate SVG radar chart for one or more fingerprints."""
    # Normalize dimensions for display
    labels = DIMENSION_NAMES
    n = len(labels)
    cx, cy, r = width // 2, height // 2, min(width, height) // 2 - 60
    angles = [2 * math.pi * i / n - math.pi / 2 for i in range(n)]

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'style="background:#1a1a2e;border-radius:12px">']

    # Grid rings
    for ring in range(1, 6):
        rr = r * ring / 5
        pts_str = " ".join(
            f"{cx + rr * math.cos(a)},{cy + rr * math.sin(a)}" for a in angles
        )
        svg.append(f'<polygon points="{pts_str}" fill="none" stroke="#333" stroke-width="1"/>')

    # Axis lines and labels
    for i, a in enumerate(angles):
        ex = cx + r * math.cos(a)
        ey = cy + r * math.sin(a)
        svg.append(f'<line x1="{cx}" y1="{cy}" x2="{ex}" y2="{ey}" stroke="#444" stroke-width="1"/>')
        lx = cx + (r + 30) * math.cos(a)
        ly = cy + (r + 30) * math.sin(a)
        anchor = "middle"
        if math.cos(a) > 0.3:
            anchor = "start"
        elif math.cos(a) < -0.3:
            anchor = "end"
        short = labels[i].replace("_", " ").replace("lacunarity ", "lac")
        svg.append(f'<text x="{lx}" y="{ly}" text-anchor="{anchor}" '
                   f'fill="#aaa" font-size="10" font-family="monospace">{short}</text>')

    colors = ["#00d2ff", "#ff6b6b", "#51cf66", "#ffd43b"]
    for fi, fp in enumerate(fps):
        vec = fp["vector"]
        # Normalize each value to [0, 1] range for display
        # Use reasonable max values per dimension
        maxvals = [0.5, 0.3, 3, 5, 10, 1, 2.5, 1, 2, 1, 5, 5, 5, 3]
        while len(maxvals) < len(vec):
            maxvals.append(5)
        norm = [min(abs(vec[i]) / max(maxvals[i], 0.01), 1.0) for i in range(n)]

        pts_str = " ".join(
            f"{cx + r * norm[i] * math.cos(angles[i])},{cy + r * norm[i] * math.sin(angles[i])}"
            for i in range(n)
        )
        color = colors[fi % len(colors)]
        svg.append(f'<polygon points="{pts_str}" fill="{color}" fill-opacity="0.15" '
                   f'stroke="{color}" stroke-width="2"/>')
        # Dots
        for i in range(n):
            dx = cx + r * norm[i] * math.cos(angles[i])
            dy = cy + r * norm[i] * math.sin(angles[i])
            svg.append(f'<circle cx="{dx}" cy="{dy}" r="3" fill="{color}"/>')

    # Legend
    for fi, fp in enumerate(fps):
        color = colors[fi % len(colors)]
        y = 20 + fi * 18
        svg.append(f'<rect x="10" y="{y}" width="12" height="12" fill="{color}" rx="2"/>')
        svg.append(f'<text x="28" y="{y + 10}" fill="#ccc" font-size="11" '
                   f'font-family="monospace">{_html.escape(fp["name"])}</text>')

    svg.append("</svg>")
    return "\n".join(svg)


def _heatmap_svg(names, matrix, width=None) -> str:
    """Generate SVG similarity heatmap for batch comparison."""
    n = len(names)
    cell = 50
    margin = 120
    if width is None:
        width = margin + n * cell + 20
    height = margin + n * cell + 20

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'style="background:#1a1a2e;border-radius:12px">']

    for i in range(n):
        for j in range(n):
            v = matrix[i][j]
            # Green for similar, red for different
            if v >= 70:
                r_c = int(50 * (100 - v) / 30)
                g_c = int(180 + 75 * (v - 70) / 30)
                b_c = 50
            elif v >= 40:
                r_c = int(50 + 180 * (70 - v) / 30)
                g_c = int(180 * (v - 40) / 30)
                b_c = 50
            else:
                r_c = 230
                g_c = int(50 + 50 * v / 40)
                b_c = 50
            color = f"rgb({r_c},{g_c},{b_c})"
            x = margin + j * cell
            y = margin + i * cell
            svg.append(f'<rect x="{x}" y="{y}" width="{cell - 2}" height="{cell - 2}" '
                       f'fill="{color}" rx="4"/>')
            svg.append(f'<text x="{x + cell // 2}" y="{y + cell // 2 + 4}" '
                       f'text-anchor="middle" fill="white" font-size="10" '
                       f'font-family="monospace">{v:.0f}</text>')

    # Labels
    for i, name in enumerate(names):
        short = name[:12]
        # Row labels
        svg.append(f'<text x="{margin - 5}" y="{margin + i * cell + cell // 2 + 4}" '
                   f'text-anchor="end" fill="#aaa" font-size="10" font-family="monospace">{_html.escape(short)}</text>')
        # Col labels (rotated)
        x = margin + i * cell + cell // 2
        y = margin - 5
        svg.append(f'<text x="{x}" y="{y}" text-anchor="end" fill="#aaa" font-size="10" '
                   f'font-family="monospace" transform="rotate(-45 {x} {y})">{_html.escape(short)}</text>')

    svg.append("</svg>")
    return "\n".join(svg)


def generate_html(fps: List[Dict], comparisons=None, matrix=None) -> str:
    """Generate interactive HTML report."""
    title = "Spatial Fingerprint Report"
    if len(fps) == 1:
        title = f"Fingerprint — {_html.escape(fps[0]['name'])}"

    radar = _radar_svg(fps)
    heatmap = ""
    if matrix and len(fps) > 1:
        names = [fp["name"] for fp in fps]
        heatmap = _heatmap_svg(names, matrix)

    cards = ""
    for fp in fps:
        cls = fp["classification"]
        dims_html = "".join(
            f"<tr><td style='padding:4px 12px;color:#aaa'>{k}</td>"
            f"<td style='padding:4px 12px;color:#fff;text-align:right'>{v:.4f}</td></tr>"
            for k, v in fp["dimensions"].items()
        )
        cards += f"""
        <div style="background:#16213e;border-radius:12px;padding:20px;margin:10px;
                    display:inline-block;vertical-align:top;min-width:280px">
            <h3 style="color:#00d2ff;margin:0 0 8px">{_html.escape(fp['name'])}</h3>
            <p style="color:#888;margin:4px 0">{fp['n_points']} points</p>
            <p style="margin:8px 0">
                <span style="background:#0f3460;color:#e94560;padding:4px 12px;border-radius:20px;
                             font-weight:bold">{cls['label']}</span>
                <span style="color:#666;margin-left:8px">{cls['confidence']}% confidence</span>
            </p>
            <table style="margin-top:12px;border-collapse:collapse">{dims_html}</table>
        </div>"""

    comp_html = ""
    if comparisons:
        for c in comparisons:
            comp_html += f"""
            <div style="background:#16213e;border-radius:12px;padding:16px;margin:10px">
                <span style="font-size:2em;font-weight:bold;color:#00d2ff">{c['similarity']:.1f}</span>
                <span style="color:#888;font-size:1.2em"> / 100 similarity</span>
                <span style="color:#555;margin-left:12px">cosine={c['cosine']:.3f}</span>
            </div>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ background:#0f0f23; color:#ccc; font-family:'Segoe UI',system-ui,sans-serif;
         margin:0; padding:20px }}
  h1 {{ color:#00d2ff; text-align:center }}
  h2 {{ color:#e94560; margin-top:30px }}
  .center {{ text-align:center }}
</style></head><body>
<h1>🔬 {title}</h1>
<div class="center">{radar}</div>
<h2>Fingerprint Details</h2>
<div>{cards}</div>
{"<h2>Similarity</h2>" + comp_html if comp_html else ""}
{"<h2>Similarity Matrix</h2><div class='center'>" + heatmap + "</div>" if heatmap else ""}
</body></html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Spatial Distribution Fingerprinting — compact signatures for point patterns",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python vormap_fingerprint.py data.txt
  python vormap_fingerprint.py data.txt --json
  python vormap_fingerprint.py data.txt --html
  python vormap_fingerprint.py data.txt --save fp.json
  python vormap_fingerprint.py --compare fp1.json fp2.json
  python vormap_fingerprint.py --match target.json library/*.json
  python vormap_fingerprint.py --batch dir/ --html""",
    )
    parser.add_argument("input", nargs="?", help="Point data file or directory (for --batch)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--html", action="store_true", help="Interactive HTML report")
    parser.add_argument("--save", metavar="PATH", help="Save fingerprint to JSON file")
    parser.add_argument("--compare", nargs=2, metavar="FP", help="Compare two fingerprint JSONs")
    parser.add_argument("--match", nargs="+", metavar="FP", help="Match target against library")
    parser.add_argument("--batch", action="store_true", help="Batch mode: fingerprint all files in dir")
    parser.add_argument("-o", "--output", metavar="PATH", help="Output file for HTML")

    args = parser.parse_args()

    # --- Compare mode ---
    if args.compare:
        with open(args.compare[0]) as f:
            fp1 = json.load(f)
        with open(args.compare[1]) as f:
            fp2 = json.load(f)
        result = compare(fp1, fp2)
        if args.html:
            html = generate_html([fp1, fp2], comparisons=[result])
            out = args.output or "fingerprint_compare.html"
            with open(out, "w") as f:
                f.write(html)
            print(f"Report saved to {out}")
        elif args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Similarity: {result['similarity']:.1f}/100")
            print(f"  Cosine:   {result['cosine']:.4f}")
            print(f"  Distance: {result['weighted_distance']:.4f}")
        return

    # --- Match mode ---
    if args.match:
        target_path = args.match[0]
        lib_paths = args.match[1:]
        with open(target_path) as f:
            target = json.load(f)
        best_score = -1
        best_name = ""
        results = []
        for lp in lib_paths:
            for path in glob.glob(lp):
                with open(path) as f:
                    lib_fp = json.load(f)
                r = compare(target, lib_fp)
                r["file"] = path
                r["name"] = lib_fp.get("name", os.path.basename(path))
                results.append(r)
                if r["similarity"] > best_score:
                    best_score = r["similarity"]
                    best_name = r["name"]
        results.sort(key=lambda x: -x["similarity"])
        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(f"Best match: {best_name} ({best_score:.1f}/100)")
            for r in results[:10]:
                print(f"  {r['similarity']:5.1f}  {r['name']}")
        return

    # --- Batch mode ---
    if args.batch:
        dir_path = args.input or "."
        files = glob.glob(os.path.join(dir_path, "*.txt"))
        if not files:
            print(f"No .txt files found in {dir_path}")
            sys.exit(1)
        fps = []
        for f in sorted(files):
            print(f"Fingerprinting {os.path.basename(f)}...", file=sys.stderr)
            fps.append(fingerprint(f))
        # Similarity matrix
        n = len(fps)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(n):
                if i == j:
                    matrix[i][j] = 100.0
                elif j > i:
                    c = compare(fps[i], fps[j])
                    matrix[i][j] = c["similarity"]
                    matrix[j][i] = c["similarity"]
        if args.html:
            html = generate_html(fps, matrix=matrix)
            out = args.output or "fingerprint_batch.html"
            with open(out, "w") as fh:
                fh.write(html)
            print(f"Report saved to {out}")
        elif args.json:
            print(json.dumps({"fingerprints": fps, "matrix": matrix}, indent=2))
        else:
            for fp in fps:
                cls = fp["classification"]
                print(f"{fp['name']:30s}  {cls['label']:15s}  ({cls['confidence']}%)")
        return

    # --- Single file mode ---
    if not args.input:
        parser.print_help()
        sys.exit(1)

    fp = fingerprint(args.input)

    if args.save:
        with open(args.save, "w") as f:
            json.dump(fp, f, indent=2)
        print(f"Fingerprint saved to {args.save}")

    if args.html:
        html = generate_html([fp])
        out = args.output or "fingerprint_report.html"
        with open(out, "w") as f:
            f.write(html)
        print(f"Report saved to {out}")
    elif args.json:
        print(json.dumps(fp, indent=2))
    else:
        cls = fp["classification"]
        print(f"Fingerprint for {fp['name']} ({fp['n_points']} points)")
        print(f"Classification: {cls['label']} ({cls['confidence']}% confidence)")
        print(f"\nDimensions:")
        for k, v in fp["dimensions"].items():
            print(f"  {k:20s}  {v:+.4f}")


if __name__ == "__main__":
    main()
