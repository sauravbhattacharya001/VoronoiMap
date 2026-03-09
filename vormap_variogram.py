"""Variogram analysis for Voronoi-based spatial data.

Computes experimental variograms (semivariance as a function of lag
distance) and fits theoretical variogram models.  Variograms reveal
the spatial correlation structure of data -- how similarity between
measurements decreases with distance.  This is fundamental to:

- **Kriging** -- optimal spatial interpolation requires a fitted
  variogram model to compute weights.
- **Spatial autocorrelation** -- variograms quantify the range and
  strength of spatial dependence.
- **Sampling design** -- the range tells you how far apart samples
  can be while still capturing spatial structure.

Theoretical models
------------------
- **Spherical** -- linear increase up to range, then flat (sill).
  Most common model for geoscience data.
- **Exponential** -- asymptotic approach to sill; practical range
  at ~3× the range parameter.
- **Gaussian** -- S-shaped; smooth near origin.  Good for very
  smooth spatial processes.
- **Linear** -- unbounded linear increase.  Simplest model.
- **Power** -- fractional power model for fractal-like variation.
- **Nugget** -- pure noise (no spatial structure).

Key parameters
--------------
- **Nugget (c₀)** -- semivariance at zero distance (measurement
  error or micro-scale variation).
- **Sill (c₀ + c₁)** -- plateau semivariance at large distances.
- **Range (a)** -- distance at which semivariance reaches the sill
  (spatial autocorrelation disappears beyond this).

Usage::

    from vormap_variogram import (
        experimental_variogram, fit_variogram,
        export_variogram_svg, export_variogram_csv,
    )

    # Points with measured values (e.g. elevation, pollution, temperature)
    points = [(100, 200), (300, 400), (500, 100), ...]
    values = [25.0, 30.0, 22.0, ...]

    # Compute experimental variogram
    ev = experimental_variogram(points, values, n_lags=15)

    # Fit a spherical model
    model = fit_variogram(ev, model_type="spherical")
    print(f"Nugget={model.nugget:.2f}, Sill={model.sill:.2f}, Range={model.range:.1f}")

    # Export SVG visualization
    export_variogram_svg(ev, model, "variogram.svg")
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Data Structures ──────────────────────────────────────────────────


@dataclass
class LagBin:
    """One bin of an experimental variogram."""
    lag_center: float       # Center distance of this bin
    semivariance: float     # Average semivariance for point pairs in this bin
    pair_count: int         # Number of point pairs contributing
    lag_low: float          # Lower bound of the bin
    lag_high: float         # Upper bound of the bin


@dataclass
class ExperimentalVariogram:
    """Result of computing an experimental (empirical) variogram."""
    bins: List[LagBin]
    n_points: int
    n_pairs: int
    max_distance: float
    lag_width: float
    direction: Optional[float] = None   # None = omnidirectional
    tolerance: float = 90.0             # angular tolerance in degrees


@dataclass
class VariogramModel:
    """A fitted theoretical variogram model."""
    model_type: str         # "spherical", "exponential", "gaussian", "linear", "power", "nugget"
    nugget: float           # c₀ -- semivariance at distance 0
    sill: float             # c₀ + c₁ -- total sill
    range_param: float      # a -- range parameter
    partial_sill: float     # c₁ = sill - nugget
    rmse: float             # Root mean square error of fit
    r_squared: float        # Goodness of fit (1 - SS_res/SS_tot)


@dataclass
class VariogramSurface:
    """2D variogram surface for anisotropy analysis."""
    angles: List[float]         # Direction angles (degrees)
    variograms: Dict[float, ExperimentalVariogram]  # angle → variogram
    anisotropy_ratio: float     # Major/minor range ratio
    major_direction: float      # Direction of maximum range (degrees)
    minor_direction: float      # Direction of minimum range (degrees)


# ── Distance / Angle helpers ─────────────────────────────────────────


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _angle_deg(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Angle in degrees from a to b (0 = East, counter-clockwise)."""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    return math.degrees(math.atan2(dy, dx)) % 360


def _angle_in_tolerance(angle: float, direction: float, tolerance: float) -> bool:
    """Check if angle is within tolerance of direction (handles wrapping)."""
    diff = abs(angle - direction) % 360
    if diff > 180:
        diff = 360 - diff
    return diff <= tolerance


# ── Theoretical Model Functions ──────────────────────────────────────


def _model_spherical(h: float, nugget: float, partial_sill: float, range_param: float) -> float:
    if h == 0:
        return 0.0
    if range_param <= 0:
        return nugget + partial_sill
    if h >= range_param:
        return nugget + partial_sill
    ratio = h / range_param
    return nugget + partial_sill * (1.5 * ratio - 0.5 * ratio ** 3)


def _model_exponential(h: float, nugget: float, partial_sill: float, range_param: float) -> float:
    if h == 0:
        return 0.0
    if range_param <= 0:
        return nugget + partial_sill
    return nugget + partial_sill * (1.0 - math.exp(-3.0 * h / range_param))


def _model_gaussian(h: float, nugget: float, partial_sill: float, range_param: float) -> float:
    if h == 0:
        return 0.0
    if range_param <= 0:
        return nugget + partial_sill
    return nugget + partial_sill * (1.0 - math.exp(-3.0 * (h / range_param) ** 2))


def _model_linear(h: float, nugget: float, partial_sill: float, range_param: float) -> float:
    if h == 0:
        return 0.0
    slope = partial_sill / max(range_param, 1e-12)
    return nugget + slope * h


def _model_power(h: float, nugget: float, partial_sill: float, range_param: float) -> float:
    if h == 0:
        return 0.0
    # range_param acts as the exponent (0 < exponent < 2)
    exponent = max(0.01, min(range_param, 1.99))
    return nugget + partial_sill * h ** exponent


def _model_nugget(h: float, nugget: float, partial_sill: float, range_param: float) -> float:
    if h == 0:
        return 0.0
    return nugget


_MODEL_FNS = {
    "spherical": _model_spherical,
    "exponential": _model_exponential,
    "gaussian": _model_gaussian,
    "linear": _model_linear,
    "power": _model_power,
    "nugget": _model_nugget,
}


def evaluate_model(model: VariogramModel, h: float) -> float:
    """Evaluate a fitted variogram model at distance h."""
    fn = _MODEL_FNS.get(model.model_type, _model_spherical)
    return fn(h, model.nugget, model.partial_sill, model.range_param)


# ── Experimental Variogram ───────────────────────────────────────────


def experimental_variogram(
    points: List[Tuple[float, float]],
    values: List[float],
    n_lags: int = 15,
    max_lag: Optional[float] = None,
    direction: Optional[float] = None,
    tolerance: float = 22.5,
) -> ExperimentalVariogram:
    """Compute an experimental (empirical) variogram.

    For each lag bin, computes the average semivariance:
        γ(h) = 1/(2N) Σ [z(xᵢ) - z(xⱼ)]²

    Parameters
    ----------
    points : list of (x, y)
        Spatial coordinates.
    values : list of float
        Measured values at each point.
    n_lags : int
        Number of lag bins (default 15).
    max_lag : float or None
        Maximum lag distance.  If None, uses half the maximum
        pairwise distance (standard practice).
    direction : float or None
        Direction for directional variogram (degrees, 0=East).
        None for omnidirectional (default).
    tolerance : float
        Angular tolerance in degrees for directional variograms.

    Returns
    -------
    ExperimentalVariogram
    """
    n = len(points)
    if n < 3:
        raise ValueError(f"Need at least 3 points, got {n}")
    if len(values) != n:
        raise ValueError(f"points ({n}) and values ({len(values)}) length mismatch")

    # Compute all pairwise distances
    max_dist = 0.0
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            d = _dist(points[i], points[j])
            if direction is not None:
                angle = _angle_deg(points[i], points[j])
                if not _angle_in_tolerance(angle, direction, tolerance):
                    # Also check the reverse direction (variograms are symmetric)
                    if not _angle_in_tolerance(angle, (direction + 180) % 360, tolerance):
                        continue
            sq_diff = (values[i] - values[j]) ** 2
            pairs.append((d, sq_diff))
            if d > max_dist:
                max_dist = d

    if max_dist == 0:
        raise ValueError("All points are at the same location")

    if max_lag is None:
        max_lag = max_dist / 2.0  # Standard: use half max distance

    lag_width = max_lag / n_lags
    bins: List[LagBin] = []

    for k in range(n_lags):
        lag_low = k * lag_width
        lag_high = (k + 1) * lag_width
        lag_center = (lag_low + lag_high) / 2.0

        # Collect pairs in this bin
        bin_pairs = [sq for d, sq in pairs if lag_low <= d < lag_high]

        if len(bin_pairs) >= 1:
            semivariance = sum(bin_pairs) / (2.0 * len(bin_pairs))
            bins.append(LagBin(
                lag_center=lag_center,
                semivariance=semivariance,
                pair_count=len(bin_pairs),
                lag_low=lag_low,
                lag_high=lag_high,
            ))

    return ExperimentalVariogram(
        bins=bins,
        n_points=n,
        n_pairs=len(pairs),
        max_distance=max_dist,
        lag_width=lag_width,
        direction=direction,
        tolerance=tolerance,
    )


# ── Model Fitting ────────────────────────────────────────────────────


def fit_variogram(
    ev: ExperimentalVariogram,
    model_type: str = "spherical",
) -> VariogramModel:
    """Fit a theoretical variogram model to experimental data.

    Uses a grid search + refinement approach that requires no external
    dependencies (no scipy).  Optimizes nugget, partial sill, and range
    to minimize RMSE against the experimental bins, weighted by pair
    count.

    Parameters
    ----------
    ev : ExperimentalVariogram
        The experimental variogram to fit.
    model_type : str
        One of: spherical, exponential, gaussian, linear, power, nugget.

    Returns
    -------
    VariogramModel
    """
    if model_type not in _MODEL_FNS:
        raise ValueError(f"Unknown model type '{model_type}'. "
                         f"Options: {', '.join(_MODEL_FNS.keys())}")

    if not ev.bins:
        return VariogramModel(model_type=model_type, nugget=0, sill=0,
                              range_param=0, partial_sill=0, rmse=0, r_squared=0)

    fn = _MODEL_FNS[model_type]
    lags = [b.lag_center for b in ev.bins]
    obs = [b.semivariance for b in ev.bins]
    weights = [b.pair_count for b in ev.bins]
    total_weight = sum(weights)
    if total_weight == 0:
        total_weight = 1

    max_sv = max(obs) if obs else 1.0
    max_lag = max(lags) if lags else 1.0

    # Initial estimates
    est_nugget = obs[0] if obs else 0
    est_sill = max_sv
    est_range = max_lag * 0.6

    def _rmse(nugget, partial_sill, range_param):
        ss = 0.0
        tw = 0.0
        for i, b in enumerate(ev.bins):
            predicted = fn(b.lag_center, nugget, partial_sill, range_param)
            ss += weights[i] * (b.semivariance - predicted) ** 2
            tw += weights[i]
        return math.sqrt(ss / max(tw, 1))

    # Grid search: coarse then fine
    best_rmse = float("inf")
    best_params = (est_nugget, est_sill - est_nugget, est_range)

    # Coarse grid
    nugget_candidates = [0, est_nugget * 0.5, est_nugget, est_nugget * 1.5]
    psill_candidates = [max_sv * f for f in [0.3, 0.5, 0.7, 0.9, 1.0, 1.2]]
    range_candidates = [max_lag * f for f in [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]]

    for n_cand in nugget_candidates:
        for ps_cand in psill_candidates:
            for r_cand in range_candidates:
                n_cand_pos = max(0, n_cand)
                ps_cand_pos = max(0.001, ps_cand)
                r_cand_pos = max(0.001, r_cand)
                err = _rmse(n_cand_pos, ps_cand_pos, r_cand_pos)
                if err < best_rmse:
                    best_rmse = err
                    best_params = (n_cand_pos, ps_cand_pos, r_cand_pos)

    # Fine refinement around best
    bn, bps, br = best_params
    for _ in range(3):  # 3 rounds of refinement
        step_n = max(bn * 0.1, max_sv * 0.01)
        step_ps = max(bps * 0.1, max_sv * 0.01)
        step_r = max(br * 0.1, max_lag * 0.01)

        for dn in [-step_n, 0, step_n]:
            for dps in [-step_ps, 0, step_ps]:
                for dr in [-step_r, 0, step_r]:
                    cn = max(0, bn + dn)
                    cps = max(0.001, bps + dps)
                    cr = max(0.001, br + dr)
                    err = _rmse(cn, cps, cr)
                    if err < best_rmse:
                        best_rmse = err
                        best_params = (cn, cps, cr)
        bn, bps, br = best_params

    # Compute R²
    mean_obs = sum(obs) / len(obs) if obs else 0
    ss_tot = sum(weights[i] * (obs[i] - mean_obs) ** 2 for i in range(len(obs)))
    ss_res = sum(
        weights[i] * (obs[i] - fn(lags[i], bn, bps, br)) ** 2
        for i in range(len(obs))
    )
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return VariogramModel(
        model_type=model_type,
        nugget=round(bn, 6),
        sill=round(bn + bps, 6),
        range_param=round(br, 4),
        partial_sill=round(bps, 6),
        rmse=round(best_rmse, 6),
        r_squared=round(max(0, r_squared), 6),
    )


def auto_fit(ev: ExperimentalVariogram) -> VariogramModel:
    """Try all model types and return the best fit (lowest RMSE).

    Parameters
    ----------
    ev : ExperimentalVariogram

    Returns
    -------
    VariogramModel
        Best-fitting model.
    """
    candidates = ["spherical", "exponential", "gaussian", "linear"]
    best = None
    for mt in candidates:
        model = fit_variogram(ev, model_type=mt)
        if best is None or model.rmse < best.rmse:
            best = model
    return best  # type: ignore


# ── Anisotropy Analysis ──────────────────────────────────────────────


def variogram_surface(
    points: List[Tuple[float, float]],
    values: List[float],
    n_directions: int = 8,
    n_lags: int = 12,
    tolerance: float = 22.5,
) -> VariogramSurface:
    """Compute directional variograms to detect anisotropy.

    Generates variograms at evenly spaced directions and identifies
    the major (maximum range) and minor (minimum range) axes.

    Parameters
    ----------
    points : list of (x, y)
    values : list of float
    n_directions : int
        Number of directions to sample (default 8 = every 22.5°).
    n_lags : int
    tolerance : float
        Angular tolerance per direction.

    Returns
    -------
    VariogramSurface
    """
    step = 180.0 / n_directions  # Only 0-180° due to symmetry
    angles = [i * step for i in range(n_directions)]
    variograms: Dict[float, ExperimentalVariogram] = {}

    for angle in angles:
        try:
            ev = experimental_variogram(
                points, values, n_lags=n_lags,
                direction=angle, tolerance=tolerance,
            )
            variograms[angle] = ev
        except ValueError:
            pass

    # Fit each direction and find range
    ranges: Dict[float, float] = {}
    for angle, ev in variograms.items():
        if ev.bins:
            model = auto_fit(ev)
            ranges[angle] = model.range_param

    if not ranges:
        return VariogramSurface(
            angles=angles, variograms=variograms,
            anisotropy_ratio=1.0, major_direction=0, minor_direction=90,
        )

    major_dir = max(ranges, key=ranges.get)  # type: ignore
    minor_dir = min(ranges, key=ranges.get)  # type: ignore
    max_range = ranges[major_dir]
    min_range = ranges[minor_dir]
    ratio = max_range / min_range if min_range > 0 else 1.0

    return VariogramSurface(
        angles=angles,
        variograms=variograms,
        anisotropy_ratio=round(ratio, 4),
        major_direction=major_dir,
        minor_direction=minor_dir,
    )


# ── Variogram Cloud ─────────────────────────────────────────────────


def variogram_cloud(
    points: List[Tuple[float, float]],
    values: List[float],
    max_lag: Optional[float] = None,
) -> List[Tuple[float, float]]:
    """Compute the variogram cloud (all pairwise semivariances).

    Returns every (distance, semivariance) pair without binning.
    Useful for identifying outliers or non-stationarity.

    Parameters
    ----------
    points : list of (x, y)
    values : list of float
    max_lag : float or None

    Returns
    -------
    list of (distance, semivariance)
    """
    n = len(points)
    cloud = []
    for i in range(n):
        for j in range(i + 1, n):
            d = _dist(points[i], points[j])
            if max_lag is not None and d > max_lag:
                continue
            sv = 0.5 * (values[i] - values[j]) ** 2
            cloud.append((round(d, 4), round(sv, 6)))
    return cloud


# ── Export: SVG ──────────────────────────────────────────────────────


def export_variogram_svg(
    ev: ExperimentalVariogram,
    model: Optional[VariogramModel] = None,
    output_path: str = "variogram.svg",
    width: int = 800,
    height: int = 500,
) -> str:
    """Export an SVG plot of the experimental variogram with optional model curve.

    Parameters
    ----------
    ev : ExperimentalVariogram
    model : VariogramModel or None
        If provided, draws the fitted model curve.
    output_path : str
    width, height : int

    Returns
    -------
    str
        Output file path.
    """
    if not ev.bins:
        raise ValueError("No variogram bins to plot")

    margin = {"top": 60, "right": 40, "bottom": 70, "left": 80}
    plot_w = width - margin["left"] - margin["right"]
    plot_h = height - margin["top"] - margin["bottom"]

    max_lag = max(b.lag_center for b in ev.bins) * 1.1
    max_sv = max(b.semivariance for b in ev.bins) * 1.2
    if model:
        # Extend y range if model exceeds empirical
        for i in range(50):
            h = max_lag * i / 49
            val = evaluate_model(model, h)
            if val > max_sv:
                max_sv = val * 1.1

    def sx(v):
        return margin["left"] + (v / max_lag) * plot_w

    def sy(v):
        return margin["top"] + plot_h - (v / max_sv) * plot_h

    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
        f' viewBox="0 0 {width} {height}" font-family="system-ui, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#0d1117"/>',
    ]

    # Grid lines
    n_grid_x = 5
    n_grid_y = 5
    for i in range(n_grid_x + 1):
        x = margin["left"] + i * plot_w / n_grid_x
        val = max_lag * i / n_grid_x
        svg_parts.append(
            f'<line x1="{x}" y1="{margin["top"]}" x2="{x}" '
            f'y2="{margin["top"] + plot_h}" stroke="#21262d" stroke-width="1"/>'
        )
        svg_parts.append(
            f'<text x="{x}" y="{margin["top"] + plot_h + 20}" '
            f'text-anchor="middle" fill="#8b949e" font-size="11">{val:.0f}</text>'
        )

    for i in range(n_grid_y + 1):
        y = margin["top"] + i * plot_h / n_grid_y
        val = max_sv * (1 - i / n_grid_y)
        svg_parts.append(
            f'<line x1="{margin["left"]}" y1="{y}" '
            f'x2="{margin["left"] + plot_w}" y2="{y}" '
            f'stroke="#21262d" stroke-width="1"/>'
        )
        svg_parts.append(
            f'<text x="{margin["left"] - 10}" y="{y + 4}" '
            f'text-anchor="end" fill="#8b949e" font-size="11">{val:.1f}</text>'
        )

    # Axes
    svg_parts.append(
        f'<line x1="{margin["left"]}" y1="{margin["top"] + plot_h}" '
        f'x2="{margin["left"] + plot_w}" y2="{margin["top"] + plot_h}" '
        f'stroke="#e6edf3" stroke-width="2"/>'
    )
    svg_parts.append(
        f'<line x1="{margin["left"]}" y1="{margin["top"]}" '
        f'x2="{margin["left"]}" y2="{margin["top"] + plot_h}" '
        f'stroke="#e6edf3" stroke-width="2"/>'
    )

    # Axis labels
    svg_parts.append(
        f'<text x="{width / 2}" y="{height - 10}" text-anchor="middle" '
        f'fill="#e6edf3" font-size="13">Lag Distance</text>'
    )
    svg_parts.append(
        f'<text x="15" y="{height / 2}" text-anchor="middle" '
        f'fill="#e6edf3" font-size="13" transform="rotate(-90,15,{height / 2})">'
        f'Semivariance γ(h)</text>'
    )

    # Model curve (draw first so points are on top)
    if model:
        n_curve = 100
        curve_pts = []
        for i in range(n_curve + 1):
            h = max_lag * i / n_curve
            v = evaluate_model(model, h)
            curve_pts.append(f"{sx(h):.1f},{sy(v):.1f}")
        svg_parts.append(
            f'<polyline points="{" ".join(curve_pts)}" '
            f'fill="none" stroke="#f0883e" stroke-width="2.5" opacity="0.9"/>'
        )

    # Experimental points (sized by pair count)
    max_pairs = max(b.pair_count for b in ev.bins)
    for b in ev.bins:
        cx = sx(b.lag_center)
        cy = sy(b.semivariance)
        r = 4 + 6 * (b.pair_count / max(max_pairs, 1))
        svg_parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
            f'fill="#58a6ff" opacity="0.85" stroke="#e6edf3" stroke-width="1"/>'
        )
        # Tooltip
        svg_parts.append(
            f'<title>Lag: {b.lag_center:.1f}, γ: {b.semivariance:.3f}, '
            f'N: {b.pair_count}</title>'
        )

    # Title
    title = "Experimental Variogram"
    if model:
        title += f" — {model.model_type.title()} model"
        title += f" (R²={model.r_squared:.3f})"
    direction_info = ""
    if ev.direction is not None:
        direction_info = f" [Direction: {ev.direction:.0f}°]"
    svg_parts.append(
        f'<text x="{width / 2}" y="30" text-anchor="middle" '
        f'fill="#e6edf3" font-size="16" font-weight="600">{title}{direction_info}</text>'
    )

    # Legend
    if model:
        lx = margin["left"] + plot_w - 200
        ly = margin["top"] + 20
        svg_parts.append(
            f'<rect x="{lx}" y="{ly}" width="190" height="80" '
            f'rx="6" fill="#161b22" stroke="#30363d"/>'
        )
        svg_parts.append(
            f'<text x="{lx + 10}" y="{ly + 18}" fill="#e6edf3" font-size="11">'
            f'Nugget: {model.nugget:.3f}</text>'
        )
        svg_parts.append(
            f'<text x="{lx + 10}" y="{ly + 35}" fill="#e6edf3" font-size="11">'
            f'Sill: {model.sill:.3f}</text>'
        )
        svg_parts.append(
            f'<text x="{lx + 10}" y="{ly + 52}" fill="#e6edf3" font-size="11">'
            f'Range: {model.range_param:.1f}</text>'
        )
        svg_parts.append(
            f'<text x="{lx + 10}" y="{ly + 69}" fill="#e6edf3" font-size="11">'
            f'RMSE: {model.rmse:.4f}</text>'
        )

    svg_parts.append("</svg>")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg_parts))
    return output_path


# ── Export: CSV ──────────────────────────────────────────────────────


def export_variogram_csv(
    ev: ExperimentalVariogram,
    model: Optional[VariogramModel] = None,
    output_path: str = "variogram.csv",
) -> str:
    """Export variogram data as CSV.

    Parameters
    ----------
    ev : ExperimentalVariogram
    model : VariogramModel or None
        If provided, adds a 'model_value' column.
    output_path : str

    Returns
    -------
    str
        Output file path.
    """
    headers = ["lag_center", "lag_low", "lag_high", "semivariance", "pair_count"]
    if model:
        headers.append("model_value")

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for b in ev.bins:
            row = [
                round(b.lag_center, 4),
                round(b.lag_low, 4),
                round(b.lag_high, 4),
                round(b.semivariance, 6),
                b.pair_count,
            ]
            if model:
                row.append(round(evaluate_model(model, b.lag_center), 6))
            writer.writerow(row)
    return output_path


# ── Export: JSON ─────────────────────────────────────────────────────


def export_variogram_json(
    ev: ExperimentalVariogram,
    model: Optional[VariogramModel] = None,
    output_path: str = "variogram.json",
) -> str:
    """Export variogram data as JSON.

    Parameters
    ----------
    ev : ExperimentalVariogram
    model : VariogramModel or None
    output_path : str

    Returns
    -------
    str
        Output file path.
    """
    data = {
        "n_points": ev.n_points,
        "n_pairs": ev.n_pairs,
        "max_distance": round(ev.max_distance, 4),
        "lag_width": round(ev.lag_width, 4),
        "direction": ev.direction,
        "bins": [
            {
                "lag_center": round(b.lag_center, 4),
                "semivariance": round(b.semivariance, 6),
                "pair_count": b.pair_count,
            }
            for b in ev.bins
        ],
    }
    if model:
        data["model"] = {
            "type": model.model_type,
            "nugget": model.nugget,
            "sill": model.sill,
            "range": model.range_param,
            "partial_sill": model.partial_sill,
            "rmse": model.rmse,
            "r_squared": model.r_squared,
        }

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    return output_path


# ── Text Summary ─────────────────────────────────────────────────────


def variogram_summary(
    ev: ExperimentalVariogram,
    model: Optional[VariogramModel] = None,
) -> str:
    """Generate a human-readable variogram summary.

    Parameters
    ----------
    ev : ExperimentalVariogram
    model : VariogramModel or None

    Returns
    -------
    str
    """
    lines = [
        "=" * 55,
        "  VARIOGRAM ANALYSIS",
        "=" * 55,
        f"  Points:        {ev.n_points}",
        f"  Pairs:         {ev.n_pairs}",
        f"  Max distance:  {ev.max_distance:.1f}",
        f"  Lag width:     {ev.lag_width:.1f}",
        f"  Bins:          {len(ev.bins)}",
    ]
    if ev.direction is not None:
        lines.append(f"  Direction:     {ev.direction:.0f}° (±{ev.tolerance:.0f}°)")
    else:
        lines.append("  Direction:     Omnidirectional")

    if ev.bins:
        lines.append("")
        lines.append("  LAG BINS")
        lines.append("  " + "-" * 50)
        lines.append(f"  {'Lag':>8s}  {'γ(h)':>10s}  {'N pairs':>8s}")
        lines.append("  " + "-" * 50)
        for b in ev.bins:
            lines.append(f"  {b.lag_center:8.1f}  {b.semivariance:10.4f}  {b.pair_count:8d}")

    if model:
        lines.append("")
        lines.append("  FITTED MODEL")
        lines.append("  " + "-" * 50)
        lines.append(f"  Type:          {model.model_type}")
        lines.append(f"  Nugget (c₀):   {model.nugget:.4f}")
        lines.append(f"  Sill (c₀+c₁): {model.sill:.4f}")
        lines.append(f"  Range (a):     {model.range_param:.1f}")
        lines.append(f"  RMSE:          {model.rmse:.6f}")
        lines.append(f"  R²:            {model.r_squared:.4f}")

        # Interpretation
        lines.append("")
        lines.append("  INTERPRETATION")
        lines.append("  " + "-" * 50)
        if model.nugget > 0.3 * model.sill:
            lines.append("  ⚠  High nugget — significant micro-scale")
            lines.append("     variation or measurement error")
        else:
            lines.append("  ✓  Low nugget — good measurement precision")

        if model.r_squared >= 0.8:
            lines.append(f"  ✓  Good model fit (R²={model.r_squared:.3f})")
        elif model.r_squared >= 0.5:
            lines.append(f"  ~  Moderate model fit (R²={model.r_squared:.3f})")
        else:
            lines.append(f"  ⚠  Poor model fit (R²={model.r_squared:.3f})")
            lines.append("     Try other model types or check for anisotropy")

        lines.append(f"  →  Spatial autocorrelation extends ~{model.range_param:.0f} units")
        lines.append(f"     Samples beyond this are effectively independent")

    lines.append("=" * 55)
    return "\n".join(lines)
