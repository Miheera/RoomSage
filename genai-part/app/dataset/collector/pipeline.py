"""Build data/furniture/products.json from the configured sources."""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz

from app.core.models import Product, VerificationStatus
from app.dataset.collector import common, curated, flipkart, ikea
from app.dataset.validate import ValidationReport, summarize, validate_products

# Listings for one product in another colour or pack size score above this.
NEAR_DUPLICATE_SCORE = 95
_PACK_NOTE = re.compile(r"\(.*?\)|pack of \d+", re.IGNORECASE)


@dataclass
class BuildResult:
    products: list[Product]
    report: ValidationReport
    collected: int
    duplicates_removed: int
    untagged_removed: int


def _dedupe_key(product: Product) -> tuple:
    return (product.source, product.category, (product.brand or "").lower(), product.price_inr)


def _comparable_name(product: Product) -> str:
    return _PACK_NOTE.sub(" ", product.name).lower()


def dedupe(products: list[Product]) -> list[Product]:
    """Keep the first of each near-identical listing. Only records sharing source,
    category, brand and price are compared, so distinct items are not merged."""
    groups: dict[tuple, list[Product]] = defaultdict(list)
    for product in products:
        groups[_dedupe_key(product)].append(product)

    kept: list[Product] = []
    for group in groups.values():
        unique: list[Product] = []
        for product in group:
            # IKEA reuses series names across different sizes, which have distinct ids.
            if product.brand == "IKEA" or not any(
                fuzz.token_sort_ratio(_comparable_name(product), _comparable_name(other))
                >= NEAR_DUPLICATE_SCORE
                for other in unique
            ):
                unique.append(product)
        kept.extend(unique)
    return kept


def build(data_dir: Path) -> BuildResult:
    furniture_dir = data_dir / "furniture"
    raw_dir = data_dir / "raw"
    themes = common.load_themes(furniture_dir / "themes.json")

    records = [
        *curated.collect(furniture_dir),
        *flipkart.collect(raw_dir / "flipkart", themes),
        *ikea.collect(raw_dir / "ikea", themes),
    ]
    collected = len(records)
    # A source can list one item under several categories; the first wins.
    by_id = {p.product_id: p for p in reversed(records)}
    unique = dedupe(list(reversed(by_id.values())))
    # A product with no theme can never be recommended, so it only adds noise.
    tagged = [p for p in unique if p.compatible_themes]
    products = sorted(tagged, key=lambda p: p.product_id)
    return BuildResult(
        products=products,
        report=validate_products(products),
        collected=collected,
        duplicates_removed=collected - len(unique),
        untagged_removed=len(unique) - len(tagged),
    )


def write(result: BuildResult, data_dir: Path) -> None:
    furniture_dir = data_dir / "furniture"
    payload = [p.model_dump(mode="json", exclude_none=True) for p in result.products]
    (furniture_dir / "products.json").write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest = {
        "built_on": date.today().isoformat(),
        "collected": result.collected,
        "duplicates_removed": result.duplicates_removed,
        "untagged_removed": result.untagged_removed,
        **summarize(result.products),
        "verification_status_note": {
            VerificationStatus.SOURCE_DATASET.value: (
                "Real listing from a dated dataset. Not re-checked against the live site."
            ),
        },
    }
    (furniture_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
