"""Batch Analysis Pipeline — chain multiple VoronoiMap tools together.

Define a multi-step analysis pipeline in JSON and run it with one
command.  Each step invokes a VoronoiMap analysis tool, feeding
results forward so later steps can reference earlier outputs.

Supported step types:

- ``hotspot``   → ``vormap_hotspot.detect_hotspots``
- ``trend``     → ``vormap_trend.fit_trend_surface``
- ``network``   → ``vormap_network.build_network``
- ``landscape`` → ``vormap_landscape.analyze_landscape``
- ``coverage``  → ``vormap_coverage.analyze_coverage``
- ``cluster``   → ``vormap_cluster.cluster_regions``
- ``transect``  → ``vormap_transect.profile_transect``
- ``hotspot_svg``  → export hotspot SVG
- ``trend_svg``    → export trend SVG
- ``network_svg``  → export network SVG
- ``report``    → generate an HTML report (``vormap_report``)
- ``export``    → write combined results to JSON/CSV

Pipeline JSON format::

    {
      "name": "Urban Analysis",
      "data_file": "city_points.txt",
      "num_points": 5,
      "steps": [
        {"type": "hotspot", "attribute": "area", "output_key": "hot"},
        {"type": "trend", "attribute": "area", "order": 2, "output_key": "trnd"},
        {"type": "network", "output_key": "net"},
        {"type": "export", "file": "results.json"}
      ]
    }

CLI::

    python vormap_pipeline.py pipeline.json
    python vormap_pipeline.py pipeline.json --dry-run
    python vormap_pipeline.py pipeline.json --only hotspot,trend
    python vormap_pipeline.py pipeline.json --skip network
    python vormap_pipeline.py pipeline.json --output-dir results/
    python vormap_pipeline.py --example > pipeline.json
"""


import argparse
import html as _html
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Sequence

# ── Path safety ─────────────────────────────────────────────────────


def _safe_join(base_dir: str, untrusted_name: str) -> str:
    """Join *base_dir* and *untrusted_name* with path-traversal protection.

    Rejects absolute paths and ``..`` components so that user-supplied
    filenames in pipeline JSON configs cannot escape *base_dir*.

    Raises ``ValueError`` on attempted traversal.
    """
    # Normalise to forward slashes for consistent checking
    cleaned = untrusted_name.replace("\\", "/")

    # Reject absolute paths (Unix or Windows drive letters)
    if os.path.isabs(cleaned) or (len(cleaned) >= 2 and cleaned[1] == ":"):
        raise ValueError(
            f"Absolute paths are not allowed in pipeline configs: "
            f"{untrusted_name!r}")

    # Reject any '..' component
    parts = cleaned.split("/")
    if ".." in parts:
        raise ValueError(
            f"Path traversal ('..') is not allowed in pipeline configs: "
            f"{untrusted_name!r}")

    result = os.path.join(base_dir, untrusted_name)

    # Belt-and-suspenders: resolved path must still be under base_dir
    resolved = os.path.realpath(result)
    base_resolved = os.path.realpath(base_dir)
    if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
        raise ValueError(
            f"Resolved path escapes output directory: "
            f"{untrusted_name!r}")

    return result


# ── Safe imports (graceful fallback when modules missing) ────────────

_AVAILABLE_MODULES: Dict[str, bool] = {}


def _try_import(name: str):
    """Attempt import and record availability."""
    try:
        mod = __import__(name)
        _AVAILABLE_MODULES[name] = True
        return mod
    except ImportError:
        _AVAILABLE_MODULES[name] = False
        return None


vormap = _try_import("vormap")
vormap_viz = _try_import("vormap_viz")
vormap_hotspot = _try_import("vormap_hotspot")
vormap_trend = _try_import("vormap_trend")
vormap_network = _try_import("vormap_network")
vormap_landscape = _try_import("vormap_landscape")
vormap_coverage = _try_import("vormap_coverage")
vormap_cluster = _try_import("vormap_cluster")
vormap_transect = _try_import("vormap_transect")
vormap_report = _try_import("vormap_report")


# ── Step types ───────────────────────────────────────────────────────

STEP_TYPES = [
    "hotspot", "trend", "network", "landscape", "coverage",
    "cluster", "transect", "hotspot_svg", "trend_svg", "network_svg",
    "report", "export",
]


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class StepResult:
    """Result of a single pipeline step."""
    step_index: int
    step_type: str
    output_key: Optional[str]
    status: str              # "ok", "skipped", "error"
    duration_ms: float
    message: str
    data: Any = None         # step-specific result object

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "step_type": self.step_type,
            "output_key": self.output_key,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "message": self.message,
        }


@dataclass
class PipelineResult:
    """Result of a complete pipeline run."""
    name: str
    data_file: str
    total_steps: int
    completed: int
    skipped: int
    failed: int
    total_duration_ms: float
    steps: List[StepResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "data_file": self.data_file,
            "total_steps": self.total_steps,
            "completed": self.completed,
            "skipped": self.skipped,
            "failed": self.failed,
            "success": self.success,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "steps": [s.to_dict() for s in self.steps],
        }

    def summary_text(self) -> str:
        """Human-readable pipeline summary."""
        lines = []
        lines.append("╔══════════════════════════════════════════════════════╗")
        lines.append("║            Pipeline Execution Summary               ║")
        lines.append("╠══════════════════════════════════════════════════════╣")
        lines.append(f"║  Name:      {self.name[:41]:<41}║")
        lines.append(f"║  Data:      {self.data_file[:41]:<41}║")
        lines.append(f"║  Steps:     {self.total_steps} total, "
                     f"{self.completed} ok, {self.skipped} skipped, "
                     f"{self.failed} failed")
        lines.append(f"║  Duration:  {self.total_duration_ms:.0f}ms"
                     f"{'':<41}║")
        lines.append(f"║  Status:    {'✓ SUCCESS' if self.success else '✗ FAILED'}"
                     f"{'':<37}║")
        lines.append("╠══════════════════════════════════════════════════════╣")

        for sr in self.steps:
            icon = {"ok": "✓", "skipped": "⊘", "error": "✗"}.get(sr.status, "?")
            line = f"║  {icon} [{sr.step_index}] {sr.step_type:<16} {sr.duration_ms:>7.0f}ms"
            if sr.output_key:
                line += f"  → {sr.output_key}"
            lines.append(line)
            if sr.status == "error":
                lines.append(f"║      Error: {sr.message[:42]}")

        lines.append("╚══════════════════════════════════════════════════════╝")
        return "\n".join(lines)


# ── Pipeline Validator ───────────────────────────────────────────────

@dataclass
class ValidationIssue:
    """A problem found during pipeline validation."""
    level: str     # "error" or "warning"
    step_index: Optional[int]
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_pipeline(config: Dict[str, Any]) -> List[ValidationIssue]:
    """Validate a pipeline configuration.

    Returns a list of issues (empty = valid).
    """
    issues: List[ValidationIssue] = []

    # Required fields
    if "data_file" not in config:
        issues.append(ValidationIssue("error", None,
                                      "Missing required field: data_file"))
    if "num_points" not in config:
        issues.append(ValidationIssue("error", None,
                                      "Missing required field: num_points"))
    if "steps" not in config:
        issues.append(ValidationIssue("error", None,
                                      "Missing required field: steps"))
        return issues

    steps = config.get("steps", [])
    if not isinstance(steps, list):
        issues.append(ValidationIssue("error", None,
                                      "steps must be a list"))
        return issues

    if len(steps) == 0:
        issues.append(ValidationIssue("warning", None,
                                      "Pipeline has no steps"))

    output_keys: set = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            issues.append(ValidationIssue("error", i,
                                          f"Step {i} must be a dict"))
            continue

        stype = step.get("type")
        if not stype:
            issues.append(ValidationIssue("error", i,
                                          f"Step {i} missing 'type' field"))
            continue

        if stype not in STEP_TYPES:
            issues.append(ValidationIssue("error", i,
                                          f"Step {i} unknown type: {stype}"))

        # Check for module availability
        module_map = {
            "hotspot": "vormap_hotspot",
            "hotspot_svg": "vormap_hotspot",
            "trend": "vormap_trend",
            "trend_svg": "vormap_trend",
            "network": "vormap_network",
            "network_svg": "vormap_network",
            "landscape": "vormap_landscape",
            "coverage": "vormap_coverage",
            "cluster": "vormap_cluster",
            "transect": "vormap_transect",
            "report": "vormap_report",
        }
        req_mod = module_map.get(stype)
        if req_mod and not _AVAILABLE_MODULES.get(req_mod, False):
            issues.append(ValidationIssue("warning", i,
                                          f"Step {i} ({stype}) requires "
                                          f"{req_mod} which is not available"))

        # Duplicate output keys
        okey = step.get("output_key")
        if okey:
            if okey in output_keys:
                issues.append(ValidationIssue("error", i,
                                              f"Step {i} duplicate output_key: "
                                              f"{okey}"))
            output_keys.add(okey)

        # Export step needs file
        if stype == "export" and "file" not in step:
            issues.append(ValidationIssue("warning", i,
                                          f"Step {i} (export) has no 'file' — "
                                          f"results will print to stdout"))

        # Reject path traversal in step file names
        step_file = step.get("file")
        if step_file:
            cleaned = step_file.replace("\\", "/")
            if os.path.isabs(cleaned) or ".." in cleaned.split("/"):
                issues.append(ValidationIssue("error", i,
                                              f"Step {i} 'file' contains "
                                              f"path traversal: {step_file}"))

    return issues


# ── Pipeline Runner ──────────────────────────────────────────────────

class Pipeline:
    """Configurable multi-step analysis pipeline.

    >>> p = Pipeline.from_file("pipeline.json")
    >>> issues = p.validate()
    >>> if not any(i.level == "error" for i in issues):
    ...     result = p.run()
    ...     print(result.summary_text())
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get("name", "Unnamed Pipeline")
        self.data_file = config.get("data_file", "")
        self.num_points = config.get("num_points", 5)
        self.steps = config.get("steps", [])
        self.output_dir = config.get("output_dir", ".")
        self._results: Dict[str, Any] = {}  # output_key → result
        self._step_results: List[StepResult] = []

    @classmethod
    def from_file(cls, path: str) -> "Pipeline":
        """Load pipeline from a JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return cls(config)

    @classmethod
    def from_json(cls, json_str: str) -> "Pipeline":
        """Load pipeline from a JSON string."""
        return cls(json.loads(json_str))

    def validate(self) -> List[ValidationIssue]:
        """Validate the pipeline configuration."""
        return validate_pipeline(self.config)

    def run(
        self,
        dry_run: bool = False,
        only: Optional[Sequence[str]] = None,
        skip: Optional[Sequence[str]] = None,
    ) -> PipelineResult:
        """Execute the pipeline.

        Args:
            dry_run: If True, validate and report but don't execute.
            only: If set, only run steps of these types.
            skip: If set, skip steps of these types.

        Returns:
            PipelineResult with per-step results.
        """
        start = time.monotonic()
        self._results.clear()
        self._step_results.clear()
        completed = 0
        skipped = 0
        failed = 0

        # Load data
        data = None
        regions = None
        stats = None

        if not dry_run:
            try:
                if vormap and os.path.exists(self.data_file):
                    data = vormap.load_data(self.data_file)
                    if vormap_viz:
                        regions = vormap_viz.compute_regions(data)
                        stats = vormap_viz.compute_region_stats(regions, data)
            except Exception as exc:
                # Data load failure — still run what we can
                pass

        for i, step in enumerate(self.steps):
            stype = step.get("type", "unknown")
            okey = step.get("output_key")

            # Filter by only/skip
            if only and stype not in only:
                self._step_results.append(StepResult(
                    i, stype, okey, "skipped", 0, "Not in --only filter"))
                skipped += 1
                continue
            if skip and stype in skip:
                self._step_results.append(StepResult(
                    i, stype, okey, "skipped", 0, "In --skip filter"))
                skipped += 1
                continue

            if dry_run:
                self._step_results.append(StepResult(
                    i, stype, okey, "skipped", 0,
                    "Dry run — would execute"))
                skipped += 1
                continue

            # Execute step
            step_start = time.monotonic()
            try:
                result_data = self._execute_step(
                    i, step, data, regions, stats)
                elapsed = (time.monotonic() - step_start) * 1000
                sr = StepResult(i, stype, okey, "ok", elapsed,
                                "Success", result_data)
                if okey:
                    self._results[okey] = result_data
                completed += 1
            except Exception as exc:
                elapsed = (time.monotonic() - step_start) * 1000
                sr = StepResult(i, stype, okey, "error", elapsed,
                                str(exc)[:200])
                failed += 1

            self._step_results.append(sr)

        total_ms = (time.monotonic() - start) * 1000

        return PipelineResult(
            name=self.name,
            data_file=self.data_file,
            total_steps=len(self.steps),
            completed=completed,
            skipped=skipped,
            failed=failed,
            total_duration_ms=total_ms,
            steps=list(self._step_results),
        )

    def _execute_step(
        self,
        index: int,
        step: Dict[str, Any],
        data: Any,
        regions: Any,
        stats: Any,
    ) -> Any:
        """Execute a single pipeline step."""
        stype = step["type"]
        handler = _STEP_REGISTRY.get(stype)
        if handler is None:
            raise ValueError(f"Unknown step type: {stype}")
        return handler(self, step, data=data, regions=regions, stats=stats)

    # ── Step handler helpers ─────────────────────────────────────────

    def _require_module(self, module: Any, name: str) -> None:
        """Raise ImportError if a required module is not available."""
        if not module:
            raise ImportError(f"{name} not available")

    def _require_stats(self, stats: Any) -> None:
        """Raise ValueError if region stats are missing."""
        if stats is None:
            raise ValueError("No region stats available")

    def _resolve_from_key(self, step: Dict[str, Any],
                          fallback_fn) -> Any:
        """Look up a previous step result by ``from_key``, or compute."""
        from_key = step.get("from_key")
        result = self._results.get(from_key) if from_key else None
        if result is None:
            result = fallback_fn()
        return result

    def _safe_output_path(self, step: Dict[str, Any],
                          default: str) -> str:
        """Resolve a step's output file path safely."""
        return _safe_join(self.output_dir, step.get("file", default))

    def _run_report(self, step: Dict[str, Any], **_kw) -> str:
        out = self._safe_output_path(step, "pipeline_report.html")
        # Collect step summaries for the report
        content = self._generate_html_report()
        with open(out, "w", encoding="utf-8") as f:
            f.write(content)
        return out

    def _run_export(self, step: Dict[str, Any], **_kw) -> str:
        """Export all collected results to JSON."""
        out = step.get("file")
        export_data = {
            "pipeline": self.name,
            "data_file": self.data_file,
            "results": {},
        }
        for key, val in self._results.items():
            try:
                # Try to serialize
                if hasattr(val, "to_dict"):
                    export_data["results"][key] = val.to_dict()
                elif hasattr(val, "__dict__"):
                    export_data["results"][key] = str(val)
                else:
                    export_data["results"][key] = val
            except Exception:
                export_data["results"][key] = f"<{type(val).__name__}>"

        json_str = json.dumps(export_data, indent=2, default=str)
        if out:
            out = _safe_join(self.output_dir, out)
            with open(out, "w", encoding="utf-8") as f:
                f.write(json_str)
            return out
        return json_str

    def _generate_html_report(self) -> str:
        """Generate a self-contained HTML report of pipeline results.

        All dynamic values are HTML-escaped to prevent XSS when report
        content is derived from untrusted input (e.g. user-supplied
        pipeline names, step types, or error messages).
        """
        _esc = _html.escape
        rows = ""
        for sr in self._step_results:
            color = {"ok": "#2ecc71", "error": "#e74c3c",
                     "skipped": "#95a5a6"}.get(sr.status, "#bdc3c7")
            icon = {"ok": "✓", "error": "✗", "skipped": "⊘"}.get(
                sr.status, "?")
            rows += (f"<tr>"
                     f"<td>{sr.step_index}</td>"
                     f"<td>{_esc(sr.step_type)}</td>"
                     f"<td style='color:{color}'>{icon} {_esc(sr.status)}</td>"
                     f"<td>{sr.duration_ms:.0f}ms</td>"
                     f"<td>{_esc(sr.output_key or '—')}</td>"
                     f"<td>{_esc(sr.message[:80])}</td>"
                     f"</tr>\n")

        safe_name = _esc(self.name)
        safe_data = _esc(self.data_file)
        return f"""<!DOCTYPE html>
<html>
<head>
<title>{safe_name} — Pipeline Report</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px;
       margin: 2rem auto; padding: 0 1rem; background: #fafafa; }}
h1 {{ color: #2c3e50; }}
table {{ width: 100%; border-collapse: collapse; margin: 1rem 0; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
th {{ background: #34495e; color: white; }}
tr:hover {{ background: #ecf0f1; }}
.meta {{ color: #7f8c8d; margin: 0.5rem 0; }}
.ok {{ color: #2ecc71; }} .error {{ color: #e74c3c; }}
</style>
</head>
<body>
<h1>📊 {safe_name}</h1>
<p class="meta">Data: {safe_data} | Steps: {len(self.steps)}</p>
<table>
<tr><th>#</th><th>Type</th><th>Status</th><th>Duration</th>
<th>Output Key</th><th>Message</th></tr>
{rows}
</table>
<p class="meta">Generated by vormap_pipeline</p>
</body>
</html>"""

    @property
    def results(self) -> Dict[str, Any]:
        """Access step results by output_key."""
        return dict(self._results)


# ── Step Registry ────────────────────────────────────────────────────
#
# Each entry maps a step type name to a handler function with signature:
#     handler(pipeline: Pipeline, step: dict, *, data, regions, stats) -> Any
#
# Analysis steps follow a uniform pattern: require module → require stats
# → extract params → call function.  SVG export steps additionally resolve
# a ``from_key`` reference (or compute on the fly) and write to a safe path.


def _step_hotspot(pipe, step, *, stats, **_kw):
    pipe._require_module(vormap_hotspot, "vormap_hotspot")
    pipe._require_stats(stats)
    return vormap_hotspot.detect_hotspots(
        stats,
        attribute=step.get("attribute", "area"),
        weights=step.get("weights", "queen"),
        k=step.get("k", 4),
    )


def _step_trend(pipe, step, *, stats, **_kw):
    pipe._require_module(vormap_trend, "vormap_trend")
    pipe._require_stats(stats)
    return vormap_trend.fit_trend_surface(
        stats,
        attribute=step.get("attribute", "area"),
        order=step.get("order", 2),
    )


def _step_network(pipe, step, *, stats, **_kw):
    pipe._require_module(vormap_network, "vormap_network")
    pipe._require_stats(stats)
    return vormap_network.build_network(stats)


def _step_landscape(pipe, step, *, stats, **_kw):
    pipe._require_module(vormap_landscape, "vormap_landscape")
    pipe._require_stats(stats)
    return vormap_landscape.analyze_landscape(
        stats,
        attribute=step.get("attribute", "area"),
        num_classes=step.get("num_classes", 5),
    )


def _step_coverage(pipe, step, *, stats, **_kw):
    pipe._require_module(vormap_coverage, "vormap_coverage")
    pipe._require_stats(stats)
    return vormap_coverage.analyze_coverage(stats)


def _step_cluster(pipe, step, *, stats, **_kw):
    pipe._require_module(vormap_cluster, "vormap_cluster")
    pipe._require_stats(stats)
    return vormap_cluster.cluster_regions(
        stats,
        k=step.get("k", 3),
        attributes=step.get("attributes", ["area"]),
    )


def _step_transect(pipe, step, *, stats, data, **_kw):
    pipe._require_module(vormap_transect, "vormap_transect")
    pipe._require_stats(stats)
    return vormap_transect.profile_transect(
        stats, data,
        start=step.get("start", [0, 0]),
        end=step.get("end", [1, 1]),
        samples=step.get("samples", 20),
    )


def _step_hotspot_svg(pipe, step, *, regions, data, stats, **_kw):
    pipe._require_module(vormap_hotspot, "vormap_hotspot")
    result = pipe._resolve_from_key(
        step,
        lambda: vormap_hotspot.detect_hotspots(
            stats, attribute=step.get("attribute", "area")),
    )
    out = pipe._safe_output_path(step, "hotspot.svg")
    vormap_hotspot.export_hotspot_svg(result, regions, data, out)
    return out


def _step_trend_svg(pipe, step, *, regions, data, stats, **_kw):
    pipe._require_module(vormap_trend, "vormap_trend")
    result = pipe._resolve_from_key(
        step,
        lambda: vormap_trend.fit_trend_surface(
            stats,
            attribute=step.get("attribute", "area"),
            order=step.get("order", 2)),
    )
    out = pipe._safe_output_path(step, "trend.svg")
    vormap_trend.export_trend_svg(result, regions, data, out)
    return out


def _step_network_svg(pipe, step, *, regions, data, stats, **_kw):
    pipe._require_module(vormap_network, "vormap_network")
    result = pipe._resolve_from_key(
        step,
        lambda: vormap_network.build_network(stats),
    )
    out = pipe._safe_output_path(step, "network.svg")
    vormap_network.export_network_svg(result, regions, data, out)
    return out


def _step_report(pipe, step, **_kw):
    return pipe._run_report(step, **_kw)


def _step_export(pipe, step, **_kw):
    return pipe._run_export(step, **_kw)


_STEP_REGISTRY: Dict[str, Any] = {
    "hotspot":     _step_hotspot,
    "trend":       _step_trend,
    "network":     _step_network,
    "landscape":   _step_landscape,
    "coverage":    _step_coverage,
    "cluster":     _step_cluster,
    "transect":    _step_transect,
    "hotspot_svg": _step_hotspot_svg,
    "trend_svg":   _step_trend_svg,
    "network_svg": _step_network_svg,
    "report":      _step_report,
    "export":      _step_export,
}


# ── Example Pipeline ─────────────────────────────────────────────────

EXAMPLE_PIPELINE = {
    "name": "Spatial Analysis Pipeline",
    "data_file": "datauni5.txt",
    "num_points": 5,
    "steps": [
        {
            "type": "hotspot",
            "attribute": "area",
            "weights": "queen",
            "output_key": "hotspots",
        },
        {
            "type": "trend",
            "attribute": "area",
            "order": 2,
            "output_key": "trend",
        },
        {
            "type": "network",
            "output_key": "network",
        },
        {
            "type": "landscape",
            "attribute": "area",
            "num_classes": 5,
            "output_key": "landscape",
        },
        {
            "type": "cluster",
            "k": 3,
            "attributes": ["area", "compactness"],
            "output_key": "clusters",
        },
        {
            "type": "export",
            "file": "results.json",
        },
    ],
}


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="vormap_pipeline",
        description="Batch analysis pipeline for VoronoiMap",
    )
    parser.add_argument("config", nargs="?",
                        help="Pipeline config JSON file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and report without executing")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated step types to run")
    parser.add_argument("--skip", type=str, default=None,
                        help="Comma-separated step types to skip")
    parser.add_argument("--output-dir", type=str, default=".",
                        help="Output directory for exports")
    parser.add_argument("--example", action="store_true",
                        help="Print example pipeline JSON and exit")
    parser.add_argument("--validate", action="store_true",
                        help="Validate config and exit")

    args = parser.parse_args(argv)

    if args.example:
        print(json.dumps(EXAMPLE_PIPELINE, indent=2))
        return 0

    if not args.config:
        parser.print_help()
        return 1

    pipe = Pipeline.from_file(args.config)

    if args.output_dir != ".":
        pipe.output_dir = args.output_dir
        os.makedirs(args.output_dir, exist_ok=True)

    # Validate
    issues = pipe.validate()
    if issues:
        for iss in issues:
            prefix = "ERROR" if iss.level == "error" else "WARN"
            step_info = f" [step {iss.step_index}]" if iss.step_index is not None else ""
            print(f"  {prefix}{step_info}: {iss.message}")
        if any(i.level == "error" for i in issues):
            print("\nPipeline has errors — fix them before running.")
            return 1

    if args.validate:
        print("Pipeline is valid.")
        return 0

    # Run
    only = args.only.split(",") if args.only else None
    skip = args.skip.split(",") if args.skip else None

    result = pipe.run(dry_run=args.dry_run, only=only, skip=skip)
    print(result.summary_text())
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
