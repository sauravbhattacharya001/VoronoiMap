"""vormap_gpx — GPX import/export for VoronoiMap.

Import point data from GPX files (waypoints and track points) and export
Voronoi seed points or region centroids back to GPX format for use in GPS
devices, mapping apps, and GIS tools.

Supports:
- Import waypoints (<wpt>) as seed points
- Import track points (<trkpt>) as seed points
- Import route points (<rtept>) as seed points
- Export seed points as GPX waypoints
- Export region centroids as GPX waypoints
- Metadata preservation (name, description, elevation, time)
- CLI interface

Usage (API)::

    from vormap_gpx import load_gpx, export_gpx

    # Load points from a GPX file
    points, metadata = load_gpx("trail.gpx", source="all")

    # Export points to GPX
    export_gpx(points, "output.gpx", names=["Site A", "Site B", ...])

Usage (CLI)::

    python vormap_gpx.py import trail.gpx --source waypoints --output data/trail.txt
    python vormap_gpx.py export data/sites.txt --output sites.gpx
    python vormap_gpx.py info trail.gpx
"""

import math
import os
import sys
import xml.etree.ElementTree as ET

__all__ = ["load_gpx", "export_gpx", "gpx_info"]

# GPX XML namespace
_GPX_NS = "http://www.topografix.com/GPX/1/1"
_NS = {"gpx": _GPX_NS}


def _find(element, tag):
    """Find a child element with GPX namespace."""
    return element.find("gpx:" + tag, _NS)


def _findall(element, tag):
    """Find all child elements with GPX namespace."""
    return element.findall("gpx:" + tag, _NS)


def _findall_deep(element, outer, inner):
    """Find nested elements (e.g. trk/trkseg/trkpt)."""
    results = []
    for parent in _findall(element, outer):
        for seg in _findall(parent, inner):
            results.append(seg)
    return results


def _parse_point(elem):
    """Parse lat/lon from an element with lat/lon attributes.

    Returns (lon, lat, metadata_dict).  Longitude is x, latitude is y,
    matching VoronoiMap's (x, y) convention.
    """
    lat = elem.get("lat")
    lon = elem.get("lon")
    if lat is None or lon is None:
        return None

    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (ValueError, TypeError):
        return None

    if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
        return None

    meta = {}
    name_el = _find(elem, "name")
    if name_el is not None and name_el.text:
        meta["name"] = name_el.text.strip()

    desc_el = _find(elem, "desc")
    if desc_el is not None and desc_el.text:
        meta["desc"] = desc_el.text.strip()

    ele_el = _find(elem, "ele")
    if ele_el is not None and ele_el.text:
        try:
            meta["ele"] = float(ele_el.text.strip())
        except ValueError:
            pass

    time_el = _find(elem, "time")
    if time_el is not None and time_el.text:
        meta["time"] = time_el.text.strip()

    return (lon_f, lat_f, meta)


def load_gpx(filepath, source="all"):
    """Load points from a GPX file.

    Parameters
    ----------
    filepath : str
        Path to the GPX file.
    source : str
        Which elements to extract: ``"waypoints"``, ``"tracks"``,
        ``"routes"``, or ``"all"`` (default).

    Returns
    -------
    points : list of (float, float)
        List of (x, y) i.e. (longitude, latitude) tuples.
    metadata : list of dict
        Per-point metadata (name, desc, ele, time) where available.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file is not valid GPX or contains no points.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError("GPX file not found: %s" % filepath)

    try:
        tree = ET.parse(filepath)
    except ET.ParseError as exc:
        raise ValueError("Invalid GPX XML: %s" % exc)

    root = tree.getroot()

    # Handle files with or without namespace
    has_ns = root.tag.startswith("{")

    parsed = []
    source = source.lower()

    if source in ("all", "waypoints"):
        if has_ns:
            elems = _findall(root, "wpt")
        else:
            elems = root.findall("wpt")
        for elem in elems:
            p = _parse_point(elem)
            if p:
                parsed.append(p)

    if source in ("all", "tracks"):
        if has_ns:
            for trk in _findall(root, "trk"):
                for seg in _findall(trk, "trkseg"):
                    for pt in _findall(seg, "trkpt"):
                        p = _parse_point(pt)
                        if p:
                            parsed.append(p)
        else:
            for trk in root.findall("trk"):
                for seg in trk.findall("trkseg"):
                    for pt in seg.findall("trkpt"):
                        p = _parse_point(pt)
                        if p:
                            parsed.append(p)

    if source in ("all", "routes"):
        if has_ns:
            for rte in _findall(root, "rte"):
                for pt in _findall(rte, "rtept"):
                    p = _parse_point(pt)
                    if p:
                        parsed.append(p)
        else:
            for rte in root.findall("rte"):
                for pt in rte.findall("rtept"):
                    p = _parse_point(pt)
                    if p:
                        parsed.append(p)

    if not parsed:
        raise ValueError(
            "No valid points found in '%s' (source=%s)" % (filepath, source)
        )

    points = [(lon, lat) for lon, lat, _ in parsed]
    metadata = [m for _, _, m in parsed]
    return points, metadata


def export_gpx(points, filepath, names=None, descriptions=None,
               elevations=None, creator="VoronoiMap"):
    """Export points to a GPX file as waypoints.

    Parameters
    ----------
    points : list of (float, float)
        List of (x, y) i.e. (longitude, latitude) tuples.
    filepath : str
        Output GPX file path.
    names : list of str, optional
        Per-point names.  Defaults to ``"Point 1"``, ``"Point 2"``, etc.
    descriptions : list of str, optional
        Per-point descriptions.
    elevations : list of float, optional
        Per-point elevations.
    creator : str
        Creator attribute in the GPX root element.
    """
    gpx = ET.Element("gpx", {
        "version": "1.1",
        "creator": creator,
        "xmlns": _GPX_NS,
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": (
            "http://www.topografix.com/GPX/1/1 "
            "http://www.topografix.com/GPX/1/1/gpx.xsd"
        ),
    })

    for i, (lon, lat) in enumerate(points):
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue

        wpt = ET.SubElement(gpx, "wpt", {
            "lat": "%.8f" % lat,
            "lon": "%.8f" % lon,
        })

        name = (names[i] if names and i < len(names)
                else "Point %d" % (i + 1))
        name_el = ET.SubElement(wpt, "name")
        name_el.text = name

        if descriptions and i < len(descriptions):
            desc_el = ET.SubElement(wpt, "desc")
            desc_el.text = descriptions[i]

        if elevations and i < len(elevations):
            try:
                ele_val = float(elevations[i])
                if math.isfinite(ele_val):
                    ele_el = ET.SubElement(wpt, "ele")
                    ele_el.text = "%.2f" % ele_val
            except (ValueError, TypeError):
                pass

    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ")

    os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
    tree.write(filepath, xml_declaration=True, encoding="UTF-8")


def gpx_info(filepath):
    """Print summary information about a GPX file.

    Returns a dict with counts and bounding box.
    """
    if not os.path.isfile(filepath):
        raise FileNotFoundError("GPX file not found: %s" % filepath)

    tree = ET.parse(filepath)
    root = tree.getroot()
    has_ns = root.tag.startswith("{")

    def count(tag):
        if has_ns:
            return len(_findall(root, tag))
        return len(root.findall(tag))

    def count_nested(outer, middle, inner):
        total = 0
        outers = _findall(root, outer) if has_ns else root.findall(outer)
        for o in outers:
            mids = _findall(o, middle) if has_ns else o.findall(middle)
            for m in mids:
                inners = _findall(m, inner) if has_ns else m.findall(inner)
                total += len(inners)
        return total

    def count_rte():
        total = 0
        rtes = _findall(root, "rte") if has_ns else root.findall("rte")
        for r in rtes:
            pts = _findall(r, "rtept") if has_ns else r.findall("rtept")
            total += len(pts)
        return total

    wpt_count = count("wpt")
    trk_count = count("trk")
    trkpt_count = count_nested("trk", "trkseg", "trkpt")
    rte_count = count("rte")
    rtept_count = count_rte()

    # Get bounding box from all points
    all_points, _ = load_gpx(filepath, source="all")
    if all_points:
        lons = [p[0] for p in all_points]
        lats = [p[1] for p in all_points]
        bbox = {
            "min_lon": min(lons),
            "max_lon": max(lons),
            "min_lat": min(lats),
            "max_lat": max(lats),
        }
    else:
        bbox = None

    info = {
        "waypoints": wpt_count,
        "tracks": trk_count,
        "track_points": trkpt_count,
        "routes": rte_count,
        "route_points": rtept_count,
        "total_points": len(all_points),
        "bbox": bbox,
    }
    return info


# ── CLI ──────────────────────────────────────────────────────────────

def _cli_import(args):
    """Import GPX → text file."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="vormap_gpx import",
        description="Import points from a GPX file",
    )
    parser.add_argument("gpx_file", help="Input GPX file")
    parser.add_argument("--source", default="all",
                        choices=["all", "waypoints", "tracks", "routes"],
                        help="Which GPX elements to extract (default: all)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output file (default: stdout). "
                             "Extension determines format: .csv, .json, .txt")
    opts = parser.parse_args(args)

    points, metadata = load_gpx(opts.gpx_file, source=opts.source)

    if opts.output:
        ext = os.path.splitext(opts.output)[1].lower()
        os.makedirs(os.path.dirname(os.path.abspath(opts.output)) or ".",
                    exist_ok=True)
        if ext == ".csv":
            with open(opts.output, "w") as f:
                f.write("longitude,latitude,name,elevation\n")
                for (lon, lat), meta in zip(points, metadata):
                    name = meta.get("name", "")
                    ele = meta.get("ele", "")
                    f.write("%.8f,%.8f,%s,%s\n" % (lon, lat, name, ele))
        elif ext == ".json":
            import json
            data = []
            for (lon, lat), meta in zip(points, metadata):
                entry = {"lon": lon, "lat": lat}
                entry.update(meta)
                data.append(entry)
            with open(opts.output, "w") as f:
                json.dump(data, f, indent=2)
        else:
            with open(opts.output, "w") as f:
                for lon, lat in points:
                    f.write("%.8f %.8f\n" % (lon, lat))
        print("Exported %d points to %s" % (len(points), opts.output))
    else:
        for lon, lat in points:
            print("%.8f %.8f" % (lon, lat))


def _cli_export(args):
    """Export text file → GPX."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="vormap_gpx export",
        description="Export points to GPX format",
    )
    parser.add_argument("input_file",
                        help="Input file (.txt, .csv, .json)")
    parser.add_argument("--output", "-o", required=True,
                        help="Output GPX file")
    opts = parser.parse_args(args)

    # Read points from input file
    ext = os.path.splitext(opts.input_file)[1].lower()
    points = []
    names = []

    if ext == ".csv":
        with open(opts.input_file) as f:
            header = f.readline().strip().lower()
            for line in f:
                parts = line.strip().split(",")
                if len(parts) >= 2:
                    try:
                        lon, lat = float(parts[0]), float(parts[1])
                        points.append((lon, lat))
                        if len(parts) >= 3 and parts[2]:
                            names.append(parts[2])
                        else:
                            names.append(None)
                    except ValueError:
                        continue
    elif ext == ".json":
        import json
        with open(opts.input_file) as f:
            data = json.load(f)
        for entry in data:
            if isinstance(entry, dict):
                lon = entry.get("lon", entry.get("x", entry.get("longitude")))
                lat = entry.get("lat", entry.get("y", entry.get("latitude")))
                if lon is not None and lat is not None:
                    points.append((float(lon), float(lat)))
                    names.append(entry.get("name"))
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                points.append((float(entry[0]), float(entry[1])))
                names.append(None)
    else:
        with open(opts.input_file) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    try:
                        points.append((float(parts[0]), float(parts[1])))
                        names.append(None)
                    except ValueError:
                        continue

    if not points:
        print("Error: No valid points found in %s" % opts.input_file)
        sys.exit(1)

    final_names = [n or ("Point %d" % (i + 1)) for i, n in enumerate(names)]
    export_gpx(points, opts.output, names=final_names)
    print("Exported %d points to %s" % (len(points), opts.output))


def _cli_info(args):
    """Show GPX file summary."""
    import argparse
    parser = argparse.ArgumentParser(
        prog="vormap_gpx info",
        description="Show GPX file summary information",
    )
    parser.add_argument("gpx_file", help="GPX file to inspect")
    opts = parser.parse_args(args)

    info = gpx_info(opts.gpx_file)
    print("GPX File: %s" % opts.gpx_file)
    print("  Waypoints:     %d" % info["waypoints"])
    print("  Tracks:        %d" % info["tracks"])
    print("  Track points:  %d" % info["track_points"])
    print("  Routes:        %d" % info["routes"])
    print("  Route points:  %d" % info["route_points"])
    print("  Total points:  %d" % info["total_points"])
    if info["bbox"]:
        b = info["bbox"]
        print("  Bounding box:  [%.6f, %.6f] to [%.6f, %.6f]" % (
            b["min_lon"], b["min_lat"], b["max_lon"], b["max_lat"]))


def main():
    """CLI entry point."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python vormap_gpx.py <command> [options]")
        print()
        print("Commands:")
        print("  import   Import points from a GPX file")
        print("  export   Export points to GPX format")
        print("  info     Show GPX file summary")
        print()
        print("Examples:")
        print("  python vormap_gpx.py import trail.gpx -o data/trail.txt")
        print("  python vormap_gpx.py export data/sites.txt -o sites.gpx")
        print("  python vormap_gpx.py info trail.gpx")
        sys.exit(0)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "import":
        _cli_import(rest)
    elif cmd == "export":
        _cli_export(rest)
    elif cmd == "info":
        _cli_info(rest)
    else:
        print("Unknown command: %s" % cmd)
        print("Use --help for usage information")
        sys.exit(1)


if __name__ == "__main__":
    main()
