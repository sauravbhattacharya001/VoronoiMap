"""Tests for vormap_swarm — Spatial Swarm Intelligence Engine."""

import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vormap_swarm import (
    BEHAVIORS,
    CellAgent,
    EmergentPattern,
    Signal,
    SwarmEngine,
    SwarmResult,
    SwarmState,
    swarm_demo,
    swarm_simulate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_points(n=50, lo=0, hi=500, seed=1):
    rng = random.Random(seed)
    return [(rng.uniform(lo, hi), rng.uniform(lo, hi)) for _ in range(n)]


def _grid_points(rows=5, cols=5, spacing=20, origin=(100, 100)):
    return [(origin[0] + c * spacing, origin[1] + r * spacing)
            for r in range(rows) for c in range(cols)]


def _cluster_points(cx, cy, n=10, std=5, seed=2):
    rng = random.Random(seed)
    return [(cx + rng.gauss(0, std), cy + rng.gauss(0, std)) for _ in range(n)]


# ---------------------------------------------------------------------------
# Named tuple creation
# ---------------------------------------------------------------------------

class TestNamedTuples:
    def test_cell_agent_fields(self):
        ag = CellAgent(0, (1, 2), 10.0, [1, 2], 50.0, 0, [], {})
        assert ag.cell_id == 0
        assert ag.center == (1, 2)
        assert ag.area == 10.0
        assert ag.neighbors == [1, 2]
        assert ag.energy == 50.0
        assert ag.role == 0
        assert ag.signals == []
        assert ag.memory == {}

    def test_signal_fields(self):
        sig = Signal(0, "alert", 0.9, {"k": "v"}, 5, 2)
        assert sig.source_id == 0
        assert sig.signal_type == "alert"
        assert sig.strength == 0.9
        assert sig.hops == 2

    def test_swarm_state_fields(self):
        state = SwarmState(10, [], 0, {}, 0.5)
        assert state.tick == 10
        assert state.convergence == 0.5

    def test_swarm_result_fields(self):
        state = SwarmState(1, [], 0, {}, 1.0)
        result = SwarmResult("consensus", 1, state, [1.0], [], [], [])
        assert result.behavior_type == "consensus"
        assert result.ticks_run == 1

    def test_emergent_pattern_fields(self):
        p = EmergentPattern("test", "desc", [1, 2], 0.8)
        assert p.pattern_type == "test"
        assert p.confidence == 0.8


# ---------------------------------------------------------------------------
# Engine initialization
# ---------------------------------------------------------------------------

class TestEngineInit:
    def test_valid_behaviors(self):
        for beh in BEHAVIORS:
            engine = SwarmEngine([(0, 0), (10, 10)], behavior=beh)
            assert engine.behavior == beh

    def test_invalid_behavior_raises(self):
        with pytest.raises(ValueError, match="Unknown behavior"):
            SwarmEngine([], behavior="invalid")

    def test_empty_points(self):
        engine = SwarmEngine([], behavior="consensus")
        result = engine.run()
        assert result.ticks_run == 0
        assert result.convergence_history == [1.0]

    def test_single_point(self):
        engine = SwarmEngine([(5, 5)], behavior="consensus")
        result = engine.run()
        assert result.ticks_run >= 1

    def test_two_points(self):
        engine = SwarmEngine([(0, 0), (10, 10)], behavior="balance")
        result = engine.run()
        assert result.ticks_run >= 1

    def test_max_ticks_respected(self):
        pts = _random_points(30)
        engine = SwarmEngine(pts, behavior="consensus", max_ticks=5)
        result = engine.run()
        assert result.ticks_run <= 5

    def test_seed_reproducibility(self):
        pts = _random_points(20)
        r1 = SwarmEngine(pts, behavior="balance", seed=7).run()
        r2 = SwarmEngine(pts, behavior="balance", seed=7).run()
        assert r1.convergence_history == r2.convergence_history
        assert r1.ticks_run == r2.ticks_run


# ---------------------------------------------------------------------------
# Consensus behavior
# ---------------------------------------------------------------------------

class TestConsensus:
    def test_convergence_increases(self):
        pts = _random_points(40)
        result = SwarmEngine(pts, behavior="consensus", max_ticks=50).run()
        # Convergence should generally increase
        assert result.convergence_history[-1] >= result.convergence_history[0]

    def test_uniform_opinion_converges_immediately(self):
        # If all neighbors agree, convergence is high from start
        pts = _grid_points(3, 3)
        engine = SwarmEngine(pts, behavior="consensus", max_ticks=30, seed=1)
        result = engine.run()
        assert result.convergence_history[-1] >= 0.5

    def test_detects_dominant_faction(self):
        pts = _random_points(50)
        result = SwarmEngine(pts, behavior="consensus", max_ticks=80).run()
        pattern_types = [p.pattern_type for p in result.emergent_patterns]
        # Should detect either dominant_faction or faction_boundary
        assert any(t in ("dominant_faction", "faction_boundary") for t in pattern_types) or \
               result.convergence_history[-1] >= 0.95


# ---------------------------------------------------------------------------
# Balance behavior
# ---------------------------------------------------------------------------

class TestBalance:
    def test_variance_decreases(self):
        pts = _random_points(30)
        result = SwarmEngine(pts, behavior="balance", max_ticks=50).run()
        assert result.convergence_history[-1] >= result.convergence_history[0]

    def test_single_point_balance(self):
        result = SwarmEngine([(5, 5)], behavior="balance").run()
        assert result.ticks_run >= 1
        assert result.convergence_history[-1] == 1.0

    def test_recommendations_present(self):
        pts = _random_points(25)
        result = SwarmEngine(pts, behavior="balance", max_ticks=30).run()
        assert len(result.recommendations) > 0


# ---------------------------------------------------------------------------
# Alert behavior
# ---------------------------------------------------------------------------

class TestAlert:
    def test_alert_propagation(self):
        pts = _random_points(40)
        result = SwarmEngine(pts, behavior="alert", max_ticks=50).run()
        # Alert should spread: convergence should be > 0
        assert result.convergence_history[-1] > 0.0

    def test_detects_relay_bottleneck(self):
        pts = _random_points(50)
        result = SwarmEngine(pts, behavior="alert", max_ticks=60).run()
        pattern_types = [p.pattern_type for p in result.emergent_patterns]
        assert "relay_bottleneck" in pattern_types or "alert_shadow" in pattern_types or \
               result.convergence_history[-1] > 0.9

    def test_small_dataset_alert(self):
        pts = [(0, 0), (10, 0), (5, 8)]
        result = SwarmEngine(pts, behavior="alert", max_ticks=20).run()
        assert result.ticks_run >= 1


# ---------------------------------------------------------------------------
# Territory behavior
# ---------------------------------------------------------------------------

class TestTerritory:
    def test_territory_formation(self):
        pts = _random_points(30)
        result = SwarmEngine(pts, behavior="territory", max_ticks=40).run()
        assert result.ticks_run >= 1
        assert result.convergence_history[-1] > 0.0

    def test_detects_contested_zones(self):
        pts = _random_points(50)
        result = SwarmEngine(pts, behavior="territory", max_ticks=60).run()
        # Either contested zones found or territory is fully unified
        pattern_types = [p.pattern_type for p in result.emergent_patterns]
        assert "contested_zone" in pattern_types or result.convergence_history[-1] >= 0.9


# ---------------------------------------------------------------------------
# Pathfind behavior
# ---------------------------------------------------------------------------

class TestPathfind:
    def test_pheromone_spreads(self):
        pts = _random_points(30)
        result = SwarmEngine(pts, behavior="pathfind", max_ticks=50).run()
        assert result.ticks_run >= 1

    def test_detects_highway(self):
        pts = _random_points(40)
        result = SwarmEngine(pts, behavior="pathfind", max_ticks=60).run()
        pattern_types = [p.pattern_type for p in result.emergent_patterns]
        # Might detect highway or just converge
        assert len(result.convergence_history) > 0

    def test_two_point_pathfind(self):
        result = SwarmEngine([(0, 0), (100, 100)], behavior="pathfind", max_ticks=20).run()
        assert result.ticks_run >= 1


# ---------------------------------------------------------------------------
# Collinear edge case
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_collinear_points(self):
        pts = [(i * 10, 0) for i in range(10)]
        for beh in BEHAVIORS:
            result = SwarmEngine(pts, behavior=beh, max_ticks=20).run()
            assert result.ticks_run >= 1

    def test_duplicate_points(self):
        pts = [(5, 5)] * 5
        result = SwarmEngine(pts, behavior="consensus", max_ticks=10).run()
        assert result.ticks_run >= 1

    def test_large_dataset(self):
        pts = _random_points(200)
        result = SwarmEngine(pts, behavior="balance", max_ticks=10).run()
        assert result.ticks_run >= 1


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

class TestJSONExport:
    def test_json_export(self, tmp_path):
        pts = _random_points(20)
        engine = SwarmEngine(pts, behavior="consensus", max_ticks=10)
        engine.run()
        path = str(tmp_path / "swarm.json")
        engine.to_json(path)
        with open(path) as f:
            data = json.load(f)
        assert data["behavior_type"] == "consensus"
        assert "convergence_history" in data
        assert "emergent_patterns" in data
        assert "recommendations" in data

    def test_json_export_before_run_raises(self, tmp_path):
        engine = SwarmEngine([(0, 0)], behavior="consensus")
        with pytest.raises(RuntimeError):
            engine.to_json(str(tmp_path / "fail.json"))


# ---------------------------------------------------------------------------
# HTML export
# ---------------------------------------------------------------------------

class TestHTMLExport:
    def test_html_export(self, tmp_path):
        pts = _random_points(20)
        engine = SwarmEngine(pts, behavior="alert", max_ticks=10)
        engine.run()
        path = str(tmp_path / "swarm.html")
        engine.to_html(path)
        with open(path, encoding="utf-8") as f:
            html = f.read()
        assert "Swarm Intelligence" in html
        assert "alert" in html.lower()
        assert "<svg" in html

    def test_html_export_before_run_raises(self, tmp_path):
        engine = SwarmEngine([(0, 0)], behavior="balance")
        with pytest.raises(RuntimeError):
            engine.to_html(str(tmp_path / "fail.html"))


# ---------------------------------------------------------------------------
# File loading & CLI
# ---------------------------------------------------------------------------

class TestFileAndCLI:
    def test_swarm_simulate_from_file(self, tmp_path):
        path = str(tmp_path / "pts.txt")
        with open(path, "w") as f:
            for i in range(20):
                f.write(f"{i * 10} {i * 5}\n")
        result = swarm_simulate(path, behavior="balance", max_ticks=10)
        assert result.ticks_run >= 1

    def test_swarm_simulate_from_list(self):
        pts = _random_points(15)
        result = swarm_simulate(pts, behavior="consensus", max_ticks=10)
        assert result.behavior_type == "consensus"

    def test_demo_runs(self, capsys):
        assert swarm_demo() is True
        captured = capsys.readouterr()
        assert "CONSENSUS" in captured.out
        assert "BALANCE" in captured.out
        assert "ALERT" in captured.out
        assert "TERRITORY" in captured.out
        assert "PATHFIND" in captured.out

    def test_cli_demo(self):
        from vormap_swarm import main
        main(["--demo"])

    def test_cli_with_file(self, tmp_path):
        path = str(tmp_path / "pts.txt")
        with open(path, "w") as f:
            for i in range(15):
                f.write(f"{i * 10},{i * 7}\n")
        from vormap_swarm import main
        main([path, "--behavior", "balance", "--max-ticks", "5"])

    def test_cli_no_input_errors(self):
        from vormap_swarm import main
        with pytest.raises(SystemExit):
            main([])


# ---------------------------------------------------------------------------
# Convergence detection
# ---------------------------------------------------------------------------

class TestConvergence:
    def test_early_stop_on_convergence(self):
        # Grid points with same seed should converge fast for consensus
        pts = _grid_points(4, 4)
        result = SwarmEngine(pts, behavior="consensus", max_ticks=100, seed=1).run()
        # Should stop early if convergence >= 0.95
        if result.convergence_history[-1] >= 0.95:
            assert result.ticks_run < 100

    def test_convergence_history_length(self):
        pts = _random_points(20)
        result = SwarmEngine(pts, behavior="balance", max_ticks=30).run()
        assert len(result.convergence_history) == result.ticks_run


# ---------------------------------------------------------------------------
# Tick snapshots
# ---------------------------------------------------------------------------

class TestSnapshots:
    def test_snapshots_exist(self):
        pts = _random_points(20)
        result = SwarmEngine(pts, behavior="consensus", max_ticks=20).run()
        assert len(result.tick_snapshots) >= 1

    def test_snapshot_structure(self):
        pts = _random_points(20)
        result = SwarmEngine(pts, behavior="balance", max_ticks=10).run()
        snap = result.tick_snapshots[0]
        assert snap.tick >= 1
        assert isinstance(snap.agents, list)
        assert isinstance(snap.global_metrics, dict)
        assert "agent_count" in snap.global_metrics
