"""Catalog-constrained image providers for 2D room designs."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import gc
import logging
import os
import random
import time
import json
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from config import settings
from schemas.gemini import CatalogImageVerification, VerifiedDesignItem
from services.empty_room_pipeline import (
    RoomMasks,
    apply_surface_edits,
    assert_same_size,
    enforce_empty_room,
    resize_longest_side,
    segment_room,
)
from services.pipeline_trace import PipelineTrace, trace_enabled

logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def _rembg_u2net_session() -> Any:
    from rembg import new_session
    model_path = Path(os.environ["U2NET_HOME"]) / "models" / "u2net" / "u2net.onnx"
    if not model_path.is_file():
        raise RuntimeError(
            f"Required rembg U-2-Net weights are missing at {model_path}. "
            "Run `python -m scripts.download_models` before placing catalog objects."
        )

    return new_session("u2net")

os.environ.setdefault("HF_HOME", settings.hf_home)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", settings.hf_hub_disable_symlinks_warning)
os.environ.setdefault("TORCH_HOME", str(Path(settings.hf_home) / "torch"))
os.environ.setdefault("U2NET_HOME", str(Path(settings.hf_home) / "rembg"))

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
        trace_context: dict[str, Any] | None = None,
    ) -> bytes: ...

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification: ...


def _png_bytes(image_bytes: bytes) -> bytes:
    with Image.open(BytesIO(image_bytes)) as image:
        output = BytesIO()
        image.convert("RGB").save(output, format="PNG")
        return output.getvalue()


def _hf_load_kwargs(model_id: str) -> dict[str, Any]:
    offline = os.getenv("HF_HUB_OFFLINE") == "1"
    if offline:
        try:
            configured_path = Path(model_id)
            if configured_path.is_dir():
                local_path = configured_path
            else:
                from huggingface_hub import snapshot_download
                local_path = Path(snapshot_download(
                    model_id, token=settings.hf_token or None, local_files_only=True,
                ))
            if not any(local_path.rglob("*.safetensors")) and not any(local_path.rglob("pytorch_model*.bin")):
                raise FileNotFoundError("snapshot has configuration but no model weight files")
        except Exception as exc:
            raise RuntimeError(
                f"Required model weights missing from offline cache: {model_id}. "
                "Run `python -m scripts.download_models` before starting the render worker."
            ) from exc
    return {"token": settings.hf_token or None, "local_files_only": offline}


class StubImageProvider:
    @property
    def render_metadata(self) -> dict[str, Any]:
        return {"pipeline": "stub"}

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
        trace_context: dict[str, Any] | None = None,
    ) -> bytes:
        del prompt, style, palette, items, depth_map, seed, trace_context
        if not room_photo:
            raise ValueError("A room photo is required to render a design.")
        return _png_bytes(room_photo)

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        del image_bytes, items
        return CatalogImageVerification(items=[], unlisted_furniture=[])


class HuggingFaceImageProvider:
    """ADE20K room-surface edits and catalog-image compositing."""

    def __init__(self) -> None:
        import torch

        if settings.hf_legacy_sd_restyle_enabled and not torch.cuda.is_available():
            raise RuntimeError(
                "The opt-in legacy SD restyle requires a CUDA-enabled render worker."
            )
        self.torch = torch
        self._placements: CatalogImageVerification | None = None
        self._placed_regions: list[dict[str, Any]] = []
        self._trace: PipelineTrace | None = None
        self._render_metadata: dict[str, Any] = {}

    @property
    def render_metadata(self) -> dict[str, Any]:
        return dict(self._render_metadata)

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
        trace_context: dict[str, Any] | None = None,
    ) -> bytes:
        self._render_metadata = {}
        if settings.hf_legacy_sd_restyle_enabled:
            return self._render_legacy(
                prompt,
                room_photo,
                style=style,
                palette=palette,
                items=items,
                depth_map=depth_map,
                seed=seed,
                trace_context=trace_context,
            )
        return self._render_mask_based(
            prompt,
            room_photo,
            style=style,
            palette=palette,
            items=items,
            seed=seed,
            trace_context=trace_context,
        )

    def _render_legacy(
        self,
        prompt: str,
        room_photo: bytes,
        *,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        depth_map: bytes | None,
        seed: int,
        trace_context: dict[str, Any] | None = None,
    ) -> bytes:
        if not room_photo:
            raise ValueError("A room photo is required to render a design.")
        started = time.perf_counter()
        torch = self.torch
        self._placements = None
        self._trace = (
            PipelineTrace(str(trace_context["design_id"]), trace_context)
            if trace_enabled() and trace_context and trace_context.get("design_id")
            else None
        )
        try:
            with Image.open(BytesIO(room_photo)) as source_image:
                original_size = source_image.size
                original = source_image.convert("RGB")
                if self._trace:
                    self._trace.write_json(
                        "prompts.json",
                        {
                            "worker_prompt": prompt,
                            "furniture_removal": (
                                "An empty room with the same architecture, floor and wall surfaces."
                            ),
                        },
                    )
                    self._trace.save_artifact(
                        "01_input.png",
                        original,
                        model_id="Pillow image decode; room photo normalization occurred at upload",
                    )
                source = self._resize_image(original)
            furniture_mask, floor_mask = self._run_stage(
                "segmentation",
                lambda: self._segment_room(source),
                model_id=settings.hf_segmentation_model_id,
            )
            depth = self._run_stage(
                "depth_estimation",
                lambda: self._depth_image(source, depth_map),
                model_id=(
                    "Project.depth_map_s3_key"
                    if depth_map is not None
                    else settings.hf_depth_model_id
                ),
            )
            if self._trace:
                self._trace.save_artifact(
                    "02_depth.png",
                    depth,
                    operation_names=("depth_estimation",),
                )
                self._trace.save_artifact(
                    "03_furniture_mask.png",
                    furniture_mask,
                    operation_names=("segmentation",),
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
                    model_id=settings.hf_inpaint_model_id,
                )
            else:
                cleared = source.copy()
            if self._trace:
                self._trace.compare_stages("01_input", original, "04_cleared", cleared)
                self._trace.save_artifact(
                    "04_cleared.png",
                    cleared,
                    operation_names=(
                        ("furniture_removal",) if furniture_mask.getbbox() else ()
                    ),
                    model_id=(
                        settings.hf_inpaint_model_id
                        if furniture_mask.getbbox()
                        else "not run: no furniture mask"
                    ),
                )
            self._save_debug(seed, "cleared", cleared)

            surface_prompt = self._surface_prompt(style, palette, items)
            if self._trace:
                self._trace.write_json(
                    "prompts.json",
                    {
                        "worker_prompt": prompt,
                        "furniture_removal": (
                            "An empty room with the same architecture, floor and wall surfaces."
                        ),
                        "surface_restyle": surface_prompt,
                        "post_restyle_furniture_removal": (
                            "An empty room with the same architecture, floor and wall surfaces."
                        ),
                        "harmonize": (
                            "Photorealistic seamless integration with consistent room lighting and shadows."
                        ),
                    },
                )
            restyled = self._run_stage(
                "depth_controlnet_restyle",
                lambda: self._restyle(cleared, depth, surface_prompt, seed),
                model_id=(
                    f"{settings.hf_base_model_id} + "
                    f"{settings.hf_controlnet_depth_model_id}"
                ),
            )
            generated_furniture_mask, _ = self._run_stage(
                "restyled_room_segmentation",
                lambda: self._segment_room(restyled),
                model_id=settings.hf_segmentation_model_id,
            )
            restyle_operations = [
                "depth_controlnet_restyle",
                "restyled_room_segmentation",
            ]
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
                    model_id=settings.hf_inpaint_model_id,
                )
                restyle_operations.append("restyle_furniture_removal")
            if self._trace:
                self._trace.compare_stages("04_cleared", cleared, "05_restyled", restyled)
                self._trace.save_artifact(
                    "05_restyled.png",
                    restyled,
                    operation_names=tuple(restyle_operations),
                )
            self._save_debug(seed, "restyled", restyled)

            arranged, placements, edge_mask = self._run_stage(
                "catalog_compositing",
                lambda: self._place_catalog_items(restyled, floor_mask, items, seed),
                model_id="Pillow/OpenCV catalog cutout and placement",
            )
            if self._trace:
                self._trace.compare_stages("05_restyled", restyled, "06_placed", arranged)
                self._trace.save_artifact(
                    "06_placed.png",
                    arranged,
                    operation_names=("catalog_compositing",),
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
                    model_id=settings.hf_inpaint_model_id,
                )
            placed_for_trace = arranged.copy()
            if arranged.size != original_size:
                arranged = arranged.resize(original_size, Image.Resampling.LANCZOS)
            self._run_stage(
                "output_difference_guard",
                lambda: self._check_output_changed(source, arranged),
                model_id="OpenCV DCT perceptual hash",
            )
            self._save_debug(seed, "final", arranged)
            output = BytesIO()
            arranged.convert("RGB").save(output, format="PNG")
            result = output.getvalue()
            self._save_final(seed, result)
            self._placements = placements
            if self._trace:
                self._trace.compare_stages("06_placed", placed_for_trace, "07_final", arranged)
                self._trace.save_artifact(
                    "07_final.png",
                    arranged,
                    operation_names=(
                        ("edge_harmonization", "output_difference_guard")
                        if settings.hf_harmonize_enabled and edge_mask.getbbox()
                        else ("output_difference_guard",)
                    ),
                )
                self._trace.compare_final(room_photo, result)
                self._trace.finish("rendered")
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
            if self._trace:
                self._trace.finish("failed", str(exc))
            raise RuntimeError(
                "Hugging Face render ran out of GPU memory (CUDA OOM); lower the render "
                "resolution or use a GPU with more VRAM."
            ) from exc
        except Exception as exc:
            if self._trace:
                self._trace.finish("failed", str(exc))
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

    def _render_mask_based(
        self,
        prompt: str,
        room_photo: bytes,
        *,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        seed: int,
        trace_context: dict[str, Any] | None = None,
    ) -> bytes:
        if not room_photo:
            raise ValueError("A room photo is required to render a design.")
        started = time.perf_counter()
        self._placements = None
        self._trace = (
            PipelineTrace(str(trace_context["design_id"]), trace_context)
            if trace_enabled() and trace_context and trace_context.get("design_id")
            else None
        )
        try:
            with Image.open(BytesIO(room_photo)) as source_image:
                source = resize_longest_side(
                    source_image.convert("RGB"), settings.hf_max_image_side
                )
            if self._trace:
                self._trace.save_artifact(
                    "01_input.png",
                    source,
                    model_id="Pillow aspect-preserving resize",
                )

            masks = self._run_geometry_stage(
                "ade20k_segmentation",
                source,
                lambda: segment_room(source),
            )
            if self._trace:
                for name, mask in (
                    ("02_wall_mask.png", masks.wall),
                    ("03_floor_mask.png", masks.floor),
                    ("04_ceiling_mask.png", masks.ceiling),
                    ("05_window_mask.png", masks.window),
                    ("06_door_mask.png", masks.door),
                    ("07_curtain_mask.png", masks.curtain),
                    ("08_furniture_mask.png", masks.furniture),
                    (
                        "mask_baseboard.png",
                        masks.baseboard if masks.baseboard is not None else Image.new("L", source.size, 0),
                    ),
                    *[
                        (f"mask_furniture_{label.replace(' ', '_')}.png", mask)
                        for label, mask in masks.furniture_by_class.items()
                    ],
                ):
                    self._trace.save_artifact(name, mask, operation_names=("ade20k_segmentation",))
                self._trace.write_json("segmentation.json", {
                    "model_id": settings.hf_segmentation_model_id,
                    "license": "MIT",
                    "model_card": "https://huggingface.co/openmmlab/upernet-convnext-small",
                    "furniture_percent": round(masks.furniture_percent, 2),
                    "furniture_classes": sorted(masks.furniture_by_class),
                    "baseboard_method": masks.baseboard_method,
                    "baseboard_width_px": masks.baseboard_width_px,
                    "door_protection_dilation_px": settings.hf_door_protection_dilation_pixels,
                })
            enforce_empty_room(masks.furniture_percent)

            paint = next(
                (item for item in items if item.get("kind") == "surface" and item.get("role") == "paint"),
                None,
            )
            flooring = next(
                (item for item in items if item.get("kind") == "surface" and item.get("role") == "flooring"),
                None,
            )
            restyled, edit_metadata = self._run_geometry_stage(
                "mask_based_surface_edits",
                source,
                lambda: apply_surface_edits(
                    source,
                    masks,
                    paint=paint,
                    flooring=flooring,
                    palette=palette,
                ),
            )
            if self._trace:
                self._trace.save_artifact(
                    "09_restyled.png",
                    restyled,
                    operation_names=("mask_based_surface_edits",),
                )
                self._trace.compare_stages("input", source, "restyled", restyled)

            final, placements, _ = self._run_geometry_stage(
                "catalog_compositing",
                restyled,
                lambda: self._place_catalog_items(
                    restyled, masks.floor, items, seed
                ),
            )
            composite = final.copy()
            if settings.hf_harmonize_enabled and self._placed_regions:
                final = self._harmonize_placed_regions(final, seed)
            self._placements = placements
            self._render_metadata = {
                "pipeline": "ade20k_mask_based",
                "segmentation": {
                    "model_id": settings.hf_segmentation_model_id,
                    "license": "MIT",
                    "model_card": "https://huggingface.co/openmmlab/upernet-convnext-small",
                    "furniture_percent": round(masks.furniture_percent, 2),
                    "baseboard_method": masks.baseboard_method,
                    "baseboard_width_px": masks.baseboard_width_px,
                    "door_protection_dilation_px": settings.hf_door_protection_dilation_pixels,
                },
                "surface_edits": edit_metadata,
            }
            if self._trace:
                self._trace.write_json("render_metadata.json", self._render_metadata)
                self._trace.save_artifact("06_composite.png", composite, operation_names=("catalog_compositing",))
                self._trace.save_artifact(
                    "10_final.png",
                    final,
                    operation_names=("catalog_compositing",),
                )
                self._trace.compare_stages("restyled", restyled, "final", final)
            self._save_debug(seed, "restyled", restyled)
            output = BytesIO()
            final.convert("RGB").save(output, format="PNG")
            result = output.getvalue()
            self._save_final(seed, result)
            if self._trace:
                self._trace.compare_final(room_photo, result)
                self._trace.finish("rendered")
            logger.info(
                "Mask-based room render completed: seed=%s seconds=%.2f "
                "furniture_percent=%.2f placed=%d not_placed=%d",
                seed,
                time.perf_counter() - started,
                masks.furniture_percent,
                sum(item.visible for item in placements.items),
                sum(not item.visible for item in placements.items),
            )
            return result
        except Exception as exc:
            if self._trace:
                self._trace.finish("failed", str(exc) or exc.__class__.__name__)
            raise
        finally:
            gc.collect()

    def _run_geometry_stage(
        self,
        name: str,
        source: Image.Image,
        operation: Any,
    ) -> Any:
        started = time.perf_counter()
        error = None
        if self._trace and self.torch.cuda.is_available():
            self.torch.cuda.reset_peak_memory_stats()
        try:
            result = operation()
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            raise
        finally:
            seconds = time.perf_counter() - started
            allocated = reserved = 0.0
            if self.torch.cuda.is_available():
                allocated = self.torch.cuda.max_memory_allocated() / (1024 * 1024)
                reserved = self.torch.cuda.max_memory_reserved() / (1024 * 1024)
            model_ids = {
                "ade20k_segmentation": settings.hf_segmentation_model_id,
                "mask_based_surface_edits": "OpenCV LAB + perspective warp",
                "catalog_compositing": "rembg/u2net + Pillow placement",
            }
            if self._trace:
                self._trace.record_operation(
                    name,
                    seconds=seconds,
                    peak_allocated_vram_mb=allocated,
                    peak_reserved_vram_mb=reserved,
                    model_id=model_ids.get(name, "CPU image stage"),
                    device="cpu",
                    error=error,
                )
        if isinstance(result, Image.Image):
            assert_same_size(name, source, result)
        elif isinstance(result, RoomMasks):
            for mask_name in (
                "wall", "floor", "ceiling", "window", "door", "curtain", "furniture"
            ):
                assert_same_size(f"{name}.{mask_name}", source, getattr(result, mask_name))
            if result.baseboard is not None:
                assert_same_size(f"{name}.baseboard", source, result.baseboard)
            for mask_name, mask in result.furniture_by_class.items():
                assert_same_size(f"{name}.furniture.{mask_name}", source, mask)
        elif (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], Image.Image)
        ):
            assert_same_size(name, source, result[0])
        elif (
            isinstance(result, tuple)
            and len(result) == 3
            and isinstance(result[0], Image.Image)
        ):
            assert_same_size(name, source, result[0])
        else:
            raise TypeError(f"Pipeline stage '{name}' returned an unsupported image result.")
        logger.info(
            "Mask-based pipeline stage: name=%s seconds=%.3f size=%sx%s",
            name,
            time.perf_counter() - started,
            source.width,
            source.height,
        )
        return result

    def _run_stage(
        self,
        name: str,
        operation: Any,
        *,
        model_id: str = "unspecified",
    ) -> Any:
        torch = self.torch
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        error: str | None = None
        previous_handler = None
        timer_started = False
        try:
            import signal
            if hasattr(signal, "setitimer") and settings.hf_stage_timeout_seconds > 0:
                previous_handler = signal.getsignal(signal.SIGALRM)
                signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError(f"Pipeline stage {name} exceeded {settings.hf_stage_timeout_seconds}s timeout.")))
                signal.setitimer(signal.ITIMER_REAL, settings.hf_stage_timeout_seconds)
                timer_started = True
            return operation()
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            raise
        finally:
            if timer_started:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous_handler)
            torch.cuda.synchronize()
            allocated_mb = torch.cuda.max_memory_allocated() / (1024 * 1024)
            reserved_mb = torch.cuda.max_memory_reserved() / (1024 * 1024)
            seconds = time.perf_counter() - started
            logger.info(
                "Hugging Face stage: name=%s seconds=%.2f "
                "peak_allocated_vram_mb=%.2f peak_reserved_vram_mb=%.2f",
                name,
                seconds,
                allocated_mb,
                reserved_mb,
            )
            if self._trace:
                self._trace.record_operation(
                    name,
                    seconds=seconds,
                    peak_allocated_vram_mb=allocated_mb,
                    peak_reserved_vram_mb=reserved_mb,
                    model_id=model_id,
                    device="cuda:0 (CPU offload)" if any(token in model_id.lower() for token in ("stable-diffusion", "controlnet")) else "cpu",
                    error=error,
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
        if self._trace:
            self._trace.save_hotspots(result.items)
        self._placements = None
        return result

    @staticmethod
    def _resize_image(image: Image.Image) -> Image.Image:
        return resize_longest_side(image, settings.hf_max_image_side)

    def _segment_room(self, image: Image.Image) -> tuple[Image.Image, Image.Image]:
        processor = model = inputs = outputs = panoptic = None
        try:
            injected = getattr(self, "_segmenter", None)
            if injected is not None:
                processor, model = injected
            else:
                from transformers import AutoImageProcessor, DetrForSegmentation

                processor = AutoImageProcessor.from_pretrained(
                    settings.hf_legacy_segmentation_model_id,
                    **_hf_load_kwargs(settings.hf_legacy_segmentation_model_id),
                )
                model = DetrForSegmentation.from_pretrained(
                    settings.hf_legacy_segmentation_model_id,
                    use_safetensors=True,
                    **_hf_load_kwargs(settings.hf_segmentation_model_id),
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
                f"{settings.hf_legacy_segmentation_model_id!r}: {exc}"
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
                **_hf_load_kwargs(settings.hf_depth_model_id),
            )
            model = DepthAnythingForDepthEstimation.from_pretrained(
                    settings.hf_depth_model_id,
                    use_safetensors=True,
                    **_hf_load_kwargs(settings.hf_depth_model_id),
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
                **_hf_load_kwargs(settings.hf_inpaint_model_id),
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
                **_hf_load_kwargs(settings.hf_controlnet_depth_model_id),
            )
            pipeline = StableDiffusionControlNetImg2ImgPipeline.from_pretrained(
                settings.hf_base_model_id,
                controlnet=controlnet,
                torch_dtype=self.torch.float16,
                variant="fp16",
                use_safetensors=True,
                safety_checker=None,
                requires_safety_checker=False,
                **_hf_load_kwargs(settings.hf_base_model_id),
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
        max_width = max(1, int(width * settings.hf_object_width_fraction))
        placements: list[VerifiedDesignItem] = []
        edge_union = Image.new("L", room.size, 0)
        placed_regions: list[dict[str, Any]] = []
        occupied: list[tuple[int, int, int, int]] = []
        role_boxes: dict[str, tuple[int, int, int, int]] = {}
        footprint_areas: dict[str, int] = {}
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
        max_objects = settings.hf_max_placed_objects
        essential = [item for item in object_items if item.get("is_essential")]
        optional = [item for item in object_items if not item.get("is_essential")]
        object_items = essential + optional
        dropped_reason = f"Not placed: maximum of {max_objects} objects per design."
        kept_items: list[dict[str, Any]] = []
        dropped_items: list[dict[str, Any]] = []
        remaining = max_objects
        for item in object_items:
            quantity = max(1, int(item.get("qty", 1)))
            if quantity <= remaining:
                kept_items.append(item)
                remaining -= quantity
            elif item.get("is_essential") and remaining:
                partial = dict(item)
                partial["qty"] = remaining
                kept_items.append(partial)
                remaining = 0
            else:
                dropped_items.append(item)
        if self._trace:
            self._trace.write_json("placement_dropped.json", [
                {"id": item.get("catalog_item_id"), "name": item.get("title"), "reason": dropped_reason}
                for item in dropped_items
            ])
        for item in dropped_items:
            placements.append(VerifiedDesignItem(
                catalog_item_id=str(item["catalog_item_id"]), visible=False,
                x=None, y=None, visibility_reason=dropped_reason,
            ))
        def is_support(item: dict[str, Any]) -> bool:
            configured = {str(role).casefold().replace(" ", "_") for role in settings.hf_support_surface_roles}
            role = str(item.get("placement_role") or item.get("role") or "").casefold().replace(" ", "_")
            title = str(item.get("title") or "").casefold().replace(" ", "_")
            return role in configured or any(token in title for token in configured)

        table_items = [item for item in kept_items if item.get("role") in {"coffee_table", "side_table"}]
        support_items = [item for item in kept_items if is_support(item)]
        kept_items = [item for item in kept_items if not is_support(item)] + support_items
        for item in kept_items:
            support_item = is_support(item)
            support_table = next((table for table in table_items if str(table.get("catalog_item_id")) in role_boxes), None) if support_item else None
            if support_item and support_table is None:
                placements.append(VerifiedDesignItem(catalog_item_id=str(item["catalog_item_id"]), visible=False, x=None, y=None, visibility_reason="needs a table to sit on"))
                continue
            reference = item.get("reference_image_bytes")
            if not reference:
                raise RuntimeError(
                    f"Catalog item {item.get('catalog_item_id')} has no reference image."
                )
            product = self._remove_background(reference)
            if self._trace:
                self._trace.save_item_cutout(
                    str(item["catalog_item_id"]),
                    product,
                )
            dimensions = item.get("dimensions_m") or [0.8, 0.8, 0.8]
            flat_item = len(dimensions) >= 3 and float(dimensions[2]) < 0.05
            product_width_m = max(0.1, float(dimensions[0]))
            product_height_m = max(0.1, float(dimensions[1] if flat_item else (dimensions[2] if len(dimensions) > 2 else dimensions[1])))
            placed_centers: list[tuple[int, int]] = []
            quantity = max(1, int(item.get("qty", 1)))
            for _ in range(quantity):
                choice: tuple[int, int, int, int, Image.Image] | None = None
                undersized: tuple[int, int] | None = None
                for center_x, floor_y in floor_centers:
                    perspective = 0.62 + 0.5 * max(
                        0.0, min(1.0, (floor_y - top) / max(1, bottom - top))
                    )
                    target_width = min(
                        max_width,
                        max(1, int(width * product_width_m / settings.hf_room_width_m * perspective)),
                    )
                    target_height = min(
                        max(1, int(height * 0.55)),
                        max(1, int(height * product_height_m / settings.hf_room_width_m * perspective * (0.5 if flat_item else 1.0))),
                    )
                    foreground = product.copy()
                    if flat_item:
                        foreground.putalpha(foreground.getchannel("A").filter(ImageFilter.GaussianBlur(radius=3.0)))
                    foreground.thumbnail((target_width, target_height), Image.Resampling.LANCZOS)
                    if flat_item:
                        # Use catalog width x depth as a floor plane; reject centers
                        # whose projected rectangle is not predominantly on the mask.
                        depth_px = max(1, int(height * float(dimensions[1]) / settings.hf_room_width_m * perspective * 0.5))
                        target_width = max(target_width, settings.hf_min_placed_object_pixels)
                        depth_px = max(depth_px, settings.hf_min_placed_object_pixels)
                        if target_width * depth_px < 2000:
                            factor = (2000 / (target_width * depth_px)) ** 0.5
                            target_width = int(target_width * factor) + 1
                            depth_px = int(depth_px * factor) + 1
                        foreground = product.resize((target_width, depth_px), Image.Resampling.LANCZOS)
                        # Project the rug onto a receding floor plane: its far edge is
                        # narrower than its near edge, with antialiased alpha edges.
                        import cv2
                        fw, fh = foreground.size
                        src_quad = np.float32([[0, 0], [fw - 1, 0], [fw - 1, fh - 1], [0, fh - 1]])
                        dst_quad = np.float32([[fw * .12, 0], [fw * .88, 0], [fw - 1, fh - 1], [0, fh - 1]])
                        transform = cv2.getPerspectiveTransform(src_quad, dst_quad)
                        warped = cv2.warpPerspective(np.asarray(foreground), transform, (fw, fh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
                        foreground = Image.fromarray(warped, mode="RGBA")
                        foreground.putalpha(foreground.getchannel("A").filter(ImageFilter.GaussianBlur(radius=2.0)))
                    if (foreground.width * foreground.height if flat_item else min(foreground.size)) < (2000 if flat_item else settings.hf_min_placed_object_pixels):
                        undersized = foreground.size
                        continue
                    if support_item:
                        x1t, y1t, x2t, y2t = role_boxes[str(support_table["catalog_item_id"])]
                        center_x = (x1t + x2t) // 2
                        floor_y = y1t + max(1, (y2t-y1t)//3)
                    x1 = center_x - foreground.width // 2
                    y1 = floor_y - foreground.height
                    x2, y2 = x1 + foreground.width, floor_y
                    if x1 < 0 or x2 > width or y1 < 0 or y2 > height:
                        continue
                    if not support_item and any(
                        x1 < old_x2 + 5 and x2 + 5 > old_x1
                        and y1 < old_y2 + 5 and y2 + 5 > old_y1
                        for old_x1, old_y1, old_x2, old_y2 in occupied
                    ):
                        continue
                    contact_y = min(height - 1, floor_y - 1)
                    if not support_item and not flat_item and not floor_array[contact_y, center_x]:
                        continue
                    if flat_item:
                        region = floor_array[max(0,y1):min(height,y2), max(0,x1):min(width,x2)]
                        if region.size == 0 or float(region.mean()) < 0.8:
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
                placed_regions.append({"catalog_item_id": str(item["catalog_item_id"]), "bbox": (x1, y1, x2, y2), "mask": edge, "flat": flat_item})
                occupied.append((x1, y1, x2, y2))
                role_boxes[str(item.get("catalog_item_id"))] = (x1, y1, x2, y2)
                footprint_areas[str(item.get("catalog_item_id"))] = {
                    "area": foreground.width * foreground.height,
                    "bbox": [x1, y1, x2, y2],
                    "flat": flat_item,
                }
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
                reason = "No available floor placement."
                if undersized is not None:
                    reason = (
                        f"Rendered item is smaller than the minimum visible size of "
                        f"{settings.hf_min_placed_object_pixels}px "
                        f"({undersized[0]}x{undersized[1]}px)."
                    )
                placements.append(
                    VerifiedDesignItem(
                        catalog_item_id=str(item["catalog_item_id"]),
                        visible=False,
                        x=None,
                        y=None,
                        visibility_reason=reason,
                    )
                )

        if self._trace:
            self._trace.write_json("placement_footprints.json", footprint_areas)
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
        self._placed_regions = placed_regions
        return placed.convert("RGB"), CatalogImageVerification(items=placements), edge_mask

    def _harmonize_placed_regions(self, composite: Image.Image, seed: int) -> Image.Image:
        import cv2

        result = composite.convert("RGB").copy()
        fallbacks: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = []
        allowed_union = Image.new("L", result.size, 0)
        started_all = time.perf_counter()
        prompt = "Match the surrounding lighting and add a subtle, tight contact shadow at the item's contact surface."
        for index, region in enumerate(self._placed_regions):
            x1, y1, x2, y2 = region["bbox"]
            margin = settings.hf_harmonize_region_margin_pixels
            l, t, r, b = max(0, x1-margin), max(0, y1-margin), min(result.width, x2+margin), min(result.height, y2+margin)
            original_crop = result.crop((l,t,r,b))
            item_alpha = np.asarray(region["mask"].crop((l,t,r,b)), dtype=np.uint8)
            expanded_alpha = np.asarray(
                Image.fromarray(item_alpha).filter(ImageFilter.MaxFilter(5 if region.get("flat") else 9)),
                dtype=np.uint8,
            )
            # Keep the product interior unchanged. Rugs get a thin edge band;
            # upright objects only harmonize their base and immediate contact.
            perimeter = np.maximum(expanded_alpha.astype(np.int16) - item_alpha.astype(np.int16), 0).astype(np.uint8)
            if not region.get("flat"):
                base_y = y2 - t
                contact = np.zeros_like(perimeter)
                contact[ max(0, base_y - 8):min(contact.shape[0], base_y + 4), :] = perimeter[
                    max(0, base_y - 8):min(contact.shape[0], base_y + 4), :
                ]
                perimeter = contact
            local_mask = Image.fromarray(perimeter).filter(ImageFilter.GaussianBlur(1.5))
            old_allowed = np.asarray(allowed_union.crop((l,t,r,b)))
            allowed_union.paste(Image.fromarray(np.maximum(old_allowed, np.asarray(local_mask))), (l,t))
            pad_w = (-original_crop.width) % 8
            pad_h = (-original_crop.height) % 8
            if pad_w or pad_h:
                padded_image = ImageOps.expand(original_crop, border=(0, 0, pad_w, pad_h), fill=0)
                padded_mask = ImageOps.expand(local_mask, border=(0, 0, pad_w, pad_h), fill=0)
            else:
                padded_image, padded_mask = original_crop, local_mask
            before = np.asarray(original_crop.convert("RGB"))
            generated = self._inpaint(padded_image, padded_mask, prompt, seed + index, steps=settings.hf_harmonize_steps, strength=settings.hf_harmonize_strength).crop((0,0,original_crop.width,original_crop.height))
            candidate = Image.composite(generated.convert("RGB"), original_crop.convert("RGB"), local_mask)
            after = np.asarray(candidate)
            ssim = PipelineTrace._ssim(before, after)
            fallback = ssim < settings.hf_harmonize_min_ssim
            details.append({"catalog_item_id": region["catalog_item_id"], "ssim": ssim, "fallback": fallback})
            if not fallback:
                result.paste(candidate, (l,t))
            else:
                fallbacks.append({"catalog_item_id": region["catalog_item_id"], "ssim": ssim, "reason": "SSIM below configured threshold"})
            if self._trace:
                self._trace.save_artifact(f"harmonize_{index+1:02}_{region['catalog_item_id']}.png", candidate)
            del generated, candidate
            gc.collect(); self.torch.cuda.empty_cache()
        if self._trace:
            allowed_union.save(self._trace.directory / "harmonize_region_mask.png")
            self._trace.write_json("harmonize.json", {"prompt": prompt, "strength": settings.hf_harmonize_strength, "ssim_threshold": settings.hf_harmonize_min_ssim, "seconds": time.perf_counter()-started_all, "items": details, "fallbacks": fallbacks})
            self._trace.record_operation("per_item_harmonization", seconds=time.perf_counter()-started_all, peak_allocated_vram_mb=0.0, peak_reserved_vram_mb=0.0, model_id=settings.hf_inpaint_model_id, device="cuda" if self.torch.cuda.is_available() else "cpu")
        return result

    @staticmethod
    def _remove_background(image_bytes: bytes) -> Image.Image:
        try:
            from rembg import remove
        except ImportError as exc:
            raise RuntimeError(
                "Catalog cutout compositing requires the rembg package."
            ) from exc
        cutout_bytes = remove(image_bytes, session=_rembg_u2net_session())
        with Image.open(BytesIO(cutout_bytes)) as cutout:
            rgba = cutout.convert("RGBA")
        alpha_image = rgba.getchannel("A").filter(ImageFilter.GaussianBlur(radius=0.7))
        rgba.putalpha(alpha_image)
        bbox = rgba.getchannel("A").getbbox()
        if bbox is None:
            raise RuntimeError("rembg did not find a foreground object in a catalog image.")
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
        draw.ellipse(shadow_box, fill=(0, 0, 0, 112))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(1, product.width * 0.012)))
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


class RemoteNearIdenticalError(RuntimeError):
    """The remote renderer returned an image indistinguishable from the source."""


class RemoteImageProvider:
    """Run the local catalog compositor, then ask a notebook to harmonize it."""

    def __init__(
        self,
        composite_provider: ImageProvider | None = None,
        *,
        http_client: Any | None = None,
        remote_url: str | None = None,
        remote_token: str | None = None,
        timeout_seconds: float | None = None,
        blend_margin_pixels: int | None = None,
    ) -> None:
        import httpx

        self.composite_provider = composite_provider or HuggingFaceImageProvider()
        self.http_client = http_client or httpx.Client()
        self.remote_url = settings.remote_render_url if remote_url is None else remote_url
        self.remote_token = settings.remote_render_token if remote_token is None else remote_token
        self.timeout_seconds = (
            settings.remote_render_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self.blend_margin_pixels = (
            settings.remote_blend_margin_pixels if blend_margin_pixels is None else blend_margin_pixels
        )
        self._render_metadata: dict[str, Any] = {
            "provider": "remote",
            "render_quality": "not_rendered",
        }

    @property
    def render_metadata(self) -> dict[str, Any]:
        return dict(self._render_metadata)

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
        trace_context: dict[str, Any] | None = None,
    ) -> bytes:
        import httpx

        composite = self.composite_provider.render(
            prompt, room_photo, style=style, palette=palette, items=items,
            depth_map=depth_map, seed=seed, trace_context=trace_context,
        )
        self._render_metadata = {
            **getattr(self.composite_provider, "render_metadata", {}),
            "provider": "remote",
            "remote_model_id": settings.remote_render_model_id,
        }
        if not self.remote_url:
            return self._composite_fallback(composite, "REMOTE_RENDER_URL is not configured")
        if not self.remote_token:
            return self._composite_fallback(composite, "REMOTE_RENDER_TOKEN is not configured")

        try:
            with Image.open(BytesIO(room_photo)) as source:
                input_size = source.size
            files: list[tuple[str, tuple[str, bytes, str]]] = [
                ("room_photo", ("room_photo.png", _png_bytes(room_photo), "image/png")),
                ("composite_image", ("composite.png", _png_bytes(composite), "image/png")),
            ]
            masks_by_item: dict[str, Image.Image] = {}
            for region in getattr(self.composite_provider, "_placed_regions", []) or []:
                mask = region.get("mask")
                if isinstance(mask, Image.Image):
                    masks_by_item[str(region.get("catalog_item_id"))] = mask.convert("L")
            item_records: list[dict[str, Any]] = []
            for index, item in enumerate(items):
                record = {
                    key: item.get(key)
                    for key in (
                        "catalog_item_id", "title", "name", "role", "kind", "qty",
                        "dimensions_m", "price_inr", "style_match", "x", "y", "visible",
                    )
                    if item.get(key) is not None
                }
                reference = item.get("reference_image_bytes") or item.get("floor_texture_bytes")
                if reference:
                    field = f"item_reference_{index}"
                    record["reference_image_field"] = field
                    files.append((field, (f"item_{index}.png", reference, "image/png")))
                item_mask = masks_by_item.get(str(item.get("catalog_item_id")))
                supplied_mask = item.get("item_mask_bytes") or item.get("mask_image_bytes")
                if item_mask is None and supplied_mask:
                    with Image.open(BytesIO(supplied_mask)) as opened_mask:
                        item_mask = opened_mask.convert("L")
                if item_mask is not None:
                    mask_output = BytesIO()
                    item_mask.save(mask_output, format="PNG")
                    field = f"item_mask_{index}"
                    record["mask_image_field"] = field
                    files.append((field, (f"item_mask_{index}.png", mask_output.getvalue(), "image/png")))
                item_records.append(record)
            if not any(record.get("mask_image_field") for record in item_records):
                return self._composite_fallback(composite, "No placed-item masks were available for safe remote blending")
            response = self.http_client.post(
                self.remote_url,
                data={
                    "style": style,
                    "palette": palette,
                    "seed": str(seed),
                    "prompt": prompt,
                    "items": json.dumps(item_records, ensure_ascii=False),
                },
                files=files,
                headers={"Authorization": f"Bearer {self.remote_token}"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            with Image.open(BytesIO(response.content)) as returned:
                returned.load()
                if returned.size != input_size:
                    return self._composite_fallback(
                        composite,
                        f"remote output size {returned.size} did not match input size {input_size}",
                    )
                final = returned.convert("RGB")
            source_array = np.asarray(Image.open(BytesIO(room_photo)).convert("RGB"))
            final_array = np.asarray(final)
            similarity = PipelineTrace._ssim(source_array, final_array)
            phash_distance = PipelineTrace._phash_distance(
                Image.fromarray(source_array), final,
            )
            if similarity >= 0.99 or (phash_distance is not None and phash_distance <= 4):
                self._render_metadata.update({
                    "render_quality": "rejected_near_identical",
                    "remote_similarity": similarity,
                    "remote_phash_distance": phash_distance,
                })
                raise RemoteNearIdenticalError(
                    "Remote renderer returned an image near-identical to the input room photo."
                )
            with Image.open(BytesIO(composite)) as composite_image:
                composite_rgb = composite_image.convert("RGB")
            remote_at_composite_size = final.resize(composite_rgb.size, Image.Resampling.LANCZOS)
            blended, allowed = self._blend_item_regions(
                composite_rgb, remote_at_composite_size, item_records, files,
            )
            output = BytesIO()
            blended.save(output, format="PNG")
            self._render_metadata.update({
                "render_quality": "remote_rendered",
                "remote_similarity": similarity,
                "remote_phash_distance": phash_distance,
                "remote_model_id": response.headers.get("X-Render-Model-Id", "configured-notebook-model"),
                "remote_model_license": response.headers.get("X-Render-License", "not-reported"),
                "blend_margin_pixels": self.blend_margin_pixels,
            })
            trace = getattr(self.composite_provider, "_trace", None)
            if trace is not None:
                trace.save_artifact("remote_model_output.png", remote_at_composite_size)
                trace.save_artifact("remote_region_blend_mask.png", allowed)
                trace.save_artifact("remote_blended_final.png", blended)
            return output.getvalue()
        except RemoteNearIdenticalError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            detail = ""
            response = getattr(exc, "response", None)
            if response is not None:
                try:
                    detail = f" response={response.text[:800]}"
                except Exception:
                    detail = ""
            return self._composite_fallback(composite, f"{type(exc).__name__}: {exc}{detail}")

    def _composite_fallback(self, composite: bytes, reason: str) -> bytes:
        self._render_metadata.update({
            "render_quality": "composite_fallback",
            "remote_fallback_reason": reason[:1000],
        })
        logger.warning("Remote room render fell back to composite: %s", reason)
        return composite

    def _blend_item_regions(
        self,
        composite: Image.Image,
        remote: Image.Image,
        item_records: list[dict[str, Any]],
        request_files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> tuple[Image.Image, Image.Image]:
        import cv2

        files_by_field = {field: spec[1] for field, spec in request_files}
        union = np.zeros((composite.height, composite.width), dtype=np.uint8)
        for item in item_records:
            field = item.get("mask_image_field")
            if not field:
                continue
            mask_bytes = files_by_field[field]
            with Image.open(BytesIO(mask_bytes)) as opened:
                mask = opened.convert("L").resize(composite.size, Image.Resampling.NEAREST)
            binary = (np.asarray(mask) > 0).astype(np.uint8) * 255
            margin = self.blend_margin_pixels
            if margin:
                kernel_size = 2 * margin + 1
                kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
                expanded = cv2.dilate(binary, kernel)
            else:
                expanded = binary
            if margin:
                feather = cv2.GaussianBlur(expanded, (0, 0), sigmaX=max(1.0, margin / 3.0))
                feather[expanded == 0] = 0
            else:
                feather = expanded
            union = np.maximum(union, feather)
        allowed = Image.fromarray(union, mode="L")
        if not allowed.getbbox():
            raise ValueError("Remote renderer received empty item masks.")
        return Image.composite(remote, composite, allowed), allowed

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        return self.composite_provider.locate_items(image_bytes, items)


@lru_cache(maxsize=1)
def get_image_generation_service() -> ImageProvider:
    if settings.image_provider == "stub":
        return StubImageProvider()
    if settings.image_provider == "huggingface":
        return HuggingFaceImageProvider()
    if settings.image_provider == "remote":
        return RemoteImageProvider()
    raise ValueError(f"Unsupported image provider {settings.image_provider!r}.")


class ImageGenerationService:
    """Backward-compatible configured provider facade."""

    def __init__(self) -> None:
        self.provider = get_image_generation_service()

    @property
    def render_metadata(self) -> dict[str, Any]:
        return dict(getattr(self.provider, "render_metadata", {}))

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
        trace_context: dict[str, Any] | None = None,
    ) -> bytes:
        return self.provider.render(
            prompt,
            room_photo,
            style=style,
            palette=palette,
            items=items or [],
            depth_map=depth_map,
            seed=seed,
            trace_context=trace_context,
        )

    def locate_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        return self.provider.locate_items(image_bytes, items)

    def verify_items(
        self, image_bytes: bytes, items: list[dict[str, Any]]
    ) -> CatalogImageVerification:
        return self.locate_items(image_bytes, items)
