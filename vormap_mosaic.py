"""Voronoi mosaic image filter — stained-glass and pixel-art effects.

Transforms an image into a Voronoi mosaic by:

1. Generating seed points on the image canvas (grid, random, Poisson disk,
   or edge-aware placement).
2. Assigning each pixel to its nearest seed → Voronoi regions.
3. Filling each region with the average (or median/dominant) colour of
   its source pixels.
4. Optionally drawing cell borders for a stained-glass look.

Supports both NumPy-accelerated and pure-Python fallback paths.

CLI usage
---------
::

    python vormap_mosaic.py input.png output.png --seeds 500
    python vormap_mosaic.py photo.jpg mosaic.png --seeds 1000 --edges --edge-color 000000
    python vormap_mosaic.py img.png art.png --seeds 2000 --placement edge_aware --color-mode median

"""


import argparse
import math
import os
import random
import struct
import zlib
from collections import defaultdict
from typing import List, Tuple, Optional, Dict

import vormap

# ── Optional dependency detection ──

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from scipy.spatial import KDTree
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ── Pure-Python minimal PNG reader/writer ──
# (so the module works without Pillow)


# Maximum image dimensions to prevent memory exhaustion from malicious PNGs.
# 16384 × 16384 × 3 bytes ≈ 768 MB — generous for real images, safe for servers.
_MAX_PNG_DIMENSION = 16384

# Maximum total IDAT (compressed) payload — 256 MB guards against chunk bombs.
_MAX_IDAT_BYTES = 256 * 1024 * 1024

# Maximum decompressed pixel data — prevents zip bombs.
_MAX_DECOMPRESSED_BYTES = 1024 * 1024 * 1024  # 1 GB


def _read_png(filepath: str) -> Tuple[int, int, list]:
    """Read a PNG file, return (width, height, pixels).

    *pixels* is a flat list of (r, g, b) tuples in row-major order.
    Supports 8-bit RGB and RGBA (alpha is discarded).

    Security hardening
    ------------------
    * Image dimensions capped at ``_MAX_PNG_DIMENSION`` to prevent OOM.
    * Cumulative IDAT size capped at ``_MAX_IDAT_BYTES``.
    * Decompressed size capped at ``_MAX_DECOMPRESSED_BYTES`` (zip-bomb guard).
    * Individual chunk lengths are validated against remaining file size.
    * CRC of every chunk is verified; mismatches raise ``ValueError``.
    """
    with open(vormap.validate_input_path(filepath, allow_absolute=True), "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a valid PNG file")

        width = height = bit_depth = color_type = 0
        idat_chunks = []
        idat_total = 0

        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            length = struct.unpack(">I", header[:4])[0]
            chunk_type = header[4:8]

            # Guard against absurd chunk lengths (max valid PNG chunk is 2^31-1,
            # but we use a tighter practical limit).
            if length > _MAX_IDAT_BYTES:
                raise ValueError(
                    f"PNG chunk {chunk_type!r} declares {length:,} bytes — "
                    f"exceeds safety limit"
                )

            data = f.read(length)
            if len(data) != length:
                raise ValueError("Truncated PNG chunk")

            crc_bytes = f.read(4)
            if len(crc_bytes) != 4:
                raise ValueError("Truncated PNG CRC")

            # Verify CRC (CRC covers chunk type + data)
            expected_crc = struct.unpack(">I", crc_bytes)[0]
            actual_crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                raise ValueError(
                    f"CRC mismatch in PNG chunk {chunk_type!r} "
                    f"(expected {expected_crc:#010x}, got {actual_crc:#010x})"
                )

            if chunk_type == b"IHDR":
                if length < 13:
                    raise ValueError("IHDR chunk too small")
                width = struct.unpack(">I", data[0:4])[0]
                height = struct.unpack(">I", data[4:8])[0]

                if width == 0 or height == 0:
                    raise ValueError("PNG has zero dimension")
                if width > _MAX_PNG_DIMENSION or height > _MAX_PNG_DIMENSION:
                    raise ValueError(
                        f"PNG dimensions {width}×{height} exceed safety limit "
                        f"of {_MAX_PNG_DIMENSION}×{_MAX_PNG_DIMENSION}"
                    )

                bit_depth = data[8]
                color_type = data[9]
                if bit_depth != 8:
                    raise ValueError(f"Only 8-bit PNGs supported, got {bit_depth}")
                if color_type not in (2, 6):
                    raise ValueError(
                        f"Only RGB(A) PNGs supported, got color_type={color_type}"
                    )
            elif chunk_type == b"IDAT":
                idat_total += length
                if idat_total > _MAX_IDAT_BYTES:
                    raise ValueError(
                        f"Cumulative IDAT size ({idat_total:,} bytes) exceeds "
                        f"safety limit of {_MAX_IDAT_BYTES:,} bytes"
                    )
                idat_chunks.append(data)
            elif chunk_type == b"IEND":
                break

        if not idat_chunks:
            raise ValueError("PNG contains no IDAT chunks")

        raw = zlib.decompress(b"".join(idat_chunks))

        if len(raw) > _MAX_DECOMPRESSED_BYTES:
            raise ValueError(
                f"Decompressed PNG data ({len(raw):,} bytes) exceeds safety "
                f"limit — possible zip bomb"
            )
        channels = 4 if color_type == 6 else 3
        stride = 1 + width * channels  # filter byte + pixel bytes

        pixels: list = []
        prev_row = b"\x00" * (width * channels)
        offset = 0
        for _y in range(height):
            filter_byte = raw[offset]
            offset += 1
            row_raw = bytearray(raw[offset : offset + width * channels])
            offset += width * channels

            if filter_byte == 1:  # Sub
                for i in range(channels, len(row_raw)):
                    row_raw[i] = (row_raw[i] + row_raw[i - channels]) & 0xFF
            elif filter_byte == 2:  # Up
                for i in range(len(row_raw)):
                    row_raw[i] = (row_raw[i] + prev_row[i]) & 0xFF
            elif filter_byte == 3:  # Average
                for i in range(len(row_raw)):
                    left = row_raw[i - channels] if i >= channels else 0
                    up = prev_row[i]
                    row_raw[i] = (row_raw[i] + (left + up) // 2) & 0xFF
            elif filter_byte == 4:  # Paeth
                for i in range(len(row_raw)):
                    a = row_raw[i - channels] if i >= channels else 0
                    b_val = prev_row[i]
                    c = prev_row[i - channels] if i >= channels else 0
                    p = a + b_val - c
                    pa, pb, pc = abs(p - a), abs(p - b_val), abs(p - c)
                    if pa <= pb and pa <= pc:
                        pred = a
                    elif pb <= pc:
                        pred = b_val
                    else:
                        pred = c
                    row_raw[i] = (row_raw[i] + pred) & 0xFF

            prev_row = bytes(row_raw)
            for x in range(width):
                idx = x * channels
                r, g, b = row_raw[idx], row_raw[idx + 1], row_raw[idx + 2]
                pixels.append((r, g, b))

        return width, height, pixels


def _write_png(filepath: str, width: int, height: int, pixels: list) -> None:
    """Write an RGB PNG file.

    *pixels* is a flat list of (r, g, b) tuples in row-major order.
    """
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # filter=None
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw_rows.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), 9)

    with open(vormap.validate_output_path(filepath, allow_absolute=True), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ── Seed placement strategies ──

def _place_grid(width: int, height: int, n_seeds: int,
                jitter: float = 0.3) -> List[Tuple[int, int]]:
    """Place seeds on a jittered grid.

    Parameters
    ----------
    jitter : float
        Fraction of cell size to randomly perturb each seed (0 = pure grid).
    """
    aspect = width / height if height > 0 else 1.0
    cols = max(1, int(math.sqrt(n_seeds * aspect)))
    rows = max(1, int(n_seeds / cols))

    cell_w = width / cols
    cell_h = height / rows

    seeds = []
    for r in range(rows):
        for c in range(cols):
            cx = (c + 0.5) * cell_w
            cy = (r + 0.5) * cell_h
            if jitter > 0:
                cx += random.uniform(-jitter, jitter) * cell_w
                cy += random.uniform(-jitter, jitter) * cell_h
            sx = max(0, min(width - 1, int(cx)))
            sy = max(0, min(height - 1, int(cy)))
            seeds.append((sx, sy))

    return seeds


def _place_random(width: int, height: int, n_seeds: int) -> List[Tuple[int, int]]:
    """Place seeds uniformly at random."""
    return [(random.randint(0, width - 1), random.randint(0, height - 1))
            for _ in range(n_seeds)]


def _place_poisson_disk(width: int, height: int, n_seeds: int) -> List[Tuple[int, int]]:
    """Place seeds via Poisson disk sampling (Bridson's algorithm).

    Guarantees a minimum spacing between seeds for more uniform regions.
    """
    area = width * height
    r = math.sqrt(area / (n_seeds * math.pi)) * 0.85
    cell_size = r / math.sqrt(2)

    grid_w = max(1, int(math.ceil(width / cell_size)))
    grid_h = max(1, int(math.ceil(height / cell_size)))
    grid: Dict[Tuple[int, int], Tuple[int, int]] = {}

    def _grid_key(px: float, py: float) -> Tuple[int, int]:
        return (int(px / cell_size), int(py / cell_size))

    def _has_nearby(px: float, py: float) -> bool:
        gx, gy = _grid_key(px, py)
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                key = (gx + dx, gy + dy)
                if key in grid:
                    nx, ny = grid[key]
                    if (px - nx) ** 2 + (py - ny) ** 2 < r * r:
                        return True
        return False

    seeds: List[Tuple[int, int]] = []
    x0 = random.uniform(0, width - 1)
    y0 = random.uniform(0, height - 1)
    seeds.append((int(x0), int(y0)))
    grid[_grid_key(x0, y0)] = (int(x0), int(y0))
    active = [(x0, y0)]

    k = 30  # candidates per point
    while active and len(seeds) < n_seeds:
        idx = random.randint(0, len(active) - 1)
        ax, ay = active[idx]
        found = False
        for _ in range(k):
            angle = random.uniform(0, 2 * math.pi)
            dist = random.uniform(r, 2 * r)
            nx = ax + dist * math.cos(angle)
            ny = ay + dist * math.sin(angle)
            if 0 <= nx < width and 0 <= ny < height and not _has_nearby(nx, ny):
                pt = (int(nx), int(ny))
                seeds.append(pt)
                grid[_grid_key(nx, ny)] = pt
                active.append((nx, ny))
                found = True
                if len(seeds) >= n_seeds:
                    break
        if not found:
            active.pop(idx)

    return seeds[:n_seeds]


def _luminance(r: int, g: int, b: int) -> float:
    """Relative luminance (BT.709)."""
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _place_edge_aware(width: int, height: int, n_seeds: int,
                      pixels: list) -> List[Tuple[int, int]]:
    """Place seeds preferring high-contrast edges.

    Uses a Sobel-like gradient magnitude to build a probability map,
    then samples seed positions proportional to gradient strength.
    Falls back to random placement for extra seeds if needed.
    """
    # Compute gradient magnitude at each pixel (simple finite difference)
    gradient = []
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            lum = _luminance(*pixels[idx])
            gx = gy = 0.0
            if x + 1 < width:
                gx = _luminance(*pixels[idx + 1]) - lum
            if y + 1 < height:
                gy = _luminance(*pixels[idx + width]) - lum
            gradient.append(math.sqrt(gx * gx + gy * gy))

    total = sum(gradient) + 1e-10

    # Weighted random sampling (reservoir-style for memory efficiency)
    # First place ~30% seeds randomly to ensure coverage of flat areas
    n_random = max(1, n_seeds // 3)
    n_edge = n_seeds - n_random

    seeds_set = set()
    for _ in range(n_random):
        seeds_set.add((random.randint(0, width - 1), random.randint(0, height - 1)))

    # Sample remaining proportional to gradient
    attempts = 0
    max_attempts = n_edge * 20
    while len(seeds_set) < n_seeds and attempts < max_attempts:
        # Roulette wheel selection
        threshold = random.uniform(0, total)
        cumulative = 0.0
        chosen = len(gradient) - 1
        for i, g in enumerate(gradient):
            cumulative += g
            if cumulative >= threshold:
                chosen = i
                break
        sx = chosen % width
        sy = chosen // width
        seeds_set.add((sx, sy))
        attempts += 1

    result = list(seeds_set)[:n_seeds]
    # Fill remaining if we didn't get enough
    while len(result) < n_seeds:
        result.append((random.randint(0, width - 1), random.randint(0, height - 1)))

    return result


PLACEMENT_STRATEGIES = {
    "grid": _place_grid,
    "random": _place_random,
    "poisson": _place_poisson_disk,
}


# ── Core mosaic engine ──

class VoronoiMosaic:
    """Voronoi-based image mosaic generator.

    Parameters
    ----------
    width : int
        Image width in pixels.
    height : int
        Image height in pixels.
    pixels : list of (r, g, b)
        Flat row-major pixel data.
    """

    def __init__(self, width: int, height: int, pixels: list):
        if width <= 0 or height <= 0:
            raise ValueError("Image dimensions must be positive")
        if len(pixels) != width * height:
            raise ValueError(
                f"Expected {width * height} pixels, got {len(pixels)}"
            )
        self.width = width
        self.height = height
        self.pixels = pixels

    def generate_seeds(self, n_seeds: int, placement: str = "grid",
                       jitter: float = 0.3,
                       rng_seed: Optional[int] = None) -> List[Tuple[int, int]]:
        """Generate seed points for the mosaic.

        Parameters
        ----------
        n_seeds : int
            Number of Voronoi cells (more = finer detail).
        placement : str
            Strategy: 'grid', 'random', 'poisson', or 'edge_aware'.
        jitter : float
            Grid jitter amount (only for 'grid' placement).
        rng_seed : int or None
            Random seed for reproducibility.

        Returns
        -------
        list of (x, y) tuples
        """
        if n_seeds <= 0:
            raise ValueError("n_seeds must be positive")
        if rng_seed is not None:
            random.seed(rng_seed)

        if placement == "edge_aware":
            return _place_edge_aware(self.width, self.height, n_seeds, self.pixels)
        elif placement == "grid":
            return _place_grid(self.width, self.height, n_seeds, jitter=jitter)
        elif placement in PLACEMENT_STRATEGIES:
            return PLACEMENT_STRATEGIES[placement](self.width, self.height, n_seeds)
        else:
            raise ValueError(
                f"Unknown placement '{placement}', "
                f"choose from: grid, random, poisson, edge_aware"
            )

    def assign_pixels(self, seeds: List[Tuple[int, int]]) -> List[int]:
        """Assign each pixel to its nearest seed index.

        Uses scipy KDTree when available, falls back to brute-force.

        Returns
        -------
        list of int
            Seed index for each pixel (flat, row-major).
        """
        if not seeds:
            raise ValueError("seeds list must not be empty")

        if _HAS_SCIPY and _HAS_NUMPY:
            tree = KDTree(seeds)
            coords = [(x, y) for y in range(self.height) for x in range(self.width)]
            _, indices = tree.query(coords)
            return list(indices)

        # Brute-force fallback
        assignment = []
        for y in range(self.height):
            for x in range(self.width):
                best_dist = float("inf")
                best_idx = 0
                for i, (sx, sy) in enumerate(seeds):
                    d = (x - sx) ** 2 + (y - sy) ** 2
                    if d < best_dist:
                        best_dist = d
                        best_idx = i
                assignment.append(best_idx)
        return assignment

    def compute_region_colors(self, assignment: List[int], n_seeds: int,
                              color_mode: str = "mean") -> List[Tuple[int, int, int]]:
        """Compute the fill colour for each region.

        Parameters
        ----------
        assignment : list of int
            Per-pixel seed index.
        n_seeds : int
            Total number of seeds.
        color_mode : str
            'mean' (average), 'median', or 'dominant' (most frequent).

        Returns
        -------
        list of (r, g, b) per region
        """
        # Collect pixels per region
        region_pixels: Dict[int, list] = defaultdict(list)
        for i, seed_idx in enumerate(assignment):
            region_pixels[seed_idx].append(self.pixels[i])

        colors: List[Tuple[int, int, int]] = [(0, 0, 0)] * n_seeds

        for idx in range(n_seeds):
            rpx = region_pixels.get(idx, [])
            if not rpx:
                colors[idx] = (128, 128, 128)
                continue

            if color_mode == "mean":
                r_sum = sum(p[0] for p in rpx)
                g_sum = sum(p[1] for p in rpx)
                b_sum = sum(p[2] for p in rpx)
                n = len(rpx)
                colors[idx] = (r_sum // n, g_sum // n, b_sum // n)

            elif color_mode == "median":
                rs = sorted(p[0] for p in rpx)
                gs = sorted(p[1] for p in rpx)
                bs = sorted(p[2] for p in rpx)
                mid = len(rpx) // 2
                colors[idx] = (rs[mid], gs[mid], bs[mid])

            elif color_mode == "dominant":
                # Quantize to reduce unique colours, then pick most frequent
                counter: Dict[Tuple[int, int, int], int] = defaultdict(int)
                for p in rpx:
                    q = ((p[0] // 16) * 16, (p[1] // 16) * 16, (p[2] // 16) * 16)
                    counter[q] += 1
                colors[idx] = max(counter.items(), key=lambda x: x[1])[0]
            else:
                raise ValueError(
                    f"Unknown color_mode '{color_mode}', "
                    f"choose from: mean, median, dominant"
                )

        return colors

    def render(self, assignment: List[int],
               region_colors: List[Tuple[int, int, int]],
               draw_edges: bool = False,
               edge_color: Tuple[int, int, int] = (0, 0, 0),
               edge_width: int = 1) -> list:
        """Render the mosaic image.

        Parameters
        ----------
        assignment : list of int
            Per-pixel seed index.
        region_colors : list of (r, g, b)
            Fill colour per region.
        draw_edges : bool
            Whether to draw borders between cells.
        edge_color : tuple
            RGB colour for edges.
        edge_width : int
            Edge thickness in pixels (1-3).

        Returns
        -------
        list of (r, g, b) — flat row-major pixel data
        """
        # Fill each pixel with its region colour
        output = [region_colors[idx] for idx in assignment]

        if draw_edges:
            ew = max(1, min(3, edge_width))
            for y in range(self.height):
                for x in range(self.width):
                    idx = y * self.width + x
                    cur = assignment[idx]
                    is_edge = False
                    for dy in range(-ew, ew + 1):
                        for dx in range(-ew, ew + 1):
                            if dx == 0 and dy == 0:
                                continue
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < self.height and 0 <= nx < self.width:
                                if assignment[ny * self.width + nx] != cur:
                                    is_edge = True
                                    break
                        if is_edge:
                            break
                    if is_edge:
                        output[idx] = edge_color

        return output

    def create_mosaic(self, n_seeds: int = 500,
                      placement: str = "grid",
                      color_mode: str = "mean",
                      draw_edges: bool = False,
                      edge_color: Tuple[int, int, int] = (0, 0, 0),
                      edge_width: int = 1,
                      jitter: float = 0.3,
                      rng_seed: Optional[int] = None) -> "MosaicResult":
        """Full pipeline: generate seeds → assign → colour → render.

        Returns
        -------
        MosaicResult
            Contains the output pixels, seeds, assignment, and stats.
        """
        seeds = self.generate_seeds(n_seeds, placement, jitter, rng_seed)
        assignment = self.assign_pixels(seeds)
        region_colors = self.compute_region_colors(assignment, len(seeds), color_mode)
        output = self.render(assignment, region_colors, draw_edges, edge_color, edge_width)

        # Compute stats
        region_sizes: Dict[int, int] = defaultdict(int)
        for idx in assignment:
            region_sizes[idx] += 1
        sizes = list(region_sizes.values()) if region_sizes else [0]
        avg_region = sum(sizes) / len(sizes) if sizes else 0
        min_region = min(sizes) if sizes else 0
        max_region = max(sizes) if sizes else 0

        return MosaicResult(
            width=self.width,
            height=self.height,
            pixels=output,
            seeds=seeds,
            assignment=assignment,
            region_colors=region_colors,
            n_regions=len(seeds),
            avg_region_size=avg_region,
            min_region_size=min_region,
            max_region_size=max_region,
            placement=placement,
            color_mode=color_mode,
        )


class MosaicResult:
    """Immutable result of a mosaic generation."""

    __slots__ = (
        "width", "height", "pixels", "seeds", "assignment",
        "region_colors", "n_regions", "avg_region_size",
        "min_region_size", "max_region_size", "placement", "color_mode",
    )

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)

    def save_png(self, filepath: str) -> None:
        """Save the mosaic as a PNG file."""
        _write_png(filepath, self.width, self.height, self.pixels)

    def summary(self) -> str:
        """Human-readable summary."""
        return (
            f"Voronoi Mosaic: {self.width}×{self.height}\n"
            f"  Regions:     {self.n_regions}\n"
            f"  Placement:   {self.placement}\n"
            f"  Color mode:  {self.color_mode}\n"
            f"  Region size: avg={self.avg_region_size:.0f}  "
            f"min={self.min_region_size}  max={self.max_region_size}\n"
        )

    def to_dict(self) -> dict:
        """Serializable dictionary (excludes large pixel arrays)."""
        return {
            "width": self.width,
            "height": self.height,
            "n_regions": self.n_regions,
            "placement": self.placement,
            "color_mode": self.color_mode,
            "avg_region_size": round(self.avg_region_size, 1),
            "min_region_size": self.min_region_size,
            "max_region_size": self.max_region_size,
            "seed_count": len(self.seeds),
        }

    def region_stats(self) -> dict:
        """Per-region statistics: size, colour, seed position."""
        region_sizes: Dict[int, int] = defaultdict(int)
        for idx in self.assignment:
            region_sizes[idx] += 1

        stats = {}
        for i in range(self.n_regions):
            stats[i] = {
                "seed": self.seeds[i],
                "color": self.region_colors[i],
                "size": region_sizes.get(i, 0),
            }
        return stats


# ── CLI ──

def _parse_hex_color(s: str) -> Tuple[int, int, int]:
    """Parse a hex colour string (e.g. 'FF0000' or '#FF0000')."""
    s = s.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Invalid hex colour: {s}")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def main(argv=None):
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Voronoi mosaic image filter — stained-glass effect for images"
    )
    parser.add_argument("input", help="Input PNG image path")
    parser.add_argument("output", help="Output PNG path")
    parser.add_argument("--seeds", type=int, default=500,
                        help="Number of Voronoi cells (default: 500)")
    parser.add_argument("--placement",
                        choices=["grid", "random", "poisson", "edge_aware"],
                        default="grid", help="Seed placement strategy")
    parser.add_argument("--color-mode",
                        choices=["mean", "median", "dominant"],
                        default="mean", help="Region colour computation")
    parser.add_argument("--edges", action="store_true",
                        help="Draw cell borders")
    parser.add_argument("--edge-color", default="000000",
                        help="Edge colour as hex (default: 000000)")
    parser.add_argument("--edge-width", type=int, default=1,
                        choices=[1, 2, 3], help="Edge thickness")
    parser.add_argument("--jitter", type=float, default=0.3,
                        help="Grid jitter amount (0-1)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--stats", action="store_true",
                        help="Print region statistics")

    args = parser.parse_args(argv)

    print(f"Reading {args.input}...")
    width, height, pixels = _read_png(args.input)
    print(f"  Image size: {width}×{height}")

    mosaic = VoronoiMosaic(width, height, pixels)
    edge_color = _parse_hex_color(args.edge_color)

    print(f"Generating mosaic ({args.seeds} cells, {args.placement})...")
    result = mosaic.create_mosaic(
        n_seeds=args.seeds,
        placement=args.placement,
        color_mode=args.color_mode,
        draw_edges=args.edges,
        edge_color=edge_color,
        edge_width=args.edge_width,
        jitter=args.jitter,
        rng_seed=args.seed,
    )

    print(result.summary())
    result.save_png(args.output)
    print(f"Saved to {args.output}")

    if args.stats:
        stats = result.region_stats()
        for i, s in sorted(stats.items()):
            print(f"  Region {i}: seed={s['seed']} color={s['color']} size={s['size']}")


if __name__ == "__main__":
    main()
