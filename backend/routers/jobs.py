import logging
import math
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from celery.exceptions import CeleryError
from fastapi import APIRouter, Depends, HTTPException, status
from kombu.exceptions import OperationalError
from redis import Redis, RedisError
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import Job, JobStatus, Project, TaskType
from schemas import EditJobRequest, EditJobResponse, JobCreate, JobCreated, JobRead
from services.job_state import get_redis_client, read_job_state, record_job_state
from services.s3_service import create_presigned_url, get_s3_client
from tasks.pipeline_tasks import reconstruct_project, run_3d_edit_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/reconstruct",
    response_model=JobCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_reconstruction(
    payload: JobCreate,
    db: Annotated[Session, Depends(get_db)],
) -> JobCreated:
    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if not 5 <= len(project.photo_keys) <= 15:
        raise HTTPException(
            status_code=422,
            detail="A project must have between 5 and 15 uploaded photos before reconstruction.",
        )

    job = Job(project_id=project.id, task_type=TaskType.GAUSSIAN_SPLATTING_TRAIN)
    db.add(job)
    db.commit()
    db.refresh(job)

    redis_client = get_redis_client()
    try:
        record_job_state(
            redis_client,
            str(job.id),
            status=JobStatus.QUEUED,
            progress=0,
            message="Reconstruction job queued.",
        )
        reconstruct_project.apply_async(
            args=[str(job.id), str(project.id)],
            task_id=str(job.id),
        )
    except (RedisError, CeleryError, OperationalError) as exc:
        logger.exception("Could not queue reconstruction job %s", job.id)
        job.status = JobStatus.FAILED
        job.error_message = "The task queue is unavailable; please retry."
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        try:
            record_job_state(
                redis_client,
                str(job.id),
                status=JobStatus.FAILED,
                progress=0,
                message=job.error_message,
                error_message=job.error_message,
            )
        except RedisError:
            logger.warning("Could not persist queue failure to Redis for job %s", job.id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="The task queue is temporarily unavailable.",
        ) from exc
    finally:
        redis_client.close()

    return JobCreated(job_id=job.id, status=job.status)


@router.post(
    "/edit",
    response_model=EditJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid edit parameters."},
        status.HTTP_404_NOT_FOUND: {"description": "Project or reconstruction not found."},
        status.HTTP_502_BAD_GATEWAY: {"description": "S3 artifact storage is unavailable."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The task queue is unavailable."},
    },
)
def start_3d_edit(
    payload: EditJobRequest,
    db: Annotated[Session, Depends(get_db)],
) -> EditJobResponse:
    if (
        payload.style_prompt is None
        and payload.remove_mask_id is None
        and payload.insert_catalog_image_key is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one edit operation must be requested.",
        )
    if payload.style_prompt is not None and not payload.style_prompt.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="style_prompt must not be blank.",
        )
    if payload.remove_mask_id is not None and payload.remove_mask_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="remove_mask_id must be a positive integer.",
        )
    if len(payload.target_position) != 3 or not all(
        math.isfinite(coordinate) for coordinate in payload.target_position
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_position must contain three finite coordinates.",
        )
    if not math.isfinite(payload.target_scale) or payload.target_scale <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="target_scale must be a positive finite number.",
        )

    catalog_image_key = payload.insert_catalog_image_key
    if catalog_image_key is not None:
        if not catalog_image_key.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="insert_catalog_image_key must not be blank.",
            )
        key_parts = catalog_image_key.replace("\\", "/").split("/")
        if (
            catalog_image_key.startswith(("/", "\\"))
            or any(part in {"", ".", ".."} for part in key_parts)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="insert_catalog_image_key must be a safe relative S3 object key.",
            )

    project = db.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found.",
        )

    completed_reconstruction = (
        db.query(Job)
        .filter(
            Job.project_id == project.id,
            Job.task_type == TaskType.GAUSSIAN_SPLATTING_TRAIN,
            Job.status == JobStatus.COMPLETED,
        )
        .order_by(Job.completed_at.desc())
        .first()
    )
    if completed_reconstruction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Completed Stage 2 reconstruction not found for project.",
        )

    before_ply_key = f"{project.id}/outputs/before_model.ply"
    try:
        artifact_metadata = get_s3_client().head_object(
            Bucket=settings.s3_bucket,
            Key=before_ply_key,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Stage 2 before_model.ply artifact was not found.",
            ) from exc
        logger.exception("Could not verify reconstruction artifact %s", before_ply_key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not verify the Stage 2 reconstruction artifact.",
        ) from exc
    except BotoCoreError as exc:
        logger.exception("Could not connect to S3 while verifying %s", before_ply_key)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not verify the Stage 2 reconstruction artifact.",
        ) from exc
    if artifact_metadata.get("ContentLength", 0) <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Stage 2 before_model.ply artifact is empty.",
        )

    job = Job(
        project_id=project.id,
        task_type=TaskType.INSTRUCT_GS2GS_EDIT,
        status=JobStatus.QUEUED,
        progress=0,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    redis_client = get_redis_client()
    edit_params = {
        "style_prompt": payload.style_prompt,
        "remove_mask_id": payload.remove_mask_id,
        "insert_catalog_image_key": catalog_image_key,
        "target_position": payload.target_position,
        "target_scale": payload.target_scale,
    }
    try:
        record_job_state(
            redis_client,
            str(job.id),
            status=JobStatus.QUEUED,
            progress=0,
            message="3D edit job queued.",
        )
        run_3d_edit_task.apply_async(
            args=[str(project.id), edit_params],
            task_id=str(job.id),
        )
    except (RedisError, CeleryError, OperationalError) as exc:
        logger.exception("Could not queue 3D edit job %s", job.id)
        job.status = JobStatus.FAILED
        job.error_message = "The task queue is unavailable; please retry."
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        try:
            record_job_state(
                redis_client,
                str(job.id),
                status=JobStatus.FAILED,
                progress=0,
                message=job.error_message,
                error_message=job.error_message,
            )
        except RedisError:
            logger.warning(
                "Could not persist edit queue failure to Redis for job %s",
                job.id,
                exc_info=True,
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The task queue is temporarily unavailable.",
        ) from exc
    finally:
        redis_client.close()

    return EditJobResponse(
        job_id=job.id,
        status=job.status,
        polling_url=f"/api/v1/jobs/{job.id}",
    )


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: UUID,
    db: Annotated[Session, Depends(get_db)],
) -> JobRead:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    status_value = job.status
    progress = job.progress
    updated_at = job.updated_at
    error_message = job.error_message
    depth_map_available = job.depth_map_available
    logs: list[str] = []
    redis_client: Redis = get_redis_client()
    try:
        state, logs = read_job_state(redis_client, str(job.id))
        if state:
            redis_updated_at = datetime.fromisoformat(state["updated_at"])
            db_updated_at = job.updated_at
            if db_updated_at.tzinfo is None:
                db_updated_at = db_updated_at.replace(tzinfo=timezone.utc)
            if redis_updated_at.tzinfo is None:
                redis_updated_at = redis_updated_at.replace(tzinfo=timezone.utc)
            if redis_updated_at >= db_updated_at:
                status_value = JobStatus(state.get("status", job.status.value))
                progress = max(0, min(100, int(state.get("progress", job.progress))))
                updated_at = redis_updated_at
                error_message = state.get("error_message", job.error_message)
                if "depth_map_available" in state:
                    depth_map_available = state["depth_map_available"].lower() in {"1", "true"}
    except RedisError:
        logger.warning("Redis job state unavailable for %s; serving database state", job_id, exc_info=True)
    finally:
        redis_client.close()

    remaining = None
    if status_value == JobStatus.QUEUED:
        remaining = settings.pipeline_estimated_seconds
    elif status_value == JobStatus.PROCESSING:
        remaining = max(
            0,
            int(settings.pipeline_estimated_seconds * (100 - progress) / 100),
        )

    output_urls: list[str] = []
    depth_map_url: str | None = None
    if status_value == JobStatus.COMPLETED and job.output_key:
        try:
            output_urls.append(create_presigned_url(job.output_key))
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Could not generate output URL for job %s", job_id)
            raise HTTPException(
                status_code=502,
                detail="The output artifact is temporarily unavailable.",
            ) from exc
    if depth_map_available:
        project = db.get(Project, job.project_id)
        if project is not None and project.depth_map_s3_key:
            try:
                depth_map_url = create_presigned_url(project.depth_map_s3_key)
            except (BotoCoreError, ClientError) as exc:
                logger.exception("Could not generate depth-map URL for job %s", job_id)
                raise HTTPException(
                    status_code=502,
                    detail="The depth-map artifact is temporarily unavailable.",
                ) from exc

    return JobRead(
        job_id=job.id,
        project_id=job.project_id,
        task_type=job.task_type,
        status=status_value,
        progress=progress,
        estimated_time_remaining_seconds=remaining,
        logs=logs,
        error_message=error_message,
        output_urls=output_urls,
        depth_map_available=depth_map_available,
        depth_map_url=depth_map_url,
        created_at=job.created_at,
        updated_at=updated_at,
    )
