"""Tests for vormap_evolve — evolutionary point placement optimizer."""

import math
import random
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_evolve import (
    evolve,
    _convex_hull_area,
    _voronoi_cell_areas,
    _fitness_uniform,
    _fitness_clustered,
    _fitness_coverage,
    _fitness_spread,
    _fitness_balanced,
    _random_individual,
    _mutate,
    _crossover,
    _tournament_select,
    _OBJECTIVES,
    _points_to_text,
    _html_report,
)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

class TestConvexHullArea:
    def test_triangle(self):
        pts = [(0, 0), (10, 0), (0, 10)]
        area = _convex_hull_area(pts)
        assert area == pytest.approx(50.0, rel=0.1)

    def test_square(self):
        pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
        area = _convex_hull_area(pts)
        assert area == pytest.approx(100.0, rel=0.1)

    def test_fewer_than_3_points(self):
        assert _convex_hull_area([(0, 0), (1, 1)]) == 0.0
        assert _convex_hull_area([(0, 0)]) == 0.0
        assert _convex_hull_area([]) == 0.0

    def test_collinear_points(self):
        pts = [(0, 0), (5, 0), (10, 0)]
        area = _convex_hull_area(pts)
        assert area == pytest.approx(0.0, abs=1e-6)


class TestVoronoiCellAreas:
    def test_returns_list_of_correct_length(self):
        pts = [(100, 100), (400, 400)]
        areas = _voronoi_cell_areas(pts, 500, 500)
        assert len(areas) == 2

    def test_total_area_approximates_bounding_box(self):
        pts = [(50, 50), (150, 50), (100, 150)]
        areas = _voronoi_cell_areas(pts, 200, 200)
        total = sum(areas)
        assert total == pytest.approx(200 * 200, rel=0.15)

    def test_single_point_gets_all_area(self):
        pts = [(250, 250)]
        areas = _voronoi_cell_areas(pts, 500, 500)
        assert len(areas) == 1
        assert areas[0] == pytest.approx(250000, rel=0.15)

    def test_symmetric_points_roughly_equal_areas(self):
        pts = [(125, 250), (375, 250)]
        areas = _voronoi_cell_areas(pts, 500, 500)
        assert areas[0] == pytest.approx(areas[1], rel=0.3)


# ---------------------------------------------------------------------------
# Fitness functions
# ---------------------------------------------------------------------------

class TestFitnessUniform:
    def test_returns_positive(self):
        pts = _random_individual(10, 100, 100)
        f = _fitness_uniform(pts, 100, 100)
        assert f > 0

    def test_grid_beats_random(self):
        random.seed(42)
        grid = [(x, y) for x in range(10, 100, 20) for y in range(10, 100, 20)]
        clumped = [(50 + random.gauss(0, 2), 50 + random.gauss(0, 2)) for _ in range(25)]
        f_grid = _fitness_uniform(grid, 100, 100)
        f_clump = _fitness_uniform(clumped, 100, 100)
        assert f_grid > f_clump


class TestFitnessClustered:
    def test_returns_nonneg(self):
        pts = _random_individual(10, 100, 100)
        assert _fitness_clustered(pts, 100, 100) >= 0

    def test_clustered_scores_higher(self):
        random.seed(7)
        c1 = [(10 + random.gauss(0, 1), 10 + random.gauss(0, 1)) for _ in range(10)]
        c2 = [(90 + random.gauss(0, 1), 90 + random.gauss(0, 1)) for _ in range(10)]
        clustered = c1 + c2
        spread = [(random.uniform(0, 100), random.uniform(0, 100)) for _ in range(20)]
        assert _fitness_clustered(clustered, 100, 100) > _fitness_clustered(spread, 100, 100)


class TestFitnessCoverage:
    def test_corner_points_high_coverage(self):
        pts = [(0, 0), (100, 0), (100, 100), (0, 100), (50, 50)]
        f = _fitness_coverage(pts, 100, 100)
        assert f == pytest.approx(1.0, rel=0.05)

    def test_center_points_low_coverage(self):
        pts = [(48, 48), (52, 48), (50, 52)]
        f = _fitness_coverage(pts, 100, 100)
        assert f < 0.01


class TestFitnessSpread:
    def test_returns_nonneg(self):
        pts = _random_individual(10, 100, 100)
        assert _fitness_spread(pts, 100, 100) >= 0


class TestFitnessBalanced:
    def test_returns_positive(self):
        pts = _random_individual(10, 100, 100)
        assert _fitness_balanced(pts, 100, 100) > 0


class TestObjectivesDict:
    def test_all_five_objectives_present(self):
        expected = {"uniform", "clustered", "coverage", "spread", "balanced"}
        assert set(_OBJECTIVES.keys()) == expected

    def test_all_callables(self):
        for fn in _OBJECTIVES.values():
            assert callable(fn)


# ---------------------------------------------------------------------------
# Genetic operators
# ---------------------------------------------------------------------------

class TestRandomIndividual:
    def test_correct_count(self):
        ind = _random_individual(15, 200, 300)
        assert len(ind) == 15

    def test_bounds(self):
        ind = _random_individual(100, 200, 300)
        for x, y in ind:
            assert 0 <= x <= 200
            assert 0 <= y <= 300


class TestMutate:
    def test_preserves_length(self):
        ind = _random_individual(20, 100, 100)
        mutated = _mutate(ind, 100, 100)
        assert len(mutated) == len(ind)

    def test_stays_in_bounds(self):
        random.seed(0)
        ind = _random_individual(50, 100, 100)
        for _ in range(100):
            ind = _mutate(ind, 100, 100, rate=1.0)
        for x, y in ind:
            assert 0 <= x <= 100
            assert 0 <= y <= 100

    def test_zero_rate_no_change(self):
        ind = [(10.0, 20.0), (30.0, 40.0)]
        result = _mutate(ind, 100, 100, rate=0.0)
        assert result == ind


class TestCrossover:
    def test_preserves_length(self):
        a = _random_individual(10, 100, 100)
        b = _random_individual(10, 100, 100)
        child = _crossover(a, b)
        assert len(child) == 10

    def test_child_points_from_parents(self):
        a = [(i, 0) for i in range(10)]
        b = [(i, 1) for i in range(10)]
        child = _crossover(a, b)
        for pt in child:
            assert pt[1] in (0, 1)


class TestTournamentSelect:
    def test_tends_to_select_fittest(self):
        pop = [[(i, i)] for i in range(10)]
        fits = list(range(10))
        counts = {i: 0 for i in range(10)}
        random.seed(42)
        for _ in range(1000):
            winner = _tournament_select(pop, fits, k=3)
            idx = int(winner[0][0])
            counts[idx] += 1
        assert counts[9] > counts[0]


# ---------------------------------------------------------------------------
# Main evolve function
# ---------------------------------------------------------------------------

class TestEvolve:
    def test_returns_required_keys(self):
        result = evolve(n_points=5, width=50, height=50, generations=5, seed=1)
        assert "points" in result
        assert "fitness" in result
        assert "objective" in result
        assert "generations_run" in result
        assert "history" in result

    def test_correct_point_count(self):
        result = evolve(n_points=12, width=50, height=50, generations=3, seed=2)
        assert len(result["points"]) == 12

    def test_history_length_matches_generations(self):
        gen = 10
        result = evolve(n_points=5, width=50, height=50, generations=gen, seed=3)
        assert len(result["history"]) == gen

    def test_fitness_improves_or_stable(self):
        result = evolve(n_points=10, width=100, height=100, generations=50, seed=4)
        assert result["history"][-1] >= result["history"][0] - 1e-9

    def test_all_objectives_runnable(self):
        for obj in _OBJECTIVES:
            result = evolve(n_points=5, width=50, height=50, objective=obj,
                            generations=3, seed=5)
            assert result["fitness"] >= 0

    def test_invalid_objective_raises(self):
        with pytest.raises(ValueError, match="Unknown objective"):
            evolve(objective="nonexistent")

    def test_seed_reproducibility(self):
        r1 = evolve(n_points=8, width=50, height=50, generations=10, seed=99)
        r2 = evolve(n_points=8, width=50, height=50, generations=10, seed=99)
        assert r1["fitness"] == pytest.approx(r2["fitness"])
        assert r1["points"] == r2["points"]

    def test_on_generation_callback(self):
        calls = []
        evolve(n_points=5, width=50, height=50, generations=5, seed=1,
               on_generation=lambda g, f: calls.append((g, f)))
        assert len(calls) == 5
        assert calls[0][0] == 0
        assert calls[4][0] == 4


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

class TestPointsToText:
    def test_format(self):
        pts = [(1.5, 2.3), (4.0, 5.0)]
        text = _points_to_text(pts)
        lines = text.strip().split("\n")
        assert len(lines) == 2
        assert "1.50" in lines[0]
        assert "2.30" in lines[0]


class TestHtmlReport:
    def test_contains_key_elements(self):
        result = evolve(n_points=5, width=100, height=100, generations=3, seed=1)
        html = _html_report(result, 100, 100)
        assert "<!DOCTYPE html>" in html
        assert "Evolutionary" in html
        assert "<circle" in html
        assert "Fitness over generations" in html
        assert result["objective"] in html
