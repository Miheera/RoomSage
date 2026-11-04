from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import Container, get_container
from app.api.schemas.design import ProductPage, ThemeInfo
from app.core.models import Category, Product, RoomType, Theme
from app.dataset.validate import summarize

router = APIRouter(prefix="/catalog", tags=["catalog"])
Deps = Annotated[Container, Depends(get_container)]


@router.get("/themes", response_model=list[ThemeInfo])
def list_themes(deps: Deps) -> list[ThemeInfo]:
    return [ThemeInfo(id=key, **{k: theme[k] for k in ThemeInfo.model_fields if k != "id"})
            for key, theme in deps.catalog.themes.items()]


@router.get("/categories", response_model=list[str])
def list_categories() -> list[str]:
    return [category.value for category in Category]


@router.get("/stats")
def catalog_stats(deps: Deps) -> dict:
    return summarize(deps.catalog.products)


@router.get("/products", response_model=ProductPage)
def list_products(
    deps: Deps,
    category: Category | None = None,
    theme: Theme | None = None,
    room_type: RoomType | None = None,
    priced_only: bool = False,
    max_price_inr: Annotated[float | None, Query(gt=0)] = None,
    small_room: bool | None = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProductPage:
    results = deps.catalog.search(
        category=category, theme=theme, room_type=room_type, priced_only=priced_only,
        max_price_inr=max_price_inr, small_room=small_room, query=q,
    )
    return ProductPage(total=len(results), limit=limit, offset=offset, items=results[offset:offset + limit])


@router.get("/products/{product_id}", response_model=Product)
def get_product(product_id: str, deps: Deps) -> Product:
    return deps.catalog.get(product_id)
