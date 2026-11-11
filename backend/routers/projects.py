import logging
import os
import re
import secrets
import uuid
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated, List

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from config import settings
from catalog_roles import ROOM_ROLE_REQUIREMENTS
from services.design_jobs import enqueue_catalog_render
from services.design_selection import InsufficientCatalogBudget, select_design_items
from database import get_db
from models import CatalogProduct, Design, DesignItem, Project, Room, RoomPhoto, User
from schemas import PhotoQualityFeedback, PhotoUploadResponse, ProjectCreate, ProjectRead
from services.gemini_service import GeminiService
from services.image_quality_service import image_quality_analyzer
from services.s3_service import create_presigned_url, delete_object, get_s3_client, upload_fileobj
from tasks.pipeline_tasks import (
    build_shopping_list_task,
    generate_3d_catalog_asset_task,
    generate_redesign_preview_task,
    ingest_photos_task,
)
from security import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/projects", tags=["projects"])
room_router = APIRouter(prefix="/rooms", tags=["rooms"])

_ALLOWED_IMAGE_TYPES = {
    ".jpg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda data: data.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    ".webp": (
        "image/webp",
        lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP",
    ),
}


def _redact_provider_error(message: str) -> str:
    secrets_to_redact = (
        settings.gemini_api_key,
        os.getenv("GEMINI_API_KEY", ""),
    )
    for secret in secrets_to_redact:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    message = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+",
        r"\1[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)((?:api[_-]?key|token)\s*[=:]\s*)[^\s&,;]+",
        r"\1[REDACTED]",
        message,
    )
    return message


class BudgetRequest(BaseModel):
    budget_cap_inr: float = Field(gt=0)
    theme: str = Field(min_length=1, max_length=120)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    image_s3_key: str | None = None


class RoomCreate(BaseModel):
    type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = Field(default=None, min_length=1, max_length=40)


class RoomBudgetRequest(BaseModel):
    budget: float = Field(gt=0)
    style: str | None = Field(default=None, min_length=1, max_length=120)


class DesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: str = Field(min_length=1, max_length=120)
    palette: str = Field(min_length=1, max_length=120)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)


def _owned_project(db: Session, project_id: uuid.UUID, user: User) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def _owned_room(db: Session, room_id: uuid.UUID, user: User) -> Room:
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found.")
    _owned_project(db, room.project_id, user)
    return room


def _room_flags(room: Room) -> dict:
    count = len(room.photos)
    designs = room.designs
    return {
        "id": str(room.id), "project_id": str(room.project_id), "type": room.type,
        "name": room.name, "budget": float(room.budget) if room.budget is not None else None,
        "status": room.status, "photo_count": count,
        "has_2d": any(design.status == "COMPLETED" and design.image_url for design in designs),
        "can_3d": count >= 3,
        "designs": [{"id": str(d.id), "status": d.status, "starred": d.starred,
                     "style": d.style, "palette": d.palette,
                     "image_url": f"/api/v1/designs/{d.id}/image" if d.image_url else None,
                     "created_at": d.created_at.isoformat() if d.created_at else None} for d in designs],
    }


def _project_read(db: Session, project: Project) -> dict:
    rooms = db.scalars(select(Room).where(Room.project_id == project.id).order_by(Room.created_at)).unique().all()
    return {"id": str(project.id), "name": project.name,
            "total_budget": sum(float(room.budget or 0) for room in rooms),
            "rooms": [_room_flags(room) for room in rooms]}


def _room_budget_range(
    db: Session, room_type: str, style: str | None = None,
) -> tuple[float, float, list[dict]]:
    requirements = ROOM_ROLE_REQUIREMENTS.get(room_type)
    if requirements is None:
        raise HTTPException(status_code=422, detail=f"No essential catalog roles are configured for {room_type.replace('_', ' ')}.")
    products = [product for product in db.scalars(select(CatalogProduct)).all()
                if room_type in (product.room_types or [])
                and product.selectable and product.kind == "object"
                and product.reference_image_s3_key
                and (style is None or product.theme.casefold() == style.casefold())]
    minimum = 0.0
    maximum = 0.0
    roles_used: list[dict] = []
    for requirement in requirements:
        matching = [product for product in products if product.role == requirement.role]
        if not matching:
            raise HTTPException(
                status_code=422,
                detail=(f"Catalog style {style!r} is missing the essential '{requirement.role}' role "
                        f"for {room_type.replace('_', ' ')}." if style else
                        f"Catalog is missing the essential '{requirement.role}' role for {room_type.replace('_', ' ')}."),
            )
        lowest = min(float(product.price_inr) for product in matching)
        highest = max(float(product.price_inr) for product in matching)
        minimum += lowest * requirement.minimum_quantity
        maximum += highest * requirement.maximum_quantity
        roles_used.append({
            "role": requirement.role,
            "minimum_quantity": requirement.minimum_quantity,
            "maximum_quantity": requirement.maximum_quantity,
            "min_item_price": lowest,
            "max_item_price": highest,
        })
    return minimum, maximum, roles_used


@router.get("")
def list_projects(db: Annotated[Session, Depends(get_db)], user: CurrentUser) -> list[dict]:
    projects = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.created_at)).all()
    return [_project_read(db, project) for project in projects]


@router.get("/{project_id}")
def get_project(
    project_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    return _project_read(db, _owned_project(db, project_id, user))


@router.get("/{project_id}/rooms")
def list_rooms(
    project_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> list[dict]:
    project = _owned_project(db, project_id, user)
    rooms = db.scalars(select(Room).where(Room.project_id == project.id).order_by(Room.created_at)).unique().all()
    return [_room_flags(room) for room in rooms]


@router.post("/{project_id}/rooms", status_code=status.HTTP_201_CREATED)
def create_room(
    project_id: uuid.UUID, payload: RoomCreate,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    project = _owned_project(db, project_id, user)
    room_type = payload.type.strip().casefold()
    products = db.scalars(select(CatalogProduct)).all()
    supported = {
        room_type for room_type, requirements in ROOM_ROLE_REQUIREMENTS.items()
        if all(any(product.role == requirement.role and product.selectable
                   and product.kind == "object" and product.reference_image_s3_key
                   and room_type in (product.room_types or []) for product in products)
               for requirement in requirements)
    }
    if room_type not in supported:
        raise HTTPException(status_code=422, detail="Choose a room type supported by the catalog.")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Room name cannot be blank.")
    room = Room(project_id=project.id, type=room_type, name=name)
    db.add(room)
    db.commit()
    db.refresh(room)
    return _room_flags(room)


@router.patch("/{project_id}/rooms/{room_id}")
def update_room(
    project_id: uuid.UUID, room_id: uuid.UUID, payload: RoomUpdate,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    _owned_project(db, project_id, user)
    room = _owned_room(db, room_id, user)
    if room.project_id != project_id:
        raise HTTPException(status_code=404, detail="Room not found.")
    if payload.name is not None:
        room.name = payload.name.strip()
    if payload.status is not None:
        room.status = payload.status.strip().upper()
    db.commit()
    db.refresh(room)
    return _room_flags(room)


@router.delete("/{project_id}/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_room(
    project_id: uuid.UUID, room_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> None:
    _owned_project(db, project_id, user)
    room = _owned_room(db, room_id, user)
    if room.project_id != project_id:
        raise HTTPException(status_code=404, detail="Room not found.")
    db.delete(room)
    db.commit()


@room_router.get("/{room_id}/budget-range")
def get_room_budget_range(
    room_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
    style: str | None = Query(default=None, min_length=1, max_length=120),
) -> dict:
    room = _owned_room(db, room_id, user)
    minimum, maximum, roles_used = _room_budget_range(db, room.type, style)
    return {"min": minimum, "max": maximum, "currency": "INR", "roles_used": roles_used,
            "style": style}


@room_router.post("/{room_id}/budget")
def set_room_budget(
    room_id: uuid.UUID, payload: RoomBudgetRequest,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    room = _owned_room(db, room_id, user)
    minimum, maximum, roles_used = _room_budget_range(db, room.type, payload.style)
    if payload.budget < minimum or payload.budget > maximum:
        raise HTTPException(
            status_code=422,
            detail=f"Budget for {room.type.replace('_', ' ')} must be between ₹{minimum:,.0f} and ₹{maximum:,.0f}.",
        )
    room.budget = payload.budget
    db.commit()
    db.refresh(room)
    return {**_room_flags(room), "budget_range": {"min": minimum, "max": maximum, "roles_used": roles_used}}


@room_router.get("/{room_id}")
def get_room(
    room_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    return _room_flags(_owned_room(db, room_id, user))


@room_router.get("/{room_id}/coverage")
def get_room_coverage(
    room_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict[str, int | bool]:
    room = _owned_room(db, room_id, user)
    count = db.scalar(select(func.count(RoomPhoto.id)).where(RoomPhoto.room_id == room.id)) or 0
    return {"photo_count": count, "2d_ready": count >= 1,
            "3d_ready": count >= 3, "photos_needed": max(0, 3 - count)}


@room_router.post("/{room_id}/photos", status_code=status.HTTP_201_CREATED)
def upload_room_photos(
    room_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
    files: Annotated[list[UploadFile], File(...)],
    angle_labels: Annotated[list[str] | None, Form()] = None,
) -> dict:
    room = _owned_room(db, room_id, user)
    existing = db.scalar(select(func.count(RoomPhoto.id)).where(RoomPhoto.room_id == room.id)) or 0
    if not files or existing + len(files) > 15:
        raise HTTPException(status_code=422, detail="A room must have between 1 and 15 photos.")
    validated: list[tuple[UploadFile, str, str]] = []
    for upload in files:
        extension = PurePosixPath(upload.filename or "").suffix.lower()
        image_type = _ALLOWED_IMAGE_TYPES.get(extension)
        if image_type is None:
            raise HTTPException(status_code=415, detail=f"Unsupported image type for {upload.filename!r}.")
        content_type, signature_check = image_type
        if upload.size is not None and upload.size > settings.upload_max_bytes:
            raise HTTPException(status_code=413, detail=f"{upload.filename!r} exceeds the upload limit.")
        header = upload.file.read(12)
        upload.file.seek(0)
        if not signature_check(header):
            raise HTTPException(status_code=415, detail=f"File contents do not match the image extension for {upload.filename!r}.")
        validated.append((upload, extension, content_type))
    uploaded: list[str] = []
    records: list[RoomPhoto] = []
    try:
        for offset, (upload, extension, content_type) in enumerate(validated):
            image_bytes = upload.file.read(settings.upload_max_bytes + 1)
            upload.file.seek(0)
            if len(image_bytes) > settings.upload_max_bytes:
                raise HTTPException(status_code=413, detail=f"{upload.filename!r} exceeds the upload limit.")
            format_name = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[extension]
            report = image_quality_analyzer.analyze_and_normalize(image_bytes, format_name)
            key = f"{room.project_id}/rooms/{room.id}/photos/{uuid.uuid4().hex}{extension}"
            upload_fileobj(BytesIO(report.normalized_bytes), key, content_type)
            uploaded.append(key)
            label = angle_labels[offset].strip() if angle_labels and offset < len(angle_labels) else None
            records.append(RoomPhoto(room_id=room.id, url=key, angle_label=label or None, order=existing + offset))
        db.add_all(records)
        db.commit()
    except HTTPException:
        db.rollback()
        for key in uploaded:
            try: delete_object(key)
            except Exception: logger.exception("Could not clean up uploaded room photo %s", key)
        raise
    except Exception as exc:
        db.rollback()
        for key in uploaded:
            try: delete_object(key)
            except Exception: logger.exception("Could not clean up uploaded room photo %s", key)
        logger.exception("Room photo upload failed for %s", room_id)
        raise HTTPException(status_code=502, detail="Room photos could not be stored.") from exc
    return {"room_id": str(room.id), "uploaded": len(records), "coverage": get_room_coverage(room.id, db, user)}


@room_router.post("/{room_id}/designs", status_code=status.HTTP_202_ACCEPTED)
def create_room_design(
    room_id: uuid.UUID, payload: DesignRequest,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    room = _owned_room(db, room_id, user)
    photo = db.scalar(select(RoomPhoto).where(RoomPhoto.room_id == room.id).order_by(RoomPhoto.order).limit(1))
    if photo is None:
        raise HTTPException(status_code=422, detail="Add at least one room photo before generating a design.")
    if room.budget is None:
        raise HTTPException(status_code=422, detail="Set a room budget before generating a design.")
    products = db.scalars(select(CatalogProduct).where(CatalogProduct.selectable.is_(True))).all()
    seed = payload.seed if payload.seed is not None else secrets.randbelow(2_147_483_647)
    try:
        selected = select_design_items(products, room.type, payload.style.strip(),
                                       room.budget, seed)
    except InsufficientCatalogBudget as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    design = Design(room_id=room.id, parent_id=None, style=payload.style.strip(),
                    palette=payload.palette.strip(),
                    prompt=(
                        "[MOCK_PREVIEW] Catalog-constrained render; DesignItems are the complete "
                        "object manifest."
                        if settings.image_provider == "stub"
                        else "Catalog-constrained render; DesignItems are the complete object manifest."
                    ),
                    status="QUEUED",
                    seed=seed,
                    image_provider=settings.image_provider,
                    model_ids=settings.image_model_ids,
                )
    db.add(design)
    db.flush()
    db.add_all([
        DesignItem(design_id=design.id, catalog_item_id=item.product.product_id,
                   role=item.role, qty=item.qty, x=None, y=None, visible=True,
                   is_essential=item.essential)
        for item in selected
    ])
    db.commit()
    db.refresh(design)
    try:
        job = enqueue_catalog_render(db, room, design)
    except RuntimeError as exc:
        raise HTTPException(status_code=503,
                            detail={"message": str(exc), "design_id": str(design.id)}) from exc
    return {"design_id": str(design.id), "room_id": str(room.id), "job_id": str(job.id),
            "status": design.status, "seed": seed}


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
    user: CurrentUser,
) -> Project:
    project = Project(name=payload.name.strip(), room_type=payload.room_type, user_id=user.id)
    if not project.name:
        raise HTTPException(status_code=422, detail="Project name cannot be blank.")
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post(
    "/{project_id}/photos",
    response_model=PhotoUploadResponse,
    status_code=status.HTTP_200_OK,
)
async def upload_project_photos(
    project_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
    files: List[UploadFile] = File(..., description="Upload 1 to 5 room photos"),
) -> PhotoUploadResponse:
    """Upload room photos for Stage 1 quality assurance and depth-map ingestion."""
    if not files or len(files) < 1 or len(files) > 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please upload between 1 and 5 room photos.",
        )
    return await run_in_threadpool(
        _upload_project_photos,
        project_id,
        db,
        user,
        files,
    )


def _upload_project_photos(
    project_id: uuid.UUID,
    db: Session,
    user: CurrentUser,
    files: List[UploadFile],
) -> PhotoUploadResponse:
    project = _owned_project(db, project_id, user)
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
    user: CurrentUser,
) -> dict[str, str]:
    _owned_project(db, project_id, user)
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
    user: CurrentUser,
) -> dict:
    project = _owned_project(db, project_id, user)
    if not project.scene_s3_key:
        raise HTTPException(status_code=404, detail="Room scene analysis is not ready yet.")
    return _read_json_artifact(project.scene_s3_key)


@router.get("/{project_id}/budget")
def get_project_budget(
    project_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
) -> dict:
    project = _owned_project(db, project_id, user)
    if not project.shopping_list_s3_key:
        raise HTTPException(status_code=404, detail="Budget shopping list is not ready yet.")
    return _read_json_artifact(project.shopping_list_s3_key)


@router.post("/{project_id}/chat")
def refine_project_design(
    project_id: uuid.UUID,
    payload: ChatRequest,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
) -> dict[str, str | None]:
    project = _owned_project(db, project_id, user)
    if payload.image_s3_key and payload.image_s3_key not in {
        project.s3_redesign_preview_key,
        project.depth_map_s3_key,
        *project.photo_keys,
    }:
        raise HTTPException(status_code=400, detail="image_s3_key does not belong to this project.")
    try:
        refinement = GeminiService().chat_refinement(str(project_id), payload.message)
    except httpx.TimeoutException as exc:
        logger.warning("Gemini chat refinement timed out for project %s: %s", project_id, exc)
        raise HTTPException(
            status_code=504,
            detail="Design refinement timed out. Please try again.",
        ) from exc
    except Exception as exc:
        error_message = _redact_provider_error(str(exc) or type(exc).__name__)
        logger.error(
            "Gemini chat refinement failed for project %s; exception_type=%s message=%s",
            project_id,
            type(exc).__name__,
            error_message,
        )
        detail = "Design refinement is temporarily unavailable."
        if settings.environment.strip().casefold() in {"development", "dev", "local"}:
            detail = f"{detail} ({type(exc).__name__})"
        raise HTTPException(status_code=502, detail=detail) from exc

    try:
        task_result = generate_redesign_preview_task.apply_async(
            args=[str(project_id), refinement.refined_prompt],
        )
        preview_key = task_result.get(
            timeout=settings.task_time_limit_seconds,
            propagate=True,
        )
        preview_url = create_presigned_url(preview_key)
    except Exception as exc:
        logger.exception("Could not generate or retrieve redesign preview for project %s", project_id)
        raise HTTPException(
            status_code=503,
            detail="Room preview generation is temporarily unavailable.",
        ) from exc

    return {
        "project_id": str(project_id),
        "reply": refinement.reply,
        "refined_prompt": refinement.refined_prompt,
        "preview_image_url": preview_url,
    }


@router.post("/{project_id}/3d-assets/{product_id}", status_code=status.HTTP_202_ACCEPTED)
def request_catalog_3d_asset(
    project_id: uuid.UUID,
    product_id: str,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
) -> dict[str, str]:
    _owned_project(db, project_id, user)
    product = db.get(CatalogProduct, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Catalog product not found.")
    if (product.category or "").casefold() in {"paint", "flooring", "rug", "rugs"}:
        raise HTTPException(status_code=422, detail="This catalog category does not use a 3D mesh.")
    try:
        task = generate_3d_catalog_asset_task.delay(product_id)
    except Exception as exc:
        logger.exception("Could not queue 3D asset generation for %s", product_id)
        raise HTTPException(status_code=503, detail="3D asset generation could not be queued.") from exc
    return {
        "project_id": str(project_id),
        "product_id": product_id,
        "status": "queued",
        "task_id": task.id,
    }


@router.get("/{project_id}/3d-assets")
def get_project_3d_assets(
    project_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    user: CurrentUser,
) -> list[dict[str, str]]:
    project = _owned_project(db, project_id, user)
    if not project.shopping_list_s3_key:
        return []
    shopping_list = _read_json_artifact(project.shopping_list_s3_key)
    selected_ids = [
        str(item.get("product_id"))
        for item in shopping_list.get("selected_items", [])
        if item.get("product_id")
    ]
    if settings.mock_meshy:
        return [
            {"product_id": product_id, "glb_url": "http://localhost:9000/roomsage/catalog_3d/sample_sofa.glb"}
            for product_id in selected_ids
        ]
    s3 = get_s3_client()
    assets: list[dict[str, str]] = []
    for product_id in selected_ids:
        key = f"catalog_3d/{product_id}.glb"
        try:
            s3.head_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                continue
            raise HTTPException(status_code=502, detail="Could not check generated 3D assets.") from exc
        assets.append({"product_id": product_id, "glb_url": create_presigned_url(key)})
    return assets
