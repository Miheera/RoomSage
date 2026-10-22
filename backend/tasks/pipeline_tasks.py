import asyncio
import json
import logging
import math
import os
import re
import signal
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TextIO
from uuid import UUID, uuid4

from celery.exceptions import SoftTimeLimitExceeded
import numpy as np
from plyfile import PlyData, PlyElement
from redis import Redis
from sqlalchemy.orm import Session
import yaml

from config import settings
from database import SessionLocal
from models import Job, JobStatus, Project, TaskType
from services.depth_service import create_depth_map
from services.job_state import get_redis_client, record_job_state
from services.s3_service import create_presigned_url, get_s3_client
from services.segmentation_service import segment_gaussians
from tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]
TRAIN_STEP_RE = re.compile(r"\bStep\s+(\d+)\s*/\s*(\d+)\b", re.IGNORECASE)
SH_C0 = 0.28209479177387814
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
EDIT_ASSETS_PREFIX = "edit_assets"
MASK_PROPERTY_NAMES = ("mask_id", "sam_mask_id", "cluster_id")
MASKED_VALUE_ERROR = "PLY does not contain a supported SAM mask/cluster ID property."
STEP_RE = re.compile(r"\bStep\s+(\d+)\s*/\s*(\d+)\b", re.IGNORECASE)


def _signal_process_tree(process: subprocess.Popen[str], sig: int) -> None:
    kill_process_group = getattr(os, "killpg", None)
    if callable(kill_process_group):
        try:
            kill_process_group(process.pid, sig)
        except ProcessLookupError:
            pass
    elif sig == signal.SIGTERM:
        process.terminate()
    else:
        process.kill()


def _update_job(
    db: Session,
    job: Job,
    redis_client: Redis,
    *,
    status: JobStatus,
    progress: int,
    message: str,
    error_message: str | None = None,
    extra_fields: dict[str, str | int] | None = None,
) -> None:
    job.status = status
    job.progress = max(0, min(100, progress))
    job.updated_at = datetime.now(timezone.utc)
    if status == JobStatus.PROCESSING and job.started_at is None:
        job.started_at = datetime.now(timezone.utc)
    if status in (JobStatus.COMPLETED, JobStatus.FAILED):
        job.completed_at = datetime.now(timezone.utc)
    job.error_message = error_message
    db.commit()
    record_job_state(
        redis_client,
        str(job.id),
        status=status,
        progress=job.progress,
        message=message,
        error_message=error_message,
        extra_fields=extra_fields,
    )


def _download_project_photos(project_id: str, input_dir: Path) -> list[Path]:
    s3 = get_s3_client()
    prefix = f"{project_id}/raw_photos/"
    paginator = s3.get_paginator("list_objects_v2")
    downloaded: list[Path] = []

    for page in paginator.paginate(Bucket=settings.s3_bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            relative_name = key.removeprefix(prefix)
            extension = Path(relative_name).suffix.lower()
            if not relative_name or extension not in _IMAGE_EXTENSIONS:
                logger.warning("Skipping unsupported S3 photo object %s", key)
                continue
            if len(downloaded) >= 15:
                raise ValueError(
                    f"More than 15 supported capture photos were found under "
                    f"s3://{settings.s3_bucket}/{prefix}."
                )

            destination = input_dir / f"{len(downloaded):04d}{extension}"
            response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
            try:
                with destination.open("wb") as output:
                    body = response["Body"]
                    while chunk := body.read(1024 * 1024):
                        output.write(chunk)
            finally:
                response["Body"].close()
            if destination.stat().st_size == 0:
                destination.unlink()
                raise ValueError(f"Capture photo {key!r} is empty.")
            downloaded.append(destination)

    if not 5 <= len(downloaded) <= 15:
        raise ValueError(
            f"Expected 5-15 capture photos in s3://{settings.s3_bucket}/{prefix}; "
            f"found {len(downloaded)} supported images."
        )
    return downloaded


def _read_lines(stream: TextIO) -> Iterator[str]:
    yield from iter(stream.readline, "")


async def _run_command(
    command: list[str],
    *,
    progress_callback: Callable[[str], None] | None = None,
    cwd: Path | None = None,
) -> None:
    logger.info("Starting command: %s", command)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        start_new_session=callable(getattr(os, "killpg", None)),
        cwd=cwd,
    )
    assert process.stdout is not None
    try:
        lines = _read_lines(process.stdout)
        while True:
            line = await asyncio.to_thread(next, lines, None)
            if line is None:
                break
            line = line.rstrip()
            if line:
                logger.info("%s", line)
                if progress_callback is not None:
                    progress_callback(line)
        return_code = await asyncio.to_thread(process.wait)
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)
    except BaseException:
        if process.poll() is None:
            _signal_process_tree(process, signal.SIGTERM)
            try:
                await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=10)
            except asyncio.TimeoutError:
                _signal_process_tree(process, signal.SIGKILL)
                await asyncio.to_thread(process.wait)
        raise
    finally:
        process.stdout.close()


def _find_training_config(output_dir: Path) -> Path:
    configs = sorted(output_dir.rglob("config.yml"))
    if not configs:
        raise FileNotFoundError(
            f"Nerfstudio training completed without producing config.yml under {output_dir}."
        )
    return configs[-1]


def _find_exported_ply(export_dir: Path) -> Path:
    ply_files = sorted(export_dir.rglob("*.ply"))
    if len(ply_files) != 1:
        raise FileNotFoundError(
            f"Expected exactly one exported Gaussian PLY under {export_dir}, found {len(ply_files)}."
        )
    return ply_files[0]


def _find_latest_checkpoint(output_dir: Path) -> tuple[Path, int]:
    checkpoints = list(output_dir.rglob("*.ckpt"))
    if not checkpoints:
        raise FileNotFoundError(f"No Nerfstudio checkpoint found under {output_dir}.")

    def checkpoint_step(path: Path) -> int:
        match = re.search(r"step[-_](\d+)", path.stem, re.IGNORECASE)
        return int(match.group(1)) if match else -1

    checkpoint = max(checkpoints, key=lambda path: (checkpoint_step(path), path.stat().st_mtime))
    step = checkpoint_step(checkpoint)
    if step < 0:
        raise ValueError(f"Could not determine the training step from checkpoint name {checkpoint.name!r}.")
    return checkpoint, step


def _upload_edit_assets(
    project_id: str,
    training_config: Path,
    processed_data_dir: Path,
    training_output_dir: Path,
) -> None:
    transforms_path = processed_data_dir / "transforms.json"
    if not transforms_path.is_file():
        raise FileNotFoundError(f"COLMAP transforms file missing: {transforms_path}")
    checkpoint_path, checkpoint_step = _find_latest_checkpoint(training_output_dir)
    prefix = f"{project_id}/outputs/{EDIT_ASSETS_PREFIX}"
    bundle_prefix = f"{prefix}/{uuid4().hex}"
    checkpoint_key = f"{bundle_prefix}/checkpoints/{checkpoint_path.name}"
    manifest = {
        "config_key": f"{bundle_prefix}/config.yml",
        "transforms_key": f"{bundle_prefix}/transforms.json",
        "checkpoint_key": checkpoint_key,
        "checkpoint_step": checkpoint_step,
    }
    s3 = get_s3_client()
    uploaded_keys: list[str] = []
    try:
        for local_path, key, content_type in (
            (training_config, manifest["config_key"], "application/yaml"),
            (transforms_path, manifest["transforms_key"], "application/json"),
            (checkpoint_path, checkpoint_key, "application/octet-stream"),
        ):
            s3.upload_file(
                str(local_path),
                settings.s3_bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
            uploaded_keys.append(key)
        manifest_path = training_config.parent / "edit_assets_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_key = f"{prefix}/manifest.json"
        s3.upload_file(
            str(manifest_path),
            settings.s3_bucket,
            manifest_key,
            ExtraArgs={"ContentType": "application/json"},
        )
        uploaded_keys.append(manifest_key)
    except Exception:
        for key in uploaded_keys:
            try:
                s3.delete_object(Bucket=settings.s3_bucket, Key=key)
            except Exception:
                logger.exception("Could not clean up partial edit asset %s", key)
        raise


def _download_s3_object(s3, key: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = s3.get_object(Bucket=settings.s3_bucket, Key=key)
    try:
        with destination.open("wb") as output:
            body = response["Body"]
            while chunk := body.read(1024 * 1024):
                output.write(chunk)
    finally:
        response["Body"].close()
    if destination.stat().st_size == 0:
        destination.unlink()
        raise ValueError(f"S3 object s3://{settings.s3_bucket}/{key} is empty.")


def _download_edit_base_assets(project_id: str, workspace: Path) -> tuple[Path, Path, Path, int]:
    s3 = get_s3_client()
    prefix = f"{project_id}/outputs/{EDIT_ASSETS_PREFIX}"
    manifest_path = workspace / "manifest.json"
    _download_s3_object(s3, f"{prefix}/manifest.json", manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for field in ("config_key", "transforms_key", "checkpoint_key", "checkpoint_step"):
        if field not in manifest:
            raise ValueError(f"Edit-assets manifest is missing {field!r}.")
    if any(
        not isinstance(manifest[field], str)
        or not manifest[field].startswith(f"{prefix}/")
        or ".." in Path(manifest[field]).parts
        for field in ("config_key", "transforms_key", "checkpoint_key")
    ):
        raise ValueError("Edit-assets manifest contains an invalid object key.")

    config_path = workspace / "config.yml"
    transforms_path = workspace / "processed_data" / "transforms.json"
    checkpoint_path = workspace / "checkpoints" / Path(manifest["checkpoint_key"]).name
    _download_s3_object(s3, manifest["config_key"], config_path)
    _download_s3_object(s3, manifest["transforms_key"], transforms_path)
    _download_s3_object(s3, manifest["checkpoint_key"], checkpoint_path)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Nerfstudio config must contain a YAML mapping.")
    trainer = config.setdefault("trainer", {})
    if not isinstance(trainer, dict):
        raise ValueError("Nerfstudio config trainer field must be a mapping.")
    trainer["load_dir"] = str(checkpoint_path.parent)
    trainer["load_step"] = int(manifest["checkpoint_step"])
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    return config_path, transforms_path, checkpoint_path, int(manifest["checkpoint_step"])


def _prune_masked_gaussians(ply_path: Path, mask_id: int | str) -> int:
    ply_data = PlyData.read(str(ply_path), mmap=False)
    if len(ply_data.elements) != 1 or ply_data.elements[0].name != "vertex":
        raise ValueError("Mask pruning only supports Gaussian PLY files with a vertex element.")
    vertex_element = ply_data["vertex"]
    vertices = vertex_element.data
    property_name = next(
        (name for name in MASK_PROPERTY_NAMES if name in (vertices.dtype.names or ())),
        None,
    )
    if property_name is None:
        raise ValueError(MASKED_VALUE_ERROR)
    property_dtype = vertices.dtype.fields[property_name][0]
    try:
        target_value = np.asarray(mask_id, dtype=property_dtype).item()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Mask ID {mask_id!r} is incompatible with PLY property {property_name!r}.") from exc

    keep = vertices[property_name] != target_value
    removed_count = int((~keep).sum())
    if removed_count == 0:
        raise ValueError(f"No Gaussian primitives matched mask ID {mask_id!r}.")
    if not keep.any():
        raise ValueError("Mask pruning would remove every Gaussian from the scene.")

    pruned_element = PlyElement.describe(
        vertices[keep],
        "vertex",
        comments=vertex_element.comments,
    )
    PlyData(
        [pruned_element],
        text=ply_data.text,
        byte_order=ply_data.byte_order,
        comments=ply_data.comments,
        obj_info=ply_data.obj_info,
    ).write(str(ply_path))
    return removed_count


def _add_mesh_as_gaussians(
    ply_path: Path,
    mesh_path: Path,
    *,
    position: list[float],
    scale: float,
    gaussian_count: int,
) -> int:
    import trimesh

    ply_data = PlyData.read(str(ply_path), mmap=False)
    if len(ply_data.elements) != 1 or ply_data.elements[0].name != "vertex":
        raise ValueError("Asset insertion only supports Gaussian PLY files with a vertex element.")
    vertex_element = ply_data["vertex"]
    vertices = vertex_element.data
    fields = set(vertices.dtype.names or ())
    required_fields = {
        "x", "y", "z", "scale_0", "scale_1", "scale_2", "opacity",
        "rot_0", "rot_1", "rot_2", "rot_3", "f_dc_0", "f_dc_1", "f_dc_2",
    }
    if missing := required_fields - fields:
        raise ValueError(f"Gaussian PLY is missing required properties: {', '.join(sorted(missing))}.")

    mesh = trimesh.load(str(mesh_path), force="mesh", process=False)
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.vertices) == 0 or len(mesh.faces) == 0:
        raise ValueError(f"TripoSR output {mesh_path} does not contain a non-empty triangle mesh.")
    bounds = mesh.bounds
    extent = float(np.max(bounds[1] - bounds[0]))
    if not math.isfinite(extent) or extent <= 0:
        raise ValueError("TripoSR mesh has invalid or zero-sized bounds.")
    mesh.vertices = (mesh.vertices - mesh.bounding_box.centroid) * (scale / extent)
    mesh.vertices += np.asarray(position, dtype=np.float64)

    point_count = max(1, min(gaussian_count, 200_000))
    sampled_points, face_indices = trimesh.sample.sample_surface(mesh, point_count)
    face_colors = np.asarray(mesh.visual.face_colors)
    if face_colors.ndim != 2 or len(face_colors) != len(mesh.faces) or face_colors.shape[1] < 3:
        sampled_rgb = np.full((point_count, 3), 192, dtype=np.uint8)
    else:
        sampled_rgb = face_colors[face_indices, :3].astype(np.uint8, copy=False)

    additions = np.zeros(point_count, dtype=vertices.dtype)
    for axis, coordinate in enumerate(("x", "y", "z")):
        additions[coordinate] = sampled_points[:, axis]
    sigma = max(scale / math.sqrt(point_count), np.finfo(np.float32).tiny)
    for axis in range(3):
        additions[f"scale_{axis}"] = math.log(sigma)
    additions["opacity"] = math.log(0.99 / 0.01)
    additions["rot_0"] = 1.0
    for axis in range(1, 4):
        additions[f"rot_{axis}"] = 0.0
    for channel in range(3):
        additions[f"f_dc_{channel}"] = (
            sampled_rgb[:, channel].astype(np.float32) / 255.0 - 0.5
        ) / SH_C0

    combined = np.concatenate((vertices, additions))
    combined_element = PlyElement.describe(
        combined,
        "vertex",
        comments=vertex_element.comments,
    )
    PlyData(
        [combined_element],
        text=ply_data.text,
        byte_order=ply_data.byte_order,
        comments=ply_data.comments,
        obj_info=ply_data.obj_info,
    ).write(str(ply_path))
    return point_count


def _byte_channel(value: float) -> int:
    return max(0, min(255, round(value * 255)))


def _convert_gaussian_ply_to_splat(ply_path: Path, splat_path: Path) -> None:
    """Convert Nerfstudio's Gaussian PLY properties to the common 32-byte .splat layout."""
    vertices = PlyData.read(str(ply_path))["vertex"].data
    names = set(vertices.dtype.names or ())
    required = {
        "x", "y", "z", "scale_0", "scale_1", "scale_2", "opacity",
        "rot_0", "rot_1", "rot_2", "rot_3", "f_dc_0", "f_dc_1", "f_dc_2",
    }
    missing = required - names
    if missing:
        raise ValueError(f"Gaussian PLY is missing required properties: {', '.join(sorted(missing))}.")

    with splat_path.open("wb") as output:
        for vertex in vertices:
            position = (float(vertex["x"]), float(vertex["y"]), float(vertex["z"]))
            scale = tuple(math.exp(float(vertex[f"scale_{axis}"])) for axis in range(3))
            opacity = float(vertex["opacity"])
            if opacity >= 0:
                alpha = 1.0 / (1.0 + math.exp(-opacity))
            else:
                exp_opacity = math.exp(opacity)
                alpha = exp_opacity / (1.0 + exp_opacity)
            rgb = tuple(
                _byte_channel(0.5 + SH_C0 * float(vertex[f"f_dc_{channel}"]))
                for channel in range(3)
            )
            rotation_wxyz = tuple(float(vertex[f"rot_{axis}"]) for axis in range(4))
            norm = math.sqrt(sum(component * component for component in rotation_wxyz))
            if norm == 0:
                rotation_wxyz = (1.0, 0.0, 0.0, 0.0)
            else:
                rotation_wxyz = tuple(component / norm for component in rotation_wxyz)
            rotation_xyzw = (
                rotation_wxyz[1],
                rotation_wxyz[2],
                rotation_wxyz[3],
                rotation_wxyz[0],
            )
            rotation_bytes = tuple(
                max(0, min(255, round(128 + component * 128)))
                for component in rotation_xyzw
            )
            output.write(
                struct.pack(
                    "<3f3f4B4B",
                    *position,
                    *scale,
                    *rgb,
                    _byte_channel(alpha),
                    *rotation_bytes,
                )
            )

    if splat_path.stat().st_size == 0:
        raise ValueError("Gaussian PLY contained no vertices; generated .splat is empty.")


async def _run_pipeline(
    project_id: str,
    workspace: Path,
    update_progress: ProgressCallback,
) -> str:
    input_dir = workspace / "input_images"
    processed_dir = workspace / "processed_data"
    training_output_dir = workspace / "exports"
    artifact_dir = workspace / "artifacts"
    for directory in (input_dir, processed_dir, training_output_dir, artifact_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _download_project_photos(project_id, input_dir)

    update_progress(20, "Starting COLMAP camera pose estimation.")
    await _run_command(
        [
            "ns-process-data",
            "images",
            "--data",
            str(input_dir),
            "--output-dir",
            str(processed_dir),
        ]
    )
    update_progress(40, "COLMAP camera pose estimation completed.")

    last_reported_progress = 40

    def report_training_progress(line: str) -> None:
        nonlocal last_reported_progress
        match = TRAIN_STEP_RE.search(line)
        if match is None:
            return
        step, total = (int(value) for value in match.groups())
        if total <= 0:
            return
        progress = 40 + min(50, int(50 * step / total))
        if progress > last_reported_progress:
            update_progress(progress, f"Gaussian splatting training: step {step}/{total}.")
            last_reported_progress = progress

    await _run_command(
        [
            "ns-train",
            "splatfacto",
            "--data",
            str(processed_dir),
            "--output-dir",
            str(training_output_dir),
        ],
        progress_callback=report_training_progress,
    )
    training_config = _find_training_config(training_output_dir)
    _upload_edit_assets(
        project_id,
        training_config,
        processed_dir,
        training_output_dir,
    )
    update_progress(max(90, last_reported_progress), "Gaussian splatting training completed.")

    export_dir = artifact_dir / "gaussian_splat"
    export_dir.mkdir(parents=True, exist_ok=True)
    await _run_command(
        [
            "ns-export",
            "gaussian-splat",
            "--load-config",
            str(training_config),
            "--output-dir",
            str(export_dir),
        ]
    )
    ply_path = _find_exported_ply(export_dir)
    splat_path = artifact_dir / "before_model.splat"
    _convert_gaussian_ply_to_splat(ply_path, splat_path)

    labeled_ply_key: str | None = None
    update_progress(92, "Running optional SAM Gaussian segmentation.")
    try:
        labeled_ply_path, labeled_ply_key, cluster_count = segment_gaussians(
            project_id,
            ply_path,
            processed_dir,
        )
        ply_path = labeled_ply_path
        logger.info(
            "SAM segmentation labeled project %s with %d object clusters",
            project_id,
            cluster_count,
        )
    except Exception as exc:
        logger.warning(
            "SAM segmentation unavailable for project %s; uploading the unlabelled reconstruction: %s",
            project_id,
            exc,
            exc_info=settings.sam_enabled,
        )

    update_progress(95, "Uploading reconstruction artifacts to S3.")
    s3 = get_s3_client()
    output_prefix = f"{project_id}/outputs"
    splat_key = f"{output_prefix}/before_model.splat"
    ply_key = f"{output_prefix}/before_model.ply"
    uploaded_keys: list[str] = [labeled_ply_key] if labeled_ply_key is not None else []
    try:
        s3.upload_file(
            str(splat_path),
            settings.s3_bucket,
            splat_key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
        uploaded_keys.append(splat_key)
        s3.upload_file(
            str(ply_path),
            settings.s3_bucket,
            ply_key,
            ExtraArgs={"ContentType": "application/octet-stream"},
        )
        uploaded_keys.append(ply_key)
        if labeled_ply_key is None:
            try:
                s3.delete_object(
                    Bucket=settings.s3_bucket,
                    Key=f"{output_prefix}/before_model_labeled.ply",
                )
            except Exception:
                logger.warning(
                    "Could not remove stale SAM-labeled PLY for project %s",
                    project_id,
                    exc_info=True,
                )
    except Exception:
        for uploaded_key in uploaded_keys:
            try:
                s3.delete_object(Bucket=settings.s3_bucket, Key=uploaded_key)
            except Exception:
                logger.exception("Could not clean up partial output object %s", uploaded_key)
        raise
    return splat_key


@celery_app.task(
    name="roomsage.reconstruct_project",
    acks_late=True,
    reject_on_worker_lost=True,
)
def reconstruct_project(job_id: str, project_id: str) -> dict[str, str | bool | None]:
    db: Session = SessionLocal()
    redis_client: Redis = get_redis_client()
    job: Job | None = None
    project_lock = None
    workspace: Path | None = None
    try:
        project_uuid = UUID(project_id)
        project_id = str(project_uuid)
        job = db.get(Job, UUID(job_id))
        if job is None:
            raise LookupError(f"Job {job_id} does not exist.")
        if job.project_id != project_uuid:
            raise ValueError(f"Job {job_id} does not belong to project {project_id}.")
        if job.task_type != TaskType.GAUSSIAN_SPLATTING_TRAIN:
            raise ValueError(f"Unsupported reconstruction task type: {job.task_type.value}")

        candidate_lock = redis_client.lock(
            f"roomsage:project:{project_id}:reconstruction",
            timeout=settings.task_time_limit_seconds + 120,
            blocking_timeout=1,
        )
        if not candidate_lock.acquire(blocking=True):
            raise RuntimeError(f"Project {project_id} already has an active reconstruction.")
        project_lock = candidate_lock

        def update_progress(progress: int, message: str) -> None:
            _update_job(
                db,
                job,
                redis_client,
                status=JobStatus.PROCESSING,
                progress=progress,
                message=message,
            )

        _update_job(
            db,
            job,
            redis_client,
            status=JobStatus.PROCESSING,
            progress=1,
            message="Reconstruction pipeline started.",
        )
        workspace_root = Path("/tmp/roomsage")
        workspace = workspace_root / project_id
        workspace.mkdir(parents=True, exist_ok=True)
        output_key = asyncio.run(_run_pipeline(project_id, workspace, update_progress))

        output_url = create_presigned_url(output_key)
        job.output_key = output_key
        depth_map_available = False
        depth_map_url: str | None = None
        depth_map_path = workspace / "outputs" / "depth_map.png"
        training_config = _find_training_config(workspace / "exports")
        try:
            _update_job(
                db,
                job,
                redis_client,
                status=JobStatus.PROCESSING,
                progress=96,
                message="Rendering primary-view depth map.",
            )
            depth_result = create_depth_map(
                project_id=project_id,
                processed_data_dir=workspace / "processed_data",
                config_path=training_config,
                output_path=depth_map_path,
                db=db,
                redis_client=redis_client,
            )
            depth_map_available = True
            depth_map_url = depth_result.url
        except Exception:
            logger.exception(
                "Depth-map rendering failed for project %s; reconstruction artifacts remain available",
                project_id,
            )

        job.depth_map_available = depth_map_available
        _update_job(
            db,
            job,
            redis_client,
            status=JobStatus.COMPLETED,
            progress=100,
            message=(
                "Reconstruction completed; .splat and .ply artifacts uploaded."
                if depth_map_available
                else "Reconstruction completed; depth map is unavailable."
            ),
            extra_fields={"depth_map_available": int(depth_map_available)},
        )
        return {
            "job_id": job_id,
            "output_key": output_key,
            "output_url": output_url,
            "depth_map_available": depth_map_available,
            "depth_map_url": depth_map_url,
        }
    except Exception as exc:
        error_message = (
            "GPU reconstruction task exceeded its time limit."
            if isinstance(exc, SoftTimeLimitExceeded)
            else str(exc) or exc.__class__.__name__
        )
        logger.exception("Reconstruction job %s failed", job_id)
        if job is not None:
            try:
                _update_job(
                    db,
                    job,
                    redis_client,
                    status=JobStatus.FAILED,
                    progress=job.progress,
                    message=f"Reconstruction failed: {error_message}",
                    error_message=error_message,
                )
            except Exception:
                db.rollback()
                logger.exception("Could not persist failure state for job %s", job_id)
        raise
    finally:
        if workspace is not None:
            try:
                shutil.rmtree(workspace)
            except OSError:
                logger.exception("Could not remove reconstruction workspace %s", workspace)
        if project_lock is not None:
            try:
                project_lock.release()
            except Exception:
                logger.exception("Could not release reconstruction lock for project %s", project_id)
        redis_client.close()
        db.close()


@celery_app.task(
    bind=True,
    name="roomsage.run_3d_edit",
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_3d_edit_task(
    self,
    project_id: str,
    edit_params: Mapping[str, Any],
) -> dict[str, str | int | None]:
    if not isinstance(edit_params, Mapping):
        raise TypeError("edit_params must be a mapping.")

    style_prompt = edit_params.get("style_prompt")
    if style_prompt is not None and (
        not isinstance(style_prompt, str) or not style_prompt.strip()
    ):
        raise ValueError("style_prompt must be a non-empty string when supplied.")
    if isinstance(style_prompt, str):
        style_prompt = style_prompt.strip()

    remove_mask_id = edit_params.get("remove_mask_id")
    if remove_mask_id is not None and (
        isinstance(remove_mask_id, bool) or not isinstance(remove_mask_id, (int, str))
    ):
        raise ValueError("remove_mask_id must be an integer or string.")

    catalog_image_key = edit_params.get("insert_catalog_image_key")
    target_position = edit_params.get("target_position")
    target_scale = edit_params.get("target_scale")
    if catalog_image_key is not None:
        if not isinstance(catalog_image_key, str) or not catalog_image_key.strip():
            raise ValueError("insert_catalog_image_key must be a non-empty S3 object key.")
        if Path(catalog_image_key).is_absolute() or ".." in Path(catalog_image_key).parts:
            raise ValueError("insert_catalog_image_key must be a safe relative S3 object key.")
        if not isinstance(target_position, (list, tuple)) or len(target_position) != 3:
            raise ValueError("target_position must contain three world-space coordinates.")
        try:
            target_position = [float(coordinate) for coordinate in target_position]
            target_scale = float(target_scale)
        except (TypeError, ValueError) as exc:
            raise ValueError("target_position and target_scale must be numeric.") from exc
        if not all(math.isfinite(coordinate) for coordinate in target_position):
            raise ValueError("target_position must contain finite coordinates.")
        if not math.isfinite(target_scale) or target_scale <= 0:
            raise ValueError("target_scale must be a positive finite number.")

    if not style_prompt and remove_mask_id is None and catalog_image_key is None:
        raise ValueError("At least one edit operation must be requested.")

    project_uuid = UUID(project_id)
    project_id = str(project_uuid)
    db: Session = SessionLocal()
    redis_client: Redis = get_redis_client()
    job: Job | None = None
    project_lock = None
    workspace: Path | None = None
    uploaded_keys: list[str] = []
    job_id = str(getattr(self.request, "id", None) or "")
    try:
        try:
            requested_job_uuid = UUID(job_id)
        except ValueError:
            requested_job_uuid = uuid4()
            job_id = str(requested_job_uuid)

        project = db.get(Project, project_uuid)
        if project is None:
            raise LookupError(f"Project {project_id} does not exist.")
        job = db.get(Job, requested_job_uuid)
        if job is None:
            job = Job(
                id=requested_job_uuid,
                project_id=project_uuid,
                task_type=TaskType.INSTRUCT_GS2GS_EDIT,
                status=JobStatus.QUEUED,
            )
            db.add(job)
            db.commit()
            db.refresh(job)
        elif job.project_id != project_uuid or job.task_type != TaskType.INSTRUCT_GS2GS_EDIT:
            raise ValueError(f"Celery task ID {job_id} is not a matching 3D edit job.")

        candidate_lock = redis_client.lock(
            f"roomsage:project:{project_id}:reconstruction",
            timeout=settings.task_time_limit_seconds + 120,
            blocking_timeout=1,
        )
        if not candidate_lock.acquire(blocking=True):
            raise RuntimeError(f"Project {project_id} already has an active 3D job.")
        project_lock = candidate_lock

        def update_progress(progress: int, message: str) -> None:
            _update_job(
                db,
                job,
                redis_client,
                status=JobStatus.PROCESSING,
                progress=progress,
                message=message,
            )

        _update_job(
            db,
            job,
            redis_client,
            status=JobStatus.PROCESSING,
            progress=1,
            message="3D edit job started.",
        )

        workspace_root = Path("/tmp/roomsage") / project_id / "edit_workspace"
        workspace = workspace_root / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        config_path, transforms_path, _, _ = _download_edit_base_assets(project_id, workspace)
        processed_data_dir = transforms_path.parent
        s3 = get_s3_client()
        base_ply_path = workspace / "before_model.ply"
        _download_s3_object(s3, f"{project_id}/outputs/before_model.ply", base_ply_path)
        working_ply_path = workspace / "edited_model.ply"
        shutil.copy2(base_ply_path, working_ply_path)

        if style_prompt:
            images_dir = processed_data_dir / "images"
            _download_project_photos(project_id, images_dir)
            style_output_dir = workspace / "style_training"
            style_output_dir.mkdir(parents=True, exist_ok=True)
            last_progress = 5

            def report_style_progress(line: str) -> None:
                nonlocal last_progress
                match = STEP_RE.search(line)
                if not match:
                    return
                step, total = (int(value) for value in match.groups())
                if total <= 0:
                    return
                progress = 5 + min(65, int(65 * step / total))
                if progress > last_progress:
                    update_progress(progress, f"Style editing: step {step}/{total}.")
                    last_progress = progress

            update_progress(5, "Starting Instruct-GS2GS style editing.")
            awaitable = _run_command(
                [
                    "ns-train",
                    "igs2gs",
                    "--data",
                    str(processed_data_dir),
                    "--load-config",
                    str(config_path),
                    "--pipeline.model.edit-prompt",
                    style_prompt,
                    "--output-dir",
                    str(style_output_dir),
                ],
                progress_callback=report_style_progress,
            )
            asyncio.run(awaitable)
            styled_config = _find_training_config(style_output_dir)
            styled_export_dir = workspace / "styled_export"
            styled_export_dir.mkdir(parents=True, exist_ok=True)
            asyncio.run(
                _run_command(
                    [
                        "ns-export",
                        "gaussian-splat",
                        "--load-config",
                        str(styled_config),
                        "--output-dir",
                        str(styled_export_dir),
                    ]
                )
            )
            shutil.copy2(_find_exported_ply(styled_export_dir), working_ply_path)
            update_progress(72, "Style editing completed.")

        if remove_mask_id is not None:
            removed_count = _prune_masked_gaussians(working_ply_path, remove_mask_id)
            update_progress(76, f"Removed {removed_count} Gaussian primitives for mask {remove_mask_id}.")

        if catalog_image_key is not None:
            triposr_script = settings.triposr_run_script
            if not triposr_script:
                raise RuntimeError(
                    "Furniture insertion requested but TRIPOSR_RUN_SCRIPT is not configured."
                )
            triposr_script_path = Path(triposr_script).resolve()
            if not triposr_script_path.is_file():
                raise FileNotFoundError(f"Configured TripoSR script does not exist: {triposr_script_path}")

            s3_response = s3.get_object(Bucket=settings.s3_bucket, Key=catalog_image_key)
            content_length = s3_response.get("ContentLength")
            if content_length is not None and content_length > settings.upload_max_bytes:
                s3_response["Body"].close()
                raise ValueError("Catalog image exceeds the configured upload size limit.")
            image_suffix = Path(catalog_image_key).suffix.lower()
            if image_suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                s3_response["Body"].close()
                raise ValueError("Catalog image must be JPEG, PNG, or WebP.")
            catalog_image_path = workspace / f"catalog_item{image_suffix}"
            catalog_image_path.parent.mkdir(parents=True, exist_ok=True)
            total_bytes = 0
            try:
                with catalog_image_path.open("wb") as output:
                    body = s3_response["Body"]
                    while chunk := body.read(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > settings.upload_max_bytes:
                            raise ValueError("Catalog image exceeds the configured upload size limit.")
                        output.write(chunk)
            finally:
                s3_response["Body"].close()

            update_progress(78, "Generating 3D furniture asset with TripoSR.")
            triposr_output_dir = workspace / "triposr_output"
            triposr_output_dir.mkdir(parents=True, exist_ok=True)
            asyncio.run(
                _run_command(
                    [
                        sys.executable,
                        str(triposr_script_path),
                        str(catalog_image_path),
                        "--output-dir",
                        str(triposr_output_dir),
                    ],
                    cwd=triposr_script_path.parent,
                )
            )
            mesh_candidates = sorted(
                path
                for path in triposr_output_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in {".glb", ".obj"}
            )
            glb_candidates = [path for path in mesh_candidates if path.suffix.lower() == ".glb"]
            if len(glb_candidates) == 1:
                mesh_path = glb_candidates[0]
            elif len(mesh_candidates) == 1:
                mesh_path = mesh_candidates[0]
            else:
                raise FileNotFoundError(
                    f"Expected one TripoSR GLB/OBJ output, found {len(mesh_candidates)}."
                )
            inserted_count = _add_mesh_as_gaussians(
                working_ply_path,
                mesh_path,
                position=target_position,
                scale=target_scale,
                gaussian_count=settings.edit_asset_gaussian_count,
            )
            update_progress(88, f"Inserted furniture as {inserted_count} Gaussian primitives.")

        updated_splat_path = workspace / "after_model.splat"
        _convert_gaussian_ply_to_splat(working_ply_path, updated_splat_path)
        update_progress(94, "Uploading edited 3D model to S3.")
        output_prefix = f"{project_id}/outputs"
        output_ply_key = f"{output_prefix}/after_model.ply"
        output_splat_key = f"{output_prefix}/after_model.splat"
        try:
            s3.upload_file(
                str(working_ply_path),
                settings.s3_bucket,
                output_ply_key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )
            uploaded_keys.append(output_ply_key)
            s3.upload_file(
                str(updated_splat_path),
                settings.s3_bucket,
                output_splat_key,
                ExtraArgs={"ContentType": "application/octet-stream"},
            )
            uploaded_keys.append(output_splat_key)
            output_url = create_presigned_url(output_splat_key)
        except Exception:
            for key in uploaded_keys:
                try:
                    s3.delete_object(Bucket=settings.s3_bucket, Key=key)
                except Exception:
                    logger.exception("Could not clean up partial edit output %s", key)
            uploaded_keys.clear()
            raise

        job.output_key = output_splat_key
        _update_job(
            db,
            job,
            redis_client,
            status=JobStatus.COMPLETED,
            progress=100,
            message="3D edit completed; after-model artifacts uploaded.",
            extra_fields={"after_model_key": output_splat_key},
        )
        return {
            "job_id": job_id,
            "output_key": output_splat_key,
            "output_url": output_url,
            "gaussian_count": len(PlyData.read(str(working_ply_path))["vertex"].data),
        }
    except Exception as exc:
        error_message = (
            "3D edit task exceeded its time limit."
            if isinstance(exc, SoftTimeLimitExceeded)
            else str(exc) or exc.__class__.__name__
        )
        logger.exception("3D edit job %s for project %s failed", job_id, project_id)
        if uploaded_keys:
            try:
                s3 = get_s3_client()
                for key in uploaded_keys:
                    s3.delete_object(Bucket=settings.s3_bucket, Key=key)
                uploaded_keys.clear()
            except Exception:
                logger.exception("Could not clean up failed edit outputs for job %s", job_id)
        if job is not None:
            try:
                _update_job(
                    db,
                    job,
                    redis_client,
                    status=JobStatus.FAILED,
                    progress=job.progress,
                    message=f"3D edit failed: {error_message}",
                    error_message=error_message,
                )
            except Exception:
                db.rollback()
                logger.exception("Could not persist failure state for edit job %s", job_id)
        raise
    finally:
        if workspace is not None:
            try:
                shutil.rmtree(workspace)
            except OSError:
                logger.exception("Could not remove edit workspace %s", workspace)
        if project_lock is not None:
            try:
                project_lock.release()
            except Exception:
                logger.exception("Could not release edit lock for project %s", project_id)
        redis_client.close()
        db.close()
