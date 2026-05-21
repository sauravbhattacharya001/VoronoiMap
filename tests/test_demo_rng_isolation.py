"""Regression tests for demo functions preserving host RNG state."""

import contextlib
import io
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _run_silently(func):
    with contextlib.redirect_stdout(io.StringIO()):
        return func()


def _assert_preserves_global_rng(func):
    random.seed(99)
    state = random.getstate()

    _run_silently(func)

    assert random.getstate() == state


def test_attention_demo_preserves_global_rng(tmp_path, monkeypatch):
    from vormap_attention import attention_demo

    monkeypatch.chdir(tmp_path)

    _assert_preserves_global_rng(attention_demo)


def test_dispatch_demo_preserves_global_rng(tmp_path, monkeypatch):
    from vormap_dispatch import demo

    monkeypatch.chdir(tmp_path)

    _assert_preserves_global_rng(demo)


def test_forensics_demo_preserves_global_rng():
    from vormap_forensics import forensics_demo

    _assert_preserves_global_rng(forensics_demo)


def test_guardian_demo_preserves_global_rng():
    from vormap_guardian import _run_demo

    _assert_preserves_global_rng(_run_demo)


def test_negotiator_demo_preserves_global_rng():
    from vormap_negotiator import _demo

    _assert_preserves_global_rng(_demo)


def test_nervous_demo_preserves_global_rng():
    from vormap_nervous import nervous_demo

    _assert_preserves_global_rng(nervous_demo)


def test_resilience_demo_preserves_global_rng():
    from vormap_resilience import _demo

    _assert_preserves_global_rng(_demo)


def test_strategist_demo_preserves_global_rng(tmp_path, monkeypatch):
    import vormap_strategist

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        vormap_strategist,
        "__file__",
        str(tmp_path / "vormap_strategist.py"),
    )
    monkeypatch.setattr(vormap_strategist, "export_html", lambda plan, path: None)

    _assert_preserves_global_rng(vormap_strategist._demo)
