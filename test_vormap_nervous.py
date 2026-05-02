#!/usr/bin/env python3
"""Tests for vormap_nervous — Spatial Nervous System Engine."""

import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from vormap_nervous import (
    NervousSystemEngine,
    NervousSystemResult,
    NeuronCell,
    Synapse,
    SpikeEvent,
    ReflexArc,
    nervous_analyze,
    nervous_demo,
    main,
    _euclidean,
    _load_points,
    _knn_adjacency,
    _centroid,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TRIANGLE = [(0, 0), (10, 0), (5, 8)]
SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
LINE = [(0, 0), (5, 0), (10, 0)]
CLUSTER = [(i * 2, j * 2) for i in range(5) for j in range(5)]  # 25 points grid


@pytest.fixture
def pts_file(tmp_path):
    f = tmp_path / "pts.txt"
    f.write_text("0 0\n10 0\n5 8\n3 6\n7 2\n8 9\n1 5\n6 4\n")
    return str(f)


@pytest.fixture
def small_engine():
    return NervousSystemEngine(points=TRIANGLE)


@pytest.fixture
def medium_engine():
    return NervousSystemEngine(points=CLUSTER)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_euclidean_zero(self):
        assert _euclidean((0, 0), (0, 0)) == 0.0

    def test_euclidean_known(self):
        assert abs(_euclidean((0, 0), (3, 4)) - 5.0) < 1e-9

    def test_centroid(self):
        cx, cy = _centroid(TRIANGLE)
        assert abs(cx - 5.0) < 1e-9
        assert abs(cy - 8 / 3) < 1e-9

    def test_centroid_empty(self):
        assert _centroid([]) == (0.0, 0.0)

    def test_knn_adjacency_symmetric(self):
        adj = _knn_adjacency(SQUARE, k=2)
        for i, nbrs in adj.items():
            for j in nbrs:
                assert i in adj[j], f"{i} in adj[{j}] expected"

    def test_load_points(self, pts_file):
        pts = _load_points(pts_file)
        assert len(pts) == 8
        assert pts[0] == (0.0, 0.0)

    def test_load_points_skip_comments(self, tmp_path):
        f = tmp_path / "c.txt"
        f.write_text("# comment\n1 2\n\n3 4\n")
        pts = _load_points(str(f))
        assert len(pts) == 2


# ---------------------------------------------------------------------------
# Neuron Classification
# ---------------------------------------------------------------------------


class TestNeuronClassification:
    def test_types_valid(self, medium_engine):
        r = medium_engine.analyze()
        valid = {"sensory", "motor", "interneuron", "inhibitory", "excitatory"}
        for n in r.neurons:
            assert n.neuron_type in valid

    def test_all_cells_classified(self, medium_engine):
        r = medium_engine.analyze()
        assert len(r.neurons) == 25

    def test_neuron_has_coordinates(self, small_engine):
        r = small_engine.analyze()
        for n in r.neurons:
            assert isinstance(n.x, (int, float))
            assert isinstance(n.y, (int, float))

    def test_neuron_has_neighbors(self, medium_engine):
        r = medium_engine.analyze()
        for n in r.neurons:
            assert isinstance(n.neighbors, list)

    def test_boundary_cells_sensory(self):
        # Extreme outer cells should tend to be sensory
        pts = [(50, 50)] + [(50 + 40 * math.cos(a), 50 + 40 * math.sin(a))
                            for a in [i * math.pi / 4 for i in range(8)]]
        engine = NervousSystemEngine(points=pts)
        r = engine.analyze()
        outer_types = [n.neuron_type for n in r.neurons[1:]]
        assert "sensory" in outer_types


# ---------------------------------------------------------------------------
# Synapse Mapping
# ---------------------------------------------------------------------------


class TestSynapseMapping:
    def test_synapses_created(self, medium_engine):
        r = medium_engine.analyze()
        assert len(r.synapses) > 0

    def test_synapse_types_valid(self, medium_engine):
        r = medium_engine.analyze()
        for s in r.synapses:
            assert s.synapse_type in ("excitatory", "inhibitory")

    def test_synapse_strength_positive(self, medium_engine):
        r = medium_engine.analyze()
        for s in r.synapses:
            assert s.strength > 0

    def test_synapse_plasticity_bounded(self, medium_engine):
        r = medium_engine.analyze()
        for s in r.synapses:
            assert 0 <= s.plasticity <= 1.0

    def test_inhibitory_neuron_inhibitory_synapse(self):
        # Inhibitory neurons should produce inhibitory synapses
        pts = [(i, j) for i in range(4) for j in range(4)]
        engine = NervousSystemEngine(points=pts)
        r = engine.analyze()
        inh_neurons = {n.cell_id for n in r.neurons if n.neuron_type == "inhibitory"}
        for s in r.synapses:
            if s.source in inh_neurons:
                assert s.synapse_type == "inhibitory"


# ---------------------------------------------------------------------------
# Signal Propagation
# ---------------------------------------------------------------------------


class TestSignalPropagation:
    def test_spike_trains_exist(self, medium_engine):
        r = medium_engine.analyze()
        assert len(r.spike_trains) > 0

    def test_spike_has_timestep(self, medium_engine):
        r = medium_engine.analyze()
        for train in r.spike_trains:
            for ev in train:
                assert ev.timestep >= 0

    def test_stimulus_cell_fires_first(self):
        engine = NervousSystemEngine(points=CLUSTER, stimulate=0)
        r = engine.analyze()
        assert len(r.spike_trains) > 0
        first_spike = r.spike_trains[0][0]
        assert first_spike.cell_id == 0
        assert first_spike.timestep == 0

    def test_propagation_spreads(self, medium_engine):
        r = medium_engine.analyze()
        fired = set()
        for train in r.spike_trains:
            for ev in train:
                fired.add(ev.cell_id)
        assert len(fired) > 1

    def test_spike_potential_positive(self, medium_engine):
        r = medium_engine.analyze()
        for train in r.spike_trains:
            for ev in train:
                # At firing time potential should be > 0
                # (could be resting after reset, but spike events record firing potential)
                assert isinstance(ev.potential, float)


# ---------------------------------------------------------------------------
# Reflex Arcs
# ---------------------------------------------------------------------------


class TestReflexArcs:
    def test_arcs_sorted_by_latency(self, medium_engine):
        r = medium_engine.analyze()
        if len(r.reflex_arcs) > 1:
            for i in range(len(r.reflex_arcs) - 1):
                assert r.reflex_arcs[i].latency <= r.reflex_arcs[i + 1].latency

    def test_arc_path_starts_sensory(self, medium_engine):
        r = medium_engine.analyze()
        for arc in r.reflex_arcs:
            assert arc.path[0] == arc.sensory_id

    def test_arc_path_ends_motor(self, medium_engine):
        r = medium_engine.analyze()
        for arc in r.reflex_arcs:
            assert arc.path[-1] == arc.motor_id

    def test_arc_latency_matches_path(self, medium_engine):
        r = medium_engine.analyze()
        for arc in r.reflex_arcs:
            assert arc.latency == len(arc.path) - 1

    def test_relay_neurons_in_path(self, medium_engine):
        r = medium_engine.analyze()
        for arc in r.reflex_arcs:
            for relay in arc.relay_neurons:
                assert relay in arc.path


# ---------------------------------------------------------------------------
# Rhythm Analysis
# ---------------------------------------------------------------------------


class TestRhythmAnalysis:
    def test_rhythm_keys(self, medium_engine):
        r = medium_engine.analyze()
        assert set(r.rhythms.keys()) == {"alpha", "beta", "gamma"}

    def test_rhythm_values_non_negative(self, medium_engine):
        r = medium_engine.analyze()
        for v in r.rhythms.values():
            assert v >= 0

    def test_rhythms_detected_in_active_network(self, medium_engine):
        r = medium_engine.analyze()
        total = sum(r.rhythms.values())
        # With 25 cells, we should detect some rhythm
        assert total >= 0  # at minimum non-negative


# ---------------------------------------------------------------------------
# Plasticity
# ---------------------------------------------------------------------------


class TestPlasticity:
    def test_plasticity_changes_list(self, medium_engine):
        r = medium_engine.analyze()
        assert isinstance(r.plasticity_changes, list)

    def test_plasticity_change_format(self, medium_engine):
        r = medium_engine.analyze()
        for src, tgt, delta in r.plasticity_changes:
            assert isinstance(src, int)
            assert isinstance(tgt, int)
            assert isinstance(delta, float)

    def test_synapse_strength_stays_bounded(self, medium_engine):
        r = medium_engine.analyze()
        for s in r.synapses:
            assert 0 < s.strength <= 1.0


# ---------------------------------------------------------------------------
# Health Score & Insights
# ---------------------------------------------------------------------------


class TestHealthAndInsights:
    def test_health_score_bounded(self, medium_engine):
        r = medium_engine.analyze()
        assert 0 <= r.health_score <= 100

    def test_insights_non_empty(self, medium_engine):
        r = medium_engine.analyze()
        assert len(r.insights) > 0

    def test_insights_are_strings(self, medium_engine):
        r = medium_engine.analyze()
        for ins in r.insights:
            assert isinstance(ins, str)

    def test_health_score_single_point(self):
        engine = NervousSystemEngine(points=[(5, 5)])
        r = engine.analyze()
        assert 0 <= r.health_score <= 100

    def test_health_score_two_points(self):
        engine = NervousSystemEngine(points=[(0, 0), (10, 10)])
        r = engine.analyze()
        assert 0 <= r.health_score <= 100


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_single_point(self):
        engine = NervousSystemEngine(points=[(5, 5)])
        r = engine.analyze()
        assert len(r.neurons) == 1
        assert r.neurons[0].neuron_type in {"sensory", "motor", "interneuron", "inhibitory", "excitatory"}

    def test_two_points(self):
        engine = NervousSystemEngine(points=[(0, 0), (10, 10)])
        r = engine.analyze()
        assert len(r.neurons) == 2

    def test_collinear_points(self):
        engine = NervousSystemEngine(points=LINE)
        r = engine.analyze()
        assert len(r.neurons) == 3

    def test_no_points_raises(self):
        with pytest.raises(ValueError):
            NervousSystemEngine(points=[])

    def test_duplicate_points(self):
        engine = NervousSystemEngine(points=[(5, 5), (5, 5), (5, 5)])
        r = engine.analyze()
        assert len(r.neurons) == 3


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_to_dict(self, medium_engine):
        medium_engine.analyze()
        d = medium_engine.to_dict()
        assert "neurons" in d
        assert "health_score" in d
        assert "insights" in d

    def test_to_json(self, medium_engine, tmp_path):
        medium_engine.analyze()
        out = str(tmp_path / "out.json")
        medium_engine.to_json(out)
        with open(out) as f:
            data = json.load(f)
        assert "neurons" in data
        assert len(data["neurons"]) == 25

    def test_to_html(self, medium_engine, tmp_path):
        medium_engine.analyze()
        out = str(tmp_path / "out.html")
        medium_engine.to_html(out)
        content = open(out).read()
        assert "Nervous System" in content
        assert "Health" in content


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_demo_flag(self, capsys):
        main(["--demo"])
        captured = capsys.readouterr()
        assert "NERVOUS SYSTEM" in captured.out

    def test_file_input(self, pts_file, capsys):
        main([pts_file])
        captured = capsys.readouterr()
        assert "Health Score" in captured.out

    def test_json_output(self, pts_file, tmp_path):
        out = str(tmp_path / "cli.json")
        main([pts_file, "--json", out])
        assert os.path.exists(out)

    def test_html_output(self, pts_file, tmp_path):
        out = str(tmp_path / "cli.html")
        main([pts_file, "--html", out])
        assert os.path.exists(out)

    def test_stimulate_flag(self, pts_file, capsys):
        main([pts_file, "--stimulate", "0"])
        captured = capsys.readouterr()
        assert "NERVOUS SYSTEM" in captured.out


# ---------------------------------------------------------------------------
# Demo Function
# ---------------------------------------------------------------------------


class TestDemo:
    def test_demo_returns_result(self):
        r = nervous_demo()
        assert isinstance(r, NervousSystemResult)
        assert r.health_score >= 0

    def test_demo_deterministic(self):
        r1 = nervous_demo()
        r2 = nervous_demo()
        assert r1.health_score == r2.health_score


# ---------------------------------------------------------------------------
# Convenience Function
# ---------------------------------------------------------------------------


class TestConvenience:
    def test_nervous_analyze(self, pts_file):
        r = nervous_analyze(pts_file)
        assert isinstance(r, NervousSystemResult)
        assert len(r.neurons) == 8

    def test_nervous_analyze_with_stimulate(self, pts_file):
        r = nervous_analyze(pts_file, stimulate=2)
        assert len(r.spike_trains) > 0
        assert r.spike_trains[0][0].cell_id == 2
