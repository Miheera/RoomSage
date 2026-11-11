from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.image_generation_service import HuggingFaceImageProvider
from config import settings

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FIXTURES = BACKEND / "tests" / "fixtures" / "sample_room"
THUMBS = BACKEND / "data" / "catalog_image_audit" / "thumbs"


def main() -> None:
    os.environ["TRACE_PIPELINE"] = "1"
    catalog = {row["product_id"]: row for row in csv.DictReader(
        (BACKEND / "data" / "furniture_catalog_with_images.csv").open(encoding="utf-8-sig")
    )}
    reports = []
    photo_numbers = tuple(
        int(value) for value in os.getenv("TRACE_PHOTOS", "2,5,6,11").split(",") if value.strip()
    )
    for number in photo_numbers:
        design_id = f"surface-photo-{number:02}-{uuid.uuid4()}"
        items = [
            {"catalog_item_id": "PAINT-JAP-087", "title": catalog["PAINT-JAP-087"]["title"],
             "kind": "surface", "role": "paint", "primary_color": catalog["PAINT-JAP-087"]["primary_color"],
             "prompt_phrase": catalog["PAINT-JAP-087"]["prompt_phrase"]},
            {"catalog_item_id": "FLOOR-JAP-009", "title": catalog["FLOOR-JAP-009"]["title"],
             "kind": "surface", "role": "flooring", "primary_color": catalog["FLOOR-JAP-009"]["primary_color"],
             "prompt_phrase": catalog["FLOOR-JAP-009"]["prompt_phrase"],
             "floor_texture_bytes": (THUMBS / "FLOOR-JAP-009.jpg").read_bytes()},
        ]
        metadata = {"design_id": design_id, "room_id": f"sample-{number}", "provider": settings.image_provider,
                    "model_ids": {"segmentation": settings.hf_segmentation_model_id},
                    "style": "Japandi", "palette": "Warm Earth", "seed": number,
                    "selected_items": [{"id": i["catalog_item_id"], "name": i["title"]} for i in items]}
        provider = HuggingFaceImageProvider()
        result = provider.render("Japandi empty room mask-based finish", (FIXTURES / f"photo_{number:02}.jpg").read_bytes(),
                                 style="Japandi", palette="Warm Earth", items=items, depth_map=None,
                                 seed=number, trace_context=metadata)
        directory = ROOT / "out" / "trace" / design_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "07_final.png").write_bytes(result)
        reports.append({"photo": number, "design_id": design_id, "render_metadata": provider.render_metadata,
                        "trace_dir": str(directory)})
        print(json.dumps(reports[-1], indent=2))
        if number == 11:
            from PIL import Image, ImageOps, ImageDraw
            with Image.open(directory / "01_input.png") as before, Image.open(directory / "09_restyled.png") as after:
                width, height = before.size
                # left doorway crop, kept large enough to inspect door frame boundary
                crop_box = (0, 0, max(1, round(width * 0.48)), max(1, round(height * 0.82)))
                left = before.crop(crop_box).convert("RGB")
                right = after.crop(crop_box).convert("RGB")
                sheet = Image.new("RGB", (left.width * 2, left.height + 36), "white")
                sheet.paste(left, (0, 36)); sheet.paste(right, (left.width, 36))
                ImageDraw.Draw(sheet).text((8, 10), "Photo 11 input crop", fill="black")
                ImageDraw.Draw(sheet).text((left.width + 8, 10), "Photo 11 after surface edit", fill="black")
                sheet.save(directory / "photo11_left_doorway_before_after.png")
    (ROOT / "out" / "surface_run_reports.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
