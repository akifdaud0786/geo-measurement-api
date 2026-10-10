"""Saving uploads to disk, with size enforcement while streaming."""

from __future__ import annotations

import re
import shutil
from pathlib import Path, PurePath

from fastapi import UploadFile

from app.models import FileFormat

CHUNK_SIZE = 1024 * 1024

EXTENSION_FORMATS = {
    ".zip": FileFormat.SHAPEFILE,
    ".kml": FileFormat.KML,
    ".kmz": FileFormat.KMZ,
}


class UploadTooLarge(Exception):
    pass


def detect_format(filename: str) -> FileFormat | None:
    return EXTENSION_FORMATS.get(PurePath(filename).suffix.lower())


def safe_filename(filename: str) -> str:
    """Drop any directory part a client may send and keep a conservative character set."""
    name = PurePath(filename.replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "upload"


async def save_upload(upload: UploadFile, dest_dir: Path, max_bytes: int) -> tuple[Path, int]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(upload.filename or "upload")
    written = 0
    try:
        with dest.open("wb") as fh:
            while chunk := await upload.read(CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise UploadTooLarge
                fh.write(chunk)
    except BaseException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return dest, written


def delete_stored(path: str | Path) -> None:
    shutil.rmtree(Path(path).parent, ignore_errors=True)
