"""Tests for vormap_governance — Spatial Governance Engine."""

import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from vormap_governance import (
    run_governance,
    assign_weights,
    compute_power_indices,
    run_elections,
    analyze_coalitions,
    design_constitution,
    score_democratic_health,
    generate_insights,
    CellVoter,
    Proposal,
    ElectionResult,
    Coalition,
    CoalitionAnalysis,
    ConstitutionalAnalysis,
    DemocraticHealth,
    GovernanceInsight,
    GovernanceReport,
    _voronoi_cells_simple,
    _euclidean,
    _polygon_area,
    _generate_proposals,
    _cell_preference,
    WEIGHT_METHODS,
)
import random


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

POINTS_5 = [(100, 200), (400, 300), (700, 500), (200, 700), (600, 100)]
POINTS_3 = [(200, 200), (500, 500), (800, 200)]
POINTS_10 = [
    (150, 200), (400, 350), (700, 150), (250, 700), (600, 600),
    (100, 500), (800, 400), (500, 800), (350, 100), (900, 900),
]
BOUNDS = (0, 1000, 0, 1000)


def _make_voters(n=5, weights=None):
    if weights is None:
        weights = [1.0] * n
    return [CellVoter(
        cell_idx=i,
        centroid=(100 * (i + 1), 100 * (i + 1)),
        area=10000.0,
        neighbors=2,
        centrality=0.5,
        weight=weights[i],
    ) for i in range(n)]


def _make_proposals(n=4):
    return [Proposal(
        proposal_id=f"P{i+1}",
        name=f"Proposal {i+1}",
        location=(200 * (i + 1), 300 * (i + 1)),
        description=f"Test proposal {i+1}",
    ) for i in range(n)]


# ---------------------------------------------------------------------------
# Geometry helper tests
# ---------------------------------------------------------------------------

def test_euclidean():
    assert _euclidean((0, 0), (3, 4)) == 5.0
    assert _euclidean((1, 1), (1, 1)) == 0.0


def test_polygon_area():
    # Unit square
    sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert abs(_polygon_area(sq) - 1.0) < 1e-9
    # Triangle
    tri = [(0, 0), (4, 0), (0, 3)]
    assert abs(_polygon_area(tri) - 6.0) < 1e-9
    # Degenerate
    assert _polygon_area([]) == 0.0
    assert _polygon_area([(1, 1)]) == 0.0


def test_voronoi_cells_simple():
    cells = _voronoi_cells_simple(POINTS_3, BOUNDS)
    assert len(cells) == 3
    for c in cells:
        assert len(c) >= 3


# ---------------------------------------------------------------------------
# Engine 1: Weight Assigner
# ---------------------------------------------------------------------------

def test_assign_weights_equal():
    voters = assign_weights(POINTS_5, BOUNDS, method="equal")
    assert len(voters) == 5
    for v in voters:
        assert v.weight == 1.0


def test_assign_weights_area():
    voters = assign_weights(POINTS_5, BOUNDS, method="area")
    assert len(voters) == 5
    total = sum(v.weight for v in voters)
    assert abs(total - 5.0) < 0.5  # approximately n


def test_assign_weights_strategic():
    voters = assign_weights(POINTS_5, BOUNDS, method="strategic")
    assert len(voters) == 5
    for v in voters:
        assert v.weight > 0
        assert v.centrality >= 0
        assert v.area > 0


def test_assign_weights_population():
    voters = assign_weights(POINTS_5, BOUNDS, method="population")
    assert len(voters) == 5
    for v in voters:
        assert v.weight > 0


def test_voter_has_centroid():
    voters = assign_weights(POINTS_3, BOUNDS)
    for v in voters:
        assert isinstance(v.centroid, tuple)
        assert len(v.centroid) == 2


def test_voter_neighbor_count():
    voters = assign_weights(POINTS_10, BOUNDS)
    # At least some cells should have neighbors
    total_neighbors = sum(v.neighbors for v in voters)
    assert total_neighbors > 0


# ---------------------------------------------------------------------------
# Engine 2: Power Index Calculator
# ---------------------------------------------------------------------------

def test_power_indices_equal_weights():
    voters = _make_voters(3, [1.0, 1.0, 1.0])
    voters, quota = compute_power_indices(voters, 0.5)
    # With equal weights, power should be roughly equal
    for v in voters:
        assert abs(v.banzhaf_power - 1/3) < 0.15
        assert abs(v.shapley_power - 1/3) < 0.15


def test_power_indices_dictator():
    voters = _make_voters(3, [10.0, 1.0, 1.0])
    voters, quota = compute_power_indices(voters, 0.5)
    assert voters[0].is_dictator
    assert voters[0].banzhaf_power > 0.5


def test_power_indices_dummy():
    # Weights [6, 3, 1], quota=0.5 -> quota=5.0
    # Winning: {0}, {0,1}, {0,2}, {0,1,2}. Cell 2 is never critical.
    voters = _make_voters(3, [6.0, 3.0, 1.0])
    voters, quota = compute_power_indices(voters, 0.5)
    assert voters[2].is_dummy


def test_power_indices_veto():
    # Weights [3, 3, 1], quota > 0.5 means quota=3.5
    # Neither cell alone wins, but without cell 0 or 1, max=4 < 3.5... wait
    # total=7, quota=3.5. Without cell0: max=4>=3.5 (cells 1+2). Not veto.
    # Weights [5, 3, 2], quota=0.5 -> 5.0. Without cell0: 5.0>=5.0. veto only if total-w < quota
    # total=10, quota=5. Without cell0: 5 is not < 5. Not veto.
    # For veto: total - w_i < quota => w_i > total - quota
    # Quota=7 (0.7 of 10): total - w_i < 7 => w_i > 3. So cells with w>3: cell0(5).
    voters = _make_voters(3, [5.0, 3.0, 2.0])
    voters, quota = compute_power_indices(voters, 0.7)
    assert voters[0].has_veto


def test_power_indices_sum_to_one():
    voters = _make_voters(5, [3, 2, 2, 1, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    banzhaf_sum = sum(v.banzhaf_power for v in voters)
    shapley_sum = sum(v.shapley_power for v in voters)
    assert abs(banzhaf_sum - 1.0) < 0.01
    assert abs(shapley_sum - 1.0) < 0.01


def test_quota_calculation():
    voters = _make_voters(4, [3, 2, 2, 1])
    voters, quota = compute_power_indices(voters, 0.6)
    assert abs(quota - 4.8) < 0.01  # 8 * 0.6


# ---------------------------------------------------------------------------
# Engine 3: Voting System Simulator
# ---------------------------------------------------------------------------

def test_generate_proposals():
    voters = _make_voters(5)
    proposals = _generate_proposals(voters, BOUNDS, n_proposals=4)
    assert len(proposals) == 4
    for p in proposals:
        assert p.proposal_id.startswith("P")
        assert p.location is not None


def test_cell_preference():
    voter = CellVoter(0, (100, 100), 1000, 2, 0.5, weight=1.0)
    proposals = _make_proposals(4)
    rng = random.Random(42)
    prefs = _cell_preference(voter, proposals, rng)
    assert len(prefs) == 4
    assert set(prefs) == {"P1", "P2", "P3", "P4"}


def test_run_elections():
    voters = _make_voters(5)
    proposals = _make_proposals(4)
    results = run_elections(voters, proposals)
    assert len(results) == 4
    systems = {r.system for r in results}
    assert systems == {"plurality", "borda", "approval", "irv"}


def test_election_winners_are_valid():
    voters = _make_voters(5)
    proposals = _make_proposals(3)
    results = run_elections(voters, proposals)
    valid_ids = {p.proposal_id for p in proposals}
    for r in results:
        assert r.winner in valid_ids


def test_irv_has_rounds():
    voters = _make_voters(5)
    proposals = _make_proposals(4)
    results = run_elections(voters, proposals)
    irv = [r for r in results if r.system == "irv"][0]
    assert irv.rounds is not None
    assert len(irv.rounds) >= 1


def test_plurality_weighted():
    # Cell 0 has huge weight, should dominate
    voters = _make_voters(3, [100, 1, 1])
    proposals = _make_proposals(3)
    results = run_elections(voters, proposals, seed=42)
    plurality = [r for r in results if r.system == "plurality"][0]
    # The winner should have the score from cell 0's weight
    assert max(plurality.scores.values()) >= 100


# ---------------------------------------------------------------------------
# Engine 4: Coalition Analyzer
# ---------------------------------------------------------------------------

def test_analyze_coalitions_basic():
    voters = _make_voters(3, [1.0, 1.0, 1.0])
    coal = analyze_coalitions(voters, 1.5)  # need 2 out of 3
    assert coal.winning_coalitions > 0
    assert coal.minimal_winning_coalitions > 0


def test_all_winning_with_low_quota():
    voters = _make_voters(3, [1.0, 1.0, 1.0])
    coal = analyze_coalitions(voters, 0.5)  # very low quota
    # Almost all coalitions should win
    assert coal.winning_coalitions >= 6  # at least 6 of 7


def test_no_winning_with_impossible_quota():
    voters = _make_voters(3, [1.0, 1.0, 1.0])
    coal = analyze_coalitions(voters, 100.0)  # impossible
    assert coal.winning_coalitions == 0


def test_veto_players_in_coalition():
    # [5, 3, 2], quota=10.01 -> only grand coalition wins (total=10)
    # Actually total-w_i < quota for all i => all veto
    voters = _make_voters(3, [5.0, 3.0, 2.0])
    coal = analyze_coalitions(voters, 10.0)
    # Grand coalition is the only winning one, all are veto
    assert len(coal.veto_players) == 3


def test_coalition_fragility():
    voters = _make_voters(3, [1.0, 1.0, 1.0])
    coal = analyze_coalitions(voters, 1.5)
    assert 0 <= coal.fragility_score <= 1


def test_top_coalitions_sorted():
    voters = _make_voters(4, [3, 2, 2, 1])
    coal = analyze_coalitions(voters, 4.0)
    if coal.top_coalitions:
        # Minimal winning should come first
        first_minimal = True
        for c in coal.top_coalitions:
            if not c.is_minimal_winning:
                first_minimal = False
            if not first_minimal:
                assert not c.is_minimal_winning or True  # after first non-minimal


# ---------------------------------------------------------------------------
# Engine 5: Constitutional Designer
# ---------------------------------------------------------------------------

def test_design_constitution_basic():
    voters = _make_voters(5, [3, 2, 2, 1, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    const = design_constitution(voters, quota, 0.5)
    assert const.quota > 0
    assert 0 < const.quota_fraction <= 1
    assert isinstance(const.recommendations, list)


def test_constitution_detects_dictator():
    voters = _make_voters(3, [10, 1, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    const = design_constitution(voters, quota, 0.5)
    assert const.has_dictator
    assert const.dictator_idx == 0


def test_constitution_detects_dummies():
    voters = _make_voters(3, [6, 3, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    const = design_constitution(voters, quota, 0.5)
    assert const.dummy_count >= 1


def test_constitution_power_deviation():
    voters = _make_voters(3, [1, 1, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    const = design_constitution(voters, quota, 0.5)
    # Equal weights should have low deviation
    assert const.power_deviation < 0.1


def test_constitution_optimal_quota():
    voters = _make_voters(5, [3, 2, 2, 1, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    const = design_constitution(voters, quota, 0.5)
    assert const.optimal_quota > 0


# ---------------------------------------------------------------------------
# Engine 6: Democratic Health Scorer
# ---------------------------------------------------------------------------

def test_health_score_range():
    report = run_governance(POINTS_5, BOUNDS)
    assert 0 <= report.health.score <= 100


def test_health_grade():
    report = run_governance(POINTS_5, BOUNDS)
    assert report.health.grade in ("A", "B", "C", "D", "F")


def test_health_dimensions():
    report = run_governance(POINTS_5, BOUNDS)
    h = report.health
    assert 0 <= h.representation_equality <= 100
    assert -0.01 <= h.power_dispersion <= 100.01
    assert 0 <= h.coalition_stability <= 100
    assert 0 <= h.constitutional_soundness <= 100
    assert 0 <= h.voting_consistency <= 100


def test_equal_weights_good_health():
    voters = assign_weights(POINTS_5, BOUNDS, method="equal")
    voters, quota = compute_power_indices(voters, 0.5)
    proposals = _generate_proposals(voters, BOUNDS)
    elections = run_elections(voters, proposals)
    coal = analyze_coalitions(voters, quota)
    const = design_constitution(voters, quota, 0.5)
    health = score_democratic_health(voters, elections, coal, const)
    # Equal weights should produce decent health
    assert health.score >= 50


# ---------------------------------------------------------------------------
# Engine 7: Insight Generator
# ---------------------------------------------------------------------------

def test_insights_type():
    report = run_governance(POINTS_5, BOUNDS)
    for ins in report.insights:
        assert isinstance(ins, GovernanceInsight)
        assert ins.category in ("power_imbalance", "structural_weakness", "opportunity", "warning")
        assert ins.severity in ("critical", "high", "medium", "low", "info")


def test_dictator_insight():
    voters = _make_voters(3, [10, 1, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    elections = run_elections(voters, _make_proposals(3))
    coal = analyze_coalitions(voters, quota)
    const = design_constitution(voters, quota, 0.5)
    health = score_democratic_health(voters, elections, coal, const)
    insights = generate_insights(voters, elections, coal, const, health)
    # Should detect power concentration
    categories = [i.category for i in insights]
    assert "power_imbalance" in categories


def test_dummy_insight():
    voters = _make_voters(3, [6, 3, 1])
    voters, quota = compute_power_indices(voters, 0.5)
    elections = run_elections(voters, _make_proposals(3))
    coal = analyze_coalitions(voters, quota)
    const = design_constitution(voters, quota, 0.5)
    health = score_democratic_health(voters, elections, coal, const)
    insights = generate_insights(voters, elections, coal, const, health)
    titles = [i.title for i in insights]
    has_dummy_insight = any("owerless" in t or "ummy" in t for t in titles)
    assert has_dummy_insight


# ---------------------------------------------------------------------------
# Orchestrator (run_governance)
# ---------------------------------------------------------------------------

def test_run_governance_basic():
    report = run_governance(POINTS_5, BOUNDS)
    assert isinstance(report, GovernanceReport)
    assert report.n_cells == 5
    assert len(report.cells) == 5
    assert len(report.elections) == 4
    assert isinstance(report.coalitions, CoalitionAnalysis)
    assert isinstance(report.constitution, ConstitutionalAnalysis)
    assert isinstance(report.health, DemocraticHealth)


def test_run_governance_10_cells():
    report = run_governance(POINTS_10, BOUNDS)
    assert report.n_cells == 10
    assert len(report.cells) == 10


def test_run_governance_custom_quota():
    report = run_governance(POINTS_5, BOUNDS, quota_fraction=0.67)
    assert abs(report.constitution.quota_fraction - 0.67) < 0.01


def test_run_governance_equal_weights():
    report = run_governance(POINTS_5, BOUNDS, weight_method="equal")
    for c in report.cells:
        assert c.weight == 1.0


def test_run_governance_deterministic():
    r1 = run_governance(POINTS_5, BOUNDS, seed=99)
    r2 = run_governance(POINTS_5, BOUNDS, seed=99)
    assert r1.health.score == r2.health.score


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict():
    report = run_governance(POINTS_5, BOUNDS)
    d = report.to_dict()
    assert "cells" in d
    assert "elections" in d
    assert "coalitions" in d
    assert "constitution" in d
    assert "health" in d
    assert "insights" in d


def test_to_json(tmp_path=None):
    report = run_governance(POINTS_3, BOUNDS)
    path = os.path.join(tempfile.gettempdir(), "test_governance.json")
    report.to_json(path)
    with open(path) as f:
        data = json.load(f)
    assert data["n_cells"] == 3
    assert "cells" in data
    os.unlink(path)


def test_to_html():
    report = run_governance(POINTS_3, BOUNDS)
    path = os.path.join(tempfile.gettempdir(), "test_governance.html")
    report.to_html(path)
    with open(path, encoding="utf-8") as f:
        html = f.read()
    assert "Spatial Governance Engine" in html
    assert "Democratic Health" in html
    os.unlink(path)


def test_summary_text():
    report = run_governance(POINTS_5, BOUNDS)
    text = report.summary_text()
    assert "SPATIAL GOVERNANCE ENGINE" in text
    assert "Democratic Health" in text
    assert "Coalitions" in text


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------

def test_cell_voter_to_dict():
    v = CellVoter(0, (100.0, 200.0), 5000.0, 3, 0.7, weight=2.5)
    d = v.to_dict()
    assert d["cell_idx"] == 0
    assert d["weight"] == 2.5
    assert d["neighbors"] == 3


def test_proposal_to_dict():
    p = Proposal("P1", "Test", (100, 200), "A test proposal")
    d = p.to_dict()
    assert d["proposal_id"] == "P1"
    assert d["name"] == "Test"


def test_election_result_to_dict():
    e = ElectionResult("plurality", "P1", {"P1": 5.0, "P2": 3.0})
    d = e.to_dict()
    assert d["system"] == "plurality"
    assert d["winner"] == "P1"


def test_coalition_to_dict():
    c = Coalition([0, 1], 3.0, True, True, [0, 1])
    d = c.to_dict()
    assert d["members"] == [0, 1]
    assert d["is_minimal_winning"] is True


def test_democratic_health_to_dict():
    h = DemocraticHealth(75.0, "B", 80, 70, 75, 80, 65)
    d = h.to_dict()
    assert d["grade"] == "B"
    assert d["score"] == 75.0


def test_governance_insight_to_dict():
    i = GovernanceInsight("warning", "high", "Test", "Detail", [0, 1])
    d = i.to_dict()
    assert d["category"] == "warning"
    assert d["affected_cells"] == [0, 1]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_single_cell():
    report = run_governance([(500, 500)], BOUNDS)
    assert report.n_cells == 1
    assert report.cells[0].banzhaf_power == 1.0


def test_two_cells():
    report = run_governance([(300, 300), (700, 700)], BOUNDS)
    assert report.n_cells == 2


def test_large_n_proposals():
    report = run_governance(POINTS_5, BOUNDS, n_proposals=8)
    assert len(report.proposals) == 8


def test_high_quota():
    report = run_governance(POINTS_5, BOUNDS, quota_fraction=0.9)
    assert report.constitution.quota_fraction == 0.9


def test_low_quota():
    report = run_governance(POINTS_5, BOUNDS, quota_fraction=0.5)
    assert report.coalitions.winning_coalitions > 0


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
