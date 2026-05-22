#!/usr/bin/env python3
"""Spatial Anomaly Forensics Engine — autonomous multi-phase investigation.

When given a point dataset the forensics engine autonomously investigates
spatial anomalies through a six-phase pipeline.  It classifies anomaly
root causes, traces evidence chains, and produces a forensic report with
confidence-scored verdicts.

This is an *agentic* capability — the engine acts like a spatial data
forensic investigator, autonomously gathering evidence, classifying
anomalies, constructing causal chains, and delivering a verdict.

Six investigation phases:

- **Scene Survey** — baseline statistics (count, bounds, NN, density grid).
- **Anomaly Detection** — density hotspots/voids, spacing outliers, tight
  clusters, boundary accumulation.
- **Evidence Collection** — density ratios, NN deviations, spatial
  autocorrelation, geometric regularity tests.
- **Root Cause Classification** — 7 cause types (data corruption,
  systematic drift, equipment artifact, boundary effect, intentional
  injection, sampling bias, natural clustering) with confidence scores.
- **Causal Chain Construction** — link related anomalies into explanatory
  chains (e.g. drift → boundary accumulation → void).
- **Verdict Generation** — integrity score 0-100, risk level, primary
  diagnosis, remediation recommendations.

Usage (Python API)::

    from vormap_forensics import ForensicsEngine, investigate

    # Quick one-liner
    verdict = investigate("points.txt")
    print(f"Integrity: {verdict.integrity_score}/100  Risk: {verdict.risk_level}")
    for a in verdict.anomalies:
        print(f"  [{a.severity}] {a.anomaly_type}: {a.root_cause} "
              f"(confidence {a.cause_confidence:.0%})")

    # Detailed API
    engine = ForensicsEngine(points=[(0,0),(5,5),(10,10)])
    verdict = engine.investigate()
    engine.to_json("forensics.json")
    engine.to_html("forensics.html")

CLI::

    python vormap_forensics.py points.txt
    python vormap_forensics.py points.txt --json report.json
    python vormap_forensics.py points.txt --html report.html
    python vormap_forensics.py points.txt --grid-resolution 30
    python vormap_forensics.py --demo
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime
from html import escape as _esc
from typing import Dict, List, Optional, Sequence, Tuple

from vormap_utils import (
    bounding_box as _bounding_box,
    euclidean as _dist,
    load_points as _load_points,
    compute_nn_distances as _compute_nn,
)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    """A single piece of forensic evidence."""

    type: str  # e.g. "density_ratio", "nn_deviation", "grid_alignment"
    value: float
    description: str
    weight: float  # 0-1, how probative


@dataclass
class Anomaly:
    """A detected spatial anomaly."""

    anomaly_id: int
    anomaly_type: str  # density / spacing / cluster / boundary
    severity: str  # info / warning / critical
    affected_indices: List[int] = field(default_factory=list)
    center: Tuple[float, float] = (0.0, 0.0)
    radius: float = 0.0
    evidence: List[Evidence] = field(default_factory=list)
    root_cause: str = "unknown"
    cause_confidence: float = 0.0


@dataclass
class CausalChain:
    """A chain linking related anomalies with causal explanation."""

    chain_id: int
    anomaly_ids: List[int] = field(default_factory=list)
    mechanism: str = ""
    confidence: float = 0.0


@dataclass
class ForensicVerdict:
    """Final output of the forensic investigation."""

    integrity_score: float = 100.0  # 0-100
    risk_level: str = "low"  # low / medium / high / critical
    primary_diagnosis: str = "clean"
    anomaly_count: int = 0
    anomalies: List[Anomaly] = field(default_factory=list)
    causal_chains: List[CausalChain] = field(default_factory=list)
    evidence_summary: str = ""
    remediation: List[str] = field(default_factory=list)
    phase_reports: Dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _median(vals):
    """Median of a list of numbers."""
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _mean(vals):
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _stdev(vals):
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _severity_for(score: float) -> str:
    """Map a 0-1 severity score to a label."""
    if score >= 0.7:
        return "critical"
    if score >= 0.4:
        return "warning"
    return "info"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ForensicsEngine:
    """Autonomous spatial anomaly forensics investigator."""

    def __init__(
        self,
        points: Sequence[Tuple[float, float]],
        grid_resolution: int = 20,
    ):
        self._points: List[Tuple[float, float]] = list(points)
        self._grid_res = max(2, grid_resolution)
        self._anomalies: List[Anomaly] = []
        self._chains: List[CausalChain] = []
        self._phase_reports: Dict[str, str] = {}
        self._verdict: Optional[ForensicVerdict] = None

        # Computed in scene survey
        self._bbox: Optional[Tuple[float, float, float, float]] = None
        self._nn_dists: List[float] = []
        self._nn_mean = 0.0
        self._nn_median = 0.0
        self._nn_std = 0.0
        self._density_grid: List[List[int]] = []
        self._cell_w = 0.0
        self._cell_h = 0.0
        self._global_density_mean = 0.0
        self._global_density_std = 0.0
        self._next_anomaly_id = 1

    # ---- Phase 1: Scene Survey ----

    def _scene_survey(self):
        pts = self._points
        n = len(pts)
        if n < 2:
            self._phase_reports["scene_survey"] = (
                f"Dataset has {n} point(s) — too few for meaningful analysis."
            )
            return

        self._bbox = _bounding_box(pts)
        xmin, ymin, xmax, ymax = self._bbox
        w = max(xmax - xmin, 1e-9)
        h = max(ymax - ymin, 1e-9)

        # NN distances
        self._nn_dists = _compute_nn(pts)
        self._nn_mean = _mean(self._nn_dists)
        self._nn_median = _median(self._nn_dists)
        self._nn_std = _stdev(self._nn_dists)

        # Density grid
        gr = self._grid_res
        self._cell_w = w / gr
        self._cell_h = h / gr
        grid = [[0] * gr for _ in range(gr)]
        for px, py in pts:
            ci = min(int((px - xmin) / self._cell_w), gr - 1)
            ri = min(int((py - ymin) / self._cell_h), gr - 1)
            grid[ri][ci] += 1
        self._density_grid = grid

        flat = [grid[r][c] for r in range(gr) for c in range(gr)]
        self._global_density_mean = _mean(flat)
        self._global_density_std = _stdev(flat)

        self._phase_reports["scene_survey"] = (
            f"Points: {n}, Bounds: ({xmin:.1f},{ymin:.1f})–({xmax:.1f},{ymax:.1f}), "
            f"NN mean={self._nn_mean:.2f} median={self._nn_median:.2f} "
            f"std={self._nn_std:.2f}, Grid {gr}×{gr}"
        )

    # ---- Phase 2: Anomaly Detection ----

    def _detect_anomalies(self):
        if len(self._points) < 3:
            self._phase_reports["anomaly_detection"] = "Too few points to detect anomalies."
            return

        self._detect_density_anomalies()
        self._detect_spacing_anomalies()
        self._detect_cluster_anomalies()
        self._detect_boundary_anomalies()

        self._phase_reports["anomaly_detection"] = (
            f"Detected {len(self._anomalies)} anomalies "
            f"(density/spacing/cluster/boundary)."
        )

    def _new_anomaly_id(self) -> int:
        aid = self._next_anomaly_id
        self._next_anomaly_id += 1
        return aid

    def _detect_density_anomalies(self):
        """Hotspots and voids in the density grid."""
        gr = self._grid_res
        dm, ds = self._global_density_mean, self._global_density_std
        if ds < 1e-9:
            return
        xmin, ymin = self._bbox[0], self._bbox[1]
        threshold = 3.0

        for r in range(gr):
            for c in range(gr):
                count = self._density_grid[r][c]
                z = (count - dm) / ds
                if z > threshold:
                    cx = xmin + (c + 0.5) * self._cell_w
                    cy = ymin + (r + 0.5) * self._cell_h
                    sev_score = min(1.0, (z - threshold) / 5.0 + 0.4)
                    # Find affected point indices
                    affected = self._points_in_cell(r, c)
                    self._anomalies.append(Anomaly(
                        anomaly_id=self._new_anomaly_id(),
                        anomaly_type="density",
                        severity=_severity_for(sev_score),
                        affected_indices=affected,
                        center=(cx, cy),
                        radius=max(self._cell_w, self._cell_h) * 0.5,
                        evidence=[Evidence(
                            type="density_ratio",
                            value=count / max(dm, 1e-9),
                            description=f"Cell ({r},{c}) density {count} is {z:.1f}σ above mean {dm:.1f}",
                            weight=min(1.0, z / 6.0),
                        )],
                    ))
                # Void detection: empty cell with populated neighbors
                if count == 0:
                    neighbors_pop = 0
                    total_neighbors = 0
                    for dr in (-1, 0, 1):
                        for dc in (-1, 0, 1):
                            if dr == 0 and dc == 0:
                                continue
                            nr, nc = r + dr, c + dc
                            if 0 <= nr < gr and 0 <= nc < gr:
                                total_neighbors += 1
                                if self._density_grid[nr][nc] > 0:
                                    neighbors_pop += 1
                    if total_neighbors > 0 and neighbors_pop / total_neighbors >= 0.75:
                        cx = xmin + (c + 0.5) * self._cell_w
                        cy = ymin + (r + 0.5) * self._cell_h
                        self._anomalies.append(Anomaly(
                            anomaly_id=self._new_anomaly_id(),
                            anomaly_type="density",
                            severity="warning",
                            affected_indices=[],
                            center=(cx, cy),
                            radius=max(self._cell_w, self._cell_h) * 0.5,
                            evidence=[Evidence(
                                type="void_surrounded",
                                value=neighbors_pop / total_neighbors,
                                description=f"Empty cell ({r},{c}) surrounded by {neighbors_pop}/{total_neighbors} populated neighbors",
                                weight=0.6,
                            )],
                        ))

    def _points_in_cell(self, row: int, col: int) -> List[int]:
        """Return indices of points falling in grid cell (row, col)."""
        xmin, ymin = self._bbox[0], self._bbox[1]
        gr = self._grid_res
        indices = []
        for i, (px, py) in enumerate(self._points):
            ci = min(int((px - xmin) / self._cell_w), gr - 1)
            ri = min(int((py - ymin) / self._cell_h), gr - 1)
            if ci == col and ri == row:
                indices.append(i)
        return indices

    def _detect_spacing_anomalies(self):
        """Points with extreme NN distances."""
        if self._nn_median < 1e-12:
            return
        low_thresh = 0.1 * self._nn_median
        high_thresh = 5.0 * self._nn_median
        for i, d in enumerate(self._nn_dists):
            if d < low_thresh:
                sev = "warning" if d > 0.01 * self._nn_median else "critical"
                self._anomalies.append(Anomaly(
                    anomaly_id=self._new_anomaly_id(),
                    anomaly_type="spacing",
                    severity=sev,
                    affected_indices=[i],
                    center=self._points[i],
                    radius=d,
                    evidence=[Evidence(
                        type="nn_deviation",
                        value=d / self._nn_median,
                        description=f"Point {i} NN distance {d:.4f} is {d/self._nn_median:.2%} of median",
                        weight=0.8,
                    )],
                ))
            elif d > high_thresh:
                self._anomalies.append(Anomaly(
                    anomaly_id=self._new_anomaly_id(),
                    anomaly_type="spacing",
                    severity="warning",
                    affected_indices=[i],
                    center=self._points[i],
                    radius=d,
                    evidence=[Evidence(
                        type="nn_deviation",
                        value=d / self._nn_median,
                        description=f"Point {i} NN distance {d:.4f} is {d/self._nn_median:.1f}× median",
                        weight=0.6,
                    )],
                ))

    def _detect_cluster_anomalies(self):
        """Simple density-based clustering to detect tight anomalous clusters."""
        pts = self._points
        n = len(pts)
        if n < 5:
            return
        # Use epsilon = 0.3 × median NN distance
        eps = 0.3 * self._nn_median
        if eps < 1e-12:
            return
        min_pts = max(3, n // 50)

        visited = [False] * n
        clusters: List[List[int]] = []

        for i in range(n):
            if visited[i]:
                continue
            neighbors = self._range_query(i, eps)
            if len(neighbors) < min_pts:
                continue
            cluster = []
            queue = list(neighbors)
            visited[i] = True
            cluster.append(i)
            while queue:
                j = queue.pop()
                if visited[j]:
                    continue
                visited[j] = True
                cluster.append(j)
                j_neighbors = self._range_query(j, eps)
                if len(j_neighbors) >= min_pts:
                    queue.extend(j_neighbors)
            if len(cluster) >= min_pts:
                clusters.append(cluster)

        # Filter clusters that are anomalously tight
        for cl in clusters:
            cl_pts = [pts[i] for i in cl]
            cl_nn = []
            for idx in cl:
                best = float("inf")
                px, py = pts[idx]
                for jdx in cl:
                    if jdx == idx:
                        continue
                    d = _dist(pts[idx], pts[jdx])
                    if d < best:
                        best = d
                cl_nn.append(best)
            avg_nn = _mean(cl_nn)
            if avg_nn < 0.25 * self._nn_median:
                cx = _mean([p[0] for p in cl_pts])
                cy = _mean([p[1] for p in cl_pts])
                rad = max(_dist((cx, cy), p) for p in cl_pts)
                self._anomalies.append(Anomaly(
                    anomaly_id=self._new_anomaly_id(),
                    anomaly_type="cluster",
                    severity="warning" if len(cl) < 10 else "critical",
                    affected_indices=cl,
                    center=(cx, cy),
                    radius=rad,
                    evidence=[Evidence(
                        type="cluster_tightness",
                        value=avg_nn / max(self._nn_median, 1e-9),
                        description=f"Cluster of {len(cl)} points, avg NN {avg_nn:.4f} vs global median {self._nn_median:.4f}",
                        weight=0.9,
                    )],
                ))

    def _range_query(self, idx: int, eps: float) -> List[int]:
        """Return indices of points within eps of points[idx]."""
        px, py = self._points[idx]
        result = []
        for j, (qx, qy) in enumerate(self._points):
            if j == idx:
                continue
            if abs(qx - px) <= eps and abs(qy - py) <= eps:
                if math.hypot(qx - px, qy - py) <= eps:
                    result.append(j)
        return result

    def _detect_boundary_anomalies(self):
        """Disproportionate point accumulation near bounding box edges."""
        xmin, ymin, xmax, ymax = self._bbox
        w = xmax - xmin
        h = ymax - ymin
        if w < 1e-9 or h < 1e-9:
            return
        margin = 0.05  # 5% edge band
        edge_w = w * margin
        edge_h = h * margin
        n = len(self._points)
        edge_indices = []
        for i, (px, py) in enumerate(self._points):
            if (px - xmin < edge_w or xmax - px < edge_w or
                    py - ymin < edge_h or ymax - py < edge_h):
                edge_indices.append(i)

        expected_frac = 1.0 - (1.0 - 2 * margin) ** 2  # area fraction of border
        actual_frac = len(edge_indices) / max(n, 1)
        if actual_frac > expected_frac * 2.5 and len(edge_indices) >= 5:
            cx = _mean([self._points[i][0] for i in edge_indices])
            cy = _mean([self._points[i][1] for i in edge_indices])
            self._anomalies.append(Anomaly(
                anomaly_id=self._new_anomaly_id(),
                anomaly_type="boundary",
                severity="warning" if actual_frac < 0.5 else "critical",
                affected_indices=edge_indices,
                center=(cx, cy),
                radius=max(w, h) * 0.5,
                evidence=[Evidence(
                    type="boundary_accumulation",
                    value=actual_frac / max(expected_frac, 1e-9),
                    description=f"{len(edge_indices)}/{n} points ({actual_frac:.1%}) in 5% edge band (expected ~{expected_frac:.1%})",
                    weight=0.7,
                )],
            ))

    # ---- Phase 3: Evidence Collection ----

    def _collect_evidence(self):
        """Enrich each anomaly with additional forensic evidence."""
        pts = self._points
        for anom in self._anomalies:
            if not anom.affected_indices:
                continue

            # Local density ratio
            local_pts = [pts[i] for i in anom.affected_indices]
            if len(local_pts) >= 2:
                local_nn = []
                for i, p in enumerate(local_pts):
                    best = float("inf")
                    for j, q in enumerate(local_pts):
                        if i == j:
                            continue
                        d = _dist(p, q)
                        if d < best:
                            best = d
                    local_nn.append(best)
                local_nn_mean = _mean(local_nn)
                ratio = local_nn_mean / max(self._nn_mean, 1e-9)
                anom.evidence.append(Evidence(
                    type="local_nn_ratio",
                    value=ratio,
                    description=f"Local NN mean {local_nn_mean:.4f} vs global {self._nn_mean:.4f} (ratio {ratio:.2f})",
                    weight=0.6,
                ))

            # Grid alignment check
            if len(local_pts) >= 4:
                alignment = self._check_grid_alignment(local_pts)
                if alignment > 0.5:
                    anom.evidence.append(Evidence(
                        type="grid_alignment",
                        value=alignment,
                        description=f"Grid alignment score {alignment:.2f} — points may be on a regular grid",
                        weight=0.8,
                    ))

            # Spatial autocorrelation (simplified Moran's I indicator)
            if len(anom.affected_indices) >= 3 and self._nn_mean > 1e-9:
                moran = self._local_moran(anom.affected_indices)
                anom.evidence.append(Evidence(
                    type="spatial_autocorrelation",
                    value=moran,
                    description=f"Local Moran's I = {moran:.3f} for anomaly region",
                    weight=0.5,
                ))

        self._phase_reports["evidence_collection"] = (
            f"Collected additional evidence for {len(self._anomalies)} anomalies."
        )

    def _check_grid_alignment(self, pts: List[Tuple[float, float]]) -> float:
        """Score 0-1 how grid-aligned a set of points is."""
        if len(pts) < 4:
            return 0.0
        xs = sorted(set(round(p[0], 2) for p in pts))
        ys = sorted(set(round(p[1], 2) for p in pts))
        if len(xs) < 2 or len(ys) < 2:
            return 0.0

        # Check if x-spacings and y-spacings are regular
        x_gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        y_gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]

        def regularity(gaps):
            if not gaps:
                return 0.0
            m = _mean(gaps)
            if m < 1e-9:
                return 0.0
            cv = _stdev(gaps) / m if len(gaps) >= 2 else 0.0
            return max(0.0, 1.0 - cv)

        return (regularity(x_gaps) + regularity(y_gaps)) / 2.0

    def _local_moran(self, indices: List[int]) -> float:
        """Simplified local Moran's I for NN distances in the index set."""
        if len(indices) < 3:
            return 0.0
        vals = [self._nn_dists[i] for i in indices if i < len(self._nn_dists)]
        if len(vals) < 3:
            return 0.0
        m = _mean(vals)
        var = _mean([(v - m) ** 2 for v in vals])
        if var < 1e-12:
            return 0.0
        # Compute using neighbor pairs
        total = 0.0
        count = 0
        for i_idx in range(len(indices)):
            for j_idx in range(i_idx + 1, len(indices)):
                pi = self._points[indices[i_idx]]
                pj = self._points[indices[j_idx]]
                d = _dist(pi, pj)
                if d < self._nn_mean * 3:
                    zi = vals[i_idx] - m
                    zj = vals[j_idx] - m
                    total += zi * zj / var
                    count += 1
        if count == 0:
            return 0.0
        return total / count

    # ---- Phase 4: Root Cause Classification ----

    _CAUSE_TYPES = [
        "data_corruption",
        "systematic_drift",
        "equipment_artifact",
        "boundary_effect",
        "intentional_injection",
        "sampling_bias",
        "natural_clustering",
    ]

    def _classify_root_causes(self):
        """Classify each anomaly's most likely root cause."""
        for anom in self._anomalies:
            scores = {c: 0.0 for c in self._CAUSE_TYPES}

            for ev in anom.evidence:
                if ev.type == "grid_alignment" and ev.value > 0.5:
                    scores["equipment_artifact"] += ev.weight * ev.value
                if ev.type == "boundary_accumulation":
                    scores["boundary_effect"] += ev.weight * 0.9
                if ev.type == "cluster_tightness":
                    if ev.value < 0.1:
                        scores["intentional_injection"] += ev.weight * 0.8
                    else:
                        scores["natural_clustering"] += ev.weight * 0.5
                if ev.type == "nn_deviation":
                    if ev.value < 0.05:
                        scores["data_corruption"] += ev.weight * 0.7
                    elif ev.value > 10:
                        scores["sampling_bias"] += ev.weight * 0.5
                if ev.type == "density_ratio":
                    if ev.value > 5:
                        scores["sampling_bias"] += ev.weight * 0.4
                if ev.type == "void_surrounded":
                    scores["sampling_bias"] += ev.weight * 0.4
                    scores["data_corruption"] += ev.weight * 0.3
                if ev.type == "local_nn_ratio" and ev.value < 0.2:
                    scores["intentional_injection"] += ev.weight * 0.6
                if ev.type == "spatial_autocorrelation":
                    if ev.value > 0.5:
                        scores["natural_clustering"] += ev.weight * 0.4

            # Check for systematic drift by looking at point order
            if anom.anomaly_type == "boundary":
                scores["boundary_effect"] += 0.3

            if anom.anomaly_type == "spacing" and len(anom.affected_indices) == 1:
                idx = anom.affected_indices[0]
                n = len(self._points)
                if idx > n * 0.8:
                    scores["systematic_drift"] += 0.3

            # Pick the highest scoring cause
            best_cause = max(scores, key=scores.get)
            best_score = scores[best_cause]

            # If all scores are zero, default to natural_clustering
            if best_score < 1e-9:
                best_cause = "natural_clustering"
                best_score = 0.3

            # Normalize confidence
            total = sum(scores.values())
            confidence = best_score / total if total > 0 else 0.3

            anom.root_cause = best_cause
            anom.cause_confidence = min(1.0, confidence)

        self._phase_reports["root_cause_classification"] = (
            f"Classified {len(self._anomalies)} anomalies into root cause categories."
        )

    # ---- Phase 5: Causal Chain Construction ----

    def _build_causal_chains(self):
        """Link related anomalies into explanatory causal chains."""
        if len(self._anomalies) < 2:
            self._phase_reports["causal_chains"] = "Fewer than 2 anomalies — no causal chains."
            return

        # Group nearby anomalies by spatial proximity
        used = set()
        chain_id = 1

        for i, a1 in enumerate(self._anomalies):
            if i in used:
                continue
            chain_members = [i]
            for j, a2 in enumerate(self._anomalies):
                if j <= i or j in used:
                    continue
                d = _dist(a1.center, a2.center)
                # Link if overlapping or within 2× radius
                link_dist = (a1.radius + a2.radius) * 2
                if link_dist < 1e-9:
                    link_dist = self._nn_mean * 3
                if d < link_dist:
                    chain_members.append(j)

            if len(chain_members) >= 2:
                # Sort by severity (critical first) then by type for causal ordering
                type_order = {"density": 0, "spacing": 1, "cluster": 2, "boundary": 3}
                chain_members.sort(key=lambda idx: (
                    type_order.get(self._anomalies[idx].anomaly_type, 9),
                ))
                anom_ids = [self._anomalies[m].anomaly_id for m in chain_members]
                causes = [self._anomalies[m].root_cause for m in chain_members]
                mechanism = " → ".join(
                    f"{self._anomalies[m].root_cause}({self._anomalies[m].anomaly_type})"
                    for m in chain_members
                )
                avg_conf = _mean([self._anomalies[m].cause_confidence for m in chain_members])
                self._chains.append(CausalChain(
                    chain_id=chain_id,
                    anomaly_ids=anom_ids,
                    mechanism=mechanism,
                    confidence=avg_conf,
                ))
                chain_id += 1
                used.update(chain_members)

        self._phase_reports["causal_chains"] = (
            f"Constructed {len(self._chains)} causal chain(s) from {len(self._anomalies)} anomalies."
        )

    # ---- Phase 6: Verdict Generation ----

    def _generate_verdict(self) -> ForensicVerdict:
        n_anomalies = len(self._anomalies)
        n_points = len(self._points)

        # Integrity score: start at 100, deduct for anomalies
        score = 100.0
        for a in self._anomalies:
            impact = len(a.affected_indices) / max(n_points, 1) * 100
            sev_mult = {"info": 0.5, "warning": 1.0, "critical": 2.0}.get(a.severity, 1.0)
            score -= impact * sev_mult
            score -= sev_mult * 2  # base penalty per anomaly
        score = max(0.0, min(100.0, score))

        # Risk level
        if score >= 80:
            risk = "low"
        elif score >= 60:
            risk = "medium"
        elif score >= 30:
            risk = "high"
        else:
            risk = "critical"

        # Primary diagnosis
        if n_anomalies == 0:
            primary = "clean — no anomalies detected"
        else:
            cause_counts: Dict[str, int] = collections.Counter(
                a.root_cause for a in self._anomalies
            )
            primary = cause_counts.most_common(1)[0][0]

        # Evidence summary
        ev_types = collections.Counter(
            ev.type for a in self._anomalies for ev in a.evidence
        )
        summary_parts = [f"{count}× {etype}" for etype, count in ev_types.most_common(5)]
        evidence_summary = (
            f"{n_anomalies} anomalies found, {sum(ev_types.values())} evidence items collected. "
            f"Key evidence: {', '.join(summary_parts)}."
            if summary_parts else "No anomalies — data appears clean."
        )

        # Remediation
        remediation = []
        cause_set = set(a.root_cause for a in self._anomalies)
        if "data_corruption" in cause_set:
            remediation.append("Review and clean corrupted data points (vormap_doctor.py --auto-fix)")
        if "systematic_drift" in cause_set:
            remediation.append("Apply drift correction — re-register data against reference frame")
        if "equipment_artifact" in cause_set:
            remediation.append("Check sensor/equipment calibration; remove grid-aligned artifacts")
        if "boundary_effect" in cause_set:
            remediation.append("Expand bounding box or apply edge-correction (vormap_buffer.py)")
        if "intentional_injection" in cause_set:
            remediation.append("Investigate suspicious point clusters — possible data tampering")
        if "sampling_bias" in cause_set:
            remediation.append("Re-sample with uniform coverage (vormap_sample.py --stratified)")
        if "natural_clustering" in cause_set:
            remediation.append("Natural clustering detected — no action needed unless unexpected")
        if not remediation:
            remediation.append("No remediation needed — data integrity is good.")

        self._verdict = ForensicVerdict(
            integrity_score=round(score, 1),
            risk_level=risk,
            primary_diagnosis=primary,
            anomaly_count=n_anomalies,
            anomalies=list(self._anomalies),
            causal_chains=list(self._chains),
            evidence_summary=evidence_summary,
            remediation=remediation,
            phase_reports=dict(self._phase_reports),
        )

        self._phase_reports["verdict"] = (
            f"Integrity={score:.1f}/100, Risk={risk}, Diagnosis={primary}"
        )

        return self._verdict

    # ---- Public API ----

    def investigate(self) -> ForensicVerdict:
        """Run the full 6-phase forensic investigation pipeline."""
        self._anomalies = []
        self._chains = []
        self._phase_reports = {}

        self._scene_survey()
        self._detect_anomalies()
        self._collect_evidence()
        self._classify_root_causes()
        self._build_causal_chains()
        return self._generate_verdict()

    def to_json(self, path: str):
        """Export the verdict as JSON."""
        if self._verdict is None:
            self.investigate()

        def _serialize(obj):
            if hasattr(obj, "__dict__"):
                return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
            if isinstance(obj, (list, tuple)):
                return list(obj)
            return str(obj)

        data = {
            "integrity_score": self._verdict.integrity_score,
            "risk_level": self._verdict.risk_level,
            "primary_diagnosis": self._verdict.primary_diagnosis,
            "anomaly_count": self._verdict.anomaly_count,
            "anomalies": [
                {
                    "anomaly_id": a.anomaly_id,
                    "anomaly_type": a.anomaly_type,
                    "severity": a.severity,
                    "affected_count": len(a.affected_indices),
                    "center": list(a.center),
                    "radius": a.radius,
                    "root_cause": a.root_cause,
                    "cause_confidence": round(a.cause_confidence, 3),
                    "evidence": [
                        {"type": e.type, "value": round(e.value, 4), "description": e.description}
                        for e in a.evidence
                    ],
                }
                for a in self._verdict.anomalies
            ],
            "causal_chains": [
                {
                    "chain_id": ch.chain_id,
                    "anomaly_ids": ch.anomaly_ids,
                    "mechanism": ch.mechanism,
                    "confidence": round(ch.confidence, 3),
                }
                for ch in self._verdict.causal_chains
            ],
            "evidence_summary": self._verdict.evidence_summary,
            "remediation": self._verdict.remediation,
            "phase_reports": self._verdict.phase_reports,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def to_html(self, path: str):
        """Export an interactive HTML forensic report."""
        if self._verdict is None:
            self.investigate()
        v = self._verdict

        risk_colors = {
            "low": "#27ae60", "medium": "#f39c12",
            "high": "#e67e22", "critical": "#e74c3c",
        }
        sev_colors = {
            "info": "#3498db", "warning": "#f39c12", "critical": "#e74c3c",
        }
        risk_color = risk_colors.get(v.risk_level, "#999")

        # SVG gauge
        pct = v.integrity_score / 100.0
        dash = pct * 283
        gauge_color = risk_colors.get(v.risk_level, "#999")

        anomaly_rows = ""
        for a in v.anomalies:
            sc = sev_colors.get(a.severity, "#999")
            anomaly_rows += (
                f"<tr>"
                f"<td>#{a.anomaly_id}</td>"
                f"<td>{_esc(a.anomaly_type)}</td>"
                f"<td><span style='color:{sc};font-weight:bold'>{_esc(a.severity)}</span></td>"
                f"<td>{len(a.affected_indices)}</td>"
                f"<td>({a.center[0]:.1f}, {a.center[1]:.1f})</td>"
                f"<td>{_esc(a.root_cause)}</td>"
                f"<td>{a.cause_confidence:.0%}</td>"
                f"</tr>\n"
            )

        chain_html = ""
        for ch in v.causal_chains:
            chain_html += (
                f"<div class='chain'>"
                f"<strong>Chain #{ch.chain_id}</strong> "
                f"(confidence {ch.confidence:.0%})<br>"
                f"<code>{_esc(ch.mechanism)}</code>"
                f"</div>\n"
            )
        if not chain_html:
            chain_html = "<p>No causal chains detected.</p>"

        phase_html = ""
        for phase, report in v.phase_reports.items():
            phase_html += f"<div class='phase'><strong>{_esc(phase)}</strong>: {_esc(report)}</div>\n"

        remed_html = "\n".join(f"<li>{_esc(r)}</li>" for r in v.remediation)

        evidence_html = ""
        for a in v.anomalies:
            if a.evidence:
                evidence_html += f"<h4>Anomaly #{a.anomaly_id} — {_esc(a.anomaly_type)}</h4><ul>"
                for e in a.evidence:
                    evidence_html += f"<li><strong>{_esc(e.type)}</strong> ({e.value:.4f}, weight {e.weight:.1f}): {_esc(e.description)}</li>"
                evidence_html += "</ul>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Spatial Forensics Report</title>
<style>
:root {{ --bg: #1a1a2e; --fg: #e0e0e0; --card: #16213e; --border: #0f3460; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 20px;
       background: var(--bg); color: var(--fg); }}
.light {{ --bg: #f5f5f5; --fg: #333; --card: #fff; --border: #ddd; }}
h1 {{ text-align: center; }}
.toggle {{ position: fixed; top: 10px; right: 10px; cursor: pointer;
           background: var(--card); border: 1px solid var(--border);
           color: var(--fg); padding: 6px 12px; border-radius: 4px; }}
.gauge {{ text-align: center; margin: 20px auto; }}
.gauge svg {{ width: 120px; height: 120px; }}
.risk-badge {{ display: inline-block; padding: 6px 18px; border-radius: 20px;
              color: #fff; font-weight: bold; font-size: 1.1em;
              background: {risk_color}; }}
.card {{ background: var(--card); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px; margin: 16px 0; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); }}
th {{ background: var(--border); }}
.chain {{ margin: 8px 0; padding: 10px; background: var(--bg);
         border-left: 3px solid #e67e22; border-radius: 4px; }}
.phase {{ padding: 6px 0; border-bottom: 1px solid var(--border); }}
code {{ background: rgba(255,255,255,0.1); padding: 2px 6px; border-radius: 3px; }}
</style>
</head>
<body>
<button class="toggle" onclick="document.body.classList.toggle('light')">🌓 Theme</button>
<h1>🔬 Spatial Forensics Report</h1>
<p style="text-align:center;opacity:0.7">Generated {_esc(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>

<div class="gauge">
<svg viewBox="0 0 100 100">
<circle cx="50" cy="50" r="45" fill="none" stroke="#333" stroke-width="8" />
<circle cx="50" cy="50" r="45" fill="none" stroke="{gauge_color}" stroke-width="8"
  stroke-dasharray="{dash:.1f} 283" stroke-dashoffset="0"
  transform="rotate(-90 50 50)" stroke-linecap="round" />
<text x="50" y="54" text-anchor="middle" fill="{gauge_color}" font-size="18" font-weight="bold">
{v.integrity_score:.0f}</text>
</svg>
<div>Integrity Score</div>
<div style="margin-top:8px"><span class="risk-badge">{_esc(v.risk_level.upper())}</span></div>
</div>

<div class="card">
<h3>📋 Diagnosis</h3>
<p><strong>Primary:</strong> {_esc(v.primary_diagnosis)}</p>
<p>{_esc(v.evidence_summary)}</p>
</div>

<div class="card">
<h3>⏱️ Investigation Phases</h3>
{phase_html}
</div>

<div class="card">
<h3>🔍 Anomalies ({v.anomaly_count})</h3>
<table>
<tr><th>ID</th><th>Type</th><th>Severity</th><th>Points</th><th>Center</th><th>Cause</th><th>Confidence</th></tr>
{anomaly_rows}
</table>
</div>

<div class="card">
<h3>🔗 Causal Chains</h3>
{chain_html}
</div>

<div class="card">
<h3>📑 Evidence Details</h3>
{evidence_html if evidence_html else "<p>No evidence collected.</p>"}
</div>

<div class="card">
<h3>🛠️ Remediation</h3>
<ul>{remed_html}</ul>
</div>

</body>
</html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------


def investigate(source, grid_resolution: int = 20) -> ForensicVerdict:
    """Load points from file and run forensic investigation.

    Parameters
    ----------
    source : str or list
        File path or list of (x, y) points.
    grid_resolution : int
        Density grid resolution.

    Returns
    -------
    ForensicVerdict
    """
    if isinstance(source, str):
        pts = _load_points(source)
    else:
        pts = list(source)
    engine = ForensicsEngine(pts, grid_resolution=grid_resolution)
    return engine.investigate()


def forensics_demo():
    """Generate demo data with known anomalies and investigate it.

    Uses a local ``random.Random`` instance so the demo does not
    perturb the host process's global RNG state. See issue #192.
    """
    rng = random.Random(42)
    pts = []

    # Base uniform-ish scatter
    for _ in range(200):
        pts.append((rng.uniform(50, 950), rng.uniform(50, 950)))

    # Inject tight cluster (intentional injection)
    for _ in range(15):
        pts.append((500 + rng.gauss(0, 3), 500 + rng.gauss(0, 3)))

    # Inject boundary accumulation
    for _ in range(30):
        pts.append((rng.uniform(0, 5), rng.uniform(0, 1000)))

    # Inject grid-aligned points (equipment artifact)
    for gx in range(0, 100, 10):
        for gy in range(0, 100, 10):
            pts.append((200 + gx, 200 + gy))

    engine = ForensicsEngine(pts, grid_resolution=20)
    verdict = engine.investigate()

    print(f"\n{'='*60}")
    print("  SPATIAL FORENSICS DEMO REPORT")
    print(f"{'='*60}")
    print(f"  Points analyzed: {len(pts)}")
    print(f"  Integrity score: {verdict.integrity_score:.1f}/100")
    print(f"  Risk level:      {verdict.risk_level.upper()}")
    print(f"  Diagnosis:       {verdict.primary_diagnosis}")
    print(f"  Anomalies found: {verdict.anomaly_count}")
    print()
    for a in verdict.anomalies:
        print(f"  [{a.severity:>8}] #{a.anomaly_id} {a.anomaly_type}: "
              f"{a.root_cause} (confidence {a.cause_confidence:.0%}), "
              f"{len(a.affected_indices)} points")
    if verdict.causal_chains:
        print("\n  Causal Chains:")
        for ch in verdict.causal_chains:
            print(f"    Chain #{ch.chain_id}: {ch.mechanism} "
                  f"(confidence {ch.confidence:.0%})")
    print("\n  Remediation:")
    for r in verdict.remediation:
        print(f"    • {r}")
    print(f"{'='*60}\n")

    return verdict


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_cli():
    p = argparse.ArgumentParser(
        description="Spatial Anomaly Forensics Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", nargs="?", help="Point data file")
    p.add_argument("--json", dest="json_out", help="Export JSON report")
    p.add_argument("--html", dest="html_out", help="Export HTML report")
    p.add_argument("--grid-resolution", type=int, default=20,
                   help="Density grid resolution (default 20)")
    p.add_argument("--demo", action="store_true", help="Run demo with synthetic data")
    return p


def main(argv=None):
    parser = _build_cli()
    args = parser.parse_args(argv)

    if args.demo:
        verdict = forensics_demo()
        if args.json_out:
            engine = ForensicsEngine([], grid_resolution=args.grid_resolution)
            engine._verdict = verdict
            engine.to_json(args.json_out)
            print(f"JSON report saved to {args.json_out}")
        if args.html_out:
            engine = ForensicsEngine([], grid_resolution=args.grid_resolution)
            engine._verdict = verdict
            engine.to_html(args.html_out)
            print(f"HTML report saved to {args.html_out}")
        return

    if not args.input:
        parser.error("Input file required (or use --demo)")

    pts = _load_points(args.input)
    engine = ForensicsEngine(pts, grid_resolution=args.grid_resolution)
    verdict = engine.investigate()

    print(f"\n{'='*60}")
    print("  SPATIAL FORENSICS REPORT")
    print(f"{'='*60}")
    print(f"  Integrity score: {verdict.integrity_score:.1f}/100")
    print(f"  Risk level:      {verdict.risk_level.upper()}")
    print(f"  Diagnosis:       {verdict.primary_diagnosis}")
    print(f"  Anomalies:       {verdict.anomaly_count}")
    for a in verdict.anomalies:
        print(f"    [{a.severity:>8}] #{a.anomaly_id} {a.anomaly_type}: "
              f"{a.root_cause} ({a.cause_confidence:.0%})")
    if verdict.causal_chains:
        print("  Causal chains:")
        for ch in verdict.causal_chains:
            print(f"    #{ch.chain_id}: {ch.mechanism}")
    print("  Remediation:")
    for r in verdict.remediation:
        print(f"    • {r}")
    print(f"{'='*60}\n")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON report saved to {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML report saved to {args.html_out}")


if __name__ == "__main__":
    main()
