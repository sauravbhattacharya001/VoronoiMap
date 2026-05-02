#!/usr/bin/env python3
"""Tests for vormap_weather.py — Spatial Weather Engine (55+ tests)."""

import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_weather import (
    WeatherEngine,
    WeatherResult,
    CellWeather,
    WindFlow,
    WeatherFront,
    StormCell,
    weather_analyze,
    weather_demo,
    _euclidean,
    _knn_adjacency,
    _voronoi_areas,
    _normalize,
    _gini,
    _load_points,
    _compute_temperatures,
    _detect_pressure_systems,
    _compute_wind_flows,
    _compute_precipitation,
    _detect_storms,
    _detect_fronts,
    _classify_climate,
    _generate_insights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestEuclidean(unittest.TestCase):
    def test_zero(self):
        self.assertAlmostEqual(_euclidean((0, 0), (0, 0)), 0.0)

    def test_basic(self):
        self.assertAlmostEqual(_euclidean((0, 0), (3, 4)), 5.0)

    def test_negative(self):
        self.assertAlmostEqual(_euclidean((-1, -1), (2, 3)), 5.0)


class TestLoadPoints(unittest.TestCase):
    def test_basic(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("0 0\n10 0\n5 8\n")
            f.flush()
            pts = _load_points(f.name)
        os.unlink(f.name)
        self.assertEqual(len(pts), 3)

    def test_skip_comments(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# header\n1 2\n# comment\n3 4\n")
            f.flush()
            pts = _load_points(f.name)
        os.unlink(f.name)
        self.assertEqual(len(pts), 2)

    def test_skip_blank(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("\n1 2\n\n3 4\n\n")
            f.flush()
            pts = _load_points(f.name)
        os.unlink(f.name)
        self.assertEqual(len(pts), 2)


class TestKnnAdjacency(unittest.TestCase):
    def test_symmetric(self):
        pts = [(0, 0), (1, 0), (0, 1)]
        adj = _knn_adjacency(pts, k=2)
        for i in adj:
            for j in adj[i]:
                self.assertIn(i, adj[j])

    def test_k_capped(self):
        pts = [(0, 0), (1, 0)]
        adj = _knn_adjacency(pts, k=10)
        self.assertEqual(len(adj[0]), 1)


class TestVoronoiAreas(unittest.TestCase):
    def test_positive(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, k=4)
        areas = _voronoi_areas(pts, adj)
        self.assertEqual(len(areas), 5)
        for a in areas:
            self.assertGreater(a, 0)


class TestNormalize(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_normalize([]), [])

    def test_uniform(self):
        self.assertEqual(_normalize([5, 5, 5]), [0.5, 0.5, 0.5])

    def test_range(self):
        result = _normalize([0, 5, 10])
        self.assertAlmostEqual(result[0], 0.0)
        self.assertAlmostEqual(result[1], 0.5)
        self.assertAlmostEqual(result[2], 1.0)


class TestGini(unittest.TestCase):
    def test_equal(self):
        self.assertAlmostEqual(_gini([1, 1, 1, 1]), 0.0)

    def test_unequal(self):
        g = _gini([0, 0, 0, 10])
        self.assertGreater(g, 0.5)

    def test_empty(self):
        self.assertAlmostEqual(_gini([]), 0.0)


# ---------------------------------------------------------------------------
# Engine 1: Temperature
# ---------------------------------------------------------------------------


class TestTemperatures(unittest.TestCase):
    def test_count(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, 4)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        self.assertEqual(len(temps), 5)

    def test_gradient_direction(self):
        """Higher y should be cooler on average."""
        pts = [(5, 0), (5, 50), (5, 100)]
        adj = _knn_adjacency(pts, 2)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        # Bottom should be warmer than top
        self.assertGreater(temps[0], temps[2])

    def test_deterministic(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        areas = _voronoi_areas(pts, adj)
        t1 = _compute_temperatures(pts, areas, adj, seed=99)
        t2 = _compute_temperatures(pts, areas, adj, seed=99)
        self.assertEqual(t1, t2)

    def test_empty(self):
        self.assertEqual(_compute_temperatures([], [], {}, 42), [])

    def test_reasonable_range(self):
        pts = [(i * 10, j * 10) for i in range(5) for j in range(5)]
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        for t in temps:
            self.assertGreater(t, -10)
            self.assertLess(t, 50)


# ---------------------------------------------------------------------------
# Engine 2: Pressure Systems
# ---------------------------------------------------------------------------


class TestPressureSystems(unittest.TestCase):
    def test_count(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, 4)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        pressures, types, hi, lo = _detect_pressure_systems(pts, temps, areas, adj)
        self.assertEqual(len(pressures), 5)
        self.assertEqual(len(types), 5)

    def test_types_valid(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, 4)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        _, types, _, _ = _detect_pressure_systems(pts, temps, areas, adj)
        for t in types:
            self.assertIn(t, ("H", "L", "neutral"))

    def test_empty(self):
        p, t, h, l = _detect_pressure_systems([], [], [], {})
        self.assertEqual(p, [])

    def test_inverse_relationship(self):
        """Cooler cells should tend toward higher pressure."""
        pts = [(5, 0), (5, 100)]
        adj = _knn_adjacency(pts, 1)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        pressures, _, _, _ = _detect_pressure_systems(pts, temps, areas, adj)
        # Cooler cell (higher y) should have higher pressure
        cool_idx = 1 if temps[1] < temps[0] else 0
        warm_idx = 1 - cool_idx
        self.assertGreater(pressures[cool_idx], pressures[warm_idx])


# ---------------------------------------------------------------------------
# Engine 3: Wind Flows
# ---------------------------------------------------------------------------


class TestWindFlows(unittest.TestCase):
    def test_flows_generated(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, 4)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        pressures, _, _, _ = _detect_pressure_systems(pts, temps, areas, adj)
        flows, speeds, dirs = _compute_wind_flows(pts, pressures, adj)
        self.assertGreater(len(flows), 0)
        self.assertEqual(len(speeds), 5)
        self.assertEqual(len(dirs), 5)

    def test_wind_direction_range(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        pressures, _, _, _ = _detect_pressure_systems(pts, temps, areas, adj)
        _, _, dirs = _compute_wind_flows(pts, pressures, adj)
        for d in dirs:
            self.assertGreaterEqual(d, 0)
            self.assertLess(d, 360)

    def test_wind_from_high_to_low(self):
        """Wind flows should target lower pressure cells."""
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        pressures = [1050.0, 1000.0, 1025.0]  # cell 0 is highest
        flows, _, _ = _compute_wind_flows(pts, pressures, adj)
        # Cell 0 has highest pressure, should have outgoing flows
        outgoing_0 = [f for f in flows if f.source == 0]
        self.assertGreater(len(outgoing_0), 0)


# ---------------------------------------------------------------------------
# Engine 4: Precipitation
# ---------------------------------------------------------------------------


class TestPrecipitation(unittest.TestCase):
    def test_count(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, 4)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        hum, precip = _compute_precipitation(pts, temps, areas, adj, seed=42)
        self.assertEqual(len(hum), 5)
        self.assertEqual(len(precip), 5)

    def test_humidity_range(self):
        pts = [(i * 10, j * 10) for i in range(5) for j in range(5)]
        adj = _knn_adjacency(pts, 6)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        hum, _ = _compute_precipitation(pts, temps, areas, adj, seed=42)
        for h in hum:
            self.assertGreaterEqual(h, 0.0)
            self.assertLessEqual(h, 1.0)

    def test_precip_non_negative(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        _, precip = _compute_precipitation(pts, temps, areas, adj, seed=42)
        for p in precip:
            self.assertGreaterEqual(p, 0.0)

    def test_empty(self):
        hum, precip = _compute_precipitation([], [], [], {}, seed=42)
        self.assertEqual(hum, [])
        self.assertEqual(precip, [])

    def test_deterministic(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        areas = _voronoi_areas(pts, adj)
        temps = _compute_temperatures(pts, areas, adj, seed=42)
        h1, p1 = _compute_precipitation(pts, temps, areas, adj, seed=42)
        h2, p2 = _compute_precipitation(pts, temps, areas, adj, seed=42)
        self.assertEqual(h1, h2)
        self.assertEqual(p1, p2)


# ---------------------------------------------------------------------------
# Engine 5: Storm Detection
# ---------------------------------------------------------------------------


class TestStormDetection(unittest.TestCase):
    def test_no_storms_calm(self):
        """Uniform pressure should produce no storms."""
        pts = [(0, 0), (10, 0), (5, 8)]
        pressures = [1013.0, 1013.0, 1013.0]
        types = ["neutral", "neutral", "neutral"]
        winds = [0.0, 0.0, 0.0]
        precips = [0.0, 0.0, 0.0]
        adj = _knn_adjacency(pts, 2)
        storms = _detect_storms(pts, pressures, types, winds, precips, adj)
        self.assertEqual(len(storms), 0)

    def test_storm_detected(self):
        """Low pressure + high wind + precipitation should create a storm."""
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        adj = _knn_adjacency(pts, 4)
        pressures = [1013.0, 990.0, 988.0, 1013.0, 1013.0]
        types = ["neutral", "L", "L", "neutral", "neutral"]
        winds = [0.5, 5.0, 6.0, 0.3, 0.2]
        precips = [0.0, 10.0, 12.0, 0.0, 0.0]
        storms = _detect_storms(pts, pressures, types, winds, precips, adj)
        self.assertGreater(len(storms), 0)

    def test_severity_values(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        pressures = [1013.0, 980.0, 985.0]
        types = ["neutral", "L", "L"]
        winds = [0.1, 8.0, 7.0]
        precips = [0.0, 15.0, 10.0]
        storms = _detect_storms(pts, pressures, types, winds, precips, adj)
        for s in storms:
            self.assertIn(s.severity, ("mild", "moderate", "severe", "extreme"))

    def test_empty(self):
        storms = _detect_storms([], [], [], [], [], {})
        self.assertEqual(storms, [])


# ---------------------------------------------------------------------------
# Engine 6: Front Detection
# ---------------------------------------------------------------------------


class TestFrontDetection(unittest.TestCase):
    def test_front_found(self):
        """Large temperature difference should create a front."""
        pts = [(0, 0), (10, 0)]
        adj = _knn_adjacency(pts, 1)
        temps = [35.0, 5.0]  # 30 degree difference
        fronts = _detect_fronts(pts, temps, adj)
        self.assertGreater(len(fronts), 0)

    def test_no_front_uniform(self):
        """Uniform temperatures should produce no fronts."""
        pts = [(0, 0), (10, 0), (5, 8)]
        adj = _knn_adjacency(pts, 2)
        temps = [20.0, 20.0, 20.0]
        fronts = _detect_fronts(pts, temps, adj)
        self.assertEqual(len(fronts), 0)

    def test_front_type_valid(self):
        pts = [(0, 0), (0, 10)]
        adj = _knn_adjacency(pts, 1)
        temps = [30.0, 5.0]
        fronts = _detect_fronts(pts, temps, adj)
        for f in fronts:
            self.assertIn(f.front_type, ("warm", "cold"))

    def test_strength_bounded(self):
        pts = [(0, 0), (10, 0)]
        adj = _knn_adjacency(pts, 1)
        temps = [35.0, 5.0]
        fronts = _detect_fronts(pts, temps, adj)
        for f in fronts:
            self.assertGreaterEqual(f.strength, 0.0)
            self.assertLessEqual(f.strength, 1.0)

    def test_empty(self):
        self.assertEqual(_detect_fronts([], [], {}), [])


# ---------------------------------------------------------------------------
# Climate Classification
# ---------------------------------------------------------------------------


class TestClimateClassification(unittest.TestCase):
    def test_tropical(self):
        self.assertEqual(_classify_climate(30.0, 0.8, 5.0), "tropical")

    def test_polar(self):
        self.assertEqual(_classify_climate(5.0, 0.3, 0.0), "polar")

    def test_arid(self):
        self.assertEqual(_classify_climate(25.0, 0.2, 0.0), "arid")

    def test_temperate(self):
        self.assertEqual(_classify_climate(20.0, 0.5, 5.0), "temperate")


# ---------------------------------------------------------------------------
# Engine 7: Insights
# ---------------------------------------------------------------------------


class TestInsights(unittest.TestCase):
    def test_non_empty(self):
        cells = [
            CellWeather(0, 0, 0, temperature=30, pressure=1000, humidity=0.8,
                        precipitation=5.0, wind_speed=3.0, wind_direction=90),
            CellWeather(1, 10, 0, temperature=15, pressure=1020, humidity=0.3,
                        precipitation=0.0, wind_speed=1.0, wind_direction=180),
        ]
        insights = _generate_insights(cells, [], [], [1], [0], 22.5, 5.0)
        self.assertGreater(len(insights), 0)

    def test_empty_cells(self):
        insights = _generate_insights([], [], [], [], [], 0, 0)
        self.assertEqual(insights, ["No cells to analyze."])

    def test_storm_insight(self):
        cells = [
            CellWeather(0, 0, 0, temperature=20, pressure=1000, humidity=0.5,
                        wind_speed=5.0, is_storm=True),
        ]
        storms = [StormCell(center=0, cells=[0], severity="severe",
                            max_wind=5.0, total_precipitation=10.0,
                            pressure_drop=20.0)]
        insights = _generate_insights(cells, storms, [], [], [0], 20, 10)
        storm_text = " ".join(insights)
        self.assertIn("storm", storm_text.lower())


# ---------------------------------------------------------------------------
# WeatherEngine class
# ---------------------------------------------------------------------------


class TestWeatherEngine(unittest.TestCase):
    def _make_engine(self, n=15):
        import random as rnd
        rng = rnd.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(n)]
        return WeatherEngine(points=pts, seed=42)

    def test_analyze_returns_result(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertIsInstance(result, WeatherResult)

    def test_health_score_bounded(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertGreaterEqual(result.health_score, 0)
        self.assertLessEqual(result.health_score, 100)

    def test_cells_populated(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertEqual(len(result.cells), 15)

    def test_wind_flows_present(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertGreater(len(result.wind_flows), 0)

    def test_insights_present(self):
        engine = self._make_engine()
        result = engine.analyze()
        self.assertGreater(len(result.insights), 0)

    def test_minimal_points(self):
        engine = WeatherEngine(points=[(0, 0)])
        result = engine.analyze()
        self.assertAlmostEqual(result.health_score, 0.0)

    def test_two_points(self):
        engine = WeatherEngine(points=[(0, 0), (10, 0)])
        result = engine.analyze()
        self.assertEqual(len(result.cells), 2)

    def test_to_json(self):
        engine = self._make_engine()
        engine.analyze()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        engine.to_json(path)
        with open(path) as fh:
            data = json.load(fh)
        os.unlink(path)
        self.assertIn("health_score", data)
        self.assertIn("cells", data)
        self.assertIn("wind_flows", data)

    def test_to_html(self):
        engine = self._make_engine()
        engine.analyze()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = f.name
        engine.to_html(path)
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        os.unlink(path)
        self.assertIn("Spatial Weather Dashboard", content)
        self.assertIn("Health Score", content)

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("0 0\n10 0\n5 8\n3 6\n7 2\n")
            f.flush()
            engine = WeatherEngine(path=f.name)
        result = engine.analyze()
        os.unlink(f.name)
        self.assertEqual(len(result.cells), 5)

    def test_deterministic(self):
        import random as rnd
        rng = rnd.Random(42)
        pts = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(10)]
        r1 = WeatherEngine(points=pts, seed=42).analyze()
        r2 = WeatherEngine(points=pts, seed=42).analyze()
        self.assertAlmostEqual(r1.health_score, r2.health_score)
        self.assertAlmostEqual(r1.avg_temperature, r2.avg_temperature)


# ---------------------------------------------------------------------------
# Convenience API
# ---------------------------------------------------------------------------


class TestWeatherAnalyze(unittest.TestCase):
    def test_with_points(self):
        pts = [(0, 0), (10, 0), (5, 8), (3, 6), (7, 2)]
        result = weather_analyze(pts)
        self.assertIsInstance(result, WeatherResult)
        self.assertEqual(len(result.cells), 5)

    def test_with_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("0 0\n10 0\n5 8\n")
            f.flush()
            result = weather_analyze(f.name)
        os.unlink(f.name)
        self.assertEqual(len(result.cells), 3)


class TestWeatherDemo(unittest.TestCase):
    def test_runs(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            weather_demo()
        output = buf.getvalue()
        self.assertIn("Weather Engine", output)
        self.assertIn("Health Score", output)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI(unittest.TestCase):
    def test_demo_flag(self):
        import subprocess
        result = subprocess.run(
            [sys.executable, "vormap_weather.py", "--demo"],
            capture_output=True, text=True, cwd=os.path.dirname(__file__),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Weather Engine", result.stdout)


if __name__ == "__main__":
    unittest.main()
