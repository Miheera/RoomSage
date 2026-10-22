import json
import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
from PIL import Image
from redis import Redis
from sqlalchemy.orm import Session

from config import settings
from models import Project
from services.s3_service import create_presigned_url, get_s3_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DepthMapResult:
    s3_key: str
    url: str


def _read_transforms(processed_data_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    transforms_path = processed_data_dir / "transforms.json"
    if not transforms_path.is_file():
        raise FileNotFoundError(f"Nerfstudio camera metadata not found: {transforms_path}")
    with transforms_path.open(encoding="utf-8") as metadata_file:
        metadata = json.load(metadata_file)
    frames = metadata.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError(f"No camera frames found in {transforms_path}.")
    return metadata, frames


def _select_primary_camera(frames: list[dict[str, Any]]) -> dict[str, Any]:
    valid_frames: list[tuple[dict[str, Any], np.ndarray]] = []
    for frame in frames:
        transform = np.asarray(frame.get("transform_matrix"), dtype=np.float64)
        if transform.shape == (4, 4) and np.isfinite(transform[:3, 3]).all():
            valid_frames.append((frame, transform))
    if not valid_frames:
        raise ValueError("Camera metadata contains no valid 4x4 transform matrices.")

    centers = np.stack([transform[:3, 3] for _, transform in valid_frames])
    scene_center = np.median(centers, axis=0)
    distances = np.linalg.norm(centers - scene_center, axis=1)
    return valid_frames[int(np.argmin(distances))][0]


def _camera_intrinsics(metadata: dict[str, Any], frame: dict[str, Any]) -> tuple[int, int, float, float, float, float]:
    width = int(frame.get("w", metadata.get("w", 0)))
    height = int(frame.get("h", metadata.get("h", 0)))
    if width <= 0 or height <= 0:
        raise ValueError("Camera metadata must include positive image width and height.")

    fl_x = frame.get("fl_x", metadata.get("fl_x"))
    fl_y = frame.get("fl_y", metadata.get("fl_y"))
    if fl_x is None:
        camera_angle_x = frame.get("camera_angle_x", metadata.get("camera_angle_x"))
        if camera_angle_x is None:
            raise ValueError("Camera metadata must include fl_x or camera_angle_x.")
        fl_x = width / (2 * np.tan(float(camera_angle_x) / 2))
    if fl_y is None:
        camera_angle_y = frame.get("camera_angle_y", metadata.get("camera_angle_y"))
        fl_y = (
            height / (2 * np.tan(float(camera_angle_y) / 2))
            if camera_angle_y is not None
            else fl_x
        )
    cx = float(frame.get("cx", metadata.get("cx", width / 2)))
    cy = float(frame.get("cy", metadata.get("cy", height / 2)))
    return width, height, float(fl_x), float(fl_y), cx, cy


def _render_depth_tensor(
    config_path: Path,
    camera_frame: dict[str, Any],
    metadata: dict[str, Any],
) -> np.ndarray:
    try:
        torch = importlib.import_module("torch")
        cameras_module = importlib.import_module("nerfstudio.cameras.cameras")
        eval_setup = importlib.import_module("nerfstudio.utils.eval_utils").eval_setup
    except ImportError as exc:
        raise RuntimeError(
            "Depth rendering requires Nerfstudio and PyTorch in the Celery worker environment."
        ) from exc

    width, height, fl_x, fl_y, cx, cy = _camera_intrinsics(metadata, camera_frame)
    transform = np.asarray(camera_frame["transform_matrix"], dtype=np.float32)
    camera_to_worlds = torch.as_tensor(transform[:3, :4]).unsqueeze(0)
    camera = cameras_module.Cameras(
        camera_to_worlds=camera_to_worlds,
        fx=torch.tensor([fl_x], dtype=torch.float32),
        fy=torch.tensor([fl_y], dtype=torch.float32),
        cx=torch.tensor([cx], dtype=torch.float32),
        cy=torch.tensor([cy], dtype=torch.float32),
        width=torch.tensor([width], dtype=torch.int64),
        height=torch.tensor([height], dtype=torch.int64),
    )

    _, pipeline, _, _ = eval_setup(config_path, test_mode="inference")
    pipeline.eval()
    camera = camera.to(pipeline.device)
    with torch.inference_mode():
        outputs = pipeline.model.get_outputs_for_camera(camera)
    if "depth" not in outputs:
        raise RuntimeError("The trained Nerfstudio model did not return a depth output.")
    depth = outputs["depth"].detach().squeeze().float().cpu().numpy()
    if depth.ndim != 2:
        raise ValueError(f"Expected a 2D depth tensor, received shape {depth.shape}.")
    return depth


def _save_normalized_depth(depth: np.ndarray, output_path: Path) -> None:
    valid = np.isfinite(depth) & (depth > 0)
    if not valid.any():
        raise ValueError("Rendered depth tensor contains no finite positive depth values.")
    near, far = np.percentile(depth[valid], (1, 99))
    grayscale = np.zeros(depth.shape, dtype=np.uint8)
    if far <= near:
        grayscale[valid] = 255
    else:
        normalized = 1.0 - np.clip((depth[valid] - near) / (far - near), 0.0, 1.0)
        grayscale[valid] = np.rint(normalized * 255).astype(np.uint8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(grayscale).save(output_path, format="PNG")


def _restore_project_metadata(
    db: Session,
    redis_client: Redis,
    project_id: UUID,
    previous_s3_key: str | None,
) -> None:
    project = db.get(Project, project_id)
    if project is not None:
        project.depth_map_s3_key = previous_s3_key
        db.commit()
    redis_key = f"roomsage:project:{project_id}"
    if previous_s3_key is None:
        redis_client.hdel(redis_key, "depth_map_s3_key")
    else:
        redis_client.hset(redis_key, "depth_map_s3_key", previous_s3_key)


def create_depth_map(
    project_id: str,
    processed_data_dir: Path,
    config_path: Path,
    output_path: Path,
    db: Session,
    redis_client: Redis,
) -> DepthMapResult:
    project_uuid = UUID(project_id)
    project = db.get(Project, project_uuid)
    if project is None:
        raise LookupError(f"Project {project_id} does not exist.")
    previous_s3_key = project.depth_map_s3_key

    metadata, frames = _read_transforms(processed_data_dir)
    primary_camera = _select_primary_camera(frames)
    depth = _render_depth_tensor(config_path, primary_camera, metadata)
    _save_normalized_depth(depth, output_path)

    s3_key = f"{project_id}/outputs/depth_map.png"
    s3 = get_s3_client()
    s3.upload_file(
        str(output_path),
        settings.s3_bucket,
        s3_key,
        ExtraArgs={"ContentType": "image/png"},
    )
    try:
        project.depth_map_s3_key = s3_key
        db.commit()
        redis_key = f"roomsage:project:{project_id}"
        redis_client.hset(redis_key, "depth_map_s3_key", s3_key)
        url = create_presigned_url(s3_key)
    except Exception:
        db.rollback()
        if previous_s3_key != s3_key:
            try:
                _restore_project_metadata(db, redis_client, project_uuid, previous_s3_key)
            except Exception:
                db.rollback()
                logger.exception("Could not restore depth-map metadata for project %s", project_id)
            try:
                s3.delete_object(Bucket=settings.s3_bucket, Key=s3_key)
            except Exception:
                logger.exception("Could not clean up depth map object %s", s3_key)
        raise

    logger.info("Depth map generated for project %s at s3://%s/%s", project_id, settings.s3_bucket, s3_key)
    return DepthMapResult(s3_key=s3_key, url=url)
