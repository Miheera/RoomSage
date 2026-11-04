"""Apply a refinement patch to a design and rerun only what it invalidates.

Preview rendering is dispatched separately to Person A's Celery-backed service.
"""

from pydantic import BaseModel, Field

from app.budget.optimizer import optimize_budget
from app.core.models import Category
from app.dataset.catalog import Catalog
from app.llm import rules
from app.llm.schemas import ChangeAction, DesignSpec, FurnitureChange, RefinementPatch
from app.recommendations.engine import recommend
from app.services.design_models import Design
from app.services.planner import build_plan

ALL_STAGES = ["spec", "recommendations", "budget", "plan", "preview"]


class RefinementOutcome(BaseModel):
    understood: bool
    patch: RefinementPatch
    stages_rerun: list[str] = Field(default_factory=list)
    stages_skipped: list[str] = Field(default_factory=list)
    slots_recomputed: list[str] = Field(default_factory=list)
    preview_stale: bool = False
    message: str


def _apply_theme(spec: DesignSpec, patch: RefinementPatch, catalog: Catalog) -> None:
    theme_data = catalog.theme(patch.theme)
    spec.theme = patch.theme
    spec.color_palette = list(theme_data["palette"])
    spec.accent_colors = list(theme_data["accent_colors"])
    spec.materials = list(theme_data["materials"])
    spec.redesign_prompt = rules.compose_redesign_prompt(theme_data["prompt"], spec.room_type, spec.color_palette)
    spec.edit_prompt_3d = rules.compose_edit_prompt(theme_data["label"], spec.color_palette, spec.materials)
    for requirement in spec.product_requirements:
        requirement.preferred_colors = [*spec.color_palette, *spec.accent_colors]
        requirement.preferred_materials = list(spec.materials)


def _drop_slots(spec: DesignSpec, categories: list[Category], action: ChangeAction) -> set[str]:
    dropped = {r.slot for r in spec.product_requirements if r.category in categories}
    spec.product_requirements = [r for r in spec.product_requirements if r.slot not in dropped]
    spec.furniture_changes = [c for c in spec.furniture_changes if c.category not in categories]
    spec.furniture_changes += [
        FurnitureChange(action=action, category=c, label=c.value.replace("_", " "),
                        reason="Requested during refinement.")
        for c in categories
    ]
    return dropped


def apply_patch(design: Design, patch: RefinementPatch, catalog: Catalog) -> RefinementOutcome:
    """Mutates `design` in place. Callers persist it afterwards."""
    spec = design.spec
    recompute: set[str] | None = set()  # None means every slot
    spec_changed = False
    selection_before = {i.product_id for i in design.budget.items} if design.budget else set()
    prompt_before = spec.redesign_prompt

    if patch.theme and patch.theme is not spec.theme:
        _apply_theme(spec, patch, catalog)
        recompute, spec_changed = None, True

    if patch.small_room is not None and patch.small_room != spec.small_room:
        spec.small_room = patch.small_room
        spec_changed = True
        scene = design.request.scene
        for requirement in spec.product_requirements:
            if requirement.category in (Category.SOFA_SEATING, Category.BED, Category.TABLE, Category.STORAGE):
                requirement.small_room_only = patch.small_room
                if scene is not None:
                    requirement.max_width_cm = rules.max_width_for(requirement.category, scene, patch.small_room)
                if recompute is not None:
                    recompute.add(requirement.slot)

    removed = list(patch.remove_categories)
    if patch.reduce_decor:
        removed.append(Category.ACCESSORY)
    if removed and _drop_slots(spec, removed, ChangeAction.REMOVE):
        spec_changed = True
    if patch.keep_categories and _drop_slots(spec, patch.keep_categories, ChangeAction.KEEP):
        spec_changed = True

    for request in patch.color_requests:
        for requirement in spec.product_requirements:
            if requirement.category is request.category:
                requirement.preferred_colors = list(request.colors)
                spec_changed = True
                if recompute is not None:
                    recompute.add(requirement.slot)

    budget_changed = patch.budget_limit_inr is not None and patch.budget_limit_inr != spec.budget_limit_inr
    if budget_changed:
        spec.budget_limit_inr = patch.budget_limit_inr

    stages: list[str] = []
    if spec_changed:
        stages.append("spec")
    recs_needed = recompute is None or bool(recompute)
    if recs_needed:
        design.recommendations = recommend(spec, catalog, slots=recompute, existing=design.recommendations)
        stages.append("recommendations")
    elif spec_changed:
        current = {r.slot for r in spec.product_requirements}
        design.recommendations = [r for r in design.recommendations if r.slot in current]

    if spec_changed or budget_changed:
        design.budget = optimize_budget(design.recommendations, spec.budget_limit_inr)
        design.plan = build_plan(spec, design.budget)
        stages += ["budget", "plan"]

    selection_after = {i.product_id for i in design.budget.items} if design.budget else set()
    preview_stale = selection_after != selection_before or spec.redesign_prompt != prompt_before
    if not patch.understood:
        message = "No supported change found in that request; the design is unchanged."
    elif not stages:
        message = "The design already matches that request."
    else:
        message = f"Updated: {', '.join(stages)}."
    return RefinementOutcome(
        understood=patch.understood,
        patch=patch,
        stages_rerun=stages,
        stages_skipped=[s for s in ALL_STAGES if s not in stages],
        slots_recomputed=sorted(recompute) if recompute is not None else
        [r.slot for r in spec.product_requirements],
        preview_stale=preview_stale,
        message=message,
    )
