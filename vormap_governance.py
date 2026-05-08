"""Spatial Governance Engine -- autonomous democratic decision-making for Voronoi tessellations.

Models Voronoi cells as autonomous political actors in a spatial legislature.
Uses voting theory, power index computation, and coalition analysis to study
how spatial arrangements affect democratic outcomes.

Seven analysis engines run autonomously:

- **Weight Assigner** -- assigns voting weights to cells based on area,
  population proxy, strategic position (neighbor count, centrality).
- **Power Index Calculator** -- computes Banzhaf and Shapley-Shubik power
  indices to reveal true influence vs nominal weight.
- **Voting System Simulator** -- runs elections under 4 systems (plurality,
  Borda count, approval voting, ranked-choice/IRV) on spatial proposals.
- **Coalition Analyzer** -- enumerates winning/blocking coalitions, identifies
  veto players, measures coalition fragility.
- **Constitutional Designer** -- finds optimal quotas, checks for dictators
  and dummies, recommends voting rule modifications.
- **Democratic Health Scorer** -- composite 0-100 score across representation
  equality, power dispersion, coalition stability, and constitutional soundness.
- **Insight Generator** -- autonomous pattern detection and actionable
  governance recommendations.

Usage (Python API)::

    from vormap_governance import run_governance, GovernanceReport

    points = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
    bounds = (0, 1000, 0, 1000)

    report = run_governance(points, bounds=bounds)
    print(report.summary_text())
    report.to_json("governance.json")
    report.to_html("governance.html")

CLI::

    python vormap_governance.py --demo
    python vormap_governance.py --demo --json out.json --html out.html
    python vormap_governance.py --demo --quota 0.6 --seed 42
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Geometry helpers (inlined to avoid hard dep on vormap_utils)
# ---------------------------------------------------------------------------


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _polygon_area(vertices: List[Tuple[float, float]]) -> float:
    """Shoelace formula."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _centroid(vertices: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    return (sum(v[0] for v in vertices) / n, sum(v[1] for v in vertices) / n)


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _convex_hull(points):
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts
    lower = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _voronoi_cells_simple(
    points: List[Tuple[float, float]], bounds: Tuple[float, float, float, float]
) -> List[List[Tuple[float, float]]]:
    """Approximate Voronoi cells via bounded grid sampling."""
    south, north, west, east = bounds
    step = max((north - south), (east - west)) / 40.0
    if step <= 0:
        step = 1.0
    cell_pts: Dict[int, List[Tuple[float, float]]] = {i: [] for i in range(len(points))}
    y = south
    while y <= north:
        x = west
        while x <= east:
            best_i = 0
            best_d = _euclidean((x, y), points[0])
            for i in range(1, len(points)):
                d = _euclidean((x, y), points[i])
                if d < best_d:
                    best_d = d
                    best_i = i
            cell_pts[best_i].append((x, y))
            x += step
        y += step
    cells = []
    for i in range(len(points)):
        pts = cell_pts[i]
        if len(pts) < 3:
            px, py = points[i]
            cells.append([(px - step, py - step), (px + step, py - step),
                          (px + step, py + step), (px - step, py + step)])
        else:
            cells.append(_convex_hull(pts))
    return cells


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

WEIGHT_METHODS = ("area", "equal", "population", "strategic")


@dataclass
class CellVoter:
    """A Voronoi cell acting as a voter in the spatial legislature."""
    cell_idx: int
    centroid: Tuple[float, float]
    area: float
    neighbors: int
    centrality: float  # 0-1, higher = more central
    weight: float = 1.0
    banzhaf_power: float = 0.0
    shapley_power: float = 0.0
    is_dictator: bool = False
    is_dummy: bool = False
    has_veto: bool = False

    def to_dict(self) -> dict:
        return {
            "cell_idx": self.cell_idx,
            "centroid": [round(c, 2) for c in self.centroid],
            "area": round(self.area, 2),
            "neighbors": self.neighbors,
            "centrality": round(self.centrality, 4),
            "weight": round(self.weight, 4),
            "banzhaf_power": round(self.banzhaf_power, 6),
            "shapley_power": round(self.shapley_power, 6),
            "is_dictator": self.is_dictator,
            "is_dummy": self.is_dummy,
            "has_veto": self.has_veto,
        }


@dataclass
class Proposal:
    """A spatial proposal that cells vote on."""
    proposal_id: str
    name: str
    location: Optional[Tuple[float, float]]  # spatial target
    description: str

    def to_dict(self) -> dict:
        d = {"proposal_id": self.proposal_id, "name": self.name, "description": self.description}
        if self.location:
            d["location"] = [round(c, 2) for c in self.location]
        return d


@dataclass
class ElectionResult:
    """Result of running an election under a specific voting system."""
    system: str  # plurality, borda, approval, irv
    winner: str  # proposal_id
    scores: Dict[str, float]
    rounds: Optional[List[dict]] = None  # IRV rounds
    participation_rate: float = 1.0

    def to_dict(self) -> dict:
        d = {
            "system": self.system,
            "winner": self.winner,
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "participation_rate": round(self.participation_rate, 4),
        }
        if self.rounds:
            d["rounds"] = self.rounds
        return d


@dataclass
class Coalition:
    """A coalition of cells."""
    members: List[int]
    total_weight: float
    is_winning: bool
    is_minimal_winning: bool
    pivot_members: List[int]  # members whose removal makes it losing

    def to_dict(self) -> dict:
        return {
            "members": self.members,
            "total_weight": round(self.total_weight, 4),
            "is_winning": self.is_winning,
            "is_minimal_winning": self.is_minimal_winning,
            "pivot_members": self.pivot_members,
        }


@dataclass
class CoalitionAnalysis:
    """Full coalition analysis results."""
    total_coalitions: int
    winning_coalitions: int
    minimal_winning_coalitions: int
    blocking_coalitions: int
    veto_players: List[int]
    fragility_score: float  # 0-1, higher = more fragile
    top_coalitions: List[Coalition]

    def to_dict(self) -> dict:
        return {
            "total_coalitions": self.total_coalitions,
            "winning_coalitions": self.winning_coalitions,
            "minimal_winning_coalitions": self.minimal_winning_coalitions,
            "blocking_coalitions": self.blocking_coalitions,
            "veto_players": self.veto_players,
            "fragility_score": round(self.fragility_score, 4),
            "top_coalitions": [c.to_dict() for c in self.top_coalitions[:10]],
        }


@dataclass
class ConstitutionalAnalysis:
    """Results of constitutional design analysis."""
    quota: float
    quota_fraction: float
    has_dictator: bool
    dictator_idx: Optional[int]
    dummy_count: int
    dummy_indices: List[int]
    optimal_quota: float
    power_deviation: float  # how much power indices deviate from weights
    recommendations: List[str]

    def to_dict(self) -> dict:
        return {
            "quota": round(self.quota, 4),
            "quota_fraction": round(self.quota_fraction, 4),
            "has_dictator": self.has_dictator,
            "dictator_idx": self.dictator_idx,
            "dummy_count": self.dummy_count,
            "dummy_indices": self.dummy_indices,
            "optimal_quota": round(self.optimal_quota, 4),
            "power_deviation": round(self.power_deviation, 6),
            "recommendations": self.recommendations,
        }


@dataclass
class DemocraticHealth:
    """Composite democratic health assessment."""
    score: float  # 0-100
    grade: str  # A/B/C/D/F
    representation_equality: float  # 0-100
    power_dispersion: float  # 0-100
    coalition_stability: float  # 0-100
    constitutional_soundness: float  # 0-100
    voting_consistency: float  # 0-100

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 2),
            "grade": self.grade,
            "representation_equality": round(self.representation_equality, 2),
            "power_dispersion": round(self.power_dispersion, 2),
            "coalition_stability": round(self.coalition_stability, 2),
            "constitutional_soundness": round(self.constitutional_soundness, 2),
            "voting_consistency": round(self.voting_consistency, 2),
        }


@dataclass
class GovernanceInsight:
    """An autonomous governance insight."""
    category: str  # power_imbalance, structural_weakness, opportunity, warning
    severity: str  # critical, high, medium, low, info
    title: str
    detail: str
    affected_cells: List[int]

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "affected_cells": self.affected_cells,
        }


@dataclass
class GovernanceReport:
    """Complete governance analysis report."""
    n_cells: int
    cells: List[CellVoter]
    proposals: List[Proposal]
    elections: List[ElectionResult]
    coalitions: CoalitionAnalysis
    constitution: ConstitutionalAnalysis
    health: DemocraticHealth
    insights: List[GovernanceInsight]

    def to_dict(self) -> dict:
        return {
            "n_cells": self.n_cells,
            "cells": [c.to_dict() for c in self.cells],
            "proposals": [p.to_dict() for p in self.proposals],
            "elections": [e.to_dict() for e in self.elections],
            "coalitions": self.coalitions.to_dict(),
            "constitution": self.constitution.to_dict(),
            "health": self.health.to_dict(),
            "insights": [i.to_dict() for i in self.insights],
        }

    def to_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def to_html(self, path: str) -> None:
        html = _render_html(self)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    def summary_text(self) -> str:
        lines = [
            "=" * 60,
            "SPATIAL GOVERNANCE ENGINE -- Analysis Report",
            "=" * 60,
            f"Cells: {self.n_cells}",
            f"Democratic Health: {self.health.score:.1f}/100 ({self.health.grade})",
            "",
            "--- Voting Weights ---",
        ]
        for c in self.cells:
            flags = []
            if c.is_dictator:
                flags.append("DICTATOR")
            if c.is_dummy:
                flags.append("DUMMY")
            if c.has_veto:
                flags.append("VETO")
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            lines.append(
                f"  Cell {c.cell_idx}: weight={c.weight:.3f}  "
                f"Banzhaf={c.banzhaf_power:.4f}  "
                f"Shapley={c.shapley_power:.4f}{flag_str}"
            )
        lines.append("")
        lines.append("--- Elections ---")
        for e in self.elections:
            lines.append(f"  {e.system}: winner={e.winner}  scores={e.scores}")
        lines.append("")
        lines.append("--- Coalitions ---")
        lines.append(f"  Winning: {self.coalitions.winning_coalitions}/{self.coalitions.total_coalitions}")
        lines.append(f"  Minimal winning: {self.coalitions.minimal_winning_coalitions}")
        lines.append(f"  Veto players: {self.coalitions.veto_players}")
        lines.append(f"  Fragility: {self.coalitions.fragility_score:.3f}")
        lines.append("")
        lines.append("--- Constitution ---")
        lines.append(f"  Quota: {self.constitution.quota:.2f} ({self.constitution.quota_fraction:.1%})")
        lines.append(f"  Dictator: {'Cell ' + str(self.constitution.dictator_idx) if self.constitution.has_dictator else 'None'}")
        lines.append(f"  Dummies: {self.constitution.dummy_indices or 'None'}")
        lines.append(f"  Power deviation: {self.constitution.power_deviation:.4f}")
        lines.append("")
        lines.append("--- Health Dimensions ---")
        lines.append(f"  Representation equality: {self.health.representation_equality:.1f}")
        lines.append(f"  Power dispersion:        {self.health.power_dispersion:.1f}")
        lines.append(f"  Coalition stability:     {self.health.coalition_stability:.1f}")
        lines.append(f"  Constitutional soundness:{self.health.constitutional_soundness:.1f}")
        lines.append(f"  Voting consistency:      {self.health.voting_consistency:.1f}")
        if self.insights:
            lines.append("")
            lines.append("--- Insights ---")
            for ins in self.insights[:10]:
                lines.append(f"  [{ins.severity.upper()}] {ins.title}: {ins.detail}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Engine 1: Weight Assigner
# ---------------------------------------------------------------------------

def assign_weights(
    points: List[Tuple[float, float]],
    bounds: Tuple[float, float, float, float],
    method: str = "strategic",
    cells: Optional[List[List[Tuple[float, float]]]] = None,
) -> List[CellVoter]:
    """Assign voting weights to Voronoi cells.

    Methods:
    - equal: all cells get weight 1.0
    - area: weight proportional to cell area
    - population: weight ~ area * centrality (proxy for population density)
    - strategic: composite of area, neighbor count, centrality
    """
    if cells is None:
        cells = _voronoi_cells_simple(points, bounds)

    n = len(points)
    areas = [_polygon_area(c) for c in cells]
    centroids = [_centroid(c) for c in cells]
    total_area = sum(areas) or 1.0

    # Compute neighbors (cells sharing a Delaunay edge ~ close points)
    neighbor_counts = [0] * n
    threshold = max((bounds[1] - bounds[0]), (bounds[3] - bounds[2])) / math.sqrt(n) * 1.5
    for i in range(n):
        for j in range(i + 1, n):
            if _euclidean(points[i], points[j]) < threshold:
                neighbor_counts[i] += 1
                neighbor_counts[j] += 1

    # Centrality: inverse distance from spatial center
    cx = (bounds[0] + bounds[1]) / 2
    cy = (bounds[2] + bounds[3]) / 2
    center = (cx, cy)
    max_dist = _euclidean(center, (bounds[0], bounds[2])) or 1.0
    centralities = [1.0 - _euclidean(centroids[i], center) / max_dist for i in range(n)]
    centralities = [max(0.01, c) for c in centralities]

    voters = []
    for i in range(n):
        if method == "equal":
            w = 1.0
        elif method == "area":
            w = areas[i] / total_area * n
        elif method == "population":
            w = (areas[i] / total_area) * centralities[i] * n
        else:  # strategic
            area_norm = areas[i] / total_area
            neigh_norm = neighbor_counts[i] / max(max(neighbor_counts), 1)
            w = (0.4 * area_norm + 0.3 * neigh_norm + 0.3 * centralities[i]) * n

        voters.append(CellVoter(
            cell_idx=i,
            centroid=centroids[i],
            area=areas[i],
            neighbors=neighbor_counts[i],
            centrality=centralities[i],
            weight=max(w, 0.01),
        ))
    return voters


# ---------------------------------------------------------------------------
# Engine 2: Power Index Calculator
# ---------------------------------------------------------------------------

def compute_power_indices(
    voters: List[CellVoter],
    quota_fraction: float = 0.5,
) -> Tuple[List[CellVoter], float]:
    """Compute Banzhaf and Shapley-Shubik power indices.

    Returns updated voters and the actual quota value.
    """
    n = len(voters)
    weights = [v.weight for v in voters]
    total_weight = sum(weights)
    quota = total_weight * quota_fraction

    # --- Banzhaf power index ---
    # For each player, count how many coalitions they are critical in
    banzhaf_raw = [0] * n

    # Enumerate all 2^n coalitions (feasible for n <= 20)
    if n <= 20:
        for mask in range(1, 1 << n):
            coalition_weight = sum(weights[i] for i in range(n) if mask & (1 << i))
            if coalition_weight >= quota:
                # Check each member: are they critical?
                for i in range(n):
                    if mask & (1 << i):
                        without = coalition_weight - weights[i]
                        if without < quota:
                            banzhaf_raw[i] += 1
    else:
        # Monte Carlo approximation for large n
        samples = 50000
        rng = random.Random(42)
        for _ in range(samples):
            mask = rng.getrandbits(n)
            if mask == 0:
                continue
            coalition_weight = sum(weights[i] for i in range(n) if mask & (1 << i))
            if coalition_weight >= quota:
                for i in range(n):
                    if mask & (1 << i):
                        without = coalition_weight - weights[i]
                        if without < quota:
                            banzhaf_raw[i] += 1

    total_banzhaf = sum(banzhaf_raw) or 1
    for i in range(n):
        voters[i].banzhaf_power = banzhaf_raw[i] / total_banzhaf

    # --- Shapley-Shubik power index ---
    # Use sampling for efficiency
    shapley_raw = [0] * n
    if n <= 10:
        # Exact: enumerate all permutations (feasible up to ~10)
        from itertools import permutations
        count = 0
        for perm in permutations(range(n)):
            running = 0.0
            for i in perm:
                running += weights[i]
                if running >= quota:
                    shapley_raw[i] += 1
                    break
            count += 1
    else:
        # Monte Carlo
        count = 20000
        rng = random.Random(42)
        indices = list(range(n))
        for _ in range(count):
            perm = indices[:]
            rng.shuffle(perm)
            running = 0.0
            for i in perm:
                running += weights[i]
                if running >= quota:
                    shapley_raw[i] += 1
                    break

    total_shapley = sum(shapley_raw) or 1
    for i in range(n):
        voters[i].shapley_power = shapley_raw[i] / total_shapley

    # Identify dictators, dummies, veto players
    for i in range(n):
        voters[i].is_dictator = weights[i] >= quota
        voters[i].is_dummy = banzhaf_raw[i] == 0
        # Veto: present in ALL winning coalitions
        voters[i].has_veto = False

    # Check veto: a player has veto if total_weight - weight[i] < quota
    for i in range(n):
        if total_weight - weights[i] < quota:
            voters[i].has_veto = True

    return voters, quota


# ---------------------------------------------------------------------------
# Engine 3: Voting System Simulator
# ---------------------------------------------------------------------------

def _generate_proposals(
    voters: List[CellVoter],
    bounds: Tuple[float, float, float, float],
    n_proposals: int = 4,
    seed: int = 42,
) -> List[Proposal]:
    """Generate spatial proposals for voting."""
    rng = random.Random(seed)
    proposals = []
    names = [
        "Central Park", "Transit Hub", "Tech Campus", "Green Belt",
        "Market District", "Cultural Center", "Solar Farm", "Waterfront",
    ]
    descs = [
        "Public green space in the spatial center",
        "Transportation infrastructure connecting regions",
        "Innovation and technology development zone",
        "Environmental protection corridor",
        "Commercial and retail district",
        "Arts, education, and community facilities",
        "Renewable energy generation facility",
        "Recreational waterfront development",
    ]
    for i in range(min(n_proposals, len(names))):
        loc = (
            rng.uniform(bounds[0], bounds[1]),
            rng.uniform(bounds[2], bounds[3]),
        )
        proposals.append(Proposal(
            proposal_id=f"P{i+1}",
            name=names[i],
            location=loc,
            description=descs[i],
        ))
    return proposals


def _cell_preference(
    voter: CellVoter,
    proposals: List[Proposal],
    rng: random.Random,
) -> List[str]:
    """Generate preference ranking for a cell based on spatial proximity + noise."""
    scored = []
    for p in proposals:
        if p.location:
            dist = _euclidean(voter.centroid, p.location)
            # Closer proposals preferred, with some noise
            score = -dist + rng.gauss(0, 50)
        else:
            score = rng.gauss(0, 100)
        scored.append((p.proposal_id, score))
    scored.sort(key=lambda x: -x[1])
    return [s[0] for s in scored]


def run_elections(
    voters: List[CellVoter],
    proposals: List[Proposal],
    seed: int = 42,
) -> List[ElectionResult]:
    """Run elections under 4 voting systems."""
    rng = random.Random(seed)
    n = len(voters)

    # Generate preference profiles
    preferences = {}  # cell_idx -> ranked list of proposal_ids
    for v in voters:
        preferences[v.cell_idx] = _cell_preference(v, proposals, rng)

    results = []

    # 1. Plurality: each cell votes for top choice, weighted
    plurality_scores: Dict[str, float] = {p.proposal_id: 0.0 for p in proposals}
    for v in voters:
        top = preferences[v.cell_idx][0]
        plurality_scores[top] += v.weight
    winner = max(plurality_scores, key=lambda k: plurality_scores[k])
    results.append(ElectionResult(
        system="plurality",
        winner=winner,
        scores=plurality_scores,
    ))

    # 2. Borda count: n-1 points for 1st, n-2 for 2nd, etc., weighted
    borda_scores: Dict[str, float] = {p.proposal_id: 0.0 for p in proposals}
    np_ = len(proposals)
    for v in voters:
        for rank, pid in enumerate(preferences[v.cell_idx]):
            borda_scores[pid] += (np_ - 1 - rank) * v.weight
    winner = max(borda_scores, key=lambda k: borda_scores[k])
    results.append(ElectionResult(
        system="borda",
        winner=winner,
        scores=borda_scores,
    ))

    # 3. Approval voting: each cell approves top half of proposals, weighted
    approval_scores: Dict[str, float] = {p.proposal_id: 0.0 for p in proposals}
    approve_count = max(1, np_ // 2)
    for v in voters:
        for pid in preferences[v.cell_idx][:approve_count]:
            approval_scores[pid] += v.weight
    winner = max(approval_scores, key=lambda k: approval_scores[k])
    results.append(ElectionResult(
        system="approval",
        winner=winner,
        scores=approval_scores,
    ))

    # 4. Instant Runoff Voting (IRV)
    irv_rounds = []
    active = set(p.proposal_id for p in proposals)
    remaining_prefs = {v.cell_idx: list(preferences[v.cell_idx]) for v in voters}
    irv_winner = None
    total_weight = sum(v.weight for v in voters)
    weight_map = {v.cell_idx: v.weight for v in voters}

    for round_num in range(np_):
        round_scores: Dict[str, float] = {pid: 0.0 for pid in active}
        for v in voters:
            for pid in remaining_prefs[v.cell_idx]:
                if pid in active:
                    round_scores[pid] += weight_map[v.cell_idx]
                    break

        irv_rounds.append({
            "round": round_num + 1,
            "scores": {k: round(v, 4) for k, v in round_scores.items()},
            "eliminated": None,
        })

        # Check majority
        for pid, s in round_scores.items():
            if s > total_weight / 2:
                irv_winner = pid
                break
        if irv_winner:
            break

        if len(active) <= 1:
            irv_winner = list(active)[0] if active else proposals[0].proposal_id
            break

        # Eliminate lowest
        lowest = min(active, key=lambda k: round_scores.get(k, 0))
        irv_rounds[-1]["eliminated"] = lowest
        active.discard(lowest)

    if not irv_winner:
        irv_winner = max(active, key=lambda k: round_scores.get(k, 0)) if active else proposals[0].proposal_id

    irv_final_scores = {p.proposal_id: 0.0 for p in proposals}
    for v in voters:
        for pid in remaining_prefs[v.cell_idx]:
            if pid in active:
                irv_final_scores[pid] += weight_map[v.cell_idx]
                break

    results.append(ElectionResult(
        system="irv",
        winner=irv_winner,
        scores=irv_final_scores,
        rounds=irv_rounds,
    ))

    return results


# ---------------------------------------------------------------------------
# Engine 4: Coalition Analyzer
# ---------------------------------------------------------------------------

def analyze_coalitions(
    voters: List[CellVoter],
    quota: float,
) -> CoalitionAnalysis:
    """Analyze winning, blocking, and minimal winning coalitions."""
    n = len(voters)
    weights = [v.weight for v in voters]
    total_weight = sum(weights)

    winning_count = 0
    minimal_winning_count = 0
    blocking_count = 0
    veto_set = set(range(n))  # Start assuming all have veto, remove as we go
    top_coalitions: List[Coalition] = []

    max_enumerate = min(1 << n, 1 << 18)  # cap at 2^18

    for mask in range(1, max_enumerate):
        members = [i for i in range(n) if mask & (1 << i)]
        cw = sum(weights[i] for i in members)

        if cw >= quota:
            winning_count += 1

            # Check minimal: removing any member makes it losing
            pivots = []
            is_minimal = True
            for m in members:
                if cw - weights[m] < quota:
                    pivots.append(m)
                else:
                    is_minimal = False

            if is_minimal:
                minimal_winning_count += 1

            coal = Coalition(
                members=members,
                total_weight=cw,
                is_winning=True,
                is_minimal_winning=is_minimal,
                pivot_members=pivots,
            )
            if len(top_coalitions) < 20 or is_minimal:
                top_coalitions.append(coal)
        else:
            # Non-winning: check if it's blocking (complement can't win)
            complement_weight = total_weight - cw
            if complement_weight < quota:
                blocking_count += 1

        # Veto: remove from veto set if there's a winning coalition without them
        if cw >= quota:
            non_members = set(range(n)) - set(members)
            veto_set -= non_members

    # Fragility: ratio of minimal to total winning
    fragility = minimal_winning_count / max(winning_count, 1)

    # Sort top coalitions: minimal winning first, then by size
    top_coalitions.sort(key=lambda c: (not c.is_minimal_winning, len(c.members)))

    return CoalitionAnalysis(
        total_coalitions=max_enumerate - 1,
        winning_coalitions=winning_count,
        minimal_winning_coalitions=minimal_winning_count,
        blocking_coalitions=blocking_count,
        veto_players=sorted(veto_set),
        fragility_score=fragility,
        top_coalitions=top_coalitions[:10],
    )


# ---------------------------------------------------------------------------
# Engine 5: Constitutional Designer
# ---------------------------------------------------------------------------

def design_constitution(
    voters: List[CellVoter],
    quota: float,
    quota_fraction: float,
) -> ConstitutionalAnalysis:
    """Analyze constitutional properties and recommend improvements."""
    n = len(voters)
    weights = [v.weight for v in voters]
    total_weight = sum(weights)

    # Dictator: single voter whose weight >= quota
    has_dictator = False
    dictator_idx = None
    for v in voters:
        if v.is_dictator:
            has_dictator = True
            dictator_idx = v.cell_idx
            break

    # Dummies
    dummy_indices = [v.cell_idx for v in voters if v.is_dummy]

    # Power deviation: sum of |weight_share - banzhaf_power|
    weight_shares = [w / total_weight for w in weights]
    power_dev = sum(abs(weight_shares[i] - voters[i].banzhaf_power) for i in range(n)) / 2

    # Find optimal quota: minimize power deviation
    best_quota_frac = quota_fraction
    best_dev = power_dev
    for test_frac in [x / 20 for x in range(10, 19)]:  # 0.50 to 0.90
        test_quota = total_weight * test_frac
        # Quick Banzhaf approximation
        test_banzhaf = [0] * n
        sample_size = min(1 << n, 1 << 16)
        for mask in range(1, sample_size):
            cw = sum(weights[i] for i in range(n) if mask & (1 << i))
            if cw >= test_quota:
                for i in range(n):
                    if (mask & (1 << i)) and cw - weights[i] < test_quota:
                        test_banzhaf[i] += 1
        tb_total = sum(test_banzhaf) or 1
        test_powers = [b / tb_total for b in test_banzhaf]
        dev = sum(abs(weight_shares[i] - test_powers[i]) for i in range(n)) / 2
        if dev < best_dev:
            best_dev = dev
            best_quota_frac = test_frac

    # Recommendations
    recs = []
    if has_dictator:
        recs.append(f"CRITICAL: Cell {dictator_idx} is a dictator — can pass any proposal alone. "
                     "Consider reducing its weight or raising the quota.")
    if dummy_indices:
        recs.append(f"WARNING: Cells {dummy_indices} are dummies with zero power. "
                     "Consider merging them or adjusting weights.")
    if power_dev > 0.15:
        recs.append(f"Power deviation is high ({power_dev:.3f}). "
                     f"Optimal quota fraction: {best_quota_frac:.2f} (current: {quota_fraction:.2f}).")
    if len([v for v in voters if v.has_veto]) > n // 2:
        recs.append("Many veto players detected — may cause governance gridlock.")
    if not recs:
        recs.append("Constitutional design is sound. No critical issues detected.")

    return ConstitutionalAnalysis(
        quota=quota,
        quota_fraction=quota_fraction,
        has_dictator=has_dictator,
        dictator_idx=dictator_idx,
        dummy_count=len(dummy_indices),
        dummy_indices=dummy_indices,
        optimal_quota=total_weight * best_quota_frac,
        power_deviation=power_dev,
        recommendations=recs,
    )


# ---------------------------------------------------------------------------
# Engine 6: Democratic Health Scorer
# ---------------------------------------------------------------------------

def score_democratic_health(
    voters: List[CellVoter],
    elections: List[ElectionResult],
    coalitions: CoalitionAnalysis,
    constitution: ConstitutionalAnalysis,
) -> DemocraticHealth:
    """Compute composite democratic health score."""
    n = len(voters)

    # 1. Representation equality (0-100): Gini of voting weights
    weights = sorted(v.weight for v in voters)
    if n > 1 and sum(weights) > 0:
        gini_sum = sum(
            abs(weights[i] - weights[j])
            for i in range(n)
            for j in range(i + 1, n)
        )
        gini = gini_sum / (n * sum(weights)) if sum(weights) > 0 else 0
        representation = max(0, (1 - gini * 2)) * 100  # Gini 0=perfect, 0.5=max
    else:
        representation = 100.0

    # 2. Power dispersion (0-100): entropy of Banzhaf powers
    powers = [v.banzhaf_power for v in voters]
    max_entropy = math.log(n) if n > 1 else 1.0
    entropy = -sum(p * math.log(p) for p in powers if p > 0)
    power_disp = (entropy / max_entropy * 100) if max_entropy > 0 else 100.0

    # 3. Coalition stability (0-100): inverse of fragility
    coal_stability = (1 - coalitions.fragility_score) * 100

    # 4. Constitutional soundness (0-100)
    const_score = 100.0
    if constitution.has_dictator:
        const_score -= 50
    const_score -= constitution.dummy_count * 10
    const_score -= constitution.power_deviation * 100
    const_score = max(0, const_score)

    # 5. Voting consistency: do different systems agree?
    if elections:
        winners = [e.winner for e in elections]
        most_common = max(set(winners), key=winners.count)
        agreement = winners.count(most_common) / len(winners)
        voting_cons = agreement * 100
    else:
        voting_cons = 50.0

    # Composite
    score = (
        0.25 * representation
        + 0.25 * power_disp
        + 0.20 * coal_stability
        + 0.15 * const_score
        + 0.15 * voting_cons
    )

    grade = (
        "A" if score >= 85 else
        "B" if score >= 70 else
        "C" if score >= 55 else
        "D" if score >= 40 else
        "F"
    )

    return DemocraticHealth(
        score=score,
        grade=grade,
        representation_equality=representation,
        power_dispersion=power_disp,
        coalition_stability=coal_stability,
        constitutional_soundness=const_score,
        voting_consistency=voting_cons,
    )


# ---------------------------------------------------------------------------
# Engine 7: Insight Generator
# ---------------------------------------------------------------------------

def generate_insights(
    voters: List[CellVoter],
    elections: List[ElectionResult],
    coalitions: CoalitionAnalysis,
    constitution: ConstitutionalAnalysis,
    health: DemocraticHealth,
) -> List[GovernanceInsight]:
    """Generate autonomous governance insights."""
    insights = []
    n = len(voters)

    # Power concentration
    powers = [(v.cell_idx, v.banzhaf_power) for v in voters]
    powers.sort(key=lambda x: -x[1])
    if powers and powers[0][1] > 0.4:
        insights.append(GovernanceInsight(
            category="power_imbalance",
            severity="critical",
            title="Extreme power concentration",
            detail=f"Cell {powers[0][0]} holds {powers[0][1]:.1%} of Banzhaf power. "
                   "This creates a de-facto hegemon that can dominate all decisions.",
            affected_cells=[powers[0][0]],
        ))

    # Power vs weight mismatch
    for v in voters:
        total_w = sum(vv.weight for vv in voters)
        weight_share = v.weight / total_w if total_w > 0 else 0
        diff = abs(v.banzhaf_power - weight_share)
        if diff > 0.1:
            over_under = "over-represented" if v.banzhaf_power > weight_share else "under-represented"
            insights.append(GovernanceInsight(
                category="power_imbalance",
                severity="high",
                title=f"Cell {v.cell_idx} is {over_under}",
                detail=f"Weight share: {weight_share:.1%}, Banzhaf power: {v.banzhaf_power:.1%}. "
                       f"Difference of {diff:.1%} indicates the voting system distorts representation.",
                affected_cells=[v.cell_idx],
            ))

    # Dummy players
    dummies = [v.cell_idx for v in voters if v.is_dummy]
    if dummies:
        insights.append(GovernanceInsight(
            category="structural_weakness",
            severity="high",
            title="Powerless voters detected",
            detail=f"Cells {dummies} have zero Banzhaf power — their votes never matter. "
                   "Consider redistributing weights or lowering the quota.",
            affected_cells=dummies,
        ))

    # Veto gridlock risk
    veto = coalitions.veto_players
    if len(veto) >= 2:
        insights.append(GovernanceInsight(
            category="warning",
            severity="medium",
            title="Gridlock risk from multiple veto players",
            detail=f"{len(veto)} cells ({veto}) each have veto power. "
                   "Any one of them can block any proposal, risking legislative paralysis.",
            affected_cells=veto,
        ))

    # Election disagreement
    if elections:
        winners = set(e.winner for e in elections)
        if len(winners) > 2:
            insights.append(GovernanceInsight(
                category="warning",
                severity="medium",
                title="Voting systems produce divergent outcomes",
                detail=f"{len(winners)} different winners across {len(elections)} voting systems. "
                       "The choice of voting rule heavily influences outcomes.",
                affected_cells=[],
            ))

    # Fragility
    if coalitions.fragility_score > 0.7:
        insights.append(GovernanceInsight(
            category="structural_weakness",
            severity="medium",
            title="High coalition fragility",
            detail=f"Fragility score: {coalitions.fragility_score:.2f}. "
                   "Most winning coalitions are minimal — losing a single member flips the outcome.",
            affected_cells=[],
        ))

    # Central vs peripheral power
    central = [v for v in voters if v.centrality > 0.7]
    peripheral = [v for v in voters if v.centrality < 0.3]
    if central and peripheral:
        central_power = sum(v.banzhaf_power for v in central)
        peripheral_power = sum(v.banzhaf_power for v in peripheral)
        if central_power > 3 * peripheral_power and peripheral_power > 0:
            insights.append(GovernanceInsight(
                category="opportunity",
                severity="info",
                title="Center-periphery power gap",
                detail=f"Central cells hold {central_power:.1%} of power vs {peripheral_power:.1%} "
                       "for peripheral cells. Decentralization reforms could improve equity.",
                affected_cells=[v.cell_idx for v in peripheral],
            ))

    # Democratic health warning
    if health.score < 40:
        insights.append(GovernanceInsight(
            category="warning",
            severity="critical",
            title="Democratic health crisis",
            detail=f"Overall score {health.score:.1f}/100 (grade {health.grade}). "
                   "Multiple governance dimensions need urgent reform.",
            affected_cells=[],
        ))

    return insights


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_governance(
    points: List[Tuple[float, float]],
    bounds: Tuple[float, float, float, float] = (0, 1000, 0, 1000),
    weight_method: str = "strategic",
    quota_fraction: float = 0.5,
    n_proposals: int = 4,
    seed: int = 42,
) -> GovernanceReport:
    """Run full spatial governance analysis.

    Parameters
    ----------
    points : list of (x, y) tuples
        Voronoi generator points.
    bounds : (south, north, west, east)
        Bounding rectangle.
    weight_method : str
        How to assign voting weights: equal, area, population, strategic.
    quota_fraction : float
        Fraction of total weight needed to win (0.5 = simple majority).
    n_proposals : int
        Number of spatial proposals to generate.
    seed : int
        Random seed for reproducibility.

    Returns
    -------
    GovernanceReport
    """
    # Engine 1: Assign weights
    voters = assign_weights(points, bounds, method=weight_method)

    # Engine 2: Power indices
    voters, quota = compute_power_indices(voters, quota_fraction)

    # Engine 3: Elections
    proposals = _generate_proposals(voters, bounds, n_proposals=n_proposals, seed=seed)
    elections = run_elections(voters, proposals, seed=seed)

    # Engine 4: Coalitions
    coal_analysis = analyze_coalitions(voters, quota)

    # Engine 5: Constitution
    const_analysis = design_constitution(voters, quota, quota_fraction)

    # Engine 6: Health
    health = score_democratic_health(voters, elections, coal_analysis, const_analysis)

    # Engine 7: Insights
    insights = generate_insights(voters, elections, coal_analysis, const_analysis, health)

    return GovernanceReport(
        n_cells=len(points),
        cells=voters,
        proposals=proposals,
        elections=elections,
        coalitions=coal_analysis,
        constitution=const_analysis,
        health=health,
        insights=insights,
    )


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

def _render_html(report: GovernanceReport) -> str:
    health = report.health
    grade_color = {
        "A": "#22c55e", "B": "#84cc16", "C": "#eab308", "D": "#f97316", "F": "#ef4444",
    }.get(health.grade, "#94a3b8")

    severity_colors = {
        "critical": "#ef4444", "high": "#f97316", "medium": "#eab308",
        "low": "#84cc16", "info": "#3b82f6",
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Spatial Governance Engine</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0f172a;color:#e2e8f0;font-family:'Segoe UI',system-ui,sans-serif;padding:20px}}
h1{{text-align:center;font-size:1.8em;margin-bottom:4px;color:#f8fafc}}
.subtitle{{text-align:center;color:#64748b;margin-bottom:20px;font-size:0.9em}}
.score-ring{{text-align:center;margin:20px auto}}
.score-ring .value{{font-size:3.5em;font-weight:800;color:{grade_color}}}
.score-ring .label{{color:#94a3b8;font-size:0.9em}}
.tabs{{display:flex;gap:4px;margin-bottom:16px;flex-wrap:wrap}}
.tab{{padding:8px 16px;background:#1e293b;border:1px solid #334155;border-radius:6px;cursor:pointer;color:#94a3b8;font-size:0.85em}}
.tab.active{{background:#334155;color:#f8fafc;border-color:#3b82f6}}
.panel{{display:none}} .panel.active{{display:block}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px;margin-bottom:16px}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:16px}}
.card h3{{color:#f8fafc;margin-bottom:8px;font-size:1em}}
.metric{{display:inline-block;text-align:center;margin:8px 12px}}
.metric .value{{font-size:1.6em;font-weight:700}}
.metric .label{{color:#94a3b8;font-size:0.75em}}
table{{width:100%;border-collapse:collapse;font-size:0.82em;margin-top:8px}}
th{{background:#334155;color:#f8fafc;padding:6px 8px;text-align:left}}
td{{padding:5px 8px;border-bottom:1px solid #1e293b}}
tr:hover td{{background:#1e293b80}}
.bar-chart{{margin:8px 0}}
.bar-row{{display:flex;align-items:center;margin:3px 0}}
.bar-label{{width:70px;font-size:0.75em;color:#94a3b8;text-align:right;padding-right:8px}}
.bar-bg{{flex:1;height:18px;background:#0f172a;border-radius:4px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:4px;transition:width 0.5s}}
.insight{{padding:10px;margin:6px 0;border-radius:6px;border-left:4px solid;font-size:0.85em}}
.badge{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:0.7em;font-weight:600;color:#fff}}
</style>
</head>
<body>
<h1>🏛️ Spatial Governance Engine</h1>
<div class="subtitle">{report.n_cells} cells · Quota {report.constitution.quota_fraction:.0%} · {len(report.proposals)} proposals</div>

<div class="score-ring">
  <div class="value">{health.score:.0f}</div>
  <div class="label">Democratic Health ({health.grade})</div>
</div>

<div class="grid">
  <div class="card"><div class="metric"><div class="value" style="color:#3b82f6">{health.representation_equality:.0f}</div><div class="label">Representation</div></div></div>
  <div class="card"><div class="metric"><div class="value" style="color:#8b5cf6">{health.power_dispersion:.0f}</div><div class="label">Power Dispersion</div></div></div>
  <div class="card"><div class="metric"><div class="value" style="color:#22c55e">{health.coalition_stability:.0f}</div><div class="label">Coalition Stability</div></div></div>
  <div class="card"><div class="metric"><div class="value" style="color:#f59e0b">{health.constitutional_soundness:.0f}</div><div class="label">Constitution</div></div></div>
  <div class="card"><div class="metric"><div class="value" style="color:#ec4899">{health.voting_consistency:.0f}</div><div class="label">Voting Consistency</div></div></div>
</div>

<div class="tabs">
  <div class="tab active" onclick="showTab(0)">Power Indices</div>
  <div class="tab" onclick="showTab(1)">Elections</div>
  <div class="tab" onclick="showTab(2)">Coalitions</div>
  <div class="tab" onclick="showTab(3)">Constitution</div>
  <div class="tab" onclick="showTab(4)">Insights</div>
</div>

<div class="panel active" id="panel0">
  <div class="card">
    <h3>Voting Weights & Power Indices</h3>
    <table>
      <tr><th>Cell</th><th>Weight</th><th>Banzhaf</th><th>Shapley</th><th>Neighbors</th><th>Centrality</th><th>Status</th></tr>
      {"".join(f'<tr><td>{c.cell_idx}</td><td>{c.weight:.3f}</td><td>{c.banzhaf_power:.4f}</td><td>{c.shapley_power:.4f}</td><td>{c.neighbors}</td><td>{c.centrality:.3f}</td><td>{"<span class=badge style=background:#ef4444>DICTATOR</span>" if c.is_dictator else "<span class=badge style=background:#f97316>VETO</span>" if c.has_veto else "<span class=badge style=background:#64748b>DUMMY</span>" if c.is_dummy else "—"}</td></tr>' for c in report.cells)}
    </table>
  </div>
  <div class="card">
    <h3>Banzhaf Power Distribution</h3>
    <div class="bar-chart">
      {"".join(f'<div class="bar-row"><div class="bar-label">Cell {c.cell_idx}</div><div class="bar-bg"><div class="bar-fill" style="width:{c.banzhaf_power * 100 * len(report.cells):.0f}%;background:#3b82f6"></div></div></div>' for c in report.cells)}
    </div>
  </div>
  <div class="card">
    <h3>Shapley-Shubik Power Distribution</h3>
    <div class="bar-chart">
      {"".join(f'<div class="bar-row"><div class="bar-label">Cell {c.cell_idx}</div><div class="bar-bg"><div class="bar-fill" style="width:{c.shapley_power * 100 * len(report.cells):.0f}%;background:#8b5cf6"></div></div></div>' for c in report.cells)}
    </div>
  </div>
</div>

<div class="panel" id="panel1">
  <div class="grid">
    {"".join(f'''<div class="card">
      <h3>{e.system.upper()}</h3>
      <p style="color:#22c55e;font-size:1.1em">Winner: {e.winner}</p>
      <div class="bar-chart">
        {"".join(f'<div class="bar-row"><div class="bar-label">{pid}</div><div class="bar-bg"><div class="bar-fill" style="width:{sc / max(e.scores.values(), default=1) * 100:.0f}%;background:#3b82f6"></div></div></div>' for pid, sc in sorted(e.scores.items(), key=lambda x: -x[1]))}
      </div>
      {f'<p style="color:#94a3b8;font-size:0.75em">{len(e.rounds)} rounds</p>' if e.rounds else ''}
    </div>''' for e in report.elections)}
  </div>
</div>

<div class="panel" id="panel2">
  <div class="card">
    <h3>Coalition Summary</h3>
    <div class="grid" style="display:flex;gap:20px;flex-wrap:wrap">
      <div class="metric"><div class="value" style="color:#3b82f6">{report.coalitions.winning_coalitions}</div><div class="label">Winning</div></div>
      <div class="metric"><div class="value" style="color:#8b5cf6">{report.coalitions.minimal_winning_coalitions}</div><div class="label">Minimal Winning</div></div>
      <div class="metric"><div class="value" style="color:#f59e0b">{report.coalitions.blocking_coalitions}</div><div class="label">Blocking</div></div>
      <div class="metric"><div class="value" style="color:#ef4444">{report.coalitions.fragility_score:.2f}</div><div class="label">Fragility</div></div>
    </div>
    <p style="margin-top:12px;color:#94a3b8;font-size:0.85em">Veto players: {report.coalitions.veto_players or 'None'}</p>
  </div>
  <div class="card">
    <h3>Top Coalitions</h3>
    <table>
      <tr><th>Members</th><th>Weight</th><th>Minimal?</th><th>Pivots</th></tr>
      {"".join(f'<tr><td>{c.members}</td><td>{c.total_weight:.3f}</td><td>{"✅" if c.is_minimal_winning else "—"}</td><td>{c.pivot_members}</td></tr>' for c in report.coalitions.top_coalitions[:10])}
    </table>
  </div>
</div>

<div class="panel" id="panel3">
  <div class="card">
    <h3>Constitutional Analysis</h3>
    <div style="display:flex;gap:20px;flex-wrap:wrap;margin-bottom:12px">
      <div class="metric"><div class="value" style="color:#3b82f6">{report.constitution.quota_fraction:.0%}</div><div class="label">Quota</div></div>
      <div class="metric"><div class="value" style="color:#ef4444">{report.constitution.power_deviation:.4f}</div><div class="label">Power Deviation</div></div>
      <div class="metric"><div class="value" style="color:#f59e0b">{report.constitution.dummy_count}</div><div class="label">Dummies</div></div>
    </div>
    <h3>Recommendations</h3>
    {"".join(f'<div class="insight" style="border-color:#3b82f6;background:#1e293b">{r}</div>' for r in report.constitution.recommendations)}
  </div>
</div>

<div class="panel" id="panel4">
  {"".join(f'<div class="insight" style="border-color:{severity_colors.get(i.severity, "#64748b")};background:#1e293b"><strong>[{i.severity.upper()}]</strong> {i.title}<br><span style="color:#94a3b8">{i.detail}</span></div>' for i in report.insights) if report.insights else '<div class="card"><p style="color:#94a3b8">No insights generated — governance looks healthy!</p></div>'}
</div>

<script>
function showTab(idx) {{
  document.querySelectorAll('.tab').forEach((t, i) => t.classList.toggle('active', i === idx));
  document.querySelectorAll('.panel').forEach((p, i) => p.classList.toggle('active', i === idx));
}}
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="Spatial Governance Engine")
    parser.add_argument("--demo", action="store_true", help="Run with demo data")
    parser.add_argument("--weight-method", choices=WEIGHT_METHODS, default="strategic")
    parser.add_argument("--quota", type=float, default=0.5, help="Quota fraction (0.5 = majority)")
    parser.add_argument("--n-proposals", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", dest="json_path", help="Output JSON path")
    parser.add_argument("--html", dest="html_path", help="Output HTML path")
    args = parser.parse_args()

    points = [
        (150, 200), (400, 350), (700, 150), (250, 700), (600, 600),
        (100, 500), (800, 400), (500, 800), (350, 100), (900, 900),
    ]
    bounds = (0, 1000, 0, 1000)

    report = run_governance(
        points, bounds=bounds,
        weight_method=args.weight_method,
        quota_fraction=args.quota,
        n_proposals=args.n_proposals,
        seed=args.seed,
    )

    print(report.summary_text())

    if args.json_path:
        report.to_json(args.json_path)
        print(f"\nJSON saved to {args.json_path}")
    if args.html_path:
        report.to_html(args.html_path)
        print(f"\nHTML dashboard saved to {args.html_path}")


if __name__ == "__main__":
    _cli()
