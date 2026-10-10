"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    # Hard cap on the uploaded (possibly compressed) payload.
    max_upload_bytes: int = 50 * 1024 * 1024
    # Cap on the total uncompressed size of a .zip/.kmz - protects against zip bombs.
    max_uncompressed_bytes: int = 200 * 1024 * 1024
    # Cap on the number of entries in an archive.
    max_archive_members: int = 1000

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("GEO_DATA_DIR", "data")).resolve()
        return cls(
            data_dir=data_dir,
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{(data_dir / 'app.db').as_posix()}"),
            max_upload_bytes=_int_env("GEO_MAX_UPLOAD_BYTES", cls.max_upload_bytes),
            max_uncompressed_bytes=_int_env("GEO_MAX_UNCOMPRESSED_BYTES", cls.max_uncompressed_bytes),
            max_archive_members=_int_env("GEO_MAX_ARCHIVE_MEMBERS", cls.max_archive_members),
        )
