"""ADE20K room masks and geometry-preserving surface edits."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
import math
import os
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageColor

from config import settings

SEGMENTATION_MODEL_CARD = (
    "https://huggingface.co/openmmlab/upernet-convnext-small"
)
SEGMENTATION_MODEL_LICENSE = "MIT"
SEGMENTATION_MODEL_LICENSE_URL = "https://opensource.org/license/mit"
EMPTY_ROOM_GUARD_MESSAGE = "This demo works best on empty or nearly empty rooms."
MAX_FURNITURE_FRACTION = 0.15

_COLOR_ALIASES: tuple[tuple[str, tuple[int, int, int]], ...] = (
    ("sugared nut", (205, 194, 170)),
    ("charcoal", (68, 68, 68)),
    ("terracotta", (179, 91, 66)),
    ("warm honey", (210, 165, 92)),
    ("honey", (210, 165, 92)),
    ("sage", (160, 174, 145)),
    ("green", (142, 165, 133)),
    ("blue", (135, 161, 181)),
    ("silver grey", (175, 177, 177)),
    ("cool grey", (145, 150, 152)),
    ("gray", (145, 150, 152)),
    ("grey", (145, 150, 152)),
    ("beige", (205, 194, 170)),
    ("ivory", (235, 230, 211)),
    ("cream", (235, 230, 211)),
    ("off-white", (235, 232, 221)),
    ("off white", (235, 232, 221)),
    ("white", (235, 232, 221)),
    ("brown", (143, 105, 75)),
    ("red", (176, 86, 75)),
    ("pink", (201, 157, 157)),
    ("yellow", (211, 190, 121)),
    ("black", (54, 54, 54)),
)


@dataclass(frozen=True)
class RoomMasks:
    wall: Image.Image
    floor: Image.Image
    ceiling: Image.Image
    window: Image.Image
    door: Image.Image
    curtain: Image.Image
    furniture: Image.Image
    furniture_percent: float
    furniture_by_class: dict[str, Image.Image] = field(default_factory=dict)
    baseboard: Image.Image | None = None
    baseboard_method: str = "not_detected"
    baseboard_width_px: int = 0


def assert_same_size(stage: str, source: Image.Image, result: Image.Image) -> None:
    if result.size != source.size:
        raise ValueError(
            f"Pipeline stage '{stage}' changed image size from "
            f"{source.width}x{source.height} to {result.width}x{result.height}; "
            "room geometry and aspect ratio must be preserved."
        )


def resize_longest_side(image: Image.Image, maximum: int) -> Image.Image:
    if maximum <= 0:
        raise ValueError("The maximum image side must be greater than zero.")
    scale = min(1.0, maximum / max(image.size))
    size = (
        max(1, round(image.width * scale)),
        max(1, round(image.height * scale)),
    )
    result = image.resize(size, Image.Resampling.LANCZOS) if image.size != size else image.copy()
    if not math.isclose(image.width / image.height, result.width / result.height, rel_tol=0.003):
        raise ValueError(
            f"Image resize changed the aspect ratio from {image.width}:{image.height} "
            f"to {result.width}:{result.height}."
        )
    return result


def enforce_empty_room(furniture_percent: float) -> None:
    if furniture_percent > MAX_FURNITURE_FRACTION * 100:
        raise ValueError(EMPTY_ROOM_GUARD_MESSAGE)


@lru_cache(maxsize=1)
def _load_ade20k_model(model_id: str, hf_home: str, token: str, offline: bool) -> tuple[Any, Any, Any]:
    import torch
    from transformers import AutoImageProcessor, UperNetForSemanticSegmentation

    kwargs: dict[str, Any] = {"local_files_only": offline, "cache_dir": hf_home}
    if token:
        kwargs["token"] = token
    processor = AutoImageProcessor.from_pretrained(model_id, **kwargs)
    model = UperNetForSemanticSegmentation.from_pretrained(model_id, **kwargs)
    model.to("cpu").eval()
    return processor, model, torch


def segment_room(image: Image.Image) -> RoomMasks:
    model_id = settings.hf_segmentation_model_id
    try:
        processor, model, torch = _load_ade20k_model(
            model_id,
            settings.hf_home,
            settings.hf_token,
            os.getenv("HF_HUB_OFFLINE") == "1",
        )
        # The model card's default 512x512 processor resize distorts room
        # geometry; this stage uses the already aspect-preserved working image.
        inputs = processor(
            images=image.convert("RGB"), return_tensors="pt", do_resize=False
        )
        with torch.inference_mode():
            logits = model(**inputs).logits
        prediction = torch.nn.functional.interpolate(
            logits,
            size=(image.height, image.width),
            mode="bilinear",
            align_corners=False,
        )[0].argmax(dim=0).cpu().numpy()

        labels = {
            int(label_id): str(label).strip().casefold()
            for label_id, label in model.config.id2label.items()
        }
        name_to_id = {label: label_id for label_id, label in labels.items()}
        required = {"wall", "floor", "ceiling", "windowpane", "door", "curtain"}
        missing = sorted(required - set(name_to_id))
        if missing:
            raise RuntimeError(
                "The configured ADE20K model is missing expected room labels: "
                + ", ".join(missing)
            )

        def mask_for(names: set[str]) -> np.ndarray:
            ids = [label_id for label_id, name in labels.items() if name in names]
            return np.isin(prediction, ids)

        def to_image(mask: np.ndarray) -> Image.Image:
            result = Image.fromarray(mask.astype(np.uint8) * 255, mode="L")
            assert_same_size("ADE20K segmentation mask", image, result)
            return result

        ceiling = mask_for({"ceiling"})
        window = mask_for({"windowpane"})
        door = mask_for({"door"})
        curtain = mask_for({"curtain"})
        floor = mask_for({"floor"})
        protection_px = int(settings.hf_door_protection_dilation_pixels)
        if protection_px:
            protection_kernel = np.ones((protection_px * 2 + 1, protection_px * 2 + 1), dtype=np.uint8)
            protected_doors = cv2.dilate(door.astype(np.uint8), protection_kernel).astype(bool)
        else:
            protected_doors = door
        protected_nonwall = ceiling | window | curtain | protected_doors
        raw_wall = mask_for({"wall"}) & ~protected_nonwall.astype(bool)
        baseboard_labels = {name for name in labels.values() if name in {"baseboard", "skirting board"}}
        strip_width = max(1, int(settings.hf_baseboard_strip_pixels))
        if baseboard_labels:
            baseboard_array = mask_for(baseboard_labels) & raw_wall
            baseboard_method = "ade20k_class"
            strip_width = 0
        else:
            # ADE20K UPerNet has wall/floor labels but no baseboard class.
            # Preserve a narrow wall-side band at the wall/floor boundary.
            distance_from_floor = cv2.distanceTransform(
                (~floor).astype(np.uint8), cv2.DIST_L2, 3
            )
            baseboard_array = raw_wall & (distance_from_floor <= strip_width)
            baseboard_method = "wall_floor_boundary_strip"
        wall = raw_wall & ~baseboard_array
        configured_furniture = {
            str(name).strip().casefold()
            for name in settings.hf_furniture_classes
            if str(name).strip()
        }
        furniture_by_class = {
            name: to_image(mask_for({name}))
            for name in sorted(configured_furniture.intersection(set(labels.values())))
        }
        furniture_array = np.zeros_like(prediction, dtype=bool)
        for class_mask in furniture_by_class.values():
            furniture_array |= np.asarray(class_mask) > 0
        percent = float(furniture_array.mean() * 100)
        return RoomMasks(
            wall=to_image(wall),
            floor=to_image(floor),
            ceiling=to_image(ceiling),
            window=to_image(window),
            door=to_image(door),
            curtain=to_image(curtain),
            furniture=to_image(furniture_array),
            furniture_percent=percent,
            furniture_by_class=furniture_by_class,
            baseboard=to_image(baseboard_array),
            baseboard_method=baseboard_method,
            baseboard_width_px=strip_width,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Could not load or run ADE20K segmentation model {model_id!r}: {exc}"
        ) from exc


def _resolve_color(*descriptions: str) -> tuple[int, int, int]:
    for description in descriptions:
        text = description.strip()
        if not text:
            continue
        try:
            return ImageColor.getrgb(text)[:3]
        except ValueError:
            pass
        lowered = text.casefold()
        exact_alias = next(
            (rgb for alias, rgb in _COLOR_ALIASES if alias == lowered), None
        )
        if exact_alias is not None:
            return exact_alias
        for alias, rgb in _COLOR_ALIASES:
            if alias in lowered:
                return rgb
    values = ", ".join(value for value in descriptions if value)
    raise ValueError(
        f"Cannot resolve a wall color from the selected catalog finish ({values!r}); "
        "use a CSS color name or #RRGGBB value."
    )


def _floor_quad(mask: np.ndarray) -> np.ndarray:
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        raise ValueError("the floor mask contains no visible floor pixels")
    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    if perimeter < 16:
        raise ValueError("the visible floor area is too small to estimate perspective")
    hull = cv2.convexHull(contour)
    quad = None
    for fraction in (0.01, 0.02, 0.03, 0.05, 0.08, 0.12):
        candidate = cv2.approxPolyDP(hull, fraction * perimeter, True)
        if len(candidate) == 4:
            quad = candidate.reshape(4, 2).astype(np.float32)
            break
    if quad is None or abs(cv2.contourArea(quad)) < 4:
        raise ValueError("the floor boundary is not a stable four-corner perspective plane")
    center = quad.mean(axis=0)
    angles = np.arctan2(quad[:, 1] - center[1], quad[:, 0] - center[0])
    quad = quad[np.argsort(angles)]
    start = int(np.argmin(quad[:, 0] + quad[:, 1]))
    return np.roll(quad, -start, axis=0)


def _texture_color(texture: np.ndarray) -> tuple[int, int, int]:
    rgb = texture.reshape(-1, 3)
    sample = np.median(rgb, axis=0).round().astype(np.uint8)
    return int(sample[0]), int(sample[1]), int(sample[2])


def _tint_floor(
    result: np.ndarray,
    source: np.ndarray,
    floor_mask: np.ndarray,
    tint_rgb: tuple[int, int, int],
) -> None:
    lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB)
    tint_lab = cv2.cvtColor(
        np.asarray([[tint_rgb]], dtype=np.uint8), cv2.COLOR_RGB2LAB
    )[0, 0]
    tinted = lab.copy()
    tinted[:, :, 1] = tint_lab[1]
    tinted[:, :, 2] = tint_lab[2]
    rgb = cv2.cvtColor(tinted, cv2.COLOR_LAB2RGB)
    result[floor_mask] = rgb[floor_mask]


def _warp_floor_texture(
    base: np.ndarray,
    source: np.ndarray,
    floor_mask: np.ndarray,
    texture: np.ndarray,
    *,
    tile_width_px: int,
    texture_contrast: float,
) -> np.ndarray:
    height, width = floor_mask.shape
    quad = _floor_quad(floor_mask)
    tile_width_px = max(8, int(tile_width_px))
    tile = cv2.resize(texture, (tile_width_px, tile_width_px), interpolation=cv2.INTER_AREA)
    tiled = np.tile(
        tile,
        (math.ceil(height / tile.shape[0]), math.ceil(width / tile.shape[1]), 1),
    )[:height, :width]
    source_corners = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(source_corners, quad)
    if not np.isfinite(transform).all() or abs(np.linalg.det(transform)) < 1e-10:
        raise ValueError("the estimated floor perspective transform is singular")
    warped = cv2.warpPerspective(
        tiled,
        transform,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )

    source_luma = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY).astype(np.float32)
    texture_luma = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY).astype(np.float32)
    floor_texture_luma = texture_luma[floor_mask]
    if floor_texture_luma.size == 0:
        raise ValueError("the floor mask contains no visible floor pixels")
    # Compress unusually dark/light texture blocks, normalize the swatch to
    # the original floor brightness, then apply original per-pixel lighting.
    texture_reference = max(1.0, float(np.median(floor_texture_luma)))
    contrast = float(np.clip(texture_contrast, 0.05, 1.0))
    compressed_luma = texture_reference + (texture_luma - texture_reference) * contrast
    normalized_texture = warped.astype(np.float32) * (
        compressed_luma[:, :, None] / np.maximum(texture_luma[:, :, None], 1.0)
    )
    floor_source_luma = source_luma[floor_mask]
    if floor_source_luma.size == 0:
        raise ValueError("the floor mask contains no visible floor pixels")
    source_reference = max(1.0, float(np.median(floor_source_luma)))
    normalized_texture *= source_reference / texture_reference
    shading = np.clip(source_luma / source_reference, 0.35, 1.8)
    shaded = np.clip(normalized_texture * shading[:, :, None], 0, 255)
    output = base.copy()
    output[floor_mask] = shaded.astype(np.uint8)[floor_mask]
    return output


def apply_surface_edits(
    image: Image.Image,
    masks: RoomMasks,
    *,
    paint: dict[str, Any] | None = None,
    flooring: dict[str, Any] | None = None,
    palette: str = "",
) -> tuple[Image.Image, dict[str, Any]]:
    source = np.asarray(image.convert("RGB")).copy()

    def array_mask(mask: Image.Image) -> np.ndarray:
        assert_same_size("surface edit mask", image, mask)
        return np.asarray(mask.convert("L")) > 0

    ceiling = array_mask(masks.ceiling)
    window = array_mask(masks.window)
    door = array_mask(masks.door)
    curtain = array_mask(masks.curtain)
    baseboard = array_mask(masks.baseboard) if masks.baseboard is not None else np.zeros_like(ceiling)
    wall_mask = array_mask(masks.wall) & ~ceiling & ~window & ~door & ~curtain & ~baseboard
    floor_mask = array_mask(masks.floor)
    editable_mask = wall_mask | floor_mask
    result = source.copy()
    metadata: dict[str, Any] = {
        "wall": {"applied": False},
        "floor": {"applied": False, "fallback": False, "reason": None},
    }

    if paint is not None:
        if not wall_mask.any():
            raise RuntimeError("The selected wall finish cannot be applied: no wall mask was detected.")
        color = _resolve_color(
            str(paint.get("primary_color") or ""),
            str(paint.get("prompt_phrase") or ""),
            palette,
        )
        lab = cv2.cvtColor(source, cv2.COLOR_RGB2LAB)
        color_lab = cv2.cvtColor(
            np.asarray([[color]], dtype=np.uint8), cv2.COLOR_RGB2LAB
        )[0, 0]
        lab[wall_mask, 1] = color_lab[1]
        lab[wall_mask, 2] = color_lab[2]
        recolored = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        result[wall_mask] = recolored[wall_mask]
        metadata["wall"] = {
            "applied": True,
            "color_rgb": list(color),
            "color_hex": "#%02X%02X%02X" % color,
            "source_color": paint.get("primary_color") or paint.get("prompt_phrase"),
            "palette": palette,
        }

    if flooring is not None:
        if not floor_mask.any():
            raise RuntimeError(
                "The selected flooring finish cannot be applied: no floor mask was detected."
            )
        texture_bytes = flooring.get("floor_texture_bytes")
        if not texture_bytes:
            raise RuntimeError(
                "The selected flooring finish has no local catalog texture image."
            )
        with Image.open(BytesIO(bytes(texture_bytes))) as texture_image:
            texture = np.asarray(texture_image.convert("RGB"))
        try:
            tile_width_px = max(
                8,
                round(image.width * settings.hf_floor_texture_tile_width_fraction),
            )
            result = _warp_floor_texture(
                result,
                source,
                floor_mask,
                texture,
                tile_width_px=tile_width_px,
                texture_contrast=settings.hf_floor_texture_contrast,
            )
            metadata["floor"] = {
                "applied": True,
                "method": "perspective_tiled_texture_with_original_luminance",
                "fallback": False,
                "reason": None,
                "tile_width_px": tile_width_px,
                "tile_width_fraction": settings.hf_floor_texture_tile_width_fraction,
                "texture_contrast": settings.hf_floor_texture_contrast,
            }
        except (ValueError, cv2.error) as exc:
            reason = str(exc) or exc.__class__.__name__
            _tint_floor(result, source, floor_mask, _texture_color(texture))
            metadata["floor"] = {
                "applied": True,
                "method": "luminance_preserving_texture_tint",
                "fallback": True,
                "reason": reason,
            }

    edited = Image.fromarray(result.astype(np.uint8), mode="RGB")
    assert_same_size("mask-based surface edits", image, edited)
    outside = ~editable_mask
    if not np.array_equal(source[outside], np.asarray(edited)[outside]):
        raise RuntimeError(
            "Mask-based surface edits modified pixels outside the wall and floor masks."
        )
    return edited, metadata
