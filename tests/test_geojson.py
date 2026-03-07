"""Tests for vormap_geojson — GeoJSON export module."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_geojson


def _make_regions():
    """Create simple test regions."""
    seed1 = (1.0, 2.0)
    seed2 = (5.0, 6.0)
    regions = {
        seed1: [(0, 0), (2, 0), (2, 4), (0, 4)],
        seed2: [(3, 3), (7, 3), (7, 9), (3, 9)],
    }
    data = [seed1, seed2]
    return regions, data


def test_export_basic():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        result = vormap_geojson.export_geojson(regions, data, path)
        assert result == path
        with open(path) as f:
            gj = json.load(f)
        assert gj['type'] == 'FeatureCollection'
        # 2 regions + 2 seeds = 4 features
        assert len(gj['features']) == 4
        # Check region features
        region_features = [f for f in gj['features'] if f['properties']['type'] == 'region']
        assert len(region_features) == 2
        for feat in region_features:
            assert feat['geometry']['type'] == 'Polygon'
            ring = feat['geometry']['coordinates'][0]
            # Ring must be closed
            assert ring[0] == ring[-1]
            assert 'area' in feat['properties']
            assert 'perimeter' in feat['properties']
        # Check seed features
        seed_features = [f for f in gj['features'] if f['properties']['type'] == 'seed']
        assert len(seed_features) == 2
        for feat in seed_features:
            assert feat['geometry']['type'] == 'Point'
    finally:
        os.unlink(path)


def test_export_no_seeds():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        vormap_geojson.export_geojson(regions, data, path, include_seeds=False)
        with open(path) as f:
            gj = json.load(f)
        assert len(gj['features']) == 2
        assert all(f['properties']['type'] == 'region' for f in gj['features'])
    finally:
        os.unlink(path)


def test_export_no_stats():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        vormap_geojson.export_geojson(regions, data, path, include_stats=False)
        with open(path) as f:
            gj = json.load(f)
        region_feat = [f for f in gj['features'] if f['properties']['type'] == 'region'][0]
        assert 'area' not in region_feat['properties']
        assert 'perimeter' not in region_feat['properties']
    finally:
        os.unlink(path)


def test_export_compact():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        vormap_geojson.export_geojson(regions, data, path, indent=None)
        with open(path) as f:
            content = f.read()
        # Compact = single line (no newlines in JSON body)
        assert '\n' not in content.strip()
    finally:
        os.unlink(path)


def test_export_precision():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        vormap_geojson.export_geojson(regions, data, path, precision=2)
        with open(path) as f:
            gj = json.load(f)
        region_feat = [f for f in gj['features'] if f['properties']['type'] == 'region'][0]
        ring = region_feat['geometry']['coordinates'][0]
        for coord in ring:
            for val in coord:
                # Check decimal places
                s = str(val)
                if '.' in s:
                    assert len(s.split('.')[1]) <= 2
    finally:
        os.unlink(path)


def test_export_custom_names():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        vormap_geojson.export_geojson(
            regions, data, path,
            region_names_fn=lambda s, v, i: 'Zone-%d' % i,
        )
        with open(path) as f:
            gj = json.load(f)
        region_names = [
            f['properties']['name']
            for f in gj['features']
            if f['properties']['type'] == 'region'
        ]
        assert all(n.startswith('Zone-') for n in region_names)
    finally:
        os.unlink(path)


def test_export_empty_raises():
    try:
        vormap_geojson.export_geojson({}, [], 'out.geojson')
        assert False, 'Should have raised ValueError'
    except ValueError:
        pass


def test_collection_properties():
    regions, data = _make_regions()
    with tempfile.NamedTemporaryFile(suffix='.geojson', delete=False) as f:
        path = f.name
    try:
        vormap_geojson.export_geojson(regions, data, path)
        with open(path) as f:
            gj = json.load(f)
        assert gj['properties']['generator'] == 'VoronoiMap'
        assert gj['properties']['region_count'] == 2
        assert gj['properties']['seed_count'] == 2
    finally:
        os.unlink(path)


if __name__ == '__main__':
    test_export_basic()
    test_export_no_seeds()
    test_export_no_stats()
    test_export_compact()
    test_export_precision()
    test_export_custom_names()
    test_export_empty_raises()
    test_collection_properties()
    print('All GeoJSON tests passed!')
