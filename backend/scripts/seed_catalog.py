"""Validate isolated catalog PNGs in S3 and upsert the furniture CSV into PostgreSQL.

Run from ``backend`` after applying ``migrations/0004_catalog_products.sql``:

    python scripts/seed_catalog.py
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func

from config import settings
from database import SessionLocal
from models import CatalogProduct
from services.s3_service import get_s3_client

LOGGER = logging.getLogger("seed_catalog")
DEFAULT_CSV = Path(__file__).resolve().parents[1] / "data" / "furniture_catalog.csv"
REQUIRED_COLUMNS = {
    "product_id",
    "title",
    "category",
    "theme",
    "price_inr",
    "dimensions_m",
    "primary_color",
    "primary_material",
    "prompt_phrase",
    "image_s3_key",
    "buy_url",
}
IMAGE_PREFIX = "catalog/isolated/"


class CatalogSeedError(ValueError):
    """Raised when the source catalog fails validation."""


def _required(row: dict[str, str], field: str, line_number: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise CatalogSeedError(f"CSV line {line_number}: {field} must not be blank.")
    return value


def _parse_dimensions(value: str, line_number: int) -> list[float]:
    try:
        dimensions = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CatalogSeedError(f"CSV line {line_number}: dimensions_m must be a JSON array.") from exc
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != 3
        or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in dimensions)
    ):
        raise CatalogSeedError(
            f"CSV line {line_number}: dimensions_m must contain three finite, non-negative numbers."
        )
    return [float(value) for value in dimensions]


def _parse_row(row: dict[str, str], line_number: int) -> dict[str, Any]:
    try:
        price = Decimal(_required(row, "price_inr", line_number))
    except InvalidOperation as exc:
        raise CatalogSeedError(f"CSV line {line_number}: price_inr must be numeric.") from exc
    if not price.is_finite() or price <= 0:
        raise CatalogSeedError(f"CSV line {line_number}: price_inr must be positive and finite.")

    image_key = _required(row, "image_s3_key", line_number).replace("\\", "/")
    key_parts = image_key.split("/")
    if (
        not image_key.startswith(IMAGE_PREFIX)
        or image_key.startswith("/")
        or any(part in {"", ".", ".."} for part in key_parts)
        or not image_key.lower().endswith(".png")
    ):
        raise CatalogSeedError(
            f"CSV line {line_number}: image_s3_key must be a safe PNG key under {IMAGE_PREFIX}."
        )

    return {
        "product_id": _required(row, "product_id", line_number),
        "title": _required(row, "title", line_number),
        "category": _required(row, "category", line_number),
        "theme": _required(row, "theme", line_number),
        "price_inr": price,
        "dimensions_m": _parse_dimensions(_required(row, "dimensions_m", line_number), line_number),
        "primary_color": _required(row, "primary_color", line_number),
        "primary_material": _required(row, "primary_material", line_number),
        "prompt_phrase": _required(row, "prompt_phrase", line_number),
        "image_s3_key": image_key,
        "buy_url": _required(row, "buy_url", line_number),
    }


def read_catalog(csv_path: Path) -> list[dict[str, Any]]:
    if not csv_path.is_file():
        raise CatalogSeedError(f"Catalog CSV does not exist: {csv_path}")
    with csv_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or [])
        missing_columns = sorted(REQUIRED_COLUMNS - columns)
        if missing_columns:
            raise CatalogSeedError(f"Catalog CSV is missing columns: {', '.join(missing_columns)}")
        records = [_parse_row(row, index) for index, row in enumerate(reader, start=2)]
    if not records:
        raise CatalogSeedError("Catalog CSV contains no product rows.")
    ids = [record["product_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise CatalogSeedError("Catalog CSV contains duplicate product_id values.")
    return records


def verify_s3_images(records: list[dict[str, Any]]) -> None:
    s3 = get_s3_client()
    missing: list[str] = []
    for record in records:
        key = record["image_s3_key"]
        try:
            s3.head_object(Bucket=settings.s3_bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {"404", "NoSuchKey", "NotFound"} or status_code == 404:
                missing.append(key)
                continue
            raise CatalogSeedError(f"Could not verify s3://{settings.s3_bucket}/{key}: {code or exc}") from exc
        except BotoCoreError as exc:
            raise CatalogSeedError(f"Could not connect to S3 while checking {key}: {exc}") from exc
    if missing:
        preview = "\n  ".join(missing[:20])
        suffix = f"\n  ... and {len(missing) - 20} more" if len(missing) > 20 else ""
        raise CatalogSeedError(
            f"{len(missing)} isolated catalog PNG(s) are missing from bucket "
            f"{settings.s3_bucket!r}:\n  {preview}{suffix}\nNo database rows were changed."
        )


def upsert_catalog(records: list[dict[str, Any]]) -> int:
    statement = insert(CatalogProduct)
    updates = {
        column.name: getattr(statement.excluded, column.name)
        for column in CatalogProduct.__table__.columns
        if column.name not in {"product_id", "created_at"}
    }
    updates["updated_at"] = func.now()
    statement = statement.values(records).on_conflict_do_update(
        index_elements=[CatalogProduct.product_id],
        set_=updates,
    )
    with SessionLocal() as db:
        try:
            db.execute(statement)
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Catalog CSV path.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    records = read_catalog(args.csv)
    LOGGER.info("Validated %d catalog rows from %s", len(records), args.csv)
    verify_s3_images(records)
    LOGGER.info("Verified all %d isolated PNGs in s3://%s/%s", len(records), settings.s3_bucket, IMAGE_PREFIX)
    count = upsert_catalog(records)
    LOGGER.info("Upserted %d catalog products into PostgreSQL", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
