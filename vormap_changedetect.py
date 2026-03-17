"""Spatial Change Detection for Voronoi point datasets.

Compares two point datasets (before/after) to detect spatial changes —
a fundamental tool for monitoring evolving spatial phenomena such as
facility relocations, habitat changes, or urban growth.

Six analyses are performed:

- **Point Matching** — nearest-neighbour matching between datasets with
  configurable distance threshold to identify correspondences.
- **Added Points** — points in the *after* set with no match in *before*,
  representing new spatial events.
- **Removed Points** — points in the *before* set with no match in *after*,
  representing disappeared features.
- **Shifted Points** — matched pairs where the point moved, with distance
  and direction (bearing) of displacement.
- **Density Change** — grid-based density comparison showing regions of
  intensification or thinning.
- **Change Summary** — overall statistics including change rate, mean
  displacement, dominant shift direction, and stability score.

Usage (Python API)::

    from vormap_changedetect import detect_changes, ChangeReport

    before = [(10, 20), (30, 40), (50, 60)]
    after  = [(10, 21), (35, 42), (70, 80)]

    report = detect_changes(before, after, match_threshold=5.0)
    print(f"Added: {len(report.added)}")
    print(f"Removed: {len(report.removed)}")
    print(f"Shifted: {len(report.shifted)}")
    print(f"Stability: {report.summary['stability_score']:.2f}")

    # Export
    report.to_json("changes.json")
    report.to_csv("changes.csv")
    report.to_svg("changes.svg")

CLI::

    voronoimap datauni5.txt 5 --change-detect data_after.txt
    voronoimap datauni5.txt 5 --change-detect data_after.txt --change-threshold 10
    voronoimap datauni5.txt 5 --change-json changes.json --change-detect data_after.txt
    voronoimap datauni5.txt 5 --change-csv changes.csv --change-detect data_after.txt
    voronoimap datauni5.txt 5 --change-svg changes.svg --change-detect data_after.txt
"""

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

from vormap import validate_output_path


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Euclidean distance between two 2-D points."""
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _bearing(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Bearing in degrees (0=N, 90=E, 180=S, 270=W) from *a* to *b*."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    angle = math.degrees(math.atan2(dx, dy)) % 360
    return angle


def _compass(bearing: float) -> str:
    """Convert bearing to 8-point compass direction."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((bearing + 22.5) / 45) % 8
    return dirs[idx]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ShiftedPoint:
    """A point that moved between the two datasets."""
    before: Tuple[float, float]
    after: Tuple[float, float]
    distance: float
    bearing: float
    compass: str


@dataclass
class DensityCell:
    """A grid cell with density counts for before/after."""
    row: int
    col: int
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    count_before: int
    count_after: int
    change: int  # after - before
    change_pct: float  # percentage change


@dataclass
class ChangeReport:
    """Complete change detection report."""
    before_count: int
    after_count: int
    matched: List[Tuple[Tuple[float, float], Tuple[float, float]]]
    added: List[Tuple[float, float]]
    removed: List[Tuple[float, float]]
    shifted: List[ShiftedPoint]
    density_grid: List[DensityCell]
    summary: Dict[str, Any]
    match_threshold: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to a plain dictionary."""
        return {
            "before_count": self.before_count,
            "after_count": self.after_count,
            "match_threshold": self.match_threshold,
            "matched_count": len(self.matched),
            "added_count": len(self.added),
            "removed_count": len(self.removed),
            "shifted_count": len(self.shifted),
            "added": [{"x": p[0], "y": p[1]} for p in self.added],
            "removed": [{"x": p[0], "y": p[1]} for p in self.removed],
            "shifted": [
                {
                    "before": {"x": s.before[0], "y": s.before[1]},
                    "after": {"x": s.after[0], "y": s.after[1]},
                    "distance": round(s.distance, 4),
                    "bearing": round(s.bearing, 1),
                    "compass": s.compass,
                }
                for s in self.shifted
            ],
            "density_changes": [
                {
                    "row": c.row, "col": c.col,
                    "x_range": [round(c.x_min, 4), round(c.x_max, 4)],
                    "y_range": [round(c.y_min, 4), round(c.y_max, 4)],
                    "before": c.count_before, "after": c.count_after,
                    "change": c.change,
                    "change_pct": round(c.change_pct, 2),
                }
                for c in self.density_grid
                if c.change != 0
            ],
            "summary": self.summary,
        }

    def to_json(self, path: str) -> str:
        """Export report to JSON file."""
        validate_output_path(path)
        data = self.to_dict()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return path

    def to_csv(self, path: str) -> str:
        """Export shifted points and density changes to CSV."""
        validate_output_path(path)
        lines: List[str] = []

        # Shifted points
        lines.append("section,before_x,before_y,after_x,after_y,distance,bearing,compass")
        for s in self.shifted:
            lines.append(
                f"shifted,{s.before[0]},{s.before[1]},"
                f"{s.after[0]},{s.after[1]},"
                f"{s.distance:.4f},{s.bearing:.1f},{s.compass}"
            )

        # Added
        for p in self.added:
            lines.append(f"added,,,{p[0]},{p[1]},,,")

        # Removed
        for p in self.removed:
            lines.append(f"removed,{p[0]},{p[1]},,,,, ")

        # Density
        lines.append("")
        lines.append("section,row,col,before,after,change,change_pct")
        for c in self.density_grid:
            if c.change != 0:
                lines.append(
                    f"density,{c.row},{c.col},"
                    f"{c.count_before},{c.count_after},"
                    f"{c.change},{c.change_pct:.2f}"
                )

        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def to_svg(self, path: str, width: int = 800, height: int = 600) -> str:
        """Export a visual change map as SVG."""
        validate_output_path(path)

        all_pts = (
            [s.before for s in self.shifted]
            + [s.after for s in self.shifted]
            + list(self.added)
            + list(self.removed)
            + [(m[0][0], m[0][1]) for m in self.matched if m not in [(s.before, s.after) for s in self.shifted]]
        )
        if not all_pts:
            # Empty SVG
            root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                              width=str(width), height=str(height))
            tree = ET.ElementTree(root)
            tree.write(path, xml_declaration=True)
            return path

        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x = max_x - min_x or 1.0
        span_y = max_y - min_y or 1.0

        margin = 60

        def tx(x: float) -> float:
            return margin + (x - min_x) / span_x * (width - 2 * margin)

        def ty(y: float) -> float:
            return height - margin - (y - min_y) / span_y * (height - 2 * margin)

        root = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                          width=str(width), height=str(height))

        # Background
        ET.SubElement(root, "rect", x="0", y="0", width=str(width),
                      height=str(height), fill="#f8f9fa")

        # Title
        title = ET.SubElement(root, "text", x=str(width // 2), y="30",
                              fill="#333")
        title.set("text-anchor", "middle")
        title.set("font-size", "18")
        title.set("font-family", "sans-serif")
        title.set("font-weight", "bold")
        title.text = "Spatial Change Detection"

        # Legend
        legend_items = [
            ("#4CAF50", "Added"),
            ("#F44336", "Removed"),
            ("#2196F3", "Shifted"),
            ("#9E9E9E", "Stable"),
        ]
        for i, (color, label) in enumerate(legend_items):
            lx = width - 140
            ly = 60 + i * 22
            ET.SubElement(root, "circle", cx=str(lx), cy=str(ly),
                          r="5", fill=color)
            t = ET.SubElement(root, "text", x=str(lx + 12), y=str(ly + 4),
                              fill="#333")
            t.set("font-size", "12")
            t.set("font-family", "sans-serif")
            t.text = label

        # Shift arrows
        defs = ET.SubElement(root, "defs")
        marker = ET.SubElement(defs, "marker", id="arrowhead",
                               markerWidth="8", markerHeight="6",
                               refX="8", refY="3", orient="auto")
        ET.SubElement(marker, "polygon", points="0 0, 8 3, 0 6",
                      fill="#2196F3")

        for s in self.shifted:
            x1, y1 = tx(s.before[0]), ty(s.before[1])
            x2, y2 = tx(s.after[0]), ty(s.after[1])
            line = ET.SubElement(root, "line",
                                 x1=f"{x1:.1f}", y1=f"{y1:.1f}",
                                 x2=f"{x2:.1f}", y2=f"{y2:.1f}",
                                 stroke="#2196F3")
            line.set("stroke-width", "1.5")
            line.set("marker-end", "url(#arrowhead)")
            ET.SubElement(root, "circle", cx=f"{x1:.1f}", cy=f"{y1:.1f}",
                          r="4", fill="#2196F3", opacity="0.5")
            ET.SubElement(root, "circle", cx=f"{x2:.1f}", cy=f"{y2:.1f}",
                          r="4", fill="#2196F3")

        # Added points
        for p in self.added:
            cx, cy = tx(p[0]), ty(p[1])
            ET.SubElement(root, "circle", cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                          r="5", fill="#4CAF50", opacity="0.8")

        # Removed points
        for p in self.removed:
            cx, cy = tx(p[0]), ty(p[1])
            ET.SubElement(root, "circle", cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                          r="5", fill="#F44336", opacity="0.8")
            # X marker
            ET.SubElement(root, "line",
                          x1=f"{cx - 3:.1f}", y1=f"{cy - 3:.1f}",
                          x2=f"{cx + 3:.1f}", y2=f"{cy + 3:.1f}",
                          stroke="#F44336")
            ET.SubElement(root, "line",
                          x1=f"{cx + 3:.1f}", y1=f"{cy - 3:.1f}",
                          x2=f"{cx - 3:.1f}", y2=f"{cy + 3:.1f}",
                          stroke="#F44336")

        # Stable matched (not shifted)
        shifted_before = {s.before for s in self.shifted}
        for m in self.matched:
            if m[0] not in shifted_before:
                cx, cy = tx(m[0][0]), ty(m[0][1])
                ET.SubElement(root, "circle", cx=f"{cx:.1f}", cy=f"{cy:.1f}",
                              r="3", fill="#9E9E9E", opacity="0.5")

        # Summary text
        sy = height - 15
        info = ET.SubElement(root, "text", x=str(margin), y=str(sy),
                             fill="#666")
        info.set("font-size", "11")
        info.set("font-family", "sans-serif")
        info.text = (
            f"Before: {self.before_count}  After: {self.after_count}  "
            f"Added: {len(self.added)}  Removed: {len(self.removed)}  "
            f"Shifted: {len(self.shifted)}  "
            f"Stability: {self.summary.get('stability_score', 0):.2f}"
        )

        tree = ET.ElementTree(root)
        tree.write(path, xml_declaration=True)
        return path


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _match_points(
    before: List[Tuple[float, float]],
    after: List[Tuple[float, float]],
    threshold: float,
) -> Tuple[
    List[Tuple[Tuple[float, float], Tuple[float, float]]],
    List[Tuple[float, float]],
    List[Tuple[float, float]],
]:
    """Greedy nearest-neighbour matching within *threshold* distance.

    Returns (matched_pairs, unmatched_after=added, unmatched_before=removed).
    """
    used_after: set = set()
    matches: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    unmatched_before: List[Tuple[float, float]] = []

    for bp in before:
        best_dist = float("inf")
        best_idx = -1
        for j, ap in enumerate(after):
            if j in used_after:
                continue
            d = _dist(bp, ap)
            if d < best_dist:
                best_dist = d
                best_idx = j
        if best_idx >= 0 and best_dist <= threshold:
            matches.append((bp, after[best_idx]))
            used_after.add(best_idx)
        else:
            unmatched_before.append(bp)

    added = [ap for j, ap in enumerate(after) if j not in used_after]
    return matches, added, unmatched_before


def _compute_density_grid(
    before: List[Tuple[float, float]],
    after: List[Tuple[float, float]],
    grid_size: int = 5,
) -> List[DensityCell]:
    """Compute density change on a grid."""
    all_pts = before + after
    if not all_pts:
        return []

    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = (max_x - min_x) or 1.0
    span_y = (max_y - min_y) or 1.0
    cell_w = span_x / grid_size
    cell_h = span_y / grid_size

    cells: List[DensityCell] = []
    for r in range(grid_size):
        for c in range(grid_size):
            x0 = min_x + c * cell_w
            x1 = x0 + cell_w
            y0 = min_y + r * cell_h
            y1 = y0 + cell_h

            cb = sum(1 for p in before if x0 <= p[0] < x1 and y0 <= p[1] < y1)
            ca = sum(1 for p in after if x0 <= p[0] < x1 and y0 <= p[1] < y1)

            # Edge: include max boundary
            if c == grid_size - 1:
                cb += sum(1 for p in before if p[0] == x1 and y0 <= p[1] <= y1)
                ca += sum(1 for p in after if p[0] == x1 and y0 <= p[1] <= y1)
            if r == grid_size - 1:
                cb += sum(1 for p in before if x0 <= p[0] <= x1 and p[1] == y1)
                ca += sum(1 for p in after if x0 <= p[0] <= x1 and p[1] == y1)

            change = ca - cb
            change_pct = (change / cb * 100) if cb > 0 else (100.0 if ca > 0 else 0.0)
            cells.append(DensityCell(r, c, x0, x1, y0, y1, cb, ca, change, change_pct))

    return cells


def detect_changes(
    before: List[Tuple[float, float]],
    after: List[Tuple[float, float]],
    match_threshold: float = 5.0,
    shift_min: float = 0.01,
    grid_size: int = 5,
) -> ChangeReport:
    """Detect spatial changes between two point datasets.

    Parameters
    ----------
    before : list of (x, y) tuples
        The *before* (baseline) dataset.
    after : list of (x, y) tuples
        The *after* (comparison) dataset.
    match_threshold : float
        Maximum distance for two points to be considered a match.
    shift_min : float
        Minimum displacement to classify a match as "shifted" (vs stable).
    grid_size : int
        Grid resolution for density change analysis.

    Returns
    -------
    ChangeReport
        Full change detection results with export methods.
    """
    if not before and not after:
        return ChangeReport(
            before_count=0, after_count=0, matched=[], added=[],
            removed=[], shifted=[], density_grid=[], match_threshold=match_threshold,
            summary={"stability_score": 1.0, "change_rate": 0.0,
                     "mean_displacement": 0.0, "dominant_direction": "N/A",
                     "added_count": 0, "removed_count": 0, "shifted_count": 0,
                     "stable_count": 0},
        )

    matched, added, removed = _match_points(before, after, match_threshold)

    # Classify matched pairs
    shifted: List[ShiftedPoint] = []
    for bp, ap in matched:
        d = _dist(bp, ap)
        if d >= shift_min:
            b = _bearing(bp, ap)
            shifted.append(ShiftedPoint(bp, ap, d, b, _compass(b)))

    stable_count = len(matched) - len(shifted)

    # Density grid
    density = _compute_density_grid(before, after, grid_size)

    # Summary
    total_points = max(len(before), len(after), 1)
    change_rate = (len(added) + len(removed) + len(shifted)) / total_points
    mean_disp = (sum(s.distance for s in shifted) / len(shifted)) if shifted else 0.0

    # Dominant direction
    if shifted:
        dir_counts: Dict[str, int] = {}
        for s in shifted:
            dir_counts[s.compass] = dir_counts.get(s.compass, 0) + 1
        dominant = max(dir_counts, key=dir_counts.get)  # type: ignore
    else:
        dominant = "N/A"

    stability = max(0.0, 1.0 - change_rate)

    summary = {
        "stability_score": round(stability, 4),
        "change_rate": round(change_rate, 4),
        "mean_displacement": round(mean_disp, 4),
        "max_displacement": round(max((s.distance for s in shifted), default=0.0), 4),
        "dominant_direction": dominant,
        "added_count": len(added),
        "removed_count": len(removed),
        "shifted_count": len(shifted),
        "stable_count": stable_count,
        "density_increase_cells": sum(1 for c in density if c.change > 0),
        "density_decrease_cells": sum(1 for c in density if c.change < 0),
    }

    return ChangeReport(
        before_count=len(before),
        after_count=len(after),
        matched=matched,
        added=added,
        removed=removed,
        shifted=shifted,
        density_grid=density,
        summary=summary,
        match_threshold=match_threshold,
    )
