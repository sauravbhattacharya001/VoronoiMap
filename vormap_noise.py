"""Voronoi-based noise generation (Worley / cellular noise).

Generates procedural noise textures using Voronoi distance calculations.
Supports multiple noise types (F1, F2, F2-F1), distance metrics
(Euclidean, Manhattan, Chebyshev), multi-octave fractal layering (fBm),
and seamless tileable output.

Example::

    from vormap_noise import generate_noise, save_noise_image

    # Generate F1 Worley noise
    grid = generate_noise(256, 256, seed_count=25, noise_type='f1')

    # Save as PNG
    save_noise_image(grid, 'noise.png', cmap='gray')

    # Multi-octave fractal noise
    grid = generate_noise(256, 256, seed_count=16, noise_type='f2-f1',
                          octaves=4, lacunarity=2.0, persistence=0.5)

    # Tileable texture
    grid = generate_noise(256, 256, seed_count=20, tileable=True)
"""

import argparse
import math
import os
import random
import sys
import warnings

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    np = None
    _HAS_NUMPY = False

try:
    from scipy.spatial import KDTree
    _HAS_SCIPY = True
except ImportError:
    KDTree = None
    _HAS_SCIPY = False

# ---------------------------------------------------------------------------
# Distance metrics
# ---------------------------------------------------------------------------

def _euclidean(x1, y1, x2, y2):
    return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)

def _manhattan(x1, y1, x2, y2):
    return abs(x1 - x2) + abs(y1 - y2)

def _chebyshev(x1, y1, x2, y2):
    return max(abs(x1 - x2), abs(y1 - y2))

_METRICS = {
    'euclidean': _euclidean,
    'manhattan': _manhattan,
    'chebyshev': _chebyshev,
}

# ---------------------------------------------------------------------------
# Seed generation helpers
# ---------------------------------------------------------------------------

def _generate_seeds(count, width, height, rng):
    """Generate *count* random seed points in [0, width) x [0, height)."""
    return [(rng.random() * width, rng.random() * height) for _ in range(count)]


def _tile_seeds(seeds, width, height):
    """Replicate seeds into a 3x3 grid for seamless tiling."""
    tiled = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for sx, sy in seeds:
                tiled.append((sx + dx * width, sy + dy * height))
    return tiled

# ---------------------------------------------------------------------------
# Core single-octave noise
# ---------------------------------------------------------------------------

def _worley_grid_pure(width, height, seeds, metric_fn, noise_type, tileable,
                      tile_w, tile_h):
    """Pure-Python Worley noise grid computation."""
    grid = [[0.0] * width for _ in range(height)]
    if tileable:
        seeds = _tile_seeds(seeds, tile_w, tile_h)

    for row in range(height):
        for col in range(width):
            px, py = col + 0.5, row + 0.5
            dists = sorted(metric_fn(px, py, sx, sy) for sx, sy in seeds)
            if noise_type == 'f1':
                grid[row][col] = dists[0] if dists else 0.0
            elif noise_type == 'f2':
                grid[row][col] = dists[1] if len(dists) > 1 else (dists[0] if dists else 0.0)
            elif noise_type == 'f2-f1':
                if len(dists) > 1:
                    grid[row][col] = dists[1] - dists[0]
                else:
                    grid[row][col] = 0.0
            else:
                raise ValueError(f"Unknown noise type: {noise_type!r}")
    return grid


def _worley_grid_np(width, height, seeds, metric_name, noise_type, tileable,
                    tile_w, tile_h):
    """NumPy-accelerated Worley noise grid computation."""
    if tileable:
        seeds = _tile_seeds(seeds, tile_w, tile_h)

    seed_arr = np.array(seeds)  # (N, 2)
    xs = np.arange(width) + 0.5
    ys = np.arange(height) + 0.5
    gx, gy = np.meshgrid(xs, ys)  # (H, W)
    gx_flat = gx.ravel()
    gy_flat = gy.ravel()

    # Compute distances from every pixel to every seed
    # shape: (H*W, N)
    dx = gx_flat[:, None] - seed_arr[:, 0][None, :]
    dy = gy_flat[:, None] - seed_arr[:, 1][None, :]

    if metric_name == 'euclidean':
        dists = np.sqrt(dx ** 2 + dy ** 2)
    elif metric_name == 'manhattan':
        dists = np.abs(dx) + np.abs(dy)
    elif metric_name == 'chebyshev':
        dists = np.maximum(np.abs(dx), np.abs(dy))
    else:
        raise ValueError(f"Unknown metric: {metric_name!r}")

    # Sort along seed axis
    dists.sort(axis=1)

    if noise_type == 'f1':
        vals = dists[:, 0]
    elif noise_type == 'f2':
        vals = dists[:, 1] if dists.shape[1] > 1 else dists[:, 0]
    elif noise_type == 'f2-f1':
        if dists.shape[1] > 1:
            vals = dists[:, 1] - dists[:, 0]
        else:
            vals = np.zeros(dists.shape[0])
    else:
        raise ValueError(f"Unknown noise type: {noise_type!r}")

    grid_np = vals.reshape(height, width)
    return grid_np


def _compute_single_octave(width, height, seeds, metric, noise_type,
                           tileable, tile_w, tile_h):
    """Dispatch to numpy or pure-python implementation."""
    if _HAS_NUMPY:
        return _worley_grid_np(width, height, seeds, metric, noise_type,
                               tileable, tile_w, tile_h)
    else:
        metric_fn = _METRICS[metric]
        return _worley_grid_pure(width, height, seeds, metric_fn, noise_type,
                                 tileable, tile_w, tile_h)

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalize_grid(grid):
    """Normalize grid values to [0, 1]. Accepts list-of-lists or ndarray."""
    if _HAS_NUMPY and isinstance(grid, np.ndarray):
        mn, mx = grid.min(), grid.max()
        if mx - mn < 1e-12:
            return np.zeros_like(grid)
        return (grid - mn) / (mx - mn)
    else:
        flat = [v for row in grid for v in row]
        mn, mx = min(flat), max(flat)
        if mx - mn < 1e-12:
            return [[0.0] * len(grid[0]) for _ in grid]
        height = len(grid)
        width = len(grid[0])
        return [[(grid[r][c] - mn) / (mx - mn) for c in range(width)]
                for r in range(height)]


def _add_grids(a, b, weight_b):
    """Add grid *b* scaled by *weight_b* to grid *a* in-place / return."""
    if _HAS_NUMPY and isinstance(a, np.ndarray):
        return a + b * weight_b
    else:
        height, width = len(a), len(a[0])
        return [[a[r][c] + b[r][c] * weight_b for c in range(width)]
                for r in range(height)]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_noise(width, height, *, seed_count=25, noise_type='f1',
                   metric='euclidean', octaves=1, lacunarity=2.0,
                   persistence=0.5, tileable=False, seed=None):
    """Generate a 2D Worley noise grid.

    Parameters
    ----------
    width, height : int
        Dimensions of the output grid in pixels.
    seed_count : int
        Number of Voronoi seed points for the base octave.
    noise_type : str
        ``'f1'`` (nearest), ``'f2'`` (2nd nearest), or ``'f2-f1'``
        (edge detection).
    metric : str
        ``'euclidean'``, ``'manhattan'``, or ``'chebyshev'``.
    octaves : int
        Number of fractal octaves (1 = single layer).
    lacunarity : float
        Frequency multiplier per octave (typically 2.0).
    persistence : float
        Amplitude multiplier per octave (typically 0.5).
    tileable : bool
        If True, output tiles seamlessly.
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    list[list[float]] or numpy.ndarray
        Normalised noise values in [0, 1]. Returns ndarray when NumPy is
        available, otherwise nested lists.
    """
    if metric not in _METRICS:
        raise ValueError(f"metric must be one of {list(_METRICS)}, got {metric!r}")
    if noise_type not in ('f1', 'f2', 'f2-f1'):
        raise ValueError(f"noise_type must be 'f1', 'f2', or 'f2-f1', got {noise_type!r}")
    if width < 1 or height < 1:
        raise ValueError("width and height must be >= 1")
    if seed_count < 1:
        raise ValueError("seed_count must be >= 1")
    if octaves < 1:
        raise ValueError("octaves must be >= 1")

    rng = random.Random(seed)

    # Accumulate octaves
    combined = None
    amplitude = 1.0
    freq = 1.0

    for _oct in range(octaves):
        cur_count = max(1, int(seed_count * freq))
        seeds = _generate_seeds(cur_count, width, height, rng)
        octave_grid = _compute_single_octave(width, height, seeds, metric,
                                              noise_type, tileable, width,
                                              height)
        if combined is None:
            if _HAS_NUMPY and isinstance(octave_grid, np.ndarray):
                combined = octave_grid * amplitude
            else:
                combined = [[v * amplitude for v in row] for row in octave_grid]
        else:
            combined = _add_grids(combined, octave_grid, amplitude)

        amplitude *= persistence
        freq *= lacunarity

    return _normalize_grid(combined)


def noise_to_nested_list(grid):
    """Convert a noise grid (ndarray or list) to a nested Python list."""
    if _HAS_NUMPY and isinstance(grid, np.ndarray):
        return grid.tolist()
    return [list(row) for row in grid]


def save_noise_image(grid, path, *, cmap='gray', dpi=100):
    """Save a noise grid as a PNG image via matplotlib.

    Parameters
    ----------
    grid : array-like
        2D noise grid (values in [0, 1]).
    path : str
        Output file path (e.g. ``'noise.png'``).
    cmap : str
        Matplotlib colormap name.
    dpi : int
        Output resolution.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        raise ImportError("matplotlib is required for save_noise_image()")

    if _HAS_NUMPY and not isinstance(grid, np.ndarray):
        grid = np.array(grid)

    fig, ax = plt.subplots(1, 1, figsize=(6, 6))
    ax.imshow(grid, cmap=cmap, origin='upper', interpolation='nearest')
    ax.axis('off')
    fig.savefig(path, dpi=dpi, bbox_inches='tight', pad_inches=0)
    plt.close(fig)


def save_noise_raw(grid, path):
    """Save noise grid as space-separated text values."""
    with open(path, 'w') as fh:
        for row in (grid if not (_HAS_NUMPY and isinstance(grid, np.ndarray)) else grid.tolist()):
            fh.write(' '.join(f'{v:.6f}' for v in row) + '\n')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    p = argparse.ArgumentParser(
        description='Generate Voronoi (Worley) noise textures.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Examples:
  python vormap_noise.py -W 256 -H 256 -n 25 -o noise.png
  python vormap_noise.py -W 512 -H 512 -t f2-f1 -m manhattan --octaves 4 -o edge.png
  python vormap_noise.py -W 256 -H 256 --tileable -o tile.png
  python vormap_noise.py -W 128 -H 128 --raw-output noise.txt
''')
    p.add_argument('-W', '--width', type=int, default=256,
                   help='Image width in pixels (default: 256)')
    p.add_argument('-H', '--height', type=int, default=256,
                   help='Image height in pixels (default: 256)')
    p.add_argument('-n', '--seed-count', type=int, default=25,
                   help='Number of seed points (default: 25)')
    p.add_argument('-t', '--noise-type', choices=['f1', 'f2', 'f2-f1'],
                   default='f1',
                   help='Noise function: f1=nearest, f2=2nd nearest, f2-f1=edges (default: f1)')
    p.add_argument('-m', '--metric', choices=['euclidean', 'manhattan', 'chebyshev'],
                   default='euclidean',
                   help='Distance metric (default: euclidean)')
    p.add_argument('--octaves', type=int, default=1,
                   help='Number of fractal octaves (default: 1)')
    p.add_argument('--lacunarity', type=float, default=2.0,
                   help='Frequency multiplier per octave (default: 2.0)')
    p.add_argument('--persistence', type=float, default=0.5,
                   help='Amplitude decay per octave (default: 0.5)')
    p.add_argument('--tileable', action='store_true',
                   help='Generate seamlessly tileable noise')
    p.add_argument('-s', '--seed', type=int, default=None,
                   help='Random seed for reproducibility')
    p.add_argument('-o', '--output', default='noise.png',
                   help='Output image path (default: noise.png)')
    p.add_argument('--cmap', default='gray',
                   help='Matplotlib colormap (default: gray)')
    p.add_argument('--dpi', type=int, default=100,
                   help='Output DPI (default: 100)')
    p.add_argument('--raw-output', default=None,
                   help='Also save raw values as text file')
    return p


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    grid = generate_noise(
        args.width, args.height,
        seed_count=args.seed_count,
        noise_type=args.noise_type,
        metric=args.metric,
        octaves=args.octaves,
        lacunarity=args.lacunarity,
        persistence=args.persistence,
        tileable=args.tileable,
        seed=args.seed,
    )

    save_noise_image(grid, args.output, cmap=args.cmap, dpi=args.dpi)
    print(f"Saved noise image: {args.output}")

    if args.raw_output:
        save_noise_raw(grid, args.raw_output)
        print(f"Saved raw values: {args.raw_output}")


if __name__ == '__main__':
    main()
