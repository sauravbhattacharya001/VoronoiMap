"""Voronoi Diagram Diagnostician — autonomous health checker & auto-fix prescriber.

Examines a point dataset, runs 12 diagnostic checks, identifies problems,
and prescribes specific fix commands using other VoronoiMap tools.  This is
the "doctor" for your spatial data — it tells you what's wrong and how to
fix it before you even ask.

Checks performed:

- **Degeneracy** — collinear/cocircular points that cause Voronoi issues
- **Duplicates** — exact or near-duplicate points (within epsilon)
- **Density Uniformity** — grid-based density, flags extreme cells
- **Boundary Crowding** — too many points near bounding box edges
- **Isolation** — orphan points whose NN distance > 3σ
- **Aspect Ratio** — extreme bounding box shapes
- **Scale** — coordinate magnitudes risking float precision loss
- **Minimum Spacing** — closest pairs too small relative to extent
- **Sample Size** — too few or very many points
- **Cluster Imbalance** — severely unbalanced spatial clusters
- **Hull Efficiency** — convex hull area vs bounding box area
- **NN Distribution** — nearest-neighbor distance anomalies

Usage (Python API)::

    from vormap_doctor import diagnose
    d = diagnose("datafile.txt")
    print(d.health_score)
    for f in d.findings:
        print(f"[{f.severity}] {f.check}: {f.message}")
    if d.auto_fix_plan:
        for cmd in d.auto_fix_plan:
            print(f"  $ {cmd}")

CLI::

    python vormap_doctor.py datafile.txt
    python vormap_doctor.py datafile.txt --json report.json
    python vormap_doctor.py datafile.txt --html report.html
    python vormap_doctor.py datafile.txt --auto-fix
    python vormap_doctor.py datafile.txt --strict
"""

import html as _html
import json
import math
from collections import namedtuple

from vormap_utils import bounding_box as _bounding_box, euclidean as _dist

# ---------------------------------------------------------------------------
# Data structures

Finding = namedtuple("Finding", ["check", "severity", "message", "fix_command", "details"])


class Diagnosis:
    """Complete diagnostic result for a point dataset."""

    def __init__(self, dataset_path, point_count, findings):
        self.dataset_path = dataset_path
        self.point_count = point_count
        self.findings = findings
        self.health_score = self._compute_score()
        self.auto_fix_plan = [f.fix_command for f in findings if f.fix_command]
        self.summary = self._make_summary()

    def _compute_score(self):
        score = 100
        for f in self.findings:
            if f.severity == "info":
                score -= 5
            elif f.severity == "warning":
                score -= 15
            elif f.severity == "critical":
                score -= 30
        return max(0, score)

    def _make_summary(self):
        crits = sum(1 for f in self.findings if f.severity == "critical")
        warns = sum(1 for f in self.findings if f.severity == "warning")
        if crits:
            return f"Health {self.health_score}/100 — {crits} critical, {warns} warnings. Immediate fixes needed."
        elif warns:
            return f"Health {self.health_score}/100 — {warns} warnings. Review recommended."
        else:
            return f"Health {self.health_score}/100 — Dataset looks good."

    def to_json(self, path=None):
        data = {
            "dataset": self.dataset_path,
            "point_count": self.point_count,
            "health_score": self.health_score,
            "summary": self.summary,
            "findings": [
                {"check": f.check, "severity": f.severity, "message": f.message,
                 "fix_command": f.fix_command, "details": f.details}
                for f in self.findings
            ],
            "auto_fix_plan": self.auto_fix_plan,
        }
        text = json.dumps(data, indent=2)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    def to_html(self, path):
        """Render the diagnosis as a self-contained HTML report.

        All user-controlled fields (``dataset_path``, finding messages,
        check names, fix commands, summary) are HTML-escaped before
        interpolation. This prevents reflected HTML/JS injection when
        the report is opened in a browser — dataset paths and filenames
        come from CLI input, so without escaping a crafted path such as
        ``"<script>alert(1)</script>.txt"`` would execute in the viewer.
        """
        sev_colors = {"ok": "#4caf50", "info": "#2196f3", "warning": "#ff9800", "critical": "#f44336"}
        esc = _html.escape
        findings_html = ""
        for f in self.findings:
            color = sev_colors.get(f.severity, "#666")
            fix = f"<code>{esc(f.fix_command)}</code>" if f.fix_command else "—"
            findings_html += f"""<tr>
                <td style="color:{color};font-weight:bold">{esc(f.severity).upper()}</td>
                <td>{esc(f.check)}</td><td>{esc(f.message)}</td><td>{fix}</td></tr>\n"""
        fix_plan = "\n".join(f"$ {esc(c)}" for c in self.auto_fix_plan) if self.auto_fix_plan else "No fixes needed."
        gauge_color = "#4caf50" if self.health_score >= 70 else "#ff9800" if self.health_score >= 40 else "#f44336"
        safe_dataset = esc(str(self.dataset_path))
        safe_summary = esc(self.summary)
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>VoronoiMap Doctor Report</title>
<style>
body{{font-family:system-ui;background:#1a1a2e;color:#eee;margin:2em auto;max-width:900px;padding:0 1em}}
h1{{color:#e94560}}h2{{color:#0f3460;color:#7ec8e3}}
table{{width:100%;border-collapse:collapse;margin:1em 0}}
td,th{{padding:8px 10px;border-bottom:1px solid #333;text-align:left}}
th{{background:#16213e}}
.gauge{{width:200px;height:200px;border-radius:50%;border:12px solid {gauge_color};
display:flex;align-items:center;justify-content:center;font-size:3em;font-weight:bold;margin:1em auto}}
pre{{background:#16213e;padding:1em;border-radius:8px;overflow-x:auto}}
</style></head><body>
<h1>&#x1F9E9; VoronoiMap Doctor Report</h1>
<p><strong>Dataset:</strong> {safe_dataset} ({self.point_count} points)</p>
<div class="gauge">{self.health_score}</div>
<p style="text-align:center">{safe_summary}</p>
<h2>Findings</h2>
<table><tr><th>Severity</th><th>Check</th><th>Message</th><th>Fix</th></tr>
{findings_html}</table>
<h2>Auto-Fix Plan</h2><pre>{fix_plan}</pre>
</body></html>"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)


# ---------------------------------------------------------------------------
# Point loading

from vormap_utils import load_points as _load_points


# ---------------------------------------------------------------------------
# Helper math

def _mean(vals):
    return sum(vals) / len(vals) if vals else 0


def _stddev(vals):
    if len(vals) < 2:
        return 0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


# _bounding_box is now imported from vormap_utils (single-pass, memory-efficient)


def _nn_distances(points):
    """Nearest-neighbor distances for every point.

    Delegates to :func:`vormap_utils.compute_nn_distances`, which picks the
    scipy ``KDTree`` (O(n log n)) fast path when available and falls back to
    a vectorised squared-distance brute force otherwise. Returns ``[]`` when
    fewer than two points are supplied.
    """
    if len(points) < 2:
        return []
    from vormap_utils import compute_nn_distances as _cnn
    return _cnn(points)


def _nn_distances_fast(points):
    """NN distances, sampled for very large datasets.

    For ``n <= 2000`` this is identical to :func:`_nn_distances`. Above that
    threshold, draws a uniform sample of 2000 query points and computes each
    one's nearest neighbour against the *full* point set - giving an
    unbiased estimator of the global NN distribution at fixed cost.

    The full-set NN search is delegated to
    :func:`vormap_utils.compute_nn_distances` (KDTree fast path when scipy
    is installed), then indexed by the sample.
    """
    import random as _rnd
    n = len(points)
    if n <= 2000:
        return _nn_distances(points)
    all_nn = _nn_distances(points)
    sample_idx = _rnd.sample(range(n), 2000)
    return [all_nn[i] for i in sample_idx]


# ---------------------------------------------------------------------------
# Diagnostic checks

def _check_sample_size(points, path):
    n = len(points)
    if n < 3:
        return Finding("sample_size", "critical", f"Only {n} points — insufficient for Voronoi diagram",
                       None, {"count": n})
    if n < 5:
        return Finding("sample_size", "warning", f"Only {n} points — diagram will be trivial",
                       None, {"count": n})
    if n > 50000:
        return Finding("sample_size", "info", f"{n} points — large dataset, some tools may be slow",
                       f"python vormap_sample.py {path} --random 10000", {"count": n})
    return Finding("sample_size", "ok", f"{n} points", None, {"count": n})


def _check_duplicates(points, path):
    dupes = 0
    seen = set()
    for p in points:
        rk = (round(p[0], 10), round(p[1], 10))
        if rk in seen:
            dupes += 1
        else:
            seen.add(rk)
    if dupes == 0:
        return Finding("duplicates", "ok", "No duplicate points", None, {"count": 0})
    sev = "critical" if dupes > len(points) * 0.1 else "warning"
    return Finding("duplicates", sev, f"{dupes} duplicate points found",
                   f"python vormap_outlier.py {path} --dedup", {"count": dupes})


def _check_scale(points, path):
    xs = [abs(p[0]) for p in points]
    ys = [abs(p[1]) for p in points]
    max_coord = max(max(xs), max(ys)) if xs else 0
    if max_coord > 1e12:
        return Finding("scale", "critical", f"Extreme coordinates (max={max_coord:.2e}) — precision loss likely",
                       f"python vormap_utils.py {path} --normalize", {"max_coord": max_coord})
    if max_coord > 1e8:
        return Finding("scale", "warning", f"Large coordinates (max={max_coord:.2e})",
                       f"python vormap_utils.py {path} --normalize", {"max_coord": max_coord})
    if max_coord < 1e-6 and max_coord > 0:
        return Finding("scale", "info", f"Very small coordinates (max={max_coord:.2e})",
                       f"python vormap_utils.py {path} --normalize", {"max_coord": max_coord})
    return Finding("scale", "ok", f"Coordinate scale normal (max={max_coord:.2g})", None, {"max_coord": max_coord})


def _check_aspect_ratio(points, path):
    x0, y0, x1, y1 = _bounding_box(points)
    w = x1 - x0
    h = y1 - y0
    if w == 0 or h == 0:
        return Finding("aspect_ratio", "critical", "All points are collinear (zero width or height)",
                       None, {"width": w, "height": h, "ratio": float("inf")})
    ratio = max(w, h) / min(w, h)
    if ratio > 20:
        return Finding("aspect_ratio", "warning", f"Extreme aspect ratio ({ratio:.1f}:1) — diagram may look distorted",
                       f"python vormap_utils.py {path} --normalize", {"ratio": ratio})
    if ratio > 10:
        return Finding("aspect_ratio", "info", f"High aspect ratio ({ratio:.1f}:1)",
                       None, {"ratio": ratio})
    return Finding("aspect_ratio", "ok", f"Aspect ratio {ratio:.1f}:1", None, {"ratio": ratio})


def _check_min_spacing(points, path):
    if len(points) < 2:
        return Finding("min_spacing", "ok", "Too few points to check", None, {})
    x0, y0, x1, y1 = _bounding_box(points)
    extent = _dist((x0, y0), (x1, y1))
    if extent == 0:
        return Finding("min_spacing", "critical", "All points at same location", None, {})
    min_d = float("inf")
    n = len(points)
    limit = min(n, 3000)
    for i in range(limit):
        for j in range(i + 1, min(i + 50, n)):
            d = _dist(points[i], points[j])
            if d < min_d:
                min_d = d
    ratio = min_d / extent if extent > 0 else 0
    if ratio < 1e-10:
        return Finding("min_spacing", "warning", f"Near-zero minimum spacing ({min_d:.2e})",
                       f"python vormap_relax.py {path} --lloyd 3", {"min_dist": min_d, "ratio": ratio})
    if ratio < 1e-6:
        return Finding("min_spacing", "info", f"Very small minimum spacing ({min_d:.2e})",
                       None, {"min_dist": min_d, "ratio": ratio})
    return Finding("min_spacing", "ok", f"Minimum spacing {min_d:.4g}", None, {"min_dist": min_d, "ratio": ratio})


def _check_isolation(points, path, nn_dists):
    if len(nn_dists) < 5:
        return Finding("isolation", "ok", "Too few points", None, {})
    m = _mean(nn_dists)
    s = _stddev(nn_dists)
    threshold = m + 3 * s if s > 0 else m * 3
    outliers = sum(1 for d in nn_dists if d > threshold)
    if outliers == 0:
        return Finding("isolation", "ok", "No isolated points", None, {"outlier_count": 0})
    sev = "warning" if outliers > len(points) * 0.05 else "info"
    return Finding("isolation", sev, f"{outliers} isolated point(s) (NN > 3\u03c3)",
                   f"python vormap_outlier.py {path} --remove-outliers", {"outlier_count": outliers})


def _check_boundary_crowding(points, path):
    if len(points) < 10:
        return Finding("boundary_crowding", "ok", "Too few points", None, {})
    x0, y0, x1, y1 = _bounding_box(points)
    w = x1 - x0
    h = y1 - y0
    if w == 0 or h == 0:
        return Finding("boundary_crowding", "ok", "Degenerate bbox", None, {})
    margin = 0.05
    near_edge = 0
    for px, py in points:
        rx = (px - x0) / w
        ry = (py - y0) / h
        if rx < margin or rx > (1 - margin) or ry < margin or ry > (1 - margin):
            near_edge += 1
    ratio = near_edge / len(points)
    if ratio > 0.5:
        return Finding("boundary_crowding", "warning",
                       f"{ratio:.0%} of points near edges — boundary cells may be extreme",
                       f"python vormap_clip.py {path} --margin 0.05", {"edge_ratio": ratio})
    return Finding("boundary_crowding", "ok", f"{ratio:.0%} near edges (normal)", None, {"edge_ratio": ratio})


def _check_density_uniformity(points, path):
    if len(points) < 20:
        return Finding("density_uniformity", "ok", "Too few points for grid analysis", None, {})
    x0, y0, x1, y1 = _bounding_box(points)
    w = x1 - x0
    h = y1 - y0
    if w == 0 or h == 0:
        return Finding("density_uniformity", "ok", "Degenerate bbox", None, {})
    res = min(5, int(math.sqrt(len(points) / 4)))
    if res < 2:
        res = 2
    grid = [[0] * res for _ in range(res)]
    for px, py in points:
        ci = min(int((px - x0) / w * res), res - 1)
        ri = min(int((py - y0) / h * res), res - 1)
        grid[ri][ci] += 1
    expected = len(points) / (res * res)
    cells = [grid[r][c] for r in range(res) for c in range(res)]
    cv = _stddev(cells) / expected if expected > 0 else 0
    max_cell = max(cells)
    min_cell = min(cells)
    if cv > 2.0:
        return Finding("density_uniformity", "warning",
                       f"Highly non-uniform density (CV={cv:.2f})",
                       f"python vormap_relax.py {path} --lloyd 5",
                       {"cv": cv, "max_cell": max_cell, "min_cell": min_cell})
    if cv > 1.0:
        return Finding("density_uniformity", "info",
                       f"Moderately non-uniform density (CV={cv:.2f})", None,
                       {"cv": cv, "max_cell": max_cell, "min_cell": min_cell})
    return Finding("density_uniformity", "ok", f"Density CV={cv:.2f}", None,
                   {"cv": cv, "max_cell": max_cell, "min_cell": min_cell})


def _check_degeneracy(points, path):
    """Check for collinear triplets (sample-based for large sets)."""
    import random as _rnd
    n = len(points)
    if n < 3:
        return Finding("degeneracy", "ok", "Too few points", None, {})
    collinear_count = 0
    eps = 1e-10
    max_checks = min(3000, n * (n - 1) * (n - 2) // 6)
    indices = list(range(n))
    for _ in range(max_checks):
        i, j, k = _rnd.sample(indices, 3)
        a, b, c = points[i], points[j], points[k]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) < eps:
            collinear_count += 1
    if collinear_count > max_checks * 0.05:
        return Finding("degeneracy", "warning",
                       f"Many collinear triplets ({collinear_count}/{max_checks} samples) — may cause degenerate cells",
                       f"python vormap_relax.py {path} --jitter 0.001",
                       {"collinear_samples": collinear_count, "total_samples": max_checks})
    if collinear_count > 0:
        return Finding("degeneracy", "info",
                       f"{collinear_count} collinear triplet(s) in {max_checks} samples",
                       None, {"collinear_samples": collinear_count, "total_samples": max_checks})
    return Finding("degeneracy", "ok", "No degeneracy detected", None, {})


def _check_hull_efficiency(points, path):
    """Ratio of convex hull area to bounding box area."""
    if len(points) < 3:
        return Finding("hull_efficiency", "ok", "Too few points", None, {})
    x0, y0, x1, y1 = _bounding_box(points)
    bbox_area = (x1 - x0) * (y1 - y0)
    if bbox_area == 0:
        return Finding("hull_efficiency", "ok", "Zero bbox area", None, {})
    # Andrew's monotone chain
    pts = sorted(points)
    hull = []
    for p in pts:
        while len(hull) >= 2:
            o = (hull[-1][0] - hull[-2][0]) * (p[1] - hull[-2][1]) - \
                (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0])
            if o <= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    lower_size = len(hull)
    for p in reversed(pts):
        while len(hull) > lower_size:
            o = (hull[-1][0] - hull[-2][0]) * (p[1] - hull[-2][1]) - \
                (hull[-1][1] - hull[-2][1]) * (p[0] - hull[-2][0])
            if o <= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    hull.pop()
    # Shoelace area
    hull_area = 0
    hn = len(hull)
    for i in range(hn):
        j = (i + 1) % hn
        hull_area += hull[i][0] * hull[j][1]
        hull_area -= hull[j][0] * hull[i][1]
    hull_area = abs(hull_area) / 2
    efficiency = hull_area / bbox_area
    if efficiency < 0.2:
        return Finding("hull_efficiency", "info",
                       f"Low hull efficiency ({efficiency:.0%}) — points form thin/irregular shape",
                       None, {"efficiency": efficiency})
    return Finding("hull_efficiency", "ok", f"Hull efficiency {efficiency:.0%}", None, {"efficiency": efficiency})


def _check_nn_distribution(points, path, nn_dists):
    """Check if NN distances show anomalous patterns."""
    if len(nn_dists) < 10:
        return Finding("nn_distribution", "ok", "Too few points", None, {})
    m = _mean(nn_dists)
    s = _stddev(nn_dists)
    cv = s / m if m > 0 else 0
    if cv > 1.5:
        return Finding("nn_distribution", "warning",
                       f"Highly irregular NN distribution (CV={cv:.2f}) — mixed clusters and voids",
                       f"python vormap_sample.py {path} --stratified",
                       {"cv": cv, "mean_nn": m, "std_nn": s})
    if cv < 0.1:
        return Finding("nn_distribution", "info",
                       f"Very regular spacing (CV={cv:.2f}) — near-lattice pattern",
                       None, {"cv": cv, "mean_nn": m, "std_nn": s})
    return Finding("nn_distribution", "ok", f"NN distance CV={cv:.2f}", None,
                   {"cv": cv, "mean_nn": m, "std_nn": s})


def _check_cluster_imbalance(points, path):
    """Grid-based cluster balance check."""
    if len(points) < 20:
        return Finding("cluster_imbalance", "ok", "Too few points", None, {})
    x0, y0, x1, y1 = _bounding_box(points)
    w = x1 - x0
    h = y1 - y0
    if w == 0 or h == 0:
        return Finding("cluster_imbalance", "ok", "Degenerate bbox", None, {})
    quads = [0, 0, 0, 0]
    mx = (x0 + x1) / 2
    my = (y0 + y1) / 2
    for px, py in points:
        qi = (0 if px < mx else 1) + (0 if py < my else 2)
        quads[qi] += 1
    expected = len(points) / 4
    max_dev = max(abs(q - expected) for q in quads) / expected if expected > 0 else 0
    if max_dev > 1.5:
        return Finding("cluster_imbalance", "warning",
                       f"Severe quadrant imbalance (max deviation {max_dev:.1f}x expected)",
                       f"python vormap_sample.py {path} --stratified",
                       {"quadrants": quads, "max_deviation": max_dev})
    return Finding("cluster_imbalance", "ok", "Quadrant balance OK", None,
                   {"quadrants": quads, "max_deviation": max_dev})


# ---------------------------------------------------------------------------
# Main diagnostic function

def diagnose(path, strict=False):
    """Run all diagnostic checks on a point dataset.

    Args:
        path: Path to whitespace-delimited point file.
        strict: If True, treat 'info' findings as 'warning'.

    Returns:
        Diagnosis object with findings, health score, and auto-fix plan.
    """
    points = _load_points(path)
    findings = []

    findings.append(_check_sample_size(points, path))

    if len(points) >= 2:
        findings.append(_check_duplicates(points, path))
        findings.append(_check_scale(points, path))
        findings.append(_check_aspect_ratio(points, path))
        findings.append(_check_min_spacing(points, path))
        findings.append(_check_boundary_crowding(points, path))
        findings.append(_check_density_uniformity(points, path))
        findings.append(_check_degeneracy(points, path))
        findings.append(_check_hull_efficiency(points, path))
        findings.append(_check_cluster_imbalance(points, path))

        nn_dists = _nn_distances_fast(points)
        findings.append(_check_isolation(points, path, nn_dists))
        findings.append(_check_nn_distribution(points, path, nn_dists))

    if strict:
        findings = [
            Finding(f.check, "warning" if f.severity == "info" else f.severity,
                    f.message, f.fix_command, f.details)
            if f.severity == "info" else f
            for f in findings
        ]

    return Diagnosis(path, len(points), findings)


# ---------------------------------------------------------------------------
# CLI

def _severity_badge(sev):
    badges = {"ok": "\033[32m[OK]\033[0m", "info": "\033[34m[INFO]\033[0m",
              "warning": "\033[33m[WARN]\033[0m", "critical": "\033[31m[CRIT]\033[0m"}
    return badges.get(sev, sev)


def _print_report(diag):
    print(f"\n{'='*60}")
    print(f"  \U0001fa7a VoronoiMap Doctor \u2014 {diag.dataset_path}")
    print(f"  Points: {diag.point_count}  |  Health: {diag.health_score}/100")
    print(f"{'='*60}\n")

    for f in diag.findings:
        badge = _severity_badge(f.severity)
        print(f"  {badge} {f.check:.<24} {f.message}")
        if f.fix_command:
            print(f"       \u21b3 Fix: {f.fix_command}")

    _line = '\u2500' * 60
    print(f"\n{_line}")
    print(f"  Summary: {diag.summary}")
    if diag.auto_fix_plan:
        print(f"\n  \U0001f4cb Auto-Fix Plan ({len(diag.auto_fix_plan)} commands):")
        for i, cmd in enumerate(diag.auto_fix_plan, 1):
            print(f"    {i}. {cmd}")
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VoronoiMap Doctor \u2014 dataset diagnostician")
    parser.add_argument("datafile", help="Path to point dataset")
    parser.add_argument("--json", metavar="PATH", help="Export JSON report")
    parser.add_argument("--html", metavar="PATH", help="Export HTML report")
    parser.add_argument("--auto-fix", action="store_true", help="Print fix plan only")
    parser.add_argument("--strict", action="store_true", help="Treat info as warning")
    args = parser.parse_args()

    diag = diagnose(args.datafile, strict=args.strict)

    if args.auto_fix:
        if diag.auto_fix_plan:
            for cmd in diag.auto_fix_plan:
                print(cmd)
        else:
            print("No fixes needed.")
        return

    _print_report(diag)

    if args.json:
        diag.to_json(args.json)
        print(f"  \U0001f4c4 JSON report saved: {args.json}")
    if args.html:
        diag.to_html(args.html)
        print(f"  \U0001f4c4 HTML report saved: {args.html}")


if __name__ == "__main__":
    main()
