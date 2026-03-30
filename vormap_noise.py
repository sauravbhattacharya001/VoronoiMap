"""Worley (cellular) noise generator for VoronoiMap.

Generates Worley noise textures based on Voronoi distance fields — a
classic procedural generation technique used for organic textures (stone,
cells, water caustics, leather, cracked earth, stained glass, etc.).

Each pixel is colored based on its distance to the nearest (F1), second
nearest (F2), or further Voronoi seed points.  Combining these distance
fields (F2-F1, F1*F2, etc.) produces a wide variety of natural patterns.

Works with the existing ``vormap`` point loader or generates its own seeds.
Outputs PPM (always available) or PNG (when Pillow is installed).

Usage (CLI)::

    python vormap_noise.py --width 512 --height 512 -n 200 -o texture.ppm
    python vormap_noise.py --mode f2-f1 --colormap fire -o veins.ppm
    python vormap_noise.py --input seeds.txt --mode f1 --invert -o cells.ppm
    python vormap_noise.py --tiled --octaves 4 -o seamless.ppm

Usage (module)::

    import vormap_noise

    img = vormap_noise.generate(width=512, height=512, num_seeds=100,
                                mode="f2-f1", colormap="ice")
    vormap_noise.save_ppm(img, "texture.ppm")

    # With existing VoronoiMap data
    import vormap
    points = vormap.load_data("sites.txt")
    img = vormap_noise.generate_from_points(points, width=800, height=600)
    vormap_noise.save_ppm(img, "voronoi_texture.ppm")
"""

import argparse
import math
import os
import random
from vormap_utils import euclidean_xy as _euclidean
import struct
import sys

try:
    import vormap as _vormap
except ImportError:
    _vormap = None

# ---------------------------------------------------------------------------
# Colormaps — each is a list of (r, g, b) anchor colors interpolated across
# the 0-1 range.  No external dependencies needed.
# ---------------------------------------------------------------------------

COLORMAPS = {
    "grayscale": [(0, 0, 0), (255, 255, 255)],
    "fire": [(0, 0, 0), (128, 0, 0), (255, 128, 0), (255, 255, 200)],
    "ice": [(0, 0, 32), (0, 64, 128), (64, 180, 255), (220, 240, 255)],
    "earth": [(30, 20, 10), (80, 60, 30), (140, 110, 60), (200, 180, 140)],
    "neon": [(0, 0, 0), (255, 0, 128), (0, 255, 128), (255, 255, 255)],
    "ocean": [(0, 0, 40), (0, 40, 100), (0, 120, 180), (180, 220, 255)],
    "moss": [(10, 20, 10), (30, 80, 30), (80, 160, 60), (180, 220, 160)],
    "lava": [(20, 0, 0), (160, 20, 0), (255, 160, 0), (255, 255, 128)],
}

MODES = ["f1", "f2", "f3", "f2-f1", "f1*f2", "f2/f1", "manhattan", "chebyshev"]


def _lerp_color(colors, t):
    """Interpolate through a color ramp at position *t* (0-1)."""
    t = max(0.0, min(1.0, t))
    n = len(colors) - 1
    idx = t * n
    i = min(int(idx), n - 1)
    frac = idx - i
    r = int(colors[i][0] + (colors[i + 1][0] - colors[i][0]) * frac)
    g = int(colors[i][1] + (colors[i + 1][1] - colors[i][1]) * frac)
    b = int(colors[i][2] + (colors[i + 1][2] - colors[i][2]) * frac)
    return (r, g, b)


# ---------------------------------------------------------------------------
# Seed generation
# ---------------------------------------------------------------------------

def _generate_seeds(n, width, height, rng=None):
    """Generate *n* random seed points within the canvas."""
    rng = rng or random.Random()
    return [(rng.random() * width, rng.random() * height) for _ in range(n)]


def _tile_seeds(seeds, width, height):
    """Replicate seeds into a 3x3 grid for seamless tiling."""
    tiled = []
    for dx in (-width, 0, width):
        for dy in (-height, 0, height):
            for x, y in seeds:
                tiled.append((x + dx, y + dy))
    return tiled


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------


def _manhattan(px, py, sx, sy):
    return abs(px - sx) + abs(py - sy)


def _chebyshev(px, py, sx, sy):
    return max(abs(px - sx), abs(py - sy))


# ---------------------------------------------------------------------------
# Core noise generation
# ---------------------------------------------------------------------------

def _compute_distances(px, py, seeds, dist_fn, k=3):
    """Return sorted list of the *k* nearest distances from (px, py) to seeds."""
    dists = [dist_fn(px, py, sx, sy) for sx, sy in seeds]
    dists.sort()
    return dists[:k]


def generate(width=256, height=256, num_seeds=50, mode="f1",
             colormap="grayscale", invert=False, seed=None,
             tiled=False, octaves=1, lacunarity=2.0, persistence=0.5,
             jitter=1.0):
    """Generate a Worley noise image.

    Parameters
    ----------
    width, height : int
        Image dimensions in pixels.
    num_seeds : int
        Number of Voronoi seed points.
    mode : str
        Distance field mode — one of: f1, f2, f3, f2-f1, f1*f2, f2/f1,
        manhattan, chebyshev.
    colormap : str
        Color ramp name (see ``COLORMAPS``).
    invert : bool
        Invert the distance field before coloring.
    seed : int or None
        Random seed for reproducibility.
    tiled : bool
        Generate seamlessly tileable texture.
    octaves : int
        Fractal octaves for fBm-style layering (1 = plain).
    lacunarity : float
        Frequency multiplier per octave.
    persistence : float
        Amplitude multiplier per octave.
    jitter : float
        Seed placement randomness (0 = grid, 1 = full random).

    Returns
    -------
    list[list[tuple]]
        2D array of (r, g, b) tuples, row-major.
    """
    colors = COLORMAPS.get(colormap, COLORMAPS["grayscale"])
    rng = random.Random(seed)

    # Choose distance function
    if mode == "manhattan":
        dist_fn = _manhattan
    elif mode == "chebyshev":
        dist_fn = _chebyshev
    else:
        dist_fn = _euclidean

    k = 3  # need up to F3

    # Generate base seeds
    base_seeds = _generate_seeds(num_seeds, width, height, rng)

    if jitter < 1.0:
        # Place seeds on a grid then jitter
        cols = int(math.sqrt(num_seeds * width / height)) or 1
        rows = int(num_seeds / cols) or 1
        grid_seeds = []
        cw, ch = width / cols, height / rows
        for r in range(rows):
            for c in range(cols):
                cx = (c + 0.5) * cw + (rng.random() - 0.5) * cw * jitter
                cy = (r + 0.5) * ch + (rng.random() - 0.5) * ch * jitter
                grid_seeds.append((cx, cy))
        base_seeds = grid_seeds[:num_seeds] if len(grid_seeds) >= num_seeds else grid_seeds

    # Compute raw distance field (with octaves for fBm)
    raw = [[0.0] * width for _ in range(height)]
    max_val = 0.0

    for octave in range(octaves):
        freq = lacunarity ** octave
        amp = persistence ** octave
        n = max(int(num_seeds * freq), 4)
        if octave == 0:
            seeds = base_seeds
        else:
            seeds = _generate_seeds(n, width, height, random.Random(
                (seed or 0) + octave))

        if tiled:
            seeds = _tile_seeds(seeds, width, height)

        for y in range(height):
            for x in range(width):
                dists = _compute_distances(x, y, seeds, dist_fn, k)
                f1 = dists[0] if len(dists) > 0 else 0
                f2 = dists[1] if len(dists) > 1 else f1
                f3 = dists[2] if len(dists) > 2 else f2

                if mode == "f1" or mode == "manhattan" or mode == "chebyshev":
                    val = f1
                elif mode == "f2":
                    val = f2
                elif mode == "f3":
                    val = f3
                elif mode == "f2-f1":
                    val = f2 - f1
                elif mode == "f1*f2":
                    val = f1 * f2
                elif mode == "f2/f1":
                    val = f2 / f1 if f1 > 1e-9 else 0
                else:
                    val = f1

                raw[y][x] += val * amp

    # Find max for normalization
    for y in range(height):
        for x in range(width):
            if raw[y][x] > max_val:
                max_val = raw[y][x]

    # Normalize and colorize
    img = []
    for y in range(height):
        row = []
        for x in range(width):
            t = raw[y][x] / max_val if max_val > 0 else 0
            if invert:
                t = 1.0 - t
            row.append(_lerp_color(colors, t))
        img.append(row)

    return img


def generate_from_points(points, width=800, height=600, mode="f1",
                         colormap="grayscale", invert=False):
    """Generate Worley noise using existing VoronoiMap point data.

    Parameters
    ----------
    points : list
        Points from ``vormap.load_data()`` — list of (x, y, ...) tuples.
    width, height : int
        Output image dimensions.
    mode, colormap, invert
        Same as ``generate()``.

    Returns
    -------
    list[list[tuple]]
        2D pixel array.
    """
    # Extract (x, y) and normalize to canvas
    coords = [(p[0], p[1]) for p in points]
    if not coords:
        return [[_lerp_color(COLORMAPS.get(colormap, COLORMAPS["grayscale"]), 0)] * width
                for _ in range(height)]

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x or 1
    span_y = max_y - min_y or 1
    margin = 0.05
    seeds = [
        ((x - min_x) / span_x * width * (1 - 2 * margin) + width * margin,
         (y - min_y) / span_y * height * (1 - 2 * margin) + height * margin)
        for x, y in coords
    ]

    colors = COLORMAPS.get(colormap, COLORMAPS["grayscale"])
    dist_fn = _manhattan if mode == "manhattan" else (
        _chebyshev if mode == "chebyshev" else _euclidean)
    k = 3

    raw = [[0.0] * width for _ in range(height)]
    max_val = 0.0

    for y in range(height):
        for x in range(width):
            dists = _compute_distances(x, y, seeds, dist_fn, k)
            f1 = dists[0] if len(dists) > 0 else 0
            f2 = dists[1] if len(dists) > 1 else f1
            f3 = dists[2] if len(dists) > 2 else f2

            if mode in ("f1", "manhattan", "chebyshev"):
                val = f1
            elif mode == "f2":
                val = f2
            elif mode == "f3":
                val = f3
            elif mode == "f2-f1":
                val = f2 - f1
            elif mode == "f1*f2":
                val = f1 * f2
            elif mode == "f2/f1":
                val = f2 / f1 if f1 > 1e-9 else 0
            else:
                val = f1

            raw[y][x] = val
            if val > max_val:
                max_val = val

    img = []
    for y in range(height):
        row = []
        for x in range(width):
            t = raw[y][x] / max_val if max_val > 0 else 0
            if invert:
                t = 1.0 - t
            row.append(_lerp_color(colors, t))
        img.append(row)

    return img


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def save_ppm(img, path):
    """Save image as PPM (P6 binary) — no dependencies needed."""
    height = len(img)
    width = len(img[0]) if height else 0
    with open(path, "wb") as f:
        f.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
        for row in img:
            for r, g, b in row:
                f.write(struct.pack("BBB", r, g, b))
    return path


def save_png(img, path):
    """Save image as PNG (requires Pillow)."""
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow is required for PNG output: pip install Pillow")
    height = len(img)
    width = len(img[0]) if height else 0
    pil_img = Image.new("RGB", (width, height))
    pixels = pil_img.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = img[y][x]
    pil_img.save(path)
    return path


def save(img, path):
    """Auto-detect format from extension and save."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ppm":
        return save_ppm(img, path)
    elif ext in (".png", ".jpg", ".jpeg", ".bmp", ".tiff"):
        return save_png(img, path)
    else:
        return save_ppm(img, path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(
        description="Worley (cellular) noise generator — Voronoi-based procedural textures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes:
  f1          Distance to nearest seed (classic cells)
  f2          Distance to 2nd nearest (larger cells)
  f3          Distance to 3rd nearest
  f2-f1       Difference — produces vein/edge patterns
  f1*f2       Product — organic cell walls
  f2/f1       Ratio — bubbly/foamy look
  manhattan   F1 with Manhattan distance (diamond cells)
  chebyshev   F1 with Chebyshev distance (square cells)

Colormaps:  grayscale, fire, ice, earth, neon, ocean, moss, lava

Examples:
  %(prog)s -o cells.ppm
  %(prog)s --mode f2-f1 --colormap fire -o veins.ppm
  %(prog)s --tiled --octaves 3 --colormap ocean -o water.ppm
  %(prog)s --input seeds.txt --mode f1 --colormap earth -o terrain.ppm
""")
    p.add_argument("-W", "--width", type=int, default=256, help="Image width (default: 256)")
    p.add_argument("-H", "--height", type=int, default=256, help="Image height (default: 256)")
    p.add_argument("-n", "--num-seeds", type=int, default=50, help="Number of seed points (default: 50)")
    p.add_argument("-m", "--mode", choices=MODES, default="f1", help="Distance field mode (default: f1)")
    p.add_argument("-c", "--colormap", choices=list(COLORMAPS.keys()), default="grayscale",
                   help="Color ramp (default: grayscale)")
    p.add_argument("--invert", action="store_true", help="Invert the distance field")
    p.add_argument("-s", "--seed", type=int, default=None, help="Random seed for reproducibility")
    p.add_argument("--tiled", action="store_true", help="Generate seamlessly tileable texture")
    p.add_argument("--octaves", type=int, default=1, help="Fractal octaves (default: 1)")
    p.add_argument("--lacunarity", type=float, default=2.0, help="Frequency multiplier per octave")
    p.add_argument("--persistence", type=float, default=0.5, help="Amplitude multiplier per octave")
    p.add_argument("--jitter", type=float, default=1.0, help="Seed jitter 0-1 (0=grid, 1=random)")
    p.add_argument("-i", "--input", default=None, help="Input points file (VoronoiMap format)")
    p.add_argument("-o", "--output", default="worley.ppm", help="Output file (default: worley.ppm)")
    return p


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    print(f"Generating {args.width}x{args.height} Worley noise ({args.mode}, "
          f"{args.colormap}, {args.num_seeds} seeds)...")

    if args.input:
        if _vormap is None:
            print("Error: vormap module not found — cannot load input file", file=sys.stderr)
            sys.exit(1)
        points = _vormap.load_data(args.input)
        img = generate_from_points(points, args.width, args.height,
                                   args.mode, args.colormap, args.invert)
    else:
        img = generate(args.width, args.height, args.num_seeds,
                       args.mode, args.colormap, args.invert,
                       args.seed, args.tiled, args.octaves,
                       args.lacunarity, args.persistence, args.jitter)

    out = save(img, args.output)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
