"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from sqlalchemy import update

from app.api.files import router as files_router
from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.models import FileStatus, UploadedFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _fail_interrupted_jobs(session_factory) -> None:
    """Background tasks live in-process; anything unfinished at startup was lost in a restart."""
    with session_factory() as db:
        db.execute(
            update(UploadedFile)
            .where(UploadedFile.status.in_([FileStatus.PENDING, FileStatus.PROCESSING]))
            .values(
                status=FileStatus.FAILED,
                error="Processing was interrupted by a server restart; please upload again.",
                processed_at=datetime.now(timezone.utc),
            )
        )
        db.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(engine)
        _fail_interrupted_jobs(session_factory)
        yield
        engine.dispose()

    app = FastAPI(
        title="Geospatial File Measurement API",
        version="1.0.0",
        description=(
            "Upload a Shapefile (zipped), KML or KMZ. Features are extracted and polygons / "
            "lines are measured in metres after projecting to a suitable local CRS."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.include_router(files_router)

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app


app = create_app()
