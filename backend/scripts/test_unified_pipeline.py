"""Live E2E check for reconstruction followed by a ControlNet redesign.

Run from ``backend`` with the API, PostgreSQL, Redis, MinIO/S3, and GPU Celery
worker available. For meaningful reconstruction results, pass 5-15 real room
captures with ``--photos``; otherwise textured synthetic images are generated.

    python scripts/test_unified_pipeline.py
    python scripts/test_unified_pipeline.py --photos D:\\RoomPhotos
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import UUID

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageDraw
from redis import Redis

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import settings  # noqa: E402
from database import SessionLocal  # noqa: E402
from models import Job, Project  # noqa: E402

LOGGER = logging.getLogger("roomsage.unified_e2e")
TERMINAL_STATUSES = {"COMPLETED", "FAILED"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
REDESIGN_PROMPT = "Modern Minimalist Nordic interior with oak wood furniture"


def _s3_client():
    options: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        options["aws_access_key_id"] = settings.aws_access_key_id
        options["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.s3_endpoint_url:
        options["endpoint_url"] = settings.s3_endpoint_url
    return boto3.client("s3", **options)


def _health_check(client: httpx.Client, s3) -> None:
    response = client.get("/health")
    response.raise_for_status()
    assert response.json().get("status") == "ok", response.text
    redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=5)
    try:
        assert redis_client.ping(), "Redis did not respond to PING."
    finally:
        redis_client.close()
    s3.head_bucket(Bucket=settings.s3_bucket)
    LOGGER.info("API, Redis, and S3/MinIO health checks passed")


def _make_synthetic_photos(directory: Path) -> list[Path]:
    """Make five different, textured JPEGs to exercise the upload endpoint."""
    paths: list[Path] = []
    for index in range(5):
        image = Image.new("RGB", (960, 720), (220 - index * 6, 214, 198 + index * 5))
        draw = ImageDraw.Draw(image)
        draw.rectangle((35, 40, 925, 680), outline=(45, 55, 48), width=7)
        draw.rectangle((100 + index * 9, 120, 420, 600), fill=(155, 116, 78), outline=(30, 35, 30), width=5)
        draw.rectangle((520, 120 + index * 6, 830, 350), fill=(80, 125, 145), outline=(25, 35, 40), width=6)
        draw.polygon(
            [(170, 520), (290 + index * 5, 420), (430, 520), (410, 595), (190, 595)],
            fill=(95, 75 + index * 4, 65),
            outline=(25, 30, 25),
        )
        for x in range(50, 920, 40):
            y = 380 + ((x // 40 + index) % 7) * 15
            draw.ellipse((x, y, x + 8, y + 8), fill=(35, 45, 38))
        path = directory / f"capture_{index + 1}.jpg"
        image.save(path, "JPEG", quality=90)
        paths.append(path)
    return paths


def _photo_paths(directory: Path, photos_dir: Path | None) -> list[Path]:
    if photos_dir is None:
        return _make_synthetic_photos(directory)
    paths = sorted(
        path for path in photos_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not 5 <= len(paths) <= 15:
        raise ValueError(f"--photos must contain 5-15 JPEG, PNG, or WebP files; found {len(paths)}.")
    return paths


def _upload_photos(client: httpx.Client, project_id: str, paths: list[Path]) -> None:
    files = [
        ("files", (path.name, path.read_bytes(), _content_type(path)))
        for path in paths
    ]
    response = client.post(f"/api/v1/projects/{project_id}/photos", files=files, timeout=180)
    response.raise_for_status()
    uploaded = response.json().get("photo_keys", [])
    assert len(uploaded) == len(paths), f"Unexpected uploaded photo keys: {uploaded!r}"
    LOGGER.info("Uploaded %d capture photos", len(uploaded))


def _content_type(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[path.suffix.lower()]


def _poll_job(
    client: httpx.Client,
    job_id: str,
    *,
    timeout_seconds: int,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = "UNKNOWN"
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        last_status = str(job.get("status", "UNKNOWN")).upper()
        LOGGER.info("Job %s: %s (%s%%)", job_id, last_status, job.get("progress", "?"))
        if last_status in TERMINAL_STATUSES:
            if last_status == "FAILED":
                raise RuntimeError(f"Job {job_id} failed: {job.get('error_message') or job.get('logs')}")
            return job
        time.sleep(poll_interval)
    raise TimeoutError(f"Job {job_id} did not finish before timeout; last status={last_status}.")


def _assert_s3_object(s3, key: str) -> None:
    try:
        metadata = s3.head_object(Bucket=settings.s3_bucket, Key=key)
    except (BotoCoreError, ClientError) as exc:
        raise AssertionError(f"S3 object s3://{settings.s3_bucket}/{key} is missing: {exc}") from exc
    assert metadata.get("ContentLength", 0) > 0, f"S3 object {key} is empty."
    LOGGER.info("Verified s3://%s/%s (%s bytes)", settings.s3_bucket, key, metadata["ContentLength"])


def _assert_output_url(job: dict[str, Any], filename: str) -> None:
    urls = job.get("output_urls")
    assert isinstance(urls, list), f"Job output_urls is not a list: {urls!r}"
    assert any(
        isinstance(url, str) and filename in unquote(urlsplit(url).path)
        for url in urls
    ), f"Completed job has no {filename} output URL: {job!r}"


def run(args: argparse.Namespace) -> None:
    s3 = _s3_client()
    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=30) as client:
        _health_check(client, s3)
        with tempfile.TemporaryDirectory(prefix="roomsage-unified-e2e-") as temp_dir:
            paths = _photo_paths(Path(temp_dir), args.photos)
            response = client.post(
                "/api/v1/projects",
                json={"name": "E2E Test Living Room", "room_type": "living_room"},
            )
            response.raise_for_status()
            assert response.status_code == 201, response.text
            project_id = response.json().get("id")
            assert project_id, f"Project response has no ID: {response.text}"
            LOGGER.info("Created project %s", project_id)
            _upload_photos(client, project_id, paths)

            response = client.post("/api/v1/jobs/reconstruct", json={"project_id": project_id})
            response.raise_for_status()
            reconstruction_id = response.json().get("job_id")
            assert reconstruction_id, f"Reconstruction response has no job ID: {response.text}"
            reconstruction = _poll_job(
                client, reconstruction_id,
                timeout_seconds=args.reconstruction_timeout,
                poll_interval=args.poll_interval,
            )
            assert reconstruction.get("depth_map_available") is True, (
                f"Stage 2 completed without a depth map: {reconstruction!r}"
            )
            _assert_output_url(reconstruction, "before_model.splat")
            _assert_s3_object(s3, f"{project_id}/outputs/before_model.splat")
            _assert_s3_object(s3, f"{project_id}/outputs/depth_map.png")

            response = client.post(
                "/api/v1/jobs/redesign",
                json={"project_id": project_id, "prompt": REDESIGN_PROMPT},
            )
            response.raise_for_status()
            redesign_id = response.json().get("job_id")
            assert redesign_id, f"Redesign response has no job ID: {response.text}"
            redesign = _poll_job(
                client, redesign_id,
                timeout_seconds=args.redesign_timeout,
                poll_interval=args.poll_interval,
            )
            _assert_output_url(redesign, "redesign_preview.png")
            preview_key = f"{project_id}/outputs/redesign_preview.png"
            _assert_s3_object(s3, preview_key)

            # The production API exposes output_urls, not raw S3 keys. Confirm the
            # durable project key and Job.output_key directly in PostgreSQL.
            with SessionLocal() as db:
                project = db.get(Project, UUID(project_id))
                job = db.get(Job, UUID(redesign_id))
                assert project is not None and project.s3_redesign_preview_key == preview_key, (
                    "Project.s3_redesign_preview_key was not persisted as expected."
                )
                assert job is not None and job.output_key == preview_key, (
                    "Redesign Job.output_key was not persisted as expected."
                )
                assert job.status.value == "COMPLETED", f"Redesign job is not completed: {job.status}"

    LOGGER.info("PASS: reconstruction and 2D redesign completed for project %s", project_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url", default=os.getenv("ROOMSAGE_API_URL", "http://localhost:8000"),
        help="FastAPI base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--photos", type=Path, default=None,
        help="Directory containing 5-15 real room photos; synthetic images are used otherwise.",
    )
    parser.add_argument(
        "--reconstruction-timeout", type=int,
        default=int(os.getenv("ROOMSAGE_RECONSTRUCTION_TIMEOUT", "7200")),
    )
    parser.add_argument(
        "--redesign-timeout", type=int,
        default=int(os.getenv("ROOMSAGE_REDESIGN_TIMEOUT", "3600")),
    )
    parser.add_argument(
        "--poll-interval", type=float,
        default=float(os.getenv("ROOMSAGE_POLL_INTERVAL", "5")),
    )
    args = parser.parse_args()
    if args.reconstruction_timeout <= 0 or args.redesign_timeout <= 0 or args.poll_interval <= 0:
        parser.error("Timeouts and polling interval must be positive.")
    if args.photos is not None and not args.photos.is_dir():
        parser.error(f"--photos is not a directory: {args.photos}")
    return args


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(parse_args())
    except (AssertionError, BotoCoreError, ClientError, httpx.HTTPError, RuntimeError, TimeoutError, ValueError) as exc:
        LOGGER.error("FAIL: %s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.error("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
