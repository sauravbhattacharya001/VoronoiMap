"""Autonomous Spatial Narrative Generator for Voronoi point patterns.

Reads a dataset, runs multiple spatial analyses, and weaves the findings
into a coherent, human-readable *story* about the spatial structure —
written in prose, not bullet points.  Think of it as a data journalist
for your point patterns.

The narrative covers:
- Overall impression (density, spread, scale)
- Clustering behavior (Hopkins statistic)
- Regularity / lattice tendencies (Clark-Evans R)
- Outlier landscape (IQR on nearest-neighbor distances)
- Directional bias (bounding-box aspect ratio, point cloud PCA)
- Hotspot summary (quadrant density analysis)
- Voronoi cell statistics (area distribution, largest/smallest cells)
- Proactive recommendations (what to explore next)

Outputs plain text, Markdown, or an interactive HTML report with the
narrative alongside a point-pattern mini-map.

Usage (CLI)::

    python vormap_narrative.py datauni5.txt
    python vormap_narrative.py datauni5.txt --format markdown
    python vormap_narrative.py datauni5.txt --html report.html
    python vormap_narrative.py datauni5.txt --json

Usage (Python API)::

    from vormap_narrative import narrate
    story = narrate("datauni5.txt")
    print(story["text"])
    print(story["sections"])
"""

import html as _html
import io
import json
import math
import random
import sys

import vormap
from vormap_utils import bounding_box, compute_nn_distances

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from vormap_utils import load_points as _load_points


def _hopkins(points, bbox, m=None):
    """Hopkins clustering statistic (0-1). Values near 1 = clustered."""
    n = len(points)
    if n < 5:
        return 0.5
    if m is None:
        m = min(max(10, n // 10), 100)
    xmin, ymin, xmax, ymax = bbox
    w, h = xmax - xmin, ymax - ymin
    if w == 0 or h == 0:
        return 0.5

    rng = random.Random(42)
    sample = rng.sample(points, min(m, n))

    def _nearest(pt, pts):
        return min(math.hypot(pt[0] - p[0], pt[1] - p[1]) for p in pts if p != pt)

    u_sum = 0.0
    w_sum = 0.0
    for s in sample:
        rand_pt = (rng.uniform(xmin, xmax), rng.uniform(ymin, ymax))
        u_sum += _nearest(rand_pt, points) ** 2
        w_sum += _nearest(s, points) ** 2

    denom = u_sum + w_sum
    if denom == 0:
        return 0.5
    return u_sum / denom


def _clark_evans(points, bbox):
    """Clark-Evans R ratio. R<1 = clustered, R>1 = regular, R≈1 = random."""
    n = len(points)
    if n < 3:
        return 1.0
    nn_dists = compute_nn_distances(points)
    mean_nn = sum(nn_dists) / len(nn_dists)
    xmin, ymin, xmax, ymax = bbox
    area = (xmax - xmin) * (ymax - ymin)
    if area == 0:
        return 1.0
    density = n / area
    expected_nn = 0.5 / math.sqrt(density)
    if expected_nn == 0:
        return 1.0
    return mean_nn / expected_nn


def _quadrant_densities(points, bbox):
    """Return densities for 4 quadrants (NW, NE, SW, SE)."""
    xmin, ymin, xmax, ymax = bbox
    mx = (xmin + xmax) / 2
    my = (ymin + ymax) / 2
    quads = {"NW": 0, "NE": 0, "SW": 0, "SE": 0}
    for x, y in points:
        if x <= mx:
            if y >= my:
                quads["NW"] += 1
            else:
                quads["SW"] += 1
        else:
            if y >= my:
                quads["NE"] += 1
            else:
                quads["SE"] += 1
    qw = (xmax - xmin) / 2
    qh = (ymax - ymin) / 2
    qa = qw * qh if qw * qh > 0 else 1
    return {k: v / qa for k, v in quads.items()}


def _cell_area_stats(points, bbox):
    """Compute Voronoi cell area statistics via vormap."""
    try:
        from vormap_geometry import polygon_area
        xmin, ymin, xmax, ymax = bbox
        # Use vormap core to compute Voronoi
        old_s, old_n, old_w, old_e = vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E
        vormap.IND_S, vormap.IND_N = ymin, ymax
        vormap.IND_W, vormap.IND_E = xmin, xmax
        try:
            cells = vormap.voronoi(points)
        finally:
            vormap.IND_S, vormap.IND_N = old_s, old_n
            vormap.IND_W, vormap.IND_E = old_w, old_e

        areas = []
        for cell in cells:
            if cell and len(cell) >= 3:
                a = abs(polygon_area(cell))
                if a > 0:
                    areas.append(a)
        if not areas:
            return None
        areas.sort()
        return {
            "count": len(areas),
            "min": areas[0],
            "max": areas[-1],
            "mean": sum(areas) / len(areas),
            "median": areas[len(areas) // 2],
            "ratio": areas[-1] / areas[0] if areas[0] > 0 else float("inf"),
            "cv": (sum((a - sum(areas)/len(areas))**2 for a in areas) / len(areas))**0.5 / (sum(areas)/len(areas)) if sum(areas) > 0 else 0,
        }
    except Exception:
        return None


def _pca_direction(points):
    """Principal axis direction (degrees from horizontal) via covariance."""
    n = len(points)
    if n < 3:
        return None
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    cxx = sum((p[0] - mx)**2 for p in points) / n
    cyy = sum((p[1] - my)**2 for p in points) / n
    cxy = sum((p[0] - mx) * (p[1] - my) for p in points) / n
    # Eigenvalue of 2x2 symmetric matrix
    trace = cxx + cyy
    det = cxx * cyy - cxy * cxy
    disc = max(0, trace * trace / 4 - det)
    l1 = trace / 2 + math.sqrt(disc)
    l2 = trace / 2 - math.sqrt(disc)
    if l1 + l2 == 0:
        return None
    elongation = l1 / (l1 + l2)  # 0.5 = isotropic, 1.0 = fully elongated
    # Principal direction
    if abs(cxy) > 1e-12:
        angle = math.degrees(math.atan2(l1 - cxx, cxy))
    else:
        angle = 0 if cxx >= cyy else 90
    return {"angle": angle % 180, "elongation": elongation}


# ---------------------------------------------------------------------------
# Narrative composer
# ---------------------------------------------------------------------------

def _describe_density(n, bbox):
    """Prose about overall density."""
    xmin, ymin, xmax, ymax = bbox
    area = (xmax - xmin) * (ymax - ymin)
    density = n / area if area > 0 else 0
    w, h = xmax - xmin, ymax - ymin

    if n < 20:
        size_desc = "a sparse handful"
    elif n < 100:
        size_desc = "a modest collection"
    elif n < 500:
        size_desc = "a substantial set"
    elif n < 2000:
        size_desc = "a dense crowd"
    else:
        size_desc = "a massive swarm"

    return (
        f"The dataset contains {n} points — {size_desc} spread across a "
        f"{w:.1f} × {h:.1f} region (area ≈ {area:,.0f} sq units). "
        f"That works out to roughly {density:.4f} points per square unit."
    )


def _describe_clustering(hopkins, ce_r):
    """Prose about clustering behavior."""
    parts = []
    if hopkins > 0.75:
        parts.append(
            f"The Hopkins statistic ({hopkins:.3f}) is high, suggesting strong "
            f"clustering — these points clearly prefer each other's company."
        )
    elif hopkins > 0.55:
        parts.append(
            f"The Hopkins statistic ({hopkins:.3f}) shows mild clustering — "
            f"there's some spatial structure here, but it's not dramatic."
        )
    else:
        parts.append(
            f"The Hopkins statistic ({hopkins:.3f}) is near 0.5, indicating "
            f"the points are close to spatially random. No obvious clustering."
        )

    if ce_r < 0.7:
        parts.append(
            f"The Clark-Evans R ratio ({ce_r:.3f}) confirms tight clustering: "
            f"nearest-neighbor distances are much shorter than expected."
        )
    elif ce_r < 1.0:
        parts.append(
            f"The Clark-Evans R ratio ({ce_r:.3f}) leans toward clustering, "
            f"though it's not extreme."
        )
    elif ce_r < 1.3:
        parts.append(
            f"The Clark-Evans R ratio ({ce_r:.3f}) is near 1.0 — consistent "
            f"with random placement."
        )
    else:
        parts.append(
            f"Interestingly, the Clark-Evans R ratio ({ce_r:.3f}) exceeds 1.0, "
            f"indicating regular spacing — the points seem to *avoid* each other, "
            f"like trees in a managed forest."
        )
    return " ".join(parts)


def _describe_outliers(nn_dists):
    """Prose about spatial outliers."""
    if not nn_dists:
        return ""
    nn = sorted(nn_dists)
    q1 = nn[len(nn) // 4]
    q3 = nn[3 * len(nn) // 4]
    iqr = q3 - q1
    threshold = q3 + 1.5 * iqr
    outlier_count = sum(1 for d in nn if d > threshold)
    pct = 100 * outlier_count / len(nn) if nn else 0

    if outlier_count == 0:
        return (
            "No spatial outliers detected — every point has reasonably close "
            "neighbors. The distribution is well-connected."
        )
    elif pct < 5:
        return (
            f"There are {outlier_count} spatial outliers ({pct:.1f}% of points) "
            f"sitting unusually far from their neighbors. These lonely points "
            f"might be noise, or they might be genuinely interesting anomalies."
        )
    else:
        return (
            f"A notable {outlier_count} points ({pct:.1f}%) qualify as spatial "
            f"outliers. That's a lot of isolated points — this dataset may have "
            f"significant gaps or multiple distinct clusters with empty space between."
        )


def _describe_direction(pca):
    """Prose about directional bias."""
    if pca is None:
        return ""
    elong = pca["elongation"]
    angle = pca["angle"]
    if elong < 0.6:
        return (
            "The point cloud is fairly isotropic — no strong directional preference. "
            "It's roughly circular in its spread."
        )
    elif elong < 0.75:
        return (
            f"There's a mild elongation at about {angle:.0f}° from horizontal "
            f"(elongation index: {elong:.2f}). The points stretch slightly in "
            f"one direction, but it's subtle."
        )
    else:
        return (
            f"The point cloud is notably elongated at {angle:.0f}° "
            f"(elongation index: {elong:.2f}). This strong directional bias "
            f"suggests an underlying linear structure — perhaps a road, river, "
            f"or geological feature driving point placement."
        )


def _describe_hotspots(quad_densities):
    """Prose about spatial hotspots from quadrant analysis."""
    if not quad_densities:
        return ""
    vals = list(quad_densities.values())
    mean_d = sum(vals) / len(vals) if vals else 1
    if mean_d == 0:
        return ""

    hot = max(quad_densities, key=quad_densities.get)
    cold = min(quad_densities, key=quad_densities.get)
    ratio = quad_densities[hot] / quad_densities[cold] if quad_densities[cold] > 0 else 999

    if ratio < 1.5:
        return (
            "Point density is fairly uniform across quadrants — no dramatic "
            "hotspots or cold spots."
        )
    elif ratio < 3:
        return (
            f"The {hot} quadrant is the densest (about {ratio:.1f}× the {cold} "
            f"quadrant). There's a mild concentration but nothing extreme."
        )
    else:
        return (
            f"There's a clear hotspot in the {hot} quadrant — it's {ratio:.1f}× "
            f"denser than the {cold} quadrant. Whatever is driving point placement "
            f"has a strong spatial preference."
        )


def _describe_cells(stats):
    """Prose about Voronoi cell area distribution."""
    if stats is None:
        return ""
    ratio = stats["ratio"]
    cv = stats["cv"]
    if ratio < 5:
        return (
            f"The Voronoi cells are fairly uniform in size (area ratio: "
            f"{ratio:.1f}×, CV: {cv:.2f}). Each point commands roughly "
            f"equal territory."
        )
    elif ratio < 50:
        return (
            f"Cell areas span a {ratio:.1f}× range (smallest to largest), with "
            f"a coefficient of variation of {cv:.2f}. Some points have much more "
            f"elbow room than others — typical of mild clustering."
        )
    else:
        return (
            f"Cell areas vary enormously — a {ratio:.0f}× range! (CV: {cv:.2f}). "
            f"The largest cells belong to isolated outlier points, while the "
            f"smallest cells are packed in dense clusters."
        )


def _build_recommendations(hopkins, ce_r, pca, outlier_pct, cell_stats):
    """Proactive next-step recommendations."""
    recs = []
    if hopkins > 0.6:
        recs.append("Run `vormap_cluster` to identify and characterize the clusters.")
        recs.append("Try `vormap_hotspot` to map density peaks.")
    if ce_r > 1.2:
        recs.append("Use `vormap_regularity` to quantify the lattice-like structure.")
    if pca and pca["elongation"] > 0.7:
        recs.append("Explore `vormap_trend` to model the directional pattern.")
    if outlier_pct > 3:
        recs.append("Run `vormap_outlier` to flag and investigate isolated points.")
    if cell_stats and cell_stats["cv"] > 0.8:
        recs.append("Try `vormap_sentinel` to monitor distribution health over time.")
    if not recs:
        recs.append("The data looks well-behaved! Try `vormap_gallery` for visual exploration.")
        recs.append("Run `vormap_recommend` for a full tool recommendation scan.")
    return recs


# ---------------------------------------------------------------------------
# Main narrate function
# ---------------------------------------------------------------------------

def narrate(path):
    """Analyze a point dataset and produce a spatial narrative.

    Parameters
    ----------
    path : str
        Path to a whitespace-delimited point file.

    Returns
    -------
    dict
        Keys: ``text`` (full narrative string), ``sections`` (list of dicts
        with ``title`` and ``body``), ``stats`` (raw numeric findings),
        ``recommendations`` (list of strings).
    """
    points = _load_points(path)
    if len(points) < 3:
        return {
            "text": "Not enough points to analyze (need at least 3).",
            "sections": [],
            "stats": {},
            "recommendations": [],
        }

    bbox = bounding_box(points)
    nn_dists = compute_nn_distances(points)
    hopkins = _hopkins(points, bbox)
    ce_r = _clark_evans(points, bbox)
    pca = _pca_direction(points)
    quad_dens = _quadrant_densities(points, bbox)
    cell_stats = _cell_area_stats(points, bbox)

    outlier_nn = sorted(nn_dists)
    q1 = outlier_nn[len(outlier_nn) // 4]
    q3 = outlier_nn[3 * len(outlier_nn) // 4]
    iqr = q3 - q1
    outlier_count = sum(1 for d in outlier_nn if d > q3 + 1.5 * iqr)
    outlier_pct = 100 * outlier_count / len(outlier_nn) if outlier_nn else 0

    recs = _build_recommendations(hopkins, ce_r, pca, outlier_pct, cell_stats)

    sections = [
        {"title": "The Big Picture", "body": _describe_density(len(points), bbox)},
        {"title": "Clustering & Structure", "body": _describe_clustering(hopkins, ce_r)},
        {"title": "Outlier Landscape", "body": _describe_outliers(nn_dists)},
        {"title": "Directional Character", "body": _describe_direction(pca)},
        {"title": "Hotspot Geography", "body": _describe_hotspots(quad_dens)},
    ]
    if cell_stats:
        sections.append({"title": "Voronoi Cell Anatomy", "body": _describe_cells(cell_stats)})

    sections.append({
        "title": "What to Explore Next",
        "body": "Based on this analysis, here are suggested next steps:\n" +
                "\n".join(f"  • {r}" for r in recs),
    })

    full_text = ""
    for sec in sections:
        if sec["body"]:
            full_text += f"## {sec['title']}\n\n{sec['body']}\n\n"

    stats = {
        "n_points": len(points),
        "bbox": list(bbox),
        "hopkins": round(hopkins, 4),
        "clark_evans_r": round(ce_r, 4),
        "pca": pca,
        "outlier_count": outlier_count,
        "outlier_pct": round(outlier_pct, 2),
        "quadrant_densities": {k: round(v, 6) for k, v in quad_dens.items()},
        "cell_stats": {k: round(v, 4) if isinstance(v, float) else v
                       for k, v in (cell_stats or {}).items()},
    }

    return {
        "text": full_text.strip(),
        "sections": sections,
        "stats": stats,
        "recommendations": recs,
    }


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _generate_html(result, path):
    """Generate an interactive HTML report."""
    sections_html = ""
    for sec in result["sections"]:
        body = _html.escape(sec["body"]).replace("\n", "<br>") if sec["body"] else ""
        sections_html += f'<div class="section"><h2>{_html.escape(sec["title"])}</h2><p>{body}</p></div>\n'

    stats = result["stats"]
    pts_json = json.dumps(stats.get("bbox", [0, 0, 1000, 2000]))
    safe_path = _html.escape(str(path))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spatial Narrative — {safe_path}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:Georgia,'Times New Roman',serif;background:#faf9f6;color:#2c2c2c;max-width:800px;margin:0 auto;padding:2rem 1.5rem;line-height:1.7}}
h1{{font-size:1.8rem;margin-bottom:.3rem;color:#1a1a2e}}
.subtitle{{color:#666;font-style:italic;margin-bottom:2rem;font-size:.95rem}}
.section{{margin-bottom:1.8rem;padding:1.2rem 1.5rem;background:#fff;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.section h2{{font-size:1.15rem;color:#16213e;margin-bottom:.6rem;border-bottom:2px solid #e8e8e8;padding-bottom:.3rem}}
.section p{{font-size:.95rem}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.8rem;margin:1.5rem 0}}
.stat-card{{background:#fff;padding:.8rem;border-radius:6px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.stat-card .val{{font-size:1.4rem;font-weight:bold;color:#0f3460}}
.stat-card .lbl{{font-size:.75rem;color:#888;margin-top:.2rem}}
footer{{margin-top:2rem;text-align:center;color:#aaa;font-size:.8rem}}
</style>
</head>
<body>
<h1>📖 Spatial Narrative</h1>
<p class="subtitle">Autonomous analysis of <strong>{safe_path}</strong></p>

<div class="stats-grid">
  <div class="stat-card"><div class="val">{stats.get('n_points',0):,}</div><div class="lbl">Points</div></div>
  <div class="stat-card"><div class="val">{stats.get('hopkins',0):.3f}</div><div class="lbl">Hopkins H</div></div>
  <div class="stat-card"><div class="val">{stats.get('clark_evans_r',0):.3f}</div><div class="lbl">Clark-Evans R</div></div>
  <div class="stat-card"><div class="val">{stats.get('outlier_count',0)}</div><div class="lbl">Outliers</div></div>
</div>

{sections_html}

<footer>Generated by vormap_narrative — VoronoiMap Spatial Narrative Generator</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Autonomous Spatial Narrative Generator — turns point data into prose."
    )
    parser.add_argument("datafile", help="Path to point data file")
    parser.add_argument("--format", choices=["text", "markdown", "json"], default="markdown",
                        help="Output format (default: markdown)")
    parser.add_argument("--html", metavar="FILE", help="Write interactive HTML report")
    parser.add_argument("--json", action="store_true", help="Output JSON (shorthand for --format json)")
    args = parser.parse_args()

    if args.json:
        args.format = "json"

    result = narrate(args.datafile)

    if args.html:
        html = _generate_html(result, args.datafile)
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML report written to {args.html}")

    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "text":
        # Strip markdown headers for plain text
        print(result["text"].replace("## ", "").replace("# ", ""))
    else:
        print(result["text"])


if __name__ == "__main__":
    main()
