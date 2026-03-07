"""Spatial outlier detection for Voronoi diagrams.

Identifies Voronoi cells whose metrics (area, compactness, perimeter,
vertex count) are statistically unusual relative to the rest of the
diagram.  Useful for detecting clusters, voids, and anomalous regions
in spatial data.

Two detection methods are supported:

- **Z-score** — flags cells whose metric is more than *k* standard
  deviations from the mean (default *k* = 2.0).
- **IQR** — flags cells outside the interquartile fence
  [Q1 − 1.5·IQR, Q3 + 1.5·IQR] (configurable multiplier).

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_outlier import detect_outliers, export_outlier_svg

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = detect_outliers(stats, metrics=["area", "compactness"])
    for o in result.outliers:
        print(f"Region {o['region_index']}: {o['flags']}")

    export_outlier_svg(result, regions, data, "outliers.svg")

CLI::

    voronoimap datauni5.txt 5 --outliers
    voronoimap datauni5.txt 5 --outliers-svg outliers.svg
    voronoimap datauni5.txt 5 --outliers-json outliers.json
    voronoimap datauni5.txt 5 --outliers --method iqr --threshold 1.5
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from vormap_geometry import mean as _mean, std as _std, percentile as _percentile


# ── Result container ────────────────────────────────────────────────

@dataclass
class OutlierResult:
    """Container for outlier detection results.

    Attributes
    ----------
    outliers : list of dict
        Regions flagged as outliers.  Each dict has:
        - ``region_index`` (int): 1-based index
        - ``seed`` (tuple): (x, y) seed coordinates
        - ``flags`` (dict): metric → direction ("high" or "low")
        - ``values`` (dict): metric → raw value
        - ``z_scores`` (dict): metric → z-score (only for method="zscore")
    normal : list of dict
        Regions *not* flagged.  Same structure minus ``flags`` (empty).
    method : str
        Detection method ("zscore" or "iqr").
    threshold : float
        Threshold used.
    metrics_used : list of str
        Which metrics were checked.
    summary : dict
        Per-metric summary: mean, std, Q1, Q3, lower_fence, upper_fence,
        outlier_count_high, outlier_count_low.
    """
    outliers: list = field(default_factory=list)
    normal: list = field(default_factory=list)
    method: str = "zscore"
    threshold: float = 2.0
    metrics_used: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def total_regions(self):
        return len(self.outliers) + len(self.normal)

    @property
    def outlier_count(self):
        return len(self.outliers)

    @property
    def outlier_rate(self):
        if self.total_regions == 0:
            return 0.0
        return self.outlier_count / self.total_regions

    def format_report(self):
        """Return a human-readable text report."""
        lines = []
        lines.append("=" * 60)
        lines.append("  Spatial Outlier Detection Report")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  Method:     {self.method} (threshold={self.threshold})")
        lines.append(f"  Metrics:    {', '.join(self.metrics_used)}")
        lines.append(f"  Regions:    {self.total_regions} total, "
                     f"{self.outlier_count} outliers "
                     f"({self.outlier_rate:.1%})")
        lines.append("")

        # Per-metric summary
        for metric in self.metrics_used:
            s = self.summary.get(metric, {})
            lines.append(f"  ── {metric} ──")
            lines.append(f"    Mean:    {s.get('mean', 0):.4f}")
            lines.append(f"    Std:     {s.get('std', 0):.4f}")
            lines.append(f"    Q1:      {s.get('q1', 0):.4f}")
            lines.append(f"    Q3:      {s.get('q3', 0):.4f}")
            lines.append(f"    High:    {s.get('outlier_count_high', 0)} outlier(s)")
            lines.append(f"    Low:     {s.get('outlier_count_low', 0)} outlier(s)")
            lines.append("")

        # Outlier details
        if self.outliers:
            lines.append("  ── Flagged Regions ──")
            for o in self.outliers:
                flag_strs = [f"{m}={d}" for m, d in o["flags"].items()]
                lines.append(
                    f"    Region {o['region_index']:>3d}  "
                    f"seed=({o['seed'][0]:.1f}, {o['seed'][1]:.1f})  "
                    f"{', '.join(flag_strs)}"
                )
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)


# ── Detection methods ───────────────────────────────────────────────

# Valid metrics that can be extracted from region stats
VALID_METRICS = ("area", "perimeter", "compactness", "vertex_count",
                 "avg_edge_length")

DEFAULT_METRICS = ("area", "compactness")


def _extract_values(stats, metric):
    """Extract a numeric list for *metric* from region stat dicts."""
    return [float(s.get(metric, 0)) for s in stats]


def _quartiles(values):
    """Return (Q1, median, Q3) for a list of floats."""
    s = sorted(values)
    return _percentile(s, 25), _percentile(s, 50), _percentile(s, 75)


def detect_outliers(stats, metrics=None, method="zscore", threshold=None):
    """Detect spatial outliers in Voronoi region statistics.

    Parameters
    ----------
    stats : list of dict
        Region statistics from ``vormap_viz.compute_region_stats()``.
    metrics : list of str or None
        Which metrics to check.  Defaults to ``("area", "compactness")``.
        Valid values: area, perimeter, compactness, vertex_count,
        avg_edge_length.
    method : str
        ``"zscore"`` (default) or ``"iqr"``.
    threshold : float or None
        - For zscore: number of standard deviations (default 2.0).
        - For iqr: IQR multiplier (default 1.5).

    Returns
    -------
    OutlierResult
        Detection results with outlier list, normal list, and summary.

    Raises
    ------
    ValueError
        If an invalid metric or method is given, or stats is empty.
    """
    if not stats:
        raise ValueError("stats list is empty")

    if metrics is None:
        metrics = list(DEFAULT_METRICS)
    else:
        metrics = list(metrics)

    for m in metrics:
        if m not in VALID_METRICS:
            raise ValueError(
                f"Invalid metric '{m}'. Valid: {', '.join(VALID_METRICS)}")

    method = method.lower()
    if method not in ("zscore", "iqr"):
        raise ValueError(f"Invalid method '{method}'. Use 'zscore' or 'iqr'.")

    if threshold is None:
        threshold = 2.0 if method == "zscore" else 1.5

    # Build per-metric summary and classify
    summary = {}
    flags_per_region = [{} for _ in stats]
    zscores_per_region = [{} for _ in stats]

    for metric in metrics:
        values = _extract_values(stats, metric)
        mean_val = _mean(values)
        std_val = _std(values, mean_val=mean_val)
        q1, median_val, q3 = _quartiles(values)
        iqr_val = q3 - q1

        if method == "zscore":
            lower = mean_val - threshold * std_val if std_val > 0 else mean_val
            upper = mean_val + threshold * std_val if std_val > 0 else mean_val
        else:  # iqr
            lower = q1 - threshold * iqr_val
            upper = q3 + threshold * iqr_val

        count_high = 0
        count_low = 0

        for i, v in enumerate(values):
            z = (v - mean_val) / std_val if std_val > 0 else 0.0
            zscores_per_region[i][metric] = round(z, 3)

            if v > upper:
                flags_per_region[i][metric] = "high"
                count_high += 1
            elif v < lower:
                flags_per_region[i][metric] = "low"
                count_low += 1

        summary[metric] = {
            "mean": round(mean_val, 6),
            "std": round(std_val, 6),
            "median": round(median_val, 6),
            "q1": round(q1, 6),
            "q3": round(q3, 6),
            "iqr": round(iqr_val, 6),
            "lower_fence": round(lower, 6),
            "upper_fence": round(upper, 6),
            "outlier_count_high": count_high,
            "outlier_count_low": count_low,
        }

    # Partition into outliers and normal
    result = OutlierResult(
        method=method,
        threshold=threshold,
        metrics_used=metrics,
        summary=summary,
    )

    for i, s in enumerate(stats):
        seed = (s.get("seed_x", 0), s.get("seed_y", 0))
        entry = {
            "region_index": s.get("region_index", i + 1),
            "seed": seed,
            "flags": flags_per_region[i],
            "values": {m: s.get(m, 0) for m in metrics},
            "z_scores": zscores_per_region[i],
        }
        if flags_per_region[i]:
            result.outliers.append(entry)
        else:
            result.normal.append(entry)

    return result


# ── Export: JSON ─────────────────────────────────────────────────────

def export_outlier_json(result, path):
    """Export outlier detection results as JSON.

    Parameters
    ----------
    result : OutlierResult
        From ``detect_outliers()``.
    path : str
        Output file path.
    """
    data = {
        "method": result.method,
        "threshold": result.threshold,
        "metrics": result.metrics_used,
        "total_regions": result.total_regions,
        "outlier_count": result.outlier_count,
        "outlier_rate": round(result.outlier_rate, 4),
        "summary": result.summary,
        "outliers": [
            {
                "region_index": o["region_index"],
                "seed_x": o["seed"][0],
                "seed_y": o["seed"][1],
                "flags": o["flags"],
                "values": o["values"],
                "z_scores": o["z_scores"],
            }
            for o in result.outliers
        ],
    }
    from vormap import validate_output_path
    path = validate_output_path(path, allow_absolute=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Export: SVG ──────────────────────────────────────────────────────

def export_outlier_svg(result, regions, data, path, width=800, height=600):
    """Export an SVG highlighting outlier regions in red.

    Parameters
    ----------
    result : OutlierResult
        From ``detect_outliers()``.
    regions : dict
        Output of ``compute_regions()``.
    data : list of (float, float)
        All seed points.
    path : str
        Output SVG file path.
    width : int
        SVG width in pixels (default 800).
    height : int
        SVG height in pixels (default 600).
    """
    # Determine bounds from all vertices
    all_x = []
    all_y = []
    for verts in regions.values():
        for v in verts:
            all_x.append(v[0])
            all_y.append(v[1])

    if not all_x:
        # Write empty SVG
        root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                          width=str(width), height=str(height))
        tree = ET.ElementTree(root)
        tree.write(path, encoding="unicode", xml_declaration=True)
        return

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)

    from vormap_geometry import SVGCoordinateTransform
    ct = SVGCoordinateTransform(
        (min_x, max_x), (min_y, max_y), width, height,
        margin=0, mode="stretch", pad_fraction=0.05)
    tx = ct.tx
    ty = ct.ty

    # Outlier seed set for fast lookup
    outlier_seeds = set()
    for o in result.outliers:
        outlier_seeds.add(o["seed"])

    # Build SVG
    root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                      width=str(width), height=str(height),
                      viewBox=f"0 0 {width} {height}")

    # Background
    ET.SubElement(root, "rect", x="0", y="0",
                  width=str(width), height=str(height),
                  fill="#0d1117")

    # Title
    title = ET.SubElement(root, "text",
                          x=str(width // 2), y="24",
                          fill="#c9d1d9", font_size="14",
                          text_anchor="middle",
                          font_family="sans-serif")
    title.text = (f"Spatial Outliers — {result.outlier_count}/"
                  f"{result.total_regions} flagged "
                  f"({result.method}, threshold={result.threshold})")

    # Draw regions
    for seed, verts in regions.items():
        if len(verts) < 3:
            continue

        is_outlier = seed in outlier_seeds
        points = " ".join(f"{tx(v[0]):.1f},{ty(v[1]):.1f}" for v in verts)

        fill = "#f8514980" if is_outlier else "#23883218"
        stroke = "#f85149" if is_outlier else "#30363d"
        stroke_w = "2" if is_outlier else "0.5"

        ET.SubElement(root, "polygon",
                      points=points,
                      fill=fill, stroke=stroke,
                      stroke_width=stroke_w)

    # Draw seed points
    for seed, verts in regions.items():
        is_outlier = seed in outlier_seeds
        cx, cy = tx(seed[0]), ty(seed[1])
        ET.SubElement(root, "circle",
                      cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                      r="4" if is_outlier else "2.5",
                      fill="#f85149" if is_outlier else "#58a6ff",
                      stroke="#0d1117", stroke_width="0.5")

    # Legend
    legend_y = height - 40
    ET.SubElement(root, "rect",
                  x="10", y=str(legend_y - 4),
                  width="200", height="30",
                  rx="6", fill="#161b22", stroke="#30363d",
                  stroke_width="1")
    ET.SubElement(root, "circle",
                  cx="25", cy=str(legend_y + 11),
                  r="5", fill="#f85149")
    label1 = ET.SubElement(root, "text",
                           x="35", y=str(legend_y + 15),
                           fill="#c9d1d9", font_size="11",
                           font_family="sans-serif")
    label1.text = "Outlier"
    ET.SubElement(root, "circle",
                  cx="100", cy=str(legend_y + 11),
                  r="4", fill="#58a6ff")
    label2 = ET.SubElement(root, "text",
                           x="110", y=str(legend_y + 15),
                           fill="#c9d1d9", font_size="11",
                           font_family="sans-serif")
    label2.text = "Normal"

    tree = ET.ElementTree(root)
    tree.write(path, encoding="unicode", xml_declaration=True)


# ── Export: CSV ──────────────────────────────────────────────────────

def export_outlier_csv(result, path):
    """Export outlier data as CSV for spreadsheet analysis.

    Parameters
    ----------
    result : OutlierResult
        From ``detect_outliers()``.
    path : str
        Output CSV file path.
    """
    all_regions = result.outliers + result.normal
    all_regions.sort(key=lambda r: r["region_index"])

    metrics = result.metrics_used
    header = ["region_index", "seed_x", "seed_y", "is_outlier"]
    for m in metrics:
        header.extend([m, f"{m}_zscore", f"{m}_flag"])

    lines = [",".join(header)]
    for r in all_regions:
        is_out = "1" if r["flags"] else "0"
        row = [
            str(r["region_index"]),
            f"{r['seed'][0]:.6f}",
            f"{r['seed'][1]:.6f}",
            is_out,
        ]
        for m in metrics:
            row.append(f"{r['values'].get(m, 0):.6f}")
            row.append(f"{r['z_scores'].get(m, 0):.3f}")
            row.append(r["flags"].get(m, ""))
        lines.append(",".join(row))

    from vormap import validate_output_path
    path = validate_output_path(path, allow_absolute=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
