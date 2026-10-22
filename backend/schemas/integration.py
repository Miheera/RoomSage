import math
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator, model_validator

from models import JobStatus


class ControlNetHandoff(BaseModel):
    """3D/backend data supplied to the ControlNet redesign service."""

    project_id: UUID
    depth_map_url: AnyHttpUrl
    camera_transform: list[list[float]] = Field(
        description="Finite 4x4 camera-to-world transform matrix."
    )
    room_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("camera_transform")
    @classmethod
    def validate_camera_transform(
        cls,
        transform: list[list[float]],
    ) -> list[list[float]]:
        if len(transform) != 4 or any(len(row) != 4 for row in transform):
            raise ValueError("camera_transform must be a 4x4 matrix.")
        if any(not math.isfinite(value) for row in transform for value in row):
            raise ValueError("camera_transform values must be finite.")
        return transform


class CatalogItemDetails(BaseModel):
    """Selected catalog item's display details exchanged with the backend."""

    catalog_item_id: str | None = None
    name: str
    category: str | None = None
    description: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CatalogEditPayload(BaseModel):
    """Catalog selection and placement recommendation supplied to the backend."""

    project_id: UUID
    selected_item: CatalogItemDetails
    insert_catalog_image_key: str = Field(min_length=1)
    target_position: tuple[float, float, float]
    style_prompt: str | None = None

    @field_validator("insert_catalog_image_key", "style_prompt")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Value must not be blank.")
        return normalized

    @field_validator("target_position")
    @classmethod
    def validate_target_position(
        cls,
        position: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        if any(not math.isfinite(value) for value in position):
            raise ValueError("target_position coordinates must be finite.")
        return position


class JobExecutionMetrics(BaseModel):
    job_id: UUID
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    estimated_time_remaining_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_timestamps(self) -> "JobExecutionMetrics":
        if (
            self.started_at is not None
            and self.completed_at is not None
            and self.completed_at < self.started_at
        ):
            raise ValueError("completed_at must not be earlier than started_at.")
        return self


class Viewer3DState(BaseModel):
    """Model URLs, interactive segmentation IDs, and job metrics for the 3D viewer."""

    project_id: UUID
    before_model_url: AnyHttpUrl
    after_model_url: AnyHttpUrl | None = None
    sam_mask_ids: list[int] = Field(default_factory=list)
    job_metrics: JobExecutionMetrics

    @field_validator("sam_mask_ids")
    @classmethod
    def validate_mask_ids(cls, mask_ids: list[int]) -> list[int]:
        if any(mask_id < 0 for mask_id in mask_ids):
            raise ValueError("sam_mask_ids must contain only non-negative integers.")
        if len(mask_ids) != len(set(mask_ids)):
            raise ValueError("sam_mask_ids must not contain duplicates.")
        return mask_ids
