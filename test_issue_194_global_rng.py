"""Regression tests for issue #194.

Verify that public class constructors and public helper functions in
``vormap_contagion``, ``vormap_ecosystem``, and ``vormap_privacy`` do
NOT mutate the host process's global ``random`` module state.

Follow-up to issue #192 (which fixed ``*_demo()`` entry points). The
five public APIs covered here are:

* ``ContagionSimulator(__init__)``
* ``EcosystemSimulator(__init__)``
* ``laplace_noise(points, epsilon, seed=...)``
* ``donut_mask(points, r_min, r_max, seed=...)``
* ``optimize_privacy(points, method=..., seed=...)``

The strategy mirrors ``test_issue_192_global_rng.py``: invoke the API
under two distinct host seeds and verify the global ``random`` state
afterwards differs. If the API internally calls ``random.seed(K)``,
the end-state is independent of the host seed and the assertion fails.

Also covers the ``optimize_privacy`` seed-shadowing bonus bug: two
different ``seed=`` values must produce different trial trajectories.
"""

from __future__ import annotations

import random

import pytest

import vormap_contagion
import vormap_ecosystem
import vormap_privacy


# ---------------------------------------------------------------------------
# Helpers


def _state_after(callable_):
    """Run ``callable_()`` twice, once seeded 0xA1 and once seeded 0xB2,
    and return the two global ``random`` end-states.
    """
    random.seed(0xA1)
    callable_()
    state_a = random.getstate()

    random.seed(0xB2)
    callable_()
    state_b = random.getstate()
    return state_a, state_b


def _assert_does_not_reseed_global(callable_, label):
    state_a, state_b = _state_after(callable_)
    assert state_a != state_b, (
        f"{label} reseeded the global random module: end-state is "
        "identical for two different host seeds. See issue #194."
    )


# ---------------------------------------------------------------------------
# Class constructors


def test_contagion_simulator_init_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_contagion.ContagionSimulator(n_points=20, ticks=5, seed=7),
        "ContagionSimulator(seed=7)",
    )


def test_contagion_simulator_init_no_seed_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_contagion.ContagionSimulator(n_points=20, ticks=5),
        "ContagionSimulator(seed=None)",
    )


def test_ecosystem_simulator_init_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_ecosystem.EcosystemSimulator(
            n_points=20, n_species=3, ticks=5, seed=7
        ),
        "EcosystemSimulator(seed=7)",
    )


# ---------------------------------------------------------------------------
# Public privacy helpers


_PTS = [(float(i), float(i * 2)) for i in range(20)]


def test_laplace_noise_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_privacy.laplace_noise(_PTS, epsilon=1.0, seed=7),
        "laplace_noise(seed=7)",
    )


def test_donut_mask_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_privacy.donut_mask(_PTS, r_min=1.0, r_max=5.0, seed=7),
        "donut_mask(seed=7)",
    )


def test_optimize_privacy_laplace_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_privacy.optimize_privacy(
            _PTS, method="laplace", max_distortion=5.0, seed=7
        ),
        "optimize_privacy(method=laplace, seed=7)",
    )


def test_optimize_privacy_donut_does_not_reseed_global():
    _assert_does_not_reseed_global(
        lambda: vormap_privacy.optimize_privacy(
            _PTS, method="donut", max_distortion=20.0, seed=7
        ),
        "optimize_privacy(method=donut, seed=7)",
    )


# ---------------------------------------------------------------------------
# Determinism: passing the same seed twice must give identical output.


def test_contagion_simulator_seed_is_deterministic():
    a = vormap_contagion.ContagionSimulator(n_points=15, ticks=3, seed=123).pts
    b = vormap_contagion.ContagionSimulator(n_points=15, ticks=3, seed=123).pts
    assert a == b


def test_ecosystem_simulator_seed_is_deterministic():
    a = vormap_ecosystem.EcosystemSimulator(n_points=15, n_species=3, ticks=3, seed=123).populations
    b = vormap_ecosystem.EcosystemSimulator(n_points=15, n_species=3, ticks=3, seed=123).populations
    assert a == b


def test_laplace_noise_seed_is_deterministic():
    a = vormap_privacy.laplace_noise(_PTS, epsilon=1.0, seed=99)
    b = vormap_privacy.laplace_noise(_PTS, epsilon=1.0, seed=99)
    assert a == b


def test_donut_mask_seed_is_deterministic():
    a = vormap_privacy.donut_mask(_PTS, r_min=1.0, r_max=5.0, seed=99)
    b = vormap_privacy.donut_mask(_PTS, r_min=1.0, r_max=5.0, seed=99)
    assert a == b


# ---------------------------------------------------------------------------
# Bonus bug: optimize_privacy seed-shadowing (issue #194 "Bonus bug").
#
# Two distinct ``seed=`` values must give distinct trial trajectories.
# Pre-fix, the hard-coded ``seed=42`` inside ``_trial`` short-circuited
# the user's seed: both runs produced identical output.


def test_optimize_privacy_seed_argument_is_respected_laplace():
    r1 = vormap_privacy.optimize_privacy(_PTS, method="laplace", max_distortion=5.0, seed=1)
    r2 = vormap_privacy.optimize_privacy(_PTS, method="laplace", max_distortion=5.0, seed=2)
    # The points sequence must differ when seeds differ; if they're
    # identical, the inner trial RNG is being seeded with a constant
    # (the bug described in issue #194).
    assert r1.points != r2.points, (
        "optimize_privacy(method=laplace) ignored the user-supplied seed: "
        "two different seeds produced identical noisy points. See issue #194."
    )


def test_optimize_privacy_seed_argument_is_respected_donut():
    r1 = vormap_privacy.optimize_privacy(_PTS, method="donut", max_distortion=20.0, seed=1)
    r2 = vormap_privacy.optimize_privacy(_PTS, method="donut", max_distortion=20.0, seed=2)
    assert r1.points != r2.points, (
        "optimize_privacy(method=donut) ignored the user-supplied seed: "
        "two different seeds produced identical noisy points. See issue #194."
    )


def test_optimize_privacy_seed_argument_is_deterministic_laplace():
    r1 = vormap_privacy.optimize_privacy(_PTS, method="laplace", max_distortion=5.0, seed=42)
    r2 = vormap_privacy.optimize_privacy(_PTS, method="laplace", max_distortion=5.0, seed=42)
    assert r1.points == r2.points
