"""Tests for vormap_cvd — Color Vision Deficiency simulator."""

import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_cvd import (
    hex_to_rgb,
    rgb_to_hex,
    srgb_to_linear,
    linear_to_srgb,
    color_distance,
    relative_luminance,
    contrast_ratio,
    simulate_cvd,
    simulate_hex,
    check_palette,
    suggest_palette,
    CVD_MATRICES,
    SAFE_PALETTES,
    WONG_PALETTE,
)


# ── hex_to_rgb / rgb_to_hex ─────────────────────────────────────────

class TestColorConversion:
    def test_hex_to_rgb_full(self):
        assert hex_to_rgb("#ff0000") == (255, 0, 0)
        assert hex_to_rgb("#00ff00") == (0, 255, 0)
        assert hex_to_rgb("#0000ff") == (0, 0, 255)

    def test_hex_to_rgb_short(self):
        assert hex_to_rgb("#fff") == (255, 255, 255)
        assert hex_to_rgb("#000") == (0, 0, 0)

    def test_hex_to_rgb_no_hash(self):
        assert hex_to_rgb("ff8800") == (255, 136, 0)

    def test_rgb_to_hex(self):
        assert rgb_to_hex(255, 0, 0) == "#ff0000"
        assert rgb_to_hex(0, 0, 0) == "#000000"

    def test_rgb_to_hex_clamping(self):
        assert rgb_to_hex(300, -10, 128) == "#ff0080"

    def test_roundtrip(self):
        for hexc in ["#e41a1c", "#377eb8", "#4daf4a"]:
            assert rgb_to_hex(*hex_to_rgb(hexc)) == hexc


# ── sRGB linearization ──────────────────────────────────────────────

class TestLinearization:
    def test_black(self):
        assert srgb_to_linear(0) == 0.0

    def test_white(self):
        assert abs(srgb_to_linear(255) - 1.0) < 1e-6

    def test_roundtrip(self):
        for v in [0, 10, 50, 128, 200, 255]:
            assert linear_to_srgb(srgb_to_linear(v)) == v

    def test_linear_to_srgb_clamping(self):
        assert linear_to_srgb(-0.5) == 0
        assert linear_to_srgb(1.5) == 255


# ── color_distance ──────────────────────────────────────────────────

class TestColorDistance:
    def test_same_color(self):
        assert color_distance((128, 128, 128), (128, 128, 128)) == 0.0

    def test_positive(self):
        d = color_distance((255, 0, 0), (0, 0, 255))
        assert d > 0

    def test_symmetric(self):
        c1, c2 = (100, 50, 200), (200, 100, 50)
        assert abs(color_distance(c1, c2) - color_distance(c2, c1)) < 1e-10


# ── luminance and contrast ──────────────────────────────────────────

class TestLuminance:
    def test_black_luminance(self):
        assert relative_luminance(0, 0, 0) == 0.0

    def test_white_luminance(self):
        assert abs(relative_luminance(255, 255, 255) - 1.0) < 1e-4

    def test_contrast_black_white(self):
        cr = contrast_ratio((0, 0, 0), (255, 255, 255))
        assert abs(cr - 21.0) < 0.1

    def test_contrast_same(self):
        cr = contrast_ratio((128, 128, 128), (128, 128, 128))
        assert abs(cr - 1.0) < 1e-6


# ── CVD simulation ──────────────────────────────────────────────────

class TestSimulateCVD:
    def test_black_unchanged(self):
        for cvd_type in ["protanopia", "deuteranopia", "tritanopia", "achromatopsia"]:
            assert simulate_cvd(0, 0, 0, cvd_type) == (0, 0, 0)

    def test_white_near_white(self):
        for cvd_type in ["protanopia", "deuteranopia", "tritanopia", "achromatopsia"]:
            r, g, b = simulate_cvd(255, 255, 255, cvd_type)
            assert all(v >= 250 for v in (r, g, b))

    def test_achromatopsia_greyscale(self):
        r, g, b = simulate_cvd(255, 0, 0, "achromatopsia")
        assert r == g == b  # should be grey

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown CVD type"):
            simulate_cvd(128, 128, 128, "nonexistent")

    def test_simulate_hex(self):
        result = simulate_hex("#ff0000", "deuteranopia")
        assert result.startswith("#")
        assert len(result) == 7

    def test_all_cvd_types_produce_valid_rgb(self):
        for cvd_type in CVD_MATRICES:
            r, g, b = simulate_cvd(200, 100, 50, cvd_type)
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255


# ── Palette checking ────────────────────────────────────────────────

class TestCheckPalette:
    def test_check_wong_palette(self):
        report = check_palette(WONG_PALETTE)
        assert hasattr(report, "overall_safe")
        assert hasattr(report, "grade")

    def test_check_confusable(self):
        # Two very similar reds should be confusable
        report = check_palette(["#ff0000", "#fe0101"])
        assert report.grade != "A"


# ── Palette suggestion ──────────────────────────────────────────────

class TestSuggestPalette:
    def test_suggest_returns_correct_count(self):
        for n in range(2, 9):
            palette = suggest_palette(n)
            assert len(palette) == n

    def test_suggest_returns_hex_strings(self):
        palette = suggest_palette(4)
        for c in palette:
            assert c.startswith("#")
            assert len(c) == 7

    def test_safe_palettes_keys(self):
        for n in range(2, 9):
            assert n in SAFE_PALETTES
