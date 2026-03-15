"""Region Merger — merge adjacent Voronoi regions by attribute similarity.

Iteratively merges the most similar neighbouring regions until a target
region count or similarity threshold is reached.  Useful for:

- **Spatial generalisation** — simplify dense diagrams into meaningful zones.
- **Regionalization** — group nearby points with similar attributes.
- **Zone aggregation** — reduce complexity for downstream analysis.

The merging process uses a greedy agglomerative approach: at each step
the pair of adjacent regions with the smallest attribute distance is
merged into a single super-region.

Functions
---------
- ``merge_regions`` — main entry point; returns merged zone assignments.
- ``merge_summary`` — text table summarising merged zones.
- ``export_merge_svg`` — colour-coded SVG of the merged diagram.
- ``export_merge_json`` — JSON file with zone definitions.
- ``export_merge_csv`` — CSV with seed-to-zone mapping.

Example::

    from vormap_merge import merge_regions, export_merge_svg

    # 10 seed points with temperature readings
    points = [(x, y) for x, y in zip(range(0, 500, 50), range(0, 500, 50))]
    values = [20, 22, 21, 30, 31, 29, 15, 16, 35, 34]

    result = merge_regions(points, values, target_zones=3)
    export_merge_svg(result, "merged.svg", width=600, height=600)
"""


import csv
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from vormap import validate_output_path

try:
    from scipy.spatial import Voronoi as ScipyVoronoi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class MergeZone:
    """A merged zone containing one or more original seed points."""
    zone_id: int
    seeds: list  # list of (x, y) tuples
    values: list  # original attribute values for each seed
    mean_value: float = 0.0
    min_value: float = 0.0
    max_value: float = 0.0
    std_value: float = 0.0

    def __post_init__(self):
        self._recalc()

    def _recalc(self):
        if not self.values:
            return
        self.mean_value = sum(self.values) / len(self.values)
        self.min_value = min(self.values)
        self.max_value = max(self.values)
        variance = sum((v - self.mean_value) ** 2 for v in self.values) / len(self.values)
        self.std_value = math.sqrt(variance)


@dataclass
class MergeResult:
    """Full result of a region merge operation."""
    zones: list  # list of MergeZone
    seed_to_zone: dict  # maps (x, y) -> zone_id
    merge_history: list  # list of (step, zone_a, zone_b, distance)
    original_count: int
    merged_count: int
    iterations: int
    method: str  # 'target' or 'threshold'
    threshold: float = 0.0


# ── Adjacency helpers ────────────────────────────────────────────────

def _build_adjacency_scipy(points):
    """Build adjacency from scipy Delaunay triangulation."""
    import numpy as np
    from scipy.spatial import Delaunay

    pts = np.array(points)
    tri = Delaunay(pts)
    adj = {tuple(p): set() for p in points}

    for simplex in tri.simplices:
        for i in range(3):
            for j in range(i + 1, 3):
                a = tuple(pts[simplex[i]])
                b = tuple(pts[simplex[j]])
                # Snap to original points
                a_key = _snap_to(a, points)
                b_key = _snap_to(b, points)
                if a_key and b_key and a_key != b_key:
                    adj[a_key].add(b_key)
                    adj[b_key].add(a_key)

    return adj


def _snap_to(pt, points, tol=1e-6):
    """Snap a point to the nearest original point within tolerance."""
    best = None
    best_d = float('inf')
    for p in points:
        d = (pt[0] - p[0]) ** 2 + (pt[1] - p[1]) ** 2
        if d < best_d:
            best_d = d
            best = p
    return tuple(best) if best_d < tol * tol or best is not None else None


def _build_adjacency_brute(points, regions):
    """Build adjacency by shared polygon edges (fallback)."""
    adj = {tuple(p): set() for p in points}
    tol = 0.5

    # Build edge -> seed mapping
    edge_map = {}
    for seed, verts in regions.items():
        n = len(verts)
        for i in range(n):
            j = (i + 1) % n
            v1 = (round(verts[i][0] / tol) * tol,
                   round(verts[i][1] / tol) * tol)
            v2 = (round(verts[j][0] / tol) * tol,
                   round(verts[j][1] / tol) * tol)
            if v1 != v2:
                edge = frozenset((v1, v2))
                if edge in edge_map:
                    other = edge_map[edge]
                    if other != seed:
                        adj.setdefault(seed, set()).add(other)
                        adj.setdefault(other, set()).add(seed)
                else:
                    edge_map[edge] = seed

    return adj


# ── Core merge algorithm ─────────────────────────────────────────────

def merge_regions(points, values, *, target_zones=None, threshold=None,
                  regions=None, adjacency=None):
    """Merge adjacent Voronoi regions based on attribute similarity.

    Uses greedy agglomerative merging: at each step, the pair of
    neighbouring zones with the smallest difference in mean attribute
    value is merged.

    Parameters
    ----------
    points : list of (float, float)
        Seed point coordinates.
    values : list of float
        Attribute value for each seed point (same order as *points*).
    target_zones : int or None
        Merge until this many zones remain.  Must be >= 1 and <= len(points).
    threshold : float or None
        Merge until the smallest inter-zone distance exceeds this value.
    regions : dict or None
        Pre-computed Voronoi regions (seed -> vertex list).  If None and
        adjacency is also None, adjacency is computed from points using
        scipy (preferred) or polygon-edge matching.
    adjacency : dict or None
        Pre-computed adjacency dict (seed -> set of neighbour seeds).

    Returns
    -------
    MergeResult

    Raises
    ------
    ValueError
        If inputs are invalid or both target_zones and threshold are None.
    """
    if len(points) != len(values):
        raise ValueError("points and values must have the same length")
    if len(points) < 2:
        raise ValueError("need at least 2 points to merge")
    if target_zones is None and threshold is None:
        raise ValueError("must specify target_zones or threshold (or both)")
    if target_zones is not None and (target_zones < 1 or target_zones > len(points)):
        raise ValueError(f"target_zones must be between 1 and {len(points)}")
    if threshold is not None and threshold < 0:
        raise ValueError("threshold must be non-negative")

    pts = [tuple(p) for p in points]

    # Build adjacency if not provided
    if adjacency is None:
        if _HAS_SCIPY and len(pts) >= 3:
            adjacency = _build_adjacency_scipy(pts)
        elif regions is not None:
            adjacency = _build_adjacency_brute(pts, regions)
        else:
            raise ValueError(
                "must provide adjacency or regions (or install scipy)"
            )

    # Initialise: one zone per seed
    zone_id_counter = 0
    seed_zone = {}  # seed -> zone_id
    zone_seeds = {}  # zone_id -> set of seeds
    zone_values = {}  # zone_id -> list of values
    zone_adj = {}  # zone_id -> set of neighbour zone_ids

    for i, pt in enumerate(pts):
        zid = zone_id_counter
        zone_id_counter += 1
        seed_zone[pt] = zid
        zone_seeds[zid] = {pt}
        zone_values[zid] = [values[i]]
        zone_adj[zid] = set()

    # Build zone adjacency from seed adjacency
    for seed, neighbours in adjacency.items():
        sz = seed_zone.get(seed)
        if sz is None:
            continue
        for nb in neighbours:
            nz = seed_zone.get(nb)
            if nz is not None and nz != sz:
                zone_adj.setdefault(sz, set()).add(nz)
                zone_adj.setdefault(nz, set()).add(sz)

    def _zone_mean(zid):
        v = zone_values[zid]
        return sum(v) / len(v) if v else 0.0

    method = 'target' if target_zones is not None else 'threshold'
    merge_history = []
    iterations = 0
    n_zones = len(zone_seeds)

    while n_zones > 1:
        # Check stopping criteria
        if target_zones is not None and n_zones <= target_zones:
            break

        # Find best merge pair
        best_dist = float('inf')
        best_pair = None

        for za, neighbours in zone_adj.items():
            ma = _zone_mean(za)
            for zb in neighbours:
                if zb <= za:
                    continue  # avoid double-checking
                mb = _zone_mean(zb)
                dist = abs(ma - mb)
                if dist < best_dist:
                    best_dist = dist
                    best_pair = (za, zb)

        if best_pair is None:
            break  # no adjacent zones left (disconnected graph)

        if threshold is not None and best_dist > threshold:
            break

        # Merge zb into za
        za, zb = best_pair
        iterations += 1
        merge_history.append((iterations, za, zb, best_dist))

        zone_seeds[za] |= zone_seeds[zb]
        zone_values[za].extend(zone_values[zb])

        # Update seed_zone mappings
        for s in zone_seeds[zb]:
            seed_zone[s] = za

        # Update adjacency: za absorbs zb's neighbours
        for nb in zone_adj.get(zb, set()):
            if nb == za:
                continue
            zone_adj[nb].discard(zb)
            zone_adj[nb].add(za)
            zone_adj[za].add(nb)
        zone_adj[za].discard(zb)

        # Remove zb
        del zone_seeds[zb]
        del zone_values[zb]
        del zone_adj[zb]
        n_zones -= 1

    # Build result
    # Re-number zones contiguously 0..N-1
    final_zones = []
    final_seed_to_zone = {}
    for new_id, (old_id, seeds) in enumerate(sorted(zone_seeds.items())):
        vals = zone_values[old_id]
        zone = MergeZone(
            zone_id=new_id,
            seeds=sorted(seeds),
            values=vals,
        )
        final_zones.append(zone)
        for s in seeds:
            final_seed_to_zone[s] = new_id

    return MergeResult(
        zones=final_zones,
        seed_to_zone=final_seed_to_zone,
        merge_history=merge_history,
        original_count=len(pts),
        merged_count=len(final_zones),
        iterations=iterations,
        method=method,
        threshold=threshold or 0.0,
    )


# ── Text summary ─────────────────────────────────────────────────────

def merge_summary(result):
    """Return a formatted text summary of merged zones.

    Parameters
    ----------
    result : MergeResult

    Returns
    -------
    str
    """
    lines = [
        f"Region Merge Summary",
        f"{'=' * 50}",
        f"Original regions: {result.original_count}",
        f"Merged zones:     {result.merged_count}",
        f"Merge steps:      {result.iterations}",
        f"Method:           {result.method}",
        "",
        f"{'Zone':<6} {'Seeds':<8} {'Mean':>8} {'Min':>8} {'Max':>8} {'Std':>8}",
        f"{'-' * 6} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8}",
    ]

    for zone in result.zones:
        lines.append(
            f"{zone.zone_id:<6} {len(zone.seeds):<8} "
            f"{zone.mean_value:>8.2f} {zone.min_value:>8.2f} "
            f"{zone.max_value:>8.2f} {zone.std_value:>8.2f}"
        )

    if result.merge_history:
        lines.append("")
        lines.append("Merge History")
        lines.append(f"{'Step':<6} {'Zone A':<8} {'Zone B':<8} {'Distance':>10}")
        lines.append(f"{'-' * 6} {'-' * 8} {'-' * 8} {'-' * 10}")
        for step, za, zb, dist in result.merge_history:
            lines.append(f"{step:<6} {za:<8} {zb:<8} {dist:>10.4f}")

    return "\n".join(lines)


# ── SVG export ───────────────────────────────────────────────────────

_ZONE_PALETTE = [
    "#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4",
    "#42d4f4", "#f032e6", "#bfef45", "#fabed4", "#469990",
    "#dcbeff", "#9A6324", "#fffac8", "#800000", "#aaffc3",
    "#808000", "#ffd8b1", "#000075", "#a9a9a9", "#000000",
]


def export_merge_svg(result, path, *, width=600, height=600,
                     points=None, show_seeds=True, show_labels=True):
    """Export a colour-coded SVG of merged zones.

    Each zone gets a distinct colour.  Seeds are shown as dots and
    zones can be labelled with their zone ID.

    Parameters
    ----------
    result : MergeResult
    path : str
        Output SVG file path (relative paths only).
    width, height : int
        SVG canvas dimensions.
    points : list of (float, float) or None
        All seed points (used for scaling).  If None, extracted from
        result.zones.
    show_seeds : bool
        Draw seed-point dots.
    show_labels : bool
        Label each zone with its ID.
    """
    validate_output_path(path)

    all_seeds = points or [s for z in result.zones for s in z.seeds]
    if not all_seeds:
        raise ValueError("no seeds to render")

    xs = [s[0] for s in all_seeds]
    ys = [s[1] for s in all_seeds]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    range_x = max_x - min_x or 1.0
    range_y = max_y - min_y or 1.0
    pad = 40

    def tx(x):
        return pad + (x - min_x) / range_x * (width - 2 * pad)

    def ty(y):
        return pad + (y - min_y) / range_y * (height - 2 * pad)

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height),
                     viewBox=f"0 0 {width} {height}")

    # Background
    ET.SubElement(svg, "rect", x="0", y="0", width=str(width),
                  height=str(height), fill="#f8f8f8")

    # Title
    title = ET.SubElement(svg, "text", x=str(width // 2), y="20",
                          fill="#333")
    title.set("text-anchor", "middle")
    title.set("font-size", "14")
    title.set("font-family", "sans-serif")
    title.text = (f"Region Merge: {result.original_count} → "
                  f"{result.merged_count} zones")

    # Draw seeds coloured by zone
    for zone in result.zones:
        colour = _ZONE_PALETTE[zone.zone_id % len(_ZONE_PALETTE)]
        for seed in zone.seeds:
            sx, sy = tx(seed[0]), ty(seed[1])
            if show_seeds:
                ET.SubElement(svg, "circle", cx=f"{sx:.1f}",
                              cy=f"{sy:.1f}", r="5",
                              fill=colour, stroke="#333",
                              **{"stroke-width": "0.5"})

        # Label at zone centroid
        if show_labels and zone.seeds:
            cx = sum(s[0] for s in zone.seeds) / len(zone.seeds)
            cy = sum(s[1] for s in zone.seeds) / len(zone.seeds)
            label = ET.SubElement(svg, "text",
                                 x=f"{tx(cx):.1f}",
                                 y=f"{ty(cy):.1f}",
                                 fill="#000")
            label.set("text-anchor", "middle")
            label.set("dominant-baseline", "central")
            label.set("font-size", "12")
            label.set("font-weight", "bold")
            label.set("font-family", "sans-serif")
            label.text = f"Z{zone.zone_id}"

    # Legend
    lx = width - 140
    ly = 40
    for zone in result.zones:
        colour = _ZONE_PALETTE[zone.zone_id % len(_ZONE_PALETTE)]
        ET.SubElement(svg, "rect", x=str(lx), y=str(ly),
                      width="12", height="12", fill=colour)
        leg = ET.SubElement(svg, "text", x=str(lx + 18), y=str(ly + 10),
                            fill="#333")
        leg.set("font-size", "10")
        leg.set("font-family", "sans-serif")
        leg.text = f"Zone {zone.zone_id} (n={len(zone.seeds)}, μ={zone.mean_value:.1f})"
        ly += 18

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(path, xml_declaration=True, encoding="unicode")


# ── JSON export ──────────────────────────────────────────────────────

def export_merge_json(result, path):
    """Export merge result as JSON.

    Parameters
    ----------
    result : MergeResult
    path : str
        Output file path (relative paths only).
    """
    validate_output_path(path)

    data = {
        "original_count": result.original_count,
        "merged_count": result.merged_count,
        "iterations": result.iterations,
        "method": result.method,
        "threshold": result.threshold,
        "zones": [
            {
                "zone_id": z.zone_id,
                "seeds": z.seeds,
                "values": z.values,
                "mean": round(z.mean_value, 4),
                "min": round(z.min_value, 4),
                "max": round(z.max_value, 4),
                "std": round(z.std_value, 4),
            }
            for z in result.zones
        ],
        "merge_history": [
            {"step": s, "zone_a": a, "zone_b": b, "distance": round(d, 4)}
            for s, a, b, d in result.merge_history
        ],
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── CSV export ───────────────────────────────────────────────────────

def export_merge_csv(result, path):
    """Export seed-to-zone mapping as CSV.

    Parameters
    ----------
    result : MergeResult
    path : str
        Output file path (relative paths only).
    """
    validate_output_path(path)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["seed_x", "seed_y", "zone_id", "zone_mean",
                         "zone_min", "zone_max", "zone_std"])
        for zone in result.zones:
            for seed in zone.seeds:
                writer.writerow([
                    seed[0], seed[1], zone.zone_id,
                    round(zone.mean_value, 4),
                    round(zone.min_value, 4),
                    round(zone.max_value, 4),
                    round(zone.std_value, 4),
                ])
