from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from app.budget.optimizer import BudgetResult
from app.llm.schemas import DesignRequest, DesignSpec
from app.recommendations.engine import SlotRecommendation
from app.services.planner import TransformationPlan


class RevisionNote(BaseModel):
    at: datetime
    message: str
    stages_rerun: list[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Design(BaseModel):
    design_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    request: DesignRequest
    spec: DesignSpec
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[SlotRecommendation] = Field(default_factory=list)
    budget: BudgetResult | None = None
    plan: TransformationPlan | None = None
    revisions: list[RevisionNote] = Field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = _now()
