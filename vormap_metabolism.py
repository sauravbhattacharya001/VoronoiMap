#!/usr/bin/env python3
"""Spatial Metabolism Engine — autonomous resource flow analysis.

Models how abstract "resources" are produced, consumed, and traded
across Voronoi cells.  Enables spatial economic analysis: which cells
are producers vs consumers, where bottlenecks constrain flow, and how
efficiently the system moves resources from surplus to deficit areas.

Seven analysis engines:

- **Production Estimator** — Assigns production capacity from cell
  area and connectivity (more area × more neighbors → higher output).
- **Consumption Modeler** — Models demand by proximity to the spatial
  centroid (central cells consume more) with deterministic noise.
- **Trade Flow Computer** — Routes surplus from producers to
  neighboring deficit cells proportional to inverse distance.
- **Bottleneck Detector** — Identifies cells that constrain trade:
  high pass-through volume, low self-sufficiency, critical bridging.
- **Efficiency Analyzer** — Per-cell and system-wide resource
  utilization ratios.
- **Metabolic Rate Calculator** — Throughput-per-unit-area: how
  metabolically active each cell is.
- **Autonomous Insight Generator** — Natural-language insights,
  Gini coefficient of surplus, and actionable recommendations.

Usage (Python API)::

    from vormap_metabolism import MetabolismEngine, metabolism_analyze, metabolism_demo

    # Quick one-liner
    result = metabolism_analyze("points.txt")
    print(f"Health: {result.health_score:.1f}/100")

    # Detailed API
    engine = MetabolismEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze()
    engine.to_html("metabolism.html")

    # Demo
    metabolism_demo()

CLI::

    python vormap_metabolism.py points.txt
    python vormap_metabolism.py points.txt --json out.json --html dash.html
    python vormap_metabolism.py --demo
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import random
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CellMetabolism:
    """Metabolic profile for a single spatial cell."""
    cell_id: int
    x: float
    y: float
    production: float = 0.0
    consumption: float = 0.0
    surplus: float = 0.0
    imports: float = 0.0
    exports: float = 0.0
    efficiency: float = 0.0
    metabolic_rate: float = 0.0
    role: str = "balanced"


@dataclass
class TradeFlow:
    """A directed resource flow between two cells."""
    source: int
    target: int
    volume: float
    distance: float
    efficiency: float  # volume / distance


@dataclass
class MetabolismResult:
    """Full metabolism analysis result."""
    cells: List[CellMetabolism] = field(default_factory=list)
    trade_flows: List[TradeFlow] = field(default_factory=list)
    bottlenecks: List[int] = field(default_factory=list)
    trade_balance: Dict[int, float] = field(default_factory=dict)
    total_production: float = 0.0
    total_consumption: float = 0.0
    system_efficiency: float = 0.0
    health_score: float = 0.0
    insights: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two 2-D points."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _load_points(path: str) -> List[Tuple[float, float]]:
    """Load points from a whitespace-separated text file."""
    pts: List[Tuple[float, float]] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _knn_adjacency(
    points: List[Tuple[float, float]], k: int = 6
) -> Dict[int, List[int]]:
    """Build symmetric k-nearest-neighbor adjacency."""
    n = len(points)
    k = min(k, n - 1)
    adj: Dict[int, List[int]] = {i: [] for i in range(n)}
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append((j, _euclidean(points[i], points[j])))
        dists.sort(key=lambda t: t[1])
        for j, _ in dists[:k]:
            if j not in adj[i]:
                adj[i].append(j)
            if i not in adj[j]:
                adj[j].append(i)
    return adj


def _voronoi_areas(
    points: List[Tuple[float, float]], adj: Dict[int, List[int]]
) -> List[float]:
    """Approximate Voronoi cell areas via neighbour midpoint polygons."""
    n = len(points)
    areas: List[float] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if len(nbrs) < 3:
            areas.append(1.0)
            continue
        mids = []
        for j in nbrs:
            mx = (points[i][0] + points[j][0]) / 2.0
            my = (points[i][1] + points[j][1]) / 2.0
            mids.append((mx, my))
        cx = sum(m[0] for m in mids) / len(mids)
        cy = sum(m[1] for m in mids) / len(mids)
        mids.sort(key=lambda m: math.atan2(m[1] - cy, m[0] - cx))
        area = 0.0
        for idx in range(len(mids)):
            x1, y1 = mids[idx]
            x2, y2 = mids[(idx + 1) % len(mids)]
            area += x1 * y2 - x2 * y1
        areas.append(abs(area) / 2.0)
    return areas


def _normalize(values: List[float]) -> List[float]:
    """Min-max normalize to [0, 1]."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _gini(values: List[float]) -> float:
    """Compute Gini coefficient of a list of values."""
    if not values or len(values) < 2:
        return 0.0
    vs = sorted(values)
    n = len(vs)
    total = sum(vs)
    if total < 1e-12:
        return 0.0
    cum = 0.0
    for i, v in enumerate(vs):
        cum += (2 * (i + 1) - n - 1) * v
    return cum / (n * total)


# ---------------------------------------------------------------------------
# Engine 1: Production Estimator
# ---------------------------------------------------------------------------


def _estimate_production(
    points: List[Tuple[float, float]],
    areas: List[float],
    adj: Dict[int, List[int]],
) -> List[float]:
    """Production = area × connectivity_factor.

    connectivity_factor = 1 + 0.1 * neighbor_count.
    """
    production = []
    for i in range(len(points)):
        connectivity = 1.0 + 0.1 * len(adj.get(i, []))
        production.append(areas[i] * connectivity)
    return production


# ---------------------------------------------------------------------------
# Engine 2: Consumption Modeler
# ---------------------------------------------------------------------------


def _model_consumption(
    points: List[Tuple[float, float]],
    areas: List[float],
    seed: int = 42,
) -> List[float]:
    """Consumption based on proximity to centroid + deterministic noise."""
    if not points:
        return []
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    max_dist = max(_euclidean(p, (cx, cy)) for p in points) or 1.0
    rng = random.Random(seed)
    consumption = []
    for i, p in enumerate(points):
        d = _euclidean(p, (cx, cy))
        centrality = 1.0 - (d / max_dist) * 0.6  # central cells consume more
        noise = rng.uniform(0.8, 1.2)
        consumption.append(areas[i] * centrality * noise)
    return consumption


# ---------------------------------------------------------------------------
# Engine 3: Trade Flow Computer
# ---------------------------------------------------------------------------


def _compute_trade_flows(
    points: List[Tuple[float, float]],
    adj: Dict[int, List[int]],
    surplus: List[float],
) -> Tuple[List[TradeFlow], Dict[int, float], Dict[int, float]]:
    """Route surplus to neighbouring deficit cells proportional to 1/distance."""
    n = len(points)
    flows: List[TradeFlow] = []
    cell_imports = {i: 0.0 for i in range(n)}
    cell_exports = {i: 0.0 for i in range(n)}

    for i in range(n):
        if surplus[i] <= 0:
            continue
        # find deficit neighbours
        deficit_nbrs = [(j, _euclidean(points[i], points[j]))
                        for j in adj.get(i, []) if surplus[j] < 0]
        if not deficit_nbrs:
            continue
        inv_dists = [(j, 1.0 / max(d, 1e-6)) for j, d in deficit_nbrs]
        total_inv = sum(w for _, w in inv_dists)
        for j, w in inv_dists:
            vol = surplus[i] * (w / total_inv)
            dist = _euclidean(points[i], points[j])
            eff = vol / max(dist, 1e-6)
            flows.append(TradeFlow(source=i, target=j, volume=vol,
                                   distance=dist, efficiency=eff))
            cell_exports[i] += vol
            cell_imports[j] += vol

    return flows, cell_imports, cell_exports


# ---------------------------------------------------------------------------
# Engine 4: Bottleneck Detector
# ---------------------------------------------------------------------------


def _detect_bottlenecks(
    points: List[Tuple[float, float]],
    adj: Dict[int, List[int]],
    flows: List[TradeFlow],
    production: List[float],
    consumption: List[float],
) -> List[int]:
    """Identify bottleneck cells by pass-through volume and self-sufficiency."""
    n = len(points)
    pass_through: Dict[int, float] = {i: 0.0 for i in range(n)}

    # Count trade volume passing through each cell's neighbourhood
    for f in flows:
        pass_through[f.source] += f.volume
        pass_through[f.target] += f.volume

    scores: List[Tuple[int, float]] = []
    for i in range(n):
        # Self-sufficiency: low production vs consumption = bottleneck potential
        self_suff = production[i] / max(consumption[i], 1e-6)
        # Criticality: high pass-through + low self-sufficiency
        criticality = pass_through[i] * (1.0 / max(self_suff, 0.1))
        scores.append((i, criticality))

    scores.sort(key=lambda t: t[1], reverse=True)
    # Top 20% or at least 1
    k = max(1, n // 5)
    return [s[0] for s in scores[:k]]


# ---------------------------------------------------------------------------
# Engine 5: Efficiency Analyzer
# ---------------------------------------------------------------------------


def _analyze_efficiency(
    production: List[float],
    consumption: List[float],
    cell_imports: Dict[int, float],
    cell_exports: Dict[int, float],
) -> Tuple[List[float], float]:
    """Per-cell efficiency and system-wide average."""
    n = len(production)
    efficiencies = []
    for i in range(n):
        supply = production[i] + cell_imports.get(i, 0.0)
        demand = consumption[i] + cell_exports.get(i, 0.0)
        eff = min(1.0, supply / max(demand, 1e-6))
        efficiencies.append(eff)

    if not efficiencies:
        return [], 0.0
    system_eff = sum(efficiencies) / len(efficiencies) * 100.0
    return efficiencies, system_eff


# ---------------------------------------------------------------------------
# Engine 6: Metabolic Rate Calculator
# ---------------------------------------------------------------------------


def _metabolic_rates(
    areas: List[float],
    cell_imports: Dict[int, float],
    cell_exports: Dict[int, float],
) -> List[float]:
    """Metabolic rate = total throughput / area."""
    rates = []
    for i in range(len(areas)):
        throughput = cell_imports.get(i, 0.0) + cell_exports.get(i, 0.0)
        rates.append(throughput / max(areas[i], 1e-6))
    return rates


# ---------------------------------------------------------------------------
# Engine 7: Autonomous Insight Generator
# ---------------------------------------------------------------------------


def _generate_insights(
    cells: List[CellMetabolism],
    flows: List[TradeFlow],
    bottlenecks: List[int],
    system_efficiency: float,
) -> List[str]:
    """Generate natural-language insights about the metabolic system."""
    insights: List[str] = []
    if not cells:
        return ["No cells to analyze."]

    # Largest producer
    top_prod = max(cells, key=lambda c: c.production)
    insights.append(
        f"Largest producer: Cell {top_prod.cell_id} "
        f"(production={top_prod.production:.2f})")

    # Biggest consumer
    top_cons = max(cells, key=lambda c: c.consumption)
    insights.append(
        f"Biggest consumer: Cell {top_cons.cell_id} "
        f"(consumption={top_cons.consumption:.2f})")

    # Most critical bottleneck
    if bottlenecks:
        insights.append(
            f"Most critical bottleneck: Cell {bottlenecks[0]}")

    # Trade volume
    if flows:
        total_vol = sum(f.volume for f in flows)
        insights.append(f"Total trade volume: {total_vol:.2f}")
        avg_eff = sum(f.efficiency for f in flows) / len(flows)
        insights.append(f"Average trade efficiency: {avg_eff:.2f}")

    # Surplus Gini coefficient
    surpluses = [abs(c.surplus) for c in cells]
    gini = _gini(surpluses)
    if gini > 0.5:
        insights.append(
            f"High surplus inequality (Gini={gini:.3f}) — "
            f"resources are concentrated in few cells")
    elif gini > 0.3:
        insights.append(
            f"Moderate surplus inequality (Gini={gini:.3f})")
    else:
        insights.append(
            f"Low surplus inequality (Gini={gini:.3f}) — "
            f"resources are well distributed")

    # System efficiency commentary
    if system_efficiency >= 80:
        insights.append(
            f"System efficiency is strong at {system_efficiency:.1f}%")
    elif system_efficiency >= 50:
        insights.append(
            f"System efficiency is moderate at {system_efficiency:.1f}% — "
            f"consider adding production near deficit areas")
    else:
        insights.append(
            f"System efficiency is low at {system_efficiency:.1f}% — "
            f"significant resource mismatch detected")

    # Deficit cells that could benefit from new production
    deficit_cells = [c for c in cells if c.surplus < 0]
    if deficit_cells:
        worst = min(deficit_cells, key=lambda c: c.surplus)
        insights.append(
            f"Cell {worst.cell_id} has the largest deficit "
            f"({worst.surplus:.2f}) — prioritize production here")

    # Role distribution
    roles = {}
    for c in cells:
        roles[c.role] = roles.get(c.role, 0) + 1
    role_str = ", ".join(f"{k}: {v}" for k, v in sorted(roles.items()))
    insights.append(f"Role distribution: {role_str}")

    return insights


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class MetabolismEngine:
    """Spatial Metabolism Engine — autonomous resource flow analysis."""

    def __init__(
        self,
        points: Optional[List[Tuple[float, float]]] = None,
        path: Optional[str] = None,
        adj_k: int = 6,
        seed: int = 42,
    ):
        if path:
            self._points = _load_points(path)
        elif points:
            self._points = list(points)
        else:
            self._points = []
        self._adj_k = adj_k
        self._seed = seed
        self._result: Optional[MetabolismResult] = None

    def analyze(self) -> MetabolismResult:
        """Run full metabolism analysis pipeline."""
        pts = self._points
        n = len(pts)
        if n < 2:
            self._result = MetabolismResult(
                health_score=0.0,
                insights=["Need at least 2 points for analysis."],
            )
            return self._result

        # Build spatial structures
        adj = _knn_adjacency(pts, self._adj_k)
        areas = _voronoi_areas(pts, adj)

        # Engine 1 & 2: Production and Consumption
        production = _estimate_production(pts, areas, adj)
        consumption = _model_consumption(pts, areas, self._seed)
        surplus = [production[i] - consumption[i] for i in range(n)]

        # Engine 3: Trade Flows
        flows, cell_imports, cell_exports = _compute_trade_flows(
            pts, adj, surplus)

        # Engine 4: Bottlenecks
        bottlenecks = _detect_bottlenecks(
            pts, adj, flows, production, consumption)

        # Engine 5: Efficiency
        efficiencies, system_eff = _analyze_efficiency(
            production, consumption, cell_imports, cell_exports)

        # Engine 6: Metabolic Rates
        rates = _metabolic_rates(areas, cell_imports, cell_exports)

        # Assign roles
        cells: List[CellMetabolism] = []
        for i in range(n):
            if i in bottlenecks:
                role = "bottleneck"
            elif surplus[i] > areas[i] * 0.1:
                role = "producer"
            elif surplus[i] < -areas[i] * 0.1:
                role = "consumer"
            else:
                role = "balanced"
            cells.append(CellMetabolism(
                cell_id=i,
                x=pts[i][0],
                y=pts[i][1],
                production=production[i],
                consumption=consumption[i],
                surplus=surplus[i],
                imports=cell_imports.get(i, 0.0),
                exports=cell_exports.get(i, 0.0),
                efficiency=efficiencies[i],
                metabolic_rate=rates[i],
                role=role,
            ))

        trade_balance = {i: cell_imports.get(i, 0.0) - cell_exports.get(i, 0.0)
                         for i in range(n)}

        # Health score
        trade_participants = sum(
            1 for i in range(n)
            if cell_imports.get(i, 0.0) > 0 or cell_exports.get(i, 0.0) > 0)
        trade_coverage = (trade_participants / n * 100.0) if n > 0 else 0.0

        surpluses_abs = [abs(s) for s in surplus]
        gini = _gini(surpluses_abs)
        balance_score = (1.0 - gini) * 100.0

        bottleneck_ratio = len(bottlenecks) / max(n, 1)
        bottleneck_risk = (1.0 - bottleneck_ratio) * 100.0

        health = (
            system_eff * 0.4
            + trade_coverage * 0.2
            + balance_score * 0.2
            + bottleneck_risk * 0.2
        )
        health = max(0.0, min(100.0, health))

        # Engine 7: Insights
        insights = _generate_insights(cells, flows, bottlenecks, system_eff)

        self._result = MetabolismResult(
            cells=cells,
            trade_flows=flows,
            bottlenecks=bottlenecks,
            trade_balance=trade_balance,
            total_production=sum(production),
            total_consumption=sum(consumption),
            system_efficiency=system_eff,
            health_score=health,
            insights=insights,
        )
        return self._result

    def to_json(self, path: str) -> None:
        """Export result to JSON."""
        if not self._result:
            self.analyze()
        assert self._result is not None
        data = {
            "health_score": self._result.health_score,
            "system_efficiency": self._result.system_efficiency,
            "total_production": self._result.total_production,
            "total_consumption": self._result.total_consumption,
            "bottlenecks": self._result.bottlenecks,
            "insights": self._result.insights,
            "cells": [asdict(c) for c in self._result.cells],
            "trade_flows": [asdict(f) for f in self._result.trade_flows],
            "trade_balance": {str(k): v
                              for k, v in self._result.trade_balance.items()},
        }
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)

    def to_html(self, path: str) -> None:
        """Export interactive HTML dashboard."""
        if not self._result:
            self.analyze()
        r = self._result
        assert r is not None
        esc = html_mod.escape

        rows_cells = ""
        for c in r.cells:
            rows_cells += (
                f"<tr><td>{c.cell_id}</td>"
                f"<td>{c.x:.2f}</td><td>{c.y:.2f}</td>"
                f"<td>{c.production:.2f}</td>"
                f"<td>{c.consumption:.2f}</td>"
                f"<td>{c.surplus:.2f}</td>"
                f"<td>{c.imports:.2f}</td>"
                f"<td>{c.exports:.2f}</td>"
                f"<td>{c.efficiency:.3f}</td>"
                f"<td>{c.metabolic_rate:.3f}</td>"
                f"<td>{esc(c.role)}</td></tr>\n"
            )

        rows_flows = ""
        for f in r.trade_flows:
            rows_flows += (
                f"<tr><td>{f.source}</td><td>{f.target}</td>"
                f"<td>{f.volume:.3f}</td><td>{f.distance:.3f}</td>"
                f"<td>{f.efficiency:.3f}</td></tr>\n"
            )

        bottleneck_html = ", ".join(str(b) for b in r.bottlenecks) or "None"
        insights_html = "".join(
            f"<li>{esc(ins)}</li>" for ins in r.insights)

        # SVG gauge
        angle = r.health_score / 100.0 * 180.0
        rad = math.radians(180 - angle)
        ex = 100 + 80 * math.cos(rad)
        ey = 100 - 80 * math.sin(rad)
        large = 1 if angle > 90 else 0
        color = "#2ecc71" if r.health_score >= 70 else (
            "#f39c12" if r.health_score >= 40 else "#e74c3c")
        gauge_svg = (
            f'<svg width="200" height="120" viewBox="0 0 200 120">'
            f'<path d="M20,100 A80,80 0 0,1 180,100" '
            f'fill="none" stroke="#eee" stroke-width="12"/>'
            f'<path d="M20,100 A80,80 0 {large},1 {ex:.1f},{ey:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="12" '
            f'stroke-linecap="round"/>'
            f'<text x="100" y="95" text-anchor="middle" '
            f'font-size="28" font-weight="bold" fill="{color}">'
            f'{r.health_score:.0f}</text>'
            f'<text x="100" y="115" text-anchor="middle" '
            f'font-size="11" fill="#888">Health Score</text></svg>'
        )

        html_content = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Spatial Metabolism Dashboard</title>
<style>
body{{font-family:system-ui,sans-serif;margin:20px;background:#f8f9fa;color:#333}}
h1{{color:#2c3e50}} h2{{color:#34495e;border-bottom:2px solid #ecf0f1;padding-bottom:6px}}
.card{{background:#fff;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ddd;padding:6px 10px;text-align:right}}
th{{background:#34495e;color:#fff;text-align:center}} tr:nth-child(even){{background:#f9f9f9}}
.stats{{display:flex;gap:16px;flex-wrap:wrap}}
.stat{{background:#fff;border-radius:8px;padding:12px 20px;box-shadow:0 1px 3px rgba(0,0,0,.1);text-align:center}}
.stat .val{{font-size:24px;font-weight:bold;color:#2c3e50}} .stat .lbl{{font-size:12px;color:#888}}
ul{{line-height:1.8}}
</style></head><body>
<h1>🔬 Spatial Metabolism Dashboard</h1>
<div class="stats">
<div class="stat">{gauge_svg}</div>
<div class="stat"><div class="val">{r.system_efficiency:.1f}%</div><div class="lbl">System Efficiency</div></div>
<div class="stat"><div class="val">{r.total_production:.1f}</div><div class="lbl">Total Production</div></div>
<div class="stat"><div class="val">{r.total_consumption:.1f}</div><div class="lbl">Total Consumption</div></div>
<div class="stat"><div class="val">{len(r.trade_flows)}</div><div class="lbl">Trade Routes</div></div>
<div class="stat"><div class="val">{len(r.bottlenecks)}</div><div class="lbl">Bottlenecks</div></div>
</div>
<div class="card"><h2>Bottlenecks</h2><p>Cells: {bottleneck_html}</p></div>
<div class="card"><h2>Autonomous Insights</h2><ul>{insights_html}</ul></div>
<div class="card"><h2>Cell Metabolism</h2>
<table><tr><th>ID</th><th>X</th><th>Y</th><th>Production</th><th>Consumption</th>
<th>Surplus</th><th>Imports</th><th>Exports</th><th>Efficiency</th>
<th>Rate</th><th>Role</th></tr>
{rows_cells}</table></div>
<div class="card"><h2>Trade Flows</h2>
<table><tr><th>Source</th><th>Target</th><th>Volume</th><th>Distance</th><th>Efficiency</th></tr>
{rows_flows}</table></div>
</body></html>"""

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_content)


# ---------------------------------------------------------------------------
# Public convenience API
# ---------------------------------------------------------------------------


def metabolism_analyze(
    path_or_points,
    adj_k: int = 6,
    seed: int = 42,
) -> MetabolismResult:
    """One-liner analysis — accepts a file path or list of (x, y) tuples."""
    if isinstance(path_or_points, str):
        engine = MetabolismEngine(path=path_or_points, adj_k=adj_k, seed=seed)
    else:
        engine = MetabolismEngine(
            points=path_or_points, adj_k=adj_k, seed=seed)
    return engine.analyze()


def metabolism_demo() -> None:
    """Generate demo points, run analysis, print summary."""
    rng = random.Random(42)
    pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
    engine = MetabolismEngine(points=pts, seed=42)
    result = engine.analyze()

    print("=" * 60)
    print("  Spatial Metabolism Engine — Demo")
    print("=" * 60)
    print(f"  Points:            {len(pts)}")
    print(f"  Health Score:      {result.health_score:.1f}/100")
    print(f"  System Efficiency: {result.system_efficiency:.1f}%")
    print(f"  Total Production:  {result.total_production:.2f}")
    print(f"  Total Consumption: {result.total_consumption:.2f}")
    print(f"  Trade Routes:      {len(result.trade_flows)}")
    print(f"  Bottlenecks:       {result.bottlenecks}")
    print()
    print("  Insights:")
    for ins in result.insights:
        print(f"    • {ins}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    parser = argparse.ArgumentParser(
        description="Spatial Metabolism Engine — autonomous resource flow analysis")
    parser.add_argument("points", nargs="?",
                        help="Path to points file (x y per line)")
    parser.add_argument("--json", dest="json_out",
                        help="Export result to JSON")
    parser.add_argument("--html", dest="html_out",
                        help="Export interactive HTML dashboard")
    parser.add_argument("--adj-k", type=int, default=6,
                        help="K for knn adjacency")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for consumption model")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo with generated data")
    args = parser.parse_args()

    if args.demo:
        metabolism_demo()
        return

    if not args.points:
        parser.error("Must provide points file or --demo")

    engine = MetabolismEngine(
        path=args.points, adj_k=args.adj_k, seed=args.seed)
    result = engine.analyze()

    print(f"Metabolism Health: {result.health_score:.1f}/100")
    print(f"System Efficiency: {result.system_efficiency:.1f}%")
    print(f"Production: {result.total_production:.2f}  "
          f"Consumption: {result.total_consumption:.2f}")
    print(f"Trade routes: {len(result.trade_flows)}  "
          f"Bottlenecks: {result.bottlenecks}")

    for ins in result.insights:
        print(f"  • {ins}")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON: {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML: {args.html_out}")


if __name__ == "__main__":
    _cli()
