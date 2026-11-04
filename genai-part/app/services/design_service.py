"""Orchestrates one design through its stages and keeps the store up to date."""

from pydantic import BaseModel

from app.budget.optimizer import BudgetResult, optimize_budget
from app.core.errors import ConflictError
from app.core.models import Category
from app.core.store import DesignStore
from app.dataset.catalog import Catalog
from app.integration.mapper import Edit3DOptions, EditPlan, build_edit_plan
from app.integration.person_a_client import PersonAClient
from app.integration.person_a_schemas import EditJobResponse
from app.llm import designer
from app.llm.provider import LLMClient
from app.llm.rules import compose_redesign_prompt
from app.llm.schemas import ChangeAction, DesignRequest
from app.recommendations.engine import SlotRecommendation, recommend
from app.services.design_models import Design, RevisionNote, _now
from app.services.planner import TransformationPlan, build_plan
from app.services.refinement import RefinementOutcome, apply_patch


class Edit3DResult(BaseModel):
    job: EditJobResponse
    plan: EditPlan


class DesignService:
    def __init__(self, catalog: Catalog, store: DesignStore, llm: LLMClient | None, person_a: PersonAClient):
        self.catalog = catalog
        self.store = store
        self.llm = llm
        self.person_a = person_a

    def create(self, request: DesignRequest) -> Design:
        theme_data = self.catalog.theme(request.theme)
        spec, warning = designer.create_spec(request, theme_data, self.llm)
        design = Design(request=request, spec=spec, warnings=[warning] if warning else [])
        self.store.save(design)
        return design

    def _ensure_recommendations(self, design: Design) -> list[SlotRecommendation]:
        if not design.recommendations:
            design.recommendations = recommend(design.spec, self.catalog)
        return design.recommendations

    def _ensure_budget(self, design: Design) -> BudgetResult:
        if design.budget is None:
            design.budget = optimize_budget(self._ensure_recommendations(design), design.spec.budget_limit_inr)
        return design.budget

    def _ensure_plan(self, design: Design) -> TransformationPlan:
        if design.plan is None:
            design.plan = build_plan(design.spec, self._ensure_budget(design))
        return design.plan

    def recommendations(self, design_id: str) -> Design:
        design = self.store.get(design_id)
        self._ensure_recommendations(design)
        self.store.save(design)
        return design

    def budget(self, design_id: str, budget_inr: float | None = None) -> Design:
        design = self.store.get(design_id)
        if budget_inr is not None and budget_inr != design.spec.budget_limit_inr:
            design.spec.budget_limit_inr = budget_inr
            design.budget = None
            design.plan = None
        self._ensure_budget(design)
        self.store.save(design)
        return design

    def plan(self, design_id: str) -> Design:
        design = self.store.get(design_id)
        self._ensure_plan(design)
        self.store.save(design)
        return design

    def run_all(self, request: DesignRequest) -> Design:
        design = self.create(request)
        self._ensure_plan(design)
        self.store.save(design)
        return design

    def refine(self, design_id: str, message: str) -> tuple[Design, RefinementOutcome]:
        design = self.store.get(design_id)
        self._ensure_plan(design)
        patch = designer.interpret_refinement(message, self.llm)
        outcome = apply_patch(design, patch, self.catalog)
        design.revisions.append(RevisionNote(at=_now(), message=message, stages_rerun=outcome.stages_rerun))
        self.store.save(design)
        return design, outcome

    def preview_prompt(self, design: Design) -> str:
        """The spec prompt, plus the actual selection so the image leans toward it."""
        spec = design.spec
        if spec.generated_by == "llm":
            return spec.redesign_prompt
        items = []
        if design.budget:
            for item in design.budget.items:
                product = self.catalog.get(item.product_id)
                if product.category in (Category.SOFA_SEATING, Category.BED, Category.RUG):
                    descriptor = " ".join(filter(None, [product.color, product.material,
                                                        product.category.value.split("_")[0]]))
                    items.append(descriptor)
        return compose_redesign_prompt(self.catalog.theme(spec.theme)["prompt"], spec.room_type,
                                       spec.color_palette, items)

    def kept_categories(self, design: Design) -> list[Category]:
        return [c.category for c in design.spec.furniture_changes
                if c.action is ChangeAction.KEEP and c.category is not None]

    def preview_edit_plan(self, design_id: str, options: Edit3DOptions) -> EditPlan:
        design = self.store.get(design_id)
        return build_edit_plan(design, options, self.catalog)

    def submit_3d_edit(self, design_id: str, options: Edit3DOptions) -> Edit3DResult:
        design = self.store.get(design_id)
        if design.budget is None and options.insertion is not None:
            raise ConflictError("Run the budget step before inserting a product.")
        plan = build_edit_plan(design, options, self.catalog)
        return Edit3DResult(job=self.person_a.start_edit(plan.request), plan=plan)
