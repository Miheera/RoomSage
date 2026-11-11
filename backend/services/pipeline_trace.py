"""Opt-in, best-effort artifacts for auditing the image-generation pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from config import settings

logger = logging.getLogger(__name__)


def trace_enabled() -> bool:
    return os.getenv("TRACE_PIPELINE") == "1"


class PipelineTrace:
    def __init__(self, design_id: str, metadata: dict[str, Any]) -> None:
        safe_design_id = re.sub(r"[^A-Za-z0-9._-]", "_", design_id)
        self.directory = Path(settings.render_output_dir) / "trace" / safe_design_id
        self.operations: dict[str, dict[str, Any]] = {}
        self.stages: list[dict[str, Any]] = []
        self.comparisons: list[dict[str, Any]] = []
        self.errors: list[str] = []
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.write_json("metadata.json", metadata)
            self.write_json("model_devices.json", {
                "ade20k_segmentation": {"model_id": metadata.get("model_ids", {}).get("segmentation"), "loaded_on": "cpu", "execution_on": "cpu"},
                "surface_edits": {"model_id": "OpenCV LAB + perspective warp", "loaded_on": "n/a", "execution_on": "cpu"},
                "background_removal": {"model_id": "rembg/u2net", "loaded_on": "not_loaded", "execution_on": "not_run (no catalog object items in this trace)"},
                "catalog_placement": {"model_id": "Pillow compositing", "loaded_on": "n/a", "execution_on": "surface-only; no catalog objects supplied"},
            })
        except Exception:
            self._record_error("Could not initialize trace output.")

    def write_json(self, filename: str, value: Any) -> None:
        try:
            (self.directory / filename).write_text(
                json.dumps(value, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            self._record_error(f"Could not write {filename}: {exc}")

    def record_operation(
        self,
        name: str,
        *,
        seconds: float,
        peak_allocated_vram_mb: float,
        peak_reserved_vram_mb: float,
        model_id: str,
        device: str = "cpu",
        error: str | None = None,
    ) -> None:
        operation = {
            "seconds": seconds,
            "peak_allocated_vram_mb": peak_allocated_vram_mb,
            "peak_reserved_vram_mb": peak_reserved_vram_mb,
            "model_id": model_id,
            "device": device,
            "error": error,
        }
        self.operations[name] = operation
        logger.info("PIPELINE_TRACE %s", json.dumps({"stage": name, **operation}))

    def save_artifact(
        self,
        filename: str,
        image: Image.Image,
        *,
        operation_names: tuple[str, ...] = (),
        model_id: str | None = None,
    ) -> None:
        try:
            path = self.directory / filename
            image.save(path, format="PNG")
            current = image.convert("RGB")
            operations = [self.operations[name] for name in operation_names if name in self.operations]
            models = sorted({str(operation["model_id"]) for operation in operations})
            devices = sorted({str(operation.get("device", "cpu")) for operation in operations})
            stage = {
                "stage": filename,
                "file": str(path),
                "seconds": sum(float(operation["seconds"]) for operation in operations),
                "peak_allocated_vram_mb": max(
                    (float(operation["peak_allocated_vram_mb"]) for operation in operations),
                    default=None,
                ),
                "peak_reserved_vram_mb": max(
                    (float(operation["peak_reserved_vram_mb"]) for operation in operations),
                    default=None,
                ),
                "model_id": model_id or (", ".join(models) if models else "no model"),
                "device": " + ".join(devices) if devices else "cpu",
            }
            self.stages.append(stage)
            logger.info("PIPELINE_TRACE %s", json.dumps(stage))
            self.write_json("stages.json", self.stages)
        except Exception as exc:
            self._record_error(f"Could not save stage {filename}: {exc}")

    def compare_stages(self, first_name: str, first: Image.Image, second_name: str, second: Image.Image) -> None:
        try:
            a, b = first.convert("RGB"), second.convert("RGB")
            if b.size != a.size:
                b = b.resize(a.size, Image.Resampling.LANCZOS)
            aa, bb = np.asarray(a), np.asarray(b)
            distance = self._phash_distance(a, b)
            result = {"from": first_name, "to": second_name,
                      "ssim": self._ssim(aa, bb), "phash_distance": distance,
                      "differs": not np.array_equal(aa, bb)}
            self.comparisons.append(result)
            self.write_json("stage_comparisons.json", self.comparisons)
        except Exception as exc:
            self._record_error(f"Could not compare {first_name} to {second_name}: {exc}")

    def save_item_cutout(self, item_id: str, image: Image.Image) -> None:
        try:
            safe_item_id = re.sub(r"[^A-Za-z0-9._-]", "_", item_id)
            image.save(self.directory / f"item_{safe_item_id}_cutout.png", format="PNG")
        except Exception as exc:
            self._record_error(f"Could not save item cutout {item_id}: {exc}")

    def save_hotspots(self, items: list[Any]) -> None:
        self.write_json(
            "hotspots.json",
            [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in items
            ],
        )

    def compare_final(self, input_bytes: bytes, output_bytes: bytes) -> None:
        try:
            with Image.open(Path(self.directory / "01_input.png")) as source:
                source_rgb = source.convert("RGB")
            with Image.open(Path(self.directory / "10_final.png")) as result:
                result_rgb = result.convert("RGB")
            if result_rgb.size != source_rgb.size:
                result_rgb = result_rgb.resize(source_rgb.size, Image.Resampling.LANCZOS)
            source_array = np.asarray(source_rgb)
            result_array = np.asarray(result_rgb)
            ssim = self._ssim(source_array, result_array)
            import cv2

            source_gray = cv2.cvtColor(source_array, cv2.COLOR_RGB2GRAY)
            result_gray = cv2.cvtColor(result_array, cv2.COLOR_RGB2GRAY)
            phash_distance = self._phash_distance(
                Image.fromarray(source_gray),
                Image.fromarray(result_gray),
            )
            self.write_json(
                "comparison.json",
                {
                    "input_sha256": hashlib.sha256(input_bytes).hexdigest(),
                    "final_sha256": hashlib.sha256(output_bytes).hexdigest(),
                    "identical_bytes": input_bytes == output_bytes,
                    "identical_pixels": bool(np.array_equal(source_array, result_array)),
                    "ssim": ssim,
                    "phash_distance": phash_distance,
                    "near_identical": ssim >= 0.99 or (phash_distance is not None and phash_distance <= 4),
                    "near_identical_threshold": {"ssim_gte": 0.99, "phash_distance_lte": 4},
                },
            )
        except Exception as exc:
            self._record_error(f"Could not compare input and final images: {exc}")

    def finish(self, status: str, error: str | None = None) -> None:
        self.write_json(
            "run.json",
            {
                "status": status,
                "error": error,
                "trace_errors": self.errors,
                "stage_count": len(self.stages),
            },
        )

    def _record_error(self, message: str) -> None:
        self.errors.append(message)
        logger.exception("PIPELINE_TRACE %s", message)

    @staticmethod
    def _phash_distance(
        previous: Image.Image | None, current: Image.Image,
    ) -> int | None:
        if previous is None:
            return None
        import cv2

        def phash(image: Image.Image) -> np.ndarray:
            gray = np.asarray(image.convert("L").resize((32, 32)), dtype=np.float32)
            coefficients = cv2.dct(gray)[:8, :8].flatten()[1:]
            return coefficients > np.median(coefficients)

        return int(np.count_nonzero(phash(previous) != phash(current)))

    @staticmethod
    def _ssim(first: np.ndarray, second: np.ndarray) -> float:
        import cv2

        first_gray = cv2.cvtColor(first, cv2.COLOR_RGB2GRAY).astype(np.float64) / 255.0
        second_gray = cv2.cvtColor(second, cv2.COLOR_RGB2GRAY).astype(np.float64) / 255.0
        mu_first = cv2.GaussianBlur(first_gray, (11, 11), 1.5)
        mu_second = cv2.GaussianBlur(second_gray, (11, 11), 1.5)
        sigma_first = cv2.GaussianBlur(first_gray * first_gray, (11, 11), 1.5) - mu_first * mu_first
        sigma_second = cv2.GaussianBlur(second_gray * second_gray, (11, 11), 1.5) - mu_second * mu_second
        covariance = (
            cv2.GaussianBlur(first_gray * second_gray, (11, 11), 1.5)
            - mu_first * mu_second
        )
        c1, c2 = 0.01**2, 0.03**2
        score = (
            (2 * mu_first * mu_second + c1) * (2 * covariance + c2)
            / ((mu_first * mu_first + mu_second * mu_second + c1)
               * (sigma_first + sigma_second + c2))
        )
        return float(score.mean())
