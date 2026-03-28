"""Tests for vormap_pipeline — validation, config, dry-run, and filtering.

These tests exercise the pipeline's pure-logic paths (validation,
configuration parsing, dry-run mode, step filtering) without requiring
the heavy vormap analysis modules to be installed.
"""

import json
import os
import sys
import tempfile
import pytest

# Ensure repo root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_pipeline import (
    Pipeline,
    PipelineResult,
    StepResult,
    ValidationIssue,
    validate_pipeline,
    STEP_TYPES,
)


# ── Fixtures ─────────────────────────────────────────────────────────

def _minimal_config(**overrides):
    """Return a minimal valid pipeline config dict."""
    cfg = {
        "name": "Test Pipeline",
        "data_file": "test.txt",
        "num_points": 5,
        "steps": [
            {"type": "export", "file": "out.json", "output_key": "exp"},
        ],
    }
    cfg.update(overrides)
    return cfg


# ── validate_pipeline ────────────────────────────────────────────────

class TestValidation:
    """Tests for validate_pipeline()."""

    def test_valid_minimal_config(self):
        issues = validate_pipeline(_minimal_config())
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0

    def test_missing_data_file(self):
        cfg = _minimal_config()
        del cfg["data_file"]
        issues = validate_pipeline(cfg)
        assert any("data_file" in i.message for i in issues)

    def test_missing_num_points(self):
        cfg = _minimal_config()
        del cfg["num_points"]
        issues = validate_pipeline(cfg)
        assert any("num_points" in i.message for i in issues)

    def test_missing_steps(self):
        cfg = _minimal_config()
        del cfg["steps"]
        issues = validate_pipeline(cfg)
        assert any("steps" in i.message for i in issues)

    def test_steps_not_list(self):
        issues = validate_pipeline(_minimal_config(steps="bad"))
        assert any("steps must be a list" in i.message for i in issues)

    def test_empty_steps_warning(self):
        issues = validate_pipeline(_minimal_config(steps=[]))
        warnings = [i for i in issues if i.level == "warning"]
        assert any("no steps" in i.message for i in warnings)

    def test_unknown_step_type(self):
        cfg = _minimal_config(steps=[{"type": "nonexistent"}])
        issues = validate_pipeline(cfg)
        assert any("unknown type" in i.message for i in issues)

    def test_missing_step_type(self):
        cfg = _minimal_config(steps=[{"output_key": "x"}])
        issues = validate_pipeline(cfg)
        assert any("missing 'type'" in i.message for i in issues)

    def test_step_not_dict(self):
        cfg = _minimal_config(steps=["bad"])
        issues = validate_pipeline(cfg)
        assert any("must be a dict" in i.message for i in issues)

    def test_duplicate_output_key(self):
        cfg = _minimal_config(steps=[
            {"type": "export", "output_key": "dup"},
            {"type": "export", "output_key": "dup"},
        ])
        issues = validate_pipeline(cfg)
        assert any("duplicate output_key" in i.message for i in issues)

    def test_export_without_file_warning(self):
        cfg = _minimal_config(steps=[{"type": "export"}])
        issues = validate_pipeline(cfg)
        warnings = [i for i in issues if i.level == "warning"]
        assert any("no 'file'" in i.message for i in warnings)

    def test_all_step_types_accepted(self):
        """Every STEP_TYPE should be accepted without an 'unknown type' error."""
        steps = [{"type": t, "output_key": f"k{i}"} for i, t in enumerate(STEP_TYPES)]
        cfg = _minimal_config(steps=steps)
        issues = validate_pipeline(cfg)
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 0


# ── Pipeline construction ────────────────────────────────────────────

class TestPipelineConstruction:

    def test_from_dict(self):
        p = Pipeline(_minimal_config())
        assert p.name == "Test Pipeline"
        assert p.num_points == 5
        assert len(p.steps) == 1

    def test_from_json_string(self):
        p = Pipeline.from_json(json.dumps(_minimal_config()))
        assert p.name == "Test Pipeline"

    def test_from_file(self):
        cfg = _minimal_config()
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            path = f.name
        try:
            p = Pipeline.from_file(path)
            assert p.name == "Test Pipeline"
        finally:
            os.unlink(path)

    def test_defaults(self):
        p = Pipeline({"steps": []})
        assert p.name == "Unnamed Pipeline"
        assert p.data_file == ""
        assert p.num_points == 5
        assert p.output_dir == "."


# ── Dry-run ──────────────────────────────────────────────────────────

class TestDryRun:

    def test_dry_run_skips_all(self):
        cfg = _minimal_config(steps=[
            {"type": "export", "file": "a.json"},
            {"type": "export", "file": "b.json"},
        ])
        p = Pipeline(cfg)
        result = p.run(dry_run=True)
        assert isinstance(result, PipelineResult)
        assert result.completed == 0
        assert result.skipped == 2
        assert result.failed == 0
        assert result.success is True

    def test_dry_run_step_messages(self):
        p = Pipeline(_minimal_config())
        result = p.run(dry_run=True)
        for sr in result.steps:
            assert sr.status == "skipped"
            assert "Dry run" in sr.message


# ── Step filtering (only / skip) ─────────────────────────────────────

class TestStepFiltering:

    def _multi_step_config(self):
        return _minimal_config(steps=[
            {"type": "hotspot", "output_key": "h"},
            {"type": "trend", "output_key": "t"},
            {"type": "export", "file": "out.json", "output_key": "e"},
        ])

    def test_only_filter(self):
        p = Pipeline(self._multi_step_config())
        result = p.run(only=["export"])
        # hotspot and trend should be skipped by filter
        skipped_types = [s.step_type for s in result.steps if s.status == "skipped"
                         and "Not in --only" in s.message]
        assert "hotspot" in skipped_types
        assert "trend" in skipped_types

    def test_skip_filter(self):
        p = Pipeline(self._multi_step_config())
        result = p.run(skip=["hotspot", "trend"])
        skipped_types = [s.step_type for s in result.steps if s.status == "skipped"
                         and "In --skip" in s.message]
        assert "hotspot" in skipped_types
        assert "trend" in skipped_types


# ── PipelineResult / StepResult ──────────────────────────────────────

class TestResultObjects:

    def test_pipeline_result_success(self):
        r = PipelineResult("t", "f", 2, 2, 0, 0, 100.0)
        assert r.success is True

    def test_pipeline_result_failure(self):
        r = PipelineResult("t", "f", 2, 1, 0, 1, 100.0)
        assert r.success is False

    def test_pipeline_result_to_dict(self):
        r = PipelineResult("t", "f", 1, 1, 0, 0, 50.5)
        d = r.to_dict()
        assert d["name"] == "t"
        assert d["success"] is True
        assert d["total_duration_ms"] == 50.5

    def test_step_result_to_dict(self):
        sr = StepResult(0, "export", "k", "ok", 12.3, "Success")
        d = sr.to_dict()
        assert d["step_type"] == "export"
        assert d["status"] == "ok"
        assert d["duration_ms"] == 12.3

    def test_summary_text(self):
        r = PipelineResult("Demo", "data.txt", 1, 1, 0, 0, 42.0,
                           [StepResult(0, "export", None, "ok", 42.0, "ok")])
        text = r.summary_text()
        assert "Demo" in text
        assert "SUCCESS" in text

    def test_summary_text_with_error(self):
        r = PipelineResult("Demo", "data.txt", 1, 0, 0, 1, 10.0,
                           [StepResult(0, "hotspot", None, "error", 10.0, "boom")])
        text = r.summary_text()
        assert "FAILED" in text
        assert "boom" in text


# ── ValidationIssue ──────────────────────────────────────────────────

class TestValidationIssue:

    def test_to_dict(self):
        vi = ValidationIssue("error", 0, "bad step")
        d = vi.to_dict()
        assert d["level"] == "error"
        assert d["step_index"] == 0
        assert d["message"] == "bad step"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
