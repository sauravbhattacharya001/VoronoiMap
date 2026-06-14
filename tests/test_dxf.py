"""Tests for vormap_dxf - AutoCAD DXF (R12) export."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vormap_dxf import (
    build_dxf,
    export_dxf,
    export_summary,
    generate_dxf,
    LAYER_CELLS,
    LAYER_SEEDS,
    LAYER_LABELS,
    _fmt,
    _pair,
    _bounds,
    _default_label_height,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _square(cx, cy, size):
    h = size / 2.0
    return [
        (cx - h, cy - h), (cx + h, cy - h),
        (cx + h, cy + h), (cx - h, cy + h),
    ]


def _two_cells():
    regions = {(0.0, 0.0): _square(0, 0, 2), (5.0, 0.0): _square(5, 0, 2)}
    data = [(0.0, 0.0), (5.0, 0.0)]
    return regions, data


def _records(doc):
    """Parse a DXF string into a list of (code, value) records."""
    lines = doc.split('\n')
    if lines and lines[-1] == '':
        lines = lines[:-1]
    assert len(lines) % 2 == 0, 'DXF must have an even number of lines'
    out = []
    for i in range(0, len(lines), 2):
        out.append((int(lines[i]), lines[i + 1]))
    return out


def _entities(doc, kind):
    """Count entities of a given type (e.g. POLYLINE, POINT, TEXT)."""
    return [v for (c, v) in _records(doc) if c == 0 and v == kind]


# ---------------------------------------------------------------------------
# _fmt
# ---------------------------------------------------------------------------

def test_fmt_trims_trailing_zeros():
    assert _fmt(1.5000, 6) == '1.5'
    assert _fmt(2.0, 6) == '2'
    assert _fmt(3, 6) == '3'


def test_fmt_respects_precision():
    assert _fmt(1.23456789, 3) == '1.235'
    assert _fmt(1.23456789, 0) == '1'


def test_fmt_normalises_negative_zero():
    assert _fmt(-0.0, 6) == '0'
    assert _fmt(-0.0000001, 3) == '0'


def test_fmt_no_scientific_notation():
    s = _fmt(0.0000001, 10)
    assert 'e' not in s.lower()


def test_fmt_handles_bad_input():
    assert _fmt(None, 6) == '0'
    assert _fmt('nan-ish', 6) == '0'


def test_fmt_default_precision_when_none():
    assert _fmt(1.123456789, None) == '1.123457'


# ---------------------------------------------------------------------------
# _pair
# ---------------------------------------------------------------------------

def test_pair_format():
    assert _pair(10, '1.5') == '10\n1.5\n'
    assert _pair(0, 'POLYLINE') == '0\nPOLYLINE\n'


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

def test_doc_is_well_formed_pairs():
    regions, data = _two_cells()
    recs = _records(build_dxf(regions, data))
    assert recs[0] == (0, 'SECTION')
    assert recs[-1] == (0, 'EOF')


def test_required_sections_present():
    regions, data = _two_cells()
    doc = build_dxf(regions, data)
    for tok in ('HEADER', 'TABLES', 'ENTITIES'):
        assert '2\n%s\n' % tok in doc
    assert '0\nENDSEC\n' in doc


def test_header_has_version_and_extents():
    regions, data = _two_cells()
    doc = build_dxf(regions, data, precision=3)
    assert '9\n$ACADVER\n' in doc
    assert '1\nAC1009\n' in doc      # R12 marker
    assert '9\n$EXTMIN\n' in doc
    assert '9\n$EXTMAX\n' in doc


def test_layer_table_defines_three_layers():
    regions, data = _two_cells()
    doc = build_dxf(regions, data)
    layers = [v for (c, v) in _records(doc) if c == 2]
    for layer in (LAYER_CELLS, LAYER_SEEDS, LAYER_LABELS):
        assert layer in layers


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

def test_cells_emitted_as_closed_polylines():
    regions, data = _two_cells()
    doc = build_dxf(regions, data, include_seeds=False)
    assert len(_entities(doc, 'POLYLINE')) == 2
    # closed flag 70=1 must appear for each polyline
    assert doc.count('70\n1\n') == 2
    # vertices-follow flag present
    assert doc.count('66\n1\n') == 2


def test_each_polyline_has_correct_vertex_count():
    regions, data = _two_cells()
    doc = build_dxf(regions, data, include_seeds=False)
    # two squares -> 8 vertices total, each terminated by a SEQEND
    assert len(_entities(doc, 'VERTEX')) == 8
    assert len(_entities(doc, 'SEQEND')) == 2


def test_seeds_emitted_as_points_when_enabled():
    regions, data = _two_cells()
    doc = build_dxf(regions, data, include_seeds=True)
    assert len(_entities(doc, 'POINT')) == 2


def test_seeds_omitted_when_disabled():
    regions, data = _two_cells()
    doc = build_dxf(regions, data, include_seeds=False)
    assert len(_entities(doc, 'POINT')) == 0


def test_labels_emitted_when_enabled():
    regions, data = _two_cells()
    doc = build_dxf(regions, data, include_labels=True)
    texts = _entities(doc, 'TEXT')
    assert len(texts) == 2
    assert 'R1' in doc and 'R2' in doc


def test_labels_omitted_by_default():
    regions, data = _two_cells()
    doc = build_dxf(regions, data)
    assert len(_entities(doc, 'TEXT')) == 0


def test_custom_region_names_fn():
    regions, data = _two_cells()
    doc = build_dxf(
        regions, data, include_labels=True,
        region_names_fn=lambda seed, verts, idx: 'CELL_%d' % idx,
    )
    assert 'CELL_0' in doc
    assert 'CELL_1' in doc


def test_empty_vertex_cell_skipped():
    regions = {(0.0, 0.0): _square(0, 0, 2), (9.0, 9.0): []}
    data = [(0.0, 0.0), (9.0, 9.0)]
    doc = build_dxf(regions, data, include_seeds=False)
    # only the non-empty cell becomes a polyline
    assert len(_entities(doc, 'POLYLINE')) == 1


# ---------------------------------------------------------------------------
# Bounds / label height
# ---------------------------------------------------------------------------

def test_bounds_over_geometry_and_seeds():
    regions, data = _two_cells()
    minx, miny, maxx, maxy = _bounds(regions, data, True)
    assert (minx, miny, maxx, maxy) == (-1.0, -1.0, 6.0, 1.0)


def test_bounds_fallback_unit_box_when_empty():
    assert _bounds({}, [], True) == (0.0, 0.0, 1.0, 1.0)


def test_default_label_height_scales_with_extent():
    assert _default_label_height(0, 0, 100, 0) == 2.0
    assert _default_label_height(0, 0, 0, 0) == 1.0


# ---------------------------------------------------------------------------
# export_summary
# ---------------------------------------------------------------------------

def test_export_summary_counts_and_geometry():
    regions, data = _two_cells()
    s = export_summary(regions, data, precision=2)
    assert s['cell_count'] == 2
    assert s['seed_count'] == 2
    assert s['vertex_count'] == 8
    assert s['total_area'] == 8.0
    assert s['total_perimeter'] == 16.0
    assert s['extent']['min_x'] == -1.0
    assert s['extent']['max_x'] == 6.0


# ---------------------------------------------------------------------------
# Errors & determinism
# ---------------------------------------------------------------------------

def test_empty_regions_raises():
    with pytest.raises(ValueError):
        build_dxf({}, [])


def test_output_is_deterministic():
    regions, data = _two_cells()
    assert build_dxf(regions, data) == build_dxf(regions, data)


def test_seed_order_does_not_change_cell_order():
    # cells are sorted by seed key, so reversed data must still yield the
    # same polyline ordering in the document.
    regions, data = _two_cells()
    doc_a = build_dxf(regions, data, include_seeds=False)
    doc_b = build_dxf(regions, list(reversed(data)), include_seeds=False)
    assert doc_a == doc_b


# ---------------------------------------------------------------------------
# File export
# ---------------------------------------------------------------------------

def test_export_dxf_writes_file():
    regions, data = _two_cells()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'out.dxf')
        result = export_dxf(regions, data, path)
        assert os.path.isfile(result)
        with open(result, 'r', encoding='utf-8') as f:
            content = f.read()
        assert content.startswith('0\nSECTION\n')
        assert content.rstrip('\n').endswith('EOF')


def test_export_dxf_roundtrips_geometry():
    regions, data = _two_cells()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'out.dxf')
        export_dxf(regions, data, path, include_seeds=True, include_labels=True)
        with open(path, 'r', encoding='utf-8') as f:
            doc = f.read()
        assert len(_entities(doc, 'POLYLINE')) == 2
        assert len(_entities(doc, 'POINT')) == 2
        assert len(_entities(doc, 'TEXT')) == 2


# ---------------------------------------------------------------------------
# generate_dxf integration (uses the real pipeline)
# ---------------------------------------------------------------------------

def test_generate_dxf_end_to_end(monkeypatch):
    import vormap
    import vormap_viz

    pts = [(0.0, 0.0), (10.0, 0.0), (5.0, 8.0), (5.0, 3.0)]
    monkeypatch.setattr(vormap, 'load_data', lambda fn: pts)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'gen.dxf')
        result = generate_dxf('ignored.txt', path, include_seeds=True)
        assert os.path.isfile(result)
        with open(result, 'r', encoding='utf-8') as f:
            doc = f.read()
        # at least one cell + four seeds
        assert len(_entities(doc, 'POLYLINE')) >= 1
        assert len(_entities(doc, 'POINT')) == 4
        # sanity: regions actually computed for these points
        regions = vormap_viz.compute_regions(pts)
        assert len(regions) >= 1
