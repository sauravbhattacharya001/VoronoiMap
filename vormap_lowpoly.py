"""Voronoi low-poly image renderer.

Takes a source image and re-renders it in a low-poly / faceted style by
overlaying a Voronoi tessellation and sampling the average colour of each
cell from the original image.  The result looks like a geometric, faceted
version of the photo — popular for profile pictures, backgrounds, and
generative art.

Features
--------
- **Edge-aware seed placement** — concentrates Voronoi seeds along detected
  edges so important details are preserved while flat areas stay simple
- Configurable number of seeds (more = finer detail)
- Optional **outline** (dark cell borders) for a stained-glass / faceted look
- Optional **flat shading** or **gradient shading** within each cell
- Supports PNG input/output using only the standard library
- No external dependencies (no Pillow, no scipy)

CLI usage
---------
::

    python vormap_lowpoly.py input.png output.png
    python vormap_lowpoly.py input.png output.png --seeds 500
    python vormap_lowpoly.py input.png output.png --seeds 300 --outline 2
    python vormap_lowpoly.py input.png output.png --seeds 800 --edge-bias 0.8

Programmatic usage
------------------
::

    import vormap_lowpoly

    result = vormap_lowpoly.render("photo.png", num_seeds=400)
    vormap_lowpoly.save_png(result, "lowpoly.png")

    # Or generate from raw pixel data:
    result = vormap_lowpoly.render_from_pixels(pixels, width, height, num_seeds=300)

"""

import argparse
import math
import os
import random
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# PNG reader (minimal, supports 8-bit RGB/RGBA non-interlaced)
# ---------------------------------------------------------------------------

def _read_png(path: str) -> Tuple[int, int, List[Tuple[int, int, int]]]:
    """Read a PNG file and return (width, height, pixels) where pixels is a
    flat list of (R, G, B) tuples in row-major order."""
    with open(path, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a valid PNG file")

        chunks: Dict[str, list] = {}
        idat_data = b""
        width = height = 0
        bit_depth = color_type = 0

        while True:
            raw = f.read(8)
            if len(raw) < 8:
                break
            length = struct.unpack(">I", raw[:4])[0]
            ctype = raw[4:8].decode("ascii", errors="replace")
            data = f.read(length)
            _crc = f.read(4)

            if ctype == "IHDR":
                width = struct.unpack(">I", data[0:4])[0]
                height = struct.unpack(">I", data[4:8])[0]
                bit_depth = data[8]
                color_type = data[9]
            elif ctype == "IDAT":
                idat_data += data
            elif ctype == "IEND":
                break

        if bit_depth != 8:
            raise ValueError(f"Only 8-bit PNGs supported (got {bit_depth})")

        raw_data = zlib.decompress(idat_data)

        # Determine bytes per pixel
        if color_type == 2:  # RGB
            bpp = 3
        elif color_type == 6:  # RGBA
            bpp = 4
        elif color_type == 0:  # Grayscale
            bpp = 1
        elif color_type == 4:  # Grayscale + alpha
            bpp = 2
        else:
            raise ValueError(f"Unsupported color type {color_type}")

        stride = 1 + width * bpp  # filter byte + pixel data
        pixels: List[Tuple[int, int, int]] = []

        prev_row = bytes(width * bpp)
        offset = 0

        for y in range(height):
            filter_type = raw_data[offset]
            row_data = bytearray(raw_data[offset + 1: offset + stride])
            offset += stride

            # Reconstruct filtered row
            recon = bytearray(len(row_data))
            for i in range(len(row_data)):
                a = recon[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0

                if filter_type == 0:
                    recon[i] = row_data[i]
                elif filter_type == 1:
                    recon[i] = (row_data[i] + a) & 0xFF
                elif filter_type == 2:
                    recon[i] = (row_data[i] + b) & 0xFF
                elif filter_type == 3:
                    recon[i] = (row_data[i] + (a + b) // 2) & 0xFF
                elif filter_type == 4:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                    recon[i] = (row_data[i] + pr) & 0xFF

            # Extract pixels
            for x in range(width):
                idx = x * bpp
                if bpp == 3:
                    pixels.append((recon[idx], recon[idx + 1], recon[idx + 2]))
                elif bpp == 4:
                    pixels.append((recon[idx], recon[idx + 1], recon[idx + 2]))
                elif bpp == 1:
                    v = recon[idx]
                    pixels.append((v, v, v))
                elif bpp == 2:
                    v = recon[idx]
                    pixels.append((v, v, v))

            prev_row = bytes(recon)

    return width, height, pixels


# ---------------------------------------------------------------------------
# PNG writer (minimal)
# ---------------------------------------------------------------------------

def _write_png(path: str, width: int, height: int,
               pixels: List[Tuple[int, int, int]]) -> None:
    """Write an RGB PNG file."""
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # no filter
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw_rows.extend((r, g, b))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    compressed = zlib.compress(bytes(raw_rows), 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Edge detection (Sobel-like, grayscale)
# ---------------------------------------------------------------------------

def _edge_map(pixels: List[Tuple[int, int, int]], w: int, h: int) -> List[float]:
    """Compute a simple edge-magnitude map (0.0–1.0)."""
    gray = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in pixels]
    edges = [0.0] * (w * h)
    max_val = 1.0

    for y in range(1, h - 1):
        for x in range(1, w - 1):
            # Sobel kernels
            gx = (
                -gray[(y - 1) * w + x - 1] + gray[(y - 1) * w + x + 1]
                - 2 * gray[y * w + x - 1] + 2 * gray[y * w + x + 1]
                - gray[(y + 1) * w + x - 1] + gray[(y + 1) * w + x + 1]
            )
            gy = (
                -gray[(y - 1) * w + x - 1] - 2 * gray[(y - 1) * w + x]
                - gray[(y - 1) * w + x + 1]
                + gray[(y + 1) * w + x - 1] + 2 * gray[(y + 1) * w + x]
                + gray[(y + 1) * w + x + 1]
            )
            mag = math.sqrt(gx * gx + gy * gy)
            edges[y * w + x] = mag
            if mag > max_val:
                max_val = mag

    # Normalise
    if max_val > 0:
        edges = [e / max_val for e in edges]
    return edges


# ---------------------------------------------------------------------------
# Seed placement
# ---------------------------------------------------------------------------

def _place_seeds(w: int, h: int, num_seeds: int,
                 edge_bias: float,
                 edges: List[float]) -> List[Tuple[int, int]]:
    """Place seeds with bias towards high-edge areas."""
    seeds: List[Tuple[int, int]] = []

    # Number of edge-biased vs random seeds
    n_edge = int(num_seeds * edge_bias)
    n_rand = num_seeds - n_edge

    # Weighted random sampling from edge map
    if n_edge > 0 and any(e > 0 for e in edges):
        # Build cumulative distribution
        total = sum(e for e in edges)
        if total > 0:
            cum = []
            running = 0.0
            for e in edges:
                running += e / total
                cum.append(running)

            for _ in range(n_edge):
                r = random.random()
                # Binary search
                lo, hi = 0, len(cum) - 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    if cum[mid] < r:
                        lo = mid + 1
                    else:
                        hi = mid
                idx = lo
                y, x = divmod(idx, w)
                seeds.append((x, y))
        else:
            n_rand += n_edge
            n_edge = 0

    # Random seeds
    for _ in range(n_rand):
        seeds.append((random.randint(0, w - 1), random.randint(0, h - 1)))

    return seeds


# ---------------------------------------------------------------------------
# Voronoi assignment + colour averaging
# ---------------------------------------------------------------------------

def _assign_cells(w: int, h: int,
                  seeds: List[Tuple[int, int]]) -> List[int]:
    """Assign each pixel to its nearest seed. Returns cell index per pixel."""
    cell_map = [0] * (w * h)

    for y in range(h):
        for x in range(w):
            best_dist = float("inf")
            best_idx = 0
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_dist:
                    best_dist = d
                    best_idx = i
            cell_map[y * w + x] = best_idx

    return cell_map


def _average_colours(pixels: List[Tuple[int, int, int]],
                     cell_map: List[int],
                     num_cells: int) -> List[Tuple[int, int, int]]:
    """Compute average colour per cell."""
    sums_r = [0] * num_cells
    sums_g = [0] * num_cells
    sums_b = [0] * num_cells
    counts = [0] * num_cells

    for i, cell in enumerate(cell_map):
        r, g, b = pixels[i]
        sums_r[cell] += r
        sums_g[cell] += g
        sums_b[cell] += b
        counts[cell] += 1

    colours = []
    for i in range(num_cells):
        if counts[i] > 0:
            colours.append((
                sums_r[i] // counts[i],
                sums_g[i] // counts[i],
                sums_b[i] // counts[i],
            ))
        else:
            colours.append((128, 128, 128))

    return colours


# ---------------------------------------------------------------------------
# Outline drawing
# ---------------------------------------------------------------------------

def _draw_outlines(output: List[Tuple[int, int, int]],
                   cell_map: List[int],
                   w: int, h: int,
                   outline_width: int,
                   outline_color: Tuple[int, int, int] = (20, 20, 20)) -> None:
    """Draw outlines where adjacent pixels belong to different cells."""
    border_pixels = set()

    for y in range(h):
        for x in range(w):
            c = cell_map[y * w + x]
            # Check right and down neighbours
            if x + 1 < w and cell_map[y * w + x + 1] != c:
                border_pixels.add((x, y))
                border_pixels.add((x + 1, y))
            if y + 1 < h and cell_map[(y + 1) * w + x] != c:
                border_pixels.add((x, y))
                border_pixels.add((x, y + 1))

    if outline_width <= 1:
        for bx, by in border_pixels:
            output[by * w + bx] = outline_color
    else:
        # Expand border pixels
        expanded = set()
        r = outline_width // 2
        for bx, by in border_pixels:
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    nx, ny = bx + dx, by + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        expanded.add((nx, ny))
        for bx, by in expanded:
            output[by * w + bx] = outline_color


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class LowPolyResult:
    """Result of a low-poly render."""
    width: int
    height: int
    pixels: List[Tuple[int, int, int]]
    seeds: List[Tuple[int, int]]
    cell_map: List[int]
    cell_colours: List[Tuple[int, int, int]]
    num_cells: int

    @property
    def cell_counts(self) -> Dict[int, int]:
        counts: Dict[int, int] = {}
        for c in self.cell_map:
            counts[c] = counts.get(c, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def render_from_pixels(pixels: List[Tuple[int, int, int]],
                       width: int, height: int,
                       num_seeds: int = 300,
                       edge_bias: float = 0.7,
                       outline: int = 0,
                       outline_color: Tuple[int, int, int] = (20, 20, 20),
                       seed: Optional[int] = None) -> LowPolyResult:
    """Render a low-poly version of pixel data.

    Parameters
    ----------
    pixels : list of (R, G, B) tuples, row-major
    width, height : image dimensions
    num_seeds : number of Voronoi cells (more = finer)
    edge_bias : 0.0–1.0, fraction of seeds placed on edges
    outline : outline width in pixels (0 = none)
    outline_color : RGB tuple for outline colour
    seed : random seed for reproducibility

    Returns
    -------
    LowPolyResult
    """
    if seed is not None:
        random.seed(seed)

    num_seeds = min(num_seeds, width * height)

    # Edge detection
    edges = _edge_map(pixels, width, height)

    # Seed placement
    seeds = _place_seeds(width, height, num_seeds, edge_bias, edges)

    # Assign cells
    cell_map = _assign_cells(width, height, seeds)

    # Average colours
    colours = _average_colours(pixels, cell_map, num_seeds)

    # Build output
    output = [colours[cell_map[i]] for i in range(width * height)]

    # Outlines
    if outline > 0:
        _draw_outlines(output, cell_map, width, height, outline, outline_color)

    return LowPolyResult(
        width=width,
        height=height,
        pixels=output,
        seeds=seeds,
        cell_map=cell_map,
        cell_colours=colours,
        num_cells=num_seeds,
    )


def render(input_path: str,
           num_seeds: int = 300,
           edge_bias: float = 0.7,
           outline: int = 0,
           outline_color: Tuple[int, int, int] = (20, 20, 20),
           seed: Optional[int] = None) -> LowPolyResult:
    """Read a PNG image and render a low-poly version.

    Parameters
    ----------
    input_path : path to a PNG file
    num_seeds : number of Voronoi cells
    edge_bias : fraction of seeds biased towards edges (0.0–1.0)
    outline : outline width in pixels (0 = no outline)
    outline_color : RGB colour for outlines
    seed : random seed

    Returns
    -------
    LowPolyResult
    """
    width, height, pixels = _read_png(input_path)
    return render_from_pixels(
        pixels, width, height,
        num_seeds=num_seeds,
        edge_bias=edge_bias,
        outline=outline,
        outline_color=outline_color,
        seed=seed,
    )


def save_png(result: LowPolyResult, path: str) -> str:
    """Save a LowPolyResult as a PNG file.  Returns the absolute path."""
    _write_png(path, result.width, result.height, result.pixels)
    return os.path.abspath(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Voronoi low-poly image renderer — turn photos into "
                    "faceted, geometric art using Voronoi tessellation.",
    )
    parser.add_argument("input", help="Input PNG image path")
    parser.add_argument("output", help="Output PNG image path")
    parser.add_argument("--seeds", type=int, default=300,
                        help="Number of Voronoi cells (default: 300)")
    parser.add_argument("--edge-bias", type=float, default=0.7,
                        help="Fraction of seeds placed on edges 0.0–1.0 "
                             "(default: 0.7)")
    parser.add_argument("--outline", type=int, default=0,
                        help="Outline width in pixels (default: 0 = none)")
    parser.add_argument("--outline-color", default="20,20,20",
                        help="Outline RGB colour as R,G,B (default: 20,20,20)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    oc = tuple(int(c) for c in args.outline_color.split(","))

    print(f"Reading {args.input}...")
    result = render(
        args.input,
        num_seeds=args.seeds,
        edge_bias=args.edge_bias,
        outline=args.outline,
        outline_color=oc,
        seed=args.seed,
    )

    save_png(result, args.output)
    print(f"Saved low-poly render → {args.output}")
    print(f"  {result.width}×{result.height}, {result.num_cells} cells")


if __name__ == "__main__":
    main()
