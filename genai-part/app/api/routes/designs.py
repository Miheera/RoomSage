from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import RedirectResponse

from app.api.deps import Container, get_container
from app.api.schemas.design import (
    BudgetRequest,
    BudgetResponse,
    CalculateRequest,
    CreateDesignRequest,
    DesignSummary,
    PlanResponse,
    PreviewRequest,
    RecommendationsResponse,
    RefineRequest,
    RefineResponse,
    SceneNormalizeRequest,
)
from app.budget.optimizer import BudgetResult, calculate_selection
from app.core.errors import ConflictError
from app.integration.mapper import Edit3DOptions, EditPlan
from app.integration.person_a_schemas import (
    EditJobResponse,
    JobRead,
    RedesignJobRequest,
    JobStatus,
)
from app.scene.models import SceneInfo
from app.scene.normalizer import build_scene
from app.services.design_models import Design
from app.services.design_service import Edit3DResult

router = APIRouter(tags=["designs"])
Deps = Annotated[Container, Depends(get_container)]


@router.post("/scene/normalize", response_model=SceneInfo, tags=["scene"])
def normalize_scene(payload: SceneNormalizeRequest) -> SceneInfo:
    """Turn partial scene data into a SceneInfo and report what is missing."""
    return build_scene(payload.raw, payload.room_type)


@router.post("/budget/calculate", response_model=BudgetResult, tags=["budget"])
def calculate_budget(payload: CalculateRequest, deps: Deps) -> BudgetResult:
    """Total a fixed list of products. Unknown prices are listed, never counted as zero."""
    return calculate_selection(payload.product_ids, payload.budget_inr, deps.catalog)


@router.post("/designs", response_model=DesignSummary, status_code=status.HTTP_201_CREATED)
def create_design(payload: CreateDesignRequest, deps: Deps) -> Design:
    """Generate the structured design specification."""
    return deps.designs.create(payload)


@router.post("/designs/full", response_model=Design, status_code=status.HTTP_201_CREATED)
def create_full_design(payload: CreateDesignRequest, deps: Deps) -> Design:
    """Spec, recommendations, budget and plan in one call. Previews are separate."""
    return deps.designs.run_all(payload)


@router.get("/designs/{design_id}", response_model=Design)
def get_design(design_id: str, deps: Deps) -> Design:
    return deps.designs.store.get(design_id)


@router.post("/designs/{design_id}/recommendations", response_model=RecommendationsResponse)
def get_recommendations(design_id: str, deps: Deps) -> RecommendationsResponse:
    design = deps.designs.recommendations(design_id)
    return RecommendationsResponse(design_id=design_id, slots=design.recommendations)


@router.post("/designs/{design_id}/budget", response_model=BudgetResponse)
def optimize(design_id: str, deps: Deps, payload: BudgetRequest | None = None) -> BudgetResponse:
    design = deps.designs.budget(design_id, payload.budget_inr if payload else None)
    return BudgetResponse(design_id=design_id, budget=design.budget)


@router.post("/designs/{design_id}/plan", response_model=PlanResponse)
def transformation_plan(design_id: str, deps: Deps) -> PlanResponse:
    design = deps.designs.plan(design_id)
    return PlanResponse(design_id=design_id, plan=design.plan)


@router.post("/designs/{design_id}/refine", response_model=RefineResponse)
def refine_design(design_id: str, payload: RefineRequest, deps: Deps) -> RefineResponse:
    """Apply a natural-language change and rerun only the affected stages."""
    design, outcome = deps.designs.refine(design_id, payload.message)
    return RefineResponse(design_id=design_id, outcome=outcome, spec=design.spec,
                          budget=design.budget, plan=design.plan)


@router.post("/designs/{design_id}/preview", response_model=EditJobResponse,
             status_code=status.HTTP_202_ACCEPTED)
def start_preview(design_id: str, payload: PreviewRequest, deps: Deps) -> EditJobResponse:
    """Queue a 2D redesign in Person A's PostgreSQL-backed Celery job system."""
    design = deps.designs.store.get(design_id)
    prompt = deps.designs.preview_prompt(design)
    return deps.person_a.start_redesign(
        RedesignJobRequest(project_id=payload.project_id, prompt=prompt)
    )


@router.get("/previews/{job_id}", response_model=JobRead)
def get_preview(job_id: str, deps: Deps) -> JobRead:
    return deps.person_a.get_job(job_id)


@router.get("/previews/{job_id}/image")
def get_preview_image(job_id: str, deps: Deps) -> RedirectResponse:
    job = deps.person_a.get_job(job_id)
    if job.status is not JobStatus.COMPLETED:
        raise ConflictError(f"Preview is {job.status.value}.", {"error": job.error_message})
    if not job.output_urls:
        raise ConflictError("Preview completed without an output URL.")
    return RedirectResponse(job.output_urls[0])


@router.post("/designs/{design_id}/edit-3d/plan", response_model=EditPlan, tags=["person-a"])
def preview_3d_edit(design_id: str, options: Edit3DOptions, deps: Deps) -> EditPlan:
    """Show what would be sent to Person A, and what cannot be. Calls nothing."""
    return deps.designs.preview_edit_plan(design_id, options)


@router.post("/designs/{design_id}/edit-3d", response_model=Edit3DResult,
             status_code=status.HTTP_202_ACCEPTED, tags=["person-a"])
def submit_3d_edit(design_id: str, options: Edit3DOptions, deps: Deps) -> Edit3DResult:
    """Send the design's edit to Person A's POST /api/v1/jobs/edit."""
    return deps.designs.submit_3d_edit(design_id, options)


@router.get("/edit-3d/jobs/{job_id}", response_model=JobRead, tags=["person-a"])
def get_3d_job(job_id: str, deps: Deps) -> JobRead:
    return deps.person_a.get_job(job_id)
