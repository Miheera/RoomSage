"""End-to-end smoke test for the RoomSage reconstruction and editing workers.

Run from the backend directory after starting the API, GPU worker, Redis, and
MinIO/PostgreSQL services:

    python scripts/test_pipeline_e2e.py

The current API does not expose a Stage 4 dispatch route, so this script queues
the edit task through Celery and uses the API's job endpoint for polling.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, ImageDraw
from redis import Redis

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import settings  # noqa: E402

LOGGER = logging.getLogger("roomsage.e2e")
TERMINAL_STATUSES = {"COMPLETED", "FAILED"}


def _http_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 30,
) -> tuple[int, bytes, dict[str, str]]:
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers.items())
    except HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc.reason}") from exc


def _json_request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    body = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    status_code, response_body, _ = _http_request(
        f"{base_url.rstrip('/')}{path}",
        method=method,
        headers=headers,
        body=body,
    )
    try:
        decoded = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"{method} {path} returned non-JSON response (HTTP {status_code})."
        ) from exc
    return status_code, decoded


def _create_sample_photos(directory: Path) -> list[Path]:
    """Create deterministic, textured JPEGs so uploads exercise real image files."""
    image_paths: list[Path] = []
    for index in range(5):
        image = Image.new("RGB", (960, 720), (220 - index * 5, 215, 200 + index * 4))
        draw = ImageDraw.Draw(image)
        draw.rectangle((70, 80, 890, 640), outline=(55, 70, 65), width=8)
        draw.rectangle((100 + index * 5, 110, 440, 600), fill=(165, 125, 86), outline=(35, 40, 38), width=5)
        draw.rectangle((500, 130 + index * 3, 825, 360), fill=(88, 130, 150), outline=(30, 42, 48), width=7)
        draw.polygon(
            [(180, 500), (300 + index * 4, 410), (420, 500), (400, 580), (200, 580)],
            fill=(105, 82 + index * 4, 70),
            outline=(30, 35, 30),
        )
        for x in range(120, 850, 45):
            y = 385 + ((x // 45 + index) % 5) * 18
            draw.ellipse((x, y, x + 10, y + 10), fill=(45, 55, 48))
        path = directory / f"capture_{index + 1}.jpg"
        image.save(path, format="JPEG", quality=92)
        image_paths.append(path)
    return image_paths


def _multipart_photos(paths: list[Path]) -> tuple[str, bytes]:
    boundary = f"roomsage-{uuid4().hex}"
    chunks: list[bytes] = []
    for path in paths:
        chunks.extend(
            (
                f"--{boundary}\r\n".encode(),
                (
                    'Content-Disposition: form-data; name="files"; '
                    f'filename="{path.name}"\r\n'
                ).encode(),
                b"Content-Type: image/jpeg\r\n\r\n",
                path.read_bytes(),
                b"\r\n",
            )
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return boundary, b"".join(chunks)


def _check_health(base_url: str) -> Any:
    LOGGER.info("Checking API gateway at %s", base_url)
    status_code, payload = _json_request(base_url, "/health")
    assert status_code == 200, f"API health check returned HTTP {status_code}: {payload}"
    assert payload.get("status") == "ok", f"Unexpected API health response: {payload}"

    LOGGER.info("Checking Redis connectivity")
    redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=5)
    try:
        assert redis_client.ping(), "Redis PING returned false."
    finally:
        redis_client.close()

    LOGGER.info("Checking S3/MinIO connectivity and bucket %r", settings.s3_bucket)
    client_options: dict[str, Any] = {"region_name": settings.aws_region}
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        client_options["aws_access_key_id"] = settings.aws_access_key_id
        client_options["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.s3_endpoint_url:
        client_options["endpoint_url"] = settings.s3_endpoint_url
    s3_client = boto3.client("s3", **client_options)
    try:
        s3_client.head_bucket(Bucket=settings.s3_bucket)
    except (BotoCoreError, ClientError) as exc:
        raise AssertionError(
            f"Could not access S3 bucket {settings.s3_bucket!r}: {exc}"
        ) from exc
    return s3_client


def _upload_sample_photos(base_url: str, project_id: str, paths: list[Path]) -> None:
    boundary, body = _multipart_photos(paths)
    LOGGER.info("Uploading %d sample capture photos", len(paths))
    status_code, response_body, _ = _http_request(
        f"{base_url.rstrip('/')}/api/v1/projects/{project_id}/photos",
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        body=body,
        timeout=120,
    )
    assert status_code == 200, (
        f"Photo upload failed (HTTP {status_code}): "
        f"{response_body.decode(errors='replace')}"
    )
    project = json.loads(response_body)
    assert len(project.get("photo_keys", [])) == 5, (
        f"Expected 5 photo keys in upload response, received {project.get('photo_keys')}."
    )


def _poll_job(
    base_url: str,
    job_id: str,
    *,
    timeout_seconds: int,
    poll_interval_seconds: float,
    allow_initial_not_found: bool = False,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status = "not yet visible"
    while time.monotonic() < deadline:
        status_code, job = _json_request(base_url, f"/api/v1/jobs/{job_id}")
        if status_code == 404 and allow_initial_not_found:
            LOGGER.info("Job %s is queued; waiting for its worker to persist the job", job_id)
            time.sleep(poll_interval_seconds)
            continue
        assert status_code == 200, f"Polling job {job_id} returned HTTP {status_code}: {job}"
        last_status = str(job.get("status", "UNKNOWN")).upper()
        LOGGER.info(
            "Job %s: status=%s progress=%s%%",
            job_id,
            last_status,
            job.get("progress", "?"),
        )
        if last_status in TERMINAL_STATUSES:
            if last_status == "FAILED":
                raise AssertionError(
                    f"Job {job_id} failed: {job.get('error_message') or job.get('logs')}"
                )
            return job
        time.sleep(poll_interval_seconds)
    raise TimeoutError(
        f"Job {job_id} did not finish in {timeout_seconds} seconds; last status={last_status}."
    )


def _download_and_verify(url: str, expected_name: str, *, expect_png: bool = False) -> int:
    assert url and url.startswith(("http://", "https://")), (
        f"{expected_name} URL is missing or invalid: {url!r}"
    )
    assert expected_name in unquote(urlsplit(url).path), (
        f"Expected {expected_name!r} in returned artifact URL: {url}"
    )
    request = Request(url, headers={"Range": "bytes=0-7"}, method="GET")
    try:
        response_context = urlopen(request, timeout=60)
    except HTTPError as exc:
        response_context = exc
    except URLError as exc:
        raise AssertionError(f"Could not download {expected_name}: {exc.reason}") from exc
    with response_context as response:
        status_code = response.status
        content = response.read(8)
        content_range = response.headers.get("Content-Range")
        content_length = response.headers.get("Content-Length")
    assert status_code in {200, 206}, (
        f"Downloading {expected_name} returned HTTP {status_code}."
    )
    assert content, f"Downloaded artifact {expected_name} is empty."
    if content_range and "/" in content_range:
        total_size = int(content_range.rsplit("/", 1)[1])
    else:
        total_size = int(content_length or len(content))
    assert total_size > 0, f"Downloaded artifact {expected_name} is empty."
    if expect_png:
        assert content.startswith(b"\x89PNG\r\n\x1a\n"), (
            f"Depth-map artifact {expected_name} is not a PNG."
        )
    LOGGER.info("Verified non-empty %s (%d bytes)", expected_name, total_size)
    return total_size


def _verify_reconstruction(job: dict[str, Any]) -> None:
    output_urls = job.get("output_urls")
    assert isinstance(output_urls, list), f"output_urls is not a list: {output_urls!r}"
    splat_urls = [
        url for url in output_urls
        if isinstance(url, str) and "before_model.splat" in unquote(urlsplit(url).path)
    ]
    assert splat_urls, f"Completed reconstruction did not return before_model.splat URL: {job}"
    _download_and_verify(splat_urls[0], "before_model.splat")

    depth_url = job.get("depth_map_url")
    assert job.get("depth_map_available") is True and depth_url, (
        "Completed reconstruction did not return an available depth_map.png URL."
    )
    _download_and_verify(depth_url, "depth_map.png", expect_png=True)


def _check_catalog_image(s3_client: Any, object_key: str) -> None:
    LOGGER.info("Checking catalog image s3://%s/%s", settings.s3_bucket, object_key)
    try:
        response = s3_client.head_object(Bucket=settings.s3_bucket, Key=object_key)
    except (BotoCoreError, ClientError) as exc:
        raise AssertionError(
            f"Required TripoSR catalog image s3://{settings.s3_bucket}/{object_key} "
            "is not available. Upload it before running the Stage 4 check."
        ) from exc
    assert response.get("ContentLength", 0) > 0, (
        f"Catalog image s3://{settings.s3_bucket}/{object_key} is empty."
    )


def _dispatch_edit(
    base_url: str,
    project_id: str,
    *,
    catalog_image_key: str,
    target_scale: float,
) -> dict[str, Any]:

    payload = {
        "project_id": project_id,
        "style_prompt": "Warm Scandinavian style with light oak wood",
        "insert_catalog_image_key": catalog_image_key,
        "target_position": [0.0, 0.0, 0.5],
        "target_scale": target_scale,
    }
    LOGGER.info("Dispatching Stage 4 edit job through the API")
    status_code, response = _json_request(
        base_url,
        "/api/v1/jobs/edit",
        method="POST",
        payload=payload,
    )
    assert status_code == 202, f"Edit dispatch failed (HTTP {status_code}): {response}"
    assert response.get("status") == "QUEUED", f"Unexpected edit job response: {response}"
    assert response.get("polling_url"), f"Edit response has no polling URL: {response}"
    return response


def run(args: argparse.Namespace) -> None:
    base_url = args.api_url.rstrip("/")
    s3_client = _check_health(base_url)
    _check_catalog_image(s3_client, args.catalog_image_key)

    with tempfile.TemporaryDirectory(prefix="roomsage-e2e-") as temp_dir:
        photo_paths = _create_sample_photos(Path(temp_dir))
        LOGGER.info("Creating E2E test project")
        status_code, project = _json_request(
            base_url,
            "/api/v1/projects",
            method="POST",
            payload={
                "name": f"RoomSage E2E {uuid4().hex[:8]}",
                "room_type": "living_room",
            },
        )
        assert status_code == 201, f"Project creation failed (HTTP {status_code}): {project}"
        project_id = project.get("id")
        assert project_id, f"Project creation response has no ID: {project}"
        LOGGER.info("Created project %s", project_id)

        _upload_sample_photos(base_url, project_id, photo_paths)

        LOGGER.info("Starting Stage 1/2 reconstruction")
        status_code, reconstruction = _json_request(
            base_url,
            "/api/v1/jobs/reconstruct",
            method="POST",
            payload={"project_id": project_id},
        )
        assert status_code == 202, (
            f"Reconstruction dispatch failed (HTTP {status_code}): {reconstruction}"
        )
        reconstruction_job_id = reconstruction.get("job_id")
        assert reconstruction_job_id, (
            f"Reconstruction response has no job ID: {reconstruction}"
        )
        reconstruction_result = _poll_job(
            base_url,
            reconstruction_job_id,
            timeout_seconds=args.reconstruction_timeout,
            poll_interval_seconds=args.poll_interval,
        )
        _verify_reconstruction(reconstruction_result)

        edit_response = _dispatch_edit(
            base_url,
            project_id,
            catalog_image_key=args.catalog_image_key,
            target_scale=args.target_scale,
        )
        edit_job_id = edit_response["job_id"]
        edit_result = _poll_job(
            base_url,
            edit_job_id,
            timeout_seconds=args.edit_timeout,
            poll_interval_seconds=args.poll_interval,
            allow_initial_not_found=True,
        )
        after_urls = edit_result.get("output_urls")
        assert isinstance(after_urls, list), f"Edit output_urls is not a list: {after_urls!r}"
        after_splat_urls = [
            url for url in after_urls
            if isinstance(url, str) and "after_model.splat" in unquote(urlsplit(url).path)
        ]
        assert after_splat_urls, (
            f"Completed edit did not return after_model.splat URL: {edit_result}"
        )
        _download_and_verify(after_splat_urls[0], "after_model.splat")

    LOGGER.info("PASS: RoomSage reconstruction and Stage 4 E2E workflow completed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.getenv("ROOMSAGE_API_URL", "http://localhost:8000"),
        help="FastAPI base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--catalog-image-key",
        default=os.getenv("ROOMSAGE_CATALOG_IMAGE_KEY", "catalog/sample_chair.png"),
        help="Existing product image key in the configured S3 bucket.",
    )
    parser.add_argument(
        "--target-scale",
        type=float,
        default=float(os.getenv("ROOMSAGE_EDIT_TARGET_SCALE", "0.5")),
        help="Positive TripoSR insertion scale (default: %(default)s)",
    )
    parser.add_argument(
        "--reconstruction-timeout",
        type=int,
        default=int(os.getenv("ROOMSAGE_RECONSTRUCTION_TIMEOUT", "7200")),
        help="Maximum reconstruction wait in seconds.",
    )
    parser.add_argument(
        "--edit-timeout",
        type=int,
        default=int(os.getenv("ROOMSAGE_EDIT_TIMEOUT", "7200")),
        help="Maximum Stage 4 edit wait in seconds.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("ROOMSAGE_POLL_INTERVAL", "5")),
        help="Job polling interval in seconds.",
    )
    args = parser.parse_args()
    if args.target_scale <= 0:
        parser.error("--target-scale must be positive.")
    if args.reconstruction_timeout <= 0 or args.edit_timeout <= 0:
        parser.error("Job timeouts must be positive.")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive.")
    if not args.catalog_image_key.strip():
        parser.error("--catalog-image-key cannot be blank.")
    return args


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run(parse_args())
    except (AssertionError, BotoCoreError, ClientError, RuntimeError, TimeoutError) as exc:
        LOGGER.error("FAIL: %s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.error("Interrupted.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
