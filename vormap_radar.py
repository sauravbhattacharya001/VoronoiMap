"""Autonomous Spatial Radar System for Voronoi point datasets.

Treats a spatial point dataset as radar targets viewed from a configurable
origin (radar station).  Computes distance and compass bearing to every
point, divides space into range rings and angular sectors, performs
density anomaly detection per sector, and generates an interactive HTML
radar display with animated sweep, blips, sector/ring tables, and
proactive alerts.

Usage (CLI)::

    python vormap_radar.py data.txt
    python vormap_radar.py data.txt --origin 500,300 --rings 6 --sectors 8
    python vormap_radar.py data.txt -o radar.html --json radar.json

Usage (Python API)::

    from vormap_radar import RadarStation, export_html

    pts = [(100, 200), (300, 400), (500, 100), (700, 600)]
    station = RadarStation(pts, width=800, height=700)
    result = station.scan()
    print(result.summary())
    export_html(result, "radar.html")
    result.to_json("radar.json")
"""

from __future__ import annotations

import argparse
import html as _html
import json
import math
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from vormap import validate_output_path
from vormap_utils import polygon_centroid_mean as _centroid, bounding_box as _bounding_box

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _bearing(origin: Tuple[float, float], target: Tuple[float, float]) -> float:
    """Compass bearing from *origin* to *target* (0°=N, clockwise).

    Uses screen coordinates (y increases downward), so North is negative-y.
    """
    dx = target[0] - origin[0]
    dy = origin[1] - target[1]  # flip y for screen coords
    angle = math.degrees(math.atan2(dx, dy)) % 360
    return angle


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Target:
    """A single radar target (point)."""
    x: float
    y: float
    distance: float
    bearing: float
    ring: int
    sector: int
    nn_dist: float  # nearest-neighbor distance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "distance": round(self.distance, 2),
            "bearing": round(self.bearing, 1),
            "ring": self.ring,
            "sector": self.sector,
            "nn_dist": round(self.nn_dist, 2),
        }


@dataclass
class SectorInfo:
    """Density analysis for one angular sector."""
    index: int
    bearing_start: float
    bearing_end: float
    count: int
    z_score: float = 0.0
    status: str = "normal"  # normal | moderate | dense | void

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "bearing_range": f"{self.bearing_start:.0f}°–{self.bearing_end:.0f}°",
            "count": self.count,
            "z_score": round(self.z_score, 2),
            "status": self.status,
        }


@dataclass
class RingInfo:
    """Stats for one range ring."""
    index: int
    dist_min: float
    dist_max: float
    count: int
    pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "dist_range": f"{self.dist_min:.1f}–{self.dist_max:.1f}",
            "count": self.count,
            "pct": round(self.pct, 1),
        }


@dataclass
class Alert:
    severity: str  # info | warning | critical
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {"severity": self.severity, "message": self.message}


@dataclass
class RadarResult:
    """Full radar scan result."""
    origin: Tuple[float, float]
    max_range: float
    num_rings: int
    num_sectors: int
    targets: List[Target]
    sectors: List[SectorInfo]
    rings: List[RingInfo]
    alerts: List[Alert]
    stats: Dict[str, Any]
    width: float
    height: float

    def summary(self) -> str:
        lines = [
            "📡 Spatial Radar Scan Summary",
            "=" * 40,
            f"Origin       : ({self.origin[0]:.1f}, {self.origin[1]:.1f})",
            f"Max range    : {self.max_range:.1f}",
            f"Targets      : {len(self.targets)}",
            f"Rings        : {self.num_rings}",
            f"Sectors      : {self.num_sectors}",
            f"Mean distance: {self.stats.get('mean_dist', 0):.1f}",
            f"Median dist  : {self.stats.get('median_dist', 0):.1f}",
            f"Coverage     : {self.stats.get('coverage_pct', 0):.1f}%",
            f"Alerts       : {len(self.alerts)}",
        ]
        if self.alerts:
            lines.append("")
            for a in self.alerts:
                icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(a.severity, "⚪")
                lines.append(f"  {icon} [{a.severity.upper()}] {a.message}")
        return "\n".join(lines)

    def to_json(self, path: str) -> None:
        validate_output_path(path)
        data = {
            "origin": {"x": round(self.origin[0], 2), "y": round(self.origin[1], 2)},
            "max_range": round(self.max_range, 2),
            "num_rings": self.num_rings,
            "num_sectors": self.num_sectors,
            "stats": self.stats,
            "targets": [t.to_dict() for t in self.targets],
            "sectors": [s.to_dict() for s in self.sectors],
            "rings": [r.to_dict() for r in self.rings],
            "alerts": [a.to_dict() for a in self.alerts],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"📄 JSON exported → {path}")


# ---------------------------------------------------------------------------
# Radar Station
# ---------------------------------------------------------------------------

class RadarStation:
    """Configurable spatial radar scanner.

    Parameters
    ----------
    points : list of (x, y) tuples
        The spatial points to scan.
    width, height : float
        Bounding dimensions of the dataset.
    origin : tuple or None
        Radar origin. Defaults to centroid of *points*.
    rings : int
        Number of concentric range rings (default 5).
    sectors : int
        Number of angular sectors (default 12).
    max_range : float or None
        Maximum radar range. Defaults to the farthest point distance.
    """

    def __init__(
        self,
        points: List[Tuple[float, float]],
        width: float = 1000.0,
        height: float = 1000.0,
        origin: Optional[Tuple[float, float]] = None,
        rings: int = 5,
        sectors: int = 12,
        max_range: Optional[float] = None,
    ) -> None:
        if not points:
            raise ValueError("Need at least one point")
        self.points = points
        self.width = width
        self.height = height
        self.origin = origin if origin else _centroid(points)
        self.num_rings = max(1, rings)
        self.num_sectors = max(1, sectors)
        self._max_range_override = max_range

    # ---- scanning ----------------------------------------------------------

    def scan(self) -> RadarResult:
        """Run the full radar analysis and return a *RadarResult*."""
        origin = self.origin
        pts = self.points

        # Compute distances & bearings
        dists = [_distance(origin, p) for p in pts]
        bearings = [_bearing(origin, p) for p in pts]

        max_range = self._max_range_override or (max(dists) * 1.05 if dists else 1.0)
        ring_width = max_range / self.num_rings
        sector_width = 360.0 / self.num_sectors

        # Nearest-neighbor distances (brute force, fine for typical datasets)
        nn_dists: List[float] = []
        for i, pi in enumerate(pts):
            best = float("inf")
            for j, pj in enumerate(pts):
                if i != j:
                    d = _distance(pi, pj)
                    if d < best:
                        best = d
            nn_dists.append(best if best < float("inf") else 0.0)

        # Build targets
        targets: List[Target] = []
        for i, p in enumerate(pts):
            ring_idx = min(int(dists[i] / ring_width), self.num_rings - 1) if ring_width > 0 else 0
            sector_idx = min(int(bearings[i] / sector_width), self.num_sectors - 1)
            targets.append(Target(
                x=p[0], y=p[1],
                distance=dists[i],
                bearing=bearings[i],
                ring=ring_idx,
                sector=sector_idx,
                nn_dist=nn_dists[i],
            ))

        # Sector analysis
        sector_counts = [0] * self.num_sectors
        for t in targets:
            sector_counts[t.sector] += 1
        mean_sc = statistics.mean(sector_counts) if sector_counts else 0
        std_sc = statistics.pstdev(sector_counts) if sector_counts else 0
        sectors: List[SectorInfo] = []
        for i in range(self.num_sectors):
            z = (sector_counts[i] - mean_sc) / std_sc if std_sc > 0 else 0.0
            if sector_counts[i] == 0:
                status = "void"
            elif z > 2:
                status = "dense"
            elif z > 1:
                status = "moderate"
            else:
                status = "normal"
            sectors.append(SectorInfo(
                index=i,
                bearing_start=i * sector_width,
                bearing_end=(i + 1) * sector_width,
                count=sector_counts[i],
                z_score=z,
                status=status,
            ))

        # Ring analysis
        ring_counts = [0] * self.num_rings
        for t in targets:
            ring_counts[t.ring] += 1
        n_total = len(targets) or 1
        rings: List[RingInfo] = []
        for i in range(self.num_rings):
            rings.append(RingInfo(
                index=i,
                dist_min=i * ring_width,
                dist_max=(i + 1) * ring_width,
                count=ring_counts[i],
                pct=100.0 * ring_counts[i] / n_total,
            ))

        # Alerts
        alerts = self._generate_alerts(sectors, rings, targets, max_range)

        # Stats
        dists_only = [t.distance for t in targets]
        occupied_sectors = sum(1 for s in sectors if s.count > 0)
        stats = {
            "total_targets": len(targets),
            "mean_dist": round(statistics.mean(dists_only), 2) if dists_only else 0,
            "median_dist": round(statistics.median(dists_only), 2) if dists_only else 0,
            "max_dist": round(max(dists_only), 2) if dists_only else 0,
            "min_dist": round(min(dists_only), 2) if dists_only else 0,
            "coverage_pct": round(100.0 * occupied_sectors / self.num_sectors, 1),
            "dominant_sector": max(range(self.num_sectors), key=lambda i: sector_counts[i]),
            "mean_nn_dist": round(statistics.mean(nn_dists), 2) if nn_dists else 0,
        }

        return RadarResult(
            origin=origin,
            max_range=max_range,
            num_rings=self.num_rings,
            num_sectors=self.num_sectors,
            targets=targets,
            sectors=sectors,
            rings=rings,
            alerts=alerts,
            stats=stats,
            width=self.width,
            height=self.height,
        )

    def _generate_alerts(
        self,
        sectors: List[SectorInfo],
        rings: List[RingInfo],
        targets: List[Target],
        max_range: float,
    ) -> List[Alert]:
        alerts: List[Alert] = []

        # Dense sectors
        dense = [s for s in sectors if s.status == "dense"]
        if dense:
            names = ", ".join(f"S{s.index} ({s.bearing_start:.0f}°–{s.bearing_end:.0f}°)" for s in dense)
            alerts.append(Alert(
                "warning",
                f"Dense cluster detected in {len(dense)} sector(s): {names}",
            ))

        # Void sectors
        voids = [s for s in sectors if s.status == "void"]
        if voids:
            names = ", ".join(f"S{s.index}" for s in voids)
            alerts.append(Alert(
                "info",
                f"{len(voids)} void sector(s) with no targets: {names}",
            ))

        # Range concentration
        for r in rings:
            if r.pct > 60:
                alerts.append(Alert(
                    "warning",
                    f"Range concentration: {r.pct:.0f}% of targets in ring {r.index} "
                    f"({r.dist_min:.0f}–{r.dist_max:.0f})",
                ))

        # Close-range targets
        close_thresh = max_range * 0.1
        close = [t for t in targets if t.distance < close_thresh]
        if close:
            alerts.append(Alert(
                "critical",
                f"{len(close)} target(s) within close range (<{close_thresh:.0f} from origin)",
            ))

        # Coverage alert
        occupied = sum(1 for s in sectors if s.count > 0)
        cov = occupied / len(sectors) if sectors else 0
        if cov < 0.5:
            alerts.append(Alert(
                "info",
                f"Low sector coverage: only {occupied}/{len(sectors)} sectors occupied ({cov*100:.0f}%)",
            ))

        return alerts


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

def export_html(result: RadarResult, path: str) -> None:
    """Generate an interactive HTML radar display."""
    validate_output_path(path)

    R = 270  # radar circle radius in pixels
    CX, CY = 300, 310  # center of radar in SVG
    origin = result.origin
    max_range = result.max_range or 1.0

    def to_radar(x: float, y: float) -> Tuple[float, float]:
        """Convert data coords to radar SVG coords."""
        dx = x - origin[0]
        dy = origin[1] - y  # flip y
        dist = math.hypot(dx, dy)
        scale = R / max_range if max_range > 0 else 1
        if dist == 0:
            return (CX, CY)
        rx = CX + dx * scale
        ry = CY - dy * scale
        return (rx, ry)

    # Build blip SVG elements
    blips_svg = []
    for t in result.targets:
        rx, ry = to_radar(t.x, t.y)
        sector = result.sectors[t.sector]
        color = {"dense": "#ff4444", "moderate": "#ffaa00", "void": "#00ff41", "normal": "#00ff41"}[sector.status]
        esc_title = _html.escape(f"({t.x:.1f}, {t.y:.1f}) d={t.distance:.1f} brg={t.bearing:.0f}°")
        blips_svg.append(
            f'<circle cx="{rx:.1f}" cy="{ry:.1f}" r="4" fill="{color}" opacity="0.85">'
            f'<title>{esc_title}</title></circle>'
        )

    # Range ring SVG
    rings_svg = []
    for i in range(1, result.num_rings + 1):
        r = R * i / result.num_rings
        dist_label = result.max_range * i / result.num_rings
        rings_svg.append(f'<circle cx="{CX}" cy="{CY}" r="{r:.1f}" fill="none" stroke="#00ff41" stroke-opacity="0.25" stroke-width="1"/>')
        rings_svg.append(f'<text x="{CX + r + 3:.1f}" y="{CY + 4}" fill="#00ff41" font-size="10" opacity="0.5">{dist_label:.0f}</text>')

    # Sector divider SVG
    sector_svg = []
    for i in range(result.num_sectors):
        angle_deg = 360.0 * i / result.num_sectors
        angle_rad = math.radians(angle_deg - 90)  # SVG: 0° is right, so offset
        # For compass: 0°=N (up), clockwise
        # In SVG: up = -90° from standard
        rad = math.radians(angle_deg)
        ex = CX + R * math.sin(rad)
        ey = CY - R * math.cos(rad)
        sector_svg.append(f'<line x1="{CX}" y1="{CY}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#00ff41" stroke-opacity="0.12" stroke-width="1"/>')

    # Compass labels
    compass = [("N", 0), ("E", 90), ("S", 180), ("W", 270)]
    compass_svg = []
    for label, deg in compass:
        rad = math.radians(deg)
        tx = CX + (R + 18) * math.sin(rad)
        ty = CY - (R + 18) * math.cos(rad)
        compass_svg.append(f'<text x="{tx:.1f}" y="{ty:.1f}" fill="#00ff41" font-size="14" text-anchor="middle" dominant-baseline="central" opacity="0.7">{label}</text>')

    # Sector table rows
    sector_rows = []
    for s in result.sectors:
        icon = {"dense": "🔴", "moderate": "🟡", "void": "⚫", "normal": "🟢"}[s.status]
        sector_rows.append(
            f"<tr><td>S{s.index}</td><td>{s.bearing_start:.0f}°–{s.bearing_end:.0f}°</td>"
            f"<td>{s.count}</td><td>{s.z_score:+.2f}</td><td>{icon} {s.status}</td></tr>"
        )

    # Ring table rows
    ring_rows = []
    for r in result.rings:
        bar_w = min(r.pct * 2, 200)
        ring_rows.append(
            f"<tr><td>{r.index}</td><td>{r.dist_min:.1f}–{r.dist_max:.1f}</td>"
            f"<td>{r.count}</td><td><div style='background:#00ff41;height:12px;width:{bar_w}px;border-radius:3px;display:inline-block'></div> {r.pct:.1f}%</td></tr>"
        )

    # Alerts HTML
    alerts_html = []
    for a in result.alerts:
        icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(a.severity, "⚪")
        bg = {"critical": "#3a1111", "warning": "#3a2f11", "info": "#0a1f0a"}.get(a.severity, "#1a1a1a")
        border = {"critical": "#ff4444", "warning": "#ffaa00", "info": "#00ff41"}.get(a.severity, "#555")
        alerts_html.append(
            f'<div style="background:{bg};border-left:3px solid {border};padding:8px 12px;margin:4px 0;border-radius:4px">'
            f'{icon} <strong>[{_html.escape(a.severity.upper())}]</strong> {_html.escape(a.message)}</div>'
        )

    st = result.stats
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spatial Radar — VoronoiMap</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0a;color:#c8c8c8;font-family:'Courier New',monospace;padding:20px}}
h1{{color:#00ff41;font-size:22px;margin-bottom:4px}}
h2{{color:#00ff41;font-size:16px;margin:18px 0 8px}}
.subtitle{{color:#888;font-size:13px;margin-bottom:16px}}
.radar-wrap{{display:flex;justify-content:center;margin:20px 0}}
svg{{background:#0a0a0a}}
table{{border-collapse:collapse;width:100%;max-width:700px;margin:8px 0}}
th,td{{border:1px solid #1a3a1a;padding:5px 10px;text-align:left;font-size:13px}}
th{{background:#0f1f0f;color:#00ff41}}
tr:hover{{background:#0f1f0f}}
.stats-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0;max-width:700px}}
.stat-card{{background:#0f1f0f;border:1px solid #1a3a1a;border-radius:6px;padding:10px 14px}}
.stat-card .label{{color:#888;font-size:11px}}
.stat-card .value{{color:#00ff41;font-size:20px;font-weight:bold}}
.sweep-line{{transform-origin:{CX}px {CY}px;animation:sweep 4s linear infinite}}
@keyframes sweep{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}
.sweep-gradient{{transform-origin:{CX}px {CY}px;animation:sweep 4s linear infinite}}
</style>
</head>
<body>
<h1>📡 Spatial Radar</h1>
<div class="subtitle">VoronoiMap Autonomous Radar System — {len(result.targets)} targets detected</div>

<div class="stats-grid">
<div class="stat-card"><div class="label">Targets</div><div class="value">{st['total_targets']}</div></div>
<div class="stat-card"><div class="label">Mean Distance</div><div class="value">{st['mean_dist']}</div></div>
<div class="stat-card"><div class="label">Median Distance</div><div class="value">{st['median_dist']}</div></div>
<div class="stat-card"><div class="label">Coverage</div><div class="value">{st['coverage_pct']}%</div></div>
<div class="stat-card"><div class="label">Mean NN Dist</div><div class="value">{st['mean_nn_dist']}</div></div>
<div class="stat-card"><div class="label">Alerts</div><div class="value">{len(result.alerts)}</div></div>
</div>

<div class="radar-wrap">
<svg width="620" height="640" viewBox="0 0 620 640">
<!-- background circle -->
<circle cx="{CX}" cy="{CY}" r="{R}" fill="#050a05" stroke="#00ff41" stroke-opacity="0.4" stroke-width="1.5"/>
<!-- range rings -->
{''.join(rings_svg)}
<!-- sector dividers -->
{''.join(sector_svg)}
<!-- compass -->
{''.join(compass_svg)}
<!-- crosshair -->
<line x1="{CX-8}" y1="{CY}" x2="{CX+8}" y2="{CY}" stroke="#00ff41" stroke-width="1" opacity="0.6"/>
<line x1="{CX}" y1="{CY-8}" x2="{CX}" y2="{CY+8}" stroke="#00ff41" stroke-width="1" opacity="0.6"/>
<circle cx="{CX}" cy="{CY}" r="3" fill="#00ff41" opacity="0.8"/>
<!-- sweep line -->
<line x1="{CX}" y1="{CY}" x2="{CX}" y2="{CY - R}" stroke="url(#sweepGrad)" stroke-width="2" class="sweep-line"/>
<defs>
<linearGradient id="sweepGrad" x1="0" y1="1" x2="0" y2="0">
<stop offset="0%" stop-color="#00ff41" stop-opacity="0"/>
<stop offset="100%" stop-color="#00ff41" stop-opacity="0.8"/>
</linearGradient>
<!-- sweep trail -->
<radialGradient id="sweepTrail" cx="50%" cy="50%" r="50%">
<stop offset="0%" stop-color="#00ff41" stop-opacity="0"/>
<stop offset="100%" stop-color="#00ff41" stop-opacity="0.06"/>
</radialGradient>
</defs>
<!-- blips -->
{''.join(blips_svg)}
</svg>
</div>

<h2>🔔 Alerts ({len(result.alerts)})</h2>
{''.join(alerts_html) if alerts_html else '<div style="color:#555">No alerts — all clear.</div>'}

<h2>📊 Sector Analysis</h2>
<table>
<tr><th>Sector</th><th>Bearing</th><th>Count</th><th>Z-Score</th><th>Status</th></tr>
{''.join(sector_rows)}
</table>

<h2>🎯 Range Rings</h2>
<table>
<tr><th>Ring</th><th>Distance</th><th>Count</th><th>Distribution</th></tr>
{''.join(ring_rows)}
</table>

<div style="color:#555;font-size:11px;margin-top:24px;text-align:center">
VoronoiMap Spatial Radar · Origin ({result.origin[0]:.1f}, {result.origin[1]:.1f}) · Range {result.max_range:.1f}
</div>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"📡 Radar HTML → {path}")


# ---------------------------------------------------------------------------
# Point loader (same pattern as other vormap_* tools)
# ---------------------------------------------------------------------------

def _load_points(path: str) -> Tuple[List[Tuple[float, float]], float, float]:
    """Load points from text file (x y per line or x,y)."""
    pts: List[Tuple[float, float]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    if not pts:
        raise ValueError(f"No points found in {path}")
    bb = _bounding_box(pts)
    w = bb[2] - bb[0]
    h = bb[3] - bb[1]
    margin = max(w, h) * 0.1
    return pts, w + margin, h + margin


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Autonomous Spatial Radar System for Voronoi Territories"
    )
    ap.add_argument("input", help="Point data file (x y per line)")
    ap.add_argument("--output", "-o", default="radar_report.html", help="Output HTML path")
    ap.add_argument("--json", default=None, help="Also export JSON report")
    ap.add_argument("--origin", default=None, help="Radar origin as x,y (default: centroid)")
    ap.add_argument("--rings", type=int, default=5, help="Number of range rings (default 5)")
    ap.add_argument("--sectors", type=int, default=12, help="Number of angular sectors (default 12)")
    ap.add_argument("--range", type=float, default=None, dest="max_range", help="Max radar range")
    ap.add_argument("--width", type=float, default=None, help="Override canvas width")
    ap.add_argument("--height", type=float, default=None, help="Override canvas height")

    args = ap.parse_args()

    print("📡 VoronoiMap Spatial Radar")
    print("=" * 40)

    pts, w, h = _load_points(args.input)
    if args.width:
        w = args.width
    if args.height:
        h = args.height

    origin = None
    if args.origin:
        parts = args.origin.replace(",", " ").split()
        if len(parts) == 2:
            origin = (float(parts[0]), float(parts[1]))

    station = RadarStation(
        pts, width=w, height=h,
        origin=origin,
        rings=args.rings,
        sectors=args.sectors,
        max_range=args.max_range,
    )
    result = station.scan()

    print(result.summary())
    print()

    export_html(result, args.output)
    if args.json:
        result.to_json(args.json)

    print("=" * 40)
    print("✅ Radar scan complete!")


if __name__ == "__main__":
    main()
