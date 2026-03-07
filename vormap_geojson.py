"""GeoJSON export for VoronoiMap — generates RFC 7946 GeoJSON files.

Exports Voronoi regions as a GeoJSON FeatureCollection of Polygons and
optionally seed points as Point features, viewable in Leaflet, Mapbox,
QGIS, GitHub, geojson.io, and any GeoJSON-consuming tool.

Usage
-----
CLI::

    python vormap_geojson.py data/sample.txt -o voronoi.geojson
    python vormap_geojson.py data/sample.txt --no-seeds --precision 2

Library::

    from vormap_geojson import generate_geojson, export_geojson
    path = generate_geojson('sample.txt', 'out.geojson')
"""

import json
import argparse
import sys

import vormap
from vormap_geometry import (
    build_data_index as _build_data_index,
    polygon_area as _compute_region_area,
    polygon_centroid as _compute_centroid,
    polygon_perimeter as _compute_perimeter,
)


def _round_coord(value, precision):
    """Round a coordinate value to the given decimal places."""
    if precision is None:
        return value
    return round(value, precision)


def export_geojson(
    regions,
    data,
    output_path,
    *,
    include_seeds=True,
    include_stats=True,
    precision=6,
    region_names_fn=None,
    indent=2,
):
    """Export Voronoi regions as a GeoJSON FeatureCollection.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` — maps seed → vertex list.
    data : list of (float, float)
        All seed points.
    output_path : str
        Path to write the GeoJSON file.
    include_seeds : bool
        If True (default), include seed points as Point features.
    include_stats : bool
        If True (default), include area/perimeter/centroid in properties.
    precision : int or None
        Decimal places for coordinates. None for full precision.
    region_names_fn : callable or None
        Optional ``(seed, vertices, index) → str`` for custom names.
    indent : int or None
        JSON indentation. None for compact output.

    Returns
    -------
    str
        Path to the generated GeoJSON file.
    """
    if not regions:
        raise ValueError("No regions to export")

    sorted_seeds = sorted(regions.keys())
    data_lookup = _build_data_index(data)
    features = []

    # Region polygons
    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        data_idx = data_lookup.get(tuple(seed), idx)

        if region_names_fn:
            name = region_names_fn(seed, verts, data_idx)
        else:
            name = 'Region %d' % (data_idx + 1)

        # GeoJSON polygon ring: list of [lng, lat] — must close
        ring = []
        for v in verts:
            ring.append([
                _round_coord(v[0], precision),
                _round_coord(v[1], precision),
            ])
        if verts:
            ring.append([
                _round_coord(verts[0][0], precision),
                _round_coord(verts[0][1], precision),
            ])

        properties = {
            'name': name,
            'type': 'region',
            'seed_x': _round_coord(seed[0], precision),
            'seed_y': _round_coord(seed[1], precision),
            'vertex_count': len(verts),
        }

        if include_stats:
            area = _compute_region_area(verts)
            perimeter = _compute_perimeter(verts)
            centroid = _compute_centroid(verts)
            properties['area'] = _round_coord(area, precision)
            properties['perimeter'] = _round_coord(perimeter, precision)
            if centroid:
                properties['centroid_x'] = _round_coord(centroid[0], precision)
                properties['centroid_y'] = _round_coord(centroid[1], precision)

        feature = {
            'type': 'Feature',
            'geometry': {
                'type': 'Polygon',
                'coordinates': [ring],
            },
            'properties': properties,
        }
        features.append(feature)

    # Seed points
    if include_seeds:
        for idx, (px, py) in enumerate(data):
            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [
                        _round_coord(px, precision),
                        _round_coord(py, precision),
                    ],
                },
                'properties': {
                    'name': 'Seed %d' % (idx + 1),
                    'type': 'seed',
                    'x': _round_coord(px, precision),
                    'y': _round_coord(py, precision),
                },
            }
            features.append(feature)

    collection = {
        'type': 'FeatureCollection',
        'features': features,
        'properties': {
            'generator': 'VoronoiMap',
            'region_count': len(sorted_seeds),
            'seed_count': len(data),
        },
    }

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(collection, f, indent=indent, ensure_ascii=False)

    return output_path


def generate_geojson(
    datafile,
    output_path='voronoi.geojson',
    *,
    include_seeds=True,
    include_stats=True,
    precision=6,
    indent=2,
):
    """Load data, compute regions, and export GeoJSON in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str
        Where to write the GeoJSON file.
    include_seeds : bool
        Include seed points as Point features.
    include_stats : bool
        Include geometric statistics in region properties.
    precision : int or None
        Decimal places for coordinates.
    indent : int or None
        JSON indentation (None for compact).

    Returns
    -------
    str
        Path to the generated GeoJSON file.
    """
    import vormap_viz

    data = vormap.load_data(datafile)
    regions = vormap_viz.compute_regions(data)
    return export_geojson(
        regions,
        data,
        output_path,
        include_seeds=include_seeds,
        include_stats=include_stats,
        precision=precision,
        indent=indent,
    )


def main():
    """CLI entry point for GeoJSON export."""
    parser = argparse.ArgumentParser(
        description='Export VoronoiMap diagrams as GeoJSON files',
    )
    parser.add_argument('datafile', help='Input data file (in data/ directory)')
    parser.add_argument(
        '-o', '--output',
        default='voronoi.geojson',
        help='Output GeoJSON file path (default: voronoi.geojson)',
    )
    parser.add_argument(
        '--no-seeds',
        action='store_true',
        help='Exclude seed points from output',
    )
    parser.add_argument(
        '--no-stats',
        action='store_true',
        help='Exclude geometric statistics from region properties',
    )
    parser.add_argument(
        '--precision',
        type=int,
        default=6,
        help='Decimal places for coordinates (default: 6)',
    )
    parser.add_argument(
        '--compact',
        action='store_true',
        help='Compact JSON output (no indentation)',
    )

    args = parser.parse_args()

    try:
        path = generate_geojson(
            args.datafile,
            args.output,
            include_seeds=not args.no_seeds,
            include_stats=not args.no_stats,
            precision=args.precision,
            indent=None if args.compact else 2,
        )
        print('GeoJSON exported to: %s' % path)
    except Exception as e:
        print('Error: %s' % e, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
