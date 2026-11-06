"""Async client for Meshy's image-to-3D task API."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from botocore.exceptions import ClientError

from config import settings
from services.s3_service import create_presigned_url, get_s3_client

logger = logging.getLogger(__name__)


class MeshyAPIError(RuntimeError):
    """Meshy rejected a request or reported a failed synthesis task."""


class MeshyService:
    BASE_URL = "https://api.meshy.ai/openapi/v1/image-to-3d"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        s3_client: Any | None = None,
        mock_meshy: bool | None = None,
        local_cache_dir: str | Path | None = None,
        poll_interval_seconds: float | None = None,
        task_timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.meshy_api_key
        if not self.api_key:
            raise ValueError("MESHY_API_KEY must be configured to use MeshyService.")
        self._client = client
        self.s3_client = s3_client
        self.mock_meshy = settings.mock_meshy if mock_meshy is None else mock_meshy
        self.local_cache_dir = Path(local_cache_dir or settings.meshy_local_cache_dir)
        self.poll_interval_seconds = (
            poll_interval_seconds if poll_interval_seconds is not None
            else settings.meshy_poll_interval_seconds
        )
        self.task_timeout_seconds = (
            task_timeout_seconds if task_timeout_seconds is not None
            else settings.meshy_task_timeout_seconds
        )
        if self.poll_interval_seconds <= 0 or self.task_timeout_seconds <= 0:
            raise ValueError("Meshy polling interval and timeout must be positive.")

    @staticmethod
    def should_generate_3d(category: str | None) -> bool:
        return (category or "").strip().casefold() not in {"paint", "flooring", "rug", "rugs"}

    def _s3(self) -> Any:
        return self.s3_client if self.s3_client is not None else get_s3_client()

    def _s3_exists(self, key: str) -> bool:
        client = self._s3()
        if hasattr(client, "exists"):
            return bool(client.exists(key))
        try:
            client.head_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def _s3_url(self, key: str) -> str:
        client = self._s3()
        if hasattr(client, "get_public_url"):
            return str(client.get_public_url(key))
        return create_presigned_url(key)

    async def generate_catalog_3d_asset(
        self,
        product_id: str,
        image_url: str,
        category: str | None = None,
    ) -> str | None:
        """Return an existing/generated catalog mesh, caching it in local storage and S3."""
        if not product_id.strip():
            raise ValueError("product_id must not be blank.")
        if not self.should_generate_3d(category):
            logger.info("Skipping Meshy generation for non-mesh category %s.", category)
            return None
        if self.mock_meshy:
            logger.info("[MOCK MODE] Returning sample local 3D GLB mesh.")
            return "http://localhost:9000/roomsage/catalog_3d/sample_sofa.glb"

        safe_product_id = quote(product_id, safe="-_.")
        s3_key = f"catalog_3d/{safe_product_id}.glb"
        local_path = self.local_cache_dir / f"{safe_product_id}.glb"
        if local_path.is_file():
            logger.info("Mesh cache hit for product %s in local storage.", product_id)
            return local_path.resolve().as_uri()
        if self._s3_exists(s3_key):
            logger.info("Mesh cache hit for product %s in S3.", product_id)
            return self._s3_url(s3_key)

        logger.info("Calling Meshy API for new product %s.", product_id)
        task_id = await self.create_image_to_3d_task(image_url)
        glb_url = await self.poll_task_completion(task_id)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        # Meshy model URLs are signed URLs; never forward the Meshy bearer token to them.
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as download_client:
            try:
                response = await download_client.get(glb_url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise MeshyAPIError(f"Could not download generated GLB: {exc}") from exc
        model_bytes = response.content
        await asyncio.to_thread(local_path.write_bytes, model_bytes)
        s3 = self._s3()
        if hasattr(s3, "download_and_upload"):
            await s3.download_and_upload(glb_url, s3_key)
        else:
            await asyncio.to_thread(
                s3.put_object,
                Bucket=settings.s3_bucket,
                Key=s3_key,
                Body=model_bytes,
                ContentType="model/gltf-binary",
            )
        return self._s3_url(s3_key)

    async def _request_client(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(60.0),
        ), True

    async def create_image_to_3d_task(self, image_url: str) -> str:
        """Submit an image URL and return the Meshy task ID."""
        if not image_url.strip():
            raise ValueError("image_url must not be blank.")
        client, should_close = await self._request_client()
        try:
            response = await client.post(
                self.BASE_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "image_url": image_url,
                    "ai_model": "meshy-7.1",
                    "should_texture": True,
                    "enable_pbr": True,
                    "target_formats": ["glb"],
                },
            )
            response.raise_for_status()
            payload: Any = response.json()
        except httpx.HTTPStatusError as exc:
            raise MeshyAPIError(
                f"Meshy task creation failed ({exc.response.status_code}): {exc.response.text}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MeshyAPIError(f"Meshy task creation request failed: {exc}") from exc
        finally:
            if should_close:
                await client.aclose()
        task_id = payload.get("result") or payload.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise MeshyAPIError("Meshy task creation response did not contain a task ID.")
        return task_id

    async def poll_task_completion(self, task_id: str) -> str:
        """Poll until completion and return the signed GLB download URL."""
        if not task_id.strip():
            raise ValueError("task_id must not be blank.")
        url = f"{self.BASE_URL}/{quote(task_id, safe='')}"
        client, should_close = await self._request_client()
        deadline = time.monotonic() + self.task_timeout_seconds
        try:
            while True:
                try:
                    response = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    response.raise_for_status()
                    payload: dict[str, Any] = response.json()
                except httpx.HTTPStatusError as exc:
                    raise MeshyAPIError(
                        f"Meshy task polling failed ({exc.response.status_code}): {exc.response.text}"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise MeshyAPIError(f"Meshy task polling request failed: {exc}") from exc

                status = str(payload.get("status", "")).upper()
                if status == "SUCCEEDED":
                    model_urls = payload.get("model_urls") or {}
                    glb_url = model_urls.get("glb") if isinstance(model_urls, dict) else None
                    if not glb_url:
                        # Some response variants expose generated files by target format.
                        formats = payload.get("target_formats") or []
                        for item in formats:
                            if isinstance(item, dict) and str(item.get("format", "")).lower() == "glb":
                                glb_url = item.get("url")
                                break
                    if isinstance(glb_url, str) and glb_url.strip():
                        return glb_url
                    raise MeshyAPIError("Meshy task succeeded but did not include a GLB URL.")
                if status in {"FAILED", "CANCELED", "CANCELLED", "EXPIRED"}:
                    detail = payload.get("task_error") or payload.get("error") or "No error details provided."
                    raise MeshyAPIError(f"Meshy task {task_id} ended with status {status}: {detail}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Meshy task {task_id} did not complete within the configured timeout.")
                await asyncio.sleep(min(self.poll_interval_seconds, remaining))
        finally:
            if should_close:
                await client.aclose()
