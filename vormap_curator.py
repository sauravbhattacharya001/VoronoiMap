#!/usr/bin/env python3
"""vormap_curator - Agentic Spatial Dataset Curator.

Triages a point dataset *before* you feed it into a Voronoi pipeline and
emits a ranked, reason-tagged action plan that another tool (or a human)
can apply.  This is the "data-quality side" companion to
``vormap_quality`` / ``vormap_doctor``: instead of just *reporting*
problems, the curator produces a concrete per-point verdict and a
reproducible cleanup plan.

Six verdicts are emitted per point:

* **KEEP**     - point looks fine, no action needed.
* **REVIEW**   - mild signal worth a human glance (e.g. close to boundary,
  unusual precision, isolated cell).
* **MERGE**    - point is part of a near-duplicate cluster; one
  representative is auto-selected, the rest are tagged ``MERGE`` with the
  representative's index.
* **DROP_DUPLICATE** - exact (lat/lng) duplicate of another point.
* **DROP_INVALID**   - NaN / Inf / non-finite coordinate.
* **DROP_OUT_OF_BOUNDS** - falls outside an explicit user bounding box.

Each verdict carries:

* a 0-100 ``priority`` (higher = act sooner),
* a list of structured ``reasons`` (codes + human messages),
* optional ``merge_into`` index when the verdict is ``MERGE``.

Aggregate output includes:

* dataset *health grade* A-F,
* per-verdict counts,
* an ordered action playbook (P0 -> P3),
* a ``simulate()`` step that computes before/after spacing metrics
  *without* mutating the source dataset,
* ``apply()`` that materialises the cleaned point list.

Renderers: ``text`` / ``markdown`` / ``json``.

CLI::

    python vormap_curator.py points.csv
    python vormap_curator.py points.csv --format md
    python vormap_curator.py points.csv --bounds 0,1000,0,2000
    python vormap_curator.py points.csv --apply cleaned.csv

The module deliberately has **no required third-party dependencies** so
it runs in the smallest VoronoiMap installs.  ``numpy`` is used opportun-
istically when present, purely for speed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Verdict",
    "PointVerdict",
    "CurationReport",
    "curate",
    "apply_plan",
    "format_text",
    "format_markdown",
    "format_json",
]

# ---------------------------------------------------------------------------
# Verdict codes
# ---------------------------------------------------------------------------

class Verdict:
    KEEP = "KEEP"
    REVIEW = "REVIEW"
    MERGE = "MERGE"
    DROP_DUPLICATE = "DROP_DUPLICATE"
    DROP_INVALID = "DROP_INVALID"
    DROP_OUT_OF_BOUNDS = "DROP_OUT_OF_BOUNDS"

    ALL = (
        KEEP,
        REVIEW,
        MERGE,
        DROP_DUPLICATE,
        DROP_INVALID,
        DROP_OUT_OF_BOUNDS,
    )


# Priority weights (higher = act sooner).  These are intentionally small,
# round numbers so the JSON output is easy for a downstream UI to reason
# about.
_PRIORITY = {
    Verdict.DROP_INVALID:       95,
    Verdict.DROP_OUT_OF_BOUNDS: 85,
    Verdict.DROP_DUPLICATE:     75,
    Verdict.MERGE:              60,
    Verdict.REVIEW:             30,
    Verdict.KEEP:                0,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PointVerdict:
    index: int
    x: float
    y: float
    verdict: str
    priority: int
    reasons: List[str] = field(default_factory=list)
    merge_into: Optional[int] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class PlaybookStep:
    tier: str          # "P0".."P3"
    action: str        # e.g. "drop_invalid", "review_boundary"
    count: int
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CurationReport:
    n_input: int
    n_kept: int
    verdicts: List[PointVerdict]
    counts: dict
    grade: str          # "A".."F"
    score: float        # 0-100
    playbook: List[PlaybookStep]
    bounds: Optional[Tuple[float, float, float, float]] = None
    before_stats: Optional[dict] = None
    after_stats: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "n_input": self.n_input,
            "n_kept": self.n_kept,
            "grade": self.grade,
            "score": self.score,
            "counts": self.counts,
            "bounds": list(self.bounds) if self.bounds else None,
            "before_stats": self.before_stats,
            "after_stats": self.after_stats,
            "playbook": [s.to_dict() for s in self.playbook],
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


# ---------------------------------------------------------------------------
# Geometry helpers (no scipy required)
# ---------------------------------------------------------------------------

def _is_finite(v) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _nearest_neighbour_distances(points: Sequence[Tuple[float, float]]) -> List[float]:
    """O(n^2) NN distances - fine for typical curation use (< ~5k points).

    Returns ``[]`` when fewer than two points.
    """
    n = len(points)
    if n < 2:
        return []
    dists = [math.inf] * n
    for i in range(n):
        xi, yi = points[i]
        for j in range(i + 1, n):
            xj, yj = points[j]
            d = math.hypot(xi - xj, yi - yj)
            if d < dists[i]:
                dists[i] = d
            if d < dists[j]:
                dists[j] = d
    # collapse any leftover inf (shouldn't happen for n >= 2)
    return [d if math.isfinite(d) else 0.0 for d in dists]


def _mean_std(values: Sequence[float]) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.0
    m = sum(values) / len(values)
    if len(values) < 2:
        return m, 0.0
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return m, math.sqrt(var)


def _spacing_stats(points: Sequence[Tuple[float, float]]) -> dict:
    if len(points) < 2:
        return {
            "count": len(points),
            "mean_nn": 0.0,
            "std_nn": 0.0,
            "min_nn": 0.0,
            "max_nn": 0.0,
        }
    nn = _nearest_neighbour_distances(points)
    mean, std = _mean_std(nn)
    return {
        "count": len(points),
        "mean_nn": mean,
        "std_nn": std,
        "min_nn": min(nn),
        "max_nn": max(nn),
    }


# ---------------------------------------------------------------------------
# Curation engine
# ---------------------------------------------------------------------------

def _detect_precision(v: float) -> int:
    """Return number of fractional digits, capped at 12."""
    if not math.isfinite(v):
        return 0
    s = f"{v:.12f}".rstrip("0")
    if "." not in s:
        return 0
    return len(s.split(".", 1)[1])


def curate(
    points: Iterable[Tuple[float, float]],
    *,
    bounds: Optional[Tuple[float, float, float, float]] = None,
    near_duplicate_tol: float = 1e-6,
    boundary_pad_frac: float = 0.02,
    isolation_k: float = 4.0,
    low_precision_max_digits: int = -1,
    compute_after_stats: bool = True,
) -> CurationReport:
    """Triage ``points`` and produce a :class:`CurationReport`.

    Parameters
    ----------
    points:
        Iterable of ``(x, y)`` tuples.
    bounds:
        Optional ``(south, north, west, east)`` bounding box.  Points
        outside the box are marked ``DROP_OUT_OF_BOUNDS``.  When ``None``
        no bounding-box check is performed.
    near_duplicate_tol:
        Euclidean distance below which two points are treated as a
        near-duplicate cluster (excluding exact duplicates which are
        always tagged ``DROP_DUPLICATE``).
    boundary_pad_frac:
        Fraction of the *observed* bounding box considered "boundary
        zone".  Points inside the zone get a ``REVIEW`` reason
        ``near_boundary`` because their Voronoi cells will be unbounded
        or distorted.  Only applied when ``bounds`` is provided.
    isolation_k:
        Multiplier for the isolation-outlier check.  A point whose NN
        distance is greater than ``isolation_k`` x median(NN) gets a
        ``REVIEW`` reason ``isolated``.
    low_precision_max_digits:
        Coordinates rounded to <= this many fractional digits get a
        ``REVIEW`` reason ``low_precision`` (often indicates a stale
        geocoder).  Set to a negative number to disable.
    compute_after_stats:
        When True, the cleaned point set is also profiled so the report
        carries before/after spacing stats.
    """

    pts_list = list(points)
    n_input = len(pts_list)

    # 1. Initial scan: invalid + bounds
    verdicts: List[PointVerdict] = []
    valid_coords: List[Tuple[int, float, float]] = []  # (orig_idx, x, y)

    south = north = west = east = None
    if bounds is not None:
        south, north, west, east = bounds

    for i, p in enumerate(pts_list):
        if not isinstance(p, (tuple, list)) or len(p) < 2:
            verdicts.append(PointVerdict(
                index=i, x=float("nan"), y=float("nan"),
                verdict=Verdict.DROP_INVALID,
                priority=_PRIORITY[Verdict.DROP_INVALID],
                reasons=["malformed_row"],
            ))
            continue
        x_raw, y_raw = p[0], p[1]
        if not (_is_finite(x_raw) and _is_finite(y_raw)):
            verdicts.append(PointVerdict(
                index=i,
                x=float(x_raw) if _is_finite(x_raw) else float("nan"),
                y=float(y_raw) if _is_finite(y_raw) else float("nan"),
                verdict=Verdict.DROP_INVALID,
                priority=_PRIORITY[Verdict.DROP_INVALID],
                reasons=["non_finite_coord"],
            ))
            continue
        x = float(x_raw); y = float(y_raw)
        if bounds is not None and not (west <= x <= east and south <= y <= north):
            verdicts.append(PointVerdict(
                index=i, x=x, y=y,
                verdict=Verdict.DROP_OUT_OF_BOUNDS,
                priority=_PRIORITY[Verdict.DROP_OUT_OF_BOUNDS],
                reasons=[f"outside_bounds(s={south},n={north},w={west},e={east})"],
            ))
            continue
        valid_coords.append((i, x, y))

    # 2. Exact duplicates
    seen: dict = {}
    surviving: List[Tuple[int, float, float]] = []
    for idx, x, y in valid_coords:
        key = (x, y)
        if key in seen:
            verdicts.append(PointVerdict(
                index=idx, x=x, y=y,
                verdict=Verdict.DROP_DUPLICATE,
                priority=_PRIORITY[Verdict.DROP_DUPLICATE],
                reasons=[f"exact_duplicate_of_index_{seen[key]}"],
                merge_into=seen[key],
            ))
        else:
            seen[key] = idx
            surviving.append((idx, x, y))

    # 3. Near-duplicate clustering (single-link, threshold = near_duplicate_tol)
    #    Pick the lowest-index point in each cluster as the representative.
    n_surv = len(surviving)
    parent = list(range(n_surv))

    def _find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            # keep the smaller original index as the root
            if surviving[ra][0] <= surviving[rb][0]:
                parent[rb] = ra
            else:
                parent[ra] = rb

    if near_duplicate_tol > 0 and n_surv >= 2:
        for i in range(n_surv):
            xi, yi = surviving[i][1], surviving[i][2]
            for j in range(i + 1, n_surv):
                xj, yj = surviving[j][1], surviving[j][2]
                if abs(xi - xj) > near_duplicate_tol or abs(yi - yj) > near_duplicate_tol:
                    continue
                if math.hypot(xi - xj, yi - yj) <= near_duplicate_tol:
                    _union(i, j)

    clusters: dict = {}
    for i in range(n_surv):
        clusters.setdefault(_find(i), []).append(i)

    cluster_rep: dict = {}  # surv-index -> orig-index of representative
    merge_targets: dict = {}  # surv-index -> orig-index it merges into
    for root, members in clusters.items():
        rep_orig = surviving[root][0]
        cluster_rep[root] = rep_orig
        if len(members) > 1:
            for m in members:
                if m != root:
                    merge_targets[m] = rep_orig

    # 4. Compute NN distances over survivors for isolation check
    nn_dists = _nearest_neighbour_distances(
        [(x, y) for _, x, y in surviving]
    )
    median_nn = 0.0
    if nn_dists:
        sorted_nn = sorted(nn_dists)
        mid = len(sorted_nn) // 2
        if len(sorted_nn) % 2 == 1:
            median_nn = sorted_nn[mid]
        else:
            median_nn = 0.5 * (sorted_nn[mid - 1] + sorted_nn[mid])

    isolation_threshold = isolation_k * median_nn if median_nn > 0 else math.inf

    # 5. Boundary zone (only when caller supplied bounds)
    boundary_zone = None
    if bounds is not None and boundary_pad_frac > 0 and n_surv > 0:
        w = east - west
        h = north - south
        pad_w = w * boundary_pad_frac
        pad_h = h * boundary_pad_frac
        boundary_zone = (south + pad_h, north - pad_h, west + pad_w, east - pad_w)

    # 6. Per-point verdicts for survivors
    for s_idx, (orig_idx, x, y) in enumerate(surviving):
        reasons: List[str] = []
        verdict = Verdict.KEEP

        if s_idx in merge_targets:
            verdict = Verdict.MERGE
            reasons.append(
                f"near_duplicate_of_index_{merge_targets[s_idx]}"
                f"(tol={near_duplicate_tol})"
            )

        # boundary
        if boundary_zone is not None:
            bs, bn, bw, be = boundary_zone
            if not (bw <= x <= be and bs <= y <= bn):
                reasons.append("near_boundary")
                if verdict == Verdict.KEEP:
                    verdict = Verdict.REVIEW

        # isolation
        if nn_dists and nn_dists[s_idx] > isolation_threshold:
            reasons.append(
                f"isolated(nn={nn_dists[s_idx]:.4g} > {isolation_k}x median={median_nn:.4g})"
            )
            if verdict == Verdict.KEEP:
                verdict = Verdict.REVIEW

        # low precision
        if low_precision_max_digits >= 0:
            digits = max(_detect_precision(x), _detect_precision(y))
            if digits <= low_precision_max_digits:
                reasons.append(f"low_precision({digits} digits)")
                if verdict == Verdict.KEEP:
                    verdict = Verdict.REVIEW

        merge_into = merge_targets.get(s_idx) if verdict == Verdict.MERGE else None
        verdicts.append(PointVerdict(
            index=orig_idx, x=x, y=y,
            verdict=verdict,
            priority=_PRIORITY[verdict],
            reasons=reasons,
            merge_into=merge_into,
        ))

    # 7. Aggregate
    verdicts.sort(key=lambda v: (-v.priority, v.index))
    counts = {k: 0 for k in Verdict.ALL}
    for v in verdicts:
        counts[v.verdict] += 1

    n_kept = counts[Verdict.KEEP] + counts[Verdict.REVIEW]

    # Health score: KEEP = 1.0, REVIEW = 0.7, MERGE = 0.4, any DROP = 0.0
    weight = {
        Verdict.KEEP: 1.0,
        Verdict.REVIEW: 0.7,
        Verdict.MERGE: 0.4,
        Verdict.DROP_DUPLICATE: 0.0,
        Verdict.DROP_INVALID: 0.0,
        Verdict.DROP_OUT_OF_BOUNDS: 0.0,
    }
    if n_input == 0:
        score = 0.0
    else:
        score = 100.0 * sum(weight[v.verdict] for v in verdicts) / n_input
    if score >= 90:   grade = "A"
    elif score >= 80: grade = "B"
    elif score >= 70: grade = "C"
    elif score >= 60: grade = "D"
    else:             grade = "F"

    # 8. Playbook
    playbook: List[PlaybookStep] = []
    if counts[Verdict.DROP_INVALID]:
        playbook.append(PlaybookStep(
            "P0", "drop_invalid", counts[Verdict.DROP_INVALID],
            "Remove rows with NaN/Inf or malformed coordinates before any Voronoi run.",
        ))
    if counts[Verdict.DROP_OUT_OF_BOUNDS]:
        playbook.append(PlaybookStep(
            "P0", "drop_out_of_bounds", counts[Verdict.DROP_OUT_OF_BOUNDS],
            "Remove points outside the declared study bounding box (or widen --bounds).",
        ))
    if counts[Verdict.DROP_DUPLICATE]:
        playbook.append(PlaybookStep(
            "P1", "drop_exact_duplicates", counts[Verdict.DROP_DUPLICATE],
            "Drop exact duplicate rows; they degenerate Voronoi cells to zero area.",
        ))
    if counts[Verdict.MERGE]:
        playbook.append(PlaybookStep(
            "P1", "merge_near_duplicates", counts[Verdict.MERGE],
            f"Collapse near-duplicate clusters (tol={near_duplicate_tol}) into their representative.",
        ))
    if counts[Verdict.REVIEW]:
        playbook.append(PlaybookStep(
            "P2", "human_review", counts[Verdict.REVIEW],
            "Inspect REVIEW points: boundary / isolation / low-precision warnings.",
        ))
    if not playbook:
        playbook.append(PlaybookStep(
            "P3", "all_clear", n_input,
            "Dataset passed all curation checks; proceed to Voronoi analysis.",
        ))

    # 9. Before/after spacing snapshot
    before_stats = _spacing_stats(
        [(x, y) for _, x, y in valid_coords]
    ) if compute_after_stats else None
    cleaned = _materialise_clean(pts_list, verdicts)
    after_stats = _spacing_stats(cleaned) if compute_after_stats else None

    return CurationReport(
        n_input=n_input,
        n_kept=n_kept,
        verdicts=verdicts,
        counts=counts,
        grade=grade,
        score=round(score, 2),
        playbook=playbook,
        bounds=bounds,
        before_stats=before_stats,
        after_stats=after_stats,
    )


def _materialise_clean(
    original: Sequence[Tuple[float, float]],
    verdicts: Sequence[PointVerdict],
) -> List[Tuple[float, float]]:
    keep = {Verdict.KEEP, Verdict.REVIEW}
    out: List[Tuple[float, float]] = []
    for v in verdicts:
        if v.verdict in keep:
            out.append((v.x, v.y))
    return out


def apply_plan(
    original: Sequence[Tuple[float, float]],
    report: CurationReport,
) -> List[Tuple[float, float]]:
    """Return the cleaned point list implied by *report*.

    KEEP + REVIEW survive; everything else is dropped.  ``MERGE`` points
    are dropped because their representative is already in KEEP/REVIEW.
    """
    return _materialise_clean(original, report.verdicts)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_stats(s: Optional[dict]) -> str:
    if not s:
        return "n/a"
    return (
        f"n={s['count']}, mean_nn={s['mean_nn']:.4g}, std_nn={s['std_nn']:.4g}, "
        f"min={s['min_nn']:.4g}, max={s['max_nn']:.4g}"
    )


def format_text(report: CurationReport, *, max_verdicts: int = 20) -> str:
    lines = [
        "VoronoiMap Curator Report",
        "=" * 32,
        f"Input points : {report.n_input}",
        f"Kept (incl. REVIEW): {report.n_kept}",
        f"Grade        : {report.grade}  (score {report.score})",
        "",
        "Counts:",
    ]
    for k in Verdict.ALL:
        lines.append(f"  {k:<22} {report.counts[k]}")
    lines.append("")
    lines.append(f"Before: {_fmt_stats(report.before_stats)}")
    lines.append(f"After : {_fmt_stats(report.after_stats)}")
    lines.append("")
    lines.append("Playbook:")
    for step in report.playbook:
        lines.append(f"  [{step.tier}] {step.action} x{step.count} - {step.detail}")
    lines.append("")
    lines.append(f"Top {min(max_verdicts, len(report.verdicts))} verdicts:")
    for v in report.verdicts[:max_verdicts]:
        reason_str = ", ".join(v.reasons) if v.reasons else "-"
        lines.append(
            f"  #{v.index:<5} ({v.x:.4g}, {v.y:.4g}) {v.verdict:<18} "
            f"p={v.priority:<3} {reason_str}"
        )
    return "\n".join(lines)


def format_markdown(report: CurationReport, *, max_verdicts: int = 20) -> str:
    lines = [
        "# VoronoiMap Curator Report",
        "",
        f"- **Input points:** {report.n_input}",
        f"- **Kept (incl. REVIEW):** {report.n_kept}",
        f"- **Grade:** **{report.grade}** (score {report.score})",
        "",
        "## Verdict counts",
        "",
        "| Verdict | Count |",
        "|---|---:|",
    ]
    for k in Verdict.ALL:
        lines.append(f"| {k} | {report.counts[k]} |")
    lines += [
        "",
        "## Spacing (before / after)",
        "",
        f"- **Before:** {_fmt_stats(report.before_stats)}",
        f"- **After:**  {_fmt_stats(report.after_stats)}",
        "",
        "## Playbook",
        "",
        "| Tier | Action | Count | Detail |",
        "|---|---|---:|---|",
    ]
    for step in report.playbook:
        lines.append(f"| {step.tier} | `{step.action}` | {step.count} | {step.detail} |")
    lines += [
        "",
        f"## Top {min(max_verdicts, len(report.verdicts))} verdicts",
        "",
        "| # | x | y | Verdict | Priority | Reasons |",
        "|---:|---:|---:|---|---:|---|",
    ]
    for v in report.verdicts[:max_verdicts]:
        reason_str = "; ".join(v.reasons) if v.reasons else "-"
        lines.append(
            f"| {v.index} | {v.x:.4g} | {v.y:.4g} | {v.verdict} | {v.priority} | {reason_str} |"
        )
    return "\n".join(lines)


def format_json(report: CurationReport, *, indent: int = 2) -> str:
    return json.dumps(report.to_dict(), indent=indent, sort_keys=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_points_from_file(path: str) -> List[Tuple[float, float]]:
    """Tiny loader that accepts CSV-ish ``x,y`` or whitespace-separated."""
    pts: List[Tuple[float, float]] = []
    with open(path, "r", encoding="utf-8") as fh:
        # sniff: if the first non-empty line has a comma, treat as CSV
        sample = fh.read(2048)
        fh.seek(0)
        use_csv = "," in sample
        if use_csv:
            reader = csv.reader(fh)
            for row in reader:
                if not row:
                    continue
                # skip header rows whose first cell isn't numeric
                try:
                    x = float(row[0]); y = float(row[1])
                except (ValueError, IndexError):
                    continue
                pts.append((x, y))
        else:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.replace(",", " ").split()
                if len(parts) < 2:
                    continue
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _parse_bounds(spec: str) -> Tuple[float, float, float, float]:
    parts = spec.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "bounds must be 'south,north,west,east' (4 floats)"
        )
    try:
        return tuple(float(p) for p in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="vormap_curator",
        description="Agentic spatial dataset curator (VoronoiMap).",
    )
    p.add_argument("input", help="Path to a CSV or whitespace-separated point file.")
    p.add_argument("--bounds", type=_parse_bounds, default=None,
                   help="Optional 'south,north,west,east' bounding box for out-of-bounds checks.")
    p.add_argument("--tol", type=float, default=1e-6, dest="near_tol",
                   help="Near-duplicate Euclidean tolerance (default: 1e-6).")
    p.add_argument("--isolation-k", type=float, default=4.0,
                   help="Isolation multiplier vs median NN distance (default: 4.0).")
    p.add_argument("--boundary-pad", type=float, default=0.02,
                   help="Boundary review zone as fraction of bbox (default: 0.02).")
    p.add_argument("--format", choices=("text", "md", "markdown", "json"),
                   default="text", help="Output format.")
    p.add_argument("--max-verdicts", type=int, default=20,
                   help="Max verdict rows in text/markdown output.")
    p.add_argument("--apply", metavar="OUT_CSV", default=None,
                   help="Write the cleaned point list to OUT_CSV (x,y header).")
    args = p.parse_args(argv)

    if not os.path.exists(args.input):
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2

    points = _load_points_from_file(args.input)
    report = curate(
        points,
        bounds=args.bounds,
        near_duplicate_tol=args.near_tol,
        isolation_k=args.isolation_k,
        boundary_pad_frac=args.boundary_pad,
    )

    if args.format == "json":
        print(format_json(report))
    elif args.format in ("md", "markdown"):
        print(format_markdown(report, max_verdicts=args.max_verdicts))
    else:
        print(format_text(report, max_verdicts=args.max_verdicts))

    if args.apply:
        cleaned = apply_plan(points, report)
        with open(args.apply, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["x", "y"])
            writer.writerows(cleaned)
        print(f"[curator] wrote {len(cleaned)} cleaned points -> {args.apply}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
