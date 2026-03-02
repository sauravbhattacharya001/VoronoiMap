"""Tests for vormap_kml — KML export functionality."""

import os
import tempfile
import xml.etree.ElementTree as ET

import vormap_kml


def _make_regions_and_data():
    """Create simple test data with 3 seeds and mock regions."""
    data = [(100, 200), (300, 400), (500, 600)]
    regions = {
        (100, 200): [(50, 150), (150, 150), (150, 250), (50, 250)],
        (300, 400): [(250, 350), (350, 350), (350, 450), (250, 450)],
        (500, 600): [(450, 550), (550, 550), (550, 650), (450, 650)],
    }
    return regions, data


class TestExportKml:
    def test_basic_export(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'test.kml')
        result = vormap_kml.export_kml(regions, data, out)
        assert os.path.exists(result)
        assert result == out

        # Parse and validate structure
        tree = ET.parse(out)
        root = tree.getroot()
        ns = '{http://www.opengis.net/kml/2.2}'
        doc = root.find(ns + 'Document')
        assert doc is not None

        folders = doc.findall(ns + 'Folder')
        assert len(folders) == 2  # Regions + Seeds

        # Check region placemarks
        region_folder = folders[0]
        placemarks = region_folder.findall(ns + 'Placemark')
        assert len(placemarks) == 3

    def test_no_seeds(self, tmp_path):
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'no_seeds.kml')
        vormap_kml.export_kml(regions, data, out, include_seeds=False)

        tree = ET.parse(out)
        root = tree.getroot()
        ns = '{http://www.opengis.net/kml/2.2}'
        doc = root.find(ns + 'Document')
        folders = doc.findall(ns + 'Folder')
        assert len(folders) == 1  # Only Regions

    def test_color_schemes(self, tmp_path):
        regions, data = _make_regions_and_data()
        for scheme in ['pastel', 'warm', 'cool', 'earth', 'mono', 'rainbow']:
            out = str(tmp_path / ('%s.kml' % scheme))
            vormap_kml.export_kml(
                regions, data, out, color_scheme=scheme
            )
            assert os.path.exists(out)

    def test_empty_regions_raises(self, tmp_path):
        import pytest
        out = str(tmp_path / 'empty.kml')
        with pytest.raises(ValueError, match="No regions"):
            vormap_kml.export_kml({}, [], out)

    def test_polygon_closed(self, tmp_path):
        """Verify KML polygon rings are closed."""
        regions, data = _make_regions_and_data()
        out = str(tmp_path / 'closed.kml')
        vormap_kml.export_kml(regions, data, out)

        tree = ET.parse(out)
        root = tree.getroot()
        ns = '{http://www.opengis.net/kml/2.2}'
        doc = root.find(ns + 'Document')
        folder = doc.findall(ns + 'Folder')[0]
        pm = folder.findall(ns + 'Placemark')[0]
        coords_text = pm.find(
            './/' + ns + 'coordinates'
        ).text.strip()
        coords = coords_text.split()
        assert coords[0] == coords[-1]  # ring is closed
