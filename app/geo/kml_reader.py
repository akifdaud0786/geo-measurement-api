"""Read KML / KMZ into a ParsedDataset.

KML coordinates are always WGS84 longitude/latitude (OGC KML 2.2, section 6.2), so the
dataset CRS is fixed to EPSG:4326. Altitudes are dropped: measurements are planimetric.

Parsing uses defusedxml so entity-expansion ("billion laughs") and external-entity
payloads are rejected.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring
from pyproj import CRS
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry

from app.geo.archive import find_by_suffix, open_archive
from app.geo.types import GeoFileError, ParsedDataset, ParsedFeature

GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry", "Track", "MultiTrack", "Model"}
UNSUPPORTED_TAGS = {"Track": "gx:Track", "MultiTrack": "gx:MultiTrack", "Model": "Model"}


class _UnsupportedGeometry(Exception):
    def __init__(self, type_name: str):
        super().__init__(type_name)
        self.type_name = type_name


def _local(tag: str) -> str:
    """Strip the XML namespace: '{http://www.opengis.net/kml/2.2}Point' -> 'Point'."""
    return tag.rsplit("}", 1)[-1]


def _child(elem: Element, name: str) -> Element | None:
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _children(elem: Element, name: str) -> list[Element]:
    return [c for c in elem if _local(c.tag) == name]


def _text(elem: Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    return elem.text.strip()


def _coords(elem: Element) -> list[tuple[float, float]]:
    node = _child(elem, "coordinates")
    raw = _text(node)
    if not raw:
        raise ValueError(f"<{_local(elem.tag)}> has no coordinates")
    out = []
    for token in raw.split():
        parts = token.split(",")
        if len(parts) < 2:
            raise ValueError(f"Malformed coordinate tuple {token!r}")
        out.append((float(parts[0]), float(parts[1])))
    return out


def _ring(elem: Element | None) -> list[tuple[float, float]]:
    ring = _child(elem, "LinearRing") if elem is not None else None
    if ring is None:
        raise ValueError("Polygon boundary is missing its <LinearRing>")
    return _coords(ring)


def _parse_geometry(elem: Element) -> BaseGeometry:
    tag = _local(elem.tag)
    if tag in UNSUPPORTED_TAGS:
        raise _UnsupportedGeometry(UNSUPPORTED_TAGS[tag])
    if tag == "Point":
        return Point(_coords(elem)[0])
    if tag in ("LineString", "LinearRing"):
        # A bare LinearRing is a closed line, not an area.
        return LineString(_coords(elem))
    if tag == "Polygon":
        outer = _ring(_child(elem, "outerBoundaryIs"))
        holes = [_ring(b) for b in _children(elem, "innerBoundaryIs")]
        return Polygon(outer, holes)
    if tag == "MultiGeometry":
        parts: list[BaseGeometry] = []
        for c in elem:
            if _local(c.tag) in GEOMETRY_TAGS:
                g = _parse_geometry(c)
                parts.extend(g.geoms if hasattr(g, "geoms") else [g])
        return _combine(parts)
    raise _UnsupportedGeometry(tag)


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    types = {p.geom_type for p in parts}
    if types == {"Point"}:
        return MultiPoint(parts)
    if types == {"LineString"}:
        return MultiLineString(parts)
    if types == {"Polygon"}:
        return MultiPolygon(parts)
    return GeometryCollection(parts)


def _properties(placemark: Element, folders: tuple[str, ...]) -> dict[str, str | None]:
    props: dict[str, str | None] = {}
    for key in ("name", "description"):
        value = _text(_child(placemark, key))
        if value is not None:
            props[key] = value
    if folders:
        props["folder"] = "/".join(folders)
    extended = _child(placemark, "ExtendedData")
    if extended is not None:
        for data in _children(extended, "Data"):
            if data.get("name"):
                props[data.get("name")] = _text(_child(data, "value"))
        for schema_data in _children(extended, "SchemaData"):
            for simple in _children(schema_data, "SimpleData"):
                if simple.get("name"):
                    props[simple.get("name")] = _text(simple)
    return props


def _iter_placemarks(root: Element):
    """Yield (placemark, folder_path) in document order."""
    stack: list[tuple[Element, tuple[str, ...]]] = [(root, ())]
    while stack:
        elem, folders = stack.pop()
        tag = _local(elem.tag)
        if tag == "Placemark":
            yield elem, folders
            continue
        if tag == "Folder":
            name = _text(_child(elem, "name"))
            folders = folders + (name,) if name else folders
        stack.extend((c, folders) for c in reversed(list(elem)))


def parse_kml(content: bytes) -> ParsedDataset:
    try:
        root = fromstring(content, forbid_dtd=True)
    except (ParseError, DefusedXmlException) as exc:
        raise GeoFileError(f"KML is not well-formed XML: {exc}") from exc
    if _local(root.tag) != "kml":
        raise GeoFileError(f"Root element is <{_local(root.tag)}>, expected <kml>.")

    features: list[ParsedFeature] = []
    for index, (placemark, folders) in enumerate(_iter_placemarks(root)):
        feature = ParsedFeature(
            index=index,
            source_id=placemark.get("id"),
            geometry_type=None,
            geometry=None,
            properties=_properties(placemark, folders),
        )
        geom_elem = next((c for c in placemark if _local(c.tag) in GEOMETRY_TAGS), None)
        if geom_elem is None:
            feature.note = "Placemark has no geometry."
        else:
            try:
                feature.geometry = _parse_geometry(geom_elem)
                feature.geometry_type = feature.geometry.geom_type
            except _UnsupportedGeometry as exc:
                feature.geometry_type = exc.type_name
                feature.note = f"Geometry type {exc.type_name} is not supported."
            except (ValueError, TypeError, IndexError, ShapelyError) as exc:
                feature.geometry_type = _local(geom_elem.tag)
                feature.note = f"Geometry could not be parsed: {exc}"
        features.append(feature)

    warnings = [] if features else ["KML contains no Placemarks."]
    return ParsedDataset(crs=CRS.from_epsg(4326), features=features, warnings=warnings)


def read_kml(path: Path) -> ParsedDataset:
    return parse_kml(path.read_bytes())


def read_kmz(path: Path, *, max_members: int, max_uncompressed_bytes: int) -> ParsedDataset:
    zf, members = open_archive(
        path, max_members=max_members, max_uncompressed_bytes=max_uncompressed_bytes
    )
    with zf:
        kmls = find_by_suffix(members, ".kml")
        if not kmls:
            raise GeoFileError("KMZ archive does not contain a .kml file.")
        # Convention: the main document is doc.kml at the root; otherwise the shallowest .kml.
        main = next((m for m in kmls if m.filename.lower() == "doc.kml"), None) or min(
            kmls, key=lambda m: len(PurePosixPath(m.filename).parts)
        )
        return parse_kml(zf.read(main))
