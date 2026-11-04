"""Clients for Person A's 3D backend. Mock is the default; the HTTP client runs only
with PERSON_A_MODE=live. Routes are the ones in backend/routers at commit 23a2fd8."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import Settings
from app.core.errors import NotFoundError, UpstreamError, ValidationFailed
from app.integration.person_a_schemas import (
    EditJobRequest,
    EditJobResponse,
    JobCreated,
    JobRead,
    JobStatus,
    ProjectRead,
    RedesignJobRequest,
)

MIN_PHOTOS = 5
MAX_PHOTOS = 15
_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


class PersonAClient(Protocol):
    def create_project(self, name: str, room_type: str | None) -> ProjectRead: ...

    def upload_photos(self, project_id: str, photos: list[Path]) -> ProjectRead: ...

    def start_reconstruction(self, project_id: str) -> JobCreated: ...

    def start_edit(self, request: EditJobRequest) -> EditJobResponse: ...

    def start_redesign(self, request: RedesignJobRequest) -> EditJobResponse: ...

    def get_job(self, job_id: str) -> JobRead: ...


def _check_photos(photos: list[Path]) -> None:
    if not MIN_PHOTOS <= len(photos) <= MAX_PHOTOS:
        raise ValidationFailed(f"Person A needs {MIN_PHOTOS}-{MAX_PHOTOS} photos in one upload.")
    for photo in photos:
        if photo.suffix.lower() not in _IMAGE_TYPES:
            raise ValidationFailed(f"{photo.name}: Person A accepts JPEG, PNG or WebP only.")


class HttpPersonAClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(
            base_url=f"{settings.person_a_base_url.rstrip('/')}/api/v1",
            timeout=settings.person_a_timeout_seconds,
            transport=transport,
        )

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise UpstreamError("Person A's backend timed out.", {"path": path}) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("Could not reach Person A's backend.", {"path": path}) from exc
        if response.is_success:
            return response.json()
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        details = {"path": path, "upstream_status": response.status_code, "detail": detail}
        if response.status_code == 404:
            raise NotFoundError(f"Person A: {detail}", details)
        if response.status_code in (400, 413, 415, 422):
            raise ValidationFailed(f"Person A rejected the request: {detail}", details)
        raise UpstreamError(f"Person A returned {response.status_code}: {detail}", details)

    def create_project(self, name: str, room_type: str | None) -> ProjectRead:
        body = self._call("POST", "/projects", json={"name": name, "room_type": room_type})
        return ProjectRead.model_validate(body)

    def upload_photos(self, project_id: str, photos: list[Path]) -> ProjectRead:
        _check_photos(photos)
        files = [("files", (p.name, p.read_bytes(), _IMAGE_TYPES[p.suffix.lower()])) for p in photos]
        return ProjectRead.model_validate(self._call("POST", f"/projects/{project_id}/photos", files=files))

    def start_reconstruction(self, project_id: str) -> JobCreated:
        return JobCreated.model_validate(
            self._call("POST", "/jobs/reconstruct", json={"project_id": project_id}))

    def start_edit(self, request: EditJobRequest) -> EditJobResponse:
        return EditJobResponse.model_validate(
            self._call("POST", "/jobs/edit", json=request.model_dump(exclude_none=True)))

    def start_redesign(self, request: RedesignJobRequest) -> EditJobResponse:
        return EditJobResponse.model_validate(
            self._call("POST", "/jobs/redesign", json=request.model_dump()))

    def get_job(self, job_id: str) -> JobRead:
        return JobRead.model_validate(self._call("GET", f"/jobs/{job_id}"))


class MockPersonAClient:
    """In-memory stand-in that applies the same rules as Person A's routes.
    Each poll advances a job one step, ending at COMPLETED with a mock:// output."""

    def __init__(self):
        self._projects: dict[str, ProjectRead] = {}
        self._jobs: dict[str, JobRead] = {}
        self._reconstructed: set[str] = set()

    def _new_job(self, project_id: str, task_type: str) -> JobRead:
        now = datetime.now(timezone.utc).isoformat()
        job = JobRead(job_id=uuid4().hex, project_id=project_id, task_type=task_type,
                      status=JobStatus.QUEUED, progress=0, created_at=now, updated_at=now)
        self._jobs[job.job_id] = job
        return job

    def _project(self, project_id: str) -> ProjectRead:
        if project_id not in self._projects:
            raise NotFoundError("Person A: Project not found.")
        return self._projects[project_id]

    def create_project(self, name: str, room_type: str | None) -> ProjectRead:
        project = ProjectRead(id=uuid4().hex, name=name, room_type=room_type,
                              created_at=datetime.now(timezone.utc).isoformat())
        self._projects[project.id] = project
        return project

    def upload_photos(self, project_id: str, photos: list[Path]) -> ProjectRead:
        _check_photos(photos)
        project = self._project(project_id)
        project.photo_keys += [f"{project_id}/raw_photos/{uuid4().hex}{p.suffix.lower()}" for p in photos]
        return project

    def start_reconstruction(self, project_id: str) -> JobCreated:
        project = self._project(project_id)
        if not MIN_PHOTOS <= len(project.photo_keys) <= MAX_PHOTOS:
            raise ValidationFailed("Person A: A project must have between 5 and 15 uploaded photos.")
        job = self._new_job(project_id, "GAUSSIAN_SPLATTING_TRAIN")
        project.depth_map_s3_key = f"{project_id}/outputs/depth_map.png"
        self._reconstructed.add(project_id)
        return JobCreated(job_id=job.job_id, status=job.status)

    def start_redesign(self, request: RedesignJobRequest) -> EditJobResponse:
        project = self._project(request.project_id)
        if request.project_id not in self._reconstructed:
            raise NotFoundError("Person A: Completed Stage 2 reconstruction not found for project.")
        if project.depth_map_s3_key != f"{request.project_id}/outputs/depth_map.png":
            raise NotFoundError("Person A: Rendered Stage 2 depth map not found for project.")
        if not project.photo_keys:
            raise ValidationFailed("Person A: Project has no primary room photo.")
        job = self._new_job(request.project_id, "REDESIGN_2D")
        return EditJobResponse(
            job_id=job.job_id,
            status=job.status,
            polling_url=f"/api/v1/jobs/{job.job_id}",
        )

    def start_edit(self, request: EditJobRequest) -> EditJobResponse:
        if request.style_prompt is None and request.remove_mask_id is None \
                and request.insert_catalog_image_key is None:
            raise ValidationFailed("Person A: At least one edit operation must be requested.")
        self._project(request.project_id)
        if request.project_id not in self._reconstructed:
            raise NotFoundError("Person A: Completed Stage 2 reconstruction not found for project.")
        job = self._new_job(request.project_id, "INSTRUCT_GS2GS_EDIT")
        return EditJobResponse(job_id=job.job_id, status=job.status, polling_url=f"/api/v1/jobs/{job.job_id}")

    def get_job(self, job_id: str) -> JobRead:
        if job_id not in self._jobs:
            raise NotFoundError("Person A: Job not found.")
        job = self._jobs[job_id]
        if job.status is JobStatus.QUEUED:
            job.status, job.progress = JobStatus.PROCESSING, 50
        elif job.status is JobStatus.PROCESSING:
            job.status, job.progress = JobStatus.COMPLETED, 100
            filename = (
                "redesign_preview.png"
                if job.task_type == "REDESIGN_2D"
                else "after_model.splat"
            )
            job.output_urls = [f"mock://{job.project_id}/outputs/{filename}"]
        return job


def build_person_a_client(settings: Settings) -> PersonAClient:
    return HttpPersonAClient(settings) if settings.person_a_mode == "live" else MockPersonAClient()
