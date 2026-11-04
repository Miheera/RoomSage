"""Mirror of Person A's request and response schemas.

Taken from Miheera/RoomSage backend/schemas.py and backend/models.py at commit
23a2fd8. If those change, this file and tests/test_person_a.py change with them.
"""

from enum import Enum

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProjectRead(BaseModel):
    id: str
    name: str
    room_type: str | None = None
    photo_keys: list[str] = Field(default_factory=list)
    depth_map_s3_key: str | None = None
    created_at: str


class JobCreated(BaseModel):
    job_id: str
    status: JobStatus


class EditJobRequest(BaseModel):
    project_id: str
    style_prompt: str | None = None
    remove_mask_id: int | None = Field(default=None, gt=0)
    insert_catalog_image_key: str | None = None
    target_position: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])
    target_scale: float = 1.0


class RedesignJobRequest(BaseModel):
    project_id: str
    prompt: str = Field(min_length=1, max_length=2000)


class EditJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    polling_url: str


class JobRead(BaseModel):
    job_id: str
    project_id: str
    task_type: str
    status: JobStatus
    progress: int = Field(ge=0, le=100)
    estimated_time_remaining_seconds: int | None = None
    logs: list[str] = Field(default_factory=list)
    error_message: str | None = None
    output_urls: list[str] = Field(default_factory=list)
    depth_map_available: bool = False
    depth_map_url: str | None = None
    created_at: str
    updated_at: str
