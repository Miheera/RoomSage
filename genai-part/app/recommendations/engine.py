"""Deterministic product selection: hard filters, then a transparent score out of 100.
Every point awarded produces a reason, so a choice can always be explained."""

from pydantic import BaseModel, Field

from app.core.models import Category, Product, Theme, ThemeSource
from app.dataset.catalog import Catalog
from app.llm.schemas import DesignSpec, Priority, ProductRequirement

# Share of the budget each category may reasonably take, used only for scoring.
CATEGORY_BUDGET_SHARE = {
    Category.SOFA_SEATING: 0.40, Category.BED: 0.40, Category.TABLE: 0.20, Category.CHAIR: 0.20,
    Category.STORAGE: 0.20, Category.LIGHTING: 0.08, Category.RUG: 0.10, Category.CURTAIN: 0.08,
    Category.WALL_DECOR: 0.05, Category.PLANT: 0.03, Category.ACCESSORY: 0.03,
}


BUDGET_ALTERNATIVES = 2
SINGLE_SEAT_WORDS = ("1 seater", "one seater", "bean bag", "armchair")


class RankedProduct(BaseModel):
    product: Product
    score: float
    reasons: list[str]
    warnings: list[str] = Field(default_factory=list)


class SlotRecommendation(BaseModel):
    slot: str
    category: Category
    priority: Priority
    options: list[RankedProduct] = Field(default_factory=list, description="Best first.")
    relaxed_filters: list[str] = Field(default_factory=list)
    message: str | None = None


def _fits(product: Product, requirement: ProductRequirement) -> bool:
    if product.product_id in requirement.excluded_product_ids:
        return False
    if requirement.small_room_only and product.small_room_suitable is False:
        return False
    if requirement.max_width_cm and product.dimensions and product.dimensions.width_cm:
        return product.dimensions.width_cm <= requirement.max_width_cm
    return True


def score_product(
    product: Product,
    requirement: ProductRequirement,
    theme: Theme,
    allowance_inr: float,
    theme_matched: bool,
) -> RankedProduct:
    score = 0.0
    reasons: list[str] = []
    warnings: list[str] = []

    if theme_matched:
        stated = product.theme_source is not ThemeSource.DERIVED_RULES
        score += 35 if stated else 28
        reasons.append(f"Listed as {theme.value}." if stated else f"Fits {theme.value} by colour or material.")
    else:
        warnings.append(f"Not tagged {theme.value}; shown because nothing tagged fits.")

    colors = [c.lower() for c in requirement.preferred_colors]
    if product.color and product.color in colors:
        score += 25 - min(colors.index(product.color), 5)
        reasons.append(f"Colour {product.color} is in the palette.")
    elif product.color is None:
        warnings.append("Colour unknown.")

    materials = [m.lower() for m in requirement.preferred_materials]
    if product.material and product.material in materials:
        score += 15
        reasons.append(f"Material {product.material} suits the theme.")

    if product.price_inr is None:
        warnings.append("Price unknown; excluded from budget totals.")
    else:
        if product.price_inr <= allowance_inr:
            score += 15
            reasons.append(f"Within the ₹{allowance_inr:,.0f} guide for this category.")
        else:
            score += max(0.0, 15 - 10 * (product.price_inr / allowance_inr - 1))
            warnings.append(f"Above the ₹{allowance_inr:,.0f} guide for this category.")

    width = product.dimensions.width_cm if product.dimensions else None
    if requirement.max_width_cm:
        if width:
            score += 10
            reasons.append(f"{width:.0f} cm wide fits the {requirement.max_width_cm:.0f} cm limit.")
        else:
            warnings.append("Width unknown; measure before buying.")
    elif product.dimensions:
        score += 5
    if product.small_room_suitable and requirement.small_room_only:
        reasons.append("Compact enough for a small room.")

    if requirement.category is Category.SOFA_SEATING:
        name = product.name.lower()
        if any(word in name for word in SINGLE_SEAT_WORDS):
            score -= 25
            warnings.append("Seats one, so it may not work as the main sofa.")

    return RankedProduct(product=product, score=round(score, 1), reasons=reasons, warnings=warnings)


def recommend_slot(
    requirement: ProductRequirement, spec: DesignSpec, catalog: Catalog, top_k: int = 4
) -> SlotRecommendation:
    candidates = [p for p in catalog.search(category=requirement.category, room_type=spec.room_type)
                  if _fits(p, requirement)]
    themed = [p for p in candidates if spec.theme in p.compatible_themes]
    relaxed: list[str] = []
    theme_matched = bool(themed)
    if not themed:
        relaxed.append("theme")
    pool = themed or candidates

    # Budget totals need prices, so priced products lead whenever any qualify.
    priced = [p for p in pool if p.has_known_price]
    allowance = spec.budget_limit_inr * CATEGORY_BUDGET_SHARE[requirement.category]

    def rank(products: list[Product]) -> list[RankedProduct]:
        ranked = [score_product(p, requirement, spec.theme, allowance, theme_matched) for p in products]
        return sorted(ranked, key=lambda r: (-r.score, r.product.price_inr or 0, r.product.product_id))

    options = rank(priced)[:top_k]
    if len(options) < top_k:
        unpriced = [p for p in pool if not p.has_known_price]
        options += rank(unpriced)[: top_k - len(options)]

    # The best matches can all be expensive, and the budget step needs somewhere to go.
    chosen_ids = {o.product.product_id for o in options}
    for ranked in sorted(rank(priced), key=lambda r: (r.product.price_inr, r.product.product_id)):
        if len(options) >= top_k + BUDGET_ALTERNATIVES:
            break
        if ranked.product.product_id not in chosen_ids:
            ranked.reasons.append("Lower-cost alternative.")
            options.append(ranked)

    message = None
    if not options:
        message = f"No catalog product fits the {requirement.slot} requirement."
    elif not priced:
        message = f"No {requirement.slot} option has a known INR price."
    return SlotRecommendation(
        slot=requirement.slot,
        category=requirement.category,
        priority=requirement.priority,
        options=options,
        relaxed_filters=relaxed,
        message=message,
    )


def recommend(
    spec: DesignSpec, catalog: Catalog, slots: set[str] | None = None,
    existing: list[SlotRecommendation] | None = None,
) -> list[SlotRecommendation]:
    """Compute recommendations. When `slots` is given, only those are recomputed and
    the rest are reused from `existing`, so refinement avoids repeating work."""
    reusable = {r.slot: r for r in existing or []}
    results = []
    for requirement in spec.product_requirements:
        if slots is not None and requirement.slot not in slots and requirement.slot in reusable:
            results.append(reusable[requirement.slot])
        else:
            results.append(recommend_slot(requirement, spec, catalog))
    return results
