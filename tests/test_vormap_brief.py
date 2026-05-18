"""Tests for vormap_brief - Autonomous Executive Brief Generator."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_brief


def _write_points(pts, suffix=".txt"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w") as f:
        for x, y in pts:
            f.write(f"{x} {y}\n")
    return path


def _sample_dataset():
    # ~20 mildly clustered points
    pts = []
    base = [(1.0, 1.0), (1.2, 1.1), (1.1, 0.9), (5.0, 5.0), (5.2, 5.1),
            (5.1, 4.9), (5.3, 5.2), (2.0, 8.0), (2.1, 8.1), (2.2, 7.9),
            (8.0, 1.0), (8.1, 1.2), (7.9, 0.8), (4.0, 4.0), (3.5, 4.5),
            (6.0, 6.5), (7.0, 2.5), (3.0, 7.0), (4.5, 2.0), (6.5, 4.0)]
    pts.extend(base)
    return pts


class TestGenerateBrief(unittest.TestCase):
    def setUp(self):
        self.path = _write_points(_sample_dataset())

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_brief_returns_brief_with_headline(self):
        brief = vormap_brief.generate_brief(self.path)
        self.assertIsInstance(brief, vormap_brief.Brief)
        self.assertTrue(brief.headline)
        self.assertGreater(brief.point_count, 0)
        self.assertGreaterEqual(brief.health_score, 0)
        self.assertLessEqual(brief.health_score, 100)

    def test_top_actions_caps_length(self):
        brief = vormap_brief.generate_brief(self.path, top_actions=2)
        self.assertLessEqual(len(brief.next_actions), 2)

    def test_next_action_priorities_renumbered(self):
        brief = vormap_brief.generate_brief(self.path, top_actions=3)
        priorities = [a["priority"] for a in brief.next_actions]
        # Should be sequentially numbered starting from 1
        self.assertEqual(priorities, list(range(1, len(priorities) + 1)))

    def test_markdown_output_includes_header(self):
        brief = vormap_brief.generate_brief(self.path)
        md = brief.to_markdown()
        self.assertIn("# Executive Brief", md)
        self.assertIn("## Headline", md)
        self.assertIn("## Next Actions", md)

    def test_json_output_is_valid(self):
        brief = vormap_brief.generate_brief(self.path)
        text = brief.to_json()
        data = json.loads(text)
        self.assertIn("headline", data)
        self.assertIn("next_actions", data)
        self.assertEqual(data["dataset_path"], self.path)

    def test_html_output_writes_file_with_score(self):
        brief = vormap_brief.generate_brief(self.path)
        out = tempfile.mktemp(suffix=".html")
        try:
            brief.to_html(out)
            with open(out, encoding="utf-8") as f:
                html = f.read()
            self.assertIn("<html", html)
            self.assertIn(str(brief.health_score), html)
            self.assertIn("Executive Brief", html)
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    def test_text_output_is_plain(self):
        brief = vormap_brief.generate_brief(self.path)
        text = brief.to_text()
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 0)
        self.assertNotIn("<html", text)
        self.assertIn("EXECUTIVE BRIEF", text)

    def test_graceful_degradation_when_doctor_fails(self):
        with mock.patch.object(vormap_brief, "_safe_diagnose",
                               return_value={"ok": False, "data": None,
                                             "error": "boom"}):
            brief = vormap_brief.generate_brief(self.path)
        self.assertIn("doctor", brief.section_errors)
        self.assertEqual(brief.section_errors["doctor"], "boom")
        # Should still produce a usable brief
        self.assertTrue(brief.headline)
        md = brief.to_markdown()
        self.assertIn("Section Errors", md)

    def test_graceful_degradation_when_profile_fails(self):
        with mock.patch.object(vormap_brief, "_safe_profile",
                               return_value={"ok": False, "data": None,
                                             "error": "no profile"}):
            brief = vormap_brief.generate_brief(self.path)
        self.assertIn("profile", brief.section_errors)
        self.assertTrue(brief.headline)

    def test_include_sections_restricts_runs(self):
        with mock.patch.object(vormap_brief, "_safe_doctor_called", create=True):
            pass  # no-op placeholder
        with mock.patch.object(vormap_brief, "_safe_diagnose") as m_doctor, \
             mock.patch.object(vormap_brief, "_safe_recommend") as m_rec, \
             mock.patch.object(vormap_brief, "_safe_profile",
                               wraps=vormap_brief._safe_profile) as m_prof:
            m_doctor.return_value = {"ok": True, "data": None, "error": None}
            m_rec.return_value = {"ok": True, "data": None, "error": None}
            vormap_brief.generate_brief(self.path,
                                        include_sections={"profile"})
            self.assertTrue(m_prof.called)
            self.assertFalse(m_doctor.called)
            self.assertFalse(m_rec.called)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.path = _write_points(_sample_dataset())

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def test_cli_json_exit_zero(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = vormap_brief.main([self.path, "--format", "json"])
        self.assertEqual(code, 0)
        out = buf.getvalue().strip()
        self.assertTrue(out)
        # Should be parseable JSON
        data = json.loads(out)
        self.assertIn("headline", data)

    def test_cli_md_default(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = vormap_brief.main([self.path])
        self.assertEqual(code, 0)
        self.assertIn("# Executive Brief", buf.getvalue())

    def test_cli_html_writes_file(self):
        out = tempfile.mktemp(suffix=".html")
        try:
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = vormap_brief.main([self.path, "--format", "html",
                                          "--output", out])
            self.assertEqual(code, 0)
            self.assertTrue(os.path.exists(out))
            with open(out, encoding="utf-8") as f:
                self.assertIn("<html", f.read())
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    def test_cli_missing_dataset(self):
        code = vormap_brief.main(["/no/such/file_xyz.txt"])
        self.assertEqual(code, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
