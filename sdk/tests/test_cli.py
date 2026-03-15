"""Tests for agentlens.cli module."""

import json
import os
from unittest import mock

import pytest

from agentlens.cli import main, _fmt_ts, _print_table


class TestFmtTs:
    def test_none(self):
        assert _fmt_ts(None) == "-"

    def test_iso_format(self):
        result = _fmt_ts("2026-03-14T22:15:00Z")
        assert "2026-03-14" in result
        assert "22:15:00" in result

    def test_short_string(self):
        assert _fmt_ts("abc") == "abc"


class TestPrintTable:
    def test_basic(self, capsys):
        _print_table(["A", "B"], [["x", "y"], ["longer", "z"]])
        out = capsys.readouterr().out
        assert "A" in out
        assert "longer" in out


class TestMainSessions:
    def test_sessions_no_connect(self):
        """Should exit with error when backend is unreachable."""
        with mock.patch.dict(os.environ, {"AGENTLENS_URL": "http://127.0.0.1:1"}):
            with pytest.raises(SystemExit):
                main(["sessions"])

    def test_sessions_mock(self):
        """Mock a successful sessions response."""
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = [
            {
                "session_id": "sess-001",
                "status": "completed",
                "event_count": 5,
                "total_tokens": 1200,
                "created_at": "2026-03-14T10:00:00Z",
            }
        ]
        fake_resp.raise_for_status = mock.MagicMock()

        fake_client = mock.MagicMock()
        fake_client.get.return_value = fake_resp
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("agentlens.cli.httpx.Client", return_value=fake_client):
            # Should not raise
            main(["sessions", "--limit", "5"])


class TestMainExport:
    def test_export_json_mock(self, capsys):
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = [
            {"event_type": "llm_call", "model": "gpt-4", "tokens": 100}
        ]
        fake_resp.raise_for_status = mock.MagicMock()

        fake_client = mock.MagicMock()
        fake_client.get.return_value = fake_resp
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("agentlens.cli.httpx.Client", return_value=fake_client):
            main(["export", "sess-001"])
            out = capsys.readouterr().out
            data = json.loads(out)
            assert len(data) == 1
            assert data[0]["model"] == "gpt-4"

    def test_export_csv_mock(self, capsys):
        fake_resp = mock.MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = [
            {"event_type": "llm_call", "model": "gpt-4", "tokens": 100}
        ]
        fake_resp.raise_for_status = mock.MagicMock()

        fake_client = mock.MagicMock()
        fake_client.get.return_value = fake_resp
        fake_client.__enter__ = mock.MagicMock(return_value=fake_client)
        fake_client.__exit__ = mock.MagicMock(return_value=False)

        with mock.patch("agentlens.cli.httpx.Client", return_value=fake_client):
            main(["export", "sess-001", "--format", "csv"])
            out = capsys.readouterr().out
            assert "event_type" in out
            assert "gpt-4" in out
