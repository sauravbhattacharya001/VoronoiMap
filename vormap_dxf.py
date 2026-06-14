"""DXF (AutoCAD Drawing Exchange Format) export for VoronoiMap.

Exports Voronoi tessellations as AutoCAD R12 DXF files - the universal
vector-CAD interchange format consumed by AutoCAD, LibreCAD, FreeCAD,
Fusion 360, Inkscape, QCAD, and virtually every laser-cutter, CNC, and
pen-plotter toolchain.  This fills the one obvious gap in VoronoiMap's
export line-up (SVG, interactive HTML, GeoJSON, KML, GPX, 3D mesh) by
producing genuinely *fabrication-ready* output: drop the cells straight
onto a laser cutter or into a CAD drawing.

R12 is targeted deliberately - it is the most broadly compatible DXF
flavour and needs no handle/ownership bookkeeping, so the output opens
cleanly everywhere.  Each Voronoi cell becomes a closed ``POLYLINE`` on
the ``VORONOI_CELLS`` layer; seeds become ``POINT`` entities on
``VORONOI_SEEDS``; optional region labels become ``TEXT`` on
``VORONOI_LABELS``.  Proper ``$EXTMIN`` / ``$EXTMAX`` header variables
are emitted so drawings open zoomed-to-fit.

The writer is pure Python, dependency-free, and deterministic.

Usage
-----
CLI::

    python vormap_dxf.py data/sample.txt -o voronoi.dxf
    python vormap_dxf.py data/sample.txt --no-seeds --labels --precision 4

Library::

    from vormap_dxf import generate_dxf, export_dxf
    path = generate_dxf('sample.txt', 'out.dxf')
"""

import argparse
import sys

import vormap
from vormap_geometry import (
    build_data_index as _build_data_index,
    polygon_area as _polygon_area,
    polygon_centroid as _polygon_centroid,
    polygon_perimeter as _polygon_perimeter,
)


# DXF AutoCAD colour indices (ACI) for the standard layers.
LAYER_CELLS = 'VORONOI_CELLS'
LAYER_SEEDS = 'VORONOI_SEEDS'
LAYER_LABELS = 'VORONOI_LABELS'

_CELL_COLOR = 7    # white/black (follows background)
_SEED_COLOR = 1    # red
_LABEL_COLOR = 3   # green

# AutoCAD R12 version marker.
_ACAD_R12 = 'AC1009'


def _fmt(value, precision):
    """Format a coordinate as a plain decimal string (no exponent).

    DXF readers expect simple decimal notation; Python's ``repr`` can emit
    scientific notation for very small/large magnitudes, which some
    importers choke on.  We therefore render via fixed precision and trim
    trailing zeros so the output stays compact yet portable.
    """
    if precision is None:
        precision = 6
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if v == 0.0:
        v = 0.0  # normalise -0.0 -> 0.0
    s = ('%.*f' % (precision, v))
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
    if s in ('', '-'):
        return '0'
    # Collapse any signed-zero result (e.g. '-0', '-0.0') to a clean '0'.
    if s in ('-0', '0'):
        return '0'
    return s


def _pair(code, value):
    """Emit a single DXF group-code / value record as two lines."""
    return '%d\n%s\n' % (code, value)


def _bounds(regions, data, include_seeds):
    """Compute (minx, miny, maxx, maxy) over all geometry, or a unit box."""
    xs = []
    ys = []
    for verts in regions.values():
        for v in verts:
            xs.append(float(v[0]))
            ys.append(float(v[1]))
    if include_seeds:
        for p in data:
            xs.append(float(p[0]))
            ys.append(float(p[1]))
    if not xs or not ys:
        return (0.0, 0.0, 1.0, 1.0)
    return (min(xs), min(ys), max(xs), max(ys))


def _header(minx, miny, maxx, maxy, precision):
    """Build the DXF HEADER section with version + drawing extents."""
    out = []
    out.append(_pair(0, 'SECTION'))
    out.append(_pair(2, 'HEADER'))
    out.append(_pair(9, '$ACADVER'))
    out.append(_pair(1, _ACAD_R12))
    out.append(_pair(9, '$EXTMIN'))
    out.append(_pair(10, _fmt(minx, precision)))
    out.append(_pair(20, _fmt(miny, precision)))
    out.append(_pair(30, '0'))
    out.append(_pair(9, '$EXTMAX'))
    out.append(_pair(10, _fmt(maxx, precision)))
    out.append(_pair(20, _fmt(maxy, precision)))
    out.append(_pair(30, '0'))
    out.append(_pair(0, 'ENDSEC'))
    return ''.join(out)


def _layer_record(name, color):
    out = []
    out.append(_pair(0, 'LAYER'))
    out.append(_pair(2, name))
    out.append(_pair(70, '0'))        # flags: none
    out.append(_pair(62, str(color)))  # colour
    out.append(_pair(6, 'CONTINUOUS')) # linetype
    return ''.join(out)


def _tables(layers):
    """Build the TABLES section containing the LAYER table."""
    out = []
    out.append(_pair(0, 'SECTION'))
    out.append(_pair(2, 'TABLES'))
    out.append(_pair(0, 'TABLE'))
    out.append(_pair(2, 'LAYER'))
    out.append(_pair(70, str(len(layers))))
    for name, color in layers:
        out.append(_layer_record(name, color))
    out.append(_pair(0, 'ENDTAB'))
    out.append(_pair(0, 'ENDSEC'))
    return ''.join(out)


def _polyline(verts, layer, color, precision):
    """Emit a closed R12 POLYLINE entity for one cell's vertices."""
    out = []
    out.append(_pair(0, 'POLYLINE'))
    out.append(_pair(8, layer))
    out.append(_pair(62, str(color)))
    out.append(_pair(66, '1'))   # vertices-follow flag (required in R12)
    out.append(_pair(70, '1'))   # polyline flag: 1 = closed
    out.append(_pair(10, '0'))   # dummy base point
    out.append(_pair(20, '0'))
    out.append(_pair(30, '0'))
    for v in verts:
        out.append(_pair(0, 'VERTEX'))
        out.append(_pair(8, layer))
        out.append(_pair(10, _fmt(v[0], precision)))
        out.append(_pair(20, _fmt(v[1], precision)))
        out.append(_pair(30, '0'))
    out.append(_pair(0, 'SEQEND'))
    out.append(_pair(8, layer))
    return ''.join(out)


def _point(x, y, layer, color, precision):
    out = []
    out.append(_pair(0, 'POINT'))
    out.append(_pair(8, layer))
    out.append(_pair(62, str(color)))
    out.append(_pair(10, _fmt(x, precision)))
    out.append(_pair(20, _fmt(y, precision)))
    out.append(_pair(30, '0'))
    return ''.join(out)


def _text(x, y, content, height, layer, color, precision):
    out = []
    out.append(_pair(0, 'TEXT'))
    out.append(_pair(8, layer))
    out.append(_pair(62, str(color)))
    out.append(_pair(10, _fmt(x, precision)))
    out.append(_pair(20, _fmt(y, precision)))
    out.append(_pair(30, '0'))
    out.append(_pair(40, _fmt(height, precision)))
    out.append(_pair(1, str(content)))
    out.append(_pair(72, '1'))   # horizontal centre
    out.append(_pair(73, '2'))   # vertical middle
    out.append(_pair(11, _fmt(x, precision)))  # alignment point (for 72/73)
    out.append(_pair(21, _fmt(y, precision)))
    out.append(_pair(31, '0'))
    return ''.join(out)


def _default_label_height(minx, miny, maxx, maxy):
    """Pick a readable text height relative to the drawing extent."""
    span = max(maxx - minx, maxy - miny)
    if span <= 0:
        return 1.0
    return span / 50.0


def build_dxf(
    regions,
    data,
    *,
    include_seeds=True,
    include_labels=False,
    precision=6,
    label_height=None,
    region_names_fn=None,
):
    """Build a complete DXF (R12) document as a string.

    Parameters
    ----------
    regions : dict
        Output of ``compute_regions()`` - maps seed -> vertex list.
    data : list of (float, float)
        All seed points.
    include_seeds : bool
        Emit seed POINT entities (default True).
    include_labels : bool
        Emit per-region TEXT labels at cell centroids (default False).
    precision : int or None
        Decimal places for coordinates (default 6; None -> 6).
    label_height : float or None
        Text height for labels.  ``None`` derives a value from the extent.
    region_names_fn : callable or None
        Optional ``(seed, vertices, index) -> str`` for custom label text.

    Returns
    -------
    str
        The DXF document text.
    """
    if not regions:
        raise ValueError("No regions to export")

    sorted_seeds = sorted(regions.keys())
    data_lookup = _build_data_index(data)

    minx, miny, maxx, maxy = _bounds(regions, data, include_seeds)
    if label_height is None:
        label_height = _default_label_height(minx, miny, maxx, maxy)

    layers = [
        (LAYER_CELLS, _CELL_COLOR),
        (LAYER_SEEDS, _SEED_COLOR),
        (LAYER_LABELS, _LABEL_COLOR),
    ]

    parts = []
    parts.append(_header(minx, miny, maxx, maxy, precision))
    parts.append(_tables(layers))

    # ENTITIES section
    parts.append(_pair(0, 'SECTION'))
    parts.append(_pair(2, 'ENTITIES'))

    for idx, seed in enumerate(sorted_seeds):
        verts = regions[seed]
        if not verts:
            continue
        parts.append(_polyline(verts, LAYER_CELLS, _CELL_COLOR, precision))

        if include_labels:
            data_idx = data_lookup.get(tuple(seed), idx)
            if region_names_fn:
                name = region_names_fn(seed, verts, data_idx)
            else:
                name = 'R%d' % (data_idx + 1)
            centroid = _polygon_centroid(verts)
            if centroid:
                cx, cy = centroid[0], centroid[1]
            else:
                cx, cy = seed[0], seed[1]
            parts.append(
                _text(cx, cy, name, label_height, LAYER_LABELS, _LABEL_COLOR, precision)
            )

    if include_seeds:
        for (px, py) in data:
            parts.append(_point(px, py, LAYER_SEEDS, _SEED_COLOR, precision))

    parts.append(_pair(0, 'ENDSEC'))
    parts.append(_pair(0, 'EOF'))

    return ''.join(parts)


def export_dxf(
    regions,
    data,
    output_path,
    *,
    include_seeds=True,
    include_labels=False,
    precision=6,
    label_height=None,
    region_names_fn=None,
):
    """Export Voronoi regions to a DXF file.

    Mirrors :func:`build_dxf` but writes the result to ``output_path``
    (validated against path-traversal) and returns the written path.
    """
    content = build_dxf(
        regions,
        data,
        include_seeds=include_seeds,
        include_labels=include_labels,
        precision=precision,
        label_height=label_height,
        region_names_fn=region_names_fn,
    )

    output_path = vormap.validate_output_path(output_path, allow_absolute=True)
    with open(output_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    return output_path


def export_summary(regions, data, *, precision=6):
    """Return a small dict describing what an export would contain.

    Handy for CLIs and tests: counts cells/seeds/vertices and reports the
    drawing extents without writing a file.
    """
    sorted_seeds = sorted(regions.keys()) if regions else []
    total_vertices = 0
    total_area = 0.0
    total_perimeter = 0.0
    for seed in sorted_seeds:
        verts = regions[seed]
        total_vertices += len(verts)
        total_area += _polygon_area(verts)
        total_perimeter += _polygon_perimeter(verts)
    minx, miny, maxx, maxy = _bounds(regions, data, True)
    return {
        'cell_count': len(sorted_seeds),
        'seed_count': len(data),
        'vertex_count': total_vertices,
        'total_area': round(total_area, precision if precision is not None else 6),
        'total_perimeter': round(
            total_perimeter, precision if precision is not None else 6
        ),
        'extent': {
            'min_x': minx, 'min_y': miny, 'max_x': maxx, 'max_y': maxy,
        },
    }


def generate_dxf(
    datafile,
    output_path='voronoi.dxf',
    *,
    include_seeds=True,
    include_labels=False,
    precision=6,
    label_height=None,
):
    """Load data, compute regions, and export DXF in one call.

    Parameters
    ----------
    datafile : str
        Filename inside the data/ directory.
    output_path : str
        Where to write the DXF file.
    include_seeds : bool
        Include seed POINT entities.
    include_labels : bool
        Include per-region TEXT labels.
    precision : int or None
        Decimal places for coordinates.
    label_height : float or None
        Text height for labels (None derives from extent).

    Returns
    -------
    str
        Path to the generated DXF file.
    """
    import vormap_viz

    data = vormap.load_data(datafile)
    regions = vormap_viz.compute_regions(data)
    return export_dxf(
        regions,
        data,
        output_path,
        include_seeds=include_seeds,
        include_labels=include_labels,
        precision=precision,
        label_height=label_height,
    )


def main():
    """CLI entry point for DXF export."""
    parser = argparse.ArgumentParser(
        description='Export VoronoiMap diagrams as AutoCAD DXF (R12) files',
    )
    parser.add_argument('datafile', help='Input data file (in data/ directory)')
    parser.add_argument(
        '-o', '--output',
        default='voronoi.dxf',
        help='Output DXF file path (default: voronoi.dxf)',
    )
    parser.add_argument(
        '--no-seeds',
        action='store_true',
        help='Exclude seed points from output',
    )
    parser.add_argument(
        '--labels',
        action='store_true',
        help='Add per-region text labels at cell centroids',
    )
    parser.add_argument(
        '--precision',
        type=int,
        default=6,
        help='Decimal places for coordinates (default: 6)',
    )
    parser.add_argument(
        '--label-height',
        type=float,
        default=None,
        help='Text height for labels (default: derived from drawing extent)',
    )

    args = parser.parse_args()

    try:
        path = generate_dxf(
            args.datafile,
            args.output,
            include_seeds=not args.no_seeds,
            include_labels=args.labels,
            precision=args.precision,
            label_height=args.label_height,
        )
        print('DXF exported to: %s' % path)
    except Exception as e:
        print('Error: %s' % e, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
