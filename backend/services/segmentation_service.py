import importlib
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from plyfile import PlyData, PlyElement

from config import settings
from services.s3_service import get_s3_client

logger = logging.getLogger(__name__)


def _keyframes(transforms_path: Path, maximum: int) -> list[tuple[dict[str, Any], Path]]:
    metadata = json.loads(transforms_path.read_text(encoding="utf-8"))
    frames = metadata.get("frames")
    if not isinstance(frames, list) or not frames:
        raise ValueError(f"No camera frames found in {transforms_path}.")
    if maximum <= 0:
        raise ValueError("SAM_MAX_KEYFRAMES must be greater than zero.")

    count = min(len(frames), maximum)
    indices = np.linspace(0, len(frames) - 1, count, dtype=int)
    root = transforms_path.parent.resolve()
    selected: list[tuple[dict[str, Any], Path]] = []
    for index in indices:
        frame = frames[int(index)]
        if not isinstance(frame, dict) or not isinstance(frame.get("file_path"), str):
            raise ValueError(f"Camera frame {int(index)} has no valid file_path.")
        image_path = Path(frame["file_path"])
        if not image_path.is_absolute():
            image_path = root / image_path
        image_path = image_path.resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"Keyframe image referenced by transforms.json is missing: {image_path}")
        selected.append((frame, image_path))
    return selected


def _intrinsic(
    frame: dict[str, Any],
    metadata: dict[str, Any],
    name: str,
    fallback: float | None = None,
) -> float:
    value = frame.get(name, metadata.get(name, fallback))
    if value is None or not np.isfinite(float(value)):
        raise ValueError(f"Camera metadata is missing a finite {name} value.")
    return float(value)


def _project_centers(
    centers: np.ndarray,
    frame: dict[str, Any],
    metadata: dict[str, Any],
    width: int,
    height: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pose = np.asarray(frame.get("transform_matrix"), dtype=np.float64)
    if pose.shape != (4, 4) or not np.isfinite(pose).all():
        raise ValueError("Camera transform_matrix must be a finite 4x4 matrix.")
    rotation = pose[:3, :3]
    camera_points = (centers - pose[:3, 3]) @ rotation
    depth = -camera_points[:, 2]

    frame_width = _intrinsic(frame, metadata, "w", width)
    frame_height = _intrinsic(frame, metadata, "h", height)
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("Camera image dimensions must be greater than zero.")
    scale_x = width / frame_width
    scale_y = height / frame_height
    fx = _intrinsic(frame, metadata, "fl_x") * scale_x
    fy = _intrinsic(frame, metadata, "fl_y", _intrinsic(frame, metadata, "fl_x")) * scale_y
    if fx <= 0 or fy <= 0:
        raise ValueError("Camera focal lengths must be greater than zero.")
    cx = _intrinsic(frame, metadata, "cx", frame_width / 2) * scale_x
    cy = _intrinsic(frame, metadata, "cy", frame_height / 2) * scale_y

    valid = np.isfinite(camera_points).all(axis=1) & (depth > 1e-6)
    normalized_x = np.zeros(len(centers), dtype=np.float64)
    normalized_y = np.zeros(len(centers), dtype=np.float64)
    normalized_x[valid] = camera_points[valid, 0] / depth[valid]
    normalized_y[valid] = -camera_points[valid, 1] / depth[valid]

    k1 = float(frame.get("k1", metadata.get("k1", 0.0)))
    k2 = float(frame.get("k2", metadata.get("k2", 0.0)))
    k3 = float(frame.get("k3", metadata.get("k3", 0.0)))
    p1 = float(frame.get("p1", metadata.get("p1", 0.0)))
    p2 = float(frame.get("p2", metadata.get("p2", 0.0)))
    radius_squared = normalized_x**2 + normalized_y**2
    radial = 1 + k1 * radius_squared + k2 * radius_squared**2 + k3 * radius_squared**3
    distorted_x = (
        normalized_x * radial
        + 2 * p1 * normalized_x * normalized_y
        + p2 * (radius_squared + 2 * normalized_x**2)
    )
    distorted_y = (
        normalized_y * radial
        + p1 * (radius_squared + 2 * normalized_y**2)
        + 2 * p2 * normalized_x * normalized_y
    )
    pixel_x = np.rint(fx * distorted_x + cx).astype(np.int64)
    pixel_y = np.rint(fy * distorted_y + cy).astype(np.int64)
    valid &= (
        (pixel_x >= 0) & (pixel_x < width) &
        (pixel_y >= 0) & (pixel_y < height)
    )
    return pixel_x, pixel_y, valid


def _assign_consistent_labels(
    frame_assignments: list[np.ndarray],
    frame_visibility: list[np.ndarray],
    mask_counts: list[int],
) -> np.ndarray:
    if not frame_assignments:
        raise ValueError("SAM did not produce any usable instance masks.")
    mask_observations: list[tuple[int, int]] = []
    observation_ids: dict[tuple[int, int], int] = {}
    for frame_id, (assignments, count) in enumerate(zip(frame_assignments, mask_counts, strict=True)):
        populated = np.bincount(assignments[assignments >= 0], minlength=count)
        for mask_id in np.flatnonzero(populated):
            if populated[mask_id]:
                observation_ids[(frame_id, mask_id)] = len(mask_observations)
                mask_observations.append((frame_id, int(mask_id)))
    if not mask_observations:
        raise ValueError("SAM masks did not overlap any projected Gaussian centers.")

    parents = list(range(len(mask_observations)))

    def find(value: int) -> int:
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(first: int, second: int) -> None:
        left = find(first)
        right = find(second)
        if left != right:
            parents[right] = left

    for first_frame in range(len(frame_assignments)):
        for second_frame in range(first_frame + 1, len(frame_assignments)):
            first = frame_assignments[first_frame]
            second = frame_assignments[second_frame]
            overlap = (first >= 0) & (second >= 0)
            if not overlap.any():
                continue
            second_count = mask_counts[second_frame]
            pairs, intersections = np.unique(
                first[overlap].astype(np.int64) * second_count + second[overlap],
                return_counts=True,
            )
            first_sizes = np.bincount(first[first >= 0], minlength=mask_counts[first_frame])
            second_sizes = np.bincount(second[second >= 0], minlength=second_count)
            for pair, intersection in zip(pairs, intersections, strict=True):
                first_mask = int(pair // second_count)
                second_mask = int(pair % second_count)
                first_id = observation_ids.get((first_frame, first_mask))
                second_id = observation_ids.get((second_frame, second_mask))
                if first_id is None or second_id is None:
                    continue
                union_size = (
                    int(first_sizes[first_mask])
                    + int(second_sizes[second_mask])
                    - int(intersection)
                )
                if intersection >= 8 and union_size > 0 and intersection / union_size >= 0.15:
                    union(first_id, second_id)

    root_labels: dict[int, int] = {}
    for index in range(len(mask_observations)):
        root = find(index)
        if root not in root_labels:
            root_labels[root] = len(root_labels) + 1

    vote_point_ids: list[np.ndarray] = []
    vote_cluster_ids: list[np.ndarray] = []
    for frame_id, assignments in enumerate(frame_assignments):
        visible_points = np.flatnonzero(assignments >= 0)
        if visible_points.size == 0:
            continue
        local_labels = np.zeros(mask_counts[frame_id], dtype=np.int32)
        for mask_id in range(mask_counts[frame_id]):
            observation_id = observation_ids.get((frame_id, mask_id))
            if observation_id is not None:
                local_labels[mask_id] = root_labels[find(observation_id)]
        vote_point_ids.append(visible_points)
        vote_cluster_ids.append(local_labels[assignments[visible_points]])
    if not vote_point_ids:
        raise ValueError("SAM masks did not overlap any projected Gaussian centers.")

    for frame_id, visible in enumerate(frame_visibility):
        background_points = np.flatnonzero(visible & (frame_assignments[frame_id] < 0))
        if background_points.size:
            vote_point_ids.append(background_points)
            vote_cluster_ids.append(np.zeros(background_points.size, dtype=np.int32))

    points = np.concatenate(vote_point_ids).astype(np.int64, copy=False)
    labels = np.concatenate(vote_cluster_ids).astype(np.int64, copy=False)
    label_stride = len(root_labels) + 1
    encoded = points * label_stride + labels
    unique_votes, counts = np.unique(encoded, return_counts=True)
    point_ids = unique_votes // label_stride
    cluster_ids = unique_votes % label_stride
    order = np.lexsort((cluster_ids, -counts, point_ids))
    sorted_points = point_ids[order]
    sorted_clusters = cluster_ids[order]
    first_for_point = np.r_[True, sorted_points[1:] != sorted_points[:-1]]

    result = np.zeros(len(frame_assignments[0]), dtype=np.int32)
    result[sorted_points[first_for_point]] = sorted_clusters[first_for_point].astype(np.int32)
    return result


def _write_mask_property(ply_path: Path, labels: np.ndarray, output_path: Path) -> None:
    ply_data = PlyData.read(str(ply_path), mmap=False)
    try:
        vertex_element = ply_data["vertex"]
    except KeyError as exc:
        raise ValueError("Gaussian PLY must contain a vertex element.") from exc
    vertices = vertex_element.data
    if len(vertices) != len(labels):
        raise ValueError("Mask label count does not match the PLY vertex count.")

    fields = [
        (name, "<i4" if name == "mask_id" else dtype)
        for name, dtype in vertices.dtype.descr
        if name != "mask_id"
    ]
    fields.append(("mask_id", "<i4"))
    labeled_vertices = np.empty(len(vertices), dtype=np.dtype(fields))
    for name in vertices.dtype.names or ():
        if name != "mask_id":
            labeled_vertices[name] = vertices[name]
    labeled_vertices["mask_id"] = labels

    elements = [
        PlyElement.describe(
            labeled_vertices if element.name == "vertex" else element.data,
            element.name,
            comments=element.comments,
        )
        for element in ply_data.elements
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.tmp")
    PlyData(
        elements,
        text=ply_data.text,
        byte_order=ply_data.byte_order,
        comments=ply_data.comments,
        obj_info=ply_data.obj_info,
    ).write(str(temporary_path))
    temporary_path.replace(output_path)


def segment_gaussians(
    project_id: str,
    ply_path: Path,
    processed_data_dir: Path,
) -> tuple[Path, str, int]:
    """Label Gaussian centers with cross-view SAM instance IDs and upload the labeled PLY."""
    if not settings.sam_enabled:
        raise RuntimeError("SAM segmentation is disabled (set SAM_ENABLED=true to enable it).")
    if not settings.sam_checkpoint_path:
        raise RuntimeError("SAM segmentation is enabled but SAM_CHECKPOINT_PATH is not configured.")
    if settings.sam_max_masks_per_keyframe <= 0:
        raise ValueError("SAM_MAX_MASKS_PER_KEYFRAME must be greater than zero.")
    checkpoint = Path(settings.sam_checkpoint_path)
    if not checkpoint.is_file():
        raise FileNotFoundError(f"SAM checkpoint does not exist: {checkpoint}")

    try:
        segment_anything = importlib.import_module("segment_anything")
    except ImportError as exc:
        raise RuntimeError(
            "SAM segmentation requires the optional segment-anything package in the GPU worker."
        ) from exc

    transforms_path = processed_data_dir / "transforms.json"
    metadata = json.loads(transforms_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Nerfstudio transforms.json must contain an object.")
    frames = _keyframes(transforms_path, settings.sam_max_keyframes)
    model_type = settings.sam_model_type
    if model_type not in segment_anything.sam_model_registry:
        raise ValueError(f"Unsupported SAM model type {model_type!r}.")
    torch = importlib.import_module("torch")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = segment_anything.sam_model_registry[model_type](checkpoint=str(checkpoint))
    model.to(device=device)
    mask_generator = segment_anything.SamAutomaticMaskGenerator(
        model,
        points_per_side=24,
        pred_iou_thresh=0.86,
        stability_score_thresh=0.92,
        crop_n_layers=0,
        min_mask_region_area=64,
        output_mode="binary_mask",
    )

    ply_data = PlyData.read(str(ply_path), mmap=False)
    vertices = ply_data["vertex"].data
    names = set(vertices.dtype.names or ())
    if not {"x", "y", "z"}.issubset(names):
        raise ValueError("Gaussian PLY must contain x, y, and z vertex properties.")
    centers = np.column_stack(
        (vertices["x"], vertices["y"], vertices["z"])
    ).astype(np.float64, copy=False)

    frame_assignments: list[np.ndarray] = []
    frame_visibility: list[np.ndarray] = []
    mask_counts: list[int] = []
    for frame, image_path in frames:
        with Image.open(image_path) as image:
            rgb_image = np.asarray(image.convert("RGB"))
        height, width = rgb_image.shape[:2]
        pixel_x, pixel_y, visible = _project_centers(
            centers,
            frame,
            metadata,
            width,
            height,
        )
        masks = mask_generator.generate(rgb_image)
        assignments = np.full(len(centers), -1, dtype=np.int32)
        candidates = [
            mask
            for mask in masks
            if isinstance(mask.get("segmentation"), np.ndarray)
            and mask["segmentation"].shape == (height, width)
            and 0.002 * width * height <= int(mask.get("area", 0)) <= 0.65 * width * height
        ]
        candidates.sort(key=lambda mask: int(mask.get("area", 0)), reverse=True)
        candidates = candidates[: settings.sam_max_masks_per_keyframe]
        candidates.sort(key=lambda mask: int(mask.get("area", 0)))
        visible_indices = np.flatnonzero(visible)
        x = pixel_x[visible_indices]
        y = pixel_y[visible_indices]
        for mask_id, mask in enumerate(candidates):
            covered = mask["segmentation"][y, x]
            unassigned = covered & (assignments[visible_indices] < 0)
            assignments[visible_indices[unassigned]] = mask_id
        frame_assignments.append(assignments)
        frame_visibility.append(visible)
        mask_counts.append(len(candidates))
        logger.info(
            "SAM frame %s: projected %d Gaussians, retained %d masks",
            image_path.name,
            int(visible.sum()),
            len(candidates),
        )

    labels = _assign_consistent_labels(frame_assignments, frame_visibility, mask_counts)
    output_path = ply_path.with_name("before_model_labeled.ply")
    _write_mask_property(ply_path, labels, output_path)
    object_key = f"{project_id}/outputs/before_model_labeled.ply"
    get_s3_client().upload_file(
        str(output_path),
        settings.s3_bucket,
        object_key,
        ExtraArgs={"ContentType": "application/octet-stream"},
    )
    cluster_count = int(np.unique(labels[labels > 0]).size)
    logger.info(
        "Uploaded SAM-labeled PLY for project %s with %d non-background labels",
        project_id,
        cluster_count,
    )
    return output_path, object_key, cluster_count
