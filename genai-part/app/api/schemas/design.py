from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.budget.optimizer import BudgetResult
from app.core.models import Product
from app.llm.schemas import DesignRequest, DesignSpec
from app.recommendations.engine import SlotRecommendation
from app.redesign.base import DetectedObject
from app.services.planner import TransformationPlan
from app.services.refinement import RefinementOutcome


class ErrorResponse(BaseModel):
    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CreateDesignRequest(DesignRequest):
    model_config = ConfigDict(json_schema_extra={"example": {
        "theme": "japandi",
        "budget_inr": 60000,
        "room_type": "living_room",
        "retain": ["curtain"],
        "small_room": True,
        "scene": {"room_type": "living_room",
                  "dimensions": {"length_m": 4.2, "width_m": 3.4, "height_m": 2.8}},
    }})


class SceneNormalizeRequest(BaseModel):
    raw: dict[str, Any] = Field(default_factory=dict, description="Partial scene data in any shape.")
    room_type: str | None = None


class DesignSummary(BaseModel):
    design_id: str
    spec: DesignSpec
    warnings: list[str]


class RecommendationsResponse(BaseModel):
    design_id: str
    slots: list[SlotRecommendation]


class BudgetRequest(BaseModel):
    budget_inr: float | None = Field(default=None, gt=0, description="Override the design's budget.")


class BudgetResponse(BaseModel):
    design_id: str
    budget: BudgetResult


class CalculateRequest(BaseModel):
    product_ids: list[str] = Field(min_length=1)
    budget_inr: float = Field(gt=0)


class PlanResponse(BaseModel):
    design_id: str
    plan: TransformationPlan


class RefineRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    model_config = ConfigDict(json_schema_extra={"example": {"message": "Keep everything under ₹20,000."}})


class RefineResponse(BaseModel):
    design_id: str
    outcome: RefinementOutcome
    spec: DesignSpec
    budget: BudgetResult | None
    plan: TransformationPlan | None


class PreviewRequest(BaseModel):
    project_id: str = Field(min_length=1)


class UploadResponse(BaseModel):
    upload_id: str
    filename: str


class AnalysisResponse(BaseModel):
    upload_id: str
    objects: list[DetectedObject]
    note: str | None = None


class ThemeInfo(BaseModel):
    id: str
    label: str
    description: str
    palette: list[str]
    accent_colors: list[str]
    materials: list[str]


class ProductPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[Product]
