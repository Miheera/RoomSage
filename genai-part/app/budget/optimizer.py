"""Budget selection as a multiple-choice knapsack: at most one product per slot, total
at or under budget, value weighted so that priority always outranks fit."""

from collections.abc import Iterable

import pulp
from pydantic import BaseModel, Field

from app.core.errors import UnavailableError
from app.core.models import Product, VerificationStatus
from app.dataset.catalog import Catalog
from app.llm.schemas import Priority
from app.recommendations.engine import SlotRecommendation

# Each level outweighs every level below it combined, for any realistic slot count.
PRIORITY_WEIGHT = {Priority.ESSENTIAL: 10_000, Priority.RECOMMENDED: 300, Priority.OPTIONAL: 10}


class LineItem(BaseModel):
    slot: str
    priority: Priority
    product_id: str
    name: str
    unit_price_inr: float
    price_as_of: str | None
    verification_status: VerificationStatus
    product_url: str | None = None


class LowerCostOption(BaseModel):
    slot: str
    product_id: str
    name: str
    price_inr: float
    saving_inr: float


class SkippedSlot(BaseModel):
    slot: str
    priority: Priority
    reason: str
    cheapest_known_price_inr: float | None = None


class UnknownPriceItem(BaseModel):
    slot: str
    product_id: str
    name: str
    note: str = "Price unknown. Not included in any total."


class BudgetResult(BaseModel):
    budget_inr: float
    items: list[LineItem] = Field(default_factory=list)
    total_inr: float = 0
    remaining_inr: float = 0
    within_budget: bool = True
    # True when a slot in the plan had to be left out.
    is_partial: bool = False
    missing_essentials: list[str] = Field(default_factory=list)
    skipped_slots: list[SkippedSlot] = Field(default_factory=list)
    unknown_price_items: list[UnknownPriceItem] = Field(default_factory=list)
    lower_cost_options: list[LowerCostOption] = Field(default_factory=list)
    price_notes: list[str] = Field(default_factory=list)


def _line_item(slot: str, priority: Priority, product: Product) -> LineItem:
    return LineItem(
        slot=slot, priority=priority, product_id=product.product_id, name=product.name,
        unit_price_inr=product.price_inr, price_as_of=product.price_as_of,
        verification_status=product.verification_status,
        product_url=str(product.product_url) if product.product_url else None,
    )


def _price_notes(items: Iterable[LineItem]) -> list[str]:
    items = list(items)
    notes = []
    dated = sorted(i.price_as_of for i in items if i.price_as_of
                   and i.verification_status is VerificationStatus.SOURCE_DATASET)
    if dated:
        notes.append(f"{len(dated)} price(s) come from listings dated {dated[0]} to {dated[-1]} "
                     "and are not current. Confirm before buying.")
    if any(i.verification_status is VerificationStatus.DEMO for i in items):
        notes.append("Includes demo records with placeholder prices.")
    return notes


def _solve(slots: list[SlotRecommendation], budget_inr: float) -> dict[str, Product]:
    model = pulp.LpProblem("room_budget", pulp.LpMaximize)
    choices: dict[tuple[int, int], pulp.LpVariable] = {}
    objective, cost = [], []
    for s, slot in enumerate(slots):
        slot_vars = []
        for o, option in enumerate(slot.options):
            if option.product.price_inr is None:
                continue
            var = model.add_variable(f"x_{s}_{o}", cat="Binary")
            choices[(s, o)] = var
            slot_vars.append(var)
            # +1 so a zero-score product still beats leaving the slot empty. The last
            # term settles exact ties toward the cheaper option, so runs are repeatable.
            value = PRIORITY_WEIGHT[slot.priority] * (1 + option.score / 100)
            objective.append((value - option.product.price_inr / budget_inr * 0.001) * var)
            cost.append(option.product.price_inr * var)
        if slot_vars:
            model += pulp.lpSum(slot_vars) <= 1, f"one_per_slot_{s}"
    if not choices:
        return {}
    model += pulp.lpSum(objective)
    model += pulp.lpSum(cost) <= budget_inr, "budget"

    # CBC can report GapLimit at a zero gap, which is still the optimal answer.
    stats = model.solve(pulp.COIN_CMD(msg=False, gapRel=0, gapAbs=0))
    if not stats.has_solution:
        raise UnavailableError(f"Budget solver finished with status {stats.status.name}.")
    return {slots[s].slot: slots[s].options[o].product
            for (s, o), var in choices.items() if var.value() and var.value() > 0.5}


def optimize_budget(slots: list[SlotRecommendation], budget_inr: float) -> BudgetResult:
    chosen = _solve(slots, budget_inr)
    items: list[LineItem] = []
    skipped: list[SkippedSlot] = []
    unknown: list[UnknownPriceItem] = []
    lower: list[LowerCostOption] = []

    for slot in slots:
        prices = [o.product.price_inr for o in slot.options if o.product.price_inr is not None]
        product = chosen.get(slot.slot)
        if product is not None:
            items.append(_line_item(slot.slot, slot.priority, product))
            cheaper = [o.product for o in slot.options
                       if o.product.price_inr and o.product.price_inr < product.price_inr]
            if cheaper:
                best = min(cheaper, key=lambda p: p.price_inr)
                lower.append(LowerCostOption(slot=slot.slot, product_id=best.product_id, name=best.name,
                                             price_inr=best.price_inr,
                                             saving_inr=product.price_inr - best.price_inr))
            continue
        if not slot.options:
            reason = "No product in the catalog fits this slot."
        elif not prices:
            reason = "No option has a known price."
            unknown += [UnknownPriceItem(slot=slot.slot, product_id=o.product.product_id,
                                         name=o.product.name) for o in slot.options[:2]]
        else:
            reason = "Does not fit in the remaining budget."
        skipped.append(SkippedSlot(slot=slot.slot, priority=slot.priority, reason=reason,
                                   cheapest_known_price_inr=min(prices) if prices else None))

    total = round(sum(item.unit_price_inr for item in items), 2)
    return BudgetResult(
        budget_inr=budget_inr,
        items=items,
        total_inr=total,
        remaining_inr=round(budget_inr - total, 2),
        within_budget=total <= budget_inr,
        is_partial=bool(skipped),
        missing_essentials=[s.slot for s in skipped if s.priority is Priority.ESSENTIAL],
        skipped_slots=skipped,
        unknown_price_items=unknown,
        lower_cost_options=lower,
        price_notes=_price_notes(items),
    )


def calculate_selection(product_ids: list[str], budget_inr: float, catalog: Catalog) -> BudgetResult:
    """Total a fixed selection without optimising it."""
    items: list[LineItem] = []
    unknown: list[UnknownPriceItem] = []
    for product_id in product_ids:
        product = catalog.get(product_id)
        if product.has_known_price:
            items.append(_line_item(product.category.value, Priority.RECOMMENDED, product))
        else:
            unknown.append(UnknownPriceItem(slot=product.category.value, product_id=product_id,
                                            name=product.name))
    total = round(sum(i.unit_price_inr for i in items), 2)
    return BudgetResult(
        budget_inr=budget_inr, items=items, total_inr=total, remaining_inr=round(budget_inr - total, 2),
        within_budget=total <= budget_inr, is_partial=bool(unknown), unknown_price_items=unknown,
        price_notes=_price_notes(items),
    )
