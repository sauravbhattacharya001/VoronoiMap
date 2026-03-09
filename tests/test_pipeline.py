"""Tests for vormap_pipeline — batch analysis pipeline."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from vormap_pipeline import (
    EXAMPLE_PIPELINE,
    STEP_TYPES,
    Pipeline,
    PipelineResult,
    StepResult,
    ValidationIssue,
    validate_pipeline,
    main,
)


class TestStepResult(unittest.TestCase):
    def test_to_dict(self):
        sr = StepResult(0, "hotspot", "hot", "ok", 123.4, "Success")
        d = sr.to_dict()
        self.assertEqual(d["step_index"], 0)
        self.assertEqual(d["step_type"], "hotspot")
        self.assertEqual(d["status"], "ok")
        self.assertEqual(d["duration_ms"], 123.4)

    def test_no_output_key(self):
        sr = StepResult(1, "export", None, "ok", 50, "Exported")
        d = sr.to_dict()
        self.assertIsNone(d["output_key"])


class TestPipelineResult(unittest.TestCase):
    def _result(self, completed=2, failed=0, skipped=1):
        return PipelineResult(
            name="test",
            data_file="data.txt",
            total_steps=completed + failed + skipped,
            completed=completed,
            skipped=skipped,
            failed=failed,
            total_duration_ms=500.0,
            steps=[
                StepResult(0, "hotspot", "hot", "ok", 200, "OK"),
                StepResult(1, "trend", "tr", "ok", 150, "OK"),
                StepResult(2, "export", None, "skipped", 0, "Skipped"),
            ],
        )

    def test_success_true(self):
        r = self._result(completed=2, failed=0)
        self.assertTrue(r.success)

    def test_success_false(self):
        r = self._result(completed=1, failed=1)
        self.assertFalse(r.success)

    def test_to_dict(self):
        r = self._result()
        d = r.to_dict()
        self.assertEqual(d["name"], "test")
        self.assertTrue(d["success"])
        self.assertEqual(len(d["steps"]), 3)

    def test_summary_text(self):
        r = self._result()
        text = r.summary_text()
        self.assertIn("Pipeline Execution Summary", text)
        self.assertIn("SUCCESS", text)
        self.assertIn("test", text)

    def test_summary_text_failed(self):
        r = self._result(completed=1, failed=1)
        text = r.summary_text()
        self.assertIn("FAILED", text)


class TestValidationIssue(unittest.TestCase):
    def test_to_dict(self):
        vi = ValidationIssue("error", 0, "Missing type")
        d = vi.to_dict()
        self.assertEqual(d["level"], "error")
        self.assertEqual(d["step_index"], 0)


class TestValidatePipeline(unittest.TestCase):
    def _valid_config(self):
        return {
            "name": "test",
            "data_file": "data.txt",
            "num_points": 5,
            "steps": [
                {"type": "hotspot", "attribute": "area", "output_key": "h"},
            ],
        }

    def test_valid_config(self):
        issues = validate_pipeline(self._valid_config())
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)

    def test_missing_data_file(self):
        config = self._valid_config()
        del config["data_file"]
        issues = validate_pipeline(config)
        self.assertTrue(any("data_file" in i.message for i in issues))

    def test_missing_num_points(self):
        config = self._valid_config()
        del config["num_points"]
        issues = validate_pipeline(config)
        self.assertTrue(any("num_points" in i.message for i in issues))

    def test_missing_steps(self):
        config = {"data_file": "x.txt", "num_points": 5}
        issues = validate_pipeline(config)
        self.assertTrue(any("steps" in i.message for i in issues))

    def test_empty_steps_warning(self):
        config = {"data_file": "x.txt", "num_points": 5, "steps": []}
        issues = validate_pipeline(config)
        warnings = [i for i in issues if i.level == "warning"]
        self.assertTrue(any("no steps" in i.message for i in warnings))

    def test_unknown_step_type(self):
        config = {
            "data_file": "x.txt",
            "num_points": 5,
            "steps": [{"type": "invalid_step"}],
        }
        issues = validate_pipeline(config)
        self.assertTrue(any("unknown type" in i.message for i in issues))

    def test_missing_step_type(self):
        config = {
            "data_file": "x.txt",
            "num_points": 5,
            "steps": [{"attribute": "area"}],
        }
        issues = validate_pipeline(config)
        self.assertTrue(any("missing 'type'" in i.message for i in issues))

    def test_duplicate_output_keys(self):
        config = {
            "data_file": "x.txt",
            "num_points": 5,
            "steps": [
                {"type": "hotspot", "output_key": "same"},
                {"type": "trend", "output_key": "same"},
            ],
        }
        issues = validate_pipeline(config)
        self.assertTrue(any("duplicate" in i.message for i in issues))

    def test_non_list_steps(self):
        config = {
            "data_file": "x.txt",
            "num_points": 5,
            "steps": "not_a_list",
        }
        issues = validate_pipeline(config)
        self.assertTrue(any("must be a list" in i.message for i in issues))

    def test_non_dict_step(self):
        config = {
            "data_file": "x.txt",
            "num_points": 5,
            "steps": ["not_a_dict"],
        }
        issues = validate_pipeline(config)
        self.assertTrue(any("must be a dict" in i.message for i in issues))

    def test_all_step_types_valid(self):
        for st in STEP_TYPES:
            config = {
                "data_file": "x.txt",
                "num_points": 5,
                "steps": [{"type": st}],
            }
            issues = validate_pipeline(config)
            errors = [i for i in issues if i.level == "error"]
            self.assertEqual(len(errors), 0,
                             f"Step type '{st}' unexpectedly invalid")


class TestPipeline(unittest.TestCase):
    def _config(self, steps=None):
        return {
            "name": "Test Pipeline",
            "data_file": "nonexistent.txt",
            "num_points": 5,
            "steps": steps or [],
        }

    def test_from_json(self):
        config = self._config()
        p = Pipeline.from_json(json.dumps(config))
        self.assertEqual(p.name, "Test Pipeline")
        self.assertEqual(p.num_points, 5)

    def test_from_file(self):
        config = self._config([{"type": "export", "file": "out.json"}])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as f:
            json.dump(config, f)
            f.flush()
            path = f.name
        try:
            p = Pipeline.from_file(path)
            self.assertEqual(p.name, "Test Pipeline")
        finally:
            os.unlink(path)

    def test_validate(self):
        p = Pipeline(self._config())
        issues = p.validate()
        # Valid config — no errors
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)

    def test_dry_run(self):
        config = self._config([
            {"type": "hotspot", "output_key": "h"},
            {"type": "trend", "output_key": "t"},
        ])
        p = Pipeline(config)
        result = p.run(dry_run=True)
        self.assertEqual(result.completed, 0)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.failed, 0)
        self.assertTrue(result.success)

    def test_only_filter(self):
        config = self._config([
            {"type": "hotspot", "output_key": "h"},
            {"type": "trend", "output_key": "t"},
            {"type": "export", "file": "out.json"},
        ])
        p = Pipeline(config)
        result = p.run(dry_run=True, only=["export"])
        # Only export should be "run" (dry-run skipped), others filtered
        statuses = [(s.step_type, s.status) for s in result.steps]
        self.assertEqual(statuses[0], ("hotspot", "skipped"))
        self.assertEqual(statuses[1], ("trend", "skipped"))
        self.assertEqual(statuses[2], ("export", "skipped"))  # dry-run

    def test_skip_filter(self):
        config = self._config([
            {"type": "hotspot", "output_key": "h"},
            {"type": "trend", "output_key": "t"},
        ])
        p = Pipeline(config)
        result = p.run(dry_run=True, skip=["hotspot"])
        self.assertEqual(result.steps[0].status, "skipped")
        self.assertEqual(result.steps[0].message, "In --skip filter")

    def test_run_empty_pipeline(self):
        config = self._config([])
        p = Pipeline(config)
        result = p.run()
        self.assertEqual(result.total_steps, 0)
        self.assertTrue(result.success)

    def test_run_export_to_stdout(self):
        """Export with no file prints to stdout (returns JSON string)."""
        config = self._config([{"type": "export"}])
        p = Pipeline(config)
        result = p.run()
        # Export step should succeed (writes to string, no file)
        self.assertEqual(result.completed, 1)
        self.assertEqual(result.failed, 0)

    def test_run_export_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config([
                {"type": "export", "file": "results.json"},
            ])
            p = Pipeline(config)
            p.output_dir = tmpdir
            result = p.run()
            self.assertEqual(result.completed, 1)
            outpath = os.path.join(tmpdir, "results.json")
            self.assertTrue(os.path.exists(outpath))
            with open(outpath) as f:
                data = json.load(f)
            self.assertEqual(data["pipeline"], "Test Pipeline")

    def test_run_report_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config([
                {"type": "report", "file": "report.html"},
            ])
            p = Pipeline(config)
            p.output_dir = tmpdir
            result = p.run()
            self.assertEqual(result.completed, 1)
            outpath = os.path.join(tmpdir, "report.html")
            self.assertTrue(os.path.exists(outpath))
            with open(outpath, encoding="utf-8") as f:
                html = f.read()
            self.assertIn("Pipeline Report", html)
            self.assertIn("Test Pipeline", html)

    def test_unknown_step_type_errors(self):
        config = self._config([{"type": "nonexistent_step"}])
        p = Pipeline(config)
        result = p.run()
        self.assertEqual(result.failed, 1)
        self.assertIn("Unknown step type", result.steps[0].message)

    def test_results_property(self):
        config = self._config([{"type": "export", "output_key": "exp"}])
        p = Pipeline(config)
        p.run()
        self.assertIn("exp", p.results)

    def test_output_dir_default(self):
        p = Pipeline(self._config())
        self.assertEqual(p.output_dir, ".")


class TestExamplePipeline(unittest.TestCase):
    def test_example_valid(self):
        issues = validate_pipeline(EXAMPLE_PIPELINE)
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)

    def test_example_has_required_fields(self):
        self.assertIn("name", EXAMPLE_PIPELINE)
        self.assertIn("data_file", EXAMPLE_PIPELINE)
        self.assertIn("num_points", EXAMPLE_PIPELINE)
        self.assertIn("steps", EXAMPLE_PIPELINE)
        self.assertGreater(len(EXAMPLE_PIPELINE["steps"]), 0)


class TestCLI(unittest.TestCase):
    def test_example_flag(self):
        """--example prints JSON and exits 0."""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--example"])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        data = json.loads(output)
        self.assertEqual(data["name"], "Spatial Analysis Pipeline")

    def test_no_args(self):
        code = main([])
        self.assertEqual(code, 1)

    def test_validate_flag(self):
        config = {
            "name": "test",
            "data_file": "x.txt",
            "num_points": 5,
            "steps": [{"type": "export"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as f:
            json.dump(config, f)
            f.flush()
            path = f.name
        try:
            code = main([path, "--validate"])
            self.assertEqual(code, 0)
        finally:
            os.unlink(path)

    def test_validate_invalid_config(self):
        config = {"steps": [{"type": "bad_type"}]}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as f:
            json.dump(config, f)
            f.flush()
            path = f.name
        try:
            code = main([path, "--validate"])
            self.assertEqual(code, 1)
        finally:
            os.unlink(path)

    def test_dry_run_flag(self):
        config = {
            "name": "dry",
            "data_file": "x.txt",
            "num_points": 5,
            "steps": [{"type": "export", "file": "out.json"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                         delete=False) as f:
            json.dump(config, f)
            f.flush()
            path = f.name
        try:
            code = main([path, "--dry-run"])
            self.assertEqual(code, 0)
        finally:
            os.unlink(path)


class TestStepTypes(unittest.TestCase):
    def test_all_known_types(self):
        expected = {
            "hotspot", "trend", "network", "landscape", "coverage",
            "cluster", "transect", "hotspot_svg", "trend_svg",
            "network_svg", "report", "export",
        }
        self.assertEqual(set(STEP_TYPES), expected)


if __name__ == "__main__":
    unittest.main()
