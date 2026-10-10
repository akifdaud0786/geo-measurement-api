"""Builders for in-memory test fixtures (zipped shapefiles, KML documents)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterable

import shapefile
from pyproj import CRS


def esri_prj(epsg: int) -> str:
    return CRS.from_epsg(epsg).to_wkt("WKT1_ESRI")


def shapefile_zip(
    shape_type: int,
    write: Callable[[shapefile.Writer], None],
    *,
    prj: str | None = None,
    stem: str = "layer",
    extra: dict[str, bytes] | None = None,
    omit: Iterable[str] = (),
) -> bytes:
    """Build a zipped shapefile. `write` receives a Writer to add fields/shapes/records."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    writer = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shape_type)
    write(writer)
    writer.close()

    members = {f"{stem}.shp": shp.getvalue(), f"{stem}.shx": shx.getvalue(), f"{stem}.dbf": dbf.getvalue()}
    if prj is not None:
        members[f"{stem}.prj"] = prj.encode()
    members.update(extra or {})
    for name in omit:
        members.pop(name)
    return make_zip(members)


def make_zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def kml(body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2">'
        f"<Document>{body}</Document></kml>"
    ).encode()


def ring(coords: list[tuple[float, float]]) -> str:
    return " ".join(f"{x},{y},0" for x, y in coords)


def square(lon: float, lat: float, size: float) -> list[tuple[float, float]]:
    """Closed counter-clockwise square ring with its south-west corner at (lon, lat)."""
    return [(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size), (lon, lat)]


def kml_polygon(coords: list[tuple[float, float]], holes: Iterable[list[tuple[float, float]]] = ()) -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{ring(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{ring(coords)}</coordinates>"
        f"</LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )
