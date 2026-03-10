"""Trend Surface Analysis for Voronoi diagrams.

Fits polynomial regression surfaces (1st–3rd order) to spatially
distributed attribute values, decomposing each observation into a
*regional trend* (systematic large-scale pattern) and a *local residual*
(deviation from the trend).

This is a classic spatial analysis technique used to:

- Identify directional gradients (e.g. temperature decreasing northward).
- Separate large-scale structure from local anomalies.
- Assess goodness-of-fit across polynomial orders.
- Visualise predicted surfaces and residual maps.

Polynomial orders
-----------------
- **Linear (1)** — plane: ``z = a + bx + cy``
- **Quadratic (2)** — paraboloid: ``z = a + bx + cy + dx² + exy + fy²``
- **Cubic (3)** — cubic surface: 10-term polynomial.

The coefficients are estimated using ordinary least squares (OLS) via
the normal equations, with no external dependencies beyond the standard
library.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_trend import fit_trend_surface, export_trend_svg

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    result = fit_trend_surface(stats, attribute="area", order=2)
    print(result.summary_text())

    export_trend_svg(result, regions, data, "trend.svg")
    export_trend_csv(result, "trend.csv")

CLI::

    voronoimap datauni5.txt 5 --trend
    voronoimap datauni5.txt 5 --trend --trend-order 2
    voronoimap datauni5.txt 5 --trend-svg trend.svg
    voronoimap datauni5.txt 5 --trend-csv trend.csv
    voronoimap datauni5.txt 5 --trend-json trend.json
"""

from __future__ import annotations

import csv
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from vormap import validate_output_path
from vormap_geometry import (
    mean as _mean,
    std as _std,
    polygon_centroid,
    polygon_area,
)


# ── Matrix helpers (pure Python OLS) ────────────────────────────────

def _transpose(matrix):
    """Transpose a list-of-lists matrix."""
    rows = len(matrix)
    cols = len(matrix[0])
    return [[matrix[r][c] for r in range(rows)] for c in range(cols)]


def _mat_mul(a, b):
    """Multiply two matrices (list-of-lists)."""
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            s = 0.0
            for k in range(cols_a):
                s += a[i][k] * b[k][j]
            result[i][j] = s
    return result


def _mat_vec(a, v):
    """Multiply matrix by column vector, return list."""
    return [sum(a[i][j] * v[j] for j in range(len(v))) for i in range(len(a))]


def _invert(matrix):
    """Invert a square matrix using Gauss-Jordan elimination."""
    n = len(matrix)
    # Augment with identity
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)]
           for i, row in enumerate(matrix)]
    for col in range(n):
        # Partial pivoting
        max_row = col
        for row in range(col + 1, n):
            if abs(aug[row][col]) > abs(aug[max_row][col]):
                max_row = row
        aug[col], aug[max_row] = aug[max_row], aug[col]
        pivot = aug[col][col]
        if abs(pivot) < 1e-15:
            raise ValueError("Singular matrix — cannot solve OLS")
        for j in range(2 * n):
            aug[col][j] /= pivot
        for row in range(n):
            if row == col:
                continue
            factor = aug[row][col]
            for j in range(2 * n):
                aug[row][j] -= factor * aug[col][j]
    return [row[n:] for row in aug]


# ── Design matrix construction ──────────────────────────────────────

def _design_row(x, y, order):
    """Build a single row of the design matrix for given (x, y) and order."""
    if order == 1:
        return [1.0, x, y]
    elif order == 2:
        return [1.0, x, y, x * x, x * y, y * y]
    elif order == 3:
        return [1.0, x, y, x * x, x * y, y * y,
                x * x * x, x * x * y, x * y * y, y * y * y]
    else:
        raise ValueError(f"Unsupported order {order}; use 1, 2, or 3")


def _term_labels(order):
    """Human-readable labels for polynomial terms."""
    if order == 1:
        return ["intercept", "x", "y"]
    elif order == 2:
        return ["intercept", "x", "y", "x²", "xy", "y²"]
    elif order == 3:
        return ["intercept", "x", "y", "x²", "xy", "y²",
                "x³", "x²y", "xy²", "y³"]
    return []


# ── OLS fitting ─────────────────────────────────────────────────────

def _ols_fit(xs, ys, values, order):
    """Fit polynomial trend surface via ordinary least squares.

    Returns (coefficients, predicted, residuals, r_squared, adj_r_squared).
    """
    n = len(values)
    X = [_design_row(xs[i], ys[i], order) for i in range(n)]
    p = len(X[0])  # number of parameters

    Xt = _transpose(X)
    XtX = _mat_mul(Xt, X)
    XtX_inv = _invert(XtX)
    Xty = _mat_vec(Xt, values)
    coeffs = _mat_vec(XtX_inv, Xty)

    predicted = [sum(X[i][j] * coeffs[j] for j in range(p)) for i in range(n)]
    residuals = [values[i] - predicted[i] for i in range(n)]

    mean_y = sum(values) / n
    ss_tot = sum((v - mean_y) ** 2 for v in values)
    ss_res = sum(r * r for r in residuals)

    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    if n > p:
        adj_r_squared = 1.0 - (1.0 - r_squared) * (n - 1) / (n - p)
    else:
        adj_r_squared = r_squared

    return coeffs, predicted, residuals, r_squared, adj_r_squared


# ── Result dataclass ────────────────────────────────────────────────

@dataclass
class TrendResult:
    """Result of a trend surface analysis."""
    order: int
    attribute: str
    n_points: int
    coefficients: list
    term_labels: list
    r_squared: float
    adj_r_squared: float
    centroids: list
    observed: list
    predicted: list
    residuals: list
    residual_mean: float
    residual_std: float
    bounds: tuple  # (xmin, xmax, ymin, ymax)

    def summary_text(self) -> str:
        """Human-readable summary."""
        lines = [
            f"Trend Surface Analysis — Order {self.order} ({['', 'Linear', 'Quadratic', 'Cubic'][self.order]})",
            f"Attribute: {self.attribute}",
            f"Points: {self.n_points}",
            "",
            "Coefficients:",
        ]
        for label, c in zip(self.term_labels, self.coefficients):
            lines.append(f"  {label:>12s}: {c:+.6f}")
        lines.append("")
        lines.append(f"R²:          {self.r_squared:.4f}")
        lines.append(f"Adjusted R²: {self.adj_r_squared:.4f}")
        lines.append(f"Residual μ:  {self.residual_mean:.4f}")
        lines.append(f"Residual σ:  {self.residual_std:.4f}")

        # Classify fit quality
        if self.r_squared >= 0.8:
            quality = "Strong trend detected"
        elif self.r_squared >= 0.5:
            quality = "Moderate trend"
        elif self.r_squared >= 0.2:
            quality = "Weak trend"
        else:
            quality = "No significant trend"
        lines.append(f"Assessment:  {quality}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialisable dictionary."""
        return {
            "order": self.order,
            "attribute": self.attribute,
            "n_points": self.n_points,
            "coefficients": {l: c for l, c in zip(self.term_labels, self.coefficients)},
            "r_squared": round(self.r_squared, 6),
            "adj_r_squared": round(self.adj_r_squared, 6),
            "residual_mean": round(self.residual_mean, 6),
            "residual_std": round(self.residual_std, 6),
            "assessment": self.summary_text().split("\n")[-1].split(": ", 1)[-1],
            "points": [
                {
                    "x": round(self.centroids[i][0], 4),
                    "y": round(self.centroids[i][1], 4),
                    "observed": round(self.observed[i], 6),
                    "predicted": round(self.predicted[i], 6),
                    "residual": round(self.residuals[i], 6),
                }
                for i in range(self.n_points)
            ],
        }


@dataclass
class TrendComparison:
    """Compare multiple polynomial orders."""
    results: list  # list of TrendResult
    best_order: int
    aic_values: list  # (order, AIC) pairs

    def summary_text(self) -> str:
        lines = ["Trend Surface Comparison", "=" * 50, ""]
        lines.append(f"{'Order':<8} {'R²':<10} {'Adj R²':<10} {'AIC':<12} {'Assessment'}")
        lines.append("-" * 60)
        for r in self.results:
            aic = next(a for o, a in self.aic_values if o == r.order)
            quality = r.summary_text().split("\n")[-1].split(": ", 1)[-1]
            lines.append(f"{r.order:<8} {r.r_squared:<10.4f} {r.adj_r_squared:<10.4f} {aic:<12.2f} {quality}")
        lines.append("")
        lines.append(f"Best order (lowest AIC): {self.best_order}")
        return "\n".join(lines)


# ── Public API ──────────────────────────────────────────────────────

def _extract_data(stats, attribute):
    """Extract centroids and attribute values from region stats."""
    centroids = []
    values = []
    for s in stats:
        verts = s.get("vertices", [])
        if not verts or len(verts) < 3:
            continue
        cx, cy = polygon_centroid(verts)
        val = s.get(attribute)
        if val is None:
            continue
        centroids.append((cx, cy))
        values.append(float(val))
    if len(centroids) < 4:
        raise ValueError(f"Need at least 4 regions with attribute '{attribute}', got {len(centroids)}")
    return centroids, values


def fit_trend_surface(stats, attribute="area", order=2):
    """Fit a polynomial trend surface to region attributes.

    Parameters
    ----------
    stats : list[dict]
        Region statistics from ``vormap_viz.compute_region_stats``.
    attribute : str
        Attribute name to model (e.g. ``"area"``, ``"compactness"``).
    order : int
        Polynomial order: 1 (linear), 2 (quadratic), or 3 (cubic).

    Returns
    -------
    TrendResult
        Fitted model with predicted values and residuals.
    """
    if order not in (1, 2, 3):
        raise ValueError(f"Order must be 1, 2, or 3; got {order}")

    centroids, values = _extract_data(stats, attribute)
    xs = [c[0] for c in centroids]
    ys = [c[1] for c in centroids]

    # Normalize coordinates for numerical stability
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    x_range = x_max - x_min or 1.0
    y_range = y_max - y_min or 1.0
    xs_norm = [(x - x_min) / x_range for x in xs]
    ys_norm = [(y - y_min) / y_range for y in ys]

    coeffs, predicted, residuals, r2, adj_r2 = _ols_fit(xs_norm, ys_norm, values, order)

    res_mean = _mean(residuals)
    res_std = _std(residuals) if len(residuals) > 1 else 0.0

    return TrendResult(
        order=order,
        attribute=attribute,
        n_points=len(values),
        coefficients=coeffs,
        term_labels=_term_labels(order),
        r_squared=r2,
        adj_r_squared=adj_r2,
        centroids=centroids,
        observed=values,
        predicted=predicted,
        residuals=residuals,
        residual_mean=res_mean,
        residual_std=res_std,
        bounds=(x_min, x_max, y_min, y_max),
    )


def compare_trends(stats, attribute="area", orders=None):
    """Compare trend surfaces across polynomial orders.

    Uses Akaike Information Criterion (AIC) to select the best order.

    Parameters
    ----------
    stats : list[dict]
        Region statistics.
    attribute : str
        Attribute name.
    orders : list[int] | None
        Orders to compare. Defaults to ``[1, 2, 3]``.

    Returns
    -------
    TrendComparison
    """
    if orders is None:
        orders = [1, 2, 3]

    results = [fit_trend_surface(stats, attribute, o) for o in orders]

    # AIC = n * ln(SS_res / n) + 2 * p
    aic_values = []
    for r in results:
        n = r.n_points
        ss_res = sum(res * res for res in r.residuals)
        p = len(r.coefficients)
        if ss_res > 0:
            aic = n * math.log(ss_res / n) + 2 * p
        else:
            aic = float("-inf")
        aic_values.append((r.order, aic))

    best_order = min(aic_values, key=lambda x: x[1])[0]
    return TrendComparison(results=results, best_order=best_order, aic_values=aic_values)


def predict_at(result, x, y):
    """Predict the trend value at an arbitrary (x, y) location.

    Parameters
    ----------
    result : TrendResult
        A fitted trend surface.
    x, y : float
        Query coordinates.

    Returns
    -------
    float
        Predicted value from the trend surface.
    """
    xmin, xmax, ymin, ymax = result.bounds
    x_range = (xmax - xmin) or 1.0
    y_range = (ymax - ymin) or 1.0
    xn = (x - xmin) / x_range
    yn = (y - ymin) / y_range
    row = _design_row(xn, yn, result.order)
    return sum(row[j] * result.coefficients[j] for j in range(len(row)))


def predict_grid(result, nx=50, ny=50, bounds=None):
    """Generate a grid of predicted trend values.

    Parameters
    ----------
    result : TrendResult
        Fitted trend surface.
    nx, ny : int
        Grid resolution.
    bounds : tuple | None
        ``(xmin, xmax, ymin, ymax)``. Defaults to data bounds.

    Returns
    -------
    dict
        ``{"grid": list[list[float]], "x_vals": list, "y_vals": list,
          "bounds": tuple}``.
    """
    if bounds is None:
        bounds = result.bounds
    bx0, bx1, by0, by1 = bounds
    x_vals = [bx0 + (bx1 - bx0) * i / max(nx - 1, 1) for i in range(nx)]
    y_vals = [by0 + (by1 - by0) * j / max(ny - 1, 1) for j in range(ny)]

    grid = []
    for j in range(ny):
        row = []
        for i in range(nx):
            row.append(predict_at(result, x_vals[i], y_vals[j]))
        grid.append(row)
    return {"grid": grid, "x_vals": x_vals, "y_vals": y_vals, "bounds": bounds}


# ── SVG export ──────────────────────────────────────────────────────

def _value_to_color(val, vmin, vmax):
    """Map value to a blue–white–red diverging colour."""
    if vmax == vmin:
        return "#ffffff"
    t = (val - vmin) / (vmax - vmin)
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        s = t * 2
        r = int(66 + s * (255 - 66))
        g = int(133 + s * (255 - 133))
        b = int(244 + s * (255 - 244))
    else:
        s = (t - 0.5) * 2
        r = int(255 - s * (255 - 239))
        g = int(255 - s * (255 - 68))
        b = int(255 - s * (255 - 68))
    return f"#{r:02x}{g:02x}{b:02x}"


def export_trend_svg(result, regions, data, path, width=800, height=600,
                     show="predicted", grid_res=40):
    """Export trend surface or residuals as SVG.

    Parameters
    ----------
    result : TrendResult
        Fitted trend surface.
    regions : list
        Voronoi regions from ``vormap_viz.compute_regions``.
    data : dict
        Loaded data from ``vormap.load_data``.
    path : str
        Output SVG path.
    show : str
        ``"predicted"`` for trend surface, ``"residuals"`` for residual map.
    grid_res : int
        Grid resolution for surface heatmap.
    """
    validate_output_path(path)

    # Data bounds with margin
    pts = data.get("points", [])
    if not pts:
        raise ValueError("No points in data")
    all_x = [p[0] for p in pts]
    all_y = [p[1] for p in pts]
    dx = max(all_x) - min(all_x) or 1
    dy = max(all_y) - min(all_y) or 1
    margin = max(dx, dy) * 0.05
    vb_x = min(all_x) - margin
    vb_y = min(all_y) - margin
    vb_w = dx + 2 * margin
    vb_h = dy + 2 * margin

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height),
                     viewBox=f"{vb_x:.2f} {vb_y:.2f} {vb_w:.2f} {vb_h:.2f}")

    # Background
    ET.SubElement(svg, "rect", x=str(vb_x), y=str(vb_y),
                  width=str(vb_w), height=str(vb_h), fill="#f8f9fa")

    if show == "predicted":
        # Render grid heatmap
        gdata = predict_grid(result, nx=grid_res, ny=grid_res)
        flat = [v for row in gdata["grid"] for v in row]
        vmin, vmax = min(flat), max(flat)
        cell_w = vb_w / grid_res
        cell_h = vb_h / grid_res
        for j, row in enumerate(gdata["grid"]):
            for i, val in enumerate(row):
                color = _value_to_color(val, vmin, vmax)
                ET.SubElement(svg, "rect",
                              x=f"{vb_x + i * cell_w:.2f}",
                              y=f"{vb_y + j * cell_h:.2f}",
                              width=f"{cell_w + 0.5:.2f}",
                              height=f"{cell_h + 0.5:.2f}",
                              fill=color, opacity="0.7")
    else:
        # Residual map — colour regions by residual
        if result.residuals:
            abs_max = max(abs(r) for r in result.residuals)
        else:
            abs_max = 1.0
        for idx, reg in enumerate(regions):
            if idx >= len(result.residuals):
                break
            verts = reg.get("vertices", [])
            if len(verts) < 3:
                continue
            res = result.residuals[idx]
            color = _value_to_color(res, -abs_max, abs_max)
            pts_str = " ".join(f"{v[0]:.2f},{v[1]:.2f}" for v in verts)
            ET.SubElement(svg, "polygon", points=pts_str,
                          fill=color, stroke="#666", **{"stroke-width": "0.3"})

    # Overlay seed points
    r_size = max(vb_w, vb_h) * 0.005
    for i, (cx, cy) in enumerate(result.centroids):
        ET.SubElement(svg, "circle", cx=f"{cx:.2f}", cy=f"{cy:.2f}",
                      r=f"{r_size:.2f}", fill="#333", opacity="0.6")

    # Title
    title = f"Trend Surface (Order {result.order}) — {show.title()}"
    ET.SubElement(svg, "text", x=f"{vb_x + vb_w * 0.02:.2f}",
                  y=f"{vb_y + vb_h * 0.05:.2f}",
                  fill="#333", **{"font-size": f"{vb_h * 0.03:.1f}",
                                  "font-family": "sans-serif"}).text = title

    # R² annotation
    r2_text = f"R² = {result.r_squared:.4f}"
    ET.SubElement(svg, "text", x=f"{vb_x + vb_w * 0.02:.2f}",
                  y=f"{vb_y + vb_h * 0.09:.2f}",
                  fill="#666", **{"font-size": f"{vb_h * 0.025:.1f}",
                                  "font-family": "sans-serif"}).text = r2_text

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(path, xml_declaration=True, encoding="unicode")


def export_trend_csv(result, path):
    """Export trend analysis results to CSV.

    Parameters
    ----------
    result : TrendResult
        Fitted trend surface.
    path : str
        Output CSV path.
    """
    validate_output_path(path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["x", "y", "observed", "predicted", "residual"])
        for i in range(result.n_points):
            writer.writerow([
                round(result.centroids[i][0], 4),
                round(result.centroids[i][1], 4),
                round(result.observed[i], 6),
                round(result.predicted[i], 6),
                round(result.residuals[i], 6),
            ])


def export_trend_json(result, path):
    """Export trend analysis results to JSON.

    Parameters
    ----------
    result : TrendResult
        Fitted trend surface.
    path : str
        Output JSON path.
    """
    validate_output_path(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2)
