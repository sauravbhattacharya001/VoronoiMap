"""Voronoi displacement & normal-map generator.

Produces displacement (height) maps and normal maps from Voronoi diagrams —
useful for 3D texturing, game development, terrain heightmaps, and procedural
material pipelines (Blender, Unreal, Unity, Substance, etc.).

Map types
---------
- **displacement** – greyscale height map (white = high, black = low)
- **normal**       – tangent-space normal map (RGB encodes XYZ normals)
- **both**         – outputs displacement + normal map pair side-by-side

Height modes
------------
- **distance** – height based on distance to nearest cell edge (default)
- **random**   – each cell gets a random plateau height with edge falloff
- **radial**   – height based on distance to cell centroid
- **ridge**    – inverted distance (edges become ridges, cells become valleys)

CLI usage
---------
::

    python vormap_displacement.py 512 512 disp.png
    python vormap_displacement.py 512 512 normal.png --map-type normal
    python vormap_displacement.py 1024 1024 pair.png --map-type both --mode ridge
    python vormap_displacement.py 512 512 disp.png --seeds 60 --mode random --falloff 0.3
    python vormap_displacement.py 512 512 disp.png --invert --blur 2

Programmatic usage
------------------
::

    import vormap_displacement

    result = vormap_displacement.generate(512, 512, map_type="normal", mode="distance")
    vormap_displacement.save_png(result, "normal_map.png")

    # Access raw data
    disp = vormap_displacement.generate(256, 256, map_type="displacement")
    heights = disp["displacement"]  # 2D list of floats [0..1]

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
# Minimal PNG writer (no external deps)
# ---------------------------------------------------------------------------

def _write_png(path: str, pixels: bytes, width: int, height: int, *, channels: int = 3) -> None:
    """Write an RGB/RGBA pixel buffer to a PNG file."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = zlib.crc32(c) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + c + struct.pack(">I", crc)

    color_type = 2 if channels == 3 else 6
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    raw = b""
    stride = width * channels
    for y in range(height):
        raw += b"\x00" + pixels[y * stride:(y + 1) * stride]
    compressed = zlib.compress(raw, 9)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(_chunk(b"IHDR", header))
        f.write(_chunk(b"IDAT", compressed))
        f.write(_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Core Voronoi helpers
# ---------------------------------------------------------------------------

def _generate_seeds(width: int, height: int, num_seeds: int, rng: random.Random) -> List[Tuple[float, float]]:
    """Generate random seed points."""
    return [(rng.uniform(0, width), rng.uniform(0, height)) for _ in range(num_seeds)]


def _find_nearest_two(x: float, y: float, seeds: List[Tuple[float, float]]) -> Tuple[int, float, float]:
    """Return (nearest_idx, dist_to_nearest, dist_to_second_nearest)."""
    best_d = float("inf")
    second_d = float("inf")
    best_idx = 0
    for i, (sx, sy) in enumerate(seeds):
        d = math.hypot(x - sx, y - sy)
        if d < best_d:
            second_d = best_d
            best_d = d
            best_idx = i
        elif d < second_d:
            second_d = d
    return best_idx, best_d, second_d


# ---------------------------------------------------------------------------
# Height computation
# ---------------------------------------------------------------------------

def _compute_heights(width: int, height: int, seeds: List[Tuple[float, float]],
                     mode: str, rng: random.Random, falloff: float) -> List[List[float]]:
    """Compute a 2D height field [0..1] for the given mode."""

    num_seeds = len(seeds)
    # Pre-compute per-cell random heights for 'random' mode
    cell_heights = [rng.random() for _ in range(num_seeds)]

    heights = [[0.0] * width for _ in range(height)]

    # First pass — raw values
    max_val = 0.0
    for py in range(height):
        for px in range(width):
            idx, d1, d2 = _find_nearest_two(px + 0.5, py + 0.5, seeds)
            edge_dist = (d2 - d1)  # distance to nearest edge

            if mode == "distance":
                val = edge_dist
            elif mode == "ridge":
                # Invert — edges are high, cell centres are low
                val = 1.0 / (edge_dist + 1.0)
            elif mode == "radial":
                # Distance from cell centroid, normalised so centre=1, far=0
                val = d1
            elif mode == "random":
                # Cell plateau with edge falloff
                plateau = cell_heights[idx]
                # Smooth falloff near edges
                edge_factor = min(edge_dist * falloff, 1.0)
                val = plateau * edge_factor
            else:
                val = edge_dist

            heights[py][px] = val
            if val > max_val:
                max_val = val

    # Normalise to [0, 1]
    if max_val > 0:
        inv = 1.0 / max_val
        for py in range(height):
            for px in range(width):
                heights[py][px] *= inv

    # For radial mode, invert so centre is high
    if mode == "radial":
        for py in range(height):
            for px in range(width):
                heights[py][px] = 1.0 - heights[py][px]

    return heights


def _box_blur(heights: List[List[float]], width: int, height: int, radius: int) -> List[List[float]]:
    """Simple box blur for smoothing."""
    if radius < 1:
        return heights
    out = [[0.0] * width for _ in range(height)]
    for py in range(height):
        for px in range(width):
            total = 0.0
            count = 0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    ny, nx = py + dy, px + dx
                    if 0 <= ny < height and 0 <= nx < width:
                        total += heights[ny][nx]
                        count += 1
            out[py][px] = total / count
    return out


# ---------------------------------------------------------------------------
# Normal map from height field
# ---------------------------------------------------------------------------

def _heights_to_normals(heights: List[List[float]], width: int, height: int,
                        strength: float) -> List[List[Tuple[int, int, int]]]:
    """Convert height field to tangent-space normal map (RGB)."""
    normals = [[(128, 128, 255)] * width for _ in range(height)]
    for py in range(height):
        for px in range(width):
            # Sobel-like sampling
            left = heights[py][max(px - 1, 0)]
            right = heights[py][min(px + 1, width - 1)]
            up = heights[max(py - 1, 0)][px]
            down = heights[min(py + 1, height - 1)][px]

            dx = (left - right) * strength
            dy = (up - down) * strength
            dz = 1.0

            # Normalise
            length = math.sqrt(dx * dx + dy * dy + dz * dz)
            if length > 0:
                dx /= length
                dy /= length
                dz /= length

            # Map [-1,1] -> [0,255]
            r = int((dx * 0.5 + 0.5) * 255)
            g = int((dy * 0.5 + 0.5) * 255)
            b = int((dz * 0.5 + 0.5) * 255)
            normals[py][px] = (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))

    return normals


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class DisplacementResult:
    """Result of displacement/normal map generation."""
    width: int
    height: int
    map_type: str
    mode: str
    num_seeds: int
    displacement: Optional[List[List[float]]] = None  # [0..1] height values
    normals: Optional[List[List[Tuple[int, int, int]]]] = None  # RGB tuples
    seeds: List[Tuple[float, float]] = field(default_factory=list)


def generate(width: int, height: int, *, num_seeds: int = 40,
             map_type: str = "displacement", mode: str = "distance",
             seed_value: Optional[int] = None, strength: float = 5.0,
             invert: bool = False, blur: int = 0,
             falloff: float = 0.15) -> DisplacementResult:
    """Generate Voronoi-based displacement and/or normal maps.

    Parameters
    ----------
    width, height : int
        Output image dimensions.
    num_seeds : int
        Number of Voronoi cells.
    map_type : str
        ``"displacement"``, ``"normal"``, or ``"both"``.
    mode : str
        ``"distance"``, ``"random"``, ``"radial"``, or ``"ridge"``.
    seed_value : int, optional
        Random seed for reproducibility.
    strength : float
        Normal map bump strength (higher = more pronounced).
    invert : bool
        Invert the height map.
    blur : int
        Box-blur radius for smoothing (0 = no blur).
    falloff : float
        Edge falloff factor for ``"random"`` mode.

    Returns
    -------
    DisplacementResult
        Contains displacement heights, normal map data, or both.
    """
    rng = random.Random(seed_value)
    seeds = _generate_seeds(width, height, num_seeds, rng)

    result = DisplacementResult(
        width=width, height=height, map_type=map_type,
        mode=mode, num_seeds=num_seeds, seeds=seeds,
    )

    if map_type in ("displacement", "both"):
        h = _compute_heights(width, height, seeds, mode, rng, falloff)
        if blur > 0:
            h = _box_blur(h, width, height, blur)
        if invert:
            for py in range(height):
                for px in range(width):
                    h[py][px] = 1.0 - h[py][px]
        result.displacement = h

    if map_type in ("normal", "both"):
        # Need heights for normal calculation
        if result.displacement is None:
            h = _compute_heights(width, height, seeds, mode, rng, falloff)
            if blur > 0:
                h = _box_blur(h, width, height, blur)
            if invert:
                for py in range(height):
                    for px in range(width):
                        h[py][px] = 1.0 - h[py][px]
        else:
            h = result.displacement
        result.normals = _heights_to_normals(h, width, height, strength)

    return result


def save_png(result: DisplacementResult, path: str) -> None:
    """Save a DisplacementResult to PNG file(s).

    For ``map_type="both"`` the displacement and normal maps are placed
    side-by-side in a single image.
    """
    w, h = result.width, result.height

    if result.map_type == "displacement" and result.displacement is not None:
        pixels = bytearray(w * h * 3)
        for py in range(h):
            for px in range(w):
                v = int(result.displacement[py][px] * 255)
                v = max(0, min(255, v))
                offset = (py * w + px) * 3
                pixels[offset] = v
                pixels[offset + 1] = v
                pixels[offset + 2] = v
        _write_png(path, bytes(pixels), w, h)

    elif result.map_type == "normal" and result.normals is not None:
        pixels = bytearray(w * h * 3)
        for py in range(h):
            for px in range(w):
                r, g, b = result.normals[py][px]
                offset = (py * w + px) * 3
                pixels[offset] = r
                pixels[offset + 1] = g
                pixels[offset + 2] = b
        _write_png(path, bytes(pixels), w, h)

    elif result.map_type == "both":
        # Side-by-side: displacement | normal
        total_w = w * 2
        pixels = bytearray(total_w * h * 3)
        for py in range(h):
            for px in range(w):
                # Displacement on left
                if result.displacement is not None:
                    v = int(result.displacement[py][px] * 255)
                    v = max(0, min(255, v))
                    offset = (py * total_w + px) * 3
                    pixels[offset] = v
                    pixels[offset + 1] = v
                    pixels[offset + 2] = v
                # Normal on right
                if result.normals is not None:
                    r, g, b = result.normals[py][px]
                    offset = (py * total_w + w + px) * 3
                    pixels[offset] = r
                    pixels[offset + 1] = g
                    pixels[offset + 2] = b
        _write_png(path, bytes(pixels), total_w, h)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Voronoi displacement & normal maps for 3D texturing.",
        epilog="Examples:\n"
               "  python vormap_displacement.py 512 512 height.png\n"
               "  python vormap_displacement.py 512 512 normals.png --map-type normal\n"
               "  python vormap_displacement.py 1024 1024 pair.png --map-type both --mode ridge\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("width", type=int, help="Image width in pixels")
    parser.add_argument("height", type=int, help="Image height in pixels")
    parser.add_argument("output", help="Output PNG file path")
    parser.add_argument("--seeds", type=int, default=40, help="Number of Voronoi cells (default: 40)")
    parser.add_argument("--map-type", choices=["displacement", "normal", "both"],
                        default="displacement", help="Map type to generate (default: displacement)")
    parser.add_argument("--mode", choices=["distance", "random", "radial", "ridge"],
                        default="distance", help="Height computation mode (default: distance)")
    parser.add_argument("--seed-value", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument("--strength", type=float, default=5.0,
                        help="Normal map bump strength (default: 5.0)")
    parser.add_argument("--invert", action="store_true", help="Invert the height map")
    parser.add_argument("--blur", type=int, default=0, help="Box-blur radius for smoothing (default: 0)")
    parser.add_argument("--falloff", type=float, default=0.15,
                        help="Edge falloff for 'random' mode (default: 0.15)")

    args = parser.parse_args()

    result = generate(
        args.width, args.height,
        num_seeds=args.seeds,
        map_type=args.map_type,
        mode=args.mode,
        seed_value=args.seed_value,
        strength=args.strength,
        invert=args.invert,
        blur=args.blur,
        falloff=args.falloff,
    )
    save_png(result, args.output)

    print(f"Generated {args.map_type} map ({args.mode} mode): {args.output}")
    print(f"  Size: {args.width}x{args.height}, Seeds: {args.seeds}")
    if args.map_type in ("normal", "both"):
        print(f"  Normal strength: {args.strength}")
    if args.invert:
        print("  Height map inverted")
    if args.blur > 0:
        print(f"  Blur radius: {args.blur}")


if __name__ == "__main__":
    main()
