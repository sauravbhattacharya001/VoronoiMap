#!/usr/bin/env python3
"""Spatial Weather Engine -- autonomous atmospheric simulation on Voronoi tessellations.

Models weather-like phenomena across Voronoi cells: temperature fields,
pressure systems, wind flows, precipitation, storms, and weather fronts.
Each cell acts as a weather station with full atmospheric properties,
enabling spatial meteorological analysis on arbitrary point sets.

Seven analysis engines:

- **Temperature Field Engine** -- Assigns temperature from latitude-like
  gradient (higher y = cooler), elevation proxy (larger area = cooler),
  and neighbor smoothing.  Produces a continuous thermal field.
- **Pressure System Detector** -- Computes atmospheric pressure (inverse
  of temperature), classifies cells as High (H) / Low (L) / neutral,
  and detects pressure-system centers (local extrema).
- **Wind Flow Computer** -- Derives wind vectors from pressure gradients
  between neighbors.  Speed ~ delta-P / distance.  Detects convergence and
  divergence zones.
- **Humidity & Precipitation Engine** -- Models humidity from boundary
  distance (coastal proxy) and temperature.  Precipitation triggers
  when humidity x orographic lift exceeds saturation threshold.
- **Storm Detector** -- Clusters low-pressure, high-wind, high-precip
  cells into storm systems.  Severity: mild / moderate / severe /
  extreme based on composite scoring.
- **Front Detector** -- Finds cell boundaries with large temperature
  differences.  Classifies warm vs cold fronts by relative position.
- **Autonomous Insight Generator** -- Climate-zone classification per
  cell (tropical / temperate / arid / polar), natural-language insights,
  and a composite health score 0-100.

Usage (Python API)::

    from vormap_weather import WeatherEngine, weather_analyze, weather_demo

    # Quick one-liner
    result = weather_analyze("points.txt")
    print(f"Health: {result.health_score:.1f}/100")

    # Detailed API
    engine = WeatherEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze()
    engine.to_html("weather.html")

    # Demo
    weather_demo()

CLI::

    python vormap_weather.py points.txt
    python vormap_weather.py points.txt --json out.json --html dash.html
    python vormap_weather.py --demo
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
class CellWeather:
    """Atmospheric profile for a single spatial cell."""
    cell_id: int
    x: float
    y: float
    temperature: float = 0.0
    pressure: float = 0.0
    humidity: float = 0.0
    precipitation: float = 0.0
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    climate_zone: str = "temperate"
    pressure_type: str = "neutral"
    is_storm: bool = False


@dataclass
class WindFlow:
    """A directed wind flow between two cells."""
    source: int
    target: int
    speed: float
    direction: float
    pressure_diff: float


@dataclass
class WeatherFront:
    """A weather front between two cells."""
    cell_a: int
    cell_b: int
    front_type: str  # "warm" or "cold"
    temp_diff: float
    strength: float


@dataclass
class StormCell:
    """A detected storm system."""
    center: int
    cells: List[int] = field(default_factory=list)
    severity: str = "mild"
    max_wind: float = 0.0
    total_precipitation: float = 0.0
    pressure_drop: float = 0.0


@dataclass
class WeatherResult:
    """Full weather analysis result."""
    cells: List[CellWeather] = field(default_factory=list)
    wind_flows: List[WindFlow] = field(default_factory=list)
    fronts: List[WeatherFront] = field(default_factory=list)
    storms: List[StormCell] = field(default_factory=list)
    pressure_centers_high: List[int] = field(default_factory=list)
    pressure_centers_low: List[int] = field(default_factory=list)
    avg_temperature: float = 0.0
    avg_pressure: float = 0.0
    avg_humidity: float = 0.0
    total_precipitation: float = 0.0
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
# Engine 1: Temperature Field
# ---------------------------------------------------------------------------


def _compute_temperatures(
    points: List[Tuple[float, float]],
    areas: List[float],
    adj: Dict[int, List[int]],
    seed: int = 42,
) -> List[float]:
    """Temperature from latitude gradient + elevation proxy + smoothing.

    Higher y => cooler (latitude effect).
    Larger area => cooler (elevation proxy).
    Result smoothed by neighbor averaging.
    Base range: ~5 to ~35 degrees.
    """
    n = len(points)
    if n == 0:
        return []

    rng = random.Random(seed)

    # Normalize y-coordinates (higher y = further north = cooler)
    ys = [p[1] for p in points]
    y_norm = _normalize(ys)  # 0 = lowest y, 1 = highest y

    # Normalize areas (larger = higher elevation = cooler)
    a_norm = _normalize(areas)

    # Raw temperature: warm at low y, cool at high y
    raw = []
    for i in range(n):
        lat_effect = (1.0 - y_norm[i]) * 25.0  # 0-25 degrees from latitude
        elev_effect = a_norm[i] * 8.0           # 0-8 degree cooling from elevation
        noise = rng.uniform(-2.0, 2.0)
        temp = 10.0 + lat_effect - elev_effect + noise
        raw.append(temp)

    # Smooth by neighbor averaging (one pass)
    temps = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if nbrs:
            nbr_avg = sum(raw[j] for j in nbrs) / len(nbrs)
            temps.append(raw[i] * 0.7 + nbr_avg * 0.3)
        else:
            temps.append(raw[i])
    return temps


# ---------------------------------------------------------------------------
# Engine 2: Pressure System Detector
# ---------------------------------------------------------------------------


def _detect_pressure_systems(
    points: List[Tuple[float, float]],
    temperatures: List[float],
    areas: List[float],
    adj: Dict[int, List[int]],
) -> Tuple[List[float], List[str], List[int], List[int]]:
    """Pressure from temperature (inverse) + area. Detect H/L centers.

    Returns (pressures, types, high_centers, low_centers).
    """
    n = len(points)
    if n == 0:
        return [], [], [], []

    # Pressure inversely related to temperature
    t_norm = _normalize(temperatures)
    a_norm = _normalize(areas)

    pressures = []
    for i in range(n):
        # Cool air = high pressure, warm air = low pressure
        base = 1013.0 + (1.0 - t_norm[i]) * 30.0 - t_norm[i] * 15.0
        # Larger cells (higher elevation) have lower pressure
        elev = a_norm[i] * 10.0
        pressures.append(base - elev)

    # Classify relative to mean
    mean_p = sum(pressures) / n if n > 0 else 0.0
    std_p = math.sqrt(sum((p - mean_p) ** 2 for p in pressures) / n) if n > 1 else 1.0
    std_p = max(std_p, 1e-6)

    types: List[str] = []
    for p in pressures:
        z = (p - mean_p) / std_p
        if z > 0.5:
            types.append("H")
        elif z < -0.5:
            types.append("L")
        else:
            types.append("neutral")

    # Detect centers: local extrema among neighbors
    high_centers: List[int] = []
    low_centers: List[int] = []
    for i in range(n):
        nbrs = adj.get(i, [])
        if not nbrs:
            continue
        if all(pressures[i] >= pressures[j] for j in nbrs):
            high_centers.append(i)
        if all(pressures[i] <= pressures[j] for j in nbrs):
            low_centers.append(i)

    return pressures, types, high_centers, low_centers


# ---------------------------------------------------------------------------
# Engine 3: Wind Flow Computer
# ---------------------------------------------------------------------------


def _compute_wind_flows(
    points: List[Tuple[float, float]],
    pressures: List[float],
    adj: Dict[int, List[int]],
) -> Tuple[List[WindFlow], List[float], List[float]]:
    """Wind from pressure gradient. Returns (flows, wind_speeds, wind_dirs)."""
    n = len(points)
    flows: List[WindFlow] = []
    wind_speeds = [0.0] * n
    wind_dirs = [0.0] * n

    # For each cell, compute net wind vector from pressure gradients
    for i in range(n):
        nbrs = adj.get(i, [])
        if not nbrs:
            continue
        # Wind blows from high pressure to low pressure
        vx, vy = 0.0, 0.0
        for j in nbrs:
            dp = pressures[i] - pressures[j]
            if dp > 0:
                # Wind flows from i to j
                dist = max(_euclidean(points[i], points[j]), 1e-6)
                speed = dp / dist
                dx = points[j][0] - points[i][0]
                dy = points[j][1] - points[i][1]
                norm = max(math.sqrt(dx * dx + dy * dy), 1e-6)
                vx += speed * dx / norm
                vy += speed * dy / norm
                flows.append(WindFlow(
                    source=i, target=j, speed=speed,
                    direction=math.degrees(math.atan2(dy, dx)) % 360,
                    pressure_diff=dp,
                ))
        wind_speeds[i] = math.sqrt(vx * vx + vy * vy)
        wind_dirs[i] = math.degrees(math.atan2(vy, vx)) % 360

    return flows, wind_speeds, wind_dirs


# ---------------------------------------------------------------------------
# Engine 4: Humidity & Precipitation
# ---------------------------------------------------------------------------


def _compute_precipitation(
    points: List[Tuple[float, float]],
    temperatures: List[float],
    areas: List[float],
    adj: Dict[int, List[int]],
    seed: int = 42,
) -> Tuple[List[float], List[float]]:
    """Humidity from boundary proximity + temperature. Precipitation from saturation.

    Returns (humidities, precipitations).
    """
    n = len(points)
    if n == 0:
        return [], []

    rng = random.Random(seed + 1)

    # Boundary distance proxy: cells with fewer neighbors are closer to edge
    max_nbrs = max(len(adj.get(i, [])) for i in range(n)) if n > 0 else 1
    max_nbrs = max(max_nbrs, 1)

    # Temperature normalization
    t_norm = _normalize(temperatures)

    humidities = []
    precipitations = []
    a_norm = _normalize(areas)

    for i in range(n):
        nbr_count = len(adj.get(i, []))
        # Cells near boundary (fewer neighbors) = coastal = more humid
        coastal = 1.0 - (nbr_count / max_nbrs)
        # Warmer air holds more moisture
        warmth = t_norm[i] * 0.4
        noise = rng.uniform(-0.05, 0.05)
        humidity = min(1.0, max(0.0, 0.3 + coastal * 0.3 + warmth + noise))
        humidities.append(humidity)

        # Orographic lift: if neighbors have larger areas (higher elevation)
        nbrs = adj.get(i, [])
        lift = 0.0
        if nbrs:
            avg_nbr_area = sum(a_norm[j] for j in nbrs) / len(nbrs)
            lift = max(0.0, a_norm[i] - avg_nbr_area) * 0.5

        # Precipitation when humidity is high enough and lift occurs
        saturation = humidity + lift
        if saturation > 0.7:
            precip = (saturation - 0.7) * 30.0  # mm equivalent
        else:
            precip = 0.0
        precipitations.append(precip)

    return humidities, precipitations


# ---------------------------------------------------------------------------
# Engine 5: Storm Detector
# ---------------------------------------------------------------------------


def _detect_storms(
    points: List[Tuple[float, float]],
    pressures: List[float],
    pressure_types: List[str],
    wind_speeds: List[float],
    precipitations: List[float],
    adj: Dict[int, List[int]],
) -> List[StormCell]:
    """Cluster low-pressure, high-wind cells into storm systems."""
    n = len(points)
    if n == 0:
        return []

    # Identify storm-candidate cells
    w_norm = _normalize(wind_speeds)
    p_norm = _normalize(precipitations)

    candidates = set()
    for i in range(n):
        is_low = pressure_types[i] == "L"
        high_wind = w_norm[i] > 0.5 if max(wind_speeds) > 0 else False
        has_precip = precipitations[i] > 0
        if is_low and (high_wind or has_precip):
            candidates.add(i)

    # Cluster candidates using BFS
    visited = set()
    storms: List[StormCell] = []
    for seed_cell in sorted(candidates):
        if seed_cell in visited:
            continue
        cluster = []
        queue = [seed_cell]
        visited.add(seed_cell)
        while queue:
            cell = queue.pop(0)
            cluster.append(cell)
            for j in adj.get(cell, []):
                if j in candidates and j not in visited:
                    visited.add(j)
                    queue.append(j)

        if not cluster:
            continue

        # Find center (lowest pressure in cluster)
        center = min(cluster, key=lambda c: pressures[c])
        max_wind = max(wind_speeds[c] for c in cluster)
        total_precip = sum(precipitations[c] for c in cluster)
        pressure_drop = max(pressures) - pressures[center] if pressures else 0.0

        # Severity scoring
        score = 0.0
        score += min(1.0, max_wind / max(max(wind_speeds), 1e-6)) * 40.0
        score += min(1.0, total_precip / max(sum(precipitations), 1e-6)) * 30.0
        score += min(1.0, len(cluster) / max(n * 0.3, 1)) * 30.0

        if score >= 75:
            severity = "extreme"
        elif score >= 50:
            severity = "severe"
        elif score >= 25:
            severity = "moderate"
        else:
            severity = "mild"

        storms.append(StormCell(
            center=center,
            cells=cluster,
            severity=severity,
            max_wind=max_wind,
            total_precipitation=total_precip,
            pressure_drop=pressure_drop,
        ))

    return storms


# ---------------------------------------------------------------------------
# Engine 6: Front Detector
# ---------------------------------------------------------------------------


def _detect_fronts(
    points: List[Tuple[float, float]],
    temperatures: List[float],
    adj: Dict[int, List[int]],
) -> List[WeatherFront]:
    """Find weather fronts: boundaries with large temperature differences."""
    n = len(points)
    if n == 0:
        return []

    # Threshold: temperature differences > 1 std dev
    if n < 2:
        return []
    mean_t = sum(temperatures) / n
    std_t = math.sqrt(sum((t - mean_t) ** 2 for t in temperatures) / n)
    threshold = max(std_t * 0.8, 1.0)

    fronts: List[WeatherFront] = []
    seen = set()
    for i in range(n):
        for j in adj.get(i, []):
            pair = (min(i, j), max(i, j))
            if pair in seen:
                continue
            seen.add(pair)
            diff = abs(temperatures[i] - temperatures[j])
            if diff < threshold:
                continue

            # Determine front type by which cell is warmer and position
            if temperatures[i] > temperatures[j]:
                warm, cold = i, j
            else:
                warm, cold = j, i

            # Warm front: warm air advancing (warm cell has neighbors in cold direction)
            # Simple heuristic: compare y positions (warm from south advancing north = warm front)
            if points[warm][1] < points[cold][1]:
                front_type = "warm"
            else:
                front_type = "cold"

            strength = min(1.0, diff / max(std_t * 2.0, 1.0))
            fronts.append(WeatherFront(
                cell_a=i, cell_b=j,
                front_type=front_type,
                temp_diff=diff,
                strength=strength,
            ))

    return fronts


# ---------------------------------------------------------------------------
# Engine 7: Autonomous Insight Generator
# ---------------------------------------------------------------------------


def _classify_climate(temperature: float, humidity: float, precipitation: float) -> str:
    """Classify cell climate zone."""
    if temperature > 28 and humidity > 0.6:
        return "tropical"
    if temperature < 8:
        return "polar"
    if humidity < 0.3 and precipitation < 1.0:
        return "arid"
    return "temperate"


def _generate_insights(
    cells: List[CellWeather],
    storms: List[StormCell],
    fronts: List[WeatherFront],
    high_centers: List[int],
    low_centers: List[int],
    avg_temp: float,
    total_precip: float,
) -> List[str]:
    """Generate natural-language weather insights."""
    insights: List[str] = []
    if not cells:
        return ["No cells to analyze."]

    n = len(cells)

    # Temperature summary
    hottest = max(cells, key=lambda c: c.temperature)
    coldest = min(cells, key=lambda c: c.temperature)
    insights.append(
        f"Temperature range: {coldest.temperature:.1f} deg (Cell {coldest.cell_id}) "
        f"to {hottest.temperature:.1f} deg (Cell {hottest.cell_id}), "
        f"mean {avg_temp:.1f} deg")

    # Pressure systems
    insights.append(
        f"Pressure systems: {len(high_centers)} high-pressure centers, "
        f"{len(low_centers)} low-pressure centers")

    # Wind
    windiest = max(cells, key=lambda c: c.wind_speed)
    if windiest.wind_speed > 0:
        insights.append(
            f"Strongest winds at Cell {windiest.cell_id} "
            f"(speed={windiest.wind_speed:.2f})")

    # Precipitation
    if total_precip > 0:
        wettest = max(cells, key=lambda c: c.precipitation)
        insights.append(
            f"Total precipitation: {total_precip:.1f}mm, "
            f"wettest cell: {wettest.cell_id} ({wettest.precipitation:.1f}mm)")
    else:
        insights.append("No precipitation detected -- dry conditions prevail")

    # Storms
    if storms:
        severe = [s for s in storms if s.severity in ("severe", "extreme")]
        insights.append(
            f"{len(storms)} storm system(s) detected"
            + (f", {len(severe)} severe/extreme" if severe else ""))
        worst = max(storms, key=lambda s: s.max_wind)
        insights.append(
            f"Strongest storm centered at Cell {worst.center} "
            f"(severity={worst.severity}, max wind={worst.max_wind:.2f})")
    else:
        insights.append("No storm systems detected -- calm conditions")

    # Fronts
    if fronts:
        warm = sum(1 for f in fronts if f.front_type == "warm")
        cold = sum(1 for f in fronts if f.front_type == "cold")
        insights.append(f"Weather fronts: {warm} warm, {cold} cold")
        strongest = max(fronts, key=lambda f: f.strength)
        insights.append(
            f"Strongest front between Cells {strongest.cell_a} and "
            f"{strongest.cell_b} (delta-T={strongest.temp_diff:.1f} deg, "
            f"strength={strongest.strength:.2f})")
    else:
        insights.append("No significant weather fronts detected")

    # Climate zone distribution
    zones: Dict[str, int] = {}
    for c in cells:
        zones[c.climate_zone] = zones.get(c.climate_zone, 0) + 1
    zone_str = ", ".join(f"{k}: {v}" for k, v in sorted(zones.items()))
    insights.append(f"Climate zones: {zone_str}")

    # Humidity
    avg_hum = sum(c.humidity for c in cells) / n
    if avg_hum > 0.7:
        insights.append(f"High average humidity ({avg_hum:.2f}) -- muggy conditions")
    elif avg_hum < 0.3:
        insights.append(f"Low average humidity ({avg_hum:.2f}) -- dry air mass dominates")
    else:
        insights.append(f"Average humidity: {avg_hum:.2f}")

    return insights


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------


class WeatherEngine:
    """Spatial Weather Engine -- autonomous atmospheric simulation."""

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
        self._result: Optional[WeatherResult] = None

    def analyze(self) -> WeatherResult:
        """Run full weather analysis pipeline."""
        pts = self._points
        n = len(pts)
        if n < 2:
            self._result = WeatherResult(
                health_score=0.0,
                insights=["Need at least 2 points for analysis."],
            )
            return self._result

        # Build spatial structures
        adj = _knn_adjacency(pts, self._adj_k)
        areas = _voronoi_areas(pts, adj)

        # Engine 1: Temperature
        temperatures = _compute_temperatures(pts, areas, adj, self._seed)

        # Engine 2: Pressure
        pressures, p_types, hi_centers, lo_centers = _detect_pressure_systems(
            pts, temperatures, areas, adj)

        # Engine 3: Wind
        wind_flows, wind_speeds, wind_dirs = _compute_wind_flows(
            pts, pressures, adj)

        # Engine 4: Humidity & Precipitation
        humidities, precipitations = _compute_precipitation(
            pts, temperatures, areas, adj, self._seed)

        # Engine 5: Storms
        storms = _detect_storms(
            pts, pressures, p_types, wind_speeds, precipitations, adj)

        # Mark storm cells
        storm_cells_set = set()
        for s in storms:
            storm_cells_set.update(s.cells)

        # Engine 6: Fronts
        fronts = _detect_fronts(pts, temperatures, adj)

        # Build cell objects
        avg_temp = sum(temperatures) / n
        avg_pres = sum(pressures) / n
        avg_hum = sum(humidities) / n
        total_precip = sum(precipitations)

        cells: List[CellWeather] = []
        for i in range(n):
            zone = _classify_climate(temperatures[i], humidities[i], precipitations[i])
            cells.append(CellWeather(
                cell_id=i,
                x=pts[i][0],
                y=pts[i][1],
                temperature=temperatures[i],
                pressure=pressures[i],
                humidity=humidities[i],
                precipitation=precipitations[i],
                wind_speed=wind_speeds[i],
                wind_direction=wind_dirs[i],
                climate_zone=zone,
                pressure_type=p_types[i],
                is_storm=i in storm_cells_set,
            ))

        # Engine 7: Insights
        insights = _generate_insights(
            cells, storms, fronts, hi_centers, lo_centers,
            avg_temp, total_precip)

        # Health score: stability-based
        # Fewer storms = healthier, more uniform temp = healthier
        storm_ratio = len(storm_cells_set) / max(n, 1)
        storm_score = (1.0 - storm_ratio) * 100.0

        temp_std = math.sqrt(sum((t - avg_temp) ** 2 for t in temperatures) / n) if n > 1 else 0.0
        temp_range = max(temperatures) - min(temperatures) if temperatures else 0.0
        temp_uniformity = max(0.0, 100.0 - temp_std * 5.0)

        p_gini = _gini([abs(p - avg_pres) for p in pressures])
        pressure_balance = (1.0 - p_gini) * 100.0

        front_ratio = len(fronts) / max(n, 1)
        front_score = max(0.0, 100.0 - front_ratio * 200.0)

        health = (
            storm_score * 0.35
            + temp_uniformity * 0.25
            + pressure_balance * 0.25
            + front_score * 0.15
        )
        health = max(0.0, min(100.0, health))

        self._result = WeatherResult(
            cells=cells,
            wind_flows=wind_flows,
            fronts=fronts,
            storms=storms,
            pressure_centers_high=hi_centers,
            pressure_centers_low=lo_centers,
            avg_temperature=avg_temp,
            avg_pressure=avg_pres,
            avg_humidity=avg_hum,
            total_precipitation=total_precip,
            health_score=health,
            insights=insights,
        )
        return self._result

    def to_json(self, path: str) -> None:
        """Export result to JSON."""
        if not self._result:
            self.analyze()
        assert self._result is not None
        r = self._result
        data = {
            "health_score": r.health_score,
            "avg_temperature": r.avg_temperature,
            "avg_pressure": r.avg_pressure,
            "avg_humidity": r.avg_humidity,
            "total_precipitation": r.total_precipitation,
            "pressure_centers_high": r.pressure_centers_high,
            "pressure_centers_low": r.pressure_centers_low,
            "insights": r.insights,
            "cells": [asdict(c) for c in r.cells],
            "wind_flows": [asdict(f) for f in r.wind_flows],
            "fronts": [asdict(f) for f in r.fronts],
            "storms": [asdict(s) for s in r.storms],
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

        # Cell table rows
        rows_cells = ""
        for c in r.cells:
            storm_icon = "[STORM]" if c.is_storm else ""
            rows_cells += (
                f"<tr><td>{c.cell_id}</td>"
                f"<td>{c.x:.2f}</td><td>{c.y:.2f}</td>"
                f"<td>{c.temperature:.1f} deg</td>"
                f"<td>{c.pressure:.1f}</td>"
                f"<td>{c.pressure_type}</td>"
                f"<td>{c.humidity:.2f}</td>"
                f"<td>{c.precipitation:.1f}</td>"
                f"<td>{c.wind_speed:.2f}</td>"
                f"<td>{c.wind_direction:.0f} deg</td>"
                f"<td>{esc(c.climate_zone)}</td>"
                f"<td>{storm_icon}</td></tr>\n"
            )

        # Storm table rows
        rows_storms = ""
        for s in r.storms:
            rows_storms += (
                f"<tr><td>{s.center}</td>"
                f"<td>{len(s.cells)}</td>"
                f"<td>{esc(s.severity)}</td>"
                f"<td>{s.max_wind:.2f}</td>"
                f"<td>{s.total_precipitation:.1f}</td>"
                f"<td>{s.pressure_drop:.1f}</td></tr>\n"
            )

        # Front table rows
        rows_fronts = ""
        for f in r.fronts:
            rows_fronts += (
                f"<tr><td>{f.cell_a}</td><td>{f.cell_b}</td>"
                f"<td>{esc(f.front_type)}</td>"
                f"<td>{f.temp_diff:.1f} deg</td>"
                f"<td>{f.strength:.2f}</td></tr>\n"
            )

        insights_html = "".join(
            f"<li>{esc(ins)}</li>" for ins in r.insights)

        hi_str = ", ".join(str(c) for c in r.pressure_centers_high) or "None"
        lo_str = ", ".join(str(c) for c in r.pressure_centers_low) or "None"

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
<title>Spatial Weather Dashboard</title>
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
<h1>&#x1F326;&#xFE0F; Spatial Weather Dashboard</h1>
<div class="stats">
<div class="stat">{gauge_svg}</div>
<div class="stat"><div class="val">{r.avg_temperature:.1f} deg</div><div class="lbl">Avg Temperature</div></div>
<div class="stat"><div class="val">{r.avg_pressure:.1f}</div><div class="lbl">Avg Pressure</div></div>
<div class="stat"><div class="val">{r.avg_humidity:.2f}</div><div class="lbl">Avg Humidity</div></div>
<div class="stat"><div class="val">{r.total_precipitation:.1f}mm</div><div class="lbl">Total Precipitation</div></div>
<div class="stat"><div class="val">{len(r.storms)}</div><div class="lbl">Storm Systems</div></div>
<div class="stat"><div class="val">{len(r.fronts)}</div><div class="lbl">Weather Fronts</div></div>
</div>
<div class="card"><h2>Pressure Centers</h2>
<p><strong>High (H):</strong> {hi_str}</p>
<p><strong>Low (L):</strong> {lo_str}</p></div>
<div class="card"><h2>Autonomous Insights</h2><ul>{insights_html}</ul></div>
<div class="card"><h2>Cell Weather Stations</h2>
<table><tr><th>ID</th><th>X</th><th>Y</th><th>Temp</th><th>Pressure</th><th>Type</th>
<th>Humidity</th><th>Precip</th><th>Wind Spd</th><th>Wind Dir</th>
<th>Climate</th><th>Storm</th></tr>
{rows_cells}</table></div>
<div class="card"><h2>Storm Systems</h2>
<table><tr><th>Center</th><th>Size</th><th>Severity</th><th>Max Wind</th>
<th>Precipitation</th><th>Pressure Drop</th></tr>
{rows_storms}</table></div>
<div class="card"><h2>Weather Fronts</h2>
<table><tr><th>Cell A</th><th>Cell B</th><th>Type</th><th>delta-T</th><th>Strength</th></tr>
{rows_fronts}</table></div>
</body></html>"""

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html_content)


# ---------------------------------------------------------------------------
# Public convenience API
# ---------------------------------------------------------------------------


def weather_analyze(
    path_or_points,
    adj_k: int = 6,
    seed: int = 42,
) -> WeatherResult:
    """One-liner analysis -- accepts a file path or list of (x, y) tuples."""
    if isinstance(path_or_points, str):
        engine = WeatherEngine(path=path_or_points, adj_k=adj_k, seed=seed)
    else:
        engine = WeatherEngine(
            points=path_or_points, adj_k=adj_k, seed=seed)
    return engine.analyze()


def weather_demo() -> None:
    """Generate demo points, run analysis, print summary."""
    rng = random.Random(42)
    pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(30)]
    engine = WeatherEngine(points=pts, seed=42)
    result = engine.analyze()

    print("=" * 60)
    print("  Spatial Weather Engine -- Demo")
    print("=" * 60)
    print(f"  Points:              {len(pts)}")
    print(f"  Health Score:        {result.health_score:.1f}/100")
    print(f"  Avg Temperature:     {result.avg_temperature:.1f} deg")
    print(f"  Avg Pressure:        {result.avg_pressure:.1f}")
    print(f"  Avg Humidity:        {result.avg_humidity:.2f}")
    print(f"  Total Precipitation: {result.total_precipitation:.1f}mm")
    print(f"  Storm Systems:       {len(result.storms)}")
    print(f"  Weather Fronts:      {len(result.fronts)}")
    print(f"  High-P Centers:      {result.pressure_centers_high}")
    print(f"  Low-P Centers:       {result.pressure_centers_low}")
    print()
    print("  Insights:")
    for ins in result.insights:
        print(f"    * {ins}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    parser = argparse.ArgumentParser(
        description="Spatial Weather Engine -- autonomous atmospheric simulation")
    parser.add_argument("points", nargs="?",
                        help="Path to points file (x y per line)")
    parser.add_argument("--json", dest="json_out",
                        help="Export result to JSON")
    parser.add_argument("--html", dest="html_out",
                        help="Export interactive HTML dashboard")
    parser.add_argument("--adj-k", type=int, default=6,
                        help="K for knn adjacency")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo with generated data")
    args = parser.parse_args()

    if args.demo:
        weather_demo()
        return

    if not args.points:
        parser.error("Must provide points file or --demo")

    engine = WeatherEngine(
        path=args.points, adj_k=args.adj_k, seed=args.seed)
    result = engine.analyze()

    print(f"Weather Health: {result.health_score:.1f}/100")
    print(f"Temperature: {result.avg_temperature:.1f} deg  "
          f"Pressure: {result.avg_pressure:.1f}  "
          f"Humidity: {result.avg_humidity:.2f}")
    print(f"Precipitation: {result.total_precipitation:.1f}mm  "
          f"Storms: {len(result.storms)}  "
          f"Fronts: {len(result.fronts)}")

    for ins in result.insights:
        print(f"  * {ins}")

    if args.json_out:
        engine.to_json(args.json_out)
        print(f"JSON: {args.json_out}")
    if args.html_out:
        engine.to_html(args.html_out)
        print(f"HTML: {args.html_out}")


if __name__ == "__main__":
    _cli()
