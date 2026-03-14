"""Monte Carlo Spatial Simulation for VoronoiMap.

Runs Monte Carlo simulations to establish null distributions and
confidence envelopes for spatial statistics.  Tests whether an observed
point pattern is significantly different from Complete Spatial
Randomness (CSR) by generating many random datasets and comparing.

Analyses supported:
    - **NNI envelope**: Nearest-Neighbor Index distribution under CSR
    - **Ripley's L envelope**: Multi-scale L(r) confidence bands
    - **Quadrat VMR envelope**: Variance-to-Mean Ratio distribution
    - **Area distribution envelope**: Voronoi cell area statistics
    - **Full hypothesis test**: Combined p-values across all metrics

Example::

    from vormap_montecarlo import MonteCarloTest

    test = MonteCarloTest(observed_points, bounds=(0, 1000, 0, 2000))
    result = test.run(simulations=999)
    print(result.summary())

CLI::

    python vormap_montecarlo.py datauni5.txt --sims 999 --seed 42
    python vormap_montecarlo.py datauni5.txt --sims 499 --json mc_result.json
    python vormap_montecarlo.py datauni5.txt --sims 999 --svg envelope.svg
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from collections import namedtuple
from typing import Dict, List, Optional, Sequence, Tuple

import vormap
from vormap_geometry import (
    mean as _mean,
    median as _median,
    std as _std,
    percentile as _percentile,
    polygon_area,
)

Point = Tuple[float, float]


# ── Reliable percentile (sorted) ───────────────────────────────────

def _safe_percentile(data, pct):
    """Percentile that sorts first to guarantee monotonicity."""
    if not data:
        return 0.0
    sd = sorted(data)
    n = len(sd)
    k = (pct / 100.0) * (n - 1)
    lo = int(math.floor(k))
    hi = min(lo + 1, n - 1)
    frac = k - lo
    return sd[lo] + frac * (sd[hi] - sd[lo])


# ── Result types ────────────────────────────────────────────────────

NNIEnvelope = namedtuple("NNIEnvelope", [
    "observed",        # observed NNI value
    "simulated_mean",  # mean NNI across simulations
    "simulated_std",   # std dev of simulated NNIs
    "lower_2_5",       # 2.5th percentile (lower 95% CI)
    "upper_97_5",      # 97.5th percentile (upper 95% CI)
    "p_value",         # two-sided Monte Carlo p-value
    "rank",            # rank of observed among simulations
    "n_sims",          # number of simulations
    "interpretation",  # "clustered" / "random" / "dispersed"
])

VMREnvelope = namedtuple("VMREnvelope", [
    "observed",
    "simulated_mean",
    "simulated_std",
    "lower_2_5",
    "upper_97_5",
    "p_value",
    "rank",
    "n_sims",
    "interpretation",
])

AreaEnvelope = namedtuple("AreaEnvelope", [
    "observed_mean",
    "observed_cv",       # coefficient of variation
    "simulated_mean_mean",
    "simulated_cv_mean",
    "cv_lower_2_5",
    "cv_upper_97_5",
    "cv_p_value",
    "cv_rank",
    "n_sims",
    "interpretation",
])

RipleysLEnvelope = namedtuple("RipleysLEnvelope", [
    "radii",           # list of r values
    "observed_l",      # observed L(r) values
    "envelope_mean",   # mean L(r) per radius across sims
    "envelope_lower",  # 2.5th percentile per radius
    "envelope_upper",  # 97.5th percentile per radius
    "max_deviation",   # max |observed - mean| across radii
    "global_p_value",  # proportion of sims with >= max deviation
    "n_sims",
])


class MonteCarloResult:
    """Combined results from a Monte Carlo spatial hypothesis test."""

    def __init__(self, n_points, bounds, n_sims, seed):
        self.n_points = n_points
        self.bounds = bounds
        self.n_sims = n_sims
        self.seed = seed
        self.nni = None           # type: Optional[NNIEnvelope]
        self.vmr = None           # type: Optional[VMREnvelope]
        self.area = None          # type: Optional[AreaEnvelope]
        self.ripleys_l = None     # type: Optional[RipleysLEnvelope]
        self._sim_nnis = []
        self._sim_vmrs = []
        self._sim_area_cvs = []

    def summary(self):
        """Return a human-readable summary of all test results."""
        lines = []
        lines.append("=" * 60)
        lines.append("Monte Carlo Spatial Hypothesis Test")
        lines.append("=" * 60)
        lines.append("Points: %d  |  Simulations: %d  |  Seed: %s"
                      % (self.n_points, self.n_sims,
                         str(self.seed) if self.seed is not None else "random"))
        bounds = self.bounds
        lines.append("Bounds: S=%.1f N=%.1f W=%.1f E=%.1f"
                      % (bounds[0], bounds[1], bounds[2], bounds[3]))
        lines.append("")

        if self.nni is not None:
            lines.append("--- Nearest-Neighbor Index (NNI) ---")
            lines.append("  Observed NNI:    %.4f" % self.nni.observed)
            lines.append("  Simulated mean:  %.4f (+/- %.4f)"
                          % (self.nni.simulated_mean, self.nni.simulated_std))
            lines.append("  95%% CI:          [%.4f, %.4f]"
                          % (self.nni.lower_2_5, self.nni.upper_97_5))
            lines.append("  p-value:         %.4f" % self.nni.p_value)
            lines.append("  Interpretation:  %s" % self.nni.interpretation)
            lines.append("")

        if self.vmr is not None:
            lines.append("--- Quadrat VMR (Variance-to-Mean Ratio) ---")
            lines.append("  Observed VMR:    %.4f" % self.vmr.observed)
            lines.append("  Simulated mean:  %.4f (+/- %.4f)"
                          % (self.vmr.simulated_mean, self.vmr.simulated_std))
            lines.append("  95%% CI:          [%.4f, %.4f]"
                          % (self.vmr.lower_2_5, self.vmr.upper_97_5))
            lines.append("  p-value:         %.4f" % self.vmr.p_value)
            lines.append("  Interpretation:  %s" % self.vmr.interpretation)
            lines.append("")

        if self.area is not None:
            lines.append("--- Voronoi Cell Area Distribution ---")
            lines.append("  Observed mean area: %.2f  (CV: %.4f)"
                          % (self.area.observed_mean, self.area.observed_cv))
            lines.append("  Simulated mean CV:  %.4f"
                          % self.area.simulated_cv_mean)
            lines.append("  CV 95%% CI:          [%.4f, %.4f]"
                          % (self.area.cv_lower_2_5, self.area.cv_upper_97_5))
            lines.append("  CV p-value:         %.4f" % self.area.cv_p_value)
            lines.append("  Interpretation:     %s" % self.area.interpretation)
            lines.append("")

        if self.ripleys_l is not None:
            lines.append("--- Ripley's L Function ---")
            lines.append("  Radii tested:    %d" % len(self.ripleys_l.radii))
            lines.append("  Max deviation:   %.4f"
                          % self.ripleys_l.max_deviation)
            lines.append("  Global p-value:  %.4f"
                          % self.ripleys_l.global_p_value)
            lines.append("")

        # Overall verdict
        lines.append("--- Overall Verdict ---")
        p_values = []
        labels = []
        if self.nni is not None:
            p_values.append(self.nni.p_value)
            labels.append("NNI")
        if self.vmr is not None:
            p_values.append(self.vmr.p_value)
            labels.append("VMR")
        if self.area is not None:
            p_values.append(self.area.cv_p_value)
            labels.append("Area CV")
        if self.ripleys_l is not None:
            p_values.append(self.ripleys_l.global_p_value)
            labels.append("Ripley's L")

        if p_values:
            sig = [(l, p) for l, p in zip(labels, p_values) if p < 0.05]
            if sig:
                sig_str = ", ".join("%s (p=%.4f)" % (l, p) for l, p in sig)
                lines.append("  REJECT CSR (alpha=0.05): %s" % sig_str)
                if self.nni is not None and self.nni.p_value < 0.05:
                    lines.append("  Pattern type: %s"
                                  % self.nni.interpretation.upper())
            else:
                lines.append("  FAIL TO REJECT CSR (alpha=0.05)")
                lines.append("  Pattern is consistent with spatial randomness.")
        else:
            lines.append("  No tests computed.")

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self):
        """Serializable dictionary of all results."""
        d = {
            "n_points": self.n_points,
            "bounds": list(self.bounds),
            "n_sims": self.n_sims,
            "seed": self.seed,
        }
        if self.nni is not None:
            d["nni"] = self.nni._asdict()
        if self.vmr is not None:
            d["vmr"] = self.vmr._asdict()
        if self.area is not None:
            d["area"] = self.area._asdict()
        if self.ripleys_l is not None:
            d["ripleys_l"] = self.ripleys_l._asdict()
        return d


# ── Monte Carlo Test Engine ─────────────────────────────────────────

class MonteCarloTest:
    """Run Monte Carlo simulations to test a point pattern against CSR.

    Parameters
    ----------
    observed_points : list[tuple[float, float]]
        The observed point dataset.
    bounds : tuple[float, float, float, float]
        Study area (south, north, west, east).
    """

    def __init__(self, observed_points, bounds):
        if len(observed_points) < 3:
            raise ValueError("Need at least 3 points for Monte Carlo test.")
        self.points = list(observed_points)
        self.n = len(self.points)
        self.bounds = bounds

    def run(self, simulations=999, seed=None, radii_count=10,
            quadrat_nx=None, quadrat_ny=None):
        """Run full Monte Carlo hypothesis test.

        Parameters
        ----------
        simulations : int
            Number of CSR simulations (default 999).
        seed : int or None
            Random seed for reproducibility.
        radii_count : int
            Number of radii for Ripley's L (default 10).
        quadrat_nx, quadrat_ny : int or None
            Quadrat grid dimensions.  Defaults to sqrt(n/2).

        Returns
        -------
        MonteCarloResult
        """
        if seed is not None:
            random.seed(seed)

        result = MonteCarloResult(self.n, self.bounds, simulations, seed)

        # Compute observed statistics
        obs_nni = self._compute_nni(self.points)
        obs_vmr = self._compute_vmr(self.points, quadrat_nx, quadrat_ny)
        obs_areas = self._compute_voronoi_areas(self.points)
        obs_area_mean = _mean(obs_areas) if obs_areas else 0.0
        obs_area_cv = (_std(obs_areas) / obs_area_mean
                       if obs_areas and obs_area_mean > 0 else 0.0)

        # Determine radii for Ripley's L
        s, n_b, w, e = self.bounds
        max_dist = min(n_b - s, e - w) / 2.0
        radii = [max_dist * (i + 1) / (radii_count + 1)
                 for i in range(radii_count)]
        obs_l = self._compute_ripleys_l(self.points, radii)

        # Run simulations
        sim_nnis = []
        sim_vmrs = []
        sim_area_cvs = []
        sim_area_means = []
        sim_l_values = [[] for _ in radii]

        for _ in range(simulations):
            sim_pts = self._generate_csr(self.n)
            sim_nnis.append(self._compute_nni(sim_pts))
            sim_vmrs.append(self._compute_vmr(sim_pts, quadrat_nx, quadrat_ny))

            sim_areas = self._compute_voronoi_areas(sim_pts)
            sim_am = _mean(sim_areas) if sim_areas else 0.0
            sim_area_means.append(sim_am)
            sim_acv = (_std(sim_areas) / sim_am
                       if sim_areas and sim_am > 0 else 0.0)
            sim_area_cvs.append(sim_acv)

            sim_l = self._compute_ripleys_l(sim_pts, radii)
            for j in range(len(radii)):
                sim_l_values[j].append(sim_l[j])

        result._sim_nnis = sim_nnis
        result._sim_vmrs = sim_vmrs
        result._sim_area_cvs = sim_area_cvs

        # ── NNI envelope ────────────────────────────────────────
        nni_rank = sum(1 for v in sim_nnis
                       if abs(v - 1.0) >= abs(obs_nni - 1.0))
        nni_p = (nni_rank + 1) / (simulations + 1)
        nni_mean = _mean(sim_nnis)
        nni_std = _std(sim_nnis)

        nni_lo = _safe_percentile(sim_nnis, 2.5)
        nni_hi = _safe_percentile(sim_nnis, 97.5)

        if obs_nni < nni_lo:
            nni_interp = "clustered"
        elif obs_nni > nni_hi:
            nni_interp = "dispersed"
        else:
            nni_interp = "random"

        result.nni = NNIEnvelope(
            observed=round(obs_nni, 6),
            simulated_mean=round(nni_mean, 6),
            simulated_std=round(nni_std, 6),
            lower_2_5=round(nni_lo, 6),
            upper_97_5=round(nni_hi, 6),
            p_value=round(nni_p, 6),
            rank=nni_rank,
            n_sims=simulations,
            interpretation=nni_interp,
        )

        # ── VMR envelope ────────────────────────────────────────
        vmr_rank = sum(1 for v in sim_vmrs
                       if abs(v - 1.0) >= abs(obs_vmr - 1.0))
        vmr_p = (vmr_rank + 1) / (simulations + 1)
        vmr_mean = _mean(sim_vmrs)
        vmr_std = _std(sim_vmrs)

        vmr_lo = _safe_percentile(sim_vmrs, 2.5)
        vmr_hi = _safe_percentile(sim_vmrs, 97.5)

        if obs_vmr > vmr_hi:
            vmr_interp = "clustered"
        elif obs_vmr < vmr_lo:
            vmr_interp = "dispersed"
        else:
            vmr_interp = "random"

        result.vmr = VMREnvelope(
            observed=round(obs_vmr, 6),
            simulated_mean=round(vmr_mean, 6),
            simulated_std=round(vmr_std, 6),
            lower_2_5=round(vmr_lo, 6),
            upper_97_5=round(vmr_hi, 6),
            p_value=round(vmr_p, 6),
            rank=vmr_rank,
            n_sims=simulations,
            interpretation=vmr_interp,
        )

        # ── Area CV envelope ────────────────────────────────────
        cv_rank = sum(1 for v in sim_area_cvs if v >= obs_area_cv)
        cv_p = (cv_rank + 1) / (simulations + 1)
        cv_mean = _mean(sim_area_cvs)

        cv_lo = _safe_percentile(sim_area_cvs, 2.5)
        cv_hi = _safe_percentile(sim_area_cvs, 97.5)

        if obs_area_cv > cv_hi:
            area_interp = "more variable than CSR (clustered/irregular)"
        elif obs_area_cv < cv_lo:
            area_interp = "more uniform than CSR (regular/dispersed)"
        else:
            area_interp = "consistent with CSR"

        result.area = AreaEnvelope(
            observed_mean=round(obs_area_mean, 4),
            observed_cv=round(obs_area_cv, 6),
            simulated_mean_mean=round(_mean(sim_area_means), 4),
            simulated_cv_mean=round(cv_mean, 6),
            cv_lower_2_5=round(cv_lo, 6),
            cv_upper_97_5=round(cv_hi, 6),
            cv_p_value=round(cv_p, 6),
            cv_rank=cv_rank,
            n_sims=simulations,
            interpretation=area_interp,
        )

        # ── Ripley's L envelope ─────────────────────────────────
        env_mean = [_mean(sim_l_values[j]) for j in range(len(radii))]
        env_lower = [_safe_percentile(sim_l_values[j], 2.5)
                     for j in range(len(radii))]
        env_upper = [_safe_percentile(sim_l_values[j], 97.5)
                     for j in range(len(radii))]

        obs_max_dev = max(abs(obs_l[j] - env_mean[j])
                         for j in range(len(radii)))

        global_count = 0
        for s_idx in range(simulations):
            sim_max = max(abs(sim_l_values[j][s_idx] - env_mean[j])
                         for j in range(len(radii)))
            if sim_max >= obs_max_dev:
                global_count += 1
        global_p = (global_count + 1) / (simulations + 1)

        result.ripleys_l = RipleysLEnvelope(
            radii=[round(r, 4) for r in radii],
            observed_l=[round(v, 6) for v in obs_l],
            envelope_mean=[round(v, 6) for v in env_mean],
            envelope_lower=[round(v, 6) for v in env_lower],
            envelope_upper=[round(v, 6) for v in env_upper],
            max_deviation=round(obs_max_dev, 6),
            global_p_value=round(global_p, 6),
            n_sims=simulations,
        )

        return result

    # ── Internal computations ───────────────────────────────────

    def _generate_csr(self, n):
        """Generate n uniform random points within bounds."""
        s, north, w, e = self.bounds
        return [(random.uniform(w, e), random.uniform(s, north))
                for _ in range(n)]

    def _compute_nni(self, points):
        """Compute Clark-Evans Nearest-Neighbor Index.

        Uses a grid-based spatial index for O(n) expected time instead
        of the naive O(n^2) all-pairs approach.  The study area is
        partitioned into square grid cells whose side length is chosen
        so that the expected nearest-neighbor distance falls within one
        cell, making only the 9 surrounding cells (3x3 neighbourhood)
        necessary for the search.
        """
        n = len(points)
        if n < 2:
            return 1.0

        s, north, w, e = self.bounds
        area = (north - s) * (e - w)
        density = n / area if area > 0 else 0
        expected = 0.5 / math.sqrt(density) if density > 0 else 0

        # Grid cell size — use 2x expected NN distance to guarantee
        # that the true nearest neighbour is in an adjacent cell.
        cell_size = max(expected * 2.0, 1e-9)
        inv_cell = 1.0 / cell_size

        # Build spatial grid — dict of (col, row) -> list of point indices
        grid = {}
        for idx in range(n):
            cx = int((points[idx][0] - w) * inv_cell)
            cy = int((points[idx][1] - s) * inv_cell)
            key = (cx, cy)
            if key in grid:
                grid[key].append(idx)
            else:
                grid[key] = [idx]

        total_nn = 0.0
        for i in range(n):
            px, py = points[i]
            cx = int((px - w) * inv_cell)
            cy = int((py - s) * inv_cell)
            min_d = float("inf")
            # Search 3x3 neighbourhood of grid cells
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    bucket = grid.get((cx + dx, cy + dy))
                    if bucket is None:
                        continue
                    for j in bucket:
                        if j == i:
                            continue
                        d = math.hypot(px - points[j][0],
                                       py - points[j][1])
                        if d < min_d:
                            min_d = d
            # Fallback: if no neighbor found in 3x3 (extremely sparse
            # or degenerate), widen search
            if min_d == float("inf"):
                for j in range(n):
                    if j == i:
                        continue
                    d = math.hypot(px - points[j][0],
                                   py - points[j][1])
                    if d < min_d:
                        min_d = d
            total_nn += min_d
        obs_mean = total_nn / n

        return obs_mean / expected if expected > 0 else 1.0

    def _compute_vmr(self, points, nx=None, ny=None):
        """Compute Variance-to-Mean Ratio from quadrat counts."""
        n = len(points)
        if nx is None:
            nx = max(2, int(math.sqrt(n / 2)))
        if ny is None:
            ny = nx

        s, north, w, e = self.bounds
        cell_w = (e - w) / nx
        cell_h = (north - s) / ny
        if cell_w <= 0 or cell_h <= 0:
            return 1.0

        counts = [[0] * ny for _ in range(nx)]
        for x, y in points:
            ci = min(int((x - w) / cell_w), nx - 1)
            ri = min(int((y - s) / cell_h), ny - 1)
            if 0 <= ci < nx and 0 <= ri < ny:
                counts[ci][ri] += 1

        flat = [counts[i][j] for i in range(nx) for j in range(ny)]
        mean_c = _mean(flat)
        if mean_c == 0:
            return 1.0
        var_c = sum((c - mean_c) ** 2 for c in flat) / len(flat)
        return var_c / mean_c

    def _compute_voronoi_areas(self, points):
        """Compute Voronoi cell areas via vormap region computation."""
        try:
            regions = vormap.compute_regions(points, self.bounds)
            areas = []
            for seed, verts in regions.items():
                a = polygon_area(verts)
                if a > 0:
                    areas.append(a)
            return areas if areas else [1.0]
        except Exception:
            s, north, w, e = self.bounds
            total = (north - s) * (e - w)
            return [total / len(points)] * len(points)

    def _compute_ripleys_l(self, points, radii):
        """Compute Besag's L(r) = sqrt(K(r)/pi) - r."""
        n = len(points)
        if n < 2:
            return [0.0] * len(radii)

        s, north, w, e = self.bounds
        area = (north - s) * (e - w)
        l_values = []

        for r in radii:
            count = 0
            for i in range(n):
                for j in range(n):
                    if i == j:
                        continue
                    d = math.hypot(points[i][0] - points[j][0],
                                   points[i][1] - points[j][1])
                    if d <= r:
                        count += 1
            k_r = area * count / (n * n) if n > 0 else 0
            l_r = math.sqrt(k_r / math.pi) - r if k_r >= 0 else -r
            l_values.append(l_r)

        return l_values


# ── SVG Export ──────────────────────────────────────────────────────

def export_envelope_svg(result, output_path, width=700, height=500):
    """Export Ripley's L envelope as an SVG chart."""
    vormap.validate_output_path(output_path)

    if result.ripleys_l is None:
        raise ValueError("No Ripley's L data in result.")

    rl = result.ripleys_l
    pad = 60
    plot_w = width - 2 * pad
    plot_h = height - 2 * pad

    all_vals = rl.observed_l + rl.envelope_lower + rl.envelope_upper
    min_r = min(rl.radii) if rl.radii else 0
    max_r = max(rl.radii) if rl.radii else 1
    min_l = min(all_vals) if all_vals else -1
    max_l = max(all_vals) if all_vals else 1

    l_range = max_l - min_l
    if l_range < 1e-9:
        l_range = 1.0
    min_l -= l_range * 0.05
    max_l += l_range * 0.05
    l_range = max_l - min_l

    r_range = max_r - min_r
    if r_range < 1e-9:
        r_range = 1.0

    def sx(r):
        return pad + (r - min_r) / r_range * plot_w

    def sy(l):
        return pad + plot_h - (l - min_l) / l_range * plot_h

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<svg xmlns="http://www.w3.org/2000/svg" '
                 'width="%d" height="%d" '
                 'viewBox="0 0 %d %d">' % (width, height, width, height))
    lines.append('<style>')
    lines.append('  text { font-family: sans-serif; font-size: 11px; '
                 'fill: #333; }')
    lines.append('  .title { font-size: 14px; font-weight: bold; }')
    lines.append('</style>')

    lines.append('<rect width="%d" height="%d" fill="white"/>'
                 % (width, height))
    lines.append('<text x="%d" y="20" text-anchor="middle" class="title">'
                 "Ripley's L(r) Envelope (%d simulations)</text>"
                 % (width // 2, rl.n_sims))

    # Axes
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" '
                 'stroke="#999"/>' % (pad, pad, pad, pad + plot_h))
    lines.append('<line x1="%d" y1="%d" x2="%d" y2="%d" '
                 'stroke="#999"/>'
                 % (pad, pad + plot_h, pad + plot_w, pad + plot_h))

    # CSR line
    if min_l <= 0 <= max_l:
        y0 = sy(0)
        lines.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" '
                     'stroke="#ccc" stroke-dasharray="4,4"/>'
                     % (pad, y0, pad + plot_w, y0))

    # Envelope band
    env_pts = []
    for j in range(len(rl.radii)):
        env_pts.append("%.1f,%.1f" % (sx(rl.radii[j]),
                                       sy(rl.envelope_upper[j])))
    for j in range(len(rl.radii) - 1, -1, -1):
        env_pts.append("%.1f,%.1f" % (sx(rl.radii[j]),
                                       sy(rl.envelope_lower[j])))
    lines.append('<polygon points="%s" fill="#e0e8f0" opacity="0.6"/>'
                 % " ".join(env_pts))

    # Mean line
    mean_pts = " ".join("%.1f,%.1f" % (sx(rl.radii[j]),
                                        sy(rl.envelope_mean[j]))
                        for j in range(len(rl.radii)))
    lines.append('<polyline points="%s" fill="none" '
                 'stroke="#7799bb" stroke-width="1.5" '
                 'stroke-dasharray="6,3"/>' % mean_pts)

    # Observed line
    obs_pts = " ".join("%.1f,%.1f" % (sx(rl.radii[j]),
                                       sy(rl.observed_l[j]))
                       for j in range(len(rl.radii)))
    lines.append('<polyline points="%s" fill="none" '
                 'stroke="#d44" stroke-width="2"/>' % obs_pts)

    for j in range(len(rl.radii)):
        lines.append('<circle cx="%.1f" cy="%.1f" r="3" fill="#d44"/>'
                     % (sx(rl.radii[j]), sy(rl.observed_l[j])))

    # Legend
    lx, ly = pad + 10, pad + 15
    lines.append('<rect x="%d" y="%d" width="12" height="3" '
                 'fill="#d44"/>' % (lx, ly))
    lines.append('<text x="%d" y="%d">Observed</text>' % (lx + 16, ly + 4))
    lines.append('<rect x="%d" y="%d" width="12" height="3" '
                 'fill="#7799bb"/>' % (lx, ly + 16))
    lines.append('<text x="%d" y="%d">Simulated mean</text>'
                 % (lx + 16, ly + 20))
    lines.append('<rect x="%d" y="%d" width="12" height="12" '
                 'fill="#e0e8f0" opacity="0.6"/>' % (lx, ly + 28))
    lines.append('<text x="%d" y="%d">95%% envelope</text>'
                 % (lx + 16, ly + 38))

    # P-value
    lines.append('<text x="%d" y="%d" text-anchor="end" '
                 'font-size="11">Global p = %.4f</text>'
                 % (pad + plot_w, pad + plot_h + 35, rl.global_p_value))

    lines.append('</svg>')

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── JSON Export ─────────────────────────────────────────────────────

def export_json(result, output_path):
    """Export Monte Carlo results as JSON."""
    vormap.validate_output_path(output_path)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)


# ── CLI ─────────────────────────────────────────────────────────────

def _load_points(path):
    """Load points from CSV/text/JSON file."""
    points = []
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    x = item.get("x", item.get("lon", item.get("long", 0)))
                    y = item.get("y", item.get("lat", 0))
                    points.append((float(x), float(y)))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    points.append((float(item[0]), float(item[1])))
        return points

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            if len(parts) >= 2:
                try:
                    x, y = float(parts[0]), float(parts[1])
                    points.append((x, y))
                except ValueError:
                    continue
    return points


def main():
    """CLI entry point for Monte Carlo spatial simulation."""
    parser = argparse.ArgumentParser(
        description="Monte Carlo spatial hypothesis test against CSR.",
        epilog="Example: python vormap_montecarlo.py data.txt --sims 999",
    )
    parser.add_argument("datafile",
                        help="Point data file (CSV/text/JSON).")
    parser.add_argument("--sims", type=int, default=199,
                        help="Number of simulations (default: 199).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility.")
    parser.add_argument("--bounds", nargs=4, type=float,
                        metavar=("S", "N", "W", "E"),
                        help="Study area bounds (auto-detected if omitted).")
    parser.add_argument("--radii", type=int, default=10,
                        help="Number of radii for Ripley's L (default: 10).")
    parser.add_argument("--json", metavar="OUTPUT",
                        help="Export results as JSON.")
    parser.add_argument("--svg", metavar="OUTPUT",
                        help="Export Ripley's L envelope as SVG.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress text summary output.")

    args = parser.parse_args()

    points = _load_points(args.datafile)
    if len(points) < 3:
        print("Error: need at least 3 points.", file=sys.stderr)
        sys.exit(1)

    if args.bounds:
        bounds = tuple(args.bounds)
    else:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        pad_x = (max(xs) - min(xs)) * 0.05 or 10
        pad_y = (max(ys) - min(ys)) * 0.05 or 10
        bounds = (min(ys) - pad_y, max(ys) + pad_y,
                  min(xs) - pad_x, max(xs) + pad_x)

    test = MonteCarloTest(points, bounds)
    result = test.run(simulations=args.sims, seed=args.seed,
                      radii_count=args.radii)

    if not args.quiet:
        print(result.summary())

    if args.json:
        export_json(result, args.json)
        print("JSON exported to %s" % args.json)

    if args.svg:
        export_envelope_svg(result, args.svg)
        print("SVG exported to %s" % args.svg)


if __name__ == "__main__":
    main()
