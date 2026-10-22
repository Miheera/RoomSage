from celery import Celery

from config import settings

celery_app = Celery(
    "roomsage",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["tasks.pipeline_tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    task_time_limit=settings.task_time_limit_seconds,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "roomsage.reconstruct_project": {"queue": "gpu_queue"},
        "roomsage.run_3d_edit": {"queue": "gpu_queue"},
    },
)
