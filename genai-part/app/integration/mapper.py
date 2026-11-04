"""Reduce a design to the one edit request Person A can run.

What Person A's backend can and cannot do, from reading it at commit 23a2fd8:

- One /jobs/edit call takes a style prompt, one mask removal and one catalog insertion.
- Every edit job starts again from before_model.ply and overwrites after_model.ply, so
  separate jobs do not accumulate. A design must fit in a single job, or the earlier
  job's work is lost.
- An insertion needs an S3 key the worker can read, and a position and scale in Nerfstudio
  scene units. No API returns scene bounds, and scene units are not metres, so neither
  can be derived here. The caller has to supply placement.
- Mask ids are class-agnostic SAM clusters and no API lists them. Removal needs an id
  that a person has reviewed.
"""

import math
from pathlib import PurePosixPath

from pydantic import BaseModel, Field

from app.core.errors import ConflictError, ValidationFailed
from app.dataset.catalog import Catalog
from app.integration.person_a_schemas import EditJobRequest
from app.services.design_models import Design

ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


class Insertion(BaseModel):
    product_id: str
    target_position: list[float] = Field(min_length=3, max_length=3,
                                         description="Nerfstudio scene coordinates.")
    target_scale: float = Field(gt=0, description="Largest extent, in scene units.")


class Edit3DOptions(BaseModel):
    person_a_project_id: str
    include_style: bool = True
    remove_mask_id: int | None = Field(default=None, gt=0, description="A reviewed Gaussian mask id.")
    insertion: Insertion | None = None


class NotSentItem(BaseModel):
    item: str
    reason: str


class EditPlan(BaseModel):
    request: EditJobRequest
    not_sent: list[NotSentItem] = Field(default_factory=list)


def _safe_s3_key(key: str) -> bool:
    parts = key.replace("\\", "/").split("/")
    return (not key.startswith(("/", "\\")) and all(p not in ("", ".", "..") for p in parts)
            and PurePosixPath(key).suffix.lower() in ALLOWED_IMAGE_SUFFIXES)


def build_edit_plan(design: Design, options: Edit3DOptions, catalog: Catalog) -> EditPlan:
    request = EditJobRequest(project_id=options.person_a_project_id, remove_mask_id=options.remove_mask_id)
    if options.include_style:
        request.style_prompt = design.spec.edit_prompt_3d

    insertion = options.insertion
    if insertion is not None:
        product = catalog.get(insertion.product_id)
        key = product.catalog_image_key
        if key is None:
            raise ValidationFailed(
                f"{product.product_id} has no catalog_image_key. Upload its image to Person A's "
                "bucket and record the key first."
            )
        if not _safe_s3_key(key):
            raise ValidationFailed(f"{product.product_id}: {key!r} is not a usable JPEG, PNG or WebP key.")
        if not all(math.isfinite(c) for c in insertion.target_position):
            raise ValidationFailed("target_position must contain three finite coordinates.")
        request.insert_catalog_image_key = key
        request.target_position = insertion.target_position
        request.target_scale = insertion.target_scale

    if request.style_prompt is None and request.remove_mask_id is None \
            and request.insert_catalog_image_key is None:
        raise ConflictError("Nothing to send: choose a style edit, a removal or an insertion.")

    inserted = insertion.product_id if insertion else None
    selected = design.budget.items if design.budget else []
    not_sent = [
        NotSentItem(item=item.name, reason="Person A inserts one catalog item per job, "
                                           "and separate edit jobs do not accumulate.")
        for item in selected if item.product_id != inserted
    ]
    return EditPlan(request=request, not_sent=not_sent)
