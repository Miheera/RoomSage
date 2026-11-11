"""Validate and import only new, clean rows from furniture_catalog_with_images.csv.

Run from ``backend``. The default mode is read-only; pass ``--import-valid`` only
after reviewing the validation report.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select

from database import SessionLocal
from models import CatalogProduct

CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "furniture_catalog_with_images.csv"
EXPECTED_HEADER = [
    "product_id", "title", "category", "theme", "price_inr", "dimensions_m",
    "primary_color", "primary_material", "prompt_phrase", "image_s3_key", "buy_url",
    "image_url", "room_types", "role", "kind", "selectable",
]
ROLES = {
    "sofa", "bed", "chair", "coffee_table", "dining_table", "side_table",
    "storage", "rug", "lighting", "decor", "paint", "flooring",
}
ROOMS = {"living_room", "bedroom", "dining_room"}
STYLE_CODES = {
    "JAP": "Japandi", "IND": "Industrial", "BOH": "Bohemian",
    "MIN": "Modern Minimalist", "NOR": "Nordic / Scandinavian",
    "MIC": "Modern Indian Contemporary",
}


def _normalized_price(value: str) -> Decimal:
    try:
        price = Decimal(value.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("price_inr must be numeric") from exc
    if not price.is_finite():
        raise ValueError("price_inr must be finite")
    if price <= 0:
        raise ValueError("price_inr must be positive")
    if price != price.to_integral_value():
        raise ValueError("price_inr must not have a non-zero fractional part")
    return Decimal(int(price))


def _record_values(row: dict[str, str]) -> dict[str, object]:
    return {
        "title": row["title"].strip(),
        "category": row["category"].strip(),
        "role": row["role"].strip(),
        "kind": row["kind"].strip(),
        "selectable": row.get("selectable", "").strip().casefold() == "true",
        "theme": row["theme"].strip(),
        "room_types": sorted(set(json.loads(row["room_types"]))),
        "price_inr": _normalized_price(row["price_inr"]),
        "dimensions_m": [float(value) for value in json.loads(row["dimensions_m"])],
        "primary_color": row["primary_color"].strip(),
        "primary_material": row["primary_material"].strip(),
        "prompt_phrase": row["prompt_phrase"].strip(),
        "image_s3_key": row["image_s3_key"].strip().replace("\\", "/"),
        "buy_url": row["buy_url"].strip(),
        # CSV image_url is the upstream source. DB image_url is the mirrored API URL.
        "source_image_url": row.get("image_url", "").strip() or None,
    }


def validate_rows(path: Path = CSV_PATH) -> tuple[list[dict[str, str]], list[tuple[str, list[str]]], list[tuple[str, str, list[str]]], dict[str, CatalogProduct]]:
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        header = reader.fieldnames or []
        rows = list(reader)
    if header != EXPECTED_HEADER:
        raise ValueError(f"CSV header/order mismatch: expected {EXPECTED_HEADER!r}, got {header!r}")

    with SessionLocal() as db:
        existing = db.scalars(select(CatalogProduct)).all()
    existing_by_id = {item.product_id: item for item in existing}
    existing_image_keys = {item.image_s3_key: item.product_id for item in existing}

    parsed = []
    errors: list[tuple[str, list[str]]] = []
    buy_urls: dict[str, list[str]] = defaultdict(list)
    names: dict[str, list[str]] = defaultdict(list)
    seen_ids: set[str] = set()
    for line, row in enumerate(rows, start=2):
        product_id = (row.get("product_id") or "").strip()
        reasons: list[str] = []
        match = re.fullmatch(r"([A-Z]+)-([A-Z]+)-(\d{3})", product_id)
        if not match:
            reasons.append("ID must match [ROLE]-[STYLE]-[NNN]")
        else:
            _, style_code, _ = match.groups()
            if STYLE_CODES.get(style_code) != row.get("theme", "").strip():
                reasons.append(f"ID style code {style_code} does not match style {row.get('theme')!r}")
        if product_id in seen_ids:
            reasons.append("duplicate product_id in CSV")
        seen_ids.add(product_id)
        for field in ("title", "role", "theme", "category", "image_s3_key", "buy_url"):
            if not (row.get(field) or "").strip():
                reasons.append(f"{field} is empty")
        image_key = (row.get("image_s3_key") or "").strip().replace("\\", "/")
        image_parts = image_key.split("/")
        if (not image_key.startswith("catalog/isolated/") or image_key.startswith("/")
                or not image_key.casefold().endswith(".png")
                or any(part in {"", ".", ".."} for part in image_parts)):
            reasons.append("image_s3_key must be a safe PNG key under catalog/isolated/")
        elif image_key in existing_image_keys and existing_image_keys[image_key] != product_id:
            reasons.append(f"image_s3_key collides with {existing_image_keys[image_key]}")
        if row.get("role", "").strip() not in ROLES:
            reasons.append("role is outside the allowed role list")
        if row.get("selectable", "").strip().casefold() not in {"true", "false"}:
            reasons.append("selectable must be true or false")
        if row.get("kind", "").strip() not in {"object", "surface"}:
            reasons.append("kind must be object or surface")
        try:
            room_types = json.loads(row.get("room_types", ""))
            if (not isinstance(room_types, list) or not room_types
                    or any(not isinstance(room, str) or room not in ROOMS for room in room_types)):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            reasons.append("room_types must be a JSON list of supported room types")
        try:
            _normalized_price(row.get("price_inr", ""))
        except ValueError as exc:
            reasons.append(str(exc))
        try:
            dimensions = json.loads(row.get("dimensions_m", ""))
            if (not isinstance(dimensions, list) or len(dimensions) != 3
                    or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                           or value < 0 for value in dimensions)):
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            reasons.append("dimensions_m must be a JSON list of three non-negative metre values")

        if row.get("buy_url", "").strip():
            buy_urls[row["buy_url"].strip()].append(product_id)
        if row.get("title", "").strip():
            name = row["title"].strip().casefold()
            names[name].append(product_id)
        parsed.append(row)
        if reasons:
            errors.append((product_id or f"line {line}", reasons))

    duplicates = []
    for label, groups in (("buy_url", buy_urls), ("name", names)):
        duplicates.extend((label, key, ids) for key, ids in groups.items() if len(ids) > 1)
    return parsed, errors, duplicates, existing_by_id


def apply_rows(
    rows: list[dict[str, str]], errors: list[tuple[str, list[str]]],
) -> tuple[list[str], list[str]]:
    rejected = {product_id for product_id, _ in errors}
    eligible = [row for row in rows if row["product_id"].strip() not in rejected]
    inserted: list[str] = []
    updated: list[str] = []
    with SessionLocal() as db:
        for row in eligible:
            product_id = row["product_id"].strip()
            values = _record_values(row)
            current = db.get(CatalogProduct, product_id)
            if current is None:
                db.add(CatalogProduct(
                    product_id=product_id,
                    **{key: value for key, value in values.items() if key != "selectable"},
                    selectable=False,
                    image_status="pending",
                    image_url=None,
                    reference_image_s3_key=None,
                ))
                inserted.append(product_id)
                continue

            source_changed = current.source_image_url != values["source_image_url"]
            for field, value in values.items():
                if field in {"selectable", "role", "kind"}:
                    # Selection is determined by the mirror job, never by a CSV flag.
                    # Role and kind are protected for existing rows; diffs remain visible
                    # in the preview but applying updates never changes these fields.
                    continue
                setattr(current, field, value)
            if source_changed:
                current.reference_image_s3_key = None
                current.image_url = None
                current.image_status = "pending"
                if current.kind == "object":
                    current.selectable = False
            updated.append(product_id)
        db.commit()
    return inserted, updated


def _display(value: object) -> str:
    if isinstance(value, Decimal):
        return str(int(value)) if value == value.to_integral_value() else str(value)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return "null" if value is None else str(value)


def compare_existing(rows: list[dict[str, str]], existing_by_id: dict[str, CatalogProduct]):
    diffs = []
    protected = []
    derived = []
    for row in rows:
        product_id = row["product_id"].strip()
        current = existing_by_id.get(product_id)
        if current is None:
            continue
        for field, new_value in _record_values(row).items():
            old_value = getattr(current, field)
            if old_value == new_value:
                continue
            record = (product_id, field, _display(old_value), _display(new_value))
            if field in {"role", "kind"}:
                protected.append(record)
            elif field == "selectable":
                derived.append(record)
            else:
                diffs.append(record)
        # The CSV ID is the lookup key and is never updated by this importer.
    return diffs, protected, derived


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--upsert", action="store_true", help="Preview existing-row diffs and new rows; do not write.")
    parser.add_argument("--apply-updates", action="store_true", help="Apply valid inserts and existing-row updates.")
    args = parser.parse_args()
    rows, errors, duplicates, existing_by_id = validate_rows(args.csv)
    existing_rows = [row for row in rows if row["product_id"].strip() in existing_by_id]
    new_rows = [row for row in rows if row["product_id"].strip() not in existing_by_id]
    print(f"Rows: {len(rows)}; passing: {len(rows) - len(errors)}; existing: {len(existing_rows)}; new: {len(new_rows)}; rejected: {len(errors)}")
    for product_id, reasons in errors:
        print(f"REJECT {product_id}: {'; '.join(reasons)}")
    for kind, key, product_ids in duplicates:
        print(f"DUPLICATE {kind}: {key!r} => {', '.join(product_ids)}")
    if args.upsert or args.apply_updates:
        rejected = {product_id for product_id, _ in errors}
        valid_rows = [row for row in rows if row["product_id"].strip() not in rejected]
        diffs, protected, derived = compare_existing(valid_rows, existing_by_id)
        print("UPDATE DIFFS (ID | field | old value | new value)")
        for record in diffs:
            print("DIFF | " + " | ".join(record))
        print("PROTECTED FIELD DIFFS (role/kind; listed separately)")
        for record in protected:
            print("PROTECTED | " + " | ".join(record))
        if not protected:
            print("PROTECTED | none")
        print("DERIVED FIELD DIFFS (selectable; importer does not apply CSV value)")
        for record in derived:
            print("DERIVED | " + " | ".join(record))
        if not derived:
            print("DERIVED | none")
        print("ID changes: none; existing product IDs are immutable lookup keys.")
    if args.apply_updates:
        inserted, updated = apply_rows(rows, errors)
        print(f"APPLIED: {len(inserted)} inserted; {len(updated)} existing rows processed")
    else:
        print("No changes applied. Use --upsert to preview; --apply-updates is required to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
