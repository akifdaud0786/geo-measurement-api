"""API request/response models."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import FileFormat, FileStatus, MeasurementStatus


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_format: FileFormat
    size_bytes: int
    status: FileStatus
    feature_count: int | None = Field(None, description="Set once processing has completed.")
    crs: str | None = Field(None, description='Authority code of the source CRS, e.g. "EPSG:4326".')
    crs_name: str | None = None
    warnings: list[str] = []
    error: str | None = Field(None, description="Why processing failed (status FAILED only).")
    created_at: datetime
    processed_at: datetime | None = None

    @field_validator("created_at", "processed_at")
    @classmethod
    def _assume_utc(cls, value: datetime | None) -> datetime | None:
        # SQLite drops tzinfo; every timestamp is stored in UTC.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class FileList(BaseModel):
    items: list[FileInfo]
    total: int
    limit: int
    offset: int


class FeatureMeasurement(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    feature_index: int = Field(validation_alias="index")
    source_id: str | None
    geometry_type: str | None
    status: MeasurementStatus = Field(validation_alias="measurement_status")
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = Field(
        None, description="Projected CRS the planar measurement was computed in."
    )
    geodesic_area_m2: float | None = Field(
        None, description="Area on the WGS84 ellipsoid; an independent cross-check."
    )
    geodesic_length_m: float | None = None
    note: str | None = None


class MeasurementSummary(BaseModel):
    feature_count: int
    measured: int
    not_applicable: int
    unsupported: int
    skipped: int
    errors: int
    total_area_m2: float
    total_length_m: float


class MeasurementsResponse(BaseModel):
    file_id: str
    crs: str | None
    units: dict[str, str] = {"area": "m2", "length": "m"}
    summary: MeasurementSummary
    limit: int
    offset: int
    measurements: list[FeatureMeasurement]


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    id: int
    geometry: dict[str, Any] | None
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    crs: str | None = Field(None, description="CRS of the coordinates (the file's own CRS).")
    total: int
    limit: int
    offset: int
    features: list[GeoJSONFeature]
