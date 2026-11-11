"""Local SD 1.5/ControlNet room redesign with a Hugging Face fallback."""

from __future__ import annotations

from io import BytesIO
import logging
import os
from typing import Any

import httpx
from PIL import Image

from config import settings

os.environ.setdefault("HF_HOME", settings.hf_home)
os.environ.setdefault(
    "HF_HUB_DISABLE_SYMLINKS_WARNING",
    settings.hf_hub_disable_symlinks_warning,
)

try:
    import torch
except ImportError:
    torch = None

if torch is not None and not hasattr(torch, "accelerator"):
    class TorchAcceleratorPolyfill:
        @staticmethod
        def is_available() -> bool:
            return torch.cuda.is_available()

    setattr(torch, "accelerator", TorchAcceleratorPolyfill)

logger = logging.getLogger(__name__)

BASE_MODEL = "runwayml/stable-diffusion-v1-5"
DEPTH_CONTROLNET_MODEL = "lllyasviel/sd-controlnet-depth"
HF_INFERENCE_MODEL = "stabilityai/stable-diffusion-3-medium-diffusers"
HF_INFERENCE_URL = f"https://router.huggingface.co/hf-inference/models/{HF_INFERENCE_MODEL}"
MAX_IMAGE_SIDE = 512


class RedesignGenerationError(RuntimeError):
    """Raised when neither local nor hosted redesign generation succeeds."""


class RedesignService:
    """Generate redesign previews locally when possible, otherwise via HF Inference."""

    def __init__(self) -> None:
        self.hf_token = os.getenv("HF_TOKEN", settings.hf_token).strip()
        cloud_setting = os.getenv("USE_HF_CLOUD")
        if cloud_setting is None:
            cloud_setting = os.getenv("FORCE_CLOUD_API")
        if cloud_setting is None:
            cloud_setting = str(
                settings.use_hf_cloud
                if settings.use_hf_cloud is not None
                else settings.force_cloud_api
            )
        self.use_hf_cloud = cloud_setting.strip().lower() in {"true", "1", "yes"}
        self._torch: Any | None = None
        self._controlnet_pipeline: Any | None = None
        self._text_pipeline: Any | None = None
        self.local_gpu_active = False

        try:
            if torch is None:
                raise ImportError("PyTorch is not installed.")
            self._torch = torch
            self.local_gpu_active = bool(torch.cuda.is_available())
        except Exception as exc:
            logger.warning("PyTorch CUDA detection failed; using Hugging Face fallback: %s", exc)

    def generate_redesign(
        self,
        prompt: str,
        depth_map_image: Image.Image | bytes | bytearray | None = None,
    ) -> bytes:
        """Generate via the configured provider, with local fallback from HF."""
        if not prompt.strip():
            raise ValueError("A non-empty redesign prompt is required.")

        if self.use_hf_cloud:
            try:
                return self._generate_hf_cloud(prompt)
            except Exception as cloud_error:
                logger.warning(
                    "Hugging Face Cloud generation failed (%s); falling back to local SD 1.5.",
                    cloud_error,
                    exc_info=True,
                )
                try:
                    return self._generate_local(prompt, depth_map_image)
                except Exception as local_error:
                    raise RedesignGenerationError(
                        f"Hugging Face Cloud generation failed ({cloud_error}); "
                        f"local SD 1.5 generation also failed ({local_error})."
                    ) from local_error

        return self._generate_local(prompt, depth_map_image)

    def _generate_local(
        self,
        prompt: str,
        depth_map_image: Image.Image | bytes | bytearray | None,
    ) -> bytes:
        if not self.local_gpu_active:
            raise RedesignGenerationError(
                "Local SD 1.5 generation requires an available CUDA GPU. "
                "Set USE_HF_CLOUD=true to use Hugging Face Cloud."
            )
        pipe = (
            self._get_controlnet_pipeline()
            if depth_map_image is not None
            else self._get_text_pipeline()
        )
        kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_inference_steps": settings.redesign_steps,
            "guidance_scale": settings.redesign_guidance,
        }

        if depth_map_image is not None:
            control_image = self._prepare_control_image(depth_map_image)
            kwargs["image"] = control_image
            kwargs["controlnet_conditioning_scale"] = 0.8
            kwargs["width"], kwargs["height"] = control_image.size

        result = pipe(**kwargs)
        if not result.images:
            raise RuntimeError("The local diffusion pipeline returned no image.")

        output = BytesIO()
        result.images[0].convert("RGB").save(output, format="PNG")
        return output.getvalue()

    def _get_controlnet_pipeline(self) -> Any:
        if self._controlnet_pipeline is None:
            if torch is None:
                raise RuntimeError("PyTorch is required for local SD 1.5 generation.")
            from diffusers import ControlNetModel, StableDiffusionControlNetPipeline

            controlnet = ControlNetModel.from_pretrained(
                DEPTH_CONTROLNET_MODEL,
                torch_dtype=torch.float16,
                use_safetensors=True,
            )
            pipeline = StableDiffusionControlNetPipeline.from_pretrained(
                BASE_MODEL,
                controlnet=controlnet,
                torch_dtype=torch.float16,
                safety_checker=None,
                requires_safety_checker=False,
                use_safetensors=True,
            )
            self._configure_gpu_pipeline(pipeline)
            self._controlnet_pipeline = pipeline
        return self._controlnet_pipeline

    def _get_text_pipeline(self) -> Any:
        if self._text_pipeline is None:
            if torch is None:
                raise RuntimeError("PyTorch is required for local SD 1.5 generation.")
            from diffusers import StableDiffusionPipeline

            pipeline = StableDiffusionPipeline.from_pretrained(
                BASE_MODEL,
                torch_dtype=torch.float16,
                safety_checker=None,
                requires_safety_checker=False,
                use_safetensors=True,
            )
            self._configure_gpu_pipeline(pipeline)
            self._text_pipeline = pipeline
        return self._text_pipeline

    @staticmethod
    def _configure_gpu_pipeline(pipeline: Any) -> None:
        pipeline.enable_attention_slicing()
        offload = getattr(pipeline, "enable_model_cpu_offload", None)
        if callable(offload):
            offload()
        else:
            pipeline.to("cuda")

    @staticmethod
    def _as_rgb_image(image: Image.Image | bytes | bytearray) -> Image.Image:
        if isinstance(image, Image.Image):
            return image.convert("RGB")
        with Image.open(BytesIO(bytes(image))) as source:
            return source.convert("RGB")

    @classmethod
    def _prepare_control_image(
        cls, image: Image.Image | bytes | bytearray
    ) -> Image.Image:
        control_image = cls._as_rgb_image(image)
        scale = min(1.0, MAX_IMAGE_SIDE / max(control_image.size))
        width = max(8, round(control_image.width * scale / 8) * 8)
        height = max(8, round(control_image.height * scale / 8) * 8)
        if control_image.size != (width, height):
            control_image = control_image.resize(
                (width, height),
                resample=Image.Resampling.BILINEAR,
            )
        return control_image

    def _generate_hf_cloud(self, prompt: str) -> bytes:
        if not self.hf_token:
            raise RedesignGenerationError(
                "Local GPU generation is unavailable and HF_TOKEN is not configured."
            )

        try:
            response = httpx.post(
                HF_INFERENCE_URL,
                headers={"Authorization": f"Bearer {self.hf_token}"},
                json={
                    "inputs": prompt,
                    "options": {"wait_for_model": True},
                },
                timeout=120.0,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"HF API request failed: {exc}") from exc

        if response.status_code == 402:
            raise RuntimeError(
                "Hugging Face Cloud API requires paid credits (HTTP 402). "
                "Local SD 1.5 GPU pipeline active."
            )
        if response.status_code != 200:
            raise RuntimeError(
                f"HF API Error {response.status_code}: {response.text}"
            )
        content_type = response.headers.get("content-type", "").lower()
        if not response.content or not content_type.startswith(
            ("image/", "application/octet-stream")
        ):
            raise RuntimeError(
                f"HF API returned no image bytes (content-type: {content_type or 'unknown'}): "
                f"{response.text}"
            )
        return response.content
