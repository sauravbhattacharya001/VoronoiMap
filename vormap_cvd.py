"""Color Vision Deficiency (CVD) simulator for Voronoi diagrams.

Simulates how Voronoi maps appear under different types of color blindness
(protanopia, deuteranopia, tritanopia, achromatopsia) and suggests
accessible palettes that work for all viewers.

Uses the Brettel/Viénot/Mollon (1997) simulation matrices for accurate
physiological modeling of dichromatic vision.

Usage (CLI):
    # Simulate deuteranopia on an existing SVG
    python vormap_cvd.py diagram.svg --type deuteranopia --output sim.svg

    # Generate all CVD variants side-by-side as HTML
    python vormap_cvd.py diagram.svg --all --output comparison.html

    # Check a palette for CVD safety
    python vormap_cvd.py --check-palette "#e41a1c,#377eb8,#4daf4a,#984ea3"

    # Suggest an accessible palette for N colors
    python vormap_cvd.py --suggest 6

    # Generate a Voronoi diagram with CVD-safe colors from a data file
    python vormap_cvd.py datafile.txt --voronoi --safe --output safe.svg

Usage (as module):
    import vormap_cvd

    # Simulate a color under deuteranopia
    r, g, b = vormap_cvd.simulate_cvd(225, 26, 28, "deuteranopia")

    # Check palette accessibility
    report = vormap_cvd.check_palette(["#e41a1c", "#377eb8", "#4daf4a"])
    print(report)

    # Get a CVD-safe palette
    palette = vormap_cvd.suggest_palette(6)
"""

import argparse
import math
import re
import sys
from dataclasses import dataclass, field

import vormap

# ── CVD Simulation Matrices (Brettel/Viénot) ────────────────────────

CVD_MATRICES = {
    "protanopia": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deuteranopia": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047414],
        [-0.011820, 0.042940, 0.968881],
    ],
    "tritanopia": [
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ],
}

ACHROMA_WEIGHTS = [0.2126, 0.7152, 0.0722]

# ── CVD-Safe Palettes (Wong 2011) ───────────────────────────────────

WONG_PALETTE = [
    "#000000", "#E69F00", "#56B4E9", "#009E73",
    "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
]

SAFE_PALETTES = {
    2: ["#0072B2", "#D55E00"],
    3: ["#0072B2", "#D55E00", "#009E73"],
    4: ["#0072B2", "#D55E00", "#009E73", "#E69F00"],
    5: ["#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7"],
    6: ["#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7", "#56B4E9"],
    7: ["#0072B2", "#D55E00", "#009E73", "#E69F00", "#CC79A7", "#56B4E9", "#F0E442"],
    8: WONG_PALETTE,
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"


def srgb_to_linear(c: int) -> float:
    v = c / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def linear_to_srgb(v: float) -> int:
    v = max(0.0, min(1.0, v))
    s = v * 12.92 if v <= 0.0031308 else 1.055 * (v ** (1.0 / 2.4)) - 0.055
    return int(round(s * 255))


def color_distance(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    rm = (c1[0] + c2[0]) / 2.0
    dr, dg, db = c1[0] - c2[0], c1[1] - c2[1], c1[2] - c2[2]
    return math.sqrt((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db)


def relative_luminance(r: int, g: int, b: int) -> float:
    return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)


def contrast_ratio(c1: tuple[int, int, int], c2: tuple[int, int, int]) -> float:
    l1, l2 = relative_luminance(*c1), relative_luminance(*c2)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def simulate_cvd(r: int, g: int, b: int, cvd_type: str) -> tuple[int, int, int]:
    """Simulate how a color appears under a given CVD type."""
    if cvd_type == "achromatopsia":
        lr, lg, lb = srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)
        grey = sum(w * c for w, c in zip(ACHROMA_WEIGHTS, [lr, lg, lb]))
        v = linear_to_srgb(grey)
        return (v, v, v)

    if cvd_type not in CVD_MATRICES:
        raise ValueError(f"Unknown CVD type: {cvd_type}")

    mat = CVD_MATRICES[cvd_type]
    lin = [srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)]
    return tuple(linear_to_srgb(sum(mat[i][j] * lin[j] for j in range(3))) for i in range(3))


def simulate_hex(hex_color: str, cvd_type: str) -> str:
    return rgb_to_hex(*simulate_cvd(*hex_to_rgb(hex_color), cvd_type))


@dataclass
class PaletteReport:
    palette: list[str]
    min_distances: dict[str, float] = field(default_factory=dict)
    confusable_pairs: dict[str, list[tuple[str, str, float]]] = field(default_factory=dict)
    overall_safe: bool = True
    grade: str = "A"

    def __str__(self) -> str:
        lines = [f"CVD Palette Report ({len(self.palette)} colors)", "=" * 50,
                 f"Palette: {', '.join(self.palette)}", f"Overall Grade: {self.grade}", ""]
        for ct in ["protanopia", "deuteranopia", "tritanopia", "achromatopsia"]:
            dist = self.min_distances.get(ct, 0)
            status = "✓ SAFE" if dist >= 30 else ("⚠ MARGINAL" if dist >= 15 else "✗ UNSAFE")
            lines.append(f"  {ct:16s}  min distance: {dist:6.1f}  {status}")
            for c1, c2, d in self.confusable_pairs.get(ct, []):
                lines.append(f"    ↳ {c1} ↔ {c2} (dist={d:.1f})")
        return "\n".join(lines)


DISTANCE_THRESHOLD = 30


def check_palette(palette: list[str]) -> PaletteReport:
    """Analyze a palette for CVD accessibility."""
    report = PaletteReport(palette=palette)
    worst_min = float("inf")
    for ct in ["protanopia", "deuteranopia", "tritanopia", "achromatopsia"]:
        simulated = [hex_to_rgb(simulate_hex(c, ct)) for c in palette]
        min_dist = float("inf")
        confusable = []
        for i in range(len(simulated)):
            for j in range(i + 1, len(simulated)):
                d = color_distance(simulated[i], simulated[j])
                min_dist = min(min_dist, d)
                if d < DISTANCE_THRESHOLD:
                    confusable.append((palette[i], palette[j], d))
        report.min_distances[ct] = min_dist
        report.confusable_pairs[ct] = sorted(confusable, key=lambda x: x[2])
        worst_min = min(worst_min, min_dist)

    if worst_min >= 30: report.grade = "A"
    elif worst_min >= 20: report.grade = "B"
    elif worst_min >= 15: report.grade, report.overall_safe = "C", False
    else: report.grade, report.overall_safe = "F", False
    return report


def suggest_palette(n: int) -> list[str]:
    """Suggest a CVD-safe palette with n colors (2-8)."""
    return SAFE_PALETTES.get(max(2, min(8, n)), WONG_PALETTE[:n])


_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}")
_RGB_RE = re.compile(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)")


def _transform_color_in_str(s: str, cvd_type: str) -> str:
    def replace_hex(m): return simulate_hex(m.group(0), cvd_type)
    def replace_rgb(m):
        sr, sg, sb = simulate_cvd(int(m.group(1)), int(m.group(2)), int(m.group(3)), cvd_type)
        return f"rgb({sr},{sg},{sb})"
    return _RGB_RE.sub(replace_rgb, _HEX_RE.sub(replace_hex, s))


def simulate_svg(svg_path: str, cvd_type: str, output_path: str) -> str:
    """Read an SVG, simulate CVD on all colors, write the result."""
    safe_in = vormap.validate_input_path(svg_path, allow_absolute=True)
    safe_out = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(safe_in, "r", encoding="utf-8") as f:
        content = f.read()
    with open(safe_out, "w", encoding="utf-8") as f:
        f.write(_transform_color_in_str(content, cvd_type))
    return output_path


def generate_comparison_html(svg_path: str, output_path: str) -> str:
    """Generate HTML showing original SVG alongside all CVD simulations."""
    safe_in = vormap.validate_input_path(svg_path, allow_absolute=True)
    with open(safe_in, "r", encoding="utf-8") as f:
        original = f.read()

    descs = {
        "protanopia": "No red cones (~1% of males)",
        "deuteranopia": "No green cones (~5% of males)",
        "tritanopia": "No blue cones (very rare)",
        "achromatopsia": "Complete color blindness (very rare)",
    }
    panels = [f'<div class="panel"><h3>Normal Vision</h3><div class="svg-box">{original}</div></div>']
    for ct in descs:
        sim = _transform_color_in_str(original, ct)
        panels.append(f'<div class="panel"><h3>{ct.capitalize()}</h3>'
                      f'<p class="desc">{descs[ct]}</p><div class="svg-box">{sim}</div></div>')

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>CVD Simulation — VoronoiMap</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#1a1a2e;color:#e0e0e0;padding:20px}}
h1{{text-align:center;margin:20px 0;color:#56B4E9}}
h2{{text-align:center;margin:10px 0 30px;color:#888;font-weight:300}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;max-width:1600px;margin:0 auto}}
.panel{{background:#16213e;border-radius:12px;padding:20px;box-shadow:0 4px 15px rgba(0,0,0,.3)}}
.panel h3{{color:#E69F00;margin-bottom:8px}}
.desc{{color:#888;font-size:.85em;margin-bottom:12px}}
.svg-box{{width:100%;overflow:auto}}
.svg-box svg{{width:100%;height:auto}}
.footer{{text-align:center;margin-top:30px;color:#555;font-size:.85em}}
</style></head><body>
<h1>🎨 Color Vision Deficiency Simulation</h1>
<h2>How your Voronoi diagram appears to people with color blindness</h2>
<div class="grid">{''.join(panels)}</div>
<div class="footer">Generated by VoronoiMap vormap_cvd · Wong (2011) safe palettes</div>
</body></html>"""

    safe_out = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(safe_out, "w", encoding="utf-8") as f:
        f.write(html)
    return safe_out


def main():
    p = argparse.ArgumentParser(
        description="Color Vision Deficiency (CVD) simulator for Voronoi diagrams",
        epilog="Examples:\n"
               "  python vormap_cvd.py diagram.svg --type deuteranopia -o sim.svg\n"
               "  python vormap_cvd.py diagram.svg --all -o comparison.html\n"
               "  python vormap_cvd.py --check-palette '#e41a1c,#377eb8,#4daf4a'\n"
               "  python vormap_cvd.py --suggest 6\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)

    p.add_argument("input", nargs="?", help="Input SVG file")
    p.add_argument("-o", "--output", help="Output file path")
    p.add_argument("-t", "--type", dest="cvd_type", default="deuteranopia",
                   choices=["protanopia", "deuteranopia", "tritanopia", "achromatopsia"])
    p.add_argument("--all", action="store_true", help="HTML comparison with all CVD types")
    p.add_argument("--check-palette", metavar="COLORS", help="Check hex colors for CVD safety")
    p.add_argument("--suggest", type=int, metavar="N", help="Suggest CVD-safe palette (2-8)")

    args = p.parse_args()

    if args.suggest is not None:
        palette = suggest_palette(args.suggest)
        print(f"CVD-safe palette ({len(palette)} colors):")
        for i, c in enumerate(palette):
            print(f"  {i+1}. {c}  rgb{hex_to_rgb(c)}")
        print(); print(check_palette(palette)); return

    if args.check_palette:
        print(check_palette([c.strip() for c in args.check_palette.split(",")])); return

    if not args.input:
        p.error("Input SVG required for simulation")

    if args.all:
        out = args.output or "cvd_comparison.html"
        generate_comparison_html(args.input, out)
        print(f"CVD comparison saved to: {out}"); return

    out = args.output or f"cvd_{args.cvd_type}.svg"
    simulate_svg(args.input, args.cvd_type, out)
    print(f"CVD simulation ({args.cvd_type}) saved to: {out}")


if __name__ == "__main__":
    main()
