"""ControlNet room preview renderer using Person A's saved depth map."""

from functools import lru_cache
from io import BytesIO

from config import settings


class ControlNetUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _pipeline():
    try:
        import torch
        from diffusers import (
            ControlNetModel,
            StableDiffusionControlNetInpaintPipeline,
            UniPCMultistepScheduler,
        )
    except ImportError as exc:
        raise ControlNetUnavailable("Install the GPU diffusion dependencies to run redesign jobs.") from exc

    cuda = settings.redesign_device == "cuda" and torch.cuda.is_available()
    dtype = torch.float16 if cuda else torch.float32
    kwargs = {"torch_dtype": dtype, "use_safetensors": True}
    if cuda:
        kwargs["variant"] = "fp16"
    controlnet = ControlNetModel.from_pretrained(settings.controlnet_model, **kwargs)
    pipe = StableDiffusionControlNetInpaintPipeline.from_pretrained(
        settings.inpaint_model,
        controlnet=controlnet,
        safety_checker=None,
        requires_safety_checker=False,
        **kwargs,
    )
    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config)
    pipe.vae.enable_slicing()
    if cuda:
        pipe.enable_attention_slicing()
        pipe.enable_model_cpu_offload()
    else:
        pipe.to("cpu")
    return pipe, torch


def render_redesign(image_bytes: bytes, depth_bytes: bytes, prompt: str) -> bytes:
    """Render a room redesign conditioned on the 8-bit grayscale depth PNG."""
    from PIL import Image, ImageOps

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        with Image.open(BytesIO(depth_bytes)) as source:
            depth = ImageOps.exif_transpose(source).convert("RGB")
    except (OSError, ValueError) as exc:
        raise ValueError("Could not decode the room photo or depth map.") from exc
    factor = min(1.0, settings.redesign_max_side / max(image.size))
    size = (max(8, int(image.width * factor) // 8 * 8), max(8, int(image.height * factor) // 8 * 8))
    image = image.resize(size, Image.Resampling.LANCZOS)
    depth = depth.resize(size, Image.Resampling.BILINEAR)
    mask = Image.new("L", size, 255)
    pipe, torch = _pipeline()
    result = pipe(
        prompt=prompt,
        image=image,
        mask_image=mask,
        control_image=depth,
        height=size[1], width=size[0], strength=1.0,
        num_inference_steps=settings.redesign_steps,
        guidance_scale=settings.redesign_guidance,
        controlnet_conditioning_scale=settings.redesign_controlnet_scale,
        generator=torch.Generator("cpu").manual_seed(settings.redesign_seed),
    ).images[0]
    output = BytesIO()
    result.convert("RGB").save(output, format="PNG")
    return output.getvalue()
