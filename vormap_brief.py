"""Autonomous Executive Brief Generator for VoronoiMap datasets.

The brief generator is an *agentic* one-shot tool that takes a spatial
point dataset and produces a single, prioritised, human-readable
"executive brief" combining the existing :mod:`vormap_profile`,
:mod:`vormap_doctor`, and :mod:`vormap_recommend` modules into one
narrative.  Instead of running three tools and stitching results
together, the user asks for a brief and the module autonomously runs
the underlying analyses, synthesises findings, and produces a ranked,
opinionated summary with a top "next actions" list.

Sections produced:

- **Headline** - one-sentence dataset summary (count, health, pattern,
  top suggestion).
- **Key Findings** - up to six bullet insights drawn from the profile
  highlights and doctor critical / warning findings, plus the strongest
  recommender reason.
- **Next Actions** - deduplicated top-N action list, prioritising
  doctor auto-fix commands over recommender suggestions.
- **Profile Snapshot** - the most useful basic statistics.

If one of the underlying analyses fails, the brief degrades gracefully
and notes which section was unavailable.

Usage (Python API)::

    from vormap_brief import generate_brief
    brief = generate_brief("datauni5.txt")
    print(brief.to_markdown())
    brief.to_html("brief.html")

CLI::

    python vormap_brief.py datauni5.txt
    python vormap_brief.py datauni5.txt --format json --output brief.json
    python vormap_brief.py datauni5.txt --format html --output brief.html
    python vormap_brief.py datauni5.txt --top 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys


# ---------------------------------------------------------------------------
# Lazy-imported analysers (guarded so this module can be loaded even if a
# downstream tool blows up during import).
# ---------------------------------------------------------------------------

def _safe_profile(path):
    try:
        import vormap_profile
        return {"ok": True, "data": vormap_profile.profile_data(path), "error": None}
    except Exception as exc:  # pragma: no cover - import-time failures rare
        return {"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}


def _safe_diagnose(path):
    try:
        import vormap_doctor
        return {"ok": True, "data": vormap_doctor.diagnose(path), "error": None}
    except Exception as exc:
        return {"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}


def _safe_recommend(path):
    try:
        import vormap_recommend
        return {"ok": True, "data": vormap_recommend.recommend(path), "error": None}
    except Exception as exc:
        return {"ok": False, "data": None, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# Brief data structure
# ---------------------------------------------------------------------------

class Brief:
    """Executive brief for a spatial point dataset.

    Attributes
    ----------
    dataset_path : str
    point_count : int
    health_score : int
        0-100, taken from the doctor (or 100 if the doctor failed).
    headline : str
    key_findings : list[str]
    next_actions : list[dict]
        Each dict has keys ``priority``, ``tool``, ``command``, ``reason``,
        ``source`` (``"doctor"`` or ``"recommend"``).
    profile_summary : dict
        Subset of the profile output useful for at-a-glance display.
    section_errors : dict
        ``{section_name: error_string}`` for any analysis that failed.
    """

    def __init__(self, dataset_path, point_count, health_score, headline,
                 key_findings, next_actions, profile_summary, section_errors):
        self.dataset_path = dataset_path
        self.point_count = point_count
        self.health_score = health_score
        self.headline = headline
        self.key_findings = list(key_findings)
        self.next_actions = list(next_actions)
        self.profile_summary = dict(profile_summary)
        self.section_errors = dict(section_errors)

    # ------------------------------------------------------------------ dict
    def as_dict(self):
        return {
            "dataset_path": self.dataset_path,
            "point_count": self.point_count,
            "health_score": self.health_score,
            "headline": self.headline,
            "key_findings": list(self.key_findings),
            "next_actions": list(self.next_actions),
            "profile_summary": dict(self.profile_summary),
            "section_errors": dict(self.section_errors),
        }

    # ------------------------------------------------------------------ json
    def to_json(self, path=None):
        text = json.dumps(self.as_dict(), indent=2, default=str)
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    # -------------------------------------------------------------- markdown
    def to_markdown(self, path=None):
        lines = []
        lines.append("# Executive Brief")
        lines.append("")
        lines.append(f"*Dataset:* `{self.dataset_path}`")
        lines.append("")
        lines.append("## Headline")
        lines.append("")
        lines.append(self.headline)
        lines.append("")
        lines.append("## Key Findings")
        lines.append("")
        if self.key_findings:
            for f in self.key_findings:
                lines.append(f"- {f}")
        else:
            lines.append("- (no findings)")
        lines.append("")
        lines.append("## Next Actions")
        lines.append("")
        if self.next_actions:
            for a in self.next_actions:
                lines.append(
                    f"- **[{a['priority']}] {a['tool']}** — {a['reason']}"
                )
                lines.append(f"    - `{a['command']}`")
        else:
            lines.append("- (no actions recommended)")
        lines.append("")
        lines.append("## Profile Snapshot")
        lines.append("")
        for k, v in self.profile_summary.items():
            lines.append(f"- **{k}:** {v}")
        if self.section_errors:
            lines.append("")
            lines.append("## Section Errors")
            lines.append("")
            for sec, err in self.section_errors.items():
                lines.append(f"- *{sec}*: {err}")
        text = "\n".join(lines) + "\n"
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        return text

    # ------------------------------------------------------------------ text
    def to_text(self):
        sep = "=" * 60
        lines = [sep, "  EXECUTIVE BRIEF", sep, "",
                 f"  Dataset: {self.dataset_path}",
                 f"  Points:  {self.point_count}",
                 f"  Health:  {self.health_score}/100",
                 "",
                 "  HEADLINE",
                 "  --------",
                 f"  {self.headline}",
                 "",
                 "  KEY FINDINGS",
                 "  ------------"]
        if self.key_findings:
            for f in self.key_findings:
                lines.append(f"  - {f}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("  NEXT ACTIONS")
        lines.append("  ------------")
        if self.next_actions:
            for a in self.next_actions:
                lines.append(f"  [{a['priority']}] {a['tool']}  -  {a['reason']}")
                lines.append(f"      $ {a['command']}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("  PROFILE SNAPSHOT")
        lines.append("  ----------------")
        for k, v in self.profile_summary.items():
            lines.append(f"  {k:>18}: {v}")
        if self.section_errors:
            lines.append("")
            lines.append("  SECTION ERRORS")
            lines.append("  --------------")
            for sec, err in self.section_errors.items():
                lines.append(f"  - {sec}: {err}")
        lines.append(sep)
        return "\n".join(lines)

    # ------------------------------------------------------------------ html
    def to_html(self, path):
        gauge_color = (
            "#4caf50" if self.health_score >= 70
            else "#ff9800" if self.health_score >= 40
            else "#f44336"
        )
        findings_html = "".join(
            f"<li>{_html_escape(f)}</li>" for f in self.key_findings
        ) or "<li><em>none</em></li>"
        actions_html = ""
        for a in self.next_actions:
            actions_html += (
                "<tr>"
                f"<td class='pri'>{a['priority']}</td>"
                f"<td class='tool'>{_html_escape(a['tool'])}</td>"
                f"<td>{_html_escape(a['reason'])}<br>"
                f"<code>{_html_escape(a['command'])}</code></td>"
                f"<td class='src'>{_html_escape(a.get('source', ''))}</td>"
                "</tr>"
            )
        if not actions_html:
            actions_html = "<tr><td colspan='4'><em>no actions recommended</em></td></tr>"
        profile_rows = "".join(
            f"<tr><th>{_html_escape(str(k))}</th><td>{_html_escape(str(v))}</td></tr>"
            for k, v in self.profile_summary.items()
        )
        errs_html = ""
        if self.section_errors:
            items = "".join(
                f"<li><strong>{_html_escape(sec)}</strong>: {_html_escape(err)}</li>"
                for sec, err in self.section_errors.items()
            )
            errs_html = f"<h2>Section Errors</h2><ul>{items}</ul>"
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>VoronoiMap Executive Brief — {_html_escape(self.dataset_path)}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;
     max-width:900px;margin:2em auto;padding:0 1em}}
h1{{color:#58a6ff}}
h2{{color:#7ee787;border-bottom:1px solid #30363d;padding-bottom:4px}}
.gauge{{width:160px;height:160px;border-radius:50%;
       border:10px solid {gauge_color};display:flex;align-items:center;
       justify-content:center;font-size:2.4em;font-weight:bold;margin:1em 0}}
.headline{{font-size:1.15em;background:#161b22;padding:1em;
          border-left:4px solid #58a6ff;border-radius:4px}}
table{{width:100%;border-collapse:collapse;margin-top:0.5em}}
th,td{{padding:8px;border-bottom:1px solid #21262d;text-align:left;
       vertical-align:top}}
th{{color:#8b949e}}
.pri{{font-weight:bold;color:#58a6ff;width:40px;text-align:center}}
.tool{{font-family:monospace;color:#7ee787;white-space:nowrap}}
.src{{color:#8b949e;font-size:0.85em}}
code{{color:#d2a8ff;font-size:0.85em}}
ul{{line-height:1.6}}
</style></head><body>
<h1>&#x1F4CB; VoronoiMap Executive Brief</h1>
<p><em>Dataset:</em> <code>{_html_escape(self.dataset_path)}</code> &nbsp;
<em>Points:</em> {self.point_count}</p>
<div class="gauge">{self.health_score}</div>
<h2>Headline</h2>
<p class="headline">{_html_escape(self.headline)}</p>
<h2>Key Findings</h2>
<ul>{findings_html}</ul>
<h2>Next Actions</h2>
<table>
  <tr><th>#</th><th>Tool</th><th>Reason / Command</th><th>Source</th></tr>
  {actions_html}
</table>
<h2>Profile Snapshot</h2>
<table>{profile_rows}</table>
{errs_html}
</body></html>
"""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        return path


def _html_escape(s):
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# Synthesis logic
# ---------------------------------------------------------------------------

def _extract_profile_summary(profile):
    """Pull the most useful fields out of a profile_data() result."""
    if not isinstance(profile, dict):
        return {}
    out = {}
    basic = profile.get("basic") or {}
    bounds = profile.get("bounds") or {}
    centroid = profile.get("centroid") or {}
    spacing = profile.get("spacing") or {}
    density = profile.get("density") or {}
    pattern = profile.get("spatial_pattern") or {}
    duplicates = profile.get("duplicates") or {}
    outliers = profile.get("outliers") or {}
    if basic.get("count") is not None:
        out["points"] = basic["count"]
    if bounds:
        out["bounding_box"] = (
            f"X[{bounds.get('x_min'):.3f}, {bounds.get('x_max'):.3f}]  "
            f"Y[{bounds.get('y_min'):.3f}, {bounds.get('y_max'):.3f}]"
            if bounds.get("x_min") is not None else ""
        )
    if centroid:
        out["centroid"] = f"({centroid.get('x')}, {centroid.get('y')})"
    if density.get("points_per_unit_area") is not None:
        out["density"] = f"{density['points_per_unit_area']:.6f} pts/unit²"
    if spacing.get("nn_mean") is not None:
        out["nn_mean_spacing"] = round(spacing["nn_mean"], 6)
    if pattern.get("pattern"):
        out["pattern"] = (
            f"{pattern['pattern']} (Clark-Evans R={pattern.get('clark_evans_r')})"
        )
    if duplicates.get("count") is not None:
        out["duplicates"] = duplicates["count"]
    if outliers.get("count") is not None:
        out["outliers"] = outliers["count"]
    return out


def _rank_actions(diag, recs, top_actions):
    """Merge doctor auto-fix commands and recommender suggestions, dedup, rank."""
    actions = []
    seen_commands = set()

    # Critical doctor fixes first
    if diag is not None:
        findings = list(getattr(diag, "findings", []) or [])
        sev_order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
        findings.sort(key=lambda f: sev_order.get(getattr(f, "severity", "info"), 4))
        for f in findings:
            cmd = getattr(f, "fix_command", None)
            if not cmd:
                continue
            if cmd in seen_commands:
                continue
            seen_commands.add(cmd)
            tool = cmd.split()[1] if len(cmd.split()) > 1 else cmd.split()[0]
            tool = os.path.basename(tool).replace(".py", "")
            actions.append({
                "priority": len(actions) + 1,
                "tool": tool,
                "command": cmd,
                "reason": getattr(f, "message", "") or f"Fix {getattr(f, 'check', '')}",
                "source": "doctor",
            })

    # Then recommender suggestions
    if recs:
        for r in recs:
            cmd = r.get("command")
            if not cmd or cmd in seen_commands:
                continue
            seen_commands.add(cmd)
            actions.append({
                "priority": len(actions) + 1,
                "tool": r.get("tool", ""),
                "command": cmd,
                "reason": r.get("reason", ""),
                "source": "recommend",
            })

    if top_actions and top_actions > 0:
        actions = actions[:top_actions]
    # Re-number after slicing
    for i, a in enumerate(actions, 1):
        a["priority"] = i
    return actions


def _build_key_findings(profile, diag, recs, max_items=6):
    findings = []

    # Top profile fact
    if isinstance(profile, dict):
        basic = profile.get("basic") or {}
        pattern = profile.get("spatial_pattern") or {}
        if basic.get("count") is not None and pattern.get("pattern"):
            findings.append(
                f"{basic['count']} points with a {pattern['pattern']} "
                f"spatial pattern (Clark-Evans R={pattern.get('clark_evans_r')})."
            )
        outliers = profile.get("outliers") or {}
        if outliers.get("count"):
            findings.append(f"{outliers['count']} spatial outlier(s) detected.")
        dup = profile.get("duplicates") or {}
        if dup.get("count"):
            findings.append(
                f"{dup['count']} duplicate point(s) "
                f"({dup.get('percentage')}% of dataset)."
            )

    # Doctor critical / warning findings (up to 3)
    if diag is not None:
        dfindings = list(getattr(diag, "findings", []) or [])
        crits = [f for f in dfindings if getattr(f, "severity", "") == "critical"]
        warns = [f for f in dfindings if getattr(f, "severity", "") == "warning"]
        for f in (crits + warns)[:3]:
            sev = getattr(f, "severity", "info").upper()
            msg = getattr(f, "message", "") or getattr(f, "check", "")
            findings.append(f"[{sev}] {msg}")

    # Strongest recommender reason
    if recs:
        top = recs[0]
        findings.append(
            f"Top recommended analysis: {top.get('tool')} — {top.get('reason')}"
        )

    # Cap and dedup while preserving order
    seen = set()
    unique = []
    for f in findings:
        if f in seen:
            continue
        seen.add(f)
        unique.append(f)
        if len(unique) >= max_items:
            break
    return unique


def _build_headline(point_count, health_score, profile, recs):
    pattern_str = ""
    if isinstance(profile, dict):
        pattern = profile.get("spatial_pattern") or {}
        if pattern.get("pattern"):
            pattern_str = f"{pattern['pattern']} pattern"
    top_tool = ""
    if recs:
        top_tool = recs[0].get("tool", "")
    parts = [f"Dataset of {point_count} point(s)"]
    parts.append(f"health {health_score}/100")
    if pattern_str:
        parts.append(pattern_str)
    if top_tool:
        parts.append(f"top suggestion: {top_tool}")
    return " — ".join(parts) + "."


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_brief(dataset_path, *, top_actions=3, include_sections=None):
    """Generate an :class:`Brief` for ``dataset_path``.

    Parameters
    ----------
    dataset_path : str
        Path to a point dataset readable by :func:`vormap.load_data`.
    top_actions : int, optional
        Maximum number of next-action entries.  Default 3.
    include_sections : iterable of str, optional
        Restrict which underlying analyses run.  Allowed names:
        ``"profile"``, ``"doctor"``, ``"recommend"``.  Default (None)
        runs all three.
    """
    allowed = {"profile", "doctor", "recommend"}
    if include_sections is None:
        sections = allowed
    else:
        sections = {s for s in include_sections if s in allowed}

    section_errors = {}

    profile = None
    if "profile" in sections:
        r = _safe_profile(dataset_path)
        if r["ok"]:
            profile = r["data"]
        else:
            section_errors["profile"] = r["error"]

    diag = None
    if "doctor" in sections:
        r = _safe_diagnose(dataset_path)
        if r["ok"]:
            diag = r["data"]
        else:
            section_errors["doctor"] = r["error"]

    recs = None
    if "recommend" in sections:
        r = _safe_recommend(dataset_path)
        if r["ok"]:
            recs = r["data"]
        else:
            section_errors["recommend"] = r["error"]

    # Derive headline numbers
    point_count = 0
    if isinstance(profile, dict):
        point_count = ((profile.get("basic") or {}).get("count") or 0)
    if point_count == 0 and diag is not None:
        point_count = getattr(diag, "point_count", 0) or 0

    health_score = 100
    if diag is not None:
        health_score = int(getattr(diag, "health_score", 100) or 100)

    profile_summary = _extract_profile_summary(profile) if profile else {}
    key_findings = _build_key_findings(profile, diag, recs)
    next_actions = _rank_actions(diag, recs, top_actions)
    headline = _build_headline(point_count, health_score, profile, recs)

    return Brief(
        dataset_path=dataset_path,
        point_count=point_count,
        health_score=health_score,
        headline=headline,
        key_findings=key_findings,
        next_actions=next_actions,
        profile_summary=profile_summary,
        section_errors=section_errors,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(
        prog="vormap_brief",
        description="Generate an executive brief for a spatial point dataset.",
    )
    p.add_argument("dataset", help="Path to point dataset (.txt/.csv/.json/.geojson)")
    p.add_argument("--format", choices=("md", "json", "html", "text"),
                   default="md", help="Output format (default: md)")
    p.add_argument("--output", "-o", default=None,
                   help="Output file path (default: stdout, required for html)")
    p.add_argument("--top", type=int, default=3,
                   help="Maximum number of next-action entries (default: 3)")
    return p


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits with 2 on bad args; surface that to callers.
        return int(exc.code) if exc.code is not None else 2

    if not os.path.exists(args.dataset):
        print(f"error: dataset not found: {args.dataset}", file=sys.stderr)
        return 1

    try:
        brief = generate_brief(args.dataset, top_actions=args.top)
    except Exception as exc:
        print(f"error: failed to generate brief: {exc}", file=sys.stderr)
        return 1

    fmt = args.format
    if fmt == "html":
        out = args.output or "brief.html"
        brief.to_html(out)
        print(f"Wrote HTML brief to {out}")
        return 0

    if fmt == "json":
        text = brief.to_json(args.output)
    elif fmt == "md":
        text = brief.to_markdown(args.output)
    else:  # text
        text = brief.to_text()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(text)

    if not args.output:
        print(text)
    else:
        print(f"Wrote brief to {args.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
