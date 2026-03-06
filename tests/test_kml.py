"""Comprehensive tests for vormap_kml — KML export functionality.

Covers: export_kml with all options, KML structure validation,
coordinate format, polygon ring closure, color schemes, custom
naming, error handling, and edge cases.
"""

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vormap_kml

KML_NS = '{http://www.opengis.net/kml/2.2}'


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_regions_and_data(n=3):
    """Create test data with n seeds and simple square regions."""
    data = [(100 + i * 200, 200 + i * 200) for i in range(n)]
    regions = {}
    for x, y in data:
        regions[(x, y)] = [
            (x - 50, y - 50), (x + 50, y - 50),
            (x + 50, y + 50), (x - 50, y + 50),
        ]
    return regions, data


def _parse_kml(path):
    """Parse KML file and return (root, Document element)."""
    tree = ET.parse(path)
    root = tree.getroot()
    doc = root.find(KML_NS + 'Document')
    return root, doc


def _get_folders(doc):
    """Get all Folder elements from Document."""
    return doc.findall(KML_NS + 'Folder')


def _get_placemarks(folder):
    """Get all Placemark elements from a Folder."""
    return folder.findall(KML_NS + 'Placemark')


# ── Basic export ─────────────────────────────────────────────────────

class TestExportKmlBasic:
    def test_creates_file(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'test.kml')
        result = vormap_kml.export_kml(regions, data, out)
        assert os.path.exists(result)
        assert result == out

    def test_returns_output_path(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'ret.kml')
        result = vormap_kml.export_kml(regions, data, out)
        assert result == out

    def test_valid_xml(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'valid.kml')
        vormap_kml.export_kml(regions, data, out)
        # Should parse without error
        ET.parse(out)

    def test_kml_namespace(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'ns.kml')
        vormap_kml.export_kml(regions, data, out)
        root, doc = _parse_kml(out)
        assert doc is not None

    def test_document_has_name(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'name.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        name_el = doc.find(KML_NS + 'name')
        assert name_el is not None
        assert 'VoronoiMap' in name_el.text

    def test_document_has_description(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'desc.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        desc_el = doc.find(KML_NS + 'description')
        assert desc_el is not None
        assert 'Voronoi' in desc_el.text


# ── Folder structure ─────────────────────────────────────────────────

class TestFolderStructure:
    def test_two_folders_with_seeds(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'folders.kml')
        vormap_kml.export_kml(regions, data, out, include_seeds=True)
        _, doc = _parse_kml(out)
        folders = _get_folders(doc)
        assert len(folders) == 2
        names = [f.find(KML_NS + 'name').text for f in folders]
        assert 'Voronoi Regions' in names
        assert 'Seed Points' in names

    def test_one_folder_without_seeds(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'noseed.kml')
        vormap_kml.export_kml(regions, data, out, include_seeds=False)
        _, doc = _parse_kml(out)
        folders = _get_folders(doc)
        assert len(folders) == 1
        assert folders[0].find(KML_NS + 'name').text == 'Voronoi Regions'


# ── Region placemarks ────────────────────────────────────────────────

class TestRegionPlacemarks:
    def test_correct_count(self, tmp_path):
        for n in (1, 3, 5, 8):
            regions, data = _make_regions_and_data(n)
            out = str(tmp_path / f'count_{n}.kml')
            vormap_kml.export_kml(regions, data, out)
            _, doc = _parse_kml(out)
            folders = _get_folders(doc)
            region_folder = [f for f in folders
                             if f.find(KML_NS + 'name').text == 'Voronoi Regions'][0]
            pms = _get_placemarks(region_folder)
            assert len(pms) == n

    def test_has_name(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'pname.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            name = pm.find(KML_NS + 'name')
            assert name is not None
            assert name.text.startswith('Region ')

    def test_has_description_with_seed_coords(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'pdesc.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            desc = pm.find(KML_NS + 'description')
            assert desc is not None
            assert 'Seed:' in desc.text
            assert 'Area:' in desc.text
            assert 'Vertices:' in desc.text

    def test_has_style_url(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'pstyle.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            style_url = pm.find(KML_NS + 'styleUrl')
            assert style_url is not None
            assert style_url.text.startswith('#style')

    def test_has_polygon(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'ppoly.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            polygon = pm.find(KML_NS + 'Polygon')
            assert polygon is not None

    def test_polygon_has_outer_boundary(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'pouter.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            polygon = pm.find(KML_NS + 'Polygon')
            outer = polygon.find(KML_NS + 'outerBoundaryIs')
            assert outer is not None
            ring = outer.find(KML_NS + 'LinearRing')
            assert ring is not None
            coords = ring.find(KML_NS + 'coordinates')
            assert coords is not None
            assert coords.text.strip() != ''

    def test_polygon_ring_closed(self, tmp_path):
        """KML polygon rings must have first == last coordinate."""
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'closed.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            coords_el = pm.find('.//' + KML_NS + 'coordinates')
            coord_list = coords_el.text.strip().split()
            assert len(coord_list) >= 4  # at least 3 vertices + closure
            assert coord_list[0] == coord_list[-1]

    def test_coordinate_format_lng_lat_alt(self, tmp_path):
        """Each coordinate should be lng,lat,alt format."""
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'fmt.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        pm = _get_placemarks(region_folder)[0]
        coords_el = pm.find('.//' + KML_NS + 'coordinates')
        for coord in coords_el.text.strip().split():
            parts = coord.split(',')
            assert len(parts) == 3  # lng, lat, alt
            for p in parts:
                float(p)  # should not raise

    def test_altitude_mode_clamp_to_ground(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'alt.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            polygon = pm.find(KML_NS + 'Polygon')
            alt_mode = polygon.find(KML_NS + 'altitudeMode')
            assert alt_mode is not None
            assert alt_mode.text == 'clampToGround'


# ── Seed placemarks ──────────────────────────────────────────────────

class TestSeedPlacemarks:
    def test_correct_count(self, tmp_path):
        regions, data = _make_regions_and_data(5)
        out = str(tmp_path / 'seeds.kml')
        vormap_kml.export_kml(regions, data, out, include_seeds=True)
        _, doc = _parse_kml(out)
        seed_folder = [f for f in _get_folders(doc)
                       if f.find(KML_NS + 'name').text == 'Seed Points'][0]
        pms = _get_placemarks(seed_folder)
        assert len(pms) == 5

    def test_has_name(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'sname.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        seed_folder = _get_folders(doc)[1]
        for pm in _get_placemarks(seed_folder):
            name = pm.find(KML_NS + 'name')
            assert name is not None
            assert name.text.startswith('Seed ')

    def test_has_point_element(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'spoint.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        seed_folder = _get_folders(doc)[1]
        for pm in _get_placemarks(seed_folder):
            point = pm.find(KML_NS + 'Point')
            assert point is not None
            coords = point.find(KML_NS + 'coordinates')
            assert coords is not None

    def test_seed_style(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'sstyle.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        seed_folder = _get_folders(doc)[1]
        for pm in _get_placemarks(seed_folder):
            style_url = pm.find(KML_NS + 'styleUrl')
            assert style_url is not None
            assert style_url.text == '#seedStyle'

    def test_seed_coordinates_match_data(self, tmp_path):
        data = [(100.0, 200.0), (300.0, 400.0)]
        regions = {
            (100.0, 200.0): [(50, 150), (150, 150), (150, 250), (50, 250)],
            (300.0, 400.0): [(250, 350), (350, 350), (350, 450), (250, 450)],
        }
        out = str(tmp_path / 'smatch.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        seed_folder = _get_folders(doc)[1]
        pms = _get_placemarks(seed_folder)
        for i, pm in enumerate(pms):
            point = pm.find(KML_NS + 'Point')
            coords_text = point.find(KML_NS + 'coordinates').text.strip()
            parts = coords_text.split(',')
            lng, lat = float(parts[0]), float(parts[1])
            assert abs(lng - data[i][0]) < 0.01
            assert abs(lat - data[i][1]) < 0.01


# ── Styles ───────────────────────────────────────────────────────────

class TestStyles:
    def test_styles_created(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'styles.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        styles = doc.findall(KML_NS + 'Style')
        # At least color styles + seedStyle
        assert len(styles) >= 2

    def test_seed_style_exists(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'seedst.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        styles = doc.findall(KML_NS + 'Style')
        style_ids = [s.get('id') for s in styles]
        assert 'seedStyle' in style_ids

    def test_region_style_has_line_and_poly(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'stylep.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        styles = doc.findall(KML_NS + 'Style')
        region_style = [s for s in styles if s.get('id', '').startswith('style')
                        and s.get('id') != 'seedStyle'][0]
        assert region_style.find(KML_NS + 'LineStyle') is not None
        assert region_style.find(KML_NS + 'PolyStyle') is not None


# ── Color schemes ────────────────────────────────────────────────────

class TestColorSchemes:
    @pytest.mark.parametrize('scheme', [
        'pastel', 'warm', 'cool', 'earth', 'mono', 'rainbow',
    ])
    def test_all_schemes_produce_valid_kml(self, tmp_path, scheme):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / f'{scheme}.kml')
        vormap_kml.export_kml(regions, data, out, color_scheme=scheme)
        assert os.path.exists(out)
        ET.parse(out)  # should not raise

    def test_unknown_scheme_falls_back(self, tmp_path):
        """Unknown scheme should fall back to pastel (not crash)."""
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'unknown.kml')
        # Module uses .get() with default, so unknown falls back
        vormap_kml.export_kml(regions, data, out, color_scheme='nonexistent')
        assert os.path.exists(out)

    def test_different_schemes_produce_different_colors(self, tmp_path):
        regions, data = _make_regions_and_data()
        contents = {}
        for scheme in ['pastel', 'warm', 'cool']:
            out = str(tmp_path / f'{scheme}.kml')
            vormap_kml.export_kml(regions, data, out, color_scheme=scheme)
            with open(out) as f:
                contents[scheme] = f.read()
        # At least two schemes should differ in PolyStyle colors
        assert contents['pastel'] != contents['warm']


# ── Custom region naming ─────────────────────────────────────────────

class TestCustomNaming:
    def test_custom_names_fn(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'custom.kml')
        vormap_kml.export_kml(
            regions, data, out,
            region_names_fn=lambda seed, verts, idx: f'Zone-{idx}',
        )
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        for pm in _get_placemarks(region_folder):
            name = pm.find(KML_NS + 'name').text
            assert name.startswith('Zone-')

    def test_default_naming(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'default.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        names = [pm.find(KML_NS + 'name').text
                 for pm in _get_placemarks(region_folder)]
        for name in names:
            assert name.startswith('Region ')

    def test_custom_names_receive_correct_args(self, tmp_path):
        regions, data = _make_regions_and_data(2)
        calls = []

        def tracker(seed, verts, idx):
            calls.append({'seed': seed, 'verts_count': len(verts), 'idx': idx})
            return f'Tracked-{idx}'

        out = str(tmp_path / 'track.kml')
        vormap_kml.export_kml(regions, data, out, region_names_fn=tracker)
        assert len(calls) == 2
        for call in calls:
            assert 'seed' in call
            assert call['verts_count'] == 4  # square regions have 4 vertices


# ── Error handling ───────────────────────────────────────────────────

class TestErrorHandling:
    def test_empty_regions_raises(self, tmp_path):
        out = str(tmp_path / 'empty.kml')
        with pytest.raises(ValueError, match="No regions"):
            vormap_kml.export_kml({}, [], out)


# ── Edge cases ───────────────────────────────────────────────────────

class TestEdgeCases:
    def test_single_region(self, tmp_path):
        regions, data = _make_regions_and_data(1)
        out = str(tmp_path / 'single.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        assert len(_get_placemarks(region_folder)) == 1

    def test_many_regions(self, tmp_path):
        """20 regions should all appear in KML."""
        regions, data = _make_regions_and_data(20)
        out = str(tmp_path / 'many.kml')
        vormap_kml.export_kml(regions, data, out)
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        assert len(_get_placemarks(region_folder)) == 20

    def test_style_cycling(self, tmp_path):
        """With more regions than colors, styles should cycle."""
        # pastel has 8 colors; 12 regions should cycle
        regions, data = _make_regions_and_data(12)
        out = str(tmp_path / 'cycle.kml')
        vormap_kml.export_kml(regions, data, out, color_scheme='pastel')
        _, doc = _parse_kml(out)
        region_folder = _get_folders(doc)[0]
        style_urls = [pm.find(KML_NS + 'styleUrl').text
                      for pm in _get_placemarks(region_folder)]
        # First 8 should be style0-7, then cycle
        assert '#style0' in style_urls

    def test_negative_coordinates(self, tmp_path):
        data = [(-100.5, -200.3), (50.0, 75.0)]
        regions = {
            (-100.5, -200.3): [(-150, -250), (-50, -250), (-50, -150), (-150, -150)],
            (50.0, 75.0): [(0, 25), (100, 25), (100, 125), (0, 125)],
        }
        out = str(tmp_path / 'neg.kml')
        vormap_kml.export_kml(regions, data, out)
        # Should parse fine
        ET.parse(out)

    def test_float_precision(self, tmp_path):
        """Coordinates should preserve reasonable precision."""
        data = [(1.123456, 2.654321)]
        regions = {
            (1.123456, 2.654321): [(1.0, 2.0), (1.2, 2.0), (1.2, 3.0), (1.0, 3.0)],
        }
        out = str(tmp_path / 'prec.kml')
        vormap_kml.export_kml(regions, data, out)
        with open(out) as f:
            content = f.read()
        # Should have 6-digit precision in coordinates
        assert '1.123456' in content

    def test_xml_declaration(self, tmp_path):
        """Output should start with XML declaration."""
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'xmldecl.kml')
        vormap_kml.export_kml(regions, data, out)
        with open(out, encoding='utf-8') as f:
            first_line = f.readline().strip()
        assert first_line.startswith('<?xml')

    def test_file_encoding_utf8(self, tmp_path):
        """File should be UTF-8 encoded."""
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'utf8.kml')
        vormap_kml.export_kml(regions, data, out)
        with open(out, 'rb') as f:
            raw = f.read()
        # Should decode as UTF-8 without error
        raw.decode('utf-8')
