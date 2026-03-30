"""Tests for the --summary and --summary-json CLI features."""
import json
import os
import subprocess
import sys
import tempfile


def _write_test_data(tmpdir, points):
    """Write points to a data/test.txt file inside tmpdir."""
    data_dir = os.path.join(tmpdir, 'data')
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, 'test.txt')
    with open(path, 'w') as f:
        for x, y in points:
            f.write('%f %f\n' % (x, y))
    return 'test.txt'


def test_summary_text():
    """--summary prints human-readable output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        fname = _write_test_data(tmpdir, [
            (0, 0), (10, 0), (10, 10), (0, 10), (5, 5),
        ])
        result = subprocess.run(
            [sys.executable, 'vormap.py', fname, '--summary'],
            capture_output=True, text=True, cwd=tmpdir,
        )
        # Copy vormap.py to tmpdir for isolation
        pass

    # Simpler: run from repo root
    points = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(repo, 'data')
    os.makedirs(data_dir, exist_ok=True)
    test_file = os.path.join(data_dir, '_test_summary.txt')
    try:
        with open(test_file, 'w') as f:
            for x, y in points:
                f.write('%f %f\n' % (x, y))
        result = subprocess.run(
            [sys.executable, 'vormap.py', '_test_summary.txt', '--summary'],
            capture_output=True, text=True, cwd=repo,
        )
        assert result.returncode == 0, result.stderr
        assert 'Dataset Summary' in result.stdout
        assert 'Points:' in result.stdout
        assert '5' in result.stdout
    finally:
        os.remove(test_file)


def test_summary_json():
    """--summary-json prints valid JSON with expected keys."""
    points = [(0, 0), (10, 0), (10, 10), (0, 10), (5, 5)]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(repo, 'data')
    os.makedirs(data_dir, exist_ok=True)
    test_file = os.path.join(data_dir, '_test_summary.txt')
    try:
        with open(test_file, 'w') as f:
            for x, y in points:
                f.write('%f %f\n' % (x, y))
        result = subprocess.run(
            [sys.executable, 'vormap.py', '_test_summary.txt', '--summary-json'],
            capture_output=True, text=True, cwd=repo,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data['point_count'] == 5
        assert 'bounding_box' in data
        assert 'nearest_neighbor' in data
        assert data['bounding_box']['width'] == 10.0
    finally:
        os.remove(test_file)
