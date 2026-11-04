"""Mask-and-inpaint room redesign, sized for a 6 GB GPU.

Steps: segment the photo (UperNet/ADE20K), estimate depth from that same photo
(Depth Anything V2 Small), then inpaint the masked region with SD 1.5 guided by a depth
ControlNet. Segmentation and depth are released before the SD pipeline loads so that
they never compete for VRAM. The mask-then-inpaint approach follows
ml6team/controlnet-interior-design; no code was copied from it.

Heavy imports stay inside functions: the service must start without torch installed.
"""

import gc
import logging
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from app.config import Settings
from app.core.errors import UnavailableError
from app.redesign import segmentation as seg_utils
from app.redesign.base import DetectedObject, EditScope, RenderRequest, RenderResult

logger = logging.getLogger(__name__)

# Below this a furniture mask usually means an empty room, so there is nothing to swap.
MIN_MASK_FRACTION = 0.03
MASK_DILATION_PX = 8
LIMITATIONS = [
    "Layout follows estimated depth; furniture positions and proportions are approximate.",
    "Generated items are illustrative and do not depict the recommended products.",
    "Walls, ceilings and openings outside the mask are original pixels. Masked areas are repainted, "
    "so geometry inside them is not guaranteed.",
]


def _load_rgb(path: Path, max_side: int) -> Image.Image:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    scale = min(1.0, max_side / max(image.size))
    # SD's VAE needs dimensions divisible by 8.
    width = max(8, int(image.width * scale) // 8 * 8)
    height = max(8, int(image.height * scale) // 8 * 8)
    return image.resize((width, height), Image.LANCZOS)


class DiffusionRenderer:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pipe = None

    def _torch(self):
        try:
            import torch
        except ImportError as exc:
            raise UnavailableError("REDESIGN_MODE=diffusion needs requirements-gpu.txt installed.") from exc
        return torch

    def _device(self, torch) -> str:
        if self._settings.redesign_device == "cuda" and not torch.cuda.is_available():
            return "cpu"
        return self._settings.redesign_device

    def _free(self, torch) -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _segment(self, image: Image.Image) -> tuple[np.ndarray, dict[int, str]]:
        torch = self._torch()
        from transformers import AutoImageProcessor, UperNetForSemanticSegmentation

        device = self._device(torch)
        processor = AutoImageProcessor.from_pretrained(self._settings.segmentation_model)
        model = UperNetForSemanticSegmentation.from_pretrained(self._settings.segmentation_model).to(device)
        inputs = processor(images=image, return_tensors="pt").to(device)
        with torch.inference_mode():
            logits = model(**inputs).logits
        logits = torch.nn.functional.interpolate(
            logits, size=image.size[::-1], mode="bilinear", align_corners=False
        )
        seg = logits.argmax(dim=1)[0].cpu().numpy()
        id2label = dict(model.config.id2label)
        del model, inputs, logits
        self._free(torch)
        return seg, id2label

    @staticmethod
    def _load_depth(path: Path, size: tuple[int, int]) -> Image.Image:
        """Load Person A's 8-bit depth PNG in the image form expected by ControlNet."""
        try:
            with Image.open(path) as source:
                depth = ImageOps.exif_transpose(source).convert("RGB")
        except (OSError, ValueError) as exc:
            raise UnavailableError(f"Could not read Person A depth map at {path}.") from exc
        if depth.size != size:
            depth = depth.resize(size, Image.Resampling.BILINEAR)
        return depth

    def _pipeline(self):
        if self._pipe is not None:
            return self._pipe
        torch = self._torch()
        from diffusers import (
            ControlNetModel,
            StableDiffusionControlNetInpaintPipeline,
            UniPCMultistepScheduler,
        )

        cuda = self._device(torch) == "cuda"
        dtype = torch.float16 if cuda else torch.float32
        load_kwargs = {"torch_dtype": dtype, "use_safetensors": True}
        if cuda:
            load_kwargs["variant"] = "fp16"
        controlnet = ControlNetModel.from_pretrained(self._settings.controlnet_model, **load_kwargs)
        pipe = StableDiffusionControlNetInpaintPipeline.from_pretrained(
            self._settings.inpaint_model, controlnet=controlnet, **load_kwargs,
            safety_checker=None, requires_safety_checker=False,
        )
        pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)
        pipe.vae.enable_slicing()
        if cuda:
            pipe.enable_attention_slicing()
            pipe.enable_model_cpu_offload()
        self._pipe = pipe
        return pipe

    def _resolve_mask(
        self, request: RenderRequest, seg: np.ndarray, id2label: dict[int, str]
    ) -> tuple[np.ndarray, EditScope, list[str]]:
        scope = EditScope(request.scope)
        notes: list[str] = []
        mask = seg_utils.build_mask(seg, id2label, scope, request.target_categories, request.keep_categories)
        if mask.mean() < MIN_MASK_FRACTION and scope is not EditScope.FULL:
            notes.append("Little furniture was detected, so walls and floor were repainted as well.")
            scope = EditScope.FULL
            mask = seg_utils.build_mask(seg, id2label, scope, keep_categories=request.keep_categories)
        if not mask.any():
            raise UnavailableError("Nothing in this photo could be selected for repainting.")
        return seg_utils.dilate_mask(mask, seg, id2label, MASK_DILATION_PX, request.keep_categories), scope, notes

    def render(self, request: RenderRequest) -> RenderResult:
        torch = self._torch()
        settings = self._settings
        image = _load_rgb(request.image_path, settings.redesign_max_side)
        seg, id2label = self._segment(image)
        mask, scope, notes = self._resolve_mask(request, seg, id2label)
        if request.depth_map_path is None:
            raise UnavailableError("Person A's depth_map_path is required for diffusion redesign.")
        depth = self._load_depth(request.depth_map_path, image.size)

        pipe = self._pipeline()
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        mask_image = Image.fromarray((mask * 255).astype(np.uint8))
        output = pipe(
            prompt=request.prompt,
            negative_prompt=request.negative_prompt,
            image=image,
            mask_image=mask_image,
            control_image=depth,
            height=image.height,
            width=image.width,
            strength=1.0,
            num_inference_steps=settings.redesign_steps,
            guidance_scale=settings.redesign_guidance,
            controlnet_conditioning_scale=settings.redesign_controlnet_scale,
            generator=torch.Generator("cpu").manual_seed(settings.redesign_seed),
        ).images[0]
        peak = torch.cuda.max_memory_allocated() / 2**20 if torch.cuda.is_available() else None

        final = seg_utils.soft_composite(image, output.resize(image.size), mask)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        final.save(request.output_path)
        mask_path = request.output_path.with_name(f"{request.output_path.stem}_mask.png")
        mask_image.save(mask_path)
        return RenderResult(
            image_path=request.output_path,
            mask_path=mask_path,
            is_mock=False,
            scope_used=scope,
            masked_fraction=round(float(mask.mean()), 3),
            peak_vram_mb=round(peak, 1) if peak else None,
            limitations=[*notes, *LIMITATIONS],
        )

    def analyze(self, image_path: Path) -> list[DetectedObject]:
        image = _load_rgb(image_path, self._settings.redesign_max_side)
        seg, id2label = self._segment(image)
        ids, counts = np.unique(seg, return_counts=True)
        objects = []
        for class_id, count in sorted(zip(ids, counts), key=lambda pair: -pair[1]):
            name = id2label[int(class_id)]
            category = seg_utils.category_for_ade_label(name)
            if category is not None and count / seg.size >= 0.005:
                objects.append(DetectedObject(label=seg_utils.primary_label(name), category=category,
                                              area_fraction=round(float(count / seg.size), 3)))
        return objects


def build_renderer(settings: Settings):
    if settings.redesign_mode == "diffusion":
        return DiffusionRenderer(settings)
    from app.redesign.mock import MockRenderer

    return MockRenderer()
