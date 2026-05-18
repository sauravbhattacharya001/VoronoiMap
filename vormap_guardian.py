#!/usr/bin/env python3
"""Spatial Constraint Guardian — autonomous constraint enforcement & auto-repair.

Users define spatial rules (constraints) about their point layouts, and the
guardian validates, reports violations, and auto-repairs layouts to achieve
compliance.  This is an *agentic* capability — the guardian autonomously
monitors spatial quality and takes corrective action.

Eight constraint types:

- **MinSpacing** — no two points closer than a threshold distance.
- **MaxSpacing** — no point's nearest-neighbor further than a threshold.
- **ExclusionZone** — no points allowed within circular exclusion zones.
- **InclusionZone** — all points must be within a rectangular inclusion boundary.
- **DensityCap** — maximum point density per grid cell.
- **DensityFloor** — minimum point density per grid cell (flags empty regions).
- **SymmetryAxis** — approximate mirror symmetry about a vertical or horizontal axis.
- **CountBounds** — total point count must be within [min, max].

Each constraint produces violations with severity (info/warning/critical),
and the guardian iteratively auto-repairs until compliance is reached or
a max-iteration limit is hit.

Usage (Python API)::

    from vormap_guardian import Guardian, MinSpacing, ExclusionZone, guard

    g = Guardian(points=[(0,0),(5,5),(10,10)])
    g.add_constraint(MinSpacing(threshold=8.0))
    report = g.validate()
    print(report.compliance_score)
    for v in report.violations:
        print(f"[{v.severity}] {v.constraint}: {v.message}")

    # Auto-repair
    report = g.auto_repair(max_iterations=5)
    print(f"Repaired {report.points_modified} points in {report.iterations} iterations")

    # Convenience
    report = guard("points.txt", constraints=[MinSpacing(6.0)], auto_repair=True)

CLI::

    python vormap_guardian.py points.txt --min-spacing 10
    python vormap_guardian.py points.txt --max-spacing 100 --density-cap 5
    python vormap_guardian.py points.txt --exclusion-zone 500,500,50
    python vormap_guardian.py points.txt --inclusion-zone 0,0,1000,1000
    python vormap_guardian.py points.txt --auto-repair --output repaired.txt
    python vormap_guardian.py points.txt --json report.json
    python vormap_guardian.py points.txt --html report.html
    python vormap_guardian.py --demo
"""

import json
import math
import random
import sys
from collections import namedtuple

import vormap
from vormap_utils import euclidean as _dist

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

Violation = namedtuple(
    "Violation",
    ["constraint", "severity", "message", "affected_indices", "detail"],
)


class GuardianReport:
    """Result of a guardian validation or auto-repair run."""

    __slots__ = (
        "compliance_score",
        "constraints_checked",
        "final_count",
        "iterations",
        "original_count",
        "points",
        "points_modified",
        "violations",
    )

    def __init__(self):
        self.violations: list = []
        self.compliance_score: float = 100.0
        self.points_modified: int = 0
        self.iterations: int = 0
        self.original_count: int = 0
        self.final_count: int = 0
        self.constraints_checked: int = 0
        self.points: list = []

    def summary(self) -> str:
        lines = [
            f"Guardian Report — compliance {self.compliance_score:.1f}/100",
            f"  Constraints checked : {self.constraints_checked}",
            f"  Violations found    : {len(self.violations)}",
            f"  Points modified     : {self.points_modified}",
            f"  Iterations          : {self.iterations}",
            f"  Original point count: {self.original_count}",
            f"  Final point count   : {self.final_count}",
        ]
        if self.violations:
            lines.append("  Violations:")
            for v in self.violations[:20]:
                lines.append(f"    [{v.severity.upper()}] {v.constraint}: {v.message}")
            if len(self.violations) > 20:
                lines.append(f"    ... and {len(self.violations) - 20} more")
        return "\n".join(lines)

    def to_json(self, path=None):
        data = {
            "compliance_score": round(self.compliance_score, 2),
            "constraints_checked": self.constraints_checked,
            "violations_count": len(self.violations),
            "points_modified": self.points_modified,
            "iterations": self.iterations,
            "original_count": self.original_count,
            "final_count": self.final_count,
            "violations": [
                {
                    "constraint": v.constraint,
                    "severity": v.severity,
                    "message": v.message,
                    "affected_indices": v.affected_indices,
                    "detail": v.detail,
                }
                for v in self.violations
            ],
        }
        blob = json.dumps(data, indent=2)
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(blob)
        return blob

    def to_html(self, path):
        sev_counts = {"critical": 0, "warning": 0, "info": 0}
        for v in self.violations:
            sev_counts[v.severity] = sev_counts.get(v.severity, 0) + 1

        rows = []
        for v in self.violations:
            color = {"critical": "#e74c3c", "warning": "#f39c12", "info": "#3498db"}.get(
                v.severity, "#ccc"
            )
            rows.append(
                f"<tr><td style='color:{color};font-weight:bold'>{v.severity.upper()}</td>"
                f"<td>{_h(v.constraint)}</td><td>{_h(v.message)}</td>"
                f"<td>{len(v.affected_indices) if v.affected_indices else 0}</td></tr>"
            )

        gauge_color = (
            "#2ecc71" if self.compliance_score >= 80
            else "#f39c12" if self.compliance_score >= 50
            else "#e74c3c"
        )

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Guardian Report</title>
<style>
body{{background:#1a1a2e;color:#e0e0e0;font-family:'Segoe UI',sans-serif;margin:0;padding:20px}}
h1{{color:#00d4ff;text-align:center}}
.gauge{{width:180px;height:180px;border-radius:50%;margin:20px auto;display:flex;align-items:center;
  justify-content:center;font-size:2.5em;font-weight:bold;color:{gauge_color};
  border:8px solid {gauge_color};background:rgba(0,0,0,0.3)}}
.stats{{display:flex;justify-content:center;gap:40px;margin:20px 0}}
.stat{{text-align:center}}.stat .num{{font-size:1.8em;font-weight:bold}}
.stat .label{{font-size:0.85em;opacity:0.7}}
table{{width:100%;border-collapse:collapse;margin-top:20px}}
th{{background:#16213e;padding:10px;text-align:left;border-bottom:2px solid #0f3460}}
td{{padding:8px 10px;border-bottom:1px solid #222}}
tr:hover{{background:rgba(255,255,255,0.03)}}
.footer{{text-align:center;margin-top:30px;opacity:0.5;font-size:0.8em}}
</style></head><body>
<h1>🛡️ Spatial Constraint Guardian</h1>
<div class="gauge">{self.compliance_score:.0f}</div>
<div class="stats">
  <div class="stat"><div class="num">{self.constraints_checked}</div><div class="label">Constraints</div></div>
  <div class="stat"><div class="num" style="color:#e74c3c">{sev_counts.get('critical',0)}</div><div class="label">Critical</div></div>
  <div class="stat"><div class="num" style="color:#f39c12">{sev_counts.get('warning',0)}</div><div class="label">Warnings</div></div>
  <div class="stat"><div class="num" style="color:#3498db">{sev_counts.get('info',0)}</div><div class="label">Info</div></div>
  <div class="stat"><div class="num">{self.points_modified}</div><div class="label">Points Fixed</div></div>
</div>
{"<table><tr><th>Severity</th><th>Constraint</th><th>Message</th><th>Affected</th></tr>" + "".join(rows) + "</table>" if rows else "<p style='text-align:center;color:#2ecc71;font-size:1.3em'>✅ All constraints satisfied</p>"}
<div class="footer">VoronoiMap Guardian — autonomous spatial constraint enforcement</div>
</body></html>"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)


def _h(text):
    """Minimal HTML escape."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# Constraint base class
# ---------------------------------------------------------------------------


class Constraint:
    """Base class for spatial constraints."""

    name: str = "Constraint"
    severity: str = "warning"

    def check(self, points, bounds):
        """Return list of Violation for points that fail this constraint."""
        raise NotImplementedError

    def repair(self, points, bounds, violations):
        """Return modified points list with violations fixed."""
        return list(points)


# ---------------------------------------------------------------------------
# Concrete constraints
# ---------------------------------------------------------------------------


class MinSpacing(Constraint):
    """No two points closer than *threshold* distance."""

    name = "MinSpacing"

    def __init__(self, threshold, severity="warning"):
        self.threshold = float(threshold)
        self.severity = severity

    def check(self, points, bounds):
        violations = []
        n = len(points)
        for i in range(n):
            for j in range(i + 1, n):
                d = _dist(points[i], points[j])
                if d < self.threshold:
                    violations.append(
                        Violation(
                            self.name,
                            self.severity,
                            f"Points {i} and {j} are {d:.2f} apart (min {self.threshold:.2f})",
                            [i, j],
                            {"distance": d, "threshold": self.threshold},
                        )
                    )
        return violations

    def repair(self, points, bounds, violations):
        pts = [list(p) for p in points]
        moved = set()
        for v in violations:
            if not v.affected_indices or len(v.affected_indices) < 2:
                continue
            i, j = v.affected_indices[0], v.affected_indices[1]
            if j in moved:
                continue
            dx = pts[j][0] - pts[i][0]
            dy = pts[j][1] - pts[i][1]
            d = math.sqrt(dx * dx + dy * dy)
            if d < 1e-12:
                dx, dy = 1.0, 0.0
                d = 1.0
            needed = (self.threshold - d) / 2 + 0.1
            ux, uy = dx / d, dy / d
            pts[j][0] += ux * needed
            pts[j][1] += uy * needed
            pts[i][0] -= ux * needed
            pts[i][1] -= uy * needed
            moved.add(i)
            moved.add(j)
        return [tuple(p) for p in pts]


class MaxSpacing(Constraint):
    """No point's nearest-neighbor further than *threshold*."""

    name = "MaxSpacing"

    def __init__(self, threshold, severity="warning"):
        self.threshold = float(threshold)
        self.severity = severity

    def check(self, points, bounds):
        violations = []
        n = len(points)
        if n < 2:
            return violations
        for i in range(n):
            min_d = float("inf")
            nn = -1
            for j in range(n):
                if i == j:
                    continue
                d = _dist(points[i], points[j])
                if d < min_d:
                    min_d = d
                    nn = j
            if min_d > self.threshold:
                violations.append(
                    Violation(
                        self.name,
                        self.severity,
                        f"Point {i} nearest-neighbor is {min_d:.2f} away (max {self.threshold:.2f})",
                        [i],
                        {"nn_distance": min_d, "nn_index": nn},
                    )
                )
        return violations

    def repair(self, points, bounds, violations):
        pts = list(points)
        added = []
        for v in violations:
            if not v.affected_indices:
                continue
            i = v.affected_indices[0]
            nn_idx = v.detail.get("nn_index", -1)
            if nn_idx < 0 or nn_idx >= len(pts):
                continue
            mx = (pts[i][0] + pts[nn_idx][0]) / 2
            my = (pts[i][1] + pts[nn_idx][1]) / 2
            added.append((mx, my))
        return pts + added


class ExclusionZone(Constraint):
    """No points allowed within a circular exclusion zone (cx, cy, radius)."""

    name = "ExclusionZone"

    def __init__(self, cx, cy, radius, severity="critical"):
        self.cx = float(cx)
        self.cy = float(cy)
        self.radius = float(radius)
        self.severity = severity

    def check(self, points, bounds):
        violations = []
        for i, p in enumerate(points):
            d = _dist(p, (self.cx, self.cy))
            if d < self.radius:
                violations.append(
                    Violation(
                        self.name,
                        self.severity,
                        f"Point {i} at ({p[0]:.1f},{p[1]:.1f}) inside exclusion zone "
                        f"({self.cx:.1f},{self.cy:.1f},r={self.radius:.1f})",
                        [i],
                        {"distance_to_center": d},
                    )
                )
        return violations

    def repair(self, points, bounds, violations):
        pts = list(points)
        for v in violations:
            if not v.affected_indices:
                continue
            i = v.affected_indices[0]
            p = pts[i]
            dx = p[0] - self.cx
            dy = p[1] - self.cy
            d = math.sqrt(dx * dx + dy * dy)
            if d < 1e-12:
                dx, dy = 1.0, 0.0
                d = 1.0
            factor = (self.radius + 1.0) / d
            pts[i] = (self.cx + dx * factor, self.cy + dy * factor)
        return pts


class InclusionZone(Constraint):
    """All points must be within a rectangular boundary [x1,y1,x2,y2]."""

    name = "InclusionZone"

    def __init__(self, x1, y1, x2, y2, severity="critical"):
        self.x1 = min(float(x1), float(x2))
        self.y1 = min(float(y1), float(y2))
        self.x2 = max(float(x1), float(x2))
        self.y2 = max(float(y1), float(y2))
        self.severity = severity

    def check(self, points, bounds):
        violations = []
        for i, p in enumerate(points):
            if p[0] < self.x1 or p[0] > self.x2 or p[1] < self.y1 or p[1] > self.y2:
                violations.append(
                    Violation(
                        self.name,
                        self.severity,
                        f"Point {i} at ({p[0]:.1f},{p[1]:.1f}) outside inclusion zone",
                        [i],
                        {"point": p, "zone": (self.x1, self.y1, self.x2, self.y2)},
                    )
                )
        return violations

    def repair(self, points, bounds, violations):
        pts = list(points)
        for v in violations:
            if not v.affected_indices:
                continue
            i = v.affected_indices[0]
            x = max(self.x1, min(self.x2, pts[i][0]))
            y = max(self.y1, min(self.y2, pts[i][1]))
            pts[i] = (x, y)
        return pts


class DensityCap(Constraint):
    """Maximum point density per grid cell."""

    name = "DensityCap"

    def __init__(self, max_per_cell, grid_res=10, severity="warning"):
        self.max_per_cell = int(max_per_cell)
        self.grid_res = int(grid_res)
        self.severity = severity

    def _grid_key(self, p, cell_w, cell_h, s, w):
        gx = int((p[0] - w) / cell_w) if cell_w > 0 else 0
        gy = int((p[1] - s) / cell_h) if cell_h > 0 else 0
        return (gx, gy)

    def check(self, points, bounds):
        s, n, w, e = bounds
        cell_w = (e - w) / self.grid_res if self.grid_res > 0 else 1
        cell_h = (n - s) / self.grid_res if self.grid_res > 0 else 1
        grid: dict = {}
        for i, p in enumerate(points):
            key = self._grid_key(p, cell_w, cell_h, s, w)
            grid.setdefault(key, []).append(i)

        violations = []
        for key, indices in grid.items():
            if len(indices) > self.max_per_cell:
                excess = len(indices) - self.max_per_cell
                violations.append(
                    Violation(
                        self.name,
                        self.severity,
                        f"Cell {key} has {len(indices)} points (max {self.max_per_cell}), "
                        f"{excess} excess",
                        indices,
                        {"cell": key, "count": len(indices), "excess": excess},
                    )
                )
        return violations

    def repair(self, points, bounds, violations):
        to_remove = set()
        for v in violations:
            excess = v.detail.get("excess", 0)
            indices = v.affected_indices or []
            for idx in indices[-excess:]:
                to_remove.add(idx)
        return [p for i, p in enumerate(points) if i not in to_remove]


class DensityFloor(Constraint):
    """Minimum point density per grid cell — flags empty regions."""

    name = "DensityFloor"

    def __init__(self, min_per_cell=1, grid_res=5, severity="info"):
        self.min_per_cell = int(min_per_cell)
        self.grid_res = int(grid_res)
        self.severity = severity

    def check(self, points, bounds):
        s, n, w, e = bounds
        cell_w = (e - w) / self.grid_res if self.grid_res > 0 else 1
        cell_h = (n - s) / self.grid_res if self.grid_res > 0 else 1
        grid: dict = {}
        for i, p in enumerate(points):
            gx = int((p[0] - w) / cell_w) if cell_w > 0 else 0
            gy = int((p[1] - s) / cell_h) if cell_h > 0 else 0
            grid.setdefault((gx, gy), []).append(i)

        violations = []
        for gx in range(self.grid_res):
            for gy in range(self.grid_res):
                count = len(grid.get((gx, gy), []))
                if count < self.min_per_cell:
                    cx = w + (gx + 0.5) * cell_w
                    cy = s + (gy + 0.5) * cell_h
                    violations.append(
                        Violation(
                            self.name,
                            self.severity,
                            f"Cell ({gx},{gy}) has {count} points (min {self.min_per_cell})",
                            [],
                            {"cell": (gx, gy), "count": count, "center": (cx, cy)},
                        )
                    )
        return violations

    def repair(self, points, bounds, violations):
        pts = list(points)
        for v in violations:
            center = v.detail.get("center")
            if center:
                deficit = self.min_per_cell - v.detail.get("count", 0)
                for _ in range(max(deficit, 0)):
                    jx = center[0] + random.uniform(-5, 5)
                    jy = center[1] + random.uniform(-5, 5)
                    pts.append((jx, jy))
        return pts


class SymmetryAxis(Constraint):
    """Points should have approximate mirror symmetry about an axis.

    *axis*: ``"vertical"`` (mirror across x=mid) or ``"horizontal"``
    (mirror across y=mid).  *tolerance* is the max distance a point's
    mirror can be from any actual point for it to count as matched.
    """

    name = "SymmetryAxis"

    def __init__(self, axis="vertical", tolerance=None, severity="info"):
        self.axis = axis
        self.tolerance = tolerance
        self.severity = severity

    def check(self, points, bounds):
        s, n, w, e = bounds
        if self.tolerance is None:
            extent = max(e - w, n - s, 1)
            tol = extent * 0.05
        else:
            tol = self.tolerance

        if self.axis == "vertical":
            mid = (w + e) / 2
        else:
            mid = (s + n) / 2

        violations = []
        for i, p in enumerate(points):
            if self.axis == "vertical":
                mirror = (2 * mid - p[0], p[1])
            else:
                mirror = (p[0], 2 * mid - p[1])

            found = False
            for j, q in enumerate(points):
                if i == j:
                    continue
                if _dist(mirror, q) <= tol:
                    found = True
                    break
            if not found:
                violations.append(
                    Violation(
                        self.name,
                        self.severity,
                        f"Point {i} at ({p[0]:.1f},{p[1]:.1f}) has no mirror partner "
                        f"(axis={self.axis}, tol={tol:.1f})",
                        [i],
                        {"mirror_expected": mirror},
                    )
                )
        return violations

    def repair(self, points, bounds, violations):
        pts = list(points)
        for v in violations:
            mirror = v.detail.get("mirror_expected")
            if mirror:
                pts.append(mirror)
        return pts


class CountBounds(Constraint):
    """Total point count must be within [min_count, max_count]."""

    name = "CountBounds"

    def __init__(self, min_count=0, max_count=float("inf"), severity="warning"):
        self.min_count = int(min_count)
        self.max_count = int(max_count) if max_count != float("inf") else float("inf")
        self.severity = severity

    def check(self, points, bounds):
        n = len(points)
        violations = []
        if n < self.min_count:
            violations.append(
                Violation(
                    self.name,
                    self.severity,
                    f"Only {n} points (minimum {self.min_count})",
                    [],
                    {"count": n, "min": self.min_count, "deficit": self.min_count - n},
                )
            )
        if n > self.max_count:
            violations.append(
                Violation(
                    self.name,
                    self.severity,
                    f"{n} points exceeds maximum {self.max_count}",
                    list(range(int(self.max_count), n)),
                    {"count": n, "max": self.max_count, "excess": n - int(self.max_count)},
                )
            )
        return violations

    def repair(self, points, bounds, violations):
        pts = list(points)
        for v in violations:
            deficit = v.detail.get("deficit", 0)
            if deficit > 0:
                s, n, w, e = bounds
                for _ in range(deficit):
                    pts.append((random.uniform(w, e), random.uniform(s, n)))
            excess = v.detail.get("excess", 0)
            if excess > 0:
                pts = pts[: int(self.max_count)]
        return pts


# ---------------------------------------------------------------------------
# Guardian class
# ---------------------------------------------------------------------------


class Guardian:
    """Autonomous spatial constraint enforcer.

    Parameters
    ----------
    points : list of (x, y)
        Initial point set to guard.
    constraints : list of Constraint, optional
        Constraints to enforce.
    bounds : tuple, optional
        (south, north, west, east). Auto-computed if omitted.
    """

    def __init__(self, points, constraints=None, bounds=None):
        self.points = list(points)
        self.constraints = list(constraints or [])
        if bounds is not None:
            self.bounds = bounds
        elif self.points:
            self.bounds = vormap.compute_bounds(self.points)
        else:
            self.bounds = (0.0, 1000.0, 0.0, 2000.0)

    def add_constraint(self, constraint):
        self.constraints.append(constraint)

    def validate(self) -> GuardianReport:
        report = GuardianReport()
        report.original_count = len(self.points)
        report.final_count = len(self.points)
        report.constraints_checked = len(self.constraints)
        report.points = list(self.points)

        for c in self.constraints:
            vs = c.check(self.points, self.bounds)
            report.violations.extend(vs)

        report.compliance_score = self._score(report.violations)
        return report

    def auto_repair(self, max_iterations=5) -> GuardianReport:
        report = GuardianReport()
        report.original_count = len(self.points)
        report.constraints_checked = len(self.constraints)

        for iteration in range(1, max_iterations + 1):
            all_violations = []
            for c in self.constraints:
                vs = c.check(self.points, self.bounds)
                all_violations.extend(vs)

            if not all_violations:
                break

            report.iterations = iteration
            for c in self.constraints:
                vs = c.check(self.points, self.bounds)
                if vs:
                    old_pts = list(self.points)
                    self.points = c.repair(self.points, self.bounds, vs)
                    if self.points and self.bounds == (0.0, 1000.0, 0.0, 2000.0):
                        pass
                    elif self.points:
                        self.bounds = vormap.compute_bounds(self.points)

        # Final validation
        all_violations = []
        for c in self.constraints:
            all_violations.extend(c.check(self.points, self.bounds))

        report.violations = all_violations
        report.final_count = len(self.points)
        report.points_modified = abs(len(self.points) - report.original_count) + report.iterations
        report.compliance_score = self._score(all_violations)
        report.points = list(self.points)
        return report

    def compliance_score(self) -> float:
        return self.validate().compliance_score

    @staticmethod
    def _score(violations):
        if not violations:
            return 100.0
        penalty = 0
        for v in violations:
            if v.severity == "critical":
                penalty += 15
            elif v.severity == "warning":
                penalty += 5
            else:
                penalty += 1
        return max(0.0, 100.0 - penalty)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def guard(source, constraints=None, auto_repair=False, max_iterations=5):
    """Guard a point set against spatial constraints.

    Parameters
    ----------
    source : str or list
        File path or list of (x, y) tuples.
    constraints : list of Constraint
        Constraints to enforce.
    auto_repair : bool
        If True, iteratively repair violations.
    max_iterations : int
        Maximum repair iterations.

    Returns
    -------
    GuardianReport
    """
    if isinstance(source, str):
        points = vormap.load_data(source)
    else:
        points = list(source)

    g = Guardian(points, constraints)
    if auto_repair:
        return g.auto_repair(max_iterations=max_iterations)
    return g.validate()


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def _run_demo():
    """Generate a demo with deliberate violations and auto-repair."""
    random.seed(42)
    points = []

    # Cluster of well-spaced points
    for _ in range(80):
        points.append((random.uniform(100, 900), random.uniform(100, 900)))

    # Deliberately close pairs (MinSpacing violations)
    points.append((500.0, 500.0))
    points.append((500.5, 500.3))
    points.append((200.0, 200.0))
    points.append((200.1, 200.1))

    # Points in an exclusion zone
    points.append((450.0, 450.0))
    points.append((460.0, 455.0))

    # Points outside inclusion zone
    points.append((-50.0, 500.0))
    points.append((500.0, 1200.0))

    constraints = [
        MinSpacing(threshold=5.0),
        ExclusionZone(cx=450.0, cy=450.0, radius=30.0),
        InclusionZone(0, 0, 1000, 1000),
        DensityCap(max_per_cell=15, grid_res=5),
        CountBounds(min_count=10, max_count=200),
    ]

    g = Guardian(points, constraints)

    print("=" * 60)
    print("🛡️  SPATIAL CONSTRAINT GUARDIAN — DEMO")
    print("=" * 60)
    print(f"\n📊 {len(points)} points, {len(constraints)} constraints\n")

    report = g.validate()
    print("BEFORE REPAIR:")
    print(report.summary())
    print()

    report = g.auto_repair(max_iterations=5)
    print("AFTER AUTO-REPAIR:")
    print(report.summary())
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    import argparse

    parser = argparse.ArgumentParser(
        description="Spatial Constraint Guardian — autonomous constraint enforcement"
    )
    parser.add_argument("input", nargs="?", help="Input point file")
    parser.add_argument("--demo", action="store_true", help="Run demo with sample violations")
    parser.add_argument("--min-spacing", type=float, help="Minimum distance between points")
    parser.add_argument("--max-spacing", type=float, help="Maximum nearest-neighbor distance")
    parser.add_argument(
        "--exclusion-zone",
        action="append",
        help="Exclusion zone as cx,cy,radius (repeatable)",
    )
    parser.add_argument(
        "--inclusion-zone",
        help="Inclusion zone as x1,y1,x2,y2",
    )
    parser.add_argument("--density-cap", type=int, help="Max points per grid cell")
    parser.add_argument("--density-floor", type=int, help="Min points per grid cell")
    parser.add_argument("--symmetry", choices=["vertical", "horizontal"], help="Symmetry axis")
    parser.add_argument("--min-count", type=int, help="Minimum point count")
    parser.add_argument("--max-count", type=int, help="Maximum point count")
    parser.add_argument("--auto-repair", action="store_true", help="Auto-repair violations")
    parser.add_argument("--iterations", type=int, default=5, help="Max repair iterations")
    parser.add_argument("--json", metavar="PATH", help="Export JSON report")
    parser.add_argument("--html", metavar="PATH", help="Export HTML report")
    parser.add_argument("--output", metavar="PATH", help="Save repaired points")

    args = parser.parse_args()

    if args.demo:
        report = _run_demo()
        if args.json:
            report.to_json(args.json)
            print(f"\n📄 JSON report: {args.json}")
        if args.html:
            report.to_html(args.html)
            print(f"\n🌐 HTML report: {args.html}")
        return

    if not args.input:
        parser.print_help()
        sys.exit(1)

    points = vormap.load_data(args.input)
    constraints = []

    if args.min_spacing is not None:
        constraints.append(MinSpacing(args.min_spacing))
    if args.max_spacing is not None:
        constraints.append(MaxSpacing(args.max_spacing))
    if args.exclusion_zone:
        for zone in args.exclusion_zone:
            parts = [float(x) for x in zone.split(",")]
            if len(parts) == 3:
                constraints.append(ExclusionZone(*parts))
    if args.inclusion_zone:
        parts = [float(x) for x in args.inclusion_zone.split(",")]
        if len(parts) == 4:
            constraints.append(InclusionZone(*parts))
    if args.density_cap is not None:
        constraints.append(DensityCap(args.density_cap))
    if args.density_floor is not None:
        constraints.append(DensityFloor(args.density_floor))
    if args.symmetry:
        constraints.append(SymmetryAxis(axis=args.symmetry))
    if args.min_count is not None or args.max_count is not None:
        constraints.append(
            CountBounds(
                min_count=args.min_count or 0,
                max_count=args.max_count if args.max_count else float("inf"),
            )
        )

    if not constraints:
        print("No constraints specified. Use --min-spacing, --exclusion-zone, etc.")
        sys.exit(1)

    g = Guardian(points, constraints)

    if args.auto_repair:
        report = g.auto_repair(max_iterations=args.iterations)
    else:
        report = g.validate()

    print(report.summary())

    if args.json:
        report.to_json(args.json)
        print(f"\n📄 JSON saved: {args.json}")
    if args.html:
        report.to_html(args.html)
        print(f"\n🌐 HTML saved: {args.html}")
    if args.output and args.auto_repair:
        with open(args.output, "w") as f:
            for p in report.points:
                f.write(f"{p[0]} {p[1]}\n")
        print(f"\n💾 Repaired points saved: {args.output}")


if __name__ == "__main__":
    _cli()
