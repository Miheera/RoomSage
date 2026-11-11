from celery import Celery
from celery.signals import worker_ready

from config import settings
from services.image_generation_service import log_image_provider


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
        "roomsage.render_catalog_design": {"queue": "render_queue"},
        "roomsage.ingest_photos": {"queue": "gpu_queue"},
        "roomsage.generate_catalog_3d_asset": {"queue": "gpu_queue"},
        "roomsage.build_shopping_list": {"queue": "gpu_queue"},
        "roomsage.generate_redesign_preview": {"queue": "gpu_queue"},
    },
)

# Keep render job redelivery in Redis DB 1; the 3D/GPU queues remain on DB 0.
render_celery_app = Celery(
    "roomsage-render",
    broker=settings.render_redis_url,
    backend=settings.render_redis_url,
    include=["tasks.pipeline_tasks"],
)
render_celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "roomsage.render_catalog_design": {"queue": "render_queue"},
        "roomsage.reap_stalled_catalog_jobs": {"queue": "render_reaper_queue"},
    },
    broker_transport_options={"visibility_timeout": settings.render_visibility_timeout_seconds},
    result_backend_transport_options={"visibility_timeout": settings.render_visibility_timeout_seconds},
    beat_schedule={
        "reap-stalled-catalog-renders": {
            "task": "roomsage.reap_stalled_catalog_jobs",
            "schedule": 60.0,
            "options": {"queue": "render_reaper_queue"},
        },
    },
)


@worker_ready.connect
def log_worker_image_provider(**_: object) -> None:
    log_image_provider("Celery worker")
