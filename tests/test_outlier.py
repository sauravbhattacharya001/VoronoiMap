"""Tests for vormap_outlier — spatial outlier detection."""

import json
import math
import os
import tempfile

import pytest

from vormap_outlier import (
    OutlierResult,
    detect_outliers,
    export_outlier_csv,
    export_outlier_json,
    export_outlier_svg,
    VALID_METRICS,
    DEFAULT_METRICS,
    _mean,
    _std,
    _percentile,
    _quartiles,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _make_stats(areas, compactnesses=None):
    """Build minimal region stat dicts for testing."""
    stats = []
    for i, area in enumerate(areas):
        s = {
            "region_index": i + 1,
            "seed_x": float(i * 100),
            "seed_y": float(i * 100),
            "area": float(area),
            "perimeter": float(math.sqrt(area) * 4),
            "compactness": compactnesses[i] if compactnesses else 0.785,
            "vertex_count": 6,
            "avg_edge_length": float(math.sqrt(area) * 4 / 6),
        }
        stats.append(s)
    return stats


# ── Statistical helpers ──────────────────────────────────────────────

class TestStatHelpers:
    def test_mean_basic(self):
        assert _mean([1, 2, 3, 4, 5]) == 3.0

    def test_mean_single(self):
        assert _mean([42]) == 42.0

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std_basic(self):
        vals = [2, 4, 4, 4, 5, 5, 7, 9]
        s = _std(vals)
        assert s > 0
        assert abs(s - 2.0) < 0.01

    def test_std_identical(self):
        assert _std([5, 5, 5, 5]) == 0.0

    def test_std_single(self):
        assert _std([5]) == 0.0

    def test_percentile_median(self):
        vals = sorted([1, 2, 3, 4, 5])
        assert _percentile(vals, 50) == 3.0

    def test_percentile_empty(self):
        assert _percentile([], 50) == 0.0

    def test_percentile_extremes(self):
        vals = sorted([10, 20, 30, 40, 50])
        assert _percentile(vals, 0) == 10.0
        assert _percentile(vals, 100) == 50.0

    def test_quartiles(self):
        q1, med, q3 = _quartiles([1, 2, 3, 4, 5, 6, 7, 8])
        assert q1 < med < q3


# ── OutlierResult ────────────────────────────────────────────────────

class TestOutlierResult:
    def test_empty_result(self):
        r = OutlierResult()
        assert r.total_regions == 0
        assert r.outlier_count == 0
        assert r.outlier_rate == 0.0

    def test_counts(self):
        r = OutlierResult(
            outliers=[{"region_index": 1}],
            normal=[{"region_index": 2}, {"region_index": 3}],
        )
        assert r.total_regions == 3
        assert r.outlier_count == 1
        assert abs(r.outlier_rate - 1 / 3) < 0.01

    def test_format_report_not_empty(self):
        r = OutlierResult(
            outliers=[{
                "region_index": 1,
                "seed": (100.0, 200.0),
                "flags": {"area": "high"},
                "values": {"area": 9999.0},
                "z_scores": {"area": 3.5},
            }],
            normal=[{
                "region_index": 2,
                "seed": (50.0, 50.0),
                "flags": {},
                "values": {"area": 100.0},
                "z_scores": {"area": -0.2},
            }],
            method="zscore",
            threshold=2.0,
            metrics_used=["area"],
            summary={"area": {
                "mean": 500.0, "std": 200.0,
                "q1": 100.0, "q3": 700.0,
                "outlier_count_high": 1, "outlier_count_low": 0,
            }},
        )
        report = r.format_report()
        assert "Spatial Outlier Detection" in report
        assert "zscore" in report
        assert "Region   1" in report
        assert "area=high" in report


# ── detect_outliers — validation ─────────────────────────────────────

class TestDetectOutliersValidation:
    def test_empty_stats_raises(self):
        with pytest.raises(ValueError, match="empty"):
            detect_outliers([])

    def test_invalid_metric_raises(self):
        stats = _make_stats([100])
        with pytest.raises(ValueError, match="Invalid metric"):
            detect_outliers(stats, metrics=["bogus"])

    def test_invalid_method_raises(self):
        stats = _make_stats([100])
        with pytest.raises(ValueError, match="Invalid method"):
            detect_outliers(stats, method="kmeans")

    def test_default_metrics(self):
        stats = _make_stats([100, 200, 300])
        result = detect_outliers(stats)
        assert result.metrics_used == list(DEFAULT_METRICS)


# ── detect_outliers — Z-score ────────────────────────────────────────

class TestDetectZScore:
    def test_no_outliers_uniform(self):
        """Uniform areas → no outliers."""
        stats = _make_stats([100, 100, 100, 100, 100])
        result = detect_outliers(stats, metrics=["area"], method="zscore")
        assert result.outlier_count == 0
        assert result.method == "zscore"

    def test_extreme_high_detected(self):
        """One region 100x larger → flagged high."""
        areas = [100] * 10 + [10000]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], method="zscore",
                                 threshold=2.0)
        assert result.outlier_count >= 1
        flagged_indices = {o["region_index"] for o in result.outliers}
        assert 11 in flagged_indices
        for o in result.outliers:
            if o["region_index"] == 11:
                assert o["flags"]["area"] == "high"

    def test_extreme_low_detected(self):
        """One tiny region among large ones → flagged low."""
        areas = [1000] * 10 + [1]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], method="zscore",
                                 threshold=2.0)
        flagged_indices = {o["region_index"] for o in result.outliers}
        assert 11 in flagged_indices
        for o in result.outliers:
            if o["region_index"] == 11:
                assert o["flags"]["area"] == "low"

    def test_threshold_sensitivity(self):
        """Lower threshold catches more outliers."""
        areas = [100, 100, 100, 100, 200]
        stats = _make_stats(areas)
        r_strict = detect_outliers(stats, metrics=["area"], threshold=3.0)
        r_loose = detect_outliers(stats, metrics=["area"], threshold=0.5)
        assert r_loose.outlier_count >= r_strict.outlier_count

    def test_z_scores_populated(self):
        """Z-score values are stored on each region."""
        stats = _make_stats([100, 200, 300])
        result = detect_outliers(stats, metrics=["area"])
        all_regions = result.outliers + result.normal
        for r in all_regions:
            assert "area" in r["z_scores"]

    def test_multiple_metrics(self):
        """Outlier on one metric but not another."""
        areas = [100] * 10 + [10000]
        compactnesses = [0.8] * 11  # all uniform
        stats = _make_stats(areas, compactnesses)
        result = detect_outliers(stats,
                                 metrics=["area", "compactness"],
                                 threshold=2.0)
        for o in result.outliers:
            if o["region_index"] == 11:
                assert "area" in o["flags"]
                assert "compactness" not in o["flags"]

    def test_single_region(self):
        """Single region → no outlier (can't be unusual alone)."""
        stats = _make_stats([500])
        result = detect_outliers(stats, metrics=["area"])
        assert result.outlier_count == 0
        assert result.total_regions == 1


# ── detect_outliers — IQR ────────────────────────────────────────────

class TestDetectIQR:
    def test_no_outliers_uniform(self):
        stats = _make_stats([100, 100, 100, 100, 100])
        result = detect_outliers(stats, metrics=["area"], method="iqr")
        assert result.outlier_count == 0
        assert result.method == "iqr"

    def test_extreme_detected(self):
        areas = [100] * 10 + [10000]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], method="iqr")
        assert result.outlier_count >= 1

    def test_iqr_threshold_changes_sensitivity(self):
        areas = [100, 100, 100, 100, 300]
        stats = _make_stats(areas)
        r_strict = detect_outliers(stats, metrics=["area"], method="iqr",
                                   threshold=3.0)
        r_loose = detect_outliers(stats, metrics=["area"], method="iqr",
                                  threshold=0.5)
        assert r_loose.outlier_count >= r_strict.outlier_count

    def test_summary_has_iqr_fields(self):
        stats = _make_stats([100, 200, 300, 400, 500])
        result = detect_outliers(stats, metrics=["area"], method="iqr")
        s = result.summary["area"]
        assert "iqr" in s
        assert "lower_fence" in s
        assert "upper_fence" in s
        assert s["q1"] <= s["q3"]


# ── Summary stats ────────────────────────────────────────────────────

class TestSummary:
    def test_summary_present_for_each_metric(self):
        stats = _make_stats([100, 200, 300])
        result = detect_outliers(stats, metrics=["area", "perimeter"])
        assert "area" in result.summary
        assert "perimeter" in result.summary

    def test_summary_contains_required_keys(self):
        stats = _make_stats([100, 200, 300])
        result = detect_outliers(stats, metrics=["area"])
        s = result.summary["area"]
        required = {"mean", "std", "median", "q1", "q3", "iqr",
                    "lower_fence", "upper_fence",
                    "outlier_count_high", "outlier_count_low"}
        assert required.issubset(set(s.keys()))

    def test_high_low_counts_sum(self):
        areas = [1] + [100] * 8 + [10000]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], threshold=2.0)
        s = result.summary["area"]
        total_flagged = s["outlier_count_high"] + s["outlier_count_low"]
        assert total_flagged == result.outlier_count


# ── All valid metrics ────────────────────────────────────────────────

class TestAllMetrics:
    @pytest.mark.parametrize("metric", VALID_METRICS)
    def test_each_metric_works(self, metric):
        stats = _make_stats([100, 200, 300, 400, 500])
        result = detect_outliers(stats, metrics=[metric])
        assert metric in result.summary
        assert result.total_regions == 5


# ── JSON export ──────────────────────────────────────────────────────

class TestExportJSON:
    def test_json_roundtrip(self):
        stats = _make_stats([100] * 5 + [10000])
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_outlier_json(result, path)
            with open(path) as f:
                data = json.load(f)
            assert data["method"] == "zscore"
            assert data["total_regions"] == 6
            assert data["outlier_count"] >= 1
            assert "summary" in data
            assert "outliers" in data
        finally:
            os.unlink(path)

    def test_json_has_outlier_details(self):
        stats = _make_stats([100] * 5 + [10000])
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            export_outlier_json(result, path)
            with open(path) as f:
                data = json.load(f)
            for o in data["outliers"]:
                assert "region_index" in o
                assert "seed_x" in o
                assert "flags" in o
                assert "z_scores" in o
        finally:
            os.unlink(path)


# ── CSV export ───────────────────────────────────────────────────────

class TestExportCSV:
    def test_csv_has_header_and_rows(self):
        stats = _make_stats([100, 200, 300])
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            export_outlier_csv(result, path)
            with open(path) as f:
                lines = f.read().strip().split("\n")
            assert len(lines) == 4  # header + 3 rows
            header = lines[0]
            assert "region_index" in header
            assert "is_outlier" in header
            assert "area" in header
            assert "area_zscore" in header
        finally:
            os.unlink(path)

    def test_csv_all_regions_present(self):
        stats = _make_stats([100] * 5 + [10000])
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False,
                                         mode="w") as f:
            path = f.name
        try:
            export_outlier_csv(result, path)
            with open(path) as f:
                lines = f.read().strip().split("\n")
            assert len(lines) == 7  # header + 6 rows
        finally:
            os.unlink(path)


# ── SVG export ───────────────────────────────────────────────────────

class TestExportSVG:
    def _make_regions_and_data(self):
        """Create simple square regions for SVG testing."""
        data = [(50, 50), (150, 50), (50, 150), (150, 150)]
        regions = {
            (50, 50): [(0, 0), (100, 0), (100, 100), (0, 100)],
            (150, 50): [(100, 0), (200, 0), (200, 100), (100, 100)],
            (50, 150): [(0, 100), (100, 100), (100, 200), (0, 200)],
            (150, 150): [(100, 100), (200, 100), (200, 200), (100, 200)],
        }
        return regions, data

    def test_svg_created(self):
        regions, data = self._make_regions_and_data()
        stats = _make_stats([100, 100, 100, 10000])
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_outlier_svg(result, regions, data, path)
            content = open(path).read()
            assert "<svg" in content
            assert "polygon" in content
            assert "circle" in content
        finally:
            os.unlink(path)

    def test_svg_has_legend(self):
        regions, data = self._make_regions_and_data()
        stats = _make_stats([100, 100, 100, 100])
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_outlier_svg(result, regions, data, path)
            content = open(path).read()
            assert "Outlier" in content
            assert "Normal" in content
        finally:
            os.unlink(path)

    def test_svg_empty_regions(self):
        """Empty regions dict → valid SVG without crash."""
        result = OutlierResult()
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_outlier_svg(result, {}, [], path)
            content = open(path).read()
            assert "<svg" in content
        finally:
            os.unlink(path)

    def test_svg_custom_dimensions(self):
        regions, data = self._make_regions_and_data()
        stats = _make_stats([100] * 4)
        result = detect_outliers(stats, metrics=["area"])
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            path = f.name
        try:
            export_outlier_svg(result, regions, data, path,
                               width=1200, height=900)
            content = open(path).read()
            assert 'width="1200"' in content
            assert 'height="900"' in content
        finally:
            os.unlink(path)
