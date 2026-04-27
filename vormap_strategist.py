#!/usr/bin/env python3
"""vormap_strategist - Autonomous Spatial Strategy Planner

Analyzes an existing point distribution and generates a strategic plan to
achieve spatial goals by recommending point additions, removals, and moves.
Multiple strategy "advisors" evaluate the layout independently and a consensus
engine merges their recommendations into a ranked action plan.

Advisors:
    * **Coverage Advisor**   — identifies gaps in spatial coverage via grid sampling
    * **Balance Advisor**    — detects area imbalance and suggests equalization moves
    * **Separation Advisor** — finds crowded pairs and recommends removals/spreads
    * **Frontier Advisor**   — identifies boundary expansion opportunities
    * **Hotspot Advisor**    — detects dense clusters and suggests thinning

Goals:
    * ``coverage``    — maximize area coverage uniformity
    * ``balance``     — equalize Voronoi cell areas
    * ``spread``      — maximize minimum pairwise distance
    * ``frontier``    — expand into underutilized boundary regions
    * ``auto``        — all advisors vote, best strategy wins

Autonomous features:
    * **Auto-goal** (``--auto``) — advisors self-assess, worst dimension targeted
    * **Multi-round planning** — iterative what-if simulation of plan execution
    * **Confidence scoring** — each recommendation has advisor agreement level
    * **Impact forecast** — predicted metric improvement per action

CLI examples::

    python vormap_strategist.py --demo
    python vormap_strategist.py points.csv --goal coverage --output strategy.html
    python vormap_strategist.py points.csv --auto --rounds 3 --output plan.html
    python vormap_strategist.py points.csv --goal spread --json plan.json

Programmatic::

    from vormap_strategist import Strategist, export_html
    s = Strategist(points=[(0,0),(1,1),(2,0)], goal="auto")
    plan = s.plan(rounds=3)
    export_html(plan, "strategy.html")
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict
from typing import List, Tuple, Dict, Optional

from vormap_utils import euclidean as _dist

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _centroid(pts: List[Tuple[float,float]]) -> Tuple[float,float]:
    if not pts:
        return (0.0, 0.0)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    return (cx, cy)

def _bbox(pts: List[Tuple[float,float]]):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)

def _nn_dists(pts):
    """Nearest-neighbor distances for each point."""
    dists = []
    for i, p in enumerate(pts):
        mind = float('inf')
        for j, q in enumerate(pts):
            if i != j:
                d = _dist(p, q)
                if d < mind:
                    mind = d
        dists.append(mind if mind < float('inf') else 0.0)
    return dists

def _voronoi_areas(pts, xmin, ymin, xmax, ymax, grid_res=50):
    """Approximate Voronoi cell areas via grid assignment."""
    if not pts:
        return []
    w = xmax - xmin
    h = ymax - ymin
    if w == 0 or h == 0:
        return [1.0] * len(pts)
    cell_w = w / grid_res
    cell_h = h / grid_res
    counts = [0] * len(pts)
    total = 0
    for gi in range(grid_res):
        gx = xmin + (gi + 0.5) * cell_w
        for gj in range(grid_res):
            gy = ymin + (gj + 0.5) * cell_h
            best_idx = 0
            best_d = float('inf')
            for k, p in enumerate(pts):
                d = _dist((gx, gy), p)
                if d < best_d:
                    best_d = d
                    best_idx = k
            counts[best_idx] += 1
            total += 1
    total_area = w * h
    return [(c / total) * total_area if total > 0 else 0.0 for c in counts]

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class Metrics:
    """Compute spatial quality metrics for a point set."""
    def __init__(self, pts, margin=0.1):
        self.pts = pts
        n = len(pts)
        if n < 2:
            self.min_sep = 0.0
            self.mean_nn = 0.0
            self.cv_nn = 0.0
            self.cv_area = 0.0
            self.coverage_gaps = 0
            self.bbox = (0,0,1,1)
            self.areas = [1.0]
            return
        bx = _bbox(pts)
        w = bx[2]-bx[0]
        h = bx[3]-bx[1]
        pad = max(w, h) * margin
        self.bbox = (bx[0]-pad, bx[1]-pad, bx[2]+pad, bx[3]+pad)

        nn = _nn_dists(pts)
        self.min_sep = min(nn)
        self.mean_nn = sum(nn)/len(nn)
        std_nn = (sum((d - self.mean_nn)**2 for d in nn)/len(nn))**0.5
        self.cv_nn = std_nn / self.mean_nn if self.mean_nn > 0 else 0

        self.areas = _voronoi_areas(pts, *self.bbox)
        mean_a = sum(self.areas)/len(self.areas)
        std_a = (sum((a-mean_a)**2 for a in self.areas)/len(self.areas))**0.5
        self.cv_area = std_a / mean_a if mean_a > 0 else 0

        # Coverage gap count (grid cells far from any point)
        threshold = self.mean_nn * 1.5
        gaps = 0
        xmin, ymin, xmax, ymax = self.bbox
        res = 30
        cw = (xmax-xmin)/res
        ch = (ymax-ymin)/res
        for gi in range(res):
            gx = xmin + (gi+0.5)*cw
            for gj in range(res):
                gy = ymin + (gj+0.5)*ch
                if all(_dist((gx,gy), p) > threshold for p in pts):
                    gaps += 1
        self.coverage_gaps = gaps

    def score(self, goal: str) -> float:
        """Lower is better."""
        if goal == "coverage":
            return self.coverage_gaps
        elif goal == "balance":
            return self.cv_area
        elif goal == "spread":
            return -self.min_sep  # negative because we want to maximize
        elif goal == "frontier":
            return self.coverage_gaps + self.cv_area * 10
        else:
            return self.cv_area + self.cv_nn + self.coverage_gaps * 0.01

# ---------------------------------------------------------------------------
# Advisors
# ---------------------------------------------------------------------------

class Action:
    """A recommended spatial action."""
    def __init__(self, kind: str, point: Tuple[float,float],
                 target: Optional[Tuple[float,float]]=None,
                 reason: str="", impact: float=0.0, advisor: str=""):
        self.kind = kind  # "add", "remove", "move"
        self.point = point
        self.target = target  # destination for moves
        self.reason = reason
        self.impact = impact  # predicted improvement (0-1)
        self.advisor = advisor
        self.confidence = 0.0  # set by consensus

    def to_dict(self):
        d = {"kind": self.kind, "point": list(self.point),
             "reason": self.reason, "impact": round(self.impact, 3),
             "advisor": self.advisor, "confidence": round(self.confidence, 3)}
        if self.target:
            d["target"] = list(self.target)
        return d


def _coverage_advisor(pts, bbox, mean_nn) -> List[Action]:
    """Find coverage gaps and recommend additions."""
    actions = []
    xmin, ymin, xmax, ymax = bbox
    threshold = mean_nn * 1.5
    res = 25
    cw = (xmax-xmin)/res
    ch = (ymax-ymin)/res
    gap_cells = []
    for gi in range(res):
        gx = xmin + (gi+0.5)*cw
        for gj in range(res):
            gy = ymin + (gj+0.5)*ch
            min_d = min(_dist((gx,gy), p) for p in pts)
            if min_d > threshold:
                gap_cells.append(((gx, gy), min_d))
    gap_cells.sort(key=lambda x: -x[1])
    for (gx, gy), d in gap_cells[:5]:
        impact = min(1.0, d / (threshold * 2))
        actions.append(Action("add", (round(gx,3), round(gy,3)),
                              reason=f"Coverage gap (nearest point {d:.2f} away)",
                              impact=impact, advisor="coverage"))
    return actions


def _balance_advisor(pts, areas, bbox) -> List[Action]:
    """Find area imbalance and recommend moves toward large cells."""
    actions = []
    mean_a = sum(areas)/len(areas) if areas else 0
    if mean_a == 0:
        return actions
    indexed = sorted(enumerate(areas), key=lambda x: -x[1])
    for idx, area in indexed[:3]:
        if area > mean_a * 1.5:
            # Suggest adding a point near this oversized cell
            p = pts[idx]
            # Offset toward cell centroid approximation
            xmin, ymin, xmax, ymax = bbox
            cx = (p[0] + (xmin+xmax)/2) / 2
            cy = (p[1] + (ymin+ymax)/2) / 2
            offset_x = (cx - p[0]) * 0.3
            offset_y = (cy - p[1]) * 0.3
            new_pt = (round(p[0]+offset_x, 3), round(p[1]+offset_y, 3))
            impact = min(1.0, (area/mean_a - 1) / 3)
            actions.append(Action("add", new_pt,
                                  reason=f"Cell {idx} area {area:.2f} is {area/mean_a:.1f}x mean",
                                  impact=impact, advisor="balance"))
    return actions


def _separation_advisor(pts) -> List[Action]:
    """Find crowded pairs and recommend spreading or removal."""
    actions = []
    if len(pts) < 3:
        return actions
    nn = _nn_dists(pts)
    mean_nn = sum(nn)/len(nn)
    threshold = mean_nn * 0.4
    seen = set()
    pairs = []
    for i in range(len(pts)):
        for j in range(i+1, len(pts)):
            d = _dist(pts[i], pts[j])
            if d < threshold:
                pairs.append((i, j, d))
    pairs.sort(key=lambda x: x[2])
    for i, j, d in pairs[:3]:
        if i in seen or j in seen:
            continue
        seen.add(j)
        # Move j away from i
        dx = pts[j][0] - pts[i][0]
        dy = pts[j][1] - pts[i][1]
        norm = max(_dist(pts[i], pts[j]), 0.001)
        target = (round(pts[j][0] + dx/norm * mean_nn * 0.5, 3),
                  round(pts[j][1] + dy/norm * mean_nn * 0.5, 3))
        impact = min(1.0, (threshold - d) / threshold)
        actions.append(Action("move", pts[j], target=target,
                              reason=f"Points {i},{j} only {d:.3f} apart (threshold {threshold:.3f})",
                              impact=impact, advisor="separation"))
    return actions


def _frontier_advisor(pts, bbox) -> List[Action]:
    """Identify boundary expansion opportunities."""
    actions = []
    xmin, ymin, xmax, ymax = bbox
    cx, cy = _centroid(pts)
    # Check 8 compass directions for sparse frontiers
    directions = [("N", 0, 1), ("NE", 1, 1), ("E", 1, 0), ("SE", 1, -1),
                  ("S", 0, -1), ("SW", -1, -1), ("W", -1, 0), ("NW", -1, 1)]
    w = xmax - xmin
    h = ymax - ymin
    radius = max(w, h) * 0.4
    for name, dx, dy in directions:
        probe = (cx + dx * radius, cy + dy * radius)
        if probe[0] < xmin or probe[0] > xmax or probe[1] < ymin or probe[1] > ymax:
            continue
        min_d = min(_dist(probe, p) for p in pts)
        nn_mean = sum(_nn_dists(pts)) / len(pts)
        if min_d > nn_mean * 1.2:
            impact = min(1.0, min_d / (nn_mean * 2))
            actions.append(Action("add", (round(probe[0],3), round(probe[1],3)),
                                  reason=f"Frontier gap in {name} direction ({min_d:.2f} from nearest)",
                                  impact=impact, advisor="frontier"))
    actions.sort(key=lambda a: -a.impact)
    return actions[:3]


def _hotspot_advisor(pts) -> List[Action]:
    """Detect dense clusters and suggest thinning."""
    actions = []
    if len(pts) < 5:
        return actions
    nn = _nn_dists(pts)
    mean_nn = sum(nn)/len(nn)
    # Count neighbors within mean_nn for each point
    density = []
    for i, p in enumerate(pts):
        count = sum(1 for j, q in enumerate(pts) if i != j and _dist(p,q) < mean_nn)
        density.append((i, count))
    density.sort(key=lambda x: -x[1])
    seen = set()
    for idx, count in density[:3]:
        if count > 3 and idx not in seen:
            seen.add(idx)
            impact = min(1.0, (count - 3) / 5)
            actions.append(Action("remove", pts[idx],
                                  reason=f"Point {idx} in dense hotspot ({count} neighbors within {mean_nn:.3f})",
                                  impact=impact, advisor="hotspot"))
    return actions

# ---------------------------------------------------------------------------
# Strategist
# ---------------------------------------------------------------------------

class Strategist:
    """Autonomous spatial strategy planner with multi-advisor consensus."""

    GOALS = ("coverage", "balance", "spread", "frontier", "auto")

    def __init__(self, points: List[Tuple[float,float]], goal: str = "auto"):
        if goal not in self.GOALS:
            raise ValueError(f"Unknown goal '{goal}'. Choose from {self.GOALS}")
        self.original_pts = list(points)
        self.goal = goal

    def _run_advisors(self, pts, m):
        all_actions = []
        all_actions.extend(_coverage_advisor(pts, m.bbox, m.mean_nn))
        all_actions.extend(_balance_advisor(pts, m.areas, m.bbox))
        all_actions.extend(_separation_advisor(pts))
        all_actions.extend(_frontier_advisor(pts, m.bbox))
        all_actions.extend(_hotspot_advisor(pts))
        return all_actions

    def _consensus(self, actions: List[Action]) -> List[Action]:
        """Merge and rank actions by spatial proximity and advisor agreement."""
        if not actions:
            return []
        # Group nearby actions (within tolerance)
        groups = []
        used = [False]*len(actions)
        tolerance = 0.5  # merge radius
        for i, a in enumerate(actions):
            if used[i]:
                continue
            group = [a]
            used[i] = True
            for j in range(i+1, len(actions)):
                if used[j]:
                    continue
                if a.kind == actions[j].kind and _dist(a.point, actions[j].point) < tolerance:
                    group.append(actions[j])
                    used[j] = True
            groups.append(group)

        merged = []
        for group in groups:
            best = max(group, key=lambda a: a.impact)
            advisors = set(a.advisor for a in group)
            best.confidence = len(advisors) / 5.0  # 5 total advisors
            best.impact = max(a.impact for a in group)
            if len(advisors) > 1:
                best.reason += f" (agreed by: {', '.join(sorted(advisors))})"
            merged.append(best)

        merged.sort(key=lambda a: -(a.impact * 0.6 + a.confidence * 0.4))
        return merged

    def _simulate_action(self, pts, action):
        """Apply an action to a point list and return new list."""
        pts = list(pts)
        if action.kind == "add":
            pts.append(action.point)
        elif action.kind == "remove":
            # Remove closest point
            if pts:
                closest = min(range(len(pts)), key=lambda i: _dist(pts[i], action.point))
                pts.pop(closest)
        elif action.kind == "move" and action.target:
            closest = min(range(len(pts)), key=lambda i: _dist(pts[i], action.point))
            pts[closest] = action.target
        return pts

    def plan(self, rounds: int = 1, max_actions_per_round: int = 5) -> Dict:
        """Generate a multi-round strategic plan."""
        pts = list(self.original_pts)
        initial_m = Metrics(pts)

        # Auto-goal selection
        if self.goal == "auto":
            scores = {g: Metrics(pts).score(g) for g in ("coverage","balance","spread","frontier")}
            self.goal = max(scores, key=lambda g: scores[g])
            auto_selected = self.goal
        else:
            auto_selected = None

        initial_score = initial_m.score(self.goal)
        plan_rounds = []

        for r in range(rounds):
            m = Metrics(pts)
            actions = self._run_advisors(pts, m)
            ranked = self._consensus(actions)[:max_actions_per_round]

            # Simulate each action for impact forecast
            round_actions = []
            for act in ranked:
                sim_pts = self._simulate_action(pts, act)
                if len(sim_pts) >= 2:
                    sim_m = Metrics(sim_pts)
                    before = m.score(self.goal)
                    after = sim_m.score(self.goal)
                    if before != 0:
                        act.impact = max(0, (before - after) / abs(before))
                    round_actions.append(act)

            # Apply top actions for next round
            applied = []
            for act in round_actions[:3]:
                pts = self._simulate_action(pts, act)
                applied.append(act)

            plan_rounds.append({
                "round": r + 1,
                "metrics_before": {
                    "coverage_gaps": m.coverage_gaps,
                    "cv_area": round(m.cv_area, 4),
                    "cv_nn": round(m.cv_nn, 4),
                    "min_separation": round(m.min_sep, 4),
                    "point_count": len(self.original_pts) if r == 0 else plan_rounds[r-1]["metrics_after"]["point_count"] if r > 0 else len(pts)
                },
                "actions": [a.to_dict() for a in round_actions],
                "applied": [a.to_dict() for a in applied],
                "metrics_after": {
                    "coverage_gaps": Metrics(pts).coverage_gaps,
                    "cv_area": round(Metrics(pts).cv_area, 4),
                    "cv_nn": round(Metrics(pts).cv_nn, 4),
                    "min_separation": round(Metrics(pts).min_sep, 4),
                    "point_count": len(pts)
                }
            })

        final_m = Metrics(pts)
        final_score = final_m.score(self.goal)
        improvement = ((initial_score - final_score) / abs(initial_score) * 100) if initial_score != 0 else 0

        return {
            "goal": self.goal,
            "auto_selected": auto_selected,
            "original_points": [list(p) for p in self.original_pts],
            "final_points": [list(p) for p in pts],
            "rounds": plan_rounds,
            "summary": {
                "initial_score": round(initial_score, 4),
                "final_score": round(final_score, 4),
                "improvement_pct": round(improvement, 1),
                "total_actions_recommended": sum(len(r["actions"]) for r in plan_rounds),
                "total_actions_applied": sum(len(r["applied"]) for r in plan_rounds),
                "points_added": len(pts) - len(self.original_pts) if len(pts) > len(self.original_pts) else 0,
                "points_removed": len(self.original_pts) - len(pts) if len(pts) < len(self.original_pts) else 0
            },
            "recommendations": _generate_recommendations(plan_rounds, self.goal, improvement)
        }


def _generate_recommendations(rounds, goal, improvement):
    recs = []
    if improvement > 20:
        recs.append("[OK] Significant improvement achievable - consider applying the top actions")
    elif improvement > 5:
        recs.append("[..] Moderate improvement possible - review individual action impacts")
    else:
        recs.append("[==] Layout is already well-optimized for this goal")

    all_actions = []
    for r in rounds:
        all_actions.extend(r["actions"])
    kinds = defaultdict(int)
    for a in all_actions:
        kinds[a["kind"]] += 1
    if kinds.get("add", 0) > kinds.get("remove", 0) * 2:
        recs.append("[+] Strategy is expansion-heavy - consider if more points are desirable")
    if kinds.get("remove", 0) > 2:
        recs.append("[-] Multiple removals suggested - distribution may be over-dense in spots")
    if kinds.get("move", 0) > 2:
        recs.append("[~] Several moves recommended - points are positioned but misaligned")

    high_conf = [a for a in all_actions if a.get("confidence", 0) >= 0.4]
    if high_conf:
        recs.append(f"[*] {len(high_conf)} action(s) have multi-advisor consensus - highest confidence")

    if goal == "coverage":
        recs.append("[i] Tip: Run with --rounds 3+ to iteratively fill remaining gaps")
    elif goal == "spread":
        recs.append("[i] Tip: Combine with vormap_relax for Lloyd relaxation after applying moves")
    elif goal == "balance":
        recs.append("[i] Tip: Use vormap_balance for detailed Gini analysis after changes")
    return recs


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def export_html(plan: Dict, path: str):
    """Generate an interactive HTML strategy report."""
    orig = plan["original_points"]
    final = plan["final_points"]
    rounds = plan["rounds"]
    summary = plan["summary"]
    recs = plan["recommendations"]

    all_pts = orig + final
    if all_pts:
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        pad = max(max(xs)-min(xs), max(ys)-min(ys)) * 0.1
        vx1, vy1 = min(xs)-pad, min(ys)-pad
        vx2, vy2 = max(xs)+pad, max(ys)+pad
    else:
        vx1, vy1, vx2, vy2 = 0, 0, 10, 10

    actions_json = json.dumps(plan, indent=2)

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Spatial Strategy Plan — {plan['goal']}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0a0a1a;color:#e0e0e0;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1a1a3e,#2d1b4e);padding:2rem;text-align:center}}
.header h1{{font-size:1.8rem;background:linear-gradient(90deg,#60a5fa,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.header .goal{{color:#a78bfa;font-size:1.1rem;margin-top:.5rem}}
.container{{max-width:1200px;margin:0 auto;padding:1.5rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:1.5rem;margin:1.5rem 0}}
.card{{background:#1a1a2e;border-radius:12px;padding:1.5rem;border:1px solid #333}}
.card h2{{font-size:1.1rem;color:#60a5fa;margin-bottom:1rem}}
.metric{{display:flex;justify-content:space-between;padding:.5rem 0;border-bottom:1px solid #222}}
.metric .label{{color:#888}}.metric .value{{color:#fff;font-weight:600}}
.metric .improved{{color:#34d399}}.metric .worsened{{color:#f87171}}
canvas{{width:100%;border-radius:8px;background:#111;cursor:crosshair}}
.action{{padding:.75rem;margin:.5rem 0;border-radius:8px;border-left:3px solid}}
.action.add{{border-color:#34d399;background:#34d39910}}
.action.remove{{border-color:#f87171;background:#f8717110}}
.action.move{{border-color:#60a5fa;background:#60a5fa10}}
.action .kind{{font-weight:700;text-transform:uppercase;font-size:.75rem}}
.action .kind.add{{color:#34d399}}.action .kind.remove{{color:#f87171}}.action .kind.move{{color:#60a5fa}}
.bar{{height:6px;border-radius:3px;background:#333;margin-top:.25rem}}
.bar-fill{{height:100%;border-radius:3px;background:linear-gradient(90deg,#60a5fa,#a78bfa)}}
.rec{{padding:.5rem .75rem;margin:.25rem 0;background:#ffffff08;border-radius:6px;font-size:.9rem}}
.tabs{{display:flex;gap:.5rem;margin-bottom:1rem}}.tab{{padding:.5rem 1rem;border-radius:6px;cursor:pointer;background:#222;color:#888;border:none;font-size:.85rem}}
.tab.active{{background:#60a5fa;color:#fff}}
.round-section{{display:none}}.round-section.active{{display:block}}
.summary-bar{{display:flex;align-items:center;gap:1rem;padding:1rem;background:#1a1a2e;border-radius:12px;margin:1rem 0}}
.summary-bar .big{{font-size:2rem;font-weight:700}}
.summary-bar .big.pos{{color:#34d399}}.summary-bar .big.neg{{color:#f87171}}.summary-bar .big.neutral{{color:#888}}
</style></head><body>
<div class="header">
<h1>🎯 Spatial Strategy Plan</h1>
<div class="goal">Goal: {plan['goal'].upper()}{' (auto-selected)' if plan.get('auto_selected') else ''}</div>
</div>
<div class="container">

<div class="summary-bar">
<div class="big {'pos' if summary['improvement_pct']>0 else 'neg' if summary['improvement_pct']<0 else 'neutral'}">{'+' if summary['improvement_pct']>0 else ''}{summary['improvement_pct']}%</div>
<div><div style="color:#888;font-size:.85rem">Predicted Improvement</div>
<div>{summary['total_actions_recommended']} recommended · {summary['total_actions_applied']} applied · {len(rounds)} round(s)</div></div>
</div>

<div class="grid">
<div class="card">
<h2>📊 Before vs After</h2>
{''.join(f'<div class="metric"><span class="label">{k.replace("_"," ").title()}</span><span class="value {_html_trend(rounds[0]["metrics_before"].get(k,0), rounds[-1]["metrics_after"].get(k,0), k)}">{rounds[0]["metrics_before"].get(k,"—")} → {rounds[-1]["metrics_after"].get(k,"—")}</span></div>' for k in ["coverage_gaps","cv_area","cv_nn","min_separation","point_count"])}
</div>
<div class="card">
<h2>📋 Recommendations</h2>
{''.join(f'<div class="rec">{r}</div>' for r in recs)}
</div>
</div>

<div class="card" style="margin:1.5rem 0">
<h2>🗺️ Strategy Map</h2>
<canvas id="mapCanvas" width="800" height="500"></canvas>
<div style="margin-top:.75rem;font-size:.8rem;color:#666">
<span style="color:#f87171">● Original</span> &nbsp;
<span style="color:#34d399">● Added</span> &nbsp;
<span style="color:#60a5fa">→ Moved</span> &nbsp;
<span style="color:#666">● Removed</span>
</div>
</div>

<div class="card" style="margin:1.5rem 0">
<h2>📋 Action Plan</h2>
<div class="tabs" id="roundTabs">
{''.join('<button class="tab' + (' active' if i==0 else '') + '" onclick="showRound(' + str(i) + ')">' + str(r['round']) + '. Round ' + str(r['round']) + '</button>' for i, r in enumerate(rounds))}
</div>
{''.join(_html_round(r, i) for i, r in enumerate(rounds))}
</div>

</div>

<script>
const plan = {actions_json};
const orig = plan.original_points;
const fin = plan.final_points;
const vx1={vx1},vy1={vy1},vx2={vx2},vy2={vy2};

function drawMap() {{
  const c = document.getElementById('mapCanvas');
  const ctx = c.getContext('2d');
  const W = c.width = c.offsetWidth;
  const H = c.height = 500;
  ctx.fillStyle='#111'; ctx.fillRect(0,0,W,H);

  function tx(x){{ return ((x-vx1)/(vx2-vx1))*W*0.9+W*0.05; }}
  function ty(y){{ return H - (((y-vy1)/(vy2-vy1))*H*0.9+H*0.05); }}

  // Draw original points
  orig.forEach(p => {{
    ctx.beginPath(); ctx.arc(tx(p[0]),ty(p[1]),4,0,Math.PI*2);
    ctx.fillStyle='#f87171'; ctx.fill();
  }});

  // Draw actions
  plan.rounds.forEach(r => {{
    r.applied.forEach(a => {{
      if(a.kind==='add'){{
        ctx.beginPath(); ctx.arc(tx(a.point[0]),ty(a.point[1]),6,0,Math.PI*2);
        ctx.fillStyle='#34d399'; ctx.fill();
        ctx.strokeStyle='#34d39966'; ctx.lineWidth=2;
        ctx.beginPath(); ctx.arc(tx(a.point[0]),ty(a.point[1]),12,0,Math.PI*2); ctx.stroke();
      }} else if(a.kind==='move' && a.target){{
        ctx.strokeStyle='#60a5fa'; ctx.lineWidth=2;
        ctx.beginPath(); ctx.moveTo(tx(a.point[0]),ty(a.point[1]));
        ctx.lineTo(tx(a.target[0]),ty(a.target[1])); ctx.stroke();
        ctx.beginPath(); ctx.arc(tx(a.target[0]),ty(a.target[1]),5,0,Math.PI*2);
        ctx.fillStyle='#60a5fa'; ctx.fill();
      }} else if(a.kind==='remove'){{
        ctx.strokeStyle='#666'; ctx.lineWidth=2;
        ctx.beginPath();
        ctx.moveTo(tx(a.point[0])-5,ty(a.point[1])-5);
        ctx.lineTo(tx(a.point[0])+5,ty(a.point[1])+5);
        ctx.moveTo(tx(a.point[0])+5,ty(a.point[1])-5);
        ctx.lineTo(tx(a.point[0])-5,ty(a.point[1])+5);
        ctx.stroke();
      }}
    }});
  }});
}}

function showRound(i) {{
  document.querySelectorAll('.round-section').forEach((el,j) => {{
    el.classList.toggle('active', j===i);
  }});
  document.querySelectorAll('.tab').forEach((el,j) => {{
    el.classList.toggle('active', j===i);
  }});
}}

drawMap();
window.addEventListener('resize', drawMap);
</script></body></html>"""

    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)


def _html_trend(before, after, key):
    if key in ("coverage_gaps", "cv_area", "cv_nn"):
        return "improved" if after < before else "worsened" if after > before else ""
    elif key == "min_separation":
        return "improved" if after > before else "worsened" if after < before else ""
    return ""


def _html_round(r, idx):
    actions_html = ""
    for a in r["actions"]:
        kind = a["kind"]
        impact_pct = int(a["impact"] * 100)
        conf_pct = int(a.get("confidence", 0) * 100)
        pt = f"({a['point'][0]:.2f}, {a['point'][1]:.2f})"
        target = f" → ({a['target'][0]:.2f}, {a['target'][1]:.2f})" if a.get("target") else ""
        actions_html += f"""<div class="action {kind}">
<div class="kind {kind}">{kind}{target and ' '}{pt}{target}</div>
<div style="font-size:.85rem;color:#aaa;margin:.25rem 0">{a['reason']}</div>
<div style="display:flex;gap:1rem;font-size:.8rem;color:#666">
<span>Impact: {impact_pct}%</span><span>Confidence: {conf_pct}%</span>
</div>
<div class="bar"><div class="bar-fill" style="width:{impact_pct}%"></div></div>
</div>"""
    active_cls = '  active' if idx == 0 else ''
    fallback = '<p style="color:#666">No actions for this round</p>'
    content = actions_html if actions_html else fallback
    return f'<div class="round-section{active_cls}" id="round{idx}">{content}</div>'


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo():
    random.seed(42)
    # Create an intentionally imbalanced distribution
    pts = []
    # Cluster in bottom-left
    for _ in range(8):
        pts.append((random.uniform(1, 3), random.uniform(1, 3)))
    # Sparse top-right
    pts.append((8, 8))
    pts.append((9, 7))
    # Gap in middle
    pts.append((5, 1))

    print("=" * 60)
    print("  VORMAP STRATEGIST — Autonomous Spatial Strategy Planner")
    print("=" * 60)
    print(f"\n  Points: {len(pts)}  |  Goal: auto  |  Rounds: 3\n")

    s = Strategist(pts, goal="auto")
    plan = s.plan(rounds=3)

    print(f"  Auto-selected goal: {plan['goal'].upper()}")
    print(f"  Predicted improvement: {plan['summary']['improvement_pct']}%")
    print(f"  Actions recommended: {plan['summary']['total_actions_recommended']}")
    print(f"  Actions applied: {plan['summary']['total_actions_applied']}")
    print()

    for r in plan["rounds"]:
        print(f"  -- Round {r['round']} --")
        for a in r["applied"]:
            kind_sym = {"add": "+", "remove": "-", "move": "→"}[a["kind"]]
            pt = f"({a['point'][0]:.2f}, {a['point'][1]:.2f})"
            target = f" → ({a['target'][0]:.2f}, {a['target'][1]:.2f})" if a.get("target") else ""
            print(f"    [{kind_sym}] {a['kind'].upper():6s} {pt}{target}")
            print(f"        {a['reason']}")
        print()

    print("  Recommendations:")
    for r in plan["recommendations"]:
        print(f"    {r}")
    print()

    out = os.path.join(os.path.dirname(__file__) or ".", "strategist_demo.html")
    export_html(plan, out)
    print(f"  Report: {out}")
    print()

    # JSON export
    json_out = os.path.join(os.path.dirname(__file__) or ".", "strategist_demo.json")
    with open(json_out, 'w') as f:
        json.dump(plan, f, indent=2)
    print(f"  JSON:   {json_out}")


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------

def load_csv(path: str) -> List[Tuple[float, float]]:
    pts = []
    with open(path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) >= 2:
                try:
                    pts.append((float(row[0]), float(row[1])))
                except ValueError:
                    continue
    return pts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Spatial Strategy Planner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python vormap_strategist.py --demo\n"
               "  python vormap_strategist.py points.csv --goal coverage --output plan.html\n"
               "  python vormap_strategist.py points.csv --auto --rounds 3\n"
    )
    parser.add_argument("input", nargs="?", help="CSV file with x,y points")
    parser.add_argument("--goal", choices=Strategist.GOALS, default="auto")
    parser.add_argument("--auto", action="store_true", help="Auto-select best goal")
    parser.add_argument("--rounds", type=int, default=1, help="Planning rounds (default 1)")
    parser.add_argument("--max-actions", type=int, default=5, help="Max actions per round")
    parser.add_argument("--output", "-o", help="HTML report output path")
    parser.add_argument("--json", help="JSON output path")
    parser.add_argument("--demo", action="store_true", help="Run interactive demo")

    args = parser.parse_args()

    if args.demo:
        _demo()
        return

    if not args.input:
        parser.print_help()
        sys.exit(1)

    pts = load_csv(args.input)
    if len(pts) < 3:
        print(f"Error: Need at least 3 points, got {len(pts)}", file=sys.stderr)
        sys.exit(1)

    goal = "auto" if args.auto else args.goal
    s = Strategist(pts, goal=goal)
    plan = s.plan(rounds=args.rounds, max_actions_per_round=args.max_actions)

    # Console output
    print(f"\nGoal: {plan['goal']}{' (auto)' if plan.get('auto_selected') else ''}")
    print(f"Improvement: {plan['summary']['improvement_pct']}%")
    print(f"Actions: {plan['summary']['total_actions_applied']} applied / {plan['summary']['total_actions_recommended']} recommended\n")

    for r in plan["rounds"]:
        print(f"Round {r['round']}:")
        for a in r["applied"]:
            print(f"  [{a['kind'].upper()}] ({a['point'][0]:.2f}, {a['point'][1]:.2f})"
                  + (f" → ({a['target'][0]:.2f}, {a['target'][1]:.2f})" if a.get('target') else ""))
            print(f"    {a['reason']}")

    if args.output:
        export_html(plan, args.output)
        print(f"\nHTML report: {args.output}")

    if args.json:
        with open(args.json, 'w') as f:
            json.dump(plan, f, indent=2)
        print(f"JSON: {args.json}")


if __name__ == "__main__":
    main()
