"""Turn a spec and a budget result into an ordered plan. It only restates what those
two already hold, so the plan cannot drift from the budget."""

from pydantic import BaseModel, Field

from app.budget.optimizer import BudgetResult, LineItem
from app.core.models import Category
from app.llm.schemas import ChangeAction, DesignSpec, Priority


class PlanStep(BaseModel):
    order: int
    title: str
    description: str
    items: list[LineItem] = Field(default_factory=list)
    estimated_cost_inr: float | None = None
    cost_note: str | None = None


class OptionalUpgrade(BaseModel):
    slot: str
    reason: str
    from_price_inr: float | None = None


class TransformationPlan(BaseModel):
    summary: str
    keep: list[str]
    remove: list[str]
    replace: list[str]
    add: list[str]
    steps: list[PlanStep]
    total_inr: float
    budget_inr: float
    budget_priorities: list[str]
    optional_upgrades: list[OptionalUpgrade]
    notes: list[str]


_STAGES: list[tuple[str, str, set[Category]]] = [
    ("Bring in the main furniture", "Place the largest pieces first; everything else is arranged around them.",
     {Category.SOFA_SEATING, Category.BED, Category.TABLE, Category.CHAIR}),
    ("Add storage", "Fit storage next, so the room's clutter has somewhere to go.", {Category.STORAGE}),
    ("Install lighting", "Add lighting once the furniture positions are fixed.", {Category.LIGHTING}),
    ("Layer textiles", "Add rugs and curtains to bring in colour and soften the room.",
     {Category.RUG, Category.CURTAIN}),
    ("Finish with decor", "Add wall decor, plants and accessories last.",
     {Category.WALL_DECOR, Category.PLANT, Category.ACCESSORY}),
]


def _labels(spec: DesignSpec, action: ChangeAction) -> list[str]:
    return [c.label for c in spec.furniture_changes if c.action is action]


def build_plan(spec: DesignSpec, budget: BudgetResult) -> TransformationPlan:
    remove, replace = _labels(spec, ChangeAction.REMOVE), _labels(spec, ChangeAction.REPLACE)
    item_category = {req.slot: req.category for req in spec.product_requirements}

    steps: list[PlanStep] = []

    def add_step(title: str, description: str, items=(), cost=None, note=None) -> None:
        steps.append(PlanStep(order=len(steps) + 1, title=title, description=description,
                              items=list(items), estimated_cost_inr=cost, cost_note=note))

    if remove or replace:
        add_step("Clear the room", f"Take out: {', '.join([*remove, *replace])}.", cost=0)
    add_step(
        "Refresh walls and surfaces",
        f"If repainting, work in {', '.join(spec.color_palette[:3])}. Do this before furniture arrives.",
        note="Paint and labour are not in the catalog and are not included in the total.",
    )
    for title, description, categories in _STAGES:
        items = [i for i in budget.items if item_category.get(i.slot) in categories]
        if items:
            add_step(title, description, items, round(sum(i.unit_price_inr for i in items), 2))

    priorities = [f"{level.value.title()}: {', '.join(slots)}" for level in Priority
                  if (slots := [i.slot for i in budget.items if i.priority is level])]
    if budget.missing_essentials:
        priorities.insert(0, f"Still needed: {', '.join(budget.missing_essentials)}")

    return TransformationPlan(
        summary=(f"{spec.theme.value.title()} {spec.room_type.value.replace('_', ' ')}: "
                 f"{len(budget.items)} items for ₹{budget.total_inr:,.0f} "
                 f"of a ₹{budget.budget_inr:,.0f} budget."),
        keep=_labels(spec, ChangeAction.KEEP),
        remove=remove,
        replace=replace,
        add=[item.name for item in budget.items],
        steps=steps,
        total_inr=budget.total_inr,
        budget_inr=budget.budget_inr,
        budget_priorities=priorities,
        optional_upgrades=[OptionalUpgrade(slot=s.slot, reason=s.reason,
                                           from_price_inr=s.cheapest_known_price_inr)
                           for s in budget.skipped_slots],
        notes=[*budget.price_notes, *spec.assumptions],
    )
