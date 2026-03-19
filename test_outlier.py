"""Tests for vormap_outlier — spatial outlier detection."""

import math
import pytest
from vormap_outlier import (
    detect_outliers,
    OutlierResult,
    VALID_METRICS,
    DEFAULT_METRICS,
)


# ── Helper: build fake region stats ─────────────────────────────────

def _make_stats(areas, compactnesses=None):
    """Build a list of region stat dicts from area values."""
    if compactnesses is None:
        compactnesses = [0.5] * len(areas)
    stats = []
    for i, (a, c) in enumerate(zip(areas, compactnesses)):
        stats.append({
            "region_index": i + 1,
            "seed_x": float(i * 10),
            "seed_y": float(i * 10),
            "area": float(a),
            "perimeter": math.sqrt(a) * 4,
            "compactness": float(c),
            "vertex_count": 6,
            "avg_edge_length": 1.0,
        })
    return stats


# ── detect_outliers (zscore) ────────────────────────────────────────

class TestDetectOutliersZscore:
    def test_no_outliers_uniform(self):
        stats = _make_stats([10, 10, 10, 10, 10])
        result = detect_outliers(stats, metrics=["area"])
        assert result.outlier_count == 0
        assert result.total_regions == 5
        assert result.method == "zscore"

    def test_detects_high_outlier(self):
        # 9 normal values near 10, one extreme at 1000
        areas = [10, 10, 11, 9, 10, 10, 11, 9, 10, 1000]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], threshold=2.0)
        assert result.outlier_count >= 1
        outlier_indices = [o["region_index"] for o in result.outliers]
        assert 10 in outlier_indices  # the 1000-area region

    def test_detects_low_outlier(self):
        areas = [100, 100, 100, 100, 100, 100, 100, 100, 100, 0.1]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], threshold=2.0)
        outlier_flags = {o["region_index"]: o["flags"] for o in result.outliers}
        assert 10 in outlier_flags
        assert outlier_flags[10]["area"] == "low"

    def test_stricter_threshold_catches_fewer(self):
        areas = [10, 10, 10, 10, 10, 10, 10, 10, 10, 30]
        stats = _make_stats(areas)
        result_loose = detect_outliers(stats, metrics=["area"], threshold=1.0)
        result_strict = detect_outliers(stats, metrics=["area"], threshold=3.0)
        assert result_loose.outlier_count >= result_strict.outlier_count

    def test_z_scores_populated(self):
        areas = [10, 10, 10, 10, 100]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"])
        for o in result.outliers:
            assert "area" in o["z_scores"]
            assert isinstance(o["z_scores"]["area"], float)

    def test_summary_has_expected_keys(self):
        stats = _make_stats([10, 20, 30])
        result = detect_outliers(stats, metrics=["area"])
        s = result.summary["area"]
        for key in ("mean", "std", "q1", "q3", "iqr", "lower_fence",
                    "upper_fence", "outlier_count_high", "outlier_count_low"):
            assert key in s


# ── detect_outliers (iqr) ───────────────────────────────────────────

class TestDetectOutliersIQR:
    def test_iqr_method(self):
        areas = [10, 10, 10, 10, 10, 10, 10, 10, 10, 1000]
        stats = _make_stats(areas)
        result = detect_outliers(stats, metrics=["area"], method="iqr")
        assert result.method == "iqr"
        assert result.outlier_count >= 1

    def test_iqr_default_threshold_is_1_5(self):
        stats = _make_stats([10, 20, 30])
        result = detect_outliers(stats, metrics=["area"], method="iqr")
        assert result.threshold == 1.5


# ── Multiple metrics ────────────────────────────────────────────────

class TestMultipleMetrics:
    def test_two_metrics(self):
        # Outlier in compactness but not area
        stats = _make_stats(
            areas=[10, 10, 10, 10, 10, 10, 10, 10, 10, 10],
            compactnesses=[0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.001],
        )
        result = detect_outliers(stats, metrics=["area", "compactness"])
        assert result.metrics_used == ["area", "compactness"]
        # Region 10 should be flagged for compactness
        flagged = [o for o in result.outliers if o["region_index"] == 10]
        assert len(flagged) >= 1
        assert "compactness" in flagged[0]["flags"]

    def test_default_metrics(self):
        stats = _make_stats([10, 20, 30])
        result = detect_outliers(stats)
        assert result.metrics_used == list(DEFAULT_METRICS)


# ── Validation errors ───────────────────────────────────────────────

class TestDetectOutliersValidation:
    def test_empty_stats_raises(self):
        with pytest.raises(ValueError, match="empty"):
            detect_outliers([])

    def test_invalid_metric_raises(self):
        stats = _make_stats([10])
        with pytest.raises(ValueError, match="Invalid metric"):
            detect_outliers(stats, metrics=["nonexistent"])

    def test_invalid_method_raises(self):
        stats = _make_stats([10])
        with pytest.raises(ValueError, match="Invalid method"):
            detect_outliers(stats, method="magic")


# ── OutlierResult properties ────────────────────────────────────────

class TestOutlierResult:
    def test_outlier_rate(self):
        r = OutlierResult()
        r.outliers = [{"region_index": 1}]
        r.normal = [{"region_index": 2}, {"region_index": 3}]
        assert r.outlier_rate == pytest.approx(1 / 3)

    def test_outlier_rate_empty(self):
        r = OutlierResult()
        assert r.outlier_rate == 0.0

    def test_format_report_runs(self):
        stats = _make_stats([10, 10, 10, 100])
        result = detect_outliers(stats, metrics=["area"])
        report = result.format_report()
        assert "Outlier Detection Report" in report
        assert "area" in report
