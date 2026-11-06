"""Structured request and response models for Gemini powered design stages."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RoomSceneAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    room_type: str
    primary_wall_color: str
    existing_furniture: list[str]
    lighting_condition: str
    structural_features: list[str]


class RefinedDesignPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    controlnet_prompt: str = Field(description="Optimized prompt for a depth conditioned interior redesign.")
    negative_prompt: str = Field(default="")
    style_parameters: dict[str, Any] = Field(default_factory=dict)


class ChatRefinementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str
    refined_prompt: str


class ShoppingListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str
    title: str
    category: str
    price_inr: float = Field(ge=0)
    buy_url: str


class ShoppingListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    theme: str
    budget_cap_inr: float = Field(ge=0)
    total_cost_inr: float = Field(ge=0)
    selected_items: list[ShoppingListItem]
    paint_estimate_inr: float = Field(ge=0)
    flooring_estimate_inr: float = Field(ge=0)

