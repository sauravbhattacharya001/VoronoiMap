"""Tests for vormap_noise — Worley cellular noise generator."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_noise


def test_generate_default():
    img = vormap_noise.generate(width=32, height=32, num_seeds=10, seed=42)
    assert len(img) == 32
    assert len(img[0]) == 32
    assert all(0 <= c <= 255 for c in img[0][0])


def test_all_modes():
    for mode in vormap_noise.MODES:
        img = vormap_noise.generate(width=16, height=16, num_seeds=5,
                                    mode=mode, seed=1)
        assert len(img) == 16


def test_all_colormaps():
    for cm in vormap_noise.COLORMAPS:
        img = vormap_noise.generate(width=16, height=16, num_seeds=5,
                                    colormap=cm, seed=1)
        assert len(img) == 16


def test_invert():
    img_normal = vormap_noise.generate(width=16, height=16, num_seeds=5, seed=7)
    img_inv = vormap_noise.generate(width=16, height=16, num_seeds=5, seed=7,
                                    invert=True)
    # At least some pixels should differ
    diffs = sum(1 for y in range(16) for x in range(16)
                if img_normal[y][x] != img_inv[y][x])
    assert diffs > 0


def test_tiled():
    img = vormap_noise.generate(width=32, height=32, num_seeds=10, seed=3,
                                tiled=True)
    assert len(img) == 32


def test_octaves():
    img = vormap_noise.generate(width=16, height=16, num_seeds=5, seed=2,
                                octaves=3)
    assert len(img) == 16


def test_jitter():
    img = vormap_noise.generate(width=16, height=16, num_seeds=9, seed=5,
                                jitter=0.3)
    assert len(img) == 16


def test_save_ppm():
    img = vormap_noise.generate(width=8, height=8, num_seeds=4, seed=1)
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as f:
        path = f.name
    try:
        vormap_noise.save_ppm(img, path)
        assert os.path.exists(path)
        with open(path, "rb") as f:
            header = f.read(2)
            assert header == b"P6"
    finally:
        os.unlink(path)


def test_generate_from_points():
    points = [(100, 200), (300, 400), (500, 100), (700, 600)]
    img = vormap_noise.generate_from_points(points, width=32, height=32,
                                            mode="f2-f1", colormap="fire")
    assert len(img) == 32
    assert len(img[0]) == 32


def test_cli_basic():
    with tempfile.NamedTemporaryFile(suffix=".ppm", delete=False) as f:
        path = f.name
    try:
        vormap_noise.main(["-W", "16", "-H", "16", "-n", "5", "-s", "1",
                           "-o", path])
        assert os.path.exists(path)
        assert os.path.getsize(path) > 0
    finally:
        os.unlink(path)


if __name__ == "__main__":
    test_generate_default()
    test_all_modes()
    test_all_colormaps()
    test_invert()
    test_tiled()
    test_octaves()
    test_jitter()
    test_save_ppm()
    test_generate_from_points()
    test_cli_basic()
    print("All tests passed!")
