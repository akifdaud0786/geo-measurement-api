"""Defensive helpers for reading .zip / .kmz uploads.

Archives are never extracted to disk: members are read straight into memory, which
rules out path-traversal ("zip slip") writes. Size and member-count limits guard
against zip bombs.
"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from app.geo.types import GeoFileError


def _is_junk(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return any(p == "__MACOSX" or p.startswith("._") for p in parts)


def open_archive(
    path: Path, *, max_members: int, max_uncompressed_bytes: int
) -> tuple[zipfile.ZipFile, list[zipfile.ZipInfo]]:
    """Open an archive and return it together with its usable (non-junk, non-directory) members."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise GeoFileError("File is not a valid zip archive.") from exc

    try:
        infos = zf.infolist()
        if len(infos) > max_members:
            raise GeoFileError(f"Archive has too many entries ({len(infos)} > {max_members}).")
        total = sum(i.file_size for i in infos)
        if total > max_uncompressed_bytes:
            raise GeoFileError(
                f"Archive expands to {total} bytes, above the {max_uncompressed_bytes} byte limit."
            )
        members = []
        for info in infos:
            name = info.filename.replace("\\", "/")
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise GeoFileError(f"Archive contains an unsafe path: {info.filename!r}.")
            if info.is_dir() or _is_junk(name):
                continue
            members.append(info)
        return zf, members
    except Exception:
        zf.close()
        raise


def find_by_suffix(members: list[zipfile.ZipInfo], suffix: str) -> list[zipfile.ZipInfo]:
    suffix = suffix.lower()
    return [m for m in members if m.filename.lower().endswith(suffix)]
