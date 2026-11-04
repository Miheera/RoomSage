"""Mask construction from ADE20K labels. Pure array logic, so it is testable without
any model. Class names follow the id2label table the segmentation model ships with."""

import numpy as np
from PIL import Image, ImageFilter

from app.core.models import Category
from app.redesign.base import EditScope

# Never repainted in any scope: the room's shell and openings.
PROTECTED = {"windowpane", "door", "ceiling", "stairs", "column", "fireplace", "radiator"}
SURFACES = {"wall", "floor", "rug"}
# Outdoors seen through a window.
OUTDOORS = {"sky", "tree", "building", "mountain", "road", "grass"}

CATEGORY_LABELS: dict[Category, set[str]] = {
    Category.SOFA_SEATING: {"sofa", "armchair", "swivel chair", "ottoman", "bench"},
    Category.BED: {"bed"},
    Category.TABLE: {"table", "coffee table", "desk", "pool table"},
    Category.CHAIR: {"chair", "stool"},
    Category.STORAGE: {"cabinet", "shelf", "bookcase", "chest of drawers", "wardrobe", "buffet"},
    Category.LIGHTING: {"lamp", "chandelier", "light", "sconce"},
    Category.RUG: {"rug"},
    Category.CURTAIN: {"curtain", "blind"},
    Category.WALL_DECOR: {"painting", "mirror", "poster", "clock"},
    Category.PLANT: {"plant", "flower"},
    Category.ACCESSORY: {"cushion", "vase", "pillow", "basket", "pot", "tray", "book", "ashcan"},
}
FURNITURE_LABELS = set().union(*CATEGORY_LABELS.values())


def primary_label(name: str) -> str:
    """ADE names look like 'sofa, couch, lounge' or 'wall'."""
    return name.split(",")[0].strip().lower()


def label_ids(id2label: dict[int, str], labels: set[str]) -> list[int]:
    return [i for i, name in id2label.items() if primary_label(name) in labels]


def category_for_ade_label(name: str) -> Category | None:
    label = primary_label(name)
    return next((c for c, labels in CATEGORY_LABELS.items() if label in labels), None)


def build_mask(
    seg: np.ndarray,
    id2label: dict[int, str],
    scope: EditScope,
    target_categories: list[Category] | None = None,
    keep_categories: list[Category] | None = None,
) -> np.ndarray:
    """Boolean array, True where pixels may be repainted."""
    if scope is EditScope.TARGETED:
        targets = set().union(*(CATEGORY_LABELS[c] for c in target_categories or [])) \
            if target_categories else set()
    elif scope is EditScope.FULL:
        everything = {primary_label(name) for name in id2label.values()}
        targets = everything - PROTECTED - OUTDOORS
    else:
        targets = FURNITURE_LABELS

    mask = np.isin(seg, label_ids(id2label, targets))
    keep = set().union(*(CATEGORY_LABELS[c] for c in keep_categories)) if keep_categories else set()
    if keep:
        mask &= ~np.isin(seg, label_ids(id2label, keep))
    return mask


def dilate_mask(
    mask: np.ndarray, seg: np.ndarray, id2label: dict[int, str], radius: int,
    keep_categories: list[Category] | None = None,
) -> np.ndarray:
    """Grow the mask past object edges so old outlines do not survive, without
    growing it into anything that must stay as photographed."""
    image = Image.fromarray((mask * 255).astype(np.uint8))
    grown = np.asarray(image.filter(ImageFilter.MaxFilter(radius * 2 + 1))) > 127
    frozen = PROTECTED | OUTDOORS
    if keep_categories:
        frozen = frozen | set().union(*(CATEGORY_LABELS[c] for c in keep_categories))
    return grown & ~np.isin(seg, label_ids(id2label, frozen))


def soft_composite(original: Image.Image, generated: Image.Image, mask: np.ndarray, feather: int = 3) -> Image.Image:
    """Restore everything outside the mask from the source photo, so untouched areas
    are the original pixels and not a VAE round-trip of them."""
    alpha = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(feather))
    return Image.composite(generated, original, alpha)
