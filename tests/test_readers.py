"""Reader tests: KML/KMZ and zipped shapefiles, including malformed input."""

from __future__ import annotations

import datetime as dt
import io
import zipfile

import pytest
import shapefile

from app.geo.kml_reader import parse_kml, read_kmz
from app.geo.shapefile_reader import read_shapefile_zip
from app.geo.types import GeoFileError
from tests.helpers import esri_prj, kml, kml_polygon, make_zip, shapefile_zip, square

LIMITS = {"max_members": 100, "max_uncompressed_bytes": 10 * 1024 * 1024}


def write_tmp(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- KML


def test_kml_reads_nested_folders_and_attributes():
    doc = kml(
        "<Folder><name>Mine</name><Folder><name>Pit 3</name>"
        '<Placemark id="stockpile-7"><name>Stockpile 7</name><description>coal</description>'
        '<ExtendedData><Data name="volume_m3"><value>1520</value></Data>'
        '<SchemaData schemaUrl="#s"><SimpleData name="grade">A</SimpleData></SchemaData></ExtendedData>'
        f"{kml_polygon(square(77.59, 12.97, 0.01))}</Placemark>"
        "</Folder></Folder>"
    )
    ds = parse_kml(doc)

    assert ds.crs.to_epsg() == 4326
    [f] = ds.features
    assert f.source_id == "stockpile-7"
    assert f.geometry_type == "Polygon"
    assert f.properties == {
        "name": "Stockpile 7",
        "description": "coal",
        "folder": "Mine/Pit 3",
        "volume_m3": "1520",
        "grade": "A",
    }


def test_kml_keeps_document_order_and_indexes():
    doc = kml(
        "<Placemark><name>a</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>"
        "<Folder><Placemark><name>b</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark></Folder>"
        "<Placemark><name>c</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>"
    )
    assert [(f.index, f.properties["name"]) for f in parse_kml(doc).features] == [(0, "a"), (1, "b"), (2, "c")]


def test_kml_polygon_with_hole_and_altitude():
    doc = kml(f"<Placemark>{kml_polygon(square(0, 0, 1), holes=[square(0.2, 0.2, 0.2)])}</Placemark>")
    [f] = parse_kml(doc).features
    assert len(f.geometry.interiors) == 1
    assert not f.geometry.has_z  # altitude dropped


def test_kml_multigeometry_becomes_multi_type():
    doc = kml(
        "<Placemark><MultiGeometry>"
        f"{kml_polygon(square(0, 0, 1))}{kml_polygon(square(2, 0, 1))}"
        "</MultiGeometry></Placemark>"
    )
    [f] = parse_kml(doc).features
    assert f.geometry_type == "MultiPolygon"
    assert len(f.geometry.geoms) == 2


def test_kml_unsupported_and_broken_geometries_do_not_fail_the_file():
    doc = kml(
        "<Placemark><name>track</name><gx:Track><when>2024-01-01T00:00:00Z</when>"
        "<gx:coord>77.5 12.9 0</gx:coord></gx:Track></Placemark>"
        "<Placemark><name>no geometry</name></Placemark>"
        "<Placemark><name>bad coords</name><LineString><coordinates>77.5</coordinates></LineString></Placemark>"
        "<Placemark><name>too short</name><Polygon><outerBoundaryIs><LinearRing>"
        "<coordinates>0,0 1,1</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        "<Placemark><name>ok</name><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>"
    )
    features = parse_kml(doc).features

    assert len(features) == 5
    track, empty, bad, short, ok = features
    assert track.geometry is None and track.geometry_type == "gx:Track" and "not supported" in track.note
    assert empty.geometry is None and "no geometry" in empty.note
    assert bad.geometry is None and "could not be parsed" in bad.note
    assert short.geometry is None and "could not be parsed" in short.note
    assert ok.geometry is not None and ok.note is None


@pytest.mark.parametrize(
    "content, message",
    [
        (b"<kml><Document>", "not well-formed"),
        (b"<gpx></gpx>", "expected <kml>"),
        (b"", "not well-formed"),
    ],
)
def test_kml_invalid_documents_raise(content, message):
    with pytest.raises(GeoFileError, match=message):
        parse_kml(content)


def test_kml_rejects_entity_expansion():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]><kml>&b;</kml>'
    )
    with pytest.raises(GeoFileError):
        parse_kml(bomb)


def test_kmz_reads_doc_kml(tmp_path):
    doc = kml("<Placemark><Point><coordinates>77.5,12.9</coordinates></Point></Placemark>")
    path = write_tmp(tmp_path, "a.kmz", make_zip({"files/icon.png": b"\x89PNG", "doc.kml": doc}))
    assert len(read_kmz(path, **LIMITS).features) == 1


def test_kmz_without_kml_is_rejected(tmp_path):
    path = write_tmp(tmp_path, "a.kmz", make_zip({"readme.txt": b"hi"}))
    with pytest.raises(GeoFileError, match="does not contain a .kml"):
        read_kmz(path, **LIMITS)


# --------------------------------------------------------------------- Shapefile


def _polygons(w: shapefile.Writer):
    w.field("name", "C")
    w.field("surveyed", "D")
    w.field("depth", "N", decimal=2)
    w.poly([square(77.59, 12.97, 0.01)])
    w.record("pit", dt.date(2024, 3, 1), 12.5)
    w.null()
    w.record("empty", None, None)


def test_shapefile_with_prj(tmp_path):
    path = write_tmp(tmp_path, "s.zip", shapefile_zip(shapefile.POLYGON, _polygons, prj=esri_prj(4326)))
    ds = read_shapefile_zip(path, **LIMITS)

    assert ds.crs.to_epsg() == 4326
    assert ds.warnings == []
    pit, empty = ds.features
    assert pit.geometry_type == "Polygon"
    assert pit.properties == {"name": "pit", "surveyed": "2024-03-01", "depth": 12.5}
    assert empty.geometry is None and "Null shape" in empty.note


def test_shapefile_projected_prj_is_recognised(tmp_path):
    def write(w):
        w.field("id", "N")
        w.line([[(650_000, 1_435_000), (650_300, 1_435_400)]])
        w.record(1)

    path = write_tmp(tmp_path, "s.zip", shapefile_zip(shapefile.POLYLINE, write, prj=esri_prj(32643)))
    assert read_shapefile_zip(path, **LIMITS).crs.to_epsg() == 32643


def test_shapefile_without_prj_lonlat_assumes_wgs84(tmp_path):
    path = write_tmp(tmp_path, "s.zip", shapefile_zip(shapefile.POLYGON, _polygons))
    ds = read_shapefile_zip(path, **LIMITS)
    assert ds.crs.to_epsg() == 4326
    assert "EPSG:4326 was assumed" in ds.warnings[0]


def test_shapefile_without_prj_projected_coords_has_unknown_crs(tmp_path):
    def write(w):
        w.field("id", "N")
        w.point(650_000, 1_435_000)
        w.record(1)

    path = write_tmp(tmp_path, "s.zip", shapefile_zip(shapefile.POINT, write))
    ds = read_shapefile_zip(path, **LIMITS)
    assert ds.crs is None
    assert "CRS is unknown" in ds.warnings[0]


def test_shapefile_in_subfolder_with_macos_junk(tmp_path):
    inner = shapefile_zip(shapefile.POLYGON, _polygons, prj=esri_prj(4326))
    members = {}
    with zipfile.ZipFile(io.BytesIO(inner)) as zf:
        for name in zf.namelist():
            members[f"export/{name}"] = zf.read(name)
            members[f"__MACOSX/export/._{name}"] = b"junk"
    path = write_tmp(tmp_path, "s.zip", make_zip(members))
    assert len(read_shapefile_zip(path, **LIMITS).features) == 2


@pytest.mark.parametrize(
    "build, message",
    [
        (lambda: make_zip({"notes.txt": b"x"}), "does not contain a .shp"),
        (lambda: shapefile_zip(shapefile.POLYGON, _polygons, omit=["layer.dbf"]), "missing required component"),
        (
            lambda: make_zip(
                {
                    **{n.replace("layer", "a"): b"" for n in ("layer.shp", "layer.shx", "layer.dbf")},
                    **{n.replace("layer", "b"): b"" for n in ("layer.shp", "layer.shx", "layer.dbf")},
                }
            ),
            "contains 2 shapefiles",
        ),
        (lambda: make_zip({"../evil.shp": b""}), "unsafe path"),
        (lambda: b"definitely not a zip", "not a valid zip"),
        (
            lambda: shapefile_zip(
                shapefile.POLYGON, _polygons, extra={"layer.shp": b"\x00" * 10}
            ),
            "Could not read shapefile",
        ),
    ],
)
def test_invalid_shapefile_archives(tmp_path, build, message):
    path = write_tmp(tmp_path, "s.zip", build())
    with pytest.raises(GeoFileError, match=message):
        read_shapefile_zip(path, **LIMITS)


def test_archive_size_limit(tmp_path):
    path = write_tmp(tmp_path, "s.zip", make_zip({"big.shp": b"\x00" * 5000}))
    with pytest.raises(GeoFileError, match="byte limit"):
        read_shapefile_zip(path, max_members=10, max_uncompressed_bytes=1000)
