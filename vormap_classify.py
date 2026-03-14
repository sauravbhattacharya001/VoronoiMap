"""Spatial classification of Voronoi regions.

Assigns categorical labels to Voronoi cells based on geometric metrics
(area, compactness, perimeter, vertex count, neighbor count) and spatial
relationships.  Supports rule-based classification, automatic breaks
(natural breaks / equal interval / quantile), and neighbor-aware spatial
smoothing.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_classify import (
        classify_by_rules, classify_by_breaks, spatial_smooth,
        ClassificationResult, export_classified_svg,
    )

    data = vormap.load_data("points.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    # Rule-based
    rules = [
        {"label": "large", "metric": "area", "op": ">", "value": 50000},
        {"label": "compact", "metric": "compactness", "op": ">", "value": 0.7},
        {"label": "small", "metric": "area", "op": "<", "value": 5000},
    ]
    result = classify_by_rules(stats, rules)

    # Natural breaks
    result = classify_by_breaks(stats, metric="area", n_classes=4)

    # Spatial smoothing (majority vote from neighbors)
    smoothed = spatial_smooth(result, regions, iterations=2)

    export_classified_svg(result, regions, data, "classified.svg")

CLI::

    voronoimap points.txt 5 --classify-rules rules.json
    voronoimap points.txt 5 --classify-breaks area --n-classes 4
    voronoimap points.txt 5 --classify-breaks area --method quantile
"""

import json
import math
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field


# -- Result container --

@dataclass
class ClassificationResult:
    """Container for classification results.

    Attributes
    ----------
    labels : list of str
        Label for each region (1-based indexing: labels[0] is region 1).
    label_set : list of str
        Unique labels in order of assignment.
    region_details : list of dict
        Per-region details: region_index, seed, label, metric_values.
    method : str
        Classification method used.
    config : dict
        Configuration used for classification.
    summary : dict
        Per-label summary: count, percentage, mean metric values.
    """
    labels: list = field(default_factory=list)
    label_set: list = field(default_factory=list)
    region_details: list = field(default_factory=list)
    method: str = ""
    config: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)


# -- Metric extraction --

def _extract_metric(stats, metric_name):
    """Extract a metric array from region stats.

    Parameters
    ----------
    stats : list of dict
        Region statistics (from vormap_viz.compute_region_stats).
    metric_name : str
        One of: area, compactness, perimeter, vertices, neighbors,
        centroid_x, centroid_y.

    Returns
    -------
    list of float
    """
    values = []
    for s in stats:
        if metric_name == "area":
            values.append(s.get("area", 0.0))
        elif metric_name == "compactness":
            values.append(s.get("compactness", 0.0))
        elif metric_name == "perimeter":
            values.append(s.get("perimeter", 0.0))
        elif metric_name == "vertices":
            values.append(float(s.get("num_vertices", 0)))
        elif metric_name == "neighbors":
            values.append(float(len(s.get("neighbors", []))))
        elif metric_name == "centroid_x":
            cx, _ = s.get("centroid", (0.0, 0.0))
            values.append(cx)
        elif metric_name == "centroid_y":
            _, cy = s.get("centroid", (0.0, 0.0))
            values.append(cy)
        else:
            values.append(s.get(metric_name, 0.0))
    return values


def _mean(vals):
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _std(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals))


# -- Comparison operators --

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: abs(a - b) < 1e-9,
    "!=": lambda a, b: abs(a - b) >= 1e-9,
    "between": lambda a, b: b[0] <= a <= b[1] if isinstance(b, (list, tuple)) else False,
}


def _apply_op(op_name, value, threshold):
    """Apply a comparison operator."""
    fn = _OPS.get(op_name)
    if fn is None:
        raise ValueError(f"Unknown operator: {op_name!r}. "
                         f"Valid: {', '.join(_OPS.keys())}")
    return fn(value, threshold)


# -- Rule-based classification --

def classify_by_rules(stats, rules, *, default_label="unclassified"):
    """Classify regions by a list of rules (first match wins).

    Parameters
    ----------
    stats : list of dict
        Region statistics.
    rules : list of dict
        Each rule has: label (str), metric (str), op (str), value (number).
        Optional: priority (int, lower = higher priority).
    default_label : str
        Label for regions matching no rule.

    Returns
    -------
    ClassificationResult
    """
    if not stats:
        return ClassificationResult(method="rules", config={"rules": rules})

    # Sort rules by priority if present
    sorted_rules = sorted(rules, key=lambda r: r.get("priority", 0))

    labels = []
    details = []
    label_set_ordered = []

    for i, s in enumerate(stats):
        matched_label = default_label
        for rule in sorted_rules:
            metric = rule["metric"]
            op = rule["op"]
            threshold = rule["value"]
            val = _extract_metric([s], metric)[0]
            if _apply_op(op, val, threshold):
                matched_label = rule["label"]
                break

        labels.append(matched_label)
        if matched_label not in label_set_ordered:
            label_set_ordered.append(matched_label)

        detail = {
            "region_index": i + 1,
            "seed": s.get("seed", (0, 0)),
            "label": matched_label,
        }
        details.append(detail)

    # Build summary
    summary = _build_summary(labels, stats, label_set_ordered)

    return ClassificationResult(
        labels=labels,
        label_set=label_set_ordered,
        region_details=details,
        method="rules",
        config={"rules": rules, "default_label": default_label},
        summary=summary,
    )


# -- Break-based classification --

def _natural_breaks(values, n_classes):
    """Jenks natural breaks optimization (1D).

    Fisher's exact algorithm for small n, greedy for large n.
    Returns n_classes - 1 break values.
    """
    if n_classes <= 1:
        return []
    if n_classes >= len(values):
        return sorted(set(values))[:-1] if values else []

    sorted_vals = sorted(values)
    n = len(sorted_vals)

    # Initialize matrices
    lower_class_limits = [[0.0] * (n_classes + 1) for _ in range(n + 1)]
    variance_combinations = [[float('inf')] * (n_classes + 1) for _ in range(n + 1)]

    for i in range(1, n_classes + 1):
        lower_class_limits[1][i] = 1.0
        variance_combinations[1][i] = 0.0

    for l in range(2, n + 1):  # noqa: E741
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
                    candidate = variance + variance_combinations[lower_idx][j - 1]
                    if candidate < variance_combinations[l][j]:
                        lower_class_limits[l][j] = lower_idx + 1
                        variance_combinations[l][j] = candidate

        lower_class_limits[l][1] = 1.0
        variance_combinations[l][1] = variance

    # Extract breaks
    breaks = []
    k = n
    for j in range(n_classes, 1, -1):
        lower = int(lower_class_limits[k][j]) - 1
        if 0 <= lower < n:
            breaks.append(sorted_vals[lower])
        k = int(lower_class_limits[k][j]) - 1

    breaks.sort()
    # Deduplicate
    unique_breaks = []
    for b in breaks:
        if not unique_breaks or abs(b - unique_breaks[-1]) > 1e-12:
            unique_breaks.append(b)

    return unique_breaks


def _equal_interval_breaks(values, n_classes):
    """Equal interval breaks."""
    if not values or n_classes <= 1:
        return []
    mn, mx = min(values), max(values)
    if abs(mx - mn) < 1e-12:
        return []
    step = (mx - mn) / n_classes
    return [mn + step * i for i in range(1, n_classes)]


def _quantile_breaks(values, n_classes):
    """Quantile breaks."""
    if not values or n_classes <= 1:
        return []
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    breaks = []
    for i in range(1, n_classes):
        idx = int(round(i * n / n_classes))
        idx = min(idx, n - 1)
        val = sorted_vals[idx]
        if not breaks or abs(val - breaks[-1]) > 1e-12:
            breaks.append(val)
    return breaks


def _std_dev_breaks(values, n_classes):
    """Standard deviation breaks centered on mean."""
    if not values or n_classes <= 1:
        return []
    m = _mean(values)
    s = _std(values)
    if s < 1e-12:
        return []
    half = n_classes // 2
    breaks = []
    for i in range(-half, half + 1):
        if i != 0 or n_classes % 2 == 1:
            breaks.append(m + i * s)
    # Keep only n_classes - 1 breaks
    breaks.sort()
    while len(breaks) >= n_classes:
        # Remove outermost
        if len(breaks) > n_classes - 1:
            breaks = breaks[1:] if len(breaks) % 2 == 0 else breaks[:-1]
        else:
            break
    return breaks[:n_classes - 1]


_BREAK_METHODS = {
    "natural": _natural_breaks,
    "equal": _equal_interval_breaks,
    "quantile": _quantile_breaks,
    "stddev": _std_dev_breaks,
}


def classify_by_breaks(stats, metric="area", n_classes=4,
                       method="natural", labels=None):
    """Classify regions into classes using break methods.

    Parameters
    ----------
    stats : list of dict
        Region statistics.
    metric : str
        Metric to classify on.
    n_classes : int
        Number of classes (2-10).
    method : str
        Break method: natural, equal, quantile, stddev.
    labels : list of str or None
        Custom labels for each class.  If None, auto-generated
        (e.g., "class_1", "class_2", ...).

    Returns
    -------
    ClassificationResult
    """
    if n_classes < 2:
        raise ValueError("n_classes must be >= 2")
    if n_classes > 10:
        raise ValueError("n_classes must be <= 10")

    break_fn = _BREAK_METHODS.get(method)
    if break_fn is None:
        raise ValueError(f"Unknown break method: {method!r}. "
                         f"Valid: {', '.join(_BREAK_METHODS.keys())}")

    if not stats:
        return ClassificationResult(method=f"breaks_{method}",
                                    config={"metric": metric, "n_classes": n_classes})

    values = _extract_metric(stats, metric)
    breaks = break_fn(values, n_classes)

    # Generate labels
    actual_classes = len(breaks) + 1
    if labels:
        if len(labels) < actual_classes:
            labels = labels + [f"class_{i+1}" for i in range(len(labels), actual_classes)]
        class_labels = labels[:actual_classes]
    else:
        class_labels = [f"class_{i+1}" for i in range(actual_classes)]

    # Assign labels
    region_labels = []
    details = []
    for i, val in enumerate(values):
        cls_idx = 0
        for j, brk in enumerate(breaks):
            if val >= brk:
                cls_idx = j + 1
        label = class_labels[min(cls_idx, len(class_labels) - 1)]
        region_labels.append(label)
        details.append({
            "region_index": i + 1,
            "seed": stats[i].get("seed", (0, 0)),
            "label": label,
            "metric_value": val,
        })

    summary = _build_summary(region_labels, stats, class_labels)
    summary["breaks"] = breaks
    summary["metric"] = metric
    summary["break_method"] = method

    return ClassificationResult(
        labels=region_labels,
        label_set=class_labels,
        region_details=details,
        method=f"breaks_{method}",
        config={"metric": metric, "n_classes": n_classes,
                "method": method, "breaks": breaks},
        summary=summary,
    )


# -- Spatial smoothing --

def _get_neighbor_indices(regions, region_idx):
    """Get neighbor indices for a region (0-based)."""
    if region_idx >= len(regions):
        return []
    region = regions[region_idx]
    if isinstance(region, dict):
        return [n - 1 for n in region.get("neighbors", []) if 1 <= n <= len(regions)]
    return []


def spatial_smooth(result, regions, iterations=1):
    """Apply spatial smoothing via majority vote of neighbors.

    Each region's label is replaced by the most common label among
    its neighbors (including itself).  Ties are broken by keeping
    the current label.

    Parameters
    ----------
    result : ClassificationResult
        Input classification.
    regions : list
        Region data (with neighbor info).
    iterations : int
        Number of smoothing passes.

    Returns
    -------
    ClassificationResult
        New result with smoothed labels.
    """
    if not result.labels:
        return result

    labels = list(result.labels)
    n = len(labels)

    for _ in range(iterations):
        new_labels = list(labels)
        for i in range(n):
            neighbors = _get_neighbor_indices(regions, i)
            if not neighbors:
                continue
            # Include self
            neighbor_labels = [labels[i]]
            for ni in neighbors:
                if 0 <= ni < n:
                    neighbor_labels.append(labels[ni])

            # Majority vote
            counts = Counter(neighbor_labels)
            most_common = counts.most_common()
            # Keep current if tied
            if most_common[0][0] != labels[i] and most_common[0][1] > len(neighbor_labels) / 2:
                new_labels[i] = most_common[0][0]

        labels = new_labels

    # Rebuild result
    details = []
    for i, label in enumerate(labels):
        d = dict(result.region_details[i]) if i < len(result.region_details) else {}
        d["label"] = label
        details.append(d)

    label_set = []
    for lb in labels:
        if lb not in label_set:
            label_set.append(lb)

    summary = _build_summary(labels, [], label_set)

    return ClassificationResult(
        labels=labels,
        label_set=label_set,
        region_details=details,
        method=f"{result.method}+smooth({iterations})",
        config={**result.config, "smooth_iterations": iterations},
        summary=summary,
    )


# -- Multi-metric classification --

def classify_multi_metric(stats, metric_weights, n_classes=4, method="quantile"):
    """Classify using a weighted combination of multiple metrics.

    Parameters
    ----------
    stats : list of dict
        Region statistics.
    metric_weights : dict
        {metric_name: weight} pairs.  Metrics are normalized to [0,1]
        then combined using weights.
    n_classes : int
        Number of output classes.
    method : str
        Break method for the combined score.

    Returns
    -------
    ClassificationResult
    """
    if not stats or not metric_weights:
        return ClassificationResult(method="multi_metric")

    # Extract and normalize each metric
    metric_arrays = {}
    for metric_name in metric_weights:
        vals = _extract_metric(stats, metric_name)
        mn, mx = min(vals), max(vals)
        rng = mx - mn
        if rng < 1e-12:
            metric_arrays[metric_name] = [0.5] * len(vals)
        else:
            metric_arrays[metric_name] = [(v - mn) / rng for v in vals]

    # Compute weighted scores
    total_weight = sum(metric_weights.values())
    if total_weight < 1e-12:
        total_weight = 1.0

    scores = []
    for i in range(len(stats)):
        score = 0.0
        for metric_name, weight in metric_weights.items():
            score += metric_arrays[metric_name][i] * weight
        scores.append(score / total_weight)

    # Create synthetic stats for break classification
    synthetic_stats = [{"score": s, "seed": stats[i].get("seed", (0, 0))}
                       for i, s in enumerate(scores)]

    # Use break method on scores
    break_fn = _BREAK_METHODS.get(method, _quantile_breaks)
    breaks = break_fn(scores, n_classes)

    class_labels = [f"class_{i+1}" for i in range(len(breaks) + 1)]

    labels = []
    details = []
    for i, score in enumerate(scores):
        cls_idx = 0
        for j, brk in enumerate(breaks):
            if score >= brk:
                cls_idx = j + 1
        label = class_labels[min(cls_idx, len(class_labels) - 1)]
        labels.append(label)
        details.append({
            "region_index": i + 1,
            "seed": stats[i].get("seed", (0, 0)),
            "label": label,
            "combined_score": round(score, 6),
        })

    summary = _build_summary(labels, stats, class_labels)
    summary["metric_weights"] = metric_weights
    summary["breaks"] = breaks

    return ClassificationResult(
        labels=labels,
        label_set=class_labels,
        region_details=details,
        method="multi_metric",
        config={"metric_weights": metric_weights, "n_classes": n_classes,
                "method": method, "breaks": breaks},
        summary=summary,
    )


# -- Summary builder --

def _build_summary(labels, stats, label_set):
    """Build per-class summary statistics."""
    total = len(labels)
    summary = {"total_regions": total, "per_class": {}}
    for lb in label_set:
        indices = [i for i, l in enumerate(labels) if l == lb]
        count = len(indices)
        pct = (count / total * 100) if total > 0 else 0.0
        class_info = {
            "count": count,
            "percentage": round(pct, 2),
            "region_indices": [i + 1 for i in indices],
        }
        if stats:
            for metric in ["area", "compactness", "perimeter"]:
                vals = [_extract_metric([stats[i]], metric)[0]
                        for i in indices if i < len(stats)]
                if vals:
                    class_info[f"mean_{metric}"] = round(_mean(vals), 4)
        summary["per_class"][lb] = class_info
    return summary


# -- Export --

_DEFAULT_PALETTE = [
    "#2196F3", "#4CAF50", "#FF9800", "#F44336",
    "#9C27B0", "#00BCD4", "#FFEB3B", "#795548",
    "#607D8B", "#E91E63",
]


def export_classified_json(result, filepath):
    """Export classification result as JSON."""
    output = {
        "method": result.method,
        "config": result.config,
        "label_set": result.label_set,
        "summary": result.summary,
        "regions": result.region_details,
    }
    with open(filepath, "w") as f:
        json.dump(output, f, indent=2, default=str)


def export_classified_svg(result, regions, data, filepath,
                          palette=None, width=800, height=600):
    """Export classified regions as a color-coded SVG.

    Parameters
    ----------
    result : ClassificationResult
        Classification result.
    regions : list
        Region polygon data.
    data : list
        Point data.
    filepath : str
        Output SVG path.
    palette : list of str or None
        Color palette for classes.
    width, height : int
        SVG dimensions.
    """
    if palette is None:
        palette = _DEFAULT_PALETTE

    label_colors = {}
    for i, lb in enumerate(result.label_set):
        label_colors[lb] = palette[i % len(palette)]

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height))

    # Add a title
    title = ET.SubElement(svg, "title")
    title.text = f"Voronoi Classification ({result.method})"

    # Draw regions
    for i, region in enumerate(regions):
        if i >= len(result.labels):
            break

        label = result.labels[i]
        color = label_colors.get(label, "#CCCCCC")

        if isinstance(region, dict):
            vertices = region.get("vertices", [])
        elif isinstance(region, (list, tuple)):
            vertices = region
        else:
            continue

        if not vertices:
            continue

        points_str = " ".join(f"{x},{y}" for x, y in vertices)
        ET.SubElement(svg, "polygon", points=points_str,
                      fill=color, stroke="white",
                      **{"stroke-width": "1", "opacity": "0.8"})

    # Legend
    legend_y = 20
    for lb in result.label_set:
        color = label_colors.get(lb, "#CCCCCC")
        count = sum(1 for l in result.labels if l == lb)
        ET.SubElement(svg, "rect", x="10", y=str(legend_y),
                      width="15", height="15", fill=color)
        text = ET.SubElement(svg, "text", x="30", y=str(legend_y + 12),
                             fill="black",
                             **{"font-size": "12", "font-family": "sans-serif"})
        text.text = f"{lb} ({count})"
        legend_y += 22

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(filepath, xml_declaration=True, encoding="unicode")


# -- Convenience --

def get_class_regions(result, label):
    """Get region indices (1-based) for a given class label."""
    return [i + 1 for i, lb in enumerate(result.labels) if lb == label]


def reclassify(result, label_map):
    """Remap labels according to a mapping dict.

    Parameters
    ----------
    result : ClassificationResult
        Original classification.
    label_map : dict
        {old_label: new_label} mapping.  Labels not in the map are kept.

    Returns
    -------
    ClassificationResult
    """
    new_labels = [label_map.get(lb, lb) for lb in result.labels]
    new_label_set = []
    for lb in new_labels:
        if lb not in new_label_set:
            new_label_set.append(lb)

    details = []
    for i, d in enumerate(result.region_details):
        nd = dict(d)
        nd["label"] = new_labels[i]
        details.append(nd)

    summary = _build_summary(new_labels, [], new_label_set)

    return ClassificationResult(
        labels=new_labels,
        label_set=new_label_set,
        region_details=details,
        method=result.method + "+reclassified",
        config={**result.config, "label_map": label_map},
        summary=summary,
    )


def confusion_matrix(result_a, result_b):
    """Compute a confusion matrix between two classifications.

    Parameters
    ----------
    result_a, result_b : ClassificationResult
        Two classification results over the same regions.

    Returns
    -------
    dict
        {(label_a, label_b): count} pairs.
    """
    matrix = {}
    n = min(len(result_a.labels), len(result_b.labels))
    for i in range(n):
        key = (result_a.labels[i], result_b.labels[i])
        matrix[key] = matrix.get(key, 0) + 1
    return matrix


def agreement_score(result_a, result_b):
    """Compute agreement percentage between two classifications."""
    n = min(len(result_a.labels), len(result_b.labels))
    if n == 0:
        return 0.0
    agree = sum(1 for i in range(n) if result_a.labels[i] == result_b.labels[i])
    return round(agree / n * 100, 2)
