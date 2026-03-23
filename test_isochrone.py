"""Tests for vormap_isochrone module."""

import json
import os
import tempfile

from vormap_isochrone import compute_isochrone, assign_bands, export_isochrone_json, export_isochrone_csv


def _make_grid_adjacency():
    """Simple 3x3 grid adjacency: 0-1-2 / 3-4-5 / 6-7-8."""
    adj = {
        0: [1, 3], 1: [0, 2, 4], 2: [1, 5],
        3: [0, 4, 6], 4: [1, 3, 5, 7], 5: [2, 4, 8],
        6: [3, 7], 7: [4, 6, 8], 8: [5, 7],
    }
    seeds = {
        0: (0, 0), 1: (1, 0), 2: (2, 0),
        3: (0, 1), 4: (1, 1), 5: (2, 1),
        6: (0, 2), 7: (1, 2), 8: (2, 2),
    }
    return adj, seeds


def test_hop_from_center():
    adj, seeds = _make_grid_adjacency()
    costs = compute_isochrone(adj, seeds, sources=[4], weight="hop")
    assert costs[4] == 0.0
    # direct neighbours are 1 hop
    for n in [1, 3, 5, 7]:
        assert costs[n] == 1.0
    # corners are 2 hops
    for c in [0, 2, 6, 8]:
        assert costs[c] == 2.0


def test_euclidean_from_corner():
    adj, seeds = _make_grid_adjacency()
    costs = compute_isochrone(adj, seeds, sources=[0], weight="euclidean")
    assert costs[0] == 0.0
    assert abs(costs[1] - 1.0) < 1e-9
    assert abs(costs[3] - 1.0) < 1e-9


def test_multi_source():
    adj, seeds = _make_grid_adjacency()
    costs = compute_isochrone(adj, seeds, sources=[0, 8], weight="hop")
    assert costs[0] == 0.0
    assert costs[8] == 0.0
    assert costs[4] == 2.0  # equidistant from both


def test_max_cost():
    adj, seeds = _make_grid_adjacency()
    costs = compute_isochrone(adj, seeds, sources=[4], weight="hop", max_cost=1)
    assert 4 in costs
    assert 1 in costs
    # corners should not be reached (cost=2 > max_cost=1)
    assert 0 not in costs or costs[0] > 1


def test_assign_bands():
    costs = {0: 0.0, 1: 3.0, 2: 7.0, 3: 15.0, 4: 100.0}
    bands = assign_bands(costs, breaks=[5, 10, 50])
    assert bands[0] == 0
    assert bands[1] == 0
    assert bands[2] == 1
    assert bands[3] == 2
    assert bands[4] == 3  # beyond all breaks


def test_json_export():
    costs = {0: 0.0, 1: 5.5}
    bands = {0: 0, 1: 1}
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        export_isochrone_json(costs, bands, path)
        with open(path) as f:
            data = json.load(f)
        assert len(data["cells"]) == 2
    finally:
        os.unlink(path)


def test_csv_export():
    costs = {0: 0.0, 1: 5.5}
    bands = {0: 0, 1: 1}
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        path = f.name
    try:
        export_isochrone_csv(costs, bands, path)
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 3  # header + 2 rows
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_hop_from_center()
    test_euclidean_from_corner()
    test_multi_source()
    test_max_cost()
    test_assign_bands()
    test_json_export()
    test_csv_export()
    print("All isochrone tests passed!")
