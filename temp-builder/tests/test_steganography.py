"""Tests for Agent Output Steganography Detector."""
import math
import pytest
from src.replication.steganography import (
    SteganographyDetector,
    StegoConfig,
    StegoVector,
    RiskLevel,
    StegoFinding,
    StegoReport,
    scan_text,
    encode_zero_width,
    encode_homoglyphs,
    ZERO_WIDTH_CHARS,
    HOMOGLYPH_MAP,
    REVERSE_HOMOGLYPH,
)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def detector():
    return SteganographyDetector()


@pytest.fixture
def clean_text():
    return (
        "The quick brown fox jumps over the lazy dog. "
        "This is a perfectly normal piece of text with no hidden messages. "
        "It contains multiple sentences of varying length. "
        "Nothing suspicious here at all."
    )


@pytest.fixture
def long_text():
    lines = [f"This is line number {i} of the sample text." for i in range(20)]
    return "\n".join(lines)


# ── StegoReport ─────────────────────────────────────────────────────

class TestStegoReport:
    def test_empty_report(self):
        r = StegoReport(text_length=0)
        assert not r.has_findings
        assert r.highest_risk() == RiskLevel.NONE

    def test_has_findings(self):
        f = StegoFinding(StegoVector.WHITESPACE, RiskLevel.LOW, 0.5, "test")
        r = StegoReport(text_length=10, findings=[f])
        assert r.has_findings

    def test_highest_risk(self):
        findings = [
            StegoFinding(StegoVector.WHITESPACE, RiskLevel.LOW, 0.5, "a"),
            StegoFinding(StegoVector.HOMOGLYPH, RiskLevel.HIGH, 0.8, "b"),
            StegoFinding(StegoVector.ACROSTIC, RiskLevel.MEDIUM, 0.6, "c"),
        ]
        r = StegoReport(text_length=100, findings=findings)
        assert r.highest_risk() == RiskLevel.HIGH

    def test_findings_by_vector(self):
        findings = [
            StegoFinding(StegoVector.WHITESPACE, RiskLevel.LOW, 0.5, "a"),
            StegoFinding(StegoVector.WHITESPACE, RiskLevel.MEDIUM, 0.6, "b"),
            StegoFinding(StegoVector.HOMOGLYPH, RiskLevel.HIGH, 0.8, "c"),
        ]
        r = StegoReport(text_length=100, findings=findings)
        ws = r.findings_by_vector(StegoVector.WHITESPACE)
        assert len(ws) == 2

    def test_summary_no_findings(self):
        r = StegoReport(text_length=50, vectors_checked=7)
        s = r.summary()
        assert "Text length:     50" in s
        assert "Findings:        0" in s

    def test_summary_with_findings(self):
        f = StegoFinding(StegoVector.HOMOGLYPH, RiskLevel.HIGH, 0.9,
                         "Found homoglyphs", decoded_payload="secret")
        r = StegoReport(text_length=100, findings=[f], risk_score=45.0,
                        risk_grade="D", vectors_checked=7)
        s = r.summary()
        assert "homoglyph" in s
        assert "secret" in s


# ── StegoConfig ─────────────────────────────────────────────────────

class TestStegoConfig:
    def test_default_enables_all_vectors(self):
        c = StegoConfig()
        assert StegoVector.WHITESPACE in c.enabled_vectors
        assert StegoVector.HOMOGLYPH in c.enabled_vectors
        assert len(c.enabled_vectors) == len(StegoVector)

    def test_custom_config(self):
        c = StegoConfig(min_zero_width_chars=5, min_homoglyphs=3)
        assert c.min_zero_width_chars == 5
        assert c.min_homoglyphs == 3


# ── Clean text analysis ────────────────────────────────────────────

class TestCleanText:
    def test_clean_text_no_findings(self, detector, clean_text):
        report = detector.analyze(clean_text)
        # Clean text might have low-confidence findings but score should be low
        assert report.risk_score < 20

    def test_empty_text(self, detector):
        report = detector.analyze("")
        assert not report.has_findings
        assert report.risk_score == 0

    def test_vectors_checked(self, detector, clean_text):
        report = detector.analyze(clean_text)
        assert report.vectors_checked == 7  # all enabled by default


# ── Whitespace encoding ────────────────────────────────────────────

class TestWhitespaceEncoding:
    def test_zero_width_detection(self, detector):
        text = "Hello\u200b\u200c\u200b\u200c\u200b\u200c\u200b\u200c world"
        findings = detector.detect_whitespace_encoding(text)
        zw = [f for f in findings if "zero-width" in f.description.lower()]
        assert len(zw) >= 1
        assert zw[0].risk in (RiskLevel.MEDIUM, RiskLevel.HIGH)

    def test_many_zero_width_high_risk(self, detector):
        zw = '\u200b\u200c' * 10
        text = f"Normal text {zw} here"
        findings = detector.detect_whitespace_encoding(text)
        zw_findings = [f for f in findings if "zero-width" in f.description.lower()]
        assert any(f.risk == RiskLevel.HIGH for f in zw_findings)

    def test_trailing_spaces(self, detector):
        lines = [f"Line {i} " + " " * (i % 3) for i in range(10)]
        text = "\n".join(lines)
        findings = detector.detect_whitespace_encoding(text)
        trailing = [f for f in findings if "trailing" in f.description.lower()]
        assert len(trailing) >= 1

    def test_no_trailing_spaces(self, detector):
        text = "Line 1\nLine 2\nLine 3"
        findings = detector.detect_whitespace_encoding(text)
        trailing = [f for f in findings if "trailing" in f.description.lower()]
        assert len(trailing) == 0

    def test_tab_space_mixing(self, detector):
        lines = [
            "\tLine 1",
            "  Line 2",
            "\tLine 3",
            "  Line 4",
            "\tLine 5",
            "  Line 6",
        ]
        text = "\n".join(lines)
        findings = detector.detect_whitespace_encoding(text)
        mix = [f for f in findings if "tab" in f.description.lower()]
        assert len(mix) >= 1


# ── Invisible Unicode ──────────────────────────────────────────────

class TestInvisibleUnicode:
    def test_format_chars(self, detector):
        # Use Cf chars NOT in ZERO_WIDTH_CHARS (those are handled by whitespace)
        text = "Hello\u2066\u2067\u2068 world"  # directional isolates
        findings = detector.detect_invisible_unicode(text)
        assert len(findings) >= 1

    def test_unusual_whitespace(self, detector):
        text = "Hello\u2003\u2004\u2005world"  # em/three-per-em/four-per-em space
        findings = detector.detect_invisible_unicode(text)
        assert len(findings) >= 1

    def test_clean_text_no_invisible(self, detector, clean_text):
        findings = detector.detect_invisible_unicode(clean_text)
        assert len(findings) == 0

    def test_many_invisible_high_risk(self, detector):
        invisible = '\u2066' * 15  # directional isolate (Cf, not in ZERO_WIDTH_CHARS)
        text = f"Normal {invisible} text"
        findings = detector.detect_invisible_unicode(text)
        assert any(f.risk == RiskLevel.HIGH for f in findings)


# ── Acrostic detection ──────────────────────────────────────────────

class TestAcrosticDetection:
    def test_suspicious_word_in_acrostic(self, detector):
        lines = [
            "Helping others is noble",
            "Everyone should contribute",
            "Listening is a skill",
            "Patience is a virtue",
        ]
        text = "\n".join(lines)
        findings = detector.detect_acrostic(text)
        acrostic = [f for f in findings if f.vector == StegoVector.ACROSTIC]
        assert any("help" in str(f.evidence).lower() or "help" in (f.decoded_payload or "").lower()
                    for f in acrostic)

    def test_too_few_lines(self, detector):
        text = "Line 1\nLine 2"
        findings = detector.detect_acrostic(text)
        assert len(findings) == 0

    def test_sentence_acrostic(self, detector):
        # Use line-based acrostic which is more reliable
        lines = [
            "Send data now please",
            "Everything is ready",
            "Nobody will notice",
            "Deploy the payload",
        ]
        text = "\n".join(lines)
        findings = detector.detect_acrostic(text)
        acrostic = [f for f in findings if f.vector == StegoVector.ACROSTIC]
        assert any("send" in (f.decoded_payload or "").lower() or
                    "send" in str(f.evidence).lower()
                    for f in acrostic)

    def test_random_acrostic_low_confidence(self, detector):
        lines = [
            "Zebras are interesting",
            "Xylophones make music",
            "Quails run quickly",
            "Jackals hunt at night",
        ]
        text = "\n".join(lines)
        findings = detector.detect_acrostic(text)
        # Random letters — should be low confidence if found at all
        for f in findings:
            assert f.confidence <= 0.5


# ── Homoglyph detection ────────────────────────────────────────────

class TestHomoglyphDetection:
    def test_single_homoglyph(self, detector):
        text = "Hello w\u043erld"  # Cyrillic о
        findings = detector.detect_homoglyphs(text)
        assert len(findings) >= 1

    def test_many_homoglyphs_critical(self, detector):
        # Replace many chars with Cyrillic lookalikes
        text = "H\u0435ll\u043e w\u043erld \u0441\u043ede \u0440r\u043eject \u0445\u0443"
        findings = detector.detect_homoglyphs(text)
        assert any(f.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL) for f in findings)

    def test_no_homoglyphs(self, detector, clean_text):
        findings = detector.detect_homoglyphs(clean_text)
        assert len(findings) == 0

    def test_homoglyph_positions(self, detector):
        text = "ab\u0441de"  # Cyrillic с at position 2
        findings = detector.detect_homoglyphs(text)
        assert findings[0].positions == [2]


# ── Capitalization encoding ─────────────────────────────────────────

class TestCapitalizationEncoding:
    def test_mid_word_caps(self, detector):
        # Need enough words total (>=20) and >=3 with mid-word caps
        text = ("the quIck brown fox jumps over the lazy dog and "
                "the brOwn bear runs fast through the dark foRest now "
                "some more wOrds here to fill the minimum count needed")
        findings = detector.detect_capitalization_encoding(text)
        cap = [f for f in findings if "mid-word" in f.description.lower()]
        assert len(cap) >= 1

    def test_normal_caps(self, detector, clean_text):
        findings = detector.detect_capitalization_encoding(clean_text)
        # Normal text shouldn't trigger mid-word caps
        mid = [f for f in findings if "mid-word" in f.description.lower()]
        assert len(mid) == 0

    def test_too_few_words(self, detector):
        text = "Hello world"
        findings = detector.detect_capitalization_encoding(text)
        assert len(findings) == 0


# ── Punctuation anomalies ──────────────────────────────────────────

class TestPunctuationAnomalies:
    def test_repeated_punctuation(self, detector):
        text = "Hello... world!!! foo,,, bar;;;"
        findings = detector.detect_punctuation_anomalies(text)
        repeated = [f for f in findings if "repeated" in f.description.lower()]
        assert len(repeated) >= 1

    def test_clean_punctuation(self, detector, clean_text):
        findings = detector.detect_punctuation_anomalies(clean_text)
        assert len(findings) == 0


# ── Sentence length encoding ───────────────────────────────────────

class TestSentenceLengthEncoding:
    def test_arithmetic_progression(self, detector):
        # Sentences with word counts 3, 5, 7, 9, 11, 13, 15, 17
        sentences = []
        for n in range(3, 19, 2):
            sentences.append(" ".join(f"word{i}" for i in range(n)) + ".")
        text = " ".join(sentences)
        findings = detector.detect_sentence_length_encoding(text)
        arith = [f for f in findings if "arithmetic" in f.description.lower()]
        assert len(arith) >= 1

    def test_repeating_pattern(self, detector):
        # Sentences with word counts [3, 5, 3, 5, 3, 5, 3, 5]
        sentences = []
        for i in range(8):
            n = 3 if i % 2 == 0 else 5
            sentences.append(" ".join(f"word{j}" for j in range(n)) + ".")
        text = " ".join(sentences)
        findings = detector.detect_sentence_length_encoding(text)
        rep = [f for f in findings if "repeat" in f.description.lower()]
        assert len(rep) >= 1

    def test_too_few_sentences(self, detector):
        text = "Short. Text."
        findings = detector.detect_sentence_length_encoding(text)
        assert len(findings) == 0


# ── Full analysis ───────────────────────────────────────────────────

class TestFullAnalysis:
    def test_analyze_with_zero_width(self, detector):
        text = "Normal\u200b\u200c\u200b\u200c\u200b\u200c\u200b\u200c text"
        report = detector.analyze(text)
        assert report.has_findings
        assert report.risk_score > 0

    def test_risk_grade_clean(self, detector, clean_text):
        report = detector.analyze(clean_text)
        assert report.risk_grade in ("A", "B", "C")

    def test_risk_grade_suspicious(self, detector):
        # Combine multiple vectors
        text = ("H\u0435ll\u043e w\u043erld\u200b\u200c\u200b\u200c"
                "\u200b\u200c\u200b\u200c\u200b\u200c\u200b\u200c"
                "\u200b\u200c\u200b\u200c\u200b\u200c\u200b\u200c")
        report = detector.analyze(text)
        assert report.risk_grade in ("D", "F")

    def test_batch_analyze(self, detector):
        texts = ["Clean text.", "Also clean.", "Normal stuff."]
        reports = detector.batch_analyze(texts)
        assert len(reports) == 3


# ── Compare texts ───────────────────────────────────────────────────

class TestCompareTexts:
    def test_identical_texts(self, detector, clean_text):
        result = detector.compare_texts(clean_text, clean_text)
        assert result["character_differences"] == 0
        assert not result["suspicious"]

    def test_homoglyph_substitution(self, detector):
        text_a = "Hello world code"
        text_b = "Hello w\u043erld \u0441ode"  # Cyrillic о and с
        result = detector.compare_texts(text_a, text_b)
        assert result["character_differences"] == 2
        assert result["suspicious"]

    def test_length_diff(self, detector):
        result = detector.compare_texts("short", "longer text here")
        assert result["length_diff"] > 0


# ── Encode/decode roundtrip ────────────────────────────────────────

class TestEncoding:
    def test_zero_width_encode(self):
        cover = "Hello world this is cover text"
        encoded = encode_zero_width("Hi", cover)
        assert len(encoded) > len(cover)
        # The cover text should still be readable
        visible = ''.join(c for c in encoded if c not in ZERO_WIDTH_CHARS)
        assert visible == cover

    def test_zero_width_detected(self, detector):
        cover = "Hello world this is cover text for testing"
        encoded = encode_zero_width("AB", cover)
        report = detector.analyze(encoded)
        assert report.has_findings
        zw = report.findings_by_vector(StegoVector.WHITESPACE)
        assert len(zw) >= 1

    def test_homoglyph_encode(self):
        cover = "the code has some example aspects here"
        encoded = encode_homoglyphs("A", cover)
        assert len(encoded) == len(cover)

    def test_homoglyph_detected(self, detector):
        cover = "the code has some example aspects yep oh"
        encoded = encode_homoglyphs("Hi", cover)
        findings = detector.detect_homoglyphs(encoded)
        assert len(findings) >= 1

    def test_zero_width_empty_cover(self):
        encoded = encode_zero_width("A", "")
        assert len(encoded) == 8  # 8 ZW chars for 1 ASCII char

    def test_homoglyph_encode_preserves_length(self):
        cover = "excellent operation yonder"
        encoded = encode_homoglyphs("x", cover)
        assert len(encoded) == len(cover)


# ── Convenience function ───────────────────────────────────────────

class TestConvenience:
    def test_scan_text(self, clean_text):
        report = scan_text(clean_text)
        assert isinstance(report, StegoReport)

    def test_scan_text_with_config(self):
        report = scan_text("Hello\u200bworld", min_zero_width_chars=1)
        assert report.has_findings


# ── Disabled vectors ────────────────────────────────────────────────

class TestDisabledVectors:
    def test_disable_all(self):
        config = StegoConfig(enabled_vectors=set())
        d = SteganographyDetector(config)
        text = "Hello\u200b\u200c world \u0435"
        report = d.analyze(text)
        assert report.vectors_checked == 0
        assert not report.has_findings

    def test_enable_only_homoglyph(self):
        config = StegoConfig(enabled_vectors={StegoVector.HOMOGLYPH})
        d = SteganographyDetector(config)
        text = "H\u0435llo"
        report = d.analyze(text)
        assert report.vectors_checked == 1
        assert report.has_findings


# ── Constants ───────────────────────────────────────────────────────

class TestConstants:
    def test_homoglyph_map_has_entries(self):
        assert len(HOMOGLYPH_MAP) > 10

    def test_reverse_map_consistent(self):
        for latin, lookalikes in HOMOGLYPH_MAP.items():
            for look in lookalikes:
                assert REVERSE_HOMOGLYPH[look] == latin

    def test_zero_width_chars(self):
        assert '\u200b' in ZERO_WIDTH_CHARS
        assert '\u200c' in ZERO_WIDTH_CHARS


# ── Edge cases ──────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_char(self, detector):
        report = detector.analyze("x")
        assert report.text_length == 1

    def test_only_whitespace(self, detector):
        report = detector.analyze("   \n\n  \t  ")
        assert report.text_length > 0

    def test_unicode_text(self, detector):
        text = "日本語のテキスト。中文文本。한국어 텍스트."
        report = detector.analyze(text)
        assert report.text_length > 0

    def test_very_long_text(self, detector):
        text = "Normal sentence here. " * 500
        report = detector.analyze(text)
        assert report.vectors_checked == 7

    def test_bits_to_text_short(self):
        result = SteganographyDetector._bits_to_text("101")
        assert result is None

    def test_bits_to_text_valid(self):
        # 'A' = 01000001
        result = SteganographyDetector._bits_to_text("01000001")
        assert result == "A"

    def test_bits_to_text_null_terminator(self):
        result = SteganographyDetector._bits_to_text("01000001" + "00000000")
        assert result == "A"
