import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.core.models import Category, Product, Theme, ThemeSource, VerificationStatus
from app.dataset.collector import common, flipkart, ikea
from app.dataset.collector.pipeline import dedupe
from app.dataset.validate import validate_products

THEMES = common.load_themes(Path(get_settings().data_dir) / "furniture" / "themes.json")
FURNITURE_DIR = Path(get_settings().data_dir) / "furniture"


def make_product(**overrides) -> Product:
    fields = {
        "product_id": "p1",
        "name": "Test Rug",
        "category": Category.RUG,
        "product_url": "https://example.com/p1",
        "compatible_themes": [Theme.MODERN],
        "room_types": ["living_room"],
        "source": "test",
        "source_license": "CC0-1.0",
        "verification_status": VerificationStatus.SOURCE_DATASET,
    }
    return Product(**{**fields, **overrides})


FLIPKART_ROW = {
    "uniq_id": "abcdef0123456789",
    "crawl_timestamp": "2016-03-25 22:59:23 +0000",
    "product_url": "http://www.flipkart.com/x/p/itm1",
    "product_name": "Purpledip Cotton Dhurrie Rug",
    "product_category_tree": '["Home Furnishing >> Floor Coverings >> Carpets & Rugs"]',
    "retail_price": "1999.0",
    "discounted_price": "899.0",
    "image": '["http://img5a.flixcart.com/image/a.jpeg"]',
    "description": "A rug.",
    "brand": "Purpledip",
    "product_specifications": (
        '{"product_specification"=>[{"key"=>"Material", "value"=>"Cotton Wool Blend"}, '
        '{"key"=>"Color", "value"=>"Multicolor"}, {"key"=>"Type", "value"=>"Dhurrie"}, '
        '{"key"=>"Length", "value"=>"60 inch / 153 cm"}, {"key"=>"Width", "value"=>"36 inch / 92 cm"}]}'
    ),
}


def test_flipkart_row_keeps_stated_facts():
    product = flipkart.row_to_product(FLIPKART_ROW, THEMES)
    assert product.category is Category.RUG
    assert product.price_inr == 899.0
    assert product.price_as_of == "2016-03-25"
    assert product.dimensions.width_cm == 92
    assert product.color == "multicolor"
    assert product.material == "cotton"
    assert Theme.BOHEMIAN in product.compatible_themes
    assert product.verification_status is VerificationStatus.SOURCE_DATASET


def test_flipkart_missing_price_stays_unknown():
    row = {**FLIPKART_ROW, "retail_price": "", "discounted_price": ""}
    product = flipkart.row_to_product(row, THEMES)
    assert product.price_inr is None
    assert product.price_as_of is None


def test_flipkart_skips_seeds_and_unmapped_categories():
    seeds = {**FLIPKART_ROW, "product_name": "Divine Tree Tomato Seed",
             "product_category_tree": '["Home Improvement >> Lawn & Gardening >> Plants"]'}
    shoes = {**FLIPKART_ROW, "product_category_tree": '["Footwear >> Women"]'}
    assert flipkart.row_to_product(seeds, THEMES) is None
    assert flipkart.row_to_product(shoes, THEMES) is None


def test_ikea_row_never_gets_an_inr_price():
    row = {
        "item_id": "90420332", "name": "FREKVENS", "category": "Tables & desks", "price": "265.0",
        "link": "https://www.ikea.com/sa/en/p/frekvens-bar-table-in-outdoor-black-90420332/",
        "short_description": "        Bar table,          51x51 cm",
        "depth": "", "height": "99", "width": "51",
    }
    product = ikea.row_to_product(row, THEMES)
    assert product.price_inr is None
    assert product.source_price.amount == 265.0
    assert product.source_price.currency == "SAR"
    assert product.color == "black"
    assert product.dimensions.depth_cm is None
    assert product.dimensions.width_cm == 51


@pytest.mark.parametrize(
    "text,expected",
    [("60 inch / 153 cm", 153.0), ("2 m", 200.0), ("10 inch", 25.4), ("Large", None), ("", None), (None, None)],
)
def test_parse_length_needs_an_explicit_unit(text, expected):
    assert common.parse_length_cm(text) == expected


def test_small_room_is_unknown_without_a_dimension():
    assert common.small_room_suitable(Category.RUG, None) is None
    assert common.small_room_suitable(Category.CURTAIN, None) is None


def test_stated_style_beats_derived_rules():
    themes, source = common.assign_themes("sofa", "white", None, THEMES, Category.SOFA_SEATING,
                                          stated_style="Contemporary and Modern")
    assert set(themes) == {Theme.MODERN, Theme.CONTEMPORARY}
    assert source is ThemeSource.SOURCE_STYLE


def test_avoid_words_block_a_theme():
    themes, _ = common.assign_themes("floral printed cushion", "white", "cotton", THEMES, Category.ACCESSORY)
    assert Theme.MINIMALIST not in themes


def test_dedupe_merges_variants_but_not_different_items():
    a = make_product(product_id="a", name="Acme Cotton Curtain (Pack of 2)", brand="Acme", price_inr=500, price_as_of="2016-01-01")
    b = make_product(product_id="b", name="Acme Cotton Curtain (Pack of 3)", brand="Acme", price_inr=500, price_as_of="2016-01-01")
    c = make_product(product_id="c", name="Acme Jute Doormat", brand="Acme", price_inr=500, price_as_of="2016-01-01")
    assert [p.product_id for p in dedupe([a, b, c])] == ["a", "c"]


def test_validator_rejects_bad_records():
    twice = make_product(product_id="same")
    demo = make_product(product_id="d", name="Real looking sofa",
                        verification_status=VerificationStatus.DEMO)
    report = validate_products([twice, twice, demo])
    assert any("duplicate" in error for error in report.errors)
    assert any("demo record not labelled" in error for error in report.errors)
    assert any("real listing" in error for error in report.errors)


def test_price_without_date_is_rejected():
    with pytest.raises(ValueError):
        make_product(price_inr=100)


def test_zero_price_is_rejected():
    with pytest.raises(ValueError):
        make_product(price_inr=0, price_as_of="2016-01-01")


def test_shipped_catalog_is_valid_and_honest():
    products = [Product.model_validate(r) for r in
                json.loads((FURNITURE_DIR / "products.json").read_text(encoding="utf-8"))]
    assert validate_products(products).ok
    assert {p.category for p in products} == set(Category)
    assert not any(p.is_demo for p in products)
    assert all(p.price_inr is None for p in products if p.source.startswith("ikea"))
