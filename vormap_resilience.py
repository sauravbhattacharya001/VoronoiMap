"""Spatial Resilience Analyzer -- simulate failures and identify critical infrastructure.

Performs systematic failure analysis on Voronoi point patterns by
simulating the removal of each point and measuring the impact on
the spatial network.  Identifies single points of failure, cascading
vulnerability chains, and recommends optimal redundancy placements.

This is an *agentic* capability -- the tool autonomously stress-tests
your spatial layout, identifies weaknesses, and prescribes fixes
before real failures occur.

Six analysis channels:

- **Impact Scoring** -- remove each point individually, measure area
  redistribution impact on neighbors (max neighbor area increase,
  coverage gap size, connectivity loss).
- **Criticality Ranking** -- rank all points by composite failure impact
  score; top-ranked points are single points of failure.
- **Cascade Analysis** -- simulate sequential failures starting from
  highest-impact points to measure cascading degradation.
- **Redundancy Mapping** -- for each critical point, suggest where to
  place a backup point that minimizes failure impact.
- **Resilience Score** -- overall 0-100 score measuring how well the
  layout tolerates random and targeted failures.
- **What-If Scenarios** -- simulate removal of user-specified point
  subsets and report the combined impact.

Usage (Python API)::

    from vormap_resilience import ResilienceAnalyzer, analyze_resilience

    # Quick analysis
    result = analyze_resilience("points.txt")
    print(f"Resilience score: {result.resilience_score}/100")
    for cp in result.critical_points[:5]:
        print(f"  Point {cp.index} @ ({cp.x:.1f}, {cp.y:.1f}) "
              f"impact={cp.impact_score:.3f}")

    # Detailed API
    ra = ResilienceAnalyzer(points=[(100,200), (300,400), ...])
    result = ra.analyze(cascade_depth=3, suggest_redundancy=True)
    result.to_json("resilience.json")
    result.to_html("resilience.html")

CLI::

    python vormap_resilience.py points.txt
    python vormap_resilience.py points.txt --json resilience.json
    python vormap_resilience.py points.txt --html resilience.html
    python vormap_resilience.py points.txt --cascade-depth 5
    python vormap_resilience.py points.txt --redundancy --top 10
    python vormap_resilience.py points.txt --what-if 0,3,7
    python vormap_resilience.py --demo
"""

from __future__ import annotations

import argparse
import csv
import html as _html
import json
import math
import os
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from vormap_utils import (
    bounding_box as _bounding_box,
    compute_nn_distances,
    euclidean as _dist,
    load_points as _load_points,
)

# ---------------------------------------------------------------------------
# Geometry helpers (lightweight, no heavy deps)
# ---------------------------------------------------------------------------


# _cross, _convex_hull removed — dead code duplicating vormap_utils.
from vormap_utils import polygon_area as _polygon_area  # backward compat for tests


def _build_adjacency(points):
    """Build adjacency list via Delaunay-like nearest-neighbor heuristic.

    For each point, connects to the k nearest neighbors where k adapts
    to point count. This approximates Voronoi adjacency without requiring
    scipy.spatial.Delaunay.
    """
    n = len(points)
    if n <= 1:
        return {i: set() for i in range(n)}

    k = min(max(6, int(math.log2(n) * 2)), n - 1)
    adj = defaultdict(set)

    try:
        from scipy.spatial import Delaunay
        tri = Delaunay(points)
        for simplex in tri.simplices:
            for i in range(3):
                for j in range(i + 1, 3):
                    adj[simplex[i]].add(simplex[j])
                    adj[simplex[j]].add(simplex[i])
    except (ImportError, Exception):
        # Fallback: k-nearest neighbors
        for i in range(n):
            dists = []
            for j in range(n):
                if i != j:
                    d = _dist(points[i], points[j])
                    dists.append((d, j))
            dists.sort()
            for _, j in dists[:k]:
                adj[i].add(j)
                adj[j].add(i)

    # Ensure all indices present
    for i in range(n):
        if i not in adj:
            adj[i] = set()

    return adj


def _voronoi_cell_areas(points, bounds=None):
    """Approximate Voronoi cell areas via grid sampling."""
    n = len(points)
    if n == 0:
        return []
    if bounds is None:
        xmin, ymin, xmax, ymax = _bounding_box(points)
        pad = max(xmax - xmin, ymax - ymin) * 0.05
        xmin -= pad
        ymin -= pad
        xmax += pad
        ymax += pad
    else:
        xmin, ymin, xmax, ymax = bounds

    # Grid resolution adapts to point count
    res = min(200, max(50, int(math.sqrt(n) * 10)))
    dx = (xmax - xmin) / res
    dy = (ymax - ymin) / res
    cell_area = dx * dy
    counts = [0] * n

    for iy in range(res):
        py = ymin + (iy + 0.5) * dy
        for ix in range(res):
            px = xmin + (ix + 0.5) * dx
            best_d = float("inf")
            best_i = 0
            for i, (sx, sy) in enumerate(points):
                d = (px - sx) ** 2 + (py - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            counts[best_i] += 1

    return [c * cell_area for c in counts]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PointImpact:
    """Failure impact assessment for a single point."""
    index: int
    x: float
    y: float
    impact_score: float  # 0.0-1.0 composite
    area_before: float
    max_neighbor_area_increase: float
    avg_neighbor_area_increase: float
    neighbors_affected: int
    connectivity_loss: float  # fraction of edges lost
    coverage_gap: float  # area of the void left


@dataclass
class CascadeStep:
    """One step in a cascading failure simulation."""
    step: int
    removed_index: int
    removed_point: Tuple[float, float]
    resilience_after: float  # score after this removal
    area_imbalance: float  # Gini coefficient of remaining areas


@dataclass
class RedundancySuggestion:
    """Suggested backup point placement."""
    for_point_index: int
    suggested_x: float
    suggested_y: float
    impact_reduction: float  # how much the failure impact decreases


@dataclass
class ResilienceResult:
    """Complete resilience analysis result."""
    resilience_score: float  # 0-100
    point_count: int
    critical_points: List[PointImpact]
    cascade_analysis: List[CascadeStep]
    redundancy_suggestions: List[RedundancySuggestion]
    area_gini: float  # baseline area inequality
    mean_degree: float  # average connectivity
    vulnerability_summary: str
    recommendations: List[str]

    def to_dict(self) -> dict:
        return {
            "resilience_score": round(self.resilience_score, 2),
            "point_count": self.point_count,
            "area_gini": round(self.area_gini, 4),
            "mean_degree": round(self.mean_degree, 2),
            "vulnerability_summary": self.vulnerability_summary,
            "recommendations": self.recommendations,
            "critical_points": [
                {
                    "index": cp.index,
                    "x": round(cp.x, 4),
                    "y": round(cp.y, 4),
                    "impact_score": round(cp.impact_score, 4),
                    "area_before": round(cp.area_before, 4),
                    "max_neighbor_area_increase": round(cp.max_neighbor_area_increase, 4),
                    "avg_neighbor_area_increase": round(cp.avg_neighbor_area_increase, 4),
                    "neighbors_affected": cp.neighbors_affected,
                    "connectivity_loss": round(cp.connectivity_loss, 4),
                    "coverage_gap": round(cp.coverage_gap, 4),
                }
                for cp in self.critical_points
            ],
            "cascade_analysis": [
                {
                    "step": cs.step,
                    "removed_index": cs.removed_index,
                    "removed_point": [round(cs.removed_point[0], 4),
                                      round(cs.removed_point[1], 4)],
                    "resilience_after": round(cs.resilience_after, 2),
                    "area_imbalance": round(cs.area_imbalance, 4),
                }
                for cs in self.cascade_analysis
            ],
            "redundancy_suggestions": [
                {
                    "for_point_index": rs.for_point_index,
                    "suggested_x": round(rs.suggested_x, 4),
                    "suggested_y": round(rs.suggested_y, 4),
                    "impact_reduction": round(rs.impact_reduction, 4),
                }
                for rs in self.redundancy_suggestions
            ],
        }

    def to_json(self, filepath: str) -> str:
        with open(filepath, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        return filepath

    def to_html(self, filepath: str) -> str:
        d = self.to_dict()
        score = d["resilience_score"]
        score_color = (
            "#27ae60" if score >= 70 else "#f39c12" if score >= 40 else "#e74c3c"
        )
        score_label = (
            "Resilient" if score >= 70 else "Moderate" if score >= 40 else "Vulnerable"
        )

        crit_rows = ""
        for cp in d["critical_points"][:20]:
            bar_w = int(cp["impact_score"] * 200)
            crit_rows += (
                "<tr>"
                "<td>%d</td>"
                "<td>(%.1f, %.1f)</td>"
                "<td><div style='background:#e74c3c;width:%dpx;height:16px;"
                "border-radius:3px;display:inline-block'></div> %.3f</td>"
                "<td>%.1f</td>"
                "<td>%d</td>"
                "<td>%.3f</td>"
                "</tr>\n"
                % (
                    cp["index"],
                    cp["x"],
                    cp["y"],
                    bar_w,
                    cp["impact_score"],
                    cp["coverage_gap"],
                    cp["neighbors_affected"],
                    cp["connectivity_loss"],
                )
            )

        cascade_rows = ""
        for cs in d["cascade_analysis"]:
            cascade_rows += (
                "<tr><td>%d</td><td>%d</td><td>(%.1f, %.1f)</td>"
                "<td>%.1f</td><td>%.4f</td></tr>\n"
                % (
                    cs["step"],
                    cs["removed_index"],
                    cs["removed_point"][0],
                    cs["removed_point"][1],
                    cs["resilience_after"],
                    cs["area_imbalance"],
                )
            )

        redundancy_rows = ""
        for rs in d["redundancy_suggestions"]:
            redundancy_rows += (
                "<tr><td>%d</td><td>(%.1f, %.1f)</td><td>%.3f</td></tr>\n"
                % (rs["for_point_index"], rs["suggested_x"],
                   rs["suggested_y"], rs["impact_reduction"])
            )

        rec_list = "".join(
            "<li>%s</li>" % _html.escape(r) for r in d["recommendations"]
        )

        html_content = _REPORT_TEMPLATE % {
            "score": score,
            "score_color": score_color,
            "score_label": score_label,
            "point_count": d["point_count"],
            "area_gini": d["area_gini"],
            "mean_degree": d["mean_degree"],
            "vulnerability_summary": _html.escape(d["vulnerability_summary"]),
            "crit_rows": crit_rows,
            "cascade_rows": cascade_rows,
            "redundancy_rows": redundancy_rows,
            "rec_list": rec_list,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        return filepath


# ---------------------------------------------------------------------------
# HTML report template
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Spatial Resilience Report</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #c9d1d9; padding: 2rem; }
  h1 { color: #58a6ff; margin-bottom: 0.5rem; }
  h2 { color: #58a6ff; margin: 1.5rem 0 0.5rem; border-bottom: 1px solid #21262d;
       padding-bottom: 0.3rem; }
  .score-card { display: inline-block; background: #161b22; border-radius: 12px;
                padding: 1.5rem 2.5rem; margin: 1rem 0; text-align: center;
                border: 1px solid #30363d; }
  .score-num { font-size: 3rem; font-weight: bold; }
  .score-label { font-size: 1.2rem; margin-top: 0.3rem; }
  .stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin: 1rem 0; }
  .stat { background: #161b22; border-radius: 8px; padding: 1rem 1.5rem;
          border: 1px solid #30363d; min-width: 150px; }
  .stat-val { font-size: 1.5rem; font-weight: bold; color: #58a6ff; }
  .stat-label { font-size: 0.85rem; color: #8b949e; }
  table { width: 100%%; border-collapse: collapse; margin: 0.5rem 0; }
  th, td { padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #21262d; }
  th { color: #8b949e; font-weight: 600; font-size: 0.85rem; text-transform: uppercase; }
  tr:hover { background: #161b22; }
  .summary { background: #161b22; border-radius: 8px; padding: 1rem 1.5rem;
             border-left: 4px solid #f39c12; margin: 1rem 0; }
  ul { margin: 0.5rem 0 0.5rem 1.5rem; }
  li { margin: 0.3rem 0; }
  .section { margin-bottom: 2rem; }
</style>
</head>
<body>
<h1>&#128737;&#65039; Spatial Resilience Report</h1>

<div class="score-card">
  <div class="score-num" style="color: %(score_color)s">%(score).0f</div>
  <div class="score-label" style="color: %(score_color)s">%(score_label)s</div>
</div>

<div class="stats">
  <div class="stat"><div class="stat-val">%(point_count)d</div><div class="stat-label">Points</div></div>
  <div class="stat"><div class="stat-val">%(area_gini).3f</div><div class="stat-label">Area Gini</div></div>
  <div class="stat"><div class="stat-val">%(mean_degree).1f</div><div class="stat-label">Mean Degree</div></div>
</div>

<div class="summary">%(vulnerability_summary)s</div>

<div class="section">
<h2>&#127919; Critical Points (Single Points of Failure)</h2>
<table>
<tr><th>Index</th><th>Location</th><th>Impact Score</th><th>Coverage Gap</th><th>Neighbors</th><th>Connectivity Loss</th></tr>
%(crit_rows)s
</table>
</div>

<div class="section">
<h2>&#128279; Cascade Analysis</h2>
<table>
<tr><th>Step</th><th>Removed</th><th>Location</th><th>Resilience After</th><th>Area Imbalance</th></tr>
%(cascade_rows)s
</table>
</div>

<div class="section">
<h2>&#128295; Redundancy Suggestions</h2>
<table>
<tr><th>Backup For</th><th>Suggested Location</th><th>Impact Reduction</th></tr>
%(redundancy_rows)s
</table>
</div>

<div class="section">
<h2>&#128203; Recommendations</h2>
<ul>%(rec_list)s</ul>
</div>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def _gini_coefficient(values):
    """Compute Gini coefficient for a list of non-negative values."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    total = sum(sorted_vals)
    if total == 0:
        return 0.0
    cumulative = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(sorted_vals):
        cumulative += v
        weighted_sum += (2 * (i + 1) - n - 1) * v
    return weighted_sum / (n * total)


class ResilienceAnalyzer:
    """Analyze spatial resilience of a point pattern."""

    def __init__(self, points: List[Tuple[float, float]],
                 bounds: Optional[Tuple[float, float, float, float]] = None):
        if len(points) < 3:
            raise ValueError("Need at least 3 points for resilience analysis")
        self.points = list(points)
        self.n = len(self.points)
        self.bounds = bounds
        self._adj = None
        self._areas = None

    @property
    def adjacency(self):
        if self._adj is None:
            self._adj = _build_adjacency(self.points)
        return self._adj

    @property
    def areas(self):
        if self._areas is None:
            self._areas = _voronoi_cell_areas(self.points, self.bounds)
        return self._areas

    def _compute_impact(self, idx: int) -> PointImpact:
        """Compute failure impact of removing point at index idx."""
        remaining = [p for i, p in enumerate(self.points) if i != idx]
        if len(remaining) < 2:
            return PointImpact(
                index=idx, x=self.points[idx][0], y=self.points[idx][1],
                impact_score=1.0, area_before=self.areas[idx],
                max_neighbor_area_increase=0, avg_neighbor_area_increase=0,
                neighbors_affected=0, connectivity_loss=1.0,
                coverage_gap=self.areas[idx],
            )

        # Compute new areas
        new_areas = _voronoi_cell_areas(remaining, self.bounds)

        # Map old indices to new indices
        old_to_new = {}
        new_idx = 0
        for i in range(self.n):
            if i != idx:
                old_to_new[i] = new_idx
                new_idx += 1

        # Measure neighbor impact
        neighbors = self.adjacency.get(idx, set())
        area_increases = []
        for nb in neighbors:
            if nb in old_to_new:
                old_a = self.areas[nb]
                new_a = new_areas[old_to_new[nb]]
                area_increases.append(new_a - old_a)

        max_inc = max(area_increases) if area_increases else 0
        avg_inc = (sum(area_increases) / len(area_increases)) if area_increases else 0

        # Connectivity loss: fraction of total edges removed
        total_edges = sum(len(v) for v in self.adjacency.values()) / 2
        edges_lost = len(neighbors)
        conn_loss = edges_lost / total_edges if total_edges > 0 else 0

        # Coverage gap = area of removed point
        gap = self.areas[idx]

        # Composite impact score (0-1)
        # Weighted combination of normalized metrics
        total_area = sum(self.areas) if self.areas else 1
        gap_norm = gap / total_area if total_area > 0 else 0
        conn_norm = conn_loss
        neighbor_norm = len(neighbors) / max(self.n - 1, 1)
        inc_norm = (max_inc / total_area) if total_area > 0 else 0

        impact = (
            0.35 * gap_norm
            + 0.25 * conn_norm
            + 0.20 * inc_norm
            + 0.20 * neighbor_norm
        )
        impact = min(1.0, impact)

        return PointImpact(
            index=idx,
            x=self.points[idx][0],
            y=self.points[idx][1],
            impact_score=impact,
            area_before=self.areas[idx],
            max_neighbor_area_increase=max_inc,
            avg_neighbor_area_increase=avg_inc,
            neighbors_affected=len(neighbors),
            connectivity_loss=conn_loss,
            coverage_gap=gap,
        )

    def analyze(
        self,
        cascade_depth: int = 3,
        suggest_redundancy: bool = True,
        top_k: int = 10,
    ) -> ResilienceResult:
        """Run full resilience analysis."""

        # Phase 1: Individual impact scoring
        impacts = []
        for i in range(self.n):
            impacts.append(self._compute_impact(i))
        impacts.sort(key=lambda x: x.impact_score, reverse=True)

        # Phase 2: Cascade analysis
        cascade = self._cascade_analysis(impacts, cascade_depth)

        # Phase 3: Redundancy suggestions
        redundancy = []
        if suggest_redundancy:
            for cp in impacts[:min(top_k, len(impacts))]:
                suggestion = self._suggest_redundancy(cp)
                if suggestion:
                    redundancy.append(suggestion)

        # Phase 4: Compute overall resilience score
        area_gini = _gini_coefficient(self.areas)
        total_edges = sum(len(v) for v in self.adjacency.values()) / 2
        mean_deg = (2 * total_edges / self.n) if self.n > 0 else 0

        # Resilience score: 100 = perfectly resilient
        # Penalize high max impact, high Gini, low connectivity
        max_impact = impacts[0].impact_score if impacts else 0
        avg_impact = (
            sum(ip.impact_score for ip in impacts) / len(impacts)
            if impacts else 0
        )

        score = 100.0
        score -= max_impact * 30  # Single point of failure penalty
        score -= avg_impact * 20  # Overall fragility penalty
        score -= area_gini * 25  # Inequality penalty
        score += min(mean_deg / 6, 1.0) * 15  # Connectivity bonus
        score = max(0, min(100, score))

        # Generate summary and recommendations
        vuln_summary = self._vulnerability_summary(
            score, impacts[:5], area_gini, mean_deg
        )
        recommendations = self._generate_recommendations(
            score, impacts[:top_k], area_gini, mean_deg, cascade
        )

        return ResilienceResult(
            resilience_score=score,
            point_count=self.n,
            critical_points=impacts[:top_k],
            cascade_analysis=cascade,
            redundancy_suggestions=redundancy,
            area_gini=area_gini,
            mean_degree=mean_deg,
            vulnerability_summary=vuln_summary,
            recommendations=recommendations,
        )

    def _cascade_analysis(
        self, ranked_impacts: List[PointImpact], depth: int
    ) -> List[CascadeStep]:
        """Simulate sequential removal of highest-impact points."""
        steps = []
        remaining = list(self.points)
        remaining_indices = list(range(self.n))

        for step in range(min(depth, len(ranked_impacts))):
            # Find highest-impact point among remaining
            target_idx = ranked_impacts[step].index
            if target_idx not in remaining_indices:
                continue

            pos = remaining_indices.index(target_idx)
            removed_pt = remaining[pos]
            remaining.pop(pos)
            remaining_indices.pop(pos)

            if len(remaining) < 2:
                steps.append(CascadeStep(
                    step=step + 1,
                    removed_index=target_idx,
                    removed_point=removed_pt,
                    resilience_after=0.0,
                    area_imbalance=1.0,
                ))
                break

            # Measure post-removal state
            new_areas = _voronoi_cell_areas(remaining, self.bounds)
            gini = _gini_coefficient(new_areas)

            # Quick resilience estimate for remaining points
            nn_dists = compute_nn_distances(remaining)
            cv = 0.0
            if nn_dists:
                mean_nn = sum(nn_dists) / len(nn_dists)
                if mean_nn > 0:
                    std_nn = math.sqrt(
                        sum((d - mean_nn) ** 2 for d in nn_dists) / len(nn_dists)
                    )
                    cv = std_nn / mean_nn

            resilience_after = max(0, min(100, 100 - gini * 50 - cv * 30))

            steps.append(CascadeStep(
                step=step + 1,
                removed_index=target_idx,
                removed_point=removed_pt,
                resilience_after=resilience_after,
                area_imbalance=gini,
            ))

        return steps

    def _suggest_redundancy(self, cp: PointImpact) -> Optional[RedundancySuggestion]:
        """Suggest a backup point placement for a critical point."""
        neighbors = self.adjacency.get(cp.index, set())
        if not neighbors:
            return None

        # Place backup at the midpoint between critical point and its
        # most-affected neighbor direction, offset to fill the gap
        cx, cy = cp.x, cp.y
        # Average neighbor direction
        nx_sum, ny_sum = 0.0, 0.0
        for nb in neighbors:
            nx_sum += self.points[nb][0] - cx
            ny_sum += self.points[nb][1] - cy
        nav = len(neighbors)
        if nav > 0:
            nx_avg = nx_sum / nav
            ny_avg = ny_sum / nav
        else:
            return None

        # Place backup opposite to the average neighbor direction
        # (fills the gap that would be largest)
        dist_avg = math.sqrt(nx_avg ** 2 + ny_avg ** 2)
        if dist_avg < 1e-10:
            return None

        # Place at 0.5 * average-neighbor-distance in opposite direction
        scale = dist_avg * 0.5
        sx = cx - (nx_avg / dist_avg) * scale
        sy = cy - (ny_avg / dist_avg) * scale

        # Estimate impact reduction (heuristic: adding nearby backup
        # reduces impact roughly proportional to proximity)
        reduction = cp.impact_score * 0.4  # conservative estimate

        return RedundancySuggestion(
            for_point_index=cp.index,
            suggested_x=sx,
            suggested_y=sy,
            impact_reduction=reduction,
        )

    def _vulnerability_summary(
        self,
        score: float,
        top_impacts: List[PointImpact],
        gini: float,
        mean_deg: float,
    ) -> str:
        if score >= 70:
            tone = "well-distributed and resilient to individual failures"
        elif score >= 40:
            tone = "moderately resilient but has identifiable weak points"
        else:
            tone = "vulnerable -- several points are critical to spatial coverage"

        top_str = ""
        if top_impacts:
            top_str = (
                " The most critical point is #%d at (%.1f, %.1f) with an "
                "impact score of %.3f."
                % (
                    top_impacts[0].index,
                    top_impacts[0].x,
                    top_impacts[0].y,
                    top_impacts[0].impact_score,
                )
            )

        return (
            "This spatial layout with %d points is %s "
            "(resilience score: %.0f/100, area Gini: %.3f, "
            "mean connectivity: %.1f).%s"
            % (self.n, tone, score, gini, mean_deg, top_str)
        )

    def _generate_recommendations(
        self,
        score: float,
        top_impacts: List[PointImpact],
        gini: float,
        mean_deg: float,
        cascade: List[CascadeStep],
    ) -> List[str]:
        recs = []

        if score < 40:
            recs.append(
                "CRITICAL: Resilience score is below 40. Consider adding "
                "redundant points near the top critical locations."
            )

        if gini > 0.4:
            recs.append(
                "Area inequality is high (Gini=%.3f). Use Lloyd relaxation "
                "(--relax) to even out cell sizes." % gini
            )

        if mean_deg < 3.5:
            recs.append(
                "Average connectivity is low (%.1f). Adding points in "
                "sparse regions will improve network robustness." % mean_deg
            )

        if top_impacts and top_impacts[0].impact_score > 0.5:
            recs.append(
                "Point #%d is a critical single point of failure "
                "(impact=%.3f). Place a backup point nearby to mitigate."
                % (top_impacts[0].index, top_impacts[0].impact_score)
            )

        if cascade and len(cascade) >= 2:
            drop = cascade[0].resilience_after - cascade[-1].resilience_after
            if drop > 30:
                recs.append(
                    "Cascade analysis shows %.0f-point resilience drop "
                    "over %d sequential failures. The layout is susceptible "
                    "to chain reactions." % (drop, len(cascade))
                )

        high_impact_count = sum(
            1 for ip in top_impacts if ip.impact_score > 0.3
        )
        if high_impact_count > 3:
            recs.append(
                "%d points have impact scores above 0.3. Consider "
                "using vormap_siting to find optimal backup locations."
                % high_impact_count
            )

        if not recs:
            recs.append(
                "Layout is well-balanced. No critical vulnerabilities "
                "detected. Continue monitoring with vormap_sentinel."
            )

        return recs

    def what_if(self, indices: List[int]) -> Dict[str, Any]:
        """Simulate simultaneous removal of specific points."""
        valid = [i for i in indices if 0 <= i < self.n]
        if not valid:
            return {"error": "No valid indices provided"}

        remaining = [
            p for i, p in enumerate(self.points) if i not in set(valid)
        ]
        if len(remaining) < 2:
            return {
                "removed": valid,
                "remaining_count": len(remaining),
                "resilience_after": 0.0,
                "area_imbalance": 1.0,
                "total_coverage_lost": sum(
                    self.areas[i] for i in valid if i < len(self.areas)
                ),
            }

        new_areas = _voronoi_cell_areas(remaining, self.bounds)
        gini = _gini_coefficient(new_areas)
        nn_dists = compute_nn_distances(remaining)
        cv = 0.0
        if nn_dists:
            mean_nn = sum(nn_dists) / len(nn_dists)
            if mean_nn > 0:
                std_nn = math.sqrt(
                    sum((d - mean_nn) ** 2 for d in nn_dists) / len(nn_dists)
                )
                cv = std_nn / mean_nn

        return {
            "removed": valid,
            "remaining_count": len(remaining),
            "resilience_after": max(0, min(100, 100 - gini * 50 - cv * 30)),
            "area_imbalance": round(gini, 4),
            "total_coverage_lost": round(
                sum(self.areas[i] for i in valid if i < len(self.areas)), 4
            ),
        }


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def analyze_resilience(
    filepath: str,
    cascade_depth: int = 3,
    suggest_redundancy: bool = True,
    top_k: int = 10,
) -> ResilienceResult:
    """One-call resilience analysis from a file path."""
    points = _load_points(filepath)
    ra = ResilienceAnalyzer(points)
    return ra.analyze(
        cascade_depth=cascade_depth,
        suggest_redundancy=suggest_redundancy,
        top_k=top_k,
    )


def _demo():
    """Run a demo with synthetic data."""
    random.seed(42)
    # Create a pattern with obvious vulnerability: one central hub
    points = [(500.0, 500.0)]  # central hub
    for angle_deg in range(0, 360, 45):
        r = 200
        x = 500 + r * math.cos(math.radians(angle_deg))
        y = 500 + r * math.sin(math.radians(angle_deg))
        points.append((x, y))
    # Add some random peripheral points
    for _ in range(12):
        points.append((random.uniform(100, 900), random.uniform(100, 900)))

    ra = ResilienceAnalyzer(points)
    result = ra.analyze(cascade_depth=3, suggest_redundancy=True, top_k=5)

    print("\n" + "=" * 60)
    print("SPATIAL RESILIENCE ANALYSIS (Demo)")
    print("=" * 60)
    print("Resilience Score: %.0f / 100" % result.resilience_score)
    print("Points: %d | Area Gini: %.3f | Mean Degree: %.1f"
          % (result.point_count, result.area_gini, result.mean_degree))
    print()
    print("Vulnerability: %s" % result.vulnerability_summary)
    print()
    print("Critical Points:")
    for cp in result.critical_points:
        print("  #%d @ (%.1f, %.1f) -- impact=%.3f, gap=%.1f, "
              "neighbors=%d, conn_loss=%.3f"
              % (cp.index, cp.x, cp.y, cp.impact_score,
                 cp.coverage_gap, cp.neighbors_affected,
                 cp.connectivity_loss))
    print()
    print("Cascade Analysis:")
    for cs in result.cascade_analysis:
        print("  Step %d: remove #%d -> resilience=%.1f, imbalance=%.4f"
              % (cs.step, cs.removed_index, cs.resilience_after,
                 cs.area_imbalance))
    print()
    print("Redundancy Suggestions:")
    for rs in result.redundancy_suggestions:
        print("  Backup for #%d -> (%.1f, %.1f), reduction=%.3f"
              % (rs.for_point_index, rs.suggested_x, rs.suggested_y,
                 rs.impact_reduction))
    print()
    print("Recommendations:")
    for r in result.recommendations:
        print("  * %s" % r)
    print("=" * 60)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_resilience_args(parser):
    """Add resilience-related arguments to an argparse parser."""
    grp = parser.add_argument_group(
        "Spatial Resilience",
        "Simulate failures and identify critical infrastructure",
    )
    grp.add_argument(
        "--resilience", action="store_true",
        help="Run spatial resilience analysis and print summary.",
    )
    grp.add_argument(
        "--resilience-json", metavar="PATH",
        help="Export resilience report as JSON.",
    )
    grp.add_argument(
        "--resilience-html", metavar="PATH",
        help="Export resilience report as interactive HTML.",
    )
    grp.add_argument(
        "--resilience-cascade", type=int, default=3, metavar="N",
        help="Cascade analysis depth -- number of sequential failures "
             "to simulate (default: 3).",
    )
    grp.add_argument(
        "--resilience-top", type=int, default=10, metavar="K",
        help="Number of top critical points to report (default: 10).",
    )
    grp.add_argument(
        "--resilience-what-if", metavar="INDICES",
        help="Comma-separated point indices to simulate removing "
             "(e.g. --resilience-what-if 0,3,7).",
    )
    grp.add_argument(
        "--resilience-no-redundancy", action="store_true",
        help="Skip redundancy placement suggestions.",
    )


def run_resilience_cli(args, input_file):
    """Execute resilience analysis from parsed CLI args."""
    has_resilience = any([
        getattr(args, "resilience", False),
        getattr(args, "resilience_json", None),
        getattr(args, "resilience_html", None),
        getattr(args, "resilience_what_if", None),
    ])
    if not has_resilience:
        return None

    points = _load_points(input_file)
    ra = ResilienceAnalyzer(points)

    cascade_depth = getattr(args, "resilience_cascade", 3)
    top_k = getattr(args, "resilience_top", 10)
    no_redundancy = getattr(args, "resilience_no_redundancy", False)

    result = ra.analyze(
        cascade_depth=cascade_depth,
        suggest_redundancy=not no_redundancy,
        top_k=top_k,
    )

    # What-if scenario
    what_if_result = None
    if getattr(args, "resilience_what_if", None):
        indices = [
            int(x.strip())
            for x in args.resilience_what_if.split(",")
            if x.strip().isdigit()
        ]
        what_if_result = ra.what_if(indices)

    # Export
    if getattr(args, "resilience_json", None):
        result.to_json(args.resilience_json)
        print("Resilience report written to %s" % args.resilience_json)
    if getattr(args, "resilience_html", None):
        result.to_html(args.resilience_html)
        print("Resilience HTML report written to %s" % args.resilience_html)

    # Print summary
    print("\n" + "=" * 60)
    print("SPATIAL RESILIENCE REPORT")
    print("=" * 60)
    print("Resilience Score: %.0f / 100" % result.resilience_score)
    print("Points: %d | Area Gini: %.3f | Mean Degree: %.1f"
          % (result.point_count, result.area_gini, result.mean_degree))
    print()
    print(result.vulnerability_summary)
    print()
    print("Top Critical Points:")
    for cp in result.critical_points[:5]:
        print("  #%d @ (%.1f, %.1f) -- impact=%.3f"
              % (cp.index, cp.x, cp.y, cp.impact_score))
    print()
    print("Recommendations:")
    for r in result.recommendations:
        print("  * %s" % r)

    if what_if_result:
        print()
        print("What-If Scenario (remove %s):" % what_if_result.get("removed", []))
        print("  Remaining: %d" % what_if_result.get("remaining_count", 0))
        print("  Resilience after: %.1f" % what_if_result.get("resilience_after", 0))
        print("  Coverage lost: %.1f" % what_if_result.get("total_coverage_lost", 0))

    print("=" * 60)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Spatial Resilience Analyzer -- simulate failures and "
                    "identify critical infrastructure in point patterns.",
    )
    parser.add_argument("datafile", nargs="?", help="Point data file")
    parser.add_argument("--json", metavar="PATH", help="Export as JSON")
    parser.add_argument("--html", metavar="PATH", help="Export as HTML")
    parser.add_argument(
        "--cascade-depth", type=int, default=3,
        help="Cascade depth (default: 3)",
    )
    parser.add_argument(
        "--top", type=int, default=10,
        help="Top K critical points (default: 10)",
    )
    parser.add_argument(
        "--what-if", metavar="INDICES",
        help="Comma-separated indices to remove",
    )
    parser.add_argument(
        "--redundancy", action="store_true", default=True,
        help="Include redundancy suggestions (default: yes)",
    )
    parser.add_argument(
        "--no-redundancy", action="store_true",
        help="Skip redundancy suggestions",
    )
    parser.add_argument("--demo", action="store_true", help="Run demo")
    args = parser.parse_args()

    if args.demo:
        result = _demo()
        if args.json:
            result.to_json(args.json)
            print("JSON written to %s" % args.json)
        if args.html:
            result.to_html(args.html)
            print("HTML written to %s" % args.html)
        return

    if not args.datafile:
        parser.error("datafile is required (or use --demo)")

    points = _load_points(args.datafile)
    ra = ResilienceAnalyzer(points)
    result = ra.analyze(
        cascade_depth=args.cascade_depth,
        suggest_redundancy=not args.no_redundancy,
        top_k=args.top,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("SPATIAL RESILIENCE ANALYSIS")
    print("=" * 60)
    print("Resilience Score: %.0f / 100" % result.resilience_score)
    print("Points: %d | Area Gini: %.3f | Mean Degree: %.1f"
          % (result.point_count, result.area_gini, result.mean_degree))
    print()
    print(result.vulnerability_summary)
    print()
    print("Critical Points:")
    for cp in result.critical_points:
        print("  #%d @ (%.1f, %.1f) -- impact=%.3f, gap=%.1f, "
              "neighbors=%d" % (cp.index, cp.x, cp.y, cp.impact_score,
                                cp.coverage_gap, cp.neighbors_affected))
    print()
    print("Cascade Analysis:")
    for cs in result.cascade_analysis:
        print("  Step %d: remove #%d -> resilience=%.1f"
              % (cs.step, cs.removed_index, cs.resilience_after))
    print()
    if result.redundancy_suggestions:
        print("Redundancy Suggestions:")
        for rs in result.redundancy_suggestions:
            print("  Backup for #%d -> (%.1f, %.1f)"
                  % (rs.for_point_index, rs.suggested_x, rs.suggested_y))
        print()
    print("Recommendations:")
    for r in result.recommendations:
        print("  * %s" % r)
    print("=" * 60)

    # What-if
    if args.what_if:
        indices = [int(x.strip()) for x in args.what_if.split(",")
                   if x.strip().isdigit()]
        wi = ra.what_if(indices)
        print()
        print("What-If: remove %s" % wi.get("removed", []))
        print("  Remaining: %d | Resilience: %.1f | Lost: %.1f"
              % (wi["remaining_count"], wi["resilience_after"],
                 wi["total_coverage_lost"]))

    # Export
    if args.json:
        result.to_json(args.json)
        print("JSON written to %s" % args.json)
    if args.html:
        result.to_html(args.html)
        print("HTML written to %s" % args.html)


if __name__ == "__main__":
    main()
