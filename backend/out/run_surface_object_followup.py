from __future__ import annotations

import csv
import json
import os
import sys
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import settings
from services.image_generation_service import HuggingFaceImageProvider

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "sample_room"
THUMBS = ROOT / "backend" / "data" / "catalog_image_audit" / "thumbs"
CATALOG = ROOT / "backend" / "data" / "furniture_catalog_with_images.csv"
OUT = ROOT / "out"


def main() -> None:
    os.environ["TRACE_PIPELINE"] = "1"
    os.environ.setdefault("U2NET_HOME", str(Path(settings.hf_home) / "rembg"))
    with CATALOG.open(encoding="utf-8-sig", newline="") as handle:
        rows = {row["product_id"]: row for row in csv.DictReader(handle)}

    object_ids = [
        "FURN-JAP-001", "CHAIR-JAP-042", "FURN-JAP-004",
        "LIGHT-JAP-007", "RUG-JAP-006", "DECOR-JAP-008",
    ]
    photo_reports: list[dict[str, object]] = []
    for photo_number in (2, 5, 6):
        photo = FIXTURES / f"photo_{photo_number:02}.jpg"
        design_id = f"empty-objects-photo-{photo_number:02}-{uuid.uuid4()}"
        items: list[dict[str, object]] = []
        for product_id in object_ids:
            row = rows[product_id]
            thumb = THUMBS / f"{product_id}.jpg"
            items.append({
                "catalog_item_id": product_id,
                "title": row["title"],
                "kind": "object",
                "role": row["role"],
                "qty": 1,
                "is_essential": True,
                "dimensions_m": json.loads(row["dimensions_m"]),
                "reference_image_bytes": thumb.read_bytes(),
                "price_inr": row["price_inr"],
                "style_match": True,
            })
        floor = rows["FLOOR-JAP-009"]
        items.extend([
            {
                "catalog_item_id": "PAINT-JAP-087", "title": rows["PAINT-JAP-087"]["title"],
                "kind": "surface", "role": "paint", "qty": 1, "is_essential": False,
                "primary_color": rows["PAINT-JAP-087"]["primary_color"],
                "prompt_phrase": rows["PAINT-JAP-087"]["prompt_phrase"],
                "price_inr": rows["PAINT-JAP-087"]["price_inr"], "style_match": True,
            },
            {
                "catalog_item_id": "FLOOR-JAP-009", "title": floor["title"],
                "kind": "surface", "role": "flooring", "qty": 1, "is_essential": False,
                "primary_color": floor["primary_color"],
                "prompt_phrase": floor["prompt_phrase"],
                "floor_texture_bytes": (THUMBS / "FLOOR-JAP-009.jpg").read_bytes(),
                "price_inr": floor["price_inr"], "style_match": True,
            },
        ])
        metadata = {
            "design_id": design_id,
            "room_id": f"sample-photo-{photo_number:02}",
            "provider": settings.image_provider,
            "model_ids": {"segmentation": settings.hf_segmentation_model_id},
            "style": "Japandi",
            "palette": "Warm Earth",
            "seed": 20261108 + photo_number,
            "selected_items": [
                {"id": item["catalog_item_id"], "name": item["title"],
                 "price_inr": item["price_inr"], "style_match": item["style_match"],
                 "role": item["role"], "kind": item["kind"], "essential": item["is_essential"]}
                for item in items
            ],
        }
        provider = HuggingFaceImageProvider()
        image_bytes = photo.read_bytes()
        output = provider.render(
            "Japandi living room, mask-based wall and floor finishes with catalog furniture",
            image_bytes,
            style="Japandi", palette="Warm Earth", items=items, depth_map=None,
            seed=int(metadata["seed"]), trace_context=metadata,
        )
        hotspots = provider.locate_items(output, items)
        design_dir = OUT / "trace" / design_id
        design_dir.mkdir(parents=True, exist_ok=True)
        (design_dir / "input_original.jpg").write_bytes(image_bytes)
        (design_dir / "07_final.png").write_bytes(output)
        (OUT / f"composite_{photo_number:02}.png").write_bytes((design_dir / "06_composite.png").read_bytes())
        (OUT / f"harmonized_{photo_number:02}.png").write_bytes((design_dir / "10_final.png").read_bytes())
        output_path = OUT / f"fix1_photo{photo_number:02}.png"
        with Image.open(BytesIO(image_bytes)) as input_image, Image.open(BytesIO(output)) as final_image:
            canvas = Image.new("RGB", (input_image.width + final_image.width, max(input_image.height, final_image.height)))
            canvas.paste(input_image.convert("RGB"), (0, 0))
            canvas.paste(final_image.convert("RGB"), (input_image.width, 0))
            canvas.save(output_path)
        (design_dir / "selected_items.json").write_text(json.dumps(metadata["selected_items"], indent=2), encoding="utf-8")
        (design_dir / "hotspots.json").write_text(json.dumps(hotspots.model_dump(), indent=2), encoding="utf-8")
        photo_reports.append({
            "photo": photo_number,
            "design_id": design_id,
            "trace_dir": str(design_dir),
            "status": "rendered",
            "render_metadata": provider.render_metadata,
            "placements": hotspots.model_dump(),
        })
        print(json.dumps(photo_reports[-1], indent=2))
    (OUT / "object_run_reports.json").write_text(json.dumps(photo_reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
