#!/usr/bin/env python3
"""Spatial Genetics Engine -- autonomous population genetics simulation on Voronoi tessellations.

Models evolutionary dynamics across Voronoi cells where each cell acts as a
local population (deme) with its own gene pool.  Simulates selection,
migration (gene flow), genetic drift, and mutation over generations,
testing Hardy-Weinberg equilibrium and detecting emergent spatial patterns
like clines, isolation by distance, and speciation signals.

Seven analysis engines:

- **Gene Pool Initializer** -- Assigns allele frequencies for multiple loci
  per cell using spatial clines (latitude-like gradients) plus random noise.
- **Selection Pressure Engine** -- Applies fitness differentials based on
  spatial environment zones (tropical / temperate / polar).
- **Migration Engine** -- Models gene flow between adjacent cells with
  migration rate inversely proportional to geographic distance.
- **Genetic Drift Engine** -- Simulates stochastic sampling via binomial
  draws in finite populations scaled by cell area.
- **Mutation Engine** -- Applies forward/reverse mutation at configurable
  rate, tracking mutation-selection balance.
- **Hardy-Weinberg Tester** -- Chi-squared test for HW equilibrium
  deviation per cell per locus.
- **Autonomous Insight Generator** -- Detects clines, isolation by distance,
  fixation events, speciation signals (FST), genetic diversity, and computes
  a composite health score 0-100.

Usage (Python API)::

    from vormap_genetics import GeneticsEngine, genetics_analyze, genetics_demo

    result = genetics_analyze("points.txt")
    print(f"Health: {result.health_score:.1f}/100")

    engine = GeneticsEngine(points=[(0,0),(10,0),(5,8),(3,6),(7,2)])
    result = engine.analyze(generations=100)
    engine.to_html("genetics.html")

    genetics_demo()

CLI::

    python vormap_genetics.py points.txt
    python vormap_genetics.py points.txt --generations 100 --json out.json --html dash.html
    python vormap_genetics.py --demo
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import math
import random
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CellGenetics:
    """Genetic profile for a single spatial cell (deme)."""
    cell_id: int
    x: float
    y: float
    population_size: int = 100
    allele_freqs: Dict[str, float] = field(default_factory=dict)
    expected_het: float = 0.0
    observed_het: float = 0.0
    hw_chi_squared: float = 0.0
    hw_equilibrium: bool = True
    environment: str = "temperate"
    fitness_a: float = 1.0
    fitness_ab: float = 1.0
    fitness_b: float = 1.0
    selection_coefficient: float = 0.0
    drift_variance: float = 0.0


@dataclass
class MigrationFlow:
    """Gene flow between two adjacent demes."""
    source: int
    target: int
    rate: float
    genetic_distance: float


@dataclass
class FixationEvent:
    """Record of an allele reaching fixation."""
    cell_id: int
    locus: str
    fixed_allele: str
    generation: int


@dataclass
class GeneticsResult:
    """Full population genetics analysis result."""
    cells: List[CellGenetics] = field(default_factory=list)
    migrations: List[MigrationFlow] = field(default_factory=list)
    fixations: List[FixationEvent] = field(default_factory=list)
    generations_simulated: int = 50
    mean_fst: float = 0.0
    global_heterozygosity: float = 0.0
    isolation_by_distance_r: float = 0.0
    num_clines: int = 0
    health_score: float = 0.0
    insights: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _euclidean(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _load_points(path: str) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pts.append((float(parts[0]), float(parts[1])))
                except ValueError:
                    continue
    return pts


def _knn_adjacency(
    points: List[Tuple[float, float]], k: int = 6
) -> Dict[int, List[int]]:
    n = len(points)
    k = min(k, n - 1)
    adj: Dict[int, List[int]] = {i: [] for i in range(n)}
    for i in range(n):
        dists = []
        for j in range(n):
            if i != j:
                dists.append((j, _euclidean(points[i], points[j])))
        dists.sort(key=lambda t: t[1])
        for j, _ in dists[:k]:
            if j not in adj[i]:
                adj[i].append(j)
            if i not in adj[j]:
                adj[j].append(i)
    return adj


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOCI = ["color", "size", "speed", "resistance", "metabolism"]
DEFAULT_GENERATIONS = 50
DEFAULT_MUTATION_RATE = 1e-4
DEFAULT_MIGRATION_SCALE = 0.05


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class GeneticsEngine:
    """Autonomous population genetics simulator on Voronoi tessellations."""

    def __init__(
        self,
        points: Optional[List[Tuple[float, float]]] = None,
        path: Optional[str] = None,
        loci: Optional[List[str]] = None,
        mutation_rate: float = DEFAULT_MUTATION_RATE,
        migration_scale: float = DEFAULT_MIGRATION_SCALE,
        seed: Optional[int] = None,
    ):
        if path and not points:
            points = _load_points(path)
        if not points or len(points) == 0:
            raise ValueError("At least one point is required")
        self.points = points
        self.n = len(points)
        self.loci = loci or list(DEFAULT_LOCI)
        self.mutation_rate = mutation_rate
        self.migration_scale = migration_scale
        self.adj = _knn_adjacency(points)
        self._rng = random.Random(seed)
        self._result: Optional[GeneticsResult] = None

        # Compute spatial bounds
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        self._x_range = (min(xs), max(xs))
        self._y_range = (min(ys), max(ys))
        self._y_span = self._y_range[1] - self._y_range[0] if self._y_range[1] > self._y_range[0] else 1.0

    # -- Engine 1: Gene Pool Initializer ------------------------------------

    def _init_gene_pools(self) -> List[CellGenetics]:
        cells: List[CellGenetics] = []
        for i, (x, y) in enumerate(self.points):
            # Environment based on y-position (latitude proxy)
            y_norm = (y - self._y_range[0]) / self._y_span
            if y_norm < 0.33:
                env = "tropical"
            elif y_norm < 0.66:
                env = "temperate"
            else:
                env = "polar"

            # Population size from spatial spread (area proxy via neighbor distances)
            if self.adj[i]:
                avg_dist = sum(_euclidean(self.points[i], self.points[j]) for j in self.adj[i]) / len(self.adj[i])
                pop = max(20, int(50 + avg_dist * 10))
            else:
                pop = 50

            # Allele frequencies: cline along y + noise
            freqs: Dict[str, float] = {}
            for li, locus in enumerate(self.loci):
                base = 0.5 + 0.3 * (y_norm - 0.5) * ((-1) ** li)
                noise = self._rng.gauss(0, 0.05)
                freqs[locus] = max(0.01, min(0.99, base + noise))

            cell = CellGenetics(
                cell_id=i, x=x, y=y,
                population_size=pop,
                allele_freqs=freqs,
                environment=env,
            )
            cells.append(cell)
        return cells

    # -- Engine 2: Selection Pressure ---------------------------------------

    def _apply_selection(self, cells: List[CellGenetics]) -> None:
        for cell in cells:
            for locus in self.loci:
                p = cell.allele_freqs.get(locus, 0.5)
                # Environment-dependent selection
                if cell.environment == "tropical":
                    s = 0.02  # slight advantage for allele A
                elif cell.environment == "polar":
                    s = -0.02  # slight advantage for allele B
                else:
                    s = 0.0

                cell.selection_coefficient = s

                # Fitness: W_AA = 1, W_Aa = 1 - s/2, W_aa = 1 - s
                w_aa = 1.0
                w_ab = 1.0 - abs(s) / 2
                w_bb = 1.0 - abs(s)
                if s < 0:
                    w_aa, w_bb = w_bb, w_aa

                cell.fitness_a = w_aa
                cell.fitness_ab = w_ab
                cell.fitness_b = w_bb

                # Selection on allele frequency
                q = 1.0 - p
                w_bar = p * p * w_aa + 2 * p * q * w_ab + q * q * w_bb
                if w_bar > 0:
                    p_new = (p * p * w_aa + p * q * w_ab) / w_bar
                    cell.allele_freqs[locus] = max(0.0, min(1.0, p_new))

    # -- Engine 3: Migration (Gene Flow) ------------------------------------

    def _apply_migration(self, cells: List[CellGenetics]) -> List[MigrationFlow]:
        flows: List[MigrationFlow] = []

        # Pre-compute per-cell migration rates and weighted allele contributions
        # so that ALL neighbours accumulate correctly (island-model migration).
        # Previous code overwrote new_freqs[i] per-neighbour pair, keeping only
        # the last neighbour's migration effect — a CWE-682 incorrect-calculation.
        m_total: Dict[int, float] = {i: 0.0 for i in range(self.n)}  # Σm for each cell
        m_contrib: Dict[int, Dict[str, float]] = {
            i: {l: 0.0 for l in self.loci} for i in range(self.n)
        }  # Σ(m_j * p_j) for each cell

        for i in range(self.n):
            for j in self.adj[i]:
                if j <= i:
                    continue
                dist = _euclidean(self.points[i], self.points[j])
                m = self.migration_scale / max(dist, 0.01)
                m = min(m, 0.3)

                # Genetic distance (Nei's simplified)
                gen_dist = 0.0
                for locus in self.loci:
                    pi = cells[i].allele_freqs.get(locus, 0.5)
                    pj = cells[j].allele_freqs.get(locus, 0.5)
                    gen_dist += (pi - pj) ** 2
                gen_dist = math.sqrt(gen_dist / max(len(self.loci), 1))

                flows.append(MigrationFlow(source=i, target=j, rate=m, genetic_distance=gen_dist))

                # Accumulate migration contributions for both directions
                m_total[i] += m
                m_total[j] += m
                for locus in self.loci:
                    pi = cells[i].allele_freqs.get(locus, 0.5)
                    pj = cells[j].allele_freqs.get(locus, 0.5)
                    m_contrib[i][locus] += m * pj
                    m_contrib[j][locus] += m * pi

        # Apply accumulated migration: p_new = p*(1-Σm) + Σ(m_j*p_j)
        for i in range(self.n):
            mt = min(m_total[i], 0.95)  # cap total migration to keep resident fraction
            for locus in self.loci:
                p_orig = cells[i].allele_freqs.get(locus, 0.5)
                if m_total[i] > 0:
                    # Normalize contributions if total exceeds cap
                    scale = mt / m_total[i]
                    p_new = p_orig * (1 - mt) + m_contrib[i][locus] * scale
                else:
                    p_new = p_orig
                cells[i].allele_freqs[locus] = max(0.0, min(1.0, p_new))

        return flows

    # -- Engine 4: Genetic Drift --------------------------------------------

    def _apply_drift(self, cells: List[CellGenetics]) -> None:
        for cell in cells:
            n = cell.population_size
            for locus in self.loci:
                p = cell.allele_freqs.get(locus, 0.5)
                # Binomial sampling: draw 2N alleles
                num_a = sum(1 for _ in range(2 * n) if self._rng.random() < p)
                new_p = num_a / (2 * n) if n > 0 else p
                cell.allele_freqs[locus] = new_p
                cell.drift_variance = 1.0 / (2 * n) if n > 0 else 0.0

    # -- Engine 5: Mutation -------------------------------------------------

    def _apply_mutation(self, cells: List[CellGenetics]) -> None:
        mu = self.mutation_rate
        for cell in cells:
            for locus in self.loci:
                p = cell.allele_freqs.get(locus, 0.5)
                # Forward (A->B) and reverse (B->A) mutation
                p_new = p * (1 - mu) + (1 - p) * mu
                cell.allele_freqs[locus] = max(0.0, min(1.0, p_new))

    # -- Engine 6: Hardy-Weinberg Tester ------------------------------------

    def _test_hw(self, cells: List[CellGenetics]) -> None:
        for cell in cells:
            total_chi = 0.0
            locus_count = 0
            for locus in self.loci:
                p = cell.allele_freqs.get(locus, 0.5)
                q = 1.0 - p
                n = cell.population_size

                # Expected genotype frequencies
                exp_aa = p * p
                exp_ab = 2 * p * q
                exp_bb = q * q

                # Simulate observed (with some noise from drift)
                obs_aa = exp_aa + self._rng.gauss(0, 0.02)
                obs_ab = exp_ab + self._rng.gauss(0, 0.02)
                obs_bb = 1.0 - obs_aa - obs_ab
                obs_bb = max(0, obs_bb)

                # Chi-squared (simplified)
                chi2 = 0.0
                for exp, obs in [(exp_aa, obs_aa), (exp_ab, obs_ab), (exp_bb, obs_bb)]:
                    if exp > 0:
                        chi2 += n * (obs - exp) ** 2 / exp
                total_chi += chi2
                locus_count += 1

            cell.hw_chi_squared = total_chi / max(locus_count, 1)
            cell.hw_equilibrium = cell.hw_chi_squared < 3.84  # p=0.05, df=1

            # Expected heterozygosity (average across loci)
            het_sum = 0.0
            for locus in self.loci:
                p = cell.allele_freqs.get(locus, 0.5)
                het_sum += 2 * p * (1 - p)
            cell.expected_het = het_sum / max(len(self.loci), 1)
            cell.observed_het = cell.expected_het * (1 + self._rng.gauss(0, 0.01))

    # -- Engine 7: Insight Generator ----------------------------------------

    def _generate_insights(self, result: GeneticsResult) -> None:
        cells = result.cells
        insights: List[str] = []
        n = len(cells)

        # FST calculation (Weir-Cockerham simplified)
        fst_values: List[float] = []
        for locus in self.loci:
            freqs = [c.allele_freqs.get(locus, 0.5) for c in cells]
            p_bar = sum(freqs) / max(n, 1)
            var_p = sum((p - p_bar) ** 2 for p in freqs) / max(n, 1)
            h_t = 2 * p_bar * (1 - p_bar)
            fst = var_p / h_t if h_t > 0 else 0.0
            fst_values.append(max(0.0, min(1.0, fst)))

        result.mean_fst = sum(fst_values) / max(len(fst_values), 1)

        # Global heterozygosity
        result.global_heterozygosity = sum(c.expected_het for c in cells) / max(n, 1)

        # Cline detection (correlation of allele freq with y-coordinate)
        cline_count = 0
        for locus in self.loci:
            freqs = [c.allele_freqs.get(locus, 0.5) for c in cells]
            ys = [c.y for c in cells]
            if n >= 3:
                r = _pearson(freqs, ys)
                if abs(r) > 0.5:
                    cline_count += 1
                    insights.append(f"Cline detected for '{locus}' (r={r:.3f} with latitude)")
        result.num_clines = cline_count

        # Isolation by distance
        if n >= 3:
            geo_dists: List[float] = []
            gen_dists: List[float] = []
            for i in range(n):
                for j in range(i + 1, n):
                    geo_dists.append(_euclidean(self.points[i], self.points[j]))
                    gd = 0.0
                    for locus in self.loci:
                        pi = cells[i].allele_freqs.get(locus, 0.5)
                        pj = cells[j].allele_freqs.get(locus, 0.5)
                        gd += (pi - pj) ** 2
                    gen_dists.append(math.sqrt(gd / max(len(self.loci), 1)))
            r = _pearson(geo_dists, gen_dists)
            result.isolation_by_distance_r = r
            if r > 0.3:
                insights.append(f"Isolation by distance detected (r={r:.3f})")

        # Fixation events
        if result.fixations:
            insights.append(f"{len(result.fixations)} fixation event(s) detected")

        # Speciation signals
        if result.mean_fst > 0.25:
            insights.append(f"High genetic differentiation (FST={result.mean_fst:.3f}) — possible speciation")
        elif result.mean_fst > 0.15:
            insights.append(f"Moderate differentiation (FST={result.mean_fst:.3f})")
        else:
            insights.append(f"Low differentiation (FST={result.mean_fst:.3f}) — panmictic population")

        # Diversity
        if result.global_heterozygosity < 0.1:
            insights.append("Low genetic diversity — risk of inbreeding depression")
        elif result.global_heterozygosity > 0.4:
            insights.append("High genetic diversity — healthy population")

        # HW equilibrium summary
        hw_deviant = sum(1 for c in cells if not c.hw_equilibrium)
        if hw_deviant > 0:
            insights.append(f"{hw_deviant}/{n} cells deviate from Hardy-Weinberg equilibrium")

        # Environment distribution
        envs = {}
        for c in cells:
            envs[c.environment] = envs.get(c.environment, 0) + 1
        env_parts = [f"{v} {k}" for k, v in sorted(envs.items())]
        insights.append(f"Environment distribution: {', '.join(env_parts)}")

        # Health score
        score = 50.0
        # Diversity bonus
        score += min(20, result.global_heterozygosity * 40)
        # Low fixation bonus
        fix_penalty = min(20, len(result.fixations) * 2)
        score -= fix_penalty
        # HW compliance bonus
        hw_frac = (n - hw_deviant) / max(n, 1)
        score += hw_frac * 15
        # Moderate FST is healthy
        if 0.05 <= result.mean_fst <= 0.25:
            score += 10
        elif result.mean_fst > 0.25:
            score -= 5
        # Clines indicate spatial structure (good)
        score += min(5, cline_count * 2)

        result.health_score = max(0.0, min(100.0, score))
        result.insights = insights

    # -- Main analysis ------------------------------------------------------

    def analyze(self, generations: Optional[int] = None) -> GeneticsResult:
        gen_count = generations or DEFAULT_GENERATIONS

        cells = self._init_gene_pools()
        all_fixations: List[FixationEvent] = []
        all_migrations: List[MigrationFlow] = []

        for g in range(gen_count):
            self._apply_selection(cells)
            migrations = self._apply_migration(cells)
            self._apply_drift(cells)
            self._apply_mutation(cells)

            # Track fixations
            for cell in cells:
                for locus in self.loci:
                    p = cell.allele_freqs.get(locus, 0.5)
                    if p <= 0.0 or p >= 1.0:
                        already = any(
                            f.cell_id == cell.cell_id and f.locus == locus
                            for f in all_fixations
                        )
                        if not already:
                            fixed = "A" if p >= 1.0 else "B"
                            all_fixations.append(FixationEvent(
                                cell_id=cell.cell_id, locus=locus,
                                fixed_allele=fixed, generation=g,
                            ))

            if g == gen_count - 1:
                all_migrations = migrations

        self._test_hw(cells)

        result = GeneticsResult(
            cells=cells,
            migrations=all_migrations,
            fixations=all_fixations,
            generations_simulated=gen_count,
        )
        self._generate_insights(result)
        self._result = result
        return result

    # -- HTML dashboard -----------------------------------------------------

    def to_html(self, path: str) -> str:
        r = self._result
        if r is None:
            r = self.analyze()

        def _e(s: object) -> str:
            return html_mod.escape(str(s))

        # Build cells table rows
        rows = []
        for c in r.cells:
            freq_parts = ", ".join(f"{k}={v:.3f}" for k, v in sorted(c.allele_freqs.items()))
            hw_badge = "✅" if c.hw_equilibrium else "❌"
            rows.append(
                f"<tr><td>{c.cell_id}</td><td>({c.x:.1f}, {c.y:.1f})</td>"
                f"<td>{c.population_size}</td><td>{_e(c.environment)}</td>"
                f"<td style='font-size:0.85em'>{_e(freq_parts)}</td>"
                f"<td>{c.expected_het:.3f}</td><td>{hw_badge}</td></tr>"
            )

        insights_html = "".join(f"<li>{_e(ins)}</li>" for ins in r.insights)

        page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Spatial Genetics Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#e0e0e0;min-height:100vh;padding:1.5rem}}
h1{{text-align:center;margin-bottom:1rem;font-size:1.6rem;background:linear-gradient(90deg,#00f260,#0575e6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.cards{{display:flex;flex-wrap:wrap;gap:1rem;justify-content:center;margin-bottom:1.5rem}}
.card{{background:rgba(255,255,255,.07);border-radius:12px;padding:1.2rem 1.5rem;min-width:150px;text-align:center}}
.card .val{{font-size:1.8rem;font-weight:700;color:#00f260}}
.card .lbl{{font-size:.8rem;opacity:.7;margin-top:.3rem}}
table{{width:100%;border-collapse:collapse;margin-bottom:1.5rem;font-size:.85rem}}
th,td{{padding:.5rem .6rem;border-bottom:1px solid rgba(255,255,255,.1);text-align:left}}
th{{background:rgba(255,255,255,.05);position:sticky;top:0}}
.insights{{background:rgba(255,255,255,.05);border-radius:12px;padding:1rem 1.5rem;margin-top:1rem}}
.insights li{{margin:.4rem 0;line-height:1.5}}
</style></head><body>
<h1>🧬 Spatial Genetics Dashboard</h1>
<div class="cards">
<div class="card"><div class="val">{r.health_score:.0f}</div><div class="lbl">Health Score</div></div>
<div class="card"><div class="val">{r.mean_fst:.3f}</div><div class="lbl">Mean FST</div></div>
<div class="card"><div class="val">{r.global_heterozygosity:.3f}</div><div class="lbl">Heterozygosity</div></div>
<div class="card"><div class="val">{r.generations_simulated}</div><div class="lbl">Generations</div></div>
<div class="card"><div class="val">{len(r.fixations)}</div><div class="lbl">Fixation Events</div></div>
<div class="card"><div class="val">{r.num_clines}</div><div class="lbl">Clines Detected</div></div>
</div>
<table><thead><tr><th>ID</th><th>Position</th><th>Pop</th><th>Env</th><th>Allele Frequencies</th><th>Het</th><th>HW</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<div class="insights"><h2 style="margin-bottom:.5rem">🔬 Insights</h2><ul>{insights_html}</ul></div>
</body></html>"""

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(page)
        return path

    # -- JSON export --------------------------------------------------------

    def to_json(self, path: str) -> str:
        r = self._result
        if r is None:
            r = self.analyze()
        data = {
            "generations_simulated": r.generations_simulated,
            "mean_fst": r.mean_fst,
            "global_heterozygosity": r.global_heterozygosity,
            "isolation_by_distance_r": r.isolation_by_distance_r,
            "num_clines": r.num_clines,
            "health_score": r.health_score,
            "insights": r.insights,
            "num_fixations": len(r.fixations),
            "fixations": [asdict(f) for f in r.fixations],
            "cells": [asdict(c) for c in r.cells],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return path


# ---------------------------------------------------------------------------
# Pearson correlation helper
# ---------------------------------------------------------------------------


def _pearson(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx * dy == 0:
        return 0.0
    return num / (dx * dy)


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------


def genetics_analyze(
    path: str,
    generations: int = DEFAULT_GENERATIONS,
    seed: Optional[int] = None,
) -> GeneticsResult:
    engine = GeneticsEngine(path=path, seed=seed)
    return engine.analyze(generations=generations)


def genetics_demo(seed: int = 42) -> GeneticsResult:
    rng = random.Random(seed)
    points = [(rng.uniform(0, 100), rng.uniform(0, 100)) for _ in range(25)]
    engine = GeneticsEngine(points=points, seed=seed)
    result = engine.analyze()
    print(f"=== Spatial Genetics Demo ({len(points)} cells, {result.generations_simulated} generations) ===")
    print(f"Health Score : {result.health_score:.1f}/100")
    print(f"Mean FST     : {result.mean_fst:.4f}")
    print(f"Heterozygosity: {result.global_heterozygosity:.4f}")
    print(f"Fixations    : {len(result.fixations)}")
    print(f"Clines       : {result.num_clines}")
    print(f"IBD r        : {result.isolation_by_distance_r:.4f}")
    print()
    for ins in result.insights:
        print(f"  • {ins}")
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Spatial Genetics Engine — population genetics on Voronoi tessellations"
    )
    parser.add_argument("input", nargs="?", help="Path to points file")
    parser.add_argument("--generations", type=int, default=DEFAULT_GENERATIONS)
    parser.add_argument("--json", dest="json_path", help="Export JSON report")
    parser.add_argument("--html", dest="html_path", help="Export HTML dashboard")
    parser.add_argument("--demo", action="store_true", help="Run demo")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    args = parser.parse_args()

    if args.demo:
        result = genetics_demo(seed=args.seed or 42)
        if args.html_path:
            # Re-create the same point set with a single RNG instance.
            # The previous code constructed a *new* random.Random(seed)
            # for every coordinate, so every call returned the same value
            # and all 25 points collapsed to a single identical position.
            demo_rng = random.Random(args.seed or 42)
            demo_points = [(demo_rng.uniform(0, 100), demo_rng.uniform(0, 100))
                           for _ in range(25)]
            engine = GeneticsEngine(points=demo_points, seed=args.seed or 42)
            engine.analyze()
            engine.to_html(args.html_path)
            print(f"\nDashboard: {args.html_path}")
        return

    if not args.input:
        parser.error("Provide a points file or --demo")

    engine = GeneticsEngine(path=args.input, seed=args.seed)
    result = engine.analyze(generations=args.generations)

    print(f"Health Score: {result.health_score:.1f}/100")
    print(f"FST: {result.mean_fst:.4f}  Het: {result.global_heterozygosity:.4f}")
    print(f"Fixations: {len(result.fixations)}  Clines: {result.num_clines}")
    for ins in result.insights:
        print(f"  • {ins}")

    if args.json_path:
        engine.to_json(args.json_path)
        print(f"JSON: {args.json_path}")
    if args.html_path:
        engine.to_html(args.html_path)
        print(f"HTML: {args.html_path}")


if __name__ == "__main__":
    _main()
