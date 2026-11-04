"""Shared domain types used across the catalog, design and budget layers."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class Theme(str, Enum):
    MODERN = "modern"
    MINIMALIST = "minimalist"
    SCANDINAVIAN = "scandinavian"
    INDUSTRIAL = "industrial"
    BOHEMIAN = "bohemian"
    TRADITIONAL = "traditional"
    CONTEMPORARY = "contemporary"
    JAPANDI = "japandi"


class Category(str, Enum):
    SOFA_SEATING = "sofa_seating"
    BED = "bed"
    TABLE = "table"
    CHAIR = "chair"
    STORAGE = "storage"
    LIGHTING = "lighting"
    RUG = "rug"
    CURTAIN = "curtain"
    WALL_DECOR = "wall_decor"
    PLANT = "plant"
    ACCESSORY = "accessory"


class RoomType(str, Enum):
    LIVING_ROOM = "living_room"
    BEDROOM = "bedroom"
    DINING_ROOM = "dining_room"
    STUDY = "study"
    KIDS_ROOM = "kids_room"
    OTHER = "other"


class VerificationStatus(str, Enum):
    # Checked by a person against the live listing on `price_as_of`.
    VERIFIED_LIVE = "verified_live"
    # Real listing from a dated public dataset; price and availability are historic.
    SOURCE_DATASET = "source_dataset"
    # Placeholder for demos. Not a real product.
    DEMO = "demo"


class ThemeSource(str, Enum):
    SOURCE_STYLE = "source_style"  # the listing itself stated a style
    DERIVED_RULES = "derived_rules"  # inferred from material, colour and keywords
    MANUAL = "manual"


class Dimensions(BaseModel):
    """Centimetres. Each axis is None when the source does not state it."""

    width_cm: float | None = Field(default=None, gt=0)
    depth_cm: float | None = Field(default=None, gt=0)
    height_cm: float | None = Field(default=None, gt=0)

    def footprint_cm(self) -> float | None:
        known = [v for v in (self.width_cm, self.depth_cm) if v is not None]
        return max(known) if known else None


class SourcePrice(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class Product(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    product_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: Category
    compatible_themes: list[Theme] = Field(default_factory=list)
    theme_source: ThemeSource = ThemeSource.DERIVED_RULES
    description: str | None = None
    image_url: HttpUrl | None = None
    local_image_path: str | None = None
    # S3 object key Person A can read; set only after the image is uploaded there.
    catalog_image_key: str | None = None
    product_url: HttpUrl | None = None
    # None means unknown. Never zero, never converted from another currency.
    price_inr: float | None = Field(default=None, gt=0)
    price_as_of: str | None = None
    source_price: SourcePrice | None = None
    dimensions: Dimensions | None = None
    color: str | None = None
    material: str | None = None
    brand: str | None = None
    tags: list[str] = Field(default_factory=list)
    room_types: list[RoomType] = Field(default_factory=list)
    small_room_suitable: bool | None = None
    source: str
    source_license: str
    verification_status: VerificationStatus

    @model_validator(mode="after")
    def _price_needs_a_date(self) -> "Product":
        if self.price_inr is not None and self.price_as_of is None:
            raise ValueError("price_inr requires price_as_of")
        return self

    @property
    def has_known_price(self) -> bool:
        return self.price_inr is not None

    @property
    def is_demo(self) -> bool:
        return self.verification_status is VerificationStatus.DEMO
