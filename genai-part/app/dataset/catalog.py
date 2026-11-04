"""In-memory read access to the product catalog and theme definitions."""

import json
from pathlib import Path

from app.config import Settings
from app.core.errors import NotFoundError
from app.core.models import Category, Product, RoomType, Theme

THEME_TEMPLATE_FIELDS = ("palette", "accent_colors", "materials", "prompt")


class Catalog:
    def __init__(self, products: list[Product], themes: dict[str, dict]):
        self.products = products
        self.themes = themes
        self._by_id = {p.product_id: p for p in products}

    @classmethod
    def load(cls, settings: Settings) -> "Catalog":
        furniture_dir = Path(settings.data_dir) / "furniture"
        rows = json.loads((furniture_dir / "products.json").read_text(encoding="utf-8"))
        demo_path = furniture_dir / "demo_products.json"
        if settings.demo_catalog and demo_path.exists():
            rows += json.loads(demo_path.read_text(encoding="utf-8"))
        themes = json.loads((furniture_dir / "themes.json").read_text(encoding="utf-8"))
        return cls([Product.model_validate(row) for row in rows], themes)

    def get(self, product_id: str) -> Product:
        try:
            return self._by_id[product_id]
        except KeyError:
            raise NotFoundError(f"Product {product_id!r} is not in the catalog.") from None

    def theme(self, theme: Theme) -> dict:
        return self.themes[theme.value]

    def search(
        self,
        *,
        category: Category | None = None,
        theme: Theme | None = None,
        room_type: RoomType | None = None,
        priced_only: bool = False,
        max_price_inr: float | None = None,
        small_room: bool | None = None,
        query: str | None = None,
    ) -> list[Product]:
        needle = query.lower() if query else None
        results = []
        for product in self.products:
            if category is not None and product.category is not category:
                continue
            if theme is not None and theme not in product.compatible_themes:
                continue
            if room_type is not None and room_type not in product.room_types:
                continue
            if priced_only and not product.has_known_price:
                continue
            if max_price_inr is not None and (
                product.price_inr is None or product.price_inr > max_price_inr
            ):
                continue
            if small_room is not None and product.small_room_suitable is not small_room:
                continue
            if needle and needle not in product.name.lower():
                continue
            results.append(product)
        return results
