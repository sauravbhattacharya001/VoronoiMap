"""Tests for vormap_animate -- Voronoi animation module."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from vormap_animate import (
    AnimationConfig,
    animate,
    animate_from_files,
    _compute_bounds,
    _interpolate_snapshots,
    _match_points,
    _lerp_point,
    _build_frame_data,
    main,
)


class TestAnimationConfig(unittest.TestCase):
    """AnimationConfig defaults and overrides."""

    def test_defaults(self):
        cfg = AnimationConfig()
        self.assertEqual(cfg.width, 900)
        self.assertEqual(cfg.height, 700)
        self.assertEqual(cfg.fps, 2)
        self.assertEqual(cfg.color_scheme, "pastel")
        self.assertTrue(cfg.show_points)
        self.assertFalse(cfg.show_labels)
        self.assertFalse(cfg.trails)
        self.assertEqual(cfg.interpolate, 0)
        self.assertTrue(cfg.loop)

    def test_overrides(self):
        cfg = AnimationConfig(width=1200, fps=10, trails=True, interpolate=3)
        self.assertEqual(cfg.width, 1200)
        self.assertEqual(cfg.fps, 10)
        self.assertTrue(cfg.trails)
        self.assertEqual(cfg.interpolate, 3)

    def test_fps_clamp(self):
        cfg = AnimationConfig(fps=-5)
        self.assertEqual(cfg.fps, 1)

    def test_interpolate_clamp(self):
        cfg = AnimationConfig(interpolate=-3)
        self.assertEqual(cfg.interpolate, 0)


class TestHelpers(unittest.TestCase):
    """Low-level helper functions."""

    def test_compute_bounds_basic(self):
        snaps = [[(0, 0), (100, 200)], [(50, 50)]]
        min_x, min_y, max_x, max_y = _compute_bounds(snaps)
        self.assertLess(min_x, 0)
        self.assertGreater(max_x, 100)
        self.assertLess(min_y, 0)
        self.assertGreater(max_y, 200)

    def test_compute_bounds_empty(self):
        result = _compute_bounds([[]])
        self.assertEqual(result, (0, 0, 100, 100))

    def test_lerp_point(self):
        p = _lerp_point((0, 0), (10, 20), 0.5)
        self.assertAlmostEqual(p[0], 5.0)
        self.assertAlmostEqual(p[1], 10.0)

    def test_lerp_endpoints(self):
        p1, p2 = (3, 7), (13, 27)
        self.assertEqual(_lerp_point(p1, p2, 0.0), p1)
        self.assertEqual(_lerp_point(p1, p2, 1.0), p2)

    def test_match_points_identical(self):
        pts = [(10, 20), (30, 40)]
        matches = _match_points(pts, pts)
        matched_curr = {ci for _, ci in matches}
        self.assertEqual(matched_curr, {0, 1})

    def test_match_points_birth(self):
        prev = [(10, 20)]
        curr = [(10, 20), (500, 500)]
        matches = _match_points(prev, curr)
        births = [ci for pi, ci in matches if pi is None]
        self.assertEqual(len(births), 1)

    def test_match_points_empty_prev(self):
        matches = _match_points([], [(10, 20)])
        self.assertEqual(len(matches), 1)
        self.assertIsNone(matches[0][0])


class TestInterpolation(unittest.TestCase):
    """Snapshot interpolation."""

    def test_no_interpolation(self):
        snaps = [[(0, 0)], [(10, 10)]]
        result = _interpolate_snapshots(snaps, 0)
        self.assertEqual(len(result), 2)

    def test_single_snapshot(self):
        snaps = [[(0, 0)]]
        result = _interpolate_snapshots(snaps, 5)
        self.assertEqual(len(result), 1)

    def test_interpolation_count(self):
        snaps = [[(0, 0), (100, 0)], [(0, 100), (100, 100)]]
        result = _interpolate_snapshots(snaps, 3)
        # 2 originals + 3 interpolated = 5
        self.assertEqual(len(result), 5)

    def test_interpolated_positions(self):
        # Use two close points so auto-radius matches them
        snaps = [[(50, 50), (60, 50)], [(51, 50), (61, 50)]]
        result = _interpolate_snapshots(snaps, 1)
        self.assertEqual(len(result), 3)
        mid = result[1]
        self.assertEqual(len(mid), 2)
        # Midpoint of (50,50)->(51,50) should be ~50.5
        self.assertAlmostEqual(mid[0][0], 50.5, places=1)


class TestBuildFrameData(unittest.TestCase):
    """Frame data building."""

    def test_basic_frames(self):
        snaps = [
            [(100, 200), (300, 400), (500, 100)],
            [(110, 210), (300, 400), (500, 100)],
        ]
        cfg = AnimationConfig()
        frames = _build_frame_data(snaps, cfg)
        self.assertEqual(len(frames), 2)
        for f in frames:
            self.assertIn("points", f)
            self.assertIn("regions", f)
            self.assertIn("colors", f)
            self.assertGreater(len(f["points"]), 0)

    def test_frame_with_interpolation(self):
        snaps = [
            [(100, 200), (300, 400)],
            [(150, 250), (350, 450)],
        ]
        cfg = AnimationConfig(interpolate=2)
        frames = _build_frame_data(snaps, cfg)
        self.assertEqual(len(frames), 4)  # 2 original + 2 interp


class TestAnimate(unittest.TestCase):
    """Main animate function."""

    def test_basic_animation(self):
        snaps = [
            [(100, 200), (300, 400), (500, 100)],
            [(110, 210), (300, 400), (500, 100), (700, 300)],
            [(120, 220), (500, 100), (700, 300)],
        ]
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            out = f.name
        try:
            result = animate(snaps, out)
            self.assertEqual(result["snapshots"], 3)
            self.assertEqual(result["frames"], 3)
            self.assertTrue(os.path.exists(out))
            content = open(out, encoding='utf-8').read()
            self.assertIn("canvas", content)
            self.assertIn("FRAMES", content)
        finally:
            os.unlink(out)

    def test_with_interpolation(self):
        snaps = [
            [(0, 0), (100, 0), (50, 100)],
            [(10, 10), (90, 10), (50, 90)],
        ]
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            out = f.name
        try:
            cfg = AnimationConfig(interpolate=4)
            result = animate(snaps, out, config=cfg)
            self.assertEqual(result["snapshots"], 2)
            self.assertEqual(result["frames"], 6)
            self.assertEqual(result["interpolated_frames"], 4)
        finally:
            os.unlink(out)

    def test_with_trails(self):
        snaps = [[(0, 0), (50, 50)], [(10, 10), (60, 60)]]
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            out = f.name
        try:
            cfg = AnimationConfig(trails=True)
            result = animate(snaps, out, config=cfg)
            content = open(out, encoding='utf-8').read()
            self.assertIn("trails", content.lower())
        finally:
            os.unlink(out)

    def test_too_few_snapshots(self):
        with self.assertRaises(ValueError):
            animate([[(0, 0)]], "nope.html")

    def test_custom_config(self):
        snaps = [[(0, 0), (100, 100)], [(10, 10), (90, 90)]]
        with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
            out = f.name
        try:
            cfg = AnimationConfig(
                width=1200, height=800, fps=8,
                color_scheme="warm", title="Test Anim",
                show_labels=True, loop=False,
            )
            result = animate(snaps, out, config=cfg)
            content = open(out, encoding='utf-8').read()
            self.assertIn("1200", content)
            self.assertIn("Test Anim", content)
            self.assertIn("showLabels", content)
        finally:
            os.unlink(out)

    def test_all_color_schemes(self):
        snaps = [[(0, 0), (100, 100), (50, 50)], [(10, 10), (90, 90), (50, 40)]]
        for scheme in ["pastel", "warm", "cool", "earth", "mono", "rainbow"]:
            with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as f:
                out = f.name
            try:
                cfg = AnimationConfig(color_scheme=scheme)
                result = animate(snaps, out, config=cfg)
                self.assertTrue(os.path.exists(out))
            finally:
                os.unlink(out)


class TestAnimateFromFiles(unittest.TestCase):
    """File-based animation."""

    def test_from_files(self):
        snaps_data = [
            "100 200\n300 400\n500 100\n",
            "110 210\n300 400\n500 100\n",
        ]
        files = []
        for data in snaps_data:
            f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            f.write(data)
            f.close()
            files.append(f.name)
        out = tempfile.NamedTemporaryFile(suffix='.html', delete=False).name
        try:
            result = animate_from_files(files, out)
            self.assertEqual(result["snapshots"], 2)
            self.assertTrue(os.path.exists(out))
        finally:
            for fp in files:
                os.unlink(fp)
            os.unlink(out)


class TestCLI(unittest.TestCase):
    """CLI entry point."""

    def test_cli_basic(self):
        snaps_data = [
            "100 200\n300 400\n500 100\n",
            "110 210\n300 400\n500 100\n",
        ]
        files = []
        for data in snaps_data:
            f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
            f.write(data)
            f.close()
            files.append(f.name)
        out = tempfile.NamedTemporaryFile(suffix='.html', delete=False).name
        try:
            main(files + ['-o', out, '--fps', '4'])
            self.assertTrue(os.path.exists(out))
        finally:
            for fp in files:
                os.unlink(fp)
            os.unlink(out)


if __name__ == "__main__":
    unittest.main()
