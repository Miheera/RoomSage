import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, File, UploadFile, status

from app.api.deps import Container, get_container
from app.api.schemas.design import AnalysisResponse, UploadResponse
from app.core.errors import NotFoundError, ValidationFailed

router = APIRouter(prefix="/uploads", tags=["uploads"])
Deps = Annotated[Container, Depends(get_container)]

_SIGNATURES = {
    ".jpg": lambda d: d.startswith(b"\xff\xd8\xff"),
    ".jpeg": lambda d: d.startswith(b"\xff\xd8\xff"),
    ".png": lambda d: d.startswith(b"\x89PNG\r\n\x1a\n"),
    ".webp": lambda d: len(d) >= 12 and d[:4] == b"RIFF" and d[8:12] == b"WEBP",
}
_UPLOAD_ID = re.compile(r"^[a-f0-9]{32}\.(jpg|jpeg|png|webp)$")


def resolve_upload(deps: Container, upload_id: str) -> Path:
    if not _UPLOAD_ID.match(upload_id):
        raise ValidationFailed("Malformed upload id.")
    path = Path(deps.settings.uploads_dir) / upload_id
    if not path.exists():
        raise NotFoundError(f"Upload {upload_id} does not exist.")
    return path


@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
def upload_photo(deps: Deps, file: Annotated[UploadFile, File()]) -> UploadResponse:
    """Store one room photo for 2D previews. 3D capture photos go to Person A's API."""
    suffix = Path(file.filename or "").suffix.lower()
    check = _SIGNATURES.get(suffix)
    if check is None:
        raise ValidationFailed("Upload a JPEG, PNG or WebP image.")
    data = file.file.read(deps.settings.max_upload_bytes + 1)
    if len(data) > deps.settings.max_upload_bytes:
        raise ValidationFailed("The image is too large.")
    if not check(data[:12]):
        raise ValidationFailed("File contents do not match the image extension.")
    upload_id = f"{uuid4().hex}{suffix}"
    (Path(deps.settings.uploads_dir) / upload_id).write_bytes(data)
    return UploadResponse(upload_id=upload_id, filename=file.filename)


@router.post("/{upload_id}/analysis", response_model=AnalysisResponse)
def analyze_photo(upload_id: str, deps: Deps) -> AnalysisResponse:
    """Detect existing furniture classes. Needs REDESIGN_MODE=diffusion; empty in mock mode."""
    objects = deps.renderer.analyze(resolve_upload(deps, upload_id))
    note = None if deps.settings.redesign_mode == "diffusion" else "Mock mode does not detect objects."
    return AnalysisResponse(upload_id=upload_id, objects=objects, note=note)
