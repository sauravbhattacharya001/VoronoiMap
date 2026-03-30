"""Voronoi Stippling — Image-to-dot-art using weighted Voronoi tessellation.

Converts grayscale images into stippled dot art where darker regions
produce denser point distributions.  Uses iterative weighted Lloyd
relaxation: each seed moves toward the density-weighted centroid of
its Voronoi cell.

Algorithm:
    1. Sample initial points with probability proportional to image darkness.
    2. Compute Voronoi diagram.
    3. For each cell, compute density-weighted centroid using the image.
    4. Move seeds to weighted centroids.
    5. Repeat for N iterations.

Example::

    from vormap_stipple import stipple_image
    points = stipple_image("photo.png", n_points=5000, iterations=30)

CLI::

    python vormap_stipple.py input.png --points 5000 --output stippled.svg
    python vormap_stipple.py input.jpg -n 3000 -i 40 --dot-size 1.5 --svg out.svg
    python vormap_stipple.py input.png -n 8000 --invert --json coords.json
    python vormap_stipple.py input.png -n 5000 --animate --svg-frames frames/

Requires: numpy, scipy, Pillow (``pip install numpy scipy Pillow``).
"""

import argparse
import json
import math
import os
import sys

import vormap

try:
    import numpy as np
    from scipy.spatial import Voronoi, cKDTree
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _load_image_density(image_path, max_dim=800):
    """Load image as grayscale density map (0=white, 1=black).

    Parameters
    ----------
    image_path : str
        Path to image file (PNG, JPG, BMP, etc.).
    max_dim : int
        Maximum dimension — image is resized to fit for performance.

    Returns
    -------
    density : np.ndarray
        2D float array in [0, 1], shape (height, width).
    width : int
    height : int
    """
    if not _HAS_PIL:
        raise ImportError("Pillow is required: pip install Pillow")

    img = Image.open(image_path).convert("L")

    # Resize if too large
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        w2, h2 = int(w * scale), int(h * scale)
        img = img.resize((w2, h2), Image.LANCZOS)
    else:
        w2, h2 = w, h

    arr = np.array(img, dtype=np.float64)
    # Invert: 0 (black in image) -> 1 (high density), 255 (white) -> 0
    density = 1.0 - arr / 255.0
    return density, w2, h2


def _sample_initial_points(density, n_points, rng=None):
    """Sample points with probability proportional to density."""
    if rng is None:
        rng = np.random.default_rng(42)

    h, w = density.shape
    # Flatten density to probability distribution
    flat = density.flatten()
    total = flat.sum()
    if total < 1e-12:
        # Uniform if image is all white
        flat = np.ones_like(flat)
        total = flat.sum()
    probs = flat / total

    indices = rng.choice(len(flat), size=n_points, p=probs)
    ys, xs = np.divmod(indices, w)

    # Add sub-pixel jitter
    xs = xs.astype(np.float64) + rng.uniform(-0.5, 0.5, size=n_points)
    ys = ys.astype(np.float64) + rng.uniform(-0.5, 0.5, size=n_points)

    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)

    return np.column_stack([xs, ys])


def _points_in_polygon(points, polygon):
    """Vectorized ray-casting point-in-polygon test.

    Retained for backward compatibility and direct polygon queries.
    The main Lloyd relaxation now uses KD-tree instead.
    """
    n = len(polygon)
    inside = np.zeros(len(points), dtype=bool)

    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]

        cond1 = (yi > points[:, 1]) != (yj > points[:, 1])
        slope = (xj - xi) * (points[:, 1] - yi) / (yj - yi + 1e-30) + xi
        cond2 = points[:, 0] < slope

        inside ^= (cond1 & cond2)
        j = i

    return inside


def _weighted_lloyd_step(points, density, width, height):
    """One step of density-weighted Lloyd relaxation.

    Uses a KD-tree to assign every pixel to its nearest seed in bulk,
    then computes density-weighted centroids via numpy aggregation.
    This is dramatically faster than the previous per-cell polygon
    rasterization approach (O(pixels) vs O(cells * cell_area * vertices)).

    Returns new points and max displacement.
    """
    n = len(points)
    h, w = density.shape

    # Build KD-tree of seed points
    tree = cKDTree(points)

    # Create grid of all pixel coordinates
    gy, gx = np.mgrid[0:h, 0:w]
    pixel_coords = np.column_stack([gx.ravel(), gy.ravel()])

    # Assign each pixel to nearest seed — single bulk query
    _, labels = tree.query(pixel_coords)

    # Flatten density for vectorized aggregation
    flat_density = density.ravel()

    # Compute weighted centroids using np.bincount (vectorized, no loops)
    weights_per_seed = np.bincount(labels, weights=flat_density, minlength=n)
    wx_per_seed = np.bincount(labels, weights=flat_density * pixel_coords[:, 0], minlength=n)
    wy_per_seed = np.bincount(labels, weights=flat_density * pixel_coords[:, 1], minlength=n)

    # Update points where weight is sufficient
    new_points = points.copy()
    valid = weights_per_seed > 1e-12
    new_points[valid, 0] = wx_per_seed[valid] / weights_per_seed[valid]
    new_points[valid, 1] = wy_per_seed[valid] / weights_per_seed[valid]

    # Clamp to image bounds
    new_points[:, 0] = np.clip(new_points[:, 0], 0, w - 1)
    new_points[:, 1] = np.clip(new_points[:, 1], 0, h - 1)

    displacement = np.sqrt(((new_points - points) ** 2).sum(axis=1)).max()
    return new_points, displacement


def stipple_image(image_path, n_points=5000, iterations=30,
                  tolerance=0.1, invert=False, max_dim=800, seed=42):
    """Convert an image to stipple points via weighted Voronoi relaxation.

    Parameters
    ----------
    image_path : str
        Path to input image.
    n_points : int
        Number of stipple dots.
    iterations : int
        Maximum Lloyd relaxation iterations.
    tolerance : float
        Convergence threshold (max displacement in pixels).
    invert : bool
        If True, light regions get more dots (invert density).
    max_dim : int
        Max image dimension for processing.
    seed : int
        Random seed.

    Returns
    -------
    dict with keys:
        points : np.ndarray, shape (n, 2)
        width : int
        height : int
        iterations_run : int
        final_displacement : float
    """
    if not _HAS_DEPS:
        raise ImportError("numpy and scipy required: pip install numpy scipy")

    density, width, height = _load_image_density(image_path, max_dim)
    if invert:
        density = 1.0 - density

    # Boost contrast
    density = density ** 1.5
    # Small floor to avoid zero-density regions trapping points
    density = np.maximum(density, 0.01)

    rng = np.random.default_rng(seed)
    points = _sample_initial_points(density, n_points, rng)

    disp = float('inf')
    iters = 0
    for i in range(iterations):
        points, disp = _weighted_lloyd_step(points, density, width, height)
        iters = i + 1
        if disp < tolerance:
            break

    return {
        "points": points,
        "width": width,
        "height": height,
        "iterations_run": iters,
        "final_displacement": disp,
    }


def points_to_svg(points, width, height, dot_size=1.2, bg_color="white",
                  dot_color="black"):
    """Render stipple points as an SVG string.

    Parameters
    ----------
    points : np.ndarray
        (N, 2) array of (x, y) coordinates.
    width, height : int
        Canvas dimensions.
    dot_size : float
        Radius of each dot.
    bg_color : str
        Background fill color.
    dot_color : str
        Dot fill color.

    Returns
    -------
    str : SVG markup.
    """
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}">',
        f'<rect width="{width}" height="{height}" fill="{bg_color}"/>',
    ]
    for x, y in points:
        lines.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{dot_size}" '
            f'fill="{dot_color}"/>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


def points_to_json(points, width, height, extra=None):
    """Export stipple points as JSON.

    Returns
    -------
    str : JSON string with points, dimensions, and optional metadata.
    """
    data = {
        "width": width,
        "height": height,
        "n_points": len(points),
        "points": [[round(float(x), 2), round(float(y), 2)] for x, y in points],
    }
    if extra:
        data.update(extra)
    return json.dumps(data, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description="Voronoi Stippling — convert images to dot art",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python vormap_stipple.py photo.png --points 5000 --svg out.svg\n"
            "  python vormap_stipple.py photo.jpg -n 3000 -i 40 --dot-size 2\n"
            "  python vormap_stipple.py photo.png -n 8000 --invert --json coords.json\n"
        ),
    )
    parser.add_argument("image", help="Input image file (PNG, JPG, BMP, etc.)")
    parser.add_argument("-n", "--points", type=int, default=5000,
                        help="Number of stipple points (default: 5000)")
    parser.add_argument("-i", "--iterations", type=int, default=30,
                        help="Max Lloyd iterations (default: 30)")
    parser.add_argument("--tolerance", type=float, default=0.1,
                        help="Convergence tolerance in pixels (default: 0.1)")
    parser.add_argument("--invert", action="store_true",
                        help="Invert density (light areas get more dots)")
    parser.add_argument("--max-dim", type=int, default=800,
                        help="Max image dimension for processing (default: 800)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--dot-size", type=float, default=1.2,
                        help="SVG dot radius (default: 1.2)")
    parser.add_argument("--bg-color", default="white",
                        help="SVG background color (default: white)")
    parser.add_argument("--dot-color", default="black",
                        help="SVG dot color (default: black)")
    parser.add_argument("--svg", metavar="FILE",
                        help="Output SVG file path")
    parser.add_argument("--json", metavar="FILE",
                        help="Output JSON file path")
    parser.add_argument("--output", "-o", metavar="FILE",
                        help="Output text file (x y per line)")
    parser.add_argument("--animate", action="store_true",
                        help="Save SVG frames for each iteration to --svg-frames dir")
    parser.add_argument("--svg-frames", metavar="DIR",
                        help="Directory for animation frames (used with --animate)")

    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"Error: Image file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading image: {args.image}")
    density, width, height = _load_image_density(args.image, args.max_dim)
    if args.invert:
        density = 1.0 - density
    density = density ** 1.5
    density = np.maximum(density, 0.01)

    rng = np.random.default_rng(args.seed)
    points = _sample_initial_points(density, args.points, rng)

    frames_dir = args.svg_frames
    if args.animate and not frames_dir:
        frames_dir = os.path.splitext(args.image)[0] + "_frames"

    if args.animate and frames_dir:
        os.makedirs(frames_dir, exist_ok=True)

    print(f"Stippling with {args.points} points, up to {args.iterations} iterations...")
    disp = float('inf')
    for i in range(args.iterations):
        points, disp = _weighted_lloyd_step(points, density, width, height)
        pct = (i + 1) / args.iterations * 100
        print(f"  Iteration {i+1}/{args.iterations} — max displacement: {disp:.3f}px ({pct:.0f}%)")

        if args.animate and frames_dir:
            frame_svg = points_to_svg(points, width, height,
                                      args.dot_size, args.bg_color, args.dot_color)
            frame_path = os.path.join(frames_dir, f"frame_{i+1:04d}.svg")
            with open(frame_path, "w") as f:
                f.write(frame_svg)

        if disp < args.tolerance:
            print(f"  Converged at iteration {i+1}")
            break

    iters_run = min(i + 1, args.iterations) if args.iterations > 0 else 0
    print(f"Done. {len(points)} points, {iters_run} iterations, "
          f"final displacement: {disp:.3f}px")

    # Write outputs
    if args.svg:
        svg = points_to_svg(points, width, height, args.dot_size,
                            args.bg_color, args.dot_color)
        safe_svg = vormap.validate_output_path(args.svg, allow_absolute=True)
        with open(safe_svg, "w") as f:
            f.write(svg)
        print(f"SVG written to {args.svg}")

    if args.json:
        js = points_to_json(points, width, height, {
            "iterations_run": iters_run,
            "final_displacement": round(disp, 4),
        })
        safe_json = vormap.validate_output_path(args.json, allow_absolute=True)
        with open(safe_json, "w") as f:
            f.write(js)
        print(f"JSON written to {args.json}")

    if args.output:
        safe_out = vormap.validate_output_path(args.output, allow_absolute=True)
        with open(safe_out, "w") as f:
            for x, y in points:
                f.write(f"{x:.4f} {y:.4f}\n")
        print(f"Coordinates written to {args.output}")


if __name__ == "__main__":
    main()
