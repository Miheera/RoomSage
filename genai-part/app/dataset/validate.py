"""Catalog integrity rules, shared by the build pipeline and validate_dataset.py."""

from collections import Counter
from dataclasses import dataclass, field

from app.core.models import Product, VerificationStatus

# Above this a Flipkart decor listing is almost always a mis-keyed price.
MAX_PLAUSIBLE_PRICE_INR = 1_000_000


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_products(products: list[Product]) -> ValidationReport:
    report = ValidationReport()

    id_counts = Counter(p.product_id for p in products)
    report.errors += [f"duplicate product_id: {pid}" for pid, n in id_counts.items() if n > 1]

    for product in products:
        pid = product.product_id
        if product.price_inr is not None and product.price_inr > MAX_PLAUSIBLE_PRICE_INR:
            report.errors.append(f"{pid}: implausible price {product.price_inr}")
        if product.source_price and product.source_price.currency != "INR" and product.price_inr:
            report.errors.append(f"{pid}: price_inr set on a non-INR source")
        if product.is_demo:
            if not product.name.startswith("DEMO"):
                report.errors.append(f"{pid}: demo record not labelled in its name")
            if product.product_url is not None:
                report.errors.append(f"{pid}: demo record must not link to a real listing")
        elif product.product_url is None:
            report.errors.append(f"{pid}: real record has no product_url")
        if (
            product.verification_status is VerificationStatus.VERIFIED_LIVE
            and product.price_inr is None
        ):
            report.errors.append(f"{pid}: verified record has no price")
        if not product.compatible_themes:
            report.warnings.append(f"{pid}: no theme tags")
        if not product.room_types:
            report.errors.append(f"{pid}: no room types")
    return report


def summarize(products: list[Product]) -> dict:
    return {
        "total": len(products),
        "by_source": dict(Counter(p.source for p in products)),
        "by_category": dict(Counter(p.category.value for p in products)),
        "by_verification_status": dict(Counter(p.verification_status.value for p in products)),
        "with_inr_price": sum(p.price_inr is not None for p in products),
        "with_dimensions": sum(p.dimensions is not None for p in products),
        "with_theme_tags": sum(bool(p.compatible_themes) for p in products),
        "with_image": sum(p.image_url is not None for p in products),
    }
