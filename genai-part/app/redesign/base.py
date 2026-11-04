from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from app.core.models import Category


class EditScope(str, Enum):
    FURNITURE = "furniture"  # repaint furniture, keep walls, floor, windows and doors
    FULL = "full"  # also repaint walls and floor, keep windows and doors
    TARGETED = "targeted"  # repaint only `target_categories`


class RenderRequest(BaseModel):
    image_path: Path
    # Person A's 8-bit depth PNG. Required by the diffusion renderer.
    depth_map_path: Path | None = None
    output_path: Path
    prompt: str
    negative_prompt: str
    scope: EditScope = EditScope.FURNITURE
    target_categories: list[Category] = Field(default_factory=list)
    # Pixels of these stay as photographed, e.g. after "keep the existing bed".
    keep_categories: list[Category] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)


class RenderResult(BaseModel):
    image_path: Path
    mask_path: Path | None = None
    is_mock: bool
    scope_used: EditScope
    masked_fraction: float | None = None
    peak_vram_mb: float | None = None
    limitations: list[str] = Field(default_factory=list)


class DetectedObject(BaseModel):
    label: str
    category: Category | None = None
    area_fraction: float


class Renderer(Protocol):
    def render(self, request: RenderRequest) -> RenderResult: ...

    def analyze(self, image_path: Path) -> list[DetectedObject]: ...
