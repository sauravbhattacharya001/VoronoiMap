"""Tests for vormap_pipeline — batch analysis pipeline."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import vormap_pipeline as vp


class TestSafeJoin(unittest.TestCase):
    """Path-traversal protection in _safe_join."""

    def test_simple_filename(self):
        result = vp._safe_join("/base", "output.json")
        self.assertTrue(result.endswith("output.json"))

    def test_rejects_absolute_unix(self):
        with self.assertRaises(ValueError):
            vp._safe_join("/base", "/etc/passwd")

    def test_rejects_absolute_windows(self):
        with self.assertRaises(ValueError):
            vp._safe_join("/base", "C:\\Windows\\system32")

    def test_rejects_dotdot_traversal(self):
        with self.assertRaises(ValueError):
            vp._safe_join("/base", "../../../etc/passwd")

    def test_rejects_embedded_dotdot(self):
        with self.assertRaises(ValueError):
            vp._safe_join("/base", "subdir/../../etc/passwd")

    def test_subdirectory_ok(self):
        result = vp._safe_join("/base", "sub/file.csv")
        self.assertIn("sub", result)
        self.assertTrue(result.endswith("file.csv"))


class TestValidatePipeline(unittest.TestCase):
    """Pipeline config validation."""

    def test_missing_data_file(self):
        issues = vp.validate_pipeline({"num_points": 5, "steps": []})
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("data_file" in i.message for i in errors))

    def test_missing_num_points(self):
        issues = vp.validate_pipeline({"data_file": "x.txt", "steps": []})
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("num_points" in i.message for i in errors))

    def test_missing_steps(self):
        issues = vp.validate_pipeline({"data_file": "x.txt", "num_points": 5})
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("steps" in i.message for i in errors))

    def test_empty_steps_warning(self):
        issues = vp.validate_pipeline({
            "data_file": "x.txt", "num_points": 5, "steps": []
        })
        warnings = [i for i in issues if i.level == "warning"]
        self.assertTrue(any("no steps" in i.message for i in warnings))

    def test_unknown_step_type(self):
        config = {
            "data_file": "x.txt", "num_points": 5,
            "steps": [{"type": "nonexistent_step"}]
        }
        issues = vp.validate_pipeline(config)
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("unknown type" in i.message for i in errors))

    def test_duplicate_output_keys(self):
        config = {
            "data_file": "x.txt", "num_points": 5,
            "steps": [
                {"type": "hotspot", "output_key": "dup"},
                {"type": "trend", "output_key": "dup"},
            ]
        }
        issues = vp.validate_pipeline(config)
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("duplicate output_key" in i.message for i in errors))

    def test_path_traversal_in_step_file(self):
        config = {
            "data_file": "x.txt", "num_points": 5,
            "steps": [{"type": "export", "file": "../../etc/passwd"}]
        }
        issues = vp.validate_pipeline(config)
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("path traversal" in i.message for i in errors))

    def test_valid_config_no_errors(self):
        config = {
            "data_file": "x.txt", "num_points": 5,
            "steps": [
                {"type": "hotspot", "output_key": "hot"},
                {"type": "export", "file": "results.json"},
            ]
        }
        issues = vp.validate_pipeline(config)
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)

    def test_step_missing_type(self):
        config = {
            "data_file": "x.txt", "num_points": 5,
            "steps": [{"output_key": "foo"}]
        }
        issues = vp.validate_pipeline(config)
        errors = [i for i in issues if i.level == "error"]
        self.assertTrue(any("missing 'type'" in i.message for i in errors))


class TestStepResult(unittest.TestCase):
    """StepResult data class."""

    def test_to_dict(self):
        sr = vp.StepResult(
            step_index=0, step_type="hotspot", output_key="h",
            status="ok", duration_ms=123.456, message="Success"
        )
        d = sr.to_dict()
        self.assertEqual(d["step_index"], 0)
        self.assertEqual(d["step_type"], "hotspot")
        self.assertEqual(d["duration_ms"], 123.5)
        self.assertEqual(d["status"], "ok")


class TestPipelineResult(unittest.TestCase):
    """PipelineResult data class."""

    def test_success_when_no_failures(self):
        pr = vp.PipelineResult(
            name="test", data_file="x.txt", total_steps=2,
            completed=2, skipped=0, failed=0, total_duration_ms=100
        )
        self.assertTrue(pr.success)

    def test_failure_when_has_failures(self):
        pr = vp.PipelineResult(
            name="test", data_file="x.txt", total_steps=2,
            completed=1, skipped=0, failed=1, total_duration_ms=100
        )
        self.assertFalse(pr.success)

    def test_summary_text_contains_name(self):
        pr = vp.PipelineResult(
            name="My Pipeline", data_file="x.txt", total_steps=1,
            completed=1, skipped=0, failed=0, total_duration_ms=50
        )
        text = pr.summary_text()
        self.assertIn("My Pipeline", text)
        self.assertIn("SUCCESS", text)

    def test_to_dict_structure(self):
        pr = vp.PipelineResult(
            name="test", data_file="x.txt", total_steps=0,
            completed=0, skipped=0, failed=0, total_duration_ms=0
        )
        d = pr.to_dict()
        self.assertIn("name", d)
        self.assertIn("steps", d)
        self.assertIn("success", d)


class TestPipelineConstruction(unittest.TestCase):
    """Pipeline loading and construction."""

    def test_from_json_string(self):
        config = json.dumps(vp.EXAMPLE_PIPELINE)
        pipe = vp.Pipeline.from_json(config)
        self.assertEqual(pipe.name, "Spatial Analysis Pipeline")
        self.assertEqual(len(pipe.steps), 6)

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(vp.EXAMPLE_PIPELINE, f)
            f.flush()
            path = f.name
        try:
            pipe = vp.Pipeline.from_file(path)
            self.assertEqual(pipe.name, "Spatial Analysis Pipeline")
        finally:
            os.unlink(path)

    def test_validate_example(self):
        pipe = vp.Pipeline(vp.EXAMPLE_PIPELINE)
        issues = pipe.validate()
        errors = [i for i in issues if i.level == "error"]
        self.assertEqual(len(errors), 0)


class TestPipelineDryRun(unittest.TestCase):
    """Pipeline execution in dry-run mode."""

    def test_dry_run_skips_all(self):
        pipe = vp.Pipeline(vp.EXAMPLE_PIPELINE)
        result = pipe.run(dry_run=True)
        self.assertEqual(result.completed, 0)
        self.assertEqual(result.skipped, len(vp.EXAMPLE_PIPELINE["steps"]))
        self.assertEqual(result.failed, 0)
        self.assertTrue(result.success)

    def test_only_filter(self):
        pipe = vp.Pipeline(vp.EXAMPLE_PIPELINE)
        result = pipe.run(dry_run=True, only=["hotspot", "trend"])
        # Only hotspot and trend should be "skipped" (dry run), rest filtered
        dry_run_types = [s.step_type for s in result.steps if s.message == "Dry run — would execute"]
        self.assertIn("hotspot", dry_run_types)
        self.assertIn("trend", dry_run_types)
        self.assertNotIn("network", dry_run_types)

    def test_skip_filter(self):
        pipe = vp.Pipeline(vp.EXAMPLE_PIPELINE)
        result = pipe.run(dry_run=True, skip=["export"])
        export_steps = [s for s in result.steps if s.step_type == "export"]
        self.assertTrue(all(s.message == "In --skip filter" for s in export_steps))


class TestPipelineExport(unittest.TestCase):
    """Pipeline export step (no external modules needed)."""

    def test_export_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "name": "Export Test",
                "data_file": "nonexistent.txt",
                "num_points": 5,
                "steps": [
                    {"type": "export", "file": "out.json"}
                ]
            }
            pipe = vp.Pipeline(config)
            pipe.output_dir = tmpdir
            result = pipe.run()
            # Export step should succeed (writes empty results)
            self.assertEqual(result.completed, 1)
            self.assertEqual(result.failed, 0)
            out_path = os.path.join(tmpdir, "out.json")
            self.assertTrue(os.path.exists(out_path))
            with open(out_path) as f:
                data = json.load(f)
            self.assertEqual(data["pipeline"], "Export Test")

    def test_export_to_stdout(self):
        config = {
            "name": "Stdout Test",
            "data_file": "nonexistent.txt",
            "num_points": 5,
            "steps": [{"type": "export"}]  # no file = returns json string
        }
        pipe = vp.Pipeline(config)
        result = pipe.run()
        self.assertEqual(result.completed, 1)
        export_step = result.steps[0]
        self.assertEqual(export_step.status, "ok")
        # data should be a JSON string
        parsed = json.loads(export_step.data)
        self.assertEqual(parsed["pipeline"], "Stdout Test")


class TestPipelineReport(unittest.TestCase):
    """Pipeline HTML report generation."""

    def test_report_step(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "name": "Report <Test>",  # test HTML escaping
                "data_file": "test.txt",
                "num_points": 5,
                "steps": [
                    {"type": "report", "file": "report.html"}
                ]
            }
            pipe = vp.Pipeline(config)
            pipe.output_dir = tmpdir
            result = pipe.run()
            self.assertEqual(result.completed, 1)
            report_path = os.path.join(tmpdir, "report.html")
            self.assertTrue(os.path.exists(report_path))
            with open(report_path) as f:
                html = f.read()
            # Verify HTML escaping of pipeline name
            self.assertIn("Report &lt;Test&gt;", html)
            self.assertNotIn("Report <Test>", html.split("<title>")[1].split("</title>")[0])


class TestPipelineCLI(unittest.TestCase):
    """CLI entry point."""

    def test_example_flag(self):
        # --example should return 0
        ret = vp.main(["--example"])
        self.assertEqual(ret, 0)

    def test_no_args_shows_help(self):
        ret = vp.main([])
        self.assertEqual(ret, 1)

    def test_validate_flag(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(vp.EXAMPLE_PIPELINE, f)
            path = f.name
        try:
            ret = vp.main([path, "--validate"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(path)

    def test_dry_run_flag(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(vp.EXAMPLE_PIPELINE, f)
            path = f.name
        try:
            ret = vp.main([path, "--dry-run"])
            self.assertEqual(ret, 0)
        finally:
            os.unlink(path)


class TestValidationIssue(unittest.TestCase):
    """ValidationIssue data class."""

    def test_to_dict(self):
        issue = vp.ValidationIssue("error", 2, "bad step")
        d = issue.to_dict()
        self.assertEqual(d["level"], "error")
        self.assertEqual(d["step_index"], 2)
        self.assertEqual(d["message"], "bad step")


if __name__ == "__main__":
    unittest.main()
