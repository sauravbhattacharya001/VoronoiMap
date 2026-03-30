"""Voronoi Jigsaw Puzzle Generator — split images into puzzle pieces.

Takes an input image and generates Voronoi-region puzzle pieces:

1. Generate seed points on the image canvas.
2. Compute Voronoi regions (assign each pixel to nearest seed).
3. Extract each region as an individual PNG with transparent background.
4. Optionally draw a puzzle overlay showing all piece outlines.

Works with pure Python (no Pillow/NumPy required), but accelerated
when NumPy/SciPy are available.

CLI usage
---------
::

    python vormap_jigsaw.py photo.png --pieces 20 --output-dir puzzle_pieces/
    python vormap_jigsaw.py photo.png --pieces 50 --overlay puzzle_overlay.png
    python vormap_jigsaw.py photo.png --pieces 30 --border-width 2 --border-color FF0000
    python vormap_jigsaw.py photo.png --pieces 12 --placement grid --shuffle

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

def _read_png(filepath: str) -> Tuple[int, int, list]:
    """Read a PNG file, return (width, height, pixels).

    *pixels* is a flat list of (r, g, b) tuples in row-major order.
    Supports 8-bit RGB and RGBA (alpha is discarded).
    """
    filepath = vormap.validate_input_path(filepath, allow_absolute=True)
    with open(filepath, "rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("Not a valid PNG file")

        width = height = bit_depth = color_type = 0
        idat_chunks = []

        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            length = struct.unpack(">I", header[:4])[0]
            chunk_type = header[4:8]
            data = f.read(length)
            _crc = f.read(4)

            if chunk_type == b"IHDR":
                width = struct.unpack(">I", data[0:4])[0]
                height = struct.unpack(">I", data[4:8])[0]
                bit_depth = data[8]
                color_type = data[9]
                if bit_depth != 8:
                    raise ValueError(f"Only 8-bit PNGs supported, got {bit_depth}")
                if color_type not in (2, 6):
                    raise ValueError(
                        f"Only RGB(A) PNGs supported, got color_type={color_type}"
                    )
            elif chunk_type == b"IDAT":
                idat_chunks.append(data)
            elif chunk_type == b"IEND":
                break

        raw = zlib.decompress(b"".join(idat_chunks))
        channels = 4 if color_type == 6 else 3

        pixels: list = []
        prev_row = b"\x00" * (width * channels)
        offset = 0
        for _y in range(height):
            filter_byte = raw[offset]
            offset += 1
            row_raw = bytearray(raw[offset: offset + width * channels])
            offset += width * channels

            if filter_byte == 1:
                for i in range(channels, len(row_raw)):
                    row_raw[i] = (row_raw[i] + row_raw[i - channels]) & 0xFF
            elif filter_byte == 2:
                for i in range(len(row_raw)):
                    row_raw[i] = (row_raw[i] + prev_row[i]) & 0xFF
            elif filter_byte == 3:
                for i in range(len(row_raw)):
                    left = row_raw[i - channels] if i >= channels else 0
                    up = prev_row[i]
                    row_raw[i] = (row_raw[i] + (left + up) // 2) & 0xFF
            elif filter_byte == 4:
                for i in range(len(row_raw)):
                    a = row_raw[i - channels] if i >= channels else 0
                    b = prev_row[i]
                    c = prev_row[i - channels] if i >= channels else 0
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    if pa <= pb and pa <= pc:
                        pr = a
                    elif pb <= pc:
                        pr = b
                    else:
                        pr = c
                    row_raw[i] = (row_raw[i] + pr) & 0xFF

            prev_row = bytes(row_raw)
            for x in range(width):
                idx = x * channels
                pixels.append((row_raw[idx], row_raw[idx + 1], row_raw[idx + 2]))

        return width, height, pixels


def _write_png_rgba(filepath: str, width: int, height: int,
                    pixels: list) -> None:
    """Write an RGBA PNG. *pixels* is a flat list of (r, g, b, a) tuples."""
    filepath = vormap.validate_output_path(filepath, allow_absolute=True)

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)  # no filter
        for x in range(width):
            r, g, b, a = pixels[y * width + x]
            raw_rows.extend((r, g, b, a))

    compressed = zlib.compress(bytes(raw_rows), 9)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    with open(filepath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr_data))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


def _write_png_rgb(filepath: str, width: int, height: int,
                   pixels: list) -> None:
    """Write an RGB PNG. *pixels* is a flat list of (r, g, b) tuples."""
    filepath = vormap.validate_output_path(filepath, allow_absolute=True)

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    raw_rows = bytearray()
    for y in range(height):
        raw_rows.append(0)
        for x in range(width):
            r, g, b = pixels[y * width + x]
            raw_rows.extend((r, g, b))

    compressed = zlib.compress(bytes(raw_rows), 9)
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    with open(filepath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", ihdr_data))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ── Seed placement ──

def _place_seeds_random(width: int, height: int, n: int,
                        rng: random.Random) -> List[Tuple[int, int]]:
    return [(rng.randint(0, width - 1), rng.randint(0, height - 1))
            for _ in range(n)]


def _place_seeds_grid(width: int, height: int, n: int,
                      rng: random.Random) -> List[Tuple[int, int]]:
    cols = max(1, int(math.sqrt(n * width / max(height, 1))))
    rows = max(1, n // cols)
    dx = width / max(cols, 1)
    dy = height / max(rows, 1)
    seeds = []
    for r in range(rows):
        for c in range(cols):
            jx = rng.uniform(-dx * 0.2, dx * 0.2)
            jy = rng.uniform(-dy * 0.2, dy * 0.2)
            x = int(dx * (c + 0.5) + jx)
            y = int(dy * (r + 0.5) + jy)
            seeds.append((max(0, min(width - 1, x)),
                          max(0, min(height - 1, y))))
            if len(seeds) >= n:
                return seeds
    return seeds


# ── Voronoi assignment ──

def _assign_voronoi_numpy(width: int, height: int,
                          seeds: List[Tuple[int, int]]) -> 'np.ndarray':
    """Return (height, width) array of region indices using NumPy/SciPy."""
    seed_arr = np.array(seeds)
    if _HAS_SCIPY:
        tree = KDTree(seed_arr)
        ys, xs = np.mgrid[0:height, 0:width]
        coords = np.column_stack([xs.ravel(), ys.ravel()])
        _, indices = tree.query(coords)
        return indices.reshape(height, width)
    else:
        grid = np.empty((height, width), dtype=int)
        for y in range(height):
            for x in range(width):
                dists = np.sum((seed_arr - [x, y]) ** 2, axis=1)
                grid[y, x] = int(np.argmin(dists))
        return grid


def _assign_voronoi_pure(width: int, height: int,
                         seeds: List[Tuple[int, int]]) -> List[List[int]]:
    """Return 2D list of region indices, pure Python."""
    grid = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            best_d = float("inf")
            best_i = 0
            for i, (sx, sy) in enumerate(seeds):
                d = (x - sx) ** 2 + (y - sy) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            grid[y][x] = best_i
    return grid


def _assign_voronoi(width: int, height: int,
                    seeds: List[Tuple[int, int]]):
    """Assign pixels to nearest seed. Returns grid[y][x] = region index."""
    if _HAS_NUMPY:
        return _assign_voronoi_numpy(width, height, seeds)
    return _assign_voronoi_pure(width, height, seeds)


# ── Border detection ──

def _find_borders(grid, width: int, height: int) -> set:
    """Return set of (x, y) pixels that are on a region boundary."""
    borders = set()
    for y in range(height):
        for x in range(width):
            val = grid[y][x] if isinstance(grid, list) else int(grid[y, x])
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    nval = grid[ny][nx] if isinstance(grid, list) else int(grid[ny, nx])
                    if nval != val:
                        borders.add((x, y))
                        break
    return borders


# ── Region bounding boxes ──

def _region_bboxes(grid, width: int, height: int,
                   n_regions: int) -> Dict[int, Tuple[int, int, int, int]]:
    """Return {region_id: (min_x, min_y, max_x, max_y)}."""
    bboxes: Dict[int, list] = {}
    for y in range(height):
        for x in range(width):
            r = grid[y][x] if isinstance(grid, list) else int(grid[y, x])
            if r not in bboxes:
                bboxes[r] = [x, y, x, y]
            else:
                bb = bboxes[r]
                if x < bb[0]: bb[0] = x
                if y < bb[1]: bb[1] = y
                if x > bb[2]: bb[2] = x
                if y > bb[3]: bb[3] = y
    return {k: tuple(v) for k, v in bboxes.items()}


# ── Main puzzle generation ──

def generate_jigsaw(
    image_path: str,
    n_pieces: int = 20,
    output_dir: str = "puzzle_pieces",
    overlay_path: Optional[str] = None,
    placement: str = "random",
    border_width: int = 2,
    border_color: Tuple[int, int, int] = (0, 0, 0),
    shuffle: bool = False,
    seed: Optional[int] = None,
) -> Dict[str, object]:
    """Generate a Voronoi jigsaw puzzle from an image.

    Parameters
    ----------
    image_path : str
        Path to the input PNG image.
    n_pieces : int
        Number of puzzle pieces to generate.
    output_dir : str
        Directory to write individual piece PNGs.
    overlay_path : str, optional
        If set, write a full-image overlay with piece outlines.
    placement : str
        Seed placement strategy: ``"random"`` or ``"grid"``.
    border_width : int
        Width of the border drawn on the overlay (pixels).
    border_color : tuple
        RGB color for piece borders.
    shuffle : bool
        If True, randomize piece filenames so order is not spatial.
    seed : int, optional
        Random seed for reproducibility.

    Returns
    -------
    dict
        Summary with keys: ``pieces`` (count), ``output_dir``, ``overlay``.
    """
    rng = random.Random(seed)
    width, height, pixels = _read_png(image_path)

    # Place seeds
    if placement == "grid":
        seeds = _place_seeds_grid(width, height, n_pieces, rng)
    else:
        seeds = _place_seeds_random(width, height, n_pieces, rng)

    n_pieces = len(seeds)

    # Assign Voronoi regions
    grid = _assign_voronoi(width, height, seeds)

    # Compute bounding boxes
    bboxes = _region_bboxes(grid, width, height, n_pieces)

    # Find borders for overlay
    borders = _find_borders(grid, width, height)

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Determine piece order (shuffle if requested)
    piece_ids = list(bboxes.keys())
    if shuffle:
        rng.shuffle(piece_ids)

    # Extract each piece as an RGBA PNG
    piece_files = []
    for file_idx, region_id in enumerate(piece_ids):
        min_x, min_y, max_x, max_y = bboxes[region_id]
        pw = max_x - min_x + 1
        ph = max_y - min_y + 1

        piece_pixels = []
        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                r_id = grid[py][px] if isinstance(grid, list) else int(grid[py, px])
                if r_id == region_id:
                    src = pixels[py * width + px]
                    # Draw border on piece edges
                    is_border = (px, py) in borders
                    if is_border and border_width > 0:
                        piece_pixels.append((*border_color, 255))
                    else:
                        piece_pixels.append((src[0], src[1], src[2], 255))
                else:
                    piece_pixels.append((0, 0, 0, 0))

        fname = f"piece_{file_idx:03d}.png"
        fpath = os.path.join(output_dir, fname)
        _write_png_rgba(fpath, pw, ph, piece_pixels)
        piece_files.append(fname)

    # Write manifest
    manifest = {
        "image": os.path.basename(image_path),
        "pieces": n_pieces,
        "width": width,
        "height": height,
        "placement": placement,
        "seed": seed,
        "files": [],
    }
    for file_idx, region_id in enumerate(piece_ids):
        min_x, min_y, max_x, max_y = bboxes[region_id]
        manifest["files"].append({
            "file": f"piece_{file_idx:03d}.png",
            "region": region_id,
            "origin_x": min_x,
            "origin_y": min_y,
            "width": max_x - min_x + 1,
            "height": max_y - min_y + 1,
        })

    import json
    manifest_path = os.path.join(output_dir, "manifest.json")
    manifest_path = vormap.validate_output_path(manifest_path, allow_absolute=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Write overlay if requested
    overlay = None
    if overlay_path:
        overlay_pixels = []
        for y in range(height):
            for x in range(width):
                if (x, y) in borders:
                    # Thicken border
                    overlay_pixels.append(border_color)
                else:
                    overlay_pixels.append(pixels[y * width + x])
        _write_png_rgb(overlay_path, width, height, overlay_pixels)
        overlay = overlay_path

    return {
        "pieces": n_pieces,
        "output_dir": output_dir,
        "overlay": overlay,
        "manifest": manifest_path,
    }


# ── CLI ──

def main():
    parser = argparse.ArgumentParser(
        description="Voronoi Jigsaw Puzzle Generator — split images into "
                    "Voronoi-shaped puzzle pieces with transparent backgrounds."
    )
    parser.add_argument("image", help="Input PNG image path")
    parser.add_argument(
        "--pieces", "-n", type=int, default=20,
        help="Number of puzzle pieces (default: 20)")
    parser.add_argument(
        "--output-dir", "-o", default="puzzle_pieces",
        help="Directory for piece PNGs (default: puzzle_pieces/)")
    parser.add_argument(
        "--overlay", default=None,
        help="Write full-image overlay with outlines to this path")
    parser.add_argument(
        "--placement", choices=["random", "grid"], default="random",
        help="Seed placement strategy (default: random)")
    parser.add_argument(
        "--border-width", type=int, default=2,
        help="Border width in pixels (default: 2)")
    parser.add_argument(
        "--border-color", default="000000",
        help="Border hex color (default: 000000)")
    parser.add_argument(
        "--shuffle", action="store_true",
        help="Randomize piece filenames")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="Random seed for reproducibility")

    args = parser.parse_args()

    bc = args.border_color.lstrip("#")
    border_rgb = (int(bc[0:2], 16), int(bc[2:4], 16), int(bc[4:6], 16))

    result = generate_jigsaw(
        image_path=args.image,
        n_pieces=args.pieces,
        output_dir=args.output_dir,
        overlay_path=args.overlay,
        placement=args.placement,
        border_width=args.border_width,
        border_color=border_rgb,
        shuffle=args.shuffle,
        seed=args.seed,
    )

    print(f"Generated {result['pieces']} puzzle pieces in {result['output_dir']}/")
    print(f"Manifest: {result['manifest']}")
    if result["overlay"]:
        print(f"Overlay:  {result['overlay']}")


if __name__ == "__main__":
    main()
