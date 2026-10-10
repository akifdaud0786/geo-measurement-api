"""/api/files endpoints."""

import uuid
from pathlib import PurePath

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.db import get_db
from app.geo import GeoFileError
from app.geo.archive import open_archive
from app.geo.shapefile_reader import validate_shapefile_zip
from app.models import Feature, FileFormat, FileStatus, MeasurementStatus, UploadedFile
from app.schemas import (
    FeatureMeasurement,
    FileInfo,
    FileList,
    GeoJSONFeature,
    GeoJSONFeatureCollection,
    MeasurementsResponse,
    MeasurementSummary,
)
from app.services.processing import process_file
from app.services.storage import UploadTooLarge, delete_stored, detect_format, save_upload

router = APIRouter(prefix="/api/files", tags=["files"])

Limit = Query(100, ge=1, le=1000)
Offset = Query(0, ge=0)


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found.")
    return record


def _require_completed(record: UploadedFile) -> None:
    if record.status is FileStatus.COMPLETED:
        return
    if record.status is FileStatus.FAILED:
        message = f"File processing failed: {record.error}"
    else:
        message = f"File is still being processed (status {record.status.value}); retry shortly."
    raise HTTPException(status.HTTP_409_CONFLICT, message)


def _validate_structure(path, file_format: FileFormat, settings: Settings) -> None:
    """Fast checks that only read the zip directory, so obvious mistakes fail the upload itself."""
    limits = {
        "max_members": settings.max_archive_members,
        "max_uncompressed_bytes": settings.max_uncompressed_bytes,
    }
    if file_format is FileFormat.SHAPEFILE:
        validate_shapefile_zip(path, **limits)
    elif file_format is FileFormat.KMZ:
        zf, _ = open_archive(path, **limits)
        zf.close()


@router.post(
    "/",
    response_model=FileInfo,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a Shapefile (.zip), KML or KMZ for processing",
    responses={
        400: {"description": "Empty or structurally invalid file"},
        413: {"description": "File larger than the upload limit"},
        415: {"description": "Unsupported file extension"},
    },
)
async def upload_file(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="A .zip containing a Shapefile, or a .kml / .kmz"),
    db: Session = Depends(get_db),
) -> UploadedFile:
    settings = _settings(request)
    original_name = PurePath((file.filename or "").replace("\\", "/")).name
    file_format = detect_format(original_name)
    if file_format is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Unsupported file type; upload a .zip containing a Shapefile, a .kml or a .kmz.",
        )

    file_id = uuid.uuid4().hex
    try:
        path, size = await save_upload(file, settings.upload_dir / file_id, settings.max_upload_bytes)
    except UploadTooLarge:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {settings.max_upload_bytes} byte upload limit.",
        )

    try:
        if size == 0:
            raise GeoFileError("Uploaded file is empty.")
        await run_in_threadpool(_validate_structure, path, file_format, settings)
    except GeoFileError as exc:
        delete_stored(path)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    record = UploadedFile(
        id=file_id,
        filename=original_name,
        file_format=file_format,
        size_bytes=size,
        storage_path=str(path),
        status=FileStatus.PENDING,
        warnings=[],
    )
    db.add(record)
    db.commit()

    background_tasks.add_task(process_file, file_id, request.app.state.session_factory, settings)
    response.headers["Location"] = f"/api/files/{file_id}/"
    return record


@router.get("/", response_model=FileList, summary="List uploaded files, newest first")
def list_files(limit: int = Limit, offset: int = Offset, db: Session = Depends(get_db)) -> FileList:
    total = db.scalar(select(func.count()).select_from(UploadedFile)) or 0
    rows = db.scalars(
        select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return FileList(
        items=[FileInfo.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{file_id}/", response_model=FileInfo, summary="File information and processing status")
def get_file(file_id: str, db: Session = Depends(get_db)) -> UploadedFile:
    return _get_file_or_404(db, file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Per-feature measurements (area in m², length in m)",
    responses={409: {"description": "File is still processing, or processing failed"}},
)
def get_measurements(
    file_id: str, limit: int = Limit, offset: int = Offset, db: Session = Depends(get_db)
) -> MeasurementsResponse:
    record = _get_file_or_404(db, file_id)
    _require_completed(record)

    def count(s: MeasurementStatus):
        return func.coalesce(func.sum(case((Feature.measurement_status == s, 1), else_=0)), 0)

    totals = db.execute(
        select(
            func.count(Feature.id),
            count(MeasurementStatus.MEASURED),
            count(MeasurementStatus.NOT_APPLICABLE),
            count(MeasurementStatus.UNSUPPORTED),
            count(MeasurementStatus.SKIPPED),
            count(MeasurementStatus.ERROR),
            func.coalesce(func.sum(Feature.area_m2), 0.0),
            func.coalesce(func.sum(Feature.length_m), 0.0),
        ).where(Feature.file_id == file_id)
    ).one()

    features = db.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.index)
        .limit(limit)
        .offset(offset)
    ).all()

    return MeasurementsResponse(
        file_id=file_id,
        crs=record.crs,
        summary=MeasurementSummary(
            feature_count=totals[0],
            measured=totals[1],
            not_applicable=totals[2],
            unsupported=totals[3],
            skipped=totals[4],
            errors=totals[5],
            total_area_m2=totals[6],
            total_length_m=totals[7],
        ),
        limit=limit,
        offset=offset,
        measurements=[FeatureMeasurement.model_validate(f) for f in features],
    )


@router.get(
    "/{file_id}/features/",
    response_model=GeoJSONFeatureCollection,
    summary="Parsed features as GeoJSON (geometry in the file's own CRS)",
    responses={409: {"description": "File is still processing, or processing failed"}},
)
def get_features(
    file_id: str, limit: int = Limit, offset: int = Offset, db: Session = Depends(get_db)
) -> GeoJSONFeatureCollection:
    record = _get_file_or_404(db, file_id)
    _require_completed(record)
    rows = db.scalars(
        select(Feature)
        .where(Feature.file_id == file_id)
        .order_by(Feature.index)
        .limit(limit)
        .offset(offset)
    ).all()
    return GeoJSONFeatureCollection(
        crs=record.crs,
        total=record.feature_count or 0,
        limit=limit,
        offset=offset,
        features=[
            GeoJSONFeature(
                id=f.index,
                geometry=f.geometry,
                properties={
                    **f.properties,
                    "_source_id": f.source_id,
                    "_geometry_type": f.geometry_type,
                },
            )
            for f in rows
        ],
    )


@router.delete(
    "/{file_id}/",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a file and its results",
    responses={409: {"description": "File is still being processed"}},
)
def delete_file(file_id: str, db: Session = Depends(get_db)) -> Response:
    record = _get_file_or_404(db, file_id)
    if record.status in (FileStatus.PENDING, FileStatus.PROCESSING):
        raise HTTPException(status.HTTP_409_CONFLICT, "File is still being processed.")
    storage_path = record.storage_path
    db.delete(record)
    db.commit()
    delete_stored(storage_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
