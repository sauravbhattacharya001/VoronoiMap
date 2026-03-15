"""AgentLens CLI — query your AgentLens backend from the terminal.

Usage:
    agentlens sessions [--limit N] [--status STATUS]
    agentlens events SESSION_ID [--limit N] [--type TYPE]
    agentlens stats
    agentlens health SESSION_ID
    agentlens alerts [--limit N]
    agentlens explain SESSION_ID
    agentlens export SESSION_ID [--format json|csv]

Environment variables:
    AGENTLENS_URL   Backend URL (default: http://localhost:3000)
    AGENTLENS_KEY   API key (default: default)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx

DEFAULT_URL = "http://localhost:3000"
DEFAULT_KEY = "default"


def _client() -> tuple[httpx.Client, str]:
    url = os.environ.get("AGENTLENS_URL", DEFAULT_URL).rstrip("/")
    key = os.environ.get("AGENTLENS_KEY", DEFAULT_KEY)
    client = httpx.Client(
        base_url=url,
        headers={"x-api-key": key},
        timeout=15.0,
    )
    return client, url


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    client, url = _client()
    try:
        resp = client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        print(f"Error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)
    except httpx.ConnectError:
        print(f"Cannot connect to AgentLens at {url}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def _fmt_ts(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return ts[:19] if ts else "-"


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(str(cell)))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


# ── Commands ─────────────────────────────────────────────────────────


def cmd_sessions(args: argparse.Namespace) -> None:
    """List recent sessions."""
    params: dict[str, Any] = {"limit": args.limit}
    if args.status:
        params["status"] = args.status

    data = _get("/sessions", params)
    sessions = data if isinstance(data, list) else data.get("sessions", data.get("data", []))

    if not sessions:
        print("No sessions found.")
        return

    headers = ["Session ID", "Status", "Events", "Tokens", "Created"]
    rows = []
    for s in sessions:
        rows.append([
            s.get("session_id", "?")[:20],
            s.get("status", "?"),
            str(s.get("event_count", s.get("events", "?"))),
            str(s.get("total_tokens", "?")),
            _fmt_ts(s.get("created_at")),
        ])
    _print_table(headers, rows)
    print(f"\n{len(rows)} session(s)")


def cmd_events(args: argparse.Namespace) -> None:
    """List events for a session."""
    params: dict[str, Any] = {"limit": args.limit}
    if args.type:
        params["type"] = args.type

    data = _get(f"/sessions/{args.session_id}/events", params)
    events = data if isinstance(data, list) else data.get("events", data.get("data", []))

    if not events:
        print("No events found.")
        return

    headers = ["Type", "Model", "Tokens", "Duration(ms)", "Timestamp"]
    rows = []
    for e in events:
        rows.append([
            e.get("event_type", "?"),
            e.get("model", "-"),
            str(e.get("total_tokens", e.get("tokens", "-"))),
            str(e.get("duration_ms", "-")),
            _fmt_ts(e.get("timestamp")),
        ])
    _print_table(headers, rows)
    print(f"\n{len(rows)} event(s)")


def cmd_stats(args: argparse.Namespace) -> None:
    """Show aggregate analytics."""
    data = _get("/analytics/summary")

    print("═══ AgentLens Analytics ═══\n")
    for key, val in data.items():
        label = key.replace("_", " ").title()
        print(f"  {label}: {val}")


def cmd_health(args: argparse.Namespace) -> None:
    """Show health score for a session."""
    data = _get(f"/sessions/{args.session_id}/health")

    grade = data.get("grade", data.get("health_grade", "?"))
    score = data.get("score", data.get("health_score", "?"))
    print(f"Session: {args.session_id}")
    print(f"Grade:   {grade}")
    print(f"Score:   {score}")

    details = data.get("details", data.get("metrics", {}))
    if details:
        print("\nMetrics:")
        for k, v in details.items():
            print(f"  {k}: {v}")


def cmd_alerts(args: argparse.Namespace) -> None:
    """List recent alerts."""
    data = _get("/alerts", {"limit": args.limit})
    alerts = data if isinstance(data, list) else data.get("alerts", data.get("data", []))

    if not alerts:
        print("No alerts.")
        return

    headers = ["Severity", "Rule", "Message", "Fired At"]
    rows = []
    for a in alerts:
        rows.append([
            a.get("severity", "?"),
            a.get("rule_name", a.get("rule", "?"))[:20],
            (a.get("message", "?"))[:40],
            _fmt_ts(a.get("fired_at", a.get("created_at"))),
        ])
    _print_table(headers, rows)


def cmd_explain(args: argparse.Namespace) -> None:
    """Get a human-readable explanation of a session."""
    data = _get(f"/sessions/{args.session_id}/explain")
    explanation = data.get("explanation", data.get("summary", json.dumps(data, indent=2)))
    print(explanation)


def cmd_export(args: argparse.Namespace) -> None:
    """Export session data as JSON or CSV."""
    data = _get(f"/sessions/{args.session_id}/events", {"limit": 1000})
    events = data if isinstance(data, list) else data.get("events", data.get("data", []))

    if args.format == "csv":
        if not events:
            print("No events to export.")
            return
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=events[0].keys())
        writer.writeheader()
        for e in events:
            writer.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in e.items()})
        print(output.getvalue())
    else:
        print(json.dumps(events, indent=2))


# ── Main ─────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="agentlens",
        description="AgentLens CLI — query your AgentLens observability backend",
    )
    parser.add_argument(
        "--url", default=None,
        help="Backend URL (or set AGENTLENS_URL env var)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # sessions
    p = sub.add_parser("sessions", help="List recent sessions")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--status", default=None)

    # events
    p = sub.add_parser("events", help="List events for a session")
    p.add_argument("session_id")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--type", default=None)

    # stats
    sub.add_parser("stats", help="Show aggregate analytics")

    # health
    p = sub.add_parser("health", help="Show health score for a session")
    p.add_argument("session_id")

    # alerts
    p = sub.add_parser("alerts", help="List recent alerts")
    p.add_argument("--limit", type=int, default=20)

    # explain
    p = sub.add_parser("explain", help="Explain a session in plain English")
    p.add_argument("session_id")

    # export
    p = sub.add_parser("export", help="Export session events as JSON or CSV")
    p.add_argument("session_id")
    p.add_argument("--format", choices=["json", "csv"], default="json")

    args = parser.parse_args(argv)

    if args.url:
        os.environ["AGENTLENS_URL"] = args.url

    commands = {
        "sessions": cmd_sessions,
        "events": cmd_events,
        "stats": cmd_stats,
        "health": cmd_health,
        "alerts": cmd_alerts,
        "explain": cmd_explain,
        "export": cmd_export,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
