"""Spatial Auction Engine -- autonomous auction-based resource allocation for Voronoi tessellations.

Models Voronoi cells as autonomous bidding agents competing for shared spatial
resources (capacity, services, infrastructure).  Uses auction theory -- sealed-bid,
Vickrey (second-price), combinatorial, and iterative ascending mechanisms -- to
find efficient allocations, then evaluates fairness and welfare.

Seven analysis engines run autonomously:

- **Demand Assessor** -- computes per-cell resource demand from area, neighbor
  count, centrality, and optional weights.
- **Budget Calculator** -- assigns bidding budgets based on urgency, utilization
  history, and priority tiers.
- **Sealed-Bid Auction** -- first-price sealed-bid: highest bidder wins, pays
  own bid.  Tracks revenue, surplus, efficiency.
- **Vickrey Auction** -- second-price: winner pays second-highest bid.  Measures
  incentive compatibility.
- **Combinatorial Auction** -- cells bid on bundles; greedy allocation with
  conflict resolution and complementarity tracking.
- **Iterative Ascending Auction** -- English-style price discovery with
  round-by-round escalation until market clears.
- **Fairness Analyzer** -- Gini coefficient, envy-freeness, Pareto efficiency,
  utilitarian vs egalitarian welfare.

Usage (Python API)::

    from vormap_auction import (
        run_auction, AuctionReport,
        assess_demand, calculate_budgets,
        run_sealed_bid, run_vickrey, run_combinatorial, run_ascending,
        analyze_fairness,
    )

    points = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
    bounds = (0, 1000, 0, 1000)

    report = run_auction(points, bounds=bounds, n_resources=4)
    print(report.summary_text())
    report.to_json("auction.json")
    report.to_html("auction.html")

CLI::

    voronoimap datauni5.txt 5 --auction
    voronoimap datauni5.txt 5 --auction --auction-json out.json
    voronoimap datauni5.txt 5 --auction --auction-html out.html
    voronoimap datauni5.txt 5 --auction --n-resources 5
    voronoimap datauni5.txt 5 --auction --mechanism vickrey
"""

from __future__ import annotations

import json
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Geometry helpers (inlined to avoid hard dep on vormap_utils at import time)
# ---------------------------------------------------------------------------


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _polygon_area(vertices: List[Tuple[float, float]]) -> float:
    """Shoelface formula."""
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
    """Polygon centroid."""
    n = len(vertices)
    if n == 0:
        return (0.0, 0.0)
    cx = sum(v[0] for v in vertices) / n
    cy = sum(v[1] for v in vertices) / n
    return (cx, cy)


def _voronoi_cells_simple(
    points: List[Tuple[float, float]], bounds: Tuple[float, float, float, float]
) -> List[List[Tuple[float, float]]]:
    """Approximate Voronoi cells via bounded grid sampling.

    For each point, the 'cell' is represented by the convex hull of grid
    points closest to it.  This avoids scipy dependency.
    """
    south, north, west, east = bounds
    step = max((north - south), (east - west)) / 40.0
    if step <= 0:
        step = 1.0
    # assign grid points
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
    # convex hull per cell
    cells = []
    for i in range(len(points)):
        pts = cell_pts[i]
        if len(pts) < 3:
            # fallback: small square around point
            px, py = points[i]
            cells.append([(px - step, py - step), (px + step, py - step),
                          (px + step, py + step), (px - step, py + step)])
        else:
            cells.append(_convex_hull(pts))
    return cells


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


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

PRIORITY_WEIGHTS = {"critical": 2.0, "high": 1.5, "medium": 1.0, "low": 0.5}


@dataclass
class CellBidder:
    cell_idx: int
    centroid: Tuple[float, float]
    area: float
    demand: float
    budget: float
    priority: str  # critical/high/medium/low
    bids: List[dict] = field(default_factory=list)
    allocations: List[str] = field(default_factory=list)
    utility: float = 0.0

    def to_dict(self) -> dict:
        return {
            "cell_idx": self.cell_idx,
            "centroid": list(self.centroid),
            "area": round(self.area, 2),
            "demand": round(self.demand, 4),
            "budget": round(self.budget, 2),
            "priority": self.priority,
            "bids_count": len(self.bids),
            "allocations": self.allocations,
            "utility": round(self.utility, 4),
        }


@dataclass
class ResourceBundle:
    resource_id: str
    quantity: float
    base_price: float
    location: Tuple[float, float]

    def to_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "quantity": self.quantity,
            "base_price": round(self.base_price, 2),
            "location": list(self.location),
        }


@dataclass
class AuctionRound:
    round_num: int
    bids: List[dict]  # {cell_idx, resource_id, amount}
    winners: List[dict]  # {cell_idx, resource_id, price_paid}
    clearing_prices: Dict[str, float]

    def to_dict(self) -> dict:
        return {
            "round_num": self.round_num,
            "n_bids": len(self.bids),
            "n_winners": len(self.winners),
            "clearing_prices": {k: round(v, 2) for k, v in self.clearing_prices.items()},
        }


@dataclass
class FairnessMetrics:
    gini_coefficient: float
    envy_free: bool
    envy_pairs: List[Tuple[int, int]]
    pareto_efficient: bool
    utilitarian_welfare: float
    egalitarian_welfare: float
    welfare_ratio: float

    def to_dict(self) -> dict:
        return {
            "gini_coefficient": round(self.gini_coefficient, 4),
            "envy_free": self.envy_free,
            "envy_pairs": self.envy_pairs,
            "pareto_efficient": self.pareto_efficient,
            "utilitarian_welfare": round(self.utilitarian_welfare, 4),
            "egalitarian_welfare": round(self.egalitarian_welfare, 4),
            "welfare_ratio": round(self.welfare_ratio, 4),
        }


@dataclass
class AuctionResults:
    mechanism: str
    winners: List[dict]
    total_revenue: float
    efficiency: float  # fraction of demand satisfied
    rounds: List[AuctionRound] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mechanism": self.mechanism,
            "winners": self.winners,
            "total_revenue": round(self.total_revenue, 2),
            "efficiency": round(self.efficiency, 4),
            "n_rounds": len(self.rounds),
        }


@dataclass
class AuctionReport:
    cells: List[CellBidder]
    resources: List[ResourceBundle]
    sealed_bid: Optional[AuctionResults]
    vickrey: Optional[AuctionResults]
    combinatorial: Optional[AuctionResults]
    ascending: Optional[AuctionResults]
    fairness: FairnessMetrics
    efficiency_score: float  # 0-100
    config: dict = field(default_factory=dict)

    def summary_text(self) -> str:
        lines = [
            "===========================================================",
            "  SPATIAL AUCTION ENGINE - REPORT",
            "===========================================================",
            f"  Cells: {len(self.cells)}  |  Resources: {len(self.resources)}",
            f"  Efficiency Score: {self.efficiency_score:.1f}/100",
            f"  Gini Coefficient: {self.fairness.gini_coefficient:.4f}",
            f"  Envy-Free: {'Yes' if self.fairness.envy_free else 'No'}",
            f"  Pareto Efficient: {'Yes' if self.fairness.pareto_efficient else 'No'}",
            f"  Utilitarian Welfare: {self.fairness.utilitarian_welfare:.4f}",
            f"  Egalitarian Welfare: {self.fairness.egalitarian_welfare:.4f}",
            "-----------------------------------------------------------",
        ]
        if self.sealed_bid:
            lines.append(f"  Sealed-Bid: revenue={self.sealed_bid.total_revenue:.2f}, "
                         f"efficiency={self.sealed_bid.efficiency:.2%}")
        if self.vickrey:
            lines.append(f"  Vickrey: revenue={self.vickrey.total_revenue:.2f}, "
                         f"efficiency={self.vickrey.efficiency:.2%}")
        if self.combinatorial:
            lines.append(f"  Combinatorial: revenue={self.combinatorial.total_revenue:.2f}, "
                         f"efficiency={self.combinatorial.efficiency:.2%}")
        if self.ascending:
            lines.append(f"  Ascending: revenue={self.ascending.total_revenue:.2f}, "
                         f"efficiency={self.ascending.efficiency:.2%}, "
                         f"rounds={len(self.ascending.rounds)}")
        lines.append("===========================================================")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "cells": [c.to_dict() for c in self.cells],
            "resources": [r.to_dict() for r in self.resources],
            "sealed_bid": self.sealed_bid.to_dict() if self.sealed_bid else None,
            "vickrey": self.vickrey.to_dict() if self.vickrey else None,
            "combinatorial": self.combinatorial.to_dict() if self.combinatorial else None,
            "ascending": self.ascending.to_dict() if self.ascending else None,
            "fairness": self.fairness.to_dict(),
            "efficiency_score": round(self.efficiency_score, 2),
            "config": self.config,
        }

    def to_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def to_html(self, path: str) -> None:
        html = _generate_html(self)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


# ---------------------------------------------------------------------------
# Engine 1: Demand Assessor
# ---------------------------------------------------------------------------


def assess_demand(
    points: List[Tuple[float, float]],
    bounds: Tuple[float, float, float, float],
    weights: Optional[List[float]] = None,
) -> List[float]:
    """Compute normalized demand per cell based on area and spatial centrality.

    Demand factors: cell area (larger = more demand), distance from spatial
    center (peripheral = more demand), neighbor density (fewer neighbors = more).
    """
    cells = _voronoi_cells_simple(points, bounds)
    south, north, west, east = bounds
    center = ((west + east) / 2, (south + north) / 2)
    total_area = (north - south) * (east - west)

    demands = []
    for i, cell in enumerate(cells):
        area = _polygon_area(cell)
        # Normalize area contribution
        area_factor = area / (total_area / len(points)) if total_area > 0 else 1.0
        # Periphery factor: further from center = more underserved
        dist_center = _euclidean(points[i], center)
        max_dist = _euclidean((west, south), center)
        periphery_factor = (dist_center / max_dist) if max_dist > 0 else 0.5
        # Combine
        raw = 0.5 * area_factor + 0.3 * (1 + periphery_factor) + 0.2
        if weights and i < len(weights):
            raw *= weights[i]
        demands.append(raw)

    # Normalize to 0-1
    max_d = max(demands) if demands else 1.0
    if max_d > 0:
        demands = [d / max_d for d in demands]
    return demands


# ---------------------------------------------------------------------------
# Engine 2: Budget Calculator
# ---------------------------------------------------------------------------


def calculate_budgets(
    demands: List[float],
    priorities: Optional[List[str]] = None,
    base_budget: float = 100.0,
) -> List[float]:
    """Assign budgets based on demand urgency and priority tier."""
    n = len(demands)
    if priorities is None:
        # Auto-assign priorities based on demand quartiles
        sorted_d = sorted(demands)
        q75 = sorted_d[int(0.75 * n)] if n > 0 else 0.5
        q50 = sorted_d[int(0.50 * n)] if n > 0 else 0.3
        q25 = sorted_d[int(0.25 * n)] if n > 0 else 0.1
        priorities = []
        for d in demands:
            if d >= q75:
                priorities.append("critical")
            elif d >= q50:
                priorities.append("high")
            elif d >= q25:
                priorities.append("medium")
            else:
                priorities.append("low")

    budgets = []
    for i in range(n):
        urgency = demands[i]
        pw = PRIORITY_WEIGHTS.get(priorities[i], 1.0)
        budget = base_budget * urgency * pw
        budgets.append(max(budget, 1.0))  # minimum budget of 1
    return budgets


# ---------------------------------------------------------------------------
# Engine 3: Sealed-Bid Auction
# ---------------------------------------------------------------------------


def run_sealed_bid(
    cells: List[CellBidder],
    resources: List[ResourceBundle],
    seed: Optional[int] = None,
) -> AuctionResults:
    """First-price sealed-bid auction. Each cell bids on its preferred resource."""
    rng = random.Random(seed if seed is not None else 42)

    # Each cell picks preferred resource (closest with enough quantity)
    # and bids: budget * strategy_factor
    bids_per_resource: Dict[str, List[Tuple[int, float]]] = {
        r.resource_id: [] for r in resources
    }

    for cell in cells:
        # Rank resources by proximity
        ranked = sorted(
            range(len(resources)),
            key=lambda ri: _euclidean(cell.centroid, resources[ri].location),
        )
        # Bid on top 1-2 resources
        n_bids = min(2, len(ranked))
        for ri_idx in range(n_bids):
            ri = ranked[ri_idx]
            r = resources[ri]
            # Strategy: bid proportional to budget, discounted for non-primary
            factor = 1.0 if ri_idx == 0 else 0.6
            noise = rng.uniform(0.85, 1.0)
            bid_amount = cell.budget * factor * noise
            bids_per_resource[r.resource_id].append((cell.cell_idx, bid_amount))
            cell.bids.append({
                "mechanism": "sealed_bid",
                "resource_id": r.resource_id,
                "amount": bid_amount,
            })

    # Resolve: highest bidder wins each resource
    winners = []
    total_revenue = 0.0
    allocated_cells = set()

    for r in resources:
        r_bids = bids_per_resource[r.resource_id]
        if not r_bids:
            continue
        r_bids.sort(key=lambda x: x[1], reverse=True)
        # Allocate to highest bidder not already allocated
        for cell_idx, amount in r_bids:
            if cell_idx not in allocated_cells:
                winners.append({
                    "cell_idx": cell_idx,
                    "resource_id": r.resource_id,
                    "price_paid": round(amount, 2),
                })
                total_revenue += amount
                allocated_cells.add(cell_idx)
                cells[cell_idx].allocations.append(r.resource_id)
                cells[cell_idx].utility += r.quantity / max(cells[cell_idx].demand, 0.01)
                break

    efficiency = len(winners) / max(len(resources), 1)
    return AuctionResults(
        mechanism="sealed_bid",
        winners=winners,
        total_revenue=round(total_revenue, 2),
        efficiency=efficiency,
    )


# ---------------------------------------------------------------------------
# Engine 4: Vickrey (Second-Price) Auction
# ---------------------------------------------------------------------------


def run_vickrey(
    cells: List[CellBidder],
    resources: List[ResourceBundle],
    seed: Optional[int] = None,
) -> AuctionResults:
    """Second-price auction: winner pays second-highest bid (truthful mechanism)."""
    rng = random.Random(seed if seed is not None else 42)

    bids_per_resource: Dict[str, List[Tuple[int, float]]] = {
        r.resource_id: [] for r in resources
    }

    for cell in cells:
        ranked = sorted(
            range(len(resources)),
            key=lambda ri: _euclidean(cell.centroid, resources[ri].location),
        )
        # In Vickrey, truthful bidding is dominant: bid true valuation
        for ri_idx in range(min(2, len(ranked))):
            ri = ranked[ri_idx]
            r = resources[ri]
            # True value = budget (no shading)
            factor = 1.0 if ri_idx == 0 else 0.5
            true_value = cell.budget * factor
            bids_per_resource[r.resource_id].append((cell.cell_idx, true_value))
            cell.bids.append({
                "mechanism": "vickrey",
                "resource_id": r.resource_id,
                "amount": true_value,
            })

    winners = []
    total_revenue = 0.0
    allocated_cells = set()

    for r in resources:
        r_bids = bids_per_resource[r.resource_id]
        if not r_bids:
            continue
        r_bids.sort(key=lambda x: x[1], reverse=True)
        for idx, (cell_idx, amount) in enumerate(r_bids):
            if cell_idx not in allocated_cells:
                # Pay second-highest bid
                second_price = r_bids[idx + 1][1] if idx + 1 < len(r_bids) else r.base_price
                winners.append({
                    "cell_idx": cell_idx,
                    "resource_id": r.resource_id,
                    "price_paid": round(second_price, 2),
                    "bid_amount": round(amount, 2),
                })
                total_revenue += second_price
                allocated_cells.add(cell_idx)
                cells[cell_idx].allocations.append(r.resource_id)
                cells[cell_idx].utility += r.quantity / max(cells[cell_idx].demand, 0.01)
                break

    efficiency = len(winners) / max(len(resources), 1)
    return AuctionResults(
        mechanism="vickrey",
        winners=winners,
        total_revenue=round(total_revenue, 2),
        efficiency=efficiency,
    )


# ---------------------------------------------------------------------------
# Engine 5: Combinatorial Auction
# ---------------------------------------------------------------------------


def run_combinatorial(
    cells: List[CellBidder],
    resources: List[ResourceBundle],
    seed: Optional[int] = None,
) -> AuctionResults:
    """Combinatorial auction: cells bid on bundles of resources."""
    rng = random.Random(seed if seed is not None else 42)

    # Generate bundles: each cell wants 1-3 closest resources
    bundle_bids: List[dict] = []  # {cell_idx, bundle: [resource_ids], amount}

    for cell in cells:
        ranked = sorted(
            range(len(resources)),
            key=lambda ri: _euclidean(cell.centroid, resources[ri].location),
        )
        # Bid on bundles of size 1 and size 2
        if len(ranked) >= 1:
            r0 = resources[ranked[0]]
            bid_single = cell.budget * rng.uniform(0.7, 1.0)
            bundle_bids.append({
                "cell_idx": cell.cell_idx,
                "bundle": [r0.resource_id],
                "amount": bid_single,
            })
        if len(ranked) >= 2:
            r0 = resources[ranked[0]]
            r1 = resources[ranked[1]]
            # Complementarity: bundle worth more than sum of parts
            bid_bundle = cell.budget * 1.3 * rng.uniform(0.7, 1.0)
            bundle_bids.append({
                "cell_idx": cell.cell_idx,
                "bundle": [r0.resource_id, r1.resource_id],
                "amount": bid_bundle,
            })

    # Greedy allocation: sort bids by amount/bundle_size (value density)
    for b in bundle_bids:
        b["density"] = b["amount"] / len(b["bundle"])
    bundle_bids.sort(key=lambda b: b["density"], reverse=True)

    allocated_resources = set()
    allocated_cells = set()
    winners = []
    total_revenue = 0.0

    for bid in bundle_bids:
        cidx = bid["cell_idx"]
        if cidx in allocated_cells:
            continue
        bundle = bid["bundle"]
        if any(rid in allocated_resources for rid in bundle):
            continue
        # Allocate
        for rid in bundle:
            allocated_resources.add(rid)
        allocated_cells.add(cidx)
        winners.append({
            "cell_idx": cidx,
            "bundle": bundle,
            "price_paid": round(bid["amount"], 2),
        })
        total_revenue += bid["amount"]
        cells[cidx].allocations.extend(bundle)
        cells[cidx].utility += sum(
            r.quantity for r in resources if r.resource_id in bundle
        ) / max(cells[cidx].demand, 0.01)

    efficiency = len(allocated_resources) / max(len(resources), 1)
    return AuctionResults(
        mechanism="combinatorial",
        winners=winners,
        total_revenue=round(total_revenue, 2),
        efficiency=efficiency,
    )


# ---------------------------------------------------------------------------
# Engine 6: Iterative Ascending Auction
# ---------------------------------------------------------------------------


def run_ascending(
    cells: List[CellBidder],
    resources: List[ResourceBundle],
    max_rounds: int = 20,
    increment: float = 0.1,
    seed: Optional[int] = None,
) -> AuctionResults:
    """English ascending auction: prices rise each round until market clears."""
    rng = random.Random(seed if seed is not None else 42)

    current_prices = {r.resource_id: r.base_price for r in resources}
    rounds: List[AuctionRound] = []
    final_winners: List[dict] = []

    for round_num in range(1, max_rounds + 1):
        round_bids = []
        active_bidders: Dict[str, List[Tuple[int, float]]] = {
            r.resource_id: [] for r in resources
        }

        for cell in cells:
            # Cell bids on resources where current price < willingness to pay
            for ri, r in enumerate(resources):
                wtp = cell.budget * (1.0 / (1 + _euclidean(cell.centroid, r.location) / 500))
                if wtp >= current_prices[r.resource_id]:
                    bid_amount = current_prices[r.resource_id] * (1 + rng.uniform(0, increment))
                    active_bidders[r.resource_id].append((cell.cell_idx, bid_amount))
                    round_bids.append({
                        "cell_idx": cell.cell_idx,
                        "resource_id": r.resource_id,
                        "amount": round(bid_amount, 2),
                    })

        # Determine round winners and update prices
        round_winners = []
        new_prices = dict(current_prices)
        all_cleared = True

        for r in resources:
            bidders = active_bidders[r.resource_id]
            if len(bidders) <= 1:
                # Market cleared for this resource
                if bidders:
                    round_winners.append({
                        "cell_idx": bidders[0][0],
                        "resource_id": r.resource_id,
                        "price_paid": round(bidders[0][1], 2),
                    })
            else:
                all_cleared = False
                # Raise price
                max_bid = max(b[1] for b in bidders)
                new_prices[r.resource_id] = max_bid

        rounds.append(AuctionRound(
            round_num=round_num,
            bids=round_bids,
            winners=round_winners,
            clearing_prices=dict(new_prices),
        ))

        current_prices = new_prices

        if all_cleared:
            final_winners = round_winners
            break
    else:
        # Didn't fully clear -- assign to highest bidders
        allocated_cells = set()
        for r in resources:
            bidders = active_bidders.get(r.resource_id, [])
            bidders.sort(key=lambda x: x[1], reverse=True)
            for cidx, amount in bidders:
                if cidx not in allocated_cells:
                    final_winners.append({
                        "cell_idx": cidx,
                        "resource_id": r.resource_id,
                        "price_paid": round(amount, 2),
                    })
                    allocated_cells.add(cidx)
                    break

    total_revenue = sum(w["price_paid"] for w in final_winners)
    efficiency = len(final_winners) / max(len(resources), 1)

    # Record allocations
    for w in final_winners:
        cidx = w["cell_idx"]
        cells[cidx].allocations.append(w["resource_id"])
        r_qty = next((r.quantity for r in resources if r.resource_id == w["resource_id"]), 1.0)
        cells[cidx].utility += r_qty / max(cells[cidx].demand, 0.01)

    return AuctionResults(
        mechanism="ascending",
        winners=final_winners,
        total_revenue=round(total_revenue, 2),
        efficiency=efficiency,
        rounds=rounds,
    )


# ---------------------------------------------------------------------------
# Engine 7: Fairness Analyzer
# ---------------------------------------------------------------------------


def analyze_fairness(cells: List[CellBidder]) -> FairnessMetrics:
    """Analyze allocation fairness: Gini, envy-freeness, Pareto efficiency."""
    utilities = [c.utility for c in cells]
    n = len(utilities)

    # Gini coefficient
    if n == 0 or sum(utilities) == 0:
        gini = 0.0
    else:
        sorted_u = sorted(utilities)
        cumulative = 0.0
        total = sum(sorted_u)
        gini_sum = 0.0
        for i, u in enumerate(sorted_u):
            cumulative += u
            gini_sum += (2 * (i + 1) - n - 1) * u
        gini = gini_sum / (n * total) if total > 0 else 0.0
        gini = max(0.0, min(1.0, gini))

    # Envy-freeness: cell i envies cell j if i prefers j's allocation
    envy_pairs = []
    for i in range(n):
        for j in range(n):
            if i != j and utilities[j] > utilities[i] and cells[i].demand >= cells[j].demand:
                envy_pairs.append((i, j))
    envy_free = len(envy_pairs) == 0

    # Pareto efficiency: check if any reallocation could improve someone without hurting another
    # Simple heuristic: if all resources are allocated and no cell has zero utility while resources exist
    allocated_count = sum(1 for c in cells if c.utility > 0)
    pareto_efficient = allocated_count >= min(n, len(set(
        rid for c in cells for rid in c.allocations
    )))

    utilitarian = sum(utilities)
    egalitarian = min(utilities) if utilities else 0.0
    welfare_ratio = egalitarian / utilitarian if utilitarian > 0 else 0.0

    return FairnessMetrics(
        gini_coefficient=gini,
        envy_free=envy_free,
        envy_pairs=envy_pairs[:20],  # cap for readability
        pareto_efficient=pareto_efficient,
        utilitarian_welfare=utilitarian,
        egalitarian_welfare=egalitarian,
        welfare_ratio=welfare_ratio,
    )


# ---------------------------------------------------------------------------
# Efficiency Score
# ---------------------------------------------------------------------------


def _compute_efficiency_score(report: AuctionReport) -> float:
    """Composite score 0-100 from revenue, coverage, fairness, speed."""
    scores = []

    # Revenue ratio (across all mechanisms)
    max_possible = sum(c.budget for c in report.cells)
    total_rev = 0.0
    for mech in [report.sealed_bid, report.vickrey, report.combinatorial, report.ascending]:
        if mech:
            total_rev += mech.total_revenue
    rev_ratio = min(total_rev / max(max_possible, 1), 1.0)
    scores.append(rev_ratio * 25)

    # Allocation coverage
    alloc_effs = []
    for mech in [report.sealed_bid, report.vickrey, report.combinatorial, report.ascending]:
        if mech:
            alloc_effs.append(mech.efficiency)
    avg_eff = statistics.mean(alloc_effs) if alloc_effs else 0.0
    scores.append(avg_eff * 30)

    # Fairness (1 - gini)
    fairness_score = (1 - report.fairness.gini_coefficient) * 25
    scores.append(fairness_score)

    # Price discovery speed (ascending auction rounds)
    if report.ascending and report.ascending.rounds:
        speed = max(0, 1 - len(report.ascending.rounds) / 20)
    else:
        speed = 0.5
    scores.append(speed * 20)

    return min(100.0, sum(scores))


# ---------------------------------------------------------------------------
# Resource Generation
# ---------------------------------------------------------------------------


def _generate_resources(
    bounds: Tuple[float, float, float, float],
    n_resources: int,
    seed: Optional[int] = None,
) -> List[ResourceBundle]:
    """Generate spatially distributed resources within bounds."""
    rng = random.Random(seed if seed is not None else 42)
    south, north, west, east = bounds
    resources = []
    for i in range(n_resources):
        x = rng.uniform(west, east)
        y = rng.uniform(south, north)
        qty = rng.uniform(5, 50)
        price = rng.uniform(10, 80)
        resources.append(ResourceBundle(
            resource_id=f"R{i+1:03d}",
            quantity=round(qty, 1),
            base_price=round(price, 2),
            location=(round(x, 2), round(y, 2)),
        ))
    return resources


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------


def run_auction(
    points: List[Tuple[float, float]],
    bounds: Tuple[float, float, float, float] = (0, 1000, 0, 1000),
    n_resources: int = 5,
    base_budget: float = 100.0,
    priorities: Optional[List[str]] = None,
    weights: Optional[List[float]] = None,
    mechanisms: Optional[List[str]] = None,
    seed: Optional[int] = None,
) -> AuctionReport:
    """Run full auction pipeline across all mechanisms.

    Parameters
    ----------
    points : list of (x, y) tuples -- Voronoi generator points
    bounds : (south, north, west, east)
    n_resources : number of resources to generate
    base_budget : base bidding budget per cell
    priorities : per-cell priority override
    weights : per-cell demand weight override
    mechanisms : list of mechanisms to run (default: all)
    seed : random seed for reproducibility
    """
    if mechanisms is None:
        mechanisms = ["sealed_bid", "vickrey", "combinatorial", "ascending"]

    # Assess demand
    demands = assess_demand(points, bounds, weights)

    # Calculate budgets
    budgets = calculate_budgets(demands, priorities, base_budget)

    # Build cell bidders
    cells_voronoi = _voronoi_cells_simple(points, bounds)
    n = len(points)
    if priorities is None:
        sorted_d = sorted(demands)
        q75 = sorted_d[int(0.75 * n)] if n > 0 else 0.5
        q50 = sorted_d[int(0.50 * n)] if n > 0 else 0.3
        q25 = sorted_d[int(0.25 * n)] if n > 0 else 0.1
        auto_priorities = []
        for d in demands:
            if d >= q75:
                auto_priorities.append("critical")
            elif d >= q50:
                auto_priorities.append("high")
            elif d >= q25:
                auto_priorities.append("medium")
            else:
                auto_priorities.append("low")
    else:
        auto_priorities = priorities

    cells = []
    for i in range(n):
        area = _polygon_area(cells_voronoi[i])
        cent = _centroid(cells_voronoi[i])
        cells.append(CellBidder(
            cell_idx=i,
            centroid=cent,
            area=area,
            demand=demands[i],
            budget=budgets[i],
            priority=auto_priorities[i],
        ))

    # Generate resources
    resources = _generate_resources(bounds, n_resources, seed)

    # Run mechanisms (each on fresh cell copies to avoid contamination)
    import copy

    sealed_bid_result = None
    vickrey_result = None
    combinatorial_result = None
    ascending_result = None

    if "sealed_bid" in mechanisms:
        cells_copy = copy.deepcopy(cells)
        sealed_bid_result = run_sealed_bid(cells_copy, resources, seed)
        # Merge utilities back
        for c in cells_copy:
            cells[c.cell_idx].utility += c.utility
            cells[c.cell_idx].allocations.extend(c.allocations)
            cells[c.cell_idx].bids.extend(c.bids)

    if "vickrey" in mechanisms:
        cells_copy = copy.deepcopy(cells)
        # Reset utility for fair comparison
        for c in cells_copy:
            c.utility = 0.0
            c.allocations = []
        vickrey_result = run_vickrey(cells_copy, resources, seed)

    if "combinatorial" in mechanisms:
        cells_copy = copy.deepcopy(cells)
        for c in cells_copy:
            c.utility = 0.0
            c.allocations = []
        combinatorial_result = run_combinatorial(cells_copy, resources, seed)

    if "ascending" in mechanisms:
        cells_copy = copy.deepcopy(cells)
        for c in cells_copy:
            c.utility = 0.0
            c.allocations = []
        ascending_result = run_ascending(cells_copy, resources, seed=seed)

    # Fairness analysis (use sealed_bid utilities as primary)
    fairness = analyze_fairness(cells)

    report = AuctionReport(
        cells=cells,
        resources=resources,
        sealed_bid=sealed_bid_result,
        vickrey=vickrey_result,
        combinatorial=combinatorial_result,
        ascending=ascending_result,
        fairness=fairness,
        efficiency_score=0.0,
        config={
            "n_points": len(points),
            "n_resources": n_resources,
            "base_budget": base_budget,
            "mechanisms": mechanisms,
            "seed": seed,
        },
    )
    report.efficiency_score = _compute_efficiency_score(report)
    return report


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------


def _generate_html(report: AuctionReport) -> str:
    """Generate self-contained 4-tab HTML dashboard."""
    cells_json = json.dumps([c.to_dict() for c in report.cells])
    resources_json = json.dumps([r.to_dict() for r in report.resources])
    fairness_json = json.dumps(report.fairness.to_dict())

    # Ascending rounds data
    ascending_rounds = []
    if report.ascending and report.ascending.rounds:
        for r in report.ascending.rounds:
            ascending_rounds.append(r.to_dict())
    rounds_json = json.dumps(ascending_rounds)

    score = report.efficiency_score
    score_color = "#22c55e" if score >= 70 else "#eab308" if score >= 40 else "#ef4444"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Spatial Auction Engine - Dashboard</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #0f172a; color: #e2e8f0; }}
.header {{ background: linear-gradient(135deg, #1e293b, #334155);
           padding: 24px 32px; border-bottom: 1px solid #475569; }}
.header h1 {{ font-size: 1.5rem; color: #f8fafc; }}
.header .score {{ font-size: 2.5rem; font-weight: 700; color: {score_color};
                  margin-top: 8px; }}
.tabs {{ display: flex; background: #1e293b; border-bottom: 1px solid #475569; }}
.tab {{ padding: 12px 24px; cursor: pointer; color: #94a3b8; font-weight: 500;
        border-bottom: 2px solid transparent; transition: all 0.2s; }}
.tab:hover {{ color: #e2e8f0; }}
.tab.active {{ color: #38bdf8; border-bottom-color: #38bdf8; }}
.panel {{ display: none; padding: 24px 32px; }}
.panel.active {{ display: block; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #334155; }}
th {{ color: #94a3b8; font-weight: 500; font-size: 0.85rem; text-transform: uppercase; }}
td {{ color: #e2e8f0; }}
.card {{ background: #1e293b; border-radius: 8px; padding: 16px; margin: 12px 0;
         border: 1px solid #334155; }}
.card h3 {{ color: #38bdf8; margin-bottom: 8px; font-size: 1rem; }}
.metric {{ display: inline-block; margin: 8px 16px 8px 0; }}
.metric .value {{ font-size: 1.3rem; font-weight: 700; color: #f8fafc; }}
.metric .label {{ font-size: 0.75rem; color: #94a3b8; }}
.bar-chart {{ margin: 12px 0; }}
.bar-row {{ display: flex; align-items: center; margin: 4px 0; }}
.bar-label {{ width: 60px; font-size: 0.8rem; color: #94a3b8; }}
.bar-bg {{ flex: 1; height: 20px; background: #334155; border-radius: 4px; overflow: hidden; }}
.bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
.envy-matrix {{ display: grid; gap: 2px; margin: 12px 0; }}
.envy-cell {{ width: 24px; height: 24px; display: flex; align-items: center;
              justify-content: center; font-size: 0.7rem; border-radius: 3px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Spatial Auction Engine - Dashboard</h1>
  <div class="score">{score:.1f}/100 Efficiency Score</div>
  <p style="color:#94a3b8;margin-top:4px">{len(report.cells)} cells competing for {len(report.resources)} resources</p>
</div>
<div class="tabs">
  <div class="tab active" onclick="showTab(0)">Overview</div>
  <div class="tab" onclick="showTab(1)">Bidding Analysis</div>
  <div class="tab" onclick="showTab(2)">Price Discovery</div>
  <div class="tab" onclick="showTab(3)">Fairness</div>
</div>

<div class="panel active" id="panel0">
  <div class="card">
    <h3>Auction Summary</h3>
    <div class="metric"><div class="value">{report.efficiency_score:.1f}</div><div class="label">Efficiency Score</div></div>
    <div class="metric"><div class="value">{report.fairness.gini_coefficient:.3f}</div><div class="label">Gini Coefficient</div></div>
    <div class="metric"><div class="value">{'Yes' if report.fairness.envy_free else 'No'}</div><div class="label">Envy-Free</div></div>
    <div class="metric"><div class="value">{'Yes' if report.fairness.pareto_efficient else 'No'}</div><div class="label">Pareto Efficient</div></div>
  </div>
  <div class="card">
    <h3>Mechanism Comparison</h3>
    <table>
      <tr><th>Mechanism</th><th>Revenue</th><th>Efficiency</th><th>Winners</th></tr>
      {"".join(f'<tr><td>{m.mechanism}</td><td>{m.total_revenue:.2f}</td><td>{m.efficiency:.1%}</td><td>{len(m.winners)}</td></tr>' for m in [report.sealed_bid, report.vickrey, report.combinatorial, report.ascending] if m)}
    </table>
  </div>
  <div class="card">
    <h3>Resources</h3>
    <table>
      <tr><th>ID</th><th>Quantity</th><th>Base Price</th><th>Location</th></tr>
      {"".join(f'<tr><td>{r.resource_id}</td><td>{r.quantity}</td><td>{r.base_price}</td><td>({r.location[0]:.0f}, {r.location[1]:.0f})</td></tr>' for r in report.resources)}
    </table>
  </div>
</div>

<div class="panel" id="panel1">
  <div class="card">
    <h3>Cell Bidders</h3>
    <table>
      <tr><th>Cell</th><th>Priority</th><th>Demand</th><th>Budget</th><th>Bids</th><th>Allocations</th><th>Utility</th></tr>
      {"".join(f'<tr><td>{c.cell_idx}</td><td>{c.priority}</td><td>{c.demand:.3f}</td><td>{c.budget:.1f}</td><td>{len(c.bids)}</td><td>{len(c.allocations)}</td><td>{c.utility:.3f}</td></tr>' for c in report.cells)}
    </table>
  </div>
  <div class="card">
    <h3>Budget Utilization</h3>
    <div class="bar-chart">
      {"".join(f'<div class="bar-row"><div class="bar-label">Cell {c.cell_idx}</div><div class="bar-bg"><div class="bar-fill" style="width:{min(100, c.utility / max(c.demand, 0.01) * 20):.0f}%;background:{"#22c55e" if c.utility > 0 else "#ef4444"}"></div></div></div>' for c in report.cells)}
    </div>
  </div>
</div>

<div class="panel" id="panel2">
  <div class="card">
    <h3>Ascending Auction -- Price Discovery</h3>
    <p style="color:#94a3b8">Round-by-round clearing prices</p>
    <div id="priceChart"></div>
    <table>
      <tr><th>Round</th><th>Bids</th><th>Winners</th><th>Avg Price</th></tr>
      {"".join(f'<tr><td>{r.round_num}</td><td>{len(r.bids)}</td><td>{len(r.winners)}</td><td>{sum(r.clearing_prices.values()) / max(len(r.clearing_prices), 1):.2f}</td></tr>' for r in (report.ascending.rounds if report.ascending else []))}
    </table>
  </div>
</div>

<div class="panel" id="panel3">
  <div class="card">
    <h3>Fairness Metrics</h3>
    <div class="metric"><div class="value">{report.fairness.gini_coefficient:.4f}</div><div class="label">Gini Coefficient</div></div>
    <div class="metric"><div class="value">{report.fairness.utilitarian_welfare:.3f}</div><div class="label">Utilitarian Welfare</div></div>
    <div class="metric"><div class="value">{report.fairness.egalitarian_welfare:.3f}</div><div class="label">Egalitarian Welfare</div></div>
    <div class="metric"><div class="value">{report.fairness.welfare_ratio:.4f}</div><div class="label">Welfare Ratio (Egal/Util)</div></div>
  </div>
  <div class="card">
    <h3>Envy Analysis</h3>
    <p style="color:#94a3b8">{'No envy detected -- allocation is envy-free! Yes' if report.fairness.envy_free else f'{len(report.fairness.envy_pairs)} envy pairs detected'}</p>
    {"" if report.fairness.envy_free else "<table><tr><th>Envious Cell</th><th>Envied Cell</th></tr>" + "".join(f"<tr><td>{p[0]}</td><td>{p[1]}</td></tr>" for p in report.fairness.envy_pairs[:10]) + "</table>"}
  </div>
  <div class="card">
    <h3>Utility Distribution (Lorenz Approximation)</h3>
    <div class="bar-chart">
      {"".join(f'<div class="bar-row"><div class="bar-label">Cell {c.cell_idx}</div><div class="bar-bg"><div class="bar-fill" style="width:{min(100, c.utility * 20):.0f}%;background:#8b5cf6"></div></div></div>' for c in sorted(report.cells, key=lambda x: x.utility))}
    </div>
  </div>
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
    parser = argparse.ArgumentParser(description="Spatial Auction Engine")
    parser.add_argument("--demo", action="store_true", help="Run with demo data")
    parser.add_argument("--n-resources", type=int, default=5)
    parser.add_argument("--base-budget", type=float, default=100.0)
    parser.add_argument("--mechanism", choices=["sealed_bid", "vickrey", "combinatorial", "ascending", "all"],
                        default="all")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", dest="json_path", help="Output JSON path")
    parser.add_argument("--html", dest="html_path", help="Output HTML path")
    args = parser.parse_args()

    # Demo points
    points = [
        (150, 200), (400, 350), (700, 150), (250, 700), (600, 600),
        (100, 500), (800, 400), (500, 800), (350, 100), (900, 900),
    ]
    bounds = (0, 1000, 0, 1000)

    mechanisms = None
    if args.mechanism != "all":
        mechanisms = [args.mechanism]

    report = run_auction(
        points, bounds=bounds,
        n_resources=args.n_resources,
        base_budget=args.base_budget,
        mechanisms=mechanisms,
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
