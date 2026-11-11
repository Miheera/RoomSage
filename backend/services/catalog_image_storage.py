"""Persistent storage for verified, mirrored catalog reference images."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError

from config import settings
from services.s3_service import get_s3_client


def uses_local_catalog_storage() -> bool:
    return settings.environment.strip().casefold() in {"development", "dev", "test", "testing", "local"}


def _root() -> Path:
    configured = Path(settings.catalog_image_storage_dir)
    return configured if configured.is_absolute() else Path(__file__).resolve().parents[1] / configured


def _path(key: str) -> Path:
    root = _root().resolve()
    target = (root / key).resolve()
    if target == root or root not in target.parents:
        raise ValueError("Catalog image key must be a safe relative path.")
    return target


def put_catalog_image(key: str, content: bytes) -> None:
    if uses_local_catalog_storage():
        target = _path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        return
    get_s3_client().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=content, ContentType="image/jpeg",
        CacheControl="public, max-age=31536000",
    )


def get_catalog_image(key: str) -> bytes:
    if uses_local_catalog_storage():
        return _path(key).read_bytes()
    response = get_s3_client().get_object(Bucket=settings.s3_bucket, Key=key)
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def catalog_image_exists(key: str) -> bool:
    if uses_local_catalog_storage():
        return _path(key).is_file()
    try:
        get_s3_client().head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return False
        raise
