from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models import JobStatus, TaskType


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    room_type: str | None = Field(default=None, max_length=80)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    room_type: str | None
    photo_keys: list[str]
    depth_map_s3_key: str | None
    created_at: datetime


class JobCreate(BaseModel):
    project_id: UUID


class JobCreated(BaseModel):
    job_id: UUID
    status: JobStatus


class EditJobRequest(BaseModel):
    project_id: UUID
    style_prompt: str | None = None
    remove_mask_id: int | None = Field(default=None, strict=True)
    insert_catalog_image_key: str | None = None
    target_position: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    target_scale: float = 1.0


class EditJobResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    polling_url: str


class JobRead(BaseModel):
    job_id: UUID
    project_id: UUID
    task_type: TaskType
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    estimated_time_remaining_seconds: int | None
    logs: list[str]
    error_message: str | None = None
    output_urls: list[str] = Field(default_factory=list)
    depth_map_available: bool = False
    depth_map_url: str | None = None
    created_at: datetime
    updated_at: datetime
