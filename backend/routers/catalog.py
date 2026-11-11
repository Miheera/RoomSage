from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from io import BytesIO
from decimal import Decimal

from database import get_db
from catalog_roles import ROOM_ROLE_REQUIREMENTS
from models import CatalogProduct
from services.catalog_image_storage import get_catalog_image

router = APIRouter(prefix="/catalog", tags=["catalog"])
_LABELS = {"living_room": "Living Room", "bedroom": "Bedroom", "dining_room": "Dining Room"}


@router.get("/room-types")
def get_room_types(db: Session = Depends(get_db)) -> list[dict[str, str]]:
    products = db.scalars(select(CatalogProduct)).all()
    renderable = [product for product in products if product.selectable
                  and product.kind == "object" and product.reference_image_s3_key]
    supported = sorted(
        room_type for room_type, requirements in ROOM_ROLE_REQUIREMENTS.items()
        if room_type in _LABELS and all(
            any(product.role == requirement.role and room_type in (product.room_types or [])
                for product in renderable)
            for requirement in requirements
        )
    )
    return [{"type": value, "name": _LABELS[value]} for value in supported]


@router.get("/room-types/{room_type}/styles")
def get_room_styles(room_type: str, db: Session = Depends(get_db)) -> list[dict]:
    requirements = ROOM_ROLE_REQUIREMENTS.get(room_type)
    if requirements is None:
        raise HTTPException(status_code=404, detail="Unsupported catalog room type.")
    products = [product for product in db.scalars(select(CatalogProduct)).all()
                if product.selectable and (product.kind == "surface" or product.reference_image_s3_key)]
    themes = sorted({
        product.theme for product in products
        if room_type in (product.room_types or [])
    })
    styles = []
    for theme in themes:
        matching = [product for product in products
                    if product.theme == theme and room_type in (product.room_types or [])]
        counts = {req.role: sum(product.kind == "object" and product.role == req.role for product in matching)
                  for req in requirements}
        if all(counts.values()):
            minimum = sum((min(product.price_inr for product in matching if product.role == req.role)
                           * req.minimum_quantity for req in requirements), Decimal("0"))
            styles.append({"style": theme, "name": theme, "items_per_role": counts,
                           "minimum_budget": float(minimum)})
    return styles


@router.get("/products/{product_id}/image")
def get_catalog_reference_image(product_id: str, db: Session = Depends(get_db)):
    product = db.get(CatalogProduct, product_id)
    if product is None or not product.reference_image_s3_key:
        raise HTTPException(status_code=404, detail="No mirrored reference image is available.")
    try:
        data = get_catalog_image(product.reference_image_s3_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read the catalog reference image.") from exc
    return StreamingResponse(BytesIO(data), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
