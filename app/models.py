"""ORM models: one row per uploaded file, one row per feature found in it."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class FileStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileFormat(str, enum.Enum):
    SHAPEFILE = "shapefile"
    KML = "kml"
    KMZ = "kmz"


class MeasurementStatus(str, enum.Enum):
    MEASURED = "measured"
    NOT_APPLICABLE = "not_applicable"  # e.g. points: nothing to measure
    UNSUPPORTED = "unsupported"  # geometry type we do not measure
    SKIPPED = "skipped"  # empty / missing geometry
    ERROR = "error"  # measurement attempted but failed


class UploadedFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[FileFormat] = mapped_column(Enum(FileFormat, native_enum=False))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus, native_enum=False), default=FileStatus.PENDING
    )
    crs: Mapped[str | None] = mapped_column(String(64))
    crs_name: Mapped[str | None] = mapped_column(String(255))
    feature_count: Mapped[int | None] = mapped_column(Integer)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        order_by="Feature.index",
        lazy="raise",
    )


class Feature(Base):
    __tablename__ = "features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(String(255))
    geometry_type: Mapped[str | None] = mapped_column(String(64))
    # GeoJSON geometry, in the file's own CRS.
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    measurement_status: Mapped[MeasurementStatus] = mapped_column(
        Enum(MeasurementStatus, native_enum=False)
    )
    area_m2: Mapped[float | None] = mapped_column(Float)
    perimeter_m: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    measurement_crs: Mapped[str | None] = mapped_column(String(255))
    geodesic_area_m2: Mapped[float | None] = mapped_column(Float)
    geodesic_length_m: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    file: Mapped[UploadedFile] = relationship(back_populates="features")
