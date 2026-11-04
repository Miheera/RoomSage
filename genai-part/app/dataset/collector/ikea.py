"""Adapter for the IKEA Saudi Arabia dataset as published by TidyTuesday (2020-11-03).

Scraped on 2020-04-20 by Ahmed Kallam. Prices are in Saudi riyals and are kept
as `source_price` only: a Saudi shelf price is not an Indian price, so `price_inr`
stays unknown. The records earn their place through real dimensions, for the
categories Flipkart barely covers. Only factual fields are kept.
"""

import csv
import re
from collections.abc import Iterator
from pathlib import Path

from app.core.models import Category, Product, SourcePrice, VerificationStatus
from app.dataset.collector import common

SOURCE_ID = "ikea_sa_tidytuesday_2020"
SOURCE_LICENSE = "CC0-1.0 (TidyTuesday); factual fields only"
DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/rfordatascience/tidytuesday/main/"
    "data/2020/2020-11-03/ikea.csv"
)
CSV_NAME = "ikea.csv"

_CATEGORIES = {
    "Sofas & armchairs": Category.SOFA_SEATING,
    "Beds": Category.BED,
    "Tables & desks": Category.TABLE,
    "Chairs": Category.CHAIR,
    "Bookcases & shelving units": Category.STORAGE,
    "Cabinets & cupboards": Category.STORAGE,
    "Wardrobes": Category.STORAGE,
    "TV & media furniture": Category.STORAGE,
    "Chests of drawers & drawer units": Category.STORAGE,
    "Sideboards, buffets & console tables": Category.STORAGE,
}


def _number(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw else None
    except ValueError:
        return None
    return value if value and value > 0 else None


def _slug(link: str) -> str:
    match = re.search(r"/p/([^/]+?)-\d+/?$", link or "")
    return match.group(1).replace("-", " ") if match else ""


def row_to_product(row: dict[str, str], themes: dict[str, dict]) -> Product | None:
    category = _CATEGORIES.get(row.get("category", ""))
    item_id = row.get("item_id", "").strip()
    if category is None or not item_id:
        return None

    short = " ".join((row.get("short_description") or "").split())
    name = f"{row['name'].strip()} {short}".strip()
    # The URL slug spells out the finish, e.g. "...-bar-table-in-outdoor-black".
    slug = _slug(row.get("link", ""))
    color = common.normalize_color(slug)
    material = common.normalize_material(f"{slug} {short}")
    dims = common.build_dimensions(
        width=_number(row.get("width")),
        depth=_number(row.get("depth")),
        height=_number(row.get("height")),
    )
    compatible, theme_source = common.assign_themes(f"{name} {slug}", color, material, themes, category)
    amount = _number(row.get("price"))

    return Product(
        product_id=f"ikea-sa-{item_id}",
        name=name,
        category=category,
        compatible_themes=compatible,
        theme_source=theme_source,
        description=short or None,
        product_url=row.get("link") or None,
        price_inr=None,
        source_price=SourcePrice(amount=amount, currency="SAR") if amount else None,
        dimensions=dims,
        color=color,
        material=material,
        brand="IKEA",
        tags=[],
        room_types=common.room_types_for(category, name),
        small_room_suitable=common.small_room_suitable(category, dims),
        source=SOURCE_ID,
        source_license=SOURCE_LICENSE,
        verification_status=VerificationStatus.SOURCE_DATASET,
    )


def collect(raw_dir: Path, themes: dict[str, dict]) -> Iterator[Product]:
    path = common.fetch_once(DOWNLOAD_URL, raw_dir / CSV_NAME)
    with path.open(encoding="utf-8", errors="replace", newline="") as stream:
        for row in csv.DictReader(stream):
            product = row_to_product(row, themes)
            if product is not None:
                yield product
