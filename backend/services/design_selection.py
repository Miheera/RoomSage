"""Deterministic, catalog-only selection of the items rendered into a design."""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

from catalog_roles import ROOM_ROLE_REQUIREMENTS
from models import CatalogProduct

MAX_OBJECT_REFERENCES = 10
OPTIONAL_ROLE_CAPS = {
    "decor": 2,
    "side_table": 1,
    "storage": 2,
    "paint": 1,
    "flooring": 1,
    "rug": 1,
    "lighting": 2,
    "chair": 6,
    "coffee_table": 1,
    "dining_table": 1,
    "sofa": 1,
    "bed": 1,
}


class InsufficientCatalogBudget(ValueError):
    def __init__(self, minimum_spend: Decimal):
        self.minimum_spend = minimum_spend
        super().__init__(f"The selected style needs at least ₹{minimum_spend:,.0f} to cover every essential role.")


@dataclass(frozen=True)
class SelectedItem:
    product: CatalogProduct
    qty: int
    essential: bool = False

    @property
    def role(self) -> str:
        return self.product.role


def _is_renderable(product: CatalogProduct) -> bool:
    if not product.selectable:
        return False
    if product.kind == "surface":
        # Paint is rendered from its catalog color. Flooring needs an actual
        # texture; image_s3_key alone is only a database key and may point to
        # an asset that was never mirrored into catalog storage.
        if product.role == "flooring":
            return bool(product.reference_image_s3_key)
        return True
    return bool(product.reference_image_s3_key)


def minimum_feasible_spend(
    products: list[CatalogProduct], room_type: str, style: str,
) -> Decimal:
    requirements = ROOM_ROLE_REQUIREMENTS.get(room_type)
    if requirements is None:
        raise ValueError(f"Unsupported room type {room_type!r}.")
    matching = sorted((p for p in products if p.theme.casefold() == style.casefold()
                       and room_type in (p.room_types or []) and _is_renderable(p)),
                      key=lambda product: product.product_id)
    missing = [req.role for req in requirements if not any(p.role == req.role for p in matching)]
    if missing:
        raise ValueError("This style is missing required catalog roles: " + ", ".join(missing) + ".")
    return sum((min(p.price_inr for p in matching if p.role == req.role)
                * req.minimum_quantity for req in requirements), Decimal("0"))


def select_design_items(
    products: list[CatalogProduct], room_type: str, style: str,
    budget: Decimal, seed: int,
) -> list[SelectedItem]:
    requirements = ROOM_ROLE_REQUIREMENTS.get(room_type)
    if requirements is None:
        raise ValueError(f"Unsupported room type {room_type!r}.")
    matching = sorted((p for p in products if p.theme.casefold() == style.casefold()
                       and room_type in (p.room_types or []) and _is_renderable(p)),
                      key=lambda product: product.product_id)
    missing = [req.role for req in requirements if not any(p.role == req.role for p in matching)]
    if missing:
        raise ValueError("This style is missing required catalog roles: " + ", ".join(missing) + ".")
    minimum = sum((min(p.price_inr for p in matching if p.role == req.role)
                   * req.minimum_quantity for req in requirements), Decimal("0"))
    if budget < minimum:
        raise InsufficientCatalogBudget(minimum)

    rng = random.Random(seed)
    remaining = budget
    selected: list[SelectedItem] = []
    selected_ids: set[str] = set()
    role_quantities: dict[str, int] = {}
    for index, requirement in enumerate(requirements):
        future_minimum = sum((min(p.price_inr for p in matching if p.role == later.role)
                              * later.minimum_quantity for later in requirements[index + 1:]), Decimal("0"))
        candidates = sorted((p for p in matching if p.role == requirement.role),
                            key=lambda p: (p.price_inr, p.product_id))
        affordable = [p for p in candidates
                      if p.price_inr * requirement.minimum_quantity <= remaining - future_minimum]
        product = rng.choice(affordable)
        qty = requirement.minimum_quantity
        selected.append(SelectedItem(product, qty, True))
        selected_ids.add(product.product_id)
        role_quantities[product.role] = role_quantities.get(product.role, 0) + qty
        remaining -= product.price_inr * qty

    # Surface rows are inserted early so the renderer always receives explicit
    # wall/floor instructions when the chosen style and remaining budget allow.
    surfaces = [p for p in matching if p.kind == "surface" and p.product_id not in selected_ids]
    rng.shuffle(surfaces)
    surfaces.sort(key=lambda p: (p.role not in {"paint", "flooring"}, p.role))
    object_count = sum(item.product.kind == "object" for item in selected)
    for product in surfaces:
        if role_quantities.get(product.role, 0) >= OPTIONAL_ROLE_CAPS.get(product.role, 1):
            continue
        if product.price_inr <= remaining:
            selected.append(SelectedItem(product, 1, False))
            selected_ids.add(product.product_id)
            role_quantities[product.role] = role_quantities.get(product.role, 0) + 1
            remaining -= product.price_inr

    optional = [p for p in matching if p.product_id not in selected_ids]
    rng.shuffle(optional)
    for product in optional:
        cap = OPTIONAL_ROLE_CAPS.get(product.role, 1)
        if role_quantities.get(product.role, 0) >= cap:
            continue
        if product.kind == "object" and object_count >= MAX_OBJECT_REFERENCES:
            continue
        if product.price_inr > remaining:
            continue
        selected.append(SelectedItem(product, 1, False))
        selected_ids.add(product.product_id)
        role_quantities[product.role] = role_quantities.get(product.role, 0) + 1
        remaining -= product.price_inr
        if product.kind == "object":
            object_count += 1
    return selected
