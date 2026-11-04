"""Validate catalog placement and map it to the production Stage 4 request."""

import math
from pathlib import PurePosixPath

from schemas import CatalogEditPayload, EditJobRequest

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def build_catalog_edit_request(payload: CatalogEditPayload) -> EditJobRequest:
    key = payload.insert_catalog_image_key
    parts = key.replace("\\", "/").split("/")
    if key.startswith(("/", "\\")) or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Catalog image key must be a safe relative S3 object key.")
    if PurePosixPath(key).suffix.lower() not in _IMAGE_SUFFIXES:
        raise ValueError("Catalog image key must point to JPEG, PNG or WebP.")
    if any(not math.isfinite(value) for value in payload.target_position):
        raise ValueError("Target position must contain finite coordinates.")
    if not math.isfinite(payload.target_scale) or payload.target_scale <= 0:
        raise ValueError("Target scale must be a positive finite number.")
    return EditJobRequest(
        project_id=payload.project_id,
        insert_catalog_image_key=key,
        target_position=list(payload.target_position),
        target_scale=payload.target_scale,
        style_prompt=payload.style_prompt,
    )
