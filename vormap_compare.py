"""Voronoi Diagram Comparison & Difference Analysis.

Compare two Voronoi diagrams and quantify structural differences:
seed displacement, area change, topology difference, overall similarity
score.

Usage
-----
::

    from vormap_compare import compare_diagrams, DiagramSnapshot

    snap_a = DiagramSnapshot.from_datafile("seeds_a.txt")
    snap_b = DiagramSnapshot.from_datafile("seeds_b.txt")
    result = compare_diagrams(snap_a, snap_b)
    print(result.summary())
"""

import math
from dataclasses import dataclass, field

from vormap import eudist_pts


@dataclass
class DiagramSnapshot:
    """Captures a Voronoi diagram's state for comparison.

    Attributes
    ----------
    seeds : list of (float, float)
        Seed coordinates.
    regions : dict
        Maps seed ``(x, y)`` → vertex list (from ``compute_regions``).
    region_stats : list of dict
        Per-region stat dicts (from ``compute_region_stats``).
    bounds : tuple or None
        ``(south, north, west, east)`` bounding box.
    """

    seeds: list
    regions: dict = field(default_factory=dict)
    region_stats: list = field(default_factory=list)
    bounds: tuple = None

    @classmethod
    def from_data(cls, data, bounds=None):
        """Create snapshot from seed point list.

        Computes regions and stats automatically using *vormap_viz*.
        """
        from vormap_viz import compute_regions, compute_region_stats
        import vormap

        if bounds:
            vormap.set_bounds(*bounds)
        else:
            s, n, w, e = vormap.compute_bounds(data)
            vormap.set_bounds(s, n, w, e)

        regions = compute_regions(data)
        stats = compute_region_stats(regions, data)

        return cls(
            seeds=list(data),
            regions=regions,
            region_stats=stats,
            bounds=(vormap.IND_S, vormap.IND_N, vormap.IND_W, vormap.IND_E),
        )

    @classmethod
    def from_datafile(cls, filename, bounds=None):
        """Create snapshot from a seed data file."""
        import vormap

        data = vormap.load_data(filename)
        return cls.from_data(data, bounds)

    @property
    def seed_count(self):
        """Number of seed points."""
        return len(self.seeds)

    @property
    def region_count(self):
        """Number of computed regions."""
        return len(self.regions)


@dataclass
class SeedMapping:
    """Mapping between seeds in two diagrams via nearest-neighbor matching.

    Attributes
    ----------
    pairs : list of (int, int, float)
        ``(idx_a, idx_b, distance)`` matched seed pairs.
    unmatched_a : list of int
        Indices in A with no match in B.
    unmatched_b : list of int
        Indices in B with no match in A.
    mean_displacement : float
        Average distance between matched pairs.
    max_displacement : float
        Maximum distance between matched pairs.
    """

    pairs: list = field(default_factory=list)
    unmatched_a: list = field(default_factory=list)
    unmatched_b: list = field(default_factory=list)
    mean_displacement: float = 0.0
    max_displacement: float = 0.0


@dataclass
class AreaComparison:
    """Area change analysis between matched regions.

    Attributes
    ----------
    changes : list of tuple
        ``(idx_a, idx_b, area_a, area_b, abs_change, pct_change)``.
    mean_abs_change : float
    mean_pct_change : float
    max_abs_change : float
    max_pct_change : float
    total_area_a : float
    total_area_b : float
    """

    changes: list = field(default_factory=list)
    mean_abs_change: float = 0.0
    mean_pct_change: float = 0.0
    max_abs_change: float = 0.0
    max_pct_change: float = 0.0
    total_area_a: float = 0.0
    total_area_b: float = 0.0


@dataclass
class TopologyDiff:
    """Topology changes between two diagrams.

    Attributes
    ----------
    neighbor_changes : int
        Number of adjacency relationships that changed.
    edge_count_a : int
        Total edges in diagram A's neighbourhood graph.
    edge_count_b : int
        Total edges in diagram B's neighbourhood graph.
    jaccard_similarity : float
        Jaccard index of edge sets (intersection / union).
    new_edges : list
        Edges in B not in A (as index pairs in A's index space).
    lost_edges : list
        Edges in A not in B.
    """

    neighbor_changes: int = 0
    edge_count_a: int = 0
    edge_count_b: int = 0
    jaccard_similarity: float = 0.0
    new_edges: list = field(default_factory=list)
    lost_edges: list = field(default_factory=list)


@dataclass
class ComparisonResult:
    """Full comparison result between two Voronoi diagrams.

    Attributes
    ----------
    seed_mapping : SeedMapping
    area_comparison : AreaComparison
    topology_diff : TopologyDiff
    similarity_score : float
        Overall 0–1 similarity (1 = identical).
    verdict : str
        Human-readable summary string.
    """

    seed_mapping: SeedMapping = field(default_factory=SeedMapping)
    area_comparison: AreaComparison = field(default_factory=AreaComparison)
    topology_diff: TopologyDiff = field(default_factory=TopologyDiff)
    similarity_score: float = 0.0
    verdict: str = ""

    def summary(self):
        """Human-readable comparison summary."""
        lines = [
            "Voronoi Diagram Comparison",
            "=" * 40,
            f"Similarity Score: {self.similarity_score:.1%}  ({self.verdict})",
            "",
            "Seed Mapping:",
            f"  Matched pairs: {len(self.seed_mapping.pairs)}",
            f"  Unmatched in A: {len(self.seed_mapping.unmatched_a)}",
            f"  Unmatched in B: {len(self.seed_mapping.unmatched_b)}",
            f"  Mean displacement: {self.seed_mapping.mean_displacement:.4f}",
            f"  Max displacement: {self.seed_mapping.max_displacement:.4f}",
            "",
            "Area Changes:",
            f"  Mean absolute: {self.area_comparison.mean_abs_change:.4f}",
            f"  Mean percentage: {self.area_comparison.mean_pct_change:.1f}%",
            f"  Max absolute: {self.area_comparison.max_abs_change:.4f}",
            f"  Max percentage: {self.area_comparison.max_pct_change:.1f}%",
            "",
            "Topology:",
            f"  Neighbor changes: {self.topology_diff.neighbor_changes}",
            (
                f"  Edge count A\u2192B: "
                f"{self.topology_diff.edge_count_a}\u2192"
                f"{self.topology_diff.edge_count_b}"
            ),
            f"  Jaccard similarity: {self.topology_diff.jaccard_similarity:.3f}",
            f"  New edges: {len(self.topology_diff.new_edges)}",
            f"  Lost edges: {len(self.topology_diff.lost_edges)}",
        ]
        return "\n".join(lines)

    def to_dict(self):
        """Serialise to dict for JSON export."""
        return {
            "similarity_score": round(self.similarity_score, 4),
            "verdict": self.verdict,
            "seed_mapping": {
                "matched_count": len(self.seed_mapping.pairs),
                "unmatched_a": len(self.seed_mapping.unmatched_a),
                "unmatched_b": len(self.seed_mapping.unmatched_b),
                "mean_displacement": round(
                    self.seed_mapping.mean_displacement, 6
                ),
                "max_displacement": round(
                    self.seed_mapping.max_displacement, 6
                ),
                "pairs": [
                    {
                        "idx_a": p[0],
                        "idx_b": p[1],
                        "distance": round(p[2], 6),
                    }
                    for p in self.seed_mapping.pairs
                ],
            },
            "area_comparison": {
                "mean_abs_change": round(
                    self.area_comparison.mean_abs_change, 6
                ),
                "mean_pct_change": round(
                    self.area_comparison.mean_pct_change, 4
                ),
                "max_abs_change": round(
                    self.area_comparison.max_abs_change, 6
                ),
                "max_pct_change": round(
                    self.area_comparison.max_pct_change, 4
                ),
                "total_area_a": round(self.area_comparison.total_area_a, 6),
                "total_area_b": round(self.area_comparison.total_area_b, 6),
            },
            "topology_diff": {
                "neighbor_changes": self.topology_diff.neighbor_changes,
                "edge_count_a": self.topology_diff.edge_count_a,
                "edge_count_b": self.topology_diff.edge_count_b,
                "jaccard_similarity": round(
                    self.topology_diff.jaccard_similarity, 4
                ),
                "new_edges": len(self.topology_diff.new_edges),
                "lost_edges": len(self.topology_diff.lost_edges),
            },
        }


# ── helpers ──────────────────────────────────────────────────────────


# ── seed matching ────────────────────────────────────────────────────


def match_seeds(seeds_a, seeds_b, max_distance=None):
    """Match seeds between two diagrams using greedy nearest-neighbor.

    Sorts all pairwise distances and greedily assigns closest unmatched
    pairs.  If *max_distance* is set, pairs beyond that threshold are
    left unmatched.

    Parameters
    ----------
    seeds_a : list of (float, float)
    seeds_b : list of (float, float)
    max_distance : float or None
        Maximum matching distance.  ``None`` = no limit.

    Returns
    -------
    SeedMapping
    """
    if not seeds_a and not seeds_b:
        return SeedMapping()
    if not seeds_a:
        return SeedMapping(unmatched_b=list(range(len(seeds_b))))
    if not seeds_b:
        return SeedMapping(unmatched_a=list(range(len(seeds_a))))

    # All pairwise distances
    n_a = len(seeds_a)
    n_b = len(seeds_b)
    distances = []
    for i in range(n_a):
        for j in range(n_b):
            d = eudist_pts(seeds_a[i], seeds_b[j])
            distances.append((d, i, j))

    distances.sort()

    used_a: set = set()
    used_b: set = set()
    pairs = []

    for d, i, j in distances:
        if i in used_a or j in used_b:
            continue
        if max_distance is not None and d > max_distance:
            break
        pairs.append((i, j, d))
        used_a.add(i)
        used_b.add(j)

    pairs.sort(key=lambda p: p[0])

    unmatched_a = [i for i in range(n_a) if i not in used_a]
    unmatched_b = [j for j in range(n_b) if j not in used_b]

    if pairs:
        dists = [p[2] for p in pairs]
        mean_disp = sum(dists) / len(dists)
        max_disp = max(dists)
    else:
        mean_disp = 0.0
        max_disp = 0.0

    return SeedMapping(
        pairs=pairs,
        unmatched_a=unmatched_a,
        unmatched_b=unmatched_b,
        mean_displacement=mean_disp,
        max_displacement=max_disp,
    )


# ── area comparison ──────────────────────────────────────────────────


def compare_areas(stats_a, stats_b, mapping):
    """Compare region areas between matched seeds.

    Parameters
    ----------
    stats_a : list of dict
        Region stats for diagram A (from ``compute_region_stats``).
    stats_b : list of dict
        Region stats for diagram B.
    mapping : SeedMapping
        Seed index mapping between diagrams.

    Returns
    -------
    AreaComparison
    """
    if not mapping.pairs:
        total_a = sum(s["area"] for s in stats_a) if stats_a else 0.0
        total_b = sum(s["area"] for s in stats_b) if stats_b else 0.0
        return AreaComparison(total_area_a=total_a, total_area_b=total_b)

    changes = []
    # Build lookup by region_index (1-based in stats) → stats dict.
    # This correctly handles gaps when some seeds fail to produce regions.
    stats_lookup_a = {s["region_index"] - 1: s for s in stats_a}
    stats_lookup_b = {s["region_index"] - 1: s for s in stats_b}

    for idx_a, idx_b, _ in mapping.pairs:
        sa = stats_lookup_a.get(idx_a)
        sb = stats_lookup_b.get(idx_b)
        if sa is None or sb is None:
            continue
        area_a = sa["area"]
        area_b = sb["area"]
        abs_change = abs(area_b - area_a)
        pct_change = (abs_change / area_a * 100) if area_a > 0 else 0.0
        changes.append(
            (idx_a, idx_b, area_a, area_b, abs_change, pct_change)
        )

    total_a = sum(s["area"] for s in stats_a)
    total_b = sum(s["area"] for s in stats_b)

    if changes:
        abs_changes = [c[4] for c in changes]
        pct_changes = [c[5] for c in changes]
        return AreaComparison(
            changes=changes,
            mean_abs_change=sum(abs_changes) / len(abs_changes),
            mean_pct_change=sum(pct_changes) / len(pct_changes),
            max_abs_change=max(abs_changes),
            max_pct_change=max(pct_changes),
            total_area_a=total_a,
            total_area_b=total_b,
        )

    return AreaComparison(total_area_a=total_a, total_area_b=total_b)


# ── topology comparison ─────────────────────────────────────────────


def compare_topology(regions_a, data_a, regions_b, data_b, mapping):
    """Compare neighbourhood topology between two diagrams.

    Extracts neighbourhood graphs for both diagrams, maps edges through
    the seed mapping, and computes Jaccard similarity of edge sets.

    Parameters
    ----------
    regions_a, regions_b : dict
        Region dicts from ``compute_regions``.
    data_a, data_b : list
        Seed point lists.
    mapping : SeedMapping
        Seed index mapping.

    Returns
    -------
    TopologyDiff
    """
    from vormap_viz import extract_neighborhood_graph

    graph_a = extract_neighborhood_graph(regions_a, data_a)
    graph_b = extract_neighborhood_graph(regions_b, data_b)

    # Build data-index-based edge sets from the graph dicts.
    seed_idx_a = graph_a["seed_indices"]  # seed (x,y) → index
    seed_idx_b = graph_b["seed_indices"]

    def _index_edges(graph, seed_idx):
        """Convert seed-coordinate edges to index-based edges."""
        edges = set()
        for s1, s2 in graph["edges"]:
            i = seed_idx.get(s1)
            j = seed_idx.get(s2)
            if i is not None and j is not None:
                edges.add((min(i, j), max(i, j)))
        return edges

    edges_a = _index_edges(graph_a, seed_idx_a)
    edges_b_raw = _index_edges(graph_b, seed_idx_b)

    # Build index mapping: A index → B index
    a_to_b = {p[0]: p[1] for p in mapping.pairs}
    b_to_a = {p[1]: p[0] for p in mapping.pairs}

    # Map edges from B's index space to A's index space
    edges_b_mapped = set()
    for i, j in edges_b_raw:
        mapped_i = b_to_a.get(i)
        mapped_j = b_to_a.get(j)
        if mapped_i is not None and mapped_j is not None:
            edges_b_mapped.add(
                (min(mapped_i, mapped_j), max(mapped_i, mapped_j))
            )

    # Only compare edges where both endpoints are in the mapping
    edges_a_matched = set()
    for i, j in edges_a:
        if i in a_to_b and j in a_to_b:
            edges_a_matched.add((i, j))

    intersection = edges_a_matched & edges_b_mapped
    union = edges_a_matched | edges_b_mapped

    jaccard = len(intersection) / len(union) if union else 1.0

    new_edges = sorted(edges_b_mapped - edges_a_matched)
    lost_edges = sorted(edges_a_matched - edges_b_mapped)

    return TopologyDiff(
        neighbor_changes=len(new_edges) + len(lost_edges),
        edge_count_a=len(edges_a),
        edge_count_b=len(edges_b_raw),
        jaccard_similarity=jaccard,
        new_edges=new_edges,
        lost_edges=lost_edges,
    )


# ── similarity score ─────────────────────────────────────────────────


def _compute_similarity_score(
    mapping, area_comp, topo_diff, seed_count_a, seed_count_b, bounds
):
    """Compute overall 0–1 similarity score from component metrics.

    Weighted combination:

    - Seed displacement (0.30): normalised by diagram diagonal.
    - Area change (0.30): based on mean percentage change.
    - Topology (0.25): Jaccard similarity of edge sets.
    - Seed count match (0.15): penalise different seed counts.
    """
    # Seed displacement score (0-1, 1 = no displacement)
    if bounds and mapping.pairs:
        diagonal = eudist_pts(
            (bounds[2], bounds[0]),  # (west, south)
            (bounds[3], bounds[1]),  # (east, north)
        )
        if diagonal > 0:
            norm_disp = mapping.mean_displacement / diagonal
            disp_score = max(0.0, 1.0 - norm_disp * 10)
        else:
            disp_score = 1.0
    elif not mapping.pairs:
        disp_score = 0.0
    else:
        disp_score = 1.0

    # Area change score (0-1, 1 = no change)
    if area_comp.mean_pct_change > 0:
        area_score = max(0.0, 1.0 - area_comp.mean_pct_change / 100.0)
    else:
        area_score = 1.0

    # Topology score = Jaccard similarity
    topo_score = topo_diff.jaccard_similarity

    # Seed count match (0-1)
    max_count = max(seed_count_a, seed_count_b, 1)
    min_count = min(seed_count_a, seed_count_b)
    count_score = min_count / max_count

    score = (
        disp_score * 0.30
        + area_score * 0.30
        + topo_score * 0.25
        + count_score * 0.15
    )
    return round(min(1.0, max(0.0, score)), 4)


def _get_verdict(score):
    """Human-readable verdict from similarity score."""
    if score >= 0.95:
        return "Nearly Identical"
    if score >= 0.80:
        return "Very Similar"
    if score >= 0.60:
        return "Moderately Similar"
    if score >= 0.40:
        return "Somewhat Different"
    if score >= 0.20:
        return "Very Different"
    return "Completely Different"


# ── main entry point ─────────────────────────────────────────────────


def compare_diagrams(snap_a, snap_b, max_match_distance=None):
    """Compare two Voronoi diagram snapshots.

    Parameters
    ----------
    snap_a : DiagramSnapshot
        First diagram.
    snap_b : DiagramSnapshot
        Second diagram.
    max_match_distance : float or None
        Maximum distance for seed matching.  ``None`` = no limit.

    Returns
    -------
    ComparisonResult
    """
    mapping = match_seeds(snap_a.seeds, snap_b.seeds, max_match_distance)

    area_comp = compare_areas(
        snap_a.region_stats, snap_b.region_stats, mapping
    )

    if snap_a.regions and snap_b.regions:
        topo_diff = compare_topology(
            snap_a.regions, snap_a.seeds,
            snap_b.regions, snap_b.seeds,
            mapping,
        )
    else:
        topo_diff = TopologyDiff()

    bounds = snap_a.bounds or snap_b.bounds
    score = _compute_similarity_score(
        mapping, area_comp, topo_diff,
        snap_a.seed_count, snap_b.seed_count, bounds,
    )
    verdict = _get_verdict(score)

    return ComparisonResult(
        seed_mapping=mapping,
        area_comparison=area_comp,
        topology_diff=topo_diff,
        similarity_score=score,
        verdict=verdict,
    )


# ── JSON export ──────────────────────────────────────────────────────


def export_comparison_json(result, output_path):
    """Export comparison result to JSON file.

    Parameters
    ----------
    result : ComparisonResult
    output_path : str
    """
    import json

    with open(output_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)
