"""Adapter for PromptCloud's "Flipkart Products" dataset (CC BY-SA 4.0).

Crawled December 2015 to June 2016. Listings are real, but prices are a decade old
and are reported as historic. See data/furniture/LICENSE.md.
"""

import csv
import io
import json
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from app.core.models import Category, Product, VerificationStatus
from app.dataset.collector import common

SOURCE_ID = "flipkart_promptcloud_2016"
SOURCE_LICENSE = "CC-BY-SA-4.0"
DOWNLOAD_URL = "https://www.kaggle.com/api/v1/datasets/download/PromptCloudHQ/flipkart-products"
ARCHIVE_NAME = "flipkart-products.zip"
CSV_NAME = "flipkart_com-ecommerce_sample.csv"

_SPEC_PAIR = re.compile(r'"key"=>"([^"]+)",\s*"value"=>"([^"]*)"')

# First matching tree fragment wins, so specific entries come before general ones.
_CATEGORY_RULES: list[tuple[str, Category]] = [
    ("Furniture >> Living Room >> Sofas", Category.SOFA_SEATING),
    ("Furniture >> Living Room Furniture >> Sofas", Category.SOFA_SEATING),
    ("Sofa Beds & Futons", Category.SOFA_SEATING),
    ("Bean Bag", Category.SOFA_SEATING),
    ("Furniture >> Bedroom Furniture >> Beds", Category.BED),
    ("Dressing Tables", Category.STORAGE),
    ("Display Units", Category.STORAGE),
    ("TV & Entertainment Units", Category.STORAGE),
    ("Shoe Racks", Category.STORAGE),
    ("Wall Shelves", Category.STORAGE),
    ("Computer Tables", Category.TABLE),
    ("Portable Laptop Tables", Category.TABLE),
    ("Dining Tables, Chairs & Sets", Category.TABLE),
    ("Outdoor & Cafeteria Chairs", Category.CHAIR),
    ("Curtains & Accessories >> Curtains", Category.CURTAIN),
    ("Floor Coverings >> Carpets & Rugs", Category.RUG),
    ("Wall Decor & Clocks", Category.WALL_DECOR),
    ("Decorative Lighting & Lamps", Category.LIGHTING),
    ("Flowers, Plants & Vases >> Artificial Plants", Category.PLANT),
    ("Flowers, Plants & Vases >> Flower Vase", Category.ACCESSORY),
    ("Table Decor & Handicrafts", Category.ACCESSORY),
    ("Showpieces", Category.ACCESSORY),
    ("Showpiece", Category.ACCESSORY),
    ("Cushions, Pillows & Covers >> Cushion Covers", Category.ACCESSORY),
    ("Candles & Fragrances", Category.ACCESSORY),
]

# Festive and devotional items are personal choices, not theme-driven decor.
_EXCLUDED_NAME_WORDS = (
    "seed", "mattress", "cover set", "sticker", "decal", "kids", "pet ",
    "christmas", "diwali", "rakhi", "holi ", "ganesh", "goddess", "durga", "lord ",
    "buddha", "krishna", "shiva", "rice light", "series light",
    # Listed under decor or seating, but not what a room plan means by those.
    "magnet", "key holder", "key hanger", "sky lantern", "without filling", "cartoon",
)


def map_category(tree: str, name: str) -> Category | None:
    if any(word in name.lower() for word in _EXCLUDED_NAME_WORDS):
        return None
    for fragment, category in _CATEGORY_RULES:
        if fragment in tree:
            return category
    return None


def parse_specifications(raw: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    for key, value in _SPEC_PAIR.findall(raw or ""):
        specs.setdefault(key, value)
    return specs


def _first_image(raw: str) -> str | None:
    try:
        images = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return None
    return images[0] if images else None


def _price(row: dict[str, str]) -> float | None:
    for column in ("discounted_price", "retail_price"):
        try:
            value = float(row.get(column) or 0)
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def row_to_product(row: dict[str, str], themes: dict[str, dict]) -> Product | None:
    name = (row.get("product_name") or "").strip()
    tree = (row.get("product_category_tree") or "").strip('[]" ')
    category = map_category(tree, name) if name else None
    if category is None:
        return None

    specs = parse_specifications(row.get("product_specifications", ""))
    description = (row.get("description") or "").strip() or None
    color = common.normalize_color(
        specs.get("Color") or specs.get("Primary Color") or specs.get("Finish Color")
        or specs.get("Upholstery Color") or name
    )
    material = common.normalize_material(
        specs.get("Material") or specs.get("Primary Material") or specs.get("Upholstery Type") or name
    )
    # Flipkart rugs and curtains call the long side Length.
    width = common.parse_length_cm(specs.get("Width"))
    length = common.parse_length_cm(specs.get("Length"))
    dims = common.build_dimensions(
        width=width if width is not None else length,
        depth=common.parse_length_cm(specs.get("Depth")),
        height=common.parse_length_cm(specs.get("Height")),
    )
    profile = " ".join(filter(None, [name, specs.get("Type"), specs.get("Pattern"), specs.get("Style")]))
    compatible, theme_source = common.assign_themes(
        profile, color, material, themes, category, stated_style=specs.get("Style")
    )
    crawl_date = (row.get("crawl_timestamp") or "")[:10]
    price = _price(row)

    return Product(
        product_id=f"fk-{row['uniq_id'][:12]}",
        name=name,
        category=category,
        compatible_themes=compatible,
        theme_source=theme_source,
        description=description[:500] if description else None,
        image_url=_first_image(row.get("image", "")),
        product_url=row.get("product_url") or None,
        price_inr=price,
        price_as_of=crawl_date if price is not None else None,
        dimensions=dims,
        color=color,
        material=material,
        brand=(row.get("brand") or "").strip() or None,
        tags=sorted({v.lower() for k, v in specs.items() if k in ("Type", "Pattern", "Shape") and v}),
        room_types=common.room_types_for(category, f"{name} {tree}"),
        small_room_suitable=common.small_room_suitable(category, dims),
        source=SOURCE_ID,
        source_license=SOURCE_LICENSE,
        verification_status=VerificationStatus.SOURCE_DATASET,
    )


def read_rows(archive: Path) -> Iterator[dict[str, str]]:
    csv.field_size_limit(10_000_000)
    with zipfile.ZipFile(archive) as bundle, bundle.open(CSV_NAME) as stream:
        yield from csv.DictReader(io.TextIOWrapper(stream, encoding="utf-8", errors="replace"))


def collect(raw_dir: Path, themes: dict[str, dict]) -> Iterator[Product]:
    archive = common.fetch_once(DOWNLOAD_URL, raw_dir / ARCHIVE_NAME)
    for row in read_rows(archive):
        product = row_to_product(row, themes)
        if product is not None:
            yield product
