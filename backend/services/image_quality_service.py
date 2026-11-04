"""Photo quality checks and resolution normalization for room captures."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import cv2
import numpy as np
from PIL import Image, ImageOps

MAX_IMAGE_DIMENSION = 3840
BLUR_VARIANCE_THRESHOLD = 100.0


@dataclass(frozen=True)
class ImageQualityReport:
    normalized_bytes: bytes
    width: int
    height: int
    blur_score: float
    is_blurry: bool
    mean_intensity: float
    lighting_status: str
    status: str
    feedback_message: str


class ImageQualityAnalyzer:
    """Normalize orientation/size and assess blur and average exposure."""

    def __init__(self, *, max_dimension: int = MAX_IMAGE_DIMENSION, blur_threshold: float = BLUR_VARIANCE_THRESHOLD) -> None:
        if max_dimension < 1:
            raise ValueError("max_dimension must be positive.")
        self.max_dimension = max_dimension
        self.blur_threshold = blur_threshold

    def analyze_and_normalize(self, image_bytes: bytes, image_format: str) -> ImageQualityReport:
        """Return normalized image bytes and quality measurements."""
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError("The uploaded file is not a valid, readable image.") from exc

        width, height = image.size
        if max(width, height) > self.max_dimension:
            scale = self.max_dimension / max(width, height)
            image = image.resize(
                (max(1, round(width * scale)), max(1, round(height * scale))),
                Image.Resampling.LANCZOS,
            )

        gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        mean_intensity = float(gray.mean())
        is_blurry = blur_score < self.blur_threshold
        if mean_intensity < 40:
            lighting_status = "too_dark"
        elif mean_intensity > 220:
            lighting_status = "too_bright"
        else:
            lighting_status = "good"

        messages: list[str] = []
        if is_blurry:
            messages.append("Warning: Photo is blurry. Keep your camera steady and retake for best 3D quality.")
        if lighting_status == "too_dark":
            messages.append("Warning: Photo is too dark. Turn on room lights or open blinds.")
        elif lighting_status == "too_bright":
            messages.append("Warning: Photo is too bright. Reduce direct light or close the curtains slightly.")
        if not messages:
            messages.append("Photo looks sharp and well-lit.")

        format_name = image_format.upper()
        options: dict[str, object] = {}
        if format_name in {"JPEG", "JPG"}:
            format_name, options = "JPEG", {"quality": 95, "optimize": True}
        elif format_name == "WEBP":
            options = {"quality": 95, "method": 4}
        elif format_name == "PNG":
            options = {"optimize": True}
        else:
            raise ValueError(f"Unsupported image format: {image_format}.")
        output = BytesIO()
        image.save(output, format=format_name, **options)

        return ImageQualityReport(
            normalized_bytes=output.getvalue(),
            width=image.width,
            height=image.height,
            blur_score=blur_score,
            is_blurry=is_blurry,
            mean_intensity=mean_intensity,
            lighting_status=lighting_status,
            status="warning" if is_blurry or lighting_status != "good" else "passed",
            feedback_message=" ".join(messages),
        )


image_quality_analyzer = ImageQualityAnalyzer()
