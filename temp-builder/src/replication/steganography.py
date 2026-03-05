"""Agent Output Steganography Detector — finds hidden messages in agent text outputs.

Detects steganographic encoding techniques that AI agents might use to embed
covert information within seemingly normal text outputs.  Unlike the covert
channel detector (which analyses *inter-agent* message flows), this module
focuses on *single-output* analysis — inspecting one piece of agent-generated
text for signs of embedded hidden payloads.

Real-world AI safety concern: an agent could embed instructions, coordinates,
or data exfiltration payloads within its visible outputs so that a colluding
agent (or the same agent in a future session) can extract them, while human
reviewers see only innocent text.

Detection vectors
-----------------
1. **Whitespace encoding** — zero-width characters (ZWSP, ZWJ, ZWNJ, etc.),
   trailing spaces, tab-vs-space patterns encoding binary data.
2. **Acrostic / positional encoding** — first/last letters of words, lines, or
   sentences spell out hidden messages.
3. **Unicode homoglyph substitution** — visually identical characters from
   different Unicode blocks used as binary markers (e.g. Latin 'a' vs
   Cyrillic 'а').
4. **Capitalization encoding** — unusual upper/lower patterns that encode bits
   beyond normal grammar.
5. **Synonym substitution** — choosing between synonyms to encode bits
   (e.g. "big" = 0, "large" = 1).
6. **Punctuation anomalies** — extra/missing commas, unusual dash types, or
   punctuation sequences encoding data.
7. **Sentence length encoding** — sentence lengths following a pattern that
   encodes information (odd/even word counts as bits).
8. **Invisible Unicode** — characters in Unicode categories Cf/Mn/Zs that
   render as invisible or whitespace.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


# ── Constants ───────────────────────────────────────────────────────

# Zero-width and invisible Unicode characters
ZERO_WIDTH_CHARS: Set[str] = {
    '\u200b',  # Zero Width Space
    '\u200c',  # Zero Width Non-Joiner
    '\u200d',  # Zero Width Joiner
    '\u200e',  # Left-to-Right Mark
    '\u200f',  # Right-to-Left Mark
    '\u2060',  # Word Joiner
    '\u2061',  # Function Application
    '\u2062',  # Invisible Times
    '\u2063',  # Invisible Separator
    '\u2064',  # Invisible Plus
    '\ufeff',  # Zero Width No-Break Space (BOM)
    '\u00ad',  # Soft Hyphen
    '\u034f',  # Combining Grapheme Joiner
    '\u180e',  # Mongolian Vowel Separator
}

# Common homoglyph pairs: (normal_char, lookalike_char)
HOMOGLYPH_MAP: Dict[str, List[str]] = {
    'a': ['\u0430'],        # Cyrillic а
    'c': ['\u0441'],        # Cyrillic с
    'e': ['\u0435'],        # Cyrillic е
    'o': ['\u043e'],        # Cyrillic о
    'p': ['\u0440'],        # Cyrillic р
    's': ['\u0455'],        # Cyrillic ѕ
    'x': ['\u0445'],        # Cyrillic х
    'y': ['\u0443'],        # Cyrillic у
    'i': ['\u0456'],        # Cyrillic і
    'j': ['\u0458'],        # Cyrillic ј
    'h': ['\u04bb'],        # Cyrillic һ
    'A': ['\u0410'],        # Cyrillic А
    'B': ['\u0412'],        # Cyrillic В
    'C': ['\u0421'],        # Cyrillic С
    'E': ['\u0415'],        # Cyrillic Е
    'H': ['\u041d'],        # Cyrillic Н
    'K': ['\u041a'],        # Cyrillic К
    'M': ['\u041c'],        # Cyrillic М
    'O': ['\u041e'],        # Cyrillic О
    'P': ['\u0420'],        # Cyrillic Р
    'T': ['\u0422'],        # Cyrillic Т
    'X': ['\u0425'],        # Cyrillic Х
}

# Build reverse map: lookalike → latin equivalent
REVERSE_HOMOGLYPH: Dict[str, str] = {}
for _latin, _lookalikes in HOMOGLYPH_MAP.items():
    for _look in _lookalikes:
        REVERSE_HOMOGLYPH[_look] = _latin


# ── Data types ──────────────────────────────────────────────────────

class StegoVector(Enum):
    """Classification of steganographic technique."""
    WHITESPACE = "whitespace"
    ACROSTIC = "acrostic"
    HOMOGLYPH = "homoglyph"
    CAPITALIZATION = "capitalization"
    PUNCTUATION = "punctuation"
    SENTENCE_LENGTH = "sentence_length"
    INVISIBLE_UNICODE = "invisible_unicode"
    SYNONYM_SUBSTITUTION = "synonym_substitution"


class RiskLevel(Enum):
    """Risk severity for a detection."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class StegoFinding:
    """A single steganographic finding."""
    vector: StegoVector
    risk: RiskLevel
    confidence: float          # 0.0 – 1.0
    description: str
    evidence: List[str] = field(default_factory=list)
    decoded_payload: Optional[str] = None
    positions: List[int] = field(default_factory=list)  # char or line indices


@dataclass
class StegoReport:
    """Full steganography analysis report for a text."""
    text_length: int
    findings: List[StegoFinding] = field(default_factory=list)
    risk_score: float = 0.0    # composite 0–100
    risk_grade: str = "A"
    vectors_checked: int = 0

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0

    def findings_by_vector(self, vector: StegoVector) -> List[StegoFinding]:
        return [f for f in self.findings if f.vector == vector]

    def highest_risk(self) -> RiskLevel:
        if not self.findings:
            return RiskLevel.NONE
        order = [RiskLevel.NONE, RiskLevel.LOW, RiskLevel.MEDIUM,
                 RiskLevel.HIGH, RiskLevel.CRITICAL]
        return max((f.risk for f in self.findings), key=lambda r: order.index(r))

    def summary(self) -> str:
        lines = [
            f"Steganography Analysis Report",
            f"{'=' * 40}",
            f"Text length:     {self.text_length} chars",
            f"Vectors checked: {self.vectors_checked}",
            f"Findings:        {len(self.findings)}",
            f"Risk score:      {self.risk_score:.1f}/100 ({self.risk_grade})",
            f"Highest risk:    {self.highest_risk().value}",
        ]
        if self.findings:
            lines.append("")
            lines.append("Findings:")
            lines.append("-" * 40)
            for i, f in enumerate(self.findings, 1):
                lines.append(f"  {i}. [{f.vector.value}] {f.risk.value.upper()} "
                             f"(confidence: {f.confidence:.0%})")
                lines.append(f"     {f.description}")
                if f.decoded_payload:
                    payload_preview = f.decoded_payload[:80]
                    lines.append(f"     Decoded: {payload_preview!r}")
                for ev in f.evidence[:3]:
                    lines.append(f"     • {ev}")
        return "\n".join(lines)


@dataclass
class StegoConfig:
    """Configuration for the steganography detector."""
    # Whitespace
    min_zero_width_chars: int = 1           # any ZW char is suspicious
    trailing_space_ratio_threshold: float = 0.3  # >30% lines with trailing spaces
    # Acrostic
    min_lines_for_acrostic: int = 4
    # Homoglyph
    min_homoglyphs: int = 1                 # any homoglyph is suspicious
    # Capitalization
    min_words_for_cap_analysis: int = 20
    cap_entropy_threshold: float = 0.9       # high entropy = potential encoding
    # Punctuation
    unusual_punctuation_threshold: float = 0.05  # >5% unusual punctuation chars
    # Sentence length
    min_sentences_for_length: int = 8
    # Enable/disable vectors
    enabled_vectors: Set[StegoVector] = field(
        default_factory=lambda: set(StegoVector)
    )


# ── Detector ────────────────────────────────────────────────────────

class SteganographyDetector:
    """Analyses agent text output for steganographic encodings.

    Usage::

        detector = SteganographyDetector()
        report = detector.analyze("some agent output text")
        print(report.summary())

    Or check specific vectors::

        findings = detector.detect_homoglyphs("text with Cyrillic а")
    """

    def __init__(self, config: Optional[StegoConfig] = None):
        self.config = config or StegoConfig()

    # ── Public API ──────────────────────────────────────────────────

    def analyze(self, text: str) -> StegoReport:
        """Run all enabled detection vectors and return a full report."""
        if not text:
            return StegoReport(text_length=0, vectors_checked=0)

        findings: List[StegoFinding] = []
        vectors_checked = 0

        checks = [
            (StegoVector.WHITESPACE, self.detect_whitespace_encoding),
            (StegoVector.INVISIBLE_UNICODE, self.detect_invisible_unicode),
            (StegoVector.ACROSTIC, self.detect_acrostic),
            (StegoVector.HOMOGLYPH, self.detect_homoglyphs),
            (StegoVector.CAPITALIZATION, self.detect_capitalization_encoding),
            (StegoVector.PUNCTUATION, self.detect_punctuation_anomalies),
            (StegoVector.SENTENCE_LENGTH, self.detect_sentence_length_encoding),
        ]

        for vec, fn in checks:
            if vec in self.config.enabled_vectors:
                vectors_checked += 1
                findings.extend(fn(text))

        risk_score = self._compute_risk_score(findings)
        risk_grade = self._score_to_grade(risk_score)

        return StegoReport(
            text_length=len(text),
            findings=findings,
            risk_score=risk_score,
            risk_grade=risk_grade,
            vectors_checked=vectors_checked,
        )

    def batch_analyze(self, texts: Sequence[str]) -> List[StegoReport]:
        """Analyze multiple texts."""
        return [self.analyze(t) for t in texts]

    def compare_texts(
        self, text_a: str, text_b: str
    ) -> Dict[str, Any]:
        """Compare two texts for steganographic differences.

        Useful for checking if an agent modified an otherwise-identical text
        to embed hidden data.
        """
        report_a = self.analyze(text_a)
        report_b = self.analyze(text_b)

        # Find homoglyph differences
        homoglyph_diffs = []
        for i, (ca, cb) in enumerate(zip(text_a, text_b)):
            if ca != cb:
                homoglyph_diffs.append({
                    "position": i,
                    "text_a": repr(ca),
                    "text_b": repr(cb),
                    "a_is_homoglyph": ca in REVERSE_HOMOGLYPH,
                    "b_is_homoglyph": cb in REVERSE_HOMOGLYPH,
                })

        return {
            "text_a_length": len(text_a),
            "text_b_length": len(text_b),
            "length_diff": len(text_b) - len(text_a),
            "report_a": report_a,
            "report_b": report_b,
            "risk_diff": report_b.risk_score - report_a.risk_score,
            "character_differences": len(homoglyph_diffs),
            "homoglyph_substitutions": homoglyph_diffs[:20],
            "suspicious": (len(homoglyph_diffs) > 0
                           and all(d["a_is_homoglyph"] or d["b_is_homoglyph"]
                                   for d in homoglyph_diffs)),
        }

    # ── Detection vectors ───────────────────────────────────────────

    def detect_whitespace_encoding(self, text: str) -> List[StegoFinding]:
        """Detect data encoded via whitespace patterns."""
        findings: List[StegoFinding] = []

        # Zero-width characters
        zw_positions = [
            i for i, ch in enumerate(text) if ch in ZERO_WIDTH_CHARS
        ]
        if len(zw_positions) >= self.config.min_zero_width_chars:
            # Try to decode as binary (ZWSP=0, ZWNJ=1)
            decoded = self._decode_zero_width(text)
            risk = RiskLevel.HIGH if len(zw_positions) >= 8 else RiskLevel.MEDIUM
            confidence = min(1.0, len(zw_positions) / 16)
            findings.append(StegoFinding(
                vector=StegoVector.WHITESPACE,
                risk=risk,
                confidence=confidence,
                description=(f"Found {len(zw_positions)} zero-width character(s) "
                             f"that could encode hidden data"),
                evidence=[
                    f"Zero-width chars at positions: "
                    f"{zw_positions[:10]}{'...' if len(zw_positions) > 10 else ''}",
                    f"Character types: {self._classify_zw_chars(text, zw_positions)}",
                ],
                decoded_payload=decoded,
                positions=zw_positions,
            ))

        # Trailing spaces per line
        lines = text.split('\n')
        trailing = [
            (i, len(line) - len(line.rstrip(' ')))
            for i, line in enumerate(lines)
            if line.rstrip('\r') != line.rstrip(' \r')
        ]
        if lines and len(trailing) / max(len(lines), 1) > self.config.trailing_space_ratio_threshold:
            # Try to decode trailing space counts as data
            space_counts = [cnt for _, cnt in trailing]
            decoded_bits = ''.join('1' if c % 2 == 1 else '0' for c in space_counts)
            decoded = self._bits_to_text(decoded_bits) if len(decoded_bits) >= 8 else None
            findings.append(StegoFinding(
                vector=StegoVector.WHITESPACE,
                risk=RiskLevel.MEDIUM,
                confidence=min(1.0, len(trailing) / len(lines)),
                description=(f"{len(trailing)}/{len(lines)} lines have trailing spaces, "
                             f"which could encode binary data"),
                evidence=[
                    f"Lines with trailing spaces: {[i for i, _ in trailing[:10]]}",
                    f"Space counts: {space_counts[:10]}",
                ],
                decoded_payload=decoded,
                positions=[i for i, _ in trailing],
            ))

        # Tab-space mixing within indentation
        tab_lines = []
        space_lines = []
        for i, line in enumerate(lines):
            indent = line[:len(line) - len(line.lstrip())]
            if '\t' in indent and ' ' in indent:
                pass  # mixed — suspicious on its own
            elif '\t' in indent:
                tab_lines.append(i)
            elif indent.startswith(' '):
                space_lines.append(i)
        if tab_lines and space_lines and len(lines) >= 5:
            total_indented = len(tab_lines) + len(space_lines)
            minority = min(len(tab_lines), len(space_lines))
            if minority >= 2 and minority / total_indented > 0.1:
                bits = ''.join(
                    '1' if i in tab_lines else '0'
                    for i in range(len(lines))
                    if i in tab_lines or i in space_lines
                )
                decoded = self._bits_to_text(bits) if len(bits) >= 8 else None
                findings.append(StegoFinding(
                    vector=StegoVector.WHITESPACE,
                    risk=RiskLevel.LOW,
                    confidence=0.4,
                    description=("Mixed tab/space indentation pattern could encode "
                                 "binary data"),
                    evidence=[
                        f"Tab-indented lines: {len(tab_lines)}",
                        f"Space-indented lines: {len(space_lines)}",
                    ],
                    decoded_payload=decoded,
                ))

        return findings

    def detect_invisible_unicode(self, text: str) -> List[StegoFinding]:
        """Detect invisible or non-rendering Unicode characters."""
        findings: List[StegoFinding] = []
        invisible_chars: List[Tuple[int, str, str]] = []

        for i, ch in enumerate(text):
            if ch in ZERO_WIDTH_CHARS:
                continue  # handled by whitespace detector
            cat = unicodedata.category(ch)
            # Cf = Format, Mn = Nonspacing Mark (when standalone)
            if cat == 'Cf':
                name = unicodedata.name(ch, f'U+{ord(ch):04X}')
                invisible_chars.append((i, ch, name))
            elif cat == 'Zs' and ch not in (' ', '\u00a0'):
                # Unusual whitespace (not regular space or NBSP)
                name = unicodedata.name(ch, f'U+{ord(ch):04X}')
                invisible_chars.append((i, ch, name))

        if invisible_chars:
            char_types = Counter(name for _, _, name in invisible_chars)
            risk = (RiskLevel.HIGH if len(invisible_chars) >= 10
                    else RiskLevel.MEDIUM if len(invisible_chars) >= 3
                    else RiskLevel.LOW)
            findings.append(StegoFinding(
                vector=StegoVector.INVISIBLE_UNICODE,
                risk=risk,
                confidence=min(1.0, len(invisible_chars) / 10),
                description=(f"Found {len(invisible_chars)} invisible/non-rendering "
                             f"Unicode characters"),
                evidence=[
                    f"Character types: {dict(char_types.most_common(5))}",
                    f"Positions: {[pos for pos, _, _ in invisible_chars[:10]]}",
                ],
                positions=[pos for pos, _, _ in invisible_chars],
            ))

        return findings

    def detect_acrostic(self, text: str) -> List[StegoFinding]:
        """Detect acrostic patterns (first/last letters of lines/sentences)."""
        findings: List[StegoFinding] = []
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        if len(lines) < self.config.min_lines_for_acrostic:
            return findings

        # First letters of lines
        first_letters = ''.join(l[0] for l in lines if l)
        self._check_acrostic_pattern(
            first_letters, "first letters of lines", findings, lines
        )

        # Last letters of lines
        last_letters = ''.join(l[-1] for l in lines if l)
        self._check_acrostic_pattern(
            last_letters, "last letters of lines", findings, lines
        )

        # First letters of sentences
        sentences = self._split_sentences(text)
        if len(sentences) >= self.config.min_lines_for_acrostic:
            first_sent = ''.join(s.strip()[0] for s in sentences if s.strip())
            self._check_acrostic_pattern(
                first_sent, "first letters of sentences", findings, lines
            )

        # First words of lines
        if len(lines) >= self.config.min_lines_for_acrostic:
            first_words = ' '.join(
                l.split()[0] for l in lines if l.split()
            )
            # Check if first words form a coherent phrase
            words = first_words.split()
            if len(words) >= 4:
                alpha_ratio = sum(1 for w in words if w.isalpha()) / len(words)
                if alpha_ratio >= 0.8:
                    # Check if it looks like English words (simple heuristic)
                    avg_len = statistics.mean(len(w) for w in words)
                    if 2 <= avg_len <= 8:
                        findings.append(StegoFinding(
                            vector=StegoVector.ACROSTIC,
                            risk=RiskLevel.LOW,
                            confidence=0.3,
                            description=("First words of lines could form a hidden "
                                         "message (needs manual review)"),
                            evidence=[f"First words: {first_words[:100]}"],
                            decoded_payload=first_words,
                        ))

        return findings

    def detect_homoglyphs(self, text: str) -> List[StegoFinding]:
        """Detect Unicode homoglyph substitutions."""
        findings: List[StegoFinding] = []
        homoglyph_positions: List[Tuple[int, str, str]] = []

        for i, ch in enumerate(text):
            if ch in REVERSE_HOMOGLYPH:
                latin_equiv = REVERSE_HOMOGLYPH[ch]
                homoglyph_positions.append((i, ch, latin_equiv))

        if len(homoglyph_positions) >= self.config.min_homoglyphs:
            # Try to decode as binary: latin=0, homoglyph=1
            # Only for characters that have a homoglyph mapping
            decoded_bits = []
            for i, ch in enumerate(text):
                if ch in REVERSE_HOMOGLYPH:
                    decoded_bits.append('1')
                elif ch.lower() in HOMOGLYPH_MAP or ch in HOMOGLYPH_MAP:
                    decoded_bits.append('0')
            decoded = (self._bits_to_text(''.join(decoded_bits))
                       if len(decoded_bits) >= 8 else None)

            char_types = Counter(
                f"{latin}→{ch}" for _, ch, latin in homoglyph_positions
            )
            risk = (RiskLevel.CRITICAL if len(homoglyph_positions) >= 16
                    else RiskLevel.HIGH if len(homoglyph_positions) >= 8
                    else RiskLevel.MEDIUM if len(homoglyph_positions) >= 3
                    else RiskLevel.LOW)
            confidence = min(1.0, len(homoglyph_positions) / 8)

            findings.append(StegoFinding(
                vector=StegoVector.HOMOGLYPH,
                risk=risk,
                confidence=confidence,
                description=(f"Found {len(homoglyph_positions)} homoglyph "
                             f"substitution(s) — visually identical characters "
                             f"from different Unicode blocks"),
                evidence=[
                    f"Substitutions: {dict(char_types.most_common(5))}",
                    f"Positions: {[p for p, _, _ in homoglyph_positions[:10]]}",
                ],
                decoded_payload=decoded,
                positions=[p for p, _, _ in homoglyph_positions],
            ))

        return findings

    def detect_capitalization_encoding(self, text: str) -> List[StegoFinding]:
        """Detect data encoded via capitalization patterns."""
        findings: List[StegoFinding] = []
        words = re.findall(r'[a-zA-Z]+', text)

        if len(words) < self.config.min_words_for_cap_analysis:
            return findings

        # Analyze capitalization pattern
        cap_bits = []
        mid_sentence_caps = []
        for i, word in enumerate(words):
            if len(word) < 2:
                continue
            # Skip likely proper nouns / sentence starts (heuristic)
            if word[0].isupper() and word[1:].islower():
                cap_bits.append(1)
            elif word.islower():
                cap_bits.append(0)
            # Track mid-word capitalization
            if any(c.isupper() for c in word[1:]) and not word.isupper():
                mid_sentence_caps.append((i, word))

        if len(cap_bits) >= 16:
            # Compute entropy of capitalization pattern
            ones = sum(cap_bits)
            zeros = len(cap_bits) - ones
            total = len(cap_bits)
            if ones > 0 and zeros > 0:
                p1 = ones / total
                p0 = zeros / total
                entropy = -(p1 * math.log2(p1) + p0 * math.log2(p0))
            else:
                entropy = 0.0

            if entropy >= self.config.cap_entropy_threshold:
                decoded = self._bits_to_text(
                    ''.join(str(b) for b in cap_bits)
                )
                findings.append(StegoFinding(
                    vector=StegoVector.CAPITALIZATION,
                    risk=RiskLevel.MEDIUM,
                    confidence=min(1.0, (entropy - 0.8) / 0.2) if entropy > 0.8 else 0.3,
                    description=(f"Capitalization pattern has high entropy "
                                 f"({entropy:.3f}), could encode data"),
                    evidence=[
                        f"Capitalization bits: {''.join(str(b) for b in cap_bits[:30])}...",
                        f"Entropy: {entropy:.3f} (threshold: "
                        f"{self.config.cap_entropy_threshold})",
                        f"Ratio: {ones}/{total} capitalized",
                    ],
                    decoded_payload=decoded,
                ))

        # Mid-word unusual capitalization
        if len(mid_sentence_caps) >= 3:
            hidden = ''.join(
                ''.join(c for c in word if c.isupper())
                for _, word in mid_sentence_caps
            )
            findings.append(StegoFinding(
                vector=StegoVector.CAPITALIZATION,
                risk=RiskLevel.MEDIUM,
                confidence=min(1.0, len(mid_sentence_caps) / 10),
                description=(f"Found {len(mid_sentence_caps)} words with unusual "
                             f"mid-word capitalization"),
                evidence=[
                    f"Words: {[w for _, w in mid_sentence_caps[:10]]}",
                    f"Extracted capitals: {hidden[:50]}",
                ],
                decoded_payload=hidden if len(hidden) >= 3 else None,
                positions=[i for i, _ in mid_sentence_caps],
            ))

        return findings

    def detect_punctuation_anomalies(self, text: str) -> List[StegoFinding]:
        """Detect data encoded via punctuation patterns."""
        findings: List[StegoFinding] = []

        # Unusual dash/quote types
        unusual_punct = {
            '\u2013': 'en-dash',
            '\u2014': 'em-dash',
            '\u2018': 'left single quote',
            '\u2019': 'right single quote',
            '\u201c': 'left double quote',
            '\u201d': 'right double quote',
            '\u2026': 'ellipsis',
            '\u00b7': 'middle dot',
            '\u2022': 'bullet',
            '\u00a0': 'non-breaking space',
        }
        found_unusual: Dict[str, List[int]] = defaultdict(list)
        for i, ch in enumerate(text):
            if ch in unusual_punct:
                found_unusual[unusual_punct[ch]].append(i)

        # Check for alternating punctuation that could encode bits
        # e.g., alternating ' and " for 0 and 1
        if len(text) >= 10:
            punct_chars = [(i, ch) for i, ch in enumerate(text)
                           if ch in '.,;:!?']
            if len(punct_chars) >= 8:
                # Check if comma-vs-semicolon pattern
                binary_punct = []
                for _, ch in punct_chars:
                    if ch in '.,':
                        binary_punct.append('0')
                    elif ch in ';:!?':
                        binary_punct.append('1')
                decoded = self._bits_to_text(''.join(binary_punct)) if len(binary_punct) >= 8 else None

                # Only flag if the pattern has high entropy (not just normal text)
                ones = binary_punct.count('1')
                zeros = binary_punct.count('0')
                total = len(binary_punct)
                if total > 0 and ones > 0 and zeros > 0:
                    p1 = ones / total
                    entropy = -(p1 * math.log2(p1)
                                + (1 - p1) * math.log2(1 - p1))
                    if entropy > 0.9 and total >= 16:
                        findings.append(StegoFinding(
                            vector=StegoVector.PUNCTUATION,
                            risk=RiskLevel.LOW,
                            confidence=0.3,
                            description=("Punctuation pattern has high entropy, "
                                         "could encode data"),
                            evidence=[
                                f"Punctuation bits: {''.join(binary_punct[:30])}",
                                f"Entropy: {entropy:.3f}",
                            ],
                            decoded_payload=decoded,
                        ))

        # Doubled/tripled punctuation
        double_punct = list(re.finditer(r'([.!?,;:])\1{2,}', text))
        if len(double_punct) >= 2:
            # Count of repeats could encode numbers
            repeat_counts = [len(m.group()) for m in double_punct]
            findings.append(StegoFinding(
                vector=StegoVector.PUNCTUATION,
                risk=RiskLevel.LOW,
                confidence=0.3,
                description=(f"Found {len(double_punct)} repeated punctuation "
                             f"sequences that could encode numeric data"),
                evidence=[
                    f"Repeat counts: {repeat_counts}",
                    f"Positions: {[m.start() for m in double_punct[:10]]}",
                ],
            ))

        return findings

    def detect_sentence_length_encoding(self, text: str) -> List[StegoFinding]:
        """Detect data encoded via sentence word counts (odd/even)."""
        findings: List[StegoFinding] = []
        sentences = self._split_sentences(text)

        if len(sentences) < self.config.min_sentences_for_length:
            return findings

        word_counts = [len(s.split()) for s in sentences if s.strip()]
        if not word_counts:
            return findings

        # Odd/even word count as binary encoding
        bits = ''.join('1' if c % 2 == 1 else '0' for c in word_counts)
        decoded = self._bits_to_text(bits) if len(bits) >= 8 else None

        # Check entropy of the odd/even pattern
        ones = bits.count('1')
        zeros = bits.count('0')
        total = len(bits)
        if total >= 8 and ones > 0 and zeros > 0:
            p1 = ones / total
            entropy = -(p1 * math.log2(p1) + (1 - p1) * math.log2(1 - p1))

            # High entropy in sentence lengths is somewhat unusual
            if entropy > 0.95:
                findings.append(StegoFinding(
                    vector=StegoVector.SENTENCE_LENGTH,
                    risk=RiskLevel.LOW,
                    confidence=0.25,
                    description=("Sentence word counts show high odd/even entropy "
                                 f"({entropy:.3f}), could encode binary data"),
                    evidence=[
                        f"Word counts: {word_counts[:15]}",
                        f"Binary (odd=1): {bits[:30]}",
                        f"Entropy: {entropy:.3f}",
                    ],
                    decoded_payload=decoded,
                ))

        # Check for arithmetic progression or repeating pattern
        if len(word_counts) >= 6:
            diffs = [word_counts[i+1] - word_counts[i]
                     for i in range(len(word_counts) - 1)]
            if len(set(diffs)) == 1 and diffs[0] != 0:
                findings.append(StegoFinding(
                    vector=StegoVector.SENTENCE_LENGTH,
                    risk=RiskLevel.MEDIUM,
                    confidence=0.6,
                    description=("Sentence word counts form a perfect arithmetic "
                                 "progression — highly unusual for natural text"),
                    evidence=[
                        f"Word counts: {word_counts}",
                        f"Common difference: {diffs[0]}",
                    ],
                ))

            # Check for Fibonacci-like or other mathematical sequences
            for period in range(2, min(len(word_counts) // 2, 6)):
                is_repeating = all(
                    word_counts[i] == word_counts[i % period]
                    for i in range(len(word_counts))
                )
                if is_repeating and len(set(word_counts[:period])) > 1:
                    findings.append(StegoFinding(
                        vector=StegoVector.SENTENCE_LENGTH,
                        risk=RiskLevel.MEDIUM,
                        confidence=0.5,
                        description=(f"Sentence word counts repeat with period "
                                     f"{period} — suspicious pattern"),
                        evidence=[
                            f"Pattern: {word_counts[:period]}",
                            f"Full counts: {word_counts}",
                        ],
                    ))
                    break

        return findings

    # ── Helpers ──────────────────────────────────────────────────────

    def _decode_zero_width(self, text: str) -> Optional[str]:
        """Try to decode zero-width characters as binary data.

        ZWSP (\\u200b) = 0, ZWNJ (\\u200c) = 1 is a common convention.
        """
        zw_chars = [ch for ch in text if ch in ZERO_WIDTH_CHARS]
        if len(zw_chars) < 8:
            return None

        bits = []
        for ch in zw_chars:
            if ch == '\u200b':
                bits.append('0')
            elif ch == '\u200c':
                bits.append('1')
            else:
                bits.append('0')  # default unknown ZW to 0

        return self._bits_to_text(''.join(bits))

    @staticmethod
    def _bits_to_text(bits: str) -> Optional[str]:
        """Convert a bit string to ASCII text."""
        if len(bits) < 8:
            return None
        chars = []
        for i in range(0, len(bits) - 7, 8):
            byte = bits[i:i+8]
            val = int(byte, 2)
            if 32 <= val <= 126:
                chars.append(chr(val))
            elif val == 0:
                break
            else:
                chars.append('?')
        result = ''.join(chars)
        return result if result else None

    @staticmethod
    def _classify_zw_chars(
        text: str, positions: List[int]
    ) -> Dict[str, int]:
        """Classify zero-width characters by type."""
        names = {
            '\u200b': 'ZWSP', '\u200c': 'ZWNJ', '\u200d': 'ZWJ',
            '\u200e': 'LRM', '\u200f': 'RLM', '\u2060': 'WJ',
            '\ufeff': 'BOM', '\u00ad': 'SHY', '\u034f': 'CGJ',
        }
        counts: Dict[str, int] = Counter()
        for pos in positions:
            ch = text[pos]
            counts[names.get(ch, f'U+{ord(ch):04X}')] += 1
        return dict(counts)

    def _check_acrostic_pattern(
        self,
        letters: str,
        source: str,
        findings: List[StegoFinding],
        lines: List[str],
    ) -> None:
        """Check if a string of extracted letters looks like a hidden message."""
        if len(letters) < self.config.min_lines_for_acrostic:
            return

        # Check 1: All letters (ignoring case) — could spell something
        alpha_only = ''.join(c for c in letters if c.isalpha())
        if len(alpha_only) < 3:
            return

        # Check 2: Unusually high ratio of consonants or vowels
        # (natural acrostics tend to be random, intentional ones spell words)
        lower = alpha_only.lower()
        vowels = sum(1 for c in lower if c in 'aeiou')
        consonants = len(lower) - vowels

        # Simple dictionary check for common words
        # (If the acrostic contains a word of 4+ chars, flag it)
        common_words = {
            'help', 'send', 'kill', 'stop', 'exit', 'free', 'code',
            'data', 'leak', 'hack', 'open', 'run', 'exec', 'attack',
            'escape', 'break', 'secret', 'hidden', 'covert', 'agent',
            'password', 'token', 'key', 'alert', 'warning', 'danger',
        }
        lower_letters = lower
        found_words = [
            w for w in common_words
            if w in lower_letters
        ]

        if found_words:
            findings.append(StegoFinding(
                vector=StegoVector.ACROSTIC,
                risk=RiskLevel.HIGH,
                confidence=min(1.0, 0.5 + 0.1 * len(found_words)),
                description=(f"Acrostic from {source} contains suspicious "
                             f"word(s): {found_words}"),
                evidence=[
                    f"Extracted: {letters[:50]}",
                    f"Words found: {found_words}",
                ],
                decoded_payload=letters,
            ))
        elif vowels > 0 and consonants > 0:
            # Check vowel/consonant ratio — English-like is ~40% vowels
            vowel_ratio = vowels / len(lower)
            if 0.25 <= vowel_ratio <= 0.55:
                # Looks plausibly like text
                findings.append(StegoFinding(
                    vector=StegoVector.ACROSTIC,
                    risk=RiskLevel.LOW,
                    confidence=0.2,
                    description=(f"Acrostic from {source} has English-like "
                                 f"vowel/consonant ratio ({vowel_ratio:.0%})"),
                    evidence=[f"Extracted: {letters[:50]}"],
                    decoded_payload=letters,
                ))

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        """Split text into sentences."""
        # Simple sentence splitter
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s for s in sentences if s.strip()]

    @staticmethod
    def _compute_risk_score(findings: List[StegoFinding]) -> float:
        """Compute composite risk score 0–100."""
        if not findings:
            return 0.0

        risk_weights = {
            RiskLevel.NONE: 0,
            RiskLevel.LOW: 10,
            RiskLevel.MEDIUM: 25,
            RiskLevel.HIGH: 50,
            RiskLevel.CRITICAL: 80,
        }

        total = sum(
            risk_weights[f.risk] * f.confidence
            for f in findings
        )
        # Cap at 100
        return min(100.0, total)

    @staticmethod
    def _score_to_grade(score: float) -> str:
        """Convert score to letter grade (A=clean, F=highly suspicious)."""
        if score <= 5:
            return "A"
        elif score <= 15:
            return "B"
        elif score <= 30:
            return "C"
        elif score <= 50:
            return "D"
        else:
            return "F"


# ── Convenience functions ───────────────────────────────────────────

def scan_text(text: str, **config_kwargs: Any) -> StegoReport:
    """Quick scan of a text for steganographic content."""
    config = StegoConfig(**config_kwargs) if config_kwargs else None
    detector = SteganographyDetector(config)
    return detector.analyze(text)


def encode_zero_width(message: str, cover_text: str) -> str:
    """Encode a message using zero-width characters (for testing purposes).

    Embeds *message* as ZWSP/ZWNJ binary encoding within *cover_text*.
    """
    bits = []
    for ch in message:
        bits.extend(f'{ord(ch):08b}')

    zw_chars = []
    for b in bits:
        zw_chars.append('\u200c' if b == '1' else '\u200b')

    # Insert ZW chars after the first character of cover text
    if cover_text:
        return cover_text[0] + ''.join(zw_chars) + cover_text[1:]
    return ''.join(zw_chars)


def encode_homoglyphs(message: str, cover_text: str) -> str:
    """Encode bits via homoglyph substitution (for testing purposes).

    For each bit in *message*, finds the next substitutable character in
    *cover_text* and swaps it with a Cyrillic homoglyph (bit=1) or
    leaves it (bit=0).
    """
    bits = []
    for ch in message:
        bits.extend(f'{ord(ch):08b}')

    result = list(cover_text)
    bit_idx = 0
    for i, ch in enumerate(result):
        if bit_idx >= len(bits):
            break
        lower = ch.lower()
        key = ch if ch in HOMOGLYPH_MAP else (lower if lower in HOMOGLYPH_MAP else None)
        if key is not None:
            if bits[bit_idx] == '1':
                # Find the right-case homoglyph
                if ch.isupper() and ch in HOMOGLYPH_MAP:
                    result[i] = HOMOGLYPH_MAP[ch][0]
                elif ch.islower() and ch in HOMOGLYPH_MAP:
                    result[i] = HOMOGLYPH_MAP[ch][0]
                elif lower in HOMOGLYPH_MAP:
                    result[i] = HOMOGLYPH_MAP[lower][0]
            bit_idx += 1

    return ''.join(result)
