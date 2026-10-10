"""Format-agnostic entry point for reading uploaded geospatial files."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.geo.kml_reader import read_kml, read_kmz
from app.geo.shapefile_reader import read_shapefile_zip
from app.geo.types import GeoFileError, ParsedDataset, ParsedFeature
from app.models import FileFormat

__all__ = ["GeoFileError", "ParsedDataset", "ParsedFeature", "read_dataset"]


def read_dataset(path: Path, file_format: FileFormat, settings: Settings) -> ParsedDataset:
    limits = {
        "max_members": settings.max_archive_members,
        "max_uncompressed_bytes": settings.max_uncompressed_bytes,
    }
    if file_format is FileFormat.SHAPEFILE:
        return read_shapefile_zip(path, **limits)
    if file_format is FileFormat.KMZ:
        return read_kmz(path, **limits)
    return read_kml(path)
