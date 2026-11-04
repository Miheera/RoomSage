"""Write placeholder products for categories with no priced real records, so a demo can
show a complete budget. Enable with DEMO_CATALOG=true.

These are not real products. Prices are round placeholders, names start with DEMO,
there is no product link, and results that use them say so in `price_notes`.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.core.models import Category, Dimensions, Product, RoomType, Theme, ThemeSource, VerificationStatus

PLACEHOLDERS = [
    ("bed", Category.BED, 15000, Dimensions(width_cm=160, depth_cm=200), [RoomType.BEDROOM]),
    ("coffee table", Category.TABLE, 4000, Dimensions(width_cm=90, depth_cm=50), [RoomType.LIVING_ROOM]),
    ("dining table", Category.TABLE, 12000, Dimensions(width_cm=120, depth_cm=75), [RoomType.DINING_ROOM]),
    ("desk", Category.TABLE, 6000, Dimensions(width_cm=110, depth_cm=55), [RoomType.STUDY]),
    ("chair", Category.CHAIR, 3000, Dimensions(width_cm=50, depth_cm=50),
     [RoomType.DINING_ROOM, RoomType.STUDY]),
]


def main() -> None:
    records = []
    for slug, category, price, dims, rooms in PLACEHOLDERS:
        records.append(Product(
            product_id=f"demo-{slug.replace(' ', '-')}",
            name=f"DEMO {slug} (placeholder, not a real product)",
            category=category,
            compatible_themes=list(Theme),
            theme_source=ThemeSource.MANUAL,
            price_inr=price,
            price_as_of="demo",
            dimensions=dims,
            room_types=rooms,
            small_room_suitable=True,
            source="demo_seed",
            source_license="none",
            verification_status=VerificationStatus.DEMO,
        ).model_dump(mode="json", exclude_none=True))
    path = get_settings().data_dir / "furniture" / "demo_products.json"
    path.write_text(json.dumps(records, indent=1) + "\n", encoding="utf-8")
    print(f"Wrote {len(records)} demo records to {path}")


if __name__ == "__main__":
    main()
