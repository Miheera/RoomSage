"""Validate, normalize, and mirror catalog reference images into object storage."""

from __future__ import annotations

import io
import logging
import re
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from database import SessionLocal
from models import CatalogProduct
from services.catalog_image_storage import catalog_image_exists, get_catalog_image, put_catalog_image

LOGGER = logging.getLogger("mirror_catalog_images")
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_SIDE = 1024
MIN_IMAGE_SIDE = 256
REPO_ROOT = Path(__file__).resolve().parents[2]
MANUAL_IMAGE_DIRS = (REPO_ROOT / "manual_images", Path.cwd() / "manual_images")
MANUAL_REVIEW_FILE = Path(__file__).resolve().parents[1] / "data" / "catalog_image_review_ids.txt"

try:  # Register AVIF decoding when the optional plugin is installed.
    import pillow_avif  # type: ignore[import-not-found]  # noqa: F401
except ImportError:
    pass


def _manual_image(product: CatalogProduct) -> bytes | None:
    for directory in dict.fromkeys(path.resolve() for path in MANUAL_IMAGE_DIRS):
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            path = directory / f"{product.product_id}{suffix}"
            if path.is_file():
                return path.read_bytes()
    return None


def _source_urls(url: str) -> list[str]:
    """Try an Amazon full-size URL in addition to the original size variant."""
    parsed = urlsplit(url)
    full_path = re.sub(r"\._(?:AC_SL|SX|SY|SL|SS|UX|UY)\d+_?", "", parsed.path, flags=re.IGNORECASE)
    full_url = urlunsplit((parsed.scheme, parsed.netloc, full_path, parsed.query, parsed.fragment))
    return [url] if full_url == url else [url, full_url]


def _normalize(raw: bytes) -> tuple[bytes | None, tuple[int, int] | None, str | None]:
    try:
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
            dimensions = image.size
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True, progressive=True)
            return output.getvalue(), dimensions, None
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return None, None, f"invalid image ({type(exc).__name__})"


def _fetch_response(client: httpx.Client, url: str, headers: dict[str, str] | None):
    with client.stream("GET", url, headers=headers) as response:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_SOURCE_BYTES:
                return response.status_code, response.headers, None, "source exceeds 20 MiB"
            chunks.append(chunk)
        return response.status_code, response.headers, b"".join(chunks), None


def _download_and_normalize(product: CatalogProduct) -> tuple[str | None, bytes | None, str | None]:
    manual = _manual_image(product)
    if manual is not None:
        normalized, dimensions, error = _normalize(manual)
        if error:
            return None, None, f"manual image: {error}"
        assert normalized is not None and dimensions is not None
        if min(dimensions) < MIN_IMAGE_SIDE:
            return f"catalog/reference-mirrored/{product.product_id}.jpg", normalized, f"review: under 256 px ({dimensions[0]}x{dimensions[1]})"
        return f"catalog/reference-mirrored/{product.product_id}.jpg", normalized, None

    url = product.source_image_url
    if not url:
        return None, None, "missing image_url"
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(18.0, connect=8.0),
            headers={"User-Agent": "RoomSageCatalogMirror/1.0"},
        ) as client:
            candidates = []
            for candidate_url in _source_urls(url):
                for browser_retry in (False, True):
                    headers = None
                    if browser_retry:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
                            "Referer": product.buy_url or url,
                        }
                    status, response_headers, raw, fetch_error = _fetch_response(client, candidate_url, headers)
                    if fetch_error:
                        return None, None, fetch_error
                    assert raw is not None
                    normalized, dimensions, _ = _normalize(raw) if status == 200 else (None, None, None)
                    content_type = response_headers.get("content-type", "").split(";")[0].casefold()
                    is_html = content_type in {"text/html", "application/xhtml+xml"}
                    if normalized is not None and dimensions is not None:
                        candidates.append((dimensions[0] * dimensions[1], len(raw), candidate_url, normalized, dimensions))
                    retryable_response = status == 403 or is_html
                    if browser_retry or not retryable_response:
                        break
                    LOGGER.info("Retrying blocked/non-image URL for %s with browser headers", product.product_id)
            if not candidates:
                return None, None, f"HTTP/content fetch failed ({status}, {content_type or 'unknown'})"
            _, _, used_url, normalized, dimensions = max(candidates, key=lambda item: (item[0], item[1]))
            if min(dimensions) < MIN_IMAGE_SIDE:
                return f"catalog/reference-mirrored/{product.product_id}.jpg", normalized, f"review: under 256 px ({dimensions[0]}x{dimensions[1]})"
            LOGGER.info("Mirrored %s from %s (%dx%d)", product.product_id, used_url, *dimensions)
            return f"catalog/reference-mirrored/{product.product_id}.jpg", normalized, None
    except httpx.TimeoutException as exc:
        return None, None, f"timeout ({type(exc).__name__})"
    except httpx.HTTPError as exc:
        return None, None, f"HTTP error ({type(exc).__name__})"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return None, None, f"invalid image ({type(exc).__name__})"


def _existing_mirror_is_usable(key: str) -> bool:
    try:
        with Image.open(io.BytesIO(get_catalog_image(key))) as image:
            image.load()
            return (image.format == "JPEG" and image.mode == "RGB"
                    and min(image.size) >= MIN_IMAGE_SIDE
                    and max(image.size) <= MAX_IMAGE_SIDE)
    except (OSError, UnidentifiedImageError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", nargs="*", help="Only mirror these product IDs.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    manually_reviewed = set()
    if MANUAL_REVIEW_FILE.is_file():
        manually_reviewed = {
            line.strip() for line in MANUAL_REVIEW_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
    with SessionLocal() as db:
        statement = select(CatalogProduct).order_by(CatalogProduct.product_id)
        if args.ids:
            statement = statement.where(CatalogProduct.product_id.in_(args.ids))
        products = db.scalars(statement).all()
        results = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            def prepare(product: CatalogProduct):
                key = f"catalog/reference-mirrored/{product.product_id}.jpg"
                try:
                    if _manual_image(product) is not None:
                        return _download_and_normalize(product)
                    if catalog_image_exists(key) and _existing_mirror_is_usable(key):
                        return key, None, None
                    return _download_and_normalize(product)
                except Exception:
                    return _download_and_normalize(product)

            for product, result in zip(products, pool.map(prepare, products)):
                key, normalized, error = result
                if product.kind != "object" and error and error.startswith("review:"):
                    error = None
                if product.kind == "surface" and error == "missing image_url":
                    error = None
                if error and not error.startswith("review:"):
                    key = None
                review = bool(error and error.startswith("review:"))
                if key and normalized is not None:
                    try:
                        put_catalog_image(key, normalized)
                    except Exception as exc:
                        LOGGER.warning("Catalog image mirror storage write failed for %s: %s",
                                       product.product_id, type(exc).__name__)
                        key, error = None, f"storage write failed ({type(exc).__name__})"
                product.reference_image_s3_key = key
                product.image_url = f"/api/v1/catalog/products/{product.product_id}/image" if key else None
                if (product.kind == "object" and product.product_id in manually_reviewed
                        and _manual_image(product) is None and key is not None):
                    error = "review: image requires manual clutter/product review"
                    review = True
                if error and error.startswith("review:"):
                    product.image_status = "review"
                elif key:
                    product.image_status = "ready"
                elif product.kind == "surface":
                    product.image_status = "not_required"
                else:
                    product.image_status = "failed"
                fits_a_room = bool(product.room_types)
                product.selectable = bool(
                    (product.kind == "surface" and fits_a_room)
                    or (product.kind == "object" and not review and key is not None)
                )
                results.append((product.product_id, error))
        db.commit()

    product_kinds = {product.product_id: product.kind for product in products}
    failures = [
        (product_id, error) for product_id, error in results
        if error and not error.startswith("review:")
        and not (product_kinds.get(product_id) == "surface" and error == "missing image_url")
    ]
    reviews = [(product_id, error) for product_id, error in results if error and error.startswith("review:")]
    mirrored = sum(bool(product.reference_image_s3_key) for product in products)
    not_required = sum(product.kind == "surface" and not product.source_image_url for product in products)
    LOGGER.info(
        "Catalog image mirror complete: %d rows, %d images mirrored, %d failed, %d needing review, %d surfaces without an optional image.",
        len(results), mirrored, len(failures), len(reviews), not_required,
    )
    for product_id, error in failures + reviews:
        product_url = next((product.source_image_url for product in products
                            if product.product_id == product_id), None)
        LOGGER.warning("Catalog image unavailable for %s (%s): %s", product_id,
                       product_url or "missing image_url", error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
