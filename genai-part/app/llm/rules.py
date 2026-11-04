"""Deterministic spec building and refinement parsing. This is the whole offline
mode, and the base every LLM draft is merged onto."""

import re

from app.core.models import Category, RoomType, Theme
from app.llm.schemas import (
    ChangeAction,
    ColorRequest,
    DesignRequest,
    DesignSpec,
    FurnitureChange,
    LLMDesignDraft,
    Priority,
    ProductRequirement,
    RefinementPatch,
)
from app.scene.models import SceneInfo
from app.scene.normalizer import category_for_label, category_for_word

E, R, O = Priority.ESSENTIAL, Priority.RECOMMENDED, Priority.OPTIONAL

ROOM_TEMPLATES: dict[RoomType, list[tuple[Category, Priority]]] = {
    RoomType.LIVING_ROOM: [
        (Category.SOFA_SEATING, E), (Category.LIGHTING, E), (Category.TABLE, R), (Category.RUG, R),
        (Category.CURTAIN, R), (Category.STORAGE, R), (Category.WALL_DECOR, O), (Category.PLANT, O),
        (Category.ACCESSORY, O),
    ],
    RoomType.BEDROOM: [
        (Category.BED, E), (Category.LIGHTING, E), (Category.STORAGE, R), (Category.CURTAIN, R),
        (Category.RUG, R), (Category.WALL_DECOR, O), (Category.PLANT, O), (Category.ACCESSORY, O),
    ],
    RoomType.DINING_ROOM: [
        (Category.TABLE, E), (Category.CHAIR, E), (Category.LIGHTING, E), (Category.STORAGE, R),
        (Category.CURTAIN, R), (Category.RUG, O), (Category.WALL_DECOR, O), (Category.PLANT, O),
    ],
    RoomType.STUDY: [
        (Category.TABLE, E), (Category.CHAIR, E), (Category.LIGHTING, E), (Category.STORAGE, R),
        (Category.CURTAIN, R), (Category.RUG, O), (Category.WALL_DECOR, O), (Category.PLANT, O),
    ],
}
ROOM_TEMPLATES[RoomType.KIDS_ROOM] = ROOM_TEMPLATES[RoomType.BEDROOM]
ROOM_TEMPLATES[RoomType.OTHER] = ROOM_TEMPLATES[RoomType.LIVING_ROOM]

NEGATIVE_PROMPT = (
    "blurry, low quality, distorted perspective, warped walls, deformed furniture, "
    "people, text, watermark, cartoon, painting, oversaturated"
)

SATURATED_COLORS = ["blue", "red", "green", "yellow", "purple", "orange", "pink"]

COLOR_ALIASES = {
    "darker": ["charcoal", "dark grey", "black", "brown", "navy"],
    "dark": ["charcoal", "dark grey", "black", "brown", "navy"],
    "lighter": ["white", "off white", "beige", "light grey", "cream"],
}
_COLOR_WORDS = ["black", "white", "grey", "gray", "beige", "brown", "blue", "navy", "green",
                "red", "yellow", "mustard", "pink", "orange", "teal", "cream", "charcoal"]


def _room_label(room_type: RoomType) -> str:
    return room_type.value.replace("_", " ")


def max_width_for(category: Category, scene: SceneInfo, small_room: bool) -> float | None:
    """A width limit for large pieces, from the real wall length when it is known."""
    if category not in (Category.SOFA_SEATING, Category.BED, Category.STORAGE, Category.TABLE):
        return None
    if scene.dimensions and scene.dimensions.length_m and scene.dimensions.width_m:
        shorter_wall_cm = min(scene.dimensions.length_m, scene.dimensions.width_m) * 100
        return round(shorter_wall_cm * (0.6 if small_room else 0.8))
    return {Category.SOFA_SEATING: 180.0, Category.BED: 170.0,
            Category.STORAGE: 100.0, Category.TABLE: 120.0}[category] if small_room else None


def _build_changes(request: DesignRequest, scene: SceneInfo) -> list[FurnitureChange]:
    changes: list[FurnitureChange] = []
    for action, labels in ((ChangeAction.KEEP, request.retain), (ChangeAction.REMOVE, request.remove),
                           (ChangeAction.REPLACE, request.replace), (ChangeAction.ADD, request.add)):
        changes += [FurnitureChange(action=action, category=category_for_label(label), label=label,
                                    reason="Requested by the user.") for label in labels]
    named = {c.category for c in changes if c.category}
    for scene_object in scene.objects:
        if scene_object.category and scene_object.category not in named:
            changes.append(FurnitureChange(
                action=ChangeAction.REPLACE, category=scene_object.category,
                label=scene_object.label, mask_id=scene_object.mask_id,
                reason="Existing piece replaced to match the theme.",
            ))
    return changes


def build_requirements(
    room_type: RoomType,
    scene: SceneInfo,
    changes: list[FurnitureChange],
    palette: list[str],
    accents: list[str],
    materials: list[str],
    small_room: bool,
) -> list[ProductRequirement]:
    keep_or_remove = {c.category for c in changes
                      if c.action in (ChangeAction.KEEP, ChangeAction.REMOVE)}
    requested = {c.category for c in changes if c.action in (ChangeAction.ADD, ChangeAction.REPLACE)}
    template = list(ROOM_TEMPLATES[room_type])
    in_template = {category for category, _ in template}
    template += [(c, R) for c in sorted(requested - in_template - {None}, key=lambda c: c.value)]

    requirements = []
    for category, priority in template:
        if category in keep_or_remove:
            continue
        if category in requested and priority is not E:
            priority = R
        requirements.append(ProductRequirement(
            slot=category.value,
            category=category,
            priority=priority,
            preferred_colors=[*palette, *accents],
            preferred_materials=materials,
            max_width_cm=max_width_for(category, scene, small_room),
            small_room_only=small_room and category in (Category.SOFA_SEATING, Category.BED,
                                                         Category.TABLE, Category.STORAGE),
        ))
    return requirements


def compose_redesign_prompt(
    base_prompt: str, room_type: RoomType, palette: list[str], selected_items: list[str] | None = None
) -> str:
    """CLIP reads about 75 tokens, so the most important terms come first."""
    parts = [f"photo of a {_room_label(room_type)}"]
    if selected_items:
        parts.append(f"with {' and '.join(selected_items[:3])}")
    parts += [base_prompt, f"{', '.join(palette[:3])} colour palette",
              "photorealistic interior photography"]
    return ", ".join(parts)


def off_palette_negative(negative_prompt: str, palette: list[str], accents: list[str]) -> str:
    """SD 1.5 drifts toward saturated colours, so name the ones the theme does not use."""
    allowed = " ".join([*palette, *accents]).lower()
    return ", ".join([negative_prompt, *(c for c in SATURATED_COLORS if c not in allowed)])


def compose_edit_prompt(theme_label: str, palette: list[str], materials: list[str]) -> str:
    return (f"Turn this room into a {theme_label} interior with a {', '.join(palette[:3])} "
            f"palette and {', '.join(materials[:2])} finishes")


def build_spec(request: DesignRequest, theme_data: dict, draft: LLMDesignDraft | None = None) -> DesignSpec:
    """Build the spec. A draft may change the creative fields only; slots stay rule-driven."""
    scene = request.scene or SceneInfo()
    room_type = request.room_type or scene.room_type
    palette = draft.color_palette if draft else list(theme_data["palette"])
    accents = draft.accent_colors if draft else list(theme_data["accent_colors"])
    materials = draft.materials if draft else list(theme_data["materials"])

    changes = _build_changes(request, scene)
    requirements = build_requirements(room_type, scene, changes, palette, accents, materials,
                                      request.small_room)

    layout = [
        "Keep walkways at least 75 cm wide.",
        "Leave doors and windows unobstructed.",
    ]
    if request.small_room:
        layout.append("Prefer compact, wall-hugging furniture and keep the centre of the floor open.")
    if draft:
        layout += draft.layout_constraints

    assumptions = [f"Scene field not provided: {field}." for field in scene.missing_fields]
    if not scene.dimensions:
        assumptions.append("Room size is unknown, so products are not checked against wall lengths.")

    return DesignSpec(
        theme=request.theme,
        room_type=room_type,
        color_palette=palette,
        accent_colors=accents,
        materials=materials,
        furniture_changes=changes,
        layout_constraints=layout,
        product_requirements=requirements,
        budget_limit_inr=request.budget_inr,
        small_room=request.small_room,
        redesign_prompt=draft.redesign_prompt if draft else compose_redesign_prompt(
            theme_data["prompt"], room_type, palette),
        negative_prompt=NEGATIVE_PROMPT,
        edit_prompt_3d=draft.edit_prompt_3d if draft else compose_edit_prompt(
            theme_data["label"], palette, materials),
        rationale=draft.rationale if draft else theme_data["description"],
        assumptions=assumptions,
        generated_by="llm" if draft else "rules",
    )


def _amount_in_text(text: str) -> float | None:
    match = re.search(r"(?:₹|rs\.?|inr|rupees?)\s*([\d,]+(?:\.\d+)?)\s*(k|lakh)?", text)
    if match is None:
        match = re.search(r"(?:under|below|within|max(?:imum)?|budget(?: of| to)?)\s+([\d,]+(?:\.\d+)?)\s*(k|lakh)?", text)
    if match is None:
        return None
    value = float(match.group(1).replace(",", ""))
    return value * {"k": 1_000, "lakh": 100_000, None: 1}[match.group(2)]


def _categories_named(text: str) -> list[Category]:
    named = [category_for_word(word) for word in re.findall(r"[a-z]+", text)]
    # An object word right before another only modifies it: "table lamp" is one lamp.
    heads = [c for c, following in zip(named, [*named[1:], None]) if c and following is None]
    return list(dict.fromkeys(heads))


def _mark_light_as_colour(text: str) -> str:
    """"Light" is an object in "remove the light" and a colour in "a light rug" or
    "make the sofa light". Rewrite the colour uses, so plain "light" always means a light."""

    def before_noun(match: re.Match) -> str:
        following = match.group(1)
        is_colour = category_for_word(following) or following in _COLOR_WORDS
        return f"lighter {following}" if is_colour else match.group(0)

    def after_noun(match: re.Match) -> str:
        return f"{match.group(1)}lighter" if category_for_word(match.group(2)) else match.group(0)

    text = re.sub(r"\blight\s+([a-z]+)", before_noun, text)
    return re.sub(r"(\b(?:make|paint)\b[^.,;]*?\b([a-z]+)\s+)light\b", after_noun, text)


def parse_refinement(message: str) -> RefinementPatch:
    text = _mark_light_as_colour(message.lower())
    patch = RefinementPatch(understood=False)

    for theme in Theme:
        if re.search(rf"\b{theme.value}\b", text):
            patch.theme = theme
            patch.understood = True
            break
    if patch.theme is Theme.MINIMALIST or re.search(r"\b(declutter|less clutter|simpler)\b", text):
        patch.reduce_decor = True
        patch.understood = True

    budget = _amount_in_text(text)
    if budget:
        patch.budget_limit_inr = budget
        patch.understood = True

    if "small room" in text or "small space" in text or "compact" in text:
        patch.small_room = True
        patch.understood = True

    keep = re.findall(r"\b(?:keep|retain|don'?t change|do not change)\b[^.,;]*", text)
    for clause in keep:
        if "under" in clause or "below" in clause or "budget" in clause:
            continue
        patch.keep_categories += _categories_named(clause)
    remove = re.findall(r"\b(?:remove|drop|get rid of|no|without)\b[^.,;]*", text)
    for clause in remove:
        patch.remove_categories += _categories_named(clause)
    patch.understood = patch.understood or bool(patch.keep_categories or patch.remove_categories)

    for category in _categories_named(text):
        if category in patch.keep_categories or category in patch.remove_categories:
            continue
        # A named colour is more specific than "darker" or "lighter".
        colors = [c for c in _COLOR_WORDS if re.search(rf"\b{c}\b", text)]
        if not colors:
            word = next((w for w in COLOR_ALIASES if re.search(rf"\b{w}\b", text)), None)
            colors = COLOR_ALIASES.get(word, [])
        if colors:
            patch.color_requests.append(ColorRequest(category=category, colors=colors))
            patch.understood = True
    return patch
