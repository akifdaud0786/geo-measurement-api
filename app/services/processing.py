"""Background processing of an uploaded file: parse -> measure -> persist."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import mapping
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.geo import GeoFileError, read_dataset
from app.geo.crs import describe_crs
from app.geo.measure import measure
from app.models import Feature, FileStatus, UploadedFile

logger = logging.getLogger(__name__)


def process_file(file_id: str, session_factory: sessionmaker[Session], settings: Settings) -> None:
    with session_factory() as db:
        record = db.get(UploadedFile, file_id)
        if record is None:  # deleted before processing started
            return
        record.status = FileStatus.PROCESSING
        db.commit()

        try:
            dataset = read_dataset(Path(record.storage_path), record.file_format, settings)
            for parsed in dataset.features:
                result = measure(parsed.geometry, dataset.crs)
                notes = " ".join(n for n in (parsed.note, result.note) if n) or None
                db.add(
                    Feature(
                        file_id=record.id,
                        index=parsed.index,
                        source_id=parsed.source_id,
                        geometry_type=parsed.geometry_type,
                        geometry=mapping(parsed.geometry) if parsed.geometry is not None else None,
                        properties=parsed.properties,
                        measurement_status=result.status,
                        area_m2=result.area_m2,
                        perimeter_m=result.perimeter_m,
                        length_m=result.length_m,
                        measurement_crs=result.measurement_crs,
                        geodesic_area_m2=result.geodesic_area_m2,
                        geodesic_length_m=result.geodesic_length_m,
                        note=notes,
                    )
                )
            record.crs, record.crs_name = describe_crs(dataset.crs)
            record.feature_count = len(dataset.features)
            record.warnings = dataset.warnings
            record.status = FileStatus.COMPLETED
        except GeoFileError as exc:
            _mark_failed(db, file_id, str(exc))
            return
        except Exception:
            logger.exception("Unexpected error while processing file %s", file_id)
            _mark_failed(db, file_id, "Unexpected internal error while processing the file.")
            return

        record.processed_at = datetime.now(timezone.utc)
        db.commit()


def _mark_failed(db: Session, file_id: str, error: str) -> None:
    db.rollback()  # discard any features added before the failure
    record = db.get(UploadedFile, file_id)
    if record is None:
        return
    record.status = FileStatus.FAILED
    record.error = error
    record.processed_at = datetime.now(timezone.utc)
    db.commit()
