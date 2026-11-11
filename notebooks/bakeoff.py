"""Compare the three remote notebook models on matching composites and masks.

Run `python notebooks/bakeoff.py --stub` for a fast local contact-sheet check.
Run without --stub in a GPU notebook after installing notebook/README.md deps.

The Hugging Face model cards do not publish exact T4 peak-VRAM numbers. The
figures below are FP16 / 4-bit inference estimates at the working side shown.
"""

from __future__ import annotations

import argparse
import csv
import gc
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

PHOTO_IDS = (2, 5, 6)
MAX_WORKING_SIDE = 512
BLEND_MARGIN = 12
INFERENCE_STEPS = 16
CATALOG_OBJECT_IDS = (
    "FURN-JAP-001", "CHAIR-JAP-042", "FURN-JAP-004",
    "LIGHT-JAP-007", "RUG-JAP-006", "DECOR-JAP-008",
)

CANDIDATES = [
    {
        "key": "sd15_inpaint",
        "label": "SD 1.5 Inpaint",
        "model_id": "stable-diffusion-v1-5/stable-diffusion-inpainting",
        "mode": "inpaint",
        "license": "CreativeML OpenRAIL-M",
        "vram_estimate": "6–8 GB fp16 at 512 px; model CPU offload enabled",
    },
    {
        "key": "instruct_pix2pix",
        "label": "InstructPix2Pix baseline",
        "model_id": "timbrooks/instruct-pix2pix",
        "mode": "edit",
        "license": "MIT on model card; underlying Stable Diffusion terms may also apply",
        "vram_estimate": "8–12 GB fp16 at 512 px; model CPU offload enabled",
    },
    {
        "key": "qwen_image_edit_4bit",
        "label": "Qwen Image Edit 4-bit",
        "model_id": "Qwen/Qwen-Image-Edit",
        "mode": "edit-4bit",
        "license": "Apache-2.0",
        "vram_estimate": "about 12–16 GB at 512 px with 4-bit transformer and CPU offload; may be T4-memory-bound",
    },
]

PROMPT = (
    "Keep the room's walls, windows, doors, floor plan and camera view unchanged. "
    "Make only the existing furniture lighting, contact shadows and floor contact natural. "
    "Add no other furniture or objects."
)


def encode_png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def resize_working(image: Image.Image) -> Image.Image:
    scale = min(1.0, MAX_WORKING_SIDE / max(image.size))
    size = (max(8, round(image.width * scale / 8) * 8),
            max(8, round(image.height * scale / 8) * 8))
    return image.resize(size, Image.Resampling.LANCZOS)


def blend_item_masks(composite: Image.Image, generated: Image.Image,
                     masks: list[Image.Image], margin: int = BLEND_MARGIN) -> tuple[Image.Image, Image.Image]:
    union = np.zeros((composite.height, composite.width), dtype=np.uint8)
    for source_mask in masks:
        mask = source_mask.convert("L").resize(composite.size, Image.Resampling.NEAREST)
        binary = (np.asarray(mask) > 0).astype(np.uint8) * 255
        if margin:
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * margin + 1, 2 * margin + 1))
            expanded = cv2.dilate(binary, kernel)
            feathered = cv2.GaussianBlur(expanded, (0, 0), sigmaX=max(1.0, margin / 3.0))
            feathered[expanded == 0] = 0
        else:
            feathered = binary
        union = np.maximum(union, feathered)
    allowed = Image.fromarray(union, mode="L")
    return Image.composite(generated.convert("RGB"), composite.convert("RGB"), allowed), allowed


class StubBakeoffModel:
    def render(self, image: Image.Image, mask: Image.Image, *, seed: int, mode: str) -> Image.Image:
        array = np.asarray(image.convert("RGB"), dtype=np.float32).copy()
        m = np.asarray(mask.convert("L")) > 0
        tint = {"inpaint": np.array([7, 2, -4]),
                "edit": np.array([-4, 6, -3]),
                "edit-4bit": np.array([3, -5, 7])}[mode]
        array[m] = np.clip(array[m] + tint, 0, 255)
        return Image.fromarray(array.astype(np.uint8), mode="RGB")


class DiffusersBakeoffModel:
    def __init__(self, candidate: dict[str, str]) -> None:
        self.candidate = candidate
        self.pipe: Any = None

    def _load(self) -> None:
        if self.pipe is not None:
            return
        import torch
        from diffusers import (
            StableDiffusionInpaintPipeline,
            StableDiffusionInstructPix2PixPipeline,
        )
        if self.candidate["mode"] == "inpaint":
            pipe_type = StableDiffusionInpaintPipeline
            self.pipe = pipe_type.from_pretrained(
                self.candidate["model_id"], torch_dtype=torch.float16, use_safetensors=True,
            )
        elif self.candidate["mode"] == "edit":
            self.pipe = StableDiffusionInstructPix2PixPipeline.from_pretrained(
                self.candidate["model_id"], torch_dtype=torch.float16,
            )
        else:
            from diffusers import QwenImageEditPipeline
            from diffusers import QwenImageTransformer2DModel
            from transformers import BitsAndBytesConfig
            quant = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_quant_type="nf4",
            )
            transformer = QwenImageTransformer2DModel.from_pretrained(
                self.candidate["model_id"], subfolder="transformer",
                quantization_config=quant, torch_dtype=torch.float16, device_map="balanced",
            )
            self.pipe = QwenImageEditPipeline.from_pretrained(
                self.candidate["model_id"], transformer=transformer, torch_dtype=torch.float16,
            )
        self.pipe.enable_model_cpu_offload()
        if hasattr(self.pipe, "enable_attention_slicing"):
            self.pipe.enable_attention_slicing()

    def render(self, image: Image.Image, mask: Image.Image, *, seed: int, mode: str) -> Image.Image:
        import torch
        self._load()
        generator = torch.Generator(device="cpu").manual_seed(seed)
        if mode == "inpaint":
            result = self.pipe(
                prompt=PROMPT, image=image, mask_image=mask, strength=0.3,
                num_inference_steps=INFERENCE_STEPS, guidance_scale=5.0, generator=generator,
            ).images[0]
        elif mode == "edit":
            result = self.pipe(
                prompt=PROMPT, image=image, num_inference_steps=INFERENCE_STEPS,
                guidance_scale=5.0, image_guidance_scale=1.5, generator=generator,
            ).images[0]
        else:
            result = self.pipe(
                prompt=PROMPT, image=image, num_inference_steps=INFERENCE_STEPS,
                generator=generator,
            ).images[0]
        return result.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)


def sample_inputs(stub: bool) -> list[dict[str, Any]]:
    from services.image_generation_service import HuggingFaceImageProvider

    catalog_path = BACKEND / "data" / "furniture_catalog_with_images.csv"
    thumbs = BACKEND / "data" / "catalog_image_audit" / "thumbs"
    with catalog_path.open(encoding="utf-8-sig", newline="") as handle:
        catalog = {row["product_id"]: row for row in csv.DictReader(handle)}
    reports: list[dict[str, Any]] = []
    for photo_id in PHOTO_IDS:
        source = Image.open(BACKEND / "tests" / "fixtures" / "sample_room" / f"photo_{photo_id:02}.jpg").convert("RGB")
        seed = 20261108 + photo_id
        if stub:
            composite = resize_working(source)
            draw = ImageDraw.Draw(composite)
            masks = []
            objects = [
                (0.18, 0.62, 0.49, 0.80, "#426176"),
                (0.52, 0.69, 0.66, 0.76, "#544439"),
                (0.71, 0.62, 0.83, 0.80, "#d5c9ad"),
            ]
            for x1f, y1f, x2f, y2f, color in objects:
                box = (int(composite.width*x1f), int(composite.height*y1f),
                       int(composite.width*x2f), int(composite.height*y2f))
                draw.rounded_rectangle(box, radius=8, fill=color)
                mask = Image.new("L", composite.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle(box, radius=8, fill=255)
                masks.append(mask)
        else:
            items = []
            for product_id in CATALOG_OBJECT_IDS:
                row = catalog[product_id]
                items.append({
                    "catalog_item_id": product_id, "title": row["title"], "name": row["title"],
                    "kind": "object", "role": row["role"], "qty": 1, "is_essential": True,
                    "dimensions_m": json.loads(row["dimensions_m"]), "price_inr": row["price_inr"],
                    "style_match": True, "reference_image_bytes": (thumbs / f"{product_id}.jpg").read_bytes(),
                })
            provider = HuggingFaceImageProvider()
            composite_bytes = provider.render(
                "Japandi living room; preserve architecture, catalog furniture layout",
                encode_png(source), style="Japandi", palette="Warm Earth", items=items,
                depth_map=None, seed=seed,
            )
            composite = Image.open(io.BytesIO(composite_bytes)).convert("RGB")
            masks = [entry["mask"].convert("L") for entry in provider._placed_regions]
        reports.append({"photo": photo_id, "seed": seed, "source": source,
                        "composite": composite, "masks": masks})
    return reports


def make_contact_sheet(records: list[dict[str, Any]], output_path: Path) -> None:
    columns = ["Composite"] + [candidate["label"] for candidate in CANDIDATES]
    tile_width = 448
    label_height = 34
    row_height = 0
    tiles: list[list[Image.Image]] = []
    for record in records:
        row = [record["composite"].convert("RGB")] + [record["results"][c["key"]] for c in CANDIDATES]
        resized = []
        heights = []
        for image in row:
            scale = tile_width / image.width
            tile = image.resize((tile_width, max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
            resized.append(tile)
            heights.append(tile.height)
        tiles.append(resized)
        row_height += max(heights) + label_height
    sheet = Image.new("RGB", (tile_width * len(columns), row_height), "white")
    draw = ImageDraw.Draw(sheet)
    y = 0
    for record, row in zip(records, tiles, strict=True):
        row_h = max(tile.height for tile in row)
        for col, (title, tile) in enumerate(zip(columns, row, strict=True)):
            x = col * tile_width
            draw.text((x + 8, y + 8), f"Photo {record['photo']:02} · {title}", fill="black")
            sheet.paste(tile, (x, y + label_height))
        y += row_h + label_height
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)


def run_bakeoff(*, stub: bool) -> Path:
    import torch

    records = sample_inputs(stub)
    for candidate in CANDIDATES:
        model = StubBakeoffModel() if stub else DiffusersBakeoffModel(candidate)
        for record in records:
            composite = record["composite"]
            union = Image.new("L", composite.size, 0)
            for mask in record["masks"]:
                union = Image.fromarray(np.maximum(np.asarray(union), np.asarray(mask.resize(composite.size, Image.Resampling.NEAREST))).astype(np.uint8))
            working = resize_working(composite)
            working_mask = union.resize(working.size, Image.Resampling.NEAREST)
            if torch.cuda.is_available() and not stub:
                torch.cuda.empty_cache()
                torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            generated = model.render(working, working_mask, seed=record["seed"], mode=candidate["mode"])
            seconds = time.perf_counter() - started
            peak = torch.cuda.max_memory_allocated() / (1024**3) if torch.cuda.is_available() and not stub else 0.0
            generated = generated.resize(composite.size, Image.Resampling.LANCZOS)
            result, _ = blend_item_masks(composite, generated, record["masks"])
            record.setdefault("results", {})[candidate["key"]] = result
            record.setdefault("metrics", []).append({
                "candidate": candidate["label"], "model_id": candidate["model_id"],
                "license": candidate["license"], "vram_estimate": candidate["vram_estimate"],
                "measured_peak_vram_gb": round(peak, 3), "seconds": round(seconds, 3),
                "seed": record["seed"], "stub": stub,
            })
        del model
        gc.collect()
        if torch.cuda.is_available() and not stub:
            torch.cuda.empty_cache()
    output = ROOT / "out" / "bakeoff.png"
    make_contact_sheet(records, output)
    (ROOT / "out" / "bakeoff_metrics.json").write_text(
        json.dumps({"photos": [{"photo": r["photo"], "metrics": r["metrics"]} for r in records]}, indent=2),
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stub", action="store_true", help="Use deterministic fake edits; do not load model weights.")
    args = parser.parse_args()
    path = run_bakeoff(stub=args.stub)
    print(path)


if __name__ == "__main__":
    main()
