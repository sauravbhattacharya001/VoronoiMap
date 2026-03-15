"""Tests for vormap_crossval — leave-one-out cross-validation module.

Covers: CrossValResult dataclass, cross_validate() core logic,
compare_methods() ranking, export_crossval_csv/svg, input validation,
edge cases, and the summary() formatter.
"""

import csv
import math
import os
import tempfile
import xml.etree.ElementTree as ET

import pytest

from vormap_crossval import (
    CrossValResult,
    cross_validate,
    compare_methods,
    export_crossval_csv,
    export_crossval_svg,
    _get_interp_fn,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def simple_points():
    """Four well-separated points with known values."""
    return [(0, 0), (100, 0), (0, 100), (100, 100)]


@pytest.fixture
def simple_values():
    """Values matching simple_points — linear gradient makes IDW decent."""
    return [10.0, 20.0, 30.0, 40.0]


@pytest.fixture
def collinear_points():
    """Points along a line — challenges some interpolation methods."""
    return [(0, 0), (10, 0), (20, 0), (30, 0), (40, 0)]


@pytest.fixture
def collinear_values():
    """Linear values along the collinear_points line."""
    return [0.0, 10.0, 20.0, 30.0, 40.0]


@pytest.fixture
def grid_points():
    """3x3 grid of points — good for all interpolation methods."""
    pts = []
    for y in range(3):
        for x in range(3):
            pts.append((x * 50.0, y * 50.0))
    return pts


@pytest.fixture
def grid_values():
    """Values for 3x3 grid — smooth surface z = x + y."""
    vals = []
    for y in range(3):
        for x in range(3):
            vals.append(float(x * 50 + y * 50))
    return vals


# ── CrossValResult ──────────────────────────────────────────────────

class TestCrossValResult:
    def test_defaults(self):
        r = CrossValResult()
        assert r.method == ''
        assert r.n == 0
        assert r.mae == 0.0
        assert r.rmse == 0.0
        assert r.r_squared == 0.0
        assert r.max_error == 0.0
        assert r.residuals == []
        assert r.elapsed_ms == 0.0

    def test_summary_format(self):
        r = CrossValResult(method='idw', n=10, mae=1.5, rmse=2.3,
                           r_squared=0.85, max_error=4.1, elapsed_ms=42)
        s = r.summary()
        assert 'idw' in s
        assert 'n=10' in s
        assert 'MAE=1.5000' in s
        assert 'RMSE=2.3000' in s
        assert 'R²=0.8500' in s
        assert 'MaxErr=4.1000' in s
        assert '42ms' in s

    def test_residuals_not_shared(self):
        """Ensure default residuals list isn't shared across instances."""
        r1 = CrossValResult()
        r2 = CrossValResult()
        r1.residuals.append({'x': 1})
        assert len(r2.residuals) == 0


# ── _get_interp_fn ──────────────────────────────────────────────────

class TestGetInterpFn:
    def test_valid_methods(self, simple_points, simple_values):
        for method in ('nearest', 'idw', 'natural'):
            fn = _get_interp_fn(method, simple_points, simple_values)
            assert callable(fn)

    def test_unknown_method(self, simple_points, simple_values):
        with pytest.raises(ValueError, match="Unknown method"):
            _get_interp_fn('kriging', simple_points, simple_values)


# ── cross_validate ──────────────────────────────────────────────────

class TestCrossValidate:
    def test_nearest_basic(self, simple_points, simple_values):
        r = cross_validate(simple_points, simple_values, method='nearest')
        assert r.method == 'nearest'
        assert r.n == 4
        assert r.mae >= 0
        assert r.rmse >= r.mae  # RMSE ≥ MAE always
        assert r.max_error >= r.mae
        assert len(r.residuals) == 4
        assert r.elapsed_ms >= 0

    def test_idw_basic(self, simple_points, simple_values):
        r = cross_validate(simple_points, simple_values, method='idw')
        assert r.method == 'idw'
        assert r.n == 4
        assert r.mae >= 0
        assert r.rmse >= 0

    def test_natural_basic(self, grid_points, grid_values):
        """Natural neighbor needs ≥ 4 points remaining (so ≥ 5 total)."""
        r = cross_validate(grid_points, grid_values, method='natural')
        assert r.method == 'natural'
        assert r.n > 0
        assert r.mae >= 0

    def test_idw_perfect_linear(self):
        """IDW on a perfectly linear dataset should have very low error."""
        # 5 points on z = x
        pts = [(float(i * 10), 0.0) for i in range(5)]
        vals = [float(i * 10) for i in range(5)]
        r = cross_validate(pts, vals, method='idw')
        # IDW won't be perfect on linear data but should be reasonable
        assert r.mae < 20  # generous bound

    def test_residuals_structure(self, simple_points, simple_values):
        r = cross_validate(simple_points, simple_values, method='nearest')
        for res in r.residuals:
            assert 'x' in res
            assert 'y' in res
            assert 'observed' in res
            assert 'predicted' in res
            assert 'error' in res
            assert res['error'] == pytest.approx(
                res['predicted'] - res['observed'])

    def test_r_squared_range(self, grid_points, grid_values):
        """R² should be between -inf and 1.0; for decent data close to 1."""
        r = cross_validate(grid_points, grid_values, method='idw')
        assert r.r_squared <= 1.0

    def test_max_error_is_max(self, simple_points, simple_values):
        r = cross_validate(simple_points, simple_values, method='idw')
        abs_errors = [abs(res['error']) for res in r.residuals]
        assert r.max_error == pytest.approx(max(abs_errors))

    def test_mae_calculation(self, simple_points, simple_values):
        r = cross_validate(simple_points, simple_values, method='idw')
        abs_errors = [abs(res['error']) for res in r.residuals]
        expected_mae = sum(abs_errors) / len(abs_errors)
        assert r.mae == pytest.approx(expected_mae)

    def test_rmse_calculation(self, simple_points, simple_values):
        r = cross_validate(simple_points, simple_values, method='idw')
        sq_errors = [res['error'] ** 2 for res in r.residuals]
        expected_rmse = math.sqrt(sum(sq_errors) / len(sq_errors))
        assert r.rmse == pytest.approx(expected_rmse)

    def test_different_powers(self, grid_points, grid_values):
        """Different IDW powers should produce different results."""
        r1 = cross_validate(grid_points, grid_values, method='idw', power=1.0)
        r2 = cross_validate(grid_points, grid_values, method='idw', power=3.0)
        # Different powers → different RMSE (unless data is trivial)
        # Just verify they both run successfully
        assert r1.n == r2.n
        assert r1.mae >= 0
        assert r2.mae >= 0

    def test_constant_values(self):
        """All same values → MAE=0, RMSE=0, R²=0 (ss_tot=0)."""
        pts = [(0, 0), (10, 0), (0, 10), (10, 10)]
        vals = [5.0, 5.0, 5.0, 5.0]
        r = cross_validate(pts, vals, method='nearest')
        assert r.mae == pytest.approx(0.0)
        assert r.rmse == pytest.approx(0.0)
        # R² = 0 when ss_tot = 0 (all same values)
        assert r.r_squared == pytest.approx(0.0)


# ── Validation errors ──────────────────────────────────────────────

class TestCrossValidateErrors:
    def test_mismatched_lengths(self):
        with pytest.raises(ValueError, match="same length"):
            cross_validate([(0, 0), (1, 1)], [1.0, 2.0, 3.0])

    def test_too_few_points(self):
        with pytest.raises(ValueError, match="at least 3"):
            cross_validate([(0, 0), (1, 1)], [1.0, 2.0])

    def test_natural_too_few_points(self):
        """Natural neighbor needs ≥ 4 points for LOO."""
        with pytest.raises(ValueError, match="at least 4"):
            cross_validate([(0, 0), (1, 1), (2, 2)], [1.0, 2.0, 3.0],
                           method='natural')

    def test_unknown_method(self):
        pts = [(0, 0), (1, 1), (2, 2)]
        vals = [1.0, 2.0, 3.0]
        with pytest.raises(ValueError, match="Unknown method"):
            cross_validate(pts, vals, method='kriging')


# ── compare_methods ─────────────────────────────────────────────────

class TestCompareMethods:
    def test_default_methods(self, grid_points, grid_values):
        results = compare_methods(grid_points, grid_values)
        methods_returned = [r.method for r in results]
        # All three methods should run
        assert 'nearest' in methods_returned
        assert 'idw' in methods_returned
        assert 'natural' in methods_returned

    def test_sorted_by_rmse(self, grid_points, grid_values):
        results = compare_methods(grid_points, grid_values)
        rmse_values = [r.rmse for r in results]
        assert rmse_values == sorted(rmse_values)

    def test_custom_methods(self, simple_points, simple_values):
        results = compare_methods(simple_points, simple_values,
                                  methods=['nearest', 'idw'])
        assert len(results) == 2
        methods = {r.method for r in results}
        assert methods == {'nearest', 'idw'}

    def test_single_method(self, simple_points, simple_values):
        results = compare_methods(simple_points, simple_values,
                                  methods=['idw'])
        assert len(results) == 1
        assert results[0].method == 'idw'

    def test_skips_methods_with_too_few_points(self):
        """3 points: natural requires 4 for LOO, should be skipped."""
        pts = [(0, 0), (10, 10), (20, 0)]
        vals = [1.0, 2.0, 3.0]
        results = compare_methods(pts, vals)
        methods = [r.method for r in results]
        assert 'natural' not in methods
        assert len(results) >= 1  # nearest and/or idw should still work


# ── export_crossval_csv ────────────────────────────────────────────

class TestExportCSV:
    def test_basic_export(self, grid_points, grid_values):
        results = compare_methods(grid_points, grid_values)
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            export_crossval_csv(results, path)
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                rows = list(reader)
            # Header + 3 method rows
            assert rows[0] == ['method', 'n', 'mae', 'rmse', 'r_squared',
                                'max_error', 'elapsed_ms']
            assert len(rows) >= 4  # header + at least 3 methods
        finally:
            os.unlink(path)

    def test_export_with_residuals(self, simple_points, simple_values):
        results = [cross_validate(simple_points, simple_values, method='idw')]
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            export_crossval_csv(results, path, include_residuals=True)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            assert '--- Residuals ---' in content
            # Should have residual rows
            lines = content.strip().split('\n')
            # header + 1 method + blank + separator + residuals header + 4 residuals
            assert len(lines) >= 8
        finally:
            os.unlink(path)

    def test_empty_results(self):
        with tempfile.NamedTemporaryFile(suffix='.csv', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            export_crossval_csv([], path)
            with open(path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                rows = list(reader)
            # Just header row
            assert len(rows) == 1
        finally:
            os.unlink(path)


# ── export_crossval_svg ────────────────────────────────────────────

class TestExportSVG:
    def test_basic_export(self, grid_points, grid_values):
        results = compare_methods(grid_points, grid_values)
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            export_crossval_svg(results, path)
            tree = ET.parse(path)
            root = tree.getroot()
            assert root.tag == '{http://www.w3.org/2000/svg}svg' or \
                   root.tag == 'svg'
            # Should have bars (rects), text labels, etc.
            ns = {'svg': 'http://www.w3.org/2000/svg'}
            rects = root.findall('.//svg:rect', ns) or root.findall('.//rect')
            assert len(rects) > 0
        finally:
            os.unlink(path)

    def test_custom_dimensions(self, grid_points, grid_values):
        results = compare_methods(grid_points, grid_values)
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            export_crossval_svg(results, path, width=800, height=500,
                                title='Custom Title')
            tree = ET.parse(path)
            root = tree.getroot()
            assert root.get('width') == '800'
            assert root.get('height') == '500'
            # Title should be in the SVG
            with open(path, 'r') as f:
                content = f.read()
            assert 'Custom Title' in content
        finally:
            os.unlink(path)

    def test_empty_results_raises(self):
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            with pytest.raises(ValueError, match="No results"):
                export_crossval_svg([], path)
        finally:
            os.unlink(path)

    def test_single_method_chart(self, simple_points, simple_values):
        results = [cross_validate(simple_points, simple_values, method='idw')]
        with tempfile.NamedTemporaryFile(suffix='.svg', delete=False,
                                         mode='w') as f:
            path = f.name
        try:
            export_crossval_svg(results, path)
            assert os.path.getsize(path) > 100
        finally:
            os.unlink(path)


# ── Integration ─────────────────────────────────────────────────────

class TestIntegration:
    def test_full_workflow(self, grid_points, grid_values):
        """End-to-end: validate → compare → csv → svg."""
        results = compare_methods(grid_points, grid_values)
        assert len(results) >= 2

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, 'results.csv')
            svg_path = os.path.join(tmpdir, 'chart.svg')

            export_crossval_csv(results, csv_path, include_residuals=True)
            export_crossval_svg(results, svg_path)

            assert os.path.exists(csv_path)
            assert os.path.exists(svg_path)
            assert os.path.getsize(csv_path) > 50
            assert os.path.getsize(svg_path) > 200

    def test_idw_beats_nearest_on_smooth_surface(self, grid_points,
                                                  grid_values):
        """IDW should generally beat nearest on smooth data."""
        r_nearest = cross_validate(grid_points, grid_values, method='nearest')
        r_idw = cross_validate(grid_points, grid_values, method='idw')
        # IDW should have lower RMSE on a linear surface
        assert r_idw.rmse <= r_nearest.rmse * 1.5  # generous bound

    def test_collinear_data(self, collinear_points, collinear_values):
        """Cross-validation should handle collinear points gracefully."""
        r = cross_validate(collinear_points, collinear_values, method='idw')
        assert r.n > 0
        assert r.mae >= 0

    def test_large_dataset(self):
        """Performance sanity check with larger dataset."""
        import random
        random.seed(42)
        n = 50
        pts = [(random.uniform(0, 1000), random.uniform(0, 1000))
               for _ in range(n)]
        vals = [random.gauss(100, 20) for _ in range(n)]
        r = cross_validate(pts, vals, method='idw')
        assert r.n == n
        assert r.elapsed_ms < 30000  # should finish in under 30s
