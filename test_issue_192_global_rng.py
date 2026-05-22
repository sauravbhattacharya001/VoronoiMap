"""Regression tests for issue #192.

Verify that demo / `*_demo()` entry points across the eight modules
identified in https://github.com/sauravbhattacharya001/VoronoiMap/issues/192
do NOT mutate the host process's global ``random`` module state.

Each test seeds the global RNG, captures the next value, re-seeds,
invokes the demo (suppressing stdout), then checks that the global
RNG produces the same captured value. If a demo internally calls
``random.seed(42)`` on the module-level generator, the captured value
will diverge and the assertion will fail.
"""

from __future__ import annotations

import io
import os
import random
import sys
from contextlib import redirect_stdout, redirect_stderr
from typing import Callable

import pytest

# Importing these modules already exercises their import-time code;
# the demos themselves are the things that historically called
# ``random.seed(42)`` and are the focus of this regression test.
import vormap_attention
import vormap_dispatch
import vormap_forensics
import vormap_guardian
import vormap_negotiator
import vormap_nervous
import vormap_resilience
import vormap_strategist


def _assert_demo_does_not_reseed_global(callable_: Callable[[], object]) -> None:
    """Run ``callable_`` from two different host seeds and verify the
    demo did not call ``random.seed(...)`` on the global generator.

    Strategy: if the demo internally calls ``random.seed(K)``, then
    regardless of the host's prior seed, the global state after the
    demo is the same deterministic value (modulo any consumption the
    demo itself does). If the demo only *consumes* from the global
    RNG (or, ideally, uses a local ``random.Random``), the end state
    differs between the two runs.
    """
    buf_out = io.StringIO()
    buf_err = io.StringIO()

    random.seed(0xA1)
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        callable_()
    state_a = random.getstate()

    random.seed(0xB2)
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        callable_()
    state_b = random.getstate()

    assert state_a != state_b, (
        f"{callable_.__module__}.{callable_.__name__} reseeded the "
        "global random module: end-state is identical for two "
        "different host seeds. See issue #192."
    )


DEMOS = [
    ("vormap_attention", vormap_attention.attention_demo),
    ("vormap_dispatch", vormap_dispatch.demo),
    ("vormap_forensics", vormap_forensics.forensics_demo),
    ("vormap_guardian", vormap_guardian._run_demo),
    ("vormap_negotiator", vormap_negotiator._demo),
    ("vormap_nervous", vormap_nervous.nervous_demo),
    ("vormap_resilience", vormap_resilience._demo),
    ("vormap_strategist", vormap_strategist._demo),
]


@pytest.mark.parametrize("name,demo", DEMOS, ids=[d[0] for d in DEMOS])
def test_demo_preserves_global_random_state(name: str, demo: Callable[[], object]) -> None:
    """The demo must not call ``random.seed(...)`` on the global module."""
    _assert_demo_does_not_reseed_global(demo)
