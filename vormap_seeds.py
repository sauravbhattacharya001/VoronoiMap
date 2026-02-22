"""Seed point generators for Voronoi diagrams.

Provides multiple point distribution algorithms for generating seed
points within a rectangular bounding box. Each generator returns a list
of (x, y) tuples suitable for use with VoronoiMap's ``load_data()`` or
direct computation.

Generators:
    - ``random_uniform``: Uniformly distributed random points
    - ``grid``: Regular rectangular grid
    - ``hexagonal``: Hexagonal (honeycomb) grid for equilateral spacing
    - ``jittered_grid``: Regular grid with random perturbation
    - ``poisson_disk``: Poisson disk sampling (Bridson's algorithm)
      for blue-noise distributions with guaranteed minimum spacing
    - ``halton``: Halton low-discrepancy quasi-random sequence

Example::

    from vormap_seeds import random_uniform, poisson_disk, save_seeds

    # Generate 100 random points in a 1000x1000 box
    points = random_uniform(100, 0, 1000, 0, 1000, seed=42)

    # Generate well-spaced points with minimum distance 50
    points = poisson_disk(0, 1000, 0, 1000, min_dist=50, seed=42)

    # Save to file for use with vormap.py
    save_seeds(points, "my_seeds.txt")
"""

import math
import random as _random


def random_uniform(n, x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0,
                   *, seed=None):
    """Generate *n* uniformly distributed random points.

    Parameters
    ----------
    n : int
        Number of points to generate (must be >= 1).
    x_min, x_max : float
        Horizontal bounds.
    y_min, y_max : float
        Vertical bounds.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    list[tuple[float, float]]
        List of (x, y) coordinate tuples.
    """
    _validate_bounds(x_min, x_max, y_min, y_max)
    if not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n}")

    rng = _random.Random(seed)
    return [(rng.uniform(x_min, x_max), rng.uniform(y_min, y_max))
            for _ in range(n)]


def grid(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0,
         *, rows=10, cols=10, margin=0.0):
    """Generate points on a regular rectangular grid.

    Parameters
    ----------
    x_min, x_max : float
        Horizontal bounds.
    y_min, y_max : float
        Vertical bounds.
    rows : int
        Number of rows (must be >= 1).
    cols : int
        Number of columns (must be >= 1).
    margin : float
        Inset from bounds on all sides (must be >= 0).

    Returns
    -------
    list[tuple[float, float]]
        Grid points, row-major order.
    """
    _validate_bounds(x_min, x_max, y_min, y_max)
    if not isinstance(rows, int) or rows < 1:
        raise ValueError(f"rows must be a positive integer, got {rows}")
    if not isinstance(cols, int) or cols < 1:
        raise ValueError(f"cols must be a positive integer, got {cols}")
    if margin < 0:
        raise ValueError(f"margin must be non-negative, got {margin}")

    eff_x_min = x_min + margin
    eff_x_max = x_max - margin
    eff_y_min = y_min + margin
    eff_y_max = y_max - margin

    if eff_x_min >= eff_x_max or eff_y_min >= eff_y_max:
        raise ValueError("margin too large for the given bounds")

    x_step = (eff_x_max - eff_x_min) / max(cols - 1, 1)
    y_step = (eff_y_max - eff_y_min) / max(rows - 1, 1)

    points = []
    for r in range(rows):
        y = eff_y_min + r * y_step if rows > 1 else (eff_y_min + eff_y_max) / 2
        for c in range(cols):
            x = eff_x_min + c * x_step if cols > 1 else (eff_x_min + eff_x_max) / 2
            points.append((x, y))
    return points


def hexagonal(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0,
              *, spacing=100.0, margin=0.0):
    """Generate points on a hexagonal (honeycomb) grid.

    Odd rows are offset by half the horizontal spacing, producing
    equilateral triangular spacing between nearest neighbours.

    Parameters
    ----------
    x_min, x_max : float
        Horizontal bounds.
    y_min, y_max : float
        Vertical bounds.
    spacing : float
        Distance between adjacent points in the same row (must be > 0).
    margin : float
        Inset from bounds on all sides (must be >= 0).

    Returns
    -------
    list[tuple[float, float]]
        Hexagonally arranged points.
    """
    _validate_bounds(x_min, x_max, y_min, y_max)
    if spacing <= 0:
        raise ValueError(f"spacing must be positive, got {spacing}")
    if margin < 0:
        raise ValueError(f"margin must be non-negative, got {margin}")

    eff_x_min = x_min + margin
    eff_x_max = x_max - margin
    eff_y_min = y_min + margin
    eff_y_max = y_max - margin

    if eff_x_min >= eff_x_max or eff_y_min >= eff_y_max:
        raise ValueError("margin too large for the given bounds")

    row_height = spacing * math.sqrt(3) / 2
    points = []
    row = 0
    y = eff_y_min
    while y <= eff_y_max:
        x_offset = spacing / 2 if row % 2 == 1 else 0
        x = eff_x_min + x_offset
        while x <= eff_x_max:
            points.append((x, y))
            x += spacing
        y += row_height
        row += 1
    return points


def jittered_grid(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0,
                  *, rows=10, cols=10, jitter=0.5, margin=0.0, seed=None):
    """Generate a regular grid with random jitter applied to each point.

    Each grid point is displaced randomly within its cell. The *jitter*
    parameter controls the displacement magnitude as a fraction of the
    cell size (0 = no jitter, 1 = full cell displacement).

    Parameters
    ----------
    x_min, x_max : float
        Horizontal bounds.
    y_min, y_max : float
        Vertical bounds.
    rows, cols : int
        Grid dimensions (each must be >= 1).
    jitter : float
        Displacement fraction, 0.0–1.0.
    margin : float
        Inset from bounds on all sides.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    list[tuple[float, float]]
        Jittered grid points.
    """
    _validate_bounds(x_min, x_max, y_min, y_max)
    if not isinstance(rows, int) or rows < 1:
        raise ValueError(f"rows must be a positive integer, got {rows}")
    if not isinstance(cols, int) or cols < 1:
        raise ValueError(f"cols must be a positive integer, got {cols}")
    if not 0 <= jitter <= 1:
        raise ValueError(f"jitter must be between 0 and 1, got {jitter}")
    if margin < 0:
        raise ValueError(f"margin must be non-negative, got {margin}")

    eff_x_min = x_min + margin
    eff_x_max = x_max - margin
    eff_y_min = y_min + margin
    eff_y_max = y_max - margin

    if eff_x_min >= eff_x_max or eff_y_min >= eff_y_max:
        raise ValueError("margin too large for the given bounds")

    rng = _random.Random(seed)
    cell_w = (eff_x_max - eff_x_min) / cols
    cell_h = (eff_y_max - eff_y_min) / rows

    points = []
    for r in range(rows):
        for c in range(cols):
            cx = eff_x_min + (c + 0.5) * cell_w
            cy = eff_y_min + (r + 0.5) * cell_h
            dx = rng.uniform(-0.5, 0.5) * cell_w * jitter
            dy = rng.uniform(-0.5, 0.5) * cell_h * jitter
            points.append((cx + dx, cy + dy))
    return points


def poisson_disk(x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0,
                 *, min_dist=50.0, max_attempts=30, seed=None):
    """Generate well-spaced points using Bridson's Poisson disk sampling.

    Produces a blue-noise distribution where no two points are closer
    than *min_dist*. This gives more aesthetically pleasing and
    uniform-looking Voronoi diagrams than pure random sampling.

    Uses Bridson's O(n) algorithm with a background grid for fast
    neighbour lookups.

    Parameters
    ----------
    x_min, x_max : float
        Horizontal bounds.
    y_min, y_max : float
        Vertical bounds.
    min_dist : float
        Minimum distance between any two points (must be > 0).
    max_attempts : int
        Candidates tried per active point before giving up (default 30).
        Higher values produce denser packings but take longer.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    list[tuple[float, float]]
        Poisson disk distributed points.

    Notes
    -----
    The number of output points depends on the area and *min_dist*.
    For a rough estimate: ``n ≈ area / (min_dist² * π/4)``.
    """
    _validate_bounds(x_min, x_max, y_min, y_max)
    if min_dist <= 0:
        raise ValueError(f"min_dist must be positive, got {min_dist}")
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    rng = _random.Random(seed)

    width = x_max - x_min
    height = y_max - y_min
    cell_size = min_dist / math.sqrt(2)
    grid_w = max(1, int(math.ceil(width / cell_size)))
    grid_h = max(1, int(math.ceil(height / cell_size)))

    # Background grid: -1 means empty, otherwise index into points list
    bg_grid = [[-1] * grid_w for _ in range(grid_h)]

    points = []
    active = []

    def _grid_coords(x, y):
        gx = int((x - x_min) / cell_size)
        gy = int((y - y_min) / cell_size)
        return min(gx, grid_w - 1), min(gy, grid_h - 1)

    def _is_valid(x, y):
        if x < x_min or x > x_max or y < y_min or y > y_max:
            return False
        gx, gy = _grid_coords(x, y)
        # Check 5x5 neighbourhood
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                nx, ny = gx + dx, gy + dy
                if 0 <= nx < grid_w and 0 <= ny < grid_h:
                    idx = bg_grid[ny][nx]
                    if idx != -1:
                        px, py = points[idx]
                        dist_sq = (x - px) ** 2 + (y - py) ** 2
                        if dist_sq < min_dist * min_dist:
                            return False
        return True

    def _add_point(x, y):
        idx = len(points)
        points.append((x, y))
        active.append(idx)
        gx, gy = _grid_coords(x, y)
        bg_grid[gy][gx] = idx

    # Start with a random initial point
    x0 = rng.uniform(x_min, x_max)
    y0 = rng.uniform(y_min, y_max)
    _add_point(x0, y0)

    while active:
        # Pick a random active point
        active_idx = rng.randint(0, len(active) - 1)
        point_idx = active[active_idx]
        px, py = points[point_idx]

        found = False
        for _ in range(max_attempts):
            # Generate random point in annulus [min_dist, 2*min_dist]
            angle = rng.uniform(0, 2 * math.pi)
            dist = rng.uniform(min_dist, 2 * min_dist)
            nx = px + dist * math.cos(angle)
            ny = py + dist * math.sin(angle)

            if _is_valid(nx, ny):
                _add_point(nx, ny)
                found = True
                break

        if not found:
            # Remove from active list (swap with last for O(1))
            active[active_idx] = active[-1]
            active.pop()

    return points


def halton(n, x_min=0.0, x_max=1000.0, y_min=0.0, y_max=1000.0,
           *, bases=(2, 3), skip=0):
    """Generate *n* points using a Halton low-discrepancy sequence.

    Halton sequences produce quasi-random points that fill space more
    evenly than pseudo-random sampling, with no minimum distance
    guarantee but better coverage at low point counts.

    Parameters
    ----------
    n : int
        Number of points to generate (must be >= 1).
    x_min, x_max : float
        Horizontal bounds.
    y_min, y_max : float
        Vertical bounds.
    bases : tuple[int, int]
        Co-prime bases for the two dimensions (default: (2, 3)).
    skip : int
        Number of initial sequence elements to skip (helps avoid
        the low-quality initial points). Must be >= 0.

    Returns
    -------
    list[tuple[float, float]]
        Halton sequence points scaled to the bounding box.
    """
    _validate_bounds(x_min, x_max, y_min, y_max)
    if not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive integer, got {n}")
    if len(bases) != 2 or any(b < 2 for b in bases):
        raise ValueError(f"bases must be two integers >= 2, got {bases}")
    if skip < 0:
        raise ValueError(f"skip must be non-negative, got {skip}")

    def _halton_value(index, base):
        result = 0.0
        f = 1.0 / base
        i = index
        while i > 0:
            result += f * (i % base)
            i //= base
            f /= base
        return result

    width = x_max - x_min
    height = y_max - y_min
    b1, b2 = bases

    points = []
    for i in range(skip + 1, skip + n + 1):
        x = x_min + _halton_value(i, b1) * width
        y = y_min + _halton_value(i, b2) * height
        points.append((x, y))
    return points


# ---------------------------------------------------------------------------
#  I/O utilities
# ---------------------------------------------------------------------------

def save_seeds(points, filename, header=True):
    """Save seed points to a text file compatible with ``vormap.py``.

    Output format is one point per line: ``x y`` (space-separated).
    An optional header line with the point count is prepended.

    Parameters
    ----------
    points : list[tuple[float, float]]
        Seed points to save.
    filename : str
        Output file path.
    header : bool
        If True, write point count as the first line.
    """
    if not points:
        raise ValueError("points list is empty")
    if not filename:
        raise ValueError("filename must not be empty")

    with open(filename, 'w') as f:
        if header:
            f.write(f"{len(points)}\n")
        for x, y in points:
            f.write(f"{x} {y}\n")


def load_seeds(filename):
    """Load seed points from a text file.

    Supports files with or without a header count line. Lines with
    exactly two numeric values are treated as points; other lines are
    skipped.

    Parameters
    ----------
    filename : str
        Input file path.

    Returns
    -------
    list[tuple[float, float]]
        Loaded seed points.
    """
    if not filename:
        raise ValueError("filename must not be empty")

    points = []
    with open(filename, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) == 2:
                try:
                    x, y = float(parts[0]), float(parts[1])
                    points.append((x, y))
                except ValueError:
                    continue  # skip non-numeric lines
    return points


# ---------------------------------------------------------------------------
#  Validation
# ---------------------------------------------------------------------------

def _validate_bounds(x_min, x_max, y_min, y_max):
    """Raise ValueError if bounds are invalid."""
    if x_min >= x_max:
        raise ValueError(
            f"x_min must be less than x_max, got {x_min} >= {x_max}")
    if y_min >= y_max:
        raise ValueError(
            f"y_min must be less than y_max, got {y_min} >= {y_max}")


# ---------------------------------------------------------------------------
#  CLI
# ---------------------------------------------------------------------------

def main():
    """Command-line interface for seed point generation."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate seed points for Voronoi diagrams",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s random 100 -o seeds.txt
  %(prog)s poisson --min-dist 50 --seed 42 -o seeds.txt
  %(prog)s grid --rows 20 --cols 20
  %(prog)s hex --spacing 80
  %(prog)s jittered --rows 15 --cols 15 --jitter 0.3
  %(prog)s halton 200 --skip 10 -o seeds.txt

  # Pipe directly into vormap.py:
  %(prog)s random 50 -o seeds.txt && python vormap.py seeds.txt
""")

    sub = parser.add_subparsers(dest='generator', required=True)

    # Common arguments
    def add_common(p):
        p.add_argument('--bounds', type=float, nargs=4,
                        metavar=('XMIN', 'XMAX', 'YMIN', 'YMAX'),
                        default=[0, 1000, 0, 1000],
                        help='Bounding box (default: 0 1000 0 1000)')
        p.add_argument('-o', '--output', help='Output file path')
        p.add_argument('--no-header', action='store_true',
                        help='Omit point count header in output')

    # random
    p_rand = sub.add_parser('random', help='Uniformly random points')
    p_rand.add_argument('n', type=int, help='Number of points')
    p_rand.add_argument('--seed', type=int, help='Random seed')
    add_common(p_rand)

    # grid
    p_grid = sub.add_parser('grid', help='Regular rectangular grid')
    p_grid.add_argument('--rows', type=int, default=10)
    p_grid.add_argument('--cols', type=int, default=10)
    p_grid.add_argument('--margin', type=float, default=0.0)
    add_common(p_grid)

    # hex
    p_hex = sub.add_parser('hex', help='Hexagonal (honeycomb) grid')
    p_hex.add_argument('--spacing', type=float, default=100.0)
    p_hex.add_argument('--margin', type=float, default=0.0)
    add_common(p_hex)

    # jittered
    p_jit = sub.add_parser('jittered', help='Jittered grid')
    p_jit.add_argument('--rows', type=int, default=10)
    p_jit.add_argument('--cols', type=int, default=10)
    p_jit.add_argument('--jitter', type=float, default=0.5)
    p_jit.add_argument('--margin', type=float, default=0.0)
    p_jit.add_argument('--seed', type=int, help='Random seed')
    add_common(p_jit)

    # poisson
    p_poi = sub.add_parser('poisson', help='Poisson disk sampling')
    p_poi.add_argument('--min-dist', type=float, default=50.0)
    p_poi.add_argument('--max-attempts', type=int, default=30)
    p_poi.add_argument('--seed', type=int, help='Random seed')
    add_common(p_poi)

    # halton
    p_hal = sub.add_parser('halton', help='Halton quasi-random sequence')
    p_hal.add_argument('n', type=int, help='Number of points')
    p_hal.add_argument('--skip', type=int, default=0)
    p_hal.add_argument('--bases', type=int, nargs=2, default=[2, 3])
    add_common(p_hal)

    args = parser.parse_args()
    b = args.bounds
    x_min, x_max, y_min, y_max = b[0], b[1], b[2], b[3]

    gen = args.generator
    if gen == 'random':
        pts = random_uniform(args.n, x_min, x_max, y_min, y_max,
                             seed=args.seed)
    elif gen == 'grid':
        pts = grid(x_min, x_max, y_min, y_max,
                   rows=args.rows, cols=args.cols, margin=args.margin)
    elif gen == 'hex':
        pts = hexagonal(x_min, x_max, y_min, y_max,
                        spacing=args.spacing, margin=args.margin)
    elif gen == 'jittered':
        pts = jittered_grid(x_min, x_max, y_min, y_max,
                            rows=args.rows, cols=args.cols,
                            jitter=args.jitter, margin=args.margin,
                            seed=args.seed)
    elif gen == 'poisson':
        pts = poisson_disk(x_min, x_max, y_min, y_max,
                           min_dist=args.min_dist,
                           max_attempts=args.max_attempts,
                           seed=args.seed)
    elif gen == 'halton':
        pts = halton(args.n, x_min, x_max, y_min, y_max,
                     bases=tuple(args.bases), skip=args.skip)

    print(f"Generated {len(pts)} points ({gen})")

    if args.output:
        save_seeds(pts, args.output, header=not args.no_header)
        print(f"Saved to {args.output}")
    else:
        for x, y in pts:
            print(f"{x:.6f} {y:.6f}")


if __name__ == '__main__':
    main()
