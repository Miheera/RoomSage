"""Hand-checked records: the route for adding current, verified Indian prices.

Add a row to data/furniture/curated_products.csv after confirming it on the live
listing. Only values a person actually read from that listing belong in the file.
"""

import csv
from collections.abc import Iterator
from pathlib import Path

from app.core.models import Category, Product, RoomType, ThemeSource, VerificationStatus
from app.dataset.collector import common

SOURCE_ID = "curated_manual"
FILE_NAME = "curated_products.csv"
COLUMNS = [
    "product_id", "name", "category", "themes", "description", "image_url", "product_url",
    "price_inr", "price_as_of", "width_cm", "depth_cm", "height_cm", "color", "material",
    "brand", "tags", "room_types", "source_license",
]


def _float(raw: str | None) -> float | None:
    return float(raw) if raw and raw.strip() else None


def _split(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split("|") if part.strip()]


def row_to_product(row: dict[str, str]) -> Product:
    category = Category(row["category"].strip())
    dims = common.build_dimensions(
        _float(row.get("width_cm")), _float(row.get("depth_cm")), _float(row.get("height_cm"))
    )
    return Product(
        product_id=row["product_id"].strip(),
        name=row["name"].strip(),
        category=category,
        compatible_themes=_split(row.get("themes")),
        theme_source=ThemeSource.MANUAL,
        description=row.get("description") or None,
        image_url=row.get("image_url") or None,
        product_url=row.get("product_url") or None,
        price_inr=_float(row.get("price_inr")),
        price_as_of=row.get("price_as_of") or None,
        dimensions=dims,
        color=common.normalize_color(row.get("color")),
        material=common.normalize_material(row.get("material")),
        brand=row.get("brand") or None,
        tags=_split(row.get("tags")),
        room_types=[RoomType(r) for r in _split(row.get("room_types"))]
        or common.room_types_for(category, row["name"]),
        small_room_suitable=common.small_room_suitable(category, dims),
        source=SOURCE_ID,
        source_license=row.get("source_license") or "facts-only",
        verification_status=VerificationStatus.VERIFIED_LIVE,
    )


def collect(furniture_dir: Path) -> Iterator[Product]:
    path = furniture_dir / FILE_NAME
    if not path.exists():
        return
    with path.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            yield row_to_product(row)
