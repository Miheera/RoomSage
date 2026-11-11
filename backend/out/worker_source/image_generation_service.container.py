"""Catalog-constrained image providers for 2D room designs."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import gc
import logging
import os
import random
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from config import settings
from schemas.gemini import CatalogImageVerification, VerifiedDesignItem

logger = logging.getLogger(__name__)

os.environ.setdefault("HF_HOME", settings.hf_home)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", settings.hf_hub_disable_symlinks_warning)
os.environ.setdefault("TORCH_HOME", str(Path(settings.hf_home) / "torch"))

FURNITURE_LABELS = {
    "bench",
    "bed",
    "chair",
    "clock",
    "couch",
    "cupboard",
    "dining table",
    "potted plant",
    "shelf",
    "sofa",
    "tv",
    "vase",
}


def log_image_provider(component: str, *, log: logging.Logger | None = None) -> None:
    (log or logger).info(
        "%s image configuration: provider=%s inpaint_model_id=%s base_model_id=%s "
        "controlnet_model_id=%s segmentation_model_id=%s depth_model_id=%s",
        component,
        settings.image_provider,
        settings.hf_inpaint_model_id,
        settings.hf_base_model_id,
        settings.hf_controlnet_depth_model_id,
        settings.hf_segmentation_model_id,
        settings.hf_depth_model_id,
    )


class ImageProvider(Protocol):
    def render(
        self,
        prompt: str,
        room_photo: bytes,
        *,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        depth_map: bytes | None,
        seed: int,
    ) -> bytes: ...

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification: ...


def _png_bytes(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        output = BytesIO()
        image.convert("RGB").save(output, format="PNG")
        return output.getvalue()


class StubImageProvider:
    def render(
        self,
        prompt: str,
        room_photo: bytes,
        *,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        depth_map: bytes | None,
        seed: int,
    ) -> bytes:
        del prompt, style, palette, items, depth_map, seed
        if not room_photo:
            raise ValueError("A room photo is required to render a design.")
        return _png_bytes(room_photo)

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        del image_bytes, items
        return CatalogImageVerification(items=[], unlisted_furniture=[])


class HuggingFaceImageProvider:
    """Local fp16 diffusion with CPU offload and catalog-image compositing."""

    def __init__(self) -> None:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError(
                "IMAGE_PROVIDER=huggingface requires a CUDA-enabled render worker."
            )
        self.torch = torch
        self._placements: CatalogImageVerification | None = None

    def render(
        self,
        prompt: str,
        room_photo: bytes,
        *,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        depth_map: bytes | None,
        seed: int,
    ) -> bytes:
        if not room_photo:
            raise ValueError("A room photo is required to render a design.")
        del prompt
        started = time.perf_counter()
        torch = self.torch
        self._placements = None
        try:
            with Image.open(BytesIO(room_photo)) as source_image:
                original_size = source_image.size
                source = self._resize_image(source_image.convert("RGB"))
            furniture_mask, floor_mask = self._run_stage(
                "segmentation", lambda: self._segment_room(source)
            )
            depth = self._run_stage(
                "depth_estimation",
                lambda: self._depth_image(source, depth_map),
            )

            if furniture_mask.getbbox():
                cleared = self._run_stage(
                    "furniture_removal",
                    lambda: self._inpaint(
                        source,
                        furniture_mask,
                        "An empty room with the same architecture, floor and wall surfaces.",
                        seed,
                        steps=settings.hf_inpaint_steps,
                    ),
                )
            else:
                cleared = source.copy()
            self._save_debug(seed, "cleared", cleared)

            surface_prompt = self._surface_prompt(style, palette, items)
            restyled = self._run_stage(
                "depth_controlnet_restyle",
                lambda: self._restyle(cleared, depth, surface_prompt, seed),
            )
            generated_furniture_mask, _ = self._run_stage(
                "restyled_room_segmentation",
                lambda: self._segment_room(restyled),
            )
            if generated_furniture_mask.getbbox():
                restyled = self._run_stage(
                    "restyle_furniture_removal",
                    lambda: self._inpaint(
                        restyled,
                        generated_furniture_mask,
                        "An empty room with the same architecture, floor and wall surfaces.",
                        seed,
                        steps=settings.hf_inpaint_steps,
                    ),
                )
            self._save_debug(seed, "restyled", restyled)

            arranged, placements, edge_mask = self._run_stage(
                "catalog_compositing",
                lambda: self._place_catalog_items(restyled, floor_mask, items, seed),
            )
            if settings.hf_harmonize_enabled and edge_mask.getbbox():
                arranged = self._run_stage(
                    "edge_harmonization",
                    lambda: self._inpaint(
                        arranged,
                        edge_mask,
                        "Photorealistic seamless integration with consistent room lighting and shadows.",
                        seed,
                        steps=settings.hf_harmonize_steps,
                        strength=settings.hf_harmonize_strength,
                    ),
                )
            if arranged.size != original_size:
                arranged = arranged.resize(original_size, Image.Resampling.LANCZOS)
            self._run_stage("output_difference_guard", lambda: self._check_output_changed(source, arranged))
            self._save_debug(seed, "final", arranged)
            output = BytesIO()
            arranged.convert("RGB").save(output, format="PNG")
            result = output.getvalue()
            self._save_final(seed, result)
            self._placements = placements
            logger.info(
                "Hugging Face render completed: seed=%s seconds=%.2f "
                "harmonize=%s placed=%d not_placed=%d",
                seed,
                time.perf_counter() - started,
                settings.hf_harmonize_enabled,
                sum(item.visible for item in placements.items),
                sum(not item.visible for item in placements.items),
            )
            return result
        except torch.cuda.OutOfMemoryError as exc:
            raise RuntimeError(
                "Hugging Face render ran out of GPU memory (CUDA OOM); lower the render "
                "resolution or use a GPU with more VRAM."
            ) from exc
        except Exception as exc:
            if str(exc) == "render did not change the room":
                raise
            if isinstance(exc, RuntimeError) and str(exc).startswith(
                ("Could not load", "Hugging Face render ran out")
            ):
                raise
            raise RuntimeError(
                f"Hugging Face room render failed ({type(exc).__name__}): {exc}"
            ) from exc
        finally:
            gc.collect()
            torch.cuda.empty_cache()

    def _run_stage(self, name: str, operation: Any) -> Any:
        torch = self.torch
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        try:
            return operation()
        finally:
            allocated_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
            reserved_mb = torch.cuda.max_memory_reserved() / (1024 * 1024)
            logger.info(
                "Hugging Face stage: name=%s seconds=%.2f "
                "peak_allocated_vram_mb=%.0f peak_reserved_vram_mb=%.0f",
                name,
                time.perf_counter() - started,
                allocated_mb,
                reserved_mb,
            )
            gc.collect()
            torch.cuda.empty_cache()

    def _check_output_changed(self, source: Image.Image, result: Image.Image) -> None:
        import cv2

        def phash(image: Image.Image) -> np.ndarray:
            gray = np.asarray(image.convert("L").resize((32, 32)), dtype=np.float32)
            coefficients = cv2.dct(gray)[:8, :8].flatten()[1:]
            median = np.median(coefficients)
            return coefficients > median

        distance = int(np.count_nonzero(phash(source) != phash(result)))
        if distance <= settings.hf_phash_max_distance:
            raise RuntimeError("render did not change the room")

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        del image_bytes, items
        if self._placements is None:
            raise RuntimeError("Hugging Face placement data is unavailable for this render.")
        result = self._placements
        self._placements = None
        return result

    @staticmethod
    def _resize_image(image: Image.Image) -> Image.Image:
        side = settings.hf_max_image_side
        scale = min(1.0, side / max(image.size))
        width = max(8, round(image.width * scale / 8) * 8)
        height = max(8, round(image.height * scale / 8) * 8)
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        return image

    def _segment_room(self, image: Image.Image) -> tuple[Image.Image, Image.Image]:
        processor = model = inputs = outputs = panoptic = None
        try:
            injected = getattr(self, "_segmenter", None)
            if injected is not None:
                processor, model = injected
            else:
                from transformers import AutoImageProcessor, DetrForSegmentation

                processor = AutoImageProcessor.from_pretrained(
                    settings.hf_segmentation_model_id,
                    token=settings.hf_token or None,
                )
                model = DetrForSegmentation.from_pretrained(
                    settings.hf_segmentation_model_id,
                    use_safetensors=True,
                    token=settings.hf_token or None,
                ).to("cpu").eval()
            inputs = processor(images=image, return_tensors="pt")
            with self.torch.inference_mode():
                outputs = model(**inputs)
            pixel_mask = inputs["pixel_mask"][0]
            processed_size = (
                int(pixel_mask.any(dim=1).sum().item()),
                int(pixel_mask.any(dim=0).sum().item()),
            )
            panoptic = processor.post_process_panoptic(
                outputs,
                processed_sizes=[processed_size],
                target_sizes=[(image.height, image.width)],
            )[0]
            if "segmentation" in panoptic:
                segmentation = panoptic["segmentation"]
            else:
                with Image.open(BytesIO(panoptic["png_string"])) as segmentation_image:
                    rgb = np.asarray(segmentation_image.convert("RGB"), dtype=np.int32)
                segmentation = rgb[:, :, 0] + (rgb[:, :, 1] << 8) + (rgb[:, :, 2] << 16)
            if hasattr(segmentation, "detach"):
                segmentation = segmentation.detach().cpu().numpy()
            labels = model.config.id2label
            furniture = np.zeros((image.height, image.width), dtype=np.uint8)
            floor = np.zeros_like(furniture)
            for segment in panoptic["segments_info"]:
                label_id = segment.get("label_id", segment.get("category_id"))
                label = str(labels.get(int(label_id), "")).casefold()
                segment_mask = segmentation == segment["id"]
                if label in FURNITURE_LABELS:
                    furniture[segment_mask] = 255
                if label.startswith("floor") or label in {"rug", "carpet"}:
                    floor[segment_mask] = 255
            furniture_image = Image.fromarray(furniture).filter(ImageFilter.MaxFilter(11))
            floor_image = Image.fromarray(floor)
            return furniture_image, self._usable_floor(floor_image, image.size)
        except Exception as exc:
            raise RuntimeError(
                f"Could not load or run segmentation model "
                f"{settings.hf_segmentation_model_id!r}: {exc}"
            ) from exc
        finally:
            self._segmenter = None
            processor = model = inputs = outputs = panoptic = None
            gc.collect()
            self.torch.cuda.empty_cache()

    @staticmethod
    def _usable_floor(floor: Image.Image, size: tuple[int, int]) -> Image.Image:
        if np.asarray(floor).sum() >= size[0] * size[1] * 255 * 0.015:
            return floor
        width, height = size
        fallback = Image.new("L", size, 0)
        draw = ImageDraw.Draw(fallback)
        draw.polygon(
            [
                (int(width * 0.08), int(height * 0.57)),
                (int(width * 0.92), int(height * 0.57)),
                (int(width * 0.99), int(height * 0.98)),
                (int(width * 0.01), int(height * 0.98)),
            ],
            fill=255,
        )
        return fallback

    def _depth_image(self, image: Image.Image, depth_bytes: bytes | None) -> Image.Image:
        if depth_bytes:
            with Image.open(BytesIO(depth_bytes)) as depth:
                return depth.convert("L").resize(image.size, Image.Resampling.BILINEAR)
        processor = model = inputs = prediction = None
        try:
            from transformers import AutoImageProcessor, DepthAnythingForDepthEstimation

            processor = AutoImageProcessor.from_pretrained(
                settings.hf_depth_model_id,
                token=settings.hf_token or None,
            )
            model = DepthAnythingForDepthEstimation.from_pretrained(
                    settings.hf_depth_model_id,
                    use_safetensors=True,
                    token=settings.hf_token or None,
            ).to("cpu").eval()
            inputs = processor(images=image, return_tensors="pt")
            with self.torch.inference_mode():
                prediction = model(**inputs).predicted_depth
            prediction = self.torch.nn.functional.interpolate(
                prediction.unsqueeze(1),
                size=(image.height, image.width),
                mode="bicubic",
                align_corners=False,
            ).squeeze()
            prediction = prediction.float().cpu().numpy()
            low, high = float(prediction.min()), float(prediction.max())
            if high - low < 1e-6:
                raise RuntimeError("Depth model returned a constant depth map.")
            normalized = ((prediction - low) * (255.0 / (high - low))).clip(0, 255)
            return Image.fromarray(normalized.astype(np.uint8), mode="L")
        except Exception as exc:
            raise RuntimeError(
                f"Could not load or run depth model {settings.hf_depth_model_id!r}: {exc}"
            ) from exc
        finally:
            processor = model = inputs = prediction = None
            gc.collect()
            self.torch.cuda.empty_cache()

    def _inpaint(
        self,
        image: Image.Image,
        mask: Image.Image,
        prompt: str,
        seed: int,
        *,
        steps: int,
        strength: float = 1.0,
    ) -> Image.Image:
        pipeline = result = None
        try:
            from diffusers import StableDiffusionInpaintPipeline

            pipeline = StableDiffusionInpaintPipeline.from_pretrained(
                settings.hf_inpaint_model_id,
                torch_dtype=self.torch.float16,
                variant="fp16",
                use_safetensors=True,
                safety_checker=None,
                requires_safety_checker=False,
                token=settings.hf_token or None,
            )
            self._configure_pipeline(pipeline)
            result = pipeline(
                prompt=prompt,
                image=image,
                mask_image=mask,
                num_inference_steps=steps,
                guidance_scale=settings.hf_guidance_scale,
                strength=strength,
                generator=self.torch.Generator(device="cpu").manual_seed(seed),
            )
            return result.images[0].convert("RGB")
        except self.torch.cuda.OutOfMemoryError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not load or run inpainting model {settings.hf_inpaint_model_id!r}: {exc}"
            ) from exc
        finally:
            pipeline = result = None
            gc.collect()
            self.torch.cuda.empty_cache()

    def _restyle(
        self,
        image: Image.Image,
        depth: Image.Image,
        prompt: str,
        seed: int,
    ) -> Image.Image:
        controlnet = pipeline = result = None
        try:
            from diffusers import (
                ControlNetModel,
                StableDiffusionControlNetImg2ImgPipeline,
            )

            controlnet = ControlNetModel.from_pretrained(
                settings.hf_controlnet_depth_model_id,
                torch_dtype=self.torch.float16,
                use_safetensors=True,
                token=settings.hf_token or None,
            )
            pipeline = StableDiffusionControlNetImg2ImgPipeline.from_pretrained(
                settings.hf_base_model_id,
                controlnet=controlnet,
                torch_dtype=self.torch.float16,
                variant="fp16",
                use_safetensors=True,
                safety_checker=None,
                requires_safety_checker=False,
                token=settings.hf_token or None,
            )
            self._configure_pipeline(pipeline)
            result = pipeline(
                prompt=prompt,
                image=image,
                control_image=depth,
                strength=0.42,
                controlnet_conditioning_scale=0.8,
                num_inference_steps=settings.hf_restyle_steps,
                guidance_scale=settings.hf_guidance_scale,
                generator=self.torch.Generator(device="cpu").manual_seed(seed),
            )
            return result.images[0].convert("RGB")
        except self.torch.cuda.OutOfMemoryError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Could not load or run depth-ControlNet model "
                f"{settings.hf_controlnet_depth_model_id!r} with base model "
                f"{settings.hf_base_model_id!r}: {exc}"
            ) from exc
        finally:
            controlnet = pipeline = result = None
            gc.collect()
            self.torch.cuda.empty_cache()

    @staticmethod
    def _configure_pipeline(pipeline: Any) -> None:
        pipeline.enable_attention_slicing()
        if callable(getattr(pipeline, "enable_vae_slicing", None)):
            pipeline.enable_vae_slicing()
        if callable(getattr(pipeline, "enable_vae_tiling", None)):
            pipeline.enable_vae_tiling()
        import torch

        gpu_memory = torch.cuda.get_device_properties(0).total_memory
        if gpu_memory <= 6 * 1024**3:
            sequential_offload = getattr(pipeline, "enable_sequential_cpu_offload", None)
            if callable(sequential_offload):
                sequential_offload()
                return
        offload = getattr(pipeline, "enable_model_cpu_offload", None)
        if callable(offload):
            offload()
            return
        pipeline.to("cuda")

    @staticmethod
    def _surface_prompt(style: str, palette: str, items: list[dict[str, Any]]) -> str:
        finishes = [
            f"{item.get('role', 'surface')}: {item.get('primary_color') or ''} "
            f"{item.get('primary_material') or ''} {item.get('prompt_phrase') or ''}".strip()
            for item in items
            if item.get("kind") == "surface"
        ]
        return (
            "Photorealistic empty interior restyling. Preserve the existing camera view, room "
            "geometry, walls, windows, doors, ceiling and floor plan. Restyle only the wall and "
            "floor finishes. "
            f"Style: {style}. Palette: {palette}. "
            f"Required wall and floor finishes: {'; '.join(finishes) or 'retain existing finishes'}."
        )

    def _place_catalog_items(
        self,
        room: Image.Image,
        floor: Image.Image,
        items: list[dict[str, Any]],
        seed: int,
    ) -> tuple[Image.Image, CatalogImageVerification, Image.Image]:
        placed = room.convert("RGBA")
        width, height = room.size
        floor_array = np.asarray(floor.convert("L")) > 0
        ys, xs = np.where(floor_array)
        floor_bounds = (
            (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
            if xs.size
            else (0, int(height * 0.57), width, height)
        )
        left, top, right, bottom = floor_bounds
        max_width = max(28, int(width * settings.hf_object_width_fraction))
        placements: list[VerifiedDesignItem] = []
        edge_union = Image.new("L", room.size, 0)
        occupied: list[tuple[int, int, int, int]] = []
        object_items = [item for item in items if item.get("kind") == "object"]
        random.Random(seed).shuffle(object_items)
        floor_centers = [
            (x, y)
            for y in range(max(top, int(height * 0.60)), min(bottom, height - 4), 16)
            for x in range(max(left, 4), min(right, width - 4), 16)
        ]
        random.Random(seed).shuffle(floor_centers)

        for item in items:
            if item.get("kind") == "surface":
                placements.append(
                    VerifiedDesignItem(
                        catalog_item_id=str(item["catalog_item_id"]),
                        visible=True,
                        x=None,
                        y=None,
                    )
                )
        for item in object_items:
            reference = item.get("reference_image_bytes")
            if not reference:
                raise RuntimeError(
                    f"Catalog item {item.get('catalog_item_id')} has no reference image."
                )
            product = self._remove_white_background(reference)
            dimensions = item.get("dimensions_m") or [0.8, 0.8, 0.8]
            product_width_m = max(0.1, float(dimensions[0]))
            product_height_m = max(0.1, float(dimensions[2] if len(dimensions) > 2 else dimensions[1]))
            placed_centers: list[tuple[int, int]] = []
            quantity = max(1, int(item.get("qty", 1)))
            for _ in range(quantity):
                choice: tuple[int, int, int, int, Image.Image] | None = None
                for center_x, floor_y in floor_centers:
                    perspective = 0.62 + 0.5 * max(
                        0.0, min(1.0, (floor_y - top) / max(1, bottom - top))
                    )
                    target_width = min(
                        max_width,
                        max(24, int(width * product_width_m / settings.hf_room_width_m * perspective)),
                    )
                    target_height = min(
                        int(height * 0.55),
                        max(20, int(width * product_height_m / settings.hf_room_width_m * perspective)),
                    )
                    foreground = product.copy()
                    foreground.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
                    x1 = center_x - foreground.width // 2
                    y1 = floor_y - foreground.height
                    x2, y2 = x1 + foreground.width, floor_y
                    if x1 < 0 or x2 > width or y1 < 0 or y2 > height:
                        continue
                    if any(
                        x1 < old_x2 + 5 and x2 + 5 > old_x1
                        and y1 < old_y2 + 5 and y2 + 5 > old_y1
                        for old_x1, old_y1, old_x2, old_y2 in occupied
                    ):
                        continue
                    contact_y = min(height - 1, floor_y - 1)
                    if not floor_array[contact_y, center_x]:
                        continue
                    choice = (x1, y1, x2, y2, foreground)
                    break
                if choice is None:
                    break
                x1, y1, x2, y2, foreground = choice
                self._composite_product(placed, foreground, x1, y1, x2, y2)
                edge = Image.new("L", room.size, 0)
                edge.paste(foreground.getchannel("A"), (x1, y1))
                edge_union = Image.fromarray(
                    np.maximum(np.asarray(edge_union), np.asarray(edge))
                )
                occupied.append((x1, y1, x2, y2))
                placed_centers.append(((x1 + x2) // 2, floor_y - foreground.height // 2))
            if placed_centers:
                center_x = sum(point[0] for point in placed_centers) / len(placed_centers)
                center_y = sum(point[1] for point in placed_centers) / len(placed_centers)
                placements.append(
                    VerifiedDesignItem(
                        catalog_item_id=str(item["catalog_item_id"]),
                        visible=True,
                        x=round(center_x / width, 5),
                        y=round(center_y / height, 5),
                    )
                )
            else:
                placements.append(
                    VerifiedDesignItem(
                        catalog_item_id=str(item["catalog_item_id"]),
                        visible=False,
                        x=None,
                        y=None,
                    )
                )

        if settings.hf_harmonize_enabled and edge_union.getbbox():
            expanded = edge_union.filter(ImageFilter.MaxFilter(13))
            contracted = edge_union.filter(ImageFilter.MinFilter(5))
            edge_mask = Image.fromarray(
                np.where(
                    np.asarray(expanded) > np.asarray(contracted),
                    np.asarray(expanded),
                    0,
                ).astype(np.uint8)
            )
        else:
            edge_mask = edge_union
        return placed.convert("RGB"), CatalogImageVerification(items=placements), edge_mask

    @staticmethod
    def _remove_white_background(image_bytes: bytes) -> Image.Image:
        import cv2

        with Image.open(BytesIO(image_bytes)) as image:
            rgb_image = image.convert("RGB")
        rgb = np.asarray(rgb_image)
        white_like = (
            (rgb.min(axis=2) >= 218)
            & ((rgb.max(axis=2) - rgb.min(axis=2)) <= 32)
        ).astype(np.uint8)
        _, labels = cv2.connectedComponents(white_like, connectivity=8)
        background_labels = set(np.unique(np.concatenate([
            labels[0, :],
            labels[-1, :],
            labels[:, 0],
            labels[:, -1],
        ])))
        background = np.isin(labels, list(background_labels)) & (white_like > 0)
        alpha = np.where(background, 0, 255).astype(np.uint8)
        alpha_image = Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(radius=0.7))
        rgba = rgb_image.convert("RGBA")
        rgba.putalpha(alpha_image)
        bbox = rgba.getchannel("A").getbbox()
        if bbox is None:
            raise RuntimeError("A catalog reference image has no removable foreground.")
        return rgba.crop(bbox)

    @staticmethod
    def _composite_product(
        canvas: Image.Image,
        product: Image.Image,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> None:
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(shadow)
        base_y = bottom - max(1, product.height // 24)
        shadow_box = (
            left + product.width * 0.12,
            base_y - max(3, product.height * 0.035),
            right - product.width * 0.12,
            base_y + max(3, product.height * 0.035),
        )
        draw.ellipse(shadow_box, fill=(0, 0, 0, 74))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(2, product.width * 0.035)))
        canvas.alpha_composite(shadow)
        canvas.alpha_composite(product, (left, top))

    @staticmethod
    def _output_dir() -> Path:
        output_dir = Path(settings.render_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _save_debug(self, seed: int, name: str, image: Image.Image) -> None:
        debug_dir = self._output_dir() / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        image.save(debug_dir / f"seed{seed}_{name}.png", format="PNG")

    def _save_final(self, seed: int, image_bytes: bytes) -> None:
        (self._output_dir() / f"seed{seed}.png").write_bytes(image_bytes)


@lru_cache(maxsize=1)
def get_image_generation_service() -> ImageProvider:
    if settings.image_provider == "stub":
        return StubImageProvider()
    if settings.image_provider == "huggingface":
        return HuggingFaceImageProvider()
    raise ValueError(f"Unsupported image provider {settings.image_provider!r}.")


class ImageGenerationService:
    """Backward-compatible configured provider facade."""

    def __init__(self) -> None:
        self.provider = get_image_generation_service()

    def render(
        self,
        prompt: str,
        room_photo: bytes,
        *,
        style: str = "",
        palette: str = "",
        items: list[dict[str, Any]] | None = None,
        depth_map: bytes | None = None,
        seed: int = 0,
    ) -> bytes:
        return self.provider.render(
            prompt,
            room_photo,
            style=style,
            palette=palette,
            items=items or [],
            depth_map=depth_map,
            seed=seed,
        )

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        return self.provider.locate_items(image_bytes, items)

    def verify_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        return self.locate_items(image_bytes, items)
