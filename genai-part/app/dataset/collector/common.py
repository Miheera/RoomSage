"""Field extraction shared by the source adapters. Nothing here guesses a value:
when the text does not state something, the result is None."""

import hashlib
import json
import re
import urllib.request
from pathlib import Path

from app.core.models import Category, Dimensions, RoomType, Theme, ThemeSource

COLOR_WORDS = [
    "off white", "off-white", "light grey", "dark grey", "light gray", "dark gray",
    "white", "black", "grey", "gray", "beige", "cream", "brown", "tan", "walnut",
    "red", "maroon", "pink", "orange", "yellow", "mustard", "gold", "golden",
    "green", "olive", "teal", "turquoise", "blue", "navy", "purple", "violet",
    "silver", "multicolor", "multicolour", "natural",
]

MATERIAL_WORDS = [
    "solid wood", "engineered wood", "mango wood", "sheesham", "teak", "oak", "pine",
    "birch", "ash", "bamboo", "rattan", "cane", "jute", "cotton", "linen", "polyester",
    "silk", "velvet", "wool", "leatherette", "leather", "steel", "iron", "brass",
    "copper", "aluminium", "metal", "glass", "ceramic", "marble", "plastic", "fabric",
    "paper", "polyresin", "acrylic", "wood", "wooden", "plywood", "mdf",
]

_UNIT_TO_CM = {"cm": 1.0, "mm": 0.1, "m": 100.0, "inch": 2.54, "in": 2.54, "ft": 30.48, "feet": 30.48}
_MEASURE = re.compile(r"(\d+(?:\.\d+)?)\s*(cm|mm|inch|in|ft|feet|m)\b", re.IGNORECASE)

# Generous by design: these reject unit and parsing mistakes, not unusual furniture.
_MAX_DIMENSION_CM = 1000.0


def find_word(text: str, words: list[str]) -> str | None:
    lowered = text.lower()
    for word in words:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            return word
    return None


def normalize_color(value: str | None) -> str | None:
    if not value:
        return None
    found = find_word(value, COLOR_WORDS)
    if found is None:
        return None
    return {"gray": "grey", "off-white": "off white", "multicolour": "multicolor",
            "golden": "gold", "light gray": "light grey", "dark gray": "dark grey"}.get(found, found)


def normalize_material(value: str | None) -> str | None:
    if not value:
        return None
    found = find_word(value, MATERIAL_WORDS)
    return "wood" if found == "wooden" else found


def parse_length_cm(value: str | None) -> float | None:
    """Parse a stated length. Prefers centimetres when several units are given,
    as in Flipkart's "60 inch / 153 cm"."""
    if not value:
        return None
    matches = _MEASURE.findall(value)
    if not matches:
        return None
    chosen = next((m for m in matches if m[1].lower() == "cm"), matches[0])
    length = float(chosen[0]) * _UNIT_TO_CM[chosen[1].lower()]
    return round(length, 1) if 0 < length <= _MAX_DIMENSION_CM else None


def build_dimensions(
    width: float | None, depth: float | None, height: float | None
) -> Dimensions | None:
    if width is None and depth is None and height is None:
        return None
    return Dimensions(width_cm=width, depth_cm=depth, height_cm=height)


def small_room_suitable(category: Category, dims: Dimensions | None) -> bool | None:
    """True/False only when the relevant dimension is stated, otherwise unknown."""
    limits_cm = {
        Category.SOFA_SEATING: 180,
        Category.BED: 170,
        Category.TABLE: 120,
        Category.CHAIR: 70,
        Category.STORAGE: 100,
        Category.RUG: 170,
    }
    limit = limits_cm.get(category)
    if limit is None or dims is None:
        return None
    footprint = dims.footprint_cm()
    return None if footprint is None else footprint <= limit


def room_types_for(category: Category, text: str) -> list[RoomType]:
    lowered = text.lower()
    defaults = {
        Category.SOFA_SEATING: [RoomType.LIVING_ROOM],
        Category.BED: [RoomType.BEDROOM],
        Category.TABLE: [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.STUDY],
        Category.CHAIR: [RoomType.LIVING_ROOM, RoomType.DINING_ROOM, RoomType.STUDY],
        Category.STORAGE: [RoomType.LIVING_ROOM, RoomType.BEDROOM, RoomType.STUDY],
    }
    rooms = list(defaults.get(category, [RoomType.LIVING_ROOM, RoomType.BEDROOM,
                                         RoomType.DINING_ROOM, RoomType.STUDY]))
    narrowing = [
        (("desk", "study", "office", "laptop", "computer"), [RoomType.STUDY]),
        (("wardrobe", "dressing", "nightstand", "bedside"), [RoomType.BEDROOM]),
        (("dining",), [RoomType.DINING_ROOM]),
        (("tv ", "tv-", "coffee table", "side table", "armchair"), [RoomType.LIVING_ROOM]),
    ]
    for needles, matched in narrowing:
        if any(needle in lowered for needle in needles):
            return matched
    return rooms


# Categories whose fit depends little on style, used when nothing else matched.
_NEUTRAL_THEMES = {
    Category.PLANT: list(Theme),
    Category.WALL_DECOR: [Theme.CONTEMPORARY, Theme.BOHEMIAN],
}


def assign_themes(
    profile_text: str,
    color: str | None,
    material: str | None,
    themes: dict[str, dict],
    category: Category,
    stated_style: str | None = None,
) -> tuple[list[Theme], ThemeSource]:
    """Tag themes from the source's own style field when it has one, else by rule."""
    if stated_style:
        stated = [Theme(key) for key in themes if key in stated_style.lower()]
        if stated:
            return stated, ThemeSource.SOURCE_STYLE

    text = profile_text.lower()
    tagged: list[Theme] = []
    for key, theme in themes.items():
        if any(find_word(text, [word]) for word in theme["avoid"]):
            continue
        score = 0
        if color and color in theme["palette"]:
            score += 2
        elif color and color in theme["accent_colors"]:
            score += 1
        if material and material in theme["materials"]:
            score += 2
        score += sum(2 for word in theme["keywords"] if find_word(text, [word]))
        if score >= 1:
            tagged.append(Theme(key))
    return tagged or list(_NEUTRAL_THEMES.get(category, [])), ThemeSource.DERIVED_RULES


def fetch_once(url: str, destination: Path, expected_sha256: str | None = None) -> Path:
    """Download a source file a single time. Later runs reuse the local copy."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "roomsage-design-engine/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            destination.write_bytes(response.read())
    if expected_sha256:
        actual = hashlib.sha256(destination.read_bytes()).hexdigest()
        if actual != expected_sha256:
            raise ValueError(f"{destination.name}: checksum {actual} is not the pinned source")
    return destination


def load_themes(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))
