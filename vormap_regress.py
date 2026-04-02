"""Spatial Regression Analysis for Voronoi diagrams.

Fits regression models to Voronoi cell attributes, with spatial
diagnostics that reveal whether standard OLS assumptions hold in
the presence of spatial structure.

Two model types are supported:

- **OLS** — Ordinary Least Squares with spatial diagnostics: Moran's I
  test on residuals (detects spatial autocorrelation), Cook's distance
  (identifies influential observations), leverage/hat values, VIF
  (multicollinearity), and heteroscedasticity tests.
- **GWR** — Geographically Weighted Regression.  Fits a separate
  weighted regression at each observation location using a distance-decay
  kernel (gaussian or bisquare).  Reveals how relationships vary across
  space — coefficient surfaces show where predictors matter more or less.

This differs from ``vormap_trend`` (polynomial trend surfaces) by
supporting arbitrary predictor variables and full diagnostic suites.

Usage (Python API)::

    import vormap, vormap_viz
    from vormap_regress import fit_ols, fit_gwr, export_regress_svg

    data = vormap.load_data("datauni5.txt")
    regions = vormap_viz.compute_regions(data)
    stats = vormap_viz.compute_region_stats(regions, data)

    # OLS: predict area from compactness and vertex_count
    ols = fit_ols(stats, y="area", x=["compactness", "vertex_count"])
    print(ols.summary_text())

    # GWR: spatially varying coefficients
    gwr = fit_gwr(stats, y="area", x=["compactness"], bandwidth=200.0)
    print(gwr.summary_text())

    export_regress_svg(ols, regions, data, "regression.svg")

CLI::

    voronoimap datauni5.txt 5 --regress area~compactness+vertex_count
    voronoimap datauni5.txt 5 --regress-gwr area~compactness --bandwidth 200
    voronoimap datauni5.txt 5 --regress-svg regress.svg
    voronoimap datauni5.txt 5 --regress-json regress.json
    voronoimap datauni5.txt 5 --regress-csv regress.csv
"""

import csv
import json
import math
import xml.etree.ElementTree as ET

from vormap import validate_output_path


# ── Matrix helpers (pure Python, no numpy) ──────────────────────────


def _transpose(m):
    """Transpose a list-of-lists matrix."""
    if not m:
        return []
    return [[m[r][c] for r in range(len(m))] for c in range(len(m[0]))]


def _mat_mul(a, b):
    """Multiply two matrices (list-of-lists)."""
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for k in range(cols_a):
            aik = a[i][k]
            for j in range(cols_b):
                result[i][j] += aik * b[k][j]
    return result


def _mat_vec(m, v):
    """Multiply matrix by column vector, return vector."""
    return [sum(m[i][j] * v[j] for j in range(len(v))) for i in range(len(m))]


def _identity(n):
    """Return n×n identity matrix."""
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def _lu_decompose(matrix):
    """LU decomposition with partial pivoting.

    Returns (L, U, perm) where perm is the row permutation vector.
    """
    n = len(matrix)
    # Copy
    U = [row[:] for row in matrix]
    L = _identity(n)
    perm = list(range(n))

    for col in range(n):
        # Partial pivot
        max_val = abs(U[col][col])
        max_row = col
        for row in range(col + 1, n):
            if abs(U[row][col]) > max_val:
                max_val = abs(U[row][col])
                max_row = row
        if max_row != col:
            U[col], U[max_row] = U[max_row], U[col]
            perm[col], perm[max_row] = perm[max_row], perm[col]
            for k in range(col):
                L[col][k], L[max_row][k] = L[max_row][k], L[col][k]

        if abs(U[col][col]) < 1e-14:
            continue

        for row in range(col + 1, n):
            factor = U[row][col] / U[col][col]
            L[row][col] = factor
            for k in range(col, n):
                U[row][k] -= factor * U[col][k]

    return L, U, perm


def _lu_solve(L, U, perm, b):
    """Solve Ax = b given LU decomposition."""
    n = len(b)
    # Permute b
    pb = [b[perm[i]] for i in range(n)]

    # Forward substitution: Ly = pb
    y = [0.0] * n
    for i in range(n):
        y[i] = pb[i] - sum(L[i][j] * y[j] for j in range(i))

    # Back substitution: Ux = y
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(U[i][i]) < 1e-14:
            x[i] = 0.0
        else:
            x[i] = (y[i] - sum(U[i][j] * x[j] for j in range(i + 1, n))) / U[i][i]

    return x


def _solve(A, b):
    """Solve Ax = b via LU decomposition."""
    L, U, perm = _lu_decompose(A)
    return _lu_solve(L, U, perm, b)


def _invert(A):
    """Invert a square matrix via LU decomposition."""
    n = len(A)
    L, U, perm = _lu_decompose(A)
    inv = []
    for col in range(n):
        e = [1.0 if i == col else 0.0 for i in range(n)]
        inv.append(_lu_solve(L, U, perm, e))
    # inv is list of column vectors; transpose to get matrix
    return _transpose(inv)


# ── Statistics helpers ───────────────────────────────────────────────


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _var(vals, ddof=1):
    n = len(vals)
    if n <= ddof:
        return 0.0
    m = _mean(vals)
    return sum((v - m) ** 2 for v in vals) / (n - ddof)


def _std(vals, ddof=1):
    return math.sqrt(_var(vals, ddof))


def _dot(a, b):
    return sum(ai * bi for ai, bi in zip(a, b))


# ── OLS Result ───────────────────────────────────────────────────────


class OLSResult:
    """Container for OLS regression results with spatial diagnostics."""

    def __init__(self, *, y_name, x_names, coefficients, std_errors,
                 t_values, p_values, r_squared, adj_r_squared, f_stat,
                 f_p_value, residuals, fitted, hat_diag, cooks_d,
                 vif, moran_i, moran_expected, moran_z, moran_p,
                 n, k, sse, mse, aic, bic, y_values, x_matrix,
                 seed_coords):
        self.y_name = y_name
        self.x_names = ["intercept"] + list(x_names)
        self.coefficients = coefficients
        self.std_errors = std_errors
        self.t_values = t_values
        self.p_values = p_values
        self.r_squared = r_squared
        self.adj_r_squared = adj_r_squared
        self.f_stat = f_stat
        self.f_p_value = f_p_value
        self.residuals = residuals
        self.fitted = fitted
        self.hat_diag = hat_diag
        self.cooks_d = cooks_d
        self.vif = vif
        self.moran_i = moran_i
        self.moran_expected = moran_expected
        self.moran_z = moran_z
        self.moran_p = moran_p
        self.n = n
        self.k = k
        self.sse = sse
        self.mse = mse
        self.aic = aic
        self.bic = bic
        self.y_values = y_values
        self.x_matrix = x_matrix
        self.seed_coords = seed_coords

    def summary_text(self):
        """Return a formatted text summary."""
        lines = []
        lines.append("=" * 60)
        lines.append("OLS Spatial Regression Results")
        lines.append("=" * 60)
        lines.append("Dependent variable: %s" % self.y_name)
        lines.append("Observations: %d" % self.n)
        lines.append("Predictors: %d (including intercept)" % self.k)
        lines.append("")
        lines.append("R²: %.4f    Adj R²: %.4f" % (self.r_squared, self.adj_r_squared))
        lines.append("F-statistic: %.4f  (p = %.4g)" % (self.f_stat, self.f_p_value))
        lines.append("AIC: %.2f    BIC: %.2f" % (self.aic, self.bic))
        lines.append("")
        lines.append("%-20s %12s %12s %10s %10s" % (
            "Variable", "Coeff", "Std Err", "t", "p-value"))
        lines.append("-" * 64)
        for i, name in enumerate(self.x_names):
            lines.append("%-20s %12.4f %12.4f %10.4f %10.4g" % (
                name[:20], self.coefficients[i], self.std_errors[i],
                self.t_values[i], self.p_values[i]))
        lines.append("")

        # VIF
        if self.vif:
            lines.append("Variance Inflation Factors:")
            for name, v in zip(self.x_names[1:], self.vif):
                flag = " *** HIGH" if v > 10 else (" * moderate" if v > 5 else "")
                lines.append("  %-20s VIF = %.2f%s" % (name[:20], v, flag))
            lines.append("")

        # Spatial diagnostics
        lines.append("Spatial Diagnostics (residuals):")
        lines.append("  Moran's I: %.4f  (expected: %.4f)" % (
            self.moran_i, self.moran_expected))
        lines.append("  z-score: %.4f    p-value: %.4g" % (
            self.moran_z, self.moran_p))
        if self.moran_p < 0.05:
            if self.moran_i > self.moran_expected:
                lines.append("  → Significant positive spatial autocorrelation detected.")
                lines.append("    OLS standard errors may be biased. Consider GWR.")
            else:
                lines.append("  → Significant negative spatial autocorrelation detected.")
        else:
            lines.append("  → No significant spatial autocorrelation (OLS is appropriate).")
        lines.append("")

        # Influential observations
        high_cook = [(i, c) for i, c in enumerate(self.cooks_d) if c > 4.0 / self.n]
        if high_cook:
            lines.append("Influential observations (Cook's D > %.4f):" % (4.0 / self.n))
            for idx, cd in sorted(high_cook, key=lambda x: -x[1])[:10]:
                lines.append("  Region %d: Cook's D = %.4f" % (idx + 1, cd))
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self):
        """Return a JSON-serialisable dictionary."""
        return {
            "model": "OLS",
            "dependent_variable": self.y_name,
            "n": self.n,
            "k": self.k,
            "r_squared": round(self.r_squared, 6),
            "adj_r_squared": round(self.adj_r_squared, 6),
            "f_statistic": round(self.f_stat, 4),
            "f_p_value": round(self.f_p_value, 6),
            "aic": round(self.aic, 2),
            "bic": round(self.bic, 2),
            "sse": round(self.sse, 4),
            "mse": round(self.mse, 6),
            "coefficients": [
                {
                    "name": name,
                    "estimate": round(self.coefficients[i], 6),
                    "std_error": round(self.std_errors[i], 6),
                    "t_value": round(self.t_values[i], 4),
                    "p_value": round(self.p_values[i], 6),
                }
                for i, name in enumerate(self.x_names)
            ],
            "vif": {name: round(v, 4) for name, v in zip(self.x_names[1:], self.vif)} if self.vif else {},
            "spatial_diagnostics": {
                "morans_i": round(self.moran_i, 6),
                "expected_i": round(self.moran_expected, 6),
                "z_score": round(self.moran_z, 4),
                "p_value": round(self.moran_p, 6),
            },
            "influential_observations": [
                {"region": i + 1, "cooks_d": round(c, 6)}
                for i, c in enumerate(self.cooks_d)
                if c > 4.0 / self.n
            ],
        }


# ── GWR Result ───────────────────────────────────────────────────────


class GWRResult:
    """Container for Geographically Weighted Regression results."""

    def __init__(self, *, y_name, x_names, local_coefficients,
                 local_t_values, local_r_squared, bandwidth,
                 kernel, global_r_squared, aic, residuals, fitted,
                 n, k, seed_coords, y_values):
        self.y_name = y_name
        self.x_names = ["intercept"] + list(x_names)
        self.local_coefficients = local_coefficients  # list of lists [n][k]
        self.local_t_values = local_t_values
        self.local_r_squared = local_r_squared
        self.bandwidth = bandwidth
        self.kernel = kernel
        self.global_r_squared = global_r_squared
        self.aic = aic
        self.residuals = residuals
        self.fitted = fitted
        self.n = n
        self.k = k
        self.seed_coords = seed_coords
        self.y_values = y_values

    def summary_text(self):
        """Return a formatted text summary."""
        lines = []
        lines.append("=" * 60)
        lines.append("Geographically Weighted Regression Results")
        lines.append("=" * 60)
        lines.append("Dependent variable: %s" % self.y_name)
        lines.append("Observations: %d" % self.n)
        lines.append("Predictors: %d (including intercept)" % self.k)
        lines.append("Bandwidth: %.2f   Kernel: %s" % (self.bandwidth, self.kernel))
        lines.append("")
        lines.append("Global R²: %.4f    AIC: %.2f" % (self.global_r_squared, self.aic))
        lines.append("")

        # Coefficient surfaces summary
        lines.append("Local coefficient summary (min / median / max):")
        lines.append("%-20s %12s %12s %12s" % ("Variable", "Min", "Median", "Max"))
        lines.append("-" * 56)
        for j, name in enumerate(self.x_names):
            vals = [self.local_coefficients[i][j] for i in range(self.n)]
            vals_sorted = sorted(vals)
            med = vals_sorted[len(vals_sorted) // 2]
            lines.append("%-20s %12.4f %12.4f %12.4f" % (
                name[:20], min(vals), med, max(vals)))
        lines.append("")

        # Local R² summary
        lr2 = self.local_r_squared
        lr2_sorted = sorted(lr2)
        lines.append("Local R² summary:")
        lines.append("  Min: %.4f  Median: %.4f  Max: %.4f  Mean: %.4f" % (
            min(lr2), lr2_sorted[len(lr2) // 2], max(lr2), _mean(lr2)))
        lines.append("")

        # Spatial variation test (coefficient range / mean)
        lines.append("Coefficient spatial variation (range / |mean|):")
        for j, name in enumerate(self.x_names):
            vals = [self.local_coefficients[i][j] for i in range(self.n)]
            rng = max(vals) - min(vals)
            m = abs(_mean(vals))
            ratio = rng / m if m > 1e-10 else float('inf')
            flag = " ← strong variation" if ratio > 2.0 else ""
            lines.append("  %-20s %.4f%s" % (name[:20], ratio, flag))

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def to_dict(self):
        """Return a JSON-serialisable dictionary."""
        return {
            "model": "GWR",
            "dependent_variable": self.y_name,
            "n": self.n,
            "k": self.k,
            "bandwidth": self.bandwidth,
            "kernel": self.kernel,
            "global_r_squared": round(self.global_r_squared, 6),
            "aic": round(self.aic, 2),
            "coefficient_surfaces": {
                name: {
                    "min": round(min(self.local_coefficients[i][j] for i in range(self.n)), 6),
                    "max": round(max(self.local_coefficients[i][j] for i in range(self.n)), 6),
                    "mean": round(_mean([self.local_coefficients[i][j] for i in range(self.n)]), 6),
                }
                for j, name in enumerate(self.x_names)
            },
            "local_r_squared": {
                "min": round(min(self.local_r_squared), 6),
                "max": round(max(self.local_r_squared), 6),
                "mean": round(_mean(self.local_r_squared), 6),
            },
            "per_observation": [
                {
                    "region": i + 1,
                    "x": round(self.seed_coords[i][0], 4),
                    "y": round(self.seed_coords[i][1], 4),
                    "fitted": round(self.fitted[i], 4),
                    "residual": round(self.residuals[i], 4),
                    "local_r2": round(self.local_r_squared[i], 6),
                    "coefficients": {
                        name: round(self.local_coefficients[i][j], 6)
                        for j, name in enumerate(self.x_names)
                    },
                }
                for i in range(self.n)
            ],
        }


# ── Spatial weight matrix ────────────────────────────────────────────


def _build_distance_weights(coords, threshold=None):
    """Build a binary contiguity weight matrix based on distance.

    If threshold is None, uses the median nearest-neighbor distance × 1.5.

    Uses NumPy vectorised distance computation when available (typically
    10-50× faster for n > 100).  Falls back to pure-Python loops when
    NumPy is not installed.
    """
    n = len(coords)

    try:
        import numpy as _np
        pts = _np.asarray(coords, dtype=float)           # (n, 2)
        # Vectorised pairwise Euclidean distance matrix
        diff = pts[:, None, :] - pts[None, :, :]         # (n, n, 2)
        dists_arr = _np.sqrt((diff * diff).sum(axis=2))  # (n, n)

        if threshold is None:
            _np.fill_diagonal(dists_arr, _np.inf)
            nn = dists_arr.min(axis=1)                    # nearest-neighbor
            threshold = float(_np.median(nn)) * 1.5
            _np.fill_diagonal(dists_arr, 0.0)

        # Binary weight matrix (row-standardised)
        mask = (dists_arr <= threshold) & (_np.eye(n, dtype=bool) == False)
        row_counts = mask.sum(axis=1, keepdims=True).astype(float)
        row_counts[row_counts == 0] = 1.0  # avoid division by zero
        W_arr = mask.astype(float) / row_counts

        # Convert back to nested lists for API compatibility
        W = W_arr.tolist()
        dists = dists_arr.tolist()
    except ImportError:
        # Pure-Python fallback
        dists = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = math.sqrt((coords[i][0] - coords[j][0]) ** 2 +
                              (coords[i][1] - coords[j][1]) ** 2)
                dists[i][j] = d
                dists[j][i] = d

        if threshold is None:
            nn_dists = []
            for i in range(n):
                min_d = float('inf')
                for j in range(n):
                    if i != j and dists[i][j] < min_d:
                        min_d = dists[i][j]
                nn_dists.append(min_d)
            nn_dists.sort()
            threshold = nn_dists[len(nn_dists) // 2] * 1.5

        W = [[0.0] * n for _ in range(n)]
        for i in range(n):
            neighbors = []
            for j in range(n):
                if i != j and dists[i][j] <= threshold:
                    neighbors.append(j)
            if neighbors:
                w = 1.0 / len(neighbors)
                for j in neighbors:
                    W[i][j] = w

    return W, dists


def _morans_i(residuals, W):
    """Compute Moran's I statistic and z-test for spatial autocorrelation.

    Uses NumPy vectorised operations when available (avoids multiple
    O(n²) Python comprehensions).  Falls back to pure-Python when NumPy
    is not installed.
    """
    n = len(residuals)
    mean_r = _mean(residuals)

    try:
        import numpy as _np
        z = _np.asarray(residuals, dtype=float) - mean_r
        Wa = _np.asarray(W, dtype=float)

        S0 = float(Wa.sum())
        if S0 < 1e-14:
            return 0.0, -1.0 / (n - 1), 0.0, 1.0

        numerator = float(n * (z[None, :] * Wa * z[:, None]).sum())
        denominator = float(S0 * (z * z).sum())

        if abs(denominator) < 1e-14:
            return 0.0, -1.0 / (n - 1), 0.0, 1.0

        I = numerator / denominator
        expected = -1.0 / (n - 1)

        Wsym = Wa + Wa.T
        S1 = float(0.5 * (Wsym * Wsym).sum())
        row_col_sums = Wa.sum(axis=1) + Wa.sum(axis=0)   # (n,)
        S2 = float((row_col_sums ** 2).sum())

        n2 = n * n
        S0_2 = S0 * S0
        var_I = (n2 * S1 - n * S2 + 3 * S0_2) / (S0_2 * (n2 - 1)) - expected ** 2

        if var_I <= 0:
            return I, expected, 0.0, 1.0

        z_score = (I - expected) / math.sqrt(var_I)
        p_value = 2.0 * (1.0 - _norm_cdf(abs(z_score)))
        return I, expected, z_score, p_value

    except ImportError:
        pass

    # Pure-Python fallback
    z = [r - mean_r for r in residuals]

    S0 = sum(W[i][j] for i in range(n) for j in range(n))
    if S0 < 1e-14:
        return 0.0, -1.0 / (n - 1), 0.0, 1.0

    numerator = n * sum(W[i][j] * z[i] * z[j]
                        for i in range(n) for j in range(n))
    denominator = S0 * sum(zi ** 2 for zi in z)

    if abs(denominator) < 1e-14:
        return 0.0, -1.0 / (n - 1), 0.0, 1.0

    I = numerator / denominator
    expected = -1.0 / (n - 1)

    S1 = 0.5 * sum((W[i][j] + W[j][i]) ** 2
                    for i in range(n) for j in range(n))
    S2 = sum((sum(W[i][j] for j in range(n)) +
              sum(W[j][i] for j in range(n))) ** 2 for i in range(n))

    n2 = n * n
    S0_2 = S0 * S0
    var_I = (n2 * S1 - n * S2 + 3 * S0_2) / (S0_2 * (n2 - 1)) - expected ** 2

    if var_I <= 0:
        return I, expected, 0.0, 1.0

    z_score = (I - expected) / math.sqrt(var_I)
    p_value = 2.0 * (1.0 - _norm_cdf(abs(z_score)))

    return I, expected, z_score, p_value


def _norm_cdf(x):
    """Approximate standard normal CDF (Abramowitz & Stegun)."""
    if x < 0:
        return 1.0 - _norm_cdf(-x)
    t = 1.0 / (1.0 + 0.2316419 * x)
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    poly = ((((1.330274429 * t - 1.821255978) * t + 1.781477937) * t
             - 0.356563782) * t + 0.319381530) * t
    return 1.0 - d * math.exp(-0.5 * x * x) * poly


# ── F-distribution p-value approximation ─────────────────────────────


def _f_pvalue(f_val, df1, df2):
    """Approximate p-value for F distribution using normal approximation."""
    if f_val <= 0 or df1 <= 0 or df2 <= 0:
        return 1.0
    # Use the Wilson-Hilferty approximation
    a = df1
    b = df2
    x = f_val
    # Transform to approximate chi-squared ratio
    lam = (a * x / (a * x + b))
    # Regularised incomplete beta; use a rough normal approx
    # F to z approximation
    z = ((1.0 - 2.0 / (9.0 * b)) * (b / (a * x)) ** (1.0 / 3.0)
         - (1.0 - 2.0 / (9.0 * a))) / math.sqrt(
            2.0 / (9.0 * a) + (2.0 / (9.0 * b)) * (b / (a * x)) ** (2.0 / 3.0))
    return _norm_cdf(z)


# ── t-distribution p-value approximation ─────────────────────────────


def _t_pvalue(t_val, df):
    """Approximate two-tailed p-value for t distribution."""
    if df <= 0:
        return 1.0
    # Convert to F and use F p-value
    return _f_pvalue(t_val * t_val, 1, df)


# ── Core fitting functions ───────────────────────────────────────────


def _extract_variables(stats, y, x_names):
    """Extract y vector, X matrix, and coordinates from stats list."""
    valid_attrs = {"area", "perimeter", "compactness", "vertex_count",
                   "avg_edge_length", "seed_x", "seed_y",
                   "centroid_x", "centroid_y"}

    if y not in valid_attrs:
        raise ValueError("Unknown dependent variable '%s'. Choose from: %s"
                         % (y, ", ".join(sorted(valid_attrs))))
    for xn in x_names:
        if xn not in valid_attrs:
            raise ValueError("Unknown predictor '%s'. Choose from: %s"
                             % (xn, ", ".join(sorted(valid_attrs))))
    if y in x_names:
        raise ValueError("Dependent variable '%s' cannot also be a predictor" % y)

    y_vals = [s[y] for s in stats]
    coords = [(s["seed_x"], s["seed_y"]) for s in stats]
    # X matrix with intercept column
    X = [[1.0] + [s[xn] for xn in x_names] for s in stats]

    return y_vals, X, coords


def fit_ols(stats, *, y, x):
    """Fit OLS regression with spatial diagnostics.

    Parameters
    ----------
    stats : list of dict
        Output of ``vormap_viz.compute_region_stats()``.
    y : str
        Dependent variable name (e.g. ``"area"``).
    x : list of str
        Predictor variable names (e.g. ``["compactness", "vertex_count"]``).

    Returns
    -------
    OLSResult
        Full regression results with diagnostics.
    """
    if not x:
        raise ValueError("At least one predictor variable is required")
    if len(stats) < len(x) + 2:
        raise ValueError("Need at least %d observations for %d predictors"
                         % (len(x) + 2, len(x)))

    y_vals, X, coords = _extract_variables(stats, y, x)
    n = len(y_vals)
    k = len(X[0])  # includes intercept

    # X'X and X'y
    Xt = _transpose(X)
    XtX = _mat_mul(Xt, X)
    Xty = _mat_vec(Xt, y_vals)

    # Solve for coefficients
    beta = _solve(XtX, Xty)

    # Fitted values and residuals
    fitted = [_dot(X[i], beta) for i in range(n)]
    residuals = [y_vals[i] - fitted[i] for i in range(n)]

    # SSE, SSR, SST
    sse = sum(r ** 2 for r in residuals)
    y_mean = _mean(y_vals)
    sst = sum((yi - y_mean) ** 2 for yi in y_vals)
    ssr = sst - sse

    # R² and adjusted R²
    r_squared = 1.0 - sse / sst if sst > 0 else 0.0
    adj_r_squared = 1.0 - (1.0 - r_squared) * (n - 1) / (n - k) if n > k else 0.0

    # MSE
    mse = sse / (n - k) if n > k else 0.0

    # Standard errors of coefficients
    try:
        XtX_inv = _invert(XtX)
    except Exception:
        XtX_inv = _identity(k)

    std_errors = []
    for j in range(k):
        se = math.sqrt(abs(mse * XtX_inv[j][j]))
        std_errors.append(se)

    # t-values and p-values
    t_values = [beta[j] / std_errors[j] if std_errors[j] > 1e-14 else 0.0
                for j in range(k)]
    p_values = [_t_pvalue(t, n - k) for t in t_values]

    # F-statistic
    if k > 1 and mse > 0:
        f_stat = (ssr / (k - 1)) / mse
        f_p_value = _f_pvalue(f_stat, k - 1, n - k)
    else:
        f_stat = 0.0
        f_p_value = 1.0

    # Hat matrix diagonal (leverage)
    hat_diag = []
    for i in range(n):
        xi = X[i]
        h = sum(xi[a] * sum(XtX_inv[a][b] * xi[b] for b in range(k))
                for a in range(k))
        hat_diag.append(h)

    # Cook's distance
    cooks_d = []
    for i in range(n):
        hi = hat_diag[i]
        if abs(1.0 - hi) > 1e-14 and mse > 0:
            cd = (residuals[i] ** 2 * hi) / (k * mse * (1.0 - hi) ** 2)
        else:
            cd = 0.0
        cooks_d.append(cd)

    # VIF (Variance Inflation Factors) for each predictor
    vif = []
    if len(x) > 1:
        for j in range(1, k):
            # Regress x_j on all other x's
            other_cols = [col for col in range(1, k) if col != j]
            y_j = [X[i][j] for i in range(n)]
            X_j = [[1.0] + [X[i][c] for c in other_cols] for i in range(n)]
            Xt_j = _transpose(X_j)
            try:
                beta_j = _solve(_mat_mul(Xt_j, X_j), _mat_vec(Xt_j, y_j))
                fitted_j = [_dot(X_j[i], beta_j) for i in range(n)]
                ss_res = sum((y_j[i] - fitted_j[i]) ** 2 for i in range(n))
                ss_tot = sum((y_j[i] - _mean(y_j)) ** 2 for i in range(n))
                r2_j = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
                vif.append(1.0 / (1.0 - r2_j) if r2_j < 1.0 else 999.0)
            except Exception:
                vif.append(1.0)
    else:
        vif = [1.0]

    # Spatial diagnostics: Moran's I on residuals
    W, _ = _build_distance_weights(coords)
    moran_i, moran_exp, moran_z, moran_p = _morans_i(residuals, W)

    # AIC, BIC
    if sse > 0 and n > 0:
        log_lik = -n / 2.0 * (math.log(2 * math.pi) + math.log(sse / n) + 1)
        aic = -2 * log_lik + 2 * k
        bic = -2 * log_lik + k * math.log(n)
    else:
        aic = bic = float('inf')

    return OLSResult(
        y_name=y, x_names=x, coefficients=beta, std_errors=std_errors,
        t_values=t_values, p_values=p_values, r_squared=r_squared,
        adj_r_squared=adj_r_squared, f_stat=f_stat, f_p_value=f_p_value,
        residuals=residuals, fitted=fitted, hat_diag=hat_diag,
        cooks_d=cooks_d, vif=vif, moran_i=moran_i,
        moran_expected=moran_exp, moran_z=moran_z, moran_p=moran_p,
        n=n, k=k, sse=sse, mse=mse, aic=aic, bic=bic,
        y_values=y_vals, x_matrix=X, seed_coords=coords,
    )


def fit_gwr(stats, *, y, x, bandwidth=None, kernel="gaussian"):
    """Fit Geographically Weighted Regression.

    Parameters
    ----------
    stats : list of dict
        Output of ``vormap_viz.compute_region_stats()``.
    y : str
        Dependent variable name.
    x : list of str
        Predictor variable names.
    bandwidth : float or None
        Distance bandwidth for the kernel. If None, uses the median
        pairwise distance.
    kernel : str
        ``"gaussian"`` or ``"bisquare"``.

    Returns
    -------
    GWRResult
        Full GWR results with local coefficient surfaces.
    """
    if not x:
        raise ValueError("At least one predictor variable is required")
    if kernel not in ("gaussian", "bisquare"):
        raise ValueError("Kernel must be 'gaussian' or 'bisquare'")

    y_vals, X, coords = _extract_variables(stats, y, x)
    n = len(y_vals)
    k = len(X[0])

    if n < k + 1:
        raise ValueError("Need at least %d observations" % (k + 1))

    # Compute all pairwise distances
    dists = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = math.sqrt((coords[i][0] - coords[j][0]) ** 2 +
                          (coords[i][1] - coords[j][1]) ** 2)
            dists[i][j] = d
            dists[j][i] = d

    # Auto bandwidth: median pairwise distance
    if bandwidth is None:
        all_dists = []
        for i in range(n):
            for j in range(i + 1, n):
                all_dists.append(dists[i][j])
        all_dists.sort()
        bandwidth = all_dists[len(all_dists) // 2] if all_dists else 1.0

    # Fit local regressions
    local_coeffs = []
    local_t_vals = []
    local_r2 = []
    fitted = []
    residuals = []

    y_mean = _mean(y_vals)
    sst_global = sum((yi - y_mean) ** 2 for yi in y_vals)

    for i in range(n):
        # Compute weights for observation i
        weights = []
        for j in range(n):
            d = dists[i][j]
            if kernel == "gaussian":
                w = math.exp(-0.5 * (d / bandwidth) ** 2)
            else:  # bisquare
                if d <= bandwidth:
                    w = (1.0 - (d / bandwidth) ** 2) ** 2
                else:
                    w = 0.0
            weights.append(w)

        # Weighted X and y
        # X'WX and X'Wy
        XtWX = [[0.0] * k for _ in range(k)]
        XtWy = [0.0] * k
        for j in range(n):
            wj = weights[j]
            if wj < 1e-14:
                continue
            for a in range(k):
                XtWy[a] += wj * X[j][a] * y_vals[j]
                for b in range(k):
                    XtWX[a][b] += wj * X[j][a] * X[j][b]

        try:
            beta_i = _solve(XtWX, XtWy)
        except Exception:
            beta_i = [0.0] * k

        local_coeffs.append(beta_i)

        # Fitted value at i
        y_hat_i = _dot(X[i], beta_i)
        fitted.append(y_hat_i)
        residuals.append(y_vals[i] - y_hat_i)

        # Local R² (weighted)
        w_ss_tot = sum(weights[j] * (y_vals[j] - y_mean) ** 2 for j in range(n))
        w_ss_res = sum(weights[j] * (y_vals[j] - _dot(X[j], beta_i)) ** 2
                       for j in range(n))
        lr2 = 1.0 - w_ss_res / w_ss_tot if w_ss_tot > 0 else 0.0
        local_r2.append(max(0.0, min(1.0, lr2)))

        # Local t-values (approximate)
        w_mse = w_ss_res / max(sum(1 for w in weights if w > 1e-14) - k, 1)
        try:
            XtWX_inv = _invert(XtWX)
            t_vals_i = []
            for a in range(k):
                se = math.sqrt(abs(w_mse * XtWX_inv[a][a]))
                t_vals_i.append(beta_i[a] / se if se > 1e-14 else 0.0)
        except Exception:
            t_vals_i = [0.0] * k
        local_t_vals.append(t_vals_i)

    # Global R² and AIC
    sse = sum(r ** 2 for r in residuals)
    global_r2 = 1.0 - sse / sst_global if sst_global > 0 else 0.0

    if sse > 0 and n > 0:
        log_lik = -n / 2.0 * (math.log(2 * math.pi) + math.log(sse / n) + 1)
        # Effective parameters approximation: trace of hat matrix ≈ k * n / effective_n
        aic = -2 * log_lik + 2 * k * 2  # rough penalty for GWR
    else:
        aic = float('inf')

    return GWRResult(
        y_name=y, x_names=x, local_coefficients=local_coeffs,
        local_t_values=local_t_vals, local_r_squared=local_r2,
        bandwidth=bandwidth, kernel=kernel,
        global_r_squared=global_r2, aic=aic,
        residuals=residuals, fitted=fitted,
        n=n, k=k, seed_coords=coords, y_values=y_vals,
    )


# ── Export functions ─────────────────────────────────────────────────


def export_regress_json(result, path):
    """Export regression results as JSON."""
    path = validate_output_path(path, allow_absolute=True)
    with open(path, 'w') as f:
        json.dump(result.to_dict(), f, indent=2)


def export_regress_csv(result, path):
    """Export per-observation regression data as CSV."""
    path = validate_output_path(path, allow_absolute=True)
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)

        if isinstance(result, OLSResult):
            writer.writerow(["# OLS Regression: %s" % result.y_name])
            writer.writerow(["# R² = %.4f, Adj R² = %.4f" % (
                result.r_squared, result.adj_r_squared)])
            writer.writerow([])
            header = ["region", "x", "y", "observed", "fitted",
                      "residual", "leverage", "cooks_d"]
            writer.writerow(header)
            for i in range(result.n):
                writer.writerow([
                    i + 1,
                    round(result.seed_coords[i][0], 4),
                    round(result.seed_coords[i][1], 4),
                    round(result.y_values[i], 4),
                    round(result.fitted[i], 4),
                    round(result.residuals[i], 4),
                    round(result.hat_diag[i], 6),
                    round(result.cooks_d[i], 6),
                ])
        else:
            # GWR
            writer.writerow(["# GWR: %s (bandwidth=%.2f, kernel=%s)" % (
                result.y_name, result.bandwidth, result.kernel)])
            writer.writerow(["# Global R² = %.4f" % result.global_r_squared])
            writer.writerow([])
            coef_names = ["coef_%s" % name for name in result.x_names]
            header = ["region", "x", "y", "observed", "fitted",
                      "residual", "local_r2"] + coef_names
            writer.writerow(header)
            for i in range(result.n):
                row = [
                    i + 1,
                    round(result.seed_coords[i][0], 4),
                    round(result.seed_coords[i][1], 4),
                    round(result.y_values[i], 4),
                    round(result.fitted[i], 4),
                    round(result.residuals[i], 4),
                    round(result.local_r_squared[i], 6),
                ] + [round(result.local_coefficients[i][j], 6)
                     for j in range(result.k)]
                writer.writerow(row)


def export_regress_svg(result, regions, data, path, *,
                       width=800, height=600, show_type="residuals"):
    """Export a choropleth SVG of residuals or fitted values.

    Parameters
    ----------
    result : OLSResult or GWRResult
        Regression result.
    regions : dict
        Voronoi regions from ``compute_regions()``.
    data : list of (float, float)
        Seed points.
    path : str
        Output SVG path.
    width, height : int
        Canvas dimensions.
    show_type : str
        ``"residuals"``, ``"fitted"``, ``"cooks_d"`` (OLS only),
        ``"local_r2"`` (GWR only), or a coefficient name.
    """
    path = validate_output_path(path, allow_absolute=True)

    # Determine values to map
    if show_type == "residuals":
        values = result.residuals
        title = "Residuals"
    elif show_type == "fitted":
        values = result.fitted
        title = "Fitted Values"
    elif show_type == "cooks_d" and isinstance(result, OLSResult):
        values = result.cooks_d
        title = "Cook's Distance"
    elif show_type == "local_r2" and isinstance(result, GWRResult):
        values = result.local_r_squared
        title = "Local R²"
    elif show_type in (result.x_names if hasattr(result, 'x_names') else []):
        j = result.x_names.index(show_type)
        if isinstance(result, GWRResult):
            values = [result.local_coefficients[i][j] for i in range(result.n)]
        else:
            values = [result.coefficients[j]] * result.n
        title = "Coefficient: %s" % show_type
    else:
        values = result.residuals
        title = "Residuals"

    # Build SVG
    sorted_seeds = sorted(regions.keys())
    coords = [(s[0], s[1]) for s in sorted_seeds]

    # Compute bounds
    all_x = [c[0] for c in coords]
    all_y = [c[1] for c in coords]
    for verts in regions.values():
        for vx, vy in verts:
            all_x.append(vx)
            all_y.append(vy)

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    range_x = max_x - min_x or 1
    range_y = max_y - min_y or 1

    margin = 40
    plot_w = width - 2 * margin
    plot_h = height - 2 * margin - 30  # room for title
    scale = min(plot_w / range_x, plot_h / range_y)

    def tx(x):
        return margin + (x - min_x) * scale

    def ty(y):
        return margin + 30 + (max_y - y) * scale

    # Color mapping: diverging blue-white-red for residuals, sequential for others
    v_min = min(values) if values else 0
    v_max = max(values) if values else 1
    v_range = v_max - v_min or 1

    def _diverging_color(v):
        """Blue-white-red diverging scheme centered on 0."""
        mid = (v_min + v_max) / 2
        if abs(v_max - v_min) < 1e-14:
            return "rgb(255,255,255)"
        t = (v - mid) / (max(abs(v_max - mid), abs(v_min - mid)) or 1)
        t = max(-1, min(1, t))
        if t < 0:
            r = int(255 * (1 + t))
            g = int(255 * (1 + t))
            b = 255
        else:
            r = 255
            g = int(255 * (1 - t))
            b = int(255 * (1 - t))
        return "rgb(%d,%d,%d)" % (r, g, b)

    def _sequential_color(v):
        """White-to-red sequential scheme."""
        t = (v - v_min) / v_range
        t = max(0, min(1, t))
        r = 255
        g = int(255 * (1 - t * 0.8))
        b = int(255 * (1 - t * 0.9))
        return "rgb(%d,%d,%d)" % (r, g, b)

    use_diverging = show_type in ("residuals",)
    color_fn = _diverging_color if use_diverging else _sequential_color

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height))

    # Background
    ET.SubElement(svg, "rect", x="0", y="0", width=str(width),
                  height=str(height), fill="#fafafa")

    # Title
    title_el = ET.SubElement(svg, "text", x=str(width // 2), y="22",
                             fill="#333")
    title_el.set("text-anchor", "middle")
    title_el.set("font-family", "sans-serif")
    title_el.set("font-size", "14")
    title_el.set("font-weight", "bold")
    model_name = "OLS" if isinstance(result, OLSResult) else "GWR"
    title_el.text = "%s — %s (%s → %s)" % (
        title, model_name, ", ".join(result.x_names[1:]), result.y_name)

    # Draw regions
    for idx, seed in enumerate(sorted_seeds):
        if idx >= len(values):
            break
        verts = regions[seed]
        if len(verts) < 3:
            continue
        points_str = " ".join("%.1f,%.1f" % (tx(vx), ty(vy))
                              for vx, vy in verts)
        color = color_fn(values[idx])
        poly = ET.SubElement(svg, "polygon", points=points_str,
                             fill=color, stroke="#666")
        poly.set("stroke-width", "0.5")
        poly.set("opacity", "0.85")

    # Legend
    legend_y = height - 20
    for li, lv in enumerate(range(5)):
        frac = lv / 4.0
        val = v_min + frac * v_range
        lx = margin + li * (plot_w // 4)
        rect = ET.SubElement(svg, "rect", x=str(lx), y=str(legend_y),
                             width="20", height="12", fill=color_fn(val),
                             stroke="#999")
        rect.set("stroke-width", "0.5")
        lab = ET.SubElement(svg, "text", x=str(lx + 24), y=str(legend_y + 10),
                            fill="#666")
        lab.set("font-family", "sans-serif")
        lab.set("font-size", "9")
        lab.text = "%.2g" % val

    tree = ET.ElementTree(svg)
    ET.indent(tree, space="  ")
    tree.write(path, xml_declaration=True, encoding="unicode")


# ── CLI integration ──────────────────────────────────────────────────


def parse_formula(formula):
    """Parse a formula string like 'area~compactness+vertex_count'.

    Returns (y_name, [x_names]).
    """
    if '~' not in formula:
        raise ValueError(
            "Formula must be in the form 'y~x1+x2'. Got: '%s'" % formula)
    parts = formula.split('~', 1)
    y = parts[0].strip()
    x_part = parts[1].strip()
    x_names = [xn.strip() for xn in x_part.split('+') if xn.strip()]
    if not x_names:
        raise ValueError("No predictors specified in formula")
    return y, x_names


def add_cli_args(parser):
    """Add regression CLI arguments to an argparse parser."""
    parser.add_argument(
        '--regress',
        metavar='FORMULA',
        help='Fit OLS spatial regression. Formula: y~x1+x2 '
             '(e.g. area~compactness+vertex_count).',
    )
    parser.add_argument(
        '--regress-gwr',
        metavar='FORMULA',
        help='Fit Geographically Weighted Regression. Same formula syntax.',
    )
    parser.add_argument(
        '--bandwidth',
        type=float,
        default=None,
        help='GWR bandwidth (distance). Auto-computed if omitted.',
    )
    parser.add_argument(
        '--kernel',
        choices=['gaussian', 'bisquare'],
        default='gaussian',
        help='GWR kernel function (default: gaussian).',
    )
    parser.add_argument(
        '--regress-svg',
        metavar='OUTPUT',
        help='Export regression residual map as SVG.',
    )
    parser.add_argument(
        '--regress-json',
        metavar='OUTPUT',
        help='Export regression results as JSON.',
    )
    parser.add_argument(
        '--regress-csv',
        metavar='OUTPUT',
        help='Export per-observation regression data as CSV.',
    )
    parser.add_argument(
        '--regress-show',
        default='residuals',
        help='SVG display: residuals, fitted, cooks_d, local_r2, or a '
             'coefficient name (default: residuals).',
    )


def run_regress_cli(args, regions, data):
    """Execute regression from CLI arguments."""
    import vormap_viz

    stats = vormap_viz.compute_region_stats(regions, data)

    result = None

    if args.regress:
        y, x_names = parse_formula(args.regress)
        result = fit_ols(stats, y=y, x=x_names)
        print(result.summary_text())

    if args.regress_gwr:
        y, x_names = parse_formula(args.regress_gwr)
        gwr_result = fit_gwr(stats, y=y, x=x_names,
                             bandwidth=args.bandwidth,
                             kernel=args.kernel)
        print(gwr_result.summary_text())
        if result is None:
            result = gwr_result

    if result is None:
        return

    if args.regress_svg:
        export_regress_svg(result, regions, data, args.regress_svg,
                           show_type=args.regress_show)
        print('Regression SVG saved to %s' % args.regress_svg)

    if args.regress_json:
        export_regress_json(result, args.regress_json)
        print('Regression JSON saved to %s' % args.regress_json)

    if args.regress_csv:
        export_regress_csv(result, args.regress_csv)
        print('Regression CSV saved to %s' % args.regress_csv)
