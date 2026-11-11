"""Remote RoomSage image editor for Kaggle/Colab notebooks.

MODEL_ID may be changed before launching the server. The default is a masked
Stable Diffusion 1.5 inpainting model, which is a practical fp16 fit for a
16 GB T4 at a 512 px working side when model CPU offload is enabled.

Candidate snapshot (official Hugging Face model cards; VRAM is an inference
estimate because the cards do not publish a T4 peak-VRAM figure):
  * stable-diffusion-v1-5/stable-diffusion-inpainting — 6–8 GB fp16 at 512 px;
    CreativeML OpenRAIL-M. Picked default.
  * timbrooks/instruct-pix2pix — 8–12 GB fp16 at 512 px; model-card license
    MIT, with a note that its Stable Diffusion base may have additional terms.
  * Qwen/Qwen-Image-Edit — about 12–16 GB with a 4-bit transformer at 512 px
    plus CPU offload; Apache-2.0. This is the larger quantized edit candidate.

Cards:
  https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-inpainting
  https://huggingface.co/timbrooks/instruct-pix2pix
  https://huggingface.co/Qwen/Qwen-Image-Edit

Tunnel setup examples (keep the bearer token private):
  # Install packages with a notebook %pip cell magic, on a line by itself.
  # Kaggle: download cloudflared with urllib.request, os.chmod it, then launch
  # it with subprocess.Popen; do not use !nohup or put !chmod in Python code.
  # Colab: install pyngrok with %pip, then launch a tunnel via pyngrok.
  # Colab tunnel: from pyngrok import ngrok; ngrok.set_auth_token(NGROK_AUTH_TOKEN); public_url = ngrok.connect(8000, "http")
  # In either notebook: set REMOTE_RENDER_TOKEN to a fresh long random secret before calling main().
"""

from __future__ import annotations

import hmac
import io
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from PIL import Image, ImageOps

logger = logging.getLogger("roomsage.render_server")

MODEL_ID = "stable-diffusion-v1-5/stable-diffusion-inpainting"
MODE = "inpaint"  # "inpaint" or "edit"
MODEL_LICENSE = "CreativeML OpenRAIL-M"
MAX_WORKING_SIDE = 512
INFERENCE_STEPS = 20

SYSTEM_PROMPT = (
    "Keep the room's walls, windows, doors, floor plan, camera view, and all "
    "architectural lines unchanged. Make only the existing furniture lighting, "
    "contact shadows, and floor contact look natural. Add no other furniture, "
    "no extra objects, and do not duplicate, remove, or move any placed item."
)


class ImageEditor(Protocol):
    model_id: str

    def edit(
        self,
        original: Image.Image,
        composite: Image.Image,
        item_mask: Image.Image,
        *,
        prompt: str,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        seed: int,
    ) -> Image.Image: ...


def _multiple_of_8_size(size: tuple[int, int], max_side: int) -> tuple[int, int]:
    width, height = size
    scale = min(1.0, max_side / max(width, height))
    scaled_width = max(8, int(round(width * scale / 8) * 8))
    scaled_height = max(8, int(round(height * scale / 8) * 8))
    return scaled_width, scaled_height


class DiffusersRoomEditor:
    """Loads the configured inpaint/edit model lazily on first request."""

    def __init__(self, model_id: str = MODEL_ID, mode: str = MODE) -> None:
        if mode not in {"inpaint", "edit"}:
            raise ValueError("MODE must be 'inpaint' or 'edit'.")
        self.model_id = model_id
        self.mode = mode
        self._pipe: Any | None = None
        self._edit_backend = "instruct-pix2pix"

    def _load(self) -> Any:
        if self._pipe is not None:
            return self._pipe
        import torch
        if self.mode == "inpaint":
            from diffusers import StableDiffusionInpaintPipeline
            pipeline_type = StableDiffusionInpaintPipeline
        elif "qwen" in self.model_id.casefold():
            from diffusers import QwenImageEditPipeline, QwenImageTransformer2DModel
            from transformers import BitsAndBytesConfig
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
            transformer = QwenImageTransformer2DModel.from_pretrained(
                self.model_id,
                subfolder="transformer",
                quantization_config=quant,
                torch_dtype=torch.float16,
                device_map="balanced",
            )
            pipe = QwenImageEditPipeline.from_pretrained(
                self.model_id, transformer=transformer, torch_dtype=torch.float16,
            )
            self._edit_backend = "qwen"
        else:
            from diffusers import StableDiffusionInstructPix2PixPipeline
            pipe = StableDiffusionInstructPix2PixPipeline.from_pretrained(
                self.model_id, torch_dtype=torch.float16,
            )
            self._edit_backend = "instruct-pix2pix"
        if self.mode == "inpaint":
            pipe = pipeline_type.from_pretrained(
                self.model_id,
                torch_dtype=torch.float16,
                # This repo's safetensors components are published with the
                # fp16 variant suffix (e.g. diffusion_pytorch_model.fp16.safetensors).
                # Request the variant explicitly so Diffusers doesn't look for
                # non-existent default-variant safetensors files.
                variant="fp16",
                use_safetensors=True,
            )
        # Offload keeps the default model inside a 16 GB T4's usable budget.
        pipe.enable_model_cpu_offload()
        if hasattr(pipe, "enable_attention_slicing"):
            pipe.enable_attention_slicing()
        self._pipe = pipe
        return pipe

    def edit(
        self,
        original: Image.Image,
        composite: Image.Image,
        item_mask: Image.Image,
        *,
        prompt: str,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        seed: int,
    ) -> Image.Image:
        import torch

        target_size = original.size
        working_size = _multiple_of_8_size(composite.size, MAX_WORKING_SIDE)
        image = composite.convert("RGB").resize(working_size, Image.Resampling.LANCZOS)
        mask = item_mask.convert("L").resize(working_size, Image.Resampling.NEAREST)
        names = ", ".join(
            str(item.get("name") or item.get("title") or item.get("catalog_item_id") or "catalog item")
            for item in items if item.get("kind", "object") == "object"
        )
        full_prompt = (
            f"Style: {style}. Palette: {palette}. "
            f"Existing catalog items in the supplied layout: {names}. "
            f"Additional render direction: {prompt}. {SYSTEM_PROMPT}"
        )
        pipe = self._load()
        generator = torch.Generator(device="cpu").manual_seed(int(seed))
        if self.mode == "inpaint":
            output = pipe(
                prompt=full_prompt,
                image=image,
                mask_image=mask,
                num_inference_steps=INFERENCE_STEPS,
                guidance_scale=5.0,
                strength=0.25,
                generator=generator,
            ).images[0]
        elif self._edit_backend == "qwen":
            output = pipe(
                prompt=full_prompt,
                image=image,
                num_inference_steps=INFERENCE_STEPS,
                generator=generator,
            ).images[0]
        else:
            output = pipe(
                prompt=full_prompt,
                image=image,
                num_inference_steps=INFERENCE_STEPS,
                guidance_scale=5.0,
                image_guidance_scale=1.5,
                generator=generator,
            ).images[0]
        return output.convert("RGB").resize(target_size, Image.Resampling.LANCZOS)


@dataclass
class StubEditor:
    """Deterministic editor used by local tests; no model weights are loaded."""

    model_id: str = "stub-room-editor"

    def edit(
        self,
        original: Image.Image,
        composite: Image.Image,
        item_mask: Image.Image,
        *,
        prompt: str,
        style: str,
        palette: str,
        items: list[dict[str, Any]],
        seed: int,
    ) -> Image.Image:
        del original, prompt, style, palette, items, seed
        result = composite.convert("RGB").copy()
        # Visible but deterministic in-mask change proving the handler's path.
        if item_mask.getbbox():
            pixel = result.getpixel((0, 0))
            result.putpixel((0, 0), tuple(max(0, channel - 1) for channel in pixel))
        return result


def _image_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def create_app(editor: ImageEditor | None = None, *, token: str | None = None) -> FastAPI:
    app = FastAPI(title="RoomSage remote image renderer")
    app.state.editor = editor
    app.state.token = token if token is not None else os.getenv("REMOTE_RENDER_TOKEN", "")

    @app.get("/health")
    async def health() -> dict[str, str]:
        editor = app.state.editor
        return {
            "status": "ok",
            "model_id": getattr(editor, "model_id", MODEL_ID),
            "mode": getattr(editor, "mode", MODE),
            "weight_variant": "fp16" if MODE == "inpaint" else "model-default",
            "safetensors": "true" if MODE == "inpaint" else "model-default",
            "license": MODEL_LICENSE,
        }

    @app.post("/render")
    async def render(request: Request) -> Response:
        expected = app.state.token
        supplied = request.headers.get("Authorization", "")
        if not expected or not hmac.compare_digest(supplied, f"Bearer {expected}"):
            raise HTTPException(status_code=401, detail="Invalid bearer token.")
        try:
            form = await request.form()
            original_upload = form.get("room_photo")
            composite_upload = form.get("composite_image")
            if original_upload is None or composite_upload is None:
                raise ValueError("room_photo and composite_image are required")
            original_bytes = await original_upload.read()
            composite_bytes = await composite_upload.read()
            with Image.open(io.BytesIO(original_bytes)) as opened:
                original = ImageOps.exif_transpose(opened).convert("RGB")
            with Image.open(io.BytesIO(composite_bytes)) as opened:
                composite = ImageOps.exif_transpose(opened).convert("RGB")
            items = json.loads(str(form.get("items", "[]")))
            if not isinstance(items, list):
                raise ValueError("items must be a JSON list")
            mask_union = Image.new("L", composite.size, 0)
            for item in items:
                mask_field = item.get("mask_image_field")
                if mask_field and form.get(mask_field) is not None:
                    mask_bytes = await form[mask_field].read()
                    with Image.open(io.BytesIO(mask_bytes)) as opened:
                        item_mask = opened.convert("L").resize(composite.size, Image.Resampling.NEAREST)
                    mask_union = Image.fromarray(
                        __import__("numpy").maximum(
                            __import__("numpy").asarray(mask_union),
                            __import__("numpy").asarray(item_mask),
                        ).astype("uint8"),
                        mode="L",
                    )
                reference_field = item.get("reference_image_field")
                if reference_field and form.get(reference_field) is not None:
                    item["reference_image_bytes"] = await form[reference_field].read()
            if not mask_union.getbbox():
                raise ValueError("At least one non-empty item mask is required")
            selected_editor = app.state.editor or DiffusersRoomEditor(MODEL_ID, MODE)
            result = selected_editor.edit(
                original,
                composite,
                mask_union,
                prompt=str(form.get("prompt", "")),
                style=str(form.get("style", "")),
                palette=str(form.get("palette", "")),
                items=items,
                seed=int(form.get("seed", "0")),
            )
            if result.size != original.size:
                raise ValueError(
                    f"editor output size {result.size} did not match room photo {original.size}"
                )
            return Response(
                content=_image_bytes(result.convert("RGB")),
                media_type="image/png",
                headers={"X-Render-Model-Id": selected_editor.model_id,
                         "X-Render-License": MODEL_LICENSE},
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Remote render request failed")
            raise HTTPException(status_code=422, detail=f"Render request failed: {exc}") from exc

    return app


app = create_app(StubEditor() if os.getenv("ROOMSAGE_STUB_MODEL") == "1" else None)


def main() -> None:
    import uvicorn

    if not os.getenv("REMOTE_RENDER_TOKEN"):
        raise SystemExit("Set REMOTE_RENDER_TOKEN to a long random bearer token before starting.")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))


if __name__ == "__main__":
    main()
