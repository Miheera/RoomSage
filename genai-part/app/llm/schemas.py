"""Design data models. The two *Draft/Patch models are all an LLM may produce, and
`extra="forbid"` plus the absence of product and price fields keeps it that way."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import Category, RoomType, Theme
from app.scene.models import SceneInfo


class Priority(str, Enum):
    ESSENTIAL = "essential"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class ChangeAction(str, Enum):
    KEEP = "keep"
    REMOVE = "remove"
    REPLACE = "replace"
    ADD = "add"


class FurnitureChange(BaseModel):
    action: ChangeAction
    category: Category | None = None
    label: str
    reason: str | None = None
    mask_id: int | None = None


class ProductRequirement(BaseModel):
    slot: str
    category: Category
    priority: Priority
    preferred_colors: list[str] = Field(default_factory=list)
    preferred_materials: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    max_width_cm: float | None = None
    small_room_only: bool = False
    excluded_product_ids: list[str] = Field(default_factory=list)


class DesignRequest(BaseModel):
    theme: Theme
    budget_inr: float = Field(gt=0)
    room_type: RoomType | None = None
    scene: SceneInfo | None = None
    retain: list[str] = Field(default_factory=list, description="Objects to keep, e.g. 'bed'.")
    remove: list[str] = Field(default_factory=list)
    replace: list[str] = Field(default_factory=list)
    add: list[str] = Field(default_factory=list)
    instructions: str | None = Field(default=None, max_length=2000)
    small_room: bool = False


class DesignSpec(BaseModel):
    theme: Theme
    room_type: RoomType
    color_palette: list[str]
    accent_colors: list[str]
    materials: list[str]
    furniture_changes: list[FurnitureChange]
    layout_constraints: list[str]
    product_requirements: list[ProductRequirement]
    budget_limit_inr: float
    small_room: bool = False
    redesign_prompt: str
    negative_prompt: str
    edit_prompt_3d: str
    rationale: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    generated_by: str = "rules"


class LLMDesignDraft(BaseModel):
    """The creative part of a spec. Slots, products and prices stay deterministic."""

    model_config = ConfigDict(extra="forbid")

    color_palette: list[str] = Field(description="3-5 colour names for the main surfaces.")
    accent_colors: list[str] = Field(description="1-3 accent colour names.")
    materials: list[str] = Field(description="3-6 material names.")
    layout_constraints: list[str] = Field(description="Practical layout rules for this room.")
    redesign_prompt: str = Field(description="Stable Diffusion prompt, under 50 words.")
    edit_prompt_3d: str = Field(description="One instruction for editing the 3D scene's style.")
    rationale: str = Field(description="One or two sentences on the design direction.")


class ColorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    colors: list[str]


class RefinementPatch(BaseModel):
    """A natural-language request reduced to the only changes refinement supports."""

    model_config = ConfigDict(extra="forbid")

    theme: Theme | None = None
    budget_limit_inr: float | None = Field(default=None, gt=0)
    small_room: bool | None = None
    keep_categories: list[Category] = Field(default_factory=list)
    remove_categories: list[Category] = Field(default_factory=list)
    reduce_decor: bool = False
    color_requests: list[ColorRequest] = Field(
        default_factory=list, description="Colours asked for, per product category."
    )
    understood: bool = True
