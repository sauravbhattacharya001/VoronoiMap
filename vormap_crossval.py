"""Leave-one-out cross-validation for spatial interpolation methods.

Evaluates interpolation accuracy by withholding each point in turn,
predicting its value from the remaining points, and computing error
metrics.  Helps users decide which interpolation method (nearest, IDW,
natural neighbor) works best for their dataset.

Metrics computed:
- **MAE** — Mean Absolute Error
- **RMSE** — Root Mean Squared Error
- **R²** — Coefficient of determination
- **Max Error** — Worst-case absolute error
- **Per-point residuals** — For spatial error analysis

Usage (Python API)::

    from vormap_crossval import cross_validate, compare_methods

    points = [(100, 200), (300, 400), (500, 100), (200, 300)]
    values = [25.0, 30.0, 22.0, 28.0]

    # Single-method LOO cross-validation
    result = cross_validate(points, values, method='idw')
    print(f"IDW  — MAE: {result.mae:.3f}, RMSE: {result.rmse:.3f}, "
          f"R²: {result.r_squared:.3f}")

    # Compare all methods at once
    comparison = compare_methods(points, values)
    for r in comparison:
        print(f"{r.method:8s}  MAE={r.mae:.3f}  RMSE={r.rmse:.3f}  "
              f"R²={r.r_squared:.3f}")

    # Export comparison report
    from vormap_crossval import export_crossval_csv, export_crossval_svg
    export_crossval_csv(comparison, "crossval_results.csv")
    export_crossval_svg(comparison, "crossval_chart.svg")

CLI::

    voronoimap data.txt 5 --crossval --interp-values values.txt
    voronoimap data.txt 5 --crossval --interp-values values.txt --crossval-method idw
    voronoimap data.txt 5 --crossval-csv results.csv --interp-values values.txt
    voronoimap data.txt 5 --crossval-svg chart.svg --interp-values values.txt
"""

from __future__ import annotations

import csv
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from vormap import validate_output_path

from vormap_interp import nearest_interp, idw_interp, natural_neighbor_interp


# ── Result containers ───────────────────────────────────────────────

@dataclass
class CrossValResult:
    """Results from leave-one-out cross-validation of one method.

    Attributes
    ----------
    method : str
        Interpolation method name ('nearest', 'idw', 'natural').
    n : int
        Number of points evaluated.
    mae : float
        Mean Absolute Error.
    rmse : float
        Root Mean Squared Error.
    r_squared : float
        Coefficient of determination (R²).  1.0 = perfect, 0.0 = mean
        model, negative = worse than predicting the mean.
    max_error : float
        Largest absolute error across all points.
    residuals : list of dict
        Per-point residuals with keys: x, y, observed, predicted, error.
    elapsed_ms : float
        Wall-clock time in milliseconds.
    """

    method: str = ''
    n: int = 0
    mae: float = 0.0
    rmse: float = 0.0
    r_squared: float = 0.0
    max_error: float = 0.0
    residuals: list = field(default_factory=list)
    elapsed_ms: float = 0.0

    def summary(self) -> str:
        """One-line summary string."""
        return (f"{self.method:8s}  n={self.n}  MAE={self.mae:.4f}  "
                f"RMSE={self.rmse:.4f}  R²={self.r_squared:.4f}  "
                f"MaxErr={self.max_error:.4f}  ({self.elapsed_ms:.0f}ms)")


# ── Core cross-validation ──────────────────────────────────────────

def _get_interp_fn(method: str, points, values, power: float = 2.0):
    """Return an interpolation function for the given method."""
    fns = {
        'nearest': lambda q: nearest_interp(points, values, q),
        'idw': lambda q: idw_interp(points, values, q, power=power),
        'natural': lambda q: natural_neighbor_interp(points, values, q),
    }
    fn = fns.get(method)
    if fn is None:
        raise ValueError(f"Unknown method '{method}'. "
                         f"Choose from: {', '.join(fns)}")
    return fn


def cross_validate(
    points: List[Tuple[float, float]],
    values: List[float],
    method: str = 'idw',
    power: float = 2.0,
) -> CrossValResult:
    """Leave-one-out cross-validation for a single interpolation method.

    Withholds each point in turn, interpolates its value from the
    remaining n-1 points, and computes prediction error metrics.

    Parameters
    ----------
    points : list of (x, y)
        Spatial coordinates of known data points.
    values : list of float
        Observed values at each point.
    method : str
        Interpolation method: 'nearest', 'idw', or 'natural'.
    power : float
        IDW power parameter (only used when method='idw').

    Returns
    -------
    CrossValResult
        Aggregated error metrics and per-point residuals.
    """
    import time

    if len(points) != len(values):
        raise ValueError("points and values must have the same length")
    n = len(points)
    if n < 3:
        raise ValueError("Cross-validation requires at least 3 points")

    # Natural neighbor needs ≥ 3 remaining points
    min_leave = 4 if method == 'natural' else 2
    if n < min_leave:
        raise ValueError(
            f"Method '{method}' requires at least {min_leave} points "
            f"for LOO cross-validation (got {n})")

    t0 = time.perf_counter()

    residuals = []
    errors = []
    abs_errors = []

    for i in range(n):
        # Leave out point i
        pts_loo = points[:i] + points[i + 1:]
        vals_loo = values[:i] + values[i + 1:]

        # Predict
        interp_fn = _get_interp_fn(method, pts_loo, vals_loo, power=power)
        try:
            predicted = interp_fn(points[i])
        except Exception:
            # Skip points where interpolation fails (edge cases)
            continue

        observed = values[i]
        error = predicted - observed

        residuals.append({
            'x': points[i][0],
            'y': points[i][1],
            'observed': observed,
            'predicted': predicted,
            'error': error,
        })
        errors.append(error)
        abs_errors.append(abs(error))

    elapsed = (time.perf_counter() - t0) * 1000

    if not errors:
        return CrossValResult(method=method, n=0, elapsed_ms=elapsed)

    evaluated = len(errors)
    mae = sum(abs_errors) / evaluated
    rmse = math.sqrt(sum(e * e for e in errors) / evaluated)
    max_error = max(abs_errors)

    # R² — coefficient of determination
    mean_obs = sum(r['observed'] for r in residuals) / evaluated
    ss_res = sum(e * e for e in errors)
    ss_tot = sum((r['observed'] - mean_obs) ** 2 for r in residuals)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-15 else 0.0

    return CrossValResult(
        method=method,
        n=evaluated,
        mae=mae,
        rmse=rmse,
        r_squared=r_squared,
        max_error=max_error,
        residuals=residuals,
        elapsed_ms=elapsed,
    )


def compare_methods(
    points: List[Tuple[float, float]],
    values: List[float],
    methods: Optional[List[str]] = None,
    power: float = 2.0,
) -> List[CrossValResult]:
    """Run LOO cross-validation for multiple methods and rank by RMSE.

    Parameters
    ----------
    points, values : see ``cross_validate``.
    methods : list of str or None
        Methods to compare.  Defaults to all three:
        ``['nearest', 'idw', 'natural']``.
    power : float
        IDW power parameter.

    Returns
    -------
    list of CrossValResult
        Sorted by RMSE (best first).
    """
    if methods is None:
        methods = ['nearest', 'idw', 'natural']

    results = []
    for m in methods:
        try:
            r = cross_validate(points, values, method=m, power=power)
            results.append(r)
        except ValueError:
            pass  # Skip methods that can't run (e.g. natural with < 4 pts)

    results.sort(key=lambda r: r.rmse)
    return results


# ── Export ──────────────────────────────────────────────────────────

def export_crossval_csv(
    results: List[CrossValResult],
    output_path: str,
    include_residuals: bool = False,
) -> None:
    """Export cross-validation results to CSV.

    Parameters
    ----------
    results : list of CrossValResult
    output_path : str
        Path for the CSV file.
    include_residuals : bool
        If True, includes per-point residual rows after the summary.
    """
    output_path = validate_output_path(output_path, allow_absolute=True)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # Summary section
        writer.writerow(['method', 'n', 'mae', 'rmse', 'r_squared',
                         'max_error', 'elapsed_ms'])
        for r in results:
            writer.writerow([r.method, r.n, f'{r.mae:.6f}',
                             f'{r.rmse:.6f}', f'{r.r_squared:.6f}',
                             f'{r.max_error:.6f}', f'{r.elapsed_ms:.1f}'])

        if include_residuals:
            writer.writerow([])
            writer.writerow(['--- Residuals ---'])
            writer.writerow(['method', 'x', 'y', 'observed', 'predicted',
                             'error'])
            for r in results:
                for res in r.residuals:
                    writer.writerow([r.method, f'{res["x"]:.4f}',
                                     f'{res["y"]:.4f}',
                                     f'{res["observed"]:.6f}',
                                     f'{res["predicted"]:.6f}',
                                     f'{res["error"]:.6f}'])


def export_crossval_svg(
    results: List[CrossValResult],
    output_path: str,
    width: int = 600,
    height: int = 400,
    title: str = 'Interpolation Cross-Validation',
) -> None:
    """Export a bar chart comparing interpolation methods as SVG.

    Shows MAE, RMSE, and R² side by side for each method, making it
    easy to visually compare accuracy.

    Parameters
    ----------
    results : list of CrossValResult
    output_path : str
        Path for the SVG file.
    width, height : int
        SVG canvas dimensions.
    title : str
        Chart title.
    """
    output_path = validate_output_path(output_path, allow_absolute=True)

    if not results:
        raise ValueError("No results to chart")

    # Layout constants
    margin_left = 80
    margin_right = 30
    margin_top = 60
    margin_bottom = 70
    chart_w = width - margin_left - margin_right
    chart_h = height - margin_top - margin_bottom

    # Metrics to chart (RMSE bar chart + R² line overlay)
    metrics = ['MAE', 'RMSE']
    colors = {'MAE': '#4e79a7', 'RMSE': '#e15759'}

    # Find max value for scaling
    max_val = max(
        max(r.mae for r in results),
        max(r.rmse for r in results),
    )
    if max_val < 1e-15:
        max_val = 1.0
    # Add 10% headroom
    max_val *= 1.1

    n_methods = len(results)
    n_metrics = len(metrics)
    group_w = chart_w / max(n_methods, 1)
    bar_w = group_w / (n_metrics + 1)  # +1 for spacing

    svg = ET.Element('svg', xmlns='http://www.w3.org/2000/svg',
                     width=str(width), height=str(height))

    # Background
    ET.SubElement(svg, 'rect', width=str(width), height=str(height),
                  fill='#ffffff', rx='4')

    # Title
    t = ET.SubElement(svg, 'text', x=str(width // 2), y='30',
                      fill='#333')
    t.set('text-anchor', 'middle')
    t.set('font-family', 'Arial, sans-serif')
    t.set('font-size', '16')
    t.set('font-weight', 'bold')
    t.text = title

    # Y-axis gridlines and labels
    n_ticks = 5
    for i in range(n_ticks + 1):
        val = max_val * i / n_ticks
        y = margin_top + chart_h - (chart_h * i / n_ticks)
        # Gridline
        ET.SubElement(svg, 'line',
                      x1=str(margin_left), y1=str(int(y)),
                      x2=str(margin_left + chart_w), y2=str(int(y)),
                      stroke='#e0e0e0')
        # Label
        lab = ET.SubElement(svg, 'text',
                            x=str(margin_left - 10), y=str(int(y) + 4),
                            fill='#666')
        lab.set('text-anchor', 'end')
        lab.set('font-family', 'Arial, sans-serif')
        lab.set('font-size', '11')
        lab.text = f'{val:.2f}'

    # Bars
    for gi, r in enumerate(results):
        group_x = margin_left + gi * group_w
        metric_vals = {'MAE': r.mae, 'RMSE': r.rmse}

        for mi, metric in enumerate(metrics):
            val = metric_vals[metric]
            bar_h = (val / max_val) * chart_h
            bx = group_x + (mi + 0.5) * bar_w
            by = margin_top + chart_h - bar_h

            ET.SubElement(svg, 'rect',
                          x=str(int(bx)), y=str(int(by)),
                          width=str(int(bar_w * 0.8)),
                          height=str(int(bar_h)),
                          fill=colors[metric], rx='2', opacity='0.85')

            # Value label on bar
            vlab = ET.SubElement(svg, 'text',
                                 x=str(int(bx + bar_w * 0.4)),
                                 y=str(int(by - 4)),
                                 fill=colors[metric])
            vlab.set('text-anchor', 'middle')
            vlab.set('font-family', 'Arial, sans-serif')
            vlab.set('font-size', '10')
            vlab.text = f'{val:.3f}'

        # Method label
        mlab = ET.SubElement(svg, 'text',
                             x=str(int(group_x + group_w / 2)),
                             y=str(margin_top + chart_h + 20),
                             fill='#333')
        mlab.set('text-anchor', 'middle')
        mlab.set('font-family', 'Arial, sans-serif')
        mlab.set('font-size', '12')
        mlab.set('font-weight', 'bold')
        mlab.text = r.method

        # R² below method name
        r2lab = ET.SubElement(svg, 'text',
                              x=str(int(group_x + group_w / 2)),
                              y=str(margin_top + chart_h + 36),
                              fill='#59a14f')
        r2lab.set('text-anchor', 'middle')
        r2lab.set('font-family', 'Arial, sans-serif')
        r2lab.set('font-size', '11')
        r2lab.text = f'R²={r.r_squared:.3f}'

    # Legend
    legend_x = margin_left + 10
    legend_y = margin_top + 10
    for i, metric in enumerate(metrics):
        ET.SubElement(svg, 'rect',
                      x=str(legend_x), y=str(legend_y + i * 18),
                      width='12', height='12',
                      fill=colors[metric], rx='2')
        lt = ET.SubElement(svg, 'text',
                           x=str(legend_x + 18),
                           y=str(legend_y + i * 18 + 10),
                           fill='#333')
        lt.set('font-family', 'Arial, sans-serif')
        lt.set('font-size', '11')
        lt.text = metric

    # Axes
    ET.SubElement(svg, 'line',
                  x1=str(margin_left), y1=str(margin_top),
                  x2=str(margin_left), y2=str(margin_top + chart_h),
                  stroke='#333')
    ET.SubElement(svg, 'line',
                  x1=str(margin_left), y1=str(margin_top + chart_h),
                  x2=str(margin_left + chart_w),
                  y2=str(margin_top + chart_h),
                  stroke='#333')

    tree = ET.ElementTree(svg)
    ET.indent(tree, space='  ')
    tree.write(output_path, xml_declaration=True, encoding='unicode')


# ── CLI integration ─────────────────────────────────────────────────

def run_crossval_cli(args, data) -> None:
    """Execute cross-validation commands from CLI args.

    Expects ``args`` to have:
    - interp_values: path to values file (one per line, matching data)
    - crossval: bool flag
    - crossval_method: str or None (None = compare all)
    - crossval_csv: output CSV path or None
    - crossval_svg: output SVG path or None
    - interp_power: float (IDW power)
    """
    from vormap import validate_input_path

    interp_path = validate_input_path(args.interp_values, allow_absolute=True)
    with open(interp_path, 'r', encoding='utf-8') as f:
        raw = [line.strip() for line in f if line.strip()]
    values = [float(v) for v in raw]

    if len(values) != len(data):
        raise ValueError(
            f"Number of values ({len(values)}) != "
            f"seed points ({len(data)})")

    points = [(d['x'], d['y']) if isinstance(d, dict) else d for d in data]
    power = getattr(args, 'interp_power', 2.0)
    method = getattr(args, 'crossval_method', None)

    if method:
        results = [cross_validate(points, values, method=method, power=power)]
    else:
        results = compare_methods(points, values, power=power)

    # Print results
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║        Interpolation Cross-Validation (LOO)             ║")
    print("╠══════════════════════════════════════════════════════════╣")
    for r in results:
        print(f"║  {r.summary()}  ║")
    print("╚══════════════════════════════════════════════════════════╝")

    if results:
        best = results[0]
        print(f"\n  ★ Best method: {best.method} "
              f"(RMSE={best.rmse:.4f}, R²={best.r_squared:.4f})")

    # Export
    csv_path = getattr(args, 'crossval_csv', None)
    if csv_path:
        export_crossval_csv(results, csv_path, include_residuals=True)
        print(f"  CSV saved to {csv_path}")

    svg_path = getattr(args, 'crossval_svg', None)
    if svg_path:
        export_crossval_svg(results, svg_path)
        print(f"  SVG chart saved to {svg_path}")
