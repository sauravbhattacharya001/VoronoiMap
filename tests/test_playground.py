"""Tests for vormap_playground module."""
import os
import tempfile
import json

import vormap_playground


def test_generate_empty_playground():
    """Generate a playground with no seed points."""
    with tempfile.TemporaryDirectory() as tmp:
        out = vormap_playground.generate_playground(os.path.join(tmp, "test.html"))
        assert os.path.isfile(out)
        content = open(out, encoding="utf-8").read()
        assert "VoronoiMap Playground" in content
        assert "[]" in content  # empty points array


def test_generate_with_data():
    """Generate a playground with pre-loaded points."""
    data = [(0, 0), (100, 200), (50, 150)]
    with tempfile.TemporaryDirectory() as tmp:
        out = vormap_playground.generate_playground(os.path.join(tmp, "test.html"), data=data)
        content = open(out, encoding="utf-8").read()
        assert "VoronoiMap Playground" in content
        # Should contain 3 point objects
        assert '"x"' in content
        assert '"y"' in content


def test_html_is_standalone():
    """Ensure no external script/CSS dependencies."""
    with tempfile.TemporaryDirectory() as tmp:
        out = vormap_playground.generate_playground(os.path.join(tmp, "test.html"))
        content = open(out, encoding="utf-8").read()
        # No external links
        assert 'src="http' not in content
        assert 'href="http' not in content
