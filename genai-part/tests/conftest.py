import pytest

from app.config import Settings
from app.core.models import Category, Product, Theme, VerificationStatus
from app.dataset.catalog import Catalog


def make_product(product_id: str, category: Category, price: float | None, **overrides) -> Product:
    fields = {
        "product_id": product_id,
        "name": f"Test {product_id}",
        "category": category,
        "compatible_themes": [Theme.MODERN, Theme.MINIMALIST],
        "room_types": ["living_room", "bedroom"],
        "product_url": f"https://example.com/{product_id}",
        "price_inr": price,
        "price_as_of": "2016-01-01" if price else None,
        "source": "test",
        "source_license": "CC0-1.0",
        "verification_status": VerificationStatus.SOURCE_DATASET,
    }
    return Product(**{**fields, **overrides})


@pytest.fixture(scope="session")
def real_catalog() -> Catalog:
    return Catalog.load(Settings(_env_file=None))
