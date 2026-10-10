"""Shared types for the geospatial readers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyproj import CRS
from shapely.geometry.base import BaseGeometry


class GeoFileError(Exception):
    """A problem with the uploaded file itself. The message is safe to show to API clients."""


@dataclass
class ParsedFeature:
    index: int
    source_id: str | None
    # Type as declared by the source (e.g. "Polygon", "gx:Track"), even if we could not parse it.
    geometry_type: str | None
    geometry: BaseGeometry | None
    properties: dict[str, Any] = field(default_factory=dict)
    # Feature-level parse problem; the feature is still kept so the client can see it.
    note: str | None = None


@dataclass
class ParsedDataset:
    crs: CRS | None
    features: list[ParsedFeature]
    warnings: list[str] = field(default_factory=list)
