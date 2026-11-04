"""Turn whatever scene data is available into a SceneInfo.

Person A's API exposes the project's room_type, photo keys and a depth-map flag.
It does not expose room dimensions or detected objects, so those arrive from the
caller or from photo analysis, or stay unknown. Nothing here fills a gap with a guess.
"""

import re
from typing import Any

from pydantic import ValidationError

from app.core.models import Category, RoomType
from app.scene.models import RoomDimensions, SceneInfo, SceneObject

_ROOM_ALIASES = {
    "living": RoomType.LIVING_ROOM, "lounge": RoomType.LIVING_ROOM, "hall": RoomType.LIVING_ROOM,
    "bed": RoomType.BEDROOM, "master": RoomType.BEDROOM,
    "dining": RoomType.DINING_ROOM,
    "study": RoomType.STUDY, "office": RoomType.STUDY,
    "kid": RoomType.KIDS_ROOM, "nursery": RoomType.KIDS_ROOM, "child": RoomType.KIDS_ROOM,
}

_OBJECT_ALIASES = {
    "sofa": Category.SOFA_SEATING, "couch": Category.SOFA_SEATING, "armchair": Category.SOFA_SEATING,
    "bed": Category.BED,
    "table": Category.TABLE, "desk": Category.TABLE,
    "chair": Category.CHAIR, "stool": Category.CHAIR,
    "wardrobe": Category.STORAGE, "cabinet": Category.STORAGE, "shelf": Category.STORAGE,
    "shelves": Category.STORAGE, "bookshelf": Category.STORAGE, "bookshelves": Category.STORAGE,
    "bookcase": Category.STORAGE, "dresser": Category.STORAGE, "chest": Category.STORAGE,
    "lamp": Category.LIGHTING, "light": Category.LIGHTING, "lighting": Category.LIGHTING,
    "chandelier": Category.LIGHTING,
    "rug": Category.RUG, "carpet": Category.RUG,
    "curtain": Category.CURTAIN, "blind": Category.CURTAIN,
    "painting": Category.WALL_DECOR, "mirror": Category.WALL_DECOR, "poster": Category.WALL_DECOR,
    "plant": Category.PLANT, "flower": Category.PLANT,
    "cushion": Category.ACCESSORY, "pillow": Category.ACCESSORY, "vase": Category.ACCESSORY,
}


def normalize_room_type(value: str | RoomType | None) -> RoomType | None:
    if isinstance(value, RoomType):
        return value
    if not value:
        return None
    lowered = value.strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return RoomType(lowered)
    except ValueError:
        return next((room for key, room in _ROOM_ALIASES.items() if key in lowered), RoomType.OTHER)


def category_for_word(word: str) -> Category | None:
    """Whole words only, so "bedroom" is not a bed and "lighter" is not a light."""
    for form in (word, word.removesuffix("s"), word.removesuffix("es")):
        if form in _OBJECT_ALIASES:
            return _OBJECT_ALIASES[form]
    return None


def category_for_label(label: str) -> Category | None:
    """The last object word is the head noun: a "table lamp" is a lamp."""
    found = [c for c in map(category_for_word, re.findall(r"[a-z]+", label.lower())) if c]
    return found[-1] if found else None


def _coerce_objects(raw: Any) -> list[SceneObject]:
    objects: list[SceneObject] = []
    for item in raw if isinstance(raw, list) else []:
        data = {"label": item} if isinstance(item, str) else item
        try:
            scene_object = SceneObject.model_validate(data)
        except ValidationError:
            continue
        if scene_object.category is None:
            scene_object.category = category_for_label(scene_object.label)
        objects.append(scene_object)
    return objects


def _coerce_dimensions(raw: Any) -> RoomDimensions | None:
    if not isinstance(raw, dict):
        return None
    try:
        dimensions = RoomDimensions.model_validate(raw)
    except ValidationError:
        return None
    return dimensions if dimensions.length_m or dimensions.width_m else None


def build_scene(
    raw: dict[str, Any] | None = None,
    room_type: str | RoomType | None = None,
) -> SceneInfo:
    """Merge a raw scene payload with explicit overrides. Tolerates partial or
    malformed input: bad parts are dropped and reported as missing."""
    data = raw or {}
    resolved_room = normalize_room_type(room_type) or normalize_room_type(data.get("room_type"))
    dimensions = _coerce_dimensions(data.get("dimensions"))
    objects = _coerce_objects(data.get("objects"))
    colors = [c for c in data.get("colors") or [] if isinstance(c, str)]
    materials = [m for m in data.get("materials") or [] if isinstance(m, str)]

    missing = [
        name
        for name, present in (
            ("room_type", resolved_room is not None),
            ("dimensions", dimensions is not None),
            ("objects", bool(objects)),
            ("colors", bool(colors)),
            ("materials", bool(materials)),
        )
        if not present
    ]
    return SceneInfo(
        room_type=resolved_room or RoomType.LIVING_ROOM,
        dimensions=dimensions,
        objects=objects,
        colors=colors,
        materials=materials,
        person_a_project_id=data.get("person_a_project_id"),
        depth_map_available=bool(data.get("depth_map_available")),
        missing_fields=missing,
    )
