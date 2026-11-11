"""Owned design reads, catalog-item mutations, and immutable re-render versions."""

import secrets
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from io import BytesIO
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import CatalogProduct, Design, DesignItem, Job, Project, Room, User
from security import CurrentUser
from services.design_jobs import enqueue_catalog_render
from config import settings
from services.s3_service import get_s3_client

router = APIRouter(prefix="/designs", tags=["designs"])


class SwapRequest(BaseModel):
    catalog_item_id: str = Field(min_length=1, max_length=80)


def _owned_design(db: Session, design_id: uuid.UUID, user: User) -> Design:
    design = db.get(Design, design_id)
    if design is None:
        raise HTTPException(status_code=404, detail="Design not found.")
    room = db.get(Room, design.room_id)
    project = db.get(Project, room.project_id) if room else None
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="Design not found.")
    return design


def _status(status: str) -> str:
    return {"QUEUED": "queued", "PROCESSING": "running", "COMPLETED": "done", "FAILED": "failed"}.get(
        status, status.casefold()
    )


def _design_response(db: Session, design: Design) -> dict:
    room = db.get(Room, design.room_id)
    rows = db.query(DesignItem, CatalogProduct).join(
        CatalogProduct, CatalogProduct.product_id == DesignItem.catalog_item_id,
    ).filter(DesignItem.design_id == design.id).all()
    items = []
    total = Decimal("0")
    for item, product in rows:
        line_total = product.price_inr * item.qty
        total += line_total
        items.append({
            "id": str(item.id),
            "catalog_item_id": product.product_id,
            "title": product.title,
            "category": product.category,
            "role": item.role,
            "kind": product.kind,
            "qty": item.qty,
            "price": float(product.price_inr),
            "line_total": float(line_total),
            "buy_url": product.buy_url,
            "image_url": product.image_url,
            "x": float(item.x) if item.x is not None else None,
            "y": float(item.y) if item.y is not None else None,
            "visible": item.visible,
            "visibility_reason": item.visibility_reason,
            "is_estimate": False,
            "display_label": "Wall" if product.role == "paint" else "Floor" if product.role == "flooring" else None,
        })
    job = db.scalar(select(Job).where(Job.design_id == design.id).order_by(Job.created_at.desc()).limit(1))
    return {
        "id": str(design.id),
        "room_id": str(design.room_id),
        "parent_id": str(design.parent_id) if design.parent_id else None,
        "style": design.style,
        "palette": design.palette,
        "status": _status(design.status),
        "progress": job.progress if job else (100 if design.status == "COMPLETED" else 0),
        "job_id": str(job.id) if job else None,
        "image_url": f"/api/v1/designs/{design.id}/image" if design.image_url else None,
        "image_stale": design.image_stale,
        "is_mock_preview": bool(design.prompt and design.prompt.startswith("[MOCK_PREVIEW]")),
        "error": design.error_message or (job.error_message if job else None),
        "render_metadata": design.render_metadata or {},
        "budget": float(room.budget) if room and room.budget is not None else None,
        "items": items,
        "total_cost": float(total),
        "estimates": [],
        "seed": design.seed,
    }


@router.get("/{design_id}")
def get_design(
    design_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    return _design_response(db, _owned_design(db, design_id, user))


@router.get("/{design_id}/image")
def get_design_image(
    design_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
):
    design = _owned_design(db, design_id, user)
    if not design.image_url:
        raise HTTPException(status_code=404, detail="Design image is not ready.")
    try:
        response = get_s3_client().get_object(Bucket=settings.s3_bucket, Key=design.image_url)
        data = response["Body"].read()
        content_type = response.get("ContentType", "image/png")
        response["Body"].close()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not read the design image.") from exc
    return StreamingResponse(BytesIO(data), media_type=content_type,
                             headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{design_id}/items/{item_id}/alternatives")
def get_item_alternatives(
    design_id: uuid.UUID, item_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> list[dict]:
    design = _owned_design(db, design_id, user)
    item = db.get(DesignItem, item_id)
    if item is None or item.design_id != design.id:
        raise HTTPException(status_code=404, detail="Design item not found.")
    current = db.get(CatalogProduct, item.catalog_item_id)
    room = db.get(Room, design.room_id)
    products = db.scalars(select(CatalogProduct).where(
        CatalogProduct.role == item.role,
        CatalogProduct.kind == current.kind,
        CatalogProduct.selectable.is_(True),
    ).order_by(CatalogProduct.price_inr, CatalogProduct.product_id)).all()
    return [{
        "catalog_item_id": product.product_id,
        "title": product.title,
        "role": product.role,
        "kind": product.kind,
        "price": float(product.price_inr),
        "price_delta": float(product.price_inr - current.price_inr),
        "image_url": product.image_url,
        "buy_url": product.buy_url,
    } for product in products
        if product.product_id != current.product_id
        and room is not None and room.type in (product.room_types or [])
        and (product.kind == "surface" or product.reference_image_s3_key)]


@router.post("/{design_id}/items/{item_id}/swap")
def swap_design_item(
    design_id: uuid.UUID, item_id: uuid.UUID, payload: SwapRequest,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    design = _owned_design(db, design_id, user)
    item = db.get(DesignItem, item_id)
    if item is None or item.design_id != design.id:
        raise HTTPException(status_code=404, detail="Design item not found.")
    room = db.get(Room, design.room_id)
    current = db.get(CatalogProduct, item.catalog_item_id)
    replacement = db.get(CatalogProduct, payload.catalog_item_id)
    if replacement is None or room is None:
        raise HTTPException(status_code=404, detail="Replacement catalog item not found.")
    if (not replacement.selectable or replacement.role != item.role
            or replacement.kind != current.kind or room.type not in (replacement.room_types or [])
            or (replacement.kind == "object" and not replacement.reference_image_s3_key)):
        raise HTTPException(status_code=422, detail="Replacement must be a selectable catalog item with the same role that fits this room.")
    item.catalog_item_id = replacement.product_id
    item.x = None
    item.y = None
    item.visible = True
    item.visibility_reason = None
    design.image_stale = True
    db.commit()
    return _design_response(db, design)


@router.delete("/{design_id}/items/{item_id}")
def remove_design_item(
    design_id: uuid.UUID, item_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    design = _owned_design(db, design_id, user)
    item = db.get(DesignItem, item_id)
    if item is None or item.design_id != design.id:
        raise HTTPException(status_code=404, detail="Design item not found.")
    db.delete(item)
    design.image_stale = True
    db.commit()
    return _design_response(db, design)


@router.post("/{design_id}/render", status_code=202)
def render_design_version(
    design_id: uuid.UUID, db: Annotated[Session, Depends(get_db)], user: CurrentUser,
) -> dict:
    parent = _owned_design(db, design_id, user)
    room = db.get(Room, parent.room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found.")
    parent_items = db.query(DesignItem, CatalogProduct).join(
        CatalogProduct, CatalogProduct.product_id == DesignItem.catalog_item_id,
    ).filter(DesignItem.design_id == parent.id).all()
    if not parent_items:
        raise HTTPException(status_code=422, detail="Add at least one catalog item before re-rendering.")
    seed = secrets.randbelow(2_147_483_647)
    while seed == parent.seed:
        seed = secrets.randbelow(2_147_483_647)
    child = Design(room_id=room.id, parent_id=parent.id, style=parent.style,
                   palette=parent.palette, prompt=parent.prompt, seed=seed,
                   status="QUEUED", image_stale=False,
                   image_provider=settings.image_provider,
                   model_ids=settings.image_model_ids)
    db.add(child)
    db.flush()
    for item, product in parent_items:
        if (not product.selectable or room.type not in (product.room_types or [])
                or (product.kind == "object" and not product.reference_image_s3_key)):
            raise HTTPException(status_code=422, detail=f"Catalog item {product.product_id} is no longer renderable.")
        db.add(DesignItem(design_id=child.id, catalog_item_id=product.product_id,
                          role=item.role, qty=item.qty, x=None, y=None, visible=True,
                          is_essential=item.is_essential))
    db.commit()
    db.refresh(child)
    try:
        job = enqueue_catalog_render(db, room, child)
    except RuntimeError as exc:
        raise HTTPException(status_code=503,
                            detail={"message": str(exc), "design_id": str(child.id)}) from exc
    return {"design_id": str(child.id), "parent_id": str(parent.id),
            "job_id": str(job.id), "status": "queued", "seed": seed}
