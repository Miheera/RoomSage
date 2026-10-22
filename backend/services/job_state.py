import json
from collections.abc import Mapping
from datetime import datetime, timezone

from redis import Redis

from config import settings
from models import JobStatus


def get_redis_client() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def state_key(job_id: str) -> str:
    return f"roomsage:job:{job_id}"


def logs_key(job_id: str) -> str:
    return f"roomsage:job:{job_id}:logs"


def record_job_state(
    redis_client: Redis,
    job_id: str,
    *,
    status: JobStatus,
    progress: int,
    message: str | None = None,
    error_message: str | None = None,
    extra_fields: Mapping[str, str | int] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    pipeline = redis_client.pipeline()
    fields: dict[str, str | int] = {
        "status": status.value,
        "progress": progress,
        "updated_at": now,
    }
    if error_message is not None:
        fields["error_message"] = error_message
    if extra_fields:
        fields.update(extra_fields)
    pipeline.hset(state_key(job_id), mapping=fields)
    if message:
        pipeline.rpush(
            logs_key(job_id),
            json.dumps({"timestamp": now, "message": message}),
        )
        pipeline.ltrim(logs_key(job_id), -200, -1)
    pipeline.expire(state_key(job_id), settings.job_state_ttl_seconds)
    pipeline.expire(logs_key(job_id), settings.job_state_ttl_seconds)
    pipeline.execute()


def read_job_state(redis_client: Redis, job_id: str) -> tuple[dict[str, str], list[str]]:
    raw_logs = redis_client.lrange(logs_key(job_id), 0, -1)
    logs: list[str] = []
    for entry in raw_logs:
        try:
            item = json.loads(entry)
            logs.append(f"{item['timestamp']}: {item['message']}")
        except (KeyError, TypeError, json.JSONDecodeError):
            logs.append(entry)
    return redis_client.hgetall(state_key(job_id)), logs
