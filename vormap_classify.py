"""Voronoi Data Classification — thematic map classification methods.

Assigns Voronoi cell attribute values to discrete classes for choropleth
and thematic mapping.  Implements the standard classification methods used
in GIS and cartography.

**Classification methods:**

- **equal_interval** — divides the value range into *k* equal-width bins.
- **quantile** — each class contains approximately the same number of observations.
- **natural_breaks** — Fisher-Jenks optimisation (minimises within-class variance).
- **std_deviation** — classes at standard deviation intervals around the mean.
- **head_tail** — recursive head/tail breaks for heavy-tailed distributions (Jiang 2013).
- **manual** — user-supplied break values.
- **pretty** — breaks on round numbers.

**Features:**

- Per-class statistics (count, min, max, mean, range, % of total).
- Goodness-of-Variance Fit (GVF) score for evaluating classification quality.
- SVG legend generation for map integration.
- JSON and CSV export.
- CLI with all methods and options.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_classify import classify, format_report, export_svg_legend

    data = vormap.load_data("sites.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)
    values = [s["area"] for s in stats]

    result = classify(values, method="natural_breaks", k=5)
    print(format_report(result))
    export_svg_legend(result, "legend.svg")

Usage (CLI)::

    python vormap_classify.py sites.txt --method natural_breaks --k 5
    python vormap_classify.py sites.txt --method quantile --k 4 --export classes.json
    python vormap_classify.py sites.txt --method std_deviation --export classes.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Sequence


@dataclass
class ClassInfo:
    """A single classification class/bin."""
    class_id: int
    lower: float
    upper: float
    count: int
    values: List[float] = field(default_factory=list)

    @property
    def range(self) -> float:
        return self.upper - self.lower

    @property
    def mean(self) -> float:
        return sum(self.values) / len(self.values) if self.values else 0.0


@dataclass
class ClassificationResult:
    """Complete classification output."""
    method: str
    k: int
    breaks: List[float]
    classes: List[ClassInfo]
    total_count: int
    global_min: float
    global_max: float
    global_mean: float
    global_std: float
    gvf: float

    def class_for_value(self, value: float) -> int:
        for c in self.classes:
            if c.lower <= value <= c.upper:
                return c.class_id
        if value <= self.global_min:
            return 0
        return len(self.classes) - 1

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "k": self.k,
            "breaks": self.breaks,
            "gvf": round(self.gvf, 6),
            "global_min": self.global_min,
            "global_max": self.global_max,
            "global_mean": round(self.global_mean, 6),
            "global_std": round(self.global_std, 6),
            "total_count": self.total_count,
            "classes": [
                {
                    "class_id": c.class_id,
                    "lower": round(c.lower, 6),
                    "upper": round(c.upper, 6),
                    "count": c.count,
                    "pct": round(c.count / self.total_count * 100, 2) if self.total_count else 0,
                    "mean": round(c.mean, 6),
                    "range": round(c.range, 6),
                }
                for c in self.classes
            ],
        }


def _mean(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _variance(vals: Sequence[float], mu: Optional[float] = None) -> float:
    if len(vals) < 2:
        return 0.0
    if mu is None:
        mu = _mean(vals)
    return sum((v - mu) ** 2 for v in vals) / len(vals)


def _std(vals: Sequence[float], mu: Optional[float] = None) -> float:
    return math.sqrt(_variance(vals, mu))


def _sdam(vals: Sequence[float]) -> float:
    mu = _mean(vals)
    return sum((v - mu) ** 2 for v in vals)


def _sdcm(classes: List[ClassInfo]) -> float:
    total = 0.0
    for c in classes:
        if c.values:
            mu = c.mean
            total += sum((v - mu) ** 2 for v in c.values)
    return total


def _gvf(classes: List[ClassInfo], all_values: Sequence[float]) -> float:
    sdam = _sdam(all_values)
    if sdam == 0:
        return 1.0
    return 1.0 - _sdcm(classes) / sdam


def _build_classes(sorted_vals: List[float], breaks: List[float]) -> List[ClassInfo]:
    classes = []
    for i in range(len(breaks) - 1):
        lower = breaks[i]
        upper = breaks[i + 1]
        if i == 0:
            vals = [v for v in sorted_vals if lower <= v <= upper]
        else:
            vals = [v for v in sorted_vals if lower < v <= upper]
        classes.append(ClassInfo(class_id=i, lower=lower, upper=upper,
                                 count=len(vals), values=vals))
    return classes


def _equal_interval(sorted_vals: List[float], k: int) -> List[float]:
    lo, hi = sorted_vals[0], sorted_vals[-1]
    if lo == hi:
        return [lo, hi]
    step = (hi - lo) / k
    return [lo + i * step for i in range(k)] + [hi]


def _quantile(sorted_vals: List[float], k: int) -> List[float]:
    n = len(sorted_vals)
    breaks = [sorted_vals[0]]
    for i in range(1, k):
        idx = int(round(i * n / k)) - 1
        idx = max(0, min(idx, n - 1))
        val = sorted_vals[idx]
        if val <= breaks[-1]:
            for j in range(idx, n):
                if sorted_vals[j] > breaks[-1]:
                    val = sorted_vals[j]
                    break
            else:
                val = breaks[-1] + 1e-10
        breaks.append(val)
    breaks.append(sorted_vals[-1])
    return breaks


def _natural_breaks(sorted_vals: List[float], k: int) -> List[float]:
    """Fisher-Jenks natural breaks (dynamic programming)."""
    n = len(sorted_vals)
    if n <= k:
        breaks = [sorted_vals[0]]
        for v in sorted_vals[1:]:
            breaks.append(v)
        return breaks

    mat = [[float("inf")] * k for _ in range(n)]
    bt = [[0] * k for _ in range(n)]

    running_sum = 0.0
    running_sq = 0.0
    for i in range(n):
        running_sum += sorted_vals[i]
        running_sq += sorted_vals[i] ** 2
        mat[i][0] = running_sq - (running_sum ** 2) / (i + 1)
        bt[i][0] = 0

    for j in range(1, k):
        for i in range(j, n):
            best = float("inf")
            best_bt = j
            s = 0.0
            sq = 0.0
            for m in range(i, j - 1, -1):
                s += sorted_vals[m]
                sq += sorted_vals[m] ** 2
                cnt = i - m + 1
                var = sq - (s ** 2) / cnt
                cost = mat[m - 1][j - 1] + var if m > 0 else var
                if cost < best:
                    best = cost
                    best_bt = m
            mat[i][j] = best
            bt[i][j] = best_bt

    breaks_idx = []
    j = k - 1
    i = n - 1
    while j > 0:
        breaks_idx.append(bt[i][j])
        i = bt[i][j] - 1
        j -= 1
    breaks_idx.reverse()

    breaks = [sorted_vals[0]]
    for idx in breaks_idx:
        if idx > 0:
            breaks.append(sorted_vals[idx - 1])
    breaks.append(sorted_vals[-1])

    while len(breaks) < k + 1:
        breaks.insert(-1, breaks[-1])
    return breaks


def _std_deviation(sorted_vals: List[float], k: int) -> List[float]:
    mu = _mean(sorted_vals)
    sd = _std(sorted_vals, mu)
    if sd == 0:
        return [sorted_vals[0], sorted_vals[-1]]
    half = k // 2
    breaks = [mu + i * sd for i in range(-half, half + 1)]
    breaks[0] = min(breaks[0], sorted_vals[0])
    breaks[-1] = max(breaks[-1], sorted_vals[-1])
    result = [breaks[0]]
    for b in breaks[1:]:
        if b > result[-1]:
            result.append(b)
    if result[-1] < sorted_vals[-1]:
        result.append(sorted_vals[-1])
    return result


def _head_tail(sorted_vals: List[float], max_classes: int = 10) -> List[float]:
    """Head/tail breaks (Jiang 2013) for heavy-tailed distributions."""
    breaks = [sorted_vals[0]]

    def _recurse(vals, depth):
        if len(vals) < 2 or depth >= max_classes - 1:
            return
        mu = _mean(vals)
        if mu <= vals[0] or mu >= vals[-1]:
            return
        head = [v for v in vals if v > mu]
        if not head or len(head) == len(vals):
            return
        breaks.append(mu)
        _recurse(head, depth + 1)

    _recurse(sorted_vals, 0)
    breaks.append(sorted_vals[-1])
    result = [breaks[0]]
    for b in breaks[1:]:
        if b > result[-1]:
            result.append(b)
    if len(result) < 2:
        result.append(result[0])
    return result


def _pretty_breaks(sorted_vals: List[float], k: int) -> List[float]:
    lo, hi = sorted_vals[0], sorted_vals[-1]
    raw_step = (hi - lo) / k
    if raw_step == 0:
        return [lo, hi]
    mag = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / mag
    if residual <= 1.5:
        nice_step = mag
    elif residual <= 3:
        nice_step = 2 * mag
    elif residual <= 7:
        nice_step = 5 * mag
    else:
        nice_step = 10 * mag
    nice_lo = math.floor(lo / nice_step) * nice_step
    nice_hi = math.ceil(hi / nice_step) * nice_step
    breaks = []
    v = nice_lo
    while v <= nice_hi + nice_step * 0.01:
        breaks.append(v)
        v += nice_step
    if breaks[0] > sorted_vals[0]:
        breaks[0] = sorted_vals[0]
    if breaks[-1] < sorted_vals[-1]:
        breaks[-1] = sorted_vals[-1]
    return breaks


METHODS = {
    "equal_interval": _equal_interval,
    "quantile": _quantile,
    "natural_breaks": _natural_breaks,
    "std_deviation": _std_deviation,
    "head_tail": _head_tail,
    "pretty": _pretty_breaks,
}


def classify(
    values: Sequence[float],
    method: str = "natural_breaks",
    k: int = 5,
    manual_breaks: Optional[List[float]] = None,
) -> ClassificationResult:
    if not values:
        raise ValueError("Cannot classify empty values")
    vals = sorted(float(v) for v in values)
    n = len(vals)
    k = max(2, min(k, n))

    if method == "manual":
        if not manual_breaks or len(manual_breaks) < 2:
            raise ValueError("manual_breaks must have at least 2 values")
        breaks = sorted(manual_breaks)
        if breaks[0] > vals[0]:
            breaks[0] = vals[0]
        if breaks[-1] < vals[-1]:
            breaks[-1] = vals[-1]
    elif method == "head_tail":
        breaks = _head_tail(vals, max_classes=k)
    elif method in METHODS:
        breaks = METHODS[method](vals, k)
    else:
        raise ValueError(f"Unknown method: {method}. Choose from: {', '.join(METHODS)}, manual")

    classes = _build_classes(vals, breaks)
    mu = _mean(vals)
    sd = _std(vals, mu)
    gvf_score = _gvf(classes, vals)

    return ClassificationResult(
        method=method, k=len(classes), breaks=breaks, classes=classes,
        total_count=n, global_min=vals[0], global_max=vals[-1],
        global_mean=mu, global_std=sd, gvf=gvf_score,
    )


def compare_methods(
    values: Sequence[float], k: int = 5,
    methods: Optional[List[str]] = None,
) -> List[ClassificationResult]:
    if methods is None:
        methods = list(METHODS.keys())
    results = []
    for m in methods:
        try:
            results.append(classify(values, method=m, k=k))
        except Exception:
            pass
    results.sort(key=lambda r: r.gvf, reverse=True)
    return results


def format_report(result: ClassificationResult) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append(f"  DATA CLASSIFICATION REPORT -- {result.method.upper()}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Method:          {result.method}")
    lines.append(f"  Classes:         {result.k}")
    lines.append(f"  Total values:    {result.total_count}")
    lines.append(f"  Range:           {result.global_min:.4f} - {result.global_max:.4f}")
    lines.append(f"  Mean:            {result.global_mean:.4f}")
    lines.append(f"  Std deviation:   {result.global_std:.4f}")
    lines.append(f"  GVF:             {result.gvf:.4f}")
    lines.append("")
    lines.append(f"  Breaks: {', '.join(f'{b:.4f}' for b in result.breaks)}")
    lines.append("")
    lines.append("  +----------+-------------------------------+-------+--------+----------+")
    lines.append("  |  Class   |         Range                 | Count |   %    |   Mean   |")
    lines.append("  +----------+-------------------------------+-------+--------+----------+")
    for c in result.classes:
        pct = c.count / result.total_count * 100 if result.total_count else 0
        lines.append(
            f"  |   {c.class_id:>3}    | {c.lower:>12.4f} - {c.upper:<12.4f}  | {c.count:>5} | {pct:>5.1f}% | {c.mean:>8.4f} |"
        )
    lines.append("  +----------+-------------------------------+-------+--------+----------+")
    lines.append("")
    gvf_pct = int(result.gvf * 20)
    gvf_bar = "#" * gvf_pct + "." * (20 - gvf_pct)
    lines.append(f"  GVF: [{gvf_bar}] {result.gvf:.2%}")
    quality = "Excellent" if result.gvf >= 0.9 else "Good" if result.gvf >= 0.8 else "Fair" if result.gvf >= 0.7 else "Poor"
    lines.append(f"  Classification quality: {quality}")
    lines.append("")
    return "\n".join(lines)


def format_comparison_report(results: List[ClassificationResult]) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("  METHOD COMPARISON REPORT")
    lines.append("=" * 60)
    lines.append("")
    lines.append("  +------------------+-----+--------+---------+")
    lines.append("  |     Method       |  k  |  GVF   | Quality |")
    lines.append("  +------------------+-----+--------+---------+")
    for r in results:
        stars = 4 if r.gvf >= 0.9 else 3 if r.gvf >= 0.8 else 2 if r.gvf >= 0.7 else 1
        quality = "*" * stars + " " * (4 - stars)
        lines.append(f"  | {r.method:<16} | {r.k:>3} | {r.gvf:.4f} | {quality} |")
    lines.append("  +------------------+-----+--------+---------+")
    lines.append("")
    if results:
        lines.append(f"  Best method: {results[0].method} (GVF = {results[0].gvf:.4f})")
    lines.append("")
    return "\n".join(lines)


def export_json(result: ClassificationResult, path: str) -> str:
    with open(path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return path


def export_csv(result: ClassificationResult, path: str) -> str:
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["class_id", "lower", "upper", "count", "pct", "mean", "range"])
        for c in result.classes:
            pct = c.count / result.total_count * 100 if result.total_count else 0
            writer.writerow([c.class_id, round(c.lower, 6), round(c.upper, 6),
                             c.count, round(pct, 2), round(c.mean, 6), round(c.range, 6)])
    return path


_CLASS_COLORS = [
    "#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0",
    "#225ea8", "#253494", "#081d58", "#0d0887", "#46039f",
]


def export_svg_legend(
    result: ClassificationResult, path: str,
    title: str = "Classification Legend",
    colors: Optional[List[str]] = None,
) -> str:
    if colors is None:
        colors = _CLASS_COLORS
    box_w, box_h = 24, 18
    padding = 10
    text_offset = 32
    row_height = 24
    width = 300
    height = padding * 2 + row_height * len(result.classes) + 30
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height))
    t = ET.SubElement(svg, "text", x=str(padding), y="20",
                      fill="#333", style="font:bold 13px sans-serif")
    t.text = title
    for i, c in enumerate(result.classes):
        y = 35 + i * row_height
        color = colors[i % len(colors)]
        ET.SubElement(svg, "rect", x=str(padding), y=str(y),
                      width=str(box_w), height=str(box_h), fill=color, stroke="#999")
        pct = c.count / result.total_count * 100 if result.total_count else 0
        label = f"{c.lower:.2f} - {c.upper:.2f}  ({c.count}, {pct:.1f}%)"
        t = ET.SubElement(svg, "text", x=str(padding + text_offset),
                          y=str(y + 14), fill="#333", style="font:11px sans-serif")
        t.text = label
    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(path, xml_declaration=True, encoding="unicode")
    return path


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Classify Voronoi cell data for thematic mapping",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python vormap_classify.py sites.txt --method natural_breaks --k 5\n"
            "  python vormap_classify.py sites.txt --method quantile --k 4 --export out.json\n"
            "  python vormap_classify.py sites.txt --compare\n"
        ),
    )
    parser.add_argument("input", help="Input points file")
    parser.add_argument("--method", default="natural_breaks",
                        choices=list(METHODS.keys()) + ["manual"],
                        help="Classification method (default: natural_breaks)")
    parser.add_argument("--k", type=int, default=5, help="Number of classes (default: 5)")
    parser.add_argument("--metric", default="area",
                        choices=["area", "density", "compactness"],
                        help="Cell metric to classify (default: area)")
    parser.add_argument("--breaks", type=float, nargs="+",
                        help="Manual break values (for method=manual)")
    parser.add_argument("--compare", action="store_true",
                        help="Compare all methods side-by-side")
    parser.add_argument("--export", metavar="PATH",
                        help="Export to JSON (.json) or CSV (.csv)")
    parser.add_argument("--legend", metavar="PATH", help="Export SVG legend")
    args = parser.parse_args(argv)

    import vormap as vm
    import vormap_viz
    data = vm.load_data(args.input)
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)
    metric_map = {
        "area": lambda s: s["area"],
        "density": lambda s: s.get("density", s["area"]),
        "compactness": lambda s: s.get("compactness", 1.0),
    }
    values = [metric_map[args.metric](s) for s in stats]

    if args.compare:
        results = compare_methods(values, k=args.k)
        print(format_comparison_report(results))
        for r in results:
            print(format_report(r))
        return

    result = classify(values, method=args.method, k=args.k, manual_breaks=args.breaks)
    print(format_report(result))

    if args.export:
        if args.export.endswith(".json"):
            export_json(result, args.export)
            print(f"  Exported JSON -> {args.export}")
        elif args.export.endswith(".csv"):
            export_csv(result, args.export)
            print(f"  Exported CSV -> {args.export}")
    if args.legend:
        export_svg_legend(result, args.legend)
        print(f"  Exported SVG legend -> {args.legend}")


if __name__ == "__main__":
    main()
