"""Read a zipped ESRI Shapefile into a ParsedDataset."""

from __future__ import annotations

import codecs
import io
import zipfile
from pathlib import Path, PurePosixPath

import shapefile  # pyshp
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape as to_shapely

from app.geo.archive import find_by_suffix, open_archive
from app.geo.properties import json_safe_properties
from app.geo.types import GeoFileError, ParsedDataset, ParsedFeature

REQUIRED_PARTS = (".shp", ".shx", ".dbf")


def _stem(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    return name[: -len(PurePosixPath(name).suffix)].lower()


def locate_shapefile(members: list[zipfile.ZipInfo]) -> dict[str, zipfile.ZipInfo]:
    """Return the component files (.shp/.shx/.dbf/.prj/.cpg) of the single shapefile in the archive."""
    shps = find_by_suffix(members, ".shp")
    if not shps:
        raise GeoFileError("Zip archive does not contain a .shp file.")
    if len(shps) > 1:
        names = ", ".join(sorted(s.filename for s in shps))
        raise GeoFileError(f"Zip archive contains {len(shps)} shapefiles ({names}); upload one per request.")

    stem = _stem(shps[0])
    parts = {
        PurePosixPath(m.filename).suffix.lower(): m for m in members if _stem(m) == stem
    }
    missing = [ext for ext in REQUIRED_PARTS if ext not in parts]
    if missing:
        raise GeoFileError(f"Shapefile is missing required component(s): {', '.join(missing)}.")
    return parts


def validate_shapefile_zip(path: Path, *, max_members: int, max_uncompressed_bytes: int) -> None:
    """Cheap structural check, run synchronously at upload time (reads only the zip directory)."""
    zf, members = open_archive(
        path, max_members=max_members, max_uncompressed_bytes=max_uncompressed_bytes
    )
    with zf:
        locate_shapefile(members)


def _read_crs(prj_text: str, warnings: list[str]) -> CRS | None:
    try:
        return CRS.from_wkt(prj_text)
    except CRSError:
        try:
            return CRS.from_user_input(prj_text.strip())
        except CRSError:
            warnings.append("Could not interpret the .prj file; CRS is unknown.")
            return None


def _looks_geographic(bbox: list[float]) -> bool:
    minx, miny, maxx, maxy = bbox[:4]
    return -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90


def read_shapefile_zip(
    path: Path, *, max_members: int, max_uncompressed_bytes: int
) -> ParsedDataset:
    zf, members = open_archive(
        path, max_members=max_members, max_uncompressed_bytes=max_uncompressed_bytes
    )
    warnings: list[str] = []
    with zf:
        parts = locate_shapefile(members)
        data = {ext: zf.read(info) for ext, info in parts.items()}

    encoding = "utf-8"
    if ".cpg" in data:
        declared = data[".cpg"].decode("ascii", errors="ignore").strip()
        try:
            codecs.lookup(declared)
            encoding = declared
        except LookupError:
            warnings.append(f"Unknown .cpg encoding {declared!r}; attributes decoded as UTF-8.")

    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(data[".shp"]),
            shx=io.BytesIO(data[".shx"]),
            dbf=io.BytesIO(data[".dbf"]),
            encoding=encoding,
            encodingErrors="replace",
        )
    except Exception as exc:  # pyshp raises ShapefileException, struct.error, ... on corrupt input
        raise GeoFileError(f"Could not read shapefile: {exc}") from exc

    crs: CRS | None = None
    if ".prj" in data:
        crs = _read_crs(data[".prj"].decode("utf-8", errors="replace"), warnings)
    else:
        if reader.numRecords and _looks_geographic(list(reader.bbox)):
            crs = CRS.from_epsg(4326)
            warnings.append(
                "No .prj file; coordinates look like longitude/latitude so EPSG:4326 was assumed."
            )
        else:
            warnings.append("No .prj file; CRS is unknown and features cannot be measured.")

    try:
        with reader:
            features = _read_features(reader)
    except GeoFileError:
        raise
    except Exception as exc:
        raise GeoFileError(f"Shapefile is corrupt or truncated: {exc}") from exc

    return ParsedDataset(crs=crs, features=features, warnings=warnings)


def _read_features(reader: shapefile.Reader) -> list[ParsedFeature]:
    features: list[ParsedFeature] = []
    for index, shape_record in enumerate(reader.iterShapeRecords()):
        shp = shape_record.shape
        feature = ParsedFeature(
            index=index,
            source_id=str(index),  # shapefile record number (0-based)
            geometry_type=None,
            geometry=None,
            properties=json_safe_properties(shape_record.record.as_dict()),
        )
        if shp.shapeType == shapefile.NULL:
            feature.note = "Null shape (record has no geometry)."
        else:
            feature.geometry_type = shp.shapeTypeName  # fallback label, e.g. "MULTIPATCH"
            try:
                geo = shp.__geo_interface__
                feature.geometry_type = geo.get("type", feature.geometry_type)
                feature.geometry = to_shapely(geo)
            except Exception as exc:  # malformed rings, too few points, ...
                feature.note = f"Geometry could not be parsed: {exc}"
        features.append(feature)
    return features
