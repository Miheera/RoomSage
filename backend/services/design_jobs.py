"""Shared async enqueue path for new catalog-constrained design versions."""

from datetime import datetime, timezone

from models import Design, Job, JobStatus, Room, TaskType
from services.job_state import get_redis_client, record_job_state
from tasks.pipeline_tasks import run_catalog_design_task


def enqueue_catalog_render(db, room: Room, design: Design) -> Job:
    job = Job(project_id=room.project_id, room_id=room.id, design_id=design.id,
              task_type=TaskType.REDESIGN_2D, status=JobStatus.QUEUED, progress=0)
    db.add(job)
    db.commit()
    db.refresh(job)
    redis_client = get_redis_client()
    try:
        record_job_state(redis_client, str(job.id), status=JobStatus.QUEUED,
                         progress=0, message="Catalog-constrained 2D render queued.")
        run_catalog_design_task.apply_async(
            args=[str(job.id), str(room.id), str(design.id)], task_id=str(job.id),
        )
    except Exception as exc:
        design.status = "FAILED"
        design.error_message = "The task queue is unavailable; please retry."
        job.status = JobStatus.FAILED
        job.error_message = design.error_message
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        raise RuntimeError(design.error_message) from exc
    finally:
        redis_client.close()
    return job
