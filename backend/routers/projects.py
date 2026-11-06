import logging
import uuid
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Project
from schemas import PhotoQualityFeedback, PhotoUploadResponse, ProjectCreate, ProjectRead
from services.gemini_service import GeminiService
from services.image_quality_service import image_quality_analyzer
from services.s3_service import create_presigned_url, delete_object, get_s3_client, upload_fileobj
from tasks.pipeline_tasks import build_shopping_list_task, ingest_photos_task

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


class BudgetRequest(BaseModel):
    budget_cap_inr: float = Field(gt=0)
    theme: str = Field(min_length=1, max_length=120)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    image_s3_key: str | None = None


def _read_json_artifact(key: str) -> dict:
    try:
        response = get_s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
        try:
            import json
            return json.loads(response["Body"].read())
        finally:
            response["Body"].close()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="Artifact is not ready yet.") from exc
        logger.exception("Could not read artifact %s", key)
        raise HTTPException(status_code=502, detail="Could not read artifact from storage.") from exc
    except BotoCoreError as exc:
        logger.exception("Could not connect to object storage for %s", key)
        raise HTTPException(status_code=502, detail="Could not connect to artifact storage.") from exc


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
    try:
        ingest_photos_task.delay(str(project.id))
    except Exception as exc:
        logger.exception("Could not enqueue room photo analysis for project %s", project.id)
        raise HTTPException(
            status_code=503,
            detail="Photos were uploaded, but room analysis could not be queued. Retry the upload or contact support.",
        ) from exc
    return PhotoUploadResponse(
        project_id=project_id,
        total_uploaded=len(photo_feedback),
        photos=photo_feedback,
        photo_keys=project.photo_keys,
    )


@router.post("/{project_id}/budget", status_code=status.HTTP_202_ACCEPTED)
def request_budget_shopping_list(
    project_id: uuid.UUID,
    payload: BudgetRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    try:
        task = build_shopping_list_task.delay(
            str(project_id), payload.theme.strip(), payload.budget_cap_inr
        )
    except Exception as exc:
        logger.exception("Could not queue budget planning for project %s", project_id)
        raise HTTPException(status_code=503, detail="Budget planning could not be queued.") from exc
    return {"project_id": str(project_id), "task_id": task.id, "status": "QUEUED"}


@router.get("/{project_id}/scene")
def get_project_scene(
    project_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not project.scene_s3_key:
        raise HTTPException(status_code=404, detail="Room scene analysis is not ready yet.")
    return _read_json_artifact(project.scene_s3_key)


@router.get("/{project_id}/budget")
def get_project_budget(
    project_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not project.shopping_list_s3_key:
        raise HTTPException(status_code=404, detail="Budget shopping list is not ready yet.")
    return _read_json_artifact(project.shopping_list_s3_key)


@router.post("/{project_id}/chat")
def refine_project_design(
    project_id: uuid.UUID,
    payload: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str | None]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if payload.image_s3_key and payload.image_s3_key not in {
        project.s3_redesign_preview_key,
        project.depth_map_s3_key,
        *project.photo_keys,
    }:
        raise HTTPException(status_code=400, detail="image_s3_key does not belong to this project.")
    try:
        refinement = GeminiService().chat_refinement(str(project_id), payload.message)
        preview_url = (
            create_presigned_url(project.s3_redesign_preview_key)
            if project.s3_redesign_preview_key
            else None
        )
    except Exception as exc:
        logger.exception("Gemini chat refinement failed for project %s", project_id)
        raise HTTPException(status_code=502, detail="Design refinement is temporarily unavailable.") from exc
    return {
        "project_id": str(project_id),
        "reply": refinement.reply,
        "refined_prompt": refinement.refined_prompt,
        "preview_image_url": preview_url,
    }
