import logging
import uuid
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Project
from schemas import PhotoQualityFeedback, PhotoUploadResponse, ProjectCreate, ProjectRead
from services.image_quality_service import image_quality_analyzer
from services.s3_service import delete_object, upload_fileobj

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])

_ALLOWED_IMAGE_TYPES = {
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".webp": (
        "image/webp",
        lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    ),
}


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Project:
    project = Project(name=payload.name.strip(), room_type=payload.room_type)
    if not project.name:
        raise HTTPException(status_code=422, detail="Project name cannot be blank.")
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/photos", response_model=PhotoUploadResponse)
def upload_project_photos(
    project_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    files: Annotated[list[UploadFile], File(...)],
) -> PhotoUploadResponse:
    if not 5 <= len(files) <= 15:
        raise HTTPException(
            status_code=422,
            detail="Upload between 5 and 15 room photos in one request.",
        )

    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if len(project.photo_keys) + len(files) > 15:
        raise HTTPException(
            status_code=422,
            detail="A project cannot contain more than 15 room photos.",
        )

    validated: list[tuple[UploadFile, str, str]] = []
    for upload in files:
        extension = PurePosixPath(upload.filename or "").suffix.lower()
        image_type = _ALLOWED_IMAGE_TYPES.get(extension)
        if image_type is None:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported image type for {upload.filename!r}. Use JPEG, PNG, or WebP.",
            )
        content_type, signature_check = image_type
        if upload.size is not None and upload.size > settings.upload_max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{upload.filename!r} exceeds the {settings.upload_max_bytes}-byte upload limit.",
            )
        header = upload.file.read(12)
        upload.file.seek(0)
        if not signature_check(header):
            raise HTTPException(
                status_code=415,
                detail=f"File contents do not match the image extension for {upload.filename!r}.",
            )
        validated.append((upload, extension, content_type))

    uploaded_keys: list[str] = []
    photo_feedback: list[PhotoQualityFeedback] = []
    try:
        for upload, extension, content_type in validated:
            image_bytes = upload.file.read(settings.upload_max_bytes + 1)
            upload.file.seek(0)
            if len(image_bytes) > settings.upload_max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"{upload.filename!r} exceeds the {settings.upload_max_bytes}-byte upload limit.",
                )
            format_name = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[extension]
            try:
                report = image_quality_analyzer.analyze_and_normalize(image_bytes, format_name)
            except ValueError as exc:
                raise HTTPException(status_code=415, detail=f"Could not process {upload.filename!r}: {exc}") from exc
            object_key = f"{project_id}/raw_photos/{uuid.uuid4().hex}{extension}"
            upload_fileobj(BytesIO(report.normalized_bytes), object_key, content_type)
            uploaded_keys.append(object_key)
            photo_feedback.append(PhotoQualityFeedback(
                filename=upload.filename or "upload",
                status=report.status,
                is_blurry=report.is_blurry,
                lighting_status=report.lighting_status,
                resolution=(report.width, report.height),
                feedback_message=report.feedback_message,
            ))
    except HTTPException:
        for object_key in uploaded_keys:
            try:
                delete_object(object_key)
            except (BotoCoreError, ClientError):
                logger.exception("Could not clean up uploaded object %s", object_key)
        raise
    except (BotoCoreError, ClientError) as exc:
        logger.exception("S3 photo upload failed for project %s", project_id)
        for object_key in uploaded_keys:
            try:
                delete_object(object_key)
            except (BotoCoreError, ClientError):
                logger.exception("Could not clean up partially uploaded object %s", object_key)
        raise HTTPException(status_code=502, detail="One or more photos could not be stored.") from exc
    except Exception:
        logger.exception("Unexpected photo upload failure for project %s", project_id)
        for object_key in uploaded_keys:
            try:
                delete_object(object_key)
            except (BotoCoreError, ClientError):
                logger.exception("Could not clean up partially uploaded object %s", object_key)
        raise

    project.photo_keys = [*(project.photo_keys or []), *uploaded_keys]
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not save photo metadata for project %s", project_id)
        for object_key in uploaded_keys:
            try:
                delete_object(object_key)
            except (BotoCoreError, ClientError):
                logger.exception("Could not clean up uploaded object %s", object_key)
        raise HTTPException(
            status_code=500,
            detail="Photos were stored but project metadata could not be saved.",
        )
    db.refresh(project)
    return PhotoUploadResponse(
        project_id=project_id,
        total_uploaded=len(photo_feedback),
        photos=photo_feedback,
        photo_keys=project.photo_keys,
    )
