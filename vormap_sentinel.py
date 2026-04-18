"""Spatial Sentinel — proactive distribution monitoring & anomaly alerting.

Watches a series of spatial dataset snapshots and detects when the
distribution shifts beyond learned baseline norms.  This brings
*agentic awareness* to VoronoiMap — the tool monitors your spatial data
and tells you when something changes before you have to ask.

Seven detection channels:

- **Density Drift** — grid-based density histogram vs baseline, measured
  by Jensen-Shannon divergence.
- **Centroid Shift** — movement of the global centroid beyond a threshold.
- **Spread Change** — standard-distance deviation vs baseline.
- **Count Anomaly** — point count outside baseline mean ± k·σ.
- **Quadrant Imbalance** — chi-squared test of quadrant proportions.
- **Cluster Emergence** — DBSCAN-like new cluster detection.
- **Void Detection** — new empty regions where points previously existed.

Each channel produces a severity (info/warning/critical) and the
sentinel produces an overall health score (0–100).

Usage (Python API)::

    from vormap_sentinel import Sentinel

    s = Sentinel(grid_res=10)

    # Feed baseline snapshots
    s.learn("baseline1.txt")
    s.learn("baseline2.txt")
    s.learn("baseline3.txt")
    s.finalize_baseline()

    # Monitor new snapshots
    report = s.monitor("snapshot_new.txt")
    print(report.health_score)
    for alert in report.alerts:
        print(f"[{alert.severity}] {alert.channel}: {alert.message}")

    # Export
    report.to_json("sentinel_report.json")
    report.to_html("sentinel_report.html")

CLI::

    voronoimap datauni5.txt 5 --sentinel-baseline data1.txt,data2.txt,data3.txt
    voronoimap datauni5.txt 5 --sentinel-baseline baseline_dir/
    voronoimap datauni5.txt 5 --sentinel-report sentinel.json
    voronoimap datauni5.txt 5 --sentinel-html sentinel.html
    voronoimap datauni5.txt 5 --sentinel-sensitivity 1.5
"""

import json
import math
import os
from collections import namedtuple
from datetime import datetime

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

Alert = namedtuple("Alert", ["channel", "severity", "value", "threshold", "message"])


class SentinelReport:
    """Result of monitoring a snapshot against the baseline."""

    __slots__ = ("timestamp", "health_score", "alerts", "metrics",
                 "point_count", "source_file")

    def __init__(self, *, source_file="", point_count=0):
        self.timestamp = datetime.utcnow().isoformat() + "Z"
        self.source_file = source_file
        self.point_count = point_count
        self.health_score = 100
        self.alerts = []
        self.metrics = {}

    # -- penalties ----------------------------------------------------------

    def _apply_penalties(self):
        score = 100
        for a in self.alerts:
            if a.severity == "critical":
                score -= 25
            elif a.severity == "warning":
                score -= 10
            else:
                score -= 2
        self.health_score = max(0, score)

    # -- export -------------------------------------------------------------

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "source_file": self.source_file,
            "point_count": self.point_count,
            "health_score": self.health_score,
            "alert_count": len(self.alerts),
            "alerts": [
                {"channel": a.channel, "severity": a.severity,
                 "value": a.value, "threshold": a.threshold,
                 "message": a.message}
                for a in self.alerts
            ],
            "metrics": self.metrics,
        }

    def to_json(self, path):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path

    def to_html(self, path):
        d = self.to_dict()
        sev_colors = {"critical": "#e74c3c", "warning": "#f39c12", "info": "#3498db"}
        health_color = "#27ae60" if d["health_score"] >= 80 else (
            "#f39c12" if d["health_score"] >= 50 else "#e74c3c")

        alert_rows = ""
        for a in d["alerts"]:
            c = sev_colors.get(a["severity"], "#999")
            alert_rows += (
                f'<tr><td style="color:{c};font-weight:bold">'
                f'{a["severity"].upper()}</td>'
                f'<td>{a["channel"]}</td>'
                f'<td>{a["message"]}</td>'
                f'<td>{a["value"]:.4f}</td>'
                f'<td>{a["threshold"]:.4f}</td></tr>\n'
            )

        metric_rows = ""
        for k, v in d["metrics"].items():
            metric_rows += f"<tr><td>{k}</td><td>{v:.4f}</td></tr>\n"

        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Spatial Sentinel Report</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;
background:#0d1117;color:#c9d1d9;padding:1em}}
h1{{color:#58a6ff}} h2{{color:#8b949e;border-bottom:1px solid #30363d;padding-bottom:.3em}}
.health{{font-size:3em;font-weight:bold;color:{health_color};text-align:center;
padding:.5em;border:3px solid {health_color};border-radius:50%;width:2.5em;
height:2.5em;line-height:2.5em;margin:1em auto;display:block}}
table{{width:100%;border-collapse:collapse;margin:1em 0}}
th,td{{padding:.5em .8em;text-align:left;border-bottom:1px solid #21262d}}
th{{color:#58a6ff;font-size:.85em;text-transform:uppercase}}
.meta{{color:#8b949e;font-size:.9em}}
.badge{{display:inline-block;padding:.15em .5em;border-radius:4px;font-size:.8em;
font-weight:600;margin:0 .3em}}
.badge-critical{{background:#e74c3c22;color:#e74c3c;border:1px solid #e74c3c}}
.badge-warning{{background:#f39c1222;color:#f39c12;border:1px solid #f39c12}}
.badge-info{{background:#3498db22;color:#3498db;border:1px solid #3498db}}
</style></head><body>
<h1>🛡️ Spatial Sentinel Report</h1>
<p class="meta">Generated: {d["timestamp"]} · Source: {d["source_file"]}
 · Points: {d["point_count"]}</p>
<div class="health">{d["health_score"]}</div>
<p style="text-align:center;color:{health_color}">Health Score</p>
<h2>Alerts ({d["alert_count"]})</h2>
{"<p style='color:#3fb950'>✅ All clear — no anomalies detected.</p>" if not d["alerts"] else
f'<table><tr><th>Severity</th><th>Channel</th><th>Message</th><th>Value</th><th>Threshold</th></tr>{alert_rows}</table>'}
<h2>Metrics</h2>
<table><tr><th>Metric</th><th>Value</th></tr>
{metric_rows}</table>
</body></html>"""
        with open(path, "w") as f:
            f.write(html)
        return path

    def summary_text(self):
        lines = [f"Sentinel Health: {self.health_score}/100  "
                 f"({len(self.alerts)} alert(s))"]
        for a in self.alerts:
            lines.append(f"  [{a.severity.upper()}] {a.channel}: {a.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sentinel engine
# ---------------------------------------------------------------------------

def _load_points(source):
    """Load points from file path or list of (x, y) tuples."""
    if isinstance(source, str):
        pts = []
        with open(source, "r") as f:
            for line in f:
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
    return list(source)


def _centroid(pts):
    n = len(pts)
    if n == 0:
        return (0.0, 0.0)
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    return (sx / n, sy / n)


def _std_distance(pts, cx, cy):
    if len(pts) < 2:
        return 0.0
    var = sum((p[0] - cx) ** 2 + (p[1] - cy) ** 2 for p in pts) / len(pts)
    return math.sqrt(var)


def _density_histogram(pts, bounds, grid_res):
    """Return normalized density grid as flat list."""
    xmin, ymin, xmax, ymax = bounds
    dx = (xmax - xmin) / grid_res if grid_res > 0 else 1.0
    dy = (ymax - ymin) / grid_res if grid_res > 0 else 1.0
    cells = grid_res * grid_res
    counts = [0] * cells
    for px, py in pts:
        ci = min(int((px - xmin) / dx), grid_res - 1) if dx > 0 else 0
        ri = min(int((py - ymin) / dy), grid_res - 1) if dy > 0 else 0
        ci = max(0, ci)
        ri = max(0, ri)
        counts[ri * grid_res + ci] += 1
    n = len(pts) or 1
    return [c / n for c in counts]


def _js_divergence(p, q):
    """Jensen-Shannon divergence between two distributions."""
    assert len(p) == len(q)
    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]
    div = 0.0
    for i in range(len(p)):
        if p[i] > 0 and m[i] > 0:
            div += p[i] * math.log(p[i] / m[i])
        if q[i] > 0 and m[i] > 0:
            div += q[i] * math.log(q[i] / m[i])
    return div / 2.0


def _quadrant_counts(pts, cx, cy):
    """Count points in 4 quadrants around centroid."""
    q = [0, 0, 0, 0]
    for px, py in pts:
        idx = (0 if px <= cx else 1) + (0 if py <= cy else 2)
        q[idx] += 1
    return q


def _chi_squared(observed, expected):
    stat = 0.0
    for o, e in zip(observed, expected):
        if e > 0:
            stat += (o - e) ** 2 / e
    return stat


def _dbscan_simple(pts, eps, min_pts):
    """Minimal DBSCAN returning cluster labels (-1 = noise)."""
    n = len(pts)
    labels = [-1] * n
    cluster_id = 0

    def neighbors(i):
        nbrs = []
        for j in range(n):
            if i != j:
                dx = pts[i][0] - pts[j][0]
                dy = pts[i][1] - pts[j][1]
                if dx * dx + dy * dy <= eps * eps:
                    nbrs.append(j)
        return nbrs

    visited = [False] * n
    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        nbrs = neighbors(i)
        if len(nbrs) < min_pts:
            continue
        labels[i] = cluster_id
        queue = list(nbrs)
        while queue:
            j = queue.pop(0)
            if not visited[j]:
                visited[j] = True
                nbrs_j = neighbors(j)
                if len(nbrs_j) >= min_pts:
                    queue.extend(nbrs_j)
            if labels[j] == -1:
                labels[j] = cluster_id
        cluster_id += 1
    return labels, cluster_id


class Sentinel:
    """Spatial distribution monitor.

    Parameters
    ----------
    grid_res : int
        Grid resolution for density histogram (grid_res × grid_res cells).
    sensitivity : float
        Multiplier for thresholds — lower = more sensitive (default 2.0).
    eps : float
        DBSCAN neighbourhood radius for cluster detection.
    min_cluster_pts : int
        Minimum points for DBSCAN core status.
    """

    def __init__(self, grid_res=10, sensitivity=2.0, eps=50.0,
                 min_cluster_pts=3):
        self.grid_res = grid_res
        self.sensitivity = sensitivity
        self.eps = eps
        self.min_cluster_pts = min_cluster_pts

        # Baseline accumulators
        self._baseline_counts = []
        self._baseline_centroids = []
        self._baseline_stds = []
        self._baseline_densities = []
        self._baseline_quadrants = []
        self._baseline_cluster_counts = []
        self._baseline_density_grids = []
        self._bounds = None
        self._finalized = False
        self._baseline = {}

    # -- bounds ------------------------------------------------------------

    def _compute_bounds(self, pts):
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        margin = 1.0
        return (min(xs) - margin, min(ys) - margin,
                max(xs) + margin, max(ys) + margin)

    def _merge_bounds(self, b1, b2):
        return (min(b1[0], b2[0]), min(b1[1], b2[1]),
                max(b1[2], b2[2]), max(b1[3], b2[3]))

    # -- learn -------------------------------------------------------------

    def learn(self, source):
        """Ingest a baseline snapshot (file path or point list)."""
        pts = _load_points(source)
        if not pts:
            return
        b = self._compute_bounds(pts)
        self._bounds = b if self._bounds is None else self._merge_bounds(self._bounds, b)

        cx, cy = _centroid(pts)
        sd = _std_distance(pts, cx, cy)
        density = _density_histogram(pts, self._bounds, self.grid_res)
        quads = _quadrant_counts(pts, cx, cy)
        _, n_clusters = _dbscan_simple(pts, self.eps, self.min_cluster_pts)

        self._baseline_counts.append(len(pts))
        self._baseline_centroids.append((cx, cy))
        self._baseline_stds.append(sd)
        self._baseline_densities.append(density)
        self._baseline_quadrants.append(quads)
        self._baseline_cluster_counts.append(n_clusters)
        self._baseline_density_grids.append(density)

    def finalize_baseline(self):
        """Compute aggregate baseline statistics from learned snapshots."""
        n = len(self._baseline_counts)
        if n == 0:
            raise ValueError("No baseline snapshots learned")

        mean_count = sum(self._baseline_counts) / n
        std_count = math.sqrt(
            sum((c - mean_count) ** 2 for c in self._baseline_counts) / max(n, 1)
        ) if n > 1 else mean_count * 0.1

        cxs = [c[0] for c in self._baseline_centroids]
        cys = [c[1] for c in self._baseline_centroids]
        mean_cx, mean_cy = sum(cxs) / n, sum(cys) / n

        mean_std = sum(self._baseline_stds) / n
        std_std = math.sqrt(
            sum((s - mean_std) ** 2 for s in self._baseline_stds) / max(n, 1)
        ) if n > 1 else mean_std * 0.1

        # Average density grid
        cells = self.grid_res * self.grid_res
        avg_density = [0.0] * cells
        for grid in self._baseline_density_grids:
            for i in range(min(cells, len(grid))):
                avg_density[i] += grid[i]
        avg_density = [v / n for v in avg_density]

        # Average quadrant proportions
        total_quads = [0.0] * 4
        for q in self._baseline_quadrants:
            for i in range(4):
                total_quads[i] += q[i]
        sq = sum(total_quads) or 1
        avg_quad_props = [v / sq for v in total_quads]

        mean_clusters = sum(self._baseline_cluster_counts) / n

        self._baseline = {
            "count_mean": mean_count,
            "count_std": max(std_count, 1.0),
            "centroid": (mean_cx, mean_cy),
            "centroid_spread": max(
                math.sqrt(sum((cx - mean_cx) ** 2 + (cy - mean_cy) ** 2
                              for cx, cy in self._baseline_centroids) / n),
                1.0),
            "std_distance_mean": mean_std,
            "std_distance_std": max(std_std, 1.0),
            "density_avg": avg_density,
            "quad_proportions": avg_quad_props,
            "cluster_count_mean": mean_clusters,
        }
        self._finalized = True

    # -- monitor -----------------------------------------------------------

    def monitor(self, source):
        """Check a new snapshot against the baseline.

        Returns a SentinelReport with health score and alerts.
        """
        if not self._finalized:
            raise RuntimeError("Call finalize_baseline() before monitoring")

        pts = _load_points(source)
        src_name = source if isinstance(source, str) else "<in-memory>"
        report = SentinelReport(source_file=src_name, point_count=len(pts))

        if not pts:
            report.alerts.append(Alert(
                "count", "critical", 0,
                self._baseline["count_mean"],
                "Empty dataset — no points found"))
            report.health_score = 0
            return report

        b = self._baseline
        k = self.sensitivity

        # 1. Count anomaly
        count_z = abs(len(pts) - b["count_mean"]) / b["count_std"]
        report.metrics["count_z_score"] = count_z
        report.metrics["point_count"] = float(len(pts))
        report.metrics["baseline_count_mean"] = b["count_mean"]
        if count_z > k:
            sev = "critical" if count_z > k * 2 else "warning"
            report.alerts.append(Alert(
                "count_anomaly", sev, count_z, k,
                f"Point count {len(pts)} deviates from baseline "
                f"(mean={b['count_mean']:.0f}, z={count_z:.2f})"))

        # 2. Centroid shift
        cx, cy = _centroid(pts)
        shift = math.sqrt((cx - b["centroid"][0]) ** 2 +
                          (cy - b["centroid"][1]) ** 2)
        threshold_shift = b["centroid_spread"] * k
        report.metrics["centroid_shift"] = shift
        report.metrics["centroid_threshold"] = threshold_shift
        if shift > threshold_shift:
            sev = "critical" if shift > threshold_shift * 2 else "warning"
            report.alerts.append(Alert(
                "centroid_shift", sev, shift, threshold_shift,
                f"Global centroid shifted {shift:.1f} units "
                f"(threshold={threshold_shift:.1f})"))

        # 3. Spread change
        sd = _std_distance(pts, cx, cy)
        spread_z = abs(sd - b["std_distance_mean"]) / b["std_distance_std"]
        report.metrics["std_distance"] = sd
        report.metrics["spread_z_score"] = spread_z
        if spread_z > k:
            direction = "expanded" if sd > b["std_distance_mean"] else "contracted"
            sev = "critical" if spread_z > k * 2 else "warning"
            report.alerts.append(Alert(
                "spread_change", sev, spread_z, k,
                f"Distribution {direction} "
                f"(σ={sd:.1f} vs baseline {b['std_distance_mean']:.1f})"))

        # 4. Density drift (Jensen-Shannon divergence)
        density = _density_histogram(pts, self._bounds, self.grid_res)
        jsd = _js_divergence(density, b["density_avg"])
        report.metrics["density_jsd"] = jsd
        jsd_thresh = 0.05 * k
        if jsd > jsd_thresh:
            sev = "critical" if jsd > jsd_thresh * 2 else "warning"
            report.alerts.append(Alert(
                "density_drift", sev, jsd, jsd_thresh,
                f"Density distribution shifted (JSD={jsd:.4f}, "
                f"threshold={jsd_thresh:.4f})"))

        # 5. Quadrant imbalance
        quads = _quadrant_counts(pts, b["centroid"][0], b["centroid"][1])
        n_pts = len(pts)
        expected = [p * n_pts for p in b["quad_proportions"]]
        chi2 = _chi_squared(quads, expected)
        report.metrics["quadrant_chi2"] = chi2
        chi2_thresh = 7.815 * k  # df=3, p=0.05 baseline
        if chi2 > chi2_thresh:
            sev = "critical" if chi2 > chi2_thresh * 2 else "warning"
            report.alerts.append(Alert(
                "quadrant_imbalance", sev, chi2, chi2_thresh,
                f"Quadrant distribution imbalanced (χ²={chi2:.2f}, "
                f"threshold={chi2_thresh:.2f})"))

        # 6. Cluster emergence
        _, n_clusters = _dbscan_simple(pts, self.eps, self.min_cluster_pts)
        cluster_diff = n_clusters - b["cluster_count_mean"]
        report.metrics["cluster_count"] = float(n_clusters)
        report.metrics["baseline_cluster_mean"] = b["cluster_count_mean"]
        if abs(cluster_diff) > max(1, b["cluster_count_mean"] * 0.5):
            direction = "emerged" if cluster_diff > 0 else "dissolved"
            sev = "warning"
            report.alerts.append(Alert(
                "cluster_change", sev, float(n_clusters),
                b["cluster_count_mean"],
                f"Clusters {direction}: {n_clusters} vs baseline "
                f"{b['cluster_count_mean']:.1f}"))

        # 7. Void detection — baseline-occupied grid cells now empty
        void_count = 0
        cells = self.grid_res * self.grid_res
        for i in range(min(cells, len(density), len(b["density_avg"]))):
            if b["density_avg"][i] > 0.01 and density[i] == 0:
                void_count += 1
        report.metrics["void_cells"] = float(void_count)
        void_thresh = max(2, cells * 0.1)
        if void_count > void_thresh:
            sev = "warning" if void_count < void_thresh * 2 else "critical"
            report.alerts.append(Alert(
                "void_detection", sev, float(void_count), void_thresh,
                f"{void_count} previously occupied grid cells now empty"))

        report._apply_penalties()
        return report

    # -- serialization -----------------------------------------------------

    def save_baseline(self, path):
        """Save the finalized baseline to JSON."""
        if not self._finalized:
            raise RuntimeError("Baseline not finalized")
        data = dict(self._baseline)
        data["bounds"] = list(self._bounds) if self._bounds else None
        data["grid_res"] = self.grid_res
        data["sensitivity"] = self.sensitivity
        data["eps"] = self.eps
        data["min_cluster_pts"] = self.min_cluster_pts
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load_baseline(cls, path):
        """Load a saved baseline and return a ready Sentinel."""
        with open(path, "r") as f:
            data = json.load(f)
        s = cls(
            grid_res=data.get("grid_res", 10),
            sensitivity=data.get("sensitivity", 2.0),
            eps=data.get("eps", 50.0),
            min_cluster_pts=data.get("min_cluster_pts", 3),
        )
        s._bounds = tuple(data["bounds"]) if data.get("bounds") else None
        s._baseline = {
            "count_mean": data["count_mean"],
            "count_std": data["count_std"],
            "centroid": tuple(data["centroid"]),
            "centroid_spread": data["centroid_spread"],
            "std_distance_mean": data["std_distance_mean"],
            "std_distance_std": data["std_distance_std"],
            "density_avg": data["density_avg"],
            "quad_proportions": data["quad_proportions"],
            "cluster_count_mean": data["cluster_count_mean"],
        }
        s._finalized = True
        return s


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def add_sentinel_args(parser):
    """Add sentinel-related arguments to an argparse parser."""
    grp = parser.add_argument_group("Spatial Sentinel",
                                    "Proactive distribution monitoring")
    grp.add_argument("--sentinel-baseline", metavar="FILES",
                     help="Comma-separated baseline files or directory")
    grp.add_argument("--sentinel-report", metavar="PATH",
                     help="Export sentinel report as JSON")
    grp.add_argument("--sentinel-html", metavar="PATH",
                     help="Export sentinel report as HTML")
    grp.add_argument("--sentinel-sensitivity", type=float, default=2.0,
                     metavar="K",
                     help="Sensitivity multiplier (lower = more sensitive, "
                          "default 2.0)")
    grp.add_argument("--sentinel-save-baseline", metavar="PATH",
                     help="Save computed baseline to JSON for reuse")
    grp.add_argument("--sentinel-load-baseline", metavar="PATH",
                     help="Load a saved baseline JSON instead of computing")


def run_sentinel_cli(args, input_file):
    """Execute sentinel analysis from parsed CLI args.

    Returns (SentinelReport, Sentinel) or (None, None) if no sentinel
    flags were provided.
    """
    has_sentinel = any([
        getattr(args, "sentinel_baseline", None),
        getattr(args, "sentinel_load_baseline", None),
    ])
    if not has_sentinel:
        return None, None

    sensitivity = getattr(args, "sentinel_sensitivity", 2.0)

    # Build or load sentinel
    if getattr(args, "sentinel_load_baseline", None):
        sentinel = Sentinel.load_baseline(args.sentinel_load_baseline)
        sentinel.sensitivity = sensitivity
    else:
        sentinel = Sentinel(grid_res=10, sensitivity=sensitivity)
        baseline_arg = args.sentinel_baseline
        if os.path.isdir(baseline_arg):
            for fn in sorted(os.listdir(baseline_arg)):
                fp = os.path.join(baseline_arg, fn)
                if os.path.isfile(fp) and fn.endswith(".txt"):
                    sentinel.learn(fp)
        else:
            for fp in baseline_arg.split(","):
                fp = fp.strip()
                if fp and os.path.isfile(fp):
                    sentinel.learn(fp)
        sentinel.finalize_baseline()

    # Optionally save baseline
    if getattr(args, "sentinel_save_baseline", None):
        sentinel.save_baseline(args.sentinel_save_baseline)

    # Monitor the input file
    report = sentinel.monitor(input_file)

    # Export
    if getattr(args, "sentinel_report", None):
        report.to_json(args.sentinel_report)
    if getattr(args, "sentinel_html", None):
        report.to_html(args.sentinel_html)

    # Print summary
    print("\n" + "=" * 60)
    print("🛡️  SPATIAL SENTINEL REPORT")
    print("=" * 60)
    print(report.summary_text())
    print("=" * 60)

    return report, sentinel
