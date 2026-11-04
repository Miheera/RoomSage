"""Legacy single-process store for design specifications and plans.

Preview jobs and rendered output are owned by Person A's PostgreSQL-backed service.
"""

import os
import re
import threading
from pathlib import Path

from app.core.errors import NotFoundError, ValidationFailed
from app.services.design_models import Design

_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


class DesignStore:
    def __init__(self, directory: Path):
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, design_id: str) -> Path:
        if not _SAFE_ID.match(design_id):
            raise ValidationFailed("Malformed design id.")
        return self._directory / f"{design_id}.json"

    def save(self, design: Design) -> None:
        design.touch()
        path = self._path(design.design_id)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            temporary.write_text(design.model_dump_json(indent=1), encoding="utf-8")
            os.replace(temporary, path)

    def get(self, design_id: str) -> Design:
        path = self._path(design_id)
        if not path.exists():
            raise NotFoundError(f"Design {design_id} does not exist.")
        with self._lock:
            return Design.model_validate_json(path.read_text(encoding="utf-8"))
