"""Spatial hotspot detection for Voronoi diagrams (Getis-Ord Gi*).

Identifies statistically significant spatial clusters of high values
(*hotspots*) and low values (*coldspots*) in Voronoi tessellations
using the Getis-Ord Gi* statistic.

The Gi* statistic measures spatial autocorrelation at the local level,
testing whether each region and its neighbors exhibit unusually high
or low attribute values compared to the global mean.

Three spatial weight schemes are supported:

- **queen** — neighbors share an edge or vertex (default).
- **distance** — all regions within a threshold distance.
- **knn** — k nearest neighbors by seed centroid distance.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_hotspot import detect_hotspots, export_hotspot_svg

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = detect_hotspots(stats, attribute="area")
    for h in result.hotspots:
        print(f"Region {h['region_index']}: z={h['z_score']:.2f} p={h['p_value']:.4f}")
    print(result.summary_text())

    export_hotspot_svg(result, regions, data, "hotspots.svg")

CLI::

    voronoimap datauni5.txt 5 --hotspots
    voronoimap datauni5.txt 5 --hotspots --attribute compactness
    voronoimap datauni5.txt 5 --hotspots --weights knn --k 5
    voronoimap datauni5.txt 5 --hotspots-svg hotspots.svg
    voronoimap datauni5.txt 5 --hotspots-json hotspots.json
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from vormap_geometry import (
    mean as _mean,
    std as _std,
    normal_cdf as _normal_cdf,
    polygon_centroid,
    polygon_area,
    edge_length,
)


# ── Spatial weights ─────────────────────────────────────────────────

def _centroids_from_stats(stats):
    """Extract (x, y) centroids from region stats."""
    return [(s["centroid_x"], s["centroid_y"]) for s in stats]


def _distance(a, b):
    """Euclidean distance between two 2D points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _shared_edge_or_vertex(verts_a, verts_b, tol=1e-9):
    """Check if two polygons share an edge or vertex (queen contiguity)."""
    for va in verts_a:
        for vb in verts_b:
            if abs(va[0] - vb[0]) < tol and abs(va[1] - vb[1]) < tol:
                return True
    return False


def build_queen_weights(regions, stats):
    """Build queen contiguity weight matrix.

    Two regions are neighbors if they share at least one vertex.

    Parameters
    ----------
    regions : dict
        Seed → vertex list from ``compute_regions()``.
    stats : list of dict
        Region stats from ``compute_region_stats()``.

    Returns
    -------
    dict
        Maps region index (0-based) to set of neighbor indices.
    """
    n = len(stats)
    seeds = [(s["seed_x"], s["seed_y"]) for s in stats]
    # Map seeds to their vertex lists
    seed_verts = []
    for s in seeds:
        key = None
        for k in regions:
            if abs(k[0] - s[0]) < 1e-9 and abs(k[1] - s[1]) < 1e-9:
                key = k
                break
        seed_verts.append(regions[key] if key is not None else [])

    weights = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if _shared_edge_or_vertex(seed_verts[i], seed_verts[j]):
                weights[i].add(j)
                weights[j].add(i)
    return weights


def build_distance_weights(stats, threshold=None):
    """Build distance-band weight matrix.

    Parameters
    ----------
    stats : list of dict
        Region stats.
    threshold : float, optional
        Distance threshold. Defaults to the mean nearest-neighbor
        distance × 1.5.

    Returns
    -------
    dict
        Maps index to set of neighbor indices.
    """
    centroids = _centroids_from_stats(stats)
    n = len(centroids)

    if threshold is None:
        # Compute mean nearest-neighbor distance
        nn_dists = []
        for i in range(n):
            min_d = float("inf")
            for j in range(n):
                if i != j:
                    d = _distance(centroids[i], centroids[j])
                    if d < min_d:
                        min_d = d
            if min_d < float("inf"):
                nn_dists.append(min_d)
        threshold = _mean(nn_dists) * 1.5 if nn_dists else 1.0

    weights = {i: set() for i in range(n)}
    for i in range(n):
        for j in range(i + 1, n):
            if _distance(centroids[i], centroids[j]) <= threshold:
                weights[i].add(j)
                weights[j].add(i)
    return weights


def build_knn_weights(stats, k=4):
    """Build k-nearest-neighbor weight matrix.

    Parameters
    ----------
    stats : list of dict
        Region stats.
    k : int
        Number of nearest neighbors per region.

    Returns
    -------
    dict
        Maps index to set of neighbor indices.
    """
    centroids = _centroids_from_stats(stats)
    n = len(centroids)
    k = min(k, n - 1)

    weights = {i: set() for i in range(n)}
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append((j, _distance(centroids[i], centroids[j])))
        dists.sort(key=lambda x: x[1])
        for j, _ in dists[:k]:
            weights[i].add(j)
            weights[j].add(i)
    return weights


# ── Getis-Ord Gi* ──────────────────────────────────────────────────

def _gi_star(values, weights, i):
    """Compute Getis-Ord Gi* statistic for region *i*.

    Gi* = (Σ_j w_ij x_j  -  X̄ Σ_j w_ij) /
          (S √((n Σ_j w_ij² − (Σ_j w_ij)²) / (n − 1)))

    where the sum includes j == i (star variant).

    Returns (z_score, p_value).
    """
    n = len(values)
    if n < 2:
        return 0.0, 1.0

    x_bar = _mean(values)
    s = _std(values, population=True)
    if s < 1e-15:
        return 0.0, 1.0

    # Neighbors including self (Gi* includes the focal region)
    neighbors = set(weights.get(i, set()))
    neighbors.add(i)

    sum_w = len(neighbors)  # binary weights
    sum_w2 = sum_w  # w^2 = 1 for binary
    sum_wx = sum(values[j] for j in neighbors)

    numerator = sum_wx - x_bar * sum_w
    denominator = s * math.sqrt((n * sum_w2 - sum_w ** 2) / (n - 1))

    if abs(denominator) < 1e-15:
        return 0.0, 1.0

    z = numerator / denominator
    # Two-tailed p-value from standard normal
    p = 2.0 * (1.0 - _normal_cdf(abs(z)))
    return z, p


def _classify_spot(z, p, confidence):
    """Classify a region based on z-score and p-value.

    Returns one of: 'hotspot_99', 'hotspot_95', 'hotspot_90',
    'coldspot_99', 'coldspot_95', 'coldspot_90', 'not_significant'.
    """
    thresholds = [
        (0.01, "99"),
        (0.05, "95"),
        (0.10, "90"),
    ]
    for p_thresh, label in thresholds:
        if p <= p_thresh:
            if z > 0:
                return f"hotspot_{label}"
            else:
                return f"coldspot_{label}"
    return "not_significant"


# ── Result container ────────────────────────────────────────────────

@dataclass
class HotspotResult:
    """Container for hotspot detection results.

    Attributes
    ----------
    hotspots : list of dict
        Regions classified as hotspots (z > 0, p ≤ confidence).
    coldspots : list of dict
        Regions classified as coldspots (z < 0, p ≤ confidence).
    not_significant : list of dict
        Regions that are not statistically significant.
    all_regions : list of dict
        All regions with their Gi* statistics.
    attribute : str
        Which attribute was analyzed.
    weight_scheme : str
        Weight scheme used ('queen', 'distance', or 'knn').
    confidence : float
        Significance level used.
    global_mean : float
        Global mean of the attribute.
    global_std : float
        Global standard deviation.
    """
    hotspots: list = field(default_factory=list)
    coldspots: list = field(default_factory=list)
    not_significant: list = field(default_factory=list)
    all_regions: list = field(default_factory=list)
    attribute: str = "area"
    weight_scheme: str = "queen"
    confidence: float = 0.05
    global_mean: float = 0.0
    global_std: float = 0.0

    @property
    def total(self):
        return len(self.all_regions)

    @property
    def hotspot_count(self):
        return len(self.hotspots)

    @property
    def coldspot_count(self):
        return len(self.coldspots)

    @property
    def significant_count(self):
        return self.hotspot_count + self.coldspot_count

    @property
    def significant_pct(self):
        return (self.significant_count / self.total * 100) if self.total else 0.0

    def summary_text(self):
        """Return a human-readable summary string."""
        lines = [
            f"Hotspot Analysis: {self.attribute}",
            f"  Weight scheme: {self.weight_scheme}",
            f"  Confidence: {self.confidence}",
            f"  Global mean: {self.global_mean:.4f}",
            f"  Global std:  {self.global_std:.4f}",
            f"  Total regions: {self.total}",
            f"  Hotspots:  {self.hotspot_count} ({self.hotspot_count/self.total*100:.1f}%)" if self.total else "  Hotspots: 0",
            f"  Coldspots: {self.coldspot_count} ({self.coldspot_count/self.total*100:.1f}%)" if self.total else "  Coldspots: 0",
            f"  Not significant: {len(self.not_significant)}",
        ]
        # Top hotspots
        if self.hotspots:
            lines.append("  Top hotspots:")
            for r in sorted(self.hotspots, key=lambda x: -x["z_score"])[:5]:
                lines.append(
                    f"    Region {r['region_index']}: z={r['z_score']:.3f} "
                    f"p={r['p_value']:.4f} ({r['classification']})"
                )
        if self.coldspots:
            lines.append("  Top coldspots:")
            for r in sorted(self.coldspots, key=lambda x: x["z_score"])[:5]:
                lines.append(
                    f"    Region {r['region_index']}: z={r['z_score']:.3f} "
                    f"p={r['p_value']:.4f} ({r['classification']})"
                )
        return "\n".join(lines)

    def to_dict(self):
        """Serialize to a JSON-friendly dict."""
        return {
            "attribute": self.attribute,
            "weight_scheme": self.weight_scheme,
            "confidence": self.confidence,
            "global_mean": round(self.global_mean, 6),
            "global_std": round(self.global_std, 6),
            "total_regions": self.total,
            "hotspot_count": self.hotspot_count,
            "coldspot_count": self.coldspot_count,
            "significant_pct": round(self.significant_pct, 2),
            "regions": self.all_regions,
        }


# ── Main API ────────────────────────────────────────────────────────

VALID_ATTRIBUTES = [
    "area", "perimeter", "compactness", "vertex_count", "avg_edge_length",
]


def detect_hotspots(
    stats,
    *,
    attribute="area",
    weight_scheme="queen",
    confidence=0.05,
    regions=None,
    distance_threshold=None,
    k=4,
):
    """Detect spatial hotspots and coldspots using Getis-Ord Gi*.

    Parameters
    ----------
    stats : list of dict
        Region stats from ``compute_region_stats()``.
    attribute : str
        Which numeric attribute to analyze (default ``"area"``).
    weight_scheme : str
        ``"queen"``, ``"distance"``, or ``"knn"`` (default ``"queen"``).
    confidence : float
        Significance level (default 0.05).
    regions : dict, optional
        Seed → vertex list (required for ``weight_scheme="queen"``).
    distance_threshold : float, optional
        For ``weight_scheme="distance"``.
    k : int
        For ``weight_scheme="knn"`` (default 4).

    Returns
    -------
    HotspotResult
        Detection results with classifications and statistics.

    Raises
    ------
    ValueError
        If attribute is not valid or queen weights requested without regions.
    """
    if attribute not in VALID_ATTRIBUTES:
        raise ValueError(
            f"Invalid attribute '{attribute}'. "
            f"Valid: {', '.join(VALID_ATTRIBUTES)}"
        )

    if not stats:
        return HotspotResult(attribute=attribute, weight_scheme=weight_scheme,
                             confidence=confidence)

    values = [s[attribute] for s in stats]
    global_mean = _mean(values)
    global_std = _std(values, population=True)

    # Build weight matrix
    if weight_scheme == "queen":
        if regions is None:
            raise ValueError("regions dict required for queen weight scheme")
        weights = build_queen_weights(regions, stats)
    elif weight_scheme == "distance":
        weights = build_distance_weights(stats, threshold=distance_threshold)
    elif weight_scheme == "knn":
        weights = build_knn_weights(stats, k=k)
    else:
        raise ValueError(f"Unknown weight scheme '{weight_scheme}'. "
                         f"Valid: queen, distance, knn")

    # Compute Gi* for each region
    result = HotspotResult(
        attribute=attribute,
        weight_scheme=weight_scheme,
        confidence=confidence,
        global_mean=global_mean,
        global_std=global_std,
    )

    for i, s in enumerate(stats):
        z, p = _gi_star(values, weights, i)
        classification = _classify_spot(z, p, confidence)

        entry = {
            "region_index": s["region_index"],
            "seed_x": s["seed_x"],
            "seed_y": s["seed_y"],
            "value": s[attribute],
            "z_score": round(z, 4),
            "p_value": round(p, 6),
            "classification": classification,
            "neighbors": len(weights.get(i, set())),
        }

        result.all_regions.append(entry)
        if classification.startswith("hotspot"):
            result.hotspots.append(entry)
        elif classification.startswith("coldspot"):
            result.coldspots.append(entry)
        else:
            result.not_significant.append(entry)

    return result


# ── SVG export ──────────────────────────────────────────────────────

_HOTSPOT_COLORS = {
    "hotspot_99": "#d73027",
    "hotspot_95": "#f46d43",
    "hotspot_90": "#fdae61",
    "coldspot_99": "#4575b4",
    "coldspot_95": "#74add1",
    "coldspot_90": "#abd9e9",
    "not_significant": "#ffffbf",
}


def export_hotspot_svg(result, regions, data, output_path, *,
                       width=800, height=600, show_legend=True):
    """Export hotspot map as SVG.

    Parameters
    ----------
    result : HotspotResult
        Output of ``detect_hotspots()``.
    regions : dict
        Seed → vertex list.
    data : list of tuple
        Original seed points.
    output_path : str
        SVG file path.
    width, height : int
        Canvas dimensions.
    show_legend : bool
        Whether to include a legend (default True).

    Returns
    -------
    str
        The output path.
    """
    if not data:
        return output_path

    # Compute bounds
    all_x = [p[0] for p in data]
    all_y = [p[1] for p in data]
    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    margin = 40
    range_x = max_x - min_x or 1
    range_y = max_y - min_y or 1
    draw_w = width - 2 * margin
    draw_h = height - 2 * margin - (80 if show_legend else 0)
    scale = min(draw_w / range_x, draw_h / range_y)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        return margin + (max_y - y) * scale  # flip y

    # Build lookup from region_index to classification
    classification_map = {}
    for r in result.all_regions:
        classification_map[r["region_index"]] = r["classification"]

    # Build data index
    data_idx = {}
    for idx, pt in enumerate(data):
        data_idx[pt] = idx

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height))

    # Background
    ET.SubElement(svg, "rect", width=str(width), height=str(height),
                  fill="white")

    # Title
    title = ET.SubElement(svg, "text", x=str(width // 2), y="20",
                          fill="black")
    title.set("text-anchor", "middle")
    title.set("font-family", "sans-serif")
    title.set("font-size", "14")
    title.set("font-weight", "bold")
    title.text = f"Hotspot Analysis: {result.attribute} ({result.weight_scheme})"

    # Draw regions
    for seed, verts in regions.items():
        seed_t = tuple(seed) if not isinstance(seed, tuple) else seed
        idx = data_idx.get(seed_t, -1)
        region_idx = idx + 1
        cls = classification_map.get(region_idx, "not_significant")
        color = _HOTSPOT_COLORS.get(cls, "#cccccc")

        points = " ".join(f"{tx(v[0]):.1f},{ty(v[1]):.1f}" for v in verts)
        ET.SubElement(svg, "polygon", points=points, fill=color,
                      stroke="#333", **{"stroke-width": "0.5"})

    # Draw seed points
    for pt in data:
        ET.SubElement(svg, "circle", cx=f"{tx(pt[0]):.1f}",
                      cy=f"{ty(pt[1]):.1f}", r="2", fill="black",
                      opacity="0.5")

    # Legend
    if show_legend:
        ly = height - 70
        labels = [
            ("hotspot_99", "Hot (99%)"), ("hotspot_95", "Hot (95%)"),
            ("hotspot_90", "Hot (90%)"), ("not_significant", "Not Sig."),
            ("coldspot_90", "Cold (90%)"), ("coldspot_95", "Cold (95%)"),
            ("coldspot_99", "Cold (99%)"),
        ]
        lx = margin
        for cls, label in labels:
            ET.SubElement(svg, "rect", x=str(lx), y=str(ly),
                          width="14", height="14",
                          fill=_HOTSPOT_COLORS[cls], stroke="#333",
                          **{"stroke-width": "0.5"})
            t = ET.SubElement(svg, "text", x=str(lx + 18), y=str(ly + 12),
                              fill="black")
            t.set("font-family", "sans-serif")
            t.set("font-size", "10")
            t.text = label
            lx += len(label) * 7 + 30

        # Summary line
        st = ET.SubElement(svg, "text", x=str(margin), y=str(ly + 35),
                           fill="#555")
        st.set("font-family", "sans-serif")
        st.set("font-size", "10")
        st.text = (
            f"Hotspots: {result.hotspot_count} | "
            f"Coldspots: {result.coldspot_count} | "
            f"Total: {result.total} | "
            f"Significant: {result.significant_pct:.1f}%"
        )

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(output_path, xml_declaration=True, encoding="unicode")
    return output_path


# ── JSON export ─────────────────────────────────────────────────────

def export_hotspot_json(result, output_path):
    """Export hotspot results as JSON.

    Parameters
    ----------
    result : HotspotResult
        Output of ``detect_hotspots()``.
    output_path : str
        JSON file path.

    Returns
    -------
    str
        The output path.
    """
    with open(output_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
    return output_path


# ── CSV export ──────────────────────────────────────────────────────

def export_hotspot_csv(result, output_path):
    """Export hotspot results as CSV.

    Parameters
    ----------
    result : HotspotResult
        Output of ``detect_hotspots()``.
    output_path : str
        CSV file path.

    Returns
    -------
    str
        The output path.
    """
    headers = [
        "region_index", "seed_x", "seed_y", "value",
        "z_score", "p_value", "classification", "neighbors",
    ]
    with open(output_path, "w") as f:
        f.write(",".join(headers) + "\n")
        for r in result.all_regions:
            row = [str(r[h]) for h in headers]
            f.write(",".join(row) + "\n")
    return output_path
