"""Spatial data classification for choropleth mapping.

Provides standard cartographic classification methods for dividing
continuous data values into discrete classes.  These methods are the
building blocks for choropleth map visualization — every value in a
dataset gets assigned a class, and each class maps to a color.

Classification methods:

- **Natural Breaks (Jenks)**: minimizes within-class variance and
  maximizes between-class variance.  The gold standard for showing
  "natural" groupings in spatial data.
- **Quantile**: equal number of observations per class.  Good when
  you want each color to represent the same count of features.
- **Equal Interval**: uniform range per class.  Simple and intuitive,
  but can produce empty classes if data is skewed.
- **Standard Deviation**: classes based on distance from the mean.
  Useful for showing how values deviate from the average.
- **Head/Tail Breaks**: for heavy-tailed distributions (Jiang 2013).
  Recursively splits at the mean of values above the current mean.
  Ideal for power-law distributed spatial data (city sizes, etc).
- **Manual**: user-defined breakpoints for domain-specific classification.
- **Pretty Breaks**: rounds breakpoints to "pretty" numbers (multiples
  of 1, 2, 5, 10, etc) for clean legend labels.

Usage (as module)::

    import vormap_classify

    values = [12, 15, 22, 37, 38, 41, 55, 63, 67, 89, 90, 95]

    # Natural Breaks into 4 classes
    result = vormap_classify.classify(values, n_classes=4, method='jenks')
    print(result.breaks)        # [37, 55, 89]
    print(result.assignments)   # [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]

    # Quantile classification
    result = vormap_classify.classify(values, n_classes=3, method='quantile')

    # Standard Deviation
    result = vormap_classify.classify(values, method='stddev')

    # Head/Tail for skewed distributions
    result = vormap_classify.classify(values, method='headtail')

    # Get class summary statistics
    for c in result.class_summaries:
        print(f"Class {c['class']}: {c['count']} values, range [{c['min']}, {c['max']}]")

Usage (CLI)::

    python vormap_classify.py values.txt --method jenks --classes 5
    python vormap_classify.py data.csv --column population --method quantile
    python vormap_classify.py data.csv --method headtail --output classified.csv
    python vormap_classify.py data.csv --method stddev --svg histogram.svg
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ── Classification result ────────────────────────────────────────

@dataclass
class ClassificationResult:
    """Result of a classification operation.

    Attributes
    ----------
    method : str
        Classification method used.
    n_classes : int
        Number of classes produced.
    breaks : list of float
        Class breakpoints (n_classes - 1 values).  The i-th class
        contains values where ``breaks[i-1] <= v < breaks[i]``
        (with the first and last classes unbounded on their outer edge).
    assignments : list of int
        Class index (0-based) for each input value, in original order.
    class_summaries : list of dict
        Per-class statistics (count, min, max, mean, sum).
    gvf : float or None
        Goodness of Variance Fit (0-1).  Only computed for Jenks;
        1.0 = perfect fit.
    """

    method: str
    n_classes: int
    breaks: List[float]
    assignments: List[int]
    class_summaries: List[Dict[str, Any]] = field(default_factory=list)
    gvf: Optional[float] = None

    def label_for(self, class_idx: int) -> str:
        """Return a human-readable label for a class index."""
        if class_idx < 0 or class_idx >= self.n_classes:
            raise ValueError(f"class_idx {class_idx} out of range [0, {self.n_classes})")
        lo = self.breaks[class_idx - 1] if class_idx > 0 else None
        hi = self.breaks[class_idx] if class_idx < len(self.breaks) else None
        if lo is None and hi is not None:
            return f"< {hi:.4g}"
        if hi is None and lo is not None:
            return f"\u2265 {lo:.4g}"
        if lo is not None and hi is not None:
            return f"{lo:.4g} \u2013 {hi:.4g}"
        return "all"


# ── Validation ───────────────────────────────────────────────────

def _validate_values(values: Sequence[float]) -> List[float]:
    """Validate and clean input values."""
    cleaned: List[float] = []
    for v in values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        try:
            cleaned.append(float(v))
        except (TypeError, ValueError):
            continue
    if not cleaned:
        raise ValueError("No valid numeric values provided")
    return cleaned


# ── Classification methods ───────────────────────────────────────

def _classify_jenks(values: List[float], n_classes: int) -> Tuple[List[float], float]:
    """Natural Breaks (Fisher-Jenks) classification.

    Uses the Fisher-Jenks algorithm (dynamic programming) to find
    class breaks that minimize within-class variance.

    Returns (breaks, gvf) where gvf is Goodness of Variance Fit.
    """
    n = len(values)
    if n <= n_classes:
        return sorted(set(values))[:-1], 1.0

    sorted_vals = sorted(values)

    # DP tables
    lower_class_limits = [[0.0] * (n_classes + 1) for _ in range(n + 1)]
    variance_combinations = [[float('inf')] * (n_classes + 1) for _ in range(n + 1)]

    for i in range(1, n_classes + 1):
        lower_class_limits[1][i] = 1.0
        variance_combinations[1][i] = 0.0

    for l in range(2, n + 1):
        sum_val = 0.0
        sum_sq = 0.0
        for m in range(1, l + 1):
            lower_idx = l - m  # 0-based index into sorted_vals
            val = sorted_vals[lower_idx]
            sum_val += val
            sum_sq += val * val
            variance = sum_sq - (sum_val * sum_val) / m
            if lower_idx > 0:
                for j in range(2, n_classes + 1):
                    new_var = variance + variance_combinations[lower_idx][j - 1]
                    if new_var < variance_combinations[l][j]:
                        lower_class_limits[l][j] = lower_idx + 1
                        variance_combinations[l][j] = new_var
            else:
                lower_class_limits[l][1] = 1.0
                variance_combinations[l][1] = variance

    # Extract breaks
    k = n
    breaks: List[float] = []
    for j in range(n_classes, 1, -1):
        idx = int(lower_class_limits[k][j]) - 1
        if 0 <= idx < n:
            breaks.append(sorted_vals[idx])
        k = int(lower_class_limits[k][j]) - 1

    breaks = sorted(set(breaks))

    # Ensure we have at most n_classes - 1 breaks
    while len(breaks) >= n_classes:
        breaks.pop()

    # Goodness of Variance Fit
    overall_mean = sum(sorted_vals) / n
    sdam = sum((v - overall_mean) ** 2 for v in sorted_vals)
    if sdam == 0:
        gvf = 1.0
    else:
        assignments = _assign_classes(values, breaks)
        sdcm = 0.0
        for cls in range(len(breaks) + 1):
            cls_vals = [values[i] for i, a in enumerate(assignments) if a == cls]
            if cls_vals:
                cls_mean = sum(cls_vals) / len(cls_vals)
                sdcm += sum((v - cls_mean) ** 2 for v in cls_vals)
        gvf = 1.0 - sdcm / sdam

    return breaks, gvf


def _classify_quantile(values: List[float], n_classes: int) -> List[float]:
    """Quantile classification — equal count per class."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    breaks: List[float] = []
    for i in range(1, n_classes):
        idx = i * n / n_classes
        lower = int(math.floor(idx))
        upper = min(lower + 1, n - 1)
        frac = idx - lower
        bp = sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])
        breaks.append(bp)
    return sorted(set(breaks))


def _classify_equal_interval(values: List[float], n_classes: int) -> List[float]:
    """Equal Interval classification — uniform range per class."""
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return []
    step = (hi - lo) / n_classes
    return [lo + step * i for i in range(1, n_classes)]


def _classify_stddev(values: List[float], n_classes: int) -> List[float]:
    """Standard Deviation classification.

    Creates classes centered on the mean at intervals of 1 standard
    deviation.
    """
    n = len(values)
    avg = sum(values) / n
    variance = sum((v - avg) ** 2 for v in values) / n
    std = math.sqrt(variance) if variance > 0 else 0

    if std == 0:
        return []

    breaks: List[float] = []
    for i in range(-3, 4):
        bp = avg + i * std
        if min(values) < bp < max(values):
            breaks.append(round(bp, 6))

    while len(breaks) >= n_classes:
        if len(breaks) % 2 == 0:
            breaks.pop()
        else:
            breaks.pop(0)

    return breaks


def _classify_headtail(values: List[float]) -> List[float]:
    """Head/Tail Breaks (Jiang 2013) for heavy-tailed distributions.

    Recursively splits at the mean: values above the mean form the
    "head" and are recursively split; values at or below form the
    "tail".  Naturally adapts to the data's distribution.
    """
    breaks: List[float] = []

    def _recurse(vals: List[float], depth: int = 0) -> None:
        if len(vals) < 2 or depth > 20:
            return
        avg = sum(vals) / len(vals)
        head = [v for v in vals if v > avg]
        if not head or len(head) == len(vals):
            return
        ratio = len(head) / len(vals)
        if ratio > 0.4:
            return
        breaks.append(avg)
        _recurse(head, depth + 1)

    _recurse(sorted(values))
    return sorted(breaks)


def _classify_pretty(values: List[float], n_classes: int) -> List[float]:
    """Pretty Breaks — rounds to "nice" numbers for clean legends."""
    lo = min(values)
    hi = max(values)
    if lo == hi:
        return []

    data_range = hi - lo
    rough_step = data_range / n_classes

    magnitude = 10 ** math.floor(math.log10(rough_step))

    for nice in [1, 2, 2.5, 5, 10]:
        step = nice * magnitude
        start = math.floor(lo / step) * step
        breaks: List[float] = []
        bp = start + step
        while bp < hi:
            if bp > lo:
                breaks.append(round(bp, 10))
            bp += step
        if len(breaks) < n_classes:
            return breaks

    return _classify_equal_interval(values, n_classes)


def _classify_manual(values: List[float], breaks: List[float]) -> List[float]:
    """Manual classification with user-defined breakpoints."""
    return sorted(breaks)


# ── Class assignment ─────────────────────────────────────────────

def _assign_classes(values: List[float], breaks: List[float]) -> List[int]:
    """Assign each value to a class based on breakpoints."""
    assignments: List[int] = []
    for v in values:
        cls = 0
        for bp in breaks:
            if v >= bp:
                cls += 1
            else:
                break
        assignments.append(cls)
    return assignments


def _compute_summaries(
    values: List[float], assignments: List[int], n_classes: int,
) -> List[Dict[str, Any]]:
    """Compute per-class summary statistics."""
    summaries: List[Dict[str, Any]] = []
    for cls in range(n_classes):
        cls_vals = [values[i] for i, a in enumerate(assignments) if a == cls]
        if cls_vals:
            summaries.append({
                "class": cls,
                "count": len(cls_vals),
                "min": min(cls_vals),
                "max": max(cls_vals),
                "mean": round(sum(cls_vals) / len(cls_vals), 6),
                "sum": round(sum(cls_vals), 6),
            })
        else:
            summaries.append({
                "class": cls,
                "count": 0,
                "min": None,
                "max": None,
                "mean": None,
                "sum": 0,
            })
    return summaries


# ── Main API ─────────────────────────────────────────────────────

METHODS = ("jenks", "quantile", "equal_interval", "stddev", "headtail", "pretty", "manual")


def classify(
    values: Sequence[float],
    n_classes: int = 5,
    method: str = "jenks",
    breaks: Optional[List[float]] = None,
) -> ClassificationResult:
    """Classify values into discrete classes.

    Parameters
    ----------
    values : sequence of float
        Data values to classify.
    n_classes : int
        Desired number of classes (default 5).
    method : str
        Classification method.  One of: jenks, quantile,
        equal_interval, stddev, headtail, pretty, manual.
    breaks : list of float, optional
        Manual breakpoints (required when method='manual').

    Returns
    -------
    ClassificationResult
    """
    if method not in METHODS:
        raise ValueError(
            f"Unknown method '{method}'. Choose from: {', '.join(METHODS)}"
        )

    cleaned = _validate_values(values)

    if n_classes < 2:
        raise ValueError("n_classes must be >= 2")

    gvf: Optional[float] = None

    if method == "jenks":
        computed_breaks, gvf = _classify_jenks(cleaned, n_classes)
    elif method == "quantile":
        computed_breaks = _classify_quantile(cleaned, n_classes)
    elif method == "equal_interval":
        computed_breaks = _classify_equal_interval(cleaned, n_classes)
    elif method == "stddev":
        computed_breaks = _classify_stddev(cleaned, n_classes)
    elif method == "headtail":
        computed_breaks = _classify_headtail(cleaned)
    elif method == "pretty":
        computed_breaks = _classify_pretty(cleaned, n_classes)
    elif method == "manual":
        if breaks is None:
            raise ValueError("breaks parameter is required for method='manual'")
        computed_breaks = _classify_manual(cleaned, breaks)
    else:
        computed_breaks = []

    actual_n = len(computed_breaks) + 1
    assignments = _assign_classes(cleaned, computed_breaks)
    summaries = _compute_summaries(cleaned, assignments, actual_n)

    return ClassificationResult(
        method=method,
        n_classes=actual_n,
        breaks=computed_breaks,
        assignments=assignments,
        class_summaries=summaries,
        gvf=gvf,
    )


def compare_methods(
    values: Sequence[float],
    n_classes: int = 5,
    methods: Optional[Sequence[str]] = None,
) -> Dict[str, ClassificationResult]:
    """Run multiple classification methods and compare results."""
    if methods is None:
        methods = [m for m in METHODS if m != "manual"]

    results: Dict[str, ClassificationResult] = {}
    for m in methods:
        try:
            results[m] = classify(values, n_classes=n_classes, method=m)
        except Exception:
            continue
    return results


# ── SVG histogram ────────────────────────────────────────────────

def _classification_svg(
    result: ClassificationResult,
    values: List[float],
    width: int = 600,
    height: int = 300,
) -> str:
    """Generate an SVG histogram colored by classification."""
    n_bins = min(50, max(10, len(values) // 5))
    lo = min(values)
    hi = max(values)
    if lo == hi:
        hi = lo + 1
    bin_width = (hi - lo) / n_bins

    bins = [0] * n_bins
    for v in values:
        idx = min(int((v - lo) / bin_width), n_bins - 1)
        bins[idx] += 1

    max_count = max(bins) if bins else 1

    palette = [
        "#2166ac", "#67a9cf", "#d1e5f0", "#fddbc7",
        "#ef8a62", "#b2182b", "#7f3b08", "#543005",
    ]

    margin_l = 50
    margin_b = 30
    margin_t = 20
    margin_r = 10
    chart_w = width - margin_l - margin_r
    chart_h = height - margin_t - margin_b
    bar_w = chart_w / n_bins

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" '
        f'style="background:#1a1a2e; font-family:sans-serif;">',
    ]

    for i in range(n_bins):
        bin_mid = lo + (i + 0.5) * bin_width
        cls = 0
        for bp in result.breaks:
            if bin_mid >= bp:
                cls += 1
            else:
                break
        color = palette[cls % len(palette)]
        bar_h = (bins[i] / max_count) * chart_h if max_count > 0 else 0
        x = margin_l + i * bar_w
        y = margin_t + chart_h - bar_h
        lines.append(
            f'  <rect x="{x:.1f}" y="{y:.1f}" '
            f'width="{bar_w - 1:.1f}" height="{bar_h:.1f}" '
            f'fill="{color}" opacity="0.85"/>'
        )

    for bp in result.breaks:
        x = margin_l + ((bp - lo) / (hi - lo)) * chart_w
        lines.append(
            f'  <line x1="{x:.1f}" y1="{margin_t}" '
            f'x2="{x:.1f}" y2="{margin_t + chart_h}" '
            f'stroke="#ffffff" stroke-width="1.5" stroke-dasharray="4,3"/>'
        )
        lines.append(
            f'  <text x="{x:.1f}" y="{margin_t - 4}" '
            f'text-anchor="middle" fill="#ffffff" font-size="9">'
            f'{bp:.4g}</text>'
        )

    lines.append(
        f'  <line x1="{margin_l}" y1="{margin_t + chart_h}" '
        f'x2="{margin_l + chart_w}" y2="{margin_t + chart_h}" '
        f'stroke="#888" stroke-width="1"/>'
    )
    lines.append(
        f'  <line x1="{margin_l}" y1="{margin_t}" '
        f'x2="{margin_l}" y2="{margin_t + chart_h}" '
        f'stroke="#888" stroke-width="1"/>'
    )

    for i in range(5):
        val = lo + (hi - lo) * i / 4
        x = margin_l + chart_w * i / 4
        lines.append(
            f'  <text x="{x:.1f}" y="{margin_t + chart_h + 15}" '
            f'text-anchor="middle" fill="#aaa" font-size="10">{val:.4g}</text>'
        )

    lines.append(
        f'  <text x="{width / 2}" y="{margin_t - 4}" text-anchor="middle" '
        f'fill="#eee" font-size="11">'
        f'{result.method} \u2014 {result.n_classes} classes'
        f'{"  (GVF=" + f"{result.gvf:.3f}" + ")" if result.gvf is not None else ""}'
        f'</text>'
    )

    lines.append("</svg>")
    return "\n".join(lines)


# ── CSV export ───────────────────────────────────────────────────

def export_csv(
    values: Sequence[float],
    result: ClassificationResult,
    output_path: str,
) -> None:
    """Export classification results to CSV."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["value", "class", "label"])
        for val, cls in zip(values, result.assignments):
            writer.writerow([val, cls, result.label_for(cls)])


# ── CLI ──────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="vormap_classify",
        description="Classify spatial data values for choropleth mapping",
    )
    parser.add_argument("input", help="Input file (one value per line, or CSV)")
    parser.add_argument("--method", "-m", choices=list(METHODS), default="jenks")
    parser.add_argument("--classes", "-k", type=int, default=5)
    parser.add_argument("--column", "-c", help="CSV column name")
    parser.add_argument("--breaks", help="Manual breakpoints (comma-separated)")
    parser.add_argument("--output", "-o", help="Output CSV file")
    parser.add_argument("--svg", help="Output SVG histogram file")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    values: List[float] = []
    suffix = input_path.suffix.lower()

    if suffix == ".csv" or args.column:
        with open(input_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            col = args.column
            if col is None:
                row = next(reader)
                for k, v in row.items():
                    try:
                        float(v)
                        col = k
                        values.append(float(v))
                        break
                    except (TypeError, ValueError):
                        continue
            for row in reader:
                try:
                    values.append(float(row[col]))
                except (TypeError, ValueError, KeyError):
                    continue
    elif suffix == ".json":
        with open(input_path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for v in data:
                try:
                    values.append(float(v))
                except (TypeError, ValueError):
                    continue
    else:
        with open(input_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    values.append(float(line.split()[0]))
                except (ValueError, IndexError):
                    continue

    if not values:
        print("Error: no valid numeric values found", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(values)} values (range: {min(values):.4g} \u2013 {max(values):.4g})")

    manual_breaks = None
    if args.breaks:
        manual_breaks = [float(x.strip()) for x in args.breaks.split(",")]

    if args.compare:
        results = compare_methods(values, n_classes=args.classes)
        for method_name, res in results.items():
            print(f"\n{'=' * 50}")
            print(f"  {method_name.upper()}: {res.n_classes} classes")
            print(f"  Breaks: {[round(b, 4) for b in res.breaks]}")
            if res.gvf is not None:
                print(f"  GVF: {res.gvf:.4f}")
            for s in res.class_summaries:
                print(f"    Class {s['class']}: n={s['count']}, "
                      f"range=[{s['min']}, {s['max']}]")
    else:
        result = classify(
            values, n_classes=args.classes, method=args.method, breaks=manual_breaks,
        )

        if args.json:
            out = {
                "method": result.method,
                "n_classes": result.n_classes,
                "breaks": [round(b, 6) for b in result.breaks],
                "gvf": round(result.gvf, 4) if result.gvf is not None else None,
                "summaries": result.class_summaries,
            }
            print(json.dumps(out, indent=2))
        else:
            print(f"\nMethod: {result.method}")
            print(f"Classes: {result.n_classes}")
            print(f"Breaks: {[round(b, 4) for b in result.breaks]}")
            if result.gvf is not None:
                print(f"GVF: {result.gvf:.4f}")
            print()
            for s in result.class_summaries:
                label = result.label_for(s["class"])
                print(f"  Class {s['class']} [{label}]: "
                      f"n={s['count']}, range=[{s['min']}, {s['max']}], "
                      f"mean={s['mean']}")

        if args.output:
            export_csv(values, result, args.output)
            print(f"\nClassified data written to {args.output}")

        if args.svg:
            cleaned = _validate_values(values)
            svg = _classification_svg(result, cleaned)
            Path(args.svg).write_text(svg, encoding="utf-8")
            print(f"Histogram SVG written to {args.svg}")


if __name__ == "__main__":
    main()
